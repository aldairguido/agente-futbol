from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import math
import numpy as np
import pandas as pd


_MAPA_ALIAS_ESQUEMA: Dict[str, str] = {
    "expected_goals": "xg",
    "xg_favor": "xg",
    "ball_possession": "posesion_pct",
    "possession": "posesion_pct",
    "posesion": "posesion_pct",
    "total_shots": "remates_totales",
    "shots_total": "remates_totales",
    "clearances": "despejes",
    "goals_for": "goles_favor",
    "goals": "goles_favor",
    "goles": "goles_favor",
    "goals_against": "goles_contra",
    "goles_concedidos": "goles_contra",
    "shots_on_target": "remates_puerta",
    "corner_kicks": "corners",
    "saques_esquina": "corners",
    "fouls": "faltas",
    "faltas_cometidas": "faltas",
    "yellow_cards": "tarjetas_amarillas",
    "saves": "paradas",
    "paradas_portero": "paradas",
    "xgot_faced": "xgot_enfrentados",
    "xgot_enfrentado": "xgot_enfrentados",
    "xgot_contra": "xgot_enfrentados",
}

CLAVES_OBLIGATORIAS_ESQUEMA: List[str] = [
    "xg",
    "posesion_pct",
    "remates_totales",
    "goles_favor",
    "despejes",
]


def normalizar_alias_metricas(metricas_equipo: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza llaves con nombres alternativos de FotMob/Flashscore al esquema canónico."""
    if not isinstance(metricas_equipo, dict):
        return {}
    out = dict(metricas_equipo)
    for k_orig, k_canon in _MAPA_ALIAS_ESQUEMA.items():
        if k_orig in out and out[k_orig] is not None:
            if k_canon not in out or out[k_canon] is None:
                out[k_canon] = out[k_orig]
    if "goles_favor" in out and ("goles" not in out or out["goles"] is None):
        out["goles"] = out["goles_favor"]
    if "xgot_enfrentados" in out and ("xgot_enfrentado" not in out or out["xgot_enfrentado"] is None):
        out["xgot_enfrentado"] = out["xgot_enfrentados"]
    return out


def validar_esquema_datos(metricas_equipo: Dict[str, Any]) -> Dict[str, Any]:
    """
    Valida que el diccionario de métricas contenga todas las claves críticas no nulas
    tras aplicar el mapeo de alias. Lanza ValueError descriptivo si falta alguna.
    """
    norm = normalizar_alias_metricas(metricas_equipo)
    for k in CLAVES_OBLIGATORIAS_ESQUEMA:
        if k not in norm or norm[k] is None:
            raise ValueError(f"Error de Esquema: La métrica crítica '{k}' no se encontró o es nula.")
    return norm


def ordenar_df_cronologico(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ordena un DataFrame de partidos cronológicamente de más antiguo a más reciente
    (para que iloc[-5:] corresponda siempre a los 5 partidos más recientes).
    """
    if df is None or df.empty:
        return df
    df_work = df.copy()
    col_fecha = None
    for cand in ("fecha_iso", "fecha", "date", "timestamp", "fecha_dt"):
        if cand in df_work.columns:
            col_fecha = cand
            break
    if col_fecha is not None:
        fechas_dt = pd.to_datetime(df_work[col_fecha], dayfirst=True, format="mixed", errors="coerce")
        if fechas_dt.notna().any():
            df_work["_fecha_sort_dt"] = fechas_dt
            df_work = df_work.sort_values(by="_fecha_sort_dt", ascending=True, kind="mergesort").drop(columns=["_fecha_sort_dt"])
            return df_work.reset_index(drop=True)
    return df_work.reset_index(drop=True)


def calcular_promedios_racha_60_40(
    partidos_o_df: Any,
    nombre_equipo: str = "",
) -> Dict[str, Any]:
    """
    Calcula promedios ponderados por racha temporal (60% últimos 5 partidos + 40% primeros 10 partidos)
    y detecta si el equipo está [EN RACHA] (>= 4 victorias en últimos 5 -> +8% lambda)
    o [EN CRISIS] (>= 3 derrotas en últimos 5 -> -10% lambda).
    """
    if isinstance(partidos_o_df, pd.DataFrame):
        df = ordenar_df_cronologico(partidos_o_df)
        df_num = df.select_dtypes(include=[np.number])
        if len(df_num) > 5:
            p10 = df_num.iloc[:-5].mean(numeric_only=True)
            u5 = df_num.iloc[-5:].mean(numeric_only=True)
            promedios = (u5 * 0.60 + p10 * 0.40).to_dict()
        elif len(df_num) > 0:
            promedios = df_num.mean(numeric_only=True).to_dict()
        else:
            promedios = {}
        ultimos_5_rows = df.iloc[-5:].to_dict(orient="records") if len(df) > 0 else []
    elif isinstance(partidos_o_df, list) and partidos_o_df:
        # Lista de dicts de partidos (de auditoría o historial)
        filas: List[Dict[str, Any]] = []
        for idx, item in enumerate(partidos_o_df):
            if not isinstance(item, dict):
                continue
            f_str = str(item.get("fecha_iso") or item.get("fecha") or item.get("date") or "")
            gf = item.get("goles_equipo", item.get("goles_favor", item.get("gf")))
            gc = item.get("goles_rival", item.get("goles_contra", item.get("gc")))
            if gf is None and isinstance(item.get("marcador"), dict):
                gf = item["marcador"].get("equipo")
                gc = item["marcador"].get("rival")
            if gf is None and item.get("goles_local") is not None and (item.get("goles_visita") is not None or item.get("goles_visitante") is not None):
                gl_i = float(item["goles_local"])
                gv_i = float(item.get("goles_visita", item.get("goles_visitante")))
                loc_s = str(item.get("local") or "").lower()
                if nombre_equipo and nombre_equipo.lower() in loc_s:
                    gf, gc = gl_i, gv_i
                elif nombre_equipo and nombre_equipo.lower() in str(item.get("visita") or item.get("visitante") or "").lower():
                    gf, gc = gv_i, gl_i
                else:
                    gf, gc = gl_i, gv_i
            if gf is None or gc is None:
                marc = str(item.get("marcador") or item.get("resultado") or "")
                import re as _re
                m_sc = _re.search(r"(\d+)\s*[-:]\s*(\d+)", marc)
                if m_sc:
                    cond = str(item.get("condicion") or item.get("condition") or "L").upper()
                    g1, g2 = int(m_sc.group(1)), int(m_sc.group(2))
                    if cond in ("V", "A", "AWAY", "FUERA", "VISITA", "VISITANTE"):
                        gf, gc = g2, g1
                    else:
                        gf, gc = g1, g2
            fila_d = dict(item)
            fila_d["_idx_orig"] = idx
            fila_d["_fecha_str"] = f_str
            if gf is not None and gc is not None:
                try:
                    fila_d["goles_favor"] = float(gf)
                    fila_d["goles_contra"] = float(gc)
                except (TypeError, ValueError):
                    pass
            filas.append(fila_d)
        if filas:
            df_tmp = pd.DataFrame(filas)
            fechas_dt = pd.to_datetime(df_tmp["_fecha_str"], dayfirst=True, format="mixed", errors="coerce")
            if fechas_dt.notna().sum() >= max(1, len(df_tmp) // 2):
                df_tmp["_dt"] = fechas_dt
                df_tmp = df_tmp.sort_values(by="_dt", ascending=True, kind="mergesort")
            else:
                # Si la lista venía de más reciente a más antiguo (convención FotMob), invertir para que [-5:] sean los 5 más recientes
                df_tmp = df_tmp.iloc[::-1]
            df_tmp = df_tmp.reset_index(drop=True)
            df_num = df_tmp.select_dtypes(include=[np.number]).drop(columns=["_idx_orig"], errors="ignore")
            if len(df_num) > 5:
                p10 = df_num.iloc[:-5].mean(numeric_only=True)
                u5 = df_num.iloc[-5:].mean(numeric_only=True)
                promedios = (u5 * 0.60 + p10 * 0.40).to_dict()
            else:
                promedios = df_num.mean(numeric_only=True).to_dict()
            ultimos_5_rows = df_tmp.iloc[-5:].to_dict(orient="records")
        else:
            promedios = {}
            ultimos_5_rows = []
    else:
        promedios = {}
        ultimos_5_rows = []

    victorias_5 = 0
    empates_5 = 0
    derrotas_5 = 0
    for r in ultimos_5_rows:
        res_letra = str(r.get("resultado_letra") or r.get("res") or "").upper().strip()
        gf = r.get("goles_favor", r.get("goles_equipo", r.get("goles")))
        gc = r.get("goles_contra", r.get("goles_rival", r.get("goles_concedidos")))
        if gf is not None and gc is not None:
            try:
                gf_f, gc_f = float(gf), float(gc)
                if gf_f > gc_f:
                    victorias_5 += 1
                elif gf_f < gc_f:
                    derrotas_5 += 1
                else:
                    empates_5 += 1
                continue
            except (TypeError, ValueError):
                pass
        if res_letra in ("G", "W", "V", "GANADO"):
            victorias_5 += 1
        elif res_letra in ("P", "L", "D", "PERDIDO"):
            derrotas_5 += 1
        elif res_letra in ("E", "T", "EMPATE"):
            empates_5 += 1

    if derrotas_5 >= 3:
        estado = "EN CRISIS"
        badge = "[EN CRISIS]"
        icono_tg = "❄️ En crisis"
        factor_lambda = 0.90
    elif victorias_5 >= 4:
        estado = "EN RACHA"
        badge = "[EN RACHA]"
        icono_tg = "🔥 En racha"
        factor_lambda = 1.08
    else:
        estado = "ESTABLE"
        badge = ""
        icono_tg = "⚖️ Estable"
        factor_lambda = 1.00

    return {
        "promedios_60_40": promedios,
        "victorias_ult5": victorias_5,
        "empates_ult5": empates_5,
        "derrotas_ult5": derrotas_5,
        "estado_racha": estado,
        "en_crisis": derrotas_5 >= 3,
        "en_racha": victorias_5 >= 4,
        "etiqueta_visual": badge,
        "etiqueta_racha": badge,
        "etiqueta_telegram": icono_tg,
        "factor_lambda": factor_lambda,
    }


import re


def _val(d: Optional[Dict[str, Any]], *claves: str, default: float = 0.01, allow_zero: bool = True) -> float:
    """Extrae un float seguro soportando strings con '%' o 'completados/totales (pct%)'."""
    if not d:
        return default
    for k in claves:
        if k in d and d[k] is not None:
            nodo = d[k]
            v = nodo.get("promedio", nodo.get("valor", default)) if isinstance(nodo, dict) else nodo
            if isinstance(v, str):
                v_str = v.strip()
                # Si viene en formato "98/124 (79%)" y la clave pide _pct, extrae el porcentaje entre paréntesis
                m_paren = re.search(r"\((\d+(?:\.\d+)?)\s*%\)", v_str)
                if m_paren and ("pct" in k or "precision" in k):
                    v = float(m_paren.group(1))
                else:
                    m_num = re.search(r"[-+]?\d+(?:\.\d+)?", v_str.replace(",", "."))
                    if not m_num:
                        continue
                    v = float(m_num.group(0))
            try:
                f = float(v)
                if not math.isnan(f):
                    if not allow_zero and abs(f) < 1e-9:
                        return max(0.01, default)
                    return f
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
    Si recibe un DataFrame de >5 partidos, aplica ponderación cronológica 60% (últimos 5) / 40% (primeros 10).
    Calcula los 48 patrones base (P01 a P48) sin divisiones por cero ni constantes estáticas en 1T/2T.
    """
    info_racha: Optional[Dict[str, Any]] = None
    if isinstance(datos_equipo, pd.DataFrame):
        df_ord = ordenar_df_cronologico(datos_equipo)
        df_num = df_ord.select_dtypes(include=[np.number]).replace(0, 0.01)
        if len(df_num) > 5:
            m_p10 = df_num.iloc[:-5].mean(numeric_only=True)
            m_u5 = df_num.iloc[-5:].mean(numeric_only=True)
            m = (m_u5 * 0.60 + m_p10 * 0.40).to_dict()
        else:
            m = df_num.mean(numeric_only=True).to_dict()
        info_racha = calcular_promedios_racha_60_40(df_ord)
    elif isinstance(datos_equipo, dict):
        raw_m = datos_equipo.get("metricas", datos_equipo)
        m = normalizar_alias_metricas(raw_m if isinstance(raw_m, dict) else {})
    else:
        m = {}

    m = normalizar_alias_metricas(m)
    m1 = normalizar_alias_metricas(metricas_1t or {})
    m2 = normalizar_alias_metricas(metricas_2t or {})
    tl = timeline_goles or {}

    xg = max(0.05, _val(m, "xg", "xg_favor", default=1.35))
    xgot = max(0.05, _val(m, "xgot", "xgot_favor", default=xg))
    npxg = max(0.05, _val(m, "xg_sin_penalti", "npxg", default=xg * 0.90))
    xg_bp = max(0.01, _val(m, "xg_balon_parado", default=xg * 0.15))
    xg_abierto = max(0.05, _val(m, "xg_juego_abierto", default=max(0.05, xg - xg_bp)))

    goles = max(0.05, _val(m, "goles", "goles_favor", "goles_favor_est", default=xg))
    goles_c = max(0.05, _val(m, "goles_concedidos", "goles_contra", "goles_contra_real", "goles_encajados", default=1.20))

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
    # Cálculo directo y sin inversión para P13_Portero_Goles_Evitados:
    # Siempre se calcula como: xGOT_enfrentado (recibido por nuestro arquero) - Goles_concedidos
    tiene_xgot_enf = any(k in m and m[k] is not None for k in ("xgot_enfrentado", "xgot_enfrentados", "xgot_contra", "xgot_recibido"))
    xgot_enf = max(0.05, _val(m, "xgot_enfrentado", "xgot_enfrentados", "xgot_contra", "xgot_recibido", default=goles_c))
    if tiene_xgot_enf:
        goles_evit = round(xgot_enf - goles_c, 2)
    else:
        goles_evit = round(_val(m, "goles_evitados", default=round(xgot_enf - goles_c, 2)), 2)

    rem_puerta_rec = max(0.5, paradas + goles_c)
    rem_area_rec = max(1.0, _val(m, "remates_dentro_area_recibidos", default=rem_puerta_rec * 1.85))

    # Lectura real de 1T y 2T buscando tanto en m1/m2 como en m
    xg_1t_raw = _val(m1, "xg", "xg_1t", "xg_favor_1t", default=_val(m, "xg_1t", "xg_favor_1t", default=-1.0))
    xg_2t_raw = _val(m2, "xg", "xg_2t", "xg_favor_2t", default=_val(m, "xg_2t", "xg_favor_2t", default=-1.0))
    g1_raw = _val(m1, "goles", "goles_1t", "goles_favor_1t", default=_val(m, "goles_1t", "goles_favor_1t", default=-1.0))
    g2_raw = _val(m2, "goles", "goles_2t", "goles_favor_2t", default=_val(m, "goles_2t", "goles_favor_2t", default=-1.0))
    r1_raw = _val(m1, "remates_totales", "remates_totales_1t", default=_val(m, "remates_totales_1t", default=-1.0))
    r2_raw = _val(m2, "remates_totales", "remates_totales_2t", default=_val(m, "remates_totales_2t", default=-1.0))

    # Si alguna mitad no trajo xG separado, derívalo de la proporción real de goles y remates en 1T vs 2T (nunca 0.45/0.55 fijo)
    if xg_1t_raw <= 0.0 and xg_2t_raw <= 0.0:
        if (g1_raw >= 0.0 and g2_raw >= 0.0 and (g1_raw + g2_raw) > 0.05) or (r1_raw >= 0.0 and r2_raw >= 0.0 and (r1_raw + r2_raw) > 0.5):
            g1_use = max(0.02, g1_raw if g1_raw >= 0.0 else goles * 0.44)
            g2_use = max(0.02, g2_raw if g2_raw >= 0.0 else max(0.02, goles - g1_use))
            r1_use = max(0.5, r1_raw if r1_raw >= 0.0 else rem_tot * 0.45)
            r2_use = max(0.5, r2_raw if r2_raw >= 0.0 else max(0.5, rem_tot - r1_use))
            ratio_2t = 0.50 * (g2_use / max(0.1, g1_use + g2_use)) + 0.50 * (r2_use / max(1.0, r1_use + r2_use))
        else:
            impulso_2t = (
                0.50
                + 0.0022 * (toques_area - 20.0)
                + 0.0080 * (corners - 4.8)
                - 0.0018 * (posesion - 50.0)
                + 0.0015 * (pases_largos_pct - 48.0)
                + 0.0150 * (xg - 1.35)
            )
            ratio_2t = max(0.36, min(0.66, impulso_2t))
        xg_2t = max(0.05, round(xg * ratio_2t, 4))
        xg_1t = max(0.05, round(xg - xg_2t, 4))
    else:
        xg_1t = max(0.05, xg_1t_raw if xg_1t_raw > 0 else max(0.05, xg - xg_2t_raw))
        xg_2t = max(0.05, xg_2t_raw if xg_2t_raw > 0 else max(0.05, xg - xg_1t))

    xg_sum_mitades = max(0.10, xg_1t + xg_2t)
    ratio_1t_ref = xg_1t / xg_sum_mitades
    ratio_2t_ref = xg_2t / xg_sum_mitades

    goles_1t = max(0.02, g1_raw if g1_raw > 0 else goles * ratio_1t_ref)
    goles_2t = max(0.02, g2_raw if g2_raw > 0 else max(0.02, goles - goles_1t))
    rem_tot_1t = max(0.5, r1_raw if r1_raw > 0 else rem_tot * ratio_1t_ref)
    rem_tot_2t = max(0.5, r2_raw if r2_raw > 0 else max(0.5, rem_tot - rem_tot_1t))
    pos_1t = max(15.0, _val(m1, "posesion_pct", "posesion_1t", default=_val(m, "posesion_1t", default=posesion + (0.50 - ratio_2t_ref) * 6.0)))
    pos_2t = max(15.0, _val(m2, "posesion_pct", "posesion_2t", default=_val(m, "posesion_2t", default=posesion - (0.50 - ratio_2t_ref) * 6.0)))
    rem_pue_1t = max(0.3, _val(m1, "remates_puerta", "remates_puerta_1t", default=_val(m, "remates_puerta_1t", default=rem_pue * ratio_1t_ref)))
    rem_pue_2t = max(0.3, _val(m2, "remates_puerta", "remates_puerta_2t", default=_val(m, "remates_puerta_2t", default=rem_pue * ratio_2t_ref)))
    pases_prec_1t = max(40.0, _val(m1, "pases_pct", "pases_precision_1t", default=_val(m, "pases_precision_1t", default=pases_prec_pct + 1.0)))
    pases_prec_2t = max(40.0, _val(m2, "pases_pct", "pases_precision_2t", default=_val(m, "pases_precision_2t", default=pases_prec_pct - 1.0)))
    rem_fue_2t = max(0.2, _val(m2, "remates_fuera_area", "remates_fuera_2t", default=_val(m, "remates_fuera_2t", default=rem_f_area * ratio_2t_ref)))
    rem_area_2t = max(0.5, _val(m2, "remates_area", "remates_dentro_area_2t", default=_val(m, "remates_dentro_area_2t", default=rem_area * ratio_2t_ref)))
    corners_1t = max(0.3, _val(m1, "corners", "corners_1t", default=_val(m, "corners_1t", default=corners * ratio_1t_ref)))
    corners_2t = max(0.3, _val(m2, "corners", "corners_2t", default=_val(m, "corners_2t", default=corners * ratio_2t_ref)))

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
    p["P13_Portero_Goles_Evitados"] = round(goles_evit, 2)
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
    p["P26_Poder_Cierre_2T"] = round(xg_2t / max(0.10, xg_1t + xg_2t), 4)
    p["P25_Explosividad_1T"] = round(1.0 - p["P26_Poder_Cierre_2T"], 4)
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
    p["xg_1t"] = round(xg_1t, 2)
    p["xg_2t"] = round(xg_2t, 2)
    p["xgot"] = round(xgot, 2)
    p["xgot_enfrentado"] = round(xgot_enf, 2)
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
    if info_racha is not None:
        p["factor_racha_lambda"] = float(info_racha.get("factor_lambda", 1.0))
        p["victorias_ult5"] = float(info_racha.get("victorias_ult5", 0))
        p["derrotas_ult5"] = float(info_racha.get("derrotas_ult5", 0))
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

    pL["P50_Choque_Portero_Rival"] = round(pL["P06_Desperdicio_Ocasiones"] * max(0.15, 1.0 + pV["P13_Portero_Goles_Evitados"]), 4)
    pV["P50_Choque_Portero_Rival"] = round(pV["P06_Desperdicio_Ocasiones"] * max(0.15, 1.0 + pL["P13_Portero_Goles_Evitados"]), 4)

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
    datos_1t_local: Optional[Dict[str, Any]] = None,
    datos_2t_local: Optional[Dict[str, Any]] = None,
    datos_1t_visita: Optional[Dict[str, Any]] = None,
    datos_2t_visita: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Calcula los 55 patrones completos (P01..P55) para Local y Visita en una sola llamada."""
    m1_l = metricas_1t_local if metricas_1t_local is not None else datos_1t_local
    m2_l = metricas_2t_local if metricas_2t_local is not None else datos_2t_local
    m1_v = metricas_1t_visita if metricas_1t_visita is not None else datos_1t_visita
    m2_v = metricas_2t_visita if metricas_2t_visita is not None else datos_2t_visita
    p_l = calcular_55_patrones_equipo(metricas_local, m1_l, m2_l, timeline_local)
    p_v = calcular_55_patrones_equipo(metricas_visita, m1_v, m2_v, timeline_visita)
    return calcular_55_patrones_matchup(p_l, p_v)
