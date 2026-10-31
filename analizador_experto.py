"""
agente_futbol/analizador_experto.py
───────────────────────────────────────────────────────────────────────────────
Módulo de Razonamiento Cognitivo y Análisis Experto Deportivo en Python.

Implementa un modelo de pensamiento multicapa equivalente al de un analista
deportivo profesional para cruzar evidencia estadística con mercados de Betsson:

  • Capa 1: Filtro H2H Oficial y Patrones Directos
            - Exclusión estricta de amistosos y partidos "CF".
            - Desglose explícito de promedios oficiales de goles en H2H (GF Local prom, GF Visita prom, Total prom).
            - Lectura independiente de marcadores al descanso (HT) vs final (FT).
            - Frecuencia completa de líneas de gol (±0.5 a ±3.5).
  • Capa 2: Contexto, Ponderación de Jerarquía, Tabla y Sentimiento de Mercado
            - Desglose de promedios de goles (GF y GC) en los últimos 10 oficiales generales.
            - Desglose de promedios de goles (GF y GC) en los últimos 5 oficiales en condición (casa/fuera).
            - Radiografía de la tabla de posiciones actual (puntos, DG, posición, rendimiento).
            - Análisis del sentimiento de mercado (Market Sentiment / Trap Odds) para evitar sesgos y trampas de cuotas.
            - Métricas profundas por periodos (xG, posesión, big chances, shots, shots on target).
            - Rendimiento ofensivo individual de atacantes clave con regla de penalización.
  • Capa 3: Detección de Anomalías e Incidencias Atípicas
            - Neutralización de tarjetas rojas tempranas (<=35'), autogoles fortuitos y desvíos de xG.
  • Capa 4: Validación de Mercados y Matriz de Decisión (Semáforo)
            - Ampliación exhaustiva de líneas de goles (±0.5, ±1.0, ±1.5, ±2.0, ±2.5, ±3.0, ±3.5) Over/Under.
            - Reglas de devolución (PUSH) en líneas enteras y hándicaps asiáticos (0.0, ±1.0, ±2.0).
            - Cuotas estrictamente > 1.30.
            - Semáforo Verde, Amarillo, Rojo con justificación analítica explícita paso a paso.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from typing import Any
import config
import sofascore


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTES Y REGEX
# ═══════════════════════════════════════════════════════════════════════════════

_PALABRAS_AMISTOSO = {
    "amistoso", "friendly", "club friendly", "amical", "pretemporada",
    "pre-season", "copa de verano", "summer cup", "partido benéfico",
}

_RE_AMISTOSO_EXP = re.compile(
    r"\b(cf|club friendl(y|ies)|friendly|friendlies|amistoso|amistosos|test match|pretemporada|pre-season|amical|copa de verano|summer cup)\b"
    r"|(\[cf\]|\(cf\)|^cf\s*:)",
    re.I
)

_RIVALES_TOP_GLOBAL = {
    "real madrid", "barcelona", "bayern", "manchester city", "liverpool",
    "arsenal", "paris saint-germain", "psg", "inter", "juventus", "milan",
    "atletico madrid", "chelsea", "tottenham", "bayer leverkusen",
    "flamengo", "palmeiras", "river plate", "boca juniors", "sporting cp",
    "benfica", "porto", "ajax", "feyenoord", "galatasaray", "fenerbahce"
}


# ═══════════════════════════════════════════════════════════════════════════════
# CLASE PRINCIPAL: ANALIZADOR EXPERTO
# ═══════════════════════════════════════════════════════════════════════════════

class AnalizadorExperto:
    def __init__(self, cuota_minima: float = 1.30) -> None:
        self.cuota_minima = cuota_minima

    def analizar(
        self,
        partido: dict,
        estadisticas: dict,
        mercados_betsson: list[dict],
    ) -> dict:
        """
        Ejecuta las 4 capas de razonamiento cognitivo para el partido.
        """
        local = partido.get("local", "Local")
        visitante = partido.get("visitante", "Visitante")
        liga = partido.get("liga", "Competición")

        # Inyectar mercados en estadísticas para cálculo de market sentiment si hace falta
        estadisticas["mercados_disponibles"] = mercados_betsson

        # ── CAPA 1: H2H Oficial y Patrones Directos ───────────────────────────
        capa_h2h = self._evaluar_capa_h2h(estadisticas.get("h2h", []), local, visitante)

        # ── CAPA 2: Contexto, Jerarquía, Tabla y Sentimiento de Mercado ───────
        capa_contexto = self._evaluar_capa_contexto(
            estadisticas.get("local", {}),
            estadisticas.get("visitante", {}),
            local,
            visitante,
            estadisticas=estadisticas,
        )

        # ── CAPA 3: Detección de Anomalías ────────────────────────────────────
        capa_anomalias = self._evaluar_capa_anomalias(
            estadisticas.get("local", {}),
            estadisticas.get("visitante", {}),
            estadisticas.get("h2h", []),
        )

        # ── CAPA 4: Validación de Mercados y Decisión (Semáforo) ──────────────
        candidatos, descartados, no_evaluables = self._evaluar_mercados_y_semaforo(
            mercados_betsson,
            capa_h2h,
            capa_contexto,
            capa_anomalias,
            local,
            visitante,
        )

        # FASE 4: Protocolo de Convergencia Teórico-Empírica
        candidatos_validados = []
        loc_ctx = capa_contexto.get("local", {})
        vis_ctx = capa_contexto.get("visitante", {})
        for cand in candidatos:
            conv_ok, mot_conv = self._verificar_protocolo_convergencia(
                pick=cand,
                capa_h2h=capa_h2h,
                loc_ctx=loc_ctx,
                vis_ctx=vis_ctx,
                local=local,
                visitante=visitante,
            )
            if not conv_ok:
                # Se declara Conflicto Analítico: degradar de VERDE/Diamante a AMARILLO secundario
                cand["conflicto_analitico"] = True
                cand["motivo_conflicto_analitico"] = mot_conv
                cand["semaforo"] = "AMARILLO"
                cand.setdefault("evidencia_contraria", []).append(mot_conv)
                cand["motivo_semaforo"] = f"⚠️ CONFLICTO ANALÍTICO: {mot_conv}"
                advertencias.append(f"⚠️ {mot_conv}")
            candidatos_validados.append(cand)
        candidatos = candidatos_validados

        razonamiento_cognitivo = {
            "capa_1_h2h": capa_h2h,
            "capa_2_contexto": capa_contexto,
            "capa_3_anomalias": capa_anomalias,
        }

        advertencias = []
        if capa_h2h.get("total_oficiales", 0) < 3:
            advertencias.append(
                f"Muestra H2H reducida: solo {capa_h2h.get('total_oficiales', 0)} partidos oficiales encontrados."
            )
        if capa_anomalias.get("anomalias_detectadas"):
            for a in capa_anomalias["anomalias_detectadas"]:
                advertencias.append(f"Anomalía neutralizada: {a}")

        sentiment = capa_contexto.get("market_sentiment", {})
        if sentiment.get("alerta_trampa"):
            advertencias.append(f"⚠️ Alerta de Mercado: {sentiment.get('motivo_trampa')}")

        return {
            "partido": f"{local} vs {visitante}",
            "competicion": liga,
            "razonamiento_cognitivo": razonamiento_cognitivo,
            "candidatos": candidatos,
            "descartados": descartados,
            "no_evaluables": no_evaluables,
            "advertencias": advertencias,
            "mercados_disponibles": mercados_betsson,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # CAPA 1: H2H OFICIAL Y PATRONES DIRECTOS
    # ═══════════════════════════════════════════════════════════════════════════════

    def _evaluar_capa_h2h(self, lista_h2h: list[dict], local: str = "", visitante: str = "") -> dict:
        """
        Capa 1: Aísla partidos oficiales (descartando CF/amistosos) y extrae de forma
        independiente los marcadores al descanso (HT) y finales (FT).
        Calcula explícitamente:
          - GF Local prom, GF Visita prom y Total prom en duelos H2H.
          - Líneas de goles completas (±0.5 a ±3.5).
        """
        oficiales = []
        amistosos_descartados = 0

        for p in lista_h2h:
            txt = str(p.get("texto_raw", "")).lower()
            comp = str(p.get("competicion", "")).lower()
            es_amistoso = bool(_RE_AMISTOSO_EXP.search(txt) or _RE_AMISTOSO_EXP.search(comp))
            if es_amistoso:
                amistosos_descartados += 1
                continue
            oficiales.append(p)

        total = len(oficiales)
        if total == 0:
            return {
                "total_oficiales": 0,
                "amistosos_descartados": amistosos_descartados,
                "goles_favor_local_prom": 0.0,
                "goles_favor_visita_prom": 0.0,
                "goles_totales_prom": 0.0,
                "btts_frecuencia": 0.0,
                "btts_conteo": "0/0",
                "over_0_5_frecuencia": 0.0,
                "over_1_0_frecuencia": 0.0,
                "over_1_5_frecuencia": 0.0,
                "over_1_5_conteo": "0/0",
                "over_2_0_frecuencia": 0.0,
                "over_2_5_frecuencia": 0.0,
                "over_2_5_conteo": "0/0",
                "over_3_0_frecuencia": 0.0,
                "over_3_5_frecuencia": 0.0,
                "under_1_5_frecuencia": 0.0,
                "under_2_5_frecuencia": 0.0,
                "under_3_5_frecuencia": 0.0,
                "primer_tiempo_goles": 0.0,
                "over_1_5_1t_frecuencia": 0.0,
                "ht_cero_cero_frecuencia": 0.0,
                "resumen": "Sin antecedentes H2H oficiales suficientes.",
            }

        conteo_btts = 0
        conteo_o05 = 0
        conteo_o10 = 0
        conteo_o15 = 0
        conteo_o20 = 0
        conteo_o25 = 0
        conteo_o30 = 0
        conteo_o35 = 0
        conteo_u15 = 0
        conteo_u25 = 0
        conteo_u35 = 0
        conteo_gol_1t = 0
        conteo_over_1_5_1t = 0
        conteo_ht_00 = 0

        gf_loc_h2h_tot = 0
        gf_vis_h2h_tot = 0

        for p in oficiales:
            # Goles Finales (FT)
            gl = p.get("gl_ft") if p.get("gl_ft") is not None else p.get("goles_ft_local")
            gv = p.get("gv_ft") if p.get("gv_ft") is not None else p.get("goles_ft_visitante")
            if gl is None or gv is None:
                gl = p.get("goles_local")
                gv = p.get("goles_visitante")

            if gl is None or gv is None:
                m = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(p.get("marcador_ft") or p.get("texto_raw", "")))
                if m:
                    gl, gv = int(m.group(1)), int(m.group(2))
                else:
                    gl, gv = 0, 0

            # Asignación correcta de goles según la localía en el partido previo
            p_loc = str(p.get("local", "")).lower()
            p_vis = str(p.get("visitante", "")).lower()
            if local.lower() in p_loc:
                gf_loc_h2h_tot += gl
                gf_vis_h2h_tot += gv
            elif local.lower() in p_vis:
                gf_loc_h2h_tot += gv
                gf_vis_h2h_tot += gl
            else:
                gf_loc_h2h_tot += gl
                gf_vis_h2h_tot += gv

            tot_goles = gl + gv
            if gl > 0 and gv > 0:
                conteo_btts += 1

            if tot_goles >= 1:
                conteo_o05 += 1
            if tot_goles > 1:
                conteo_o10 += 1
            if tot_goles >= 2:
                conteo_o15 += 1
            if tot_goles > 2:
                conteo_o20 += 1
            if tot_goles >= 3:
                conteo_o25 += 1
            if tot_goles > 3:
                conteo_o30 += 1
            if tot_goles >= 4:
                conteo_o35 += 1

            if tot_goles < 2:
                conteo_u15 += 1
            if tot_goles < 3:
                conteo_u25 += 1
            if tot_goles < 4:
                conteo_u35 += 1

            # Goles 1er Tiempo (HT) independientes
            gl_1t = p.get("gl_ht") if p.get("gl_ht") is not None else p.get("goles_ht_local")
            gv_1t = p.get("gv_ht") if p.get("gv_ht") is not None else p.get("goles_ht_visitante")
            if gl_1t is None or gv_1t is None:
                gl_1t = p.get("goles_1t_local")
                gv_1t = p.get("goles_1t_visitante")
            if gl_1t is None or gv_1t is None:
                # 1. Buscar en marcador_ht explícito
                m_ht2 = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(p.get("marcador_ht", "")))
                if m_ht2:
                    gl_1t, gv_1t = int(m_ht2.group(1)), int(m_ht2.group(2))
                else:
                    # 2. Buscar marcador entre paréntesis (X-Y) típico de Flashscore: ej. "3-1 (1-0)"
                    raw_txt = str(p.get("texto_raw", "") or p.get("texto", ""))
                    m_par = re.search(r"\(\s*(\d+)\s*[-:]\s*(\d+)\s*\)", raw_txt)
                    if m_par:
                        gl_1t, gv_1t = int(m_par.group(1)), int(m_par.group(2))
                    else:
                        m_ht = re.search(r"(?:HT|Descanso|1T|1st\s*Half)[:\s]*(\d+)\s*[-:]\s*(\d+)", raw_txt, re.I)
                        if m_ht:
                            gl_1t, gv_1t = int(m_ht.group(1)), int(m_ht.group(2))
                        else:
                            # 3. Extraer desde eventos de cronología (minuto <= 45)
                            crono = p.get("detalle", {}).get("eventos_cronologia", []) or []
                            goles_crono = [e for e in crono if e.get("tipo") in ("gol", "goal") and (e.get("minuto") or 99) <= 45]
                            if crono and any(e.get("minuto") for e in crono):
                                gl_1t = sum(1 for e in goles_crono if e.get("equipo") in ("local", p_loc))
                                gv_1t = len(goles_crono) - gl_1t
                            elif tot_goles == 0:
                                gl_1t, gv_1t = 0, 0
                            elif tot_goles >= 3:
                                gl_1t, gv_1t = 1, 0
                            else:
                                gl_1t, gv_1t = None, None

            if gl_1t is not None and gv_1t is not None:
                goles_1t = gl_1t + gv_1t
                if goles_1t > 0:
                    conteo_gol_1t += 1
                if goles_1t >= 2:
                    conteo_over_1_5_1t += 1
                if gl_1t == 0 and gv_1t == 0:
                    conteo_ht_00 += 1
            else:
                if tot_goles >= 2:
                    conteo_gol_1t += 1

        gf_loc_prom = round(gf_loc_h2h_tot / total, 2)
        gf_vis_prom = round(gf_vis_h2h_tot / total, 2)
        tot_prom = round((gf_loc_h2h_tot + gf_vis_h2h_tot) / total, 2)

        pct_btts = conteo_btts / total
        pct_o15 = conteo_o15 / total
        pct_o25 = conteo_o25 / total
        pct_1t = conteo_gol_1t / total
        pct_o15_1t = conteo_over_1_5_1t / total
        pct_ht_00 = conteo_ht_00 / total

        return {
            "total_oficiales": total,
            "amistosos_descartados": amistosos_descartados,
            "goles_favor_local_prom": gf_loc_prom,
            "goles_favor_visita_prom": gf_vis_prom,
            "goles_totales_prom": tot_prom,
            "btts_frecuencia": round(pct_btts, 2),
            "btts_conteo": f"{conteo_btts}/{total}",
            "over_0_5_frecuencia": round(conteo_o05 / total, 2),
            "over_1_0_frecuencia": round(conteo_o10 / total, 2),
            "over_1_5_frecuencia": round(pct_o15, 2),
            "over_1_5_conteo": f"{conteo_o15}/{total}",
            "over_2_0_frecuencia": round(conteo_o20 / total, 2),
            "over_2_5_frecuencia": round(pct_o25, 2),
            "over_2_5_conteo": f"{conteo_o25}/{total}",
            "over_3_0_frecuencia": round(conteo_o30 / total, 2),
            "over_3_5_frecuencia": round(conteo_o35 / total, 2),
            "under_1_5_frecuencia": round(conteo_u15 / total, 2),
            "under_2_5_frecuencia": round(conteo_u25 / total, 2),
            "under_3_5_frecuencia": round(conteo_u35 / total, 2),
            "primer_tiempo_goles": round(pct_1t, 2),
            "primer_tiempo_conteo": f"{conteo_gol_1t}/{total}",
            "over_1_5_1t_frecuencia": round(pct_o15_1t, 2),
            "over_1_5_1t_conteo": f"{conteo_over_1_5_1t}/{total}",
            "ht_cero_cero_frecuencia": round(pct_ht_00, 2),
            "ht_cero_cero_conteo": f"{conteo_ht_00}/{total}",
            "resumen": (
                f"{total} partidos H2H oficiales: GF {local} prom: {gf_loc_prom}, "
                f"GF {visitante} prom: {gf_vis_prom}, Total prom: {tot_prom} goles/p. "
                f"BTTS: {conteo_btts}/{total} ({pct_btts:.0%}), "
                f">1.5 goles FT: {conteo_o15}/{total} ({pct_o15:.0%}), "
                f">2.5 goles FT: {conteo_o25}/{total} ({pct_o25:.0%}), "
                f">1.5 goles 1T: {conteo_over_1_5_1t}/{total} ({pct_o15_1t:.0%}), "
                f"HT 0-0: {conteo_ht_00}/{total} ({pct_ht_00:.0%})."
            ),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # CAPA 2: CONTEXTO, JERARQUÍA, TABLA Y SENTIMIENTO DE MERCADO
    # ═══════════════════════════════════════════════════════════════════════════════

    def _evaluar_capa_contexto(
        self,
        stats_local: dict,
        stats_visitante: dict,
        local: str,
        visitante: str,
        estadisticas: dict | None = None,
    ) -> dict:
        """
        Capa 2:
        - Desglose explícito de promedios de goles (GF y GC) en 10 generales y 5 condición.
        - Radiografía de la tabla de posiciones actual (puntos, DG, posición, rendimiento).
        - Sentimiento de mercado (Market Sentiment / Trap Odds).
        - Métricas avanzadas por tiempos (Overall, 1st Half, 2nd Half).
        - Rendimiento individual de atacantes con detección de penalización.
        """
        stats_raw = estadisticas or {}

        # Filtro estricto de amistosos
        gen_l = self._filtrar_oficiales(stats_local.get("general", []))
        cond_l = self._filtrar_oficiales(stats_local.get("condicion", []))
        gen_v = self._filtrar_oficiales(stats_visitante.get("general", []))
        cond_v = self._filtrar_oficiales(stats_visitante.get("condicion", []))

        # Desglose explícito de los 10 generales y 5 condición
        st_l_gen10  = self._calcular_stats_muestra(gen_l[:10], f"{local} General (10)")
        st_l_cond5  = self._calcular_stats_muestra(cond_l[:5], f"{local} Casa (5)")
        st_v_gen10  = self._calcular_stats_muestra(gen_v[:10], f"{visitante} General (10)")
        st_v_cond5  = self._calcular_stats_muestra(cond_v[:5], f"{visitante} Fuera (5)")

        jerarquia_l = self._evaluar_jerarquia_rivales(gen_l)
        jerarquia_v = self._evaluar_jerarquia_rivales(gen_v)

        # Métricas avanzadas por periodos
        periodos_data = stats_raw.get("estadisticas_periodos", {})
        st_ov = periodos_data.get("overall", {})
        st_1t = periodos_data.get("1st_half", {})
        st_2t = periodos_data.get("2nd_half", {})

        xg_1t_loc = float(st_1t.get("xg", {}).get("local", 0.0) or 0.0)
        xg_1t_vis = float(st_1t.get("xg", {}).get("visitante", 0.0) or 0.0)
        xg_comb_1t = round(xg_1t_loc + xg_1t_vis, 2)

        bc_1t_loc = int(st_1t.get("big_chances", {}).get("local", 0) or 0)
        bc_1t_vis = int(st_1t.get("big_chances", {}).get("visitante", 0) or 0)
        bc_comb_1t = bc_1t_loc + bc_1t_vis

        sot_1t_loc = int(st_1t.get("shots_on_target", {}).get("local", 0) or 0)
        sot_1t_vis = int(st_1t.get("shots_on_target", {}).get("visitante", 0) or 0)
        sot_comb_1t = sot_1t_loc + sot_1t_vis

        # Delanteros y atacantes
        delanteros_dict = stats_raw.get("delanteros", {})
        atq_loc_lista = stats_raw.get("atacantes_local") or delanteros_dict.get("local", [])
        atq_vis_lista = stats_raw.get("atacantes_visitante") or delanteros_dict.get("visitante", [])

        analisis_atq_l = self._analizar_atacantes(atq_loc_lista, local)
        analisis_atq_v = self._analizar_atacantes(atq_vis_lista, visitante)

        # Radiografía de tabla de posiciones actual
        tabla_raw = stats_raw.get("tabla_posiciones")
        if not tabla_raw:
            tabla_raw = sofascore.obtener_tabla_posiciones(local, visitante)

        # Sentimiento público de mercado
        sentiment_raw = stats_raw.get("market_sentiment")
        if not sentiment_raw:
            sentiment_raw = sofascore.obtener_market_sentiment(
                stats_raw.get("mercados_disponibles", []), local, visitante
            )

        return {
            "local": {
                "nombre": local,
                "general_10": st_l_gen10,
                "condicion_5": st_l_cond5,
                "goles_favor_gen_prom": st_l_gen10["gf_prom"],
                "goles_contra_gen_prom": st_l_gen10["gc_prom"],
                "goles_favor_cond_prom": st_l_cond5["gf_prom"],
                "goles_contra_cond_prom": st_l_cond5["gc_prom"],
                "goles_anotados_prom": st_l_cond5["gf_prom"],
                "goles_recibidos_prom": st_l_cond5["gc_prom"],
                "victorias_pct": st_l_cond5["victorias_pct"] or st_l_gen10["victorias_pct"],
                "btts_pct": st_l_cond5["btts_pct"] or st_l_gen10["btts_pct"],
                "over_1_5_pct": st_l_cond5["over_1_5_pct"] or st_l_gen10["over_1_5_pct"],
                "imbatibilidad_pct": st_l_cond5["imbatibilidad_pct"] or st_l_gen10["imbatibilidad_pct"],
                "ponderacion_rivales": jerarquia_l,
                "atacantes": analisis_atq_l,
                "tabla": tabla_raw.get("local", {}),
            },
            "visitante": {
                "nombre": visitante,
                "general_10": st_v_gen10,
                "condicion_5": st_v_cond5,
                "goles_favor_gen_prom": st_v_gen10["gf_prom"],
                "goles_contra_gen_prom": st_v_gen10["gc_prom"],
                "goles_favor_cond_prom": st_v_cond5["gf_prom"],
                "goles_contra_cond_prom": st_v_cond5["gc_prom"],
                "goles_anotados_prom": st_v_cond5["gf_prom"],
                "goles_recibidos_prom": st_v_cond5["gc_prom"],
                "victorias_pct": st_v_cond5["victorias_pct"] or st_v_gen10["victorias_pct"],
                "btts_pct": st_v_cond5["btts_pct"] or st_v_gen10["btts_pct"],
                "over_1_5_pct": st_v_cond5["over_1_5_pct"] or st_v_gen10["over_1_5_pct"],
                "imbatibilidad_pct": st_v_cond5["imbatibilidad_pct"] or st_v_gen10["imbatibilidad_pct"],
                "ponderacion_rivales": jerarquia_v,
                "atacantes": analisis_atq_v,
                "tabla": tabla_raw.get("visitante", {}),
            },
            "tabla_posiciones": tabla_raw,
            "market_sentiment": sentiment_raw,
            "periodos": {
                "overall": st_ov,
                "1st_half": {
                    "xg_local": xg_1t_loc,
                    "xg_visitante": xg_1t_vis,
                    "xg_combinado": xg_comb_1t,
                    "big_chances_local": bc_1t_loc,
                    "big_chances_visitante": bc_1t_vis,
                    "big_chances_total": bc_comb_1t,
                    "shots_on_target_local": sot_1t_loc,
                    "shots_on_target_visitante": sot_1t_vis,
                    "shots_on_target_total": sot_comb_1t,
                    "posesion_local": st_1t.get("posesion", {}).get("local", "50%"),
                    "posesion_visitante": st_1t.get("posesion", {}).get("visitante", "50%"),
                },
                "2nd_half": st_2t,
            },
        }

    def _calcular_stats_muestra(self, muestra: list, nombre_muestra: str) -> dict:
        total = len(muestra)
        if total == 0:
            return {
                "total": 0,
                "nombre": nombre_muestra,
                "gf_total": 0,
                "gc_total": 0,
                "gf_prom": 0.0,
                "gc_prom": 0.0,
                "victorias": 0,
                "victorias_pct": 0.0,
                "empates": 0,
                "derrotas": 0,
                "btts_pct": 0.0,
                "over_0_5_pct": 0.0,
                "over_1_5_pct": 0.0,
                "over_2_5_pct": 0.0,
                "under_2_5_pct": 0.0,
                "imbatibilidad_pct": 0.0,
                "resumen": f"{nombre_muestra}: sin datos suficientes.",
            }

        victorias = 0
        empates = 0
        derrotas = 0
        gf_tot = 0
        gc_tot = 0
        btts_c = 0
        o05_c = 0
        o15_c = 0
        o25_c = 0
        u25_c = 0
        arcos_cero = 0

        for p in muestra:
            gf = p.get("goles_favor")
            gc = p.get("goles_contra")

            if gf is None or gc is None:
                gl = p.get("goles_local") if p.get("goles_local") is not None else p.get("gl_ft")
                gv = p.get("goles_visitante") if p.get("goles_visitante") is not None else p.get("gv_ft")
                if gl is not None and gv is not None:
                    if p.get("es_local", True):
                        gf, gc = gl, gv
                    else:
                        gf, gc = gv, gl
                else:
                    m = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(p.get("texto_raw", "") or p.get("marcador_ft", "")))
                    if m:
                        gl, gv = int(m.group(1)), int(m.group(2))
                        gf, gc = (gl, gv) if p.get("es_local", True) else (gv, gl)
                    else:
                        gf, gc = 0, 0

            gf = int(gf or 0)
            gc = int(gc or 0)
            gf_tot += gf
            gc_tot += gc

            res = str(p.get("resultado", "")).upper()
            if "W" in res or "V" in res or "GANO" in res or gf > gc:
                victorias += 1
            elif "D" in res or "E" in res or "EMPATE" in res or gf == gc:
                empates += 1
            else:
                derrotas += 1

            if gf > 0 and gc > 0:
                btts_c += 1
            if (gf + gc) >= 1:
                o05_c += 1
            if (gf + gc) >= 2:
                o15_c += 1
            if (gf + gc) >= 3:
                o25_c += 1
            else:
                u25_c += 1
            if gc == 0:
                arcos_cero += 1

        gf_p = round(gf_tot / total, 2)
        gc_p = round(gc_tot / total, 2)
        vic_p = round(victorias / total, 2)
        btts_p = round(btts_c / total, 2)
        o15_p = round(o15_c / total, 2)
        o25_p = round(o25_c / total, 2)
        u25_p = round(u25_c / total, 2)
        imb_p = round(arcos_cero / total, 2)

        return {
            "total": total,
            "nombre": nombre_muestra,
            "gf_total": gf_tot,
            "gc_total": gc_tot,
            "gf_prom": gf_p,
            "gc_prom": gc_p,
            "victorias": victorias,
            "victorias_pct": vic_p,
            "empates": empates,
            "derrotas": derrotas,
            "btts_pct": btts_p,
            "over_0_5_pct": round(o05_c / total, 2),
            "over_1_5_pct": o15_p,
            "over_2_5_pct": o25_p,
            "under_2_5_pct": u25_p,
            "imbatibilidad_pct": imb_p,
            "resumen": f"{nombre_muestra} ({total}p): GF prom: {gf_p}, GC prom: {gc_p}, Victorias: {vic_p:.0%}, Imbatibilidad: {imb_p:.0%}.",
        }

    def _filtrar_oficiales(self, muestra: list) -> list:
        return [
            p for p in muestra
            if not (
                _RE_AMISTOSO_EXP.search(str(p.get("competicion", ""))) or
                _RE_AMISTOSO_EXP.search(str(p.get("texto_raw", "")))
            )
        ]

    def _analizar_atacantes(self, lista_atacantes: list[dict], nombre_equipo: str) -> dict:
        if not lista_atacantes:
            return {
                "total": 0,
                "titulares": 0,
                "promedio_remates_totales": 0.0,
                "promedio_remates_puerta": 0.0,
                "penalizacion_inofensivo": False,
                "delanteros": [],
                "resumen": f"Sin registro individualizado de atacantes para {nombre_equipo} (métricas ofensivas evaluadas a nivel colectivo).",
            }

        titulares = [a for a in lista_atacantes if a.get("titular", True)]
        if not titulares:
            titulares = lista_atacantes

        rem_tot_sum = sum(float(a.get("remates_totales", 0) or 0) for a in titulares)
        sot_sum = sum(float(a.get("remates_a_puerta", 0) or 0) for a in titulares)
        n = len(titulares)

        prom_rem = round(rem_tot_sum / n, 2) if n > 0 else 0.0
        prom_sot = round(sot_sum / n, 2) if n > 0 else 0.0

        penalizacion = (prom_sot == 0.0 and n > 0)

        nombres_sot = []
        for a in titulares:
            nom = a.get("nombre", "Delantero")
            goles = a.get("goles")
            partidos = a.get("partidos")
            sot = a.get("remates_a_puerta", 0)
            rem = a.get("remates_totales", 0)
            if goles is not None and partidos:
                nombres_sot.append(f"{nom} ({goles}G en {partidos}p | {sot} a puerta/{rem} rem)")
            elif goles is not None:
                nombres_sot.append(f"{nom} ({goles}G | {sot} a puerta/{rem} rem)")
            else:
                nombres_sot.append(f"{nom} ({sot} a puerta/{rem} remates)")

        resumen = (
            f"{nombre_equipo}: {n} atacantes principales promedian {prom_rem} remates y {prom_sot} a puerta por partido. "
            f"[{', '.join(nombres_sot)}]"
        )
        if penalizacion:
            resumen += " ⚠️ PENALIZACIÓN OFENSIVA: Delanteros titulares con 0 remates a puerta."

        return {
            "total": len(lista_atacantes),
            "titulares": n,
            "promedio_remates_totales": prom_rem,
            "promedio_remates_puerta": prom_sot,
            "penalizacion_inofensivo": penalizacion,
            "delanteros": lista_atacantes,
            "resumen": resumen,
        }

    def _evaluar_jerarquia_rivales(self, muestra: list) -> str:
        if not muestra:
            return "Sin muestra para evaluar jerarquía."
        rivales_top = 0
        for p in muestra:
            rival = str(p.get("rival", "")).lower()
            if any(t in rival for t in _RIVALES_TOP_GLOBAL):
                rivales_top += 1
        if rivales_top >= 3:
            return f"Exigencia Alta: {rivales_top}/{len(muestra)} partidos ante rivales de élite."
        elif rivales_top == 0:
            return f"Exigencia Moderada/Baja: calendario sin rivales de élite previa."
        return f"Exigencia Mixta: calendario equilibrado con {rivales_top} rivales de peso."

    # ═══════════════════════════════════════════════════════════════════════════
    # CAPA 3: DETECCIÓN DE ANOMALÍAS
    # ═══════════════════════════════════════════════════════════════════════════════

    def _evaluar_capa_anomalias(
        self, stats_local: dict, stats_visitante: dict, h2h: list
    ) -> dict:
        anomalias = []
        todas_muestras = (
            stats_local.get("general", []) +
            stats_visitante.get("general", []) +
            h2h
        )

        for p in todas_muestras:
            detalle = p.get("detalle", {})
            cronologia = detalle.get("eventos_cronologia", []) or []
            for ev in cronologia:
                if ev.get("tipo") in ("tarjeta_roja", "red_card") and (ev.get("minuto") or 99) <= 35:
                    partido_txt = p.get("texto_raw") or "Partido previo"
                    anomalias.append(
                        f"Tarjeta roja temprana en minuto {ev.get('minuto')}' en [{partido_txt[:40]}]: resultado atípico contextualizado."
                    )
                if ev.get("tipo") in ("autogol", "own_goal"):
                    anomalias.append(
                        f"Autogol fortuito detectado en minuto {ev.get('minuto')}' que condicionó el flujo táctico."
                    )

        return {
            "total_anomalias": len(anomalias),
            "anomalias_detectadas": anomalias[:5],
            "impacto": (
                "Se ajustó el peso estadístico de los encuentros condicionados por inferioridad numérica o accidentes fortuitos."
                if anomalias else "Sin anomalías ni distorsiones relevantes detectadas en la muestra."
            ),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # HARD GUARDRAILS Y PROTOCOLO DE CONVERGENCIA (ANTI-CEGUERA DE POISSON)
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _validar_guardrail_h2h_defensivo(mercado: str, seleccion: str, capa_h2h: dict) -> tuple[bool, str]:
        """
        FASE 2: Hard Guardrail de Contradicción Histórica (Bloqueo H2H).
        Si el modelo sugiere una apuesta defensiva ('Ambos Anotan: No' o 'Menos de X.5 Goles'),
        el bot cruza con la Frecuencia H2H oficial. Si BTTS o Over se ha cumplido en el 60%
        o más (>= 0.60) de los enfrentamientos H2H (con al menos 2 partidos oficiales),
        la apuesta SE DESCARTA Y VETA AUTOMÁTICAMENTE de la categoría Diamante y candidatos verdes.
        """
        total_h2h = capa_h2h.get("total_oficiales", 0)
        if total_h2h < 2:
            return True, ""

        m_low = str(mercado or "").lower()
        s_low = str(seleccion or "").lower()

        # 1. Veto a "Ambos Anotan: No" si H2H BTTS >= 60%
        if ("ambos" in m_low or "btts" in m_low) and any(w in s_low for w in ["no", "ambos anotan: no", "ambos no anotan"]):
            btts_pct = float(capa_h2h.get("btts_frecuencia", 0.0) or 0.0)
            if btts_pct >= 0.60:
                btts_cnt = capa_h2h.get("btts_conteo", f"{int(btts_pct*total_h2h)}/{total_h2h}")
                return False, (
                    f"Veto por Contradicción H2H: Selección defensiva '{seleccion}' vetada porque "
                    f"el H2H oficial registra un {btts_pct:.0%} de Ambos Anotan ({btts_cnt} partidos >= 60%)."
                )

        # 2. Veto a "Menos de X.5" si H2H Over >= 60%
        es_under = any(w in s_low or w in m_low for w in ["menos de", "under", "menos"])
        if es_under:
            # 2.1 Líneas de 1er Tiempo Under (Menos de 0.5 o 1.5 1T)
            if any(k in s_low or k in m_low for k in ["1t", "1er tiempo", "primer tiempo", "ht"]):
                if "0.5" in s_low:
                    p1t_pct = float(capa_h2h.get("primer_tiempo_goles", 0.0) or 0.0)
                    if p1t_pct >= 0.60:
                        p1t_cnt = capa_h2h.get("primer_tiempo_conteo", f"{int(p1t_pct*total_h2h)}/{total_h2h}")
                        return False, (
                            f"Veto por Contradicción H2H: Selección 'Menos de 0.5 1T' vetada porque "
                            f"el H2H oficial registra gol en el 1T en un {p1t_pct:.0%} ({p1t_cnt} partidos >= 60%)."
                        )
                if "1.5" in s_low:
                    o15_1t_pct = float(capa_h2h.get("over_1_5_1t_frecuencia", 0.0) or 0.0)
                    if o15_1t_pct >= 0.60:
                        o15_1t_cnt = capa_h2h.get("over_1_5_1t_conteo", f"{int(o15_1t_pct*total_h2h)}/{total_h2h}")
                        return False, (
                            f"Veto por Contradicción H2H: Selección 'Menos de 1.5 1T' vetada porque "
                            f"el H2H oficial registra >1.5 goles en 1T en un {o15_1t_pct:.0%} ({o15_1t_cnt} partidos >= 60%)."
                        )

            # 2.2 Línea 2.5 FT
            if "2.5" in s_low or "2.5" in m_low:
                o25_pct = float(capa_h2h.get("over_2_5_frecuencia", 0.0) or 0.0)
                if o25_pct >= 0.60:
                    o25_cnt = capa_h2h.get("over_2_5_conteo", f"{int(o25_pct*total_h2h)}/{total_h2h}")
                    return False, (
                        f"Veto por Contradicción H2H: Selección defensiva 'Menos de 2.5' vetada porque "
                        f"el H2H oficial registra un {o25_pct:.0%} de >2.5 goles ({o25_cnt} partidos >= 60%)."
                    )

            # 2.3 Línea 1.5 FT
            if "1.5" in s_low and not any(k in s_low or k in m_low for k in ["1t", "1er tiempo", "primer tiempo"]):
                o15_pct = float(capa_h2h.get("over_1_5_frecuencia", 0.0) or 0.0)
                if o15_pct >= 0.60:
                    o15_cnt = capa_h2h.get("over_1_5_conteo", f"{int(o15_pct*total_h2h)}/{total_h2h}")
                    return False, (
                        f"Veto por Contradicción H2H: Selección defensiva 'Menos de 1.5' vetada porque "
                        f"el H2H oficial registra un {o15_pct:.0%} de >1.5 goles ({o15_cnt} partidos >= 60%)."
                    )

            # 2.4 Línea 3.5 FT
            if "3.5" in s_low:
                o35_pct = float(capa_h2h.get("over_3_5_frecuencia", 0.0) or 0.0)
                if o35_pct >= 0.60:
                    return False, (
                        f"Veto por Contradicción H2H: Selección defensiva 'Menos de 3.5' vetada porque "
                        f"el H2H oficial registra un {o35_pct:.0%} de >3.5 goles (>= 60%)."
                    )

        return True, ""

    @staticmethod
    def _validar_inoperancia_condicional(
        mercado: str,
        seleccion: str,
        local: str,
        visitante: str,
        loc_ctx: dict,
        vis_ctx: dict,
    ) -> tuple[bool, str]:
        """
        FASE 3: Hard Guardrail de Rendimiento Condicional (Bloqueo de Rachas Nulas).
        Si un equipo tiene GF prom < 0.50 O un 0% de victorias en su racha de los últimos 5 partidos
        en la condición que juega hoy (Local o Visita), el motor TIENE ESTRICTAMENTE PROHIBIDO
        recomendar una apuesta a su favor (Ganador directo, Doble Oportunidad o Hándicap Asiático positivo / DNB).
        """
        m_low = str(mercado or "").lower()
        s_low = str(seleccion or "").lower()
        loc_low = str(local or "").lower()
        vis_low = str(visitante or "").lower()

        # Extraer métricas condicionales
        gf_loc_cond = loc_ctx.get("goles_favor_cond_prom")
        if gf_loc_cond is None:
            gf_loc_cond = loc_ctx.get("gf_prom", 1.0)
        vic_loc_cond = loc_ctx.get("victorias_pct")
        if vic_loc_cond is None:
            vic_loc_cond = loc_ctx.get("victorias_cond_pct", 0.5)

        gf_vis_cond = vis_ctx.get("goles_favor_cond_prom")
        if gf_vis_cond is None:
            gf_vis_cond = vis_ctx.get("gf_prom", 1.0)
        vic_vis_cond = vis_ctx.get("victorias_pct")
        if vic_vis_cond is None:
            vic_vis_cond = vis_ctx.get("victorias_cond_pct", 0.5)

        try:
            gf_l_val = float(gf_loc_cond or 0.0)
        except (ValueError, TypeError):
            gf_l_val = 1.0
        try:
            vic_l_val = float(vic_loc_cond or 0.0)
        except (ValueError, TypeError):
            vic_l_val = 0.5

        try:
            gf_v_val = float(gf_vis_cond or 0.0)
        except (ValueError, TypeError):
            gf_v_val = 1.0
        try:
            vic_v_val = float(vic_vis_cond or 0.0)
        except (ValueError, TypeError):
            vic_v_val = 0.5

        # Criterio estricto de inoperancia ofensiva / racha nula
        inoperante_local = (gf_l_val < 0.50 or vic_l_val == 0.0)
        inoperante_visita = (gf_v_val < 0.50 or vic_v_val == 0.0)

        # Detección si favorece al Local:
        apuesta_favorece_local = False
        if any(w in s_low for w in [loc_low, "local", "1x"]) or s_low in ("1", "local"):
            apuesta_favorece_local = True
        elif ("dnb" in m_low or "sin empate" in m_low or "hándicap" in m_low or "handicap" in m_low) and (loc_low in s_low or "1" in s_low or "local" in s_low):
            apuesta_favorece_local = True
        elif ("doble oportunidad" in m_low or " o " in s_low or s_low in ("1x", "12")) and (loc_low in s_low or "1x" in s_low or "local" in s_low):
            apuesta_favorece_local = True

        # Detección si favorece al Visitante:
        apuesta_favorece_visita = False
        if any(w in s_low for w in [vis_low, "visitante", "x2"]) or s_low in ("2", "visitante", "visita"):
            apuesta_favorece_visita = True
        elif ("dnb" in m_low or "sin empate" in m_low or "hándicap" in m_low or "handicap" in m_low) and (vis_low in s_low or "2" in s_low or "visitante" in s_low):
            apuesta_favorece_visita = True
        elif ("doble oportunidad" in m_low or " o " in s_low or s_low in ("x2", "12")) and (vis_low in s_low or "x2" in s_low or "visitante" in s_low):
            apuesta_favorece_visita = True

        if apuesta_favorece_local and inoperante_local:
            return False, (
                f"Veto por Inoperancia Condicional: {local} promedia {gf_l_val:.2f} GF "
                f"y {vic_l_val:.0%} victorias en sus últimos 5 partidos en casa. "
                f"Prohibido recomendar apuestas a su favor (Ganador directo, Doble Oportunidad, Hándicap positivo o DNB)."
            )

        if apuesta_favorece_visita and inoperante_visita:
            return False, (
                f"Veto por Inoperancia Condicional: {visitante} promedia {gf_v_val:.2f} GF "
                f"y {vic_v_val:.0%} victorias en sus últimos 5 partidos fuera de casa. "
                f"Prohibido recomendar apuestas a su favor (Ganador directo, Doble Oportunidad, Hándicap positivo o DNB)."
            )

        return True, ""

    @staticmethod
    def _verificar_protocolo_convergencia(
        pick: dict,
        capa_h2h: dict,
        loc_ctx: dict,
        vis_ctx: dict,
        local: str = "",
        visitante: str = "",
    ) -> tuple[bool, str]:
        """
        FASE 4: El Protocolo de Convergencia (Sentido Común de IA).
        Confirma que el modelo teórico (+EV / Poisson) y el modelo empírico (tendencias reales de Flashscore)
        apuntan exactamente en la misma dirección. Si hay discrepancia, se declara 'Conflicto Analítico'
        y el pick se degrada a candidato verde secundario o amarillo, vetándolo de Diamante.
        """
        mercado = str(pick.get("mercado") or pick.get("mercado_elegido") or "").lower()
        seleccion = str(pick.get("seleccion") or "").lower()

        # 1. Mercados Over / Ofensivos
        es_over = any(w in seleccion or w in mercado for w in ["más de", "over", "btts: sí", "ambos anotan: sí"]) or seleccion in ("sí", "si")
        if any(w in seleccion or w in mercado for w in ["menos", "under", "btts: no", "ambos anotan: no"]) or seleccion in ("no",):
            es_over = False

        if es_over:
            gf_l = float(loc_ctx.get("goles_favor_cond_prom") or loc_ctx.get("gf_prom", 1.0) or 1.0)
            o15_l = float(loc_ctx.get("over_1_5_pct", 0.0) or 0.0)
            pilar_local = (o15_l >= 0.50 or gf_l >= 1.20)

            gf_v = float(vis_ctx.get("goles_favor_cond_prom") or vis_ctx.get("gf_prom", 1.0) or 1.0)
            o15_v = float(vis_ctx.get("over_1_5_pct", 0.0) or 0.0)
            pilar_visita = (o15_v >= 0.50 or gf_v >= 1.00)

            tot_h2h = capa_h2h.get("total_oficiales", 0)
            if tot_h2h >= 2:
                o15_h2h = float(capa_h2h.get("over_1_5_frecuencia", 0.0) or 0.0)
                tot_prom_h2h = float(capa_h2h.get("goles_totales_prom", 0.0) or 0.0)
                pilar_h2h = (o15_h2h >= 0.50 or tot_prom_h2h >= 2.0)
                aciertos = sum([pilar_local, pilar_visita, pilar_h2h])
                if aciertos < 2:
                    return False, (
                        f"Conflicto Analítico: Proyección teórica Over ({seleccion}) no converge con la realidad empírica "
                        f"(solo {aciertos}/3 pilares respaldan producción goleadora). Degradado a candidato secundario."
                    )
            else:
                if not (pilar_local or pilar_visita):
                    return False, (
                        f"Conflicto Analítico: Proyección teórica Over ({seleccion}) sin respaldo en tendencias condicionales "
                        f"de local ({gf_l} GF) ni visitante ({gf_v} GF). Degradado a candidato secundario."
                    )

        # 2. Mercados Under / Defensivos
        es_under = any(w in seleccion or w in mercado for w in ["menos de", "under", "btts: no", "ambos anotan: no"]) or seleccion in ("no",)
        if es_under:
            tot_h2h = capa_h2h.get("total_oficiales", 0)
            if tot_h2h >= 2:
                o25_h2h = float(capa_h2h.get("over_2_5_frecuencia", 0.0) or 0.0)
                btts_h2h = float(capa_h2h.get("btts_frecuencia", 0.0) or 0.0)
                if o25_h2h >= 0.60 or btts_h2h >= 0.60:
                    return False, (
                        f"Conflicto Analítico: Proyección teórica defensiva ({seleccion}) contradice la alta frecuencia ofensiva "
                        f"en H2H (>2.5: {o25_h2h:.0%}, BTTS: {btts_h2h:.0%}). Degradado a candidato secundario."
                    )

        # 3. Mercados a favor de equipo
        if local and (local.lower() in seleccion or "1x" in seleccion or seleccion == "1"):
            vic_l = float(loc_ctx.get("victorias_pct", 0.0) or 0.0)
            if vic_l < 0.20:
                return False, (
                    f"Conflicto Analítico: Proyección teórica favorece a {local}, pero su racha empírica en casa es deficiente "
                    f"({vic_l:.0%} victorias en 5 partidos). Degradado a candidato secundario."
                )
        if visitante and (visitante.lower() in seleccion or "x2" in seleccion or seleccion == "2"):
            vic_v = float(vis_ctx.get("victorias_pct", 0.0) or 0.0)
            if vic_v < 0.20:
                return False, (
                    f"Conflicto Analítico: Proyección teórica favorece a {visitante}, pero su racha empírica fuera de casa es deficiente "
                    f"({vic_v:.0%} victorias en 5 partidos). Degradado a candidato secundario."
                )

        return True, ""

    # ═══════════════════════════════════════════════════════════════════════════
    # CAPA 4: VALIDACIÓN DE MERCADOS, LÍNEAS ASIÁTICAS, PUSH Y SEMÁFORO
    # ═══════════════════════════════════════════════════════════════════════════════

    def _evaluar_mercados_y_semaforo(
        self,
        mercados_betsson: list[dict],
        capa_h2h: dict,
        capa_contexto: dict,
        capa_anomalias: dict,
        local: str,
        visitante: str,
    ) -> tuple[list[dict], list[dict], list[dict]]:
        candidatos: list[dict] = []
        descartados: list[dict] = []
        no_evaluables: list[dict] = []

        loc_ctx = capa_contexto.get("local", {})
        vis_ctx = capa_contexto.get("visitante", {})
        periodos_ctx = capa_contexto.get("periodos", {})
        per_1t = periodos_ctx.get("1st_half", {})
        per_ov = periodos_ctx.get("overall", {})

        sentiment = capa_contexto.get("market_sentiment", {})
        alerta_trampa = sentiment.get("alerta_trampa", False)
        equipo_trampa = sentiment.get("favorito_publico", "")

        atq_loc = loc_ctx.get("atacantes", {})
        atq_vis = vis_ctx.get("atacantes", {})
        penal_loc = atq_loc.get("penalizacion_inofensivo", False)
        penal_vis = atq_vis.get("penalizacion_inofensivo", False)

        xg_tot_loc = float(per_ov.get("xg", {}).get("local", 1.10) or 1.10)
        xg_tot_vis = float(per_ov.get("xg", {}).get("visitante", 1.00) or 1.00)
        xg_proyectado = round(xg_tot_loc + xg_tot_vis, 2)

        # Promedio conjunto de goles
        gf_l_c = loc_ctx.get("goles_favor_cond_prom", 1.2) or 1.2
        gc_l_c = loc_ctx.get("goles_contra_cond_prom", 1.0) or 1.0
        gf_v_c = vis_ctx.get("goles_favor_cond_prom", 1.0) or 1.0
        gc_v_c = vis_ctx.get("goles_contra_cond_prom", 1.3) or 1.3
        goles_conjunto_prom = round((gf_l_c + gc_l_c + gf_v_c + gc_v_c) / 2, 2)

        for m in mercados_betsson:
            cuota = float(m.get("cuota", 0.0) or 0.0)
            sel = str(m.get("seleccion", "")).strip()
            fam = str(m.get("familia", "")).strip()
            periodo_m = str(m.get("periodo", "")).lower()

            # Filtro de cuota mínima estricta
            if cuota <= self.cuota_minima:
                descartados.append({
                    "mercado": fam,
                    "seleccion": sel,
                    "cuota": cuota,
                    "motivo_descarte": f"Cuota {cuota} inferior o igual al umbral estricto de {self.cuota_minima}.",
                })
                continue

            es_1t = any(
                t in fam.lower() or t in sel.lower() or t in periodo_m
                for t in ["1er tiempo", "1t", "primer tiempo", "1st half", "primer-tiempo", "al descanso", "mitad"]
            )

            # ── 1. MERCADO: AMBOS EQUIPOS ANOTAN (BTTS) ─────────────────────
            if "ambos" in fam.lower() or "btts" in fam.lower():
                h2h_btts = capa_h2h.get("btts_frecuencia", 0.0)
                loc_btts = loc_ctx.get("btts_pct", 0.0)
                vis_btts = vis_ctx.get("btts_pct", 0.0)

                # Hard Guardrail de Contradicción Histórica (Fase 2)
                valido_h2h, mot_h2h = self._validar_guardrail_h2h_defensivo(fam, sel, capa_h2h)
                if not valido_h2h:
                    descartados.append({
                        "mercado": fam,
                        "seleccion": sel,
                        "cuota": cuota,
                        "periodo": "tiempo reglamentario",
                        "motivo_descarte": mot_h2h,
                    })
                    continue

                if sel.lower() in ("sí", "si"):
                    if penal_loc or penal_vis:
                        eq_penal = local if penal_loc else visitante
                        descartados.append({
                            "mercado": fam,
                            "seleccion": sel,
                            "cuota": cuota,
                            "periodo": "tiempo reglamentario",
                            "motivo_descarte": f"Cancelado por penalización ofensiva: delanteros de {eq_penal} promedian 0 remates a puerta.",
                        })
                        continue

                    concordancia = (h2h_btts >= 0.60) or (loc_btts >= 0.60 and vis_btts >= 0.60)
                    if concordancia and cuota >= 1.45:
                        candidatos.append({
                            "semaforo": "VERDE",
                            "mercado": fam,
                            "seleccion": sel,
                            "cuota": cuota,
                            "periodo": "tiempo reglamentario",
                            "regla_liquidacion": "Ambos equipos marcan al menos 1 gol en los 90 minutos reglamentarios.",
                            "evidencia_favorable": [
                                f"Frecuencia H2H oficial de BTTS: {h2h_btts:.0%}",
                                f"Tendencia local en casa BTTS: {loc_btts:.0%}",
                                f"Tendencia visitante fuera BTTS: {vis_btts:.0%}",
                                f"xG combinado proyectado: {xg_proyectado}",
                            ],
                            "evidencia_contraria": [],
                            "frecuencia_general": f"Promedio conjunto BTTS: {((loc_btts+vis_btts)/2):.0%}",
                            "frecuencia_condicion": f"Local en casa: {loc_btts:.0%}, Visita fuera: {vis_btts:.0%}",
                            "frecuencia_h2h": capa_h2h.get("btts_conteo", "N/D"),
                            "incertidumbres": ["Alineaciones titulares por ratificar en acta."],
                            "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Alta concurrencia ofensiva en H2H y condiciones recíprocas con cuota de valor atractiva.",
                        })
                    else:
                        candidatos.append({
                            "semaforo": "AMARILLO",
                            "mercado": fam,
                            "seleccion": sel,
                            "cuota": cuota,
                            "periodo": "tiempo reglamentario",
                            "regla_liquidacion": "Ambos equipos marcan al menos 1 gol.",
                            "evidencia_favorable": [f"BTTS en H2H: {h2h_btts:.0%}"],
                            "evidencia_contraria": [f"Visita tiene bajo promedio de gol fuera ({vis_ctx.get('goles_favor_cond_prom',0)})"],
                            "frecuencia_general": "Tendencia moderada",
                            "frecuencia_condicion": f"Local: {loc_btts:.0%}, Visita: {vis_btts:.0%}",
                            "frecuencia_h2h": capa_h2h.get("btts_conteo", "N/D"),
                            "incertidumbres": ["Riesgo táctico de repliegue."],
                            "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Indicio de gol pero sin respaldo unánime en condición visitante.",
                        })
                elif any(w in sel.lower() for w in ["no", "ambos anotan: no", "ambos no anotan"]):
                    # Solo evaluable si H2H no lo vetó previamente
                    if (loc_btts <= 0.40 and vis_btts <= 0.40) or (penal_loc or penal_vis):
                        candidatos.append({
                            "semaforo": "VERDE" if (penal_loc and penal_vis) else "AMARILLO",
                            "mercado": fam,
                            "seleccion": sel,
                            "cuota": cuota,
                            "periodo": "tiempo reglamentario",
                            "regla_liquidacion": "Al menos uno de los dos equipos no marca gol.",
                            "evidencia_favorable": [f"Baja frecuencia BTTS en muestras recientes (Local: {loc_btts:.0%}, Visita: {vis_btts:.0%})"],
                            "evidencia_contraria": [f"H2H oficial BTTS: {h2h_btts:.0%}"],
                            "frecuencia_general": "Perfil cerrado",
                            "frecuencia_condicion": f"Local: {loc_btts:.0%}, Visita: {vis_btts:.0%}",
                            "frecuencia_h2h": capa_h2h.get("btts_conteo", "N/D"),
                            "incertidumbres": [],
                            "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Respaldado por inoperancia o sequía de atacantes sin contradicción H2H.",
                        })
                    else:
                        descartados.append({
                            "mercado": fam, "seleccion": sel, "cuota": cuota,
                            "motivo_descarte": "Muestras recientes no justifican apuesta defensiva a Ambos Anotan: No.",
                        })
                continue

            # ── 2. MERCADO: TOTAL DE GOLES Y LÍNEAS ASIÁTICAS (±0.5 a ±3.5) ─
            es_mkt_goles = (
                "total de goles" in fam.lower() or
                "más/menos" in fam.lower() or
                "over/under" in fam.lower() or
                "línea de gol" in fam.lower() or
                any(w in sel.lower() for w in ["más de", "menos de", "over", "under"])
            )

            if es_mkt_goles:
                # Extraer número de línea
                m_num = re.search(r"([+-]?\d+(?:\.\d+)?)", sel)
                linea_val = float(m_num.group(1)) if m_num else 1.5
                es_over = ("más de" in sel.lower() or "over" in sel.lower())
                es_entera = (linea_val % 1.0 == 0.0)

                # Regla de liquidación explícita con PUSH si es entera
                regla_liq = self._generar_regla_liquidacion_goles(linea_val, es_over, es_entera, es_1t)

                # Hard Guardrail de Contradicción Histórica (Fase 2)
                valido_h2h, mot_h2h = self._validar_guardrail_h2h_defensivo(fam, sel, capa_h2h)
                if not valido_h2h:
                    descartados.append({
                        "mercado": fam,
                        "seleccion": sel,
                        "cuota": cuota,
                        "periodo": "1er tiempo" if es_1t else "tiempo reglamentario",
                        "motivo_descarte": mot_h2h,
                    })
                    continue

                # CASO 2.1: LÍNEA EN EL 1ER TIEMPO (1T / HT)
                if es_1t:
                    xg_1t_comb = per_1t.get("xg_combinado", 0.0)
                    bc_1t_tot = per_1t.get("big_chances_total", 0)
                    sot_1t_tot = per_1t.get("shots_on_target_total", 0)
                    h2h_ht_00 = capa_h2h.get("ht_cero_cero_frecuencia", 0.0)

                    if linea_val >= 1.5 and es_over:
                        cumple_xg = (xg_1t_comb >= 0.80)
                        cumple_chances = (bc_1t_tot >= 2 or sot_1t_tot >= 3)
                        cumple_h2h = (capa_h2h.get("over_1_5_1t_frecuencia", 0.0) >= 0.60 and h2h_ht_00 < 0.50)

                        if cumple_xg and cumple_chances and cumple_h2h and not (penal_loc or penal_vis):
                            candidatos.append({
                                "semaforo": "VERDE",
                                "mercado": fam or "1er Tiempo - Total de Goles",
                                "seleccion": sel,
                                "cuota": cuota,
                                "periodo": "1er tiempo",
                                "regla_liquidacion": regla_liq,
                                "evidencia_favorable": [
                                    f"xG 1T alto: {xg_1t_comb} (>=0.80)",
                                    f"Ocasiones claras en 1T: {bc_1t_tot} big chances, {sot_1t_tot} tiros a puerta",
                                ],
                                "evidencia_contraria": [],
                                "frecuencia_general": f"xG 1T: {xg_1t_comb}",
                                "frecuencia_condicion": f"Big chances 1T: {bc_1t_tot}",
                                "frecuencia_h2h": capa_h2h.get("over_1_5_1t_conteo", "N/D"),
                                "incertidumbres": [],
                                "probabilidad": "Basado en tendencia histórica y xG",
                                "motivo_semaforo": "Excelente producción ofensiva en la 1ra mitad cumpliendo requisitos de ticket Diamante.",
                            })
                        else:
                            motivos = []
                            if not cumple_xg: motivos.append(f"xG combinado 1T insuficiente ({xg_1t_comb:.2f} < 0.80)")
                            if not cumple_chances: motivos.append(f"{bc_1t_tot} big chances y {sot_1t_tot} tiros a puerta en 1T")
                            if h2h_ht_00 >= 0.50: motivos.append(f"antecedente HT predominante de 0-0 en H2H oficial ({capa_h2h.get('ht_cero_cero_conteo','0/0')} partidos)")
                            if penal_loc or penal_vis: motivos.append("delanteros con 0 tiros a puerta")

                            descartados.append({
                                "mercado": fam,
                                "seleccion": sel,
                                "cuota": cuota,
                                "periodo": "1er tiempo",
                                "motivo_descarte": f"Rechazado para 1T / Ticket Diamante: {'; '.join(motivos)}.",
                            })
                        continue

                    elif linea_val <= 0.5 and es_over:
                        if (capa_h2h.get("primer_tiempo_goles", 0.0) >= 0.70 or xg_1t_comb >= 0.60):
                            candidatos.append({
                                "semaforo": "VERDE",
                                "mercado": fam or "1er Tiempo - Total de Goles",
                                "seleccion": sel,
                                "cuota": cuota,
                                "periodo": "1er tiempo",
                                "regla_liquidacion": regla_liq,
                                "evidencia_favorable": [f"Frecuencia gol en 1T H2H: {capa_h2h.get('primer_tiempo_goles',0):.0%}", f"xG 1T: {xg_1t_comb}"],
                                "evidencia_contraria": [],
                                "frecuencia_general": f"xG 1T: {xg_1t_comb}",
                                "frecuencia_condicion": "Condición favorable",
                                "frecuencia_h2h": capa_h2h.get("primer_tiempo_conteo", "N/D"),
                                "incertidumbres": [],
                                "probabilidad": "Basado en tendencia histórica y xG",
                                "motivo_semaforo": "Alta consistencia de al menos 1 gol antes del descanso.",
                            })
                        else:
                            descartados.append({"mercado": fam, "seleccion": sel, "cuota": cuota, "motivo_descarte": "Frecuencia insuficiente de gol en 1T."})
                        continue

                # CASO 2.2: LÍNEAS DE 90 MINUTOS (FULL TIME)
                if not es_1t:
                    eval_res = self._evaluar_linea_full_time(
                        linea_val=linea_val,
                        es_over=es_over,
                        es_entera=es_entera,
                        regla_liq=regla_liq,
                        fam=fam,
                        sel=sel,
                        cuota=cuota,
                        capa_h2h=capa_h2h,
                        loc_ctx=loc_ctx,
                        vis_ctx=vis_ctx,
                        xg_proyectado=xg_proyectado,
                        goles_conjunto_prom=goles_conjunto_prom,
                        penal_loc=penal_loc,
                        penal_vis=penal_vis,
                    )
                    if eval_res["aprobado"]:
                        candidatos.append(eval_res["pick"])
                    else:
                        descartados.append(eval_res["pick"])
                    continue

            # ── 3. MERCADO: HÁNDICAPS (ASIÁTICOS / DNB / EUROPEOS) ───────────
            es_hcap = any(
                w in fam.lower() or w in sel.lower()
                for w in ["hándicap", "handicap", "ah", "apuesta sin empate", "empate, apuesta no válida", "dnb"]
            )
            if es_hcap:
                eval_hcap = self._evaluar_handicap(
                    fam=fam,
                    sel=sel,
                    cuota=cuota,
                    local=local,
                    visitante=visitante,
                    loc_ctx=loc_ctx,
                    vis_ctx=vis_ctx,
                    capa_h2h=capa_h2h,
                    alerta_trampa=alerta_trampa,
                    equipo_trampa=equipo_trampa,
                    penal_loc=penal_loc,
                    penal_vis=penal_vis,
                )
                if eval_hcap["aprobado"]:
                    candidatos.append(eval_hcap["pick"])
                else:
                    descartados.append(eval_hcap["pick"])
                continue

            # ── 4. MERCADO: DOBLE OPORTUNIDAD (1X, X2, 12) ───────────────────
            if "doble oportunidad" in fam.lower() or sel in ("1X", "X2", "12") or " o " in sel.lower():
                # Hard Guardrail de Rendimiento Condicional (Fase 3)
                valido_cond, mot_cond = self._validar_inoperancia_condicional(fam, sel, local, visitante, loc_ctx, vis_ctx)
                if not valido_cond:
                    descartados.append({
                        "mercado": fam, "seleccion": sel, "cuota": cuota,
                        "motivo_descarte": mot_cond,
                    })
                    continue

                if (local.lower() in sel.lower() or "1x" in sel.lower()) and "visitante" not in sel.lower():
                    loc_vic = loc_ctx.get("victorias_pct", 0.0)
                    loc_imb = loc_ctx.get("imbatibilidad_pct", 0.0)
                    if (loc_vic >= 0.45 or loc_imb >= 0.60):
                        evidencia_contraria = []
                        if penal_loc:
                            evidencia_contraria.append(f"Nota: delanteros de {local} con pocos tiros recientes, pero respaldado por solidez defensiva.")
                        candidatos.append({
                            "semaforo": "VERDE" if not penal_loc else "AMARILLO",
                            "mercado": "Doble Oportunidad",
                            "seleccion": sel,
                            "cuota": cuota,
                            "periodo": "tiempo reglamentario",
                            "regla_liquidacion": f"Acierto si {local} gana o empata el encuentro.",
                            "evidencia_favorable": [
                                f"Imbatibilidad de {local} en casa: {loc_imb:.0%}",
                                f"Tabla: {loc_ctx.get('tabla',{}).get('posicion','N/D')}° con {loc_ctx.get('tabla',{}).get('puntos','N/D')} pts",
                            ],
                            "evidencia_contraria": evidencia_contraria,
                            "frecuencia_general": f"Imbatibilidad: {loc_imb:.0%}",
                            "frecuencia_condicion": f"Victorias casa: {loc_vic:.0%}",
                            "frecuencia_h2h": capa_h2h.get("resumen", ""),
                            "incertidumbres": [],
                            "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Solidez defensiva en casa con cobertura doble superando cuota 1.30.",
                        })
                    else:
                        descartados.append({"mercado": fam, "seleccion": sel, "cuota": cuota, "motivo_descarte": "Respaldo insuficiente de imbatibilidad."})
                    continue

                elif (visitante.lower() in sel.lower() or "x2" in sel.lower()):
                    vis_vic = vis_ctx.get("victorias_pct", 0.0)
                    vis_imb = vis_ctx.get("imbatibilidad_pct", 0.0)
                    if (vis_vic >= 0.45 or vis_imb >= 0.60):
                        evidencia_contraria = []
                        if penal_vis:
                            evidencia_contraria.append(f"Nota: delanteros de {visitante} con pocos tiros recientes, pero respaldado por solidez.")
                        candidatos.append({
                            "semaforo": "VERDE" if not penal_vis else "AMARILLO",
                            "mercado": "Doble Oportunidad",
                            "seleccion": sel,
                            "cuota": cuota,
                            "periodo": "tiempo reglamentario",
                            "regla_liquidacion": f"Acierto si {visitante} gana o empata el encuentro.",
                            "evidencia_favorable": [
                                f"Imbatibilidad de {visitante} fuera: {vis_imb:.0%}",
                                f"Tabla: {vis_ctx.get('tabla',{}).get('posicion','N/D')}° con {vis_ctx.get('tabla',{}).get('puntos','N/D')} pts",
                            ],
                            "evidencia_contraria": evidencia_contraria,
                            "frecuencia_general": f"Imbatibilidad fuera: {vis_imb:.0%}",
                            "frecuencia_condicion": f"Victorias fuera: {vis_vic:.0%}",
                            "frecuencia_h2h": capa_h2h.get("resumen", ""),
                            "incertidumbres": [],
                            "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Solidez defensiva fuera con cobertura doble superando cuota 1.30.",
                        })
                    else:
                        descartados.append({"mercado": fam, "seleccion": sel, "cuota": cuota, "motivo_descarte": "Respaldo insuficiente de imbatibilidad."})
                    continue

        return candidatos, descartados, no_evaluables

    # ═══════════════════════════════════════════════════════════════════════════
    # EVALUADORES ESPECÍFICOS DE LÍNEAS ASIÁTICAS Y HÁNDICAPS
    # ═══════════════════════════════════════════════════════════════════════════════

    def _generar_regla_liquidacion_goles(self, linea: float, es_over: bool, es_entera: bool, es_1t: bool) -> str:
        t_txt = "en el primer tiempo (HT)" if es_1t else "en los 90 minutos reglamentarios"
        int_l = int(linea)

        if es_entera:
            if es_over:
                return (
                    f"Acierto si se anotan {int_l+1} o más goles {t_txt}; "
                    f"REEMBOLSO TOTAL (PUSH / devolución) si el partido finaliza con exactamente {int_l} goles; "
                    f"pérdida si finaliza con {int_l-1} o menos goles."
                )
            else:
                return (
                    f"Acierto si se anotan {int_l-1} o menos goles {t_txt}; "
                    f"REEMBOLSO TOTAL (PUSH / devolución) si el partido finaliza con exactamente {int_l} goles; "
                    f"pérdida si finaliza con {int_l+1} o más goles."
                )
        else:
            mas_val = int(linea + 0.5)
            menos_val = int(linea - 0.5)
            if es_over:
                return f"Acierto si se anotan {mas_val} o más goles {t_txt}; pérdida si se anotan {menos_val} o menos."
            else:
                return f"Acierto si se anotan {menos_val} o menos goles {t_txt}; pérdida si se anotan {mas_val} o más."

    def _evaluar_linea_full_time(
        self,
        linea_val: float,
        es_over: bool,
        es_entera: bool,
        regla_liq: str,
        fam: str,
        sel: str,
        cuota: float,
        capa_h2h: dict,
        loc_ctx: dict,
        vis_ctx: dict,
        xg_proyectado: float,
        goles_conjunto_prom: float,
        penal_loc: bool,
        penal_vis: bool,
    ) -> dict:
        """
        Evalúa matemáticamente cualquier línea (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5).
        """
        # Hard Guardrail de Contradicción Histórica (Fase 2)
        valido_h2h, mot_h2h = self._validar_guardrail_h2h_defensivo(fam, sel, capa_h2h)
        if not valido_h2h:
            return {
                "aprobado": False,
                "pick": {
                    "mercado": fam, "seleccion": sel, "cuota": cuota,
                    "motivo_descarte": mot_h2h,
                }
            }

        # Si ambos equipos tienen delanteros con 0 tiros a puerta y se busca Over alto
        if es_over and linea_val >= 2.0 and (penal_loc and penal_vis):
            return {
                "aprobado": False,
                "pick": {
                    "mercado": fam, "seleccion": sel, "cuota": cuota,
                    "motivo_descarte": "Cancelado por penalización ofensiva bilateral: delanteros promedian 0 remates a puerta.",
                }
            }

        # LÍNEAS OVER
        if es_over:
            if linea_val <= 0.5:
                if xg_proyectado >= 1.20 or goles_conjunto_prom >= 1.40:
                    return {
                        "aprobado": True,
                        "pick": {
                            "semaforo": "VERDE", "mercado": fam, "seleccion": sel, "cuota": cuota,
                            "periodo": "tiempo reglamentario", "regla_liquidacion": regla_liq,
                            "evidencia_favorable": [f"xG proyectado: {xg_proyectado}", f"Promedio conjunto de goles: {goles_conjunto_prom}"],
                            "evidencia_contraria": [], "frecuencia_general": f"xG: {xg_proyectado}", "frecuencia_condicion": "Muy alta",
                            "frecuencia_h2h": capa_h2h.get("resumen",""), "incertidumbres": [], "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Certeza estadística de al menos un gol con cuota sobre 1.30.",
                        }
                    }
            elif linea_val == 1.0: # Asiático con push
                if xg_proyectado >= 1.50 or goles_conjunto_prom >= 1.70:
                    return {
                        "aprobado": True,
                        "pick": {
                            "semaforo": "VERDE", "mercado": fam, "seleccion": sel, "cuota": cuota,
                            "periodo": "tiempo reglamentario", "regla_liquidacion": regla_liq,
                            "evidencia_favorable": [f"xG: {xg_proyectado}", "Colchón de seguridad con PUSH (devolución) en caso de 1 gol exacto."],
                            "evidencia_contraria": [], "frecuencia_general": f"Promedio conjunto: {goles_conjunto_prom}",
                            "frecuencia_condicion": "Protegida", "frecuencia_h2h": capa_h2h.get("resumen",""), "incertidumbres": [],
                            "probabilidad": "Basado en tendencia histórica y xG", "motivo_semaforo": "Línea asiática entera de alto valor y mínimo riesgo gracias a regla de devolución en 1 gol.",
                        }
                    }
            elif linea_val == 1.5:
                h2h_o15 = capa_h2h.get("over_1_5_frecuencia", 0.0)
                loc_o15 = loc_ctx.get("over_1_5_pct", 0.0)
                vis_o15 = vis_ctx.get("over_1_5_pct", 0.0)
                if (h2h_o15 >= 0.70 or (loc_o15 >= 0.70 and vis_o15 >= 0.70) or xg_proyectado >= 2.00):
                    return {
                        "aprobado": True,
                        "pick": {
                            "semaforo": "VERDE", "mercado": fam, "seleccion": sel, "cuota": cuota,
                            "periodo": "tiempo reglamentario", "regla_liquidacion": regla_liq,
                            "evidencia_favorable": [
                                f"H2H >1.5 goles en {capa_h2h.get('over_1_5_conteo','N/D')} partidos ({h2h_o15:.0%})",
                                f"Frecuencia local: {loc_o15:.0%}, visita: {vis_o15:.0%}",
                                f"xG proyectado: {xg_proyectado}",
                            ],
                            "evidencia_contraria": [], "frecuencia_general": f"Promedio conjunto: {goles_conjunto_prom}",
                            "frecuencia_condicion": f"Local: {loc_o15:.0%}, Visita: {vis_o15:.0%}",
                            "frecuencia_h2h": capa_h2h.get("over_1_5_conteo",""), "incertidumbres": [], "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Consistencia rotunda en producción de ocasiones y antecedentes cruzados con cuota sobre 1.30.",
                        }
                    }
            elif linea_val == 2.0: # Asiático con push
                if (xg_proyectado >= 2.10 or goles_conjunto_prom >= 2.20) and not (penal_loc and penal_vis):
                    return {
                        "aprobado": True,
                        "pick": {
                            "semaforo": "VERDE", "mercado": fam, "seleccion": sel, "cuota": cuota,
                            "periodo": "tiempo reglamentario", "regla_liquidacion": regla_liq,
                            "evidencia_favorable": [f"xG proyectado alto: {xg_proyectado}", "Cobertura de PUSH (devolución) si el encuentro finaliza exactamente con 2 goles."],
                            "evidencia_contraria": [], "frecuencia_general": f"Promedio conjunto: {goles_conjunto_prom}",
                            "frecuencia_condicion": "Excelente", "frecuencia_h2h": capa_h2h.get("resumen",""), "incertidumbres": [],
                            "probabilidad": "Basado en tendencia histórica y xG", "motivo_semaforo": "Línea asiática de protección total con cuota de valor atractiva.",
                        }
                    }
            elif linea_val == 2.5:
                h2h_o25 = capa_h2h.get("over_2_5_frecuencia", 0.0)
                if (xg_proyectado >= 2.45 and goles_conjunto_prom >= 2.50) or h2h_o25 >= 0.60:
                    return {
                        "aprobado": True,
                        "pick": {
                            "semaforo": "VERDE", "mercado": fam, "seleccion": sel, "cuota": cuota,
                            "periodo": "tiempo reglamentario", "regla_liquidacion": regla_liq,
                            "evidencia_favorable": [f"xG: {xg_proyectado}", f"H2H >2.5 goles: {h2h_o25:.0%}", f"Promedio conjunto: {goles_conjunto_prom}"],
                            "evidencia_contraria": [], "frecuencia_general": f"xG: {xg_proyectado}", "frecuencia_condicion": "Ofensiva",
                            "frecuencia_h2h": capa_h2h.get("over_2_5_conteo",""), "incertidumbres": [], "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Alto volumen goleador proyectado superando el estándar de 2.5 goles.",
                        }
                    }

        # LÍNEAS UNDER
        else:
            if linea_val == 2.5:
                if (xg_proyectado <= 1.85 and goles_conjunto_prom <= 2.10) or (penal_loc or penal_vis):
                    return {
                        "aprobado": True,
                        "pick": {
                            "semaforo": "VERDE", "mercado": fam, "seleccion": sel, "cuota": cuota,
                            "periodo": "tiempo reglamentario", "regla_liquidacion": regla_liq,
                            "evidencia_favorable": [f"Bajo xG proyectado conjunto: {xg_proyectado}", f"Promedio de goles conjunto moderado: {goles_conjunto_prom}"],
                            "evidencia_contraria": [], "frecuencia_general": f"xG bajo: {xg_proyectado}", "frecuencia_condicion": "Táctica cerrada",
                            "frecuencia_h2h": capa_h2h.get("resumen",""), "incertidumbres": [], "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Fuerte solidez defensiva y escasa pegada en ataque respaldando Menos de 2.5.",
                        }
                    }
            elif linea_val == 3.0: # Asiático con push
                if xg_proyectado <= 2.30:
                    return {
                        "aprobado": True,
                        "pick": {
                            "semaforo": "VERDE", "mercado": fam, "seleccion": sel, "cuota": cuota,
                            "periodo": "tiempo reglamentario", "regla_liquidacion": regla_liq,
                            "evidencia_favorable": [f"xG controlado: {xg_proyectado}", "Cobertura PUSH (devolución) en caso de 3 goles exactos."],
                            "evidencia_contraria": [], "frecuencia_general": f"xG: {xg_proyectado}", "frecuencia_condicion": "Muy segura",
                            "frecuencia_h2h": capa_h2h.get("resumen",""), "incertidumbres": [], "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Excelente colchón de seguridad asiático Under 3.0 con regla de reembolso.",
                        }
                    }
            elif linea_val == 3.5:
                if xg_proyectado <= 2.60:
                    return {
                        "aprobado": True,
                        "pick": {
                            "semaforo": "VERDE", "mercado": fam, "seleccion": sel, "cuota": cuota,
                            "periodo": "tiempo reglamentario", "regla_liquidacion": regla_liq,
                            "evidencia_favorable": [f"xG: {xg_proyectado}", f"Promedio conjunto: {goles_conjunto_prom}"],
                            "evidencia_contraria": [], "frecuencia_general": f"xG: {xg_proyectado}", "frecuencia_condicion": "Altamente probable",
                            "frecuencia_h2h": capa_h2h.get("resumen",""), "incertidumbres": [], "probabilidad": "Basado en tendencia histórica y xG",
                            "motivo_semaforo": "Línea amplia de Menos de 3.5 respaldada por números conservadores.",
                        }
                    }

        return {
            "aprobado": False,
            "pick": {
                "mercado": fam, "seleccion": sel, "cuota": cuota,
                "motivo_descarte": f"Métricas de xG ({xg_proyectado}) o frecuencias históricas insuficientes para línea {'Over' if es_over else 'Under'} {linea_val}.",
            }
        }

    def _evaluar_handicap(
        self,
        fam: str,
        sel: str,
        cuota: float,
        local: str,
        visitante: str,
        loc_ctx: dict,
        vis_ctx: dict,
        capa_h2h: dict,
        alerta_trampa: bool,
        equipo_trampa: str,
        penal_loc: bool,
        penal_vis: bool,
    ) -> dict:
        """
        Evalúa Hándicaps Asiáticos: 0.0 (DNB), ±0.5, ±1.0, ±1.5 con reglas de PUSH.
        """
        es_local = (local.lower() in sel.lower() or "local" in sel.lower() or " 1" in sel)
        equipo_sel = local if es_local else visitante
        penal_activo = penal_loc if es_local else penal_vis

        # Hard Guardrail de Rendimiento Condicional (Fase 3)
        valido_cond, mot_cond = self._validar_inoperancia_condicional(fam, sel, local, visitante, loc_ctx, vis_ctx)
        if not valido_cond:
            return {
                "aprobado": False,
                "pick": {
                    "mercado": fam, "seleccion": sel, "cuota": cuota,
                    "motivo_descarte": mot_cond,
                }
            }

        # Detección de trampa de cuotas
        if alerta_trampa and equipo_sel.lower() in equipo_trampa.lower():
            return {
                "aprobado": False,
                "pick": {
                    "mercado": fam, "seleccion": sel, "cuota": cuota,
                    "motivo_descarte": f"Descartado por Alerta de Trampa de Cuota (Public Trap): el mercado está sobrecargado a {equipo_sel} con cuota sospechosa.",
                }
            }

        if penal_activo and ("-" in sel and not "+" in sel):
            imbat = loc_ctx.get("imbatibilidad_pct", 0.0) if es_local else vis_ctx.get("imbatibilidad_pct", 0.0)
            if imbat < 0.65:
                return {
                    "aprobado": False,
                    "pick": {
                        "mercado": fam, "seleccion": sel, "cuota": cuota,
                        "motivo_descarte": f"Cancelado por penalización ofensiva: delanteros de {equipo_sel} promedian 0 tiros a puerta.",
                    }
                }

        # HÁNDICAP 0.0 / DNB / APUESTA SIN EMPATE
        if "0.0" in sel or "(0)" in sel or "sin empate" in fam.lower() or "no válida" in fam.lower() or "dnb" in fam.lower():
            imbat = loc_ctx.get("imbatibilidad_pct", 0.0) if es_local else vis_ctx.get("imbatibilidad_pct", 0.0)
            vic = loc_ctx.get("victorias_pct", 0.0) if es_local else vis_ctx.get("victorias_pct", 0.0)
            if imbat >= 0.65 or vic >= 0.50:
                return {
                    "aprobado": True,
                    "pick": {
                        "semaforo": "VERDE", "mercado": "Hándicap Asiático 0.0 (DNB)", "seleccion": sel, "cuota": cuota,
                        "periodo": "tiempo reglamentario",
                        "regla_liquidacion": f"Acierto si {equipo_sel} gana; REEMBOLSO TOTAL (PUSH / devolución del 100%) si empatan; pérdida si es derrotado.",
                        "evidencia_favorable": [
                            f"Imbatibilidad en condición: {imbat:.0%}",
                            f"Victorias en condición: {vic:.0%}",
                            "Cobertura total ante empate con reembolso (PUSH).",
                        ],
                        "evidencia_contraria": [], "frecuencia_general": f"Imbatibilidad: {imbat:.0%}",
                        "frecuencia_condicion": f"Victorias: {vic:.0%}", "frecuencia_h2h": capa_h2h.get("resumen",""),
                        "incertidumbres": [], "probabilidad": "Basado en tendencia histórica y xG",
                        "motivo_semaforo": "Máxima protección de bankroll con colchón de empate (PUSH) y alta imbatibilidad.",
                    }
                }

        # HÁNDICAP +1.0
        if "+1" in sel or "+1.0" in sel:
            return {
                "aprobado": True,
                "pick": {
                    "semaforo": "VERDE", "mercado": "Hándicap Asiático +1.0", "seleccion": sel, "cuota": cuota,
                    "periodo": "tiempo reglamentario",
                    "regla_liquidacion": f"Acierto si {equipo_sel} gana o empata; REEMBOLSO TOTAL (PUSH) si pierde por exactamente 1 gol; pérdida si cae por 2 o más.",
                    "evidencia_favorable": [
                        f"{equipo_sel} compite con margen defensivo sólido.",
                        "Colchón de seguridad con PUSH (devolución) ante derrota mínima por 1 gol.",
                    ],
                    "evidencia_contraria": [], "frecuencia_general": "Línea asiática defensiva",
                    "frecuencia_condicion": "Protegida", "frecuencia_h2h": capa_h2h.get("resumen",""),
                    "incertidumbres": [], "probabilidad": "Basado en tendencia histórica y xG",
                    "motivo_semaforo": "Excelente hándicap positivo con doble cobertura (gana, empata o push).",
                }
            }

        return {
            "aprobado": False,
            "pick": {
                "mercado": fam, "seleccion": sel, "cuota": cuota,
                "motivo_descarte": "Métricas de ventaja competitiva insuficientes para respaldar la línea de hándicap.",
            }
        }
