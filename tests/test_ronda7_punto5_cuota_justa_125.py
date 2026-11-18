# -*- coding: utf-8 -*-
"""
Ronda 7 - Punto 5: Restablecimiento de cuota justa minima 1.25 (p <= 0.80 sin +EV real).
Si califican menos de 5 lineas, el Top 5 muestra menos lineas sin bajar el umbral a 1.14.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cuadro_especialista as ce
import decision_cobertura as dc
import motor_mercados as mm
import servicio_mercados as sm


FIXTURE_R5 = json.loads((ROOT / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))
FIXTURE_LN = json.loads((ROOT / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))


def test_punto5_cuota_justa_125_restablecida_por_defecto_y_con_activar_todas():
    assert dc.obtener_umbral_cuota_justa_top5() == pytest.approx(1.25, abs=1e-6)
    with dc.banderas_comportamiento(activar_todas=True):
        assert dc.obtener_umbral_cuota_justa_top5() == pytest.approx(1.25, abs=1e-6)


def test_punto5_top5_muestra_menos_de_5_lineas_sin_bajar_umbral_en_italia_francia_y_londrina():
    with dc.banderas_comportamiento(activar_todas=True):
        an_if = mm.analizar(
            FIXTURE_R5["docs"]["italy"],
            FIXTURE_R5["docs"]["france"],
            "Italy",
            "France",
            competicion="UEFA Nations League",
        )
        top5_if, s1_if, _ = sm.obtener_top5_y_seccion1_filtrados(an_if, an_if.metadatos, umbral=0.70)
        assert 1 <= len(top5_if) < 5
        for item in top5_if:
            assert float(item["probabilidad"]) <= 0.80 + 1e-6
            assert float(item["cuota_justa"]) >= 1.25 - 1e-4

        sec1_rows = ce._organizar_sec1_para_cuadro(
            an_if, s1_if, "Italy", "France", matriz_prob=an_if.motor.matriz_ft, top5_podio=top5_if
        )
        assert isinstance(sec1_rows, list) and len(sec1_rows) >= 1

        an_ln = mm.analizar(
            FIXTURE_LN["londrina"],
            FIXTURE_LN["nautico"],
            "Londrina",
            "Náutico",
            competicion="Brasileirao Serie C",
        )
        top5_ln, _, _ = sm.obtener_top5_y_seccion1_filtrados(an_ln, an_ln.metadatos, umbral=0.70)
        assert 1 <= len(top5_ln) < 5
        for item in top5_ln:
            assert float(item["probabilidad"]) <= 0.80 + 1e-6
            assert float(item["cuota_justa"]) >= 1.25 - 1e-4
