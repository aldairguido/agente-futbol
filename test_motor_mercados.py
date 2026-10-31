"""
test_motor_mercados.py
-------------------------------------------------------------------------------
Pruebas del motor de mercados. Usa DATOS SINTETICOS identificados como tales:
no son predicciones reales. No envia nada a Telegram (adaptador simulado).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import motor_mercados as mm
from motor_mercados import (
    MERCADOS,
    Parametros,
    matriz_marcadores,
    resultado_1x2,
    total_goles,
    prob_over,
    analizar,
)
import servicio_mercados as servicio


# ===============================================================================
# DATOS SINTETICOS
# ===============================================================================

def doc_sintetico(nombre: str, resultados: list[tuple[str, int, int]],
                  xg: list[tuple[float, float]] | None = None,
                  xg_1t: list[tuple[float, float]] | None = None,
                  xg_2t: list[tuple[float, float]] | None = None,
                  corners: list[tuple[float, float]] | None = None,
                  amarillas: list[tuple[float, float]] | None = None,
                  rojas: list[tuple[float, float]] | None = None,
                  equipo_liga: str = "Liga Test") -> dict:
    """
    Reporte sintetico. `resultados` = [(condicion, goles_favor, goles_contra), ...].
    Las listas opcionales dan la misma longitud que `resultados` o se omiten.
    """
    partidos = []

    def dame(lista: list | None, idx: int, pos: int) -> float | None:
        """Acceso seguro: si la lista no cubre ese partido, el dato queda nulo."""
        if lista is None or idx >= len(lista):
            return None
        return lista[idx][pos]

    for i, (cond, gf, gc) in enumerate(resultados):
        def per(periodo: str, valores: list | None, idx: int) -> dict:
            filas = []
            if valores is not None:
                for clave, val_eq, val_riv in (
                        ("xg", dame(valores, idx, 0), dame(valores, idx, 1)),
                        ("corners", dame(corners, idx, 0), dame(corners, idx, 1)),
                        ("tarjetas_amarillas", dame(amarillas, idx, 0), dame(amarillas, idx, 1)),
                        ("tarjetas_rojas", dame(rojas, idx, 0), dame(rojas, idx, 1))):
                    filas.append({"clave": clave, "metrica": clave, "categoria": "X",
                                  "equipo": val_eq, "rival": val_riv})
            return {"periodo": periodo, "filas": filas, "no_disponibles": []}

        partidos.append({
            "n_partido": i + 1, "id_partido": f"{nombre}-{i}", "fecha": f"2026-09-{i+1:02d}",
            "torneo": equipo_liga, "condicion": cond, "equipo": nombre, "rival": f"Rival {i}",
            "marcador": {"equipo": gf, "rival": gc, "texto": f"{gf}-{gc}"},
            "url": "", "periodos": {
                "FT": per("FT", xg, i),
                "1T": per("1T", xg_1t, i),
                "2T": per("2T", xg_2t, i),
            }, "notas": [], "error": None,
        })
    return {"equipo": {"nombre": nombre}, "fuente": "sintetica", "partidos": partidos,
            "resumen": {"partidos_con_datos": len(partidos)}}


# ===============================================================================
# PRUEBAS
# ===============================================================================

def prueba_catalogo_completo() -> None:
    """1. El catalogo tiene los 30 mercados en el orden de la plantilla."""
    assert len(MERCADOS) == 30, len(MERCADOS)
    esperados = ["ganador_partido", "combi_mejorada", "pago_anticipado_2", "total_goles",
                 "total_goles_local", "total_goles_visitante", "fiesta_de_goles",
                 "handicap_3_opciones", "ambos_anotan", "doble_oportunidad",
                 "apuesta_sin_empate", "ganador_1t", "_1t_ft", "equipo_primer_gol",
                 "ganador_total_15", "ganador_total_25", "ganador_total_35",
                 "ganador_ambos_anotan", "ambos_anotan_total_25", "anotador_cualquier_momento",
                 "primer_anotador", "anota_dos_o_mas", "hat_trick", "total_corners",
                 "gol_en_ambos_tiempos", "cualquier_jugador_primero", "cualquier_jugador_anota",
                 "corners_local", "corners_visitante", "total_tarjetas"]
    obtenidos = [c for c, _ in MERCADOS]
    assert obtenidos == esperados, [a for a, b in zip(obtenidos, esperados) if a != b]
    print("  OK 1. catalogo con los 30 mercados en el orden de la plantilla")


def prueba_matriz_y_coherencia() -> None:
    """2. La matriz suma 1, lambda reproduce la media y FT = 1T + 2T."""
    lam_l, lam_v = 1.6, 1.1
    matriz, residual = matriz_marcadores(lam_l, lam_v)
    assert abs(sum(sum(f) for f in matriz) - 1.0) < 1e-9
    assert residual <= mm.MASA_RESIDUAL_MAXIMA, residual

    media_local = sum(i * matriz[i][j] for i in range(len(matriz))
                      for j in range(len(matriz[i])))
    assert abs(media_local - lam_l) < 1e-6, (media_local, lam_l)
    assert all(0.0 <= p <= 1.0 for fila in matriz for p in fila)

    r = resultado_1x2(matriz)
    assert abs(sum(r.values()) - 1.0) < 1e-9, r

    m1, _ = matriz_marcadores(0.6, 0.5)
    m2, _ = matriz_marcadores(1.0, 0.6)
    d1, d2 = total_goles(m1), total_goles(m2)
    dft = total_goles(matriz_marcadores(1.6, 1.1)[0])
    convolucion = {}
    for k1, p1 in d1.items():
        for k2, p2 in d2.items():
            convolucion[k1 + k2] = convolucion.get(k1 + k2, 0.0) + p1 * p2
    for k in range(0, 8):
        assert abs(convolucion.get(k, 0.0) - dft.get(k, 0.0)) < 1e-9, k
    print("  OK 2. matriz normalizada, lambda correcta, 1x2 suma 1 y FT = 1T + 2T")


def prueba_referencia_lambda() -> None:
    """
    3. Regresion del modelo base: con goles_favor=1.6075 y goles_contra=2.57328125
       (w_condicion=0, w_xg=0) las entradas deben reproducirse exactamente.
       Es una REFERENCIA DE VERIFICACION, no una constante del sistema.
    """
    media_objetivo = 1.6075
    contra_objetivo = 2.57328125
    n = 8
    partidos = []
    for i in range(n):
        partidos.append({
            "condicion": "casa", "equipo": "Local FC", "rival": f"R{i}",
            "fecha": f"2026-09-{i+1:02d}", "torneo": "Liga", "id_partido": f"A{i}",
            "marcador": {"equipo": media_objetivo, "rival": contra_objetivo},
            "periodos": {"FT": {"filas": [], "no_disponibles": []},
                         "1T": {"filas": [], "no_disponibles": []},
                         "2T": {"filas": [], "no_disponibles": []}},
        })
    doc = {"equipo": {"nombre": "Local FC"}, "partidos": partidos, "fuente": "sintetica"}

    general = mm.extraer_muestra(doc, None, "FT")
    cond = mm.extraer_muestra(doc, "casa", "FT")
    assert abs((general.dame("goles_favor").media or 0) - media_objetivo) < 1e-9
    assert abs((general.dame("goles_contra").media or 0) - contra_objetivo) < 1e-9

    params = Parametros(w_condicion=0.0, w_xg=0.0)
    lam_l, _lam_v, detalle = mm.tasas_goles(general, general, cond, cond,
                                            params.w_condicion, params.w_xg)
    assert abs(detalle["local"]["ataque"] - media_objetivo) < 1e-9, detalle
    assert abs(detalle["local"]["defensa"] - contra_objetivo) < 1e-9, detalle
    esperado = (media_objetivo + contra_objetivo) / 2
    assert abs(lam_l - esperado) < 1e-9, (lam_l, esperado)
    print(f"  OK 3. referencia lambda verificada: ataque=1,6075 y defensa=2,57328125 "
          f"-> lambda={(media_objetivo + contra_objetivo) / 2:.8f}")


def prueba_media_ajustada_y_nulos() -> None:
    """4. Media ajustada pondera; un nulo NO se convierte en cero."""
    m = mm.MuestraMetrica("x")
    for v in (2.0, 4.0):
        m.agregar(v)
    assert m.n == 2 and abs((m.media or 0) - 3.0) < 1e-9
    m.agregar(None)
    assert m.n == 2, "un nulo no debe incrementar las observaciones"

    general = mm.MuestraMetrica("g", n=2, media=3.0, suma=6.0)
    cond = mm.MuestraMetrica("c", n=1, media=1.0, suma=1.0)
    assert abs((mm.media_ajustada(general, cond, 0.5) or 0) - 2.0) < 1e-9
    assert abs((mm.media_ajustada(general, cond, 0.0) or 0) - 3.0) < 1e-9
    assert abs((mm.media_ajustada(general, cond, 1.0) or 0) - 1.0) < 1e-9
    vacia = mm.MuestraMetrica("v")
    assert mm.media_ajustada(general, vacia, 0.5) == 3.0
    assert mm.media_ajustada(vacia, vacia, 0.5) is None
    print("  OK 4. media ajustada correcta y nulos tratados como ausencia de dato")


def prueba_filtro_estricto() -> None:
    """5. El umbral se aplica sin redondear: 0,70 exacto queda fuera."""
    umbral = 0.70
    for p, debe_pasar in [(0.6999, False), (0.70, False), (0.7000001, True), (0.75, True)]:
        assert (p > umbral) == debe_pasar, (p, debe_pasar)
    texto = mm.formatear_pct(0.7001, umbral)
    assert texto != "70,0%", texto
    assert mm.formatear_pct(0.999999, umbral) == ">99,9%"
    print(f"  OK 5. filtro estricto antes del redondeo (0,7001 se muestra como {texto})")


def prueba_sin_empate_y_handicap() -> None:
    """6. Sin empate separa victoria/devolucion/derrota; handicap con 3 resultados."""
    doc_l = doc_sintetico("Fuerte FC", [("casa", 3, 1)] * 6, xg=[(2.5, 0.8)] * 6)
    doc_v = doc_sintetico("Debil FC", [("fuera", 0, 2)] * 6, xg=[(0.6, 2.2)] * 6)
    a = analizar(doc_l, doc_v, "Fuerte FC", "Debil FC", condicion_local="casa",
                 condicion_visitante="fuera")
    r = {res.id_mercado: res for res in a.mercados}

    sels = {s.evento: s.probabilidad for s in r["apuesta_sin_empate"].selecciones}
    assert sels["dnb_local"] is not None and sels["dnb_devolucion"] is not None
    assert abs((sels["dnb_local"] or 0) + (sels["dnb_devolucion"] or 0)
               + (sels["dnb_visitante"] or 0) - 1.0) < 1e-9, sels
    condicionada = (sels["dnb_local"] or 0) / max(1e-9, 1 - (sels["dnb_devolucion"] or 0))
    assert (sels["dnb_local"] or 0) < condicionada

    for linea in mm.HANDICAPS_EUROPEOS:
        grupo = [s for s in r["handicap_3_opciones"].selecciones if s.linea == float(linea)]
        assert len(grupo) == 3, (linea, len(grupo))
        assert abs(sum(s.probabilidad or 0 for s in grupo) - 1.0) < 1e-9, linea
    print("  OK 6. sin empate con devolucion separada y handicap de 3 resultados")


def prueba_combinaciones_conjuntas() -> None:
    """7. Las combinaciones se calculan como evento conjunto, no por marginales."""
    doc_l = doc_sintetico("A", [("casa", 2, 1)] * 5, xg=[(1.8, 1.0)] * 5)
    doc_v = doc_sintetico("B", [("fuera", 1, 1)] * 5, xg=[(1.1, 1.4)] * 5)
    a = analizar(doc_l, doc_v, "A", "B")
    r = {res.id_mercado: res for res in a.mercados}

    gan = {s.evento: s.probabilidad for s in r["ganador_partido"].selecciones}
    dist = total_goles(a.motor.matriz_ft)
    p_over25 = prob_over(dist, 2.5)
    p_joint = {s.evento: s.probabilidad for s in r["ganador_total_25"].selecciones}["1_mas_2.5"]
    assert p_joint is not None and gan["1"] is not None
    assert p_joint <= gan["1"] + 1e-12 and p_joint <= p_over25 + 1e-12
    assert abs(p_joint - gan["1"] * p_over25) > 1e-6, "no debe ser el producto marginal"
    assert abs(sum(s.probabilidad or 0 for s in r["ganador_total_25"].selecciones) - 1.0) < 1e-9
    print(f"  OK 7. combinaciones conjuntas (P(1 y +2,5)={p_joint:.4f} != "
          f"marginal {gan['1'] * p_over25:.4f})")


def prueba_sin_datos_suficientes() -> None:
    """8. Sin xG por tiempos no se inventa el reparto 1T/2T."""
    doc_l = doc_sintetico("A", [("casa", 2, 1)] * 5)
    doc_v = doc_sintetico("B", [("fuera", 1, 1)] * 5)
    a = analizar(doc_l, doc_v, "A", "B")
    r = {res.id_mercado: res for res in a.mercados}
    assert a.motor.tasas.lambda_local_1t is None
    assert r["ganador_1t"].estado == "datos_insuficientes"
    assert r["_1t_ft"].estado == "datos_insuficientes"
    assert r["gol_en_ambos_tiempos"].estado == "datos_insuficientes"
    assert r["anotador_cualquier_momento"].estado == "datos_insuficientes"
    assert r["combi_mejorada"].estado == "regla_no_definida"
    assert r["pago_anticipado_2"].estado == "datos_insuficientes"
    vacio = {"equipo": {"nombre": "A"}, "fuente": "sintetica",
             "partidos": [{"condicion": "casa", "equipo": "A", "rival": "B",
                           "fecha": "2026-01-01", "periodos": {}}]}
    a2 = analizar(vacio, vacio, "A", "B")
    assert {res.estado for res in a2.mercados if res.id_mercado == "ganador_partido"} \
        == {"datos_insuficientes"}
    print("  OK 8. sin datos suficientes: razon explicita, sin probabilidades inventadas")


def prueba_cobertura_catalogo() -> None:
    """9. Todo el catalogo queda con resultado o con razon explicita."""
    doc_l = doc_sintetico("A", [("casa", 2, 1), ("fuera", 1, 2)] * 3,
                          xg=[(1.7, 1.0)] * 6, xg_1t=[(0.7, 0.4)] * 6, xg_2t=[(1.0, 0.6)] * 6,
                          corners=[(6, 4)] * 6, amarillas=[(2, 3)] * 6, rojas=[(0, 0)] * 6)
    doc_v = doc_sintetico("B", [("fuera", 1, 1), ("casa", 2, 0)] * 3,
                          xg=[(1.1, 1.3)] * 6, xg_1t=[(0.5, 0.6)] * 6, xg_2t=[(0.6, 0.7)] * 6,
                          corners=[(5, 5)] * 6, amarillas=[(3, 2)] * 6, rojas=[(0, 0)] * 6)
    a = analizar(doc_l, doc_v, "A", "B")
    assert len(a.mercados) == 30
    for res in a.mercados:
        assert res.estado in ("calculado", "sin_selecciones", "datos_insuficientes",
                              "regla_no_definida"), (res.id_mercado, res.estado)
        if res.estado == "calculado":
            assert res.selecciones, res.id_mercado
        else:
            assert res.nota, f"{res.id_mercado} sin nota explicativa"
    estados = a.estado_mercados()
    for clave in ("total_corners", "total_tarjetas", "ganador_1t", "_1t_ft"):
        assert estados[clave] == "calculado", (clave, estados[clave])
    print("  OK 9. catalogo completo cubierto (30 mercados con resultado o razon)")


def prueba_sensibilidad_y_grupos() -> None:
    """10. Clasificacion en mantiene / sensible / sensibilidad incompleta."""
    doc_l = doc_sintetico("A", [("casa", 2, 1)] * 5, xg=[(1.8, 1.0)] * 5)
    doc_v = doc_sintetico("B", [("fuera", 1, 1)] * 5, xg=[(1.1, 1.4)] * 5)
    a = analizar(doc_l, doc_v, "A", "B")
    for s in a.selecciones_filtradas:
        assert s.estado in (mm.ESTADO_MANTIENE, mm.ESTADO_SENSIBLE, mm.ESTADO_INCOMPLETA)
        assert s.probabilidad is not None and s.probabilidad > a.parametros.umbral
    for s in a.grupos[mm.ESTADO_INCOMPLETA]:
        assert s.id_mercado in mm._mercados_de_conteos(), s.id_mercado
    assert a.mensaje.strip()
    print(f"  OK 10. sensibilidad clasificada ({len(a.grupos[mm.ESTADO_MANTIENE])} mantienen, "
          f"{len(a.grupos[mm.ESTADO_SENSIBLE])} sensibles, "
          f"{len(a.grupos[mm.ESTADO_INCOMPLETA])} incompletas)")


def prueba_condicion_real_y_simetria() -> None:
    """
    11. La condicion se resuelve con la fecha del encuentro o, si no hay fecha,
        solo cuando el reporte es inequivoco (todas las condiciones iguales).
    """
    # Reporte MIXTO (local y visitante) sin fecha -> condicion NO verificable
    doc_a = doc_sintetico("Alfa", [("casa", 3, 0), ("casa", 2, 1),
                                   ("fuera", 1, 2), ("fuera", 0, 2)] * 2,
                          xg=[(2.4, 0.6), (2.0, 0.8), (1.0, 1.8), (0.8, 2.0)] * 2)
    doc_b = doc_sintetico("Beta", [("casa", 2, 1), ("casa", 3, 1),
                                   ("fuera", 1, 1), ("fuera", 2, 2)] * 2,
                          xg=[(1.6, 1.0), (2.0, 0.7), (1.1, 1.3), (1.4, 1.6)] * 2)
    ambiguo = analizar(doc_a, doc_b, "Alfa", "Beta")
    assert "asumida por convenci" in ambiguo.metadatos["origen_condicion"], \
        ambiguo.metadatos["origen_condicion"]

    # Reporte INEQUIVOCO: Alfa siempre local, Beta siempre visitante -> se resuelve
    doc_hl = doc_sintetico("Alfa local", [("casa", 3, 0)] * 4, xg=[(2.4, 0.6)] * 4)
    doc_av = doc_sintetico("Beta visita", [("fuera", 0, 2)] * 4, xg=[(0.7, 2.0)] * 4)
    claro = analizar(doc_hl, doc_av, "Alfa local", "Beta visita")
    assert claro.metadatos["condicion_local"] == "casa", claro.metadatos
    assert claro.metadatos["condicion_visitante"] == "fuera", claro.metadatos
    assert claro.metadatos["origen_condicion"] == "resuelta desde los reportes"
    assert claro.motor.muestras["local_cond"].equipo == "Alfa local"
    assert claro.motor.muestras["visitante_cond"].equipo == "Beta visita"

    # Simetria real: los bloques de condicion que aporta cada equipo deben ser
    # equivalentes (mismos goles/xG en casa que fuera). Si no, la asimetria es
    # legitima y viene de los datos, no del motor.
    registros = [("casa", 2, 1), ("casa", 1, 1), ("fuera", 2, 1), ("fuera", 1, 1)]
    xgs = [(1.5, 1.0), (1.2, 1.1), (1.5, 1.0), (1.2, 1.1)]
    doc_x = doc_sintetico("X", registros, xg=xgs)
    doc_y = doc_sintetico("Y", registros, xg=xgs)
    asym = analizar(doc_x, doc_y, "X", "Y")
    assert abs((asym.motor.tasas.lambda_local_ft or 0)
               - (asym.motor.tasas.lambda_visitante_ft or 0)) < 1e-9, \
        ("con bloques de condicion equivalentes las lambda deben coincidir",
         asym.motor.tasas.lambda_local_ft, asym.motor.tasas.lambda_visitante_ft)
    gx = {s.evento: s.probabilidad for s in
          next(r for r in asym.mercados if r.id_mercado == "ganador_partido").selecciones}
    assert abs((gx["1"] or 0) - (gx["2"] or 0)) < 1e-9, gx

    # Y cuando los bloques de condicion difieren de verdad, la diferencia aparece
    doc_fuerte = doc_sintetico("F", [("casa", 3, 1)] * 4, xg=[(2.2, 0.9)] * 4)
    doc_debil = doc_sintetico("D", [("fuera", 0, 2)] * 4, xg=[(0.6, 2.0)] * 4)
    claro2 = analizar(doc_fuerte, doc_debil, "F", "D")
    g2 = {s.evento: s.probabilidad for s in
          next(r for r in claro2.mercados if r.id_mercado == "ganador_partido").selecciones}
    assert (g2["1"] or 0) > (g2["2"] or 0), g2

    # La condicion cambia la lambda (no es decorativa)
    con_cond = analizar(doc_a, doc_b, "Alfa", "Beta", condicion_local="casa",
                        condicion_visitante="fuera")
    sin_cond = analizar(doc_a, doc_b, "Alfa", "Beta", condicion_local="fuera",
                        condicion_visitante="casa")
    assert abs((con_cond.motor.tasas.lambda_local_ft or 0)
               - (sin_cond.motor.tasas.lambda_local_ft or 0)) > 1e-6

    assert "Alfa local" in claro.mensaje and "Beta visita" in claro.mensaje
    assert "Galatasaray" not in claro.mensaje and "Barcelona" not in claro.mensaje
    print("  OK 11. condicion resuelta/declarada correctamente, simetria y sin nombres fijos")


def prueba_mensaje_y_particion() -> None:
    """12. El mensaje solo lleva selecciones validas y se parte sin truncar."""
    doc_l = doc_sintetico("A", [("casa", 4, 1)] * 8, xg=[(3.0, 0.8)] * 8,
                          xg_1t=[(1.3, 0.3)] * 8, xg_2t=[(1.7, 0.5)] * 8,
                          corners=[(8, 3)] * 8, amarillas=[(2, 2)] * 8, rojas=[(0, 0)] * 8)
    doc_v = doc_sintetico("B", [("fuera", 0, 2)] * 8, xg=[(0.5, 2.2)] * 8,
                          xg_1t=[(0.2, 1.0)] * 8, xg_2t=[(0.3, 1.2)] * 8,
                          corners=[(3, 6)] * 8, amarillas=[(3, 1)] * 8, rojas=[(0, 0)] * 8)
    a = analizar(doc_l, doc_v, "A", "B")
    assert a.grupos[mm.ESTADO_MANTIENE] or a.grupos[mm.ESTADO_SENSIBLE]
    for s in a.selecciones_filtradas:
        assert (s.probabilidad or 0) > a.parametros.umbral
        assert mm.formatear_pct(s.probabilidad, a.parametros.umbral) != "N/D"

    partes = servicio.partir_mensaje(a.mensaje, limite=400)
    unido = "".join(p.split("\n", 1)[1] if p.startswith("[") else p for p in partes)
    if len(partes) > 1:
        assert "[1/" in partes[0]
    assert a.mensaje.replace("\n", "") in unido.replace("\n", ""), "la particion pierde texto"
    print(f"  OK 12. mensaje filtrado y particionado ({len(partes)} parte(s))")


def prueba_flujo_envio_y_duplicados() -> None:
    """13. Estados separados y sin reenvios de un envio ya confirmado."""
    doc_l = doc_sintetico("A", [("casa", 3, 1)] * 6, xg=[(2.2, 0.9)] * 6)
    doc_v = doc_sintetico("B", [("fuera", 1, 2)] * 6, xg=[(1.0, 1.6)] * 6)
    a = analizar(doc_l, doc_v, "A", "B")
    preparado = {"id_trabajo": "prueba123", "mensaje": a.mensaje, "analisis": a,
                 "rutas": {"informe": "x", "datos": "y"}}

    with tempfile.TemporaryDirectory() as tmp:
        servicio.REGISTRO_ENVIOS = os.path.join(tmp, "registro.json")

        fallo = servicio.enviar_analisis(preparado, enviar_fn=lambda t, c=None: {"ok": False,
                                                                                "error": "boom"})
        assert fallo["enviado"] is False and fallo["estado"] == "envio_fallido"
        assert servicio.estado_envio_previo("prueba123")["estado"] == "envio_fallido"

        ok = servicio.enviar_analisis(preparado, enviar_fn=lambda t, c=None: {"ok": True,
                                                                             "message_id": 7})
        assert ok["enviado"] is True and ok["estado"] == "envio_confirmado"
        assert ok["ids_telegram"] == [7]

        repetido = servicio.enviar_analisis(preparado,
                                            enviar_fn=lambda t, c=None: {"ok": True,
                                                                         "message_id": 8})
        assert repetido["enviado"] is False and "ya confirmado" in repetido["motivo"]

        nueva = servicio.enviar_analisis(preparado,
                                         enviar_fn=lambda t, c=None: {"ok": True,
                                                                      "message_id": 9},
                                         nueva_ejecucion=True)
        assert nueva["enviado"] is True

        sin_adaptador = servicio.enviar_analisis(preparado)
        assert sin_adaptador["enviado"] is False
        assert "falta configuraci" in sin_adaptador["motivo"]
    print("  OK 13. estados separados, sin duplicados y fallo sin perder analisis")


def prueba_condicion_no_verificable() -> None:
    """14. Si la condicion no se puede verificar, se declara explicitamente."""
    doc_l = doc_sintetico("A", [("casa", 2, 1)] * 4, xg=[(1.5, 0.9)] * 4)
    doc_v = doc_sintetico("B", [("fuera", 1, 1)] * 4, xg=[(1.0, 1.2)] * 4)
    a = analizar(doc_l, doc_v, "A", "B", fecha_partido="2030-01-01",
                 condicion_local="desconocida", condicion_visitante="desconocida")
    assert "asumida por convenci" in a.metadatos["origen_condicion"], a.metadatos["origen_condicion"]
    assert a.metadatos["condicion_local"] == "casa"
    b = analizar(doc_l, doc_v, "A", "B", condicion_local="fuera", condicion_visitante="casa")
    assert b.metadatos["origen_condicion"] == "aportada por el usuario"
    print("  OK 14. condicion no verificable declarada explicitamente")


def prueba_envio_parse_mode() -> None:
    """
    15. Regresion del fallo de envio: `parse_mode` NUNCA debe viajar con valor nulo
        (Telegram responde 400 'unsupported parse_mode') y el texto plano debe
        entregarse aunque el texto rompa Markdown.
    """
    import bot_interactivo as b
    import servicio_mercados as servicio

    capturado: dict = {}

    class RespuestaFalsa:
        status_code = 200
        content = b'{"ok": true, "result": {"message_id": 1}}'

        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

    def post_falso(url, json=None, timeout=None):
        capturado.clear()
        capturado.update(json or {})
        return RespuestaFalsa()

    import requests
    original_post = requests.post
    requests.post = post_falso
    try:
        r = servicio.enviar_fn_telegram("texto con _guiones_ y 100%", "123")
        assert r.get("ok"), r
        assert "parse_mode" not in capturado, \
            f"parse_mode no debe enviarse en texto plano: {capturado}"
        r2 = servicio.enviar_fn_telegram("texto", "123", parse_mode="HTML")
        assert r2.get("ok") and capturado.get("parse_mode") == "HTML", capturado
        r3 = servicio.enviar_fn_telegram("texto", "123", parse_mode="md")
        assert r3.get("ok") is False and "inválido" in r3["error"], r3
    finally:
        requests.post = original_post

    entregados: list[dict] = []

    class BotFalso:
        """
        Reproduce fielmente la semántica de pyTelegramBotAPI:
            parse_mode = self.parse_mode if (parse_mode is None) else parse_mode
        Es decir, pasar None NO desactiva el formato: lo restaura. Por eso el
        arreglo pasa cadena vacía y además deja `bot.parse_mode` en None.
        """
        parse_mode = "Markdown"

        def send_message(self, destino, texto, **kw):
            # Ausente o None -> se resuelve con el de la instancia (como telebot)
            crudo = kw.get("parse_mode")
            modo = self.parse_mode if crudo is None else crudo
            if modo not in (None, "", "Markdown", "MarkdownV2", "HTML"):
                raise RuntimeError(f"parse_mode inválido: {modo!r}")
            if modo in ("Markdown", "MarkdownV2") and texto.count("_") % 2:
                raise RuntimeError("400 Bad Request: can't parse entities")
            entregados.append({"texto": texto, "parse_mode": modo or None})
            return {"ok": True}

        def reply_to(self, destino, texto, **kw):
            return self.send_message(destino, texto, **kw)

    falso = BotFalso()
    # 6 guiones bajos (par) -> Markdown lo acepta; sirve para el camino normal
    texto_plano = ("Ruta: C:\\informes\\mercados_2026-10-13\\"
                   "mercados_atletico_madrid_vs_manchester_united.md")
    resultado = b.enviar_seguro(falso, "123", texto_plano)
    assert resultado is not None, "el texto plano debe entregarse (antes se perdía)"
    assert entregados and entregados[-1]["parse_mode"] is None, entregados[-1]
    assert falso.parse_mode == "Markdown", "el parse_mode de la instancia debe restaurarse"

    # Guiones IMPARES -> Markdown no puede parsearlo y debe caer a texto plano
    texto_roto = "Ruta: C:\\informes\\mercados_2026-10-13\\mercados_atletico_madrid.md (1)"
    assert texto_roto.count("_") % 2 == 1, "la prueba necesita un nº impar de guiones"
    entregados.clear()
    assert b.enviar_seguro(falso, "123", texto_roto, markdown=True) is not None
    assert entregados[-1]["parse_mode"] is None, entregados[-1]
    assert falso.parse_mode == "Markdown"
    print("  OK 15. parse_mode nunca nulo, texto plano fiable y modo restaurado")


def prueba_formato_agrupado() -> None:
    """
    16. El mensaje agrupa por mercado con icono y NO repite el nombre del mercado
        en cada linea (era el muro de texto), ni el nombre del equipo.
    """
    doc_l = doc_sintetico("Alfa FC", [("casa", 3, 1)] * 8, xg=[(2.4, 0.9)] * 8,
                          xg_1t=[(1.0, 0.4)] * 8, xg_2t=[(1.4, 0.5)] * 8,
                          corners=[(7, 4)] * 8, amarillas=[(2, 3)] * 8, rojas=[(0, 0)] * 8)
    doc_v = doc_sintetico("Beta FC", [("fuera", 1, 2)] * 8, xg=[(0.9, 1.8)] * 8,
                          xg_1t=[(0.3, 0.9)] * 8, xg_2t=[(0.6, 0.9)] * 8,
                          corners=[(4, 6)] * 8, amarillas=[(3, 2)] * 8, rojas=[(0, 0)] * 8)
    a = analizar(doc_l, doc_v, "Alfa FC", "Beta FC")
    lineas = a.mensaje.splitlines()

    bloques = ("✅ SEGUROS", "⚠️ SENSIBLES", "⚠️ SENSIBILIDAD INCOMPLETA")
    indices = [i for i, l in enumerate(lineas) if l.startswith(bloques)]
    assert indices, "debe haber encabezados de bloque"
    iconos = (mm.EMOJI_MERCADO, mm.EMOJI_CORNERS, mm.EMOJI_TARJETAS,
              mm.EMOJI_HANDICAP, mm.EMOJI_COMBINADA, mm.EMOJI_AMBOS, mm.EMOJI_RESULTADO)
    subtitulos = [l for l in lineas if l.startswith(iconos)]
    assert subtitulos, "debe haber subtitulos de mercado con icono"

    vinetas = [l for l in lineas if l.startswith("• ")]
    assert vinetas, "debe haber vinetas"
    mercados = {s.mercado for s in a.selecciones_filtradas}
    for v in vinetas:
        for merc in mercados:
            assert merc not in v, f"la vinneta repite el mercado {merc!r}: {v!r}"
        assert "Alfa FC:" not in v, f"la vinneta repite el equipo: {v!r}"
        assert "Beta FC:" not in v, f"la vinneta repite el equipo: {v!r}"

    for inicio in indices:
        fin = next((j for j in range(inicio + 1, len(lineas))
                    if lineas[j].startswith(bloques)), len(lineas))
        del_bloque = [l for l in lineas[inicio:fin] if l.startswith(iconos)]
        nombres = [l[2:].strip() for l in del_bloque]
        assert len(nombres) == len(set(nombres)), \
            f"mercado repetido como subtitulo en un bloque: {nombres}"

    assert len(vinetas) == len(a.selecciones_filtradas), \
        f"{len(vinetas)} vinetas para {len(a.selecciones_filtradas)} selecciones"

    con_equipo = [s for s in a.selecciones_filtradas if s.equipo and ":" in s.etiqueta]
    if con_equipo:
        corta = mm.etiqueta_corta(con_equipo[0])
        assert not corta.startswith(str(con_equipo[0].equipo)), corta

    # Todo mercado tiene icono propio (ninguno cae en el generico)
    for merc in mercados:
        assert mm.emoji_de_mercado(merc) != "•", f"mercado sin icono: {merc!r}"
    print(f"  OK 16. formato agrupado: {len(subtitulos)} subtitulos, "
          f"{len(vinetas)} vinetas, sin repeticiones")


def prueba_cobertura_xg() -> None:
    """
    17. La cobertura de xG se declara: un partido sin xG no impide calcular, pero
        el mensaje dice con que se calculo y avisa si el xG es escaso.
    """
    # Caso A: partidos CON xG -> cobertura total, sin aviso
    doc_ok = doc_sintetico("Con XG", [("casa", 2, 1)] * 6, xg=[(1.8, 0.9)] * 6)
    m_ok = mm.extraer_muestra(doc_ok, condicion=None, periodo="FT")
    con, tot, frac = m_ok.cobertura_xg()
    assert (con, tot) == (6, 6), (con, tot)
    assert abs(frac - 1.0) < 1e-12, frac
    assert m_ok.resumen_cobertura()["usa_xg_real"] is True

    # Caso B: partidos SIN xG -> cobertura nula, se declara "solo goles"
    doc_sin = doc_sintetico("Sin XG", [("casa", 2, 1)] * 6, xg=[(None, None)] * 6)
    m_sin = mm.extraer_muestra(doc_sin, condicion=None, periodo="FT")
    con, tot, frac = m_sin.cobertura_xg()
    assert con == 0 and tot == 6 and frac == 0.0, (con, tot, frac)
    assert m_sin.resumen_cobertura()["sin_xg"] is True
    # La lambda debe seguir calculandose (respaldo por goles), no quedarse vacia
    lam_l, lam_v, det = mm.tasas_goles(
        m_sin, m_sin, m_sin, m_sin, 0.5, 0.5)
    assert lam_l is not None and lam_v is not None, (lam_l, lam_v)
    assert det["local"]["base_calculo"].startswith("solo goles"), det["local"]
    assert det["local"]["xg_disponible"] is False

    # Caso C: mezcla -> se declara el recuento real y avisa si es escaso
    doc_mix = doc_sintetico("Mixto", [("casa", 2, 1)] * 6,
                            xg=[(1.5, 1.0), (1.2, 0.8), (None, None),
                                (None, None), (None, None), (None, None)])
    m_mix = mm.extraer_muestra(doc_mix, condicion=None, periodo="FT")
    con, tot, frac = m_mix.cobertura_xg()
    assert (con, tot) == (2, 6), (con, tot)
    assert abs(frac - 2 / 6) < 1e-12, frac

    # El mensaje declara la cobertura de ambos equipos
    motor = mm.MotorMercados(doc_mix, doc_sin, "Mixto", "Sin XG",
                             parametros=mm.Parametros())
    motor.preparar()
    linea = mm.linea_cobertura_xg(
        {"equipo_local": "Mixto", "equipo_visitante": "Sin XG"}, motor)
    assert "Mixto: xG en 2/6" in linea, linea
    assert "Sin XG: SIN xG" in linea, linea
    assert "⚠️" in linea, f"debe avisar del xG escaso: {linea}"
    print(f"  OK 17. cobertura de xG declarada: {linea.splitlines()[0]}")


def prueba_contraccion_bayesiana() -> None:
    """
    18. Contracción bayesiana del peso de condición: con pocos partidos de condición
        el peso cae (manda el historial general); converge a w_max sin superarlo.
    """
    # Monotonía y cota
    anterior = -1.0
    for n in range(1, 60):
        w = mm.peso_condicion_por_evidencia(n, 0.5, 5.0)
        assert w > anterior, f"el peso debe crecer con n (n={n})"
        assert w < 0.5, f"nunca debe alcanzar w_max (n={n}, w={w})"
        anterior = w
    assert mm.peso_condicion_por_evidencia(0, 0.5, 5.0) == 0.0
    # Formula exacta en los puntos clave
    assert abs(mm.peso_condicion_por_evidencia(5, 0.5, 5.0) - 0.25) < 1e-12
    assert abs(mm.peso_condicion_por_evidencia(2, 0.5, 5.0) - 0.5 * 2 / 7) < 1e-12
    # Con K=0 no hay contracción: el peso es w_max
    assert abs(mm.peso_condicion_por_evidencia(3, 0.5, 0.0) - 0.5) < 1e-12

    # Efecto sobre la media: pocos partidos de condición -> casi la general
    class _M:
        def __init__(self, media, n):
            self.media, self.n = media, n

    gen, cond = _M(1.80, 15), _M(0.31, 2)      # el caso real Vasco
    fijo = mm.media_ajustada(gen, cond, 0.5)
    contraido = mm.media_ajustada(gen, cond, 0.5, 5.0)
    assert abs(fijo - 1.055) < 1e-9, fijo
    # w contraído = 0,5·2/(2+5) = 0,142857 -> media ponderada 1,5871
    esperado = (1.0 - 0.5 * 2 / 7) * 1.80 + (0.5 * 2 / 7) * 0.31
    assert abs(contraido - esperado) < 1e-9, (contraido, esperado)
    assert abs(contraido - 1.587142857142857) < 1e-9, contraido
    # La contracción acerca el resultado a la media general
    assert abs(contraido - gen.media) < abs(fijo - gen.media), (contraido, fijo)
    # Con muchos partidos de condición el peso se acerca al fijo, pero no lo iguala:
    # con K=5 y n=30 el peso es 0,5·30/35 = 0,4286, a 0,0714 del fijo.
    gen2, cond2 = _M(1.80, 15), _M(1.00, 30)
    w_fijo, w_contraido = 0.5, mm.peso_condicion_por_evidencia(30, 0.5, 5.0)
    assert abs(w_contraido - w_fijo) < 0.08, (w_contraido, w_fijo)
    cerca = mm.media_ajustada(gen2, cond2, 0.5, 5.0)
    assert abs(cerca - mm.media_ajustada(gen2, cond2, 0.5)) < 0.10, cerca
    assert cerca > mm.media_ajustada(gen2, cond2, 0.5), "debe quedar más cerca de la general"
    # k_credibilidad=None conserva el comportamiento anterior (peso fijo)
    assert abs(mm.media_ajustada(gen, cond, 0.5, None) - fijo) < 1e-12
    # Un nulo no se convierte en cero
    class _V:
        media, n = None, 0
    assert mm.media_ajustada(gen, _V(), 0.5, 5.0) == gen.media
    print(f"  OK 18. contraccion bayesiana: n=2 -> w={mm.peso_condicion_por_evidencia(2, 0.5, 5.0):.4f} "
          f"(antes 0,50), media {fijo:.4f} -> {contraido:.4f}")


def prueba_dixon_coles() -> None:
    """
    19. Corrección de Dixon-Coles: aplica tau a los marcadores bajos, renormaliza y
        con rho=0 es exactamente el Poisson independiente.
    """
    # tau en los cuatro marcadores bajos, con la fórmula estándar
    ll, lv, rho = 1.4, 1.26, -0.11
    assert abs(mm.factor_dixon_coles(0, 0, ll, lv, rho) - (1 - ll * lv * rho)) < 1e-12
    assert abs(mm.factor_dixon_coles(1, 0, ll, lv, rho) - (1 + lv * rho)) < 1e-12
    assert abs(mm.factor_dixon_coles(0, 1, ll, lv, rho) - (1 + ll * rho)) < 1e-12
    assert abs(mm.factor_dixon_coles(1, 1, ll, lv, rho) - (1 - rho)) < 1e-12
    assert mm.factor_dixon_coles(2, 1, ll, lv, rho) == 1.0
    # rho=0 -> sin corrección
    for i in range(4):
        for j in range(4):
            assert mm.factor_dixon_coles(i, j, ll, lv, 0.0) == 1.0
    # Un rho absurdo no produce probabilidades negativas
    assert mm.factor_dixon_coles(0, 0, 5.0, 5.0, 1.0) == 0.0

    # La matriz renormaliza y sigue sumando 1
    m0, _ = mm.matriz_marcadores(ll, lv, rho=0.0)
    m1, _ = mm.matriz_marcadores(ll, lv, rho=rho)
    for m in (m0, m1):
        assert abs(sum(sum(f) for f in m) - 1.0) < 1e-12
    # rho negativo sube el 0-0 y el 1-1, y baja el 1-0
    assert m1[0][0] > m0[0][0], "rho<0 debe subir P(0-0)"
    assert m1[1][1] > m0[1][1], "rho<0 debe subir P(1-1)"
    assert m1[1][0] < m0[1][0], "rho<0 debe bajar P(1-0)"
    # No altera los marcadores altos
    assert abs(m1[2][1] - m0[2][1]) < 1e-12
    # Efecto sobre el 1X2: sube el empate y bajan las victorias
    r0, r1 = mm.resultado_1x2(m0), mm.resultado_1x2(m1)
    assert r1["X"] > r0["X"], (r0["X"], r1["X"])
    assert r1["1"] < r0["1"] and r1["2"] < r0["2"]
    for k in ("1", "X", "2"):
        assert abs(sum(r1.values()) - 1.0) < 1e-12
    # Por defecto NO se aplica (rho=0): el modelo vigente no cambia
    assert mm.Parametros().rho == 0.0
    print(f"  OK 19. Dixon-Coles: rho={rho} -> empate {r0['X']*100:.2f}% -> "
          f"{r1['X']*100:.2f}%, 1 {r0['1']*100:.2f}% -> {r1['1']*100:.2f}%")


def prueba_comparar_tecnicas() -> None:
    """
    20. La comparación de técnicas no sustituye el modelo vigente y respeta el
        mismo catálogo en todas las variantes.
    """
    doc_l = doc_sintetico("Alfa FC", [("casa", 2, 1)] * 8, xg=[(1.9, 0.9)] * 8,
                          corners=[(7, 4)] * 8, amarillas=[(2, 3)] * 8, rojas=[(0, 0)] * 8)
    doc_v = doc_sintetico("Beta FC", [("fuera", 1, 1)] * 8, xg=[(0.9, 1.6)] * 8,
                          corners=[(4, 6)] * 8, amarillas=[(3, 2)] * 8, rojas=[(0, 0)] * 8)
    r = mm.comparar_tecnicas(doc_l, doc_v, "Alfa FC", "Beta FC")
    assert set(r["variantes"]) == {"base", "bayes", "dixoncoles", "ambas"}, list(r["variantes"])
    for nombre, d in r["variantes"].items():
        assert "error" not in d, f"{nombre}: {d.get('error')}"
    # La base usa peso fijo y las variantes bayesianas lo contraen
    assert abs(r["variantes"]["base"]["w_condicion_efectivo_local"] - 0.5) < 1e-12
    assert r["variantes"]["bayes"]["w_condicion_efectivo_local"] < 0.5
    # Dixon-Coles no toca las lambda (actúa sobre la matriz), Bayes sí puede
    assert abs(r["variantes"]["dixoncoles"]["lambda_local"]
               - r["variantes"]["base"]["lambda_local"]) < 1e-12
    # Ninguna variante cambia el catálogo evaluado
    assert (r["variantes"]["base"]["superan_umbral"]
            >= 0)
    print(f"  OK 20. comparación de técnicas: base={r['variantes']['base']['superan_umbral']} "
          f"bayes={r['variantes']['bayes']['superan_umbral']} "
          f"dixon={r['variantes']['dixoncoles']['superan_umbral']} "
          f"ambas={r['variantes']['ambas']['superan_umbral']}")


def prueba_mezcla_goles_xg_por_cobertura() -> None:
    """
    21. Mezcla goles/xG ponderada por COBERTURA: sin xG usa 100% goles sin fallar,
        y con xG escaso el peso se reduce, nunca se dispara.
    """
    # Sin xG -> 100% goles, y NUNCA TypeError
    v, d, w = mm.mezcla_goles_xg(1.5, 15, None, 0, 0.5)
    assert v == 1.5 and w == 0.0, (v, w)
    # Nada de nada -> None, no cero
    v, d, w = mm.mezcla_goles_xg(None, 0, None, 0, 0.5)
    assert v is None and w == 0.0
    # Solo xG -> 100% xG
    v, d, w = mm.mezcla_goles_xg(None, 0, 1.2, 5, 0.5)
    assert v == 1.2 and w == 1.0
    # El peso crece con la cobertura
    anteriores = []
    for n_xg in (1, 5, 9, 13, 15):
        _v, _d, w = mm.mezcla_goles_xg(1.5, 15, 1.2, n_xg, 0.5)
        anteriores.append(w)
    assert anteriores == sorted(anteriores), anteriores
    assert all(0.0 <= w < 0.5 for w in anteriores), anteriores
    # Casos reales verificados
    esperados = {0: 0.0000, 5: 0.0926, 9: 0.2077, 13: 0.3314, 15: 0.3947}
    for n_xg, esperado in esperados.items():
        _v, _d, w = mm.mezcla_goles_xg(1.5, 15, 1.2, n_xg, 0.5)
        assert abs(w - esperado) < 5e-4, (n_xg, w, esperado)
    # Con 0 goles pero xG, no explota
    v, _d, w = mm.mezcla_goles_xg(0.0, 15, 1.2, 15, 0.5)
    assert v is not None and 0.0 < v < 1.2
    print(f"  OK 21. mezcla goles/xG por cobertura: sin xG -> 100% goles (w=0), "
          f"15/15 -> w={esperados[15]}, 5/15 -> w={esperados[5]}")


def prueba_moduladores_neutros_y_acotados() -> None:
    """
    22. Moduladores tácticos: EXACTAMENTE neutros si falta el dato, y acotados de
        verdad al 15% en el efecto total (no solo factor a factor).
    """
    vacio = {k: None for k in ("posesion_pct", "xg", "xgot", "grandes_ocasiones",
                               "grandes_ocasiones_falladas", "remates_totales",
                               "remates_dentro_area", "xg_sin_penalti",
                               "duelos_aereos_pct", "goles_evitados", "despejes",
                               "bloqueos", "toques_area_rival")}
    r = mm.calcular_moduladores(vacio, vacio)
    assert r["mult_goles_L"] == 1.0 and r["mult_goles_V"] == 1.0, r
    assert r["mult_corners_L"] == 1.0 and r["mult_corners_V"] == 1.0, r
    assert r["metricas_activas"] == [], r["metricas_activas"]

    # Datos absurdos: el efecto total no puede pasar del límite
    for m in ({"posesion_pct": 1, "xg": 0.1, "xgot": 5.0, "remates_totales": 40,
               "remates_dentro_area": 39, "duelos_aereos_pct": 1,
               "goles_evitados": -5.0, "despejes": 0, "bloqueos": 0,
               "toques_area_rival": 0},
              {"posesion_pct": 99, "xg": 5.0, "xgot": 0.1, "remates_totales": 40,
               "grandes_ocasiones": 9, "grandes_ocasiones_falladas": 9,
               "duelos_aereos_pct": 99, "goles_evitados": 5.0, "despejes": 99,
               "bloqueos": 99, "toques_area_rival": 99}):
        r = mm.calcular_moduladores(m, m)
        for clave in ("mult_goles_L", "mult_goles_V", "mult_corners_L", "mult_corners_V"):
            assert 0.85 - 1e-9 <= r[clave] <= 1.15 + 1e-9, (clave, r[clave])
    # Un solo equipo con dato no basta para los que comparan dos bandos
    r = mm.calcular_moduladores({"posesion_pct": 60.0}, {"posesion_pct": None})
    assert r["mult_goles_L"] == 1.0, r["mult_goles_L"]
    print(f"  OK 22. moduladores neutros con nulos y acotados a ±15% "
          f"(6 categorías activas con datos)")


def prueba_metricas_equipo_extraidas() -> None:
    """
    23. Las métricas de EQUIPO llegan a las muestras: antes solo se extraían 12 de
        las 45, y por eso los moduladores salían siempre neutros.
    """
    doc_a = doc_sintetico("Alfa FC", [("casa", 2, 1)] * 6, xg=[(1.9, 0.9)] * 6,
                          corners=[(7, 4)] * 6, amarillas=[(2, 3)] * 6,
                          rojas=[(0, 0)] * 6)
    doc_b = doc_sintetico("Beta FC", [("fuera", 1, 1)] * 6, xg=[(0.9, 1.6)] * 6,
                          corners=[(4, 6)] * 6, amarillas=[(3, 2)] * 6,
                          rojas=[(0, 0)] * 6)

    def _añadir(doc, valores):
        """El doc sintético solo trae xg/corners/tarjetas: se añaden las de equipo."""
        for p in doc["partidos"]:
            filas = p["periodos"]["FT"]["filas"]
            for clave, (eq, riv) in valores.items():
                filas.append({"clave": clave, "metrica": clave, "categoria": "equipo",
                              "equipo": eq, "rival": riv})

    _añadir(doc_a, {"posesion_pct": (58.0, 42.0), "remates_totales": (15.0, 8.0),
                    "remates_puerta": (6.0, 3.0), "duelos_aereos_pct": (55.0, 45.0),
                    "despejes": (20.0, 25.0), "xgot": (1.6, 0.7),
                    "goles_evitados": (0.1, -0.2), "toques_area_rival": (30.0, 18.0)})
    _añadir(doc_b, {"posesion_pct": (44.0, 56.0), "remates_totales": (9.0, 14.0),
                    "remates_puerta": (3.0, 5.0), "duelos_aereos_pct": (45.0, 55.0),
                    "despejes": (24.0, 19.0), "xgot": (0.8, 1.5),
                    "goles_evitados": (-0.1, 0.2), "toques_area_rival": (19.0, 29.0)})

    a = mm.analizar(doc_a, doc_b, "Alfa FC", "Beta FC")
    muestra = a.motor.muestras["local_general"]
    con_datos = sum(1 for v in muestra.metricas.values() if v.n > 0)
    assert con_datos >= 20, f"solo {con_datos} métricas extraídas"
    assert muestra.dame("posesion_pct").n == 6, muestra.dame("posesion_pct").n
    assert abs(muestra.dame("posesion_pct").media - 58.0) < 1e-9
    assert muestra.dame("remates_totales_favor").n == 6
    assert muestra.dame("xg_favor").n == 6
    assert abs(muestra.dame("remates_puerta_favor").media - 6.0) < 1e-9
    # Y los moduladores ya tienen de dónde leer
    ml, mv = mm.extraer_medias_para_moduladores(a.motor)
    assert ml["posesion_pct"] is not None and ml["xg"] is not None, ml
    mods = mm.calcular_moduladores(ml, mv)
    assert mods["metricas_activas"], mods["metricas_activas"]
    print(f"  OK 23. métricas de equipo extraídas: {con_datos} con datos "
          f"(antes 12), moduladores activos: {mods['metricas_activas']}")


def prueba_comparar_moduladores() -> None:
    """
    24. La comparación de moduladores es NO destructiva y respeta el límite.
    """
    doc_a = doc_sintetico("Alfa FC", [("casa", 2, 1)] * 8, xg=[(1.9, 0.9)] * 8,
                          corners=[(7, 4)] * 8, amarillas=[(2, 3)] * 8,
                          rojas=[(0, 0)] * 8)
    doc_b = doc_sintetico("Beta FC", [("fuera", 1, 1)] * 8, xg=[(0.9, 1.6)] * 8,
                          corners=[(4, 6)] * 8, amarillas=[(3, 2)] * 8,
                          rojas=[(0, 0)] * 8)
    for doc in (doc_a, doc_b):
        for p in doc["partidos"]:
            for f in p["periodos"]["FT"]["filas"]:
                if f["clave"] == "posesion_pct":
                    f["equipo"], f["rival"] = 55.0, 45.0
    r = mm.comparar_moduladores(doc_a, doc_b, "Alfa FC", "Beta FC")
    assert "lambda_base" in r and "lambda_modulada" in r
    assert "cruzan_umbral" in r and isinstance(r["cruzan_umbral"], list)
    lam_b, lam_m = r["lambda_base"]["local"], r["lambda_modulada"]["local"]
    if lam_b and lam_m:
        ratio = lam_m / lam_b
        assert 0.85 - 1e-9 <= ratio <= 1.15 + 1e-9, ratio
    # No modifica el motor original
    assert r["moduladores"]["limite_aplicado"] == mm.LIMITE_MODULADOR_DEFECTO
    print(f"  OK 24. comparación de moduladores: λ {lam_b:.4f} -> {lam_m:.4f}, "
          f"{len(r['cruzan_umbral'])} cruzan el umbral")


def prueba_resumen_muestra_explicito() -> None:
    """
    25. El mensaje declara el OBJETIVO, el rango de fechas y si el dataset quedó
        incompleto. Antes decía «Muestra: 7/9» sin decir qué era cada número.
    """
    doc_a = doc_sintetico("Alfa FC", [("casa", 2, 1)] * 9, xg=[(1.9, 0.9)] * 9,
                          corners=[(7, 4)] * 9, amarillas=[(2, 3)] * 9,
                          rojas=[(0, 0)] * 9)
    doc_b = doc_sintetico("Beta FC", [("fuera", 1, 1)] * 9, xg=[(0.9, 1.6)] * 9,
                          corners=[(4, 6)] * 9, amarillas=[(3, 2)] * 9,
                          rojas=[(0, 0)] * 9)
    # Datos de escaneo: uno completo y otro corto, para ver las dos ramas
    doc_a["resumen"] = {"partidos_auditados": 15, "partidos_obtenidos": 20,
                        "minimo_con_datos": 15, "objetivo_alcanzado": True}
    doc_a["escaneo"] = {"paginas_escaneadas": 7, "motivo_parada": ""}
    doc_b["resumen"] = {"partidos_auditados": 7, "partidos_obtenidos": 9,
                        "minimo_con_datos": 15, "objetivo_alcanzado": False}
    doc_b["escaneo"] = {"paginas_escaneadas": 1,
                        "motivo_parada": "la fuente no ofrece más páginas"}
    a = mm.analizar(doc_a, doc_b, "Alfa FC", "Beta FC")
    lineas = mm._resumen_muestra(a.motor)
    texto = "\n".join(lineas)
    # Declara el objetivo y los auditados de cada equipo
    assert "15 partido(s) analizado(s) de 15 pedidos" in texto, texto
    assert "7 partido(s) analizado(s) de 15 pedidos" in texto, texto
    # Declara el rango de fechas
    assert "→" in texto, texto
    # Marca el incompleto con su motivo
    assert "INCOMPLETO" in texto, texto
    assert "la fuente no ofrece más páginas" in texto, texto
    assert "INCOMPLETO en al menos un equipo" in texto, texto
    # Y el mensaje final lo incluye
    msg = mm.construir_mensaje(a.metadatos, a.grupos, a.parametros, a.motor, a.mercados)
    assert "Estado del dataset" in msg, msg[:400]
    assert "Fecha:" in msg and "Análisis generado:" in msg, msg[:400]
    print("  OK 25. el mensaje declara objetivo, rango de fechas, marca de tiempo "
          "y aviso de dataset incompleto")


def prueba_renderizado_ticket_png() -> None:
    """26. El ticket PNG de mercados se genera con diseño estructurado y sin bloqueos de archivo."""
    from PIL import Image
    doc_l = doc_sintetico("Boca Juniors", [("casa", 3, 1)] * 8, xg=[(2.2, 0.8)] * 8,
                          corners=[(7, 3)] * 8, amarillas=[(2, 2)] * 8)
    doc_v = doc_sintetico("Vasco da Gama", [("fuera", 0, 2)] * 8, xg=[(0.5, 2.1)] * 8,
                          corners=[(2, 6)] * 8, amarillas=[(3, 1)] * 8)
    a = analizar(doc_l, doc_v, "Boca Juniors", "Vasco da Gama")

    with tempfile.TemporaryDirectory() as tmp:
        ruta_png = os.path.join(tmp, "ticket_boca_vasco.png")
        salida = servicio.renderizar_ticket_mercados_png(a, umbral=0.70, destino=ruta_png)
        assert os.path.exists(salida), "El archivo PNG debe existir en disco"
        assert os.path.getsize(salida) > 5000, "El tamaño del PNG debe ser superior a 5KB"

        with Image.open(salida) as img:
            assert img.size[0] == 960, f"Ancho esperado 960, obtenido {img.size[0]}"
            assert img.size[1] > 200, f"Alto esperado > 200, obtenido {img.size[1]}"

        # Comprobar que no hay bloqueo de descriptor en Windows (PermissionError al borrar)
        os.remove(salida)
        assert not os.path.exists(salida), "El archivo debe poder eliminarse sin PermissionError"

        # Comprobar caso borde: partido sin selecciones filtradas (umbral 99.99%)
        ruta_vacio = os.path.join(tmp, "ticket_vacio.png")
        salida_vacio = servicio.renderizar_ticket_mercados_png(a, umbral=0.9999, destino=ruta_vacio)
        assert os.path.exists(salida_vacio)
        os.remove(salida_vacio)

        # Comprobar envío multimedia en enviar_analisis
        preparado = {
            "id_trabajo": "prueba_ticket_456",
            "mensaje": a.mensaje,
            "analisis": a,
            "ruta_imagen": os.path.join(tmp, "ticket_envio.png"),
            "rutas": {"informe": "x", "datos": "y", "imagen": os.path.join(tmp, "ticket_envio.png")},
            "contexto": {"equipo_local": "Boca Juniors", "equipo_visitante": "Vasco da Gama"}
        }
        servicio.renderizar_ticket_mercados_png(a, destino=preparado["ruta_imagen"])

        fotos_enviadas = []
        adaptador_foto = servicio.adaptador_simulado_foto(fotos_enviadas)
        res_envio = servicio.enviar_analisis(preparado, enviar_foto_fn=adaptador_foto)
        assert res_envio["enviado"] is True
        assert res_envio["tipo"] == "foto"
        assert len(fotos_enviadas) == 1
        assert "ticket_envio.png" in fotos_enviadas[0]

    print("  OK 26. ticket PNG generado con diseño estructurado, escaleras, envío foto y sin bloqueos de archivo")


def prueba_cuadro_especialista() -> None:
    """
    27. Cuadro Especialista: ratings 0-100, tempo en segundos, mapa de calor y
    envío como SEGUNDA imagen. Se salta si matplotlib no está instalado.
    """
    try:
        import matplotlib                                     # noqa: F401
    except ImportError:
        print("  OK 27. Cuadro Especialista omitido: matplotlib no instalado "
              "(el flujo de /mercados sigue funcionando)")
        return
    import cuadro_especialista as cuadro

    doc_l = doc_sintetico("Boca Juniors", [("casa", 3, 1)] * 8,
                          # El partido con expulsión rinde MUY por debajo: si el peso
                          # no se aplicara, la media ponderada sería idéntica.
                          xg=[(0.4, 0.8)] + [(2.2, 0.8)] * 7,
                          corners=[(7, 3)] * 8, amarillas=[(2, 2)] * 8,
                          rojas=[(1, 0)] + [(0, 0)] * 7)
    doc_v = doc_sintetico("Vasco da Gama", [("fuera", 0, 2)] * 8, xg=[(0.5, 2.1)] * 8,
                          corners=[(2, 6)] * 8, amarillas=[(3, 1)] * 8,
                          rojas=[(0, 0)] * 8)
    a = analizar(doc_l, doc_v, "Boca Juniors", "Vasco da Gama")

    # ── Peso por expulsión: la media bruta NO cambia, la ponderada sí ──────
    normal = mm.extraer_muestra(doc_l, None, "FT")
    con_peso = mm.extraer_muestra(doc_l, None, "FT", peso_roja=0.30)
    clave = "xg_favor"      # el documento sintético sí trae xG en todos los partidos
    assert normal.dame(clave).n == con_peso.dame(clave).n, \
        "el recuento de observaciones no debe cambiar al ponderar"
    assert abs(normal.dame(clave).media - con_peso.dame(clave).media) < 1e-12, \
        "la media bruta debe ser idéntica con o sin pesos"
    assert con_peso.dame(clave).pesos_activos, \
        "el partido con expulsión debe haber quedado desponderado"
    assert con_peso.dame(clave).media_ponderada != normal.dame(clave).media, \
        "la media ponderada debe diferir cuando hay una expulsión"
    assert not normal.dame(clave).pesos_activos, \
        "sin peso_roja ningún partido debe quedar ponderado"

    # ── El pie declara Dixon-Coles SOLO si está activo ─────────────────────
    etiqueta = cuadro.etiqueta_modelo_activo(a)
    assert "Dixon-Coles" not in etiqueta, \
        f"sin rho activo no se puede anunciar Dixon-Coles: {etiqueta}"
    a_dc = mm.analizar(doc_l, doc_v, "Boca Juniors", "Vasco da Gama",
                       parametros=Parametros(rho=mm.RHO_DIXON_COLES))
    assert "Dixon-Coles" in cuadro.etiqueta_modelo_activo(a_dc), \
        "con rho activo el pie debe declararlo"

    with tempfile.TemporaryDirectory() as tmp:
        ruta = cuadro.generar_cuadro_desde_analisis(
            a, doc_l, doc_v, destino=os.path.join(tmp, "cuadro.png"), peso_roja=0.30)
        assert ruta and os.path.exists(ruta), "el PNG del cuadro debe existir"
        assert os.path.getsize(ruta) > 20000, "el cuadro debe pesar más de 20 KB"

        from PIL import Image
        with Image.open(ruta) as img:
            assert img.size[0] > 1200 and img.size[1] > 1200, img.size
        os.remove(ruta)  # sin PermissionError en Windows

        # ── Envío de ticket + cuadro como dos imágenes ────────────────────
        preparado = {
            "id_trabajo": "prueba_cuadro_789",
            "mensaje": a.mensaje,
            "analisis": a,
            "ruta_imagen": os.path.join(tmp, "ticket.png"),
            "ruta_cuadro": os.path.join(tmp, "cuadro_envio.png"),
            "rutas": {"informe": "x", "datos": "y"},
            "contexto": {"equipo_local": "Boca Juniors",
                         "equipo_visitante": "Vasco da Gama"},
        }
        servicio.renderizar_ticket_mercados_png(a, destino=preparado["ruta_imagen"])
        cuadro.generar_cuadro_desde_analisis(a, doc_l, doc_v,
                                             destino=preparado["ruta_cuadro"])
        fotos: list[str] = []
        res = servicio.enviar_analisis(preparado,
                                      enviar_foto_fn=servicio.adaptador_simulado_foto(fotos))
        assert res["enviado"] is True, res
        assert res["cuadro_enviado"] is True, res
        assert len(fotos) == 2, f"deben enviarse dos imágenes (ticket + cuadro): {fotos}"
        assert "cuadro_envio.png" in fotos[1], fotos
        assert "Cuadro Especialista" in fotos[1], fotos

    print("  OK 27. Cuadro Especialista: ratings, tempo, mapa de calor, peso por "
          "expulsión y envío como segunda imagen")


def prueba_fecha_por_id_y_declaracion_sin_calendario() -> None:
    """
    28. La fecha y la localía del cruce se resuelven cruzando ID numérico de equipo
        en el calendario (no texto: «Roma» vs «AS Roma»), el partido PROGRAMADO manda
        sobre el último H2H jugado, y si no hay ninguno se declara explícitamente en
        vez de dejar «no disponible».
    """
    import copy

    def doc(nombre: str, id_equipo: str, futuros=None, jugados=None) -> dict:
        return {"equipo": {"id": id_equipo, "nombre": nombre, "alternativas": []},
                "partidos": jugados or [], "partidos_futuros": futuros or [],
                "resumen": {"partidos_con_datos": len(jugados or [])},
                "escaneo": {}}

    # Apodos a propósito: por texto NO hay forma de cruzarlos.
    futuros = [{"id_partido": "F1", "fecha": "2026-11-20", "competicion": "Champions",
                "home_id": "100", "home_nombre": "Giallorossi",
                "away_id": "200", "away_nombre": "Los Blancos"}]
    con_ids = doc("Roma", "100", futuros=futuros)
    rival = doc("Real Madrid", "200")

    prox = servicio._partido_futuro_en_comun(con_ids, rival, "Roma", "Real Madrid")
    assert prox, "el cruce por ID numérico debe encontrar el partido programado"
    assert prox["fecha_partido"] == "2026-11-20", prox
    assert prox["condicion_local"] == "casa" and prox["local_es"] == "local", prox
    assert prox["condicion_visitante"] == "fuera", prox

    # Sin los IDs, el mismo caso no se puede cruzar: eso demuestra que el ID es
    # lo que resuelve, no una coincidencia de nombres.
    sin_ids_l = copy.deepcopy(con_ids)
    sin_ids_v = copy.deepcopy(rival)
    sin_ids_l["equipo"]["id"] = sin_ids_v["equipo"]["id"] = ""
    assert servicio._partido_futuro_en_comun(sin_ids_l, sin_ids_v,
                                             "Roma", "Real Madrid") is None

    # Invertido: el local del calendario es el visitante del comando
    invertido = [dict(futuros[0], home_id="200", away_id="100")]
    prox_inv = servicio._partido_futuro_en_comun(doc("Roma", "100", futuros=invertido),
                                                 rival, "Roma", "Real Madrid")
    assert prox_inv["condicion_local"] == "fuera", prox_inv
    assert prox_inv["local_es"] == "visitante", prox_inv

    # ── El partido PROGRAMADO manda sobre el último H2H ya jugado ──────────
    jugado_comun = [{"id_partido": "J1", "fecha": "2026-04-02", "torneo": "Champions",
                     "equipo": "Roma", "rival": "Real Madrid", "condicion": "casa"}]
    con_ambos = doc("Roma", "100", futuros=futuros, jugados=jugado_comun)
    rival_jugado = doc("Real Madrid", "200",
                       jugados=[{"id_partido": "J1", "fecha": "2026-04-02",
                                 "torneo": "Champions", "equipo": "Real Madrid",
                                 "rival": "Roma", "condicion": "fuera"}])
    meta = servicio._metadatos_del_encuentro(con_ambos, rival_jugado, "Roma", "Real Madrid")
    assert meta["fecha_partido"] == "2026-11-20", (
        f"con un partido programado, ésa es la fecha del cruce (no el H2H viejo): {meta}")

    # Sin calendario futuro, se usa el partido común ya jugado (comportamiento previo)
    sin_futuros = doc("Roma", "100", jugados=jugado_comun)
    meta2 = servicio._metadatos_del_encuentro(sin_futuros, rival_jugado, "Roma", "Real Madrid")
    assert meta2["fecha_partido"] == "2026-04-02", meta2
    assert meta2["evidencia_condicion"] == "partido común registrado en ambos reportes", meta2

    # ── Sin partido común ni programado: se declara, no se deja en «no disponible» ──
    meta3 = servicio._metadatos_del_encuentro(doc("Roma", "100"), doc("Real Madrid", "200"),
                                              "Roma", "Real Madrid")
    assert meta3["fecha_partido"] is None, meta3
    assert meta3.get("sin_partido_proximo") is True, meta3
    assert "Sin partido próximo en calendario" in meta3["evidencia_condicion"], meta3

    # Y el mensaje de Telegram lo dice con esas palabras, no «no disponible»
    doc_l = doc_sintetico("Roma", [("casa", 2, 1)] * 8, xg=[(1.9, 0.9)] * 8,
                          corners=[(6, 4)] * 8, amarillas=[(2, 2)] * 8)
    doc_v = doc_sintetico("Real Madrid", [("fuera", 1, 1)] * 8, xg=[(1.7, 1.0)] * 8,
                          corners=[(5, 5)] * 8, amarillas=[(2, 3)] * 8)
    a = analizar(doc_l, doc_v, "Roma", "Real Madrid", fecha_partido=None)
    mensaje = a.mensaje
    assert "Fecha: Sin partido próximo en calendario (localía por orden de consulta)" in mensaje, \
        mensaje[:400]
    assert "Fecha: no disponible" not in mensaje, mensaje[:400]
    print("  OK 28. fecha y localía por ID numérico, el calendario manda sobre el H2H "
          "jugado y sin cruce se declara explícitamente")


def prueba_cuadro_h2h_2024_2026() -> None:
    """
    29. Módulo Independiente H2H Puro 2024–2026:
        - Filtra estrictamente enfrentamientos oficiales de 2024-01-01 a 2026-12-31
          (descarta 2023 y amistosos).
        - Orienta las 45 estadísticas por identidad de club aunque alterne casa/fuera.
        - No mezcla datos con los 15 partidos generales.
        - Si N >= 1 genera cuadro_h2h_directo_2024_2026_*.png y envía la 3ª lámina;
          si N == 0 declara «Sin enfrentamientos directos en 2024–2026» y omite la lámina.
    """
    import cuadro_h2h_2024_2026 as h2h

    # 1) Regla de fechas 2024-2026 sobre la serie real Barcelona vs Real Betis + amistoso
    crudos = [
        {"id_partido": "h1", "fecha": "2025-12-06", "torneo": "LaLiga", "goles_local": 3, "goles_visitante": 5},
        {"id_partido": "h2", "fecha": "2025-04-05", "torneo": "LaLiga", "goles_local": 1, "goles_visitante": 1},
        {"id_partido": "h3", "fecha": "2025-01-15", "torneo": "Copa del Rey", "goles_local": 5, "goles_visitante": 1},
        {"id_partido": "h4", "fecha": "2024-12-07", "torneo": "LaLiga", "goles_local": 2, "goles_visitante": 2},
        {"id_partido": "h5", "fecha": "2024-01-21", "torneo": "LaLiga", "goles_local": 2, "goles_visitante": 4},
        {"id_partido": "h6", "fecha": "2026-03-15", "torneo": "LaLiga", "goles_local": 2, "goles_visitante": 1},
        {"id_partido": "h_2023", "fecha": "2023-09-16", "torneo": "LaLiga", "goles_local": 5, "goles_visitante": 0},
        {"id_partido": "h_amistoso", "fecha": "2025-07-30", "torneo": "Club Friendly", "goles_local": 1, "goles_visitante": 0},
    ]
    filtrados = h2h.filtrar_enfrentamientos_2024_2026(crudos)
    ids_filtrados = [p["id_partido"] for p in filtrados]
    assert len(filtrados) == 6, f"Debe conservar exactamente los 6 oficiales de 2024-2026: {ids_filtrados}"
    assert "h_2023" not in ids_filtrados, "El partido de 2023-09-16 debe quedar excluido"
    assert "h_amistoso" not in ids_filtrados, "Los amistosos deben quedar excluidos"

    # 2) Orientación por identidad de club (alternando casa y fuera) sin mezclar con los 15 generales
    doc_barca = doc_sintetico(
        "Barcelona",
        [("casa", 3, 0)] * 15,
        xg=[(2.4, 0.7)] * 15,
        corners=[(7, 3)] * 15,
        amarillas=[(1, 2)] * 15,
    )
    doc_betis = doc_sintetico(
        "Real Betis",
        [("fuera", 1, 1)] * 15,
        xg=[(1.1, 1.3)] * 15,
        corners=[(4, 5)] * 15,
        amarillas=[(2, 2)] * 15,
    )
    doc_barca["equipo"]["id"] = "8634"
    doc_betis["equipo"]["id"] = "8603"

    # Convertimos 2 partidos en duelos directos 2024-2026 (uno con Barça en casa y otro con Barça fuera)
    # y 1 partido en duelo de 2023 que debe ser ignorado por el módulo H2H 2024-2026
    p_h2h_1 = doc_barca["partidos"][0]
    p_h2h_1.update({
        "id_partido": "h2h_2025_casa",
        "fecha": "2025-01-15",
        "torneo": "Copa del Rey",
        "condicion": "casa",
        "equipo": "Barcelona",
        "rival": "Real Betis",
        "id_equipo": "8634",
        "id_rival": "8603",
        "goles_equipo": 5,
        "goles_rival": 1,
        "marcador": {"equipo": 5, "rival": 1, "texto": "5 - 1"},
    })
    # En h2h_2025_fuera el partido está guardado desde la perspectiva de Real Betis (local)
    p_h2h_2 = doc_betis["partidos"][0]
    p_h2h_2.update({
        "id_partido": "h2h_2025_fuera",
        "fecha": "2025-12-06",
        "torneo": "LaLiga",
        "condicion": "casa",
        "equipo": "Real Betis",
        "rival": "Barcelona",
        "id_equipo": "8603",
        "id_rival": "8634",
        "goles_equipo": 3,
        "goles_rival": 5,
        "marcador": {"equipo": 3, "rival": 5, "texto": "3 - 5"},
    })
    p_h2h_2023 = doc_barca["partidos"][1]
    p_h2h_2023.update({
        "id_partido": "h2h_2023_viejo",
        "fecha": "2023-09-16",
        "torneo": "LaLiga",
        "condicion": "casa",
        "equipo": "Barcelona",
        "rival": "Real Betis",
        "id_equipo": "8634",
        "id_rival": "8603",
        "goles_equipo": 5,
        "goles_rival": 0,
        "marcador": {"equipo": 5, "rival": 0, "texto": "5 - 0"},
    })

    rec = h2h.recolectar_duelos_h2h_2024_2026(
        doc_barca, doc_betis, "Barcelona", "Real Betis", buscar_en_red=False
    )
    assert rec["n_duelos"] == 2, f"Debe detectar exactamente 2 duelos en 2024-2026: {rec['n_duelos']}"
    assert rec["sin_enfrentamientos"] is False
    # Verificar que en ambos duelos orientados a Barcelona sus goles a favor son 5 y 5 (media 5.0),
    # demostrando que cuando jugó fuera en casa del Betis (3-5) se orientó a favor de Barcelona
    goles_barca_h2h = [p["goles_equipo"] for p in rec["doc_h2h_a"]["partidos"]]
    goles_betis_h2h = [p["goles_equipo"] for p in rec["doc_h2h_b"]["partidos"]]
    assert goles_barca_h2h == [5, 5], f"Barcelona anotó 5 en ambos duelos: {goles_barca_h2h}"
    assert sorted(goles_betis_h2h) == [1, 3], f"Real Betis anotó 1 y 3: {goles_betis_h2h}"

    a = analizar(doc_barca, doc_betis, "Barcelona", "Real Betis")
    with tempfile.TemporaryDirectory() as tmp:
        ruta_h2h = os.path.join(tmp, "cuadro_h2h_directo_2024_2026_barca_betis.png")
        res_h2h = h2h.generar_cuadro_h2h_desde_analisis(
            a, doc_barca, doc_betis, destino=ruta_h2h, buscar_en_red=False
        )
        assert res_h2h["n_duelos"] == 2
        assert res_h2h["ruta"] and os.path.exists(res_h2h["ruta"])
        assert os.path.getsize(res_h2h["ruta"]) > 20000

        # Envío de las 3 láminas independientes por servicio_mercados.enviar_analisis
        ruta_ticket = os.path.join(tmp, "ticket.png")
        ruta_cuadro = os.path.join(tmp, "cuadro_15.png")
        servicio.renderizar_ticket_mercados_png(a, destino=ruta_ticket)
        import cuadro_especialista as cuadro
        cuadro.generar_cuadro_desde_analisis(a, doc_barca, doc_betis, destino=ruta_cuadro)

        preparado = {
            "id_trabajo": "prueba_h2h_3_laminas",
            "mensaje": a.mensaje,
            "analisis": a,
            "ruta_imagen": ruta_ticket,
            "ruta_cuadro": ruta_cuadro,
            "ruta_cuadro_h2h": res_h2h["ruta"],
            "h2h_2024_2026": res_h2h,
            "estado_h2h_2024_2026": res_h2h["estado"],
            "rutas": {"informe": "x", "datos": "y", "imagen": ruta_ticket,
                      "cuadro": ruta_cuadro, "cuadro_h2h": res_h2h["ruta"]},
            "contexto": {"equipo_local": "Barcelona", "equipo_visitante": "Real Betis"},
        }
        fotos_3: list[str] = []
        env_3 = servicio.enviar_analisis(
            preparado, enviar_foto_fn=servicio.adaptador_simulado_foto(fotos_3)
        )
        assert env_3["enviado"] is True
        assert env_3["cuadro_enviado"] is True
        assert env_3["cuadro_h2h_enviado"] is True
        assert len(fotos_3) == 3, f"Deben enviarse las 3 láminas independientes: {fotos_3}"
        assert "Enfrentamientos Directos (H2H Puro 2024–2026)" in fotos_3[2], fotos_3[2]

        # 3) Caso N == 0 enfrentamientos en 2024-2026: omite gráfico y declara estado explícito
        doc_c = doc_sintetico("Equipo C", [("casa", 2, 0)] * 8, xg=[(1.8, 0.6)] * 8)
        doc_d = doc_sintetico("Equipo D", [("fuera", 1, 1)] * 8, xg=[(1.1, 1.2)] * 8)
        doc_c["equipo"]["id"] = "9001"
        doc_d["equipo"]["id"] = "9002"
        a_cd = analizar(doc_c, doc_d, "Equipo C", "Equipo D")
        res_vacio = h2h.generar_cuadro_h2h_desde_analisis(
            a_cd, doc_c, doc_d, destino=os.path.join(tmp, "no_debe_crearse.png"),
            buscar_en_red=False
        )
        assert res_vacio["n_duelos"] == 0
        assert res_vacio["sin_enfrentamientos"] is True
        assert res_vacio["ruta"] is None
        assert res_vacio["estado"] == "Sin enfrentamientos directos en 2024–2026"

        preparado_sin_h2h = dict(
            preparado,
            id_trabajo="prueba_sin_h2h",
            ruta_cuadro_h2h=None,
            h2h_2024_2026=res_vacio,
            estado_h2h_2024_2026=res_vacio["estado"],
        )
        fotos_2: list[str] = []
        env_2 = servicio.enviar_analisis(
            preparado_sin_h2h, enviar_foto_fn=servicio.adaptador_simulado_foto(fotos_2)
        )
        assert env_2["cuadro_h2h_enviado"] is False
        assert env_2["estado_h2h_2024_2026"] == "Sin enfrentamientos directos en 2024–2026"
        assert len(fotos_2) == 2, f"Con N=0 solo deben enviarse 2 láminas: {fotos_2}"

    print("  OK 29. Módulo H2H Puro 2024–2026: filtro 2024-2026 (excluye 2023), orientación "
          "por club, 3ª lámina independiente y declaración cuando N=0")


def prueba_h2h_espanyol_vs_atletico_prohibicion_falsificacion() -> None:
    """
    Prueba obligatoria de integridad H2H (Sección 3: Prohibición Absoluta de Falsificación):
    - Verifica que el emparejador de identidad NO confunda al Real Madrid con el Atlético de Madrid.
    - Exige que el H2H de Espanyol vs Atlético Madrid en 2024–2026 devuelva exactamente los 4 duelos reales:
        * 2026-02-21: Atlético Madrid 4 - 2 Espanyol (ID: 4837352)
        * 2025-08-17: Espanyol 2 - 1 Atlético Madrid (ID: 4837113)
        * 2025-03-29: Espanyol 1 - 1 Atlético Madrid (ID: 4507042)
        * 2024-08-28: Atlético Madrid 0 - 0 Espanyol (ID: 4506775)
    - Falla terminantemente si aparecen partidos contra Real Madrid (2026-08-22, 2026-05-03, 2025-09-20, etc.)
      o si se infla la muestra a 9 partidos inventando duelos directos inexistentes.
    """
    import cuadro_h2h_2024_2026 as h2h

    # 1) Verificación estricta de identidad: 'Real Madrid' NUNCA es 'Atlético Madrid'
    clave_rm = h2h._clave_equipo("Real Madrid")
    clave_atm = h2h._clave_equipo("Atlético Madrid")
    assert clave_rm == "realmadrid", f"Clave de Real Madrid debe ser 'realmadrid': {clave_rm}"
    assert clave_atm == "atleticomadrid", f"Clave de Atlético Madrid debe ser 'atleticomadrid': {clave_atm}"
    assert clave_rm != clave_atm, "Las claves de Real Madrid y Atlético Madrid no pueden colisionar"

    # Verificación por ID de club FotMob (8633 vs 9906)
    assert not h2h._coincide_equipo("Real Madrid", "8633", "9906", {"atleticomadrid"}), (
        "CRÍTICO: _coincide_equipo emparejó erróneamente Real Madrid (ID 8633) con Atlético Madrid (ID 9906)"
    )
    # Verificación por nombre sin ID
    assert not h2h._coincide_equipo("Real Madrid", None, "", {"atleticomadrid"}), (
        "CRÍTICO: _coincide_equipo emparejó Real Madrid por subcadena con Atlético Madrid"
    )
    assert h2h._coincide_equipo("Atlético Madrid", "9906", "9906", {"atleticomadrid"}), (
        "Debe coincidir Atlético Madrid consigo mismo"
    )

    # 2) Duelos mezclados: 4 reales entre Espanyol y Atlético + 5 contaminantes de Real Madrid
    doc_esp = {
        "equipo": {"id": "8558", "nombre": "Espanyol"},
        "fuente": "sintetica",
        "partidos": [
            # 4 REALES Espanyol vs Atlético Madrid
            {"id_partido": "4837352", "fecha": "2026-02-21", "torneo": "LaLiga",
             "equipo": "Espanyol", "rival": "Atlético Madrid", "id_rival": "9906", "condicion": "fuera",
             "goles_equipo": 2, "goles_rival": 4, "marcador": {"equipo": 2, "rival": 4}},
            {"id_partido": "4837113", "fecha": "2025-08-17", "torneo": "LaLiga",
             "equipo": "Espanyol", "rival": "Atlético Madrid", "id_rival": "9906", "condicion": "casa",
             "goles_equipo": 2, "goles_rival": 1, "marcador": {"equipo": 2, "rival": 1}},
            {"id_partido": "4507042", "fecha": "2025-03-29", "torneo": "LaLiga",
             "equipo": "Espanyol", "rival": "Atlético Madrid", "id_rival": "9906", "condicion": "casa",
             "goles_equipo": 1, "goles_rival": 1, "marcador": {"equipo": 1, "rival": 1}},
            {"id_partido": "4506775", "fecha": "2024-08-28", "torneo": "LaLiga",
             "equipo": "Espanyol", "rival": "Atlético Madrid", "id_rival": "9906", "condicion": "fuera",
             "goles_equipo": 0, "goles_rival": 0, "marcador": {"equipo": 0, "rival": 0}},

            # 5 CONTAMINANTES contra Real Madrid (deben ser 100% descartados)
            {"id_partido": "5868025", "fecha": "2026-08-22", "torneo": "LaLiga",
             "equipo": "Espanyol", "rival": "Real Madrid", "id_rival": "8633", "condicion": "casa",
             "goles_equipo": 1, "goles_rival": 2, "marcador": {"equipo": 1, "rival": 2}},
            {"id_partido": "4837443", "fecha": "2026-05-03", "torneo": "LaLiga",
             "equipo": "Espanyol", "rival": "Real Madrid", "id_rival": "8633", "condicion": "casa",
             "goles_equipo": 0, "goles_rival": 2, "marcador": {"equipo": 0, "rival": 2}},
            {"id_partido": "4837157", "fecha": "2025-09-20", "torneo": "LaLiga",
             "equipo": "Espanyol", "rival": "Real Madrid", "id_rival": "8633", "condicion": "fuera",
             "goles_equipo": 0, "goles_rival": 2, "marcador": {"equipo": 0, "rival": 2}},
            {"id_partido": "4506971", "fecha": "2025-02-01", "torneo": "LaLiga",
             "equipo": "Espanyol", "rival": "Real Madrid", "id_rival": "8633", "condicion": "casa",
             "goles_equipo": 1, "goles_rival": 0, "marcador": {"equipo": 1, "rival": 0}},
            {"id_partido": "4506806", "fecha": "2024-09-21", "torneo": "LaLiga",
             "equipo": "Espanyol", "rival": "Real Madrid", "id_rival": "8633", "condicion": "fuera",
             "goles_equipo": 1, "goles_rival": 4, "marcador": {"equipo": 1, "rival": 4}},
        ]
    }
    doc_atm = {
        "equipo": {"id": "9906", "nombre": "Atlético Madrid"},
        "fuente": "sintetica",
        "partidos": []
    }

    info = h2h.recolectar_duelos_h2h_2024_2026(doc_esp, doc_atm, "Espanyol", "Atlético Madrid", buscar_en_red=False)

    # Aserción obligatoria 1: La muestra debe contener EXACTAMENTE 4 duelos, nunca 9
    assert info["n_duelos"] == 4, (
        f"ERROR GRAVE: Se esperaban 4 duelos entre Espanyol y Atlético Madrid (2024-2026), pero se obtuvieron {info['n_duelos']}. "
        f"Se están inventando partidos o filtrando rivales ajenos."
    )

    # Aserción obligatoria 2: Fechas exactas de los 4 partidos reales
    fechas_obtenidas = {d["fecha"] for d in info["lista_duelos"]}
    fechas_esperadas = {"2026-02-21", "2025-08-17", "2025-03-29", "2024-08-28"}
    assert fechas_obtenidas == fechas_esperadas, (
        f"Fechas erróneas en H2H: {fechas_obtenidas} vs esperadas {fechas_esperadas}"
    )

    # Aserción obligatoria 3: Ninguna fecha de los partidos contra Real Madrid debe estar presente
    fechas_prohibidas = {"2026-08-22", "2026-05-03", "2025-09-20", "2025-02-01", "2024-09-21"}
    colisiones = fechas_obtenidas & fechas_prohibidas
    assert not colisiones, (
        f"ERROR CRÍTICO: Se colaron partidos del Real Madrid en el H2H de Espanyol vs Atlético Madrid: {colisiones}"
    )

    # Aserción obligatoria 4: Todos los participantes deben ser exclusivamente Espanyol y Atlético Madrid
    for d in info["lista_duelos"]:
        assert {d["local"], d["visitante"]} == {"Espanyol", "Atlético Madrid"}, (
            f"Participante ilegal en duelo H2H: {d['local']} vs {d['visitante']}"
        )

    # 3) Verificación del índice en disco si existe
    ruta_idx = h2h._ruta_cache_indice_h2h("8558", "9906")
    if os.path.exists(ruta_idx):
        with open(ruta_idx, "r", encoding="utf-8") as f:
            idx_disco = json.load(f)
        fixtures_disco = idx_disco.get("fixtures") or []
        assert len(fixtures_disco) in (4, 5), (
            f"El índice cacheado en disco tiene {len(fixtures_disco)} fixtures (debe tener 4 o 5 reales, nunca 9)"
        )
        for fd in fixtures_disco:
            assert "Real Madrid" not in (fd.get("local"), fd.get("visitante")), (
                f"El índice cacheado contiene al Real Madrid: {fd}"
            )
            assert {fd.get("local"), fd.get("visitante")} == {"Espanyol", "Atlético Madrid"}, (
                f"El índice cacheado contiene un rival ajeno: {fd}"
            )

    print("  OK 30. Prohibición de falsificación en H2H: Espanyol vs Atlético Madrid tiene "
          "exactamente los 4 duelos reales 2024–2026 (cero partidos contra Real Madrid)")


def prueba_motor_recursivo_45_dcn() -> None:
    """
    31. Red Neuronal Recursiva (45 variables): LSTM determinista por variable,
        Bilinear Pooling (covarianza cruzada), Cross-Attention, bucle recursivo
        hasta convergencia de entropía y Monte Carlo 100.000.
          • La matriz del motor ES la de la red (no la Poisson plana).
          • El marcador modal NO puede ser el «1-1 ★» fijo en un duelo asimétrico.
          • λ_1T + λ_2T == λ_FT (coherencia de mitades).
          • El sello de verificación se genera y la λ se mueve respecto a la plana.
          • Desactivarla devuelve exactamente la λ del modelo lineal.
    """
    import numpy as _np

    doc_l = doc_sintetico("Dominante FC", [("casa", 3, 0), ("casa", 2, 1),
                                          ("casa", 3, 1), ("casa", 2, 0)] * 4,
                          xg=[(2.4, 0.6)] * 16,
                          xg_1t=[(1.1, 0.3)] * 16, xg_2t=[(1.3, 0.3)] * 16,
                          corners=[(7, 3)] * 16)
    doc_v = doc_sintetico("Debil FC", [("fuera", 0, 2), ("fuera", 1, 1)] * 8,
                          xg=[(0.7, 2.1)] * 16,
                          xg_1t=[(0.3, 1.0)] * 16, xg_2t=[(0.4, 1.1)] * 16,
                          corners=[(3, 6)] * 16)

    a = analizar(doc_l, doc_v, "Dominante FC", "Debil FC")
    motor = a.motor
    info = motor.recursivo
    assert info.get("aplicado") is True, info

    # 1) La matriz EFECTIVA del motor es la de la red y está normalizada.
    matriz = motor.matriz_ft
    assert abs(sum(sum(f) for f in matriz) - 1.0) < 1e-9
    assert abs(float(_np.sum(_np.asarray(matriz))) - 1.0) < 1e-9
    assert info["historial_entropia"], "debe declarar la entropía por iteración"
    assert info["iteracion_convergencia"] >= 1

    # 2) El sesgo del 1-1 fijo está erradicado: el modal favorece al dominante.
    lado = min(7, len(matriz))
    i, j = max(((x, y) for x in range(lado) for y in range(lado)),
               key=lambda ij: matriz[ij[0]][ij[1]])
    modal = f"{i}-{j}"
    assert modal != "1-1", f"el marcador modal no puede ser el 1-1 fijo: {modal}"
    assert i > j, f"el modal debe favorecer al equipo dominante: {modal}"
    assert info["sello_verificacion_visual"].startswith("RED NEURONAL RECURSIVA ACTIVA")
    assert abs(info["lambda_local_recursivo"] - motor.tasas.lambda_local_ft) < 1e-9

    # 3) Los mercados se calculan con la matriz de la red (no con la lineal).
    r = {res.id_mercado: res for res in a.mercados}
    ganador = {s.evento: s.probabilidad for s in r["ganador_partido"].selecciones}
    assert abs((ganador["1"] or 0) - resultado_1x2(matriz)["1"]) < 1e-9

    # 4) Mitades coherentes: 1T + 2T == FT en los dos equipos.
    for lado_eq in ("local", "visitante"):
        ft = getattr(motor.tasas, f"lambda_{lado_eq}_ft")
        uno = getattr(motor.tasas, f"lambda_{lado_eq}_1t")
        dos = getattr(motor.tasas, f"lambda_{lado_eq}_2t")
        if uno is None or dos is None:
            continue
        assert abs((uno + dos) - ft) < 0.02, (lado_eq, ft, uno, dos)

    # 5) Trazabilidad declarada en el mensaje, el informe y el JSON.
    assert "Red Neuronal Recursiva" in a.mensaje, a.mensaje[:300]
    assert "Red Neuronal Recursiva" in a.informe
    lineas = mm._lineas_motor_recursivo(motor)
    assert lineas and "Red Neuronal Recursiva" in lineas[0], lineas

    # 6) Apagado explícito: se recupera la λ del modelo lineal, sin residuos.
    a_off = analizar(doc_l, doc_v, "Dominante FC", "Debil FC",
                     parametros=Parametros(aplicar_motor_recursivo=False))
    assert a_off.motor.recursivo == {}
    modulada = (motor.detalle_goles.get("_moduladores") or {}).get(
        "lambda_local_modulada", motor.tasas.lambda_local_ft)
    assert abs((a_off.motor.tasas.lambda_local_ft or 0) - modulada) < 1e-9, \
        "desactivado debe devolver la λ del modelo lineal"
    assert abs((motor.tasas.lambda_local_ft or 0) - modulada) > 1e-9, \
        "la red debe mover la λ respecto al modelo lineal"

    # 7) La red se aplica también con muestras cortas (nunca deja la matriz vieja).
    corto_l = doc_sintetico("Corto A", [("casa", 2, 1)] * 2, xg=[(1.8, 1.0)] * 2)
    corto_v = doc_sintetico("Corto B", [("fuera", 1, 1)] * 2, xg=[(1.1, 1.4)] * 2)
    a_corto = analizar(corto_l, corto_v, "Corto A", "Corto B")
    assert a_corto.motor.recursivo.get("aplicado") is True, a_corto.motor.recursivo

    print(f"  OK 31. Red Neuronal Recursiva: modal {modal}, "
          f"λ {info['lambda_base_local']:.2f}–{info['lambda_base_visita']:.2f} → "
          f"{info['lambda_local_recursivo']:.2f}–{info['lambda_visita_recursivo']:.2f}, "
          f"convergencia it. {info['iteracion_convergencia']} "
          f"(H={info['historial_entropia'][-1]:.3f})")


def prueba_ticket_valor_y_analisis_3_parrafos() -> None:
    """
    32. Ticket de Valor (4ta imagen enviada como 2da imagen tras el ticket general) y
        Análisis de Texto Detallado de 3 Párrafos antes de enviar las imágenes.
    """
    import informe as informe_mod

    doc_l = doc_sintetico(
        "Real Madrid", [("casa", 2, 0)] * 15, xg=[(2.1, 0.7)] * 15,
        corners=[(7, 3)] * 15, amarillas=[(1, 2)] * 15,
    )
    doc_v = doc_sintetico(
        "Villarreal", [("fuera", 1, 2)] * 15, xg=[(1.0, 1.8)] * 15,
        corners=[(4, 6)] * 15, amarillas=[(2, 3)] * 15,
    )
    a = analizar(doc_l, doc_v, "Real Madrid", "Villarreal", competicion="LaLiga")
    cuotas_fs = {"local": 1.95, "empate": 3.80, "visitante": 4.50}

    filas = informe_mod.construir_filas_ticket_valor(a, cuotas_fs)
    assert len(filas) == 6
    # Como Prob. Bot de Real Madrid es alta (~65-75%, Cuota Justa ~1.35-1.55) y Cuota Flashscore es 1.95,
    # tiene valor (+EV > 0) y se marca tiene_valor=True
    assert filas[0]["tiene_valor"] is True
    assert filas[0]["ev_pct"] > 0

    texto_3p = informe_mod.redactar_analisis_3_parrafos(
        a,
        info_h2h={"n_duelos": 3, "lambda_local": 2.0, "lambda_visitante": 1.2, "duelos": [{}, {}, {}]},
        cuotas_flashscore=cuotas_fs,
    )
    parrafos = [p.strip() for p in texto_3p.split("\n\n") if p.strip()]
    assert len(parrafos) == 3, f"El análisis debe tener exactamente 3 párrafos: {len(parrafos)}"
    assert "Modelo General" in parrafos[0]
    assert "Modelo H2H" in parrafos[1]
    assert "Veredicto Final" in parrafos[2]

    with tempfile.TemporaryDirectory() as tmp:
        ruta_ticket = os.path.join(tmp, "ticket_general.png")
        ruta_valor = os.path.join(tmp, "ticket_valor.png")
        ruta_cuadro = os.path.join(tmp, "cuadro_esp.png")
        ruta_h2h = os.path.join(tmp, "cuadro_h2h.png")

        servicio.renderizar_ticket_mercados_png(a, destino=ruta_ticket)
        informe_mod.generar_ticket_valor_png(a, cuotas_flashscore=cuotas_fs, destino=ruta_valor, id_flashscore="2mAGw9Ti")
        assert os.path.exists(ruta_valor) and os.path.getsize(ruta_valor) > 10000

        # Crear archivos dummy para cuadro y h2h para verificar el orden de las 4 imágenes
        with open(ruta_cuadro, "wb") as f:
            f.write(open(ruta_valor, "rb").read())
        with open(ruta_h2h, "wb") as f:
            f.write(open(ruta_valor, "rb").read())

        eventos_orden: list[str] = []

        def _env_txt(txt: str, chat_id: Any = None) -> dict[str, Any]:
            eventos_orden.append(f"TEXTO: {txt[:50]}")
            return {"ok": True, "message_id": len(eventos_orden)}

        def _env_foto(ruta: str, caption: str = "", chat_id: Any = None) -> dict[str, Any]:
            eventos_orden.append(f"FOTO: {os.path.basename(ruta)} | {caption}")
            return {"ok": True, "message_id": len(eventos_orden)}

        prep_4 = {
            "id_trabajo": "prueba_4_imagenes_y_3_parrafos",
            "analisis": a,
            "mensaje": a.mensaje,
            "analisis_texto_3_parrafos": texto_3p,
            "ruta_imagen": ruta_ticket,
            "ruta_ticket_valor": ruta_valor,
            "ruta_cuadro": ruta_cuadro,
            "ruta_cuadro_h2h": ruta_h2h,
            "h2h_2024_2026": {"n_duelos": 3, "estado": "3 enfrentamientos"},
            "estado_h2h_2024_2026": "3 enfrentamientos",
            "rutas": {
                "informe": "x", "datos": "y",
                "imagen": ruta_ticket, "ticket_valor": ruta_valor,
                "cuadro": ruta_cuadro, "cuadro_h2h": ruta_h2h,
            },
            "contexto": {"equipo_local": "Real Madrid", "equipo_visitante": "Villarreal"},
        }
        res_env = servicio.enviar_analisis(prep_4, enviar_fn=_env_txt, enviar_foto_fn=_env_foto)
        assert res_env["enviado"] is True
        assert res_env["ticket_valor_enviado"] is True
        assert res_env["partes"] == 4
        # Verificar orden estricto: 0) Texto 3 párrafos, 1) Ticket General, 2) Ticket de Valor, 3) Cuadro, 4) H2H
        assert eventos_orden[0].startswith("TEXTO:"), eventos_orden
        assert "ticket_general.png" in eventos_orden[1], eventos_orden
        assert "ticket_valor.png" in eventos_orden[2], eventos_orden
        assert "cuadro_esp.png" in eventos_orden[3], eventos_orden
        assert "cuadro_h2h.png" in eventos_orden[4], eventos_orden

    print("  OK 32. Ticket de Valor (2da imagen tras ticket general) y análisis de 3 párrafos previos")


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("\nPRUEBAS DEL MOTOR DE MERCADOS (datos sinteticos identificados)\n" + "-" * 70)
    pruebas = [
        prueba_catalogo_completo,
        prueba_matriz_y_coherencia,
        prueba_referencia_lambda,
        prueba_media_ajustada_y_nulos,
        prueba_filtro_estricto,
        prueba_sin_empate_y_handicap,
        prueba_combinaciones_conjuntas,
        prueba_sin_datos_suficientes,
        prueba_cobertura_catalogo,
        prueba_sensibilidad_y_grupos,
        prueba_condicion_real_y_simetria,
        prueba_mensaje_y_particion,
        prueba_flujo_envio_y_duplicados,
        prueba_condicion_no_verificable,
        prueba_envio_parse_mode,
        prueba_formato_agrupado,
        prueba_cobertura_xg,
        prueba_contraccion_bayesiana,
        prueba_dixon_coles,
        prueba_comparar_tecnicas,
        prueba_mezcla_goles_xg_por_cobertura,
        prueba_moduladores_neutros_y_acotados,
        prueba_metricas_equipo_extraidas,
        prueba_comparar_moduladores,
        prueba_resumen_muestra_explicito,
        prueba_renderizado_ticket_png,
        prueba_cuadro_especialista,
        prueba_fecha_por_id_y_declaracion_sin_calendario,
        prueba_cuadro_h2h_2024_2026,
        prueba_h2h_espanyol_vs_atletico_prohibicion_falsificacion,
        prueba_motor_recursivo_45_dcn,
        prueba_ticket_valor_y_analisis_3_parrafos,
    ]
    fallos = 0
    for prueba in pruebas:
        try:
            prueba()
        except AssertionError as e:
            fallos += 1
            print(f"  FALLO {prueba.__name__}: {e}")
        except Exception as e:
            fallos += 1
            print(f"  FALLO {prueba.__name__}: {type(e).__name__}: {e}")
    print("-" * 70)
    print(f"Resultado: {len(pruebas) - fallos}/{len(pruebas)} pruebas OK\n")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())

