"""Tests para ORDEN RONDA 12 — Punto 2:
Tercer brazo 'plano' (λ plano Etapa 1 + Poisson/Dixon-Coles + NB de córneres)
en modo_sombra tras bandera apagada por defecto, registrado pre-partido junto
con soloA y A+B, y benchmark OOS sobre 24 partidos."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import decision_cobertura as dc
import evaluacion_pronosticos as ep
import motor_mercados as mm


def _cargar_gal_bar():
    aud = Path(ROOT) / "informes" / "auditorias" / "2026-10-13"
    dl = json.loads((aud / "auditoria_galatasaray_2026-10-13_102501.json").read_text(encoding="utf-8"))
    dv = json.loads((aud / "auditoria_barcelona_2026-10-13_102522.json").read_text(encoding="utf-8"))
    dl_c = {"equipo": dl.get("equipo"), "partidos": [x for x in dl.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"]}
    dv_c = {"equipo": dv.get("equipo"), "partidos": [x for x in dv.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"]}
    return dl_c, dv_c


def test_ronda12_punto2_bandera_defecto_false_y_no_cambia_modo_dual(tmp_path):
    assert dc.bandera_activa("usar_tercer_brazo_plano_sombra") is False
    assert all(not v for v in dc.obtener_banderas_comportamiento().values())
    assert "usar_tercer_brazo_plano_sombra" in dc._BANDERAS_AUXILIARES_DEFECTO
    assert "usar_tercer_brazo_plano_sombra" not in dc.BANDERAS_COMPORTAMIENTO_DEFECTO
    with dc.banderas_comportamiento(activar_todas=True):
        assert dc.bandera_activa("usar_tercer_brazo_plano_sombra") is False

    dl_c, dv_c = _cargar_gal_bar()
    with dc.banderas_comportamiento(activar_todas=False):
        an_of = mm.analizar(dl_c, dv_c, "Galatasaray", "Barcelona", fecha_partido="2026-10-13")
    an_of.metadatos["fecha_partido"] = "2026-10-13"
    an_of.metadatos["generado_en"] = "2026-10-13T10:00:00"
    an_of.metadatos["competicion"] = "UEFA Champions League"

    prep_off = {
        "id_trabajo": "TEST_R12_P2_OFF",
        "analisis": an_of,
        "contexto": {
            "doc_local": dl_c, "doc_visitante": dv_c,
            "equipo_local": "Galatasaray", "equipo_visitante": "Barcelona",
            "fecha_partido": "2026-10-13", "competicion": "UEFA Champions League",
            "condicion_local": "casa", "condicion_visitante": "fuera",
        },
    }
    somb_off = ep.adjuntar_modo_sombra_preparado(prep_off)
    assert somb_off is not None
    assert somb_off["esquema"] == "sombra-v1"
    assert "lambdas_plano" not in somb_off
    assert all("prob_plano" not in c for c in somb_off["comparaciones"])

    # Activar bandera: agrega tercer brazo plano (Etapa 1 + Poisson/DC + NB corners) pre-partido y post-partido
    prep_on = {
        "id_trabajo": "TEST_R12_P2_ON",
        "analisis": an_of,
        "contexto": dict(prep_off["contexto"]),
    }
    with dc.banderas_comportamiento(activar_todas=False, usar_tercer_brazo_plano_sombra=True):
        somb_on = ep.adjuntar_modo_sombra_preparado(prep_on)
    assert somb_on is not None
    assert somb_on["esquema"] == "sombra-v2-tribrazo"
    assert somb_on["version_base"] == "soloA"
    assert somb_on["version_sombra"] == "A+B"
    assert somb_on["version_plano"] == "plano"
    assert somb_on["reglas_brazo_plano"]["etapa_lambda"] == "1_lambda_plano"
    assert somb_on["reglas_brazo_plano"]["corners"] == "binomial_negativa_sobredispersa"

    et_esp = ep.extraer_lambdas_etapas_cadena(an_of.motor.traza_lambda, (an_of.motor.tasas.lambda_local_ft, an_of.motor.tasas.lambda_visitante_ft))["1_lambda_plano"]
    assert abs(somb_on["lambdas_plano"]["local_ft"] - et_esp[0]) < 1e-3
    assert abs(somb_on["lambdas_plano"]["visitante_ft"] - et_esp[1]) < 1e-3

    # Verificar uso real de NB de córneres en análisis plano
    an_plano = prep_on["analisis_plano"]
    crn_rm = [rm for rm in an_plano.mercados if "corner" in rm.id_mercado.lower()]
    assert len(crn_rm) == 3
    assert any(rm.detalle.get("distribucion_activa") == "binomial_negativa" for rm in crn_rm)

    # Pre-partido + liquidación en ledger tmp_path
    snap = ep.snapshot_desde_analisis(prep_on)
    ruta_snap = ep.guardar_snapshot(snap, directorio=str(tmp_path / "snaps"))
    ruta_led = str(tmp_path / "ledger.jsonl")
    r_pre = ep.registrar_sombra_prepartido_en_ledger_oos(prep_on, ruta_snapshot=ruta_snap, ruta_ledger=ruta_led)
    assert r_pre["registrado"] is True
    assert r_pre["snapshot_sha256"] == hashlib.sha256(Path(ruta_snap).read_bytes()).hexdigest()

    regs_pre = ep.cargar_ledger_oos(ruta_led)
    assert len(regs_pre) == 1
    assert regs_pre[0]["estado_registro"] == "pre_partido"
    assert regs_pre[0]["modo_sombra"]["esquema"] == "sombra-v2-tribrazo"
    assert "lambdas_plano" in regs_pre[0]["modo_sombra"]
    assert set(regs_pre[0]["modo_sombra"]["brazos"].keys()) == {"soloA", "A+B", "plano"}
    assert any(c.get("prob_plano") is not None for c in regs_pre[0]["modo_sombra"]["comparaciones"])

    snap_dict = json.loads(Path(ruta_snap).read_text(encoding="utf-8"))
    res_obj = ep.ResultadoPartido(
        id_partido="6106338",
        estado="finalizado",
        local="Galatasaray",
        visitante="Barcelona",
        goles_local=1,
        goles_visitante=2,
        goles_local_1t=0,
        goles_visitante_1t=1,
        inicio_utc="2026-10-13T19:00:00Z",
        competicion="UEFA Champions League",
        fuente="fotmob",
        consultado_en="2026-10-14T00:00:00Z",
        corners_local={"FT": 5, "1T": 2, "2T": 3},
        corners_visitante={"FT": 6, "1T": 3, "2T": 3},
        amarillas_local={"FT": 2, "1T": 1, "2T": 1},
        amarillas_visitante={"FT": 2, "1T": 1, "2T": 1},
        rojas_local=0,
        rojas_visitante=0,
    )
    r_liq = ep.liquidar_sombras_en_ledger_oos(
        [snap_dict],
        {"Galatasaray vs Barcelona": res_obj},
        ruta_ledger=ruta_led,
    )
    assert r_liq["n_liquidados_nuevos"] == 1
    assert ep.verificar_cadena_ledger_oos(ruta_led)["ok"] is True

    regs_liq = ep.cargar_ledger_oos(ruta_led)
    assert len(regs_liq) == 2
    assert regs_liq[1]["modo_sombra"]["brier_plano"] is not None
    assert regs_liq[1]["modo_sombra"]["log_loss_plano"] is not None

    obs_ext = ep.extraer_observaciones_sombra_desde_ledger(ruta_led)
    assert obs_ext["n_partidos_sombra_validos"] == 1
    assert set(obs_ext["observaciones"][0]["probs"].keys()) == {"base", "sombra", "plano"}


def test_ronda12_punto2_benchmark_json_oos():
    ruta_json = Path(ROOT) / "datos" / "ronda12_punto2_tercer_brazo_plano.json"
    assert ruta_json.exists()
    rep = json.loads(ruta_json.read_text(encoding="utf-8"))
    bench = rep["benchmark_24_oos_1376_filas"]
    assert bench["n_partidos_unicos"] == 24
    assert bench["n_filas"] == 1311
    ver = bench["versiones"]
    assert set(ver.keys()) == {"plano_poisson_nb", "plano_dixon_coles_nb", "soloA", "A+B"}
    assert ver["plano_poisson_nb"]["log_loss"] < ver["soloA"]["log_loss"]
    assert ver["plano_dixon_coles_nb"]["log_loss"] < ver["soloA"]["log_loss"]
    assert ver["plano_poisson_nb"]["log_loss"] < ver["A+B"]["log_loss"]
