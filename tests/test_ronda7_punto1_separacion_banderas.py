"""Tests Ronda 7 Punto 1: separación Conjunto A (sin cambiar probabilidades) vs Conjunto B (detrás de banderas apagadas por defecto)."""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import decision_cobertura as dc  # noqa: E402
import motor_mercados as mm  # noqa: E402


def _cargar_r5() -> dict:
    with open(os.path.join(ROOT, "tests", "fixtures", "ronda5_real_fixtures.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _cargar_ln() -> dict:
    with open(os.path.join(ROOT, "tests", "fixtures", "londrina_nautico_fixtures.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_todas_las_banderas_conjunto_b_apagadas_por_defecto():
    u = dc.cargar_umbrales_cobertura()
    assert len(dc.BANDERAS_COMPORTAMIENTO_DEFECTO) == 14
    for clave in dc.BANDERAS_COMPORTAMIENTO_DEFECTO:
        assert u.get(clave) is False, f"{clave} debe estar en False por defecto"
        assert dc.bandera_activa(clave) is False
    assert dc.obtener_umbral_cuota_justa_top5() == pytest.approx(1.25, abs=1e-6)

    excluidas = set(dc._BANDERAS_EXCLUIDAS_ACTIVAR_TODAS)
    with dc.banderas_comportamiento(activar_todas=True) as estado_on:
        for clave in dc.BANDERAS_COMPORTAMIENTO_DEFECTO:
            esperado = False if clave in excluidas else True
            assert estado_on[clave] is esperado
            assert dc.bandera_activa(clave) is esperado
        assert dc.obtener_umbral_cuota_justa_top5() == pytest.approx(1.25, abs=1e-6)
    with dc.banderas_comportamiento(activar_todas=True, usar_cuota_justa_114_top5=True) as estado_114:
        assert estado_114["usar_cuota_justa_114_top5"] is True
        assert dc.obtener_umbral_cuota_justa_top5() == pytest.approx(1.14, abs=1e-6)

    # Al salir del contexto vuelven a False
    for clave in dc.BANDERAS_COMPORTAMIENTO_DEFECTO:
        assert dc.bandera_activa(clave) is False
    assert dc.obtener_umbral_cuota_justa_top5() == pytest.approx(1.25, abs=1e-6)


def test_solo_a_preserva_lambdas_y_probabilidades_5e8425f():
    r5 = _cargar_r5()
    ln = _cargar_ln()

    # Turkey vs Belgium (solo A == 5e8425f)
    an_tb = mm.analizar(r5["docs"]["turkey"], r5["docs"]["belgium"], "Turkey", "Belgium", competicion="UEFA Nations League")
    t_tb = an_tb.motor.tasas
    assert round(t_tb.lambda_local_ft, 2) == 1.04
    assert round(t_tb.lambda_visitante_ft, 2) == 3.55
    assert round(t_tb.lambda_local_1t, 4) == pytest.approx(0.4412, abs=1e-3)
    assert round(t_tb.lambda_visitante_1t, 4) == pytest.approx(1.8923, abs=1e-3)
    top5_tb = mm.seleccionar_top_5_jugadas_recomendadas(an_tb.selecciones_filtradas, motor=an_tb.motor, eq_l="Turkey", eq_v="Belgium")
    assert top5_tb[0]["etiqueta"] == "1X2: Belgium Gana (FT)"
    assert top5_tb[3]["etiqueta"] == "Córneres Totales (FT): +7.5 Córn."

    # Italy vs France (solo A == 5e8425f, sin etiqueta invertida "-0.5")
    an_if = mm.analizar(r5["docs"]["italy"], r5["docs"]["france"], "Italy", "France", competicion="UEFA Nations League")
    t_if = an_if.motor.tasas
    assert round(t_if.lambda_local_ft, 2) == 1.03
    assert round(t_if.lambda_visitante_ft, 2) == 2.33
    assert round(t_if.lambda_local_1t, 4) == pytest.approx(0.3444, abs=1e-3)
    assert round(t_if.lambda_visitante_1t, 4) == pytest.approx(1.1039, abs=1e-3)
    top5_if = mm.seleccionar_top_5_jugadas_recomendadas(an_if.selecciones_filtradas, motor=an_if.motor, eq_l="Italy", eq_v="France")
    etqs_if = [x["etiqueta"] for x in top5_if]
    assert etqs_if == [
        "Doble Oportunidad X2: Empate o France (FT)",
        "Goles Totales (FT): +1.5 Goles",
        "Goles 2T: +0.5 Goles",
        "Córneres France (FT): +3.5 Córn.",
        "Goles Totales (FT): -4.5 Goles",
    ]
    assert all("-0.5 Goles" not in e for e in etqs_if)

    # Londrina vs Nautico (solo A == 5e8425f)
    an_ln = mm.analizar(ln["londrina"], ln["nautico"], "Londrina", "Náutico", competicion="Brasileirão Série B")
    t_ln = an_ln.motor.tasas
    assert round(t_ln.lambda_local_ft, 2) == 1.14
    assert round(t_ln.lambda_visitante_ft, 2) == 0.85
    top5_ln = mm.seleccionar_top_5_jugadas_recomendadas(an_ln.selecciones_filtradas, motor=an_ln.motor, eq_l="Londrina", eq_v="Náutico")
    assert [x["etiqueta"] for x in top5_ln] == [
        "Goles Náutico (FT): -1.5 Goles",
        "Córneres Totales (FT): -11.5 Córn.",
    ]


def test_bugfix_al_menos_vs_menos_en_conjunto_a():
    r5 = _cargar_r5()
    sel_al_menos = {
        "id_mercado": "total_goles",
        "mercado": "Total de goles",
        "evento": "al_menos_1",
        "etiqueta": "al menos 1 goles",
        "periodo": "2T",
        "linea": 1.0,
        "probabilidad": 0.8522,
    }
    f_obs = mm.calcular_frecuencia_observada(sel_al_menos, r5["docs"]["italy"], r5["docs"]["france"], "Italy", "France")
    assert f_obs is not None and f_obs >= 0.80, f"al menos NO debe evaluarse como Under (dio {f_obs})"

    # _etiqueta_limpia tampoco debe rotular "al menos 1 goles" como "-0.5 Goles"
    an_if = mm.analizar(r5["docs"]["italy"], r5["docs"]["france"], "Italy", "France", competicion="UEFA Nations League")
    sel_2t = {
        "id_mercado": "total_goles",
        "mercado": "Total de goles",
        "evento": "al_menos_1",
        "etiqueta": "al menos 1 goles",
        "periodo": "2T",
        "linea": 1.0,
        "probabilidad": 0.80,
        "estado": "mantiene",
    }
    pod = mm.seleccionar_top_5_jugadas_recomendadas([sel_2t], motor=an_if.motor, eq_l="Italy", eq_v="France")
    assert pod and pod[0]["etiqueta"] == "Goles 2T: +0.5 Goles"


def test_activar_banderas_conjunto_b_produce_lambdas_fbb6dc0():
    r5 = _cargar_r5()
    with dc.banderas_comportamiento(activar_todas=True):
        an_tb = mm.analizar(r5["docs"]["turkey"], r5["docs"]["belgium"], "Turkey", "Belgium", competicion="UEFA Nations League")
        an_if = mm.analizar(r5["docs"]["italy"], r5["docs"]["france"], "Italy", "France", competicion="UEFA Nations League")
    assert round(an_tb.motor.tasas.lambda_local_ft, 2) == 1.10
    assert round(an_tb.motor.tasas.lambda_visitante_ft, 2) == 2.98
    assert round(an_if.motor.tasas.lambda_local_ft, 2) == 1.31
    assert round(an_if.motor.tasas.lambda_visitante_ft, 2) == 1.79
