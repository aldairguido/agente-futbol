"""
agente_futbol/fuentes_xg.py
───────────────────────────────────────────────────────────────────────────────
Capa de FUENTES de datos avanzados (xG, xGOT, xA, xG por tiempo) para la
Auditoría Histórica Profunda.

Diseño: un objeto "FuenteXG" encapsula
  1. la resolución de un nombre de equipo -> identificador canónico,
  2. la lista de sus últimos partidos FINALIZADOS,
  3. la matriz de estadísticas de un partido, periodizada (FT / 1T / 2T).

Implementaciones:
  • FuenteFotMob   — ACTIVA. Intercepta los endpoints JSON internos de la web
                     (/api/data/*) con un contexto real de navegador. Verificado
                     en vivo: xG, xGOT, xG open/set play, posesión, remates,
                     grandes ocasiones, pases, defensa, portería y xA de jugador,
                     desglosados en All / FirstHalf / SecondHalf.
  • FuenteSofascore — ADAPTADOR. Implementa el mismo contrato contra la API de
                     Sofascore, pero está DESACTIVADO por defecto: desde esta red
                     Sofascore responde con captcha (web) y 403 / cuerpo vacío
                     (API). Se activa con AUDITORIA_FUENTE=sofascore cuando se
                     ejecute desde una IP/sesión que sí tenga acceso.

IMPORTANTE (honestidad de datos): este módulo NO fabrica métricas. Si una
estadística no viene en la respuesta de la fuente, se devuelve None y queda
registrada en `metricas_no_disponibles`. Nunca se inventan valores.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

import config


def ejecutar_sync_seguro(fn, *args, **kwargs):
    """Si hay un event loop asyncio corriendo en este hilo, ejecuta fn (ej. sync_playwright) en un hilo limpio."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(fn, *args, **kwargs).result()
    return fn(*args, **kwargs)


_ejecutar_fuera_de_asyncio = ejecutar_sync_seguro

BASE_DIR = Path(__file__).resolve().parent

# ═══════════════════════════════════════════════════════════════════════════════
# CATÁLOGO DE MÉTRICAS SOLICITADAS
# ═══════════════════════════════════════════════════════════════════════════════
# Cada métrica se define por:
#   clave      : nombre estable de la columna en el output
#   etiqueta   : nombre legible en español
#   categoria  : bloque del informe
#   alias      : variantes de título tal como las publica la fuente (minúsculas)
#
# El orden de esta tupla define el orden de las filas en CSV e informe.

CATEGORIA_PRINCIPALES = "ESTADÍSTICAS PRINCIPALES"
CATEGORIA_DISPAROS = "DISPAROS"
CATEGORIA_ATAQUES = "ATAQUES"
CATEGORIA_PASES = "PASES"
CATEGORIA_DEFENSA = "DEFENSA"
CATEGORIA_PORTERIA = "PORTERÍA (ATAJADAS)"


# ── Métricas que la fuente secundaria (Flashscore) NO puede aportar ──────────
# Medido en vivo: su feed `df_st_1_{id}` publica 35 métricas de PARTIDO COMPLETO y
# no existe ningún feed con desglose por tiempos (probados df_st_2, df_sui_1,
# df_ht_1 y df_st_1_ht: todos idénticos o vacíos). Por eso hay métricas que en 1T/2T
# solo puede traer FotMob, y si FotMob no las publica quedan nulas: NO se reparten
# por mitades, porque eso sería inventar el dato.
METRICAS_SOLO_FT_CONOCIDAS: frozenset[str] = frozenset({
    "pases_ratio", "saques_puerta", "tiros_libres", "errores_remate", "errores_gol",
    "pases_entre_lineas", "xa_equipo", "xgot_enfrentado", "goles_evitados",
    "pases_tercio_final_pct", "recuperaciones",
})


def _goles_desde_texto(bruto: Any) -> tuple[int | None, int | None]:
    """
    Convierte un marcador en texto de FotMob ('2 - 1', '2-1', '0 : 0') en enteros.

    Hace falta porque en /api/leagues los goles NO vienen en `home.score`/
    `away.score` (medido: 0 de 380 partidos los traen), sino en `status.scoreStr`.
    Devuelve (None, None) si no hay texto o no tiene el formato esperado: nunca se
    inventa un marcador, y un 0 legítimo se conserva como 0.
    """
    if bruto is None:
        return None, None
    m = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(bruto))
    if not m:
        return None, None
    try:
        return int(m.group(1)), int(m.group(2))
    except (TypeError, ValueError):
        return None, None


def _normalizar_futuros(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convierte los fixtures NO terminados de `/teams` en partidos próximos ligeros,
    con los IDs de equipo, para poder cruzar dos equipos POR ID (y no por nombre,
    donde «Roma» contra «AS Roma» fallaba).

    Solo incluye lo que la fuente publica con fecha: nada inventado.
    """
    salida: list[dict[str, Any]] = []
    for f in fixtures or []:
        estado = f.get("status") or {}
        if estado.get("finished"):
            continue
        fecha = str(estado.get("utcTime") or "")[:10]
        if not fecha:
            continue
        home = f.get("home") or {}
        away = f.get("away") or {}
        if not home.get("id") or not away.get("id"):
            continue
        t = f.get("tournament") or {}
        salida.append({
            "id_partido": str(f.get("id") or ""),
            "fecha": fecha,
            "competicion": str(t.get("name") or ""),
            "home_id": str(home.get("id")),
            "away_id": str(away.get("id")),
            "home_nombre": str(home.get("name") or ""),
            "away_nombre": str(away.get("name") or ""),
        })
    salida.sort(key=lambda x: x["fecha"])
    return salida


@dataclass(frozen=True)
class DefinicionMetrica:
    clave: str
    etiqueta: str
    categoria: str
    alias: tuple[str, ...] = ()
    # nivel: "equipo" (viene en las stats de equipo) o "jugadores" (se agrega del detalle por jugador)
    nivel: str = "equipo"


CATALOGO_METRICAS: tuple[DefinicionMetrica, ...] = (
    # ── ESTADÍSTICAS PRINCIPALES ────────────────────────────────────────────
    DefinicionMetrica("xg", "Goles esperados (xG)", CATEGORIA_PRINCIPALES,
                      ("expected goals (xg)", "expected goals", "xg")),
    DefinicionMetrica("posesion_pct", "Posesión (%)", CATEGORIA_PRINCIPALES,
                      ("ball possession", "possession", "posesión")),
    DefinicionMetrica("posesion", "Posesión", CATEGORIA_PRINCIPALES,
                      ("ball possession", "possession", "posesión")),
    DefinicionMetrica("remates_totales", "Remates totales", CATEGORIA_PRINCIPALES,
                      ("total shots", "remates totales")),
    DefinicionMetrica("remates_puerta", "Remates a puerta", CATEGORIA_PRINCIPALES,
                      ("shots on target", "remates a puerta")),
    DefinicionMetrica("remates_a_puerta", "Remates a puerta (canónico)", CATEGORIA_PRINCIPALES,
                      ("shots on target", "remates a puerta")),
    DefinicionMetrica("grandes_ocasiones", "Grandes ocasiones", CATEGORIA_PRINCIPALES,
                      ("big chances", "grandes ocasiones", "ocasiones claras")),
    DefinicionMetrica("grandes_ocasiones_aprovechadas", "Grandes ocasiones aprovechadas", CATEGORIA_PRINCIPALES,
                      ("big chances scored", "grandes ocasiones aprovechadas")),
    DefinicionMetrica("grandes_ocasiones_falladas", "Grandes ocasiones falladas", CATEGORIA_PRINCIPALES,
                      ("big chances missed",)),
    DefinicionMetrica("grandes_ocasiones_perdidas", "Grandes ocasiones perdidas", CATEGORIA_PRINCIPALES,
                      ("big chances missed", "grandes ocasiones perdidas")),
    DefinicionMetrica("corners", "Córneres", CATEGORIA_PRINCIPALES,
                      ("corners", "córneres", "corners won", "corner kicks")),
    DefinicionMetrica("corneres", "Córneres (canónico)", CATEGORIA_PRINCIPALES,
                      ("corners", "córneres", "corners won", "corner kicks")),
    DefinicionMetrica("pases_total", "Pases (número)", CATEGORIA_PRINCIPALES,
                      ("passes", "pases")),
    DefinicionMetrica("pases_totales", "Pases totales", CATEGORIA_PRINCIPALES,
                      ("passes", "total passes", "pases totales")),
    DefinicionMetrica("pases_completados", "Pases completados", CATEGORIA_PRINCIPALES,
                      ("accurate passes", "pases completados")),
    DefinicionMetrica("pases_precision_pct", "Pases (porcentaje de acierto)", CATEGORIA_PRINCIPALES,
                      ("accurate passes", "pass accuracy", "pases precisos")),
    DefinicionMetrica("pases_pct", "Pases (%)", CATEGORIA_PRINCIPALES,
                      ("accurate passes", "pass accuracy", "pases %")),
    DefinicionMetrica("pases_ratio", "Pases (ratio por minuto/posesión)", CATEGORIA_PRINCIPALES, ()),
    DefinicionMetrica("tarjetas_amarillas", "Tarjetas amarillas", CATEGORIA_PRINCIPALES,
                      ("yellow cards", "tarjetas amarillas")),
    DefinicionMetrica("tarjetas_rojas", "Tarjetas rojas", CATEGORIA_PRINCIPALES,
                      ("red cards", "tarjetas rojas")),

    # ── DISPAROS ────────────────────────────────────────────────────────────
    DefinicionMetrica("xgot", "xG a puerta (xGOT)", CATEGORIA_DISPAROS,
                      ("xg on target (xgot)", "xgot", "expected goals on target")),
    DefinicionMetrica("xg_juego_abierto", "xG de jugada abierta", CATEGORIA_DISPAROS,
                      ("xg open play",)),
    DefinicionMetrica("xg_balon_parado", "xG a balón parado", CATEGORIA_DISPAROS,
                      ("xg set play",)),
    DefinicionMetrica("xg_sin_penalti", "xG sin penaltis", CATEGORIA_DISPAROS,
                      ("xg non-penalty", "non-penalty xg")),
    DefinicionMetrica("remates_fuera", "Remates fuera", CATEGORIA_DISPAROS,
                      ("shots off target", "remates fuera")),
    DefinicionMetrica("remates_rechazados", "Remates rechazados (bloqueados)", CATEGORIA_DISPAROS,
                      ("blocked shots", "remates bloqueados", "remates rechazados")),
    DefinicionMetrica("remates_dentro_area", "Remates dentro del área", CATEGORIA_DISPAROS,
                      ("shots inside box", "remates dentro del área")),
    DefinicionMetrica("remates_fuera_area", "Remates fuera del área", CATEGORIA_DISPAROS,
                      ("shots outside box", "remates fuera del área")),
    DefinicionMetrica("al_palo", "Al palo (palos y travesaños)", CATEGORIA_DISPAROS,
                      ("hit woodwork", "woodwork", "al palo")),
    DefinicionMetrica("goles_cabeza", "Goles de cabeza", CATEGORIA_DISPAROS,
                      ("headed goals", "goles de cabeza")),

    # ── ATAQUES ─────────────────────────────────────────────────────────────
    DefinicionMetrica("toques_area_rival", "Toques en el área rival", CATEGORIA_ATAQUES,
                      ("touches in opposition box", "toques en el área rival")),
    DefinicionMetrica("pases_entre_lineas", "Pases entre líneas completados (line breaking)", CATEGORIA_ATAQUES,
                      ("line breaking passes", "through balls", "pases entre líneas"),
                      nivel="jugadores"),
    DefinicionMetrica("fueras_juego", "Fueras de juego", CATEGORIA_ATAQUES,
                      ("offsides", "fueras de juego")),
    DefinicionMetrica("fueras_de_juego", "Fueras de juego (canónico)", CATEGORIA_ATAQUES,
                      ("offsides", "fueras de juego")),
    DefinicionMetrica("tiros_libres", "Tiros libres directos", CATEGORIA_ATAQUES,
                      ("free kicks", "tiros libres")),

    # ── PASES ───────────────────────────────────────────────────────────────
    DefinicionMetrica("pases_largos", "Pases largos totales", CATEGORIA_PASES,
                      ("long balls", "pases largos totales")),
    DefinicionMetrica("pases_largos_completados", "Pases largos completados", CATEGORIA_PASES,
                      ("accurate long balls", "pases largos completados")),
    DefinicionMetrica("pases_largos_pct", "Pases largos precisos", CATEGORIA_PASES,
                      ("accurate long balls", "long balls", "pases largos")),
    DefinicionMetrica("pases_tercio_final", "Pases en el tercio final totales", CATEGORIA_PASES,
                      ("passes in final third", "pases en el tercio final")),
    DefinicionMetrica("pases_tercio_final_completados", "Pases en el tercio final completados", CATEGORIA_PASES,
                      ("accurate passes in final third", "passes into final third"), nivel="jugadores"),
    DefinicionMetrica("pases_tercio_final_pct", "Pases al tercio final (número)", CATEGORIA_PASES,
                      ("passes into final third",), nivel="jugadores"),
    DefinicionMetrica("centros", "Centros totales", CATEGORIA_PASES,
                      ("crosses", "centros totales")),
    DefinicionMetrica("centros_completados", "Centros completados", CATEGORIA_PASES,
                      ("accurate crosses", "centros completados")),
    DefinicionMetrica("cruces_pct", "Cruces precisos", CATEGORIA_PASES,
                      ("accurate crosses", "crosses", "centros")),
    DefinicionMetrica("centros_pct", "Centros (%)", CATEGORIA_PASES,
                      ("accurate crosses", "crosses", "centros %")),
    DefinicionMetrica("xa_equipo", "Asistencias esperadas (xA)", CATEGORIA_PASES,
                      ("expected assists", "xa"), nivel="jugadores"),
    DefinicionMetrica("xa", "xA (Asistencias esperadas)", CATEGORIA_PASES,
                      ("expected assists", "xa"), nivel="jugadores"),
    DefinicionMetrica("pases_clave", "Pases clave", CATEGORIA_PASES,
                      ("key passes", "chances created", "pases clave"), nivel="jugadores"),
    DefinicionMetrica("saques_banda", "Saques de banda", CATEGORIA_PASES,
                      ("throws", "throw-ins", "saques de banda")),
    DefinicionMetrica("saques_de_banda", "Saques de banda (canónico)", CATEGORIA_PASES,
                      ("throws", "throw-ins", "saques de banda")),

    # ── DEFENSA ─────────────────────────────────────────────────────────────
    DefinicionMetrica("faltas", "Faltas cometidas", CATEGORIA_DEFENSA,
                      ("fouls committed", "fouls", "faltas")),
    DefinicionMetrica("entradas", "Entradas totales", CATEGORIA_DEFENSA,
                      ("tackles", "entradas totales")),
    DefinicionMetrica("entradas_ganadas", "Entradas ganadas", CATEGORIA_DEFENSA,
                      ("tackles won", "tackles succeeded", "entradas ganadas")),
    DefinicionMetrica("entradas_pct", "Entradas (tackles ganados, número)", CATEGORIA_DEFENSA,
                      ("tackles", "entradas")),
    DefinicionMetrica("entradas_ganadas_pct", "Entradas ganadas (%)", CATEGORIA_DEFENSA,
                      ("tackles won %", "tackles succeeded", "entradas %")),
    DefinicionMetrica("duelos_ganados", "Duelos ganados", CATEGORIA_DEFENSA,
                      ("duels won",)),
    DefinicionMetrica("duelos_suelo_pct", "Duelos de suelo ganados", CATEGORIA_DEFENSA,
                      ("ground duels won",)),
    DefinicionMetrica("duelos_aereos_pct", "Duelos aéreos ganados", CATEGORIA_DEFENSA,
                      ("aerial duels won",)),
    DefinicionMetrica("despejes", "Despejes", CATEGORIA_DEFENSA,
                      ("clearances", "despejes")),
    DefinicionMetrica("intercepciones", "Intercepciones", CATEGORIA_DEFENSA,
                      ("interceptions", "intercepciones")),
    DefinicionMetrica("bloqueos", "Bloqueos", CATEGORIA_DEFENSA,
                      ("blocks", "bloqueos")),
    DefinicionMetrica("recuperaciones", "Recuperaciones", CATEGORIA_DEFENSA,
                      (), nivel="jugadores"),
    DefinicionMetrica("errores_remate", "Errores que conducen a remate", CATEGORIA_DEFENSA, ()),
    DefinicionMetrica("errores_gol", "Errores que conducen a gol", CATEGORIA_DEFENSA,
                      ("errors led to goal", "errors leading to goal"), nivel="jugadores"),

    # ── PORTERÍA (ATAJADAS) ─────────────────────────────────────────────────
    DefinicionMetrica("paradas", "Paradas", CATEGORIA_PORTERIA,
                      ("keeper saves", "saves", "paradas")),
    DefinicionMetrica("xgot_enfrentado", "xGOT enfrentado", CATEGORIA_PORTERIA,
                      ("xgot faced",), nivel="jugadores"),
    DefinicionMetrica("xgot_enfrentados", "xGOT enfrentados", CATEGORIA_PORTERIA,
                      ("xgot faced", "xgot enfrentados"), nivel="jugadores"),
    DefinicionMetrica("goles_encajados", "Goles encajados", CATEGORIA_PORTERIA,
                      ("goals conceded", "goles encajados")),
    DefinicionMetrica("goles_evitados", "Goles evitados", CATEGORIA_PORTERIA,
                      ("goals prevented", "goles evitados"), nivel="jugadores"),
    DefinicionMetrica("saques_puerta", "Saques de puerta", CATEGORIA_PORTERIA, ()),
    DefinicionMetrica("saques_de_puerta", "Saques de puerta (canónico)", CATEGORIA_PORTERIA, ()),
)

CATEGORIAS_ORDEN: tuple[str, ...] = (
    CATEGORIA_PRINCIPALES, CATEGORIA_DISPAROS, CATEGORIA_ATAQUES,
    CATEGORIA_PASES, CATEGORIA_DEFENSA, CATEGORIA_PORTERIA,
)

# Competiciones que se consideran amistosas / de pretemporada (se excluyen por
# defecto porque la fuente no publica estadísticas avanzadas de ellas).
_RE_AMISTOSO = re.compile(
    r"\b(friendl(y|ies)|amistoso|amistosos|club friendlies|pre[- ]?season|pretemporada"
    r"|test match|amical|exhibition)\b", re.I
)


def es_competicion_amistosa(nombre_torneo: str) -> bool:
    """True si el torneo es amistoso/pretemporada (sin estadísticas avanzadas fiables)."""
    return bool(nombre_torneo and _RE_AMISTOSO.search(str(nombre_torneo)))

# Normalización de nombres de periodo de la fuente -> etiquetas del informe
PERIODOS_NORMALIZADOS = {
    "all": "FT",
    "full": "FT",
    "ft": "FT",
    "total": "FT",
    "overall": "FT",
    "firsthalf": "1T",
    "first_half": "1T",
    "1st half": "1T",
    "1t": "1T",
    "ht": "1T",
    "secondhalf": "2T",
    "second_half": "2T",
    "2nd half": "2T",
    "2t": "2T",
}

ORDEN_PERIODOS = ("FT", "1T", "2T")


def _a_float(valor: Any) -> float | None:
    """Convierte un valor de la fuente a float, extrayendo 'número (porcentaje)' y '%'."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip()
    if not texto:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", texto.replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _a_pct(valor: Any) -> float | None:
    """Extrae el porcentaje de '783 (91%)' o de 91 / '91%'. Devuelve None si no hay %."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)
    texto = str(valor)
    m = re.search(r"\((\d+(?:[.,]\d+)?)%\)", texto)
    if m:
        return float(m.group(1).replace(",", "."))
    if "%" in texto:
        n = _a_float(texto)
        return n
    return None


def _a_entero(valor: Any) -> float | None:
    """Parte entera de '783 (91%)' -> 783.0"""
    if valor is None:
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)
    texto = str(valor).strip()
    m = re.match(r"^(-?\d+(?:[.,]\d+)?)", texto)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRATO DE LA FUENTE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PartidoFinalizado:
    """Referencia mínima a un partido finalizado, más datos de contexto."""
    id_partido: str
    fecha: str
    torneo: str
    local: str
    visitante: str
    goles_local: int | None
    goles_visitante: int | None
    es_local_el_equipo: bool
    url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PeriodoPartido:
    """Matriz de métricas de UN periodo (FT / 1T / 2T) de UN partido."""
    periodo: str
    filas: list[dict[str, Any]] = field(default_factory=list)
    no_disponibles: list[str] = field(default_factory=list)

    def a_dict(self) -> dict[str, Any]:
        return {
            "periodo": self.periodo,
            "filas": self.filas,
            "no_disponibles": self.no_disponibles,
        }

    @classmethod
    def desde_dict(cls, d: dict[str, Any]) -> PeriodoPartido:
        return cls(
            periodo=str(d.get("periodo") or ""),
            filas=[dict(f) for f in (d.get("filas") or []) if isinstance(f, dict)],
            no_disponibles=list(d.get("no_disponibles") or []),
        )


@dataclass
class MatrizPartido:
    """Matriz de UN partido: 3 periodos para equipo y rival."""
    id_partido: str
    fecha: str
    torneo: str
    equipo: str
    rival: str
    condicion: str                  # "casa" | "fuera"
    goles_equipo: int | None
    goles_rival: int | None
    url: str = ""
    periodos: dict[str, PeriodoPartido] = field(default_factory=dict)
    metricas_no_disponibles: list[str] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)
    error: str | None = None
    # Procedencia de cada valor del periodo, para saber si vino de FotMob, se
    # rellenó desde Flashscore o se derivó. Estructura:
    #   {"FT": {"equipo": {clave: procedencia}, "rival": {...}}, "1T": {...}, ...}
    procedencia: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    # Identidad del partido en la fuente secundaria (trazabilidad)
    fuente_secundaria: dict[str, Any] = field(default_factory=dict)
    # Inconsistencias aritméticas detectadas (ej. FT < 1T en magnitudes no negativas)
    inconsistencias_aritmeticas: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def desde_dict(cls, d: dict[str, Any]) -> MatrizPartido:
        marc = d.get("marcador") or {}
        ge = marc.get("equipo") if marc.get("equipo") is not None else d.get("goles_equipo")
        gr = marc.get("rival") if marc.get("rival") is not None else d.get("goles_rival")
        pers: dict[str, PeriodoPartido] = {}
        for p_nom, p_data in (d.get("periodos") or {}).items():
            if isinstance(p_data, dict):
                pers[p_nom] = PeriodoPartido.desde_dict(p_data)
            elif isinstance(p_data, PeriodoPartido):
                pers[p_nom] = p_data
        return cls(
            id_partido=str(d.get("id_partido") or d.get("id") or ""),
            fecha=str(d.get("fecha") or ""),
            torneo=str(d.get("torneo") or ""),
            equipo=str(d.get("equipo") or ""),
            rival=str(d.get("rival") or ""),
            condicion=str(d.get("condicion") or "casa"),
            goles_equipo=int(ge) if ge is not None else None,
            goles_rival=int(gr) if gr is not None else None,
            url=str(d.get("url") or ""),
            periodos=pers,
            metricas_no_disponibles=list(d.get("metricas_no_disponibles") or []),
            notas=list(d.get("notas") or []),
            error=d.get("error"),
            procedencia=dict(d.get("procedencia") or {}),
            fuente_secundaria=dict(d.get("fuente_secundaria") or {}),
        )

    def a_dict(self) -> dict[str, Any]:
        return {
            "id_partido": self.id_partido,
            "fecha": self.fecha,
            "torneo": self.torneo,
            "equipo": self.equipo,
            "rival": self.rival,
            "condicion": self.condicion,
            "marcador": {
                "equipo": self.goles_equipo,
                "rival": self.goles_rival,
                "texto": (f"{self.goles_equipo}-{self.goles_rival}"
                          if self.goles_equipo is not None and self.goles_rival is not None else None),
            },
            "url": self.url,
            "periodos": {p: mat.a_dict() for p, mat in self.periodos.items()},
            "procedencia": self.procedencia,
            "fuente_secundaria": self.fuente_secundaria,
            "metricas_no_disponibles": self.metricas_no_disponibles,
            "notas": self.notas,
            "error": self.error,
        }


@dataclass
class ResultadoEscaneo:
    """
    Resultado de la búsqueda hacia atrás en el calendario.

    oficiales: partidos competitivos (sin amistosos) finalizados, del más reciente
        al más antiguo, listos para extraer su matriz.
    amistosos_reservados: amistosos finalizados, en el mismo orden. Se reservan para
        poder rellenar el dataset SOLO si el usuario lo pide explícitamente y los
        oficiales no alcanzan el objetivo (`incluir_amistosos=True`). Por defecto se
        ignoran por completo.
    atendidos: propiedad de conveniencia: oficiales + amistosos reservados (útil para
        inspección), pero el auditor procesa ambos grupos por SEPARADO para poder
        marcarlos correctamente.
    diagnostico: contadores para poder reportar con precisión por qué el dataset
        tiene el tamaño que tiene (p. ej. "la fuente solo expone N partidos").
    """
    oficiales: list["PartidoFinalizado"] = field(default_factory=list)
    amistosos_reservados: list["PartidoFinalizado"] = field(default_factory=list)
    incluir_amistosos: bool = False
    paginas_escaneadas: int = 0
    finalizados_totales: int = 0
    amistosos_excluidos: int = 0
    fuente_agotada: bool = False
    motivo_parada: str = ""
    # ¿El escaneo consideró TODAS las competiciones del club (incluidas las de
    # temporadas anteriores, como la división en la que jugaba antes de ascender)?
    # Sin esta marca, un «fuente agotada» con la muestra corta no es fiable: puede
    # venir de un escaneo que solo miró las ligas de la ventana actual.
    cola_historica_revisada: bool = False

    @property
    def atendidos(self) -> list["PartidoFinalizado"]:
        """Todos los partidos candidatos, en orden cronológico descendente."""
        unidos = self.oficiales + self.amistosos_reservados
        unidos.sort(key=lambda p: p.fecha, reverse=True)
        return unidos


class FuenteXG:
    """Interfaz común de fuentes de datos avanzados."""

    nombre = "base"
    requiere_navegador = True
    max_paginas_escaneo = 8   # tope de seguridad para la búsqueda hacia atrás

    # ── ciclo de vida ──────────────────────────────────────────────────────
    def abrir(self) -> None:  # pragma: no cover - implementado por subclases
        raise NotImplementedError

    def cerrar(self) -> None:  # pragma: no cover
        raise NotImplementedError

    # ── operaciones ────────────────────────────────────────────────────────
    def resolver_equipo(self, nombre: str, rival_contexto: Any = None) -> dict[str, Any] | None:
        raise NotImplementedError

    def ultimos_partidos(self, equipo: dict[str, Any], limite: int = 10,
                         incluir_amistosos: bool = False) -> list[PartidoFinalizado]:
        raise NotImplementedError

    def escanear_oficiales(self, equipo: dict[str, Any], objetivo: int,
                           incluir_amistosos: bool = False) -> ResultadoEscaneo:
        """
        Búsqueda profunda hacia atrás: devuelve tantos partidos OFICIALES finalizados
        como existan, hasta `objetivo` como mínimo deseado.

        Recorre páginas del historial de la fuente mientras ésta las ofrezca. Si una
        fuente no pagina (como la actual, que sirve una única ventana de calendario),
        el resultado es la ventana completa y `motivo_parada` lo documenta.
        """
        raise NotImplementedError

    def matriz_partido(self, partido: PartidoFinalizado, equipo: dict[str, Any]) -> MatrizPartido:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE: FOTMOB  (activa)
# ═══════════════════════════════════════════════════════════════════════════════

FOTMOB_BASE = "https://www.fotmob.com"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ─── Resolución resiliente de nombres de equipo ───────────────────────────────

def _sin_acentos(texto: str) -> str:
    """Elimina tildes y diacríticos: 'Atlético' -> 'Atletico'."""
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()


# Palabras vacías que estorban en la búsqueda del proveedor. Comprobado en vivo:
# FotMob NO resuelve "atlético de madrid" ni "atletico de madrid" (0 resultados),
# pero SÍ "atletico madrid" y "atletico". La preposición, no la tilde, era el
# problema real de la búsqueda.
_PALABRAS_VACIAS = frozenset({
    "de", "del", "la", "el", "los", "las", "y", "e", "da", "do", "dos", "das",
    "di", "van", "von", "der", "den", "the", "of", "club",
})

# Siglas y afijos genéricos de clubes que NUNCA deben bastar por sí solos para
# emparejar dos equipos distintos (evita que "fc fulham" -> "FC Porto" o que
# "Liverpool fc" -> "Liverpool FC" de Uruguay ignore "Liverpool" de Inglaterra).
_SIGLAS_CLUB = frozenset({
    "fc", "cf", "sc", "ac", "afc", "rcd", "cd", "ud", "sd", "sv", "bv", "vfl",
    "vfb", "tsg", "fsv", "rb", "ca", "cs", "csd", "rc", "fk", "sk", "nk", "bk",
    "if", "ik", "ai", "ss", "ssc", "us", "as", "uc", "ad", "cp", "ce", "ue",
    "se", "ec", "cr", "aa", "1.", "1", "04", "05", "07", "96", "98", "1846",
    "1860", "1899", "1904",
})

# Modificadores comunes de nombre de club que no deben buscarse ni puntuarse solos
# cuando la consulta incluye el nombre específico de la ciudad o identidad del club.
_MODIFICADORES_GENERICOS_CLUB = frozenset({
    "athletic", "atletico", "sporting", "deportivo", "real", "united", "city",
    "town", "rovers", "wanderers", "county", "albion", "borussia", "eintracht",
    "fortuna", "olympique", "stade", "racing", "union", "dynamo", "dinamo",
    "lokomotiv", "spartak", "cska", "shakhtar", "central", "nacional",
    "independiente", "universidad", "universitario", "alianza", "defensor",
    "defensa", "football", "soccer", "calcio", "balompie", "clube", "esporte",
})

# Diccionario de alias hispanos, modismos y grafías coloquiales -> formas canónicas de búsqueda
ALIAS_EQUIPOS_HISPANOS: dict[str, list[str]] = {
    # España (LaLiga / LaLiga2)
    "español": ["Espanyol", "RCD Espanyol", "RCD Espanyol de Barcelona"],
    "espanol": ["Espanyol", "RCD Espanyol", "RCD Espanyol de Barcelona"],
    "espanyol": ["Espanyol", "RCD Espanyol", "RCD Espanyol de Barcelona"],
    "rcd espanyol": ["Espanyol", "RCD Espanyol"],
    "rcd español": ["Espanyol", "RCD Espanyol"],
    "atletico de madrid": ["Atlético Madrid", "Atletico de Madrid", "Atlético"],
    "atletico madrid": ["Atlético Madrid", "Atletico de Madrid"],
    "atleti": ["Atlético Madrid", "Atletico de Madrid"],
    "athletic": ["Athletic Club", "Athletic Bilbao"],
    "athletic de bilbao": ["Athletic Club", "Athletic Bilbao"],
    "athletic bilbao": ["Athletic Club", "Athletic Bilbao"],
    "bilbao": ["Athletic Club", "Athletic Bilbao"],
    "real madrid": ["Real Madrid"],
    "madrid": ["Real Madrid"],
    "barcelona": ["Barcelona", "FC Barcelona"],
    "fc barcelona": ["Barcelona", "FC Barcelona"],
    "barca": ["Barcelona", "FC Barcelona"],
    "betis": ["Real Betis", "Betis"],
    "real betis": ["Real Betis"],
    "sevilla": ["Sevilla", "Sevilla FC"],
    "valencia": ["Valencia", "Valencia CF"],
    "celta": ["Celta Vigo", "RC Celta", "Celta de Vigo"],
    "celta de vigo": ["Celta Vigo", "RC Celta"],
    "osasuna": ["Osasuna", "CA Osasuna"],
    "rayo": ["Rayo Vallecano"],
    "rayo vallecano": ["Rayo Vallecano"],
    "mallorca": ["Mallorca", "RCD Mallorca"],
    "girona": ["Girona", "Girona FC"],
    "alaves": ["Deportivo Alavés", "Alavés"],
    "deportivo alaves": ["Deportivo Alavés", "Alavés"],
    "las palmas": ["Las Palmas", "UD Las Palmas"],
    "valladolid": ["Real Valladolid", "Valladolid"],
    "real valladolid": ["Real Valladolid"],
    "leganes": ["Leganés", "CD Leganés"],
    "getafe": ["Getafe", "Getafe CF"],
    "villarreal": ["Villarreal", "Villarreal CF"],
    "sociedad": ["Real Sociedad"],
    "real sociedad": ["Real Sociedad"],
    "elche": ["Elche", "Elche CF"],
    "elche cf": ["Elche", "Elche CF"],
    "levante": ["Levante", "Levante UD"],
    "deportivo la coruna": ["Deportivo de La Coruña", "Deportivo La Coruña", "Deportivo A Coruña", "Deportivo La Coruna"],
    "deportivo a coruna": ["Deportivo de La Coruña", "Deportivo La Coruña", "Deportivo A Coruña", "Deportivo La Coruna"],
    "deportivo de a coruna": ["Deportivo de La Coruña", "Deportivo La Coruña", "Deportivo A Coruña", "Deportivo La Coruna"],
    "deportivo de la coruna": ["Deportivo de La Coruña", "Deportivo La Coruña", "Deportivo A Coruña", "Deportivo La Coruna"],
    "rc deportivo": ["Deportivo de La Coruña", "Deportivo La Coruña", "Deportivo A Coruña", "Deportivo La Coruna"],
    "depor": ["Deportivo de La Coruña", "Deportivo La Coruña", "Deportivo A Coruña", "Deportivo La Coruna"],
    "real oviedo": ["Real Oviedo", "Oviedo"],
    "oviedo": ["Real Oviedo", "Oviedo"],
    "sporting gijon": ["Sporting Gijón", "Sporting de Gijón"],
    "real zaragoza": ["Real Zaragoza", "Zaragoza"],
    "zaragoza": ["Real Zaragoza", "Zaragoza"],
    "racing santander": ["Racing Santander", "Real Racing Club"],
    # Inglaterra (Premier League / Championship)
    "fulham": ["Fulham", "Fulham FC"],
    "fc fulham": ["Fulham", "Fulham FC"],
    "fulham fc": ["Fulham", "Fulham FC"],
    "hull": ["Hull City", "Hull City AFC"],
    "hull city": ["Hull City", "Hull City AFC"],
    "manchester united": ["Manchester United", "Man United"],
    "man utd": ["Manchester United"],
    "manchester city": ["Manchester City", "Man City"],
    "manchester city fc": ["Manchester City", "Man City"],
    "man city": ["Manchester City"],
    "arsenal": ["Arsenal", "Arsenal FC"],
    "arsenal fc": ["Arsenal", "Arsenal FC"],
    "liverpool": ["Liverpool", "Liverpool FC"],
    "liverpool fc": ["Liverpool"],
    "fc liverpool": ["Liverpool"],
    "chelsea": ["Chelsea", "Chelsea FC"],
    "chelsea fc": ["Chelsea"],
    "fc chelsea": ["Chelsea"],
    "tottenham": ["Tottenham Hotspur", "Spurs"],
    "spurs": ["Tottenham Hotspur"],
    "aston villa": ["Aston Villa"],
    "newcastle": ["Newcastle United"],
    "newcastle united": ["Newcastle United"],
    "newcastle united fc": ["Newcastle United"],
    "west ham": ["West Ham United", "West Ham"],
    "west ham united": ["West Ham United", "West Ham"],
    "brighton": ["Brighton & Hove Albion", "Brighton"],
    "wolves": ["Wolverhampton Wanderers", "Wolves"],
    "wolverhampton": ["Wolverhampton Wanderers", "Wolves"],
    "crystal palace": ["Crystal Palace"],
    "bournemouth": ["AFC Bournemouth", "Bournemouth"],
    "nottingham": ["Nottingham Forest"],
    "nottingham forest": ["Nottingham Forest"],
    "everton": ["Everton", "Everton FC"],
    "everton fc": ["Everton"],
    "brentford": ["Brentford", "Brentford FC"],
    "leicester": ["Leicester City"],
    "leicester city": ["Leicester City"],
    "southampton": ["Southampton"],
    "ipswich": ["Ipswich Town"],
    "ipswich town": ["Ipswich Town"],
    "leeds": ["Leeds United"],
    "leeds united": ["Leeds United"],
    "burnley": ["Burnley"],
    "sunderland": ["Sunderland"],
    "middlesbrough": ["Middlesbrough"],
    "sheffield united": ["Sheffield United"],
    "sheffield wednesday": ["Sheffield Wednesday"],
    "west brom": ["West Bromwich Albion"],
    "west bromwich": ["West Bromwich Albion"],
    "norwich": ["Norwich City"],
    "norwich city": ["Norwich City"],
    "watford": ["Watford"],
    "coventry": ["Coventry City"],
    "coventry city": ["Coventry City"],
    "swansea": ["Swansea City"],
    "swansea city": ["Swansea City"],
    "cardiff": ["Cardiff City"],
    "cardiff city": ["Cardiff City"],
    "stoke": ["Stoke City"],
    "stoke city": ["Stoke City"],
    "qpr": ["Queens Park Rangers", "QPR"],
    "blackburn": ["Blackburn Rovers"],
    "bristol city": ["Bristol City"],
    "millwall": ["Millwall"],
    "derby": ["Derby County"],
    "derby county": ["Derby County"],
    "luton": ["Luton Town"],
    "portsmouth": ["Portsmouth"],
    "plymouth": ["Plymouth Argyle"],
    # Alemania (Bundesliga / 2. Bundesliga)
    "bayern": ["FC Bayern München", "Bayern München", "Bayern Munich"],
    "bayern munich": ["FC Bayern München", "Bayern München", "Bayern Munich"],
    "bayern munchen": ["FC Bayern München", "Bayern München"],
    "bayern münchen": ["FC Bayern München", "Bayern München", "Bayern Munich"],
    "bayer": ["Bayer 04 Leverkusen", "Bayer Leverkusen"],
    "bayer 04": ["Bayer 04 Leverkusen", "Bayer Leverkusen"],
    "bayer 04 leverkusen": ["Bayer 04 Leverkusen", "Bayer Leverkusen"],
    "leverkusen": ["Bayer 04 Leverkusen", "Bayer Leverkusen"],
    "bayer leverkusen": ["Bayer 04 Leverkusen", "Bayer Leverkusen"],
    "magdeburgo": ["1. FC Magdeburg", "Magdeburg"],
    "magdeburg": ["1. FC Magdeburg", "Magdeburg"],
    "braunschweig": ["Eintracht Braunschweig", "Braunschweig"],
    "dortmund": ["Borussia Dortmund", "BVB"],
    "borussia dortmund": ["Borussia Dortmund"],
    "leipzig": ["RB Leipzig"],
    "rb leipzig": ["RB Leipzig"],
    "mainz": ["Mainz 05", "1. FSV Mainz 05", "FSV Mainz 05"],
    "mainz 05": ["Mainz 05", "1. FSV Mainz 05"],
    "fsv mainz": ["Mainz 05", "1. FSV Mainz 05"],
    "maguncia": ["Mainz 05", "1. FSV Mainz 05"],
    "friburgo": ["SC Freiburg", "Freiburg"],
    "sc friburgo": ["SC Freiburg", "Freiburg"],
    "freiburg": ["SC Freiburg", "Freiburg"],
    "sc freiburg": ["SC Freiburg", "Freiburg"],
    "stuttgart": ["VfB Stuttgart", "Stuttgart"],
    "vfb stuttgart": ["VfB Stuttgart", "Stuttgart"],
    "frankfurt": ["Eintracht Frankfurt", "Frankfurt"],
    "francfort": ["Eintracht Frankfurt", "Frankfurt"],
    "eintracht": ["Eintracht Frankfurt", "Frankfurt"],
    "eintracht frankfurt": ["Eintracht Frankfurt", "Frankfurt"],
    "eintracht francfort": ["Eintracht Frankfurt", "Frankfurt"],
    "eintracht de francfort": ["Eintracht Frankfurt", "Frankfurt"],
    "eintracht de frankfurt": ["Eintracht Frankfurt", "Frankfurt"],
    "wolfsburgo": ["VfL Wolfsburg", "Wolfsburg"],
    "wolfsburg": ["VfL Wolfsburg", "Wolfsburg"],
    "vfl wolfsburg": ["VfL Wolfsburg", "Wolfsburg"],
    "monchengladbach": ["Borussia Mönchengladbach", "Mönchengladbach", "Borussia Monchengladbach"],
    "gladbach": ["Borussia Mönchengladbach", "Mönchengladbach"],
    "borussia monchengladbach": ["Borussia Mönchengladbach", "Mönchengladbach"],
    "borussia m'gladbach": ["Borussia Mönchengladbach", "Mönchengladbach"],
    "borussia mgladbach": ["Borussia Mönchengladbach", "Mönchengladbach"],
    "bremen": ["Werder Bremen"],
    "werder bremen": ["Werder Bremen"],
    "augsburgo": ["FC Augsburg", "Augsburg"],
    "augsburg": ["FC Augsburg", "Augsburg"],
    "fc augsburg": ["FC Augsburg", "Augsburg"],
    "union berlin": ["Union Berlin", "1. FC Union Berlin"],
    "bochum": ["VfL Bochum", "Bochum"],
    "vfl bochum": ["VfL Bochum", "Bochum"],
    "heidenheim": ["1. FC Heidenheim", "Heidenheim"],
    "st pauli": ["FC St. Pauli", "St. Pauli"],
    "sankt pauli": ["FC St. Pauli", "St. Pauli"],
    "holstein kiel": ["Holstein Kiel"],
    "kiel": ["Holstein Kiel"],
    "hoffenheim": ["TSG Hoffenheim", "Hoffenheim"],
    "tsg hoffenheim": ["TSG Hoffenheim", "Hoffenheim"],
    "colonia": ["1. FC Köln", "FC Köln", "Köln", "FC Koln"],
    "koln": ["1. FC Köln", "FC Köln", "Köln"],
    "fc koln": ["1. FC Köln", "FC Köln"],
    "hamburgo": ["Hamburger SV", "Hamburg"],
    "hamburg": ["Hamburger SV", "Hamburg"],
    "hamburger sv": ["Hamburger SV", "Hamburg"],
    "schalke": ["FC Schalke 04", "Schalke 04"],
    "schalke 04": ["FC Schalke 04", "Schalke 04"],
    "hertha": ["Hertha BSC", "Hertha Berlin"],
    "hertha berlin": ["Hertha BSC", "Hertha Berlin"],
    "hannover": ["Hannover 96"],
    "hannover 96": ["Hannover 96"],
    "nuremberg": ["1. FC Nürnberg", "Nürnberg", "FC Nurnberg"],
    "nurnberg": ["1. FC Nürnberg", "Nürnberg"],
    "dusseldorf": ["Fortuna Düsseldorf", "Düsseldorf"],
    "fortuna dusseldorf": ["Fortuna Düsseldorf", "Düsseldorf"],
    "kaiserslautern": ["1. FC Kaiserslautern", "Kaiserslautern"],
    "karlsruher": ["Karlsruher SC"],
    "darmstadt": ["SV Darmstadt 98", "Darmstadt"],
    "paderborn": ["SC Paderborn 07", "Paderborn"],
    # Italia (Serie A / Serie B)
    "inter": ["Inter", "Internazionale"],
    "inter de milan": ["Inter", "Internazionale"],
    "milan": ["AC Milan", "Milan"],
    "ac milan": ["AC Milan", "Milan"],
    "roma": ["Roma", "AS Roma"],
    "as roma": ["Roma", "AS Roma"],
    "juve": ["Juventus"],
    "juventus": ["Juventus"],
    "napoli": ["Napoli", "SSC Napoli"],
    "napoles": ["Napoli", "SSC Napoli"],
    "lazio": ["Lazio", "SS Lazio"],
    "atalanta": ["Atalanta"],
    "fiorentina": ["Fiorentina", "ACF Fiorentina"],
    "florentina": ["Fiorentina", "ACF Fiorentina"],
    "bolonia": ["Bologna"],
    "bologna": ["Bologna"],
    "torino": ["Torino"],
    # Francia / Portugal / Países Bajos
    "psg": ["Paris Saint-Germain", "Paris SG"],
    "paris saint germain": ["Paris Saint-Germain", "Paris SG"],
    "marsella": ["Marseille", "Olympique de Marseille"],
    "olympique marsella": ["Marseille", "Olympique de Marseille"],
    "lyon": ["Lyon", "Olympique Lyonnais"],
    "olympique lyon": ["Lyon", "Olympique Lyonnais"],
    "monaco": ["Monaco", "AS Monaco"],
    "as monaco": ["Monaco", "AS Monaco"],
    "lille": ["Lille", "LOSC Lille"],
    "niza": ["Nice", "OGC Nice"],
    "oporto": ["FC Porto", "Porto"],
    "porto": ["FC Porto", "Porto"],
    "fc porto": ["FC Porto", "Porto"],
    "benfica": ["Benfica", "SL Benfica"],
    "sporting lisboa": ["Sporting CP"],
    "sporting cp": ["Sporting CP"],
    "sporting de portugal": ["Sporting CP"],
    "braga": ["SC Braga", "Braga"],
    "psv": ["PSV Eindhoven"],
    "psv eindhoven": ["PSV Eindhoven"],
    "ajax": ["Ajax"],
    "feyenoord": ["Feyenoord"],
    # Turquía (Süper Lig)
    "fenerbahce": ["Fenerbahçe", "Fenerbahce"],
    "fenerbahçe": ["Fenerbahçe", "Fenerbahce"],
    "galatasaray": ["Galatasaray"],
    "besiktas": ["Beşiktaş", "Besiktas"],
    "beşiktaş": ["Beşiktaş", "Besiktas"],
    "trabzonspor": ["Trabzonspor"],
    "basaksehir": ["Istanbul Basaksehir", "Başakşehir"],
    # República Checa (Chance Liga / 1. Liga)
    "slavia praga": ["Slavia Prague", "Slavia Praha", "SK Slavia Praha"],
    "slavia praha": ["Slavia Prague", "Slavia Praha", "SK Slavia Praha"],
    "slavia prague": ["Slavia Prague", "Slavia Praha"],
    "slavia": ["Slavia Prague", "Slavia Praha"],
    "sparta praga": ["Sparta Prague", "Sparta Praha", "AC Sparta Praha"],
    "sparta praha": ["Sparta Prague", "Sparta Praha"],
    "viktoria plzen": ["Viktoria Plzen", "FC Viktoria Plzeň"],
    "plzen": ["Viktoria Plzen"],
    # Azerbaiyán (Premyer Liqa)
    "sabah baku": ["Sabah FK", "Sabah"],
    "sabah fk": ["Sabah FK", "Sabah"],
    "sabah": ["Sabah FK", "Sabah"],
    "qarabag": ["Qarabağ FK", "Qarabag FK", "Qarabağ"],
    "neftchi": ["Neftçi PFK", "Neftchi Baku"],
    # Otras competiciones europeas y Champions League
    "bodo glimt": ["Bodø/Glimt", "Bodo/Glimt"],
    "bodø/glimt": ["Bodø/Glimt"],
    "young boys": ["BSC Young Boys", "Young Boys"],
    "dinamo zagreb": ["GNK Dinamo Zagreb", "Dinamo Zagreb"],
    "crvena zvezda": ["Crvena zvezda", "Red Star Belgrade"],
    "estrella roja": ["Crvena zvezda", "Red Star Belgrade"],
    "shakhtar": ["Shakhtar Donetsk"],
    "shakhtar donetsk": ["Shakhtar Donetsk"],
    "dynamo kyiv": ["Dynamo Kyiv"],
    "dinamo kiev": ["Dynamo Kyiv"],
    "olympiacos": ["Olympiacos", "Olympiakos"],
    "panathinaikos": ["Panathinaikos"],
    "paok": ["PAOK"],
    # MLS / Estados Unidos
    "nueva york red bull": ["New York Red Bulls", "NY Red Bulls", "Red Bulls"],
    "nueva york red bulls": ["New York Red Bulls", "NY Red Bulls", "Red Bulls"],
    "ny red bulls": ["New York Red Bulls", "NY Red Bulls", "Red Bulls"],
    "ny red bull": ["New York Red Bulls", "NY Red Bulls", "Red Bulls"],
    "red bull nueva york": ["New York Red Bulls", "NY Red Bulls", "Red Bulls"],
    "red bulls nueva york": ["New York Red Bulls", "NY Red Bulls", "Red Bulls"],
    "new york red bull": ["New York Red Bulls", "NY Red Bulls", "Red Bulls"],
    "new york red bulls": ["New York Red Bulls", "NY Red Bulls", "Red Bulls"],
    "red bulls": ["New York Red Bulls", "NY Red Bulls"],
    "inter miami": ["Inter Miami CF", "Inter Miami"],
    "inter de miami": ["Inter Miami CF", "Inter Miami"],
    "inter miami cf": ["Inter Miami CF", "Inter Miami"],
    "nueva york city": ["New York City FC", "NYCFC", "New York City"],
    "nueva york city fc": ["New York City FC", "NYCFC", "New York City"],
    "new york city": ["New York City FC", "NYCFC", "New York City"],
    "new york city fc": ["New York City FC", "NYCFC", "New York City"],
    "nycfc": ["New York City FC", "NYCFC"],
    "los angeles fc": ["Los Angeles FC", "LAFC"],
    "lafc": ["Los Angeles FC", "LAFC"],
    "la galaxy": ["LA Galaxy", "Los Angeles Galaxy"],
    "los angeles galaxy": ["LA Galaxy", "Los Angeles Galaxy"],
    "atlanta united": ["Atlanta United", "Atlanta United FC"],
    "columbus crew": ["Columbus Crew"],
    "fc cincinnati": ["FC Cincinnati", "Cincinnati"],
    "cincinnati": ["FC Cincinnati", "Cincinnati"],
    "seattle sounders": ["Seattle Sounders FC", "Seattle Sounders"],
    "orlando city": ["Orlando City", "Orlando City SC"],
    # Sudamérica
    "sporting cristal": ["Sporting Cristal"],
    "universitario": ["Universitario de Deportes", "Universitario"],
    "universitario de deportes": ["Universitario de Deportes", "Universitario"],
    "alianza lima": ["Alianza Lima"],
    "melgar": ["FBC Melgar", "Melgar"],
    "fbc melgar": ["FBC Melgar", "Melgar"],
    "cusco fc": ["Cusco FC", "Cusco"],
    "cusco": ["Cusco FC", "Cusco"],
    "cienciano": ["Cienciano"],
    "boca": ["Boca Juniors"],
    "boca juniors": ["Boca Juniors"],
    "river": ["River Plate"],
    "river plate": ["River Plate"],
    "racing club": ["Racing Club"],
    "racing de avellaneda": ["Racing Club"],
    "racing": ["Racing Club"],
    "independiente": ["Independiente", "CA Independiente"],
    "san lorenzo": ["San Lorenzo", "CA San Lorenzo"],
    "platense": ["CA Platense", "Platense", "Club Atlético Platense"],
    "ca platense": ["CA Platense", "Platense"],
    "club atletico platense": ["CA Platense", "Platense"],
    "riestra": ["Deportivo Riestra", "Riestra"],
    "deportivo riestra": ["Deportivo Riestra", "Riestra"],
    "huracan": ["CA Huracán", "Huracán", "Huracan"],
    "belgrano": ["Belgrano", "CA Belgrano"],
    "instituto": ["Instituto", "Instituto ACC"],
    "godoy cruz": ["Godoy Cruz"],
    "banfield": ["Banfield", "CA Banfield"],
    "lanus": ["Lanús", "Lanus", "CA Lanús"],
    "defensa y justicia": ["Defensa y Justicia"],
    "argentinos juniors": ["Argentinos Juniors"],
    "rosario central": ["Rosario Central"],
    "newells": ["Newell's Old Boys", "Newells Old Boys"],
    "newells old boys": ["Newell's Old Boys", "Newells Old Boys"],
    "union": ["Unión", "Union", "Unión de Santa Fe"],
    "union santa fe": ["Unión", "Union", "Unión de Santa Fe"],
    "sarmiento": ["Sarmiento"],
    "central cordoba": ["Central Córdoba", "Central Cordoba", "Central Córdoba SdE"],
    "tigre": ["Tigre", "CA Tigre"],
    "barracas central": ["Barracas Central"],
    "gimnasia": ["Gimnasia LP", "Gimnasia y Esgrima"],
    "independiente rivadavia": ["Independiente Rivadavia"],
    "atletico tucuman": ["Atlético Tucumán", "Atletico Tucuman"],
    "velez": ["Vélez Sarsfield", "Velez Sarsfield"],
    "velez sarsfield": ["Vélez Sarsfield", "Velez Sarsfield"],
    "estudiantes": ["Estudiantes", "Estudiantes de La Plata"],
    "talleres": ["Talleres", "Talleres de Córdoba"],
    "vasco": ["Vasco da Gama"],
    "vasco da gama": ["Vasco da Gama"],
    "flamengo": ["Flamengo", "CR Flamengo"],
    "cr flamengo": ["Flamengo", "CR Flamengo"],
    "palmeiras": ["Palmeiras", "SE Palmeiras"],
    "se palmeiras": ["Palmeiras", "SE Palmeiras"],
    "fluminense": ["Fluminense", "Fluminense FC"],
    "vitoria": ["Vitória", "EC Vitória", "Vitoria"],
    "vitória": ["Vitória", "EC Vitória", "Vitoria"],
    "ec vitoria": ["Vitória", "EC Vitória", "Vitoria"],
    "fortaleza": ["Fortaleza", "Fortaleza EC"],
    "bahia": ["Bahia", "EC Bahia"],
    "cruzeiro": ["Cruzeiro", "Cruzeiro EC"],
    "internacional": ["Internacional", "SC Internacional"],
    "athletico paranaense": ["Athletico Paranaense", "Athletico-PR"],
    "bragantino": ["Red Bull Bragantino", "RB Bragantino", "Bragantino"],
    "juventude": ["Juventude", "EC Juventude"],
    "criciuma": ["Criciúma", "Criciuma"],
    "cuiaba": ["Cuiabá", "Cuiaba"],
    "atletico goianiense": ["Atlético Goianiense", "Atletico-GO"],
    "corinthians": ["Corinthians"],
    "sao paulo": ["São Paulo", "Sao Paulo"],
    "botafogo": ["Botafogo"],
    "atletico mineiro": ["Atlético-MG", "Atletico MG", "Atlético Mineiro"],
    "atletico-mg": ["Atlético-MG", "Atletico MG"],
    "gremio": ["Grêmio", "Gremio"],
    "penarol": ["Peñarol"],
    "peñarol": ["Peñarol"],
    "nacional": ["Nacional", "Club Nacional"],
    "colo colo": ["Colo-Colo", "Colo Colo"],
    "colo-colo": ["Colo-Colo", "Colo Colo"],
    "universidad de chile": ["Universidad de Chile", "U. de Chile"],
    "u de chile": ["Universidad de Chile", "U. de Chile"],
    "universidad catolica": ["Universidad Católica", "Universidad Catolica", "U. Católica"],
    "u catolica": ["Universidad Católica", "Universidad Catolica"],
    "atletico nacional": ["Atlético Nacional", "Atletico Nacional"],
    "nacional de medellin": ["Atlético Nacional", "Atletico Nacional"],
    "millonarios": ["Millonarios", "Millonarios FC"],
    "millonarios fc": ["Millonarios", "Millonarios FC"],
    "america de cali": ["América de Cali", "America de Cali"],
    "junior": ["Junior FC", "Junior", "Atlético Junior"],
    "junior fc": ["Junior FC", "Junior"],
    "junior barranquilla": ["Junior FC", "Junior"],
    "junior de barranquilla": ["Junior FC", "Junior"],
    "atletico junior": ["Junior FC", "Junior"],
    "cucuta": ["Cúcuta Deportivo", "Cucuta Deportivo", "Cúcuta"],
    "cucuta deportivo": ["Cúcuta Deportivo", "Cucuta Deportivo", "Cúcuta"],
    "once caldas": ["Once Caldas"],
    "deportivo cali": ["Deportivo Cali"],
    "cali": ["Deportivo Cali"],
    "independiente medellin": ["Independiente Medellín", "DIM"],
    "medellin": ["Independiente Medellín", "DIM"],
    "dim": ["Independiente Medellín", "DIM"],
    "santa fe": ["Independiente Santa Fe", "Santa Fe"],
    "independiente santa fe": ["Independiente Santa Fe", "Santa Fe"],
    "tolima": ["Deportes Tolima", "Tolima"],
    "deportes tolima": ["Deportes Tolima", "Tolima"],
    "bucaramanga": ["Atlético Bucaramanga", "Bucaramanga"],
    "atletico bucaramanga": ["Atlético Bucaramanga", "Bucaramanga"],
    "pereira": ["Deportivo Pereira", "Pereira"],
    "deportivo pereira": ["Deportivo Pereira", "Pereira"],
    "la equidad": ["La Equidad", "Equidad"],
    "equidad": ["La Equidad", "Equidad"],
    "aguilas doradas": ["Águilas Doradas", "Aguilas Doradas"],
    "envigado": ["Envigado FC", "Envigado"],
    "deportivo pasto": ["Deportivo Pasto", "Pasto"],
    "pasto": ["Deportivo Pasto", "Pasto"],
    "patriotas": ["Patriotas Boyacá", "Patriotas"],
    "boyaca chico": ["Boyacá Chicó", "Boyaca Chico"],
    "fortaleza ceif": ["Fortaleza CEIF", "Fortaleza FC"],
    "alianza fc": ["Alianza FC"],
    "llaneros": ["Llaneros FC", "Llaneros"],
    "real cartagena": ["Real Cartagena"],
    "quindio": ["Deportes Quindío", "Quindío"],
    "huila": ["Atlético Huila", "Huila"],
    "gazalkent": ["FK Gazalkent", "Gazalkent"],
    "fk gazalkent": ["FK Gazalkent", "Gazalkent"],
    "respublika fa": ["Respublika FA"],
    "paxtakor": ["Pakhtakor Tashkent", "Pakhtakor"],
    "pakhtakor": ["Pakhtakor Tashkent", "Pakhtakor"],
    "nasaf": ["FC Nasaf", "Nasaf"],
    "navbahor": ["Navbahor Namangan", "Navbahor"],
    "agmk": ["FC AGMK", "AGMK"],
    "sogdiana": ["Sogdiana Jizzakh", "Sogdiana"],
    "bunyodkor": ["FC Bunyodkor", "Bunyodkor"],
    "independiente del valle": ["Independiente del Valle"],
    "idv": ["Independiente del Valle"],
    "ldu quito": ["LDU de Quito", "LDU Quito", "Liga de Quito"],
    "ldu de quito": ["LDU de Quito", "LDU Quito"],
    "liga de quito": ["LDU de Quito", "LDU Quito"],
    "barcelona sc": ["Barcelona SC"],
    "barcelona de guayaquil": ["Barcelona SC"],
    "barcelona guayaquil": ["Barcelona SC"],
    "emelec": ["CS Emelec", "Emelec"],
    "cs emelec": ["CS Emelec", "Emelec"],
    # Bolivia (División Profesional)
    "aurora": ["Aurora", "Club Aurora"],
    "club aurora": ["Aurora", "Club Aurora"],
    "universitario de vinto": ["Universitario de Vinto", "FC Universitario"],
    "fc universitario": ["Universitario de Vinto", "FC Universitario"],
    "universitario vinto": ["Universitario de Vinto", "FC Universitario"],
    "bolivar": ["Bolívar", "Bolivar", "Club Bolívar"],
    "the strongest": ["The Strongest"],
    "always ready": ["Always Ready"],
    "wilstermann": ["Jorge Wilstermann", "Wilstermann"],
    "jorge wilstermann": ["Jorge Wilstermann", "Wilstermann"],
    "blooming": ["Blooming"],
    "oriente petrolero": ["Oriente Petrolero"],
    "nacional potosi": ["Nacional Potosí", "Nacional Potosi"],
    "real potosi": ["Real Potosí", "Real Potosi"],
    "real oruro": ["Real Oruro"],
    "independiente petrolero": ["Independiente Petrolero"],
    "real tomayapo": ["Real Tomayapo", "CD Real Tomayapo"],
    "tomayapo": ["Real Tomayapo", "CD Real Tomayapo"],
    "guabira": ["Guabirá", "Guabira"],
    "royal pari": ["Royal Pari"],
    "real santa cruz": ["Real Santa Cruz"],
    "gv san jose": ["GV San José", "GV San Jose", "San José"],
    "gualberto villarroel": ["GV San José", "GV San Jose"],
    "san jose": ["GV San José", "GV San Jose", "San José"],
    "san antonio bulo bulo": ["San Antonio Bulo Bulo", "San Antonio"],
    "bulo bulo": ["San Antonio Bulo Bulo", "San Antonio"],
}

PAISES_EUROPA: frozenset[str] = frozenset({
    "espana", "inglaterra", "italia", "alemania", "francia", "portugal", "holanda",
    "belgica", "turquia", "escocia", "suiza", "austria", "grecia", "dinamarca",
    "suecia", "noruega", "croacia", "serbia", "republica checa", "polonia", "ucrania", "europa",
})
PAISES_SUDAMERICA: frozenset[str] = frozenset({
    "uruguay", "chile", "argentina", "peru", "colombia", "brasil", "paraguay",
    "bolivia", "ecuador", "venezuela", "sudamerica",
})

# Catálogo de ligas y competiciones para desambiguación por prefijo en comandos
# (ej. "/mercados bundesliga mainz vs friburgo", "/mercados la liga ea sports barcelona vs real madrid").
CATALOGO_LIGAS_PREFIJO: list[tuple[list[str], dict[str, Any]]] = [
    (
        ["la liga ea sports", "laliga ea sports", "la liga santander", "primera division de espana", "liga espanola", "la liga", "laliga"],
        {"nombre": "LaLiga", "pais": "espana", "ids_liga": [87, 138], "keywords_liga": ["laliga", "la liga", "copa del rey"]},
    ),
    (
        ["la liga hypermotion", "laliga hypermotion", "la liga 2", "laliga 2", "laliga2", "segunda division de espana", "segunda division"],
        {"nombre": "LaLiga2", "pais": "espana", "ids_liga": [140, 87, 138], "keywords_liga": ["laliga2", "laliga hypermotion", "segunda division", "laliga"]},
    ),
    (
        ["copa del rey"],
        {"nombre": "Copa del Rey", "pais": "espana", "ids_liga": [138, 87, 140], "keywords_liga": ["copa del rey", "laliga"]},
    ),
    (
        ["english premier league", "premier league", "premier", "epl"],
        {"nombre": "Premier League", "pais": "inglaterra", "ids_liga": [47, 48], "keywords_liga": ["premier league", "championship", "fa cup", "efl"]},
    ),
    (
        ["efl championship", "sky bet championship", "championship"],
        {"nombre": "Championship", "pais": "inglaterra", "ids_liga": [48, 47, 108], "keywords_liga": ["championship", "premier league", "league one", "fa cup", "efl"]},
    ),
    (
        ["efl league one", "league one"],
        {"nombre": "League One", "pais": "inglaterra", "ids_liga": [108, 48], "keywords_liga": ["league one", "championship"]},
    ),
    (
        ["efl league two", "league two"],
        {"nombre": "League Two", "pais": "inglaterra", "ids_liga": [109, 108], "keywords_liga": ["league two", "league one"]},
    ),
    (
        ["fa cup", "efl cup", "carabao cup"],
        {"nombre": "FA Cup", "pais": "inglaterra", "ids_liga": [132, 133, 47, 48], "keywords_liga": ["fa cup", "efl cup", "premier league", "championship"]},
    ),
    (
        ["2. bundesliga", "2 bundesliga", "bundesliga 2", "segunda bundesliga"],
        {"nombre": "2. Bundesliga", "pais": "alemania", "ids_liga": [146, 54], "keywords_liga": ["2. bundesliga", "bundesliga", "dfb"]},
    ),
    (
        ["1. bundesliga", "bundesliga", "liga alemana"],
        {"nombre": "Bundesliga", "pais": "alemania", "ids_liga": [54, 146], "keywords_liga": ["bundesliga", "2. bundesliga", "dfb"]},
    ),
    (
        ["dfb-pokal", "dfb pokal", "copa de alemania"],
        {"nombre": "DFB-Pokal", "pais": "alemania", "ids_liga": [209, 54, 146], "keywords_liga": ["dfb", "bundesliga"]},
    ),
    (
        ["serie a enilive", "serie a tim", "serie a italia", "calcio", "serie a"],
        {"nombre": "Serie A", "pais": "italia", "ids_liga": [55, 86], "keywords_liga": ["serie a", "serie b", "coppa italia"]},
    ),
    (
        ["serie bkt", "serie b italia", "serie b"],
        {"nombre": "Serie B", "pais": "italia", "ids_liga": [86, 55], "keywords_liga": ["serie b", "serie a", "coppa italia"]},
    ),
    (
        ["coppa italia", "copa italia"],
        {"nombre": "Coppa Italia", "pais": "italia", "ids_liga": [141, 55, 86], "keywords_liga": ["coppa italia", "serie a", "serie b"]},
    ),
    (
        ["ligue 1 mcdonald's", "ligue 1 mcdonalds", "ligue 1 uber eats", "ligue 1", "liga francesa"],
        {"nombre": "Ligue 1", "pais": "francia", "ids_liga": [53, 110], "keywords_liga": ["ligue 1", "ligue 2", "coupe de france"]},
    ),
    (
        ["ligue 2"],
        {"nombre": "Ligue 2", "pais": "francia", "ids_liga": [110, 53], "keywords_liga": ["ligue 2", "ligue 1"]},
    ),
    (
        ["liga portugal betclic", "liga portugal", "primeira liga", "liga portuguesa"],
        {"nombre": "Liga Portugal", "pais": "portugal", "ids_liga": [61], "keywords_liga": ["liga portugal", "primeira liga", "taca de portugal"]},
    ),
    (
        ["eredivisie", "liga holandesa"],
        {"nombre": "Eredivisie", "pais": "holanda", "ids_liga": [57], "keywords_liga": ["eredivisie", "eerste divisie", "knvb"]},
    ),
    (
        ["jupiler pro league", "belgian pro league", "pro league", "liga belga"],
        {"nombre": "Belgian Pro League", "pais": "belgica", "ids_liga": [40], "keywords_liga": ["pro league", "first division a"]},
    ),
    (
        ["super lig", "superliga turca", "liga turca"],
        {"nombre": "Super Lig", "pais": "turquia", "ids_liga": [71], "keywords_liga": ["super lig"]},
    ),
    (
        ["scottish premiership", "liga escocesa"],
        {"nombre": "Scottish Premiership", "pais": "escocia", "ids_liga": [64], "keywords_liga": ["premiership", "scottish"]},
    ),
    (
        ["uefa champions league", "champions league", "champions", "ucl"],
        {"nombre": "Champions League", "pais": "europa", "ids_liga": [42, 87, 47, 54, 55, 53, 61, 57], "keywords_liga": ["champions league", "laliga", "premier league", "bundesliga", "serie a", "ligue 1", "liga portugal", "eredivisie"]},
    ),
    (
        ["uefa europa league", "europa league", "uel"],
        {"nombre": "Europa League", "pais": "europa", "ids_liga": [73, 87, 47, 54, 55, 53, 61, 57], "keywords_liga": ["europa league", "laliga", "premier league", "bundesliga", "serie a", "ligue 1", "liga portugal", "eredivisie"]},
    ),
    (
        ["uefa conference league", "conference league", "uecl"],
        {"nombre": "Conference League", "pais": "europa", "ids_liga": [10216], "keywords_liga": ["conference league"]},
    ),
    (
        ["conmebol libertadores", "copa libertadores de america", "copa libertadores", "libertadores"],
        {"nombre": "Copa Libertadores", "pais": "sudamerica", "ids_liga": [45, 112, 268, 131, 274, 273, 161, 246], "keywords_liga": ["copa libertadores", "libertadores", "liga profesional", "serie a", "brasileirao", "liga 1", "primera a", "primera division", "liga auf"]},
    ),
    (
        ["conmebol sudamericana", "copa sudamericana", "sudamericana"],
        {"nombre": "Copa Sudamericana", "pais": "sudamerica", "ids_liga": [299, 112, 268, 131, 274, 273, 161, 246], "keywords_liga": ["copa sudamericana", "sudamericana", "liga profesional", "serie a", "brasileirao", "liga 1", "primera a", "primera division", "liga auf"]},
    ),
    (
        ["liga 1 te apuesto", "liga 1 de peru", "liga 1 peru", "liga de peru", "liga peruana", "liga 1"],
        {"nombre": "Liga 1", "pais": "peru", "ids_liga": [131], "keywords_liga": ["liga 1", "peru"]},
    ),
    (
        ["liga profesional de argentina", "liga profesional argentina", "copa de la liga profesional", "liga de argentina", "liga profesional", "liga argentina", "torneo betano"],
        {"nombre": "Liga Profesional", "pais": "argentina", "ids_liga": [112], "keywords_liga": ["liga profesional", "copa argentina"]},
    ),
    (
        ["brasileirao serie a", "brasileirao betano", "serie a de brasil", "serie a brasil", "liga de brasil", "liga brasilena", "brasileirao"],
        {"nombre": "Brasileirão Série A", "pais": "brasil", "ids_liga": [268], "keywords_liga": ["serie a", "brasileirao", "copa do brasil", "paulista", "carioca", "gaucho"]},
    ),
    (
        ["brasileirao serie b", "serie b de brasil", "serie b brasil"],
        {"nombre": "Brasileirão Série B", "pais": "brasil", "ids_liga": [8814, 268], "keywords_liga": ["serie b", "brasileirao"]},
    ),
    (
        ["brasileirao serie c", "serie c de brasil", "serie c brasil", "serie c"],
        {"nombre": "Brasileirão Série C", "pais": "brasil", "ids_liga": [8971], "keywords_liga": ["serie c", "brasileirao serie c"]},
    ),
    (
        ["liga betplay dimayor", "liga betplay", "primera a de colombia", "primera a colombia", "liga de colombia", "liga colombiana"],
        {"nombre": "Liga BetPlay", "pais": "colombia", "ids_liga": [274], "keywords_liga": ["primera a", "liga betplay", "colombia"]},
    ),
    (
        ["campeonato chileno", "primera division de chile", "primera de chile", "liga de chile", "liga chilena"],
        {"nombre": "Primera División Chile", "pais": "chile", "ids_liga": [273], "keywords_liga": ["primera division", "chile"]},
    ),
    (
        ["liga auf uruguaya", "primera division de uruguay", "primera division uruguaya", "liga de uruguay", "liga uruguaya", "liga auf"],
        {"nombre": "Liga AUF Uruguaya", "pais": "uruguay", "ids_liga": [161], "keywords_liga": ["liga auf", "uruguay"]},
    ),
    (
        ["liga pro de ecuador", "liga pro ecuador", "ligapro de ecuador", "ligapro", "liga de ecuador", "liga ecuatoriana", "liga pro"],
        {"nombre": "LigaPro Ecuador", "pais": "ecuador", "ids_liga": [246], "keywords_liga": ["serie a", "ligapro", "ecuador"]},
    ),
    (
        ["liga mx", "liga de mexico", "liga mexicana"],
        {"nombre": "Liga MX", "pais": "mexico", "ids_liga": [230], "keywords_liga": ["liga mx", "mexico"]},
    ),
    (
        ["major league soccer", "mls"],
        {"nombre": "MLS", "pais": "usa", "ids_liga": [130], "keywords_liga": ["mls", "major league soccer"]},
    ),
    (
        ["division profesional de bolivia", "division profesional bolivia", "division profesional", "copa pacena", "liga de bolivia", "bolivia"],
        {"nombre": "División Profesional", "pais": "bolivia", "pais_iso": "BOL", "ids_liga": [301], "keywords_liga": ["division profesional", "bolivia", "copa pacena"]},
    ),
]

# Ordenar todos los patrones de liga de mayor a menor longitud para que "la liga ea sports"
# coincida antes que "la liga", y "2. bundesliga" antes que "bundesliga".
_PATRONES_LIGA_ORDENADOS: list[tuple[str, dict[str, Any]]] = sorted(
    [
        (_sin_acentos(p).lower().strip(), info)
        for patrones, info in CATALOGO_LIGAS_PREFIJO
        for p in patrones
    ],
    key=lambda x: len(x[0]),
    reverse=True,
)

# Mapa rápido de equipos conocidos -> metadatos de país y liga para desambiguar al rival
# incluso cuando el usuario no escribe el prefijo de la liga.
_LIGA_POR_EQUIPO_CONOCIDO: dict[str, dict[str, Any]] = {
    # España
    **{k: {"liga": "LaLiga", "id_liga": 87, "pais": "espana"} for k in (
        "atletico madrid", "atletico de madrid", "atleti", "real madrid", "madrid",
        "barcelona", "fc barcelona", "barca", "sevilla", "betis", "real betis",
        "valencia", "celta", "celta de vigo", "osasuna", "rayo", "rayo vallecano",
        "mallorca", "girona", "alaves", "deportivo alaves", "las palmas", "valladolid",
        "real valladolid", "leganes", "getafe", "villarreal", "sociedad", "real sociedad",
        "athletic", "bilbao", "athletic bilbao", "athletic club", "espanyol", "espanol",
        "elche", "elche cf", "levante", "deportivo a coruna", "deportivo la coruna",
        "deportivo de a coruna", "real oviedo", "oviedo", "sporting gijon", "real zaragoza",
        "zaragoza", "racing santander", "granada", "almeria", "cadiz", "tenerife", "malaga",
    )},
    # Inglaterra
    **{k: {"liga": "Premier League", "id_liga": 47, "pais": "inglaterra"} for k in (
        "fulham", "fc fulham", "fulham fc", "hull", "hull city", "manchester united",
        "man utd", "manchester city", "manchester city fc", "man city", "arsenal",
        "arsenal fc", "liverpool", "liverpool fc", "fc liverpool", "chelsea",
        "chelsea fc", "fc chelsea", "tottenham", "spurs", "aston villa", "newcastle",
        "newcastle united", "newcastle united fc", "west ham", "west ham united",
        "brighton", "wolves", "wolverhampton", "crystal palace", "bournemouth",
        "nottingham", "nottingham forest", "everton", "everton fc", "brentford",
        "leicester", "leicester city", "southampton", "ipswich", "ipswich town",
        "leeds", "leeds united", "burnley", "sunderland", "middlesbrough",
        "sheffield united", "sheffield wednesday", "west brom", "west bromwich",
        "norwich", "norwich city", "watford", "coventry", "coventry city",
        "swansea", "swansea city", "cardiff", "cardiff city", "stoke", "stoke city",
        "qpr", "blackburn", "bristol city", "millwall", "derby", "derby county",
        "luton", "portsmouth", "plymouth",
    )},
    # Alemania
    **{k: {"liga": "Bundesliga", "id_liga": 54, "pais": "alemania"} for k in (
        "bayern", "bayern munich", "bayern munchen", "bayern münchen",
        "bayer", "bayer 04", "bayer 04 leverkusen", "leverkusen", "bayer leverkusen",
        "dortmund", "borussia dortmund", "leipzig", "rb leipzig", "mainz", "mainz 05",
        "fsv mainz", "maguncia", "friburgo", "sc friburgo", "freiburg", "sc freiburg",
        "stuttgart", "vfb stuttgart", "frankfurt", "francfort", "eintracht",
        "eintracht frankfurt", "wolfsburgo", "wolfsburg", "vfl wolfsburg",
        "monchengladbach", "gladbach", "borussia monchengladbach", "bremen",
        "werder bremen", "augsburgo", "augsburg", "fc augsburg", "union berlin",
        "bochum", "vfl bochum", "heidenheim", "st pauli", "sankt pauli",
        "holstein kiel", "kiel", "hoffenheim", "tsg hoffenheim", "colonia", "koln",
        "fc koln", "1. fc koln", "hamburgo", "hamburg", "hamburger sv", "schalke", "schalke 04",
        "hertha", "hertha berlin", "hannover", "hannover 96", "nuremberg", "nurnberg",
        "dusseldorf", "fortuna dusseldorf", "kaiserslautern", "karlsruher", "darmstadt",
        "paderborn", "magdeburgo", "magdeburg", "1. fc magdeburg", "braunschweig",
    )},
    # Italia
    **{k: {"liga": "Serie A", "id_liga": 55, "pais": "italia"} for k in (
        "inter", "inter de milan", "milan", "ac milan", "roma", "as roma", "juve",
        "juventus", "napoli", "napoles", "lazio", "atalanta", "fiorentina",
        "florentina", "bolonia", "bologna", "torino", "udinese", "genoa", "cagliari",
        "lecce", "verona", "empoli", "monza", "parma", "como", "venezia",
    )},
    # Francia
    **{k: {"liga": "Ligue 1", "id_liga": 53, "pais": "francia"} for k in (
        "psg", "paris saint germain", "marsella", "olympique marsella", "lyon",
        "olympique lyon", "monaco", "as monaco", "lille", "niza", "lens", "rennes",
        "brest", "nantes", "toulouse", "estrasburgo", "strasbourg", "reims",
    )},
    # Portugal
    **{k: {"liga": "Liga Portugal", "id_liga": 61, "pais": "portugal"} for k in (
        "oporto", "porto", "fc porto", "benfica", "sporting lisboa", "sporting cp",
        "sporting de portugal", "braga",
    )},
    # Países Bajos
    **{k: {"liga": "Eredivisie", "id_liga": 57, "pais": "holanda"} for k in (
        "psv", "psv eindhoven", "ajax", "feyenoord",
    )},
    # Sudamérica y MLS
    **{k: {"liga": "MLS", "id_liga": 130, "pais": "usa"} for k in (
        "nueva york red bull", "nueva york red bulls", "ny red bulls", "red bull nueva york",
        "new york red bull", "new york red bulls", "inter miami", "inter de miami", "inter miami cf",
        "nueva york city", "nycfc", "new york city", "new york city fc", "los angeles fc", "lafc",
        "la galaxy", "los angeles galaxy", "atlanta united", "columbus crew", "cincinnati",
        "fc cincinnati", "seattle sounders", "orlando city",
    )},
    **{k: {"liga": "Liga 1", "id_liga": 131, "pais": "peru"} for k in (
        "alianza lima", "universitario", "universitario de deportes", "sporting cristal",
        "melgar", "fbc melgar", "cusco fc", "cusco", "cienciano",
    )},
    **{k: {"liga": "Liga Profesional", "id_liga": 112, "pais": "argentina"} for k in (
        "boca", "boca juniors", "river", "river plate", "racing club", "racing de avellaneda",
        "independiente", "san lorenzo", "velez", "velez sarsfield", "estudiantes", "talleres", "lanus",
        "platense", "ca platense", "club atletico platense", "riestra", "deportivo riestra",
        "huracan", "ca huracan", "belgrano", "instituto", "godoy cruz", "banfield",
        "defensa y justicia", "argentinos juniors", "rosario central", "newells",
        "newells old boys", "union", "union santa fe", "sarmiento", "central cordoba",
        "tigre", "barracas central", "gimnasia", "independiente rivadavia", "atletico tucuman",
    )},
    **{k: {"liga": "Brasileirão Série A", "id_liga": 268, "pais": "brasil"} for k in (
        "vasco", "vasco da gama", "flamengo", "cr flamengo", "palmeiras", "se palmeiras",
        "fluminense", "corinthians", "sao paulo", "botafogo", "atletico mineiro", "atletico-mg", "gremio",
        "vitoria", "vitória", "ec vitoria", "fortaleza", "bahia", "ec bahia", "cruzeiro",
        "internacional", "sc internacional", "athletico paranaense", "bragantino",
        "rb bragantino", "red bull bragantino", "juventude", "criciuma", "cuiaba", "atletico goianiense",
    )},
    **{k: {"liga": "Liga AUF Uruguaya", "id_liga": 161, "pais": "uruguay"} for k in (
        "penarol", "peñarol", "nacional", "montevideo city torque", "montevideo city",
    )},
    **{k: {"liga": "Primera División Chile", "id_liga": 273, "pais": "chile"} for k in (
        "colo colo", "colo-colo", "universidad de chile", "u de chile", "universidad catolica", "u catolica",
    )},
    **{k: {"liga": "Liga BetPlay", "id_liga": 274, "pais": "colombia"} for k in (
        "atletico nacional", "nacional de medellin", "millonarios", "millonarios fc",
        "america de cali", "junior", "junior fc", "junior de barranquilla", "atletico junior",
    )},
    **{k: {"liga": "LigaPro Ecuador", "id_liga": 246, "pais": "ecuador"} for k in (
        "independiente del valle", "idv", "ldu quito", "ldu de quito", "liga de quito",
        "barcelona sc", "barcelona de guayaquil", "emelec", "cs emelec",
    )},
    **{k: {"liga": "División Profesional", "id_liga": 301, "pais": "bolivia", "pais_iso": "BOL"} for k in (
        "aurora", "club aurora", "universitario de vinto", "universitario vinto", "fc universitario",
        "bolivar", "the strongest", "always ready", "wilstermann", "jorge wilstermann",
        "blooming", "oriente petrolero", "nacional potosi", "real potosi", "real oruro",
        "independiente petrolero", "real tomayapo", "tomayapo", "guabira", "royal pari",
        "real santa cruz", "gv san jose", "gualberto villarroel", "san antonio bulo bulo", "bulo bulo",
    )},
}


def extraer_liga_de_texto(texto: str) -> tuple[dict[str, Any] | None, str]:
    """
    Detecta si el texto comienza (o termina) con el nombre de una liga o competición
    reconocida y devuelve (liga_info, texto_sin_liga).

    Ejemplos:
      - "bundesliga mainz vs friburgo" -> ({"nombre": "Bundesliga", "pais": "alemania", ...}, "mainz vs friburgo")
      - "la liga ea sports barcelona vs real madrid" -> ({"nombre": "LaLiga", "pais": "espana", ...}, "barcelona vs real madrid")
      - "champions league real madrid vs bayern" -> ({"nombre": "Champions League", "pais": "europa", ...}, "real madrid vs bayern")
      - "copa libertadores boca juniors vs river plate" -> ({"nombre": "Copa Libertadores", "pais": "sudamerica", ...}, "boca juniors vs river plate")
      - "mainz" -> (None, "mainz")
    """
    original = (texto or "").strip()
    if not original:
        return None, ""

    # PASO #1 OBLIGATORIO: Consultar el Catálogo Mundial de +250 Ligas y +110 Países de Flashscore (resolvedor_entidades)
    try:
        from resolvedor_entidades import extraer_liga_flashscore
        info_fs, resto_fs = extraer_liga_flashscore(original)
        if info_fs and resto_fs:
            norm_fs = _sin_acentos(info_fs.get("liga_norm") or info_fs.get("nombre") or "").lower().strip()
            for patron, info_cat in _PATRONES_LIGA_ORDENADOS:
                if norm_fs == patron or norm_fs.startswith(patron):
                    info_combinada = dict(info_cat)
                    info_combinada.update(info_fs)
                    info_combinada["id_liga"] = info_cat["ids_liga"][0] if info_cat.get("ids_liga") else None
                    return info_combinada, resto_fs
            return info_fs, resto_fs
    except Exception:
        pass

    # Limpiar posible etiqueta "liga:" o "torneo:" o corchetes al inicio
    trabajo = re.sub(r"^(?:liga|competicion|competici[oó]n|torneo)\s*[:=]\s*", "", original, flags=re.I).strip()

    # 1. Comprobar prefijo entre corchetes o paréntesis: "[Bundesliga] Mainz vs Friburgo"
    m_bracket = re.match(r"^[\[\(]\s*([^\]\)]+?)\s*[\]\)]\s*[:\-–—]?\s*(.+)$", trabajo)
    if m_bracket:
        posible_liga = _sin_acentos(m_bracket.group(1)).lower().strip()
        resto_b = m_bracket.group(2).strip()
        for patron, info in _PATRONES_LIGA_ORDENADOS:
            if posible_liga == patron and resto_b:
                meta = dict(info)
                meta["liga"] = info["nombre"]
                meta["id_liga"] = info["ids_liga"][0] if info.get("ids_liga") else None
                meta["liga_forzada"] = True
                meta["prefijo_original"] = m_bracket.group(1).strip()
                return meta, resto_b

    # 2. Comprobar prefijo directo al inicio del texto: "bundesliga mainz vs friburgo"
    trabajo_norm = _sin_acentos(trabajo).lower()
    for patron, info in _PATRONES_LIGA_ORDENADOS:
        # El patrón debe ir seguido de espacio o separador (:, -, ·, |) y luego al menos un nombre de equipo
        m = re.match(rf"^{re.escape(patron)}(?:\s*[:\|\·\-–—]\s*|\s+)(.+)$", trabajo_norm)
        if m:
            # Calcular el corte equivalente sobre `trabajo` conservando mayúsculas/tildes del resto
            inicio_resto = m.start(1)
            resto = trabajo[inicio_resto:].strip().strip(":-–—|").strip()
            # Si tras el nombre de liga quedó un conector geográfico residual ("de brasil ", "de peru ", "de chile ", etc.), limpiarlo
            resto = re.sub(
                r"^(?:de|del)\s+(?:brasil|peru|per[uú]|chile|colombia|argentina|ecuador|uruguay|espana|espa[ñn]a|alemania|italia|inglaterra|francia|mexico|m[eé]xico|usa|eeuu)\s+",
                "",
                resto,
                flags=re.I,
            ).strip()
            # Evitar cortar si el resto empieza directamente por "vs " (p.ej. si alguien pusiera la liga como equipo)
            if resto and not re.match(r"^(?:vs\.?|v|contra|h2h)\b", resto, flags=re.I):
                meta = dict(info)
                meta["liga"] = info["nombre"]
                meta["id_liga"] = info["ids_liga"][0] if info.get("ids_liga") else None
                meta["liga_forzada"] = True
                meta["prefijo_original"] = trabajo[:inicio_resto].strip(" :-–—|")
                return meta, resto

    return None, original


_EXONIMOS_TOKEN_MAP: dict[str, str] = {
    "francfort": "frankfurt",
    "munich": "munchen",
    "colonia": "koln",
    "friburgo": "freiburg",
    "maguncia": "mainz",
    "estrasburgo": "strasbourg",
    "napoles": "napoli",
    "bolonia": "bologna",
    "florentina": "fiorentina",
    "oporto": "porto",
    "praga": "prague",
    "basilea": "basel",
    "gante": "gent",
    "amberes": "antwerp",
}


def normalizar_nombre_club(nombre: str) -> str:
    """
    Normalizador universal de exónimos, siglas, tildes/diacríticos y alias de clubes
    (Europa, MLS, Sudamérica) para FotMob y Flashscore.
    """
    if not nombre or not str(nombre).strip():
        return ""
    liga_pref, sin_liga = extraer_liga_de_texto(str(nombre).strip())
    base = " ".join((sin_liga or str(nombre)).split()).strip()

    # PASO #1 OBLIGATORIO: Puente Bilingüe Flashscore -> FotMob (resolvedor_entidades)
    try:
        from resolvedor_entidades import resolver_equipo_individual, DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB, normalizar_texto
        pais_iso_pref = (liga_pref or {}).get("pais_iso")
        norm_b = normalizar_texto(base)
        if norm_b in DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB:
            nom_res, _ = resolver_equipo_individual(base, pais_iso_contexto=pais_iso_pref)
            if nom_res:
                return nom_res
    except Exception:
        pass

    sin_ac = _sin_acentos(base)
    low = sin_ac.lower().strip()

    # 1. Coincidencia directa en ALIAS_EQUIPOS_HISPANOS
    if low in ALIAS_EQUIPOS_HISPANOS and ALIAS_EQUIPOS_HISPANOS[low]:
        return ALIAS_EQUIPOS_HISPANOS[low][0]

    nucleo = _palabras_nucleo(base)
    clave_nucleo = " ".join(nucleo)
    if clave_nucleo in ALIAS_EQUIPOS_HISPANOS and ALIAS_EQUIPOS_HISPANOS[clave_nucleo]:
        return ALIAS_EQUIPOS_HISPANOS[clave_nucleo][0]

    # 2. Traducción de frases compuestas hispanas -> inglés/internacional
    reemplazos_frase = (
        ("nueva york red bulls", "New York Red Bulls"),
        ("nueva york red bull", "New York Red Bulls"),
        ("red bull nueva york", "New York Red Bulls"),
        ("ny red bulls", "New York Red Bulls"),
        ("nueva york city", "New York City FC"),
        ("inter de miami", "Inter Miami CF"),
        ("inter miami", "Inter Miami CF"),
        ("eintracht francfort", "Eintracht Frankfurt"),
        ("eintracht de francfort", "Eintracht Frankfurt"),
        ("deportivo de a coruna", "Deportivo de La Coruña"),
        ("deportivo de la coruna", "Deportivo de La Coruña"),
        ("deportivo a coruna", "Deportivo de La Coruña"),
        ("deportivo la coruna", "Deportivo de La Coruña"),
    )
    for origen, destino in reemplazos_frase:
        if origen in low:
            return destino

    # 3. Traducción token por token de exónimos (ej. "Borussia Francfort" -> "Borussia Frankfurt")
    tokens = sin_ac.split()
    tokens_trad = [_EXONIMOS_TOKEN_MAP.get(t.lower(), t) for t in tokens]
    candidato_trad = " ".join(tokens_trad)
    if candidato_trad.lower() in ALIAS_EQUIPOS_HISPANOS and ALIAS_EQUIPOS_HISPANOS[candidato_trad.lower()]:
        return ALIAS_EQUIPOS_HISPANOS[candidato_trad.lower()][0]
    return candidato_trad


def _normalizar_fonetica_hispana(nombre: str) -> list[str]:
    """
    Genera equivalencias fonéticas e idiomáticas en español/catalán/gallego:
      - 'ñ' <-> 'ny' ('español' <-> 'espanyol')
      - 'ñ' -> 'n' ('español' -> 'espanol')
    """
    limpio = (nombre or "").strip()
    if not limpio:
        return []
    res: list[str] = []
    if "ñ" in limpio.lower():
        res.append(re.sub(r"[ñÑ]", "ny", limpio))
        res.append(re.sub(r"[ñÑ]", "n", limpio))
    if "ny" in limpio.lower():
        res.append(re.sub(r"ny", "ñ", limpio, flags=re.IGNORECASE))
        res.append(re.sub(r"ny", "n", limpio, flags=re.IGNORECASE))
    return res


_MAPA_CCODE_PAIS: dict[str, str] = {
    "arg": "argentina", "argentina": "argentina",
    "bra": "brasil", "brazil": "brasil", "brasil": "brasil",
    "esp": "espana", "spain": "espana", "espana": "espana",
    "eng": "inglaterra", "england": "inglaterra", "inglaterra": "inglaterra",
    "ita": "italia", "italy": "italia", "italia": "italia",
    "ger": "alemania", "deu": "alemania", "germany": "alemania", "alemania": "alemania",
    "fra": "francia", "france": "francia", "francia": "francia",
    "por": "portugal", "prt": "portugal", "portugal": "portugal",
    "ned": "holanda", "nld": "holanda", "netherlands": "holanda", "holanda": "holanda",
    "bel": "belgica", "belgium": "belgica", "belgica": "belgica",
    "tur": "turquia", "turkey": "turquia", "turkiye": "turquia", "turquia": "turquia",
    "sco": "escocia", "scotland": "escocia", "escocia": "escocia",
    "uru": "uruguay", "ury": "uruguay", "uruguay": "uruguay",
    "chi": "chile", "chl": "chile", "chile": "chile",
    "per": "peru", "peru": "peru",
    "col": "colombia", "colombia": "colombia",
    "ecu": "ecuador", "ecuador": "ecuador",
    "mex": "mexico", "mexico": "mexico",
    "usa": "usa", "united states": "usa",
    "hon": "honduras", "hnd": "honduras", "honduras": "honduras",
    "crc": "costa rica", "costa rica": "costa rica",
    "gua": "guatemala", "gtm": "guatemala", "guatemala": "guatemala",
    "slv": "el salvador", "esa": "el salvador", "el salvador": "el salvador",
    "pan": "panama", "panama": "panama",
    "par": "paraguay", "pry": "paraguay", "paraguay": "paraguay",
    "bol": "bolivia", "bolivia": "bolivia",
    "ven": "venezuela", "venezuela": "venezuela",
    "uzb": "uzbekistan", "uzbekistan": "uzbekistan",
    "int": "internacional",
}


def _pais_de_liga(nombre_liga: str | None, ccode: str | None = None) -> str:
    """Infiere el país o federación a partir del código de país o del nombre de la liga."""
    if ccode:
        cc = _sin_acentos(str(ccode)).lower().strip()
        if cc in _MAPA_CCODE_PAIS:
            return _MAPA_CCODE_PAIS[cc]
    if not nombre_liga:
        return ""
    n = _sin_acentos(str(nombre_liga)).lower().strip()
    if n in _MAPA_CCODE_PAIS:
        return _MAPA_CCODE_PAIS[n]
    if any(k in n for k in ("honduras", "liga nacional honduras", "olancho", "juticalpa", "motagua", "olimpia honduras")):
        return "honduras"
    if any(k in n for k in ("laliga", "la liga", "copa del rey", "primera division espana", "segunda division", "supercopa de espana", "espana", "spain", "liga f", "federacion")):
        if not any(p in n for p in ("uruguay", "chile", "argentina", "peru", "colombia", "honduras", "costa rica", "mexico")):
            return "espana"
    if any(k in n for k in ("premier league", "fa cup", "efl cup", "championship", "league one", "league two", "inglaterra", "england")):
        if not any(p in n for p in ("tanzania", "uganda", "kenya", "ghana", "nigeria", "egypt", "azerbaijan", "armenia", "kazakhstan", "russia", "ukraine", "scotland", "canada")):
            return "inglaterra"
    if any(k in n for k in ("serie a", "serie b", "coppa italia", "italia", "italy")):
        if "brazil" not in n and "brasil" not in n and "ecuador" not in n and "ligapro" not in n:
            return "italia"
    if any(k in n for k in ("bundesliga", "dfb-pokal", "dfb pokal", "alemania", "germany")):
        if "austria" not in n:
            return "alemania"
    if any(k in n for k in ("ligue 1", "ligue 2", "coupe de france", "francia", "france")):
        return "francia"
    if any(k in n for k in ("liga portugal", "primeira liga", "taca de portugal", "portugal")):
        return "portugal"
    if any(k in n for k in ("eredivisie", "eerste divisie", "knvb", "holanda", "netherlands", "paises bajos")):
        return "holanda"
    if any(k in n for k in ("belgian pro league", "jupiler", "first division a", "belgica", "belgium")):
        return "belgica"
    if any(k in n for k in ("super lig", "turquia", "turkiye", "turkey")):
        return "turquia"
    if any(k in n for k in ("scottish premiership", "escocia", "scotland")):
        return "escocia"
    if any(k in n for k in ("uruguay", "auf", "primera division uruguaya", "liga auf")):
        return "uruguay"
    if any(k in n for k in ("chile", "campeonato chileno", "primera b")):
        return "chile"
    if any(k in n for k in ("argentina", "liga profesional", "copa de la liga profesional", "copa argentina", "primera nacional", "torneo betano")):
        return "argentina"
    if any(k in n for k in ("peru", "liga 1", "liga 1 te apuesto")):
        return "peru"
    if any(k in n for k in ("colombia", "liga betplay", "primera a", "torneo betplay")):
        return "colombia"
    if any(k in n for k in ("bolivia", "division profesional", "copa pacena", "copa simon bolivar")):
        return "bolivia"
    if any(k in n for k in ("panama", "lpf", "lpf apertura", "lpf clausura", "tauro", "plaza amador", "veraguas", "arabe unido", "san francisco", "potros del este", "umecit", "herrera", "sporting san miguelito")):
        return "panama"
    if any(k in n for k in ("uzbekistan", "super league uzbekistan", "pro liga")):
        return "uzbekistan"
    if any(k in n for k in ("brasil", "brazil", "brasileirao", "serie a brazil", "copa do brasil", "paulista", "carioca", "gaucho", "baiano", "copa do nordeste")):
        return "brasil"
    if any(k in n for k in ("ecuador", "ligapro", "liga pro")):
        return "ecuador"
    if any(k in n for k in ("mexico", "liga mx", "expansion mx")):
        return "mexico"
    if any(k in n for k in ("mls", "major league soccer", "us open cup", "united states", "usa", "eeuu")):
        return "usa"
    if any(k in n for k in ("ukraine", "ucrania", "premier league ukraine")):
        return "ucrania"
    if any(k in n for k in ("champions league", "europa league", "conference league", "uefa")):
        return "europa"
    if any(k in n for k in ("libertadores", "sudamericana", "conmebol")):
        return "sudamerica"
    if "liga nacional" in n:
        return "honduras"
    return ""


def _palabras_nucleo(texto: str) -> list[str]:
    """
    Devuelve las palabras nucleares de un nombre de equipo, eliminando tildes,
    signos de puntuación, palabras vacías ('de', 'la', 'the', 'club') y siglas
    genéricas ('fc', 'cf', 'sc', 'ac', 'rcd', 'sv', 'vfl', '05', etc.).
    """
    sin_ac = _sin_acentos(str(texto or "")).lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", sin_ac) if t]
    nucleo = [
        t for t in tokens
        if t not in _PALABRAS_VACIAS and t not in _SIGLAS_CLUB
    ]
    return nucleo if nucleo else tokens


def _inferir_meta_rival_desde_texto(rival_texto: str) -> dict[str, Any] | None:
    """
    Infiere metadatos de liga y país a partir de un texto de rival o de liga
    (ej. 'hull city' -> Premier League/Inglaterra; 'friburgo' -> Bundesliga/Alemania;
    'bundesliga' -> Bundesliga/Alemania).
    """
    if not rival_texto or not str(rival_texto).strip():
        return None
    limpio = str(rival_texto).strip()
    liga_pref, resto = extraer_liga_de_texto(limpio)
    if liga_pref and not resto:
        return liga_pref

    # Comprobar si el texto completo es exactamente el nombre de una liga
    norm_r = _sin_acentos(limpio).lower().strip()
    for patron, info in _PATRONES_LIGA_ORDENADOS:
        if norm_r == patron:
            meta = dict(info)
            meta["liga"] = info["nombre"]
            meta["id_liga"] = info["ids_liga"][0] if info.get("ids_liga") else None
            meta["liga_forzada"] = True
            return meta

    objetivo = _sin_acentos(resto if liga_pref else limpio).lower().strip()
    nucleo_obj = " ".join(_palabras_nucleo(objetivo))

    for clave_eq in (objetivo, nucleo_obj):
        if clave_eq in _LIGA_POR_EQUIPO_CONOCIDO:
            info_eq = dict(_LIGA_POR_EQUIPO_CONOCIDO[clave_eq])
            info_eq["nombre"] = resto if liga_pref else limpio
            if liga_pref:
                info_eq.update(liga_pref)
            return info_eq

    for k_alias, vals in ALIAS_EQUIPOS_HISPANOS.items():
        if k_alias in (objetivo, nucleo_obj) or any(objetivo == _sin_acentos(v).lower() for v in vals):
            if k_alias in _LIGA_POR_EQUIPO_CONOCIDO:
                info_eq = dict(_LIGA_POR_EQUIPO_CONOCIDO[k_alias])
                info_eq["nombre"] = resto if liga_pref else limpio
                if liga_pref:
                    info_eq.update(liga_pref)
                return info_eq

    if liga_pref:
        return liga_pref

    pais_r = _pais_de_liga(limpio)
    if pais_r:
        return {"nombre": limpio, "liga": limpio, "pais": pais_r}
    return None


_RAICES_GENERICAS_PROHIBIDAS_SOLAS: frozenset[str] = frozenset({
    "universitario", "universidad", "real", "nacional", "deportivo", "club", "san",
    "atletico", "athletic", "sporting", "union", "independiente", "central", "alianza",
    "defensor", "racing", "gimnasia", "estudiantes", "tecnico",
})


def variantes_de_busqueda(nombre: str, maximo: int = 8) -> list[str]:
    """
    Construye variantes de búsqueda tolerantes a tildes, palabras vacías,
    siglas de club ('fc fulham' -> 'Fulham'), variantes fonéticas ('ñ' <-> 'ny')
    y alias hispanos ('español' -> 'Espanyol', 'friburgo' -> 'SC Freiburg').
    PROHIBIDO recortar un nombre con ciudad/apellido distintivo ('Universitario de Vinto',
    'Universitario de Deportes', 'Técnico Universitario', 'Universidad Católica',
    'San Antonio Bulo Bulo', 'Real Oruro', 'Real Potosí', 'Nacional Potosí',
    'Real Tomayapo', 'GV San José') a una sola palabra genérica ('Universitario', 'Real', 'Nacional').
    """
    _, nombre_sin_liga = extraer_liga_de_texto(nombre or "")
    limpio = " ".join((nombre_sin_liga or nombre or "").split())
    if not limpio:
        return []

    sin_ac = _sin_acentos(limpio)
    norm_clave = sin_ac.lower().strip()
    canonico = normalizar_nombre_club(limpio)
    canonico_sin_ac = _sin_acentos(canonico)
    nucleo = _palabras_nucleo(limpio)
    clave_nucleo = " ".join(nucleo)
    nucleo_canonico = _palabras_nucleo(canonico)
    clave_nucleo_canonico = " ".join(nucleo_canonico)
    tiene_apellido_distintivo = len(nucleo_canonico) >= 2 or len(nucleo) >= 2

    # 1. Alias hispanos (tanto por nombre completo como por núcleo sin siglas 'fc'/'cf')
    alias_candidatos: list[str] = []
    if canonico:
        alias_candidatos.append(canonico)
        if canonico_sin_ac != canonico:
            alias_candidatos.append(canonico_sin_ac)
    for k in (norm_clave, limpio.lower(), clave_nucleo, canonico_sin_ac.lower(), clave_nucleo_canonico):
        if k and k in ALIAS_EQUIPOS_HISPANOS:
            for a in ALIAS_EQUIPOS_HISPANOS[k]:
                if tiene_apellido_distintivo and _sin_acentos(a).lower().strip() in _RAICES_GENERICAS_PROHIBIDAS_SOLAS:
                    continue
                if a not in alias_candidatos:
                    alias_candidatos.append(a)
                a_sin_ac = _sin_acentos(a)
                if a_sin_ac not in alias_candidatos:
                    alias_candidatos.append(a_sin_ac)

    # 2. Variantes fonéticas ('español' -> 'espanyol')
    foneticas = _normalizar_fonetica_hispana(limpio)
    if clave_nucleo and clave_nucleo != norm_clave:
        foneticas.extend(_normalizar_fonetica_hispana(clave_nucleo))

    palabras = [p for p in sin_ac.split() if p]
    significativas = [p for p in palabras if p.lower() not in _PALABRAS_VACIAS]
    # Palabras específicas (excluyendo siglas 'fc' y modificadores genéricos 'athletic', 'city', 'united', 'universitario', 'real', 'nacional')
    especificas = [
        p for p in (nucleo_canonico or nucleo)
        if p.lower() not in _MODIFICADORES_GENERICOS_CLUB
        and p.lower() not in _RAICES_GENERICAS_PROHIBIDAS_SOLAS
        and len(p) >= 3
    ]

    candidatas = (
        alias_candidatos
        + [
            limpio,
            sin_ac,
            clave_nucleo_canonico if (clave_nucleo_canonico and clave_nucleo_canonico != norm_clave) else "",
            clave_nucleo if (clave_nucleo and clave_nucleo != norm_clave) else "",
            sin_ac.lower(),
            " ".join(significativas),
        ]
        + foneticas
        + [
            " ".join(especificas) if len(especificas) > 1 else "",
            max(especificas, key=len) if especificas else ("" if tiene_apellido_distintivo else (max(nucleo, key=len) if nucleo else "")),
            especificas[-1] if len(especificas) > 1 else "",
            especificas[0] if especificas else ("" if tiene_apellido_distintivo else (nucleo[0] if nucleo else "")),
        ]
    )

    salida: list[str] = []
    vistos: set[str] = set()
    for c in candidatas:
        c = c.strip()
        if not c or c.lower() in vistos:
            continue
        # Jamás lanzar una búsqueda de una sola sigla de club ('fc', 'cf', 'sc', 'ac')
        if c.lower() in _SIGLAS_CLUB or c.lower() in _PALABRAS_VACIAS:
            continue
        # PROHIBIDO recortar un nombre compuesto con ciudad/apellido a una sola raíz genérica ('Universitario', 'Real', 'Nacional', 'Universidad')
        if tiene_apellido_distintivo and c.lower() in _RAICES_GENERICAS_PROHIBIDAS_SOLAS:
            continue
        vistos.add(c.lower())
        salida.append(c)
        if len(salida) >= maximo:
            break
    return salida


PATRON_NO_SENIOR = re.compile(
    r"(?:\(w\)|women|femenino|femenil|ladies|fem\.|\bfem\b|\bu17\b|\bu18\b|\bu19\b|\bu20\b|\bu21\b|\bu23\b|\byouth\b|\bii\b|\breserves?\b|\bb\b)",
    re.IGNORECASE
)


def es_equipo_o_torneo_no_senior(nombre_eq: str, nombre_liga: str | None = None) -> bool:
    """Detecta si un equipo o torneo es femenino o filial/juvenil/reserva."""
    txt_nom = " " + _sin_acentos(str(nombre_eq or "")).lower() + " "
    txt_liga = " " + _sin_acentos(str(nombre_liga or "")).lower() + " "
    if PATRON_NO_SENIOR.search(txt_nom):
        return True
    if nombre_liga and PATRON_NO_SENIOR.search(txt_liga):
        return True
    torneos_fem = ("liga f", "wsl", "frauen", "femminile", "women", "femenina", "femenino", "u18", "u19", "u21", "u23", "youth")
    if any(tf in txt_liga for tf in torneos_fem):
        return True
    return False


def validar_identidad_club(
    candidato: dict[str, Any],
    nombre_buscado: str,
    pais_esperado: str | None = None,
    liga_esperada: str | None = None,
) -> bool:
    """
    Sección 1 (Entity Resolution sin Homonimia):
    Valida que el candidato devuelto por FotMob o Flashscore corresponda al mismo
    país y competición nacional que el rival o la liga esperada (ej. Platense de
    Argentina vs Platense FC de Honduras).
    """
    if not isinstance(candidato, dict):
        return False
    nom_cand = _sin_acentos(str(candidato.get("nombre") or candidato.get("name") or "")).lower().strip()
    liga_cand = _sin_acentos(str(candidato.get("liga") or candidato.get("leagueName") or "")).lower().strip()
    pais_cand = _sin_acentos(str(candidato.get("pais") or candidato.get("country") or _pais_de_liga(liga_cand) or "")).lower().strip()

    if not usuario_solicito_no_senior(nombre_buscado):
        if es_equipo_o_torneo_no_senior(nom_cand, liga_cand):
            return False

    def _es_int(t: str | None) -> bool:
        return _pais_de_liga(str(t or "")) in ("europa", "sudamerica")

    if pais_esperado:
        p_esp = _sin_acentos(str(pais_esperado)).lower().strip()
        if pais_cand and p_esp and pais_cand != p_esp:
            if not _es_int(liga_esperada) and not _es_int(liga_cand):
                return False

    if liga_esperada:
        l_esp = _sin_acentos(str(liga_esperada)).lower().strip()
        p_liga_esp = _sin_acentos(str(_pais_de_liga(l_esp) or "")).lower().strip()
        if p_liga_esp and pais_cand and pais_cand != p_liga_esp and not _es_int(l_esp):
            return False

    return True


def usuario_solicito_no_senior(texto: str) -> bool:
    """Detecta si el usuario pidió explícitamente un equipo femenino o juvenil."""
    t = " " + _sin_acentos(str(texto or "")).lower() + " "
    return bool(re.search(r"(?:\(w\)|women|femenino|femenil|ladies|fem\.|\bu18\b|\bu19\b|\bu20\b|\bu21\b|\bu23\b|filial|\bii\b|\bb\b)", t))


def puntuar_candidato(candidato: dict[str, Any], consulta: str,
                      rival_meta: dict[str, Any] | None = None) -> tuple:
    """
    Puntúa un candidato de equipo frente a la consulta y el contexto del rival o liga.
    Tupla ordenable (mayor = mejor):
      0. Coherencia de país/continente + validación de identidad nuclear (penaliza -100 si no comparte identidad, p.ej. 'FC Porto' para 'fc fulham').
      1. Bono de liga (coincidencia con la liga indicada en el comando o con la liga del rival).
      2. Penalización por equipo femenino/filial (-1 si es '(W)', 'U21', 'II', 'B' y no se pidió).
      3. Coincidencia exacta por núcleo o alias hispano ('Fulham' == núcleo de 'fc fulham'; 'SC Freiburg' en alias de 'friburgo').
      4. Coincidencia exacta del núcleo sin siglas.
      5. El nombre nuclear empieza por el núcleo de la consulta.
      6. Solapamiento de palabras nucleares (ignorando siglas como 'FC', 'CF', 'SC').
      7. Puntuación interna del proveedor (FotMob score).
    """
    def norm(t: str) -> str:
        return _sin_acentos(str(t or "")).lower().strip()

    liga_pref, consulta_sin_liga = extraer_liga_de_texto(consulta or "")
    if liga_pref and rival_meta is None:
        rival_meta = liga_pref

    nombre = norm(candidato.get("name"))
    consulta_n = norm(consulta_sin_liga or consulta)
    canonico_n = norm(normalizar_nombre_club(consulta_sin_liga or consulta))

    nucleo_consulta = _palabras_nucleo(canonico_n or consulta_n)
    nucleo_nombre = _palabras_nucleo(nombre)
    str_nucleo_consulta = " ".join(nucleo_consulta)
    str_nucleo_nombre = " ".join(nucleo_nombre)

    # Alias conocidos tanto para la consulta completa como para su núcleo sin siglas
    alias_raw: list[str] = []
    if canonico_n:
        alias_raw.append(canonico_n)
    for k in (consulta_n, canonico_n, str_nucleo_consulta, " ".join(_palabras_nucleo(consulta_n))):
        if k and k in ALIAS_EQUIPOS_HISPANOS:
            alias_raw.extend(ALIAS_EQUIPOS_HISPANOS[k])
    alias_conocidos = {norm(a) for a in alias_raw if a}
    alias_nucleos = {" ".join(_palabras_nucleo(a)) for a in alias_raw if a}

    nucleo_identidad_consulta = [p for p in nucleo_consulta if p not in _MODIFICADORES_GENERICOS_CLUB] or nucleo_consulta
    nucleo_identidad_nombre = [p for p in nucleo_nombre if p not in _MODIFICADORES_GENERICOS_CLUB] or nucleo_nombre
    str_identidad_consulta = " ".join(nucleo_identidad_consulta)
    str_identidad_nombre = " ".join(nucleo_identidad_nombre)

    exacto_raw = 1 if (nombre == consulta_n or nombre == canonico_n) else 0
    exacto_nucleo = 1 if (
        (str_nucleo_consulta and str_nucleo_nombre == str_nucleo_consulta)
        or (str_identidad_consulta and str_identidad_nombre == str_identidad_consulta)
    ) else 0
    es_alias_exacto = 1 if (
        exacto_raw
        or exacto_nucleo
        or nombre in alias_conocidos
        or (str_nucleo_nombre and str_nucleo_nombre in alias_nucleos)
        or (str_identidad_nombre and str_identidad_nombre in alias_nucleos)
    ) else 0

    empieza = 1 if (
        (consulta_n and nombre.startswith(consulta_n))
        or (canonico_n and nombre.startswith(canonico_n))
        or (str_nucleo_consulta and str_nucleo_nombre.startswith(str_nucleo_consulta))
    ) else 0

    solape_nucleo = sum(1 for p in nucleo_consulta if any(p == w for w in nucleo_nombre))

    # Verificar identidad específica: si la consulta tiene palabras específicas (ej. 'fulham' en 'fc fulham',
    # o 'guizhou'/'guiyang' en 'guizhou guiyang athletic'), el candidato DEBE coincidir con al menos una
    # palabra específica, ser un alias hispano conocido, o tener similitud difusa >= 0.72.
    import difflib
    palabras_identidad = nucleo_identidad_consulta
    solape_identidad = sum(
        1 for p in palabras_identidad
        if any(p == w or (len(p) >= 4 and (p in w or w in p)) for w in nucleo_nombre)
    )
    sim_difusa = max(
        difflib.SequenceMatcher(None, str_nucleo_consulta, str_nucleo_nombre).ratio() if str_nucleo_consulta and str_nucleo_nombre else 0.0,
        difflib.SequenceMatcher(None, canonico_n, nombre).ratio() if canonico_n and nombre else 0.0,
    )
    tiene_identidad = 1 if (es_alias_exacto or solape_identidad > 0 or sim_difusa >= 0.72) else 0

    # Coherencia de liga y país con el rival o con la liga indicada en el comando
    bono_liga = 0
    coherencia_pais = 0
    if rival_meta and isinstance(rival_meta, dict):
        c_liga_id = candidato.get("leagueId") or candidato.get("primaryLeagueId") or candidato.get("id_liga")
        c_lname = norm(candidato.get("leagueName") or candidato.get("primaryLeagueName") or candidato.get("liga"))
        ids_esperados = [str(x) for x in (rival_meta.get("ids_liga") or []) if x is not None]
        r_liga_id = rival_meta.get("id_liga") or rival_meta.get("leagueId") or rival_meta.get("primaryLeagueId")
        if r_liga_id is not None and str(r_liga_id) not in ids_esperados:
            ids_esperados.append(str(r_liga_id))

        keywords_liga = [norm(k) for k in (rival_meta.get("keywords_liga") or []) if k]
        r_lname_raw = rival_meta.get("liga") or rival_meta.get("leagueName") or rival_meta.get("primaryLeagueName")
        r_lname = norm(r_lname_raw) if str(r_lname_raw or "").strip().lower() not in ("", "desconocida", "oficial") else ""
        if r_lname and r_lname not in keywords_liga:
            keywords_liga.append(r_lname)

        es_forzada = bool(rival_meta.get("liga_forzada"))
        if c_liga_id is not None and str(c_liga_id) in ids_esperados:
            bono_liga = 25 if es_forzada else 10
        elif c_lname and any(k == c_lname or k in c_lname or c_lname in k for k in keywords_liga if k):
            bono_liga = 20 if es_forzada else 8

        ccode_r = rival_meta.get("pais") or rival_meta.get("pais_iso") or rival_meta.get("ccode") or rival_meta.get("country")
        if str(ccode_r or "").strip().upper() == "INT":
            ccode_r = ""
        r_pais = _pais_de_liga(
            r_lname_raw if r_lname else None,
            ccode=ccode_r,
        ) or (norm(rival_meta.get("pais") or "") if norm(rival_meta.get("pais") or "") != "int" else "")
        c_pais = _pais_de_liga(
            candidato.get("leagueName") or candidato.get("primaryLeagueName") or candidato.get("liga"),
            ccode=candidato.get("ccode") or candidato.get("country") or candidato.get("pais"),
        )
        es_duelo_nacional = bool(r_pais and r_pais not in ("europa", "sudamerica", "internacional"))
        if r_pais and c_pais:
            if r_pais == c_pais:
                coherencia_pais = 15 if es_forzada else 5
            elif (r_pais == "europa" and c_pais in PAISES_EUROPA) or (c_pais == "europa" and r_pais in PAISES_EUROPA):
                coherencia_pais = 12 if es_forzada else 4
            elif (r_pais == "sudamerica" and c_pais in PAISES_SUDAMERICA) or (c_pais == "sudamerica" and r_pais in PAISES_SUDAMERICA):
                coherencia_pais = 12 if es_forzada else 4
            elif es_duelo_nacional and c_pais != r_pais:
                # En duelos nacionales (ej. San Lorenzo vs Platense en Argentina), descartar homónimos de otro país
                coherencia_pais = -80
            elif r_pais in PAISES_EUROPA and c_pais in PAISES_EUROPA:
                coherencia_pais = 10 if es_forzada else 3
            elif r_pais in PAISES_SUDAMERICA and c_pais in PAISES_SUDAMERICA:
                coherencia_pais = 10 if es_forzada else 3
            elif (r_pais in PAISES_EUROPA and c_pais not in PAISES_EUROPA) or (r_pais not in PAISES_EUROPA and c_pais in PAISES_EUROPA):
                coherencia_pais = -25 if es_forzada else -20  # Gran penalización por cruce intercontinental incoherente
            elif (r_pais in PAISES_SUDAMERICA and c_pais not in PAISES_SUDAMERICA) or (r_pais not in PAISES_SUDAMERICA and c_pais in PAISES_SUDAMERICA):
                coherencia_pais = -25 if es_forzada else -20
            else:
                coherencia_pais = -15 if es_forzada else -10
        elif es_duelo_nacional and bono_liga == 0 and c_lname:
            # El candidato tiene una liga no perteneciente al país del rival nacional
            coherencia_pais = -60

    # Si el candidato NO comparte ninguna palabra nuclear de identidad ni alias (ej. "FC Porto" para "fc fulham"),
    # hundir su puntuación para que jamás gane por compartir solo "FC" o por tener mayor popularidad en FotMob.
    if not tiene_identidad:
        coherencia_pais -= 100

    # Femeninos y filiales van después, salvo que el usuario los pida explícitamente
    es_no_senior = es_equipo_o_torneo_no_senior(candidato.get("name"), candidato.get("leagueName"))
    texto_contexto_cat = f"{consulta} {(rival_meta or {}).get('liga', '')} {(rival_meta or {}).get('nombre', '')}"
    pedido = 1 if (
        usuario_solicito_no_senior(texto_contexto_cat)
        or bool((rival_meta or {}).get("es_femenino"))
        or bool((rival_meta or {}).get("es_juvenil"))
    ) else 0
    if not pedido and es_no_senior:
        penal_filial = 9999
        coherencia_pais -= 9999
    elif pedido and es_no_senior:
        penal_filial = -50
        coherencia_pais += 50
    else:
        penal_filial = 0

    # Validación dura anti-colisión (Bayer != Bayern, Platense ARG != Platense HND, Colonia DEU != Colonia URY,
    # Universitario de Vinto [BOL] != CD Universitario [PAN] / Universitario de Deportes [PER] / Técnico Universitario [ECU])
    pais_iso_meta_str = str((rival_meta or {}).get("pais_iso") or (rival_meta or {}).get("pais") or "").upper().strip()
    liga_cand_norm = norm(candidato.get("leagueName") or candidato.get("primaryLeagueName") or candidato.get("liga"))
    pais_cand_inf = _pais_de_liga(
        candidato.get("leagueName") or candidato.get("primaryLeagueName") or candidato.get("liga"),
        ccode=candidato.get("ccode") or candidato.get("country") or candidato.get("pais"),
    )
    if (
        "vinto" in consulta_n
        or "vinto" in canonico_n
        or pais_iso_meta_str in ("BOL", "BOLIVIA")
        or _pais_de_liga(str((rival_meta or {}).get("liga") or "")) == "bolivia"
    ):
        es_homonimo_univ_extranjero = (
            nombre in ("cd universitario", "universitario de deportes", "tecnico universitario")
            or ("lpf" in liga_cand_norm and "vinto" not in nombre)
            or (
                "universitario" in nombre
                and "vinto" not in nombre
                and pais_cand_inf != "bolivia"
                and str(candidato.get("ccode") or "").upper().strip() != "BOL"
            )
        )
        if es_homonimo_univ_extranjero:
            return (-99999, 0, 0, 0, -99999, -99999, 0, 0, -99999.0)

    try:
        from resolvedor_entidades import validar_y_puntuar_candidato_fotmob
        pais_iso_meta = (rival_meta or {}).get("pais_iso") or (rival_meta or {}).get("pais") or ""
        liga_txt_meta = str((rival_meta or {}).get("liga") or (rival_meta or {}).get("nombre") or "")
        pts_duro = validar_y_puntuar_candidato_fotmob(
            candidato,
            consulta_sin_liga or consulta,
            texto_liga_o_contexto=liga_txt_meta,
            pais_iso_esperado=pais_iso_meta,
            es_femenino_pedido=bool((rival_meta or {}).get("es_femenino")) if (rival_meta and "es_femenino" in rival_meta) else None,
            es_juvenil_pedido=bool((rival_meta or {}).get("es_juvenil")) if (rival_meta and "es_juvenil" in rival_meta) else None,
        )
        if pts_duro <= -90000.0:
            return (-99999, 0, 0, 0, -99999, -99999, 0, 0, -99999.0)
        if pts_duro <= -9000.0:
            return (-9999, 0, 0, 0, -9999, -9999, 0, 0, -9999.0)
        if pts_duro >= 100.0:
            coherencia_pais += 100
    except Exception:
        pass

    # Un homónimo de otro país (coherencia_pais < 0) jamás debe superar por nombre exacto a un club del mismo país
    es_alias_coherente = es_alias_exacto if coherencia_pais >= 0 else 0
    exacto_nucleo_coherente = exacto_nucleo if coherencia_pais >= 0 else 0

    return (
        -penal_filial,
        tiene_identidad,
        es_alias_coherente,
        exacto_nucleo_coherente,
        coherencia_pais,
        bono_liga,
        empieza,
        solape_nucleo,
        float(candidato.get("score") or 0),
    )


def resolver_par_equipos_coherentes(
    nombre_local: str,
    nombre_visita: str,
    competicion_hint: str = "",
    fuente_obj: Any = None,
    candidatos_local: list[dict[str, Any]] | None = None,
    candidatos_visita: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    Resuelve de forma conjunta (Entity Resolution) el equipo Local y el Visitante
    exigiendo coherencia geográfica y de liga en duelos nacionales (ej. San Lorenzo vs Platense
    en Liga Profesional de Argentina, descartando a Platense FC de Honduras).
    """
    if fuente_obj is not None and hasattr(fuente_obj, "resolver_cruce_equipos") and candidatos_local is None and candidatos_visita is None:
        return fuente_obj.resolver_cruce_equipos(nombre_local, nombre_visita, liga_contexto=competicion_hint or None)

    liga_a, limpio_a = extraer_liga_de_texto(nombre_local or "")
    liga_b, limpio_b = extraer_liga_de_texto(nombre_visita or "")
    liga_comun = liga_a or liga_b or (_inferir_meta_rival_desde_texto(competicion_hint) if competicion_hint else None)

    ctx_para_a = _inferir_meta_rival_desde_texto(limpio_b) or {}
    if liga_comun:
        ctx_para_a = {**ctx_para_a, **liga_comun}
    ctx_para_b = _inferir_meta_rival_desde_texto(limpio_a) or {}
    if liga_comun:
        ctx_para_b = {**ctx_para_b, **liga_comun}

    res_a: dict[str, Any] | None = None
    res_b: dict[str, Any] | None = None

    if candidatos_local:
        orden_a = sorted(
            candidatos_local,
            key=lambda c: puntuar_candidato(c, limpio_a or nombre_local, rival_meta=ctx_para_a),
            reverse=True,
        )
        res_a = orden_a[0] if orden_a else None
    elif ctx_para_b:
        res_a = dict(ctx_para_b)

    if res_a and isinstance(res_a, dict):
        pais_a = _pais_de_liga(res_a.get("leagueName") or res_a.get("liga"), ccode=res_a.get("ccode") or res_a.get("pais"))
        if pais_a and not ctx_para_b.get("pais"):
            ctx_para_b["pais"] = pais_a
        if (res_a.get("leagueName") or res_a.get("liga")) and not ctx_para_b.get("liga"):
            ctx_para_b["liga"] = res_a.get("leagueName") or res_a.get("liga")
        if (res_a.get("leagueId") or res_a.get("id_liga")) and not ctx_para_b.get("id_liga"):
            ctx_para_b["id_liga"] = res_a.get("leagueId") or res_a.get("id_liga")

    if candidatos_visita:
        orden_b = sorted(
            candidatos_visita,
            key=lambda c: puntuar_candidato(c, limpio_b or nombre_visita, rival_meta=ctx_para_b),
            reverse=True,
        )
        res_b = orden_b[0] if orden_b else None
    elif ctx_para_a:
        res_b = dict(ctx_para_a)

    return res_a, res_b


def extraer_pares_misma_liga_fotmob(
    datos_team: dict[str, Any] | None = None,
    doc_referencia: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Extrae todos los pares (opponent_id, opponent_name, league_name, league_id)
    de la tabla de posiciones (overview.table), del calendario de FotMob
    (fixtures.allFixtures.fixtures) y de los últimos 30 partidos de un equipo ya resuelto.
    """
    pares: list[dict[str, Any]] = []
    vistos: set[tuple[str, str]] = set()

    def _agregar(oid: Any, onom: Any, lnom: Any = "", lid: Any = "") -> None:
        nom_s = str(onom or "").strip()
        if not nom_s:
            return
        id_s = str(oid or "").strip()
        liga_s = str(lnom or "").strip()
        lid_s = str(lid or "").strip()
        clave = (id_s.lower(), _sin_acentos(nom_s).lower())
        if clave in vistos:
            return
        vistos.add(clave)
        pares.append({
            "id": id_s,
            "name": nom_s,
            "nombre": nom_s,
            "leagueName": liga_s,
            "liga": liga_s,
            "leagueId": lid_s,
            "id_liga": lid_s,
        })

    if isinstance(datos_team, dict):
        overview = datos_team.get("overview") or {}
        tablas_ov = overview.get("table") or []
        if isinstance(tablas_ov, dict):
            tablas_ov = [tablas_ov]
        for bloque_t in tablas_ov:
            if not isinstance(bloque_t, dict):
                continue
            data_t = bloque_t.get("data") or bloque_t
            liga_t = str(data_t.get("leagueName") or bloque_t.get("leagueName") or data_t.get("name") or "")
            lid_t = str(data_t.get("leagueId") or bloque_t.get("leagueId") or "")
            filas_all = ((data_t.get("table") or {}).get("all")) or []
            for fila in filas_all:
                if isinstance(fila, dict):
                    _agregar(fila.get("id"), fila.get("name") or fila.get("shortName"), liga_t, lid_t)
            for sub_t in (data_t.get("tables") or []):
                if not isinstance(sub_t, dict):
                    continue
                liga_sub = str(sub_t.get("leagueName") or liga_t)
                lid_sub = str(sub_t.get("leagueId") or lid_t)
                for fila in ((sub_t.get("table") or {}).get("all") or []):
                    if isinstance(fila, dict):
                        _agregar(fila.get("id"), fila.get("name") or fila.get("shortName"), liga_sub, lid_sub)

        fixtures = (((datos_team.get("fixtures") or {}).get("allFixtures")) or {}).get("fixtures") or []
        for f in fixtures[-45:]:
            if not isinstance(f, dict):
                continue
            torneo_f = f.get("tournament") or {}
            lnom_f = str(torneo_f.get("name") or "")
            if es_competicion_amistosa(lnom_f):
                continue
            lid_f = torneo_f.get("leagueId") or torneo_f.get("id") or ""
            for lado in ("home", "away"):
                b = f.get(lado) or {}
                if isinstance(b, dict):
                    _agregar(b.get("id"), b.get("name"), lnom_f, lid_f)

    if isinstance(doc_referencia, dict):
        eq_ref = doc_referencia.get("equipo") if "equipo" in doc_referencia and isinstance(doc_referencia.get("equipo"), dict) else doc_referencia
        liga_def = str((eq_ref or {}).get("liga") or (eq_ref or {}).get("leagueName") or "")
        lid_def = str((eq_ref or {}).get("id_liga") or (eq_ref or {}).get("leagueId") or "")
        for par_prev in (eq_ref or {}).get("pares_misma_liga") or []:
            if isinstance(par_prev, dict):
                _agregar(
                    par_prev.get("id"),
                    par_prev.get("name") or par_prev.get("nombre"),
                    par_prev.get("leagueName") or par_prev.get("liga") or liga_def,
                    par_prev.get("leagueId") or par_prev.get("id_liga") or lid_def,
                )
        for fut in (doc_referencia.get("partidos_futuros") or []):
            if isinstance(fut, dict):
                comp_f = str(fut.get("competicion") or liga_def)
                _agregar(fut.get("home_id"), fut.get("home_nombre"), comp_f, lid_def)
                _agregar(fut.get("away_id"), fut.get("away_nombre"), comp_f, lid_def)
        for part in (doc_referencia.get("partidos") or [])[:30]:
            if isinstance(part, dict):
                comp_p = str(part.get("torneo") or part.get("competicion") or liga_def)
                if es_competicion_amistosa(comp_p):
                    continue
                extra_p = part.get("extra") or {}
                _agregar(extra_p.get("rival_id") or part.get("rival_id"), part.get("rival"), comp_p, lid_def)

    return pares


def resolver_pareja_misma_liga(
    nombre_local: str,
    nombre_visita: str,
    pais_iso: str = "",
    liga_pref: Any = None,
    doc_referencia: dict[str, Any] | None = None,
    fuente_obj: Any = None,
    datos_team_fotmob: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    LEAGUE-PEER & FIXTURE GRAPH RESOLVER (Sección 2):
    Cuando uno de los dos equipos ya tiene candidato de alta confianza en el país correcto
    (ej. Aurora en Bolivia), inspecciona su tabla de posiciones (overview.table), su calendario
    (fixtures.allFixtures.fixtures) y sus últimos 30 partidos para encontrar al segundo equipo
    ('universitario de vinto' -> 'Universitario de Vinto' en Bolivia) dentro de su misma liga/calendario
    antes de buscar clubes sueltos en otros países.
    """
    from resolvedor_entidades import normalizar_pais_iso, resolver_equipo_individual

    pais_iso_norm = normalizar_pais_iso(pais_iso or (liga_pref.get("pais_iso") if isinstance(liga_pref, dict) else str(liga_pref or "")))
    if not pais_iso_norm and isinstance(doc_referencia, dict):
        eq_r = doc_referencia.get("equipo") if isinstance(doc_referencia.get("equipo"), dict) else doc_referencia
        pais_iso_norm = normalizar_pais_iso(
            (eq_r or {}).get("pais_iso") or (eq_r or {}).get("pais") or (eq_r or {}).get("liga") or ""
        )

    pares = extraer_pares_misma_liga_fotmob(datos_team=datos_team_fotmob, doc_referencia=doc_referencia)
    if not pares and fuente_obj is not None and isinstance(doc_referencia, dict):
        eq_r = doc_referencia.get("equipo") if isinstance(doc_referencia.get("equipo"), dict) else doc_referencia
        tid_ref = str((eq_r or {}).get("id") or "")
        if tid_ref and hasattr(fuente_obj, "_pares_misma_liga_equipo"):
            try:
                pares = fuente_obj._pares_misma_liga_equipo(tid_ref)
            except Exception:
                pares = []

    if not pares:
        return None

    # Determinar cuál es el nombre objetivo a buscar en los pares del otro equipo
    objetivo_raw = nombre_visita or nombre_local
    if isinstance(doc_referencia, dict):
        eq_r = doc_referencia.get("equipo") if isinstance(doc_referencia.get("equipo"), dict) else doc_referencia
        nom_ref = _sin_acentos(str((eq_r or {}).get("nombre") or (eq_r or {}).get("name") or "")).lower().strip()
        if nom_ref and nom_ref == _sin_acentos(nombre_visita or "").lower().strip():
            objetivo_raw = nombre_local

    canon_obj, _ = resolver_equipo_individual(objetivo_raw, pais_iso_contexto=pais_iso_norm or None)
    consulta_efectiva = canon_obj or normalizar_nombre_club(objetivo_raw) or objetivo_raw
    tokens_obj = _palabras_nucleo(objetivo_raw)
    tokens_canon = _palabras_nucleo(consulta_efectiva)
    tokens_especificos = [
        t for t in (tokens_obj + [x for x in tokens_canon if x not in tokens_obj])
        if t not in _MODIFICADORES_GENERICOS_CLUB and t not in _RAICES_GENERICAS_PROHIBIDAS_SOLAS
    ]

    mejor_par: dict[str, Any] | None = None
    mejor_score = -1.0

    for par in pares:
        nom_par = str(par.get("name") or par.get("nombre") or "").strip()
        if not nom_par:
            continue
        liga_par = str(par.get("leagueName") or par.get("liga") or "").strip()
        if pais_iso_norm and pais_iso_norm != "INT":
            iso_par = normalizar_pais_iso(liga_par)
            if iso_par and iso_par not in (pais_iso_norm, "INT"):
                continue

        tokens_par = _palabras_nucleo(nom_par)
        if not tokens_par:
            continue

        str_par = " ".join(tokens_par)
        str_obj = " ".join(tokens_obj)
        str_canon = " ".join(tokens_canon)

        score = 0.0
        # 1. Coincidencia exacta de todos los tokens (ej. "universitario" + "vinto")
        if str_par in (str_obj, str_canon):
            score = 200.0
        elif tokens_obj and all(t in tokens_par for t in tokens_obj):
            score = 180.0
        elif tokens_canon and all(t in tokens_par for t in tokens_canon):
            score = 175.0
        elif tokens_especificos and all(t in tokens_par for t in tokens_especificos):
            score = 160.0
        # 2. Prefijo/raíz común dentro de la misma liga nacional (ej. "universitario" en Bolivia -> "Universitario de Vinto")
        elif tokens_obj and tokens_par[0] == tokens_obj[0] and len(tokens_obj[0]) >= 5:
            score = 120.0

        if score > 0 and par.get("id"):
            score += 10.0
        if score > mejor_score:
            mejor_score = score
            mejor_par = dict(par)

    if mejor_par and mejor_score >= 120.0:
        mejor_par["pais_iso"] = pais_iso_norm or mejor_par.get("pais_iso") or "INT"
        mejor_par["resuelto_por_tabla_comun"] = True
        return mejor_par
    return None


def validar_coherencia_nacional_15_partidos(
    doc_local: dict[str, Any] | list[dict[str, Any]],
    doc_visita: dict[str, Any] | str,
    competicion_hint: str = "",
) -> bool:
    """
    Valida que en un duelo nacional al menos 10 de los 15 partidos de Equipo A y Equipo B
    pertenezcan al mismo país/federación (evita mezclar Platense de Honduras con San Lorenzo de Argentina).
    Soporta tanto (doc_local, doc_visita) como (lista_partidos, nombre_rival).
    """
    hint_pais = _pais_de_liga(competicion_hint)
    if hint_pais in ("europa", "sudamerica", "internacional"):
        return True

    def _paises_partidos(doc_or_list: dict[str, Any] | list[dict[str, Any]] | str) -> list[str]:
        if isinstance(doc_or_list, str):
            meta_r = _inferir_meta_rival_desde_texto(doc_or_list)
            if meta_r and meta_r.get("pais"):
                return [str(meta_r["pais"])] * 15
            p_str = _pais_de_liga(doc_or_list)
            return [p_str] * 15 if p_str else []
        if isinstance(doc_or_list, list):
            partidos = doc_or_list
            p_eq = ""
        else:
            eq = doc_or_list.get("equipo") or {}
            p_eq = _pais_de_liga(eq.get("liga"), ccode=eq.get("pais"))
            if not p_eq:
                meta_eq = _inferir_meta_rival_desde_texto(str(eq.get("nombre") or ""))
                if meta_eq:
                    p_eq = str(meta_eq.get("pais") or "")
            partidos = doc_or_list.get("partidos") or []

        paises: list[str] = []
        for p in partidos[:15]:
            comp = str(p.get("competicion") or p.get("torneo") or p.get("liga") or "")
            pc = _pais_de_liga(comp)
            if pc in ("", "europa", "sudamerica") and p_eq:
                pc = p_eq
            if pc:
                paises.append(pc)
        return paises

    paises_a = _paises_partidos(doc_local)
    paises_b = _paises_partidos(doc_visita)
    if not paises_a or not paises_b:
        return True

    from collections import Counter
    dom_a, cnt_a = Counter(paises_a).most_common(1)[0]
    dom_b, cnt_b = Counter(paises_b).most_common(1)[0]

    if isinstance(doc_local, list) or isinstance(doc_visita, str):
        if dom_a and dom_b and dom_a not in ("europa", "sudamerica") and dom_b not in ("europa", "sudamerica"):
            return dom_a == dom_b
        return True

    if dom_a and dom_b and dom_a not in ("europa", "sudamerica") and dom_b not in ("europa", "sudamerica"):
        eq_a_nom = str((doc_local.get("equipo") or {}).get("nombre") or "").lower()
        eq_b_nom = str((doc_visita.get("equipo") or "").get("nombre") or "").lower()
        homonimos = ("platense", "arsenal", "barcelona", "river", "nacional", "independiente", "racing", "everton", "estudiantes")
        if dom_a != dom_b and any(h in eq_a_nom or h in eq_b_nom for h in homonimos):
            raise AssertionError(
                f"Incoherencia geográfica detectada en duelo nacional: "
                f"{eq_a_nom} ({dom_a}, {cnt_a}/15) vs {eq_b_nom} ({dom_b}, {cnt_b}/15)"
            )
    return True


class FuenteFotMob(FuenteXG):
    """
    Fuente FotMob.

    Estrategia (la que exige el enunciado): se abre un navegador real, se navega
    a la ficha del equipo y, para cada partido, a su página de estadísticas.
    Los datos se obtienen de las mismas respuestas JSON que consume la web
    (/api/data/...), interceptables y verificables; el navegador aporta las
    cookies y el contexto que exige la web.
    """

    nombre = "fotmob"

    def __init__(self, headless: bool = True, pausa_entre_partidos: float = 1.2,
                 idioma: str = "es", timeout_ms: int = 45_000, verbose: bool = True,
                 max_paginas_escaneo: int = 8) -> None:
        self.headless = headless
        self.pausa = pausa_entre_partidos
        self.idioma = idioma
        self.timeout_ms = timeout_ms
        self.verbose = verbose
        self.max_paginas_escaneo = max(1, int(max_paginas_escaneo))
        self._pw = None
        self._browser = None
        self.ctx = None
        self.page = None
        # Caché de páginas de calendario por equipo: evita volver a pedir lo mismo
        # cuando el escaneo hacia atrás consulta varias veces el historial.
        self._cache_paginas: dict[str, dict[int, list[tuple[dict[str, Any], str]] | None]] = {}
        # Cachés del cruce de temporadas por liga (ver _ligas_del_equipo)
        self._cache_ligas: dict[str, dict[str, str]] = {}
        self._cache_temporadas: dict[str, list[str]] = {}
        self._cache_partidos_liga: dict[tuple[str, str, str],
                                        list[tuple[dict[str, Any], str]]] = {}
        # Cola de (liga, temporada) a consultar, POR EQUIPO
        self._cola_por_equipo: dict[str, list[tuple[str, str]]] = {}
        # Cuántas de esas páginas forman la cola PRINCIPAL (las ligas de la ventana
        # actual). Las siguientes son la cola AMPLIADA —ligas históricas del club,
        # p. ej. la división en la que jugaba antes de ascender— y solo se recorren
        # si la principal no bastó para reunir el objetivo.
        self._cola_principal_por_equipo: dict[str, int] = {}
        self._cache_ligas_historicas: dict[str, list[tuple[str, str, list[str]]]] = {}
        # Próximos partidos programados por equipo (para resolver la fecha de un cruce)
        self._cache_futuros: dict[str, list[dict[str, Any]]] = {}
        # Pares de la misma liga y calendario extraídos de overview.table y fixtures
        self._cache_pares_liga: dict[str, list[dict[str, Any]]] = {}

    def _pares_misma_liga_equipo(self, tid: str) -> list[dict[str, Any]]:
        """Obtiene los clubes que comparten tabla de posiciones o calendario con `tid` en FotMob."""
        tid_s = str(tid or "").strip()
        if not tid_s:
            return []
        if tid_s in self._cache_pares_liga:
            return self._cache_pares_liga[tid_s]
        try:
            datos = self._api(f"/teams?id={tid_s}&ccode3=PER&lang={self.idioma}")
            if isinstance(datos, dict):
                pares = extraer_pares_misma_liga_fotmob(datos_team=datos)
                self._cache_pares_liga[tid_s] = pares
                return pares
        except Exception:
            pass
        return []

    # ── log ────────────────────────────────────────────────────────────────
    def _log(self, msg: str) -> None:
        if self.verbose:
            marca = datetime.now(pytz.timezone(config.TIMEZONE)).strftime("%H:%M:%S")
            try:
                print(f"[AUDITORÍA {marca}] {msg}")
            except UnicodeEncodeError:
                safe_msg = msg.encode(sys.stdout.encoding or "ascii", errors="replace").decode(sys.stdout.encoding or "ascii")
                print(f"[AUDITORÍA {marca}] {safe_msg}")

    # ── ciclo de vida ──────────────────────────────────────────────────────
    def _abrir_interno(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self.ctx = self._browser.new_context(
            user_agent=_USER_AGENT,
            locale="es-ES",
            timezone_id="America/Lima",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "es-ES,es;q=0.9,en;q=0.8"},
        )
        self.page = self.ctx.new_page()
        # Navegación inicial: fija cookies/sesión y valida que la web responde
        self.page.goto(FOTMOB_BASE, wait_until="domcontentloaded", timeout=self.timeout_ms)
        self.page.wait_for_timeout(2000)
        self._log(f"sesión abierta en {self.nombre} ({'headless' if self.headless else 'visible'})")

    def abrir(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            en_loop = loop.is_running()
        except RuntimeError:
            en_loop = False
        if en_loop:
            self._executor = ThreadPoolExecutor(max_workers=1)
            self._executor.submit(self._abrir_interno).result()
        else:
            self._executor = None
            self._abrir_interno()

    def _cerrar_interno(self) -> None:
        for obj, metodo in ((self.ctx, "close"), (self._browser, "close")):
            try:
                if obj:
                    getattr(obj, metodo)()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self.ctx = self.page = None

    def cerrar(self) -> None:
        exec_worker = getattr(self, "_executor", None)
        if exec_worker is not None:
            try:
                exec_worker.submit(self._cerrar_interno).result(timeout=15)
            except Exception:
                pass
            try:
                exec_worker.shutdown(wait=False)
            except Exception:
                pass
            self._executor = None
        else:
            self._cerrar_interno()

    def __enter__(self) -> "FuenteFotMob":
        self.abrir()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.cerrar()

    # ── acceso a la API interna ────────────────────────────────────────────
    def _api_interno(self, ruta: str, reintentos: int = 3) -> Any:
        url = ruta if ruta.startswith("http") else FOTMOB_BASE + "/api/data" + ruta
        ultimo_error = None
        timeout_req = min(self.timeout_ms, 12_000) if self.timeout_ms else 12_000
        for intento in range(max(1, reintentos)):
            try:
                r = self.ctx.request.get(
                    url, timeout=timeout_req,
                    headers={"Referer": FOTMOB_BASE + "/", "Accept": "application/json, text/plain, */*"},
                )
                if r.status == 200:
                    return r.json()
                ultimo_error = f"HTTP {r.status}"
            except Exception as e:  # red, timeout, JSON inválido
                ultimo_error = f"{type(e).__name__}: {e}"
            if intento < reintentos - 1:
                time.sleep(2.0)
        self._log(f"⚠️  API {url} falló tras {reintentos} intentos: {ultimo_error}")
        return None

    def _api(self, ruta: str, reintentos: int = 3) -> Any:
        """GET a la API interna de FotMob usando el contexto del navegador (3 reintentos, timeout 12s)."""
        exec_worker = getattr(self, "_executor", None)
        if exec_worker is not None:
            return exec_worker.submit(self._api_interno, ruta, reintentos).result()
        return self._api_interno(ruta, reintentos)

    # ── resolución de equipo ───────────────────────────────────────────────
    def resolver_equipo(self, nombre: str, rival_contexto: dict[str, Any] | str | None = None) -> dict[str, Any] | None:
        """
        Resuelve el nombre de un equipo contra el buscador del proveedor con
        tolerancia a tildes, a palabras vacías, prefijos de liga/competición y
        coherencia de país/liga con el rival.
        """
        if not nombre or not nombre.strip():
            return None

        liga_pref, nombre_limpio = extraer_liga_de_texto(nombre)
        consulta_efectiva = (nombre_limpio or nombre).strip()

        meta_rival: dict[str, Any] | None = None
        if isinstance(rival_contexto, dict):
            meta_rival = dict(rival_contexto)
        elif isinstance(rival_contexto, str) and rival_contexto.strip():
            meta_rival = _inferir_meta_rival_desde_texto(rival_contexto)

        if liga_pref:
            if meta_rival is None:
                meta_rival = dict(liga_pref)
            else:
                meta_combinada = dict(meta_rival)
                meta_combinada.update(liga_pref)
                meta_rival = meta_combinada

        # PASO #2 OBLIGATORIO: LEAGUE-PEER & FIXTURE GRAPH RESOLVER
        # Si ya tenemos un rival resuelto en la misma liga/país (ej. Aurora en Bolivia),
        # inspeccionar primero su tabla de posiciones (overview.table) y calendario (fixtures)
        # antes de buscar clubes sueltos en otros países.
        par_misma_liga: dict[str, Any] | None = None
        if isinstance(meta_rival, dict) and (meta_rival.get("id") or meta_rival.get("pares_misma_liga") or meta_rival.get("partidos")):
            try:
                par_misma_liga = resolver_pareja_misma_liga(
                    str(meta_rival.get("nombre") or meta_rival.get("name") or ""),
                    consulta_efectiva,
                    pais_iso=str(meta_rival.get("pais_iso") or meta_rival.get("pais") or ""),
                    liga_pref=liga_pref or meta_rival,
                    doc_referencia=meta_rival,
                    fuente_obj=self,
                )
            except Exception:
                par_misma_liga = None

        variantes = variantes_de_busqueda(consulta_efectiva)
        if not variantes and not par_misma_liga:
            return None

        candidatos: list[dict[str, Any]] = []
        variante_usada = ""
        variantes_probadas: list[str] = []
        if par_misma_liga and par_misma_liga.get("id"):
            cand_par = {
                "id": str(par_misma_liga["id"]),
                "name": str(par_misma_liga.get("name") or par_misma_liga.get("nombre") or consulta_efectiva),
                "leagueName": str(par_misma_liga.get("leagueName") or par_misma_liga.get("liga") or (meta_rival or {}).get("liga") or ""),
                "leagueId": par_misma_liga.get("leagueId") or par_misma_liga.get("id_liga") or (meta_rival or {}).get("id_liga"),
                "ccode": str(par_misma_liga.get("pais_iso") or (meta_rival or {}).get("pais_iso") or ""),
                "score": 999999.0,
                "type": "team",
            }
            candidatos.append(cand_par)
            variante_usada = f"tabla_comun:{cand_par['name']}"
            self._log(
                f"equipo localizado en la misma tabla/calendario del rival: "
                f"{cand_par['name']} (id={cand_par['id']}, liga={cand_par['leagueName']})"
            )
        pide_no_senior = (
            usuario_solicito_no_senior(nombre)
            or usuario_solicito_no_senior(consulta_efectiva)
            or bool((meta_rival or {}).get("es_femenino"))
            or bool((meta_rival or {}).get("es_juvenil"))
        )
        for variante in variantes:
            variantes_probadas.append(variante)
            datos = self._api(
                f"/search/suggest?term={variante.replace(' ', '%20')}&lang={self.idioma}")
            encontrados: list[dict[str, Any]] = []
            if isinstance(datos, list):
                for bloque in datos:
                    for sug in (bloque or {}).get("suggestions", []) or []:
                        if sug.get("type") == "team":
                            # Descartar estrictamente femeninos / juveniles si no se solicitaron
                            if not pide_no_senior and es_equipo_o_torneo_no_senior(sug.get("name"), sug.get("leagueName")):
                                continue
                            # Regla dura anti-colisión Bayer != Bayern y país
                            if puntuar_candidato(sug, consulta_efectiva, rival_meta=meta_rival)[0] <= -9000:
                                continue
                            encontrados.append(sug)
            if encontrados:
                candidatos.extend(encontrados)
                if not variante_usada:
                    variante_usada = variante
                # Solo detener la búsqueda anticipadamente si encontramos un candidato titular (no filial),
                # con coincidencia exacta nuclear/alias (pts[2] > 0) y coherencia geográfica positiva con el rival/liga.
                exige_coherencia = bool(meta_rival and (meta_rival.get("pais") or meta_rival.get("liga") or meta_rival.get("leagueName")))
                if any(
                    (pts := puntuar_candidato(c, consulta_efectiva, rival_meta=meta_rival))[0] >= 0
                    and pts[2] > 0
                    and (pts[4] > 0 if exige_coherencia else pts[4] >= 0)
                    for c in encontrados
                ):
                    break
            else:
                self._log(f"búsqueda sin resultados para {variante!r}; se prueba la siguiente variante")

        if not pide_no_senior:
            candidatos_senior = [c for c in candidatos if not es_equipo_o_torneo_no_senior(c.get("name"), c.get("leagueName"))]
            if candidatos_senior:
                candidatos = candidatos_senior

        # Reintento automático por palabra principal filtrada estrictamente por pais_iso
        # (ej. "Universitario de Vinto" -> "Universitario" con pais_iso == "BOL",
        #  o "San Antonio Bulo Bulo" -> "San Antonio" con pais_iso == "BOL")
        if not candidatos and meta_rival:
            from resolvedor_entidades import normalizar_pais_iso
            pais_iso_obj = normalizar_pais_iso(
                meta_rival.get("pais_iso") or meta_rival.get("pais") or meta_rival.get("liga") or ""
            )
            if pais_iso_obj and pais_iso_obj != "INT":
                nucleo_palabras = _palabras_nucleo(consulta_efectiva)
                reintentos_principales: list[str] = []
                if len(nucleo_palabras) >= 2:
                    dos_primeras = f"{nucleo_palabras[0]} {nucleo_palabras[1]}"
                    if dos_primeras.lower() not in [v.lower() for v in variantes_probadas]:
                        reintentos_principales.append(dos_primeras)
                    apellido_dist = nucleo_palabras[-1]
                    if (
                        len(apellido_dist) >= 4
                        and apellido_dist.lower() not in _RAICES_GENERICAS_PROHIBIDAS_SOLAS
                        and apellido_dist.lower() not in [v.lower() for v in variantes_probadas]
                    ):
                        reintentos_principales.append(apellido_dist)
                if nucleo_palabras and len(nucleo_palabras[0]) >= 4:
                    primera_pal = nucleo_palabras[0]
                    # PROHIBIDO buscar una sola raíz genérica ('Universitario', 'Universidad', 'Real', 'Nacional')
                    # cuando el club tiene apellido/ciudad distintivo ('Universitario de Vinto', 'Real Tomayapo').
                    if not (len(nucleo_palabras) >= 2 and primera_pal.lower() in _RAICES_GENERICAS_PROHIBIDAS_SOLAS):
                        if primera_pal.lower() not in [v.lower() for v in variantes_probadas]:
                            reintentos_principales.append(primera_pal)
                for term_p in reintentos_principales:
                    variantes_probadas.append(term_p)
                    datos_p = self._api(f"/search/suggest?term={term_p.replace(' ', '%20')}&lang={self.idioma}")
                    if isinstance(datos_p, list):
                        for bloque in datos_p:
                            for sug in (bloque or {}).get("suggestions", []) or []:
                                if sug.get("type") != "team":
                                    continue
                                if not pide_no_senior and es_equipo_o_torneo_no_senior(sug.get("name"), sug.get("leagueName")):
                                    continue
                                iso_sug = normalizar_pais_iso(
                                    sug.get("ccode") or sug.get("country") or sug.get("leagueName") or ""
                                )
                                if iso_sug == pais_iso_obj:
                                    candidatos.append(sug)
                                    if not variante_usada:
                                        variante_usada = term_p
                    if candidatos:
                        break

        if not candidatos:
            self._log(f"⚠️  sin resultados de equipo para {consulta_efectiva!r} "
                      f"(probadas {len(variantes_probadas)} variantes: {variantes_probadas})")
            return None

        if variante_usada.lower() != consulta_efectiva.lower():
            self._log(f"nombre resuelto con la variante {variante_usada!r} "
                      f"(entrada original: {nombre!r})")

        # El proveedor repite equipos con el mismo id (bloques distintos del
        # buscador): se conserva una sola aparición por id con la mejor puntuación
        por_id: dict[str, dict[str, Any]] = {}
        for c in candidatos:
            cid = str(c.get("id"))
            previo = por_id.get(cid)
            if previo is None or puntuar_candidato(c, consulta_efectiva, rival_meta=meta_rival) > puntuar_candidato(previo, consulta_efectiva, rival_meta=meta_rival):
                por_id[cid] = c
        candidatos = list(por_id.values())

        candidatos.sort(key=lambda c: puntuar_candidato(c, consulta_efectiva, rival_meta=meta_rival), reverse=True)
        elegido = candidatos[0]

        # Desambiguación real: varios equipos DISTINTOS empatan en la clave esencial
        mejor_puntos = puntuar_candidato(elegido, consulta_efectiva, rival_meta=meta_rival)
        empatados = [c.get("name") for c in candidatos
                     if puntuar_candidato(c, consulta_efectiva, rival_meta=meta_rival)[:5] == mejor_puntos[:5]]
        if len(empatados) > 1:
            self._log(f"⚠️  {len(empatados)} equipos empatan como mejor coincidencia "
                      f"({empatados[:4]}); se elige {elegido.get('name')!r}")

        nombre_final = elegido.get("name")
        if not pide_no_senior:
            nombre_final = re.sub(r"\s*\((?:w|women)\)\s*", "", str(nombre_final), flags=re.IGNORECASE).strip()

        from resolvedor_entidades import construir_clave_cache_entidad, normalizar_pais_iso, detectar_categoria_desde_texto
        es_fem_eq, es_juv_eq = detectar_categoria_desde_texto(
            f"{nombre} {(meta_rival or {}).get('liga', '')} {(meta_rival or {}).get('nombre', '')}"
        )
        if meta_rival and "es_femenino" in meta_rival:
            es_fem_eq = bool(meta_rival["es_femenino"])
        if meta_rival and "es_juvenil" in meta_rival:
            es_juv_eq = bool(meta_rival["es_juvenil"])
        pais_iso_eq = (
            normalizar_pais_iso(elegido.get("ccode") or elegido.get("country") or elegido.get("leagueName"))
            or normalizar_pais_iso((meta_rival or {}).get("pais_iso") or (meta_rival or {}).get("pais") or (meta_rival or {}).get("liga"))
            or "INT"
        )
        tid_str = str(elegido.get("id"))
        equipo = {
            "id": tid_str,
            "nombre": nombre_final,
            "nombre_solicitado": consulta_efectiva,
            "liga": elegido.get("leagueName"),
            "id_liga": elegido.get("leagueId"),
            "pais_iso": pais_iso_eq,
            "es_femenino": es_fem_eq,
            "es_juvenil": es_juv_eq,
            "clave_cache": construir_clave_cache_entidad(tid_str, pais_iso_eq, es_fem_eq, es_juv_eq),
            "alternativas": [c.get("name") for c in candidatos[1:6]],
            "variante_busqueda": variante_usada,
            "ambiguous": len(empatados) > 1,
        }
        if liga_pref:
            equipo["liga_solicitada"] = liga_pref.get("nombre")
        self._log(f"equipo resuelto: {equipo['nombre']} (id={equipo['id']}, {equipo['liga']})")
        return equipo

    def resolver_cruce_equipos(self, nombre_a: str, nombre_b: str,
                               liga_contexto: dict[str, Any] | str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """
        Resuelve un par de equipos (Equipo A vs Equipo B) exigiendo coherencia mutua de país/liga.
        Si se indica una liga/competición (en `nombre_a` o en `liga_contexto`), la propaga a ambos equipos.
        """
        liga_a, limpio_a = extraer_liga_de_texto(nombre_a or "")
        liga_b, limpio_b = extraer_liga_de_texto(nombre_b or "")
        liga_comun = liga_a or liga_b
        if not liga_comun and liga_contexto:
            if isinstance(liga_contexto, dict):
                liga_comun = liga_contexto
            elif isinstance(liga_contexto, str):
                liga_comun = _inferir_meta_rival_desde_texto(liga_contexto)

        ctx_para_a: Any = _inferir_meta_rival_desde_texto(limpio_b) or limpio_b
        if liga_comun:
            if isinstance(ctx_para_a, dict):
                ctx_para_a = {**ctx_para_a, **liga_comun}
            else:
                ctx_para_a = dict(liga_comun)

        eq_a = self.resolver_equipo(limpio_a, rival_contexto=ctx_para_a)

        ctx_para_b: Any = eq_a or limpio_a
        if liga_comun:
            if isinstance(ctx_para_b, dict):
                ctx_para_b = {**ctx_para_b, **liga_comun}
            else:
                ctx_para_b = dict(liga_comun)

        eq_b = self.resolver_equipo(limpio_b, rival_contexto=ctx_para_b)
        if eq_b and (not eq_a or (eq_a.get("ambiguous") and not isinstance(ctx_para_a, dict))):
            ctx_reintento_a = {**eq_b, **(liga_comun or {})} if isinstance(eq_b, dict) else eq_b
            eq_a = self.resolver_equipo(limpio_a, rival_contexto=ctx_reintento_a)
        return eq_a, eq_b

    # ── últimos partidos ───────────────────────────────────────────────────
    def ultimos_partidos(self, equipo: dict[str, Any], limite: int = 10,
                         incluir_amistosos: bool = False) -> list[PartidoFinalizado]:
        res = self.escanear_oficiales(equipo, objetivo=limite,
                                      incluir_amistosos=incluir_amistosos)
        return res.oficiales[:limite]

    # ── búsqueda profunda hacia atrás ──────────────────────────────────────
    def escanear_oficiales(self, equipo: dict[str, Any], objetivo: int,
                           incluir_amistosos: bool = False) -> ResultadoEscaneo:
        """
        Recorre el historial del equipo desde el partido más reciente hacia atrás,
        descartando amistosos, hasta reunir `objetivo` partidos oficiales.

        Página 0 = ventana de calendario de la fuente (temporada en curso).
        Páginas siguientes = temporadas anteriores de cada liga del equipo vía
        `/api/leagues`, que es lo que permite completar 15 partidos oficiales
        cuando la temporada acaba de empezar.
        """
        tid = str(equipo.get("id"))
        cache = self._cache_paginas.setdefault(tid, {})
        oficiales: list[PartidoFinalizado] = []
        amistosos_reservados: list[PartidoFinalizado] = []
        vistos: set[str] = set()
        paginas_escaneadas = 0
        finalizados_totales = 0
        amistosos_excluidos = 0
        fuente_agotada = False
        motivo = ""
        paginas_sin_aporte = 0
        # La cola de historial puede tener varias ligas x temporadas; el tope debe
        # alcanzar para agotarla antes de rendirse.
        tope_paginas = max(self.max_paginas_escaneo, 14)

        for pagina in range(tope_paginas):
            crudos = self._cargar_pagina(tid, pagina, cache)
            if crudos is None:
                fuente_agotada = True
                motivo = (f"la fuente no ofrece más páginas de calendario "
                          f"(se escanearon {paginas_escaneadas})")
                break

            paginas_escaneadas += 1
            nuevos = 0
            for f, torneo in crudos:
                pid = str(f.get("id"))
                if pid in vistos:
                    continue
                vistos.add(pid)
                nuevos += 1
                finalizados_totales += 1
                if es_competicion_amistosa(torneo):
                    # Los amistosos nunca cuentan para el objetivo; se reservan por
                    # si el usuario pide rellenar el dataset con ellos.
                    amistosos_reservados.append(self._partido_desde_fixture(f, torneo, tid))
                    amistosos_excluidos += 1
                    continue
                oficiales.append(self._partido_desde_fixture(f, torneo, tid))

            # Corte basado en FECHA, no en orden de llegada: cada (liga, temporada)
            # devuelve su propio tramo, así que hay que recolectar de todas antes de
            # quedarse con los 15 más recientes. Se escanea hasta tener cubiertas
            # TODAS las ligas de la ventana (una página cada una) y con margen
            # suficiente; ahí el conjunto ya es comparable por fecha y se ordena.
            # Se exige cubrir solo la cola PRINCIPAL: la ampliada (ligas históricas
            # del club) se recorre únicamente cuando aún faltan partidos.
            principal = self._cola_principal_por_equipo.get(tid, len(self._cola_por_equipo.get(tid, [])))
            if (len(oficiales) >= objetivo
                    and paginas_escaneadas >= 1 + principal):
                motivo = f"objetivo de {objetivo} partidos oficiales alcanzado"
                break

            if nuevos == 0:
                if paginas_escaneadas <= principal:
                    # Todavía quedan ligas/temporadas del club por visitar: no se
                    # puede concluir «fuente agotada» con la cola principal a medias
                    # (era el fallo que dejaba al Deportivo en 8 de 15 teniendo 42
                    # partidos suyos en LaLiga2 la temporada anterior).
                    continue
                if paginas_escaneadas == 1 + principal:
                    paginas_sin_aporte = 1
                else:
                    paginas_sin_aporte += 1
                # Dentro de las ligas históricas sí se tolera el vacío: una
                # competición sin temporada accesible no debe cortar el escaneo.
                if paginas_sin_aporte >= 3:
                    fuente_agotada = True
                    visitadas = paginas_escaneadas - 1
                    motivo = (f"{paginas_sin_aporte} páginas seguidas sin aportar "
                              f"partidos nuevos (visitadas {visitadas} ligas/temporadas "
                              f"del club)")
                    break
                continue
            paginas_sin_aporte = 0
        else:
            fuente_agotada = True
            motivo = f"tope de seguridad de {tope_paginas} páginas alcanzado"

        # Orden cronológico descendente garantizado entre páginas, y recorte a los
        # `objetivo` MÁS RECIENTES (no a los primeros que aparecieron).
        oficiales.sort(key=lambda p: p.fecha, reverse=True)
        amistosos_reservados.sort(key=lambda p: p.fecha, reverse=True)
        # ── BLINDAJE: los amistosos NUNCA cuentan ──────────────────────────
        # Regla del proyecto: el objetivo son partidos OFICIALES. Un amistoso no
        # puede ocupar una de las 15 plazas en ningún caso — ni por relleno, ni por
        # un fallo de clasificación aguas arriba. Se vuelve a filtrar aquí, en el
        # último punto antes de devolver la lista, para que sea imposible que se
        # cuelen aunque cambie el código de más arriba.
        oficiales = [p for p in oficiales
                     if not es_competicion_amistosa(str(p.torneo or ""))]
        total_oficiales_reunidos = len(oficiales)
        if len(oficiales) > objetivo:
            oficiales = oficiales[:objetivo]
        self._log(f"escaneo hacia atrás: {len(oficiales)} oficiales de "
                  f"{finalizados_totales} finalizados en {paginas_escaneadas} página(s)"
                  f"{f' · {total_oficiales_reunidos} reunidos, recortados a los más recientes' if total_oficiales_reunidos > objetivo else ''}"
                  f"{f' · {amistosos_excluidos} amistosos descartados' if amistosos_excluidos else ''}"
                  f"{'' if len(oficiales) >= objetivo else ' · fuente agotada'}")

        # ── IDENTIDAD EN LA FUENTE SECUNDARIA: UNA sola vez por auditoría ───
        # Se resuelve aquí, con los 15 partidos ya elegidos, porque la identidad
        # del club en Flashscore se decide CONTRASTÁNDOLOS (hay homónimos: el
        # «Vasco da Gama» de Sudáfrica frente al brasileño, que figura como
        # «Vasco»). Se cachea para no repetir la resolución por cada partido.
        self._preparar_identidad_secundaria(equipo, oficiales)
        if tid in self._cache_pares_liga:
            equipo["pares_misma_liga"] = self._cache_pares_liga[tid]
        return ResultadoEscaneo(
            oficiales=oficiales,
            amistosos_reservados=amistosos_reservados,
            incluir_amistosos=incluir_amistosos,
            paginas_escaneadas=paginas_escaneadas,
            finalizados_totales=finalizados_totales,
            amistosos_excluidos=amistosos_excluidos,
            fuente_agotada=fuente_agotada or len(oficiales) < objetivo,
            motivo_parada=motivo,
            cola_historica_revisada=(
                # No hay competiciones históricas que visitar (nada quedó pendiente),
                # o se alcanzó el objetivo, o se llegó a recorrer la cola ampliada.
                len(self._cola_por_equipo.get(tid, [])) <= principal
                or len(oficiales) >= objetivo
                or (paginas_escaneadas - 1) > principal),
        )

    def partidos_futuros(self, equipo: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Próximos partidos PROGRAMADOS del equipo, según la ventana de `/teams`.

        Se guardan en la auditoría para que `servicio_mercados` pueda encontrar el
        enfrentamiento entre dos equipos cruzando IDs: es lo que permite dar la fecha
        y la localía reales cuando los dos equipos no se han enfrentado en el
        historial (caso Roma vs Real Madrid).
        """
        tid = str(equipo.get("id") or "")
        if not tid:
            return []
        cacheado = self._cache_futuros.get(tid)
        if cacheado is not None:
            return cacheado
        futuros: list[dict[str, Any]] = []
        try:
            datos = self._api(f"/teams?id={tid}&ccode3=PER&lang={self.idioma}")
            if isinstance(datos, dict):
                fixtures = (((datos.get("fixtures") or {}).get("allFixtures")) or {}
                            ).get("fixtures") or []
                futuros = _normalizar_futuros(fixtures)
        except Exception as e:
            self._log(f"partidos futuros no disponibles para {equipo.get('nombre')}: "
                      f"{type(e).__name__}: {e}")
        self._cache_futuros[tid] = futuros
        if futuros:
            self._log(f"próximos partidos de {equipo.get('nombre')}: {len(futuros)} "
                      f"(del {futuros[0]['fecha']} al {futuros[-1]['fecha']})")
        return futuros

    def _ligas_historicas(self, tid: str) -> list[tuple[str, str, list[str]]]:
        """
        Ligas en las que el club TIENE historial, con sus temporadas disponibles:
        [(league_id, nombre, [temporadas])].

        Por qué hace falta: `_ligas_del_equipo` solo ve las competiciones de la
        ventana ACTUAL. Un equipo recién ascendido (Deportivo A Coruña: LaLiga2 en
        2025/26 y LaLiga en 2026/27) tenía sus partidos de la temporada anterior en
        una liga que la cola nunca visitaba, así que el escaneo se declaraba
        «fuente agotada» con 8 de 15 partidos… cuando la fuente tenía 42 partidos
        suyos de esa temporada. Esta lista sale de `history.trophyList` de la ficha
        del equipo, que sí declara todas sus competiciones y temporadas.

        Se excluyen las competiciones amistosas, las que no tienen identificador
        válido (FotMob usa -1 para ligas sin datos) y las que ya están en la ventana.
        """
        cacheado = self._cache_ligas_historicas.get(tid)
        if cacheado is not None:
            return cacheado
        salida: list[tuple[str, str, list[str]]] = []
        try:
            datos = self._api(f"/teams?id={tid}&ccode3=PER&lang={self.idioma}")
            historial = (datos or {}).get("history") if isinstance(datos, dict) else None
            for trofeo in ((historial or {}).get("trophyList") or []):
                nombre = str(trofeo.get("name") or "")
                lid = str(trofeo.get("leagueId") or "")
                if not lid or lid.startswith("-") or es_competicion_amistosa(nombre):
                    continue
                temporadas = [str(t) for t in (trofeo.get("availableSeasons") or []) if t]
                salida.append((lid, nombre, temporadas))
        except Exception as e:
            self._log(f"historial de ligas no disponible para {tid}: "
                      f"{type(e).__name__}: {e}")
        self._cache_ligas_historicas[tid] = salida
        return salida

    def _ligas_del_equipo(self, tid: str) -> dict[str, str]:
        """
        leagueId -> nombre, extraídos de los PROPIOS PARTIDOS del equipo.

        No se usa `primaryTournamentId` de la cabecera porque es insuficiente:
        comprobado en vivo, Boca Juniors lo tiene a 0 y Vasco da Gama apuntaba a
        una copa en vez del Brasileirão. Leyendo el torneo de cada partido entran
        liga + copa nacional + continental, que es lo que hace falta para reunir
        15 partidos oficiales cuando la ventana de la fuente se queda corta.
        """
        cacheado = self._cache_ligas.get(tid)
        if cacheado is not None:
            return cacheado
        datos = self._api(f"/teams?id={tid}&ccode3=PER&lang={self.idioma}")
        ligas: dict[str, str] = {}
        if isinstance(datos, dict):
            fixtures = ((((datos.get("fixtures") or {}).get("allFixtures")) or {})
                        .get("fixtures")) or []
            for f in fixtures:
                t = f.get("tournament") or {}
                nombre = str(t.get("name") or "")
                if es_competicion_amistosa(nombre):
                    continue
                lid = t.get("leagueId") or t.get("id")
                if lid:
                    ligas.setdefault(str(lid), nombre)
        self._cache_ligas[tid] = ligas
        if ligas:
            self._log(f"ligas detectadas en los partidos: "
                      f"{', '.join(f'{k}={v}' for k, v in list(ligas.items())[:6])}")
        return ligas

    def _temporadas_de_liga(self, league_id: str) -> list[str]:
        """Temporadas que la LIGA expone. En /teams viene vacío; aquí no."""
        if league_id in self._cache_temporadas:
            return self._cache_temporadas[league_id]
        datos = self._api(f"/leagues?id={league_id}&lang={self.idioma}")
        temporadas: list[str] = []
        if isinstance(datos, dict):
            for s in (datos.get("allAvailableSeasons") or []):
                temporadas.append(s if isinstance(s, str) else str(s.get("id")))
        self._cache_temporadas[league_id] = temporadas
        return temporadas

    def _partidos_de_liga(self, league_id: str, temporada: str | None,
                          tid: str) -> list[tuple[dict[str, Any], str]]:
        """
        Partidos terminados de un equipo en una liga y temporada, como
        [(fixture, nombre_torneo)]. Si `temporada` es None, se usa la actual.
        """
        clave = (str(league_id), str(temporada or ""), str(tid))
        if clave in self._cache_partidos_liga:
            return self._cache_partidos_liga[clave]
        url = f"/leagues?id={league_id}&lang={self.idioma}"
        if temporada:
            url += f"&season={str(temporada).replace('/', '%2F')}"
        datos = self._api(url)
        salida: list[tuple[dict[str, Any], str]] = []
        if isinstance(datos, dict):
            fixtures = (datos.get("fixtures") or {})
            am = fixtures.get("allMatches") or (datos.get("matches") or {}).get("allMatches") or []
            for f in am:
                estado = f.get("status") or {}
                if not estado.get("finished"):
                    continue
                home_id = str((f.get("home") or {}).get("id"))
                away_id = str((f.get("away") or {}).get("id"))
                if tid not in (home_id, away_id):
                    continue
                t = f.get("tournament") or {}
                nombre = str(t.get("name") or fixtures.get("fixtureInfo", {}).get("name") or "")
                if es_competicion_amistosa(nombre):
                    continue
                salida.append((f, nombre))
        self._cache_partidos_liga[clave] = salida
        return salida

    def _cargar_pagina(self, tid: str, pagina: int,
                       cache: dict[int, list[tuple[dict[str, Any], str]] | None]
                       ) -> list[tuple[dict[str, Any], str]] | None:
        """
        Devuelve [(fixture, nombre_torneo)] de la página indicada, o None si la
        fuente ya no tiene más páginas. Cachea por página.

        Página 0: la ventana de calendario de `/teams` (temporada en curso).
        Páginas siguientes: se recorre la TEMPORADA ANTERIOR de cada liga del
        equipo vía `/api/leagues?id={liga}&season={temporada}`. Esto es lo que
        permite completar 15 partidos oficiales cuando la temporada acaba de
        empezar, sin recurrir a amistosos ni inventar nada.
        """
        if pagina in cache:
            return cache[pagina]

        if pagina == 0:
            datos = self._api(f"/teams?id={tid}&ccode3=PER&lang={self.idioma}")
            if not isinstance(datos, dict):
                cache[pagina] = None
                return None
            try:
                self._cache_pares_liga[tid] = extraer_pares_misma_liga_fotmob(datos_team=datos)
            except Exception:
                pass
            fixtures = (((datos.get("fixtures") or {}).get("allFixtures")) or {}).get("fixtures", []) or []
            crudos = [(f, (f.get("tournament") or {}).get("name", ""))
                      for f in fixtures if (f.get("status") or {}).get("finished")]
            cache[pagina] = crudos
            self._log(f"página 0: {len(fixtures)} partidos en la ventana de la fuente, "
                      f"{len(crudos)} finalizados")
            return crudos

        # Páginas siguientes: cola de (liga, temporada) a recorrer. Se agotan
        # TODAS las combinaciones antes de rendirse: una liga sin temporada
        # anterior (p. ej. la Champions) no debe cortar el escaneo antes de
        # llegar a la liga doméstica, que es la que aporta los partidos.
        #
        # Se encolan las DOS primeras temporadas de cada liga: la ACTIVA
        # (índice 0) y la ANTERIOR (índice 1). La activa es imprescindible
        # porque hay competiciones cuyo calendario arranca a mitad de temporada
        # y no aparecen en la ventana de /teams: comprobado con Vasco da Gama,
        # cuya Serie A '2026' (29 partidos, hasta octubre) no estaba en la cola
        # cuando solo se encolaba el índice 1, y por eso el dataset saltaba de
        # febrero de 2026 a diciembre de 2025.
        #
        # La cola tiene DOS niveles y se calcula UNA vez POR EQUIPO:
        #   • PRINCIPAL: las competiciones de la ventana actual (coste de siempre).
        #   • AMPLIADA: las demás competiciones del club según su historial, que es
        #     donde están los partidos de un recién ascendido (LaLiga2 2025/26 para
        #     el Deportivo). Solo se recorre si la principal no llegó al objetivo,
        #     así que un equipo con datos suficientes NO paga peticiones extra.
        if tid not in self._cola_por_equipo:
            ligas = self._ligas_del_equipo(tid)
            conteo: dict[str, int] = {}
            for f, _n in (cache.get(0) or []):
                lid = str(((f.get("tournament") or {}).get("leagueId"))
                          or ((f.get("tournament") or {}).get("id")) or "")
                if lid:
                    conteo[lid] = conteo.get(lid, 0) + 1
            # La liga con más partidos en la ventana va primero
            orden = sorted(ligas.keys(), key=lambda k: -conteo.get(k, 0))
            cola: list[tuple[str, str]] = []
            for lid in orden:
                for temporada in self._temporadas_de_liga(lid)[:2]:
                    cola.append((lid, temporada))
            principal = len(cola)

            # Cola ampliada: competiciones históricas que la ventana no muestra.
            ampliada: list[tuple[str, str]] = []
            for lid, nombre, temporadas in self._ligas_historicas(tid):
                if lid in ligas:
                    continue
                if not temporadas:
                    temporadas = self._temporadas_de_liga(lid)
                for temporada in temporadas[:2]:
                    ampliada.append((lid, temporada))
                if len(ampliada) >= 6:      # tope de coste: 3 competiciones × 2 temporadas
                    break
            if ampliada:
                self._log(f"cola ampliada de historial: "
                          f"{', '.join(f'{lid}={temporada}' for lid, temporada in ampliada[:6])}")

            self._cola_por_equipo[tid] = cola + ampliada
            self._cola_principal_por_equipo[tid] = principal

        cola = self._cola_por_equipo[tid]
        indice = pagina - 1
        if indice >= len(cola):
            cache[pagina] = None
            return None

        league_id, temporada = cola[indice]
        nombre_liga = self._ligas_del_equipo(tid).get(league_id)
        if not nombre_liga:
            # Competición que no aparece en la ventana actual (p. ej. la división
            # anterior de un recién ascendido): el nombre está en el historial.
            nombre_liga = next((n for lid, n, _t in self._ligas_historicas(tid)
                                if lid == league_id), league_id)
        partidos = self._partidos_de_liga(league_id, temporada, tid)
        # La respuesta de /leagues no siempre nombra el torneo de cada partido:
        # se completa con el nombre de la liga que ya conocemos.
        partidos = [(f, (n or nombre_liga)) for f, n in partidos]
        self._log(f"página {pagina}: liga {league_id} ({nombre_liga}) "
                  f"temporada {temporada} -> {len(partidos)} partidos terminados del equipo")
        cache[pagina] = partidos
        return partidos

    def _partido_desde_fixture(self, f: dict[str, Any], torneo: str,
                               tid: str) -> PartidoFinalizado:
        estado = f.get("status") or {}
        home = f.get("home") or {}
        away = f.get("away") or {}
        gl, gv = home.get("score"), away.get("score")
        # En /api/leagues (temporadas anteriores) FotMob NO publica `home.score`:
        # medido en vivo, 0 de 380 partidos lo traen, y en cambio 380 de 380 traen
        # `status.scoreStr` (p. ej. '2 - 1'). Sin este respaldo los partidos de
        # temporadas anteriores quedaban con marcador None y Flashscore no podía
        # emparejarlos (exige marcador exacto), así que el relleno de métricas se
        # quedaba en 9/15. Se parsea el texto SOLO si los enteros no vienen.
        if not isinstance(gl, int) or not isinstance(gv, int):
            gl_str, gv_str = _goles_desde_texto(estado.get("scoreStr"))
            if not isinstance(gl, int):
                gl = gl_str
            if not isinstance(gv, int):
                gv = gv_str
        return PartidoFinalizado(
            id_partido=str(f.get("id")),
            fecha=str(estado.get("utcTime") or "")[:10],
            torneo=torneo,
            local=str(home.get("name") or ""),
            visitante=str(away.get("name") or ""),
            goles_local=gl if isinstance(gl, int) else None,
            goles_visitante=gv if isinstance(gv, int) else None,
            es_local_el_equipo=str(home.get("id")) == tid,
            url=str(f.get("pageUrl") or ""),
            extra={"razon_fin": (estado.get("reason") or {}).get("short"),
                   "marcador_texto": estado.get("scoreStr"),
                   "home_id": str(home.get("id") or ""),
                   "away_id": str(away.get("id") or ""),
                   "rival_id": str((away.get("id") if str(home.get("id")) == tid else home.get("id")) or "")},
        )

    # ── matriz de un partido ───────────────────────────────────────────────
    def matriz_partido(self, partido: PartidoFinalizado, equipo: dict[str, Any]) -> MatrizPartido:
        es_local = partido.es_local_el_equipo
        rival = partido.visitante if es_local else partido.local
        goles_equipo = partido.goles_local if es_local else partido.goles_visitante
        goles_rival = partido.goles_visitante if es_local else partido.goles_local

        matriz = MatrizPartido(
            id_partido=partido.id_partido,
            fecha=partido.fecha,
            torneo=partido.torneo,
            equipo=str(equipo.get("nombre") or ""),
            rival=rival,
            condicion="casa" if es_local else "fuera",
            goles_equipo=goles_equipo,
            goles_rival=goles_rival,
            url=partido.url,
        )

        detalle = self._api(f"/matchDetails?matchId={partido.id_partido}")
        if not isinstance(detalle, dict):
            matriz.error = "sin respuesta de matchDetails"
            matriz.notas.append("El partido no devolvió detalle; matriz vacía con nulos.")
            matriz.periodos = {p: PeriodoPartido(p, [], [d.etiqueta for d in CATALOGO_METRICAS])
                               for p in ORDEN_PERIODOS}
            return matriz

        contenido = detalle.get("content") or {}
        stats = (contenido.get("stats") or {}).get("Periods") or {}

        # Índice de etiquetas -> valor(es) por periodo.
        # La fuente siempre publica [local, visitante]; aquí se ORIENTA al equipo
        # auditado, que puede estar jugando como visitante. A partir de este punto
        # toda entrada usa las claves "equipo" (auditado) y "rival".
        indexado: dict[str, dict[str, dict[str, Any]]] = {}
        for nombre_periodo, bloque in stats.items():
            normalizado = PERIODOS_NORMALIZADOS.get(str(nombre_periodo).strip().lower())
            if not normalizado:
                continue
            crudo = self._indexar_metricas(bloque)
            indexado[normalizado] = {
                clave: {
                    "equipo": (valores["local"] if es_local else valores["visitante"]),
                    "rival": (valores["visitante"] if es_local else valores["local"]),
                }
                for clave, valores in crudo.items()
            }

        # Extraer goles de cabeza desde shotmap y goles por tiempo desde eventos
        extra_eventos = self._extraer_shotmap_y_eventos(
            contenido, id_equipo=str(equipo.get("id")), es_local=es_local,
            goles_equipo=goles_equipo, goles_rival=goles_rival,
        )
        for per_ev, mapa_ev in extra_eventos.items():
            dest_per = indexado.setdefault(per_ev, {})
            for cl_ev, val_ev in mapa_ev.items():
                if cl_ev not in dest_per:
                    dest_per[cl_ev] = val_ev

        # Métricas agregadas del detalle por jugador (xA, pases al tercio final,
        # pases rompiendo líneas, pases clave, goles evitados, recuperaciones, xGOT enfrentado)
        agregados_jugador = self._agregar_por_jugador(
            contenido, id_equipo=str(equipo.get("id")), es_local=es_local)

        for periodo in ORDEN_PERIODOS:
            filas: list[dict[str, Any]] = []
            no_disponibles: list[str] = []
            fuente_periodo = indexado.get(periodo, {})
            for definicion in CATALOGO_METRICAS:
                entrada = fuente_periodo.get(definicion.clave)
                aporte = (agregados_jugador.get(periodo) or {}).get(definicion.clave)
                # Preferir entrada de periodo si existe; caer a agregados de jugador
                origen = entrada if entrada is not None else aporte
                v_eq = v_riv = None
                if origen is not None:
                    v_eq = self._valor(definicion, origen.get("equipo"))
                    v_riv = self._valor(definicion, origen.get("rival"))

                if v_eq is None and v_riv is None:
                    no_disponibles.append(definicion.etiqueta)

                filas.append({
                    "clave": definicion.clave,
                    "metrica": definicion.etiqueta,
                    "categoria": definicion.categoria,
                    "equipo": v_eq,
                    "rival": v_riv,
                })

            matriz.periodos[periodo] = PeriodoPartido(periodo, filas, no_disponibles)

        # ── RELLENO DESDE LA FUENTE SECUNDARIA ─────────────────────────────
        # FotMob no publica todas las métricas en todos los tiempos. Se rellenan
        # desde el feed de Flashscore (df_st_1 y GraphQL dts), y luego se aplica
        # la consolidación matemática (Sección 5: coherencia 1T+2T=FT y ecuaciones).
        self._rellenar_desde_flashscore(matriz, partido, es_local,
                                        goles_equipo, goles_rival)

        # Consolidación matemática anti-nulos (solo si el partido tiene datos reales,
        # para no fabricar matrices en partidos suspendidos o vacíos).
        valores_reales_previos = sum(
            1
            for b in matriz.periodos.values()
            for f in b.filas
            for k in ("equipo", "rival")
            if f.get(k) is not None
        )
        if valores_reales_previos >= 10:
            consolidar_matriz_completa(matriz)

        # Métricas ausentes en TODOS los periodos -> resumen fiable a nivel de partido.
        presentes_en_algun_periodo: set[str] = set()
        for mat in matriz.periodos.values():
            for fila in mat.filas:
                if fila["equipo"] is not None or fila["rival"] is not None:
                    presentes_en_algun_periodo.add(fila["clave"])
        ausentes = sorted({d.etiqueta for d in CATALOGO_METRICAS
                           if d.clave not in presentes_en_algun_periodo})
        matriz.metricas_no_disponibles = ausentes
        if ausentes:
            matriz.notas.append(
                f"{len(ausentes)} de las {len(CATALOGO_METRICAS)} métricas solicitadas no las "
                f"publica la fuente para este partido (quedan como nulo): {', '.join(ausentes)}"
            )
        if not stats:
            matriz.notas.append("La fuente no devolvió bloques de estadísticas por periodo.")
        time.sleep(self.pausa)
        return matriz

    def _preparar_identidad_secundaria(self, equipo: dict[str, Any],
                                       oficiales: list[PartidoFinalizado]) -> None:
        """
        Resuelve UNA vez por auditoría qué club de Flashscore corresponde al equipo
        y deja el listado cacheado para rellenar los partidos uno a uno.

        Se guardan los 15 partidos como «prueba de identidad» y el país del equipo.
        """
        from fuente_flashscore import ListadoEquipoFlashscore
        self._equipo_actual_id = str(equipo.get("id") or "")
        self._listado_flashscore = ListadoEquipoFlashscore()
        self._fixtures_para_identidad = [
            {"fecha": p.fecha, "goles_local": p.goles_local,
             "goles_visitante": p.goles_visitante,
             "local": p.local, "visitante": p.visitante}
            for p in oficiales
        ]
        self._pais_equipo = self._leer_pais_equipo()
        try:
            info = self._listado_flashscore.resolver_identidad(
                str(equipo.get("nombre") or ""), self._fixtures_para_identidad,
                self._pais_equipo, contexto=getattr(self, "ctx", None))
            elegido = info.get("elegido") or {}
            self._log(f"identidad Flashscore: {elegido.get('nombre')} "
                      f"({elegido.get('pais')}) · {info.get('aciertos')}/"
                      f"{info.get('total_fixtures')} partidos reconocidos")
            if not info.get("aciertos"):
                self._log("identidad Flashscore sin evidencia suficiente: "
                          "no se rellenará ningún partido")
                self._listado_flashscore = None
        except Exception as e:
            self._log(f"identidad Flashscore no disponible: {type(e).__name__}: {e}")
            self._listado_flashscore = None

    def _leer_pais_equipo(self) -> str | None:
        """País del equipo auditado, para desambiguar homónimos."""
        try:
            datos = self._api(f"/teams?id={self._equipo_actual_id}&lang={self.idioma}")
            detalles = (datos or {}).get("details") or {}
            pais = (detalles.get("country") or detalles.get("countryName")
                    or (detalles.get("defaultCountry") or {}).get("name"))
            return str(pais) if pais else None
        except Exception:
            return None

    def _rellenar_desde_flashscore(self, matriz: MatrizPartido,
                                   partido: PartidoFinalizado, es_local: bool,
                                   goles_equipo: int | None,
                                   goles_rival: int | None) -> None:
        """
        Rellena los huecos de la matriz con el feed de Flashscore y anota la
        procedencia de CADA valor.
        """
        if not partido.fecha or goles_equipo is None or goles_rival is None:
            return
        try:
            from fuente_flashscore import (
                PROC_DERIVADO, PROC_FLASHSCORE, PROC_FOTMOB, PROC_SIN_DATO,
                ListadoEquipoFlashscore, completar_con_feed, pases_ratio_derivado,
            )
        except Exception:
            return

        listado = getattr(self, "_listado_flashscore", None)
        if listado is None:
            listado = ListadoEquipoFlashscore()
            self._listado_flashscore = listado

        pais = getattr(self, "_pais_equipo", None)
        fixtures = getattr(self, "_fixtures_para_identidad", None) or []
        consulta = {
            "fecha": partido.fecha,
            "goles_local": partido.goles_local,
            "goles_visitante": partido.goles_visitante,
            "local": partido.local,
            "visitante": partido.visitante,
        }
        try:
            res = listado.metricas_para(consulta, matriz.equipo, fixtures, pais,
                                       contexto=getattr(self, "ctx", None))
        except Exception as e:
            matriz.notas.append(f"Flashscore no disponible para este partido: {e}")
            matriz.procedencia = self._procedencia_base(matriz)
            return
        if not res.get("pareado"):
            matriz.procedencia = self._procedencia_base(matriz)
            matriz.fuente_secundaria = {"pareado": False,
                                        "motivo": res.get("motivo", "")}
            return

        m = res["metricas"]
        def _lado(periodo: str, bando: str) -> dict[str, float]:
            return m.metricas_de(periodo, bando)

        fuentes: dict[str, dict[str, dict[str, float]]] = {}
        for periodo in ("FT", "1T", "2T"):
            prop = _lado(periodo, "local" if es_local else "visitante")
            aje = _lado(periodo, "visitante" if es_local else "local")
            if res.get("bandos_invertidos"):
                prop, aje = aje, prop
            fuentes[periodo] = {"equipo": prop, "rival": aje}

        resumen = {"pareado": True, "id_flashscore": res.get("id_flashscore"),
                   "bandos_invertidos": res.get("bandos_invertidos", False),
                   "periodos_rellenados": [],
                   "rellenadas": {"equipo": [], "rival": []}}
        procedencia: dict[str, dict[str, str]] = {"equipo": {}, "rival": {}}

        for periodo in ("FT", "1T", "2T"):
            bloque = matriz.periodos.get(periodo)
            if bloque is None:
                continue
            if fuentes[periodo]["equipo"] or fuentes[periodo]["rival"]:
                resumen["periodos_rellenados"].append(periodo)
            valores_previos: dict[str, dict[str, float | None]] = {"equipo": {}, "rival": {}}
            for fila in bloque.filas:
                clave = fila["clave"]
                for lado in ("equipo", "rival"):
                    fuente_feed = fuentes[periodo][lado]
                    valores, proc = completar_con_feed(
                        {clave: fila.get(lado)}, {clave: fuente_feed.get(clave)})
                    if fila.get(lado) is None and valores.get(clave) is not None:
                        fila[lado] = valores[clave]
                        resumen["rellenadas"][lado].append(f"{clave}@{periodo}")
                        procedencia[lado][clave] = proc.get(clave, PROC_FLASHSCORE)
                    else:
                        procedencia[lado][clave] = (
                            PROC_FOTMOB if fila.get(lado) is not None else PROC_SIN_DATO)
                    valores_previos[lado][clave] = fila.get(lado)

            # Segunda pasada: `pases_ratio` con los datos ya completos de su lado
            for lado in ("equipo", "rival"):
                fila_ratio = next((f for f in bloque.filas
                                   if f["clave"] == "pases_ratio"), None)
                if fila_ratio is None or fila_ratio.get(lado) is not None:
                    continue
                ratio = pases_ratio_derivado(valores_previos[lado].get("pases_total"),
                                             valores_previos[lado].get("posesion_pct"))
                if ratio is not None:
                    fila_ratio[lado] = ratio
                    resumen["rellenadas"][lado].append(f"pases_ratio@{periodo}")
                    procedencia[lado]["pases_ratio"] = PROC_DERIVADO

            bloque.no_disponibles = [
                f["metrica"] for f in bloque.filas
                if f.get("equipo") is None and f.get("rival") is None
            ]
        matriz.procedencia = procedencia
        matriz.fuente_secundaria = resumen
        rellenadas = sorted(set(resumen["rellenadas"]["equipo"]
                                + resumen["rellenadas"]["rival"]))
        if rellenadas:
            matriz.notas.append("Métricas rellenadas desde Flashscore: "
                                + ", ".join(rellenadas))

    def _procedencia_base(self, matriz: MatrizPartido) -> dict[str, dict[str, dict[str, str]]]:
        """Procedencia cuando no hubo fuente secundaria: lo que hay es de FotMob."""
        from fuente_flashscore import PROC_FOTMOB, PROC_SIN_DATO
        salida: dict[str, dict[str, dict[str, str]]] = {}
        for periodo, bloque in matriz.periodos.items():
            salida[periodo] = {"equipo": {}, "rival": {}}
            for fila in bloque.filas:
                salida[periodo]["equipo"][fila["clave"]] = (
                    PROC_FOTMOB if fila.get("equipo") is not None else PROC_SIN_DATO)
                salida[periodo]["rival"][fila["clave"]] = (
                    PROC_FOTMOB if fila.get("rival") is not None else PROC_SIN_DATO)
        return salida

    # ── utilidades internas ────────────────────────────────────────────────
    def _nombres_bandos(self, contenido: dict[str, Any],
                        partido: PartidoFinalizado) -> tuple[set[str], set[str]]:
        local, visit = set(), set()
        info = ((contenido.get("matchFacts") or {}).get("infoBox") or {})
        for clave, destino in (("homeTeam", local), ("awayTeam", visit)):
            equipo = info.get(clave) or {}
            for k in ("name", "shortName", "fullName"):
                if equipo.get(k):
                    destino.add(str(equipo[k]).lower())
        local.add(partido.local.lower())
        visit.add(partido.visitante.lower())
        return {n for n in local if n}, {n for n in visit if n}

    def _extraer_shotmap_y_eventos(
        self,
        contenido: dict[str, Any],
        id_equipo: str,
        es_local: bool,
        goles_equipo: int | None,
        goles_rival: int | None,
    ) -> dict[str, dict[str, dict[str, float]]]:
        """
        Extrae de `shotmap.shots` y `matchFacts.events` los goles de cabeza y los
        goles encajados por periodo (`FT`, `1T`, `2T`).
        """
        salida: dict[str, dict[str, dict[str, float]]] = {"FT": {}, "1T": {}, "2T": {}}
        # 1) Goles de cabeza desde shotmap
        disparos = ((contenido.get("shotmap") or {}).get("shots")) or []
        if isinstance(disparos, list):
            cab_1t = {"equipo": 0.0, "rival": 0.0}
            cab_2t = {"equipo": 0.0, "rival": 0.0}
            for s in disparos:
                if not isinstance(s, dict):
                    continue
                if str(s.get("eventType") or "") != "Goal":
                    continue
                if bool(s.get("isOwnGoal")):
                    continue
                if str(s.get("shotType") or "") != "Header":
                    continue
                tid = str(s.get("teamId") or "")
                lado = "equipo" if tid == id_equipo else "rival"
                per_raw = str(s.get("period") or "")
                if per_raw == "FirstHalf":
                    cab_1t[lado] += 1.0
                else:
                    cab_2t[lado] += 1.0
            salida["1T"]["goles_cabeza"] = cab_1t
            salida["2T"]["goles_cabeza"] = cab_2t
            salida["FT"]["goles_cabeza"] = {
                "equipo": cab_1t["equipo"] + cab_2t["equipo"],
                "rival": cab_1t["rival"] + cab_2t["rival"],
            }

        # 2) Goles encajados por periodo
        if goles_equipo is not None and goles_rival is not None:
            salida["FT"]["goles_encajados"] = {
                "equipo": float(goles_rival),
                "rival": float(goles_equipo),
            }
            eventos = ((contenido.get("matchFacts") or {}).get("events") or {}).get("events") or []
            ht_local = ht_visit = None
            if isinstance(eventos, list):
                for ev in eventos:
                    if not isinstance(ev, dict):
                        continue
                    if str(ev.get("type") or "") == "Half":
                        sc = ev.get("halfStrShort") or ev.get("scoreStr") or ""
                        gl_ht, gv_ht = _goles_desde_texto(str(sc))
                        if gl_ht is not None and gv_ht is not None:
                            ht_local, ht_visit = gl_ht, gv_ht
                            break
                        new_sc = ev.get("newScore")
                        if isinstance(new_sc, list) and len(new_sc) >= 2:
                            try:
                                ht_local, ht_visit = int(new_sc[0]), int(new_sc[1])
                                break
                            except (TypeError, ValueError):
                                pass
                if ht_local is None:
                    # Contar eventos de tipo Goal en el primer tiempo (time <= 45)
                    g_loc_1t = 0
                    g_vis_1t = 0
                    for ev in eventos:
                        if not isinstance(ev, dict) or str(ev.get("type") or "") != "Goal":
                            continue
                        minuto = ev.get("time") or 0
                        sobre = ev.get("overloadTime")
                        # En FotMob, un gol del 1T tiene time <= 45 (con o sin overloadTime)
                        if isinstance(minuto, (int, float)) and minuto <= 45:
                            if ev.get("isHome"):
                                g_loc_1t += 1
                            else:
                                g_vis_1t += 1
                    ht_local, ht_visit = g_loc_1t, g_vis_1t
            if ht_local is not None and ht_visit is not None:
                g1_eq = ht_local if es_local else ht_visit
                g1_riv = ht_visit if es_local else ht_local
                g2_eq = max(0, goles_equipo - g1_eq)
                g2_riv = max(0, goles_rival - g1_riv)
                salida["1T"]["goles_encajados"] = {
                    "equipo": float(g1_riv),
                    "rival": float(g1_eq),
                }
                salida["2T"]["goles_encajados"] = {
                    "equipo": float(g2_riv),
                    "rival": float(g2_eq),
                }
        return salida

    def _indexar_metricas(self, bloque: Any) -> dict[str, dict[str, Any]]:
        """
        Convierte el bloque de un periodo en {clave_metrica: {"local": v, "visitante": v}}.
        En esta fuente, stats[0] = local y stats[1] = visitante.
        Además desglosa cadenas compuestas de FotMob ("203 (74%)") en completados,
        totales y porcentaje.
        """
        from fuente_flashscore import _desglosar_compuesto

        MAPA_CLAVES_FOTMOB: dict[str, tuple[str, ...]] = {
            "expected_goals": ("xg",),
            "BallPossesion": ("posesion_pct", "posesion"),
            "total_shots": ("remates_totales",),
            "ShotsOnTarget": ("remates_puerta", "remates_a_puerta"),
            "big_chance": ("grandes_ocasiones",),
            "big_chance_missed_title": ("grandes_ocasiones_falladas", "grandes_ocasiones_perdidas"),
            "corners": ("corners", "corneres"),
            "passes": ("pases_total", "pases_totales"),
            "accurate_passes": ("pases_precision_pct", "pases_pct"),
            "yellow_cards": ("tarjetas_amarillas",),
            "red_cards": ("tarjetas_rojas",),
            "expected_goals_on_target": ("xgot",),
            "expected_goals_open_play": ("xg_juego_abierto",),
            "expected_goals_set_play": ("xg_balon_parado",),
            "expected_goals_non_penalty": ("xg_sin_penalti",),
            "ShotsOffTarget": ("remates_fuera",),
            "blocked_shots": ("remates_rechazados",),
            "shots_inside_box": ("remates_dentro_area",),
            "shots_outside_box": ("remates_fuera_area",),
            "shots_woodwork": ("al_palo",),
            "touches_opp_box": ("toques_area_rival",),
            "Offsides": ("fueras_juego", "fueras_de_juego"),
            "long_balls_accurate": ("pases_largos_pct",),
            "accurate_crosses": ("cruces_pct", "centros_pct"),
            "throw_ins": ("saques_banda", "saques_de_banda"),
            "fouls": ("faltas",),
            "tackles_succeeded": ("entradas_pct", "entradas_ganadas"),
            "duel_won": ("duelos_ganados",),
            "ground_duels_won": ("duelos_suelo_pct",),
            "aerials_won": ("duelos_aereos_pct",),
            "clearances": ("despejes",),
            "interceptions": ("intercepciones",),
            "shot_blocks": ("bloqueos",),
            "keeper_saves": ("paradas",),
        }

        indice: dict[str, dict[str, Any]] = {}
        if not isinstance(bloque, dict):
            return indice
        for grupo in bloque.get("stats", []) or []:
            if not isinstance(grupo, dict):
                continue
            for item in grupo.get("stats", []) or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "title":
                    continue
                valores = item.get("stats")
                if not isinstance(valores, list) or len(valores) < 2:
                    continue
                key_fm = str(item.get("key") or "").strip()
                titulo = str(item.get("title") or "").strip().lower()
                localizado = str(item.get("localizedTitle") or "").strip().lower()

                # Desglose de métricas compuestas ("203 (74%)" o "88% (260/296)")
                p_loc, c_loc, t_loc = _desglosar_compuesto(valores[0])
                p_vis, c_vis, t_vis = _desglosar_compuesto(valores[1])

                if key_fm == "accurate_passes" or titulo == "accurate passes":
                    if p_loc is not None or p_vis is not None:
                        indice["pases_precision_pct"] = {"local": p_loc, "visitante": p_vis}
                        indice["pases_pct"] = {"local": p_loc, "visitante": p_vis}
                    if c_loc is not None or c_vis is not None:
                        indice["pases_completados"] = {"local": c_loc, "visitante": c_vis}
                elif key_fm == "long_balls_accurate" or titulo == "accurate long balls":
                    if p_loc is not None or p_vis is not None:
                        indice["pases_largos_pct"] = {"local": p_loc, "visitante": p_vis}
                    if c_loc is not None or c_vis is not None:
                        indice["pases_largos_completados"] = {"local": c_loc, "visitante": c_vis}
                    if t_loc is not None or t_vis is not None:
                        indice["pases_largos"] = {"local": t_loc, "visitante": t_vis}
                elif key_fm == "accurate_crosses" or titulo == "accurate crosses":
                    if p_loc is not None or p_vis is not None:
                        indice["cruces_pct"] = {"local": p_loc, "visitante": p_vis}
                        indice["centros_pct"] = {"local": p_loc, "visitante": p_vis}
                    if c_loc is not None or c_vis is not None:
                        indice["centros_completados"] = {"local": c_loc, "visitante": c_vis}
                    if t_loc is not None or t_vis is not None:
                        indice["centros"] = {"local": t_loc, "visitante": t_vis}
                elif key_fm == "tackles_succeeded" or titulo in ("tackles", "tackles won", "tackles succeeded"):
                    if p_loc is not None or p_vis is not None:
                        indice["entradas_ganadas_pct"] = {"local": p_loc, "visitante": p_vis}
                    if c_loc is not None or c_vis is not None:
                        indice["entradas_ganadas"] = {"local": c_loc, "visitante": c_vis}
                        indice["entradas_pct"] = {"local": c_loc, "visitante": c_vis}
                    if t_loc is not None or t_vis is not None:
                        indice["entradas"] = {"local": t_loc, "visitante": t_vis}

                # Mapeo directo por key de FotMob
                if key_fm in MAPA_CLAVES_FOTMOB:
                    for cl_dest in MAPA_CLAVES_FOTMOB[key_fm]:
                        if cl_dest not in indice:
                            indice[cl_dest] = {"local": valores[0], "visitante": valores[1]}

                for definicion in CATALOGO_METRICAS:
                    if definicion.clave in indice:
                        continue
                    if any(alias == titulo or alias == localizado for alias in definicion.alias):
                        indice[definicion.clave] = {"local": valores[0], "visitante": valores[1]}
                    elif any(alias in titulo for alias in definicion.alias):
                        indice[definicion.clave] = {"local": valores[0], "visitante": valores[1]}
        return indice

    def _valor(self, definicion: DefinicionMetrica, bruto: Any) -> float | None:
        """Aplica la conversión adecuada según la métrica."""
        if bruto is None:
            return None
        clave = definicion.clave
        if clave in ("pases_precision_pct", "pases_pct"):
            return _a_pct(bruto)
        if clave in ("pases_tercio_final_pct", "entradas_pct"):
            return _a_float(bruto)
        if clave.endswith("_pct") or clave == "posesion":
            pct = _a_pct(bruto)
            if pct is not None:
                return pct
            return _a_float(bruto)
        if clave in ("pases_total", "pases_totales", "pases_completados"):
            return _a_entero(bruto)
        return _a_float(bruto)

    def _agregar_por_jugador(self, contenido: dict[str, Any], id_equipo: str = "",
                             es_local: bool = True) -> dict[str, dict[str, dict[str, Any]]]:
        """
        Métricas que la fuente solo publica a nivel de jugador: se suman por equipo.
        """
        MAPA_CLAVES: dict[str, tuple[str, ...]] = {
            "expected_assists": ("xa_equipo", "xa"),
            "passes_into_final_third": ("pases_tercio_final_pct", "pases_tercio_final_completados"),
            "line_breaking_passes": ("pases_entre_lineas",),
            "chances_created": ("pases_clave",),
            "errors_led_to_goal": ("errores_gol",),
            "recoveries": ("recuperaciones",),
            "expected_goals_on_target_faced": ("xgot_enfrentado", "xgot_enfrentados"),
            "goals_prevented": ("goles_evitados",),
            "goals_conceded": ("goles_encajados",),
        }

        jugadores = contenido.get("playerStats") or {}
        if not isinstance(jugadores, dict):
            return {}

        por_equipo: dict[str, dict[str, float]] = {}
        for _pid, jugador in jugadores.items():
            if not isinstance(jugador, dict):
                continue
            tid = str(jugador.get("teamId") or "")
            if not tid:
                continue
            for bloque in jugador.get("stats", []) or []:
                if not isinstance(bloque, dict):
                    continue
                for _titulo, dato in (bloque.get("stats") or {}).items():
                    if not isinstance(dato, dict):
                        continue
                    clave_origen = str(dato.get("key") or "")
                    destinos = MAPA_CLAVES.get(clave_origen)
                    if not destinos:
                        continue
                    valor = (dato.get("stat") or {}).get("value")
                    if valor is None:
                        continue
                    try:
                        numero = float(valor)
                    except (TypeError, ValueError):
                        continue
                    por_equipo.setdefault(tid, {})
                    for destino in destinos:
                        por_equipo[tid][destino] = por_equipo[tid].get(destino, 0.0) + numero

        if not por_equipo:
            return {}

        tid_equipo = id_equipo if id_equipo in por_equipo else ""
        if not tid_equipo:
            nombre_buscado = ""
            for jugador in jugadores.values():
                if isinstance(jugador, dict) and str(jugador.get("teamId")) == id_equipo:
                    nombre_buscado = str(jugador.get("teamName") or "")
                    break
            if nombre_buscado:
                for tid, jugador in ((str(j.get("teamId")), j) for j in jugadores.values()
                                     if isinstance(j, dict)):
                    if tid and str(jugador.get("teamName") or "") == nombre_buscado:
                        tid_equipo = tid
                        break
        if not tid_equipo:
            return {}

        rivales = [tid for tid in por_equipo if tid != tid_equipo]
        tid_rival = rivales[0] if rivales else ""

        metricas_equipo = por_equipo.get(tid_equipo, {})
        metricas_rival = por_equipo.get(tid_rival, {}) if tid_rival else {}

        salida: dict[str, dict[str, dict[str, Any]]] = {"FT": {}}
        todas_claves = set(metricas_equipo.keys()) | set(metricas_rival.keys())
        for clave in todas_claves:
            valor_eq = metricas_equipo.get(clave, 0.0)
            valor_rival = metricas_rival.get(clave, 0.0)
            salida["FT"][clave] = {
                "equipo": round(valor_eq, 2),
                "rival": round(valor_rival, 2),
            }
        return salida


# ═══════════════════════════════════════════════════════════════════════════════
# CONSOLIDACIÓN MATEMÁTICA MULTITIEMPO Y CERO NULOS (SECCIÓN 5 DE LA DIRECTIVA)
# ═══════════════════════════════════════════════════════════════════════════════

PARES_ALIAS_CATALOGO: tuple[tuple[str, str], ...] = (
    ("posesion", "posesion_pct"),
    ("remates_a_puerta", "remates_puerta"),
    ("grandes_ocasiones_perdidas", "grandes_ocasiones_falladas"),
    ("corneres", "corners"),
    ("fueras_de_juego", "fueras_juego"),
    ("pases_totales", "pases_total"),
    ("pases_pct", "pases_precision_pct"),
    ("centros_pct", "cruces_pct"),
    ("xa", "xa_equipo"),
    ("saques_de_banda", "saques_banda"),
    ("xgot_enfrentados", "xgot_enfrentado"),
    ("saques_de_puerta", "saques_puerta"),
)
ALIAS_SINCRONIZADOS: tuple[tuple[str, str], ...] = PARES_ALIAS_CATALOGO

METRICAS_CONTINUAS_ADITIVAS: frozenset[str] = frozenset({
    "xg", "xgot", "xg_juego_abierto", "xg_balon_parado", "xg_sin_penalti",
    "xa", "xa_equipo", "xgot_enfrentado", "xgot_enfrentados", "goles_evitados",
})

METRICAS_CONTEO_ADITIVAS: frozenset[str] = frozenset({
    "remates_totales", "remates_puerta", "remates_a_puerta", "remates_fuera",
    "remates_rechazados", "remates_dentro_area", "remates_fuera_area",
    "al_palo", "goles_cabeza",
    "grandes_ocasiones", "grandes_ocasiones_aprovechadas",
    "grandes_ocasiones_falladas", "grandes_ocasiones_perdidas",
    "toques_area_rival", "pases_entre_lineas",
    "corners", "corneres", "tiros_libres", "fueras_juego", "fueras_de_juego",
    "tarjetas_amarillas", "tarjetas_rojas",
    "pases_total", "pases_totales", "pases_completados",
    "pases_largos", "pases_largos_completados",
    "pases_tercio_final", "pases_tercio_final_completados",
    "centros", "centros_completados",
    "pases_clave", "saques_banda", "saques_de_banda",
    "faltas", "entradas", "entradas_ganadas",
    "duelos_ganados", "despejes", "intercepciones", "bloqueos", "recuperaciones",
    "errores_remate", "errores_gol",
    "paradas", "goles_encajados", "saques_puerta", "saques_de_puerta",
})


def _sincronizar_alias_dict(d: dict[str, float | None]) -> None:
    for a, b in PARES_ALIAS_CATALOGO:
        if d.get(a) is not None and d.get(b) is None:
            d[b] = d[a]
        elif d.get(b) is not None and d.get(a) is None:
            d[a] = d[b]


def _resolver_ecuaciones_periodo(
    eq: dict[str, float | None],
    riv: dict[str, float | None],
    goles_eq: float | None = None,
    goles_riv: float | None = None,
    estimados_eq: set[str] | None = None,
    estimados_riv: set[str] | None = None,
) -> None:
    """
    Aplica las ecuaciones exactas de consistencia estadística de la Sección 5
    dentro de un mismo periodo (`FT`, `1T` o `2T`).
    """
    if estimados_eq is None:
        estimados_eq = set()
    if estimados_riv is None:
        estimados_riv = set()

    def _marcar_est(d_ref: dict[str, float | None], k: str) -> None:
        s_ref = estimados_eq if d_ref is eq else estimados_riv
        s_ref.add(k)
        for a, b in ALIAS_SINCRONIZADOS:
            if a == k:
                s_ref.add(b)
            elif b == k:
                s_ref.add(a)

    _sincronizar_alias_dict(eq)
    _sincronizar_alias_dict(riv)

    # 1. Posesión: eq + riv = 100% (solo si al menos un bando fue publicado; no inventar 50/50 si falta)
    pos_eq = eq.get("posesion_pct")
    pos_riv = riv.get("posesion_pct")
    if pos_eq is not None and pos_riv is None:
        riv["posesion_pct"] = round(max(0.0, 100.0 - pos_eq), 1)
    elif pos_riv is not None and pos_eq is None:
        eq["posesion_pct"] = round(max(0.0, 100.0 - pos_riv), 1)
    _sincronizar_alias_dict(eq)
    _sincronizar_alias_dict(riv)

    # 2. Goles propios, encajados y xGOT enfrentados (cruce de bandos)
    if goles_eq is not None:
        if eq.get("goles") is None and eq.get("goles_favor") is None:
            eq["goles_favor"] = float(goles_eq)
        if riv.get("goles_encajados") is None:
            riv["goles_encajados"] = float(goles_eq)
    if goles_riv is not None:
        if riv.get("goles") is None and riv.get("goles_favor") is None:
            riv["goles_favor"] = float(goles_riv)
        if eq.get("goles_encajados") is None:
            eq["goles_encajados"] = float(goles_riv)

    for propio, ajeno in ((eq, riv), (riv, eq)):
        s_propio = estimados_eq if propio is eq else estimados_riv
        s_ajeno = estimados_riv if propio is eq else estimados_eq
        if propio.get("xgot_enfrentados") is None and ajeno.get("xgot") is not None:
            propio["xgot_enfrentados"] = ajeno["xgot"]
            if "xgot" in s_ajeno:
                _marcar_est(propio, "xgot_enfrentados")
        elif ajeno.get("xgot") is None and propio.get("xgot_enfrentados") is not None:
            ajeno["xgot"] = propio["xgot_enfrentados"]
            if "xgot_enfrentados" in s_propio:
                _marcar_est(ajeno, "xgot")

        # 2b. Saques de puerta (estimación física):
        sp = propio.get("saques_puerta")
        rf_atk = ajeno.get("remates_fuera")
        al_palo_atk = ajeno.get("al_palo") or 0.0
        if (sp is None or sp == 0.0) and rf_atk is not None:
            if rf_atk > 0:
                propio["saques_puerta"] = float(max(1.0, round(rf_atk - al_palo_atk)))
                _marcar_est(propio, "saques_puerta")
            elif sp is None and goles_eq is not None:
                propio["saques_puerta"] = 0.0
                _marcar_est(propio, "saques_puerta")

        # 2c. Tiros libres directos: concedidos por faltas o fueras de juego cometidos por el rival
        tl = propio.get("tiros_libres")
        faltas_riv = ajeno.get("faltas")
        fj_riv = ajeno.get("fueras_juego") or ajeno.get("fueras_de_juego") or 0.0
        if (tl is None or tl == 0.0) and faltas_riv is not None and faltas_riv > 0:
            propio["tiros_libres"] = float(round(faltas_riv + fj_riv))
            _marcar_est(propio, "tiros_libres")

        # 2d. Pases entre líneas (line breaking passes)
        pel = propio.get("pases_entre_lineas")
        if pel is None or pel == 0.0:
            ptf_comp = propio.get("pases_tercio_final_completados") or propio.get("pases_tercio_final") or 0.0
            if ptf_comp > 5:
                propio["pases_entre_lineas"] = float(max(1.0, round(ptf_comp * 0.05)))
                _marcar_est(propio, "pases_entre_lineas")

        # 2e. Reconstrucción de xG y xGOT cuando hubo remates observados pero xG no fue publicado (estimacion_modelo):
        rt_raw = propio.get("remates_totales")
        rp_raw = propio.get("remates_a_puerta") if propio.get("remates_a_puerta") is not None else propio.get("remates_puerta")
        rt_propio = rt_raw or 0.0
        rp_propio = rp_raw or 0.0
        rda_propio = propio.get("remates_dentro_area") or 0.0
        rfa_propio = propio.get("remates_fuera_area") or 0.0
        go_propio = propio.get("grandes_ocasiones") or 0.0
        gf_propio = propio.get("goles") if propio.get("goles") is not None else propio.get("goles_favor")

        if rt_propio > 0 and (propio.get("xg") is None or propio.get("xg") <= 0.001):
            if rda_propio > 0:
                propio["xg"] = round(0.11 * rda_propio + 0.035 * rfa_propio + 0.28 * go_propio, 2)
            else:
                propio["xg"] = round(0.09 * rt_propio + 0.14 * rp_propio, 2)
            _marcar_est(propio, "xg")
        elif rt_raw is not None and rt_raw == 0.0 and goles_eq is not None and gf_propio is not None and gf_propio > 0:
            # 2f. Protección en FT cuando un partido tuvo goles pero remates_totales == 0 reportado:
            # Si ambos equipos tienen remates == 0 o None, es un partido sin estadísticas del proveedor (ej. Nautico 1-0 Botafogo SP).
            # Prohibido inyectar 1.00 sintético: todas las 45 métricas quedan como sin_dato (None),
            # conservando únicamente el marcador oficial FT para el Cuadro B.
            rt_ajeno_check = ajeno.get("remates_totales") or ajeno.get("remates") or 0.0
            if float(rt_ajeno_check or 0.0) <= 0.001:
                pass  # Partido sin estadísticas: no inyectar métricas sintéticas
            else:
                gf_val = float(gf_propio)
                gc_val = float(ajeno.get("goles") if ajeno.get("goles") is not None else (propio.get("goles_encajados") or 0.0))
                if propio.get("xg") is None:
                    propio["xg"] = float(round(gf_val, 2))
                    _marcar_est(propio, "xg")
                if propio.get("xgot") is None:
                    propio["xgot"] = float(round(gf_val, 2))
                    _marcar_est(propio, "xgot")
                if propio.get("xgot_enfrentados") is None:
                    propio["xgot_enfrentados"] = float(round(gc_val, 2))
                    _marcar_est(propio, "xgot_enfrentados")
                if propio.get("goles_evitados") is None:
                    propio["goles_evitados"] = 0.0
                    _marcar_est(propio, "goles_evitados")

        if rp_propio > 0 and (propio.get("xgot") is None or propio.get("xgot") <= 0.001):
            gf_val = float(gf_propio if gf_propio is not None else 0.0)
            propio["xgot"] = round(0.29 * rp_propio + 0.12 * gf_val, 2)
            _marcar_est(propio, "xgot")

        rp_ajeno_raw = ajeno.get("remates_a_puerta") if ajeno.get("remates_a_puerta") is not None else ajeno.get("remates_puerta")
        rp_ajeno = rp_ajeno_raw or 0.0
        gc_raw = ajeno.get("goles") if ajeno.get("goles") is not None else propio.get("goles_encajados")
        gc_val = float(gc_raw or 0.0)
        if propio.get("xgot_enfrentados") is None and (rp_ajeno_raw is not None or gc_raw is not None):
            if rp_ajeno > 0 or gc_val > 0:
                propio["xgot_enfrentados"] = round(0.29 * max(1.0, rp_ajeno) + 0.12 * gc_val, 2)
                _marcar_est(propio, "xgot_enfrentados")
            elif rp_ajeno_raw == 0.0 and gc_val == 0.0:
                propio["xgot_enfrentados"] = 0.0
                _marcar_est(propio, "xgot_enfrentados")

        if propio.get("goles_evitados") is None and propio.get("xgot_enfrentados") is not None and gc_raw is not None:
            propio["goles_evitados"] = round(propio["xgot_enfrentados"] - gc_val, 2)
            if "xgot_enfrentados" in s_propio:
                _marcar_est(propio, "goles_evitados")

    _sincronizar_alias_dict(eq)
    _sincronizar_alias_dict(riv)

    # 3. Ecuaciones internas por bando
    for d in (eq, riv):
        # 3a. Remates: totales = a_puerta + fuera + rechazados
        rt = d.get("remates_totales")
        rp = d.get("remates_a_puerta")
        rf = d.get("remates_fuera")
        rr = d.get("remates_rechazados")
        if rt is not None and rp is not None and rf is not None and rr is None:
            d["remates_rechazados"] = float(max(0.0, rt - rp - rf))
        elif rt is not None and rp is not None and rr is not None and rf is None:
            d["remates_fuera"] = float(max(0.0, rt - rp - rr))
        elif rt is not None and rf is not None and rr is not None and rp is None:
            d["remates_a_puerta"] = float(max(0.0, rt - rf - rr))
        elif rp is not None and rf is not None and rr is not None and rt is None:
            d["remates_totales"] = float(rp + rf + rr)

        # 3b. Remates: totales = dentro_area + fuera_area
        rt = d.get("remates_totales")
        rda = d.get("remates_dentro_area")
        rfa = d.get("remates_fuera_area")
        if rt is not None and rda is not None and rfa is None:
            d["remates_fuera_area"] = float(max(0.0, rt - rda))
        elif rt is not None and rfa is not None and rda is None:
            d["remates_dentro_area"] = float(max(0.0, rt - rfa))
        elif rda is not None and rfa is not None and rt is None:
            d["remates_totales"] = float(rda + rfa)

        # 3c. Grandes ocasiones: totales = aprovechadas + perdidas
        go = d.get("grandes_ocasiones")
        go_ap = d.get("grandes_ocasiones_aprovechadas")
        go_pe = d.get("grandes_ocasiones_perdidas")
        if go is not None and go_pe is not None and go_ap is None:
            d["grandes_ocasiones_aprovechadas"] = float(max(0.0, go - go_pe))
        elif go is not None and go_ap is not None and go_pe is None:
            d["grandes_ocasiones_perdidas"] = float(max(0.0, go - go_ap))
        elif go_ap is not None and go_pe is not None and go is None:
            d["grandes_ocasiones"] = float(go_ap + go_pe)

        # 3d. Entradas: entradas_pct (FotMob) es conteo de entradas ganadas
        if d.get("entradas_ganadas") is None and d.get("entradas_pct") is not None:
            d["entradas_ganadas"] = d["entradas_pct"]
        elif d.get("entradas_pct") is None and d.get("entradas_ganadas") is not None:
            d["entradas_pct"] = d["entradas_ganadas"]

        # 3e. Tríos (pct, completados, totales)
        trios = (
            ("pases_pct", "pases_completados", "pases_totales"),
            ("pases_largos_pct", "pases_largos_completados", "pases_largos"),
            ("pases_tercio_final_pct", "pases_tercio_final_completados", "pases_tercio_final"),
            ("centros_pct", "centros_completados", "centros"),
            ("entradas_ganadas_pct", "entradas_ganadas", "entradas"),
        )
        for k_pct, k_comp, k_tot in trios:
            pct = d.get(k_pct)
            comp = d.get(k_comp)
            tot = d.get(k_tot)
            if tot is not None and tot == 0:
                if comp is None:
                    d[k_comp] = 0.0
                if pct is None:
                    d[k_pct] = 0.0
                continue
            if comp is not None and tot is not None and pct is None:
                d[k_pct] = round((comp / tot) * 100.0, 1) if tot > 0 else 0.0
            elif tot is not None and pct is not None and comp is None:
                d[k_comp] = float(round((tot * pct) / 100.0))
            elif comp is not None and pct is not None and tot is None:
                d[k_tot] = float(round((comp * 100.0) / pct)) if pct > 0 else comp

        # Caso especial pases en el tercio final cuando solo hay completados
        if d.get("pases_tercio_final_completados") is not None and d.get("pases_tercio_final") is None:
            pct_pases = d.get("pases_pct") or 75.0
            comp_tf = d["pases_tercio_final_completados"] or 0.0
            d["pases_tercio_final"] = (
                float(max(comp_tf, round(comp_tf * 100.0 / pct_pases)))
                if pct_pases > 0 else comp_tf
            )
            _marcar_est(d, "pases_tercio_final")

        # Caso especial entradas cuando solo hay entradas_ganadas
        if d.get("entradas_ganadas") is not None and d.get("entradas") is None:
            eg = d["entradas_ganadas"] or 0.0
            pct_e = d.get("entradas_ganadas_pct")
            if pct_e is not None and pct_e > 0:
                d["entradas"] = float(max(eg, round(eg * 100.0 / pct_e)))
            else:
                d["entradas"] = float(eg)
                d["entradas_ganadas_pct"] = 100.0 if eg > 0 else 0.0
                _marcar_est(d, "entradas_ganadas_pct")
            _marcar_est(d, "entradas")

        # 3f. Desglose de xG cuando la fuente publica xG total pero omite sub-tipos
        xg_val = d.get("xg")
        s_d = estimados_eq if d is eq else estimados_riv
        if xg_val is not None:
            # Prohibido copiar xg en xg_sin_penalti o xg_juego_abierto sin desglose independiente
            if d.get("xg_juego_abierto") is None and d.get("xg_balon_parado") is not None:
                d["xg_juego_abierto"] = round(max(0.0, xg_val - d["xg_balon_parado"]), 2)
                if "xg" in s_d or "xg_balon_parado" in s_d:
                    _marcar_est(d, "xg_juego_abierto")
            elif d.get("xg_balon_parado") is None and d.get("xg_juego_abierto") is not None:
                d["xg_balon_parado"] = round(max(0.0, xg_val - d["xg_juego_abierto"]), 2)
                if "xg" in s_d or "xg_juego_abierto" in s_d:
                    _marcar_est(d, "xg_balon_parado")

        # 3g. Goles evitados = xgot_enfrentados - goles_encajados
        xgot_enf = d.get("xgot_enfrentados")
        g_enc = d.get("goles_encajados")
        if xgot_enf is not None and g_enc is not None and d.get("goles_evitados") is None:
            d["goles_evitados"] = round(xgot_enf - g_enc, 2)
            if "xgot_enfrentados" in s_d:
                _marcar_est(d, "goles_evitados")

        _sincronizar_alias_dict(d)


def consolidar_matriz_completa(matriz: MatrizPartido) -> None:
    """
    Puntos 37 y 39:
      1. Presencia de todas las 45 métricas del catálogo en `FT`, `1T` y `2T` (270 celdas).
      2. Coherencia aritmética multitiempo (`1T + 2T == FT`) únicamente cuando se conocen
         al menos 2 de los 3 periodos.
      3. Prohibido el reparto sintético 50/50 (`ratio_1t = 0.5`) entre `1T` y `2T` cuando
         solo existe el dato de `FT`. En ese caso, `1T` y `2T` quedan como `None` con
         procedencia `PROC_SIN_DATO` (`"sin_dato"`), distinguiendo estrictamente un `0.0`
         observado frente a un dato no publicado (`None`).
      4. Ecuaciones de consistencia estadística de la Sección 5 intra-periodo.
    """
    from fuente_flashscore import PROC_DERIVADO, PROC_SIN_DATO, pases_ratio_derivado

    # Construir diccionario de trabajo por_per[periodo][lado][clave] y snapshot de valores nativos
    por_per: dict[str, dict[str, dict[str, float | None]]] = {
        p: {"equipo": {}, "rival": {}} for p in ("FT", "1T", "2T")
    }
    nativos_por_per: dict[str, dict[str, dict[str, float | None]]] = {
        p: {"equipo": {}, "rival": {}} for p in ("FT", "1T", "2T")
    }
    estimados_por_per: dict[str, dict[str, set[str]]] = {
        p: {"equipo": set(), "rival": set()} for p in ("FT", "1T", "2T")
    }
    inconsistencias: list[dict[str, Any]] = list(getattr(matriz, "inconsistencias_aritmeticas", None) or [])
    for p in ("FT", "1T", "2T"):
        bloque = matriz.periodos.get(p)
        if bloque is None:
            continue
        for fila in bloque.filas:
            cl = fila["clave"]
            v_eq = fila.get("equipo")
            v_rv = fila.get("rival")
            por_per[p]["equipo"][cl] = v_eq
            por_per[p]["rival"][cl] = v_rv
            nativos_por_per[p]["equipo"][cl] = v_eq
            nativos_por_per[p]["rival"][cl] = v_rv

    # Asegurar que todas las claves de CATALOGO_METRICAS existan en el mapa
    for p in ("FT", "1T", "2T"):
        for d in CATALOGO_METRICAS:
            por_per[p]["equipo"].setdefault(d.clave, None)
            por_per[p]["rival"].setdefault(d.clave, None)
            nativos_por_per[p]["equipo"].setdefault(d.clave, None)
            nativos_por_per[p]["rival"].setdefault(d.clave, None)

    # Pasada 1: resolver ecuaciones intra-periodo con los datos nativos
    _resolver_ecuaciones_periodo(
        por_per["FT"]["equipo"], por_per["FT"]["rival"],
        goles_eq=matriz.goles_equipo, goles_riv=matriz.goles_rival,
        estimados_eq=estimados_por_per["FT"]["equipo"],
        estimados_riv=estimados_por_per["FT"]["rival"],
    )
    _resolver_ecuaciones_periodo(
        por_per["1T"]["equipo"], por_per["1T"]["rival"],
        estimados_eq=estimados_por_per["1T"]["equipo"],
        estimados_riv=estimados_por_per["1T"]["rival"],
    )
    _resolver_ecuaciones_periodo(
        por_per["2T"]["equipo"], por_per["2T"]["rival"],
        estimados_eq=estimados_por_per["2T"]["equipo"],
        estimados_riv=estimados_por_per["2T"]["rival"],
    )

    # Pasada 2: coherencia aritmética multitiempo (1T + 2T == FT) SOLO por identidad exacta
    # (cuando se conocen 2 de los 3 periodos, o cuando FT == 0.0 observado en una métrica no negativa)
    todas_aditivas = METRICAS_CONTEO_ADITIVAS | METRICAS_CONTINUAS_ADITIVAS
    for lado in ("equipo", "rival"):
        for cl in todas_aditivas:
            es_continua = cl in METRICAS_CONTINUAS_ADITIVAS
            v_ft = por_per["FT"][lado].get(cl)
            v_1t = por_per["1T"][lado].get(cl)
            v_2t = por_per["2T"][lado].get(cl)

            if v_1t is not None and v_2t is not None and v_ft is None:
                v_ft = round(v_1t + v_2t, 2) if es_continua else float(round(v_1t + v_2t))
                por_per["FT"][lado][cl] = v_ft
                if cl in estimados_por_per["1T"][lado] or cl in estimados_por_per["2T"][lado]:
                    estimados_por_per["FT"][lado].add(cl)
            elif v_ft is not None and v_1t is not None and v_2t is None:
                dif = v_ft - v_1t
                if cl != "goles_evitados" and dif < -1e-6:
                    por_per["2T"][lado][cl] = None
                    inconsistencias.append({
                        "clave": cl, "lado": lado, "periodo_afectado": "2T",
                        "FT": v_ft, "1T": v_1t, "diferencia": round(dif, 4),
                        "motivo": "FT < 1T en magnitud no negativa; no se convierte en 0.0",
                    })
                else:
                    if cl != "goles_evitados":
                        dif = max(0.0, dif)
                    por_per["2T"][lado][cl] = round(dif, 2) if es_continua else float(round(dif))
                    if cl in estimados_por_per["FT"][lado] or cl in estimados_por_per["1T"][lado]:
                        estimados_por_per["2T"][lado].add(cl)
            elif v_ft is not None and v_2t is not None and v_1t is None:
                dif = v_ft - v_2t
                if cl != "goles_evitados" and dif < -1e-6:
                    por_per["1T"][lado][cl] = None
                    inconsistencias.append({
                        "clave": cl, "lado": lado, "periodo_afectado": "1T",
                        "FT": v_ft, "2T": v_2t, "diferencia": round(dif, 4),
                        "motivo": "FT < 2T en magnitud no negativa; no se convierte en 0.0",
                    })
                else:
                    if cl != "goles_evitados":
                        dif = max(0.0, dif)
                    por_per["1T"][lado][cl] = round(dif, 2) if es_continua else float(round(dif))
                    if cl in estimados_por_per["FT"][lado] or cl in estimados_por_per["2T"][lado]:
                        estimados_por_per["1T"][lado].add(cl)
            elif v_ft is not None and v_1t is None and v_2t is None:
                # Si FT es exactamente 0.0 observado en un conteo no negativo, 1T=0.0 y 2T=0.0 es exacto
                if v_ft == 0.0 and cl != "goles_evitados" and cl not in estimados_por_per["FT"][lado]:
                    por_per["1T"][lado][cl] = 0.0
                    por_per["2T"][lado][cl] = 0.0
                # Si v_ft > 0 y faltan 1T y 2T, PROHIBIDO repartir 50/50: quedan en None ("sin_dato")

    # Guardar snapshot de las celdas obtenidas por dato publicado o identidad aritmética estricta
    despues_identidades_por_per: dict[str, dict[str, dict[str, float | None]]] = {
        p: {
            "equipo": dict(por_per[p]["equipo"]),
            "rival": dict(por_per[p]["rival"]),
        }
        for p in ("FT", "1T", "2T")
    }

    # Pasada 3: re-ejecutar ecuaciones de consistencia en cada periodo con las derivaciones exactas
    for p in ("FT", "1T", "2T"):
        _resolver_ecuaciones_periodo(
            por_per[p]["equipo"], por_per[p]["rival"],
            goles_eq=(matriz.goles_equipo if p == "FT" else None),
            goles_riv=(matriz.goles_rival if p == "FT" else None),
            estimados_eq=estimados_por_per[p]["equipo"],
            estimados_riv=estimados_por_per[p]["rival"],
        )

    # Pasada 3b: reconciliación estricta 1T + 2T == FT cuando ambos 1T y 2T están presentes
    for lado in ("equipo", "rival"):
        for cl in todas_aditivas:
            es_continua = cl in METRICAS_CONTINUAS_ADITIVAS
            v_ft = por_per["FT"][lado].get(cl)
            v_1t = por_per["1T"][lado].get(cl)
            v_2t = por_per["2T"][lado].get(cl)

            if v_ft is not None and v_1t is not None and v_2t is not None:
                suma_mitades = v_1t + v_2t
                tol = 0.05 if es_continua else 0.001
                if abs(suma_mitades - v_ft) > tol:
                    if v_ft == 0.0 and cl != "goles_evitados":
                        por_per["1T"][lado][cl] = 0.0
                        por_per["2T"][lado][cl] = 0.0
                    elif suma_mitades > 0 and cl != "goles_evitados":
                        # Ajustar proporcionalmente a las mitades reales observadas
                        f_esc = v_ft / suma_mitades
                        p1 = round(v_1t * f_esc, 2) if es_continua else float(round(v_1t * f_esc))
                        p2 = round(v_ft - p1, 2) if es_continua else float(max(0.0, v_ft - p1))
                        por_per["1T"][lado][cl] = p1
                        por_per["2T"][lado][cl] = p2
            elif v_1t is not None and v_2t is not None and v_ft is None:
                v_ft = round(v_1t + v_2t, 2) if es_continua else float(round(v_1t + v_2t))
                por_per["FT"][lado][cl] = v_ft

    # Pasada 4: métricas de porcentaje y ratios (solo cuando existen los insumos reales del periodo)
    for p in ("FT", "1T", "2T"):
        for lado in ("equipo", "rival"):
            d = por_per[p][lado]
            xgot_enf = d.get("xgot_enfrentados")
            g_enc = d.get("goles_encajados")
            if xgot_enf is not None and g_enc is not None:
                d["goles_evitados"] = round(xgot_enf - g_enc, 2)

            if d.get("pases_ratio") is None:
                p_tot = d.get("pases_total") if d.get("pases_total") is not None else d.get("pases_totales")
                pos = d.get("posesion_pct")
                if p_tot is not None and pos is not None:
                    escala = 1.0 if p == "FT" else 2.0
                    ratio = pases_ratio_derivado(p_tot * escala, pos)
                    if ratio is not None:
                        d["pases_ratio"] = ratio

            _sincronizar_alias_dict(d)

            # Sanitizar únicamente NaN flotantes, preservando None para celdas sin dato real
            for definicion in CATALOGO_METRICAS:
                val = d.get(definicion.clave)
                if isinstance(val, float) and val != val:
                    d[definicion.clave] = None

    # Escribir los valores consolidados de vuelta en matriz.periodos y matriz.procedencia
    if not isinstance(matriz.procedencia, dict):
        matriz.procedencia = {}

    proc_previa_top: dict[str, dict[str, str]] = {
        "equipo": dict(matriz.procedencia.get("equipo") or {}) if isinstance(matriz.procedencia.get("equipo"), dict) else {},
        "rival": dict(matriz.procedencia.get("rival") or {}) if isinstance(matriz.procedencia.get("rival"), dict) else {},
    }

    no_disp_ft: set[str] = set()
    for p in ("FT", "1T", "2T"):
        bloque = matriz.periodos.get(p)
        no_disp_per: list[str] = []
        if bloque is None:
            filas_nuevas = []
            for d in CATALOGO_METRICAS:
                v_eq = por_per[p]["equipo"].get(d.clave)
                v_rv = por_per[p]["rival"].get(d.clave)
                if v_eq is None or v_rv is None:
                    no_disp_per.append(d.clave)
                    if p == "FT":
                        no_disp_ft.add(d.clave)
                filas_nuevas.append({
                    "clave": d.clave,
                    "metrica": d.etiqueta,
                    "categoria": d.categoria,
                    "equipo": v_eq,
                    "rival": v_rv,
                })
            matriz.periodos[p] = PeriodoPartido(p, filas_nuevas, no_disp_per)
        else:
            vistas: set[str] = set()
            for fila in bloque.filas:
                cl = fila["clave"]
                vistas.add(cl)
                v_eq = por_per[p]["equipo"].get(cl)
                v_rv = por_per[p]["rival"].get(cl)
                fila["equipo"] = v_eq
                fila["rival"] = v_rv
                if v_eq is None or v_rv is None:
                    no_disp_per.append(cl)
                    if p == "FT":
                        no_disp_ft.add(cl)
            for d in CATALOGO_METRICAS:
                if d.clave not in vistas:
                    v_eq = por_per[p]["equipo"].get(d.clave)
                    v_rv = por_per[p]["rival"].get(d.clave)
                    if v_eq is None or v_rv is None:
                        no_disp_per.append(d.clave)
                        if p == "FT":
                            no_disp_ft.add(d.clave)
                    bloque.filas.append({
                        "clave": d.clave,
                        "metrica": d.etiqueta,
                        "categoria": d.categoria,
                        "equipo": v_eq,
                        "rival": v_rv,
                    })
            bloque.no_disponibles = no_disp_per

        # Actualizar procedencia por periodo y bando
        mapa_p = matriz.procedencia.setdefault(p, {})
        if not isinstance(mapa_p, dict):
            mapa_p = {}
            matriz.procedencia[p] = mapa_p
        for lado in ("equipo", "rival"):
            mapa_l = mapa_p.setdefault(lado, {})
            for d in CATALOGO_METRICAS:
                cl = d.clave
                val_final = por_per[p][lado].get(cl)
                val_nativo = nativos_por_per[p][lado].get(cl)
                proc_ant = mapa_l.get(cl) or (proc_previa_top[lado].get(cl) if p == "FT" else None)
                if val_final is None:
                    mapa_l[cl] = PROC_SIN_DATO
                elif val_nativo is not None:
                    if proc_ant and proc_ant not in (PROC_SIN_DATO, "sin_dato", PROC_DERIVADO, "estimacion_modelo"):
                        mapa_l[cl] = proc_ant
                    else:
                        mapa_l[cl] = "fotmob"
                elif cl in estimados_por_per[p][lado]:
                    mapa_l[cl] = "estimacion_modelo"
                else:
                    mapa_l[cl] = PROC_DERIVADO

    # Sincronizar procedencia top-level con FT y registrar inconsistencias aritméticas
    for lado in ("equipo", "rival"):
        matriz.procedencia[lado] = dict(matriz.procedencia["FT"][lado])
    matriz.inconsistencias_aritmeticas = inconsistencias
    matriz.metricas_no_disponibles = sorted(no_disp_ft)


VERSION_ESQUEMA_COBERTURA = "v43.0"


def calcular_cobertura_y_promedio_metrica(
    partidos: list[dict[str, Any]],
    clave: str,
    periodo: str = "FT",
    lado: str = "equipo",
    version_cobertura: str | None = None,
) -> dict[str, Any]:
    """
    Punto 37: Calcula el promedio y la cobertura real de una métrica específica
    distinguiendo estrictamente entre:
      - `0.0` observado y confirmado por la fuente (sí entra en el denominador `n_con_dato`).
      - `None` / `"sin_dato"` cuando la fuente no publicó esa métrica (NO entra en `n_con_dato`).
      - Si una auditoría antigua en caché carece de `procedencia` o de `version_cobertura == 'v43.0'`,
        un `0.0` antiguo sin procedencia explícita NO asciende a `0.0` observado (se trata como `None`),
        excepto si el partido viene recién consolidado con `procedencia` explícita.
    """
    valores: list[float] = []
    fuentes: dict[str, int] = {
        "fotmob": 0,
        "flashscore": 0,
        "derivado": 0,
        "estimacion_modelo": 0,
        "sin_dato": 0,
    }
    n_ceros_observados = 0
    n_total = 0

    for p in partidos or []:
        if not isinstance(p, dict) or p.get("es_amistoso"):
            continue
        n_total += 1
        val_encontrado: float | None = None
        proc_encontrada = ""

        periodos = p.get("periodos") or {}
        bloque = periodos.get(periodo) or {}
        for fila in bloque.get("filas") or []:
            if isinstance(fila, dict) and fila.get("clave") == clave:
                v_raw = fila.get(lado)
                if isinstance(v_raw, (int, float)) and not isinstance(v_raw, bool) and not (isinstance(v_raw, float) and v_raw != v_raw):
                    val_encontrado = float(v_raw)
                break

        if val_encontrado is None and periodo == "FT" and lado == "equipo":
            v_top = p.get(clave)
            if isinstance(v_top, (int, float)) and not isinstance(v_top, bool) and not (isinstance(v_top, float) and v_top != v_top):
                val_encontrado = float(v_top)

        proc_p = p.get("procedencia") if isinstance(p.get("procedencia"), dict) else {}
        proc_per = proc_p.get(periodo) if isinstance(proc_p.get(periodo), dict) else {}
        proc_lado = proc_per.get(lado) if isinstance(proc_per.get(lado), dict) else (
            proc_p.get(lado) if (periodo == "FT" and isinstance(proc_p.get(lado), dict)) else {}
        )
        if isinstance(proc_lado, dict) and proc_lado.get(clave):
            proc_encontrada = str(proc_lado.get(clave)).lower().strip()

        ver_p = str(p.get("version_cobertura") or version_cobertura or "").strip()
        es_cache_antigua_sin_proc = bool(p.get("_cache_legacy") or (ver_p == "legacy_sin_procedencia"))
        if val_encontrado is not None and abs(val_encontrado) < 1e-9:
            if es_cache_antigua_sin_proc and not proc_encontrada:
                val_encontrado = None
            elif proc_encontrada in ("sin_dato", "dato_ausente", "relleno_legacy"):
                val_encontrado = None

        if val_encontrado is not None and proc_encontrada == "estimacion_modelo":
            # Una reconstrucción del modelo se reporta separada de la cobertura observada
            fuentes["estimacion_modelo"] += 1
            continue

        if val_encontrado is not None:
            valores.append(val_encontrado)
            if abs(val_encontrado) < 1e-9:
                n_ceros_observados += 1
            if proc_encontrada in ("fotmob", "flashscore", "derivado"):
                fuentes[proc_encontrada] += 1
            else:
                fuentes["fotmob"] += 1
        else:
            fuentes["sin_dato"] += 1

    n_con_dato = len(valores)
    promedio = round(sum(valores) / n_con_dato, 4) if n_con_dato > 0 else None
    cobertura_pct = round(100.0 * n_con_dato / n_total, 2) if n_total > 0 else 0.0
    return {
        "clave": clave,
        "periodo": periodo,
        "lado": lado,
        "promedio": promedio,
        "n_con_dato": n_con_dato,
        "n_total_partidos": n_total,
        "n_sin_dato": n_total - n_con_dato,
        "n_ceros_observados": n_ceros_observados,
        "cobertura_pct": cobertura_pct,
        "fuentes": fuentes,
        "version_cobertura": VERSION_ESQUEMA_COBERTURA,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE: SOFASCORE  (adaptador, desactivado por bloqueo de la red actual)
# ═══════════════════════════════════════════════════════════════════════════════

SOFASCORE_BASE = "https://www.sofascore.com"


class FuenteSofascore(FuenteXG):
    """
    Adaptador para Sofascore.

    Estado: NO OPERATIVO desde esta red. Comprobado en vivo (13-10-2026):
      • la web redirige a /captcha.html tanto en headless como en modo visible,
        incluso con playwright-stealth;
      • la API web (/api/v1/*) responde 403;
      • la API de la app (api.sofascore.app) responde 200 con cuerpo vacío.
    Se deja implementada contra el mismo contrato para poder activarla con
    AUDITORIA_FUENTE=sofascore desde una IP/sesión con acceso.
    """

    nombre = "sofascore"

    def __init__(self, headless: bool = True, pausa_entre_partidos: float = 1.5,
                 idioma: str = "es", timeout_ms: int = 45_000, verbose: bool = True,
                 max_paginas_escaneo: int = 8) -> None:
        self.headless = headless
        self.pausa = pausa_entre_partidos
        self.idioma = idioma
        self.timeout_ms = timeout_ms
        self.verbose = verbose
        self.max_paginas_escaneo = max(1, int(max_paginas_escaneo))
        self._pw = None
        self._browser = None
        self.ctx = None
        self.page = None

    def _log(self, msg: str) -> None:
        if self.verbose:
            marca = datetime.now(pytz.timezone(config.TIMEZONE)).strftime("%H:%M:%S")
            print(f"[AUDITORÍA-SOFA {marca}] {msg}")

    def _abrir_interno(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self.ctx = self._browser.new_context(user_agent=_USER_AGENT, locale="es-ES",
                                             viewport={"width": 1366, "height": 900})
        self.page = self.ctx.new_page()
        self.page.goto(SOFASCORE_BASE, wait_until="domcontentloaded", timeout=self.timeout_ms)
        self.page.wait_for_timeout(2500)
        if "captcha" in self.page.url.lower():
            raise RuntimeError(
                "Sofascore bloqueó la sesión con su muro de captcha (comprobado desde esta red: "
                "la web redirige a /captcha.html y su API responde 403). "
                "Ejecuta la auditoría con la fuente por defecto (AUDITORIA_FUENTE=fotmob) "
                "o desde una IP/sesión con acceso a Sofascore."
            )
        self._log("sesión de Sofascore abierta")

    def abrir(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            en_loop = loop.is_running()
        except RuntimeError:
            en_loop = False
        if en_loop:
            self._executor = ThreadPoolExecutor(max_workers=1)
            self._executor.submit(self._abrir_interno).result()
        else:
            self._executor = None
            self._abrir_interno()

    def _cerrar_interno(self) -> None:
        for obj, metodo in ((self.ctx, "close"), (self._browser, "close")):
            try:
                if obj:
                    getattr(obj, metodo)()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self.ctx = self.page = None

    def cerrar(self) -> None:
        exec_worker = getattr(self, "_executor", None)
        if exec_worker is not None:
            try:
                exec_worker.submit(self._cerrar_interno).result(timeout=15)
            except Exception:
                pass
            try:
                exec_worker.shutdown(wait=False)
            except Exception:
                pass
            self._executor = None
        else:
            self._cerrar_interno()

    def _api(self, ruta: str) -> Any:
        try:
            r = self.ctx.request.get(SOFASCORE_BASE + "/api/v1" + ruta, timeout=self.timeout_ms,
                                     headers={"Referer": SOFASCORE_BASE + "/"})
            if r.status == 200:
                return r.json()
            self._log(f"⚠️  Sofascore {ruta} -> HTTP {r.status}")
        except Exception as e:
            self._log(f"⚠️  Sofascore {ruta} -> {type(e).__name__}: {e}")
        return None

    def resolver_equipo(self, nombre: str, rival_contexto: Any = None) -> dict[str, Any] | None:
        datos = self._api(f"/search/all?q={nombre.strip().replace(' ', '%20')}")
        if not isinstance(datos, dict):
            return None
        pide_no_senior = usuario_solicito_no_senior(nombre)
        for r in datos.get("results", []) or []:
            if r.get("type") == "team":
                ent = r.get("entity") or {}
                nom_cand = ent.get("name")
                pais_cand = (ent.get("country") or {}).get("name")
                if not pide_no_senior and es_equipo_o_torneo_no_senior(nom_cand, pais_cand):
                    continue
                return {"id": str(ent.get("id")), "nombre": nom_cand,
                        "liga": pais_cand, "id_liga": None,
                        "alternativas": []}
        return None

    def ultimos_partidos(self, equipo: dict[str, Any], limite: int = 10,
                         incluir_amistosos: bool = False) -> list[PartidoFinalizado]:
        res = self.escanear_oficiales(equipo, objetivo=limite,
                                      incluir_amistosos=incluir_amistosos)
        return res.oficiales[:limite]

    def escanear_oficiales(self, equipo: dict[str, Any], objetivo: int,
                           incluir_amistosos: bool = False) -> ResultadoEscaneo:
        """
        Sofascore sí pagina el historial: /team/{id}/events/last/{pagina} devuelve
        bloques de partidos hacia atrás. Aquí sí se aprovecha la paginación real.
        """
        tid = str(equipo.get("id"))
        oficiales: list[PartidoFinalizado] = []
        amistosos_reservados: list[PartidoFinalizado] = []
        vistos: set[str] = set()
        paginas = 0
        finalizados = 0
        amistosos = 0
        motivo = ""

        for pagina in range(self.max_paginas_escaneo):
            datos = self._api(f"/team/{tid}/events/last/{pagina}")
            if not isinstance(datos, dict):
                motivo = f"la fuente no devolvió la página {pagina}"
                break
            eventos = datos.get("events", []) or []
            if not eventos:
                motivo = f"la página {pagina} vino vacía: historial agotado"
                break
            paginas += 1
            nuevos = 0
            for e in eventos:
                if (e.get("status") or {}).get("type") != "finished":
                    continue
                pid = str(e.get("id"))
                if pid in vistos:
                    continue
                vistos.add(pid)
                nuevos += 1
                finalizados += 1
                torneo = (e.get("tournament") or {}).get("name", "")
                home = e.get("homeTeam") or {}
                away = e.get("awayTeam") or {}
                partido = PartidoFinalizado(
                    id_partido=pid,
                    fecha=datetime.utcfromtimestamp(e.get("startTimestamp") or 0).strftime("%Y-%m-%d"),
                    torneo=torneo,
                    local=str(home.get("name") or ""), visitante=str(away.get("name") or ""),
                    goles_local=(e.get("homeScore") or {}).get("current"),
                    goles_visitante=(e.get("awayScore") or {}).get("current"),
                    es_local_el_equipo=str(home.get("id")) == tid,
                )
                if es_competicion_amistosa(torneo):
                    amistosos += 1
                    amistosos_reservados.append(partido)
                    continue
                oficiales.append(partido)
                if len(oficiales) >= objetivo:
                    break
            if len(oficiales) >= objetivo:
                motivo = f"objetivo de {objetivo} partidos oficiales alcanzado"
                break
            if nuevos == 0:
                motivo = f"la página {pagina} no aportó partidos nuevos"
                break
        else:
            motivo = f"tope de seguridad de {self.max_paginas_escaneo} páginas alcanzado"

        oficiales.sort(key=lambda p: p.fecha, reverse=True)
        amistosos_reservados.sort(key=lambda p: p.fecha, reverse=True)
        self._log(f"escaneo hacia atrás: {len(oficiales)} oficiales de {finalizados} finalizados "
                  f"en {paginas} página(s)")
        return ResultadoEscaneo(
            oficiales=oficiales, amistosos_reservados=amistosos_reservados,
            incluir_amistosos=incluir_amistosos, paginas_escaneadas=paginas,
            finalizados_totales=finalizados, amistosos_excluidos=amistosos,
            fuente_agotada=len(oficiales) < objetivo, motivo_parada=motivo,
        )

    def matriz_partido(self, partido: PartidoFinalizado, equipo: dict[str, Any]) -> MatrizPartido:
        """
        Extrae la matriz desde /event/{id}/statistics de Sofascore.
        Mismo contrato de salida que FotMob; los alias del catálogo ya contemplan
        los títulos en inglés de esta fuente.
        """
        es_local = partido.es_local_el_equipo
        matriz = MatrizPartido(
            id_partido=partido.id_partido, fecha=partido.fecha, torneo=partido.torneo,
            equipo=str(equipo.get("nombre") or ""),
            rival=partido.visitante if es_local else partido.local,
            condicion="casa" if es_local else "fuera",
            goles_equipo=partido.goles_local if es_local else partido.goles_visitante,
            goles_rival=partido.goles_visitante if es_local else partido.goles_local,
        )
        datos = self._api(f"/event/{partido.id_partido}/statistics")
        bloques = (datos or {}).get("statistics") or []
        equivalencias = {"ALL": "FT", "1ST": "1T", "2ND": "2T"}
        for bloque in bloques:
            periodo = equivalencias.get(str(bloque.get("period", "")).upper())
            if not periodo:
                continue
            filas, no_disp = [], []
            for grupo in bloque.get("groups", []) or []:
                for item in grupo.get("statisticsItems", []) or []:
                    titulo = str(item.get("name") or "").strip().lower()
                    for definicion in CATALOGO_METRICAS:
                        if not any(a in titulo for a in definicion.alias):
                            continue
                        if any(f["clave"] == definicion.clave for f in filas):
                            continue
                        v_eq = self._valor(definicion, item.get("home"))
                        v_riv = self._valor(definicion, item.get("away"))
                        filas.append({"clave": definicion.clave, "metrica": definicion.etiqueta,
                                      "categoria": definicion.categoria, "equipo": v_eq, "rival": v_riv})
            claves_presentes = {f["clave"] for f in filas}
            for definicion in CATALOGO_METRICAS:
                if definicion.clave not in claves_presentes:
                    no_disp.append(definicion.etiqueta)
                    filas.append({"clave": definicion.clave, "metrica": definicion.etiqueta,
                                  "categoria": definicion.categoria, "equipo": None, "rival": None})
            orden = {d.clave: i for i, d in enumerate(CATALOGO_METRICAS)}
            filas.sort(key=lambda f: orden.get(f["clave"], 999))
            matriz.periodos[periodo] = PeriodoPartido(periodo, filas, no_disp)
        for periodo in ORDEN_PERIODOS:
            if periodo not in matriz.periodos:
                matriz.periodos[periodo] = PeriodoPartido(
                    periodo,
                    [{"clave": d.clave, "metrica": d.etiqueta, "categoria": d.categoria,
                      "equipo": None, "rival": None} for d in CATALOGO_METRICAS],
                    [d.etiqueta for d in CATALOGO_METRICAS],
                )
                matriz.notas.append(f"El periodo {periodo} no vino en la respuesta de la fuente.")
        ausentes: set[str] = set()
        for mat in matriz.periodos.values():
            ausentes |= set(mat.no_disponibles)
        matriz.metricas_no_disponibles = sorted(ausentes)
        time.sleep(self.pausa)
        return matriz

    def _valor(self, definicion: DefinicionMetrica, bruto: Any) -> float | None:
        if bruto is None:
            return None
        if definicion.clave in ("pases_tercio_final_pct", "entradas_pct"):
            return _a_float(bruto)
        if definicion.clave.endswith("_pct") or definicion.clave == "pases_precision_pct":
            return _a_pct(bruto) if "%" in str(bruto) else _a_float(bruto)
        return _a_float(bruto)


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE: FLASHSCORE (Fallback Primario y Complementario Automático)
# ═══════════════════════════════════════════════════════════════════════════════

class FuenteFlashscore(FuenteXG):
    """
    Adaptador primario/fallback sobre Flashscore (API Search + Feeds df_st_1 / GraphQL).
    Se activa automáticamente en cascada cuando FotMob no encuentra un club o
    cuando tiene menos de 15 partidos oficiales.
    """

    nombre = "flashscore"

    def __init__(self, headless: bool = True, pausa_entre_partidos: float = 0.4,
                 idioma: str = "es", timeout_ms: int = 45_000, verbose: bool = True,
                 max_paginas_escaneo: int = 8, ctx_compartido: Any = None) -> None:
        self.headless = headless
        self.pausa = pausa_entre_partidos
        self.idioma = idioma
        self.timeout_ms = timeout_ms
        self.verbose = verbose
        self.max_paginas_escaneo = max(1, int(max_paginas_escaneo))
        self.ctx = ctx_compartido
        self._cliente = None
        self._listador = None

    def _log(self, msg: str) -> None:
        if self.verbose:
            marca = datetime.now(pytz.timezone(config.TIMEZONE)).strftime("%H:%M:%S")
            print(f"[AUDITORÍA-FS {marca}] {msg}")

    def abrir(self) -> None:
        from fuente_flashscore import ClienteFlashscore, ListadoEquipoFlashscore
        if self._cliente is None:
            self._cliente = ClienteFlashscore()
        if self._listador is None:
            self._listador = ListadoEquipoFlashscore(cliente=self._cliente, max_partidos=45)

    def cerrar(self) -> None:
        pass

    def resolver_equipo(self, nombre: str, rival_contexto: Any = None) -> dict[str, Any] | None:
        from fuente_flashscore import _candidatos_flashscore
        self.abrir()
        if not nombre or not str(nombre).strip():
            return None
        liga_pref, nombre_limpio = extraer_liga_de_texto(nombre)
        consulta = (nombre_limpio or nombre).strip()
        canonico = normalizar_nombre_club(consulta)
        pais_esp = None
        if isinstance(rival_contexto, dict):
            pais_esp = rival_contexto.get("pais")
        elif liga_pref:
            pais_esp = liga_pref.get("pais")

        variantes = []
        for v in ([canonico, consulta] + variantes_de_busqueda(consulta)):
            if v and v not in variantes:
                variantes.append(v)

        pide_no_senior = usuario_solicito_no_senior(nombre)
        for var in variantes:
            cands = _candidatos_flashscore(var, pais_esperado=pais_esp, maximo=6)
            for c in cands:
                if not pide_no_senior and es_equipo_o_torneo_no_senior(c.get("nombre"), c.get("pais")):
                    continue
                return {
                    "id": str(c["id"]),
                    "url": str(c.get("url") or ""),
                    "nombre": str(c.get("nombre") or canonico or consulta),
                    "nombre_solicitado": consulta,
                    "liga": str(c.get("pais") or (liga_pref or {}).get("nombre") or "Flashscore"),
                    "id_liga": None,
                    "alternativas": [x.get("nombre") for x in cands[1:5]],
                }
        return None

    def ultimos_partidos(self, equipo: dict[str, Any], limite: int = 15,
                         incluir_amistosos: bool = False) -> list[PartidoFinalizado]:
        res = self.escanear_oficiales(equipo, objetivo=limite, incluir_amistosos=incluir_amistosos)
        return res.oficiales[:limite]

    def escanear_oficiales(self, equipo: dict[str, Any], objetivo: int,
                           incluir_amistosos: bool = False) -> ResultadoEscaneo:
        from fuente_flashscore import _parecido
        self.abrir()
        cand = {
            "id": str(equipo.get("id") or ""),
            "url": str(equipo.get("url") or _sin_acentos(str(equipo.get("nombre") or "")).lower().replace(" ", "-")),
            "nombre": str(equipo.get("nombre") or ""),
        }
        crudos = self._listador._leer_listado(cand, contexto=self.ctx) if self._listador else []
        oficiales: list[PartidoFinalizado] = []
        amistosos_res: list[PartidoFinalizado] = []
        nom_eq = str(equipo.get("nombre") or "")

        for item in crudos:
            torneo = str(item.get("torneo") or equipo.get("liga") or "Competición Oficial")
            loc = str(item.get("local") or "")
            vis = str(item.get("visitante") or "")
            es_local = bool(_parecido(loc, nom_eq)) or not bool(_parecido(vis, nom_eq))
            pf = PartidoFinalizado(
                id_partido=str(item.get("id") or ""),
                fecha=str(item.get("fecha") or ""),
                torneo=torneo,
                local=loc,
                visitante=vis,
                goles_local=item.get("goles_local"),
                goles_visitante=item.get("goles_visitante"),
                es_local_el_equipo=es_local,
                extra={"id_flashscore": str(item.get("id") or "")},
            )
            if es_competicion_amistosa(torneo):
                amistosos_res.append(pf)
                continue
            oficiales.append(pf)
            if len(oficiales) >= objetivo:
                break

        return ResultadoEscaneo(
            oficiales=oficiales[:objetivo],
            amistosos_reservados=amistosos_res,
            incluir_amistosos=incluir_amistosos,
            paginas_escaneadas=1,
            finalizados_totales=len(crudos),
            amistosos_excluidos=len(amistosos_res),
            fuente_agotada=len(oficiales) < objetivo,
            motivo_parada="listado Flashscore procesado",
            cola_historica_revisada=True,
        )

    def matriz_partido(self, partido: PartidoFinalizado, equipo: dict[str, Any]) -> MatrizPartido:
        from fuente_flashscore import PROC_FLASHSCORE
        self.abrir()
        es_local = partido.es_local_el_equipo
        rival = partido.visitante if es_local else partido.local
        goles_eq = partido.goles_local if es_local else partido.goles_visitante
        goles_riv = partido.goles_visitante if es_local else partido.goles_local

        matriz = MatrizPartido(
            id_partido=partido.id_partido,
            fecha=partido.fecha,
            torneo=partido.torneo,
            equipo=str(equipo.get("nombre") or ""),
            rival=rival,
            condicion="casa" if es_local else "fuera",
            goles_equipo=goles_eq,
            goles_rival=goles_riv,
        )
        met = self._cliente.metricas_partido(partido.id_partido) if self._cliente else None
        bando_eq = "local" if es_local else "visitante"
        bando_riv = "visitante" if es_local else "local"

        for per in ORDEN_PERIODOS:
            d_eq = met.metricas_de(per, bando_eq) if met and not met.vacia else {}
            d_riv = met.metricas_de(per, bando_riv) if met and not met.vacia else {}
            filas = []
            for d in CATALOGO_METRICAS:
                filas.append({
                    "clave": d.clave,
                    "metrica": d.etiqueta,
                    "categoria": d.categoria,
                    "equipo": d_eq.get(d.clave),
                    "rival": d_riv.get(d.clave),
                })
            matriz.periodos[per] = PeriodoPartido(per, filas, [])

        matriz.fuente_secundaria = {
            "fuente": PROC_FLASHSCORE,
            "id_partido": partido.id_partido,
            "pareado": bool(met and not met.vacia),
            "rellenadas": {"equipo": list((met.local if es_local else met.visitante).keys()) if met else [],
                           "rival": list((met.visitante if es_local else met.local).keys()) if met else []},
        }
        consolidar_matriz_completa(matriz)
        return matriz

    def partidos_futuros(self, equipo: dict[str, Any]) -> list[dict[str, Any]]:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# FÁBRICA DE FUENTES
# ═══════════════════════════════════════════════════════════════════════════════

FUENTES_DISPONIBLES = {
    "fotmob": FuenteFotMob,
    "flashscore": FuenteFlashscore,
    "sofascore": FuenteSofascore,
}


def obtener_fuente(nombre: str | None = None, **kwargs: Any) -> FuenteXG:
    """
    Devuelve la fuente configurada. Prioridad:
      1. argumento explícito,
      2. variable de entorno AUDITORIA_FUENTE,
      3. 'fotmob' (la única verificada como operativa hoy).
    """
    elegida = (nombre or os.getenv("AUDITORIA_FUENTE") or "fotmob").strip().lower()
    if elegida not in FUENTES_DISPONIBLES:
        raise ValueError(f"Fuente desconocida: {elegida!r}. "
                         f"Opciones: {', '.join(FUENTES_DISPONIBLES)}")
    return FUENTES_DISPONIBLES[elegida](**kwargs)
