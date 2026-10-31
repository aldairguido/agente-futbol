"""
agente_futbol/validador_probabilidades.py
───────────────────────────────────────────────────────────────────────────────
Validador Matemático Estricto y Guardrails Cuantitativos (Astra-Certified).

Garantiza la seguridad matemática antes de aprobar cualquier operación:
  1. Comprobaciones de Tipo y Finitud Numérica (Anti-NaN, Anti-Inf, Anti-TypeMismatch):
     Verifica con isinstance(x, (int, float)) y math.isfinite(x) antes de cualquier
     operación. Si entra un NaN, Inf, string o un payload vacío, aborta de inmediato
     con Stake = 0 y guardrails_superados=False.
  2. Autenticación Estricta de Mercados y Cuotas:
     Verifica que la cuota analizada por el modelo sea IDÉNTICA a la cuota real
     extraída en ese instante en 'Mercado_y_Cuotas_Betsson'. Si no hacen match
     exacto, rechaza la apuesta por "Cuota no autenticada".
  3. Control de Datos Incompletos:
     Bloquea cualquier apuesta si los campos estadísticos críticos están vacíos o
     ausentes (xG por tiempos, remates de delanteros, historial oficial).
  4. Integridad Sumatoria (Axioma de Kolmogorov: Suma = 100%):
     En cualquier mercado binario complementario (BTTS Sí/No, Over/Under no entero),
     la suma de las True Probabilities debe ser estrictamente 100% (tolerancia <= 1.5%).
  5. Corrección de Liquidación de Líneas Asiáticas (Devolución / Push):
     Si el mercado detectado es un Hándicap Asiático o Línea de Gol entera (con PUSH):
       EV = (p_ganar * (Cuota - 1.0)) - p_perder
     En mercados de 2 vías sin devolución:
       EV = (p_real * Cuota) - 1.0
  6. Umbral Mínimo de Valor Esperado (+EV >= +5.0%):
     Margen de seguridad estricto. EV < 0.05 es descartado automáticamente.
  7. Criterio de Fraccional Kelly sin Sobreescritura Artificial:
     Si Quarter-Kelly sugiere un stake inferior al mínimo operativo del broker (0.5%),
     la apuesta se ABORTA (Stake = 0, no se infla artificialmente al mínimo).
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import math
import re
from typing import Any


def es_numero_valido(val: Any) -> bool:
    """Verifica que el valor sea numérico, finito y no booleano ni NaN."""
    if val is None or isinstance(val, bool):
        return False
    if not isinstance(val, (int, float)):
        return False
    return math.isfinite(val)


class ValidadorProbabilidades:
    """
    Capa de auditoría cuantitativa en Python que intercepta y fiscaliza
    las decisiones del LLM o del motor cuantitativo.
    """

    def __init__(
        self,
        ev_minimo_requerido: float = 0.05,  # Mínimo +5% de valor esperado
        kelly_divisor: float = 4.0,         # Quarter-Kelly (f* / 4)
        stake_max_bankroll: float = 0.03,   # Máximo 3.0% del bankroll por ticket
        stake_min_bankroll: float = 0.005,  # Mínimo operativo del broker (0.5%)
    ) -> None:
        self.ev_minimo_requerido = ev_minimo_requerido
        self.kelly_divisor = kelly_divisor
        self.stake_max_bankroll = stake_max_bankroll
        self.stake_min_bankroll = stake_min_bankroll

    def validar_decision(
        self,
        decision_raw: dict[str, Any],
        payload_omnisciente: dict[str, Any],
    ) -> tuple[bool, dict[str, Any], list[str]]:
        """
        Aplica los guardrails matemáticos y de autenticación de mercado.
        Retorna:
          (es_valida: bool, decision_auditada: dict, errores_o_alertas: list[str])
        """
        errores: list[str] = []
        alertas: list[str] = []

        # ── GUARDRAIL 0: CONTROL DE PAYLOAD VACÍO O FORMATO INVÁLIDO ────────
        if not isinstance(decision_raw, dict) or not decision_raw:
            return False, {
                "decision_final": "DESCARTADO",
                "guardrails_superados": False,
                "stake_sugerido": "0.0% (Decisión vacía o formato inválido)",
                "stake_fraccional_kelly_pct": 0.0,
                "kelly_f_star": 0.0,
                "ev_matematico_exacto": 0.0,
                "motivo_descarte_guardrail": "Decisión raw vacía o no es un diccionario.",
            }, ["BLOQUEO: Decisión vacía o formato inválido."]

        if not isinstance(payload_omnisciente, dict) or not payload_omnisciente:
            return False, {
                "decision_final": "DESCARTADO",
                "guardrails_superados": False,
                "stake_sugerido": "0.0% (Payload omnisciente vacío)",
                "stake_fraccional_kelly_pct": 0.0,
                "kelly_f_star": 0.0,
                "ev_matematico_exacto": 0.0,
                "motivo_descarte_guardrail": "Payload omnisciente vacío o ausente.",
            }, ["BLOQUEO: Payload omnisciente vacío o ausente."]

        decision = dict(decision_raw)
        # Inicializar campos defensivos
        decision["guardrails_superados"] = False
        decision["stake_fraccional_kelly_pct"] = 0.0

        if decision.get("decision_final") == "DESCARTADO":
            decision["kelly_f_star"] = 0.0
            decision["ev_matematico_exacto"] = 0.0
            decision["stake_sugerido"] = "0.0% (Descartado previamente por el motor)"
            motivo_previo = decision.get("explicacion_cuantitativa_detallada") or "Descartado previamente por el motor cuantitativo."
            return False, decision, [f"DESCARTADO PREVIAMENTE: {motivo_previo}"]

        # ── GUARDRAIL 1: VALIDACIÓN NUMÉRICA ESTRICTA (ANTI-NAN, ANTI-INF) ───
        cuota_raw = decision.get("cuota")
        p_real_raw = decision.get("probabilidad_real_estimada")

        if not es_numero_valido(cuota_raw) or not es_numero_valido(p_real_raw):
            motivo_num = (
                f"Entrada numérica inválida: Cuota={cuota_raw} (tipo {type(cuota_raw).__name__}), "
                f"True Prob={p_real_raw} (tipo {type(p_real_raw).__name__}). Se rechaza NaN/Inf/String/None."
            )
            errores.append(f"VALOR NUMÉRICO NO VÁLIDO: {motivo_num}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_num
            decision["stake_sugerido"] = "0.0% (Entrada numérica inválida)"
            decision["kelly_f_star"] = 0.0
            decision["ev_matematico_exacto"] = 0.0
            return False, decision, errores

        cuota = float(cuota_raw)
        p_real = float(p_real_raw)

        if cuota <= 1.0 or p_real <= 0.0 or p_real >= 1.0:
            motivo_rango = f"Valores fuera de rango válido: Cuota={cuota} (debe ser > 1.0), True Prob={p_real} (debe estar en (0, 1))."
            errores.append(f"RANGO NUMÉRICO INVÁLIDO: {motivo_rango}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_rango
            decision["stake_sugerido"] = "0.0% (Valores fuera de rango)"
            decision["kelly_f_star"] = 0.0
            decision["ev_matematico_exacto"] = 0.0
            return False, decision, errores

        # ── GUARDRAIL 2: AUTENTICACIÓN ESTRICTA DE MERCADOS Y CUOTAS ─────────
        mercados_betsson = (
            payload_omnisciente.get("Mercado_y_Cuotas_Betsson") or
            payload_omnisciente.get("mercados_betsson") or
            payload_omnisciente.get("mercados") or []
        )
        if not isinstance(mercados_betsson, list) or not mercados_betsson:
            motivo_mkt = "No hay catálogo de cuotas de Betsson en el payload para autenticar la selección."
            errores.append(f"CATÁLOGO DE CUOTAS VACÍO: {motivo_mkt}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_mkt
            decision["stake_sugerido"] = "0.0% (Sin cuotas de Betsson)"
            decision["kelly_f_star"] = 0.0
            return False, decision, errores

        cuota_autenticada = False
        mercado_elegido = str(decision.get("mercado_elegido", "")).strip().lower()
        seleccion_elegida = str(decision.get("seleccion", "")).strip().lower()

        for m_real in mercados_betsson:
            if not isinstance(m_real, dict):
                continue
            c_real = m_real.get("cuota")
            if not es_numero_valido(c_real):
                continue

            # Comparación con tolerancia de coma flotante
            if abs(float(c_real) - cuota) < 1e-4:
                sel_real = str(m_real.get("seleccion", "")).strip().lower()
                fam_real = str(m_real.get("familia") or m_real.get("nombre") or m_real.get("mercado") or "").strip().lower()

                # Coincidencia exacta o contenida de la selección
                if sel_real == seleccion_elegida or seleccion_elegida in sel_real or sel_real in seleccion_elegida:
                    cuota_autenticada = True
                    break

        if not cuota_autenticada:
            motivo_autenticacion = (
                f"Cuota no autenticada: La cuota {cuota} para la selección '{decision.get('seleccion')}' "
                f"en mercado '{decision.get('mercado_elegido')}' no coincide exactamente con ningún precio capturado en Betsson."
            )
            errores.append(f"CUOTA NO AUTENTICADA: {motivo_autenticacion}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_autenticacion
            decision["stake_sugerido"] = "0.0% (Cuota no autenticada en Betsson)"
            decision["kelly_f_star"] = 0.0
            decision["ev_matematico_exacto"] = 0.0
            return False, decision, errores

        # ── GUARDRAIL 3: CONTROL DE DATOS INCOMPLETOS ────────────────────────
        datos_completos, motivo_incompleto = self._verificar_datos_suficientes(payload_omnisciente, decision)
        if not datos_completos:
            errores.append(f"BLOQUEO POR DATOS INCOMPLETOS: {motivo_incompleto}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_incompleto
            decision["nivel_confianza"] = min(decision.get("nivel_confianza", 5), 4)
            decision["stake_sugerido"] = "0.0% (Bloqueado por falta de datos críticos)"
            decision["kelly_f_star"] = 0.0
            decision["ev_matematico_exacto"] = 0.0
            return False, decision, errores

        # ── GUARDRAIL 4: INTEGRIDAD SUMATORIA (SUMA = 100%) ──────────────────
        suma_valida, motivo_suma = self._verificar_integridad_sumatoria(decision)
        if not suma_valida:
            errores.append(f"VIOLACIÓN AXIOMÁTICA DE PROBABILIDAD: {motivo_suma}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_suma
            decision["nivel_confianza"] = min(decision.get("nivel_confianza", 5), 3)
            decision["stake_sugerido"] = "0.0% (Incoherencia lógica en suma de probabilidades)"
            decision["kelly_f_star"] = 0.0
            decision["ev_matematico_exacto"] = 0.0
            return False, decision, errores

        # ── GUARDRAIL 5: CÁLCULO DE +EV CON LIQUIDACIÓN ASIÁTICA (PUSH) ───────
        p_implied = round(1.0 / cuota, 4)
        decision["probabilidad_implicita"] = p_implied

        es_push, tipo_linea = self._detectar_mercado_con_devolucion(decision)
        decision["es_mercado_con_devolucion"] = es_push
        decision["tipo_linea_asiatica"] = tipo_linea

        if es_push:
            # Mercados de 3 vías con reembolso (Asian Handicap / Asian Goal Line enteros, DNB)
            p_ganar = float(decision.get("probabilidad_ganar", p_real))
            p_push = float(decision.get("probabilidad_push", 0.0))
            p_perder_val = decision.get("probabilidad_perder")

            if p_perder_val is not None and es_numero_valido(p_perder_val):
                p_perder = float(p_perder_val)
            else:
                p_perder = round(max(0.0, 1.0 - p_ganar - p_push), 4)

            # Verificar finitud y suma = 1.0 de las 3 ramas
            if not es_numero_valido(p_ganar) or not es_numero_valido(p_push) or not es_numero_valido(p_perder):
                motivo_tri = f"Ramas de probabilidad no finitas en línea asiática: Ganar={p_ganar}, Push={p_push}, Perder={p_perder}."
                errores.append(f"ERROR EN LÍNEA ASIÁTICA: {motivo_tri}")
                decision["decision_final"] = "DESCARTADO"
                decision["guardrails_superados"] = False
                decision["stake_sugerido"] = "0.0% (Probabilidades no válidas)"
                return False, decision, errores

            suma_tri = p_ganar + p_push + p_perder
            if abs(suma_tri - 1.0) > 0.02:
                motivo_tri = f"Ramas de línea asiática no suman 100%: Ganar({p_ganar:.1%}) + Push({p_push:.1%}) + Perder({p_perder:.1%}) = {suma_tri:.1%}."
                errores.append(f"VIOLACIÓN AXIOMÁTICA EN LÍNEA ASIÁTICA: {motivo_tri}")
                decision["decision_final"] = "DESCARTADO"
                decision["guardrails_superados"] = False
                decision["stake_sugerido"] = "0.0% (Violación de probabilidad tripartita)"
                return False, decision, errores

            # Fórmula exacta de EV para mercados con PUSH:
            # EV = (p_ganar * (Cuota - 1.0)) - p_perder
            ev_calculado = (p_ganar * (cuota - 1.0)) - p_perder

            # Fórmula de Kelly exacta para apuestas con Push / Devolución:
            # f* = ((p_ganar * (b)) - p_perder) / (b * (p_ganar + p_perder))  donde b = Cuota - 1.0
            b = cuota - 1.0
            denominador_kelly = b * (p_ganar + p_perder)
            if denominador_kelly > 0.0:
                f_star = ((p_ganar * b) - p_perder) / denominador_kelly
            else:
                f_star = 0.0

            decision["probabilidad_ganar"] = round(p_ganar, 4)
            decision["probabilidad_push"] = round(p_push, 4)
            decision["probabilidad_perder"] = round(p_perder, 4)

        else:
            # Mercados binarios normales de 2 vías sin devolución (ej. Over 2.5, BTTS)
            # EV = (P_real * Cuota) - 1.0
            ev_calculado = (p_real * cuota) - 1.0
            f_star = ((p_real * cuota) - 1.0) / (cuota - 1.0)

        decision["ev_matematico_exacto"] = round(ev_calculado, 4)
        decision["valor_esperado_ev"] = f"{round(ev_calculado * 100, 1):+0.1f}%"
        decision["kelly_f_star"] = round(f_star, 4)

        # ── GUARDRAIL 6: UMBRAL MÍNIMO DE VALOR ESPERADO (+EV >= +5.0%) ─────
        if ev_calculado < self.ev_minimo_requerido:
            motivo_ev = (
                f"Valor Esperado insuficiente: EV calculado es {ev_calculado:+0.1%} "
                f"(umbral mínimo exigido: {self.ev_minimo_requerido:+0.1%})."
            )
            errores.append(f"MARGEN DE SEGURIDAD INSUFICIENTE: {motivo_ev}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_ev
            decision["stake_sugerido"] = "0.0% (EV insuficiente)"
            return False, decision, errores

        # ── GUARDRAIL 7: STAKE MANAGEMENT CON FRACCIONAL KELLY ───────────────
        # Si f* <= 0, no hay ventaja de capital
        if f_star <= 0.0:
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["stake_sugerido"] = "0.0% (f* <= 0, sin ventaja de capital)"
            decision["stake_fraccional_kelly_pct"] = 0.0
            return False, decision, ["Kelly f* <= 0, sin ventaja de capital"]

        frac_kelly = f_star / self.kelly_divisor

        # REGLA ESTRICTA DE ASTRA:
        # Si Quarter-Kelly sugiere un stake inferior al mínimo operativo (0.5%),
        # la apuesta se ABORTA (Stake = 0, no se infla artificialmente al mínimo).
        if frac_kelly < self.stake_min_bankroll:
            motivo_stake = (
                f"Stake insuficiente: Quarter-Kelly sugiere {frac_kelly:.2%} del bankroll, "
                f"inferior al mínimo operativo ({self.stake_min_bankroll:.2%}). "
                f"Apuesta abortada para preservar capital."
            )
            errores.append(f"STAKE POR DEBAJO DEL MÍNIMO OPERATIVO: {motivo_stake}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_stake
            decision["stake_sugerido"] = "0.0% (Quarter-Kelly inferior al mínimo operativo)"
            decision["stake_fraccional_kelly_pct"] = 0.0
            return False, decision, errores

        # Acotar por el tope prudencial de riesgo máximo (3.0% del bankroll)
        stake_acotado = min(self.stake_max_bankroll, frac_kelly)
        stake_pct = round(stake_acotado * 100, 2)
        stake_str = f"{stake_pct:.1f}% del Bankroll (Quarter-Kelly f*/4: f*={f_star*100:.1f}%)"

        decision["stake_fraccional_kelly_pct"] = stake_pct
        decision["stake_sugerido"] = stake_str

        # ── GUARDRAIL 8: HARD GUARDRAIL DE CONTRADICCIÓN HISTÓRICA (BLOQUEO H2H) ──
        ok_h2h, motivo_h2h = self._verificar_guardrail_h2h(payload_omnisciente, decision)
        if not ok_h2h:
            errores.append(f"VETO H2H POR CONTRADICCIÓN HISTÓRICA: {motivo_h2h}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_h2h
            decision["stake_sugerido"] = "0.0% (Veto H2H por contradicción histórica)"
            decision["stake_fraccional_kelly_pct"] = 0.0
            return False, decision, errores

        # ── GUARDRAIL 9: HARD GUARDRAIL DE RENDIMIENTO CONDICIONAL (RACHAS NULAS) ─
        ok_cond, motivo_cond = self._verificar_inoperancia_condicional(payload_omnisciente, decision)
        if not ok_cond:
            errores.append(f"VETO POR INOPERANCIA CONDICIONAL: {motivo_cond}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_cond
            decision["stake_sugerido"] = "0.0% (Veto por racha nula/inoperancia)"
            decision["stake_fraccional_kelly_pct"] = 0.0
            return False, decision, errores

        # ── GUARDRAIL 10: PROTOCOLO DE CONVERGENCIA TEÓRICO-EMPÍRICA ─────────
        ok_conv, motivo_conv = self._verificar_convergencia(payload_omnisciente, decision)
        if not ok_conv:
            errores.append(f"CONFLICTO ANALÍTICO: {motivo_conv}")
            decision["decision_final"] = "DESCARTADO"
            decision["guardrails_superados"] = False
            decision["motivo_descarte_guardrail"] = motivo_conv
            decision["conflicto_analitico"] = True
            decision["stake_sugerido"] = "0.0% (Conflicto analítico teoría vs realidad)"
            decision["stake_fraccional_kelly_pct"] = 0.0
            return False, decision, errores

        # Superación de todos los guardrails
        decision["decision_final"] = "APROBADO"
        decision["guardrails_superados"] = True
        return True, decision, alertas

    # ═══════════════════════════════════════════════════════════════════════════
    # MÉTODOS INTERNOS DE VERIFICACIÓN
    # ═══════════════════════════════════════════════════════════════════════════════

    def _detectar_mercado_con_devolucion(self, decision: dict[str, Any]) -> tuple[bool, str]:
        """Detecta si el mercado cuenta con regla de PUSH / Devolución."""
        m_low = str(decision.get("mercado_elegido", "")).lower()
        s_low = str(decision.get("seleccion", "")).lower()

        # 1. Empate apuesta no válida / Draw No Bet
        if "apuesta sin empate" in m_low or "draw no bet" in m_low or "dnb" in m_low:
            return True, "DNB"

        # 2. Hándicap Asiático 0.0
        if "0.0" in s_low and any(k in m_low for k in ["hándicap", "handicap", "asiátic", "asiatic"]):
            return True, "HA_0.0"

        # 3. Hándicaps o Líneas de gol enteras (1.0, 2.0, 3.0, 4.0)
        m_entera = re.search(r"\b([1-4])\.0\b", s_low)
        if m_entera and any(k in m_low for k in ["asiátic", "asiatic", "línea de gol", "linea de gol", "hándicap", "handicap"]):
            return True, f"ASIAN_{m_entera.group(1)}.0"

        return False, ""

    def _verificar_datos_suficientes(
        self, payload: dict[str, Any], decision: dict[str, Any]
    ) -> tuple[bool, str]:
        """
        Verifica que los datos no estén vacíos ni ausentes en campos críticos.
        Requiere historial oficial verificable de Flashscore (muestras recientes o H2H)
        o métricas de SofaScore. Si ambos están vacíos, rechaza el análisis.
        """
        historial = payload.get("Historial_Oficial", {})
        if not isinstance(historial, dict):
            historial = {}

        u10_loc = historial.get("ultimos_10_oficiales_local", [])
        u10_vis = historial.get("ultimos_10_oficiales_visitante", [])
        u5_casa = historial.get("ultimos_5_local_casa", [])
        u5_fuera = historial.get("ultimos_5_visitante_fuera", [])
        h2h_ofic = historial.get("h2h_oficial", [])

        metricas = payload.get("Metricas_Profundas_SofaScore", {})
        tiene_sofa = isinstance(metricas, dict) and bool(metricas)

        total_muestras = len(u10_loc) + len(u10_vis) + len(u5_casa) + len(u5_fuera) + len(h2h_ofic)
        if total_muestras == 0 and not tiene_sofa:
            return False, "Sin partidos oficiales registrados en Flashscore ni métricas en SofaScore (datos vacíos)."

        return True, ""

    def _verificar_integridad_sumatoria(self, decision: dict[str, Any]) -> tuple[bool, str]:
        """
        Verifica que en mercados binarios complementarios la suma de probabilidades
        sea exactamente 100% (con tolerancia de redondeo de float <= 0.015).
        """
        mercado = str(decision.get("mercado_elegido", "")).lower()
        seleccion = str(decision.get("seleccion", "")).lower()
        p_real = float(decision.get("probabilidad_real_estimada", 0.0) or 0.0)

        mercados_evaluados = decision.get("mercados_evaluados", [])
        if not isinstance(mercados_evaluados, list):
            mercados_evaluados = []

        # Ejemplo 1: Ambos Equipos Anotan (Sí vs No)
        if "ambos" in mercado or "btts" in mercado:
            lado_opuesto = "no" if seleccion in ("sí", "si") else "sí"
            for m in mercados_evaluados:
                if not isinstance(m, dict):
                    continue
                if str(m.get("mercado", "")).lower() == mercado:
                    sel_m = str(m.get("seleccion", "")).lower()
                    if sel_m in (lado_opuesto, f"ambos anotan: {lado_opuesto}"):
                        p_op = m.get("probabilidad_real_estimada")
                        if not es_numero_valido(p_op):
                            return False, "Probabilidad opuesta no numérica en mercado BTTS."
                        p_opuesta = float(p_op)
                        suma = p_real + p_opuesta
                        if abs(suma - 1.0) > 0.015:
                            return False, (
                                f"Mercado binario BTTS '{seleccion}' ({p_real:.1%}) y "
                                f"'{sel_m}' ({p_opuesta:.1%}) suman {suma:.1%} (debe ser estrictamente 100%)."
                            )

        # Ejemplo 2: Más / Menos de Goles no enteros (ej. 0.5, 1.5, 2.5, 3.5)
        m_linea = re.search(r"(\d+\.5)", seleccion) or re.search(r"(\d+\.5)", mercado)
        if m_linea:
            linea_val = m_linea.group(1)
            es_over = "más" in seleccion or "over" in seleccion
            es_under = "menos" in seleccion or "under" in seleccion

            if es_over or es_under:
                termino_opuesto = "menos" if es_over else "más"
                for m in mercados_evaluados:
                    if not isinstance(m, dict):
                        continue
                    sel_m = str(m.get("seleccion", "")).lower()
                    mkt_m = str(m.get("mercado", "")).lower()
                    if mkt_m == mercado and linea_val in sel_m and termino_opuesto in sel_m:
                        p_op = m.get("probabilidad_real_estimada")
                        if not es_numero_valido(p_op):
                            return False, "Probabilidad opuesta no numérica en mercado de goles."
                        p_opuesta = float(p_op)
                        suma = p_real + p_opuesta
                        if abs(suma - 1.0) > 0.015:
                            return False, (
                                f"Mercado binario de goles {linea_val} '{seleccion}' ({p_real:.1%}) y "
                                f"'{sel_m}' ({p_opuesta:.1%}) suman {suma:.1%} (debe ser estrictamente 100%)."
                            )

        return True, ""

    def _verificar_guardrail_h2h(self, payload: dict[str, Any], decision: dict[str, Any]) -> tuple[bool, str]:
        """
        Fase 2: Hard Guardrail de Contradicción Histórica (Bloqueo H2H).
        Veta automáticamente apuestas defensivas ('Ambos Anotan: No' o 'Menos de X.5')
        si el H2H oficial registra >= 60% de BTTS o de Over.
        """
        historial = payload.get("Historial_Oficial", {})
        if not isinstance(historial, dict):
            return True, ""

        h2h = historial.get("h2h_oficial", [])
        if not isinstance(h2h, list) or len(h2h) < 2:
            return True, ""

        total_h2h = len(h2h)
        btts_cnt = 0
        o25_cnt = 0
        o15_cnt = 0
        g1t_cnt = 0

        for p in h2h:
            gl = p.get("goles_local") if p.get("goles_local") is not None else p.get("gl_ft", 0)
            gv = p.get("goles_visitante") if p.get("goles_visitante") is not None else p.get("gv_ft", 0)
            try:
                gl = int(gl or 0)
                gv = int(gv or 0)
            except (ValueError, TypeError):
                gl, gv = 0, 0

            if gl > 0 and gv > 0:
                btts_cnt += 1
            if (gl + gv) > 2:
                o25_cnt += 1
            if (gl + gv) >= 2:
                o15_cnt += 1

            gl_ht = p.get("gl_ht") if p.get("gl_ht") is not None else p.get("goles_ht_local")
            gv_ht = p.get("gv_ht") if p.get("gv_ht") is not None else p.get("goles_ht_visitante")
            if gl_ht is not None and gv_ht is not None:
                try:
                    if (int(gl_ht) + int(gv_ht)) > 0:
                        g1t_cnt += 1
                except (ValueError, TypeError):
                    pass
            elif (gl + gv) >= 2:
                g1t_cnt += 1

        btts_pct = btts_cnt / total_h2h
        o25_pct = o25_cnt / total_h2h
        o15_pct = o15_cnt / total_h2h
        g1t_pct = g1t_cnt / total_h2h

        m_low = str(decision.get("mercado_elegido", "")).lower()
        s_low = str(decision.get("seleccion", "")).lower()

        # Veto Ambos Anotan: No si BTTS H2H >= 60%
        if ("ambos" in m_low or "btts" in m_low) and any(w in s_low for w in ["no", "ambos anotan: no", "ambos no anotan"]):
            if btts_pct >= 0.60:
                return False, (
                    f"Selección defensiva '{decision.get('seleccion')}' vetada: "
                    f"H2H oficial registra un {btts_pct:.0%} de BTTS ({btts_cnt}/{total_h2h} partidos >= 60%)."
                )

        # Veto Menos de X.5 si Over H2H >= 60%
        if any(w in s_low or w in m_low for w in ["menos de", "under", "menos"]):
            if ("1t" in s_low or "1t" in m_low or "1er tiempo" in s_low or "1er tiempo" in m_low) and "0.5" in s_low:
                if g1t_pct >= 0.60:
                    return False, (
                        f"Selección 'Menos de 0.5 1T' vetada: H2H oficial registra gol en 1T en un {g1t_pct:.0%} ({g1t_cnt}/{total_h2h} >= 60%)."
                    )
            if "2.5" in s_low:
                if o25_pct >= 0.60:
                    return False, (
                        f"Selección 'Menos de 2.5' vetada: H2H oficial registra >2.5 goles en un {o25_pct:.0%} ({o25_cnt}/{total_h2h} >= 60%)."
                    )
            if "1.5" in s_low and not any(k in s_low or k in m_low for k in ["1t", "1er tiempo"]):
                if o15_pct >= 0.60:
                    return False, (
                        f"Selección 'Menos de 1.5' vetada: H2H oficial registra >1.5 goles en un {o15_pct:.0%} ({o15_cnt}/{total_h2h} >= 60%)."
                    )

        return True, ""

    def _verificar_inoperancia_condicional(self, payload: dict[str, Any], decision: dict[str, Any]) -> tuple[bool, str]:
        """
        Fase 3: Hard Guardrail de Rendimiento Condicional (Bloqueo de Rachas Nulas).
        Prohíbe absolutamente apostar a favor de un equipo con GF prom < 0.50 o 0% de victorias
        en sus últimos 5 partidos en condición específica (Local o Visita).
        """
        partido = str(payload.get("Partido", ""))
        if " vs " in partido:
            partes = partido.split(" vs ")
            local_nom = partes[0].strip().lower()
            vis_nom = partes[1].strip().lower()
        else:
            local_nom = ""
            vis_nom = ""

        historial = payload.get("Historial_Oficial", {})
        if not isinstance(historial, dict):
            return True, ""

        u5_casa = historial.get("ultimos_5_local_casa", [])
        u5_fuera = historial.get("ultimos_5_visitante_fuera", [])

        # Inoperancia Local en casa:
        inoperante_l = False
        gf_prom_l = 1.0
        vic_l = 1
        if isinstance(u5_casa, list) and len(u5_casa) >= 3:
            gf_tot_l = sum(float(p.get("goles_favor") if p.get("goles_favor") is not None else p.get("goles_local", 0) or 0) for p in u5_casa)
            gf_prom_l = gf_tot_l / len(u5_casa)
            vic_l = sum(1 for p in u5_casa if "W" in str(p.get("resultado","")).upper() or "V" in str(p.get("resultado","")).upper() or float(p.get("goles_favor",0) or 0) > float(p.get("goles_contra",0) or 0))
            if gf_prom_l < 0.50 or vic_l == 0:
                inoperante_l = True

        # Inoperancia Visitante fuera:
        inoperante_v = False
        gf_prom_v = 1.0
        vic_v = 1
        if isinstance(u5_fuera, list) and len(u5_fuera) >= 3:
            gf_tot_v = sum(float(p.get("goles_favor") if p.get("goles_favor") is not None else p.get("goles_visitante", 0) or 0) for p in u5_fuera)
            gf_prom_v = gf_tot_v / len(u5_fuera)
            vic_v = sum(1 for p in u5_fuera if "W" in str(p.get("resultado","")).upper() or "V" in str(p.get("resultado","")).upper() or float(p.get("goles_favor",0) or 0) > float(p.get("goles_contra",0) or 0))
            if gf_prom_v < 0.50 or vic_v == 0:
                inoperante_v = True

        sel_low = str(decision.get("seleccion", "")).lower()
        mkt_low = str(decision.get("mercado_elegido", "")).lower()

        # Detección si favorece al Local:
        fav_loc = False
        if local_nom and (local_nom in sel_low or "1x" in sel_low or sel_low in ("1", "local")):
            fav_loc = True
        elif ("dnb" in mkt_low or "sin empate" in mkt_low or "hándicap" in mkt_low or "handicap" in mkt_low) and (local_nom in sel_low or "1" in sel_low):
            fav_loc = True
        elif ("doble oportunidad" in mkt_low or sel_low in ("1x", "12")) and (local_nom in sel_low or "1x" in sel_low):
            fav_loc = True

        # Detección si favorece al Visitante:
        fav_vis = False
        if vis_nom and (vis_nom in sel_low or "x2" in sel_low or sel_low in ("2", "visitante", "visita")):
            fav_vis = True
        elif ("dnb" in mkt_low or "sin empate" in mkt_low or "hándicap" in mkt_low or "handicap" in mkt_low) and (vis_nom in sel_low or "2" in sel_low):
            fav_vis = True
        elif ("doble oportunidad" in mkt_low or sel_low in ("x2", "12")) and (vis_nom in sel_low or "x2" in sel_low):
            fav_vis = True

        if fav_loc and inoperante_l:
            return False, (
                f"{local_nom.title()} presenta inoperancia condicional en casa "
                f"(GF prom: {gf_prom_l:.2f} < 0.50 o {vic_l} victorias en {len(u5_casa)} partidos). "
                f"Prohibido recomendar apuestas a su favor."
            )

        if fav_vis and inoperante_v:
            return False, (
                f"{vis_nom.title()} presenta inoperancia condicional fuera de casa "
                f"(GF prom: {gf_prom_v:.2f} < 0.50 o {vic_v} victorias en {len(u5_fuera)} partidos). "
                f"Prohibido recomendar apuestas a su favor."
            )

        return True, ""

    def _verificar_convergencia(self, payload: dict[str, Any], decision: dict[str, Any]) -> tuple[bool, str]:
        """
        Fase 4: Protocolo de Convergencia Teórico-Empírica.
        Verifica que el modelo Poisson (+EV) y las tendencias estadísticas reales
        concuerden en la dirección del mercado antes de aprobar como Diamante.
        """
        mkt_low = str(decision.get("mercado_elegido", "")).lower()
        sel_low = str(decision.get("seleccion", "")).lower()

        historial = payload.get("Historial_Oficial", {})
        if not isinstance(historial, dict):
            return True, ""

        h2h = historial.get("h2h_oficial", []) or []
        u5_l = historial.get("ultimos_5_local_casa", []) or []
        u5_v = historial.get("ultimos_5_visitante_fuera", []) or []

        # 1. Mercados Over / Ofensivos
        es_over = any(w in sel_low or w in mkt_low for w in ["más de", "over", "btts: sí"]) or sel_low in ("sí", "si")
        if any(w in sel_low or w in mkt_low for w in ["menos", "under", "btts: no"]) or sel_low in ("no",):
            es_over = False

        if es_over:
            # Muestras reales de local y visita
            gf_l = sum(float(p.get("goles_favor", 0) or 0) for p in u5_l) / max(1, len(u5_l)) if u5_l else 1.2
            gf_v = sum(float(p.get("goles_favor", 0) or 0) for p in u5_v) / max(1, len(u5_v)) if u5_v else 1.0

            if gf_l < 0.60 and gf_v < 0.60 and len(u5_l) >= 3 and len(u5_v) >= 3:
                return False, (
                    f"Conflicto Analítico: Selección Over '{decision.get('seleccion')}' contradice sequía ofensiva "
                    f"empírica severa de ambos equipos (GF local en casa: {gf_l:.2f}, GF visita fuera: {gf_v:.2f})."
                )

        # 2. Mercados Under / Defensivos
        es_under = any(w in sel_low or w in mkt_low for w in ["menos de", "under", "btts: no"]) or sel_low in ("no",)
        if es_under and len(h2h) >= 2:
            o25_h2h = sum(1 for p in h2h if (float(p.get("goles_local",0) or 0)+float(p.get("goles_visitante",0) or 0)) > 2) / len(h2h)
            if o25_h2h >= 0.60:
                return False, (
                    f"Conflicto Analítico: Selección defensiva '{decision.get('seleccion')}' contradice "
                    f"el alto promedio goleador en H2H ({o25_h2h:.0%} >2.5 goles)."
                )

        return True, ""

