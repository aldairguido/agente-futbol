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
        assert p_bol["equipo_visita"] == "Universitario de Vinto"
        assert p_bol["equipo_visita"] not in ("FC Universitario", "CD Universitario")
        assert p_bol["pais_iso"] == "BOL"

        res_bol = ejecutar_sync_seguro(
            resolver,
            "división profesional",
            "Aurora",
            "universitario de vinto",
        )
        assert tuple(res_bol) == ("Aurora", "Universitario de Vinto", "BOL")
        assert res_bol[1] not in ("FC Universitario", "CD Universitario")

        # Sin escribir liga explícita también detecta BOL por los clubes
        res_bol_auto = ejecutar_sync_seguro(resolver, "", "Aurora", "universitario de vinto")
        assert tuple(res_bol_auto) == ("Aurora", "Universitario de Vinto", "BOL")
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
            "partidos": [
                {"id_partido": f"au{i}", "fecha": f"2026-0{min(9, i//2 + 1)}-10", "torneo": "Bolivia", "rival": "Universitario de Vinto" if i == 0 else "Bolívar", "goles_equipo": 2, "goles_rival": 0, "condicion": "casa"}
                for i in range(15)
            ],
            "resumen": {"partidos_con_datos": 15},
        }
        doc_vinto_mock = {
            "equipo": {"id": "20202", "nombre": "Universitario de Vinto", "liga": "División Profesional", "id_liga": 301, "pais_iso": "BOL"},
            "partidos": [
                {"id_partido": f"uv{i}", "fecha": f"2026-0{min(9, i//2 + 1)}-12", "torneo": "Bolivia", "rival": "Aurora" if i == 0 else "The Strongest", "goles_equipo": 1, "goles_rival": 1, "condicion": "fuera"}
                for i in range(15)
            ],
            "resumen": {"partidos_con_datos": 15},
        }
        guardar_auditoria_en_memoria(construir_clave_cache_entidad("Aurora", "BOL", False, False), doc_aurora_mock)
        guardar_auditoria_en_memoria("aurora", doc_aurora_mock)
        guardar_auditoria_en_memoria(construir_clave_cache_entidad("Universitario de Vinto", "BOL", False, False), doc_vinto_mock)
        guardar_auditoria_en_memoria("universitario_de_vinto", doc_vinto_mock)
        guardar_auditoria_en_memoria("universitario_vinto", doc_vinto_mock)

        enf_bol = servicio_mercados.resolver_enfrentamiento(
            "división profesional Aurora vs universitario de vinto", limite=15
        )
        assert enf_bol["equipo_local"] == "Aurora"
        assert enf_bol["equipo_visitante"] == "Universitario de Vinto"

    asyncio.run(_correr_en_loop_asyncio())


def test_coherencia_competitiva_y_veto_falso_homonimo_cd_universitario():
    from fuentes_xg import resolver_pareja_misma_liga, variantes_de_busqueda
    from auditoria_historica import (
        guardar_auditoria_en_memoria,
        obtener_auditoria_en_memoria,
        validar_coherencia_competitiva_local_visita,
    )

    # 1. Prohibido borrar el apellido/ciudad en resolver y variantes_de_busqueda
    res = resolver("división profesional", "Aurora", "universitario de vinto")
    assert tuple(res) == ("Aurora", "Universitario de Vinto", "BOL")
    assert res[1] != "FC Universitario"
    assert res[1] != "CD Universitario"

    vars_vinto = variantes_de_busqueda("universitario de vinto")
    assert "Universitario de Vinto" in vars_vinto
    assert "universitario" not in [v.lower() for v in vars_vinto]

    # 2. puntuar_candidato para "universitario de vinto" con pais_iso="BOL" da máxima puntuación
    #    a "Universitario de Vinto" (Bolivia) y descarta (< 0, -99999) a "CD Universitario" (Panamá, LPF Apertura)
    meta_bol = {"liga": "División Profesional", "pais": "bolivia", "pais_iso": "BOL", "liga_forzada": True}
    cand_vinto_bol = {
        "id": "1352110",
        "name": "Universitario de Vinto",
        "leagueName": "División Profesional",
        "ccode": "BOL",
        "score": 80.0,
    }
    cand_cd_univ_pan = {
        "id": "594210",
        "name": "CD Universitario",
        "leagueName": "LPF Apertura",
        "ccode": "PAN",
        "score": 950.0,
    }
    cand_univ_peru = {
        "id": "5021",
        "name": "Universitario de Deportes",
        "leagueName": "Liga 1",
        "ccode": "PER",
        "score": 990.0,
    }
    cand_tec_univ_ecu = {
        "id": "6021",
        "name": "Técnico Universitario",
        "leagueName": "LigaPro",
        "ccode": "ECU",
        "score": 700.0,
    }

    pts_vinto = puntuar_candidato(cand_vinto_bol, "universitario de vinto", rival_meta=meta_bol)
    pts_pan = puntuar_candidato(cand_cd_univ_pan, "universitario de vinto", rival_meta=meta_bol)
    pts_per = puntuar_candidato(cand_univ_peru, "universitario de vinto", rival_meta=meta_bol)
    pts_ecu = puntuar_candidato(cand_tec_univ_ecu, "universitario de vinto", rival_meta=meta_bol)

    assert pts_vinto > pts_pan
    assert pts_vinto[0] >= 0 and pts_vinto[4] > 0
    assert pts_pan[0] == -99999
    assert pts_per[0] == -99999
    assert pts_ecu[0] == -99999

    # 3. League-Peer & Fixture Graph Resolver: resolver_pareja_misma_liga extrae Universitario de Vinto
    #    directamente de la tabla/calendario de Aurora
    datos_fotmob_aurora = {
        "overview": {
            "table": [
                {
                    "data": {
                        "leagueName": "División Profesional",
                        "leagueId": 301,
                        "table": {
                            "all": [
                                {"id": 20201, "name": "Aurora"},
                                {"id": 1352110, "name": "Universitario de Vinto"},
                                {"id": 10001, "name": "Bolívar"},
                            ]
                        },
                    }
                }
            ]
        },
        "fixtures": {
            "allFixtures": {
                "fixtures": [
                    {
                        "id": "4500123",
                        "tournament": {"name": "Bolivia", "leagueId": 301},
                        "home": {"id": 20201, "name": "Aurora"},
                        "away": {"id": 1352110, "name": "Universitario de Vinto"},
                        "status": {"finished": True, "utcTime": "2026-09-19T20:00:00Z"},
                    }
                ]
            }
        },
    }
    par_resuelto = resolver_pareja_misma_liga(
        "Aurora",
        "universitario de vinto",
        pais_iso="BOL",
        liga_pref="División Profesional",
        datos_team_fotmob=datos_fotmob_aurora,
    )
    assert par_resuelto is not None
    assert str(par_resuelto["id"]) == "1352110"
    assert par_resuelto["nombre"] == "Universitario de Vinto"
    assert par_resuelto["resuelto_por_tabla_comun"] is True

    # 4. Compuerta validar_coherencia_competitiva_local_visita detecta y rechaza cualquier cruce
    #    donde el Local juegue en "Bolivia" y el Visitante juegue en "LPF Apertura" (Panamá),
    #    invalidando su caché inmediatamente
    audit_aurora_bol = {
        "equipo": {"id": "20201", "nombre": "Aurora", "liga": "Bolivia", "pais_iso": "BOL"},
        "partidos": [
            {"id_partido": f"b{i}", "torneo": "Bolivia", "rival": r}
            for i, r in enumerate(["Bolívar", "The Strongest", "Always Ready", "GV San José", "Universitario de Vinto"])
        ],
    }
    audit_cd_univ_pan = {
        "equipo": {"id": "594210", "nombre": "CD Universitario", "liga": "LPF Apertura", "pais_iso": "PAN"},
        "partidos": [
            {"id_partido": f"p{i}", "torneo": "LPF Apertura", "rival": r}
            for i, r in enumerate(["Tauro FC", "Plaza Amador", "Veraguas", "Árabe Unido", "San Francisco"])
        ],
    }
    guardar_auditoria_en_memoria("cd_universitario", audit_cd_univ_pan)
    assert obtener_auditoria_en_memoria("cd_universitario") is not None

    veredicto = validar_coherencia_competitiva_local_visita(audit_aurora_bol, audit_cd_univ_pan, pais_iso="BOL")
    assert bool(veredicto) is False
    assert veredicto["coherente"] is False
    assert veredicto["estado"] == "FALSO HOMÓNIMO EXTRANJERO"
    assert veredicto["falso_homonimo"] == "visita"
    assert veredicto["equipo_invalido"] == "CD Universitario"
    # El caché contaminado de CD Universitario debe haber sido invalidado automáticamente
    assert obtener_auditoria_en_memoria("cd_universitario") is None


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
    test_coherencia_competitiva_y_veto_falso_homonimo_cd_universitario()
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
        test_resolver_argentina_san_lorenzo_vs_platense_descarta_honduras()

    def test_6_bundesliga_femenina_bayern_vs_wolfsburgo(self):
        test_resolver_bundesliga_femenina_bayern_vs_wolfsburgo()

    def test_7_ligas_compuestas_y_cache_entidad(self):
        test_separador_ligas_compuestas_y_cache_entidad()

    def test_8_integracion_sin_nameerror_meta_ctx_y_colombia_uzbekistan(self):
        test_integracion_sin_nameerror_meta_ctx_y_colombia_uzbekistan()

    def test_9_async_playwright_y_bolivia_division_profesional(self):
        test_async_playwright_y_bolivia_division_profesional()

    def test_10_coherencia_competitiva_y_veto_falso_homonimo_cd_universitario(self):
        test_coherencia_competitiva_y_veto_falso_homonimo_cd_universitario()
