"""
persistencia_produccion.py
───────────────────────────────────────────────────────────────────────────────
RONDA 13 — Persistencia durable y append-only en Git (rama de datos dedicada)
con:
  1. Reconstrucción estricta de `ledger_oos.jsonl` desde la copia remota antes
     de cada sincronización / reintento (sin 3-way merge textual que corrompa
     `indice`, `prev_sha256` o `sha256`).
  2. Hilo de cola dedicado (`ColaPersistenciaGit`) con `threading.Lock`,
     worktree Git aislado y reintentos ante conflictos `non-fast-forward`.
  3. Hidratación automática al arranque del runner (`hidratar_desde_remoto_datos`)
     para que `/evaluar` tras un reinicio conserve todos los snapshots
     (`informes/pronosticos/*.json`), el `ledger_oos.jsonl` y los registros.
  4. Escritura atómica en disco (`tempfile` + `os.replace`) y CLI de smoke
     sintético (`--smoke-prueba`) sin Telegram para el workflow de pruebas.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import random
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit


RUTA_LEDGER_REL = "informes/evaluacion/ledger_oos.jsonl"
RUTA_REGISTRO_EVAL_REL = "informes/evaluacion/registro_evaluaciones.json"
DIR_PRONOSTICOS_REL = "informes/pronosticos"
DIR_EVALUACION_REL = "informes/evaluacion"
DIR_EVENTOS_REL = "informes/evaluacion/eventos"
RAMAS_DEFECTO_PROHIBIDAS = frozenset({"main", "master", "principal"})


def persistencia_activa() -> bool:
    """Devuelve True solo si la persistencia Git en producción está habilitada por entorno."""
    val = os.getenv("PERSISTENCIA_PRODUCCION_ACTIVA", "").strip().lower()
    return val in ("1", "true", "si", "sí", "yes", "on")


def normalizar_url_git(url: str | None) -> str:
    """Normaliza URLs HTTPS/SSH o rutas locales para comparar equivalencia con `origin`."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    m_ssh = re.match(r"^(?:[^@/\s]+@)?([A-Za-z0-9._-]+):(.+)$", raw)
    if m_ssh and not re.match(r"^[A-Za-z]:[\\/]", raw) and "://" not in raw:
        host = m_ssh.group(1).lower()
        path = m_ssh.group(2).strip("/")
        if path.lower().endswith(".git"):
            path = path[:-4]
        return f"{host}/{path.lower()}"
    if "://" in raw:
        p = urlsplit(raw)
        host = (p.hostname or p.netloc.split("@")[-1]).lower()
        port = f":{p.port}" if p.port else ""
        path = p.path.strip("/")
        if path.lower().endswith(".git"):
            path = path[:-4]
        return f"{host}{port}/{path.lower()}".rstrip("/")
    try:
        return str(Path(raw).resolve()).replace("\\", "/").rstrip("/").lower()
    except OSError:
        return raw.replace("\\", "/").rstrip("/").lower()


def obtener_url_origin(ws: Path | str) -> str:
    """Obtiene la URL configurada para `origin` en `ws`, o cadena vacía si no existe."""
    cp = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=str(ws),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return cp.stdout.strip() if cp.returncode == 0 else ""


def validar_rama_datos(
    rama_datos: str | None,
    *,
    rama_defecto_remota: str | None = None,
) -> str:
    """Bloquea el uso de ramas por defecto (`main`, `master`, `principal` o HEAD remota)."""
    rama = str(rama_datos or "").strip()
    if not rama:
        raise ValueError("Rama de datos vacía o inválida.")
    rl = rama.lower()
    if rl in RAMAS_DEFECTO_PROHIBIDAS:
        raise ValueError(f"La rama por defecto {rama!r} está prohibida como rama de datos.")
    if rama_defecto_remota and rl == str(rama_defecto_remota).strip().lower():
        raise ValueError(
            f"La rama {rama!r} coincide con la rama por defecto remota {rama_defecto_remota!r} y está prohibida."
        )
    return rama


@dataclass
class ConfigPersistencia:
    workspace_dir: Path
    remote: str = "origin"
    rama_datos: str = "produccion-datos"
    remote_url: str = field(default="", repr=False)
    remote_token: str = field(default="", repr=False)
    worktree_dir: Path | None = None
    habilitada: bool = False
    max_reintentos_push: int = 4
    backoff_base_s: float = 0.35
    author_name: str = "Agente Futbol Bot"
    author_email: str = "bot@agente-futbol.local"
    archivos_extra_rel: tuple[str, ...] = field(default_factory=tuple)
    codigo_estado: str = "inactiva"
    motivo_estado: str = ""

    @classmethod
    def desde_entorno(cls, workspace_dir: Path | str | None = None) -> "ConfigPersistencia":
        raiz = Path(workspace_dir) if workspace_dir else Path(__file__).resolve().parent
        activa_env = persistencia_activa()
        r_url = os.getenv("PERSISTENCIA_REMOTO_URL", "").strip()
        r_tok = os.getenv("PERSISTENCIA_REMOTO_TOKEN", "").strip()
        rama = os.getenv("PERSISTENCIA_RAMA_DATOS", "produccion-datos").strip() or "produccion-datos"
        wt_s = os.getenv("PERSISTENCIA_WORKTREE_DIR", "").strip()

        cfg = cls(
            workspace_dir=raiz,
            remote=os.getenv("PERSISTENCIA_REMOTE", "origin").strip() or "origin",
            rama_datos=rama,
            remote_url=r_url,
            remote_token=r_tok,
            worktree_dir=Path(wt_s) if wt_s else None,
            habilitada=activa_env,
            max_reintentos_push=max(1, int(os.getenv("PERSISTENCIA_MAX_REINTENTOS", "4") or "4")),
            backoff_base_s=max(0.02, float(os.getenv("PERSISTENCIA_BACKOFF_S", "0.35") or "0.35")),
        )
        if not activa_env:
            cfg.habilitada = False
            cfg.codigo_estado = "inactiva"
            cfg.motivo_estado = "PERSISTENCIA_PRODUCCION_ACTIVA desactivada en entorno."
            return cfg

        ok, cod, msg = validar_config_persistencia(cfg)
        if not ok:
            cfg.habilitada = False
            cfg.codigo_estado = cod
            cfg.motivo_estado = msg
            print(f"[PERSISTENCIA] ⚠️ Desactivada/rechazada ({cod}): {msg}")
        else:
            cfg.habilitada = True
            cfg.codigo_estado = "activa"
            cfg.motivo_estado = ""
        return cfg


def validar_config_persistencia(cfg: ConfigPersistencia) -> tuple[bool, str, str]:
    """
    Exige remoto externo dedicado (`remote_url` obligatorio, distinto de `origin`) y
    rama de datos no por defecto. Nunca permite escribir datos en el repo público.
    """
    url_cfg = (cfg.remote_url or "").strip()
    if not url_cfg:
        return (
            False,
            "desactivada_falta_remoto_url",
            "Falta PERSISTENCIA_REMOTO_URL; producción nunca escribe datos en el repo público ni usa GITHUB_TOKEN.",
        )
    origin_url = obtener_url_origin(cfg.workspace_dir)
    if origin_url and normalizar_url_git(url_cfg) == normalizar_url_git(origin_url):
        return (
            False,
            "rechazada_url_igual_origin",
            "PERSISTENCIA_REMOTO_URL coincide con 'origin'; rechazado para no escribir datos en el repo público.",
        )
    try:
        validar_rama_datos(cfg.rama_datos)
    except ValueError as exc:
        return (False, "rechazada_rama_prohibida", str(exc))
    return (True, "ok", "")


def escribir_atomico_texto(ruta: Path | str, contenido: str) -> Path:
    """Escribe un archivo de forma atómica (`tempfile` en el mismo directorio + `os.replace`)."""
    p = Path(ruta)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(contenido)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return p


def _parsear_jsonl(texto: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, ln in enumerate((texto or "").splitlines(), 1):
        s = ln.strip()
        if not s:
            continue
        try:
            out.append(json.loads(s))
        except json.JSONDecodeError as e:
            raise ValueError(f"Línea JSONL inválida #{idx}: {e}") from e
    return out


def _clave_dedup_ledger(reg: dict[str, Any]) -> tuple[str, ...]:
    estado = str(reg.get("estado_registro") or "liquidado").strip().lower()
    id_trab = str(reg.get("id_trabajo") or "").strip()
    id_part = str(reg.get("id_partido") or "").strip()
    fecha = str(reg.get("fecha") or "").strip()
    partido = str(reg.get("partido") or "").strip()
    if estado == "pre_partido" and id_trab:
        return ("pre_trab", estado, id_trab)
    if id_part:
        return ("part_fecha", estado, id_part, fecha)
    return ("nom_fecha", estado, partido, fecha)


def reconstruir_ledger_desde_remoto(
    remote_ledger_text: str | None,
    *,
    local_ledger_path: Path | str | None = None,
    entradas_extra: list[dict[str, Any]] | None = None,
    destino_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Reconstruye `ledger_oos.jsonl` partiendo SIEMPRE de la copia remota (conservando
    intactos sus índices, `prev_sha256` y `sha256`), y re-encadenando encima cualquier
    entrada local aún no presente en el remoto mediante `registrar_en_ledger_oos`.
    """
    import evaluacion_pronosticos as ep

    rem_entries = _parsear_jsonl(remote_ledger_text or "")
    loc_path = Path(local_ledger_path) if local_ledger_path else None
    loc_entries = _parsear_jsonl(loc_path.read_text(encoding="utf-8")) if (loc_path and loc_path.exists()) else []
    candidatas = list(loc_entries) + [dict(e) for e in (entradas_extra or []) if isinstance(e, dict)]

    # Validar remota antes de reconstruir encima
    fd_tmp, tmp_path_str = tempfile.mkstemp(prefix=".ledger_reconst_", suffix=".jsonl")
    os.close(fd_tmp)
    tmp_path = Path(tmp_path_str)
    try:
        if rem_entries:
            escribir_atomico_texto(
                tmp_path,
                "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rem_entries) + "\n",
            )
            ver_rem = ep.verificar_cadena_ledger_oos(str(tmp_path))
            if not ver_rem.get("ok"):
                raise ValueError(f"Cadena remota corrupta en ledger_oos.jsonl: {ver_rem.get('errores')}")
            base_entries = list(rem_entries)
        elif loc_entries:
            # Bootstrap inicial (rama remota aún sin ledger): si el local ya tiene cadena válida, se conserva intacto
            escribir_atomico_texto(
                tmp_path,
                "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in loc_entries) + "\n",
            )
            ver_loc = ep.verificar_cadena_ledger_oos(str(tmp_path))
            if ver_loc.get("ok"):
                base_entries = list(loc_entries)
                candidatas = [dict(e) for e in (entradas_extra or []) if isinstance(e, dict)]
            else:
                escribir_atomico_texto(tmp_path, "")
                base_entries = []
        else:
            escribir_atomico_texto(tmp_path, "")
            base_entries = []

        claves_presentes = {_clave_dedup_ledger(e) for e in base_entries}
        pre_hash_por_trab: dict[str, str] = {}
        pre_hash_por_clave: dict[tuple[str, str], str] = {}
        for e in base_entries:
            if str(e.get("estado_registro") or "liquidado").strip().lower() == "pre_partido":
                sh = str(e.get("sha256") or "")
                if e.get("id_trabajo") and sh:
                    pre_hash_por_trab[str(e["id_trabajo"])] = sh
                if sh:
                    pre_hash_por_clave[(str(e.get("partido") or ""), str(e.get("fecha") or "")[:10])] = sh

        n_reencadenados = 0
        for cand in candidatas:
            clave = _clave_dedup_ledger(cand)
            if clave in claves_presentes:
                continue
            estado = str(cand.get("estado_registro") or "liquidado").strip().lower()
            payload = {
                k: v for k, v in cand.items()
                if k not in ("version", "indice", "prev_sha256", "sha256", "retroalimenta_modelo")
            }
            if estado == "liquidado":
                id_trab = str(payload.get("id_trabajo") or "")
                cl_pf = (str(payload.get("partido") or ""), str(payload.get("fecha") or "")[:10])
                pre_sh_actual = pre_hash_por_trab.get(id_trab) or pre_hash_por_clave.get(cl_pf)
                if pre_sh_actual:
                    payload["pre_partido_sha256"] = pre_sh_actual
                    if isinstance(payload.get("modo_sombra"), dict):
                        payload["modo_sombra"] = {**payload["modo_sombra"], "pre_partido_sha256": pre_sh_actual}

            res_ins = ep.registrar_en_ledger_oos(payload, ruta_ledger=str(tmp_path))
            if res_ins.get("registrado"):
                n_reencadenados += 1
                claves_presentes.add(clave)
                if estado == "pre_partido" and res_ins.get("sha256"):
                    sh_n = str(res_ins["sha256"])
                    if payload.get("id_trabajo"):
                        pre_hash_por_trab[str(payload["id_trabajo"])] = sh_n
                    pre_hash_por_clave[(str(payload.get("partido") or ""), str(payload.get("fecha") or "")[:10])] = sh_n

        ver_final = ep.verificar_cadena_ledger_oos(str(tmp_path))
        if not ver_final.get("ok"):
            raise ValueError(f"Cadena reconstruida inválida: {ver_final.get('errores')}")

        contenido_final = tmp_path.read_text(encoding="utf-8")
        destinos: list[Path] = []
        if destino_path is not None:
            destinos.append(Path(destino_path))
        if loc_path is not None and (destino_path is None or Path(destino_path).resolve() != loc_path.resolve()):
            destinos.append(loc_path)
        for dp in destinos:
            escribir_atomico_texto(dp, contenido_final)

        return {
            "ok": True,
            "n_remoto": len(rem_entries),
            "n_final": int(ver_final.get("entradas") or 0),
            "n_reencadenados": n_reencadenados,
            "ultimo_sha256": ver_final.get("ultimo_sha256"),
            "contenido": contenido_final,
        }
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _censurar_secreto(
    texto: str,
    token: str | None = None,
    remote_url: str | None = None,
) -> str:
    s = str(texto or "")
    tok = (token or "").strip()
    if tok:
        if tok in s:
            s = s.replace(tok, "***")
        tok_b64 = base64.b64encode(f"x-access-token:{tok}".encode("utf-8")).decode("ascii")
        if tok_b64 in s:
            s = s.replace(tok_b64, "***")
    s = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", s)
    rurl = (remote_url or "").strip()
    if rurl and rurl.lower().startswith(("http://", "https://")):
        base_sin_slash = rurl.rstrip("/")
        for cand in (rurl, base_sin_slash, base_sin_slash[:-4] if base_sin_slash.endswith(".git") else f"{base_sin_slash}.git"):
            if cand and cand in s:
                s = s.replace(cand, "[remoto-datos]")
    return s


def _run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=check,
    )


def resolver_objetivo_remoto(ws: Path, cfg: ConfigPersistencia) -> tuple[str, str]:
    """
    Resuelve `(alias_remoto, url_efectiva)` exigiendo remoto dedicado distinto de `origin`.
    """
    ok, _, msg = validar_config_persistencia(cfg)
    if not ok:
        raise ValueError(msg)
    return (cfg.remote or "datos"), (cfg.remote_url or "").strip()


def detectar_rama_defecto_remota(
    ws: Path,
    cfg: ConfigPersistencia,
    target_url: str,
) -> tuple[bool, str | None, str | None]:
    """Consulta `HEAD` remoto con auth; devuelve `(ok, rama_defecto, error_censurado)`."""
    cp = _run_git_con_auth(
        ws,
        cfg,
        "ls-remote",
        "--symref",
        target_url,
        "HEAD",
        target_url=target_url,
        check=False,
    )
    if cp.returncode != 0:
        err = _censurar_secreto((cp.stderr or cp.stdout or "fallo ls-remote HEAD").strip(), cfg.remote_token, target_url)
        return (False, None, err)
    for ln in (cp.stdout or "").splitlines():
        m = re.match(r"^ref:\s+refs/heads/([^\s]+)\s+HEAD$", ln.strip())
        if m:
            return (True, m.group(1).strip(), None)
    return (True, None, None)


def _crear_script_askpass(dir_tmp: Path) -> Path:
    helper = dir_tmp / "askpass_helper.py"
    helper.write_text(
        "import os, sys\n"
        "p = (sys.argv[1] if len(sys.argv) > 1 else '').lower()\n"
        "print('x-access-token' if 'username' in p else os.environ.get('_PERSISTENCIA_GIT_TOKEN', ''))\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        sc = dir_tmp / f"askpass_{os.getpid()}.cmd"
        sc.write_text(
            f'@echo off\r\n"{sys.executable}" "{helper}" "%~1"\r\n',
            encoding="utf-8",
        )
    else:
        sc = dir_tmp / f"askpass_{os.getpid()}.sh"
        sc.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{helper}" "$1"\n',
            encoding="utf-8",
        )
        os.chmod(sc, 0o700)
    return sc


def _run_git_con_auth(
    cwd: Path,
    cfg: ConfigPersistencia,
    *args: str,
    target_url: str = "",
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Ejecuta Git autenticando por `GIT_CONFIG_COUNT` en entorno (sin secretos en argv y
    anulando cualquier `http.*.extraheader` heredado de `actions/checkout`).
    """
    tok = (cfg.remote_token or "").strip()
    es_http = target_url.lower().startswith(("http://", "https://"))
    if not tok or not es_http:
        return _run_git(cwd, *args, check=check)

    partes = urlsplit(target_url)
    host_port = f"{partes.hostname}{f':{partes.port}' if partes.port else ''}" if partes.hostname else partes.netloc
    base_host_clean = f"{partes.scheme}://{host_port}/" if partes.scheme and host_port else ""
    base_host_netloc = f"{partes.scheme}://{partes.netloc}/" if partes.scheme and partes.netloc else ""
    tok_b64 = base64.b64encode(f"x-access-token:{tok}".encode("utf-8")).decode("ascii")

    neutral_dir = Path(tempfile.mkdtemp(prefix=".git_auth_"))
    try:
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        cfg_entries: list[tuple[str, str]] = [
            ("credential.helper", ""),
            ("core.askPass", ""),
        ]
        if base_host_netloc and base_host_netloc != base_host_clean:
            cfg_entries.append((f"http.{base_host_netloc}.extraheader", ""))
        if base_host_clean:
            cfg_entries.append((f"http.{base_host_clean}.extraheader", ""))
            cfg_entries.append((f"http.{base_host_clean}.extraheader", f"Authorization: Basic {tok_b64}"))
        env["GIT_CONFIG_COUNT"] = str(len(cfg_entries))
        for idx_c, (k_c, v_c) in enumerate(cfg_entries):
            env[f"GIT_CONFIG_KEY_{idx_c}"] = k_c
            env[f"GIT_CONFIG_VALUE_{idx_c}"] = v_c

        exec_cwd = neutral_dir if (args and args[0] == "ls-remote") else cwd
        cp = subprocess.run(
            ["git", *args],
            cwd=str(exec_cwd),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        cp = subprocess.CompletedProcess(
            args=cp.args,
            returncode=cp.returncode,
            stdout=_censurar_secreto(cp.stdout, tok, target_url),
            stderr=_censurar_secreto(cp.stderr, tok, target_url),
        )
        if check and cp.returncode != 0:
            raise subprocess.CalledProcessError(cp.returncode, ["git", *args], output=cp.stdout, stderr=cp.stderr)
        return cp
    finally:
        _rmtree_seguro(neutral_dir)


def _merge_registro_evaluaciones(remoto_txt: str | None, local_path: Path, dst_path: Path) -> bool:
    rem_data = json.loads(remoto_txt) if remoto_txt else {"evaluaciones": {}}
    loc_data = json.loads(local_path.read_text(encoding="utf-8")) if local_path.exists() else {"evaluaciones": {}}
    merged = {"evaluaciones": dict(rem_data.get("evaluaciones") or {})}
    for k, v in (loc_data.get("evaluaciones") or {}).items():
        prev = merged["evaluaciones"].get(k)
        if prev is None:
            merged["evaluaciones"][k] = v
        elif isinstance(v, dict) and isinstance(prev, dict):
            # Preferir estado envio_confirmado o marca más reciente
            if str(v.get("estado") or "") == "envio_confirmado" and str(prev.get("estado") or "") != "envio_confirmado":
                merged["evaluaciones"][k] = v
            elif str(v.get("actualizado_en") or "") > str(prev.get("actualizado_en") or ""):
                merged["evaluaciones"][k] = v
    txt_new = json.dumps(merged, ensure_ascii=False, indent=1)
    cambio = not dst_path.exists() or dst_path.read_text(encoding="utf-8") != txt_new
    if merged["evaluaciones"] or dst_path.exists():
        escribir_atomico_texto(dst_path, txt_new)
    return cambio


def _leer_archivo_git_ref(repo_dir: Path, ref: str, rel_path: str) -> str | None:
    cp = _run_git(repo_dir, "show", f"{ref}:{rel_path}", check=False)
    if cp.returncode == 0:
        return cp.stdout
    return None


def _listar_archivos_git_ref(repo_dir: Path, ref: str, prefix: str) -> list[str]:
    cp = _run_git(repo_dir, "ls-tree", "-r", "--name-only", ref, "--", prefix, check=False)
    if cp.returncode != 0:
        return []
    return [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]


def hidratar_desde_remoto_datos(cfg: ConfigPersistencia) -> dict[str, Any]:
    """
    Al iniciar un runner limpio, trae del remoto privado (`cfg.remote_url`) en `cfg.rama_datos`
    los snapshots, evaluaciones, eventos y reconstruye `ledger_oos.jsonl` desde la copia remota.
    Distingue explícitamente un remoto válido sin `rama_datos` (`ok=True, rama_existe=False`)
    de un fallo de autenticación/red (`ok=False`).
    """
    ok_cfg, cod_cfg, msg_cfg = validar_config_persistencia(cfg)
    if not ok_cfg:
        return {
            "ok": False,
            "codigo_error": cod_cfg,
            "error": msg_cfg,
            "rama_existe": None,
            "n_pronosticos_hidratados": 0,
            "n_evaluaciones_hidratadas": 0,
            "n_eventos_hidratados": 0,
            "ledger": {"ok": False, "n_remoto": 0, "n_final": 0, "n_reencadenados": 0},
        }

    ws = cfg.workspace_dir
    _, target_url = resolver_objetivo_remoto(ws, cfg)
    ok_head, rama_def_rem, err_head = detectar_rama_defecto_remota(ws, cfg, target_url)
    if not ok_head:
        return {
            "ok": False,
            "codigo_error": "error_remoto_auth_o_red",
            "error": err_head or "No se pudo consultar el remoto de datos.",
            "rama_existe": None,
            "n_pronosticos_hidratados": 0,
            "n_evaluaciones_hidratadas": 0,
            "n_eventos_hidratados": 0,
            "ledger": {"ok": False, "n_remoto": 0, "n_final": 0, "n_reencadenados": 0},
        }
    try:
        validar_rama_datos(cfg.rama_datos, rama_defecto_remota=rama_def_rem)
    except ValueError as exc_r:
        return {
            "ok": False,
            "codigo_error": "rechazada_rama_prohibida",
            "error": str(exc_r),
            "rama_existe": None,
            "n_pronosticos_hidratados": 0,
            "n_evaluaciones_hidratadas": 0,
            "n_eventos_hidratados": 0,
            "ledger": {"ok": False, "n_remoto": 0, "n_final": 0, "n_reencadenados": 0},
        }

    cp_ls = _run_git_con_auth(
        ws,
        cfg,
        "ls-remote",
        "--heads",
        target_url,
        f"refs/heads/{cfg.rama_datos}",
        target_url=target_url,
        check=False,
    )
    if cp_ls.returncode != 0:
        return {
            "ok": False,
            "codigo_error": "error_remoto_auth_o_red",
            "error": _censurar_secreto((cp_ls.stderr or cp_ls.stdout or "fallo ls-remote").strip(), cfg.remote_token, target_url),
            "rama_existe": None,
            "n_pronosticos_hidratados": 0,
            "n_evaluaciones_hidratadas": 0,
            "n_eventos_hidratados": 0,
            "ledger": {"ok": False, "n_remoto": 0, "n_final": 0, "n_reencadenados": 0},
        }
    if not (cp_ls.stdout or "").strip():
        return {
            "ok": True,
            "rama_existe": False,
            "n_pronosticos_hidratados": 0,
            "n_evaluaciones_hidratadas": 0,
            "n_eventos_hidratados": 0,
            "ledger": {"ok": True, "n_remoto": 0, "n_final": 0, "n_reencadenados": 0},
        }

    ref_full = f"refs/persistencia_remoto/{cfg.rama_datos}"
    cp_fetch = _run_git_con_auth(
        ws,
        cfg,
        "fetch",
        target_url,
        f"refs/heads/{cfg.rama_datos}:{ref_full}",
        target_url=target_url,
        check=False,
    )
    cp_rev = _run_git(ws, "rev-parse", "--verify", ref_full, check=False)
    if cp_fetch.returncode != 0 or cp_rev.returncode != 0:
        return {
            "ok": False,
            "codigo_error": "error_fetch_remoto",
            "error": _censurar_secreto((cp_fetch.stderr or cp_fetch.stdout or "fallo fetch").strip(), cfg.remote_token, target_url),
            "rama_existe": True,
            "n_pronosticos_hidratados": 0,
            "n_evaluaciones_hidratadas": 0,
            "n_eventos_hidratados": 0,
            "ledger": {"ok": False, "n_remoto": 0, "n_final": 0, "n_reencadenados": 0},
        }

    n_pron = 0
    for rel in _listar_archivos_git_ref(ws, ref_full, DIR_PRONOSTICOS_REL):
        if not rel.endswith(".json"):
            continue
        dst = ws / rel
        if not dst.exists():
            cp_cat = subprocess.run(
                ["git", "show", f"{ref_full}:{rel}"],
                cwd=str(ws), capture_output=True, check=False,
            )
            if cp_cat.returncode == 0:
                dst.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_n = tempfile.mkstemp(prefix=".hyd_", suffix=".json", dir=str(dst.parent))
                with os.fdopen(fd, "wb") as fh:
                    fh.write(cp_cat.stdout)
                os.replace(tmp_n, dst)
                n_pron += 1

    n_eval = 0
    n_ev = 0
    for rel in _listar_archivos_git_ref(ws, ref_full, DIR_EVALUACION_REL):
        if rel in (RUTA_LEDGER_REL, RUTA_REGISTRO_EVAL_REL):
            continue
        dst = ws / rel
        if not dst.exists():
            cp_cat = subprocess.run(
                ["git", "show", f"{ref_full}:{rel}"],
                cwd=str(ws), capture_output=True, check=False,
            )
            if cp_cat.returncode == 0:
                dst.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_n = tempfile.mkstemp(prefix=".hyd_", suffix=dst.suffix or ".tmp", dir=str(dst.parent))
                with os.fdopen(fd, "wb") as fh:
                    fh.write(cp_cat.stdout)
                os.replace(tmp_n, dst)
                if rel.startswith(f"{DIR_EVENTOS_REL}/"):
                    n_ev += 1
                else:
                    n_eval += 1

    reg_rem_txt = _leer_archivo_git_ref(ws, ref_full, RUTA_REGISTRO_EVAL_REL)
    if reg_rem_txt is not None:
        reg_dst = ws / RUTA_REGISTRO_EVAL_REL
        _merge_registro_evaluaciones(reg_rem_txt, reg_dst, reg_dst)

    rem_ledger_txt = _leer_archivo_git_ref(ws, ref_full, RUTA_LEDGER_REL)
    loc_ledger = ws / RUTA_LEDGER_REL
    res_led = reconstruir_ledger_desde_remoto(
        rem_ledger_txt,
        local_ledger_path=loc_ledger,
        destino_path=loc_ledger,
    )
    return {
        "ok": True,
        "rama_existe": True,
        "remoto_sha": cp_rev.stdout.strip(),
        "n_pronosticos_hidratados": n_pron,
        "n_evaluaciones_hidratadas": n_eval,
        "n_eventos_hidratados": n_ev,
        "ledger": {k: v for k, v in res_led.items() if k != "contenido"},
    }


def _copiar_si_nuevo_o_actualizado(src: Path, dst: Path) -> bool:
    if not src.exists() or not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    raw = src.read_bytes()
    if dst.exists():
        if dst.read_bytes() == raw:
            return False
        # Para snapshots en pronosticos/: si el snapshot local tiene más metadatos de telegram
        # (ej. telegram_envio recién adjuntado sin alterar selecciones) permitimos actualizarlo.
        try:
            old_j = json.loads(dst.read_text(encoding="utf-8"))
            new_j = json.loads(raw.decode("utf-8"))
            if (
                old_j.get("id_trabajo") == new_j.get("id_trabajo")
                and old_j.get("selecciones") == new_j.get("selecciones")
                and bool(new_j.get("telegram_envio"))
                and not bool(old_j.get("telegram_envio"))
            ):
                pass
            elif dst.parent.name == "pronosticos":
                # Inmutable: el pasado no se reescribe
                return False
        except Exception:
            if dst.parent.name == "pronosticos":
                return False
    fd, tmp_n = tempfile.mkstemp(prefix=".cp_", suffix=dst.suffix or ".tmp", dir=str(dst.parent))
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
        fh.flush()
    os.replace(tmp_n, dst)
    return True


def _rmtree_onerror(func: Any, path: str, _exc: Any) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _rmtree_seguro(ruta: Path) -> None:
    if ruta.exists():
        shutil.rmtree(ruta, onerror=_rmtree_onerror)


def _preparar_staging_git(cfg: ConfigPersistencia, stage_dir: Path) -> bool:
    """Clona/inicializa un árbol aislado en `stage_dir` en la punta de `<remote>/<rama_datos>`."""
    _rmtree_seguro(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    ws = cfg.workspace_dir
    _, remote_url = resolver_objetivo_remoto(ws, cfg)
    ok_head, rama_def_rem, err_head = detectar_rama_defecto_remota(ws, cfg, remote_url)
    if not ok_head:
        raise RuntimeError(err_head or "No se pudo consultar el remoto de datos.")
    validar_rama_datos(cfg.rama_datos, rama_defecto_remota=rama_def_rem)

    _run_git(stage_dir, "init")
    _run_git(stage_dir, "config", "user.name", cfg.author_name)
    _run_git(stage_dir, "config", "user.email", cfg.author_email)
    _run_git(stage_dir, "remote", "add", cfg.remote, remote_url)

    cp_fetch = _run_git_con_auth(
        stage_dir,
        cfg,
        "fetch",
        "--depth=1",
        cfg.remote,
        cfg.rama_datos,
        target_url=remote_url,
        check=False,
    )
    cp_rev = _run_git(stage_dir, "rev-parse", "--verify", "FETCH_HEAD", check=False)
    if cp_fetch.returncode == 0 and cp_rev.returncode == 0:
        _run_git(stage_dir, "checkout", "-B", cfg.rama_datos, "FETCH_HEAD")
        return True
    _run_git(stage_dir, "checkout", "--orphan", cfg.rama_datos)
    return False


def sincronizar_y_push_datos(
    cfg: ConfigPersistencia,
    *,
    archivos_rel: list[str] | None = None,
    entradas_ledger: list[dict[str, Any]] | None = None,
    mensaje_commit: str | None = None,
    simular_antes_de_push: Callable[[int], None] | None = None,
    solo_archivos_indicados: bool = False,
) -> dict[str, Any]:
    """
    Ejecuta en un repositorio staging aislado contra `cfg.remote_url`:
      validar remoto/rama -> fetch remoto -> reconstruir ledger desde copia remota ->
      copiar archivos -> commit -> push con reintentos ante conflictos.
    """
    ok_cfg, cod_cfg, msg_cfg = validar_config_persistencia(cfg)
    if not ok_cfg:
        return {"ok": False, "pushed": False, "codigo_error": cod_cfg, "errores": [msg_cfg]}

    ws = cfg.workspace_dir
    base_tmp = cfg.worktree_dir if cfg.worktree_dir is not None else Path(tempfile.gettempdir()) / "agente_futbol_persistencia"
    base_tmp.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=f"stage_{os.getpid()}_", dir=str(base_tmp)))

    errores: list[str] = []
    try:
        for intento in range(1, cfg.max_reintentos_push + 1):
            rama_existia = _preparar_staging_git(cfg, stage_dir)

            # 1. Copiar archivos nuevos/actualizados de pronósticos, evaluación y eventos
            candidatos_rel: set[str] = set()
            if not solo_archivos_indicados:
                for d_rel in (DIR_PRONOSTICOS_REL, DIR_EVALUACION_REL, DIR_EVENTOS_REL):
                    src_dir = ws / d_rel
                    if src_dir.exists():
                        for p in src_dir.glob("*"):
                            if p.is_file() and (p.suffix in (".json", ".md")) and p.name != "registro_evaluaciones.json":
                                candidatos_rel.add(p.relative_to(ws).as_posix())
            for r_ex in (archivos_rel or []):
                candidatos_rel.add(Path(r_ex).as_posix())
            for r_cfg in cfg.archivos_extra_rel:
                candidatos_rel.add(Path(r_cfg).as_posix())

            for rel in sorted(candidatos_rel):
                if rel == RUTA_LEDGER_REL or rel == RUTA_REGISTRO_EVAL_REL:
                    continue
                _copiar_si_nuevo_o_actualizado(ws / rel, stage_dir / rel)

            # 2. Merge idempotente de registro_evaluaciones.json si existe local o remoto
            reg_rem = _leer_archivo_git_ref(stage_dir, "HEAD", RUTA_REGISTRO_EVAL_REL) if rama_existia else None
            if reg_rem is not None or (ws / RUTA_REGISTRO_EVAL_REL).exists():
                _merge_registro_evaluaciones(reg_rem, ws / RUTA_REGISTRO_EVAL_REL, stage_dir / RUTA_REGISTRO_EVAL_REL)

            # 3. Ledger SIEMPRE reconstruido desde la copia remota actual
            rem_ledger_txt = _leer_archivo_git_ref(stage_dir, "HEAD", RUTA_LEDGER_REL) if rama_existia else None
            res_led = reconstruir_ledger_desde_remoto(
                rem_ledger_txt,
                local_ledger_path=ws / RUTA_LEDGER_REL,
                entradas_extra=entradas_ledger,
                destino_path=stage_dir / RUTA_LEDGER_REL,
            )

            # 4. Stage & commit si hay cambios
            _run_git(stage_dir, "add", "-A")
            st = _run_git(stage_dir, "status", "--porcelain")
            if not st.stdout.strip():
                # Asegurar copia local alineada con remoto
                escribir_atomico_texto(ws / RUTA_LEDGER_REL, res_led["contenido"])
                return {
                    "ok": True,
                    "pushed": False,
                    "sin_cambios": True,
                    "intentos": intento,
                    "ledger": {k: v for k, v in res_led.items() if k != "contenido"},
                }

            msg = mensaje_commit or f"datos: sync {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
            _run_git(stage_dir, "commit", "-m", msg)

            if simular_antes_de_push is not None:
                simular_antes_de_push(intento)

            _, remote_url = resolver_objetivo_remoto(ws, cfg)
            cp_push = _run_git_con_auth(
                stage_dir,
                cfg,
                "push",
                cfg.remote,
                f"HEAD:refs/heads/{cfg.rama_datos}",
                target_url=remote_url,
                check=False,
            )
            if cp_push.returncode == 0:
                escribir_atomico_texto(ws / RUTA_LEDGER_REL, res_led["contenido"])
                if (stage_dir / RUTA_REGISTRO_EVAL_REL).exists():
                    shutil.copy2(stage_dir / RUTA_REGISTRO_EVAL_REL, ws / RUTA_REGISTRO_EVAL_REL)
                cp_head = _run_git(stage_dir, "rev-parse", "HEAD", check=False)
                return {
                    "ok": True,
                    "pushed": True,
                    "sin_cambios": False,
                    "intentos": intento,
                    "commit_sha": cp_head.stdout.strip() if cp_head.returncode == 0 else None,
                    "ledger": {k: v for k, v in res_led.items() if k != "contenido"},
                }

            err_txt = _censurar_secreto((cp_push.stderr or cp_push.stdout or "push fallido").strip(), cfg.remote_token, remote_url)
            errores.append(f"intento {intento}: {err_txt}")
            if intento < cfg.max_reintentos_push:
                time.sleep(cfg.backoff_base_s * intento + random.uniform(0.0, cfg.backoff_base_s * 0.5))

        return {
            "ok": False,
            "pushed": False,
            "intentos": cfg.max_reintentos_push,
            "errores": errores,
        }
    finally:
        _rmtree_seguro(stage_dir)


class ColaPersistenciaGit:
    """
    Cola asíncrona FIFO atendida por un único hilo worker serializado para empujar
    snapshots y actualizaciones de `ledger_oos.jsonl` a la rama de datos.
    """

    def __init__(
        self,
        cfg: ConfigPersistencia,
        *,
        on_resultado: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self._on_resultado = on_resultado
        self._q: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._cerrando = threading.Event()
        self.historial_resultados: list[dict[str, Any]] = []

    def iniciar(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._cerrando.clear()
            self._worker = threading.Thread(
                target=self._bucle_worker,
                name="ColaPersistenciaGit",
                daemon=True,
            )
            self._worker.start()

    def _bucle_worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                break
            ev_done: threading.Event | None = item.get("evento_done")
            try:
                res = sincronizar_y_push_datos(
                    self.cfg,
                    archivos_rel=item.get("archivos_rel"),
                    entradas_ledger=item.get("entradas_ledger"),
                    mensaje_commit=item.get("mensaje_commit"),
                    simular_antes_de_push=item.get("simular_antes_de_push"),
                    solo_archivos_indicados=bool(item.get("solo_archivos_indicados", False)),
                )
            except Exception as exc:
                err_c = _censurar_secreto(f"{type(exc).__name__}: {exc}", self.cfg.remote_token, self.cfg.remote_url)
                res = {"ok": False, "pushed": False, "errores": [err_c]}
            finally:
                with self._lock:
                    item["resultado"] = res
                    self.historial_resultados.append({
                        "tipo": item.get("tipo"),
                        "id_trabajo": item.get("id_trabajo"),
                        **res,
                    })
                if self._on_resultado is not None:
                    try:
                        self._on_resultado(dict(res))
                    except Exception:
                        pass
                if ev_done is not None:
                    ev_done.set()
                self._q.task_done()

    def encolar(
        self,
        *,
        tipo: str = "evento",
        id_trabajo: str | None = None,
        archivos_rel: list[str] | None = None,
        entradas_ledger: list[dict[str, Any]] | None = None,
        mensaje_commit: str | None = None,
        simular_antes_de_push: Callable[[int], None] | None = None,
        solo_archivos_indicados: bool = False,
        esperar: bool = False,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Encola una operación de persistencia. Si `esperar=True`, bloquea hasta terminar."""
        if self._cerrando.is_set():
            return {"encolado": False, "motivo": "cola_cerrando"}
        self.iniciar()
        ev = threading.Event()
        item: dict[str, Any] = {
            "tipo": tipo,
            "id_trabajo": id_trabajo,
            "archivos_rel": list(archivos_rel or []),
            "entradas_ledger": list(entradas_ledger or []),
            "mensaje_commit": mensaje_commit,
            "simular_antes_de_push": simular_antes_de_push,
            "solo_archivos_indicados": solo_archivos_indicados,
            "evento_done": ev,
            "resultado": None,
        }
        self._q.put(item)
        if esperar:
            ev.wait(timeout=timeout)
            return dict(item.get("resultado") or {"ok": False, "timeout": True})
        return {"encolado": True, "evento_done": ev, "item": item}

    def esperar_vacia(self, timeout: float = 45.0) -> bool:
        """Espera a que todos los elementos encolados terminen su sincronización/push."""
        fin = time.monotonic() + timeout
        while self._q.unfinished_tasks > 0 and time.monotonic() < fin:
            time.sleep(0.02)
        return self._q.unfinished_tasks == 0

    def cerrar(self, timeout: float = 20.0) -> bool:
        """Drena elementos pendientes y detiene ordenadamente el hilo de cola."""
        self._cerrando.set()
        if self._worker is None or not self._worker.is_alive():
            return True
        self._q.put(None)
        self._worker.join(timeout=timeout)
        return not self._worker.is_alive()


def inicializar_produccion_limpia(
    workspace_dir: Path | str | None = None,
    *,
    remote: str = "datos",
    remote_url: str | None = None,
    remote_token: str | None = None,
    rama_datos: str = "produccion-datos",
) -> dict[str, Any]:
    """
    Crea de forma idempotente la rama `produccion-datos` en el remoto privado externo
    con un `ledger_oos.jsonl` limpio que contenga únicamente entradas reales válidas
    (TB, IF y Colombia-Chile), excluyendo cualquier registro de prueba sintético.
    Si la rama ya existe en el remoto, no la sobrescribe.
    """
    ws = Path(workspace_dir) if workspace_dir else Path(__file__).resolve().parent
    rama_norm = (rama_datos or "").strip()
    if rama_norm != "produccion-datos":
        raise ValueError(f"La rama de producción debe ser 'produccion-datos', recibido: {rama_datos!r}")

    r_url = (remote_url if remote_url is not None else os.getenv("PERSISTENCIA_REMOTO_URL", "")).strip()
    r_tok = (remote_token if remote_token is not None else os.getenv("PERSISTENCIA_REMOTO_TOKEN", "")).strip()
    cfg = ConfigPersistencia(
        workspace_dir=ws,
        remote=remote,
        rama_datos=rama_norm,
        remote_url=r_url,
        remote_token=r_tok,
        habilitada=True,
    )
    ok_cfg, cod_cfg, msg_cfg = validar_config_persistencia(cfg)
    if not ok_cfg:
        return {"ok": False, "codigo_error": cod_cfg, "error": msg_cfg}

    ok_head, rama_def_rem, err_head = detectar_rama_defecto_remota(ws, cfg, cfg.remote_url)
    if not ok_head:
        return {"ok": False, "codigo_error": "error_remoto_auth_o_red", "error": err_head or "Fallo remoto"}
    validar_rama_datos(rama_norm, rama_defecto_remota=rama_def_rem)

    cp_ls = _run_git_con_auth(
        ws,
        cfg,
        "ls-remote",
        "--heads",
        cfg.remote_url,
        f"refs/heads/{rama_norm}",
        target_url=cfg.remote_url,
        check=False,
    )
    if cp_ls.returncode != 0:
        return {
            "ok": False,
            "codigo_error": "error_remoto_auth_o_red",
            "error": _censurar_secreto((cp_ls.stderr or cp_ls.stdout or "fallo ls-remote").strip(), cfg.remote_token, cfg.remote_url),
        }
    rem_line = (cp_ls.stdout or "").strip()
    if rem_line:
        return {
            "ok": True,
            "creada": False,
            "ya_existia": True,
            "rama": rama_norm,
            "remoto_sha": rem_line.split()[0],
        }

    loc_ledger = ws / RUTA_LEDGER_REL
    if not loc_ledger.exists():
        return {"ok": False, "codigo_error": "sin_ledger_base", "error": f"No existe {RUTA_LEDGER_REL}"}
    entries_raw = _parsear_jsonl(loc_ledger.read_text(encoding="utf-8"))
    limpias = [
        e for e in entries_raw
        if not e.get("es_registro_prueba_mentira")
        and str(e.get("estado_registro") or "").strip().lower() != "simulado_prueba"
        and str(e.get("tipo_partido") or "").strip().lower() != "simulado_prueba"
    ]
    if not limpias:
        return {"ok": False, "codigo_error": "ledger_limpio_vacio", "error": "El ledger limpio quedó vacío."}

    import evaluacion_pronosticos as ep
    stage_dir = Path(tempfile.mkdtemp(prefix=f"init_prod_{os.getpid()}_"))
    try:
        _run_git(stage_dir, "init")
        _run_git(stage_dir, "config", "user.name", cfg.author_name)
        _run_git(stage_dir, "config", "user.email", cfg.author_email)
        _run_git(stage_dir, "checkout", "--orphan", rama_norm)
        _run_git(stage_dir, "remote", "add", cfg.remote, cfg.remote_url)

        dst_ledger = stage_dir / RUTA_LEDGER_REL
        fd_tmp, tmp_src = tempfile.mkstemp(prefix=".clean_src_", suffix=".jsonl")
        os.close(fd_tmp)
        try:
            escribir_atomico_texto(
                tmp_src,
                "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in limpias) + "\n",
            )
            ver_clean = ep.verificar_cadena_ledger_oos(tmp_src)
            if not ver_clean.get("ok"):
                raise ValueError(f"Ledger base limpio inválido: {ver_clean.get('errores')}")
            escribir_atomico_texto(dst_ledger, Path(tmp_src).read_text(encoding="utf-8"))
        finally:
            try:
                Path(tmp_src).unlink(missing_ok=True)
            except OSError:
                pass

        _run_git(stage_dir, "add", RUTA_LEDGER_REL)
        _run_git(stage_dir, "commit", "-m", "datos(init): crear rama produccion-datos con ledger limpio (TB, IF, Colombia vs Chile)")
        cp_push = _run_git_con_auth(
            stage_dir,
            cfg,
            "push",
            cfg.remote,
            f"HEAD:refs/heads/{rama_norm}",
            target_url=cfg.remote_url,
            check=False,
        )
        if cp_push.returncode != 0:
            return {
                "ok": False,
                "codigo_error": "error_push_init",
                "error": _censurar_secreto((cp_push.stderr or cp_push.stdout or "fallo push").strip(), cfg.remote_token, cfg.remote_url),
            }
        commit_sha = _run_git(stage_dir, "rev-parse", "HEAD", check=True).stdout.strip()
        return {
            "ok": True,
            "creada": True,
            "ya_existia": False,
            "rama": rama_norm,
            "commit_sha": commit_sha,
            "archivos_escritos": [RUTA_LEDGER_REL],
            "n_entradas": int(ver_clean.get("entradas") or 0),
            "ultimo_sha256": ver_clean.get("ultimo_sha256"),
            "partidos": [str(r.get("partido") or "") for r in limpias],
        }
    finally:
        _rmtree_seguro(stage_dir)


def ejecutar_smoke_prueba(
    workspace_dir: Path | str | None = None,
    *,
    remote: str = "datos",
    remote_url: str | None = None,
    remote_token: str | None = None,
    rama_pruebas: str = "pruebas-persistencia-datos",
    solo_local_sin_push: bool = False,
) -> dict[str, Any]:
    """
    Escribe un registro sintético claramente etiquetado (`es_registro_prueba_mentira=True`,
    `tipo_partido='simulado_prueba'`, `entra_promedios=False`, `entra_calibracion=False`)
    en una ruta de pruebas y, si `solo_local_sin_push=False`, lo sincroniza en `rama_pruebas`
    del remoto externo privado (`PERSISTENCIA_REMOTO_URL`, nunca `origin`).
    """
    rama_norm = validar_rama_datos(rama_pruebas)
    if rama_norm == "produccion-datos":
        raise ValueError(f"Rama de pruebas inválida o protegida: {rama_pruebas!r}")
    ws = Path(workspace_dir) if workspace_dir else Path(__file__).resolve().parent
    ts = datetime.now(timezone.utc)
    ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = os.getenv("GITHUB_RUN_ID", f"local_{os.getpid()}")
    id_trabajo = f"smoke_r13_{run_id}"

    snap_fake = {
        "esquema": "pronostico-v1",
        "id_trabajo": id_trabajo,
        "es_registro_prueba_mentira": True,
        "equipos": {"local": "Equipo Prueba A", "visitante": "Equipo Prueba B"},
        "partido": {"fecha": ts.strftime("%Y-%m-%d"), "competicion": "Simulacro Persistencia R13"},
        "tipo_partido": "simulado_prueba",
        "entra_promedios": False,
        "entra_calibracion": False,
        "generado_en": ts_iso,
        "publicado_en": None,
        "telegram_message_id": None,
        "telegram_message_date": None,
        "modelo": {"nombre": "smoke_r13", "version": "test"},
        "datos_corte": {"origen": "workflow_prueba_sin_telegram"},
        "umbral_publicado": 0.70,
        "selecciones": [],
        "selecciones_publicadas": [],
        "referencia": "workflow_test_persistencia_datos",
        "inmutable": True,
    }
    rel_snap = f"{DIR_EVENTOS_REL}/smoke_{id_trabajo}.json"
    escribir_atomico_texto(ws / rel_snap, json.dumps(snap_fake, ensure_ascii=False, indent=2) + "\n")

    entrada_fake = {
        "estado_registro": "simulado_prueba",
        "es_registro_prueba_mentira": True,
        "id_trabajo": id_trabajo,
        "id_partido": f"SMOKE_{run_id}",
        "partido": "Equipo Prueba A vs Equipo Prueba B",
        "fecha": ts.strftime("%Y-%m-%d"),
        "competicion": "Simulacro Persistencia R13",
        "tipo_partido": "simulado_prueba",
        "entra_promedios": False,
        "entra_calibracion": False,
        "generado_en": ts_iso,
    }

    if solo_local_sin_push:
        return {
            "ok": True,
            "modo": "solo_local_sin_push",
            "id_trabajo": id_trabajo,
            "archivo_rel": rel_snap,
            "entrada_ledger": entrada_fake,
        }

    r_url = (remote_url if remote_url is not None else os.getenv("PERSISTENCIA_REMOTO_URL", "")).strip()
    r_tok = (remote_token if remote_token is not None else os.getenv("PERSISTENCIA_REMOTO_TOKEN", "")).strip()
    cfg = ConfigPersistencia(
        workspace_dir=ws,
        remote=remote,
        rama_datos=rama_norm,
        remote_url=r_url,
        remote_token=r_tok,
        habilitada=True,
    )
    ok_cfg, cod_cfg, msg_cfg = validar_config_persistencia(cfg)
    if not ok_cfg:
        return {"ok": False, "codigo_error": cod_cfg, "error": msg_cfg}

    hid = hidratar_desde_remoto_datos(cfg)
    if not hid.get("ok"):
        return {
            "ok": False,
            "id_trabajo": id_trabajo,
            "rama_pruebas": rama_norm, "remoto_configurado": "url_externa_env",
            "hidratacion": hid,
            "push": {"ok": False, "pushed": False, "errores": [hid.get("error") or "fallo hidratacion"]},
        }
    cola = ColaPersistenciaGit(cfg)
    res_push = cola.encolar(
        tipo="smoke_prueba",
        id_trabajo=id_trabajo,
        archivos_rel=[rel_snap],
        entradas_ledger=[entrada_fake],
        mensaje_commit=f"test(r15): registro smoke ficticio {id_trabajo} [skip ci]",
        solo_archivos_indicados=True,
        esperar=True,
        timeout=60.0,
    )
    cola.cerrar(timeout=5.0)
    return {
        "ok": bool(res_push.get("ok")),
        "id_trabajo": id_trabajo,
        "rama_pruebas": rama_norm, "remoto_configurado": "url_externa_env",
        "archivos_escritos": [rel_snap, RUTA_LEDGER_REL],
        "hidratacion": hid,
        "push": res_push,
    }


def _emitir_anotacion_ci_segura(paso: str, out: dict[str, Any]) -> None:
    """Emite una anotación de GitHub Actions libre de secretos, tokens o URLs privadas."""
    r_url = os.getenv("PERSISTENCIA_REMOTO_URL", "").strip()
    r_tok = os.getenv("PERSISTENCIA_REMOTO_TOKEN", "").strip()
    limpiar = lambda x: _censurar_secreto(str(x or ""), r_tok, r_url).replace("\n", " ").replace("\r", " ")
    if not out.get("ok"):
        cod = out.get("codigo_error") or (out.get("hidratacion") or {}).get("codigo_error") or "fallo"
        errs = out.get("errores") or (out.get("push") or {}).get("errores") or []
        err = out.get("error") or ("; ".join(str(x) for x in errs) if errs else "error desconocido")
        print(
            f"::error::{paso} codigo_error={limpiar(cod)} has_url={int(bool(r_url))} "
            f"has_tok={int(bool(r_tok))} detalle={limpiar(err)[:220]}"
        )
        return
    if paso == "init_prod":
        print(
            f"::notice::{paso} rama={limpiar(out.get('rama'))} creada={out.get('creada')} "
            f"ya_existia={out.get('ya_existia')} n_entradas={out.get('n_entradas')} "
            f"archivos={out.get('archivos_escritos')} ultimo_sha256={limpiar(out.get('ultimo_sha256'))} "
            f"remoto_sha={limpiar(out.get('remoto_sha'))}"
        )
    else:
        push_led = (out.get("push") or {}).get("ledger") or {}
        print(
            f"::notice::{paso} rama={limpiar(out.get('rama_pruebas'))} id_trabajo={limpiar(out.get('id_trabajo'))} "
            f"archivos={out.get('archivos_escritos')} n_final={push_led.get('n_final')} "
            f"ultimo_sha256={limpiar(push_led.get('ultimo_sha256'))}"
        )


def _main_cli() -> int:
    parser = argparse.ArgumentParser(description="Utilidad CLI de persistencia Git")
    parser.add_argument("--smoke-prueba", action="store_true", help="Ejecuta prueba de escritura ficticia en rama de pruebas.")
    parser.add_argument("--inicializar-produccion-limpia", action="store_true", help="Crea produccion-datos en el remoto privado con el ledger limpio (sin registros de prueba).")
    parser.add_argument(
        "--rama-pruebas",
        default=os.getenv("PERSISTENCIA_RAMA_DATOS", "").strip() or "pruebas-persistencia-datos",
        help="Rama de pruebas destino.",
    )
    parser.add_argument("--rama-produccion", default="produccion-datos", help="Rama de producción destino.")
    parser.add_argument("--remote", default=os.getenv("PERSISTENCIA_REMOTE", "").strip() or "datos", help="Alias del remoto Git.")
    parser.add_argument("--remote-url", default=None, help="URL opcional del remoto de datos (preferir PERSISTENCIA_REMOTO_URL).")
    parser.add_argument("--solo-local", action="store_true", help="No hace push; solo genera registro local de prueba.")
    parser.add_argument("--ci-anotacion", action="store_true", help="Emite ::notice/::error censurado para GitHub Actions.")
    args = parser.parse_args()

    if args.inicializar_produccion_limpia:
        out = inicializar_produccion_limpia(
            remote=args.remote,
            remote_url=args.remote_url,
            rama_datos=args.rama_produccion,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        if args.ci_anotacion:
            _emitir_anotacion_ci_segura("init_prod", out)
        return 0 if out.get("ok") else 1
    if args.smoke_prueba:
        out = ejecutar_smoke_prueba(
            remote=args.remote,
            remote_url=args.remote_url,
            rama_pruebas=args.rama_pruebas,
            solo_local_sin_push=args.solo_local,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        if args.ci_anotacion:
            _emitir_anotacion_ci_segura("smoke_r15", out)
        return 0 if out.get("ok") else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main_cli())
