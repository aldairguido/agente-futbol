"""Tests de Ronda 13 Punto 4:
Tabla Leave-One-Out (LOO) de las 14 banderas de Conjunto B sobre los 24 OOS
con bootstrap agrupado por partido (`n=24`) e IC95%, para sustentar defectos.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import decision_cobertura as dc
import evaluacion_pronosticos as ep
import motor_mercados as mm


def test_ronda13_punto4_tabla_loo_14_banderas_y_defectos_intactos():
    ruta_json = Path(ROOT) / "datos" / "ronda13_punto4_loo_banderas_b.json"
    assert ruta_json.exists()
    res = ep.auditar_leave_one_out_banderas_b_ronda13(regenerar=False)

    assert res["cohorte"]["n_partidos_unicos_oos"] == 24
    assert res["cohorte"]["n_jobs_oos"] == 26
    assert res["cohorte"]["n_filas_comunes_catalogo"] == 1376
    assert res["cohorte"]["n_boot"] == 2000

    # Snapshot histórico R13 tenía todas en False; tras R14 solo usar_sobredispersion_nb_corners=True
    banderas_def = dc.obtener_banderas_comportamiento()
    assert len(banderas_def) == 14
    assert all(v is False for v in res["banderas_defecto_actuales"].values())
    assert banderas_def["usar_sobredispersion_nb_corners"] is True
    assert all(v is False for k, v in banderas_def.items() if k != "usar_sobredispersion_nb_corners")
    assert abs(dc.obtener_umbral_cuota_justa_top5() - 1.25) < 1e-9

    tabla = res["tabla_leave_one_out_14_banderas"]
    assert [r["bandera_quitada"] for r in tabla] == list(dc.BANDERAS_COMPORTAMIENTO_DEFECTO.keys())

    por_bandera = {r["bandera_quitada"]: r for r in tabla}
    b14 = res["baselines"]["B_14"]
    b9 = res["baselines"]["B_9"]
    sa = res["baselines"]["soloA"]

    assert b14["catalogo_1376"]["log_loss"] < b9["catalogo_1376"]["log_loss"] < sa["catalogo_1376"]["log_loss"]
    # Sin guarda_doble_defensa_rota coincide en catalogo_1376 con B_9
    assert por_bandera["usar_guarda_doble_defensa_rota"]["catalogo_1376"]["log_loss"] == b9["catalogo_1376"]["log_loss"]
    assert por_bandera["usar_sobredispersion_nb_corners"]["catalogo_1376"]["d_log_loss_vs_B14"] > 0.05
    assert por_bandera["usar_sobredispersion_nb_corners"]["catalogo_1376"]["d_log_loss_vs_B14_ic95"][0] > 0.0
    assert por_bandera["usar_gate_corners_8_15"]["cobertura_y_gates_26_jobs"]["delta_estimables_catalogo_vs_B14"] > 0
    assert por_bandera["usar_gate_cronologia_mitades_10_15"]["seccion1_24_unicos"]["d_log_loss_vs_B14_ic95"][1] < 0.0
    assert por_bandera["usar_kelly_conservador_min_3pct"]["kelly_top5_pct"]["stake_max_observado_pct"] > 3.0
    assert b14["kelly_top5_pct"]["stake_max_observado_pct"] == 3.0

    # Comprobación en vivo sobre un cruce OOS con córneres activos en B_14 del efecto real de NB córneres vs B_14
    jmap = json.loads((Path(ROOT) / "datos" / "ronda10_oos_26_jobs_map.json").read_text(encoding="utf-8"))
    prob_nb_diff_verificada = False
    for _, (fpl, fpv, cut) in sorted(jmap["jobs"].items()):
        dl = mm.filtrar_por_fecha(json.loads((Path(ROOT) / fpl).read_text(encoding="utf-8")), cut)
        dv = mm.filtrar_por_fecha(json.loads((Path(ROOT) / fpv).read_text(encoding="utf-8")), cut)
        with dc.banderas_comportamiento(activar_todas=False, **{k: True for k in banderas_def}):
            m14 = mm.MotorMercados(dl, dv, "casa", "fuera", "Local", "Visita", mm.Parametros())
            m14.preparar()
            c14 = [s.probabilidad for rm in m14.evaluar_catalogo() if "corner" in rm.id_mercado for s in rm.selecciones if s.estimable]
        if not c14:
            continue
        with dc.banderas_comportamiento(activar_todas=False, **{k: (k != "usar_sobredispersion_nb_corners") for k in banderas_def}):
            mno_nb = mm.MotorMercados(dl, dv, "casa", "fuera", "Local", "Visita", mm.Parametros())
            mno_nb.preparar()
            cno_nb = [s.probabilidad for rm in mno_nb.evaluar_catalogo() if "corner" in rm.id_mercado for s in rm.selecciones if s.estimable]
        assert c14 != cno_nb
        prob_nb_diff_verificada = True
        break
    assert prob_nb_diff_verificada is True
