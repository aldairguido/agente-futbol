"""
agente_futbol/flashscore.py
───────────────────────────────────────────────────────────────────────────────
Intermediario en tiempo real para Flashscore.

Arquitectura:
  • playwright-stealth  →  evasión de anti-bots
  • Intercepción de tráfico de red (XHR/Fetch + WebSocket)  →  captura de APIs
    internas antes de tocar el DOM visual
  • Todo en memoria RAM  →  ningún archivo propio; los datos son descartados
    tras generar el informe
  • Filtro estricto de amistosos en todas las muestras
  • Ventanas: 10 generales + 5 H2H por defecto (ajustable en config.py)
  • Métricas profundas: xG, posesión, xGOT, errores del portero, etc.
  • Delanteros aislados por posición funcional (izquierda/derecha/central)
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import re
import time
import threading
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import pytz
from playwright.sync_api import (
    BrowserContext,
    Page,
    Response,
    Route,
    TimeoutError as PWTimeout,
)
from playwright_stealth.stealth import Stealth as _Stealth

import config

# Instancia reutilizable de Stealth (configuración por defecto de v2)
_stealth = _Stealth()

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTES Y MAPEOS NORMALIZADOS
# ═══════════════════════════════════════════════════════════════════════════════

_AMISTOSOS = frozenset({
    "friendly", "international friendly", "club friendly",
    "amistoso", "amistosos", "test match", "friendlies",
})

# Diccionario de normalización y alias para torneos de Betsson -> Flashscore
_MAPEO_NOMBRES_EQUIPOS = {
    "athletico pr": "Athletico-PR",
    "chapecoense": "Chapecoense-SC",
    "botafogo": "Botafogo RJ",
    "botafogo fc": "Botafogo-SP",
    "sp ceara": "Ceara",
    "sc america mg": "America MG",
    "criciuma ec": "Criciuma",
    "lanus": "Lanus",
    "velez sarsfield": "Velez Sarsfield",
    "newells old boys": "Newells Old Boys",
    "gimnasia de mendoza": "Gimnasia Mendoza",
    "racing de cordoba": "Racing Cordoba",
    "gv club deportivo san": "San Jose",
    "jose de oruro guabira": "Guabira",
    "universidad de chile nublense": "Universidad de Chile",
    "nublense": "Nublense",
    "agf": "Aarhus",
    "sonderjyske": "Sonderjyske",
    "eyupspor": "Eyupspor",
    "goztepe": "Goztepe",
    "alverca": "Alverca",
    "famalicao": "Famalicao",
}


# Códigos de posición de atacante reconocidos por Flashscore/Sofascore
_POS_ATACANTE = frozenset({
    "ST", "CF", "SS", "FW",           # centros
    "LW", "LF", "IL", "EI",           # extremo izquierdo
    "RW", "RF", "IR", "ED",           # extremo derecho
})

# Patrones de URL que Flashscore usa para sus APIs internas
_PATRON_API_STATS    = re.compile(r"flashscore\.(com|pe|es|co\.uk)/match/[^/]+/statistics", re.I)
_PATRON_API_EVENTOS  = re.compile(r"flashscore\.(com|pe|es|co\.uk)/match/[^/]+/incidents", re.I)
_PATRON_API_LINEUP   = re.compile(r"flashscore\.(com|pe|es|co\.uk)/match/[^/]+/lineups", re.I)
_PATRON_API_RESULTS  = re.compile(r"d\.flashscore\.(com|pe|es|co\.uk)/r/\d+/", re.I)

# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES PRIVADAS
# ═══════════════════════════════════════════════════════════════════════════════

def _es_amistoso(competicion: str) -> bool:
    return any(t in competicion.lower() for t in _AMISTOSOS)


def _ts() -> str:
    """Timestamp ISO en zona Lima."""
    return datetime.now(pytz.timezone(config.TIMEZONE)).isoformat()


def _aplicar_stealth(page: Page) -> None:
    """Aplica playwright-stealth v2 sin forzar text/html en llamadas internas XHR."""
    _stealth.apply_stealth_sync(page)
    page.set_extra_http_headers({
        "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
    })



def _pausa() -> None:
    time.sleep(config.PAUSA_ENTRE_PAGINAS)


def _pausa_larga() -> None:
    time.sleep(config.PAUSA_ENTRE_PARTIDOS)


# ═══════════════════════════════════════════════════════════════════════════════
# INTERCEPTOR DE RED
# ═══════════════════════════════════════════════════════════════════════════════

class _NetCapture:
    """
    Registra las respuestas JSON de las APIs internas de Flashscore
    mientras el navegador carga la página.
    Toda la captura ocurre en RAM; el objeto se descarta al salir del contexto.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stats:   dict[str, Any] = {}   # estadísticas del partido
        self.eventos: list[dict]     = []   # incidentes (goles, tarjetas…)
        self.lineup:  dict[str, Any] = {}   # alineaciones
        self.raw_results: list[str]  = []   # fragmentos de resultados del equipo

    # ── Manejador de respuestas ──────────────────────────────────────────────

    def on_response(self, response: Response) -> None:
        url = response.url
        try:
            if not response.ok:
                return

            ct = response.headers.get("content-type", "")
            if "json" not in ct and "javascript" not in ct:
                # Algunos endpoints de Flashscore devuelven text/plain con JSON
                if "text/plain" not in ct:
                    return

            body = response.body()
            if not body:
                return

            # Intentar decodificar como JSON
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Flashscore usa un formato propio delimitado por "¬" en algunos endpoints
                texto = body.decode("utf-8", errors="ignore")
                if "¬" in texto:
                    with self._lock:
                        self.raw_results.append(texto)
                return

            with self._lock:
                if _PATRON_API_STATS.search(url):
                    self.stats = data
                elif _PATRON_API_EVENTOS.search(url):
                    if isinstance(data, list):
                        self.eventos = data
                    elif isinstance(data, dict):
                        self.eventos = data.get("incidents", data.get("events", []))
                elif _PATRON_API_LINEUP.search(url):
                    self.lineup = data
                elif _PATRON_API_RESULTS.search(url):
                    texto = body.decode("utf-8", errors="ignore")
                    self.raw_results.append(texto)

        except Exception:
            pass  # Las respuestas de recursos no-JSON se ignoran silenciosamente

    def adjuntar(self, page: Page) -> None:
        page.on("response", self.on_response)

    def desacoplar(self, page: Page) -> None:
        page.remove_listener("response", self.on_response)


# ═══════════════════════════════════════════════════════════════════════════════
# PARSEO DE DATOS INTERCEPTADOS
# ═══════════════════════════════════════════════════════════════════════════════

def _normalizar_stats_api(data: dict) -> dict:
    """
    Convierte la respuesta JSON de /statistics en el formato interno:
    { nombre_stat: { local: valor, visitante: valor, fuente: "api" } }
    """
    result: dict[str, Any] = {}
    if not isinstance(data, dict):
        return result

    # Estructura común: { "statistics": [ { "period": "ALL", "groups": [...] } ] }
    for bloque in data.get("statistics", []):
        periodo = bloque.get("period", "ALL")
        for grupo in bloque.get("groups", []):
            for item in grupo.get("statisticsItems", []):
                nombre = item.get("name", "").strip()
                if not nombre:
                    continue
                key = f"{nombre} [{periodo}]" if periodo != "ALL" else nombre
                result[key] = {
                    "local":     item.get("home"),
                    "visitante": item.get("away"),
                    "fuente":    "api_flashscore",
                }
    return result


def _normalizar_eventos_api(eventos: list) -> list[dict]:
    """Convierte los incidentes en una lista de dicts con campos normalizados."""
    out = []
    for ev in eventos:
        if not isinstance(ev, dict):
            continue
        out.append({
            "minuto":     ev.get("time") or ev.get("minute"),
            "tipo":       ev.get("incidentType") or ev.get("type"),
            "equipo":     ev.get("isHome", None),   # True=local, False=visitante
            "jugador":    ev.get("player", {}).get("name") if isinstance(ev.get("player"), dict)
                          else ev.get("playerName"),
            "detalle":    ev.get("incidentClass") or ev.get("class"),
        })
    return out


def _normalizar_lineup_api(data: dict) -> dict[str, list[dict]]:
    """
    Extrae alineaciones de la respuesta JSON de /lineups.
    Devuelve { "local": [...jugadores], "visitante": [...jugadores] }
    """
    result: dict[str, list] = {"local": [], "visitante": []}
    if not isinstance(data, dict):
        return result

    def _parsear_equipo(equipo_data: list | dict, clave: str) -> list[dict]:
        jugadores = []
        if isinstance(equipo_data, dict):
            equipo_data = equipo_data.get("players", []) or equipo_data.get("lineupPlayers", [])
        for j in (equipo_data or []):
            if not isinstance(j, dict):
                continue
            jugadores.append({
                "nombre":   j.get("player", {}).get("name") or j.get("name", "?"),
                "posicion": j.get("position") or j.get("positionName", ""),
                "titular":  j.get("substitute") is False or j.get("type") == "starter",
                "numero":   j.get("jerseyNumber") or j.get("shirtNumber"),
            })
        return jugadores

    result["local"]     = _parsear_equipo(data.get("home", []), "local")
    result["visitante"] = _parsear_equipo(data.get("away", []), "visitante")
    return result


def _parsear_raw_flashscore(texto: str) -> list[dict]:
    """
    Flashscore devuelve en algunos endpoints un formato propio:
    campos separados por '¬' y registros por '~'.
    Extrae una lista de dicts básicos: fecha, local, visitante, resultado, competición.
    """
    partidos = []
    for bloque in texto.split("~"):
        campos = dict(
            p.split("÷", 1) for p in bloque.split("¬") if "÷" in p
        )
        if not campos:
            continue

        competicion = campos.get("ZA", "") or campos.get("Zk", "")
        if _es_amistoso(competicion):
            continue

        partidos.append({
            "id":           campos.get("AA", ""),
            "fecha":        campos.get("AD", ""),
            "local":        campos.get("CL", "") or campos.get("WU", ""),
            "visitante":    campos.get("CT", "") or campos.get("WV", ""),
            "goles_local":  campos.get("BD", ""),
            "goles_visita": campos.get("BE", ""),
            "competicion":  competicion,
            "fuente":       "flashscore_api_raw",
        })

    return partidos


# ═══════════════════════════════════════════════════════════════════════════════
# DOM FALLBACK  (cuando la API no devuelve datos suficientes)
# ═══════════════════════════════════════════════════════════════════════════════

def _dom_stats_tabla(page: Page) -> dict:
    """Extrae tabla de estadísticas visible en el DOM como fallback."""
    stats: dict[str, Any] = {}
    filas = page.query_selector_all(
        "[class*='stat__row'], [class*='statRow'], "
        "[class*='wcl-statistics'], [class*='_row_']"
    )
    for fila in filas:
        try:
            celdas = fila.query_selector_all(
                "[class*='stat__value'], [class*='value'], "
                "[class*='_valueWrapper_'], td"
            )
            if len(celdas) >= 3:
                nombre = celdas[1].inner_text().strip()
                vl = celdas[0].inner_text().strip() or None
                vv = celdas[2].inner_text().strip() or None
                if nombre:
                    stats[nombre] = {
                        "local":     vl,
                        "visitante": vv,
                        "fuente":    "dom_flashscore",
                    }
        except Exception:
            continue
    return stats


def _dom_resultado_cronologia(page: Page) -> dict:
    datos: dict[str, Any] = {}
    try:
        m = page.query_selector(
            ".smv__score, [class*='detailScore'], "
            "[class*='score__score'], [class*='_scoreWrapper_']"
        )
        if m:
            datos["marcador"] = m.inner_text().strip()

        ht = page.query_selector(
            ".smv__halfTime, [class*='halfTime'], [class*='_partScore_']"
        )
        if ht:
            datos["marcador_descanso"] = ht.inner_text().strip()

        eventos = [
            el.inner_text().strip()
            for el in page.query_selector_all(
                "[class*='incident'], [class*='event__incident'], "
                "[class*='_incidentRow_']"
            )
        ]
        datos["eventos_cronologia"] = eventos
    except Exception:
        pass
    return datos


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL: estadísticas de un partido
# ═══════════════════════════════════════════════════════════════════════════════

def obtener_estadisticas_partido(ctx: BrowserContext, url_partido: str,
                                  nombre_equipo: str) -> dict:
    """
    Abre la página del partido con intercepción de red activa.
    Combina datos de API (prioritario) con fallback DOM.
    Todo en memoria; sin escritura de archivos.

    Devuelve dict con:
      completo, primer_tiempo, segundo_tiempo,
      eventos_cronologia, alineaciones, errores_acceso
    """
    salida: dict[str, Any] = {
        "url":            url_partido,
        "equipo":         nombre_equipo,
        "fuente":         "flashscore",
        "fecha_consulta": _ts(),
        "completo":       {},
        "primer_tiempo":  {},
        "segundo_tiempo": {},
        "eventos_cronologia": [],
        "alineaciones":   {"local": [], "visitante": []},
        "errores_acceso": [],
        "_via_api":       False,
    }

    if not url_partido:
        salida["errores_acceso"].append("URL no disponible")
        return salida

    page = ctx.new_page()
    _aplicar_stealth(page)
    cap = _NetCapture()
    cap.adjuntar(page)

    try:
        # Navegar a la página del partido
        page.goto(url_partido, wait_until="domcontentloaded",
                  timeout=config.TIMEOUT_NAVEGACION)
        _pausa()

        # Forzar carga de estadísticas haciendo clic en la pestaña correspondiente
        for label in ("Stats", "Estadísticas", "Statistics"):
            try:
                btn = page.query_selector(
                    f"button:has-text('{label}'), a:has-text('{label}'), "
                    f"[class*='tabs__tab']:has-text('{label}')"
                )
                if btn:
                    btn.click()
                    time.sleep(1.5)
                    break
            except Exception:
                pass

        # Intentar las pestañas de tiempo reglamentario + 1T + 2T
        _clic_pestaña_y_capturar(page, cap, "1er Tiempo",  "primer_tiempo",  salida)
        _clic_pestaña_y_capturar(page, cap, "2do Tiempo",  "segundo_tiempo", salida)
        _clic_pestaña_y_capturar(page, cap, "All",         "completo",       salida)

        # Esperar breve para que lleguen las respuestas asíncronas
        time.sleep(1)

    except Exception as e:
        salida["errores_acceso"].append(f"Carga página: {e}")
    finally:
        cap.desacoplar(page)
        page.close()

    # ── Procesar capturas de red ──────────────────────────────────────────────

    if cap.stats:
        salida["completo"].update(_normalizar_stats_api(cap.stats))
        salida["_via_api"] = True
    else:
        # Fallback DOM (ya capturado antes del close)
        salida["errores_acceso"].append(
            "API stats no interceptada — datos provienen del DOM"
        )

    if cap.eventos:
        salida["eventos_cronologia"] = _normalizar_eventos_api(cap.eventos)
        gl_ht = 0
        gv_ht = 0
        for ev in salida["eventos_cronologia"]:
            tipo = (ev.get("tipo") or "").lower()
            if "goal" in tipo:
                m_str = str(ev.get("minuto") or "99").split("+")[0].replace("'", "").strip()
                if m_str.isdigit() and int(m_str) <= 45:
                    if ev.get("equipo") is True:
                        gl_ht += 1
                    else:
                        gv_ht += 1
        salida["gl_ht"] = gl_ht
        salida["gv_ht"] = gv_ht
        salida["marcador_ht"] = f"({gl_ht}-{gv_ht})"

    if cap.lineup:
        salida["alineaciones"] = _normalizar_lineup_api(cap.lineup)

    # ── Extraer métricas profundas del dict normalizado ───────────────────────

    salida["metricas_profundas"] = _extraer_metricas_profundas(
        salida["completo"]
    )

    return salida


def _clic_pestaña_y_capturar(page: Page, cap: _NetCapture,
                               label: str, clave: str, salida: dict) -> None:
    """Hace clic en una pestaña de periodo y extrae estadísticas DOM como backup."""
    try:
        btn = page.query_selector(
            f"button:has-text('{label}'), "
            f"[class*='tabs__tab']:has-text('{label}'), "
            f"[class*='filter']:has-text('{label}')"
        )
        if btn:
            btn.click()
            time.sleep(1)
            dom = _dom_stats_tabla(page)
            if dom and not salida[clave]:
                salida[clave] = dom
    except Exception:
        pass


def _extraer_metricas_profundas(stats: dict) -> dict:
    """
    Extrae las métricas de alta relevancia del dict de estadísticas normalizado.
    Busca múltiples variantes de nombre porque Flashscore usa distintos labels
    según el idioma y la competición.
    """
    def _buscar(*claves) -> Any:
        for k in claves:
            for sk, sv in stats.items():
                if k.lower() in sk.lower():
                    return sv
        return None

    return {
        "xg_local":             _buscar("Expected Goals", "xG", "xgoals"),
        "xg_visitante":         _buscar("Expected Goals", "xG"),
        "posesion":             _buscar("Ball Possession", "Posesión", "Possession"),
        "remates_local":        _buscar("Total Shots", "Shots", "Tiros"),
        "remates_puerta_local": _buscar("Shots on Target", "On Target", "A puerta"),
        "remates_fuera":        _buscar("Shots off Target", "Off Target"),
        "remates_bloqueados":   _buscar("Blocked Shots", "Bloqueados"),
        "corners":              _buscar("Corner Kicks", "Corners", "Córners"),
        "faltas":               _buscar("Fouls", "Faltas"),
        "tarjetas_amarillas":   _buscar("Yellow Cards", "Amarillas"),
        "tarjetas_rojas":       _buscar("Red Cards", "Rojas"),
        "fueras_juego":         _buscar("Offsides", "Fuera de juego"),
        "pases_total":          _buscar("Passes", "Total Passes", "Pases"),
        "pases_precisos":       _buscar("Accurate Passes", "Pases precisos"),
        "errores_portero":      _buscar("Goalkeeper Saves", "Saves", "Paradas",
                                        "Goalkeeper Errors", "Error que terminó"),
        "grandes_ocasiones":    _buscar("Big Chances", "Ocasiones claras",
                                        "Clear Chances"),
        "xg_guardado":          _buscar("xGOT", "Post-shot xG", "Expected Goals on Target"),
        "toques_area_rival":    _buscar("Touches in Opponent's Box", "Toques área"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BÚSQUEDA DE EQUIPO
# ═══════════════════════════════════════════════════════════════════════════════

def buscar_equipo(ctx: BrowserContext, nombre_equipo: str) -> str | None:
    """
    Busca el equipo en Flashscore y devuelve la URL de su página.
    Combina normalización con la API directa de búsqueda de Livesport (propietaria de Flashscore)
    para resolución instantánea en milisegundos sin bloqueos.
    """
    if not nombre_equipo or nombre_equipo == "?":
        return None

    norm = nombre_equipo.lower().strip()
    norm = _MAPEO_NOMBRES_EQUIPOS.get(norm, norm)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    }

    intentos = [norm]
    palabras = norm.split()
    if len(palabras) > 1:
        intentos.append(" ".join(palabras[:2]))
        intentos.append(palabras[0])
        intentos.append(palabras[-1])

    # Eliminar duplicados preservando orden
    intentos_unicos = []
    for it in intentos:
        if it and it not in intentos_unicos:
            intentos_unicos.append(it)

    for q_str in intentos_unicos:
        q = urllib.parse.quote(q_str)
        url_api = f"https://s.livesport.services/api/v2/search/?lang-id=1&q={q}&project-id=2&project-type-id=1"
        try:
            req = urllib.request.Request(url_api, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                soccer = [
                    d for d in data
                    if d.get("sport", {}).get("name") == "Soccer"
                    and d.get("type", {}).get("name") == "Team"
                    and not any(x in d.get("name", "") for x in ["U19", "U20", "U21", "U23", " W", "Women", "Youth"])
                ]
                if soccer:
                    t = soccer[0]
                    url_fs = f"https://www.flashscore.com/team/{t['url']}/{t['id']}/"
                    print(f"[FLASHSCORE] Equipo '{nombre_equipo}' -> {t.get('name')} ({url_fs})")
                    return url_fs
        except Exception:
            continue

    print(f"[FLASHSCORE] Equipo '{nombre_equipo}' no encontrado")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# MUESTRA GENERAL Y CONDICIÓN (10 y 5 partidos)
# ═══════════════════════════════════════════════════════════════════════════════

def obtener_resultados_equipo(
    ctx:       BrowserContext,
    url_equipo: str,
    limite:    int = None,
    condicion: str | None = None,
    nombre_equipo: str = "",
) -> list[dict]:
    """
    Obtiene los últimos `limite` partidos oficiales del equipo.
    condicion: None = todos | 'casa' = solo como local | 'fuera' = solo como visitante
    Sin amistosos. Extrae goles a favor, en contra y resultado en RAM.
    """
    limite = limite or config.MUESTRA_GENERAL
    partidos: list[dict] = []

    page = ctx.new_page()
    _aplicar_stealth(page)

    try:
        url_res = url_equipo.rstrip("/") + "/results/"
        page.goto(url_res, wait_until="domcontentloaded", timeout=config.TIMEOUT_NAVEGACION)
        try:
            page.wait_for_selector("[class*='event__match']", timeout=8000)
        except Exception:
            time.sleep(2)

        filas = page.query_selector_all("[class*='event__match']")


        for fila in filas:
            p = _dom_parsear_fila(page, fila, nombre_equipo)
            if not p:
                continue
            if not _es_amistoso(p.get("competicion", "")):
                if _aplicar_filtro_condicion(p, condicion):
                    partidos.append(p)
            if len(partidos) >= limite:
                break


    except Exception as e:
        print(f"[FLASHSCORE] ❌ obtener_resultados_equipo: {e}")
    finally:
        page.close()

    cond_txt = f" condición={condicion}" if condicion else " (general)"
    print(f"[FLASHSCORE] {len(partidos)}/{limite} partidos{cond_txt} para {url_equipo.rstrip('/').split('/')[-1]}")
    return partidos[:limite]


def _aplicar_filtro_condicion(partido: dict, condicion: str | None) -> bool:
    if condicion is None:
        return True
    es_local = partido.get("es_local", True)
    if condicion == "casa":
        return bool(es_local)
    if condicion == "fuera":
        return not bool(es_local)
    return True


def obtener_marcador_ht_feed(match_id: str) -> tuple[int | None, int | None]:
    """
    Consulta directa al feed ultraligero de incidentes de Flashscore (df_sui_1_{mid})
    para extraer el marcador exacto del 1er Tiempo (HT) en milisegundos.
    """
    if not match_id:
        return None, None
    url = f"https://global.flashscore.ninja/2/x/feed/df_sui_1_{match_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-fsign": "SW9D1eZo",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            content = resp.read().decode("utf-8")
            for b in content.split("~"):
                if "1st Half" in b:
                    m1 = re.search(r"IG[^\d]*(\d+)", b)
                    m2 = re.search(r"IH[^\d]*(\d+)", b)
                    if m1 and m2:
                        return int(m1.group(1)), int(m2.group(1))
    except Exception:
        pass
    return None, None


def _dom_parsear_fila(page: Page, fila, nombre_equipo: str = "") -> dict | None:
    try:
        texto = fila.inner_text()
        lineas = [l.strip() for l in texto.split("\n") if l.strip()]
        if len(lineas) < 4:
            return None

        competicion = ""
        try:
            competicion = fila.evaluate(
                "el => el.closest('[class*=\"league\"], [class*=\"header\"]')?.innerText?.split('\\n')[0] || ''"
            ).strip()
        except Exception:
            pass

        if _es_amistoso(competicion) or _es_amistoso(texto):
            return None

        enlace = fila.query_selector("a.eventRowLink, a[href*='/match/'], a[href*='/partido/']")
        url_partido = ""
        if enlace:
            href = enlace.get_attribute("href") or ""
            url_partido = config.FLASHSCORE_URL + href if href.startswith("/") else href

        fecha = lineas[0]
        local = lineas[1] if len(lineas) > 1 else ""
        visitante = lineas[2] if len(lineas) > 2 else ""

        numeros = [l for l in lineas[3:] if l.isdigit()]
        if len(numeros) >= 2:
            gl = int(numeros[0])
            gv = int(numeros[1])
        else:
            m = re.search(r"(\d+)\s*[-:]\s*(\d+)", texto)
            if m:
                gl = int(m.group(1))
                gv = int(m.group(2))
            else:
                return None

        # ── Extracción exacta del marcador de 1er Tiempo (HT) ─────────────────
        gl_ht: int | None = None
        gv_ht: int | None = None

        # 1. Búsqueda directa entre paréntesis en el texto (ej. "(3 - 1)", "(1-0)")
        m_ht = re.search(r"\(\s*(\d+)\s*[-:]\s*(\d+)\s*\)", texto)
        if not m_ht:
            m_ht = re.search(r"HT[:\s]+(\d+)\s*[-:]\s*(\d+)", texto, re.I)
        if not m_ht:
            m_ht = re.search(r"1T[:\s]+(\d+)\s*[-:]\s*(\d+)", texto, re.I)

        if m_ht:
            gl_ht = int(m_ht.group(1))
            gv_ht = int(m_ht.group(2))
        else:
            # 2. Búsqueda en sub-elementos de periodo (part, subResult, stage)
            part_el = fila.query_selector("[class*='part'], [class*='subResult'], [class*='stage--block']")
            if part_el:
                pt = part_el.inner_text().strip()
                m_p = re.search(r"(\d+)\s*[-:]\s*(\d+)", pt)
                if m_p:
                    gl_ht = int(m_p.group(1))
                    gv_ht = int(m_p.group(2))

        # 3. Si el marcador final fue 0-0, necesariamente el HT fue 0-0
        if gl_ht is None and gl == 0 and gv == 0:
            gl_ht, gv_ht = 0, 0

        # 4. Fallback vía feed ultraligero de Flashscore si hubo goles y hay match ID
        mid = None
        if url_partido:
            m_mid = re.search(r"mid=([a-zA-Z0-9]+)", url_partido)
            if m_mid:
                mid = m_mid.group(1)
        if not mid:
            fid = fila.get_attribute("id") or ""
            m_fid = re.search(r"g_1_([a-zA-Z0-9]+)", fid)
            if m_fid:
                mid = m_fid.group(1)
        if gl_ht is None and (gl + gv > 0) and mid:
            gl_ht, gv_ht = obtener_marcador_ht_feed(mid)

        es_local = True
        n_clean = nombre_equipo.lower().replace("-", " ").strip()
        if n_clean:
            if n_clean in visitante.lower():
                es_local = False
            elif n_clean in local.lower():
                es_local = True

        gf = gl if es_local else gv
        gc = gv if es_local else gl

        res = "D"
        if gf > gc:
            res = "W"
        elif gf < gc:
            res = "L"

        return {
            "competicion":        competicion or "Oficial",
            "texto_raw":          texto[:200],
            "url_partido":        url_partido,
            # ID de 8 caracteres de Flashscore: es la clave para pedir el feed de
            # estadísticas df_st_1_{id} y rellenar las métricas que FotMob no publica.
            "id_flashscore":      mid,
            "local":              local,
            "visitante":          visitante,
            "es_local":           es_local,
            "goles_local":        gl,
            "goles_visitante":    gv,
            "goles_favor":        gf,
            "goles_contra":       gc,
            "gl_ht":              gl_ht,
            "gv_ht":              gv_ht,
            "goles_ht_local":     gl_ht,
            "goles_ht_visitante": gv_ht,
            "marcador_ht":        f"({gl_ht}-{gv_ht})" if (gl_ht is not None and gv_ht is not None) else None,
            "resultado":          res,
            "fecha":              fecha,
            "fuente":             "flashscore_dom",
            "fecha_consulta":     _ts(),
        }
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# H2H (5 encuentros oficiales)
# ═══════════════════════════════════════════════════════════════════════════════

def buscar_partido_h2h_url(
    ctx: BrowserContext,
    url_equipo_local: str,
    nombre_visitante: str,
) -> str | None:
    """
    Busca en el fixture de Flashscore del equipo local el próximo partido
    contra el equipo visitante y devuelve la URL del partido en Flashscore.
    """
    if not url_equipo_local or not nombre_visitante:
        return None

    page = ctx.new_page()
    _aplicar_stealth(page)
    match_url = None

    try:
        url_fix = url_equipo_local.rstrip("/") + "/fixtures/"
        page.goto(url_fix, wait_until="domcontentloaded", timeout=20000)
        time.sleep(1.5)

        filas = page.query_selector_all("[class*='event__match']")
        v_clean = nombre_visitante.lower()[:4]

        for f in filas:
            t = f.inner_text().lower()
            if v_clean in t:
                a = f.query_selector("a.eventRowLink, a[href*='/match/']")
                if a:
                    match_url = a.get_attribute("href")
                    if match_url and match_url.startswith("/"):
                        match_url = config.FLASHSCORE_URL + match_url
                    break
    except Exception as e:
        print(f"[FLASHSCORE] ⚠️ buscar_partido_h2h_url aviso: {e}")
    finally:
        page.close()

    return match_url


def obtener_h2h(
    ctx: BrowserContext,
    url_partido_objetivo: str,
    nombre_local: str = "",
    nombre_visitante: str = "",
) -> list[dict]:
    """
    Navega a la pestaña H2H del partido objetivo en Flashscore.
    Aísla estrictamente los enfrentamientos mutuos entre ambos equipos sin amistosos.
    """
    h2h: list[dict] = []
    if not url_partido_objetivo:
        return h2h

    page = ctx.new_page()
    _aplicar_stealth(page)

    try:
        clean_url = url_partido_objetivo.split("#")[0]
        page.goto(clean_url, wait_until="domcontentloaded", timeout=config.TIMEOUT_NAVEGACION)
        time.sleep(1.5)

        # Clic explícito en la pestaña H2H
        for tab_sel in ("a:has-text('H2H')", "button:has-text('H2H')", "[href*='h2h']"):
            btn_h2h = page.query_selector(tab_sel)
            if btn_h2h:
                btn_h2h.click()
                time.sleep(1.5)
                break

        filas_h2h = page.query_selector_all("[class*='h2h__row'], [class*='h2h'] [class*='event__match']")

        l_root = nombre_local.lower()[:4] if nombre_local else ""
        v_root = nombre_visitante.lower()[:4] if nombre_visitante else ""

        for f in filas_h2h:
            texto = f.inner_text()
            t_low = texto.lower()

            if _es_amistoso(texto):
                continue

            # Si se proporcionaron los nombres, verificar que sea un duelo mutuo entre ambos
            if l_root and v_root:
                if not (l_root in t_low and v_root in t_low):
                    continue

            lineas = [l.strip() for l in texto.split("\n") if l.strip()]
            if len(lineas) < 4:
                continue

            numeros = [l for l in lineas if l.isdigit()]
            if len(numeros) >= 2:
                gl = int(numeros[-2])
                gv = int(numeros[-1])
            else:
                m = re.search(r"(\d+)\s*[-:]\s*(\d+)", texto)
                if m:
                    gl, gv = int(m.group(1)), int(m.group(2))
                else:
                    gl, gv = 0, 0

            # ── Extracción exacta de marcador de medio tiempo (HT) ────────────
            gl_ht: int | None = None
            gv_ht: int | None = None

            # 1. Búsqueda en texto entre paréntesis (ej. "(3 - 1)", "(1-0)")
            m_ht = re.search(r"\(\s*(\d+)\s*[-:]\s*(\d+)\s*\)", texto)
            if not m_ht:
                m_ht = re.search(r"HT[:\s]+(\d+)\s*[-:]\s*(\d+)", texto, re.I)
            if not m_ht:
                m_ht = re.search(r"1T[:\s]+(\d+)\s*[-:]\s*(\d+)", texto, re.I)

            if m_ht:
                gl_ht = int(m_ht.group(1))
                gv_ht = int(m_ht.group(2))
            elif gl == 0 and gv == 0:
                gl_ht, gv_ht = 0, 0
            else:
                # 2. Fallback vía feed ultraligero Flashscore si hay match ID y hubo goles
                href = f.get_attribute("href") or ""
                mid = None
                if href:
                    m_mid = re.search(r"mid=([a-zA-Z0-9]+)", href)
                    if m_mid:
                        mid = m_mid.group(1)
                if not mid:
                    fid = f.get_attribute("id") or ""
                    m_fid = re.search(r"g_1_([a-zA-Z0-9]+)", fid)
                    if m_fid:
                        mid = m_fid.group(1)
                if mid:
                    gl_ht, gv_ht = obtener_marcador_ht_feed(mid)

            h2h.append({
                "texto_raw":          texto,
                "competicion":        "Oficial",
                "goles_local":        gl,
                "goles_visitante":    gv,
                "gl_ht":              gl_ht,
                "gv_ht":              gv_ht,
                "goles_ht_local":     gl_ht,
                "goles_ht_visitante": gv_ht,
                "marcador_ht":        f"({gl_ht}-{gv_ht})" if (gl_ht is not None and gv_ht is not None) else None,
                "tipo":               "H2H",
                "fuente":             "flashscore_dom",
                "fecha_consulta":     _ts(),
            })

            if len(h2h) >= config.MUESTRA_H2H:
                break

    except Exception as e:
        print(f"[FLASHSCORE] ❌ obtener_h2h: {e}")
    finally:
        page.close()

    print(f"[FLASHSCORE] H2H: {len(h2h)}/{config.MUESTRA_H2H} encontrados (sin amistosos)")
    return h2h


def extraer_h2h_desde_muestras(general_local: list[dict], nombre_visitante: str) -> list[dict]:
    """
    Fallback en RAM: si el fixture no estaba abierto en Flashscore,
    extrae cualquier enfrentamiento previo con el visitante presente en los últimos partidos.
    """
    h2h = []
    v_clean = nombre_visitante.lower()[:4]
    for p in general_local:
        loc = p.get("local", "").lower()
        vis = p.get("visitante", "").lower()
        if v_clean in loc or v_clean in vis:
            h2h.append({
                "texto_raw":          p.get("texto_raw", ""),
                "competicion":        p.get("competicion", "Oficial"),
                "goles_local":        p.get("goles_local", 0),
                "goles_visitante":    p.get("goles_visitante", 0),
                "gl_ht":              p.get("gl_ht"),
                "gv_ht":              p.get("gv_ht"),
                "goles_ht_local":     p.get("gl_ht"),
                "goles_ht_visitante": p.get("gv_ht"),
                "marcador_ht":        p.get("marcador_ht"),
                "tipo":               "H2H",
                "fuente":             "ram_cruce_historial",
                "fecha_consulta":     _ts(),
            })
            if len(h2h) >= config.MUESTRA_H2H:
                break
    return h2h


# ═══════════════════════════════════════════════════════════════════════════════
# DELANTEROS — aislamiento de los 3 principales por equipo
# ═══════════════════════════════════════════════════════════════════════════════

def aislar_delanteros(alineaciones: dict, stats_partido: dict) -> dict[str, list[dict]]:
    """
    A partir de las alineaciones y las estadísticas de un partido ya cargado,
    aísla hasta 3 delanteros por equipo (central, derecha, izquierda).

    Entrada:
      alineaciones  → output de _normalizar_lineup_api
      stats_partido → output de obtener_estadisticas_partido

    Salida:
      { "local": [...], "visitante": [...] }
    Cada jugador incluye:
      nombre, posicion_funcional, titular, remates_totales, remates_a_puerta
    """
    resultado: dict[str, list] = {"local": [], "visitante": []}

    for lado in ("local", "visitante"):
        jugadores_equipo = alineaciones.get(lado, [])
        delanteros_encontrados: list[dict] = []

        for j in jugadores_equipo:
            pos = (j.get("posicion") or "").upper().strip()
            if pos in _POS_ATACANTE:
                delanteros_encontrados.append({
                    "nombre":             j["nombre"],
                    "posicion_raw":       pos,
                    "posicion_funcional": _clasificar_pos_funcional(pos),
                    "titular":            j.get("titular"),
                    "numero":             j.get("numero"),
                    # Los remates se cruzan desde stats individuales si están en la API
                    "remates_totales":    None,
                    "remates_a_puerta":   None,
                    "fuente_remates":     "pendiente",
                })

        # Prioridad: titulares antes que suplentes; máximo 3
        titulares  = [d for d in delanteros_encontrados if d.get("titular") is True]
        suplentes  = [d for d in delanteros_encontrados if d.get("titular") is not True]
        seleccion  = (titulares + suplentes)[:3]

        resultado[lado] = seleccion

    return resultado


def _clasificar_pos_funcional(pos: str) -> str:
    if pos in ("LW", "LF", "IL", "EI"):
        return "izquierda"
    if pos in ("RW", "RF", "IR", "ED"):
        return "derecha"
    if pos in ("ST", "CF", "SS", "FW"):
        return "central"
    return "sin_clasificar"


def obtener_delanteros_equipo(
    ctx: BrowserContext,
    url_equipo: str,
    nombre_equipo: str = "",
) -> list[dict]:
    """
    Navega a la sección de plantilla (/squad/) del equipo en Flashscore
    y extrae estadísticas reales de los delanteros y atacantes principales:
    nombre, dorsal, partidos jugados, minutos, goles, asistencias y remates estimados.
    """
    if not url_equipo:
        return []

    url_squad = url_equipo.rstrip("/") + "/squad/"
    delanteros: list[dict] = []

    page = ctx.new_page()
    _aplicar_stealth(page)

    try:
        page.goto(url_squad, wait_until="domcontentloaded", timeout=config.TIMEOUT_NAVEGACION)
        try:
            page.wait_for_selector("div.lineupTable", timeout=6000)
        except Exception:
            time.sleep(1.5)

        jugadores_map: dict[str, dict] = {}
        medios_goleadores: dict[str, dict] = {}

        tables = page.query_selector_all("div.lineupTable")
        for t in tables:
            title_el = t.query_selector(".lineupTable__title")
            title = (title_el.inner_text().strip() if title_el else "").lower()

            es_delanteros = any(w in title for w in ["forward", "delanter", "attacker", "angreifer", "attaquant"])
            es_medios = any(w in title for w in ["midfield", "centrocamp", "volante", "mittelfeld", "milieu"])

            if not (es_delanteros or es_medios):
                continue

            rows = t.query_selector_all(".lineupTable__row")
            for r in rows:
                name_el = r.query_selector(".lineupTable__cell--player")
                if not name_el:
                    continue
                nombre_raw = name_el.inner_text().strip()
                nombre = nombre_raw.split("\n")[0].strip()
                if not nombre:
                    continue

                jersey_el = r.query_selector(".lineupTable__cell--jersey")
                jersey = jersey_el.inner_text().strip() if jersey_el else ""

                mp_el = r.query_selector(".lineupTable__cell--matchesPlayed")
                mp_str = mp_el.inner_text().strip() if mp_el else "0"
                partidos = int(mp_str) if mp_str.isdigit() else 0

                min_el = r.query_selector(".lineupTable__cell--minutesPlayed")
                min_str = min_el.inner_text().strip() if min_el else "0"
                minutos = int(min_str) if min_str.isdigit() else 0

                goal_el = r.query_selector(".lineupTable__cell--goal")
                goal_str = goal_el.inner_text().strip() if goal_el else "0"
                goles = int(goal_str) if goal_str.isdigit() else 0

                ast_el = r.query_selector(".lineupTable__cell--assist")
                ast_str = ast_el.inner_text().strip() if ast_el else "0"
                asistencias = int(ast_str) if ast_str.isdigit() else 0

                target_dict = jugadores_map if es_delanteros else medios_goleadores

                # Guardar el registro con mayor cantidad de partidos o goles (temporada consolidada)
                if nombre not in target_dict or (partidos > target_dict[nombre]["partidos"] or goles > target_dict[nombre]["goles"]):
                    p_ref = max(1, partidos)
                    if goles > 0:
                        sot = round(max(1.0, (goles * 1.6) / p_ref), 1)
                        rem_tot = round(max(1.8, (goles * 2.9) / p_ref), 1)
                    elif minutos > 180:
                        sot = 0.5
                        rem_tot = 1.3
                    else:
                        sot = 0.2
                        rem_tot = 0.6

                    target_dict[nombre] = {
                        "nombre": nombre,
                        "numero": jersey,
                        "posicion": "FW" if es_delanteros else "MF",
                        "rol_ofensivo": "Delantero" if es_delanteros else "Extremo/Volante Ofensivo",
                        "titular": (partidos >= 3 or minutos >= 150),
                        "partidos": partidos,
                        "minutos": minutos,
                        "goles": goles,
                        "asistencias": asistencias,
                        "remates_totales": rem_tot,
                        "remates_a_puerta": sot,
                        "fuente": "flashscore_squad",
                    }

        # Ordenar delanteros por goles descendente y luego minutos
        atacantes = list(jugadores_map.values())
        atacantes.sort(key=lambda x: (x["goles"], x["minutos"], x["partidos"]), reverse=True)

        # Si hay menos de 3 delanteros, complementar con mediocampistas con gol
        if len(atacantes) < 3:
            medios_con_gol = [m for m in medios_goleadores.values() if m["goles"] > 0]
            medios_con_gol.sort(key=lambda x: (x["goles"], x["minutos"]), reverse=True)
            for m in medios_con_gol:
                if m["nombre"] not in [a["nombre"] for a in atacantes]:
                    atacantes.append(m)
                if len(atacantes) >= 3:
                    break

        delanteros = atacantes[:3]

    except Exception as e:
        print(f"[FLASHSCORE] ⚠️ obtener_delanteros_equipo ({nombre_equipo}): {e}")
    finally:
        page.close()

    if delanteros:
        nombres_resumen = ", ".join(f"{d['nombre']} ({d['goles']}G/{d['partidos']}p)" for d in delanteros)
        print(f"[FLASHSCORE] Atacantes {nombre_equipo or url_equipo}: {len(delanteros)} identificados [{nombres_resumen}]")
    else:
        print(f"[FLASHSCORE] ⚠️ Atacantes {nombre_equipo or url_equipo}: no se pudieron extraer de squad")

    return delanteros
