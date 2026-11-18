"""Tests para ORDEN RONDA 12 - Punto 6 (aciertos reales de arreglos de frecuencia en 24 OOS)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import evaluacion_pronosticos as ep


def test_ronda12_p6_aciertos_reales_frecuencias_24oos():
    rep = ep.auditar_impacto_real_frecuencias_24oos_ronda12(n_boot=2000, seed=42, guardar_ruta=None)
    assert rep["cohorte"]["n_jobs_oos"] == 26
    assert rep["cohorte"]["n_partidos_unicos"] == 24

    # Cero lineas sin resolver en todas las comparativas
    for modo in ("soloA", "AB_con_banderas"):
        for cmp_k in ("pre_vs_ambos_arreglos", "pre_vs_solo_al_menos", "pre_vs_solo_periodo_1t"):
            for sec in ("top5", "sec1"):
                sub = rep[modo][cmp_k][sec]
                assert sub["n_no_resolubles_anadidas"] == 0
                assert sub["n_no_resolubles_quitadas"] == 0

    # soloA Top 5 (pre vs ambos arreglos)
    t5 = rep["soloA"]["pre_vs_ambos_arreglos"]["top5"]["sobre_24_partidos_unicos"]
    assert t5["anadidas"]["n_lineas"] == 12
    assert t5["anadidas"]["aciertos"] == 8
    assert t5["anadidas"]["tasa_acierto"] == 0.6667
    assert t5["quitadas"]["n_lineas"] == 5
    assert t5["quitadas"]["aciertos"] == 3
    assert t5["quitadas"]["tasa_acierto"] == 0.6

    # soloA Seccion 1 (pre vs ambos arreglos) sobre 24 unicos y 26 jobs
    s1_24 = rep["soloA"]["pre_vs_ambos_arreglos"]["sec1"]["sobre_24_partidos_unicos"]
    assert s1_24["anadidas"]["n_lineas"] == 112
    assert s1_24["anadidas"]["aciertos"] == 80
    assert s1_24["anadidas"]["tasa_acierto"] == 0.7143
    assert s1_24["quitadas"]["n_lineas"] == 17
    assert s1_24["quitadas"]["aciertos"] == 11
    assert s1_24["quitadas"]["tasa_acierto"] == 0.6471
    assert len(s1_24["diferencia"]["ic95_diff_tasa"]) == 2

    s1_26 = rep["soloA"]["pre_vs_ambos_arreglos"]["sec1"]["sobre_26_jobs_ic_por_partido_24"]
    assert s1_26["anadidas"]["n_lineas"] == 116
    assert s1_26["anadidas"]["aciertos"] == 84

    # A+B Top 5 y Seccion 1
    ab_t5 = rep["AB_con_banderas"]["pre_vs_ambos_arreglos"]["top5"]["sobre_24_partidos_unicos"]
    assert ab_t5["anadidas"]["n_lineas"] == 2
    assert ab_t5["anadidas"]["aciertos"] == 1
    assert ab_t5["quitadas"]["n_lineas"] == 1
    assert ab_t5["quitadas"]["aciertos"] == 0

    ab_s1 = rep["AB_con_banderas"]["pre_vs_ambos_arreglos"]["sec1"]["sobre_24_partidos_unicos"]
    assert ab_s1["anadidas"]["n_lineas"] == 44
    assert ab_s1["anadidas"]["aciertos"] == 28
    assert ab_s1["quitadas"]["n_lineas"] == 0
    assert ab_s1["quitadas"]["aciertos"] is None

    # Artefacto guardado coincide con valores principales
    art = json.loads((Path(ROOT) / "datos" / "ronda12_punto6_frecuencias_24oos.json").read_text(encoding="utf-8"))
    assert art["soloA"]["pre_vs_ambos_arreglos"]["sec1"]["sobre_24_partidos_unicos"]["anadidas"]["aciertos"] == 80
