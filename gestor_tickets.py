"""
agente_futbol/gestor_tickets.py
───────────────────────────────────────────────────────────────────────────────
Gestor de Bankroll y Constructor de Tickets Inteligentes.

Responsabilidades:
  1. Filtro Diamante (Apuestas Simples):
     - Selecciones con respaldo H2H histórico > 85% y cuota estrictamente > 1.55.
     - Filtro estricto de partidos oficiales reales (exclusión de eFootball / simulados).
     - Prevención de riesgo: máximo 1 apuesta simple por partido.
  2. Armador de Parlays (Combinadas Dobles):
     - Selecciones verdes de alta seguridad (cuotas entre 1.30 y 1.45).
     - Agrupación óptima de 2 en 2 para cuota combinada global cercana a 2.00.
     - Prevención de riesgo: sin partidos repetidos entre patas ni entre tickets.
  3. Gestión de Capital / Staking:
     - Unidades sugeridas de stake para preservar el bankroll.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import re
from typing import Any


def _es_partido_simulado(partido: str, competicion: str) -> bool:
    """Detecta si un partido corresponde a eFootball o simulaciones virtuales."""
    p_lower = (partido or "").lower()
    c_lower = (competicion or "").lower()
    
    palabras_virtual = [
        "efootball", "8 minutos", "gt league", "esports", "cyber", "smetana",
        "crinner", "saportj", "dava", "meltoik", "dm1trena", "jekunam", "upcake"
    ]
    return any(w in p_lower or w in c_lower for w in palabras_virtual)


def _extraer_ratio_h2h(candidato: dict) -> float:
    """
    Calcula el porcentaje histórico H2H a partir de las fuentes disponibles:
    1. Campo 'h2h_ratio' numérico directo (si existe).
    2. Campo 'frecuencia_h2h' (ej: '5/5', '4/5', '1/5').
    3. Texto en 'evidencia_favorable' (ej: 'H2H en 5/5 partidos (100%)').
    """
    if "h2h_ratio" in candidato and candidato["h2h_ratio"] is not None:
        try:
            return float(candidato["h2h_ratio"])
        except (ValueError, TypeError):
            pass

    freq_h2h = str(candidato.get("frecuencia_h2h", "")).strip()
    if "/" in freq_h2h:
        partes = freq_h2h.split("/")
        try:
            num = float(partes[0].strip())
            den = float(partes[1].strip())
            if den > 0:
                return round(num / den, 2)
        except (ValueError, IndexError):
            pass

    # Búsqueda por regex en evidencia_favorable
    for ev in candidato.get("evidencia_favorable", []):
        m = re.search(r"H2H.*?\((\d+)%\)", str(ev), re.I)
        if m:
            try:
                return round(float(m.group(1)) / 100.0, 2)
            except ValueError:
                pass
        m_conteo = re.search(r"H2H.*?(\d+)\s*/\s*(\d+)", str(ev), re.I)
        if m_conteo:
            try:
                num = float(m_conteo.group(1))
                den = float(m_conteo.group(2))
                if den > 0:
                    return round(num / den, 2)
            except ValueError:
                pass

    return 0.0


def calcular_fraccional_kelly(
    true_probability: float,
    cuota: float,
    divisor: float = 4.0,
    stake_min: float = 0.005,
    stake_max: float = 0.03,
) -> tuple[float, float, str]:
    """
    Calcula el stake óptimo mediante la fórmula de Kelly:
      f* = ((True_Probability * Cuota) - 1) / (Cuota - 1)
      stake_fraccional = f* / divisor
    Retorna: (f_star, stake_fraccional_acotado, stake_string)
    """
    if cuota <= 1.0 or true_probability <= 0.0 or true_probability >= 1.0:
        return 0.0, 0.0, "0.0% (Valores matemáticos no válidos)"

    f_star = ((true_probability * cuota) - 1.0) / (cuota - 1.0)
    if f_star <= 0.0:
        return round(f_star, 4), 0.0, "0.0% (Sin ventaja matemática f* <= 0)"

    frac_kelly = f_star / divisor
    if frac_kelly < stake_min:
        divisor_nombre = "Quarter-Kelly f*/4" if divisor == 4.0 else ("Eighth-Kelly f*/8" if divisor == 8.0 else f"Kelly f*/{int(divisor)}")
        return round(f_star, 4), 0.0, f"0.0% ({divisor_nombre} {frac_kelly:.2%} inferior al mínimo operativo {stake_min:.2%})"

    stake_acotado = min(stake_max, frac_kelly)
    stake_pct = round(stake_acotado * 100, 2)

    divisor_nombre = "Quarter-Kelly f*/4" if divisor == 4.0 else ("Eighth-Kelly f*/8" if divisor == 8.0 else f"Kelly f*/{int(divisor)}")
    stake_str = f"{stake_pct:.1f}% del Bankroll ({divisor_nombre}: f*={f_star*100:.1f}%)"
    return round(f_star, 4), round(stake_acotado, 4), stake_str


def _obtener_probabilidad_estimada(p: dict) -> float:
    """Extrae o estima la True Probability respaldada por las fuentes analíticas."""
    if p.get("cuantitativo_ia"):
        prob = p["cuantitativo_ia"].get("probabilidad_real_estimada")
        if prob and float(prob) > 0:
            return float(prob)
    if p.get("h2h_ratio") and float(p["h2h_ratio"]) > 0:
        return float(p["h2h_ratio"])
    cuota = float(p.get("cuota", 0.0) or 0.0)
    if cuota > 1.0:
        return min(0.92, round((1.0 / cuota) + 0.04, 4))
    return 0.50


class GestorTickets:
    def __init__(
        self,
        cuota_min_diamante: float = 1.55,
        h2h_min_diamante: float = 0.85,
        parlay_cuota_min: float = 1.30,
        parlay_cuota_max: float = 1.45,
    ) -> None:
        self.cuota_min_diamante = cuota_min_diamante
        self.h2h_min_diamante   = h2h_min_diamante
        self.parlay_cuota_min   = parlay_cuota_min
        self.parlay_cuota_max   = parlay_cuota_max

    def procesar_resultados(self, resultados: list[dict]) -> dict[str, Any]:
        """
        Analiza todos los partidos con veredicto Verde y genera:
          - Apuestas Simples Diamante
          - Parlays / Combinadas Dobles (x2)
          - Métricas de riesgo y staking
        """
        picks_verdes_planos: list[dict] = []

        for r in resultados:
            partido = r.get("partido", "?")
            comp    = r.get("competicion", "?")
            
            # Solo fútbol real profesional para tickets de capital
            if _es_partido_simulado(partido, comp):
                continue

            candidatos_verdes = [
                c for c in r.get("candidatos", [])
                if str(c.get("semaforo", "")).upper() == "VERDE"
            ]

            for c in candidatos_verdes:
                cuota = float(c.get("cuota", 0.0) or 0.0)
                h2h_ratio = _extraer_ratio_h2h(c)
                conflicto = bool(
                    c.get("conflicto_analitico")
                    or c.get("cuantitativo_ia", {}).get("conflicto_analitico")
                    or r.get("conflicto_analitico")
                    or r.get("decision_cuantitativa", {}).get("conflicto_analitico")
                )
                picks_verdes_planos.append({
                    "partido":           partido,
                    "competicion":       comp,
                    "mercado":           c.get("mercado", "?"),
                    "seleccion":         c.get("seleccion", "?"),
                    "cuota":             cuota,
                    "periodo":           c.get("periodo", "tiempo reglamentario"),
                    "regla_liquidacion": c.get("regla_liquidacion", ""),
                    "frecuencia_h2h":    c.get("frecuencia_h2h", "N/D"),
                    "h2h_ratio":         h2h_ratio,
                    "motivo_semaforo":   c.get("motivo_semaforo", ""),
                    "evidencia_favorable": c.get("evidencia_favorable", []),
                    "es_diamante_ia":    bool((c.get("tipo") == "DIAMANTE_IA" or c.get("cuantitativo_ia")) and not conflicto),
                    "cuantitativo_ia":   c.get("cuantitativo_ia", {}),
                    "conflicto_analitico": conflicto,
                })

        # ── 1. FILTRO DIAMANTE (Apuestas Simples de Alto Valor / Quant Trader) ─
        candidatos_diamante = [
            p for p in picks_verdes_planos
            if not p.get("conflicto_analitico") and (
                p.get("es_diamante_ia") or (p["cuota"] > self.cuota_min_diamante and p["h2h_ratio"] > self.h2h_min_diamante)
            )
        ]

        # Prevención de riesgo: máximo 1 pick diamante por partido
        partidos_en_diamante: set[str] = set()
        simples_diamante: list[dict] = []

        # Priorizar picks Diamante IA validados por el Trader Cuantitativo Senior (+EV)
        candidatos_diamante.sort(key=lambda x: (1 if x.get("es_diamante_ia") else 0, x["h2h_ratio"], x["cuota"]), reverse=True)

        for p in candidatos_diamante:
            if p["partido"] not in partidos_en_diamante:
                partidos_en_diamante.add(p["partido"])
                q_info = p.get("cuantitativo_ia", {})
                tipo_ticket = "Apuesta Simple Diamante (+EV Quant Trader)" if p.get("es_diamante_ia") else "Apuesta Simple Diamante"
                motivo = q_info.get("explicacion_cuantitativa_detallada") or p["motivo_semaforo"] or (
                    f"Historial H2H oficial demoledor ({p['h2h_ratio']:.0%}) "
                    f"con cuota atractiva de valor @ {p['cuota']}."
                )

                # Cálculo de True Probability y Fraccional Kelly (f* / 4)
                p_real = float(q_info.get("probabilidad_real_estimada") or p["h2h_ratio"] or 0.0)
                if p_real <= 0.0 and p["cuota"] > 1.0:
                    p_real = round((1.0 / p["cuota"]) + 0.05, 4)

                ev_num = (p_real * p["cuota"]) - 1.0 if p_real > 0 else 0.0
                ev_display = q_info.get("valor_esperado_ev") or f"{round(ev_num * 100, 1):+0.1f}%"
                h2h_display = f"{p_real:.0%}"

                # Stake por Quarter-Kelly f*/4
                if q_info.get("stake_sugerido") and "Quarter-Kelly" in str(q_info.get("stake_sugerido")):
                    stake_sugerido = q_info["stake_sugerido"]
                else:
                    _, _, stake_sugerido = calcular_fraccional_kelly(p_real, p["cuota"], divisor=4.0)

                simples_diamante.append({
                    "id_ticket":                  f"DIAMANTE-{len(simples_diamante)+1}",
                    "tipo":                       tipo_ticket,
                    "partido":                    p["partido"],
                    "competicion":                p["competicion"],
                    "mercado":                    p["mercado"],
                    "seleccion":                  p["seleccion"],
                    "cuota":                      p["cuota"],
                    "h2h_ratio_pct":              h2h_display,
                    "probabilidad_real_estimada": q_info.get("probabilidad_real_estimada") or p_real,
                    "probabilidad_implicita":     q_info.get("probabilidad_implicita"),
                    "frecuencia_h2h":             p.get("frecuencia_h2h", "N/D"),
                    "regla_liquidacion":          p["regla_liquidacion"],
                    "stake_sugerido":             stake_sugerido,
                    "motivo":                     motivo,
                    "valor_esperado_ev":          ev_display,
                    "confianza_ia":               q_info.get("nivel_confianza", 9),
                    "trampa_detectada":           q_info.get("trampa_detectada"),
                    "estado_consciente":          q_info.get("estado_consciente"),
                    "nivel_madurez":              q_info.get("nivel_madurez"),
                    "cuantitativo_ia":            q_info,
                })

        # ── 2. ARMADOR DE PARLAYS (Combinadas Dobles Seguras) ─────────────────
        # Picks con cuota en el rango seguro (1.30 a 1.45)
        # Excluir partidos ya utilizados en tickets Diamante para no concentrar riesgo
        candidatos_parlay = [
            p for p in picks_verdes_planos
            if not p.get("conflicto_analitico")
            and self.parlay_cuota_min <= p["cuota"] <= self.parlay_cuota_max
            and p["partido"] not in partidos_en_diamante
        ]

        # Deduplicar por partido: máximo 1 pick seguro por partido
        picks_parlay_unicos: dict[str, dict] = {}
        for p in candidatos_parlay:
            partido = p["partido"]
            if partido not in picks_parlay_unicos:
                picks_parlay_unicos[partido] = p
            else:
                # Si hay varios, elegir el que tenga mayor cuota dentro del rango seguro
                if p["cuota"] > picks_parlay_unicos[partido]["cuota"]:
                    picks_parlay_unicos[partido] = p

        lista_parlays_disponibles = list(picks_parlay_unicos.values())
        parlays_duplas: list[dict] = []
        partidos_en_parlays: set[str] = set()

        # Emparejar de 2 en 2 buscando producto cercano a 2.00 (ideal 1.80 a 2.10)
        # Algoritmo de emparejamiento codicioso con distancia mínima a 2.00
        while len(lista_parlays_disponibles) >= 2:
            mejor_par: tuple[int, int] | None = None
            menor_distancia = 999.0

            for i in range(len(lista_parlays_disponibles)):
                for j in range(i + 1, len(lista_parlays_disponibles)):
                    p1 = lista_parlays_disponibles[i]
                    p2 = lista_parlays_disponibles[j]
                    cuota_combinada = p1["cuota"] * p2["cuota"]
                    distancia = abs(cuota_combinada - 2.00)
                    if distancia < menor_distancia:
                        menor_distancia = distancia
                        mejor_par = (i, j)

            if mejor_par is None:
                break

            idx1, idx2 = mejor_par
            p1 = lista_parlays_disponibles[idx1]
            p2 = lista_parlays_disponibles[idx2]

            cuota_total = round(p1["cuota"] * p2["cuota"], 2)
            prob1 = _obtener_probabilidad_estimada(p1)
            prob2 = _obtener_probabilidad_estimada(p2)
            prob_comb = round(prob1 * prob2, 4)
            ev_parlay = (prob_comb * cuota_total) - 1.0
            ev_parlay_str = f"{round(ev_parlay * 100, 1):+0.1f}%"

            _, _, stake_parlay = calcular_fraccional_kelly(
                prob_comb, cuota_total, divisor=8.0, stake_min=0.0025, stake_max=0.015
            )

            parlays_duplas.append({
                "id_ticket":         f"PARLAY-{len(parlays_duplas)+1}",
                "tipo":              "Combinada Doble (x2)",
                "cuota_total":       cuota_total,
                "prob_combinada":    f"{prob_comb:.1%}",
                "valor_esperado_ev": ev_parlay_str,
                "stake_sugerido":    stake_parlay,
                "patas": [
                    {
                        "partido":     p1["partido"],
                        "competicion": p1["competicion"],
                        "mercado":     p1["mercado"],
                        "seleccion":   p1["seleccion"],
                        "cuota":       p1["cuota"],
                        "prob_est":    f"{prob1:.1%}",
                    },
                    {
                        "partido":     p2["partido"],
                        "competicion": p2["competicion"],
                        "mercado":     p2["mercado"],
                        "seleccion":   p2["seleccion"],
                        "cuota":       p2["cuota"],
                        "prob_est":    f"{prob2:.1%}",
                    },
                ],
                "justificacion": (
                    f"Agrupación de 2 selecciones de alta consistencia sin intersección de partidos "
                    f"(@ {p1['cuota']} y @ {p2['cuota']}). Cuota combinada: {cuota_total} | "
                    f"Prob. combinada estimada: {prob_comb:.1%} | EV: {ev_parlay_str}."
                ),
            })

            partidos_en_parlays.add(p1["partido"])
            partidos_en_parlays.add(p2["partido"])

            # Remover los elementos emparejados (en orden inverso de índice)
            for idx in sorted([idx1, idx2], reverse=True):
                lista_parlays_disponibles.pop(idx)

        return {
            "simples_diamante":     simples_diamante,
            "parlays_duplas":       parlays_duplas,
            "total_tickets":        len(simples_diamante) + len(parlays_duplas),
            "partidos_protegidos":  len(partidos_en_diamante) + len(partidos_en_parlays),
        }
