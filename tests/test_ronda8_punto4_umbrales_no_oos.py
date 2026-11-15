"""
Tests reales para Ronda 8 — Punto 4:
Auditoría cronológica de los 4 umbrales no OOS (`8/15`, `10/15`, `-10 pp`, `factor NB 1.05`),
fijados en el commit `9593ba6` (`2026-11-13T04:16:01-05:00`), separación de partidos liquidados
antes vs después de esa fecha y reevaluación estrictamente posterior.
"""

from __future__ import annotations

import json
import os

from evaluacion_pronosticos import (
    CATALOGO_UMBRALES_NO_OOS,
    COMMIT_FIJACION_UMBRALES_BLOQUE3,
    FECHA_FIJACION_UMBRALES_BLOQUE3_ISO,
    auditar_umbrales_no_oos_por_fecha,
)


def test_catalogo_umbrales_no_oos_documenta_4_gates_en_9593ba6():
    assert set(CATALOGO_UMBRALES_NO_OOS.keys()) == {
        "gate_corners_8_15",
        "gate_mitades_10_15",
        "veto_conciencia_menos_10_pp",
        "factor_nb_105_corners",
    }
    for clave, meta in CATALOGO_UMBRALES_NO_OOS.items():
        assert meta["commit"] == "9593ba6", clave
        assert meta["commit_full"] == COMMIT_FIJACION_UMBRALES_BLOQUE3, clave
        assert meta["fecha_fijacion_iso"] == FECHA_FIJACION_UMBRALES_BLOQUE3_ISO, clave
        assert meta["rotulo_yaml"] == "regla operativa, sin validación cronológica", clave
        assert "Turkey 1-2 Belgium" in meta["datos_origen"] or "evaluacion_20261018_093622.json" in meta["datos_origen"]


def test_auditar_umbrales_no_oos_separa_24_partidos_octubre_antes_y_cero_despues():
    ruta_ev = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "informes",
        "evaluacion",
        "evaluacion_9393a7b9017a5365.json",
    )
    with open(ruta_ev, "r", encoding="utf-8") as f:
        ev = json.load(f)

    obs = []
    for idx, e in enumerate(ev.get("evaluaciones") or []):
        if e.get("evaluable_binario") and e.get("evento_ocurrio") in (0, 1):
            partido = str(e.get("partido") or "")
            prob = float(e.get("probabilidad", 0.5))
            obs.append({
                "id_partido": partido,
                "partido": partido,
                "familia": "corners_totales" if idx % 2 == 0 else "ganador_1x2",
                "evento_ocurrio": int(e["evento_ocurrio"]),
                "fecha_partido": e.get("fecha"),
                "publicado_en": e.get("publicado_en") or e.get("fecha"),
                "liquidado_en": ev.get("generado_en") or "2026-10-18T09:36:22",
                "probs": {"v5e84": prob, "soloA": prob, "AB": prob},
            })
    # Añadimos también los dos partidos de diseño del 2026-11-12 (que además son anteriores a 2026-11-13)
    obs.append({
        "id_partido": "Turkey vs Belgium",
        "partido": "Turkey vs Belgium",
        "familia": "corners_totales",
        "evento_ocurrio": 0,
        "fecha_partido": "2026-11-12",
        "liquidado_en": "2026-11-13T00:00:00",
        "probs": {"v5e84": 0.85, "soloA": 0.85, "AB": 0.72},
    })
    obs.append({
        "id_partido": "Italy vs France",
        "partido": "Italy vs France",
        "familia": "doble_oportunidad",
        "evento_ocurrio": 0,
        "fecha_partido": "2026-11-12",
        "liquidado_en": "2026-11-13T00:00:00",
        "probs": {"v5e84": 0.816, "soloA": 0.816, "AB": 0.533},
    })

    res = auditar_umbrales_no_oos_por_fecha(obs, n_boot=200, seed=42)
    assert res["antes_de_fijacion"]["n_partidos_unicos"] == 24
    assert res["antes_de_fijacion"]["n_filas"] == 2435
    assert res["despues_de_fijacion"]["estado"] == "muestra_posterior_vacia"
    assert res["despues_de_fijacion"]["n_partidos_unicos"] == 0
    assert res["despues_de_fijacion"]["n_filas"] == 0
    # None nunca es 0 cuando no hay muestra posterior
    assert res["despues_de_fijacion"]["comparacion"]["total"]["brier"] is None
    assert res["despues_de_fijacion"]["comparacion"]["total"]["log_loss"] is None


def test_auditar_umbrales_no_oos_evalua_subconjunto_posterior_cuando_existe():
    obs = [
        {
            "id_partido": "Match Pre 1",
            "partido": "Match Pre 1",
            "familia": "corners_totales",
            "evento_ocurrio": 1,
            "liquidado_en": "2026-10-18T09:36:22",
            "probs": {"soloA": 0.60, "AB": 0.65},
        },
        {
            "id_partido": "Match Post 1",
            "partido": "Match Post 1",
            "familia": "corners_totales",
            "evento_ocurrio": 0,
            "liquidado_en": "2026-11-14T18:00:00",
            "probs": {"soloA": 0.80, "AB": 0.70},
        },
        {
            "id_partido": "Match Post 2",
            "partido": "Match Post 2",
            "familia": "corners_totales",
            "evento_ocurrio": 1,
            "liquidado_en": "2026-11-14T20:00:00",
            "probs": {"soloA": 0.55, "AB": 0.62},
        },
    ]
    res = auditar_umbrales_no_oos_por_fecha(obs, n_boot=200, seed=42)
    assert res["antes_de_fijacion"]["n_partidos_unicos"] == 1
    assert res["despues_de_fijacion"]["n_partidos_unicos"] == 2
    tot_post = res["despues_de_fijacion"]["comparacion"]["total"]
    assert tot_post["versiones"]["AB"]["brier"] < tot_post["versiones"]["soloA"]["brier"]
