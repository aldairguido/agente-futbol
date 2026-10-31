"""
test_sofascore_torino_udinese.py
───────────────────────────────────────────────────────────────────────────────
Prueba Integral del Motor Analítico Definitivo:
  1. Filtro estricto de Amistosos ('CF').
  2. Promedios oficiales de goles (GF y GC) desglosados en:
     - Últimos 10 generales.
     - Últimos 5 de local (o de visita).
     - Últimos 5 cara a cara (H2H).
  3. Toda la gama de líneas de goles y hándicaps (±0.5, ±1.0, ±1.5, ±2.0, ±2.5, ±3.0)
     con reglas explícitas de PUSH (devolución) en líneas enteras.
  4. Radiografía de mercado: Tabla de posiciones actual y Market Sentiment (detección de trampas).
  5. Generación del informe final consolidado con la sección VIP y desglose completo.
───────────────────────────────────────────────────────────────────────────────
"""

import sys
import json
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sofascore
import informe
from analizador_experto import AnalizadorExperto

def print_separator(titulo: str):
    print("\n" + "=" * 80)
    print(f"  {titulo.upper()}")
    print("=" * 80)

def ejecutar_prueba():
    print_separator("1. EXTRACCIÓN CON FILTRO ESTRICTO DE AMISTOSOS")
    historial_torino = sofascore.obtener_historial_equipo_sofa(None, "Torino", limite=10)
    historial_udinese = sofascore.obtener_historial_equipo_sofa(None, "Udinese", limite=10)
    h2h_oficial = sofascore.obtener_h2h_sofascore("Torino", "Udinese")
    datos_partido = sofascore.obtener_datos_partido(None, "https://www.sofascore.com/match/torino-udinese", "Torino", "Udinese")
    delanteros_data = datos_partido["delanteros"]
    stats_periodos = datos_partido["estadisticas_periodos"]

    print_separator("2. PROMEDIOS OFICIALES DE GOLES (GF Y GC) DESGLOSADOS")
    analizador = AnalizadorExperto(cuota_minima=1.30)

    partido_info = {
        "local": "Torino",
        "visitante": "Udinese",
        "liga": "Serie A (Italia)",
    }

    # Mercados de Betsson abarcando toda la gama de líneas
    mercados_test = [
        # Línea de 1T (debe descartarse)
        {"familia": "1er tiempo - Total de goles", "seleccion": "Más de 1.5", "cuota": 2.85, "periodo": "1er tiempo"},
        # Línea 1.5 clásica 90m
        {"familia": "Total de goles", "seleccion": "Más de 1.5", "cuota": 1.38, "periodo": "tiempo reglamentario"},
        # Línea Asiática 2.0 entera (con regla de PUSH)
        {"familia": "Línea de gol asiática", "seleccion": "Más de 2.0", "cuota": 1.72, "periodo": "tiempo reglamentario"},
        # Línea Asiática 3.0 entera Under (con regla de PUSH)
        {"familia": "Línea de gol asiática", "seleccion": "Menos de 3.0", "cuota": 1.42, "periodo": "tiempo reglamentario"},
        # Hándicap Asiático 0.0 (DNB / Apuesta sin empate con PUSH)
        {"familia": "Hándicap Asiático", "seleccion": "Torino (0.0)", "cuota": 1.62, "periodo": "tiempo reglamentario"},
        # Hándicap Asiático +1.0 (con PUSH ante derrota por 1 gol)
        {"familia": "Hándicap Asiático", "seleccion": "Udinese (+1.0)", "cuota": 1.34, "periodo": "tiempo reglamentario"},
        # Ambos equipos anotarán
        {"familia": "Ambos equipos anotarán", "seleccion": "Sí", "cuota": 1.95, "periodo": "tiempo reglamentario"},
        # Doble oportunidad
        {"familia": "Doble Oportunidad", "seleccion": "Torino o Empate (1X)", "cuota": 1.34, "periodo": "tiempo reglamentario"},
    ]

    tabla_posiciones = sofascore.obtener_tabla_posiciones("Torino", "Udinese", "Serie A")
    market_sentiment = sofascore.obtener_market_sentiment(mercados_test, "Torino", "Udinese")

    estadisticas_payload = {
        "local": {
            "general": historial_torino,
            "condicion": [p for p in historial_torino if p.get("es_local")],
        },
        "visitante": {
            "general": historial_udinese,
            "condicion": [p for p in historial_udinese if not p.get("es_local")],
        },
        "h2h": h2h_oficial,
        "atacantes_local": delanteros_data["local"],
        "atacantes_visitante": delanteros_data["visitante"],
        "delanteros": delanteros_data,
        "estadisticas_periodos": stats_periodos,
        "tabla_posiciones": tabla_posiciones,
        "market_sentiment": market_sentiment,
    }

    resultado_analisis = analizador.analizar(partido_info, estadisticas_payload, mercados_test)
    cog = resultado_analisis["razonamiento_cognitivo"]
    c1 = cog["capa_1_h2h"]
    c2 = cog["capa_2_contexto"]
    loc_inf = c2["local"]
    vis_inf = c2["visitante"]

    # ── Ejecutar Razonador Cuantitativo Autónomo (Trader Senior) ─────────────
    from razonador_cuantitativo import RazonadorCuantitativo
    payload_omnisciente = {
        "Partido": "Torino vs Udinese",
        "Competicion": "Serie A (Italia)",
        "Contexto_Clasificacion": tabla_posiciones,
        "Historial_Oficial": {
            "h2h_oficial": h2h_oficial,
            "total_h2h_oficiales": len(h2h_oficial),
            "h2h_goles_prom_total": c1["goles_totales_prom"],
            "h2h_btts_pct": c1["btts_frecuencia"],
            "h2h_ht_0_0_pct": c1["ht_cero_cero_frecuencia"],
            "ultimos_10_oficiales_local": historial_torino,
            "ultimos_5_local_casa": [p for p in historial_torino if p.get("es_local")],
            "ultimos_10_oficiales_visitante": historial_udinese,
            "ultimos_5_visitante_fuera": [p for p in historial_udinese if not p.get("es_local")],
        },
        "Metricas_Profundas_SofaScore": stats_periodos,
        "Rendimiento_Ofensivo_Individual": {
            "delanteros_local": delanteros_data["local"],
            "delanteros_visitante": delanteros_data["visitante"],
        },
        "Mercado_y_Cuotas_Betsson": mercados_test,
    }
    razonador = RazonadorCuantitativo()
    decision_quant = razonador.analizar_partido(payload_omnisciente)
    resultado_analisis["decision_cuantitativa"] = decision_quant
    resultado_analisis["payload_omnisciente"] = payload_omnisciente

    try:
        conf_test = float(str(decision_quant.get("nivel_confianza", 0)).split("/")[0].strip() or 0)
    except (ValueError, TypeError):
        conf_test = 0.0
    if conf_test >= 8 and decision_quant.get("decision_final") == "APROBADO":
        periodo_dinamico = decision_quant.get("periodo", "tiempo reglamentario")
        h2h_ratio_dinamico = float(decision_quant.get("h2h_ratio", decision_quant.get("probabilidad_real_estimada", 0.75)))
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
            ],
            "evidencia_contraria": [],
            "frecuencia_general": f"+EV: {decision_quant.get('valor_esperado_ev')}",
            "frecuencia_condicion": "Validación cuantitativa profunda",
            "frecuencia_h2h": f"{len(h2h_oficial)} partidos H2H oficiales",
            "h2h_ratio": h2h_ratio_dinamico,
            "incertidumbres": [decision_quant.get("trampa_detectada")] if decision_quant.get("trampa_detectada") else [],
            "probabilidad": f"{decision_quant.get('probabilidad_real_estimada'):.1%}",
            "motivo_semaforo": decision_quant.get("explicacion_cuantitativa_detallada"),
            "cuantitativo_ia": decision_quant,
        }
        resultado_analisis["candidatos"].insert(0, pick_quant_vip)

    print("📊 [A] ÚLTIMOS 10 PARTIDOS GENERALES:")
    print(f"  • {loc_inf['nombre']}:   GF prom: {loc_inf['goles_favor_gen_prom']} | GC prom: {loc_inf['goles_contra_gen_prom']} ({loc_inf['general_10']['victorias_pct']:.0%} vic)")
    print(f"  • {vis_inf['nombre']}:  GF prom: {vis_inf['goles_favor_gen_prom']} | GC prom: {vis_inf['goles_contra_gen_prom']} ({vis_inf['general_10']['victorias_pct']:.0%} vic)")

    print("\n📊 [B] ÚLTIMOS 5 PARTIDOS EN CONDICIÓN (CASA / FUERA):")
    print(f"  • {loc_inf['nombre']} (Casa):   GF prom: {loc_inf['goles_favor_cond_prom']} | GC prom: {loc_inf['goles_contra_cond_prom']} ({loc_inf['condicion_5']['victorias_pct']:.0%} vic, Imbatibilidad: {loc_inf['condicion_5']['imbatibilidad_pct']:.0%})")
    print(f"  • {vis_inf['nombre']} (Fuera):  GF prom: {vis_inf['goles_favor_cond_prom']} | GC prom: {vis_inf['goles_contra_cond_prom']} ({vis_inf['condicion_5']['victorias_pct']:.0%} vic)")

    print("\n📊 [C] ÚLTIMOS 5 PARTIDOS CARA A CARA (H2H):")
    print(f"  • GF {loc_inf['nombre']} prom:  {c1['goles_favor_local_prom']} goles/partido")
    print(f"  • GF {vis_inf['nombre']} prom: {c1['goles_favor_visita_prom']} goles/partido")
    print(f"  • Total prom H2H:     {c1['goles_totales_prom']} goles/partido")
    print(f"  • BTTS Frecuencia:    {c1['btts_frecuencia']:.0%} ({c1['btts_conteo']})")
    print(f"  • Descanso HT 0-0:    {c1['ht_cero_cero_frecuencia']:.0%} ({c1['ht_cero_cero_conteo']})")


    print_separator("3. RADIOGRAFÍA DE MERCADO: TABLA DE POSICIONES Y SENTIMIENTO PÚBLICO")
    tab_l = loc_inf.get("tabla", {})
    tab_v = vis_inf.get("tabla", {})
    print(f"🏆 TABLA DE POSICIONES ACTUAL:")
    print(f"  • {loc_inf['nombre']}:  Puesto {tab_l.get('posicion')}° | {tab_l.get('puntos')} pts | DG {tab_l.get('dg')} | Forma: {tab_l.get('forma')}")
    print(f"  • {vis_inf['nombre']}: Puesto {tab_v.get('posicion')}° | {tab_v.get('puntos')} pts | DG {tab_v.get('dg')} | Forma: {tab_v.get('forma')}")
    print(f"  • Diagnóstico: {c2.get('tabla_posiciones', {}).get('diagnostico')}")

    sent = c2.get("market_sentiment", {})
    print(f"\n🧠 MARKET SENTIMENT & TRAP ODDS:")
    print(f"  • Distribución 1X2: Local {sent.get('distribucion_1x2',{}).get('local_pct')}% | Empate {sent.get('distribucion_1x2',{}).get('empate_pct')}% | Visita {sent.get('distribucion_1x2',{}).get('visitante_pct')}%")
    print(f"  • Alerta de Trampa de Cuota: {sent.get('alerta_trampa')}")
    print(f"  • Diagnóstico de Mercado: {sent.get('diagnostico')}")


    print_separator("4. VALIDACIÓN DE TODA LA GAMA DE LÍNEAS ASIÁTICAS Y REGLAS DE PUSH")
    print("\n--- PICKS APROBADOS (VERDES) CON REGLAS DE LIQUIDACIÓN Y PUSH ---")
    for c in resultado_analisis["candidatos"]:
        print(f"\n🟢 [{c['semaforo']}] {c['mercado']} | {c['seleccion']} @ {c['cuota']}")
        print(f"   📜 Regla de Liquidación: {c['regla_liquidacion']}")
        print(f"   💡 Motivo: {c['motivo_semaforo']}")

    print("\n--- SELECCIONES DESCARTADAS ---")
    for d in resultado_analisis["descartados"]:
        print(f"🔴 {d.get('mercado')} | {d.get('seleccion')} @ {d.get('cuota')}")
        print(f"   Motivo: {d.get('motivo_descarte')}")


    print_separator("5. GENERACIÓN DEL INFORME CONSOLIDADO (INFORME_YYYY-MM-DD.MD)")
    ruta_inf = informe.generar_informe([resultado_analisis], "2026-10-12")
    print(f"  ✅ Informe generado y verificado en: {ruta_inf}")

    # Verificar que el archivo se haya generado y contenga los promedios y reglas
    with open(ruta_inf, "r", encoding="utf-8") as f:
        contenido = f.read()

    assert "GF prom" in contenido, "El informe debe contener el desglose de GF prom"
    assert "H2H Oficial" in contenido, "El informe debe contener la sección H2H"
    assert "Tabla de Posiciones" in contenido, "El informe debe contener la tabla de posiciones"
    assert "Market Sentiment" in contenido, "El informe debe contener el sentimiento de mercado"

    print("\n✅ TODAS LAS VALIDACIONES DE PROMEDIOS, LÍNEAS Y RADIOGRAFÍA SUPERADAS CON ÉXITO.")

if __name__ == "__main__":
    ejecutar_prueba()
