"""
test_cerebro.py
───────────────────────────────────────────────────────────────────────────────
Prueba Unitaria Completa (Dry-Run) del Agente Cuantitativo Autónomo.

Simula la inyección del partido "Torino vs Udinese" entregando el Payload
Omnisciente al Razonador Cuantitativo (Trader Deportivo Senior):
  1. Contexto de clasificación y factor urgencia.
  2. Historial oficial H2H y muestras con marcadores HT/FT (sin amistosos).
  3. Métricas profundas por periodos (xG 1T bajo en 0.70, 0 big chances).
  4. Rendimiento individual de delanteros titulares y tiros a puerta.
  5. Abanico completo de cuotas de Betsson.
  6. Sentimiento del público con detección de Public Trap.

Demuestra el proceso de pensamiento de la IA, el cálculo de Implied vs True
Probability, la maximización de +EV y la libertad de escanear cualquier mercado.
───────────────────────────────────────────────────────────────────────────────
"""

import sys
import json
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from razonador_cuantitativo import RazonadorCuantitativo


def print_banner(titulo: str):
    print("\n" + "=" * 80)
    print(f"  🧠 {titulo.upper()}")
    print("=" * 80)


def ejecutar_dry_run():
    print_banner("1. PREPARACIÓN DEL PAYLOAD OMNISCIENTE (TORINO VS UDINESE)")

    payload_torino_udinese = {
        "Partido": "Torino vs Udinese",
        "Competicion": "Italia Serie A",
        "Fecha": "2026-10-12",
        "Contexto_Clasificacion": {
            "local": {
                "equipo": "Torino",
                "posicion": 9,
                "puntos": 38,
                "pj": 26,
                "dg": +2,
                "forma": "D-L-L-W-W",
                "rendimiento_pct": "48.7%",
                "urgencia": "Mitad de tabla con aspiración matemática a Conference League (-4 pts de zona europea)."
            },
            "visitante": {
                "equipo": "Udinese",
                "posicion": 11,
                "puntos": 35,
                "pj": 26,
                "dg": -3,
                "forma": "W-L-L-W-D",
                "rendimiento_pct": "44.8%",
                "urgencia": "Tranquilidad relativa en mitad de tabla (+9 pts sobre zona de descenso)."
            },
            "disparidad_puntos": 3,
            "disparidad_dg": 5,
            "diagnostico": "Equipos de mitad de tabla con rendimientos muy parejos (separados por 3 puntos). Duelo de alta fricción táctica."
        },
        "Historial_Oficial": {
            "h2h_oficial": [
                {"local": "Udinese", "visitante": "Torino", "competicion": "Serie A", "ft": "1 - 1", "ht": "0 - 0", "gl_ft": 1, "gv_ft": 1, "gl_ht": 0, "gv_ht": 0},
                {"local": "Torino", "visitante": "Udinese", "competicion": "Serie A", "ft": "1 - 0", "ht": "0 - 0", "gl_ft": 1, "gv_ft": 0, "gl_ht": 0, "gv_ht": 0},
                {"local": "Udinese", "visitante": "Torino", "competicion": "Serie A", "ft": "0 - 2", "ht": "0 - 1", "gl_ft": 0, "gv_ft": 2, "gl_ht": 0, "gv_ht": 1},
                {"local": "Torino", "visitante": "Udinese", "competicion": "Serie A", "ft": "2 - 1", "ht": "1 - 1", "gl_ft": 2, "gv_ft": 1, "gl_ht": 1, "gv_ht": 1},
            ],
            "total_h2h_oficiales": 4,
            "amistosos_cf_descartados": 1, # Torino vs Udinese (CF) 1-0 descartado
            "h2h_goles_prom_total": 2.0,
            "h2h_btts_pct": 0.50,
            "h2h_ht_0_0_pct": 0.50,
            "ultimos_10_oficiales_local": [
                {"rival": "Lazio", "goles_favor": 0, "goles_contra": 2, "es_local": False, "competicion": "Serie A"},
                {"rival": "Monza", "goles_favor": 1, "goles_contra": 1, "es_local": True, "competicion": "Serie A"},
                {"rival": "Napoli", "goles_favor": 0, "goles_contra": 1, "es_local": True, "competicion": "Serie A"},
            ],
            "ultimos_5_local_casa": [
                {"rival": "Monza", "goles_favor": 1, "goles_contra": 1, "es_local": True, "competicion": "Serie A"},
                {"rival": "Napoli", "goles_favor": 0, "goles_contra": 1, "es_local": True, "competicion": "Serie A"},
            ],
            "ultimos_10_oficiales_visitante": [
                {"rival": "Empoli", "goles_favor": 1, "goles_contra": 1, "es_local": False, "competicion": "Serie A"},
                {"rival": "Roma", "goles_favor": 0, "goles_contra": 3, "es_local": False, "competicion": "Serie A"},
                {"rival": "Lecce", "goles_favor": 2, "goles_contra": 1, "es_local": True, "competicion": "Serie A"},
            ],
            "ultimos_5_visitante_fuera": [
                {"rival": "Empoli", "goles_favor": 1, "goles_contra": 1, "es_local": False, "competicion": "Serie A"},
                {"rival": "Roma", "goles_favor": 0, "goles_contra": 3, "es_local": False, "competicion": "Serie A"},
            ],
            "promedios_goles": {
                "local_general_gf_prom": 0.8,
                "local_general_gc_prom": 1.4,
                "local_casa_gf_prom": 0.0,
                "local_casa_gc_prom": 1.5,
                "visitante_general_gf_prom": 1.5,
                "visitante_general_gc_prom": 2.0,
                "visitante_fuera_gf_prom": 1.5,
                "visitante_fuera_gc_prom": 2.5
            }
        },
        "Metricas_Profundas_SofaScore": {
            "overall": {
                "xg": {"local": 1.15, "visitante": 1.05, "total": 2.20},
                "posesion": {"local": "52%", "visitante": "48%"},
                "big_chances": {"local": 2, "visitante": 1, "total": 3},
                "total_shots": {"local": 13, "visitante": 11, "total": 24},
                "shots_on_target": {"local": 4, "visitante": 3, "total": 7},
                "corners": {"local": 5, "visitante": 4, "total": 9}
            },
            "1st_half": {
                "xg": {"local": 0.38, "visitante": 0.32, "total": 0.70},  # xG combinado 1T extremadamente bajo
                "posesion": {"local": "51%", "visitante": "49%"},
                "big_chances": {"local": 0, "visitante": 0, "total": 0},   # Cero grandes ocasiones en 1T
                "total_shots": {"local": 5, "visitante": 4, "total": 9},
                "shots_on_target": {"local": 1, "visitante": 1, "total": 2}, # Solo 1 tiro a puerta por lado
                "corners": {"local": 2, "visitante": 2, "total": 4}
            },
            "2nd_half": {
                "xg": {"local": 0.77, "visitante": 0.73, "total": 1.50},
                "posesion": {"local": "53%", "visitante": "47%"},
                "big_chances": {"local": 2, "visitante": 1, "total": 3},
                "total_shots": {"local": 8, "visitante": 7, "total": 15},
                "shots_on_target": {"local": 3, "visitante": 2, "total": 5},
                "corners": {"local": 3, "visitante": 2, "total": 5}
            }
        },
        "Rendimiento_Ofensivo_Individual": {
            "delanteros_local": [
                {
                    "nombre": "Giovanni Simeone",
                    "posicion": "CF",
                    "rol_ofensivo": "Striker",
                    "titular": True,
                    "minutos": 78,
                    "total_shots": 3,
                    "shots_on_target": 1,
                    "xg_jugador": 0.45,
                    "goles": 0
                },
                {
                    "nombre": "Nicolò Zaniolo",
                    "posicion": "CAM",
                    "rol_ofensivo": "Attacking Midfielder",
                    "titular": True,
                    "minutos": 82,
                    "total_shots": 2,
                    "shots_on_target": 1,
                    "xg_jugador": 0.28,
                    "goles": 0
                }
            ],
            "delanteros_visitante": [
                {
                    "nombre": "Adam Buksa",
                    "posicion": "CF",
                    "rol_ofensivo": "Striker",
                    "titular": True,
                    "minutos": 75,
                    "total_shots": 3,
                    "shots_on_target": 2,
                    "xg_jugador": 0.52,
                    "goles": 0
                },
                {
                    "nombre": "Florian Thauvin",
                    "posicion": "FW",
                    "rol_ofensivo": "Forward",
                    "titular": True,
                    "minutos": 80,
                    "total_shots": 2,
                    "shots_on_target": 1,
                    "xg_jugador": 0.31,
                    "goles": 0
                }
            ]
        },
        "Mercado_y_Cuotas_Betsson": [
            {"familia": "1er tiempo - Total de goles", "seleccion": "Menos de 0.5", "cuota": 2.45, "periodo": "1er tiempo"},
            {"familia": "1er tiempo - Total de goles", "seleccion": "Más de 1.5", "cuota": 2.85, "periodo": "1er tiempo"},
            {"familia": "1er tiempo - Total de goles", "seleccion": "Menos de 1.5", "cuota": 1.38, "periodo": "1er tiempo"},
            {"familia": "Total de goles", "seleccion": "Más de 1.5", "cuota": 1.38, "periodo": "tiempo reglamentario"},
            {"familia": "Línea de gol asiática", "seleccion": "Más de 2.0", "cuota": 1.72, "periodo": "tiempo reglamentario"},
            {"familia": "Línea de gol asiática", "seleccion": "Menos de 3.0", "cuota": 1.42, "periodo": "tiempo reglamentario"},
            {"familia": "Hándicap Asiático", "seleccion": "Udinese (+1.0)", "cuota": 1.34, "periodo": "tiempo reglamentario"},
            {"familia": "Hándicap Asiático", "seleccion": "Torino (0.0)", "cuota": 1.62, "periodo": "tiempo reglamentario"},
            {"familia": "Ganador del partido (1X2)", "seleccion": "Torino", "cuota": 2.15, "periodo": "tiempo reglamentario"},
            {"familia": "Ganador del partido (1X2)", "seleccion": "Empate", "cuota": 3.10, "periodo": "tiempo reglamentario"},
            {"familia": "Ganador del partido (1X2)", "seleccion": "Udinese", "cuota": 3.60, "periodo": "tiempo reglamentario"},
            {"familia": "Ambos equipos anotarán", "seleccion": "Sí", "cuota": 1.95, "periodo": "tiempo reglamentario"},
            {"familia": "Ambos equipos anotarán", "seleccion": "No", "cuota": 1.80, "periodo": "tiempo reglamentario"},
            {"familia": "Doble Oportunidad", "seleccion": "Torino o Empate (1X)", "cuota": 1.34, "periodo": "tiempo reglamentario"},
        ]
    }

    print("✅ Payload omnisciente construido:")
    print(f"  • Partido: {payload_torino_udinese['Partido']}")
    print(f"  • Mercados Betsson disponibles: {len(payload_torino_udinese['Mercado_y_Cuotas_Betsson'])}")
    print(f"  • xG combinado 1er Tiempo: {payload_torino_udinese['Metricas_Profundas_SofaScore']['1st_half']['xg']['total']}")
    print(f"  • Big Chances 1T: {payload_torino_udinese['Metricas_Profundas_SofaScore']['1st_half']['big_chances']['total']}")

    print_banner("2. INYECCIÓN AL CEREBRO ANALÍTICO (RAZONADOR CUANTITATIVO)")
    cerebro = RazonadorCuantitativo()
    resultado = cerebro.analizar_partido(payload_torino_udinese)

    print_banner("3. RESULTADO DEL PROCESO DE PENSAMIENTO (+EV TRADER SENIOR & GUARDRAILS)")
    print(f"📌 Partido:                   {resultado.get('partido')}")
    print(f"🎯 Mercado Elegido:           {resultado.get('mercado_elegido')}")
    print(f"🎯 Selección:                 {resultado.get('seleccion')}")
    print(f"💰 Cuota Ofrecida en Betsson: {resultado.get('cuota')}")
    print(f"📉 Implied Probability:       {resultado.get('probabilidad_implicita'):.1%}")
    print(f"📈 True Probability Estimada: {resultado.get('probabilidad_real_estimada'):.1%}")
    print(f"🚀 Valor Esperado (+EV):      {resultado.get('valor_esperado_ev')}")
    print(f"📐 Kelly f* (Ventaja Capital): {resultado.get('kelly_f_star', 0.0):.1%}")
    print(f"💵 Stake Sugerido:            {resultado.get('stake_sugerido')}")
    print(f"🛡️ Guardrails Superados:      {resultado.get('guardrails_superados')}")
    print(f"⭐ Nivel de Confianza:        {resultado.get('nivel_confianza')}/10")
    print(f"🏁 Decisión Final:            {resultado.get('decision_final')}")
    print(f"🤖 Motor Utilizado:           {resultado.get('motor')}")

    print("\n📝 EXPLICACIÓN CUANTITATIVA DETALLADA:")
    print(f"   \"{resultado.get('explicacion_cuantitativa_detallada')}\"")

    print("\n📊 OTROS MERCADOS ESCANEADOS (+EV RANKING):")
    for m in resultado.get("mercados_evaluados", []):
        p_imp = m.get('probabilidad_implicita')
        p_tre = m.get('probabilidad_real_estimada')
        str_imp = f"{float(p_imp):.1%}" if p_imp is not None else "N/D"
        str_tre = f"{float(p_tre):.1%}" if p_tre is not None else "N/D"
        print(f"   • {m.get('mercado')} [{m.get('seleccion')}] @ {m.get('cuota')} | Implied: {str_imp} | True: {str_tre} | EV: {m.get('valor_esperado_ev')}")

    print_banner("4. VALIDACIÓN DE CRITERIOS DE REQUERIMIENTOS Y GUARDRAILS")
    assert resultado.get("mercado_elegido") is not None, "Debe elegir un mercado"
    assert resultado.get("cuota") is not None, "Debe tener cuota"
    assert resultado.get("probabilidad_implicita") is not None, "Debe calcular probabilidad implícita"
    assert resultado.get("probabilidad_real_estimada") is not None, "Debe calcular probabilidad real"
    assert resultado.get("valor_esperado_ev") is not None, "Debe calcular +EV"
    assert resultado.get("ev_matematico_exacto", 0.0) >= 0.05, "Debe cumplir estrictamente el margen de seguridad (+EV >= +5.0%)"
    assert resultado.get("guardrails_superados") is True, "Debe superar todos los guardrails matemáticos"
    assert resultado.get("kelly_f_star", 0.0) > 0.0, "Kelly f* debe ser positivo para apostar capital"
    assert "Quarter-Kelly" in str(resultado.get("stake_sugerido")), "El stake debe calcularse por Quarter-Kelly f*/4"
    assert resultado.get("nivel_confianza") >= 8, "Debe tener alta confianza si aprueba"
    assert resultado.get("decision_final") == "APROBADO", "Debe emitir decisión de APROBADO"

    print("✅ TEST COMPLETADO CON ÉXITO: El Agente Cuantitativo Autónomo razona, supera los guardrails matemáticos (+EV >= +5%) y dimensiona el stake por Quarter-Kelly.")


if __name__ == "__main__":
    ejecutar_dry_run()
