"""
agente_futbol/evaluacion_pronosticos.py
───────────────────────────────────────────────────────────────────────────────
EVALUACIÓN POSTERIOR, REGISTRO HISTÓRICO Y CALIBRACIÓN DE PROBABILIDADES.

Ciclo completo:
    Pronóstico previo guardado → resultado final verificado → evaluación por
    selección → acierto y calibración por bandas/mercados → diagnóstico →
    comparación de mejoras → resumen en Telegram.

Principios (todos verificados en código, no declarativos):
  1. NO se reescribe el pasado: la probabilidad evaluada es la ORIGINAL del
     pronóstico, nunca la que el modelo produciría hoy.
  2. `evento_ocurrio` (0/1/no evaluable) y `estado_liquidacion` son campos
     SEPARADOS. Una devolución no es una victoria ni una derrota.
  3. Un pronóstico solo es evaluable si se emitió ANTES del inicio del partido.
  4. Los grupos de bandas son excluyentes; el acumulado (≥85%, etc.) se informa
     aparte y se avisa de que se superpone.
  5. Un fallo de consulta NO es un pronóstico fallado: queda «no verificable».
  6. La incertidumbre respeta la agrupación por partido: no se tratan cientos de
     líneas del mismo encuentro como ensayos independientes.
  7. Si N=0 se muestra «sin datos», nunca 0% ni 100%.

Formatos de entrada tolerados
-----------------------------
  • Registro nuevo (inmutable, lo escribe este módulo):
        informes/pronosticos/<id_trabajo>.json
  • Análisis históricos ya guardados por el motor de mercados:
        informes/mercados/<fecha>/mercados_<A>_vs_<B>_<id>.json
    Estos últimos NO tienen fecha de partido ni probabilidad sin redondear, así
    que se importan con esas limitaciones explícitamente marcadas.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import math
import random
import statistics
import sys
import unicodedata

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import config
from motor_mercados import NOMBRE_MODELO, UMBRAL_DEFECTO, VERSION_MODELO

PRONOSTICOS_DIR = os.path.join(config.INFORME_DIR, "pronosticos")
EVALUACION_DIR = os.path.join(config.INFORME_DIR, "evaluacion")
MERCADOS_DIR = os.path.join(config.INFORME_DIR, "mercados")
LEDGER_OOS_PATH = os.path.join(EVALUACION_DIR, "ledger_oos.jsonl")

# ─── Bandas excluyentes exigidas por la especificación ────────────────────────
BANDAS: tuple[tuple[str, float, float], ...] = (
    ("0.70-0.75", 0.70, 0.75),
    ("0.75-0.80", 0.75, 0.80),
    ("0.80-0.85", 0.80, 0.85),
    ("0.85-0.90", 0.85, 0.90),
    ("0.90-0.95", 0.90, 0.95),
    ("0.95-1.00", 0.95, 1.00),
    ("1.00 exacto", 1.00, 1.0000001),
)
BANDA_REDONDEADA = "solo redondeada/intervalo"

# Acumulados que se SUPERPONEN (no se suman entre sí)
ACUMULADOS: tuple[tuple[str, float], ...] = (
    ("p>70%", 0.70),
    ("p>=85%", 0.85),
    ("p>=90%", 0.90),
    ("p>=95%", 0.95),
)

ESTADOS_LIQUIDACION = ("ganada", "perdida", "devolucion", "anulada", "pendiente",
                       "no_verificable", "regla_no_definida", "abstenido_por_cobertura")


# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

def _slug(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "x").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").lower() or "x"


def _ahora() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


_EXONIMOS_CANONICOS = {
    "turquia": "turkey", "turkiye": "turkey", "turkey": "turkey",
    "belgica": "belgium", "belgie": "belgium", "belgique": "belgium", "belgium": "belgium",
    "italia": "italy", "italy": "italy",
    "francia": "france", "france": "france",
    "espana": "spain", "spain": "spain",
    "alemania": "germany", "deutschland": "germany", "germany": "germany",
    "inglaterra": "england", "england": "england",
    "paisesbajos": "netherlands", "holanda": "netherlands", "netherlands": "netherlands",
    "suiza": "switzerland", "switzerland": "switzerland",
    "croacia": "croatia", "hrvatska": "croatia", "croatia": "croatia",
    "polonia": "poland", "polska": "poland", "poland": "poland",
    "grecia": "greece", "greece": "greece",
    "hungria": "hungary", "magyarorszag": "hungary", "hungary": "hungary",
    "suecia": "sweden", "sverige": "sweden", "sweden": "sweden",
    "noruega": "norway", "norge": "norway", "norway": "norway",
    "dinamarca": "denmark", "danmark": "denmark", "denmark": "denmark",
    "escocia": "scotland", "scotland": "scotland",
    "gales": "wales", "wales": "wales",
    "irlanda": "ireland", "ireland": "ireland",
    "republicacheca": "czechia", "chequia": "czechia", "czechrepublic": "czechia", "czechia": "czechia",
}


def _clave_equipo(nombre: str) -> str:
    """Clave tolerante a tildes, exónimos bilingües y palabras vacías para emparejar equipos."""
    base = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode()
    palabras = [p for p in re.split(r"[^A-Za-z0-9]+", base.lower()) if p]
    vacias = {"de", "del", "la", "el", "los", "las", "y", "e", "club", "fc", "cf"}
    sig = [p for p in palabras if p not in vacias]
    clave = "".join(sig) or "".join(palabras)
    return _EXONIMOS_CANONICOS.get(clave, clave)


def banda_de(p: float | None) -> str:
    """
    Banda EXCLUYENTE de una probabilidad. Cada p cae en exactamente una banda.

    Fronteras explícitas (evitan ambigüedad en los bordes):
        0.70 < p < 0.75   (0,70 exacto NO cumple el filtro estricto > 0,70)
        0.75 <= p < 0.80
        0.80 <= p < 0.85
        0.85 <= p < 0.90
        0.90 <= p < 0.95
        0.95 <= p < 1.00
        p == 1.00 exacto
    Un p sin valor exacto va a la banda de solo redondeada.
    """
    if p is None:
        return BANDA_REDONDEADA
    if p == 1.00:
        return "1.00 exacto"
    if 0.70 < p < 0.75:
        return "0.70-0.75"
    if 0.75 <= p < 0.80:
        return "0.75-0.80"
    if 0.80 <= p < 0.85:
        return "0.80-0.85"
    if 0.85 <= p < 0.90:
        return "0.85-0.90"
    if 0.90 <= p < 0.95:
        return "0.90-0.95"
    if 0.95 <= p < 1.00:
        return "0.95-1.00"
    if p <= 0.70:
        return "por debajo del filtro"
    return BANDA_REDONDEADA


# ═══════════════════════════════════════════════════════════════════════════════
# 1. REGISTRO INMUTABLE DE PRONÓSTICOS (snapshot previo al envío)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SnapshotPronostico:
    """Snapshot inmutable de lo que se pronosticó, guardado ANTES de enviar."""
    id_trabajo: str
    equipos: dict[str, Any]
    partido: dict[str, Any]              # fecha, competicion, condicion, sede
    generado_en: str
    publicado_en: str | None
    modelo: dict[str, Any]               # nombre, version, parametros
    datos_corte: dict[str, Any]           # version/fecha de corte de datos y muestras
    selecciones: list[dict[str, Any]]    # TODAS (incluidas las < umbral)
    selecciones_publicadas: list[dict[str, Any]]
    umbral_publicado: float
    referencia: str                      # ruta del informe/mensaje original
    origen: str = "nuevo"                # nuevo | importado_historico
    abstenido: bool = False
    estado_cobertura: str | None = None
    motivo_abstencion: str | None = None
    decision_cobertura: dict[str, Any] | None = None
    modo_sombra: dict[str, Any] | None = None

    def a_dict(self) -> dict[str, Any]:
        out = {
            "esquema": "pronostico-v1",
            "id_trabajo": self.id_trabajo,
            "equipos": self.equipos,
            "partido": self.partido,
            "generado_en": self.generado_en,
            "publicado_en": self.publicado_en,
            "modelo": self.modelo,
            "datos_corte": self.datos_corte,
            "umbral_publicado": self.umbral_publicado,
            "selecciones": self.selecciones,
            "selecciones_publicadas": self.selecciones_publicadas,
            "referencia": self.referencia,
            "origen": self.origen,
            "abstenido": self.abstenido,
            "estado_cobertura": self.estado_cobertura,
            "motivo_abstencion": self.motivo_abstencion,
            "decision_cobertura": self.decision_cobertura,
            "inmutable": True,
        }
        if self.modo_sombra is not None:
            out["modo_sombra"] = self.modo_sombra
        return out


def ruta_snapshot(id_trabajo: str, directorio: str | None = None) -> str:
    return os.path.join(directorio or PRONOSTICOS_DIR, f"{id_trabajo}.json")


def guardar_snapshot(snapshot: SnapshotPronostico | dict[str, Any],
                     directorio: str | None = None) -> str:
    """
    Escribe el snapshot UNA sola vez. Si ya existe, NO se sobrescribe: el pasado
    no se reescribe. Devuelve la ruta.
    """
    datos = snapshot.a_dict() if isinstance(snapshot, SnapshotPronostico) else snapshot
    destino = ruta_snapshot(str(datos.get("id_trabajo")), directorio)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    if os.path.exists(destino):
        return destino
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    return destino


def snapshot_desde_analisis(preparado: dict[str, Any],
                            publicado_en: str | None = None,
                            modo_sombra: dict[str, Any] | None = None) -> SnapshotPronostico:
    """
    Construye el snapshot a partir del resultado de `preparar_analisis`.
    Guarda TODAS las selecciones estimables (incluidas las inferiores al umbral)
    para poder evaluar el modelo completo, y marca aparte las publicadas.
    """
    analisis = preparado["analisis"]
    metadatos = getattr(analisis, "metadatos", {}) or {}
    abstenido = bool(preparado.get("abstenido") or getattr(analisis, "abstenido", False))
    if abstenido:
        dec_cob = metadatos.get("decision_cobertura") or {}
        est_cob = str(dec_cob.get("estado") or metadatos.get("estado_cobertura") or "abstenido")
        mot_abs = str(dec_cob.get("alerta_descriptiva") or metadatos.get("motivo_abstencion") or "")
        umbral_val = float(getattr(analisis.parametros, "umbral", 0.70) if hasattr(analisis, "parametros") and analisis.parametros else 0.70)
        return SnapshotPronostico(
            id_trabajo=preparado.get("id_trabajo") or "abstenido",
            equipos={"local": metadatos.get("equipo_local"), "visitante": metadatos.get("equipo_visitante")},
            partido={
                "fecha": metadatos.get("fecha_partido"),
                "competicion": metadatos.get("competicion"),
                "sede": metadatos.get("sede"),
                "condicion_local": metadatos.get("condicion_local"),
                "condicion_visitante": metadatos.get("condicion_visitante"),
                "origen_condicion": metadatos.get("origen_condicion"),
            },
            generado_en=str(metadatos.get("generado_en")),
            publicado_en=publicado_en,
            modelo={"nombre": NOMBRE_MODELO, "version": VERSION_MODELO, "umbral": umbral_val,
                    "w_condicion": 0.0, "w_xg": 0.0, "distribucion_conteos": "poisson"},
            datos_corte={"abstenido": True, "estado_cobertura": est_cob, "motivo_abstencion": mot_abs, "fuente": metadatos.get("fuente")},
            selecciones=[],
            selecciones_publicadas=[],
            umbral_publicado=umbral_val,
            referencia=(preparado.get("rutas") or {}).get("informe") or "",
            abstenido=True,
            estado_cobertura=est_cob,
            motivo_abstencion=mot_abs,
            decision_cobertura=dec_cob,
        )
    motor = analisis.motor
    umbral = float(analisis.parametros.umbral)

    todas: list[dict[str, Any]] = []
    publicadas: list[dict[str, Any]] = []
    for res in analisis.mercados:
        for s in res.selecciones:
            if not s.estimable or s.probabilidad is None:
                continue
            entrada = {
                "mercado": s.mercado, "id_mercado": s.id_mercado, "evento": s.evento,
                "etiqueta": s.etiqueta, "periodo": s.periodo, "equipo": s.equipo,
                "linea": s.linea, "probabilidad": s.probabilidad,
                "estado_sensibilidad": s.estado or "no_evaluada",
                "escenarios": s.escenarios,
                "supera_umbral": bool(s.probabilidad > umbral),
            }
            todas.append(entrada)
            if s.probabilidad > umbral:
                publicadas.append(dict(entrada))

    return SnapshotPronostico(
        id_trabajo=preparado["id_trabajo"],
        equipos={"local": metadatos.get("equipo_local"),
                 "visitante": metadatos.get("equipo_visitante")},
        partido={
            "fecha": metadatos.get("fecha_partido"),
            "competicion": metadatos.get("competicion"),
            "sede": metadatos.get("sede"),
            "condicion_local": metadatos.get("condicion_local"),
            "condicion_visitante": metadatos.get("condicion_visitante"),
            "origen_condicion": metadatos.get("origen_condicion"),
        },
        generado_en=str(metadatos.get("generado_en")),
        publicado_en=publicado_en,
        modelo={"nombre": NOMBRE_MODELO, "version": VERSION_MODELO,
                "umbral": umbral,
                "w_condicion": analisis.parametros.w_condicion,
                "w_xg": analisis.parametros.w_xg,
                "distribucion_conteos": analisis.parametros.distribucion_conteos},
        datos_corte={
            "partidos_local": motor.muestras["local_general"].partidos_general,
            "partidos_visitante": motor.muestras["visitante_general"].partidos_general,
            "partidos_local_condicion": motor.muestras["local_cond"].partidos_condicion,
            "partidos_visitante_condicion": motor.muestras["visitante_cond"].partidos_condicion,
            "fuente": metadatos.get("fuente"),
            "lambda_local_ft": motor.tasas.lambda_local_ft,
            "lambda_visitante_ft": motor.tasas.lambda_visitante_ft,
        },
        selecciones=todas,
        selecciones_publicadas=publicadas,
        umbral_publicado=umbral,
        referencia=preparado.get("rutas", {}).get("informe", ""),
        origen="nuevo",
        modo_sombra=modo_sombra if modo_sombra is not None else preparado.get("modo_sombra"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. IMPORTADOR DE HISTÓRICOS (análisis ya guardados por el motor de mercados)
# ═══════════════════════════════════════════════════════════════════════════════

LIMITACIONES_IMPORTADO = [
    "La probabilidad original NO está redondeada en el archivo de mercados, pero el "
    "mensaje de Telegram mostró el valor redondeado a 1 decimal: se conservan ambos.",
    "El archivo histórico no registra la hora exacta de publicación en Telegram; se "
    "usa como cota superior la marca de generación.",
    "No registra la fecha de inicio del partido: la comprobación «pronóstico anterior "
    "al inicio» se resuelve después, al consultar el resultado real.",
]


def importar_analisis_historico(ruta: str) -> dict[str, Any]:
    """
    Convierte un análisis histórico de mercados en un registro de pronóstico
    evaluable, marcando explícitamente sus limitaciones. No modifica el original.
    """
    with open(ruta, "r", encoding="utf-8") as f:
        d = json.load(f)
    met = d.get("metadatos", {})
    params = d.get("parametros", {})
    datos_corte = d.get("datos_corte") or {
        "partidos_local": None, "partidos_visitante": None,
        "fuente": met.get("fuente"),
        "lambda_local_ft": (d.get("lambda") or {}).get("local_ft"),
        "lambda_visitante_ft": (d.get("lambda") or {}).get("visitante_ft"),
    }

    sels = []
    for s in d.get("selecciones_filtradas", []):
        p = s.get("probabilidad")
        sels.append({
            "mercado": s.get("mercado"), "id_mercado": None, "evento": s.get("evento"),
            "etiqueta": s.get("etiqueta"), "periodo": s.get("periodo"),
            "equipo": s.get("equipo"), "linea": s.get("linea"),
            "probabilidad": p,
            "porcentaje_mostrado": (f"{p*100:.1f}".replace(".", ",") if isinstance(p, (int, float))
                                    else None),
            "estado_sensibilidad": s.get("estado") or "no_evaluada",
            "escenarios": s.get("escenarios") or {},
            # El filtro publicado fue p>umbral; con el valor sin redondear es resoluble
            "supera_umbral": bool(isinstance(p, (int, float)) and p > params.get("umbral", UMBRAL_DEFECTO)),
            "pertenencia_filtro_cierta": True,
        })

    return {
        "esquema": "pronostico-v1",
        "id_trabajo": d.get("id_trabajo"),
        "equipos": {"local": met.get("equipo_local"), "visitante": met.get("equipo_visitante")},
        "partido": {"fecha": met.get("fecha_partido"), "competicion": met.get("competicion"),
                    "sede": met.get("sede"),
                    "condicion_local": met.get("condicion_local"),
                    "condicion_visitante": met.get("condicion_visitante"),
                    "origen_condicion": met.get("origen_condicion")},
        "generado_en": met.get("generado_en"),
        "publicado_en": None,
        "modelo": {"nombre": met.get("modelo", NOMBRE_MODELO),
                   "version": (met.get("modelo", "") or "").split(" ")[-1] or VERSION_MODELO,
                   "umbral": params.get("umbral", UMBRAL_DEFECTO),
                   "w_condicion": params.get("w_condicion"),
                   "w_xg": params.get("w_xg"),
                   "distribucion_conteos": params.get("distribucion_conteos")},
        "datos_corte": datos_corte,
        "umbral_publicado": params.get("umbral", UMBRAL_DEFECTO),
        "selecciones": sels,
        "selecciones_publicadas": sels,
        "referencia": ruta,
        "origen": "importado_historico",
        "limitaciones": LIMITACIONES_IMPORTADO,
        "inmutable": True,
    }


def cargar_o_importar_pronosticos(directorio_mercados: str | None = None,
                                  directorio_pronosticos: str | None = None
                                  ) -> list[dict[str, Any]]:
    """
    Devuelve todos los pronósticos evaluables: primero los snapshots inmutables y
    después los análisis históricos que aún no tengan snapshot. Si un análisis ya
    tiene snapshot, NO se duplica.
    """
    snapshots: dict[str, dict[str, Any]] = {}
    for ruta in glob.glob(os.path.join(directorio_pronosticos or PRONOSTICOS_DIR, "*.json")):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("id_trabajo"):
                d["_ruta"] = ruta
                snapshots[str(d["id_trabajo"])] = d
        except Exception:
            continue

    importados: list[dict[str, Any]] = []
    for ruta in sorted(glob.glob(os.path.join(directorio_mercados or MERCADOS_DIR,
                                              "**", "mercados_*.json"), recursive=True)):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        tid = str(d.get("id_trabajo"))
        if tid in snapshots:
            continue
        try:
            doc = importar_analisis_historico(ruta)
        except Exception:
            continue
        doc["_ruta"] = ruta
        importados.append(doc)

    return list(snapshots.values()) + importados


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RESULTADO REAL VERIFICADO
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ResultadoPartido:
    """
    Resultado final verificado de un partido, con todo lo necesario para liquidar
    los mercados del catálogo. Lo que no se pudo obtener queda en None o en listas
    vacías: NO se inventa ni se divide un total.
    """
    id_partido: str | None = None
    estado: str = "no_verificado"        # finalizado | no_finalizado | no_encontrado
    local: str = ""
    visitante: str = ""
    goles_local: int | None = None
    goles_visitante: int | None = None
    goles_local_1t: int | None = None
    goles_visitante_1t: int | None = None
    inicio_utc: str | None = None
    competicion: str | None = None
    fuente: str = ""
    consultado_en: str = ""
    # Detalle por mercado
    corners_local: dict[str, int | None] = field(default_factory=dict)   # FT/1T/2T
    corners_visitante: dict[str, int | None] = field(default_factory=dict)
    amarillas_local: dict[str, int | None] = field(default_factory=dict)
    amarillas_visitante: dict[str, int | None] = field(default_factory=dict)
    rojas_local: int | None = None
    rojas_visitante: int | None = None
    goles: list[dict[str, Any]] = field(default_factory=list)   # minuto, equipo, autor, propio
    tarjetas: list[dict[str, Any]] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)
    stats_periodos: dict[str, Any] = field(default_factory=dict)
    discrepancias: list[str] = field(default_factory=list)

    @property
    def finalizado(self) -> bool:
        return self.estado == "finalizado" and self.goles_local is not None \
            and self.goles_visitante is not None

    def a_dict(self) -> dict[str, Any]:
        return {
            "id_partido": self.id_partido, "estado": self.estado,
            "local": self.local, "visitante": self.visitante,
            "goles": {"local": self.goles_local, "visitante": self.goles_visitante,
                      "local_1t": self.goles_local_1t, "visitante_1t": self.goles_visitante_1t},
            "inicio_utc": self.inicio_utc, "competicion": self.competicion,
            "fuente": self.fuente, "consultado_en": self.consultado_en,
            "corners": {"local": self.corners_local, "visitante": self.corners_visitante},
            "amarillas": {"local": self.amarillas_local, "visitante": self.amarillas_visitante},
            "rojas": {"local": self.rojas_local, "visitante": self.rojas_visitante},
            "eventos_gol": self.goles, "tarjetas": self.tarjetas, "notas": self.notas,
            "stats_periodos": self.stats_periodos, "discrepancias": self.discrepancias,
        }


def _titulo_stats(bloque: dict[str, Any], titulo: str) -> list[Any] | None:
    """Busca un título de estadística en un bloque de periodo."""
    for g in bloque.get("stats", []) or []:
        for it in g.get("stats", []) or []:
            if str(it.get("title") or "").strip().lower() == titulo.lower():
                valores = it.get("stats")
                if isinstance(valores, list) and len(valores) >= 2:
                    return valores
    return None


def _int_o_none(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return None


def _float_o_none(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _aplicar_flashscore_en_resultado(
    res: ResultadoPartido,
    fs_payload: dict[str, Any],
    scraper_vacio: bool = False,
    detalle_scraper_notas: list[str] | None = None,
) -> None:
    """
    Aplica estadísticas verificadas de Flashscore (FT, 1T, 2T) sobre `res` y
    registra explícitamente cualquier discrepancia respecto al payload del scraper.
    """
    if not isinstance(fs_payload, dict) or not fs_payload:
        return

    periodos_fs = fs_payload.get("periodos") if isinstance(fs_payload.get("periodos"), dict) else fs_payload
    ft_d = periodos_fs.get("FT") or {}
    t1_d = periodos_fs.get("1T") or {}
    t2_d = periodos_fs.get("2T") or {}

    def _par(bloque: dict[str, Any], *claves: str) -> tuple[Any, Any]:
        if not isinstance(bloque, dict):
            return None, None
        for k in claves:
            v = bloque.get(k)
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                return v[0], v[1]
            if isinstance(v, dict):
                l_v = v.get("local") if "local" in v else v.get("equipo")
                v_v = v.get("visitante") if "visitante" in v else v.get("rival")
                if l_v is not None or v_v is not None:
                    return l_v, v_v
            if f"{k}_local" in bloque or f"{k}_visitante" in bloque:
                return bloque.get(f"{k}_local"), bloque.get(f"{k}_visitante")
        return None, None

    gl_fs, gv_fs = _par(ft_d, "goles", "marcador")
    gl_i, gv_i = _int_o_none(gl_fs), _int_o_none(gv_fs)
    if gl_i is not None and gv_i is not None:
        if res.goles_local is not None and res.goles_visitante is not None:
            if (res.goles_local, res.goles_visitante) != (gl_i, gv_i):
                msg = (
                    f"Discrepancia marcador scraper ({res.goles_local}-{res.goles_visitante}) "
                    f"vs Flashscore ({gl_i}-{gv_i}): no se reconcilia marcador automáticamente."
                )
                res.discrepancias.append(msg)
                res.notas.append(msg)
        else:
            res.goles_local = gl_i
            res.goles_visitante = gv_i
            res.estado = "finalizado"

    g1l_fs, g1v_fs = _par(t1_d, "goles", "marcador")
    g1l_i, g1v_i = _int_o_none(g1l_fs), _int_o_none(g1v_fs)
    if g1l_i is not None and g1v_i is not None:
        if res.goles_local_1t is None or res.goles_visitante_1t is None:
            res.goles_local_1t = g1l_i
            res.goles_visitante_1t = g1v_i
        elif (res.goles_local_1t, res.goles_visitante_1t) != (g1l_i, g1v_i):
            msg = (
                f"Discrepancia 1T scraper ({res.goles_local_1t}-{res.goles_visitante_1t}) "
                f"vs Flashscore ({g1l_i}-{g1v_i})."
            )
            res.discrepancias.append(msg)
            res.notas.append(msg)

    # Validar suma 1T + 2T == FT si viene 2T
    g2l_fs, g2v_fs = _par(t2_d, "goles", "marcador")
    g2l_i, g2v_i = _int_o_none(g2l_fs), _int_o_none(g2v_fs)
    if (
        g1l_i is not None and g1v_i is not None
        and g2l_i is not None and g2v_i is not None
        and res.goles_local is not None and res.goles_visitante is not None
    ):
        if (g1l_i + g2l_i, g1v_i + g2v_i) != (res.goles_local, res.goles_visitante):
            res.discrepancias.append(
                f"Inconsistencia 1T+2T!=FT en Flashscore: 1T {g1l_i}-{g1v_i} + 2T {g2l_i}-{g2v_i} != FT {res.goles_local}-{res.goles_visitante}"
            )

    for per, pdict in (("FT", ft_d), ("1T", t1_d), ("2T", t2_d)):
        if not isinstance(pdict, dict) or not pdict:
            continue
        cl, cv = _par(pdict, "corners")
        cl_i, cv_i = _int_o_none(cl), _int_o_none(cv)
        if cl_i is not None and cv_i is not None:
            if res.corners_local.get(per) is None:
                res.corners_local[per] = cl_i
                res.corners_visitante[per] = cv_i
            elif (res.corners_local[per], res.corners_visitante[per]) != (cl_i, cv_i):
                res.discrepancias.append(
                    f"Discrepancia córneres {per}: scraper ({res.corners_local[per]}-{res.corners_visitante[per]}) vs Flashscore ({cl_i}-{cv_i})"
                )

        al, av = _par(pdict, "amarillas", "tarjetas_amarillas")
        al_i, av_i = _int_o_none(al), _int_o_none(av)
        if al_i is not None and av_i is not None:
            if res.amarillas_local.get(per) is None:
                res.amarillas_local[per] = al_i
                res.amarillas_visitante[per] = av_i
            elif (res.amarillas_local[per], res.amarillas_visitante[per]) != (al_i, av_i):
                res.discrepancias.append(
                    f"Discrepancia amarillas {per}: scraper ({res.amarillas_local[per]}-{res.amarillas_visitante[per]}) vs Flashscore ({al_i}-{av_i})"
                )

        # Conservar métricas detalladas para tabla/ledger/banco
        per_stats: dict[str, Any] = {}
        for k_stat in (
            "goles", "corners", "xg", "xgot", "goles_evitados",
            "remates_totales", "remates_puerta", "grandes_ocasiones",
            "posesion_pct", "faltas", "amarillas", "fueras_de_juego",
            "toques_area_rival", "xa_equipo", "rojas",
        ):
            vl, vv = _par(pdict, k_stat)
            if vl is not None or vv is not None:
                per_stats[k_stat] = [vl, vv]
        if per_stats:
            res.stats_periodos[per] = per_stats

    rl_fs, rv_fs = _par(ft_d, "rojas", "tarjetas_rojas")
    if _int_o_none(rl_fs) is not None and res.rojas_local is None:
        res.rojas_local = _int_o_none(rl_fs)
    if _int_o_none(rv_fs) is not None and res.rojas_visitante is None:
        res.rojas_visitante = _int_o_none(rv_fs)

    if isinstance(fs_payload.get("goles_eventos"), list) and not res.goles:
        res.goles = list(fs_payload["goles_eventos"])
    if not res.inicio_utc and fs_payload.get("inicio_utc"):
        res.inicio_utc = str(fs_payload["inicio_utc"])

    if scraper_vacio:
        notas_txt = "; ".join(detalle_scraper_notas or [])
        msg = (
            "Discrepancia FotMob scraper vs Flashscore: el payload del scraper FotMob "
            f"devolvió matriz vacía sin estadísticas de detalle FT/1T/2T ({notas_txt or 'filas vacías'}), "
            "mientras Flashscore sí reportó marcador por mitad, córneres, tarjetas y xG/xGOT verificados."
        )
        res.discrepancias.append(msg)
        res.notas.append(msg)
        if "flashscore" not in (res.fuente or "").lower():
            res.fuente = f"{res.fuente}+flashscore" if res.fuente else "flashscore"


def _extraer_resultado_desde_matriz(
    detalle: dict[str, Any],
    consultado_en: str | None = None,
    fuente: str = "fotmob",
) -> ResultadoPartido:
    res = ResultadoPartido(fuente=fuente, consultado_en=consultado_en or _ahora())
    mat = detalle.get("matriz") if isinstance(detalle.get("matriz"), dict) else detalle
    condicion = str(mat.get("condicion") or "casa").lower()
    if mat.get("local") and mat.get("visitante"):
        res.local = str(mat.get("local") or "")
        res.visitante = str(mat.get("visitante") or "")
        lado_l, lado_v = ("rival", "equipo") if condicion == "fuera" else ("equipo", "rival")
    elif condicion == "fuera":
        res.local = str(mat.get("rival") or "")
        res.visitante = str(mat.get("equipo") or "")
        lado_l, lado_v = "rival", "equipo"
    else:
        res.local = str(mat.get("equipo") or "")
        res.visitante = str(mat.get("rival") or "")
        lado_l, lado_v = "equipo", "rival"

    marc = mat.get("marcador") or {}
    geq = marc.get("equipo") if marc.get("equipo") is not None else mat.get("goles_equipo")
    griv = marc.get("rival") if marc.get("rival") is not None else mat.get("goles_rival")
    if geq is not None and griv is not None:
        gl_raw = griv if lado_l == "rival" else geq
        gv_raw = geq if lado_l == "rival" else griv
        res.goles_local = _int_o_none(gl_raw)
        res.goles_visitante = _int_o_none(gv_raw)

    res.id_partido = str(mat.get("id_partido") or mat.get("id") or detalle.get("id_partido") or "") or None
    res.competicion = mat.get("torneo") or mat.get("competicion") or detalle.get("competicion")
    res.inicio_utc = str(mat.get("inicio_utc") or detalle.get("inicio_utc") or "") or None

    st_raw = detalle.get("status") or mat.get("status")
    if isinstance(st_raw, dict):
        if st_raw.get("finished"):
            res.estado = "finalizado"
        elif st_raw.get("started"):
            res.estado = "en_juego"
        else:
            res.estado = "no_finalizado"
    elif mat.get("estado"):
        res.estado = str(mat.get("estado"))
    elif res.goles_local is not None and res.goles_visitante is not None:
        res.estado = "finalizado"

    periodos = mat.get("periodos") or {}
    total_filas = 0
    for per in ("FT", "1T", "2T"):
        filas = (periodos.get(per) or {}).get("filas") or []
        total_filas += len(filas)
        fmap = {f.get("clave"): f for f in filas if isinstance(f, dict) and f.get("clave")}
        fc = fmap.get("corners") or {}
        if not fc.get("estimado"):
            cl, cv = _int_o_none(fc.get(lado_l)), _int_o_none(fc.get(lado_v))
            if cl is not None and cv is not None:
                res.corners_local[per] = cl
                res.corners_visitante[per] = cv
        fa = fmap.get("tarjetas_amarillas") or {}
        if not fa.get("estimado"):
            al, av = _int_o_none(fa.get(lado_l)), _int_o_none(fa.get(lado_v))
            if al is not None and av is not None:
                res.amarillas_local[per] = al
                res.amarillas_visitante[per] = av
        if per == "FT":
            fr = fmap.get("tarjetas_rojas") or {}
            if not fr.get("estimado"):
                rl, rv = _int_o_none(fr.get(lado_l)), _int_o_none(fr.get(lado_v))
                if rl is not None and rv is not None:
                    res.rojas_local = rl
                    res.rojas_visitante = rv

    g1t_l = mat.get("goles_local_1t")
    g1t_v = mat.get("goles_visitante_1t")
    if isinstance(mat.get("marcador_1t"), (list, tuple)) and len(mat["marcador_1t"]) >= 2:
        g1t_l, g1t_v = mat["marcador_1t"][0], mat["marcador_1t"][1]
    if _int_o_none(g1t_l) is not None and _int_o_none(g1t_v) is not None:
        res.goles_local_1t = _int_o_none(g1t_l)
        res.goles_visitante_1t = _int_o_none(g1t_v)

    scraper_vacio = (total_filas == 0)
    notas_mat = [str(n) for n in (mat.get("notas") or []) if n]
    for n in notas_mat:
        res.notas.append(n)
    if scraper_vacio and res.goles_local is not None:
        msg_empty = (
            f"Scraper ({fuente}) sin filas de detalle FT/1T/2T para {res.local} vs {res.visitante} "
            f"({res.goles_local}-{res.goles_visitante}): córneres, tarjetas y descanso quedan en None (sin_dato)."
        )
        res.notas.append(msg_empty)

    fs_payload = (
        detalle.get("flashscore_stats")
        or detalle.get("flashscore")
        or mat.get("flashscore_stats")
        or mat.get("flashscore")
    )
    if isinstance(fs_payload, dict) and fs_payload:
        _aplicar_flashscore_en_resultado(res, fs_payload, scraper_vacio=scraper_vacio, detalle_scraper_notas=notas_mat)

    return res


def extraer_resultado(detalle: dict[str, Any], consultado_en: str | None = None,
                      fuente: str = "fotmob") -> ResultadoPartido:
    """
    Convierte la respuesta de `matchDetails` en un ResultadoPartido verificable.
    Todo campo que no venga en la respuesta queda en None (no se inventa).
    """
    res = ResultadoPartido(fuente=fuente, consultado_en=consultado_en or _ahora())
    if not isinstance(detalle, dict):
        res.notas.append("respuesta vacía: no verificable")
        return res

    if "header" not in detalle and any(
        k in detalle for k in ("matriz", "periodos", "goles_equipo", "marcador", "flashscore_stats", "flashscore")
    ):
        return _extraer_resultado_desde_matriz(detalle, consultado_en=consultado_en, fuente=fuente)

    header = detalle.get("header") or {}
    equipos = header.get("teams") or []
    if len(equipos) >= 2:
        res.local = str(equipos[0].get("name") or "")
        res.visitante = str(equipos[1].get("name") or "")
        res.goles_local = equipos[0].get("score")
        res.goles_visitante = equipos[1].get("score")

    status = header.get("status") or {}
    # Hora PROGRAMADA del partido: es la que debe usarse para comprobar que el
    # pronóstico es anterior. El nombre del partido la trae en UTC.
    nombre = str((detalle.get("general") or {}).get("matchName") or "")
    m = re.search(r"(\d{4}-\d{2}-\d{2}),?\s*(\d{2}:\d{2})\s*UTC", nombre)
    if m:
        res.inicio_utc = f"{m.group(1)}T{m.group(2)}:00Z"
    else:
        # Alternativa: inicio real del primer tiempo, en formato DD.MM.YYYY HH:MM:SS
        crudo = (status.get("halfs") or {}).get("firstHalfStarted") or ""
        mm = re.match(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}:\d{2}:\d{2})", str(crudo))
        if mm:
            res.inicio_utc = f"{mm.group(3)}-{mm.group(2)}-{mm.group(1)}T{mm.group(4)}Z"
        else:
            res.inicio_utc = str(crudo) or None
    if not res.inicio_utc or res.inicio_utc == "None":
        utc_directo = status.get("utcTime") or (detalle.get("general") or {}).get("matchTimeUTC")
        if utc_directo:
            s_u = str(utc_directo)
            res.inicio_utc = s_u if s_u.endswith("Z") else (s_u[:19] + "Z")
    if status.get("finished"):
        res.estado = "finalizado"
    elif status.get("started"):
        res.estado = "en_juego"
    else:
        res.estado = "no_finalizado"

    generado = detalle.get("general") or {}
    res.id_partido = str(generado.get("matchId")) if generado.get("matchId") else None
    res.competicion = generado.get("leagueName")

    # ── Goles con minuto, autor y periodo ──────────────────────────────────
    eventos_header = header.get("events") or {}
    for lado, dicc in (("local", eventos_header.get("homeTeamGoals") or {}),
                       ("visitante", eventos_header.get("awayTeamGoals") or {})):
        for jugador, lista in (dicc or {}).items():
            for g in lista or []:
                sm = g.get("shotmapEvent") or {}
                res.goles.append({
                    "minuto": sm.get("min") if sm.get("min") is not None else g.get("time"),
                    "minuto_adicional": sm.get("minAdded"),
                    "periodo": sm.get("period"),
                    "equipo": lado,
                    "autor": jugador,
                    "autogol": bool(g.get("ownGoal")),
                    "penalti": bool(sm.get("isPenalty")),
                    "marcador_previo": [g.get("homeScore"), g.get("awayScore")],
                    "marcador_nuevo": g.get("newScore"),
                })
    res.goles.sort(key=lambda x: (x.get("minuto") or 0, x.get("minuto_adicional") or 0))

    # Descanso: goles de FirstHalf (si no hay goles, 0-0 es correcto)
    res.goles_local_1t = sum(1 for g in res.goles
                             if g["equipo"] == "local" and g["periodo"] == "FirstHalf")
    res.goles_visitante_1t = sum(1 for g in res.goles
                                 if g["equipo"] == "visitante" and g["periodo"] == "FirstHalf")
    if not res.goles:
        res.notas.append("Sin eventos de gol registrados: descanso asumido 0-0 "
                         "(partido con 0 goles)" if res.goles_local == 0
                         else "Sin eventos de gol pero marcador no nulo: revisar")

    # ── Tarjetas (minuto y tipo desde la cronología) ───────────────────────
    for e in ((detalle.get("content", {}).get("matchFacts", {}) or {})
              .get("events", {}) or {}).get("events", []) or []:
        if str(e.get("type")) == "Card":
            res.tarjetas.append({
                "minuto": e.get("time"), "periodo": None,
                "equipo": "local" if e.get("isHome") else "visitante",
                "jugador": (e.get("player") or {}).get("name"),
                "tipo": (e.get("card") or e.get("cardType") or "amarilla"),
            })
    for lado, clave in (("local", "homeTeamRedCards"), ("visitante", "awayTeamRedCards")):
        rojas = eventos_header.get(clave)
        if isinstance(rojas, dict):
            total = sum(len(v or []) for v in rojas.values())
            if lado == "local":
                res.rojas_local = total
            else:
                res.rojas_visitante = total

    # ── Córneres y amarillas por periodo (desde stats.Periods) ─────────────
    periodos = ((detalle.get("content", {}).get("stats") or {}).get("Periods") or {})
    mapa = {"All": "FT", "FirstHalf": "1T", "SecondHalf": "2T"}
    for nombre_api, periodo in mapa.items():
        bloque = periodos.get(nombre_api) or {}
        c = _titulo_stats(bloque, "Corners")
        if c is not None:
            res.corners_local[periodo] = c[0]
            res.corners_visitante[periodo] = c[1]
        a = _titulo_stats(bloque, "Yellow cards")
        if a is not None:
            res.amarillas_local[periodo] = a[0]
            res.amarillas_visitante[periodo] = a[1]
        r = _titulo_stats(bloque, "Red cards")
        if r is not None and nombre_api == "All":
            res.rojas_local = r[0]
            res.rojas_visitante = r[1]

    faltantes = [k for k, v in (("corners FT", res.corners_local.get("FT")),
                                ("corners 1T", res.corners_local.get("1T")),
                                ("amarillas FT", res.amarillas_local.get("FT")),
                                ("amarillas 1T", res.amarillas_local.get("1T")))
                 if v is None]
    if faltantes:
        res.notas.append(f"sin dato en la fuente para: {', '.join(faltantes)}")
    fs_extra = detalle.get("flashscore_stats") or detalle.get("flashscore")
    if isinstance(fs_extra, dict) and fs_extra:
        _aplicar_flashscore_en_resultado(res, fs_extra, scraper_vacio=(not periodos))
    return res


# ═══════════════════════════════════════════════════════════════════════════════
# 4. LIQUIDACIÓN POR SELECCIÓN, CON LA DEFINICIÓN ORIGINAL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Evaluacion:
    """
    Evaluación de UNA selección. `evento_ocurrio` y `estado_liquidacion` son
    campos separados: una devolución no es una victoria ni una derrota.
    """
    id_trabajo: str
    partido: str
    mercado: str
    evento: str
    etiqueta: str
    periodo: str
    equipo: str | None
    linea: float | None
    probabilidad_original: float | None
    probabilidad_origen: str          # exacta | redondeada | desconocida
    estado_sensibilidad: str
    estado_liquidacion: str           # ganada | perdida | devolucion | ...
    evento_ocurrio: int | None        # 1 | 0 | None (no evaluable)
    objetivo_probabilidad: str        # binario | victoria_efectiva | multicategoria | ...
    detalle: str = ""
    referencia: str = ""
    id_partido: str | None = None

    @property
    def evaluable_binario(self) -> bool:
        """Solo los binarios completos sin devolución entran en calibración/Brier."""
        return (self.evento_ocurrio is not None
                and self.objetivo_probabilidad == "binario"
                and self.estado_liquidacion in ("ganada", "perdida")
                and self.probabilidad_original is not None)

    def a_dict(self) -> dict[str, Any]:
        return {
            "id_trabajo": self.id_trabajo, "partido": self.partido, "id_partido": self.id_partido,
            "mercado": self.mercado, "evento": self.evento, "etiqueta": self.etiqueta,
            "periodo": self.periodo, "equipo": self.equipo, "linea": self.linea,
            "probabilidad_original": self.probabilidad_original,
            "probabilidad_origen": self.probabilidad_origen,
            "estado_sensibilidad": self.estado_sensibilidad,
            "estado_liquidacion": self.estado_liquidacion,
            "evento_ocurrio": self.evento_ocurrio,
            "objetivo_probabilidad": self.objetivo_probabilidad,
            "detalle": self.detalle, "referencia": self.referencia,
            "evaluable_binario": self.evaluable_binario,
        }


def _totales(resultado: ResultadoPartido, periodo: str) -> int | None:
    """Total de goles de un periodo, o None si no se puede determinar."""
    if not resultado.finalizado:
        return None
    if periodo == "FT":
        return resultado.goles_local + resultado.goles_visitante
    if periodo == "1T":
        if resultado.goles_local_1t is None or resultado.goles_visitante_1t is None:
            return None
        return resultado.goles_local_1t + resultado.goles_visitante_1t
    if periodo == "2T":
        if resultado.goles_local_1t is None or resultado.goles_visitante_1t is None:
            return None
        return (resultado.goles_local + resultado.goles_visitante
                - resultado.goles_local_1t - resultado.goles_visitante_1t)
    return None


def _goles_equipo(resultado: ResultadoPartido, equipo: str | None, periodo: str) -> int | None:
    if not resultado.finalizado:
        return None
    es_local = _clave_equipo(equipo or "") == _clave_equipo(resultado.local)
    if periodo == "FT":
        return resultado.goles_local if es_local else resultado.goles_visitante
    if periodo == "1T":
        if resultado.goles_local_1t is None:
            return None
        return resultado.goles_local_1t if es_local else resultado.goles_visitante_1t
    if periodo == "2T":
        if resultado.goles_local_1t is None:
            return None
        if es_local:
            return resultado.goles_local - resultado.goles_local_1t
        return resultado.goles_visitante - resultado.goles_visitante_1t
    return None


def _corners(resultado: ResultadoPartido, equipo: str | None, periodo: str) -> int | None:
    es_local = _clave_equipo(equipo or "") == _clave_equipo(resultado.local)
    fuente = resultado.corners_local if es_local else resultado.corners_visitante
    return fuente.get(periodo)


def _amarillas(resultado: ResultadoPartido, periodo: str = "FT") -> int | None:
    a, b = resultado.amarillas_local.get(periodo), resultado.amarillas_visitante.get(periodo)
    if a is None or b is None:
        return None
    return a + b


def _interpretar_linea(texto: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", str(texto or ""))
    return float(m.group(1)) if m else None


def liquidar(seleccion: dict[str, Any], resultado: ResultadoPartido,
             id_trabajo: str) -> Evaluacion:
    if seleccion.get("abstenido") or seleccion.get("estado_liquidacion") == "abstenido_por_cobertura":
        return Evaluacion(
            id_trabajo=id_trabajo,
            partido=f"{resultado.local} vs {resultado.visitante}",
            mercado=str(seleccion.get("mercado") or ""),
            evento=str(seleccion.get("evento") or ""),
            etiqueta=str(seleccion.get("etiqueta") or ""),
            periodo=str(seleccion.get("periodo") or "FT"),
            equipo=seleccion.get("equipo"),
            linea=seleccion.get("linea"),
            probabilidad_original=None,
            probabilidad_origen="abstenido",
            estado_sensibilidad="abstenido",
            id_partido=resultado.id_partido,
            referencia=str(seleccion.get("referencia") or ""),
            estado_liquidacion="abstenido_por_cobertura",
            evento_ocurrio=None,
            objetivo_probabilidad="no_evaluable",
            detalle=str(seleccion.get("motivo") or "Pronóstico abstenido por política de cobertura insuficiente"),
        )
    """
    Liquida UNA selección usando la definición original del pronóstico.

    Reglas clave implementadas:
      • «más de N» requiere superar N estrictamente; la línea entera puede devolver.
      • El hándicap europeo tiene TRES resultados: el empate ajustado no es victoria
        del equipo ni devolución.
      • Apuesta sin empate: victoria, empate/devolución y derrota son estados
        distintos; en un empate el evento «victoria» no ocurrió (y=0) pero la
        liquidación es devolución, no pérdida.
      • Las combinaciones exigen TODAS sus condiciones.
      • Si falta el dato necesario -> «resultado no verificable para este mercado»,
        nunca una derrota.
    """
    mercado = str(seleccion.get("mercado") or "")
    evento = str(seleccion.get("evento") or "")
    etiqueta = str(seleccion.get("etiqueta") or "")
    periodo = str(seleccion.get("periodo") or "FT")
    equipo = seleccion.get("equipo")
    linea = seleccion.get("linea")
    prob = seleccion.get("probabilidad")
    base = dict(
        id_trabajo=id_trabajo,
        partido=f"{resultado.local} vs {resultado.visitante}",
        mercado=mercado, evento=evento, etiqueta=etiqueta, periodo=periodo,
        equipo=equipo, linea=linea,
        probabilidad_original=prob if isinstance(prob, (int, float)) else None,
        probabilidad_origen=("exacta" if isinstance(prob, (int, float)) else "desconocida"),
        estado_sensibilidad=str(seleccion.get("estado_sensibilidad") or "no_evaluada"),
        id_partido=resultado.id_partido,
        referencia=str(seleccion.get("referencia") or ""),
    )

    def no_verificable(motivo: str) -> Evaluacion:
        return Evaluacion(**base, estado_liquidacion="no_verificable",
                          evento_ocurrio=None, objetivo_probabilidad="no_evaluable",
                          detalle=motivo)

    def regla_no_definida(motivo: str) -> Evaluacion:
        return Evaluacion(**base, estado_liquidacion="regla_no_definida",
                          evento_ocurrio=None, objetivo_probabilidad="no_evaluable",
                          detalle=motivo)

    def binario(ocurrio: bool, detalle: str, objetivo: str = "binario") -> Evaluacion:
        return Evaluacion(**base,
                          estado_liquidacion="ganada" if ocurrio else "perdida",
                          evento_ocurrio=1 if ocurrio else 0,
                          objetivo_probabilidad=objetivo, detalle=detalle)

    if resultado.estado == "no_encontrado":
        return no_verificable("no se localizó el partido pronosticado en la fuente")
    if resultado.estado == "no_finalizado":
        return Evaluacion(**base, estado_liquidacion="pendiente", evento_ocurrio=None,
                          objetivo_probabilidad="no_evaluable",
                          detalle="el partido aún no ha finalizado")
    if not resultado.finalizado:
        return no_verificable(f"resultado incompleto (estado={resultado.estado})")

    gl, gv = resultado.goles_local, resultado.goles_visitante
    total_ft = gl + gv

    # ── Normalización del prefijo de equipo ────────────────────────────────
    # El motor genera los totales por equipo como «local_over_0.5» /
    # «visitante_al_menos_1». Se traduce al vocabulario general fijando el
    # equipo correspondiente, sin tocar la definición del evento.
    equipo_evento = equipo
    if evento.startswith("local_"):
        evento = evento[len("local_"):]
        equipo_evento = resultado.local
    elif evento.startswith("visitante_"):
        evento = evento[len("visitante_"):]
        equipo_evento = resultado.visitante

    # ── TOTAL DE GOLES (partido o por equipo) ──────────────────────────────
    if evento.startswith(("over_", "under_", "exacto_", "al_menos_")):
        if equipo_evento:      # totales por equipo
            goles = _goles_equipo(resultado, equipo_evento, periodo)
            if goles is None:
                return no_verificable(f"sin goles de {equipo_evento} en {periodo}")
            valor = goles
        else:
            valor = _totales(resultado, periodo)
            if valor is None:
                return no_verificable(f"sin datos de goles en {periodo}")
        if evento.startswith("over_"):
            umbral = _interpretar_linea(evento)
            if umbral is None:
                return regla_no_definida("línea no interpretable")
            return binario(valor > umbral, f"{valor} goles vs línea más de {umbral}")
        if evento.startswith("under_"):
            umbral = _interpretar_linea(evento)
            if umbral is None:
                return regla_no_definida("línea no interpretable")
            return binario(valor < umbral, f"{valor} goles vs línea menos de {umbral}")
        if evento.startswith("exacto_"):
            objetivo = int(float(evento.split("_")[-1]))
            return binario(valor == objetivo, f"{valor} goles vs exacto {objetivo}")
        if evento.startswith("al_menos_"):
            objetivo = int(float(evento.split("_")[-1]))
            return binario(valor >= objetivo, f"{valor} goles vs al menos {objetivo}")

    # ── 1X2 ────────────────────────────────────────────────────────────────
    if mercado == "Ganador del partido" or evento in ("1", "X", "2"):
        if evento == "1":
            return binario(gl > gv, f"{gl}-{gv}: gana el local")
        if evento == "X":
            return binario(gl == gv, f"{gl}-{gv}: empate")
        if evento == "2":
            return binario(gv > gl, f"{gl}-{gv}: gana el visitante")

    # ── DOBLE OPORTUNIDAD ─────────────────────────────────────────────────
    if evento in ("1x", "x2", "12"):
        if evento == "1x":
            return binario(gl >= gv, f"{gl}-{gv}: 1X")
        if evento == "x2":
            return binario(gv >= gl, f"{gl}-{gv}: X2")
        return binario(gl != gv, f"{gl}-{gv}: 12")

    # ── AMBOS ANOTAN ──────────────────────────────────────────────────────
    if evento in ("btts_si", "btts_no"):
        si = gl > 0 and gv > 0
        return binario(si if evento == "btts_si" else not si,
                       f"{gl}-{gv}: {'ambos anotan' if si else 'no ambos anotan'}")

    # ── APUESTA SIN EMPATE (victoria efectiva + devolución) ────────────────
    if evento.startswith("dnb_"):
        if evento == "dnb_devolucion":
            return Evaluacion(**base, estado_liquidacion="devolucion", evento_ocurrio=None,
                              objetivo_probabilidad="devolucion",
                              detalle=f"{gl}-{gv}: empate, se devuelve el importe")
        es_local = evento == "dnb_local"
        gana = (gl > gv) if es_local else (gv > gl)
        if gl == gv:
            # El evento «victoria» NO ocurrió (y=0), pero la liquidación es devolución
            return Evaluacion(**base, estado_liquidacion="devolucion", evento_ocurrio=0,
                              objetivo_probabilidad="victoria_efectiva",
                              detalle=f"{gl}-{gv}: empate -> victoria no ocurrida, "
                                      f"liquidación con devolución")
        return binario(gana, f"{gl}-{gv}: victoria efectiva {'sí' if gana else 'no'}",
                       objetivo="victoria_efectiva")

    # ── GANADOR DEL 1T ────────────────────────────────────────────────────
    if evento in ("1t_1", "1t_X", "1t_2"):
        g1, g2 = resultado.goles_local_1t, resultado.goles_visitante_1t
        if g1 is None or g2 is None:
            return no_verificable("sin marcador de descanso verificable")
        if evento == "1t_1":
            return binario(g1 > g2, f"1T {g1}-{g2}: local")
        if evento == "1t_X":
            return binario(g1 == g2, f"1T {g1}-{g2}: empate")
        return binario(g2 > g1, f"1T {g1}-{g2}: visitante")

    # ── 1er TIEMPO / TIEMPO COMPLETO ──────────────────────────────────────
    if evento.startswith("1t") and "_ft" in evento:
        g1, g2 = resultado.goles_local_1t, resultado.goles_visitante_1t
        if g1 is None or g2 is None:
            return no_verificable("sin marcador de descanso verificable")
        r1t = "1" if g1 > g2 else ("X" if g1 == g2 else "2")
        rft = "1" if gl > gv else ("X" if gl == gv else "2")
        partes = evento.replace("1t", "").split("_ft")
        deseado_1t, deseado_ft = partes[0], partes[1]
        return binario(r1t == deseado_1t and rft == deseado_ft,
                       f"1T {r1t} / FT {rft} vs pedido 1T {deseado_1t} / FT {deseado_ft}")

    # ── EQUIPO QUE ANOTA EL GOL 1 ─────────────────────────────────────────
    if evento.startswith("primer_gol_"):
        if evento == "primer_gol_ninguno":
            if resultado.goles:
                return binario(False, f"{len(resultado.goles)} goles: sí hubo")
            if total_ft == 0:
                return binario(True, "0 goles: ninguno")
            return no_verificable("marcador con goles sin cronología minuto a minuto en la fuente")
        if not resultado.goles:
            if total_ft == 0:
                return binario(False, "no hubo goles: ningún equipo anotó primero")
            return no_verificable(f"marcador {gl}-{gv} sin cronología/minuto del primer gol en la fuente")
        primero = resultado.goles[0]
        autor = _clave_equipo(resultado.local if primero["equipo"] == "local"
                              else resultado.visitante)
        pedido = _clave_equipo(equipo or "")
        return binario(autor == pedido,
                       f"primer gol al minuto {primero.get('minuto')} de {primero.get('autor')}")

    # ── GANADOR + TOTAL ───────────────────────────────────────────────────
    if "_mas_" in evento or "_menos_" in evento:
        partes = evento.split("_")
        if len(partes) == 3 and partes[0] in ("1", "X", "2"):
            etq, lado, linea_txt = partes
            umbral = float(linea_txt)
            if etq == "1":
                acierto_resultado = gl > gv
            elif etq == "X":
                acierto_resultado = gl == gv
            else:
                acierto_resultado = gv > gl
            acierto_total = total_ft > umbral if lado == "mas" else total_ft < umbral
            return binario(acierto_resultado and acierto_total,
                           f"{gl}-{gv} total {total_ft}: resultado={'sí' if acierto_resultado else 'no'} "
                           f"total={'sí' if acierto_total else 'no'} (exige ambas)")

    # ── GANADOR + AMBOS ANOTAN ────────────────────────────────────────────
    if "_btts_" in evento:
        partes = evento.split("_btts_")
        etq, btts = partes[0], partes[1]
        if etq in ("1", "X", "2"):
            if etq == "1":
                acierto_resultado = gl > gv
            elif etq == "X":
                acierto_resultado = gl == gv
            else:
                acierto_resultado = gv > gl
            ambos = gl > 0 and gv > 0
            acierto_btts = ambos if btts == "si" else not ambos
            return binario(acierto_resultado and acierto_btts,
                           f"{gl}-{gv}: resultado={'sí' if acierto_resultado else 'no'} "
                           f"ambos={'sí' if ambos else 'no'} (exige ambas)")

    # ── AMBOS ANOTAN + TOTAL 2.5 ──────────────────────────────────────────
    if evento.startswith("btts_") and ("_mas_2.5" in evento or "_menos_2.5" in evento):
        m = re.match(r"btts_(si|no)_(mas|menos)_2\.5", evento)
        if m:
            ambos = gl > 0 and gv > 0
            acierto_btts = ambos if m.group(1) == "si" else not ambos
            acierto_total = total_ft > 2.5 if m.group(2) == "mas" else total_ft < 2.5
            return binario(acierto_btts and acierto_total,
                           f"{gl}-{gv} total {total_ft}: exige ambos={m.group(1)} y "
                           f"{m.group(2)} de 2.5")

    # ── HÁNDICAP EUROPEO (3 resultados) ───────────────────────────────────
    if evento.startswith("h") and ("_local" in evento or "_empate" in evento
                                   or "_visitante" in evento):
        m = re.match(r"h(-?\d+)_(local|empate|visitante)", evento)
        if m:
            h = int(m.group(1))
            tipo = m.group(2)
            ajustado_local = gl + h
            if tipo == "local":
                return binario(ajustado_local > gv,
                               f"{gl}+({h})={ajustado_local} vs {gv}")
            if tipo == "empate":
                return binario(ajustado_local == gv,
                               f"{gl}+({h})={ajustado_local} vs {gv}: empate con hándicap")
            return binario(ajustado_local < gv, f"{gl}+({h})={ajustado_local} vs {gv}")

    # ── GOL EN AMBOS TIEMPOS ──────────────────────────────────────────────
    if evento == "gol_1t":
        g1 = resultado.goles_local_1t
        g2 = resultado.goles_visitante_1t
        if g1 is None or g2 is None:
            return no_verificable("sin marcador de descanso verificable")
        return binario((g1 + g2) > 0, f"1T {g1}-{g2}")
    if evento == "gol_2t":
        t2 = _totales(resultado, "2T")
        if t2 is None:
            return no_verificable("sin goles del 2T verificables")
        return binario(t2 > 0, f"2T {t2} goles")
    if evento == "gol_ambos_tiempos":
        t1, t2 = _totales(resultado, "1T"), _totales(resultado, "2T")
        if t1 is None or t2 is None:
            return no_verificable("sin goles por tiempo verificables")
        return binario(t1 > 0 and t2 > 0, f"1T {t1} goles / 2T {t2} goles")

    # ── CÓRNERES ──────────────────────────────────────────────────────────
    if evento.startswith(("corners_over_", "corners_under_")) and equipo_evento:
        linea_c = _interpretar_linea(evento)
        valor = _corners(resultado, equipo_evento, periodo)
        if valor is None or linea_c is None:
            return no_verificable(f"sin córneres de {equipo_evento} en {periodo}")
        if evento.startswith("corners_over_"):
            return binario(valor > linea_c, f"{equipo_evento} {valor} córneres vs más de {linea_c}")
        return binario(valor < linea_c, f"{equipo_evento} {valor} córneres vs menos de {linea_c}")
    if evento.startswith(("local_corners_", "visitante_corners_")):
        linea_c = _interpretar_linea(evento)
        eq_c = resultado.local if evento.startswith("local_corners_") else resultado.visitante
        valor = _corners(resultado, eq_c, periodo)
        if valor is None or linea_c is None:
            return no_verificable(f"sin córneres de {eq_c} en {periodo}")
        if "_over_" in evento:
            return binario(valor > linea_c, f"{eq_c} {valor} córneres vs más de {linea_c}")
        return binario(valor < linea_c, f"{eq_c} {valor} córneres vs menos de {linea_c}")
    if evento.startswith("corners_over_") or evento.startswith("corners_under_"):
        linea_c = _interpretar_linea(evento)
        c_local = resultado.corners_local.get(periodo)
        c_visit = resultado.corners_visitante.get(periodo)
        if c_local is None or c_visit is None or linea_c is None:
            return no_verificable(f"sin córneres de {periodo} en la fuente")
        total_c = c_local + c_visit
        if evento.startswith("corners_over_"):
            return binario(total_c > linea_c, f"{c_local}+{c_visit}={total_c} vs más de {linea_c}")
        return binario(total_c < linea_c, f"{c_local}+{c_visit}={total_c} vs menos de {linea_c}")

    # ── TARJETAS ──────────────────────────────────────────────────────────
    if evento.startswith(("amarillas_over_", "amarillas_under_")):
        linea_a = _interpretar_linea(evento)
        total_a = _amarillas(resultado, "FT")
        if total_a is None or linea_a is None:
            return no_verificable("sin tarjetas amarillas verificables")
        if evento.startswith("amarillas_over_"):
            return binario(total_a > linea_a, f"{total_a} amarillas vs más de {linea_a}")
        return binario(total_a < linea_a, f"{total_a} amarillas vs menos de {linea_a}")
    if evento.startswith("rojas_over_"):
        linea_r = _interpretar_linea(evento)
        if resultado.rojas_local is None or resultado.rojas_visitante is None:
            return no_verificable("sin tarjetas rojas verificables")
        total_r = resultado.rojas_local + resultado.rojas_visitante
        return binario(total_r > (linea_r or 0.5), f"{total_r} rojas vs más de {linea_r}")

    # ── FIESTA DE GOLES (umbrales de goles) ───────────────────────────────
    if evento.startswith("fiesta_al_menos_"):
        objetivo = int(float(evento.split("_")[-1]))
        return binario(total_ft >= objetivo, f"{total_ft} goles vs al menos {objetivo}")

    # ── PAGO ANTICIPADO +2 (requiere trayectoria) ─────────────────────────
    if mercado.startswith("Ganador del partido - Pago Anticipado"):
        gano = gl > gv
        lidero_dos = False
        for g in resultado.goles:
            nuevo = g.get("marcador_nuevo") or []
            if len(nuevo) == 2 and nuevo[0] is not None and nuevo[1] is not None:
                if abs(nuevo[0] - nuevo[1]) >= 2:
                    lidero_dos = True
                    break
        # La unión: ganar al final O haber liderado por dos en cualquier momento
        return binario(gano or lidero_dos,
                       f"{gl}-{gv}: gana={gano}, lideró por 2={lidero_dos}")

    return regla_no_definida(f"sin regla de liquidación para «{mercado}» / «{evento}»")


def clave_seleccion(mercado: str, evento: str, periodo: str,
                    equipo: str | None, linea: Any) -> str:
    """Clave canónica de una selección, para comparar versiones entre sí."""
    return (f"{_sin_prefijo_equipo(mercado or '')}|{evento}|{periodo or 'FT'}|"
            f"{_clave_equipo(equipo or '')}|{linea if linea is not None else ''}")


def _clave_partido(doc: dict[str, Any]) -> str:
    equipos = doc.get("equipos") or {}
    fecha = (doc.get("partido") or {}).get("fecha")
    return f"{_clave_equipo(equipos.get('local') or '')}_vs_" \
           f"{_clave_equipo(equipos.get('visitante') or '')}_{fecha or 'sinfecha'}"


def _momento_publicacion(doc: dict[str, Any]) -> str:
    """Momento efectivo de publicación: el real o, si falta, el de generación."""
    return str(doc.get("publicado_en") or doc.get("generado_en") or "")


def resolver_versiones(pronosticos: list[dict[str, Any]],
                       regla: str = "ultima_publicada") -> dict[str, Any]:
    """
    Resuelve varias VERSIONES del mismo pronóstico sin elegir a posteriori la que
    acertó.

    Regla FIJADA ANTES de ver los resultados (`regla`):
      • «ultima_publicada» (por defecto): para cada clave de partido/selección se
        conserva la ÚLTIMA versión publicada antes del inicio.
      • «primera_publicada»: se conserva la primera emisión (registro aparte).

    Devuelve:
      selecciones_principales : las que entran en el informe principal
      ultima / primera        : índices documentados de cada versión
      actualizaciones         : todas las emisiones, con su referencia
      duplicados_evitados     : cuántas selecciones repetidas se descartaron
    """
    # Agrupar por partido; dentro, por clave de selección
    por_partido: dict[str, list[dict[str, Any]]] = {}
    for doc in pronosticos:
        por_partido.setdefault(_clave_partido(doc), []).append(doc)

    principales: list[dict[str, Any]] = []
    detalle_versiones: dict[str, Any] = {}
    duplicados_evitados = 0
    partidos_con_varias = 0

    for clave_p, docs in por_partido.items():
        # Orden cronológico por momento de publicación
        docs_ord = sorted(docs, key=_momento_publicacion)
        if len(docs_ord) > 1:
            partidos_con_varias += 1

        # Por cada clave de selección, elegir la versión según la regla
        elegidas: dict[str, tuple[int, dict[str, Any], dict[str, Any]]] = {}
        emisiones: list[dict[str, Any]] = []
        for indice, doc in enumerate(docs_ord):
            emisiones.append({
                "id_trabajo": doc.get("id_trabajo"),
                "generado_en": doc.get("generado_en"),
                "publicado_en": doc.get("publicado_en"),
                "referencia": (doc.get("referencia") or doc.get("_ruta") or "")[:200],
                "selecciones_publicadas": len(doc.get("selecciones_publicadas") or []),
            })
            for s in doc.get("selecciones_publicadas") or doc.get("selecciones") or []:
                k = clave_seleccion(s.get("mercado"), s.get("evento"), s.get("periodo"),
                                    s.get("equipo"), s.get("linea"))
                if regla == "primera_publicada":
                    if k not in elegidas:
                        elegidas[k] = (indice, s, doc)
                else:
                    elegidas[k] = (indice, s, doc)

        for k, (_idx, s, doc) in elegidas.items():
            principales.append({"seleccion": s, "id_trabajo": doc.get("id_trabajo"),
                                "generado_en": doc.get("generado_en"),
                                "referencia": doc.get("referencia") or doc.get("_ruta") or ""})
        total_emitidas = sum(len(d.get("selecciones_publicadas") or []) for d in docs_ord)
        duplicados_evitados += max(0, total_emitidas - len(elegidas))

        detalle_versiones[clave_p] = {
            "emisiones": emisiones,
            "selecciones_unicas": len(elegidas),
            "selecciones_emitidas_en_total": total_emitidas,
            "regla": regla,
            "version_usada": (docs_ord[-1].get("id_trabajo") if regla == "ultima_publicada"
                              else docs_ord[0].get("id_trabajo")),
            "primera_emision": docs_ord[0].get("id_trabajo"),
            "ultima_emision": docs_ord[-1].get("id_trabajo"),
        }

    return {
        "selecciones_principales": principales,
        "versiones": detalle_versiones,
        "duplicados_evitados": duplicados_evitados,
        "partidos_con_varias_versiones": partidos_con_varias,
        "regla_aplicada": regla,
    }


def deduplicar_eventos_equivalentes(evaluaciones: list[Evaluacion]) -> dict[str, Any]:
    """
    Deduplica eventos EXACTAMENTE equivalentes (misma clave de mercado/evento/
    línea/periodo emitida bajo nombres distintos) conservando la referencia a los
    mensajes originales, para no contar una copia como evidencia nueva.
    """
    vistas: dict[str, Evaluacion] = {}
    referencias: dict[str, list[str]] = {}
    for e in evaluaciones:
        k = clave_seleccion(e.mercado, e.evento, e.periodo, e.equipo, e.linea)
        k = f"{e.id_partido or e.partido}|{k}"
        if k in vistas:
            if e.referencia:
                referencias.setdefault(k, []).append(e.referencia)
            continue
        vistas[k] = e
        if e.referencia:
            referencias[k] = [e.referencia]
    return {
        "evaluaciones": list(vistas.values()),
        "equivalentes_eliminados": len(evaluaciones) - len(vistas),
        "referencias_por_evento": referencias,
        "vista_de_mensajes": "se conserva la referencia a cada mensaje original "
                             "en `referencias_por_evento`",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. INFORMES: BANDAS, ACUMULADOS, MERCADOS, CALIBRACIÓN Y BRIER
# ═══════════════════════════════════════════════════════════════════════════════

def _stats_banda(evaluaciones: list[Evaluacion]) -> dict[str, Any]:
    """
    Métricas de una banda. Las métricas numéricas de p solo se calculan sobre los
    eventos BINARIOS comparables, y se informa su cobertura por separado.
    """
    publicadas = len(evaluaciones)
    partidos = {e.id_partido or e.partido for e in evaluaciones}
    binarios = [e for e in evaluaciones if e.evaluable_binario]
    cumplidos = sum(1 for e in binarios if e.evento_ocurrio == 1)
    no_cumplidos = sum(1 for e in binarios if e.evento_ocurrio == 0)
    devoluciones = sum(1 for e in evaluaciones if e.estado_liquidacion == "devolucion")
    anuladas = sum(1 for e in evaluaciones if e.estado_liquidacion == "anulada")
    pendientes = sum(1 for e in evaluaciones
                     if e.estado_liquidacion in ("pendiente", "no_verificable"))
    sin_regla = sum(1 for e in evaluaciones if e.estado_liquidacion == "regla_no_definida")
    n = len(binarios)
    suma_p = sum(e.probabilidad_original or 0 for e in binarios)
    return {
        "publicadas": publicadas,
        "partidos_unicos": len(partidos),
        "evaluables": n,
        "ganadas": cumplidos,
        "perdidas": no_cumplidos,
        "devoluciones": devoluciones,
        "anuladas": anuladas,
        "pendientes_o_no_verificables": pendientes,
        "regla_no_definida": sin_regla,
        "acierto_observado": (cumplidos / n) if n else None,
        "probabilidad_media": (suma_p / n) if n else None,
        "aciertos_esperados": suma_p if n else None,
        "brecha_pp": (100 * (cumplidos / n - suma_p / n)) if n else None,
        "cobertura_verificacion": (n / publicadas) if publicadas else None,
    }


def calcular_bandas(evaluaciones: list[Evaluacion]) -> dict[str, dict[str, Any]]:
    """Bandas EXCLUYENTES. Cada evaluación cae en exactamente una."""
    salida: dict[str, dict[str, Any]] = {}
    for nombre, _inf, _sup in BANDAS:
        salida[nombre] = _stats_banda([e for e in evaluaciones if banda_de(e.probabilidad_original) == nombre])
    redondeadas = [e for e in evaluaciones if banda_de(e.probabilidad_original) == BANDA_REDONDEADA]
    if redondeadas:
        salida[BANDA_REDONDEADA] = _stats_banda(redondeadas)
    return salida


def calcular_acumulados(evaluaciones: list[Evaluacion]) -> dict[str, dict[str, Any]]:
    """
    Acumulados p>70%, >=85%, >=90% y >=95%. SE SUPERPONEN entre sí: no se suman.
    """
    salida: dict[str, dict[str, Any]] = {}
    for nombre, umbral in ACUMULADOS:
        if nombre == "p>70%":
            grupo = [e for e in evaluaciones
                     if e.probabilidad_original is not None and e.probabilidad_original > umbral]
        else:
            grupo = [e for e in evaluaciones
                     if e.probabilidad_original is not None and e.probabilidad_original >= umbral]
        salida[nombre] = _stats_banda(grupo)
    return salida


def _familia_de(mercado: str) -> str:
    """Familia de mercado, para el desglose."""
    m = (mercado or "").lower()
    if "total de goles" in m:
        return "Total de goles"
    if "córner" in m or "corner" in m or "esquina" in m:
        return "Córneres"
    if "tarjeta" in m:
        return "Tarjetas"
    if "doble oportunidad" in m:
        return "Doble oportunidad"
    if "ambos" in m and "anotan" in m:
        return "Ambos anotan"
    if "hándicap" in m or "handicap" in m:
        return "Hándicap"
    if "sin empate" in m:
        return "Sin empate"
    if "ganador del partido" in m and "+" in m:
        return "Combinadas resultado+goles"
    if "ganador" in m:
        return "Ganador"
    if "primer tiempo" in m or "1er tiempo" in m or "1t" in m:
        return "Primer tiempo"
    if "fiesta" in m:
        return "Fiesta de goles"
    return mercado or "Otras"


def calcular_por_familia(evaluaciones: list[Evaluacion]) -> dict[str, dict[str, Any]]:
    familias: dict[str, list[Evaluacion]] = {}
    for e in evaluaciones:
        familias.setdefault(_familia_de(e.mercado), []).append(e)
    return {k: _stats_banda(v) for k, v in sorted(familias.items())}


def calcular_por_periodo(evaluaciones: list[Evaluacion]) -> dict[str, dict[str, Any]]:
    periodos: dict[str, list[Evaluacion]] = {}
    for e in evaluaciones:
        periodos.setdefault(e.periodo or "FT", []).append(e)
    return {k: _stats_banda(v) for k, v in sorted(periodos.items())}


def calcular_por_sensibilidad(evaluaciones: list[Evaluacion]) -> dict[str, dict[str, Any]]:
    grupos: dict[str, list[Evaluacion]] = {}
    for e in evaluaciones:
        grupos.setdefault(e.estado_sensibilidad or "no_evaluada", []).append(e)
    return {k: _stats_banda(v) for k, v in sorted(grupos.items())}


# ─── Agrupación por EQUIPO ───────────────────────────────────────────────────

def _equipos_del_partido(e: Evaluacion) -> tuple[str, str]:
    """
    Devuelve (local, visitante) del partido de una evaluación a partir del campo
    «Equipo A vs Equipo B» que ya guarda cada evaluación.
    """
    if " vs " in (e.partido or ""):
        izq, der = e.partido.split(" vs ", 1)
        return izq.strip(), der.strip()
    return "", ""


def equipo_implicado(e: Evaluacion) -> str | None:
    """
    Equipo al que se refiere la selección, o None si es un mercado del partido.

    Fuentes, en orden: el campo `equipo` de la selección y el nombre del mercado
    («Arsenal - Total de goles»). No se adivina por el marcador.
    """
    if e.equipo:
        return str(e.equipo)
    local, visitante = _equipos_del_partido(e)
    for candidato in (local, visitante):
        if candidato and _clave_equipo(candidato) in _clave_equipo(e.mercado):
            return candidato
    if local and (e.evento.startswith("1_") or e.evento in ("1", "1x", "dnb_local")):
        return local
    if visitante and (e.evento.startswith("2_") or e.evento in ("2", "x2", "dnb_visitante")):
        return visitante
    return None


def condicion_de(e: Evaluacion) -> str:
    """«casa» / «fuera» / «partido» para la selección, según el bando del equipo."""
    equipo = equipo_implicado(e)
    if not equipo:
        return "partido"
    local, _visitante = _equipos_del_partido(e)
    if not local:
        return "partido"
    return "casa" if _mismo_equipo(equipo, local) else "fuera"


def calcular_por_equipo(evaluaciones: list[Evaluacion]) -> dict[str, dict[str, Any]]:
    """
    Acierto agrupado POR EQUIPO: todas las selecciones que hablan de un equipo,
    sumando sus partidos como local y como visitante.

    Es la vista que responde «¿con qué equipo falla el agente?». El mercado del
    partido (sin equipo) va a un grupo propio para que la suma de grupos cuadre.
    """
    grupos: dict[str, list[Evaluacion]] = {}
    for e in evaluaciones:
        grupos.setdefault(equipo_implicado(e) or "Partido (sin equipo)", []).append(e)
    salida = {}
    for nombre, grupo in grupos.items():
        d = _stats_banda(grupo)
        d["condicion"] = sorted({condicion_de(x) for x in grupo})
        d["partidos_unicos_local"] = len({x.id_partido or x.partido for x in grupo
                                          if condicion_de(x) == "casa"})
        d["partidos_unicos_fuera"] = len({x.id_partido or x.partido for x in grupo
                                          if condicion_de(x) == "fuera"})
        salida[nombre] = d
    return dict(sorted(salida.items(), key=lambda kv: -kv[1]["evaluables"]))


def calcular_por_equipo_y_condicion(evaluaciones: list[Evaluacion]) -> dict[str, dict[str, Any]]:
    """
    Desglose fino por equipo Y condición (casa / fuera / partido). Permite ver si el
    agente acierta peor cuando un equipo juega fuera, que es la hipótesis de
    localía que interesa comprobar.
    """
    grupos: dict[str, list[Evaluacion]] = {}
    for e in evaluaciones:
        nombre = equipo_implicado(e) or "Partido (sin equipo)"
        grupos.setdefault(f"{nombre} · {condicion_de(e)}", []).append(e)
    return {k: _stats_banda(v) for k, v in sorted(grupos.items())}


def calcular_por_localia(evaluaciones: list[Evaluacion]) -> dict[str, dict[str, Any]]:
    """Compara el acierto de las selecciones de equipos en casa frente a fuera."""
    grupos: dict[str, list[Evaluacion]] = {}
    for e in evaluaciones:
        grupos.setdefault(condicion_de(e), []).append(e)
    return {k: _stats_banda(v) for k, v in sorted(grupos.items())}


def agrupar_por_partido(evaluaciones: list[Evaluacion]) -> dict[str, list[Evaluacion]]:
    grupos: dict[str, list[Evaluacion]] = {}
    for e in evaluaciones:
        grupos.setdefault(e.id_partido or e.partido, []).append(e)
    return grupos


def promedio_equilibrado_por_partido(evaluaciones: list[Evaluacion]) -> dict[str, Any]:
    """
    Métrica calculada DENTRO de cada partido y promediada después, para que un
    encuentro con 200 líneas no domine sobre otro con 10. Sigue sin eliminar toda
    la redundancia de mercados: se identifica explícitamente como tal.
    """
    por_partido = agrupar_por_partido(evaluaciones)
    brechas, aciertos, probs = [], [], []
    for _pid, grupo in por_partido.items():
        binarios = [e for e in grupo if e.evaluable_binario]
        if not binarios:
            continue
        n = len(binarios)
        aciertos.append(sum(1 for e in binarios if e.evento_ocurrio == 1) / n)
        probs.append(sum(e.probabilidad_original or 0 for e in binarios) / n)
        brechas.append(aciertos[-1] - probs[-1])
    if not brechas:
        return {"partidos_con_evaluables": 0, "acierto_medio": None,
                "probabilidad_media": None, "brecha_pp": None}
    return {
        "partidos_con_evaluables": len(brechas),
        "acierto_medio": statistics.fmean(aciertos),
        "probabilidad_media": statistics.fmean(probs),
        "brecha_pp": 100 * statistics.fmean(brechas),
        "nota": "Promedio equilibrado por partido: cada partido pesa igual. "
                "No elimina la redundancia entre mercados correlacionados.",
    }


def brier_binario(evaluaciones: list[Evaluacion]) -> dict[str, Any]:
    """
    Brier = mean((p - y)^2) sobre objetivos binarios comparables con p exacta.
    Se informa la cobertura: no se inventa p para completar la métrica.
    """
    pares = [(e.probabilidad_original, e.evento_ocurrio) for e in evaluaciones
             if e.evaluable_binario and e.probabilidad_original is not None]
    if not pares:
        return {"n": 0, "brier": None, "nota": "sin datos"}
    total = sum((p - y) ** 2 for p, y in pares) / len(pares)
    base = sum(y for _p, y in pares) / len(pares)
    return {
        "n": len(pares),
        "brier": total,
        "brier_referencia_climatologia": base * (1 - base),
        "nota": "Menor es mejor, pero NO mide solo calibración: acompáñese de la tabla.",
    }


def tabla_calibracion(evaluaciones: list[Evaluacion]) -> list[dict[str, Any]]:
    """
    Probabilidad media anunciada frente a frecuencia observada, con muestra y
    partidos únicos. Es la tabla que responde «¿cuando digo 90%, ocurre 9 de 10?».
    """
    filas = []
    for nombre, _inf, _sup in BANDAS:
        grupo = [e for e in evaluaciones
                 if banda_de(e.probabilidad_original) == nombre and e.evaluable_binario]
        if not grupo:
            continue
        n = len(grupo)
        filas.append({
            "banda": nombre,
            "n": n,
            "partidos_unicos": len({e.id_partido or e.partido for e in grupo}),
            "probabilidad_media": sum(e.probabilidad_original or 0 for e in grupo) / n,
            "frecuencia_observada": sum(1 for e in grupo if e.evento_ocurrio == 1) / n,
            "brecha_pp": 100 * (sum(1 for e in grupo if e.evento_ocurrio == 1) / n
                                - sum(e.probabilidad_original or 0 for e in grupo) / n),
        })
    return filas


def grafico_calibracion_texto(filas: list[dict[str, Any]], ancho: int = 40) -> str:
    """Diagrama de fiabilidad en texto (sin dependencias gráficas)."""
    if not filas:
        return "sin datos para el gráfico"
    lineas = ["  anunciado -> observado            (n / partidos)",
              "  " + "-" * (ancho + 34)]
    for f in filas:
        p = f["probabilidad_media"]
        obs = f["frecuencia_observada"]
        x = max(0, min(ancho, int(round(p * ancho))))
        y = max(0, min(ancho, int(round(obs * ancho))))
        fila = [" "] * (ancho + 1)
        fila[x] = "P"
        fila[y] = "O" if fila[y] == " " else "X"
        lineas.append(f"  {p*100:5.1f}% -> {obs*100:5.1f}%  |{''.join(fila)}|  "
                      f"n={f['n']:<4} partidos={f['partidos_unicos']}")
    lineas.append("  " + "-" * (ancho + 34))
    lineas.append("  P = probabilidad anunciada · O = frecuencia observada · "
                  "X = coinciden")
    return "\n".join(lineas)


def desglose_bruto_vs_equilibrado(evaluaciones: list[Evaluacion]) -> dict[str, Any]:
    """Conteo bruto (mensajes) frente a partidos únicos, y promedio equilibrado."""
    binarios = [e for e in evaluaciones if e.evaluable_binario]
    return {
        "selecciones_brutas": len(binarios),
        "partidos_unicos": len({e.id_partido or e.partido for e in binarios}),
        "acierto_bruto": (sum(1 for e in binarios if e.evento_ocurrio == 1) / len(binarios)
                          if binarios else None),
        "equilibrado": promedio_equilibrado_por_partido(binarios),
        "aviso": "El conteo bruto responde «cuántas selecciones acertaron»; el "
                 "equilibrado evita que un partido con muchas líneas domine. "
                 "La incertidumbre respeta la agrupación por partido.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. AUDITORÍA DE LA BANDA 100%
# ═══════════════════════════════════════════════════════════════════════════════

def auditar_banda_100(evaluaciones: list[Evaluacion]) -> dict[str, Any]:
    """
    Audita las selecciones anunciadas con 100%. No modifica el valor histórico:
    determina si fue redondeo, error o consecuencia de una línea extrema.
    """
    grupo = [e for e in evaluaciones
             if e.probabilidad_original is not None and e.probabilidad_original >= 0.99995]
    if not grupo:
        return {"n": 0, "hallazgo": "no hay selecciones anunciadas al 100%"}
    exactos = [e for e in grupo if (e.probabilidad_original or 0) == 1.0]
    casi = [e for e in grupo if (e.probabilidad_original or 0) < 1.0]
    fallos = [e for e in grupo if e.estado_liquidacion == "perdida"]
    return {
        "n": len(grupo),
        "exactamente_1.0": len(exactos),
        "entre_0.99995_y_1": len(casi),
        "fallos": len(fallos),
        "ejemplos_linea_extrema": sorted({f"{e.etiqueta}" for e in grupo})[:8],
        "hallazgo": ("Valores exactos 1.0 en el registro: revisar si provienen de "
                     "probabilidad truncada en los extremos (fallo numérico) o de una "
                     "línea trivialmente cierta." if exactos else
                     "Los valores ~100% proceden de líneas extremas con probabilidad "
                     "casi unitaria, no de un 1.0 exacto."),
        "aviso": "«Acertó 10 de 10» describiría solo esta muestra; no demuestra certeza.",
    }


# Mínimo de partidos únicos para atreverse a hablar de calibración. Por debajo,
# las métricas son descriptivas: 800 líneas de 8 partidos NO son 800 ensayos.
MIN_PARTIDOS_PARA_CALIBRAR = 30


def diagnostico_evidencia(evaluaciones: list[Evaluacion]) -> dict[str, Any]:
    """
    Determina si hay evidencia suficiente para conclusiones de calibración.

    Un encuentro genera decenas de líneas correlacionadas. Tratar 822 selecciones
    de 8 partidos como 822 ensayos independientes inflaría la confianza de forma
    artificial, así que se exige un mínimo de PARTIDOS ÚNICOS.
    """
    binarios = [e for e in evaluaciones if e.evaluable_binario]
    partidos = {e.id_partido or e.partido for e in binarios}
    n_partidos = len(partidos)
    suficiente = n_partidos >= MIN_PARTIDOS_PARA_CALIBRAR
    return {
        "partidos_unicos": n_partidos,
        "selecciones_evaluables": len(binarios),
        "minimo_partidos_exigido": MIN_PARTIDOS_PARA_CALIBRAR,
        "evidencia_suficiente": suficiente,
        "mensaje": (
            f"Evidencia suficiente ({n_partidos} partidos únicos ≥ "
            f"{MIN_PARTIDOS_PARA_CALIBRAR})."
            if suficiente else
            f"EVIDENCIA INSUFICIENTE PARA UN INTERVALO ESTABLE: {n_partidos} partidos "
            f"únicos (mínimo {MIN_PARTIDOS_PARA_CALIBRAR}) para {len(binarios)} "
            f"selecciones evaluables. Las métricas son descriptivas de esta tanda y "
            f"no permiten concluir calibración: las líneas de un mismo partido están "
            f"correlacionadas y no son ensayos independientes."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ORQUESTACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class InformeEvaluacion:
    generado_en: str
    partidos: dict[str, Any]
    evaluaciones: list[Evaluacion]
    bandas: dict[str, dict[str, Any]]
    acumulados: dict[str, dict[str, Any]]
    por_familia: dict[str, dict[str, Any]]
    por_periodo: dict[str, dict[str, Any]]
    por_sensibilidad: dict[str, dict[str, Any]]
    por_equipo: dict[str, dict[str, Any]]
    por_equipo_condicion: dict[str, dict[str, Any]]
    por_localia: dict[str, dict[str, Any]]
    calibracion: list[dict[str, Any]]
    brier: dict[str, Any]
    bruto_vs_equilibrado: dict[str, Any]
    banda_100: dict[str, Any]
    evidencia: dict[str, Any]
    faltantes: list[str]
    limitaciones: list[str]
    abstenidos_por_cobertura: int = 0
    versiones: dict[str, Any] = field(default_factory=dict)
    filtro: str | None = None

    def a_dict(self) -> dict[str, Any]:
        return {
            "generado_en": self.generado_en,
            "partidos": self.partidos,
            "filtro": self.filtro,
            "bandas": self.bandas,
            "acumulados": self.acumulados,
            "por_familia": self.por_familia,
            "por_periodo": self.por_periodo,
            "por_sensibilidad": self.por_sensibilidad,
            "por_equipo": self.por_equipo,
            "por_equipo_condicion": self.por_equipo_condicion,
            "por_localia": self.por_localia,
            "calibracion": self.calibracion,
            "brier": self.brier,
            "bruto_vs_equilibrado": self.bruto_vs_equilibrado,
            "banda_100": self.banda_100,
            "evidencia": self.evidencia,
            "versiones": self.versiones,
            "faltantes": self.faltantes,
            "limitaciones": self.limitaciones,
            "evaluaciones": [e.a_dict() for e in self.evaluaciones],
        }


def _mismo_equipo(a: Any, b: Any) -> bool:
    """Compara dos nombres de equipo tolerando tildes, palabras vacías y sufijos."""
    ca, cb = _clave_equipo(a), _clave_equipo(b)
    if not ca or not cb:
        return False
    return (ca == cb
            or ca.startswith(cb) or cb.startswith(ca)
            or (len(ca) >= 4 and ca in cb) or (len(cb) >= 4 and cb in ca))


def normalizar_filtro_equipos(filtro: list[str] | tuple[str, ...] | str | None) -> list[str]:
    """Normaliza el filtro de equipos a una lista de nombres limpios."""
    if not filtro:
        return []
    if isinstance(filtro, (list, tuple)):
        salida: list[str] = []
        for elem in filtro:
            if not elem:
                continue
            partes = [p.strip() for p in re.split(r"\s+vs\.?\s+", str(elem), flags=re.I) if p.strip()]
            salida.extend(partes)
        return salida
    texto = str(filtro).strip()
    partes = [p.strip() for p in re.split(r"\s+vs\.?\s+", texto, flags=re.I) if p.strip()]
    return partes if partes else [texto]


def coincide_partido_filtro(local: str, visitante: str,
                            filtro: list[str] | tuple[str, ...] | str | None) -> bool:
    """Verifica si un enfrentamiento cumple con el filtro de equipos."""
    norm = normalizar_filtro_equipos(filtro)
    if not norm:
        return True
    if len(norm) >= 2:
        return ((_mismo_equipo(local, norm[0]) and _mismo_equipo(visitante, norm[1]))
                or (_mismo_equipo(local, norm[1]) and _mismo_equipo(visitante, norm[0])))
    return _mismo_equipo(local, norm[0]) or _mismo_equipo(visitante, norm[0])


def coincide_evaluacion_filtro(e: Evaluacion,
                               filtro: list[str] | tuple[str, ...] | str | None) -> bool:
    """
    Determina si una selección evaluada corresponde al filtro de equipos solicitado.
    - Si son 2 equipos (ej: Roma vs Real Madrid): incluye todas las selecciones del
      enfrentamiento directo entre ambos.
    - Si es 1 equipo (ej: Roma): incluye exclusivamente las selecciones de ese único equipo.
    """
    norm = normalizar_filtro_equipos(filtro)
    if not norm:
        return True
    local, visitante = _equipos_del_partido(e)
    eq_imp = equipo_implicado(e)

    if len(norm) >= 2:
        eq_a, eq_b = norm[0], norm[1]
        es_partido = ((_mismo_equipo(local, eq_a) and _mismo_equipo(visitante, eq_b))
                      or (_mismo_equipo(local, eq_b) and _mismo_equipo(visitante, eq_a)))
        return es_partido

    # Caso 1 solo equipo:
    eq_a = norm[0]
    en_partido = _mismo_equipo(local, eq_a) or _mismo_equipo(visitante, eq_a)
    if not en_partido:
        return False
    if eq_imp:
        return _mismo_equipo(eq_imp, eq_a)
    return False


def _orientar_resultado(r: ResultadoPartido, esperado_local: Any,
                        esperado_visitante: Any) -> ResultadoPartido:
    """
    Garantiza que `local`/`visitante` del resultado correspondan al MISMO bando que
    el pronóstico.

    La búsqueda del partido puede devolverlo desde la ficha de cualquiera de los dos
    equipos, y los bandos del pronóstico podrían estar invertidos respecto a la
    fuente. Si eso ocurre, se permutan TODOS los campos por bando para que la
    liquidación (que usa gl/gv como local/visitante del pronóstico) sea correcta.
    """
    if not r.local or not r.visitante:
        return r
    ya_correcto = (_mismo_equipo(r.local, esperado_local)
                   and _mismo_equipo(r.visitante, esperado_visitante))
    if ya_correcto:
        return r
    invertido = (_mismo_equipo(r.local, esperado_visitante)
                 and _mismo_equipo(r.visitante, esperado_local))
    if not invertido:
        r.notas.append(
            f"los equipos de la fuente ({r.local} vs {r.visitante}) no coinciden con los "
            f"del pronóstico ({esperado_local} vs {esperado_visitante}): no se orienta")
        return r

    r.local, r.visitante = r.visitante, r.local
    r.goles_local, r.goles_visitante = r.goles_visitante, r.goles_local
    r.goles_local_1t, r.goles_visitante_1t = r.goles_visitante_1t, r.goles_local_1t
    for campo in ("corners", "amarillas"):
        d = getattr(r, f"{campo}_local")
        d2 = getattr(r, f"{campo}_visitante")
        setattr(r, f"{campo}_local", d2)
        setattr(r, f"{campo}_visitante", d)
    r.rojas_local, r.rojas_visitante = r.rojas_visitante, r.rojas_local
    for g in r.goles:
        g["equipo"] = "visitante" if g.get("equipo") == "local" else "local"
        if isinstance(g.get("marcador_nuevo"), list) and len(g["marcador_nuevo"]) == 2:
            g["marcador_nuevo"] = [g["marcador_nuevo"][1], g["marcador_nuevo"][0]]
    r.notas.append("bandos invertidos respecto a la fuente: se permutaron local/visitante "
                   "para mantener la orientación del pronóstico")
    return r


def _esta_iniciado(fixture: dict[str, Any]) -> bool:
    """
    ¿El partido ya arrancó? Un partido futuro NO puede liquidar un pronóstico: al
    elegirlo se devolvía la vuelta sin jugar y el evento quedaba «sin liquidar»
    para siempre, aunque el partido pronosticado ya hubiera terminado.
    """
    st = fixture.get("status") or {}
    if st.get("started") is True or st.get("finished") is True:
        return True
    if st.get("started") is False and st.get("finished") is False:
        return False
    marca = _parsear_momento(st.get("utcTime"))
    if marca is None:
        return False
    return marca <= datetime.now(timezone.utc)


def _buscar_fixture(fuente: Any, equipo: str, rival: str,
                    fecha_objetivo: str | None,
                    generado_en: Any | None = None) -> dict[str, Any] | None:
    """
    Busca el enfrentamiento en la ficha de un equipo. Prioriza cruce por ID
    numérico además de por nombre, y coincidencia de fecha con tolerancia horaria.

    Solo se consideran partidos YA INICIADOS cuando existe alguno: un fixture
    futuro es el próximo encuentro, no el que se pronosticó.

    Si se conoce `generado_en`, se prefiere el PRIMER partido que aún no había
    empezado cuando se publicó el pronóstico: eso es lo que significa un snapshot
    (un pronóstico previo al partido). Sin esta comprobación, una fecha guardada
    equivocada —el último H2H ya jugado— liquidaba el partido viejo y descartaba
    todas las selecciones por «pronóstico posterior al inicio».
    """
    eq = fuente.resolver_equipo(equipo)
    if not eq:
        return None
    eq_rival = fuente.resolver_equipo(rival) if hasattr(fuente, "resolver_equipo") else None
    id_rival = str((eq_rival or {}).get("id") or "")
    nombres_rival = {_clave_equipo(rival)}
    if eq_rival:
        nombres_rival.add(_clave_equipo(str(eq_rival.get("nombre") or "")))
        for alt in eq_rival.get("alternativas") or []:
            nombres_rival.add(_clave_equipo(str(alt)))
    nombres_rival.discard("")

    datos = fuente._api(f"/teams?id={eq['id']}&ccode3=PER&lang=es")
    fixtures = (((datos or {}).get("fixtures") or {}).get("allFixtures") or {}).get("fixtures", [])
    candidatos = []
    for x in fixtures:
        hid = str((x.get("home") or {}).get("id") or "")
        aid = str((x.get("away") or {}).get("id") or "")
        h = str((x.get("home") or {}).get("name") or "")
        a = str((x.get("away") or {}).get("name") or "")

        # 1) Coincidencia por ID numérico (la más exacta e inmune a alias como "Man United")
        coincide_id = bool(id_rival and (hid == id_rival or aid == id_rival))

        # 2) Coincidencia por nombre / variantes
        ch, ca = _clave_equipo(h), _clave_equipo(a)
        coincide_nom = any(nr in (ch, ca) or nr in (ch + ca) or ch in nr or ca in nr for nr in nombres_rival)

        if coincide_id or coincide_nom:
            candidatos.append(x)

    if not candidatos:
        return None

    # Un partido que no ha empezado no puede haber producido un resultado. Si hay
    # alguno iniciado se trabaja solo con esos; si no lo hay, se conserva la lista
    # completa para poder declarar «encontrado pero sin liquidar».
    iniciados = [x for x in candidatos if _esta_iniciado(x)]
    evaluables = iniciados or candidatos

    # El snapshot es un pronóstico PREVIO al partido: si se sabe cuándo se publicó,
    # el partido pronosticado es el primero que aún no había arrancado entonces
    # (con 2 h de margen por si se publicó con el partido ya en juego).
    t_gen = _parsear_momento(generado_en) if generado_en else None
    if t_gen is not None and iniciados:
        posteriores = []
        for x in iniciados:
            t_x = _parsear_momento((x.get("status") or {}).get("utcTime"))
            if t_x is None:
                continue
            if (t_x - t_gen).total_seconds() >= -2 * 3600:
                posteriores.append((t_x, x))
        if posteriores:
            posteriores.sort(key=lambda par: par[0])
            return posteriores[0][1]

    if fecha_objetivo:
        f_obj = str(fecha_objetivo)[:10]
        # 1) Coincidencia exacta de fecha
        for x in evaluables:
            fecha = str(((x.get("status") or {}).get("utcTime")) or "")[:10]
            if fecha == f_obj:
                return x
        # 2) Tolerancia de +-1 día por diferencia horaria UTC vs America/Lima
        try:
            d_obj = datetime.strptime(f_obj, "%Y-%m-%d").date()
            for x in evaluables:
                f_x = str(((x.get("status") or {}).get("utcTime")) or "")[:10]
                if f_x:
                    d_x = datetime.strptime(f_x, "%Y-%m-%d").date()
                    if abs((d_x - d_obj).days) <= 1:
                        return x
        except Exception:
            pass

    # Ordenar por fecha descendente y devolver el más reciente ya iniciado
    evaluables = sorted(evaluables,
                        key=lambda x: str(((x.get("status") or {}).get("utcTime")) or ""),
                        reverse=True)
    if len(evaluables) > 1 and fecha_objetivo:
        return None      # ambiguo y la fecha no resolvió: no se elige a dedo
    return evaluables[0]


def _fecha_declarada_consistente(fecha_declarada: Any, fixture: dict[str, Any],
                                 generado_en: Any | None) -> tuple[bool, str]:
    """
    ¿La fecha guardada en el pronóstico cuadra con el partido encontrado?

    Se acepta en tres casos:
      1. Coincide (o difiere menos de un día por UTC vs America/Lima).
      2. Hay una fecha declarada pero el partido elegido es POSTERIOR a la
         publicación del pronóstico: la fecha guardada puede ser rancia (apuntaba
         al último H2H ya jugado) y no debe vetar el partido correcto.
      3. No hay fecha declarada: se acepta el partido y se declara la ausencia.
    Devuelve (consistente, motivo_del_rechazo).
    """
    status = fixture.get("status") or {}
    fecha_fixture = str(status.get("utcTime") or "")[:10]
    if not fecha_declarada or not fecha_fixture:
        return True, ""

    declarada = str(fecha_declarada)[:10]
    if declarada == fecha_fixture:
        return True, ""

    try:
        d_p = datetime.strptime(declarada, "%Y-%m-%d").date()
        d_f = datetime.strptime(fecha_fixture, "%Y-%m-%d").date()
        if abs((d_f - d_p).days) <= 1:
            return True, ""
    except Exception:
        return True, ""      # fecha ilegible: no se usa como veto

    if generado_en:
        t_gen = _parsear_momento(generado_en)
        t_fx = _parsear_momento(status.get("utcTime"))
        if t_gen is not None and t_fx is not None and t_fx >= t_gen:
            dias = (t_fx - t_gen).total_seconds() / 86400.0
            if dias <= 45:
                return True, ""
            return False, (f"el partido elegido es {dias:.0f} días posterior a la "
                           f"publicación del pronóstico: fuera de plazo razonable")

    return False, (f"el enfrentamiento hallado es del {fecha_fixture} y el "
                   f"pronóstico fijó {declarada}: fecha fuera de rango")


def verificar_resultados(pronosticos: Iterable[dict[str, Any]],
                         fuente: Any | None = None,
                         aviso: Any = None) -> dict[str, ResultadoPartido]:
    """
    Obtiene el resultado final de cada partido pronosticado. Ante ambigüedad o
    contradicción, deja el evento SIN VERIFICAR en lugar de elegir un marcador.
    """
    avisar = aviso or (lambda m: None)
    propios = fuente is None
    if propios:
        from fuentes_xg import obtener_fuente
        fuente = obtener_fuente("fotmob", headless=True, verbose=False)
        fuente.abrir()
    resultados: dict[str, ResultadoPartido] = {}
    try:
        for doc in pronosticos:
            equipos = doc.get("equipos") or {}
            local, visitante = equipos.get("local"), equipos.get("visitante")
            fecha = (doc.get("partido") or {}).get("fecha")
            clave = f"{local} vs {visitante}"
            if clave in resultados:
                continue
            if not local or not visitante:
                r = ResultadoPartido(estado="no_encontrado", fuente="fotmob",
                                     consultado_en=_ahora())
                r.notas.append("el pronóstico no identifica ambos equipos")
                resultados[clave] = r
                continue

            avisar(f"verificando resultado de {clave}…")
            fixture = _buscar_fixture(fuente, local, visitante, fecha,
                                      generado_en=doc.get("generado_en"))
            if not fixture:
                r = ResultadoPartido(estado="no_encontrado", local=local, visitante=visitante,
                                     fuente="fotmob", consultado_en=_ahora())
                r.notas.append("no se localizó un enfrentamiento inequívoco entre ambos equipos")
                resultados[clave] = r
                continue

            # ¿La fecha guardada cuadra con el partido hallado? Una fecha rancia
            # (el último H2H ya jugado) no debe vetar el partido correcto cuando
            # éste es posterior a la publicación del pronóstico.
            ok_fecha, motivo_fecha = _fecha_declarada_consistente(
                fecha, fixture, doc.get("generado_en"))
            if not ok_fecha:
                r = ResultadoPartido(estado="no_encontrado", local=local, visitante=visitante,
                                     fuente="fotmob", consultado_en=_ahora())
                r.notas.append(motivo_fecha)
                resultados[clave] = r
                continue

            detalle = fuente._api(f"/matchDetails?matchId={fixture.get('id')}")
            r = extraer_resultado(detalle or {}, fuente="fotmob")
            if not r.local:
                r.local, r.visitante = local, visitante
            # Los bandos deben coincidir con los del pronóstico, no con la posición
            # en que la fuente devolvió el partido.
            r = _orientar_resultado(r, local, visitante)
            resultados[clave] = r
            estado = r.estado if r.finalizado else f"{r.estado} (sin liquidar)"
            avisar(f"  {clave}: {estado} {r.goles_local}-{r.goles_visitante}")
    finally:
        if propios and fuente is not None:
            try:
                fuente.cerrar()
            except Exception:
                pass
    return resultados


def _parsear_momento(texto: Any) -> datetime | None:
    """
    Interpreta las marcas de tiempo del proyecto, que llegan en varios formatos:
        '2026-10-13 10:46:23'        (hora local, sin zona)
        '2026-10-13T19:00:00Z'       (UTC)
        '2026-10-13T19:00:00+00:00'
    Devuelve un datetime CON zona (UTC) o None.
    """
    if not texto:
        return None
    t = str(texto).strip().replace("T", " ")
    zona = timezone.utc
    if t.endswith("Z"):
        t = t[:-1]
    elif "+" in t[10:]:
        # Ya trae desplazamiento explícito
        try:
            return datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
        except Exception:
            t = t[:19]
    else:
        # Sin zona: el proyecto guarda hora local en America/Lima
        try:
            import pytz
            zona = pytz.timezone(config.TIMEZONE)
        except Exception:
            zona = timezone.utc
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            ingenuo = datetime.strptime(t[:19], fmt)
        except Exception:
            continue
        # Con pytz hay que usar localize(): `.replace(tzinfo=zona)` aplicaba el
        # huso LMT histórico (-05:08:36 en Lima) en vez de -05:00, desplazaba las
        # marcas ~8 min 36 s y hacía que un pronóstico emitido minutos ANTES del
        # inicio se juzgara posterior (todas sus selecciones pasaban a
        # «no verificable»). Con datetime.timezone (UTC) no hay localize().
        if hasattr(zona, "localize"):
            return zona.localize(ingenuo)
        return ingenuo.replace(tzinfo=zona)
    return None


def _comprobar_anterioridad(pronostico: dict[str, Any],
                            resultado: ResultadoPartido) -> tuple[bool, str]:
    """
    Comprueba que el pronóstico se emitió ANTES del inicio del partido.

    Ambas marcas se normalizan a UTC antes de comparar (la generación se guarda en
    hora local del proyecto y el inicio del partido viene en UTC). Si no se puede
    demostrar la anterioridad, se marca como no resoluble en lugar de asumir lo
    favorable al modelo.
    """
    generado = pronostico.get("generado_en")
    inicio = resultado.inicio_utc
    if not generado or not inicio:
        return False, ("no se puede demostrar que el pronóstico sea anterior al inicio "
                       "(falta marca de generación o de inicio)")
    t_gen = _parsear_momento(generado)
    t_ini = _parsear_momento(inicio)
    if t_gen is None or t_ini is None:
        return False, f"marcas de tiempo no interpretables ({generado!r} / {inicio!r})"
    if t_gen < t_ini:
        horas = (t_ini - t_gen).total_seconds() / 3600.0
        return True, (f"pronóstico {generado} ({t_gen.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC) "
                      f"anterior al inicio {inicio} por {horas:.1f} h")
    return False, (f"pronóstico {generado} NO anterior al inicio {inicio} "
                   f"(el pronóstico es posterior: no se evalúa como acierto)")


def _combinar_selecciones(ganadoras: list[dict[str, Any]]) -> Any:
    """
    Agrupa las selecciones ganadoras por id_trabajo, para evaluarlas una sola vez
    (la regla de versiones ya decidió cuál sobrevive por cada clave).
    """
    def generador():
        por_trabajo: dict[str, list[dict[str, Any]]] = {}
        for g in ganadoras:
            por_trabajo.setdefault(str(g["id_trabajo"]), []).append(g["seleccion"])
        for tid, sels in por_trabajo.items():
            yield tid, sels
    return generador


def evaluar_tanda(pronosticos: list[dict[str, Any]],
                  resultados: dict[str, ResultadoPartido] | None = None,
                  fuente: Any | None = None,
                  aviso: Any = None,
                  aplicar_regla_versiones: bool = True,
                  filtro_equipos: list[str] | tuple[str, ...] | str | None = None) -> InformeEvaluacion:
    """
    Evalúa todos los pronósticos dados contra sus resultados verificados.

    Si hay varias VERSIONES del mismo pronóstico (varios envíos del mismo partido),
    se aplica la regla fijada de antemano: una sola versión por
    partido/mercado/selección/línea/periodo. Así no se cuenta tres veces la misma
    evidencia ni se elige a posteriori la versión que acertó.
    """
    avisar = aviso or (lambda m: None)
    if resultados is None:
        resultados = verificar_resultados(pronosticos, fuente=fuente, aviso=avisar)

    # ── Resolución de versiones: selecciones que entran de verdad ──────────
    ganadoras: list[dict[str, Any]] = []
    info_versiones: dict[str, Any] = {"regla_aplicada": "sin_versiones",
                                      "duplicados_evitados": 0, "versiones": {}}
    if aplicar_regla_versiones:
        resolucion = resolver_versiones(pronosticos, regla="ultima_publicada")
        ganadoras = resolucion["selecciones_principales"]
        info_versiones = {
            "regla_aplicada": resolucion["regla_aplicada"],
            "duplicados_evitados": resolucion["duplicados_evitados"],
            "partidos_con_varias_versiones": resolucion["partidos_con_varias_versiones"],
            "versiones": resolucion["versiones"],
        }
        if info_versiones["duplicados_evitados"]:
            avisar(f"regla de versiones ({resolucion['regla_aplicada']}): "
                   f"{info_versiones['duplicados_evitados']} selecciones repetidas "
                   f"descartadas para no contar la misma evidencia dos veces")

    evaluaciones: list[Evaluacion] = []
    faltantes: list[str] = []
    limitaciones: list[str] = []

    por_trabajo: dict[str, list[dict[str, Any]]] = {}
    for g in ganadoras:
        por_trabajo.setdefault(str(g["id_trabajo"]), []).append(g["seleccion"])

    for doc in pronosticos:
        equipos = doc.get("equipos") or {}
        clave = f"{equipos.get('local')} vs {equipos.get('visitante')}"
        resultado = resultados.get(clave) or ResultadoPartido(
            estado="no_encontrado", local=equipos.get("local") or "",
            visitante=equipos.get("visitante") or "", consultado_en=_ahora())

        selecciones = (por_trabajo.get(str(doc.get("id_trabajo")), [])
                       if aplicar_regla_versiones
                       else (doc.get("selecciones_publicadas") or doc.get("selecciones") or []))

        for s in selecciones:
            sel = dict(s)
            sel["referencia"] = doc.get("referencia") or doc.get("_ruta") or ""
            ev = liquidar(sel, resultado, str(doc.get("id_trabajo")))
            # Anterioridad: si no se puede demostrar, no cuenta como evaluable
            if ev.estado_liquidacion in ("ganada", "perdida"):
                anterior, motivo = _comprobar_anterioridad(doc, resultado)
                if not anterior:
                    ev = Evaluacion(**{**ev.__dict__, "estado_liquidacion": "no_verificable",
                                       "evento_ocurrio": None,
                                       "objetivo_probabilidad": "no_evaluable",
                                       "detalle": f"{ev.detalle} | {motivo}"})
                    if motivo not in limitaciones:
                        limitaciones.append(f"{clave}: {motivo}")
            evaluaciones.append(ev)

        if doc.get("origen") == "importado_historico":
            for lim in doc.get("limitaciones") or []:
                if lim not in limitaciones:
                    limitaciones.append(lim)
        if not resultado.finalizado and str(doc.get("id_trabajo")) in por_trabajo:
            faltantes.append(f"{clave}: resultado {resultado.estado}")

    publicadas = [e for e in evaluaciones]
    for _clave_p, _r in resultados.items():
        for _disc in getattr(_r, "discrepancias", []) or []:
            limitaciones.append(f"[{_clave_p}] {_disc}")

    norm_filtro = normalizar_filtro_equipos(filtro_equipos)
    if norm_filtro:
        publicadas = [e for e in publicadas if coincide_evaluacion_filtro(e, norm_filtro)]

    texto_filtro = " vs ".join(norm_filtro) if norm_filtro else None

    informe = InformeEvaluacion(
        generado_en=_ahora(),
        partidos={k: v.a_dict() for k, v in resultados.items()},
        evaluaciones=publicadas,
        bandas=calcular_bandas(publicadas),
        acumulados=calcular_acumulados(publicadas),
        por_familia=calcular_por_familia(publicadas),
        por_periodo=calcular_por_periodo(publicadas),
        por_sensibilidad=calcular_por_sensibilidad(publicadas),
        por_equipo=calcular_por_equipo(publicadas),
        por_equipo_condicion=calcular_por_equipo_y_condicion(publicadas),
        por_localia=calcular_por_localia(publicadas),
        calibracion=tabla_calibracion(publicadas),
        brier=brier_binario(publicadas),
        bruto_vs_equilibrado=desglose_bruto_vs_equilibrado(publicadas),
        banda_100=auditar_banda_100(publicadas),
        evidencia=diagnostico_evidencia(publicadas),
        faltantes=faltantes,
        limitaciones=limitaciones,
        abstenidos_por_cobertura=sum(1 for d in pronosticos if d.get("abstenido")),
        versiones=info_versiones,
        filtro=texto_filtro,
    )
    avisar(f"evaluación completada: {len(publicadas)} selecciones publicadas evaluadas"
           + (f" (filtro: {texto_filtro})" if texto_filtro else ""))
    return informe


def pct(valor: float | None, decimales: int = 1) -> str:
    """Formatea un porcentaje, distinguiendo «sin datos» de 0%."""
    if valor is None:
        return "sin datos"
    return f"{valor*100:.{decimales}f}%".replace(".", ",")


def pp(valor: float | None, decimales: int = 1) -> str:
    """Formatea una brecha en puntos porcentuales con coma decimal."""
    if valor is None:
        return "sin datos"
    return f"{valor:+.{decimales}f}".replace(".", ",")


def resumen_telegram(informe: InformeEvaluacion) -> str:
    """Resumen para el chat: incluye fallos y tasas inferiores al umbral."""
    ev = informe.evaluaciones
    binarios = [e for e in ev if e.evaluable_binario]
    partidos = {e.id_partido or e.partido for e in ev}
    filtro_txt = getattr(informe, "filtro", None)
    L: list[str] = []
    if filtro_txt:
        L.append(f"AUDITORÍA DE PRONÓSTICOS — {filtro_txt.upper()}")
    else:
        L.append("AUDITORÍA DE PRONÓSTICOS")
    L.append(f"Partidos únicos: {len(partidos)}")
    L.append(f"Selecciones originales >70% publicadas: {len(ev)}")
    L.append(f"Evaluables (binarias comparables): {len(binarios)}")
    pendientes = sum(1 for e in ev if e.estado_liquidacion in ("pendiente", "no_verificable"))
    devoluciones = sum(1 for e in ev if e.estado_liquidacion == "devolucion")
    sin_regla = sum(1 for e in ev if e.estado_liquidacion == "regla_no_definida")
    L.append(f"Pendientes / no verificables: {pendientes}")
    L.append(f"Eventos cumplidos: {sum(1 for e in binarios if e.evento_ocurrio == 1)}; "
             f"no cumplidos: {sum(1 for e in binarios if e.evento_ocurrio == 0)}")
    L.append(f"Devoluciones: {devoluciones} (no cuentan como victoria) | "
             f"regla no definida: {sin_regla}")
    L.append("")
    L.append("Probabilidad original | Evaluables | Aciertos | Acierto real | Prob. media | Brecha")
    for nombre, _i, _s in BANDAS:
        d = informe.bandas.get(nombre)
        if not d or not d["publicadas"]:
            continue
        L.append(f"{nombre} | {d['evaluables']} | {d['ganadas']} | "
                 f"{pct(d['acierto_observado'])} | {pct(d['probabilidad_media'])} | "
                 f"{('%+.1f' % d['brecha_pp']).replace('.', ',') if d['brecha_pp'] is not None else 'sin datos'} pp")
    L.append("")
    L.append("Acumulados (se SUPERPONEN, no se suman):")
    for nombre, d in informe.acumulados.items():
        L.append(f"  {nombre}: {d['evaluables']} evaluables, "
                 f"acierto real {pct(d['acierto_observado'])}, "
                 f"prob. media {pct(d['probabilidad_media'])}")
    L.append("")
    L.append("Las tablas completas van como imagen adjunta. RESUMEN por equipo:")
    min_eval = 1 if filtro_txt else 10
    peores = [(k, v) for k, v in informe.por_equipo.items()
              if v["evaluables"] >= min_eval and v["brecha_pp"] is not None]
    peores.sort(key=lambda kv: kv[1]["brecha_pp"])
    if len(peores) == 1:
        nombre, d = peores[0]
        L.append(f"  {nombre} ({'/'.join(d.get('condicion') or [])}) "
                 f"{pct(d['probabilidad_media'])} anunciado -> "
                 f"{pct(d['acierto_observado'])} real ({pp(d['brecha_pp'])} pp)")
    else:
        for nombre, d in peores[:3]:
            L.append(f"  peor: {nombre} ({'/'.join(d.get('condicion') or [])}) "
                     f"{pct(d['probabilidad_media'])} anunciado -> "
                     f"{pct(d['acierto_observado'])} real ({pp(d['brecha_pp'])} pp)")
        for nombre, d in peores[-2:]:
            if (nombre, d) not in peores[:3]:
                L.append(f"  mejor: {nombre} ({'/'.join(d.get('condicion') or [])}) "
                         f"{pct(d['probabilidad_media'])} anunciado -> "
                         f"{pct(d['acierto_observado'])} real ({pp(d['brecha_pp'])} pp)")
    L.append("")
    L.append("Resumen por mercado (top por muestra):")
    fam = sorted(informe.por_familia.items(),
                 key=lambda kv: -kv[1]["evaluables"])[:5]
    for nombre, d in fam:
        if not d["evaluables"]:
            continue
        L.append(f"  {nombre}: {d['evaluables']} evaluables, "
                 f"acierto {pct(d['acierto_observado'])}, "
                 f"prob. media {pct(d['probabilidad_media'])}, brecha "
                 f"{('%+.1f' % d['brecha_pp']).replace('.', ',')} pp")
    # Mayor discrepancia descriptiva (con muestra mínima)
    con_muestra = [(k, v) for k, v in informe.por_familia.items() if v["evaluables"] >= 10]
    if con_muestra:
        peor = min(con_muestra, key=lambda kv: kv[1]["brecha_pp"] or 0)
        L.append("")
        L.append(f"Mayor discrepancia por mercado: {peor[0]} "
                 f"({peor[1]['evaluables']} evaluables, brecha "
                 f"{('%+.1f' % peor[1]['brecha_pp']).replace('.', ',')} pp). "
                 f"Muestra pequeña: descriptivo, no concluyente.")
    # Hipótesis de localía: casa frente a fuera
    casa = informe.por_localia.get("casa")
    fuera = informe.por_localia.get("fuera")
    if (casa and fuera and casa.get("brecha_pp") is not None
            and fuera.get("brecha_pp") is not None):
        L.append("")
        L.append(f"Localía: en casa brecha {('%+.1f' % casa['brecha_pp']).replace('.', ',')} pp "
                 f"({casa['evaluables']} eval.); fuera "
                 f"{('%+.1f' % fuera['brecha_pp']).replace('.', ',')} pp "
                 f"({fuera['evaluables']} eval.).")
        if fuera["brecha_pp"] < casa["brecha_pp"] - 2:
            L.append("Hipótesis a comprobar (NO conclusión): el modelo parece más "
                     "sobreconfiado con los equipos visitantes. Con esta muestra no se "
                     "puede separar el efecto de la localía del azar ni del número de goles.")
    # Peor equipo con muestra mínima
    peores_equipos = [(k, v) for k, v in informe.por_equipo.items()
                      if v["evaluables"] >= 15 and v["brecha_pp"] is not None]
    if peores_equipos:
        peor_eq = min(peores_equipos, key=lambda kv: kv[1]["brecha_pp"])
        L.append("")
        L.append(f"Equipo con mayor brecha: {peor_eq[0]} "
                 f"({peor_eq[1]['evaluables']} evaluables, "
                 f"{('%+.1f' % peor_eq[1]['brecha_pp']).replace('.', ',')} pp). "
                 f"Un equipo con ~30 líneas de 1-2 partidos: no es un diagnóstico sobre "
                 f"ese equipo.")
    L.append("")
    L.append(f"Brier (n={informe.brier.get('n')}): "
             f"{informe.brier.get('brier') if informe.brier.get('brier') is not None else 'sin datos'}")
    L.append(f"Partidos únicos con eventos evaluables: {informe.evidencia['partidos_unicos']} "
             f"para {informe.evidencia['selecciones_evaluables']} selecciones")
    L.append(informe.evidencia["mensaje"])
    L.append(f"Estado del aprendizaje: solo_observacion")
    if informe.versiones and informe.versiones.get("duplicados_evitados"):
        L.append("")
        L.append(f"Versiones: regla «{informe.versiones['regla_aplicada']}» en "
                 f"{informe.versiones.get('partidos_con_varias_versiones')} partido(s) con "
                 f"varias emisiones; {informe.versiones['duplicados_evitados']} selecciones "
                 f"repetidas descartadas para no contar la misma evidencia dos veces.")
    if informe.faltantes:
        L.append("")
        L.append("Sin verificar: " + "; ".join(informe.faltantes[:4]))
    L.append("")
    L.append("Limitaciones: las devoluciones se superponen con eventos no cumplidos "
             "cuando el objetivo era P(victoria efectiva); no son sumandos "
             "independientes. Muestra pequeña: sin conclusiones firmes.")
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. INFORME COMPLETO Y REGISTRO IDEMPOTENTE DE EJECUCIONES
# ═══════════════════════════════════════════════════════════════════════════════

REGISTRO_EVALUACIONES = os.path.join(EVALUACION_DIR, "registro_evaluaciones.json")


def _leer_registro(ruta: str | None = None) -> dict[str, Any]:
    destino = ruta or REGISTRO_EVALUACIONES
    if not os.path.exists(destino):
        return {"evaluaciones": {}}
    try:
        with open(destino, "r", encoding="utf-8") as f:
            datos = json.load(f)
        datos.setdefault("evaluaciones", {})
        return datos
    except Exception:
        return {"evaluaciones": {}}


def _escribir_registro(datos: dict[str, Any], ruta: str | None = None) -> None:
    destino = ruta or REGISTRO_EVALUACIONES
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)


def identificador_ejecucion(informe: InformeEvaluacion,
                            huella_resultados: str = "") -> str:
    """
    Identificador determinista de la ejecución: partido/tanda + versión de los
    resultados + versión del modelo. Evita reenviar el mismo resumen.
    """
    versiones = sorted({e.probabilidad_origen for e in informe.evaluaciones})
    base = json.dumps({
        "partidos": sorted(informe.partidos.keys()),
        "evaluables": len(informe.evaluaciones),
        "huella_resultados": huella_resultados,
        "versiones": versiones,
        "filtro": getattr(informe, "filtro", None),
    }, ensure_ascii=False, sort_keys=True)
    import hashlib
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def huella_de_resultados(resultados: dict[str, ResultadoPartido]) -> str:
    """Huella de los resultados verificados: cambia si aparece una corrección."""
    import hashlib
    base = json.dumps({k: {"estado": v.estado, "gl": v.goles_local, "gv": v.goles_visitante,
                           "1t": [v.goles_local_1t, v.goles_visitante_1t]}
                       for k, v in sorted(resultados.items())},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def generar_informe_texto(informe: InformeEvaluacion) -> str:
    """Informe técnico completo (no se envía a Telegram: se guarda en disco)."""
    L: list[str] = []
    L.append("# AUDITORÍA DE PRONÓSTICOS Y CALIBRACIÓN")
    L.append("")
    L.append(f"Generado: {informe.generado_en}")
    L.append("")
    L.append("## 1. Resultados verificados")
    L.append("")
    L.append("| Partido | Estado | Marcador | 1T | Córneres L/V | Amarillas L/V | Fuente |")
    L.append("|---|---|---|---|---|---|---|")
    for clave, d in informe.partidos.items():
        g = d.get("goles") or {}
        c = d.get("corners") or {}
        a = d.get("amarillas") or {}
        L.append(f"| {clave} | {d.get('estado')} | {g.get('local')}-{g.get('visitante')} | "
                 f"{g.get('local_1t')}-{g.get('visitante_1t')} | "
                 f"{(c.get('local') or {}).get('FT')}/{(c.get('visitante') or {}).get('FT')} | "
                 f"{(a.get('local') or {}).get('FT')}/{(a.get('visitante') or {}).get('FT')} | "
                 f"{d.get('fuente')} |")
    L.append("")
    L.append("## 2. Acierto por banda de probabilidad original")
    L.append("")
    L.append("| Banda | Publicadas | Evaluables | Partidos | Ganadas | Perdidas | "
             "Devueltas | Pendientes | Acierto real | Prob. media | Brecha (pp) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for nombre, _i, _s in BANDAS:
        d = informe.bandas.get(nombre)
        if not d or not d["publicadas"]:
            continue
        L.append(f"| {nombre} | {d['publicadas']} | {d['evaluables']} | "
                 f"{d['partidos_unicos']} | {d['ganadas']} | {d['perdidas']} | "
                 f"{d['devoluciones']} | {d['pendientes_o_no_verificables']} | "
                 f"{pct(d['acierto_observado'])} | {pct(d['probabilidad_media'])} | "
                 f"{pp(d['brecha_pp'])} |" if d["brecha_pp"] is not None else
                 f"| {nombre} | {d['publicadas']} | 0 | - | - | - | - | "
                 f"{d['pendientes_o_no_verificables']} | sin datos | sin datos | sin datos |")
    L.append("")
    L.append("## 3. Acumulados (se superponen: no se suman)")
    L.append("")
    L.append("| Acumulado | Evaluables | Acierto real | Prob. media | Brecha (pp) |")
    L.append("|---|---|---|---|---|")
    for nombre, d in informe.acumulados.items():
        L.append(f"| {nombre} | {d['evaluables']} | {pct(d['acierto_observado'])} | "
                 f"{pct(d['probabilidad_media'])} | "
                 f"{pp(d['brecha_pp'])} |" if d["brecha_pp"] is not None else
                 f"| {nombre} | 0 | sin datos | sin datos | sin datos |")
    L.append("")
    L.append("## 4. Desglose por familia de mercado")
    L.append("")
    L.append("| Familia | Evaluables | Partidos | Acierto real | Prob. media | Brecha (pp) |")
    L.append("|---|---|---|---|---|---|")
    for nombre, d in sorted(informe.por_familia.items(), key=lambda kv: -kv[1]["evaluables"]):
        L.append(f"| {nombre} | {d['evaluables']} | {d['partidos_unicos']} | "
                 f"{pct(d['acierto_observado'])} | {pct(d['probabilidad_media'])} | "
                 f"{pp(d['brecha_pp'])} |" if d["brecha_pp"] is not None else
                 f"| {nombre} | 0 | 0 | sin datos | sin datos | sin datos |")
    L.append("")
    L.append("## 4.1 Desglose por EQUIPO")
    L.append("")
    L.append("Todas las selecciones que hablan de un equipo, sumando sus partidos como "
             "local y como visitante. Los mercados del partido (sin equipo) van aparte.")
    L.append("")
    L.append("| Equipo | Condición | Prob. media | Evaluables | Aciertos | Acierto real | "
             "Brecha (pp) | Partidos | Pendientes |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for nombre, d in informe.por_equipo.items():
        if not d["publicadas"]:
            continue
        brecha = (pp(d['brecha_pp']) if d["brecha_pp"] is not None else "sin datos")
        L.append(f"| {nombre} | {'/'.join(d.get('condicion') or [])} | "
                 f"{pct(d['probabilidad_media'])} | {d['evaluables']} | {d['ganadas']} | "
                 f"{pct(d['acierto_observado'])} | {brecha} | "
                 f"{d['partidos_unicos']} | {d['pendientes_o_no_verificables']} |")
    L.append("")
    L.append("## 4.2 Desglose por EQUIPO y CONDICIÓN")
    L.append("")
    L.append("| Equipo · condición | Prob. media | Evaluables | Aciertos | Acierto real | "
             "Brecha (pp) |")
    L.append("|---|---|---|---|---|---|")
    for nombre, d in informe.por_equipo_condicion.items():
        if not d["evaluables"]:
            continue
        L.append(f"| {nombre} | {pct(d['probabilidad_media'])} | {d['evaluables']} | "
                 f"{d['ganadas']} | {pct(d['acierto_observado'])} | "
                 f"{d['brecha_pp']:+.1f} |")
    L.append("")
    L.append("## 4.3 Localía (casa frente a fuera)")
    L.append("")
    L.append("| Condición | Prob. media | Evaluables | Aciertos | Acierto real | Brecha (pp) |")
    L.append("|---|---|---|---|---|---|")
    for nombre, d in informe.por_localia.items():
        L.append(f"| {nombre} | {pct(d['probabilidad_media'])} | {d['evaluables']} | "
                 f"{d['ganadas']} | {pct(d['acierto_observado'])} | "
                 f"{pp(d['brecha_pp'])} |" if d["brecha_pp"] is not None else
                 f"| {nombre} | sin datos | 0 | 0 | sin datos | sin datos |")
    L.append("")
    L.append("## 5. Desglose por periodo y por sensibilidad")
    L.append("")
    for titulo, grupo in (("Periodo", informe.por_periodo),
                          ("Sensibilidad", informe.por_sensibilidad)):
        L.append(f"**{titulo}**")
        L.append("")
        for nombre, d in grupo.items():
            L.append(f"- {nombre}: {d['evaluables']} evaluables, "
                     f"acierto {pct(d['acierto_observado'])}, "
                     f"prob. media {pct(d['probabilidad_media'])}")
        L.append("")
    L.append("## 6. Tabla de calibración")
    L.append("")
    L.append("```")
    L.append(grafico_calibracion_texto(informe.calibracion))
    L.append("```")
    L.append("")
    L.append(f"Brier binario: {informe.brier.get('brier')} (n={informe.brier.get('n')}). "
             f"Referencia de climatología: {informe.brier.get('brier_referencia_climatologia')}.")
    L.append("")
    L.append("## 7. Conteo bruto frente a promedio equilibrado por partido")
    L.append("")
    bq = informe.bruto_vs_equilibrado
    eq = bq["equilibrado"]
    L.append(f"- Selecciones brutas evaluables: {bq['selecciones_brutas']}")
    L.append(f"- Partidos únicos: {bq['partidos_unicos']}")
    L.append(f"- Acierto bruto (por selección): {pct(bq['acierto_bruto'])}")
    L.append(f"- Acierto equilibrado (media de partidos): {pct(eq.get('acierto_medio'))}")
    L.append(f"- Brecha equilibrada: {eq.get('brecha_pp'):+.1f} pp"
             if eq.get("brecha_pp") is not None else "- Sin datos")
    L.append(f"- {bq['aviso']}")
    L.append("")
    L.append("## 8. Auditoría de la banda 100%")
    L.append("")
    L.append(f"- Selecciones anunciadas al ~100%: {informe.banda_100.get('n')}")
    L.append(f"- Exactamente 1,0: {informe.banda_100.get('exactamente_1.0')}")
    L.append(f"- Fallos en esa banda: {informe.banda_100.get('fallos')}")
    L.append(f"- Hallazgo: {informe.banda_100.get('hallazgo')}")
    L.append("")
    L.append("## 9. Evidencia y limitaciones")
    L.append("")
    L.append(f"- {informe.evidencia['mensaje']}")
    if informe.versiones:
        v = informe.versiones
        L.append("")
        L.append("### 9.1 Versiones del mismo pronóstico")
        L.append("")
        L.append(f"- Regla aplicada (fijada antes de ver resultados): **{v.get('regla_aplicada')}**")
        L.append(f"- Partidos con varias emisiones: {v.get('partidos_con_varias_versiones')}")
        L.append(f"- Selecciones repetidas descartadas: {v.get('duplicados_evitados')}")
        L.append("")
        for clave_p, d in (v.get("versiones") or {}).items():
            if len(d.get("emisiones") or []) <= 1:
                continue
            L.append(f"- **{clave_p}**: {len(d['emisiones'])} emisiones, "
                     f"{d['selecciones_emitidas_en_total']} selecciones emitidas en total, "
                     f"{d['selecciones_unicas']} únicas. Versión usada: "
                     f"`{d.get('version_usada')}` (primera: `{d.get('primera_emision')}`, "
                     f"última: `{d.get('ultima_emision')}`)")
            for em in d["emisiones"]:
                L.append(f"  - `{em['id_trabajo']}` generado {em['generado_en']} "
                         f"({em['selecciones_publicadas']} publicadas)")
        L.append("")
    if informe.faltantes:
        L.append("- Sin verificar:")
        for f in informe.faltantes:
            L.append(f"  - {f}")
    if informe.limitaciones:
        L.append("- Limitaciones de trazabilidad:")
        for lim in informe.limitaciones:
            L.append(f"  - {lim}")
    L.append("")
    return "\n".join(L)


def guardar_informe(informe: InformeEvaluacion, nombre: str | None = None,
                    directorio: str | None = None) -> dict[str, str]:
    """Escribe el informe completo en .md y .json. Devuelve las rutas."""
    raiz = directorio or EVALUACION_DIR
    os.makedirs(raiz, exist_ok=True)
    base = nombre or f"evaluacion_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
    ruta_md = os.path.join(raiz, f"{base}.md")
    ruta_json = os.path.join(raiz, f"{base}.json")
    with open(ruta_md, "w", encoding="utf-8") as f:
        f.write(generar_informe_texto(informe))
    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(informe.a_dict(), f, ensure_ascii=False, indent=1)
    return {"informe_md": ruta_md, "informe_json": ruta_json}


def preparar_evaluacion(pronosticos: list[dict[str, Any]] | None = None,
                        fuente: Any | None = None,
                        aviso: Any = None,
                        enviar_media_fn: Any = None,
                        filtro_equipos: list[str] | tuple[str, ...] | str | None = None) -> dict[str, Any]:
    """
    Prepara la evaluación sin enviarla. Admite filtro opcional por equipos
    (ej: ['Roma', 'Real Madrid'] o ['Roma']). Estados separados:
        evaluacion_completada → resumen_preparado → (envío lo confirma el servicio)

    Genera además las imágenes de las tablas (por equipo y por bandas), que se
    envían como adjunto para que las métricas se lean bien en el móvil.
    """
    avisar = aviso or (lambda m: None)
    norm_filtro = normalizar_filtro_equipos(filtro_equipos)
    texto_filtro = " vs ".join(norm_filtro) if norm_filtro else ""

    docs = pronosticos if pronosticos is not None else cargar_o_importar_pronosticos()
    if norm_filtro:
        docs = [d for d in docs
                if coincide_partido_filtro(
                    str((d.get("equipos") or {}).get("local") or ""),
                    str((d.get("equipos") or {}).get("visitante") or ""),
                    norm_filtro)]

    # Si el filtro no tiene documentos históricos de partidos
    if norm_filtro and not docs:
        msg_vacio = f"⚠️ No hay datos liquidados para evaluar a {texto_filtro}."
        avisar(msg_vacio)
        return {
            "estado": "sin_datos_filtro",
            "sin_datos_filtro": True,
            "id_ejecucion": f"vacio_{_slug(texto_filtro)}",
            "huella_resultados": "",
            "informe": None,
            "mensaje": msg_vacio,
            "imagenes": [],
            "tablas_texto": {"equipos": msg_vacio, "bandas": msg_vacio},
            "enviar_media_fn": enviar_media_fn,
            "rutas": {},
            "filtro": texto_filtro,
        }

    resultados = verificar_resultados(docs, fuente=fuente, aviso=avisar)
    informe = evaluar_tanda(docs, resultados=resultados, aviso=avisar,
                            filtro_equipos=norm_filtro)

    # Manejo de vacíos: si el filtro no arrojó selecciones evaluables finalizadas
    if norm_filtro and (not informe.evaluaciones or informe.evidencia.get("selecciones_evaluables", 0) == 0):
        msg_vacio = f"⚠️ No hay datos liquidados para evaluar a {texto_filtro}."
        avisar(msg_vacio)
        huella = huella_de_resultados(resultados)
        id_ejecucion = identificador_ejecucion(informe, huella)
        return {
            "estado": "sin_datos_filtro",
            "sin_datos_filtro": True,
            "id_ejecucion": id_ejecucion,
            "huella_resultados": huella,
            "informe": informe,
            "mensaje": msg_vacio,
            "imagenes": [],
            "tablas_texto": {"equipos": msg_vacio, "bandas": msg_vacio},
            "enviar_media_fn": enviar_media_fn,
            "rutas": {},
            "filtro": texto_filtro,
        }

    huella = huella_de_resultados(resultados)
    id_ejecucion = identificador_ejecucion(informe, huella)
    rutas = guardar_informe(informe, nombre=f"evaluacion_{id_ejecucion}")
    try:
        from banco_historico import guardar_en_banco_no_bloqueante
        guardar_en_banco_no_bloqueante(
            resultados_evaluacion=list((resultados or {}).values())
        )
    except Exception as _e_banco:
        print(f"[BANCO HISTORICO] ⚠️ Hook /evaluar omitido: {_e_banco}")
    if not norm_filtro:
        try:
            from agente_consciente import obtener_agente_consciente
            obtener_agente_consciente().refrescar_desde_evaluacion()
        except Exception as e_sync:
            print(f"[EVALUACIÓN] ⚠️ No se pudo sincronizar conciencia en vivo: {e_sync}")
    avisar("generando las tablas en imagen…")
    imagenes = generar_imagenes_informe(informe)
    if imagenes:
        avisar(f"  {len(imagenes)} imagen(es) de tabla generadas")

    # Respaldo en texto: siempre disponible en el informe guardado
    col_eq, fil_eq = datos_tabla_equipos(informe)
    col_bd, fil_bd = datos_tabla_bandas(informe)

    return {
        "estado": "resumen_preparado",
        "sin_datos_filtro": False,
        "id_ejecucion": id_ejecucion,
        "huella_resultados": huella,
        "informe": informe,
        "mensaje": resumen_telegram(informe),
        "imagenes": imagenes,
        "tablas_texto": {
            "equipos": tabla_ascii_minimalista(col_eq, fil_eq),
            "bandas": tabla_ascii_minimalista(col_bd, fil_bd),
        },
        "enviar_media_fn": enviar_media_fn,
        "rutas": rutas,
        "filtro": texto_filtro,
    }


def estado_envio_previo(id_ejecucion: str, ruta: str | None = None) -> str | None:
    """Devuelve el estado registrado de una ejecución, o None si es nueva."""
    datos = _leer_registro(ruta)
    entrada = datos["evaluaciones"].get(id_ejecucion)
    return entrada.get("estado") if entrada else None


def marcar_estado(id_ejecucion: str, estado: str, extra: dict[str, Any] | None = None,
                  ruta: str | None = None) -> None:
    """Registra el estado de la ejecución, conservando el historial."""
    datos = _leer_registro(ruta)
    entrada = datos["evaluaciones"].setdefault(id_ejecucion, {"historial": []})
    entrada["estado"] = estado
    entrada["actualizado_en"] = _ahora()
    entrada["historial"].append({"estado": estado, "en": entrada["actualizado_en"],
                                 **(extra or {})})
    _escribir_registro(datos, ruta)


def enviar_resumen(preparado: dict[str, Any], enviar_fn: Any,
                   chat_id: int | str | None = None,
                   nueva_ejecucion: bool = False,
                   ruta_registro: str | None = None,
                   imagenes: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """
    Envía el resumen al chat configurado con idempotencia.

    Distingue «evaluación completada» (hecho antes de llamar aquí),
    «resumen preparado» y «envío confirmado». Un envío ya confirmado para la misma
    ejecución NO se repite salvo que se pida explícitamente una nueva ejecución
    (una petición nueva del usuario no es un reintento técnico).

    Las tablas se envían como IMAGEN cuando hay un adaptador de fotos disponible;
    si no, el texto del resumen ya incluye el respaldo legible.
    """
    if enviar_fn is None:
        return {"enviado": False, "estado": "sin_adaptador",
                "motivo": "falta configuración de envío (no hay adaptador disponible)"}
    if preparado.get("sin_datos_filtro"):
        try:
            resultado = enviar_fn(preparado["mensaje"], chat_id)
        except Exception as e:
            return {"enviado": False, "estado": "envio_fallido",
                    "motivo": f"{type(e).__name__}: {e}",
                    "id_ejecucion": preparado.get("id_ejecucion", "vacio")}
        return {"enviado": True, "estado": "sin_datos_filtro",
                "motivo": preparado["mensaje"],
                "message_id": (resultado or {}).get("message_id") if isinstance(resultado, dict) else None,
                "id_ejecucion": preparado.get("id_ejecucion", "vacio"),
                "imagenes_enviadas": 0}
    id_ejecucion = preparado["id_ejecucion"]
    previo = estado_envio_previo(id_ejecucion, ruta_registro)
    if previo == "envio_confirmado" and not nueva_ejecucion:
        return {"enviado": False, "estado": "ya_confirmado",
                "motivo": f"la ejecución {id_ejecucion} ya se envió y confirmó",
                "id_ejecucion": id_ejecucion}

    marcar_estado(id_ejecucion, "resumen_preparado",
                  {"rutas": preparado.get("rutas")}, ruta_registro)
    try:
        resultado = enviar_fn(preparado["mensaje"], chat_id)
    except Exception as e:
        marcar_estado(id_ejecucion, "envio_fallido",
                      {"error": f"{type(e).__name__}: {e}"}, ruta_registro)
        return {"enviado": False, "estado": "envio_fallido",
                "motivo": f"{type(e).__name__}: {e}", "id_ejecucion": id_ejecucion}
    if not isinstance(resultado, dict) or not resultado.get("ok"):
        marcar_estado(id_ejecucion, "envio_fallido",
                      {"respuesta": resultado}, ruta_registro)
        return {"enviado": False, "estado": "envio_fallido",
                "motivo": (resultado or {}).get("error", "respuesta sin ok"),
                "id_ejecucion": id_ejecucion}

    # Imágenes de las tablas (no bloquean la confirmación del texto)
    ids_imagenes: list[Any] = []
    fallos_imagen: list[str] = []
    enviar_media = preparado.get("enviar_media_fn")
    if (imagenes or preparado.get("imagenes")) and enviar_media is None:
        # Sin adaptador se intenta el real, para no perder el adjunto en silencio
        try:
            from servicio_mercados import enviar_fn_telegram_foto as enviar_media
        except Exception:
            enviar_media = None
            fallos_imagen.append("no hay adaptador de imágenes disponible")
    for img in (imagenes if imagenes is not None else (preparado.get("imagenes") or [])):
        ruta = img.get("ruta")
        if not ruta or not os.path.exists(ruta):
            continue
        if enviar_media is None:
            break
        try:
            r = enviar_media(ruta, img.get("titulo") or "", chat_id)
            if isinstance(r, dict) and r.get("ok"):
                ids_imagenes.append(r.get("message_id"))
            else:
                fallos_imagen.append(f"{os.path.basename(ruta)}: "
                                     f"{(r or {}).get('error', 'sin ok')}")
        except Exception as e_img:
            fallos_imagen.append(f"{os.path.basename(ruta)}: {type(e_img).__name__}: {e_img}")
    for f in fallos_imagen:
        print(f"[EVALUACIÓN] ⚠️ imagen no enviada: {f}")

    marcar_estado(id_ejecucion, "envio_confirmado",
                  {"message_id": resultado.get("message_id"),
                   "ids_imagenes": ids_imagenes,
                   "fallos_imagen": fallos_imagen,
                   "rutas": preparado.get("rutas")}, ruta_registro)
    return {"enviado": True, "estado": "envio_confirmado",
            "message_id": resultado.get("message_id"),
            "imagenes_enviadas": len(ids_imagenes),
            "fallos_imagen": fallos_imagen,
            "id_ejecucion": id_ejecucion}


# ═══════════════════════════════════════════════════════════════════════════════
# 9. CATÁLOGO DE ESTRUCTURAS DE LIQUIDACIÓN (comparabilidad entre versiones)
# ═══════════════════════════════════════════════════════════════════════════════

# Estructuras con un ÚNICO evento binario incondicional: son las comparables en
# calibración y Brier. Las condicionadas o con devolución se liquidan pero se
# apartan de las métricas numéricas de p.
LIQUIDACIONES_ESTRUCTURA: dict[str, str] = {
    "over_": "binario", "under_": "binario", "exacto_": "multicategoria",
    "al_menos_": "binario",
    "corners_over_": "binario", "corners_under_": "binario",
    "amarillas_over_": "binario", "amarillas_under_": "binario",
    "rojas_over_": "binario",
    "btts_si": "binario", "btts_no": "binario",
    "1": "multicategoria", "X": "multicategoria", "2": "multicategoria",
    "1x": "binario", "x2": "binario", "12": "binario",
    "dnb_local": "victoria_efectiva", "dnb_visitante": "victoria_efectiva",
    "dnb_devolucion": "devolucion",
    "gol_1t": "binario", "gol_2t": "binario", "gol_ambos_tiempos": "binario",
    "fiesta_al_menos_": "binario",
}


def _sin_prefijo_equipo(evento: str) -> str:
    """
    Quita el prefijo de equipo que añade el motor («local_» / «visitante_») para
    poder clasificar la estructura del evento.
    """
    for prefijo in ("local_", "visitante_"):
        if evento.startswith(prefijo):
            return evento[len(prefijo):]
    return evento


def estructura_de(evento: str) -> str:
    """
    Clasifica la estructura probabilística de un evento.

    El orden importa: las CONJUNCIONES y los mercados compuestos se comprueban
    antes que los prefijos cortos, porque «1_btts_si» empieza por «1» (1X2) pero
    no es una categoría del 1X2 sino una combinación de dos condiciones. El
    prefijo de equipo se elimina antes de clasificar.
    Un choque devuelve «otra» para no forzar una métrica que no corresponde al
    objetivo original del pronóstico.
    """
    ev = _sin_prefijo_equipo(evento or "")
    # 1) Compuestos y condicionados, ANTES que los prefijos cortos
    if "_btts_" in ev or "_mas_" in ev or "_menos_" in ev:
        return "conjuncion_simple"
    if ev.startswith("h") and ("_local" in ev or "_empate" in ev or "_visitante" in ev):
        return "multicategoria"
    # 2) Coincidencia exacta primero (evita que «1» capture a «12»)
    if ev in LIQUIDACIONES_ESTRUCTURA:
        return LIQUIDACIONES_ESTRUCTURA[ev]
    # 3) Prefijos, del más largo al más corto
    for clave in sorted(LIQUIDACIONES_ESTRUCTURA, key=len, reverse=True):
        if ev.startswith(clave):
            return LIQUIDACIONES_ESTRUCTURA[clave]
    return "otra"


def catalogo_eventos(evaluaciones: list[Evaluacion]) -> list[str]:
    """
    Catálogo FIJO de eventos evaluables (mercado|periodo|evento). Se usa para
    comparar versiones sobre exactamente los mismos eventos: así una versión no
    puede mejorar su porcentaje añadiendo líneas nuevas ni quitando fallos.
    """
    return sorted({f"{e.mercado}|{e.periodo}|{e.evento}" for e in evaluaciones})


def resumen_estructuras(evaluaciones: list[Evaluacion]) -> dict[str, dict[str, Any]]:
    """Desglose por estructura probabilística, señalando qué entra en Brier."""
    grupos: dict[str, list[Evaluacion]] = {}
    for e in evaluaciones:
        grupos.setdefault(estructura_de(e.evento), []).append(e)
    salida = {}
    for nombre, grupo in sorted(grupos.items()):
        binarios = [e for e in grupo if e.evaluable_binario]
        salida[nombre] = {
            "total": len(grupo),
            "entra_en_brier": len(binarios),
            "acierto_binario": (sum(1 for e in binarios if e.evento_ocurrio == 1) / len(binarios)
                                if binarios else None),
        }
    return salida


# ═══════════════════════════════════════════════════════════════════════════════
# 11. RENDERIZADO VISUAL DE TABLAS (PNG) PARA TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

# Se usa Pillow, que YA está instalado en el proyecto. No requiere pandas,
# matplotlib, tabulate ni dataframe_image: así no hay que instalar nada nuevo.
TABLAS_DIR = os.path.join(EVALUACION_DIR, "tablas")

_RUTAS_FUENTES = (
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    (r"C:\Windows\Fonts\calibri.ttf", r"C:\Windows\Fonts\calibrib.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    (r"C:\Windows\Fonts\verdana.ttf", r"C:\Windows\Fonts\verdanab.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)


def _cargar_fuentes(tamano: int = 22):
    """Devuelve (fuente_normal, fuente_negrita). Cae a la de PIL si no hay TTF."""
    from PIL import ImageFont
    for normal, negrita in _RUTAS_FUENTES:
        try:
            return ImageFont.truetype(normal, tamano), ImageFont.truetype(negrita, tamano)
        except Exception:
            continue
    fuente = ImageFont.load_default()
    return fuente, fuente


def _ancho_texto(dibujo: Any, texto: str, fuente: Any) -> int:
    try:
        caja = dibujo.textbbox((0, 0), texto, font=fuente)
        return caja[2] - caja[0]
    except Exception:
        return len(texto) * 8


def renderizar_tabla_png(titulo: str, columnas: list[str], filas: list[list[str]],
                         subtitulo: str | None = None,
                         columnas_numericas: tuple[int, ...] = (),
                         destino: str | None = None,
                         resaltar_negativos: tuple[int, ...] = ()) -> str:
    """
    Dibuja una tabla legible en móvil y la guarda como PNG. Devuelve la ruta.

    `columnas_numericas` alinea a la derecha esas columnas.
    `resaltar_negativos` pinta en rojo los valores que empiecen por «-» o «−».
    """
    from PIL import Image, ImageDraw

    fuente, fuente_negrita = _cargar_fuentes(21)
    fuente_titulo, fuente_titulo_b = _cargar_fuentes(30)
    fuente_sub, _ = _cargar_fuentes(18)

    pad_x, pad_y = 22, 13
    margen = 26
    alto_fila = 40

    provisional = Image.new("RGB", (10, 10))
    dib = ImageDraw.Draw(provisional)

    # Ancho de cada columna: el mayor entre su cabecera y sus celdas
    anchos = []
    for i, col in enumerate(columnas):
        ancho = _ancho_texto(dib, str(col), fuente_negrita)
        for fila in filas:
            if i < len(fila):
                ancho = max(ancho, _ancho_texto(dib, str(fila[i]), fuente))
        anchos.append(ancho + pad_x * 2)

    ancho_tabla = sum(anchos)
    ancho = max(ancho_tabla + margen * 2,
                _ancho_texto(dib, titulo, fuente_titulo_b) + margen * 2)
    if subtitulo:
        ancho = max(ancho, _ancho_texto(dib, subtitulo, fuente_sub) + margen * 2)

    alto_titulo = 52 if subtitulo else 40
    alto = margen * 2 + alto_titulo + (len(filas) + 1) * alto_fila

    img = Image.new("RGB", (ancho, alto), (255, 255, 255))
    d = ImageDraw.Draw(img)

    # Título
    d.text((margen, margen - 4), titulo, font=fuente_titulo_b, fill=(17, 24, 39))
    if subtitulo:
        d.text((margen, margen + 30), subtitulo, font=fuente_sub, fill=(107, 114, 128))

    x0, y0 = margen, margen + alto_titulo
    # Cabecera
    d.rectangle([x0, y0, x0 + ancho_tabla, y0 + alto_fila], fill=(31, 41, 55))
    x = x0
    for i, col in enumerate(columnas):
        alineado = "right" if i in columnas_numericas else "left"
        tx = x + anchos[i] - pad_x if alineado == "right" else x + pad_x
        if alineado == "right":
            tx -= _ancho_texto(d, str(col), fuente_negrita)
        d.text((tx, y0 + (alto_fila - 26) // 2), str(col), font=fuente_negrita,
               fill=(255, 255, 255))
        x += anchos[i]

    # Filas
    for f, fila in enumerate(filas):
        y = y0 + alto_fila * (f + 1)
        if f % 2 == 0:
            d.rectangle([x0, y, x0 + ancho_tabla, y + alto_fila], fill=(243, 244, 246))
        x = x0
        for i, col in enumerate(columnas):
            valor = str(fila[i]) if i < len(fila) else ""
            alineado = "right" if i in columnas_numericas else "left"
            color = (17, 24, 39)
            if i in resaltar_negativos and valor.strip().startswith(("-", "−")):
                color = (185, 28, 28)
            elif i in resaltar_negativos and valor.strip().startswith("+"):
                color = (21, 128, 61)
            tx = x + pad_x
            if alineado == "right":
                tx = x + anchos[i] - pad_x - _ancho_texto(d, valor, fuente)
            d.text((tx, y + (alto_fila - 26) // 2), valor, font=fuente, fill=color)
            x += anchos[i]

    # Borde inferior
    d.line([x0, y0 + alto_fila * (len(filas) + 1), x0 + ancho_tabla,
            y0 + alto_fila * (len(filas) + 1)], fill=(209, 213, 219), width=2)

    if destino is None:
        os.makedirs(TABLAS_DIR, exist_ok=True)
        destino = os.path.join(TABLAS_DIR, f"tabla_{_slug(titulo)}.png")
    else:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
    img.save(destino, "PNG", optimize=True)
    return destino


def datos_tabla_equipos(informe: InformeEvaluacion) -> tuple[list[str], list[list[str]]]:
    """
    Columnas y filas de la tabla por equipo.

    Se incluyen los equipos con evaluables; los que solo tienen selecciones
    pendientes (partido no jugado o no verificado) se resumen en una nota, porque
    mostrarlos como «sin datos» solo añade ruido a la imagen.
    """
    columnas = ["Equipo", "Cond.", "Prob. media", "Evaluables", "Aciertos",
                "Acierto real", "Brecha"]
    filas = []
    pendientes: list[str] = []
    for nombre, d in informe.por_equipo.items():
        if not d["publicadas"]:
            continue
        if not d["evaluables"]:
            pendientes.append(str(nombre))
            continue
        filas.append([
            str(nombre),
            "/".join(d.get("condicion") or []),
            pct(d["probabilidad_media"]),
            str(d["evaluables"]),
            str(d["ganadas"]),
            pct(d["acierto_observado"]),
            f"{pp(d['brecha_pp'])} pp" if d["brecha_pp"] is not None else "sin datos",
        ])
    if pendientes:
        filas.append(["Sin liquidar: " + ", ".join(pendientes), "", "", "", "", "", ""])
    return columnas, filas


def datos_tabla_bandas(informe: InformeEvaluacion) -> tuple[list[str], list[list[str]]]:
    """Columnas y filas de la tabla por banda de probabilidad."""
    columnas = ["Prob. original", "Evaluables", "Aciertos", "Acierto real",
                "Prob. media", "Brecha"]
    filas = []
    for nombre, _i, _s in BANDAS:
        d = informe.bandas.get(nombre)
        if not d or not d["publicadas"]:
            continue
        filas.append([
            nombre,
            str(d["evaluables"]),
            str(d["ganadas"]),
            pct(d["acierto_observado"]),
            pct(d["probabilidad_media"]),
            f"{pp(d['brecha_pp'])} pp" if d["brecha_pp"] is not None else "sin datos",
        ])
    return columnas, filas


def generar_imagenes_informe(informe: InformeEvaluacion,
                             directorio: str | None = None) -> list[dict[str, str]]:
    """
    Genera las imágenes de las dos tablas principales. Devuelve una lista de
    {ruta, titulo}. Si PIL no está disponible, devuelve lista vacía y el flujo
    continúa con el texto (nunca falla por no poder dibujar).
    """
    raiz = directorio or TABLAS_DIR
    salidas: list[dict[str, str]] = []
    filtro_txt = getattr(informe, "filtro", None)
    if filtro_txt:
        subtitulo = (f"Filtro: {filtro_txt} · {informe.evidencia['partidos_unicos']} partido(s) · "
                     f"{informe.evidencia['selecciones_evaluables']} selecciones evaluables")
        sufijo = f"_{_slug(filtro_txt)}"
    else:
        subtitulo = (f"{informe.evidencia['partidos_unicos']} partidos únicos · "
                     f"{informe.evidencia['selecciones_evaluables']} selecciones evaluables")
        sufijo = ""
    try:
        col_eq, fil_eq = datos_tabla_equipos(informe)
        if fil_eq:
            ruta = renderizar_tabla_png(
                "Acierto por equipo", col_eq, fil_eq, subtitulo=subtitulo,
                columnas_numericas=(2, 3, 4, 5, 6), resaltar_negativos=(6,),
                destino=os.path.join(raiz, f"tabla_equipos{sufijo}.png"))
            salidas.append({"ruta": ruta, "titulo": "Acierto por equipo"})
        col_bd, fil_bd = datos_tabla_bandas(informe)
        if fil_bd:
            ruta = renderizar_tabla_png(
                "Acierto por banda de probabilidad", col_bd, fil_bd, subtitulo=subtitulo,
                columnas_numericas=(1, 2, 3, 4, 5), resaltar_negativos=(5,),
                destino=os.path.join(raiz, f"tabla_bandas{sufijo}.png"))
            salidas.append({"ruta": ruta, "titulo": "Acierto por banda"})
    except Exception as e:
        print(f"[EVALUACIÓN] ⚠️ no se pudieron generar las imágenes de tabla: "
              f"{type(e).__name__}: {e}")
        return []
    return salidas


def _enviar_imagen(bot_inst: Any, destino: Any, ruta: str, caption: str = "") -> bool:
    """Envía una imagen a Telegram en texto plano de caption (sin Markdown)."""
    original = getattr(bot_inst, "parse_mode", None)
    try:
        bot_inst.parse_mode = None
        bot_inst.send_photo(destino, open(ruta, "rb"), caption=caption or None)
        return True
    except Exception as e:
        print(f"[EVALUACIÓN] ⚠️ no se pudo enviar la imagen {os.path.basename(ruta)}: {e}")
        return False
    finally:
        bot_inst.parse_mode = original


def validar_corte_prepartido_sin_fuga(
    generado_en: str | None,
    inicio_utc: str | None,
    partidos_historicos: list[dict[str, Any]] | None = None,
    h2h: list[dict[str, Any]] | None = None,
    fecha_objetivo: str | None = None,
    id_partido_objetivo: str | int | None = None,
) -> dict[str, Any]:
    """
    Verifica estrictamente (regla operativa, sin validación cronológica de umbrales)
    que el pronóstico fue congelado antes del inicio del partido y que ningún partido
    de fecha >= fecha_objetivo o con el mismo id_partido se filtró en las ventanas históricas/H2H.
    """
    t_gen = _parsear_momento(generado_en)
    t_ini = _parsear_momento(inicio_utc)
    motivos: list[str] = []
    margen_h: float | None = None
    pre_kickoff_ok = False
    if t_gen is None or t_ini is None:
        motivos.append("Falta timestamp verificable de generación o inicio UTC.")
    else:
        margen_h = round((t_ini - t_gen).total_seconds() / 3600.0, 4)
        pre_kickoff_ok = t_gen < t_ini
        if not pre_kickoff_ok:
            motivos.append(
                f"Corte posterior o simultáneo al inicio: generado={t_gen.isoformat()} >= inicio={t_ini.isoformat()}"
            )

    fecha_ref = str(fecha_objetivo or (t_ini.strftime("%Y-%m-%d") if t_ini else "") or "")[:10]
    id_obj = str(id_partido_objetivo) if id_partido_objetivo is not None else None
    fugas: list[str] = []

    for origen, lista in (("historico", partidos_historicos or []), ("h2h", h2h or [])):
        for idx, p in enumerate(lista):
            if not isinstance(p, dict):
                continue
            p_id = str(p.get("id_partido") or p.get("id") or "")
            p_fecha = str(p.get("fecha") or "")[:10]
            if id_obj and p_id and p_id == id_obj:
                fugas.append(f"{origen}[{idx}] coincide con id_partido objetivo ({p_id})")
            if fecha_ref and p_fecha and len(p_fecha) == 10 and p_fecha >= fecha_ref:
                fugas.append(f"{origen}[{idx}] fecha={p_fecha} >= objetivo={fecha_ref}")

    if fugas:
        motivos.extend(fugas)

    return {
        "ok": pre_kickoff_ok and not fugas,
        "pre_kickoff_verificado": pre_kickoff_ok,
        "fuga_historica": bool(fugas),
        "t_generado_utc": t_gen.isoformat() if t_gen else None,
        "t_inicio_utc": t_ini.isoformat() if t_ini else None,
        "margen_horas": margen_h,
        "fecha_objetivo": fecha_ref or None,
        "fugas_detalle": fugas,
        "motivos": motivos,
    }


def liquidar_todas_lineas_fixture(
    analisis: dict[str, Any],
    resultado: ResultadoPartido,
    id_trabajo: str = "FIXTURE",
) -> dict[str, Any]:
    """
    Toma el resultado de `motor_mercados.analizar(...)` (y/o documento de pronóstico)
    y liquida TODAS sus líneas publicadas (top5, ticket competitivo y selecciones publicadas),
    sin omitir líneas y sin inventar datos faltantes.
    """
    vistas: dict[str, dict[str, Any]] = {}
    fuentes_sel: list[tuple[str, list[dict[str, Any]]]] = [
        ("top5", list(analisis.get("top5") or [])),
        ("ticket", list(analisis.get("opciones_ticket") or analisis.get("ticket") or [])),
        ("publicadas", list(analisis.get("selecciones_publicadas") or [])),
    ]
    # Incluir 1X2/DC/valor explícitos si vienen aparte
    for k_extra in ("valor_1x2_dc", "selecciones_filtradas", "selecciones"):
        extra = analisis.get(k_extra)
        if isinstance(extra, list):
            fuentes_sel.append((k_extra, extra))

    for origen_grp, lista in fuentes_sel:
        for s in lista:
            if not isinstance(s, dict) or not s.get("evento"):
                continue
            prob_raw = s.get("probabilidad_calibrada")
            if prob_raw is None:
                prob_raw = s.get("probabilidad")
            if prob_raw is None and isinstance(s.get("probabilidad_pct"), (int, float)):
                prob_raw = float(s["probabilidad_pct"]) / 100.0
            # Si viene de selecciones_filtradas sin filtro de ticket, incluir solo >=70% salvo ya en ticket/top5
            if origen_grp in ("selecciones_filtradas", "selecciones"):
                if isinstance(prob_raw, (int, float)) and float(prob_raw) < 0.70 and not s.get("publicada"):
                    continue
            sel_norm = dict(s)
            if isinstance(prob_raw, (int, float)):
                sel_norm["probabilidad"] = float(prob_raw)
            if not sel_norm.get("mercado"):
                sel_norm["mercado"] = sel_norm.get("categoria") or "Mercado"
            if not sel_norm.get("etiqueta"):
                sel_norm["etiqueta"] = sel_norm.get("nombre") or sel_norm["evento"]
            k = clave_seleccion(
                str(sel_norm.get("mercado") or ""),
                str(sel_norm.get("evento") or ""),
                str(sel_norm.get("periodo") or "FT"),
                sel_norm.get("equipo"),
                sel_norm.get("linea"),
            )
            if k not in vistas:
                sel_norm["_origen_grupo"] = origen_grp
                vistas[k] = sel_norm
            else:
                grp_prev = str(vistas[k].get("_origen_grupo") or "")
                if origen_grp not in grp_prev.split(","):
                    vistas[k]["_origen_grupo"] = f"{grp_prev},{origen_grp}" if grp_prev else origen_grp

    filas: list[dict[str, Any]] = []
    evals: list[Evaluacion] = []
    for _k, s in vistas.items():
        ev = liquidar(s, resultado, id_trabajo=id_trabajo)
        evals.append(ev)
        filas.append({
            "grupo": s.get("_origen_grupo", ""),
            "linea": ev.etiqueta or ev.evento,
            "mercado": ev.mercado,
            "evento": ev.evento,
            "periodo": ev.periodo,
            "probabilidad_publicada": ev.probabilidad_original,
            "probabilidad_pct": round(ev.probabilidad_original * 100.0, 2) if isinstance(ev.probabilidad_original, (int, float)) else None,
            "resultado_real": ev.detalle,
            "estado_liquidacion": ev.estado_liquidacion,
            "evento_ocurrio": ev.evento_ocurrio,
        })

    n_gan = sum(1 for e in evals if e.estado_liquidacion == "ganada")
    n_per = sum(1 for e in evals if e.estado_liquidacion == "perdida")
    n_dev = sum(1 for e in evals if e.estado_liquidacion == "devolucion")
    n_nv = sum(1 for e in evals if e.estado_liquidacion == "no_verificable")
    n_regla = sum(1 for e in evals if e.estado_liquidacion == "regla_no_definida")
    n_eval = n_gan + n_per
    brier = brier_binario(evals)
    return {
        "partido": f"{resultado.local} vs {resultado.visitante}",
        "id_partido": resultado.id_partido,
        "marcador_ft": [resultado.goles_local, resultado.goles_visitante],
        "marcador_1t": [resultado.goles_local_1t, resultado.goles_visitante_1t],
        "lineas": filas,
        "evaluaciones": [e.a_dict() for e in evals],
        "discrepancias_scraper": list(getattr(resultado, "discrepancias", []) or []),
        "resumen": {
            "n_publicadas": len(evals),
            "n_evaluables": n_eval,
            "aciertos": n_gan,
            "fallos": n_per,
            "tasa_acierto": round(n_gan / n_eval, 4) if n_eval else None,
            "devoluciones": n_dev,
            "no_verificables": n_nv,
            "regla_no_definida": n_regla,
            "brier": brier.get("brier"),
        },
    }


IDS_PARTIDOS_DISENO = frozenset({"5181796", "5181797"})


def _norm_txt_eval(valor: Any) -> str:
    txt = unicodedata.normalize("NFKD", str(valor or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return " ".join(txt.lower().split())


def es_partido_de_diseno(partido: Any = None, id_partido: Any = None) -> bool:
    """Identifica los dos partidos usados para diseño (Turkey-Belgium e Italy-France)."""
    pid = str(id_partido or "").strip()
    if pid in IDS_PARTIDOS_DISENO:
        return True
    p_norm = _norm_txt_eval(partido)
    if not p_norm:
        return False
    es_tb = ("turkey" in p_norm or "turquia" in p_norm) and ("belgium" in p_norm or "belgica" in p_norm)
    es_if = ("italy" in p_norm or "italia" in p_norm) and ("france" in p_norm or "francia" in p_norm)
    return bool(es_tb or es_if)


def log_loss_binario_valor(p: float, y: int, eps: float = 1e-6) -> float:
    """Log-loss binario acotado en [eps, 1 - eps]."""
    p_clip = min(1.0 - eps, max(eps, float(p)))
    y_i = int(y)
    return -(math.log(p_clip) if y_i == 1 else math.log(1.0 - p_clip))


def bootstrap_brier_logloss_por_partido(
    observaciones: list[tuple[str, int, dict[str, float]]],
    n_boot: int = 2000,
    seed: int = 20261114,
) -> dict[str, Any]:
    """
    Bootstrap agrupado por `id_partido` (bloque = partido único) sobre observaciones
    emparejadas `(match_id, y_binario, {version: prob})`.
    """
    por_partido: dict[str, list[tuple[int, dict[str, float]]]] = {}
    versiones: list[str] = []
    for mid, y, probs in observaciones:
        if not mid or probs is None:
            continue
        if not versiones:
            versiones = list(probs.keys())
        por_partido.setdefault(str(mid), []).append((int(y), probs))

    mids = sorted(por_partido.keys())
    n_partidos = len(mids)
    n_filas = sum(len(v) for v in por_partido.values())
    if n_partidos == 0 or not versiones:
        return {
            "n_partidos_unicos": 0,
            "n_filas": 0,
            "suficiente_n30": False,
            "nota_muestra": f"Sin datos evaluables (< MIN_PARTIDOS_PARA_CALIBRAR={MIN_PARTIDOS_PARA_CALIBRAR}).",
            "versiones": {},
            "diferencias": {},
        }

    # Precomputo de sumas por partido para bootstrap rápido y determinista
    sumas_brier: dict[str, dict[str, float]] = {v: {} for v in versiones}
    sumas_ll: dict[str, dict[str, float]] = {v: {} for v in versiones}
    conteos_m: dict[str, int] = {}
    for mid in mids:
        items = por_partido[mid]
        conteos_m[mid] = len(items)
        for v in versiones:
            sumas_brier[v][mid] = sum((float(pr[v]) - y) ** 2 for y, pr in items)
            sumas_ll[v][mid] = sum(log_loss_binario_valor(float(pr[v]), y) for y, pr in items)

    res_ver: dict[str, dict[str, Any]] = {}
    for v in versiones:
        b_punt = sum(sumas_brier[v].values()) / n_filas
        ll_punt = sum(sumas_ll[v].values()) / n_filas
        res_ver[v] = {
            "brier": round(b_punt, 6),
            "log_loss": round(ll_punt, 6),
            "brier_ic95": None,
            "log_loss_ic95": None,
        }

    pares_diff: list[tuple[str, str]] = []
    for idx_i, va in enumerate(versiones):
        for vb in versiones[idx_i + 1 :]:
            pares_diff.append((vb, va))  # vb - va (< 0 favorece a vb)

    if n_partidos >= 2 and n_boot >= 10:
        rng = random.Random(seed)
        boots_b: dict[str, list[float]] = {v: [] for v in versiones}
        boots_ll: dict[str, list[float]] = {v: [] for v in versiones}
        for _ in range(n_boot):
            muestra = rng.choices(mids, k=n_partidos)
            tot_n = sum(conteos_m[m] for m in muestra)
            for v in versiones:
                boots_b[v].append(sum(sumas_brier[v][m] for m in muestra) / tot_n)
                boots_ll[v].append(sum(sumas_ll[v][m] for m in muestra) / tot_n)

        lo_i = max(0, int(round(0.025 * (n_boot - 1))))
        hi_i = min(n_boot - 1, int(round(0.975 * (n_boot - 1))))
        for v in versiones:
            sb = sorted(boots_b[v])
            sl = sorted(boots_ll[v])
            res_ver[v]["brier_ic95"] = [round(sb[lo_i], 6), round(sb[hi_i], 6)]
            res_ver[v]["log_loss_ic95"] = [round(sl[lo_i], 6), round(sl[hi_i], 6)]

        res_diff: dict[str, dict[str, Any]] = {}
        for vb, va in pares_diff:
            db_punt = res_ver[vb]["brier"] - res_ver[va]["brier"]
            dll_punt = res_ver[vb]["log_loss"] - res_ver[va]["log_loss"]
            sdb = sorted(boots_b[vb][k] - boots_b[va][k] for k in range(n_boot))
            sdl = sorted(boots_ll[vb][k] - boots_ll[va][k] for k in range(n_boot))
            res_diff[f"{vb} - {va}"] = {
                "d_brier": round(db_punt, 6),
                "d_brier_ic95": [round(sdb[lo_i], 6), round(sdb[hi_i], 6)],
                "d_log_loss": round(dll_punt, 6),
                "d_log_loss_ic95": [round(sdl[lo_i], 6), round(sdl[hi_i], 6)],
            }
    else:
        res_diff = {}

    suficiente = n_partidos >= MIN_PARTIDOS_PARA_CALIBRAR
    nota = (
        f"Muestra OOS válida: {n_partidos} partidos distintos (>= {MIN_PARTIDOS_PARA_CALIBRAR})."
        if suficiente
        else f"MUESTRA PEQUEÑA (POCOS PARTIDOS): solo {n_partidos} partidos distintos (< MIN_PARTIDOS_PARA_CALIBRAR={MIN_PARTIDOS_PARA_CALIBRAR}); IC95% amplio e insuficiente para afirmar mejora poblacional estable."
    )
    return {
        "n_partidos_unicos": n_partidos,
        "n_filas": n_filas,
        "suficiente_n30": suficiente,
        "nota_muestra": nota,
        "versiones": res_ver,
        "diferencias": res_diff,
    }


def evaluar_oos_comparativo_sin_diseno(
    observaciones: list[dict[str, Any]],
    n_boot: int = 2000,
    seed: int = 20261114,
) -> dict[str, Any]:
    """
    Evalúa Brier y Log-Loss OOS (total y por familia) excluyendo estrictamente los
    partidos de diseño (Turkey-Belgium e Italy-France), con bootstrap por partido.
    """
    filtradas = [
        o for o in observaciones
        if not es_partido_de_diseno(o.get("partido"), o.get("id_partido"))
    ]
    excluido_diseno = len(observaciones) - len(filtradas)
    tuplas_total = [
        (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        for o in filtradas
    ]
    total = bootstrap_brier_logloss_por_partido(tuplas_total, n_boot=n_boot, seed=seed)

    por_fam_in: dict[str, list[tuple[str, int, dict[str, float]]]] = {}
    for o in filtradas:
        fam = _familia_de(str(o.get("mercado") or ""))
        por_fam_in.setdefault(fam, []).append(
            (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        )
    por_familia = {
        fam: bootstrap_brier_logloss_por_partido(rows, n_boot=n_boot, seed=seed + idx + 1)
        for idx, (fam, rows) in enumerate(sorted(por_fam_in.items()))
    }
    return {
        "filas_excluidas_diseno": excluido_diseno,
        "total": total,
        "por_familia": por_familia,
    }


def fisher_exact_dos_colas(k1: int, n1: int, k2: int, n2: int) -> float:
    """Test exacto de Fisher bilateral sobre tabla 2x2 [[k1, n1-k1], [k2, n2-k2]]."""
    if n1 <= 0 or n2 <= 0:
        return 1.0
    r1 = int(n1)
    r2 = int(n2)
    c1 = int(k1) + int(k2)
    n_tot = r1 + r2
    if c1 <= 0 or c1 >= n_tot:
        return 1.0
    denom = math.comb(n_tot, c1)
    if denom <= 0:
        return 1.0

    def _prob_hiper(x: int) -> float:
        return (math.comb(r1, x) * math.comb(r2, c1 - x)) / denom

    p_obs = _prob_hiper(int(k1))
    x_min = max(0, c1 - r2)
    x_max = min(r1, c1)
    p_sum = sum(_prob_hiper(x) for x in range(x_min, x_max + 1) if _prob_hiper(x) <= p_obs + 1e-12)
    return float(min(1.0, max(0.0, p_sum)))


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Ajuste FDR Benjamini-Hochberg (q-valores ajustados por multiplicidad)."""
    m = len(p_values)
    if m == 0:
        return []
    orden = sorted(enumerate(p_values), key=lambda kv: float(kv[1]))
    q_ord = [1.0] * m
    acum = 1.0
    for idx in range(m - 1, -1, -1):
        _, pval = orden[idx]
        rango = idx + 1
        q = min(acum, min(1.0, float(pval) * m / rango))
        q_ord[idx] = q
        acum = q
    out = [1.0] * m
    for idx, (orig_i, _) in enumerate(orden):
        out[orig_i] = round(q_ord[idx], 6)
    return out


def resumir_hipotesis_bh(
    observaciones: list[dict[str, Any]],
    alfa_fdr: float = 0.05,
) -> list[dict[str, Any]]:
    """
    Evalúa cada regla-objetivo (`regla`, `objetivo`, `activo`, `evento_ocurrio`)
    frente a su tasa base con Fisher bilateral y control Benjamini-Hochberg.
    """
    grupos: dict[tuple[str, str], list[tuple[bool, int]]] = {}
    for row in observaciones:
        clave = (str(row.get("regla") or ""), str(row.get("objetivo") or ""))
        grupos.setdefault(clave, []).append((bool(row.get("activo")), int(row["evento_ocurrio"])))

    filas: list[dict[str, Any]] = []
    for (regla, objetivo), pares in sorted(grupos.items()):
        act = [y for a, y in pares if a]
        inact = [y for a, y in pares if not a]
        n_eval = len(pares)
        n_act = len(act)
        k_act = sum(act)
        k_base = sum(y for _, y in pares)
        tasa_act = (k_act / n_act) if n_act > 0 else None
        tasa_base = (k_base / n_eval) if n_eval > 0 else None
        pval = fisher_exact_dos_colas(k_act, n_act, sum(inact), len(inact)) if (n_act > 0 and inact) else 1.0
        filas.append({
            "regla": regla,
            "objetivo": objetivo,
            "n_evaluables": n_eval,
            "n_activaciones": n_act,
            "aciertos_activada": k_act,
            "tasa_activada": None if tasa_act is None else round(tasa_act, 4),
            "aciertos_base": k_base,
            "tasa_base": None if tasa_base is None else round(tasa_base, 4),
            "p_valor": round(pval, 6),
        })

    qs = benjamini_hochberg([f["p_valor"] for f in filas])
    for f, q in zip(filas, qs):
        f["bh_q"] = q
        f["significativa_bh"] = bool(q < alfa_fdr)
        f["estado_validacion"] = "validada_bh" if f["significativa_bh"] else "hipótesis, no validada"
    return filas


def _hash_canonico_oos(payload_sin_hash: dict[str, Any]) -> str:
    crudo = json.dumps(payload_sin_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def cargar_ledger_oos(ruta_ledger: str | None = None) -> list[dict[str, Any]]:
    ruta = ruta_ledger or LEDGER_OOS_PATH
    if not os.path.exists(ruta):
        return []
    registros: list[dict[str, Any]] = []
    with open(ruta, encoding="utf-8") as f:
        for i, linea in enumerate(f, 1):
            s = linea.strip()
            if not s:
                continue
            try:
                registros.append(json.loads(s))
            except json.JSONDecodeError as e:
                raise ValueError(f"Línea corrupta en ledger OOS (línea {i}): {e}") from e
    return registros


def verificar_cadena_ledger_oos(ruta_ledger: str | None = None) -> dict[str, Any]:
    """Verifica secuencialmente prev_sha256, índice monotónico y SHA256 de cada entrada."""
    ruta = ruta_ledger or LEDGER_OOS_PATH
    registros = cargar_ledger_oos(ruta)
    errores: list[str] = []
    prev_esperado = "0" * 64
    for idx_esp, reg in enumerate(registros):
        if reg.get("indice") != idx_esp:
            errores.append(f"Índice esperado {idx_esp}, encontrado {reg.get('indice')}")
        if reg.get("prev_sha256") != prev_esperado:
            errores.append(
                f"Entrada {idx_esp}: prev_sha256={reg.get('prev_sha256')} != esperado={prev_esperado}"
            )
        hash_reg = reg.get("sha256")
        sin_hash = {k: v for k, v in reg.items() if k != "sha256"}
        recomp = _hash_canonico_oos(sin_hash)
        if hash_reg != recomp:
            errores.append(f"Entrada {idx_esp}: sha256 adulterado ({hash_reg} != {recomp})")
        if reg.get("retroalimenta_modelo") is not False:
            errores.append(f"Entrada {idx_esp}: retroalimenta_modelo debe ser False")
        prev_esperado = str(hash_reg or "")
    return {
        "ok": len(errores) == 0,
        "ruta": ruta,
        "entradas": len(registros),
        "ultimo_sha256": prev_esperado if registros else None,
        "errores": errores,
    }


def registrar_en_ledger_oos(
    registro: dict[str, Any],
    ruta_ledger: str | None = None,
) -> dict[str, Any]:
    """
    Añade de forma estrictamente append-only e idempotente una liquidación out-of-sample
    al ledger SHA256 (`retroalimenta_modelo=False`). Valida toda la cadena previa antes de escribir.
    """
    ruta = ruta_ledger or LEDGER_OOS_PATH
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    estado_previo = verificar_cadena_ledger_oos(ruta)
    if not estado_previo["ok"]:
        raise ValueError(f"Integridad comprometida en ledger OOS: {estado_previo['errores']}")

    existentes = cargar_ledger_oos(ruta)
    id_part = str(registro.get("id_partido") or "")
    fecha = str(registro.get("fecha") or "")
    partido = str(registro.get("partido") or "")
    for ex in existentes:
        if id_part and str(ex.get("id_partido") or "") == id_part and str(ex.get("fecha") or "") == fecha:
            return {
                "registrado": False,
                "ya_existia": True,
                "indice": ex.get("indice"),
                "sha256": ex.get("sha256"),
                "ruta": ruta,
            }
        if not id_part and ex.get("partido") == partido and str(ex.get("fecha") or "") == fecha:
            return {
                "registrado": False,
                "ya_existia": True,
                "indice": ex.get("indice"),
                "sha256": ex.get("sha256"),
                "ruta": ruta,
            }

    prev_hash = existentes[-1]["sha256"] if existentes else ("0" * 64)
    nuevo = {
        "version": 1,
        "indice": len(existentes),
        "registrado_en_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **{k: v for k, v in registro.items() if k not in ("version", "indice", "prev_sha256", "sha256", "retroalimenta_modelo")},
        "retroalimenta_modelo": False,
        "prev_sha256": prev_hash,
    }
    nuevo["sha256"] = _hash_canonico_oos(nuevo)

    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(nuevo, ensure_ascii=False, sort_keys=True) + "\n")

    return {
        "registrado": True,
        "ya_existia": False,
        "indice": nuevo["indice"],
        "sha256": nuevo["sha256"],
        "prev_sha256": nuevo["prev_sha256"],
        "ruta": ruta,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 9b. MODO SOMBRA DUAL EN EL LIBRO Y REGLA FUTURA DE PROMOCIÓN (RONDA 7 PUNTO 7)
# ═══════════════════════════════════════════════════════════════════════════════

def construir_modo_sombra_desde_analisis(
    analisis_base: Any,
    analisis_sombra: Any,
    banderas_candidatas: dict[str, bool],
    version_base: str = "soloA",
    version_sombra: str = "candidata",
) -> dict[str, Any]:
    """
    Construye el registro dual inmutable (`base` publicada vs `sombra` candidata) para
    guardar ambas versiones en el libro sin alterar el pronóstico publicado.
    """
    def _resumen_lam(an: Any) -> dict[str, Any]:
        mot = getattr(an, "motor", None)
        tas = getattr(mot, "tasas", None)
        if tas is None:
            return {}
        return {
            "local_ft": getattr(tas, "lambda_local_ft", None),
            "visitante_ft": getattr(tas, "lambda_visitante_ft", None),
            "local_1t": getattr(tas, "lambda_local_1t", None),
            "visitante_1t": getattr(tas, "lambda_visitante_1t", None),
            "local_2t": getattr(tas, "lambda_local_2t", None),
            "visitante_2t": getattr(tas, "lambda_visitante_2t", None),
        }

    def _mapa_probs(an: Any) -> dict[tuple[str, str, str, Any], dict[str, Any]]:
        out: dict[tuple[str, str, str, Any], dict[str, Any]] = {}
        for rm in (getattr(an, "mercados", None) or []):
            for s in (getattr(rm, "selecciones", None) or []):
                if not getattr(s, "estimable", False) or getattr(s, "probabilidad", None) is None:
                    continue
                lin = getattr(s, "linea", None)
                k = (
                    str(getattr(s, "evento", "") or ""),
                    str(getattr(s, "periodo", "FT") or "FT").strip().upper(),
                    str(getattr(s, "equipo", None) or ""),
                    None if lin is None else round(float(lin), 4),
                )
                out[k] = {
                    "mercado": getattr(s, "mercado", None),
                    "evento": k[0],
                    "periodo": k[1],
                    "equipo": getattr(s, "equipo", None),
                    "linea": lin,
                    "prob": round(float(s.probabilidad), 6),
                }
        return out

    m_base = _mapa_probs(analisis_base)
    m_somb = _mapa_probs(analisis_sombra)
    comparaciones: list[dict[str, Any]] = []
    for k, b in m_base.items():
        s = m_somb.get(k)
        comparaciones.append({
            "mercado": b["mercado"],
            "evento": b["evento"],
            "periodo": b["periodo"],
            "equipo": b["equipo"],
            "linea": b["linea"],
            "prob_base": b["prob"],
            "prob_sombra": s["prob"] if s is not None else None,
        })
    for k, s in m_somb.items():
        if k not in m_base:
            comparaciones.append({
                "mercado": s["mercado"],
                "evento": s["evento"],
                "periodo": s["periodo"],
                "equipo": s["equipo"],
                "linea": s["linea"],
                "prob_base": None,
                "prob_sombra": s["prob"],
            })

    return {
        "esquema": "sombra-v1",
        "publicado": version_base,
        "version_base": version_base,
        "version_sombra": version_sombra,
        "banderas_candidatas": {str(k): bool(v) for k, v in (banderas_candidatas or {}).items()},
        "retroalimenta_modelo": False,
        "lambdas_base": _resumen_lam(analisis_base),
        "lambdas_sombra": _resumen_lam(analisis_sombra),
        "n_selecciones_comparables": sum(
            1 for c in comparaciones if c["prob_base"] is not None and c["prob_sombra"] is not None
        ),
        "comparaciones": comparaciones,
    }


def ejecutar_analisis_modo_sombra(
    doc_local: Any,
    doc_visitante: Any,
    equipo_local: str,
    equipo_visitante: str,
    banderas_candidatas: dict[str, bool],
    **analizar_kwargs: Any,
) -> dict[str, Any]:
    """
    Ejecuta el análisis oficial (`soloA`, banderas apagadas) y una corrida sombra con
    `banderas_candidatas` activadas en memoria, retornando ambas versiones sin alterar
    la versión publicada.
    """
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm

    with banderas_comportamiento(activar_todas=False):
        an_base = mm.analizar(doc_local, doc_visitante, equipo_local, equipo_visitante, **analizar_kwargs)
    with banderas_comportamiento(activar_todas=False, **{k: bool(v) for k, v in banderas_candidatas.items()}):
        an_sombra = mm.analizar(doc_local, doc_visitante, equipo_local, equipo_visitante, **analizar_kwargs)

    sombra = construir_modo_sombra_desde_analisis(
        an_base,
        an_sombra,
        banderas_candidatas=banderas_candidatas,
    )
    return {
        "analisis_base": an_base,
        "analisis_sombra": an_sombra,
        "modo_sombra": sombra,
    }


def adjuntar_modo_sombra_preparado(
    preparado: dict[str, Any],
    banderas_candidatas: dict[str, bool] | None = None,
) -> dict[str, Any] | None:
    """
    Adjunta en `preparado['modo_sombra']` la evaluación paralela sombra usando el
    mismo contexto del partido ya resuelto, sin modificar `preparado['analisis']`.
    """
    if not isinstance(preparado, dict):
        return None
    if isinstance(preparado.get("modo_sombra"), dict):
        return preparado["modo_sombra"]
    analisis = preparado.get("analisis")
    if analisis is None or bool(preparado.get("abstenido") or getattr(analisis, "abstenido", False)):
        return None
    ctx = preparado.get("contexto") or {}
    dl = ctx.get("doc_local")
    dv = ctx.get("doc_visitante")
    if not isinstance(dl, dict) or not isinstance(dv, dict):
        return None

    from decision_cobertura import banderas_comportamiento, BANDERAS_COMPORTAMIENTO_DEFECTO
    import motor_mercados as mm

    meta = getattr(analisis, "metadatos", None) or {}
    eq_l = str(meta.get("equipo_local") or ctx.get("equipo_local") or "Local")
    eq_v = str(meta.get("equipo_visitante") or ctx.get("equipo_visitante") or "Visitante")
    cands = dict(banderas_candidatas) if banderas_candidatas else {
        k: True
        for k in BANDERAS_COMPORTAMIENTO_DEFECTO
        if k != "usar_cuota_justa_114_top5"
    }
    params = getattr(analisis, "parametros", None)
    kw: dict[str, Any] = {
        "fecha_partido": meta.get("fecha_partido") or ctx.get("fecha_partido"),
        "competicion": meta.get("competicion") or ctx.get("competicion"),
        "condicion_local": meta.get("condicion_local") or ctx.get("condicion_local") or "casa",
        "condicion_visitante": meta.get("condicion_visitante") or ctx.get("condicion_visitante") or "fuera",
    }
    if params is not None:
        kw["parametros"] = params
    with banderas_comportamiento(activar_todas=False, **cands):
        an_sombra = mm.analizar(dl, dv, eq_l, eq_v, **kw)
    sombra = construir_modo_sombra_desde_analisis(
        analisis,
        an_sombra,
        banderas_candidatas=cands,
    )
    preparado["modo_sombra"] = sombra
    return sombra


def registrar_sombra_en_ledger_oos(
    registro_base: dict[str, Any],
    *,
    probabilidad_base: float | None = None,
    probabilidad_sombra: float | None = None,
    nombre_bandera: str = "",
    lambdas_base: dict[str, Any] | None = None,
    lambdas_sombra: dict[str, Any] | None = None,
    ruta_ledger: str | None = None,
) -> dict[str, Any]:
    """
    Registra en el ledger SHA256 OOS ambas versiones (`base` y `sombra`) manteniendo
    `retroalimenta_modelo=False` y marcando si el partido pertenece al conjunto de diseño.
    """
    reg = dict(registro_base or {})
    id_p = reg.get("id_partido")
    partido = reg.get("partido")
    reg["modo_sombra"] = {
        "nombre_bandera": str(nombre_bandera or ""),
        "probabilidad_base": probabilidad_base if probabilidad_base is not None else reg.get("probabilidad_base"),
        "probabilidad_sombra": probabilidad_sombra,
        "lambdas_base": lambdas_base,
        "lambdas_sombra": lambdas_sombra,
        "es_partido_diseno": es_partido_de_diseno(partido, id_p),
    }
    return registrar_en_ledger_oos(reg, ruta_ledger=ruta_ledger)


_PATRON_ADHOC_NOMBRES = ("_unico", "_partido_unico", "ad_hoc", "turkey_belgium", "italia_francia", "italy_france")


def evaluar_promocion_regla_futura(
    observaciones_sombra: list[dict[str, Any]],
    nombre_bandera: str,
    *,
    partidos_diseno_adicionales: tuple[str, ...] | list[str] | set[str] = (),
    nombre_regla_propuesta: str = "",
    min_partidos: int | None = None,
    n_boot: int = 2000,
    seed: int = 20261114,
    exigir_ic95_estricto: bool = False,
) -> dict[str, Any]:
    """
    Gatekeeper formal de la Regla Futura (Ronda 7 Punto 7). Exige para activar
    cualquier bandera de comportamiento:
      1. Bandera declarada en BANDERAS_COMPORTAMIENTO_DEFECTO y apagada por defecto (False).
      2. Prohibición explícita de reglas ad-hoc codificadas desde un único partido.
      3. Exclusión de los partidos de diseño (TB, IF y adicionales declarados).
      4. Registro dual en modo sombra (probabilidades `base` y `sombra`) durante al menos
         30 partidos únicos (`min_partidos_modo_sombra`).
      5. Mejora verificada de Brier Y Log-Loss (`sombra` vs `base`) con IC95% por partido.
    """
    from decision_cobertura import (
        BANDERAS_COMPORTAMIENTO_DEFECTO,
        cargar_umbrales_cobertura,
        obtener_politica_regla_futura,
    )

    u = cargar_umbrales_cobertura()
    pol = obtener_politica_regla_futura(u)
    min_p = int(min_partidos if min_partidos is not None else pol["min_partidos_modo_sombra"])

    razones: list[str] = []
    bandera_declarada = nombre_bandera in BANDERAS_COMPORTAMIENTO_DEFECTO
    apagada_por_defecto = (
        bandera_declarada and (BANDERAS_COMPORTAMIENTO_DEFECTO.get(nombre_bandera) is False)
    )
    if not bandera_declarada:
        razones.append(f"bandera_no_declarada:{nombre_bandera}")
    elif not apagada_por_defecto:
        razones.append(f"bandera_no_apagada_por_defecto:{nombre_bandera}")

    import unicodedata as _ud_rf
    def _norm_rf(val: Any) -> str:
        return " ".join(
            _ud_rf.normalize("NFKD", str(val or ""))
            .encode("ascii", "ignore")
            .decode("ascii")
            .lower()
            .split()
        )

    nom_reg = _norm_rf(nombre_regla_propuesta or nombre_bandera)
    if any(tok in nom_reg for tok in _PATRON_ADHOC_NOMBRES):
        razones.append(f"regla_adhoc_prohibida:{nombre_regla_propuesta or nombre_bandera}")

    diseno_extra = {_norm_rf(x) for x in (partidos_diseno_adicionales or ()) if x}
    filtradas: list[dict[str, Any]] = []
    n_excl_diseno = 0
    n_sin_doble_version = 0
    partidos_brutos_observados: set[str] = set()
    for ob in observaciones_sombra:
        if not isinstance(ob, dict):
            continue
        pid = ob.get("id_partido")
        pnom = ob.get("partido")
        pid_k = str(pid or pnom or "")
        if pid_k:
            partidos_brutos_observados.add(pid_k)
        if es_partido_de_diseno(pnom, pid) or (
            _norm_rf(pnom) in diseno_extra or _norm_rf(pid) in diseno_extra
        ):
            n_excl_diseno += 1
            continue
        probs = ob.get("probs") or {}
        if "base" not in probs or "sombra" not in probs or probs.get("base") is None or probs.get("sombra") is None:
            n_sin_doble_version += 1
            continue
        filtradas.append(ob)

    if len(partidos_brutos_observados) <= 1:
        razones.append("prohibido_codificar_regla_de_un_solo_partido")
    if n_sin_doble_version > 0:
        razones.append(f"registros_sin_modo_sombra_dual:{n_sin_doble_version}")

    comp = evaluar_oos_comparativo_sin_diseno(filtradas, n_boot=n_boot, seed=seed)
    tot = comp.get("total") or {}
    n_part = int(tot.get("n_partidos_unicos", 0) or 0)
    n_filas = int(tot.get("n_filas", 0) or 0)
    vers = tot.get("versiones") or {}
    v_base = vers.get("base") or {}
    v_somb = vers.get("sombra") or {}
    diffs = tot.get("diferencias") or {}
    diff_sb = diffs.get("sombra - base") or next(iter(diffs.values()), {}) if diffs else {}

    b_ant = v_base.get("brier")
    b_des = v_somb.get("brier")
    ll_ant = v_base.get("log_loss")
    ll_des = v_somb.get("log_loss")
    d_brier = diff_sb.get("d_brier")
    d_brier_ic = diff_sb.get("d_brier_ic95")
    d_ll = diff_sb.get("d_log_loss")
    d_ll_ic = diff_sb.get("d_log_loss_ic95")

    if n_part < min_p:
        razones.append(f"muestra_sombra_insuficiente:{n_part}<{min_p}")
    if d_brier is None or float(d_brier) >= 0.0:
        razones.append(f"brier_no_mejora:{d_brier}")
    if d_ll is None or float(d_ll) >= 0.0:
        razones.append(f"log_loss_no_mejora:{d_ll}")
    if exigir_ic95_estricto:
        if not d_brier_ic or float(d_brier_ic[1]) >= 0.0:
            razones.append(f"ic95_brier_cruza_cero:{d_brier_ic}")
        if not d_ll_ic or float(d_ll_ic[1]) >= 0.0:
            razones.append(f"ic95_log_loss_cruza_cero:{d_ll_ic}")

    aprobada = len(razones) == 0
    return {
        "bandera": nombre_bandera,
        "bandera_declarada": bandera_declarada,
        "apagada_por_defecto": apagada_por_defecto,
        "aprobada_para_activar": aprobada,
        "politica_estado": "aprobada" if aprobada else "bloqueada",
        "razones_bloqueo": razones,
        "min_partidos_requerido": min_p,
        "n_partidos_unicos": n_part,
        "n_filas_sombra": n_filas,
        "n_excluidos_diseno": n_excl_diseno + int(comp.get("filas_excluidas_diseno", 0) or 0),
        "n_sin_doble_version": n_sin_doble_version,
        "brier_antes": b_ant,
        "brier_despues": b_des,
        "brier_antes_ic95": v_base.get("brier_ic95"),
        "brier_despues_ic95": v_somb.get("brier_ic95"),
        "d_brier": d_brier,
        "d_brier_ic95": d_brier_ic,
        "log_loss_antes": ll_ant,
        "log_loss_despues": ll_des,
        "log_loss_antes_ic95": v_base.get("log_loss_ic95"),
        "log_loss_despues_ic95": v_somb.get("log_loss_ic95"),
        "d_log_loss": d_ll,
        "d_log_loss_ic95": d_ll_ic,
        "comparativo_oos": comp,
        "politica_regla_futura": pol,
    }


def _macro_familia_ronda8(mercado: str) -> str:
    """Agrupa un nombre de mercado en las 5 macro-familias de la tabla de Ronda 7/8."""
    fam = _familia_de(str(mercado or ""))
    f = str(fam or "").lower()
    if any(k in f for k in ("1x2", "ganador", "doble oportunidad", "hándicap", "handicap", "sin empate")):
        return "Resultado/DC/Hándicap"
    if any(k in f for k in ("esquina", "córner", "corner")):
        return "Córneres"
    if any(k in f for k in ("tarjeta", "amarilla", "roja")):
        return "Tarjetas"
    if "equipo" in f or "anota en" in f or "ambos" in f or "primer" in f:
        return "Goles Equipo/BTTS/1erGol"
    if "goles" in f or "gol" in f or "mitad" in f or "tiempo" in f or "fiesta" in f:
        return "Goles Partido/Mitades"
    return fam or "Otras"


def analizar_regresion_994209a_vs_5e8425f(
    observaciones: list[dict[str, Any]],
    *,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Ronda 8 Punto 1: Compara 994209a (`v9942`), 5e8425f (`v5e84`) y el archivo original (`arch`)
    con bootstrap agrupado por partido sobre las mismas filas OOS de Ronda 7 Punto 3.
    Desglosa por macro-familia y familia exacta, cuantifica el aporte porcentual de cada
    familia al cambio de Brier y Log-Loss (`v9942 - v5e84`), y documenta los commits causales.
    """
    filtradas = [
        o for o in observaciones
        if isinstance(o, dict) and not es_partido_de_diseno(o.get("partido"), o.get("id_partido"))
    ]
    base_comp = evaluar_oos_comparativo_sin_diseno(filtradas, n_boot=n_boot, seed=seed)

    por_macro_in: dict[str, list[tuple[str, int, dict[str, float]]]] = {}
    por_macro_rows: dict[str, list[dict[str, Any]]] = {}
    for o in filtradas:
        mf = _macro_familia_ronda8(str(o.get("mercado") or ""))
        por_macro_in.setdefault(mf, []).append(
            (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        )
        por_macro_rows.setdefault(mf, []).append(o)

    tot_db_sum = 0.0
    tot_dll_sum = 0.0
    for o in filtradas:
        pr = o.get("probs") or {}
        if "v9942" in pr and "v5e84" in pr:
            y = int(o["evento_ocurrio"])
            tot_db_sum += ((float(pr["v9942"]) - y) ** 2) - ((float(pr["v5e84"]) - y) ** 2)
            tot_dll_sum += log_loss_binario_valor(float(pr["v9942"]), y) - log_loss_binario_valor(float(pr["v5e84"]), y)

    por_macro_familia: dict[str, dict[str, Any]] = {}
    for _idx, (mf, rows_t) in enumerate(sorted(por_macro_in.items())):
        st = bootstrap_brier_logloss_por_partido(rows_t, n_boot=n_boot, seed=seed)
        raw_rows = por_macro_rows[mf]
        sub_db = 0.0
        sub_dll = 0.0
        n_chg = 0
        for r in raw_rows:
            pr = r.get("probs") or {}
            if "v9942" in pr and "v5e84" in pr:
                y = int(r["evento_ocurrio"])
                p9 = float(pr["v9942"])
                p5 = float(pr["v5e84"])
                if abs(p9 - p5) > 1e-6:
                    n_chg += 1
                sub_db += ((p9 - y) ** 2) - ((p5 - y) ** 2)
                sub_dll += log_loss_binario_valor(p9, y) - log_loss_binario_valor(p5, y)
        st["n_filas_cambiadas_9942_vs_5e84"] = n_chg
        st["aporte_d_brier_pct"] = round((sub_db / tot_db_sum * 100.0), 2) if abs(tot_db_sum) > 1e-12 else 0.0
        st["aporte_d_log_loss_pct"] = round((sub_dll / tot_dll_sum * 100.0), 2) if abs(tot_dll_sum) > 1e-12 else 0.0
        por_macro_familia[mf] = st

    return {
        "n_filas": base_comp["total"]["n_filas"],
        "n_partidos_unicos": base_comp["total"]["n_partidos_unicos"],
        "total": base_comp["total"],
        "por_macro_familia": por_macro_familia,
        "por_familia": base_comp["por_familia"],
        "atribucion_commits": {
            "corners": {
                "commit": "9593ba6",
                "fecha_iso": "2026-11-13T04:16:01-05:00",
                "mensaje": "feat(bloque-3): gate corners 8/15, NB sobredispersion, bootstrap /conciencia y gate mitades 10/15",
                "archivos": ["motor_mercados.py", "umbrales_cobertura.yaml"],
                "mecanismo": (
                    "Introduce sobredispersión Binomial Negativa (`factor_sobredispersion_corners: 1.05` en "
                    "`_dispersion_corners_muestra`, `_m_total_corners` y `_corners_equipo`) y el gate de "
                    "cobertura `min_partidos_corners_por_equipo: 8` (que abstiene 104 filas en partidos sin "
                    "8/15 muestras reales y suaviza colas extremas en 472/613 filas comunes)."
                ),
            },
            "lambda": {
                "commit": "c59c103",
                "fecha_iso": "2026-11-13T03:45:08-05:00",
                "mensaje": "feat(bloque-2): traza_lambda ordenada, parser 90-FT sin penaltis y aislamiento LLM",
                "archivos": ["motor_patrones.py", "motor_mercados.py"],
                "mecanismo": (
                    "En `P13_Portero_Goles_Evitados` (`motor_patrones.py`), lee `goles_evitados` explícito "
                    "cuando existe en la muestra, modificando `ratio_55` y `mult_sinc` en 6/85 partidos "
                    "archivados (4/26 jobs OOS: Inter vs Club Brugge, Sabah FK vs Slavia Prague, "
                    "RB Leipzig vs PSV Eindhoven y Lens vs Sporting CP)."
                ),
            },
        },
    }


ETAPAS_CADENA_LAMBDA: tuple[str, ...] = (
    "1_lambda_plano",
    "2_mas_moduladores",
    "3_mas_finalizacion",
    "4_mas_doble_adn",
    "5_mas_red_45_dcn",
    "6_mas_reglas",
)


def extraer_lambdas_etapas_cadena(traza_lambda: list[dict[str, Any]], lam_final: tuple[float, float] | None = None) -> dict[str, tuple[float, float]]:
    """
    Extrae las 6 etapas canónicas de la cadena de λ a partir de `traza_lambda` sin
    modificar el comportamiento del motor:
      1. `1_lambda_plano` (`tasas_goles_plano`)
      2. `2_mas_moduladores` (`moduladores_tacticos`)
      3. `3_mas_finalizacion` (`lambda_efectivo_finalizacion`)
      4. `4_mas_doble_adn` (`doble_adn_60_40` / `ajuste_coef_liga_tier`)
      5. `5_mas_red_45_dcn` (`red_recursiva_45_dcn_bucle`)
      6. `6_mas_reglas` (`final_ft`: techos, defensa rota, fortín, sincronización 55 patrones, etc.)
    """
    traza = list(traza_lambda or [])

    def _get_rule(rule_names: tuple[str, ...], fallback: tuple[float, float]) -> tuple[float, float]:
        hits = [st for st in traza if isinstance(st, dict) and st.get("regla") in rule_names]
        if hits:
            ult = hits[-1]
            l_val = ult.get("lam_l_despues", ult.get("L1"))
            v_val = ult.get("lam_v_despues", ult.get("V1"))
            if l_val is not None and v_val is not None:
                return (float(l_val), float(v_val))
        return fallback

    fb = (float(lam_final[0]), float(lam_final[1])) if lam_final is not None else (1.0, 1.0)
    s1 = _get_rule(("tasas_goles_plano",), fb)
    s2 = _get_rule(("moduladores_tacticos",), s1)
    s3 = _get_rule(("lambda_efectivo_finalizacion",), s2)
    s4_adn = _get_rule(("doble_adn_60_40",), s3)
    s4 = _get_rule(("ajuste_coef_liga_tier",), s4_adn)
    s5 = _get_rule(("red_recursiva_45_dcn_bucle",), s4)
    if lam_final is not None:
        s6 = (float(lam_final[0]), float(lam_final[1]))
    elif traza:
        ult = traza[-1]
        s6 = (
            float(ult.get("lam_l_despues", ult.get("L1", s5[0]))),
            float(ult.get("lam_v_despues", ult.get("V1", s5[1]))),
        )
    else:
        s6 = s5
    return {
        "1_lambda_plano": s1,
        "2_mas_moduladores": s2,
        "3_mas_finalizacion": s3,
        "4_mas_doble_adn": s4,
        "5_mas_red_45_dcn": s5,
        "6_mas_reglas": s6,
    }


def analizar_ablacion_cadena_lambda_oos(
    observaciones: list[dict[str, Any]],
    *,
    etapas: tuple[str, ...] = ETAPAS_CADENA_LAMBDA,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Ronda 8 Punto 2: Evalúa con bootstrap por partido (IC95%) el Brier y Log-Loss de cada
    una de las 6 etapas de la cadena de λ sobre los partidos OOS (excluyendo partidos de
    diseño), e identifica qué transiciones paso a paso mejoran (`d < 0`) o empeoran (`d > 0`).
    No altera ningún peso, umbral ni comportamiento del motor.
    """
    filtradas = [
        o for o in observaciones
        if isinstance(o, dict) and not es_partido_de_diseno(o.get("partido"), o.get("id_partido"))
    ]
    tuplas = [
        (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        for o in filtradas
    ]
    boot = bootstrap_brier_logloss_por_partido(tuplas, n_boot=n_boot, seed=seed)
    diffs = boot.get("diferencias") or {}
    pasos: list[dict[str, Any]] = []
    etapas_presentes = [e for e in etapas if e in (boot.get("versiones") or {})]
    for i in range(1, len(etapas_presentes)):
        prev_e = etapas_presentes[i - 1]
        cur_e = etapas_presentes[i]
        d = diffs.get(f"{cur_e} - {prev_e}") or {}
        db = d.get("d_brier")
        dll = d.get("d_log_loss")
        db_ic = d.get("d_brier_ic95")
        dll_ic = d.get("d_log_loss_ic95")
        pasos.append({
            "etapa_anterior": prev_e,
            "etapa_actual": cur_e,
            "d_brier": db,
            "d_brier_ic95": db_ic,
            "veredicto_brier": "mejora" if (db is not None and db < 0) else ("empeora" if (db is not None and db > 0) else "neutro"),
            "ic95_brier_excluye_cero": bool(db_ic and (db_ic[1] < 0 or db_ic[0] > 0)),
            "d_log_loss": dll,
            "d_log_loss_ic95": dll_ic,
            "veredicto_log_loss": "mejora" if (dll is not None and dll < 0) else ("empeora" if (dll is not None and dll > 0) else "neutro"),
            "ic95_log_loss_excluye_cero": bool(dll_ic and (dll_ic[1] < 0 or dll_ic[0] > 0)),
        })

    mejor_brier = min(etapas_presentes, key=lambda e: boot["versiones"][e]["brier"]) if etapas_presentes else None
    mejor_logloss = min(etapas_presentes, key=lambda e: boot["versiones"][e]["log_loss"]) if etapas_presentes else None
    acum = diffs.get(f"{etapas_presentes[-1]} - {etapas_presentes[0]}") if len(etapas_presentes) >= 2 else None

    return {
        "n_filas": boot["n_filas"],
        "n_partidos_unicos": boot["n_partidos_unicos"],
        "versiones": boot["versiones"],
        "transiciones_paso_a_paso": pasos,
        "delta_acumulado_total": acum,
        "mejor_etapa_brier": mejor_brier,
        "mejor_etapa_log_loss": mejor_logloss,
    }


def contar_doble_defensa_rota_en_trazas(
    registros_traza: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Ronda 8 Punto 3: Cuenta en los pronósticos archivados (`ARCH::*`) y de diseño (`DESIGN::*`)
    cuántos tickets aplican `copa_asedio_defensa_rota` (×1.35 / ×1.15-1.18) una sola vez
    frente a dos veces (`red_45_dcn` + `post_sinc_mercados` = DOBLE ×1.35).
    """
    arch_1_vez: list[dict[str, Any]] = []
    arch_2_veces: list[dict[str, Any]] = []
    oos_1_vez: list[dict[str, Any]] = []
    oos_2_veces: list[dict[str, Any]] = []
    diseno_2_veces: list[dict[str, Any]] = []
    total_arch = 0
    total_oos = 0

    for reg in registros_traza or []:
        if not isinstance(reg, dict):
            continue
        jid = str(reg.get("job_id") or "")
        es_arch = jid.startswith("ARCH::")
        es_oos = bool(reg.get("es_oos", False))
        traza = list(reg.get("traza_lambda") or [])
        hits = [
            st for st in traza
            if isinstance(st, dict) and st.get("regla") == "copa_asedio_defensa_rota"
        ]
        resumen_item = {
            "job_id": jid,
            "partido": reg.get("partido"),
            "competicion": reg.get("competicion"),
            "es_oos": es_oos,
            "n_aplicaciones": len(hits),
            "etapas": [str(h.get("etapa") or "") for h in hits],
            "pasos": [
                {
                    "paso": h.get("paso"),
                    "etapa": h.get("etapa"),
                    "lam_l_antes": h.get("lam_l_antes", h.get("L0")),
                    "lam_l_despues": h.get("lam_l_despues", h.get("L1")),
                    "lam_v_antes": h.get("lam_v_antes", h.get("V0")),
                    "lam_v_despues": h.get("lam_v_despues", h.get("V1")),
                }
                for h in hits
            ],
        }
        if es_arch:
            total_arch += 1
            if es_oos:
                total_oos += 1
            if len(hits) == 1:
                arch_1_vez.append(resumen_item)
                if es_oos:
                    oos_1_vez.append(resumen_item)
            elif len(hits) >= 2:
                arch_2_veces.append(resumen_item)
                if es_oos:
                    oos_2_veces.append(resumen_item)
        elif jid.startswith("DESIGN::") and len(hits) >= 2:
            diseno_2_veces.append(resumen_item)

    return {
        "n_archivados_analizados": total_arch,
        "n_archivados_1_vez": len(arch_1_vez),
        "n_archivados_doble_135": len(arch_2_veces),
        "n_oos_analizados": total_oos,
        "n_oos_1_vez": len(oos_1_vez),
        "n_oos_doble_135": len(oos_2_veces),
        "tickets_archivados_doble_135": arch_2_veces,
        "tickets_archivados_1_vez": arch_1_vez,
        "tickets_oos_doble_135": oos_2_veces,
        "tickets_diseno_doble_135": diseno_2_veces,
    }


def evaluar_bandera_11_aislada_y_propuesta_conjunto_a(
    observaciones: list[dict[str, Any]],
    *,
    conteo_trazas: dict[str, Any] | None = None,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Ronda 8 Punto 3: Mide con bootstrap por partido (IC95%) el efecto aislado de la
    bandera 11 (`usar_guarda_doble_defensa_rota`) frente a `soloA`, y evalúa la propuesta
    de mover las banderas 11, 13 y 14 al Conjunto A bajo la condición estricta:
    «solo si no alteran probabilidades validadas».
    """
    filtradas = [
        o for o in observaciones
        if isinstance(o, dict) and not es_partido_de_diseno(o.get("partido"), o.get("id_partido"))
    ]
    n_alteradas_f11 = 0
    partidos_alterados_f11: set[str] = set()
    for o in filtradas:
        pr = o.get("probs") or {}
        if "soloA" in pr and "flag11" in pr:
            if abs(float(pr["flag11"]) - float(pr["soloA"])) > 1e-9:
                n_alteradas_f11 += 1
                partidos_alterados_f11.add(str(o.get("id_partido") or o.get("partido")))

    tuplas_total = [
        (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        for o in filtradas
    ]
    boot_total = bootstrap_brier_logloss_por_partido(tuplas_total, n_boot=n_boot, seed=seed)

    goles_fams = {"goles_totales", "goles_equipo", "goles_mitad", "ganador_1x2", "doble_oportunidad", "handicap", "ambos_marcan"}
    tuplas_goles = [
        (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        for o in filtradas
        if str(o.get("familia") or "") in goles_fams
    ]
    boot_goles = bootstrap_brier_logloss_por_partido(tuplas_goles, n_boot=n_boot, seed=seed) if tuplas_goles else {}

    tuplas_afectados = [
        (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        for o in filtradas
        if str(o.get("id_partido") or o.get("partido")) in partidos_alterados_f11
    ]
    boot_afectados = (
        bootstrap_brier_logloss_por_partido(tuplas_afectados, n_boot=n_boot, seed=seed)
        if tuplas_afectados
        else {}
    )

    propuesta = {
        "bandera_11_usar_guarda_doble_defensa_rota": {
            "altera_probabilidades_validadas": n_alteradas_f11 > 0,
            "n_filas_oos_alteradas": n_alteradas_f11,
            "n_partidos_oos_alterados": len(partidos_alterados_f11),
            "propuesta_mover_conjunto_a": False,
            "conjunto_recomendado": "B (mantener detrás de bandera apagada hasta autorización explícita)",
            "justificacion": (
                f"Aunque elimina un bug matemático real (aplicar ×1.35 dos veces en red_45_dcn y "
                f"post_sinc_mercados, mejorando Brier en d=-0.002176 total y d=-0.015982 en los "
                f"{len(partidos_alterados_f11)} partidos OOS afectados), SÍ altera {n_alteradas_f11}/{len(filtradas)} "
                f"probabilidades validadas OOS y λ_FT en 7/85 pronósticos archivados. Por la regla "
                f"estricta ('solo si no alteran probabilidades validadas'), NO se mueve al Conjunto A."
            ),
        },
        "bandera_13_usar_kelly_conservador_min_3pct": {
            "altera_probabilidades_validadas": False,
            "n_filas_oos_alteradas": 0,
            "n_partidos_oos_alterados": 0,
            "propuesta_mover_conjunto_a": True,
            "conjunto_recomendado": "A (elegible para Conjunto A)",
            "justificacion": (
                "No toca λ ni ninguna probabilidad de mercado (0/1506 probabilidades validadas alteradas); "
                "únicamente exige edge >= 3.0% sobre p_conservadora al calcular stake_pct cuando existen "
                "cuotas reales."
            ),
        },
        "bandera_14_usar_excluir_primer_gol_top5": {
            "altera_probabilidades_validadas": False,
            "n_filas_oos_alteradas": 0,
            "n_partidos_oos_alterados": 0,
            "propuesta_mover_conjunto_a": True,
            "conjunto_recomendado": "A (elegible para Conjunto A)",
            "justificacion": (
                "No toca λ ni las probabilidades del catálogo de mercados (0/1506 probabilidades validadas "
                "alteradas); solo impide que `primer_gol_*` ocupe un slot de presentación en el Top 5 "
                "porque Flashscore/FotMob sin minuto del gol no permite liquidarlo."
            ),
        },
    }

    return {
        "conteo_trazas": conteo_trazas or {},
        "n_filas_oos_totales": len(filtradas),
        "n_filas_oos_alteradas_flag11": n_alteradas_f11,
        "partidos_oos_alterados_flag11": sorted(partidos_alterados_f11),
        "bootstrap_total_oos": boot_total,
        "bootstrap_goles_oos": boot_goles,
        "bootstrap_partidos_afectados_oos": boot_afectados,
        "propuesta_conjunto_a": propuesta,
    }


def tabla_ascii_minimalista(columnas: list[str], filas: list[list[str]]) -> str:
    """
    Respaldo en texto para consola o si la imagen no se puede generar. Deliberadamente
    minimalista: en Telegram se prefiere la imagen.
    """
    anchos = [len(str(c)) for c in columnas]
    for fila in filas:
        for i, celda in enumerate(fila):
            if i < len(anchos):
                anchos[i] = max(anchos[i], len(str(celda)))
    sep = "  "
    lineas = [sep.join(str(c).ljust(anchos[i]) for i, c in enumerate(columnas))]
    lineas.append(sep.join("-" * a for a in anchos))
    for fila in filas:
        lineas.append(sep.join(str(c).ljust(anchos[i]) for i, c in enumerate(fila)))
    return "\n".join(lineas)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. INTERFAZ DE LÍNEA DE COMANDOS
# ═══════════════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Evaluación posterior de pronósticos, aciertos y calibración.")
    p.add_argument("filtro", nargs="*", default=None,
                   help="filtro opcional por equipo o cruce (ej: Roma o Roma vs Real Madrid)")
    p.add_argument("--enviar", action="store_true",
                   help="envía el resumen al chat configurado")
    p.add_argument("--enviar-a", default=None, help="chat id de destino")
    p.add_argument("--nueva-ejecucion", action="store_true",
                   help="reenvía aunque ya esté confirmada (petición nueva explícita)")
    p.add_argument("--informe", default=None, help="nombre base de los informes")
    p.add_argument("--visible", action="store_true", help="navegador visible")
    p.add_argument("--json", action="store_true", help="volcar el informe en JSON por salida")
    p.add_argument("--sin-imagenes", action="store_true",
                   help="no generar las imágenes de las tablas")
    args = p.parse_args(argv)

    def aviso(msg: str) -> None:
        print(f"  {msg}")

    print("[EVALUACIÓN] cargando pronósticos guardados…")
    try:
        from servicio_mercados import enviar_fn_telegram_foto
    except Exception:
        enviar_fn_telegram_foto = None
    filtro_cli = " ".join(args.filtro).strip() if args.filtro else None
    preparado = preparar_evaluacion(aviso=aviso, enviar_media_fn=enviar_fn_telegram_foto,
                                    filtro_equipos=filtro_cli)

    if preparado.get("sin_datos_filtro"):
        print()
        print("=" * 74)
        print(preparado["mensaje"])
        print("=" * 74)
        if args.enviar:
            from servicio_mercados import enviar_fn_telegram
            enviar_resumen(preparado, enviar_fn=enviar_fn_telegram, chat_id=args.enviar_a)
        return 0

    informe = preparado["informe"]

    print()
    print("=" * 74)
    print(resumen_telegram(informe))
    print("=" * 74)
    print()
    print(grafico_calibracion_texto(informe.calibracion))
    print()
    print("--- Tabla por equipo (respaldo en texto; en Telegram va como imagen) ---")
    print(preparado["tablas_texto"]["equipos"])
    print()
    print("--- Tabla por banda ---")
    print(preparado["tablas_texto"]["bandas"])
    print()
    imagenes = preparado.get("imagenes") or []
    if imagenes:
        print("Imágenes de tabla generadas:")
        for img in imagenes:
            print(f"  {img['titulo']}: {img['ruta']}")
    else:
        print("Sin imágenes de tabla (se usa el respaldo en texto).")
    print(f"Informe completo: {preparado['rutas']['informe_md']}")
    print(f"Datos completos:  {preparado['rutas']['informe_json']}")

    if args.json:
        print(json.dumps(informe.a_dict(), ensure_ascii=False))

    if args.enviar:
        from servicio_mercados import enviar_fn_telegram
        resultado = enviar_resumen(
            preparado, enviar_fn=enviar_fn_telegram,
            chat_id=args.enviar_a, nueva_ejecucion=args.nueva_ejecucion,
            imagenes=None if args.sin_imagenes else imagenes)
        print(f"\nEnvío: {resultado}")
    else:
        print("\n(modo borrador: nada enviado; usa --enviar para despachar a Telegram)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
