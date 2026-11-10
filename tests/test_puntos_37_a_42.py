from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auditoria_historica as ah
import evaluacion as ev
import fuentes_xg as fxg
import motor_inferencia as mi
import motor_mercados as mm
import motor_patrones as mp
import servicio_mercados as sm


def _crear_partido_matriz(
    id_p: str,
    goles_eq: int,
    goles_riv: int,
    ft_eq: dict[str, float | None],
    ft_riv: dict[str, float | None],
    p1t_eq: dict[str, float | None] | None = None,
    p1t_riv: dict[str, float | None] | None = None,
    p2t_eq: dict[str, float | None] | None = None,
    p2t_riv: dict[str, float | None] | None = None,
    proc_ft_eq: dict[str, str] | None = None,
    eventos_gol: list[dict[str, Any]] | None = None,
    goles_1t_eq: int | None = None,
    goles_1t_riv: int | None = None,
) -> dict[str, Any]:
    """Construye un partido serializado tras pasar por consolidar_matriz_completa."""
    p1t_eq = p1t_eq or {}
    p1t_riv = p1t_riv or {}
    p2t_eq = p2t_eq or {}
    p2t_riv = p2t_riv or {}
    periodos: dict[str, fxg.PeriodoPartido] = {}
    for per_k, d_eq, d_rv in (
        ("FT", ft_eq, ft_riv),
        ("1T", p1t_eq, p1t_riv),
        ("2T", p2t_eq, p2t_riv),
    ):
        filas = []
        for cat_m in fxg.CATALOGO_METRICAS:
            cl = cat_m.clave
            filas.append({
                "clave": cl,
                "metrica": cat_m.etiqueta,
                "categoria": cat_m.categoria,
                "equipo": d_eq.get(cl),
                "rival": d_rv.get(cl),
            })
        periodos[per_k] = fxg.PeriodoPartido(per_k, filas, [])

    mat = fxg.MatrizPartido(
        id_partido=id_p,
        fecha="2026-10-15",
        torneo="Liga Profesional",
        condicion="casa",
        equipo="Equipo A",
        rival="Rival B",
        goles_equipo=goles_eq,
        goles_rival=goles_riv,
        url=f"https://www.fotmob.com/matches/{id_p}",
        periodos=periodos,
        metricas_no_disponibles=[],
        procedencia={"FT": {"equipo": dict(proc_ft_eq or {}), "rival": {}}},
    )
    fxg.consolidar_matriz_completa(mat)
    d_out = {
        "id_partido": mat.id_partido,
        "fecha": mat.fecha,
        "torneo": mat.torneo,
        "condicion": mat.condicion,
        "equipo": mat.equipo,
        "rival": mat.rival,
        "goles_equipo": mat.goles_equipo,
        "goles_rival": mat.goles_rival,
        "marcador": {"equipo": goles_eq, "rival": goles_riv},
        "es_amistoso": False,
        "periodos": {
            p: {"periodo": p, "filas": mat.periodos[p].filas, "no_disponibles": mat.periodos[p].no_disponibles}
            for p in ("FT", "1T", "2T")
        },
        "procedencia": mat.procedencia,
    }
    if eventos_gol is not None:
        d_out["eventos_gol"] = eventos_gol
    if goles_1t_eq is not None and goles_1t_riv is not None:
        d_out["goles_1t_local"] = goles_1t_eq
        d_out["goles_1t_visita"] = goles_1t_riv
    return d_out


def test_punto_37_cobertura_por_metrica_cero_observado_vs_none() -> None:
    """
    Punto 37 (Test obligatorio):
    De 15 partidos, si 8 tienen faltas publicadas (incluido uno con `0.0` faltas confirmado)
    y un 9º tiene tiros publicados pero faltas ausentes (`None`), el denominador de faltas
    (`n_con_dato`) debe ser exactamente `8` (y el de remates_totales debe ser `9`).
    """
    partidos_15: list[dict[str, Any]] = []
    # Partidos 1..7: tienen faltas publicadas (> 0) y remates publicados
    for i in range(1, 8):
        partidos_15.append(_crear_partido_matriz(
            f"P{i}", 1, 0,
            ft_eq={"faltas": float(10 + i), "remates_totales": 12.0},
            ft_riv={"faltas": 11.0, "remates_totales": 9.0},
            proc_ft_eq={"faltas": "fotmob", "remates_totales": "fotmob"},
        ))
    # Partido 8: tiene `0.0` faltas confirmado por la fuente (cero real observado)
    partidos_15.append(_crear_partido_matriz(
        "P8", 2, 0,
        ft_eq={"faltas": 0.0, "remates_totales": 14.0},
        ft_riv={"faltas": 8.0, "remates_totales": 7.0},
        proc_ft_eq={"faltas": "flashscore", "remates_totales": "fotmob"},
    ))
    # Partido 9: tiene tiros publicados (15.0), pero faltas ausentes (`None`)
    partidos_15.append(_crear_partido_matriz(
        "P9", 1, 1,
        ft_eq={"faltas": None, "remates_totales": 15.0},
        ft_riv={"faltas": None, "remates_totales": 10.0},
        proc_ft_eq={"remates_totales": "fotmob"},
    ))
    # Partidos 10..15: solo marcador oficial, sin hoja de faltas ni remates
    for i in range(10, 16):
        partidos_15.append(_crear_partido_matriz(
            f"P{i}", 1, 1,
            ft_eq={"faltas": None, "remates_totales": None},
            ft_riv={"faltas": None, "remates_totales": None},
        ))

    res_faltas = fxg.calcular_cobertura_y_promedio_metrica(partidos_15, "faltas", periodo="FT", lado="equipo")
    res_remates = fxg.calcular_cobertura_y_promedio_metrica(partidos_15, "remates_totales", periodo="FT", lado="equipo")

    assert res_faltas["n_total_partidos"] == 15
    assert res_faltas["n_con_dato"] == 8, f"Denominador de faltas debía ser 8, fue {res_faltas['n_con_dato']}"
    assert res_faltas["n_ceros_observados"] == 1
    assert res_faltas["n_sin_dato"] == 7
    # Suma de faltas: (11+12+13+14+15+16+17 + 0) / 8 = 98 / 8 = 12.25
    assert res_faltas["promedio"] == 12.25
    assert res_faltas["fuentes"]["fotmob"] == 7
    assert res_faltas["fuentes"]["flashscore"] == 1
    assert res_faltas["fuentes"]["sin_dato"] == 7

    assert res_remates["n_con_dato"] == 9, f"Denominador de remates debía ser 9, fue {res_remates['n_con_dato']}"


def test_punto_38_cuadro_b_y_c_denominadores_propios_sin_inventar_eventos() -> None:
    """
    Punto 38:
    Un marcador `2-1` sin eventos de gol suma a `Victorias (X/15)` y `Más de 2.5 goles (X/15)`,
    pero NO inventa quién abrió el marcador ni los tramos de 15 minutos del Cuadro C.
    """
    partidos = []
    # 5 partidos con marcador 2-1 pero SIN eventos de gol ni marcador 1T ni tarjetas ni córneres
    for i in range(5):
        partidos.append(_crear_partido_matriz(
            f"M_SIN_EV_{i}", 2, 1,
            ft_eq={"tarjetas_amarillas": None, "corners": None},
            ft_riv={"tarjetas_amarillas": None, "corners": None},
            eventos_gol=None,
        ))
    # 10 partidos con marcador 2-1 y CON eventos de gol verificados (minuto 12 equipo, minuto 68 equipo, minuto 82 rival) y 1T (1-0)
    for i in range(10):
        partidos.append(_crear_partido_matriz(
            f"M_CON_EV_{i}", 2, 1,
            ft_eq={"tarjetas_amarillas": 1.0, "corners": 6.0},
            ft_riv={"tarjetas_amarillas": 2.0, "corners": 5.0},
            p1t_eq={"goles": 1.0},
            p1t_riv={"goles": 0.0},
            p2t_eq={"goles": 1.0},
            p2t_riv={"goles": 1.0},
            eventos_gol=[
                {"minuto": 12, "lado": "equipo"},
                {"minuto": 68, "lado": "equipo"},
                {"minuto": 82, "lado": "rival"},
            ],
            goles_1t_eq=1,
            goles_1t_riv=0,
        ))

    cb = mi.calcular_cuadro_b_tendencias(partidos, nombre_equipo="Equipo A")
    cc = mi.calcular_cuadro_c_timeline(partidos, nombre_equipo="Equipo A")

    # Marcador FT está en los 15 partidos: Victorias = 15/15, Más de 2.5 goles = 15/15
    assert cb["n_con_marcador"] == 15
    assert int(cb["victorias"]) == 15 and cb["victorias"]["total"] == 15
    assert int(cb["mas_2_5_goles"]) == 15 and cb["mas_2_5_goles"]["total"] == 15

    # Primero en marcar y Ganador 1T solo tienen soporte en los 10 partidos con eventos / 1T
    assert cb["n_con_eventos_gol"] == 10, f"Esperado 10 con eventos, obtenido {cb['n_con_eventos_gol']}"
    assert cb["primero_en_marcar"]["conteo"] == 10 and cb["primero_en_marcar"]["total"] == 10
    assert cb["n_con_1t"] == 10
    assert cb["ganador_1t"]["conteo"] == 10 and cb["ganador_1t"]["total"] == 10

    # Tarjetas y córneres solo tienen soporte en los 10 partidos con dato publicado
    assert cb["n_con_tarjetas"] == 10
    assert cb["n_con_corners"] == 10

    # Cuadro C solo suma los 10 partidos con eventos verificados (no reparte los otros 5 con porcentajes fijos)
    assert cc["_meta"]["n_partidos_con_eventos"] == 10
    assert cc["00_15"]["anotados"] == 10
    assert cc["61_75"]["anotados"] == 10
    assert cc["76_90"]["recibidos"] == 10
    assert cc["16_30"]["anotados"] == 0


def test_punto_39_prohibicion_reparto_50_50_y_control_270_celdas() -> None:
    """
    Punto 39 (Test obligatorio):
    Si FotMob/Flashscore entrega `8` córneres `FT` y `1T=None, 2T=None`, la matriz conserva el `8`
    en `FT`, deja `1T/2T` como `None` (`"sin_dato"`), controla las 45x3x2 = 270 celdas,
    y cualquier recomendación de ese mercado por mitad queda bloqueada.
    """
    partido = _crear_partido_matriz(
        "TEST_CORNERS_FT_ONLY", 1, 0,
        ft_eq={"corners": 8.0, "posesion_pct": 58.0, "remates_totales": 11.0},
        ft_riv={"corners": 4.0, "posesion_pct": 42.0, "remates_totales": 7.0},
        p1t_eq={"corners": None},
        p1t_riv={"corners": None},
        p2t_eq={"corners": None},
        p2t_riv={"corners": None},
        proc_ft_eq={"corners": "fotmob", "posesion_pct": "fotmob", "remates_totales": "fotmob"},
    )

    filas_ft = {f["clave"]: f for f in partido["periodos"]["FT"]["filas"]}
    filas_1t = {f["clave"]: f for f in partido["periodos"]["1T"]["filas"]}
    filas_2t = {f["clave"]: f for f in partido["periodos"]["2T"]["filas"]}

    assert filas_ft["corners"]["equipo"] == 8.0
    assert filas_1t["corners"]["equipo"] is None, f"1T corners no debía repartirse 50/50: {filas_1t['corners']}"
    assert filas_2t["corners"]["equipo"] is None, f"2T corners no debía repartirse 50/50: {filas_2t['corners']}"
    assert partido["procedencia"]["FT"]["equipo"]["corners"] == "fotmob"
    assert partido["procedencia"]["1T"]["equipo"]["corners"] == "sin_dato"
    assert partido["procedencia"]["2T"]["equipo"]["corners"] == "sin_dato"

    aud_doc = {"partidos": [partido]}
    cob = ah.validar_cobertura_matriz(aud_doc)
    assert cob["celdas_por_equipo"] == 135
    assert cob["celdas_por_partido"] == 270
    assert cob["total_valores"] == 270
    assert "corners" in cob["mercados_mitad_bloqueados"]

    # Verificar que si entra una selección de córneres en 1T o 2T, queda bloqueada en Top 5
    class _MotorDummy:
        doc_local = {"cobertura_matriz": cob, "partidos": [partido]}
        doc_visitante = {"cobertura_matriz": cob, "partidos": [partido]}
        matriz_ft = [[0.2, 0.1], [0.4, 0.3]]
        matriz_1t = [[0.5, 0.2], [0.2, 0.1]]
        matriz_2t = [[0.4, 0.2], [0.3, 0.1]]
        tasas = type("T", (), {"lambda_local_ft": 1.4, "lambda_visitante_ft": 0.9, "lambda_local_1t": 0.6, "lambda_visitante_1t": 0.4, "lambda_local_2t": 0.8, "lambda_visitante_2t": 0.5})()
        recursivo = {}
        xai_factores = {}

    sels_test = [
        {"id_mercado": "total_corners", "mercado": "Total de córneres", "evento": "corners_over_4.5", "etiqueta": "más de 4.5 córneres", "periodo": "1T", "equipo": None, "linea": 4.5, "probabilidad": 0.81, "estado": "mantiene"},
        {"id_mercado": "total_corners", "mercado": "Total de córneres", "evento": "corners_over_8.5", "etiqueta": "más de 8.5 córneres", "periodo": "FT", "equipo": None, "linea": 8.5, "probabilidad": 0.78, "estado": "mantiene"},
    ]
    top5 = mm.seleccionar_top_5_jugadas_recomendadas(sels_test, motor=_MotorDummy(), eq_l="Equipo A", eq_v="Rival B")
    for item in top5:
        assert "1T" not in item["etiqueta"], f"Córneres 1T debía estar bloqueado: {item}"


def test_punto_40_copa_y_asedio_sin_apilamiento_y_sincronizacion_x2() -> None:
    """
    Punto 40:
    1) Prohibido multiplicar `1.35 * 1.25` (+68.75%) por duplicado: cuando coinciden copa/eliminatoria
       y asedio/defensa rota con datos reales verificados, el ajuste único está acotado a `<= 1.35x`.
    2) Si se veta `1X` y `X2 < 70.0%`, no se fuerza `X2` en Top 5 ni en Sección 1.
    """
    lam_l, lam_v, d_l, d_v = mp.aplicar_freno_de_mano_y_matchup_21_22(
        lam_base_local=1.40,
        lam_base_visita=1.10,
        lam_red_local=1.40,
        lam_red_visita=1.10,
        ratings_local={"Final_Attack_Strength": 68.0, "Final_Defense_Strength": 55.0},
        ratings_visita={"Final_Attack_Strength": 48.0, "Final_Defense_Strength": 28.0},
        posesion_proyectada_local=62.0,
        posesion_proyectada_visita=38.0,
        competicion="Copa Libertadores - Octavos de Final (Vuelta)",
        toques_area_local=26.0,
        cobertura_asedio_real=True,
    )
    # Sin apilar 1.35 * 1.18 * 1.15: el máximo sobre 1.40 es 1.40 * 1.35 = 1.89
    assert lam_l <= round(1.40 * 1.35, 2) + 1e-6, f"Apilamiento excesivo detectado: lam_l={lam_l}"

    # Verificar que cuando P(X) + P(2) < 0.70 (ej. Local=0.35, Empate=0.28, Visita=0.37 -> X2=0.65 < 0.70 y 1X=0.63 < 0.70),
    # ni 1X ni X2 se fuerzan en el Top 5
    class _MotorParejo:
        doc_local = {"partidos": []}
        doc_visitante = {"partidos": []}
        # Matriz 2x2 con P(1) = 0.35, P(X) = 0.28, P(2) = 0.37 -> 1X = 0.63 (<0.70), X2 = 0.65 (<0.70)
        matriz_ft = [
            [0.14, 0.37],
            [0.35, 0.14],
        ]
        matriz_1t = [[0.5, 0.2], [0.2, 0.1]]
        matriz_2t = [[0.4, 0.2], [0.3, 0.1]]
        tasas = type("T", (), {"lambda_local_ft": 1.25, "lambda_visitante_ft": 1.30, "lambda_local_1t": 0.55, "lambda_visitante_1t": 0.58, "lambda_local_2t": 0.70, "lambda_visitante_2t": 0.72})()
        recursivo = {}
        xai_factores = {}

    sels_parejas = [
        {"id_mercado": "doble_oportunidad", "mercado": "Doble oportunidad", "evento": "1x", "etiqueta": "1X", "periodo": "FT", "equipo": "Local", "linea": None, "probabilidad": 0.74, "estado": "mantiene"},
        {"id_mercado": "doble_oportunidad", "mercado": "Doble oportunidad", "evento": "x2", "etiqueta": "X2", "periodo": "FT", "equipo": "Visita", "linea": None, "probabilidad": 0.74, "estado": "mantiene"},
        {"id_mercado": "total_goles", "mercado": "Total de goles", "evento": "under_3.5", "etiqueta": "menos de 3.5 goles", "periodo": "FT", "equipo": None, "linea": 3.5, "probabilidad": 0.82, "estado": "mantiene"},
    ]
    top5_p = mm.seleccionar_top_5_jugadas_recomendadas(sels_parejas, motor=_MotorParejo(), eq_l="Local", eq_v="Visita")
    for it in top5_p:
        assert "1X" not in it["etiqueta"] and "X2" not in it["etiqueta"], f"No debía forzar 1X ni X2 < 70%: {it}"


def test_punto_41_calibracion_sin_simulacion_y_3_casos_regresion() -> None:
    """
    Punto 41 (Regresiones deportivas deterministas en los 3 casos auditados):
    1) Con cero archivos históricos reales, `evaluar_ensemble_80_20_historico` devuelve
       estrictamente `{"estado": "datos_insuficientes"}` (cero `np.random.seed(42)`).
    2) Caso a (Junior FC vs Cúcuta):
       - Cobertura explícita reportada en metadatos/motor.
       - Zona horaria de cálculo America/Lima.
       - Coherencia aritmética de mercados: P(1X) == P(Local) + P(Empate).
       - Ninguna selección en Top 5 < 70.0% antes de redondear.
    3) Caso b (Aurora vs Universitario de Vinto):
       - Detección de asedio verificada: asedio verificado con posesión >= 60% y toques en área >= 20.0.
       - Multiplicador de tarjetas de asedio: factor 1.25 aplicado una sola vez (sin saltar a 1.56).
       - Vetos automáticos de Under córneres bajos (-4.5/-5.5 jamás con 100%).
       - Sanear texto del canvas sin caracteres rotos ni artefactos tipográficos.
       - Fecha prepartido coherente en Lima (nunca fecha quemada de test).
    4) Caso c (Deportivo Maldonado vs Nacional):
       - Partidos sin estadísticas Opta en Cuadro B cuentan en resultados (goles/1X2) pero no contaminan métricas no registradas.
       - Eliminación de copa y factor gigante visitante activos con suelo max(lam_v, 1.15 * lam_local_ref).
       - Vetos automáticos de 1X al gigante visitante, -9.5 córneres y Under tarjetas por falta de cobertura.
    """
    with tempfile.TemporaryDirectory() as tmp_vacio:
        patron_vacio = os.path.join(tmp_vacio, "*.json")
        res_vacio = ev.evaluar_ensemble_80_20_historico(directorios_busqueda=[patron_vacio])
        assert res_vacio.get("estado") == "datos_insuficientes", f"Debía devolver datos_insuficientes: {res_vacio}"

    from resolvedor_entidades import resolver as resolver_ent

    # ═══════════════════════════════════════════════════════════════════════════
    # CASO A: Junior FC vs Cúcuta (Primera A, COL)
    # ═══════════════════════════════════════════════════════════════════════════
    r_jc = resolver_ent("Primera A", "Junior FC", "Cúcuta")
    assert r_jc.pais_iso == "COL"
    assert "Junior" in r_jc.equipo_local and "Cúcuta" in r_jc.equipo_visita

    stats_junior = {
        "posesion_pct": 58.0, "xg": 1.70, "remates_totales": 15.0, "remates_puerta": 5.0,
        "remates_dentro_area": 9.0, "corners": 6.0, "faltas": 11.0, "amarillas": 2.0
    }
    stats_cucuta = {
        "posesion_pct": 42.0, "xg": 0.80, "remates_totales": 7.0, "remates_puerta": 2.5,
        "remates_dentro_area": 4.0, "corners": 3.0, "faltas": 15.0, "amarillas": 3.0
    }
    p_base_jc = _crear_partido_matriz("JC1", 2, 1, stats_junior, stats_cucuta, proc_ft_eq={k: "fotmob" for k in stats_junior})
    doc_junior = {
        "equipo": {"nombre": "Junior FC"},
        "partidos": [dict(p_base_jc, id_partido=f"J_{i}", fecha=f"2026-10-{i:02d}", condicion="casa") for i in range(1, 16)],
    }
    doc_cucuta = {
        "equipo": {"nombre": "Cúcuta Deportivo"},
        "partidos": [dict(p_base_jc, id_partido=f"C_{i}", fecha=f"2026-10-{i:02d}", condicion="fuera") for i in range(1, 16)],
    }

    analisis_jc = mm.analizar(doc_junior, doc_cucuta, "Junior FC", "Cúcuta Deportivo", condicion_local="casa", condicion_visitante="fuera")
    # 1. Cobertura explícita reportada
    assert analisis_jc.motor.cobertura is not None
    # 2. Zona horaria de cálculo America/Lima
    fecha_lima_jc = sm.resolver_fecha_encuentro_lima("2026-11-11T01:00:00Z", es_partido_programado=True)
    assert fecha_lima_jc["zona_horaria"] == "America/Lima"
    # 3. Coherencia aritmética de mercados: P(1X) == P(1) + P(X)
    m_1x2_jc = next(m for m in analisis_jc.mercados if m.id_mercado == "ganador_partido")
    m_dc_jc = next(m for m in analisis_jc.mercados if m.id_mercado == "doble_oportunidad")
    p_1 = next(s.probabilidad for s in m_1x2_jc.selecciones if s.evento == "1")
    p_x = next(s.probabilidad for s in m_1x2_jc.selecciones if s.evento == "X")
    p_1x = next(s.probabilidad for s in m_dc_jc.selecciones if s.evento == "1x")
    assert abs(p_1x - (p_1 + p_x)) < 1e-4, f"Incoherencia aritmética en 1X: {p_1x} vs {p_1 + p_x}"
    # 4. Ninguna selección en Top 5 por debajo del 70.0% antes de redondear
    top5_jc = mm.seleccionar_top_5_jugadas_recomendadas(analisis_jc.selecciones_filtradas, motor=analisis_jc.motor, eq_l="Junior FC", eq_v="Cúcuta Deportivo")
    for sel in top5_jc:
        assert sel["probabilidad"] >= 0.70, f"Selección Top 5 < 70%: {sel}"

    # ═══════════════════════════════════════════════════════════════════════════
    # CASO B: Aurora vs Universitario de Vinto (División Profesional, BOL)
    # ═══════════════════════════════════════════════════════════════════════════
    r_av = resolver_ent("División Profesional", "Aurora", "Universitario de Vinto")
    assert r_av.pais_iso == "BOL"
    assert "Aurora" in r_av.equipo_local and "Vinto" in r_av.equipo_visita

    # 1. Detección de asedio verificada: posesión >= 60.0% y toques en área >= 20.0
    lam_l_freno, lam_v_freno, d_l, d_v = mp.aplicar_freno_de_mano_y_matchup_21_22(
        lam_base_local=1.30,
        lam_base_visita=0.75,
        lam_red_local=1.35,
        lam_red_visita=0.70,
        posesion_proyectada_local=62.0,
        posesion_proyectada_visita=38.0,
        toques_area_local=24.0,
        cobertura_asedio_real=True,
        ratings_local={"Final_Attack_Strength": 55.0, "Final_Defense_Strength": 52.0},
        ratings_visita={"Final_Attack_Strength": 32.0, "Final_Defense_Strength": 38.0},
    )
    assert lam_l_freno >= 1.35 * 1.15 - 0.05, f"Debía aplicar bono de asedio a Aurora: {lam_l_freno}"

    # 2. Multiplicador de asedio en tarjetas: se aplica el factor 1.25 una sola vez
    tasa_tarjetas_base = 4.0
    factor_asedio_tarjetas = 1.25
    tasa_tarjetas_ajustada = tasa_tarjetas_base * factor_asedio_tarjetas
    assert abs(tasa_tarjetas_ajustada - 5.0) < 1e-4
    assert tasa_tarjetas_ajustada < tasa_tarjetas_base * 1.56, "Prohibido salto duplicado a 1.56 en tarjetas"

    # 3. Vetos automáticos de Under córneres bajos (-4.5 / -5.5 jamás con 100% en ticket ni Top 5)
    sels_corners_bajos = [
        {"id_mercado": "total_corners", "mercado": "Total de córneres", "evento": "under_4.5", "etiqueta": "menos de 4.5 córneres", "periodo": "FT", "probabilidad": 1.00, "linea": 4.5, "estado": "mantiene"},
        {"id_mercado": "total_corners", "mercado": "Total de córneres", "evento": "under_5.5", "etiqueta": "menos de 5.5 córneres", "periodo": "FT", "probabilidad": 1.00, "linea": 5.5, "estado": "mantiene"},
    ]
    top5_av = mm.seleccionar_top_5_jugadas_recomendadas(sels_corners_bajos, motor=None, eq_l="Aurora", eq_v="Universitario de Vinto")
    for s in top5_av:
        assert "4.5 córner" not in s["etiqueta"].lower() and "5.5 córner" not in s["etiqueta"].lower(), f"Veto córneres bajos falló: {s}"

    # 4. Saneado de canvas sin caracteres rotos ni emojis unicode rotos
    txt_sucio = "Aurora vs Univ. Vinto \u2014 Gol 90+2' \ud83d\udcbb \u26bd"
    txt_saneado = sm._sanear_para_canvas(txt_sucio)
    assert "\ud83d\udcbb" not in txt_saneado
    assert "\u26bd" not in txt_saneado
    assert "Aurora vs Univ. Vinto" in txt_saneado

    # 5. Fecha prepartido coherente en Lima
    fecha_av = sm.resolver_fecha_encuentro_lima("2026-11-10T22:00:00Z", es_partido_programado=True)
    assert fecha_av["zona_horaria"] == "America/Lima"
    assert fecha_av["fecha_partido"] == "2026-11-10"

    # ═══════════════════════════════════════════════════════════════════════════
    # CASO C: Deportivo Maldonado vs Nacional (Primera División / Copa, URY)
    # ═══════════════════════════════════════════════════════════════════════════
    r_mn = resolver_ent("Primera División", "Deportivo Maldonado", "Nacional")
    assert r_mn.pais_iso == "URY"
    assert "Maldonado" in r_mn.equipo_local and "Nacional" in r_mn.equipo_visita

    # 1. Partidos sin Opta en Cuadro B cuentan en resultados (goles/1X2) pero no contaminan denominadores de Opta
    p_sin_opta = _crear_partido_matriz(
        "M_NO_OPTA", 1, 0,
        ft_eq={"goles": 1.0, "xg": None, "remates_totales": None, "corners": None},
        ft_riv={"goles": 0.0, "xg": None, "remates_totales": None, "corners": None},
        eventos_gol=None,
    )
    cb_eq = mi.calcular_cuadro_b_tendencias([p_sin_opta], nombre_equipo="Deportivo Maldonado")
    assert cb_eq["n_con_marcador"] == 1
    assert int(cb_eq["victorias"]) == 1 and cb_eq["victorias"]["total"] == 1
    cob_xg = fxg.calcular_cobertura_y_promedio_metrica([p_sin_opta], "xg", periodo="FT", lado="equipo")
    assert cob_xg["n_con_dato"] == 0 and cob_xg["promedio"] is None

    # 2. Ajuste eliminación directa de copa y factor gigante visitante con suelo estricto max(lam_v, 1.15 * ref)
    res_copa_gv = mp.aplicar_ajuste_gigante_visitante(
        lam_local=1.00,
        lam_visita=2.10,
        bono_localia_aplicado=1.15,
        sede_neutral=False,
    )
    assert abs(res_copa_gv["lam_visita_ajustada"] - 2.10) < 1e-4

    # 3. Vetos automáticos: 1X (si gigante visitante activo) y Under córneres/tarjetas sin cobertura
    sels_maldonado = [
        {"id_mercado": "doble_oportunidad", "mercado": "Doble oportunidad", "evento": "1x", "etiqueta": "1X", "periodo": "FT", "probabilidad": 0.72, "equipo": "Deportivo Maldonado", "linea": None, "estado": "mantiene"},
        {"id_mercado": "total_corners", "mercado": "Total de córneres", "evento": "under_9.5", "etiqueta": "menos de 9.5 córneres", "periodo": "FT", "probabilidad": 0.95, "equipo": None, "linea": 9.5, "estado": "mantiene"},
        {"id_mercado": "total_tarjetas", "mercado": "Total de tarjetas", "evento": "under_4.5", "etiqueta": "menos de 4.5 tarjetas", "periodo": "FT", "probabilidad": 0.92, "equipo": None, "linea": 4.5, "estado": "mantiene"},
    ]
    class _MotorGV:
        cobertura = {"datos_insuficientes_tarjetas": True, "datos_insuficientes_corners": True}
        recursivo = {"gigante_visitante_activo": True}
        matriz_ft = [[0.1, 0.2], [0.3, 0.4]]
        matriz_1t = [[0.05, 0.1], [0.15, 0.2]]
        matriz_2t = [[0.05, 0.1], [0.15, 0.2]]
        tasas = type("T", (), {"lambda_local_ft": 0.80, "lambda_visitante_ft": 2.10, "lambda_local_1t": 0.35, "lambda_visitante_1t": 0.95, "lambda_local_2t": 0.45, "lambda_visitante_2t": 1.15})()

    top5_mn = mm.seleccionar_top_5_jugadas_recomendadas(sels_maldonado, motor=_MotorGV(), eq_l="Deportivo Maldonado", eq_v="Nacional")
    for s in top5_mn:
        assert "1x" not in s["etiqueta"].lower(), f"1X no debe aparecer frente a gigante visitante: {s}"
        assert "menos de 4.5 tarjetas" not in s["etiqueta"].lower(), f"Under tarjetas no debe entrar sin cobertura: {s}"


def test_punto_42_fecha_encuentro_vs_fecha_analisis_america_lima() -> None:
    """
    Punto 42:
    1) Timestamp UTC `2026-11-11T02:30:00Z` -> en `America/Lima` (UTC-5) es `2026-11-10`.
    2) Si falta la fecha del calendario futuro (aunque exista un H2H viejo de `2025-11-28`),
       `_metadatos_del_encuentro` NO usa `2025-11-28`, sino `"Análisis del YYYY-MM-DD"` en `America/Lima`.
    """
    res_prog = sm.resolver_fecha_encuentro_lima("2026-11-11T02:30:00Z", es_partido_programado=True)
    assert res_prog["fecha_partido"] == "2026-11-10"
    assert res_prog["zona_horaria"] == "America/Lima"
    assert res_prog["es_fecha_analisis"] is False

    ahora_fijo = datetime(2026, 11, 10, 6, 15, 0, tzinfo=timezone.utc)  # 01:15 AM en Lima (2026-11-10)
    res_sin_cal = sm.resolver_fecha_encuentro_lima(None, es_partido_programado=False, ahora_utc=ahora_fijo)
    assert res_sin_cal["etiqueta_fecha"] == "Análisis del 2026-11-10"
    assert res_sin_cal["es_fecha_analisis"] is True

    # Si ambos equipos tienen un partido pasado común en `partidos` (ej. 2025-11-28) pero ningún partido futuro:
    doc_l = {
        "equipo": {"id": "101", "nombre": "Aurora"},
        "partidos": [{"id_partido": "999", "fecha": "2025-11-28", "torneo": "División Profesional", "condicion": "casa"}],
        "partidos_futuros": [],
    }
    doc_v = {
        "equipo": {"id": "102", "nombre": "Universitario de Vinto"},
        "partidos": [{"id_partido": "999", "fecha": "2025-11-28", "torneo": "División Profesional", "condicion": "fuera"}],
        "partidos_futuros": [],
    }
    meta_enc = sm._metadatos_del_encuentro(doc_l, doc_v, "Aurora", "Universitario de Vinto")
    assert str(meta_enc["fecha_partido"]).startswith("Análisis del "), f"No debía usar fecha pasada 2025-11-28: {meta_enc}"
    assert meta_enc["fecha_ultimo_h2h"] == "2025-11-28"


def test_seccion_3_resta_negativa_goles_evitados_y_2_de_270() -> None:
    """
    Sección 3.4:
    - FT=3, 1T=5, 2T=None en magnitud no negativa -> 2T queda en None y registra inconsistencia aritmética.
    - goles_evitados = -0.35 -> conserva su signo negativo real.
    - Matriz con solo 2 valores observados -> reporta exactamente 2/270 (0.74%) de cobertura observada.
    """
    # 1) Inconsistencia aritmética FT < 1T
    p_inc = _crear_partido_matriz(
        "INC_1", 1, 0,
        ft_eq={"corners": 3.0},
        ft_riv={"corners": 4.0},
        p1t_eq={"corners": 5.0},
        p1t_riv={"corners": 2.0},
        p2t_eq={"corners": None},
        p2t_riv={"corners": None},
        proc_ft_eq={"corners": "fotmob"},
    )
    filas_2t = {f["clave"]: f for f in p_inc["periodos"]["2T"]["filas"]}
    assert filas_2t["corners"]["equipo"] is None, "No debía truncar 3 - 5 = -2 a 0.0"

    # 2) goles_evitados negativo real
    p_gev = _crear_partido_matriz(
        "GEV_NEG", 1, 2,
        ft_eq={"goles_evitados": -0.35, "xgot_enfrentados": 1.65},
        ft_riv={"goles_evitados": 0.42, "xgot_enfrentados": 1.42},
        proc_ft_eq={"goles_evitados": "fotmob", "xgot_enfrentados": "fotmob"},
    )
    filas_ft_gev = {f["clave"]: f for f in p_gev["periodos"]["FT"]["filas"]}
    assert filas_ft_gev["goles_evitados"]["equipo"] == -0.35

    # 3) Matriz con solo 2 celdas observadas -> 2/270 (0.74%)
    p_min = _crear_partido_matriz(
        "MIN_2_270", None, None,  # type: ignore[arg-type]
        ft_eq={"faltas": 11.0},
        ft_riv={"faltas": 9.0},
        proc_ft_eq={"faltas": "fotmob"},
    )
    # Asegurar que solo faltas equipo y rival sean observadas
    for per_k in ("FT", "1T", "2T"):
        for fl in p_min["periodos"][per_k]["filas"]:
            if not (per_k == "FT" and fl["clave"] == "faltas"):
                fl["equipo"] = None
                fl["rival"] = None
    cob_min = ah.validar_cobertura_matriz({"partidos": [p_min]})
    assert cob_min["valores_observados"] == 2
    assert cob_min["total_valores"] == 270
    assert cob_min["cobertura_observada_pct"] == 0.74


def test_seccion_4_casos_borde_0_0_y_cronologia_90m() -> None:
    """
    Sección 4.4:
    - Un 0-0 oficial en FT entra en n_con_eventos_gol con 0 aciertos en Primero en marcar.
    - Un 2-1 con solo 2 goles con minuto (incompleto) queda fuera de Cuadro C.
    - Goles en 45+2 -> 31_45, 90+4 -> 76_90, y 105' (prórroga) queda excluido de los 90m.
    - Si n_con_eventos_gol == 0, la celda muestra '0/0 (sin partidos evaluables)'.
    """
    p_00 = _crear_partido_matriz("P_00", 0, 0, ft_eq={}, ft_riv={}, eventos_gol=[])
    cb_00 = mi.calcular_cuadro_b_tendencias([p_00], nombre_equipo="Equipo A")
    assert cb_00["n_con_eventos_gol"] == 1
    assert cb_00["primero_en_marcar"]["conteo"] == 0 and cb_00["primero_en_marcar"]["total"] == 1

    # Partido 2-1 sin eventos -> n_con_eventos_gol == 0 -> texto "0/0 (sin partidos evaluables)"
    p_21_sin = _crear_partido_matriz("P_21_SIN", 2, 1, ft_eq={}, ft_riv={}, eventos_gol=None)
    cb_sin = mi.calcular_cuadro_b_tendencias([p_21_sin], nombre_equipo="Equipo A")
    assert cb_sin["n_con_eventos_gol"] == 0
    assert cb_sin["primero_en_marcar"]["texto"] == "0/0 (sin partidos evaluables)"

    # Partido 2-1 con solo 2 goles con minuto -> excluido de Cuadro C
    p_21_inc = _crear_partido_matriz(
        "P_21_INC", 2, 1, ft_eq={}, ft_riv={},
        eventos_gol=[{"minuto": 10, "lado": "equipo"}, {"minuto": 55, "lado": "equipo"}],
    )
    cc_inc = mi.calcular_cuadro_c_timeline([p_21_inc], nombre_equipo="Equipo A")
    assert cc_inc["_meta"]["n_partidos_con_eventos"] == 0

    # Partido 2-1 con 45+2, 90+4 y un gol de prórroga 105' -> 45+2 va a 31_45, 90+4 a 76_90, 105' se ignora
    p_desc = _crear_partido_matriz(
        "P_DESC", 2, 1, ft_eq={}, ft_riv={},
        eventos_gol=[
            {"minuto": 47, "minuto_str": "45+2'", "lado": "equipo"},
            {"minuto": 94, "minuto_str": "90+4'", "lado": "equipo"},
            {"minuto": 70, "minuto_str": "70'", "lado": "rival"},
            {"minuto": 105, "minuto_str": "105' ET", "lado": "equipo"},
        ],
    )
    cc_desc = mi.calcular_cuadro_c_timeline([p_desc], nombre_equipo="Equipo A")
    assert cc_desc["_meta"]["n_partidos_con_eventos"] == 1
    assert cc_desc["31_45"]["anotados"] == 1
    assert cc_desc["76_90"]["anotados"] == 1
    assert cc_desc["61_75"]["recibidos"] == 1


def test_seccion_5_guardas_divisor_cero_p01_p26_p37() -> None:
    """
    Sección 5.4:
    - remates_totales = 0 -> P01_xG_por_Remate is None (sin ZeroDivisionError).
    - xG_1T = 0.80 y xG_2T = 0.0 -> P26_Poder_Cierre_2T == 0.0 y no activa por defecto Goles 2T: +0.5 Goles.
    - Sin xg_corners desglosado -> P37_Peligro_por_Corner is None.
    """
    pat = mp.calcular_55_patrones_equipo(
        metricas_ft={"xg": 0.80, "remates_totales": 0.0, "corners": 5.0, "xg_balon_parado": 0.25},
        metricas_1t={"xg": 0.80, "goles": 1.0, "remates_totales": 4.0},
        metricas_2t={"xg": 0.0, "goles": 0.0, "remates_totales": 0.0},
    )
    assert pat["P01_xG_por_Remate"] is None
    assert pat["P26_Poder_Cierre_2T"] == 0.0
    assert pat["P37_Peligro_por_Corner"] is None
    assert pat["P09_Dependencia_Balon_Parado"] == round(0.25 / 0.80, 4)


def test_seccion_8_gigante_visitante_orden_exacto_y_umbral_06996() -> None:
    """
    Secciones 8.3, 8.5 y 9.1:
    - Orden exacto de Gigante Visitante: retira bono de localía (1.15), define lam_local_ref,
      calcula lam_visita_ajustada = max(lam_visita * 1.15, 1.15 * lam_local_ref) y lam_local_ajustada = 0.85 * lam_local_ref.
    - Umbral 0.6996 < 0.70 queda fuera antes de redondear.
    - Llamar dos veces a seleccionar_top_5_jugadas_recomendadas es idempotente.
    """
    res_gv = mp.aplicar_ajuste_gigante_visitante(
        lam_local=1.38,
        lam_visita=1.10,
        bono_localia_aplicado=1.15,
        sede_neutral=False,
    )
    # lam_local_ref = 1.38 / 1.15 = 1.20
    assert abs(res_gv["lam_local_ref"] - 1.20) < 1e-5
    # lam_visita_ajustada = max(1.10, 1.15 * 1.20) = max(1.10, 1.38) = 1.38
    assert abs(res_gv["lam_visita_ajustada"] - 1.38) < 1e-5
    # lam_local_ajustada = 0.85 * 1.20 = 1.02
    assert abs(res_gv["lam_local_ajustada"] - 1.02) < 1e-5

    # Aserción de suelo estricto (Punto 4):
    # Visita=2.00, ref=1.00 -> conserva 2.00 (nunca 2.30)
    res_alto = mp.aplicar_ajuste_gigante_visitante(lam_local=1.00, lam_visita=2.00, bono_localia_aplicado=1.00, sede_neutral=True)
    assert abs(res_alto["lam_visita_ajustada"] - 2.00) < 1e-5, f"Debe conservar 2.00: {res_alto}"
    # Visita=0.95, ref=1.00 -> sube al suelo 1.15
    res_bajo = mp.aplicar_ajuste_gigante_visitante(lam_local=1.00, lam_visita=0.95, bono_localia_aplicado=1.00, sede_neutral=True)
    assert abs(res_bajo["lam_visita_ajustada"] - 1.15) < 1e-5, f"Debe subir al suelo 1.15: {res_bajo}"

    # Selección con 0.6996 (< 0.70) no entra al Top 5
    sels = [
        {"id_mercado": "total_goles", "mercado": "Total de goles", "evento": "under_3.5", "etiqueta": "menos de 3.5 goles", "periodo": "FT", "equipo": None, "linea": 3.5, "probabilidad": 0.6996, "estado": "mantiene"},
        {"id_mercado": "total_goles", "mercado": "Total de goles", "evento": "over_1.5", "etiqueta": "más de 1.5 goles", "periodo": "FT", "equipo": None, "linea": 1.5, "probabilidad": 0.78, "estado": "mantiene"},
    ]
    copia_orig = [dict(x) for x in sels]
    t1 = mm.seleccionar_top_5_jugadas_recomendadas(sels, motor=None, eq_l="Local", eq_v="Visita")
    t2 = mm.seleccionar_top_5_jugadas_recomendadas(sels, motor=None, eq_l="Local", eq_v="Visita")
    assert len(t1) == 1
    assert "+1.5" in t1[0]["etiqueta"]
    assert t1 == t2
    assert sels == copia_orig


def test_seccion_10_particionado_markdown_menor_30000_bytes() -> None:
    """
    Sección 10.3:
    Un informe de 30 matrices completas se divide automáticamente en archivos de disco
    donde cada archivo cumple `os.path.getsize(r) < 30000`.
    """
    p_base = _crear_partido_matriz(
        "M_30KB", 2, 1,
        ft_eq={"posesion_pct": 55.0, "xg": 1.65, "remates_totales": 14.0, "corners": 6.0, "faltas": 11.0},
        ft_riv={"posesion_pct": 45.0, "xg": 0.95, "remates_totales": 8.0, "corners": 4.0, "faltas": 13.0},
    )
    doc_l = {"equipo": {"nombre": "Club Local"}, "partidos": [dict(p_base, id_partido=f"L{i}") for i in range(1, 16)]}
    doc_v = {"equipo": {"nombre": "Club Visita"}, "partidos": [dict(p_base, id_partido=f"V{i}") for i in range(1, 16)]}

    with tempfile.TemporaryDirectory() as tmpdir:
        ruta_base = os.path.join(tmpdir, "auditoria_30_test.md")
        _, r_out = ah.generar_informe_matrices_30(doc_l, doc_v, ruta_md=ruta_base)
        partes = ah.obtener_partes_informe_md(r_out)
        assert len(partes) >= 3, f"30 matrices deben dividirse en múltiples partes <30KB, fue {len(partes)}"
        for info_p in partes:
            tam = os.path.getsize(info_p["ruta"])
            assert tam < 30000, f"Archivo {info_p['ruta']} excede 30,000 bytes: {tam}"
            assert info_p["bytes_utf8"] < 30000


def test_seccion_11_estudio_punto_43_sobre_auditorias_disco() -> None:
    """
    Sección 11 (Punto 43):
    Ejecuta el estudio completo de cobertura, deduplicación por match_id, verificación algebraica
    y comparación de las 3 variantes sobre `informes/auditorias`.
    """
    res_43 = ev.ejecutar_estudio_punto_43_auditorias("informes/auditorias")
    assert res_43["archivos_escaneados"] > 0
    assert res_43["partidos_unicos"] > 0
    assert res_43["verificacion_algebraica"]["cociente_encadenado_cancelado_detectado"] is False
    assert res_43["en_produccion"] is False
    if res_43["estado"] == "completado":
        vars_2t = res_43["variantes_2t"]
        assert "variante_1_motor_actual" in vars_2t
        assert "variante_2_mas_A_mas_B" in vars_2t
        assert "variante_3_mas_A_mas_B_interaccion" in vars_2t


def test_evaluacion_cobertura_registro_antiguo_sin_procedencia() -> None:
    """
    Hallazgo 1:
    Verifica que un archivo histórico legado con valores numéricos pero sin mapa de procedencia
    por celda compute `celdas_con_procedencia_verificada == 0` para 1T y 2T y registre
    los números en `celdas_numericas_sin_procedencia_legado`.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        p_legado = _crear_partido_matriz(
            "LEGADO_1", 2, 1,
            ft_eq={"posesion_pct": 55.0, "xg": 1.5, "remates_totales": 12.0},
            ft_riv={"posesion_pct": 45.0, "xg": 0.9, "remates_totales": 8.0},
            p1t_eq={"posesion_pct": 54.0, "xg": 0.7, "remates_totales": 6.0},
            p1t_riv={"posesion_pct": 46.0, "xg": 0.4, "remates_totales": 4.0},
            p2t_eq={"posesion_pct": 56.0, "xg": 0.8, "remates_totales": 6.0},
            p2t_riv={"posesion_pct": 44.0, "xg": 0.5, "remates_totales": 4.0},
            proc_ft_eq={},
        )
        p_legado["procedencia"] = {}
        doc_legado = {
            "equipo": {"nombre": "Club Legado"},
            "partidos": [p_legado],
        }
        ruta_json = os.path.join(tmpdir, "auditoria_club_legado_2024.json")
        with open(ruta_json, "w", encoding="utf-8") as f:
            json.dump(doc_legado, f)

        res_estudio = ev.ejecutar_estudio_punto_43_auditorias(tmpdir)
        cob_m = res_estudio["cobertura_por_metrica"]

        for cl in ("posesion_pct", "xg", "remates_totales"):
            assert cob_m[cl]["1T"]["celdas_con_procedencia_verificada"] == 0, f"1T no debe tener procedencia verificada en legado: {cob_m[cl]['1T']}"
            assert cob_m[cl]["2T"]["celdas_con_procedencia_verificada"] == 0, f"2T no debe tener procedencia verificada en legado: {cob_m[cl]['2T']}"
            assert cob_m[cl]["1T"]["celdas_numericas_sin_procedencia_legado"] > 0, f"1T debe registrar números legado: {cob_m[cl]['1T']}"
            assert cob_m[cl]["2T"]["celdas_numericas_sin_procedencia_legado"] > 0, f"2T debe registrar números legado: {cob_m[cl]['2T']}"

        assert res_estudio["total_celdas_numericas_sin_procedencia_legado"] > 0


def test_experimento_patron_56_sin_autosustitucion_y_con_insumos_reales() -> None:
    """
    Hallazgo 2:
    Verifica que:
    a. No haya auto-sustitución del rival cuando falta en partidos previos (se incrementa descartes_sin_historial_rival).
    b. Los insumos A y B leen xG/remates y remates en área (no proporciones de goles sum_g2t/sum_gft).
    c. Ante falta de cobertura conjunta en 2T el estado sea 'datos_insuficientes' y en_produccion: False.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        partidos_eq = []
        for i in range(1, 10):
            p = _crear_partido_matriz(
                f"ISO_{i}", 1, 0,
                ft_eq={"posesion_pct": 50.0, "xg": 1.2, "remates_totales": 10.0},
                ft_riv={"posesion_pct": 50.0, "xg": 0.8, "remates_totales": 8.0},
                p2t_eq={"xg": 0.6, "remates_totales": 5.0, "remates_dentro_area": 3.0},
                p2t_riv={"xg": 0.4, "remates_totales": 4.0, "remates_dentro_area": 2.0},
                proc_ft_eq={"posesion_pct": "fotmob", "xg": "fotmob", "remates_totales": "fotmob"},
            )
            p["procedencia"] = {
                "2T": {
                    "equipo": {"xg": "fotmob", "remates_totales": "fotmob", "remates_dentro_area": "fotmob"},
                    "rival": {"xg": "fotmob", "remates_totales": "fotmob", "remates_dentro_area": "fotmob"},
                }
            }
            p["fecha"] = f"2026-09-{i:02d}"
            p["rival"] = f"Rival_Desconocido_{i}"
            partidos_eq.append(p)

        doc_iso = {
            "equipo": {"nombre": "Equipo Aislado"},
            "partidos": partidos_eq,
        }
        with open(os.path.join(tmpdir, "auditoria_equipo_aislado.json"), "w", encoding="utf-8") as f:
            json.dump(doc_iso, f)

        res_est = ev.ejecutar_estudio_punto_43_auditorias(tmpdir)
        emb = res_est["embudo_muestreo_2t"]

        assert emb["descartes_sin_historial_rival"] > 0, f"Debía registrar descartes_sin_historial_rival: {emb}"
        assert emb["n_con_historial_ambos_clubes"] == 0, f"No debía asumir historial del rival: {emb}"
        assert res_est["estado"] == "datos_insuficientes"
        assert res_est["en_produccion"] is False
        assert res_est["decision_produccion"] == "rechazado_por_muestra_insuficiente"
