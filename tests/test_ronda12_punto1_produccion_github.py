"""Tests para ORDEN RONDA 12 — Punto 1:
Auditoría de producción en GitHub (solo lectura), persistencia de pronóstico/ledger,
comparación SHA-256 origin/main vs local, explicación X2 Italy–France (84.1% vs 81.6%),
marcado `contaminado_por_diseno` en TB e IF, resumen sin líneas >=99%, y verificación
de si producción usa el camino pre_partido/liquidado de `bot_interactivo.py`."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import evaluacion_pronosticos as ep


def test_ronda12_punto1_produccion_github_cadena_y_resumen_sin_99():
    ruta_json = Path(ROOT) / "datos" / "ronda12_punto1_produccion_github.json"
    assert ruta_json.exists()
    guardado = json.loads(ruta_json.read_text(encoding="utf-8"))

    # Ejecutar offline determinista sin sobrescribir JSON
    res = ep.auditar_produccion_github_ronda12(
        consultar_github_en_vivo=False,
        guardar_ruta=None,
    )

    # 1. Inventario bot_24_7.yml desde 2026-11-13
    inv = res["inventario_corridas_bot_24_7"]
    assert inv["n_corridas_desde_2026_11_13"] == 37
    assert sum(inv["conteo_por_estado"].values()) == 37
    assert inv["conteo_por_head_sha"].get("fbb6dc0") == 30
    assert inv["conteo_por_head_sha"].get("5e8425f") == 7
    assert guardado["inventario_corridas_bot_24_7"]["origen_datos"] == "api_publica_github"

    # 2. Cero persistencia de pronósticos y ledger en GitHub Actions
    pers = res["persistencia_github"]
    assert pers["permissions_contents"] == "read"
    assert pers["tiene_upload_artifact"] is False
    assert pers["tiene_git_push_o_commit"] is False
    assert pers["artifacts"]["total_count"] == 0
    assert pers["caches"]["total_count"] == 0

    # 3. Cadena SHA-256 origin/main vs local (Colombia-Chile enlaza con a5498f8f...)
    cad = res["comparacion_cadena_sha256_remota_vs_local"]
    assert cad["n_entradas_remoto_origin_main"] == 2
    assert cad["n_entradas_local"] == 3
    assert cad["verificacion_local"]["ok"] is True
    assert cad["if_remoto_sha256"] == "a5498f8f0df84a1933b2e07e538e29f408d82524d0d55682b5cd54cc3e003828"
    assert cad["colombia_chile_prev_sha256"] == cad["if_remoto_sha256"]
    assert cad["colombia_chile_enlaza_exactamente_con_a5498f8f"] is True
    assert cad["entradas"][0]["identico_byte_a_byte"] is True
    assert cad["entradas"][1]["identico_byte_a_byte"] is True

    # 4. Explicación Italy-France X2: 84.1% (5e8425f/soloA fixture) vs 81.6% (PNG-IF-1211)
    if_x2 = res["explicacion_italy_france_x2_841_vs_816"]
    assert abs(if_x2["probabilidad_ledger_lineas"] - 0.8410462427992359) < 1e-12
    assert if_x2["probabilidad_png_1211"] == 0.816
    assert if_x2["reconstruccion_5e8425f_soloA"]["lambdas"] == [1.03, 2.33]
    assert abs(if_x2["reconstruccion_5e8425f_soloA"]["p_x2"] - 0.841046) < 1e-6
    assert abs(if_x2["reconstruccion_AB_14_banderas"]["p_x2"] - 0.712566) < 1e-5
    assert if_x2["snaps_pronosticos_con_italy_france"] == 0

    # 5. Marcado contaminado_por_diseno y resumen sin líneas >=99%
    led = res["ledger_contaminado_por_diseno_y_resumen_sin_99_pct"]
    assert led["n_contaminados_por_diseno"] == 2
    et = {x["id_partido"]: x for x in led["registros_etiquetados"]}
    assert et["5181796"]["contaminado_por_diseno"] is True
    assert et["5181797"]["contaminado_por_diseno"] is True
    assert et["6539346"]["contaminado_por_diseno"] is False

    todas = led["resumen_todas_lineas_con_diseno"]
    assert todas["n_lineas_evaluables"] == 102
    assert todas["aciertos_totales"] == 89
    assert abs(todas["brier_medio_equilibrado"] - 0.109642) < 1e-5

    sin99 = led["resumen_sin_lineas_99_pct"]
    assert sin99["umbral_excl_prob_alta"] == 0.99
    assert sin99["n_lineas_excluidas_prob_alta"] == 11
    assert sin99["n_lineas_evaluables"] == 91
    assert sin99["aciertos_totales"] == 78
    assert abs(sin99["tasa_acierto_global"] - (78 / 91)) < 1e-5
    assert abs(sin99["brier_medio_equilibrado"] - 0.122385) < 1e-5

    # None nunca es 0 al excluir partidos de diseño y amistoso
    sin_dis = led["resumen_sin_diseno_ni_amistosos"]
    assert sin_dis["n_incluidos"] == 0
    assert sin_dis["n_excluidos_diseno"] == 2
    assert sin_dis["n_excluidos_amistosos"] == 1
    assert sin_dis["tasa_acierto_global"] is None
    assert sin_dis["acierto_medio_equilibrado"] is None
    assert sin_dis["brier_medio_equilibrado"] is None
    assert sin_dis["log_loss_medio_equilibrado"] is None

    # 6. Producción (origin/main fbb6dc0) NO ejecuta el camino pre_partido/liquidado
    prod = res["ruta_produccion_bot_interactivo"]
    assert prod["origin_main_y_local_main_iguales"] is True
    assert prod["produccion_usa_pre_partido_y_liquidado_bot_interactivo"] is False
    assert prod["simbolos_en_origin_main_bot_interactivo"]["registrar_sombra_prepartido_en_ledger_oos"] is False
    assert prod["simbolos_en_origin_main_evaluacion"]["liquidar_sombras_en_ledger_oos"] is False
