"""
Tests para Ronda 10 — Punto 7:
  1. VETO 1X con brecha con signo: solo veta cuando modelo - observado > 10.0 pp.
  2. Reproducción exacta del 46.7% (14/30 FT < 1.5) frente a 1T real en matrices:
     11/14 (78.6%) / 12/15 (80.0%) Colombia y 6/9 (66.7%) Chile en mitades Cuadro C,
     y 24/28 (85.7%) en franjas_goles; corrige el cálculo 1T en calcular_frecuencia_observada.
"""

from __future__ import annotations

import pytest

from evaluacion_pronosticos import auditar_veto_1x_y_frec_1t_ronda10


def test_ronda10_punto7_veto_1x_con_signo_y_frecuencia_1t() -> None:
    r = auditar_veto_1x_y_frec_1t_ronda10()

    v1x = r["veto_1x_brecha_con_signo"]
    # Colombia-Chile 1X: modelo 72.8% < observado 93.3% (-20.5 pp) => NO vetado
    cc = v1x["colombia_chile_1x"]
    assert cc["observado_pct"] == pytest.approx(93.3, abs=0.1)
    assert cc["brecha_modelo_menos_obs_pp"] == pytest.approx(-20.5, abs=0.1)
    assert cc["excede_desvio_vetado"] is False

    # Modelo sobreconfiado: 71.7% > 40.0% (+31.7 pp > 10.0 pp) => SÍ vetado
    sc = v1x["caso_sobreconfiado_prueba"]
    assert sc["observado_pct"] == pytest.approx(40.0, abs=0.1)
    assert sc["brecha_modelo_menos_obs_pp"] == pytest.approx(31.7, abs=0.1)
    assert sc["excede_desvio_vetado"] is True

    # Reproducción exacta del 46.7% y cálculo real 1T
    f1t = r["origen_46_7_vs_real_1t"]
    bug = f1t["reproduccion_46_7_bug_ft_ignora_periodo_1t"]
    assert bug["colombia_ft_u15"] == "7/15"
    assert bug["chile_ft_u15"] == "7/15"
    assert bug["combinado"] == "14/30"
    assert bug["frecuencia_pct"] == pytest.approx(46.7, abs=0.1)

    mit = f1t["frecuencia_1t_cuadro_c_mitades"]
    assert "6/9 (66.7%)" in mit["chile_9_mitades_cuadro_c"]
    assert "11/14" in mit["colombia_sin_m14_0_0_inferido"]

    fr = f1t["frecuencia_1t_desde_franjas_goles"]
    assert "12/15 (80.0%)" in fr["colombia"]
    assert "12/13 (92.3%)" in fr["chile_con_franjas"]
    assert "24/28 (85.7%)" in fr["combinado_28p"]

    assert f1t["frecuencia_1t_corregida_en_motor_pct"] == pytest.approx(85.7, abs=0.1)
    assert f1t["veto_calibracion_1t_u15_75_7_en_AB"] is False
