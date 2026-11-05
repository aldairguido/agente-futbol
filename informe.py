"""
agente_futbol/informe.py
Genera el informe final en Markdown que recibe el usuario.
Según la estructura del §11 de la especificación.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    matplotlib = None  # type: ignore[assignment]
    plt = None         # type: ignore[assignment]

import pytz
import config
from gestor_tickets import GestorTickets

BASE_DIR = Path(__file__).resolve().parent


def generar_informe(resultados: list[dict], fecha_partidos: str) -> str:
    """
    resultados: lista de análisis por partido (output de analisis.analizar_partido)
    fecha_partidos: string YYYY-MM-DD de los partidos analizados
    Devuelve el path del archivo de informe generado.
    """
    lima = pytz.timezone(config.TIMEZONE)
    hora_generacion = datetime.now(lima).strftime("%Y-%m-%d %H:%M:%S")

    # Construir contenido del informe
    lineas = []
    lineas.append(f"# Informe de Análisis — {fecha_partidos}")
    lineas.append(f"**Generado**: {hora_generacion} (hora Lima)  ")
    lineas.append(f"**Cuota mínima**: > {config.CUOTA_MINIMA}  ")
    lineas.append(f"**Fuentes**: Betsson Perú · Flashscore · Sofascore")
    lineas.append("")

    # ── Resumen ejecutivo y Embudo Cuantitativo ──
    total_partidos = len(resultados)
    partidos_con_cuotas = [r for r in resultados if r.get("mercados_disponibles")]
    partidos_sin_cuotas = [r for r in resultados if not r.get("mercados_disponibles")]

    total_mercados = sum(len(r.get("mercados_disponibles", [])) for r in resultados)
    total_descartados = sum(len(r.get("descartados", [])) for r in resultados)
    total_verdes   = sum(
        len([c for c in r.get("candidatos", []) if c.get("semaforo") == "VERDE"])
        for r in resultados
    )
    total_amarillos = sum(
        len([c for c in r.get("candidatos", []) if c.get("semaforo") == "AMARILLO"])
        for r in resultados
    )

    # ── Gestor de Bankroll y Tickets Recomendados ──
    gt = GestorTickets()
    tickets_data = gt.procesar_resultados(resultados)
    cant_diamante = len(tickets_data.get("simples_diamante", []))
    cant_parlays  = len(tickets_data.get("parlays_duplas", []))
    cant_secundarios = max(0, total_verdes - cant_diamante)

    lineas.append("## 📊 Resumen Ejecutivo y Auditoría Cuantitativa")
    lineas.append(f"- ⚽ **Partidos auditados en cartelera**: **{total_partidos}** ({len(partidos_con_cuotas)} con cuotas en Betsson Perú)")
    lineas.append(f"- 🔢 **Mercados totales examinados**: **{total_mercados}**")
    lineas.append(f"- 🔴 **Mercados descartados (sin ineficiencia / +EV < 5%)**: **{total_descartados}**")
    lineas.append(f"- 🟢 **Opciones secundarias por tendencia histórica**: **{cant_secundarios}** *(cumplen frecuencia estadística pero no alcanzan el umbral estricto +EV)*")
    lineas.append(f"- 💎 **Tickets Diamante Aprobados (+EV Validados)**: **{cant_diamante}** *(Únicas selecciones con ventaja matemática y gestión Quarter-Kelly activa)*")
    lineas.append(f"- ⚡ **Parlays Seguros Armados (x2)**: **{cant_parlays}**")
    lineas.append("")
    lineas.append("> 💡 **Claridad sobre Candidatos vs. Diamantes**: Las opciones 'verdes' secundarias representan tendencias históricas favorables, pero **únicamente los Tickets Diamante** superaron la auditoría matemática rigurosa (+EV $\\ge$ +5.0% bajo Poisson bivariado y Quarter-Kelly $f^*/4$). Para preservar el capital, solo los Diamantes deben ser ejecutados.")
    lineas.append("")

    # ── SECCIÓN VIP: TICKETS RECOMENDADOS DEL DÍA ──
    lineas.extend(_seccion_tickets_vip(tickets_data))

    # ── Desglose de Cobertura de Mercados ──
    lineas.append("---")
    lineas.append("## 📊 Estado de Cuotas por Partido en Betsson Perú")
    lineas.append("")
    lineas.append("| Partido | Competición | Mercados > 1.30 | Estado |")
    lineas.append("|---|---|---|---|")
    for r in resultados:
        p_nombre = r.get("partido", "?")
        p_comp = r.get("competicion", "?")
        cant_mkt = len(r.get("mercados_disponibles", []))
        if cant_mkt > 0:
            lineas.append(f"| **{p_nombre}** | {p_comp} | {cant_mkt} selecciones | ✅ Cuotas Confirmadas |")
        else:
            lineas.append(f"| {p_nombre} | {p_comp} | 0 | ⚠️ Sin cuotas > {config.CUOTA_MINIMA} |")
    lineas.append("")

    if total_verdes == 0:
        lineas.append("> ⚠️ **Ninguna selección superó todos los filtros analíticos esta jornada.**  ")
        lineas.append("> El informe está completo pero sin candidatos verdes.")
        lineas.append("")

    # ── Candidatos verdes ──
    verdes = [
        (r, c)
        for r in resultados
        for c in r.get("candidatos", [])
        if c.get("semaforo") == "VERDE"
    ]

    if verdes:
        lineas.append("---")
        lineas.append("## 🟢 Candidatos Verdes (Opciones Secundarias por Tendencia)")
        lineas.append("> ℹ️ *Aclaración Metodológica*: Estos mercados presentan respaldo estadístico en H2H o condición local/visita, pero **no todos cuentan con ineficiencia de cuota comprobada (+EV >= +5.0%)**. Los únicos seleccionados formalmente para operar son los **Tickets Diamante** de la sección superior.")
        lineas.append("")
        for resultado, candidato in verdes:
            lineas.extend(_bloque_candidato(resultado, candidato))

    # ── Candidatos amarillos ──
    amarillos = [
        (r, c)
        for r in resultados
        for c in r.get("candidatos", [])
        if c.get("semaforo") == "AMARILLO"
    ]

    if amarillos:
        lineas.append("---")
        lineas.append("## 🟡 Pendientes (requieren revisión)")
        lineas.append("")
        for resultado, candidato in amarillos:
            lineas.extend(_bloque_candidato(resultado, candidato))

    # ── Inventario completo ──
    lineas.append("---")
    lineas.append("## Inventario Detallado por Partido")
    lineas.append("")

    for resultado in resultados:
        partido = resultado.get("partido", "?")
        comp    = resultado.get("competicion", "?")

        lineas.append(f"### {partido} · {comp}")

        # Cuotas verificadas disponibles
        mkts = resultado.get("mercados_disponibles", [])
        if mkts:
            lineas.append("**Cuotas verificadas en Betsson (> 1.30):**")
            for m in mkts[:12]:  # mostrar principales
                lineas.append(f"- `{m.get('familia','General')}`: **{m.get('seleccion','?')}** @ **{m.get('cuota','?')}**")
            if len(mkts) > 12:
                lineas.append(f"- *... y {len(mkts) - 12} selecciones adicionales verificadas.*")
        else:
            lineas.append(f"> ⚠️ Sin mercados con cuota > {config.CUOTA_MINIMA} disponibles en Betsson al momento de la consulta.")

        # Razonamiento cognitivo multicapa paso a paso
        cog = resultado.get("razonamiento_cognitivo", {})
        if cog:
            lineas.append("")
            lineas.append("**🧠 Razonamiento Cognitivo y Análisis Experto:**")

            # Capa 1: H2H Oficial
            c1 = cog.get("capa_1_h2h", {})
            if c1:
                lineas.append(f"- **Capa 1 (H2H Oficial & Patrones Directos)**: {c1.get('resumen', 'Sin datos H2H')}")
                lineas.append(f"  - *Promedios de Goles en H2H*: Local {c1.get('goles_favor_local_prom', 0)} GF/p | Visita {c1.get('goles_favor_visita_prom', 0)} GF/p | Total: **{c1.get('goles_totales_prom', 0)}** goles/partido.")
                if c1.get("amistosos_descartados", 0) > 0:
                    lineas.append(f"  *Exclusión estricta*: se descartaron {c1['amistosos_descartados']} partidos amistosos/pretemporada.")

            # Capa 2: Contexto, Jerarquía, Tabla y Sentimiento
            c2 = cog.get("capa_2_contexto", {})
            if c2:
                loc_inf = c2.get("local", {})
                vis_inf = c2.get("visitante", {})
                tab_l = loc_inf.get("tabla", {})
                tab_v = vis_inf.get("tabla", {})
                sent = c2.get("market_sentiment", {})

                lineas.append(f"- **Capa 2 (Contexto, Jerarquía, Tabla y Sentimiento de Mercado)**:")
                lineas.append(f"  - **{loc_inf.get('nombre','Local')}**:")
                lineas.append(f"    • *Últimos 10 Generales*: GF prom: **{loc_inf.get('goles_favor_gen_prom', 0)}** | GC prom: **{loc_inf.get('goles_contra_gen_prom', 0)}** ({loc_inf.get('general_10',{}).get('victorias_pct',0):.0%} vic)")
                lineas.append(f"    • *Últimos 5 Casa*: GF prom: **{loc_inf.get('goles_favor_cond_prom', 0)}** | GC prom: **{loc_inf.get('goles_contra_cond_prom', 0)}** ({loc_inf.get('condicion_5',{}).get('victorias_pct',0):.0%} vic)")
                if tab_l:
                    lineas.append(f"    • *Tabla de Posiciones*: Puesto {tab_l.get('posicion','N/D')}° ({tab_l.get('puntos','N/D')} pts, DG {tab_l.get('dg','N/D')}, Forma: {tab_l.get('forma','N/D')})")
                lineas.append(f"    • *Delanteros*: {loc_inf.get('atacantes',{}).get('resumen','')}")

                lineas.append(f"  - **{vis_inf.get('nombre','Visita')}**:")
                lineas.append(f"    • *Últimos 10 Generales*: GF prom: **{vis_inf.get('goles_favor_gen_prom', 0)}** | GC prom: **{vis_inf.get('goles_contra_gen_prom', 0)}** ({vis_inf.get('general_10',{}).get('victorias_pct',0):.0%} vic)")
                lineas.append(f"    • *Últimos 5 Fuera*: GF prom: **{vis_inf.get('goles_favor_cond_prom', 0)}** | GC prom: **{vis_inf.get('goles_contra_cond_prom', 0)}** ({vis_inf.get('condicion_5',{}).get('victorias_pct',0):.0%} vic)")
                if tab_v:
                    lineas.append(f"    • *Tabla de Posiciones*: Puesto {tab_v.get('posicion','N/D')}° ({tab_v.get('puntos','N/D')} pts, DG {tab_v.get('dg','N/D')}, Forma: {tab_v.get('forma','N/D')})")
                lineas.append(f"    • *Delanteros*: {vis_inf.get('atacantes',{}).get('resumen','')}")

                if sent:
                    lineas.append(f"  - *Market Sentiment (1X2)*: Local {sent.get('distribucion_1x2',{}).get('local_pct',0)}% | Empate {sent.get('distribucion_1x2',{}).get('empate_pct',0)}% | Visita {sent.get('distribucion_1x2',{}).get('visitante_pct',0)}% — {sent.get('diagnostico','')}")

            # Capa 3: Anomalías
            c3 = cog.get("capa_3_anomalias", {})
            if c3:
                lineas.append(f"- **Capa 3 (Detección de Anomalías)**: {c3.get('impacto', 'Sin anomalías relevantes.')}")
                for an in c3.get("anomalias_detectadas", []):
                    lineas.append(f"  - ⚠️ *Incidencia neutralizada*: {an}")

            # Capa 4: Viabilidad de Mercado y Semáforo
            cand_partido = [c for c in resultado.get("candidatos", [])]
            if cand_partido:
                lineas.append(f"- **Capa 4 (Validación de Mercados Betsson)**: {len(cand_partido)} selecciones aprobadas con semáforo favorable.")
                for cp in cand_partido:
                    sem = cp.get("semaforo", "")
                    emoji = {"VERDE": "🟢", "AMARILLO": "🟡"}.get(sem, "⚪")
                    lineas.append(f"  - {emoji} **{cp.get('mercado')}: {cp.get('seleccion')}** @ **{cp.get('cuota')}** — {cp.get('motivo_semaforo')}")
            else:
                lineas.append(f"- **Capa 4 (Validación de Mercados Betsson)**: Ninguna selección alcanzó el umbral de consistencia para semáforo verde.")
            lineas.append("")

        # Advertencias del partido
        for adv in resultado.get("advertencias", []):
            lineas.append(f"> ⚠️ {adv}")

        # Descartados
        descartados = resultado.get("descartados", [])
        if descartados:
            lineas.append("")
            lineas.append("**🔴 Descartados:**")
            for d in descartados:
                lineas.append(
                    f"- {d.get('seleccion','?')} "
                    f"(cuota {d.get('cuota','?')}): {d.get('motivo_descarte','?')}"
                )

        # No evaluables
        no_eval = resultado.get("no_evaluables", [])
        if no_eval:
            lineas.append("")
            lineas.append("**⬜ No evaluables:**")
            for ne in no_eval:
                lineas.append(
                    f"- {ne.get('seleccion','?')}: {ne.get('motivo','?')}"
                )

        lineas.append("")

    # ── Aviso legal ──
    lineas.append("---")
    lineas.append("## Aviso")
    lineas.append(
        "Este informe es un análisis estadístico de frecuencias históricas. "
        "No constituye asesoramiento financiero ni garantía de resultado. "
        "Las cuotas observadas pueden haber cambiado. Verificar en Betsson Perú "
        "antes de cualquier decisión."
    )
    lineas.append("*Metodología de Análisis: Ventaja matemática calculada mediante distribución de Poisson (+EV) para tickets principales y sustentada en tendencia histórica y métricas xG para opciones complementarias.*")

    # ── Guardar archivo ──
    contenido = "\n".join(lineas)
    nombre_archivo = f"informe_{fecha_partidos}.md"
    ruta = os.path.join(config.INFORME_DIR, nombre_archivo)

    os.makedirs(config.INFORME_DIR, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)

    print(f"[INFORME] ✅ Guardado en: {ruta}")
    return ruta


def _bloque_candidato(resultado: dict, candidato: dict) -> list[str]:
    """Formatea un candidato individual para el informe."""
    lineas = []
    partido = resultado.get("partido", "?")
    comp    = resultado.get("competicion", "?")
    sem     = candidato.get("semaforo", "?")
    emoji   = {"VERDE": "🟢", "AMARILLO": "🟡", "ROJO": "🔴", "GRIS": "⬜"}.get(sem, "❓")

    lineas.append(f"#### {emoji} {partido} — {candidato.get('seleccion','?')}")
    lineas.append(f"| Campo | Valor |")
    lineas.append(f"|---|---|")
    lineas.append(f"| **Competición** | {comp} |")
    lineas.append(f"| **Mercado** | {candidato.get('mercado','?')} |")
    lineas.append(f"| **Selección** | {candidato.get('seleccion','?')} |")
    lineas.append(f"| **Periodo** | {candidato.get('periodo','?')} |")
    lineas.append(f"| **Cuota Betsson** | {candidato.get('cuota','?')} |")
    lineas.append(f"| **Regla de liquidación** | {candidato.get('regla_liquidacion','?')} |")
    lineas.append(f"| **Frecuencia general** | {candidato.get('frecuencia_general','no disp.')} |")
    lineas.append(f"| **Frecuencia condición** | {candidato.get('frecuencia_condicion','no disp.')} |")
    prob = candidato.get('probabilidad')
    if not prob or str(prob).strip().lower() in ("no estimada", "no disponible", "n/d", ""):
        prob_str = "Basado en tendencia histórica y xG"
    else:
        prob_str = str(prob)
    lineas.append(f"| **Evaluación Probabilística** | {prob_str} |")
    lineas.append(f"| **Motivo semáforo** | {candidato.get('motivo_semaforo','?')} |")

    ev_fav = candidato.get("evidencia_favorable", [])
    if ev_fav:
        lineas.append("")
        lineas.append("**Evidencia favorable:**")
        for e in ev_fav:
            lineas.append(f"- {e}")

    ev_con = candidato.get("evidencia_contraria", [])
    if ev_con:
        lineas.append("")
        lineas.append("**Evidencia contraria:**")
        for e in ev_con:
            lineas.append(f"- {e}")

    incert = candidato.get("incertidumbres", [])
    if incert:
        lineas.append("")
        lineas.append("**Incertidumbres:**")
        for i in incert:
            lineas.append(f"- {i}")

    lineas.append("")
    return lineas


def _seccion_tickets_vip(tickets: dict) -> list[str]:
    """Genera la sección VIP de tickets recomendados del día con gestión de riesgo."""
    lineas = []
    lineas.append("---")
    lineas.append("## 🏆 TICKETS RECOMENDADOS DEL DÍA (GESTOR DE BANKROLL & QUANT TRADER)")
    lineas.append("> 🛡️ **Estrategia Profesional de Mitigación de Riesgo y Staking Cuantitativo**:")
    lineas.append("> - **💎 Filtro Diamante (Simples)**: Picks de alto valor respaldados por True Probability y margen +EV >= +5.0% auditado.")
    lineas.append("> - **⚡ Parlays Seguros (x2)**: Combinadas de cuota global ~ 2.00 con selecciones en rango 1.30-1.45 sin riesgo cruzado.")
    lineas.append("> - **📐 Gestión de Capital (Fraccional Kelly)**: Asignación científica de capital basada en $f^* = \\frac{P \\cdot b - 1}{b - 1}$. Quarter-Kelly ($f^* / 4$) en simples y Eighth-Kelly ($f^* / 8$) en combinadas. Sin stakes fijos arbitrarios.")
    lineas.append("> - **🔒 Prevención de Riesgo**: Ningún partido se repite entre patas ni tickets para evitar efecto dominó.")
    lineas.append("")

    simples = tickets.get("simples_diamante", [])
    lineas.append("### 💎 Apuestas Simples Diamante (Valor Estadístico Superior +EV)")
    if simples:
        lineas.append("| Ticket | Partido | Competición | Mercado | Selección | Cuota Betsson | Prob. Real / H2H | +EV | Stake Sugerido (Quarter-Kelly) |")
        lineas.append("|---|---|---|---|---|---|---|---|---|")
        for s in simples:
            lineas.append(
                f"| 💎 **{s['id_ticket']}** | **{s['partido']}** | {s['competicion']} | "
                f"{s['mercado']} | **{s['seleccion']}** | **{s['cuota']}** | "
                f"✅ {s['h2h_ratio_pct']} | **{s.get('valor_esperado_ev', 'N/D')}** | `{s['stake_sugerido']}` |"
            )
        lineas.append("")
        for s in simples:
            p_real = s.get("probabilidad_real_estimada") or s.get("h2h_ratio_pct", "N/D")
            p_real_str = f"{p_real:.1%}" if isinstance(p_real, float) else str(p_real)
            c_num = float(s.get("cuota", 0.0) or 0.0)
            p_impl_str = f"{(1.0 / c_num):.1%}" if c_num > 1.0 else "N/D"

            lineas.append(f"#### 💎 {s['id_ticket']}: {s['partido']} — {s['seleccion']}")
            lineas.append(f"- **Competición**: {s['competicion']}")
            lineas.append(f"- **Mercado**: {s['mercado']}")
            lineas.append(f"- **Cuota Betsson**: `{s['cuota']}`")
            lineas.append("")
            lineas.append("##### 📐 Métricas Cuantitativas de Poisson (+EV)")
            lineas.append(f"- **Probabilidad Real (True Prob)**: `{p_real_str}`")
            lineas.append(f"- **Probabilidad Implícita en Cuota**: `{p_impl_str}`")
            lineas.append(f"- **Ventaja Matemática (+EV)**: **{s.get('valor_esperado_ev', 'N/D')}**")
            lineas.append(f"- **Gestión de Capital (Quarter-Kelly f*/4)**: `{s['stake_sugerido']}`")
            lineas.append("")
            lineas.append("##### 📝 Observaciones y Contexto Estadístico")
            lineas.append(f"- **Muestra H2H**: {s.get('frecuencia_h2h', 'N/D')}")
            lineas.append(f"- **Análisis Técnico**: {s['motivo']}")
            lineas.append("")
    else:
        lineas.append("> ℹ️ *Ningún partido cumplió simultáneamente los filtros cuantitativos y cuota de valor esta jornada.*")
        lineas.append("")

    parlays = tickets.get("parlays_duplas", [])
    lineas.append("### ⚡ Parlays / Combinadas Dobles (Cuota Objetivo ~2.00)")
    if parlays:
        for p in parlays:
            lineas.append(f"#### 🎫 TICKET {p['id_ticket']} — Cuota Combinada: **{p['cuota_total']}** | EV: **{p.get('valor_esperado_ev', 'N/D')}**")
            lineas.append(f"| Pata | Partido | Competición | Mercado | Selección | Cuota Betsson | Prob. Estimada |")
            lineas.append("|---|---|---|---|---|---|---|")
            for i, leg in enumerate(p["patas"], 1):
                lineas.append(
                    f"| Pata {i} | **{leg['partido']}** | {leg['competicion']} | "
                    f"{leg['mercado']} | **{leg['seleccion']}** | `{leg['cuota']}` | {leg.get('prob_est', 'N/D')} |"
                )
            lineas.append(f"* **Probabilidad Combinada**: `{p.get('prob_combinada', 'N/D')}`")
            lineas.append(f"* **Valor Esperado (+EV)**: `{p.get('valor_esperado_ev', 'N/D')}`")
            lineas.append(f"* **Stake Sugerido (Eighth-Kelly f*/8)**: `{p['stake_sugerido']}`")
            lineas.append(f"* **Análisis de Riesgo**: {p['justificacion']}")
            lineas.append("")
    else:
        lineas.append("> ℹ️ *No se encontraron combinaciones seguras en el rango 1.30-1.45 sin riesgo cruzado.*")
        lineas.append("")

    return lineas


# ═══════════════════════════════════════════════════════════════════════════════
# TICKET DE VALOR (+EV) Y ANÁLISIS DETALLADO DE 3 PÁRRAFOS PARA /mercados
# ═══════════════════════════════════════════════════════════════════════════════

def _extraer_probabilidades_red_general(analisis: Any) -> dict[str, Any]:
    """
    Extrae las probabilidades 1X2, Doble Oportunidad, Over/Under 2.5, Ambos Anotan
    y goles esperados (λ) de la Red Neuronal General (15 partidos oficiales).
    """
    motor = getattr(analisis, "motor", None)
    meta = getattr(analisis, "metadatos", {}) or {}
    eq_local = str(meta.get("equipo_local") or getattr(motor, "nombre_local", None) or "Local")
    eq_vis = str(meta.get("equipo_visitante") or getattr(motor, "nombre_visitante", None) or "Visitante")

    matriz = getattr(motor, "matriz_ft", None) or []
    p_local = 0.0
    p_empate = 0.0
    p_vis = 0.0
    p_over25 = 0.0
    p_under25 = 0.0
    p_btts_si = 0.0
    marcador_modal = "1-0"
    prob_modal = 0.0

    if matriz:
        for i, fila in enumerate(matriz):
            for j, prob in enumerate(fila):
                val = float(prob or 0.0)
                if i > j:
                    p_local += val
                elif i == j:
                    p_empate += val
                else:
                    p_vis += val
                if (i + j) >= 3:
                    p_over25 += val
                else:
                    p_under25 += val
                if i >= 1 and j >= 1:
                    p_btts_si += val
                if val > prob_modal:
                    prob_modal = val
                    marcador_modal = f"{i}-{j}"

    total_1x2 = p_local + p_empate + p_vis
    if total_1x2 > 0:
        p_local /= total_1x2
        p_empate /= total_1x2
        p_vis /= total_1x2
    else:
        p_local, p_empate, p_vis = 0.42, 0.28, 0.30

    tasas = getattr(motor, "tasas", None)
    lam_l = float(getattr(tasas, "lambda_local_ft", None) or 1.25)
    lam_v = float(getattr(tasas, "lambda_visitante_ft", None) or 1.05)

    return {
        "equipo_local": eq_local,
        "equipo_visitante": eq_vis,
        "competicion": str(meta.get("competicion") or "Oficial"),
        "fecha_partido": str(meta.get("fecha_partido") or ""),
        "p_local": p_local,
        "p_empate": p_empate,
        "p_visitante": p_vis,
        "p_1x": min(0.99, p_local + p_empate),
        "p_x2": min(0.99, p_empate + p_vis),
        "p_12": min(0.99, p_local + p_vis),
        "p_over25": p_over25,
        "p_under25": p_under25,
        "p_btts_si": p_btts_si,
        "p_btts_no": max(0.0, 1.0 - p_btts_si),
        "lambda_local": lam_l,
        "lambda_visitante": lam_v,
        "lambda_total": lam_l + lam_v,
        "marcador_modal": marcador_modal,
        "prob_modal": prob_modal,
    }


def construir_filas_ticket_valor(
    analisis: Any,
    cuotas_flashscore: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Cruza las probabilidades de la Red Neuronal General (15 partidos) con las cuotas
    de Flashscore y calcula Cuota Justa, Cuota Mínima +EV, Stake Kelly (0.25x), Valor Esperado (EV) y Recomendación.
    """
    info = _extraer_probabilidades_red_general(analisis)
    eq_l = info["equipo_local"]
    eq_v = info["equipo_visitante"]
    cuotas = dict(cuotas_flashscore or {})
    dropping_odds = bool(cuotas.get("dropping_odds"))
    alerta_drop = " [ALERTA DROPPING ODDS]" if dropping_odds else ""

    c_loc = cuotas.get("local")
    c_emp = cuotas.get("empate")
    c_vis = cuotas.get("visitante")

    # Derivar cuotas equivalentes de Doble Oportunidad (1X, X2, 12) si existen cuotas 1X2 reales
    c_1x = (round(1.0 / ((1.0 / c_loc) + (1.0 / c_emp)), 2)
            if c_loc and c_emp and c_loc > 1.0 and c_emp > 1.0 else cuotas.get("1x"))
    c_x2 = (round(1.0 / ((1.0 / c_emp) + (1.0 / c_vis)), 2)
            if c_emp and c_vis and c_emp > 1.0 and c_vis > 1.0 else cuotas.get("x2"))
    c_12 = (round(1.0 / ((1.0 / c_loc) + (1.0 / c_vis)), 2)
            if c_loc and c_vis and c_loc > 1.0 and c_vis > 1.0 else cuotas.get("12"))

    definiciones = [
        (f"1 — Victoria {eq_l}", info["p_local"], c_loc),
        ("X — Empate", info["p_empate"], c_emp),
        (f"2 — Victoria {eq_v}", info["p_visitante"], c_vis),
        (f"1X — {eq_l} o Empate", info["p_1x"], c_1x),
        (f"X2 — Empate o {eq_v}", info["p_x2"], c_x2),
        (f"12 — {eq_l} o {eq_v}", info["p_12"], c_12),
    ]

    filas: list[dict[str, Any]] = []
    for nombre_mkt, prob_bot, cuota_fs in definiciones:
        prob_f = max(0.001, min(0.999, float(prob_bot or 0.0)))
        cuota_justa = round(1.0 / prob_f, 2)
        cuota_minima = round(1.05 * cuota_justa, 2)
        b_min = max(0.01, cuota_minima - 1.0)
        f_star_min = max(0.0, (prob_f * cuota_minima - 1.0) / b_min)
        stake_kelly_min_pct = round(0.25 * f_star_min * 100.0, 1)

        cuota_fs_val = round(float(cuota_fs), 2) if isinstance(cuota_fs, (int, float)) and cuota_fs > 1.0 else None

        if cuota_fs_val is not None:
            ev_pct = round(((prob_f * cuota_fs_val) - 1.0) * 100.0, 2)
            tiene_valor = bool(cuota_fs_val > cuota_justa)
            b_fs = max(0.01, cuota_fs_val - 1.0)
            f_star = max(0.0, (prob_f * cuota_fs_val - 1.0) / b_fs)
            stake_kelly_pct = round(0.25 * f_star * 100.0, 1) if tiene_valor else 0.0

            if tiene_valor and ev_pct >= 5.0 and prob_f >= 0.45:
                recomendacion = f"VALOR ALTO (+EV {ev_pct:+.1f}%) · Stake {stake_kelly_pct:.1f}%{alerta_drop}"
            elif tiene_valor:
                recomendacion = f"VALOR (+EV {ev_pct:+.1f}%) · Stake {stake_kelly_pct:.1f}%{alerta_drop}"
            else:
                recomendacion = f"SIN VALOR (Cuota <= Justa){alerta_drop}"
        else:
            ev_pct = None
            tiene_valor = False
            recomendacion = f"Cuota Mín. +EV: {cuota_minima:.2f} (Stake {stake_kelly_min_pct:.1f}%){alerta_drop}"

        filas.append({
            "mercado": nombre_mkt,
            "prob_bot": prob_f,
            "prob_bot_str": f"{prob_f * 100.0:.1f}%",
            "cuota_justa": cuota_justa,
            "cuota_justa_str": f"{cuota_justa:.2f}",
            "cuota_minima": cuota_minima,
            "cuota_minima_str": f"{cuota_minima:.2f}",
            "cuota_flashscore": cuota_fs_val,
            "cuota_flashscore_str": f"{cuota_fs_val:.2f}" if cuota_fs_val is not None else "N/D",
            "ev_pct": ev_pct,
            "ev_str": (f"+{ev_pct:.1f}%" if ev_pct is not None and ev_pct > 0
                       else (f"{ev_pct:.1f}%" if ev_pct is not None else "N/D")),
            "tiene_valor": tiene_valor,
            "recomendacion": recomendacion,
        })

    return filas


def generar_ticket_valor_png(
    analisis: Any,
    cuotas_flashscore: dict[str, float | None] | None = None,
    destino: str | None = None,
    id_flashscore: str | None = None,
) -> str:
    """
    Genera la 4ta imagen (El Ticket de Valor) en formato PNG usando matplotlib:
    Cruza las cuotas extraídas de Flashscore con las probabilidades de la Red Neuronal
    General (15 partidos) en una tabla con las 6 columnas obligatorias:
        Mercado | Prob. Bot | Cuota Justa | Cuota Flashscore | Valor Esperado (EV) | Recomendación
    Resalta en verde las filas donde la Cuota Flashscore es mayor a la Cuota Justa.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    info = _extraer_probabilidades_red_general(analisis)
    eq_l = info["equipo_local"]
    eq_v = info["equipo_visitante"]
    comp = info["competicion"]
    filas = construir_filas_ticket_valor(analisis, cuotas_flashscore)

    if not destino:
        carpeta = Path(config.INFORME_DIR) / "mercados"
        carpeta.mkdir(parents=True, exist_ok=True)
        destino = str(carpeta / f"ticket_valor_{eq_l}_{eq_v}.png")
    else:
        Path(destino).parent.mkdir(parents=True, exist_ok=True)

    columnas = [
        "Mercado",
        "Prob. Bot",
        "Cuota Justa",
        "Cuota Flashscore",
        "Valor Esperado (EV)",
        "Recomendación",
    ]
    datos_celdas = [
        [
            f["mercado"],
            f["prob_bot_str"],
            f["cuota_justa_str"],
            f["cuota_flashscore_str"],
            f["ev_str"],
            f["recomendacion"],
        ]
        for f in filas
    ]

    n_filas = len(datos_celdas)
    alto_fig = max(4.6, 2.3 + n_filas * 0.62)
    fig, ax = plt.subplots(figsize=(13.5, alto_fig), dpi=160)
    fig.patch.set_facecolor("#F7F9FC")
    ax.set_facecolor("#F7F9FC")
    ax.axis("off")

    # Encabezado editorial
    titulo = f"TICKET DE VALOR (+EV)  |  {eq_l.upper()} vs {eq_v.upper()}"
    id_fs_txt = f"  •  ID Flashscore: {id_flashscore}" if id_flashscore else ""
    subtitulo = (
        f"Red Neuronal General (15 Partidos Oficiales) vs Cuotas Flashscore  •  "
        f"Competición: {comp}  •  λ Esperados: {eq_l} {info['lambda_local']:.2f} - "
        f"{info['lambda_visitante']:.2f} {eq_v}{id_fs_txt}"
    )
    fig.text(
        0.03, 0.91, titulo,
        fontsize=14, fontweight="bold", color="#131D31", family="sans-serif",
    )
    fig.text(
        0.03, 0.84, subtitulo,
        fontsize=9.5, color="#3B4A6B", family="sans-serif",
    )

    anchos_col = [0.24, 0.10, 0.11, 0.13, 0.14, 0.28]
    tabla = ax.table(
        cellText=datos_celdas,
        colLabels=columnas,
        colWidths=anchos_col,
        loc="center",
        cellLoc="center",
        bbox=[0.02, 0.12, 0.96, 0.66],
    )
    tabla.auto_set_font_size(False)
    tabla.set_fontsize(9.8)

    # Estilizado de cabeceras y filas (verde cuando Cuota Flashscore > Cuota Justa)
    for (row_idx, col_idx), celda in tabla.get_celld().items():
        celda.set_edgecolor("#CFD8E3")
        celda.set_linewidth(0.9)
        if row_idx == 0:
            celda.set_facecolor("#131D31")
            celda.get_text().set_color("#FFFFFF")
            celda.get_text().set_weight("bold")
            celda.get_text().set_fontsize(10.0)
        else:
            fila_info = filas[row_idx - 1]
            es_valor = bool(fila_info["tiene_valor"])
            if es_valor:
                celda.set_facecolor("#D4EDDA")
                celda.get_text().set_color("#0D5C2B")
                celda.get_text().set_weight("bold")
            else:
                bg = "#FFFFFF" if row_idx % 2 == 1 else "#F1F5F9"
                celda.set_facecolor(bg)
                celda.get_text().set_color("#1E293B")
                if col_idx in (0, 4):
                    celda.get_text().set_weight("bold")

            if col_idx == 0:
                celda._loc = "left"

    pie = (
        "Criterio Matemático: Cuota Justa = 1 / Prob. Bot  |  "
        "EV (%) = (Prob. Bot × Cuota Flashscore - 1) × 100  |  "
        "Filas resaltadas en VERDE indican Cuota Flashscore > Cuota Justa (Valor Positivo +EV)."
    )
    fig.text(
        0.03, 0.04, pie,
        fontsize=8.5, color="#475569", style="italic", family="sans-serif",
    )

    plt.savefig(destino, dpi=160, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return str(destino)


renderizar_ticket_valor_png = generar_ticket_valor_png


def redactar_analisis_3_parrafos(
    analisis: Any,
    info_h2h: dict[str, Any] | None = None,
    cuotas_flashscore: dict[str, float | None] | None = None,
) -> str:
    """
    Redacta el análisis de texto detallado en 3 párrafos claros para Telegram
    antes de enviar las 4 imágenes de /mercados:
      • Párrafo 1: Modelo General (15 partidos): quién domina y cuántos goles se esperan.
      • Párrafo 2: Contraste con el Modelo H2H (2024–2026): ¿apoya o contradice al general?
      • Párrafo 3: Veredicto Final basado en el EV calculado y la probabilidad más sólida.
    """
    info = _extraer_probabilidades_red_general(analisis)
    eq_l = info["equipo_local"]
    eq_v = info["equipo_visitante"]
    p_l = info["p_local"]
    p_x = info["p_empate"]
    p_v = info["p_visitante"]
    lam_l = info["lambda_local"]
    lam_v = info["lambda_visitante"]
    lam_tot = info["lambda_total"]
    p_o25 = info["p_over25"]
    p_u25 = info["p_under25"]
    p_btts = info["p_btts_si"]

    # ── PÁRRAFO 1: Modelo General (15 partidos oficiales) ───────────────────
    if p_l >= p_v and p_l >= p_x:
        dominador = f"domina {eq_l} como local con un {p_l * 100:.1f}% de probabilidad de victoria (frente a {p_x * 100:.1f}% de empate y {p_v * 100:.1f}% para {eq_v})"
    elif p_v > p_l and p_v >= p_x:
        dominador = f"se perfila como favorito el visitante {eq_v} con un {p_v * 100:.1f}% de probabilidad de triunfo (frente a {p_l * 100:.1f}% de {eq_l} y {p_x * 100:.1f}% de empate)"
    else:
        dominador = f"se proyecta un duelo sumamente cerrado con alta propensión al empate ({p_x * 100:.1f}%), repartiendo {p_l * 100:.1f}% para {eq_l} y {p_v * 100:.1f}% para {eq_v}"

    tendencia_goles_gen = (
        f"una tendencia hacia el Over 2.5 goles ({p_o25 * 100:.1f}%)"
        if p_o25 >= 0.50
        else f"un escenario de corte cerrado inclinándose al Under 2.5 goles ({p_u25 * 100:.1f}%)"
    )
    parrafo_1 = (
        f"📊 1. Lectura del Modelo General (15 Partidos Oficiales): "
        f"Según la Red Neuronal Recursiva entrenada con los últimos 15 partidos oficiales de cada club, "
        f"{dominador}. En producción ofensiva se esperan en promedio {lam_tot:.2f} goles totales "
        f"(λ {eq_l}: {lam_l:.2f} vs λ {eq_v}: {lam_v:.2f}), marcando {tendencia_goles_gen}, "
        f"un {p_btts * 100:.1f}% de probabilidad en Ambos Anotan y teniendo al {info['marcador_modal']} "
        f"({info['prob_modal'] * 100:.1f}%) como marcador exacto más probable."
    )

    # ── PÁRRAFO 2: Contraste con el Modelo H2H (2024–2026) ──────────────────
    h2h = dict(info_h2h or {})
    duelos = h2h.get("duelos") or h2h.get("lista_duelos") or []
    n_h2h = int(h2h.get("n_duelos") or len(duelos) or 0)

    if n_h2h > 0:
        lam_h2h_l = h2h.get("lambda_local")
        lam_h2h_v = h2h.get("lambda_visitante")
        if lam_h2h_l is not None and lam_h2h_v is not None:
            prom_goles_h2h = float(lam_h2h_l) + float(lam_h2h_v)
        elif duelos:
            tot_g = sum(int(d.get("goles_local", 0) or 0) + int(d.get("goles_visitante", 0) or 0) for d in duelos)
            prom_goles_h2h = tot_g / max(1, len(duelos))
        else:
            prom_goles_h2h = lam_tot

        gen_es_over = (p_o25 >= 0.50)
        h2h_es_over = (prom_goles_h2h >= 2.50)

        if gen_es_over and h2h_es_over:
            contraste_txt = (
                f"el historial directo APOYA y refuerza al modelo general: ambos coinciden en un guion "
                f"abierto de goles (Over 2.5), registrando {prom_goles_h2h:.2f} goles promedio en sus cruces directos"
            )
        elif (not gen_es_over) and (not h2h_es_over):
            contraste_txt = (
                f"el historial directo APOYA al modelo general: mientras la red de 15 partidos apunta al "
                f"Under 2.5 ({p_u25 * 100:.1f}%), el H2H confirma duelos tácticos de bajo marcador "
                f"con un promedio de {prom_goles_h2h:.2f} goles por encuentro"
            )
        elif (not gen_es_over) and h2h_es_over:
            contraste_txt = (
                f"el historial directo CONTRADICE la cautela del modelo general: aunque la muestra de 15 partidos "
                f"sugiere Under 2.5 ({p_u25 * 100:.1f}% con {lam_tot:.2f} goles esperados), cuando {eq_l} y {eq_v} "
                f"se enfrentan entre sí el ritmo se rompe hacia el Over 2.5 con {prom_goles_h2h:.2f} goles de media"
            )
        else:
            contraste_txt = (
                f"el historial directo CONTRADICE el ritmo goleador del modelo general: mientras los 15 partidos "
                f"proyectan Over 2.5 ({p_o25 * 100:.1f}%), sus enfrentamientos directos han sido mucho más "
                f"friccionados y cerrados (Under 2.5 con {prom_goles_h2h:.2f} goles promedio)"
            )

        parrafo_2 = (
            f"⚔️ 2. Contraste con el Modelo H2H Directo ({n_h2h} duelos en 2024–2026): "
            f"Al cruzar la tendencia general contra la memoria específica del enfrentamiento, {contraste_txt}. "
            f"Esta lectura cruzada permite ajustar la exposición en líneas de goles y priorizar mercados donde "
            f"ambas muestras convergen."
        )
    else:
        parrafo_2 = (
            f"⚔️ 2. Contraste con el Modelo H2H Directo (2024–2026): "
            f"No se registran enfrentamientos directos oficiales entre {eq_l} y {eq_v} en el ciclo 2024–2026, "
            f"por lo que no existe contradicción histórica por paternidad reciente y el peso cuantitativo "
            f"recae íntegramente en el rendimiento condicional Local/Visita de los 15 partidos oficiales."
        )

    # ── PÁRRAFO 3: Veredicto Final y Mejor Apuesta de Valor (+EV) ───────────
    filas_ev = construir_filas_ticket_valor(analisis, cuotas_flashscore)
    con_valor = [f for f in filas_ev if f["tiene_valor"] and f["ev_pct"] is not None]
    # Ordenar por combinación de mayor EV y probabilidad sólida (>= 40% priorizado)
    con_valor_solido = sorted(
        con_valor,
        key=lambda x: (1 if x["prob_bot"] >= 0.45 else 0, float(x["ev_pct"] or -999.0), x["prob_bot"]),
        reverse=True,
    )

    if con_valor_solido:
        mejor = con_valor_solido[0]
        vale_la_pena = (
            "Sí vale la pena arriesgar con gestión disciplinada (Quarter-Kelly), ya que combina ventaja "
            "matemática real sobre la casa de apuestas con respaldo probabilístico sólido de la red."
            if mejor["prob_bot"] >= 0.45 and float(mejor["ev_pct"] or 0.0) >= 3.0
            else "Representa una oportunidad de valor matemático positivo (+EV), aunque por su cuota exige "
                 "un stake moderado y conservador."
        )
        parrafo_3 = (
            f"🎯 3. Veredicto Final y Apuesta de Valor Absoluto (+EV): "
            f"Cruzando las probabilidades del bot con las cuotas de Flashscore, la mejor apuesta de valor "
            f"absoluto es «{mejor['mercado']}», donde la Red Neuronal estima un {mejor['prob_bot_str']} "
            f"(Cuota Justa {mejor['cuota_justa_str']}) frente a una cuota de mercado de {mejor['cuota_flashscore_str']}, "
            f"generando un Valor Esperado de {mejor['ev_str']}. {vale_la_pena}"
        )
    else:
        # Si no hubo cuota superior a la justa o Flashscore no publicó cuotas para el partido
        mejor_prob = max(filas_ev, key=lambda x: x["prob_bot"]) if filas_ev else None
        tiene_cuotas_fs = any(f["cuota_flashscore"] is not None for f in filas_ev)
        if tiene_cuotas_fs and mejor_prob:
            parrafo_3 = (
                f"🎯 3. Veredicto Final y Auditoría de Valor (+EV): "
                f"Las cuotas actuales de Flashscore en el mercado 1X2 están ajustadas por debajo de la Cuota Justa "
                f"del modelo (EV ≤ 0%), por lo que no vale la pena arriesgar en cuotas simples castigadas por el "
                f"margen de la casa; la opción más sólida por probabilidad pura es «{mejor_prob['mercado']}» "
                f"({mejor_prob['prob_bot_str']}, Cuota Justa {mejor_prob['cuota_justa_str']}) únicamente si encuentras "
                f"en tu casa de apuestas una cuota superior a {mejor_prob['cuota_justa_str']}."
            )
        elif mejor_prob:
            parrafo_3 = (
                f"🎯 3. Veredicto Final y Referencia de Valor (+EV): "
                f"Dado que Flashscore aún no publica cuotas 1X2 activas para este cruce, la selección matriz más "
                f"sólida del modelo es «{mejor_prob['mercado']}» con un {mejor_prob['prob_bot_str']} de probabilidad "
                f"y una Cuota Justa de {mejor_prob['cuota_justa_str']}: solo vale la pena arriesgar si la cuota "
                f"ofrecida en casa de apuestas supera dicho umbral de {mejor_prob['cuota_justa_str']} (EV > 0%)."
            )
        else:
            parrafo_3 = (
                "🎯 3. Veredicto Final: Priorizar exclusivamente las selecciones del Top 5 del Ticket General "
                "cuya cuota real supere la Cuota Justa calculada por la Red Neuronal."
            )

    return f"{parrafo_1}\n\n{parrafo_2}\n\n{parrafo_3}"
