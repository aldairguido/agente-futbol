"""Tests para ORDEN RONDA 12 — Punto 3 (`REGLAS QUE PESAN`):
Tabla de cada regla que modifica λ sobre los 73 registros únicos (techos, pisos,
clamp, copa/asedio/defensa rota, sincronización, doble ×1.35 y
`techo_lambda_ataque_bajo_local`) en `soloA` y `A+B`."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import evaluacion_pronosticos as ep


def test_ronda12_punto3_tabla_reglas_que_pesan_73_registros():
    ruta_json = Path(ROOT) / "datos" / "ronda12_punto3_reglas_que_pesan.json"
    assert ruta_json.exists()
    guardado = json.loads(ruta_json.read_text(encoding="utf-8"))

    res = ep.auditar_reglas_que_pesan_ronda12(guardar_ruta=None)
    assert res == guardado
    assert res["n_registros_unicos"] == 73

    for variante, n_techo_act, n_techo_part in (("soloA", 30, 19), ("A+B", 32, 21)):
        v = res[variante]
        assert v["n_registros"] == 73
        por_nombre = {row["regla"]: row for row in v["tabla_reglas"]}

        # Todas las categorías obligatorias están cubiertas
        cats = set(v["resumen_por_categoria"].keys())
        assert {"techos_y_frenos", "pisos", "clamp", "copa_asedio_defensa_rota", "sincronizacion", "doble_x135"}.issubset(cats)

        # techo_lambda_ataque_bajo_local presente con cifras exactas
        tb = v["techo_lambda_ataque_bajo_local"]
        assert tb is not None
        assert tb["regla"] == "techo_lambda_ataque_bajo_local"
        assert tb["n_activaciones"] == n_techo_act
        assert tb["n_partidos_unicos"] == n_techo_part
        assert tb["d_lam_l_medio_por_partido_activo"] < -0.70
        assert tb["d_lam_v_medio_por_partido_activo"] == 0.0

        # doble ×1.35 defensa rota explícitamente medido (6 partidos únicos)
        d135 = v["doble_x135_defensa_rota_2da_aplicacion"]
        assert d135["n_partidos_unicos"] == 6
        assert d135["n_activaciones"] == 6
        assert d135["d_lam_total_medio_sobre_73"] > 0.0

        # Reglas clave presentes
        for req in (
            "doble_adn_60_40",
            "red_recursiva_45_dcn_bucle",
            "copa_asedio_defensa_rota",
            "sincronizacion_55_patrones_racha_contexto",
            "clamping_rango_y_redondeo",
            "piso_doble_castigo_punto50_local",
            "techo_caja_azul_visita_26",
        ):
            assert req in por_nombre, f"Falta {req} en {variante}"

        if variante == "A+B":
            assert "tope_dcn_18_post_sinc" in por_nombre
            assert por_nombre["tope_dcn_18_post_sinc"]["n_partidos_unicos"] == 21
