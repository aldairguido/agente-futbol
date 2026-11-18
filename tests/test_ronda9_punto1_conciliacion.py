"""
Tests para Ronda 9 — Punto 1:
Conciliación de Ronda 7 vs Ronda 8 sobre las mismas 1376 y 1506 filas OOS,
especificación de hashes y banderas por columna, detección automática de inversión
de etiquetas (5e8425f/994209a y soloA/A+B) y verificación de cronología de commits.
"""

from __future__ import annotations

import pytest

from evaluacion_pronosticos import (
    CRONOLOGIA_COMMITS_VERIFICADA,
    analizar_regresion_994209a_vs_5e8425f,
    conciliar_ronda7_vs_ronda8,
    normalizar_etiquetas_probs_modelos,
    verificar_no_inversion_etiquetas_modelos,
)


def test_conciliar_ronda7_vs_ronda8_paridad_exacta_1376_y_1506() -> None:
    res = conciliar_ronda7_vs_ronda8(n_boot=200, seed=42)

    cols = res["especificacion_columnas"]
    assert set(cols.keys()) == {"arch", "5e8425f", "994209a", "soloA", "A+B"}
    assert cols["5e8425f"]["hash_exacto"] == "5e8425f45ea6d3296523160cbb7a133f2c53f550"
    assert cols["994209a"]["hash_exacto"] == "994209a6b3b9a854067c3d53a12835a8c2eecb8a"
    assert cols["soloA"]["hash_exacto"] == "c2a1a34fe73f3885cf0d8d352b91e93c1e522cc7"
    assert cols["5e8425f"]["n_banderas_true"] == 0
    assert cols["soloA"]["n_banderas_true"] == 0
    assert cols["994209a"]["n_banderas_true"] == 4
    assert cols["A+B"]["n_banderas_true"] == 9

    i1376 = res["interseccion_1376"]
    assert i1376["n_filas"] == 1376
    assert i1376["n_partidos_unicos"] == 24
    assert len(i1376["row_ids"]) == 1376
    assert i1376["paridad_exacta_ronda7_vs_ronda8"] is True

    v7 = i1376["ronda7_evaluar_oos"]["total"]["versiones"]
    v8 = i1376["ronda8_analizar_regresion"]["total"]["versiones"]
    assert v7 == v8

    # Valores conciliados exactos en n=1376
    assert v7["arch"]["brier"] == pytest.approx(0.187028, abs=1e-6)
    assert v7["arch"]["log_loss"] == pytest.approx(0.626833, abs=1e-6)
    assert v7["5e8425f"]["brier"] == pytest.approx(0.194422, abs=1e-6)
    assert v7["5e8425f"]["log_loss"] == pytest.approx(0.672389, abs=1e-6)
    assert v7["994209a"]["brier"] == pytest.approx(0.190509, abs=1e-6)
    assert v7["994209a"]["log_loss"] == pytest.approx(0.615002, abs=1e-6)
    assert v7["soloA"]["brier"] == pytest.approx(0.193777, abs=1e-6)
    assert v7["soloA"]["log_loss"] == pytest.approx(0.670603, abs=1e-6)
    assert v7["A+B"]["brier"] == pytest.approx(0.190662, abs=1e-6)
    assert v7["A+B"]["log_loss"] == pytest.approx(0.614583, abs=1e-6)

    # Intersección 1506 (5e8425f ∩ soloA sin el gate 8/15 de córneres)
    i1506 = res["interseccion_1506_base_sin_gate_corners"]
    assert i1506["n_filas"] == 1506
    assert i1506["n_filas_corners_abstenidas_en_9942_y_AB"] == 130
    v1506 = i1506["ronda7_evaluar_oos"]["total"]["versiones"]
    assert v1506["arch"]["brier"] == pytest.approx(0.185737, abs=1e-6)
    assert v1506["5e8425f"]["brier"] == pytest.approx(0.192854, abs=1e-6)
    assert v1506["soloA"]["brier"] == pytest.approx(0.192264, abs=1e-6)


def test_detector_inversion_etiquetas_rechaza_intercambio_5e84_9942_y_soloa_ab() -> None:
    filas_invertidas = []
    for i in range(6):
        y = 0 if i % 2 == 0 else 1
        # Intercambiamos deliberadamente 5e8425f (0.65) y 994209a (0.90 sobreconfiado)
        filas_invertidas.append({
            "id_partido": f"m{i}",
            "partido": f"EquipoA{i} vs EquipoB{i}",
            "mercado": "Total de tiros de esquina",
            "evento_ocurrio": y,
            "probs": {
                "5e8425f": 0.65 if y == 0 else 0.75,
                "v5e84": 0.65 if y == 0 else 0.75,
                "994209a": 0.92 if y == 0 else 0.75,
                "v9942": 0.92 if y == 0 else 0.75,
            },
        })
    with pytest.raises(ValueError, match="Inversión de etiquetas detectada"):
        verificar_no_inversion_etiquetas_modelos(filas_invertidas)

    with pytest.raises(ValueError, match="Inversión de etiquetas detectada"):
        analizar_regresion_994209a_vs_5e8425f(filas_invertidas, n_boot=50, seed=42)

    with pytest.raises(ValueError, match="Conflicto de probabilidad entre alias"):
        normalizar_etiquetas_probs_modelos({"5e8425f": 0.6146, "v5e84": 0.6724})


def test_cronologia_commits_verificada_fbb6dc0_posterior_a_5e8425f_y_994209a() -> None:
    fechas = {c["commit_short"]: c["fecha_iso"] for c in CRONOLOGIA_COMMITS_VERIFICADA}
    assert fechas["5e8425f"] < fechas["c59c103"] < fechas["9593ba6"] < fechas["994209a"] < fechas["fbb6dc0"] < fechas["c2a1a34"]
