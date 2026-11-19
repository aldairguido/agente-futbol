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
