"""Tests de verificación Ronda 18 (fix/ronda-18-tickets)."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import cuadro_especialista as ce
import motor_inferencia as mi
import motor_patrones as mp


def _partido_flashscore_sin_minutos(idx: int, gf1: int = 1, gf2: int = 1, gc1: int = 0, gc2: int = 1, con_mitad: bool = True) -> dict:
    periodos = {
        "FT": {"filas": [{"clave": "xg", "equipo": 1.1, "rival": 0.9} for _ in range(12)]},
    }
    if con_mitad:
        periodos["1T"] = {
            "filas": [
                {"clave": "xgot_enfrentado", "equipo": float(gc1), "rival": float(gf1)},
                {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
            ]
        }
        periodos["2T"] = {
            "filas": [
                {"clave": "xgot_enfrentado", "equipo": float(gc2), "rival": float(gf2)},
                {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
            ]
        }
    return {
        "id_partido": f"fs_{idx}",
        "fecha": f"2026-10-{min(28, 1 + idx):02d}",
        "torneo": "BRAZIL: Serie A",
        "rival": f"RivalFS{idx}",
        "condicion": "casa" if idx % 2 == 0 else "fuera",
        "marcador": {"equipo": gf1 + gf2, "rival": gc1 + gc2},
        "goles_equipo": gf1 + gf2,
        "goles_rival": gc1 + gc2,
        "resumen_claves": 20,
        "periodos": periodos,
    }


def _partido_fotmob_con_minutos(idx: int, minuto: int = 23) -> dict:
    return {
        "id_partido": f"fm_{idx}",
        "fecha": f"2026-10-{min(28, 1 + idx):02d}",
        "torneo": "Série A",
        "rival": f"RivalFM{idx}",
        "condicion": "fuera" if idx % 2 == 0 else "casa",
        "marcador": {"equipo": 1, "rival": 0},
        "goles_equipo": 1,
        "goles_rival": 0,
        "resumen_claves": 25,
        "eventos_gol": [{"minuto": minuto, "minuto_str": f"{minuto}'", "lado": "equipo"}],
        "periodos": {"FT": {"filas": [{"clave": "xg", "equipo": 1.3, "rival": 0.7} for _ in range(12)]}},
    }


def test_bloque_a_sin_minutos_franjas_none_y_suprime_patron35_alerta_veredicto() -> None:
    partidos_coritiba = [_partido_flashscore_sin_minutos(i, 1, 0, 0, 1, con_mitad=(i < 11)) for i in range(15)]
    partidos_botafogo = [_partido_fotmob_con_minutos(i, minuto=23 if i < 10 else 65) for i in range(15)]

    cc_cor = mi.calcular_cuadro_c_timeline_15min(partidos_coritiba, "Coritiba")
    assert cc_cor["sin_dato"] is True
    assert cc_cor["_meta"]["sin_dato"] is True
    assert cc_cor["_meta"]["n_partidos_con_eventos"] == 0
    assert cc_cor["total_anotados"] is None
    assert cc_cor["total_recibidos"] is None
    for fr_lbl in ("00'-15'", "16'-30'", "31'-45'", "46'-60'", "61'-75'", "76'-90'"):
        assert cc_cor["anotados"][fr_lbl] is None
        assert cc_cor["recibidos"][fr_lbl] is None

    cc_bot = mi.calcular_cuadro_c_timeline_15min(partidos_botafogo, "Botafogo")
    assert cc_bot["sin_dato"] is False
    assert cc_bot["_meta"]["n_partidos_con_eventos"] == 15
    assert cc_bot["anotados"]["16'-30'"] == 10
    assert cc_bot["anotados"]["61'-75'"] == 5
    assert cc_bot["total_anotados"] == 15

    pat_cor = mp.calcular_55_patrones_equipo({"xg": 1.1, "posesion_pct": 50.0, "faltas": 12.0}, timeline_goles=cc_cor)
    pat_bot = mp.calcular_55_patrones_equipo({"xg": 1.2, "posesion_pct": 52.0, "faltas": 11.0}, timeline_goles=cc_bot)
    assert pat_cor.get("P35_Peso_Goles_Tardios") is None

    _, _, _, alertas = mi.aplicar_reglas_matchup_55(pat_cor, pat_bot, 0.35, 0.30, 0.35, "Coritiba", "Botafogo")
    assert not any("Patrón #35" in a or "76'-90'" in a for a in alertas)

    rep_tel = mi.generar_reporte_xai_telegram(
        "Coritiba", "Botafogo", pat_cor, pat_bot, {"local": 0.35, "empate": 0.30, "visita": 0.35},
        alertas, {}, {}, cc_cor, cc_bot,
    )
    assert "Patrón #35" not in rep_tel
    assert "76'-90'" not in rep_tel

    ver = mi.generar_veredicto_tactico_partido(
        "Coritiba", "Botafogo", pat_cor, pat_bot, {"local": 0.35, "empate": 0.30, "visita": 0.35},
        {}, {}, cc_cor, cc_bot,
    )
    assert "76'-90'" not in ver
    assert "Cobertura de minutos insuficiente" in ver

    doc_l = {"equipo": {"nombre": "Coritiba"}, "partidos": partidos_coritiba}
    doc_v = {"equipo": {"nombre": "Botafogo"}, "partidos": partidos_botafogo}
    analisis = MagicMock()
    analisis.doc_local = doc_l
    analisis.doc_visitante = doc_v
    analisis.nombre_local = "Coritiba"
    analisis.nombre_visitante = "Botafogo"
    analisis.grupos = {}
    analisis.selecciones = []
    analisis.mercados = []
    analisis.motor = None

    textos = []
    with patch("matplotlib.axes.Axes.text") as mock_text:
        with tempfile.TemporaryDirectory() as td:
            ce.generar_cuadro_auditoria_15_partidos_png(analisis, doc_l, doc_v, destino=os.path.join(td, "aud.png"))
            for c in mock_text.call_args_list:
                if len(c[0]) >= 3 and isinstance(c[0][2], str):
                    textos.append(c[0][2])

    banner_cron = [t for t in textos if "CRONOLOGÍA" in t]
    assert banner_cron
    assert all("ALERTA CRONOLÓGICA" not in t for t in textos)
    assert all("76'-90'" not in t for t in textos if t != "76'-90'" and "TIMELINE DE GOLES" not in t)
    assert "sin cobertura de minutos" in banner_cron[0]
    assert "—" in textos
    assert "15" in textos and "10" in textos and "5" in textos
