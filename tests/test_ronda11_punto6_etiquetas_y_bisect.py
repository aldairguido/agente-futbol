# -*- coding: utf-8 -*-
"""
Ronda 11 Punto 6 (Etiquetas, citas y git bisect 96483b7..5e8425f).
Verifica determinísticamente sin alterar λ ni probabilidades:
  (a) Etiqueta `"1.54 G estimado contraído (n=7 casa, prior ...)"` en XAI y Cuadro
      Especialista, compatibilidad con regex `(n=\\d+`, y conciliación matemática de
      `h_gf_lstm_15` (0.9891 en MotorMercados vs 0.9650 en serie directa) vs media 22/15=1.47.
  (b) Cita exacta de campo/archivo/fuente para el autogol de Guillermo Maripán min 35.
  (c) Conciliación explícita de Punto 9 separando `3 OOS (ev93)` de `5 archivados` + `1 diseño`.
  (d) Resultado completo del `git bisect` `96483b7..5e8425f` para Tarjetas (n=65) y Córneres (n=613).
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


def test_ronda11_punto6_etiquetas_citas_y_bisect():
    with dc.banderas_comportamiento(activar_todas=False):
        rep = ep.auditar_etiquetas_citas_y_bisect_ronda11(guardar_ruta=None)

    # (a) Etiqueta 1.54 G contraída y regex (n=
    a = rep["a_etiqueta_154g"]
    xai_ln = a["etiqueta_xai_goles_xg"]
    cuadro_ln = a["etiqueta_cuadro_punteria"]
    assert "1.54 G estimado contraído (n=7 casa, prior " in xai_ln
    assert "1.54 G estimado contraído (n=7 casa, prior " in cuadro_ln
    assert a["coherencia_n_regex_ok"] is True
    assert "(n=7c/15g)" not in xai_ln
    assert "promedia 1.54 G" not in cuadro_ln
    assert a["mu_local_casa_campos"]["goles_favor"] == 1.54
    assert a["conciliacion_prior_lstm"]["media_cruda_15p_22_sobre_15"] == 1.4667
    assert round(a["conciliacion_prior_lstm"]["r10_directo_doc_partidos"]["h_gf_lstm_15"], 3) == 0.965
    assert round(a["conciliacion_prior_lstm"]["ruta_viva_motor_mercados"]["h_gf_lstm_15"], 4) == 0.9891

    # (b) Cita exacta Guillermo Maripán min 35
    b = rep["b_maripan_autogol_min35"]
    assert b["archivo"] == "datos/colombia_chile_2026_11_13_primario.json"
    assert b["fotmob_match_id"] == "6539346"
    assert b["ruta_campo"] == "eventos_gol_fotmob['Maripán'][0]"
    ev = b["evento"]
    assert ev["eventId"] == 14732077
    assert ev["time"] == 35
    assert ev["ownGoal"] is True
    assert ev["goalDescriptionKey"] == "owngoal"
    assert ev["player"]["name"] == "Guillermo Maripán"
    assert ev["newScore"] == [1, 0]

    # (c) Conciliación Punto 9: 3 OOS vs 5 archivados + 1 diseño
    c = rep["c_punto9_desambiguado_3oos_vs_5arch"]
    assert c["n_archivados_unicos"] == 5
    assert c["n_oos_ev93"] == 3
    assert c["n_archivados_no_oos"] == 2
    assert c["n_diseno"] == 1
    assert c["n_total_unicos"] == 6
    assert c["denominador"] == 73
    assert {x["partido"] for x in c["partidos_oos_ev93"]} == {
        "Everton vs Chelsea",
        "Newcastle United vs Aston Villa",
        "Union Berlin vs Borussia Dortmund",
    }
    assert {x["partido"] for x in c["partidos_archivados_no_oos"]} == {
        "New Zealand vs Inter Miami CF",
        "Barcelona vs Aston Villa",
    }
    assert [x["partido"] for x in c["partidos_diseno"]] == ["Turkey vs Belgium"]

    # (d) Bisect 96483b7..5e8425f Tarjetas (n=65) y Córneres (n=613)
    d = rep["d_bisect_tarjetas_y_corneres"]
    hastas = [t["hasta"] for t in d["transiciones_exactas"]]
    assert "0bac1ce" in hastas
    assert "6aace49" in hastas
    assert "e68fff4" in hastas
    assert "443c650" in hastas
    assert "5976c27" in hastas
    assert "5e8425f" in hastas
    assert d["referencia_arch_vs_5e8425f"]["Tarjetas_n65"]["5e8425f"]["brier"] == 0.142492
    assert d["referencia_arch_vs_5e8425f"]["Corneres_n613"]["5e8425f"]["log_loss"] == 0.831128

    # JSON persistido debe existir y coincidir con la auditoría
    disco = json.loads((ROOT / "datos" / "ronda11_punto6_etiquetas_y_bisect.json").read_text(encoding="utf-8"))
    assert disco == rep
