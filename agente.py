"""
agente_futbol/agente.py
Script principal del agente de análisis de fútbol.

Uso:
  python agente.py                    → analiza partidos de mañana
  python agente.py --url <URL_BETSSON> → analiza un partido específico por enlace

Flujo:
  1. Pre-filtro en Betsson: identificar partidos con mercados cuota > 1.30
  2. Por cada partido que pasa el filtro:
     a. Recoger estadísticas de Flashscore (muestras general, condición, H2H)
     b. Recoger estadísticas individuales de Sofascore (atacantes)
  3. Analizar con GPT-4o según la especificación
  4. Generar informe Markdown
"""

import argparse
import time
import random
import sys
from datetime import datetime, timedelta
import pytz
from playwright.sync_api import sync_playwright

import config
import betsson
import flashscore
import sofascore
import analisis
import informe


# ─── Fecha de hoy y mañana ───────────────────────────────────────────────────

def fecha_hoy() -> str:
    lima = pytz.timezone(config.TIMEZONE)
    return datetime.now(lima).strftime("%Y-%m-%d")


def fecha_manana() -> str:
    lima = pytz.timezone(config.TIMEZONE)
    return (datetime.now(lima) + timedelta(days=1)).strftime("%Y-%m-%d")


# ─── Flujo completo para un partido ──────────────────────────────────────────

def analizar_partido_completo(
    ctx_betsson,   # BrowserContext de Playwright para Betsson
    ctx_stats,     # BrowserContext de Playwright para Flashscore/Sofascore
    page_betsson,  # Page activa para Betsson (login ya hecho)
    partido: dict,
    fecha_partido: str = None,
) -> dict:
    """
    Orquesta la recolección de datos y el análisis de un partido.
    flashscore y sofascore reciben BrowserContext y gestionan sus propias páginas.
    Todo en memoria: ningún módulo escribe archivos propios.
    """
    local     = partido.get("local", "?")
    visitante = partido.get("visitante", "?")

    # Filtro estricto de eventos de eFootball, videojuegos o simulaciones virtuales
    if betsson.es_evento_virtual(partido):
        print(f"\n{'='*60}")
        print(f"  🚫 [FILTRO VIRTUAL] Saltando: {local} vs {visitante} (eFootball / videojuego / simulación)")
        print(f"{'='*60}")
        return {
            "partido":              f"{local} vs {visitante}",
            "competicion":          partido.get("liga", "?"),
            "candidatos":           [],
            "descartados":          [],
            "no_evaluables":        [],
            "mercados_disponibles": [],
            "advertencias":         ["Evento descartado: eFootball / fútbol virtual / videojuego / simulación"],
        }

    print(f"\n{'='*60}")
    print(f"  Analizando: {local} vs {visitante}")
    print(f"{'='*60}")

    # ── Paso 1: Mercados de Betsson ──────────────────────────────────────────
    print(f"  [1/4] Obteniendo mercados de Betsson...")
    mercados = betsson.obtener_mercados(page_betsson, partido)

    # Actualizar nombres a los oficiales normalizados devueltos por Betsson
    local     = partido.get("local", local)
    visitante = partido.get("visitante", visitante)

    if not mercados:
        print(f"  ⏩ Sin mercados con cuota > {config.CUOTA_MINIMA}. Saltando.")
        return {
            "partido":              f"{local} vs {visitante}",
            "competicion":          partido.get("liga", "?"),
            "candidatos":           [],
            "descartados":          [],
            "no_evaluables":        [],
            "mercados_disponibles": [],
            "advertencias":         [f"Sin mercados con cuota > {config.CUOTA_MINIMA} en Betsson"],
        }

    # ── Paso 2: Estadísticas Flashscore ─────────────────────────────────────
    print(f"  [2/4] Recopilando estadísticas Flashscore...")

    url_local = flashscore.buscar_equipo(ctx_stats, local)
    time.sleep(config.PAUSA_ENTRE_PAGINAS)

    stats_local: dict = {}
    if url_local:
        print(f"    {local}: muestra general ({config.MUESTRA_GENERAL} partidos)...")
        general_local = flashscore.obtener_resultados_equipo(
            ctx_stats, url_local, config.MUESTRA_GENERAL, nombre_equipo=local
        )
        if general_local and general_local[0].get("url_partido"):
            general_local[0]["detalle"] = flashscore.obtener_estadisticas_partido(
                ctx_stats, general_local[0]["url_partido"], local
            )

        print(f"    {local}: condición (casa)...")
        condicion_local = flashscore.obtener_resultados_equipo(
            ctx_stats, url_local, config.MUESTRA_CONDICION, condicion="casa", nombre_equipo=local
        )

        stats_local = {
            "general":   general_local,
            "condicion": condicion_local,
        }

    url_visitante = flashscore.buscar_equipo(ctx_stats, visitante)
    time.sleep(config.PAUSA_ENTRE_PAGINAS)

    stats_visitante: dict = {}
    if url_visitante:
        print(f"    {visitante}: muestra general ({config.MUESTRA_GENERAL} partidos)...")
        general_visitante = flashscore.obtener_resultados_equipo(
            ctx_stats, url_visitante, config.MUESTRA_GENERAL, nombre_equipo=visitante
        )
        if general_visitante and general_visitante[0].get("url_partido"):
            general_visitante[0]["detalle"] = flashscore.obtener_estadisticas_partido(
                ctx_stats, general_visitante[0]["url_partido"], visitante
            )

        print(f"    {visitante}: condición (fuera)...")
        condicion_visitante = flashscore.obtener_resultados_equipo(
            ctx_stats, url_visitante, config.MUESTRA_CONDICION, condicion="fuera", nombre_equipo=visitante
        )

        stats_visitante = {
            "general":   general_visitante,
            "condicion": condicion_visitante,
        }

    print(f"    H2H ({config.MUESTRA_H2H} partidos sin amistosos)...")
    h2h: list = []
    if url_local:
        url_partido_fs = flashscore.buscar_partido_h2h_url(ctx_stats, url_local, visitante)
        if url_partido_fs:
            h2h = flashscore.obtener_h2h(ctx_stats, url_partido_fs, local, visitante)
    if not h2h and stats_local.get("general"):
        h2h = flashscore.extraer_h2h_desde_muestras(stats_local["general"], visitante)
        if h2h:
            print(f"[FLASHSCORE] H2H: {len(h2h)} partidos encontrados via cruce de historial en RAM")


    # ── Paso 3: Sofascore — delanteros y métricas profundas ─────────────────
    print(f"  [3/4] Sofascore — atacantes y métricas profundas...")

    datos_sofa: dict = {}
    fecha_sofa = fecha_partido or fecha_hoy()
    url_sofa = sofascore.buscar_partido_sofascore(
        ctx_stats, local, visitante, fecha_sofa
    )
    if url_sofa:
        datos_sofa = sofascore.obtener_datos_partido(
            ctx_stats, url_sofa, local, visitante
        )

    delanteros_local     = datos_sofa.get("delanteros", {}).get("local", [])
    delanteros_visitante = datos_sofa.get("delanteros", {}).get("visitante", [])

    # Obtención de delanteros reales vía Flashscore Squad (/squad/)
    if not delanteros_local and url_local:
        delanteros_local = flashscore.obtener_delanteros_equipo(
            ctx_stats, url_local, local
        )
    elif not delanteros_local:
        url_l = flashscore.buscar_equipo(ctx_stats, local)
        if url_l:
            delanteros_local = flashscore.obtener_delanteros_equipo(ctx_stats, url_l, local)

    if not delanteros_visitante and url_visitante:
        delanteros_visitante = flashscore.obtener_delanteros_equipo(
            ctx_stats, url_visitante, visitante
        )
    elif not delanteros_visitante:
        url_v = flashscore.buscar_equipo(ctx_stats, visitante)
        if url_v:
            delanteros_visitante = flashscore.obtener_delanteros_equipo(ctx_stats, url_v, visitante)

    # Fallback adicional: si Flashscore Squad no devolvió atacantes, usar alineaciones de último partido
    if not delanteros_local and stats_local.get("general"):
        ultimo_det = stats_local["general"][0].get("detalle", {})
        alin_fs    = ultimo_det.get("alineaciones", {})
        if alin_fs:
            delanteros_local = flashscore.aislar_delanteros(
                alin_fs, ultimo_det
            ).get("local", [])

    if not delanteros_visitante and stats_visitante.get("general"):
        ultimo_det_v = stats_visitante["general"][0].get("detalle", {})
        alin_fs_v    = ultimo_det_v.get("alineaciones", {})
        if alin_fs_v:
            delanteros_visitante = flashscore.aislar_delanteros(
                alin_fs_v, ultimo_det_v
            ).get("visitante", [])

    # ── Paso 3.5: Tabla de posiciones y clasificación actual ────────────────
    tabla_posiciones = sofascore.obtener_tabla_posiciones(local, visitante, partido.get("liga", ""))

    # ── Construcción del Payload Omnisciente para el Cerebro Cuantitativo ────
    payload_omnisciente = {
        "Partido": f"{local} vs {visitante}",
        "Competicion": partido.get("liga", "Fútbol"),
        "Contexto_Clasificacion": tabla_posiciones,
        "Historial_Oficial": {
            "h2h_oficial": h2h,
            "total_h2h_oficiales": len(h2h),
            "h2h_goles_prom_total": round(sum(p.get("goles_local",0)+p.get("goles_visitante",0) for p in h2h)/max(1, len(h2h)), 2) if h2h else 0.0,
            "h2h_btts_pct": round(sum(1 for p in h2h if p.get("goles_local",0)>0 and p.get("goles_visitante",0)>0)/max(1, len(h2h)), 2) if h2h else 0.0,
            "h2h_ht_0_0_pct": round(sum(1 for p in h2h if p.get("gl_ht",1)==0 and p.get("gv_ht",1)==0)/max(1, len(h2h)), 2) if h2h else 0.0,
            "ultimos_10_oficiales_local": stats_local.get("general", []),
            "ultimos_5_local_casa": stats_local.get("condicion", []),
            "ultimos_10_oficiales_visitante": stats_visitante.get("general", []),
            "ultimos_5_visitante_fuera": stats_visitante.get("condicion", []),
        },
        "Metricas_Profundas_SofaScore": datos_sofa.get("estadisticas_periodos", {}),
        "Rendimiento_Ofensivo_Individual": {
            "delanteros_local": delanteros_local,
            "delanteros_visitante": delanteros_visitante,
        },
        "Mercado_y_Cuotas_Betsson": mercados,
    }

    # ── Paso 4: Análisis Cuantitativo Autónomo (+EV Trader Senior) ───────────
    print(f"  [4/4] Ejecutando Razonador Cuantitativo Autónomo (Trader Senior)...")
    from razonador_cuantitativo import RazonadorCuantitativo
    razonador = RazonadorCuantitativo()
    decision_quant = razonador.analizar_partido(payload_omnisciente)

    estadisticas = {
        "local":               stats_local,
        "visitante":           stats_visitante,
        "h2h":                 h2h,
        "atacantes_local":     delanteros_local,
        "atacantes_visitante": delanteros_visitante,
        "delanteros": {
            "local":     delanteros_local,
            "visitante": delanteros_visitante,
        },
        "metricas_sofa":       datos_sofa.get("metricas_equipo", {}),
        "estadisticas_periodos": datos_sofa.get("estadisticas_periodos", {}),
        "tabla_posiciones":    tabla_posiciones,
        "payload_omnisciente": payload_omnisciente,
    }

    resultado = analisis.analizar_partido(partido, estadisticas, mercados)
    resultado["mercados_disponibles"] = mercados
    resultado["decision_cuantitativa"] = decision_quant
    resultado["payload_omnisciente"] = payload_omnisciente

    # Si el Trader Cuantitativo aprueba con confianza >= 8 y no existe conflicto analítico, inyectar como Diamante VIP prioritario
    try:
        conf_val = float(str(decision_quant.get("nivel_confianza", 0)).split("/")[0].strip() or 0)
    except (ValueError, TypeError):
        conf_val = 0.0
    es_aprobado = conf_val >= 8 and decision_quant.get("decision_final") == "APROBADO"
    sin_conflicto = not decision_quant.get("conflicto_analitico", False)

    if es_aprobado and sin_conflicto:
        # Extraer periodo dinámico real desde la decisión o catálogo Betsson (sin hardcode)
        periodo_dinamico = decision_quant.get("periodo")
        if not periodo_dinamico:
            for m in mercados:
                if str(m.get("seleccion", "")).strip().lower() == str(decision_quant.get("seleccion", "")).strip().lower():
                    periodo_dinamico = m.get("periodo")
                    break
        if not periodo_dinamico:
            m_txt = (str(decision_quant.get("mercado_elegido", "")) + " " + str(decision_quant.get("seleccion", ""))).lower()
            if any(k in m_txt for k in ["1er tiempo", "1t", "ht", "primer tiempo"]):
                periodo_dinamico = "1er tiempo"
            elif any(k in m_txt for k in ["2do tiempo", "2t", "segundo tiempo"]):
                periodo_dinamico = "2do tiempo"
            else:
                periodo_dinamico = "tiempo reglamentario"

        # Extraer h2h_ratio dinámico real (sin forzar 1.0)
        if decision_quant.get("h2h_ratio") is not None:
            h2h_ratio_dinamico = float(decision_quant["h2h_ratio"])
        elif decision_quant.get("probabilidad_real_estimada") is not None:
            h2h_ratio_dinamico = float(decision_quant["probabilidad_real_estimada"])
        elif h2h:
            h2h_ratio_dinamico = round(len(h2h) / max(1, len(h2h)), 2)
        else:
            h2h_ratio_dinamico = 0.0

        tipo_liq_desc = "con regla de PUSH" if decision_quant.get("es_mercado_con_devolucion") else "estándar"
        pick_quant_vip = {
            "semaforo": "VERDE",
            "tipo": "DIAMANTE_IA",
            "mercado": decision_quant.get("mercado_elegido"),
            "seleccion": decision_quant.get("seleccion"),
            "cuota": decision_quant.get("cuota"),
            "periodo": periodo_dinamico,
            "regla_liquidacion": f"Validado por Trader Cuantitativo Senior (+EV {decision_quant.get('valor_esperado_ev')}, {tipo_liq_desc}).",
            "evidencia_favorable": [
                f"Probabilidad real estimada: {decision_quant.get('probabilidad_real_estimada'):.1%}",
                f"Probabilidad implícita de cuota: {decision_quant.get('probabilidad_implicita'):.1%}",
                f"Ventaja matemática (+EV): {decision_quant.get('valor_esperado_ev')}",
                f"Confianza IA: {decision_quant.get('nivel_confianza')}/10",
                f"Conciencia Vivencial: Estado {str(decision_quant.get('estado_consciente', 'curioso')).upper()} (Madurez {float(decision_quant.get('nivel_madurez', 1.0)):.1f}/10)",
            ],
            "evidencia_contraria": [],
            "frecuencia_general": f"+EV: {decision_quant.get('valor_esperado_ev')}",
            "frecuencia_condicion": "Validación cuantitativa profunda",
            "frecuencia_h2h": f"{len(h2h)} partidos H2H oficiales",
            "h2h_ratio": h2h_ratio_dinamico,
            "incertidumbres": [decision_quant.get("trampa_detectada")] if decision_quant.get("trampa_detectada") else [],
            "probabilidad": f"{decision_quant.get('probabilidad_real_estimada'):.1%}",
            "motivo_semaforo": decision_quant.get("explicacion_cuantitativa_detallada"),
            "cuantitativo_ia": decision_quant,
        }
        resultado["candidatos"].insert(0, pick_quant_vip)
    elif es_aprobado and not sin_conflicto:
        print(f"  ⚠️ Pick cuantitativo '{decision_quant.get('seleccion')}' degradado: no califica como Diamante por Conflicto Analítico con tendencia empírica.")

    verdes = [c for c in resultado.get("candidatos", []) if c.get("semaforo") == "VERDE"]
    print(f"  ✅ Análisis completo: {len(verdes)} candidatos verdes (incluyendo Diamante IA +EV)")
    return resultado


# ─── Punto de entrada principal ───────────────────────────────────────────────

def ejecutar_barrido(
    url_especifica: str | None = None,
    fecha: str | None = None,
    headless: bool = False,
    notificar_telegram_automatico: bool = True,
) -> tuple[list[dict], dict, str]:
    """
    Ejecuta el pipeline completo de barrido cuantitativo y genera el informe:
      1. Login y barrido en Betsson (o URL específica).
      2. Extracción de estadísticas en Flashscore y SofaScore.
      3. Análisis experto y razonador cuantitativo (+EV Poisson & Guardrails).
      4. Formación de tickets Diamante y Parlays.
      5. Generación de informe y notificación.
    Retorna: (resultados_finales, tickets_resumen, ruta_informe)
    """
    fecha_objetivo = fecha or fecha_hoy()
    print(f"\n🤖 Agente de Análisis de Fútbol")
    print(f"   Fecha objetivo: {fecha_objetivo}")
    print(f"   Hora de inicio: "
          f"{datetime.now(pytz.timezone(config.TIMEZONE)).strftime('%H:%M:%S')} Lima")
    print()

    resultados_finales: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)

        ctx_betsson = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-PE",
            viewport={"width": 1280, "height": 900},
        )

        ctx_stats = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-PE",
            viewport={"width": 1280, "height": 900},
        )

        page_betsson = ctx_betsson.new_page()

        try:
            print("🔐 Iniciando sesión en Betsson Perú...")
            betsson.login_betsson(page_betsson)

            if url_especifica:
                print(f"\n📎 Modo partido específico: {url_especifica}")
                partido_manual = {
                    "id_evento": "manual",
                    "local":     "Equipo Local",
                    "visitante": "Equipo Visitante",
                    "liga":      "Por determinar",
                    "hora":      "Por determinar",
                    "url":       url_especifica,
                }
                resultado = analizar_partido_completo(
                    ctx_betsson, ctx_stats, page_betsson, partido_manual, fecha_partido=fecha_objetivo
                )
                resultados_finales.append(resultado)

            else:
                if fecha_objetivo == fecha_hoy():
                    print("\n📅 Obteniendo partidos de HOY en Betsson...")
                    partidos = betsson.obtener_partidos_hoy(page_betsson)
                else:
                    print(f"\n📅 Obteniendo partidos para {fecha_objetivo} en Betsson...")
                    partidos = betsson.obtener_partidos_manana(page_betsson)

                # ── Filtrado estricto de eFootball / videojuegos / simulaciones ──
                partidos_reales = [p for p in partidos if not betsson.es_evento_virtual(p)]
                descartados_virtuales = len(partidos) - len(partidos_reales)
                if descartados_virtuales > 0:
                    print(f"🚫 [FILTRO VIRTUAL] Se omitieron {descartados_virtuales} encuentros de eFootball / simulaciones / videojuegos.")
                partidos = partidos_reales

                if not partidos:
                    print("❌ No se encontraron partidos oficiales reales para analizar.")
                    return [], {}, ""

                print(f"\n📋 {len(partidos)} partidos de fútbol real encontrados. "
                      f"Pre-filtro y análisis en curso...\n")

                for i, partido in enumerate(partidos, 1):
                    if betsson.es_evento_virtual(partido):
                        print(f"\n[{i}/{len(partidos)}] 🚫 Omitiendo evento virtual: {partido.get('local')} vs {partido.get('visitante')}")
                        continue

                    print(f"\n[{i}/{len(partidos)}] "
                          f"{partido.get('local')} vs {partido.get('visitante')}")
                    try:
                        resultado = analizar_partido_completo(
                            ctx_betsson, ctx_stats, page_betsson, partido, fecha_partido=fecha_objetivo
                        )
                        resultados_finales.append(resultado)
                        pausa_dinamica = round(random.uniform(1.2, 2.5), 2)
                        print(f"  ⏳ Pausa dinámica anti-baneo: {pausa_dinamica}s...")
                        time.sleep(pausa_dinamica)
                    except Exception as e:
                        print(f"  ❌ Error: {e}")
                        resultados_finales.append({
                            "partido": (f"{partido.get('local','?')} vs "
                                        f"{partido.get('visitante','?')}"),
                            "candidatos": [],
                            "advertencias": [f"Error: {e}"],
                        })

        finally:
            page_betsson.close()
            ctx_betsson.close()
            ctx_stats.close()
            browser.close()

    print(f"\n📄 Generando informe final...")
    ruta_informe = informe.generar_informe(resultados_finales, fecha_objetivo)

    from gestor_tickets import GestorTickets
    gt = GestorTickets()
    tickets_resumen = gt.procesar_resultados(resultados_finales)

    total_verdes    = sum(
        len([c for c in r.get("candidatos", []) if c.get("semaforo") == "VERDE"])
        for r in resultados_finales
    )
    total_amarillos = sum(
        len([c for c in r.get("candidatos", []) if c.get("semaforo") == "AMARILLO"])
        for r in resultados_finales
    )

    print(f"\n{'='*60}")
    print(f"  ANÁLISIS COMPLETADO")
    print(f"  Partidos procesados:       {len(resultados_finales)}")
    print(f"  Candidatos 🟢 Verdes:     {total_verdes}")
    print(f"  Pendientes 🟡 Amarillos:  {total_amarillos}")
    print(f"  💎 Apuestas Diamante:     {len(tickets_resumen.get('simples_diamante', []))}")
    print(f"  ⚡ Parlays Seguros (x2):  {len(tickets_resumen.get('parlays_duplas', []))}")
    print(f"  Informe: {ruta_informe}")
    print(f"{'='*60}\n")

    # ── Notificación automática a Telegram ───────────────────────────────────
    if notificar_telegram_automatico:
        cant_diamantes = len(tickets_resumen.get("simples_diamante", []))
        cant_parlays   = len(tickets_resumen.get("parlays_duplas", []))

        if cant_diamantes > 0 or cant_parlays > 0:
            print("📲 Despachando tickets VIP a Telegram...")
            import notificador_telegram
            notificador_telegram.enviar_informe_tickets(ruta_informe, fecha_objetivo)
        else:
            print("ℹ️ Sin tickets VIP válidos hoy; se omite notificación de Telegram.")

    return resultados_finales, tickets_resumen, ruta_informe


def main():
    parser = argparse.ArgumentParser(
        description="Agente de análisis de apuestas deportivas"
    )
    parser.add_argument(
        "--url", type=str, default=None,
        help="URL de un partido específico en Betsson para análisis puntual"
    )
    parser.add_argument(
        "--fecha", type=str, default=None,
        help="Fecha YYYY-MM-DD. Default: hoy"
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Ejecutar navegador en modo headless"
    )
    args = parser.parse_args()

    ejecutar_barrido(
        url_especifica=args.url,
        fecha=args.fecha,
        headless=args.headless,
        notificar_telegram_automatico=True,
    )


if __name__ == "__main__":
    main()
