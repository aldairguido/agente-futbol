"""
Tests para Ronda 10 — Punto 5:
Descomposición completa de Colombia λ 1.39 -> 1.10 (etapas, factores F1..F5 crudos y
reescalados, ajuste de portero y techo Regla 21) + reproducción exacta de `"1.54 G"`.
"""

from __future__ import annotations

import pytest

from evaluacion_pronosticos import auditar_descomposicion_colombia_110_y_154g_ronda10


def test_auditar_descomposicion_colombia_110_y_154g_ronda10() -> None:
    r = auditar_descomposicion_colombia_110_y_154g_ronda10()

    assert r["lambda_base_entrada_dcn"] == pytest.approx(1.39, abs=1e-4)
    assert r["lambda_final"] == pytest.approx(1.10, abs=1e-4)

    red = r["descomposicion_interna_red_45_dcn"]
    assert red["ajuste_portero_local_goles"] == pytest.approx(-0.35, abs=1e-4)
    assert red["lam_post_iteraciones_bucle"] == pytest.approx(1.0718, abs=1e-3)
    assert red["factor_crudo_que_mas_empuja_baja"]["factor"] == "F_porteria_rival"
    assert red["factor_crudo_que_mas_empuja_baja"]["valor_pct"] == pytest.approx(-3.9, abs=1e-4)
    assert red["factor_red_que_mas_empuja"]["factor"] == "F_porteria_rival"
    assert red["factor_red_que_mas_empuja"]["valor_pct"] == pytest.approx(-12.0, abs=1e-4)

    g154 = r["reproduccion_154_g"]
    assert g154["valor_publicado_mu_local_casa_goles_favor"] == pytest.approx(1.54, abs=1e-4)
    assert g154["valor_reproducido_redondeado"] == pytest.approx(1.54, abs=1e-4)
    assert g154["coincide_exactamente"] is True
    assert g154["n_cond"] == 7
    assert g154["w_cond"] == pytest.approx(7.0 / 10.5, abs=1e-4)
    assert g154["media_gf_cond"] == pytest.approx(1.822047, abs=1e-4)
    assert g154["h_gf_lstm_15"] == pytest.approx(0.965, abs=1e-3)
