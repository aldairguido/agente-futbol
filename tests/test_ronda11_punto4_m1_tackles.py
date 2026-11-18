"""Tests para ORDEN RONDA 11 — Punto 4 (M1 tackles FT != 1T + 2T)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evaluacion_pronosticos import auditar_m1_tackles_ronda11


def test_ronda11_p4_m1_tackles_crudo_clasificacion_y_validador():
    rep = auditar_m1_tackles_ronda11(guardar_ruta=None)

    des = rep["desajuste_crudo_mitades"]
    assert des["switzerland_local_rival"]["suma_1t_2t"] == 8
    assert des["switzerland_local_rival"]["FT"] == 12
    assert des["switzerland_local_rival"]["coincide_ft"] is False
    assert des["colombia_visita_equipo"]["suma_1t_2t"] == 16
    assert des["colombia_visita_equipo"]["FT"] == 17
    assert des["colombia_visita_equipo"]["coincide_ft"] is False

    cd = rep["cache_disco_colombia"]
    assert cd["entradas_ft"] == [17.0, 12.0]
    assert cd["entradas_1t"] == [12.0, 9.0]
    assert cd["entradas_2t"] == [5.0, 3.0]
    assert cd["coincide_recalculo_pasada_3b"] is True
    assert cd["hallazgos_validador_sobre_cache_disco_m1"] == []

    cla = rep["clasificacion"]
    assert cla["discrepancia_fuente_cruda"] == "suma_mitades_distinta_ft"
    assert len(cla["hallazgos_sobre_fuente_cruda"]) >= 2
    assert len(cla["inconsistencias_tras_consolidar_nuevo"]) >= 2
    assert len(cla["hallazgos_validador_post_consolidar_nuevo"]) >= 2

    # Sin regresión en banco 83
    assert rep["banco_83_intacto_sin_regresion"] == {"total_pre": 152, "total_post": 7}

    # Exactitud contra artefacto en disco
    cache_p = Path(ROOT) / "datos" / "ronda11_punto4_m1_tackles.json"
    assert cache_p.exists()
    cached = json.loads(cache_p.read_text(encoding="utf-8"))
    assert cached == rep
