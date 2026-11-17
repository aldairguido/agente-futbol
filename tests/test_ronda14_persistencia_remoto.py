"""Tests de Ronda 14 Punto 2:
- Workflow test_persistencia_datos.yml con disparadores `push` (pruebas-persistencia-datos)
  y `workflow_dispatch`, guarda `[skip ci]`, y remoto configurable por variable o secreto
  (misma repo / otra repo) sin URLs ni tokens hardcodeados.
- `ConfigPersistencia`, `hidratar_desde_remoto_datos` y `ejecutar_smoke_prueba` con
  remoto externo simulado (bare repo separado de `origin`) y censura de secreto.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

import evaluacion_pronosticos as ep
import persistencia_produccion as pp


def _git(cwd: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )
    return cp.stdout.strip()


def test_ronda14_config_desde_entorno_y_censura_token(monkeypatch):
    monkeypatch.setenv("PERSISTENCIA_PRODUCCION_ACTIVA", "1")
    monkeypatch.setenv("PERSISTENCIA_REMOTO_URL", "https://example.invalid/org/datos.git")
    monkeypatch.setenv("PERSISTENCIA_REMOTO_TOKEN", "tok_super_secreto_r14")
    monkeypatch.setenv("PERSISTENCIA_RAMA_DATOS", "pruebas-persistencia-datos")

    cfg = pp.ConfigPersistencia.desde_entorno(ROOT)
    assert cfg.habilitada is True
    assert cfg.remote_url == "https://example.invalid/org/datos.git"
    assert cfg.remote_token == "tok_super_secreto_r14"
    assert cfg.rama_datos == "pruebas-persistencia-datos"
    # repr() nunca debe exponer el secreto
    assert "tok_super_secreto_r14" not in repr(cfg)
    assert pp._censurar_secreto("error con tok_super_secreto_r14 aqui", cfg.remote_token) == "error con *** aqui"

    # Script askpass se genera y se limpia correctamente tras usar _run_git_con_auth
    import tempfile
    tmp_ask = Path(tempfile.mkdtemp())
    try:
        sc = pp._crear_script_askpass(tmp_ask)
        assert sc.exists()
        cp_u = subprocess.run([str(sc), "Username for 'https://example.invalid': "], env={**os.environ, "_PERSISTENCIA_GIT_TOKEN": "tok_r14"}, text=True, capture_output=True, check=True)
        cp_p = subprocess.run([str(sc), "Password for 'https://example.invalid': "], env={**os.environ, "_PERSISTENCIA_GIT_TOKEN": "tok_r14"}, text=True, capture_output=True, check=True)
        assert cp_u.stdout.strip() == "x-access-token"
        assert cp_p.stdout.strip() == "tok_r14"
    finally:
        pp._rmtree_seguro(tmp_ask)


def test_ronda14_remoto_externo_configurable_via_env_sin_tocar_origin(tmp_path, monkeypatch):
    # Remoto A = origin (repo de código); Remoto B = repo externo exclusivo para datos
    rem_origin = tmp_path / "origin_codigo.git"
    rem_externo = tmp_path / "externo_datos.git"
    rem_origin.mkdir()
    rem_externo.mkdir()
    _git(rem_origin, "init", "--bare")
    _git(rem_externo, "init", "--bare")

    seed = tmp_path / "_seed"
    seed.mkdir()
    _git(seed, "init")
    _git(seed, "config", "user.name", "Seed")
    _git(seed, "config", "user.email", "seed@local")
    (seed / "README.md").write_text("codigo\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "init")
    _git(seed, "branch", "-M", "main")
    _git(seed, "remote", "add", "origin", str(rem_origin))
    _git(seed, "push", "-u", "origin", "main")

    runner_1 = tmp_path / "runner_1"
    _git(tmp_path, "clone", str(rem_origin), str(runner_1))
    _git(runner_1, "config", "user.name", "Bot R14")
    _git(runner_1, "config", "user.email", "bot@local")

    monkeypatch.setenv("PERSISTENCIA_REMOTO_URL", str(rem_externo))
    monkeypatch.setenv("PERSISTENCIA_RAMA_DATOS", "datos-externos-r14")

    res = pp.ejecutar_smoke_prueba(runner_1, rama_pruebas="datos-externos-r14")
    assert res["ok"] is True
    assert res["remoto_configurado"] == "url_externa_env"

    # origin NO debe haber recibido la rama de datos externa
    heads_origin = _git(rem_origin, "for-each-ref", "--format=%(refname)", "refs/heads")
    assert "datos-externos-r14" not in heads_origin
    heads_ext = _git(rem_externo, "for-each-ref", "--format=%(refname)", "refs/heads")
    assert "refs/heads/datos-externos-r14" in heads_ext

    # Segundo runner limpio hidratado únicamente desde PERSISTENCIA_REMOTO_URL externo
    runner_2 = tmp_path / "runner_2"
    _git(tmp_path, "clone", str(rem_origin), str(runner_2))
    cfg_2 = pp.ConfigPersistencia.desde_entorno(runner_2)
    hid = pp.hidratar_desde_remoto_datos(cfg_2)
    assert hid["ok"] is True
    assert hid["rama_existe"] is True
    assert hid["n_eventos_hidratados"] == 1
    assert (runner_2 / "informes" / "evaluacion" / "ledger_oos.jsonl").exists()
    ver = ep.verificar_cadena_ledger_oos(str(runner_2 / "informes" / "evaluacion" / "ledger_oos.jsonl"))
    assert ver["ok"] is True and ver["entradas"] == 1
    regs = ep.cargar_ledger_oos(str(runner_2 / "informes" / "evaluacion" / "ledger_oos.jsonl"))
    assert regs[0]["es_registro_prueba_mentira"] is True
    assert regs[0]["entra_promedios"] is False
    assert regs[0]["entra_calibracion"] is False


def test_ronda14_workflow_prueba_disparadores_remoto_configurable_y_sin_hardcode():
    wf = (Path(ROOT) / ".github" / "workflows" / "test_persistencia_datos.yml").read_text(encoding="utf-8")

    # Disparadores requeridos: push a pruebas-persistencia-datos + workflow_dispatch
    assert re.search(r"^\s*push:\s*$", wf, re.MULTILINE)
    assert "pruebas-persistencia-datos" in wf
    assert "workflow_dispatch:" in wf
    assert "!contains(github.event.head_commit.message, '[skip ci]')" in wf

    # Remoto privado configurado únicamente vía secretos (nunca github.token, nunca vars, nunca repo hardcodeado)
    assert "secrets.PERSISTENCIA_REMOTO_URL" in wf
    assert "secrets.PERSISTENCIA_REMOTO_TOKEN" in wf
    assert "vars.PERSISTENCIA_REMOTO_URL" not in wf
    assert "github.token" not in wf
    assert "GITHUB_TOKEN" not in wf
    assert "contents: read" in wf
    assert "contents: write" not in wf
    assert "upload-artifact" not in wf
    assert "--inicializar-produccion-limpia" in wf
    assert "github.com/" not in wf
    assert "aldairguido" not in wf
    assert "ghp_" not in wf
    assert "github_pat_" not in wf

    # Sin Telegram ni modificación de bot_24_7.yml respecto a main
    assert "TELEGRAM" not in wf
    assert "bot-telegram-24-7" not in wf
    bot_main = subprocess.check_output(["git", "show", "main:.github/workflows/bot_24_7.yml"], cwd=ROOT, encoding="utf-8").replace("\r\n", "\n")
    bot_disk = (Path(ROOT) / ".github" / "workflows" / "bot_24_7.yml").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert bot_main == bot_disk
