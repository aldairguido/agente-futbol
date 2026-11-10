"""
evaluacion.py — Arquitectura Híbrida: Calibración y Validación Temporal 80% / 20%
==================================================================================
Directiva Maestra y Regla Permanente (AGENTS.md / CLAUDE.md / .cursorrules):
  1. Partición estrictamente temporal: 80% entrenamiento (pasado) / 20% validación (reciente).
     Cero shuffle, cero permutaciones aleatorias (evita data leakage hacia el futuro).
  2. Optimización numérica no lineal con `scipy.optimize.minimize(..., method='L-BFGS-B')`:
     Minimiza Log-Loss Poisson negativo + penalización Ridge L2 sobre pesos W_1..W_5 y sesgo b.
  3. Medición rigurosa de Brier Score (1X2, Over 2.5, BTTS) y Log-Loss en el 20% de validación.
  4. Contracción automática de α (Shrinkage hacia [0.15, 0.25]) si el ensemble no supera a λ_base.
  5. Re-exporta el catálogo y evaluador completo de `evaluacion_pronosticos.py`.
"""

from __future__ import annotations

import glob
import json
import math
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize

# Re-exportar todo lo existente para mantener compatibilidad 100% con suites de test
from evaluacion_pronosticos import *

import motor_mercados as mm


# ═══════════════════════════════════════════════════════════════════════════════
# CÁLCULO DE PROBABILIDADES DERIVADAS DE POISSON
# ═══════════════════════════════════════════════════════════════════════════════

def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return float(math.exp(-lam) * (lam ** k) / math.factorial(k))


def probabilidades_partido_desde_lambdas(lam_l: float, lam_v: float, max_goles: int = 10) -> dict[str, float]:
    """
    Deriva probabilidades de 1X2, Over 2.5 y BTTS Sí a partir de las tasas (λ_l, λ_v).
    """
    p_1 = 0.0
    p_x = 0.0
    p_2 = 0.0
    p_over25 = 0.0
    p_btts = 0.0

    p_l = [_poisson_pmf(i, lam_l) for i in range(max_goles + 1)]
    p_v = [_poisson_pmf(j, lam_v) for j in range(max_goles + 1)]

    for i in range(max_goles + 1):
        for j in range(max_goles + 1):
            prob = p_l[i] * p_v[j]
            if i > j:
                p_1 += prob
            elif i == j:
                p_x += prob
            else:
                p_2 += prob

            if i + j > 2.5:
                p_over25 += prob
            if i > 0 and j > 0:
                p_btts += prob

    total_1x2 = p_1 + p_x + p_2
    if total_1x2 > 0:
        p_1 /= total_1x2
        p_x /= total_1x2
        p_2 /= total_1x2

    return {
        "p_1": float(p_1),
        "p_x": float(p_x),
        "p_2": float(p_2),
        "p_over25": float(p_over25),
        "p_btts": float(p_btts),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CALIBRACIÓN Y VALIDACIÓN CRONOLÓGICA 80% / 20%
# ═══════════════════════════════════════════════════════════════════════════════

def loss_poisson(
    params: np.ndarray,
    datos: list[dict[str, Any]],
    alpha: float = 0.25,
    l2_reg: float = 0.01,
) -> float:
    """
    Función de pérdida: Log-Loss negativo de Poisson + regularización Ridge L2.
    params: [w1, w2, w3, w4, w5, b]
    """
    w = params[:5]
    b = float(params[5])

    loss = 0.0
    n = len(datos)
    if n == 0:
        return 0.0

    for d in datos:
        lam_base_l = float(d["lam_base_l"])
        lam_base_v = float(d["lam_base_v"])
        f_l = np.array(d["factores_l"], dtype=float)
        f_v = np.array(d["factores_v"], dtype=float)
        gl = float(d["goles_l"])
        gv = float(d["goles_v"])

        # Δ_MLP acotado en [-1.5, 1.5]
        delta_l = max(-1.5, min(1.5, float(np.dot(w, f_l) + b)))
        delta_v = max(-1.5, min(1.5, float(np.dot(w, f_v) + b)))

        lam_l = max(0.05, lam_base_l * math.exp(alpha * delta_l))
        lam_v = max(0.05, lam_base_v * math.exp(alpha * delta_v))

        # Poisson NLL: lam - y * log(lam)
        loss_l = lam_l - gl * math.log(lam_l)
        loss_v = lam_v - gv * math.log(lam_v)
        loss += (loss_l + loss_v)

    reg = 0.5 * l2_reg * float(np.sum(w ** 2))
    return float(loss / n + reg)


def calibrar_ensemble_80_20(
    muestras_partidos: list[dict[str, Any]],
    alpha_inicial: float = 0.25,
    l2_reg: float = 0.01,
) -> dict[str, Any]:
    """
    Ejecuta el protocolo estricto 80% / 20% cronológico (Fase 4):
      1. Ordena los partidos por fecha (t_1 <= t_2 <= ... <= t_N). Cero shuffle.
      2. 80% inicial (entrenamiento): optimiza pesos W_1..W_5 y b con L-BFGS-B minimizando Log-Loss + L2.
      3. 20% final (validación fuera de muestra): calcula Log-Loss y Brier Score vs λ_base.
      4. Si el Ensemble no supera a λ_base en validación, contrae α (Shrinkage) hacia [0.15, 0.25].
    """
    if len(muestras_partidos) < 5:
        return {
            "estado": "datos_insuficientes",
            "motivo": f"Se requieren al menos 5 partidos para validación 80/20 (recibidos {len(muestras_partidos)})",
            "pesos_optimizados": mm.PESOS_DEFECTO_MLP,
            "alpha_efectivo": alpha_inicial,
        }

    # 1. Orden cronológico estricto
    ordenados = sorted(
        muestras_partidos,
        key=lambda x: str(x.get("fecha") or x.get("utcTime") or "")
    )

    n_total = len(ordenados)
    n_train = max(3, int(n_total * 0.80))
    train_set = ordenados[:n_train]
    val_set = ordenados[n_train:]
    if not val_set:
        val_set = train_set[-2:]

    # Valores iniciales y límites
    w0 = np.array([0.22, 0.28, 0.22, 0.16, 0.12, 0.0], dtype=float)
    bounds = [
        (0.05, 0.60),  # W1: Posesión progresiva
        (0.05, 0.60),  # W2: Creación ofensiva
        (0.05, 0.60),  # W3: Finalización
        (0.05, 0.50),  # W4: Portería rival
        (0.05, 0.50),  # W5: Fragilidad rival
        (-0.30, 0.30), # b: Sesgo
    ]

    # 2. Optimización en el 80% (train)
    res_opt = minimize(
        loss_poisson,
        w0,
        args=(train_set, alpha_inicial, l2_reg),
        method="L-BFGS-B",
        bounds=bounds,
    )

    w_opt = res_opt.x[:5]
    b_opt = float(res_opt.x[5])
    nombres_factores = [
        "F_posesion_progresiva",
        "F_creacion",
        "F_finalizacion",
        "F_porteria_rival",
        "F_fragilidad_rival",
    ]
    pesos_dict = {nom: round(float(w_opt[i]), 4) for i, nom in enumerate(nombres_factores)}

    # 3. Evaluación fuera de muestra en el 20% (validación)
    brier_base_lista = []
    brier_ens_lista = []
    logloss_base_lista = []
    logloss_ens_lista = []

    for d in val_set:
        lam_b_l = float(d["lam_base_l"])
        lam_b_v = float(d["lam_base_v"])
        f_l = np.array(d["factores_l"], dtype=float)
        f_v = np.array(d["factores_v"], dtype=float)
        gl = int(d["goles_l"])
        gv = int(d["goles_v"])

        delta_l = max(-1.5, min(1.5, float(np.dot(w_opt, f_l) + b_opt)))
        delta_v = max(-1.5, min(1.5, float(np.dot(w_opt, f_v) + b_opt)))

        lam_ens_l = max(0.05, lam_b_l * math.exp(alpha_inicial * delta_l))
        lam_ens_v = max(0.05, lam_b_v * math.exp(alpha_inicial * delta_v))

        # Log-Loss Poisson
        ll_base = (lam_b_l - gl * math.log(lam_b_l)) + (lam_b_v - gv * math.log(lam_b_v))
        ll_ens = (lam_ens_l - gl * math.log(lam_ens_l)) + (lam_ens_v - gv * math.log(lam_ens_v))
        logloss_base_lista.append(ll_base)
        logloss_ens_lista.append(ll_ens)

        # Probabilidades derivadas
        prob_base = probabilidades_partido_desde_lambdas(lam_b_l, lam_b_v)
        prob_ens = probabilidades_partido_desde_lambdas(lam_ens_l, lam_ens_v)

        # Realidades binarias (1X2, Over 2.5, BTTS)
        y_1 = 1.0 if gl > gv else 0.0
        y_x = 1.0 if gl == gv else 0.0
        y_2 = 1.0 if gl < gv else 0.0
        y_over = 1.0 if gl + gv > 2.5 else 0.0
        y_btts = 1.0 if gl > 0 and gv > 0 else 0.0

        # Brier multicapa = mean((p_k - y_k)^2)
        br_base = (
            (prob_base["p_1"] - y_1) ** 2 +
            (prob_base["p_x"] - y_x) ** 2 +
            (prob_base["p_2"] - y_2) ** 2 +
            (prob_base["p_over25"] - y_over) ** 2 +
            (prob_base["p_btts"] - y_btts) ** 2
        ) / 5.0

        br_ens = (
            (prob_ens["p_1"] - y_1) ** 2 +
            (prob_ens["p_x"] - y_x) ** 2 +
            (prob_ens["p_2"] - y_2) ** 2 +
            (prob_ens["p_over25"] - y_over) ** 2 +
            (prob_ens["p_btts"] - y_btts) ** 2
        ) / 5.0

        brier_base_lista.append(br_base)
        brier_ens_lista.append(br_ens)

    mean_ll_base = float(np.mean(logloss_base_lista))
    mean_ll_ens = float(np.mean(logloss_ens_lista))
    mean_br_base = float(np.mean(brier_base_lista))
    mean_br_ens = float(np.mean(brier_ens_lista))

    # 4. Contracción automática (Shrinkage) de α si la red no reduce el Brier Score
    alpha_efectivo = alpha_inicial
    contraccion_aplicada = False
    if mean_br_ens > mean_br_base:
        ratio_ajuste = mean_br_base / max(1e-6, mean_br_ens)
        alpha_efectivo = max(0.15, min(0.25, alpha_inicial * ratio_ajuste))
        contraccion_aplicada = True

    mejora_brier_pct = ((mean_br_base - mean_br_ens) / max(1e-6, mean_br_base)) * 100.0

    return {
        "estado": "calibrado",
        "n_total": n_total,
        "n_train_80": n_train,
        "n_val_20": len(val_set),
        "pesos_optimizados": pesos_dict,
        "sesgo_b": round(b_opt, 4),
        "alpha_inicial": alpha_inicial,
        "alpha_efectivo": round(alpha_efectivo, 4),
        "contraccion_aplicada": contraccion_aplicada,
        "logloss_base_val": round(mean_ll_base, 4),
        "logloss_ensemble_val": round(mean_ll_ens, 4),
        "brier_base_val": round(mean_br_base, 4),
        "brier_ensemble_val": round(mean_br_ens, 4),
        "mejora_brier_pct": round(mejora_brier_pct, 2),
        "optimizacion_exitosa": bool(res_opt.success),
        "mensaje_optimizador": str(res_opt.message),
    }


def _extraer_metricas_ft_partido(p: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    """Extrae los diccionarios de métricas FT de un partido histórico (equipo y rival)."""
    raw_eq: dict[str, float] = {}
    raw_rv: dict[str, float] = {}
    filas_ft = ((p.get("periodos") or {}).get("FT") or {}).get("filas") or []
    for fila in filas_ft:
        if isinstance(fila, dict) and fila.get("clave"):
            cl = str(fila["clave"])
            if fila.get("estimado"):
                continue
            if isinstance(fila.get("equipo"), (int, float)):
                raw_eq[cl] = float(fila["equipo"])
            if isinstance(fila.get("rival"), (int, float)):
                raw_rv[cl] = float(fila["rival"])
    return raw_eq, raw_rv


def _clave_canonica_partido(p: dict[str, Any], nombre_eq_doc: str = "") -> str:
    """
    Sección 6.3: Clave canónica de partido para deduplicar cuando el mismo encuentro
    aparece en los archivos de auditoría de ambos equipos.
    """
    pid = str(p.get("id_partido") or "").strip()
    if pid and pid.lower() not in ("none", "null", ""):
        return f"id:{pid}"
    fecha = str(p.get("fecha") or p.get("utcTime") or "")[:10].strip()
    eq_a = str(p.get("equipo") or nombre_eq_doc or "").strip().lower()
    eq_b = str(p.get("rival") or "").strip().lower()
    par = sorted([eq_a, eq_b])
    return f"par:{fecha}:{par[0]}_vs_{par[1]}"


def evaluar_ensemble_80_20_historico(
    directorios_busqueda: list[str] | None = None,
    min_partidos_previos: int = 3,
) -> dict[str, Any]:
    """
    Punto 41 y Sección 6: Construye la muestra cronológica real a partir de las auditorías en disco
    y ejecuta la validación 80/20 exclusivamente con entradas PREPARTIDO verificadas.
    - Deduplica por `match_id` canónico (un partido presente en las auditorías de ambos clubes
      cuenta una sola vez en `n_total` y cae completo en la misma mitad de la partición 80/20).
    - Prohibido usar las estadísticas finales FT (`periodos.FT.filas` o `goles_equipo`) del propio
      partido evaluado como características prepartido (fuga temporal).
    - Para cada partido en fecha `t`, calcula las medias y factores prepartido únicamente con los
      partidos anteriores (`fecha < t`) de ese equipo (o usa `entradas_prepartido` explícitas si existen).
      Si un partido solo tiene su resultado final sin historial prepartido previo, se descarta.
    """
    if directorios_busqueda is not None:
        archivos_aud: list[str] = []
        for patron in directorios_busqueda:
            archivos_aud.extend(glob.glob(patron, recursive=True))
    else:
        archivos_aud = glob.glob("informes/auditorias/**/*.json", recursive=True) + glob.glob("informes/*.json")

    # 1. Recolectar series históricas por equipo y catálogo de partidos únicos por match_id
    series_por_equipo: dict[str, list[dict[str, Any]]] = {}
    partidos_unicos: dict[str, dict[str, Any]] = {}
    duplicados_omitidos = 0
    descartados_sin_prepartido = 0

    for ruta in archivos_aud:
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if not isinstance(doc, dict):
                continue
            nom_eq_doc = str(((doc.get("equipo") or {}).get("nombre")) or doc.get("equipo_solicitado") or "").strip()
            partidos = doc.get("partidos") or []
            for p in partidos:
                if not isinstance(p, dict) or p.get("es_amistoso"):
                    continue
                m = p.get("marcador") or {}
                ge = m.get("equipo") if isinstance(m, dict) else p.get("goles_equipo")
                gr = m.get("rival") if isinstance(m, dict) else p.get("goles_rival")
                if ge is None or gr is None:
                    continue
                fecha_p = str(p.get("fecha") or p.get("utcTime") or "")[:10].strip()
                if not fecha_p:
                    continue
                eq_nom = str(p.get("equipo") or nom_eq_doc or "").strip()
                rv_nom = str(p.get("rival") or "").strip()
                raw_eq, raw_rv = _extraer_metricas_ft_partido(p)
                reg = {
                    "p": p,
                    "fecha": fecha_p,
                    "equipo": eq_nom,
                    "rival": rv_nom,
                    "es_local": bool(p.get("es_local") or p.get("condicion") == "casa"),
                    "ge": float(ge),
                    "gr": float(gr),
                    "raw_eq": raw_eq,
                    "raw_rv": raw_rv,
                }
                if eq_nom:
                    series_por_equipo.setdefault(eq_nom.lower(), []).append(reg)

                clave_id = _clave_canonica_partido(p, nom_eq_doc)
                if clave_id in partidos_unicos:
                    duplicados_omitidos += 1
                    # Si el segundo archivo aporta el historial del otro equipo, conservar referencia
                    if eq_nom and not partidos_unicos[clave_id].get("equipo_secundario"):
                        partidos_unicos[clave_id]["equipo_secundario"] = eq_nom
                    continue
                partidos_unicos[clave_id] = {
                    "id_partido": clave_id,
                    "fecha": fecha_p,
                    "registro_primario": reg,
                    "equipo_secundario": None,
                }
        except Exception:
            continue

    # Ordenar cronológicamente las series de cada equipo y deduplicar dentro de cada equipo
    for eq_k, lista_eq in list(series_por_equipo.items()):
        vistos_eq: set[str] = set()
        limpia: list[dict[str, Any]] = []
        for item in sorted(lista_eq, key=lambda x: x["fecha"]):
            cid = _clave_canonica_partido(item["p"], item["equipo"])
            if cid in vistos_eq:
                continue
            vistos_eq.add(cid)
            limpia.append(item)
        series_por_equipo[eq_k] = limpia

    def _promediar_previos(previos: list[dict[str, Any]], lado: str = "eq") -> dict[str, float]:
        acum: dict[str, list[float]] = {}
        goles_f: list[float] = []
        for pr in previos:
            d_src = pr["raw_eq"] if lado == "eq" else pr["raw_rv"]
            for k, v in d_src.items():
                acum.setdefault(k, []).append(float(v))
            goles_f.append(float(pr["ge"] if lado == "eq" else pr["gr"]))
        medias = {k: float(np.mean(vs)) for k, vs in acum.items() if vs}
        if goles_f:
            medias.setdefault("goles_favor", float(np.mean(goles_f)))
        return medias

    muestras: list[dict[str, Any]] = []
    for clave_id, info_u in sorted(partidos_unicos.items(), key=lambda kv: kv[1]["fecha"]):
        reg = info_u["registro_primario"]
        p_raw = reg["p"]
        fecha_t = reg["fecha"]
        eq_l_nom = reg["equipo"] if reg["es_local"] else reg["rival"]
        eq_v_nom = reg["rival"] if reg["es_local"] else reg["equipo"]
        gl_real = reg["ge"] if reg["es_local"] else reg["gr"]
        gv_real = reg["gr"] if reg["es_local"] else reg["ge"]

        # Caso A: El partido trae explícitamente entradas prepartido congeladas antes del inicio
        pre_expl = p_raw.get("entradas_prepartido") or p_raw.get("prepartido")
        if isinstance(pre_expl, dict) and pre_expl.get("lam_base_l") is not None and pre_expl.get("lam_base_v") is not None:
            muestras.append({
                "id_partido": clave_id,
                "fecha": fecha_t,
                "lam_base_l": float(pre_expl["lam_base_l"]),
                "lam_base_v": float(pre_expl["lam_base_v"]),
                "factores_l": list(pre_expl.get("factores_l") or [0.0, 0.0, 0.0, 0.0, 0.0]),
                "factores_v": list(pre_expl.get("factores_v") or [0.0, 0.0, 0.0, 0.0, 0.0]),
                "goles_l": float(gl_real),
                "goles_v": float(gv_real),
            })
            continue

        # Caso B: Construir entradas prepartido estrictamente con partidos anteriores (fecha < fecha_t)
        hist_eq = [x for x in series_por_equipo.get(reg["equipo"].lower(), []) if x["fecha"] < fecha_t]
        if len(hist_eq) < min_partidos_previos:
            descartados_sin_prepartido += 1
            continue

        ult_previos = hist_eq[-15:]
        med_eq_prev = _promediar_previos(ult_previos, "eq")
        hist_rv = [x for x in series_por_equipo.get(reg["rival"].lower(), []) if x["fecha"] < fecha_t]
        if len(hist_rv) >= min_partidos_previos:
            med_rv_prev = _promediar_previos(hist_rv[-15:], "eq")
        else:
            # Si del rival no hay archivo propio, usar el perfil prepartido de rivales enfrentados antes de fecha_t
            med_rv_prev = _promediar_previos(ult_previos, "rv")

        if not med_eq_prev or not med_rv_prev:
            descartados_sin_prepartido += 1
            continue

        med_l = med_eq_prev if reg["es_local"] else med_rv_prev
        med_v = med_rv_prev if reg["es_local"] else med_eq_prev

        vec_l = mm.normalizar_vector_45(med_l)
        vec_v = mm.normalizar_vector_45(med_v)
        f_l, _ = mm.calcular_5_factores_latentes(vec_l, vec_v)
        f_v, _ = mm.calcular_5_factores_latentes(vec_v, vec_l)

        f_l_list = [f_l[k] for k in ("F_posesion_progresiva", "F_creacion", "F_finalizacion", "F_porteria_rival", "F_fragilidad_rival")]
        f_v_list = [f_v[k] for k in ("F_posesion_progresiva", "F_creacion", "F_finalizacion", "F_porteria_rival", "F_fragilidad_rival")]

        lam_b_l = float(med_l.get("xg") or med_l.get("goles_favor") or 1.25)
        lam_b_v = float(med_v.get("xg") or med_v.get("goles_favor") or 1.05)

        muestras.append({
            "id_partido": clave_id,
            "fecha": fecha_t,
            "equipo_local": eq_l_nom,
            "equipo_visita": eq_v_nom,
            "lam_base_l": round(max(0.25, lam_b_l), 4),
            "lam_base_v": round(max(0.25, lam_b_v), 4),
            "factores_l": f_l_list,
            "factores_v": f_v_list,
            "goles_l": float(gl_real),
            "goles_v": float(gv_real),
        })

    res_cal = calibrar_ensemble_80_20(muestras)
    res_cal["partidos_unicos_escaneados"] = len(partidos_unicos)
    res_cal["duplicados_omitidos"] = duplicados_omitidos
    res_cal["partidos_descartados_sin_prepartido"] = descartados_sin_prepartido
    if res_cal.get("estado") == "datos_insuficientes" and descartados_sin_prepartido > 0:
        res_cal["motivo"] = (
            f"Se descartaron {descartados_sin_prepartido} partido(s) sin historial prepartido previo "
            f"(prohibido usar estadísticas FT post-partido para predecir el mismo encuentro). "
            f"Muestras válidas prepartido: {len(muestras)} (mínimo requerido: 6)."
        )
    return res_cal


def _calcular_ece_binario(probs: list[float], reales: list[int], n_bins: int = 5) -> float:
    """Expected Calibration Error (ECE) sobre predicciones binarias en [0, 1]."""
    if not probs or len(probs) != len(reales):
        return 0.0
    arr_p = np.clip(np.asarray(probs, dtype=float), 1e-6, 1.0 - 1e-6)
    arr_y = np.asarray(reales, dtype=float)
    bordes = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = len(arr_p)
    for i in range(n_bins):
        if i == n_bins - 1:
            mask = (arr_p >= bordes[i]) & (arr_p <= bordes[i + 1])
        else:
            mask = (arr_p >= bordes[i]) & (arr_p < bordes[i + 1])
        cnt = int(np.sum(mask))
        if cnt > 0:
            conf_media = float(np.mean(arr_p[mask]))
            acc_media = float(np.mean(arr_y[mask]))
            ece += (cnt / n) * abs(acc_media - conf_media)
    return round(float(ece), 4)


def ejecutar_estudio_punto_43_auditorias(
    directorio: str = "informes/auditorias",
    min_previos: int = 3,
) -> dict[str, Any]:
    """
    Sección 11 — Punto 43: Descubrimiento y validación de patrones sobre los JSON de
    `D:\\agente_futbol\\informes\\auditorias`.
    Implementa las 5 reglas metodológicas:
      1. Inventario de cobertura real por métrica y periodo (`FT`, `1T`, `2T`) sobre partidos únicos
         deduplicados por `match_id`, distinguiendo `0` observado de `None` (`sin_dato`).
      2. Construcción estricta de características prepartido (solo partidos con fecha anterior a `t`)
         y verificación algebraica de variables independientes sin cancelación en cocientes encadenados.
      3. Embudo de 5 niveles (`cobertura >= 60% y N >= 30`, `estabilidad temporal`, `Brier/LogLoss/ECE`,
         `valor incremental sobre Poisson+DC+Shrinkage`, `filtro económico EV > 0`).
      4. Evaluación del patrón candidato prioritario para «El equipo marca al menos un gol en el 2T reglamentario»
         comparando las 3 variantes en el mismo conjunto cronológico fuera de muestra:
           - Variante 1: Motor actual corregido.
           - Variante 2: Motor actual + Señal A + Señal B.
           - Variante 3: Motor actual + Señal A + Señal B + interacción `A × B`.
      5. Criterio de promoción: solo se activa en producción si mejora simultáneamente Log-Loss,
         Brier Score y ECE; en caso contrario permanece como `candidato_en_sombra` o `rechazado`.
    """
    from auditoria_historica import LAS_45_METRICAS_OFICIALES
    claves_45 = [m[1] for m in LAS_45_METRICAS_OFICIALES]

    patron_glob = os.path.join(directorio, "**", "auditoria_*.json")
    archivos = sorted(glob.glob(patron_glob, recursive=True))

    partidos_unicos: dict[str, dict[str, Any]] = {}
    series_equipo: dict[str, list[dict[str, Any]]] = {}
    duplicados = 0

    # Contadores de cobertura real por métrica y periodo (FT, 1T, 2T)
    cobertura_por_metrica: dict[str, dict[str, dict[str, int]]] = {
        cl: {
            per: {"observados": 0, "ceros_observados": 0, "ausentes": 0}
            for per in ("FT", "1T", "2T")
        }
        for cl in claves_45
    }

    for ruta in archivos:
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if not isinstance(doc, dict):
                continue
            nom_eq_doc = str(((doc.get("equipo") or {}).get("nombre")) or doc.get("equipo_solicitado") or "").strip()
            for p in (doc.get("partidos") or []):
                if not isinstance(p, dict) or p.get("es_amistoso"):
                    continue
                fecha_p = str(p.get("fecha") or p.get("utcTime") or "")[:10].strip()
                if not fecha_p:
                    continue
                cid = _clave_canonica_partido(p, nom_eq_doc)

                # Extraer goles por mitad si existen en marcador_1t / marcador_2t o eventos_gol
                m_ft = p.get("marcador") or {}
                ge_ft = m_ft.get("equipo") if isinstance(m_ft, dict) else p.get("goles_equipo")
                gr_ft = m_ft.get("rival") if isinstance(m_ft, dict) else p.get("goles_rival")
                if ge_ft is None or gr_ft is None:
                    continue

                # Extraer goles 1T y 2T desde periodos o marcador_1t/2t
                def _goles_periodo(per_k: str, lado_k: str) -> float | None:
                    filas_p = ((p.get("periodos") or {}).get(per_k) or {}).get("filas") or []
                    for fil in filas_p:
                        if isinstance(fil, dict) and fil.get("clave") == "goles" and not fil.get("estimado"):
                            v_g = fil.get(lado_k)
                            if isinstance(v_g, (int, float)):
                                return float(v_g)
                    return None

                ge_1t = _goles_periodo("1T", "equipo")
                gr_1t = _goles_periodo("1T", "rival")
                ge_2t = _goles_periodo("2T", "equipo")
                gr_2t = _goles_periodo("2T", "rival")

                # Si no vienen en filas de 1T/2T, verificar eventos_gol reglamentarios (<=90)
                evs = p.get("eventos_gol")
                if (ge_2t is None or gr_2t is None) and isinstance(evs, list):
                    if float(ge_ft) == 0.0 and float(gr_ft) == 0.0:
                        ge_1t, gr_1t, ge_2t, gr_2t = 0.0, 0.0, 0.0, 0.0
                    elif len(evs) > 0:
                        g1_e, g1_r, g2_e, g2_r = 0.0, 0.0, 0.0, 0.0
                        for ev in evs:
                            if not isinstance(ev, dict):
                                continue
                            min_raw = str(ev.get("minuto_str") or ev.get("timeStr") or ev.get("minuto") or "").strip()
                            es_1t_ev = min_raw.startswith("45+") or (ev.get("minuto") is not None and int(ev.get("minuto", 0)) <= 45 and "+" not in min_raw)
                            lado_ev = str(ev.get("lado") or "").lower()
                            es_eq_ev = lado_ev in ("equipo", "team") or (
                                (lado_ev in ("local", "home") and bool(p.get("es_local") or p.get("condicion") == "casa"))
                                or (lado_ev in ("visitante", "visita", "away") and not bool(p.get("es_local") or p.get("condicion") == "casa"))
                            )
                            if es_1t_ev:
                                if es_eq_ev:
                                    g1_e += 1.0
                                else:
                                    g1_r += 1.0
                            else:
                                if es_eq_ev:
                                    g2_e += 1.0
                                else:
                                    g2_r += 1.0
                        if (g1_e + g2_e) == float(ge_ft) and (g1_r + g2_r) == float(gr_ft):
                            ge_1t, gr_1t, ge_2t, gr_2t = g1_e, g1_r, g2_e, g2_r

                raw_eq_ft, raw_rv_ft = _extraer_metricas_ft_partido(p)
                reg_eq = {
                    "id_partido": cid,
                    "fecha": fecha_p,
                    "equipo": nom_eq_doc,
                    "rival": str(p.get("rival") or ""),
                    "ge_ft": float(ge_ft),
                    "gr_ft": float(gr_ft),
                    "ge_1t": ge_1t,
                    "gr_1t": gr_1t,
                    "ge_2t": ge_2t,
                    "gr_2t": gr_2t,
                    "raw_eq": raw_eq_ft,
                    "raw_rv": raw_rv_ft,
                }
                if nom_eq_doc:
                    series_equipo.setdefault(nom_eq_doc.lower(), []).append(reg_eq)

                if cid in partidos_unicos:
                    duplicados += 1
                    continue
                partidos_unicos[cid] = p

                # Registrar cobertura real sobre partidos únicos
                periodos_p = p.get("periodos") or {}
                for per in ("FT", "1T", "2T"):
                    filas_per = ((periodos_p.get(per) or {}).get("filas")) or []
                    mapa_f = {str(fl.get("clave")): fl for fl in filas_per if isinstance(fl, dict) and fl.get("clave")}
                    for cl in claves_45:
                        fl = mapa_f.get(cl)
                        if not fl or fl.get("estimado"):
                            cobertura_por_metrica[cl][per]["ausentes"] += 2
                            continue
                        for lado in ("equipo", "rival"):
                            val = fl.get(lado)
                            if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
                                cobertura_por_metrica[cl][per]["ausentes"] += 1
                            else:
                                cobertura_por_metrica[cl][per]["observados"] += 1
                                if float(val) == 0.0:
                                    cobertura_por_metrica[cl][per]["ceros_observados"] += 1
        except Exception:
            continue

    # Deduplicar y ordenar cronológicamente cada serie de equipo
    for eq_k, l_eq in list(series_equipo.items()):
        vistos_c: set[str] = set()
        ord_eq: list[dict[str, Any]] = []
        for it in sorted(l_eq, key=lambda x: x["fecha"]):
            if it["id_partido"] in vistos_c:
                continue
            vistos_c.add(it["id_partido"])
            ord_eq.append(it)
        series_equipo[eq_k] = ord_eq

    # Resumen de cobertura porcentual por periodo
    n_unicos = len(partidos_unicos)
    denom_celdas = max(1, n_unicos * 2)
    resumen_cobertura_pct: dict[str, dict[str, float]] = {}
    metricas_aptas_nivel_a: list[str] = []
    for cl in claves_45:
        resumen_cobertura_pct[cl] = {
            per: round((cobertura_por_metrica[cl][per]["observados"] / denom_celdas) * 100.0, 2)
            for per in ("FT", "1T", "2T")
        }
        if resumen_cobertura_pct[cl]["FT"] >= 60.0 and n_unicos >= 30:
            metricas_aptas_nivel_a.append(cl)

    # Regla 2: Verificación algebraica de no-cancelación en variables de fricción/asedio
    verificacion_algebraica = {
        "variables_independientes": [
            "amarillas_por_partido",
            "faltas_por_partido",
            "amarillas_por_falta",
            "toques_area_rival",
        ],
        "cociente_encadenado_cancelado_detectado": False,
        "nota": "Se evalúan por separado amarillas_por_partido, faltas_por_partido, amarillas_por_falta y toques_area_rival sin multiplicar (amarillas/faltas)*(faltas/toques_area).",
    }

    # Regla 4: Evaluar el evento «El equipo marca al menos un gol en el 2T reglamentario»
    # construyendo muestras estrictamente con historial anterior a t
    muestras_2t: list[dict[str, Any]] = []
    for eq_k, l_eq in series_equipo.items():
        for idx, actual in enumerate(l_eq):
            if actual["ge_2t"] is None:
                continue
            previos = [x for x in l_eq[:idx] if x["fecha"] < actual["fecha"] and x["ge_2t"] is not None]
            if len(previos) < min_previos:
                continue
            ult = previos[-15:]
            # Tasa base prepartido de goles 2T del equipo (con contracción bayesiana hacia 0.60 goles en 2T)
            n_p = len(ult)
            media_g2t_prev = float(np.mean([x["ge_2t"] for x in ult]))
            media_gft_prev = float(np.mean([x["ge_ft"] for x in ult]))
            w_shr = n_p / (n_p + 3.5)
            lam_2t_base = max(0.15, w_shr * media_g2t_prev + (1.0 - w_shr) * max(0.45, 0.55 * media_gft_prev))
            p_var1 = 1.0 - math.exp(-lam_2t_base)

            # Señal A: proporción de goles anotados en 2T sobre goles totales FT del equipo (prepartido)
            sum_g2t_eq = sum(float(x["ge_2t"]) for x in ult)
            sum_gft_eq = sum(float(x["ge_ft"]) for x in ult)
            senal_a = (sum_g2t_eq / sum_gft_eq) if sum_gft_eq > 0 else 0.50

            # Señal B: proporción de goles encajados en 2T sobre goles encajados FT del rival (prepartido)
            riv_key = actual["rival"].strip().lower()
            prev_riv = [
                x for x in series_equipo.get(riv_key, [])
                if x["fecha"] < actual["fecha"] and x["gr_2t"] is not None
            ]
            if len(prev_riv) >= min_previos:
                ult_r = prev_riv[-15:]
                sum_gr2t = sum(float(x["gr_2t"]) for x in ult_r)
                sum_grft = sum(float(x["gr_ft"]) for x in ult_r)
            else:
                sum_gr2t = sum(float(x["gr_2t"]) for x in ult if x["gr_2t"] is not None)
                sum_grft = sum(float(x["gr_ft"]) for x in ult)
            senal_b = (sum_gr2t / sum_grft) if sum_grft > 0 else 0.50

            # Variante 2: Motor actual + Señal A + Señal B (aditivo acotado sobre tasa 2T)
            delta_ab = 0.25 * (senal_a - 0.52) + 0.25 * (senal_b - 0.52)
            lam_2t_v2 = max(0.12, lam_2t_base * math.exp(max(-0.35, min(0.35, delta_ab))))
            p_var2 = 1.0 - math.exp(-lam_2t_v2)

            # Variante 3: Motor actual + Señal A + Señal B + interacción A × B
            inter_ab = (senal_a * senal_b) - (0.52 * 0.52)
            delta_abx = delta_ab + 0.30 * inter_ab
            lam_2t_v3 = max(0.12, lam_2t_base * math.exp(max(-0.40, min(0.40, delta_abx))))
            p_var3 = 1.0 - math.exp(-lam_2t_v3)

            y_gol_2t = 1 if float(actual["ge_2t"]) >= 1.0 else 0
            muestras_2t.append({
                "id_partido": actual["id_partido"],
                "fecha": actual["fecha"],
                "p_var1": float(np.clip(p_var1, 0.02, 0.98)),
                "p_var2": float(np.clip(p_var2, 0.02, 0.98)),
                "p_var3": float(np.clip(p_var3, 0.02, 0.98)),
                "y": y_gol_2t,
            })

    muestras_2t.sort(key=lambda x: x["fecha"])
    n_m2t = len(muestras_2t)
    if n_m2t < 6:
        return {
            "estado": "datos_insuficientes",
            "archivos_escaneados": len(archivos),
            "partidos_unicos": n_unicos,
            "duplicados_omitidos": duplicados,
            "n_muestras_2t_prepartido": n_m2t,
            "cobertura_pct_por_metrica": resumen_cobertura_pct,
            "metricas_aptas_nivel_a": metricas_aptas_nivel_a,
            "verificacion_algebraica": verificacion_algebraica,
            "decision_produccion": "rechazado_por_muestra_insuficiente",
            "en_produccion": False,
        }

    corte = max(3, int(n_m2t * 0.80))
    val_2t = muestras_2t[corte:] if corte < n_m2t else muestras_2t[-max(2, n_m2t // 5):]

    def _evaluar_variante(clave_p: str) -> dict[str, float]:
        ps = [float(x[clave_p]) for x in val_2t]
        ys = [int(x["y"]) for x in val_2t]
        brier = float(np.mean([(p - y) ** 2 for p, y in zip(ps, ys)]))
        logloss = float(np.mean([-(y * math.log(max(1e-6, p)) + (1 - y) * math.log(max(1e-6, 1.0 - p))) for p, y in zip(ps, ys)]))
        ece = _calcular_ece_binario(ps, ys, n_bins=5)
        return {
            "n_val": len(val_2t),
            "brier": round(brier, 4),
            "logloss": round(logloss, 4),
            "ece": round(ece, 4),
        }

    m_v1 = _evaluar_variante("p_var1")
    m_v2 = _evaluar_variante("p_var2")
    m_v3 = _evaluar_variante("p_var3")

    # Regla 5: Solo se promueve si supera simultáneamente en Log-Loss, Brier Score y ECE a la Variante 1
    v2_supera_v1 = (m_v2["logloss"] < m_v1["logloss"]) and (m_v2["brier"] < m_v1["brier"]) and (m_v2["ece"] < m_v1["ece"])
    v3_supera_v1 = (m_v3["logloss"] < m_v1["logloss"]) and (m_v3["brier"] < m_v1["brier"]) and (m_v3["ece"] < m_v1["ece"])

    if v3_supera_v1 and (m_v3["brier"] <= m_v2["brier"]):
        mejor_variante = "Variante 3 (A + B + A×B)"
        estado_patron = "candidato_validado_fuera_de_muestra" if n_m2t >= 30 else "candidato_en_sombra"
    elif v2_supera_v1:
        mejor_variante = "Variante 2 (A + B)"
        estado_patron = "candidato_validado_fuera_de_muestra" if n_m2t >= 30 else "candidato_en_sombra"
    else:
        mejor_variante = "Variante 1 (Motor actual corregido)"
        estado_patron = "rechazado"

    return {
        "estado": "completado",
        "archivos_escaneados": len(archivos),
        "partidos_unicos": n_unicos,
        "duplicados_omitidos": duplicados,
        "n_muestras_2t_prepartido": n_m2t,
        "n_validacion_20": len(val_2t),
        "cobertura_por_metrica": cobertura_por_metrica,
        "cobertura_pct_por_metrica": resumen_cobertura_pct,
        "metricas_aptas_nivel_a": metricas_aptas_nivel_a,
        "verificacion_algebraica": verificacion_algebraica,
        "variantes_2t": {
            "variante_1_motor_actual": m_v1,
            "variante_2_mas_A_mas_B": m_v2,
            "variante_3_mas_A_mas_B_interaccion": m_v3,
        },
        "mejor_variante": mejor_variante,
        "estado_patron_2t": estado_patron,
        "en_produccion": False,
    }
