"""Tests para ORDEN RONDA 9 — Punto 4 (5(c)): Ticket regenerado Colombia-Chile,
conciliación 1X2 (42.0/30.8/27.2 vs 46.5/26.3), calibración observada OOS vs
frecuencia empírica en 30 matrices con brecha con signo (+20.5 pp), y explicación
de por qué 'Chile Menos de 1.5 (80.2%)' es #1 en soloA y no salía en A+B."""

from __future__ import annotations

import os
from evaluacion_pronosticos import (
    auditar_colombia_chile_ronda8,
    auditar_punto_5c_colombia_chile_ronda9,
)


def test_ronda9_punto4_conciliacion_1x2_y_brecha_con_signo():
    res = auditar_punto_5c_colombia_chile_ronda9(regenerar_png=False)

    # 1. Conciliación 1X2: con lambda=(1.10, 0.82) y rho=-0.11, siempre es 42.0 / 30.7 (30.8 cierre) / 27.2
    c1x2 = res["conciliacion_1x2"]
    assert round(c1x2["lambda_local_ft"], 2) == 1.10
    assert round(c1x2["lambda_visitante_ft"], 2) == 0.82
    assert c1x2["p_1x2_redondeado_1d"] == (42.0, 30.7, 27.2)
    assert c1x2["p_1x2_cierre_100_pct"] == (42.0, 30.8, 27.2)
    assert c1x2["p_1x_matriz_pct"] == 72.8
    assert c1x2["igual_en_soloA_y_AB"] is True

    # 2. Definición y valores de calibración observada OOS (24 partidos) vs frecuencia empírica (30 matrices)
    assert "histórico fuera de muestra (OOS, 24 partidos liquidados" in res["definicion_calibracion_observada"]
    fams = res["calibracion_oos_por_familia"]
    assert fams["Doble oportunidad"]["n_evaluables_oos"] == 31
    assert fams["Doble oportunidad"]["observado_oos_pct"] == 80.6
    assert fams["Doble oportunidad"]["anunciado_oos_pct"] == 76.5
    assert fams["Doble oportunidad"]["brecha_oos_con_signo_pp"] == 4.1
    assert fams["Doble oportunidad"]["veto_top5"] is False

    assert fams["Goles por equipo"]["n_evaluables_oos"] == 864
    assert fams["Goles por equipo"]["observado_oos_pct"] == 91.7
    assert fams["Goles por equipo"]["anunciado_oos_pct"] == 93.8
    assert fams["Goles por equipo"]["brecha_oos_con_signo_pp"] == -2.1
    assert fams["Goles por equipo"]["veto_top5"] is False

    assert fams["Córneres por equipo"]["n_evaluables_oos"] == 449
    assert fams["Córneres por equipo"]["veto_top5"] is True

    # 3. Brecha con signo: 93.3 contra 72.8 es +20.5 pp (no 20.6)
    c_1x = res["calculo_1x_con_signo"]
    assert c_1x["prob_modelo_pct"] == 72.8
    assert c_1x["frec_empirica_matrices_colombia_15p_pct"] == 93.3
    assert c_1x["brecha_empirica_con_signo_pp"] == 20.5
    assert c_1x["observado_oos_pct"] == 80.6
    assert c_1x["anunciado_oos_pct"] == 76.5
    assert c_1x["brecha_oos_con_signo_pp"] == 4.1

    lineas_soloA = {r["etiqueta"]: r for r in res["lineas_70_solo_A"]}
    l_1x = lineas_soloA["1X (Colombia o empate)"]
    assert l_1x["prob_pct"] == 72.8
    assert l_1x["frec_empirica_matrices_pct"] == 93.3
    assert l_1x["brecha_empirica_con_signo_pp"] == 20.5
    assert l_1x["desvio_abs_pp"] == 20.5
    assert "+20.5 pp" in str(l_1x["filtro_quito_top5"])
    assert "Sensibilidad 9 escenarios" in str(l_1x["filtro_quito_seccion1"])

    # 4. Explicación de por qué Chile Menos de 1.5 (80.2%) es #1 en soloA y no salía en A+B
    exp_chile = res["explicacion_chile_menos_15_top1"]
    assert exp_chile["prob_pct"] == 80.2
    assert exp_chile["frec_empirica_chile_15p_pct"] == 86.7
    assert exp_chile["brecha_empirica_con_signo_pp"] == 6.5
    assert exp_chile["en_top5_soloA"] is True
    assert exp_chile["en_top5_AB"] is False
    assert exp_chile["cuota_justa_exacta"] < 1.25

    # 5. Ticket regenerado en texto y PNG
    assert "Goles Chile (FT): -1.5 Goles — 80.2%" in res["ticket_soloA_texto"]
    assert "🏆 TOP" not in res["ticket_AB_texto"]
    for k, ruta in res["rutas_png"].items():
        assert os.path.exists(ruta), f"Falta PNG {k}: {ruta}"

    # 6. Coherencia con auditar_colombia_chile_ronda8
    r8 = auditar_colombia_chile_ronda8()
    l_1x_r8 = {r["etiqueta"]: r for r in r8["punto_5c_filtros_y_coherencia"]["lineas_70_solo_A"]}[
        "1X (Colombia o empate)"
    ]
    assert l_1x_r8["brecha_empirica_con_signo_pp"] == 20.5
    assert l_1x_r8["desvio_abs_pp"] == 20.5
