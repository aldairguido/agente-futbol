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


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    test_motor_55_patrones_completo()
    test_sevilla_alaves_cuadro_b_hibrido()
    print("OK: test_motor_55_patrones_completo + test_sevilla_alaves_cuadro_b_hibrido superados en verde.")
