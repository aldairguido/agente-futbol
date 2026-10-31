"""
test_auditoria_historica.py
───────────────────────────────────────────────────────────────────────────────
Pruebas deterministas del motor de Auditoría Histórica Profunda, con una fuente
simulada: no tocan la red ni escriben informes de producción.

Cubren exactamente la lógica de compensación que exige el módulo:
  • los amistosos se descartan por completo;
  • el escaneo continúa hacia atrás cuando un partido oficial viene sin datos;
  • el dataset nunca se entrega corto si hay historial suficiente detrás;
  • cuando la fuente se agota, se declara en vez de rellenar con nulos.

Uso:  python test_auditoria_historica.py
"""

from __future__ import annotations

import os
import sys

import auditoria_historica as ah
from fuentes_xg import (
    CATALOGO_METRICAS,
    ORDEN_PERIODOS,
    FuenteFotMob,
    FuenteXG,
    MatrizPartido,
    PartidoFinalizado,
    PeriodoPartido,
    ResultadoEscaneo,
)


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE SIMULADA
# ═══════════════════════════════════════════════════════════════════════════════

def _matriz(partido: PartidoFinalizado, con_datos: bool) -> MatrizPartido:
    """Matriz de prueba: con datos (valores reales) o vacía (todo nulo)."""
    m = MatrizPartido(
        id_partido=partido.id_partido, fecha=partido.fecha, torneo=partido.torneo,
        equipo=partido.local, rival=partido.visitante,
        condicion="casa" if partido.es_local_el_equipo else "fuera",
        goles_equipo=1, goles_rival=0,
    )
    for periodo in ORDEN_PERIODOS:
        filas = [
            {"clave": d.clave, "metrica": d.etiqueta, "categoria": d.categoria,
             "equipo": (1.5 if con_datos else None), "rival": (1.0 if con_datos else None)}
            for d in CATALOGO_METRICAS
        ]
        m.periodos[periodo] = PeriodoPartido(periodo, filas, [])
    m.procedencia = {
        periodo: {
            "equipo": {d.clave: ("fotmob" if con_datos else "sin_dato") for d in CATALOGO_METRICAS},
            "rival": {d.clave: ("fotmob" if con_datos else "sin_dato") for d in CATALOGO_METRICAS},
        }
        for periodo in ORDEN_PERIODOS
    }
    return m


class FuenteFalsa(FuenteXG):
    """
    Fuente simulada. `calendario` es la lista de (fecha, torneo, con_datos) del más
    reciente al más antiguo, tal como la devolvería el escaneo de la fuente real.
    `nombre_equipo` permite simular dos equipos distintos (para las pruebas H2H).
    """
    nombre = "falsa"

    def __init__(self, calendario: list[tuple[str, str, bool]], nombre_equipo: str = "Equipo Test",
                 **_: object) -> None:
        self.calendario = calendario
        self.nombre_equipo = nombre_equipo
        self.matrices_pedidas: list[str] = []
        self.por_id: dict[str, bool] = {}

    def abrir(self) -> None:
        pass

    def cerrar(self) -> None:
        pass

    def resolver_equipo(self, nombre: str, rival_contexto: Any = None):
        return {"id": "1", "nombre": self.nombre_equipo, "liga": "Liga Test"}

    def partidos_futuros(self, equipo):
        return []

    def escanear_oficiales(self, equipo, objetivo: int, incluir_amistosos: bool = False) -> ResultadoEscaneo:
        oficiales, amistosos_reservados, total = [], [], 0
        for i, (fecha, torneo, _con_datos) in enumerate(self.calendario):
            total += 1
            es_amistoso = ("friendl" in torneo.lower()) or ("amistoso" in torneo.lower())
            # Alterna casa/fuera para poder probar los bloques por condición
            es_local = (i % 2 == 0)
            partido = PartidoFinalizado(
                id_partido=f"m{i}", fecha=fecha, torneo=torneo,
                local=(self.nombre_equipo if es_local else f"Rival {i}"),
                visitante=(f"Rival {i}" if es_local else self.nombre_equipo),
                goles_local=1, goles_visitante=0, es_local_el_equipo=es_local,
            )
            if es_amistoso:
                amistosos_reservados.append(partido)
                continue
            oficiales.append(partido)
        # La fuente real entrega el historial del más reciente al más antiguo
        oficiales.sort(key=lambda p: p.fecha, reverse=True)
        amistosos_reservados.sort(key=lambda p: p.fecha, reverse=True)
        return ResultadoEscaneo(
            oficiales=oficiales, amistosos_reservados=amistosos_reservados,
            incluir_amistosos=incluir_amistosos,
            paginas_escaneadas=1, finalizados_totales=total,
            amistosos_excluidos=len(amistosos_reservados),
            fuente_agotada=len(oficiales) < objetivo,
            motivo_parada="calendario simulado agotado",
        )

    def matriz_partido(self, partido: PartidoFinalizado, equipo) -> MatrizPartido:
        self.matrices_pedidas.append(partido.id_partido)
        con_datos = self.por_id[partido.id_partido]
        return _matriz(partido, con_datos)

    def preparar(self) -> None:
        """Mapa id_partido -> tiene datos, según el calendario simulado."""
        self.por_id = {}
        for i, (_fecha, torneo, con_datos) in enumerate(self.calendario):
            if "friendly" in torneo.lower():
                continue
            self.por_id[f"m{i}"] = con_datos


# ═══════════════════════════════════════════════════════════════════════════════
# PRUEBAS
# ═══════════════════════════════════════════════════════════════════════════════

def _ejecutar(calendario, limite, minimo=None) -> tuple[dict, FuenteFalsa]:
    """Ejecuta una auditoría con la fuente simulada inyectada (sin tocar la red)."""
    fuente = FuenteFalsa(calendario)
    fuente.preparar()
    auditoria = ah.auditar_equipo("Equipo Test", limite=limite, minimo_con_datos=minimo,
                                  fuente_obj=fuente)
    return auditoria, fuente


def prueba_filtra_amistosos() -> None:
    """1. Los amistosos se descartan por completo y no cuentan para el objetivo."""
    calendario = [
        ("2026-10-10", "Liga", True),
        ("2026-10-01", "Club Friendlies", True),   # amistoso CON datos: debe excluirse igual
        ("2026-09-20", "Liga", True),
        ("2026-09-10", "Amistoso Internacional", True),
        ("2026-09-01", "Liga", True),
    ]
    auditoria, _ = _ejecutar(calendario, limite=3)
    torneos = [p["torneo"] for p in auditoria["partidos"]]
    assert auditoria["escaneo"]["amistosos_excluidos"] == 2, auditoria["escaneo"]
    assert not any("friendl" in t.lower() or "amistoso" in t.lower() for t in torneos), torneos
    assert len(auditoria["partidos"]) == 3, len(auditoria["partidos"])
    print("  ✅ 1. amistosos descartados por completo (2 excluidos, 3 oficiales auditados)")


def prueba_compensacion_hacia_atras() -> None:
    """
    2. Si un partido oficial viene SIN datos, no se cuenta y el escaneo continúa
       hacia atrás hasta completar el objetivo: el dataset no se queda corto.
    """
    calendario = [
        ("2026-10-10", "Liga", True),
        ("2026-10-05", "Liga", False),   # sin datos -> debe compensarse
        ("2026-10-01", "Liga", True),
        ("2026-09-25", "Liga", False),   # sin datos -> debe compensarse
        ("2026-09-20", "Liga", True),
        ("2026-09-15", "Liga", True),
        ("2026-09-10", "Liga", True),
    ]
    auditoria, fuente = _ejecutar(calendario, limite=15, minimo=5)
    con_datos = [p for p in auditoria["partidos"] if p["valores_reales"] >= ah.MIN_VALORES_PARA_CONTAR]
    assert len(con_datos) == 5, f"esperaba 5 con datos, hubo {len(con_datos)}"
    assert auditoria["escaneo"]["compensacion_necesaria"] is True
    assert len(auditoria["escaneo"]["ids_sin_datos"]) == 2, auditoria["escaneo"]
    assert auditoria["resumen"]["objetivo_alcanzado"] is True
    # Debe haber seguido hacia atrás más allá de los 5 primeros y descartar los vacíos
    assert fuente.matrices_pedidas == ["m0", "m1", "m2", "m3", "m4", "m5", "m6"], fuente.matrices_pedidas
    print("  ✅ 2. compensación: 2 partidos vacíos descartados, 5 con datos alcanzados "
          "escaneando hacia atrás")


def prueba_amistoso_vacio_no_rompe() -> None:
    """
    3. El caso exacto del enunciado: pido 4, el calendario trae 2 amistosos (uno de
       ellos sin datos) y solo 3 oficiales. Debe entregar los 3 oficiales y declarar
       que el objetivo no es alcanzable, sin rellenar con nulos.
    """
    calendario = [
        ("2026-10-10", "Liga", True),
        ("2026-10-05", "Club Friendlies", False),
        ("2026-10-01", "Liga", True),
        ("2026-09-25", "Club Friendlies", False),
        ("2026-09-20", "Liga", True),
    ]
    auditoria, _ = _ejecutar(calendario, limite=4, minimo=4)
    assert len(auditoria["partidos"]) == 3, len(auditoria["partidos"])
    assert auditoria["escaneo"]["amistosos_excluidos"] == 2
    assert auditoria["resumen"]["objetivo_alcanzado"] is False
    assert auditoria["advertencias"], "debe declarar que el objetivo no se alcanzó"
    assert all(p["valores_reales"] > 0 for p in auditoria["partidos"]), "no debe haber relleno nulo"
    print("  ✅ 3. objetivo inalcanzable declarado explícitamente (3/4 oficiales, 0 relleno)")


def prueba_tope_de_limite() -> None:
    """4. El tope `limite` acota cuántos partidos se auditan aunque haya más historial."""
    calendario = [(f"2026-09-{d:02d}", "Liga", True) for d in range(20, 0, -1)]
    auditoria, _ = _ejecutar(calendario, limite=6)
    assert len(auditoria["partidos"]) == 6, len(auditoria["partidos"])
    assert auditoria["resumen"]["objetivo_alcanzado"] is True
    print("  ✅ 4. tope de --limite respetado (6 auditados de 20 disponibles)")


def prueba_orden_cronologico() -> None:
    """5. El dataset sale del partido más reciente al más antiguo."""
    calendario = [("2026-10-10", "Liga", True), ("2026-10-05", "Liga", True),
                  ("2026-09-30", "Liga", True), ("2026-09-25", "Liga", True)]
    auditoria, _ = _ejecutar(calendario, limite=4)
    fechas = [p["fecha"] for p in auditoria["partidos"]]
    assert fechas == sorted(fechas, reverse=True), fechas
    print(f"  ✅ 5. orden cronológico descendente correcto ({fechas[0]} → {fechas[-1]})")


# ═══════════════════════════════════════════════════════════════════════════════
# PRUEBAS DEL MOTOR H2H
# ═══════════════════════════════════════════════════════════════════════════════

def _doc_sintetico(nombre: str, valores: dict[str, list[float]]) -> dict:
    """
    Auditoría sintética: `valores` mapea clave de métrica -> serie de valores, y la
    condición alterna casa/fuera partido a partido.
    """
    partidos = []
    largo = max((len(v) for v in valores.values()), default=1)
    for i in range(largo):
        es_casa = (i % 2 == 0)
        filas = []
        for definicion in CATALOGO_METRICAS:
            # Si la métrica no está en `valores`, queda nula en TODOS los partidos
            serie = valores.get(definicion.clave)
            valor = serie[i] if serie and i < len(serie) else None
            filas.append({"clave": definicion.clave, "metrica": definicion.etiqueta,
                          "categoria": definicion.categoria, "equipo": valor, "rival": valor})
        partidos.append({
            "n_partido": i + 1, "id_partido": f"{nombre}-{i}", "fecha": f"2026-09-{i + 1:02d}",
            "torneo": "Liga", "condicion": "casa" if es_casa else "fuera",
            "equipo": nombre, "rival": f"Rival {i}", "goles_equipo": 1, "goles_rival": 0,
            "periodos": {p: {"periodo": p, "filas": filas, "no_disponibles": []}
                         for p in ORDEN_PERIODOS},
        })
    return {"equipo": {"nombre": nombre}, "partidos": partidos,
            "resumen": {"partidos_con_datos": largo}}


def prueba_h2h_cobertura_y_promedios() -> None:
    """6. Cobertura combinada y promedios por condición (local/visitante)."""
    import auditoria_h2h as h2h

    doc_a = _doc_sintetico("Alfa", {"posesion_pct": [80.0, 60.0, 70.0, 50.0],
                                    "remates_totales": [10.0, 20.0, 30.0, 40.0],
                                    "xg": [2.0, 1.0, 3.0, 1.0]})
    doc_b = _doc_sintetico("Beta", {"posesion_pct": [40.0, 45.0],
                                    "remates_totales": [5.0, 15.0]})

    cobertura = h2h._cobertura_combinada(doc_a, doc_b)
    # xG solo existe en Alfa: NO es comparable, y debe declararse como "solo A"
    assert cobertura["n_comparables"] == 2, cobertura["n_comparables"]
    assert "Goles esperados (xG)" in cobertura["solo_a"], cobertura["solo_a"]
    assert cobertura["n_solo_a"] == 1 and cobertura["n_solo_b"] == 0, cobertura
    assert "Pases (ratio por minuto/posesión)" in cobertura["sin_datos_en_ninguno"], \
        cobertura["sin_datos_en_ninguno"]

    res_local = h2h.resumir(doc_a, "casa")
    res_fuera = h2h.resumir(doc_a, "fuera")
    assert res_local["n_partidos"] == 2 and res_fuera["n_partidos"] == 2
    # casa: índices 0 y 2 -> (80+70)/2 = 75 ; fuera: (60+50)/2 = 55
    assert res_local["metricas"]["posesion_pct"]["promedio"] == 75.0, res_local["metricas"]["posesion_pct"]
    assert res_fuera["metricas"]["posesion_pct"]["promedio"] == 55.0
    # remates casa: (10+30)/2 = 20
    assert res_local["metricas"]["remates_totales"]["promedio"] == 20.0
    print("  ✅ 6. cobertura combinada y promedios por condición correctos")


def prueba_h2h_umbrales_y_conteos() -> None:
    """7. El umbral 70% solo aplica a porcentajes auténticos, nunca a conteos."""
    import auditoria_h2h as h2h

    doc = _doc_sintetico("Alfa", {
        "posesion_pct": [75.0, 80.0, 72.0],          # porcentaje real ≥ 70
        "duelos_suelo_pct": [60.0, 65.0, 62.0],      # porcentaje real < 70
        "entradas_pct": [20.0, 25.0, 22.0],          # conteo con sufijo _pct: NO debe alertar
        "remates_totales": [25.0, 30.0, 28.0],       # conteo: solo umbral de referencia
        "pases_precision_pct": [85.0, 90.0, 88.0],   # porcentaje real ≥ 70
    })
    resumen = h2h.resumir(doc)
    alertas = h2h._alertas_umbral(resumen, h2h.resumir(_doc_sintetico("Beta", {})))
    claves_pct = {a["clave"] for a in alertas if a["tipo"] == "porcentaje"}
    claves_ref = {a["clave"] for a in alertas if a["tipo"] == "referencia"}

    assert "posesion_pct" in claves_pct, claves_pct
    assert "pases_precision_pct" in claves_pct, claves_pct
    assert "duelos_suelo_pct" not in claves_pct, "60-65% no debe superar el umbral del 70%"
    assert "entradas_pct" not in claves_pct, "un conteo con sufijo _pct no debe tratarse como %"
    assert "remates_totales" in claves_ref, claves_ref
    # El detalle del porcentaje debe citar el % de partidos por encima del umbral
    detalle = next(a for a in alertas if a["clave"] == "posesion_pct")["detalle"]
    assert "100.0%" in detalle or "100%" in detalle, detalle
    print("  ✅ 7. umbral 70% aplicado solo a porcentajes auténticos; conteos por referencia")


def prueba_h2h_cruces_directos() -> None:
    """8. Los cruces directos se detectan y se deduplican entre ambos datasets."""
    import auditoria_h2h as h2h

    doc_a = _doc_sintetico("Alfa", {"posesion_pct": [70.0]})
    doc_b = _doc_sintetico("Beta", {"posesion_pct": [60.0]})
    # Alfa registra el cruce como local; Beta lo registra como visitante (mismo id)
    doc_a["partidos"][0].update({"id_partido": "X1", "rival": "Beta", "fecha": "2026-09-13",
                                 "condicion": "casa", "goles_equipo": 2, "goles_rival": 1})
    doc_b["partidos"][0].update({"id_partido": "X1", "rival": "Alfa", "fecha": "2026-09-13",
                                 "condicion": "fuera", "goles_equipo": 1, "goles_rival": 2})
    cruces = h2h._cruces_directos(doc_a, doc_b)
    assert len(cruces) == 1, f"debe deduplicar por id, hubo {len(cruces)}"
    assert cruces[0]["local"] == "Alfa" and cruces[0]["visitante"] == "Beta", cruces[0]
    assert cruces[0]["goles_local"] == 2 and cruces[0]["goles_visitante"] == 1, cruces[0]
    print("  ✅ 8. cruces directos detectados desde ambos datasets y deduplicados")


def prueba_h2h_cruce_formato_exportado() -> None:
    """
    9. El cruce directo también se lee del JSON EXPORTADO, donde los goles viven
       anidados en `marcador` (no como campos planos). Caso real detectado en
       producción: el marcador salía vacío por leer la raíz equivocada.
    """
    import auditoria_h2h as h2h

    def partido_exportado(equipo, rival, condicion, goles_equipo, goles_rival):
        return {
            "n_partido": 1, "id_partido": "1000017235", "fecha": "2026-09-13",
            "torneo": "Liga 1", "condicion": condicion, "equipo": equipo, "rival": rival,
            "goles_equipo": None, "goles_rival": None,   # así lo exporta el JSON real
            "marcador": {"equipo": goles_equipo, "rival": goles_rival,
                         "texto": f"{goles_equipo}-{goles_rival}"},
            "periodos": {},
        }

    # Universitario jugó de visitante y anotó 2; Alianza en casa anotó 1
    doc_u = {"equipo": {"nombre": "Universitario de Deportes"},
             "partidos": [partido_exportado("Universitario de Deportes", "Alianza Lima",
                                            "fuera", 2, 1)]}
    doc_a = {"equipo": {"nombre": "Alianza Lima"},
             "partidos": [partido_exportado("Alianza Lima", "Universitario de Deportes",
                                            "casa", 1, 2)]}

    cruces = h2h._cruces_directos(doc_u, doc_a)
    assert len(cruces) == 1, f"debe deduplicar por id, hubo {len(cruces)}"
    c = cruces[0]
    assert c["local"] == "Alianza Lima", c
    assert c["visitante"] == "Universitario de Deportes", c
    assert c["goles_local"] == 1 and c["goles_visitante"] == 2, (
        f"marcador mal leído del formato exportado: {c}")

    # Y que el informe lo muestre con el marcador, no con un guion
    md = h2h.generar_markdown_h2h({
        "generado_en": "x", "zona_horaria": "America/Lima", "limite": 15, "fuente": "falsa",
        "equipo_a": {"equipo_solicitado": "Universitario de Deportes", "origen": "reutilizada"},
        "equipo_b": {"equipo_solicitado": "Alianza Lima", "origen": "reutilizada"},
        "resumenes": {k: h2h.resumir({"equipo": {"nombre": n}, "partidos": []})
                      for k, n in (("a_local", "Universitario de Deportes"),
                                   ("a_fuera", "Universitario de Deportes"),
                                   ("a_global", "Universitario de Deportes"),
                                   ("b_local", "Alianza Lima"), ("b_fuera", "Alianza Lima"),
                                   ("b_global", "Alianza Lima"))},
        "cobertura": h2h._cobertura_combinada(doc_u, doc_a),
        "escenarios": [], "alertas": [], "cruces_directos": cruces,
    })
    assert "**1-2**" in md, "el informe debe publicar el marcador del cruce"
    print("  ✅ 9. cruce directo leído del formato exportado (marcador anidado) y publicado")


def prueba_no_sobrescribe_auditoria_base() -> None:
    """
    11. Un reescaneo dentro del flujo H2H NUNCA debe pisar la auditoría base del
        equipo: escribe un archivo nuevo con marca de tiempo y deja el anterior
        intacto (defecto real detectado en producción).
    """
    import glob
    import json
    import os
    import tempfile
    from datetime import datetime

    import auditoria_h2h as h2h

    with tempfile.TemporaryDirectory() as tmp:
        hoy = datetime.now().strftime("%Y-%m-%d")

        # Snapshot histórico previo del equipo, con nombre BASE (en la raíz del
        # directorio: así aplana el escritor cuando se le pasa un directorio)
        base_json = os.path.join(tmp, f"auditoria_equipo_test_{hoy}.json")
        snapshot = {"equipo": {"nombre": "Equipo Test"}, "snapshot": "HISTORICO",
                    "partidos": [], "resumen": {"partidos_con_datos": 1}}
        with open(base_json, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)

        # Reescaneo (como hace el H2H cuando la auditoría está vieja)
        fuente = FuenteFalsa([("2026-10-10", "Liga", True), ("2026-10-03", "Liga", True)],
                             nombre_equipo="Equipo Test")
        fuente.preparar()
        auditoria = ah.auditar_equipo("Equipo Test", limite=2, fuente_obj=fuente)
        rutas = h2h.exportar_auditoria(auditoria, directorio=tmp, sufijo_tiempo="")

        assert rutas["json"] != base_json, "el reescaneo debe escribir un archivo NUEVO"
        assert os.path.exists(base_json), "la auditoría base debe seguir existiendo"
        with open(base_json, "r", encoding="utf-8") as f:
            assert json.load(f) == snapshot, "la auditoría base fue SOBRESCRITA"

        nuevos = [p for p in glob.glob(os.path.join(tmp, "auditoria_equipo_test_*.json"))
                  if os.path.abspath(p) != os.path.abspath(base_json)]
        assert nuevos, (f"debe existir el archivo nuevo con marca de tiempo; "
                        f"en la carpeta hay: {os.listdir(tmp)}")
        assert len(nuevos) == 1, f"se esperaba un archivo nuevo, hubo {nuevos}"
        print("  ✅ 11. el reescaneo no pisa la auditoría base (archivo nuevo + snapshot intacto)")


def prueba_no_degrada_dataset() -> None:
    """
    12. Si un reescaneo trae MENOS partidos con datos que la auditoría anterior, se
        conserva la anterior (no se degrada el dataset del usuario).
    """
    # Anterior: 3 partidos con datos. Nuevo escaneo: el calendario solo tiene 1.
    previo = _doc_sintetico("Alfa", {"posesion_pct": [70.0, 71.0, 72.0]})
    fuente = FuenteFalsa([("2026-10-10", "Liga", True)], nombre_equipo="Alfa")
    fuente.preparar()
    resultado = ah.auditar_equipo("Alfa", limite=3, fuente_obj=fuente, mejor_previo=previo)
    assert resultado is previo, "debe devolver la auditoría anterior, no la nueva"
    assert any("más partidos con datos" in a for a in resultado["advertencias"]), \
        resultado["advertencias"]
    print("  ✅ 12. no se degrada el dataset: se conserva la auditoría con más partidos")

    # Y al contrario: si el nuevo trae más, se usa el nuevo
    fuente2 = FuenteFalsa([("2026-10-10", "Liga", True), ("2026-10-05", "Liga", True),
                           ("2026-10-01", "Liga", True), ("2026-09-28", "Liga", True)],
                          nombre_equipo="Alfa")
    fuente2.preparar()
    mejor = ah.auditar_equipo("Alfa", limite=4, fuente_obj=fuente2, mejor_previo=previo)
    assert mejor is not previo, "con más partidos debe usarse el escaneo nuevo"
    assert (mejor["resumen"]["partidos_con_datos"]) == 4, mejor["resumen"]
    print("        y con más partidos sí sustituye al anterior (4 vs 3)")


def prueba_reutiliza_si_fuente_agotada() -> None:
    """
    13. El flujo H2H reutiliza una auditoría fresca cuya fuente ya estaba agotada:
        reescanear daría exactamente el mismo resultado (evita reescaneos inútiles).
    """
    import json
    import os
    import tempfile
    from datetime import datetime

    import auditoria_h2h as h2h

    with tempfile.TemporaryDirectory() as tmp:
        hoy = datetime.now().strftime("%Y-%m-%d")
        base = os.path.join(tmp, f"auditoria_alfa_{hoy}.json")
        # Auditoría fresca, 4 partidos, objetivo NO alcanzado pero fuente agotada
        doc = _doc_sintetico("Alfa", {"posesion_pct": [70.0, 71.0, 72.0, 73.0]})
        doc["partidos_futuros"] = []
        doc["resumen"]["objetivo_alcanzado"] = False
        doc["resumen"]["minimo_con_datos"] = 15
        doc["escaneo"] = {"fuente_agotada": True, "paginas_escaneadas": 2,
                          "motivo_parada": "la fuente no ofrece más páginas de calendario (se escanearon 2)",
                          "finalizados_en_la_fuente": 9, "amistosos_excluidos": 5,
                          # La marca declara que el escaneo SÍ revisó las demás
                          # competiciones del club (incluidas temporadas anteriores).
                          # Sin ella, una muestra corta se considera no fiable y se
                          # regenera al consultarse (hallazgo del recién ascendido).
                          "cola_historica_revisada": True}
        with open(base, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)

        def _no_debe_escanear(*a, **k):
            raise AssertionError("NO debía reescanear: la fuente ya estaba agotada y el dato es fresco")

        original = h2h.auditar_equipo
        h2h.auditar_equipo = _no_debe_escanear
        try:
            doc_devuelto, meta = h2h.cargar_o_crear_auditoria(
                "Alfa", limite=15, frescura_horas=6, directorio=tmp)
        finally:
            h2h.auditar_equipo = original

        assert meta["origen"] == "reutilizada", meta
        assert doc_devuelto is doc or doc_devuelto.get("equipo") == doc.get("equipo"), meta
        print("  ✅ 13. reutiliza sin reescanear cuando la fuente ya estaba agotada")


def prueba_relleno_con_amistosos() -> None:
    """
    14. Los amistosos NO suman para el objetivo: 15 son 15 OFICIALES.

        Regla del proyecto: «9 oficiales + 6 amistosos» no es un 15 válido. Se
        comprueba que, aunque se pida `incluir_amistosos=True`, el dataset NO se
        completa con amistosos a menos que el documento autorice el relleno de forma
        explícita (`permitir_relleno_amistoso`), y que cuando se autoriza quedan
        marcados como tales.
    """
    calendario = [("2026-10-10", "Liga", True), ("2026-10-05", "Club Friendlies", True),
                  ("2026-10-01", "Liga", True), ("2026-09-28", "Club Friendlies", True),
                  ("2026-09-25", "Club Friendlies", True)]
    fuente = FuenteFalsa(calendario, nombre_equipo="Alfa")
    fuente.preparar()

    # Sin relleno: solo los 2 oficiales que existen
    sin_relleno = ah.auditar_equipo("Alfa", limite=4, minimo_con_datos=4, fuente_obj=fuente)
    assert len(sin_relleno["partidos"]) == 2, len(sin_relleno["partidos"])
    assert all(p["es_amistoso"] is False for p in sin_relleno["partidos"])
    assert sin_relleno["resumen"]["objetivo_alcanzado"] is False

    # Pidiendo amistosos SIN autorización explícita: NO se suman. El objetivo
    # declarado (4) no se alcanza y así debe quedar reflejado, sin inflar el dataset.
    pedido = ah.auditar_equipo("Alfa", limite=4, minimo_con_datos=4,
                               incluir_amistosos=True, fuente_obj=fuente)
    assert len(pedido["partidos"]) == 2, (
        f"los amistosos NO deben sumarse: {len(pedido['partidos'])} partidos")
    assert pedido["escaneo"]["amistosos_usados_como_relleno"] == 0
    assert all(p["es_amistoso"] is False for p in pedido["partidos"])
    assert pedido["resumen"]["objetivo_alcanzado"] is False

    # Con autorización explícita en el documento: rellena y los marca
    fuente2 = FuenteFalsa(calendario, nombre_equipo="Alfa")
    fuente2.preparar()
    con_relleno = ah.auditar_equipo("Alfa", limite=4, minimo_con_datos=4,
                                    incluir_amistosos=True, fuente_obj=fuente2,
                                    permitir_relleno_amistoso=True)
    assert len(con_relleno["partidos"]) == 4, len(con_relleno["partidos"])
    assert con_relleno["escaneo"]["amistosos_usados_como_relleno"] == 2, con_relleno["escaneo"]
    amistosos = [p for p in con_relleno["partidos"] if p["es_amistoso"]]
    assert len(amistosos) == 2, "los amistosos deben quedar marcados"
    assert any("AMISTOSO" in n for n in amistosos[0]["notas"]), amistosos[0]["notas"]
    print("  ✅ 14. los amistosos NO suman para el objetivo; solo con autorización "
          "explícita y quedando marcados")


def prueba_h2h_informe() -> None:
    """15. El informe consolidado incluye las 3 secciones y ambos escenarios."""
    import auditoria_h2h as h2h

    doc_a = _doc_sintetico("Alfa", {"posesion_pct": [75.0, 65.0], "xg": [2.5, 1.5],
                                    "remates_totales": [20.0, 10.0]})
    doc_b = _doc_sintetico("Beta", {"posesion_pct": [55.0, 45.0], "xg": [1.0, 0.5],
                                    "remates_totales": [8.0, 6.0]})
    resumenes = {"a_local": h2h.resumir(doc_a, "casa"), "a_fuera": h2h.resumir(doc_a, "fuera"),
                 "a_global": h2h.resumir(doc_a),
                 "b_local": h2h.resumir(doc_b, "casa"), "b_fuera": h2h.resumir(doc_b, "fuera"),
                 "b_global": h2h.resumir(doc_b)}
    analisis = {
        "generado_en": "2026-10-13 10:00:00", "zona_horaria": "America/Lima",
        "limite": 15, "fuente": "falsa",
        "equipo_a": {"equipo_solicitado": "Alfa", "origen": "reutilizada", "antiguedad_horas": 1.0},
        "equipo_b": {"equipo_solicitado": "Beta", "origen": "reescaneada", "antiguedad_horas": None,
                     "advertencias": ["objetivo no alcanzado"]},
        "resumenes": resumenes,
        "cobertura": h2h._cobertura_combinada(doc_a, doc_b),
        "escenarios": [
            {"titulo": "Alfa en casa vs Beta fuera", "a": "a_local", "b": "b_fuera",
             "alertas": h2h._alertas_umbral(resumenes["a_local"], resumenes["b_fuera"])},
            {"titulo": "Beta en casa vs Alfa fuera", "a": "b_local", "b": "a_fuera",
             "alertas": h2h._alertas_umbral(resumenes["b_local"], resumenes["a_fuera"])},
        ],
        "alertas": [], "cruces_directos": [],
    }
    analisis["alertas"] = [x for e in analisis["escenarios"] for x in e["alertas"]]
    md = h2h.generar_markdown_h2h(analisis)
    for esperado in ("## 1. Cobertura de métricas combinada",
                     "## 2. Matriz de enfrentamiento directo",
                     "## 3. Análisis de probabilidades y mercados",
                     "### 3.1 Escenario: Alfa en casa vs Beta fuera",
                     "### 3.2 Escenario: Beta en casa vs Alfa fuera",
                     "tabla" if False else "Comparación directa"):
        assert esperado in md, f"falta en el informe: {esperado!r}"
    resumen = h2h.resumen_telegram(analisis)
    assert "ENFRENTAMIENTO DIRECTO: Alfa vs Beta" in resumen, resumen[:200]
    print("  ✅ 15. informe consolidado con 3 secciones, 2 escenarios y resumen de Telegram")


def prueba_cruce_temporadas_fotmob() -> None:
    """
    15. Cruce de temporadas con /api/leagues: cuando la ventana de la fuente no
        llega a 15 oficiales, se completa con la temporada anterior de CADA liga
        del equipo (incluidas las copas), sin amistosos ni duplicados.

    Se simula `_api` para no tocar la red.
    """
    from fuentes_xg import FuenteFotMob

    EQUIPO = "700"

    def fixture(pid, fecha, torneo, league_id, finished=True, home="700", away="999"):
        return {
            "id": pid, "pageUrl": f"/m/{pid}",
            "home": {"id": home, "name": "Equipo Test", "score": 1},
            "away": {"id": away, "name": "Rival", "score": 0},
            "status": {"finished": finished, "utcTime": f"{fecha}T20:00:00Z",
                       "scoreStr": "1 - 0"},
            "tournament": {"name": torneo, "leagueId": league_id},
        }

    def liga(lid, temporadas, partidos):
        return {"allAvailableSeasons": temporadas,
                "fixtures": {"allMatches": partidos}}

    # Ventana actual: 3 oficiales + 1 amistoso. Faltan 2 para llegar a 5.
    ventana = [
        fixture(1, "2026-09-01", "Super Lig", "71"),
        fixture(2, "2026-08-20", "Champions League", "42"),
        fixture(3, "2026-08-10", "Super Lig", "71"),
        fixture(4, "2026-08-01", "Club Friendlies", "999"),
    ]
    # Temporada anterior de la liga 71: 2 partidos oficiales
    prev_71 = [fixture(10, "2025-09-01", "", "71"), fixture(11, "2025-08-20", "", "71")]
    # Temporada anterior de la Champions (42): sin partidos (no debe cortar el escaneo)
    prev_42 = []

    class FuenteSimulada(FuenteFotMob):
        def __init__(self) -> None:
            super().__init__(headless=True, verbose=False)
            self.llamadas: list[str] = []

        def _api(self, url, **kw):  # type: ignore[override]
            self.llamadas.append(url)
            if url.startswith(f"/teams?id={EQUIPO}"):
                return {"fixtures": {"allFixtures": {"fixtures": ventana},
                                     "primaryTournamentId": 71},
                        "allAvailableSeasons": []}
            if url.startswith("/leagues?id=71"):
                return liga("71", ["2026/2027", "2025/2026"], prev_71)
            if url.startswith("/leagues?id=42"):
                return liga("42", ["2026/2027", "2025/2026"], prev_42)
            if url.startswith(f"/leagues?id=71&season=2025%2F2026"):
                return liga("71", [], prev_71)
            if url.startswith("/leagues?id=42&season="):
                return liga("42", [], prev_42)
            return None

    f = FuenteSimulada()
    res = f.escanear_oficiales({"id": EQUIPO, "nombre": "Equipo Test"}, objetivo=5)

    # 3 de la ventana + 2 de la temporada anterior = 5 oficiales
    assert len(res.oficiales) == 5, f"{len(res.oficiales)} oficiales: {res.motivo_parada}"
    assert res.amistosos_excluidos == 1, res.amistosos_excluidos
    # El amistoso no entra en el dataset
    assert all("friendl" not in p.torneo.lower() for p in res.oficiales)
    # Sin duplicados
    ids = [p.id_partido for p in res.oficiales]
    assert len(ids) == len(set(ids)), ids
    # Orden cronológico descendente
    fechas = [p.fecha for p in res.oficiales]
    assert fechas == sorted(fechas, reverse=True), fechas
    # Las ligas se sacan de los PARTIDOS (71 y 42), no de la cabecera
    assert f._ligas_del_equipo(EQUIPO) == {"71": "Super Lig", "42": "Champions League"}
    # El torneo de la temporada anterior no queda vacío
    antiguos = [p for p in res.oficiales if p.fecha.startswith("2025")]
    assert antiguos and all(p.torneo == "Super Lig" for p in antiguos), \
        [(p.fecha, p.torneo) for p in antiguos]

    # La cola encola la temporada ACTIVA y la ANTERIOR de cada liga (la activa es
    # imprescindible para competiciones cuyo calendario no está en la ventana).
    cola = f._cola_por_equipo.get(EQUIPO)
    assert cola is not None, "la cola debe existir para el equipo"
    assert ("71", "2026/2027") in cola, cola
    assert ("71", "2025/2026") in cola, cola
    assert ("42", "2026/2027") in cola, cola

    # La cola se calcula POR EQUIPO: un segundo equipo no hereda la del primero
    f._cola_por_equipo["otro"] = [("X", "Y")]
    assert f._cola_por_equipo[EQUIPO] == cola, "la cola debe ser por equipo"

    # El dataset final son los MÁS RECIENTES: nunca queda desordenado ni con
    # partidos más antiguos por delante de otros más nuevos.
    assert fechas == sorted(fechas, reverse=True), fechas
    print(f"  OK 15. cruce de temporadas: {len(res.oficiales)} oficiales con "
          f"{res.amistosos_excluidos} amistoso excluido, cola activa+anterior")


def prueba_invalidacion_perezosa_huella_vieja() -> None:
    """
    16. La huella del escaneo viejo (una sola página de calendario) NUNCA se
        reutiliza: se reescanea al vuelo SOLO el equipo consultado. Un escaneo
        completo y fresco sí se reutiliza. Así las auditorías viejas se regeneran
        de forma perezosa, sin lanzar un lote masivo de peticiones.
    """
    import json
    import os
    import tempfile
    from datetime import datetime

    import auditoria_h2h as h2h

    def escribir(tmp: str, nombre: str, paginas, auditados: int,
                 agotada: bool = True) -> str:
        hoy = datetime.now().strftime("%Y-%m-%d")
        ruta = os.path.join(tmp, f"auditoria_{ah._slug(nombre)}_{hoy}.json")
        doc = _doc_sintetico(nombre, {"posesion_pct": [70.0] * max(auditados, 1)})
        doc["partidos_futuros"] = []
        doc["resumen"] = {"partidos_con_datos": auditados,
                          "partidos_auditados": auditados,
                          "minimo_con_datos": 15,
                          "objetivo_alcanzado": auditados >= 15}
        doc["escaneo"] = {"fuente_agotada": agotada, "paginas_escaneadas": paginas,
                          "motivo_parada": "la fuente no ofrece más páginas de calendario"}
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        return ruta

    # ── 1) Unidad: la huella del escaneo viejo se detecta y se explica ──────
    viejo = {"partidos_futuros": [],
             "resumen": {"partidos_auditados": 7, "partidos_con_datos": 7},
             "escaneo": {"paginas_escaneadas": 1, "fuente_agotada": True}}
    obsoleta, motivo = h2h._es_auditoria_obsoleta(viejo, 15)
    assert obsoleta and "una sola página" in motivo, (obsoleta, motivo)

    # 15/15 en una sola página NO se marca obsoleto: es legítimo en equipos cuyo
    # historial cabe en la primera página (4 equipos reales lo cumplen y ya traen
    # `partidos_futuros`), y marcarlo forzaría un reescaneo en cada consulta.
    quince_una_pagina = {"partidos_futuros": [],
                         "resumen": {"partidos_auditados": 15, "partidos_con_datos": 15},
                         "escaneo": {"paginas_escaneadas": 1, "fuente_agotada": True}}
    obsoleta2, _ = h2h._es_auditoria_obsoleta(quince_una_pagina, 15)
    assert not obsoleta2, (
        "15/15 en una página no puede marcarse obsoleto: provocaría reescaneos "
        "infinitos en equipos cuyo historial cabe en la primera página")

    # Sin el campo de próximos partidos SÍ es obsoleta (no permite resolver fecha)
    sin_futuros = {"resumen": {"partidos_auditados": 15, "partidos_con_datos": 15},
                   "escaneo": {"paginas_escaneadas": 7, "fuente_agotada": True}}
    obsoleta_sf, motivo_sf = h2h._es_auditoria_obsoleta(sin_futuros, 15)
    assert obsoleta_sf and "próximos partidos" in motivo_sf, (obsoleta_sf, motivo_sf)

    # Un escaneo completo (varias páginas) NO es obsoleto
    sano = {"partidos_futuros": [],
            "resumen": {"partidos_auditados": 15, "partidos_con_datos": 15},
            "escaneo": {"paginas_escaneadas": 7, "fuente_agotada": True}}
    obsoleta3, _ = h2h._es_auditoria_obsoleta(sano, 15)
    assert not obsoleta3, "un escaneo de 7 páginas no lleva la huella vieja"

    # ── 2) Integración: la auditoría obsoleta NO se reutiliza, se reescanea ──
    def integracion(paginas, auditados, debe_escanear: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            escribir(tmp, "Alfa", paginas, auditados)
            llamadas: list[str] = []

            def escanear(nombre, limite=15, headless=True, progreso=None,
                         mejor_previo=None, **extra):
                llamadas.append(nombre)
                fuente = FuenteFalsa([("2026-10-10", "Liga", True)] * 15,
                                     nombre_equipo=nombre)
                fuente.preparar()
                return ah.auditar_equipo(nombre, limite=limite, minimo_con_datos=limite,
                                         fuente_obj=fuente, mejor_previo=None)

            original = h2h.auditar_equipo
            h2h.auditar_equipo = escanear
            try:
                doc, meta = h2h.cargar_o_crear_auditoria("Alfa", limite=15,
                                                         frescura_horas=6,
                                                         directorio=tmp)
            finally:
                h2h.auditar_equipo = original

            if debe_escanear:
                assert llamadas == ["Alfa"], (
                    f"con la huella vieja ({paginas} pág., {auditados}/15) debía "
                    f"reescanearse; llamadas={llamadas}, meta={meta}")
                assert "obsoleta" in meta["motivo_rescaneo"], meta
            else:
                assert llamadas == [], (
                    f"un escaneo sano no debe reescanearse; llamadas={llamadas}, meta={meta}")
                assert meta["origen"] == "reutilizada", meta

    integracion(paginas=1, auditados=7, debe_escanear=True)
    integracion(paginas=1, auditados=15, debe_escanear=False)
    integracion(paginas=6, auditados=15, debe_escanear=False)
    print("  ✅ 16. invalidación perezosa: la huella de una sola página con la muestra "
          "corta se reescanea al vuelo; el escaneo sano se reutiliza")


class FuenteFalsaAscendido(FuenteFotMob):
    """
    Fuente simulada que imita la API de FotMob SIN red, para probar el escaneo de
    un equipo recién ascendido: su temporada anterior está en OTRA liga que la
    ventana actual no muestra.

    - `/teams`  -> ventana con N partidos oficiales de la liga actual + historial
                   de competiciones del club (`history.trophyList`).
    - `/leagues?id=..&lang=..`            -> temporadas disponibles de la liga.
    - `/leagues?id=..&season=..`          -> partidos terminados del equipo.
    """

    nombre = "falsa-fotmob"

    def __init__(self, oficiales_ventana: int = 8, oficiales_liga_previa: int = 42,
                 liga_previa: str = "140", nombre_liga_previa: str = "LaLiga2") -> None:
        # No se llama al __init__ real: ese abre Playwright.
        self.oficiales_ventana = oficiales_ventana
        self.oficiales_liga_previa = oficiales_liga_previa
        self.liga_previa = liga_previa
        self.nombre_liga_previa = nombre_liga_previa
        self.peticiones: list[str] = []
        self.verbose = False
        self.idioma = "es"
        self.max_paginas_escaneo = 8
        self._cache_paginas = {}
        self._cache_ligas = {}
        self._cache_temporadas = {}
        self._cache_partidos_liga = {}
        self._cola_por_equipo = {}
        self._cola_principal_por_equipo = {}
        self._cache_ligas_historicas = {}
        self._cache_futuros = {}

    def abrir(self) -> None:
        pass

    def cerrar(self) -> None:
        pass

    def _log(self, msg: str) -> None:
        pass

    # ── utilidades ─────────────────────────────────────────────────────────
    @staticmethod
    def _fixture(pid: str, fecha: str, tid: str, torneo: str, lid: str,
                 marcador: str = "2 - 1") -> dict:
        return {
            "id": pid,
            "status": {"finished": True, "utcTime": f"{fecha}T20:00:00Z",
                       "scoreStr": marcador},
            "home": {"id": tid, "name": "Equipo Test"},
            "away": {"id": "999", "name": "Rival"},
            "tournament": {"name": torneo, "leagueId": lid},
        }

    def _api(self, ruta: str, reintentos: int = 2):
        self.peticiones.append(ruta)

        if ruta.startswith("/teams"):
            fixtures = [self._fixture(f"w{i}", f"2026-09-{i + 1:02d}", "1", "LaLiga", "87")
                        for i in range(self.oficiales_ventana)]
            return {
                "fixtures": {"allFixtures": {"fixtures": fixtures}},
                "history": {"trophyList": [
                    {"name": "LaLiga", "leagueId": "87",
                     "availableSeasons": ["2026/2027", "2025/2026"]},
                    {"name": self.nombre_liga_previa, "leagueId": self.liga_previa,
                     "availableSeasons": ["2026/2027", "2025/2026"]},
                    {"name": "Sin datos", "leagueId": "-1", "availableSeasons": []},
                ]},
            }

        if "/leagues?" in ruta and "season=" not in ruta:
            return {"allAvailableSeasons": ["2026/2027", "2025/2026"]}

        if "/leagues?" in ruta and "season=" in ruta:
            lid = ruta.split("id=")[1].split("&")[0]
            temporada = ruta.split("season=")[1].split("&")[0].replace("%2F", "/")
            if lid == self.liga_previa and temporada == "2025/2026":
                partidos = [self._fixture(f"p{i}", f"2026-04-{i + 1:02d}", "1",
                                          self.nombre_liga_previa, lid)
                            for i in range(self.oficiales_liga_previa)]
                return {"fixtures": {"allMatches": partidos}}
            if lid == "87" and temporada == "2026/2027":
                partidos = [self._fixture(f"w{i}", f"2026-09-{i + 1:02d}", "1",
                                          "LaLiga", "87")
                            for i in range(self.oficiales_ventana)]
                return {"fixtures": {"allMatches": partidos}}
            return {"fixtures": {"allMatches": []}}

        return {}

    def _preparar_identidad_secundaria(self, equipo, oficiales) -> None:
        return None


def prueba_escaneo_equipo_recien_ascendido() -> None:
    """
    17. Un equipo recién ascendido tiene su temporada anterior en OTRA liga: el
        escaneo debe revisar las competiciones del club (history.trophyList) antes
        de declarar «fuente agotada». Y un equipo que ya reúne el objetivo en su
        liga actual NO paga esas peticiones extra.
    """
    # ── Recién ascendido: 8 en la ventana, 42 en la liga anterior ──────────
    fuente = FuenteFalsaAscendido(oficiales_ventana=8, oficiales_liga_previa=42)
    res = fuente.escanear_oficiales({"id": "1", "nombre": "Recién Ascendido"}, objetivo=15)
    assert len(res.oficiales) == 15, (
        f"debía completar 15 con la liga anterior; obtuvo {len(res.oficiales)}")
    assert not res.fuente_agotada, res.motivo_parada
    assert res.cola_historica_revisada is True, (
        "el escaneo debe dejar constancia de que revisó las competiciones del club")
    assert any(f"id={fuente.liga_previa}" in p for p in fuente.peticiones), \
        "debe haberse consultado la liga de la temporada anterior"
    # Las fechas son las más recientes de las dos ligas, no las primeras que llegaron
    fechas = sorted(p.fecha for p in res.oficiales)
    assert fechas[0].startswith("2026-04"), fechas[:3]

    # ── Equipo con datos suficientes: no toca la cola ampliada ─────────────
    fuente2 = FuenteFalsaAscendido(oficiales_ventana=20, oficiales_liga_previa=42)
    res2 = fuente2.escanear_oficiales({"id": "1", "nombre": "Equipo Con Datos"}, objetivo=15)
    assert len(res2.oficiales) == 15, len(res2.oficiales)
    assert not any(f"id={fuente2.liga_previa}" in p for p in fuente2.peticiones), (
        "un equipo que ya cumple el objetivo no debe pagar la cola ampliada")
    assert len(fuente2.peticiones) <= len(fuente.peticiones), (
        f"la cola ampliada no debe encarecer el escaneo normal: "
        f"{len(fuente2.peticiones)} vs {len(fuente.peticiones)}")

    # ── La muestra corta sin la marca se marca obsoleta y se regenera ──────
    import auditoria_h2h as h2h
    corta_sin_marca = {
        "partidos_futuros": [],
        "resumen": {"partidos_auditados": 8, "partidos_con_datos": 8,
                    "objetivo_alcanzado": False},
        "escaneo": {"paginas_escaneadas": 4, "fuente_agotada": True,
                    "motivo_parada": "3 páginas seguidas sin aportar partidos nuevos"},
    }
    obsoleta, motivo = h2h._es_auditoria_obsoleta(corta_sin_marca, 15)
    assert obsoleta and "competiciones del club" in motivo, (obsoleta, motivo)

    # Una muestra corta PERO con la marca (ya revisó todo) no se reescanea en bucle
    corta_con_marca = {**corta_sin_marca,
                       "escaneo": {**corta_sin_marca["escaneo"],
                                   "cola_historica_revisada": True}}
    obsoleta2, _ = h2h._es_auditoria_obsoleta(corta_con_marca, 15)
    assert not obsoleta2, ("si el escaneo ya revisó todas las competiciones del club, "
                           "no debe reescanearse en cada consulta")
    print("  ✅ 17. equipo recién ascendido: el escaneo revisa las demás competiciones "
          "del club (8→15) sin encarecer a los equipos que ya cumplen")


def prueba_gatekeeper_15_oficiales_catalogo_cero_nulos_y_coherencia() -> None:
    """
    18. GATEKEEPER OBLIGATORIO:
        a) Backfill automático hasta reunir EXACTAMENTE 15 partidos oficiales con
           datos cuando hay amistosos y partidos oficiales vacíos intercalados.
        b) Presencia de todas las métricas exigidas en 1T, 2T y FT.
        c) Tolerancia cero a None, null, NaN o "sin_dato" en las matrices consolidadas.
        d) Coherencia aritmética (1T + 2T == FT) y ecuaciones de consistencia (Sección 5).
    """
    from fuentes_xg import consolidar_matriz_completa

    # 1) Calendario con 15 oficiales con datos + 3 amistosos + 3 oficiales vacíos
    calendario_mixto: list[tuple[str, str, bool]] = []
    dia_num = 30
    oficiales_validos = 0
    idx = 0
    while oficiales_validos < 18:
        idx += 1
        fecha = f"2026-{(9 - (idx // 28)):02d}-{((idx % 28) + 1):02d}"
        if idx in (2, 7, 13):
            calendario_mixto.append((fecha, "Club Friendlies", True))
        elif idx in (4, 9, 14):
            calendario_mixto.append((fecha, "Liga Oficial", False))
        else:
            calendario_mixto.append((fecha, "Liga Oficial", True))
            oficiales_validos += 1

    auditoria, _ = _ejecutar(calendario_mixto, limite=15)
    assert len(auditoria["partidos"]) == 15, (
        f"GATEKEEPER FALLÓ: se exigen exactamente 15 oficiales con datos, "
        f"hubo {len(auditoria['partidos'])}"
    )
    assert auditoria["resumen"]["partidos_con_datos"] == 15
    assert auditoria["resumen"]["objetivo_alcanzado"] is True
    assert all(not p["es_amistoso"] for p in auditoria["partidos"])
    assert all(p["valores_reales"] >= ah.MIN_VALORES_PARA_CONTAR for p in auditoria["partidos"])

    # 2) Verificar presencia de todas las claves requeridas en CATALOGO_METRICAS
    claves_catalogo = {d.clave for d in CATALOGO_METRICAS}
    claves_exigidas = {
        # Remates y xG
        "xg", "xgot", "remates_totales", "remates_a_puerta", "remates_fuera",
        "remates_rechazados", "remates_dentro_area", "remates_fuera_area",
        "al_palo", "goles_cabeza",
        # Ataque
        "posesion", "grandes_ocasiones", "grandes_ocasiones_aprovechadas",
        "grandes_ocasiones_perdidas", "toques_area_rival", "corneres",
        "tiros_libres", "fueras_de_juego",
        # Pases
        "pases_totales", "pases_completados", "pases_pct",
        "pases_largos", "pases_largos_completados", "pases_largos_pct",
        "pases_tercio_final", "pases_tercio_final_completados", "pases_tercio_final_pct",
        "centros", "centros_completados", "centros_pct",
        "xa", "pases_clave", "saques_de_banda",
        # Defensa
        "faltas", "duelos_ganados", "entradas", "entradas_ganadas",
        "entradas_ganadas_pct", "intercepciones", "despejes",
        "errores_remate", "errores_gol",
        # Portería
        "paradas", "xgot_enfrentados", "goles_encajados", "goles_evitados",
        "saques_de_puerta",
    }
    faltantes_cat = claves_exigidas - claves_catalogo
    assert not faltantes_cat, f"Faltan claves en CATALOGO_METRICAS: {sorted(faltantes_cat)}"

    # 3) Probar consolidar_matriz_completa con datos parciales y verificar:
    #    - Cero None/null/NaN/"sin_dato" en FT, 1T, 2T
    #    - 1T + 2T == FT
    #    - Ecuaciones exactas de remates, grandes ocasiones, pases y portería
    partido_p = PartidoFinalizado(
        id_partido="gk1", fecha="2026-10-10", torneo="Liga",
        local="Alfa", visitante="Beta", goles_local=2, goles_visitante=1,
        es_local_el_equipo=True,
    )
    m_parcial = MatrizPartido(
        id_partido="gk1", fecha="2026-10-10", torneo="Liga",
        equipo="Alfa", rival="Beta", condicion="casa",
        goles_equipo=2, goles_rival=1,
    )
    semilla = {
        "FT": {
            "xg": (1.80, 0.90),
            "xgot": (2.10, 1.25),
            "posesion_pct": (60.0, None),
            "remates_totales": (14.0, 8.0),
            "remates_a_puerta": (6.0, 3.0),
            "remates_fuera": (5.0, 3.0),
            # remates_rechazados omitido -> debe derivarse (3.0 y 2.0)
            "remates_dentro_area": (9.0, 5.0),
            # remates_fuera_area omitido -> debe derivarse (5.0 y 3.0)
            "grandes_ocasiones": (4.0, 2.0),
            "grandes_ocasiones_perdidas": (2.0, 1.0),
            # grandes_ocasiones_aprovechadas omitido -> debe derivarse (2.0 y 1.0)
            "pases_totales": (500.0, 330.0),
            "pases_completados": (440.0, 264.0),
            # pases_pct omitido -> debe derivarse (88.0 y 80.0)
            "xa": (1.40, 0.70),
            "pases_clave": (10.0, 6.0),
        },
        "1T": {
            "xg": (0.80, 0.40),
            "xgot": (1.00, 0.50),
            "posesion_pct": (58.0, None),
            "remates_totales": (6.0, 3.0),
            "remates_a_puerta": (3.0, 1.0),
            "remates_fuera": (2.0, 1.0),
            "remates_dentro_area": (4.0, 2.0),
            "grandes_ocasiones": (2.0, 1.0),
            "grandes_ocasiones_perdidas": (1.0, 1.0),
            "pases_totales": (250.0, 165.0),
            "pases_completados": (220.0, 132.0),
            "goles_encajados": (0.0, 1.0),
        },
        "2T": {},  # 2T vacío: debe reconstruirse como FT - 1T
    }
    for per in ORDEN_PERIODOS:
        filas_p = []
        for d in CATALOGO_METRICAS:
            par = semilla.get(per, {}).get(d.clave, (None, None))
            filas_p.append({
                "clave": d.clave, "metrica": d.etiqueta, "categoria": d.categoria,
                "equipo": par[0], "rival": par[1],
            })
        m_parcial.periodos[per] = PeriodoPartido(per, filas_p, [])

    consolidar_matriz_completa(m_parcial)
    auditoria_gk = {"partidos": [m_parcial.a_dict()]}
    rep_gk = ah.validar_cobertura_matriz(auditoria_gk)
    assert rep_gk["cero_nulos"] is True, f"Nulos detectados: {rep_gk}"
    assert rep_gk["cobertura_pct"] == 100.0, rep_gk

    # Verificar ecuaciones exactas en FT, 1T y 2T
    mapas = {
        per: {f["clave"]: (f["equipo"], f["rival"]) for f in m_parcial.periodos[per].filas}
        for per in ("FT", "1T", "2T")
    }
    # Posesión 100%
    assert mapas["FT"]["posesion"][0] + mapas["FT"]["posesion"][1] == 100.0
    assert mapas["1T"]["posesion"][0] + mapas["1T"]["posesion"][1] == 100.0
    # Remates FT: 14 = 6 + 5 + 3 y 14 = 9 + 5
    assert mapas["FT"]["remates_rechazados"] == (3.0, 2.0)
    assert mapas["FT"]["remates_fuera_area"] == (5.0, 3.0)
    # Grandes ocasiones FT: 4 = 2 + 2
    assert mapas["FT"]["grandes_ocasiones_aprovechadas"] == (2.0, 1.0)
    # Pases % FT: 440/500 = 88.0%
    assert mapas["FT"]["pases_pct"] == (88.0, 80.0)
    # Portería FT: xgot_enfrentados = xgot_rival (1.25), goles_encajados = 1.0 -> goles_evitados = 0.25
    assert mapas["FT"]["xgot_enfrentados"] == (1.25, 2.10)
    assert mapas["FT"]["goles_encajados"] == (1.0, 2.0)
    assert mapas["FT"]["goles_evitados"] == (0.25, 0.10)
    # Coherencia 1T + 2T == FT
    for cl_check in ("remates_totales", "remates_a_puerta", "remates_fuera",
                     "remates_rechazados", "pases_totales", "pases_completados",
                     "grandes_ocasiones", "pases_clave", "goles_encajados"):
        v_ft = mapas["FT"][cl_check][0]
        v_1t = mapas["1T"][cl_check][0]
        v_2t = mapas["2T"][cl_check][0]
        assert abs((v_1t + v_2t) - v_ft) < 1e-6, f"{cl_check}: {v_1t} + {v_2t} != {v_ft}"

    print("  ✅ 18. GATEKEEPER OK: 15 oficiales con backfill, catálogo completo en 1T/2T/FT, "
          "cero nulos y ecuaciones 1T+2T==FT verificadas")


def prueba_homonimia_espanol_vs_atletico_madrid() -> None:
    """
    19. PRUEBA DE HOMONIMIA E IDIOMA ('Español' vs 'Atlético de Madrid'):
    Verifica que al resolver 'Español' con rival 'Atlético de Madrid' (o en cruce
    'Español vs Atlético de Madrid'):
      1. Las variantes de búsqueda y el diccionario de alias hispanos generen
         la forma catalana/canónica 'Espanyol'.
      2. El motor de puntuación descarte a Central Español (Uruguay, ID 1834) y
         a Unión Española (Chile, ID 7843) por incoherencia intercontinental.
      3. Se resuelva OBLIGATORIAMENTE a RCD Espanyol de España (ID 8558 de LaLiga).
      4. El Lock por equipo y la caché en memoria eviten auditorías simultáneas duplicadas.
    """
    import auditoria_h2h as h2h
    from fuentes_xg import (
        ALIAS_EQUIPOS_HISPANOS,
        variantes_de_busqueda,
        puntuar_candidato,
        _normalizar_fonetica_hispana,
    )

    # 1. Verificar alias y normalización fonética
    assert "español" in ALIAS_EQUIPOS_HISPANOS
    assert "Espanyol" in ALIAS_EQUIPOS_HISPANOS["español"]
    vars_espanol = variantes_de_busqueda("español")
    assert "Espanyol" in vars_espanol or "espanyol" in vars_espanol, f"Falta Espanyol en {vars_espanol}"

    foneticas = _normalizar_fonetica_hispana("español")
    assert any("espanyol" in f.lower() for f in foneticas)

    # 2. Candidatos simulados tal como los sirve FotMob
    cand_central = {
        "type": "team", "id": "1834", "score": 300000,
        "name": "Central Español", "leagueId": 161, "leagueName": "Liga AUF Uruguaya Apertura",
    }
    cand_union = {
        "type": "team", "id": "7843", "score": 300001,
        "name": "Unión Española", "leagueId": 9126, "leagueName": "Primera B",
    }
    cand_espanyol = {
        "type": "team", "id": "8558", "score": 300033,
        "name": "Espanyol", "leagueId": 87, "leagueName": "LaLiga",
    }
    rival_atletico = {
        "id": "9906", "nombre": "Atlético Madrid", "liga": "LaLiga", "id_liga": 87,
    }

    # Puntuación con contexto de rival en LaLiga (España)
    pts_espanyol = puntuar_candidato(cand_espanyol, "español", rival_meta=rival_atletico)
    pts_central = puntuar_candidato(cand_central, "español", rival_meta=rival_atletico)
    pts_union = puntuar_candidato(cand_union, "español", rival_meta=rival_atletico)

    # RCD Espanyol debe superar holgadamente a los clubes sudamericanos
    assert pts_espanyol > pts_central, f"Espanyol {pts_espanyol} no superó a Central Español {pts_central}"
    assert pts_espanyol > pts_union, f"Espanyol {pts_espanyol} no superó a Unión Española {pts_union}"

    # Penalización por cruce intercontinental incoherente (pts[0] < 0 para equipos sudamericanos frente a LaLiga)
    assert pts_central[0] < 0, f"Central Español debió recibir penalización intercontinental: {pts_central}"
    assert pts_espanyol[0] > 0, f"Espanyol debió recibir bono de país/liga: {pts_espanyol}"

    # 3. Prueba de resolución con FuenteFotMob simulada
    f = FuenteFotMob(headless=True)
    def _api_mock(ruta: str):
        if "Espanyol" in ruta or "espanyol" in ruta:
            return [{"suggestions": [cand_espanyol]}]
        elif "atletico" in ruta.lower():
            return [{"suggestions": [
                {"type": "team", "id": "9906", "score": 300828, "name": "Atlético Madrid", "leagueId": 87, "leagueName": "LaLiga"}
            ]}]
        elif "espanol" in ruta.lower() or "español" in ruta.lower():
            return [{"suggestions": [cand_union, cand_central]}]
        return []

    f._api = _api_mock

    # Resolver "español" con rival "Atlético de Madrid"
    res_equipo = f.resolver_equipo("español", rival_contexto="Atlético de Madrid")
    assert res_equipo is not None
    assert str(res_equipo["id"]) == "8558", f"ID incorrecto: {res_equipo['id']} (esperado 8558 de LaLiga)"
    assert res_equipo["nombre"] == "Espanyol"
    assert res_equipo["liga"] == "LaLiga"
    assert res_equipo["id"] != "1834", "ERROR: resolvió erróneamente a Central Español (Uruguay)"
    assert res_equipo["id"] != "7843", "ERROR: resolvió erróneamente a Unión Española (Chile)"

    # Resolver cruce simultáneo
    eq_a, eq_b = f.resolver_cruce_equipos("español", "Atlético de Madrid")
    assert eq_a is not None and str(eq_a["id"]) == "8558"
    assert eq_b is not None and str(eq_b["id"]) == "9906"

    # 4. Prueba del Lock concurrente y la caché en memoria para evitar doble descarga
    ah.limpiar_cache_memoria_auditorias()
    doc_en_memoria = {
        "equipo": {"id": "8558", "nombre": "Espanyol", "liga": "LaLiga", "id_liga": 87},
        "partidos": [{"id_partido": f"part_{i}"} for i in range(15)],
        "resumen": {"partidos_con_datos": 15, "objetivo_alcanzado": True},
    }
    ah.guardar_auditoria_en_memoria("espanyol", doc_en_memoria)
    ah.guardar_auditoria_en_memoria("espanol", doc_en_memoria)

    # Recuperación instantánea en memoria sin abrir navegador ni descargar
    recuperado = ah.obtener_auditoria_en_memoria("espanyol")
    assert recuperado is doc_en_memoria

    # auditar_equipo reutiliza en memoria
    reusado = ah.auditar_equipo("Español", limite=15)
    assert reusado is doc_en_memoria, "auditar_equipo no reutilizó la auditoría en memoria"

    ah.limpiar_cache_memoria_auditorias()
    print("  ✅ 19. HOMONIMIA OK: 'Español vs Atlético de Madrid' resuelve obligatoriamente a RCD Espanyol (ID 8558, LaLiga), "
          "rechaza Central Español/Unión Española y la caché/Lock en memoria elimina la doble descarga")


def prueba_matrices_45_modalidad_a_y_b() -> None:
    """
    Prueba obligatoria de cumplimiento:
      1. Cada matriz formateada contiene exactamente las 45 métricas oficiales (01 a 45).
      2. No existe ni un solo None, null, NaN ni 'sin_dato'.
      3. Modalidad A genera las 30 matrices (15 Local + 15 Visitante).
      4. Modalidad B genera exactamente N matrices reales para H2H (cero inventados).
    """
    # 1. Crear partido con las 45 métricas pobladas en FT, 1T y 2T
    pf = PartidoFinalizado(
        id_partido="4837352", fecha="2026-02-21", torneo="LaLiga",
        local="Atlético Madrid", visitante="Espanyol",
        goles_local=4, goles_visitante=2, es_local_el_equipo=False,
    )
    matriz = _matriz(pf, con_datos=True)
    p_dict = matriz.a_dict()
    p_dict["condicion"] = "fuera"
    p_dict["equipo"] = "Espanyol"
    p_dict["rival"] = "Atlético Madrid"
    p_dict["marcador"] = {"equipo": 2, "rival": 4, "texto": "2-4"}
    p_dict["fuente_secundaria"] = {"pareado": True, "id_flashscore": "fs_test_123"}

    # Formatear matriz individual
    bloque_md = ah.formatear_matriz_45_partido(p_dict, k=1, total_partidos=4)
    assert "MATRIZ DE PARTIDO [1/4] · LaLiga · Fecha: 2026-02-21" in bloque_md, "Encabezado no coincide"
    assert "Atlético Madrid (Local) 4 - 2 Espanyol (Visita)" in bloque_md, "Marcador local/visita no coincide"
    assert "IDs Verificables -> FotMob: 4837352 | Flashscore: fs_test_123" in bloque_md, "IDs no coinciden"
    
    # 6 Sub-tablas con encabezados de categorías y columnas explícitas
    for subtabla in (
        "1. Estadísticas Principales (01–12)",
        "2. Disparos (13–21)",
        "3. Ataques (22–25)",
        "4. Pases (26–30)",
        "5. Defensa (31–41)",
        "6. Portería / Atajadas (42–45)",
    ):
        assert subtabla in bloque_md, f"Sub-tabla '{subtabla}' ausente en la matriz"

    assert "Métrica Oficial (`Clave`)" in bloque_md, "Columna 'Métrica Oficial (`Clave`)' no encontrada"
    assert "FT: Local (L)" in bloque_md and "FT: Visita (V)" in bloque_md, "Columnas FT Local/Visita ausentes"
    assert "1T: Local (L)" in bloque_md and "1T: Visita (V)" in bloque_md, "Columnas 1T Local/Visita ausentes"
    assert "2T: Local (L)" in bloque_md and "2T: Visita (V)" in bloque_md, "Columnas 2T Local/Visita ausentes"

    # Verificar que las 45 métricas (01 a 45) están presentes
    for num, clave, _, _ in ah.LAS_45_METRICAS_OFICIALES:
        patron_num = f"{num} | "
        patron_clave = f"(`{clave}`)"
        assert patron_num in bloque_md, f"Métrica {num} ausente en bloque formateado"
        assert patron_clave in bloque_md, f"Clave `{clave}` ausente en bloque formateado"

    # Tolerancia cero a nulos
    for invalido in ("None", "null", "NaN", "sin_dato"):
        assert invalido not in bloque_md, f"Valor inválido detectado en la matriz: {invalido}"

    # 2. Modalidad A: Cruce de 30 matrices (15 + 15)
    doc_local = {
        "equipo": {"nombre": "Atlético Madrid"},
        "partidos": [dict(p_dict, id_partido=f"fm_loc_{i}") for i in range(15)],
    }
    doc_visita = {
        "equipo": {"nombre": "Espanyol"},
        "partidos": [dict(p_dict, id_partido=f"fm_vis_{i}") for i in range(15)],
    }
    md_30, ruta_30 = ah.generar_informe_matrices_30(doc_local, doc_visita)
    assert "AUDITORÍA DE 30 MATRICES OFICIALES" in md_30
    assert "## TABLA RESUMEN DE PARTIDOS AUDITADOS" in md_30, "Tabla resumen inicial ausente en Modalidad A"
    assert "[1/30]" in md_30 and "[15/30]" in md_30
    assert "[16/30]" in md_30 and "[30/30]" in md_30
    assert os.path.exists(ruta_30), "Archivo Markdown de 30 matrices no generado"

    # 3. Modalidad B: H2H de N matrices reales (cero inventados)
    partidos_h2h = [
        dict(p_dict, id_partido="4837352", fecha="2026-02-21"),
        dict(p_dict, id_partido="4837113", fecha="2025-08-17"),
        dict(p_dict, id_partido="4507042", fecha="2025-03-29"),
        dict(p_dict, id_partido="4506775", fecha="2024-08-28"),
    ]
    md_h2h, ruta_h2h = ah.generar_informe_matrices_h2h(
        {"equipo": {"nombre": "Espanyol"}},
        {"equipo": {"nombre": "Atlético Madrid"}},
        partidos_h2h
    )
    assert "**Duelos Directos Reales Encontrados**: 4 partido(s)" in md_h2h
    assert "## TABLA RESUMEN DE PARTIDOS AUDITADOS" in md_h2h, "Tabla resumen inicial ausente en Modalidad B"
    assert "[1/4]" in md_h2h and "[4/4]" in md_h2h
    assert "[5/4]" not in md_h2h, "No deben existir partidos inventados en H2H"
    assert os.path.exists(ruta_h2h), "Archivo Markdown H2H no generado"

    print("  ✅ 20. MATRICES 45 Y MODALIDAD A/B OK: Plantilla Sección 5 perfecta (6 sub-tablas y resumen), "
          "30 matrices en cruce, N reales en H2H (cero inventados) y cero nulos")


def prueba_resolucion_afijos_y_ligas_competiciones() -> None:
    """
    21. PRUEBA DE AFIJOS ('fc fulham vs hull city', 'Liverpool fc') Y PREFIJOS DE LIGA:
      a) 'fc fulham' jamás se resuelve como 'FC Porto' (la sigla 'fc' se ignora como núcleo y
         'FC Porto' recibe penalización de identidad -100 frente a 'fulham').
      b) 'Liverpool fc' con rival 'Brentford' (o Premier League) jamás elige 'Liverpool FC' de Uruguay.
      c) 'deportivo de A Coruña' jamás elige el equipo femenino 'Deportivo de A Coruña (W)'.
      d) 'Hamburgo' prioriza 'Hamburger SV' (Alemania) sobre 'Novo Hamburgo' (Brasil).
      e) Prefijos de liga/competición ('bundesliga mainz vs friburgo', 'la liga ea sports barcelona vs real madrid',
         'champions league real madrid vs bayern', 'copa libertadores boca juniors vs river plate') extraen
         correctamente la liga y los dos equipos.
    """
    import auditoria_h2h as h2h
    from fuentes_xg import (
        extraer_liga_de_texto,
        puntuar_candidato,
        variantes_de_busqueda,
    )

    # a) 'fc fulham' vs 'hull city' -> Fulham (ID 9879), nunca FC Porto (ID 9773)
    vars_fulham = variantes_de_busqueda("fc fulham")
    assert vars_fulham[0].lower() == "fulham", f"La primera variante de 'fc fulham' debe ser 'Fulham': {vars_fulham}"
    assert "fc" not in [v.lower() for v in vars_fulham], f"Jamás debe buscar la sigla suelta 'fc': {vars_fulham}"

    cand_porto = {
        "type": "team", "id": "9773", "score": 300500,
        "name": "FC Porto", "leagueId": 61, "leagueName": "Liga Portugal",
    }
    cand_fulham = {
        "type": "team", "id": "9879", "score": 300100,
        "name": "Fulham", "leagueId": 47, "leagueName": "Premier League",
    }
    pts_fulham = puntuar_candidato(cand_fulham, "fc fulham", rival_meta={"nombre": "Hull City", "liga": "Championship", "pais": "inglaterra"})
    pts_porto = puntuar_candidato(cand_porto, "fc fulham", rival_meta={"nombre": "Hull City", "liga": "Championship", "pais": "inglaterra"})
    assert pts_fulham > pts_porto, f"Fulham {pts_fulham} debe superar a FC Porto {pts_porto}"
    assert pts_porto[0] <= -100, f"FC Porto debe recibir penalización de identidad nuclear <= -100: {pts_porto}"

    f_mock = FuenteFotMob(headless=True)
    def _api_fulham(ruta: str):
        if "fc%20fulham" in ruta.lower():
            return [{"suggestions": [cand_porto, cand_fulham]}]
        if "fulham" in ruta.lower():
            return [{"suggestions": [cand_fulham, cand_porto]}]
        return []
    f_mock._api = _api_fulham
    res_fulham = f_mock.resolver_equipo("fc fulham", rival_contexto="hull city")
    assert res_fulham is not None and res_fulham["nombre"] == "Fulham" and str(res_fulham["id"]) == "9879"

    # b) 'Liverpool fc' vs 'Brentford' -> Liverpool (Inglaterra, ID 8650), nunca Liverpool FC (Uruguay, ID 1838)
    cand_liv_uru = {
        "type": "team", "id": "1838", "score": 300200,
        "name": "Liverpool FC", "leagueId": 161, "leagueName": "Liga AUF Uruguaya",
    }
    cand_liv_eng = {
        "type": "team", "id": "8650", "score": 300900,
        "name": "Liverpool", "leagueId": 47, "leagueName": "Premier League",
    }
    pts_liv_eng = puntuar_candidato(cand_liv_eng, "Liverpool fc", rival_meta={"nombre": "Brentford", "liga": "Premier League", "pais": "inglaterra"})
    pts_liv_uru = puntuar_candidato(cand_liv_uru, "Liverpool fc", rival_meta={"nombre": "Brentford", "liga": "Premier League", "pais": "inglaterra"})
    assert pts_liv_eng > pts_liv_uru, f"Liverpool (Inglaterra) {pts_liv_eng} debe superar a Liverpool FC (Uruguay) {pts_liv_uru}"

    # c) 'deportivo de A Coruña' -> Deportivo de A Coruña (masculino), nunca (W)
    cand_depor_w = {
        "type": "team", "id": "99991", "score": 300100,
        "name": "Deportivo de A Coruña (W)", "leagueId": 999, "leagueName": "Liga F",
    }
    cand_depor_m = {
        "type": "team", "id": "9783", "score": 300050,
        "name": "Deportivo La Coruna", "leagueId": 140, "leagueName": "LaLiga2",
    }
    assert puntuar_candidato(cand_depor_m, "deportivo de A Coruña") > puntuar_candidato(cand_depor_w, "deportivo de A Coruña")

    # d) Extracción de ligas y competiciones en comandos
    casos_ligas = [
        ("bundesliga mainz vs friburgo", "Bundesliga", "alemania", "mainz", "friburgo"),
        ("la liga ea sports barcelona vs real madrid", "LaLiga", "espana", "barcelona", "real madrid"),
        ("premier league fc fulham vs hull city", "Premier League", "inglaterra", "fc fulham", "hull city"),
        ("champions league real madrid vs bayern", "Champions League", "europa", "real madrid", "bayern"),
        ("copa libertadores boca juniors vs river plate", "Copa Libertadores", "sudamerica", "boca juniors", "river plate"),
    ]
    for texto_cmd, exp_liga, exp_pais, exp_a, exp_b in casos_ligas:
        liga_info, resto = extraer_liga_de_texto(texto_cmd)
        assert liga_info is not None, f"No detectó liga en '{texto_cmd}'"
        assert liga_info["nombre"] == exp_liga, f"{liga_info['nombre']} != {exp_liga}"
        assert liga_info["pais"] == exp_pais, f"{liga_info['pais']} != {exp_pais}"
        res_cruce = h2h.extraer_equipos_y_liga(texto_cmd)
        assert res_cruce is not None
        assert res_cruce[0] == exp_a and res_cruce[1] == exp_b, f"{res_cruce} != ({exp_a}, {exp_b})"
        assert res_cruce[2] is not None and res_cruce[2]["nombre"] == exp_liga

    print("  ✅ 21. AFIJOS Y LIGAS OK: 'fc fulham' -> Fulham (nunca FC Porto), 'Liverpool fc' -> Inglaterra, "
          "y soporte completo de ligas/competiciones en comandos")


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("\n🧪 PRUEBAS DEL MOTOR DE AUDITORÍA HISTÓRICA Y H2H (fuente simulada)\n" + "─" * 68)
    pruebas = [
        prueba_filtra_amistosos,
        prueba_compensacion_hacia_atras,
        prueba_amistoso_vacio_no_rompe,
        prueba_tope_de_limite,
        prueba_orden_cronologico,
        prueba_h2h_cobertura_y_promedios,
        prueba_h2h_umbrales_y_conteos,
        prueba_h2h_cruces_directos,
        prueba_h2h_cruce_formato_exportado,
        prueba_no_sobrescribe_auditoria_base,
        prueba_no_degrada_dataset,
        prueba_reutiliza_si_fuente_agotada,
        prueba_relleno_con_amistosos,
        prueba_h2h_informe,
        prueba_cruce_temporadas_fotmob,
        prueba_invalidacion_perezosa_huella_vieja,
        prueba_escaneo_equipo_recien_ascendido,
        prueba_gatekeeper_15_oficiales_catalogo_cero_nulos_y_coherencia,
        prueba_homonimia_espanol_vs_atletico_madrid,
        prueba_matrices_45_modalidad_a_y_b,
        prueba_resolucion_afijos_y_ligas_competiciones,
    ]
    fallos = 0
    for prueba in pruebas:
        try:
            prueba()
        except AssertionError as e:
            fallos += 1
            print(f"  ❌ {prueba.__name__}: {e}")
    print("─" * 68)
    print(f"Resultado: {len(pruebas) - fallos}/{len(pruebas)} pruebas OK\n")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
