"""
motor_autonomo/explorador_jerarquico.py
───────────────────────────────────────────────────────────────────────────────
CAPA 1 — Explorador Jerárquico Podado de Órdenes 1 a 5 (Beam Search):
  - Predicados basados en terciles empíricos (BAJO <= Q33, MEDIO, ALTO >= Q67)
    aprendidos exclusivamente sobre los valores observados finitos de `X_train_z`.
  - `None` / `NaN` jamás se convierte en `0.0` ni activa ningún predicado.
  - Expansión en serie k = 1 -> 2 -> 3 -> 4 -> 5 con 3 reglas de poda estrictas:
      (1) Soporte mínimo por orden N_act(P') >= N_min(k+1).
      (2) No redundancia inmediata: Jaccard(P', padre) <= 0.95.
      (3) Ganancia marginal estricta: delta_LL(P') > delta_LL(padre) + eps_marg.
  - Lleva el conteo exacto `n_hipotesis_evaluadas` (M_total) para corrección BH-FDR.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def _log_loss_binario(p: float, y: int, eps: float = 1e-12) -> float:
    pc = min(max(float(p), eps), 1.0 - eps)
    return -math.log(pc) if int(y) == 1 else -math.log(1.0 - pc)


def p_valor_exacto_binomial(k_aciertos: int, n_act: int, pi0: float) -> float:
    """
    Calcula el p-valor exacto binomial unilateral respecto a la tasa base `pi0`
    en la dirección observada del patrón (enriquecimiento hacia 1 o hacia 0).
    """
    if n_act <= 0:
        return 1.0
    p0 = min(max(float(pi0), 1e-9), 1.0 - 1e-9)
    tasa = k_aciertos / n_act
    # Si la tasa observada supera pi0, cola superior P(K >= k); si es menor, cola inferior P(K <= k)
    if tasa >= p0:
        rango = range(int(k_aciertos), int(n_act) + 1)
        p_exito = p0
    else:
        rango = range(0, int(k_aciertos) + 1)
        p_exito = p0

    log_p = math.log(p_exito)
    log_q = math.log(1.0 - p_exito)
    log_n_fact = math.lgamma(n_act + 1)
    prob_cola = 0.0
    for j in rango:
        log_comb = log_n_fact - math.lgamma(j + 1) - math.lgamma(n_act - j + 1)
        prob_cola += math.exp(log_comb + j * log_p + (n_act - j) * log_q)
    return float(min(max(prob_cola, 0.0), 1.0))


def _evaluar_metricas_patron(
    mascara_act: np.ndarray,
    y_train: np.ndarray,
    pi0: float,
    alpha0: float = 4.0,
) -> Dict[str, Any]:
    """
    Calcula métricas estrictamente propias (Log-Loss, Brier, Cramér discreto binario,
    probabilidad suavizada Beta-Binomial y p-valor exacto) sobre las activaciones.
    """
    idx = np.flatnonzero(mascara_act)
    n_act = int(idx.size)
    if n_act == 0:
        return {
            "n_activaciones": 0,
            "k_aciertos": 0,
            "tasa_cruda": 0.0,
            "prob_suavizada_lap": float(pi0),
            "delta_ll": -1.0,
            "delta_brier": -1.0,
            "delta_cramer": -1.0,
            "score": 0.0,
            "p_valor_exacto_binomial": 1.0,
        }
    y_sub = y_train[idx]
    k_ac = int(np.sum(y_sub == 1))
    tasa_cruda = float(k_ac / n_act)
    p_lap = float((k_ac + alpha0 * pi0) / (n_act + alpha0))

    # Reducción media de Log-Loss y Brier frente a la climatología pi0
    ll_pi0 = np.array([_log_loss_binario(pi0, int(yv)) for yv in y_sub], dtype=float)
    ll_pat = np.array([_log_loss_binario(p_lap, int(yv)) for yv in y_sub], dtype=float)
    delta_ll = float(np.mean(ll_pi0 - ll_pat))

    br_pi0 = (pi0 - y_sub.astype(float)) ** 2
    br_pat = (p_lap - y_sub.astype(float)) ** 2
    delta_brier = float(np.mean(br_pi0 - br_pat))
    # En evento binario {0, 1}, la distancia de Cramér coincide con Brier
    delta_cramer = delta_brier

    score = max(0.0, delta_ll) * math.sqrt(n_act / (n_act + 10.0))
    # Si el patrón apunta a la clase 0 (p_lap < pi0), evaluar p-valor en esa cola
    pval = p_valor_exacto_binomial(k_ac, n_act, pi0)
    return {
        "n_activaciones": n_act,
        "k_aciertos": k_ac,
        "tasa_cruda": tasa_cruda,
        "prob_suavizada_lap": p_lap,
        "delta_ll": delta_ll,
        "delta_brier": delta_brier,
        "delta_cramer": delta_cramer,
        "score": float(score),
        "p_valor_exacto_binomial": float(pval),
    }


def _construir_predicados_atomicos(
    X_train_z: np.ndarray,
    x_actual_z: np.ndarray,
    nombres_variables: Sequence[str],
) -> List[Dict[str, Any]]:
    """
    Construye predicados atómicos (Orden 1) para cada variable cuyo valor en
    `x_actual_z` sea finito (nunca `None`/`NaN`), usando los terciles empíricos
    `Q33` y `Q67` aprendidos SOLO en `X_train_z`.
    """
    n_train, D = X_train_z.shape
    atomicos: List[Dict[str, Any]] = []
    for j in range(D):
        val_act = float(x_actual_z[j]) if (j < len(x_actual_z) and x_actual_z[j] is not None and np.isfinite(x_actual_z[j])) else np.nan
        if not np.isfinite(val_act):
            continue
        col = X_train_z[:, j]
        finitos = col[np.isfinite(col)]
        if finitos.size < 3:
            continue
        q33 = float(np.percentile(finitos, 33.3333))
        q67 = float(np.percentile(finitos, 66.6667))
        q25 = float(np.percentile(finitos, 25.0))
        q75 = float(np.percentile(finitos, 75.0))
        nom = str(nombres_variables[j])

        # Evaluar regímenes que se cumplen en el partido actual (P(x_actual) == 1)
        regimenes_activos: List[Tuple[str, np.ndarray]] = []
        if val_act >= q67:
            mask_alto = np.isfinite(col) & (col >= q67)
            regimenes_activos.append(("ALTO", mask_alto))
        if val_act <= q33:
            mask_bajo = np.isfinite(col) & (col <= q33)
            regimenes_activos.append(("BAJO", mask_bajo))
        if q33 < val_act < q67:
            mask_med = np.isfinite(col) & (col > q33) & (col < q67)
            regimenes_activos.append(("MEDIO", mask_med))
        if val_act >= q75 and q75 > q67 + 1e-9:
            mask_p75 = np.isfinite(col) & (col >= q75)
            regimenes_activos.append(("MUY_ALTO", mask_p75))
        if val_act <= q25 and q25 < q33 - 1e-9:
            mask_p25 = np.isfinite(col) & (col <= q25)
            regimenes_activos.append(("MUY_BAJO", mask_p25))

        for reg_nombre, mask in regimenes_activos:
            atomicos.append({
                "var_idx": j,
                "variable": nom,
                "regimen": reg_nombre,
                "clave_pred": (nom, reg_nombre),
                "mascara": mask.astype(bool),
            })
    return atomicos


def _jaccard_mascaras(m1: np.ndarray, m2: np.ndarray) -> float:
    inter = int(np.sum(m1 & m2))
    union = int(np.sum(m1 | m2))
    return (inter / union) if union > 0 else 1.0


def explorar_patrones_jerarquicos(
    X_train_z: np.ndarray,
    y_train: np.ndarray,
    x_actual_z: np.ndarray,
    *,
    nombres_variables: Sequence[str],
    periodo: str = "FT",
    objetivo: str = "+2.5 FT",
    orden_maximo: int = 5,
    soporte_minimo_por_orden: Optional[Dict[int, int]] = None,
    beam_width_por_orden: Optional[Dict[int, int]] = None,
    ganancia_marginal_minima_ll: float = 0.005,
    jaccard_maximo_padre_hijo: float = 0.95,
    alpha0: float = 4.0,
) -> Dict[str, Any]:
    """
    Expande combinaciones de orden k in {1, ..., orden_maximo} mediante Beam Search
    con poda por soporte mínimo, Jaccard padre-hijo y ganancia marginal de Log-Loss.
    """
    X_arr = np.asarray(X_train_z, dtype=float)
    y_arr = np.asarray(y_train, dtype=int)
    x_act = np.asarray(x_actual_z, dtype=float)

    soportes = dict({1: 12, 2: 10, 3: 8, 4: 8, 5: 8})
    if soporte_minimo_por_orden:
        for k_s, v_s in soporte_minimo_por_orden.items():
            soportes[int(k_s)] = int(v_s)

    beams = dict({1: 24, 2: 120, 3: 200, 4: 250, 5: 250})
    if beam_width_por_orden:
        for k_b, v_b in beam_width_por_orden.items():
            beams[int(k_b)] = int(v_b)

    conteo_por_orden: Dict[int, int] = {k: 0 for k in range(1, int(orden_maximo) + 1)}
    if X_arr.ndim != 2 or X_arr.shape[0] == 0 or X_arr.shape[1] == 0 or y_arr.size == 0:
        return {
            "patrones_candidatos": [],
            "n_hipotesis_evaluadas": 0,
            "conteo_por_orden": conteo_por_orden,
            "tasa_base_pi0": 0.5,
        }

    pi0 = float(np.clip(np.mean(y_arr), 1e-4, 1.0 - 1e-4))
    atomicos_raw = _construir_predicados_atomicos(X_arr, x_act, nombres_variables)
    n_hipotesis = 0
    pool_total: List[Dict[str, Any]] = []

    # ── Nivel k = 1 ──
    n_min_1 = int(soportes.get(1, 12))
    candidatos_k1: List[Dict[str, Any]] = []
    atomicos_validos: List[Dict[str, Any]] = []
    for at in atomicos_raw:
        n_act = int(np.sum(at["mascara"]))
        if n_act < n_min_1:
            continue
        n_hipotesis += 1
        met = _evaluar_metricas_patron(at["mascara"], y_arr, pi0, alpha0=alpha0)
        pat = {
            "id_canonico": f"{periodo}::{objetivo}::O1::{at['variable']}={at['regimen']}",
            "orden": 1,
            "periodo": str(periodo).upper(),
            "objetivo": str(objetivo),
            "variables": (at["variable"],),
            "regimenes": (at["regimen"],),
            "predicados": (at["clave_pred"],),
            "tasa_base_pi0": pi0,
            **met,
            "vector_activacion": at["mascara"].astype(int),
            "_mascara_bool": at["mascara"],
        }
        atomicos_validos.append(pat)
        if met["delta_ll"] > 0.0:
            candidatos_k1.append(pat)

    candidatos_k1.sort(key=lambda p: (p["score"], p["delta_ll"], p["n_activaciones"]), reverse=True)
    beam_actual = candidatos_k1[: int(beams.get(1, 24))]
    # Si todos los atómicos tuvieron delta_ll <= 0 pero hay atómicos con soporte, usar los mejores para permitir exploración
    atomicos_expansion = (
        beam_actual
        if beam_actual
        else sorted(atomicos_validos, key=lambda p: p["delta_ll"], reverse=True)[: int(beams.get(1, 24))]
    )
    pool_total.extend(beam_actual)
    conteo_por_orden[1] = len(beam_actual)

    vistos_predicados = {p["predicados"] for p in atomicos_validos}
    mapa_delta_ll: Dict[Tuple[Tuple[str, str], ...], float] = {
        p["predicados"]: float(p["delta_ll"]) for p in atomicos_validos
    }

    # ── Niveles k -> k + 1 (k = 1, 2, 3, 4) ──
    padres_nivel = list(atomicos_expansion)
    for k_nuevo in range(2, int(orden_maximo) + 1):
        if not padres_nivel:
            break
        n_min_k = int(soportes.get(k_nuevo, 8))
        w_k = int(beams.get(k_nuevo, 250))
        candidatos_nivel: List[Dict[str, Any]] = []

        for padre in padres_nivel:
            vars_padre = set(padre["variables"])
            mask_padre = padre["_mascara_bool"]
            ll_padre = float(padre["delta_ll"])

            for at in atomicos_expansion:
                var_at = at["variables"][0]
                if var_at in vars_padre:
                    continue
                preds_hijo = tuple(sorted(padre["predicados"] + at["predicados"]))
                if preds_hijo in vistos_predicados:
                    continue
                vistos_predicados.add(preds_hijo)

                mask_hijo = mask_padre & at["_mascara_bool"]
                n_act_hijo = int(np.sum(mask_hijo))
                # Regla de Poda 1: Soporte mínimo
                if n_act_hijo < n_min_k:
                    continue

                # Regla de Poda 2: No redundancia inmediata (Jaccard <= umbral)
                jac = _jaccard_mascaras(mask_hijo, mask_padre)
                if jac > jaccard_maximo_padre_hijo:
                    continue

                n_hipotesis += 1
                met_hijo = _evaluar_metricas_patron(mask_hijo, y_arr, pi0, alpha0=alpha0)
                ll_hijo = float(met_hijo["delta_ll"])
                mapa_delta_ll[preds_hijo] = ll_hijo

                # Regla de Poda 3: Ganancia marginal estricta sobre el mejor padre conocido
                mejor_ll_padre = max(ll_padre, float(at["delta_ll"]))
                if ll_hijo <= mejor_ll_padre + float(ganancia_marginal_minima_ll):
                    continue

                vars_hijo = tuple(v for (v, _) in preds_hijo)
                regs_hijo = tuple(r for (_, r) in preds_hijo)
                eti_preds = "&".join(f"{v}={r}" for (v, r) in preds_hijo)
                pat_hijo = {
                    "id_canonico": f"{periodo}::{objetivo}::O{k_nuevo}::{eti_preds}",
                    "orden": k_nuevo,
                    "periodo": str(periodo).upper(),
                    "objetivo": str(objetivo),
                    "variables": vars_hijo,
                    "regimenes": regs_hijo,
                    "predicados": preds_hijo,
                    "tasa_base_pi0": pi0,
                    "jaccard_con_padre": round(float(jac), 4),
                    "ganancia_marginal_ll": round(float(ll_hijo - mejor_ll_padre), 6),
                    **met_hijo,
                    "vector_activacion": mask_hijo.astype(int),
                    "_mascara_bool": mask_hijo,
                }
                candidatos_nivel.append(pat_hijo)

        candidatos_nivel.sort(key=lambda p: (p["score"], p["delta_ll"], p["n_activaciones"]), reverse=True)
        beam_k = candidatos_nivel[:w_k]
        pool_total.extend(beam_k)
        conteo_por_orden[k_nuevo] = len(beam_k)
        padres_nivel = beam_k

    # Limpiar clave interna `_mascara_bool` antes de devolver
    for p in pool_total:
        p.pop("_mascara_bool", None)

    return {
        "patrones_candidatos": pool_total,
        "n_hipotesis_evaluadas": int(n_hipotesis),
        "conteo_por_orden": conteo_por_orden,
        "tasa_base_pi0": pi0,
    }
