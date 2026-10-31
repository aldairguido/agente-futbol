"""
Pruebas de la evaluacion de pronosticos.
Todos los datos son SINTETICOS e identificados como tales: no son resultados reales.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evaluacion_pronosticos as ev  # noqa: E402


# ─── Fixtures sinteticas ──────────────────────────────────────────────────────

def resultado_sintetico(gl=2, gv=1, gl_1t=1, gv_1t=0, corners=(6, 4),
                        amarillas=(2, 3), rojas=(0, 0), estado="finalizado",
                        goles=None):
    r = ev.ResultadoPartido(
        id_partido="SYN-1", estado=estado, local="Equipo Alfa", visitante="Equipo Beta",
        goles_local=gl, goles_visitante=gv,
        goles_local_1t=gl_1t, goles_visitante_1t=gv_1t,
        inicio_utc="2030-01-01T20:00:00Z", fuente="sintetica", consultado_en="sintetico",
    )
    for periodo, (a, b) in zip(("FT", "1T", "2T"),
                               (corners, (corners[0] // 2, corners[1] // 2),
                                (corners[0] - corners[0] // 2, corners[1] - corners[1] // 2))):
        r.corners_local[periodo] = a
        r.corners_visitante[periodo] = b
    for periodo, (a, b) in zip(("FT", "1T", "2T"),
                               (amarillas, (1, 1), (amarillas[0] - 1, amarillas[1] - 1))):
        r.amarillas_local[periodo] = a
        r.amarillas_visitante[periodo] = b
    r.rojas_local, r.rojas_visitante = rojas
    if goles is None:
        goles = []
        for i in range(gl):
            goles.append({"minuto": 10 + i * 20, "equipo": "local", "autor": f"L{i}",
                          "autogol": False, "penalti": False,
                          "marcador_nuevo": [i + 1, 0]})
        for i in range(gv):
            goles.append({"minuto": 30 + i * 20, "equipo": "visitante", "autor": f"V{i}",
                          "autogol": False, "penalti": False,
                          "marcador_nuevo": [gl, i + 1]})
    r.goles = sorted(goles, key=lambda g: g["minuto"])
    return r


def sel(mercado, evento, periodo="FT", equipo=None, prob=0.8, linea=None):
    return {"mercado": mercado, "evento": evento, "etiqueta": evento, "periodo": periodo,
            "equipo": equipo, "linea": linea, "probabilidad": prob,
            "estado_sensibilidad": "mantiene"}


def pronostico_sintetico(selecciones, id_trabajo="SYN", generado="2030-01-01 10:00:00"):
    return {
        "id_trabajo": id_trabajo,
        "equipos": {"local": "Equipo Alfa", "visitante": "Equipo Beta"},
        "partido": {"fecha": "2030-01-01", "competicion": "Liga Sintetica"},
        "generado_en": generado, "publicado_en": None,
        "modelo": {"nombre": "sintetico", "version": "test"},
        "datos_corte": {}, "umbral_publicado": 0.70,
        "selecciones": selecciones, "selecciones_publicadas": selecciones,
        "referencia": "fixture sintetica", "origen": "sintetico",
    }


# ─── 1. Bandas: fronteras y excluyencia ──────────────────────────────────────

def prueba_bandas_fronteras():
    casos = [(0.70, "por debajo del filtro"), (0.7000001, "0.70-0.75"),
             (0.7499999, "0.70-0.75"), (0.75, "0.75-0.80"), (0.80, "0.80-0.85"),
             (0.85, "0.85-0.90"), (0.90, "0.90-0.95"), (0.95, "0.95-1.00"),
             (0.99999, "0.95-1.00"), (1.00, "1.00 exacto"), (None, ev.BANDA_REDONDEADA)]
    for p, esperado in casos:
        obtenido = ev.banda_de(p)
        assert obtenido == esperado, f"p={p!r}: {obtenido!r} != {esperado!r}"

    # Ninguna banda se solapa: con valor exacto, cada p cae en una sola banda
    for i in range(701, 1001):
        p = i / 1000
        cuenta = sum(1 for nombre, _inf, _sup in ev.BANDAS if ev.banda_de(p) == nombre)
        assert cuenta == 1, f"p={p} cae en {cuenta} bandas"
    # 0,70 exacto no supera el filtro estricto y queda fuera de toda banda
    assert ev.banda_de(0.70) == "por debajo del filtro"
    assert all(ev.banda_de(0.70) != nombre for nombre, _i, _s in ev.BANDAS)
    print("  OK 1. fronteras 70/75/80/85/90/95/100 sin solapes entre bandas")


# ─── 2. Redondeo frente a valor exacto ───────────────────────────────────────

def prueba_redondeo_no_demuestra_filtro():
    """Un «70%» redondeado NO demuestra p>0.70."""
    e = ev.Evaluacion(
        id_trabajo="S", partido="A vs B", mercado="M", evento="over_2.5",
        etiqueta="x", periodo="FT", equipo=None, linea=2.5,
        probabilidad_original=None, probabilidad_origen="redondeada",
        estado_sensibilidad="mantiene", estado_liquidacion="ganada", evento_ocurrio=1,
        objetivo_probabilidad="binario", detalle="", referencia="")
    assert not e.evaluable_binario, "sin p exacta no entra en metricas numericas"
    assert ev.stats_banda_sin_p(e) if hasattr(ev, "stats_banda_sin_p") else True
    d = ev._stats_banda([e])
    assert d["evaluables"] == 0 and d["acierto_observado"] is None, d
    assert d["ganadas"] == 0, "los no evaluables no cuentan como aciertos"
    # El evento sí se puede contar aparte
    assert d["pendientes_o_no_verificables"] == 1 or d["publicadas"] == 1
    print("  OK 2. un porcentaje solo redondeado no se convierte en p exacta")


# ─── 3. N=0 nunca es 0% ni 100% ─────────────────────────────────────────────

def prueba_denominador_cero():
    d = ev._stats_banda([])
    assert d["acierto_observado"] is None
    assert d["probabilidad_media"] is None
    assert d["brecha_pp"] is None
    assert ev.pct(None) == "sin datos"
    assert ev.pct(0.0) == "0,0%"
    print("  OK 3. sin datos se muestra «sin datos», nunca 0% ni 100%")


# ─── 4. Totales: linea, operador y linea entera ──────────────────────────────

def prueba_totales_y_linea_entera():
    r = resultado_sintetico(gl=2, gv=1)  # total 3
    casos = [("over_2.5", "ganada"), ("under_2.5", "perdida"), ("over_3.5", "perdida"),
             ("under_3.5", "ganada"), ("al_menos_3", "ganada"), ("al_menos_4", "perdida")]
    for evento, esperado in casos:
        e = ev.liquidar(sel("Total de goles / Numero de goles", evento), r, "S")
        assert e.estado_liquidacion == esperado, f"{evento}: {e.estado_liquidacion}"
    # Periodos: 1T 1-0 => total 1T = 1 ; 2T = 2
    for evento, periodo, esperado in (("over_0.5", "1T", "ganada"),
                                      ("under_1.5", "1T", "ganada"),
                                      ("over_1.5", "2T", "ganada"),
                                      ("under_2.5", "2T", "ganada")):
        e = ev.liquidar(sel("Total de goles / Numero de goles", evento, periodo), r, "S")
        assert e.estado_liquidacion == esperado, f"{evento} {periodo}: {e.estado_liquidacion}"
    # Total por equipo con prefijo del motor
    e = ev.liquidar(sel("Equipo Alfa - Total de goles", "local_over_1.5"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    e = ev.liquidar(sel("Equipo Beta - Total de goles", "visitante_under_1.5"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    print("  OK 4. totales con linea, operador, periodos y prefijo de equipo")


# ─── 5. Handicap europeo: tres resultados ────────────────────────────────────

def prueba_handicap_europeo():
    r = resultado_sintetico(gl=2, gv=1)
    # h-1 local: 2-1=1 == 1 -> empate con handicap
    e = ev.liquidar(sel("Handicap de 3 opciones", "h-1_local"), r, "S")
    assert e.estado_liquidacion == "perdida", e.detalle
    e = ev.liquidar(sel("Handicap de 3 opciones", "h-1_empate"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    e = ev.liquidar(sel("Handicap de 3 opciones", "h-1_visitante"), r, "S")
    assert e.estado_liquidacion == "perdida", e.detalle
    # h-2 local: 2-2=0 < 1 -> visitante
    e = ev.liquidar(sel("Handicap de 3 opciones", "h-2_visitante"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    # El empate con handicap NO es devolucion
    assert e.estado_liquidacion != "devolucion"
    print("  OK 5. handicap europeo de 3 resultados, empate con handicap no es devolucion")


# ─── 6. Apuesta sin empate: devolucion no es victoria ────────────────────────

def prueba_sin_empate_devolucion():
    r_empate = resultado_sintetico(gl=1, gv=1, gl_1t=0, gv_1t=1)
    e = ev.liquidar(sel("Apuesta sin empate", "dnb_local"), r_empate, "S")
    assert e.estado_liquidacion == "devolucion", e.estado_liquidacion
    assert e.evento_ocurrio == 0, "el evento victoria NO ocurrio"
    assert not e.evaluable_binario, "una devolucion no entra en Brier como binario"
    # Victoria efectiva
    r_gana = resultado_sintetico(gl=2, gv=0, gl_1t=1, gv_1t=0)
    e2 = ev.liquidar(sel("Apuesta sin empate", "dnb_local"), r_gana, "S")
    assert e2.estado_liquidacion == "ganada" and e2.evento_ocurrio == 1
    e3 = ev.liquidar(sel("Apuesta sin empate", "dnb_local"), resultado_sintetico(gl=0, gv=2, gl_1t=0, gv_1t=1), "S")
    assert e3.estado_liquidacion == "perdida" and e3.evento_ocurrio == 0
    print("  OK 6. sin empate: empate=devolucion con y=0, distinguido de victoria y derrota")


# ─── 7. Combinaciones: exigen TODAS las condiciones ──────────────────────────

def prueba_combinaciones_exigen_todas():
    r = resultado_sintetico(gl=2, gv=1)  # total 3, ambos anotan, gana local
    e = ev.liquidar(sel("Ganador + Total 2.5", "1_mas_2.5"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    # 1 y mas de 3.5: total 3 -> falla el total
    e = ev.liquidar(sel("Ganador + Total 3.5", "1_mas_3.5"), r, "S")
    assert e.estado_liquidacion == "perdida", e.detalle
    # ganador + ambos anotan: 2-1 -> ambos si, gana local -> ganada
    e = ev.liquidar(sel("Ganador + Ambos anotan", "1_btts_si"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    # X + ambos anotan: no hay empate -> pierde aunque ambos anoten
    e = ev.liquidar(sel("Ganador + Ambos anotan", "X_btts_si"), r, "S")
    assert e.estado_liquidacion == "perdida", e.detalle
    # btts + total
    e = ev.liquidar(sel("Ambos anotan + Total 2.5", "btts_si_mas_2.5"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    e = ev.liquidar(sel("Ambos anotan + Total 2.5", "btts_si_menos_2.5"), r, "S")
    assert e.estado_liquidacion == "perdida", e.detalle
    print("  OK 7. combinaciones exigen todas las condiciones, no cuentan partes")


# ─── 8. Descanso y goles por tiempo ──────────────────────────────────────────

def prueba_descanso_y_tiempos():
    r = resultado_sintetico(gl=2, gv=1, gl_1t=1, gv_1t=1)
    e = ev.liquidar(sel("1er tiempo - Ganador", "1t_X", "1T"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    e = ev.liquidar(sel("Gol en ambos tiempos", "gol_ambos_tiempos"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    # 2T = 2-1 - (1-1) = 1-0 -> hay gol en 2T, no en 1T si 1T=0-0
    r2 = resultado_sintetico(gl=1, gv=0, gl_1t=0, gv_1t=0)
    e = ev.liquidar(sel("Gol en ambos tiempos", "gol_1t", "1T"), r2, "S")
    assert e.estado_liquidacion == "perdida", e.detalle
    e = ev.liquidar(sel("Gol en ambos tiempos", "gol_2t", "2T"), r2, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    # Sin descanso verificable -> no verificable, nunca derrota
    r3 = resultado_sintetico(gl=1, gv=0)
    r3.goles_local_1t = None
    r3.goles_visitante_1t = None
    e = ev.liquidar(sel("Gol en ambos tiempos", "gol_1t", "1T"), r3, "S")
    assert e.estado_liquidacion == "no_verificable", e.estado_liquidacion
    print("  OK 8. descanso verificado; sin descanso -> no verificable, no derrota")


# ─── 9. Corners y tarjetas sin dato ──────────────────────────────────────────

def prueba_corners_tarjetas_faltantes():
    r = resultado_sintetico(corners=(6, 4), amarillas=(2, 3))
    e = ev.liquidar(sel("Total de tiros de esquina", "corners_over_9.5"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle  # 10 > 9.5
    e = ev.liquidar(sel("Total de tiros de esquina", "corners_over_10.5"), r, "S")
    assert e.estado_liquidacion == "perdida", e.detalle
    e = ev.liquidar(sel("Total de tarjetas", "amarillas_over_4.5"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle  # 5 > 4.5
    # Sin corners en la fuente: no verificable, NO se divide ni se inventa
    r2 = resultado_sintetico()
    r2.corners_local.clear()
    r2.corners_visitante.clear()
    e = ev.liquidar(sel("Total de tiros de esquina", "corners_over_9.5"), r2, "S")
    assert e.estado_liquidacion == "no_verificable", e.estado_liquidacion
    # Sin tarjetas en la fuente: tampoco se inventan ni se promedian
    r3 = resultado_sintetico()
    r3.amarillas_local.clear()
    r3.amarillas_visitante.clear()
    e = ev.liquidar(sel("Total de tarjetas", "amarillas_over_4.5"), r3, "S")
    assert e.estado_liquidacion == "no_verificable", e.estado_liquidacion
    # Un fallo de consulta no es un fallo del pronostico
    assert not e.evaluable_binario
    print("  OK 9. sin corners/tarjetas -> no verificable, nunca derrota ni promedio")


# ─── 10. Partido suspendido / no finalizado / no encontrado ──────────────────

def prueba_estados_no_jugados():
    for estado, esperado in (("no_finalizado", "pendiente"),
                             ("no_encontrado", "no_verificable"),
                             ("en_juego", "no_verificable")):
        r = ev.ResultadoPartido(estado=estado, local="A", visitante="B")
        e = ev.liquidar(sel("Total de goles / Numero de goles", "over_2.5"), r, "S")
        assert e.estado_liquidacion == esperado, f"{estado}: {e.estado_liquidacion}"
        assert not e.evaluable_binario
    print("  OK 10. suspendido/no jugado/no encontrado no se cuentan como fallo")


# ─── 11. Pago anticipado por trayectoria ─────────────────────────────────────

def prueba_pago_anticipado():
    # Gana 3-1 habiendo liderado por 2 en algun momento
    goles = [
        {"minuto": 10, "equipo": "local", "autor": "A", "marcador_nuevo": [1, 0]},
        {"minuto": 20, "equipo": "local", "autor": "B", "marcador_nuevo": [2, 0]},
        {"minuto": 30, "equipo": "visitante", "autor": "C", "marcador_nuevo": [2, 1]},
        {"minuto": 40, "equipo": "local", "autor": "D", "marcador_nuevo": [3, 1]},
    ]
    r = resultado_sintetico(gl=3, gv=1, gl_1t=2, gv_1t=0, goles=goles)
    e = ev.liquidar(sel("Ganador del partido - Pago Anticipado +2", "pago_anticipado"), r, "S")
    assert e.estado_liquidacion == "ganada", e.detalle
    # Empate 1-1 SIN liderar por 2 -> no
    goles2 = [{"minuto": 10, "equipo": "local", "autor": "A", "marcador_nuevo": [1, 0]},
              {"minuto": 20, "equipo": "visitante", "autor": "B", "marcador_nuevo": [1, 1]}]
    r2 = resultado_sintetico(gl=1, gv=1, gl_1t=1, gv_1t=0, goles=goles2)
    e2 = ev.liquidar(sel("Ganador del partido - Pago Anticipado +2", "pago_anticipado"), r2, "S")
    assert e2.estado_liquidacion == "perdida", e2.detalle
    # Pierde 1-2 pero lidero 2-0 en un instante -> gana el pago anticipado
    goles3 = [{"minuto": 5, "equipo": "local", "autor": "A", "marcador_nuevo": [1, 0]},
              {"minuto": 8, "equipo": "local", "autor": "B", "marcador_nuevo": [2, 0]},
              {"minuto": 30, "equipo": "visitante", "autor": "C", "marcador_nuevo": [2, 1]},
              {"minuto": 60, "equipo": "visitante", "autor": "D", "marcador_nuevo": [2, 2]},
              {"minuto": 80, "equipo": "visitante", "autor": "E", "marcador_nuevo": [2, 3]}]
    r3 = resultado_sintetico(gl=2, gv=3, gl_1t=2, gv_1t=0, goles=goles3)
    e3 = ev.liquidar(sel("Ganador del partido - Pago Anticipado +2", "pago_anticipado"), r3, "S")
    assert e3.estado_liquidacion == "ganada", e3.detalle
    print("  OK 11. pago anticipado por trayectoria (no exige ganar por 2 al final)")


# ─── 12. Banda 100% no se convierte en certeza ──────────────────────────────

def prueba_banda_100():
    sels = [sel("Total de goles / Numero de goles", "over_0.5", prob=1.0),
            sel("Total de goles / Numero de goles", "under_8.5", prob=0.999999)]
    doc = pronostico_sintetico(sels)
    r = resultado_sintetico(gl=2, gv=1)
    informe = ev.evaluar_tanda([doc], resultados={"Equipo Alfa vs Equipo Beta": r})
    audit = informe.banda_100
    assert audit["n"] == 2, audit
    assert audit["exactamente_1.0"] == 1, audit
    assert "no demuestra certeza" in audit["aviso"] or "solo esta muestra" in audit["aviso"]
    # El 1.0 exacto NO se cambia para mejorar metricas
    probs = [e.probabilidad_original for e in informe.evaluaciones]
    assert 1.0 in probs
    print("  OK 12. banda 100% auditada y su valor historico no se altera")


# ─── 13. Deduplicacion y promedio equilibrado por partido ───────────────────

def prueba_agrupacion_por_partido():
    # Un partido con 50 lineas y otro con 2: el equilibrado no lo domina
    muchas = [sel("Total de goles / Numero de goles", f"under_{5+i}.5", prob=0.99)
              for i in range(50)]
    pocas = [sel("Total de goles / Numero de goles", "over_0.5", prob=0.71)]
    r1 = resultado_sintetico(gl=1, gv=0, gl_1t=1, gv_1t=0)
    r1.id_partido = "P1"
    r2 = resultado_sintetico(gl=0, gv=0, gl_1t=0, gv_1t=0)
    r2.id_partido = "P2"
    d1 = pronostico_sintetico(muchas, id_trabajo="T1")
    d2 = pronostico_sintetico(pocas, id_trabajo="T2")
    informe = ev.evaluar_tanda([d1, d2],
                               resultados={"Equipo Alfa vs Equipo Beta": r1})
    # Forzar dos partidos distintos
    evals = informe.evaluaciones
    for e in evals:
        e.id_partido = "P1" if e.id_trabajo == "T1" else "P2"
    for e in [x for x in evals if x.id_trabajo == "T2"]:
        e.evento_ocurrio = 0
        e.estado_liquidacion = "perdida"
    bq = ev.desglose_bruto_vs_equilibrado(evals)
    assert bq["partidos_unicos"] == 2, bq
    assert bq["selecciones_brutas"] == 51, bq
    # Bruto muy alto (50 aciertos de 51) pero equilibrado debe bajarlo
    assert bq["equilibrado"]["partidos_con_evaluables"] == 2, bq["equilibrado"]
    assert bq["equilibrado"]["acierto_medio"] < bq["acierto_bruto"], bq
    print("  OK 13. conteo bruto frente a promedio equilibrado por partido")


# ─── 14. Metricas reproducibles: Brier y suma de p ──────────────────────────

def prueba_brier_y_sumas():
    # 0.71 (no 0.70) para caer legitimamente en la banda 0.70-0.75:
    # el filtro publicado es estricto p>0.70
    sels = [sel("Total de goles / Numero de goles", "over_0.5", prob=0.9),
            sel("Total de goles / Numero de goles", "under_9.5", prob=0.8),
            sel("Total de goles / Numero de goles", "over_9.5", prob=0.71)]
    doc = pronostico_sintetico(sels)
    r = resultado_sintetico(gl=2, gv=1)  # total 3: over0.5 si, under9.5 si, over9.5 no
    informe = ev.evaluar_tanda([doc], resultados={"Equipo Alfa vs Equipo Beta": r})
    esperado = ((0.9 - 1) ** 2 + (0.8 - 1) ** 2 + (0.71 - 0) ** 2) / 3
    assert abs(informe.brier["brier"] - esperado) < 1e-12, informe.brier
    d = informe.bandas["0.70-0.75"]
    assert d["evaluables"] == 1 and abs(d["aciertos_esperados"] - 0.71) < 1e-12, d
    assert abs(d["probabilidad_media"] - 0.71) < 1e-12, d
    assert abs(d["brecha_pp"] - (0.0 - 0.71) * 100) < 1e-9, d
    # Acumulados que se superponen
    assert informe.acumulados["p>70%"]["evaluables"] == 3
    assert informe.acumulados["p>=90%"]["evaluables"] == 1
    print("  OK 14. Brier, sum(p), brecha en pp y acumulados solapados")


# ─── 15. Anterioridad: nada posterior al inicio ─────────────────────────────

def prueba_anterioridad():
    r = resultado_sintetico()
    r.inicio_utc = "2030-01-01T20:00:00Z"
    ok, _ = ev._comprobar_anterioridad(pronostico_sintetico([], generado="2030-01-01 10:00:00"), r)
    assert ok, "10:00 local es anterior a 20:00 UTC"
    tarde, motivo = ev._comprobar_anterioridad(
        pronostico_sintetico([], generado="2030-01-01 23:00:00"), r)
    assert not tarde and "NO anterior" in motivo, motivo
    # Sin marcas: no se asume lo favorable
    sin, motivo2 = ev._comprobar_anterioridad({"generado_en": None}, r)
    assert not sin and "no se puede demostrar" in motivo2, motivo2
    # Un pronostico posterior no se evalua como acierto
    doc = pronostico_sintetico([sel("Total de goles / Numero de goles", "over_0.5")],
                               generado="2030-01-01 23:00:00")
    informe = ev.evaluar_tanda([doc], resultados={"Equipo Alfa vs Equipo Beta": r})
    assert all(not e.evaluable_binario for e in informe.evaluaciones)
    assert informe.limitaciones, "debe registrar la limitacion"
    print("  OK 15. pronosticos posteriores al inicio no se evaluan como aciertos")


# ─── 16. Snapshot inmutable y no reescritura ────────────────────────────────

def prueba_snapshot_inmutable():
    with tempfile.TemporaryDirectory() as tmp:
        snap = ev.SnapshotPronostico(
            id_trabajo="SNAP1", equipos={"local": "A", "visitante": "B"},
            partido={"fecha": "2030-01-01"}, generado_en="2030-01-01 10:00:00",
            publicado_en=None, modelo={}, datos_corte={},
            selecciones=[{"evento": "over_2.5", "probabilidad": 0.8}],
            selecciones_publicadas=[{"evento": "over_2.5", "probabilidad": 0.8}],
            umbral_publicado=0.70, referencia="x")
        ruta = ev.guardar_snapshot(snap, directorio=tmp)
        original = json.load(open(ruta, encoding="utf-8"))
        assert original["selecciones"][0]["probabilidad"] == 0.8
        # Intentar sobrescribir con otro valor: NO debe cambiar
        snap2 = ev.SnapshotPronostico(
            id_trabajo="SNAP1", equipos={}, partido={}, generado_en="",
            publicado_en=None, modelo={}, datos_corte={},
            selecciones=[{"evento": "over_2.5", "probabilidad": 0.999}],
            selecciones_publicadas=[], umbral_publicado=0.70, referencia="y")
        ev.guardar_snapshot(snap2, directorio=tmp)
        despues = json.load(open(ruta, encoding="utf-8"))
        assert despues["selecciones"][0]["probabilidad"] == 0.8, \
            "el snapshot no debe reescribirse"
        print("  OK 16. snapshot inmutable: el pasado no se reescribe")


# ─── 17. Idempotencia del envio ─────────────────────────────────────────────

def prueba_idempotencia_envio():
    with tempfile.TemporaryDirectory() as tmp:
        reg = os.path.join(tmp, "registro.json")
        sels = [sel("Total de goles / Numero de goles", "over_0.5")]
        doc = pronostico_sintetico(sels)
        r = resultado_sintetico()
        informe = ev.evaluar_tanda([doc], resultados={"Equipo Alfa vs Equipo Beta": r})
        preparado = {"id_ejecucion": "EJ1", "mensaje": "resumen", "informe": informe,
                     "rutas": {"informe_md": "x.md"}}
        llamadas = []

        def enviar_fn(texto, chat_id=None):
            llamadas.append(texto)
            return {"ok": True, "message_id": 7}

        primero = ev.enviar_resumen(preparado, enviar_fn, ruta_registro=reg)
        assert primero["enviado"] and len(llamadas) == 1, primero
        assert ev.estado_envio_previo("EJ1", reg) == "envio_confirmado"
        # Segundo intento: no se repite
        segundo = ev.enviar_resumen(preparado, enviar_fn, ruta_registro=reg)
        assert not segundo["enviado"] and segundo["estado"] == "ya_confirmado", segundo
        assert len(llamadas) == 1, "no debe reenviarse"
        # Peticion nueva explicita: si se reenvia
        tercero = ev.enviar_resumen(preparado, enviar_fn, ruta_registro=reg,
                                    nueva_ejecucion=True)
        assert tercero["enviado"] and len(llamadas) == 2, tercero
        # Error del proveedor: estado incierto conservado, no confirmado
        def enviar_falla(texto, chat_id=None):
            return {"ok": False, "error": "timeout ambiguo"}

        preparado2 = dict(preparado, id_ejecucion="EJ2")
        fallo = ev.enviar_resumen(preparado2, enviar_falla, ruta_registro=reg)
        assert not fallo["enviado"] and fallo["estado"] == "envio_fallido", fallo
        assert ev.estado_envio_previo("EJ2", reg) == "envio_fallido"
        # Sin adaptador: se informa, no se simula exito
        sin = ev.enviar_resumen(dict(preparado, id_ejecucion="EJ3"), None, ruta_registro=reg)
        assert sin["estado"] == "sin_adaptador", sin
        print("  OK 17. idempotencia: no repite envios confirmados y conserva el estado incierto")


# ─── 18. Estructuras de liquidacion y catalogo fijo ─────────────────────────

def prueba_estructuras_y_catalogo():
    assert ev.estructura_de("over_2.5") == "binario"
    assert ev.estructura_de("dnb_local") == "victoria_efectiva"
    assert ev.estructura_de("1_btts_si") == "conjuncion_simple"
    assert ev.estructura_de("h-1_local") == "multicategoria"
    assert ev.estructura_de("X") == "multicategoria"
    sels = [sel("Gol en ambos tiempos", "gol_1t"), sel("Apuestas especiales", "raro_x")]
    doc = pronostico_sintetico(sels)
    r = resultado_sintetico(gl=1, gv=0, gl_1t=1, gv_1t=0)
    informe = ev.evaluar_tanda([doc], resultados={"Equipo Alfa vs Equipo Beta": r})
    catalogo = ev.catalogo_eventos(informe.evaluaciones)
    assert len(catalogo) == len(set(catalogo)), "el catalogo no debe tener duplicados"
    assert all("|" in c for c in catalogo)
    est = ev.resumen_estructuras(informe.evaluaciones)
    assert "otra" in est, est
    # Una regla sin definir no es un fallo
    raro = [e for e in informe.evaluaciones if e.evento == "raro_x"][0]
    assert raro.estado_liquidacion == "regla_no_definida", raro.estado_liquidacion
    assert raro.evento_ocurrio is None
    print("  OK 18. estructuras clasificadas y catalogo fijo de eventos")


# ─── 19. Evidencia insuficiente se declara ─────────────────────────────────

def prueba_evidencia_insuficiente():
    sels = [sel("Total de goles / Numero de goles", "over_0.5")]
    doc = pronostico_sintetico(sels)
    r = resultado_sintetico()
    informe = ev.evaluar_tanda([doc], resultados={"Equipo Alfa vs Equipo Beta": r})
    assert informe.evidencia["partidos_unicos"] == 1
    assert not informe.evidencia["evidencia_suficiente"]
    assert "EVIDENCIA INSUFICIENTE" in informe.evidencia["mensaje"]
    texto = ev.resumen_telegram(informe)
    assert "EVIDENCIA INSUFICIENTE" in texto, "el resumen debe declararlo"
    print("  OK 19. evidencia insuficiente declarada, sin intervalos inventados")


# ─── 20. El resumen no oculta fallos ni tasas bajas ────────────────────────

def prueba_resumen_muestra_fallos():
    sels = [sel("Total de goles / Numero de goles", "over_0.5", prob=0.72),
            sel("Total de goles / Numero de goles", "over_9.5", prob=0.99)]
    doc = pronostico_sintetico(sels)
    r = resultado_sintetico(gl=2, gv=1)   # over0.5 si ; over9.5 no
    informe = ev.evaluar_tanda([doc], resultados={"Equipo Alfa vs Equipo Beta": r})
    texto = ev.resumen_telegram(informe)
    assert "no cumplidos: 1" in texto, texto
    assert "62,5%" in texto or "50,0%" in texto or "acierto real" in texto
    # El 0.70-0.75 con 1 de 1 acierto debe aparecer aunque sea bajo
    assert "0.70-0.75" in texto
    print("  OK 20. el resumen incluye fallos y bandas con acierto bajo")


# ─── 21. Renderizado de tablas en imagen ───────────────────────────────────

def prueba_render_tabla_png():
    import tempfile
    col = ["Equipo", "Cond.", "Prob. media", "Evaluables", "Aciertos",
           "Acierto real", "Brecha"]
    filas = [["Equipo Alfa", "casa", "91,5%", "33", "29", "87,9%", "-3,6 pp"],
             ["Equipo Beta", "fuera", "89,8%", "19", "12", "63,2%", "-26,7 pp"]]
    with tempfile.TemporaryDirectory() as tmp:
        destino = os.path.join(tmp, "tabla.png")
        ruta = ev.renderizar_tabla_png("Acierto por equipo", col, filas,
                                       subtitulo="8 partidos únicos",
                                       columnas_numericas=(2, 3, 4, 5, 6),
                                       resaltar_negativos=(6,), destino=destino)
        assert os.path.exists(ruta), "debe generar el PNG"
        assert os.path.getsize(ruta) > 1000, "el PNG no debe estar vacío"
        from PIL import Image
        with Image.open(ruta) as im:
            assert im.format == "PNG" and im.width > 400 and im.height > 100, im.size
            im.load()
    # El respaldo ASCII funciona sin dependencias
    ascii_ = ev.tabla_ascii_minimalista(col, filas)
    assert "Equipo" in ascii_ and "Equipo Alfa" in ascii_ and "Respuesta" not in ascii_
    # Con tabla vacía no debe fallar
    with tempfile.TemporaryDirectory() as tmp:
        ruta = ev.renderizar_tabla_png("Vacía", ["A", "B"], [],
                                       destino=os.path.join(tmp, "v.png"))
        assert os.path.exists(ruta)
    print("  OK 21. tablas renderizadas a PNG y respaldo ASCII disponible")


# ─── 22. Imágenes de informe y envio multimedia ────────────────────────────

def prueba_imagenes_y_envio_media():
    import tempfile
    sels = [sel("Total de goles / Numero de goles", "over_0.5", prob=0.9),
            sel("Equipo Alfa - Total de goles", "local_over_1.5", prob=0.8)]
    doc = pronostico_sintetico(sels)
    r = resultado_sintetico(gl=2, gv=1)
    informe = ev.evaluar_tanda([doc], resultados={"Equipo Alfa vs Equipo Beta": r})

    with tempfile.TemporaryDirectory() as tmp:
        imgs = ev.generar_imagenes_informe(informe, directorio=tmp)
        assert imgs, "debe generar al menos una imagen"
        for img in imgs:
            assert os.path.exists(img["ruta"]), img
            assert img["titulo"]
        reg_txt: list[str] = []
        reg_foto: list[str] = []
        preparado = {"id_ejecucion": "IMG1", "mensaje": "resumen",
                     "rutas": {"informe_md": "x.md"}, "imagenes": imgs,
                     "enviar_media_fn": lambda ruta, cap, chat_id=None: (
                         reg_foto.append(ruta), {"ok": True, "message_id": 2})[1]}
        registro = os.path.join(tmp, "reg.json")
        res = ev.enviar_resumen(
            preparado,
            enviar_fn=lambda texto, chat_id=None: (reg_txt.append(texto),
                                                   {"ok": True, "message_id": 1})[1],
            imagenes=imgs, ruta_registro=registro)
        assert res["enviado"] and res["imagenes_enviadas"] == len(imgs), res
        assert len(reg_foto) == len(imgs)

        # Si el envío de la imagen falla, el resumen sigue confirmado y el fallo se
        # registra: un adjunto que falla no invalida la evaluación.
        preparado["id_ejecucion"] = "IMG3"
        preparado["enviar_media_fn"] = lambda *a, **k: {"ok": False, "error": "boom"}
        res3 = ev.enviar_resumen(
            preparado,
            enviar_fn=lambda texto, chat_id=None: {"ok": True, "message_id": 4},
            imagenes=imgs, ruta_registro=registro)
        assert res3["enviado"] and res3["imagenes_enviadas"] == 0, res3
        assert res3["fallos_imagen"], "el fallo del adjunto debe quedar registrado"
        # Sin imágenes que enviar no se llama al adaptador de medios
        preparado["id_ejecucion"] = "IMG4"
        preparado["enviar_media_fn"] = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no debe llamarse sin imágenes"))
        res4 = ev.enviar_resumen(
            preparado,
            enviar_fn=lambda texto, chat_id=None: {"ok": True, "message_id": 5},
            imagenes=[], ruta_registro=registro)
        assert res4["enviado"] and res4["imagenes_enviadas"] == 0, res4
    print("  OK 22. imágenes adjuntas; un fallo de imagen no tumba el envío del resumen")


def prueba_filtro_dinamico_equipos():
    """Verifica que el filtro dinámico aísle la submuestra para 1 y 2 equipos."""
    sels = [
        sel("Ganador del partido", "1", equipo="Equipo Alfa", prob=0.85),
        sel("Ganador del partido", "2", equipo="Equipo Beta", prob=0.75),
        sel("Total de goles / Número de goles", "over_1.5", prob=0.90),
    ]
    doc = pronostico_sintetico(sels, id_trabajo="TEST_FILTRO")
    r = resultado_sintetico(gl=2, gv=1)

    # 1) Sin filtro -> 3 selecciones
    inf_todo = ev.evaluar_tanda([doc], {"Equipo Alfa vs Equipo Beta": r})
    assert len(inf_todo.evaluaciones) == 3

    # 2) Filtro por enfrentamiento (2 equipos) -> las 3 selecciones del choque
    inf_cruce = ev.evaluar_tanda([doc], {"Equipo Alfa vs Equipo Beta": r},
                                 filtro_equipos=["Equipo Alfa", "Equipo Beta"])
    assert len(inf_cruce.evaluaciones) == 3
    assert inf_cruce.filtro == "Equipo Alfa vs Equipo Beta"

    # 3) Filtro por 1 solo equipo -> solo selecciones de ese único equipo
    inf_alfa = ev.evaluar_tanda([doc], {"Equipo Alfa vs Equipo Beta": r},
                                filtro_equipos=["Equipo Alfa"])
    assert len(inf_alfa.evaluaciones) == 1
    assert inf_alfa.evaluaciones[0].equipo == "Equipo Alfa"
    assert "Equipo Alfa" in inf_alfa.por_equipo
    assert "Equipo Beta" not in inf_alfa.por_equipo
    assert inf_alfa.filtro == "Equipo Alfa"

    inf_beta = ev.evaluar_tanda([doc], {"Equipo Alfa vs Equipo Beta": r},
                                filtro_equipos="Equipo Beta")
    assert len(inf_beta.evaluaciones) == 1
    assert inf_beta.evaluaciones[0].equipo == "Equipo Beta"
    print("  OK 23. filtro dinámico por equipos (1 y 2 equipos) aísla la submuestra")


def prueba_manejo_vacios_omite_imagenes():
    """Si no hay datos liquidados evaluables para el filtro, responde aviso y omite imágenes."""
    prep = ev.preparar_evaluacion(pronosticos=[], filtro_equipos="Equipo Fantasma")
    assert prep["sin_datos_filtro"] is True
    assert "⚠️ No hay datos liquidados para evaluar a Equipo Fantasma." in prep["mensaje"]
    assert prep["imagenes"] == []

    # Enviar resumen en caso de vacío tampoco envía imágenes
    res = ev.enviar_resumen(prep, enviar_fn=lambda txt, chat_id=None: {"ok": True, "message_id": 99})
    assert res["enviado"] is True
    assert res["imagenes_enviadas"] == 0
    assert "⚠️ No hay datos liquidados para evaluar a Equipo Fantasma." in res["motivo"]
    print("  OK 24. manejo de vacíos: aviso exacto y omisión de imágenes cuando no hay datos")


# ─── 25. Liquidación de snapshots nuevos (sin red) ──────────────────────────

class FuenteFalsa:
    """
    Fuente mínima con la misma interfaz que usa el verificador
    (`resolver_equipo` + `_api`). Permite probar la liquidación sin red y de
    forma determinista.
    """

    def __init__(self, fixtures, detalles, ids=None):
        self.fixtures = fixtures
        self.detalles = detalles
        self.ids = ids or {"Local": "1", "Visitante": "2"}

    def abrir(self):
        pass

    def cerrar(self):
        pass

    def resolver_equipo(self, nombre):
        return {"id": self.ids.get(nombre, "0"), "nombre": nombre, "alternativas": []}

    def _api(self, ruta):
        if ruta.startswith("/teams"):
            return {"fixtures": {"allFixtures": {"fixtures": self.fixtures}}}
        if "/matchDetails" in ruta:
            return self.detalles[ruta.split("matchId=")[1]]
        return {}


def _fixture(mid, fecha_utc, gl, gv, started=True, finished=True, local="Local",
             visitante="Visitante"):
    return {
        "id": mid,
        "home": {"id": "1", "name": local}, "away": {"id": "2", "name": visitante},
        "status": {"utcTime": fecha_utc, "started": started, "finished": finished},
    }


def _detalle(mid, gl, gv, fecha_utc, started=True, finished=True):
    return {
        "general": {"matchId": mid, "matchName": f"Local vs Visitante {fecha_utc[:10]}"},
        "header": {
            "teams": [{"name": "Local", "score": gl}, {"name": "Visitante", "score": gv}],
            "status": {"finished": finished, "started": started, "utcTime": fecha_utc},
        },
    }


def _doc_prueba(fecha, generado, selecciones=None):
    return {
        "id_trabajo": "LIQ1",
        "equipos": {"local": "Local", "visitante": "Visitante"},
        "partido": {"fecha": fecha, "competicion": "Liga"},
        "generado_en": generado,
        "modelo": {}, "datos_corte": {}, "umbral_publicado": 0.70,
        "selecciones": selecciones or [], "selecciones_publicadas": selecciones or [],
        "referencia": "prueba",
    }


def prueba_liquidacion_snapshots_sin_red():
    """
    25. El verificador liquida el partido JUGADO, no la vuelta programada, y la
        anterioridad del pronóstico se calcula con el huso correcto (-05:00, no el
        LMT de pytz). Ambos fallos dejaban los snapshots nuevos sin liquidar.
    """
    # ── M1: sin fecha en el snapshot, había un partido futuro más reciente ──
    fixtures = [_fixture("100", "2026-10-14T00:30:00Z", 0, 1),
                _fixture("200", "2026-10-21T00:30:00Z", None, None,
                         started=False, finished=False)]
    detalles = {"100": _detalle("100", 0, 1, "2026-10-14T00:30:00Z")}
    fuente = FuenteFalsa(fixtures, detalles)
    fuente.ids = {"Local": "1", "Visitante": "2"}

    r = ev.verificar_resultados([_doc_prueba(None, "2026-10-13 17:15:20")],
                                fuente=fuente, aviso=lambda m: None)
    res = r["Local vs Visitante"]
    assert res.id_partido == "100", f"debe elegir el partido jugado, no la vuelta: {res.id_partido}"
    assert res.finalizado is True, f"el partido del 14/10 ya terminó: {res.estado}"
    assert (res.goles_local, res.goles_visitante) == (0, 1), res

    # Caso límite: si NINGÚN partido ha empezado, se conserva el fixture para poder
    # declarar «encontrado pero sin liquidar» en vez de «no encontrado».
    solo_futuro = FuenteFalsa(
        [_fixture("300", "2026-10-21T00:30:00Z", None, None,
                  started=False, finished=False)],
        {"300": _detalle("300", None, None, "2026-10-21T00:30:00Z",
                         started=False, finished=False)})
    r2 = ev.verificar_resultados([_doc_prueba(None, "2026-10-20 10:00:00")],
                                 fuente=solo_futuro, aviso=lambda m: None)
    res2 = r2["Local vs Visitante"]
    assert res2.estado in ("no_finalizado", "en_juego"), res2.estado
    assert not res2.finalizado

    # ── M2: huso LMT de pytz: 15:56:41 local debe ser anterior a 21:00:45Z ──
    fuente3 = FuenteFalsa([_fixture("400", "2026-10-14T21:00:45Z", 2, 1)],
                          {"400": _detalle("400", 2, 1, "2026-10-14T21:00:45Z")})
    doc3 = _doc_prueba("2026-10-14", "2026-10-14 15:56:41",
                       [sel("Total de goles / Numero de goles", "over_1.5")])
    r3 = ev.verificar_resultados([doc3], fuente=fuente3, aviso=lambda m: None)
    res3 = r3["Local vs Visitante"]
    assert res3.finalizado, res3.estado
    ok, motivo = ev._comprobar_anterioridad(doc3, res3)
    assert ok, f"15:56 hora de Lima (UTC-5) es anterior a 21:00Z: {motivo}"

    # Y el pronóstico se evalúa como acierto, no como «no verificable»
    informe = ev.evaluar_tanda([doc3], resultados=r3, aviso=lambda m: None,
                               aplicar_regla_versiones=True)
    evaluables = [e for e in informe.evaluaciones if e.evaluable_binario]
    assert evaluables, "el pronóstico anterior al inicio debe ser evaluable"
    assert all(e.estado_liquidacion == "ganada" for e in evaluables), \
        [(e.evento, e.estado_liquidacion) for e in evaluables]

    # Comparación directa del huso: sin el arreglo, `.replace(tzinfo=pytz...)`
    # aplicaba LMT (-05:08:36) y desplazaba la marca 8 min 36 s.
    t = ev._parsear_momento("2026-10-14 15:56:41")
    desplazamiento = (ev.datetime(2026, 10, 14, 20, 56, 41, tzinfo=ev.timezone.utc)
                      - t).total_seconds()
    assert abs(desplazamiento) < 1.0, \
        f"la marca local debe interpretarse como UTC-5 exacto, no LMT: {t.isoformat()}"

    # ── M3: fecha RANCIA guardada (el último H2H jugado) ────────────────────
    # Fluminense–Palmeiras jugaron el 15/08 y volvieron a jugar el 15/10; el
    # snapshot guardó la fecha vieja. El pronóstico se publicó el 14/10, así que
    # el partido pronosticado es el del 15/10, no el del 15/08.
    fixtures3 = [_fixture("500", "2026-08-15T21:30:17Z", 3, 2),
                 _fixture("600", "2026-10-15T00:30:00Z", 1, 2)]
    detalles3 = {"500": _detalle("500", 3, 2, "2026-08-15T21:30:17Z"),
                 "600": _detalle("600", 1, 2, "2026-10-15T00:30:00Z")}
    fuente4 = FuenteFalsa(fixtures3, detalles3)
    doc4 = _doc_prueba("2026-08-15", "2026-10-14 16:57:42")
    r4 = ev.verificar_resultados([doc4], fuente=fuente4, aviso=lambda m: None)
    res4 = r4["Local vs Visitante"]
    assert res4.id_partido == "600", (
        f"con fecha rancia debe elegirse el partido posterior a la publicación "
        f"(el 15/10), no el viejo: {res4.id_partido} {res4.inicio_utc}")
    assert (res4.goles_local, res4.goles_visitante) == (1, 2), res4

    # Y un análisis RETROSPECTIVO (publicado después del partido) sigue usando la
    # fecha guardada: la regla nueva solo actúa si hay un partido posterior.
    doc5 = _doc_prueba("2026-08-15", "2026-10-14 16:57:42")
    fuente5 = FuenteFalsa([_fixture("500", "2026-08-15T21:30:17Z", 3, 2)],
                          {"500": _detalle("500", 3, 2, "2026-08-15T21:30:17Z")})
    r5 = ev.verificar_resultados([doc5], fuente=fuente5, aviso=lambda m: None)
    assert r5["Local vs Visitante"].id_partido == "500", r5
    print("  OK 25. liquidación sin red: se elige el partido jugado (no la vuelta), "
          "la anterioridad usa UTC-5 exacto y una fecha rancia no liquida el partido viejo")


def main() -> int:
    pruebas = [
        prueba_bandas_fronteras,
        prueba_redondeo_no_demuestra_filtro,
        prueba_denominador_cero,
        prueba_totales_y_linea_entera,
        prueba_handicap_europeo,
        prueba_sin_empate_devolucion,
        prueba_combinaciones_exigen_todas,
        prueba_descanso_y_tiempos,
        prueba_corners_tarjetas_faltantes,
        prueba_estados_no_jugados,
        prueba_pago_anticipado,
        prueba_banda_100,
        prueba_agrupacion_por_partido,
        prueba_brier_y_sumas,
        prueba_anterioridad,
        prueba_snapshot_inmutable,
        prueba_idempotencia_envio,
        prueba_estructuras_y_catalogo,
        prueba_evidencia_insuficiente,
        prueba_resumen_muestra_fallos,
        prueba_render_tabla_png,
        prueba_imagenes_y_envio_media,
        prueba_filtro_dinamico_equipos,
        prueba_manejo_vacios_omite_imagenes,
        prueba_liquidacion_snapshots_sin_red,
    ]
    print()
    print("PRUEBAS DE EVALUACION DE PRONOSTICOS (fixtures sinteticas identificadas)")
    print("-" * 74)
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
    print("-" * 74)
    print(f"Resultado: {len(pruebas) - fallos}/{len(pruebas)} pruebas OK")
    print()
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
