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
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import random
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


def persistencia_activa() -> bool:
    """Devuelve True solo si la persistencia Git en producción está habilitada por entorno."""
    val = os.getenv("PERSISTENCIA_PRODUCCION_ACTIVA", "").strip().lower()
    return val in ("1", "true", "si", "sí", "yes", "on")


@dataclass
class ConfigPersistencia:
    workspace_dir: Path
    remote: str = "origin"
    rama_datos: str = "produccion-datos"
    remote_url: str = ""
    remote_token: str = field(default="", repr=False)
    worktree_dir: Path | None = None
    habilitada: bool = False
    max_reintentos_push: int = 4
    backoff_base_s: float = 0.35
    author_name: str = "Agente Futbol Bot"
    author_email: str = "bot@agente-futbol.local"
    archivos_extra_rel: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def desde_entorno(cls, workspace_dir: Path | str | None = None) -> "ConfigPersistencia":
        raiz = Path(workspace_dir) if workspace_dir else Path(__file__).resolve().parent
        return cls(
            workspace_dir=raiz,
            remote=os.getenv("PERSISTENCIA_REMOTE", "origin").strip() or "origin",
            rama_datos=os.getenv("PERSISTENCIA_RAMA_DATOS", "produccion-datos").strip() or "produccion-datos",
            remote_url=os.getenv("PERSISTENCIA_REMOTO_URL", "").strip(),
            remote_token=os.getenv("PERSISTENCIA_REMOTO_TOKEN", "").strip(),
            worktree_dir=(
                Path(os.getenv("PERSISTENCIA_WORKTREE_DIR", "").strip())
                if os.getenv("PERSISTENCIA_WORKTREE_DIR", "").strip()
                else None
            ),
            habilitada=persistencia_activa(),
            max_reintentos_push=max(1, int(os.getenv("PERSISTENCIA_MAX_REINTENTOS", "4") or "4")),
            backoff_base_s=max(0.02, float(os.getenv("PERSISTENCIA_BACKOFF_S", "0.35") or "0.35")),
        )


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


def _censurar_secreto(texto: str, token: str | None) -> str:
    s = str(texto or "")
    tok = (token or "").strip()
    if tok and tok in s:
        s = s.replace(tok, "***")
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
    Resuelve `(alias_remoto, url_efectiva)` sin hardcodear repositorios:
    prioriza `cfg.remote_url` (`PERSISTENCIA_REMOTO_URL`) sobre `git remote get-url <remote>`.
    """
    url_cfg = (cfg.remote_url or "").strip()
    if url_cfg:
        return (cfg.remote or "origin"), url_cfg
    cp_url = _run_git(ws, "remote", "get-url", cfg.remote, check=False)
    url_loc = cp_url.stdout.strip() if (cp_url.returncode == 0 and cp_url.stdout.strip()) else str(ws)
    return (cfg.remote or "origin"), url_loc


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
    """Ejecuta Git con `GIT_ASKPASS` efímero cuando `cfg.remote_token` está presente, sin imprimir credenciales."""
    tok = (cfg.remote_token or "").strip()
    es_http = target_url.lower().startswith(("http://", "https://"))
    if not tok or not es_http:
        return _run_git(cwd, *args, check=check)

    extra_args: list[str] = []
    partes = urlsplit(target_url)
    if partes.scheme and partes.netloc:
        base_host = f"{partes.scheme}://{partes.netloc}/"
        extra_args.extend(["-c", f"http.{base_host}.extraheader="])

    ask_dir = Path(tempfile.mkdtemp(prefix=".git_askpass_"))
    try:
        sc_path = _crear_script_askpass(ask_dir)
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = str(sc_path)
        env["_PERSISTENCIA_GIT_TOKEN"] = tok
        cp = subprocess.run(
            ["git", *extra_args, *args],
            cwd=str(cwd),
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
            stdout=_censurar_secreto(cp.stdout, tok),
            stderr=_censurar_secreto(cp.stderr, tok),
        )
        if check and cp.returncode != 0:
            raise subprocess.CalledProcessError(cp.returncode, ["git", *args], output=cp.stdout, stderr=cp.stderr)
        return cp
    finally:
        _rmtree_seguro(ask_dir)


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
    Al iniciar un runner limpio, trae de `<remoto>/<rama_datos>` todos los snapshots,
    evaluaciones, eventos y reconstruye `ledger_oos.jsonl` desde la copia remota.
    """
    ws = cfg.workspace_dir
    _, target_url = resolver_objetivo_remoto(ws, cfg)
    if (cfg.remote_url or "").strip():
        ref_full = f"refs/persistencia_remoto/{cfg.rama_datos}"
        objetivo = target_url
    else:
        ref_full = f"refs/remotes/{cfg.remote}/{cfg.rama_datos}"
        objetivo = cfg.remote
    cp_fetch = _run_git_con_auth(
        ws,
        cfg,
        "fetch",
        objetivo,
        f"refs/heads/{cfg.rama_datos}:{ref_full}",
        target_url=target_url,
        check=False,
    )
    cp_rev = _run_git(ws, "rev-parse", "--verify", ref_full, check=False)
    if cp_fetch.returncode != 0 or cp_rev.returncode != 0:
        return {
            "ok": True,
            "rama_existe": False,
            "n_pronosticos_hidratados": 0,
            "n_evaluaciones_hidratadas": 0,
            "n_eventos_hidratados": 0,
            "ledger": {"ok": True, "n_remoto": 0, "n_final": 0, "n_reencadenados": 0},
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
) -> dict[str, Any]:
    """
    Ejecuta en un repositorio staging aislado:
      fetch remoto -> reconstruir ledger desde copia remota -> copiar archivos ->
      commit -> push con reintentos ante conflictos.
    """
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

            err_txt = _censurar_secreto((cp_push.stderr or cp_push.stdout or "push fallido").strip(), cfg.remote_token)
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

    def __init__(self, cfg: ConfigPersistencia) -> None:
        self.cfg = cfg
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
                )
            except Exception as exc:
                res = {"ok": False, "pushed": False, "errores": [f"{type(exc).__name__}: {exc}"]}
            finally:
                with self._lock:
                    item["resultado"] = res
                    self.historial_resultados.append({
                        "tipo": item.get("tipo"),
                        "id_trabajo": item.get("id_trabajo"),
                        **res,
                    })
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


def ejecutar_smoke_prueba(
    workspace_dir: Path | str | None = None,
    *,
    remote: str = "origin",
    remote_url: str | None = None,
    remote_token: str | None = None,
    rama_pruebas: str = "pruebas-persistencia-datos",
    solo_local_sin_push: bool = False,
) -> dict[str, Any]:
    """
    Escribe un registro sintético claramente etiquetado (`es_registro_prueba_mentira=True`,
    `tipo_partido='simulado_prueba'`, `entra_promedios=False`, `entra_calibracion=False`)
    en una ruta de pruebas y, si `solo_local_sin_push=False`, lo sincroniza en `rama_pruebas`.
    """
    rama_norm = (rama_pruebas or "").strip()
    if not rama_norm or rama_norm in ("main", "master", "produccion-datos"):
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
    hid = hidratar_desde_remoto_datos(cfg)
    cola = ColaPersistenciaGit(cfg)
    res_push = cola.encolar(
        tipo="smoke_prueba",
        id_trabajo=id_trabajo,
        archivos_rel=[rel_snap],
        entradas_ledger=[entrada_fake],
        mensaje_commit=f"test(r14): registro smoke ficticio {id_trabajo} [skip ci]",
        esperar=True,
        timeout=60.0,
    )
    cola.cerrar(timeout=5.0)
    return {
        "ok": bool(res_push.get("ok")),
        "id_trabajo": id_trabajo,
        "rama_pruebas": rama_norm,
        "remoto_configurado": "url_externa_env" if r_url else remote,
        "hidratacion": hid,
        "push": res_push,
    }


def _main_cli() -> int:
    parser = argparse.ArgumentParser(description="Utilidad CLI de persistencia Git")
    parser.add_argument("--smoke-prueba", action="store_true", help="Ejecuta prueba de escritura ficticia en rama de pruebas.")
    parser.add_argument(
        "--rama-pruebas",
        default=os.getenv("PERSISTENCIA_RAMA_DATOS", "").strip() or "pruebas-persistencia-datos",
        help="Rama de pruebas destino.",
    )
    parser.add_argument("--remote", default=os.getenv("PERSISTENCIA_REMOTE", "").strip() or "origin", help="Nombre del remoto Git.")
    parser.add_argument("--remote-url", default=None, help="URL opcional del remoto de datos.")
    parser.add_argument("--solo-local", action="store_true", help="No hace push; solo genera registro local de prueba.")
    args = parser.parse_args()

    if args.smoke_prueba:
        out = ejecutar_smoke_prueba(
            remote=args.remote,
            remote_url=args.remote_url,
            rama_pruebas=args.rama_pruebas,
            solo_local_sin_push=args.solo_local,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main_cli())
