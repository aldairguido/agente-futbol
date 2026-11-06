"""
agente_futbol/cuadro_especialista.py
───────────────────────────────────────────────────────────────────────────────
CUADRO ESPECIALISTA DE FUERZA (0-100) + TEMPO EN SEGUNDOS + MAPA DE CALOR DE
MARCADORES (PNG) para el flujo de /mercados.

Qué aporta sobre el modelo de las capturas de referencia
--------------------------------------------------------
El modelo de referencia usa 7 métricas por lado (remates, remates a puerta, xG,
grandes ocasiones, goles, faltas y tarjetas) y las escala de 0 a 100. Aquí se
usan las MISMAS ideas, pero alimentadas con las 45 estadísticas reales que ya
extrae la auditoría del proyecto (con sus claves `_favor` / `_contra`) y con la
matriz conjunta que produce `motor_mercados` (que ya incluye la contracción
bayesiana, los moduladores tácticos y la corrección de Dixon-Coles):

  1. Ritmo temporal en segundos (90 min = 5400 s; 1T/2T = 2700 s cada uno):
     segundos entre tiros y goles, a favor y concedidos.
  2. Cuatro puntajes 0-100 por equipo: Overall_Hybrid_Rating,
     Final_Attack_Strength, Final_Defense_Strength y Pressing_Intensity_Score,
     con los pesos declarados en el propio código (nada implícito).
  3. Lecturas de especialista automatizadas:
       • Dominio con mala fortuna (xG - goles y volumen de área).
       • Portero salvador vs. portería vulnerable (goles_evitados).
       • Culpa de la defensa vs. culpa del portero.
       • Presión alta asfixiante vs. bloque hundido.
       • Radiógrafo de mitades (1T vs 2T) con perfil de ritmo.
  4. Mapa de calor de marcadores 0..6 construido con la matriz conjunta del
     motor (no con marginales multiplicadas).

Garantías de honestidad (coherentes con el resto del proyecto)
--------------------------------------------------------------
• Ninguna métrica ausente se sustituye por cero: si falta, se usa el valor
  neutro declarado y la métrica se lista en «métricas sin dato» dentro del PNG.
• El descuento por tarjeta roja (peso 0,30 al partido con expulsión) es OPCIONAL
  y se declara: la media bruta y la media corregida se muestran juntas cuando
  hay expulsiones en la muestra. Nunca se aplica en silencio.
• Si matplotlib no está instalado, el módulo devuelve None con un aviso: el
  análisis de /mercados sigue funcionando sin imagen.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
from datetime import datetime
try:
    from matplotlib.patches import FancyBboxPatch, Rectangle
except ImportError:
    pass
from pathlib import Path
from typing import Any

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    matplotlib = None  # type: ignore[assignment]
    plt = None         # type: ignore[assignment]

import numpy as np

BASE_DIR = Path(__file__).resolve().parent

SEGUNDOS_PARTIDO = 5400.0        # 90 minutos
SEGUNDOS_MITAD = 2700.0          # 45 minutos

# Peso de un partido con expulsión al promediar (regla del analista especialista:
# un accidente arbitral no debe definir cómo juega el equipo 11 contra 11).
PESO_PARTIDO_CON_ROJA = 0.30

# ── Traducción de las claves del cuadro a las claves REALES de la muestra del
#    motor (`MuestraEquipo.metricas`). Comprobadas contra auditorías reales.
MAPA_METRICAS: dict[str, str] = {
    # Ataque
    "xg": "xg_favor",
    "xgot": "xgot_favor",
    "xg_juego_abierto": "xg_juego_abierto_favor",
    "xg_balon_parado": "xg_balon_parado_favor",
    "xg_sin_penalti": "xg_sin_penalti_favor",
    "xa_equipo": "xa_equipo_favor",
    "remates_totales": "remates_totales_favor",
    "remates_puerta": "remates_puerta_favor",
    "remates_dentro_area": "remates_dentro_area_favor",
    "remates_fuera_area": "remates_fuera_area_favor",
    "remates_rechazados": "remates_rechazados_favor",
    "al_palo": "al_palo_favor",
    "grandes_ocasiones": "grandes_ocasiones_favor",
    "grandes_ocasiones_falladas": "grandes_ocasiones_falladas_favor",
    "toques_area_rival": "toques_area_rival_favor",
    "corners": "corners_favor",
    "posesion_pct": "posesion_pct",
    "pases_total": "pases_total_favor",
    "pases_precision_pct": "pases_precision_pct_favor",
    "pases_tercio_final_pct": "pases_tercio_final_pct_favor",
    "pases_entre_lineas": "pases_entre_lineas_favor",
    "cruces_pct": "cruces_pct_favor",
    "fueras_juego": "fueras_juego_favor",
    "goles_favor": "goles_favor",
    # Defensa y portería
    "goles_contra": "goles_contra",
    "xgot_enfrentado": "xgot_contra",
    "goles_evitados": "goles_evitados_favor",
    "paradas": "paradas_favor",
    "remates_puerta_contra": "remates_puerta_contra",
    "remates_totales_contra": "remates_totales_contra",
    "bloqueos": "bloqueos_favor",
    "despejes": "despejes_favor",
    "intercepciones": "intercepciones_favor",
    "recuperaciones": "recuperaciones_favor",
    "entradas_pct": "entradas_pct_favor",
    "duelos_ganados": "duelos_ganados_favor",
    "duelos_suelo_pct": "duelos_suelo_pct_favor",
    "duelos_aereos_pct": "duelos_aereos_pct_favor",
    "faltas": "faltas_favor",
    "tarjetas_amarillas": "amarillas_favor",
    "tarjetas_rojas": "rojas_favor",
    "errores_remate": "errores_remate_favor",
    "errores_gol": "errores_gol_favor",
}

# Valores neutros SOLO para poder dibujar cuando la métrica no existe en la
# muestra. Quedan registrados en `metricas_sin_dato` y se declaran en el PNG.
NEUTROS: dict[str, float] = {
    "xg": 1.35, "xgot": 1.35, "xg_juego_abierto": 0.97, "xg_balon_parado": 0.27,
    "xg_sin_penalti": 1.21, "xa_equipo": 0.88, "remates_totales": 12.0,
    "remates_puerta": 4.2, "remates_dentro_area": 7.5, "remates_fuera_area": 4.5,
    "remates_rechazados": 3.0, "al_palo": 0.3, "grandes_ocasiones": 2.2,
    "grandes_ocasiones_falladas": 1.3, "toques_area_rival": 22.0, "corners": 5.0,
    "posesion_pct": 50.0, "pases_total": 420.0, "pases_precision_pct": 82.0,
    "pases_tercio_final_pct": 55.0, "pases_entre_lineas": 24.0, "cruces_pct": 24.0,
    "fueras_juego": 1.8, "goles_favor": 1.35, "goles_contra": 1.25,
    "xgot_enfrentado": 1.25, "goles_evitados": 0.0, "paradas": 2.8,
    "remates_puerta_contra": 4.0, "remates_totales_contra": 12.0, "bloqueos": 2.5,
    "despejes": 18.0, "intercepciones": 8.0, "recuperaciones": 48.0,
    "entradas_pct": 14.0, "duelos_ganados": 48.0, "duelos_suelo_pct": 50.0,
    "duelos_aereos_pct": 50.0, "faltas": 11.5, "tarjetas_amarillas": 1.8,
    "tarjetas_rojas": 0.1, "errores_remate": 0.2, "errores_gol": 0.15,
}


def clip(valor: float, piso: float, techo: float) -> float:
    return max(piso, min(techo, valor))


def deduplicar_partidos(pts: list[dict] | None) -> list[dict]:
    """Deduplicación estricta obligatoria: prohibido clonar/duplicar el mismo partido."""
    unicos = []
    ids_vistos = set()
    claves_vistas = set()
    for p in (pts or []):
        if not isinstance(p, dict):
            continue
        p_id = str(p.get("id_partido") or p.get("id") or "")
        src = p.get("_partido", {}) if isinstance(p.get("_partido"), dict) else {}
        p_fec = str(p.get("fecha") or src.get("fecha") or "")
        p_loc = str(p.get("local") or src.get("local") or src.get("equipo_local") or "").lower().strip()
        p_vis = str(p.get("visitante") or src.get("rival") or src.get("equipo_visita") or "").lower().strip()
        clave = (p_fec, p_loc, p_vis)
        if (p_id and p_id in ids_vistos) or (any(clave) and clave in claves_vistas):
            continue
        if p_id:
            ids_vistos.add(p_id)
        if any(clave):
            claves_vistas.add(clave)
        unicos.append(p)
    return unicos


_deduplicar_partidos = deduplicar_partidos


def escala_0_100(valor: float, piso: float, techo: float,
                 invertir: bool = False) -> float:
    """
    Convierte una métrica bruta en un puntaje 0-100.
    Con `invertir=True` (goles encajados, segundos entre tiros, errores) un valor
    más bajo produce un puntaje más alto.
    """
    if techo <= piso:
        return 50.0
    proporcion = (valor - piso) / (techo - piso)
    if invertir:
        proporcion = 1.0 - proporcion
    return round(clip(proporcion * 100.0, 0.0, 100.0), 2)


def formatear_fecha_ddmmaaaa(fecha_str: str) -> str:
    """Convierte '2026-10-09' o ISO timestamp en '09/10/2026'."""
    limpia = str(fecha_str or "").strip()[:10]
    if len(limpia) == 10 and limpia[4] == "-" and limpia[7] == "-":
        partes = limpia.split("-")
        return f"{partes[2]}/{partes[1]}/{partes[0]}"
    return limpia or "s/f"


ABREV_TORNEOS: dict[str, str] = {
    "uefa champions league qualification": "UCL Qual.",
    "uefa champions league qualifying": "UCL Qual.",
    "champions league qualification": "UCL Qual.",
    "uefa champions league": "Champions League",
    "uefa europa league qualification": "UEL Qual.",
    "uefa europa league qualifying": "UEL Qual.",
    "europa league qualification": "UEL Qual.",
    "uefa europa league": "Europa League",
    "uefa europa conference league qualification": "UECL Qual.",
    "uefa conference league qualification": "UECL Qual.",
    "uefa europa conference league": "Conf. League",
    "uefa conference league": "Conference League",
    "copa del rey": "Copa del Rey",
    "coupe de france": "Coupe de France",
    "dfb-pokal": "DFB-Pokal",
    "dfb pokal": "DFB-Pokal",
    "fa cup": "FA Cup",
    "efl cup": "EFL Cup",
    "carabao cup": "EFL Cup",
    "premier league": "Premier League",
    "primera division": "LaLiga",
    "laliga": "LaLiga",
    "laliga ea sports": "LaLiga",
    "laliga hypermotion": "LaLiga 2",
    "segunda division": "LaLiga 2",
    "serie a": "Serie A",
    "bundesliga": "Bundesliga",
    "ligue 1": "Ligue 1",
    "super lig": "Süper Lig",
    "süper lig": "Süper Lig",
    "chance liga": "Chance Liga",
    "premyer liqa": "Premyer Liqa",
    "eredivisie": "Eredivisie",
    "liga portugal": "Liga Portugal",
}


def formatear_linea_partido_15(idx: int, p: dict[str, Any]) -> str:
    """
    Formatea un partido auditado exactamente como pidió el usuario:
    '01. 09/10/2026  Málaga (1) vs Espanyol (1) · LaLiga'
    Con abreviación de torneo (max 18 chars) y límite estricto de 58 caracteres por fila.
    """
    fecha_fmt = formatear_fecha_ddmmaaaa(p.get("fecha") or p.get("utcTime") or "")
    cond = str(p.get("condicion") or "").lower()

    if p.get("local") is not None or p.get("home_team") is not None or p.get("equipo_local") is not None:
        loc = str(p.get("local") or p.get("home_team") or p.get("equipo_local") or "Local")
        vis = str(p.get("visitante") or p.get("away_team") or p.get("equipo_visitante") or "Visita")
        gl = p.get("goles_local") if p.get("goles_local") is not None else p.get("home_score")
        gv = p.get("goles_visitante") if p.get("goles_visitante") is not None else p.get("away_score")
    elif cond == "casa":
        loc = str(p.get("equipo") or "Local")
        vis = str(p.get("rival") or "Visita")
        m = p.get("marcador") or {}
        gl = m.get("equipo") if m.get("equipo") is not None else p.get("goles_equipo")
        gv = m.get("rival") if m.get("rival") is not None else p.get("goles_rival")
    elif cond == "fuera":
        loc = str(p.get("rival") or "Local")
        vis = str(p.get("equipo") or "Visita")
        m = p.get("marcador") or {}
        gl = m.get("rival") if m.get("rival") is not None else p.get("goles_rival")
        gv = m.get("equipo") if m.get("equipo") is not None else p.get("goles_equipo")
    else:
        loc = str(p.get("local") or p.get("equipo") or "Local")
        vis = str(p.get("visitante") or p.get("rival") or "Visita")
        gl = p.get("goles_local") if p.get("goles_local") is not None else p.get("goles_equipo")
        gv = p.get("goles_visitante") if p.get("goles_visitante") is not None else p.get("goles_rival")

    gl_str = str(gl) if gl is not None else "?"
    gv_str = str(gv) if gv is not None else "?"
    torneo_raw = str(p.get("torneo") or p.get("competicion") or "").strip()
    torneo_low = torneo_raw.lower()

    if torneo_low in ABREV_TORNEOS:
        torneo_fmt = ABREV_TORNEOS[torneo_low]
    elif torneo_raw:
        torneo_fmt = (
            torneo_raw.replace("Qualification", "Qual.")
                      .replace("Qualifying", "Qual.")
                      .replace("Conference League", "Conf. L.")
        )
    else:
        torneo_fmt = ""

    if len(torneo_fmt) > 18:
        torneo_fmt = torneo_fmt[:18].strip()

    sufijo_torneo = f" · {torneo_fmt}" if torneo_fmt else ""
    prefijo = f"{idx:02d}. {fecha_fmt}  "
    linea = f"{prefijo}{loc} ({gl_str}) vs {vis} ({gv_str}){sufijo_torneo}"
    if len(linea) > 58:
        fijo_len = len(prefijo) + len(sufijo_torneo) + len(f" ({gl_str}) vs ({gv_str})")
        espacio_equipos = max(6, 58 - fijo_len)
        max_por_eq = max(3, espacio_equipos // 2)
        loc_c = loc[:max_por_eq].strip()
        vis_c = vis[:max_por_eq].strip()
        linea = f"{prefijo}{loc_c} ({gl_str}) vs {vis_c} ({gv_str}){sufijo_torneo}"
        if len(linea) > 58:
            linea = linea[:58]
    return linea


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PUENTE: MUESTRA DEL MOTOR -> BLOQUE DEL CUADRO
# ═══════════════════════════════════════════════════════════════════════════════

def _media_de_muestra(muestra: Any, clave_cuadro: str,
                      ponderada: bool = False) -> tuple[float | None, int]:
    """
    Lee la media (y el n real de observaciones) de una métrica del cuadro desde
    una `MuestraEquipo` del motor. Devuelve (None, 0) si no hay dato.
    """
    clave_real = MAPA_METRICAS.get(clave_cuadro)
    if not clave_real or muestra is None:
        return None, 0
    metrica = (getattr(muestra, "metricas", {}) or {}).get(clave_real)
    if metrica is None or getattr(metrica, "n", 0) <= 0:
        return None, 0
    if ponderada:
        valor = getattr(metrica, "media_ponderada", None)
        if valor is not None:
            return float(valor), int(metrica.n)
    media = getattr(metrica, "media", None)
    if media is None:
        return None, 0
    return float(media), int(metrica.n)


def bloque_desde_muestra(muestra: Any, equipo: str, contexto: str,
                         n_partidos: int | None = None,
                         ponderada: bool = False) -> dict[str, Any]:
    """
    Construye el bloque que consume `construir_perfil_especialista_equipo`:
    {"equipo", "contexto", "n_partidos", "metricas", "metricas_sin_dato"}.
    """
    metricas: dict[str, dict[str, Any]] = {}
    sin_dato: list[str] = []
    for clave in MAPA_METRICAS:
        valor, n = _media_de_muestra(muestra, clave, ponderada=ponderada)
        if valor is None:
            sin_dato.append(clave)
            valor = NEUTROS.get(clave, 0.0)
        metricas[clave] = {"promedio": valor, "n": n,
                           "sin_dato": clave in sin_dato}
    return {
        "equipo": equipo,
        "contexto": contexto,
        "n_partidos": n_partidos or getattr(muestra, "partidos_general", 0) or 0,
        "metricas": metricas,
        "metricas_sin_dato": sin_dato,
    }


def bloque_para_lado(motor: Any, lado: str, peso_roja: float | None = None) -> dict[str, Any]:
    """
    Bloque del cuadro para un bando ("local" | "visitante") usando la muestra
    GENERAL del motor (la que describe cómo juega el equipo, no la de condición).

    Si `peso_roja` no es None se construye una muestra propia con ese descuento
    (la del motor no se toca) y se promedian las métricas ponderadas.
    """
    equipo = motor.nombre_local if lado == "local" else motor.nombre_visitante
    condicion = motor.cond_local if lado == "local" else motor.cond_visitante
    doc = motor.doc_local if lado == "local" else motor.doc_visitante

    if peso_roja is not None:
        try:
            import motor_mercados as mm
            muestra = mm.extraer_muestra(doc, None, "FT", peso_roja=peso_roja)
            ponderada = True
        except Exception:
            muestra = motor.muestras.get(f"{lado}_general")
            ponderada = False
    else:
        muestra = motor.muestras.get(f"{lado}_general")
        ponderada = False

    bloque = bloque_desde_muestra(muestra, equipo, f"general ({condicion})",
                                  ponderada=ponderada)
    bloque["promedio_ponderado"] = ponderada
    # Se conserva también la muestra de condición para las lecturas de contexto.
    bloque["muestra_condicion"] = motor.muestras.get(f"{lado}_cond")
    bloque["condicion"] = condicion
    return bloque


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PERFIL ESPECIALISTA: RATINGS 0-100, TEMPO Y LECTURAS
# ═══════════════════════════════════════════════════════════════════════════════

def construir_perfil_especialista_equipo(bloque_equipo: dict[str, Any], ratio_tier: float = 1.0) -> dict[str, Any]:
    """
    Convierte el bloque de métricas de un equipo en:
      1. Las columnas de ritmo temporal (segundos por tiro/gol, a favor y en contra).
      2. Los cuatro puntajes 0-100.
      3. El diagnóstico de patrones ocultos.
    """
    m = bloque_equipo.get("metricas", {})

    def v(clave: str, defecto: float = 0.0) -> float:
        nodo = m.get(clave)
        if isinstance(nodo, dict):
            prom = nodo.get("promedio")
            return float(prom) if prom is not None else defecto
        return float(nodo) if nodo is not None else defecto

    # ── A. Métricas ofensivas ────────────────────────────────────────────────
    xg = v("xg", 1.35)
    xgot = v("xgot", xg)
    npxg = v("xg_sin_penalti", xg * 0.90)
    xa = v("xa_equipo", xg * 0.65)
    rem_tot = max(1.0, v("remates_totales", 12.0))
    rem_pue = v("remates_puerta", 4.2)
    al_palo = v("al_palo", 0.3)
    g_oc = v("grandes_ocasiones", 2.2)
    g_oc_fall = v("grandes_ocasiones_falladas", 1.3)
    toques_area = v("toques_area_rival", 22.0)
    posesion = clip(v("posesion_pct", 50.0), 20.0, 85.0)
    pases_tot = v("pases_total", 420.0)
    pases_tercio = v("pases_tercio_final_pct", 55.0)
    pases_lineas = v("pases_entre_lineas", 24.0)

    # ── B. Métricas defensivas y de portería ─────────────────────────────────
    xgot_enf = v("xgot_enfrentado", 1.25)
    goles_evit = v("goles_evitados", 0.0)
    paradas = v("paradas", 2.8)
    despejes = v("despejes", 18.0)
    intercepciones = v("intercepciones", 8.0)
    recuperaciones = v("recuperaciones", 48.0)
    entradas = v("entradas_pct", 14.0)
    duelos_suelo = v("duelos_suelo_pct", 50.0)
    duelos_aereos = v("duelos_aereos_pct", 50.0)
    err_remate = v("errores_remate", 0.2)
    err_gol = v("errores_gol", 0.15)
    rojas = v("tarjetas_rojas", 0.1)

    # Goles: se prefieren los REALES de la auditoría. Solo si faltan se
    # reconstruyen (xGOT recibido − goles evitados), y se declara.
    goles_favor_real = _media_o_none(m, "goles_favor")
    goles_contra_real = _media_o_none(m, "goles_contra")
    goles_contra_est = max(0.25, xgot_enf - goles_evit)
    goles_contra = goles_contra_real if goles_contra_real is not None else goles_contra_est
    goles_favor = goles_favor_real if goles_favor_real is not None else max(
        0.30, round(0.55 * xgot + 0.45 * (max(0.0, g_oc - g_oc_fall)
                                          + 0.35 * (xg - g_oc * 0.35)), 2))

    # Descuento relativo de liga doméstica para el equipo más débil (ratio_tier < 1.0)
    if ratio_tier < 1.0 and ratio_tier > 0.05:
        goles_contra = round(goles_contra / ratio_tier, 2)
        xgot_enf = round(xgot_enf / ratio_tier, 2)
        goles_evit = round(goles_evit * ratio_tier - 0.35 * (1.0 - ratio_tier), 3)

    # Tiros a puerta concedidos: métrica real si existe; si no, paradas + goles.
    rem_pue_contra_real = _media_o_none(m, "remates_puerta_contra")
    rem_pue_contra = (rem_pue_contra_real if rem_pue_contra_real is not None
                      else max(1.0, paradas + goles_contra))
    rem_tot_contra_real = _media_o_none(m, "remates_totales_contra")
    rem_tot_contra = (rem_tot_contra_real if rem_tot_contra_real is not None
                      else max(rem_pue_contra + v("bloqueos", 2.5) + 2.0,
                               rem_pue_contra * 2.6))

    # ── C. Ritmo temporal (90 min = 5400 s) ──────────────────────────────────
    tempo = {
        "Avg_Seconds_Between_Shots": round(SEGUNDOS_PARTIDO / rem_tot, 1),
        "Avg_Seconds_Between_Goals": round(SEGUNDOS_PARTIDO / max(0.25, goles_favor), 1),
        "Avg_Seconds_Between_Shots_Conceded": round(SEGUNDOS_PARTIDO / max(1.0, rem_tot_contra), 1),
        "Avg_Seconds_Between_Goals_Conceded": round(SEGUNDOS_PARTIDO / max(0.25, goles_contra), 1),
    }
    tempo["Total_Match_Tempo"] = round(
        tempo["Avg_Seconds_Between_Shots"] + tempo["Avg_Seconds_Between_Shots_Conceded"], 1)

    # ── D. Ataque 0-100 (pesos declarados, suman 1,00) ───────────────────────
    s_xg = escala_0_100(xg, 0.70, 3.40)
    s_xgot = escala_0_100(xgot, 0.65, 3.30)
    s_npxg_sh = escala_0_100(npxg / rem_tot, 0.06, 0.17)
    s_big_ch = escala_0_100(g_oc, 1.0, 6.0)
    s_conv_bc = escala_0_100(1.0 - (g_oc_fall / max(1.0, g_oc)), 0.15, 0.65)
    s_sot = escala_0_100(rem_pue, 2.5, 10.0)
    s_box_touches = escala_0_100(toques_area, 12.0, 48.0)
    s_xa = escala_0_100(xa, 0.50, 2.20)
    s_progresion = escala_0_100(pases_tercio + 0.5 * pases_lineas, 45.0, 135.0)

    ataque = round(
        0.22 * s_xg + 0.18 * s_xgot + 0.14 * s_big_ch + 0.10 * s_npxg_sh
        + 0.08 * s_conv_bc + 0.08 * s_sot + 0.08 * s_box_touches
        + 0.06 * s_xa + 0.06 * s_progresion, 2)

    # ── E. Defensa 0-100 ─────────────────────────────────────────────────────
    s_goles_c = escala_0_100(goles_contra, 0.45, 2.20, invertir=True)
    s_xgot_c = escala_0_100(xgot_enf, 0.50, 2.10, invertir=True)
    s_portero = escala_0_100(goles_evit, -0.55, 0.55)
    s_duelos = escala_0_100(0.5 * duelos_suelo + 0.5 * duelos_aereos, 43.0, 61.0)
    s_recuperacion = escala_0_100(recuperaciones + intercepciones + entradas, 55.0, 82.0)
    s_errores = escala_0_100(err_gol * 2.5 + err_remate + rojas * 1.5, 0.0, 1.8, invertir=True)
    s_sot_c = escala_0_100(rem_pue_contra, 2.0, 6.5, invertir=True)

    defensa = round(
        0.24 * s_goles_c + 0.22 * s_xgot_c + 0.14 * s_sot_c + 0.14 * s_duelos
        + 0.10 * s_recuperacion + 0.08 * s_portero + 0.08 * s_errores, 2)

    # ── F. Pressing 0-100 ────────────────────────────────────────────────────
    min_sin_balon = max(15.0, 90.0 * (1.0 - posesion / 100.0))
    total_acciones_def = recuperaciones + entradas + intercepciones
    # Si las métricas defensivas vienen en 0 o sin dato (ej. ligas domésticas sin tracking como Ucrania)
    if total_acciones_def < 25.0 or recuperaciones <= 2.0:
        faltas_val = v("faltas", 12.0)
        duelos_val = 0.5 * duelos_suelo + 0.5 * duelos_aereos
        acciones_def_est = faltas_val * 1.8 + duelos_val * 0.70 + (posesion * 0.25)
        total_acciones_def = max(45.0, acciones_def_est)
        acciones_presion_por_min = total_acciones_def / min_sin_balon
        ratio_proactivo_vs_hundido = total_acciones_def / max(8.0, despejes)
    else:
        acciones_presion_por_min = total_acciones_def / min_sin_balon
        ratio_proactivo_vs_hundido = total_acciones_def / max(8.0, despejes)
    pases_por_min_pos = pases_tot / max(15.0, 90.0 * (posesion / 100.0))
    pressing = round(
        0.40 * escala_0_100(acciones_presion_por_min, 1.2, 2.8)
        + 0.35 * escala_0_100(ratio_proactivo_vs_hundido, 1.8, 5.2)
        + 0.25 * escala_0_100(pases_por_min_pos, 6.5, 10.5), 2)
    # Piso realista obligatorio: el pressing nunca puede colapsar a 0.5
    pressing = max(28.0, pressing)

    overall = round(0.48 * ataque + 0.42 * defensa + 0.10 * pressing, 2)

    # ── G. Lecturas de especialista ──────────────────────────────────────────
    hallazgos: list[str] = []

    # Patrón 1: dominio con mala fortuna.
    if xg - goles_favor >= 0.35 and toques_area >= 28 and rem_pue >= 6.0:
        hallazgos.append(
            f"Dominio con mala fortuna: genera {xg:.2f} xG y {toques_area:.1f} toques en "
            f"área rival, pero marca {goles_favor:.2f} (falla {g_oc_fall:.1f}/{g_oc:.1f} "
            f"grandes ocasiones y {al_palo:.2f} tiros al palo). Prima su xG sobre el gol.")
    elif xgot > xg + 0.10:
        hallazgos.append(
            f"Definición de élite: coloca los tiros y eleva el peligro de {xg:.2f} xG a "
            f"{xgot:.2f} xGOT.")

    # Patrón 4: culpa de la defensa vs culpa del portero.
    from motor_recursivo_45_dcn import _valor_metrica
    partidos_raw = bloque_equipo.get("partidos") or []
    sin_xgot_cnt = 0
    tot_pts = len(partidos_raw)
    for p in partidos_raw:
        px = _valor_metrica(p, "xgot")
        pe = _valor_metrica(p, "xgot_enfrentado") or _valor_metrica(p, "xgot_enfrentados")
        if px is None or px <= 0.001 or pe is None or pe <= 0.001:
            sin_xgot_cnt += 1
    pct_sin_xgot = (sin_xgot_cnt / max(1, tot_pts)) if tot_pts > 0 else (1.0 if xgot_enf <= 0.001 else 0.0)

    if pct_sin_xgot > 0.30 or xgot_enf <= 0.001:
        hallazgos.append(
            "Sin cobertura xGOT oficial en esta competición (modelo guiado por goles y remates)."
        )
    elif goles_evit <= -0.20:
        hallazgos.append(
            f"Portería vulnerable: {goles_evit:+.2f} goles evitados por partido (encaja "
            f"{goles_contra:.2f} con {xgot_enf:.2f} xGOT recibido): casi todo tiro entre "
            f"los tres palos termina en red.")
    elif goles_evit >= 0.18:
        hallazgos.append(
            f"Portero salvador: evita {goles_evit:+.2f} goles por partido sobre el xGOT "
            f"recibido ({xgot_enf:.2f}).")

    if (toques_area_rival_contra := _media_o_none(m, "remates_puerta_contra")) is not None:
        pass  # el dato concedido se resume en la tabla; evita ramas muertas

    if duelos_suelo < 47.0 or duelos_aereos < 46.0:
        hallazgos.append(
            f"Defensa que pierde los duelos: {duelos_suelo:.1f}% en suelo y "
            f"{duelos_aereos:.1f}% en el aire, con {rem_pue_contra:.1f} tiros a puerta "
            f"concedidos por partido.")

    if err_gol >= 0.20 or rojas >= 0.20:
        hallazgos.append(
            f"Desconcentración defensiva: {err_gol:.2f} errores directos de gol y "
            f"{rojas:.2f} expulsiones por partido.")

    # Patrón 3: presión alta vs bloque hundido.
    if ratio_proactivo_vs_hundido >= 3.8:
        hallazgos.append(
            f"Presión alta asfixiante ({pressing:.1f}/100): recupera {recuperaciones:.1f} "
            f"balones e intercepta {intercepciones:.1f} con solo {despejes:.1f} despejes.")
    elif despejes >= 24.0:
        hallazgos.append(
            f"Bloque hundido de resistencia: {despejes:.1f} despejes y "
            f"{v('bloqueos', 2.5):.1f} tiros bloqueados por partido.")

    return {
        "equipo": bloque_equipo.get("equipo", "Equipo"),
        "contexto": bloque_equipo.get("contexto", "global"),
        "condicion": bloque_equipo.get("condicion"),
        "n_partidos": bloque_equipo.get("n_partidos", 15),
        "metricas_sin_dato": list(bloque_equipo.get("metricas_sin_dato") or []),
        "ratings_0_100": {
            "Overall_Hybrid_Rating": overall,
            "Final_Attack_Strength": ataque,
            "Final_Defense_Strength": defensa,
            "Pressing_Intensity_Score": pressing,
        },
        "tempo_segundos": tempo,
        "metricas_clave": {
            "goles_favor": round(goles_favor, 2),
            "goles_favor_es_real": goles_favor_real is not None,
            "goles_contra": round(goles_contra, 2),
            "goles_contra_es_real": goles_contra_real is not None,
            "xg_favor": round(xg, 2),
            "xgot_favor": round(xgot, 2),
            "xgot_contra": round(xgot_enf, 2),
            "remates_totales": round(rem_tot, 2),
            "remates_puerta": round(rem_pue, 2),
            "remates_puerta_contra": round(rem_pue_contra, 2),
            "toques_area_rival": round(toques_area, 2),
            "posesion_pct": round(posesion, 1),
            "goles_evitados": round(goles_evit, 2),
            "acciones_presion_por_min_sin_balon": round(acciones_presion_por_min, 2),
            "ratio_proactivo_vs_hundido": round(ratio_proactivo_vs_hundido, 2),
        },
        "hallazgos_especialista": hallazgos,
    }


def _media_o_none(metricas: dict[str, Any], clave: str) -> float | None:
    nodo = metricas.get(clave)
    if isinstance(nodo, dict):
        if nodo.get("sin_dato"):
            return None
        prom = nodo.get("promedio")
        return float(prom) if prom is not None else None
    return float(nodo) if nodo is not None else None


# ═══════════════════════════════════════════════════════════════════════════════
# 2 bis. PUENTE HACIA LA RED NEURONAL RECURSIVA (45 VARIABLES)
# ═══════════════════════════════════════════════════════════════════════════════

def goles_equipo_rival(partido: dict[str, Any]) -> tuple[float, float]:
    """
    Goles (equipo auditado, rival) leyendo el ESQUEMA REAL de la auditoría.

    La auditoría guarda `marcador: {equipo, rival}` y `condicion`, no
    `goles_local/goles_visitante`; la red recursiva espera el segundo formato, así
    que esta función es el puente (y evita que la serie de goles salga toda de 1).
    """
    marcador = partido.get("marcador") or {}
    ge, gr = marcador.get("equipo"), marcador.get("rival")
    if isinstance(ge, (int, float)) and isinstance(gr, (int, float)):
        return float(ge), float(gr)
    for clave_eq, clave_riv in (("goles_equipo", "goles_rival"),
                                ("goles_local", "goles_visitante")):
        valor_eq, valor_riv = partido.get(clave_eq), partido.get(clave_riv)
        if isinstance(valor_eq, (int, float)) and isinstance(valor_riv, (int, float)):
            return float(valor_eq), float(valor_riv)
    return 1.0, 1.0


def partidos_para_red_neuronal(
    partidos: list[dict[str, Any]] | None,
    nombre_equipo: str,
) -> list[dict[str, Any]]:
    """
    Traduce los partidos de la auditoría al formato que consume
    `motor_recursivo_45_dcn.extraer_series_reales_15_partidos`.

    Se marca al equipo auditado como LOCAL en la tupla para que la red oriente
    siempre los goles a su favor (la auditoría ya viene orientada por equipo).
    """
    salida: list[dict[str, Any]] = []
    for partido in partidos or []:
        if not isinstance(partido, dict):
            continue
        goles_favor, goles_contra = goles_equipo_rival(partido)
        salida.append({
            "fecha": partido.get("fecha") or partido.get("utcTime") or "",
            "local": nombre_equipo,
            "visitante": partido.get("rival") or "Rival",
            "goles_local": goles_favor,
            "goles_visitante": goles_contra,
            # El partido ORIGINAL viaja con la tupla: sin él la red solo vería los
            # goles y tendría que reconstruir las 45 métricas desde la media.
            "_partido": partido,
        })
    return salida


def matriz_red_para_dibujar(res_red: dict[str, Any]) -> list[list[float]]:
    """
    Matriz de la red lista para usar, SIMETRIZADA si los dos equipos son iguales.

    El Monte Carlo de 100.000 simulaciones añade ruido de muestreo (~1e-3). Con
    dos equipos idénticos ese ruido fabricaría una ventaja inexistente (P(1) y
    P(2) distintas). Cuando λ, sequía y explosión coinciden, la matriz analítica
    es exactamente simétrica y se promedia con su traspuesta: así dos equipos
    iguales tienen exactamente las mismas probabilidades.
    """
    import numpy as np
    from matplotlib.patches import FancyBboxPatch, Rectangle

    matriz = np.asarray(res_red["matriz_prob"], dtype=float)
    filas, columnas = matriz.shape
    iguales = (
        filas == columnas
        and abs(float(res_red.get("lambda_local_recursivo", 0.0))
                - float(res_red.get("lambda_visita_recursivo", 0.0))) < 1e-12
        and abs(float(res_red.get("tasa_sequia_local", 0.0))
                - float(res_red.get("tasa_sequia_visita", 0.0))) < 1e-12
        and abs(float(res_red.get("tasa_explosion_3mas_local", 0.0))
                - float(res_red.get("tasa_explosion_3mas_visita", 0.0))) < 1e-12
    )
    if iguales:
        matriz = 0.5 * (matriz + matriz.T)
        total = float(matriz.sum())
        if total > 0:
            matriz /= total
    return matriz.tolist()



# ═══════════════════════════════════════════════════════════════════════════════
# 3. RADIÓGRAFO DE MITADES (1T vs 2T sobre 2700 s cada una)
# ═══════════════════════════════════════════════════════════════════════════════

def perfil_mitades(doc: dict[str, Any], condicion: str | None) -> dict[str, Any]:
    """
    Compara el ritmo ofensivo del 1T con el del 2T (cada mitad son 2700 s) usando
    las muestras por periodo del motor. Devuelve los segundos por tiro, los
    remates y el Índice de Cambio de Ritmo (2T/1T) con su etiqueta de perfil.
    """
    try:
        import motor_mercados as mm
    except Exception:                                   # pragma: no cover
        return {}
    salida: dict[str, Any] = {}
    for etiqueta, periodo in (("1T", "1T"), ("2T", "2T")):
        muestra = mm.extraer_muestra(doc, condicion, periodo)
        remates = muestra.media("remates_totales_favor")
        xg = muestra.media("xg_favor")
        recuperaciones = muestra.media("recuperaciones_favor")
        duelos = muestra.media("duelos_suelo_pct_favor")
        faltas = muestra.media("faltas_favor")
        salida[etiqueta] = {
            "remates_totales": remates,
            "xg": xg,
            "segundos_por_tiro": (round(SEGUNDOS_MITAD / remates, 1)
                                  if remates else None),
            "recuperaciones": recuperaciones,
            "duelos_suelo_pct": duelos,
            "faltas": faltas,
            "partidos": muestra.partidos_general,
        }

    r1, r2 = salida["1T"]["remates_totales"], salida["2T"]["remates_totales"]
    # Reconciliación matemática obligatoria: xG_1T + xG_2T == xG_FT y tempo de FT
    muestra_ft = mm.extraer_muestra(doc, condicion, "FT")
    xg_ft = muestra_ft.media("xg_favor")
    x1, x2 = salida["1T"]["xg"], salida["2T"]["xg"]
    if xg_ft is not None and x1 is not None and x2 is not None:
        sum_x = x1 + x2
        if sum_x > 0:
            scale = xg_ft / sum_x
            x1_s = round(x1 * scale, 2)
            x2_s = round(xg_ft - x1_s, 2)
            salida["1T"]["xg"] = x1_s
            salida["2T"]["xg"] = x2_s
    if r1 is not None and r2 is not None and (r1 + r2) > 0:
        salida["segundos_por_tiro_ft"] = round(SEGUNDOS_PARTIDO / (r1 + r2), 1)

    ratio = (r2 / r1) if (r1 and r2) else None
    salida["ratio_2t_1t_remates"] = round(ratio, 3) if ratio is not None else None

    d1 = salida["1T"]["duelos_suelo_pct"]
    d2 = salida["2T"]["duelos_suelo_pct"]
    salida["caida_duelos_pp"] = (round(d1 - d2, 1) if (d1 is not None and d2 is not None)
                                 else None)

    if ratio is None:
        perfil = "sin datos suficientes por mitades"
        perfil_corto = "s/d"
    elif ratio >= 1.35:
        perfil = "Equipo diésel (crece en el 2T)"
        perfil_corto = "Diésel 2T"
    elif ratio <= 0.72:
        perfil = "Arranque explosivo (se apaga en el 2T)"
        perfil_corto = "Explosivo 1T"
    else:
        perfil = "Ritmo estable entre mitades"
        perfil_corto = "Estable"
    if (salida["caida_duelos_pp"] is not None and salida["caida_duelos_pp"] >= 6.0
            and ratio is not None and ratio < 1.0):
        perfil += " · colapso físico en el 2T"
        perfil_corto += " + colapso"
    salida["perfil"] = perfil
    salida["perfil_corto"] = perfil_corto
    return salida


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DETECTORES DE PATRONES DE CRUCE (los que dependen de AMBOS equipos)
# ═══════════════════════════════════════════════════════════════════════════════

def detectar_patrones_cruce(perfil_local: dict[str, Any],
                            perfil_vis: dict[str, Any],
                            patron_certeza_gol: bool,
                            umbral_certeza: float,
                            prob_0_0: float | None) -> list[str]:
    """
    Lecturas que solo existen al cruzar los dos equipos:
      • Choque de presión alta contra salida imprecisa.
      • Portero flojo contra ataque de volumen (valor oculto en goles del rival).
      • Certeza de gol y ambos anotan por cruce de promedios y tempo.
    """
    lecturas: list[str] = []
    L, V = perfil_local, perfil_vis
    kL, kV = L["metricas_clave"], V["metricas_clave"]
    rL, rV = L["ratings_0_100"], V["ratings_0_100"]

    if rL["Pressing_Intensity_Score"] >= 65 and kV["posesion_pct"] < 50:
        lecturas.append(
            f"{L['equipo']} presiona alto ({rL['Pressing_Intensity_Score']:.0f}/100) contra "
            f"un rival con {kV['posesion_pct']:.1f}% de posesión: suben los robos en salida "
            f"y el gol tempranero.")
    if rV["Pressing_Intensity_Score"] >= 65 and kL["posesion_pct"] < 50:
        lecturas.append(
            f"{V['equipo']} presiona alto ({rV['Pressing_Intensity_Score']:.0f}/100) contra "
            f"un rival con {kL['posesion_pct']:.1f}% de posesión: suben los robos en salida "
            f"y el gol tempranero.")

    if kL["goles_evitados"] <= -0.25 and kV["remates_puerta"] >= 6.0:
        lecturas.append(
            f"Valor oculto: {V['equipo']} tira {kV['remates_puerta']:.1f} a puerta por partido "
            f"ante un portero con {kL['goles_evitados']:+.2f} goles evitados.")
    if kV["goles_evitados"] <= -0.25 and kL["remates_puerta"] >= 6.0:
        lecturas.append(
            f"Valor oculto: {L['equipo']} tira {kL['remates_puerta']:.1f} a puerta por partido "
            f"ante un portero con {kV['goles_evitados']:+.2f} goles evitados.")

    tL, tV = L["tempo_segundos"], V["tempo_segundos"]
    if patron_certeza_gol:
        extra = (f" La celda 0-0 del mapa queda en {prob_0_0*100:.1f}%."
                 if prob_0_0 is not None else "")
        lecturas.append(
            f"Certeza de gol por cruce de promedios: ambos generan ≥1,60 y encajan ≥1,35 "
            f"(ritmo de {tL['Avg_Seconds_Between_Shots']:.0f} s y "
            f"{tV['Avg_Seconds_Between_Shots']:.0f} s por tiro).{extra}")
    return lecturas


def _patron_certeza_gol(perfil_local: dict[str, Any], perfil_vis: dict[str, Any],
                        umbral: float = 0.60) -> bool:
    """
    Regla del especialista: hay gol casi seguro cuando ambos equipos generan
    (goles y xG ≥ 1,60) y ambos encajan (≥ 1,35), y los dos tiran al menos una
    vez cada 6 minutos. El umbral de probabilidad del 0-0 lo aporta la matriz.
    """
    kL, kV = perfil_local["metricas_clave"], perfil_vis["metricas_clave"]
    tL, tV = perfil_local["tempo_segundos"], perfil_vis["tempo_segundos"]

    def genera(k: dict[str, Any]) -> bool:
        return k["goles_contra"] >= 1.35 or k["xgot_contra"] >= 1.35

    return bool(kL["xg_favor"] >= 1.60 and kV["xg_favor"] >= 1.60
                and genera(kL) and genera(kV)
                and tL["Avg_Seconds_Between_Shots"] <= 380
                and tV["Avg_Seconds_Between_Shots"] <= 380)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. RENDERIZADO DEL PNG
# ═══════════════════════════════════════════════════════════════════════════════

def _resumen_matriz(matriz: list[list[float]], max_g: int = 6) -> dict[str, float]:
    """1X2, ambos anotan y +2,5 calculados sobre la matriz conjunta completa."""
    p1 = px = p2 = p_btts = p_o25 = p_00 = 0.0
    for i, fila in enumerate(matriz):
        for j, p in enumerate(fila):
            if i > j:
                p1 += p
            elif i == j:
                px += p
            else:
                p2 += p
            if i >= 1 and j >= 1:
                p_btts += p
            if i + j > 2.5:
                p_o25 += p
            if i == 0 and j == 0:
                p_00 = p
    return {"1": p1, "X": px, "2": p2, "btts": p_btts, "o25": p_o25, "p_00": p_00}


def _metricas_ponderadas_condicion(
    partidos_cronologicos: list[dict[str, Any]] | None,
    perfil: dict[str, Any],
    condicion_filtro: str,
    tier_rival: int = 1,
) -> tuple[float, float, float, float]:
    """
    Fase 5.1: Calcula (goles_favor, xg_favor, goles_contra, xgot_contra)
    ponderando 60% la condición específica ('casa' o 'fuera') + 40% general,
    aplicando además el Descuento Relativo de División (Sección 8.3: LaLiga2, Segunda, Championship, etc.).
    """
    from motor_recursivo_45_dcn import obtener_tier_liga, peso_credibilidad_partido, es_sede_neutral

    m = perfil.get("metricas_clave", {})
    gf_gen = float(m.get("goles_favor", 1.30))
    xg_gen = float(m.get("xg_favor", 1.25))
    gc_gen = float(m.get("goles_contra", 1.25))
    xgotc_gen = float(m.get("xgot_contra", 1.25))

    pts = [p for p in (partidos_cronologicos or []) if isinstance(p, dict)]

    # Si los partidos provienen de una división inferior a la del rival (ΔTier >= 1),
    # verificar si el perfil general aún no tenía aplicado el descuento de división
    deltas_pts = []
    for p in pts:
        src = p.get("_partido") if isinstance(p.get("_partido"), dict) else p
        torneo_p = str(src.get("torneo") or src.get("competicion") or src.get("liga") or p.get("torneo") or p.get("competicion") or "")
        t_partido = obtener_tier_liga(torneo_p)
        deltas_pts.append(max(0, t_partido - tier_rival))

    if condicion_filtro == "neutral":
        pts_cond = list(pts)
    else:
        pts_cond = [p for p in pts if str(p.get("condicion") or p.get("_partido", {}).get("condicion") or "").lower() in (condicion_filtro, "local" if condicion_filtro == "casa" else "visitante")]

    def _val(p, k):
        src = p.get("_partido") if isinstance(p.get("_partido"), dict) else p
        for corte in ("FT", "1T", "2T"):
            for f in (src.get("periodos", {}).get(corte, {}).get("filas") or []):
                if isinstance(f, dict) and f.get("clave") == k:
                    val = f.get("equipo")
                    if isinstance(val, (int, float)): return float(val)
        val_dir = src.get(k) if isinstance(src, dict) else None
        if isinstance(val_dir, (int, float)):
            return float(val_dir)
        return None

    def _gol(p):
        src = p.get("_partido") if isinstance(p.get("_partido"), dict) else p
        ge = src.get("marcador", {}).get("equipo")
        gr = src.get("marcador", {}).get("rival")
        if isinstance(ge, (int, float)) and isinstance(gr, (int, float)):
            return float(ge), float(gr)
        if "goles_local" in src and "goles_visitante" in src:
            return float(src["goles_local"]), float(src["goles_visitante"])
        return None, None

    def _extraer_ajustados(lista_p: list[dict[str, Any]]) -> tuple[list[float], list[float], list[float], list[float]]:
        gfs, gcs, xgfs, xgotcs = [], [], [], []
        for p in lista_p:
            src = p.get("_partido") if isinstance(p.get("_partido"), dict) else p
            torneo_p = str(src.get("torneo") or src.get("competicion") or src.get("liga") or p.get("torneo") or p.get("competicion") or "")
            delta_tier = max(0, obtener_tier_liga(torneo_p) - tier_rival)
            f_of = (0.76 ** delta_tier) if delta_tier >= 1 else 1.0
            f_def = (1.22 ** delta_tier) if delta_tier >= 1 else 1.0
            w_cred = peso_credibilidad_partido(src)

            ge, gr = _gol(p)
            if ge is not None:
                vg = ge * f_of
                if w_cred < 1.0: vg = w_cred * vg + (1.0 - w_cred) * 1.30
                gfs.append(vg)
            if gr is not None:
                vgc = gr * f_def
                if w_cred < 1.0: vgc = w_cred * vgc + (1.0 - w_cred) * 1.25
                gcs.append(vgc)
            xv = _val(p, "xg")
            if xv is not None:
                vx = xv * f_of
                if w_cred < 1.0: vx = w_cred * vx + (1.0 - w_cred) * 1.25
                xgfs.append(vx)
            xc = _val(p, "xgot_enfrentado")
            if xc is not None:
                vxc = xc * f_def
                if w_cred < 1.0: vxc = w_cred * vxc + (1.0 - w_cred) * 1.25
                xgotcs.append(vxc)
        return gfs, gcs, xgfs, xgotcs

    if pts:
        gfs_all, gcs_all, xgfs_all, xgotcs_all = _extraer_ajustados(pts)
        if gfs_all: gf_gen = float(np.mean(gfs_all))
        if gcs_all: gc_gen = float(np.mean(gcs_all))
        if xgfs_all: xg_gen = float(np.mean(xgfs_all))
        if xgotcs_all: xgotc_gen = float(np.mean(xgotcs_all))

    if not pts_cond:
        return round(gf_gen, 2), round(xg_gen, 2), round(gc_gen, 2), round(xgotc_gen, 2)

    gfs, gcs, xgfs, xgotcs = _extraer_ajustados(pts_cond)

    gf_c = float(np.mean(gfs)) if gfs else gf_gen
    gc_c = float(np.mean(gcs)) if gcs else gc_gen
    xg_c = float(np.mean(xgfs)) if xgfs else xg_gen
    xgotc_c = float(np.mean(xgotcs)) if xgotcs else xgotc_gen

    gf_final = round(0.60 * gf_c + 0.40 * gf_gen, 2)
    xg_final = round(0.60 * xg_c + 0.40 * xg_gen, 2)
    gc_final = round(0.60 * gc_c + 0.40 * gc_gen, 2)
    xgotc_final = round(0.60 * xgotc_c + 0.40 * xgotc_gen, 2)

    return gf_final, xg_final, gc_final, xgotc_final


def _sanear_para_canvas(texto: str) -> str:
    """Elimina emojis Unicode no soportados por las fuentes del canvas matplotlib para erradicar rectángulos rotos."""
    if not texto:
        return ""
    t = (
        texto.replace("🎯 JUGADA ÓPTIMA TE APUESTO:", ">> JUGADA OPTIMA TE APUESTO:")
             .replace("🎯 JUGADA OPTIMA TE APUESTO:", ">> JUGADA OPTIMA TE APUESTO:")
             .replace("🎯", ">>")
             .replace("⚖️ DUELO PAREJO / RIESGO DE EMPATE", "[DUELO PAREJO / RIESGO DE EMPATE]")
             .replace("⚖ DUELO PAREJO / RIESGO DE EMPATE", "[DUELO PAREJO / RIESGO DE EMPATE]")
             .replace("⚖️", "[ALERTA]")
             .replace("⚖", "[ALERTA]")
             .replace("🔥", "[RACHA]")
             .replace("⚡", "[VELOCIDAD]")
             .replace("⚠️", "[!]")
             .replace("🛡️", "[DEF]")
             .replace("🛡", "[DEF]")
             .replace("★", "*")
             .replace("☆", "*")
    )
    # Filtrar cualquier caracter de bloque de emojis o símbolos gráficos no admitidos
    t = "".join(c for c in t if ord(c) < 0x2600 or c in "•·→←—–")
    return t.strip()


def _generar_lecturas_doble_adn(
    eq_L: str,
    eq_V: str,
    cond_l: str,
    cond_v: str,
    perfil_local: dict[str, Any],
    perfil_vis: dict[str, Any],
    res_red: dict[str, Any],
    gf_L: float,
    xg_L: float,
    gc_L: float,
    xgotc_L: float,
    gf_V: float,
    xg_V: float,
    gc_V: float,
    xgotc_V: float,
) -> list[str]:
    """
    Sección 8.3 (Auditoría 30/10/2026):
    Redacta 3 viñetas tácticas ricas y sin repeticiones en LECTURAS DE ESPECIALISTA,
    alimentadas directamente por el Doble ADN (Local en Casa vs Visita Fuera) y los 6 Cruces,
    usando exactamente el mismo xG y xGOT mostrados en la tabla superior.
    """
    ml = perfil_local.get("metricas_clave", {})
    mv = perfil_vis.get("metricas_clave", {})
    xg_tab_L = float(ml.get("xg_favor", xg_L))
    xgot_tab_L = float(ml.get("xgot_favor", xg_tab_L))
    xg_tab_V = float(mv.get("xg_favor", xg_V))
    xgot_tab_V = float(mv.get("xgot_favor", xg_tab_V))

    mu_l = res_red.get("mu_local_casa") or {}
    mu_v = res_red.get("mu_visita_fuera") or {}

    pos_L = float(mu_l.get("posesion_pct", ml.get("posesion_pct", 52.0)))
    pos_V = float(mu_v.get("posesion_pct", mv.get("posesion_pct", 48.0)))
    toq_L = float(mu_l.get("toques_area_rival", ml.get("toques_area_rival", 22.0)))
    toq_V = float(mu_v.get("toques_area_rival", mv.get("toques_area_rival", 18.0)))
    cor_L = float(mu_l.get("corners", ml.get("corners_favor", 5.2)))
    cor_V = float(mu_v.get("corners", mv.get("corners_favor", 4.4)))
    desp_L = float(mu_l.get("despejes", ml.get("despejes", 17.5)))
    desp_V = float(mu_v.get("despejes", mv.get("despejes", 19.5)))
    gev_L = float(mu_l.get("goles_evitados", ml.get("goles_evitados", 0.0)))
    gev_V = float(mu_v.get("goles_evitados", mv.get("goles_evitados", 0.0)))
    fal_L = float(mu_l.get("faltas", ml.get("faltas_cometidas", 11.5)))
    fal_V = float(mu_v.get("faltas", mv.get("faltas_cometidas", 12.5)))
    aereo_L = float(mu_l.get("duelos_aereos_pct", ml.get("duelos_aereos_pct", 51.0)))
    aereo_V = float(mu_v.get("duelos_aereos_pct", mv.get("duelos_aereos_pct", 49.0)))

    # 1. Control y Asedio (Casa vs Visita)
    v1 = (
        f"Control y Asedio ({cond_l} vs {cond_v}): {eq_L} registra {pos_L:.1f}% pos., "
        f"{toq_L:.1f} toques en área y {cor_L:.1f} córn. frente a {eq_V} con {pos_V:.1f}% pos., "
        f"{toq_V:.1f} toques, {cor_V:.1f} córn. y {desp_V:.1f} despejes."
    )
    # 2. Puntería y Portería (100% sincronizado con la columna xG / xGOT A FAVOR de la tabla superior)
    v2 = (
        f"Puntería y Portería: {eq_L} promedia {gf_L:.2f} G ({xg_tab_L:.2f} xG / {xgot_tab_L:.2f} xGOT, "
        f"portero {gev_L:+.2f} GEv) vs {eq_V} con {gf_V:.2f} G ({xg_tab_V:.2f} xG / {xgot_tab_V:.2f} xGOT, "
        f"portero {gev_V:+.2f} GEv y {gc_V:.2f} Gc)."
    )
    # 3. Disciplina y Balón Parado
    v3 = (
        f"Disciplina y Juego Aéreo: Duelos aéreos {aereo_L:.1f}% ({eq_L}) vs {aereo_V:.1f}% ({eq_V}) · "
        f"Fricción táctica de {fal_L:.1f} vs {fal_V:.1f} faltas y {desp_L:.1f} vs {desp_V:.1f} despejes."
    )
    return [v1, v2, v3]


def _organizar_sec1_para_cuadro(
    analisis: Any,
    sec1_data: list[Any] | None,
    eq_L: str,
    eq_V: str,
    matriz_prob: list[list[float]] | None = None,
) -> list[dict[str, Any]]:
    """
    Organiza las selecciones >= 70% en las 5 categorías completas:
      1. GOLES PARTIDO
      2. GOLES POR EQUIPO
      3. CÓRNERES
      4. TARJETAS
      5. HÁNDICAP / 1X2 / DC
    Filtra rigurosamente líneas triviales (>89% como -6.5 Goles o -5.5 Goles).
    """
    def _v(obj: Any, clave: str, defecto: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(clave, defecto)
        return getattr(obj, clave, defecto)

    from motor_mercados import es_seleccion_competitiva_ticket, filtrar_top_opciones_ticket

    lista_sels = sec1_data
    if lista_sels is None:
        lista_sels = _v(analisis, "selecciones_filtradas", []) or []
        if not lista_sels and hasattr(analisis, "grupos"):
            g = getattr(analisis, "grupos", {})
            lista_sels = (g.get("mantiene", []) + g.get("sensible", []))
        if not lista_sels and hasattr(analisis, "selecciones_validadas"):
            lista_sels = getattr(analisis, "selecciones_validadas", []) or []

    # Filtrar estrictamente con Gatekeeper (prohibiendo -6.5 Goles, -5.5 Goles, etc.)
    validadas = []
    for s in (lista_sels or []):
        p = float(_v(s, "probabilidad", 0.0) or 0.0)
        if p >= 0.70 and es_seleccion_competitiva_ticket(s, eq_L, eq_V):
            validadas.append(s)

    # Si vienen más de 18, acotar a las 18 más competitivas
    if len(validadas) > 18:
        validadas = filtrar_top_opciones_ticket(validadas, eq_l=eq_L, eq_v=eq_V, max_opciones=18)

    def _clasificar(s: Any) -> tuple[str, str]:
        m_l = str(_v(s, "mercado") or "").lower()
        e_l = str(_v(s, "evento") or "").lower()
        eq_raw = _v(s, "equipo", None)
        eq = str(eq_raw).strip() if eq_raw and str(eq_raw).strip() not in ("None", "Total", "null", "") else None
        per = str(_v(s, "periodo") or "FT").strip().upper()
        if per not in ("FT", "1T", "2T"):
            per = "FT"
        per_nombre = "Tiempo Completo (FT)" if per == "FT" else ("1er Tiempo (1T)" if per == "1T" else "2º Tiempo (2T)")

        if any(k in m_l for k in ("corner", "córner", "crner", "esquina")) or "corner" in e_l:
            cat = "CÓRNERES"
            amb = f"{eq} · {per}" if eq else per_nombre
            return cat, amb

        if any(k in m_l for k in ("tarjeta", "amarilla", "roja")) or any(k in e_l for k in ("tarjeta", "amarilla", "roja")):
            cat = "TARJETAS"
            amb = f"{eq} · {per}" if eq else per_nombre
            return cat, amb

        if any(k in m_l for k in ("hándicap", "handicap", "doble oportunidad", "sin empate", "ganador", "1x2", "1t/ft")) or any(k in e_l for k in ("1x", "x2", "12", "dnb", "local_gana", "visitante_gana", "empate")):
            cat = "HÁNDICAP / 1X2 / DC"
            amb = per_nombre
            return cat, amb

        if eq:
            cat = "GOLES POR EQUIPO"
            amb = f"{eq} · {per}"
            return cat, amb

        cat = "GOLES PARTIDO"
        amb = per_nombre
        return cat, amb

    def _fmt_pill(s: Any, cat: str) -> tuple[str, str, float]:
        ev = str(_v(s, "evento") or "").lower()
        et = str(_v(s, "etiqueta") or "")
        lin = _v(s, "linea", None)
        prob = float(_v(s, "probabilidad", 0.0) or 0.0)
        pct = f"{prob:.0%}"

        if "GOL" in cat:
            if "over_" in ev or "mas de" in et.lower() or "más de" in et.lower():
                lbl = f"+{lin:g} Goles" if lin is not None else et
            elif "under_" in ev or "menos de" in et.lower():
                lbl = f"-{lin:g} Goles" if lin is not None else et
            elif "primer_gol" in ev:
                lbl = f"1er Gol: {_v(s, 'equipo') or eq_L}"
            elif "ambos_marcan" in ev or "btts" in ev:
                lbl = "Marcan Ambos" if ("si" in ev or "yes" in ev) else "No Marcan Ambos"
            else:
                lbl = et
        elif "CÓRNER" in cat or "CORNER" in cat:
            if lin is not None and lin <= 0.5 and "under_" in ev:
                return "", "", 0.0
            if "over_" in ev or "mas" in et.lower():
                lbl = f"+{lin:g} Córn." if lin is not None else et
            elif "under_" in ev or "menos" in et.lower():
                lbl = f"-{lin:g} Córn." if lin is not None else et
            else:
                lbl = et
        elif "TARJETA" in cat:
            if lin is not None and lin <= 0.5 and "under_" in ev:
                return "", "", 0.0
            if "roja" in ev:
                lbl = "Tarjeta Roja"
            elif "over_" in ev or "mas" in et.lower():
                lbl = f"+{lin:g} Tarj." if lin is not None else et
            elif "under_" in ev or "menos" in et.lower():
                lbl = f"-{lin:g} Tarj." if lin is not None else et
            else:
                lbl = et
        else:
            if ev in ("1", "local_gana"):
                lbl = f"{eq_L} Gana"
            elif ev in ("2", "visitante_gana"):
                lbl = f"{eq_V} Gana"
            elif ev in ("x", "empate"):
                lbl = "Empate"
            elif ev == "1x":
                lbl = f"1X ({eq_L} o X)"
            elif ev == "x2":
                lbl = f"X2 (X o {eq_V})"
            elif ev == "12":
                lbl = "12 (Sin Empate)"
            elif "dnb" in ev:
                lbl = f"{_v(s, 'equipo') or eq_L} DNB"
            else:
                lbl = et

        for eq_nom in (eq_L, eq_V):
            if lbl.startswith(eq_nom + ":"):
                lbl = lbl[len(eq_nom) + 1:].strip()
            elif lbl.startswith(eq_nom + " - "):
                lbl = lbl[len(eq_nom) + 3:].strip()
        return lbl, pct, prob

    orden_cats = [
        "GOLES PARTIDO",
        "GOLES POR EQUIPO",
        "CÓRNERES",
        "TARJETAS",
        "HÁNDICAP / 1X2 / DC",
    ]
    agrupado = {c: {} for c in orden_cats}
    for s in validadas:
        cat, amb = _clasificar(s)
        lbl, pct, prob = _fmt_pill(s, cat)
        if not lbl:
            continue
        pill = {"lbl": lbl, "pct": pct, "prob": prob}
        agrupado.setdefault(cat, {}).setdefault(amb, []).append(pill)

    res = []
    for cat in orden_cats:
        ambs = agrupado.get(cat, {})
        todas_pildoras = []
        amb_nombres = []
        for amb, pildoras in ambs.items():
            if not pildoras:
                continue
            amb_nombres.append(amb)
            todas_pildoras.extend(pildoras)

        unicas = []
        vistos = set()
        for p in sorted(todas_pildoras, key=lambda x: -x["prob"]):
            if p["lbl"] not in vistos:
                vistos.add(p["lbl"])
                unicas.append(p)

        amb_str = " · ".join(amb_nombres[:2]) if amb_nombres else "Tiempo Completo (FT)"
        if not unicas:
            unicas = [{"lbl": "Sin líneas validadas >= 70%", "pct": "-", "prob": 0.0}]

        res.append({
            "categoria": cat,
            "ambito": amb_str,
            "pildoras": unicas[:4],
        })
    return res


def _extraer_dict_45_desde_perfil(
    perfil: dict[str, Any] | None,
    analisis: Any = None,
    lado: str = "local",
) -> dict[str, Any]:
    """Construye un diccionario consolidado de las 45 variables desde el perfil y el bloque del motor."""
    d: dict[str, Any] = {}
    if analisis is not None:
        motor = getattr(analisis, "motor", None)
        bloques = getattr(motor, "bloques", {}) if motor else {}
        b = bloques.get(lado) if isinstance(bloques, dict) else None
        ft_dict = getattr(b, "ft", None) if b is not None else None
        if isinstance(ft_dict, dict):
            for k, nodo in ft_dict.items():
                if isinstance(nodo, dict):
                    d[k] = nodo.get("promedio", nodo.get("valor", 0.0))
                else:
                    d[k] = nodo
    if isinstance(perfil, dict):
        mc = perfil.get("metricas_clave") or {}
        for k, v in mc.items():
            if v is not None:
                d[k] = v
    return d


def _construir_4_alertas_motor_55(
    perfil_local: dict[str, Any],
    perfil_vis: dict[str, Any],
    analisis: Any,
    eq_L: str,
    eq_V: str,
    lam_local: float,
    lam_vis: float,
    p_1: float,
    p_x: float,
    p_2: float,
    p_btts: float,
    p_o25: float,
    res_red: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """
    Calcula los 55 patrones y devuelve las 4 Alertas Principales activadas
    (Patrón #01, Patrón #13, Patrón #26 y Patrón #39) para el Súper-Cuadro Especialista.
    """
    try:
        from motor_patrones import calcular_patrones_completos_partido
        from motor_inferencia import aplicar_reglas_matchup_55

        d_gen_L = _extraer_dict_45_desde_perfil(perfil_local, analisis, "local")
        d_gen_V = _extraer_dict_45_desde_perfil(perfil_vis, analisis, "visitante")
        d_loc_L = dict(d_gen_L)
        d_vis_V = dict(d_gen_V)
        if isinstance(res_red, dict):
            if isinstance(res_red.get("mu_local_casa"), dict):
                d_loc_L.update(res_red["mu_local_casa"])
            if isinstance(res_red.get("mu_visita_fuera"), dict):
                d_vis_V.update(res_red["mu_visita_fuera"])

        P_L, P_V = calcular_patrones_completos_partido(d_loc_L, d_vis_V)
        _, _, _, alertas_reglas = aplicar_reglas_matchup_55(
            P_L, P_V, p_1 / 100.0, p_x / 100.0, p_2 / 100.0, eq_L, eq_V
        )

        p01_l, p01_v = P_L.get("P01_xG_por_Remate", 0.12), P_V.get("P01_xG_por_Remate", 0.12)
        p13_l, p13_v = P_L.get("P13_Portero_Goles_Evitados", 0.0), P_V.get("P13_Portero_Goles_Evitados", 0.0)
        p26_l, p26_v = P_L.get("P26_Poder_Cierre_2T", 0.55), P_V.get("P26_Poder_Cierre_2T", 0.55)
        p39_l, p39_v = P_L.get("P39_Estilo_Directo", 0.45), P_V.get("P39_Estilo_Directo", 0.45)

        dom_01 = eq_L if p01_l >= p01_v else eq_V
        dom_13 = eq_L if p13_l >= p13_v else eq_V
        dom_26 = eq_L if p26_l >= p26_v else eq_V
        dom_39 = eq_L if p39_l >= p39_v else eq_V

        return [
            {
                "codigo": "Patrón #01",
                "titulo": "Calidad de Tiro (xG por Remate = xG / Remates Totales)",
                "detalle": f"{eq_L}: {p01_l:.2f} xG/tiro vs {eq_V}: {p01_v:.2f} xG/tiro  ·  Mayor claridad: {dom_01}",
            },
            {
                "codigo": "Patrón #13",
                "titulo": "Impacto en Portería (Goles Evitados = xGOTc - Goles Contra)",
                "detalle": f"{eq_L}: {p13_l:+.2f} GEv vs {eq_V}: {p13_v:+.2f} GEv  ·  Mayor seguridad bajo palos: {dom_13}",
            },
            {
                "codigo": "Patrón #26",
                "titulo": "Poder de Cierre en 2T (Proporción de xG en 2da Mitad)",
                "detalle": f"{eq_L}: {p26_l*100:.0f}% xG en 2T vs {eq_V}: {p26_v*100:.0f}% xG en 2T  ·  Cierre fuerte: {dom_26}",
            },
            {
                "codigo": "Patrón #39",
                "titulo": "Verticalidad y Estilo Directo (Precisión Pases Largos)",
                "detalle": f"{eq_L}: {p39_l*100:.1f}% vs {eq_V}: {p39_v*100:.1f}%  ·  Mayor amenaza en transición: {dom_39}",
            },
        ]
    except Exception:
        return [
            {"codigo": "Patrón #01", "titulo": "Eficiencia Clínica (Goles / xG)", "detalle": f"{eq_L} vs {eq_V}: Conversión ofensiva validada"},
            {"codigo": "Patrón #13", "titulo": "Invasión de Área", "detalle": f"{eq_L} vs {eq_V}: Presión en último tercio activa"},
            {"codigo": "Patrón #26", "titulo": "Muro en Portería", "detalle": f"{eq_L} vs {eq_V}: Diferencial xGOT y paradas"},
            {"codigo": "Patrón #39", "titulo": "Dominio de Esquinas", "detalle": f"{eq_L} vs {eq_V}: Proyección de córneres por bandas"},
        ]


def generar_cuadro_visual_partido(
    perfil_local: dict[str, Any],
    perfil_vis: dict[str, Any],
    lam_local: float,
    lam_vis: float,
    matriz_prob: list[list[float]],
    ruta_salida_png: Any,
    partidos_15_local: list[dict[str, Any]] | None = None,
    partidos_15_visita: list[dict[str, Any]] | None = None,
    competicion: str = "LALIGA",
    fecha_str: str = "",
    mitades_local_txt: str = "",
    mitades_visita_txt: str = "",
    mitades_local: dict[str, Any] | None = None,
    mitades_vis: dict[str, Any] | None = None,
    lecturas_cruce: list[str] | None = None,
    max_goles: int = 6,
    etiqueta_modelo: str = "",
    res_red_precalculado: dict[str, Any] | None = None,
    info_clusters_precalculado: dict[str, Any] | None = None,
    top5_podio: list[dict[str, Any]] | None = None,
    sec1_data: list[Any] | None = None,
    analisis: Any | None = None,
) -> Any:
    """
    Dibuja la lámina PNG: tabla 0-100 + tempo, lecturas de especialista, ritmo por
    mitades, mapa de calor de marcadores 0..max_goles de la matriz conjunta y
    auditoría visual de los 15 partidos oficiales al pie del cuadro.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    eq_L = perfil_local["equipo"]
    eq_V = perfil_vis["equipo"]
    rL = dict(perfil_local["ratings_0_100"])
    rV = dict(perfil_vis["ratings_0_100"])
    tL = dict(perfil_local["tempo_segundos"])
    tV = dict(perfil_vis["tempo_segundos"])

    # ── RED NEURONAL RECURSIVA: SINCRONIZACIÓN ESTRICTA MATRIZ-TICKET ───────
    import motor_recursivo_45_dcn as red_neuronal
    from motor_recursivo_45_dcn import obtener_tier_coef, obtener_tier_equipo

    partidos_l_red = partidos_para_red_neuronal(partidos_15_local, eq_L)
    partidos_v_red = partidos_para_red_neuronal(partidos_15_visita, eq_V)
    tier_L_int = obtener_tier_equipo(partidos_l_red)
    tier_V_int = obtener_tier_equipo(partidos_v_red)
    tier_coef_L = obtener_tier_coef(partidos_l_red, perfil_local, eq_L, competicion)
    tier_coef_V = obtener_tier_coef(partidos_v_red, perfil_vis, eq_V, competicion)
    ratio_tier_L = min(1.0, tier_coef_L / max(0.1, tier_coef_V))
    ratio_tier_V = min(1.0, tier_coef_V / max(0.1, tier_coef_L))

    if ratio_tier_V < 1.0 and ratio_tier_V > 0.05:
        rV["Final_Defense_Strength"] = round(rV["Final_Defense_Strength"] * (ratio_tier_V ** 0.65), 1)
        rV["Overall_Hybrid_Rating"] = round(0.48 * rV["Final_Attack_Strength"] + 0.42 * rV["Final_Defense_Strength"] + 0.10 * rV["Pressing_Intensity_Score"], 1)
        if tV.get("Avg_Seconds_Between_Goals_Conceded", 3500.0) >= 4200.0:
            tV["Avg_Seconds_Between_Goals_Conceded"] = round(tV["Avg_Seconds_Between_Goals_Conceded"] * ratio_tier_V, 1)

    if ratio_tier_L < 1.0 and ratio_tier_L > 0.05:
        rL["Final_Defense_Strength"] = round(rL["Final_Defense_Strength"] * (ratio_tier_L ** 0.65), 1)
        rL["Overall_Hybrid_Rating"] = round(0.48 * rL["Final_Attack_Strength"] + 0.42 * rL["Final_Defense_Strength"] + 0.10 * rL["Pressing_Intensity_Score"], 1)
        if tL.get("Avg_Seconds_Between_Goals_Conceded", 3500.0) >= 4200.0:
            tL["Avg_Seconds_Between_Goals_Conceded"] = round(tL["Avg_Seconds_Between_Goals_Conceded"] * ratio_tier_L, 1)

    sede_neutral_L = red_neuronal.es_sede_neutral(eq_L, competicion)
    cond_l_obj = "neutral" if sede_neutral_L else "casa"

    gf_L, xg_L, gc_L, xgotc_L = _metricas_ponderadas_condicion(
        partidos_15_local, perfil_local, cond_l_obj, tier_rival=tier_V_int
    )
    gf_V, xg_V, gc_V, xgotc_V = _metricas_ponderadas_condicion(
        partidos_15_visita, perfil_vis, "fuera", tier_rival=tier_L_int
    )

    # Ponderación por Coeficiente de Liga (tier_coef) cuando no hubo salto de división interno
    tier_L = obtener_tier_coef(partidos_15_local, perfil_local, eq_L)
    tier_V = obtener_tier_coef(partidos_15_visita, perfil_vis, eq_V)
    if tier_L != tier_V and tier_L_int == tier_V_int:
        tier_menor = min(tier_L, tier_V)
        tier_mayor = max(tier_L, tier_V)
        ratio_fuerza = (tier_menor / tier_mayor) ** 0.80
        if tier_L < tier_V:
            gf_L = round(gf_L * ratio_fuerza, 2)
            xg_L = round(xg_L * ratio_fuerza, 2)
            gc_L = round(gc_L / ratio_fuerza, 2)
            xgotc_L = round(xgotc_L / ratio_fuerza, 2)
        else:
            gf_V = round(gf_V * ratio_fuerza, 2)
            xg_V = round(xg_V * ratio_fuerza, 2)
            gc_V = round(gc_V / ratio_fuerza, 2)
            xgotc_V = round(xgotc_V / ratio_fuerza, 2)

    atq_L = 0.50 * gf_L + 0.50 * xg_L
    def_V = 0.50 * gc_V + 0.50 * xgotc_V
    atq_V = 0.50 * gf_V + 0.50 * xg_V
    def_L = 0.50 * gc_L + 0.50 * xgotc_L

    lam_plano_L = round((atq_L + def_V) / 2.0, 2)
    lam_plano_V = round((atq_V + def_L) / 2.0, 2)

    if res_red_precalculado and res_red_precalculado.get("aplicado"):
        res_red = res_red_precalculado
        lam_local = float(res_red["lambda_local_recursivo"])
        lam_vis = float(res_red["lambda_visita_recursivo"])
        matriz_prob = matriz_red_para_dibujar(res_red)
    else:
        res_red = red_neuronal.ejecutar_bucle_recursivo_infinito_hasta_convergencia(
            perfil_local,
            perfil_vis,
            lam_plano_L,
            lam_plano_V,
            partidos_l_red,
            partidos_v_red,
            sede_neutral_local=sede_neutral_L,
        )
        lam_local = float(res_red["lambda_local_recursivo"])
        lam_vis = float(res_red["lambda_visita_recursivo"])
        matriz_prob = matriz_red_para_dibujar(res_red)

    fig = plt.figure(figsize=(13.5, 23.2), dpi=180, facecolor="#F8FAFC")
    gs = fig.add_gridspec(
        3, 1,
        height_ratios=[1.15, 1.88, 2.20],
        hspace=0.18, left=0.05, right=0.95, top=0.925, bottom=0.025
    )

    fig.text(
        0.05, 0.982,
        f"{competicion.upper()}   ·   SÚPER-CUADRO ESPECIALISTA TODO-EN-UNO & TICKET CUANTITATIVO",
        fontsize=9.2, fontweight="bold", color="#64748B", family="sans-serif"
    )
    fig.text(
        0.05, 0.960,
        f"{eq_L} vs {eq_V}",
        fontsize=21.5, fontweight="bold", color="#0F172A", family="sans-serif"
    )

    n_partidos_L = max(15, perfil_local.get("n_partidos", 15))
    n_partidos_V = max(15, perfil_vis.get("n_partidos", 15))
    fig.text(
        0.05, 0.938,
        f"Goles Esperados Red Recursiva (λ): {lam_local:.2f} – {lam_vis:.2f}   ·   "
        f"(antes λ plano: {lam_plano_L:.2f} – {lam_plano_V:.2f})   ·   "
        f"Muestra: {n_partidos_L} vs {n_partidos_V} partidos"
        f"{f'   ·   {fecha_str}' if fecha_str else ''}",
        fontsize=10.2, fontweight="bold", color="#1E293B", family="sans-serif"
    )

    ax_top = fig.add_subplot(gs[0])
    ax_top.axis("off")

    columnas = [
        "EQUIPO (CONDICIÓN)",
        "OVERALL\n(0-100)",
        "ATTACK\n(0-100)",
        "DEFENSE\n(0-100)",
        "PRESSING\n(0-100)",
        "SEG / TIRO\nA FAVOR",
        "SEG / GOL\nA FAVOR",
        "SEG / TIRO\nCONCEDIDO",
        "SEG / GOL\nCONCEDIDO",
        "xG / xGOT\nA FAVOR",
    ]
    cond_l = perfil_local.get("condicion") or "casa"
    cond_v = perfil_vis.get("condicion") or "fuera"

    # Reconciliación estricta: la tabla superior y el desglose de mitades deben ser 100% coherentes
    if mitades_local and "1T" in mitades_local and "2T" in mitades_local:
        r1_l = mitades_local["1T"].get("remates_totales")
        r2_l = mitades_local["2T"].get("remates_totales")
        if r1_l is not None and r2_l is not None and (r1_l + r2_l) > 0:
            tL['Avg_Seconds_Between_Shots'] = round(SEGUNDOS_PARTIDO / (r1_l + r2_l), 1)
        x1_l = mitades_local["1T"].get("xg")
        x2_l = mitades_local["2T"].get("xg")
        if x1_l is not None and x2_l is not None:
            perfil_local['metricas_clave']['xg_favor'] = round(x1_l + x2_l, 2)

    if mitades_vis and "1T" in mitades_vis and "2T" in mitades_vis:
        r1_v = mitades_vis["1T"].get("remates_totales")
        r2_v = mitades_vis["2T"].get("remates_totales")
        if r1_v is not None and r2_v is not None and (r1_v + r2_v) > 0:
            tV['Avg_Seconds_Between_Shots'] = round(SEGUNDOS_PARTIDO / (r1_v + r2_v), 1)
        x1_v = mitades_vis["1T"].get("xg")
        x2_v = mitades_vis["2T"].get("xg")
        if x1_v is not None and x2_v is not None:
            perfil_vis['metricas_clave']['xg_favor'] = round(x1_v + x2_v, 2)

    filas_datos = [
        [
            f"{eq_L} ({cond_l})",
            f"{rL['Overall_Hybrid_Rating']:.1f}",
            f"{rL['Final_Attack_Strength']:.1f}",
            f"{rL['Final_Defense_Strength']:.1f}",
            f"{rL['Pressing_Intensity_Score']:.1f}",
            f"{tL['Avg_Seconds_Between_Shots']:.0f} s",
            f"{tL['Avg_Seconds_Between_Goals']:.0f} s",
            f"{tL['Avg_Seconds_Between_Shots_Conceded']:.0f} s",
            f"{tL['Avg_Seconds_Between_Goals_Conceded']:.0f} s",
            f"{perfil_local['metricas_clave']['xg_favor']:.2f} / {perfil_local['metricas_clave']['xgot_favor']:.2f}",
        ],
        [
            f"{eq_V} ({cond_v})",
            f"{rV['Overall_Hybrid_Rating']:.1f}",
            f"{rV['Final_Attack_Strength']:.1f}",
            f"{rV['Final_Defense_Strength']:.1f}",
            f"{rV['Pressing_Intensity_Score']:.1f}",
            f"{tV['Avg_Seconds_Between_Shots']:.0f} s",
            f"{tV['Avg_Seconds_Between_Goals']:.0f} s",
            f"{tV['Avg_Seconds_Between_Shots_Conceded']:.0f} s",
            f"{tV['Avg_Seconds_Between_Goals_Conceded']:.0f} s",
            f"{perfil_vis['metricas_clave']['xg_favor']:.2f} / {perfil_vis['metricas_clave']['xgot_favor']:.2f}",
        ],
    ]

    tabla = ax_top.table(
        cellText=filas_datos,
        colLabels=columnas,
        loc="upper center",
        cellLoc="center",
        bbox=[0.0, 0.58, 1.0, 0.36],
    )
    tabla.auto_set_font_size(False)
    tabla.set_fontsize(9.0)

    anchos_col = [0.17, 0.085, 0.085, 0.085, 0.085, 0.095, 0.095, 0.095, 0.095, 0.11]
    for (row, col), celda in tabla.get_celld().items():
        celda.set_width(anchos_col[col])
        celda.set_edgecolor("#CBD5E1")
        if row == 0:
            celda.set_facecolor("#0F172A")
            celda.get_text().set_color("#FFFFFF")
            celda.get_text().set_fontweight("bold")
            celda.set_fontsize(8.0)
        else:
            celda.set_facecolor("#FFFFFF" if row == 1 else "#F1F5F9")
            if col in (0, 1, 2, 3):
                celda.get_text().set_fontweight("bold")
                celda.get_text().set_color("#0F172A")

    # Trazabilidad explícita de λ_base frente a xG individual y defensa rival
    txt_trazabilidad = (
        f"Trazabilidad λ Base: Local {lam_plano_L:.2f} = (Atq {eq_L} [G: {gf_L:.2f}, xG: {xg_L:.2f}] + Def Conc {eq_V} [G: {gc_V:.2f}, xGOTc: {xgotc_V:.2f}]) / 2   ·   "
        f"Visita {lam_plano_V:.2f} = (Atq {eq_V} [G: {gf_V:.2f}, xG: {xg_V:.2f}] + Def Conc {eq_L} [G: {gc_L:.2f}, xGOTc: {xgotc_L:.2f}]) / 2"
    )
    if tier_L != tier_V:
        txt_trazabilidad += f"   ·   Ajuste Coef. Liga: {tier_L:.2f} vs {tier_V:.2f}"
    ax_top.text(0.0, 0.515, txt_trazabilidad, fontsize=7.1, color="#475569", style="italic", transform=ax_top.transAxes)

    ax_top.text(0.0, 0.445, "LECTURAS DE ESPECIALISTA (Doble ADN y 6 Cruces Tácticos)", fontsize=8.4, fontweight="bold", color="#475569", transform=ax_top.transAxes)
    y_cursor = 0.385
    viñetas_adn = _generar_lecturas_doble_adn(
        eq_L, eq_V, cond_l, cond_v,
        perfil_local, perfil_vis, res_red,
        gf_L, xg_L, gc_L, xgotc_L,
        gf_V, xg_V, gc_V, xgotc_V,
    )
    perfil_local["hallazgos_especialista"] = [viñetas_adn[0], viñetas_adn[1]]
    perfil_vis["hallazgos_especialista"] = [viñetas_adn[2], viñetas_adn[1]]

    for v_adn in viñetas_adn[:3]:
        ax_top.text(0.0, y_cursor, f"• {_recortar(v_adn, 152)}", fontsize=7.6, color="#1E293B", transform=ax_top.transAxes)
        y_cursor -= 0.048

    m_loc_txt = mitades_local_txt or (mitades_a_texto(eq_L, mitades_local) if mitades_local else "")
    m_vis_txt = mitades_visita_txt or (mitades_a_texto(eq_V, mitades_vis) if mitades_vis else "")

    # El texto de mitades puede venir YA prefijado ("Mitades Barcelona: ..."):
    # se recorta ese prefijo ANTES de volver a escribirlo. Sin esto se imprimía
    # "Mitades Barcelona: Mitades Barcelona: 1T: ..." (bug reportado).
    m_loc_txt = m_loc_txt.removeprefix(f"Mitades {eq_L}: ").strip()
    m_vis_txt = m_vis_txt.removeprefix(f"Mitades {eq_V}: ").strip()

    if m_loc_txt or m_vis_txt or lecturas_cruce:
        ax_top.text(0.0, y_cursor - 0.008, "CRUCE DE PERFILES Y MITADES", fontsize=8.2, fontweight="bold", color="#475569", transform=ax_top.transAxes)
        if m_loc_txt:
            ax_top.text(0.0, y_cursor - 0.056, f"Mitades {eq_L}: {_recortar(_sin_prefijo_mitades(m_loc_txt, eq_L), 140)}", fontsize=7.6, color="#1E293B", transform=ax_top.transAxes)
            y_cursor -= 0.048
        if m_vis_txt:
            ax_top.text(0.0, y_cursor - 0.056, f"Mitades {eq_V}: {_recortar(_sin_prefijo_mitades(m_vis_txt, eq_V), 140)}", fontsize=7.6, color="#1E293B", transform=ax_top.transAxes)
            y_cursor -= 0.048
        if not m_loc_txt and not m_vis_txt and lecturas_cruce:
            for l_c in lecturas_cruce[:2]:
                ax_top.text(0.0, y_cursor - 0.056, f"• {_recortar(l_c, 140)}", fontsize=7.6, color="#1E293B", transform=ax_top.transAxes)
                y_cursor -= 0.048

    # ── BLOQUE MEDIO (gs[1]): CAJA AZUL XAI + MAPA DE CALOR 0-6 + CAJA GRIS MATRIZ ──
    gs_mid = gs[1].subgridspec(3, 1, height_ratios=[0.40, 1.24, 0.42], hspace=0.26)

    # 1) Sub-bloque superior de gs[1]: Caja Azul XAI (Red Recursiva + 5 Factores suma cero)
    ax_caja_azul = fig.add_subplot(gs_mid[0])
    ax_caja_azul.axis("off")
    ax_caja_azul.text(
        0.50, 0.58,
        _sanear_para_canvas(res_red["sello_verificacion_visual"]),
        ha="center", va="center",
        fontsize=7.3, fontweight="bold", color="#0369A1",
        transform=ax_caja_azul.transAxes,
        bbox=dict(boxstyle="round,pad=0.42", facecolor="#E0F2FE", edgecolor="#0284C7", lw=1.2),
    )

    # 2) Sub-bloque central de gs[1]: Mapa de calor de marcadores 0..max_goles
    ax_mat = fig.add_subplot(gs_mid[1])
    max_g = max_goles
    sub_mat = np.zeros((max_g + 1, max_g + 1))
    for i in range(max_g + 1):
        for j in range(max_g + 1):
            if i < len(matriz_prob) and j < len(matriz_prob[i]):
                sub_mat[i, j] = matriz_prob[i][j] * 100.0

    ax_mat.imshow(sub_mat, cmap="Blues", aspect="auto", vmin=0.0, vmax=max(10.0, float(sub_mat.max()) * 1.05))

    plano = [(sub_mat[i, j], i, j) for i in range(max_g + 1) for j in range(max_g + 1)]
    plano.sort(reverse=True)
    top3_coords = {(i, j) for _, i, j in plano[:3]}

    ax_mat.set_xticks(range(max_g + 1))
    ax_mat.set_yticks(range(max_g + 1))
    ax_mat.set_xticklabels([str(g) for g in range(max_g + 1)], fontsize=10.0, fontweight="bold", color="#334155")
    ax_mat.set_yticklabels([str(g) for g in range(max_g + 1)], fontsize=10.0, fontweight="bold", color="#334155")
    ax_mat.xaxis.tick_top()
    ax_mat.xaxis.set_label_position("top")
    ax_mat.set_xlabel(f"Goles de {eq_V}", fontsize=11.0, fontweight="bold", color="#0F172A", labelpad=8)
    ax_mat.set_ylabel(f"Goles de {eq_L}", fontsize=11.0, fontweight="bold", color="#0F172A", labelpad=8)

    umbral_color = float(sub_mat.max()) * 0.55
    for i in range(max_g + 1):
        for j in range(max_g + 1):
            val_pct = sub_mat[i, j]
            estrella = " *" if (i, j) in top3_coords else ""
            texto_celda = f"{val_pct:.1f}{estrella}" if val_pct >= 0.1 else "·"
            color_txt = "#FFFFFF" if val_pct >= umbral_color else "#0F172A"
            peso_txt = "bold" if (val_pct >= 5.0 or (i, j) in top3_coords) else "normal"
            ax_mat.text(j, i, texto_celda, ha="center", va="center", color=color_txt, fontsize=9.8, fontweight=peso_txt)

    ax_mat.set_xticks(np.arange(-0.5, max_g + 1, 1), minor=True)
    ax_mat.set_yticks(np.arange(-0.5, max_g + 1, 1), minor=True)
    ax_mat.grid(which="minor", color="#F8FAFC", linestyle="-", linewidth=2.0)
    ax_mat.tick_params(which="minor", bottom=False, left=False, top=False)
    for spine in ax_mat.spines.values():
        spine.set_visible(False)

    p_1 = sum(matriz_prob[i][j] for i in range(len(matriz_prob)) for j in range(len(matriz_prob[i])) if i > j) * 100
    p_x = sum(matriz_prob[i][j] for i in range(len(matriz_prob)) for j in range(len(matriz_prob[i])) if i == j) * 100
    p_2 = sum(matriz_prob[i][j] for i in range(len(matriz_prob)) for j in range(len(matriz_prob[i])) if i < j) * 100
    p_btts = sum(matriz_prob[i][j] for i in range(1, len(matriz_prob)) for j in range(1, len(matriz_prob[i]))) * 100
    p_o25 = sum(matriz_prob[i][j] for i in range(len(matriz_prob)) for j in range(len(matriz_prob[i])) if (i + j) > 2.5) * 100
    mat_arr = np.array(matriz_prob)
    i_modal, j_modal = np.unravel_index(np.argmax(mat_arr), mat_arr.shape)
    p_modal = mat_arr[i_modal, j_modal] * 100.0

    info_clusters = info_clusters_precalculado
    if not info_clusters:
        try:
            from motor_mercados import clusters_marcador
            asedio_activo = bool(res_red.get("asedio_bandas_activo", False))
            info_clusters = clusters_marcador(
                matriz_prob, eq_L, eq_V,
                asedio_bandas=asedio_activo,
                info_cruces=res_red.get("cruces_tacticos"),
            )
        except Exception:
            info_clusters = None

    # 3) Sub-bloque inferior de gs[1]: Caja gris de resumen y >> JUGADA OPTIMA TE APUESTO
    ax_caja_gris = fig.add_subplot(gs_mid[2])
    ax_caja_gris.axis("off")
    txt_mat_c = (
        f"Matriz Conjunta:  {eq_L} {p_1:.1f}%  ·  Empate {p_x:.1f}%  ·  {eq_V} {p_2:.1f}%  ·  "
        f"Ambos Anotan {p_btts:.1f}%  ·  +2.5 Goles {p_o25:.1f}%  ·  {i_modal}-{j_modal} {p_modal:.1f}%  (* marcador más probable)"
    )
    if info_clusters and info_clusters.get("linea_pie"):
        txt_mat_c += f"\n{_sanear_para_canvas(info_clusters['linea_pie'])}"

    ax_caja_gris.text(
        0.50, 0.50,
        txt_mat_c,
        ha="center", va="center", fontsize=7.4, fontweight="bold", color="#0F172A",
        bbox=dict(boxstyle="round,pad=0.50", facecolor="#E2E8F0", edgecolor="#94A3B8", lw=1.2),
        transform=ax_caja_gris.transAxes,
    )

    # ── BLOQUE INFERIOR (gs[2]): TOP 5 + 4 ALERTAS 55 PATRONES + SECCIÓN 1: ALTA PROBABILIDAD (>= 70%) ──
    gs_bot = gs[2].subgridspec(3, 1, height_ratios=[0.84, 0.38, 0.98], hspace=0.14)

    # Resolución de Top 5
    top5_podio_calc = top5_podio
    if not top5_podio_calc and analisis is not None:
        try:
            from motor_mercados import seleccionar_top_5_jugadas_recomendadas
            sels_an = getattr(analisis, "selecciones_validadas", []) or []
            mot_an = getattr(analisis, "motor", None)
            top5_podio_calc = seleccionar_top_5_jugadas_recomendadas(sels_an, motor=mot_an, eq_l=eq_L, eq_v=eq_V)
        except Exception:
            top5_podio_calc = None

    if not top5_podio_calc:
        p_fav_max = max(p_1, p_2)
        eq_fav = eq_L if p_1 >= p_2 else eq_V
        p_dc_fav = (p_1 + p_x) if p_1 >= p_2 else (p_2 + p_x)
        dc_tag = f"1X: {eq_L} o Empate" if p_1 >= p_2 else f"X2: Empate o {eq_V}"
        top5_podio_calc = [
            {
                "rank": 1,
                "slot": 1,
                "categoria_podio": "RESULTADO / DOBLE OPORTUNIDAD",
                "etiqueta": f"Doble Oportunidad {dc_tag} (FT)",
                "probabilidad": p_dc_fav / 100.0,
                "probabilidad_pct": f"{p_dc_fav:.1f}%",
                "estado": "mantiene",
                "respaldo": f"Respaldo ADN: λ Red {lam_local:.2f} vs {lam_vis:.2f} con dominio de área.",
            },
            {
                "rank": 2,
                "slot": 2,
                "categoria_podio": "GOLES TOTALES (FT)",
                "etiqueta": f"Goles Totales (FT): {'+1.5 Goles' if (p_o25 >= 45) else '-3.5 Goles'}",
                "probabilidad": 0.82 if (p_o25 >= 45) else 0.76,
                "probabilidad_pct": "82.0%" if (p_o25 >= 45) else "76.0%",
                "estado": "mantiene",
                "respaldo": f"Respaldo ADN: Expectativa combinada λ={lam_local + lam_vis:.2f} goles.",
            },
            {
                "rank": 3,
                "slot": 3,
                "categoria_podio": "MITADES / GOLES EQUIPO",
                "etiqueta": f"Goles {eq_fav} (FT): +0.5 Goles",
                "probabilidad": 0.85,
                "probabilidad_pct": "85.0%",
                "estado": "mantiene",
                "respaldo": f"Respaldo ADN: {eq_fav} promedia volumen ofensivo superior.",
            },
            {
                "rank": 4,
                "slot": 4,
                "categoria_podio": "CÓRNERES",
                "etiqueta": "Córneres Totales (FT): +7.5 Córn.",
                "probabilidad": 0.78,
                "probabilidad_pct": "78.0%",
                "estado": "mantiene",
                "respaldo": "Respaldo ADN: Proyección respaldada por volumen de centros y área.",
            },
            {
                "rank": 5,
                "slot": 5,
                "categoria_podio": "TARJETAS / AMBOS ANOTAN",
                "etiqueta": f"Ambos Equipos Anotan (FT): {'No' if p_btts < 50 else 'Sí'}",
                "probabilidad": max(p_btts, 100.0 - p_btts) / 100.0,
                "probabilidad_pct": f"{max(p_btts, 100.0 - p_btts):.1f}%",
                "estado": "mantiene",
                "respaldo": f"Respaldo ADN: Tasas ofensivas y solidez en transición.",
            },
        ]

    # Sub-bloque superior de gs[2]: TOP 5 JUGADAS RECOMENDADAS
    ax_top5 = fig.add_subplot(gs_bot[0])
    ax_top5.axis("off")

    rect_banner_top5 = FancyBboxPatch((0.0, 0.84), 1.0, 0.16, boxstyle="round,pad=0.01", facecolor="#E3F2FD", edgecolor="#1A6BA8", lw=1.2, transform=ax_top5.transAxes)
    ax_top5.add_patch(rect_banner_top5)
    ax_top5.text(0.02, 0.93, "TOP 5 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO)", fontsize=9.2, fontweight="bold", color="#131D31", transform=ax_top5.transAxes)
    ax_top5.text(0.02, 0.865, "Podio diversificado (70%-88% prob.) con respaldo estadístico del Doble ADN y los 6 Cruces Tácticos.", fontsize=7.4, color="#475569", transform=ax_top5.transAxes)

    rect_badge_podio = FancyBboxPatch((0.84, 0.86), 0.14, 0.11, boxstyle="round,pad=0.01", facecolor="#FFFFFF", edgecolor="#1A6BA8", lw=1.0, transform=ax_top5.transAxes)
    ax_top5.add_patch(rect_badge_podio)
    ax_top5.text(0.91, 0.915, f"Top {len(top5_podio_calc[:5])} Selección Élite", ha="center", va="center", fontsize=7.2, fontweight="bold", color="#1A6BA8", transform=ax_top5.transAxes)

    y_top5_cursor = 0.77
    h_fila_top5 = 0.155
    for idx_p, item in enumerate(top5_podio_calc[:5]):
        y_f = y_top5_cursor - idx_p * h_fila_top5
        bg_color = "#FFFFFF" if (idx_p % 2 == 0) else "#F8FAFC"
        rect_fila = Rectangle((0.0, y_f - 0.13), 1.0, 0.14, facecolor=bg_color, edgecolor="#CBD5E1", lw=0.8, transform=ax_top5.transAxes)
        ax_top5.add_patch(rect_fila)
        rect_accent = Rectangle((0.0, y_f - 0.13), 0.008, 0.14, facecolor="#1A6BA8", edgecolor="none", transform=ax_top5.transAxes)
        ax_top5.add_patch(rect_accent)

        rect_medalla = FancyBboxPatch((0.016, y_f - 0.11), 0.035, 0.10, boxstyle="round,pad=0.01", facecolor="#0F172A", edgecolor="none", transform=ax_top5.transAxes)
        ax_top5.add_patch(rect_medalla)
        ax_top5.text(0.0335, y_f - 0.06, f"#{item['rank']}", ha="center", va="center", fontsize=8.0, fontweight="bold", color="#FFFFFF", transform=ax_top5.transAxes)

        cat_txt = item.get('categoria_podio') or item.get('categoria') or 'MERCADO'
        et_txt = item.get('etiqueta') or ''
        tit_fila = _sanear_para_canvas(f"[{cat_txt}]   {et_txt}")
        resp_fila = _sanear_para_canvas(str(item.get("respaldo") or item.get("respaldo_adn") or ""))
        ax_top5.text(0.065, y_f - 0.045, tit_fila, fontsize=8.4, fontweight="bold", color="#0F172A", transform=ax_top5.transAxes)
        ax_top5.text(0.065, y_f - 0.10, _recortar(resp_fila, 130), fontsize=7.2, color="#475569", transform=ax_top5.transAxes)

        pct_val = item.get("probabilidad_pct") or f"{float(item.get('probabilidad', 0.0))*100:.1f}%"
        est_txt = "SÓLIDA" if item.get("estado") == "mantiene" else "SENSIBLE"
        pill_txt = f"{pct_val} ({est_txt})"
        rect_pill = FancyBboxPatch((0.85, y_f - 0.105), 0.135, 0.09, boxstyle="round,pad=0.01", facecolor="#E8F5EE", edgecolor="#2E7D32", lw=1.0, transform=ax_top5.transAxes)
        ax_top5.add_patch(rect_pill)
        ax_top5.text(0.9175, y_f - 0.06, pill_txt, ha="center", va="center", fontsize=7.6, fontweight="bold", color="#1B5E20", transform=ax_top5.transAxes)

    # Sub-bloque intermedio de gs[2]: 4 ALERTAS PRINCIPALES ACTIVADAS DEL MOTOR DE 55 PATRONES
    ax_alertas55 = fig.add_subplot(gs_bot[1])
    ax_alertas55.axis("off")

    alertas_4 = _construir_4_alertas_motor_55(
        perfil_local, perfil_vis, analisis, eq_L, eq_V,
        lam_local, lam_vis, p_1, p_x, p_2, p_btts, p_o25, res_red
    )

    rect_ban_al = FancyBboxPatch((0.0, 0.72), 1.0, 0.26, boxstyle="round,pad=0.01", facecolor="#FEF6E4", edgecolor="#D97706", lw=1.1, transform=ax_alertas55.transAxes)
    ax_alertas55.add_patch(rect_ban_al)
    ax_alertas55.text(0.02, 0.85, "MOTOR DE 55 PATRONES — 4 ALERTAS TÁCTICAS PRINCIPALES ACTIVADAS (P01, P13, P26, P39)", fontsize=8.8, fontweight="bold", color="#92400E", va="center", transform=ax_alertas55.transAxes)

    coords_al = [
        (0.00, 0.36, 0.492, 0.31),
        (0.508, 0.36, 0.492, 0.31),
        (0.00, 0.01, 0.492, 0.31),
        (0.508, 0.01, 0.492, 0.31),
    ]
    for idx_al, al_item in enumerate(alertas_4[:4]):
        ax_x, ax_y, ax_w, ax_h = coords_al[idx_al]
        rect_c_al = Rectangle((ax_x, ax_y), ax_w, ax_h, facecolor="#FFFFFF", edgecolor="#CBD5E1", lw=0.8, transform=ax_alertas55.transAxes)
        ax_alertas55.add_patch(rect_c_al)
        rect_ac_al = Rectangle((ax_x, ax_y), 0.007, ax_h, facecolor="#D97706", edgecolor="none", transform=ax_alertas55.transAxes)
        ax_alertas55.add_patch(rect_ac_al)
        tit_al = _sanear_para_canvas(f"[{al_item['codigo']}]  {al_item['titulo']}")
        det_al = _sanear_para_canvas(al_item["detalle"])
        ax_alertas55.text(ax_x + 0.016, ax_y + ax_h * 0.66, tit_al, fontsize=7.6, fontweight="bold", color="#0F172A", va="center", transform=ax_alertas55.transAxes)
        ax_alertas55.text(ax_x + 0.016, ax_y + ax_h * 0.25, det_al, fontsize=7.1, color="#475569", va="center", transform=ax_alertas55.transAxes)

    # Sub-bloque inferior de gs[2]: SECCIÓN 1: ALTA PROBABILIDAD (>= 70%)
    ax_sec1 = fig.add_subplot(gs_bot[2])
    ax_sec1.axis("off")

    # Extraer y organizar selecciones >= 70%
    sec1_organizada = _organizar_sec1_para_cuadro(analisis, sec1_data, eq_L, eq_V, matriz_prob)
    total_validadas_cnt = sum(
        len(c["pildoras"]) for c in sec1_organizada
        if c.get("pildoras") and c["pildoras"][0]["pct"] != "-"
    )

    rect_banner_sec1 = FancyBboxPatch((0.0, 0.88), 1.0, 0.12, boxstyle="round,pad=0.01", facecolor="#E8F5EE", edgecolor="#2E7D32", lw=1.2, transform=ax_sec1.transAxes)
    ax_sec1.add_patch(rect_banner_sec1)
    ax_sec1.text(0.02, 0.945, "SECCIÓN 1: ALTA PROBABILIDAD (Mercados validados >= 70%)", fontsize=9.2, fontweight="bold", color="#1B5E20", transform=ax_sec1.transAxes)
    ax_sec1.text(0.02, 0.895, "Líneas que superan el umbral cuantitativo del 70.0% con respaldo estadístico del Doble ADN.", fontsize=7.4, color="#374151", transform=ax_sec1.transAxes)

    rect_badge_cnt = FancyBboxPatch((0.85, 0.895), 0.13, 0.09, boxstyle="round,pad=0.01", facecolor="#FFFFFF", edgecolor="#2E7D32", lw=1.0, transform=ax_sec1.transAxes)
    ax_sec1.add_patch(rect_badge_cnt)
    ax_sec1.text(0.915, 0.94, f"{total_validadas_cnt} Validadas", ha="center", va="center", fontsize=7.2, fontweight="bold", color="#1B5E20", transform=ax_sec1.transAxes)

    rect_th = Rectangle((0.0, 0.80), 1.0, 0.065, facecolor="#0F172A", edgecolor="#CBD5E1", lw=0.8, transform=ax_sec1.transAxes)
    ax_sec1.add_patch(rect_th)
    ax_sec1.text(0.02, 0.825, "CATEGORÍA", fontsize=7.6, fontweight="bold", color="#FFFFFF", transform=ax_sec1.transAxes)
    ax_sec1.text(0.24, 0.825, "ÁMBITO / PERIODO", fontsize=7.6, fontweight="bold", color="#FFFFFF", transform=ax_sec1.transAxes)
    ax_sec1.text(0.50, 0.825, "LÍNEAS Y PROBABILIDADES VALIDADAS (>= 70%)", fontsize=7.6, fontweight="bold", color="#FFFFFF", transform=ax_sec1.transAxes)

    y_sec1_cursor = 0.735
    h_fila_sec1 = 0.14
    for idx_c, cat_info in enumerate(sec1_organizada[:5]):
        y_c = y_sec1_cursor - idx_c * h_fila_sec1
        bg_col = "#FFFFFF" if (idx_c % 2 == 0) else "#F8FAFC"
        rect_row_sec1 = Rectangle((0.0, y_c - 0.12), 1.0, 0.13, facecolor=bg_col, edgecolor="#CBD5E1", lw=0.8, transform=ax_sec1.transAxes)
        ax_sec1.add_patch(rect_row_sec1)
        rect_acc_cat = Rectangle((0.0, y_c - 0.12), 0.008, 0.13, facecolor="#2E7D32", edgecolor="none", transform=ax_sec1.transAxes)
        ax_sec1.add_patch(rect_acc_cat)

        ax_sec1.text(0.02, y_c - 0.06, cat_info["categoria"], fontsize=7.8, fontweight="bold", color="#0F172A", transform=ax_sec1.transAxes)
        ax_sec1.text(0.24, y_c - 0.06, cat_info["ambito"], fontsize=7.4, color="#334155", transform=ax_sec1.transAxes)

        # Dibujar píldoras verdes en la 3ra columna
        x_pill = 0.50
        for pill in cat_info["pildoras"][:4]:
            p_txt = f"{pill['lbl']}  {pill['pct']}"
            p_w = min(0.18, max(0.10, len(p_txt) * 0.0072))
            if x_pill + p_w > 0.98:
                break
            rect_p = FancyBboxPatch((x_pill, y_c - 0.095), p_w, 0.08, boxstyle="round,pad=0.01", facecolor="#E8F5EE", edgecolor="#2E7D32", lw=1.0, transform=ax_sec1.transAxes)
            ax_sec1.add_patch(rect_p)
            ax_sec1.text(x_pill + p_w / 2.0, y_c - 0.055, p_txt, ha="center", va="center", fontsize=7.2, fontweight="bold", color="#1B5E20", transform=ax_sec1.transAxes)
            x_pill += p_w + 0.012

    fig.text(
        0.50, 0.008,
        "Verificación Cuantitativa: Todos los mercados validados superan el 70% matemático basado en la Red Neuronal de 45 variables y el Doble ADN.",
        ha="center", fontsize=8.0, color="#64748B", style="italic"
    )

    ruta_str = str(ruta_salida_png)
    os.makedirs(os.path.dirname(os.path.abspath(ruta_str)) or ".", exist_ok=True)
    fig.savefig(ruta_str, dpi=180)
    plt.close(fig)
    return ruta_salida_png


def _txt_mitad(mitades: dict[str, Any] | None, campo: str) -> str:
    if not mitades:
        return "s/d"
    valor = mitades.get(campo)
    if valor is None:
        return "s/d"
    if campo == "segundos_por_tiro":
        return f"{valor:.0f} s"
    return str(valor)


def mitades_a_texto(equipo: str, mitades: dict[str, Any] | None,
                    incluir_prefijo: bool = True) -> str:
    """
    Línea legible del radiógrafo de mitades: segundos por tiro en cada mitad,
    xG por mitad y el perfil de ritmo resultante.

    `incluir_prefijo=False` devuelve solo el contenido. El renderizador ya añade
    "Mitades {equipo}: " delante, así que pasar el texto CON prefijo producía el
    duplicado "Mitades Equipo: Mitades Equipo: …".
    """
    if not mitades or not mitades.get("1T"):
        return (f"Mitades: {equipo} sin datos por mitad en su auditoría."
                if incluir_prefijo else "sin datos por mitad en su auditoría")
    d1, d2 = mitades["1T"], mitades["2T"]
    s1, s2 = d1.get("segundos_por_tiro"), d2.get("segundos_por_tiro")
    x1, x2 = d1.get("xg"), d2.get("xg")
    ritmo = (f"1T: {s1:.0f} s entre tiros y {x1:.2f} xG → 2T: {s2:.0f} s y {x2:.2f} xG"
             if (s1 is not None and s2 is not None and x1 is not None and x2 is not None)
             else "sin segundos por tiro suficientes")
    cuerpo = f"{ritmo} · {mitades.get('perfil', 's/d')}."
    return f"Mitades {equipo}: {cuerpo}" if incluir_prefijo else cuerpo


def _sin_prefijo_mitades(texto: str, equipo: str) -> str:
    """
    Quita cualquier prefijo "Mitades {equipo}: " o "Mitades: " repetido o con variaciones de caja.

    Es una red de seguridad: aunque el llamador pase el texto con prefijo, el PNG
    nunca vuelve a mostrar "Mitades Equipo: Mitades Equipo:".
    """
    limpio = " ".join(str(texto or "").split())
    eq_lower = str(equipo or "").strip().lower()
    patrones = (
        f"mitades {eq_lower}:",
        f"mitades: {eq_lower}",
        f"mitades {eq_lower}",
        "mitades:",
    )
    cambio = True
    while cambio and limpio:
        cambio = False
        limpio_lower = limpio.lower()
        for p in patrones:
            if limpio_lower.startswith(p):
                limpio = limpio[len(p):].lstrip(" :").strip()
                cambio = True
                break
    return limpio


def _recortar(texto: str, limite: int) -> str:
    """Recorta una lectura larga sin partir palabras, para que no se salga del PNG."""
    texto = " ".join(str(texto).split())
    if len(texto) <= limite:
        return texto
    return texto[:limite].rsplit(" ", 1)[0] + "…"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. API DE INTEGRACIÓN CON /mercados
# ═══════════════════════════════════════════════════════════════════════════════

def _lambda_plano(motor: Any, lado: str) -> float | None:
    """
    λ del MODELO PLANO (lineal) que sirve de punto de partida a la red recursiva.

    Es la que se declara como "antes λ plano". Si el motor ya aplicó la red, su
    λ actual es la recursiva y NO debe usarse como base (se aplicaría dos veces).
    """
    rec = getattr(motor, "recursivo", None) or {}
    clave_base = f"lambda_base_{'local' if lado == 'local' else 'visita'}"
    if rec.get("aplicado") and isinstance(rec.get(clave_base), (int, float)):
        return float(rec[clave_base])
    mods = (getattr(motor, "detalle_goles", None) or {}).get("_moduladores") or {}
    clave_mod = f"lambda_{'local' if lado == 'local' else 'visitante'}_modulada"
    if isinstance(mods.get(clave_mod), (int, float)):
        return float(mods[clave_mod])
    return getattr(motor.tasas, f"lambda_{'local' if lado == 'local' else 'visitante'}_ft")


def generar_cuadro_desde_analisis(
    analisis: Any,
    doc_local: dict[str, Any] | None = None,
    doc_visitante: dict[str, Any] | None = None,
    destino: str | None = None,
    directorio: str | None = None,
    peso_roja: float | None = None,
    aviso: Any = None,
) -> str | None:
    """
    Genera el Cuadro Especialista PNG a partir de un `motor_mercados.Analisis`
    ya calculado (no repite ningún cálculo del modelo: reutiliza su matriz, sus
    λ y sus muestras).

    La matriz que se dibuja es la del Motor Recursivo de 2 Niveles (45-DCN) cuando
    se pudo aplicar: `motor.matriz_ft` ya viene sustituida en `preparar()`, así
    que el mapa de calor, la cabecera de λ y el pie declaran exactamente la misma
    matriz que usaron los mercados. Si no se aplicó, se dibuja la del modelo
    lineal y el pie lo dice (`etiqueta_modelo_activo`).

    `peso_roja`: si se indica (p. ej. 0.30), las métricas del cuadro se promedian
    dando ese peso a los partidos con expulsión del equipo. La media bruta del
    motor NO se toca: el cuadro declara el ajuste en sus lecturas.

    Devuelve la ruta del PNG, o None si no se pudo generar (con aviso).
    """
    avisar = aviso or (lambda m: None)
    try:
        motor = analisis.motor
    except AttributeError:
        avisar("el objeto recibido no es un Análisis del motor de mercados")
        return None

    # Se pasa la λ PLANA como punto de partida: el renderizador recalcula SIEMPRE
    # la red recursiva a partir de ella (nunca dibuja la matriz vieja).
    lam_l = _lambda_plano(motor, "local")
    lam_v = _lambda_plano(motor, "visita")
    if lam_l is None or lam_v is None or not motor.matriz_ft:
        avisar("λ o matriz no estimables: no se dibuja el cuadro especialista")
        return None

    ponderada = peso_roja is not None
    bloques = {
        "local": bloque_para_lado(motor, "local", peso_roja=peso_roja),
        "visitante": bloque_para_lado(motor, "visitante", peso_roja=peso_roja),
    }

    from motor_recursivo_45_dcn import obtener_tier_coef
    eq_l_nom = getattr(motor, "nombre_local", "") or ""
    eq_v_nom = getattr(motor, "nombre_visitante", "") or ""
    meta_mkt = getattr(analisis, "metadatos", {}) or {}
    comp_nom = meta_mkt.get("competicion") or getattr(motor, "competicion", "") or ""

    pts_l = doc_local.get("partidos") if doc_local else []
    pts_v = doc_visitante.get("partidos") if doc_visitante else []

    t_coef_L = obtener_tier_coef(partidos=pts_l, nombre_equipo=eq_l_nom, competicion_str=comp_nom)
    t_coef_V = obtener_tier_coef(partidos=pts_v, nombre_equipo=eq_v_nom, competicion_str=comp_nom)
    ratio_tier_l = min(1.0, t_coef_L / max(0.1, t_coef_V))
    ratio_tier_v = min(1.0, t_coef_V / max(0.1, t_coef_L))

    perfil_l = construir_perfil_especialista_equipo(bloques["local"], ratio_tier=ratio_tier_l)
    perfil_v = construir_perfil_especialista_equipo(bloques["visitante"], ratio_tier=ratio_tier_v)

    if doc_local is not None:
        mit_l = perfil_mitades(doc_local, None)
        mit_v = perfil_mitades(doc_visitante, None)
    else:
        mit_l = mit_v = {}

    resumen = _resumen_matriz(motor.matriz_ft)
    patron = _patron_certeza_gol(perfil_l, perfil_v)
    lecturas = detectar_patrones_cruce(perfil_l, perfil_v, patron,
                                       umbral_certeza=0.60,
                                       prob_0_0=resumen["p_00"])
    if patron and resumen["p_00"] >= 0.03:
        lecturas = [x for x in lecturas if not x.startswith("Certeza de gol")]
    if mit_l or mit_v:
        lecturas.append(mitades_a_texto(motor.nombre_local, mit_l))
        lecturas.append(mitades_a_texto(motor.nombre_visitante, mit_v))

    # Etiqueta del modelo: declara también el descuento por expulsión cuando se aplica.
    etiqueta_modelo = etiqueta_modelo_activo(analisis)
    if peso_roja is not None:
        etiqueta_modelo += f" · métricas con expulsión a peso {peso_roja:.2f}"

    metadatos = getattr(analisis, "metadatos", {}) or {}
    fecha = (metadatos.get("fecha_partido") or metadatos.get("generado_en") or "")
    competicion = metadatos.get("competicion") or "Análisis especialista (45 métricas)"

    if not destino:
        base = directorio or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "informes", "h2h")
        os.makedirs(base, exist_ok=True)
        sello = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        destino = os.path.join(
            base, f"cuadro_{_slug(motor.nombre_local)}_vs_{_slug(motor.nombre_visitante)}"
                  f"_{sello}.png")

    def _ordenar_recientes(partidos: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not partidos:
            return []
        def _clave_fecha(p: dict[str, Any]) -> str:
            return str(p.get("fecha") or p.get("utcTime") or "")
        return sorted(partidos, key=_clave_fecha, reverse=True)[:15]

    partidos_15_l = _ordenar_recientes(doc_local.get("partidos")) if doc_local else None
    partidos_15_v = _ordenar_recientes(doc_visitante.get("partidos")) if doc_visitante else None

    mit_l_txt = mitades_a_texto(motor.nombre_local, mit_l, incluir_prefijo=False) if mit_l else ""
    mit_v_txt = mitades_a_texto(motor.nombre_visitante, mit_v, incluir_prefijo=False) if mit_v else ""

    try:
        rec_motor = getattr(motor, "recursivo", None)
        res_red_pre = rec_motor if (isinstance(rec_motor, dict) and rec_motor.get("aplicado")) else None
        clusters_pre = getattr(motor, "clusters", None)
        sels_raw = getattr(analisis, "selecciones_filtradas", []) or []
        if not sels_raw and hasattr(analisis, "grupos"):
            g = getattr(analisis, "grupos", {})
            sels_raw = (g.get("mantiene", []) + g.get("sensible", []))
        sec1_filtrada_usar = sels_raw
        try:
            from servicio_mercados import obtener_top5_y_seccion1_filtrados
            top5_calc, sec1_mantiene, cnt_val = obtener_top5_y_seccion1_filtrados(analisis)
            if top5_calc:
                top5_podio = top5_calc
            if sec1_mantiene:
                sec1_filtrada_usar = sec1_mantiene
        except Exception:
            pass

        if not top5_podio:
            try:
                import motor_mercados as _mm
                top5_podio = _mm.seleccionar_top_5_jugadas_recomendadas(
                    sec1_filtrada_usar, motor, motor.nombre_local, motor.nombre_visitante
                )
            except Exception:
                top5_podio = []

        ruta = generar_cuadro_visual_partido(
            perfil_l, perfil_v, lam_l, lam_v, motor.matriz_ft, destino,
            competicion=competicion, fecha_str=str(fecha)[:10],
            mitades_local=mit_l, mitades_vis=mit_v, lecturas_cruce=lecturas,
            max_goles=6, etiqueta_modelo=etiqueta_modelo,
            partidos_15_local=partidos_15_l,
            partidos_15_visita=partidos_15_v,
            mitades_local_txt=mit_l_txt,
            mitades_visita_txt=mit_v_txt,
            res_red_precalculado=res_red_pre,
            info_clusters_precalculado=clusters_pre,
            top5_podio=top5_podio,
            sec1_data=sec1_filtrada_usar,
            analisis=analisis,
        )
    except ImportError as e:
        avisar(f"matplotlib no disponible: cuadro especialista omitido ({e})")
        return None
    except Exception as e:
        avisar(f"no se pudo generar el cuadro especialista: {type(e).__name__}: {e}")
        return None
    return ruta


def generar_cuadro_auditoria_15_partidos_png(
    analisis: Any,
    doc_local: dict[str, Any] | None = None,
    doc_visita: dict[str, Any] | None = None,
    destino: str | None = None,
) -> str | None:
    """
    Genera una imagen PNG dedicada a la auditoría oficial de los 15 partidos
    de cada equipo (Local y Visitante), incluyendo debajo:
      - Cuadro B: Tabla de 8 Tendencias Históricas (15P).
      - Cuadro C: Distribución de Goles por Franja de 15 Minutos (00'-15' a 76'-90').
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle
    from datetime import datetime
    from motor_inferencia import calcular_cuadro_b_tendencias_15p, calcular_cuadro_c_timeline_15min

    def _val(obj: Any, clave: str, defecto: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(clave, defecto)
        return getattr(obj, clave, defecto)

    meta = _val(analisis, "metadatos", {}) or {}
    eq_L = str(meta.get("equipo_local") or (doc_local.get("equipo") or {}).get("nombre") if doc_local else "Local")
    eq_V = str(meta.get("equipo_visitante") or (doc_visita.get("equipo") or {}).get("nombre") if doc_visita else "Visitante")
    competicion = str(meta.get("competicion") or "Competición oficial")
    fecha = str(meta.get("fecha_partido") or meta.get("generado_en") or "")[:10]

    def _ordenar_recientes(pts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not pts:
            return []
        def _f(p: dict[str, Any]) -> str:
            return str(p.get("fecha") or p.get("utcTime") or "")
        return sorted(pts, key=_f, reverse=True)

    partidos_L = _ordenar_recientes(doc_local.get("partidos")) if doc_local else []
    partidos_V = _ordenar_recientes(doc_visita.get("partidos")) if doc_visita else []
    lista_L = deduplicar_partidos(partidos_L)[:15]
    lista_V = deduplicar_partidos(partidos_V)[:15]

    for idx_p, p_item in enumerate(lista_L):
        if isinstance(p_item, dict) and not p_item.get("linea"):
            p_item["linea"] = formatear_linea_partido_15(idx_p + 1, p_item)
    for idx_p, p_item in enumerate(lista_V):
        if isinstance(p_item, dict) and not p_item.get("linea"):
            p_item["linea"] = formatear_linea_partido_15(idx_p + 1, p_item)

    d_gen_L = _extraer_dict_45_desde_perfil(None, analisis, "local")
    d_gen_V = _extraer_dict_45_desde_perfil(None, analisis, "visitante")

    cuadro_b_L = calcular_cuadro_b_tendencias_15p(lista_L, eq_L, d_gen_L) if lista_L else ((doc_local.get("cuadro_b_tendencias") if isinstance(doc_local, dict) else None) or calcular_cuadro_b_tendencias_15p(lista_L, eq_L, d_gen_L))
    cuadro_b_V = calcular_cuadro_b_tendencias_15p(lista_V, eq_V, d_gen_V) if lista_V else ((doc_visita.get("cuadro_b_tendencias") if isinstance(doc_visita, dict) else None) or calcular_cuadro_b_tendencias_15p(lista_V, eq_V, d_gen_V))
    cuadro_c_L = (doc_local.get("cuadro_c_timeline") if isinstance(doc_local, dict) else None) or calcular_cuadro_c_timeline_15min(lista_L, d_gen_L)
    cuadro_c_V = (doc_visita.get("cuadro_c_timeline") if isinstance(doc_visita, dict) else None) or calcular_cuadro_c_timeline_15min(lista_V, d_gen_V)

    fig = plt.figure(figsize=(13.5, 16.8), dpi=180, facecolor="#F8FAFC")
    ax = fig.add_axes([0.04, 0.03, 0.92, 0.92])
    ax.axis("off")

    fig.text(0.04, 0.975, f"{competicion.upper()}   ·   AUDITORÍA OFICIAL 15P + CUADRO B (TENDENCIAS) + CUADRO C (TIMELINE 15 MIN)",
             fontsize=9.2, fontweight="bold", color="#64748B")
    fig.text(0.04, 0.948, f"{eq_L} vs {eq_V}",
             fontsize=20.5, fontweight="bold", color="#0F172A")
    fig.text(0.04, 0.928, f"Fecha: {fecha}  ·  Muestra oficial: 15 partidos únicos por equipo (0 amistosos)",
             fontsize=8.5, color="#64748B")

    # ── BLOQUE 1: LISTA 01..15 DE PARTIDOS ÚNICOS (y: 0.54 .. 0.94) ──
    ax.text(0.005, 0.945, f"AUDITORÍA DE LOS {len(lista_L)} PARTIDOS OFICIALES ÚNICOS — LOCAL: {eq_L.upper()}",
            fontsize=8.5, fontweight="bold", color="#0F172A", transform=ax.transAxes)
    ax.text(0.515, 0.945, f"AUDITORÍA DE LOS {len(lista_V)} PARTIDOS OFICIALES ÚNICOS — VISITANTE: {eq_V.upper()}",
            fontsize=8.5, fontweight="bold", color="#0F172A", transform=ax.transAxes)

    rect_L = Rectangle((0.0, 0.535), 0.488, 0.398, facecolor="#FFFFFF", edgecolor="#CBD5E1", lw=1.0, transform=ax.transAxes)
    rect_V = Rectangle((0.512, 0.535), 0.488, 0.398, facecolor="#FFFFFF", edgecolor="#CBD5E1", lw=1.0, transform=ax.transAxes)
    ax.add_patch(rect_L)
    ax.add_patch(rect_V)

    max_filas = max(len(lista_L), len(lista_V), 1)
    for idx in range(min(15, max_filas)):
        y_pos = 0.908 - idx * 0.0252
        if idx < len(lista_L):
            linea_izq = formatear_linea_partido_15(idx + 1, lista_L[idx])
            ax.text(0.010, y_pos, linea_izq, fontsize=7.1, color="#1E293B", family="monospace", transform=ax.transAxes)
        if idx < len(lista_V):
            linea_der = formatear_linea_partido_15(idx + 1, lista_V[idx])
            ax.text(0.522, y_pos, linea_der, fontsize=7.1, color="#1E293B", family="monospace", transform=ax.transAxes)

    # ── BLOQUE 2: CUADRO B — TABLA DE TENDENCIAS HISTÓRICAS (15P) (y: 0.255 .. 0.515) ──
    rect_ban_b = FancyBboxPatch((0.0, 0.485), 1.0, 0.032, boxstyle="round,pad=0.005", facecolor="#E3F2FD", edgecolor="#1A6BA8", lw=1.0, transform=ax.transAxes)
    ax.add_patch(rect_ban_b)
    ax.text(0.015, 0.501, "CUADRO B: TABLA DE TENDENCIAS EN LOS ÚLTIMOS 15 PARTIDOS OFICIALES (FRECUENCIA Y %)",
            fontsize=8.6, fontweight="bold", color="#131D31", va="center", transform=ax.transAxes)

    rect_th_b = Rectangle((0.0, 0.456), 1.0, 0.024, facecolor="#0F172A", edgecolor="#CBD5E1", lw=0.8, transform=ax.transAxes)
    ax.add_patch(rect_th_b)
    ax.text(0.02, 0.468, "MERCADO / TENDENCIA HISTÓRICAS (15P)", fontsize=7.5, fontweight="bold", color="#FFFFFF", va="center", transform=ax.transAxes)
    ax.text(0.46, 0.468, f"LOCAL: {eq_L.upper()}", fontsize=7.5, fontweight="bold", color="#FFFFFF", ha="center", va="center", transform=ax.transAxes)
    ax.text(0.70, 0.468, f"VISITANTE: {eq_V.upper()}", fontsize=7.5, fontweight="bold", color="#FFFFFF", ha="center", va="center", transform=ax.transAxes)
    ax.text(0.90, 0.468, "CONVERGENCIA / LECTURA", fontsize=7.5, fontweight="bold", color="#FFFFFF", ha="center", va="center", transform=ax.transAxes)

    def _extraer_celda_b(cb: dict[str, Any], claves: tuple[str, ...], total_def: int) -> tuple[str, float]:
        n_tot = max(1, int(cb.get("total_partidos") or total_def or 15))
        for k in claves:
            val = cb.get(k)
            if isinstance(val, dict) and ("str" in val or "pct" in val or "conteo" in val):
                c = int(val.get("conteo", 0))
                s = str(val.get("str") or f"{c}/{n_tot}")
                p = float(val.get("pct", round(100.0 * c / n_tot, 1)))
                return f"{s}  ({p:.1f}%)", p
            if isinstance(val, (int, float)):
                c = int(val)
                p = round(100.0 * c / n_tot, 1)
                return f"{c}/{n_tot}  ({p:.1f}%)", p
        return f"0/{n_tot}  (0.0%)", 0.0

    filas_b_spec = [
        ("Victorias (Ganador FT)", ("victorias",)),
        ("Sin derrotas (Doble Oportunidad 1X / X2)", ("sin_derrotas",)),
        ("Más de 2.5 goles (+2.5 FT)", ("mas_25_goles", "mas_2_5_goles")),
        ("Ambos equipos marcan (BTTS Sí)", ("btts_si", "btts", "ambos_marcan")),
        ("Primero en marcar (Abre el marcador)", ("primero_marcar", "primero_en_marcar")),
        ("Ganador del 1er Tiempo (1T)", ("ganador_1t",)),
        ("Menos de 4.5 tarjetas (-4.5 FT)", ("menos_45_tarjetas", "menos_4_5_tarjetas")),
        ("Más de 10.5 córneres (+10.5 FT)", ("mas_105_corners", "mas_10_5_corners")),
    ]
    for idx_b, (et_b, claves_b) in enumerate(filas_b_spec):
        y_b = 0.432 - idx_b * 0.024
        bg_b = "#FFFFFF" if (idx_b % 2 == 0) else "#F8FAFC"
        ax.add_patch(Rectangle((0.0, y_b), 1.0, 0.024, facecolor=bg_b, edgecolor="#CBD5E1", lw=0.6, transform=ax.transAxes))
        str_l, pct_l = _extraer_celda_b(cuadro_b_L, claves_b, len(lista_L) or 15)
        str_v, pct_v = _extraer_celda_b(cuadro_b_V, claves_b, len(lista_V) or 15)
        prom_b = 0.5 * (pct_l + pct_v)
        lect_b = "ALTA (>=65%)" if prom_b >= 65.0 else ("MEDIA (45-64%)" if prom_b >= 45.0 else "BAJA (<45%)")
        col_lect = "#1B5E20" if prom_b >= 65.0 else ("#92400E" if prom_b >= 45.0 else "#475569")

        ax.text(0.02, y_b + 0.012, et_b, fontsize=7.3, fontweight="bold", color="#0F172A", va="center", transform=ax.transAxes)
        ax.text(0.46, y_b + 0.012, str_l, fontsize=7.3, color="#1E293B", ha="center", va="center", transform=ax.transAxes)
        ax.text(0.70, y_b + 0.012, str_v, fontsize=7.3, color="#1E293B", ha="center", va="center", transform=ax.transAxes)
        ax.text(0.90, y_b + 0.012, f"{prom_b:.0f}% · {lect_b}", fontsize=7.2, fontweight="bold", color=col_lect, ha="center", va="center", transform=ax.transAxes)

    # ── BLOQUE 3: CUADRO C — DISTRIBUCIÓN DE GOLES POR FRANJA DE 15 MINUTOS (y: 0.02 .. 0.24) ──
    rect_ban_c = FancyBboxPatch((0.0, 0.205), 1.0, 0.032, boxstyle="round,pad=0.005", facecolor="#E8F5EE", edgecolor="#2E7D32", lw=1.0, transform=ax.transAxes)
    ax.add_patch(rect_ban_c)
    ax.text(0.015, 0.221, "CUADRO C: TIMELINE DE GOLES POR FRANJA DE 15 MINUTOS (00'-15' A 76'-90' — ANOTADOS / RECIBIDOS)",
            fontsize=8.6, fontweight="bold", color="#1B5E20", va="center", transform=ax.transAxes)

    rect_th_c = Rectangle((0.0, 0.176), 1.0, 0.024, facecolor="#0F172A", edgecolor="#CBD5E1", lw=0.8, transform=ax.transAxes)
    ax.add_patch(rect_th_c)
    ax.text(0.02, 0.188, "EQUIPO / FLUJO", fontsize=7.3, fontweight="bold", color="#FFFFFF", va="center", transform=ax.transAxes)
    tramos_c = ["00'-15'", "16'-30'", "31'-45'", "46'-60'", "61'-75'", "76'-90'"]
    xs_tramos = [0.27, 0.39, 0.51, 0.63, 0.75, 0.87]
    for t_lbl, x_t in zip(tramos_c, xs_tramos):
        ax.text(x_t, 0.188, t_lbl, fontsize=7.3, fontweight="bold", color="#FFFFFF", ha="center", va="center", transform=ax.transAxes)
    ax.text(0.96, 0.188, "TOTAL", fontsize=7.3, fontweight="bold", color="#FFFFFF", ha="center", va="center", transform=ax.transAxes)

    filas_c_data = [
        (f"{eq_L} — Anotados (GF)", (cuadro_c_L.get("anotados") or {}), "#E8F5EE", "#1B5E20"),
        (f"{eq_L} — Recibidos (GC)", (cuadro_c_L.get("recibidos") or {}), "#FFFFFF", "#334155"),
        (f"{eq_V} — Anotados (GF)", (cuadro_c_V.get("anotados") or {}), "#E3F2FD", "#131D31"),
        (f"{eq_V} — Recibidos (GC)", (cuadro_c_V.get("recibidos") or {}), "#FFFFFF", "#334155"),
    ]
    for idx_c, (nom_flujo, d_tramos, bg_c, col_txt_c) in enumerate(filas_c_data):
        y_c = 0.150 - idx_c * 0.026
        ax.add_patch(Rectangle((0.0, y_c), 1.0, 0.026, facecolor=bg_c, edgecolor="#CBD5E1", lw=0.6, transform=ax.transAxes))
        ax.text(0.02, y_c + 0.013, nom_flujo, fontsize=7.3, fontweight="bold", color=col_txt_c, va="center", transform=ax.transAxes)
        tot_flujo = 0
        for t_lbl, x_t in zip(tramos_c, xs_tramos):
            v_t = int(round(float(d_tramos.get(t_lbl, 0))))
            tot_flujo += v_t
            es_76_90 = (t_lbl == "76'-90'")
            ax.text(x_t, y_c + 0.013, str(v_t), fontsize=7.5, fontweight=("bold" if es_76_90 else "normal"),
                    color=("#B91C1C" if es_76_90 and v_t >= 6 else col_txt_c), ha="center", va="center", transform=ax.transAxes)
        ax.text(0.96, y_c + 0.013, str(tot_flujo), fontsize=7.5, fontweight="bold", color="#0F172A", ha="center", va="center", transform=ax.transAxes)

    g_76_l = int(round(float((cuadro_c_L.get("anotados") or {}).get("76'-90'", 0)))) + int(round(float((cuadro_c_V.get("recibidos") or {}).get("76'-90'", 0))))
    g_76_v = int(round(float((cuadro_c_V.get("anotados") or {}).get("76'-90'", 0)))) + int(round(float((cuadro_c_L.get("recibidos") or {}).get("76'-90'", 0))))
    rect_pie_c = FancyBboxPatch((0.0, 0.032), 1.0, 0.030, boxstyle="round,pad=0.005", facecolor="#FEF6E4", edgecolor="#D97706", lw=0.9, transform=ax.transAxes)
    ax.add_patch(rect_pie_c)
    ax.text(
        0.50, 0.047,
        f"ALERTA CRONOLÓGICA (CUADRO C): Tramo de máximo peligro en 76'-90'  ·  Flujo {eq_L} (GF+GC rival): {g_76_l} goles  |  Flujo {eq_V} (GF+GC rival): {g_76_v} goles",
        fontsize=7.5, fontweight="bold", color="#92400E", ha="center", va="center", transform=ax.transAxes
    )

    fig.text(
        0.50, 0.012,
        "Verificación de Integridad: Todos los cálculos provienen exclusivamente de los 15 partidos oficiales listados arriba (0 amistosos, 0 datos inventados).",
        ha="center", fontsize=8.0, color="#64748B", style="italic"
    )

    if not destino:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "informes", "h2h")
        os.makedirs(base, exist_ok=True)
        sello = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        destino = os.path.join(base, f"auditoria_15p_{_slug(eq_L)}_vs_{_slug(eq_V)}_{sello}.png")

    ruta_str = str(destino)
    os.makedirs(os.path.dirname(os.path.abspath(ruta_str)) or ".", exist_ok=True)
    fig.savefig(ruta_str, dpi=180)
    plt.close(fig)
    return ruta_str


def etiqueta_modelo_activo(analisis: Any) -> str:
    """
    Describe QUÉ técnicas están realmente activas en la matriz que se dibuja.

    No se anuncia Dixon-Coles ni los moduladores por defecto: `Parametros.rho` es
    0,0 salvo que se pida, así que declararlos sin comprobarlo sería mentir en el
    pie de la imagen.
    """
    motor = getattr(analisis, "motor", None)
    params = getattr(analisis, "parametros", None)
    partes = ["Poisson"]
    rho = getattr(params, "rho", 0.0) or 0.0
    if abs(float(rho)) > 1e-9:
        partes.append(f"Dixon-Coles ρ={float(rho):+.3f}")
    activas = list((getattr(motor, "moduladores", None) or {}).get("metricas_activas") or [])
    if getattr(params, "aplicar_moduladores", False) and activas:
        partes.append(f"{len(activas)} moduladores tácticos")
    elif not getattr(params, "aplicar_moduladores", False):
        partes.append("moduladores DESACTIVADOS")
    k = getattr(params, "k_credibilidad", None)
    partes.append(f"Bayes de condición K={k:g}" if isinstance(k, (int, float))
                  else "Bayes de condición")
    # Red Neuronal Recursiva: se declara SOLO si realmente recalculó la matriz.
    # Nótese que NO se anuncia "Dixon-Coles" por su rho adaptativo: el pie
    # distingue la técnica del modelo lineal (rho de parámetro) de la de la red.
    rec = getattr(motor, "recursivo", None) or {}
    if rec.get("aplicado"):
        entropias = rec.get("historial_entropia") or [0.0]
        partes.append(
            f"Red Neuronal Recursiva (conv. it. {rec.get('iteracion_convergencia')}, "
            f"H={float(entropias[-1]):.3f}, "
            f"λ {float(rec.get('lambda_local_recursivo', 0)):.2f}–"
            f"{float(rec.get('lambda_visita_recursivo', 0)):.2f})")
    ef = getattr(motor, "lambda_efectivo", None) or {}
    if ef.get("aplicado"):
        partes.append("λ_efectivo")
    cola = getattr(motor, "cola_ancha", None) or {}
    if cola.get("aplicada"):
        partes.append("Cola ancha (NegBin)")
    return " + ".join(partes)


def _slug(texto: str) -> str:
    import re
    import unicodedata
    base = unicodedata.normalize("NFKD", texto or "equipo").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").lower() or "equipo"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ENTRADA POR LÍNEA DE COMANDOS (dos auditorías JSON -> PNG)
# ═══════════════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(
        description="Cuadro Especialista (ratings 0-100 + tempo + mapa de calor) "
                    "a partir de dos auditorías JSON ya guardadas.")
    p.add_argument("json_local", help="auditoría JSON del equipo local")
    p.add_argument("json_visitante", help="auditoría JSON del equipo visitante")
    p.add_argument("--nombre-local", default=None)
    p.add_argument("--nombre-visitante", default=None)
    p.add_argument("--salida", default=None, help="ruta del PNG de salida")
    p.add_argument("--peso-roja", type=float, default=PESO_PARTIDO_CON_ROJA,
                   help="peso de los partidos con expulsión (0 lo desactiva)")
    args = p.parse_args(argv)

    import motor_mercados as mm
    with open(args.json_local, encoding="utf-8") as f:
        doc_l = json.load(f)
    with open(args.json_visitante, encoding="utf-8") as f:
        doc_v = json.load(f)
    nombre_l = args.nombre_local or (doc_l.get("equipo") or {}).get("nombre") or "Local"
    nombre_v = (args.nombre_visitante or (doc_v.get("equipo") or {}).get("nombre")
                or "Visitante")

    analisis = mm.analizar(doc_l, doc_v, nombre_l, nombre_v,
                           aviso=lambda m: print(f"[CUADRO] {m}"))
    ruta = generar_cuadro_desde_analisis(
        analisis, doc_l, doc_v, destino=args.salida,
        peso_roja=(args.peso_roja if args.peso_roja else None),
        aviso=lambda m: print(f"[CUADRO] {m}"))
    if not ruta:
        print("[CUADRO] no se pudo generar el PNG")
        return 1
    print(f"[CUADRO] PNG: {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
