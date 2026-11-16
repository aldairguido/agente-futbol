"""Tests para ORDEN RONDA 10 — Punto 9 (DOBLE x1.35 por partidos únicos y
un solo denominador declarado = 73 partidos únicos reproducibles)."""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evaluacion_pronosticos import auditar_doble_135_unicos_ronda10


def test_ronda10_punto9_doble_135_partidos_unicos_y_denominador_unico():
    rep = auditar_doble_135_unicos_ronda10(activar_b=False)

    assert rep["denominador_unico_declarado"] == 73
    assert rep["composicion_denominador"]["archivados_unicos_reproducibles"] == 69
    assert rep["composicion_denominador"]["referencia_diseno_control_auditoria"] == 4

    # Sin duplicar New Zealand vs Inter Miami CF: 5 archivados únicos + 1 diseño (TB) = 6 / 73
    assert rep["n_archivados_unicos_doble_135"] == 5
    assert rep["n_diseno_unicos_doble_135"] == 1
    assert rep["n_oos_unicos_doble_135"] == 3
    assert rep["n_total_unicos_doble_135"] == 6
    assert rep["tasa_sobre_denominador_pct"] == 8.22

    nombres = [h["partido"] for h in rep["partidos_unicos_doble_135"]]
    assert len(nombres) == len(set(nombres)) == 6
    assert nombres.count("New Zealand vs Inter Miami CF") == 1
    assert "Turkey vs Belgium" in nombres
    assert "Everton vs Chelsea" in nombres
    assert "Newcastle United vs Aston Villa" in nombres
    assert "Union Berlin vs Borussia Dortmund" in nombres
    assert "Barcelona vs Aston Villa" in nombres

    dups = rep["conciliacion_tickets_vs_partidos_unicos"]["duplicado_eliminado"]
    assert len(dups) == 1
    assert dups[0]["conteo_tickets_ronda9"] == 2
    assert dups[0]["conteo_partido_unico_ronda10"] == 1
