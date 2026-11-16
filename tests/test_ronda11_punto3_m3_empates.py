"""Tests para ORDEN RONDA 11 — Punto 3 (M3 entradas final vs fuente y 6 empates del banco)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evaluacion_pronosticos import auditar_m3_y_6_empates_ronda11


def test_ronda11_p3_m3_entradas_y_contradiccion():
    rep = auditar_m3_y_6_empates_ronda11(guardar_ruta=None)

    assert rep["m3_id_fotmob"] == "4667823"
    assert rep["m3_id_flashscore"] == "vL2qotaK"
    m3 = rep["m3_entradas_final_vs_fuente"]

    assert m3["FT"]["final_colombia"] == "8/15 (53%)"
    assert m3["FT"]["final_portugal"] == "5/9 (56%)"
    assert m3["FT"]["coincide_exactamente"] is True

    assert m3["1T"]["final_colombia"] == "2/6 (33%)"
    assert m3["1T"]["final_portugal"] == "3/5 (60%)"
    assert m3["1T"]["coincide_exactamente"] is True

    assert m3["2T"]["final_colombia"] == "6/9 (67%)"
    assert m3["2T"]["final_portugal"] == "2/4 (50%)"
    assert m3["2T"]["coincide_exactamente"] is True


def test_ronda11_p3_6_empates_banco_son_artefactos_gl_igual_gv():
    rep = auditar_m3_y_6_empates_ronda11(guardar_ruta=None)
    emp = rep["banco_83_empate_con_bandos_invertidos"]

    assert emp["total_en_validador_banco"] == 6
    assert emp["total_auditados"] == 6
    assert emp["total_artefactos_gl_igual_gv"] == 6
    assert emp["todos_son_artefactos"] is True

    for item in emp["detalle_6_partidos"]:
        assert item["ac_dir"] == 2
        assert item["ac_inv"] == 0
        assert item["bandos_invertidos_emparejamiento_actual"] is False
        assert item["es_artefacto_gl_igual_gv"] is True

    # Verifica artefacto concreto en 5190716 y 5190670 donde el cruce invertido dejó ganadas > intentadas
    por_pid = {x["id_partido"]: x for x in emp["detalle_6_partidos"]}
    assert por_pid["5190716"]["cache_entradas_crudo_rival"] == [19.0, 12.0]
    assert por_pid["5190670"]["cache_entradas_crudo_rival"] == [11.0, 10.0]

    # Verifica coincidencia exacta con artefacto JSON en disco
    cache_p = Path(ROOT) / "datos" / "ronda11_punto3_m3_empates.json"
    assert cache_p.exists()
    cached = json.loads(cache_p.read_text(encoding="utf-8"))
    assert cached == rep
