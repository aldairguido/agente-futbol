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
import json
import os
import re
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
                       "no_verificable", "regla_no_definida")


# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

def _slug(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "x").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").lower() or "x"


def _ahora() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _clave_equipo(nombre: str) -> str:
    """Clave tolerante a tildes y palabras vacías para emparejar equipos."""
    base = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode()
    palabras = [p for p in re.split(r"[^A-Za-z0-9]+", base.lower()) if p]
    vacias = {"de", "del", "la", "el", "los", "las", "y", "e", "club", "fc", "cf"}
    sig = [p for p in palabras if p not in vacias]
    return "".join(sig) or "".join(palabras)


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

    def a_dict(self) -> dict[str, Any]:
        return {
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
            "inmutable": True,
        }


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
                            publicado_en: str | None = None) -> SnapshotPronostico:
    """
    Construye el snapshot a partir del resultado de `preparar_analisis`.
    Guarda TODAS las selecciones estimables (incluidas las inferiores al umbral)
    para poder evaluar el modelo completo, y marca aparte las publicadas.
    """
    analisis = preparado["analisis"]
    metadatos = analisis.metadatos
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
            return binario(len(resultado.goles) == 0,
                           f"{len(resultado.goles)} goles: {'ninguno' if not resultado.goles else 'sí hubo'}")
        if not resultado.goles:
            return binario(False, "no hubo goles: ningún equipo anotó primero")
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
    if evento.startswith(("local_corners_", "visitante_corners_")):
        linea_c = _interpretar_linea(evento)
        valor = _corners(resultado, equipo_evento, periodo)
        if valor is None or linea_c is None:
            return no_verificable(f"sin córneres de {equipo_evento} en {periodo}")
        if "_over_" in evento:
            return binario(valor > linea_c, f"{equipo_evento} {valor} córneres vs más de {linea_c}")
        return binario(valor < linea_c, f"{equipo_evento} {valor} córneres vs menos de {linea_c}")

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
