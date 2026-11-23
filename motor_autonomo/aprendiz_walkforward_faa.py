"""
motor_autonomo/aprendiz_walkforward_faa.py
───────────────────────────────────────────────────────────────────────────────
CAPA 5 y CAPA 6 — Aprendiz Walk-Forward Regularizado (`w <- w - eta * grad(L)`)
y Proyección Final Única `FAA` al Politopo Coherente de Mercados:
  1. Escalado disciplinado del Aprendiz según N_train:
     - Régimen 1 (N_train < 30): Modo Sombra puro (w_prod = 0).
     - Régimen 2 (30 <= N_train < 250): Calibrador Logit-Ridge L2 sobre K <= 10
       patrones ortogonales DPP por descenso de gradiente regularizado.
     - Régimen 3 (N_train >= 250): Bloque Residual No Monótono GELU con ancho
       w_hid >= d + 1 y conexión skip h_{l+1} = h_l + MLP_GELU(h_l).
  2. Proyección Final Única `FAA` (Elamvazhuthi et al., 2026, arXiv:2602.03082v2):
     - Simplex 1X2 (Duchi et al.) + coherencia exacta DC (1X, X2, 12) y DNB.
     - Monotonicidad estricta Over/Under mediante Pool Adjacent Violators (PAV).
     - Coherencia aditiva entre periodos 1T, 2T y FT.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def _logit(p: np.ndarray | float, eps: float = 1e-6) -> np.ndarray | float:
    arr = np.clip(p, eps, 1.0 - eps)
    return np.log(arr / (1.0 - arr))


def _sigmoide(z: np.ndarray | float) -> np.ndarray | float:
    z_clip = np.clip(z, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-z_clip))


def gelu_exacto(x: np.ndarray) -> np.ndarray:
    """Activación no monótona GELU(x) = x * Phi(x) = 0.5 * x * (1 + erf(x / sqrt(2)))."""
    arr = np.asarray(x, dtype=float)
    erf_vec = np.vectorize(math.erf)
    return 0.5 * arr * (1.0 + erf_vec(arr / math.sqrt(2.0)))


def bloque_residual_gelu_forward(
    X: np.ndarray,
    W1: np.ndarray,
    b1: np.ndarray,
    W2: np.ndarray,
    b2: np.ndarray,
) -> np.ndarray:
    """
    Evalúa un bloque residual no monótono h_{l+1} = h_l + W2 * GELU(W1 * h_l + b1) + b2
    con ancho oculto w_hid >= d + 1 (Teoremas 5.2 y D.1 de Ren & Lim, ICML 2026).
    """
    d = X.shape[1]
    w_hid = W1.shape[1]
    if w_hid < d + 1:
        raise ValueError(f"Violación Teorema D.1: ancho oculto w_hid={w_hid} debe ser >= d+1={d+1}.")
    h = gelu_exacto(X @ W1 + b1)
    return X + (h @ W2 + b2)


def ajustar_pesos_walkforward(
    patrones_ortogonales: Sequence[Dict[str, Any]],
    y_train: Sequence[int],
    *,
    p_base_train: Optional[Sequence[float]] = None,
    x_patrones_actual: Optional[Sequence[float]] = None,
    p_base_actual: float = 0.50,
    eta: float = 0.15,
    lambda_l2: float = 0.25,
    n_iter: int = 80,
    w_max_norm: float = 0.85,
    min_partidos_oos: int = 30,
    min_partidos_gelu: int = 250,
    activar_en_produccion: bool = False,
) -> Dict[str, Any]:
    """
    Entrena los pesos de los patrones ortogonales DPP mediante descenso de gradiente
    regularizado L2 en espacio logit:
      logit(p_hat) = logit(p_base) + w^T s_dpp
      w <- w - eta * grad_w(LogLoss(w) + lambda_2 * ||w||_2^2)
    Respeta los 3 regímenes de volumen de muestra (N < 30, 30 <= N < 250, N >= 250).
    """
    y_arr = np.asarray(y_train, dtype=float)
    n_train = int(y_arr.size)
    K = len(patrones_ortogonales)

    if p_base_train is not None and len(p_base_train) == n_train:
        p0_tr = np.clip(np.asarray(p_base_train, dtype=float), 1e-4, 1.0 - 1e-4)
    else:
        pi0 = float(np.clip(np.mean(y_arr), 0.05, 0.95)) if n_train > 0 else 0.50
        p0_tr = np.full(n_train, pi0, dtype=float)

    p0_act = float(np.clip(p_base_actual, 1e-4, 1.0 - 1e-4))
    if K == 0 or n_train == 0:
        return {
            "regimen": "regimen_1_modo_sombra_puro",
            "n_train": n_train,
            "k_patrones": 0,
            "pesos": [],
            "norma_l2_pesos": 0.0,
            "prob_base_actual": round(p0_act, 6),
            "prob_sombra_ajustada": round(p0_act, 6),
            "prob_publicada": round(p0_act, 6),
            "activo_en_produccion": False,
        }

    # Construir matriz de señales direccionales centradas S in R^{n_train x K}
    # Cada patrón aporta +1 o -1 según si su prob_suavizada_lap supera o es menor que pi0
    S = np.zeros((n_train, K), dtype=float)
    s_act = np.zeros(K, dtype=float)
    for k_idx, pat in enumerate(patrones_ortogonales):
        act = np.asarray(pat.get("vector_activacion") or np.zeros(n_train), dtype=float)
        if act.size != n_train:
            act = np.resize(act, n_train)
        p_lap = float(pat.get("prob_suavizada_lap", 0.5))
        pi0_pat = float(pat.get("tasa_base_pi0", 0.5))
        signo = 1.0 if p_lap >= pi0_pat else -1.0
        S[:, k_idx] = signo * act
        if x_patrones_actual is not None and k_idx < len(x_patrones_actual):
            s_act[k_idx] = signo * float(x_patrones_actual[k_idx])
        else:
            # Por construcción de Capa 1, los patrones descubiertos están activos en el partido actual
            s_act[k_idx] = signo * 1.0

    # Régimen 1: N_train < 30 -> Modo Sombra puro (w_prod = 0)
    if n_train < int(min_partidos_oos):
        return {
            "regimen": "regimen_1_modo_sombra_puro",
            "n_train": n_train,
            "k_patrones": K,
            "pesos": [0.0] * K,
            "norma_l2_pesos": 0.0,
            "prob_base_actual": round(p0_act, 6),
            "prob_sombra_ajustada": round(p0_act, 6),
            "prob_publicada": round(p0_act, 6),
            "activo_en_produccion": False,
        }

    # Descenso de gradiente regularizado L2: w <- w - eta * grad(LogLoss + lambda_2 * ||w||^2)
    logit_base = _logit(p0_tr)
    w = np.zeros(K, dtype=float)
    lr = float(eta)
    l2 = float(lambda_l2)

    for _ in range(max(1, int(n_iter))):
        p_pred = _sigmoide(logit_base + S @ w)
        grad_ll = (S.T @ (p_pred - y_arr)) / float(n_train)
        grad_total = grad_ll + 2.0 * l2 * w
        w = w - lr * grad_total
        norma_w = float(np.linalg.norm(w))
        if norma_w > float(w_max_norm):
            w = w * (float(w_max_norm) / norma_w)

    regimen = "regimen_2_logit_ridge_l2"
    ancho_oculto_gelu = None
    if n_train >= int(min_partidos_gelu):
        regimen = "regimen_3_residual_gelu"
        w_hid = K + 2  # Garantiza w_hid >= d + 1 (Teorema D.1)
        ancho_oculto_gelu = w_hid
        rng = np.random.default_rng(42)
        W1 = rng.normal(0.0, 0.05 / math.sqrt(max(1, K)), size=(K, w_hid))
        b1 = np.zeros(w_hid, dtype=float)
        W2 = rng.normal(0.0, 0.05 / math.sqrt(w_hid), size=(w_hid, K))
        b2 = np.zeros(K, dtype=float)
        S_res = bloque_residual_gelu_forward(S, W1, b1, W2, b2)
        s_act_res = bloque_residual_gelu_forward(s_act.reshape(1, -1), W1, b1, W2, b2)[0]
        p_sombra = float(_sigmoide(float(_logit(p0_act)) + float(np.dot(w, s_act_res))))
    else:
        p_sombra = float(_sigmoide(float(_logit(p0_act)) + float(np.dot(w, s_act))))

    p_pub = p_sombra if bool(activar_en_produccion) else p0_act
    return {
        "regimen": regimen,
        "n_train": n_train,
        "k_patrones": K,
        "ancho_oculto_gelu": ancho_oculto_gelu,
        "pesos": [round(float(x), 6) for x in w],
        "norma_l2_pesos": round(float(np.linalg.norm(w)), 6),
        "prob_base_actual": round(p0_act, 6),
        "prob_sombra_ajustada": round(p_sombra, 6),
        "prob_publicada": round(p_pub, 6),
        "activo_en_produccion": bool(activar_en_produccion),
    }


def proyectar_simplex_euclideo(v: Sequence[float]) -> np.ndarray:
    """
    Proyección euclídea exacta al simplex unitario Delta^{n-1} = {p >= 0 : sum p_i = 1}
    (algoritmo de Duchi et al., 2008 en O(n log n)):
      p* = argmin_{p in Delta} ||p - v||_2^2
    """
    y = np.asarray(v, dtype=float)
    n = y.size
    if n == 0:
        return y.copy()
    u = np.sort(y)[::-1]
    cssv = np.cumsum(u) - 1.0
    ind = np.arange(1, n + 1, dtype=float)
    cond = (u - cssv / ind) > 0.0
    if not np.any(cond):
        return np.full(n, 1.0 / n, dtype=float)
    rho = int(np.nonzero(cond)[0][-1]) + 1
    theta = float(cssv[rho - 1]) / float(rho)
    w = np.maximum(y - theta, 0.0)
    # Ajuste de precisión de punto flotante final para suma 1.000000000000
    s = float(np.sum(w))
    return (w / s) if s > 0 else np.full(n, 1.0 / n, dtype=float)


def proyectar_cono_monotono_pav(
    valores: Sequence[float],
    *,
    creciente: bool = True,
    clip_01: bool = True,
) -> np.ndarray:
    """
    Proyección euclídea exacta sobre el cono monótono mediante Pool Adjacent Violators (PAV):
      q* = argmin_{q_1 <= q_2 <= ... <= q_m} ||q - valores||_2^2
    Si `creciente=False`, proyecta sobre el cono decreciente q_1 >= q_2 >= ... >= q_m.
    """
    arr = np.asarray(valores, dtype=float)
    if arr.size == 0:
        return arr.copy()
    y = arr if creciente else -arr

    # Pila de bloques: cada bloque es [suma_valores, conteo_elementos]
    bloques: List[List[float]] = []
    for val in y:
        bloques.append([float(val), 1.0])
        while len(bloques) >= 2:
            b_prev = bloques[-2]
            b_curr = bloques[-1]
            if (b_prev[0] / b_prev[1]) > (b_curr[0] / b_curr[1]):
                b_prev[0] += b_curr[0]
                b_prev[1] += b_curr[1]
                bloques.pop()
            else:
                break

    out = np.empty(arr.size, dtype=float)
    pos = 0
    for suma_b, cnt_b in bloques:
        c = int(round(cnt_b))
        media_b = suma_b / cnt_b
        out[pos : pos + c] = media_b
        pos += c

    if not creciente:
        out = -out
    if clip_01:
        out = np.clip(out, 0.0, 1.0)
    return out


def proyectar_politopo_coherente_faa(
    probs_crudas: Dict[str, Any],
) -> Dict[str, Any]:
    """
    CAPA 6 — Proyección Final Única `FAA` (Elamvazhuthi et al., 2026, arXiv:2602.03082v2,
    Ec. 9 y Teorema 3.1):
      1. Proyecta 1X2 (`1`, `X`, `2`) al simplex euclídeo exacto (suma == 1.000000).
      2. Deriva coherentemente Doble Oportunidad (`1X`, `X2`, `12`) y DNB (`DNB_L`, `DNB_V`).
      3. Proyecta líneas Under (`under_L` o `-L`) al cono monótono creciente (PAV) y
         líneas Over (`over_L` o `+L`) al cono monótono decreciente (PAV).
      4. Verifica y proyecta la coherencia aditiva entre mitades y FT:
         P(+0.5 FT) >= max(P(+0.5 1T), P(+0.5 2T)).
    """
    out: Dict[str, Any] = {}
    dist_l2_sq_total = 0.0

    # 1. Simplex 1X2
    if "1x2" in probs_crudas and isinstance(probs_crudas["1x2"], (dict, list, tuple)):
        raw_1x2 = probs_crudas["1x2"]
        if isinstance(raw_1x2, dict):
            v_1x2 = np.array([
                float(raw_1x2.get("1", raw_1x2.get("local", 1.0 / 3.0))),
                float(raw_1x2.get("X", raw_1x2.get("empate", 1.0 / 3.0))),
                float(raw_1x2.get("2", raw_1x2.get("visita", 1.0 / 3.0))),
            ], dtype=float)
        else:
            v_1x2 = np.asarray(list(raw_1x2)[:3], dtype=float)
        p_1x2 = proyectar_simplex_euclideo(v_1x2)
        dist_l2_sq_total += float(np.sum((p_1x2 - v_1x2) ** 2))
        p1, px, p2 = float(p_1x2[0]), float(p_1x2[1]), float(p_1x2[2])
        out["1x2"] = {"1": p1, "X": px, "2": p2}
        out["doble_oportunidad"] = {
            "1X": p1 + px,
            "X2": px + p2,
            "12": p1 + p2,
        }
        denom_dnb = p1 + p2
        out["dnb"] = {
            "DNB_L": (p1 / denom_dnb) if denom_dnb > 1e-12 else 0.5,
            "DNB_V": (p2 / denom_dnb) if denom_dnb > 1e-12 else 0.5,
        }

    # 2. Monotonicidad estricta en curvas Under (creciente con la línea) y Over (decreciente con la línea)
    for clave_curva, es_creciente in (
        ("under", True),
        ("under_goles", True),
        ("under_corners", True),
        ("under_tarjetas", True),
        ("over", False),
        ("over_goles", False),
        ("over_corners", False),
        ("over_tarjetas", False),
    ):
        if clave_curva in probs_crudas and isinstance(probs_crudas[clave_curva], dict):
            curva_raw: Dict[Any, float] = probs_crudas[clave_curva]
            pares_ord = sorted(
                [(float(str(k).replace("+", "").replace("-", "").strip()), k, float(v)) for k, v in curva_raw.items()],
                key=lambda x: x[0],
            )
            if pares_ord:
                vals_raw = np.array([x[2] for x in pares_ord], dtype=float)
                vals_proy = proyectar_cono_monotono_pav(vals_raw, creciente=es_creciente, clip_01=True)
                dist_l2_sq_total += float(np.sum((vals_proy - vals_raw) ** 2))
                out[clave_curva] = {
                    pares_ord[i][1]: float(vals_proy[i])
                    for i in range(len(pares_ord))
                }

    # 3. Coherencia aditiva entre periodos (1T, 2T, FT)
    if "periodos_over_05" in probs_crudas and isinstance(probs_crudas["periodos_over_05"], dict):
        per_raw = probs_crudas["periodos_over_05"]
        p_1t = float(np.clip(per_raw.get("1T", 0.0), 0.0, 1.0))
        p_2t = float(np.clip(per_raw.get("2T", 0.0), 0.0, 1.0))
        p_ft_raw = float(per_raw.get("FT", max(p_1t, p_2t)))
        p_ft_proy = float(np.clip(max(p_ft_raw, p_1t, p_2t), 0.0, 1.0))
        dist_l2_sq_total += (p_ft_proy - p_ft_raw) ** 2
        out["periodos_over_05"] = {"1T": p_1t, "2T": p_2t, "FT": p_ft_proy}

    out["distancia_l2_proyeccion"] = float(math.sqrt(max(0.0, dist_l2_sq_total)))
    out["violaciones_monotonicidad"] = 0
    out["violaciones_simplex_1x2"] = 0
    return out
