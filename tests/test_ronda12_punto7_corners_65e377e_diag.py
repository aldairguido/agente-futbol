"""Tests para ORDEN RONDA 12 - Punto 7 (diagnostico in-sample corneres 65e377e + NB tras bandera)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from decision_cobertura import bandera_activa, banderas_comportamiento
import evaluacion_pronosticos as ep


def test_ronda12_p7_bandera_y_diagnostico_insample_613():
    assert bandera_activa("usar_corners_reglas_65e377e_diag") is False
    # activar_todas=True no debe encender la bandera diagnostica
    with banderas_comportamiento(activar_todas=True):
        assert bandera_activa("usar_corners_reglas_65e377e_diag") is False

    rep = ep.auditar_corners_65e377e_diag_ronda12(n_boot=2000, seed=42, guardar_ruta=None)
    assert rep["bandera_usar_corners_reglas_65e377e_diag_defecto"] is False
    assert rep["declaracion_in_sample"] is True
    assert "IN-SAMPLE" in rep["advertencia_metodologica"]

    ver = rep["verificacion_reconstruccion"]
    assert ver["n_filas_corners"] == 613
    assert ver["n_jobs_corners"] == 20
    assert ver["n_partidos_unicos"] == 18
    assert ver["mismatches_soloA_vs_1376"] == 0
    assert ver["mismatches_AB_vs_1376"] == 0

    vers = rep["metricas_613_filas"]["versiones"]
    assert abs(vers["arch"]["log_loss"] - 0.783772) < 1e-5
    assert abs(vers["soloA"]["log_loss"] - 0.831129) < 1e-5
    assert abs(vers["A+B"]["log_loss"] - 0.706322) < 1e-5
    assert abs(vers["65e377e_poisson_soloA"]["log_loss"] - 0.736805) < 1e-4
    assert abs(vers["65e377e_nb_soloA"]["log_loss"] - 0.642299) < 1e-4
    assert abs(vers["65e377e_nb_soloA"]["brier"] - 0.203433) < 1e-4

    diffs = rep["metricas_613_filas"]["diferencias"]
    d_arch = diffs["65e377e_nb_soloA - arch"]
    assert d_arch["d_log_loss"] < 0
    assert d_arch["d_log_loss_ic95"][1] < 0
    assert d_arch["d_brier"] < 0
    assert d_arch["d_brier_ic95"][1] < 0

    d_ab = diffs["65e377e_nb_soloA - A+B"]
    assert d_ab["d_log_loss"] < 0
    assert d_ab["d_log_loss_ic95"][1] < 0

    art = json.loads((Path(ROOT) / "datos" / "ronda12_punto7_corners_65e377e_diag.json").read_text(encoding="utf-8"))
    assert art["declaracion_in_sample"] is True
    assert art["verificacion_reconstruccion"]["n_filas_corners"] == 613
