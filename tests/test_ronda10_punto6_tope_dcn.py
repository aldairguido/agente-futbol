"""
Tests para Ronda 10 — Punto 6:
Tabla exacta del tope DCN (±18%) con signos `+` y `-`, por lado (`local`/`visitante`)
y etapa (`red_45_dcn`, `post_sinc_mercados`, `global`), sobre `n=73` partidos (`146` lados),
y conciliación explícita de los denominadores `73 / 83 / 85 / 89`.
"""

from __future__ import annotations

from evaluacion_pronosticos import resumen_tope_dcn_18_ronda10


def test_resumen_tope_dcn_18_ronda10_signos_y_denominadores() -> None:
    r = resumen_tope_dcn_18_ronda10(regenerar=False)

    assert r["denominador_declarado_partidos"] == 73
    assert r["denominador_declarado_lados"] == 146

    den = r["conciliacion_denominadores"]
    assert den["73_tope_dcn"]["valor"] == 73
    assert den["83_banco_partidos"]["valor"] == 83
    assert den["85_archivados_ejecutables"]["valor"] == 85
    assert den["89_mercados_json_totales"]["valor"] == 89

    tab = r["tabla_por_lado_y_etapa"]
    red = tab["en_red_45_dcn"]
    assert red["local"] == {"total": 16, "+18%": 11, "-18%": 5}
    assert red["visitante"] == {"total": 23, "+18%": 18, "-18%": 5}
    assert red["total"] == {"total": 39, "+18%": 29, "-18%": 10}

    post = tab["en_post_sinc_mercados"]
    assert post["local"] == {"total": 8, "+18%": 2, "-18%": 6}
    assert post["visitante"] == {"total": 15, "+18%": 13, "-18%": 2}
    assert post["total"] == {"total": 23, "+18%": 15, "-18%": 8}

    glob = tab["global_cualquier_etapa"]
    assert glob["local"]["+18%"] == 11
    assert glob["local"]["-18%"] == 6
    assert glob["visitante"]["+18%"] == 21
    assert glob["visitante"]["-18%"] == 6
    assert glob["total"]["+18%"] == 32
    assert glob["total"]["-18%"] == 12
    assert glob["total"]["total"] == 44
    assert glob["partidos_al_menos_un_lado"] == 37
