"""
Tests para Ronda 10 — Punto 3:
Evidencia de Córneres NB (`usar_sobredispersion_nb_corners`, factor 1.05):
separación estricta entre los partidos de ajuste (JSON usado el 2026-11-13)
y partidos OOS competitivos posteriores (`0` -> `None`, regla operativa sin validación).
"""

from __future__ import annotations

import pytest

import decision_cobertura as dc
from evaluacion_pronosticos import auditar_evidencia_corners_nb_ronda10


def test_auditar_evidencia_corners_nb_ronda10_separa_ajuste_y_cero_posteriores() -> None:
    r = auditar_evidencia_corners_nb_ronda10(n_boot=200, seed=42)

    assert r["bandera"] == "usar_sobredispersion_nb_corners"
    assert r["valor_por_defecto"] is True
    assert dc.obtener_metadatos_defectos_banderas_b().get("usar_sobredispersion_nb_corners") == "regla operativa sin validación"
    assert r["activo_en_runtime"] is True
    assert r["estado_regla"] == "regla operativa en muestra sin validación OOS"

    aj = r["partidos_de_ajuste_en_muestra"]
    assert aj["n_partidos_unicos_corners_oos"] == 18
    assert aj["n_filas_corners_totales"] == 613
    assert aj["n_filas_corners_cambiadas"] == 472
    assert aj["aporte_a_diferencia_global_1376_log_loss"] == pytest.approx(-0.055600, abs=1e-4)

    post = r["partidos_oos_posteriores"]
    assert post["estado"] == "sin_partidos_oos_posteriores"
    assert post["n_partidos_posteriores"] == 0
    # None nunca es 0
    assert post["brier"] is None
    assert post["log_loss"] is None
    assert post["ic95"] is None
