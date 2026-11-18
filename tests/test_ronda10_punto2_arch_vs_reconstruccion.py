"""
Tests para Ronda 10 — Punto 2:
Auditoría de por qué `5e8425f` reconstruido empeora a `arch` en Goles, Resultado,
Tarjetas y Córneres con IC95% por partido, deslindando versión de código vs datos/fixtures.
"""

from __future__ import annotations

import pytest

from evaluacion_pronosticos import auditar_arch_vs_reconstruccion_ronda10


def test_auditar_arch_vs_reconstruccion_ronda10_causa_codigo_y_ic95() -> None:
    r = auditar_arch_vs_reconstruccion_ronda10(regenerar=False)
    caus = r["clasificacion_causal"]
    assert caus["causa_principal"] == "version_de_codigo"
    assert caus["n_exactos_etapa2"] == 10
    assert caus["fluminense_palmeiras_490ae216_con_corte_2026_08_15"]["s2_mod_con_corte_2026_08_15"][0] == pytest.approx(0.4311, abs=1e-4)
    assert caus["fluminense_palmeiras_490ae216_con_corte_2026_08_15"]["s2_mod_con_corte_2026_08_15"][1] == pytest.approx(0.6995, abs=1e-4)

    # En los 10 jobs exactos de Etapa 2, Goles y Resultado coinciden 0.0000 en s2_mod
    c10 = r["comparativas_ic95"]["cohorte_exacta_s2_10_jobs"]
    assert c10["Goles Partido/Mitades"]["diferencias"]["s2_mod - arch"]["d_log_loss"] == pytest.approx(0.0, abs=1e-4)
    assert c10["Resultado/DC/Hándicap"]["diferencias"]["s2_mod - arch"]["d_log_loss"] == pytest.approx(0.0, abs=1e-4)
    # Tarjetas ya empeora con IC95% excluyendo 0 en los mismos 10 jobs exactos (por cambio _mu_tarjetas)
    d_tarj_10 = c10["Tarjetas"]["diferencias"]["5e8425f - arch"]
    assert d_tarj_10["d_log_loss"] > 0.05
    assert d_tarj_10["d_log_loss_ic95"][0] > 0.0

    # Global n=1376 (24 partidos OOS) y excluyendo los 2 cortes (22 partidos OOS)
    ctot = r["comparativas_ic95"]["todos_26_jobs_1376"]
    assert ctot["TOTAL"]["n_filas"] == 1376
    assert ctot["TOTAL"]["diferencias"]["5e8425f - arch"]["d_log_loss_ic95"][0] > 0.0
    assert ctot["Tarjetas"]["diferencias"]["5e8425f - arch"]["d_log_loss_ic95"][0] > 0.0
    assert ctot["Resultado/DC/Hándicap"]["diferencias"]["5e8425f - arch"]["d_brier_ic95"][0] > 0.0

    csin2 = r["comparativas_ic95"]["sin_2_datacut_24_jobs"]
    assert csin2["TOTAL"]["diferencias"]["5e8425f - arch"]["d_log_loss_ic95"][0] > 0.0
    assert len(r["trazabilidad_26_jobs"]) == 26
