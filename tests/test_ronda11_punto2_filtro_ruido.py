# -*- coding: utf-8 -*-
"""Tests para ORDEN RONDA 11 — Punto 2: Filtro de ruido Colombia, tabla de impacto y techo Attack_L<35."""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from decision_cobertura import _BANDERAS_AUXILIARES_DEFECTO
from evaluacion_pronosticos import auditar_filtro_ruido_colombia_ronda11


def test_ronda11_punto2_filtro_ruido_y_techo_attack_lt_35():
    assert _BANDERAS_AUXILIARES_DEFECTO["usar_filtro_ruido_ic_factores_dcn"] is False
    rep = auditar_filtro_ruido_colombia_ronda11(regenerar=False)
    assert rep["valor_por_defecto"] is False

    cc = rep["tabla_impacto"]["Colombia-Chile"]
    # Sin filtro: ajuste_portero_local = -0.35 y F_porteria_rival = -11.4 / -12.0
    assert cc["soloA_sin_filtro"]["ajuste_portero_local"] == -0.35
    assert cc["AB_sin_filtro"]["F_porteria_rival_reescalado_L"] == -12.0
    # Con filtro: ambos pasan a 0.0 y lambda_red sube 1.07 -> 1.28 (soloA), 1.14 -> 1.28 (AB), 1.39 -> 1.49 (AB_sininv)
    for m_fil in ("soloA_con_filtro", "AB_con_filtro", "AB_sininv_con_filtro"):
        assert cc[m_fil]["ajuste_portero_local"] == 0.0
        assert cc[m_fil]["F_porteria_rival_reescalado_L"] == 0.0
    assert cc["soloA_con_filtro"]["lambda_red"][0] > cc["soloA_sin_filtro"]["lambda_red"][0]
    assert cc["AB_con_filtro"]["lambda_red"][0] > cc["AB_sin_filtro"]["lambda_red"][0]

    # Tabla incluye TB, IF y LN completos
    for k in ("TB", "IF", "LN"):
        assert k in rep["tabla_impacto"]
        assert rep["tabla_impacto"][k]["AB_con_filtro"]["F_porteria_rival_reescalado_L"] == 0.0

    # Origen Attack_L < 35 registrado como regla operativa sin validación
    org = rep["origen_umbral_attack_lt_35"]
    assert org["commit_introduccion"].startswith("e68fff4a")
    assert org["tiene_validacion_oos"] is False
    assert org["registro_validacion"] == "regla operativa sin validación"
