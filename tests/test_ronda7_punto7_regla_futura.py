# -*- coding: utf-8 -*-
"""
Ronda 7 - Punto 7: REGLA FUTURA de promocion de cambios de comportamiento.

Verifica deterministicamente:
  1. Politica configurada en umbrales_cobertura.yaml (min_partidos_modo_sombra=30).
  2. Modo sombra dual en el libro (SnapshotPronostico / informes/pronosticos/*.json
     y ledger_oos.jsonl con hash encadenado SHA256), guardando ambas versiones
     (`base` publicada y `sombra` candidata) sin alterar la salida oficial.
  3. Bloqueo formal de promocion ante:
       - bandera inexistente o activa por defecto,
       - regla ad-hoc codificada desde 1 solo partido o desde partidos de diseno,
       - registros sin ambas versiones en modo sombra,
       - menos de 30 partidos unicos no-diseno (ej. muestra actual OOS de 24 partidos),
       - ausencia de mejora simultanea en Brier y Log-Loss con IC95% por partido.
  4. Aprobacion solo cuando TODOS los requisitos se cumplen (>= 30 partidos no-diseno,
     bandera apagada por defecto, modo sombra dual completo, Brier y Log-Loss mejorados).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_cobertura as dc
import evaluacion_pronosticos as ep
import motor_mercados as mm


def _obs_sombra(n_partidos: int, *, p_base_pos=0.55, p_base_neg=0.45, p_somb_pos=0.75, p_somb_neg=0.25, incluir_diseno: bool = False):
    obs = []
    if incluir_diseno:
        obs.append({
            "id_partido": "5181796",
            "partido": "Turkey vs Belgium",
            "mercado": "Total de goles",
            "evento_ocurrio": 1,
            "probs": {"base": 0.10, "sombra": 0.99},
        })
        obs.append({
            "id_partido": "9999999",
            "partido": "Italia vs Francia",
            "mercado": "Total de goles",
            "evento_ocurrio": 1,
            "probs": {"base": 0.10, "sombra": 0.99},
        })
    for i in range(n_partidos):
        obs.append({
            "id_partido": f"oos_{i:03d}",
            "partido": f"ClubLocal_{i} vs ClubVisita_{i}",
            "mercado": "Total de goles",
            "evento_ocurrio": 1 if (i % 2 == 0) else 0,
            "probs": {
                "base": p_base_pos if (i % 2 == 0) else p_base_neg,
                "sombra": p_somb_pos if (i % 2 == 0) else p_somb_neg,
            },
        })
    return obs


def test_punto7_politica_y_banderas_regla_futura():
    pol = dc.obtener_politica_regla_futura()
    assert pol["min_partidos_modo_sombra"] == 30
    assert pol["exigir_datos_no_diseno"] is True
    assert pol["exigir_brier_y_logloss_con_ic_por_partido"] is True
    assert pol["exigir_bandera_apagada_por_defecto"] is True
    assert pol["exigir_modo_sombra_dual_en_libro"] is True
    assert pol["prohibir_regla_ad_hoc_por_partido"] is True

    # Ninguna bandera de comportamiento esta encendida por defecto
    for k, v in dc.BANDERAS_COMPORTAMIENTO_DEFECTO.items():
        assert v is False, f"{k} debe iniciar apagada"


def test_punto7_modo_sombra_dual_en_snapshot_y_ledger(tmp_path):
    """Guarda ambas versiones (base publicada y sombra candidata) en snapshot y ledger SHA256."""
    aud = ROOT / "informes" / "auditorias" / "2026-10-13"
    dl = json.loads((aud / "auditoria_galatasaray_2026-10-13_102501.json").read_text(encoding="utf-8"))
    dv = json.loads((aud / "auditoria_barcelona_2026-10-13_102522.json").read_text(encoding="utf-8"))
    dl_c = {"equipo": dl.get("equipo"), "partidos": [x for x in dl.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"]}
    dv_c = {"equipo": dv.get("equipo"), "partidos": [x for x in dv.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"]}

    cands = {"usar_tope_dcn_18_pct": True, "usar_anclaje_coef_torneo": True}
    with dc.banderas_comportamiento(activar_todas=False):
        an_of = mm.analizar(dl_c, dv_c, "Galatasaray", "Barcelona", fecha_partido="2026-10-13")

    preparado = {
        "id_trabajo": "SOMBRA_TEST_GAL_BAR",
        "analisis": an_of,
        "rutas": {"informe": "tmp_informe.md"},
        "contexto": {
            "doc_local": dl_c,
            "doc_visitante": dv_c,
            "equipo_local": "Galatasaray",
            "equipo_visitante": "Barcelona",
            "fecha_partido": "2026-10-13",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
        },
    }
    sombra = ep.adjuntar_modo_sombra_preparado(preparado, banderas_candidatas=cands)
    assert sombra is not None
    assert sombra["esquema"] == "sombra-v1"
    assert sombra["publicado"] == "soloA"
    assert sombra["retroalimenta_modelo"] is False
    assert sombra["n_selecciones_comparables"] >= 20
    # Version publicada no cambia
    assert preparado["analisis"] is an_of
    assert sombra["lambdas_base"]["local_ft"] == an_of.motor.tasas.lambda_local_ft
    assert sombra["lambdas_sombra"]["visitante_ft"] != sombra["lambdas_base"]["visitante_ft"] or sombra["lambdas_sombra"]["local_ft"] != sombra["lambdas_base"]["local_ft"]

    snap = ep.snapshot_desde_analisis(preparado)
    ruta_snap = ep.guardar_snapshot(snap, directorio=str(tmp_path / "pronosticos"))
    leido = json.loads(Path(ruta_snap).read_text(encoding="utf-8"))
    assert "modo_sombra" in leido
    assert leido["modo_sombra"]["lambdas_base"] == sombra["lambdas_base"]
    assert leido["modo_sombra"]["lambdas_sombra"] == sombra["lambdas_sombra"]
    assert leido["selecciones"][0]["probabilidad"] is not None

    # Ledger SHA256 append-only con ambas versiones
    ruta_led = str(tmp_path / "ledger_sombra.jsonl")
    r_led = ep.registrar_sombra_en_ledger_oos(
        {
            "id_partido": "6106338",
            "partido": "Galatasaray vs Barcelona",
            "fecha": "2026-10-13",
            "evento": "over_2.5",
            "probabilidad_base": sombra["comparaciones"][0]["prob_base"],
            "evento_ocurrio": 1,
        },
        probabilidad_base=sombra["comparaciones"][0]["prob_base"],
        probabilidad_sombra=sombra["comparaciones"][0]["prob_sombra"],
        nombre_bandera="usar_tope_dcn_18_pct",
        lambdas_base=sombra["lambdas_base"],
        lambdas_sombra=sombra["lambdas_sombra"],
        ruta_ledger=ruta_led,
    )
    assert r_led["registrado"] is True
    chk = ep.verificar_cadena_ledger_oos(ruta_led)
    assert chk["ok"] is True
    led_rows = ep.cargar_ledger_oos(ruta_led)
    assert led_rows[0]["modo_sombra"]["probabilidad_base"] is not None
    assert led_rows[0]["modo_sombra"]["probabilidad_sombra"] is not None
    assert led_rows[0]["modo_sombra"]["es_partido_diseno"] is False


def test_punto7_bloqueos_y_aprobacion_regla_futura():
    # 1. Bloqueo por < 30 partidos (ej. 24 partidos OOS) incluso con mejora fuerte y excluyendo TB/IF
    rep_24 = ep.evaluar_promocion_regla_futura(
        _obs_sombra(24, incluir_diseno=True),
        nombre_bandera="usar_tope_dcn_18_pct",
        n_boot=200,
    )
    assert rep_24["aprobada_para_activar"] is False
    assert rep_24["n_partidos_unicos"] == 24
    assert rep_24["n_excluidos_diseno"] >= 2
    assert any("muestra_sombra_insuficiente:24<30" in r for r in rep_24["razones_bloqueo"])

    # 2. Bloqueo por intentar codificar una regla a partir de 1 solo partido o ad-hoc
    rep_1 = ep.evaluar_promocion_regla_futura(
        _obs_sombra(1),
        nombre_bandera="usar_micropatrones_combinatorios",
        nombre_regla_propuesta="regla_ad_hoc_italia_francia",
        n_boot=100,
    )
    assert rep_1["aprobada_para_activar"] is False
    assert "prohibido_codificar_regla_de_un_solo_partido" in rep_1["razones_bloqueo"]
    assert any("regla_adhoc_prohibida" in r for r in rep_1["razones_bloqueo"])

    # 3. Bloqueo por bandera inexistente o por falta de version dual en el libro
    obs_sin_sombra = [
        {"id_partido": f"x_{i}", "partido": f"A{i} vs B{i}", "mercado": "Total de goles", "evento_ocurrio": 1, "probs": {"base": 0.6}}
        for i in range(30)
    ]
    rep_no_sombra = ep.evaluar_promocion_regla_futura(
        obs_sin_sombra,
        nombre_bandera="bandera_inventada",
        n_boot=100,
    )
    assert rep_no_sombra["aprobada_para_activar"] is False
    assert "bandera_no_declarada:bandera_inventada" in rep_no_sombra["razones_bloqueo"]
    assert any("registros_sin_modo_sombra_dual" in r for r in rep_no_sombra["razones_bloqueo"])

    # 4. Bloqueo cuando >= 30 partidos pero sombra empeora Brier y Log-Loss
    rep_empeora = ep.evaluar_promocion_regla_futura(
        _obs_sombra(32, p_base_pos=0.75, p_base_neg=0.25, p_somb_pos=0.52, p_somb_neg=0.48),
        nombre_bandera="usar_tope_dcn_18_pct",
        n_boot=200,
    )
    assert rep_empeora["aprobada_para_activar"] is False
    assert rep_empeora["n_partidos_unicos"] == 32
    assert any("brier_no_mejora" in r for r in rep_empeora["razones_bloqueo"])
    assert any("log_loss_no_mejora" in r for r in rep_empeora["razones_bloqueo"])

    # 5. Aprobacion estricta cuando cumple TODOS los criterios (>= 30 no-diseno, dual, bandera apagada, mejora Brier + Log-Loss + IC95)
    rep_ok = ep.evaluar_promocion_regla_futura(
        _obs_sombra(35, p_base_pos=0.55, p_base_neg=0.45, p_somb_pos=0.80, p_somb_neg=0.20, incluir_diseno=True),
        nombre_bandera="usar_tope_dcn_18_pct",
        n_boot=300,
        exigir_ic95_estricto=True,
    )
    assert rep_ok["aprobada_para_activar"] is True
    assert rep_ok["politica_estado"] == "aprobada"
    assert rep_ok["razones_bloqueo"] == []
    assert rep_ok["n_partidos_unicos"] == 35
    assert rep_ok["d_brier"] < 0.0 and rep_ok["d_brier_ic95"][1] < 0.0
    assert rep_ok["d_log_loss"] < 0.0 and rep_ok["d_log_loss_ic95"][1] < 0.0
