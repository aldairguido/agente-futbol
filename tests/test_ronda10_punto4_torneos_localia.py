"""
Tests para Ronda 10 — Punto 4:
Clasificación de las 30 matrices (Colombia + Chile) en `amistoso` (FIFA Series M29/M30),
`torneo_sede_neutral` y `sede_real`, conteo de "Local" en sede neutral y tabla GF/GC.
"""

from __future__ import annotations

import pytest

from evaluacion_pronosticos import clasificar_torneos_y_localia_30_matrices_ronda10


def test_clasificar_torneos_y_localia_30_matrices_ronda10() -> None:
    r = clasificar_torneos_y_localia_30_matrices_ronda10()

    assert r["n_matrices"] == 30
    pts = r["partidos_clasificados"]
    assert len(pts) == 30

    # FIFA Series (partidos 29 y 30) cuentan como amistoso aunque el caché tenga es_amistoso=False
    m29, m30 = pts[28], pts[29]
    assert m29["idx_matriz"] == 29 and m29["clasificacion"] == "amistoso"
    assert m30["idx_matriz"] == 30 and m30["clasificacion"] == "amistoso"

    # Ningún partido etiquetado "Local" fue jugado en sede real
    loc = r["resumen_locales"]
    assert loc["colombia_local_total"] == 7
    assert loc["colombia_local_sede_neutral"] == 7
    assert loc["colombia_local_sede_real"] == 0

    assert loc["chile_local_total"] == 6
    assert loc["chile_local_sede_neutral"] == 5
    assert loc["chile_local_amistoso_fifa_series"] == 1
    assert loc["chile_local_sede_real"] == 0

    assert loc["total_etiqueta_local"] == 13
    assert loc["total_local_en_torneo_sede_neutral"] == 12
    assert loc["total_local_en_sede_real"] == 0

    # Tabla GF/GC por tipo verificable
    t = r["tabla_gf_gc_por_tipo"]
    assert t["amistoso"]["n_partidos"] == 2
    assert t["amistoso"]["gf_total"] == 5
    assert t["amistoso"]["gc_total"] == 6
    assert t["torneo_sede_neutral"]["n_partidos"] == 26
    assert t["sede_real"]["n_partidos"] == 2
    assert t["sede_real"]["gf_prom"] == pytest.approx(0.5, abs=1e-4)
    assert t["sede_real"]["gc_prom"] == pytest.approx(1.5, abs=1e-4)
