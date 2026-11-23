"""
motor_autonomo/diagnostico_topologico.py
───────────────────────────────────────────────────────────────────────────────
CAPA 4 — Diagnóstico Topológico de Entrelazamiento, Representation Probe
y Aduana Estadística Benjamini-Hochberg FDR:
  1. Algoritmo 1 (Ren & Lim, ICML 2026, arXiv:2606.31856v2):
     - Proyección a R^3 vía PCA ajustado en entrenamiento (o directo si D == 3).
     - Grafo k-NN mutuo filtrado por epsilon.
     - Bosque generador BFS + base de ciclos fundamentales (longitud >= 4).
     - Integral de enlace de Gauss por cuadratura del punto medio (N_sub = 4).
     - Índice continuo de solapamiento local k-NN alrededor del partido actual.
  2. Representation Probe (Prada-Corral et al., NeurIPS 2026, §E.1):
     - AUC_sep = Pr[d_crit > d_surf] en distancia coseno entre pares opuestos vs iguales.
  3. Aduana Benjamini-Hochberg FDR sobre las M_total hipótesis exploradas.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections import deque
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def _imputar_nan_con_cero_centrado(X: np.ndarray) -> np.ndarray:
    """Reemplaza NaN en coordenadas ya estandarizadas z-score por 0.0 solo para álgebra lineal PCA/distancias."""
    arr = np.asarray(X, dtype=float)
    return np.where(np.isfinite(arr), arr, 0.0)


def proyectar_pca_3d_fit_on_train(
    X_train: np.ndarray,
    x_actual: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """
    Proyecta `X_train` (n x D) y `x_actual` (D,) a R^3 ajustando PCA únicamente
    sobre `X_train`. Si D == 3, conserva directamente las coordenadas en R^3.
    """
    X_clean = _imputar_nan_con_cero_centrado(X_train)
    if X_clean.ndim != 2 or X_clean.shape[0] == 0:
        return np.zeros((0, 3), dtype=float), (np.zeros(3, dtype=float) if x_actual is not None else None)
    n, D = X_clean.shape
    if D == 3:
        x_act_3d = _imputar_nan_con_cero_centrado(x_actual)[:3] if x_actual is not None else None
        return X_clean.copy(), x_act_3d
    if D < 3:
        out_tr = np.zeros((n, 3), dtype=float)
        out_tr[:, :D] = X_clean
        out_act = None
        if x_actual is not None:
            out_act = np.zeros(3, dtype=float)
            x_c = _imputar_nan_con_cero_centrado(x_actual)
            out_act[: min(D, x_c.size)] = x_c[: min(D, x_c.size)]
        return out_tr, out_act

    mu = np.mean(X_clean, axis=0, keepdims=True)
    Xc = X_clean - mu
    _, _, vt = np.linalg.svd(Xc, full_matrices=False)
    comp = vt[:3, :].T  # (D, 3)
    if comp.shape[1] < 3:
        pad = np.zeros((D, 3), dtype=float)
        pad[:, : comp.shape[1]] = comp
        comp = pad
    X_3d = Xc @ comp
    x_act_3d = None
    if x_actual is not None:
        xa = _imputar_nan_con_cero_centrado(x_actual).reshape(1, -1)
        x_act_3d = ((xa - mu) @ comp)[0]
    return X_3d, x_act_3d


def _construir_grafo_knn_mutuo(
    puntos_3d: np.ndarray,
    k: int = 6,
    percentil_eps: float = 85.0,
) -> Dict[int, List[int]]:
    """Construye grafo no dirigido k-NN mutuo filtrado por radio epsilon."""
    n = puntos_3d.shape[0]
    adj: Dict[int, List[int]] = {i: [] for i in range(n)}
    if n < 4:
        return adj
    k_eff = min(max(2, int(k)), n - 1)
    diff = puntos_3d[:, np.newaxis, :] - puntos_3d[np.newaxis, :, :]
    dist_mat = np.linalg.norm(diff, axis=2)
    np.fill_diagonal(dist_mat, np.inf)

    vecinos_k: List[set[int]] = []
    dist_kth: List[float] = []
    for i in range(n):
        orden = np.argsort(dist_mat[i])[:k_eff]
        vecinos_k.append(set(int(x) for x in orden))
        dist_kth.append(float(dist_mat[i, orden[-1]]))

    eps_corte = float(np.percentile(dist_kth, percentil_eps)) * 1.15
    for i in range(n):
        for j in vecinos_k[i]:
            if j > i and (i in vecinos_k[j]) and dist_mat[i, j] <= eps_corte:
                adj[i].append(j)
                adj[j].append(i)
    return adj


def _extraer_ciclos_fundamentales_bfs(
    adj: Dict[int, List[int]],
    min_longitud: int = 4,
    max_ciclos: int = 12,
) -> List[List[int]]:
    """
    Extrae una base de ciclos fundamentales del grafo mediante bosque generador BFS.
    Cada arista no perteneciente al árbol (u, v) cierra un ciclo único en el árbol.
    """
    n = len(adj)
    visitado = [False] * n
    padre = [-1] * n
    profundidad = [0] * n
    aristas_no_arbol: List[Tuple[int, int]] = []

    for raiz in range(n):
        if visitado[raiz]:
            continue
        visitado[raiz] = True
        cola: deque[int] = deque([raiz])
        while cola:
            u = cola.popleft()
            for v in adj.get(u, []):
                if not visitado[v]:
                    visitado[v] = True
                    padre[v] = u
                    profundidad[v] = profundidad[u] + 1
                    cola.append(v)
                elif v != padre[u] and u < v:
                    aristas_no_arbol.append((u, v))

    ciclos: List[List[int]] = []
    for (u, v) in aristas_no_arbol:
        camino_u: List[int] = []
        camino_v: List[int] = []
        cu, cv = u, v
        ancestros_u: Dict[int, int] = {}
        paso = 0
        while cu != -1:
            ancestros_u[cu] = paso
            camino_u.append(cu)
            cu = padre[cu]
            paso += 1
        lca = -1
        while cv != -1:
            if cv in ancestros_u:
                lca = cv
                break
            camino_v.append(cv)
            cv = padre[cv]
        if lca == -1:
            continue
        idx_lca = ancestros_u[lca]
        ciclo = camino_u[: idx_lca + 1] + list(reversed(camino_v))
        if len(ciclo) >= int(min_longitud):
            ciclos.append(ciclo)
            if len(ciclos) >= int(max_ciclos):
                break

    # Ordenar ciclos por longitud descendente para priorizar lazos macroscópicos
    ciclos.sort(key=len, reverse=True)
    return ciclos[:max_ciclos]


def integral_enlace_gauss(
    ciclo_c: np.ndarray,
    ciclo_d: np.ndarray,
    n_sub: int = 4,
    eps: float = 1e-9,
) -> float:
    """
    Calcula la integral de enlace de Gauss entre dos curvas poligonales cerradas
    C y D en R^3 por cuadratura del punto medio con `n_sub` subdivisiones por arista:
      link(C, D) = (1 / (4 * pi)) * oint_C oint_D ((x - y) . (dx x dy)) / ||x - y||^3
    """
    def _subdividir_curva_cerrada(pts: np.ndarray, m: int) -> Tuple[np.ndarray, np.ndarray]:
        k = pts.shape[0]
        mids: List[np.ndarray] = []
        dvec: List[np.ndarray] = []
        for i in range(k):
            p0 = pts[i]
            p1 = pts[(i + 1) % k]
            delta = (p1 - p0) / float(m)
            for s in range(m):
                a = p0 + s * delta
                b = a + delta
                mids.append(0.5 * (a + b))
                dvec.append(delta)
        return np.asarray(mids, dtype=float), np.asarray(dvec, dtype=float)

    mid_c, dx = _subdividir_curva_cerrada(np.asarray(ciclo_c, dtype=float), max(1, int(n_sub)))
    mid_d, dy = _subdividir_curva_cerrada(np.asarray(ciclo_d, dtype=float), max(1, int(n_sub)))

    # Diferencias por pares (N_c, N_d, 3)
    r_vec = mid_c[:, np.newaxis, :] - mid_d[np.newaxis, :, :]
    dist = np.linalg.norm(r_vec, axis=2)
    dist3 = np.maximum(dist ** 3, eps)

    # Producto cruz dx x dy para cada par (N_c, N_d, 3)
    cruz = np.cross(dx[:, np.newaxis, :], dy[np.newaxis, :, :])
    num = np.sum(r_vec * cruz, axis=2)
    integral = float(np.sum(num / dist3)) / (4.0 * math.pi)
    return integral


def calcular_entrelazamiento_local_knn(
    X_train: np.ndarray,
    y_train: np.ndarray,
    x_actual: np.ndarray,
    k_vecinos: int = 8,
) -> float:
    """
    Calcula el Índice de Solapamiento Local k-NN en [0, 1] alrededor de `x_actual`:
      entrelazamiento_local(x_actual) = 1 - |2 * mean(y_{kNN}) - 1|
    donde 0.0 indica vecindario puro de una sola clase y 1.0 empate 50/50.
    """
    X_clean = _imputar_nan_con_cero_centrado(X_train)
    y_arr = np.asarray(y_train, dtype=float)
    if X_clean.ndim != 2 or X_clean.shape[0] == 0 or y_arr.size == 0:
        return 0.0
    xa = _imputar_nan_con_cero_centrado(x_actual).reshape(1, -1)
    if xa.shape[1] != X_clean.shape[1]:
        d_min = min(xa.shape[1], X_clean.shape[1])
        xa = xa[:, :d_min]
        X_clean = X_clean[:, :d_min]

    dists = np.linalg.norm(X_clean - xa, axis=1)
    k_eff = min(max(1, int(k_vecinos)), X_clean.shape[0])
    idx_knn = np.argsort(dists)[:k_eff]
    prop_1 = float(np.mean(y_arr[idx_knn]))
    return float(1.0 - abs(2.0 * prop_1 - 1.0))


def diagnosticar_entrelazamiento_topologico(
    X_train: np.ndarray,
    y_train: np.ndarray,
    x_actual: Optional[np.ndarray] = None,
    *,
    k_vecinos: int = 8,
    umbral_cautela: float = 0.75,
    n_sub_gauss: int = 4,
) -> Dict[str, Any]:
    """
    Ejecuta el Algoritmo 1 de detección de entrelazamiento topológico (Gauss linking
    number sobre ciclos fundamentales en R^3) más el índice continuo `entrelazamiento_local`.
    """
    X_arr = np.asarray(X_train, dtype=float)
    y_arr = np.asarray(y_train, dtype=int)
    if X_arr.ndim != 2 or X_arr.shape[0] < 6 or y_arr.size != X_arr.shape[0]:
        return {
            "link_gauss_detectado": False,
            "max_abs_link_gauss": 0.0,
            "max_abs_link_entero": 0,
            "n_ciclos_clase_1": 0,
            "n_ciclos_clase_0": 0,
            "entrelazamiento_local": 0.0,
            "alerta_cautela_topologica": False,
        }

    X_3d, x_act_3d = proyectar_pca_3d_fit_on_train(X_arr, x_actual)
    pts_1 = X_3d[y_arr == 1]
    pts_0 = X_3d[y_arr == 0]

    ciclos_1: List[List[int]] = []
    ciclos_0: List[List[int]] = []
    if pts_1.shape[0] >= 4 and pts_0.shape[0] >= 4:
        k_g1 = min(max(3, int(k_vecinos)), pts_1.shape[0] - 1)
        k_g0 = min(max(3, int(k_vecinos)), pts_0.shape[0] - 1)
        g1 = _construir_grafo_knn_mutuo(pts_1, k=k_g1)
        g0 = _construir_grafo_knn_mutuo(pts_0, k=k_g0)
        ciclos_1 = _extraer_ciclos_fundamentales_bfs(g1, min_longitud=4)
        ciclos_0 = _extraer_ciclos_fundamentales_bfs(g0, min_longitud=4)
        # Si el grafo tiene aristas no-árbol (componente cíclica), añadir también el lazo
        # macroscópico ordenado angularmente en su plano principal 2D para evitar que
        # cuerdas locales k-NN fragmenten un anillo global en triángulos de longitud 3.
        for pts_cls, adj_cls, lista_ciclos in ((pts_1, g1, ciclos_1), (pts_0, g0, ciclos_0)):
            n_aristas = sum(len(v) for v in adj_cls.values()) // 2
            if pts_cls.shape[0] >= 6 and n_aristas >= pts_cls.shape[0]:
                c_mu = np.mean(pts_cls, axis=0, keepdims=True)
                pts_c = pts_cls - c_mu
                _, s_vals, vt_c = np.linalg.svd(pts_c, full_matrices=False)
                # Verificar que no sea un cúmulo esférico colapsado con agujero nulo:
                # evaluar distancia mínima al centroide en el plano 2D principal
                proy_2d = pts_c @ vt_c[:2, :].T
                radios = np.linalg.norm(proy_2d, axis=1)
                if float(np.min(radios)) > 0.35 * float(np.median(radios)):
                    angulos = np.arctan2(proy_2d[:, 1], proy_2d[:, 0])
                    lazo_macro = [int(i) for i in np.argsort(angulos)]
                    lista_ciclos.insert(0, lazo_macro)

    max_abs_link = 0.0
    for c1 in ciclos_1:
        curva_1 = pts_1[c1]
        for c0 in ciclos_0:
            curva_0 = pts_0[c0]
            val_link = abs(integral_enlace_gauss(curva_1, curva_0, n_sub=n_sub_gauss))
            if val_link > max_abs_link:
                max_abs_link = val_link

    link_entero = int(round(max_abs_link))
    link_detectado = bool(max_abs_link >= 0.55 and link_entero >= 1)

    ent_local = (
        calcular_entrelazamiento_local_knn(X_arr, y_arr, x_actual, k_vecinos=k_vecinos)
        if x_actual is not None
        else 0.0
    )
    alerta = bool(link_detectado or (ent_local + 1e-12 >= float(umbral_cautela)))

    return {
        "link_gauss_detectado": link_detectado,
        "max_abs_link_gauss": round(float(max_abs_link), 4),
        "max_abs_link_entero": link_entero,
        "n_ciclos_clase_1": len(ciclos_1),
        "n_ciclos_clase_0": len(ciclos_0),
        "entrelazamiento_local": round(float(ent_local), 6),
        "alerta_cautela_topologica": alerta,
    }


def evaluar_probe_separacion_variables(
    X_hist: np.ndarray,
    y_hist: np.ndarray,
    *,
    auc_minimo: float = 0.52,
) -> Dict[str, Any]:
    """
    Representation Probe (§E.1 de Prada-Corral et al., NeurIPS 2026):
      Compara la distancia coseno entre pares de partidos con resultados opuestos (d_crit)
      frente a pares con el mismo resultado (d_surf):
        AUC_sep = Pr[d_crit > d_surf] + 0.5 * Pr[d_crit == d_surf]
    """
    X_clean = _imputar_nan_con_cero_centrado(X_hist)
    y_arr = np.asarray(y_hist, dtype=int)
    n = X_clean.shape[0] if X_clean.ndim == 2 else 0
    if n < 4 or len(set(y_arr.tolist())) < 2:
        return {
            "auc_separacion": 0.50,
            "tiene_poder_separador": False,
            "diagnostico": "espacio sin poder separador",
            "n_pares_opuestos": 0,
            "n_pares_iguales": 0,
        }

    normas = np.linalg.norm(X_clean, axis=1, keepdims=True)
    X_unit = np.divide(X_clean, np.maximum(normas, 1e-12))
    sim_mat = np.clip(X_unit @ X_unit.T, -1.0, 1.0)
    dist_mat = 1.0 - sim_mat

    d_crit: List[float] = []
    d_surf: List[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            d_ij = float(dist_mat[i, j])
            if y_arr[i] != y_arr[j]:
                d_crit.append(d_ij)
            else:
                d_surf.append(d_ij)

    if not d_crit or not d_surf:
        return {
            "auc_separacion": 0.50,
            "tiene_poder_separador": False,
            "diagnostico": "espacio sin poder separador",
            "n_pares_opuestos": len(d_crit),
            "n_pares_iguales": len(d_surf),
        }

    # Estadístico de Mann-Whitney exacto en O(N_crit log N_surf)
    surf_sorted = np.sort(np.asarray(d_surf, dtype=float))
    crit_arr = np.asarray(d_crit, dtype=float)
    menores = np.searchsorted(surf_sorted, crit_arr, side="left")
    hasta = np.searchsorted(surf_sorted, crit_arr, side="right")
    iguales = hasta - menores
    wins = float(np.sum(menores) + 0.5 * np.sum(iguales))
    total_comparaciones = float(len(d_crit) * len(d_surf))
    auc = wins / total_comparaciones if total_comparaciones > 0 else 0.50

    tiene_poder = bool(auc > float(auc_minimo))
    return {
        "auc_separacion": round(float(auc), 6),
        "tiene_poder_separador": tiene_poder,
        "diagnostico": "espacio con poder separador" if tiene_poder else "espacio sin poder separador",
        "media_d_crit": round(float(np.mean(crit_arr)), 6),
        "media_d_surf": round(float(np.mean(surf_sorted)), 6),
        "n_pares_opuestos": len(d_crit),
        "n_pares_iguales": len(d_surf),
    }


def calcular_q_valores_benjamini_hochberg(
    p_valores: Sequence[float],
    m_total_explorados: Optional[int] = None,
) -> List[float]:
    """
    Corrección Benjamini-Hochberg (FDR) sobre `p_valores`, usando `M_total`
    (el conteo total de hipótesis exploradas, que puede ser >= len(p_valores)):
      q_(i) = min_{j >= i} min(1.0, (M_total / j) * p_(j))
    """
    n = len(p_valores)
    if n == 0:
        return []
    M = max(n, int(m_total_explorados or n))
    p_arr = np.clip(np.asarray(p_valores, dtype=float), 0.0, 1.0)
    orden = np.argsort(p_arr)
    p_ord = p_arr[orden]

    q_ord = np.zeros(n, dtype=float)
    min_acum = 1.0
    for idx_rev in range(n - 1, -1, -1):
        rango_j = idx_rev + 1  # 1-indexed
        val = min(1.0, (float(M) / float(rango_j)) * float(p_ord[idx_rev]))
        if val < min_acum:
            min_acum = val
        q_ord[idx_rev] = min_acum

    q_out = np.zeros(n, dtype=float)
    q_out[orden] = q_ord
    return [float(x) for x in q_out]


def aplicar_aduana_benjamini_hochberg(
    patrones: Sequence[Dict[str, Any]],
    *,
    m_total_explorados: Optional[int] = None,
    q_max: float = 0.05,
) -> Dict[str, Any]:
    """
    Aplica la Aduana Estadística BH-FDR sobre los patrones candidatos/ortogonales:
      - Si q_i < q_max y delta_brier > 0: `estado_validacion = "validado_oos_fdr"`.
      - En cualquier otro caso: `estado_validacion = "hipotesis_en_observacion"`, `peso_produccion = 0.0`.
    """
    if not patrones:
        return {
            "m_total_explorados": int(m_total_explorados or 0),
            "q_max": float(q_max),
            "n_validados_oos_fdr": 0,
            "patrones": [],
        }

    pvals = [float(p.get("p_valor_exacto_binomial", 1.0)) for p in patrones]
    M = max(len(patrones), int(m_total_explorados or len(patrones)))
    qvals = calcular_q_valores_benjamini_hochberg(pvals, m_total_explorados=M)

    out_patrones: List[Dict[str, Any]] = []
    n_val = 0
    for pat, qv in zip(patrones, qvals):
        copia = dict(pat)
        copia["q_valor_bh_fdr"] = round(float(qv), 6)
        mejora_brier = float(copia.get("delta_brier") or 0.0) > 0.0
        es_valido = (float(qv) < float(q_max)) and mejora_brier
        if es_valido:
            copia["estado_validacion"] = "validado_oos_fdr"
            n_val += 1
        else:
            copia["estado_validacion"] = "hipotesis_en_observacion"
            copia["peso_produccion"] = 0.0
        out_patrones.append(copia)

    return {
        "m_total_explorados": M,
        "q_max": float(q_max),
        "n_validados_oos_fdr": n_val,
        "patrones": out_patrones,
    }
