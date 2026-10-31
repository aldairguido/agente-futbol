"""
Pruebas del adaptador de Flashscore (Puntos 2 y 3). Sin red: todo simulado con
respuestas del feed en su formato real (clave÷valor¬ separado por ~).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fuente_flashscore as fs  # noqa: E402


# ─── Fixtures: el feed en su formato real ────────────────────────────────────

def feed_de_ejemplo() -> str:
    """Respuesta real reducida, con la estructura exacta del feed."""
    campos = [
        ("SG", "Goles esperados (xG)"), ("SH", "0.15"), ("SI", "0.65"),
        ("SG", "Posesión"), ("SH", "54%"), ("SI", "46%"),
        ("SG", "Pases"), ("SH", "88% (260/296)"), ("SI", "85% (205/240)"),
        ("SG", "Tiros libres"), ("SH", "6"), ("SI", "5"),
        ("SG", "Saques de puerta"), ("SH", "1"), ("SI", "4"),
        ("SG", "Errores conducentes a remate"), ("SH", "0"), ("SI", "0"),
        ("SG", "Errores conducentes a gol"), ("SH", "1"), ("SI", "0"),
        ("SG", "Córneres"), ("SH", "1"), ("SI", "3"),
        ("SG", "Etiqueta desconocida sin mapeo"), ("SH", "9"), ("SI", "9"),
    ]
    bloques = []
    i = 0
    while i < len(campos):
        bloque = "¬".join(f"{k}÷{v}" for k, v in campos[i:i + 3])
        bloques.append(bloque)
        i += 3
    return "~".join(bloques) + "~"


def resultados_de_ejemplo() -> str:
    """Feed de resultados: dos partidos 5-1 el mismo día (caso ambiguo real)."""
    def partido(pid, estado, elocal, evisit, gl, gv, epoch="1791972000"):
        return "¬".join([
            f"AA÷{pid}", f"AD÷{epoch}", f"AB÷{estado}",
            f"AE÷{elocal}", f"AF÷{evisit}", f"AG÷{gl}", f"AH÷{gv}",
        ])
    bloques = [
        partido("21mxSPJb", "3", "Kashiwa (Jpn)", "Port MTI FC (Tha)", "5", "1"),
        partido("4Srj0NUb", "3", "Ararat 2", "Lernayin Artsakh", "5", "1"),
        partido("bTtcKF3S", "3", "Hajer", "Al Orubah", "", ""),
        partido("NOJUGADO", "1", "A", "B", "", ""),
        partido("ENJUEGO", "2", "C", "D", "1", "0"),
    ]
    return "~".join(bloques) + "~"


# ─── 1. Parseo del feed ─────────────────────────────────────────────────────

def prueba_parseo_feed() -> None:
    metricas = fs.parsear_feed(feed_de_ejemplo())
    assert metricas["Tiros libres"] == ("6", "5"), metricas.get("Tiros libres")
    assert metricas["Saques de puerta"] == ("1", "4")
    assert metricas["Pases"] == ("88% (260/296)", "85% (205/240)")
    assert len(metricas) == 9, len(metricas)
    # Formato vacío o basura no revienta
    assert fs.parsear_feed("") == {}
    assert fs.parsear_feed("basura sin formato") == {}
    print("  OK 1. parseo del formato clave/valor del feed")


# ─── 2. Conversion de valores ──────────────────────────────────────────────

def prueba_conversion_valores() -> None:
    assert fs._a_numero("6") == 6.0
    assert fs._a_numero("54%") == 54.0
    assert fs._a_numero("0.15") == 0.15
    assert fs._a_numero("88% (260/296)") == 260.0, "se toma el numerador"
    assert fs._a_numero("") is None
    assert fs._a_numero("-") is None
    assert fs._a_numero("—") is None
    assert fs._a_numero(None) is None
    # Un valor no numerico no se convierte en 0
    assert fs._a_numero("N/D") is None
    print("  OK 2. conversion de valores, incluido el numerador de los porcentajes")


# ─── 3. pases_ratio derivado ───────────────────────────────────────────────

def prueba_pases_ratio_derivado() -> None:
    # 260 pases con 54% de posesion -> 260 / (90*0.54)
    r = fs.pases_ratio_derivado(260.0, 54.0)
    assert r is not None and abs(r - 260 / (90 * 0.54)) < 1e-12, r
    # Con 50% de posesion el cociente es exacto y facil de comprobar
    assert abs(fs.pases_ratio_derivado(450.0, 50.0) - 10.0) < 1e-12
    # Sin los dos datos NO se inventa
    assert fs.pases_ratio_derivado(None, 54.0) is None
    assert fs.pases_ratio_derivado(260.0, None) is None
    assert fs.pases_ratio_derivado(None, None) is None
    # Posesion 0 -> division imposible -> None (no infinito)
    assert fs.pases_ratio_derivado(260.0, 0.0) is None
    print("  OK 3. pases_ratio derivado, y None si falta cualquier dato")


# ─── 4. Relleno que NUNCA sobrescribe la fuente primaria ───────────────────

def prueba_relleno_no_sobrescribe() -> None:
    propias = {"xg": 0.15, "corners": 2.0, "tiros_libres": None, "saques_puerta": None}
    del_feed = {"xg": 9.99, "corners": 9.99, "tiros_libres": 6.0, "saques_puerta": 1.0}
    valores, proc = fs.rellenar_metricas(propias, del_feed, solo_vacias=True)
    # FotMob manda: no se sobrescribe
    assert valores["xg"] == 0.15, valores["xg"]
    assert valores["corners"] == 2.0, valores["corners"]
    assert proc["xg"] == fs.PROC_FOTMOB
    assert proc["corners"] == fs.PROC_FOTMOB
    # Lo vacio se rellena y se marca como flashscore
    assert valores["tiros_libres"] == 6.0 and proc["tiros_libres"] == fs.PROC_FLASHSCORE
    assert valores["saques_puerta"] == 1.0 and proc["saques_puerta"] == fs.PROC_FLASHSCORE
    # Un None del feed nunca sustituye un valor real ni crea uno
    valores2, proc2 = fs.rellenar_metricas({"xg": 1.0, "tiros_libres": None},
                                           {"xg": None, "tiros_libres": None})
    assert valores2["xg"] == 1.0 and valores2["tiros_libres"] is None
    print("  OK 4. relleno: la fuente primaria manda y los None no se inventan")


# ─── 5. Procedencia completa ───────────────────────────────────────────────

def prueba_procedencia() -> None:
    propias = {"xg": 0.15, "pases_total": 260.0, "posesion_pct": 54.0,
               "tiros_libres": None, "saques_puerta": None,
               "errores_remate": None, "pases_ratio": None}
    del_feed = {"tiros_libres": 6.0, "saques_puerta": 1.0, "errores_remate": 0.0}
    valores, proc = fs.completar_con_feed(propias, del_feed)
    assert proc["xg"] == fs.PROC_FOTMOB
    assert proc["pases_total"] == fs.PROC_FOTMOB
    assert proc["tiros_libres"] == fs.PROC_FLASHSCORE
    assert proc["saques_puerta"] == fs.PROC_FLASHSCORE
    assert proc["errores_remate"] == fs.PROC_FLASHSCORE
    # pases_ratio no viene publicado: se deriva y se etiqueta como tal
    assert proc["pases_ratio"] == fs.PROC_DERIVADO, proc["pases_ratio"]
    assert abs(valores["pases_ratio"] - 260 / (90 * 0.54)) < 1e-12
    # Toda clave tiene procedencia (ninguna se queda sin declarar)
    for clave, valor in valores.items():
        assert clave in proc, f"{clave} sin procedencia"
        if valor is None:
            assert proc[clave] == fs.PROC_SIN_DATO, (clave, proc[clave])
    print(f"  OK 5. procedencia declarada para las {len(valores)} claves")


# ─── 6. Emparejamiento por fecha + marcador + nombres ──────────────────────

def prueba_emparejamiento() -> None:
    candidatos = [
        {"id": "21mxSPJb", "fecha": "2026-10-14", "local": "Kashiwa (Jpn)",
         "visitante": "Port MTI FC (Tha)", "goles_local": 5, "goles_visitante": 1},
        {"id": "4Srj0NUb", "fecha": "2026-10-14", "local": "Ararat 2",
         "visitante": "Lernayin Artsakh", "goles_local": 5, "goles_visitante": 1},
        {"id": "otro", "fecha": "2026-10-14", "local": "Equipo X",
         "visitante": "Equipo Y", "goles_local": 2, "goles_visitante": 0},
    ]
    # Caso ambiguo (dos 5-1 el mismo dia) resuelto por nombres, con tolerancia a
    # sufijos de pais y abreviaturas
    r = fs.emparejar_partido({"fecha": "2026-10-14", "goles_local": 5,
                              "goles_visitante": 1, "local": "Kashiwa Reysol",
                              "visitante": "Port FC"}, candidatos)
    assert r and r["candidato"]["id"] == "21mxSPJb", r
    # El otro 5-1 tambien se distingue
    r2 = fs.emparejar_partido({"fecha": "2026-10-14", "goles_local": 5,
                               "goles_visitante": 1, "local": "Ararat",
                               "visitante": "Lernayin Artsakh"}, candidatos)
    assert r2 and r2["candidato"]["id"] == "4Srj0NUb", r2
    # Marcador inequivoco, sin nombres
    r3 = fs.emparejar_partido({"fecha": "2026-10-14", "goles_local": 2,
                               "goles_visitante": 0}, candidatos)
    assert r3 and r3["candidato"]["id"] == "otro", r3
    # Fecha dentro de la tolerancia de 1 dia
    r4 = fs.emparejar_partido({"fecha": "2026-10-15", "goles_local": 2,
                               "goles_visitante": 0}, candidatos)
    assert r4 and r4["candidato"]["id"] == "otro", r4
    # Fuera de tolerancia -> None
    assert fs.emparejar_partido({"fecha": "2026-10-20", "goles_local": 2,
                                 "goles_visitante": 0}, candidatos) is None
    # Sin marcador -> None (nunca se empareja solo por fecha)
    assert fs.emparejar_partido({"fecha": "2026-10-14", "goles_local": None,
                                 "goles_visitante": None}, candidatos) is None
    # Ambiguedad irresoluble -> None en vez de elegir a dedo
    r5 = fs.emparejar_partido({"fecha": "2026-10-14", "goles_local": 5,
                               "goles_visitante": 1}, candidatos)
    assert r5 is None, f"no debe elegir a dedo: {r5}"
    # Un 1-0 invertido (la fuente cambio los bandos) se detecta
    inv = [{"id": "z", "fecha": "2026-10-14", "local": "B", "visitante": "A",
            "goles_local": 0, "goles_visitante": 1}]
    r6 = fs.emparejar_partido({"fecha": "2026-10-14", "goles_local": 1,
                               "goles_visitante": 0}, inv)
    assert r6 and r6["bandos_invertidos"] is True, r6
    print("  OK 6. emparejamiento por fecha+marcador, con nombres como desempate")


# ─── 7. Parser de resultados del feed ──────────────────────────────────────

def prueba_parser_resultados() -> None:
    """Usa el parser real sobre una respuesta simulada, sin red."""
    class ClienteFalso(fs.ClienteFlashscore):
        def _get(self, url, binario=False):
            class R:
                status_code = 200
                content = b"x" * 100
                text = resultados_de_ejemplo()
            return R()

    c = ClienteFalso()
    partidos = c.partidos_terminados(dia=-1)
    # Solo los terminados (AB=3): 3 de 5
    assert len(partidos) == 3, [p["id"] for p in partidos]
    ids = {p["id"] for p in partidos}
    assert ids == {"21mxSPJb", "4Srj0NUb", "bTtcKF3S"}, ids
    kashiwa = next(p for p in partidos if p["id"] == "21mxSPJb")
    assert kashiwa["local"] == "Kashiwa (Jpn)"
    assert kashiwa["goles_local"] == 5 and kashiwa["goles_visitante"] == 1
    assert kashiwa["fecha"] == "2026-10-14"
    # Un partido sin marcador queda en None, no en 0
    hajer = next(p for p in partidos if p["id"] == "bTtcKF3S")
    assert hajer["goles_local"] is None and hajer["goles_visitante"] is None
    print(f"  OK 7. parser de resultados: {len(partidos)} terminados, "
          f"sin marcador -> None")


# ─── 8. Metricas por bando de un partido ───────────────────────────────────

def prueba_metricas_partido() -> None:
    class ClienteFalso(fs.ClienteFlashscore):
        def _get(self, url, binario=False):
            class R:
                status_code = 200
                content = b"x" * 100
                text = feed_de_ejemplo()
            return R()

        def estadisticas(self, id_flashscore):
            return fs.parsear_feed(feed_de_ejemplo())

    m = ClienteFalso().metricas_partido("21mxSPJb")
    assert m.local["tiros_libres"] == 6.0 and m.visitante["tiros_libres"] == 5.0
    assert m.local["saques_puerta"] == 1.0 and m.visitante["saques_puerta"] == 4.0
    assert m.local["pases_total"] == 260.0 and m.visitante["pases_total"] == 205.0
    assert m.local["posesion_pct"] == 54.0
    assert m.local["xg"] == 0.15
    # La etiqueta sin mapeo se ignora sin romper
    assert "Etiqueta desconocida sin mapeo" not in m.etiquetas_crudas.values()
    assert not m.vacia
    # Un partido sin feed devuelve objeto vacio, no excepcion
    class SinDatos(fs.ClienteFlashscore):
        def _get(self, url, binario=False):
            class R:
                status_code = 200
                content = b""
                text = ""
            return R()

        def estadisticas(self, id_flashscore):
            return {}
    assert SinDatos().metricas_partido("x").vacia
    print(f"  OK 8. metricas por bando: {len(m.local)} claves leidas del feed")


def feed_con_periodos() -> str:
    """
    Payload REAL con la estructura de periodos que publica Flashscore: el campo `SE`
    marca el inicio de cada sección (Partido / 1er Tiempo / 2º Tiempo) y las
    etiquetas `SG` siguientes pertenecen a ese periodo.
    """
    def seccion(nombre: str, xg, saques, corners) -> str:
        bloques = [f"SE÷{nombre}", "SF÷Estadísticas principales",
                   f"SD÷432¬SG÷Goles esperados (xG)¬SH÷{xg[0]}¬SI÷{xg[1]}",
                   f"SD÷20¬SG÷Saques de puerta¬SH÷{saques[0]}¬SI÷{saques[1]}",
                   f"SD÷16¬SG÷Córneres¬SH÷{corners[0]}¬SI÷{corners[1]}"]
        return "~".join(bloques)
    return "~".join([
        seccion("Partido", ("1.43", "0.60"), ("9", "9"), ("9", "3")),
        seccion("1er Tiempo", ("0.72", "0.07"), ("3", "7"), ("4", "1")),
        seccion("2º Tiempo", ("0.71", "0.53"), ("6", "2"), ("5", "2")),
    ]) + "~"


def prueba_parseo_por_periodos() -> None:
    """
    9. El feed publica el partido Y sus DOS MITADES. El parser antiguo leía solo el
       primer bloque y descartaba 1T/2T: por eso esas métricas salían nulas y se creía
       una limitación de la fuente cuando era un fallo del parser.
    """
    por_per = fs.parsear_feed_por_periodos(feed_con_periodos())
    assert set(por_per) == {"FT", "1T", "2T"}, list(por_per)
    assert len(por_per["FT"]) == 3, por_per["FT"]
    assert len(por_per["1T"]) == 3, por_per["1T"]
    assert len(por_per["2T"]) == 3, por_per["2T"]
    # Cada periodo tiene SUS valores, no los del total
    assert por_per["FT"]["Saques de puerta"] == ("9", "9")
    assert por_per["1T"]["Saques de puerta"] == ("3", "7")
    assert por_per["2T"]["Saques de puerta"] == ("6", "2")
    assert por_per["1T"]["Goles esperados (xG)"] == ("0.72", "0.07")
    # Un payload sin `SE` cae entero a FT (feed diario)
    sin_se = "SD÷432¬SG÷Goles esperados (xG)¬SH÷1.0¬SI÷0.5¬~"
    solo_ft = fs.parsear_feed_por_periodos(sin_se)
    assert list(solo_ft["FT"]) == ["Goles esperados (xG)"], solo_ft
    assert not solo_ft["1T"] and not solo_ft["2T"]
    print("  OK 9. el feed separa Partido / 1er Tiempo / 2º Tiempo por el campo SE")


def prueba_aritmetica_de_periodos() -> None:
    """
    10. Los valores del feed CUADRAN: 1T + 2T = FT. Es la prueba de que el desglose
        es real y no un reparto inventado.
    """
    class ClienteFalso(fs.ClienteFlashscore):
        def _get(self, url, binario=False):
            class R:
                status_code = 200
                content = b"x" * 100
                text = feed_con_periodos()
            return R()

    m = ClienteFalso().metricas_partido("test")
    assert m.periodos_disponibles == ["FT", "1T", "2T"], m.periodos_disponibles
    for clave in ("xg", "saques_puerta", "corners"):
        ft = m.local.get(clave)
        a = ((m.por_periodo.get("1T") or {}).get("local") or {}).get(clave)
        b = ((m.por_periodo.get("2T") or {}).get("local") or {}).get(clave)
        assert None not in (ft, a, b), (clave, ft, a, b)
        assert abs((a + b) - ft) < 1e-9, f"{clave}: {a}+{b} != {ft}"
    # El total sigue disponible en .local para compatibilidad
    assert m.local["saques_puerta"] == 9.0
    print("  OK 10. aritmética verificada: 1T + 2T = FT en los tres periodos")


def prueba_gatekeeper_catalogo_y_desglose_compuestos() -> None:
    """
    11. Gatekeeper: verifica el desglose de métricas compuestas (totales, completados
        y %) en 1T, 2T y FT, las ecuaciones derivadas (grandes ocasiones aprovechadas)
        y el respaldo GraphQL dtsse/dts.
    """
    pct, comp, tot = fs._desglosar_compuesto("88% (260/296)")
    assert (pct, comp, tot) == (88.0, 260.0, 296.0), (pct, comp, tot)
    pct2, comp2, tot2 = fs._desglosar_compuesto("203 (74%)")
    assert pct2 == 74.0 and comp2 == 203.0 and tot2 == 274.0, (pct2, comp2, tot2)

    def seccion_completa(nombre: str, mult: int) -> str:
        p_comp = 130 * mult
        p_tot = 150 * mult
        bloques = [
            f"SE÷{nombre}",
            f"SD÷1¬SG÷Pases¬SH÷87% ({p_comp}/{p_tot})¬SI÷80% ({100*mult}/{125*mult})",
            f"SD÷2¬SG÷Pases largos¬SH÷50% ({10*mult}/{20*mult})¬SI÷60% ({12*mult}/{20*mult})",
            f"SD÷3¬SG÷Pases en el tercio final¬SH÷75% ({30*mult}/{40*mult})¬SI÷70% ({21*mult}/{30*mult})",
            f"SD÷4¬SG÷Centros¬SH÷25% ({2*mult}/{8*mult})¬SI÷30% ({3*mult}/{10*mult})",
            f"SD÷5¬SG÷Entradas¬SH÷60% ({6*mult}/{10*mult})¬SI÷50% ({5*mult}/{10*mult})",
            f"SD÷6¬SG÷Grandes ocasiones¬SH÷{2*mult}¬SI÷{1*mult}",
            f"SD÷7¬SG÷Grandes ocasiones falladas¬SH÷{1*mult}¬SI÷{1*mult}",
        ]
        return "~".join(bloques)

    payload = "~".join([
        seccion_completa("Partido", 2),
        seccion_completa("1er Tiempo", 1),
        seccion_completa("2º Tiempo", 1),
    ]) + "~"

    class ClienteCompuesto(fs.ClienteFlashscore):
        def _get(self, url, binario=False):
            class R:
                status_code = 200
                content = payload.encode("utf-8")
                text = payload
            return R()

    m = ClienteCompuesto().metricas_partido("comp")
    for per, mult in (("FT", 2), ("1T", 1), ("2T", 1)):
        loc = m.por_periodo[per]["local"]
        assert loc["pases_completados"] == 130.0 * mult
        assert loc["pases_totales"] == 150.0 * mult
        assert loc["pases_pct"] == 87.0
        assert loc["pases_largos_completados"] == 10.0 * mult
        assert loc["pases_largos"] == 20.0 * mult
        assert loc["pases_largos_pct"] == 50.0
        assert loc["pases_tercio_final_completados"] == 30.0 * mult
        assert loc["pases_tercio_final"] == 40.0 * mult
        assert loc["pases_tercio_final_pct"] == 75.0
        assert loc["centros_completados"] == 2.0 * mult
        assert loc["centros"] == 8.0 * mult
        assert loc["centros_pct"] == 25.0
        assert loc["entradas_ganadas"] == 6.0 * mult
        assert loc["entradas"] == 10.0 * mult
        assert loc["entradas_ganadas_pct"] == 60.0
        assert loc["grandes_ocasiones_aprovechadas"] == 1.0 * mult
        assert loc["grandes_ocasiones_perdidas"] == 1.0 * mult
    print("  OK 11. gatekeeper desglose compuesto (totales, completados, %) en 1T, 2T y FT")


def prueba_extraer_cuotas() -> None:
    html_cuotas = """
    <table id="odds_1x2" class="odds">
      <thead><tr><th>Casa</th><th>1</th><th>X</th><th>2</th></tr></thead>
      <tbody>
        <tr>
          <td class="bookmaker">Bet365</td>
          <td class="kx"><span class="odds-wrap">1.85</span></td>
          <td class="kx"><span class="odds-wrap">3.50</span></td>
          <td class="kx"><span class="odds-wrap">4.20</span></td>
        </tr>
        <tr>
          <td class="bookmaker">Bwin</td>
          <td>1.80</td><td>3.60</td><td>4.33</td>
        </tr>
      </tbody>
    </table>
    """
    urls_capturadas: list[tuple[str, str]] = []

    class ClienteCuotas(fs.ClienteFlashscore):
        def _get(self, url, binario=False):
            urls_capturadas.append((url, self._cabeceras().get("x-fsign", "")))
            class R:
                status_code = 200
                text = html_cuotas
            return R()

    c = ClienteCuotas()
    cuotas = c.extraer_cuotas("2mAGw9Ti")
    assert urls_capturadas == [("https://d.flashscore.com/x/feed/d_od_2mAGw9Ti_es_1_eu", "SW9D1eZo")]
    assert cuotas == {"local": 1.85, "empate": 3.50, "visitante": 4.20}
    print("  OK 12. extraer_cuotas con self._get (x-fsign) y primera fila HTML (local, empate, visitante)")


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("\nPRUEBAS DEL ADAPTADOR DE FLASHSCORE (datos simulados, sin red)\n" + "-" * 70)
    pruebas = [
        prueba_parseo_feed,
        prueba_conversion_valores,
        prueba_pases_ratio_derivado,
        prueba_relleno_no_sobrescribe,
        prueba_procedencia,
        prueba_emparejamiento,
        prueba_parser_resultados,
        prueba_metricas_partido,
        prueba_parseo_por_periodos,
        prueba_aritmetica_de_periodos,
        prueba_gatekeeper_catalogo_y_desglose_compuestos,
        prueba_extraer_cuotas,
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
