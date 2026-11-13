"""
banco_historico.py
==================
Bloque 6 (Ronda 5+6): Banco histórico persistente (SQLite + CSV versionado),
catálogo de procedencia FotMob vs Flashscore para las 45 métricas + goles por
mitad, validación estricta de aditividad (1T + 2T == FT), mapa de cobertura
variable × periodo sin estimaciones sintéticas (`None` = `"sin_dato"`, nunca `0`)
y hooks no bloqueantes para `/mercados`, `/auditar` y `/evaluar`.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "banco_partidos.db"
DEFAULT_CSV_PATH = DATA_DIR / "banco_partidos.csv"

PERIODOS_OFICIALES: tuple[str, ...] = ("FT", "1T", "2T")
_ORDEN_PERIODO: dict[str, int] = {"FT": 0, "1T": 1, "2T": 2}
TOLERANCIA_ADITIVIDAD_FLOAT: float = 0.05  # umbral de redondeo Opta 2 decimales (< 0.05)

# Catálogo canónico de 45 métricas idéntico a auditoria_historica.LAS_45_METRICAS_OFICIALES
CATALOGO_45_METRICAS: tuple[tuple[str, str, str, str], ...] = (
    # A. PRINCIPALES (12)
    ("01", "xg", "Goles esperados (xG)", "PRINCIPALES"),
    ("02", "posesion_pct", "Posesión del balón (%)", "PRINCIPALES"),
    ("03", "remates_totales", "Remates totales", "PRINCIPALES"),
    ("04", "remates_puerta", "Remates a puerta", "PRINCIPALES"),
    ("05", "grandes_ocasiones", "Grandes ocasiones creadas", "PRINCIPALES"),
    ("06", "grandes_ocasiones_falladas", "Grandes ocasiones falladas", "PRINCIPALES"),
    ("07", "corners", "Saques de esquina (córneres)", "PRINCIPALES"),
    ("08", "pases_total", "Pases totales", "PRINCIPALES"),
    ("09", "pases_precision_pct", "Precisión de pases (%)", "PRINCIPALES"),
    ("10", "pases_ratio", "Pases (completados/totales)", "PRINCIPALES"),
    ("11", "tarjetas_amarillas", "Tarjetas amarillas", "PRINCIPALES"),
    ("12", "tarjetas_rojas", "Tarjetas rojas", "PRINCIPALES"),
    # B. DISPAROS (9)
    ("13", "xgot", "xG a puerta (xGOT)", "DISPAROS"),
    ("14", "xg_juego_abierto", "xG de jugada abierta", "DISPAROS"),
    ("15", "xg_balon_parado", "xG a balón parado", "DISPAROS"),
    ("16", "xg_sin_penalti", "xG sin penalti (npxG)", "DISPAROS"),
    ("17", "remates_fuera", "Remates fuera", "DISPAROS"),
    ("18", "remates_rechazados", "Remates bloqueados/rechazados", "DISPAROS"),
    ("19", "remates_dentro_area", "Remates dentro del área", "DISPAROS"),
    ("20", "remates_fuera_area", "Remates fuera del área", "DISPAROS"),
    ("21", "al_palo", "Disparos al poste/larguero", "DISPAROS"),
    # C. ATAQUES (4)
    ("22", "toques_area_rival", "Toques en el área rival", "ATAQUES"),
    ("23", "pases_entre_lineas", "Pases entre líneas / al tercio final", "ATAQUES"),
    ("24", "fueras_juego", "Fueras de juego", "ATAQUES"),
    ("25", "tiros_libres", "Tiros libres directos", "ATAQUES"),
    # D. PASES (5)
    ("26", "pases_largos_pct", "Pases largos precisos", "PASES"),
    ("27", "pases_tercio_final_pct", "Pases al tercio final", "PASES"),
    ("28", "cruces_pct", "Cruces / centros precisos", "PASES"),
    ("29", "xa_equipo", "Asistencias esperadas (xA)", "PASES"),
    ("30", "saques_banda", "Saques de banda", "PASES"),
    # E. DEFENSA (11)
    ("31", "faltas", "Faltas cometidas", "DEFENSA"),
    ("32", "entradas_pct", "Entradas (tackles ganados)", "DEFENSA"),
    ("33", "duelos_ganados", "Duelos ganados", "DEFENSA"),
    ("34", "duelos_suelo_pct", "Duelos de suelo ganados", "DEFENSA"),
    ("35", "duelos_aereos_pct", "Duelos aéreos ganados", "DEFENSA"),
    ("36", "despejes", "Despejes", "DEFENSA"),
    ("37", "intercepciones", "Intercepciones", "DEFENSA"),
    ("38", "bloqueos", "Bloqueos", "DEFENSA"),
    ("39", "recuperaciones", "Recuperaciones", "DEFENSA"),
    ("40", "errores_remate", "Errores que conducen a remate", "DEFENSA"),
    ("41", "errores_gol", "Errores que conducen a gol", "DEFENSA"),
    # F. PORTERÍA / ATAJADAS (4)
    ("42", "paradas", "Paradas de portero", "PORTERÍA"),
    ("43", "xgot_enfrentado", "xGOT enfrentado", "PORTERÍA"),
    ("44", "goles_evitados", "Goles evitados", "PORTERÍA"),
    ("45", "saques_puerta", "Saques de puerta", "PORTERÍA"),
)

CLAVES_45: tuple[str, ...] = tuple(cl for _, cl, _, _ in CATALOGO_45_METRICAS)

# Alias heredados aceptados al ingestar diccionarios externos / Flashscore manual
ALIAS_A_CANONICO: dict[str, str] = {
    "posesion": "posesion_pct",
    "remates_a_puerta": "remates_puerta",
    "grandes_ocasiones_perdidas": "grandes_ocasiones_falladas",
    "corneres": "corners",
    "fueras_de_juego": "fueras_juego",
    "pases_totales": "pases_total",
    "pases_pct": "pases_precision_pct",
    "centros_pct": "cruces_pct",
    "xa": "xa_equipo",
    "saques_de_banda": "saques_banda",
    "xgot_enfrentados": "xgot_enfrentado",
    "saques_de_puerta": "saques_puerta",
    "amarillas": "tarjetas_amarillas",
    "rojas": "tarjetas_rojas",
    "entradas_ganadas_pct": "entradas_pct",
}

METRICAS_CONTEO_ADITIVAS_BANCO: frozenset[str] = frozenset({
    "goles_favor", "goles_contra",
    "remates_totales", "remates_puerta", "grandes_ocasiones",
    "grandes_ocasiones_falladas", "corners", "pases_total",
    "tarjetas_amarillas", "tarjetas_rojas",
    "remates_fuera", "remates_rechazados", "remates_dentro_area",
    "remates_fuera_area", "al_palo", "toques_area_rival",
    "pases_entre_lineas", "fueras_juego", "tiros_libres",
    "saques_banda", "faltas", "duelos_ganados",
    "despejes", "intercepciones", "bloqueos", "recuperaciones",
    "errores_remate", "errores_gol", "paradas", "saques_puerta",
})

METRICAS_CONTINUAS_ADITIVAS_BANCO: frozenset[str] = frozenset({
    "xg", "xgot", "xg_juego_abierto", "xg_balon_parado",
    "xg_sin_penalti", "xa_equipo", "xgot_enfrentado", "goles_evitados",
})

METRICAS_CON_SIGNO: frozenset[str] = frozenset({"goles_evitados"})

METRICAS_NO_ADITIVAS_BANCO: frozenset[str] = frozenset({
    "posesion_pct", "pases_precision_pct", "pases_ratio",
    "pases_largos_pct", "pases_tercio_final_pct", "cruces_pct",
    "entradas_pct", "duelos_suelo_pct", "duelos_aereos_pct",
})

# Métricas FotMob provenientes de stats de equipo vs playerStats (FT-only o escasas) vs solo Flashscore
_FOTMOB_TEAM_STATS = frozenset({
    "xg", "posesion_pct", "remates_totales", "remates_puerta", "grandes_ocasiones",
    "grandes_ocasiones_falladas", "corners", "pases_total", "pases_precision_pct",
    "tarjetas_amarillas", "tarjetas_rojas", "xgot", "xg_juego_abierto",
    "xg_balon_parado", "xg_sin_penalti", "remates_fuera", "remates_rechazados",
    "remates_dentro_area", "remates_fuera_area", "al_palo", "toques_area_rival",
    "fueras_juego", "pases_largos_pct", "cruces_pct", "saques_banda", "faltas",
    "entradas_pct", "duelos_ganados", "duelos_suelo_pct", "duelos_aereos_pct",
    "despejes", "intercepciones", "bloqueos", "paradas",
})
_FOTMOB_PLAYER_STATS = frozenset({
    "pases_entre_lineas", "pases_tercio_final_pct", "xa_equipo",
    "recuperaciones", "errores_gol", "xgot_enfrentado", "goles_evitados",
})
_SOLO_FLASHSCORE_EN_BOT = frozenset({"tiros_libres", "errores_remate", "saques_puerta"})
_FLASHSCORE_NO_CUBRE = frozenset({"xg_juego_abierto", "xg_balon_parado", "xg_sin_penalti"})


def construir_catalogo_provenancia_45() -> dict[str, dict[str, Any]]:
    """
    Devuelve la matriz oficial FotMob vs Flashscore de las 45 métricas del bot
    según `fuentes_xg.py` y `fuente_flashscore.py`.
    """
    catalogo: dict[str, dict[str, Any]] = {}
    for num, cl, etq, cat in CATALOGO_45_METRICAS:
        en_fotmob = cl not in _SOLO_FLASHSCORE_EN_BOT
        if cl in _SOLO_FLASHSCORE_EN_BOT:
            mod_fm = "NO_PUBLICA"
            origen_primario = "Flashscore (df_st_1 FT / GraphQL dts)"
        elif cl == "pases_ratio":
            mod_fm = "derivado(accurate_passes)"
            origen_primario = "FotMob (derivado FT/1T/2T) + Flashscore (derivado Pases)"
        elif cl in _FOTMOB_PLAYER_STATS:
            mod_fm = "playerStats (agregación por jugador)"
            origen_primario = "FotMob playerStats + Flashscore GraphQL/df_st_1"
        else:
            mod_fm = "stats (team-level FT/1T/2T)"
            origen_primario = "FotMob stats (FT/1T/2T) + Flashscore (df_st_1 FT / GraphQL)"

        en_flashscore = cl not in _FLASHSCORE_NO_CUBRE
        if cl in _FLASHSCORE_NO_CUBRE:
            origen_fs = "NO_MAPA (sin sub-xG en Flashscore)"
        elif cl == "pases_ratio":
            origen_fs = "derivado de Pases completados/totales"
        else:
            origen_fs = "df_st_1 (FT) / GraphQL dts (FT/1T/2T según cobertura)"

        if cl in METRICAS_CONTEO_ADITIVAS_BANCO:
            tipo_ag = "conteo_entero_aditivo"
        elif cl in METRICAS_CONTINUAS_ADITIVAS_BANCO:
            tipo_ag = "continuo_float_aditivo"
        else:
            tipo_ag = "ratio_o_pct_no_aditivo"

        catalogo[cl] = {
            "numero": num,
            "clave": cl,
            "etiqueta": etq,
            "categoria": cat,
            "en_fotmob": en_fotmob,
            "modulo_fotmob": mod_fm,
            "en_flashscore": en_flashscore,
            "modulo_flashscore": origen_fs,
            "fuentes_bot": origen_primario,
            "tipo_agregacion": tipo_ag,
        }
    return catalogo


CATALOGO_PROVENANCIA_45: dict[str, dict[str, Any]] = construir_catalogo_provenancia_45()

COLUMNAS_METADATOS: tuple[str, ...] = (
    "id_partido",
    "equipo_clave",
    "periodo",
    "equipo_nombre",
    "rival_nombre",
    "fecha",
    "inicio_utc",
    "competicion",
    "pais",
    "condicion",
    "goles_favor",
    "goles_contra",
    "marcador_valido_mitad",
    "fuente_primaria",
    "id_flashscore",
    "flashscore_pareado",
    "coherencia_1t_2t_ft",
    "discrepancias_aditividad",
    "excluidos_estimados_json",
    "procedencia_json",
    "registrado_en_utc",
)

COLUMNAS_CSV_COMPLETAS: tuple[str, ...] = COLUMNAS_METADATOS + CLAVES_45

_MAPA_EXONIMOS_EQUIPO: dict[str, str] = {
    "turquia": "turkey",
    "turkiye": "turkey",
    "belgica": "belgium",
    "italia": "italy",
    "francia": "france",
    "espana": "spain",
    "alemania": "germany",
    "inglaterra": "england",
    "paises bajos": "netherlands",
    "holanda": "netherlands",
    "portugal": "portugal",
    "brasil": "brazil",
    "argentina": "argentina",
    "londrina": "londrina",
    "nautico": "nautico",
}


def canonizar_equipo_clave(nombre: Any) -> str:
    """Normaliza nombre o slug de equipo a clave ASCII estable."""
    txt = str(nombre or "").strip()
    if not txt:
        return "desconocido"
    norm = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"[^a-zA-Z0-9\s\-_]", " ", norm).lower().strip()
    norm = re.sub(r"\s+", " ", norm)
    if norm in _MAPA_EXONIMOS_EQUIPO:
        return _MAPA_EXONIMOS_EQUIPO[norm]
    slug = re.sub(r"[^a-z0-9]+", "_", norm).strip("_")
    return _MAPA_EXONIMOS_EQUIPO.get(slug, slug) or "desconocido"


def _es_faltante(val: Any) -> bool:
    if val is None or isinstance(val, bool):
        return True
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return True
    if isinstance(val, str):
        s = val.strip().lower()
        return s in ("", "none", "null", "nan", "sin_dato", "n/d", "-")
    return False


def _a_numero_o_none(val: Any) -> float | None:
    """Convierte a float preservando `0`/`0.0`; nunca convierte `None`/`sin_dato` en `0`."""
    if _es_faltante(val):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("%", "").replace(",", ".")
    try:
        num = float(s)
        return None if (math.isnan(num) or math.isinf(num)) else num
    except ValueError:
        return None


def _a_entero_o_none(val: Any) -> int | None:
    num = _a_numero_o_none(val)
    if num is None:
        return None
    return int(round(num))


def _formatear_valor_csv(val: Any, como_entero: bool = False) -> str:
    if isinstance(val, bool):
        return "1" if val else "0"
    if _es_faltante(val):
        return "sin_dato"
    if isinstance(val, (int, float)):
        if como_entero or (isinstance(val, float) and val.is_integer()):
            return str(int(round(float(val))))
        return f"{float(val):.6g}"
    return str(val)


def _parsear_celda_csv(col: str, bruto: str | None) -> Any:
    if bruto is None:
        return None
    s = str(bruto).strip()
    if s.lower() in ("", "sin_dato", "none", "null", "nan"):
        return None
    if col in ("marcador_valido_mitad", "flashscore_pareado", "goles_favor", "goles_contra"):
        try:
            return int(float(s))
        except ValueError:
            return None
    if col in CLAVES_45:
        try:
            num = float(s)
            return None if (math.isnan(num) or math.isinf(num)) else num
        except ValueError:
            return None
    return s


def _ahora_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolver_rutas_banco(
    db_path: str | Path | None = None,
    csv_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Resuelve rutas activas de SQLite y CSV respetando overrides por parámetro o entorno."""
    env_db = os.environ.get("AGENTE_FUTBOL_BANCO_DB", "").strip()
    env_csv = os.environ.get("AGENTE_FUTBOL_BANCO_CSV", "").strip()
    db_p = Path(db_path) if db_path else (Path(env_db) if env_db else DEFAULT_DB_PATH)
    csv_p = Path(csv_path) if csv_path else (Path(env_csv) if env_csv else DEFAULT_CSV_PATH)
    return db_p, csv_p


# ═══════════════════════════════════════════════════════════════════════════════
# 6.2 VALIDACIÓN DE COHERENCIA 1T + 2T == FT
# ═══════════════════════════════════════════════════════════════════════════════

def validar_coherencia_1t_2t_ft(
    fila_ft: dict[str, Any] | None,
    fila_1t: dict[str, Any] | None,
    fila_2t: dict[str, Any] | None,
    tolerancia_float: float = TOLERANCIA_ADITIVIDAD_FLOAT,
) -> dict[str, Any]:
    """
    Valida `1T + 2T == FT` para goles (`goles_favor`, `goles_contra`) y las 36
    métricas aditivas de las 45 (conteos exactos con tol 0; continuas `< tolerancia_float`).
    Nunca convierte `None` en `0`: si falta al menos un periodo, solo verifica
    desbordamiento parcial (`mitad > FT`) en métricas no negativas.
    """
    discrepancias: list[dict[str, Any]] = []
    verificadas = 0
    variables_objetivo = ("goles_favor", "goles_contra") + tuple(
        cl for cl in CLAVES_45
        if cl in METRICAS_CONTEO_ADITIVAS_BANCO or cl in METRICAS_CONTINUAS_ADITIVAS_BANCO
    )
    f_ft = fila_ft or {}
    f_1t = fila_1t or {}
    f_2t = fila_2t or {}

    for var in variables_objetivo:
        v_ft = _a_numero_o_none(f_ft.get(var))
        v_1t = _a_numero_o_none(f_1t.get(var))
        v_2t = _a_numero_o_none(f_2t.get(var))

        es_conteo = var in METRICAS_CONTEO_ADITIVAS_BANCO
        tol = 1e-6 if es_conteo else tolerancia_float

        if v_ft is not None and v_1t is not None and v_2t is not None:
            verificadas += 1
            suma = v_1t + v_2t
            diff = abs(v_ft - suma)
            if (diff > tol) if es_conteo else (diff >= tolerancia_float):
                discrepancias.append({
                    "variable": var,
                    "ft": round(v_ft, 4),
                    "1t": round(v_1t, 4),
                    "2t": round(v_2t, 4),
                    "suma_1t_2t": round(suma, 4),
                    "diferencia": round(v_ft - suma, 4),
                    "motivo": "suma_mitades_distinta_ft",
                })
        elif v_ft is not None and var not in METRICAS_CON_SIGNO:
            for tag_m, v_m in (("1T", v_1t), ("2T", v_2t)):
                if v_m is not None and v_m > (v_ft + tol):
                    discrepancias.append({
                        "variable": var,
                        "ft": round(v_ft, 4),
                        "1t": round(v_1t, 4) if v_1t is not None else None,
                        "2t": round(v_2t, 4) if v_2t is not None else None,
                        "suma_1t_2t": None,
                        "diferencia": round(v_m - v_ft, 4),
                        "motivo": f"mitad_{tag_m}_supera_ft",
                    })

    return {
        "es_coherente": len(discrepancias) == 0,
        "estado": "SIN_EVALUAR" if verificadas == 0 and not discrepancias else (
            "OK" if len(discrepancias) == 0 else "CON_DISCREPANCIAS"
        ),
        "verificadas": verificadas,
        "discrepancias": discrepancias,
        "variables_con_error": sorted({d["variable"] for d in discrepancias}),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACTORES DE PARTIDOS HACIA FILAS NORMALIZADAS
# ═══════════════════════════════════════════════════════════════════════════════

def _goles_mitad_desde_partido(
    partido: dict[str, Any],
) -> tuple[dict[str, tuple[int | None, int | None]], bool]:
    """Extrae `(goles_favor, goles_contra)` por FT/1T/2T sin inventar mitades."""
    from auditoria_historica import reconstruir_marcador_mitades_partido

    marc = partido.get("marcador") if isinstance(partido.get("marcador"), dict) else {}
    gf_ft = _a_entero_o_none(marc.get("equipo"))
    if gf_ft is None:
        gf_ft = _a_entero_o_none(partido.get("goles_equipo"))
    gc_ft = _a_entero_o_none(marc.get("rival"))
    if gc_ft is None:
        gc_ft = _a_entero_o_none(partido.get("goles_rival"))

    if gf_ft is None or gc_ft is None:
        txt_m = str(marc.get("texto") or "")
        m = re.search(r"(\d+)\s*[-:]\s*(\d+)", txt_m)
        if m:
            gf_ft = int(m.group(1))
            gc_ft = int(m.group(2))

    gl_1t, gv_1t, gl_2t, gv_2t, valido, _etq = reconstruir_marcador_mitades_partido(
        partido, "equipo", "rival"
    )
    if valido:
        return (
            {
                "FT": (gf_ft if gf_ft is not None else int(gl_1t) + int(gl_2t),
                       gc_ft if gc_ft is not None else int(gv_1t) + int(gv_2t)),
                "1T": (int(gl_1t), int(gv_1t)),
                "2T": (int(gl_2t), int(gv_2t)),
            },
            True,
        )
    return {"FT": (gf_ft, gc_ft), "1T": (None, None), "2T": (None, None)}, False


def _normalizar_clave_metrica(clave: Any) -> str | None:
    cl = str(clave or "").strip()
    if not cl:
        return None
    cl = ALIAS_A_CANONICO.get(cl, cl)
    return cl if cl in CLAVES_45 else None


def _es_celda_sintetica(fila: dict[str, Any], proc_celda: Any) -> bool:
    if bool(fila.get("estimado")):
        return True
    p_str = str(proc_celda or fila.get("procedencia") or fila.get("fuente") or "").strip().lower()
    return p_str in ("estimacion_modelo", "estimado", "sintetico")


def construir_filas_partido(
    partido: dict[str, Any],
    *,
    equipo_clave: str,
    equipo_nombre: str = "",
    pais: str | None = None,
    fuente_primaria_doc: str = "fotmob",
    registrado_en_utc: str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Convierte un `MatrizPartido` (dict) en exactamente 3 filas normalizadas (`FT`, `1T`, `2T`)
    para el equipo objetivo. Cualquier valor `estimacion_modelo`/`estimado=True` se
    descarta a `None` y queda registrado en `excluidos_estimados_json`.
    """
    id_partido = str(partido.get("id_partido") or "").strip()
    if not id_partido:
        raise ValueError("Partidos sin id_partido no pueden ingresar al banco histórico.")

    eq_key = canonizar_equipo_clave(equipo_clave or equipo_nombre or partido.get("equipo"))
    eq_nom = str(equipo_nombre or partido.get("equipo") or eq_key).strip()
    riv_nom = str(partido.get("rival") or "").strip()
    fecha = str(partido.get("fecha") or "").strip()
    comp = str(partido.get("torneo") or partido.get("competicion") or "").strip()
    cond = str(partido.get("condicion") or "desconocida").strip().lower() or "desconocida"
    inicio_utc = str(partido.get("inicio_utc") or "").strip() or None

    fs = partido.get("fuente_secundaria") if isinstance(partido.get("fuente_secundaria"), dict) else {}
    id_fs = str(fs.get("id_flashscore") or "N/D").strip() or "N/D"
    fs_pareado = 1 if (fs.get("pareado") and id_fs.upper() != "N/D") else 0

    goles_map, valido_mitad = _goles_mitad_desde_partido(partido)
    periodos_dict = partido.get("periodos") if isinstance(partido.get("periodos"), dict) else {}
    proc_root = partido.get("procedencia") if isinstance(partido.get("procedencia"), dict) else {}
    ts_utc = registrado_en_utc or _ahora_utc_iso()

    filas_out: dict[str, dict[str, Any]] = {}
    for per in PERIODOS_OFICIALES:
        per_obj = periodos_dict.get(per) if isinstance(periodos_dict.get(per), dict) else {}
        filas_raw = per_obj.get("filas") or []
        proc_per = proc_root.get(per) if isinstance(proc_root.get(per), dict) else {}
        proc_eq = proc_per.get("equipo") if isinstance(proc_per.get("equipo"), dict) else {}

        metricas_vals: dict[str, float | None] = {cl: None for cl in CLAVES_45}
        proc_real: dict[str, str] = {}
        excluidos_est: list[str] = []

        for fila in filas_raw:
            if not isinstance(fila, dict):
                continue
            cl = _normalizar_clave_metrica(fila.get("clave"))
            if not cl:
                continue
            p_raw = proc_eq.get(cl) or proc_eq.get(fila.get("clave")) or fila.get("procedencia") or fila.get("fuente") or ""
            if _es_celda_sintetica(fila, p_raw):
                if cl not in excluidos_est:
                    excluidos_est.append(cl)
                metricas_vals[cl] = None
                continue
            num = _a_numero_o_none(fila.get("equipo"))
            if num is None:
                continue
            if cl in METRICAS_CONTEO_ADITIVAS_BANCO:
                num = float(int(round(num)))
            metricas_vals[cl] = num
            p_tag = str(p_raw or fuente_primaria_doc or "fotmob").strip().lower()
            if p_tag == "sin_dato":
                p_tag = str(fuente_primaria_doc or "fotmob").strip().lower()
            proc_real[cl] = p_tag

        gf, gc = goles_map.get(per, (None, None))
        row: dict[str, Any] = {
            "id_partido": id_partido,
            "equipo_clave": eq_key,
            "periodo": per,
            "equipo_nombre": eq_nom,
            "rival_nombre": riv_nom,
            "fecha": fecha,
            "inicio_utc": inicio_utc,
            "competicion": comp,
            "pais": pais,
            "condicion": cond,
            "goles_favor": gf,
            "goles_contra": gc,
            "marcador_valido_mitad": 1 if valido_mitad else 0,
            "fuente_primaria": str(partido.get("fuente_primaria") or fuente_primaria_doc or "fotmob"),
            "id_flashscore": id_fs,
            "flashscore_pareado": fs_pareado,
            "coherencia_1t_2t_ft": "SIN_EVALUAR",
            "discrepancias_aditividad": None,
            "excluidos_estimados_json": json.dumps(sorted(excluidos_est), ensure_ascii=False) if excluidos_est else None,
            "procedencia_json": json.dumps(proc_real, ensure_ascii=False, sort_keys=True) if proc_real else None,
            "registrado_en_utc": ts_utc,
        }
        row.update(metricas_vals)
        filas_out[per] = row

    val = validar_coherencia_1t_2t_ft(filas_out["FT"], filas_out["1T"], filas_out["2T"])
    disc_json = json.dumps(val["discrepancias"], ensure_ascii=False) if val["discrepancias"] else None
    for per in PERIODOS_OFICIALES:
        filas_out[per]["coherencia_1t_2t_ft"] = val["estado"]
        filas_out[per]["discrepancias_aditividad"] = disc_json
    return filas_out


def construir_filas_desde_stats_flashscore(
    *,
    id_partido: str,
    fecha: str,
    competicion: str,
    local_nombre: str,
    visitante_nombre: str,
    stats_periodos: dict[str, dict[str, Any]],
    pais_local: str | None = None,
    pais_visitante: str | None = None,
    id_flashscore: str = "Flashscore_Manual",
    inicio_utc: str | None = None,
    fuente_primaria: str = "flashscore_real",
    registrado_en_utc: str | None = None,
) -> list[dict[str, Any]]:
    """
    Construye 6 filas (`FT`, `1T`, `2T` × local y visitante) desde estadísticas reales
    de Flashscore (`stats_periodos[per][clave] = [val_local, val_visita]`).
    Todo campo ausente permanece como `None` (`"sin_dato"`).
    """
    if not str(id_partido or "").strip():
        raise ValueError("id_partido obligatorio para ingestar stats de Flashscore.")
    ts_utc = registrado_en_utc or _ahora_utc_iso()
    lados = (
        (0, canonizar_equipo_clave(local_nombre), local_nombre, visitante_nombre, pais_local, "casa"),
        (1, canonizar_equipo_clave(visitante_nombre), visitante_nombre, local_nombre, pais_visitante, "fuera"),
    )

    todas_filas: list[dict[str, Any]] = []
    for idx_lado, eq_key, eq_nom, riv_nom, pais_eq, cond in lados:
        filas_eq: dict[str, dict[str, Any]] = {}
        valido_mitad = True
        for per in PERIODOS_OFICIALES:
            bloque = stats_periodos.get(per) if isinstance(stats_periodos.get(per), dict) else {}
            goles_par = bloque.get("goles")
            gf = _a_entero_o_none(goles_par[idx_lado]) if isinstance(goles_par, (list, tuple)) and len(goles_par) >= 2 else None
            gc = _a_entero_o_none(goles_par[1 - idx_lado]) if isinstance(goles_par, (list, tuple)) and len(goles_par) >= 2 else None
            if per in ("1T", "2T") and (gf is None or gc is None):
                valido_mitad = False

            metricas_vals: dict[str, float | None] = {cl: None for cl in CLAVES_45}
            proc_real: dict[str, str] = {}
            for raw_k, par_v in bloque.items():
                if raw_k == "goles" or not isinstance(par_v, (list, tuple)) or len(par_v) < 2:
                    continue
                cl = _normalizar_clave_metrica(raw_k)
                if not cl:
                    continue
                num = _a_numero_o_none(par_v[idx_lado])
                if num is None:
                    continue
                if cl in METRICAS_CONTEO_ADITIVAS_BANCO:
                    num = float(int(round(num)))
                metricas_vals[cl] = num
                proc_real[cl] = "flashscore"

            row: dict[str, Any] = {
                "id_partido": str(id_partido),
                "equipo_clave": eq_key,
                "periodo": per,
                "equipo_nombre": eq_nom,
                "rival_nombre": riv_nom,
                "fecha": str(fecha or ""),
                "inicio_utc": inicio_utc,
                "competicion": str(competicion or ""),
                "pais": pais_eq,
                "condicion": cond,
                "goles_favor": gf,
                "goles_contra": gc,
                "marcador_valido_mitad": 0,  # se actualiza abajo tras chequear los 3 periodos
                "fuente_primaria": fuente_primaria,
                "id_flashscore": str(id_flashscore),
                "flashscore_pareado": 1,
                "coherencia_1t_2t_ft": "SIN_EVALUAR",
                "discrepancias_aditividad": None,
                "excluidos_estimados_json": None,
                "procedencia_json": json.dumps(proc_real, ensure_ascii=False, sort_keys=True) if proc_real else None,
                "registrado_en_utc": ts_utc,
            }
            row.update(metricas_vals)
            filas_eq[per] = row

        val = validar_coherencia_1t_2t_ft(filas_eq["FT"], filas_eq["1T"], filas_eq["2T"])
        disc_json = json.dumps(val["discrepancias"], ensure_ascii=False) if val["discrepancias"] else None
        for per in PERIODOS_OFICIALES:
            filas_eq[per]["marcador_valido_mitad"] = 1 if valido_mitad else 0
            filas_eq[per]["coherencia_1t_2t_ft"] = val["estado"]
            filas_eq[per]["discrepancias_aditividad"] = disc_json
            todas_filas.append(filas_eq[per])
    return todas_filas


def construir_filas_desde_documento(
    doc: dict[str, Any],
    *,
    equipo_clave: str | None = None,
    pais: str | None = None,
    registrado_en_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Extrae todas las filas `(id_partido, equipo_clave, periodo)` de un documento de equipo."""
    if not isinstance(doc, dict):
        return []
    raw_eq = doc.get("equipo")
    if isinstance(raw_eq, dict):
        eq_nom = str(
            raw_eq.get("nombre")
            or raw_eq.get("name")
            or raw_eq.get("nombre_solicitado")
            or doc.get("nombre")
            or equipo_clave
            or ""
        ).strip()
        if not pais and not doc.get("pais"):
            pais = raw_eq.get("pais_iso") or raw_eq.get("country_code") or pais
    else:
        eq_nom = str(raw_eq or doc.get("nombre") or equipo_clave or "").strip()
    eq_key = canonizar_equipo_clave(equipo_clave or eq_nom)
    pais_doc = pais or doc.get("pais") or doc.get("country_code")
    fuente_doc = str(doc.get("fuente") or "fotmob").strip().lower()
    ts_utc = registrado_en_utc or _ahora_utc_iso()

    visto_ids: set[str] = set()
    filas: list[dict[str, Any]] = []
    for partido in doc.get("partidos") or []:
        if not isinstance(partido, dict):
            continue
        pid = str(partido.get("id_partido") or "").strip()
        if not pid or pid in visto_ids:
            continue
        visto_ids.add(pid)
        por_per = construir_filas_partido(
            partido,
            equipo_clave=eq_key,
            equipo_nombre=eq_nom,
            pais=str(pais_doc) if pais_doc else None,
            fuente_primaria_doc=fuente_doc,
            registrado_en_utc=ts_utc,
        )
        for per in PERIODOS_OFICIALES:
            filas.append(por_per[per])
    return filas


def construir_filas_desde_resultado_evaluacion(
    resultado: Any,
    *,
    registrado_en_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Convierte un `ResultadoPartido` de `/evaluar` en 6 filas (`FT`, `1T`, `2T` × ambos equipos)."""
    if resultado is None:
        return []
    pid = str(getattr(resultado, "id_partido", "") or "").strip()
    if not pid:
        return []
    local = str(getattr(resultado, "local", "") or "").strip()
    visitante = str(getattr(resultado, "visitante", "") or "").strip()
    fecha = str(getattr(resultado, "fecha", "") or "").strip()
    comp = str(getattr(resultado, "competicion", "") or "").strip()
    inicio_utc = str(getattr(resultado, "inicio_utc", "") or "").strip() or None
    stats_per = getattr(resultado, "stats_periodos", None)
    if isinstance(stats_per, dict) and stats_per:
        sp = {
            per: dict(bloque) if isinstance(bloque, dict) else {}
            for per, bloque in stats_per.items()
            if per in PERIODOS_OFICIALES
        }
        for per_k, g_pair in (
            ("FT", (getattr(resultado, "goles_local_ft", None), getattr(resultado, "goles_visitante_ft", None))),
            ("1T", (getattr(resultado, "goles_local_1t", None), getattr(resultado, "goles_visitante_1t", None))),
            ("2T", (getattr(resultado, "goles_local_2t", None), getattr(resultado, "goles_visitante_2t", None))),
        ):
            sp.setdefault(per_k, {})
            if "goles" not in sp[per_k] and g_pair[0] is not None and g_pair[1] is not None:
                sp[per_k]["goles"] = [g_pair[0], g_pair[1]]
        return construir_filas_desde_stats_flashscore(
            id_partido=pid,
            fecha=fecha,
            competicion=comp,
            local_nombre=local,
            visitante_nombre=visitante,
            stats_periodos=sp,
            id_flashscore=str(getattr(resultado, "fuente", "evaluar")),
            inicio_utc=inicio_utc,
            fuente_primaria=str(getattr(resultado, "fuente", "evaluar")),
            registrado_en_utc=registrado_en_utc,
        )

    sp_solo_goles = {
        "FT": {"goles": [getattr(resultado, "goles_local_ft", None), getattr(resultado, "goles_visitante_ft", None)]},
        "1T": {"goles": [getattr(resultado, "goles_local_1t", None), getattr(resultado, "goles_visitante_1t", None)]},
        "2T": {"goles": [getattr(resultado, "goles_local_2t", None), getattr(resultado, "goles_visitante_2t", None)]},
    }
    return construir_filas_desde_stats_flashscore(
        id_partido=pid,
        fecha=fecha,
        competicion=comp,
        local_nombre=local,
        visitante_nombre=visitante,
        stats_periodos=sp_solo_goles,
        id_flashscore="N/D",
        inicio_utc=inicio_utc,
        fuente_primaria=str(getattr(resultado, "fuente", "evaluar")),
        registrado_en_utc=registrado_en_utc,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CAPA DE PERSISTENCIA SQLITE + CSV VERSIONADO
# ═══════════════════════════════════════════════════════════════════════════════

def inicializar_sqlite(conn: sqlite3.Connection) -> None:
    """Crea tablas `partidos_periodos` y `catalogo_fuentes_45` si no existen."""
    cols_metricas_sql = ",\n    ".join(
        f'"{cl}" {"INTEGER" if cl in METRICAS_CONTEO_ADITIVAS_BANCO else "REAL"}'
        for cl in CLAVES_45
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS partidos_periodos (
            id_partido TEXT NOT NULL,
            equipo_clave TEXT NOT NULL,
            periodo TEXT NOT NULL CHECK (periodo IN ('FT', '1T', '2T')),
            equipo_nombre TEXT NOT NULL,
            rival_nombre TEXT NOT NULL,
            fecha TEXT NOT NULL,
            inicio_utc TEXT,
            competicion TEXT,
            pais TEXT,
            condicion TEXT,
            goles_favor INTEGER,
            goles_contra INTEGER,
            marcador_valido_mitad INTEGER NOT NULL DEFAULT 0,
            fuente_primaria TEXT,
            id_flashscore TEXT,
            flashscore_pareado INTEGER NOT NULL DEFAULT 0,
            coherencia_1t_2t_ft TEXT,
            discrepancias_aditividad TEXT,
            excluidos_estimados_json TEXT,
            procedencia_json TEXT,
            registrado_en_utc TEXT NOT NULL,
            {cols_metricas_sql},
            PRIMARY KEY (id_partido, equipo_clave, periodo)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_banco_equipo_fecha ON partidos_periodos (equipo_clave, fecha DESC, periodo)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalogo_fuentes_45 (
            clave TEXT PRIMARY KEY,
            numero TEXT NOT NULL,
            etiqueta TEXT NOT NULL,
            categoria TEXT NOT NULL,
            en_fotmob INTEGER NOT NULL,
            modulo_fotmob TEXT NOT NULL,
            en_flashscore INTEGER NOT NULL,
            modulo_flashscore TEXT NOT NULL,
            fuentes_bot TEXT NOT NULL,
            tipo_agregacion TEXT NOT NULL
        )
        """
    )
    for cl, info in CATALOGO_PROVENANCIA_45.items():
        conn.execute(
            """
            INSERT INTO catalogo_fuentes_45 (
                clave, numero, etiqueta, categoria, en_fotmob, modulo_fotmob,
                en_flashscore, modulo_flashscore, fuentes_bot, tipo_agregacion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(clave) DO UPDATE SET
                numero=excluded.numero,
                etiqueta=excluded.etiqueta,
                categoria=excluded.categoria,
                en_fotmob=excluded.en_fotmob,
                modulo_fotmob=excluded.modulo_fotmob,
                en_flashscore=excluded.en_flashscore,
                modulo_flashscore=excluded.modulo_flashscore,
                fuentes_bot=excluded.fuentes_bot,
                tipo_agregacion=excluded.tipo_agregacion
            """,
            (
                cl,
                info["numero"],
                info["etiqueta"],
                info["categoria"],
                1 if info["en_fotmob"] else 0,
                info["modulo_fotmob"],
                1 if info["en_flashscore"] else 0,
                info["modulo_flashscore"],
                info["fuentes_bot"],
                info["tipo_agregacion"],
            ),
        )


def _combinar_fila_si_mejor(existente: dict[str, Any], nueva: dict[str, Any]) -> dict[str, Any]:
    """Combina fila existente con nueva preservando valores observados si una trae `None`."""
    out = dict(existente)
    for col in COLUMNAS_METADATOS:
        val_n = nueva.get(col)
        if val_n not in (None, "", "N/D") or out.get(col) in (None, "", "N/D"):
            out[col] = val_n
    # Conservar 0 legítimo de goles o métricas; rellenar None con dato nuevo
    for col in ("goles_favor", "goles_contra") + CLAVES_45:
        if nueva.get(col) is not None:
            out[col] = nueva[col]
    return out


def escribir_banco_csv(filas: Iterable[dict[str, Any]], csv_path: Path) -> Path:
    """Escribe el banco en CSV versionado serializando `None` como `'sin_dato'` (nunca `0`)."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    ordenadas = sorted(
        filas,
        key=lambda r: (
            str(r.get("fecha") or ""),
            str(r.get("id_partido") or ""),
            str(r.get("equipo_clave") or ""),
            _ORDEN_PERIODO.get(str(r.get("periodo") or "FT"), 9),
        ),
        reverse=False,
    )
    # Orden cronológico inverso en fecha/partido pero FT -> 1T -> 2T dentro del partido
    ordenadas.sort(key=lambda r: _ORDEN_PERIODO.get(str(r.get("periodo") or "FT"), 9))
    ordenadas.sort(key=lambda r: (str(r.get("fecha") or ""), str(r.get("id_partido") or ""), str(r.get("equipo_clave") or "")), reverse=True)

    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNAS_CSV_COMPLETAS), extrasaction="ignore")
        writer.writeheader()
        for r in ordenadas:
            out_row: dict[str, str] = {}
            for col in COLUMNAS_CSV_COMPLETAS:
                val = r.get(col)
                if col in ("goles_favor", "goles_contra", "marcador_valido_mitad", "flashscore_pareado"):
                    out_row[col] = _formatear_valor_csv(val, como_entero=True)
                elif col in CLAVES_45:
                    out_row[col] = _formatear_valor_csv(val, como_entero=(col in METRICAS_CONTEO_ADITIVAS_BANCO))
                else:
                    out_row[col] = "sin_dato" if val is None else str(val)
            writer.writerow(out_row)
    return csv_path


def cargar_banco_csv(csv_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Lee el CSV del banco devolviendo `None` donde el CSV dice `'sin_dato'`."""
    _, def_csv = resolver_rutas_banco(csv_path=csv_path)
    if not def_csv.exists():
        return []
    filas: list[dict[str, Any]] = []
    with open(def_csv, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row = {col: _parsear_celda_csv(col, raw.get(col)) for col in COLUMNAS_CSV_COMPLETAS}
            filas.append(row)
    return filas


def cargar_banco_sqlite(
    db_path: str | Path | None = None,
    *,
    equipo_clave: str | None = None,
    fecha_max: str | None = None,
) -> list[dict[str, Any]]:
    """Consulta `partidos_periodos` desde SQLite preservando `NULL` como `None`."""
    def_db, _ = resolver_rutas_banco(db_path=db_path)
    if not def_db.exists():
        return []
    conn = sqlite3.connect(str(def_db))
    conn.row_factory = sqlite3.Row
    try:
        filtros: list[str] = []
        params: list[Any] = []
        if equipo_clave:
            filtros.append("equipo_clave = ?")
            params.append(canonizar_equipo_clave(equipo_clave))
        if fecha_max:
            filtros.append("fecha <= ?")
            params.append(str(fecha_max))
        where_sql = ("WHERE " + " AND ".join(filtros)) if filtros else ""
        sql = (
            f"SELECT * FROM partidos_periodos {where_sql} "
            "ORDER BY fecha DESC, id_partido DESC, equipo_clave ASC, "
            "CASE periodo WHEN 'FT' THEN 0 WHEN '1T' THEN 1 ELSE 2 END ASC"
        )
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def guardar_filas_banco(
    filas: Iterable[dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    csv_path: str | Path | None = None,
    sincronizar_csv: bool = True,
) -> dict[str, Any]:
    """
    Inserta/actualiza filas en SQLite con deduplicación exacta por `(id_partido, equipo_clave, periodo)`
    y sincroniza `data/banco_partidos.csv`.
    """
    def_db, def_csv = resolver_rutas_banco(db_path=db_path, csv_path=csv_path)
    def_db.parent.mkdir(parents=True, exist_ok=True)
    lista_entrada = [dict(r) for r in filas if r and r.get("id_partido") and r.get("equipo_clave") and r.get("periodo") in PERIODOS_OFICIALES]

    # Deduplicar en memoria el lote entrante
    por_pk: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in lista_entrada:
        pk = (str(r["id_partido"]), canonizar_equipo_clave(r["equipo_clave"]), str(r["periodo"]))
        r["equipo_clave"] = pk[1]
        por_pk[pk] = _combinar_fila_si_mejor(por_pk[pk], r) if pk in por_pk else r

    conn = sqlite3.connect(str(def_db))
    conn.row_factory = sqlite3.Row
    insertadas = 0
    actualizadas = 0
    try:
        inicializar_sqlite(conn)
        cols_str = ", ".join(f'"{c}"' for c in COLUMNAS_CSV_COMPLETAS)
        placeholders = ", ".join("?" for _ in COLUMNAS_CSV_COMPLETAS)
        update_cols = ", ".join(
            f'"{c}" = excluded."{c}"'
            for c in COLUMNAS_CSV_COMPLETAS
            if c not in ("id_partido", "equipo_clave", "periodo")
        )
        upsert_sql = (
            f"INSERT INTO partidos_periodos ({cols_str}) VALUES ({placeholders}) "
            f"ON CONFLICT(id_partido, equipo_clave, periodo) DO UPDATE SET {update_cols}"
        )

        for pk, r in por_pk.items():
            prev = conn.execute(
                "SELECT * FROM partidos_periodos WHERE id_partido=? AND equipo_clave=? AND periodo=?",
                pk,
            ).fetchone()
            if prev is not None:
                merged = _combinar_fila_si_mejor(dict(prev), r)
                actualizadas += 1
            else:
                merged = r
                insertadas += 1

            vals = []
            for col in COLUMNAS_CSV_COMPLETAS:
                v = merged.get(col)
                if col in ("goles_favor", "goles_contra"):
                    vals.append(_a_entero_o_none(v))
                elif col in METRICAS_CONTEO_ADITIVAS_BANCO and col in CLAVES_45:
                    vals.append(_a_entero_o_none(v))
                elif col in CLAVES_45:
                    vals.append(_a_numero_o_none(v))
                else:
                    vals.append(v)
            conn.execute(upsert_sql, vals)

        conn.commit()
        total_filas = int(conn.execute("SELECT COUNT(*) FROM partidos_periodos").fetchone()[0])
        if sincronizar_csv:
            todas = [
                dict(row)
                for row in conn.execute("SELECT * FROM partidos_periodos").fetchall()
            ]
            escribir_banco_csv(todas, def_csv)
    finally:
        conn.close()

    return {
        "insertadas": insertadas,
        "actualizadas": actualizadas,
        "total_filas_db": total_filas,
        "db_path": str(def_db),
        "csv_path": str(def_csv) if sincronizar_csv else None,
    }


def guardar_documento_equipo_banco(
    doc: dict[str, Any],
    *,
    equipo_clave: str | None = None,
    db_path: str | Path | None = None,
    csv_path: str | Path | None = None,
) -> dict[str, Any]:
    """Atajo para ingestar un documento de equipo (`doc_local` / `doc_visitante`)."""
    filas = construir_filas_desde_documento(doc, equipo_clave=equipo_clave)
    return guardar_filas_banco(filas, db_path=db_path, csv_path=csv_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 6.3 MAPA DE COBERTURA VARIABLE × PERIODO + MOTIVO DE FALTANTES
# ═══════════════════════════════════════════════════════════════════════════════

def _explicar_motivo_faltante(
    clave: str,
    n_tot: int,
    n_ft: int,
    n_1t: int,
    n_2t: int,
    est_ft: int,
    est_1t: int,
    est_2t: int,
    n_flashscore_pareado: int,
) -> str:
    if n_ft == n_tot and n_1t == n_tot and n_2t == n_tot:
        return "Completa 15/15 en FT, 1T y 2T"

    partes: list[str] = []
    if clave in ("goles_favor", "goles_contra"):
        if n_1t < n_tot or n_2t < n_tot:
            partes.append(f"Sin eventos minuto a minuto en {n_tot - min(n_1t, n_2t)}/{n_tot} partidos")
        return "; ".join(partes) or "Completa"

    info = CATALOGO_PROVENANCIA_45.get(clave, {})
    total_est = est_ft + est_1t + est_2t

    if clave == "tiros_libres":
        partes.append("FotMob no publica tiros libres directos")
        if est_ft:
            partes.append(f"{est_ft}/{n_tot} excluidos como estimacion_modelo")
        if n_flashscore_pareado == 0:
            partes.append("sin pareo Flashscore (id=N/D)")
    elif clave == "saques_puerta":
        partes.append("FotMob no publica saques de puerta")
        if total_est:
            partes.append(f"derivación sintética estimacion_modelo excluida (FT={est_ft},1T={est_1t},2T={est_2t})")
        if n_flashscore_pareado == 0:
            partes.append("sin pareo Flashscore (id=N/D)")
    elif clave == "errores_remate":
        partes.append("FotMob no publica errores conducentes a remate")
        if n_flashscore_pareado == 0:
            partes.append("sin pareo Flashscore (id=N/D)")
    elif clave in ("pases_tercio_final_pct", "xa_equipo", "recuperaciones"):
        partes.append("En FotMob proviene de playerStats que solo se agrega en FT (sin desglose 1T/2T Opta)")
    elif clave == "pases_entre_lineas":
        if n_ft == 0:
            partes.append(f"FotMob playerStats omitió line_breaking_passes en {n_tot}/{n_tot}")
        else:
            partes.append(f"Solo {n_ft}/{n_tot} con line_breaking_passes en FotMob FT")
        if est_ft:
            partes.append(f"{est_ft} estimacion_modelo excluidos en FT")
        if n_1t == 0 and n_2t == 0:
            partes.append("1T/2T no publicados en playerStats")
    elif clave == "errores_gol":
        partes.append(
            f"Opta/FotMob playerStats solo publica errors_led_to_goal en {n_ft}/{n_tot} partidos (resto omitido); "
            "en mitades solo derivable cuando FT=0 (si FT>=1 queda sin_dato en 1T/2T)"
        )
    elif clave in _FOTMOB_PLAYER_STATS and (n_1t < n_ft or n_2t < n_ft):
        partes.append("playerStats Opta sin desglose 1T/2T en partidos faltantes")
    else:
        if info.get("modulo_fotmob"):
            partes.append(f"Dato ausente en {info['modulo_fotmob']}")
        if total_est:
            partes.append(f"estimacion_modelo excluida ({est_ft}/{est_1t}/{est_2t})")
        if n_flashscore_pareado == 0:
            partes.append("sin respaldo Flashscore")

    return "; ".join(partes) if partes else "Cobertura parcial en fuente primaria"


def generar_mapa_cobertura_equipo(
    equipo_clave: str,
    *,
    db_path: str | Path | None = None,
    filas: list[dict[str, Any]] | None = None,
    fecha_max: str | None = None,
    max_partidos: int | None = None,
) -> dict[str, Any]:
    """
    Calcula la matriz de cobertura `variable × periodo` (47 variables = 2 goles + 45 métricas
    × FT/1T/2T) contando únicamente datos reales observados/derivados aritméticos (`None` nunca es `0`).
    """
    eq_key = canonizar_equipo_clave(equipo_clave)
    if filas is None:
        origen_filas = cargar_banco_sqlite(db_path=db_path, equipo_clave=eq_key, fecha_max=fecha_max)
    else:
        origen_filas = [
            r for r in filas
            if canonizar_equipo_clave(r.get("equipo_clave")) == eq_key
            and (not fecha_max or str(r.get("fecha") or "") <= str(fecha_max))
        ]

    por_partido: dict[str, dict[str, dict[str, Any]]] = {}
    for r in origen_filas:
        pid = str(r.get("id_partido") or "")
        per = str(r.get("periodo") or "")
        if not pid or per not in PERIODOS_OFICIALES:
            continue
        por_partido.setdefault(pid, {})[per] = r

    # Orden cronológico descendente; si max_partidos (ej. 15) conservar los últimos N
    partidos_ordenados = sorted(
        por_partido.items(),
        key=lambda kv: (
            str((kv[1].get("FT") or next(iter(kv[1].values()))).get("fecha") or ""),
            kv[0],
        ),
        reverse=True,
    )
    if max_partidos and max_partidos > 0:
        partidos_ordenados = partidos_ordenados[:max_partidos]

    n_partidos = len(partidos_ordenados)
    n_flashscore_pareado = sum(
        1 for _, pmap in partidos_ordenados
        if int((pmap.get("FT") or next(iter(pmap.values()))).get("flashscore_pareado") or 0) == 1
    )

    excl_por_partido_per: dict[tuple[str, str], set[str]] = {}
    for pid, pmap in partidos_ordenados:
        for per in PERIODOS_OFICIALES:
            r = pmap.get(per) or {}
            raw_excl = r.get("excluidos_estimados_json")
            excl_set: set[str] = set()
            if raw_excl:
                try:
                    excl_set = set(json.loads(raw_excl))
                except Exception:
                    excl_set = set()
            excl_por_partido_per[(pid, per)] = excl_set

    variables_lista: list[tuple[str, str, str, str]] = [
        ("00a", "goles_favor", "Goles a favor", "MARCADOR"),
        ("00b", "goles_contra", "Goles en contra", "MARCADOR"),
        *CATALOGO_45_METRICAS,
    ]

    filas_mapa: list[dict[str, Any]] = []
    for num, cl, etq, cat in variables_lista:
        conteos: dict[str, int] = {"FT": 0, "1T": 0, "2T": 0}
        est_excl: dict[str, int] = {"FT": 0, "1T": 0, "2T": 0}
        for pid, pmap in partidos_ordenados:
            for per in PERIODOS_OFICIALES:
                r = pmap.get(per) or {}
                if cl in excl_por_partido_per[(pid, per)]:
                    est_excl[per] += 1
                if not _es_faltante(r.get(cl)):
                    conteos[per] += 1

        motivo = _explicar_motivo_faltante(
            cl,
            n_partidos,
            conteos["FT"],
            conteos["1T"],
            conteos["2T"],
            est_excl["FT"],
            est_excl["1T"],
            est_excl["2T"],
            n_flashscore_pareado,
        )
        filas_mapa.append({
            "numero": num,
            "clave": cl,
            "etiqueta": etq,
            "categoria": cat,
            "n_partidos": n_partidos,
            "FT": conteos["FT"],
            "1T": conteos["1T"],
            "2T": conteos["2T"],
            "excluidos_estimados": dict(est_excl),
            "motivo_faltantes": motivo,
        })

    return {
        "equipo_clave": eq_key,
        "n_partidos": n_partidos,
        "n_flashscore_pareado": n_flashscore_pareado,
        "fecha_min": min(
            (str((pm.get("FT") or next(iter(pm.values()))).get("fecha") or "") for _, pm in partidos_ordenados),
            default="",
        ),
        "fecha_max": max(
            (str((pm.get("FT") or next(iter(pm.values()))).get("fecha") or "") for _, pm in partidos_ordenados),
            default="",
        ),
        "variables": filas_mapa,
    }


def formatear_mapa_cobertura_markdown(mapa: dict[str, Any]) -> str:
    """Formatea la tabla completa `variable × periodo` en Markdown limpio (sin LaTeX)."""
    n_p = mapa.get("n_partidos", 0)
    lineas = [
        f"### Mapa de cobertura: {mapa.get('equipo_clave')} (n={n_p}, "
        f"rango {mapa.get('fecha_min')} a {mapa.get('fecha_max')}, "
        f"Flashscore pareados={mapa.get('n_flashscore_pareado')}/{n_p})",
        "",
        "| # | Variable (`clave`) | Categoría | FT | 1T | 2T | Motivo de faltantes |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for v in mapa.get("variables") or []:
        n_tot = v["n_partidos"]
        lineas.append(
            f"| {v['numero']} | {v['etiqueta']} (`{v['clave']}`) | {v['categoria']} "
            f"| {v['FT']}/{n_tot} | {v['1T']}/{n_tot} | {v['2T']}/{n_tot} | {v['motivo_faltantes']} |"
        )
    return "\n".join(lineas)


# ═══════════════════════════════════════════════════════════════════════════════
# 6.4 HOOKS NO BLOQUEANTES (/mercados, /auditar, /evaluar)
# ═══════════════════════════════════════════════════════════════════════════════

def guardar_en_banco_no_bloqueante(
    *,
    doc_local: dict[str, Any] | None = None,
    doc_visitante: dict[str, Any] | None = None,
    resultados_evaluacion: Iterable[Any] | None = None,
    filas_extra: Iterable[dict[str, Any]] | None = None,
    db_path: str | Path | None = None,
    csv_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """
    Persiste partidos analizados (`/mercados`, `/auditar`) o liquidados (`/evaluar`)
    sin propagar excepciones al flujo principal del bot.

    En ejecución bajo pytest sin rutas explícitas ni overrides de entorno
    (`AGENTE_FUTBOL_BANCO_DB` / `AGENTE_FUTBOL_BANCO_CSV`), se omite el guardado
    para no contaminar `data/banco_partidos.*` con datos de pruebas.
    """
    try:
        if (
            db_path is None
            and csv_path is None
            and not os.environ.get("AGENTE_FUTBOL_BANCO_DB", "").strip()
            and not os.environ.get("AGENTE_FUTBOL_BANCO_CSV", "").strip()
            and os.environ.get("PYTEST_CURRENT_TEST")
        ):
            return None
        lote: list[dict[str, Any]] = []
        if isinstance(doc_local, dict):
            lote.extend(construir_filas_desde_documento(doc_local))
        if isinstance(doc_visitante, dict):
            lote.extend(construir_filas_desde_documento(doc_visitante))
        if resultados_evaluacion is not None:
            for res in resultados_evaluacion:
                lote.extend(construir_filas_desde_resultado_evaluacion(res))
        if filas_extra is not None:
            lote.extend(list(filas_extra))
        if not lote:
            return None
        return guardar_filas_banco(lote, db_path=db_path, csv_path=csv_path)
    except Exception as exc:
        print(f"[BANCO HISTORICO] ⚠️ Guardado no bloqueante omitido por error: {exc}")
        return None
