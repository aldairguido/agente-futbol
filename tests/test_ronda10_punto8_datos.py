"""Tests para ORDEN RONDA 10 — Punto 8 (DATOS):
(a) M1 entradas 17 y 12 son conteos enteros de FotMob (no porcentajes);
(b) M3 ac_dir / ac_inv en empate 0-0 Colombia vs Portugal;
(c) Eliminación de los 17 tiros_libres_sin_invertir en el banco + validación Colombia-Chile 2026-11-13;
(d) Verificación de cachés truncados de Turquía no usados por Turkey vs Belgium.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evaluacion_pronosticos import auditar_datos_ronda10


def test_ronda10_punto8_datos_completo():
    rep = auditar_datos_ronda10()

    # 8(a): M1 17 y 12 son conteos enteros, no porcentajes
    m1 = rep["m1_entradas"]
    assert m1["fotmob_matchdetails_tackles"]["format"] == "integer"
    assert m1["fotmob_matchdetails_tackles"]["stats_ft"] == [12, 17]
    assert m1["fotmob_matchdetails_tackles"]["son_conteos_enteros"] is True
    assert m1["fotmob_matchdetails_tackles"]["son_porcentajes"] is False
    assert m1["post_saneamiento"]["ft_entradas_pct_conteo"] == (17.0, 12.0)
    assert "| 12 | 17 | 9 | 12 | 3 | 5 | FotMob |" in m1["post_saneamiento"]["fila_32_markdown"]

    # 8(b): M3 evidencia ac_dir / ac_inv en empate 0-0
    m3 = rep["m3_inversion_bandos"]
    emp_real = m3["emparejamiento_sobre_candidato_real_vL2qotaK"]
    assert emp_real["ac_dir"] == 2
    assert emp_real["amb_dir"] is True
    assert emp_real["ac_inv"] == 0
    assert emp_real["amb_inv"] is False
    assert emp_real["regla_actual_ac_inv_gt_ac_dir"] is False
    assert emp_real["bandos_invertidos_actual"] is False
    assert m3["emparejamiento_candidato_invertido_control"]["bandos_invertidos_actual"] is True
    assert m3["entradas_post_saneamiento"]["FT"]["equipo_colombia"] == (8.0, 15.0, 53.0)
    assert m3["entradas_post_saneamiento"]["FT"]["rival_portugal"] == (5.0, 9.0, 56.0)

    # 8(c): Los 17 tiros_libres_sin_invertir pasan a 0 y se validan en Col-Chi 2026-11-13
    tl = rep["tiros_libres_17_y_col_chi"]
    assert tl["banco_pre_tiros_libres_sin_invertir"] == 17
    assert tl["banco_post_tiros_libres_sin_invertir"] == 0
    assert "tiros_libres_sin_invertir_igual_faltas" not in tl["banco_post_por_tipo"]
    assert tl["banco_post_total_defectos"] == 7
    cc = tl["validacion_colombia_chile_2026_11_13"]
    assert cc["FT"]["cumple_tl_propio_igual_faltas_rival"] is True
    assert (cc["FT"]["tiros_libres_colombia"], cc["FT"]["tiros_libres_chile"]) == (14, 19)
    assert cc["1T"]["cumple_tl_propio_igual_faltas_rival"] is True
    assert (cc["1T"]["tiros_libres_colombia"], cc["1T"]["tiros_libres_chile"]) == (6, 9)
    assert cc["2T"]["cumple_tl_propio_igual_faltas_rival"] is True
    assert (cc["2T"]["tiros_libres_colombia"], cc["2T"]["tiros_libres_chile"]) == (8, 10)
    assert tl["sanear_repara_colombia_chile_sin_invertir"]["FT"] == (14.0, 19.0)
    assert tl["sanear_repara_colombia_chile_sin_invertir"]["1T"] == (6.0, 9.0)
    assert tl["sanear_repara_colombia_chile_sin_invertir"]["2T"] == (8.0, 10.0)

    # 8(d): Turkey-Belgium no usó cachés truncados de Turquía
    tur = rep["turkey_belgium_caches_truncados"]
    assert tur["turkey_belgium_uso_caches_truncados"] is False
    assert tur["referencias_en_codigo_py"] == []
    assert tur["ronda5_real_fixtures_turkey"]["n_partidos"] == 15
    assert tur["ronda5_real_fixtures_turkey"]["json_valido"] is True
    assert tur["auditoria_turquia_belgica_2026_11_13"]["usa_fotmob_100_opta"] is True
    for item in tur["caches_truncados_detectados"]:
        if item.get("existe"):
            assert item["json_valido"] is False
            assert item["bytes"] == 481224
