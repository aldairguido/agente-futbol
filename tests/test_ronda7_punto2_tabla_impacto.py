# -*- coding: utf-8 -*-
"""
Ronda 7 - Punto 2: Tabla de impacto (i) 5e8425f vs (ii) solo A vs (iii) A+B.

Verifica deterministicamente sobre fixtures reales:
  - Turkey vs Belgium (ronda5_real_fixtures.json)
  - Italy vs France (ronda5_real_fixtures.json)
  - Londrina vs Nautico (londrina_nautico_fixtures.json)
  - Trazabilidad exacta por etapa de por que Belgium pasa a lambda=2.98 (74.29%)
    en A+B vs lambda=3.55 (81.70%) en 5e8425f/soloA y 1.87 pre-DCN.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from decision_cobertura import banderas_comportamiento
import motor_mercados as mm
import servicio_mercados as sm


FIXTURES_R5 = json.loads((ROOT / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))
FIXTURES_LN = json.loads((ROOT / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))


def _1x2_pct(matriz):
    n = len(matriz)
    m = len(matriz[0])
    p1 = sum(matriz[i][j] for i in range(n) for j in range(m) if i > j) * 100.0
    px = sum(matriz[i][j] for i in range(n) for j in range(m) if i == j) * 100.0
    p2 = sum(matriz[i][j] for i in range(n) for j in range(m) if i < j) * 100.0
    return (round(p1, 2), round(px, 2), round(p2, 2))


def _ejecutar_caso(dl, dv, eq_l, eq_v, comp=None):
    an = mm.analizar(
        dl,
        dv,
        eq_l,
        eq_v,
        competicion=comp,
        condicion_local="casa",
        condicion_visitante="fuera",
    )
    t = an.motor.tasas
    top5, sec1, _ = sm.obtener_top5_y_seccion1_filtrados(an, metadatos={"equipo_local": eq_l, "equipo_visitante": eq_v}, umbral=0.70)
    return an, t, top5, sec1


def test_punto2_tabla_impacto_diseno_soloA_vs_AB():
    # (i) 5e8425f y (ii) solo A coinciden en lambdas y 1X2 para TB_rf, IF_rf y LN_fx
    esperado_soloA = {
        "TB": {
            "ft": (1.04, 3.55),
            "1t": (0.4412, 1.8923),
            "2t": (0.5988, 1.6577),
            "1x2": (8.34, 9.96, 81.70),
            "top5_len": 5,
        },
        "IF": {
            "ft": (1.03, 2.33),
            "1t": (0.3444, 1.1039),
            "2t": (0.6856, 1.2261),
            "1x2": (15.90, 17.01, 67.09),
            "top5_len": 5,
        },
        "LN": {
            "ft": (1.14, 0.85),
            "1t": (0.5286, 0.3480),
            "2t": (0.6114, 0.5020),
            "1x2": (42.41, 30.28, 27.31),
            "top5_len": 2,
        },
    }
    esperado_AB = {
        "TB": {
            "ft": (1.10, 2.98),
            "1t": (0.4626, 1.4987),
            "2t": (0.6374, 1.4813),
            "1x2": (12.55, 13.16, 74.29),
            "top5_len": 5,
        },
        "IF": {
            "ft": (1.31, 1.79),
            "1t": (0.4878, 0.7448),
            "2t": (0.8222, 1.0452),
            "1x2": (28.74, 22.01, 49.25),
            # Tras corregir 'al menos 1 goles' (g >= 1.0 -> 13/15=86.7% vs 73.02%, desv 13.7 > 10.0), quedan 3 líneas en Top 5
            "top5_len": 3,
        },
        "LN": {
            "ft": (1.14, 0.86),
            "1t": (0.5212, 0.3555),
            "2t": (0.6188, 0.5045),
            "1x2": (42.17, 30.22, 27.61),
            "top5_len": 2,
        },
    }

    casos = [
        ("TB", FIXTURES_R5["docs"]["turkey"], FIXTURES_R5["docs"]["belgium"], "Turquía", "Belgium", None),
        ("IF", FIXTURES_R5["docs"]["italy"], FIXTURES_R5["docs"]["france"], "Italy", "France", None),
        ("LN", FIXTURES_LN["londrina"], FIXTURES_LN["nautico"], "Londrina", "Náutico", "Brasileirao Serie C"),
    ]

    with banderas_comportamiento(activar_todas=False):
        for clave, dl, dv, el, ev, comp in casos:
            an, t, top5, _ = _ejecutar_caso(dl, dv, el, ev, comp)
            exp = esperado_soloA[clave]
            assert (t.lambda_local_ft, t.lambda_visitante_ft) == exp["ft"]
            assert (round(t.lambda_local_1t, 4), round(t.lambda_visitante_1t, 4)) == exp["1t"]
            assert (round(t.lambda_local_2t, 4), round(t.lambda_visitante_2t, 4)) == exp["2t"]
            assert _1x2_pct(an.motor.matriz_ft) == exp["1x2"]
            assert len(top5) == exp["top5_len"]

    with banderas_comportamiento(activar_todas=True, usar_cuota_justa_114_top5=True):
        for clave, dl, dv, el, ev, comp in casos:
            an, t, top5, _ = _ejecutar_caso(dl, dv, el, ev, comp)
            exp = esperado_AB[clave]
            assert (t.lambda_local_ft, t.lambda_visitante_ft) == exp["ft"]
            assert (round(t.lambda_local_1t, 4), round(t.lambda_visitante_1t, 4)) == exp["1t"]
            assert (round(t.lambda_local_2t, 4), round(t.lambda_visitante_2t, 4)) == exp["2t"]
            assert _1x2_pct(an.motor.matriz_ft) == exp["1x2"]
            assert len(top5) == exp["top5_len"]


def test_punto2_traza_exacta_turkey_belgium_355_a_298():
    dl = FIXTURES_R5["docs"]["turkey"]
    dv = FIXTURES_R5["docs"]["belgium"]

    with banderas_comportamiento(activar_todas=False):
        an_a, t_a, top5_a, _ = _ejecutar_caso(dl, dv, "Turquía", "Belgium")
        traza_a = {f"{s['etapa']}:{s['regla']}": s for s in an_a.motor.traza_lambda}
        assert round(float(traza_a["doble_adn:doble_adn_60_40"]["lam_v_despues"]), 2) == 1.87
        assert round(float(traza_a["red_45_dcn:reescalado_caja_azul_y_factores_xai"]["lam_v_despues"]), 2) == 2.71
        assert round(float(traza_a["post_sinc_mercados:copa_asedio_defensa_rota"]["lam_v_despues"]), 2) == 3.55
        assert (t_a.lambda_local_ft, t_a.lambda_visitante_ft) == (1.04, 3.55)
        assert _1x2_pct(an_a.motor.matriz_ft)[2] == 81.70
        assert any("Belgium Gana" in str(x.get("etiqueta")) for x in top5_a)

    with banderas_comportamiento(activar_todas=True, usar_cuota_justa_114_top5=True):
        an_ab, t_ab, top5_ab, _ = _ejecutar_caso(dl, dv, "Turquía", "Belgium")
        traza_ab = {f"{s['etapa']}:{s['regla']}": s for s in an_ab.motor.traza_lambda}
        assert round(float(traza_ab["doble_adn:doble_adn_60_40"]["lam_v_despues"]), 2) == 1.87
        assert round(float(traza_ab["red_45_dcn:reescalado_caja_azul_y_factores_xai"]["lam_v_despues"]), 2) == 2.21
        assert round(float(traza_ab["post_sinc_mercados:copa_asedio_defensa_rota"]["lam_v_despues"]), 2) == 2.98
        assert round(float(traza_ab["post_sinc_mercados:techo_lambda_ataque_bajo_local"]["lam_l_despues"]), 2) == 1.10
        assert (t_ab.lambda_local_ft, t_ab.lambda_visitante_ft) == (1.10, 2.98)
        assert _1x2_pct(an_ab.motor.matriz_ft)[2] == 74.29
        assert top5_ab[0]["etiqueta"] == "Doble Oportunidad X2: Empate o Belgium (FT)"
        assert any(
            x["etiqueta"] == "1X2: Belgium Gana (FT)" and round(float(x["probabilidad"]) * 100.0, 1) == 74.3
            for x in top5_ab
        )
