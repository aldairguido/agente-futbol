"""
agente_futbol/betsson.py
───────────────────────────────────────────────────────────────────────────────
Intermediario en tiempo real para Betsson Perú (selectores verificados oct-2026).

Arquitectura confirmada por inspección real:
  • SPA Angular — datos llegan por API, no en HTML inicial
  • Elemento de partido: <obg-event-row-market-container market-id="m-f-{eventId}-{mktCode}">
  • eventId extraído del atributo market-id → m-f-{eventId}-XXXXX
  • Slug del partido en API /widgets/view/v1 → metaData[].slug
  • Cuotas en: <span test-id="odds"> dentro de obg-selection-container
  • Equipos en: <span class="obg-selection-label">
  • Login: botón inyectado dinámicamente como <a> "Iniciar sesión"
  • Tab "Mañana" activada con click para filtrar por fecha
"""

from __future__ import annotations

import json
import os
import re
import time
import threading
from datetime import datetime, timedelta
from typing import Any

import pytz
from playwright.sync_api import Page, Response, TimeoutError as PWTimeout
from dotenv import load_dotenv

import config

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTES (verificadas por diagnóstico oct-2026)
# ═══════════════════════════════════════════════════════════════════════════════

# Selectores DOM verificados
_SEL_EVENT_ROW   = "obg-event-row-market-container[test-id='event-market-row']"
_SEL_ODDS        = "span[test-id='odds']"
_SEL_TEAM_LABEL  = "span.obg-selection-label"
_SEL_MKT_LABEL   = "span[test-id='event-row.market-header.label']"

# Patrones para APIs internas
_RE_VIEW  = re.compile(r"betsson\.pe/api/sb/v\d+/widgets/view/v\d+", re.I)
_RE_MKT   = re.compile(r"betsson\.pe/api/sb/v\d+/widgets/event-market/v\d+", re.I)
_RE_EVT   = re.compile(r"betsson\.pe/api/sb/v\d+/events/fixtures/v\d+", re.I)

# Extrae eventId del atributo market-id: "m-f-{eventId}-{code}"
_RE_EID   = re.compile(r"^m-f-([^-].+?)-[A-Z0-9]+(?:-[\d.]+)?$")

# Palabras a excluir al detectar nombres de equipos (insensible a mayúsculas)
_EXCLUIR_NOMBRES = {
    "ganador del partido", "ganador", "resultado final",
    "empate", "draw", "x", "1", "2", "si", "no",
    "más de", "menos de", "over", "under",
    "1er tiempo", "2do tiempo", "ambos", "primer gol",
}

_MERCADOS_INTERES = {
    "MW3W": "Ganador del Partido (1X2)",
    "FTCSR": "Marcador Exacto",
    "AWEH": "Ambos Equipos Anotan",
    "DC": "Doble Oportunidad",
    "BTTS": "Ambos Equipos Anotan",
    "MWOU": "Total de Goles",
    "1HTG": "1er Tiempo - Total de Goles",
}

def _ts() -> str:
    return datetime.now(pytz.timezone(config.TIMEZONE)).isoformat()

def _fecha_hoy_lima() -> str:
    """Retorna la fecha de hoy Lima en formato YYYY-MM-DD."""
    lima = pytz.timezone(config.TIMEZONE)
    return datetime.now(lima).strftime("%Y-%m-%d")

def _fecha_manana_utc() -> str:
    """Retorna la fecha de mañana Lima en formato UTC (para comparar con API de Betsson)."""
    lima = pytz.timezone(config.TIMEZONE)
    manana_lima = datetime.now(lima) + timedelta(days=1)
    return manana_lima.strftime("%Y-%m-%d")


# Encabezados de sesión capturados de Betsson para llamadas API directas
_session_headers: dict[str, str] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# FILTRO DE EXCLUSIÓN ESTRICTO PARA eFOOTBALL, VIDEOJUEGOS Y SIMULACIONES
# ═══════════════════════════════════════════════════════════════════════════════

# Términos explícitos de exclusión (videojuegos, simulaciones y fútbol virtual)
_TERMINOS_EXCLUSION_VIRTUAL = [
    # Términos explícitos solicitados por el usuario
    "volta",
    "efootball",
    "e-football",
    "battling",
    "valkyrie",
    "valkyries",
    "adratic",
    "adriatic",
    "minute",
    "minutes",
    "6 min de juego",
    "agent",
    "storm",
    "dusk",
    "stasyan",
    "groma",
    # Variaciones de duración de juego en videojuegos / esports
    "min de juego",
    "mins de juego",
    "minutos de juego",
    # Ligas virtuales, simulaciones y esports
    "battle",
    "cyber",
    "esports",
    "e-sports",
    "esport",
    "virtual",
    "simulated reality",
    "srl",
    "gt league",
    "gt sports",
    "liga pro (",
    "h2h liga pro",
    "ea sports fc",
    "ea fc",
    "fifa 2",
    "fifa 1",
    "fifa ",
    "electronic league",
    "e-league",
    "eleague",
]

_REGEX_VIRTUAL_DURACION = re.compile(r"\b\d+\s*min(?:s|utos)?\s*de\s*juego\b", re.I)
_REGEX_VIRTUAL_MINUTOS = re.compile(r"\b(?:2x3|2x4|2x5|2x6|2x7|2x8|2x10|\d+)\s*(?:min|mins|minute|minutes)\b", re.I)
_REGEX_ESPORTS_TAGS = re.compile(r"\((?:agent|storm|dusk|stasyan|groma|volta|valkyrie|battling|adratic|adriatic)\)", re.I)
_REGEX_LEAGUE_VIRTUAL = re.compile(
    r"\b(?:efootball|volta|battling|valkyrie|adratic|adriatic|cyber|gt|srl|battle|electronic|esport|esports|virtual)\s+league\b",
    re.I
)


def es_evento_virtual(evento: Any) -> bool:
    """
    Examina de forma estricta los metadatos o el título del evento (local, visitante,
    liga, slug, título) y determina si corresponde a eFootball, videojuegos o fútbol virtual.

    Términos filtrados:
      - 'Volta', 'eFootball', '6 min de juego', 'Battling', 'Minute', 'Valkyrie', 'Adratic'
      - Nombres de jugadores esports: 'Agent', 'Storm', 'Dusk', 'Stasyan', 'Groma'
      - Duraciones de partido sintéticas (ej. '8 min de juego', '2x5 min', '8 minute')
      - Categorías Cyber, SRL (Simulated Reality League), FIFA, GT League, Battling League, etc.

    Retorna True si el evento debe ser saltado/descartado automáticamente.
    """
    if not evento:
        return False

    if isinstance(evento, dict):
        textos = [
            str(evento.get("local", "")),
            str(evento.get("visitante", "")),
            str(evento.get("liga", "")),
            str(evento.get("slug", "")),
            str(evento.get("partido", "")),
            str(evento.get("titulo", "")),
            str(evento.get("nombre", "")),
        ]
        cadena_total = " ".join(textos).lower()
    else:
        cadena_total = str(evento).lower()

    if not cadena_total.strip():
        return False

    # Excepción para el club profesional real brasileño "Volta Redonda"
    # Solo si no está acompañado de otros tags de simulación o jugadores virtuales
    es_volta_redonda = "volta redonda" in cadena_total

    # 1. Duración típica de videojuegos / esports (ej. "6 min de juego", "2x5 min", "8 minute")
    if _REGEX_VIRTUAL_DURACION.search(cadena_total) or _REGEX_VIRTUAL_MINUTOS.search(cadena_total):
        return True

    # 2. Tags de jugadores esports entre paréntesis: (Agent), (Storm), (Dusk), (Valkyrie), etc.
    if _REGEX_ESPORTS_TAGS.search(cadena_total):
        return True

    # 3. Ligas virtuales compuestas: eFootball League, Battling League, GT League, etc.
    if _REGEX_LEAGUE_VIRTUAL.search(cadena_total):
        return True

    # 4. Comprobación de palabras clave
    for term in _TERMINOS_EXCLUSION_VIRTUAL:
        if term == "volta" and es_volta_redonda:
            continue

        # Para apodos cortos o palabras clave, usar límite de palabra para evitar falsos positivos
        if term in ("agent", "storm", "dusk", "groma", "volta", "battle", "battling", "valkyrie", "valkyries", "adratic", "adriatic", "minute", "minutes"):
            if re.search(r"\b" + re.escape(term) + r"\b", cadena_total):
                return True
        else:
            if term in cadena_total:
                return True

    return False


# ═══════════════════════════════════════════════════════════════════════════════
# INTERCEPTOR DE RED
# ═══════════════════════════════════════════════════════════════════════════════

class _BetssonCapture:
    def __init__(self) -> None:
        self._lock     = threading.Lock()
        self.view_data: list[dict] = []
        self.mkt_data:  list[dict] = []

    def on_request(self, req) -> None:
        global _session_headers
        if "/api/sb/" in req.url and not _session_headers:
            with self._lock:
                _session_headers.update(dict(req.headers))

    def on_response(self, resp: Response) -> None:
        url = resp.url
        try:
            if not resp.ok:
                return
            ct = resp.headers.get("content-type", "")
            if "json" not in ct:
                return
            body = resp.body()
            if not body:
                return
            data = json.loads(body)
        except Exception:
            return

        with self._lock:
            if _RE_VIEW.search(url):
                self.view_data.append(data)
            elif _RE_MKT.search(url):
                self.mkt_data.append(data)

    def adjuntar(self, page: Page) -> None:
        page.on("request", self.on_request)
        page.on("response", self.on_response)

    def desacoplar(self, page: Page) -> None:
        page.remove_listener("request", self.on_request)
        page.remove_listener("response", self.on_response)


# ═══════════════════════════════════════════════════════════════════════════════
# LOGIN
# ═══════════════════════════════════════════════════════════════════════════════

def login_betsson(page: Page) -> bool:
    email    = os.getenv("BETSSON_EMAIL", "")
    password = os.getenv("BETSSON_PASSWORD", "")

    if not email or not password:
        print("[BETSSON] Sin credenciales — continuando sin sesion")
        return False

    try:
        page.goto(
            "https://www.betsson.pe/es/apuestas-deportivas/futbol",
            wait_until="networkidle", timeout=40000,
        )
        time.sleep(2)

        # El botón aparece como <a> con texto "Iniciar sesión" (verificado)
        for sel in (
            "a:has-text('Iniciar sesión')",
            "button:has-text('Iniciar sesión')",
            "button:has-text('Ingresar')",
            "[data-testid*='login']",
        ):
            try:
                page.wait_for_selector(sel, timeout=8000)
                page.click(sel)
                print(f"[BETSSON] Boton login: {sel}")
                break
            except Exception:
                continue
        else:
            print("[BETSSON] Boton login no encontrado — sin sesion")
            return False

        time.sleep(1.5)

        # Rellenar formulario del modal
        for sel in ("input[type='email']", "input[name='username']",
                    "input[autocomplete='username']", "input[placeholder*='correo' i]"):
            try:
                page.wait_for_selector(sel, timeout=4000)
                page.fill(sel, email)
                break
            except Exception:
                continue

        for sel in ("input[type='password']", "input[name='password']"):
            try:
                page.fill(sel, password)
                break
            except Exception:
                continue

        for sel in ("button[type='submit']", "button:has-text('Entrar')",
                    "button:has-text('Iniciar')"):
            try:
                page.click(sel, timeout=4000)
                break
            except Exception:
                continue

        time.sleep(3)

        # Verificar login
        try:
            page.wait_for_selector(
                "[class*='balance'], [class*='wallet'], [class*='account']",
                timeout=8000,
            )
            print("[BETSSON] Login exitoso")
            return True
        except Exception:
            print("[BETSSON] Login no confirmado — continuando")
            return False

    except Exception as e:
        print(f"[BETSSON] Error login: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# OBTENER PARTIDOS DE HOY / MAÑANA
# ═══════════════════════════════════════════════════════════════════════════════

def obtener_partidos_hoy(page: Page) -> list[dict]:
    """
    Extrae los partidos de fútbol que se juegan HOY en Betsson (horario Lima).
    Aplica filtro por pestaña 'Hoy' y validación de tiempo para procesar únicamente
    partidos que aún no han comenzado.
    """
    fecha_hoy = _fecha_hoy_lima()
    url = getattr(config, "BETSSON_URL", "https://www.betsson.pe/es/apuestas-deportivas/futbol")
    print(f"[BETSSON] 🎯 Buscando partidos programados para HOY: {fecha_hoy}")
    print(f"[BETSSON] 🌐 Navegando a la cartelera: {url}")

    cap = _BetssonCapture()
    cap.adjuntar(page)
    partidos: list[dict] = []

    try:
        page.goto(
            url,
            wait_until="networkidle", timeout=40000,
        )
        time.sleep(3)

        # Clic en tab Hoy
        for label in ("Hoy", "Today"):
            try:
                btn = page.query_selector(
                    f"button:has-text('{label}'), "
                    f"[class*='tab']:has-text('{label}'), "
                    f"[class*='filter']:has-text('{label}')"
                )
                if btn:
                    btn.click()
                    time.sleep(2.5)
                    print(f"[BETSSON] Tab '{label}' activada para partidos de HOY")
                    break
            except Exception:
                pass

        # Scroll exhaustivo para cargar todas las categorías y ligas
        for _ in range(16):
            page.evaluate("window.scrollBy(0, 800)")
            time.sleep(0.6)
        time.sleep(2)

        # ── Estrategia 1: API (metaData de view/v1) filtrado por fecha_hoy ───
        partidos_api = _parsear_view_metadata(cap.view_data, fecha_hoy)
        if partidos_api:
            partidos = partidos_api
            print(f"[BETSSON] {len(partidos)} partidos de HOY extraídos via API metaData")

        # ── Estrategia 2: DOM (market-id attribute) ───────────────────────────
        if not partidos:
            partidos = _parsear_dom_market_id(page)
            print(f"[BETSSON] {len(partidos)} partidos via DOM market-id")

    except Exception as e:
        print(f"[BETSSON] Error obtener_partidos_hoy: {e}")
    finally:
        cap.desacoplar(page)

    # Deduplicar por eventId
    vistos: set[str] = set()
    unicos: list[dict] = []
    for p in partidos:
        if es_evento_virtual(p):
            continue
        eid = p.get("id_evento", "")
        key = eid or f"{p.get('local','')}|{p.get('visitante','')}"
        if key and key not in vistos:
            vistos.add(key)
            unicos.append(p)

    print(f"[BETSSON] Total partidos únicos para HOY (fútbol real pendiente): {len(unicos)}")
    return unicos


def obtener_partidos(page: Page, fecha_objetivo: str = "hoy") -> list[dict]:
    """
    Función unificada para extraer partidos de Betsson según la fecha objetivo:
    'hoy' (por defecto) o 'manana'.
    """
    if str(fecha_objetivo).lower().strip() in ("hoy", "today"):
        return obtener_partidos_hoy(page)
    else:
        return obtener_partidos_manana(page)


def obtener_partidos_manana(page: Page) -> list[dict]:
    """
    Extrae los partidos de fútbol de mañana en Betsson.

    Estrategia 1: metaData de /widgets/view/v1 → eventId + slug + startDate
    Estrategia 2: DOM — market-id del elemento obg-event-row-market-container
    """
    fecha_manana = _fecha_manana_utc()
    url = getattr(config, "BETSSON_URL", "https://www.betsson.pe/es/apuestas-deportivas/futbol")
    print(f"[BETSSON] Buscando partidos para: {fecha_manana}")
    print(f"[BETSSON] 🌐 Navegando a la cartelera: {url}")

    cap = _BetssonCapture()
    cap.adjuntar(page)
    partidos: list[dict] = []

    try:
        page.goto(
            url,
            wait_until="networkidle", timeout=40000,
        )
        time.sleep(3)

        # Clic en tab Mañana
        for label in ("Mañana", "Tomorrow"):
            try:
                btn = page.query_selector(
                    f"button:has-text('{label}'), "
                    f"[class*='tab']:has-text('{label}'), "
                    f"[class*='filter']:has-text('{label}')"
                )
                if btn:
                    btn.click()
                    time.sleep(2.5)
                    print(f"[BETSSON] Tab '{label}' activada")
                    break
            except Exception:
                pass

        # Scroll exhaustivo para cargar todas las categorías y ligas
        for _ in range(16):
            page.evaluate("window.scrollBy(0, 800)")
            time.sleep(0.6)
        time.sleep(2)

        # ── Estrategia 1: API (metaData de view/v1) ───────────────────────────
        partidos_api = _parsear_view_metadata(cap.view_data, fecha_manana)
        if partidos_api:
            partidos = partidos_api
            print(f"[BETSSON] {len(partidos)} partidos via API metaData")

        # ── Estrategia 2: DOM (market-id attribute) ───────────────────────────
        if not partidos:
            partidos = _parsear_dom_market_id(page)
            print(f"[BETSSON] {len(partidos)} partidos via DOM market-id")

    except Exception as e:
        print(f"[BETSSON] Error obtener_partidos_manana: {e}")
    finally:
        cap.desacoplar(page)

    # Deduplicar por eventId
    vistos: set[str] = set()
    unicos: list[dict] = []
    for p in partidos:
        if es_evento_virtual(p):
            continue
        eid = p.get("id_evento", "")
        key = eid or f"{p.get('local','')}|{p.get('visitante','')}"
        if key and key not in vistos:
            vistos.add(key)
            unicos.append(p)

    print(f"[BETSSON] Total unicos extraidos (futbol real): {len(unicos)}")
    return unicos


def _parsear_view_metadata(view_data: list[dict], fecha_filtro: str) -> list[dict]:
    """
    Parsea /widgets/view/v1: busca items con metaData que contengan
    eventId y slug, filtrando dinámicamente por la fecha especificada en horario Lima.
    Si la fecha corresponde a HOY, aplica un filtro estricto de tiempo para procesar
    exclusivamente partidos que aún no hayan empezado.
    """
    partidos: list[dict] = []
    tz_lima = pytz.timezone(config.TIMEZONE)
    ahora_lima = datetime.now(tz_lima)
    es_hoy = (fecha_filtro == _fecha_hoy_lima())

    for bloque in view_data:
        widgets = bloque.get("data", {}).get("widgets", [])
        for w in widgets:
            items = w.get("data", {}).get("data", {}).get("items", [])
            for item in items:
                # Extraer timestamp general del item si existe como fallback
                item_fecha_lima = None
                item_ts = item.get("timestamp")
                if item_ts:
                    try:
                        dt_item = datetime.fromtimestamp(
                            item_ts / 1000 if item_ts > 1e10 else item_ts,
                            tz=tz_lima
                        )
                        item_fecha_lima = dt_item.strftime("%Y-%m-%d")
                    except Exception:
                        pass

                for meta in item.get("metaData", []):
                    eid  = str(meta.get("eventId", ""))
                    slug = meta.get("slug", "")   # ej: "futbol/peru/peru-liga-1/sport-boys-alianza-atletico"
                    start= meta.get("startDate", "")
                    comp = meta.get("competitionName", "") or (slug.split("/")[-2].replace("-", " ").title() if "/" in slug else "")

                    if not eid or not slug:
                        continue

                    # Extraer hora y verificar si corresponde a la fecha objetivo en Lima
                    hora = ""
                    es_fecha_correcta = False
                    if start:
                        try:
                            clean_start = start.replace("Z", "+00:00")
                            dt_event = datetime.fromisoformat(clean_start)
                            if dt_event.tzinfo is None:
                                dt_event = dt_event.replace(tzinfo=pytz.utc)
                            dt_lima = dt_event.astimezone(tz_lima)
                            hora = dt_lima.strftime("%H:%M")
                            if dt_lima.strftime("%Y-%m-%d") == fecha_filtro:
                                # Filtro de tiempo para HOY: solo partidos futuros o por comenzar
                                if es_hoy and dt_lima < ahora_lima - timedelta(minutes=5):
                                    es_fecha_correcta = False
                                else:
                                    es_fecha_correcta = True
                        except Exception:
                            pass

                    if not start and item_fecha_lima == fecha_filtro:
                        es_fecha_correcta = True

                    # Si el evento no es de la fecha o ya finalizó/comenzó, descartarlo
                    if not es_fecha_correcta:
                        continue

                    # Extraer nombres de equipos (slug o participantes directos si existen)
                    local, visitante = "", ""
                    if meta.get("homeParticipant") and meta.get("awayParticipant"):
                        local = meta["homeParticipant"].get("name", "")
                        visitante = meta["awayParticipant"].get("name", "")

                    if not local or not visitante:
                        partes_slug = slug.split("/")
                        partido_slug = partes_slug[-1] if partes_slug else ""
                        l_slug, v_slug = _equipos_desde_slug(partido_slug)
                        local = local or l_slug
                        visitante = visitante or v_slug

                    # Construir URL del evento garantizando que url siempre esté definida
                    url_base = getattr(config, "BETSSON_URL", "https://www.betsson.pe/es/apuestas-deportivas/futbol")
                    if slug:
                        url = f"https://www.betsson.pe/es/apuestas-deportivas/{slug.lstrip('/')}"
                    elif eid:
                        url = f"{url_base}?tab=liveAndUpcoming&eventId={eid}"
                    else:
                        url = url_base

                    partido_dict = {
                        "id_evento":      eid,
                        "local":          local,
                        "visitante":      visitante,
                        "liga":           comp,
                        "hora":           hora,
                        "url":            url,
                        "slug":           slug,
                        "fecha_consulta": _ts(),
                    }

                    # Exclusión estricta de eFootball, videojuegos y simulaciones virtuales
                    if es_evento_virtual(partido_dict):
                        continue

                    partidos.append(partido_dict)

    return partidos


def _equipos_desde_slug(partido_slug: str) -> tuple[str, str]:
    """
    Dado "sport-boys-alianza-atletico" intenta separar en dos equipos.
    Busca el punto de corte comparando con nombres conocidos o usando heurística.
    """
    partes = partido_slug.split("-")
    if len(partes) < 2:
        return partido_slug.title(), "?"

    # Heurística: probar todos los puntos de corte y tomar el más balanceado
    mejor = (1, len(partes) - 1)  # (idx_corte, diferencia)
    min_dif = abs(1 - (len(partes) - 1))

    for i in range(1, len(partes)):
        dif = abs(i - (len(partes) - i))
        if dif < min_dif:
            min_dif = dif
            mejor = (i, len(partes) - i)

    corte = mejor[0]
    local     = " ".join(p.title() for p in partes[:corte])
    visitante = " ".join(p.title() for p in partes[corte:])
    return local, visitante


def _parsear_dom_market_id(page: Page) -> list[dict]:
    """
    Fallback DOM: extrae eventId del atributo market-id de los elementos
    obg-event-row-market-container. Formato: m-f-{eventId}-{marketCode}
    También extrae equipos de obg-selection-label y cuotas de span[test-id='odds'].
    """
    partidos: list[dict] = []
    vistos_eid: set[str] = set()

    rows = page.query_selector_all(_SEL_EVENT_ROW)
    for row in rows:
        try:
            market_id = row.get_attribute("market-id") or ""
            # Extraer eventId: m-f-{eventId}-{CODE}
            m = re.match(r"^m-f-(.+?)-[A-Z0-9]+(?:-[\d.]+)?$", market_id)
            if not m:
                continue
            eid = "f-" + m.group(1) if not m.group(1).startswith("f-") else m.group(1)

            # Solo el primer mercado por evento (evita duplicados por mercado)
            # Reconstruir eventId limpio
            eid_clean = market_id.split("-MW")[0].split("-FTCSR")[0] \
                                 .split("-MWOU")[0].split("-AWEH")[0]
            # Extraer parte f-XXXXX
            pm = re.search(r"(f-[A-Za-z0-9_]+)", market_id)
            if not pm:
                continue
            eid = pm.group(1)

            if eid in vistos_eid:
                continue
            vistos_eid.add(eid)

            # Equipos de las etiquetas de selección
            labels = row.query_selector_all(_SEL_TEAM_LABEL)
            nombres = []
            for lbl in labels:
                txt = lbl.inner_text().strip()
                if txt and txt.lower() not in _EXCLUIR_NOMBRES:
                    nombres.append(txt)

            local     = nombres[0] if len(nombres) > 0 else "?"
            visitante = nombres[1] if len(nombres) > 1 else "?"

            # Cuotas
            odds_els = row.query_selector_all(_SEL_ODDS)
            odds = [el.inner_text().strip() for el in odds_els if el.inner_text().strip()]

            url = (
                f"https://www.betsson.pe/es/apuestas-deportivas/futbol"
                f"?tab=liveAndUpcoming&eventId={eid}"
            )

            partido_dict = {
                "id_evento":      eid,
                "local":          local,
                "visitante":      visitante,
                "liga":           "Fútbol",
                "hora":           "",
                "url":            url,
                "cuotas_raw":     odds,
                "fecha_consulta": _ts(),
            }

            # Exclusión estricta de eFootball, videojuegos y simulaciones virtuales
            if es_evento_virtual(partido_dict):
                continue

            partidos.append(partido_dict)

        except Exception:
            continue

    return partidos


# ═══════════════════════════════════════════════════════════════════════════════
# FILTRADO Y EXTRACCIÓN DE MERCADOS
# ═══════════════════════════════════════════════════════════════════════════════

def filtrar_mercados_cuota(
    json_data: dict, local: str, visitante: str, cuota_minima: float = 1.30
) -> list[dict]:
    """
    Filtra estrictamente las selecciones del JSON de Betsson:
    - Cruza marketId de marketSelections con markets para obtener el nombre del mercado.
    - Descarta automáticamente cualquier selección con cuota <= cuota_minima (estrictamente > 1.30).
    - Mapea HOME/AWAY/DRAW a nombres limpios.
    """
    mercados_filtrados: list[dict] = []
    data_obj = json_data.get("data", {})
    markets_list = data_obj.get("markets", []) or []
    markets_map: dict[str, str] = {}
    for m in markets_list:
        mid = m.get("id") or m.get("marketId")
        label = m.get("label") or m.get("name") or "Mercado General"
        if mid:
            markets_map[mid] = label

    selections_list = data_obj.get("marketSelections", []) or []
    for sel in selections_list:
        try:
            status = sel.get("status", "")
            if status and status.lower() == "suspended":
                continue

            odds_val = sel.get("odds") or sel.get("price") or sel.get("priceDecimal")
            if odds_val is None:
                continue
            cuota = float(str(odds_val).replace(",", "."))
            # REQUISITO ESTRICTO: cuota decimal > cuota_minima (1.30 no pasa; 1.31 pasa)
            if cuota <= cuota_minima:
                continue

            mid = sel.get("marketId", "")
            familia = markets_map.get(mid, "Mercado General")

            nombre_sel = (
                sel.get("participantLabel") or
                sel.get("alternateLabel") or
                sel.get("participant") or
                sel.get("label") or
                sel.get("name") or ""
            )

            templ = str(sel.get("selectionTemplateId", "")).upper()
            if templ == "HOME" and not nombre_sel:
                nombre_sel = local
            elif templ == "AWAY" and not nombre_sel:
                nombre_sel = visitante
            elif templ == "DRAW" and not nombre_sel:
                nombre_sel = "Empate"

            mercados_filtrados.append({
                "familia":       familia,
                "nombre":        familia,
                "seleccion":     nombre_sel,
                "cuota":         cuota,
                "disponible":    True,
                "hora_consulta": _ts(),
                "fuente":        "api_betsson_directa",
            })
        except Exception:
            continue

    return mercados_filtrados


# ═══════════════════════════════════════════════════════════════════════════════
# OBTENER MERCADOS DE UN PARTIDO
# ═══════════════════════════════════════════════════════════════════════════════

def obtener_mercados(page: Page, partido: dict) -> list[dict]:
    """
    Extrae todos los mercados disponibles para un partido.
    Estrategia 1: Consulta DIRECTA por API al endpoint /api/sb/v1/widgets/event-market/v1.
    Estrategia 2: Fallback visual al DOM si la API no devuelve selecciones.
    Aplica filtro estricto: cuota estrictamente > config.CUOTA_MINIMA.
    """
    eid = partido.get("id_evento", "")
    local = partido.get("local", "?")
    visitante = partido.get("visitante", "?")
    mercados: list[dict] = []

    # ── ESTRATEGIA 1: Consulta DIRECTA por API ───────────────────────────────
    if eid and eid.startswith("f-"):
        codigos = [
            "MW3W", "FTCSR", "AWEH", "DC", "BTTS", "AGSCRSB",
            "MWOU-0.5", "MWOU-1.5", "MWOU-2.5", "MWOU-3.5", "MWOU-4.5",
            "1HTG-0.5", "1HTG-1.5", "HTFTFB", "HWEH"
        ]
        mkt_ids = ",".join(f"m-{eid}-{c}" for c in codigos)
        url_api = f"https://www.betsson.pe/api/sb/v1/widgets/event-market/v1?includescoreboards=true&marketids={mkt_ids}"

        try:
            headers = _session_headers or {
                "accept": "application/json, text/plain, */*",
                "marketcode": "pe",
                "x-sb-country-code": "PE",
            }
            resp = page.request.get(url_api, headers=headers, timeout=12000)
            if resp.ok:
                data = resp.json()
                try:
                    events = data.get("data", {}).get("events", [])
                    if events and isinstance(events, list) and len(events) > 0:
                        ev = events[0]
                        h_name = (ev.get("homeParticipant") or {}).get("name")
                        a_name = (ev.get("awayParticipant") or {}).get("name")
                        if h_name and a_name:
                            partido["local"] = h_name
                            partido["visitante"] = a_name
                            local, visitante = h_name, a_name
                except Exception:
                    pass

                mercados = filtrar_mercados_cuota(
                    data, local, visitante, cuota_minima=config.CUOTA_MINIMA
                )
                if mercados:
                    print(f"[BETSSON] API Directa: '{local} vs {visitante}': {len(mercados)} selecciones con cuota > {config.CUOTA_MINIMA}")
                    return mercados
        except Exception as e:
            print(f"[BETSSON] API Directa aviso para {local} vs {visitante}: {e}")

    # ── ESTRATEGIA 2: Fallback visual al DOM ──────────────────────────────────
    url_evento = partido.get("url", "")
    if not url_evento:
        return []

    cap = _BetssonCapture()
    cap.adjuntar(page)

    try:
        page.goto(url_evento, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_selector(
                "[test-id='event-markets.content'], [test-id='selection']", timeout=8000
            )
        except Exception:
            pass

        try:
            titulo = page.title()
            tm = re.search(r"Cuanto paga (.+?) - (.+?) Apuestas", titulo, re.I)
            if not tm:
                tm = re.search(r"(.+?)\s*-\s*(.+?)(?:\s+Apuestas|\s*\|\s*Betsson)", titulo, re.I)
            if tm:
                partido["local"] = tm.group(1).strip()
                partido["visitante"] = tm.group(2).strip()
                local = partido["local"]
                visitante = partido["visitante"]
        except Exception:
            pass

        if cap.mkt_data:
            for b in cap.mkt_data:
                mercados.extend(filtrar_mercados_cuota(b, local, visitante, cuota_minima=config.CUOTA_MINIMA))

        if not mercados:
            mercados = _parsear_mkt_dom(page, local, visitante)

    except Exception as e:
        print(f"[BETSSON] Fallback DOM error '{local} vs {visitante}': {e}")
    finally:
        cap.desacoplar(page)

    filtrados = [m for m in mercados if (m.get("cuota") or 0) > config.CUOTA_MINIMA]
    print(f"[BETSSON] DOM: '{local} vs {visitante}': {len(filtrados)} cuotas > {config.CUOTA_MINIMA}")
    return filtrados


def _parsear_mkt_dom(page: Page, local: str, visitante: str) -> list[dict]:
    """
    DOM: extrae mercados de la página de evento individual.
    Usa el contenedor verificado [test-id='event-markets.content'] y las
    selecciones con [test-id='selection'].
    """
    mercados: list[dict] = []
    content = page.query_selector("[test-id='event-markets.content']")
    if not content:
        content = page

    selections = content.query_selector_all("[test-id='selection'], obg-selection-container")
    for sel in selections:
        try:
            txt = sel.inner_text().strip()
            partes = [p.strip() for p in txt.split("\n") if p.strip()]
            if len(partes) < 2:
                continue

            nombre_sel = partes[0]
            odd_str = partes[-1]
            try:
                cuota = float(odd_str.replace(",", "."))
            except ValueError:
                continue

            sel_id = sel.get_attribute("selection-id") or ""
            familia = "Mercado General"
            for code, fam_name in _MERCADOS_INTERES.items():
                if f"-{code}-" in sel_id or f"-{code}" in sel_id:
                    familia = fam_name
                    break
            else:
                if "más de" in nombre_sel.lower() or "menos de" in nombre_sel.lower():
                    familia = "Más/Menos Goles"
                elif any(x in nombre_sel.lower() for x in ["empate", local.lower(), visitante.lower()]):
                    familia = "Ganador del Partido (1X2)"
                elif nombre_sel.lower() in ("sí", "si", "no"):
                    familia = "Ambos Equipos Anotan"

            mercados.append({
                "familia":       familia,
                "nombre":        familia,
                "seleccion":     nombre_sel,
                "cuota":         cuota,
                "disponible":    True,
                "hora_consulta": _ts(),
                "fuente":        "dom_betsson",
            })
        except Exception:
            continue

    return mercados
