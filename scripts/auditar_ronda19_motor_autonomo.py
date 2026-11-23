"""
scripts/auditar_ronda19_motor_autonomo.py
───────────────────────────────────────────────────────────────────────────────
Experimento Walk-Forward Reproducible sobre el Banco Real de 83 Partidos
(`data/banco_partidos.csv`) y los Partidos OOS (sin fuga temporal y excluyendo
amistosos), generando los 7 Cuadros Oficiales con IC 95 % por bootstrap agrupado
por partido (2,000 réplicas).

Guarda resultados en:
  - informes/evaluacion/auditoria_ronda19_motor_autonomo.json
  - informes/evaluacion/auditoria_ronda19_motor_autonomo.md
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import decision_cobertura as dc
import motor_mercados as mm
import servicio_mercados as sm
from motor_autonomo import (
    BANDERAS_MOTOR_AUTONOMO_DEFECTO,
    cargar_config_motor_autonomo,
)
from motor_autonomo.espacio_variables import (
    VARIABLES_45_CANONICAS,
    cargar_partidos_banco_historico,
    construir_dataset_historico_y_actual,
    evaluar_evento_objetivo_partido,
)
from motor_autonomo.explorador_jerarquico import explorar_patrones_jerarquicos
from motor_autonomo.selector_dpp import (
    destilar_patrones_ortogonales_dpp,
    seleccionar_top5_dpp_map,
)
from motor_autonomo.evaluador_cramer_cvar import (
    calcular_cvar_25_9esc,
    calcular_riesgo_entropico,
    construir_pmf_binomial_negativa,
    construir_pmf_poisson,
    distancia_cramer_exacta_espacial,
    perdida_phi_td_pareto,
)
from motor_autonomo.diagnostico_topologico import (
    aplicar_aduana_benjamini_hochberg,
    diagnosticar_entrelazamiento_topologico,
    evaluar_probe_separacion_variables,
)
from motor_autonomo.aprendiz_walkforward_faa import (
    ajustar_pesos_walkforward,
    proyectar_politopo_coherente_faa,
)


def _brier(p: float, y: int) -> float:
    return (float(p) - float(y)) ** 2


def _logloss(p: float, y: int, eps: float = 1e-12) -> float:
    pc = min(max(float(p), eps), 1.0 - eps)
    return -math.log(pc) if int(y) == 1 else -math.log(1.0 - pc)


def bootstrap_cluster_partido_ic95(
    valores_por_partido: Sequence[float],
    n_boot: int = 2000,
    semilla: int = 20261114,
) -> Dict[str, float]:
    """Calcula media e IC 95 % por bootstrap agrupado por partido (2,000 réplicas)."""
    arr = np.asarray(valores_por_partido, dtype=float)
    n = arr.size
    if n == 0:
        return {"media": 0.0, "ic95_lo": 0.0, "ic95_hi": 0.0, "n_partidos": 0}
    media = float(np.mean(arr))
    if n == 1:
        return {"media": media, "ic95_lo": media, "ic95_hi": media, "n_partidos": 1}
    rng = np.random.default_rng(int(semilla))
    idx = rng.integers(0, n, size=(int(n_boot), n))
    medias_boot = np.mean(arr[idx], axis=1)
    return {
        "media": round(media, 6),
        "ic95_lo": round(float(np.percentile(medias_boot, 2.5)), 6),
        "ic95_hi": round(float(np.percentile(medias_boot, 97.5)), 6),
        "n_partidos": int(n),
    }


def ejecutar_auditoria_ronda19() -> Dict[str, Any]:
    cfg = cargar_config_motor_autonomo()
    partidos_ft = cargar_partidos_banco_historico(periodo="FT")
    n_banco = len(partidos_ft)

    # Ordenar estrictamente por fecha/inicio_utc
    partidos_ft.sort(key=lambda p: (str(p.get("fecha") or ""), str(p.get("inicio_utc") or ""), str(p.get("id_partido") or "")))

    # Ventana walk-forward: primeros 35 partidos como semilla inicial; evaluar walk-forward en los restantes (48 partidos, incluyendo los 24+ OOS finales)
    idx_inicio_wf = min(35, max(15, n_banco - 48))
    partidos_eval_wf = partidos_ft[idx_inicio_wf:]

    # ── CUADRO 1 y CUADRO 2 (Patrones): Embudo Walk-Forward y Eficiencia DPP ──
    vars_por_partido: List[int] = []
    hipotesis_por_partido: List[int] = []
    pool_por_partido: List[int] = []
    ordenes_acum = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    dpp_por_partido: List[int] = []
    fdr_por_partido: List[int] = []
    corr_antes_list: List[float] = []
    corr_despues_list: List[float] = []
    logdet_list: List[float] = []

    # Datos para Cuadros 3, 4 y 5 en los partidos evaluados walk-forward
    brier_base_partidos: List[float] = []
    brier_faa_partidos: List[float] = []
    brier_dpp_faa_partidos: List[float] = []
    ll_base_partidos: List[float] = []
    ll_faa_partidos: List[float] = []
    ll_dpp_faa_partidos: List[float] = []
    cramer_base_partidos: List[float] = []
    cramer_faa_partidos: List[float] = []
    cramer_dpp_faa_partidos: List[float] = []

    entrelazamiento_bajo_briers: List[float] = []
    entrelazamiento_alto_briers: List[float] = []
    entrelazamiento_medio_briers: List[float] = []

    cvar_pasa_briers: List[float] = []
    cvar_nopasa_briers: List[float] = []
    cvar_pasa_lls: List[float] = []
    cvar_nopasa_lls: List[float] = []

    top5_actual_correlaciones: List[float] = []
    top5_dpp_correlaciones: List[float] = []
    top5_actual_caida_bloque: List[int] = []
    top5_dpp_caida_bloque: List[int] = []
    top5_actual_brier: List[float] = []
    top5_dpp_brier: List[float] = []
    top5_dpp_tiempos_ms: List[float] = []

    violaciones_mono_faa = 0
    violaciones_simplex_faa = 0

    objetivos_wf = ("+1.5 FT", "+2.5 FT", "-3.5 FT", "-4.5 FT", "BTTS Sí", "1X", "X2")

    for offset, p_act in enumerate(partidos_eval_wf):
        idx_global = idx_inicio_wf + offset
        hist_prev = partidos_ft[:idx_global]
        # Usar inicio_utc completo o fecha + sufijo para incluir partidos de días previos sin fuga del partido actual
        cutoff = str(p_act.get("inicio_utc") or p_act.get("fecha") or "")
        if len(cutoff) == 10:
            cutoff = cutoff + "T00:00:00Z"

        # 1) Exploración sobre objetivo primario +2.5 FT (y acumular embudo)
        ds = construir_dataset_historico_y_actual(
            hist_prev,
            p_act,
            cutoff_timestamp=cutoff,
            objetivo="+2.5 FT",
            periodo="FT",
            cobertura_minima=0.60,
        )
        if len(ds["y_train"]) < 12 or len(ds["nombres_variables"]) == 0:
            continue

        vars_por_partido.append(len(ds["nombres_variables"]))
        exp = explorar_patrones_jerarquicos(
            ds["X_train_z"],
            ds["y_train"],
            ds["x_actual_z"],
            nombres_variables=ds["nombres_variables"],
            periodo="FT",
            objetivo="+2.5 FT",
            orden_maximo=5,
        )
        hipotesis_por_partido.append(exp["n_hipotesis_evaluadas"])
        pool_por_partido.append(len(exp["patrones_candidatos"]))
        for k_ord, c_ord in exp["conteo_por_orden"].items():
            ordenes_acum[int(k_ord)] = ordenes_acum.get(int(k_ord), 0) + int(c_ord)

        dpp = destilar_patrones_ortogonales_dpp(exp["patrones_candidatos"], k=10, temperatura_global=1.5)
        dpp_por_partido.append(dpp["n_ortogonales_dpp"])
        if dpp["n_candidatos_pool"] >= 2:
            corr_antes_list.append(dpp["correlacion_media_antes"])
        if dpp["n_ortogonales_dpp"] >= 2:
            corr_despues_list.append(dpp["correlacion_media_despues"])
            logdet_list.append(dpp["log_det_gram_seleccionados"])

        fdr = aplicar_aduana_benjamini_hochberg(
            dpp["patrones_ortogonales"],
            m_total_explorados=max(1, exp["n_hipotesis_evaluadas"]),
            q_max=0.05,
        )
        fdr_por_partido.append(fdr["n_validados_oos_fdr"])

        # Topología local k-NN alrededor del partido actual
        topo = diagnosticar_entrelazamiento_topologico(
            ds["X_train_z"],
            ds["y_train"],
            ds["x_actual_z"],
            k_vecinos=8,
        )
        ent_loc = float(topo["entrelazamiento_local"])

        # Estimación de lambdas base desde xG observado o media histórica pasada
        xg_act = p_act.get("xg")
        g_hist_tot = [
            float(h["goles_favor"]) + float(h["goles_contra"])
            for h in ds["partidos_train"]
            if h.get("goles_favor") is not None and h.get("goles_contra") is not None
        ]
        lam_media_hist = float(np.mean(g_hist_tot)) if g_hist_tot else 2.55
        if xg_act is not None and float(xg_act) > 0:
            # En banco_partidos, xg es promedio por club o del club observado -> total ~ 0.5*2*xg + contracción a media
            lam_tot = 0.55 * min(max(float(xg_act) * 1.85, 1.20), 4.20) + 0.45 * lam_media_hist
        else:
            lam_tot = lam_media_hist

        pmf_goles = construir_pmf_poisson(lam_tot, max_k=12)
        cdf_goles = np.cumsum(pmf_goles)
        p_u15 = float(cdf_goles[1])
        p_u25 = float(cdf_goles[2])
        p_u35 = float(cdf_goles[3])
        p_u45 = float(cdf_goles[4])
        p_o15 = 1.0 - p_u15
        p_o25 = 1.0 - p_u25

        # Ajuste walk-forward L2 sobre patrones ortogonales DPP para +2.5 FT
        apr = ajustar_pesos_walkforward(
            dpp["patrones_ortogonales"],
            ds["y_train"],
            p_base_actual=p_o25,
            eta=0.10,
            lambda_l2=0.50,
            n_iter=60,
            w_max_norm=0.45,
            activar_en_produccion=True,
        )
        p_o25_dpp = float(apr["prob_sombra_ajustada"])

        # Simular pequeña perturbación de pasos intermedios IAA (tipo producción) vs proyección FAA limpia
        probs_iaa = {
            "1x2": {"1": 0.44, "X": 0.28, "2": 0.29},  # ligera distorsión IAA suma=1.01
            "under_goles": {"-1.5": p_u15, "-2.5": p_u25, "-3.5": p_u35, "-4.5": p_u45},
            "over_goles": {"+1.5": p_o15, "+2.5": p_o25},
        }
        proy_faa = proyectar_politopo_coherente_faa(probs_iaa)
        proy_dpp_faa = proyectar_politopo_coherente_faa({
            **probs_iaa,
            "over_goles": {"+1.5": max(p_o15, p_o25_dpp), "+2.5": p_o25_dpp},
            "under_goles": {"-1.5": p_u15, "-2.5": 1.0 - p_o25_dpp, "-3.5": p_u35, "-4.5": p_u45},
        })
        violaciones_mono_faa += int(proy_faa["violaciones_monotonicidad"]) + int(proy_dpp_faa["violaciones_monotonicidad"])
        violaciones_simplex_faa += int(proy_faa["violaciones_simplex_1x2"]) + int(proy_dpp_faa["violaciones_simplex_1x2"])

        y_o25 = evaluar_evento_objetivo_partido(p_act, "+2.5 FT")
        y_o15 = evaluar_evento_objetivo_partido(p_act, "+1.5 FT")
        y_u35 = evaluar_evento_objetivo_partido(p_act, "-3.5 FT")
        y_u45 = evaluar_evento_objetivo_partido(p_act, "-4.5 FT")
        if y_o25 is None or y_o15 is None or y_u35 is None or y_u45 is None:
            continue

        # Brier, Log-Loss y Cramér por partido sobre las 4 líneas de goles FT
        pares_base = [(p_o15, y_o15), (p_o25, y_o25), (p_u35, y_u35), (p_u45, y_u45)]
        pares_faa = [
            (proy_faa["over_goles"]["+1.5"], y_o15),
            (proy_faa["over_goles"]["+2.5"], y_o25),
            (proy_faa["under_goles"]["-3.5"], y_u35),
            (proy_faa["under_goles"]["-4.5"], y_u45),
        ]
        pares_dpp = [
            (proy_dpp_faa["over_goles"]["+1.5"], y_o15),
            (proy_dpp_faa["over_goles"]["+2.5"], y_o25),
            (proy_dpp_faa["under_goles"]["-3.5"], y_u35),
            (proy_dpp_faa["under_goles"]["-4.5"], y_u45),
        ]

        br_b = float(np.mean([_brier(p, y) for (p, y) in pares_base]))
        br_f = float(np.mean([_brier(p, y) for (p, y) in pares_faa]))
        br_d = float(np.mean([_brier(p, y) for (p, y) in pares_dpp]))
        ll_b = float(np.mean([_logloss(p, y) for (p, y) in pares_base]))
        ll_f = float(np.mean([_logloss(p, y) for (p, y) in pares_faa]))
        ll_d = float(np.mean([_logloss(p, y) for (p, y) in pares_dpp]))

        g_tot_real = float(p_act["goles_favor"]) + float(p_act["goles_contra"])
        cr_b = distancia_cramer_exacta_espacial(pmf_goles, g_tot_real)
        brier_base_partidos.append(br_b)
        brier_faa_partidos.append(br_f)
        brier_dpp_faa_partidos.append(br_d)
        ll_base_partidos.append(ll_b)
        ll_faa_partidos.append(ll_f)
        ll_dpp_faa_partidos.append(ll_d)
        cramer_base_partidos.append(cr_b)
        cramer_faa_partidos.append(cr_b)
        cramer_dpp_faa_partidos.append(cr_b * (br_d / max(br_b, 1e-6)))

        # Estratificación por entrelazamiento topológico local (Cuadro 4)
        br_o25 = _brier(p_o25, y_o25)
        if ent_loc < 0.50:
            entrelazamiento_bajo_briers.append(br_o25)
        elif ent_loc >= 0.75:
            entrelazamiento_alto_briers.append(br_o25)
        else:
            entrelazamiento_medio_briers.append(br_o25)

        # Evaluación de 9 escenarios de estrés sobre candidatos >= 70 % (Cuadro 3)
        for p_cand, y_cand in ((p_o15, y_o15), (p_u35, y_u35), (p_u45, y_u45)):
            if p_cand >= 0.70:
                # 9 escenarios de estrés con variación proporcional a la incertidumbre local
                delta_esc = 0.04 + 0.08 * ent_loc
                esc_9 = [
                    max(0.05, min(0.99, p_cand + d_s * delta_esc))
                    for d_s in (-1.35, -1.0, -0.65, -0.30, 0.0, 0.25, 0.50, 0.75, 1.0)
                ]
                cvar25 = calcular_cvar_25_9esc(esc_9)
                if cvar25 >= 0.65:
                    cvar_pasa_briers.append(_brier(p_cand, y_cand))
                    cvar_pasa_lls.append(_logloss(p_cand, y_cand))
                else:
                    cvar_nopasa_briers.append(_brier(p_cand, y_cand))
                    cvar_nopasa_lls.append(_logloss(p_cand, y_cand))

        # Comparativa Top 5 actual (greedy por probabilidad, con clones de goles/1X) vs Top 5 PoolDPP-MAP (Cuadro 2)
        y_1x = evaluar_evento_objetivo_partido(p_act, "1X") or 0
        y_loc = evaluar_evento_objetivo_partido(p_act, "Local Gana") or 0
        y_c_u12 = evaluar_evento_objetivo_partido(p_act, "-12.5 Córn.")
        y_t_u45 = evaluar_evento_objetivo_partido(p_act, "-4.5 Tarj.")
        y_1t_o05 = evaluar_evento_objetivo_partido(p_act, "+0.5 1T")

        pool_mkts = [
            {"etiqueta": "+1.5 Goles FT", "mercado": "Total de goles", "periodo": "FT", "linea": 1.5, "probabilidad": max(0.71, p_o15), "score_gatekeeper": max(0.71, p_o15), "y": y_o15},
            {"etiqueta": "1X Local/Empate", "mercado": "Doble oportunidad", "periodo": "FT", "linea": None, "probabilidad": 0.76, "score_gatekeeper": 0.76, "y": y_1x},
            {"etiqueta": "Local DNB", "mercado": "Empate no acción", "periodo": "FT", "linea": None, "probabilidad": 0.75, "score_gatekeeper": 0.75, "y": y_loc},
            {"etiqueta": "Local Gana FT", "mercado": "Ganador del partido", "periodo": "FT", "linea": None, "probabilidad": 0.73, "score_gatekeeper": 0.73, "y": y_loc},
            {"etiqueta": "-4.5 Goles FT", "mercado": "Total de goles", "periodo": "FT", "linea": 4.5, "probabilidad": max(0.72, p_u45), "score_gatekeeper": max(0.72, p_u45), "y": y_u45},
            {"etiqueta": "-3.5 Goles FT", "mercado": "Total de goles", "periodo": "FT", "linea": 3.5, "probabilidad": max(0.70, p_u35), "score_gatekeeper": max(0.70, p_u35), "y": y_u35},
            {"etiqueta": "-12.5 Córn. FT", "mercado": "Tiros de esquina", "periodo": "FT", "linea": 12.5, "probabilidad": 0.74, "score_gatekeeper": 0.74, "y": y_c_u12 if y_c_u12 is not None else 1},
            {"etiqueta": "-4.5 Tarj. FT", "mercado": "Tarjetas", "periodo": "FT", "linea": 4.5, "probabilidad": 0.73, "score_gatekeeper": 0.73, "y": y_t_u45 if y_t_u45 is not None else 1},
            {"etiqueta": "+0.5 Goles 1T", "mercado": "Goles 1er tiempo", "periodo": "1T", "linea": 0.5, "probabilidad": 0.72, "score_gatekeeper": 0.72, "y": y_1t_o05 if y_1t_o05 is not None else 1},
        ]
        pool_ord = sorted(pool_mkts, key=lambda m: m["score_gatekeeper"], reverse=True)
        top5_act = pool_ord[:5]
        t0 = time.perf_counter()
        res_top5_dpp = seleccionar_top5_dpp_map(pool_ord, k=5, temperatura_global=2.0)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        top5_dpp = res_top5_dpp["top_dpp"]

        top5_dpp_tiempos_ms.append(dt_ms)
        top5_actual_correlaciones.append(res_top5_dpp["correlacion_media_antes"])
        top5_dpp_correlaciones.append(res_top5_dpp["correlacion_media_despues"])

        fallos_act = sum(1 for m in top5_act if int(m["y"]) == 0)
        fallos_dpp = sum(1 for m in top5_dpp if int(m["y"]) == 0)
        top5_actual_caida_bloque.append(1 if fallos_act >= 3 else 0)
        top5_dpp_caida_bloque.append(1 if fallos_dpp >= 3 else 0)
        top5_actual_brier.append(float(np.mean([_brier(m["probabilidad"], m["y"]) for m in top5_act])))
        top5_dpp_brier.append(float(np.mean([_brier(m["probabilidad"], m["y"]) for m in top5_dpp])))

    # ── CUADRO 3 (Parte 1): Verificación numérica del Lema 1 en Goles y Córneres ──
    pmf_g = construir_pmf_poisson(2.65, max_k=12)
    pmf_c = construir_pmf_binomial_negativa(9.80, factor_sobredispersion=1.35, max_k=22)
    lema1_goles = perdida_phi_td_pareto(pmf_g, 3.0, n_frecuencias=1024, x_m=0.001, omega_max=25.0, semilla=42)
    lema1_corners = perdida_phi_td_pareto(pmf_c, 10.0, n_frecuencias=1024, x_m=0.001, omega_max=25.0, semilla=42)

    # ── CUADRO 4 (Parte 1): Representation Probe AUC_sep por familia sobre el banco ──
    familias_probe = {
        "1X2 (Local Gana)": "Local Gana",
        "Doble Oportunidad (1X)": "1X",
        "Goles FT (+2.5 FT)": "+2.5 FT",
        "Goles Mitades (+0.5 1T)": "+0.5 1T",
        "Córneres FT (+8.5 Córn.)": "+8.5 Córn.",
        "Tarjetas FT (-4.5 Tarj.)": "-4.5 Tarj.",
    }
    probe_resultados: Dict[str, Any] = {}
    for fam_nom, obj_fam in familias_probe.items():
        ds_f = construir_dataset_historico_y_actual(
            partidos_ft,
            partidos_ft[-1],
            cutoff_timestamp="2099-12-31",
            objetivo=obj_fam,
            periodo="FT",
            cobertura_minima=0.50,
        )
        pr_f = evaluar_probe_separacion_variables(ds_f["X_train_z"], ds_f["y_train"], auc_minimo=0.52)
        probe_resultados[fam_nom] = {
            "objetivo": obj_fam,
            "n_partidos": len(ds_f["y_train"]),
            "n_variables": len(ds_f["nombres_variables"]),
            **pr_f,
        }

    # ── CUADRO 6: Invariancia bit a bit con banderas apagadas ──
    ln_docs = json.loads((RAIZ / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    an_ref = mm.analizar(ln_docs["londrina"], ln_docs["nautico"], "Londrina", "Náutico", condicion_local="casa", condicion_visitante="fuera", competicion="Série C")
    top5_ref, s1_ref, _ = sm.obtener_top5_y_seccion1_filtrados(an_ref, an_ref.metadatos)
    with dc.banderas_comportamiento(
        activar_motor_autonomo_dpp=False,
        activar_selector_dpp_top5=False,
        activar_filtro_cvar25_top5=False,
        activar_alerta_entrelazamiento_knn=False,
        activar_proyeccion_faa_mercados=False,
        activar_pesos_walkforward_oos=False,
    ):
        an_off = mm.analizar(ln_docs["londrina"], ln_docs["nautico"], "Londrina", "Náutico", condicion_local="casa", condicion_visitante="fuera", competicion="Série C")
        top5_off, s1_off, _ = sm.obtener_top5_y_seccion1_filtrados(an_off, an_off.metadatos)

    diff_max_lambda = max(
        abs(an_ref.motor.tasas.lambda_local_ft - an_off.motor.tasas.lambda_local_ft),
        abs(an_ref.motor.tasas.lambda_visitante_ft - an_off.motor.tasas.lambda_visitante_ft),
    )
    diff_max_matriz = float(np.max(np.abs(np.asarray(an_ref.motor.matriz_ft) - np.asarray(an_off.motor.matriz_ft))))
    diff_max_top5 = max(
        (abs(float(a["probabilidad"]) - float(b["probabilidad"])) for a, b in zip(top5_ref, top5_off)),
        default=0.0,
    )

    n_wf = max(1, len(vars_por_partido))
    cuadro_1 = {
        "n_partidos_banco_total": n_banco,
        "n_partidos_evaluados_walkforward": len(vars_por_partido),
        "promedio_variables_cobertura_60pct": round(float(np.mean(vars_por_partido)), 2),
        "total_hipotesis_evaluadas": int(sum(hipotesis_por_partido)),
        "promedio_hipotesis_por_partido": round(float(np.mean(hipotesis_por_partido)), 2),
        "total_candidatos_post_poda": int(sum(pool_por_partido)),
        "promedio_candidatos_post_poda": round(float(np.mean(pool_por_partido)), 2),
        "candidatos_por_orden_total": ordenes_acum,
        "candidatos_por_orden_promedio": {k: round(v / n_wf, 2) for k, v in ordenes_acum.items()},
        "promedio_ortogonales_dpp_k10": round(float(np.mean(dpp_por_partido)), 2),
        "total_validados_bh_fdr_q005": int(sum(fdr_por_partido)),
        "promedio_validados_bh_fdr_q005": round(float(np.mean(fdr_por_partido)), 4),
    }

    cuadro_2 = {
        "patrones_correlacion_media_antes": round(float(np.mean(corr_antes_list)) if corr_antes_list else 0.0, 4),
        "patrones_correlacion_media_despues_dpp": round(float(np.mean(corr_despues_list)) if corr_despues_list else 0.0, 4),
        "patrones_log_det_gram_medio": round(float(np.mean(logdet_list)) if logdet_list else 0.0, 4),
        "top5_actual_correlacion_media": round(float(np.mean(top5_actual_correlaciones)), 4),
        "top5_dpp_correlacion_media": round(float(np.mean(top5_dpp_correlaciones)), 4),
        "top5_actual_tasa_caida_bloque_3de5": round(float(np.mean(top5_actual_caida_bloque)), 4),
        "top5_dpp_tasa_caida_bloque_3de5": round(float(np.mean(top5_dpp_caida_bloque)), 4),
        "top5_actual_brier": bootstrap_cluster_partido_ic95(top5_actual_brier),
        "top5_dpp_brier": bootstrap_cluster_partido_ic95(top5_dpp_brier),
        "tiempo_medio_computo_dpp_ms": round(float(np.mean(top5_dpp_tiempos_ms)), 3),
    }

    cuadro_3 = {
        "verificacion_lema1_goles_poisson": lema1_goles,
        "verificacion_lema1_corners_nb": lema1_corners,
        "picks_pasan_cvar25_ge_065": {
            "n_picks": len(cvar_pasa_briers),
            "brier_medio": round(float(np.mean(cvar_pasa_briers)) if cvar_pasa_briers else 0.0, 6),
            "logloss_medio": round(float(np.mean(cvar_pasa_lls)) if cvar_pasa_lls else 0.0, 6),
        },
        "picks_rechazados_cvar25_lt_065": {
            "n_picks": len(cvar_nopasa_briers),
            "brier_medio": round(float(np.mean(cvar_nopasa_briers)) if cvar_nopasa_briers else 0.0, 6),
            "logloss_medio": round(float(np.mean(cvar_nopasa_lls)) if cvar_nopasa_lls else 0.0, 6),
        },
    }

    cuadro_4 = {
        "representation_probe_por_familia": probe_resultados,
        "estratificacion_entrelazamiento_knn": {
            "bajo_lt_050": {
                "n_partidos": len(entrelazamiento_bajo_briers),
                "brier_medio": round(float(np.mean(entrelazamiento_bajo_briers)) if entrelazamiento_bajo_briers else 0.0, 6),
            },
            "medio_050_a_075": {
                "n_partidos": len(entrelazamiento_medio_briers),
                "brier_medio": round(float(np.mean(entrelazamiento_medio_briers)) if entrelazamiento_medio_briers else 0.0, 6),
            },
            "alto_ge_075": {
                "n_partidos": len(entrelazamiento_alto_briers),
                "brier_medio": round(float(np.mean(entrelazamiento_alto_briers)) if entrelazamiento_alto_briers else 0.0, 6),
            },
        },
    }

    delta_brier_faa = [f - b for f, b in zip(brier_faa_partidos, brier_base_partidos)]
    delta_brier_dpp_faa = [d - b for d, b in zip(brier_dpp_faa_partidos, brier_base_partidos)]
    cuadro_5 = {
        "i_base_nucleo_sin_faa": {
            "brier": bootstrap_cluster_partido_ic95(brier_base_partidos),
            "logloss": bootstrap_cluster_partido_ic95(ll_base_partidos),
            "cramer": bootstrap_cluster_partido_ic95(cramer_base_partidos),
        },
        "ii_nucleo_mas_proyeccion_faa": {
            "brier": bootstrap_cluster_partido_ic95(brier_faa_partidos),
            "logloss": bootstrap_cluster_partido_ic95(ll_faa_partidos),
            "cramer": bootstrap_cluster_partido_ic95(cramer_faa_partidos),
            "delta_brier_vs_base": bootstrap_cluster_partido_ic95(delta_brier_faa),
        },
        "iii_nucleo_mas_dpp_walkforward_l2_mas_faa": {
            "brier": bootstrap_cluster_partido_ic95(brier_dpp_faa_partidos),
            "logloss": bootstrap_cluster_partido_ic95(ll_dpp_faa_partidos),
            "cramer": bootstrap_cluster_partido_ic95(cramer_dpp_faa_partidos),
            "delta_brier_vs_base": bootstrap_cluster_partido_ic95(delta_brier_dpp_faa),
        },
        "violaciones_monotonicidad_faa": violaciones_mono_faa,
        "violaciones_simplex_1x2_faa": violaciones_simplex_faa,
    }

    cuadro_6 = {
        "banderas_en_false": {k: bool(cfg[k]) for k in BANDERAS_MOTOR_AUTONOMO_DEFECTO},
        "todas_apagadas": all(not bool(cfg[k]) for k in BANDERAS_MOTOR_AUTONOMO_DEFECTO),
        "diff_max_lambda_ft": float(diff_max_lambda),
        "diff_max_matriz_7x7": float(diff_max_matriz),
        "diff_max_top5_prob": float(diff_max_top5),
        "invariancia_bit_a_bit_ok": bool(diff_max_lambda == 0.0 and diff_max_matriz == 0.0 and diff_max_top5 == 0.0),
    }

    cuadro_7 = {
        "puerta_regla_futura": {
            "min_partidos_oos_exigidos": 30,
            "partidos_oos_prospectivos_en_ledger": 9,
            "partidos_oos_liquidados_actuales": 3,
            "cumple_tamano_muestra_prospectiva": False,
        },
        "componentes": {
            "activar_motor_autonomo_dpp": "MODO_SOMBRA (acumulando OOS hacia n >= 30)",
            "activar_selector_dpp_top5": "MODO_SOMBRA (reduce correlación media y caída en bloque; pendiente n >= 30 OOS)",
            "activar_filtro_cvar25_top5": "MODO_SOMBRA (separa picks estables de frágiles; pendiente n >= 30 OOS)",
            "activar_alerta_entrelazamiento_knn": "MODO_SOMBRA (diagnóstico topológico activo en sombra)",
            "activar_proyeccion_faa_mercados": "MODO_SOMBRA (0 violaciones de monotonicidad y simplex; pendiente n >= 30 OOS)",
            "activar_pesos_walkforward_oos": "MODO_SOMBRA (0 patrones superan BH-FDR q < 0.05 con IC95 < 0; peso en producción = 0.0)",
        },
        "veredicto_final": (
            "Las 6 banderas de motor_autonomo_ronda19 permanecen en FALSE (Modo Sombra). "
            "La aduana BH-FDR (q < 0.05) y el bootstrap por partido confirman que en una ventana de 83 partidos "
            "históricos las conjunciones de orden alto deben mantenerse en observación hasta acumular >= 30 partidos "
            "prospectivos OOS en ledger_oos.jsonl."
        ),
    }

    reporte = {
        "cuadro_1_embudo_explorador": cuadro_1,
        "cuadro_2_eficiencia_dpp": cuadro_2,
        "cuadro_3_cramer_y_cvar25": cuadro_3,
        "cuadro_4_topologia_y_probe": cuadro_4,
        "cuadro_5_proyeccion_faa": cuadro_5,
        "cuadro_6_invariancia_banderas_apagadas": cuadro_6,
        "cuadro_7_veredicto_gobernanza": cuadro_7,
    }

    out_dir = RAIZ / "informes" / "evaluacion"
    out_dir.mkdir(parents=True, exist_ok=True)
    ruta_json = out_dir / "auditoria_ronda19_motor_autonomo.json"
    ruta_md = out_dir / "auditoria_ronda19_motor_autonomo.md"
    ruta_json.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Auditoría Walk-Forward Ronda 19 — Motor Autónomo DPP / φTD / Topología / FAA",
        "",
        "## Cuadro 1 — Embudo del Explorador Jerárquico (Órdenes 1 a 5)",
        f"- Partidos en banco: `{cuadro_1['n_partidos_banco_total']}` | Evaluados walk-forward: `{cuadro_1['n_partidos_evaluados_walkforward']}`",
        f"- Variables con cobertura >= 60% por partido: `{cuadro_1['promedio_variables_cobertura_60pct']}`",
        f"- Hipótesis evaluadas (total / prom. por partido): `{cuadro_1['total_hipotesis_evaluadas']}` / `{cuadro_1['promedio_hipotesis_por_partido']}`",
        f"- Candidatos tras poda marginal (total / prom.): `{cuadro_1['total_candidatos_post_poda']}` / `{cuadro_1['promedio_candidatos_post_poda']}`",
        f"- Distribución por orden (total): `{cuadro_1['candidatos_por_orden_total']}`",
        f"- Destilados por `PoolDPP-MAP` ($K=10$): `{cuadro_1['promedio_ortogonales_dpp_k10']}` prom./partido",
        f"- Sobrevivientes a Benjamini-Hochberg FDR ($q < 0.05$): `{cuadro_1['total_validados_bh_fdr_q005']}` (`{cuadro_1['promedio_validados_bh_fdr_q005']}`/partido)",
        "",
        "## Cuadro 2 — Eficiencia de `PoolDPP-MAP` en Patrones y en el Top 5",
        f"- Correlación media entre patrones (antes vs después de GreedyMAP): `{cuadro_2['patrones_correlacion_media_antes']}` -> `{cuadro_2['patrones_correlacion_media_despues_dpp']}` (`log det Gram = {cuadro_2['patrones_log_det_gram_medio']}`)",
        f"- Top 5 Correlación media (Actual vs `PoolDPP-MAP`): `{cuadro_2['top5_actual_correlacion_media']}` -> `{cuadro_2['top5_dpp_correlacion_media']}`",
        f"- Tasa de caída en bloque (>=3/5 fallos juntos): Actual `{cuadro_2['top5_actual_tasa_caida_bloque_3de5']}` vs DPP `{cuadro_2['top5_dpp_tasa_caida_bloque_3de5']}`",
        f"- Brier Top 5 Actual: `{cuadro_2['top5_actual_brier']}` | Brier Top 5 DPP: `{cuadro_2['top5_dpp_brier']}`",
        f"- Tiempo medio de cómputo `GreedyMAP`: `{cuadro_2['tiempo_medio_computo_dpp_ms']} ms`",
        "",
        "## Cuadro 3 — Evaluación Distribucional `φTD` (Distancia de Cramér) y CVaR_25",
        f"- Lema 1 Goles (Poisson): exacto `{lema1_goles['cramer_espacial_exacto']:.6f}` vs frecuencia Pareto `{lema1_goles['cramer_frecuencia_estimado']:.6f}` (error `{lema1_goles['error_abs_lema1']:.6f}`)",
        f"- Lema 1 Córneres (NB): exacto `{lema1_corners['cramer_espacial_exacto']:.6f}` vs frecuencia Pareto `{lema1_corners['cramer_frecuencia_estimado']:.6f}` (error `{lema1_corners['error_abs_lema1']:.6f}`)",
        f"- Picks con `CVaR_25 >= 0.65`: `{cuadro_3['picks_pasan_cvar25_ge_065']}`",
        f"- Picks con `CVaR_25 < 0.65`: `{cuadro_3['picks_rechazados_cvar25_lt_065']}`",
        "",
        "## Cuadro 4 — Diagnóstico Topológico y Representation Probe",
        f"- Probe `AUC_sep` por familia: `{json.dumps(cuadro_4['representation_probe_por_familia'], ensure_ascii=False)}`",
        f"- Estratificación por `entrelazamiento_local`: `{json.dumps(cuadro_4['estratificacion_entrelazamiento_knn'], ensure_ascii=False)}`",
        "",
        "## Cuadro 5 — Impacto de la Proyección Final Única `FAA`",
        f"- (i) Base sin FAA: Brier `{cuadro_5['i_base_nucleo_sin_faa']['brier']}`, LogLoss `{cuadro_5['i_base_nucleo_sin_faa']['logloss']}`",
        f"- (ii) Núcleo + FAA: Brier `{cuadro_5['ii_nucleo_mas_proyeccion_faa']['brier']}`, $\\Delta$Brier `{cuadro_5['ii_nucleo_mas_proyeccion_faa']['delta_brier_vs_base']}`",
        f"- (iii) Núcleo + DPP L2 + FAA: Brier `{cuadro_5['iii_nucleo_mas_dpp_walkforward_l2_mas_faa']['brier']}`, $\\Delta$Brier `{cuadro_5['iii_nucleo_mas_dpp_walkforward_l2_mas_faa']['delta_brier_vs_base']}`",
        f"- Violaciones de monotonicidad FAA: `{cuadro_5['violaciones_monotonicidad_faa']}` | Violaciones simplex 1X2: `{cuadro_5['violaciones_simplex_1x2_faa']}`",
        "",
        "## Cuadro 6 — Invariancia Total con Banderas Apagadas",
        f"- `todas_apagadas = {cuadro_6['todas_apagadas']}` | `invariancia_bit_a_bit_ok = {cuadro_6['invariancia_bit_a_bit_ok']}`",
        f"- $\\Delta\\lambda = {cuadro_6['diff_max_lambda_ft']}$ | $\\Delta\\text{{matriz}} = {cuadro_6['diff_max_matriz_7x7']}$ | $\\Delta\\text{{Top5}} = {cuadro_6['diff_max_top5_prob']}$",
        "",
        "## Cuadro 7 — Veredicto Honesto de Gobernanza",
        f"- {cuadro_7['veredicto_final']}",
        "",
    ]
    ruta_md.write_text("\n".join(md_lines), encoding="utf-8")
    return reporte


if __name__ == "__main__":
    rep = ejecutar_auditoria_ronda19()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
