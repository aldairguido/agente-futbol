"""
Tests reales para Bloque 4.1:
  - EV calculado desde la probabilidad calibrada (`agente_consciente`), mostrando ambos:
    EV bruto (`ev_bruto_pct`) y EV calibrado (`ev_calibrado_pct`).
  - Quarter-Kelly calculado estrictamente desde `prob_calibrada`.
  - Stake forzado en `0.0` (`estado_stake == "sin_stake_bajo_vigilancia"`) cuando la
    familia está `bajo vigilancia`, tanto en Top 5 como en Ticket de Valor (`informe.py`).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import informe as inf_mod
import motor_mercados as mm


def _cargar_ronda5():
    with open(ROOT / "tests" / "fixtures" / "ronda5_real_fixtures.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_4_1a_ev_bruto_vs_ev_calibrado_y_stake_kelly_desde_calibrada():
    """
    Muestra ambos EV (bruto y calibrado) y calcula Quarter-Kelly desde prob_calibrada,
    rechazando +EV espurio cuando EV_bruto > 0 pero EV_calibrado <= 0.
    """
    # Familia NO bajo vigilancia: Doble oportunidad (muestra empírica real positiva)
    res_do = mm.calcular_ev_y_stake_calibrado("Doble oportunidad", 0.78, cuota_real=1.40)
    assert res_do["bajo_vigilancia"] is False
    p_cal_do = float(res_do["prob_calibrada"])
    assert p_cal_do != 0.78
    assert res_do["ev_bruto_pct"] == round(((0.78 * 1.40) - 1.0) * 100.0, 1)
    assert res_do["ev_calibrado_pct"] == round(((p_cal_do * 1.40) - 1.0) * 100.0, 1)
    assert res_do["ev_pct"] == res_do["ev_calibrado_pct"]
    assert res_do["estado_stake"] == "activo"
    b = max(0.05, 1.40 - 1.0)
    f_q_cal = (((b * p_cal_do) - (1.0 - p_cal_do)) / b) * 0.25
    assert res_do["stake_sugerido"] == round(max(0.012, min(0.08, f_q_cal)), 4)

    # Caso EV_bruto > 0 pero EV_calibrado <= 0 en familia Hándicap/Ganador (no vigilancia, cuota justa intermedia)
    res_espurio = mm.calcular_ev_y_stake_calibrado("Ganador del partido", 0.78, cuota_real=1.32)
    assert res_espurio["ev_bruto_pct"] > 0.0
    assert res_espurio["ev_calibrado_pct"] <= 0.0
    assert res_espurio["ev_pct"] == res_espurio["ev_calibrado_pct"]
    assert res_espurio["stake_sugerido"] == 0.0
    assert res_espurio["stake_pct"] == 0.0
    assert res_espurio["estado_stake"] == "sin_valor_calibrado"


def test_4_1b_stake_cero_en_familias_bajo_vigilancia_top5_y_ticket_valor():
    """
    Toda selección cuya familia esté `bajo vigilancia` debe recibir stake_sugerido == 0.0,
    stake_pct == 0.0 y estado_stake == 'sin_stake_bajo_vigilancia' aunque el EV sea muy positivo.
    """
    sels = [
        {
            "mercado": "Total de goles",
            "evento": "over_1.5",
            "etiqueta": "Más de 1.5 goles",
            "periodo": "FT",
            "linea": 1.5,
            "probabilidad": 0.85,
            "estado": "mantiene",
            "cuota_real": 2.00,  # EV altísimo intencional para comprobar el bloqueo de stake
        },
        {
            "mercado": "Doble oportunidad",
            "evento": "dc_x2",
            "etiqueta": "X2 (Empate o Visita)",
            "periodo": "FT",
            "probabilidad": 0.82,
            "estado": "mantiene",
            "cuota_real": 1.45,
        },
    ]
    top5 = mm.seleccionar_top_5_jugadas_recomendadas(sels, motor=None, eq_l="Turkey", eq_v="Belgium")
    assert len(top5) >= 2

    por_etq = {it["etiqueta"]: it for it in top5}
    it_tot = next(v for k, v in por_etq.items() if "1.5" in k)
    assert it_tot["bajo_vigilancia"] is True
    assert it_tot["ev_bruto_pct"] == round(((0.85 * 2.00) - 1.0) * 100.0, 1)
    assert it_tot["ev_calibrado_pct"] is not None and it_tot["ev_calibrado_pct"] > 0.0
    assert it_tot["ev_pct"] == it_tot["ev_calibrado_pct"]
    assert it_tot["stake_sugerido"] == 0.0
    assert it_tot["stake_sugerido_pct"] == 0.0
    assert it_tot["stake_pct"] == 0.0
    assert it_tot["estado_stake"] == "sin_stake_bajo_vigilancia"
    assert "bajo vigilancia" in it_tot["badge_completo"].lower()
    assert "cal" in it_tot["badge_completo"].lower() and "bruto" in it_tot["badge_completo"].lower()

    it_dc = next(v for k, v in por_etq.items() if "X2" in k)
    assert it_dc["bajo_vigilancia"] is False
    assert it_dc["estado_stake"] == "activo"
    assert it_dc["stake_sugerido"] > 0.0 and it_dc["stake_pct"] > 0.0

    # Verificar Ticket de Valor (`informe.construir_filas_ticket_valor`) con fixture real Turkey-Belgium
    r5 = _cargar_ronda5()
    analisis = mm.analizar(r5["docs"]["turkey"], r5["docs"]["belgium"], "Turkey", "Belgium")
    filas = inf_mod.construir_filas_ticket_valor(
        analisis,
        cuotas_flashscore={"local": 4.20, "empate": 3.70, "visitante": 1.95},
    )
    assert len(filas) == 6
    for fila in filas:
        assert "ev_bruto_pct" in fila and fila["ev_bruto_pct"] is not None
        assert "ev_calibrado_pct" in fila and fila["ev_calibrado_pct"] is not None
        assert fila["ev_pct"] == fila["ev_calibrado_pct"]
        assert "prob_calibrada" in fila
        assert "stake_sugerido" in fila and "stake_pct" in fila
        assert "bruto" in fila["ev_str"].lower()
