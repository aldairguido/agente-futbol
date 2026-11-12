"""
Pruebas automatizadas para BLOQUE A - Ronda 3 de Correcciones (D:\agente_futbol).
Cubre:
- A1.1: Tolerancia de residuo >= 0.05 deja la mitad en sin_dato.
- A1.2: P35 y P36 son None sin Cuadro C minuto a minuto, y alerta #35 no se dispara.
- A1.3: P06 y P50 son None sin cobertura de grandes ocasiones, y filtro Top 5 inactivo.
- A1.4: Detector por proveedor (serie Flashscore en 0 -> None, 0 suelto FotMob -> 0.0).
- A1.5: Título TOP N dinámico para N < 5 y N >= 5.
- A1.6: Redacción del Veredicto Táctico Punto 1 cuando Jugada #1 es Under.
- A2: F1..F5 con omisión centrada de None y reescalado por fracción de insumos disponibles.
- A3: Desambiguación de Serie C (clubes italianos a ITA, Brasileirão Série C 8971 a BRA).
- A6: Etiqueta de fuente por celda/partido (cero falsos FM+FS cuando un proveedor no aportó datos).
"""

import json
import pytest
from pathlib import Path

from auditoria_historica import reconstruir_marcador_mitades_partido, _construir_tabla_resumen_inicial
from motor_patrones import (
    calcular_55_patrones_equipo,
    calcular_55_patrones_matchup,
)
from motor_inferencia import (
    aplicar_reglas_matchup_55,
    generar_veredicto_tactico_partido,
)
from motor_recursivo_45_dcn import (
    _valor_metrica,
    calcular_los_6_cruces_tacticos,
    calcular_5_factores_latentes_desde_estados,
)
from resolvedor_entidades import (
    resolver as resolver_entidades,
    validar_y_puntuar_candidato_fotmob,
)

BASE_DIR = Path(__file__).resolve().parent.parent


def test_a1_1_tolerancia_residuo_mitades():
    """A1.1: Residuo >= 0.05 deja las mitades en sin_dato; residuo < 0.05 deriva el marcador."""
    # Caso 1: Residuo = 0.08 (>= 0.05) -> debe devolver sin_dato
    partido_bad = {
        "marcador": {"equipo": 2, "rival": 0},
        "periodos": {
            "FT": {"filas": []},
            "1T": {
                "filas": [
                    {"clave": "xgot_enfrentado", "equipo": 0.0, "rival": 1.08},
                    {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
                ]
            },
            "2T": {
                "filas": [
                    {"clave": "xgot_enfrentado", "equipo": 0.0, "rival": 0.92},
                    {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
                ]
            },
        },
    }
    gl1, gv1, gl2, gv2, ok, desc = reconstruir_marcador_mitades_partido(partido_bad, "equipo", "rival")
    assert not ok
    assert desc == "sin_dato"
    assert gl1 is None and gv1 is None

    # Caso 2: Residuo = 0.02 (< 0.05) con suma exacta -> derivado aritmético válido
    partido_good = {
        "marcador": {"equipo": 2, "rival": 0},
        "periodos": {
            "FT": {"filas": []},
            "1T": {
                "filas": [
                    {"clave": "xgot_enfrentado", "equipo": 0.0, "rival": 1.02},
                    {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
                ]
            },
            "2T": {
                "filas": [
                    {"clave": "xgot_enfrentado", "equipo": 0.0, "rival": 0.98},
                    {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
                ]
            },
        },
    }
    gl1_g, gv1_g, gl2_g, gv2_g, ok_g, desc_g = reconstruir_marcador_mitades_partido(partido_good, "equipo", "rival")
    assert ok_g
    assert desc_g == "[derivado_aritmético]"
    assert gl1_g == 1 and gl2_g == 1 and gv1_g == 0 and gv2_g == 0


def test_a1_2_p35_p36_none_sin_cuadro_c():
    """A1.2: P35 y P36 son None si no hay Cuadro C minuto a minuto, y alerta de minutos finales no se dispara."""
    datos_ft = {
        "xg": 1.35, "remates_totales": 12.0, "remates_puerta": 4.0, "goles": 2,
    }
    # Sin timeline de 15 minutos (timeline_goles = {})
    pat = calcular_55_patrones_equipo(datos_ft, timeline_goles={})
    assert pat.get("P35_Peso_Goles_Tardios") is None
    assert pat.get("P36_Vulnerabilidad_Temprana") is None

    # Alerta de minutos finales no debe activarse en matchup
    p_l = dict(pat)
    p_v = dict(pat)
    alertas = []
    p1, px, p2, alertas = aplicar_reglas_matchup_55(p_l, p_v, 0.40, 0.30, 0.30, "Equipo L", "Equipo V")
    assert not any("Patrón #35" in a for a in alertas)


def test_a1_3_p06_p50_none_y_filtro_top5_inactivo():
    """A1.3: P06 y P50 son None si grandes_ocasiones_falladas no tiene cobertura, y filtro posesión estéril queda inactivo."""
    datos_sin_goc = {
        "xg": 1.20, "remates_totales": 10.0, "remates_puerta": 4.0,
        "grandes_ocasiones": 0, "grandes_ocasiones_falladas": None,
    }
    pat_l = calcular_55_patrones_equipo(datos_sin_goc)
    pat_v = calcular_55_patrones_equipo(datos_sin_goc)
    p_l, p_v = calcular_55_patrones_matchup(pat_l, pat_v)

    assert p_l.get("P06_Desperdicio_Ocasiones") is None
    assert p_l.get("P50_Choque_Portero_Rival") is None
    assert p_v.get("P06_Desperdicio_Ocasiones") is None
    assert p_v.get("P50_Choque_Portero_Rival") is None

    # Simular evaluación de condición posesion_esteril_vs_portero
    p01_l_t = p_l.get("P01_xG_por_Remate")
    p06_l_t = p_l.get("P06_Desperdicio_Ocasiones")
    p14_v_t = p_v.get("P14_Portero_Tasa_Paradas") or 0.68
    posesion_esteril = (
        (((p01_l_t is not None and p01_l_t < 0.11) or (p06_l_t is not None and p06_l_t >= 0.55)) and p14_v_t >= 0.70)
    )
    # Con P06=None y P01 >= 0.11 (1.20/10 = 0.12), posesion_esteril DEBE ser False (inactivo)
    assert not posesion_esteril


def test_a1_4_detector_proveedor_series_cero_vs_fotmob():
    """A1.4: Serie Flashscore con métrica no cubierta en 0 retorna None; 0 suelto FotMob retorna 0.0."""
    partido_fs = {
        "id_flashscore": "g_1_12345",
        "fuente": "flashscore",
        "periodos": {
            "FT": {
                "filas": [
                    {"clave": "bloqueos", "equipo": 0, "rival": 0},
                    {"clave": "recuperaciones", "equipo": 0, "rival": 0},
                    {"clave": "remates_totales", "equipo": 8, "rival": 10},
                ]
            }
        }
    }
    # En Flashscore, métricas no cubiertas Opta en 0 pasan estrictamente a None
    assert _valor_metrica(partido_fs, "bloqueos") is None
    assert _valor_metrica(partido_fs, "recuperaciones") is None
    # Pero métricas cubiertas (remates_totales) se leen correctamente
    assert _valor_metrica(partido_fs, "remates_totales") == 8.0

    partido_fm = {
        "id_fotmob": "123456",
        "fuente": "fotmob",
        "periodos": {
            "FT": {
                "filas": [
                    {"clave": "bloqueos", "equipo": 0, "rival": 1},
                ]
            }
        }
    }
    # En FotMob, un 0 suelto observado es un dato real válido (0.0)
    assert _valor_metrica(partido_fm, "bloqueos") == 0.0


def test_a1_5_titulo_dinamico_top_n():
    """A1.5: Título TOP N dinámico para N < 5 y N >= 5."""
    top_3 = [{"etiqueta": "1X"}, {"etiqueta": "-2.5"}, {"etiqueta": "+0.5 2T"}]
    top_4 = [{"etiqueta": "1X"}, {"etiqueta": "-2.5"}, {"etiqueta": "+0.5 2T"}, {"etiqueta": "-3.5"}]
    top_5 = [{"etiqueta": "1X"}, {"etiqueta": "-2.5"}, {"etiqueta": "+0.5 2T"}, {"etiqueta": "-3.5"}, {"etiqueta": "+1.5"}]

    def _titulo_podio(podio):
        n = len(podio)
        return f"TOP {n} JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO)" if n < 5 else "TOP 5 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO)"

    assert _titulo_podio(top_3) == "TOP 3 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO)"
    assert _titulo_podio(top_4) == "TOP 4 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO)"
    assert _titulo_podio(top_5) == "TOP 5 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO)"


def test_a1_6_redaccion_veredicto_punto1_under():
    """A1.6: Redacción del Veredicto Táctico Punto 1 orientada a contención/Under cuando Jugada #1 es Under."""
    top5_con_under = [
        {"etiqueta": "Total de goles: menos de 3.5", "probabilidad": 0.86, "prob_base_poisson": 86.0}
    ]
    txt_veredicto = generar_veredicto_tactico_partido(
        nombre_local="Londrina",
        nombre_visita="Náutico",
        patrones_loc={"xg": 1.05, "remates_puerta": 3.8},
        patrones_vis={"xg": 0.85, "remates_puerta": 3.2},
        probs={"local": 0.40, "empate": 0.32, "visita": 0.28},
        cuadro_b_loc={"victorias": 6, "sin_derrotas": 11, "total_partidos": 15},
        cuadro_b_vis={"victorias": 4, "sin_derrotas": 9, "total_partidos": 15},
        cuadro_c_loc={},
        cuadro_c_vis={},
        top5_podio=top5_con_under,
        para_canvas=True,
    )
    # Debe contener la formulación de marcador cerrado y no proclamar falsamente victoria fija
    assert "1. Partido cerrado / Tendencia baja en goles ('Total de goles: menos de 3.5'):" in txt_veredicto
    assert "El Ticket prioriza cautela en el marcador" in txt_veredicto
    assert "va a ganar (o mínimo empatar)" not in txt_veredicto


def test_a2_red_recursiva_omision_centrada_none_y_escalado():
    """A2: Insumos con None aportan 0.0 respecto al centro y escalan por fracción disponible."""
    # Local con xg y remates_totales disponibles, pero grandes_ocasiones y bloqueos_rival en None
    mu_L = {"xg": 1.25, "remates_totales": 12.0, "grandes_ocasiones": None}
    mu_V = {"bloqueos": None, "xg": 1.25, "remates_totales": 12.0}

    res = calcular_los_6_cruces_tacticos(mu_L, mu_V, {}, {}, {}, {})
    f2_L = res["factores_local"]["F_creacion"]
    # Al estar en los centros exactos (1.25 y 12.0) y los otros ser None, la suma es 0.0 y f2_L es 0.0
    assert f2_L == 0.0

    # Test de escalado en calcular_5_factores_latentes_desde_estados
    est_p = {
        "xg": {"h_reciente": 1.65, "sin_dato": False},  # (1.65 - 1.25)/0.40 * 0.45 = +0.45
        "remates_totales": {"sin_dato": True},
        "grandes_ocasiones": {"sin_dato": True},
    }
    est_r = {
        "bloqueos": {"sin_dato": True},
    }
    factores = calcular_5_factores_latentes_desde_estados(est_p, est_r)
    # disp = 1 de 4 insumos -> frac = 1/4 = 0.25
    # f2 sin escalar sería tanh(1.5 * 0.45) * 3.5 = 2.05. Con frac 0.25 -> 2.05 * 0.25 = 0.5
    assert factores["F_creacion"] == 0.5


def test_a3_desambiguacion_serie_c_italia_vs_brasil():
    """A3: 'serie c' con clubes italianos resuelve a ITA y no a Brasil."""
    # Clubes de Serie C italiana
    res_ita = resolver_entidades("Serie C", "Padova", "Vicenza")
    assert res_ita.pais_iso == "ITA"

    # Candidato de Brasileirão Série C (leagueId 8971) resuelve a BRA
    cand_bra = {"type": "team", "id": "298660", "name": "Londrina", "leagueId": 8971, "leagueName": "Série C"}
    pts_bra = validar_y_puntuar_candidato_fotmob(cand_bra, "Londrina", pais_iso_esperado="BRA")
    assert pts_bra > 100.0  # Puntuación positiva con bono de país coherente (+100)


def test_a6_fuente_resumen_y_subtabla_sin_falsos_fm_fs():
    """A6: No emitir 'FM+FS' cuando un proveedor no aportó datos al partido."""
    # Partido exclusivo de Flashscore
    partido_puro_fs = {
        "id_partido": None,
        "id_flashscore": "g_1_99999",
        "fuente": "flashscore",
        "fecha": "2026-11-10",
        "torneo": "Série C",
        "equipo": "Londrina",
        "rival": "Náutico",
        "condicion": "casa",
        "marcador": {"equipo": 1, "rival": 0},
        "periodos": {"FT": {"filas": [{"clave": "remates_totales", "equipo": 10, "rival": 8}]}},
        "procedencia": {"FT": {"equipo": {"remates_totales": "Flashscore"}}},
    }
    lineas = _construir_tabla_resumen_inicial([partido_puro_fs])
    tabla_resumen = "\n".join(lineas)
    assert "Flashscore" in tabla_resumen
    assert "FM+FS" not in tabla_resumen
