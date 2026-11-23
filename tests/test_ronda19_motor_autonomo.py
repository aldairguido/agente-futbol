"""
tests/test_ronda19_motor_autonomo.py
───────────────────────────────────────────────────────────────────────────────
Suite de verificación obligatoria del Motor Autónomo (Capas 0 a 6):
  1. test_cero_fuga_temporal_espacio_variables
  2. test_none_nunca_es_cero_en_explorador
  3. test_poda_jerarquica_ordenes_1_a_5
  4. test_greedy_map_k_dpp_repele_clones
  5. test_lema1_cramer_frecuencia_vs_espacial
  6. test_cvar25_y_riesgo_entropico_exactos
  7. test_topologia_entrelazamiento_hopf_vs_separable
  8. test_proyeccion_faa_coherencia_exacta
  9. test_benjamini_hochberg_fdr_controla_ruido_puro
 10. test_invariancia_bit_a_bit_con_banderas_apagadas
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import numpy as np
import pytest

from motor_autonomo import (
    BANDERAS_MOTOR_AUTONOMO_DEFECTO,
    cargar_config_motor_autonomo,
    ejecutar_motor_autonomo_sombra,
)
from motor_autonomo.espacio_variables import (
    EstandarizadorFitOnPast,
    calcular_cobertura_simetrica_punto44,
    construir_dataset_historico_y_actual,
    filtrar_partidos_por_cutoff,
    parsear_valor_observado,
)
from motor_autonomo.explorador_jerarquico import explorar_patrones_jerarquicos
from motor_autonomo.selector_dpp import (
    construir_kernel_calidad_diversidad,
    destilar_patrones_ortogonales_dpp,
    greedy_map_k_dpp,
    normalizar_columnas_l2,
    seleccionar_top5_dpp_map,
)
from motor_autonomo.evaluador_cramer_cvar import (
    calcular_cvar_25_9esc,
    calcular_riesgo_entropico,
    construir_pmf_poisson,
    distancia_cramer_exacta_espacial,
    evaluar_riesgo_escenarios_9,
    perdida_phi_td_pareto,
)
from motor_autonomo.diagnostico_topologico import (
    aplicar_aduana_benjamini_hochberg,
    calcular_q_valores_benjamini_hochberg,
    diagnosticar_entrelazamiento_topologico,
    evaluar_probe_separacion_variables,
)
from motor_autonomo.aprendiz_walkforward_faa import (
    ajustar_pesos_walkforward,
    proyectar_cono_monotono_pav,
    proyectar_politopo_coherente_faa,
    proyectar_simplex_euclideo,
)


def test_cero_fuga_temporal_espacio_variables():
    """
    1. Verifica que ningún partido con fecha >= cutoff_timestamp ni amistoso
       entra en cuantiles, estandarización ni activaciones de patrones.
    """
    partidos = [
        {"id_partido": "p1", "fecha": "2026-10-01", "goles_favor": 2, "goles_contra": 1, "xg": 1.4, "corners": 5},
        {"id_partido": "p2", "fecha": "2026-10-05", "goles_favor": 1, "goles_contra": 0, "xg": 1.6, "corners": 6},
        {"id_partido": "p3", "fecha": "2026-10-10", "goles_favor": 3, "goles_contra": 1, "xg": 1.8, "corners": 7},
        # Partido en la fecha exacta de corte -> DEBE EXCLUIRSE
        {"id_partido": "p_cut", "fecha": "2026-10-15", "goles_favor": 9, "goles_contra": 9, "xg": 999.0, "corners": 99},
        # Partido futuro -> DEBE EXCLUIRSE
        {"id_partido": "p_fut", "fecha": "2026-10-20", "goles_favor": 8, "goles_contra": 8, "xg": 888.0, "corners": 88},
        # Partido amistoso previo al corte -> DEBE EXCLUIRSE
        {"id_partido": "p_ami", "fecha": "2026-10-03", "competicion": "Club Friendlies", "goles_favor": 7, "goles_contra": 7, "xg": 777.0, "corners": 77},
    ]
    actual = {"id_partido": "act", "fecha": "2026-10-15", "xg": 1.6, "corners": 6}
    ds = construir_dataset_historico_y_actual(
        partidos,
        actual,
        cutoff_timestamp="2026-10-15",
        objetivo="+2.5 FT",
        cobertura_minima=0.60,
        variables_candidatas=("xg", "corners"),
    )
    assert len(ds["partidos_train"]) == 3
    ids_usados = [p["id_partido"] for p in ds["partidos_train"]]
    assert ids_usados == ["p1", "p2", "p3"]
    # La media de xg en p1, p2, p3 es esattamente 1.6 -> z_actual de xg=1.6 debe ser 0.0 (sin contaminación de 999/888/777)
    idx_xg = ds["nombres_variables"].index("xg")
    assert abs(float(ds["estandarizador"].medias_[idx_xg]) - 1.6) < 1e-9
    assert abs(float(ds["x_actual_z"][idx_xg])) < 1e-9


def test_none_nunca_es_cero_en_explorador():
    """
    2. Inyectar None ('sin_dato') en una variable y verificar que nunca genera
       predicados falsos ni se convierte en 0.0.
    """
    assert parsear_valor_observado(None) is None
    assert parsear_valor_observado("sin_dato") is None
    assert parsear_valor_observado("") is None
    assert parsear_valor_observado(0.0) == 0.0
    assert parsear_valor_observado("0") == 0.0

    rng = np.random.default_rng(123)
    X_tr = rng.normal(0.0, 1.0, size=(30, 3))
    y_tr = (X_tr[:, 0] > 0.0).astype(int)
    # Variable 1 ('var_faltante') es None/NaN en el partido actual, aunque 0.0 caería en régimen MEDIO
    x_act = np.array([1.2, np.nan, -0.9], dtype=float)
    res = explorar_patrones_jerarquicos(
        X_tr,
        y_tr,
        x_act,
        nombres_variables=["var_activa_1", "var_faltante", "var_activa_2"],
        orden_maximo=3,
    )
    for pat in res["patrones_candidatos"]:
        assert "var_faltante" not in pat["variables"]


def test_poda_jerarquica_ordenes_1_a_5():
    """
    3. Verificar que la expansión respeta soporte_minimo_por_orden, rechaza hijos con
       Jaccard > 0.95 respecto al padre y solo avanza de orden k a k+1 cuando hay
       ganancia marginal estricta delta_LL > eps_marg.
    """
    n = 60
    X_tr = np.full((n, 6), -1.0, dtype=float)
    # v0 activa en 0..35 (36 partidos)
    X_tr[:36, 0] = 2.0
    # v1 es clon exacto de v0 (activa en 0..35 -> Jaccard = 1.0 > 0.95 -> DEBE PODARSE)
    X_tr[:36, 1] = 2.0
    # v2 refina a 0..23 (24 partidos, Jaccard = 24/36 = 0.667 <= 0.95)
    X_tr[:24, 2] = 2.0
    # v3 refina a 0..15 (16 partidos, Jaccard = 16/24 = 0.667 <= 0.95)
    X_tr[:16, 3] = 2.0
    # v4 refina a 0..11 (12 partidos, Jaccard = 12/16 = 0.75 <= 0.95)
    X_tr[:12, 4] = 2.0
    # v5 solo activa en 0..4 (5 partidos < N_min(5)=8 -> DEBE PODARSE por soporte)
    X_tr[:5, 5] = 2.0

    # y=1 concentrado en los primeros 12 partidos y unos pocos fuera para crear ganancia marginal real en cada orden
    y_tr = np.zeros(n, dtype=int)
    y_tr[:12] = 1
    y_tr[12:16] = [1, 1, 1, 0]
    y_tr[16:24] = [1, 1, 0, 0, 0, 0, 0, 0]
    y_tr[24:36] = [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    x_act = np.full(6, 2.0, dtype=float)
    nombres = ["v0", "v1_clon", "v2", "v3", "v4", "v5_bajo_soporte"]
    res = explorar_patrones_jerarquicos(
        X_tr,
        y_tr,
        x_act,
        nombres_variables=nombres,
        orden_maximo=5,
        ganancia_marginal_minima_ll=0.005,
        jaccard_maximo_padre_hijo=0.95,
    )
    pats = res["patrones_candidatos"]
    assert len(pats) > 0
    for p in pats:
        k = p["orden"]
        assert p["n_activaciones"] >= {1: 12, 2: 10, 3: 8, 4: 8, 5: 8}[k]
        assert "v5_bajo_soporte" not in p["variables"]
        # El par clonado (v0, v1_clon) con Jaccard=1.0 jamás puede sobrevivir en orden >= 2
        assert not ("v0" in p["variables"] and "v1_clon" in p["variables"])
        if k >= 2:
            assert p["jaccard_con_padre"] <= 0.95
            assert p["ganancia_marginal_ll"] > 0.005


def test_greedy_map_k_dpp_repele_clones():
    """
    4. Dados 2 vectores casi idénticos con alta calidad y 1 vector ortogonal con
       calidad ligeramente menor, verificar que greedy_map_k_dpp elige el par diverso
       y que log det(L_S) coincide con np.linalg.slogdet(L[np.ix_(S, S)]) hasta 1e-9.
    """
    v1 = np.array([1.0, 0.0, 0.0])
    v2_clon = np.array([0.9999, 0.01414, 0.0])
    v2_clon = v2_clon / np.linalg.norm(v2_clon)
    v3_ort = np.array([0.0, 1.0, 0.0])

    phi = np.column_stack([v1, v2_clon, v3_ort])
    calidades = np.array([10.0, 9.8, 8.5], dtype=float)

    sel, log_det = greedy_map_k_dpp(phi, calidades, k=2, temperatura_global=1.0)
    assert sel == [0, 2], f"Se esperaba [0, 2] (repeliendo al clon 1), se obtuvo {sel}"

    L = construir_kernel_calidad_diversidad(phi, calidades, temperatura_global=1.0)
    L_sub = L[np.ix_(sel, sel)]
    signo, logdet_exacto = np.linalg.slogdet(L_sub)
    assert signo > 0
    assert abs(log_det - float(logdet_exacto)) < 1e-9


def test_lema1_cramer_frecuencia_vs_espacial():
    """
    5. Verificar numéricamente que la pérdida de función característica con muestreo
       Pareto(alpha=1) converge a la distancia de Cramér al cuadrado exacta l2^2(P, delta_y)
       y clasifica en el mismo orden una distribución calibrada vs. una sesgada.
    """
    pmf_cal = construir_pmf_poisson(2.2, max_k=12)
    pmf_sesgada = construir_pmf_poisson(5.5, max_k=12)
    y_obs = 2.0

    res_cal = perdida_phi_td_pareto(pmf_cal, y_obs, n_frecuencias=2048, x_m=0.0005, omega_max=40.0, semilla=42)
    res_ses = perdida_phi_td_pareto(pmf_sesgada, y_obs, n_frecuencias=2048, x_m=0.0005, omega_max=40.0, semilla=42)

    assert res_cal["cramer_espacial_exacto"] < res_ses["cramer_espacial_exacto"]
    assert res_cal["loss_phi_td_cruda"] < res_ses["loss_phi_td_cruda"]
    assert res_cal["cramer_frecuencia_estimado"] < res_ses["cramer_frecuencia_estimado"]
    assert res_cal["error_abs_lema1"] < 0.05


def test_cvar25_y_riesgo_entropico_exactos():
    """
    6. Verificar el cálculo exacto de CVaR_25 sobre un vector conocido de 9 escenarios
       y confirmar que penaliza picks con caída brusca en los 2–3 peores escenarios.
    """
    esc_estable = [0.72, 0.73, 0.74, 0.75, 0.75, 0.76, 0.77, 0.78, 0.79]
    esc_fragil = [0.40, 0.44, 0.52, 0.84, 0.85, 0.86, 0.87, 0.88, 0.89]

    # Fórmula exacta Tabla 15: (p_(1) + p_(2) + 0.25 * p_(3)) / 2.25
    esperado_est = (0.72 + 0.73 + 0.25 * 0.74) / 2.25
    esperado_fra = (0.40 + 0.44 + 0.25 * 0.52) / 2.25

    assert abs(calcular_cvar_25_9esc(esc_estable) - esperado_est) < 1e-12
    assert abs(calcular_cvar_25_9esc(esc_fragil) - esperado_fra) < 1e-12

    ev_est = evaluar_riesgo_escenarios_9(esc_estable, eta=-4.0, umbral_cvar25=0.65)
    ev_fra = evaluar_riesgo_escenarios_9(esc_fragil, eta=-4.0, umbral_cvar25=0.65)
    # Aunque ambos tienen media >= 0.72, el frágil cae bajo 0.65 en CVaR_25 y es rechazado
    assert ev_est["supera_filtro_cvar25"] is True
    assert ev_fra["supera_filtro_cvar25"] is False
    assert ev_est["riesgo_entropico_9esc"] > ev_fra["riesgo_entropico_9esc"]


def test_topologia_entrelazamiento_hopf_vs_separable():
    """
    7. Generar dos anillos de Hopf sintéticos en R^3 (link = +-1) y dos nubes
       linealmente separables (link = 0), verificando que diagnostico_topologico.py
       detecta el enlace en el caso entrelazado y devuelve 0 en el separable.
    """
    m = 32
    t = np.linspace(0.0, 2.0 * np.pi, m, endpoint=False)
    # Anillo 1 en plano XY centrado en (0, 0, 0) de radio 1
    anillo_1 = np.column_stack([np.cos(t), np.sin(t), np.zeros_like(t)])
    # Anillo 2 en plano XZ centrado en (1, 0, 0) de radio 1 (pasa por (0,0,0) y (2,0,0) -> entrelazado Hopf link = 1)
    anillo_2 = np.column_stack([1.0 + np.cos(t), np.zeros_like(t), np.sin(t)])

    X_hopf = np.vstack([anillo_1, anillo_2])
    y_hopf = np.array([1] * m + [0] * m, dtype=int)
    res_hopf = diagnosticar_entrelazamiento_topologico(X_hopf, y_hopf, X_hopf[0], k_vecinos=4)
    assert res_hopf["link_gauss_detectado"] is True
    assert res_hopf["max_abs_link_entero"] >= 1

    # Dos anillos desplazados y linealmente separables (distancia 5 en eje X -> link = 0)
    anillo_sep_2 = np.column_stack([5.0 + np.cos(t), np.sin(t), np.zeros_like(t)])
    X_sep = np.vstack([anillo_1, anillo_sep_2])
    y_sep = np.array([1] * m + [0] * m, dtype=int)
    res_sep = diagnosticar_entrelazamiento_topologico(X_sep, y_sep, X_sep[0], k_vecinos=4)
    assert res_sep["link_gauss_detectado"] is False
    assert res_sep["max_abs_link_entero"] == 0


def test_proyeccion_faa_coherencia_exacta():
    """
    8. Pasar probabilidades crudas inconsistentes (1X2 sumando 1.12,
       P(-1.5) = 0.78 > P(-2.5) = 0.65) por proyectar_politopo_coherente_faa y
       verificar suma exacta 1.000000, monotonicidad estricta y cota de distancia
       ||P_M(x) - f|| <= 2 ||x - f|| (Teorema 3.1).
    """
    crudas = {
        "1x2": {"1": 0.55, "X": 0.32, "2": 0.25},  # suma = 1.12
        "under_goles": {"-1.5": 0.78, "-2.5": 0.65, "-3.5": 0.85, "-4.5": 0.92},
        "over_goles": {"+1.5": 0.70, "+2.5": 0.76, "+3.5": 0.40},
        "periodos_over_05": {"1T": 0.68, "2T": 0.74, "FT": 0.65},
    }
    proy = proyectar_politopo_coherente_faa(crudas)

    # 1) Suma 1X2 exacta == 1.000000 y DC coherente
    p1 = proy["1x2"]["1"]
    px = proy["1x2"]["X"]
    p2 = proy["1x2"]["2"]
    assert abs((p1 + px + p2) - 1.0) < 1e-12
    assert abs(proy["doble_oportunidad"]["1X"] - (p1 + px)) < 1e-12
    assert abs(proy["doble_oportunidad"]["X2"] - (px + p2)) < 1e-12

    # 2) Monotonicidad estricta en Under (creciente) y Over (decreciente)
    u = proy["under_goles"]
    assert u["-1.5"] <= u["-2.5"] <= u["-3.5"] <= u["-4.5"]
    ov = proy["over_goles"]
    assert ov["+1.5"] >= ov["+2.5"] >= ov["+3.5"]

    # 3) Coherencia aditiva entre periodos
    assert proy["periodos_over_05"]["FT"] >= max(
        proy["periodos_over_05"]["1T"],
        proy["periodos_over_05"]["2T"],
    )

    # 4) Cota del Teorema 3.1: ||P_M(x) - f|| <= 2 ||x - f|| para cualquier punto factible f in M
    x_vec = np.array([0.55, 0.32, 0.25], dtype=float)
    pm_vec = np.array([p1, px, p2], dtype=float)
    f_factible = np.array([0.50, 0.30, 0.20], dtype=float)
    dist_proy = float(np.linalg.norm(pm_vec - f_factible))
    dist_orig = float(np.linalg.norm(x_vec - f_factible))
    assert dist_proy <= 2.0 * dist_orig + 1e-12


def test_benjamini_hochberg_fdr_controla_ruido_puro():
    """
    9. Correr el explorador completo sobre etiquetas aleatorias permutadas (ruido puro)
       y verificar que con BH-FDR (q < 0.05) la tasa de falsos descubrimientos validados es 0.
    """
    rng = np.random.default_rng(20261123)
    X_noise = rng.normal(0.0, 1.0, size=(75, 20))
    y_noise = rng.integers(0, 2, size=75)
    x_act = rng.normal(0.0, 1.0, size=20)
    nombres = [f"var_{i}" for i in range(20)]

    exp = explorar_patrones_jerarquicos(
        X_noise,
        y_noise,
        x_act,
        nombres_variables=nombres,
        orden_maximo=5,
    )
    dpp = destilar_patrones_ortogonales_dpp(exp["patrones_candidatos"], k=10)
    fdr = aplicar_aduana_benjamini_hochberg(
        dpp["patrones_ortogonales"],
        m_total_explorados=max(1, exp["n_hipotesis_evaluadas"]),
        q_max=0.05,
    )
    assert fdr["n_validados_oos_fdr"] == 0
    for p in fdr["patrones"]:
        assert p["estado_validacion"] == "hipotesis_en_observacion"
        assert p["peso_produccion"] == 0.0


def test_invariancia_bit_a_bit_con_banderas_apagadas():
    """
    10. Verificar que las 6 banderas de motor_autonomo_ronda19 nacen en `false` en
        umbrales_cobertura.yaml y que la salida de producción (analizar + Top 5) es
        100 % idéntica bit a bit.
    """
    import decision_cobertura as dc
    import motor_mercados as mm
    import servicio_mercados as sm

    cfg = cargar_config_motor_autonomo()
    for fk in BANDERAS_MOTOR_AUTONOMO_DEFECTO:
        assert cfg[fk] is False
        assert dc.bandera_activa(fk) is False

    raiz = Path(__file__).resolve().parent.parent
    ln_docs = json.loads((raiz / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    dl, dv = ln_docs["londrina"], ln_docs["nautico"]

    an1 = mm.analizar(dl, dv, "Londrina", "Náutico", condicion_local="casa", condicion_visitante="fuera", competicion="Série C")
    top5_1, s1_1, _ = sm.obtener_top5_y_seccion1_filtrados(an1, an1.metadatos)

    # Ejecutar motor autónomo en sombra y volver a correr producción
    sombra = ejecutar_motor_autonomo_sombra(
        [
            {"id_partido": f"h{i}", "fecha": f"2026-09-{1 + (i % 28):02d}", "goles_favor": 1 + (i % 3), "goles_contra": i % 2, "xg": 1.3, "corners": 5}
            for i in range(35)
        ],
        {"id_partido": "act", "fecha": "2026-11-12", "xg": 1.3, "corners": 5},
        cutoff_timestamp="2026-11-12",
    )
    assert sombra["modifica_produccion"] is False

    an2 = mm.analizar(dl, dv, "Londrina", "Náutico", condicion_local="casa", condicion_visitante="fuera", competicion="Série C")
    top5_2, s1_2, _ = sm.obtener_top5_y_seccion1_filtrados(an2, an2.metadatos)

    assert an1.motor.tasas.lambda_local_ft == an2.motor.tasas.lambda_local_ft
    assert an1.motor.tasas.lambda_visitante_ft == an2.motor.tasas.lambda_visitante_ft
    assert an1.motor.matriz_ft == an2.motor.matriz_ft
    assert [t["etiqueta"] for t in top5_1] == [t["etiqueta"] for t in top5_2]
    assert [t["probabilidad"] for t in top5_1] == [t["probabilidad"] for t in top5_2]
    assert [s.probabilidad for s in s1_1] == [s.probabilidad for s in s1_2]
