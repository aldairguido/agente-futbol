"""Tests de Ronda 22: calidad de datos, homónimos, Sección 2, etiquetas y hora discrepante."""
from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# BLOQUE 1: Penales en Flashscore, liquidación 90' y FIRMA_MOTOR
# ═══════════════════════════════════════════════════════════════════════════════


def test_b1_flashscore_penales_intercalados_palmeiras_90_min():
    from fuente_flashscore import resolver_marcador_reglamentario_flashscore

    # Los 3 partidos reales de copa de Palmeiras en Flashscore DOM (intercalados):
    # 1) 08.11 Vasco vs Palmeiras: 90' FT 2-1, penales (4)-(5)
    gl, gv, desemp = resolver_marcador_reglamentario_flashscore(
        ["2", "(4)", "1", "(5)", "W"], estado_extra="Pen"
    )
    assert (gl, gv) == (2, 1)
    assert desemp == "4-5 (Pen)"

    # 2) 21.10 Palmeiras vs Fluminense: 90' FT 0-1, penales (5)-(3)
    gl, gv, desemp = resolver_marcador_reglamentario_flashscore(
        ["0", "(5)", "1", "(3)", "W"], estado_extra="Pen"
    )
    assert (gl, gv) == (0, 1)
    assert desemp == "5-3 (Pen)"

    # 3) 16.09 LDU Quito vs Palmeiras: 90' FT 3-2, penales (3)-(4)
    gl, gv, desemp = resolver_marcador_reglamentario_flashscore(
        ["3", "(3)", "2", "(4)", "W"], estado_extra="Pen"
    )
    assert (gl, gv) == (3, 2)
    assert desemp == "3-4 (Pen)"

    # Compatibilidad con formato contiguo Bosnia (Pen) y prórroga (AET):
    gl_b, gv_b, desemp_b = resolver_marcador_reglamentario_flashscore(
        ["5", "2", "(1)", "(1)"], estado_extra="Pen"
    )
    assert (gl_b, gv_b) == (1, 1)
    assert desemp_b == "5-2 (Pen)"

    gl_aet, gv_aet, desemp_aet = resolver_marcador_reglamentario_flashscore(
        ["2", "(1)", "1", "(1)"], estado_extra="AET"
    )
    assert (gl_aet, gv_aet) == (1, 1)
    assert desemp_aet == "2-1 (AET)"


def test_b1_liquidacion_extraer_resultado_usa_90_en_penales_y_prorroga():
    from evaluacion_pronosticos import extraer_resultado

    # Caso Penales: 90' terminó 1-1, tanda 5-4 (header.teams.score viene con 5-4 o 1-1)
    detalle_pen = {
        "general": {"matchId": "999001", "leagueName": "Copa do Brasil"},
        "header": {
            "teams": [
                {"name": "Vasco", "score": 4},
                {"name": "Palmeiras", "score": 5},
            ],
            "status": {
                "finished": True,
                "reason": {"short": "Pen", "longKey": "finished_after_penalties"},
                "scoreBeforeExtraTime": "2 - 1",
            },
            "events": {"homeTeamGoals": {}, "awayTeamGoals": {}},
        },
        "content": {"matchFacts": {"events": {"events": []}}},
    }
    res = extraer_resultado(detalle_pen)
    assert res.goles_local == 2
    assert res.goles_visitante == 1
    assert any("90'" in n for n in res.notas)

    # Caso Prórroga (AET): 90' terminó 1-1, prórroga 2-1
    detalle_aet = {
        "general": {"matchId": "999002", "leagueName": "Copa Libertadores"},
        "header": {
            "teams": [
                {"name": "Local FC", "score": 2},
                {"name": "Visita FC", "score": 1},
            ],
            "status": {
                "finished": True,
                "reason": {"short": "AET", "longKey": "finished_after_extratime"},
                "scoreBeforeExtraTime": "1 - 1",
            },
            "events": {"homeTeamGoals": {}, "awayTeamGoals": {}},
        },
        "content": {"matchFacts": {"events": {"events": []}}},
    }
    res_aet = extraer_resultado(detalle_aet)
    assert res_aet.goles_local == 1
    assert res_aet.goles_visitante == 1


def test_b1_firma_motor_actualizada_r22():
    from servicio_mercados import FIRMA_MOTOR

    assert FIRMA_MOTOR == "red-neuronal-recursiva-45-r22"


# ═══════════════════════════════════════════════════════════════════════════════
# BLOQUE 2: Falso homónimo extranjero, guarda de antigüedad y "Datos hasta"
# ═══════════════════════════════════════════════════════════════════════════════


def _doc_minimo(nombre: str, liga: str, pais_iso: str, fechas: list[str], rivales: list[str], fuente: str = "fotmob"):
    partidos = []
    for i, fec in enumerate(fechas):
        partidos.append({
            "id_partido": f"{nombre}_{i}",
            "fecha": fec,
            "torneo": liga,
            "rival": rivales[i % len(rivales)],
            "condicion": "casa" if i % 2 == 0 else "fuera",
            "marcador": {"equipo": 1, "rival": 1},
            "periodos": {"FT": {"filas": []}, "1T": {"filas": []}, "2T": {"filas": []}},
        })
    return {
        "equipo": {"id": f"id_{nombre}", "nombre": nombre, "liga": liga, "pais_iso": pais_iso},
        "fuente": fuente,
        "generado_en": "2026-11-19T00:00:00Z",
        "partidos": partidos,
        "partidos_futuros": [],
    }


def test_b2_falso_homonimo_extranjero_abstiene_con_motivo_explicito(monkeypatch):
    import servicio_mercados as sm

    fechas_ok = [f"2026-11-{15 - i:02d}" for i in range(15)]
    doc_cor = _doc_minimo("Corinthians", "Serie A", "BRA", fechas_ok, ["Flamengo", "Palmeiras", "Bahia"])
    doc_bot_cmr = _doc_minimo("Botafogo", "Elite One", "CMR", fechas_ok, ["Coton Sport", "Canon Yaounde"])

    res_enf = {
        "equipo_local": "Corinthians",
        "equipo_visitante": "Botafogo",
        "doc_local": doc_cor,
        "doc_visitante": doc_bot_cmr,
        "contexto": {
            "competicion": "Série A (Brasil)",
            "pais_iso": "BRA",
            "fecha_partido": "2026-11-19",
        },
    }
    monkeypatch.setattr(sm, "resolver_enfrentamiento", lambda *a, **kw: dict(res_enf))
    res = sm.preparar_analisis("Corinthians", "Botafogo")
    assert res["abstenido"] is True
    assert res["estado_cobertura"] == "falso_homonimo_extranjero"
    assert "FALSO HOMÓNIMO EXTRANJERO" in res["motivo_abstencion"]
    assert res["analisis"].selecciones_filtradas == []
    assert "Datos hasta: 2026-11-15" in res["analisis_texto_3_parrafos"]


def test_b2_guarda_antiguedad_abstiene_datos_desactualizados(monkeypatch):
    import servicio_mercados as sm
    from servicio_mercados import (
        ETIQUETA_REGLA_ANTIGUEDAD,
        MAX_DIAS_ANTIGUEDAD_ULTIMO_PARTIDO,
        evaluar_antiguedad_datos,
    )

    fechas_rec = [f"2026-11-{15 - i:02d}" for i in range(15)]
    fechas_viejas = [f"2017-05-{20 - i:02d}" for i in range(15)]
    doc_l = _doc_minimo("Corinthians", "Brasileirao Serie A", "BRA", fechas_rec, ["Flamengo", "Palmeiras"])
    doc_v = _doc_minimo("Botafogo", "Brasileirao Serie A", "BRA", fechas_viejas, ["Flamengo", "Palmeiras"])

    antig = evaluar_antiguedad_datos(doc_l, doc_v, fecha_referencia="2026-11-19")
    assert antig["desactualizado"] is True
    assert antig["estado"] == "datos_desactualizados"
    assert antig["max_dias"] == MAX_DIAS_ANTIGUEDAD_ULTIMO_PARTIDO == 60
    assert antig["etiqueta_regla"] == ETIQUETA_REGLA_ANTIGUEDAD
    assert "regla operativa sin validación" in antig["motivo"]

    res_enf = {
        "equipo_local": "Corinthians",
        "equipo_visitante": "Botafogo",
        "doc_local": doc_l,
        "doc_visitante": doc_v,
        "contexto": {
            "competicion": "Série A (Brasil)",
            "pais_iso": "BRA",
            "fecha_partido": "2026-11-19",
        },
    }
    monkeypatch.setattr(sm, "resolver_enfrentamiento", lambda *a, **kw: dict(res_enf))
    res = sm.preparar_analisis("Corinthians", "Botafogo")
    assert res["abstenido"] is True
    assert res["estado_cobertura"] == "datos_desactualizados"
    assert "regla operativa sin validación" in res["motivo_abstencion"]
    assert "Datos hasta: 2026-11-15 / 2017-05-20" in res["analisis_texto_3_parrafos"]


def test_b2_encabezado_muestra_datos_hasta():
    from servicio_mercados import formatear_fuentes_clubes_encabezado

    doc_l = _doc_minimo("Vasco", "Serie A", "BRA", ["2026-11-12", "2026-11-08"], ["Bahia"])
    doc_v = _doc_minimo("Internacional", "Serie A", "BRA", ["2026-11-14", "2026-11-09"], ["Bahia"])
    hdr = formatear_fuentes_clubes_encabezado(
        {"equipo_local": "Vasco", "equipo_visitante": "Internacional"},
        doc_l,
        doc_v,
    )
    assert "Fuentes: Vasco: fotmob · Internacional: fotmob" in hdr
    assert "Datos hasta: 2026-11-12 / 2026-11-14" in hdr


# ═══════════════════════════════════════════════════════════════════════════════
# BLOQUE 3: Sección 2 con motivo de degradación (estrés vs desvío observado)
# ═══════════════════════════════════════════════════════════════════════════════


def test_b3_seccion2_muestra_motivo_degradacion_desvio_y_estres():
    from motor_mercados import (
        ESTADO_SENSIBLE,
        Parametros,
        Seleccion,
        construir_mensaje,
        formatear_motivo_degradacion_seccion2,
    )

    sel_desv = Seleccion(
        mercado="Doble oportunidad",
        id_mercado="doble_oportunidad",
        evento="1x",
        etiqueta="1X (Corinthians o empate)",
        periodo="FT",
        equipo="Corinthians",
        linea=None,
        probabilidad=0.714,
        estado=ESTADO_SENSIBLE,
        motivo="sin calibración validada (frec_emp: 33.3% vs mod: 71.4%, brecha -38.1 pp, desvío 38.1 pts > 10.0)",
        escenarios={"wc0_wx0": 0.714, "wc1_wx1": 0.714},
    )
    mot_desv_full = formatear_motivo_degradacion_seccion2(sel_desv, umbral=0.70, corto=False)
    mot_desv_corto = formatear_motivo_degradacion_seccion2(sel_desv, umbral=0.70, corto=True)
    assert "modelo 71.4% frente a observado 5/15 = 33%" in mot_desv_full
    assert "desvío obs. 5/15=33%" in mot_desv_corto

    sel_est = Seleccion(
        mercado="Botafogo - Tiros de esquina",
        id_mercado="corners_visitante",
        evento="under_5.5",
        etiqueta="Botafogo: menos de 5.5 córneres",
        periodo="FT",
        equipo="Botafogo",
        linea=5.5,
        probabilidad=0.736,
        estado=ESTADO_SENSIBLE,
        motivo="Cae por debajo del 70% en escenario wc1_wx1",
        escenarios={"wc0_wx0": 0.736, "wc1_wx1": 0.691},
    )
    mot_est_full = formatear_motivo_degradacion_seccion2(sel_est, umbral=0.70, corto=False)
    mot_est_corto = formatear_motivo_degradacion_seccion2(sel_est, umbral=0.70, corto=True)
    assert "estrés" in mot_est_full and "69.1%" in mot_est_full
    assert "estrés" in mot_est_corto and "69%" in mot_est_corto

    from types import SimpleNamespace
    motor_stub = SimpleNamespace(
        doc_local={"partidos": []},
        doc_visitante={"partidos": []},
        nombre_local="Corinthians",
        nombre_visitante="Botafogo",
        cond_local="casa",
        cond_visitante="fuera",
        tasas=SimpleNamespace(lambda_local_ft=1.3, lambda_visitante_ft=1.0, procedencia_tiempos="real"),
        matriz_ft=None,
        muestras={
            "local_cond": SimpleNamespace(partidos_condicion=8),
            "visitante_cond": SimpleNamespace(partidos_condicion=7),
        },
        clusters=None,
        recursivo={},
    )
    msg = construir_mensaje(
        {"equipo_local": "Corinthians", "equipo_visitante": "Botafogo", "fecha_partido": "2026-11-19"},
        {"mantiene": [], "sensible": [sel_desv, sel_est], "incompleta": []},
        Parametros(umbral=0.70),
        motor_stub,
        [],
    )
    assert "modelo 71.4% frente a observado 5/15 = 33%" in msg
    assert "estrés: cae hasta 69.1%" in msg


# ═══════════════════════════════════════════════════════════════════════════════
# BLOQUE 4: Calidad de fuente (n_claves_no_nulas, n_eventos_gol y "datos reducidos")
# ═══════════════════════════════════════════════════════════════════════════════


def test_b4_calidad_fuente_por_club_y_datos_reducidos_en_encabezado():
    from evaluacion_pronosticos import _extraer_fuentes_y_firma_registro
    from servicio_mercados import (
        calcular_calidad_fuente_club,
        extraer_fuentes_y_firma_datos,
        formatear_fuentes_clubes_encabezado,
    )

    doc_fm = {
        "equipo": {"nombre": "Vasco"},
        "fuente": "fotmob",
        "generado_en": "2026-11-19T00:00:00Z",
        "partidos": [
            {
                "fecha": "2026-11-15",
                "goles_eventos": [{"minuto": 12}, {"minuto": 67}],
                "periodos": {
                    "FT": {
                        "filas": [
                            {"clave": "xg", "equipo": 1.4, "rival": 0.8},
                            {"clave": "posesion", "equipo": 55, "rival": 45},
                            {"clave": "faltas", "equipo": None, "rival": None},
                        ]
                    }
                },
            }
        ],
    }
    doc_fs = {
        "equipo": {"nombre": "Botafogo"},
        "fuente": "flashscore",
        "generado_en": "2026-11-19T00:00:00Z",
        "partidos": [
            {
                "fecha": "2026-11-14",
                "periodos": {
                    "FT": {
                        "filas": [
                            {"clave": "posesion", "equipo": 48, "rival": 52},
                        ]
                    }
                },
            }
        ],
    }

    cal_fm = calcular_calidad_fuente_club(doc_fm)
    cal_fs = calcular_calidad_fuente_club(doc_fs)
    assert cal_fm == {"n_claves_no_nulas": 2, "n_eventos_gol": 2}
    assert cal_fs == {"n_claves_no_nulas": 1, "n_eventos_gol": 0}

    info = extraer_fuentes_y_firma_datos(doc_fm, doc_fs)
    assert info["calidad_fuente_local"] == {"n_claves_no_nulas": 2, "n_eventos_gol": 2}
    assert info["calidad_fuente_visitante"] == {"n_claves_no_nulas": 1, "n_eventos_gol": 0}

    reg = _extraer_fuentes_y_firma_registro(
        preparado={"contexto": {"doc_local": doc_fm, "doc_visitante": doc_fs}},
        metadatos={},
    )
    assert reg["calidad_fuente_local"] == {"n_claves_no_nulas": 2, "n_eventos_gol": 2}
    assert reg["calidad_fuente_visitante"] == {"n_claves_no_nulas": 1, "n_eventos_gol": 0}

    hdr_fs = formatear_fuentes_clubes_encabezado({}, doc_fm, doc_fs)
    assert "Fuentes: Vasco: fotmob · Botafogo: flashscore" in hdr_fs
    assert "datos reducidos" in hdr_fs

    hdr_fm = formatear_fuentes_clubes_encabezado({}, doc_fm, doc_fm)
    assert "datos reducidos" not in hdr_fm


# ═══════════════════════════════════════════════════════════════════════════════
# BLOQUE 5: Etiquetas (λ base tras Doble ADN, lambdas_finales_soloA) y Veredicto #1
# ═══════════════════════════════════════════════════════════════════════════════


def test_b5_etiquetas_lambda_base_adn_y_veredicto_under_35_y_attack_orden():
    from types import SimpleNamespace
    from evaluacion_pronosticos import extraer_lambdas_base_doble_adn_desde_analisis
    from motor_inferencia import generar_veredicto_tactico_partido

    # (a) Extraer lambdas_base_doble_adn desde motor.recursivo
    an_stub = SimpleNamespace(
        motor=SimpleNamespace(recursivo={"lambda_base_local": 1.33, "lambda_base_visita": 1.23})
    )
    lb = extraer_lambdas_base_doble_adn_desde_analisis(an_stub)
    assert lb == {"local_ft": 1.33, "visitante_ft": 1.23}

    # (b) y (c) Veredicto #1 con -3.5 goles al 71% y 2.71 goles esperados (Vitória vs RB Bragantino)
    ver = generar_veredicto_tactico_partido(
        nombre_local="Vitória",
        nombre_visita="RB Bragantino",
        probs={"local": 0.21, "empate": 0.25, "visita": 0.54, "lambda_local": 0.96, "lambda_visita": 1.75},
        ratings_loc={"Final_Attack_Strength": 17.1},
        ratings_vis={"Final_Attack_Strength": 39.0},
        patrones_loc={"posesion_pct": 46.0},
        patrones_vis={"posesion_pct": 54.0},
        cuadro_b_loc={"sin_derrotas": 7, "victorias": 4, "n_partidos": 15},
        cuadro_b_vis={"sin_derrotas": 11, "victorias": 8, "n_partidos": 15},
        cuadro_c_loc={"_meta": {"n_partidos_con_eventos": 15}},
        cuadro_c_vis={"_meta": {"n_partidos_con_eventos": 15}},
        top5_podio=[{"etiqueta": "Menos de 3.5 goles", "probabilidad": 0.71, "estado": "mantiene"}],
        para_canvas=True,
    )
    assert "Partido cerrado / Tendencia baja en goles" not in ver
    assert "Control de techo de goles" in ver
    assert "2.71 goles esperados" in ver
    assert "Attack 39.0 de RB Bragantino vs 17.1 de Vitória" in ver
