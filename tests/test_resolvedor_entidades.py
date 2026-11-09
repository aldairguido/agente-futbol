"""
Tests unitarios del Punto 31: Sistema Universal de Resolución de Entidades
Flashscore (ES) <-> FotMob (Nativo/EN) para +110 países y +250 ligas.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from resolvedor_entidades import (
    CATALOGO_LIGAS_FLASHSCORE,
    DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB,
    construir_clave_cache_entidad,
    formatear_mensaje_entidades_verificadas,
    parsear_comando_mercados,
    resolver,
    validar_y_puntuar_candidato_fotmob,
)
from fuentes_xg import puntuar_candidato


def test_resolver_bundesliga_colonia_vs_bayer_leverkusen():
    res = resolver("Bundesliga", "colonia", "Bayer Leverkusen")
    assert tuple(res) == ("1. FC Köln", "Bayer 04 Leverkusen", "DEU")
    assert "Bayern München" not in res[0]
    assert "Bayern München" not in res[1]
    assert res.es_femenino is False
    assert res.es_juvenil is False


def test_resolver_bundesliga_colonia_vs_bayer_sin_colision_bayern():
    res = resolver("Bundesliga", "colonia", "Bayer")
    assert tuple(res) == ("1. FC Köln", "Bayer 04 Leverkusen", "DEU")
    assert "Bayern" not in res[1]

    # Verificar que validar_y_puntuar_candidato_fotmob penaliza con -9999 a Bayern München cuando se busca "Bayer"
    cand_bayern = {"id": "9823", "name": "FC Bayern München", "leagueName": "Bundesliga", "ccode": "DEU"}
    cand_leverkusen = {"id": "8178", "name": "Bayer 04 Leverkusen", "leagueName": "Bundesliga", "ccode": "DEU"}
    assert validar_y_puntuar_candidato_fotmob(cand_bayern, "Bayer", "Bundesliga", pais_iso_esperado="DEU") == -9999.0
    assert validar_y_puntuar_candidato_fotmob(cand_leverkusen, "Bayer", "Bundesliga", pais_iso_esperado="DEU") > 150.0


def test_resolver_2_bundesliga_hamburgo_vs_magdeburgo_sin_contaminar():
    res = resolver("2. Bundesliga", "Hamburgo", "Magdeburgo")
    assert tuple(res) == ("Hamburger SV", "1. FC Magdeburg", "DEU")
    assert "bundesliga" not in res.equipo_local.lower()
    assert "bundesliga" not in res.equipo_local_raw.lower()

    parsed = parsear_comando_mercados("/mercados 2. Bundesliga Hamburgo vs Magdeburgo")
    assert parsed["liga"] == "2. Bundesliga"
    assert parsed["equipo_local_raw"] == "Hamburgo"
    assert parsed["equipo_local"] == "Hamburger SV"
    assert parsed["equipo_visita"] == "1. FC Magdeburg"
    assert parsed["pais_iso"] == "DEU"


def test_resolver_laliga_ea_sports_celta_vs_levante():
    res = resolver("LaLiga EA Sports", "Celta", "Levante")
    assert tuple(res) == ("Celta Vigo", "Levante", "ESP")

    parsed = parsear_comando_mercados("/mercados LaLiga EA Sports Celta vs Levante")
    assert parsed["liga"] == "LaLiga EA Sports"
    assert parsed["equipo_local"] == "Celta Vigo"
    assert parsed["equipo_visita"] == "Levante"
    assert parsed["pais_iso"] == "ESP"


def test_resolver_argentina_san_lorenzo_vs_platense_descarta_honduras():
    res = resolver("Argentina", "San Lorenzo", "Platense")
    assert tuple(res) == ("San Lorenzo", "CA Platense", "ARG")

    cand_arg = {"id": "108854", "name": "CA Platense", "leagueName": "Liga Profesional", "ccode": "ARG"}
    cand_hnd = {"id": "999999", "name": "Platense FC", "leagueName": "Liga Nacional", "ccode": "HND"}
    pts_arg = validar_y_puntuar_candidato_fotmob(cand_arg, "Platense", "Argentina", pais_iso_esperado="ARG")
    pts_hnd = validar_y_puntuar_candidato_fotmob(cand_hnd, "Platense", "Argentina", pais_iso_esperado="ARG")
    assert pts_arg > 150.0
    assert pts_hnd == -9999.0

    # También en puntuar_candidato de fuentes_xg
    meta_arg = {"liga": "Liga Profesional", "pais": "argentina", "pais_iso": "ARG", "liga_forzada": True}
    assert puntuar_candidato(cand_arg, "Platense", rival_meta=meta_arg) > puntuar_candidato(cand_hnd, "Platense", rival_meta=meta_arg)
    assert puntuar_candidato(cand_hnd, "Platense", rival_meta=meta_arg)[0] == -9999


def test_resolver_bundesliga_femenina_bayern_vs_wolfsburgo():
    res = resolver("Bundesliga Femenina", "Bayern", "Wolfsburgo")
    assert tuple(res) == ("FC Bayern München", "VfL Wolfsburg", "DEU")
    assert res.es_femenino is True
    assert res.es_juvenil is False

    # Verificar que cuando es_femenino=True se bonifican (+50) los equipos femeninos y cuando es False se descartan (-9999)
    cand_fem = {"id": "111", "name": "Bayern München (W)", "leagueName": "Frauen-Bundesliga", "ccode": "DEU"}
    assert validar_y_puntuar_candidato_fotmob(cand_fem, "Bayern", "Bundesliga", pais_iso_esperado="DEU", es_femenino_pedido=False) == -9999.0
    assert validar_y_puntuar_candidato_fotmob(cand_fem, "Bayern", "Bundesliga Femenina", pais_iso_esperado="DEU", es_femenino_pedido=True) > 150.0


def test_separador_ligas_compuestas_y_cache_entidad():
    p1 = parsear_comando_mercados("/mercados Primera Federación - Grupo 1 Deportivo vs Lugo")
    assert p1["liga"] == "Primera Federación - Grupo 1"
    assert p1["equipo_local_raw"] == "Deportivo"
    assert p1["equipo_visita_raw"] == "Lugo"
    assert p1["pais_iso"] == "ESP"

    p2 = parsear_comando_mercados("/mercados Liga 1 - Clausura Alianza Lima vs Universitario")
    assert p2["liga"] == "Liga 1 - Clausura"
    assert p2["equipo_local"] == "Alianza Lima"
    assert p2["equipo_visita"] == "Universitario de Deportes"
    assert p2["pais_iso"] == "PER"

    p3 = parsear_comando_mercados("/mercados Serie A Femenina Roma vs Juventus")
    assert p3["liga"] == "Serie A Femenina"
    assert p3["equipo_local"] == "AS Roma"
    assert p3["equipo_visita"] == "Juventus"
    assert p3["es_femenino"] is True

    # Clave de caché indexada por (fotmob_team_id, pais_iso, es_femenino, es_juvenil)
    c_koln = construir_clave_cache_entidad("colonia", "DEU", False, False)
    assert c_koln == "8722_DEU_False_False"
    c_bayer = construir_clave_cache_entidad("bayer", "DEU", False, False)
    assert c_bayer == "8178_DEU_False_False"
    c_bayern = construir_clave_cache_entidad("bayern", "DEU", False, False)
    assert c_bayern == "9823_DEU_False_False"
    assert c_bayer != c_bayern

    msg = formatear_mensaje_entidades_verificadas("1. FC Köln", "DEU", "Bayer 04 Leverkusen", "DEU", "Bundesliga")
    assert msg == "✅ Entidades verificadas: 1. FC Köln [DEU] vs Bayer 04 Leverkusen [DEU] (Liga: Bundesliga)."


def test_integracion_sin_nameerror_meta_ctx_y_colombia_uzbekistan():
    from auditoria_historica import auditar_equipo, guardar_auditoria_en_memoria
    import servicio_mercados

    # 1. Con liga explícita: "/mercados primera a junior vs Cúcuta"
    p_col_liga = parsear_comando_mercados("/mercados primera a junior vs Cúcuta")
    assert p_col_liga["equipo_local"] == "Junior FC"
    assert p_col_liga["equipo_visita"] == "Cúcuta Deportivo"
    assert p_col_liga["pais_iso"] == "COL"
    assert tuple(resolver("primera a", "junior", "Cúcuta")) == ("Junior FC", "Cúcuta Deportivo", "COL")

    # 2. Sin liga explícita: "/mercados junior vs Cúcuta" -> propaga automáticamente COL
    p_col_sin_liga = parsear_comando_mercados("/mercados junior vs Cúcuta")
    assert p_col_sin_liga["equipo_local"] == "Junior FC"
    assert p_col_sin_liga["equipo_visita"] == "Cúcuta Deportivo"
    assert p_col_sin_liga["pais_iso"] == "COL"
    assert tuple(resolver("", "junior", "Cúcuta")) == ("Junior FC", "Cúcuta Deportivo", "COL")
    msg_col = formatear_mensaje_entidades_verificadas(
        p_col_sin_liga["equipo_local"],
        p_col_sin_liga["pais_iso"],
        p_col_sin_liga["equipo_visita"],
        p_col_sin_liga["pais_iso"],
        p_col_sin_liga["liga"] or "Primera A",
    )
    assert msg_col == "✅ Entidades verificadas: Junior FC [COL] vs Cúcuta Deportivo [COL] (Liga: Primera A)."

    # 3. Uzbekistán con y sin liga: "/mercados Gazalkent vs Respublika FA"
    p_uzb = parsear_comando_mercados("/mercados Gazalkent vs Respublika FA")
    assert p_uzb["equipo_local"] == "FK Gazalkent"
    assert p_uzb["equipo_visita"] == "Respublika FA"
    assert p_uzb["pais_iso"] == "UZB"
    assert tuple(resolver("Pro Liga", "Gazalkent", "Respublika FA")) == ("FK Gazalkent", "Respublika FA", "UZB")

    # 4. Ejecución real del pipeline de auditar_equipo y servicio_mercados.resolver_enfrentamiento
    # para verificar que JAMÁS se lance NameError: name 'meta_ctx' is not defined
    doc_junior_mock = {
        "equipo": {"id": "10101", "nombre": "Junior FC", "liga": "Primera A", "id_liga": 274, "pais_iso": "COL"},
        "partidos": [{"id_partido": f"j{i}", "fecha": f"2026-0{min(9, i//2 + 1)}-10", "goles_equipo": 2, "goles_rival": 1, "condicion": "casa"} for i in range(15)],
        "resumen": {"partidos_con_datos": 15},
    }
    doc_cucuta_mock = {
        "equipo": {"id": "10102", "nombre": "Cúcuta Deportivo", "liga": "Torneo BetPlay", "id_liga": 275, "pais_iso": "COL"},
        "partidos": [{"id_partido": f"c{i}", "fecha": f"2026-0{min(9, i//2 + 1)}-12", "goles_equipo": 1, "goles_rival": 1, "condicion": "fuera"} for i in range(15)],
        "resumen": {"partidos_con_datos": 15},
    }
    guardar_auditoria_en_memoria(construir_clave_cache_entidad("Junior FC", "COL", False, False), doc_junior_mock)
    guardar_auditoria_en_memoria("junior_fc", doc_junior_mock)
    guardar_auditoria_en_memoria(construir_clave_cache_entidad("Cúcuta Deportivo", "COL", False, False), doc_cucuta_mock)
    guardar_auditoria_en_memoria("cucuta_deportivo", doc_cucuta_mock)

    doc_j = auditar_equipo("Junior FC", limite=15, rival="Cúcuta Deportivo")
    assert doc_j["equipo"]["nombre"] == "Junior FC"
    doc_c = auditar_equipo("Cúcuta Deportivo", limite=15, rival=doc_j["equipo"])
    assert doc_c["equipo"]["nombre"] == "Cúcuta Deportivo"

    enf = servicio_mercados.resolver_enfrentamiento("primera a junior vs Cúcuta", limite=15)
    assert enf["equipo_local"] == "Junior FC"
    assert enf["equipo_visitante"] == "Cúcuta Deportivo"


def test_async_playwright_y_bolivia_division_profesional():
    import asyncio
    from fuentes_xg import ejecutar_sync_seguro
    from auditoria_historica import guardar_auditoria_en_memoria
    import servicio_mercados

    async def _correr_en_loop_asyncio():
        # 1. Resolución directa de "división profesional Aurora vs universitario de vinto" dentro de asyncio loop
        p_bol = ejecutar_sync_seguro(
            parsear_comando_mercados,
            "/mercados división profesional Aurora vs universitario de vinto",
        )
        assert p_bol["equipo_local"] == "Aurora"
        assert p_bol["equipo_visita"] == "FC Universitario"
        assert p_bol["pais_iso"] == "BOL"

        res_bol = ejecutar_sync_seguro(
            resolver,
            "División Profesional",
            "Aurora",
            "universitario de vinto",
        )
        assert tuple(res_bol) == ("Aurora", "FC Universitario", "BOL")

        # Sin escribir liga explícita también detecta BOL por los clubes
        res_bol_auto = ejecutar_sync_seguro(resolver, "", "Aurora", "universitario de vinto")
        assert tuple(res_bol_auto) == ("Aurora", "FC Universitario", "BOL")
        assert tuple(resolver("", "GV San José", "San Antonio Bulo Bulo")) == ("GV San José", "San Antonio Bulo Bulo", "BOL")

        # 2. Verificar que ejecutar_sync_seguro corre en un hilo separado sin running asyncio loop
        def _verificar_hilo_limpio():
            try:
                loop_interno = asyncio.get_running_loop()
            except RuntimeError:
                loop_interno = None
            assert loop_interno is None, "El hilo de ejecutar_sync_seguro NO debe tener un asyncio loop corriendo"
            return "HILO_LIMPIO_OK"

        resultado_hilo = ejecutar_sync_seguro(_verificar_hilo_limpio)
        assert resultado_hilo == "HILO_LIMPIO_OK"

        # 3. Ejecutar servicio_mercados.resolver_enfrentamiento dentro del loop asyncio
        doc_aurora_mock = {
            "equipo": {"id": "20201", "nombre": "Aurora", "liga": "División Profesional", "id_liga": 301, "pais_iso": "BOL"},
            "partidos": [{"id_partido": f"au{i}", "fecha": f"2026-0{min(9, i//2 + 1)}-10", "goles_equipo": 2, "goles_rival": 0, "condicion": "casa"} for i in range(15)],
            "resumen": {"partidos_con_datos": 15},
        }
        doc_vinto_mock = {
            "equipo": {"id": "20202", "nombre": "FC Universitario", "liga": "División Profesional", "id_liga": 301, "pais_iso": "BOL"},
            "partidos": [{"id_partido": f"uv{i}", "fecha": f"2026-0{min(9, i//2 + 1)}-12", "goles_equipo": 1, "goles_rival": 1, "condicion": "fuera"} for i in range(15)],
            "resumen": {"partidos_con_datos": 15},
        }
        guardar_auditoria_en_memoria(construir_clave_cache_entidad("Aurora", "BOL", False, False), doc_aurora_mock)
        guardar_auditoria_en_memoria("aurora", doc_aurora_mock)
        guardar_auditoria_en_memoria(construir_clave_cache_entidad("FC Universitario", "BOL", False, False), doc_vinto_mock)
        guardar_auditoria_en_memoria("fc_universitario", doc_vinto_mock)

        enf_bol = servicio_mercados.resolver_enfrentamiento(
            "división profesional Aurora vs universitario de vinto", limite=15
        )
        assert enf_bol["equipo_local"] == "Aurora"
        assert enf_bol["equipo_visitante"] == "FC Universitario"

    asyncio.run(_correr_en_loop_asyncio())


if __name__ == "__main__":
    test_resolver_bundesliga_colonia_vs_bayer_leverkusen()
    test_resolver_bundesliga_colonia_vs_bayer_sin_colision_bayern()
    test_resolver_2_bundesliga_hamburgo_vs_magdeburgo_sin_contaminar()
    test_resolver_laliga_ea_sports_celta_vs_levante()
    test_resolver_argentina_san_lorenzo_vs_platense_descarta_honduras()
    test_resolver_bundesliga_femenina_bayern_vs_wolfsburgo()
    test_separador_ligas_compuestas_y_cache_entidad()
    test_integracion_sin_nameerror_meta_ctx_y_colombia_uzbekistan()
    test_async_playwright_y_bolivia_division_profesional()
    print("OK: Todos los tests de test_resolvedor_entidades.py pasaron en verde (0 failed).")


import unittest


class TestResolvedorEntidades(unittest.TestCase):
    def test_1_bundesliga_colonia_bayer_leverkusen(self):
        test_resolver_bundesliga_colonia_vs_bayer_leverkusen()

    def test_2_bundesliga_colonia_bayer_sin_colision(self):
        test_resolver_bundesliga_colonia_vs_bayer_sin_colision_bayern()

    def test_3_2_bundesliga_hamburgo_magdeburgo(self):
        test_resolver_2_bundesliga_hamburgo_vs_magdeburgo_sin_contaminar()

    def test_4_laliga_ea_sports_celta_levante(self):
        test_resolver_laliga_ea_sports_celta_vs_levante()

    def test_5_argentina_san_lorenzo_platense(self):
        test_resolver_argentina_san_lorenzo_platense_descarta_honduras()

    def test_6_bundesliga_femenina_bayern_vs_wolfsburgo(self):
        test_resolver_bundesliga_femenina_bayern_vs_wolfsburgo()

    def test_7_ligas_compuestas_y_cache_entidad(self):
        test_separador_ligas_compuestas_y_cache_entidad()

    def test_8_integracion_sin_nameerror_meta_ctx_y_colombia_uzbekistan(self):
        test_integracion_sin_nameerror_meta_ctx_y_colombia_uzbekistan()

    def test_9_async_playwright_y_bolivia_division_profesional(self):
        test_async_playwright_y_bolivia_division_profesional()
