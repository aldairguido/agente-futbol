import math
import pytest


def _matriz_poisson(lam_l: float, lam_v: float, n: int = 10) -> list[list[float]]:
    return [
        [
            (math.exp(-lam_l) * (lam_l ** i) / math.factorial(i))
            * (math.exp(-lam_v) * (lam_v ** j) / math.factorial(j))
            for j in range(n)
        ]
        for i in range(n)
    ]


def test_r24_b1_jugada_optima_exige_70_y_pertenencia_sec1_2_y_sin_te_apuesto_en_md():
    from motor_mercados import clusters_marcador, analizar
    from test_motor_mercados import doc_sintetico

    # 1) Partido abierto donde ninguna candidata de clusters llega a 70%: "Sin jugada óptima ≥70%"
    mat_abierto = _matriz_poisson(2.25, 2.12)
    cl_abierto = clusters_marcador(mat_abierto, "Orlando City", "FC Cincinnati")
    assert cl_abierto["jugada_optima"] == "Sin jugada óptima ≥70%"
    assert "ℹ️ Sin jugada óptima ≥70%" in cl_abierto["linea_pie"]
    assert "TE APUESTO" not in cl_abierto["linea_pie"]

    # 2) Candidata >= 70% en matriz (p. ej. favorito claro o DC >= 70%), pero ausente de Sección 1 y 2
    mat_fav = _matriz_poisson(2.40, 0.55)
    cl_sin_sec = clusters_marcador(
        mat_fav,
        "Local FC",
        "Visita FC",
        selecciones_sec1_2=[{"evento": "corners_over_7.5", "periodo": "FT", "probabilidad": 0.80}],
    )
    assert cl_sin_sec["jugada_optima"] == "Sin jugada óptima ≥70%"
    assert "ℹ️ Sin jugada óptima ≥70%" in cl_sin_sec["linea_pie"]

    # 3) Misma matriz con la selección presente en Sección 1 o 2: sí emite jugada óptima sin "TE APUESTO"
    cl_con_sec = clusters_marcador(
        mat_fav,
        "Local FC",
        "Visita FC",
        selecciones_sec1_2=[{"evento": "1", "periodo": "FT", "probabilidad": 0.78}],
    )
    assert "1x2 : Local FC" in cl_con_sec["jugada_optima"]
    assert "🎯 JUGADA ÓPTIMA:" in cl_con_sec["linea_pie"]
    assert "TE APUESTO" not in cl_con_sec["linea_pie"]

    # 4) Informe .md generado por analizar() no contiene "TE APUESTO"
    doc_l = doc_sintetico("Local FC", [("casa", 2, 0), ("fuera", 2, 1)] * 8, xg=[(1.9, 0.7)] * 16)
    doc_v = doc_sintetico("Visita FC", [("fuera", 0, 2), ("casa", 1, 2)] * 8, xg=[(0.8, 1.8)] * 16)
    an = analizar(doc_l, doc_v, "Local FC", "Visita FC")
    assert "TE APUESTO" not in an.informe.upper()


def test_r24_b2_stake_ticket_valor_sin_cuotas_rotulado_hipotetico(tmp_path):
    from types import SimpleNamespace
    import informe as inf

    mat = _matriz_poisson(2.10, 0.85)
    an_stub = SimpleNamespace(
        metadatos={"equipo_local": "Local FC", "equipo_visitante": "Visita FC", "competicion": "Liga"},
        parametros=SimpleNamespace(umbral=0.70),
        motor=SimpleNamespace(
            matriz_ft=mat,
            tasas=SimpleNamespace(lambda_local_ft=2.10, lambda_visitante_ft=0.85),
            recursivo={"aplicado": False},
        ),
        mercados=[],
        selecciones_filtradas=[],
        grupos={"mantiene": [], "sensible": []},
    )

    filas = inf.construir_filas_ticket_valor(an_stub, cuotas_flashscore={})
    filas_con_stake = [f for f in filas if "Stake" in f["recomendacion"]]
    assert filas_con_stake
    for f in filas_con_stake:
        assert "hipotético a cuota mínima, sin validar" in f["recomendacion"]

    filas_leg = inf._construir_filas_ticket_valor_legacy_5e8(an_stub, cuotas_flashscore={})
    for f in filas_leg:
        if "Stake" in f["recomendacion"]:
            assert "hipotético a cuota mínima, sin validar" in f["recomendacion"]

    txt_3p = inf.redactar_analisis_3_parrafos(an_stub, cuotas_flashscore={})
    assert "hipotético a cuota mínima, sin validar" in txt_3p

    png_out = tmp_path / "ticket_valor_sin_cuotas.png"
    ruta_png = inf.generar_ticket_valor_png(an_stub, cuotas_flashscore={}, destino=str(png_out))
    assert png_out.exists() and png_out.stat().st_size > 5000


def test_r24_b3_h2h_promedios_crudos_conteos_y_parrafo2_datos_crudos():
    from types import SimpleNamespace
    import cuadro_h2h_2024_2026 as ch2h
    import informe as inf

    # Caso Orlando City vs FC Cincinnati: 2 duelos crudos (1-1 y 1-0 para Cincinnati en casa -> para Orlando: 0V-1E-1D,
    # goles crudos Orlando 0.50, Cincinnati 1.00, total crudo 1.50 < 2.5, +2.5: 0/2, BTTS: 1/2),
    # aunque el λ del módulo H2H tras red recursiva fuese alto (p. ej. 1.71 - 1.70 = 3.41).
    duelos = [
        {"fecha": "2025-07-10", "torneo": "MLS", "local": "Orlando City", "visitante": "FC Cincinnati", "goles_local": 1, "goles_visitante": 1},
        {"fecha": "2024-09-01", "torneo": "MLS", "local": "FC Cincinnati", "visitante": "Orlando City", "goles_local": 1, "goles_visitante": 0},
    ]
    crudo = ch2h.calcular_resumen_crudo_h2h(duelos, "Orlando City", "FC Cincinnati")
    assert crudo["n"] == 2
    assert crudo["etiqueta"] == "exploratorio, n=2"
    assert abs(crudo["prom_local"] - 0.50) < 1e-6
    assert abs(crudo["prom_visita"] - 1.00) < 1e-6
    assert abs(crudo["prom_total"] - 1.50) < 1e-6
    assert crudo["ved_str"] == "0/1/1"
    assert crudo["over_25"] == 0
    assert crudo["btts"] == 1
    assert "exploratorio, n=2" in crudo["texto_crudo"]

    # El párrafo 2 de Telegram debe basarse en los datos crudos (1.50 < 2.50 -> contradice el Over 2.5 general),
    # NO en la suma de lambdas del módulo (1.71 + 1.70 = 3.41 > 2.50), y mostrar ambos junto con V/E/D, +2.5 y BTTS.
    mat = _matriz_poisson(2.25, 2.12)
    an_stub = SimpleNamespace(
        metadatos={"equipo_local": "Orlando City", "equipo_visitante": "FC Cincinnati", "competicion": "MLS"},
        parametros=SimpleNamespace(umbral=0.70),
        motor=SimpleNamespace(
            matriz_ft=mat,
            tasas=SimpleNamespace(lambda_local_ft=2.25, lambda_visitante_ft=2.12),
            recursivo={"aplicado": True},
        ),
        mercados=[],
        selecciones_filtradas=[],
        grupos={"mantiene": [], "sensible": []},
    )
    info_h2h = {
        "n_duelos": 2,
        "duelos": duelos,
        "lambda_a_h2h": 1.71,
        "lambda_b_h2h": 1.70,
    }
    txt_3p = inf.redactar_analisis_3_parrafos(an_stub, info_h2h=info_h2h, cuotas_flashscore={})
    parrafos = [p.strip() for p in txt_3p.split("\n\n") if "2. Contraste con el Modelo H2H" in p]
    assert len(parrafos) == 1
    p2 = parrafos[0]
    assert "exploratorio, n=2" in p2
    assert "λ módulo H2H: 1.71–1.70" in p2
    assert "0.50 – 1.00" in p2 and "1.50" in p2
    assert "V/E/D: 0/1/1" in p2
    assert "+2.5: 0/2" in p2
    assert "BTTS: 1/2" in p2
    assert "CONTRADICE el ritmo goleador del modelo general" in p2



def test_r24_b4_snapshot_signo_desvio_sec2_y_ronda_etapa_formato():
    from types import SimpleNamespace
    import fuentes_xg as fxg
    import evaluacion_pronosticos as ep
    import motor_mercados as mm

    # 1. Fixture de FotMob con ronda, etapa y formato -> se normalizan, y cuando faltan quedan en None
    fut_con = fxg._normalizar_futuros([{
        "id": "999001",
        "status": {"finished": False, "utcTime": "2026-11-21T20:00:00Z"},
        "home": {"id": "101", "name": "Orlando City"},
        "away": {"id": "102", "name": "FC Cincinnati"},
        "tournament": {"name": "MLS Cup Playoffs", "stage": "Knockout", "format": "Eliminación directa"},
        "round": "Semifinal de Conferencia",
    }])
    assert len(fut_con) == 1
    assert fut_con[0]["ronda"] == "Semifinal de Conferencia"
    assert fut_con[0]["etapa"] == "Knockout"
    assert fut_con[0]["formato"] == "Eliminación directa"

    fut_sin = fxg._normalizar_futuros([{
        "id": "999002",
        "status": {"finished": False, "utcTime": "2026-11-21T22:00:00Z"},
        "home": {"id": "103", "name": "Vitória"},
        "away": {"id": "104", "name": "RB Bragantino"},
        "tournament": {"name": "Brasileirão Série A"},
    }])
    assert len(fut_sin) == 1
    assert fut_sin[0]["ronda"] is None
    assert fut_sin[0]["etapa"] is None
    assert fut_sin[0]["formato"] is None

    # 2. Snapshot guarda signo_desvio en líneas de Sección 2 (sensible) y None en mantiene,
    #    además de ronda/etapa/formato en partido (o None si no vienen).
    sel_sec1 = mm.Seleccion(
        mercado="Dobles oportunidades (1X2)",
        id_mercado="doble_oportunidad",
        evento="1X",
        etiqueta="Local o empate (1X)",
        periodo="FT",
        equipo="ambos",
        linea=None,
        probabilidad=0.81,
        estimable=True,
        estado="mantiene",
        escenarios={"c_low": 0.78},
    )
    sel_sec2_neg = mm.Seleccion(
        mercado="Total de goles (Más/Menos)",
        id_mercado="goles_ft",
        evento="mas_de",
        etiqueta="Más de 1.5 goles",
        periodo="FT",
        equipo="ambos",
        linea=1.5,
        probabilidad=0.88,
        estimable=True,
        estado="sensible",
        escenarios={"c_low": 0.65},
    )
    setattr(sel_sec2_neg, "signo_desvio", "-")
    setattr(sel_sec2_neg, "desvio_observado_pp", -14.2)

    sel_sec2_pos = mm.Seleccion(
        mercado="Ambos equipos anotan (BTTS)",
        id_mercado="ambos_anotan",
        evento="si",
        etiqueta="Ambos anotan: Sí",
        periodo="FT",
        equipo="ambos",
        linea=None,
        probabilidad=0.72,
        estimable=True,
        estado="sensible",
        escenarios={"c_low": 0.71},
    )
    setattr(sel_sec2_pos, "signo_desvio", "+")
    setattr(sel_sec2_pos, "desvio_observado_pp", 12.5)

    muestra_dummy = SimpleNamespace(partidos_general=15, partidos_condicion=7)
    motor_stub = SimpleNamespace(
        muestras={
            "local_general": muestra_dummy,
            "visitante_general": muestra_dummy,
            "local_cond": muestra_dummy,
            "visitante_cond": muestra_dummy,
        },
        tasas=SimpleNamespace(lambda_local_ft=1.80, lambda_visitante_ft=1.35),
        recursivo={},
        doc_local=None,
        doc_visitante=None,
        nombre_local="Orlando City",
        nombre_visitante="FC Cincinnati",
    )
    an_con = SimpleNamespace(
        metadatos={
            "equipo_local": "Orlando City",
            "equipo_visitante": "FC Cincinnati",
            "fecha_partido": "2026-11-21",
            "hora_inicio_partido": "2026-11-21T20:00:00Z",
            "fixture_confirmado": True,
            "fuente_fecha": "fixture",
            "competicion": "MLS Cup Playoffs",
            "ronda": "Semifinal de Conferencia",
            "etapa": "Knockout",
            "formato": "Eliminación directa",
            "generado_en": "2026-11-20T12:00:00Z",
        },
        motor=motor_stub,
        parametros=SimpleNamespace(umbral=0.70, w_condicion=0.5, w_xg=0.6, distribucion_conteos="poisson"),
        mercados=[SimpleNamespace(selecciones=[sel_sec1, sel_sec2_neg, sel_sec2_pos])],
    )
    prep_con = {"id_trabajo": "test_b4_con", "analisis": an_con, "rutas": {}}
    snap_con = ep.snapshot_desde_analisis(prep_con, publicado_en="2026-11-20T12:01:00Z").a_dict()
    assert snap_con["partido"]["ronda"] == "Semifinal de Conferencia"
    assert snap_con["partido"]["etapa"] == "Knockout"
    assert snap_con["partido"]["formato"] == "Eliminación directa"

    por_id = {s["id_mercado"]: s for s in snap_con["selecciones_publicadas"]}
    assert por_id["doble_oportunidad"]["signo_desvio"] is None
    assert por_id["goles_ft"]["signo_desvio"] == "-"
    assert por_id["goles_ft"]["desvio_observado_pp"] == -14.2
    assert por_id["ambos_anotan"]["signo_desvio"] == "+"
    assert por_id["ambos_anotan"]["desvio_observado_pp"] == 12.5

    # Sin ronda/etapa/formato en metadatos -> deben existir con valor None
    an_sin = SimpleNamespace(
        metadatos={
            "equipo_local": "Vitória",
            "equipo_visitante": "RB Bragantino",
            "fecha_partido": "2026-11-21",
            "generado_en": "2026-11-20T12:00:00Z",
        },
        motor=motor_stub,
        parametros=SimpleNamespace(umbral=0.70, w_condicion=0.5, w_xg=0.6, distribucion_conteos="poisson"),
        mercados=[SimpleNamespace(selecciones=[sel_sec1])],
    )
    snap_sin = ep.snapshot_desde_analisis({"id_trabajo": "test_b4_sin", "analisis": an_sin, "rutas": {}}, publicado_en="2026-11-20T12:01:00Z").a_dict()
    assert "ronda" in snap_sin["partido"] and snap_sin["partido"]["ronda"] is None
    assert "etapa" in snap_sin["partido"] and snap_sin["partido"]["etapa"] is None
    assert "formato" in snap_sin["partido"] and snap_sin["partido"]["formato"] is None



def test_r24_b5_inputs_gz_sha256_y_reproduccion_ab(tmp_path):
    from pathlib import Path
    import json
    import evaluacion_pronosticos as ep
    import motor_mercados as mm
    from decision_cobertura import banderas_comportamiento

    raiz = Path(r"D:\agente_futbol")
    ln_docs = json.loads((raiz / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    dl = ln_docs["londrina"]
    dv = ln_docs["nautico"]

    with banderas_comportamiento(activar_todas=False):
        an = mm.analizar(
            dl, dv, "Londrina", "Náutico",
            condicion_local="casa", condicion_visitante="fuera",
            competicion="Série C",
        )
    an.metadatos["fecha_partido"] = "2026-11-12"
    an.metadatos["generado_en"] = "2026-11-12T10:00:00Z"
    an.metadatos["competicion"] = "Série C"
    an.metadatos["ronda"] = "Jornada 19"
    an.metadatos["etapa"] = "Fase Regular"
    an.metadatos["formato"] = "Liga"

    prep = {
        "id_trabajo": "r24_b5_ln_repro",
        "analisis": an,
        "contexto": {
            "doc_local": dl,
            "doc_visitante": dv,
            "equipo_local": "Londrina",
            "equipo_visitante": "Náutico",
            "fecha_partido": "2026-11-12",
            "competicion": "Série C",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
        },
        "rutas": {},
    }
    ep.adjuntar_modo_sombra_preparado(prep)
    res_inp = ep.guardar_inputs_reproducibles(prep, directorio=str(tmp_path))
    assert res_inp["ok"] is True
    ruta_gz = Path(res_inp["ruta"])
    assert ruta_gz.name == "inputs_r24_b5_ln_repro.json.gz"
    assert ruta_gz.exists() and ruta_gz.stat().st_size > 1000
    assert res_inp["inputs_sha256"] and len(res_inp["inputs_sha256"]) == 64

    # Verificar contenido de inputs_<id_trabajo>.json.gz
    doc_inp = ep.cargar_inputs_reproducibles(str(ruta_gz))
    assert doc_inp["esquema"] == "inputs-reproducibles-v1"
    assert doc_inp["semillas"]["semilla_mc"] == 42
    assert doc_inp["semillas"]["semilla_bootstrap"] == 20261114
    assert doc_inp["perfiles"]["local"] is not None
    assert doc_inp["perfiles"]["visitante"] is not None
    assert len(doc_inp["clubes_15p"]["local"]["partidos"]) == 15
    assert len(doc_inp["clubes_15p"]["visitante"]["partidos"]) == 15
    assert doc_inp["lambdas_base_doble_adn"]["local_ft"] is not None
    assert doc_inp["traza_dcn"]["aplicado"] is True
    assert len(doc_inp["traza_dcn"]["traza_lambda"]) > 0

    # Snapshot incluye inputs_sha256 como campo opcional compatible
    snap = ep.snapshot_desde_analisis(prep, publicado_en="2026-11-12T10:05:00Z").a_dict()
    assert snap.get("inputs_sha256") == res_inp["inputs_sha256"]

    # Prueba de reproducción: recalculando A+B (y soloA) desde inputs_<id>.json.gz coincide con el snapshot
    repro = ep.reproducir_ab_desde_inputs(str(ruta_gz))
    sombra_snap = snap["modo_sombra"]
    sombra_repro = repro["modo_sombra"]
    assert sombra_repro["lambdas_base"] == sombra_snap["lambdas_base"]
    assert sombra_repro["lambdas_sombra"] == sombra_snap["lambdas_sombra"]
    assert sombra_repro["n_selecciones_comparables"] == sombra_snap["n_selecciones_comparables"]

    comp_snap = {(c["evento"], c["periodo"], c["equipo"], c["linea"]): (c["prob_base"], c["prob_sombra"]) for c in sombra_snap["comparaciones"]}
    comp_repro = {(c["evento"], c["periodo"], c["equipo"], c["linea"]): (c["prob_base"], c["prob_sombra"]) for c in sombra_repro["comparaciones"]}
    assert comp_repro == comp_snap

    # Si falla la escritura de inputs_<id_trabajo>.json.gz, NO bloquea el pronóstico
    res_fail = ep.guardar_inputs_reproducibles({"id_trabajo": ""}, directorio=str(tmp_path))
    assert res_fail["ok"] is False
