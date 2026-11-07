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

    # 6, 10, 12, 14 & 20. Exclusión de "12 Sin Empate" como #1 + Cuota Mínima y Stake Kelly
    sels_test = [
        {"mercado": "Doble oportunidad", "evento": "dc_12", "etiqueta": "12 (Sin Empate)", "periodo": "FT", "probabilidad": 0.75, "estado": "mantiene"},
        {"mercado": "Doble oportunidad", "evento": "dc_1x", "etiqueta": "1X (San Lorenzo o empate)", "periodo": "FT", "probabilidad": 0.78, "estado": "mantiene"},
        {"mercado": "Total de goles", "evento": "under_2.5", "etiqueta": "Menos de 2.5 goles", "periodo": "FT", "linea": 2.5, "probabilidad": 0.76, "estado": "mantiene"},
        {"mercado": "Total de córneres", "evento": "corners_under_10.5", "etiqueta": "Menos de 10.5 córneres", "periodo": "FT", "linea": 10.5, "probabilidad": 0.74, "estado": "mantiene"},
        {"mercado": "Total de tarjetas", "evento": "amarillas_over_3.5", "etiqueta": "Más de 3.5 tarjetas amarillas", "periodo": "FT", "linea": 3.5, "probabilidad": 0.77, "estado": "mantiene"},
    ]
    top5 = seleccionar_top_5_jugadas_recomendadas(sels_test, motor=None, eq_l="San Lorenzo", eq_v="Platense")
    assert len(top5) >= 3
    assert "Sin Empate" not in top5[0]["etiqueta"], f"La jugada #1 jamás puede ser 12 Sin Empate: {top5[0]}"
    assert "cuota_minima" in top5[0] and top5[0]["cuota_minima"] > 1.05
    assert "stake_sugerido_pct" in top5[0] and 1.0 <= top5[0]["stake_sugerido_pct"] <= 8.0

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


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    test_motor_55_patrones_completo()
    test_sevilla_alaves_cuadro_b_hibrido()
    test_especificacion_20_puntos_produccion()
    print("OK: todos los tests de los 20 puntos superados en verde.")
