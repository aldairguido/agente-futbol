from __future__ import annotations

from evaluacion_pronosticos import (
    ETAPAS_CADENA_LAMBDA,
    analizar_ablacion_cadena_lambda_oos,
    extraer_lambdas_etapas_cadena,
)


def test_extraer_lambdas_etapas_cadena_respeta_orden_canonico() -> None:
    traza = [
        {"regla": "tasas_goles_plano", "lam_l_despues": 1.8606, "lam_v_despues": 1.4175},
        {"regla": "moduladores_tacticos", "lam_l_despues": 1.9374, "lam_v_despues": 1.4914},
        {"regla": "lambda_efectivo_finalizacion", "lam_l_despues": 2.2792, "lam_v_despues": 1.7365},
        {"regla": "doble_adn_60_40", "lam_l_despues": 1.835, "lam_v_despues": 1.39},
        {"regla": "red_recursiva_45_dcn_bucle", "lam_l_despues": 3.0672, "lam_v_despues": 1.4022},
    ]
    res = extraer_lambdas_etapas_cadena(traza, lam_final=(2.78, 1.0))
    assert tuple(res.keys()) == ETAPAS_CADENA_LAMBDA
    assert res["1_lambda_plano"] == (1.8606, 1.4175)
    assert res["2_mas_moduladores"] == (1.9374, 1.4914)
    assert res["3_mas_finalizacion"] == (2.2792, 1.7365)
    assert res["4_mas_doble_adn"] == (1.835, 1.39)
    assert res["5_mas_red_45_dcn"] == (3.0672, 1.4022)
    assert res["6_mas_reglas"] == (2.78, 1.0)


def test_analizar_ablacion_cadena_lambda_oos_identifica_mejoras_y_empeoramientos() -> None:
    rows = []
    for idx in range(8):
        pid = f"m_{idx}"
        y = 1 if idx % 2 == 0 else 0
        # 4_mas_doble_adn es la etapa mejor calibrada; 2, 3, 5 y 6 empeoran respecto a su previa
        probs = {
            "1_lambda_plano": 0.72 if y == 1 else 0.28,
            "2_mas_moduladores": 0.70 if y == 1 else 0.31,
            "3_mas_finalizacion": 0.67 if y == 1 else 0.35,
            "4_mas_doble_adn": 0.75 if y == 1 else 0.25,
            "5_mas_red_45_dcn": 0.69 if y == 1 else 0.33,
            "6_mas_reglas": 0.66 if y == 1 else 0.36,
        }
        rows.append({
            "id_partido": pid,
            "partido": f"Local{idx} vs Visita{idx}",
            "mercado": "Total de goles",
            "evento_ocurrio": y,
            "probs": probs,
        })

    res = analizar_ablacion_cadena_lambda_oos(rows, n_boot=300, seed=42)
    assert res["n_partidos_unicos"] == 8
    assert res["mejor_etapa_brier"] == "4_mas_doble_adn"
    assert res["mejor_etapa_log_loss"] == "4_mas_doble_adn"

    pasos = {p["etapa_actual"]: p for p in res["transiciones_paso_a_paso"]}
    assert pasos["2_mas_moduladores"]["veredicto_brier"] == "empeora"
    assert pasos["3_mas_finalizacion"]["veredicto_brier"] == "empeora"
    assert pasos["4_mas_doble_adn"]["veredicto_brier"] == "mejora"
    assert pasos["5_mas_red_45_dcn"]["veredicto_brier"] == "empeora"
    assert pasos["6_mas_reglas"]["veredicto_brier"] == "empeora"
