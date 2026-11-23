"""
motor_autonomo/evaluador_cramer_cvar.py
───────────────────────────────────────────────────────────────────────────────
CAPA 3 — Evaluación Distribucional `φTD` (Distancia de Cramér en Frecuencia)
y Riesgo de Cola `CVaR_25` / Riesgo Entrópico `rho_eta`:
  1. Función característica analítica phi_P(omega) = sum_j w_j * exp(i * omega * z_j).
  2. Distancia de Cramér al cuadrado exacta en espacio espacial:
       l2^2(P, delta_y) = sum_{k=0}^{M-1} (F_P(k) - 1{y <= k})^2
  3. Pérdida en dominio de frecuencia con muestreo Pareto(alpha=1, x_m)
     (Lema 1 y Ec. 5 de Mousseau et al., NeurIPS 2026, arXiv:2611.25208v1).
  4. CVaR_25 exacto sobre los 9 escenarios de estrés:
       CVaR_25(m) = (p_(1) + p_(2) + 0.25 * p_(3)) / 2.25
  5. Riesgo entrópico averso al riesgo (eta < 0, por defecto -4.0):
       rho_eta(m) = (1 / eta) * log((1/9) * sum_{s=1}^9 exp(eta * p_s))
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def normalizar_pmf_discreta(pesos: Sequence[float], soporte: Optional[Sequence[float]] = None) -> Tuple[np.ndarray, np.ndarray]:
    """Normaliza una distribución discreta (soporte z_j, pesos w_j >= 0 con sum w_j = 1)."""
    w = np.maximum(np.asarray(pesos, dtype=float), 0.0)
    s = float(np.sum(w))
    if s <= 0.0 or not np.isfinite(s):
        w = np.full(len(pesos), 1.0 / max(1, len(pesos)), dtype=float)
    else:
        w = w / s
    if soporte is None:
        z = np.arange(len(w), dtype=float)
    else:
        z = np.asarray(soporte, dtype=float)
    return z, w


def funcion_caracteristica_discreta(
    omegas: np.ndarray,
    pesos: Sequence[float],
    soporte: Optional[Sequence[float]] = None,
) -> np.ndarray:
    """
    Evalúa la función característica phi_P(omega) = sum_{j=0}^M w_j * exp(i * omega * z_j)
    para un vector de frecuencias `omegas`.
    """
    z, w = normalizar_pmf_discreta(pesos, soporte)
    w_arr = np.asarray(omegas, dtype=float)
    # Matriz (len(omegas), len(z))
    fases = np.outer(w_arr, z)
    return np.exp(1j * fases) @ w


def distancia_cramer_exacta_espacial(
    pesos: Sequence[float],
    y_obs: float,
    soporte: Optional[Sequence[float]] = None,
) -> float:
    """
    Distancia de Cramér al cuadrado exacta en espacio espacial (Lema 1 / RPS):
      l2^2(P, delta_{y_obs}) = int_{-inf}^{+inf} |F_P(x) - 1{y_obs <= x}|^2 dx
    Para soporte entero consecutivo z_k = 0, 1, ..., M:
      l2^2(P, delta_{y_obs}) = sum_{k=0}^{M-1} (F_P(k) - 1{y_obs <= k})^2
    """
    z, w = normalizar_pmf_discreta(pesos, soporte)
    if z.size <= 1:
        return 0.0
    cdf = np.cumsum(w)[:-1]
    anchos = np.diff(z)
    ind = (float(y_obs) <= z[:-1]).astype(float)
    diff_sq = (cdf - ind) ** 2
    return float(np.sum(diff_sq * anchos))


def muestrear_frecuencias_pareto(
    n_frecuencias: int = 128,
    x_m: float = 0.001,
    omega_max: float = 15.0,
    semilla: int = 42,
) -> np.ndarray:
    """
    Muestrea `n_frecuencias` de Pareto(alpha=1, x_m) truncada en [x_m, omega_max]
    combinando cuantiles estratificados + jitter determinista (reducción de varianza
    para el estimador de importancia 1/omega^2 del Lema 1).
    En Pareto(1, x_m) truncada en [x_m, omega_max], la CDF inversa es:
      omega(u) = x_m / (1 - u * (1 - x_m / omega_max)),  u in (0, 1).
    """
    n = max(1, int(n_frecuencias))
    xm = max(float(x_m), 1e-6)
    wmax = max(float(omega_max), xm * 10.0)
    rng = np.random.default_rng(int(semilla))
    # Estratificación en n celdas de (0, 1) con semilla fija
    u = (np.arange(n, dtype=float) + rng.uniform(0.15, 0.85, size=n)) / float(n)
    factor_trunc = 1.0 - (xm / wmax)
    omegas = xm / (1.0 - u * factor_trunc)
    return np.clip(omegas, xm, wmax)


def perdida_phi_td_pareto(
    pesos: Sequence[float],
    y_obs: float,
    *,
    soporte: Optional[Sequence[float]] = None,
    n_frecuencias: int = 128,
    x_m: float = 0.001,
    omega_max: float = 15.0,
    semilla: int = 42,
) -> Dict[str, float]:
    """
    Calcula la pérdida en dominio de frecuencia L_{phiTD}^{Pareto}(P, y_obs)
    (Ec. 5 de Mousseau et al., NeurIPS 2026) y su estimador escalado por Lema 1
    de la distancia de Cramér al cuadrado:
      l2^2(P, delta_y) = (1 / pi) * int_0^inf |phi_P(w) - e^{i w y}|^2 / w^2 dw
    """
    omegas = muestrear_frecuencias_pareto(
        n_frecuencias=n_frecuencias,
        x_m=x_m,
        omega_max=omega_max,
        semilla=semilla,
    )
    phi_p = funcion_caracteristica_discreta(omegas, pesos, soporte=soporte)
    phi_y = np.exp(1j * omegas * float(y_obs))
    diff_sq = np.abs(phi_p - phi_y) ** 2

    # Ec. 5: promedio directo sobre frecuencias Pareto
    loss_cruda = float(np.mean(diff_sq))

    # Estimador de importancia reescalado por la constante de normalización de Pareto truncada:
    # int_{x_m}^{omega_max} (1 / w^2) dw = (1 / x_m) - (1 / omega_max)
    masa_pareto = (1.0 / float(x_m)) - (1.0 / float(omega_max))
    cramer_estimado = float((masa_pareto / math.pi) * loss_cruda)
    cramer_exacto = distancia_cramer_exacta_espacial(pesos, y_obs, soporte=soporte)

    return {
        "loss_phi_td_cruda": loss_cruda,
        "cramer_frecuencia_estimado": cramer_estimado,
        "cramer_espacial_exacto": cramer_exacto,
        "error_abs_lema1": abs(cramer_estimado - cramer_exacto),
    }


def construir_pmf_poisson(lam: float, max_k: int = 15) -> np.ndarray:
    """Construye PMF de Poisson(lam) truncada y normalizada en 0..max_k."""
    lval = max(float(lam), 1e-6)
    ks = np.arange(int(max_k) + 1, dtype=float)
    log_p = -lval + ks * math.log(lval) - np.vectorize(lambda x: math.lgamma(x + 1.0))(ks)
    p = np.exp(log_p)
    return p / float(np.sum(p))


def construir_pmf_binomial_negativa(media: float, factor_sobredispersion: float = 1.35, max_k: int = 25) -> np.ndarray:
    """
    Construye PMF de Binomial Negativa con media `media` y varianza `factor_sobredispersion * media`
    (cuando factor <= 1.01 cae limpiamente a Poisson).
    """
    mu = max(float(media), 1e-6)
    fac = max(float(factor_sobredispersion), 1.0)
    if fac <= 1.01:
        return construir_pmf_poisson(mu, max_k=max_k)
    var = fac * mu
    r = (mu * mu) / max(var - mu, 1e-6)
    p_exito = r / (r + mu)
    ks = np.arange(int(max_k) + 1, dtype=float)
    pmf = np.zeros_like(ks)
    for idx, k in enumerate(ks):
        log_c = math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1.0)
        log_prob = log_c + r * math.log(p_exito) + k * math.log(1.0 - p_exito)
        pmf[idx] = math.exp(log_prob)
    s = float(np.sum(pmf))
    return (pmf / s) if s > 0 else construir_pmf_poisson(mu, max_k=max_k)


def construir_pmf_mezcla_2_poisson(
    lam_cerrado: float,
    lam_abierto: float,
    peso_abierto: float = 0.50,
    max_k: int = 15,
) -> np.ndarray:
    """Construye mezcla bimodal de 2 regímenes Poisson (cerrado vs abierto)."""
    w = min(max(float(peso_abierto), 0.0), 1.0)
    p1 = construir_pmf_poisson(lam_cerrado, max_k=max_k)
    p2 = construir_pmf_poisson(lam_abierto, max_k=max_k)
    pmf = (1.0 - w) * p1 + w * p2
    return pmf / float(np.sum(pmf))


def calcular_cvar_25_9esc(escenarios: Sequence[float]) -> float:
    """
    Calcula el CVaR_25 (Expected Shortfall del peor 25 %, Tabla 15 de Mousseau et al., 2026)
    sobre los 9 escenarios de estrés ordenados p_(1) <= p_(2) <= ... <= p_(9):
      CVaR_25(m) = (p_(1) + p_(2) + 0.25 * p_(3)) / 2.25
    Si la longitud S difiere de 9, integra exactamente la función cuantil empírica
    sobre u in [0, 0.25].
    """
    vals = sorted(float(x) for x in escenarios if x is not None and math.isfinite(float(x)))
    S = len(vals)
    if S == 0:
        return 0.0
    if S == 9:
        return float((vals[0] + vals[1] + 0.25 * vals[2]) / 2.25)

    # Integral exacta sobre [0, 0.25] para S arbitrario (cuota de masa = 0.25 * S)
    masa_objetivo = 0.25 * S
    k_enteros = int(math.floor(masa_objetivo))
    frac = masa_objetivo - k_enteros
    suma = sum(vals[:k_enteros])
    if k_enteros < S and frac > 0:
        suma += frac * vals[k_enteros]
    return float(suma / masa_objetivo) if masa_objetivo > 0 else float(vals[0])


def calcular_riesgo_entropico(escenarios: Sequence[float], eta: float = -4.0) -> float:
    """
    Calcula la medida de riesgo entrópico (Tabla 15 de Mousseau et al., 2026)
    con parámetro de aversión eta < 0:
      rho_eta(m) = (1 / eta) * log((1 / S) * sum_{s=1}^S exp(eta * p_s))
    """
    vals = [float(x) for x in escenarios if x is not None and math.isfinite(float(x))]
    if not vals:
        return 0.0
    arr = np.asarray(vals, dtype=float)
    eta_val = float(eta)
    if abs(eta_val) < 1e-9:
        return float(np.mean(arr))
    # Estabilización log-sum-exp
    escalados = eta_val * arr
    m_max = float(np.max(escalados))
    log_mean = m_max + math.log(float(np.mean(np.exp(escalados - m_max))))
    return float(log_mean / eta_val)


def evaluar_riesgo_escenarios_9(
    escenarios_9: Sequence[float],
    *,
    eta: float = -4.0,
    umbral_cvar25: float = 0.65,
    umbral_prob_media: float = 0.70,
) -> Dict[str, Any]:
    """
    Evalúa `prob_media_9esc`, `cvar_25_9esc`, `riesgo_entropico_9esc` y si supera
    la compuerta combinada (`prob_media >= 0.70` y `cvar_25 >= 0.65`).
    """
    vals = [float(x) for x in escenarios_9 if x is not None and math.isfinite(float(x))]
    if not vals:
        return {
            "n_escenarios": 0,
            "prob_media_9esc": 0.0,
            "cvar_25_9esc": 0.0,
            "riesgo_entropico_9esc": 0.0,
            "supera_filtro_cvar25": False,
        }
    p_med = float(np.mean(vals))
    cvar25 = calcular_cvar_25_9esc(vals)
    rho = calcular_riesgo_entropico(vals, eta=eta)
    pasa = (p_med + 1e-12 >= float(umbral_prob_media)) and (cvar25 + 1e-12 >= float(umbral_cvar25))
    return {
        "n_escenarios": len(vals),
        "prob_media_9esc": round(p_med, 6),
        "cvar_25_9esc": round(cvar25, 6),
        "riesgo_entropico_9esc": round(rho, 6),
        "supera_filtro_cvar25": bool(pasa),
    }
