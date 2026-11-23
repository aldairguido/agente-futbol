"""
motor_autonomo/selector_dpp.py
───────────────────────────────────────────────────────────────────────────────
CAPA 2 — Desduplicación Geométrica y Selección de Conjuntos con `PoolDPP-MAP`:
  - Algoritmo 2 (`greedy_map_k_dpp`, Prada-Corral et al., NeurIPS 2026, pág. 30)
    en complemento de Schur sobre matriz de características B en O(D * N * K).
  - Uso A: Destilación de K patrones ortogonales a partir del pool de 500–1,000
    candidatos del Explorador Jerárquico.
  - Uso B: Selección descorrelacionada del Top 5 del Ticket en el espacio de
    resultados ponderados por sqrt(P(i, j)) sobre la matriz 7x7 + bloques de
    córneres/tarjetas/mitades.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def normalizar_columnas_l2(matriz: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    """Normaliza cada columna de `matriz` (shape D x N) a norma L2 unitaria."""
    arr = np.asarray(matriz, dtype=float)
    if arr.ndim != 2 or arr.shape[1] == 0:
        return arr.copy()
    normas = np.linalg.norm(arr, axis=0, keepdims=True)
    out = np.zeros_like(arr, dtype=float)
    for j in range(arr.shape[1]):
        nj = float(normas[0, j])
        if nj > eps and np.isfinite(nj):
            out[:, j] = arr[:, j] / nj
        else:
            out[j % arr.shape[0], j] = 1.0
    return out


def construir_kernel_calidad_diversidad(
    embeddings_unit: np.ndarray,
    calidades: np.ndarray,
    temperatura_global: float = 1.0,
    eps: float = 1e-12,
) -> np.ndarray:
    """
    Construye el kernel L = B^T B (Ec. 4 y 6 de Prada-Corral et al., NeurIPS 2026):
      B_{:, n} = sqrt(calidad_n^(1/T)) * phi(y^(n))
    """
    phi = normalizar_columnas_l2(embeddings_unit, eps=eps)
    cal = np.asarray(calidades, dtype=float)
    T = max(float(temperatura_global), eps)
    kappa = np.power(np.maximum(cal, eps), 1.0 / T)
    B = phi * np.sqrt(kappa)[np.newaxis, :]
    return B.T @ B


def greedy_map_k_dpp(
    embeddings_unit: np.ndarray,   # shape (D, N), columnas con norma L2 == 1
    calidades: np.ndarray,         # shape (N,), estrictamente > 0
    k: int,
    temperatura_global: float = 1.0,
    eps: float = 1e-12,
) -> Tuple[List[int], float]:
    """
    Algoritmo 2 (GreedyMAP, Prada-Corral et al., NeurIPS 2026, pág. 30).
    Aproximación voraz en complemento de Schur para maximizar log det(L_S)
    en O(D * N * K). Devuelve (indices_seleccionados, log_det_total).
    """
    arr = np.asarray(embeddings_unit, dtype=float)
    if arr.ndim != 2:
        raise ValueError("embeddings_unit debe tener shape (D, N).")
    D, N = arr.shape
    if N == 0 or k <= 0 or D == 0:
        return [], 0.0
    k_eff = min(int(k), N, D)

    # Normalización estricta de columnas (nota al pie 5 del paper)
    phi = normalizar_columnas_l2(arr, eps=eps)

    # Escalado por calidad con temperatura global T (Ec. 6)
    cal = np.asarray(calidades, dtype=float)
    kappa = np.power(np.maximum(cal, eps), 1.0 / max(float(temperatura_global), eps))
    B = phi * np.sqrt(kappa)[np.newaxis, :]   # shape (D, N)

    # d_y = ||B_{:,y}||^2 (norma residual al cuadrado = L_{yy})
    d = np.sum(B * B, axis=0)
    seleccionados: List[int] = []
    mascara_disp = np.ones(N, dtype=bool)
    log_det = 0.0

    for _ in range(k_eff):
        d_masked = np.where(mascara_disp, d, -np.inf)
        y_star = int(np.argmax(d_masked))
        d_star = float(d[y_star])
        if not np.isfinite(d_star) or d_star <= eps:
            break  # Rango efectivo agotado: todos los restantes son linealmente dependientes
        seleccionados.append(y_star)
        mascara_disp[y_star] = False
        log_det += float(np.log(d_star))

        # Dirección residual unitaria b = B_{:, y*} / sqrt(d_{y*})
        b = B[:, y_star] / np.sqrt(d_star)

        # Proyección ortogonal de todos los candidatos restantes fuera de b
        alpha = b @ B  # shape (N,)
        B = B - np.outer(b, alpha)
        d = np.sum(B * B, axis=0)

    return seleccionados, float(log_det)


def _correlacion_media_pares(phi_unit: np.ndarray) -> float:
    """Calcula la similitud coseno absoluta media fuera de la diagonal entre columnas de `phi_unit`."""
    if phi_unit.ndim != 2 or phi_unit.shape[1] < 2:
        return 0.0
    N = phi_unit.shape[1]
    gram = np.abs(phi_unit.T @ phi_unit)
    suma_fuera = float(np.sum(gram) - np.trace(gram))
    n_pares = N * (N - 1)
    return float(suma_fuera / n_pares) if n_pares > 0 else 0.0


def destilar_patrones_ortogonales_dpp(
    patrones_candidatos: Sequence[Dict[str, Any]],
    *,
    k: int = 10,
    temperatura_global: float = 1.5,
    eps: float = 1e-12,
) -> Dict[str, Any]:
    """
    Uso A — Destila hasta `k` patrones ortogonales no redundantes a partir del
    pool de candidatos del Explorador Jerárquico usando `greedy_map_k_dpp`.
    """
    N = len(patrones_candidatos)
    if N == 0 or k <= 0:
        return {
            "patrones_ortogonales": [],
            "indices_seleccionados": [],
            "n_candidatos_pool": 0,
            "n_ortogonales_dpp": 0,
            "log_det_gram_seleccionados": 0.0,
            "correlacion_media_antes": 0.0,
            "correlacion_media_despues": 0.0,
            "correlacion_media_pares_antes_vs_despues": {"antes": 0.0, "despues": 0.0},
        }

    vec0 = np.asarray(patrones_candidatos[0].get("vector_activacion") or [], dtype=float)
    D = max(int(vec0.size), 1)
    phi_raw = np.zeros((D, N), dtype=float)
    calidades = np.zeros(N, dtype=float)

    for idx, pat in enumerate(patrones_candidatos):
        act = np.asarray(pat.get("vector_activacion") or np.zeros(D), dtype=float)
        if act.size != D:
            act = np.resize(act, D)
        centrado = act - float(np.mean(act))
        norm_c = float(np.linalg.norm(centrado))
        if norm_c > eps:
            phi_raw[:, idx] = centrado / norm_c
        else:
            norm_a = float(np.linalg.norm(act))
            if norm_a > eps:
                phi_raw[:, idx] = act / norm_a
            else:
                phi_raw[idx % D, idx] = 1.0
        sc = float(pat.get("score") or max(0.0, float(pat.get("delta_ll") or 0.0)))
        calidades[idx] = math.exp(sc)

    phi_unit = normalizar_columnas_l2(phi_raw, eps=eps)
    corr_antes = _correlacion_media_pares(phi_unit)

    indices, log_det_l = greedy_map_k_dpp(
        phi_unit,
        calidades,
        k=int(k),
        temperatura_global=float(temperatura_global),
        eps=eps,
    )
    seleccionados = [dict(patrones_candidatos[i]) for i in indices]
    phi_sel = phi_unit[:, indices] if indices else np.zeros((D, 0), dtype=float)
    corr_despues = _correlacion_media_pares(phi_sel)

    if indices:
        gram_sel = phi_sel.T @ phi_sel
        signo, logdet_gram = np.linalg.slogdet(gram_sel + eps * np.eye(len(indices)))
        log_det_gram = float(logdet_gram) if signo > 0 else float(log_det_l)
    else:
        log_det_gram = 0.0

    return {
        "patrones_ortogonales": seleccionados,
        "indices_seleccionados": indices,
        "n_candidatos_pool": N,
        "n_ortogonales_dpp": len(seleccionados),
        "log_det_L_seleccionados": float(log_det_l),
        "log_det_gram_seleccionados": float(log_det_gram),
        "correlacion_media_antes": round(float(corr_antes), 6),
        "correlacion_media_despues": round(float(corr_despues), 6),
        "correlacion_media_pares_antes_vs_despues": {
            "antes": round(float(corr_antes), 6),
            "despues": round(float(corr_despues), 6),
        },
    }


def _matriz_poisson_7x7(lambda_local: float = 1.45, lambda_visita: float = 1.15) -> np.ndarray:
    """Construye una matriz 7x7 de probabilidades Poisson normalizada si no se provee."""
    mat = np.zeros((7, 7), dtype=float)
    for i in range(7):
        pi = math.exp(-lambda_local) * (lambda_local ** i) / math.factorial(i)
        for j in range(7):
            pj = math.exp(-lambda_visita) * (lambda_visita ** j) / math.factorial(j)
            mat[i, j] = pi * pj
    s = float(np.sum(mat))
    return (mat / s) if s > 0 else np.full((7, 7), 1.0 / 49.0)


def construir_embedding_mercado_top5(
    item: Dict[str, Any],
    matriz_7x7: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Construye el vector unitario phi(m) in R^65 de un mercado candidato al Top 5:
      - Primeras 49 componentes: resultado {0, 0.5, 1} en cada celda (i, j) de la
        matriz 7x7 de marcador FT ponderado por sqrt(P(i, j)).
      - Siguientes 16 componentes: canales ortogonales por familia no-goles-FT
        (córneres over/under/equipo, tarjetas over/under, 1T over/under/1x2, 2T over/under/1x2).
    """
    mat = np.asarray(matriz_7x7 if matriz_7x7 is not None else _matriz_poisson_7x7(), dtype=float)
    if mat.shape != (7, 7):
        mat = _matriz_poisson_7x7()
    pesos_celda = np.sqrt(np.maximum(mat, 0.0))

    etq = str(item.get("etiqueta") or item.get("evento") or "").strip().lower()
    mkt = str(item.get("mercado") or item.get("familia") or "").strip().lower()
    per = str(item.get("periodo") or "FT").strip().upper()
    linea = item.get("linea")
    try:
        val_linea = float(linea) if linea is not None else None
    except (ValueError, TypeError):
        val_linea = None

    vec = np.zeros(65, dtype=float)
    es_corner = ("córn" in etq or "corn" in etq or "esquina" in mkt or "corner" in mkt)
    es_tarjeta = ("tarj" in etq or "tarjeta" in mkt)
    es_1t = (per == "1T" or "1t" in etq or "1er tiempo" in mkt or "1ª mitad" in mkt)
    es_2t = (per == "2T" in etq or "2t" in etq or "2do tiempo" in mkt or "2ª mitad" in mkt)

    if es_corner:
        es_under = ("-" in etq or "menos" in etq or "under" in etq)
        base_idx = 49 if not es_under else 51
        vec[base_idx] = 1.0
        vec[base_idx + 1] = float(val_linea or 9.5) / 15.0
    elif es_tarjeta:
        es_under = ("-" in etq or "menos" in etq or "under" in etq)
        base_idx = 53 if not es_under else 55
        vec[base_idx] = 1.0
        vec[base_idx + 1] = float(val_linea or 4.5) / 10.0
    elif es_1t:
        es_under = ("-" in etq or "menos" in etq or "under" in etq)
        base_idx = 57 if not es_under else 59
        vec[base_idx] = 1.0
        vec[base_idx + 1] = float(val_linea or 0.5) / 3.0
    elif es_2t:
        es_under = ("-" in etq or "menos" in etq or "under" in etq)
        base_idx = 61 if not es_under else 63
        vec[base_idx] = 1.0
        vec[base_idx + 1] = float(val_linea or 0.5) / 3.0
    else:
        # Evaluar sobre las 49 celdas (i = goles local, j = goles visitante)
        grid = np.zeros((7, 7), dtype=float)
        for i in range(7):
            for j in range(7):
                tot = i + j
                if "1x" in etq or ("doble" in mkt and "1x" in etq):
                    g = 1.0 if i >= j else 0.0
                elif "x2" in etq or ("doble" in mkt and "x2" in etq):
                    g = 1.0 if j >= i else 0.0
                elif "12" in etq and "doble" in mkt:
                    g = 1.0 if i != j else 0.0
                elif "dnb" in etq or "empate no acción" in mkt or "sin empate" in mkt:
                    if "visita" in etq or "2" in etq:
                        g = 1.0 if j > i else (0.5 if i == j else 0.0)
                    else:
                        g = 1.0 if i > j else (0.5 if i == j else 0.0)
                elif "ambos" in mkt or "btts" in etq:
                    es_no = ("no" in etq)
                    ambos = (i >= 1 and j >= 1)
                    g = (0.0 if ambos else 1.0) if es_no else (1.0 if ambos else 0.0)
                elif "+" in etq or "más" in etq or "over" in etq:
                    ln = val_linea if val_linea is not None else 1.5
                    g = 1.0 if tot > ln else 0.0
                elif "-" in etq or "menos" in etq or "under" in etq:
                    ln = val_linea if val_linea is not None else 3.5
                    g = 1.0 if tot < ln else 0.0
                elif "visita" in etq or etq == "2":
                    g = 1.0 if j > i else 0.0
                else:
                    # Por defecto victoria/hándicap local
                    g = 1.0 if i > j else (0.5 if i == j else 0.0)
                grid[i, j] = g * pesos_celda[i, j]
        vec[:49] = grid.ravel()

    norma = float(np.linalg.norm(vec))
    if norma > 1e-12:
        vec = vec / norma
    else:
        vec[0] = 1.0
    return vec


def seleccionar_top5_dpp_map(
    candidatos: Sequence[Dict[str, Any]],
    *,
    k: int = 5,
    temperatura_global: float = 2.0,
    matriz_7x7: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """
    Uso B — Selecciona el Top `k` descorrelacionado del Ticket mediante `PoolDPP-MAP`
    a partir de los mercados candidatos que superaron los filtros del Gatekeeper.
    """
    N = len(candidatos)
    if N == 0 or k <= 0:
        return {
            "top_dpp": [],
            "indices_seleccionados": [],
            "log_det": 0.0,
            "correlacion_media_antes": 0.0,
            "correlacion_media_despues": 0.0,
        }
    embeddings = np.column_stack([
        construir_embedding_mercado_top5(item, matriz_7x7=matriz_7x7)
        for item in candidatos
    ])
    calidades = np.array([
        max(1e-6, float(item.get("score_gatekeeper") or item.get("probabilidad") or 0.70))
        for item in candidatos
    ], dtype=float)

    corr_antes = _correlacion_media_pares(embeddings)
    indices, log_det = greedy_map_k_dpp(
        embeddings,
        calidades,
        k=int(k),
        temperatura_global=float(temperatura_global),
    )
    top_dpp = [dict(candidatos[i]) for i in indices]
    emb_sel = embeddings[:, indices] if indices else np.zeros((embeddings.shape[0], 0))
    corr_despues = _correlacion_media_pares(emb_sel)

    return {
        "top_dpp": top_dpp,
        "indices_seleccionados": indices,
        "log_det": float(log_det),
        "correlacion_media_antes": round(float(corr_antes), 6),
        "correlacion_media_despues": round(float(corr_despues), 6),
    }
