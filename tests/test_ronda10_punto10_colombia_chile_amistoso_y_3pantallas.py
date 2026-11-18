"""Tests para ORDEN RONDA 10 — Punto 10:
Colombia–Chile 2026-11-13 (1-0, amistoso) anotado en `ledger_oos` con etiqueta
'amistoso' sin entrar a promedios ni calibración, explicación física del 1-0
(2 remates a puerta, 2 paradas, autogol min 35 de G. Maripán + 1 error gol,
GEv Chile = 1.08 sin alterar) y prueba del parser sobre las 3 pantallas (FT, 1T, 2T)."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import evaluacion_pronosticos as ep


def test_ronda10_punto10_ledger_excluye_amistoso_y_prueba_3_pantallas(tmp_path):
    # 1. Ledger real: Colombia vs Chile 2026-11-13 ya registrado e idempotente
    rep = ep.auditar_colombia_chile_amistoso_3_pantallas_ronda10(ruta_ledger=None, guardar_ruta=None)
    assert rep["cadena_sha256"]["ok"] is True
    assert rep["cadena_sha256"]["entradas"] >= 3

    res_led = rep["resumen_ledger_sin_amistosos"]
    assert res_led["cadena_sha256_ok"] is True
    assert res_led["n_incluidos_promedios_calibracion"] == 2
    assert res_led["n_excluidos_amistosos"] >= 1
    assert any(
        x.get("id_partido") == "6539346" and x.get("tipo_partido") == "amistoso"
        for x in res_led["amistosos_excluidos"]
    )
    # No entra en promedios ni calibracion (solo Turkiye-Belgium e Italy-France):
    assert res_led["partidos_incluidos"] == [
        "Turkiye vs Belgium (2026-11-12)",
        "Italy vs France (2026-11-12)",
    ]

    # Point 3 sigue teniendo 0 partidos OOS competitivos posteriores y lista Colombia-Chile en amistosos_excluidos
    post3 = rep["evidencia_corners_nb_posteriores"]
    assert post3["n_partidos_posteriores"] == 0
    assert post3["brier"] is None
    assert post3["log_loss"] is None
    assert any(x.get("id_partido") == "6539346" for x in post3["amistosos_excluidos"])

    # 2. Aislamiento en ledger temporal: agregar un amistoso no altera promedios ni calibracion
    ruta_tmp = str(tmp_path / "ledger_test.jsonl")
    ep.registrar_en_ledger_oos(
        {
            "id_partido": "5181796",
            "partido": "Turkiye vs Belgium",
            "fecha": "2026-11-12",
            "competicion": "UEFA Nations League",
            "marcador_real": {"ft": [1, 2]},
            "resumen": {"aciertos": 52, "fallos": 6, "n_evaluables": 58, "brier": 0.0966945},
        },
        ruta_ledger=ruta_tmp,
    )
    antes = ep.resumen_ledger_oos(ruta_ledger=ruta_tmp)
    ep.registrar_colombia_chile_amistoso_ronda10(ruta_ledger=ruta_tmp)
    despues = ep.resumen_ledger_oos(ruta_ledger=ruta_tmp)
    assert antes["acierto_medio_equilibrado"] == despues["acierto_medio_equilibrado"]
    assert antes["brier_medio_equilibrado"] == despues["brier_medio_equilibrado"]
    assert despues["n_excluidos_amistosos"] == 1
    assert despues["n_incluidos_promedios_calibracion"] == 1

    # evaluar_tanda y evaluar_promocion_regla_futura también excluyen amistosos
    obs = [
        {"id_partido": "P1", "partido": "A vs B", "familia": "goles_totales", "evento_ocurrio": 1, "probs": {"base": 0.6, "sombra": 0.7}},
        {"id_partido": "6539346", "partido": "Colombia vs Chile", "tipo_partido": "amistoso", "entra_promedios": False, "entra_calibracion": False, "familia": "goles_totales", "evento_ocurrio": 1, "probs": {"base": 0.9, "sombra": 0.2}},
    ]
    prom = ep.evaluar_promocion_regla_futura(obs, nombre_bandera="usar_tope_dcn_18_pct", n_boot=50)
    assert prom["n_partidos_unicos"] == 1

    # 3. Explicacion física 1-0 + GEv Chile = 1.08 verbatim + parser 3 pantallas (FT, 1T, 2T)
    exp = rep["explicacion_1_0_y_gev_chile"]
    assert exp["remates_puerta_colombia"] == {"1T": 1.0, "2T": 1.0, "FT": 2.0}
    assert exp["paradas_chile"] == {"1T": 1.0, "2T": 1.0, "FT": 2.0}
    assert exp["errores_gol_chile"] == {"1T": 1.0, "2T": 0.0, "FT": 1.0}
    assert exp["autogol_fotmob_6539346"]["Maripán"][0]["ownGoal"] is True
    assert exp["gev_chile_fuente_sin_corregir"] == {
        "1T": 0.9,
        "2T": 0.18,
        "FT": 1.08,
        "suma_mitades": 1.08,
    }

    p3 = rep["parser_3_pantallas_ft_1t_2t"]
    assert p3["n_etiquetas_por_pantalla"] == {"FT": 34, "1T": 34, "2T": 34}
    assert p3["n_metricas_aditivas_validadas"] >= 40
    assert p3["mismatches_aditivos"] == []
    assert p3["tiros_libres_vs_faltas"]["FT"] == {"faltas": (19.0, 14.0), "tiros_libres": (14.0, 19.0)}
    assert p3["tiros_libres_vs_faltas"]["1T"] == {"faltas": (9.0, 6.0), "tiros_libres": (6.0, 9.0)}
    assert p3["tiros_libres_vs_faltas"]["2T"] == {"faltas": (10.0, 8.0), "tiros_libres": (8.0, 10.0)}
