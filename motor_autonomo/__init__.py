"""
motor_autonomo/__init__.py
───────────────────────────────────────────────────────────────────────────────
Motor Autónomo de Descubrimiento Jerárquico de Patrones (Órdenes 1 a 5+),
Desduplicación Geométrica PoolDPP-MAP, Evaluación Distribucional φTD
(Cramér + CVaR_25), Diagnóstico Topológico de Entrelazamiento y Proyección
Coherente Final FAA.

Opera en Modo Sombra Dual bajo banderas apagadas por defecto (`false`) en
`umbrales_cobertura.yaml`.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

BANDERAS_MOTOR_AUTONOMO_DEFECTO: Dict[str, bool] = {
    "activar_motor_autonomo_dpp": False,
    "activar_selector_dpp_top5": False,
    "activar_filtro_cvar25_top5": False,
    "activar_alerta_entrelazamiento_knn": False,
    "activar_proyeccion_faa_mercados": False,
    "activar_pesos_walkforward_oos": False,
}

CONFIG_MOTOR_AUTONOMO_DEFECTO: Dict[str, Any] = {
    **BANDERAS_MOTOR_AUTONOMO_DEFECTO,
    "orden_maximo_patrones": 5,
    "cobertura_minima_variable": 0.60,
    "soporte_minimo_por_orden": {1: 12, 2: 10, 3: 8, 4: 8, 5: 8},
    "beam_width_por_orden": {1: 24, 2: 120, 3: 200, 4: 250, 5: 250},
    "ganancia_marginal_minima_ll": 0.005,
    "jaccard_maximo_padre_hijo": 0.95,
    "suavizado_laplace_alpha0": 4.0,
    "k_patrones_ortogonales": 10,
    "temperatura_global_dpp_patrones": 1.5,
    "temperatura_global_dpp_top5": 2.0,
    "cramer_n_frecuencias": 128,
    "cramer_pareto_xm": 0.001,
    "cramer_omega_max": 15.0,
    "cramer_semilla_fija": 42,
    "cvar25_umbral_minimo_top5": 0.65,
    "riesgo_entropico_eta": -4.0,
    "knn_entrelazamiento_k": 8,
    "entrelazamiento_umbral_cautela": 0.75,
    "fdr_benjamini_hochberg_q_max": 0.05,
    "probe_auc_minimo": 0.52,
    "min_partidos_oos_para_encender": 30,
    "min_partidos_oos_para_red_gelu": 250,
}


def cargar_config_motor_autonomo(ruta_yaml: Optional[str] = None) -> Dict[str, Any]:
    """Carga el bloque `motor_autonomo_ronda19` desde `umbrales_cobertura.yaml`."""
    from decision_cobertura import RUTA_UMBRALES_COBERTURA, bandera_activa

    cfg: Dict[str, Any] = {
        **CONFIG_MOTOR_AUTONOMO_DEFECTO,
        "soporte_minimo_por_orden": dict(CONFIG_MOTOR_AUTONOMO_DEFECTO["soporte_minimo_por_orden"]),
        "beam_width_por_orden": dict(CONFIG_MOTOR_AUTONOMO_DEFECTO["beam_width_por_orden"]),
    }
    path = ruta_yaml or RUTA_UMBRALES_COBERTURA
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            lineas = fh.read().splitlines()
        en_bloque = False
        sub_mapa: Optional[str] = None
        for ln in lineas:
            sin_com = ln.split("#")[0].rstrip()
            if not sin_com.strip():
                continue
            indent = len(sin_com) - len(sin_com.lstrip(" "))
            limpia = sin_com.strip()
            if indent == 0:
                en_bloque = (limpia == "motor_autonomo_ronda19:")
                sub_mapa = None
                continue
            if not en_bloque or ":" not in limpia:
                continue
            k, v = [x.strip() for x in limpia.split(":", 1)]
            if v == "":
                if k in ("soporte_minimo_por_orden", "beam_width_por_orden"):
                    sub_mapa = k
                else:
                    sub_mapa = None
                continue
            if indent <= 2:
                sub_mapa = None
            val: Any = None
            if re.match(r"^-?\d+$", v):
                val = int(v)
            elif re.match(r"^-?\d+\.\d+$", v):
                val = float(v)
            elif v.lower() in ("true", "false"):
                val = (v.lower() == "true")
            if val is not None:
                if sub_mapa and re.match(r"^\d+$", k):
                    cfg[sub_mapa][int(k)] = int(val)
                else:
                    cfg[k] = val

    for fk in BANDERAS_MOTOR_AUTONOMO_DEFECTO:
        if bandera_activa(fk):
            cfg[fk] = True
    return cfg


def ejecutar_motor_autonomo_sombra(
    partidos_historico: list[dict[str, Any]],
    partido_actual: dict[str, Any],
    *,
    cutoff_timestamp: str,
    objetivo: str = "+2.5 FT",
    periodo: str = "FT",
    probs_crudas_mercados: Optional[dict[str, Any]] = None,
    escenarios_9: Optional[list[float]] = None,
    ruta_yaml: Optional[str] = None,
) -> dict[str, Any]:
    """
    Fachada de extremo a extremo del Motor Autónomo (Capas 0 a 6) en Modo Sombra.
    Cuando todas las banderas están en `false`, devuelve el diagnóstico completo de
    sombra sin alterar ninguna probabilidad de producción.
    """
    from .espacio_variables import construir_dataset_historico_y_actual
    from .explorador_jerarquico import explorar_patrones_jerarquicos
    from .selector_dpp import destilar_patrones_ortogonales_dpp
    from .evaluador_cramer_cvar import evaluar_riesgo_escenarios_9
    from .diagnostico_topologico import (
        diagnosticar_entrelazamiento_topologico,
        evaluar_probe_separacion_variables,
        aplicar_aduana_benjamini_hochberg,
    )
    from .aprendiz_walkforward_faa import (
        ajustar_pesos_walkforward,
        proyectar_politopo_coherente_faa,
    )

    cfg = cargar_config_motor_autonomo(ruta_yaml)
    ds = construir_dataset_historico_y_actual(
        partidos_historico,
        partido_actual,
        cutoff_timestamp=cutoff_timestamp,
        objetivo=objetivo,
        periodo=periodo,
        cobertura_minima=float(cfg["cobertura_minima_variable"]),
    )
    exp = explorar_patrones_jerarquicos(
        ds["X_train_z"],
        ds["y_train"],
        ds["x_actual_z"],
        nombres_variables=ds["nombres_variables"],
        periodo=periodo,
        objetivo=objetivo,
        orden_maximo=int(cfg["orden_maximo_patrones"]),
        soporte_minimo_por_orden=cfg["soporte_minimo_por_orden"],
        beam_width_por_orden=cfg["beam_width_por_orden"],
        ganancia_marginal_minima_ll=float(cfg["ganancia_marginal_minima_ll"]),
        jaccard_maximo_padre_hijo=float(cfg["jaccard_maximo_padre_hijo"]),
        alpha0=float(cfg["suavizado_laplace_alpha0"]),
    )
    dpp = destilar_patrones_ortogonales_dpp(
        exp["patrones_candidatos"],
        k=int(cfg["k_patrones_ortogonales"]),
        temperatura_global=float(cfg["temperatura_global_dpp_patrones"]),
    )
    fdr = aplicar_aduana_benjamini_hochberg(
        dpp["patrones_ortogonales"],
        m_total_explorados=max(1, exp["n_hipotesis_evaluadas"]),
        q_max=float(cfg["fdr_benjamini_hochberg_q_max"]),
    )
    topo = diagnosticar_entrelazamiento_topologico(
        ds["X_train_z"],
        ds["y_train"],
        ds["x_actual_z"],
        k_vecinos=int(cfg["knn_entrelazamiento_k"]),
        umbral_cautela=float(cfg["entrelazamiento_umbral_cautela"]),
    )
    probe = evaluar_probe_separacion_variables(
        ds["X_train_z"],
        ds["y_train"],
        auc_minimo=float(cfg["probe_auc_minimo"]),
    )
    aprendiz = ajustar_pesos_walkforward(
        dpp["patrones_ortogonales"],
        ds["y_train"],
        p_base_train=ds["p_base_train"],
        p_base_actual=float(ds["p_base_actual"]),
        activar_en_produccion=bool(cfg["activar_pesos_walkforward_oos"]),
    )
    riesgo_9 = (
        evaluar_riesgo_escenarios_9(
            escenarios_9,
            eta=float(cfg["riesgo_entropico_eta"]),
            umbral_cvar25=float(cfg["cvar25_umbral_minimo_top5"]),
        )
        if escenarios_9
        else None
    )
    proy_faa = (
        proyectar_politopo_coherente_faa(probs_crudas_mercados)
        if isinstance(probs_crudas_mercados, dict)
        else None
    )
    return {
        "esquema": "motor-autonomo-r19-sombra-v1",
        "banderas_activas": {k: bool(cfg[k]) for k in BANDERAS_MOTOR_AUTONOMO_DEFECTO},
        "modifica_produccion": any(bool(cfg[k]) for k in BANDERAS_MOTOR_AUTONOMO_DEFECTO),
        "capa_0_espacio": {
            "n_train": len(ds["y_train"]),
            "n_variables_observadas": len(ds["nombres_variables"]),
            "nombres_variables": ds["nombres_variables"],
            "cutoff_timestamp": cutoff_timestamp,
        },
        "capa_1_explorador": {
            "n_hipotesis_evaluadas": exp["n_hipotesis_evaluadas"],
            "n_candidatos_pool": len(exp["patrones_candidatos"]),
            "conteo_por_orden": exp["conteo_por_orden"],
        },
        "capa_2_dpp": {
            "n_candidatos_pool": dpp["n_candidatos_pool"],
            "n_ortogonales_dpp": dpp["n_ortogonales_dpp"],
            "log_det_gram_seleccionados": dpp["log_det_gram_seleccionados"],
            "correlacion_media_antes": dpp["correlacion_media_antes"],
            "correlacion_media_despues": dpp["correlacion_media_despues"],
        },
        "capa_3_riesgo": riesgo_9,
        "capa_4_topologia_fdr": {
            "topologia": topo,
            "probe_separacion": probe,
            "n_validados_oos_fdr": fdr["n_validados_oos_fdr"],
            "patrones_evaluados_fdr": fdr["patrones"],
        },
        "capa_5_aprendiz": aprendiz,
        "capa_6_faa": proy_faa,
    }
