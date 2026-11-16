import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from auditoria_historica import (
    formatear_matriz_45_partido,
    sanear_documento_auditoria_45,
    sanear_partido_auditoria_45,
)
from fuente_flashscore import emparejar_partido


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_COL = os.path.join(ROOT, "cache", "cache_fotmob_colombia_COL_False_False.json")
CACHE_CHI = os.path.join(ROOT, "cache", "cache_fotmob_chile_CHL_False_False.json")


def _fila(partido: dict, periodo: str, clave: str) -> dict:
    filas = ((partido.get("periodos") or {}).get(periodo) or {}).get("filas") or []
    for f in filas:
        if f.get("clave") == clave:
            return f
    return {}


def _cargar_30() -> list[dict]:
    with open(CACHE_COL, "r", encoding="utf-8") as f:
        doc_col = sanear_documento_auditoria_45(json.load(f))
    with open(CACHE_CHI, "r", encoding="utf-8") as f:
        doc_chi = sanear_documento_auditoria_45(json.load(f))
    p_col = [p for p in (doc_col.get("partidos") or []) if not p.get("es_amistoso")][:15]
    p_chi = [p for p in (doc_chi.get("partidos") or []) if not p.get("es_amistoso")][:15]
    return p_col + p_chi


def test_r9_p5_pases_total_igual_denominador_pases_ratio_m29_m30():
    pts = _cargar_30()
    p29 = pts[28]
    p30 = pts[29]

    assert (_fila(p29, "FT", "pases_total")["rival"], _fila(p29, "FT", "pases_total")["equipo"]) == (478.0, 278.0)
    assert (_fila(p29, "1T", "pases_total")["rival"], _fila(p29, "1T", "pases_total")["equipo"]) == (213.0, 133.0)
    assert (_fila(p29, "2T", "pases_total")["rival"], _fila(p29, "2T", "pases_total")["equipo"]) == (265.0, 145.0)
    assert _fila(p29, "1T", "pases_total")["rival"] + _fila(p29, "2T", "pases_total")["rival"] == 478.0

    assert (_fila(p30, "FT", "pases_total")["equipo"], _fila(p30, "FT", "pases_total")["rival"]) == (420.0, 382.0)
    assert (_fila(p30, "1T", "pases_total")["equipo"], _fila(p30, "1T", "pases_total")["rival"]) == (229.0, 190.0)
    assert (_fila(p30, "2T", "pases_total")["equipo"], _fila(p30, "2T", "pases_total")["rival"]) == (191.0, 192.0)
    assert _fila(p30, "1T", "pases_total")["equipo"] + _fila(p30, "2T", "pases_total")["equipo"] == 420.0
    assert _fila(p30, "1T", "pases_total")["rival"] + _fila(p30, "2T", "pases_total")["rival"] == 382.0


def test_r9_p5_entradas_partido_3_y_no_recalcular_denominador_dudoso():
    pts = _cargar_30()
    p3 = pts[2]
    assert (_fila(p3, "FT", "entradas_ganadas")["equipo"], _fila(p3, "FT", "entradas")["equipo"]) == (8.0, 15.0)
    assert (_fila(p3, "FT", "entradas_ganadas")["rival"], _fila(p3, "FT", "entradas")["rival"]) == (5.0, 9.0)
    assert (_fila(p3, "1T", "entradas_ganadas")["equipo"], _fila(p3, "1T", "entradas")["equipo"]) == (2.0, 6.0)
    assert (_fila(p3, "1T", "entradas_ganadas")["rival"], _fila(p3, "1T", "entradas")["rival"]) == (3.0, 5.0)
    assert (_fila(p3, "2T", "entradas_ganadas")["equipo"], _fila(p3, "2T", "entradas")["equipo"]) == (6.0, 9.0)
    assert (_fila(p3, "2T", "entradas_ganadas")["rival"], _fila(p3, "2T", "entradas")["rival"]) == (2.0, 4.0)

    # Caso sintetico generico con ganadas > intentadas fuera de M3: no inventar denominador ni recalcular %
    sint = {
        "id_partido": "9999999",
        "fecha": "2026-05-01",
        "equipo": "EquipoA",
        "rival": "EquipoB",
        "periodos": {
            "FT": {
                "filas": [
                    {"clave": "entradas_ganadas", "equipo": 8.0, "rival": 4.0},
                    {"clave": "entradas", "equipo": 5.0, "rival": 6.0},
                    {"clave": "entradas_ganadas_pct", "equipo": 53.0, "rival": 67.0},
                    {"clave": "entradas_pct", "equipo": 53.0, "rival": 67.0},
                ]
            },
            "1T": {"filas": []},
            "2T": {"filas": []},
        },
        "procedencia": {"FT": {"equipo": {}, "rival": {}}, "1T": {"equipo": {}, "rival": {}}, "2T": {"equipo": {}, "rival": {}}},
    }
    sanear_partido_auditoria_45(sint)
    et = _fila(sint, "FT", "entradas")
    ep = _fila(sint, "FT", "entradas_ganadas_pct")
    assert et["equipo"] is None
    assert ep["equipo"] is None  # nunca None != 0 ni recalculado a 160% o 62.5%


def test_r9_p5_centros_mitades_sin_dato_en_m1_y_m3_y_preserva_m30():
    pts = _cargar_30()
    p1 = pts[0]
    p3 = pts[2]
    p30 = pts[29]

    for p in (p1, p3):
        # FT se conserva observado
        assert _fila(p, "FT", "centros")["equipo"] is not None
        # 1T y 2T sin intentos observados -> sin_dato (None, nunca 0)
        for per in ("1T", "2T"):
            for k in ("centros", "centros_completados", "cruces_pct"):
                fl = _fila(p, per, k)
                assert fl.get("equipo") is None
                assert fl.get("rival") is None

    # Partido 30 (CHI[15]): Flashscore SI da 0/0 en 1T y 1/7 en 2T para Chile, debe preservarse
    assert _fila(p30, "1T", "centros_completados")["equipo"] == 0.0
    assert _fila(p30, "2T", "centros_completados")["equipo"] == 1.0
    assert _fila(p30, "2T", "centros")["equipo"] == 7.0


def test_r9_p5_emparejar_partido_no_invierte_empate_0_0():
    datos_fm = {
        "fecha": "2026-06-27",
        "local": "Colombia",
        "visitante": "Portugal",
        "goles_local": 0,
        "goles_visitante": 0,
    }
    candidatos = [
        {
            "id": "vL2qotaK",
            "fecha": "2026-06-27",
            "local": "Colombia",
            "visitante": "Portugal",
            "goles_local": 0,
            "goles_visitante": 0,
        }
    ]
    res = emparejar_partido(datos_fm, candidatos)
    assert res is not None
    assert res["candidato"]["id"] == "vL2qotaK"
    assert res["bandos_invertidos"] is False


def test_r9_p5_invarianza_lambda_colombia_chile():
    from decision_cobertura import banderas_comportamiento
    from motor_mercados import analizar

    with open(CACHE_COL, "r", encoding="utf-8") as f:
        doc_col = sanear_documento_auditoria_45(json.load(f))
    with open(CACHE_CHI, "r", encoding="utf-8") as f:
        doc_chi = sanear_documento_auditoria_45(json.load(f))

    # Mismo resultado con cache saneado (pases/entradas/centros no alteran ningun lambda)
    with banderas_comportamiento(activar_todas=False):
        an_a = analizar(
            doc_col, doc_chi, "Colombia", "Chile",
            fecha_partido="2026-11-12",
            competicion="Eliminatorias CONMEBOL",
            condicion_local="casa",
            condicion_visitante="fuera",
        )
    with banderas_comportamiento(activar_todas=True):
        an_ab = analizar(
            doc_col, doc_chi, "Colombia", "Chile",
            fecha_partido="2026-11-12",
            competicion="Eliminatorias CONMEBOL",
            condicion_local="casa",
            condicion_visitante="fuera",
        )
    assert float(an_a.motor.tasas.lambda_local_ft) == pytest.approx(1.10, abs=1e-6)
    assert float(an_a.motor.tasas.lambda_visitante_ft) == pytest.approx(0.82, abs=1e-6)
    assert float(an_ab.motor.tasas.lambda_local_ft) == pytest.approx(1.10, abs=1e-6)
    assert float(an_ab.motor.tasas.lambda_visitante_ft) == pytest.approx(0.82, abs=1e-6)
