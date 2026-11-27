from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import decision_cobertura as dc
import fuente_flashscore as ff
import motor_inferencia as mi
import motor_mercados as mm
import servicio_mercados as sm


PARES_10_LIGAS_DISTINTAS = [
    ("Arsenal", "9825", "ENG", "Arsenal Sarandí", "10081", "ARG"),
    ("Barcelona", "8634", "ESP", "Barcelona SC", "8352", "ECU"),
    ("Liverpool", "8650", "ENG", "Liverpool Montevideo", "10256", "URU"),
    ("Nacional", "10255", "URU", "Atlético Nacional", "8342", "COL"),
    ("River Plate", "10076", "ARG", "River Plate Montevideo", "10257", "URU"),
    ("Racing Santander", "8696", "ESP", "Racing Club", "10080", "ARG"),
    ("Sporting Gijón", "9869", "ESP", "Sporting CP", "9768", "POR"),
    ("Guadalajara", "9901", "ESP", "Chivas Guadalajara", "6618", "MEX"),
    ("Everton", "8668", "ENG", "Everton Viña del Mar", "8412", "CHI"),
    ("Unión", "10084", "ARG", "Union Berlin", "8149", "DEU"),
]


def _doc_club_con_futuro(
    nombre: str,
    club_id: str,
    *,
    home_nombre: str,
    home_id: str,
    away_nombre: str,
    away_id: str,
    con_ids_en_fixture: bool = True,
) -> dict:
    return {
        "equipo": {"id": club_id if con_ids_en_fixture else "", "nombre": nombre},
        "partidos": [],
        "partidos_futuros": [
            {
                "id_partido": "9100001",
                "home_id": home_id if con_ids_en_fixture else "",
                "away_id": away_id if con_ids_en_fixture else "",
                "home_nombre": home_nombre,
                "away_nombre": away_nombre,
                "utcTime": "2026-12-15T20:00:00Z",
                "competicion": "Copa Internacional",
                "estado": "Not Started",
            }
        ],
    }


def test_r31_bloque1_inter_vs_internacional_ambos_ordenes_y_bandera():
    # Orden 1: consulta Inter (8636) vs Internacional (8702), cuando el fixture real tiene Internacional en casa
    for con_ids in (True, False):
        dl = _doc_club_con_futuro(
            "Inter", "8636",
            home_nombre="Internacional", home_id="8702",
            away_nombre="Inter", away_id="8636",
            con_ids_en_fixture=con_ids,
        )
        dv = {"equipo": {"id": "8702" if con_ids else "", "nombre": "Internacional"}, "partidos": [], "partidos_futuros": []}
        res = sm._partido_futuro_en_comun(dl, dv, "Inter", "Internacional")
        assert res is not None
        assert res["condicion_local"] == "fuera", f"Inter debe ser fuera (con_ids={con_ids}): {res}"
        assert res["condicion_visitante"] == "casa", f"Internacional debe ser casa (con_ids={con_ids}): {res}"

        # Orden 2: consulta Internacional (8702) vs Inter (8636), mismo fixture (Internacional en casa)
        res_inv = sm._partido_futuro_en_comun(dv, dl, "Internacional", "Inter")
        assert res_inv is not None
        assert res_inv["condicion_local"] == "casa"
        assert res_inv["condicion_visitante"] == "fuera"

        # Orden 3: fixture real tiene Inter en casa e Internacional fuera
        dl_casa = _doc_club_con_futuro(
            "Inter", "8636",
            home_nombre="Inter", home_id="8636",
            away_nombre="Internacional", away_id="8702",
            con_ids_en_fixture=con_ids,
        )
        res_casa = sm._partido_futuro_en_comun(dl_casa, dv, "Inter", "Internacional")
        assert res_casa is not None
        assert res_casa["condicion_local"] == "casa"
        assert res_casa["condicion_visitante"] == "fuera"
        res_casa_inv = sm._partido_futuro_en_comun(dv, dl_casa, "Internacional", "Inter")
        assert res_casa_inv is not None
        assert res_casa_inv["condicion_local"] == "fuera"
        assert res_casa_inv["condicion_visitante"] == "casa"

    # Metadatos de partido ya jugado sin futuros: Inter (fuera) vs Internacional (casa)
    doc_inter_jug = {
        "equipo": {"id": "8636", "nombre": "Inter"},
        "partidos": [{
            "id_partido": "77701", "fecha": "2026-11-10", "condicion": "fuera",
            "rival": "Internacional", "goles_favor": 1, "goles_contra": 2,
            "marcador": {"equipo": 1, "rival": 2, "texto": "2 - 1"},
        }],
    }
    doc_intl_jug = {
        "equipo": {"id": "8702", "nombre": "Internacional"},
        "partidos": [{
            "id_partido": "77701", "fecha": "2026-11-10", "condicion": "casa",
            "rival": "Inter", "goles_favor": 2, "goles_contra": 1,
            "marcador": {"equipo": 2, "rival": 1, "texto": "2 - 1"},
        }],
    }
    meta_jug = sm._metadatos_del_encuentro(doc_inter_jug, doc_intl_jug, "Inter", "Internacional")
    assert meta_jug["condicion_local"] == "fuera"
    assert meta_jug["condicion_visitante"] == "casa"
    meta_jug_inv = sm._metadatos_del_encuentro(doc_intl_jug, doc_inter_jug, "Internacional", "Inter")
    assert meta_jug_inv["condicion_local"] == "casa"
    assert meta_jug_inv["condicion_visitante"] == "fuera"

    # Con bandera en False reproduce el comportamiento previo
    with dc.banderas_comportamiento(usar_emparejamiento_exacto_sin_subcadena=False):
        dl_no_id = _doc_club_con_futuro(
            "Inter", "8636",
            home_nombre="Internacional", home_id="8702",
            away_nombre="Inter", away_id="8636",
            con_ids_en_fixture=False,
        )
        dv_no_id = {"equipo": {"id": "", "nombre": "Internacional"}, "partidos": [], "partidos_futuros": []}
        res_legacy = sm._partido_futuro_en_comun(dl_no_id, dv_no_id, "Inter", "Internacional")
        assert res_legacy["condicion_local"] == "casa"


def test_r31_bloque1_real_madrid_sociedad_athletic_atletico_y_10_pares():
    pares_todos = [
        ("Real Madrid", "8633", "ESP", "Real Sociedad", "8560", "ESP"),
        ("Athletic Club", "8315", "ESP", "Atlético", "9906", "ESP"),
        *PARES_10_LIGAS_DISTINTAS,
    ]
    assert len(PARES_10_LIGAS_DISTINTAS) == 10

    cli_fs = ff.ClienteFlashscore()
    for nom_a, id_a, _pais_a, nom_b, id_b, _pais_b in pares_todos:
        for con_ids in (True, False):
            # Caso donde B es local y A es visitante en el fixture programado
            doc_a = _doc_club_con_futuro(
                nom_a, id_a,
                home_nombre=nom_b, home_id=id_b,
                away_nombre=nom_a, away_id=id_a,
                con_ids_en_fixture=con_ids,
            )
            doc_b = {"equipo": {"id": id_b if con_ids else "", "nombre": nom_b}, "partidos": [], "partidos_futuros": []}

            r_ab = sm._partido_futuro_en_comun(doc_a, doc_b, nom_a, nom_b)
            assert r_ab is not None, f"Fallo en {nom_a} vs {nom_b} (con_ids={con_ids})"
            assert r_ab["condicion_local"] == "fuera", f"{nom_a} vs {nom_b} (con_ids={con_ids}) invirtió localía"
            assert r_ab["condicion_visitante"] == "casa"

            r_ba = sm._partido_futuro_en_comun(doc_b, doc_a, nom_b, nom_a)
            assert r_ba is not None
            assert r_ba["condicion_local"] == "casa"
            assert r_ba["condicion_visitante"] == "fuera"

        # Flashscore _parecido y buscar_evento_programado_feed_diario no deben confundir A con B
        assert ff._parecido(nom_a, nom_b) is False, f"_parecido({nom_a}, {nom_b}) dio True"
        assert ff._parecido(nom_b, nom_a) is False, f"_parecido({nom_b}, {nom_a}) dio True"

        cli_fs._CACHE_FEED_DIARIO.clear()
        feed_txt = (
            f"ZA÷LIGA OFICIAL DE PRUEBA RONDA 31~AA÷ID_BA¬AD÷1795557600¬AB÷1¬AE÷{nom_b}¬JA÷HB¬AF÷{nom_a}¬JB÷AA~"
            f"ZA÷LIGA OFICIAL DE PRUEBA RONDA 31~AA÷ID_AB¬AD÷1795557600¬AB÷1¬AE÷{nom_a}¬JA÷HA¬AF÷{nom_b}¬JB÷AB~"
            + ("~" * 60)
        )
        with patch.object(cli_fs, "_get", return_value=SimpleNamespace(status_code=200, text=feed_txt)):
            ev_ab = cli_fs.buscar_evento_programado_feed_diario(nom_a, nom_b, dias=(0,))
            ev_ba = cli_fs.buscar_evento_programado_feed_diario(nom_b, nom_a, dias=(0,))
        assert ev_ab is not None and ev_ab["id_flashscore"] == "ID_AB", f"Feed {nom_a} vs {nom_b}: {ev_ab}"
        assert ev_ba is not None and ev_ba["id_flashscore"] == "ID_BA", f"Feed {nom_b} vs {nom_a}: {ev_ba}"


def test_r31_bloque1_motor_mercados_e_inferencia_sin_subcadenas():
    # _es_mismo_equipo en motor_inferencia no debe confundir Inter con Internacional ni Athletic con Atlético
    assert mi._es_mismo_equipo("Inter", "Internacional") is False
    assert mi._es_mismo_equipo("Internacional", "Inter") is False
    assert mi._es_mismo_equipo("Athletic Club", "Atlético") is False
    assert mi._es_mismo_equipo("Real Madrid", "Real Sociedad") is False

    # calcular_frecuencia_observada en motor_mercados: selección de Internacional no debe leer historial de Inter
    doc_inter = {
        "equipo": {"id": "8636", "nombre": "Inter"},
        "partidos": [
            {"gf": 3, "gc": 0, "periodos": {"FT": {"filas": [{"clave": "corners", "equipo": 8, "rival": 2}]}}}
            for _ in range(15)
        ],
    }
    doc_intl = {
        "equipo": {"id": "8702", "nombre": "Internacional"},
        "partidos": [
            {"gf": 0, "gc": 2, "periodos": {"FT": {"filas": [{"clave": "corners", "equipo": 2, "rival": 7}]}}}
            for _ in range(15)
        ],
    }
    sel_vis_corners = SimpleNamespace(
        mercado="Córneres por equipo",
        evento="over_4.5",
        etiqueta="Córneres Internacional (FT): +4.5 Córn.",
        equipo="Internacional",
        periodo="FT",
        linea=4.5,
    )
    tasa = mm.calcular_frecuencia_observada(sel_vis_corners, doc_inter, doc_intl, "Inter", "Internacional")
    assert tasa == 0.0


def test_r31_bloque2_veredicto_sin_fallbacks_numericos_y_tope_099_solo_presentacion():
    import informe

    ver_vacio = mi.generar_veredicto_tactico_partido(
        nombre_local="EqA",
        nombre_visita="EqB",
        patrones_loc={},
        patrones_vis={},
        probs={"local": 0.45, "empate": 0.25, "visita": 0.30},
        cuadro_b_loc={},
        cuadro_b_vis={},
        cuadro_c_loc={},
        cuadro_c_vis={},
        ratings_loc={},
        ratings_vis={},
    )
    prohibidos = ("68.2", "52.1", "8/15", "8 victorias", "50.0%", "62.5", "11.0", "11.5", "3.7", "1.50")
    for tok in prohibidos:
        assert tok not in ver_vacio, f"Fallback numérico {tok!r} apareció en Veredicto vacío:\n{ver_vacio}"
    assert "sin dato" in ver_vacio

    rep_xai_vacio = mi.generar_reporte_xai_telegram(
        nombre_local="EqA",
        nombre_visita="EqB",
        patrones_loc={},
        patrones_vis={},
        probs={"local": 0.45, "empate": 0.25, "visita": 0.30},
        alertas=[],
        cuadro_b_loc={},
        cuadro_b_vis={},
        cuadro_c_loc={},
        cuadro_c_vis={},
        ratings_loc={},
        ratings_vis={},
    )
    for tok in (*prohibidos, "0/0", "0.0 tiros por gol"):
        assert tok not in rep_xai_vacio, f"Fallback {tok!r} apareció en reporte XAI vacío:\n{rep_xai_vacio}"
    assert "sin dato" in rep_xai_vacio

    cb_vacio = mi.calcular_cuadro_b_tendencias_15p([], "SinDatos")
    assert cb_vacio["n_partidos"] == 0
    assert cb_vacio["victorias"]["sin_dato"] is True
    assert cb_vacio["victorias"]["str"] == "sin dato"

    # Con bandera False restaura los fallbacks antiguos
    with dc.banderas_comportamiento(usar_sin_dato_sin_fallbacks_veredicto=False):
        ver_leg = mi.generar_veredicto_tactico_partido(
            "EqA", "EqB", {}, {}, {"local": 0.45, "empate": 0.25, "visita": 0.30}, {}, {}, {}, {}
        )
        assert "68.2" in ver_leg and "50.0%" in ver_leg and "3.7" in ver_leg and "1.50" in ver_leg

    # Demostración de que el tope 0.99 de informe._extraer_probabilidades_red_general es solo de presentación
    # y no altera las probabilidades reales del catálogo de mercados del motor (doble_oportunidad).
    mat_extrema = [[0.0] * 5 for _ in range(5)]
    mat_extrema[2][0] = 0.90
    mat_extrema[1][1] = 0.095
    mat_extrema[0][2] = 0.005
    r1x2 = mm.resultado_1x2(mat_extrema)
    assert abs((r1x2["1"] + r1x2["X"]) - 0.995) < 1e-9  # sin recorte 0.99 en el motor

    an_stub = SimpleNamespace(
        metadatos={"equipo_local": "EqA", "equipo_visitante": "EqB", "competicion": "LaLiga"},
        motor=SimpleNamespace(matriz_ft=mat_extrema, tasas=SimpleNamespace(lambda_local_ft=2.5, lambda_visitante_ft=0.2)),
        mercados=[],
        selecciones_filtradas=[],
    )
    info_pres = informe._extraer_probabilidades_red_general(an_stub)
    assert info_pres["p_1x"] == 0.99
    assert abs((r1x2["1"] + r1x2["X"]) - 0.995) < 1e-9

