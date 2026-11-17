"""Tests para ORDEN RONDA 12 - Punto 5 (unificación h_gf_lstm_15 y prior 0.99 vs 1.47)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from decision_cobertura import bandera_activa
import evaluacion_pronosticos as ep


def test_ronda12_p5_unificacion_h_gf_lstm_15_y_explicacion_prior():
    assert bandera_activa("usar_unificacion_h_gf_lstm_15") is False
    rep = ep.auditar_unificacion_h_gf_lstm_15_ronda12(guardar_ruta=None)

    assert rep["bandera_usar_unificacion_h_gf_lstm_15_defecto"] is False
    res_u = rep["resumen_unificacion"]
    assert res_u["denominador_registros"] == 73

    # Con bandera apagada existen 27 lados en 26 partidos con discrepancia directa vs viva
    assert res_u["con_bandera_apagada_defecto"]["n_lados_con_diferencia_directa_vs_viva"] == 27
    assert res_u["con_bandera_apagada_defecto"]["n_local_con_diferencia"] == 12
    assert res_u["con_bandera_apagada_defecto"]["n_visitante_con_diferencia"] == 15
    assert res_u["con_bandera_apagada_defecto"]["n_partidos_con_diferencia"] == 26

    # Con bandera activa la unificacion directa vs viva es 100% exacta (0 discrepancias)
    assert res_u["con_bandera_activa"]["n_lados_con_diferencia_directa_vs_viva"] == 0
    assert res_u["con_bandera_activa"]["unificacion_100_pct"] is True

    # Efecto sobre los 73 registros
    ef = rep["efecto_sobre_73_registros"]
    assert ef["n_partidos_con_cambio_h_gf_lstm"] == 26
    assert ef["n_lados_con_cambio_h_gf_lstm"] == 27
    assert ef["n_partidos_con_cambio_lambda"] >= 5
    por_jid = {r["job_id"]: r for r in ef["registros_afectados"]}
    assert por_jid["AUDIT::COL_CHI"]["hl_viva_antes"] == 0.9891
    assert por_jid["AUDIT::COL_CHI"]["hl_viva_despues"] == 0.965
    assert por_jid["DESIGN::TB"]["hv_viva_antes"] == 1.7961
    assert por_jid["DESIGN::TB"]["hv_viva_despues"] == 1.7689

    # Explicacion exacta de por que Colombia tiene prior ~0.99 (0.9891) vs media 1.47 (22/15)
    exp = rep["explicacion_prior_099_vs_media_147"]
    assert exp["media_simple_15p_crudos"]["suma_goles"] == 22
    assert exp["media_simple_15p_crudos"]["n_partidos"] == 15
    assert exp["media_simple_15p_crudos"]["media_simple"] == 1.4667
    assert exp["ruta_viva_bandera_apagada_09891"]["panama_5_0_idx7_entrada"] == 2.7033
    assert exp["ruta_viva_bandera_apagada_09891"]["estado_h_final_idx14"] == 0.9819
    assert exp["ruta_viva_bandera_apagada_09891"]["media_ult5"] == 1.0
    assert exp["ruta_viva_bandera_apagada_09891"]["h_gf_lstm_15"] == 0.9891
    assert exp["ruta_unificada_bandera_activa_09650"]["panama_5_0_idx7_entrada"] == 1.602
    assert exp["ruta_unificada_bandera_activa_09650"]["estado_h_final_idx14"] == 0.9417
    assert exp["ruta_unificada_bandera_activa_09650"]["h_gf_lstm_15"] == 0.965

    # Artefacto guardado en disco coincide
    art = json.loads((Path(ROOT) / "datos" / "ronda12_punto5_unificacion_h_lstm.json").read_text(encoding="utf-8"))
    assert art["resumen_unificacion"]["con_bandera_activa"]["unificacion_100_pct"] is True
