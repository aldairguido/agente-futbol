"""
tests/test_clv_y_calibracion_oos.py
───────────────────────────────────────────────────────────────────────────────
Pruebas unitarias offline para:
  1. Guarda anti-fuga pre-partido (rechazo de cuotas in-play AB=2 o liquidadas WON/LOST,
     y rechazo de capturado_en_utc >= hora_inicio_partido).
  2. Lectura de cierre archivado post-partido (`modo_cierre_archivado=True`) incluyendo
     bloques con `active=False` y etiquetado explícito de procedencia.
  3. Cálculo de CLV sin margen (`clv_devig_pct = p_cierre_devig * cuota_consulta - 1`)
     sin autoengaño por overround, y eliminación del ROI de muestra pequeña.
  4. Separación de calibración SOLO OOS válido y canónico en /evaluar.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evaluacion_pronosticos as ep
from fuente_flashscore import ClienteFlashscore


def _snapshot_oos_con_cuotas(
    id_trabajo: str = "oos_cuotas_01",
    local: str = "Bodø/Glimt",
    visitante: str = "LASK",
    es_oos: bool = True,
    capturado_en_utc: str = "2026-11-24T06:00:00Z",
) -> dict:
    return {
        "id_trabajo": id_trabajo,
        "partido": {"local": local, "visitante": visitante, "fecha": "2026-11-24"},
        "fixture_confirmado": es_oos,
        "hora_inicio_partido": "2026-11-24T17:45:00Z" if es_oos else None,
        "telegram_envio": {"message_id": 101, "message_date": 1795500000, "message_date_utc": "2026-11-24T06:00:00Z"} if es_oos else None,
        "es_oos_valido": es_oos,
        "es_canonica": es_oos,
        "estado_canonico": "canonica" if es_oos else "legacy_sin_fixture",
        "modo_sombra": {
            "comparaciones": [
                {"mercado": "Ganador del partido", "evento": "1", "periodo": "FT", "equipo": local, "linea": None, "prob_base": 0.68},
                {"mercado": "Ganador del partido", "evento": "X", "periodo": "FT", "equipo": None, "linea": None, "prob_base": 0.17},
                {"mercado": "Ganador del partido", "evento": "2", "periodo": "FT", "equipo": visitante, "linea": None, "prob_base": 0.15},
                {"mercado": "Total de goles / Número de goles", "evento": "over_2.5", "periodo": "FT", "equipo": None, "linea": 2.5, "prob_base": 0.72},
                {"mercado": "Total de goles / Número de goles", "evento": "under_2.5", "periodo": "FT", "equipo": None, "linea": 2.5, "prob_base": 0.28},
                {"mercado": "Ambos equipos anotan", "evento": "btts_si", "periodo": "FT", "equipo": None, "linea": None, "prob_base": 0.65},
                {"mercado": "Ambos equipos anotan", "evento": "btts_no", "periodo": "FT", "equipo": None, "linea": None, "prob_base": 0.35},
            ]
        },
        "cuotas_mercado": {
            "local": 1.65,
            "empate": 4.50,
            "visitante": 5.50,
            "casa": "Betsson",
            "fuente_cuotas": "flashscore_graphql",
            "id_flashscore_cuotas": "pItlFEBk",
            "home_participant_id": "b94Fok0s",
            "away_participant_id": "jHMMqTUg",
            "capturado_en_utc": capturado_en_utc,
            "mercados": {
                "local": 1.65,
                "empate": 4.50,
                "visitante": 5.50,
                "over_2.5": 1.50,
                "under_2.5": 2.60,
                "btts_si": 1.65,
                "btts_no": 2.25,
            },
        },
    }


def test_graphql_prepartido_rechaza_liquidados_y_modo_cierre_acepta_active_false():
    cli = ClienteFlashscore()
    cli._CACHE_FEED_DIARIO.clear()
    payload_cerrado = {
        "data": {
            "findOddsByEventId": {
                "eventId": "rwA1cHoQ",
                "settings": {"bookmakers": [{"bookmaker": {"id": 43, "name": "Betsson"}}]},
                "odds": [
                    {
                        "bookmakerId": 43,
                        "bettingType": "HOME_DRAW_AWAY",
                        "bettingScope": "FULL_TIME",
                        "odds": [
                            {"eventParticipantId": "WQv7amJa", "value": "5.25", "opening": "5.00", "active": False, "result": "LOST"},
                            {"eventParticipantId": "virFcRmC", "value": "1.62", "opening": "1.65", "active": False, "result": "LOST"},
                            {"eventParticipantId": None, "value": "3.45", "opening": "3.40", "active": False, "result": "WON"},
                        ],
                    }
                ],
            }
        }
    }

    def fake_get(url, **kwargs):
        if "pq_graphql" in url:
            return SimpleNamespace(status_code=200, text=json.dumps(payload_cerrado))
        return SimpleNamespace(status_code=200, text="")

    with patch.object(cli, "_get", side_effect=fake_get):
        # En modo pre-partido DEBE rechazarse porque ya tiene result WON/LOST y active=False
        res_pre = cli.extraer_cuotas("rwA1cHoQ", home_participant_id="WQv7amJa", away_participant_id="virFcRmC", modo_cierre_archivado=False)
        assert res_pre["local"] is None

        # En modo cierre archivado SÍ recupera el cierre pre-partido y declara origen_temporal_cuotas
        res_cie = cli.extraer_cuotas("rwA1cHoQ", home_participant_id="WQv7amJa", away_participant_id="virFcRmC", modo_cierre_archivado=True)
        assert res_cie["local"] == 5.25
        assert res_cie["empate"] == 3.45
        assert res_cie["visitante"] == 1.62
        assert res_cie["origen_temporal_cuotas"] == "archivo_postpartido_flashscore_oce"


def test_clv_devig_no_confunde_overround_con_equilibrio_y_rechaza_post_inicio():
    # Si consultas over_2.5 a 1.90 y cierra a 1.90 / 1.90 (p_cierre_devig = 0.50),
    # el movimiento bruto es 0.0%, pero el CLV real sin margen es 0.50 * 1.90 - 1 = -5.00%!
    snap = _snapshot_oos_con_cuotas()
    snap["cuotas_mercado"]["mercados"]["over_2.5"] = 1.90
    snap["cuotas_mercado"]["mercados"]["under_2.5"] = 1.90
    res = ep.ResultadoPartido(id_partido="6106317", estado="finalizado", local="Bodø/Glimt", visitante="LASK", goles_local=2, goles_visitante=1)
    cierre = {
        "local": 1.45,
        "empate": 5.00,
        "visitante": 6.20,
        "origen_temporal_cuotas": "archivo_postpartido_flashscore_oce",
        "mercados": {
            "local": 1.45,
            "empate": 5.00,
            "visitante": 6.20,
            "over_2.5": 1.90,
            "under_2.5": 1.90,
        },
    }
    met = ep.calcular_metricas_clv_y_mercado(snap, res, cuotas_cierre=cierre)
    assert met["disponible"] is True
    assert met["fuente_cierre"] == "archivo_postpartido_flashscore_oce"
    clv_o25 = next(x for x in met["clv_selecciones"] if x["seleccion"] == "over_2.5")
    assert clv_o25["movimiento_cuota_pct"] == 0.0
    assert abs(clv_o25["clv_devig_pct"] - (-5.00)) < 0.05

    # Si las cuotas se capturaron después del inicio, se rechazan estrictamente
    snap_post = _snapshot_oos_con_cuotas(capturado_en_utc="2026-11-24T18:05:00Z")
    met_post = ep.calcular_metricas_clv_y_mercado(snap_post, res, cuotas_cierre=cierre)
    assert met_post["disponible"] is False
    assert met_post["motivo"] == "cuotas_capturadas_post_inicio"


def test_resumen_mercado_excluye_legacy_y_no_muestra_roi_enganoso():
    snap_oos = _snapshot_oos_con_cuotas("oos_1", "Bodø/Glimt", "LASK", es_oos=True)
    snap_leg = _snapshot_oos_con_cuotas("leg_1", "Club X", "Club Y", es_oos=False)
    resultados = {
        "Bodø/Glimt vs LASK": ep.ResultadoPartido(id_partido="6106317", estado="finalizado", local="Bodø/Glimt", visitante="LASK", goles_local=2, goles_visitante=1),
        "Club X vs Club Y": ep.ResultadoPartido(id_partido="999", estado="finalizado", local="Club X", visitante="Club Y", goles_local=0, goles_visitante=3),
    }
    resumen = ep.agregar_resumen_mercado_y_clv(
        [snap_oos, snap_leg],
        resultados,
        cuotas_cierre_por_id={"oos_1": {"local": 1.45, "empate": 5.00, "visitante": 6.20, "origen_temporal_cuotas": "archivo_postpartido_flashscore_oce"}},
    )
    assert resumen["n_partidos_oos_con_cuotas"] == 1
    assert resumen["n_partidos_oos_con_clv"] == 1
    assert resumen["clv_devig_medio_pct"] is not None
    assert "roi_1u_ev_positivo_pct" not in resumen


def test_resumen_telegram_muestra_calibracion_solo_oos_primero_sin_roi_chico():
    ev_oos = ep.Evaluacion(
        id_trabajo="oos_1",
        partido="Bodø/Glimt vs LASK",
        id_partido="6106317",
        mercado="Total de goles / Número de goles",
        evento="over_1.5",
        etiqueta="más de 1.5 goles",
        periodo="FT",
        equipo=None,
        linea=1.5,
        probabilidad_original=0.82,
        probabilidad_origen="exacta",
        estado_sensibilidad="mantiene",
        estado_liquidacion="ganada",
        evento_ocurrio=1,
        objetivo_probabilidad="binario",
        detalle="3 goles",
        referencia="",
        clasificacion_oos="canonica",
        es_oos_valido=True,
        es_canonica=True,
    )
    ev_leg = ep.Evaluacion(
        id_trabajo="leg_1",
        partido="Club X vs Club Y",
        id_partido="999",
        mercado="Total de tarjetas",
        evento="amarillas_under_4.5",
        etiqueta="menos de 4.5 tarjetas",
        periodo="FT",
        equipo=None,
        linea=4.5,
        probabilidad_original=1.0,
        probabilidad_origen="exacta",
        estado_sensibilidad="mantiene",
        estado_liquidacion="perdida",
        evento_ocurrio=0,
        objetivo_probabilidad="binario",
        detalle="5 amarillas",
        referencia="",
        clasificacion_oos="legacy_sin_fixture",
        es_oos_valido=False,
        es_canonica=False,
    )
    inf = ep.InformeEvaluacion(
        generado_en="2026-11-24 08:00:00",
        partidos={},
        evaluaciones=[ev_oos, ev_leg],
        bandas=ep.calcular_bandas([ev_oos, ev_leg]),
        bandas_oos=ep.calcular_bandas([ev_oos]),
        acumulados=ep.calcular_acumulados([ev_oos, ev_leg]),
        por_familia=ep.calcular_por_familia([ev_oos, ev_leg]),
        por_periodo=ep.calcular_por_periodo([ev_oos, ev_leg]),
        por_sensibilidad=ep.calcular_por_sensibilidad([ev_oos, ev_leg]),
        por_equipo=ep.calcular_por_equipo([ev_oos, ev_leg]),
        por_equipo_condicion=ep.calcular_por_equipo_y_condicion([ev_oos, ev_leg]),
        por_localia=ep.calcular_por_localia([ev_oos, ev_leg]),
        calibracion=ep.tabla_calibracion([ev_oos, ev_leg]),
        brier=ep.brier_binario([ev_oos, ev_leg]),
        bruto_vs_equilibrado=ep.desglose_bruto_vs_equilibrado([ev_oos, ev_leg]),
        banda_100=ep.auditar_banda_100([ev_oos, ev_leg]),
        evidencia=ep.diagnostico_evidencia([ev_oos, ev_leg], partidos_oos_validos=1, partidos_legacy_sin_fixture=1, partidos_no_oos=0),
        faltantes=[],
        limitaciones=[],
        separacion_oos={
            "oos_valido_canonico": {"partidos_unicos": 1, "selecciones_evaluables": 1, "acierto_observado": 1.0, "brier": 0.0324},
            "legacy_sin_fixture": {"partidos_unicos": 1, "selecciones_evaluables": 1, "acierto_observado": 0.0, "brier": 1.0},
            "no_oos": {"partidos_unicos": 0, "selecciones_evaluables": 0, "acierto_observado": None, "brier": None},
        },
        resumen_mercado_y_clv={
            "n_partidos_oos_con_cuotas": 1,
            "n_partidos_oos_con_clv": 1,
            "casas": ["Betsson"],
            "mercados_primarios": {
                "1x2": {"n": 1, "brier_modelo": 0.1538, "brier_mercado_devig": 0.21, "delta_brier_vs_mercado": -0.0562, "skill_vs_mercado": 0.2676},
                "ou_2.5": {"n": 0},
                "btts": {"n": 0},
            },
            "clv_devig_medio_pct": 4.25,
        },
    )
    txt = ep.resumen_telegram(inf)
    assert txt.index("Calibración SOLO OOS válido y canónico") < txt.index("Histórico global mezclado")
    assert "CLV sin margen (archivo post-partido): +4.25%" in txt
    assert "ROI" not in txt
