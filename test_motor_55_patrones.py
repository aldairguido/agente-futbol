from __future__ import annotations
import os
import sys
import pandas as pd
try:
    import pytest
except ImportError:
    pytest = None

from motor_patrones import calcular_55_patrones_equipo, calcular_55_patrones_matchup
from motor_inferencia import (
    aplicar_reglas_matchup_55,
    calcular_cuadro_b_tendencias,
    calcular_cuadro_c_timeline,
    generar_reporte_xai_telegram,
)


def test_motor_55_patrones_completo():
    milan_45 = {
        "xg": 1.67, "posesion_pct": 56.60, "remates_totales": 12.40, "remates_puerta": 4.40,
        "grandes_ocasiones": 3.27, "grandes_ocasiones_falladas": 2.07, "corners": 3.53,
        "pases_total": 486.73, "pases_pct": 88.20, "tarjetas_amarillas": 1.73, "tarjetas_rojas": 0.13,
        "xgot": 1.65, "xg_juego_abierto": 1.29, "xg_balon_parado": 0.27, "xg_sin_penalti": 1.56,
        "remates_fuera": 5.20, "remates_bloqueados": 2.80, "remates_area": 9.13, "remates_fuera_area": 3.27,
        "al_palo": 0.20, "toques_area_rival": 24.93, "pases_entre_lineas": 32.73, "fueras_juego": 1.13,
        "tiros_libres": 12.53, "pases_largos_pct": 52.47, "pases_tercio_final_pct": 47.13, "centros_pct": 33.00,
        "xa_equipo": 1.08, "saques_banda": 16.87, "faltas": 10.27, "entradas_pct": 61.33,
        "duelos_ganados": 47.00, "duelos_suelo_pct": 50.27, "duelos_aereos_pct": 52.73, "despejes": 20.80,
        "intercepciones": 8.53, "bloqueos": 2.60, "recuperaciones": 47.40, "errores_remate": 1.00,
        "errores_gol": 0.20, "paradas": 3.67, "xgot_enfrentado": 1.70, "goles_evitados": 0.30, "saques_puerta": 6.47,
    }
    feren_45 = {
        "xg": 2.07, "posesion_pct": 53.67, "remates_totales": 14.73, "remates_puerta": 6.67,
        "grandes_ocasiones": 3.53, "grandes_ocasiones_falladas": 0.93, "corners": 6.27,
        "pases_total": 304.60, "pases_pct": 77.47, "tarjetas_amarillas": 2.40, "tarjetas_rojas": 0.07,
        "xgot": 2.14, "xg_juego_abierto": 1.83, "xg_balon_parado": 0.13, "xg_sin_penalti": 1.97,
        "remates_fuera": 5.73, "remates_bloqueados": 3.67, "remates_area": 11.40, "remates_fuera_area": 4.07,
        "al_palo": 0.53, "toques_area_rival": 27.00, "pases_entre_lineas": 10.40, "fueras_juego": 1.53,
        "tiros_libres": 13.33, "pases_largos_pct": 40.93, "pases_tercio_final_pct": 57.07, "centros_pct": 25.73,
        "xa_equipo": 1.25, "saques_banda": 20.60, "faltas": 15.33, "entradas_pct": 65.00,
        "duelos_ganados": 48.00, "duelos_suelo_pct": 48.93, "duelos_aereos_pct": 48.07, "despejes": 24.67,
        "intercepciones": 9.53, "bloqueos": 1.60, "recuperaciones": 17.00, "errores_remate": 0.20,
        "errores_gol": 0.13, "paradas": 3.00, "xgot_enfrentado": 1.10, "goles_evitados": 0.50, "saques_puerta": 8.33,
    }

    pat_loc = calcular_55_patrones_equipo(milan_45)
    pat_vis = calcular_55_patrones_equipo(feren_45)
    pat_loc, pat_vis = calcular_55_patrones_matchup(pat_loc, pat_vis)

    assert len(pat_loc) >= 55, "Faltan patrones en el Local"
    assert len(pat_vis) >= 55, "Faltan patrones en el Visita"
    assert "P55_Contraataque_Letal" in pat_loc, "Falta el Patrón 55"

    p_loc, p_emp, p_vis, alertas = aplicar_reglas_matchup_55(
        pat_loc, pat_vis, 0.493, 0.219, 0.288, "Milan", "Ferencváros"
    )
    assert round(p_loc + p_emp + p_vis, 2) == 1.00, "Las probabilidades no suman 1"
    assert len(alertas) >= 3, "El motor no generó suficientes alertas XAI"

    cb_loc = calcular_cuadro_b_tendencias([], "Milan", pat_loc)
    cb_vis = calcular_cuadro_b_tendencias([], "Ferencváros", pat_vis)
    cc_loc = calcular_cuadro_c_timeline([], "Milan", pat_loc)
    cc_vis = calcular_cuadro_c_timeline([], "Ferencváros", pat_vis)

    texto_xai = generar_reporte_xai_telegram(
        "Milan", "Ferencváros", pat_loc, pat_vis,
        {"local": p_loc, "empate": p_emp, "visita": p_vis},
        alertas, cb_loc, cb_vis, cc_loc, cc_vis
    )
    assert "TICKET DE ANÁLISIS PROFUNDO" in texto_xai
    assert "Patrón #01" in texto_xai and "Patrón #13" in texto_xai and "Patrón #26" in texto_xai


def test_sevilla_alaves_cuadro_b_hibrido():
    partidos_sevilla = [
        "01. 01/11/2026 Getafe (3) vs Sevilla (1)",
        "02. 28/10/2026 Sevilla (1) vs Osasuna (0)",
        "03. 25/10/2026 Atlético (3) vs Sevilla (0)",
        "04. 19/10/2026 Sevilla (1) vs Mallorca (3)",
        "05. 05/10/2026 Sevilla (4) vs Barcelona (1)",
        "06. 28/09/2026 Rayo (0) vs Sevilla (1)",
        "07. 23/09/2026 Sevilla (1) vs Villarreal (2)",
        "08. 20/09/2026 Alavés (1) vs Sevilla (2)",
        "09. 12/09/2026 Sevilla (2) vs Elche (2)",
        "10. 30/08/2026 Girona (0) vs Sevilla (2)",
        "11. 25/08/2026 Sevilla (1) vs Getafe (2)",
        "12. 17/08/2026 Athletic (3) vs Sevilla (2)",
        "13. 25/05/2026 Villarreal (4) vs Sevilla (2)",
        "14. 18/05/2026 Sevilla (0) vs Real Madrid (2)",
        "15. 13/05/2026 Sevilla (1) vs Las Palmas (0)",
    ]
    partidos_alaves = [
        "01. 27/10/2026 Elche (0) vs Deportivo A (2)",
        "02. 20/10/2026 Deportivo A (0) vs Valencia (0)",
        "03. 05/10/2026 Deportivo A (3) vs Elche (1)",
        "04. 27/09/2026 Mallorca (1) vs Deportivo A (0)",
        "05. 24/09/2026 Getafe (1) vs Deportivo A (1)",
        "06. 20/09/2026 Deportivo A (1) vs Sevilla (2)",
        "07. 13/09/2026 Athletic (0) vs Deportivo A (1)",
        "08. 30/08/2026 Deportivo A (1) vs Atlético (1)",
        "09. 22/08/2026 Real Betis (1) vs Deportivo A (0)",
        "10. 16/08/2026 Deportivo A (2) vs Levante (1)",
        "11. 24/05/2026 Deportivo A (1) vs Osasuna (1)",
        "12. 18/05/2026 Valladolid (0) vs Deportivo A (1)",
        "13. 14/05/2026 Deportivo A (1) vs Valencia (0)",
        "14. 11/05/2026 Athletic (1) vs Deportivo A (0)",
        "15. 03/05/2026 Deportivo A (0) vs Atlético (0)",
    ]
    cb_sev = calcular_cuadro_b_tendencias(partidos_sevilla, "Sevilla")
    cb_ala = calcular_cuadro_b_tendencias(partidos_alaves, "Deportivo Alavés")

    assert cb_sev["victorias"]["conteo"] == 6, f"Esperado 6 victorias Sevilla, obtenido {cb_sev['victorias']}"
    assert cb_sev["sin_derrotas"]["conteo"] == 7, f"Esperado 7 sin derrotas Sevilla, obtenido {cb_sev['sin_derrotas']}"
    assert cb_sev["btts"]["conteo"] == 9, f"Esperado 9 BTTS Sevilla, obtenido {cb_sev['btts']}"
    assert cb_sev["ganador_1t"]["conteo"] > 0

    assert cb_ala["victorias"]["conteo"] == 6, f"Esperado 6 victorias Alavés, obtenido {cb_ala['victorias']}"
    assert cb_ala["sin_derrotas"]["conteo"] == 11, f"Esperado 11 sin derrotas Alavés, obtenido {cb_ala['sin_derrotas']}"
    assert cb_ala["btts"]["conteo"] == 6, f"Esperado 6 BTTS Alavés, obtenido {cb_ala['btts']}"
    assert cb_ala["ganador_1t"]["conteo"] > 0


def test_especificacion_20_puntos_produccion():
    from fuentes_xg import (
        puntuar_candidato,
        validar_coherencia_nacional_15_partidos,
        _inferir_meta_rival_desde_texto,
    )
    from motor_patrones import (
        calcular_55_patrones_equipo,
        calcular_patrones_completos_partido,
        calcular_promedios_racha_60_40,
        validar_esquema_datos,
    )
    from motor_mercados import (
        es_seleccion_competitiva_ticket,
        seleccionar_top_5_jugadas_recomendadas,
        prob_under,
        prob_over,
        distribucion_conteo,
        Parametros,
    )

    # 1. Resolución de Identidad: San Lorenzo vs Platense (Argentina vs Honduras)
    meta_san_lorenzo = _inferir_meta_rival_desde_texto("San Lorenzo")
    assert meta_san_lorenzo is not None and meta_san_lorenzo.get("pais") == "argentina", f"Meta rival incorrecta: {meta_san_lorenzo}"

    cand_honduras = {
        "id": 1001, "name": "Platense FC",
        "leagueName": "Liga Nacional", "leagueId": 999, "ccode": "HON",
    }
    cand_argentina = {
        "id": 2002, "name": "CA Platense",
        "leagueName": "Liga Profesional", "leagueId": 112, "ccode": "ARG",
    }
    sc_hon = puntuar_candidato(cand_honduras, "Platense", rival_meta=meta_san_lorenzo)
    sc_arg = puntuar_candidato(cand_argentina, "Platense", rival_meta=meta_san_lorenzo)
    assert sc_arg > sc_hon, f"Platense ARG ({sc_arg}) debe superar ampliamente a HON ({sc_hon})"
    assert sc_hon[4] < 0, f"Platense HON debe tener penalización de país: {sc_hon}"

    partidos_hon = [
        {"torneo": "Liga Nacional", "rival": {"nombre": "Olancho FC"}},
        {"torneo": "Liga Nacional", "rival": {"nombre": "Juticalpa"}},
        {"torneo": "Liga Nacional", "rival": {"nombre": "Real España"}},
    ]
    partidos_arg = [
        {"torneo": "Liga Profesional", "rival": {"nombre": "River Plate"}},
        {"torneo": "Liga Profesional", "rival": {"nombre": "Boca Juniors"}},
        {"torneo": "Liga Profesional", "rival": {"nombre": "Racing Club"}},
    ]
    assert validar_coherencia_nacional_15_partidos(partidos_hon, "San Lorenzo") is False
    assert validar_coherencia_nacional_15_partidos(partidos_arg, "San Lorenzo") is True

    # Vitória vs Fluminense (Brasil)
    meta_flu = _inferir_meta_rival_desde_texto("Fluminense")
    meta_vit = _inferir_meta_rival_desde_texto("Vitória")
    assert meta_flu is not None and meta_flu.get("pais") == "brasil"
    assert meta_vit is not None and meta_vit.get("pais") == "brasil"

    # 2 & 3. Patrón #26 Dinámico y Patrón #13 Goles Evitados con signo algebraico
    datos_l = {"xg": 1.50, "xg_1t": 0.60, "xg_2t": 0.90, "posesion_pct": 52.0, "remates_totales": 13.0, "goles_favor": 1.4, "despejes": 18.0, "xgot_enfrentado": 1.15, "goles_contra": 1.40}
    datos_v = {"xg": 1.20, "xg_1t": 0.72, "xg_2t": 0.48, "posesion_pct": 48.0, "remates_totales": 10.0, "goles_favor": 1.1, "despejes": 22.0, "xgot_enfrentado": 1.35, "goles_contra": 1.10}
    P_L, P_V = calcular_patrones_completos_partido(datos_l, datos_v)
    assert abs(P_L["P26_Poder_Cierre_2T"] - 0.60) < 0.02, f"P26_L debe ser ~0.60, obtenido {P_L['P26_Poder_Cierre_2T']}"
    assert abs(P_V["P26_Poder_Cierre_2T"] - 0.40) < 0.02, f"P26_V debe ser ~0.40, obtenido {P_V['P26_Poder_Cierre_2T']}"
    assert P_L["P13_Portero_Goles_Evitados"] < 0 < P_V["P13_Portero_Goles_Evitados"]

    p1, px, p2, alertas_55 = aplicar_reglas_matchup_55(P_L, P_V, 0.42, 0.30, 0.28, "San Lorenzo", "Platense")
    alerta_13 = [a for a in alertas_55 if "Patrón #13" in a][0]
    assert "Mayor seguridad bajo palos: Platense" in alerta_13, f"Error signo P13: {alerta_13}"

    # Empate técnico en Patrón #26 (< 3%)
    P_L_emp = dict(P_L, P26_Poder_Cierre_2T=0.51)
    P_V_emp = dict(P_V, P26_Poder_Cierre_2T=0.50)
    _, _, _, alertas_emp = aplicar_reglas_matchup_55(P_L_emp, P_V_emp, 0.40, 0.32, 0.28, "Sevilla", "Deportivo Alavés")
    alerta_26 = [a for a in alertas_emp if "Patrón #26" in a][0]
    assert "Empate técnico en el poder de cierre del 2T" in alerta_26, f"Falta empate técnico P26: {alerta_26}"

    # 4 & 5. Prohibición de "-0.5 Goles" y Monotonicidad Poisson en Córneres
    sel_prohibida = {"mercado": "Total de goles", "evento": "under_0.5", "etiqueta": "Menos de 0.5 goles", "periodo": "FT", "linea": 0.5, "probabilidad": 0.12}
    assert es_seleccion_competitiva_ticket(sel_prohibida, "Sevilla", "Deportivo Alavés") is False

    dist_c = {k: p for k, p in enumerate(distribucion_conteo(9.5, parametros=Parametros(distribucion_conteos="poisson")))}
    u65 = prob_under(dist_c, 6.5)
    u85 = prob_under(dist_c, 8.5)
    u105 = prob_under(dist_c, 10.5)
    u125 = prob_under(dist_c, 12.5)
    assert u65 < u85 < u105 < u125, "Las probabilidades Under de córneres deben ser estrictamente crecientes"

    # 6, 10, 12, 14 & 20. Exclusión de "12 Sin Empate" en Sección 1 y Top 5 + Cuota Mínima y Stake Kelly
    sel_dc12 = {"mercado": "Doble oportunidad", "evento": "dc_12", "etiqueta": "12 (Sin Empate)", "periodo": "FT", "probabilidad": 0.85, "estado": "mantiene"}
    assert es_seleccion_competitiva_ticket(sel_dc12, "Osasuna", "Athletic Club", p_1x2=(0.45, 0.25, 0.30)) is False
    sels_test = [
        sel_dc12,
        {"mercado": "Doble oportunidad", "evento": "dc_1x", "etiqueta": "1X (San Lorenzo o empate)", "periodo": "FT", "probabilidad": 0.78, "estado": "mantiene"},
        {"mercado": "Total de goles", "evento": "under_2.5", "etiqueta": "Menos de 2.5 goles", "periodo": "FT", "linea": 2.5, "probabilidad": 0.76, "estado": "mantiene"},
        {"mercado": "Total de córneres", "evento": "corners_under_10.5", "etiqueta": "Menos de 10.5 córneres", "periodo": "FT", "linea": 10.5, "probabilidad": 0.74, "estado": "mantiene"},
        {"mercado": "Total de tarjetas", "evento": "amarillas_over_3.5", "etiqueta": "Más de 3.5 tarjetas amarillas", "periodo": "FT", "linea": 3.5, "probabilidad": 0.77, "estado": "mantiene"},
    ]
    top5 = seleccionar_top_5_jugadas_recomendadas(sels_test, motor=None, eq_l="San Lorenzo", eq_v="Platense")
    assert len(top5) >= 3
    assert all("Sin Empate" not in it["etiqueta"] for it in top5), f"12 Sin Empate debe quedar excluido del Top 5: {top5}"
    assert "cuota_minima" in top5[0] and top5[0]["cuota_minima"] > 1.05
    assert top5[0]["stake_sugerido_pct"] is None and top5[0]["ev_pct"] is None
    assert "Cuota Mín:" in top5[0]["badge_completo"]

    sels_con_cuotas = [dict(s, cuota_real=1.45) for s in sels_test]
    top5_cuotas = seleccionar_top_5_jugadas_recomendadas(sels_con_cuotas, motor=None, eq_l="San Lorenzo", eq_v="Platense")
    assert "stake_sugerido_pct" in top5_cuotas[0] and 1.0 <= top5_cuotas[0]["stake_sugerido_pct"] <= 8.0

    # 11 & 15. Ponderación 60/40 de rachas y Validación de Esquema
    racha_info = calcular_promedios_racha_60_40(
        [
            {"fecha": "2026-11-01", "local": "Vitória", "visita": "Rival1", "goles_local": 0, "goles_visita": 2, "xg": 0.8},
            {"fecha": "2026-10-25", "local": "Vitória", "visita": "Rival2", "goles_local": 1, "goles_visita": 2, "xg": 0.9},
            {"fecha": "2026-10-18", "local": "Vitória", "visita": "Rival3", "goles_local": 0, "goles_visita": 1, "xg": 0.7},
            {"fecha": "2026-10-10", "local": "Vitória", "visita": "Rival4", "goles_local": 2, "goles_visita": 0, "xg": 1.6},
            {"fecha": "2026-10-01", "local": "Vitória", "visita": "Rival5", "goles_local": 1, "goles_visita": 1, "xg": 1.2},
        ],
        "Vitória",
    )
    assert racha_info["en_crisis"] is True and racha_info["etiqueta_racha"] == "[EN CRISIS]"
    assert bool(validar_esquema_datos(datos_l)) is True

    # Verificación de Badges con EV% + Stake Kelly 25%, Nota de Calibración y Veredicto Táctico (4 conclusiones)
    from motor_inferencia import generar_veredicto_tactico_partido
    assert "badge_completo" in top5[0] and "Cuota Mín:" in top5[0]["badge_completo"]
    assert "EV +" in top5_cuotas[0]["badge_completo"] and "Stake:" in top5_cuotas[0]["badge_completo"]
    assert "nota_calibracion" in top5[0] and "Base Matriz Poisson:" in top5[0]["nota_calibracion"]
    veredicto_txt = generar_veredicto_tactico_partido(
        nombre_local="Nottingham Forest",
        nombre_visita="Manchester City",
        patrones_loc=P_L,
        patrones_vis=P_V,
        probs={"local": 0.069, "empate": 0.251, "visita": 0.680},
        cuadro_b_loc={"victorias": 4, "sin_derrotas": 7, "n_partidos": 15},
        cuadro_b_vis={"victorias": 8, "sin_derrotas": 12, "n_partidos": 15},
        cuadro_c_loc={"anotados": {"76'-90'": 4}, "recibidos": {"76'-90'": 6}},
        cuadro_c_vis={"anotados": {"76'-90'": 10}, "recibidos": {"76'-90'": 2}},
        ratings_loc={"Final_Attack_Strength": 53.7, "Pressing_Intensity_Score": 52.0},
        ratings_vis={"Final_Attack_Strength": 96.0, "Pressing_Intensity_Score": 64.9},
        top5_podio=top5,
    )
    assert "VEREDICTO TÁCTICO: ¿Qué va a pasar en este partido?" in veredicto_txt
    assert "1. El Manchester City va a ganar (o mínimo empatar):" in veredicto_txt
    assert "2. Cuidado con los goles de Manchester City al final:" in veredicto_txt
    assert "3. " in veredicto_txt and "4. " in veredicto_txt


def test_contexto_avanzado_y_5_ajustes_finos():
    """Verifica el módulo contexto_avanzado.py, validar_identidad_club y los 5 ajustes matemáticos finos."""
    from contexto_avanzado import evaluar_contexto_avanzado_partido
    from fuentes_xg import validar_identidad_club
    from motor_patrones import calcular_promedios_racha_60_40, calcular_patrones_completos_partido
    from motor_inferencia import aplicar_reglas_matchup_55

    # 1. Validación estricta de club sin homonimia
    cand_arg = {"nombre": "Club Atlético Platense", "liga": "Liga Profesional", "pais": "Argentina"}
    cand_hon = {"nombre": "Platense FC", "liga": "Liga Nacional", "pais": "Honduras"}
    assert validar_identidad_club(cand_arg, "Platense", pais_esperado="Argentina", liga_esperada="Liga Profesional") is True
    assert validar_identidad_club(cand_hon, "Platense", pais_esperado="Argentina", liga_esperada="Liga Profesional") is False

    # 2. Los 5 módulos de contexto_avanzado.py (Fatiga <= 3 días, Motivación, Bajas, Árbitro, Sharp Money)
    partidos_fatiga = [{"fecha": "2026-11-05"}]
    partidos_descanso = [{"fecha": "2026-10-31"}]
    ctx = evaluar_contexto_avanzado_partido(
        partidos_fatiga,
        partidos_descanso,
        fecha_partido_actual="2026-11-07",
        info_contexto_fs_fm={
            "motivacion_local": "zona_muerta",
            "motivacion_visita": "alta_urgencia",
            "bajas_local": {"sin_goleador_titular": True},
            "arbitro_promedio_tarjetas": 5.8,
            "cuota_apertura_favorito": 1.60,
            "cuota_actual_favorito": 1.82,
        },
    )
    assert ctx["dias_descanso_local"] == 2
    assert ctx["mult_lambda_local"] < 0.70  # Fatiga (0.85) * zona_muerta (0.90) * sin_goleador (0.80)
    assert ctx["mult_lambda_visita"] == 1.06
    assert ctx["mult_tarjetas"] == 1.18
    assert ctx["alerta_sharp_money"] is True and ctx["descartar_1x2_favorito_top1"] is True

    # 3. Sobre-rendimiento (> 1.30x xG en últimos 5) -> factor_regresion_media == 0.90
    pts_over = [
        {"fecha": f"2026-10-{20+i:02d}", "local": "Man City", "visita": "Rival", "goles_local": 3, "goles_visita": 0, "xg": 1.4}
        for i in range(5)
    ]
    racha_over = calcular_promedios_racha_60_40(pts_over, "Man City")
    assert racha_over["sobre_rendimiento"] is True and racha_over["factor_regresion_media"] == 0.90

    # 4. Patrón Matagigantes (Bloque Bajo Letal: pos_local < 36% + P07 >= 0.38 frente a favorito visitante)
    d_forest = {"xg": 1.35, "remates_totales": 9.0, "remates_puerta": 4.2, "goles_favor": 1.5, "posesion_pct": 31.0, "pases_largos_pct": 58.0}
    d_city = {"xg": 2.10, "remates_totales": 17.5, "remates_puerta": 6.5, "goles_favor": 2.2, "posesion_pct": 69.0, "pases_largos_pct": 65.0}
    P_for, P_cit = calcular_patrones_completos_partido(d_forest, d_city)
    p1_m, px_m, p2_m, al_m = aplicar_reglas_matchup_55(P_for, P_cit, 0.15, 0.22, 0.63, "Nottingham Forest", "Manchester City")
    assert p2_m < 0.63, f"El favorito visitante debe reducir su prob ante bloque bajo letal: {p2_m}"
    assert any("ALERTA MATAGIGANTES" in a for a in al_m), f"Falta alerta Matagigantes: {al_m}"


def test_5_escenarios_laliga_puntos_21_30():
    """
    Punto 30: Verifica con pytest los 5 escenarios reales de LaLiga (Puntos 21 al 30):
      1. Atlético vs Barcelona (Fortín Defensivo Local + Embudo Interior de Córneres)
      2. Elche vs Betis (Doble Freno de Mano a local débil Attack < 26 + Techo Caja Azul <= 0%)
      3. Real Sociedad vs Rayo (Prohibición de Inflación Dual <= +10%, Bloque Bajo Visitante y Sincronización HÁNDICAP/1X2/DC)
      4. Celta vs Levante (Multiplicador Asedio / Defensa Rota, Posesión Estéril vs Portero Figura y Reemplazo 1T +0.5 -> 2T +0.5)
      5. Villarreal vs Getafe (Autobús Visitante Pressing < 30 y Posesión < 42% -> lam_red_visita <= 0.60 y prohibición Getafe +0.5)
      Además verifica eliminación de badges duplicados en Sección 1, empate técnico en Patrón #01 y parser Cuadro B sin tildes.
    """
    from motor_patrones import aplicar_freno_de_mano_y_matchup_21_22, calcular_patrones_completos_partido
    from motor_inferencia import _es_mismo_equipo, aplicar_reglas_matchup_55
    from motor_mercados import seleccionar_top_5_jugadas_recomendadas
    from cuadro_especialista import _organizar_sec1_para_cuadro

    # 1. Atlético vs Barcelona: Amortiguador de Fortín Defensivo Local (Defense_Local >= 60.0 y lam_V > 2.05)
    lam_at, lam_bar, _, _ = aplicar_freno_de_mano_y_matchup_21_22(
        lam_base_local=1.45,
        lam_base_visita=2.30,
        lam_red_local=1.55,
        lam_red_visita=2.45,
        ratings_local={"Final_Attack_Strength": 68.0, "Final_Defense_Strength": 64.5},
        ratings_visita={"Final_Attack_Strength": 88.0, "Final_Defense_Strength": 58.0},
        posesion_proyectada_local=42.0,
        posesion_proyectada_visita=58.0,
    )
    assert lam_bar <= 2.20, f"El amortiguador de Fortín Local debe contener lam_visita de Barça (<= 2.20): {lam_bar}"

    # 2. Elche vs Betis: Local con Attack < 26.0 (22.7) -> delta_pct_local <= 0.0 y lam_red_local <= 0.90
    lam_elc, lam_bet, d_elc, d_bet = aplicar_freno_de_mano_y_matchup_21_22(
        lam_base_local=1.07,
        lam_base_visita=1.55,
        lam_red_local=1.52,
        lam_red_visita=1.70,
        ratings_local={"Final_Attack_Strength": 22.7, "Final_Defense_Strength": 40.0},
        ratings_visita={"Final_Attack_Strength": 58.0, "Final_Defense_Strength": 52.0},
        posesion_proyectada_local=45.0,
        posesion_proyectada_visita=55.0,
    )
    assert lam_elc <= 0.90 and d_elc <= 0.0, f"Elche (Attack 22.7) no puede inflarse en Caja Azul: lam={lam_elc}, delta={d_elc}%"

    # 3. Real Sociedad vs Rayo Vallecano: Ambos Attack < 40 (31.3 vs 27.4) -> suma Caja Azul <= +10.0% y Rayo lam <= 0.80
    lam_rso, lam_ray, d_rso, d_ray = aplicar_freno_de_mano_y_matchup_21_22(
        lam_base_local=1.18,
        lam_base_visita=1.09,
        lam_red_local=1.71,
        lam_red_visita=1.48,
        ratings_local={"Final_Attack_Strength": 31.3, "Final_Defense_Strength": 55.0},
        ratings_visita={"Final_Attack_Strength": 27.4, "Final_Defense_Strength": 48.0, "Pressing_Intensity_Score": 44.0},
        posesion_proyectada_local=56.5,
        posesion_proyectada_visita=43.5,
    )
    assert (d_rso + d_ray) <= 10.0, f"Prohibición de Inflación Dual violada en Real Sociedad vs Rayo: {d_rso}% + {d_ray}%"
    assert lam_ray <= 0.80, f"Rayo en bloque bajo (<45% pos y Attack <35) debe toparse en 0.80: {lam_ray}"

    # 4. Celta vs Levante: Multiplicador contra Defensa Pésima (Defense_V 24.4 < 30 y Pos_L 60.4% >= 60%)
    lam_cel, lam_lev, _, _ = aplicar_freno_de_mano_y_matchup_21_22(
        lam_base_local=1.35,
        lam_base_visita=0.95,
        lam_red_local=1.35,
        lam_red_visita=0.90,
        ratings_local={"Final_Attack_Strength": 52.0, "Final_Defense_Strength": 50.0},
        ratings_visita={"Final_Attack_Strength": 36.0, "Final_Defense_Strength": 24.4},
        posesion_proyectada_local=60.4,
        posesion_proyectada_visita=39.6,
    )
    assert lam_cel >= 1.85, f"Celta ante defensa rota (24.4) y asedio (60.4% pos) debe superar 1.85: {lam_cel}"

    # 5. Villarreal vs Getafe: Autobús visitante (Pressing 24.0 < 30 y Posesión 39.6% < 42%) -> lam_visita <= 0.60
    lam_vil, lam_get, _, _ = aplicar_freno_de_mano_y_matchup_21_22(
        lam_base_local=1.65,
        lam_base_visita=0.98,
        lam_red_local=1.80,
        lam_red_visita=1.22,
        ratings_local={"Final_Attack_Strength": 64.0, "Final_Defense_Strength": 55.0},
        ratings_visita={"Final_Attack_Strength": 28.5, "Final_Defense_Strength": 52.0, "Pressing_Intensity_Score": 24.0},
        posesion_proyectada_local=60.4,
        posesion_proyectada_visita=39.6,
    )
    assert lam_get <= 0.60, f"Getafe en modo autobús (Pressing 24.0, Pos 39.6%) debe toparse en 0.60: {lam_get}"

    # Gatekeeper Puntos 21c, 23 y 24b:
    # - Prohibido recomendar "Getafe +0.5 Goles" o "Goles 1T: +0.5 Goles" o "+10.5 Córneres"
    # - Reemplazo automático de 1T +0.5 por "Goles 2T: +0.5 Goles"
    class _DummyMotor:
        matriz_ft = [[0.08, 0.10, 0.08], [0.24, 0.10, 0.04], [0.20, 0.14, 0.02]]
        recursivo = {
            "lambda_local_recursivo": 1.80,
            "lambda_visita_recursivo": 0.58,
            "mu_local_casa": {"posesion_pct": 60.4, "despejes": 14.0},
            "mu_visita_fuera": {"posesion_pct": 39.6, "despejes": 24.0},
            "patrones_55_local": {"P26_Poder_Cierre_2T": 0.56, "posesion_pct": 60.4},
            "patrones_55_visita": {"P26_Poder_Cierre_2T": 0.58, "posesion_pct": 39.6},
            "ratings_local": {"Final_Attack_Strength": 64.0},
            "ratings_visita": {"Final_Attack_Strength": 28.5},
        }

    sels_gate = [
        {"mercado": "Doble oportunidad", "evento": "1x", "etiqueta": "1X (Villarreal o empate)", "periodo": "FT", "equipo": "Villarreal", "probabilidad": 0.78, "estado": "mantiene"},
        {"mercado": "Total de goles", "evento": "under_3.5", "etiqueta": "menos de 3.5 goles", "periodo": "FT", "linea": 3.5, "probabilidad": 0.78, "estado": "mantiene"},
        {"mercado": "Total de goles Getafe", "evento": "visitante_over_0.5", "etiqueta": "Getafe: más de 0.5 goles", "periodo": "FT", "equipo": "Getafe", "linea": 0.5, "probabilidad": 0.75, "estado": "mantiene"},
        {"mercado": "Gol en ambos tiempos", "evento": "gol_1t", "etiqueta": "Al menos un gol en el 1T", "periodo": "1T", "probabilidad": 0.76, "estado": "mantiene"},
        {"mercado": "Total de córneres", "evento": "corners_over_10.5", "etiqueta": "más de 10.5 córneres", "periodo": "FT", "linea": 10.5, "probabilidad": 0.74, "estado": "mantiene"},
        {"mercado": "Total de córneres", "evento": "corners_over_8.5", "etiqueta": "más de 8.5 córneres", "periodo": "FT", "linea": 8.5, "probabilidad": 0.79, "estado": "mantiene"},
        {"mercado": "Total de tarjetas", "evento": "amarillas_under_4.5", "etiqueta": "menos de 4.5 tarjetas amarillas", "periodo": "FT", "linea": 4.5, "probabilidad": 0.80, "estado": "mantiene"},
    ]
    top5_vg = seleccionar_top_5_jugadas_recomendadas(sels_gate, motor=_DummyMotor(), eq_l="Villarreal", eq_v="Getafe")
    etiquetas_top5 = [it["etiqueta"] for it in top5_vg]
    assert not any("Getafe" in e and "+0.5" in e for e in etiquetas_top5), f"Prohibido Getafe +0.5 Goles en Top 5: {etiquetas_top5}"
    assert not any("1T" in e and "+0.5" in e for e in etiquetas_top5), f"Prohibido 1T +0.5 Goles ante bloque bajo: {etiquetas_top5}"
    assert any("Goles 2T: +0.5 Goles" in e for e in etiquetas_top5), f"Debe reemplazar 1T +0.5 por Goles 2T: +0.5 Goles: {etiquetas_top5}"
    assert not any("+10.5 Córn" in e for e in etiquetas_top5), f"Prohibido +10.5 Córneres en Top 5: {etiquetas_top5}"

    # Puntos 26 y 27: Eliminación de badges duplicados en Sección 1 ("Al menos un gol en el 1T" == "+0.5 Goles (1T)")
    # y sincronización de HÁNDICAP / 1X2 / DC con el Puesto #1 del Top 5
    sels_sec1_dup = [
        {"mercado": "Total de goles", "evento": "over_0.5", "etiqueta": "más de 0.5 goles", "periodo": "1T", "linea": 0.5, "probabilidad": 0.85, "estado": "mantiene"},
        {"mercado": "Gol en ambos tiempos", "evento": "gol_1t", "etiqueta": "Al menos un gol en el 1T", "periodo": "1T", "probabilidad": 0.85, "estado": "mantiene"},
    ]
    sec1_org = _organizar_sec1_para_cuadro(None, sels_sec1_dup, "Real Sociedad", "Rayo Vallecano", top5_podio=top5_vg)
    cat_goles = [c for c in sec1_org if c["categoria"] == "GOLES PARTIDO"][0]
    assert len(cat_goles["pildoras"]) == 1 and cat_goles["pildoras"][0]["lbl"] == "+0.5 Goles (1T)", f"Duplicado en 1T no eliminado: {cat_goles['pildoras']}"
    cat_dc = [c for c in sec1_org if c["categoria"] == "HÁNDICAP / 1X2 / DC"][0]
    assert cat_dc["pildoras"][0]["lbl"] != "Sin líneas validadas >= 70%", f"Fila HÁNDICAP/1X2/DC desincronizada con Top 5: {cat_dc}"

    # Punto 28: Empate técnico en Patrón #01 cuando abs(P01_L - P01_V) < 0.015
    d_p01_l = {"xg": 1.21, "remates_totales": 11.0, "xgot": 1.21}
    d_p01_v = {"xg": 1.20, "remates_totales": 11.0, "xgot": 1.20}
    P1_L, P1_V = calcular_patrones_completos_partido(d_p01_l, d_p01_v)
    _, _, _, al_p01 = aplicar_reglas_matchup_55(P1_L, P1_V, 0.45, 0.30, 0.25, "Real Sociedad", "Rayo Vallecano")
    txt_al01 = [a for a in al_p01 if "Patrón #01" in a][0]
    assert "Paridad en calidad de tiro por remate" in txt_al01, f"Falta empate técnico en Patrón #01: {txt_al01}"

    # Punto 29: Parser Cuadro B sin tildes ni colisiones en clubes que llevan "Real"
    assert _es_mismo_equipo("Atlético de Madrid", "Atletico") is True
    assert _es_mismo_equipo("FC Barcelona", "Barça") is True
    assert _es_mismo_equipo("Deportivo Alavés", "Deportivo A") is True
    assert _es_mismo_equipo("Real Sociedad", "Real Betis") is False
    assert _es_mismo_equipo("Real Sociedad", "Real Socied") is True


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    test_motor_55_patrones_completo()
    test_sevilla_alaves_cuadro_b_hibrido()
    test_especificacion_20_puntos_produccion()
    test_contexto_avanzado_y_5_ajustes_finos()
    test_5_escenarios_laliga_puntos_21_30()
    print("OK: todos los tests de los 30 puntos y los 5 escenarios de LaLiga superados en verde.")
