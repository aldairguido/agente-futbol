# -*- coding: utf-8 -*-
"""Tests para ORDEN RONDA 11 — Punto 1: Cadena completa de 6 etapas (1 a 6) con IC95."""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evaluacion_pronosticos import ETAPAS_CADENA_LAMBDA, reportar_cadena_6_etapas_completa_ronda11


def test_ronda11_punto1_cadena_6_etapas_y_deltas_4_a_6():
    rep = reportar_cadena_6_etapas_completa_ronda11(regenerar=False)
    assert rep["hashes"]["sha256_ids_1376"] == "2e6b62ac82638b7a9298dd9a5f4d50f0bb9d84cf21ae90116f3204068c1e6cc4"
    assert rep["hashes"]["sha256_ids_1506"] == "839c56f8bbe71392e758eee22e0f3db2ea9aa818ec5d5c498e1e76f78e839935"

    for clave, n_esp in [("soloA_1376", 1376), ("soloA_1506", 1506), ("AB_1376", 1376)]:
        c = rep["cohortes"][clave]
        assert c["n_filas"] == n_esp
        assert c["n_partidos_unicos"] == 24
        for ek in ETAPAS_CADENA_LAMBDA:
            v = c["versiones"][ek]
            assert isinstance(v["brier"], float) and len(v["brier_ic95"]) == 2
            assert isinstance(v["log_loss"], float) and len(v["log_loss_ic95"]) == 2
        assert len(c["transiciones_paso_a_paso"]) == 5
        for tr in c["transiciones_paso_a_paso"]:
            assert len(tr["d_brier_ic95"]) == 2
            assert len(tr["d_log_loss_ic95"]) == 2
        d46 = c["delta_4_a_6"]
        assert d46 is not None
        assert len(d46["d_brier_ic95"]) == 2
        assert len(d46["d_log_loss_ic95"]) == 2
        # Etapa 4 (doble ADN) supera a Etapa 6 (reglas completas) en todas las cohortes
        assert d46["d_brier"] > 0
        assert d46["d_log_loss"] > 0
        assert c["mejor_etapa_brier"] == "4_mas_doble_adn"
        assert c["mejor_etapa_log_loss"] == "4_mas_doble_adn"
