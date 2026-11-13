from __future__ import annotations

from utilidades_seguridad import _safe_float, _safe_int, _SafeFloat, _SafeInt

float = _SafeFloat
int = _SafeInt
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
            if "xg" not in fila_d or fila_d.get("xg") is None:
                filas_ft = ((item.get("periodos") or {}).get("FT") or {}).get("filas") or []
                for f_m in filas_ft:
                    if isinstance(f_m, dict) and f_m.get("clave") == "xg" and f_m.get("equipo") is not None:
                        try:
                            fila_d["xg"] = float(f_m["equipo"])
                        except (TypeError, ValueError):
                            pass
                        break
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
    goles_u5_vals: List[float] = []
    xg_u5_vals: List[float] = []
    for r in ultimos_5_rows:
        res_letra = str(r.get("resultado_letra") or r.get("res") or "").upper().strip()
        gf = r.get("goles_favor", r.get("goles_equipo", r.get("goles")))
        gc = r.get("goles_contra", r.get("goles_rival", r.get("goles_concedidos")))
        xg_r = r.get("xg", r.get("xg_favor", r.get("expected_goals")))
        if xg_r is not None:
            try:
                xg_u5_vals.append(float(xg_r))
            except (TypeError, ValueError):
                pass
        if gf is not None and gc is not None:
            try:
                gf_f, gc_f = float(gf), float(gc)
                goles_u5_vals.append(gf_f)
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

    goles_ult5_prom = float(sum(goles_u5_vals) / len(goles_u5_vals)) if goles_u5_vals else 1.20
    xg_ult5_prom = float(sum(xg_u5_vals) / len(xg_u5_vals)) if xg_u5_vals else goles_ult5_prom
    goles_ult5_sum = float(sum(goles_u5_vals)) if goles_u5_vals else goles_ult5_prom * 5.0
    xg_ult5_sum = float(sum(xg_u5_vals)) if xg_u5_vals else xg_ult5_prom * 5.0
    ratio_overperf = goles_ult5_sum / max(0.5, xg_ult5_sum) if xg_u5_vals and goles_u5_vals else 1.0
    sobre_rendimiento = ratio_overperf > 1.30
    factor_regresion_media = 0.90 if sobre_rendimiento else 1.00

    es_crisis = (derrotas_5 >= 3) or (len(ultimos_5_rows) >= 5 and victorias_5 == 0 and derrotas_5 >= 2)
    if es_crisis:
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
        "goles_ult5_prom": round(goles_ult5_prom, 2),
        "xg_ult5_prom": round(xg_ult5_prom, 2),
        "ratio_overperformance": round(ratio_overperf, 3),
        "sobre_rendimiento": sobre_rendimiento,
        "factor_regresion_media": factor_regresion_media,
        "estado_racha": estado,
        "en_crisis": es_crisis,
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


def _val_opt(d: Optional[Dict[str, Any]], *claves: str) -> Optional[float]:
    """Extrae un float opcional si alguna de las claves existe con dato real (distinguiendo 0.0 de None)."""
    if not d:
        return None
    for k in claves:
        if k in d and d[k] is not None:
            nodo = d[k]
            if isinstance(nodo, dict):
                if nodo.get("sin_dato"):
                    continue
                v = nodo.get("promedio", nodo.get("valor"))
                if v is None:
                    continue
            else:
                v = nodo
            if isinstance(v, str):
                v_str = v.strip()
                if not v_str or v_str.lower() in ("none", "null", "nan", "sin_dato", "n/d"):
                    continue
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
                    return f
            except (TypeError, ValueError):
                continue
    return None


def calcular_55_patrones_equipo(
    datos_equipo: Any = None,
    metricas_1t: Optional[Dict[str, Any]] = None,
    metricas_2t: Optional[Dict[str, Any]] = None,
    timeline_goles: Optional[Dict[str, Any]] = None,
    metricas_ft: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Acepta un DataFrame de 15 partidos o un diccionario con las 45 variables del equipo.
    Si recibe un DataFrame de >5 partidos, aplica ponderación cronológica 60% (últimos 5) / 40% (primeros 10).
    Calcula los 48 patrones base (P01 a P48) sin sustituir `0.0` observado por `0.01`, sin divisiones por cero
    (devolviendo `None` si el denominador real es `0`) y sin constantes estáticas en 1T/2T.
    """
    if datos_equipo is None and metricas_ft is not None:
        datos_equipo = metricas_ft
    info_racha: Optional[Dict[str, Any]] = None
    if isinstance(datos_equipo, pd.DataFrame):
        df_ord = ordenar_df_cronologico(datos_equipo)
        df_num = df_ord.select_dtypes(include=[np.number])
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

    xg_obs = _val_opt(m, "xg", "xg_favor")
    xg = max(0.0, xg_obs) if xg_obs is not None else 1.35
    xgot = max(0.0, _val(m, "xgot", "xgot_favor", default=max(0.05, xg)))
    npxg = max(0.0, _val(m, "xg_sin_penalti", "npxg", default=max(0.05, xg) * 0.90))
    xg_bp = max(0.0, _val(m, "xg_balon_parado", default=max(0.05, xg) * 0.15))
    xg_abierto = max(0.0, _val(m, "xg_juego_abierto", default=max(0.05, xg - xg_bp)))
    xg_corners_obs = _val_opt(m, "xg_corners", "xg_corner", "xg_saques_esquina")

    goles = max(0.0, _val(m, "goles", "goles_favor", "goles_favor_est", default=max(0.05, xg)))
    goles_c = max(0.0, _val(m, "goles_concedidos", "goles_contra", "goles_contra_real", "goles_encajados", default=1.20))

    posesion = max(15.0, min(85.0, _val(m, "posesion_pct", default=50.0)))
    posesion_rival = max(15.0, 100.0 - posesion)

    rem_tot_obs = _val_opt(m, "remates_totales")
    rem_tot = max(1.0, rem_tot_obs) if (rem_tot_obs is not None and rem_tot_obs > 0) else (12.0 if rem_tot_obs is None else 0.0)
    rem_pue = max(0.5, _val(m, "remates_puerta", default=4.2))
    rem_fue = max(0.5, _val(m, "remates_fuera", default=5.0))
    rem_bloq = max(0.0, _val(m, "remates_bloqueados", "remates_rechazados", default=2.8))
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
    # Cálculo unificado para P13_Portero_Goles_Evitados (Punto 1.1):
    # Misma fuente que Doble ADN/Consola/Cuadro cuando el proveedor reporta 'goles_evitados' explícito;
    # si 'goles_evitados' es None/ausente, deriva algebraicamente xGOT_enfrentado - Goles_concedidos.
    tiene_ge_obs = m.get("goles_evitados") is not None
    xgot_enf = max(0.05, _val(m, "xgot_enfrentado", "xgot_enfrentados", "xgot_contra", "xgot_recibido", default=goles_c))
    if tiene_ge_obs:
        goles_evit = round(_val(m, "goles_evitados", default=0.0), 2)
    else:
        goles_evit = round(xgot_enf - goles_c, 2)

    rem_puerta_rec = max(0.5, paradas + goles_c)
    rem_area_rec = max(1.0, _val(m, "remates_dentro_area_recibidos", default=rem_puerta_rec * 1.85))

    # Lectura real de 1T y 2T buscando tanto en m1/m2 como en m, distinguiendo 0.0 observado de None (no publicado)
    xg_1t_opt = _val_opt(m1, "xg", "xg_1t", "xg_favor_1t")
    if xg_1t_opt is None:
        xg_1t_opt = _val_opt(m, "xg_1t", "xg_favor_1t")
    xg_2t_opt = _val_opt(m2, "xg", "xg_2t", "xg_favor_2t")
    if xg_2t_opt is None:
        xg_2t_opt = _val_opt(m, "xg_2t", "xg_favor_2t")

    g1_opt = _val_opt(m1, "goles", "goles_1t", "goles_favor_1t")
    if g1_opt is None:
        g1_opt = _val_opt(m, "goles_1t", "goles_favor_1t")
    g2_opt = _val_opt(m2, "goles", "goles_2t", "goles_favor_2t")
    if g2_opt is None:
        g2_opt = _val_opt(m, "goles_2t", "goles_favor_2t")

    r1_opt = _val_opt(m1, "remates_totales", "remates_totales_1t")
    if r1_opt is None:
        r1_opt = _val_opt(m, "remates_totales_1t")
    r2_opt = _val_opt(m2, "remates_totales", "remates_totales_2t")
    if r2_opt is None:
        r2_opt = _val_opt(m, "remates_totales_2t")

    # P26_Poder_Cierre_2T:
    # - Si xG_1T y xG_2T están ambos confirmados (incluyendo 0.0 observado):
    #   si xG_1T + xG_2T > 0 -> P26 = xg_2t / (xg_1t + xg_2t) (exactamente 0.0 cuando xg_1t > 0 y xg_2t == 0.0).
    # - Si solo una mitad de xG está presente y la otra es None -> P26 = None (no_evaluable).
    # - Si ninguna mitad trajo xG directo pero sí existen goles y remates por mitad verificados -> derivar de goles/remates por mitad.
    # - Si tampoco existen mitades verificadas y se pasó explicitamente un dict de mitad con None (ej. metricas_2t={"xg": None}) -> P26 = None.
    mitad_explicita_incompleta = (
        (isinstance(metricas_1t, dict) and "xg" in metricas_1t and metricas_1t.get("xg") is None)
        or (isinstance(metricas_2t, dict) and "xg" in metricas_2t and metricas_2t.get("xg") is None)
        or ("xg_1t" in m and m.get("xg_1t") is None)
        or ("xg_2t" in m and m.get("xg_2t") is None)
    )

    p26_val: Optional[float] = None
    if xg_1t_opt is not None and xg_2t_opt is not None:
        xg_1t = max(0.0, float(xg_1t_opt))
        xg_2t = max(0.0, float(xg_2t_opt))
        if (xg_1t + xg_2t) > 0.0:
            p26_val = round(xg_2t / (xg_1t + xg_2t), 4)
        else:
            p26_val = None
    elif (xg_1t_opt is not None) ^ (xg_2t_opt is not None):
        # Falta una de las dos mitades: no_evaluable (None)
        xg_1t = max(0.0, float(xg_1t_opt)) if xg_1t_opt is not None else 0.0
        xg_2t = max(0.0, float(xg_2t_opt)) if xg_2t_opt is not None else 0.0
        p26_val = None
    elif mitad_explicita_incompleta:
        xg_1t = 0.0
        xg_2t = 0.0
        p26_val = None
    elif (g1_opt is not None and g2_opt is not None and (g1_opt + g2_opt) > 0.0) or (r1_opt is not None and r2_opt is not None and (r1_opt + r2_opt) > 0.0):
        if (g1_opt is not None and g2_opt is not None and (g1_opt + g2_opt) > 0.0) and (r1_opt is not None and r2_opt is not None and (r1_opt + r2_opt) > 0.0):
            ratio_2t = 0.50 * (g2_opt / (g1_opt + g2_opt)) + 0.50 * (r2_opt / (r1_opt + r2_opt))
        elif g1_opt is not None and g2_opt is not None and (g1_opt + g2_opt) > 0.0:
            ratio_2t = g2_opt / (g1_opt + g2_opt)
        else:
            ratio_2t = float(r2_opt or 0.0) / max(1e-6, float((r1_opt or 0.0) + (r2_opt or 0.0)))
        xg_2t = round(xg * ratio_2t, 4)
        xg_1t = round(max(0.0, xg - xg_2t), 4)
        p26_val = round(ratio_2t, 4)
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
        p26_val = round(xg_2t / max(0.10, xg_1t + xg_2t), 4)

    xg_sum_mitades = max(0.10, xg_1t + xg_2t)
    ratio_1t_ref = (xg_1t / xg_sum_mitades) if (xg_1t + xg_2t) > 0 else 0.45
    ratio_2t_ref = (xg_2t / xg_sum_mitades) if (xg_1t + xg_2t) > 0 else 0.55

    goles_1t = max(0.0, g1_opt if g1_opt is not None else goles * ratio_1t_ref)
    goles_2t = max(0.0, g2_opt if g2_opt is not None else max(0.0, goles - goles_1t))
    rem_tot_1t = max(0.5, r1_opt if (r1_opt is not None and r1_opt > 0) else max(0.5, rem_tot * ratio_1t_ref))
    rem_tot_2t = max(0.5, r2_opt if (r2_opt is not None and r2_opt > 0) else max(0.5, rem_tot - rem_tot_1t))
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
    elif "prop_anotados_76_90" in tl and tl.get("prop_anotados_76_90") is not None:
        prop_ult_15 = float(tl["prop_anotados_76_90"])
        prop_rec_0_15 = float(tl.get("prop_recibidos_00_15", 0.12))
    else:
        prop_ult_15 = None
        prop_rec_0_15 = None

    p: Dict[str, Any] = {}

    # A. EFICIENCIA OFENSIVA (P01 - P12)
    if (rem_tot_obs is not None and rem_tot_obs <= 0.0) or xg_obs is None:
        p["P01_xG_por_Remate"] = None
    else:
        p["P01_xG_por_Remate"] = round(xg / max(1.0, rem_tot), 4)

    p["P02_xG_Area_por_Remate"] = round(xg_abierto / rem_area, 4) if xg_obs is not None else None

    xgot_obs = _val_opt(m, "xgot", "xgot_favor")
    if xgot_obs is not None and xg_obs is not None and xg > 0:
        p["P03_Calidad_xGOT_vs_xG"] = round(xgot_obs / max(0.05, xg), 4)
    else:
        p["P03_Calidad_xGOT_vs_xG"] = None
    p["P04_Eficiencia_npxG"] = round(npxg / max(1.0, rem_tot), 4) if (rem_tot > 0 and xg_obs is not None) else None
    p["P05_Conversion_Real"] = round(goles / rem_pue, 4)
    g_oc_fall_obs = _val_opt(m, "grandes_ocasiones_falladas")
    if g_oc_fall_obs is not None and g_oc > 0:
        p["P06_Desperdicio_Ocasiones"] = round(g_oc_fall_obs / g_oc, 4)
    else:
        p["P06_Desperdicio_Ocasiones"] = None
    p["P07_Precision_Disparo"] = round(rem_pue / max(1.0, rem_tot), 4) if rem_tot > 0 else None
    p["P08_Tiros_Bloqueados_Ratio"] = round(rem_bloq / max(1.0, rem_tot), 4) if rem_tot > 0 else None
    p["P09_Dependencia_Balon_Parado"] = round(xg_bp / max(0.05, xg), 4) if xg > 0 else None
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
    p["P26_Poder_Cierre_2T"] = p26_val
    p["P25_Explosividad_1T"] = round(1.0 - p26_val, 4) if p26_val is not None else None
    p["P27_Caida_Posesion_2T"] = round(pos_1t - pos_2t, 4)
    p["P28_Aceleracion_2T"] = round(pos_2t - pos_1t, 4)
    p["P29_Letalidad_1T"] = round(goles_1t / rem_pue_1t, 4)
    p["P30_Letalidad_2T"] = round(goles_2t / rem_pue_2t, 4)
    p["P31_Precision_Bajo_Fatiga"] = round(pases_prec_2t / pases_prec_1t, 4)
    p["P32_Intensidad_Remates_2T"] = round(rem_tot_2t / rem_tot_1t, 4)
    p["P33_Desesperacion_2T"] = round(rem_fue_2t / rem_area_2t, 4)
    p["P34_Asedio_Corners_2T"] = round(corners_2t / corners_1t, 4)
    p["P35_Peso_Goles_Tardios"] = round(prop_ult_15, 4) if prop_ult_15 is not None else None
    p["P36_Vulnerabilidad_Temprana"] = round(prop_rec_0_15, 4) if prop_rec_0_15 is not None else None

    # D. ASEDIO Y CONTEXTO (P37 - P48)
    # P37_Peligro_por_Corner: solo evaluable si existe xG exclusivo de córneres (`xg_corners`);
    # si solo existe `xg_balon_parado` general, ese cociente pertenece a P09 y P37 queda en None (no_evaluable).
    p["P37_Peligro_por_Corner"] = round(xg_corners_obs / corners, 4) if (xg_corners_obs is not None and corners > 0) else None
    p["P38_Densidad_Pases"] = round(pases_tot / posesion, 4)
    p["P39_Estilo_Directo"] = round(pases_largos_pct / 100.0, 4)
    p["P40_Rompe_Lineas"] = round(pases_lineas / pases_tot, 4)
    p["P41_Verticalidad"] = round(pases_tercio_pct / 100.0, 4)
    p["P42_Dependencia_Centros"] = round((centros_pct / 100.0) * 15.0 / toques_area, 4)
    p["P43_Agresividad_Defensiva"] = round(amarillas / faltas, 4)
    p["P44_Pressing_Alto"] = round((intercepciones + recuperaciones) / max(15.0, posesion_rival), 4)
    p["P45_Fueras_Juego_Ratio"] = round(fueras_juego / max(1.0, toques_area + rem_tot), 4)
    p["P46_Tiros_Libres_Peligrosos"] = round(tiros_libres / faltas_rec, 4)
    p["P47_Ritmo_Juego_Roto"] = round(saques_banda, 4)
    p["P48_Frecuencia_Corners"] = round((corners / rem_tot) * 10.0, 4) if rem_tot > 0 else None

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
        p["factor_racha_lambda"] = _safe_float(info_racha.get("factor_lambda"), 1.0)
        p["victorias_ult5"] = _safe_float(info_racha.get("victorias_ult5"), 0)
        p["derrotas_ult5"] = _safe_float(info_racha.get("derrotas_ult5"), 0)
    return p


def calcular_55_patrones_matchup(
    patrones_local: Dict[str, Any],
    patrones_visita: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Calcula los Patrones 49 a 55 (Matchup Adversarial Local vs Visita)."""
    pL = dict(patrones_local)
    pV = dict(patrones_visita)

    p01_l = pL.get("P01_xG_por_Remate")
    p01_v = pV.get("P01_xG_por_Remate")
    pL["P49_Espejismo_Posesion"] = (
        round((pL["posesion_pct"] / 50.0) * pV["P15_Muro_Asedio"] / max(0.05, float(p01_l) * 10.0), 4)
        if p01_l is not None else None
    )
    pV["P49_Espejismo_Posesion"] = (
        round((pV["posesion_pct"] / 50.0) * pL["P15_Muro_Asedio"] / max(0.05, float(p01_v) * 10.0), 4)
        if p01_v is not None else None
    )

    p06_l = pL.get("P06_Desperdicio_Ocasiones")
    p06_v = pV.get("P06_Desperdicio_Ocasiones")
    p13_l = pL.get("P13_Portero_Goles_Evitados") or 0.0
    p13_v = pV.get("P13_Portero_Goles_Evitados") or 0.0
    pL["P50_Choque_Portero_Rival"] = round(float(p06_l) * max(0.15, 1.0 + p13_v), 4) if p06_l is not None else None
    pV["P50_Choque_Portero_Rival"] = round(float(p06_v) * max(0.15, 1.0 + p13_l), 4) if p06_v is not None else None

    p26_l = pL.get("P26_Poder_Cierre_2T")
    p26_v = pV.get("P26_Poder_Cierre_2T")
    pL["P51_Diferencial_Cierre_2T"] = (
        round(float(p26_l) * (1.0 + max(0.0, float(pV.get("P27_Caida_Posesion_2T") or 0.0)) / 10.0), 4)
        if p26_l is not None else None
    )
    pV["P51_Diferencial_Cierre_2T"] = (
        round(float(p26_v) * (1.0 + max(0.0, float(pL.get("P27_Caida_Posesion_2T") or 0.0)) / 10.0), 4)
        if p26_v is not None else None
    )

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


_METRICAS_TENSOR_DEFAULT: tuple[str, ...] = (
    "xg",
    "xg_contra",
    "goles",
    "goles_contra",
    "remates_totales",
    "remates_totales_contra",
    "remates_puerta",
    "remates_puerta_contra",
    "grandes_ocasiones",
    "grandes_ocasiones_contra",
    "toques_area_rival",
    "toques_area_rival_contra",
    "corners",
    "corners_contra",
    "tarjetas_amarillas",
    "faltas",
    "posesion_pct",
    "pases_precision_pct",
    "duelos_suelo_pct",
    "recuperaciones",
    "despejes",
)


def _normalizar_condicion_tensor(cond: Any) -> str:
    c = str(cond or "").strip().lower()
    if c in ("casa", "local", "l", "h", "home"):
        return "casa"
    if c in ("fuera", "visita", "visitante", "v", "a", "away"):
        return "fuera"
    return c


def _ordenar_partidos_cronologico_tensor(partidos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ordena los partidos de más antiguo a más reciente preservando None-safety."""
    def _clave(item: Tuple[int, Dict[str, Any]]) -> Tuple[str, int]:
        idx, p = item
        f = str(p.get("fecha_iso") or p.get("fecha") or p.get("utcTime") or p.get("date") or "").strip()
        # Normaliza DD.MM.YYYY -> YYYY-MM-DD si aparece
        if len(f) >= 10 and f[2:3] in (".", "/") and f[5:6] in (".", "/"):
            f = f"{f[6:10]}-{f[3:5]}-{f[0:2]}"
        else:
            f = f[:10]
        # Si fechas empatan o faltan, respeta el orden inverso asumiendo idx=0 más reciente
        return (f, -idx)

    ordenados = sorted(enumerate(partidos), key=_clave)
    return [p for _, p in ordenados]


def _a_float_real_tensor(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)
    txt = str(val).strip().lower()
    if txt in ("", "none", "null", "nan", "n/a", "n/d", "sin_dato", "sin dato", "-"):
        return None
    try:
        num = float(val)
        return None if (math.isnan(num) or math.isinf(num)) else num
    except (TypeError, ValueError):
        return None


def _extraer_goles_periodo_tensor(partido: Dict[str, Any], periodo: str, bando: str) -> Optional[float]:
    filas = ((partido.get("periodos") or {}).get(periodo) or {}).get("filas") or []
    for fila in filas:
        if str(fila.get("clave") or "").strip().lower() == "goles":
            v = _a_float_real_tensor(fila.get(bando))
            if v is not None:
                return v

    def _leer_m(clave_m: str) -> Optional[float]:
        m = partido.get(clave_m)
        if isinstance(m, dict):
            return _a_float_real_tensor(m.get(bando))
        return None

    if periodo == "FT":
        return _leer_m("marcador")
    if periodo == "1T":
        return _leer_m("marcador_1t")
    if periodo == "2T":
        g2 = _leer_m("marcador_2t")
        if g2 is not None:
            return g2
        g_ft = _leer_m("marcador")
        g_1t = _leer_m("marcador_1t")
        if g_ft is not None and g_1t is not None and g_ft >= g_1t:
            return float(g_ft - g_1t)
    return None


def _extraer_valor_tensor_partido(partido: Dict[str, Any], metrica: str, periodo: str) -> Optional[float]:
    m_low = str(metrica or "").strip().lower()
    if m_low.endswith("_contra"):
        bando = "rival"
        clave_base = m_low[:-7]
    elif m_low.endswith("_favor"):
        bando = "equipo"
        clave_base = m_low[:-6]
    else:
        bando = "equipo"
        clave_base = m_low

    if clave_base == "goles":
        return _extraer_goles_periodo_tensor(partido, periodo, bando)

    filas = ((partido.get("periodos") or {}).get(periodo) or {}).get("filas") or []
    for fila in filas:
        if str(fila.get("clave") or "").strip().lower() == clave_base:
            v = _a_float_real_tensor(fila.get(bando))
            if v is not None:
                return v
    return None


def _percentil_lineal_tensor(ordenados: List[float], q: float) -> float:
    if not ordenados:
        return 0.0
    if len(ordenados) == 1:
        return float(ordenados[0])
    pos = (len(ordenados) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(ordenados[lo])
    frac = pos - lo
    return float(ordenados[lo] + (ordenados[hi] - ordenados[lo]) * frac)


def _calcular_estadisticos_tensor(valores: List[float]) -> Dict[str, Any]:
    import statistics as _stats

    n = len(valores)
    if n == 0:
        return {
            "n": 0,
            "media": None,
            "media_ponderada_60_40": None,
            "mediana": None,
            "desviacion_estandar": None,
            "rango_min_max": None,
            "p25_p75": None,
            "frecuencias_empiricas": {},
        }
    media = sum(valores) / n
    if n > 5:
        u5 = sum(valores[-5:]) / 5.0
        prev = sum(valores[:-5]) / float(n - 5)
        w60_40 = 0.60 * u5 + 0.40 * prev
    else:
        w60_40 = media
    med = float(_stats.median(valores))
    std = float(_stats.pstdev(valores)) if n >= 2 else 0.0
    ord_v = sorted(valores)
    p25 = _percentil_lineal_tensor(ord_v, 0.25)
    p75 = _percentil_lineal_tensor(ord_v, 0.75)

    umbrales_gt = (("gt_0_5", 0.5), ("gt_1_5", 1.5), ("gt_2_5", 2.5), ("gt_3_5", 3.5), ("gt_4_5", 4.5), ("gt_5_5", 5.5))
    umbrales_lt = (("lt_1_5", 1.5), ("lt_2_5", 2.5), ("lt_3_5", 3.5), ("lt_12_5", 12.5))
    freqs: Dict[str, Dict[str, Any]] = {}
    for k_lbl, thr in umbrales_gt:
        cnt = sum(1 for v in valores if v > thr)
        freqs[k_lbl] = {"k": cnt, "n": n, "pct": round(cnt / n, 4), "etiqueta": f"{cnt}/{n}"}
    for k_lbl, thr in umbrales_lt:
        cnt = sum(1 for v in valores if v < thr)
        freqs[k_lbl] = {"k": cnt, "n": n, "pct": round(cnt / n, 4), "etiqueta": f"{cnt}/{n}"}

    return {
        "n": n,
        "media": round(media, 4),
        "media_ponderada_60_40": round(w60_40, 4),
        "mediana": round(med, 4),
        "desviacion_estandar": round(std, 4),
        "rango_min_max": [round(ord_v[0], 4), round(ord_v[-1], 4)],
        "p25_p75": [round(p25, 4), round(p75, 4)],
        "frecuencias_empiricas": freqs,
    }


def construir_tensor_multi_periodo_equipo(
    doc_o_partidos: Any,
    condicion_objetivo: Optional[str] = "casa",
    metricas: Optional[Tuple[str, ...]] = None,
) -> Dict[str, Any]:
    """
    Construye el tensor multi-periodo (FT, 1T, 2T) x (General 15P, Condicion)
    con media ponderada 60/40 (últimos 5 cronológicos), mediana, desviación
    estándar, rango, P25/P75, frecuencias empíricas (k/N) y mezcla bayesiana
    65% condición + 35% general cuando N_cond >= 4 (fallback a general si < 4).
    """
    if isinstance(doc_o_partidos, dict):
        partidos_raw = list(doc_o_partidos.get("partidos") or [])
    elif isinstance(doc_o_partidos, list):
        partidos_raw = list(doc_o_partidos)
    else:
        partidos_raw = []

    partidos_ord = _ordenar_partidos_cronologico_tensor([p for p in partidos_raw if isinstance(p, dict)])
    cond_norm = _normalizar_condicion_tensor(condicion_objetivo) if condicion_objetivo else ""
    if cond_norm not in ("casa", "fuera"):
        cond_norm = ""
    partidos_cond = [
        p for p in partidos_ord
        if cond_norm and _normalizar_condicion_tensor(p.get("condicion")) == cond_norm
    ]

    lista_metricas = tuple(metricas) if metricas else _METRICAS_TENSOR_DEFAULT
    tensor: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    mezcla: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for periodo in ("FT", "1T", "2T"):
        tensor[periodo] = {"general": {}, "condicion": {}}
        mezcla[periodo] = {}
        for m in lista_metricas:
            vals_gen = [
                v for v in (_extraer_valor_tensor_partido(p, m, periodo) for p in partidos_ord)
                if v is not None
            ]
            vals_cond = [
                v for v in (_extraer_valor_tensor_partido(p, m, periodo) for p in partidos_cond)
                if v is not None
            ]
            est_gen = _calcular_estadisticos_tensor(vals_gen)
            est_cond = _calcular_estadisticos_tensor(vals_cond)
            tensor[periodo]["general"][m] = est_gen
            tensor[periodo]["condicion"][m] = est_cond

            n_c = est_cond["n"]
            n_g = est_gen["n"]
            if cond_norm and n_c >= 4 and n_g >= 1 and est_cond["media_ponderada_60_40"] is not None and est_gen["media_ponderada_60_40"] is not None:
                m60 = 0.65 * float(est_cond["media_ponderada_60_40"]) + 0.35 * float(est_gen["media_ponderada_60_40"])
                m_arp = 0.65 * float(est_cond["media"]) + 0.35 * float(est_gen["media"])
                m_med = 0.65 * float(est_cond["mediana"]) + 0.35 * float(est_gen["mediana"])
                modo = f"mezcla_65_{cond_norm}_35_general"
                w_c, w_g = 0.65, 0.35
                freqs = est_cond["frecuencias_empiricas"]
            elif n_g >= 1:
                m60 = est_gen["media_ponderada_60_40"]
                m_arp = est_gen["media"]
                m_med = est_gen["mediana"]
                modo = "fallback_general"
                w_c, w_g = 0.0, 1.0
                freqs = est_gen["frecuencias_empiricas"]
            else:
                m60 = m_arp = m_med = None
                modo = "sin_dato"
                w_c, w_g = 0.0, 0.0
                freqs = {}

            mezcla[periodo][m] = {
                "n_general": n_g,
                "n_condicion": n_c,
                "peso_condicion": w_c,
                "peso_general": w_g,
                "modo": modo,
                "media": round(m_arp, 4) if m_arp is not None else None,
                "media_ponderada_60_40": round(m60, 4) if m60 is not None else None,
                "mediana": round(m_med, 4) if m_med is not None else None,
                "desviacion_estandar": est_cond["desviacion_estandar"] if (w_c > 0 and est_cond["desviacion_estandar"] is not None) else est_gen["desviacion_estandar"],
                "rango_min_max": est_cond["rango_min_max"] if (w_c > 0 and est_cond["rango_min_max"] is not None) else est_gen["rango_min_max"],
                "p25_p75": est_cond["p25_p75"] if (w_c > 0 and est_cond["p25_p75"] is not None) else est_gen["p25_p75"],
                "frecuencias_empiricas": freqs,
            }

    def _ratio_m(clave: str) -> Optional[float]:
        v1 = (mezcla["1T"].get(clave) or {}).get("media_ponderada_60_40")
        v2 = (mezcla["2T"].get(clave) or {}).get("media_ponderada_60_40")
        if v1 is not None and v2 is not None and float(v1) > 0:
            return round(float(v2) / float(v1), 3)
        return None

    d1_pct = (mezcla["1T"].get("duelos_suelo_pct") or {}).get("media")
    d2_pct = (mezcla["2T"].get("duelos_suelo_pct") or {}).get("media")
    caida_duelos = round(float(d1_pct) - float(d2_pct), 1) if (d1_pct is not None and d2_pct is not None) else None

    return {
        "condicion_objetivo": cond_norm or "general",
        "n_general": len(partidos_ord),
        "n_condicion": len(partidos_cond),
        "tensor": tensor,
        "mezcla_65_35": mezcla,
        "ratios_mitades": {
            "ratio_2t_1t_xg": _ratio_m("xg"),
            "ratio_2t_1t_remates": _ratio_m("remates_totales"),
            "ratio_2t_1t_goles": _ratio_m("goles"),
            "caida_duelos_pp": caida_duelos,
        },
    }


def detectar_contexto_competicion(
    competicion: str = "",
    fase: str = "",
    sede_neutral: bool = False,
) -> Dict[str, Any]:
    """
    Puntos 34 y 40: Distingue estrictamente fase de grupos, eliminatoria directa,
    ida, vuelta y sede neutral para evitar apilar multiplicadores excesivos.
    """
    txt = f"{competicion or ''} {fase or ''}".lower().strip()
    es_grupos = any(k in txt for k in ("grupo", "group", "fase de liga", "league stage", "jornada", "apertura", "clausura"))
    es_ida = any(k in txt for k in ("ida", "1st leg", "first leg", "partido 1"))
    es_vuelta = any(k in txt for k in ("vuelta", "2nd leg", "second leg", "partido 2", "revancha"))
    es_final = any(k in txt for k in ("final", "supercopa", "super cup", "trofeo")) and not any(k in txt for k in ("octavos", "cuartos", "semifinal", "semi-final", "quarter"))
    es_eliminatoria = any(
        k in txt
        for k in (
            "copa", "cup", "playoff", "play-off", "eliminatoria", "knockout",
            "octavos", "cuartos", "semifinal", "semi-final", "final", "repechaje",
            "libertadores", "sudamericana", "champions", "europa league", "conference",
        )
    ) and not es_grupos

    es_neutral = bool(sede_neutral or es_final or "neutral" in txt)
    if es_grupos:
        tipo_fase = "fase_grupos"
        factor_copa_local = 1.00
    elif es_neutral:
        tipo_fase = "sede_neutral"
        factor_copa_local = 1.00
    elif es_vuelta:
        tipo_fase = "eliminatoria_vuelta"
        factor_copa_local = 1.18
    elif es_ida:
        tipo_fase = "eliminatoria_ida"
        factor_copa_local = 1.10
    elif es_eliminatoria:
        tipo_fase = "eliminatoria_directa"
        factor_copa_local = 1.15
    else:
        tipo_fase = "liga_regular"
        factor_copa_local = 1.00

    return {
        "tipo_fase": tipo_fase,
        "es_copa_o_playoff": bool(es_eliminatoria),
        "es_fase_grupos": bool(es_grupos),
        "es_ida": bool(es_ida),
        "es_vuelta": bool(es_vuelta),
        "sede_neutral": bool(es_neutral),
        "factor_copa_local": round(factor_copa_local, 4),
    }


def evaluar_condicion_gigante_visitante(
    cuadro_b_local: Optional[Dict[str, Any]] = None,
    cuadro_b_visita: Optional[Dict[str, Any]] = None,
    attack_local: float = 50.0,
    attack_visita: float = 50.0,
) -> Dict[str, Any]:
    """
    Sección 8.3 (Puntos 35 y 40): Evalúa si se cumplen simultáneamente las 4 condiciones
    de activación de 'Gigante Visitante' con cobertura real verificada (n >= 10).
    """
    cb_l = cuadro_b_local if isinstance(cuadro_b_local, dict) else {}
    cb_v = cuadro_b_visita if isinstance(cuadro_b_visita, dict) else {}

    def _extraer_n_y_pct(cb_d: Dict[str, Any], clave: str, clave_alt: str = "") -> Tuple[int, float]:
        cel = cb_d.get(clave)
        if cel is None and clave_alt:
            cel = cb_d.get(clave_alt)
        if cel is None and isinstance(cb_d.get("filas"), dict):
            cel = cb_d["filas"].get(clave) or (cb_d["filas"].get(clave_alt) if clave_alt else None)
        if isinstance(cel, dict) or hasattr(cel, "get"):
            n_tot = int(cel.get("total", 0) or 0)
            pct = float(cel.get("pct", 0.0) or 0.0)
            return n_tot, pct
        return 0, 0.0

    n_vic_l, pct_vic_l = _extraer_n_y_pct(cb_l, "victorias")
    n_vic_v, pct_vic_v = _extraer_n_y_pct(cb_v, "victorias")
    n_pm_l, pct_pm_l = _extraer_n_y_pct(cb_l, "primero_en_marcar", "primero_marcar")
    n_pm_v, pct_pm_v = _extraer_n_y_pct(cb_v, "primero_en_marcar", "primero_marcar")

    if n_vic_l == 0:
        n_vic_l = int(cb_l.get("n_con_marcador", cb_l.get("n_partidos", 0)) or 0)
    if n_vic_v == 0:
        n_vic_v = int(cb_v.get("n_con_marcador", cb_v.get("n_partidos", 0)) or 0)
    if n_pm_l == 0:
        n_pm_l = int(cb_l.get("n_con_eventos_gol", 0) or 0)
    if n_pm_v == 0:
        n_pm_v = int(cb_v.get("n_con_eventos_gol", 0) or 0)

    cobertura_ok = bool(n_vic_l >= 10 and n_vic_v >= 10 and n_pm_l >= 10 and n_pm_v >= 10)
    cond_victorias = bool(pct_vic_v >= (pct_vic_l + 25.0))
    cond_primero_marcar = bool(pct_pm_v >= 65.0)
    cond_attack = bool(float(attack_visita) >= (float(attack_local) + 8.0))
    activo = bool(cobertura_ok and cond_victorias and cond_primero_marcar and cond_attack)

    return {
        "activo": activo,
        "cobertura_ok": cobertura_ok,
        "n_victorias_local": n_vic_l,
        "n_victorias_visita": n_vic_v,
        "n_primero_marcar_local": n_pm_l,
        "n_primero_marcar_visita": n_pm_v,
        "pct_victorias_local": round(pct_vic_l, 2),
        "pct_victorias_visita": round(pct_vic_v, 2),
        "pct_primero_marcar_local": round(pct_pm_l, 2),
        "pct_primero_marcar_visita": round(pct_pm_v, 2),
        "attack_local": round(float(attack_local), 2),
        "attack_visita": round(float(attack_visita), 2),
    }


def aplicar_ajuste_gigante_visitante(
    lam_local: float,
    lam_visita: float,
    bono_localia_aplicado: float = 1.0,
    sede_neutral: bool = False,
) -> Dict[str, float]:
    """
    Sección 8.3: Orden exacto de operaciones sobre λ cuando se activa 'Gigante Visitante':
      1. Retirar primero el bono de localía aplicable (sin retirar ningún bono si sede_neutral=True
         o si no se había aplicado bono de localía).
      2. Definir `lam_local_ref` como la λ local después de retirar el bono de localía y ANTES de
         multiplicar por 0.85.
      3. Calcular `lam_visita_ajustada = max(lam_visita, 1.15 * lam_local_ref)`.
      4. Calcular `lam_local_ajustada = 0.85 * lam_local_ref`.
    """
    lam_l_in = max(0.05, float(lam_local))
    lam_v_in = max(0.05, float(lam_visita))
    bono_ef = float(bono_localia_aplicado) if (not sede_neutral and float(bono_localia_aplicado) > 1.0) else 1.0
    lam_local_ref = lam_l_in / bono_ef
    lam_visita_ajustada = max(lam_v_in, 1.15 * lam_local_ref)
    lam_local_ajustada = 0.85 * lam_local_ref
    return {
        "lam_local_ref": round(lam_local_ref, 6),
        "lam_local_ajustada": round(lam_local_ajustada, 6),
        "lam_visita_ajustada": round(lam_visita_ajustada, 6),
        "bono_retirado": round(bono_ef, 4),
    }


def aplicar_freno_de_mano_y_matchup_21_22(
    lam_base_local: float,
    lam_base_visita: float,
    lam_red_local: float,
    lam_red_visita: float,
    ratings_local: Optional[Dict[str, Any]] = None,
    ratings_visita: Optional[Dict[str, Any]] = None,
    posesion_proyectada_local: float = 50.0,
    posesion_proyectada_visita: float = 50.0,
    en_crisis_local: bool = False,
    en_crisis_visita: bool = False,
    goles_ult5_local: float = 1.2,
    goles_ult5_visita: float = 1.2,
    competicion: str = "",
    fase: str = "",
    sede_neutral: bool = False,
    toques_area_local: Optional[float] = None,
    cobertura_asedio_real: bool = True,
    cuadro_b_local: Optional[Dict[str, Any]] = None,
    cuadro_b_visita: Optional[Dict[str, Any]] = None,
    es_gigante_visitante: Optional[bool] = None,
    traza_out: Optional[List[Dict[str, Any]]] = None,
    etapa_prefijo: str = "",
) -> Tuple[float, float, float, float]:
    """
    Implementa los Puntos 21, 22, 34, 35 y 40:
      - Punto 21a/21b: Doble Freno de Mano y Techo de Bono en Caja Azul.
      - Punto 22: Multiplicador contra Defensas Rotas, Asedio Local y Amortiguador de Fortín Local.
      - Punto 40: Prohibido multiplicar `1.35 * 1.25` (+68.75%) por duplicado entre Copa y Asedio.
      - Punto 35/8.3: Si se activa 'Gigante Visitante', retira el bono de localía, calcula `lam_local_ref`,
        `lam_visita_ajustada = max(lam_visita * 1.15, 1.15 * lam_local_ref)` y `lam_local_ajustada = 0.85 * lam_local_ref`.
    Retorna: (lam_final_local, lam_final_visita, delta_pct_local, delta_pct_visita).
    """
    def _trazar(regla: str, al: float, dl: float, av: float, dv: float, motivo: str) -> None:
        if traza_out is None:
            return
        if abs(dl - al) < 1e-6 and abs(dv - av) < 1e-6:
            return
        traza_out.append({
            "etapa": etapa_prefijo or "freno_matchup_21_22",
            "regla": regla,
            "lam_l_antes": round(float(al), 4),
            "lam_l_despues": round(float(dl), 4),
            "lam_v_antes": round(float(av), 4),
            "lam_v_despues": round(float(dv), 4),
            "motivo": motivo,
        })

    rL = ratings_local or {}
    rV = ratings_visita or {}

    attack_rating_local = _safe_float(rL.get("Final_Attack_Strength", rL.get("attack", rL.get("Attack_Rating"))), 50.0)
    attack_rating_visita = _safe_float(rV.get("Final_Attack_Strength", rV.get("attack", rV.get("Attack_Rating"))), 50.0)
    defense_rating_local = _safe_float(rL.get("Final_Defense_Strength", rL.get("defense", rL.get("Defense_Rating"))), 50.0)
    defense_rating_visita = _safe_float(rV.get("Final_Defense_Strength", rV.get("defense", rV.get("Defense_Rating"))), 50.0)
    pressing_rating_visita = _safe_float(rV.get("Pressing_Intensity_Score", rV.get("pressing", rV.get("Pressing_Rating"))), 50.0)

    lam_b_l = max(0.10, float(lam_base_local))
    lam_b_v = max(0.10, float(lam_base_visita))
    lam_l = max(0.20, float(lam_red_local))
    lam_v = max(0.20, float(lam_red_visita))

    # ── Puntos 22, 34 y 40: Copa, Asedio y Defensa Rota SIN apilamiento 1.35 * 1.25 ──
    ctx_comp = detectar_contexto_competicion(competicion=competicion, fase=fase, sede_neutral=sede_neutral)
    factor_copa_l = float(ctx_comp.get("factor_copa_local", 1.0))

    if es_gigante_visitante is None and (cuadro_b_local is not None or cuadro_b_visita is not None):
        info_gv = evaluar_condicion_gigante_visitante(
            cuadro_b_local=cuadro_b_local,
            cuadro_b_visita=cuadro_b_visita,
            attack_local=attack_rating_local,
            attack_visita=attack_rating_visita,
        )
        es_gigante_visitante = bool(info_gv.get("activo", False))

    factor_def_rota_l = 1.35 if defense_rating_visita < 30.0 else 1.0
    factor_def_rota_v = 1.35 if defense_rating_local < 30.0 else 1.0

    # Evidencia independiente de asedio: exige cobertura real y (posesion >= 57% con toques_area >= 22, o posesion >= 60% con Defense_V < 42)
    tiene_asedio_verificado_l = bool(
        cobertura_asedio_real
        and (
            (posesion_proyectada_local >= 57.0 and toques_area_local is not None and float(toques_area_local) >= 22.0)
            or (posesion_proyectada_local >= 60.0 and defense_rating_visita < 42.0)
        )
    )
    factor_asedio_l = 1.18 if (tiene_asedio_verificado_l and posesion_proyectada_local >= 57.0 and (toques_area_local or 0.0) >= 22.0) else (
        1.15 if (tiene_asedio_verificado_l and posesion_proyectada_local >= 60.0 and defense_rating_visita < 42.0) else 1.0
    )

    # Punto 40: Prohibido apilar copa/eliminatoria con asedio/defensa rota (+68.75%):
    # Si es copa o torneo eliminatorio, el ajuste combinado está acotado estrictamente a <= 1.35x.
    es_copa = bool(ctx_comp.get("es_copa_o_playoff", False)) or factor_copa_l > 1.0
    if es_copa:
        ajuste_l = min(1.35, max(factor_copa_l, factor_asedio_l, factor_def_rota_l))
        ajuste_v = min(1.35, factor_def_rota_v)
    else:
        # En liga regular: Asedio (1.15x) y Defensa Rota rival (1.35x) aplican según Punto 22
        bono_ofensivo_l = factor_asedio_l
        ajuste_l = min(1.55, bono_ofensivo_l * factor_def_rota_l)
        ajuste_v = min(1.55, factor_def_rota_v)

    _al, _av = lam_l, lam_v
    lam_l *= ajuste_l
    lam_v *= ajuste_v
    _trazar(
        "copa_asedio_defensa_rota",
        _al, lam_l, _av, lam_v,
        f"copa={es_copa} (x{factor_copa_l:.2f}), asedio_L=x{factor_asedio_l:.2f}, "
        f"def_rota_V=x{factor_def_rota_l:.2f} (Def_V={defense_rating_visita:.1f}), "
        f"def_rota_L=x{factor_def_rota_v:.2f} (Def_L={defense_rating_local:.1f})",
    )

    # ── Sección 8.3: Regla 'Gigante Visitante' con orden exacto de operaciones ──
    if es_gigante_visitante:
        bono_localia_a_retirar = factor_copa_l if (ajuste_l == factor_copa_l and factor_copa_l > 1.0) else 1.0
        _al, _av = lam_l, lam_v
        res_gv = aplicar_ajuste_gigante_visitante(
            lam_local=lam_l,
            lam_visita=lam_v,
            bono_localia_aplicado=bono_localia_a_retirar,
            sede_neutral=bool(ctx_comp.get("sede_neutral", False)),
        )
        lam_l = float(res_gv["lam_local_ajustada"])
        lam_v = float(res_gv["lam_visita_ajustada"])
        _trazar("gigante_visitante", _al, lam_l, _av, lam_v, "Regla Gigante Visitante (retira localía y ajusta ratio)")

    # c) Amortiguador de "Fortín Defensivo Local" (Defense_Local >= 60.0):
    if defense_rating_local >= 60.0 and lam_v > 2.05:
        _av = lam_v
        lam_v = 1.95 + (lam_v - 1.95) * 0.40
        _trazar("fortin_defensivo_local", lam_l, lam_l, _av, lam_v, f"Def_L={defense_rating_local:.1f}>=60 comprime lam_V>2.05")

    # ── Punto 21b: Doble Freno de Mano Ofensivo (Local Débil y Visitante en Bloque Bajo "Autobús") ──
    # 1. Local con ataque paupérrimo:
    try:
        from decision_cobertura import cargar_umbrales_cobertura
        techo_atk_bajo = float(cargar_umbrales_cobertura().get("techo_lambda_ataque_bajo", 1.10))
    except Exception:
        techo_atk_bajo = 1.10

    if attack_rating_local < 25.0:
        _al = lam_l
        lam_l = min(lam_l, 0.90)
        _trazar("freno_local_ataque_muy_bajo_0.90", _al, lam_l, lam_v, lam_v, f"Attack_L={attack_rating_local:.1f}<25")
    if attack_rating_local < 20.0 and (en_crisis_local or goles_ult5_local < 0.50):
        _al = lam_l
        lam_l = min(lam_l, 0.60)
        _trazar("freno_local_crisis_extrema_0.60", _al, lam_l, lam_v, lam_v, f"Attack_L={attack_rating_local:.1f}<20 & crisis/gol_ult5={goles_ult5_local:.2f}")
    elif (en_crisis_local or attack_rating_local < 35.0) and defense_rating_visita >= 30.0:
        _al = lam_l
        lam_l = min(lam_l, techo_atk_bajo)
        _trazar("techo_lambda_ataque_bajo_local", _al, lam_l, lam_v, lam_v, f"en_crisis_L={en_crisis_local} o Attack_L={attack_rating_local:.1f}<35 con Def_V={defense_rating_visita:.1f}>=30 -> techo={techo_atk_bajo:.2f}")

    # 2. Visitante en Bloque Bajo ("Autobús"):
    if posesion_proyectada_visita < 45.0 and attack_rating_visita < 35.0:
        _av = lam_v
        lam_v = min(lam_v, 0.80)
        _trazar("freno_visita_bloque_bajo_0.80", lam_l, lam_l, _av, lam_v, f"Pos_V={posesion_proyectada_visita:.1f}<45 & Attack_V={attack_rating_visita:.1f}<35")
    if pressing_rating_visita < 30.0 and posesion_proyectada_visita < 42.0:
        _av = lam_v
        lam_v = min(lam_v, 0.60)
        _trazar("freno_visita_autobus_0.60", lam_l, lam_l, _av, lam_v, f"Press_V={pressing_rating_visita:.1f}<30 & Pos_V={posesion_proyectada_visita:.1f}<42")
    if attack_rating_visita < 20.0 and (en_crisis_visita or goles_ult5_visita < 0.50):
        _av = lam_v
        lam_v = min(lam_v, 0.60)
        _trazar("freno_visita_crisis_extrema_0.60", lam_l, lam_l, _av, lam_v, f"Attack_V={attack_rating_visita:.1f}<20 & crisis/gol_ult5={goles_ult5_visita:.2f}")
    elif (en_crisis_visita or attack_rating_visita < 35.0) and defense_rating_local >= 30.0:
        _av = lam_v
        lam_v = min(lam_v, techo_atk_bajo)
        _trazar("techo_lambda_ataque_bajo_visita", lam_l, lam_l, _av, lam_v, f"en_crisis_V={en_crisis_visita} o Attack_V={attack_rating_visita:.1f}<35 con Def_L={defense_rating_local:.1f}>=30 -> techo={techo_atk_bajo:.2f}")

    # Punto 50 (Ítem 3): Tope contra doble penalización acumulada (45-DCN + [EN CRISIS])
    # Cuando lambda_red < lambda_base (la red ya descontó la racha/jerarquía),
    # el factor multiplicativo post-red no puede recortar más de un -10% adicional
    # (lambda_final >= 0.90 * lambda_red), respetando un piso acumulado de
    # lambda_final >= 0.72 * lambda_base (salvo expulsión en vivo).
    if lam_red_local < lam_base_local:
        _al = lam_l
        piso_l = max(round(lam_red_local * 0.90, 2), round(lam_base_local * 0.72, 2))
        lam_l = max(lam_l, piso_l)
        _trazar("piso_doble_castigo_punto50_local", _al, lam_l, lam_v, lam_v, f"piso={piso_l:.2f} (0.90*red o 0.72*base)")

    if lam_red_visita < lam_base_visita:
        _av = lam_v
        piso_v = max(round(lam_red_visita * 0.90, 2), round(lam_base_visita * 0.72, 2))
        lam_v = max(lam_v, piso_v)
        _trazar("piso_doble_castigo_punto50_visita", lam_l, lam_l, _av, lam_v, f"piso={piso_v:.2f} (0.90*red o 0.72*base)")

    # ── Punto 21a: Techo de Bono en la Red Recursiva (Caja Azul) según Attack_Rating ──
    delta_pct_l = ((lam_l - lam_b_l) / lam_b_l) * 100.0
    delta_pct_v = ((lam_v - lam_b_v) / lam_b_v) * 100.0

    if attack_rating_local < 26.0:
        _al = lam_l
        delta_pct_l = min(0.0, delta_pct_l)
        lam_l = min(0.90, lam_b_l * (1.0 + delta_pct_l / 100.0))
        delta_pct_l = ((lam_l - lam_b_l) / lam_b_l) * 100.0
        _trazar("techo_caja_azul_local_26", _al, lam_l, lam_v, lam_v, f"Attack_L={attack_rating_local:.1f}<26 (delta<=0%, lam<=0.90)")
    elif attack_rating_local < 35.0 and defense_rating_visita >= 30.0:
        if delta_pct_l > 12.0:
            _al = lam_l
            delta_pct_l = 12.0
            lam_l = lam_b_l * (1.0 + delta_pct_l / 100.0)
            _trazar("techo_caja_azul_local_35_12pct", _al, lam_l, lam_v, lam_v, f"Attack_L={attack_rating_local:.1f}<35 limita bono<=+12%")

    if attack_rating_visita < 26.0:
        _av = lam_v
        delta_pct_v = min(0.0, delta_pct_v)
        lam_v = min(0.90, lam_b_v * (1.0 + delta_pct_v / 100.0))
        delta_pct_v = ((lam_v - lam_b_v) / lam_b_v) * 100.0
        _trazar("techo_caja_azul_visita_26", lam_l, lam_l, _av, lam_v, f"Attack_V={attack_rating_visita:.1f}<26 (delta<=0%, lam<=0.90)")
    elif attack_rating_visita < 35.0 and defense_rating_local >= 30.0:
        if delta_pct_v > 12.0:
            _av = lam_v
            delta_pct_v = 12.0
            lam_v = lam_b_v * (1.0 + delta_pct_v / 100.0)
            _trazar("techo_caja_azul_visita_35_12pct", lam_l, lam_l, _av, lam_v, f"Attack_V={attack_rating_visita:.1f}<35 limita bono<=+12%")

    # Prohibición de Inflación Dual:
    # La suma de los cambios porcentuales de ambos equipos en la Caja Azul jamás puede superar +25.0%
    # (y si ambos tienen Attack_Rating < 40.0, jamás puede superar +10.0%),
    # salvo que alguno enfrente una defensa rota (Defense_Rating < 30.0) o asedio extremo (Defense < 42 y Pos >= 60%).
    hay_defensa_rota_o_asedio = (
        defense_rating_local < 30.0
        or defense_rating_visita < 30.0
        or (posesion_proyectada_local >= 60.0 and defense_rating_visita < 42.0)
    )
    techo_suma_dual = 10.0 if (attack_rating_local < 40.0 and attack_rating_visita < 40.0 and not hay_defensa_rota_o_asedio) else (65.0 if hay_defensa_rota_o_asedio else 25.0)
    suma_actual = delta_pct_l + delta_pct_v
    if suma_actual > techo_suma_dual:
        _al, _av = lam_l, lam_v
        exceso_dual = suma_actual - techo_suma_dual
        pos_l = max(0.0, delta_pct_l)
        pos_v = max(0.0, delta_pct_v)
        tot_pos = pos_l + pos_v
        if tot_pos > 0:
            delta_pct_l -= exceso_dual * (pos_l / tot_pos)
            delta_pct_v -= exceso_dual * (pos_v / tot_pos)
        else:
            delta_pct_l -= exceso_dual * 0.5
            delta_pct_v -= exceso_dual * 0.5
        lam_l = lam_b_l * (1.0 + delta_pct_l / 100.0)
        lam_v = lam_b_v * (1.0 + delta_pct_v / 100.0)
        _trazar("prohibicion_inflacion_dual", _al, lam_l, _av, lam_v, f"suma={suma_actual:.1f}% > techo={techo_suma_dual:.1f}%")

    # Re-aplicar techos absolutos de Doble Freno de Mano tras ajuste porcentual
    if attack_rating_local < 26.0:
        _al = lam_l
        lam_l = min(lam_l, 0.90)
        _trazar("reaplicacion_techo_local_0.90", _al, lam_l, lam_v, lam_v, f"Attack_L={attack_rating_local:.1f}<26")
    if posesion_proyectada_visita < 45.0 and attack_rating_visita < 35.0:
        _av = lam_v
        lam_v = min(lam_v, 0.80)
        _trazar("reaplicacion_bloque_bajo_visita_0.80", lam_l, lam_l, _av, lam_v, f"Pos_V={posesion_proyectada_visita:.1f}<45 & Attack_V={attack_rating_visita:.1f}<35")
    if pressing_rating_visita < 30.0 and posesion_proyectada_visita < 42.0:
        _av = lam_v
        lam_v = min(lam_v, 0.60)
        _trazar("reaplicacion_autobus_visita_0.60", lam_l, lam_l, _av, lam_v, f"Press_V={pressing_rating_visita:.1f}<30 & Pos_V={posesion_proyectada_visita:.1f}<42")
    if defense_rating_local >= 60.0 and lam_v > 2.05:
        _av = lam_v
        lam_v = 1.95 + (lam_v - 1.95) * 0.40
        _trazar("reaplicacion_fortin_local", lam_l, lam_l, _av, lam_v, f"Def_L={defense_rating_local:.1f}>=60")

    # Re-asegurar piso contra doble penalización post-recorte dual
    if lam_red_local < lam_base_local:
        _al = lam_l
        piso_l = max(round(lam_red_local * 0.90, 2), round(lam_base_local * 0.72, 2))
        lam_l = max(lam_l, piso_l)
        _trazar("reaplicacion_piso_doble_castigo_local", _al, lam_l, lam_v, lam_v, f"piso={piso_l:.2f}")

    if lam_red_visita < lam_base_visita:
        _av = lam_v
        piso_v = max(round(lam_red_visita * 0.90, 2), round(lam_base_visita * 0.72, 2))
        lam_v = max(lam_v, piso_v)
        _trazar("reaplicacion_piso_doble_castigo_visita", lam_l, lam_l, _av, lam_v, f"piso={piso_v:.2f}")

    _al, _av = lam_l, lam_v
    lam_l = round(max(0.25, min(4.20, lam_l)), 2)
    lam_v = round(max(0.25, min(4.20, lam_v)), 2)
    _trazar("clamping_rango_y_redondeo", _al, lam_l, _av, lam_v, "Rango operativo [0.25, 4.20] y redondeo a 2 dec.")
    delta_pct_l = round(((lam_l - lam_b_l) / lam_b_l) * 100.0, 1)
    delta_pct_v = round(((lam_v - lam_b_v) / lam_b_v) * 100.0, 1)

    return lam_l, lam_v, delta_pct_l, delta_pct_v

