from __future__ import annotations
from typing import Any, Dict, Optional, Tuple
import math
import numpy as np
import pandas as pd


def _val(d: Dict[str, Any], *claves: str, default: float = 0.01) -> float:
    """Extrae un valor numérico seguro desde un dict o Serie de las 45 variables."""
    for k in claves:
        if k in d and d[k] is not None:
            nodo = d[k]
            if isinstance(nodo, dict):
                v = nodo.get("promedio", nodo.get("valor", default))
            else:
                v = nodo
            try:
                f = float(v)
                if not math.isnan(f):
                    return max(0.01, f) if abs(f) < 1e-9 else f
            except (TypeError, ValueError):
                continue
    return default


def calcular_55_patrones_equipo(
    datos_equipo: Any,
    metricas_1t: Optional[Dict[str, Any]] = None,
    metricas_2t: Optional[Dict[str, Any]] = None,
    timeline_goles: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """
    Acepta un DataFrame de 15 partidos o un diccionario con las 45 variables del equipo.
    Calcula los 48 patrones base (P01 a P48) sin divisiones por cero.
    """
    if isinstance(datos_equipo, pd.DataFrame):
        df_num = datos_equipo.replace(0, 0.01)
        m = df_num.mean(numeric_only=True).to_dict()
    elif isinstance(datos_equipo, dict):
        m = datos_equipo.get("metricas", datos_equipo)
    else:
        m = {}

    m1 = metricas_1t or {}
    m2 = metricas_2t or {}
    tl = timeline_goles or {}

    xg = max(0.05, _val(m, "xg", "xg_favor", default=1.35))
    xgot = max(0.05, _val(m, "xgot", "xgot_favor", default=xg))
    npxg = max(0.05, _val(m, "xg_sin_penalti", "npxg", default=xg * 0.90))
    xg_bp = max(0.01, _val(m, "xg_balon_parado", default=xg * 0.15))
    xg_abierto = max(0.05, _val(m, "xg_juego_abierto", default=max(0.05, xg - xg_bp)))

    goles = max(0.05, _val(m, "goles", "goles_favor", "goles_favor_est", default=xg))
    goles_c = max(0.05, _val(m, "goles_concedidos", "goles_contra", "goles_contra_real", default=1.20))

    posesion = max(15.0, min(85.0, _val(m, "posesion_pct", default=50.0)))
    posesion_rival = max(15.0, 100.0 - posesion)

    rem_tot = max(1.0, _val(m, "remates_totales", default=12.0))
    rem_pue = max(0.5, _val(m, "remates_puerta", default=4.2))
    rem_fue = max(0.5, _val(m, "remates_fuera", default=5.0))
    rem_bloq = max(0.1, _val(m, "remates_bloqueados", "remates_rechazados", default=2.8))
    rem_area = max(0.5, _val(m, "remates_area", "remates_dentro_area", default=8.5))
    rem_f_area = max(0.5, _val(m, "remates_fuera_area", default=3.5))

    g_oc = max(0.5, _val(m, "grandes_ocasiones", default=2.4))
    g_oc_fall = max(0.0, _val(m, "grandes_ocasiones_falladas", default=1.3))
    toques_area = max(1.0, _val(m, "toques_area_rival", default=22.0))

    pases_tot = max(50.0, _val(m, "pases_total", "pases_totales", default=420.0))
    pases_prec_pct = max(40.0, _val(m, "pases_pct", "pases_precision_pct", "precision_pases_pct", default=82.0))
    pases_lineas = max(1.0, _val(m, "pases_entre_lineas", default=20.0))
    pases_largos_pct = max(10.0, _val(m, "pases_largos_pct", default=48.0))
    pases_tercio_pct = max(15.0, _val(m, "pases_tercio_final_pct", default=52.0))
    centros_pct = max(5.0, _val(m, "centros_pct", "centros_precision_pct", "cruces_pct", default=28.0))
    xa = max(0.05, _val(m, "xa_equipo", "xa", default=1.00))

    corners = max(0.5, _val(m, "corners", "corners_favor", "saques_esquina", default=4.8))
    corners_c = max(0.5, _val(m, "corners_concedidos", "corners_contra", default=4.5))
    tiros_libres = max(1.0, _val(m, "tiros_libres", default=12.0))
    fueras_juego = max(0.1, _val(m, "fueras_juego", default=1.3))
    saques_banda = max(1.0, _val(m, "saques_banda", default=17.0))

    faltas = max(1.0, _val(m, "faltas", "faltas_cometidas", default=11.5))
    faltas_rec = max(1.0, _val(m, "faltas_recibidas", default=11.5))
    amarillas = max(0.1, _val(m, "tarjetas_amarillas", default=1.8))

    entradas_pct = max(20.0, _val(m, "entradas_pct", default=60.0))
    duelos_suelo_pct = max(20.0, _val(m, "duelos_suelo_pct", default=50.0))
    duelos_aereos_pct = max(20.0, _val(m, "duelos_aereos_pct", default=50.0))

    despejes = max(1.0, _val(m, "despejes", default=20.0))
    intercepciones = max(1.0, _val(m, "intercepciones", default=8.5))
    bloqueos = max(0.2, _val(m, "bloqueos", default=2.4))
    recuperaciones = max(5.0, _val(m, "recuperaciones", default=42.0))
    err_remate = max(0.0, _val(m, "errores_remate", "errores_tiro", default=0.35))
    err_gol = max(0.0, _val(m, "errores_gol", default=0.15))

    paradas = max(0.2, _val(m, "paradas", "paradas_portero", default=3.1))
    xgot_enf = max(0.1, _val(m, "xgot_enfrentado", "xgot_contra", default=1.30))
    goles_evit = _val(m, "goles_evitados", default=round(xgot_enf - goles_c, 2))

    rem_puerta_rec = max(0.5, paradas + goles_c)
    rem_area_rec = max(1.0, _val(m, "remates_dentro_area_recibidos", default=rem_puerta_rec * 1.85))

    xg_1t = max(0.05, _val(m1, "xg", "xg_1t", default=_val(m, "xg_1t", default=xg * 0.45)))
    xg_2t = max(0.05, _val(m2, "xg", "xg_2t", default=_val(m, "xg_2t", default=max(0.05, xg - xg_1t))))
    pos_1t = max(15.0, _val(m1, "posesion_pct", "posesion_1t", default=_val(m, "posesion_1t", default=posesion)))
    pos_2t = max(15.0, _val(m2, "posesion_pct", "posesion_2t", default=_val(m, "posesion_2t", default=posesion)))
    goles_1t = max(0.05, _val(m1, "goles", "goles_1t", default=_val(m, "goles_1t", default=goles * 0.44)))
    goles_2t = max(0.05, _val(m2, "goles", "goles_2t", default=_val(m, "goles_2t", default=max(0.05, goles - goles_1t))))
    rem_pue_1t = max(0.3, _val(m1, "remates_puerta", "remates_puerta_1t", default=rem_pue * 0.45))
    rem_pue_2t = max(0.3, _val(m2, "remates_puerta", "remates_puerta_2t", default=rem_pue * 0.55))
    pases_prec_1t = max(40.0, _val(m1, "pases_pct", "pases_precision_1t", default=pases_prec_pct + 1.0))
    pases_prec_2t = max(40.0, _val(m2, "pases_pct", "pases_precision_2t", default=pases_prec_pct - 1.0))
    rem_tot_1t = max(0.5, _val(m1, "remates_totales", "remates_totales_1t", default=rem_tot * 0.45))
    rem_tot_2t = max(0.5, _val(m2, "remates_totales", "remates_totales_2t", default=rem_tot * 0.55))
    rem_fue_2t = max(0.2, _val(m2, "remates_fuera_area", "remates_fuera_2t", default=rem_f_area * 0.55))
    rem_area_2t = max(0.5, _val(m2, "remates_area", "remates_dentro_area_2t", default=rem_area * 0.55))
    corners_1t = max(0.3, _val(m1, "corners", "corners_1t", default=corners * 0.45))
    corners_2t = max(0.3, _val(m2, "corners", "corners_2t", default=corners * 0.55))

    # Proporción de goles en últimos 15' y primeros 15' desde el Cuadro C (si está disponible)
    if "76_90" in tl and isinstance(tl["76_90"], dict):
        tot_anot = max(1, sum(int(v.get("anotados", 0)) for v in tl.values() if isinstance(v, dict)))
        tot_rec = max(1, sum(int(v.get("recibidos", 0)) for v in tl.values() if isinstance(v, dict)))
        prop_ult_15 = float(tl["76_90"].get("anotados", 0)) / tot_anot
        prop_rec_0_15 = float(tl.get("00_15", {}).get("recibidos", 0)) / tot_rec
    else:
        prop_ult_15 = float(tl.get("prop_anotados_76_90", 0.26))
        prop_rec_0_15 = float(tl.get("prop_recibidos_00_15", 0.12))

    p: Dict[str, float] = {}

    # A. EFICIENCIA OFENSIVA (P01 - P12)
    p["P01_xG_por_Remate"] = round(xg / rem_tot, 4)
    p["P02_xG_Area_por_Remate"] = round(xg_abierto / rem_area, 4)
    p["P03_Calidad_xGOT_vs_xG"] = round(xgot / xg, 4)
    p["P04_Eficiencia_npxG"] = round(npxg / rem_tot, 4)
    p["P05_Conversion_Real"] = round(goles / rem_pue, 4)
    p["P06_Desperdicio_Ocasiones"] = round(g_oc_fall / g_oc, 4)
    p["P07_Precision_Disparo"] = round(rem_pue / rem_tot, 4)
    p["P08_Tiros_Bloqueados_Ratio"] = round(rem_bloq / rem_tot, 4)
    p["P09_Dependencia_Balon_Parado"] = round(xg_bp / xg, 4)
    p["P10_Creatividad_xA_Pases"] = round(xa / pases_lineas, 4)
    p["P11_Peligro_Area_vs_Fuera"] = round(rem_area / rem_f_area, 4)
    p["P12_Precision_Centros"] = round(centros_pct / 100.0, 4)

    # B. RESISTENCIA DEFENSIVA Y PORTERÍA (P13 - P24)
    p["P13_Portero_Goles_Evitados"] = round(goles_evit, 4)
    p["P14_Portero_Tasa_Paradas"] = round(paradas / rem_puerta_rec, 4)
    p["P15_Muro_Asedio"] = round((despejes + intercepciones) / posesion_rival, 4)
    p["P16_Fuerza_Aerea_Defensiva"] = round((duelos_aereos_pct / 100.0) * despejes, 4)
    p["P17_Precision_Entradas"] = round(entradas_pct / 100.0, 4)
    p["P18_Disciplina_Faltas"] = round(faltas / max(5.0, entradas_pct * 0.25), 4)
    p["P19_Errores_Fatales"] = round(err_remate + err_gol, 4)
    p["P20_Bloqueos_Area"] = round(bloqueos / rem_area_rec, 4)
    p["P21_Densidad_Recuperaciones"] = round(recuperaciones / posesion_rival, 4)
    p["P22_Vulnerabilidad_Corners"] = round(despejes / corners_c, 4)
    p["P23_Ratio_Duelos_Suelo"] = round(duelos_suelo_pct / 100.0, 4)
    p["P24_Ratio_Duelos_Aereos"] = round(duelos_aereos_pct / 100.0, 4)

    # C. RITMO, TEMPO Y FATIGA 1T vs 2T (P25 - P36)
    p["P25_Explosividad_1T"] = round(xg_1t / xg, 4)
    p["P26_Poder_Cierre_2T"] = round(xg_2t / xg, 4)
    p["P27_Caida_Posesion_2T"] = round(pos_1t - pos_2t, 4)
    p["P28_Aceleracion_2T"] = round(pos_2t - pos_1t, 4)
    p["P29_Letalidad_1T"] = round(goles_1t / rem_pue_1t, 4)
    p["P30_Letalidad_2T"] = round(goles_2t / rem_pue_2t, 4)
    p["P31_Precision_Bajo_Fatiga"] = round(pases_prec_2t / pases_prec_1t, 4)
    p["P32_Intensidad_Remates_2T"] = round(rem_tot_2t / rem_tot_1t, 4)
    p["P33_Desesperacion_2T"] = round(rem_fue_2t / rem_area_2t, 4)
    p["P34_Asedio_Corners_2T"] = round(corners_2t / corners_1t, 4)
    p["P35_Peso_Goles_Tardios"] = round(prop_ult_15, 4)
    p["P36_Vulnerabilidad_Temprana"] = round(prop_rec_0_15, 4)

    # D. ASEDIO Y CONTEXTO (P37 - P48)
    p["P37_Peligro_por_Corner"] = round(xg_bp / corners, 4)
    p["P38_Densidad_Pases"] = round(pases_tot / posesion, 4)
    p["P39_Estilo_Directo"] = round(pases_largos_pct / 100.0, 4)
    p["P40_Rompe_Lineas"] = round(pases_lineas / pases_tot, 4)
    p["P41_Verticalidad"] = round(pases_tercio_pct / 100.0, 4)
    p["P42_Dependencia_Centros"] = round((centros_pct / 100.0) * 15.0 / toques_area, 4)
    p["P43_Agresividad_Defensiva"] = round(amarillas / faltas, 4)
    p["P44_Pressing_Alto"] = round((intercepciones + recuperaciones) / max(15.0, posesion_rival), 4)
    p["P45_Fueras_Juego_Ratio"] = round(fueras_juego / (toques_area + rem_tot), 4)
    p["P46_Tiros_Libres_Peligrosos"] = round(tiros_libres / faltas_rec, 4)
    p["P47_Ritmo_Juego_Roto"] = round(saques_banda, 4)
    p["P48_Frecuencia_Corners"] = round((corners / rem_tot) * 10.0, 4)

    # Métricas base auxiliares para el Narrador XAI
    p["posesion_pct"] = round(posesion, 2)
    p["xg"] = round(xg, 2)
    p["xgot"] = round(xgot, 2)
    p["npxg"] = round(npxg, 2)
    p["goles_favor"] = round(goles, 2)
    p["goles_contra"] = round(goles_c, 2)
    p["remates_totales"] = round(rem_tot, 2)
    p["remates_puerta"] = round(rem_pue, 2)
    p["grandes_ocasiones"] = round(g_oc, 2)
    p["grandes_ocasiones_falladas"] = round(g_oc_fall, 2)
    p["toques_area_rival"] = round(toques_area, 2)
    p["pases_entre_lineas"] = round(pases_lineas, 2)
    p["despejes"] = round(despejes, 2)
    p["intercepciones"] = round(intercepciones, 2)
    p["bloqueos"] = round(bloqueos, 2)
    p["faltas"] = round(faltas, 2)
    p["corners"] = round(corners, 2)
    p["tarjetas_amarillas"] = round(amarillas, 2)
    return p


def calcular_55_patrones_matchup(
    patrones_local: Dict[str, float],
    patrones_visita: Dict[str, float],
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Calcula los Patrones 49 a 55 (Matchup Adversarial Local vs Visita)."""
    pL = dict(patrones_local)
    pV = dict(patrones_visita)

    pL["P49_Espejismo_Posesion"] = round((pL["posesion_pct"] / 50.0) * pV["P15_Muro_Asedio"] / max(0.05, pL["P01_xG_por_Remate"] * 10.0), 4)
    pV["P49_Espejismo_Posesion"] = round((pV["posesion_pct"] / 50.0) * pL["P15_Muro_Asedio"] / max(0.05, pV["P01_xG_por_Remate"] * 10.0), 4)

    pL["P50_Choque_Portero_Rival"] = round(pL["P06_Desperdicio_Ocasiones"] * (1.0 + pV["P13_Portero_Goles_Evitados"]), 4)
    pV["P50_Choque_Portero_Rival"] = round(pV["P06_Desperdicio_Ocasiones"] * (1.0 + pL["P13_Portero_Goles_Evitados"]), 4)

    pL["P51_Diferencial_Cierre_2T"] = round(pL["P26_Poder_Cierre_2T"] * (1.0 + max(0.0, pV["P27_Caida_Posesion_2T"]) / 10.0), 4)
    pV["P51_Diferencial_Cierre_2T"] = round(pV["P26_Poder_Cierre_2T"] * (1.0 + max(0.0, pL["P27_Caida_Posesion_2T"]) / 10.0), 4)

    pL["P52_Superioridad_Aerea"] = round(pL["P24_Ratio_Duelos_Aereos"] * (15.0 / max(4.0, pV["P16_Fuerza_Aerea_Defensiva"])), 4)
    pV["P52_Superioridad_Aerea"] = round(pV["P24_Ratio_Duelos_Aereos"] * (15.0 / max(4.0, pL["P16_Fuerza_Aerea_Defensiva"])), 4)

    pL["P53_Indice_Transicion_BTTS"] = round((pL["P47_Ritmo_Juego_Roto"] / 16.0) * pV["P39_Estilo_Directo"], 4)
    pV["P53_Indice_Transicion_BTTS"] = round((pV["P47_Ritmo_Juego_Roto"] / 16.0) * pL["P39_Estilo_Directo"], 4)

    pL["P54_Asedio_Corners_Matchup"] = round(pL["P34_Asedio_Corners_2T"] * (5.0 / max(1.5, pV["P22_Vulnerabilidad_Corners"])), 4)
    pV["P54_Asedio_Corners_Matchup"] = round(pV["P34_Asedio_Corners_2T"] * (5.0 / max(1.5, pL["P22_Vulnerabilidad_Corners"])), 4)

    pL["P55_Contraataque_Letal"] = round(pL["P39_Estilo_Directo"] * (pL["P21_Densidad_Recuperaciones"] / max(0.2, pV["P21_Densidad_Recuperaciones"])), 4)
    pV["P55_Contraataque_Letal"] = round(pV["P39_Estilo_Directo"] * (pV["P21_Densidad_Recuperaciones"] / max(0.2, pL["P21_Densidad_Recuperaciones"])), 4)

    return pL, pV


def calcular_patrones_completos_partido(
    metricas_local: Any,
    metricas_visita: Any,
    metricas_1t_local: Optional[Dict[str, Any]] = None,
    metricas_2t_local: Optional[Dict[str, Any]] = None,
    metricas_1t_visita: Optional[Dict[str, Any]] = None,
    metricas_2t_visita: Optional[Dict[str, Any]] = None,
    timeline_local: Optional[Dict[str, Any]] = None,
    timeline_visita: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Calcula los 55 patrones completos (P01..P55) para Local y Visita en una sola llamada."""
    p_l = calcular_55_patrones_equipo(metricas_local, metricas_1t_local, metricas_2t_local, timeline_local)
    p_v = calcular_55_patrones_equipo(metricas_visita, metricas_1t_visita, metricas_2t_visita, timeline_visita)
    return calcular_55_patrones_matchup(p_l, p_v)
