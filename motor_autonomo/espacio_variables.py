"""
motor_autonomo/espacio_variables.py
───────────────────────────────────────────────────────────────────────────────
CAPA 0 — Construcción del Espacio de Variables Observables:
  1. Corte temporal estricto: `fecha_partido < cutoff_timestamp` (cero fuga temporal).
  2. Exclusión estricta de amistosos (`entra_calibracion == False`).
  3. Intersección comparable simétrica Punto 44 (`claves_comunes[p] = A[p] ∩ B[p]`)
     con cobertura real observada >= `cobertura_minima` (por defecto 0.60, >= 9/15).
  4. Diferenciación estricta entre `0.0` observado y `None` ausente (`sin_dato`).
     Prohibido imputar `None` con `0.0` o con medias sintéticas.
  5. Estadísticos reales por variable (media ponderada por recencia 5 vs 10, mediana,
     [mín, P20, P80, máx], suma s_v, diferencial delta_v y cruce ataque/defensa c_v).
  6. Estandarización estrictamente fit-on-past (`z_j = (x_j - mu_past) / max(sigma_past, 1e-6)`).
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

# Las 45 variables estadísticas observables canónicas en matrices FT / 1T / 2T
VARIABLES_45_CANONICAS: Tuple[str, ...] = (
    "xg",
    "posesion_pct",
    "remates_totales",
    "remates_puerta",
    "grandes_ocasiones",
    "grandes_ocasiones_falladas",
    "corners",
    "pases_total",
    "pases_precision_pct",
    "pases_ratio",
    "tarjetas_amarillas",
    "tarjetas_rojas",
    "xgot",
    "xg_juego_abierto",
    "xg_balon_parado",
    "xg_sin_penalti",
    "remates_fuera",
    "remates_rechazados",
    "remates_dentro_area",
    "remates_fuera_area",
    "al_palo",
    "toques_area_rival",
    "pases_entre_lineas",
    "fueras_juego",
    "tiros_libres",
    "pases_largos_pct",
    "pases_tercio_final_pct",
    "cruces_pct",
    "xa_equipo",
    "saques_banda",
    "faltas",
    "entradas_pct",
    "duelos_ganados",
    "duelos_suelo_pct",
    "duelos_aereos_pct",
    "despejes",
    "intercepciones",
    "bloqueos",
    "recuperaciones",
    "errores_remate",
    "errores_gol",
    "paradas",
    "xgot_enfrentado",
    "goles_evitados",
    "saques_puerta",
)

# Pares espejo ataque vs defensa rival (cuando ambos están observados)
PARES_ESPEJO_ATAQUE_DEFENSA: Dict[str, str] = {
    "xg": "xgot_enfrentado",
    "xgot": "xgot_enfrentado",
    "remates_puerta": "paradas",
    "remates_totales": "bloqueos",
    "toques_area_rival": "despejes",
    "pases_entre_lineas": "intercepciones",
}

# Catálogo canónico de eventos objetivo 100 % liquidables automáticamente
CATALOGO_EVENTOS_OBJETIVO: Tuple[str, ...] = (
    "+1.5 FT",
    "+2.5 FT",
    "-3.5 FT",
    "-4.5 FT",
    "BTTS Sí",
    "1X",
    "X2",
    "Local Gana",
    "Visita Gana",
    "+0.5 1T",
    "+0.5 2T",
    "+1.5 2T",
    "+7.5 Córn.",
    "+8.5 Córn.",
    "-11.5 Córn.",
    "-12.5 Córn.",
    "-3.5 Tarj.",
    "-4.5 Tarj.",
    "+3.5 Tarj.",
)


def parsear_valor_observado(val: Any) -> Optional[float]:
    """
    Convierte un valor observado a `float` o devuelve `None` si es dato faltante.
    - `0`, `0.0`, `"0"`, `"0.0"` -> `0.0` (observado real).
    - `None`, `""`, `"sin_dato"`, `"None"`, `"null"`, `"nan"`, `bool`, `NaN`/`Inf` -> `None`.
    """
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        fval = float(val)
        return fval if math.isfinite(fval) else None
    s = str(val).strip()
    if not s or s.lower() in ("sin_dato", "none", "null", "nan", "n/a", "s/d", "-"):
        return None
    try:
        fval = float(s)
        return fval if math.isfinite(fval) else None
    except (ValueError, TypeError):
        return None


def _extraer_timestamp_comparable(partido: dict[str, Any]) -> str:
    """Devuelve la cadena ISO de fecha/inicio del partido (YYYY-MM-DD...) ignorando 'sin_dato'."""
    for k in ("inicio_utc", "hora_inicio_partido", "fecha_partido", "fecha"):
        v = partido.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if len(s) >= 10 and s[:4].isdigit() and s[4] == "-" and s[7] == "-":
            return s
    return ""


def es_partido_amistoso(partido: dict[str, Any]) -> bool:
    """Detecta si un partido es amistoso o tiene `entra_calibracion == False`."""
    if partido.get("entra_calibracion") is False or partido.get("entra_promedios") is False:
        return True
    tipo = str(partido.get("tipo_partido") or "").strip().lower()
    comp = str(partido.get("competicion") or partido.get("torneo") or "").strip().lower()
    if tipo == "amistoso":
        return True
    return any(tok in comp for tok in ("friendly", "friendlies", "amistoso", "club friendlies"))


def filtrar_partidos_por_cutoff(
    partidos: Sequence[dict[str, Any]],
    cutoff_timestamp: str,
    *,
    excluir_amistosos: bool = True,
) -> List[dict[str, Any]]:
    """
    Filtra partidos garantizando cero fuga temporal (`fecha_partido < cutoff_timestamp`)
    y excluyendo amistosos.
    """
    cutoff = str(cutoff_timestamp or "").strip()
    if not cutoff:
        raise ValueError("cutoff_timestamp es obligatorio para garantizar cero fuga temporal.")
    cutoff_day = cutoff[:10]
    filtrados: List[dict[str, Any]] = []
    for p in partidos:
        if not isinstance(p, dict):
            continue
        if excluir_amistosos and es_partido_amistoso(p):
            continue
        ts = _extraer_timestamp_comparable(p)
        if not ts:
            continue
        # Si ambos tienen hora ISO completa en el mismo día, compara timestamp completo;
        # de lo contrario, exige estrictamente fecha[:10] < cutoff[:10]
        if len(ts) > 10 and len(cutoff) > 10 and ts[:10] == cutoff_day:
            es_anterior = (ts < cutoff)
        else:
            es_anterior = (ts[:10] < cutoff_day)
        if es_anterior:
            filtrados.append(p)
    return filtrados


def extraer_valor_variable_partido(
    partido: dict[str, Any],
    variable: str,
    periodo: str = "FT",
) -> Optional[float]:
    """
    Extrae el valor de `variable` en `periodo` (`FT`, `1T`, `2T`) desde un diccionario
    de partido sin inventar ni imputar `None` con `0.0`.
    """
    per = str(periodo or "FT").upper()
    # 1) Si el registro ya corresponde al periodo solicitado (formato banco_partidos.csv)
    per_reg = str(partido.get("periodo") or "").upper()
    if per_reg == per and variable in partido:
        return parsear_valor_observado(partido.get(variable))

    # 2) Subdiccionario por_periodo / estadisticas_por_periodo / periodos
    for contenedor_k in ("por_periodo", "estadisticas_por_periodo", "periodos"):
        cont = partido.get(contenedor_k)
        if isinstance(cont, dict):
            sub_p = cont.get(per)
            if isinstance(sub_p, dict) and variable in sub_p:
                return parsear_valor_observado(sub_p.get(variable))

    # 3) Clave con sufijo de periodo (ej. corners_1t, xg_2t) o clave directa en FT
    suf = f"_{per.lower()}"
    if variable + suf in partido:
        return parsear_valor_observado(partido.get(variable + suf))
    if per == "FT" and variable in partido:
        return parsear_valor_observado(partido.get(variable))

    # 4) Subdiccionario `estadisticas`
    est = partido.get("estadisticas")
    if isinstance(est, dict):
        if per == "FT" and variable in est:
            return parsear_valor_observado(est.get(variable))
        if variable + suf in est:
            return parsear_valor_observado(est.get(variable + suf))
    return None


def calcular_cobertura_simetrica_punto44(
    partidos_local: Sequence[dict[str, Any]],
    partidos_visita: Sequence[dict[str, Any]],
    *,
    periodo: str = "FT",
    cobertura_minima: float = 0.60,
    variables_candidatas: Sequence[str] = VARIABLES_45_CANONICAS,
) -> Dict[str, Any]:
    """
    Intersección comparable simétrica (Punto 44):
      `claves_comunes[periodo] = A[periodo] ∩ B[periodo]`
    donde cada variable debe tener cobertura observada real >= `cobertura_minima`
    en ambos equipos.
    """
    n_l = len(partidos_local)
    n_v = len(partidos_visita)
    cobertura_l: Dict[str, float] = {}
    cobertura_v: Dict[str, float] = {}
    claves_l: List[str] = []
    claves_v: List[str] = []
    claves_comunes: List[str] = []

    for var in variables_candidatas:
        obs_l = sum(1 for p in partidos_local if extraer_valor_variable_partido(p, var, periodo) is not None)
        obs_v = sum(1 for p in partidos_visita if extraer_valor_variable_partido(p, var, periodo) is not None)
        cob_l = (obs_l / n_l) if n_l > 0 else 0.0
        cob_v = (obs_v / n_v) if n_v > 0 else 0.0
        cobertura_l[var] = cob_l
        cobertura_v[var] = cob_v
        if cob_l + 1e-12 >= cobertura_minima and obs_l > 0:
            claves_l.append(var)
        if cob_v + 1e-12 >= cobertura_minima and obs_v > 0:
            claves_v.append(var)
        if (cob_l + 1e-12 >= cobertura_minima and obs_l > 0) and (cob_v + 1e-12 >= cobertura_minima and obs_v > 0):
            claves_comunes.append(var)

    return {
        "periodo": str(periodo).upper(),
        "n_partidos_local": n_l,
        "n_partidos_visita": n_v,
        "cobertura_minima": float(cobertura_minima),
        "claves_local": claves_l,
        "claves_visita": claves_v,
        "claves_comunes": claves_comunes,
        "cobertura_local": cobertura_l,
        "cobertura_visita": cobertura_v,
    }


def extraer_estadisticos_variable_equipo(
    valores_cronologicos: Sequence[Optional[float]],
) -> Optional[Dict[str, float]]:
    """
    Calcula sobre los valores reales observados (ignorando `None`, nunca convirtiendo `None` en `0.0`):
      - Media ponderada por recencia (últimos 5 partidos peso 0.60 vs previos 10 peso 0.40).
      - Media simple, mediana y cuantiles empíricos [mín, P20, P80, máx].
    Devuelve `None` si no hay ningún valor observado.
    """
    observados = [float(x) for x in valores_cronologicos if x is not None and math.isfinite(float(x))]
    if not observados:
        return None
    arr = np.asarray(observados, dtype=float)
    if len(observados) > 5:
        recientes = np.asarray(observados[-5:], dtype=float)
        previos = np.asarray(observados[:-5], dtype=float)
        media_recencia = 0.60 * float(np.mean(recientes)) + 0.40 * float(np.mean(previos))
    else:
        media_recencia = float(np.mean(arr))

    return {
        "n_obs": float(len(observados)),
        "media_recencia": float(media_recencia),
        "media_simple": float(np.mean(arr)),
        "mediana": float(np.percentile(arr, 50)),
        "min": float(np.min(arr)),
        "p20": float(np.percentile(arr, 20)),
        "p80": float(np.percentile(arr, 80)),
        "max": float(np.max(arr)),
    }


@dataclass
class EstandarizadorFitOnPast:
    """
    Estandarizador z-score ajustado EXCLUSIVAMENTE sobre el conjunto histórico
    pasado (`fecha < cutoff_timestamp`). Respeta `None`/`np.nan` sin imputar `0.0`.
    """
    nombres_variables: List[str] = field(default_factory=list)
    medias_: Optional[np.ndarray] = None
    sigmas_: Optional[np.ndarray] = None
    cutoff_ajuste: Optional[str] = None

    def fit(self, X_past: np.ndarray, nombres_variables: Sequence[str], cutoff_timestamp: str) -> "EstandarizadorFitOnPast":
        if X_past.ndim != 2:
            raise ValueError("X_past debe ser una matriz 2D (n_partidos, n_variables).")
        self.nombres_variables = list(nombres_variables)
        self.cutoff_ajuste = str(cutoff_timestamp)
        D = X_past.shape[1]
        medias = np.full(D, np.nan, dtype=float)
        sigmas = np.ones(D, dtype=float)
        for j in range(D):
            col = X_past[:, j]
            mask = np.isfinite(col)
            if np.any(mask):
                vals = col[mask]
                medias[j] = float(np.mean(vals))
                std_j = float(np.std(vals, ddof=0))
                sigmas[j] = max(std_j, 1e-6)
        self.medias_ = medias
        self.sigmas_ = sigmas
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.medias_ is None or self.sigmas_ is None:
            raise RuntimeError("EstandarizadorFitOnPast debe ajustarse con fit() antes de transform().")
        arr = np.asarray(X, dtype=float)
        es_1d = (arr.ndim == 1)
        if es_1d:
            arr = arr[np.newaxis, :]
        out = np.full_like(arr, np.nan, dtype=float)
        for j in range(arr.shape[1]):
            mu_j = self.medias_[j]
            sd_j = max(float(self.sigmas_[j]), 1e-6)
            if not np.isfinite(mu_j):
                continue
            col = arr[:, j]
            mask = np.isfinite(col)
            out[mask, j] = (col[mask] - mu_j) / sd_j
        return out[0] if es_1d else out


def evaluar_evento_objetivo_partido(
    partido_ft: dict[str, Any],
    objetivo: str,
    *,
    partido_1t: Optional[dict[str, Any]] = None,
    partido_2t: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """
    Evalúa de forma 100 % determinista y liquidable el resultado binario `{0, 1}`
    de un evento del `CATALOGO_EVENTOS_OBJETIVO` sobre un partido finalizado.
    Devuelve `None` si falta el dato real requerido (nunca inventa `0`).
    """
    if objetivo not in CATALOGO_EVENTOS_OBJETIVO:
        raise ValueError(f"Objetivo {objetivo!r} no pertenece al catálogo liquidable: {CATALOGO_EVENTOS_OBJETIVO}")

    gf_ft = parsear_valor_observado(partido_ft.get("goles_favor"))
    gc_ft = parsear_valor_observado(partido_ft.get("goles_contra"))
    if gf_ft is None or gc_ft is None:
        return None
    tot_ft = gf_ft + gc_ft

    if objetivo == "+1.5 FT":
        return 1 if tot_ft > 1.5 else 0
    if objetivo == "+2.5 FT":
        return 1 if tot_ft > 2.5 else 0
    if objetivo == "-3.5 FT":
        return 1 if tot_ft < 3.5 else 0
    if objetivo == "-4.5 FT":
        return 1 if tot_ft < 4.5 else 0
    if objetivo == "BTTS Sí":
        return 1 if (gf_ft >= 1.0 and gc_ft >= 1.0) else 0
    if objetivo == "1X":
        return 1 if gf_ft >= gc_ft else 0
    if objetivo == "X2":
        return 1 if gc_ft >= gf_ft else 0
    if objetivo == "Local Gana":
        return 1 if gf_ft > gc_ft else 0
    if objetivo == "Visita Gana":
        return 1 if gc_ft > gf_ft else 0

    if objetivo == "+0.5 1T":
        p1 = partido_1t or partido_ft
        gf_1t = parsear_valor_observado(p1.get("goles_favor_1t") if "goles_favor_1t" in p1 else (partido_1t or {}).get("goles_favor"))
        gc_1t = parsear_valor_observado(p1.get("goles_contra_1t") if "goles_contra_1t" in p1 else (partido_1t or {}).get("goles_contra"))
        if gf_1t is None or gc_1t is None:
            return None
        return 1 if (gf_1t + gc_1t) > 0.5 else 0

    if objetivo in ("+0.5 2T", "+1.5 2T"):
        p2 = partido_2t or partido_ft
        gf_2t = parsear_valor_observado(p2.get("goles_favor_2t") if "goles_favor_2t" in p2 else (partido_2t or {}).get("goles_favor"))
        gc_2t = parsear_valor_observado(p2.get("goles_contra_2t") if "goles_contra_2t" in p2 else (partido_2t or {}).get("goles_contra"))
        if gf_2t is None or gc_2t is None:
            return None
        umbral_2t = 0.5 if objetivo == "+0.5 2T" else 1.5
        return 1 if (gf_2t + gc_2t) > umbral_2t else 0

    if "Córn." in objetivo:
        c_tot = parsear_valor_observado(partido_ft.get("corners_totales"))
        if c_tot is None:
            cf = parsear_valor_observado(partido_ft.get("corners_favor") if "corners_favor" in partido_ft else partido_ft.get("corners"))
            cc = parsear_valor_observado(partido_ft.get("corners_contra"))
            if cf is not None and cc is not None:
                c_tot = cf + cc
            elif cf is not None:
                c_tot = cf * 2.0
        if c_tot is None:
            return None
        if objetivo == "+7.5 Córn.":
            return 1 if c_tot > 7.5 else 0
        if objetivo == "+8.5 Córn.":
            return 1 if c_tot > 8.5 else 0
        if objetivo == "-11.5 Córn.":
            return 1 if c_tot < 11.5 else 0
        if objetivo == "-12.5 Córn.":
            return 1 if c_tot < 12.5 else 0

    if "Tarj." in objetivo:
        t_tot = parsear_valor_observado(partido_ft.get("tarjetas_totales"))
        if t_tot is None:
            tf = parsear_valor_observado(partido_ft.get("tarjetas_amarillas_favor") if "tarjetas_amarillas_favor" in partido_ft else partido_ft.get("tarjetas_amarillas"))
            tc = parsear_valor_observado(partido_ft.get("tarjetas_amarillas_contra"))
            if tf is not None and tc is not None:
                t_tot = tf + tc
            elif tf is not None:
                t_tot = tf * 2.0
        if t_tot is None:
            return None
        if objetivo == "-3.5 Tarj.":
            return 1 if t_tot < 3.5 else 0
        if objetivo == "-4.5 Tarj.":
            return 1 if t_tot < 4.5 else 0
        if objetivo == "+3.5 Tarj.":
            return 1 if t_tot > 3.5 else 0

    return None


def cargar_partidos_banco_historico(
    ruta_csv: Optional[str] = None,
    *,
    periodo: str = "FT",
) -> List[dict[str, Any]]:
    """
    Lee `data/banco_partidos.csv` agrupando pares local/visitante cuando existan o
    filas individuales del periodo indicado, conservando estrictamente `None` para `sin_dato`.
    """
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = ruta_csv or os.path.join(raiz, "data", "banco_partidos.csv")
    if not os.path.isfile(path):
        return []

    per = str(periodo or "FT").upper()
    por_partido_per: Dict[Tuple[str, str], List[dict[str, Any]]] = {}
    with open(path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pid = str(row.get("id_partido") or "").strip()
            p_row = str(row.get("periodo") or "FT").strip().upper()
            if not pid:
                continue
            por_partido_per.setdefault((pid, p_row), []).append(row)

    # Identificar todos los id_partido únicos con fila FT
    pids_ft = sorted(
        {pid for (pid, p_row) in por_partido_per if p_row == "FT"},
        key=lambda k: (
            str(por_partido_per[(k, "FT")][0].get("fecha") or ""),
            str(por_partido_per[(k, "FT")][0].get("inicio_utc") or ""),
            k,
        ),
    )

    partidos: List[dict[str, Any]] = []
    for pid in pids_ft:
        filas_ft = por_partido_per.get((pid, "FT"), [])
        filas_p = por_partido_per.get((pid, per), filas_ft)
        if not filas_ft:
            continue
        # Ordenar de modo que 'casa' sea primero si existe
        filas_ft_ord = sorted(filas_ft, key=lambda r: 0 if str(r.get("condicion") or "").lower() == "casa" else 1)
        filas_p_ord = sorted(filas_p, key=lambda r: 0 if str(r.get("condicion") or "").lower() == "casa" else 1)
        r0_ft = filas_ft_ord[0]
        r1_ft = filas_ft_ord[1] if len(filas_ft_ord) > 1 else None
        r0_p = filas_p_ord[0]
        r1_p = filas_p_ord[1] if len(filas_p_ord) > 1 else None

        gf = parsear_valor_observado(r0_ft.get("goles_favor"))
        gc = parsear_valor_observado(r0_ft.get("goles_contra"))
        if str(r0_ft.get("condicion") or "").lower() == "fuera" and r1_ft is None:
            # Invertir perspectiva para que goles_favor sea siempre Local y goles_contra Visitante
            gf, gc = gc, gf

        reg: dict[str, Any] = {
            "id_partido": pid,
            "fecha": str(r0_ft.get("fecha") or "").strip(),
            "inicio_utc": (
            str(r0_ft.get("inicio_utc")).strip()
            if str(r0_ft.get("inicio_utc") or "").strip()[:4].isdigit()
            else str(r0_ft.get("fecha") or "").strip()
        ),
            "competicion": str(r0_ft.get("competicion") or "").strip(),
            "periodo": per,
            "goles_favor": gf,
            "goles_contra": gc,
        }
        # Mitades para objetivos 1T/2T
        if (pid, "1T") in por_partido_per:
            f1 = por_partido_per[(pid, "1T")][0]
            reg["goles_favor_1t"] = parsear_valor_observado(f1.get("goles_favor"))
            reg["goles_contra_1t"] = parsear_valor_observado(f1.get("goles_contra"))
        if (pid, "2T") in por_partido_per:
            f2 = por_partido_per[(pid, "2T")][0]
            reg["goles_favor_2t"] = parsear_valor_observado(f2.get("goles_favor"))
            reg["goles_contra_2t"] = parsear_valor_observado(f2.get("goles_contra"))

        # Córneres y tarjetas reales observados
        cf_ft = parsear_valor_observado(r0_ft.get("corners"))
        cc_ft = parsear_valor_observado(r1_ft.get("corners")) if r1_ft is not None else None
        reg["corners_favor"] = cf_ft
        reg["corners_contra"] = cc_ft
        if cf_ft is not None and cc_ft is not None:
            reg["corners_totales"] = cf_ft + cc_ft

        tf_ft = parsear_valor_observado(r0_ft.get("tarjetas_amarillas"))
        tc_ft = parsear_valor_observado(r1_ft.get("tarjetas_amarillas")) if r1_ft is not None else None
        reg["tarjetas_amarillas_favor"] = tf_ft
        reg["tarjetas_amarillas_contra"] = tc_ft
        if tf_ft is not None and tc_ft is not None:
            reg["tarjetas_totales"] = tf_ft + tc_ft

        # Variables observables en el periodo solicitado (suma/promedio o diferencial si hay par)
        for var in VARIABLES_45_CANONICAS:
            v0 = parsear_valor_observado(r0_p.get(var))
            v1 = parsear_valor_observado(r1_p.get(var)) if r1_p is not None else None
            if v0 is not None and v1 is not None:
                reg[var] = 0.5 * (v0 + v1)
                reg[f"s_{var}"] = v0 + v1
                reg[f"delta_{var}"] = v0 - v1
            else:
                reg[var] = v0
        partidos.append(reg)
    return partidos


def construir_dataset_historico_y_actual(
    partidos_historico: Sequence[dict[str, Any]],
    partido_actual: dict[str, Any],
    *,
    cutoff_timestamp: str,
    objetivo: str = "+2.5 FT",
    periodo: str = "FT",
    cobertura_minima: float = 0.60,
    variables_candidatas: Sequence[str] = VARIABLES_45_CANONICAS,
) -> Dict[str, Any]:
    """
    Construye `(X_train_z, y_train, x_actual_z, nombres_variables)` aplicando:
      1. Filtro temporal estricto `fecha < cutoff_timestamp`.
      2. Selección de variables con cobertura observada >= `cobertura_minima` en el pasado
         y presentes en `partido_actual`.
      3. Estandarización `EstandarizadorFitOnPast` ajustada SOLO sobre `X_train`.
    """
    hist_validos = filtrar_partidos_por_cutoff(partidos_historico, cutoff_timestamp, excluir_amistosos=True)
    filas_con_y: List[Tuple[dict[str, Any], int]] = []
    for p in hist_validos:
        y_val = evaluar_evento_objetivo_partido(p, objetivo)
        if y_val in (0, 1):
            filas_con_y.append((p, int(y_val)))

    n_train = len(filas_con_y)
    nombres_vars: List[str] = []
    for var in variables_candidatas:
        val_act = extraer_valor_variable_partido(partido_actual, var, periodo)
        if val_act is None:
            continue
        if n_train == 0:
            nombres_vars.append(var)
            continue
        n_obs = sum(1 for (p, _) in filas_con_y if extraer_valor_variable_partido(p, var, periodo) is not None)
        if (n_obs / n_train) + 1e-12 >= cobertura_minima:
            nombres_vars.append(var)

    D = len(nombres_vars)
    X_train = np.full((n_train, D), np.nan, dtype=float)
    y_train = np.zeros(n_train, dtype=int)
    p_base_train = np.full(n_train, 0.5, dtype=float)
    for i, (p, y_val) in enumerate(filas_con_y):
        y_train[i] = y_val
        p_b = parsear_valor_observado(p.get("prob_base"))
        p_base_train[i] = float(p_b) if (p_b is not None and 0.0 < p_b < 1.0) else 0.5
        for j, var in enumerate(nombres_vars):
            v_ij = extraer_valor_variable_partido(p, var, periodo)
            if v_ij is not None:
                X_train[i, j] = float(v_ij)

    if n_train > 0 and np.all(p_base_train == 0.5):
        pi0 = float(np.clip(np.mean(y_train), 0.05, 0.95))
        p_base_train[:] = pi0

    x_actual = np.full(D, np.nan, dtype=float)
    for j, var in enumerate(nombres_vars):
        v_j = extraer_valor_variable_partido(partido_actual, var, periodo)
        if v_j is not None:
            x_actual[j] = float(v_j)

    scaler = EstandarizadorFitOnPast()
    if n_train > 0 and D > 0:
        scaler.fit(X_train, nombres_vars, cutoff_timestamp)
        X_train_z = scaler.transform(X_train)
        x_actual_z = scaler.transform(x_actual)
    else:
        X_train_z = X_train.copy()
        x_actual_z = x_actual.copy()

    p_act = parsear_valor_observado(partido_actual.get("prob_base"))
    if p_act is None or not (0.0 < p_act < 1.0):
        p_act = float(np.clip(np.mean(y_train), 0.05, 0.95)) if n_train > 0 else 0.5

    return {
        "X_train_raw": X_train,
        "X_train_z": X_train_z,
        "y_train": y_train,
        "x_actual_raw": x_actual,
        "x_actual_z": x_actual_z,
        "p_base_train": p_base_train,
        "p_base_actual": float(p_act),
        "nombres_variables": nombres_vars,
        "estandarizador": scaler,
        "partidos_train": [p for (p, _) in filas_con_y],
    }
