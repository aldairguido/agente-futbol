"""
Tests para Ronda 10 — Punto 1:
Rehacer Ronda 8 puntos 2 (ablación etapa por etapa) y 4.2 (umbrales no OOS por fecha)
con las etiquetas verificadas de Ronda 9 (`n=1376`, `n=1506` y subconjunto goles `n=698`),
y corregir la etiqueta "613 filas" por `472` cambiadas en la verificación anti-inversión.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluacion_pronosticos import (
    auditar_umbrales_no_oos_por_fecha,
    conciliar_ronda7_vs_ronda8,
    rehacer_ronda8_puntos_2_y_42_ronda10,
    verificar_no_inversion_etiquetas_modelos,
)


def test_verificar_no_inversion_reporta_472_cambiadas_sobre_613_universo() -> None:
    ruta = Path(__file__).resolve().parent.parent / "datos" / "oos_conciliacion_ronda7_ronda8.json"
    oos = json.loads(ruta.read_text(encoding="utf-8"))
    filas_1376 = [
        r for r in oos["filas_1506"]
        if all(k in (r.get("probs") or {}) for k in ("arch", "5e8425f", "994209a", "soloA", "A+B"))
    ]
    verif = verificar_no_inversion_etiquetas_modelos(filas_1376)

    assert verif["n_filas_corners_universo"] == 613
    assert verif["n_filas_corners_verificadas"] == 472
    assert verif["n_filas_corners_cambiadas"] == 472
    assert "472 filas cambiadas" in verif["etiqueta_muestra_corners"]

    for par in ("994209a vs 5e8425f", "A+B vs soloA"):
        p = verif["pares_verificados"][par]
        assert p["n_cambiadas"] == 472
        assert p["n_universo_corners"] == 613
        assert p["n_sin_cambio"] == 141
        assert p["d_log_loss_sobre_cambiadas"] == pytest.approx(-0.162089, abs=1e-5)
        assert p["d_log_loss_ponderado_universo"] == pytest.approx(-0.124806, abs=1e-5)


def test_auditar_umbrales_no_oos_soporta_1376_y_1506_sin_keyerror() -> None:
    ruta = Path(__file__).resolve().parent.parent / "datos" / "oos_conciliacion_ronda7_ronda8.json"
    oos = json.loads(ruta.read_text(encoding="utf-8"))
    filas_1506 = oos["filas_1506"]
    filas_1376 = [
        r for r in filas_1506
        if all(k in (r.get("probs") or {}) for k in ("arch", "5e8425f", "994209a", "soloA", "A+B"))
    ]

    u1376 = auditar_umbrales_no_oos_por_fecha(filas_1376, n_boot=200, seed=42)
    u1506 = auditar_umbrales_no_oos_por_fecha(filas_1506, n_boot=200, seed=42)

    assert u1376["antes_de_fijacion"]["n_filas"] == 1376
    assert u1376["antes_de_fijacion"]["n_partidos_unicos"] == 24
    assert u1376["despues_de_fijacion"]["estado"] == "muestra_posterior_vacia"
    assert u1376["despues_de_fijacion"]["comparacion"]["total"]["brier"] is None
    assert u1376["despues_de_fijacion"]["comparacion"]["total"]["log_loss"] is None

    assert u1506["antes_de_fijacion"]["n_filas"] == 1506
    assert u1506["antes_de_fijacion"]["n_partidos_unicos"] == 24
    assert u1506["despues_de_fijacion"]["estado"] == "muestra_posterior_vacia"
    assert u1506["despues_de_fijacion"]["comparacion"]["total"]["brier"] is None
    assert u1506["despues_de_fijacion"]["comparacion"]["total"]["log_loss"] is None


def test_rehacer_ablacion_y_umbrales_ronda10_coincide_con_ronda9_y_confirma_mejor_etapa() -> None:
    r = rehacer_ronda8_puntos_2_y_42_ronda10(regenerar=False)
    r9 = conciliar_ronda7_vs_ronda8(n_boot=200, seed=42)

    assert r["hashes"]["sha256_ids_1376"] == r9["interseccion_1376"]["sha256_row_ids"]
    assert r["hashes"]["sha256_ids_1506"] == r9["interseccion_1506_base_sin_gate_corners"]["sha256_row_ids"]
    assert r["paridad_reconstruccion"]["mismatches_6_mas_reglas_vs_soloA_1506"] == 0

    for clave, exp_n in (("soloA_1376", 1376), ("soloA_1506", 1506), ("soloA_698_goles", 698), ("AB_1376", 1376), ("AB_698_goles", 698)):
        ab = r["ablacion"][clave]
        assert ab["n_filas"] == exp_n
        assert ab["n_partidos_unicos"] == 24
        assert ab["mejor_etapa_brier"] == "4_mas_doble_adn"
        assert ab["mejor_etapa_log_loss"] == "4_mas_doble_adn"
        assert ab["versiones"]["6_mas_reglas"]["log_loss"] > ab["versiones"]["4_mas_doble_adn"]["log_loss"]
