"""
tests/test_clv_y_calibracion_oos.py
───────────────────────────────────────────────────────────────────────────────
Pruebas unitarias offline para:
  1. Baseline de mercado sin margen (de-vig) + CLV real + EV/ROI 1u plana (SOLO OOS).
  2. Separación explícita de la calibración SOLO OOS válido y canónico vs histórico
     global mezclado en /evaluar (`bandas_oos`, `resumen_telegram`, Markdown).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evaluacion_pronosticos as ep


def _snapshot_oos_con_cuotas(
    id_trabajo: str = "oos_cuotas_01",
    local: str = "Bodø/Glimt",
    visitante: str = "LASK",
    es_oos: bool = True,
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
            "local": 1.60,
            "empate": 4.50,
            "visitante": 5.50,
            "casa": "Betsson",
            "fuente_cuotas": "flashscore_graphql",
            "capturado_en_utc": "2026-11-24T06:00:00Z",
            "mercados": {
                "local": 1.60,
                "empate": 4.50,
                "visitante": 5.50,
                "over_2.5": 1.50,
                "under_2.5": 2.60,
                "btts_si": 1.65,
                "btts_no": 2.25,
            },
        },
    }


def test_calcular_metricas_clv_y_mercado_exacto():
    snap = _snapshot_oos_con_cuotas()
    res = ep.ResultadoPartido(
        id_partido="6106317",
        estado="finalizado",
        local="Bodø/Glimt",
        visitante="LASK",
        goles_local=2,
        goles_visitante=1,
    )
    cuotas_cierre = {
        "local": 1.45,
        "empate": 5.00,
        "visitante": 6.20,
        "mercados": {
            "local": 1.45,
            "empate": 5.00,
            "visitante": 6.20,
            "over_2.5": 1.40,
            "under_2.5": 2.90,
            "btts_si": 1.55,
            "btts_no": 2.40,
        },
    }

    met = ep.calcular_metricas_clv_y_mercado(snap, res, cuotas_cierre=cuotas_cierre)
    assert met["disponible"] is True
    assert met["casa"] == "Betsson"
    assert set(met["mercados"].keys()) == {"1x2", "ou_2.5", "btts"}

    # Brier 1X2 = suma de 3 clases (0.68-1)^2 + 0.17^2 + 0.15^2 = 0.1024 + 0.0289 + 0.0225 = 0.1538
    assert abs(met["mercados"]["1x2"]["brier_modelo"] - 0.1538) < 1e-5
    # Modelo dio 0.68 al ganador vs mercado ~0.607 -> modelo vence al mercado en 1X2 (delta < 0)
    assert met["mercados"]["1x2"]["delta_brier_vs_mercado"] < 0.0
    assert met["mercados"]["1x2"]["skill_vs_mercado"] > 0.0

    # CLV en "1": 1.60 / 1.45 - 1 = +10.34%
    clv_1 = next(x for x in met["clv_selecciones"] if x["seleccion"] == "1")
    assert abs(clv_1["clv_pct"] - 10.34) < 0.05

    # EV+ apuestas: 1 (0.68*1.60=1.088 > 1 -> ganó +0.60u), over_2.5 (0.72*1.50=1.08 > 1 -> ganó +0.50u), btts_si (0.65*1.65=1.0725 > 1 -> ganó +0.65u)
    sels_ev = {x["seleccion"]: x for x in met["apuestas_ev_positivo"]}
    assert "1" in sels_ev and sels_ev["1"]["gano"] is True
    assert "over_2.5" in sels_ev and sels_ev["over_2.5"]["gano"] is True
    assert "btts_si" in sels_ev and sels_ev["btts_si"]["gano"] is True


def test_calcular_metricas_sin_cuotas_no_alucina():
    snap_sin = _snapshot_oos_con_cuotas()
    snap_sin.pop("cuotas_mercado")
    res = ep.ResultadoPartido(id_partido="1", estado="finalizado", local="A", visitante="B", goles_local=1, goles_visitante=0)
    met = ep.calcular_metricas_clv_y_mercado(snap_sin, res)
    assert met["disponible"] is False


def test_agregar_resumen_mercado_y_clv_excluye_legacy():
    snap_oos = _snapshot_oos_con_cuotas("oos_1", "Bodø/Glimt", "LASK", es_oos=True)
    snap_leg = _snapshot_oos_con_cuotas("leg_1", "Club X", "Club Y", es_oos=False)
    resultados = {
        "Bodø/Glimt vs LASK": ep.ResultadoPartido(id_partido="6106317", estado="finalizado", local="Bodø/Glimt", visitante="LASK", goles_local=2, goles_visitante=1),
        "Club X vs Club Y": ep.ResultadoPartido(id_partido="999", estado="finalizado", local="Club X", visitante="Club Y", goles_local=0, goles_visitante=3),
    }
    resumen = ep.agregar_resumen_mercado_y_clv(
        [snap_oos, snap_leg],
        resultados,
        cuotas_cierre_por_id={"oos_1": {"local": 1.45, "empate": 5.00, "visitante": 6.20}},
    )
    assert resumen["n_partidos_oos_con_cuotas"] == 1
    assert resumen["n_partidos_oos_con_clv"] == 1
    assert resumen["mercados_primarios"]["1x2"]["n"] == 1
    assert resumen["clv_medio_pct"] is not None
    assert resumen["roi_1u_ev_positivo_pct"] is not None


def test_resumen_telegram_muestra_calibracion_solo_oos_primero_y_etiqueta_mezclado():
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
            "clv_medio_pct": 10.34,
            "n_selecciones_ev_positivo": 3,
            "roi_1u_ev_positivo_pct": 58.33,
        },
    )
    txt = ep.resumen_telegram(inf)
    idx_oos = txt.index("Calibración SOLO OOS válido y canónico")
    idx_mix = txt.index("Histórico global mezclado")
    assert idx_oos < idx_mix
    assert "Baseline de Mercado sin margen" in txt
    assert "CLV medio real: +10.34%" in txt
    assert "ROI +58.33%" in txt
