import sys
from razonador_cuantitativo import RazonadorCuantitativo

def test_motor():
    razonador = RazonadorCuantitativo()
    # Forzar modo contingencia matemático
    razonador._gemini_desactivado = True

    # CASO 1: Datos vacíos -> Debe descartar
    partido_vacio = {
        "partido": {"local": "Equipo A", "visitante": "Equipo B", "liga": "Liga Test"},
        "Historial_Oficial": {},
        "mercados_betsson": [
            {"mercado": "1er tiempo - Total de goles", "seleccion": "Menos de 0.5", "cuota": 3.20}
        ]
    }
    res_vacio = razonador.analizar_partido(partido_vacio)
    print("\n--- TEST CASO 1: DATOS VACÍOS ---")
    confianza = res_vacio.get("nivel_confianza", res_vacio.get("confianza_modelo", 0))
    explicacion = res_vacio.get("explicacion_cuantitativa_detallada") or res_vacio.get("explicacion")
    print("Confianza:", confianza)
    print("Explicación:", explicacion)
    assert res_vacio.get("decision_final") == "DESCARTADO", "Error: No descartó datos vacíos"
    assert confianza == 0, f"Error: Confianza no es 0, es {confianza}"

    # CASO 2: Perfil Ofensivo (ej. Bayern vs Dortmund)
    # Goles frecuentes: local promedia 2.4 GF, visitante 1.8 GF
    historial_ofensivo = {
        "ultimos_5_local_casa": [
            {"goles_favor": 3, "goles_contra": 1},
            {"goles_favor": 2, "goles_contra": 0},
            {"goles_favor": 4, "goles_contra": 2},
            {"goles_favor": 1, "goles_contra": 1},
            {"goles_favor": 2, "goles_contra": 1},
        ],
        "ultimos_5_visitante_fuera": [
            {"goles_favor": 2, "goles_contra": 2},
            {"goles_favor": 1, "goles_contra": 1},
            {"goles_favor": 3, "goles_contra": 1},
            {"goles_favor": 0, "goles_contra": 2},
            {"goles_favor": 2, "goles_contra": 1},
        ],
        "h2h_oficial": [
            {"goles_favor": 3, "goles_contra": 2},
            {"goles_favor": 2, "goles_contra": 2},
            {"goles_favor": 1, "goles_contra": 3},
        ]
    }
    mercados_ofensivos = [
        {"mercado": "1er tiempo - Total de goles", "seleccion": "Menos de 0.5", "cuota": 3.40},
        {"mercado": "Total de goles", "seleccion": "Más de 1.5", "cuota": 1.35},
        {"mercado": "Ambos equipos anotarán", "seleccion": "Sí", "cuota": 1.65},
        {"mercado": "Ganador del partido", "seleccion": "Equipo Ofensivo Local", "cuota": 1.70},
    ]
    partido_ofensivo = {
        "partido": {"local": "Equipo Ofensivo Local", "visitante": "Equipo Ofensivo Vis", "liga": "Liga Test"},
        "Historial_Oficial": historial_ofensivo,
        "mercados_betsson": mercados_ofensivos
    }
    res_ofensivo = razonador.analizar_partido(partido_ofensivo)
    print("\n--- TEST CASO 2: PERFIL OFENSIVO ---")
    print("Decisión:", res_ofensivo.get("decision_final"))
    print("Mercado:", res_ofensivo.get("mercado_elegido"))
    print("Selección:", res_ofensivo.get("seleccion"))
    print("Cuota:", res_ofensivo.get("cuota"))
    print("True Prob:", res_ofensivo.get("probabilidad_real_estimada"))
    print("EV:", res_ofensivo.get("valor_esperado_ev"))
    print("Stake Kelly:", res_ofensivo.get("stake_sugerido"))
    print("Explicación:", res_ofensivo.get("explicacion_cuantitativa_detallada"))
    
    assert res_ofensivo.get("seleccion") != "Menos de 0.5", "CRÍTICO: Seleccionó Menos de 0.5 en partido altamente ofensivo!"

    # CASO 3: Perfil Ofensivo con Cuota Ineficiente (+EV > 5%)
    mercados_con_valor = [
        {"mercado": "1er tiempo - Total de goles", "seleccion": "Menos de 0.5", "cuota": 4.50}, # Atractiva pero ofensivo -> guardrail debe vetarlo
        {"mercado": "Total de goles", "seleccion": "Más de 1.5", "cuota": 1.65}, # Prob real ~80% -> EV ~ +32%
    ]
    partido_con_valor = {
        "partido": {"local": "Equipo A", "visitante": "Equipo B", "liga": "Liga Test"},
        "Historial_Oficial": historial_ofensivo,
        "mercados_betsson": mercados_con_valor
    }
    res_valor = razonador.analizar_partido(partido_con_valor)
    print("\n--- TEST CASO 3: PERFIL OFENSIVO CON VALOR POSITIVO ---")
    print("Decisión:", res_valor.get("decision_final"))
    print("Mercado:", res_valor.get("mercado_elegido"))
    print("Selección:", res_valor.get("seleccion"))
    print("Cuota:", res_valor.get("cuota"))
    print("True Prob:", res_valor.get("probabilidad_real_estimada"))
    print("EV:", res_valor.get("valor_esperado_ev"))
    print("Stake Kelly:", res_valor.get("stake_sugerido"))
    print("Explicación:", res_valor.get("explicacion_cuantitativa_detallada"))

    assert res_valor.get("decision_final") == "APROBADO", "Error: Debería haber aprobado la apuesta con +EV"
    assert res_valor.get("seleccion") == "Más de 1.5", f"Error: Selección esperada 'Más de 1.5', obtenida {res_valor.get('seleccion')}"
    assert res_valor.get("guardrails_superados") is True, "Error: Guardrails deberían estar superados"

    print("\n✅ Todos los tests del motor cuantitativo y contingencia pasaron satisfactoriamente.")

if __name__ == "__main__":
    test_motor()
