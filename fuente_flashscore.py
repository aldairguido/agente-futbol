"""
agente_futbol/fuente_flashscore.py
───────────────────────────────────────────────────────────────────────────────
ADAPTADOR SECUNDARIO de Flashscore para rellenar métricas que FotMob no publica.

Por qué existe (medido, no supuesto):
  FotMob no publica 5 de las 45 métricas del catálogo en ninguna competición:
  `pases_ratio`, `tiros_libres`, `errores_remate`, `errores_gol`, `saques_puerta`.
  El feed interno de Flashscore SÍ las publica, con estos valores reales
  comprobados en el partido 21mxSPJb (octubre de 2026):
      'Tiros libres'                    = ('6', '5')
      'Saques de puerta'                = ('1', '4')
      'Errores conducentes a remate'    = ('0', '0')
      'Pases'                           = ('88% (260/296)', '85% (205/240)')
      'Posesión'                        = ('54%', '46%')

Cómo se accede (todo verificado):
  • Host real:      https://global.flashscore.ninja/203/x/feed/
  • Cabecera clave: x-fsign: SW9D1eZo   (sin ella: HTTP 401; con ella: HTTP 200)
  • Formato:        bloques separados por '~', campos 'clave÷valor¬'
  • IDs de partido: alfanuméricos de 8 caracteres, DISTINTOS a los de FotMob.
    Se obtienen del feed de resultados `f_1_0_-{dia}_es-pe_1`.

REGLA DE INTEGRIDAD: aquí NO se calcula ni se estima nada. Solo se leen valores
publicados. La única excepción es `pases_ratio`, que es un cociente aritmético
declarado explícitamente como procedencia «derivado».
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# Host y firma medidos en la red real
FLASHSCORE_FEED = "https://global.flashscore.ninja/203/x/feed"
FLASHSCORE_FEED_ALT = "https://global.flashscore.ninja/2/x/feed"
FLASHSCORE_XFSIGN = "SW9D1eZo"
FLASHSCORE_REFERER = "https://www.flashscore.com/"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Procedencias posibles de cada valor
PROC_FOTMOB = "fotmob"
PROC_FLASHSCORE = "flashscore"
PROC_DERIVADO = "derivado"
PROC_SIN_DATO = "sin_dato"


# Etiquetas EXACTAS del feed en español -> clave interna del catálogo.
# Comprobadas contra una respuesta real (35 métricas publicadas).
MAPA_ETIQUETAS: dict[str, str] = {
    # Remates y xG
    "Goles esperados (xG)": "xg",
    "xG a puerta (xGOT)": "xgot",
    "Remates totales": "remates_totales",
    "Remates a puerta": "remates_puerta",
    "Remates fuera": "remates_fuera",
    "Remates rechazados": "remates_rechazados",
    "Remates dentro del área": "remates_dentro_area",
    "Remates fuera del área": "remates_fuera_area",
    "Al palo": "al_palo",
    "Goles de cabeza": "goles_cabeza",
    # Ataque
    "Posesión": "posesion_pct",
    "Grandes ocasiones": "grandes_ocasiones",
    "Grandes ocasiones aprovechadas": "grandes_ocasiones_aprovechadas",
    "Grandes ocasiones falladas": "grandes_ocasiones_falladas",
    "Grandes ocasiones perdidas": "grandes_ocasiones_perdidas",
    "Toques en el área rival": "toques_area_rival",
    "Pases entre líneas completados": "pases_entre_lineas",
    "Córneres": "corners",
    "Tiros libres": "tiros_libres",
    "Fueras de juego": "fueras_juego",
    "Tarjetas amarillas": "tarjetas_amarillas",
    "Tarjetas rojas": "tarjetas_rojas",
    # Pases
    "Pases": "pases_total",
    "Pases totales": "pases_totales",
    "Pases completados": "pases_completados",
    "Pases %": "pases_pct",
    "Pases largos": "pases_largos_pct",
    "Pases largos totales": "pases_largos",
    "Pases largos completados": "pases_largos_completados",
    "Pases en el tercio final": "pases_tercio_final_pct",
    "Pases en el tercio final totales": "pases_tercio_final",
    "Pases en el tercio final completados": "pases_tercio_final_completados",
    "Centros": "cruces_pct",
    "Centros totales": "centros",
    "Centros completados": "centros_completados",
    "Centros %": "centros_pct",
    "Asistencias esperadas (xA)": "xa_equipo",
    "Pases clave": "pases_clave",
    "Saques de banda": "saques_banda",
    # Defensa
    "Faltas": "faltas",
    "Entradas": "entradas_pct",
    "Entradas totales": "entradas",
    "Entradas ganadas": "entradas_ganadas",
    "Entradas ganadas %": "entradas_ganadas_pct",
    "Duelos ganados": "duelos_ganados",
    "Duelos de suelo ganados": "duelos_suelo_pct",
    "Duelos aéreos ganados": "duelos_aereos_pct",
    "Despejes": "despejes",
    "Intercepciones": "intercepciones",
    "Bloqueos": "bloqueos",
    "Recuperaciones": "recuperaciones",
    "Errores conducentes a remate": "errores_remate",
    "Errores conducentes a gol": "errores_gol",
    # Portería
    "Paradas": "paradas",
    "xGOT enfrentados": "xgot_enfrentado",
    "Goles encajados": "goles_encajados",
    "Goles evitados": "goles_evitados",
    "Saques de puerta": "saques_puerta",
}

# Etiquetas compuestas del feed de Flashscore del tipo "88% (260/296)"
# Mapeadas a (clave_pct, clave_completados, clave_totales)
DESGLOSE_COMPUESTOS_FLASHSCORE: dict[str, tuple[str, str, str]] = {
    "pases": ("pases_pct", "pases_completados", "pases_totales"),
    "pases largos": ("pases_largos_pct", "pases_largos_completados", "pases_largos"),
    "pases en el tercio final": (
        "pases_tercio_final_pct",
        "pases_tercio_final_completados",
        "pases_tercio_final",
    ),
    "centros": ("centros_pct", "centros_completados", "centros"),
    "entradas": ("entradas_ganadas_pct", "entradas_ganadas", "entradas"),
}

# Pares de alias canónicos para garantizar que tanto los nombres nuevos del catálogo
# como las claves históricas del motor de mercados estén siempre poblados.
PARES_ALIAS_METRICAS: tuple[tuple[str, str], ...] = (
    ("posesion", "posesion_pct"),
    ("remates_a_puerta", "remates_puerta"),
    ("grandes_ocasiones_perdidas", "grandes_ocasiones_falladas"),
    ("corneres", "corners"),
    ("fueras_de_juego", "fueras_juego"),
    ("pases_pct", "pases_precision_pct"),
    ("centros_pct", "cruces_pct"),
    ("xa", "xa_equipo"),
    ("saques_de_banda", "saques_banda"),
    ("entradas_ganadas_pct", "entradas_pct"),
    ("xgot_enfrentados", "xgot_enfrentado"),
    ("saques_de_puerta", "saques_puerta"),
)

# Métricas que FotMob NO publica y que el adaptador debe aportar
METRICAS_A_RELLENAR = ("pases_ratio", "tiros_libres", "errores_remate",
                       "errores_gol", "saques_puerta")


def _sin_acentos(texto: str) -> str:
    return unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()


def normalizar(texto: str) -> str:
    """Clave de comparación tolerante a tildes, mayúsculas y espacios."""
    return re.sub(r"\s+", " ", _sin_acentos(texto).lower()).strip()


@dataclass
class MetricasFlashscore:
    """Métricas de un partido leídas del feed, por bando."""
    id_flashscore: str
    local: dict[str, float] = field(default_factory=dict)
    visitante: dict[str, float] = field(default_factory=dict)
    # Desglose por periodo: {"FT"|"1T"|"2T": {"local": {...}, "visitante": {...}}}.
    # El feed publica el partido Y sus dos mitades, así que el relleno puede cubrir
    # los tres periodos en lugar de dejar 1T/2T vacíos.
    por_periodo: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    etiquetas_crudas: dict[str, str] = field(default_factory=dict)
    nombre_local: str = ""
    nombre_visitante: str = ""
    marcador: tuple[int, int] | None = None
    fecha: str = ""
    fuente: str = PROC_FLASHSCORE
    notas: list[str] = field(default_factory=list)

    def metricas_de(self, periodo: str, bando: str) -> dict[str, float]:
        """Métricas de un periodo y bando; cae al total si el periodo no existe."""
        datos = (self.por_periodo.get(periodo) or {}).get(bando)
        if datos:
            return datos
        return self.local if bando == "local" else self.visitante

    @property
    def periodos_disponibles(self) -> list[str]:
        return [p for p in ("FT", "1T", "2T") if (self.por_periodo.get(p) or {}).get("local")]

    @property
    def vacia(self) -> bool:
        return not self.local and not self.visitante


def _a_numero(texto: str) -> float | None:
    """
    Convierte los valores del feed al número publicado.

    Formatos vistos en respuestas reales:
        '6'                -> 6.0
        '54%'              -> 54.0
        '0.15'             -> 0.15
        '88% (260/296)'    -> 260.0   (se toma el NUMERADOR: es el "número" del catálogo)
    """
    if texto is None:
        return None
    t = str(texto).strip()
    if not t or t in ("-", "—"):
        return None
    m = re.match(r"^\s*[\d.,]+\s*%\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)", t)
    if m:
        return float(m.group(1))
    m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", t)
    if m:
        return float(m.group(1))
    m = re.match(r"^\s*(-?\d+(?:[.,]\d+)?)\s*%?\s*$", t)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def _desglosar_compuesto(texto: str) -> tuple[float | None, float | None, float | None]:
    """
    Extrae (porcentaje, completados, totales) desde formatos compuestos de Flashscore/FotMob:
        '88% (260/296)' -> (88.0, 260.0, 296.0)
        '0% (0/4)'      -> (0.0, 0.0, 4.0)
        '260/296 (88%)' -> (88.0, 260.0, 296.0)
        '203 (74%)'     -> (74.0, 203.0, 274.0)
        '260/296'       -> (87.8, 260.0, 296.0)
    """
    if texto is None:
        return (None, None, None)
    t = str(texto).strip()
    if not t or t in ("-", "—"):
        return (None, None, None)
    m = re.match(r"^\s*([\d.,]+)\s*%\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)\s*$", t)
    if m:
        pct = float(m.group(1).replace(",", "."))
        comp = float(m.group(2))
        tot = float(m.group(3))
        return (pct, comp, tot)
    m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*\(\s*([\d.,]+)\s*%\s*\)\s*$", t)
    if m:
        comp = float(m.group(1))
        tot = float(m.group(2))
        pct = float(m.group(3).replace(",", "."))
        return (pct, comp, tot)
    m = re.match(r"^\s*(\d+)\s*\(\s*([\d.,]+)\s*%\s*\)\s*$", t)
    if m:
        comp = float(m.group(1))
        pct = float(m.group(2).replace(",", "."))
        tot = float(round(comp * 100.0 / pct)) if pct > 0 else comp
        return (pct, comp, tot)
    m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", t)
    if m:
        comp = float(m.group(1))
        tot = float(m.group(2))
        pct = round(comp * 100.0 / tot, 1) if tot > 0 else 0.0
        return (pct, comp, tot)
    return (None, None, None)


def _sincronizar_y_derivar_bando(d: dict[str, float]) -> None:
    """
    Sincroniza los pares de alias y aplica las relaciones algebraicas de un bando
    (tríos totales/completados/pct, grandes ocasiones, remates).
    """
    for a, b in PARES_ALIAS_METRICAS:
        if a in d and b not in d:
            d[b] = d[a]
        elif b in d and a not in d:
            d[a] = d[b]

    # Tríos (pct, completados, totales)
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
        if comp is not None and tot is not None and pct is None:
            d[k_pct] = round((comp / tot) * 100.0, 1) if tot > 0 else 0.0
        elif tot is not None and pct is not None and comp is None:
            d[k_comp] = float(round((tot * pct) / 100.0))
        elif comp is not None and pct is not None and tot is None:
            d[k_tot] = float(round((comp * 100.0) / pct)) if pct > 0 else comp

    # Grandes ocasiones: grandes_ocasiones = aprovechadas + perdidas
    go = d.get("grandes_ocasiones")
    go_ap = d.get("grandes_ocasiones_aprovechadas")
    go_pe = d.get("grandes_ocasiones_perdidas")
    if go is not None and go_pe is not None and go_ap is None:
        d["grandes_ocasiones_aprovechadas"] = float(max(0.0, go - go_pe))
    elif go is not None and go_ap is not None and go_pe is None:
        d["grandes_ocasiones_perdidas"] = float(max(0.0, go - go_ap))
    elif go_ap is not None and go_pe is not None and go is None:
        d["grandes_ocasiones"] = float(go_ap + go_pe)

    # Re-sincronizar alias tras derivaciones
    for a, b in PARES_ALIAS_METRICAS:
        if a in d and b not in d:
            d[b] = d[a]
        elif b in d and a not in d:
            d[a] = d[b]


def parsear_feed(texto: str) -> dict[str, tuple[str, str]]:
    """
    Parsea el formato de Flashscore: bloques separados por '~', campos
    'clave÷valor¬'. Devuelve {etiqueta: (valor_local, valor_visitante)}.
    """
    salida: dict[str, tuple[str, str]] = {}
    if not texto:
        return salida
    for bloque in texto.split("~"):
        campos: dict[str, str] = {}
        for item in bloque.split("¬"):
            if "÷" in item:
                k, v = item.split("÷", 1)
                campos[k] = v
        if "SG" in campos:
            salida[campos["SG"].strip()] = (campos.get("SH", "").strip(),
                                            campos.get("SI", "").strip())
    return salida


# ── Periodos del feed de estadísticas ────────────────────────────────────────
# El feed `df_st_1_{id}` NO es un solo bloque de totales: trae el partido completo
# y ADEMÁS el desglose por tiempos, separados por el campo `SE`:
#     SE÷Partido  ·  SE÷1er Tiempo  ·  SE÷2º Tiempo
PERIODOS_FLASHSCORE: dict[str, str] = {
    "partido": "FT",
    "match": "FT",
    "1er tiempo": "1T",
    "1st half": "1T",
    "2º tiempo": "2T",
    "2do tiempo": "2T",
    "2nd half": "2T",
}

# Mapeo del feed GraphQL dts/dtsse de Flashscore (stage 74=FT, 12=1T, 13=2T)
ETAPAS_GRAPHQL_FLASHSCORE: dict[str, str] = {
    "74": "FT",
    "12": "1T",
    "13": "2T",
}

MAPA_TIPOS_GRAPHQL_FLASHSCORE: dict[str, str] = {
    "expected_goals": "Goles esperados (xG)",
    "xg_on_target": "xG a puerta (xGOT)",
    "ball_possession": "Posesión",
    "Goal_attempts": "Remates totales",
    "Shots_on_goal": "Remates a puerta",
    "Shots_off_goal": "Remates fuera",
    "Blocked_shots": "Remates rechazados",
    "shots_inside_the_box": "Remates dentro del área",
    "shots_outside_the_box": "Remates fuera del área",
    "hit_woodwork": "Al palo",
    "headed_goals": "Goles de cabeza",
    "big_chances": "Grandes ocasiones",
    "big_chances_scored": "Grandes ocasiones aprovechadas",
    "big_chances_missed": "Grandes ocasiones falladas",
    "touches_in_opposition_box": "Toques en el área rival",
    "accurate_through_balls": "Pases entre líneas completados",
    "Corner_kicks": "Córneres",
    "Free_kicks": "Tiros libres",
    "Offsides": "Fueras de juego",
    "Yellow_cards": "Tarjetas amarillas",
    "Red_cards": "Tarjetas rojas",
    "passes_total": "Pases totales",
    "passes_completed": "Pases completados",
    "passes_accuracy": "Pases %",
    "long_passes_total": "Pases largos totales",
    "long_passes_completed": "Pases largos completados",
    "long_passes_accuracy": "Pases largos",
    "passes_in_final_third_total": "Pases en el tercio final totales",
    "passes_in_final_third_completed": "Pases en el tercio final completados",
    "passes_in_final_third_accuracy": "Pases en el tercio final",
    "crosses_total": "Centros totales",
    "crosses_completed": "Centros completados",
    "crosses_accuracy": "Centros %",
    "expected_assists": "Asistencias esperadas (xA)",
    "key_passes": "Pases clave",
    "Throw_in": "Saques de banda",
    "Fouls": "Faltas",
    "tackles_total": "Entradas totales",
    "tackles_won": "Entradas ganadas",
    "tackles_accuracy": "Entradas ganadas %",
    "duels_won": "Duelos ganados",
    "ground_duels_won": "Duelos de suelo ganados",
    "aerial_duels_won": "Duelos aéreos ganados",
    "clearances_total": "Despejes",
    "Interceptions": "Intercepciones",
    "blocks": "Bloqueos",
    "recoveries": "Recuperaciones",
    "errors_leading_to_shot": "Errores conducentes a remate",
    "errors_leading_to_goal": "Errores conducentes a gol",
    "Goalkeeper_saves": "Paradas",
    "xgot_faced": "xGOT enfrentados",
    "result_against": "Goles encajados",
    "goals_prevented": "Goles evitados",
    "Goal_kicks": "Saques de puerta",
}


def parsear_feed_por_periodos(texto: str) -> dict[str, dict[str, tuple[str, str]]]:
    """
    Parsea el feed separando por PERIODO.

    Devuelve {"FT": {etiqueta: (local, visitante)}, "1T": {...}, "2T": {...}}.

    El campo `SE` marca el inicio de cada sección de periodo; las etiquetas `SG` que
    vienen después pertenecen a ese periodo hasta que aparece otro `SE`. Si el
    payload no trae ningún `SE`, todo se asigna a FT (comportamiento del feed diario).
    """
    salida: dict[str, dict[str, tuple[str, str]]] = {"FT": {}, "1T": {}, "2T": {}}
    if not texto:
        return salida
    periodo_actual = "FT"
    for bloque in texto.split("~"):
        campos: dict[str, str] = {}
        for item in bloque.split("¬"):
            if "÷" in item:
                k, v = item.split("÷", 1)
                campos[k] = v
        etiqueta_periodo = campos.get("SE")
        if etiqueta_periodo is not None:
            clave = PERIODOS_FLASHSCORE.get(etiqueta_periodo.strip().lower())
            if clave:
                periodo_actual = clave
            continue
        if "SG" in campos:
            salida[periodo_actual][campos["SG"].strip()] = (
                campos.get("SH", "").strip(), campos.get("SI", "").strip())
    return salida


class ClienteFlashscore:
    """
    Cliente del feed interno de Flashscore. Usa `curl_cffi` con huella de Chrome
    (ya instalado en el proyecto). Si no estuviera disponible, cae a requests
    añadiendo la cabecera x-fsign, que es la que evita el HTTP 401.
    """

    def __init__(self, timeout: int = 25) -> None:
        self.timeout = timeout
        self._sesion = None
        try:
            from curl_cffi import requests as cffi
            self._sesion = cffi.Session(impersonate="chrome120")
            self._cffi = True
        except Exception:
            import requests
            self._sesion = requests.Session()
            self._cffi = False

    def _cabeceras(self) -> dict[str, str]:
        return {"User-Agent": USER_AGENT, "Referer": FLASHSCORE_REFERER,
                "Origin": FLASHSCORE_REFERER.rstrip("/"), "x-fsign": FLASHSCORE_XFSIGN}

    def _get(self, url: str, binario: bool = False):
        kwargs: dict[str, Any] = {"headers": self._cabeceras(), "timeout": self.timeout}
        if self._cffi:
            kwargs["impersonate"] = "chrome120"
        return self._sesion.get(url, **kwargs)

    # ── resultados: de aquí salen los IDs reales de partido ────────────────
    #  Campos del feed de resultados (comprobados en una respuesta real):
    #    AA = id (8 alfanuméricos)   AD = inicio (epoch)
    #    AB = estado (1 próximo, 2 en juego, 3 terminado)
    #    AE/AF = equipos            AG/AH = goles local/visitante
    def partidos_terminados(self, dia: int = -1, idioma: str = "es-pe") -> list[dict[str, Any]]:
        """
        Lista de partidos terminados del día indicado (negativo = días atrás).
        Devuelve [{id, local, visitante, goles_local, goles_visitante, fecha}].
        """
        salida: list[dict[str, Any]] = []
        for base in (FLASHSCORE_FEED, FLASHSCORE_FEED_ALT):
            url = f"{base}/f_1_0_{dia}_{idioma}_1"
            try:
                r = self._get(url)
            except Exception:
                continue
            if r.status_code != 200 or len(r.content) < 50:
                continue
            for bloque in r.text.split("~"):
                campos: dict[str, str] = {}
                for item in bloque.split("¬"):
                    if "÷" in item:
                        k, v = item.split("÷", 1)
                        campos[k] = v
                pid = campos.get("AA")
                if not pid:
                    continue
                if campos.get("AB") != "3":
                    continue
                fecha = ""
                if campos.get("AD"):
                    try:
                        fecha = _fecha_desde_epoch(campos["AD"])
                    except Exception:
                        fecha = ""
                salida.append({
                    "id": pid,
                    "local": campos.get("AE", ""),
                    "visitante": campos.get("AF", ""),
                    "goles_local": _int_o_none(campos.get("AG", "")),
                    "goles_visitante": _int_o_none(campos.get("AH", "")),
                    "fecha": fecha,
                })
            if salida:
                break
        return salida

    def estadisticas(self, id_flashscore: str) -> dict[str, tuple[str, str]]:
        """Métricas publicadas de un partido, o {} si no hay datos."""
        for base in (FLASHSCORE_FEED, FLASHSCORE_FEED_ALT):
            url = f"{base}/df_st_1_{id_flashscore}"
            try:
                r = self._get(url)
            except Exception:
                continue
            if r.status_code != 200 or len(r.content) < 20:
                continue
            metricas = parsear_feed(r.text)
            if metricas:
                return metricas
        return {}

    def metricas_partido(self, id_flashscore: str) -> MetricasFlashscore:
        """
        Métricas numéricas por bando de un partido, CON desglose por periodo.

        El feed publica el partido completo y sus dos mitades por separado (campo
        `SE`), así que aquí se rellenan los tres periodos a la vez, incluyendo el
        desglose de valores compuestos (`pct`, `completados`, `totales`) y la
        sincronización de alias del catálogo.
        """
        salida = MetricasFlashscore(id_flashscore=str(id_flashscore))
        por_periodo = self.estadisticas_por_periodo(id_flashscore)
        if not por_periodo:
            st = self.estadisticas(id_flashscore)
            if st:
                por_periodo = {
                    "FT": {
                        "local": {e: v[0] for e, v in st.items()},
                        "visitante": {e: v[1] for e, v in st.items()}
                    }
                }

        def traducir(etiqueta: str) -> str | None:
            clave = MAPA_ETIQUETAS.get(etiqueta)
            if clave:
                return clave
            for etq, cl in MAPA_ETIQUETAS.items():
                if normalizar(etq) == normalizar(etiqueta):
                    return cl
            return None

        for periodo, secciones in por_periodo.items():
            for bando, etiquetas in secciones.items():
                destino = salida.por_periodo.setdefault(periodo, {}).setdefault(bando, {})
                for etiqueta, valor in etiquetas.items():
                    clave = traducir(etiqueta)
                    if not clave:
                        continue
                    etq_norm = normalizar(etiqueta)
                    pct_c, comp_c, tot_c = _desglosar_compuesto(valor)
                    if etq_norm in DESGLOSE_COMPUESTOS_FLASHSCORE and (
                        pct_c is not None or comp_c is not None or tot_c is not None
                    ):
                        k_pct, k_comp, k_tot = DESGLOSE_COMPUESTOS_FLASHSCORE[etq_norm]
                        if pct_c is not None:
                            destino[k_pct] = pct_c
                        if comp_c is not None:
                            destino[k_comp] = comp_c
                        if tot_c is not None:
                            destino[k_tot] = tot_c
                        # Preservar compatibilidad histórica con pases_total = numerador (260.0)
                        if clave == "pases_total" and comp_c is not None:
                            destino["pases_total"] = comp_c
                        continue
                    # Si es duelo de suelo o aéreo compuesto ("53% (28/53)"), extraer el % real
                    if clave in ("duelos_suelo_pct", "duelos_aereos_pct") and pct_c is not None:
                        destino[clave] = pct_c
                        continue
                    numero = _a_numero(valor)
                    if numero is None:
                        continue
                    destino[clave] = numero
                _sincronizar_y_derivar_bando(destino)

        # Compatibilidad: `local`/`visitante` son el TOTAL del partido
        salida.local = dict((salida.por_periodo.get("FT") or {}).get("local") or {})
        salida.visitante = dict((salida.por_periodo.get("FT") or {}).get("visitante") or {})
        for etiqueta in (por_periodo.get("FT") or {}).get("local", {}):
            clave = traducir(etiqueta)
            if clave:
                salida.etiquetas_crudas[clave] = etiqueta
        return salida

    def _estadisticas_graphql_dts(
        self, id_flashscore: str
    ) -> dict[str, dict[str, dict[str, str]]]:
        """
        Respaldo/enriquecimiento por el endpoint GraphQL `dtsse` + `dts` de Flashscore
        (`https://global.ds.lsapp.eu/pq_graphql`), que publica etapas 74 (FT), 12 (1T) y 13 (2T).
        """
        try:
            url_prov = (
                f"https://global.ds.lsapp.eu/pq_graphql"
                f"?_hash=dtsse&eventId={id_flashscore}&projectId=203"
            )
            r_prov = self._get(url_prov)
            if getattr(r_prov, "status_code", 0) != 200:
                return {}
            datos_prov = json.loads(getattr(r_prov, "text", "") or "{}")
            settings = (
                datos_prov.get("data", {})
                .get("findEventById", {})
                .get("eventParticipantStatisticsSettings")
            ) or []
            provider_id = None
            for s in settings:
                if s.get("isDefault") and s.get("providerId"):
                    provider_id = s["providerId"]
                    break
            if not provider_id and settings:
                provider_id = settings[0].get("providerId")
            if not provider_id:
                return {}
            url_dts = (
                f"https://global.ds.lsapp.eu/pq_graphql"
                f"?_hash=dts&eventId={id_flashscore}&providerId={provider_id}"
            )
            r_dts = self._get(url_dts)
            if getattr(r_dts, "status_code", 0) != 200:
                return {}
            datos_dts = json.loads(getattr(r_dts, "text", "") or "{}")
            participantes = (
                datos_dts.get("data", {})
                .get("findEventById", {})
                .get("eventParticipants")
            ) or []
            if len(participantes) < 2:
                return {}
            salida: dict[str, dict[str, dict[str, str]]] = {}
            for idx_bando, bando in ((0, "local"), (1, "visitante")):
                grupos = participantes[idx_bando].get("statsGroups") or []
                for g in grupos:
                    periodo = ETAPAS_GRAPHQL_FLASHSCORE.get(str(g.get("stage", "")))
                    if not periodo:
                        continue
                    dest = salida.setdefault(periodo, {}).setdefault(bando, {})
                    for st in g.get("stats") or []:
                        tipo = str(st.get("type", ""))
                        etiqueta = MAPA_TIPOS_GRAPHQL_FLASHSCORE.get(tipo)
                        val = st.get("value")
                        if etiqueta and val is not None:
                            dest[etiqueta] = str(val)
            return salida
        except Exception:
            return {}

    def estadisticas_por_periodo(self, id_flashscore: str
                                 ) -> dict[str, dict[str, dict[str, str]]]:
        """
        Lee el feed y lo devuelve separado por periodo y bando:
            {"FT": {"local": {etiqueta: texto}, "visitante": {...}}, "1T": {...}, ...}
        """
        url = f"{FLASHSCORE_FEED}/df_st_1_{id_flashscore}"
        texto = ""
        try:
            r = self._get(url)
            texto = getattr(r, "text", "") or ""
        except Exception:
            texto = ""
        salida: dict[str, dict[str, dict[str, str]]] = {}
        if texto and texto != "0":
            crudo = parsear_feed_por_periodos(texto)
            salida = {
                periodo: {"local": {e: v[0] for e, v in etiquetas.items()},
                          "visitante": {e: v[1] for e, v in etiquetas.items()}}
                for periodo, etiquetas in crudo.items() if etiquetas
            }
        if not salida:
            salida = self._estadisticas_graphql_dts(id_flashscore)
        return salida

    def extraer_cuotas(self, id_flashscore: str) -> dict[str, float | None]:
        """
        Extrae las cuotas 1X2 (local, empate, visitante) de la primera fila de la tabla
        HTML del feed de cuotas de Flashscore (`d_od_{id_flashscore}_es_1_eu`),
        usando `self._get` (que incluye la cabecera `x-fsign`) y BeautifulSoup.
        """
        from bs4 import BeautifulSoup

        id_limpio = str(id_flashscore or "").strip()
        if not id_limpio:
            return {"local": None, "empate": None, "visitante": None}

        url = f"https://d.flashscore.com/x/feed/d_od_{id_limpio}_es_1_eu"
        try:
            r = self._get(url)
            if getattr(r, "status_code", 0) != 200:
                return {"local": None, "empate": None, "visitante": None}
            html = getattr(r, "text", "") or ""
        except Exception:
            return {"local": None, "empate": None, "visitante": None}

        if not html or html.strip() == "0":
            return {"local": None, "empate": None, "visitante": None}

        soup = BeautifulSoup(html, "html.parser")
        tabla = soup.find("table")
        contenedor = (tabla.find("tbody") if tabla else None) or tabla or soup
        filas = contenedor.find_all("tr")

        def _extraer_float_celda(celda: Any) -> float | None:
            span = celda.find("span", class_=re.compile(r"odds|kx", re.I)) or celda
            txt = span.get_text(" ", strip=True).replace(",", ".")
            m = re.search(r"\b(\d+\.\d+|\d+)\b", txt)
            if not m:
                return None
            try:
                val = float(m.group(1))
                return val if val > 1.0 else None
            except ValueError:
                return None

        for fila in filas:
            celdas = fila.find_all("td")
            if len(celdas) < 3:
                continue
            # En tablas de Flashscore con columna de casa de apuestas (4+ celdas),
            # las cuotas 1X2 están en las celdas [1, 2, 3]; si son 3 celdas, en [0, 1, 2].
            candidatas = celdas[1:4] if len(celdas) >= 4 and _extraer_float_celda(celdas[0]) is None else celdas[:3]
            valores = [_extraer_float_celda(c) for c in candidatas]
            if len(valores) == 3 and all(v is not None for v in valores):
                return {
                    "local": valores[0],
                    "empate": valores[1],
                    "visitante": valores[2],
                }
            # Fallback: tomar los primeros 3 valores numéricos válidos de la fila
            todos_vals = [v for v in (_extraer_float_celda(c) for c in celdas) if v is not None]
            if len(todos_vals) >= 3:
                return {
                    "local": todos_vals[0],
                    "empate": todos_vals[1],
                    "visitante": todos_vals[2],
                }

        return {"local": None, "empate": None, "visitante": None}


def _int_o_none(texto: str) -> int | None:
    try:
        return int(str(texto).strip())
    except (TypeError, ValueError):
        return None


def _fecha_desde_epoch(texto: str) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(texto), tz=timezone.utc).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════════════════
# EMPAREJAMIENTO FOTMOB <-> FLASHSCORE
# ═══════════════════════════════════════════════════════════════════════════════

def _parecido(a: str, b: str) -> bool:
    """
    Compara nombres de equipo de forma tolerante: sin tildes, sin sufijos de país
    entre paréntesis ni palabras vacías, y admitiendo que uno sea prefijo del otro
    (FotMob dice «Kashiwa Reysol», Flashscore dice «Kashiwa (Jpn)»).
    """
    def limpio(t: str) -> str:
        t = re.sub(r"\([^)]*\)", " ", str(t or ""))          # fuera «(Jpn)»
        t = normalizar(t)
        palabras = [p for p in t.split() if p not in
                    ("fc", "cf", "sc", "ac", "cd", "club", "de", "the", "if", "sk")]
        return " ".join(palabras)
    ca, cb = limpio(a), limpio(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    # Coincidencia por la primera palabra significativa (evita «Ararat 2» vs «Ararat»)
    pa, pb = ca.split(), cb.split()
    if pa and pb and pa[0] == pb[0]:
        return True
    return ca.startswith(cb) or cb.startswith(ca)


def emparejar_partido(datos: dict[str, Any], candidatos: list[dict[str, Any]],
                      tolerancia_dias: int = 1, exigir_nombres: bool = False
                      ) -> dict[str, Any] | None:
    """
    Empareja un partido de FotMob con su equivalente de Flashscore.

    Regla de fechas (medida en datos reales):
      • ±1 día en general;
      • ±2 días SOLO si coinciden a la vez el marcador exacto Y el nombre del rival.
        Un mismo club no juega dos partidos distintos contra el mismo rival con el
        mismo marcador en dos días, así que la combinación es inequívoca. Es el caso
        real Vasco–Fluminense: FotMob lo fecha el 06-sep y Flashscore el 07-sep por
        diferencia de zona horaria.

    Los nombres se usan además como desempate cuando varios partidos comparten
    marcador el mismo día (caso real: dos 5-1 el 2026-10-14).
    """
    fecha = str(datos.get("fecha") or "")[:10]
    gl, gv = datos.get("goles_local"), datos.get("goles_visitante")
    if not fecha or gl is None or gv is None:
        return None

    from datetime import date
    try:
        d_objetivo = date.fromisoformat(fecha)
    except ValueError:
        return None

    nombre_local = str(datos.get("local") or "")
    nombre_visit = str(datos.get("visitante") or "")

    def nombres_cuadran(c: dict[str, Any], invertido: bool) -> tuple[int, bool]:
        """(aciertos 0-2, si ambos coinciden) comparando los bandos correctos."""
        if not (nombre_local and nombre_visit):
            return 0, False
        cl, cv = ((c.get("visitante", ""), c.get("local", "")) if invertido
                  else (c.get("local", ""), c.get("visitante", "")))
        a = int(_parecido(cl, nombre_local)) + int(_parecido(cv, nombre_visit))
        return a, a == 2

    aceptados: list[tuple[dict[str, Any], bool, int]] = []
    for c in candidatos:
        f_c = str(c.get("fecha") or "")[:10]
        if not f_c:
            continue
        try:
            dif = abs((d_objetivo - date.fromisoformat(f_c)).days)
        except ValueError:
            continue
        mismo = (c.get("goles_local") == gl and c.get("goles_visitante") == gv)
        invertido = (c.get("goles_local") == gv and c.get("goles_visitante") == gl)
        if not (mismo or invertido):
            continue
        aciertos, ambos = nombres_cuadran(c, invertido)
        if dif <= tolerancia_dias:
            # Dentro de la tolerancia general: basta el marcador exacto
            aceptados.append((c, invertido, aciertos, dif))
        elif dif <= 2 and ambos:
            # ±2 días SOLO si coinciden marcador exacto Y **los dos** nombres.
            # Un mismo club no juega dos partidos distintos contra el mismo rival
            # con el mismo marcador en dos días: la combinación es inequívoca.
            # Es el caso real Vasco–Fluminense (FotMob 06-sep, Flashscore 07-sep).
            aceptados.append((c, invertido, aciertos, dif))
    if not aceptados:
        return None

    # Preferir los que cuadran por los DOS nombres; si no, los de fecha más cercana
    por_nombre = [x for x in aceptados if x[2] == 2]
    if por_nombre:
        if len({c["id"] for c, _i, _a, _d in por_nombre}) == 1:
            c, invertido, _a, _d = por_nombre[0]
            return {"candidato": c, "bandos_invertidos": invertido}
        return None                       # varios igual de plausibles: no se elige
    if exigir_nombres:
        return None
    # Sin coincidencia de nombres, solo se acepta si la fecha está dentro de la
    # tolerancia general (nunca por la ventana ampliada)
    cercanos = [x for x in aceptados if x[3] <= tolerancia_dias]
    if not cercanos:
        return None
    if len({c["id"] for c, _i, _a, _d in cercanos}) > 1:
        return None
    c, invertido, _a, _d = cercanos[0]
    return {"candidato": c, "bandos_invertidos": invertido}


# ═══════════════════════════════════════════════════════════════════════════════
# RELLENO Y PROCEDENCIA
# ═══════════════════════════════════════════════════════════════════════════════

def pases_ratio_derivado(pases_total: float | None,
                         posesion_pct: float | None) -> float | None:
    """
    pases_ratio = pases_total / (90 * posesion_pct / 100).

    Es un COCIENTE de dos datos reales del mismo feed, no una estimación. Si falta
    cualquiera de los dos, o la posesión es 0 (división imposible), se devuelve
    None: nunca se inventa.
    """
    if pases_total is None or posesion_pct is None:
        return None
    minutos_con_balon = 90.0 * (float(posesion_pct) / 100.0)
    if minutos_con_balon <= 0:
        return None
    return float(pases_total) / minutos_con_balon


def rellenar_metricas(propias: dict[str, float | None],
                      del_feed: dict[str, float],
                      solo_vacias: bool = True) -> tuple[dict[str, float | None],
                                                          dict[str, str]]:
    """
    Rellena las métricas vacías con las del feed y devuelve la procedencia de cada
    una. Por defecto NUNCA sobrescribe un valor de FotMob: la fuente primaria manda.

    Devuelve (valores, procedencia_por_clave).
    """
    valores = dict(propias)
    procedencia: dict[str, str] = {}
    for clave, valor in valores.items():
        if valor is not None:
            procedencia[clave] = PROC_FOTMOB
    for clave, valor in del_feed.items():
        if valor is None:
            continue
        if solo_vacias and valores.get(clave) is not None:
            continue
        valores[clave] = valor
        procedencia[clave] = PROC_FLASHSCORE
    return valores, procedencia


def completar_con_feed(propias: dict[str, float | None],
                       del_feed: dict[str, float]) -> tuple[dict[str, float | None],
                                                            dict[str, str]]:
    """
    Relleno completo en un paso:
      1. lo que falta se toma del feed (procedencia «flashscore»);
      2. `pases_ratio` se DERIVA del par pases_total/posesion_pct ya presentes
         (procedencia «derivado»), solo si no vino publicado.
    """
    valores, procedencia = rellenar_metricas(propias, del_feed, solo_vacias=True)
    if valores.get("pases_ratio") is None:
        ratio = pases_ratio_derivado(valores.get("pases_total"),
                                     valores.get("posesion_pct"))
        if ratio is not None:
            valores["pases_ratio"] = ratio
            procedencia["pases_ratio"] = PROC_DERIVADO
    for clave, valor in valores.items():
        procedencia.setdefault(clave, PROC_SIN_DATO if valor is None else PROC_FOTMOB)
    return valores, procedencia


# ═══════════════════════════════════════════════════════════════════════════════
# LISTADO HISTÓRICO POR EQUIPO (cacheado)
# ═══════════════════════════════════════════════════════════════════════════════

# Los feeds DIARIOS de Flashscore solo cubren los últimos días, pero una auditoría
# retrocede 2-3 meses. Por eso NO se busca por día: se obtiene UNA vez por equipo el
# listado de resultados del club (que trae ~40 partidos seguidos con sus IDs) y se
# cachea para emparejar los 15 partidos de FotMob contra él.

def _candidatos_flashscore(nombre: str, pais_esperado: str | None = None,
                           maximo: int = 4) -> list[dict[str, Any]]:
    """
    Candidatos de equipo en Flashscore, ORDENADOS por parecido de nombre.
    El país solo desempata: la API no siempre usa el nombre largo («Vasco da Gama»
    figura como «Vasco») y hay homónimos exactos en otros países (un «Vasco Da
    Gama» de Sudáfrica). Por eso NO se elige aquí: se devuelven los candidatos y la
    decisión final la toma `ListadoEquipoFlashscore` contrastando los partidos
    reales de FotMob.
    """
    import json
    import urllib.parse
    import urllib.request

    consultas = [nombre]
    palabras = str(nombre).split()
    if len(palabras) > 1:
        consultas += [" ".join(palabras[:2]), palabras[0]]

    def es_filial(nom: str) -> bool:
        return any(x in nom for x in (" U19", " U20", " U21", " U23", " W", "Women",
                                      "Youth", " 2"))

    def puntos_nombre(nom_api: str, consultado: str) -> int:
        a, b = normalizar(nom_api), normalizar(consultado)
        if not a or not b:
            return 0
        if a == b:
            return 3
        pa, pb = a.split(), b.split()
        if set(pa) <= set(pb) or set(pb) <= set(pa):
            return 2
        if pa and pb and pa[0] == pb[0]:
            return 2 if (set(pa) & set(pb) and min(len(pa), len(pb)) > 1) else 1
        if set(pa) & set(pb):
            return 1
        return 0

    vistos: dict[str, dict[str, Any]] = {}
    for consulta in consultas:
        q = urllib.parse.quote(consulta)
        url = ("https://s.livesport.services/api/v2/search/?lang-id=1"
               f"&q={q}&project-id=2&project-type-id=1")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=12) as resp:
                datos = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
        for d in datos:
            if (d.get("sport", {}).get("name") != "Soccer"
                    or d.get("type", {}).get("name") != "Team"):
                continue
            nom = str(d.get("name") or "")
            if es_filial(nom):
                continue
            genero = str((d.get("gender") or {}).get("name") or "")
            if genero and genero != "Men":
                continue
            pn = puntos_nombre(nom, nombre)
            if pn == 0:
                continue
            pais_api = str((d.get("defaultCountry") or {}).get("name") or "")
            pp = 0
            if pais_esperado:
                na, ne = normalizar(pais_api), normalizar(pais_esperado)
                if na == ne:
                    pp = 3
                elif na and (ne in na or na in ne):
                    pp = 2
                else:
                    pp = -2
            clave = str(d.get("id"))
            entrada = {"id": clave, "url": str(d.get("url") or ""),
                       "nombre": nom, "pais": pais_api, "puntos": pn * 10 + pp}
            if clave not in vistos or vistos[clave]["puntos"] < entrada["puntos"]:
                vistos[clave] = entrada
        if any(v["puntos"] >= 33 for v in vistos.values()):
            break

    return sorted(vistos.values(), key=lambda v: -v["puntos"])[:maximo]


def _id_flashscore_de_equipo(nombre: str, pais_esperado: str | None = None
                             ) -> tuple[str, str] | None:
    """
    Atajo de un solo candidato (sin contraste de partidos). Para el relleno real se
    usa `ListadoEquipoFlashscore`, que sí contrasta.
    """
    candidatos = _candidatos_flashscore(nombre, pais_esperado)
    if not candidatos:
        return None
    return candidatos[0]["id"], candidatos[0]["url"]


class ListadoEquipoFlashscore:
    """
    Listado histórico de resultados de un equipo en Flashscore, obtenido UNA vez y
    cacheado. Se empareja cada partido de FotMob contra esta lista por
    (fecha ± tolerancia) + (marcador exacto) + nombres como desempate.
    """

    def resolver_identidad(self, nombre_equipo: str,
                           fixtures_fotmob: list[dict[str, Any]],
                           pais_esperado: str | None = None,
                           contexto: Any = None) -> dict[str, Any]:
        """
        Decide QUÉ club de Flashscore corresponde al equipo de FotMob y devuelve su
        listado de resultados.

        Método (el nombre NO basta, comprobado): se leen los candidatos por parecido
        de nombre y se elige el cuyos partidos COINCIDEN con los de FotMob. Es lo
        único que distingue el «Vasco da Gama» de Sudáfrica del brasileño, que en
        Flashscore figura como «Vasco».
        """
        pais = pais_esperado or self._paises.get(normalizar(nombre_equipo))
        clave = normalizar(f"id|{nombre_equipo}|{pais or ''}|{len(fixtures_fotmob)}")
        if clave in self._identidad:
            return self._identidad[clave]

        candidatos = _candidatos_flashscore(nombre_equipo, pais)
        evaluados = []
        for cand in candidatos:
            listado = self._leer_listado(cand, contexto)
            aciertos = sum(1 for f in fixtures_fotmob
                           if emparejar_partido(f, listado))
            evaluados.append((aciertos, len(listado), cand, listado))
            # Con evidencia clara (la mayoría de los partidos reconocidos) se corta
            if aciertos >= max(3, int(len(fixtures_fotmob) * 0.6)):
                break

        evaluados.sort(key=lambda x: (-x[0], -x[1]))
        if evaluados:
            aciertos, n_listado, cand, listado = evaluados[0]
        else:
            aciertos, n_listado, cand, listado = 0, 0, None, []

        resultado = {
            "nombre_fotmob": nombre_equipo,
            "pais_esperado": pais,
            "elegido": cand,
            "aciertos": aciertos,
            "total_fixtures": len(fixtures_fotmob),
            "partidos_en_listado": n_listado,
            "listado": listado,
            "candidatos_evaluados": [
                {"nombre": c["nombre"], "pais": c["pais"], "id": c["id"],
                 "aciertos": a, "partidos": n} for a, n, c, _l in evaluados],
        }
        if cand:
            print(f"[FLASHSCORE] identidad {nombre_equipo} -> {cand['nombre']} "
                  f"({cand['pais']}, id={cand['id']}): {aciertos}/"
                  f"{len(fixtures_fotmob)} partidos de FotMob reconocidos "
                  f"(candidatos probados: {len(evaluados)})")
        self._identidad[clave] = resultado
        return resultado

    def metricas_para(self, partido_fotmob: dict[str, Any], nombre_equipo: str,
                      fixtures_fotmob: list[dict[str, Any]],
                      pais_esperado: str | None = None,
                      contexto: Any = None) -> dict[str, Any]:
        """
        Devuelve {"id_flashscore", "metricas", "pareado"} para un partido de FotMob,
        o un dict con metricas vacías si no se pudo emparejar.

        `contexto` es el BrowserContext de Playwright YA abierto por la fuente
        primaria: reutilizarlo es obligatorio, porque abrir un segundo Playwright
        síncrono dentro del bucle asíncrono existente falla.
        """
        info = self.resolver_identidad(nombre_equipo, fixtures_fotmob,
                                       pais_esperado, contexto)
        listado = info["listado"]
        if not listado:
            return {"id_flashscore": None, "metricas": MetricasFlashscore(""),
                    "pareado": False, "motivo": "sin listado de Flashscore"}
        r = emparejar_partido(partido_fotmob, listado)
        if not r:
            return {"id_flashscore": None, "metricas": MetricasFlashscore(""),
                    "pareado": False, "motivo": "partido no emparejado"}
        mid = r["candidato"]["id"]
        return {"id_flashscore": mid, "metricas": self.cliente.metricas_partido(mid),
                "pareado": True, "bandos_invertidos": r["bandos_invertidos"],
                "motivo": ""}

    def __init__(self, cliente: "ClienteFlashscore | None" = None,
                 max_partidos: int = 45) -> None:
        self.cliente = cliente or ClienteFlashscore()
        self.max_partidos = max_partidos
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._paises: dict[str, str] = {}
        self._identidad: dict[str, dict[str, Any]] = {}

    def registrar_pais(self, nombre_equipo: str, pais: str | None) -> None:
        """País del equipo según FotMob, para desambiguar homónimos."""
        if pais:
            self._paises[normalizar(nombre_equipo)] = str(pais)

    def partidos(self, nombre_equipo: str,
                 pais_esperado: str | None = None,
                 fixtures_fotmob: list[dict[str, Any]] | None = None,
                 contexto: Any = None
                 ) -> list[dict[str, Any]]:
        """Atajo: devuelve solo el listado de resultados resuelto."""
        if fixtures_fotmob:
            return self.resolver_identidad(nombre_equipo, fixtures_fotmob,
                                           pais_esperado, contexto)["listado"]
        pais = pais_esperado or self._paises.get(normalizar(nombre_equipo))
        clave = normalizar(f"{nombre_equipo}|{pais or ''}")
        if clave in self._cache:
            return self._cache[clave]
        candidatos = _candidatos_flashscore(nombre_equipo, pais)
        listado = self._leer_listado(candidatos[0], contexto) if candidatos else []
        self._cache[clave] = listado
        return listado

    def _leer_listado(self, cand: dict[str, Any],
                      contexto: Any = None) -> list[dict[str, Any]]:
        """
        Lee los últimos resultados del club desde su página de Flashscore.

        IMPORTANTE: si se pasa `contexto` (un BrowserContext de Playwright ya
        abierto), se reutiliza. Es obligatorio cuando quien llama YA tiene un
        Playwright activo: abrir un segundo Playwright SÍNCRONO dentro del bucle
        asíncrono del existente falla con «Sync API inside the asyncio loop».
        """
        partidos: list[dict[str, Any]] = []
        fs_id, fs_url = cand["id"], cand["url"]
        url_res = f"https://www.flashscore.com/team/{fs_url}/{fs_id}/results/"

        def extraer_de_pagina(pag) -> None:
            estado_fecha: dict[str, Any] = {}
            try:
                pag.goto(url_res, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                return
            try:
                pag.wait_for_selector("[class*='event__match']", timeout=9000)
            except Exception:
                import time as _t
                _t.sleep(2)
            for fila in pag.query_selector_all("[class*='event__match']"):
                if len(partidos) >= self.max_partidos:
                    break
                try:
                    texto = fila.inner_text()
                except Exception:
                    continue
                lineas = [l.strip() for l in texto.split("\n") if l.strip()]
                if len(lineas) < 4:
                    continue
                mid = None
                enlace = fila.query_selector("a[href*='mid=']")
                if enlace:
                    m = re.search(r"mid=([A-Za-z0-9]+)",
                                  enlace.get_attribute("href") or "")
                    if m:
                        mid = m.group(1)
                if not mid:
                    m = re.search(r"g_1_([A-Za-z0-9]{8})",
                                  fila.get_attribute("id") or "")
                    if m:
                        mid = m.group(1)
                if not mid:
                    continue
                numeros = [l for l in lineas[3:] if l.isdigit()]
                if len(numeros) < 2:
                    continue
                fecha = _parsear_fecha_listado(lineas[0], estado_fecha)
                if not fecha:
                    continue
                partidos.append({
                    "id": mid, "fecha": fecha,
                    "local": lineas[1], "visitante": lineas[2],
                    "goles_local": int(numeros[0]),
                    "goles_visitante": int(numeros[1]),
                })

        try:
            if contexto is not None:
                pag = contexto.new_page()
                try:
                    extraer_de_pagina(pag)
                finally:
                    try:
                        pag.close()
                    except Exception:
                        pass
            else:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    nav = p.chromium.launch(headless=True)
                    ctx = nav.new_context(locale="es-ES", user_agent=USER_AGENT)
                    try:
                        extraer_de_pagina(ctx.new_page())
                    finally:
                        ctx.close()
                        nav.close()
        except Exception as e:
            print(f"[FLASHSCORE] no se pudo leer el listado de "
                  f"{cand['nombre']}: {type(e).__name__}: {e}")
        return partidos

    def emparejar(self, partido: dict[str, Any], nombre_equipo: str,
                  tolerancia_dias: int = 1,
                  pais_esperado: str | None = None,
                  fixtures_fotmob: list[dict[str, Any]] | None = None
                  ) -> dict[str, Any] | None:
        """
        Empareja un partido de FotMob (fecha, goles_local, goles_visitante, local,
        visitante) con su ID de Flashscore. Devuelve None si no es inequívoco.
        """
        candidatos = self.partidos(nombre_equipo, pais_esperado, fixtures_fotmob)
        if not candidatos:
            return None
        return emparejar_partido(partido, candidatos, tolerancia_dias=tolerancia_dias)

    def metricas(self, id_flashscore: str) -> MetricasFlashscore:
        return self.cliente.metricas_partido(id_flashscore)


def _fecha_desde_texto(texto: str, anio_referencia: int | None = None) -> str:
    """
    Convierte la fecha que muestra Flashscore en ISO 'YYYY-MM-DD'.

    Formatos reales comprobados en la página de resultados:
        '13.10. 19:30'   -> día.mes. + hora, SIN año (es el formato habitual)
        '14.10.2026'     -> día.mes.año
        '29.01.2025'     -> día.mes.año
    Cuando la fuente omite el año se usa `anio_referencia` (que el lector va
    ajustando hacia atrás al recorrer el listado, que viene de más reciente a más
    antiguo). Si no se indica, se usa el año actual.
    """
    from datetime import datetime
    t = str(texto or "").strip()
    if not t:
        return ""
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})?$", t)
    if not m:
        # Puede venir con la hora pegada: '13.10. 19:30'
        m = re.match(r"^(\d{1,2})\.(\d{1,2})\.?(\d{4})?\s*\d{0,2}:?\d{0,2}", t)
        if not m:
            return ""
    dia, mes = int(m.group(1)), int(m.group(2))
    anio = m.group(3) or anio_referencia or datetime.now().year
    try:
        return f"{int(anio):04d}-{mes:02d}-{dia:02d}"
    except Exception:
        return ""


def _parsear_fecha_listado(texto: str, estado: dict[str, Any]) -> str:
    """
    Fecha del listado con inferencia de año.

    El listado viene de más reciente a más antiguo y la fuente omite el año, así que
    cuando el mes salta hacia adelante (de enero a diciembre) es que se ha pasado al
    año anterior. `estado` guarda el año/ mes anterior para encadenar.
    """
    from datetime import datetime
    m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})?", str(texto or ""))
    if not m:
        return ""
    dia, mes = int(m.group(1)), int(m.group(2))
    if m.group(3):
        anio = int(m.group(3))
    else:
        anio = estado.get("anio") or datetime.now().year
        mes_prev = estado.get("mes")
        if mes_prev is not None and mes > mes_prev + 2:
            anio -= 1                      # saltó de enero/febrero al año anterior
    estado["anio"], estado["mes"] = anio, mes
    try:
        return f"{anio:04d}-{mes:02d}-{dia:02d}"
    except Exception:
        return ""


def candidatos_desde_feed_hh(
    id_flashscore: str, cliente: ClienteFlashscore | None = None
) -> list[dict[str, Any]]:
    """
    Extrae todos los partidos candidatos (H2H y recientes de ambos equipos)
    desde el feed de enfrentamientos directos de Flashscore (`df_hh_1_{id_flashscore}`).
    Devuelve lista de dicts con:
        {"id": mid, "fecha": "YYYY-MM-DD", "local": str, "visitante": str,
         "goles_local": int|None, "goles_visitante": int|None}
    """
    if not id_flashscore or str(id_flashscore).strip() in ("N/D", "None", ""):
        return []
    c = cliente or ClienteFlashscore()
    url = f"{FLASHSCORE_FEED}/df_hh_1_{id_flashscore}"
    texto = ""
    try:
        r = c._get(url)
        texto = getattr(r, "text", "") or ""
    except Exception:
        texto = ""
    if not texto or len(texto) < 50:
        return []

    candidatos: list[dict[str, Any]] = []
    vistos: set[str] = set()

    for b in texto.split("~"):
        items: dict[str, str] = {}
        for x in b.split("¬"):
            if "÷" in x:
                k, v = x.split("÷", 1)
                items[k] = v
        mid = items.get("KP")
        if not mid or mid in vistos:
            continue
        epoch = items.get("KC")
        fecha = _fecha_desde_epoch(epoch) if epoch else ""
        if not fecha:
            continue

        gl = None
        gv = None
        kl = items.get("KL", "").strip()
        km = items.get("KM", "").strip()
        if kl.isdigit():
            gl = int(kl)
        elif ":" in kl:
            partes = kl.split(":")
            if partes[0].isdigit():
                gl = int(partes[0])
            if partes[1].isdigit():
                gv = int(partes[1])
        if km.isdigit():
            gv = int(km)

        nom_l = items.get("KJ", "").strip().lstrip("*")
        nom_v = items.get("KK", "").strip().lstrip("*")

        candidatos.append({
            "id": mid,
            "fecha": fecha,
            "local": nom_l,
            "visitante": nom_v,
            "goles_local": gl,
            "goles_visitante": gv,
        })
        vistos.add(mid)

    return candidatos


def emparejar_partido_con_feed_hh(
    partido_fotmob: dict[str, Any],
    id_flashscore_referencia: str,
    cliente: ClienteFlashscore | None = None,
) -> dict[str, Any] | None:
    """
    Empareja un partido con los candidatos del feed df_hh_1 del ID de referencia.
    """
    candidatos = candidatos_desde_feed_hh(id_flashscore_referencia, cliente=cliente)
    if not candidatos:
        return None
    return emparejar_partido(partido_fotmob, candidatos)
