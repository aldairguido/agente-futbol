"""Tests para ORDEN RONDA 9 — Punto 7: Conteo corregido de doble ×1.35
incluyendo Turkey–Belgium y explicación verificable del '1/28' de Ronda 8."""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evaluacion_pronosticos import (
    auditar_doble_135_ronda9,
    contar_doble_defensa_rota_en_trazas,
)


def test_r9_p7_contar_doble_defensa_rota_incluye_turkey_belgium_en_total():
    registros = [
        {
            "job_id": "ARCH::1edc8c043cfdd28a",
            "partido": "Newcastle United vs Aston Villa",
            "competicion": "Premier League",
            "es_oos": True,
            "traza_lambda": [
                {"paso": 6, "etapa": "red_45_dcn", "regla": "copa_asedio_defensa_rota", "L0": 1.97, "L1": 2.66, "V0": 2.56, "V1": 2.56},
                {"paso": 10, "etapa": "post_sinc_mercados", "regla": "copa_asedio_defensa_rota", "L0": 2.05, "L1": 2.77, "V0": 2.28, "V1": 2.28},
            ],
        },
        {
            "job_id": "DESIGN::TB",
            "partido": "Turkey vs Belgium",
            "competicion": "UEFA Nations League",
            "es_oos": False,
            "traza_lambda": [
                {"paso": 6, "etapa": "red_45_dcn", "regla": "copa_asedio_defensa_rota", "L0": 1.05, "L1": 1.05, "V0": 3.42, "V1": 4.62},
                {"paso": 11, "etapa": "post_sinc_mercados", "regla": "copa_asedio_defensa_rota", "L0": 1.04, "L1": 1.04, "V0": 2.63, "V1": 3.55},
            ],
        },
    ]
    res = contar_doble_defensa_rota_en_trazas(registros)
    assert res["n_archivados_doble_135"] == 1
    assert res["n_total_con_diseno_doble_135"] == 2
    assert res["incluye_turkey_belgium"] is True
    assert {x["job_id"] for x in res["tickets_todos_doble_135"]} == {"ARCH::1edc8c043cfdd28a", "DESIGN::TB"}


def test_r9_p7_auditar_doble_135_real_85_archivados_y_turkey_belgium():
    rep = auditar_doble_135_ronda9(activar_b=False)
    assert rep["n_mercados_json_totales"] == 89
    assert rep["n_archivados_ejecutables"] == 85
    assert rep["n_ev93_id_partido_unicos"] == 27
    assert "28" in rep["explicacion_1_28_ronda8"]
    assert rep["turkey_belgium_incluido"] is True
    assert rep["londrina_nautico_n_aplicaciones"] == 0

    cnt = rep["conteo"]
    assert cnt["n_archivados_doble_135"] == 6
    assert cnt["n_oos_doble_135"] == 3
    assert cnt["n_total_con_diseno_doble_135"] == 7
    tb = rep["turkey_belgium_detalle"]
    assert tb is not None
    assert tb["etapas"] == ["red_45_dcn", "post_sinc_mercados"]
    assert round(float(tb["pasos"][0]["lam_v_despues"]), 2) == 4.62
    assert round(float(tb["pasos"][1]["lam_v_despues"]), 2) == 3.55
    assert "28" in rep["explicacion_1_28_ronda8"]
    assert "Turkey vs Belgium" in rep["explicacion_1_28_ronda8"]
