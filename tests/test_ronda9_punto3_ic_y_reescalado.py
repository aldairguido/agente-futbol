# -*- coding: utf-8 -*-
"""
Tests de Ronda 9 — Punto 3 (5(b)):
  1. `n` = valores no nulos reales de cada serie (excluyendo `estimacion_modelo` y `sin_dato`):
     - Chile `goles_evitados`: `n=5` en 15P (`[0.49, 2.64, 1.02, -1.60, -1.00]`, media `+0.31`, SE `0.7526`, IC95 `[-1.1652, +1.7852]`)
       y `n=3` como visitante (`[0.49, 1.02, -1.60]`, media `-0.03`, SE `0.7998`, IC95 `[-1.5976, +1.5376]`).
     - Colombia local (`casa`): `n=6` con `xG` y `xGOT` (`xGOT - xG` media `+0.1033`, SE `0.3682`, IC95 `[-0.6183, +0.8249]`;
       `G - xG` media `+0.2367`, SE `0.6550`, IC95 `[-1.0472, +1.5206]`) y `n=11` en 15P.
  2. Aísla y corrige tras la bandera `usar_reescalado_sin_inversion_signo_dcn` (apagada por defecto: `False`)
     el reescalado aditivo de la Caja Azul que invertía el signo de `Finaliz` (`+0.9%` -> `-8.2%`).
  3. Verifica el valor por defecto de `usar_filtro_ruido_ic_factores_dcn` (`False`) y la tabla de impacto
     en `TB`, `IF`, `LN` y `Colombia-Chile`.
"""

from __future__ import annotations

from evaluacion_pronosticos import auditar_punto_5b_y_tabla_impacto_ronda9
from motor_recursivo_45_dcn import reescalar_factores_a_delta


def test_ronda9_punto3_n_no_nulos_ic_y_reescalado_sin_inversion():
    res = auditar_punto_5b_y_tabla_impacto_ronda9()

    # 1. Valores por defecto de ambas banderas (False)
    assert res["valor_defecto_usar_filtro_ruido_ic_factores_dcn"] is False
    assert res["valor_defecto_usar_reescalado_sin_inversion_signo_dcn"] is False
    assert res["activo_en_runtime_por_defecto_filtro_ruido"] is False
    assert res["activo_en_runtime_por_defecto_sin_inversion"] is False

    # 2. n = valores no nulos reales de cada serie e intervalos de confianza
    ic_cc = res["ic_colombia_chile"]
    port_riv = ic_cc["PortRiv_local"]
    finaliz = ic_cc["Finaliz_local"]

    # Chile goles_evitados: n=3 visitante, n=5 en 15P
    assert port_riv["insumo_cond_n"] == 3
    assert port_riv["insumo_cond_valores"] == [0.49, 1.02, -1.6]
    assert abs(port_riv["insumo_cond_media"] - (-0.03)) <= 1e-4
    assert abs(port_riv["insumo_cond_se"] - 0.7998) <= 1e-3
    assert port_riv["insumo_cond_ic95"][0] < 0.0 < port_riv["insumo_cond_ic95"][1]

    assert port_riv["insumo_15p_n"] == 5
    assert port_riv["insumo_15p_valores"] == [0.49, 2.64, 1.02, -1.6, -1.0]
    assert abs(port_riv["insumo_15p_media"] - 0.31) <= 1e-4
    assert abs(port_riv["insumo_15p_se"] - 0.7526) <= 1e-3
    assert port_riv["insumo_15p_ic95"][0] < 0.0 < port_riv["insumo_15p_ic95"][1]
    assert port_riv["supera_ruido"] is False

    # Colombia xGOT - xG y G - xG: n=6 local (casa), n=11 en 15P
    assert finaliz["insumo_cond_n"] == 6
    assert finaliz["insumo_cond_valores"] == [0.65, -1.09, 0.41, 1.39, -0.64, -0.1]
    assert abs(finaliz["insumo_cond_media"] - 0.1033) <= 1e-3
    assert abs(finaliz["insumo_cond_se"] - 0.3682) <= 1e-3
    assert finaliz["insumo_cond_ic95"][0] < 0.0 < finaliz["insumo_cond_ic95"][1]

    assert finaliz["insumo_15p_n"] == 11
    assert finaliz["insumo_15p_ic95"][0] < 0.0 < finaliz["insumo_15p_ic95"][1]
    assert finaliz["supera_ruido"] is False

    g_xg = finaliz["insumo_g_menos_xg"]
    assert g_xg["cond_n"] == 6
    assert abs(g_xg["cond_media"] - 0.2367) <= 1e-3
    assert abs(g_xg["cond_se"] - 0.6550) <= 1e-3
    assert g_xg["cond_ic95"][0] < 0.0 < g_xg["cond_ic95"][1]
    assert g_xg["15p_n"] == 11

    # 3. Aislamiento y corrección del reescalado aditivo que invertía el signo de Finaliz
    factores_crudos_col = {
        "F_posesion_progresiva": 3.9,
        "F_creacion": 3.3,
        "F_finalizacion": 0.9,
        "F_porteria_rival": -3.9,
        "F_fragilidad_rival": 3.8,
    }
    reesc_def = reescalar_factores_a_delta(
        factores_crudos_col, -18.0, fijos=("F_posesion_progresiva",), pisos={"F_fragilidad_rival": 4.0}, evitar_inversion_signo=False
    )
    assert reesc_def["F_finalizacion"] == -8.2
    assert reesc_def["F_porteria_rival"] == -11.9

    reesc_sin_inv = reescalar_factores_a_delta(
        factores_crudos_col, -18.0, fijos=("F_posesion_progresiva",), pisos={"F_fragilidad_rival": 4.0}, evitar_inversion_signo=True
    )
    assert reesc_sin_inv["F_finalizacion"] == 0.9  # preserva signo positivo +0.9%
    assert reesc_sin_inv["F_creacion"] == 3.3      # preserva signo positivo +3.3%
    assert reesc_sin_inv["F_porteria_rival"] < 0.0

    # 4. Tabla de impacto en TB, IF, LN y Colombia-Chile
    ti = res["tabla_impacto"]
    for k_p in ("TB", "IF", "LN", "Colombia-Chile"):
        assert k_p in ti
        assert "soloA_defecto" in ti[k_p]
        assert "AB_defecto" in ti[k_p]
        assert "AB_sin_inversion_signo" in ti[k_p]
        assert "AB_filtro_y_sin_inversion" in ti[k_p]

    cc_ab_def = ti["Colombia-Chile"]["AB_defecto"]
    cc_ab_sin_inv = ti["Colombia-Chile"]["AB_sin_inversion_signo"]
    cc_ab_filt_inv = ti["Colombia-Chile"]["AB_filtro_y_sin_inversion"]

    assert cc_ab_def["factores_crudos_local"]["F_finalizacion"] == 0.9
    assert cc_ab_def["factores_caja_azul_local"]["F_finalizacion"] < 0.0  # signo invertido (-8.2% en DCN / -9.2% post-sinc)
    assert cc_ab_sin_inv["factores_caja_azul_local"]["F_finalizacion"] == 0.9  # preserva signo positivo +0.9%
    assert cc_ab_filt_inv["factores_caja_azul_local"]["F_finalizacion"] == 0.0
    assert cc_ab_filt_inv["factores_caja_azul_local"]["F_porteria_rival"] == 0.0
