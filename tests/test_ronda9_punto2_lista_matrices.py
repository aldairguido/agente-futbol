"""
Tests para Ronda 9 — Punto 2:
Generación por código de la lista 5(a) desde las 30 matrices de Colombia y Chile
con fecha, marcador, torneo e ID FotMob, verificación de cero amistosos y cero fechas
imposibles, y lista exacta de IDs con mitades del Cuadro C de Chile.
"""

from __future__ import annotations

import pytest

from evaluacion_pronosticos import generar_lista_5a_desde_30_matrices


def test_generar_lista_5a_desde_30_matrices_verifica_conteos_marcadores_y_cuadro_c() -> None:
    res = generar_lista_5a_desde_30_matrices()

    col = res["colombia"]
    chi = res["chile"]

    assert col["n_total"] == 15
    assert col["n_casa_local"] == 7
    assert col["n_fuera_visita"] == 8

    assert chi["n_total"] == 15
    assert chi["n_casa_local"] == 6
    assert chi["n_fuera_visita"] == 9

    assert res["cero_amistosos_30_matrices"] is True
    assert res["cero_fechas_imposibles_30_matrices"] is True

    # Todos los 30 partidos tienen fecha, marcador, torneo e ID FotMob no vacío
    todos_30 = col["partidos_todos"] + chi["partidos_todos"]
    assert len(todos_30) == 30
    for p in todos_30:
        assert p["id_fotmob"], f"Falta id_fotmob en matriz {p['idx_matriz']}"
        assert p["fecha"] and p["fecha"] < "2026-11-12"
        assert p["torneo"]
        assert "friendly" not in p["torneo"].lower()
        assert "amistoso" not in p["torneo"].lower()

    # Verificar Matriz 3: Colombia 0-0 Portugal (ID 4667823)
    m3 = col["partidos_todos"][2]
    assert m3["idx_matriz"] == 3
    assert m3["id_fotmob"] == "4667823"
    assert m3["rival"] == "Portugal"
    assert m3["marcador_eq_riv"] == "0-0"
    assert m3["duelo_local_vs_visitante"] == "Colombia 0-0 Portugal"

    # Verificar Matriz 29: New Zealand 4-1 Chile (perspectiva Chile 1-4, xG 0.59 vs 2.27)
    m29 = chi["partidos_todos"][13]
    assert m29["idx_matriz"] == 29
    assert m29["id_fotmob"] == "xjJMMS8F"
    assert m29["rival"] == "New Zealand"
    assert m29["marcador_eq_riv"] == "1-4"
    assert m29["marcador_local_visitante"] == "4-1"
    assert m29["duelo_local_vs_visitante"] == "New Zealand 4-1 Chile"
    assert m29["xg_eq_riv"] == (pytest.approx(0.59, abs=1e-2), pytest.approx(2.27, abs=1e-2))

    # IDs con mitades del Cuadro C de Chile: [16, 17, 18, 21, 23, 26, 27, 29, 30]
    assert res["cuadro_c_chile_ids_con_mitades"] == [16, 17, 18, 21, 23, 26, 27, 29, 30]
    assert res["cuadro_c_chile_ids_sin_mitades"] == [19, 20, 22, 24, 25, 28]
    for bad_id in (19, 20, 22):
        assert bad_id not in res["cuadro_c_chile_ids_con_mitades"]
    for good_id in (23, 26, 27):
        assert good_id in res["cuadro_c_chile_ids_con_mitades"]
