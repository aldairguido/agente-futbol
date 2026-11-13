import pytest
from unittest.mock import MagicMock
from motor_patrones import aplicar_freno_de_mano_y_matchup_21_22
from motor_inferencia import generar_veredicto_tactico_partido
from servicio_mercados import son_ligas_equivalentes, obtener_top5_y_seccion1_filtrados
from motor_mercados import es_seleccion_competitiva_ticket


def test_equivalencia_continental_ligas():
    # Regla 1.6 (Ronda 5+6): Regiones genéricas ('Europe', 'South America') NO sustituyen nombres reales de torneo
    assert son_ligas_equivalentes("UEFA Nations League", "Europe") is False
    assert son_ligas_equivalentes("UEFA nations league", "Europe") is False
    assert son_ligas_equivalentes("Copa América", "South America") is False

    # Torneos reales de selecciones con variantes de idioma/formato sí son equivalentes
    assert son_ligas_equivalentes("UEFA Nations League", "UEFA Nations League - League A") is True
    assert son_ligas_equivalentes("UEFA Euro", "Eurocopa") is True
    assert son_ligas_equivalentes("World Cup Qualification UEFA", "Eliminatorias UEFA") is True
    assert son_ligas_equivalentes("Eliminatorias CONMEBOL", "World Cup - Clasificación CONMEBOL") is True

    # Ligas domésticas idénticas o discrepantes
    assert son_ligas_equivalentes("Brasileirão Série A", "Campeonato Brasileiro Série A") is True
    assert son_ligas_equivalentes("LaLiga", "LaLiga EA Sports") is True
    assert son_ligas_equivalentes("LaLiga", "Premier League") is False


def test_gramatica_veredicto_seleccion_sin_articulo_el():
    # Con selección nacional favorita (Belgium)
    v_sel = generar_veredicto_tactico_partido(
        nombre_local="Turquía",
        nombre_visita="Belgium",
        patrones_loc={"posesion_pct": 50.0, "faltas": 11.0, "amarillas": 1.8},
        patrones_vis={"posesion_pct": 50.0, "faltas": 12.0, "amarillas": 1.9},
        probs={"local": 0.20, "empate": 0.25, "visita": 0.55, "es_seleccion": True},
        cuadro_b_loc={},
        cuadro_b_vis={},
        cuadro_c_loc={},
        cuadro_c_vis={},
    )
    assert "1. Belgium va a ganar" in v_sel
    assert "1. El Belgium va a ganar" not in v_sel

    # Con club (Real Madrid)
    v_club = generar_veredicto_tactico_partido(
        nombre_local="Osasuna",
        nombre_visita="Real Madrid",
        patrones_loc={"posesion_pct": 45.0, "faltas": 12.0, "amarillas": 2.0},
        patrones_vis={"posesion_pct": 55.0, "faltas": 10.0, "amarillas": 1.5},
        probs={"local": 0.15, "empate": 0.25, "visita": 0.60, "es_seleccion": False},
        cuadro_b_loc={},
        cuadro_b_vis={},
        cuadro_c_loc={},
        cuadro_c_vis={},
    )
    assert "1. El Real Madrid va a ganar" in v_club


def test_sincronizacion_tarjetas_veredicto_y_seccion1():
    # Caso 1: Sin líneas validadas de tarjetas >= 70%
    v_sin = generar_veredicto_tactico_partido(
        nombre_local="Turquía",
        nombre_visita="Belgium",
        patrones_loc={"posesion_pct": 50.0, "faltas": 11.0, "amarillas": 1.8},
        patrones_vis={"posesion_pct": 50.0, "faltas": 12.0, "amarillas": 1.9},
        probs={"local": 0.40, "empate": 0.30, "visita": 0.30},
        cuadro_b_loc={},
        cuadro_b_vis={},
        cuadro_c_loc={},
        cuadro_c_vis={},
        top5_podio=[],
        selecciones_sec1=[],
    )
    assert "Sin líneas de tarjetas validadas >= 70% en el Ticket" in v_sin

    # Caso 2: Con línea de tarjetas validada >= 70% en selecciones_sec1
    sec1_con_tarj = [
        {"mercado": "Total de tarjetas", "evento": "over_3.5", "etiqueta": "más de 3.5 tarjetas amarillas", "probabilidad": 0.74}
    ]
    v_con = generar_veredicto_tactico_partido(
        nombre_local="Turquía",
        nombre_visita="Belgium",
        patrones_loc={"posesion_pct": 50.0, "faltas": 11.0, "amarillas": 2.2},
        patrones_vis={"posesion_pct": 50.0, "faltas": 12.0, "amarillas": 2.1},
        probs={"local": 0.40, "empate": 0.30, "visita": 0.30},
        cuadro_b_loc={},
        cuadro_b_vis={},
        cuadro_c_loc={},
        cuadro_c_vis={},
        top5_podio=[],
        selecciones_sec1=sec1_con_tarj,
    )
    assert "El agente valida 'más de 3.5 tarjetas amarillas' (74%)" in v_con


def test_veto_under_tarjetas_por_asedio_tension():
    motor_mock = MagicMock()
    motor_mock.hay_tension_asedio_tarjetas = True

    sel_under = {
        "mercado": "Total de tarjetas",
        "evento": "amarillas_under_4.5",
        "etiqueta": "menos de 4.5 tarjetas amarillas",
        "periodo": "FT",
        "linea": 4.5,
        "probabilidad": 0.82,
    }
    sel_over = {
        "mercado": "Total de tarjetas",
        "evento": "amarillas_over_3.5",
        "etiqueta": "más de 3.5 tarjetas amarillas",
        "periodo": "FT",
        "linea": 3.5,
        "probabilidad": 0.75,
    }

    # Under vetado por asedio/tensión
    assert es_seleccion_competitiva_ticket(sel_under, motor=motor_mock) is False
    # Over permitido si cumple rango competitivo
    assert es_seleccion_competitiva_ticket(sel_over, motor=motor_mock) is True


def test_piso_contra_doble_penalizacion_lambda():
    # Caso Turkey vs Belgium: lam_base = 1.87, lam_red = 1.56 (-16.6%)
    # Si entra en_crisis_local y ratings paupérrimos, no puede bajar de:
    # max(0.90 * 1.56, 0.72 * 1.87) = max(1.40, 1.35) = 1.40
    lam_b_l = 1.87
    lam_red_l = 1.56
    lam_b_v = 1.80
    lam_red_v = 1.80

    lam_fin_l, lam_fin_v, delta_l, delta_v = aplicar_freno_de_mano_y_matchup_21_22(
        lam_base_local=lam_b_l,
        lam_base_visita=lam_b_v,
        lam_red_local=lam_red_l,
        lam_red_visita=lam_red_v,
        ratings_local={"Attack_Rating": 34.0, "Defense_Rating": 50.0},
        ratings_visita={"Attack_Rating": 65.0, "Defense_Rating": 55.0},
        en_crisis_local=True,
    )

    # El resultado final no puede ser recortado a 1.10; debe respetar el piso >= 1.40
    assert lam_fin_l >= 1.40
    assert lam_fin_l >= round(lam_red_l * 0.90, 2)
    assert lam_fin_l >= round(lam_b_l * 0.72, 2)


def test_respaldo_adn_sin_variables_fantasma():
    from motor_mercados import seleccionar_top_5_jugadas_recomendadas

    # Muestra sin cobertura de duelos y sin bloqueos (#38)
    motor_mock = MagicMock()
    motor_mock.xai_factores = {
        "mu_local_casa": {"xg": 1.2, "remates_totales": 12.0, "remates_puerta": 4.5, "remates_bloqueados": 2.5},
        "mu_visita_fuera": {"xg": 1.1, "remates_totales": 10.0, "remates_puerta": 3.5, "remates_bloqueados": 2.5},
    }
    motor_mock.recursivo = {}
    motor_mock.tasas = MagicMock(lambda_local_ft=1.3, lambda_visitante_ft=1.1, lambda_local_1t=0.6, lambda_visitante_1t=0.5, lambda_local_2t=0.7, lambda_visitante_2t=0.6)
    motor_mock.patrones_diesel_1t = False
    motor_mock.mercados_mitad_bloqueados = set()

    cands = [
        {"id_mercado": "total_goles", "mercado": "Total de goles", "evento": "over_0.5", "etiqueta": "más de 0.5 goles", "periodo": "2T", "linea": 0.5, "probabilidad": 0.85, "estado": "mantiene"},
        {"id_mercado": "corners", "mercado": "Total de córneres", "evento": "over_8.5", "etiqueta": "más de 8.5 córneres", "periodo": "FT", "linea": 8.5, "probabilidad": 0.76, "estado": "mantiene"},
    ]

    top5 = seleccionar_top_5_jugadas_recomendadas(cands, motor=motor_mock, eq_l="Turquía", eq_v="Bélgica")
    for item in top5:
        resp = item.get("respaldo_adn", "")
        if item.get("periodo") == "2T":
            # Debe usar tiros_2T y remates_puerta_2T, jamás caida_duelos
            assert "caida_duelos" not in resp
            assert "tiros_2T" in resp or "remates_puerta_2T" in resp
        if item.get("slot") == 4:
            # Debe usar remates_rechazados_rival, no bloqueos_rival con datos inventados
            assert "remates_rechazados_rival" in resp
