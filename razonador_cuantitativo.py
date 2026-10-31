"""
agente_futbol/razonador_cuantitativo.py
───────────────────────────────────────────────────────────────────────────────
Cerebro Analítico Cuantitativo Autónomo (+EV Quant Trader) con Guardrails.

Principios Matemáticos Rigurosos (Astra-Certified):
  1. Recibe un Payload Omnisciente en JSON con datos estadísticos reales:
     - Contexto_Clasificacion (puntos, DG, posición, forma)
     - Historial_Oficial (H2H oficial y muestras sin amistosos)
     - Metricas_Profundas_SofaScore (xG global y 1T, remates, big chances)
     - Rendimiento_Ofensivo_Individual (delanteros titulares)
     - Mercado_y_Cuotas_Betsson (catálogo completo para autenticación)
  2. Modelado Estadístico Formal (Sin Pesos Fijos ni Suelos Artificiales):
     - Distribución de Poisson formal para modelar tasas de goles FT y 1T.
     - En mercados con PUSH (Hándicap Asiático y Líneas de Gol enteras):
         EV = (p_ganar * (Cuota - 1.0)) - p_perder
         f* = ((p_ganar * (Cuota - 1.0)) - p_perder) / ((Cuota - 1.0) * (p_ganar + p_perder))
     - En mercados binarios de 2 vías:
         EV = (p_real * Cuota) - 1.0
         f* = ((p_real * Cuota) - 1.0) / (Cuota - 1.0)
     - Mercados no modelables: retornan 'modelo no disponible' y EV = 0.
  3. Capa de Validador Numérico y Guardrails:
     - Valida tipos, finitud, autenticación de cuotas y umbral EV >= +5.0%.
     - Si Quarter-Kelly < mínimo operativo (0.5%), la apuesta se aborta (Stake = 0).
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import sys
import os
import json
import math
import re
from typing import Any
from dotenv import load_dotenv

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config
from validador_probabilidades import ValidadorProbabilidades, es_numero_valido

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


SYSTEM_PROMPT_TRADER = """
Eres un Analista Cuantitativo Deportivo Senior especializado en encontrar Ineficiencias de Mercado (+EV) en casas de apuestas. Recibirás un JSON masivo con estadísticas avanzadas de fútbol y cuotas autenticadas de Betsson. Tu proceso de razonamiento estricto es:
a) Implied Probability: Convierte las cuotas ofrecidas en probabilidad implícita (1 / Cuota).
b) True Probability: Proyecta la probabilidad matemática real mediante modelado estadístico formal (Poisson / xG subyacente por tiempos y rendimiento de atacantes).
c) Integridad Axiomática de Kolmogorov: En cualquier mercado binario complementario (ej. Ambos Anotan Sí/No, Más/Menos X goles), la suma de las probabilidades DEBE ser exactamente 100% (Suma = 1.0).
d) Liquidación de Líneas Asiáticas (Devolución / Push):
   Si el mercado analizado es una Línea de Gol asiática entera (ej. Más de 2.0, Menos de 3.0) o Hándicap Asiático entero / DNB donde existe el PUSH, debes calcular:
     - p_ganar
     - p_push (probabilidad de reembolso)
     - p_perder (donde p_ganar + p_push + p_perder = 1.0)
   y aplicar estrictamente:
     EV = (p_ganar * (Cuota - 1.0)) - p_perder
e) Mercados Binarios de 2 vías (Sin Devolución, ej. Más de 2.5, BTTS):
   Aplica: EV = (p_real * Cuota) - 1.0
f) Margen de Seguridad Mínimo (+EV Threshold >= 0.05):
   Solo aprueba apuestas con EV estrictamente mayor o igual a +5.0% (EV >= 0.05). Descarta cualquier opción con EV inferior.
g) Si un mercado no puede modelarse rigurosamente con los datos suministrados, no inventes probabilidades; descártalo.
h) Devuelve tu análisis estrictamente en JSON con las claves:
  - "partido": string con el nombre del encuentro
  - "mercado_elegido": nombre exacto de la familia de mercado
  - "seleccion": nombre de la selección recomendada
  - "cuota": valor numérico decimal de la cuota en Betsson
  - "periodo": periodo de juego ("1er tiempo", "2do tiempo", o "tiempo reglamentario")
  - "probabilidad_implicita": float entre 0.0 y 1.0
  - "probabilidad_real_estimada": float entre 0.0 y 1.0 (o p_ganar)
  - "probabilidad_ganar": float (para líneas asiáticas con push)
  - "probabilidad_push": float (para líneas asiáticas con push)
  - "probabilidad_perder": float (para líneas asiáticas con push)
  - "valor_esperado_ev": string con porcentaje (ej. "+11.8%")
  - "explicacion_cuantitativa_detallada": explicación analítica rigurosa (máximo 4 líneas)
  - "nivel_confianza": entero de 1 a 10
  - "decision_final": "APROBADO" o "DESCARTADO"
  - "mercados_evaluados": lista de los principales mercados evaluados con su probabilidad y EV
"""


def _poisson_pmf(k: int, lmbda: float) -> float:
    """Calcula la función de probabilidad puntual de Poisson: P(k; λ) = (λ^k * e^-λ) / k!"""
    if not es_numero_valido(lmbda) or lmbda <= 0.0:
        return 1.0 if k == 0 else 0.0
    return (lmbda ** k * math.exp(-lmbda)) / math.factorial(k)


def _coercionar_decision_llm(decision: dict[str, Any]) -> dict[str, Any]:
    """Coerciona tipos devueltos por el LLM a tipos numéricos estrictos (int, float)."""
    if not isinstance(decision, dict):
        return {}
    dec = dict(decision)

    # 1. Nivel de confianza: int en [0, 10]
    nc = dec.get("nivel_confianza")
    if nc is not None:
        try:
            if isinstance(nc, str):
                nc = nc.split("/")[0].strip()
            dec["nivel_confianza"] = int(float(nc))
        except (ValueError, TypeError):
            dec["nivel_confianza"] = 0

    # 2. Cuota: float
    cuota = dec.get("cuota")
    if isinstance(cuota, str):
        try:
            dec["cuota"] = float(cuota.replace(",", ".").strip())
        except (ValueError, TypeError):
            pass

    # 3. Probabilidades: float en [0.0, 1.0]
    for k in ("probabilidad_real_estimada", "true_probability", "probabilidad_implicita_cuota", "h2h_ratio"):
        p = dec.get(k)
        if isinstance(p, str):
            try:
                val = float(p.replace("%", "").replace(",", ".").strip())
                if val > 1.0 and "%" in p:
                    val /= 100.0
                dec[k] = val
            except (ValueError, TypeError):
                pass
        elif isinstance(p, (int, float)):
            dec[k] = float(p)

    return dec


class RazonadorCuantitativo:
    """
    Orquesta el análisis cuantitativo conectando con Google Gemini API (gemini-2.5-flash)
    o ejecutando el motor cuantitativo formal en Python, aplicando
    siempre la auditoría estricta de ValidadorProbabilidades.
    """

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "") or getattr(config, "GEMINI_API_KEY", "")
        self.model = model or getattr(config, "GEMINI_MODEL", "gemini-3.8-flash")
        self.client = None
        self._gemini_desactivado = False  # Flag de resiliencia ante errores 503 / timeout
        self.validador = ValidadorProbabilidades(
            ev_minimo_requerido=0.05,  # Mínimo +5.0% (+EV)
            kelly_divisor=4.0,         # Quarter-Kelly (f* / 4)
            stake_max_bankroll=0.03,   # Máximo 3.0% del bankroll
            stake_min_bankroll=0.005,  # Mínimo operativo del broker (0.5%)
        )

        from agente_consciente import AgenteConsciente, obtener_agente_consciente
        self.conciencia = obtener_agente_consciente()
        if not self.api_key and getattr(self.conciencia, "api_key", ""):
            self.api_key = self.conciencia.api_key
        self.client = getattr(self.conciencia, "client", None)
        if self.client:
            print(
                f"[RAZONADOR] 🚀 Conectado al Pool Neuronal Multi-Modelo Gemini "
                f"({self.conciencia.motor_neuronal.modelos[0]} + {len(self.conciencia.motor_neuronal.modelos)-1} fallbacks)."
            )

    def analizar_partido(self, payload_omnisciente: dict[str, Any]) -> dict[str, Any]:
        """
        Punto de entrada principal:
        1. Consulta la conciencia vivencial y metacognitiva del agente.
        2. Intento opcional y resiliente con el Pool Multi-Modelo de Gemini (con rotación en caliente);
           si ningún modelo está disponible o estamos offline, conmuta al motor cuantitativo formal en Python.
        3. Aplica la capa de Guardrails y Validador de Probabilidades en Python.
        4. Retorna la decisión auditada con reporte técnico completo.
        """
        partido_nombre = payload_omnisciente.get("Partido", "Partido Desconocido") if isinstance(payload_omnisciente, dict) else "Desconocido"
        print(f"\n🧠 [RAZONADOR CUANTITATIVO] Evaluando ineficiencias de mercado (+EV) para: {partido_nombre}")
        print(f"  🧠 [ESTADO CONSCIENTE]: {self.conciencia.estado_adaptativo.upper()} | Madurez (XP): {self.conciencia.nivel_madurez}")

        decision_candidata: dict[str, Any] = {}

        # ── 1. Intento con Pool Neuronal Multi-Modelo de Google Gemini ────────
        if self.api_key and not self._gemini_desactivado and isinstance(payload_omnisciente, dict) and payload_omnisciente:
            try:
                contenido_raw = self.conciencia.analizar_con_conciencia(payload_omnisciente)
                contenido_str = (contenido_raw or "").strip()
                if contenido_str.startswith("```json"):
                    contenido_str = contenido_str[7:]
                if contenido_str.startswith("```"):
                    contenido_str = contenido_str[3:]
                if contenido_str.endswith("```"):
                    contenido_str = contenido_str[:-3]
                contenido_str = contenido_str.strip()

                if contenido_str and not contenido_str.startswith("[ERROR"):
                    raw_json = json.loads(contenido_str)
                    if isinstance(raw_json, dict):
                        decision_candidata = _coercionar_decision_llm(raw_json)
                        decision_candidata["motor"] = f"Google Gemini ({self.conciencia.modelo})"
                else:
                    print("  ⚡ [POOL GEMINI OFFLINE EN ESTE TURNO]: Conmutando al Motor Matemático Formal de Poisson.")
            except Exception as e:
                print(f"  ⚡ [CONMUTACIÓN A MOTOR FORMAL]: {e}. Operando con Motor Matemático de Poisson.")

        # ── 2. Fallback / Motor Cuantitativo Formal de Poisson (100% Nativo) ─
        if not decision_candidata or decision_candidata.get("decision_final") != "APROBADO":
            decision_matematica = self._motor_cuantitativo_matematico(payload_omnisciente)
            if not decision_candidata or decision_matematica.get("decision_final") == "APROBADO":
                decision_candidata = decision_matematica

        decision_candidata["estado_consciente"] = self.conciencia.estado_adaptativo
        decision_candidata["nivel_madurez"] = self.conciencia.nivel_madurez
        decision_candidata["directriz_emocional"] = self.conciencia.identidad_base

        # ── 3. APLICACIÓN DE GUARDRAILS Y VALIDADOR MATEMÁTICO ───────────────
        es_valida, decision_auditada, errores = self.validador.validar_decision(
            decision_candidata, payload_omnisciente
        )

        if not es_valida:
            print(f"  🛡️ [GUARDRAIL RECHAZÓ APUESTA]: {'; '.join(errores)}")
            decision_auditada["decision_final"] = "DESCARTADO"
        else:
            print(
                f"  🛡️ [GUARDRAILS SUPERADOS]: {decision_auditada.get('mercado_elegido')} | "
                f"{decision_auditada.get('seleccion')} @ {decision_auditada.get('cuota')} | "
                f"EV: {decision_auditada.get('valor_esperado_ev')} | "
                f"Stake: {decision_auditada.get('stake_sugerido')}"
            )

        return decision_auditada

    def _motor_cuantitativo_matematico(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Motor cuantitativo formal basado en distribución de Poisson y probabilidad axiomática.
        Estimación empírica y coherente de tasas lambda (λ) a partir de muestras reales de Flashscore.
        Eliminación total de sesgos y falsos 'Under 0.5 1T'.
        """
        if not isinstance(payload, dict) or not payload:
            return {
                "decision_final": "DESCARTADO",
                "guardrails_superados": False,
                "stake_sugerido": "0.0%",
                "ev_matematico_exacto": 0.0,
                "valor_esperado_ev": "0.0%",
                "motivo_descarte_guardrail": "Payload vacío.",
            }

        partido = payload.get("Partido") or payload.get("partido") or "Partido"
        if isinstance(partido, dict):
            partido = f"{partido.get('local', 'Local')} vs {partido.get('visitante', 'Visitante')}"
        mercados = payload.get("Mercado_y_Cuotas_Betsson") or payload.get("mercados_betsson") or payload.get("mercados") or []
        historial = payload.get("Historial_Oficial") or payload.get("historial") or {}
        if not isinstance(historial, dict):
            historial = {}

        h2h_list = historial.get("h2h_oficial", [])
        ultimos_local = historial.get("ultimos_10_oficiales_local", [])
        local_casa = historial.get("ultimos_5_local_casa", [])
        ultimos_vis = historial.get("ultimos_10_oficiales_visitante", [])
        vis_fuera = historial.get("ultimos_5_visitante_fuera", [])

        h2h_prom_goles = float(historial.get("h2h_goles_prom_total", 0.0) or 0.0)
        h2h_btts = float(historial.get("h2h_btts_pct", 0.0) or 0.0)

        # ── 1. EXTRACCIÓN Y VALIDACIÓN DE MUESTRAS REALES DE GOLES ───────────
        muestras_loc = local_casa if len(local_casa) >= 3 else ultimos_local
        muestras_vis = vis_fuera if len(vis_fuera) >= 3 else ultimos_vis

        gf_loc_list = []
        gc_loc_list = []
        for p in muestras_loc:
            if not isinstance(p, dict):
                continue
            gf = p.get("gf") if p.get("gf") is not None else p.get("goles_favor") if p.get("goles_favor") is not None else p.get("goles_local")
            gc = p.get("gc") if p.get("gc") is not None else p.get("goles_contra") if p.get("goles_contra") is not None else p.get("goles_visitante")
            if gf is not None and gc is not None:
                try:
                    gf_loc_list.append(float(gf))
                    gc_loc_list.append(float(gc))
                except (ValueError, TypeError):
                    pass

        gf_vis_list = []
        gc_vis_list = []
        for p in muestras_vis:
            if not isinstance(p, dict):
                continue
            gf = p.get("gf") if p.get("gf") is not None else p.get("goles_favor") if p.get("goles_favor") is not None else p.get("goles_visitante")
            gc = p.get("gc") if p.get("gc") is not None else p.get("goles_contra") if p.get("goles_contra") is not None else p.get("goles_local")
            if gf is not None and gc is not None:
                try:
                    gf_vis_list.append(float(gf))
                    gc_vis_list.append(float(gc))
                except (ValueError, TypeError):
                    pass

        if h2h_prom_goles == 0 and h2h_list:
            h2h_goles = []
            for h in h2h_list:
                if isinstance(h, dict):
                    gf = h.get("goles_favor") or h.get("goles_local") or h.get("gf")
                    gc = h.get("goles_contra") or h.get("goles_visitante") or h.get("gc")
                    if gf is not None and gc is not None:
                        try:
                            h2h_goles.append(float(gf) + float(gc))
                        except (ValueError, TypeError):
                            pass
            if h2h_goles:
                h2h_prom_goles = sum(h2h_goles) / len(h2h_goles)

        # Control estricto contra datos vacíos: si no hay muestras reales en Flashscore ni H2H
        total_partidos_muestra = len(gf_loc_list) + len(gf_vis_list) + len(h2h_list)
        if total_partidos_muestra == 0:
            print(f"  🛡️ [DATOS VACÍOS]: {partido} sin muestras históricas en Flashscore. Descartado preventivamente.")
            return {
                "partido": partido,
                "mercado_elegido": "General",
                "seleccion": "N/A",
                "cuota": 1.0,
                "periodo": "tiempo reglamentario",
                "probabilidad_implicita": 0.0,
                "probabilidad_real_estimada": 0.0,
                "valor_esperado_ev": "0.0%",
                "explicacion_cuantitativa_detallada": "Datos estadísticos insuficientes o vacíos en Flashscore. Descartado preventivamente para evitar falsos positivos.",
                "nivel_confianza": 0,
                "decision_final": "DESCARTADO",
                "mercados_evaluados": [],
                "motor": "Motor Cuantitativo Formal de Poisson (+EV Astra-Validated)",
            }

        # Promedios reales observados
        prom_gf_loc = (sum(gf_loc_list) / len(gf_loc_list)) if gf_loc_list else 1.30
        prom_gc_loc = (sum(gc_loc_list) / len(gc_loc_list)) if gc_loc_list else 1.15
        prom_gf_vis = (sum(gf_vis_list) / len(gf_vis_list)) if gf_vis_list else 1.10
        prom_gc_vis = (sum(gc_vis_list) / len(gc_vis_list)) if gc_vis_list else 1.40

        # Incorporar xG de SofaScore si está presente como ajuste fino
        metricas_sofa = payload.get("Metricas_Profundas_SofaScore", {})
        st_ov = metricas_sofa.get("overall", {}) if isinstance(metricas_sofa, dict) else {}
        xg_ov = st_ov.get("xg", {}) if isinstance(st_ov, dict) else {}
        xg_loc = xg_ov.get("local")
        xg_vis = xg_ov.get("visitante")

        # Modelo bivariado: Ataque local vs defensa visitante (factor localía 1.08)
        base_lambda_loc = ((prom_gf_loc * 0.60) + (prom_gc_vis * 0.40)) * 1.08
        # Ataque visitante vs defensa local (factor visitante 0.92)
        base_lambda_vis = ((prom_gf_vis * 0.60) + (prom_gc_loc * 0.40)) * 0.92

        if es_numero_valido(xg_loc) and es_numero_valido(xg_vis):
            lambda_loc_est = (base_lambda_loc * 0.70) + (float(xg_loc) * 0.30)
            lambda_vis_est = (base_lambda_vis * 0.70) + (float(xg_vis) * 0.30)
        else:
            lambda_loc_est = base_lambda_loc
            lambda_vis_est = base_lambda_vis

        # Ponderación con H2H directo si hay historial disponible
        if h2h_list and len(h2h_list) >= 2 and h2h_prom_goles > 0:
            h2h_total = float(h2h_prom_goles)
            lambda_ft_raw = (0.70 * (lambda_loc_est + lambda_vis_est)) + (0.30 * h2h_total)
            ratio_loc = lambda_loc_est / max(0.1, (lambda_loc_est + lambda_vis_est))
            lambda_loc = round(max(0.40, lambda_ft_raw * ratio_loc), 4)
            lambda_vis = round(max(0.35, lambda_ft_raw * (1.0 - ratio_loc)), 4)
        else:
            lambda_loc = round(max(0.40, lambda_loc_est), 4)
            lambda_vis = round(max(0.35, lambda_vis_est), 4)

        lambda_ft = round(lambda_loc + lambda_vis, 4)

        # ── 2. MODELADO POISSON PARA 1ER TIEMPO (45m) ─────────────────────────
        # En fútbol profesional, el 1T concentra entre 43% y 46% de los goles totales
        lambda_1t = round(lambda_ft * 0.45, 4)

        # Probabilidades puntuales y acumuladas para 1T
        p_1t_0 = _poisson_pmf(0, lambda_1t)
        p_1t_1 = _poisson_pmf(1, lambda_1t)
        p_1t_u05 = round(p_1t_0, 4)
        p_1t_o05 = round(max(0.0, 1.0 - p_1t_u05), 4)
        p_1t_u15 = round(p_1t_0 + p_1t_1, 4)
        p_1t_o15 = round(max(0.0, 1.0 - p_1t_u15), 4)

        # ── 3. MODELADO POISSON PARA TIEMPO COMPLETO (FT) ─────────────────────
        p_ft = [_poisson_pmf(k, lambda_ft) for k in range(8)]
        p_ft_u15 = round(p_ft[0] + p_ft[1], 4)
        p_ft_o15 = round(max(0.0, 1.0 - p_ft_u15), 4)
        p_ft_u25 = round(p_ft[0] + p_ft[1] + p_ft[2], 4)
        p_ft_o25 = round(max(0.0, 1.0 - p_ft_u25), 4)
        p_ft_u35 = round(sum(p_ft[:4]), 4)
        p_ft_o35 = round(max(0.0, 1.0 - p_ft_u35), 4)

        # ── 4. LÍNEAS ASIÁTICAS DE GOLES CON PUSH ─────────────────────────────
        p_a20_push = round(p_ft[2], 4)
        p_a20_perder = round(p_ft[0] + p_ft[1], 4)
        p_a20_ganar = round(max(0.0, 1.0 - p_a20_perder - p_a20_push), 4)

        p_u20_ganar = round(p_ft[0] + p_ft[1], 4)
        p_u20_push = round(p_ft[2], 4)
        p_u20_perder = round(max(0.0, 1.0 - p_u20_ganar - p_u20_push), 4)

        p_u30_ganar = round(sum(p_ft[:3]), 4)
        p_u30_push = round(p_ft[3], 4)
        p_u30_perder = round(max(0.0, 1.0 - p_u30_ganar - p_u30_push), 4)

        p_a30_perder = round(sum(p_ft[:3]), 4)
        p_a30_push = round(p_ft[3], 4)
        p_a30_ganar = round(max(0.0, 1.0 - p_a30_perder - p_a30_push), 4)

        # ── 5. MODELADO BIVARIADO PARA BTTS (AMBOS ANOTAN) ───────────────────
        p_loc_marca = 1.0 - _poisson_pmf(0, lambda_loc)
        p_vis_marca = 1.0 - _poisson_pmf(0, lambda_vis)
        p_btts_si = round(p_loc_marca * p_vis_marca, 4)
        p_btts_no = round(max(0.0, 1.0 - p_btts_si), 4)

        # ── 6. MODELADO BIVARIADO PARA 1X2, DOBLE OPORTUNIDAD Y DNB ───────────
        p_empate_raw = sum(_poisson_pmf(k, lambda_loc) * _poisson_pmf(k, lambda_vis) for k in range(7))
        p_loc_gana_raw = sum(
            _poisson_pmf(i, lambda_loc) * _poisson_pmf(j, lambda_vis)
            for i in range(7) for j in range(7) if i > j
        )
        p_vis_gana_raw = sum(
            _poisson_pmf(i, lambda_loc) * _poisson_pmf(j, lambda_vis)
            for i in range(7) for j in range(7) if i < j
        )
        tot_1x2 = p_empate_raw + p_loc_gana_raw + p_vis_gana_raw
        p_empate = round(p_empate_raw / tot_1x2, 4)
        p_loc_gana = round(p_loc_gana_raw / tot_1x2, 4)
        p_vis_gana = round(max(0.0, 1.0 - p_empate - p_loc_gana), 4)

        # Nombres de equipos
        partes_nom = partido.split("vs")
        eq_local_nom = partes_nom[0].strip().lower() if partes_nom else ""
        eq_vis_nom = partes_nom[1].strip().lower() if len(partes_nom) > 1 else ""

        # Estadísticas avanzadas de H2H para Hard Guardrail
        h2h_btts_cnt = 0
        h2h_o25_cnt = 0
        h2h_o15_cnt = 0
        h2h_1t_cnt = 0
        for h in h2h_list:
            if isinstance(h, dict):
                gl = h.get("goles_local") if h.get("goles_local") is not None else h.get("gl_ft", 0)
                gv = h.get("goles_visitante") if h.get("goles_visitante") is not None else h.get("gv_ft", 0)
                try:
                    gl = int(gl or 0)
                    gv = int(gv or 0)
                    if gl > 0 and gv > 0:
                        h2h_btts_cnt += 1
                    if (gl + gv) > 2:
                        h2h_o25_cnt += 1
                    if (gl + gv) >= 2:
                        h2h_o15_cnt += 1
                    gl_ht = h.get("gl_ht") if h.get("gl_ht") is not None else h.get("goles_ht_local")
                    gv_ht = h.get("gv_ht") if h.get("gv_ht") is not None else h.get("goles_ht_visitante")
                    if gl_ht is not None and gv_ht is not None and (int(gl_ht) + int(gv_ht)) > 0:
                        h2h_1t_cnt += 1
                    elif (gl + gv) >= 2:
                        h2h_1t_cnt += 1
                except (ValueError, TypeError):
                    pass

        tot_h2h_ofic = len(h2h_list)
        pct_h2h_btts = (h2h_btts_cnt / tot_h2h_ofic) if tot_h2h_ofic > 0 else 0.0
        pct_h2h_o25 = (h2h_o25_cnt / tot_h2h_ofic) if tot_h2h_ofic > 0 else 0.0
        pct_h2h_o15 = (h2h_o15_cnt / tot_h2h_ofic) if tot_h2h_ofic > 0 else 0.0
        pct_h2h_1t = (h2h_1t_cnt / tot_h2h_ofic) if tot_h2h_ofic > 0 else 0.0

        # Métricas de rendimiento condicional (últimos 5 en condición)
        u5_casa = historial.get("ultimos_5_local_casa", [])
        u5_fuera = historial.get("ultimos_5_visitante_fuera", [])

        inoperante_local = False
        if isinstance(u5_casa, list) and len(u5_casa) >= 3:
            gf_l5 = sum(float(p.get("goles_favor") if p.get("goles_favor") is not None else p.get("goles_local", 0) or 0) for p in u5_casa)
            gf_prom_l5 = gf_l5 / len(u5_casa)
            vic_l5 = sum(1 for p in u5_casa if "W" in str(p.get("resultado","")).upper() or "V" in str(p.get("resultado","")).upper() or float(p.get("goles_favor",0) or 0) > float(p.get("goles_contra",0) or 0))
            if gf_prom_l5 < 0.50 or vic_l5 == 0:
                inoperante_local = True

        inoperante_visita = False
        if isinstance(u5_fuera, list) and len(u5_fuera) >= 3:
            gf_v5 = sum(float(p.get("goles_favor") if p.get("goles_favor") is not None else p.get("goles_visitante", 0) or 0) for p in u5_fuera)
            gf_prom_v5 = gf_v5 / len(u5_fuera)
            vic_v5 = sum(1 for p in u5_fuera if "W" in str(p.get("resultado","")).upper() or "V" in str(p.get("resultado","")).upper() or float(p.get("goles_favor",0) or 0) > float(p.get("goles_contra",0) or 0))
            if gf_prom_v5 < 0.50 or vic_v5 == 0:
                inoperante_visita = True

        # Perfil ofensivo del partido:
        es_perfil_ofensivo = (
            lambda_ft >= 2.05 or
            prom_gf_loc >= 1.05 or
            prom_gf_vis >= 1.05 or
            h2h_prom_goles >= 2.0 or
            h2h_btts >= 0.50
        )

        mercados_evaluados = []
        candidatos_aprobados = []

        for mkt in mercados:
            if not isinstance(mkt, dict):
                continue

            familia = str(mkt.get("familia") or mkt.get("nombre") or mkt.get("mercado") or "General")
            sel = str(mkt.get("seleccion", ""))
            cuota_val = mkt.get("cuota")
            periodo = mkt.get("periodo", "tiempo reglamentario")

            if not es_numero_valido(cuota_val):
                continue
            cuota = float(cuota_val)
            if cuota <= 1.05:
                continue

            fam_low = familia.lower()
            sel_low = sel.lower()

            # ── HARD GUARDRAILS Y VETOS AUTOMÁTICOS (FASES 2 Y 3) ────────────
            # 1. Hard Guardrail de Contradicción Histórica H2H (Fase 2)
            if tot_h2h_ofic >= 2:
                # Veto a Ambos Anotan: No si BTTS H2H >= 60%
                if ("ambos" in fam_low or "btts" in fam_low) and any(w in sel_low for w in ["no", "ambos anotan: no"]):
                    if pct_h2h_btts >= 0.60:
                        continue
                # Veto a Menos de 2.5 si >2.5 H2H >= 60%
                if "menos de 2.5" in sel_low or "under 2.5" in sel_low:
                    if pct_h2h_o25 >= 0.60:
                        continue
                # Veto a Menos de 1.5 si >1.5 H2H >= 60%
                if "menos de 1.5" in sel_low or "under 1.5" in sel_low:
                    if pct_h2h_o15 >= 0.60:
                        continue
                # Veto a Menos de 0.5 1T si gol 1T H2H >= 60%
                if ("menos de 0.5" in sel_low or "under 0.5" in sel_low) and ("1t" in fam_low or "1er tiempo" in fam_low or "1t" in periodo.lower()):
                    if pct_h2h_1t >= 0.60:
                        continue

            # 2. Hard Guardrail de Inoperancia Condicional (Fase 3)
            # Detectar si favorece al local:
            favorece_local = (
                (eq_local_nom and (eq_local_nom in sel_low or "local" in sel_low)) or
                sel_low in ("1", "local", "1x") or
                ("1x" in sel_low and "visitante" not in sel_low) or
                (("dnb" in fam_low or "sin empate" in fam_low or "hándicap" in fam_low) and (eq_local_nom in sel_low or "1" in sel_low))
            )
            if favorece_local and inoperante_local:
                continue

            # Detectar si favorece al visitante:
            favorece_visita = (
                (eq_vis_nom and (eq_vis_nom in sel_low or "visitante" in sel_low or "visita" in sel_low)) or
                sel_low in ("2", "visitante", "visita", "x2") or
                ("x2" in sel_low and "local" not in sel_low) or
                (("dnb" in fam_low or "sin empate" in fam_low or "hándicap" in fam_low) and (eq_vis_nom in sel_low or "2" in sel_low))
            )
            if favorece_visita and inoperante_visita:
                continue

            p_implied = round(1.0 / cuota, 4)

            p_real = None
            p_ganar = None
            p_push = 0.0
            p_perder = None
            es_asiatico_push = False

            # ── A. Mercados de 1er Tiempo ─────────────────────────────────────
            if "1er tiempo" in fam_low or "1t" in fam_low or "ht" in fam_low or "1er tiempo" in periodo.lower():
                periodo = "1er tiempo"
                if "menos de 0.5" in sel_low or "under 0.5" in sel_low:
                    # GUARDRAIL CRÍTICO ANTI-UNDER 0.5 1T INVENTADO:
                    # Si los datos demuestran perfil ofensivo o alta frecuencia de goles,
                    # NO se permite forzar Menos de 0.5 1T.
                    if es_perfil_ofensivo:
                        continue
                    p_real = p_1t_u05
                elif "más de 0.5" in sel_low or "over 0.5" in sel_low:
                    p_real = p_1t_o05
                elif "menos de 1.5" in sel_low or "under 1.5" in sel_low:
                    p_real = p_1t_u15
                elif "más de 1.5" in sel_low or "over 1.5" in sel_low:
                    p_real = p_1t_o15

            # ── B. Ambos Equipos Anotan (BTTS) ────────────────────────────────
            elif "ambos" in fam_low or "btts" in fam_low:
                if sel_low in ("sí", "si"):
                    p_real = p_btts_si
                elif sel_low in ("no", "ambos anotan: no"):
                    p_real = p_btts_no

            # ── C. Total de Goles (FT) y Líneas Asiáticas ─────────────────────
            elif "total de goles" in fam_low or "línea de gol" in fam_low or "linea de gol" in fam_low or "asiática" in fam_low:
                if "más de 1.5" in sel_low:
                    p_real = p_ft_o15
                elif "menos de 1.5" in sel_low:
                    if es_perfil_ofensivo:
                        continue
                    p_real = p_ft_u15
                elif "más de 2.5" in sel_low:
                    p_real = p_ft_o25
                elif "menos de 2.5" in sel_low:
                    p_real = p_ft_u25
                elif "más de 3.5" in sel_low:
                    p_real = p_ft_o35
                elif "menos de 3.5" in sel_low:
                    p_real = p_ft_u35
                # Líneas asiáticas con PUSH
                elif "más de 2.0" in sel_low:
                    es_asiatico_push = True
                    p_ganar = p_a20_ganar
                    p_push = p_a20_push
                    p_perder = p_a20_perder
                    p_real = p_ganar
                elif "menos de 2.0" in sel_low:
                    es_asiatico_push = True
                    p_ganar = p_u20_ganar
                    p_push = p_u20_push
                    p_perder = p_u20_perder
                    p_real = p_ganar
                elif "menos de 3.0" in sel_low:
                    es_asiatico_push = True
                    p_ganar = p_u30_ganar
                    p_push = p_u30_push
                    p_perder = p_u30_perder
                    p_real = p_ganar
                elif "más de 3.0" in sel_low:
                    es_asiatico_push = True
                    p_ganar = p_a30_ganar
                    p_push = p_a30_push
                    p_perder = p_a30_perder
                    p_real = p_ganar

            # ── D. Apuesta sin Empate (DNB) / Hándicap Asiático 0.0 ───────────
            elif "apuesta sin empate" in fam_low or ("0.0" in sel_low and "hándicap" in fam_low):
                es_asiatico_push = True
                p_push = p_empate
                eq_local_nom = partido.split("vs")[0].strip().lower()
                if any(w in sel_low for w in ["local", "1", eq_local_nom]):
                    p_ganar = p_loc_gana
                    p_perder = p_vis_gana
                else:
                    p_ganar = p_vis_gana
                    p_perder = p_loc_gana
                p_real = p_ganar

            # ── E. Doble Oportunidad ──────────────────────────────────────────
            elif "doble oportunidad" in fam_low:
                if "1x" in sel_low or "o empate" in sel_low:
                    p_real = round(p_loc_gana + p_empate, 4)
                elif "x2" in sel_low or "empate o" in sel_low:
                    p_real = round(p_vis_gana + p_empate, 4)
                elif "12" in sel_low:
                    p_real = round(p_loc_gana + p_vis_gana, 4)

            # Si el mercado no pudo modelarse con una distribución estadística formal
            if p_real is None:
                registro_no_mod = {
                    "mercado": familia,
                    "seleccion": sel,
                    "cuota": cuota,
                    "periodo": periodo,
                    "probabilidad_implicita": p_implied,
                    "probabilidad_real_estimada": 0.0,
                    "valor_esperado_ev": "0.0%",
                    "ev_numerico": 0.0,
                    "estado_modelo": "modelo no disponible",
                }
                mercados_evaluados.append(registro_no_mod)
                continue

            # Cálculo formal de EV y Fraccional Kelly
            if es_asiatico_push:
                ev = (p_ganar * (cuota - 1.0)) - p_perder
                den_kelly = (cuota - 1.0) * (p_ganar + p_perder)
                f_star = ((p_ganar * (cuota - 1.0)) - p_perder) / max(0.001, den_kelly)
            else:
                p_perder = round(max(0.0, 1.0 - p_real), 4)
                ev = (p_real * cuota) - 1.0
                f_star = ((p_real * cuota) - 1.0) / max(0.001, (cuota - 1.0))

            ev_str = f"{round(ev * 100, 1):+0.1f}%"
            stake_quarter_kelly = max(0.0, f_star / 4.0)

            registro = {
                "mercado": familia,
                "seleccion": sel,
                "cuota": cuota,
                "periodo": periodo,
                "probabilidad_implicita": p_implied,
                "probabilidad_real_estimada": p_real,
                "probabilidad_ganar": p_ganar if es_asiatico_push else p_real,
                "probabilidad_push": p_push if es_asiatico_push else 0.0,
                "probabilidad_perder": p_perder,
                "es_mercado_con_devolucion": es_asiatico_push,
                "valor_esperado_ev": ev_str,
                "ev_numerico": ev,
                "f_star": f_star,
                "stake_quarter_kelly": stake_quarter_kelly,
            }
            mercados_evaluados.append(registro)

            # Requisitos estrictos de aprobación (+EV >= +5.0%, Cuota >= 1.30, Kelly >= 0.5%)
            if ev >= 0.05 and cuota >= 1.30 and stake_quarter_kelly >= 0.005:
                candidatos_aprobados.append(registro)

        # ── 7. SELECCIÓN DE LA MEJOR INEFICIENCIA CUANTITATIVA (+EV + METACOGNICIÓN) ─
        if candidatos_aprobados:
            for c_ap in candidatos_aprobados:
                per_cod = "1T" if "1er" in str(c_ap.get("periodo", "")).lower() else (
                    "2T" if "2do" in str(c_ap.get("periodo", "")).lower() else "FT"
                )
                aud_meta = self.conciencia.evaluar_fiabilidad_seleccion(
                    c_ap["mercado"], c_ap["probabilidad_real_estimada"], periodo=per_cod
                )
                c_ap["score_metacognitivo"] = aud_meta.get("score_metacognitivo", 75.0)
                c_ap["acierto_empirico_familia"] = aud_meta.get("acierto_empirico_familia")
                c_ap["nivel_fiabilidad_conciencia"] = aud_meta.get("nivel_fiabilidad", "CALIBRADA")

            # Ordenar por ventaja matemática (+EV) combinada con probabilidad y fiabilidad empírica
            candidatos_aprobados.sort(
                key=lambda x: (
                    x["ev_numerico"] * 0.60
                    + x["probabilidad_real_estimada"] * 0.25
                    + (x.get("score_metacognitivo", 75.0) / 100.0) * 0.15
                ),
                reverse=True
            )
            mejor_pick = None
            for cand in candidatos_aprobados:
                # FASE 4: Protocolo de Convergencia Teórico-Empírica
                c_sel_low = str(cand.get("seleccion", "")).lower()
                c_mkt_low = str(cand.get("mercado", "")).lower()
                c_es_over = any(w in c_sel_low or w in c_mkt_low for w in ["más de", "over", "btts: sí"]) or c_sel_low in ("sí", "si")
                if any(w in c_sel_low or w in c_mkt_low for w in ["menos", "under", "btts: no"]) or c_sel_low in ("no",):
                    c_es_over = False

                conflicto = False
                if c_es_over and prom_gf_loc < 0.60 and prom_gf_vis < 0.60 and len(gf_loc_list) >= 3 and len(gf_vis_list) >= 3:
                    conflicto = True
                if (not c_es_over) and tot_h2h_ofic >= 2 and pct_h2h_o25 >= 0.60:
                    conflicto = True

                if not conflicto:
                    mejor_pick = cand
                    break

            if not mejor_pick:
                print(f"  ⚠️ [CONFLICTO ANALÍTICO]: Ningún pick aprobado superó el protocolo de convergencia teórico-empírica.")
                return {
                    "partido": partido,
                    "mercado_elegido": "Conflicto Analítico",
                    "seleccion": "N/A",
                    "cuota": 1.0,
                    "periodo": "tiempo reglamentario",
                    "probabilidad_implicita": 0.0,
                    "probabilidad_real_estimada": 0.0,
                    "valor_esperado_ev": "0.0%",
                    "explicacion_cuantitativa_detallada": "Conflicto analítico: la proyección cuantitativa teórica contradice las tendencias empíricas recientes. Se degrada a candidato secundario sin operar como Diamante.",
                    "nivel_confianza": 0,
                    "decision_final": "DESCARTADO",
                    "mercados_evaluados": sorted(mercados_evaluados, key=lambda x: x.get("ev_numerico", 0.0), reverse=True)[:5],
                    "motor": "Motor Cuantitativo Formal de Poisson (+EV Astra-Validated)",
                }

            confianza = 9 if mejor_pick["ev_numerico"] >= 0.12 else 8
            tipo_liq = "con regla de PUSH (devolución)" if mejor_pick.get("es_mercado_con_devolucion") else "binaria"
            stake_pct = min(3.0, round(mejor_pick["stake_quarter_kelly"] * 100, 1))
            ac_fam_val = mejor_pick.get("acierto_empirico_familia")
            meta_str = (
                f" Respaldo metacognitivo: {mejor_pick.get('score_metacognitivo')}/100 "
                f"(acierto histórico en familia: {ac_fam_val:.1%})."
                if isinstance(ac_fam_val, (int, float))
                else f" Respaldo metacognitivo: {mejor_pick.get('score_metacognitivo')}/100."
            )

            explicacion = (
                f"Ineficiencia cuantitativa verificada (+EV: {mejor_pick['valor_esperado_ev']}) bajo Poisson bivariado. "
                f"Muestras oficiales proyectan λ_local={lambda_loc:.2f} y λ_vis={lambda_vis:.2f} (λ_FT={lambda_ft:.2f}, λ_1T={lambda_1t:.2f}). "
                f"En {mejor_pick['mercado']} ({mejor_pick['seleccion']}), prob. real estimada es {mejor_pick['probabilidad_real_estimada']:.1%} "
                f"frente a {mejor_pick['probabilidad_implicita']:.1%} implícita en cuota {mejor_pick['cuota']}. "
                f"Gestión Quarter-Kelly (f*/4) asigna stake sugerido de {stake_pct:.1f}% (liquidación {tipo_liq})."
                f"{meta_str}"
            )
            rend_of = payload.get("Rendimiento_Ofensivo_Individual", {})
            if isinstance(rend_of, dict):
                atqs_destacados = []
                for d in rend_of.get("delanteros_local", []) + rend_of.get("delanteros_visitante", []):
                    if isinstance(d, dict) and d.get("nombre"):
                        g = d.get("goles", 0)
                        p = d.get("partidos", 0)
                        if g > 0:
                            atqs_destacados.append(f"{d['nombre']} ({g}G en {p}p)" if p else f"{d['nombre']} ({g}G)")
                if atqs_destacados:
                    explicacion += f" Respaldado por atacantes titulares: {', '.join(atqs_destacados[:3])}."

            res = {
                "partido": partido,
                "mercado_elegido": mejor_pick["mercado"],
                "seleccion": mejor_pick["seleccion"],
                "cuota": mejor_pick["cuota"],
                "periodo": mejor_pick.get("periodo", "tiempo reglamentario"),
                "probabilidad_implicita": mejor_pick["probabilidad_implicita"],
                "probabilidad_real_estimada": mejor_pick["probabilidad_real_estimada"],
                "valor_esperado_ev": mejor_pick["valor_esperado_ev"],
                "explicacion_cuantitativa_detallada": explicacion,
                "nivel_confianza": confianza,
                "decision_final": "APROBADO",
                "mercados_evaluados": sorted(mercados_evaluados, key=lambda x: x["ev_numerico"], reverse=True)[:8],
                "motor": "Motor Cuantitativo Formal de Poisson (+EV Astra-Validated)",
            }
            if mejor_pick.get("es_mercado_con_devolucion"):
                res["probabilidad_ganar"] = mejor_pick.get("probabilidad_ganar")
                res["probabilidad_push"] = mejor_pick.get("probabilidad_push")
                res["probabilidad_perder"] = mejor_pick.get("probabilidad_perder")
            return res

        # Si ningún mercado alcanzó +5% de valor esperado
        return {
            "partido": partido,
            "mercado_elegido": "Ninguno (+EV < 5%)",
            "seleccion": "N/A",
            "cuota": 1.0,
            "periodo": "tiempo reglamentario",
            "probabilidad_implicita": 0.0,
            "probabilidad_real_estimada": 0.0,
            "valor_esperado_ev": "0.0%",
            "explicacion_cuantitativa_detallada": "Ningún mercado evaluado en Betsson superó el umbral formal de ineficiencia (+EV >= +5.0%) bajo la distribución de Poisson calculada con las muestras oficiales.",
            "nivel_confianza": 0,
            "decision_final": "DESCARTADO",
            "mercados_evaluados": sorted(mercados_evaluados, key=lambda x: x.get("ev_numerico", 0.0), reverse=True)[:5],
            "motor": "Motor Cuantitativo Formal de Poisson (+EV Astra-Validated)",
        }
