"""Tests de Ronda 13 (Puntos 1, 2 y 3):
Persistencia durable append-only en rama de datos simulada con remoto Git local:
  - Conflicto de push y reconstrucción del ledger siempre desde la copia remota
  - Cancelación a mitad con SIGTERM y drenaje de cola de push
  - Reinicio completo (destrucción de runner 1 -> hidratación runner 2) y /evaluar tras reinicio
  - Metadatos Telegram message_id / message.date en snapshots y sin coerción None -> 0
  - Workflow de prueba aparte sin Telegram y .github/workflows/bot_24_7.yml intacto
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

import bot_interactivo as bi
import evaluacion_pronosticos as ep
import motor_mercados as mm
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


def _init_remoto_local_y_runner(tmp_path: Path, nombre_runner: str = "runner_1") -> tuple[Path, Path]:
    remoto_codigo = tmp_path / "remoto_codigo_publico.git"
    remoto_datos = tmp_path / "remoto_datos_privado.git"
    if not remoto_codigo.exists():
        remoto_codigo.mkdir(parents=True, exist_ok=True)
        _git(remoto_codigo, "init", "--bare")

        seed = tmp_path / "_seed"
        seed.mkdir(parents=True, exist_ok=True)
        _git(seed, "init")
        _git(seed, "config", "user.name", "Seed Bot")
        _git(seed, "config", "user.email", "seed@local")
        (seed / "README.md").write_text("base\n", encoding="utf-8")
        _git(seed, "add", "README.md")
        _git(seed, "commit", "-m", "init main")
        _git(seed, "branch", "-M", "main")
        _git(seed, "remote", "add", "origin", str(remoto_codigo))
        _git(seed, "push", "-u", "origin", "main")

    if not remoto_datos.exists():
        remoto_datos.mkdir(parents=True, exist_ok=True)
        _git(remoto_datos, "init", "--bare")

    runner = tmp_path / nombre_runner
    _git(tmp_path, "clone", str(remoto_codigo), str(runner))
    _git(runner, "config", "user.name", "Test Bot")
    _git(runner, "config", "user.email", "bot@local")
    (runner / "informes" / "pronosticos").mkdir(parents=True, exist_ok=True)
    (runner / "informes" / "evaluacion").mkdir(parents=True, exist_ok=True)
    return remoto_datos, runner


def _entrada_pre_falsa(id_trabajo: str, id_partido: str, partido: str, fecha: str) -> dict:
    return {
        "estado_registro": "pre_partido",
        "corte_temporal": "pre_partido",
        "id_trabajo": id_trabajo,
        "id_partido": id_partido,
        "partido": partido,
        "fecha": fecha,
        "competicion": "Simulacro OOS",
        "generado_en": f"{fecha}T10:00:00Z",
        "modo_sombra": {"esquema": "sombra-v1", "lambdas_base": {"local_ft": 1.4, "visitante_ft": 1.1}},
    }


def _escribir_snapshot_falso(runner: Path, id_trabajo: str, id_partido: str, eq_l: str, eq_v: str, fecha: str, *, mid: int = 101, mdate: int = 1763300000) -> str:
    rel = f"informes/pronosticos/{id_trabajo}.json"
    doc = {
        "esquema": "pronostico-v1",
        "id_trabajo": id_trabajo,
        "id_partido": id_partido,
        "equipos": {"local": eq_l, "visitante": eq_v},
        "partido": {"fecha": fecha, "competicion": "Simulacro Liga", "condicion_local": "casa", "condicion_visitante": "fuera"},
        "generado_en": f"{fecha}T10:00:00-05:00",
        "publicado_en": f"{fecha}T10:00:05-05:00",
        "modelo": {"nombre": "Poisson", "version": "test", "umbral": 0.70},
        "datos_corte": {"fuente": "fixture"},
        "umbral_publicado": 0.70,
        "selecciones": [
            {"mercado": "Total de goles FT", "evento": "over_0.5", "periodo": "FT", "equipo": None, "linea": 0.5, "probabilidad": 0.90, "supera_umbral": True},
        ],
        "selecciones_publicadas": [
            {"mercado": "Total de goles FT", "evento": "over_0.5", "periodo": "FT", "equipo": None, "linea": 0.5, "probabilidad": 0.90, "supera_umbral": True},
        ],
        "referencia": "",
        "inmutable": True,
        "telegram_message_id": mid,
        "telegram_message_date": mdate,
    }
    pp.escribir_atomico_texto(runner / rel, json.dumps(doc, ensure_ascii=False, indent=1))
    return rel


def test_ronda13_conflicto_push_reconstruye_ledger_desde_remoto(tmp_path):
    remoto, runner_a = _init_remoto_local_y_runner(tmp_path, "runner_a")
    _, runner_b = _init_remoto_local_y_runner(tmp_path, "runner_b")

    cfg_a = pp.ConfigPersistencia(workspace_dir=runner_a, remote="datos", remote_url=str(remoto), rama_datos="produccion-datos", habilitada=True, backoff_base_s=0.02)
    cfg_b = pp.ConfigPersistencia(workspace_dir=runner_b, remote="datos", remote_url=str(remoto), rama_datos="produccion-datos", habilitada=True, backoff_base_s=0.02)

    # Runner A empuja partido 1
    rel_1 = _escribir_snapshot_falso(runner_a, "job_001", "P001", "Equipo A", "Equipo B", "2026-11-18")
    e_1 = _entrada_pre_falsa("job_001", "P001", "Equipo A vs Equipo B", "2026-11-18")
    r1 = pp.sincronizar_y_push_datos(cfg_a, archivos_rel=[rel_1], entradas_ledger=[e_1], mensaje_commit="add job_001")
    assert r1["ok"] is True and r1["pushed"] is True

    # Runner B hidrata desde remoto y prepara partido 2; antes de su push #1, Runner A empuja partido 3 creando conflicto non-fast-forward
    pp.hidratar_desde_remoto_datos(cfg_b)
    rel_2 = _escribir_snapshot_falso(runner_b, "job_002", "P002", "Equipo C", "Equipo D", "2026-11-18", mid=102)
    e_2 = _entrada_pre_falsa("job_002", "P002", "Equipo C vs Equipo D", "2026-11-18")

    carrera_disparada = {"hecho": False}

    def _provocar_conflicto(intento: int) -> None:
        if intento == 1 and not carrera_disparada["hecho"]:
            carrera_disparada["hecho"] = True
            rel_3 = _escribir_snapshot_falso(runner_a, "job_003", "P003", "Equipo E", "Equipo F", "2026-11-18", mid=103)
            e_3 = _entrada_pre_falsa("job_003", "P003", "Equipo E vs Equipo F", "2026-11-18")
            rc = pp.sincronizar_y_push_datos(cfg_a, archivos_rel=[rel_3], entradas_ledger=[e_3], mensaje_commit="race job_003")
            assert rc["ok"] is True and rc["pushed"] is True

    cola_b = pp.ColaPersistenciaGit(cfg_b)
    res_b = cola_b.encolar(
        tipo="mercados_pre",
        id_trabajo="job_002",
        archivos_rel=[rel_2],
        entradas_ledger=[e_2],
        mensaje_commit="add job_002 tras conflicto",
        simular_antes_de_push=_provocar_conflicto,
        esperar=True,
        timeout=20.0,
    )
    assert cola_b.cerrar(timeout=5.0) is True

    assert res_b["ok"] is True and res_b["pushed"] is True, res_b
    assert res_b["intentos"] >= 2
    assert res_b["ledger"]["n_remoto"] == 2
    assert res_b["ledger"]["n_final"] == 3

    # Clonar verificador limpio desde remoto: cadena SHA-256 intacta y reconstruida sobre copia remota [job_001, job_003, job_002]
    verif = tmp_path / "verificador"
    _git(tmp_path, "clone", "-b", "produccion-datos", str(remoto), str(verif))
    for jid in ("job_001", "job_002", "job_003"):
        assert (verif / "informes" / "pronosticos" / f"{jid}.json").exists()
    ver_cad = ep.verificar_cadena_ledger_oos(str(verif / "informes" / "evaluacion" / "ledger_oos.jsonl"))
    assert ver_cad["ok"] is True
    assert ver_cad["entradas"] == 3
    regs = ep.cargar_ledger_oos(str(verif / "informes" / "evaluacion" / "ledger_oos.jsonl"))
    assert [r["id_trabajo"] for r in regs] == ["job_001", "job_003", "job_002"]
    assert regs[2]["prev_sha256"] == regs[1]["sha256"]


def test_ronda13_cancelacion_a_mitad_sigterm_drena_y_persiste(tmp_path, monkeypatch):
    remoto, runner = _init_remoto_local_y_runner(tmp_path, "runner_sig")
    cfg = pp.ConfigPersistencia(workspace_dir=runner, remote="datos", remote_url=str(remoto), rama_datos="produccion-datos", habilitada=True, backoff_base_s=0.02)
    cola = pp.ColaPersistenciaGit(cfg)

    monkeypatch.setattr(bi, "BASE_DIR", runner)
    monkeypatch.setattr(bi, "_cola_persistencia", cola)
    monkeypatch.setattr(bi, "_shutdown_event", threading.Event())
    monkeypatch.setattr(bi, "_hilos_activos", set())
    monkeypatch.setattr(bi, "enviar_seguro", lambda *a, **k: {"ok": True, "message_id": 50})
    monkeypatch.setattr(bi.bot, "stop_polling", lambda: None)

    paso_inicio = threading.Event()
    permitir_fin = threading.Event()

    def _worker_a_mitad():
        bi._registrar_hilo_actual()
        try:
            paso_inicio.set()
            assert permitir_fin.wait(timeout=5.0)
            rel = _escribir_snapshot_falso(runner, "job_sigterm", "PSIG", "Local Sig", "Visita Sig", "2026-11-19", mid=777, mdate=1763500000)
            ent = _entrada_pre_falsa("job_sigterm", "PSIG", "Local Sig vs Visita Sig", "2026-11-19")
            bi._encolar_persistencia_segura(tipo="mercados_pre", id_trabajo="job_sigterm", archivos_rel=[rel], entradas_ledger=[ent])
        finally:
            bi._desregistrar_hilo_actual()

    h = threading.Thread(target=_worker_a_mitad, daemon=True)
    h.start()
    assert paso_inicio.wait(timeout=5.0)

    # Llega SIGTERM mientras el hilo está a mitad; nuevo /mercados debe rechazarse limpiamente
    bi._al_recibir_senal_salida(getattr(signal, "SIGTERM", 15), None)
    assert bi._shutdown_event.is_set()
    mensajes_rechazo = []
    monkeypatch.setattr(bi, "enviar_seguro", lambda _b, _c, txt, **k: mensajes_rechazo.append(txt))
    bi._lanzar_mercados(12345, "Equipo X", "Equipo Y", 0.70, 15)
    assert any("reinicio" in m.lower() for m in mensajes_rechazo)

    # El worker concluye su guardado y _cerrar_ordenadamente_bot drena la cola Git antes de salir
    permitir_fin.set()
    res_cierre = bi._cerrar_ordenadamente_bot(timeout_hilos=10.0, timeout_cola=15.0)
    assert res_cierre["cola_cerrada"] is True
    assert res_cierre["hilos_aun_vivos"] == []

    verif = tmp_path / "verif_sig"
    _git(tmp_path, "clone", "-b", "produccion-datos", str(remoto), str(verif))
    snap = json.loads((verif / "informes" / "pronosticos" / "job_sigterm.json").read_text(encoding="utf-8"))
    assert snap["telegram_message_id"] == 777
    assert snap["telegram_message_date"] == 1763500000
    regs = ep.cargar_ledger_oos(str(verif / "informes" / "evaluacion" / "ledger_oos.jsonl"))
    assert len(regs) == 1 and regs[0]["id_trabajo"] == "job_sigterm"
    assert ep.verificar_cadena_ledger_oos(str(verif / "informes" / "evaluacion" / "ledger_oos.jsonl"))["ok"] is True


def test_ronda13_reinicio_completo_y_evaluar_tras_reinicio(tmp_path, monkeypatch):
    remoto, runner_1 = _init_remoto_local_y_runner(tmp_path, "runner_1")
    cfg_1 = pp.ConfigPersistencia(workspace_dir=runner_1, remote="datos", remote_url=str(remoto), rama_datos="produccion-datos", habilitada=True, backoff_base_s=0.02)

    # Datos reales de tests/fixtures/ronda5_real_fixtures.json recortados para un partido pre-partido determinista
    fix_path = Path(ROOT) / "tests" / "fixtures" / "ronda5_real_fixtures.json"
    docs = json.loads(fix_path.read_text(encoding="utf-8"))["docs"]
    analisis = mm.analizar(
        docs["italy"], docs["france"], "Italy", "France",
        fecha_partido="2026-11-20", competicion="UEFA Nations League",
    )
    preparado = {
        "id_trabajo": "r13_restart_it_fr",
        "id_partido": "99001122",
        "analisis": analisis,
        "rutas": {"informe": "", "imagen": ""},
        "contexto": {
            "doc_local": docs["italy"],
            "doc_visitante": docs["france"],
            "equipo_local": "Italy",
            "equipo_visitante": "France",
            "fecha_partido": "2026-11-20",
            "competicion": "UEFA Nations League",
        },
        "telegram_meta": {
            "chat_id": "999",
            "message_id": 8801,
            "message_date": 1763560000,
            "message_date_utc": "2025-11-19T13:46:40Z",
        },
    }
    analisis.metadatos["generado_en"] = "2026-11-19 10:00:00"
    ep.adjuntar_modo_sombra_preparado(preparado)
    snap = ep.snapshot_desde_analisis(preparado, telegram_meta=preparado["telegram_meta"])
    ruta_snap = ep.guardar_snapshot(snap, directorio=str(runner_1 / "informes" / "pronosticos"))
    ep.adjuntar_acuse_telegram_snapshot(
        ruta_snap,
        telegram_envio={
            "message_id": 8802,
            "message_date": 1763560012,
            "message_date_utc": "2025-11-19T13:46:52Z",
            "ids_telegram": [8802],
        },
    )
    ruta_led_1 = str(runner_1 / "informes" / "evaluacion" / "ledger_oos.jsonl")
    res_pre = ep.registrar_sombra_prepartido_en_ledger_oos(preparado, ruta_snapshot=ruta_snap, ruta_ledger=ruta_led_1)
    assert res_pre["registrado"] is True
    sha_pre = res_pre["sha256"]

    cola_1 = pp.ColaPersistenciaGit(cfg_1)
    push_1 = cola_1.encolar(
        tipo="mercados_pre",
        id_trabajo="r13_restart_it_fr",
        archivos_rel=["informes/pronosticos/r13_restart_it_fr.json"],
        mensaje_commit="pre_partido tras /mercados",
        esperar=True,
        timeout=20.0,
    )
    cola_1.cerrar(timeout=5.0)
    assert push_1["ok"] is True and push_1["pushed"] is True

    # Destruimos completamente runner_1 para simular pérdida total del disco del runner
    def _onerr(fn, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    shutil.rmtree(runner_1, onerror=_onerr)
    assert not runner_1.exists()

    # Reinicio: runner_2 arranca limpio solo con checkout de main e hidrata desde remoto
    _, runner_2 = _init_remoto_local_y_runner(tmp_path, "runner_2")
    assert not (runner_2 / "informes" / "pronosticos" / "r13_restart_it_fr.json").exists()
    cfg_2 = pp.ConfigPersistencia(workspace_dir=runner_2, remote="datos", remote_url=str(remoto), rama_datos="produccion-datos", habilitada=True, backoff_base_s=0.02)
    hid = pp.hidratar_desde_remoto_datos(cfg_2)
    assert hid["rama_existe"] is True
    assert hid["n_pronosticos_hidratados"] == 1
    assert (runner_2 / "informes" / "pronosticos" / "r13_restart_it_fr.json").exists()

    snap_hid = json.loads((runner_2 / "informes" / "pronosticos" / "r13_restart_it_fr.json").read_text(encoding="utf-8"))
    assert snap_hid["telegram_origen"]["message_id"] == 8801
    assert snap_hid["telegram_origen"]["message_date"] == 1763560000
    assert snap_hid["telegram_envio"]["message_id"] == 8802
    assert snap_hid["telegram_envio"]["message_date"] == 1763560012
    assert snap_hid["telegram_message_id"] == 8802
    assert snap_hid["telegram_message_date"] == 1763560012

    # /evaluar tras reinicio apuntando módulos de evaluación al directorio hidratado de runner_2
    monkeypatch.setattr(ep, "PRONOSTICOS_DIR", str(runner_2 / "informes" / "pronosticos"))
    monkeypatch.setattr(ep, "EVALUACION_DIR", str(runner_2 / "informes" / "evaluacion"))
    monkeypatch.setattr(ep, "MERCADOS_DIR", str(runner_2 / "informes" / "mercados"))
    monkeypatch.setattr(ep, "LEDGER_OOS_PATH", str(runner_2 / "informes" / "evaluacion" / "ledger_oos.jsonl"))
    monkeypatch.setattr(ep, "REGISTRO_EVALUACIONES", str(runner_2 / "informes" / "evaluacion" / "registro_evaluaciones.json"))

    class _FuenteMockEval:
        def resolver_equipo(self, nombre):
            n = str(nombre or "").lower()
            if "ital" in n:
                return {"id": "8204", "nombre": "Italy"}
            if "fran" in n:
                return {"id": "6723", "nombre": "France"}
            return None

        def _api(self, endpoint: str):
            if str(endpoint).startswith("/teams"):
                return {
                    "fixtures": {
                        "allFixtures": {
                            "fixtures": [
                                {
                                    "id": "99001122",
                                    "home": {"id": "8204", "name": "Italy"},
                                    "away": {"id": "6723", "name": "France"},
                                    "status": {
                                        "utcTime": "2026-11-20T19:45:00Z",
                                        "started": True,
                                        "finished": True,
                                    },
                                }
                            ]
                        }
                    }
                }
            if "matchDetails" in str(endpoint):
                return {
                    "general": {
                        "matchId": "99001122",
                        "matchName": "Italy vs France 2026-11-20, 19:45 UTC",
                        "leagueName": "UEFA Nations League",
                    },
                    "header": {
                        "teams": [
                            {"name": "Italy", "score": 2},
                            {"name": "France", "score": 1},
                        ],
                        "status": {
                            "utcTime": "2026-11-20T19:45:00Z",
                            "started": True,
                            "finished": True,
                        },
                        "events": {
                            "homeTeamGoals": {
                                "Chiesa": [{"time": 20, "shotmapEvent": {"min": 20, "period": "FirstHalf"}}],
                                "Barella": [{"time": 65, "shotmapEvent": {"min": 65, "period": "SecondHalf"}}],
                            },
                            "awayTeamGoals": {
                                "Mbappe": [{"time": 75, "shotmapEvent": {"min": 75, "period": "SecondHalf"}}],
                            },
                        },
                    },
                }
            return {}

    prep_ev = ep.preparar_evaluacion(
        fuente=_FuenteMockEval(),
        registrar_ledger_oos_sombra=True,
        ruta_ledger_oos=str(runner_2 / "informes" / "evaluacion" / "ledger_oos.jsonl"),
    )
    assert not prep_ev.get("sin_datos_filtro")
    res_env = ep.enviar_resumen(
        prep_ev,
        enviar_fn=lambda txt, cid: {"ok": True, "message_id": 9901, "date": 1763600000},
        chat_id=999,
        ruta_registro=str(runner_2 / "informes" / "evaluacion" / "registro_evaluaciones.json"),
    )
    assert res_env["enviado"] is True

    cola_2 = pp.ColaPersistenciaGit(cfg_2)
    push_2 = cola_2.encolar(
        tipo="evaluar_liquidado",
        id_trabajo=prep_ev["id_ejecucion"],
        mensaje_commit="liquidado tras reinicio",
        esperar=True,
        timeout=20.0,
    )
    cola_2.cerrar(timeout=5.0)
    assert push_2["ok"] is True and push_2["pushed"] is True

    # Tercer clon limpio confirma persistencia durable de pre_partido + liquidado enlazados + informe + registro
    verif = tmp_path / "runner_3_verif"
    _git(tmp_path, "clone", "-b", "produccion-datos", str(remoto), str(verif))
    ruta_led_verif = str(verif / "informes" / "evaluacion" / "ledger_oos.jsonl")
    ver_cad = ep.verificar_cadena_ledger_oos(ruta_led_verif)
    assert ver_cad["ok"] is True and ver_cad["entradas"] == 2
    regs = ep.cargar_ledger_oos(ruta_led_verif)
    assert regs[0]["estado_registro"] == "pre_partido"
    assert regs[1]["estado_registro"] == "liquidado"
    assert regs[1]["pre_partido_sha256"] == sha_pre == regs[0]["sha256"]
    assert (verif / "informes" / "evaluacion" / "registro_evaluaciones.json").exists()
    assert list((verif / "informes" / "evaluacion").glob("evaluacion_*.json"))


def test_ronda13_workflow_prueba_sin_telegram_y_bot_24_7_intacto(tmp_path):
    remoto, runner = _init_remoto_local_y_runner(tmp_path, "runner_smoke")
    res_smoke = pp.ejecutar_smoke_prueba(runner, remote="datos", remote_url=str(remoto), rama_pruebas="pruebas-persistencia-datos")
    assert res_smoke["ok"] is True
    assert res_smoke["rama_pruebas"] == "pruebas-persistencia-datos"

    verif = tmp_path / "verif_smoke"
    _git(tmp_path, "clone", "-b", "pruebas-persistencia-datos", str(remoto), str(verif))
    regs = ep.cargar_ledger_oos(str(verif / "informes" / "evaluacion" / "ledger_oos.jsonl"))
    assert len(regs) == 1
    assert regs[0]["es_registro_prueba_mentira"] is True
    assert regs[0]["tipo_partido"] == "simulado_prueba"
    assert regs[0]["entra_promedios"] is False
    assert regs[0]["entra_calibracion"] is False

    # Bloqueo de ramas protegidas en smoke
    with pytest.raises(ValueError):
        pp.ejecutar_smoke_prueba(runner, rama_pruebas="main")
    with pytest.raises(ValueError):
        pp.ejecutar_smoke_prueba(runner, rama_pruebas="produccion-datos")

    # bot_24_7.yml con persistencia privada activa y test_persistencia_datos.yml sin Telegram ni colisión de concurrencia
    bot_disk = (Path(ROOT) / ".github" / "workflows" / "bot_24_7.yml").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "cancel-in-progress: false" in bot_disk
    assert "contents: read" in bot_disk
    assert "contents: write" not in bot_disk
    assert "timeout -k 60 285m" in bot_disk
    assert "--hidratar --ci-anotacion" in bot_disk

    wf_test = (Path(ROOT) / ".github" / "workflows" / "test_persistencia_datos.yml").read_text(encoding="utf-8")
    assert "test-persistencia-datos-ronda13" in wf_test
    assert "bot-telegram-24-7" not in wf_test
    assert "TELEGRAM" not in wf_test
    assert "timeout -k 60 285m" in wf_test
    assert "contents: read" in wf_test
    assert "contents: write" not in wf_test


def test_ronda13_none_no_se_vuelve_cero_y_token_censurado(tmp_path):
    fix_path = Path(ROOT) / "tests" / "fixtures" / "ronda5_real_fixtures.json"
    docs = json.loads(fix_path.read_text(encoding="utf-8"))["docs"]
    analisis = mm.analizar(docs["italy"], docs["france"], "Italy", "France", competicion="UEFA Nations League")
    prep = {"id_trabajo": "r13_none_tg", "analisis": analisis, "rutas": {}}
    snap = ep.snapshot_desde_analisis(prep, telegram_meta={"chat_id": "1", "message_id": None, "message_date": None})
    d = snap.a_dict()
    assert d.get("telegram_message_id") is None
    assert d.get("telegram_message_date") is None
    assert d["telegram_origen"]["message_id"] is None
    assert d["telegram_origen"]["message_date"] is None

    # Censura del token si aparece en una excepción y ausencia de slice TOKEN[:5]
    bot_src = (Path(ROOT) / "bot_interactivo.py").read_text(encoding="utf-8")
    assert "TOKEN[:5]" not in bot_src
    assert bi._censurar_token("sin secreto") == "sin secreto"
