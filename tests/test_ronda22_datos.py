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
