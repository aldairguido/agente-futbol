"""
agente_futbol/informe.py
Genera el informe final en Markdown que recibe el usuario.
Según la estructura del §11 de la especificación.
"""

import os
from datetime import datetime
import pytz
import config
from gestor_tickets import GestorTickets


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
