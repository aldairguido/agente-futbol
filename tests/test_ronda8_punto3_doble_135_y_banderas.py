"""
Tests reales para Ronda 8 — Punto 3:
Auditoría de doble ×1.35 (`copa_asedio_defensa_rota` en `red_45_dcn` y `post_sinc_mercados`),
medición aislada con IC95% de la bandera 11 (`usar_guarda_doble_defensa_rota`) y clasificación
estricta de las banderas 11, 13 y 14 respecto al Conjunto A («solo si no alteran probabilidades
validadas»).
"""

from __future__ import annotations

from decision_cobertura import bandera_activa, banderas_comportamiento
from evaluacion_pronosticos import (
    contar_doble_defensa_rota_en_trazas,
    evaluar_bandera_11_aislada_y_propuesta_conjunto_a,
)


def test_contar_doble_defensa_rota_en_trazas_separa_una_y_dos_aplicaciones():
    registros = [
        {
            "job_id": "ARCH::1edc8c043cfdd28a",
            "partido": "Newcastle United vs Aston Villa",
            "competicion": "Premier League",
            "es_oos": True,
            "traza_lambda": [
                {"paso": 6, "etapa": "red_45_dcn", "regla": "copa_asedio_defensa_rota", "L0": 1.9688, "L1": 2.6579, "V0": 2.5623, "V1": 2.5623},
                {"paso": 10, "etapa": "post_sinc_mercados", "regla": "copa_asedio_defensa_rota", "L0": 2.0534, "L1": 2.7720, "V0": 2.2839, "V1": 2.2839},
            ],
        },
        {
            "job_id": "ARCH::0f42c568a51abbbe",
            "partido": "Villarreal vs Elche",
            "competicion": "LaLiga",
            "es_oos": True,
            "traza_lambda": [
                {"paso": 12, "etapa": "post_sinc_mercados", "regla": "copa_asedio_defensa_rota", "L0": 2.4137, "L1": 2.7758, "V0": 1.0, "V1": 1.0},
            ],
        },
        {
            "job_id": "DESIGN::TB_rf",
            "partido": "Turquía vs Belgium",
            "competicion": "UEFA Nations League",
            "es_oos": False,
            "traza_lambda": [
                {"paso": 6, "etapa": "red_45_dcn", "regla": "copa_asedio_defensa_rota", "L0": 1.0467, "L1": 1.0467, "V0": 3.4214, "V1": 4.6189},
                {"paso": 11, "etapa": "post_sinc_mercados", "regla": "copa_asedio_defensa_rota", "L0": 1.04, "L1": 1.04, "V0": 2.6289, "V1": 3.549},
            ],
        },
    ]
    res = contar_doble_defensa_rota_en_trazas(registros)
    assert res["n_archivados_analizados"] == 2
    assert res["n_archivados_1_vez"] == 1
    assert res["n_archivados_doble_135"] == 1
    assert res["n_oos_doble_135"] == 1
    assert len(res["tickets_diseno_doble_135"]) == 1
    assert res["tickets_archivados_doble_135"][0]["etapas"] == ["red_45_dcn", "post_sinc_mercados"]


def test_evaluar_bandera_11_aislada_y_propuesta_conjunto_a_respeta_regla_estricta():
    assert bandera_activa("usar_guarda_doble_defensa_rota") is False
    assert bandera_activa("usar_kelly_conservador_min_3pct") is False
    assert bandera_activa("usar_excluir_primer_gol_top5") is False

    observaciones = []
    # 3 partidos afectados donde flag11 desinfla exceso de doble ×1.35 y mejora Brier/LogLoss
    for idx, pid in enumerate(["Newcastle United vs Aston Villa", "Everton vs Chelsea", "Union Berlin vs Borussia Dortmund"]):
        for k in range(6):
            observaciones.append({
                "id_partido": pid,
                "partido": pid,
                "familia": "goles_totales",
                "evento_ocurrio": 0,
                "probs": {
                    "soloA": 0.78 - 0.01 * idx,
                    "flag11": 0.66 - 0.01 * idx,
                },
            })
    # 5 partidos no afectados donde flag11 == soloA
    for idx in range(5):
        pid = f"Partido Neutro {idx}"
        for k in range(6):
            observaciones.append({
                "id_partido": pid,
                "partido": pid,
                "familia": "ganador_1x2",
                "evento_ocurrio": 1 if (k % 2 == 0) else 0,
                "probs": {
                    "soloA": 0.62,
                    "flag11": 0.62,
                },
            })

    rep = evaluar_bandera_11_aislada_y_propuesta_conjunto_a(observaciones, n_boot=400, seed=42)
    assert rep["n_filas_oos_alteradas_flag11"] == 18
    assert len(rep["partidos_oos_alterados_flag11"]) == 3

    diff_tot = rep["bootstrap_total_oos"]["diferencias"]["flag11 - soloA"]
    assert diff_tot["d_brier"] < 0
    assert diff_tot["d_log_loss"] < 0

    diff_af = rep["bootstrap_partidos_afectados_oos"]["diferencias"]["flag11 - soloA"]
    assert diff_af["d_brier"] < 0
    assert diff_af["d_brier_ic95"][1] < 0

    prop = rep["propuesta_conjunto_a"]
    # Bandera 11 altera probabilidades validadas -> NO puede pasar al Conjunto A sin autorización
    assert prop["bandera_11_usar_guarda_doble_defensa_rota"]["altera_probabilidades_validadas"] is True
    assert prop["bandera_11_usar_guarda_doble_defensa_rota"]["propuesta_mover_conjunto_a"] is False
    # Banderas 13 y 14 alteran 0 probabilidades validadas -> elegibles para Conjunto A
    assert prop["bandera_13_usar_kelly_conservador_min_3pct"]["altera_probabilidades_validadas"] is False
    assert prop["bandera_13_usar_kelly_conservador_min_3pct"]["propuesta_mover_conjunto_a"] is True
    assert prop["bandera_14_usar_excluir_primer_gol_top5"]["altera_probabilidades_validadas"] is False
    assert prop["bandera_14_usar_excluir_primer_gol_top5"]["propuesta_mover_conjunto_a"] is True
