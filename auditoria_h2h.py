"""
agente_futbol/auditoria_h2h.py
───────────────────────────────────────────────────────────────────────────────
ENFRENTAMIENTO DIRECTO (HEAD-TO-HEAD) ENTRE DOS EQUIPOS YA AUDITADOS.

Flujo unificado:
  1. Se detectan los dos equipos a partir de un texto con formato cruzado
     ("galatasaray vs barcelona", "real madrid - roma", ...).
  2. Se BUSCAN las auditorías históricas ya generadas de ambos equipos. Si no
     existen, están incompletas o superan la antigüedad máxima permitida, se
     lanza el scraper para crearlas/actualizarlas (reutilizando
     `auditoria_historica.auditar_equipo`, con su filtro anti-amistosos y su
     búsqueda profunda hacia atrás).
  3. Se consolidan en UN ÚNICO informe Markdown con tres bloques:
       a) Cobertura de métricas combinada (qué se puede comparar y qué no).
       b) Matriz de enfrentamiento directo: promedio de los últimos N partidos
          oficiales de cada equipo, en sus dos condiciones (local y visitante),
          más los cruces directos reales ya registrados en los datasets.
       c) Análisis de probabilidades y mercados: métricas porcentuales con
          umbral ≥ 70% y métricas de dominio posicional con referencia explícita.

Honestidad de datos: no se estiman probabilidades nuevas ni se mezclan fuentes.
Todo valor del informe sale de estadísticas observadas en los partidos
auditados; lo que no existe se marca como nulo y se declara en la cobertura.

Uso como biblioteca:
    from auditoria_h2h import generar_h2h
    resultado = generar_h2h("Galatasaray", "Barcelona", limite=15)

Uso como script:
    python auditoria_h2h.py "galatasaray vs barcelona" --limite 15
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any

import config
from auditoria_historica import (
    AUDITORIA_DIR,
    FRESCURA_HORAS_POR_DEFECTO,
    LIMITE_PARTIDOS_POR_DEFECTO,
    auditar_equipo,
    exportar_auditoria,
    obtener_auditoria_en_memoria,
)
from fuentes_xg import (
    ALIAS_EQUIPOS_HISPANOS,
    CATALOGO_METRICAS,
    CATEGORIAS_ORDEN,
    ORDEN_PERIODOS,
    PAISES_EUROPA,
    PAISES_SUDAMERICA,
    _SIGLAS_CLUB,
    _inferir_meta_rival_desde_texto,
    _normalizar_fonetica_hispana,
    _pais_de_liga,
    _palabras_nucleo,
    extraer_liga_de_texto,
    puntuar_candidato,
)

H2H_DIR = os.path.join(config.INFORME_DIR, "h2h")

# Palabras vacías que se eliminan al construir la clave de archivo de un equipo,
# para que "Atlético de Madrid" y "Atletico Madrid" apunten al mismo archivo.
_PALABRAS_VACIAS_SLUG = frozenset({
    "de", "del", "la", "el", "los", "las", "y", "e", "da", "do", "dos", "das",
    "di", "van", "von", "the", "of", "club",
})

# Separadores aceptados para detectar un enfrentamiento directo
_SEPARADOR_RE = re.compile(r"\s+(?:vs\.?|v|contra|x|@|-|–|—)\s+", re.I)

# Métricas cuyo valor es un PORCENTAJE auténtico (0-100) y por tanto admiten
# umbrales del 70%. Se excluyen deliberadamente las que llevan sufijo _pct en el
# catálogo pero la fuente publica como conteo (entradas, pases largos, cruces,
# duelos): su porcentaje se calcula contra el total, no se inventa.
PORCENTAJES_AUTENTICOS: frozenset[str] = frozenset({
    "posesion_pct",
    "pases_precision_pct",
    "cruces_pct",
    "duelos_suelo_pct",
    "duelos_aereos_pct",
})

# Métricas de dominio posicional/ofensivo con umbral de referencia explícito.
# El umbral se declara en el informe: no es una probabilidad de apuesta.
UMBRALES_REFERENCIA: dict[str, dict[str, Any]] = {
    "posesion_pct": {"umbral": 70.0, "etiqueta": "Posesión ≥ 70%"},
    "pases_precision_pct": {"umbral": 70.0, "etiqueta": "Precisión de pase ≥ 70%"},
    "cruces_pct": {"umbral": 30.0, "etiqueta": "Precisión de centros ≥ 30%"},
    "duelos_suelo_pct": {"umbral": 70.0, "etiqueta": "Duelos de suelo ganados ≥ 70%"},
    "duelos_aereos_pct": {"umbral": 70.0, "etiqueta": "Duelos aéreos ganados ≥ 70%"},
    "remates_puerta": {"umbral": 5.0, "etiqueta": "≥ 5 remates a puerta"},
    "remates_totales": {"umbral": 12.0, "etiqueta": "≥ 12 remates totales"},
    "grandes_ocasiones": {"umbral": 2.0, "etiqueta": "≥ 2 grandes ocasiones"},
    "corners": {"umbral": 5.0, "etiqueta": "≥ 5 córneres"},
    "toques_area_rival": {"umbral": 20.0, "etiqueta": "≥ 20 toques en el área rival"},
    "xg": {"umbral": 1.5, "etiqueta": "xG ≥ 1.5"},
    "xgot": {"umbral": 1.5, "etiqueta": "xGOT ≥ 1.5"},
    "xa_equipo": {"umbral": 1.0, "etiqueta": "xA ≥ 1.0"},
}


# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

def _slug(texto: str) -> str:
    """
    Clave estable de archivo a partir del nombre de un equipo.

    Normaliza tildes y ELIMINA las palabras vacías, de modo que todas las formas
    de nombrar al mismo equipo apunten al mismo archivo (y el flujo H2H pueda
    reutilizar la auditoría ya generada):
        "Atlético de Madrid" -> atletico_madrid
        "atletico de madrid" -> atletico_madrid
        "Atletico Madrid"    -> atletico_madrid
    """
    _, sin_liga = extraer_liga_de_texto(texto or "")
    base = unicodedata.normalize("NFKD", sin_liga or texto or "equipo").encode("ascii", "ignore").decode()
    palabras = [p for p in re.split(r"[^A-Za-z0-9]+", base) if p]
    significativas = [p for p in palabras if p.lower() not in _PALABRAS_VACIAS_SLUG]
    if significativas:
        palabras = significativas
    return "_".join(p.lower() for p in palabras) or "equipo"


def _fmt(valor: Any, decimales: int = 2) -> str:
    if valor is None:
        return "—"
    if isinstance(valor, float):
        texto = f"{valor:.{decimales}f}".rstrip("0").rstrip(".")
        return texto or "0"
    return str(valor)


def _etiquetas() -> dict[str, str]:
    return {d.clave: d.etiqueta for d in CATALOGO_METRICAS}


def extraer_equipos_y_liga(texto: str, comando: str = "h2h") -> tuple[str, str, dict[str, Any] | None] | None:
    """
    Devuelve (equipo_a, equipo_b, liga_info) si el texto tiene formato de cruce, o None.
    Soporta prefijos de liga/competición antes de los equipos:
      - 'bundesliga mainz vs friburgo' -> ('mainz', 'friburgo', {'nombre': 'Bundesliga', ...})
      - 'la liga ea sports barcelona vs real madrid' -> ('barcelona', 'real madrid', {'nombre': 'LaLiga', ...})
      - 'champions league real madrid vs bayern' -> ('real madrid', 'bayern', {'nombre': 'Champions League', ...})
    """
    if not texto:
        return None
    cuerpo = re.sub(rf"^/{comando}(@\w+)?", "", texto.strip(), flags=re.I).strip()

    # Primero dividir limpiamente por el separador 'vs' / 'contra' / '-'
    partes = _SEPARADOR_RE.split(cuerpo, maxsplit=1)
    if len(partes) != 2:
        liga_global, cuerpo_sin_liga = extraer_liga_de_texto(cuerpo)
        trabajo = cuerpo_sin_liga if liga_global else cuerpo
        partes = _SEPARADOR_RE.split(trabajo, maxsplit=1)
        if len(partes) != 2:
            return None
    else:
        liga_global = None

    a_raw, b_raw = (partes[0].strip().strip('"').strip("'"), partes[1].strip().strip('"').strip("'"))
    liga_a, a = extraer_liga_de_texto(a_raw)
    liga_b, b = extraer_liga_de_texto(b_raw)
    a = (a or a_raw).strip().strip('"').strip("'")
    b = (b or b_raw).strip().strip('"').strip("'")
    if not a or not b:
        return None
    # Evita confundir con límites tipo "limite:15"
    if re.fullmatch(r"[\d:]+", a) or re.fullmatch(r"[\d:]+", b):
        return None
    liga_final = liga_a or liga_global or liga_b
    return a, b, liga_final


def extraer_equipos(texto: str, comando: str = "h2h") -> tuple[str, str] | None:
    """
    Devuelve (equipo_a, equipo_b) si el texto tiene formato de cruce, o None.
    Acepta: 'galatasaray vs barcelona', 'bundesliga mainz vs friburgo', '/h2h a vs b'.
    """
    res = extraer_equipos_y_liga(texto, comando=comando)
    if not res:
        return None
    return res[0], res[1]


# ═══════════════════════════════════════════════════════════════════════════════
# BÚSQUEDA Y CARGA DE AUDITORÍAS EXISTENTES
# ═══════════════════════════════════════════════════════════════════════════════

def _slug_legado(texto: str) -> str:
    """
    Clave con el criterio ANTERIOR (conservaba las palabras vacías). Se mantiene
    solo para poder localizar auditorías ya generadas con nombres como
    'auditoria_universitario_de_deportes_*.json'.
    """
    _, sin_liga = extraer_liga_de_texto(texto or "")
    base = unicodedata.normalize("NFKD", sin_liga or texto or "equipo").encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").lower()
    return base or "equipo"


def _auditoria_corresponde_a_consulta(
    doc: dict[str, Any],
    equipo_consulta: str,
    rival: Any = None,
) -> tuple[bool, str]:
    """
    Verifica que el documento de auditoría en caché corresponda realmente al equipo
    y liga solicitados (evita reutilizar cachés contaminados como 'FC Porto' para
    'fc fulham', 'Deportivo (W)' para 'Deportivo A Coruña' o 'Liverpool FC' de Uruguay
    para un cruce de Premier League).
    """
    eq_doc = doc.get("equipo") or {}
    nombre_doc = str(eq_doc.get("nombre") or "").strip()
    if not nombre_doc:
        return True, ""

    liga_pref, consulta_limpia = extraer_liga_de_texto(equipo_consulta or "")
    meta_ctx: dict[str, Any] | None = None
    if isinstance(rival, dict):
        meta_ctx = dict(rival)
    elif isinstance(rival, str) and rival.strip():
        meta_ctx = _inferir_meta_rival_desde_texto(rival)
    if liga_pref:
        meta_ctx = {**(meta_ctx or {}), **liga_pref}

    cand_doc = {
        "id": eq_doc.get("id"),
        "name": nombre_doc,
        "leagueName": eq_doc.get("liga"),
        "leagueId": eq_doc.get("id_liga"),
        "score": 100.0,
    }
    pts = puntuar_candidato(cand_doc, consulta_limpia or equipo_consulta, rival_meta=meta_ctx)
    # pts[0] <= -50 significa que `tiene_identidad` es False (ej. FC Porto para fc fulham)
    if pts[0] <= -50:
        return False, f"el equipo en caché ({nombre_doc}) no coincide con «{consulta_limpia or equipo_consulta}»"
    # pts[2] < 0 significa que es equipo femenino o filial no solicitado
    if pts[2] < 0:
        return False, f"el equipo en caché ({nombre_doc}) es filial/femenino no solicitado"
    # pts[0] < 0 con contexto de liga o rival significa incoherencia geográfica/liga
    if pts[0] < 0 and meta_ctx:
        return False, (
            f"incoherencia de liga/país con el contexto solicitado "
            f"({nombre_doc} en {eq_doc.get('liga')})"
        )
    return True, ""


def buscar_auditoria(equipo: str, directorio: str | None = None,
                     rival: Any = None) -> str | None:
    """
    Devuelve la ruta del JSON de auditoría más reciente del equipo, o None.
    Prueba la clave actual, la anterior, el núcleo sin siglas ('fc fulham' -> 'fulham')
    y alias hispanos/fonéticos ('friburgo' -> 'sc_freiburg', 'español' -> 'espanyol').
    """
    raiz = directorio or AUDITORIA_DIR
    _, eq_limpio = extraer_liga_de_texto(equipo or "")
    eq_base = eq_limpio or equipo
    candidatos: list[str] = []
    slug_nucleo = "_".join(_palabras_nucleo(eq_base))
    slugs_a_probar = {_slug(eq_base), _slug_legado(eq_base)}
    if slug_nucleo:
        slugs_a_probar.add(slug_nucleo)

    norm_eq = _slug(eq_base).replace("_", " ")
    claves_alias = {norm_eq, slug_nucleo.replace("_", " ")}
    for k, aliases in ALIAS_EQUIPOS_HISPANOS.items():
        if k in claves_alias or _slug(k) in slugs_a_probar:
            for a in aliases:
                slugs_a_probar.add(_slug(a))
                slugs_a_probar.add(_slug_legado(a))
    for fon in _normalizar_fonetica_hispana(eq_base):
        slugs_a_probar.add(_slug(fon))
        slugs_a_probar.add(_slug_legado(fon))

    for slug in slugs_a_probar:
        if not slug or slug in _SIGLAS_CLUB:
            continue
        patron = os.path.join(raiz, "**", f"auditoria_{slug}_*.json")
        candidatos += [p for p in glob.glob(patron, recursive=True)
                       if not p.endswith("_matriz.csv")]
    if not candidatos:
        return None

    # Ordenar del más reciente al más antiguo y devolver el primero cuya identidad sea coherente
    for ruta_cand in sorted(set(candidatos), key=os.path.getmtime, reverse=True):
        try:
            with open(ruta_cand, "r", encoding="utf-8") as f_c:
                doc_c = json.load(f_c)
            ok_id, _ = _auditoria_corresponde_a_consulta(doc_c, equipo, rival=rival)
            if ok_id:
                return ruta_cand
        except Exception:
            continue
    return max(set(candidatos), key=os.path.getmtime)


def _antiguedad_horas(ruta: str) -> float:
    return (datetime.now().timestamp() - os.path.getmtime(ruta)) / 3600.0


def _es_auditoria_obsoleta(doc: dict[str, Any],
                           limite: int) -> tuple[bool, str]:
    """
    Detecta la HUELLA del escaneo viejo, el anterior al cruce de temporadas por
    `/api/leagues`. Devuelve (obsoleta, motivo).

    Por qué hace falta: el escaneo roto dejaba escrito `fuente_agotada: True` y
    «la fuente no ofrece más páginas de calendario». El validador de caché leía esa
    marca y REUTILIZABA la auditoría para siempre — el error del pasado se leía como
    si fuera un hecho de la fuente, así que la auditoría mala nunca se regeneraba.

    Huellas que delatan el escaneo viejo (medidas en los JSON reales):
      • `auditados < límite` con `paginas_escaneadas <= 1` → se rindió antes de
        pedir la segunda página (Roma 7/15, Real Madrid 9/15, Arsenal 9/15…).
      • Falta el campo `partidos_futuros` → la auditoría es anterior a que el
        escaneo cruzara temporadas por `/api/leagues`.
      • Muestra corta sin la marca `cola_historica_revisada` → el escaneo no
        consta como haber mirado las demás competiciones del club. Medido en vivo
        con Deportivo A Coruña: 8/15 y «fuente agotada» cuando la fuente tenía 42
        partidos suyos de LaLiga2 2025/26, la liga en la que jugaba antes de
        ascender y que la cola no visitaba.

    Lo que NO se marca obsoleto, a propósito:
      • `paginas_escaneadas <= 1` habiendo alcanzado el objetivo (15/15 en una
        página). Se comprobó sobre los JSON reales que ese caso es legítimo en
        equipos cuyo historial reciente cabe en la primera página del calendario
        (4 equipos en disco lo cumplen y además traen `partidos_futuros`, es decir
        ya se escanearon con el cruce de temporadas). Marcarlo obsoleto obligaría a
        reescanear esos equipos en CADA consulta para siempre, porque el reescaneo
        volvería a dar 15/15 en una página: es exactamente el gasto inútil de
        peticiones que la invalidación perezosa quiere evitar.
      • Una muestra corta CON la marca `cola_historica_revisada`: el escaneo ya
        revisó todas las competiciones del club y no había más. Reescanear daría lo
        mismo, así que se reutiliza y solo se declara «INCOMPLETO».
    """
    esc = doc.get("escaneo") or {}
    res = doc.get("resumen") or {}
    paginas = esc.get("paginas_escaneadas")
    auditados = res.get("partidos_auditados")
    if auditados is None:
        auditados = res.get("partidos_con_datos")
    if auditados is None:
        auditados = len(doc.get("partidos") or [])

    # Una auditoría anterior a la incorporación de `partidos_futuros` no permite
    # resolver la fecha ni la localía reales de un cruce (caso Roma vs Real Madrid).
    # Se marca obsoleta para que se regenere al vuelo la próxima vez que se consulte.
    if "partidos_futuros" not in doc:
        return True, ("sin el campo de próximos partidos: no permite resolver la fecha "
                      "ni la localía de un cruce")

    try:
        auditados_i = int(auditados)
    except (TypeError, ValueError):
        auditados_i = 0

    # Huella MÁS ESPECÍFICA: una sola página con la muestra corta. Se comprueba antes
    # que la regla general para poder informar del motivo exacto.
    try:
        paginas_i = int(paginas) if paginas is not None else None
    except (TypeError, ValueError):
        paginas_i = None
    if paginas_i is not None and paginas_i <= 1:
        if auditados_i < int(limite):
            return True, (f"escaneo de una sola página con {auditados_i}/{limite} "
                          f"partidos: huella del bug anterior al cruce de temporadas")
        return False, ""

    # Regla general: muestra corta sin la marca de haber revisado TODAS las
    # competiciones del club (incluidas las temporadas en otra división). Se regenera
    # de forma perezosa, solo para el equipo que se consulte.
    if not res.get("objetivo_alcanzado") and auditados_i < int(limite) \
            and not esc.get("cola_historica_revisada"):
        return True, ("muestra corta escaneada sin revisar las demás competiciones del "
                      "club (marca ausente): un recién ascendido tenía sus partidos en "
                      "otra liga y quedaban fuera del escaneo")
    return False, ""


def cargar_o_crear_auditoria(
    equipo: str,
    limite: int = LIMITE_PARTIDOS_POR_DEFECTO,
    frescura_horas: float = FRESCURA_HORAS_POR_DEFECTO,
    forzar: bool = False,
    headless: bool = True,
    directorio: str | None = None,
    max_paginas: int = 8,
    progreso: Any = None,
    rival: str | dict[str, Any] | None = None,
    liga: dict[str, Any] | str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Devuelve (auditoría, meta) del equipo:
      • Si existe en memoria una auditoría de la sesión que ya cumplió el objetivo, se reutiliza.
      • Si existe una auditoría fresca que ya cumplió el objetivo, se reutiliza.
      • Si existe una auditoría fresca que NO llegó al objetivo pero cuyo escaneo ya
        había agotado la fuente, también se reutiliza: volver a escanear daría
        exactamente el mismo resultado (la fuente no expone más historial).
      • Si la auditoría lleva la huella del escaneo viejo o incoherencia geográfica/de identidad,
        NUNCA se reutiliza: se reescanea al vuelo para el equipo y rival consultados.
      • En cualquier otro caso se lanza el scraper. Si la nueva auditoría trajera
        MENOS partidos con datos que la anterior, se conserva la anterior.
    """
    liga_pref, equipo_limpio = extraer_liga_de_texto(equipo or "")
    equipo_efectivo = (equipo_limpio or equipo or "").strip()

    # Construir contexto enriquecido de rival + liga
    liga_info: dict[str, Any] | None = liga_pref
    if not liga_info and liga:
        if isinstance(liga, dict):
            liga_info = dict(liga)
        elif isinstance(liga, str) and liga.strip():
            liga_info = _inferir_meta_rival_desde_texto(liga.strip())

    rival_efectivo: Any = rival
    if liga_info:
        if isinstance(rival, dict):
            rival_efectivo = {**rival, **liga_info}
        elif isinstance(rival, str) and rival.strip():
            base_riv = _inferir_meta_rival_desde_texto(rival.strip()) or {"nombre": rival.strip()}
            rival_efectivo = {**base_riv, **liga_info}
        else:
            rival_efectivo = dict(liga_info)

    meta: dict[str, Any] = {"equipo_solicitado": equipo_efectivo, "origen": "reescaneada",
                            "archivo_previo": None, "antiguedad_horas": None,
                            "motivo_rescaneo": "", "archivo": None}
    if liga_info:
        meta["liga_solicitada"] = liga_info.get("nombre") or liga_info.get("liga")
    previo: dict[str, Any] | None = None

    if not forzar and directorio is None:
        # 1. Comprobar caché en memoria
        doc_mem = obtener_auditoria_en_memoria(_slug(equipo_efectivo))
        if doc_mem is not None:
            ok_mem, _ = _auditoria_corresponde_a_consulta(doc_mem, equipo_efectivo, rival=rival_efectivo)
            pais_doc = _pais_de_liga((doc_mem.get("equipo") or {}).get("liga"))
            pais_riv = (rival_efectivo.get("pais") if isinstance(rival_efectivo, dict) else "") or _pais_de_liga(
                rival_efectivo.get("liga") if isinstance(rival_efectivo, dict) else str(rival_efectivo or "")
            )
            es_incoherente = (not ok_mem) or bool(
                pais_doc and pais_riv and (
                    (pais_doc in PAISES_EUROPA and pais_riv in PAISES_SUDAMERICA)
                    or (pais_doc in PAISES_SUDAMERICA and pais_riv in PAISES_EUROPA)
                )
            )
            if not es_incoherente:
                res_mem = doc_mem.get("resumen") or {}
                con_datos = res_mem.get("partidos_con_datos") or len(doc_mem.get("partidos") or [])
                if con_datos >= limite:
                    meta = {"equipo_solicitado": equipo_efectivo, "origen": "reutilizada", "archivo_previo": None, "antiguedad_horas": 0.0, "motivo_rescaneo": "", "archivo": None}
                    if callable(progreso):
                        progreso(f"{equipo_efectivo}: auditoría en memoria reutilizada ({con_datos} partidos)")
                    return doc_mem, meta

    if not forzar:
        # 2. Comprobar disco
        ruta = buscar_auditoria(equipo_efectivo, directorio, rival=rival_efectivo)
        if ruta:
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                horas = _antiguedad_horas(ruta)
                resumen = doc.get("resumen") or {}
                escaneo_prev = doc.get("escaneo") or {}
                con_datos = resumen.get("partidos_con_datos") or 0
                reciente = horas <= frescura_horas
                objetivo_cumplido = bool(resumen.get("objetivo_alcanzado")) or con_datos >= limite
                fuente_agotada = bool(escaneo_prev.get("fuente_agotada")) or bool(
                    escaneo_prev.get("motivo_parada", "").startswith(
                        ("la fuente no ofrece más páginas", "tope de seguridad",
                         "la página", "historial agotado")))

                obsoleta, motivo_obsoleta = _es_auditoria_obsoleta(doc, limite)

                # Validar identidad del equipo en caché y coherencia geográfica/de liga
                ok_id, motivo_id = _auditoria_corresponde_a_consulta(doc, equipo_efectivo, rival=rival_efectivo)
                if not ok_id:
                    obsoleta = True
                    motivo_obsoleta = motivo_id
                else:
                    # Solo conservar `previo` como fallback si realmente corresponde al mismo club
                    previo = doc

                pais_doc = _pais_de_liga((doc.get("equipo") or {}).get("liga"))
                pais_riv = (rival_efectivo.get("pais") if isinstance(rival_efectivo, dict) else "") or _pais_de_liga(
                    rival_efectivo.get("liga") if isinstance(rival_efectivo, dict) else str(rival_efectivo or "")
                )
                if not obsoleta and pais_doc and pais_riv and (
                    (pais_doc in PAISES_EUROPA and pais_riv in PAISES_SUDAMERICA)
                    or (pais_doc in PAISES_SUDAMERICA and pais_riv in PAISES_EUROPA)
                ):
                    obsoleta = True
                    previo = None
                    motivo_obsoleta = f"incoherencia geográfica con rival ({doc.get('equipo',{}).get('nombre')} en {pais_doc} vs rival en {pais_riv})"

                if obsoleta:
                    meta["archivo_previo"] = ruta
                    meta["antiguedad_horas"] = round(horas, 2)
                    meta["motivo_rescaneo"] = f"auditoría obsoleta: {motivo_obsoleta}"
                    if callable(progreso):
                        progreso(f"{equipo_efectivo}: auditoría obsoleta, se reescanea ahora "
                                 f"({motivo_obsoleta})")
                elif reciente and (objetivo_cumplido or fuente_agotada):
                    meta.update({"origen": "reutilizada", "archivo_previo": ruta,
                                 "archivo": ruta, "antiguedad_horas": round(horas, 2)})
                    motivo_reuso = ("objetivo cumplido" if objetivo_cumplido
                                    else "fuente ya agotada en el escaneo previo")
                    if callable(progreso):
                        progreso(f"{equipo_efectivo}: auditoría reutilizada ({con_datos} partidos, "
                                 f"hace {horas:.1f} h, {motivo_reuso})")
                    return doc, meta
                else:
                    meta["archivo_previo"] = ruta
                    meta["antiguedad_horas"] = round(horas, 2)
                    if not reciente:
                        meta["motivo_rescaneo"] = (f"auditoría desactualizada "
                                                   f"({horas:.1f} h > {frescura_horas} h)")
                    else:
                        meta["motivo_rescaneo"] = (f"auditoría incompleta "
                                                   f"({con_datos} < {limite} partidos con datos)")
            except Exception as e:
                meta["motivo_rescaneo"] = f"auditoría previa ilegible ({type(e).__name__})"
        else:
            meta["motivo_rescaneo"] = "no existe auditoría previa"

    auditoria = auditar_equipo(equipo_efectivo, limite=limite, headless=headless, progreso=progreso,
                               mejor_previo=previo, rival=rival_efectivo)
    if previo is not None and auditoria is previo:
        # Se conservó la anterior: no se reescribe nada, solo se reporta
        meta["origen"] = "conservada"
        meta["archivo"] = buscar_auditoria(equipo_efectivo, directorio, rival=rival_efectivo)
        meta["motivo_rescaneo"] = (meta.get("motivo_rescaneo") or "") + \
            " · se conservó la auditoría previa por aportar más partidos"
        return auditoria, meta

    # Se persiste igualmente (queda cacheada para el próximo H2H). Se escribe con
    # marca de tiempo para NO sobrescribir la auditoría base del equipo, que puede
    # ser un snapshot histórico que el usuario quiera conservar.
    rutas = exportar_auditoria(auditoria, directorio=directorio, sufijo_tiempo="")
    meta["archivos_nuevos"] = rutas
    # Ruta canónica que el informe debe citar (la del markdown de la auditoría)
    meta["archivo"] = rutas.get("markdown")
    return auditoria, meta


# ═══════════════════════════════════════════════════════════════════════════════
# AGREGACIÓN ESTADÍSTICA
# ═══════════════════════════════════════════════════════════════════════════════

def _es_local(partido: dict[str, Any]) -> bool:
    return str(partido.get("condicion", "")).lower() == "casa"


def valores_de_equipo(partido: dict[str, Any], clave: str,
                      periodo: str = "FT") -> float | None:
    """Valor del equipo auditado para una métrica y periodo de un partido."""
    for fila in ((partido.get("periodos") or {}).get(periodo) or {}).get("filas", []):
        if fila.get("clave") == clave:
            valor = fila.get("equipo")
            return float(valor) if isinstance(valor, (int, float)) else None
    return None


def resumir(equipo: dict[str, Any], contexto: str | None = None,
            claves: list[str] | None = None, periodo: str = "FT") -> dict[str, Any]:
    """
    Promedia las métricas de los partidos de una auditoría, opcionalmente filtrando
    por condición del equipo auditado ("casa" o "fuera").

    Devuelve, por métrica: n (partidos con dato), promedio, mínimo, máximo y, si es
    un porcentaje auténtico, cuántos partidos alcanzan los umbrales 70/80/90 %.
    """
    claves = claves or [d.clave for d in CATALOGO_METRICAS]
    partidos = [p for p in equipo.get("partidos", [])
                if contexto is None or str(p.get("condicion", "")).lower() == contexto]

    salida: dict[str, Any] = {
        "equipo": (equipo.get("equipo") or {}).get("nombre") or equipo.get("equipo_solicitado"),
        "contexto": contexto or "global",
        "n_partidos": len(partidos),
        "metricas": {},
    }
    for clave in claves:
        serie = [v for v in (valores_de_equipo(p, clave, periodo) for p in partidos) if v is not None]
        if not serie:
            salida["metricas"][clave] = {"n": 0, "promedio": None, "min": None, "max": None}
            continue
        entrada: dict[str, Any] = {
            "n": len(serie),
            "promedio": round(sum(serie) / len(serie), 2),
            "min": min(serie),
            "max": max(serie),
        }
        if clave in PORCENTAJES_AUTENTICOS:
            for umbral in (70.0, 80.0, 90.0):
                alcanzan = sum(1 for v in serie if v >= umbral)
                entrada[f"n_ge_{int(umbral)}"] = alcanzan
                entrada[f"pct_ge_{int(umbral)}"] = round(alcanzan / len(serie) * 100, 1)
        salida["metricas"][clave] = entrada
    return salida


def _goles_del_partido(partido: dict[str, Any]) -> tuple[Any, Any]:
    """
    Goles (del equipo auditado, del rival) de un partido.
    Soporta las dos formas del registro: campos planos (en memoria) y el bloque
    anidado `marcador` (tal como queda en el JSON exportado).
    """
    if partido.get("goles_equipo") is not None or partido.get("goles_rival") is not None:
        return partido.get("goles_equipo"), partido.get("goles_rival")
    marcador = partido.get("marcador") or {}
    return marcador.get("equipo"), marcador.get("rival")


def _cruces_directos(doc_a: dict[str, Any], doc_b: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Partidos que YA están en los datasets y enfrentan a ambos equipos entre sí.
    Se deduplica por id de partido (cada auditoría lo registra desde su óptica, con
    los goles anotados por cada uno).
    """
    nombre_a = ((doc_a.get("equipo") or {}).get("nombre") or "").lower()
    nombre_b = ((doc_b.get("equipo") or {}).get("nombre") or "").lower()
    vistos: dict[str, dict[str, Any]] = {}

    def _recolectar(doc: dict[str, Any], rival_esperado: str) -> None:
        for p in doc.get("partidos", []):
            rival = str(p.get("rival") or "").lower()
            if not rival or rival_esperado not in rival:
                continue
            pid = str(p.get("id_partido"))
            if pid in vistos:
                continue
            goles_equipo, goles_rival = _goles_del_partido(p)
            es_casa = _es_local(p)
            vistos[pid] = {
                "id_partido": pid,
                "fecha": p.get("fecha"),
                "torneo": p.get("torneo"),
                "local": (p.get("equipo") if es_casa else p.get("rival")),
                "visitante": (p.get("rival") if es_casa else p.get("equipo")),
                "goles_local": (goles_equipo if es_casa else goles_rival),
                "goles_visitante": (goles_rival if es_casa else goles_equipo),
                "registrado_en": (doc.get("equipo") or {}).get("nombre"),
            }

    _recolectar(doc_a, nombre_b)
    _recolectar(doc_b, nombre_a)
    return sorted(vistos.values(), key=lambda x: str(x.get("fecha")), reverse=True)


def _cobertura_combinada(doc_a: dict[str, Any], doc_b: dict[str, Any]) -> dict[str, Any]:
    """Cobertura de métricas de los dos datasets, en FT."""
    def por_clave(doc: dict[str, Any]) -> dict[str, int]:
        conteo: dict[str, int] = {}
        for p in doc.get("partidos", []):
            for fila in ((p.get("periodos") or {}).get("FT") or {}).get("filas", []):
                if fila.get("equipo") is not None:
                    conteo[fila["clave"]] = conteo.get(fila["clave"], 0) + 1
        return conteo

    a, b = por_clave(doc_a), por_clave(doc_b)
    nombre_a = (doc_a.get("equipo") or {}).get("nombre")
    nombre_b = (doc_b.get("equipo") or {}).get("nombre")
    n_a, n_b = len(doc_a.get("partidos", [])), len(doc_b.get("partidos", []))
    etiquetas = _etiquetas()

    filas = []
    for definicion in CATALOGO_METRICAS:
        ca, cb = a.get(definicion.clave, 0), b.get(definicion.clave, 0)
        filas.append({
            "clave": definicion.clave,
            "metrica": definicion.etiqueta,
            "categoria": definicion.categoria,
            "n_a": ca, "n_b": cb,
            "pct_a": round(ca / n_a * 100, 1) if n_a else 0.0,
            "pct_b": round(cb / n_b * 100, 1) if n_b else 0.0,
            "comparable": ca > 0 and cb > 0,
            "solo_a": ca > 0 and cb == 0,
            "solo_b": cb > 0 and ca == 0,
        })
    comparables = [f for f in filas if f["comparable"]]
    return {
        "equipo_a": nombre_a, "equipo_b": nombre_b,
        "partidos_a": n_a, "partidos_b": n_b,
        "filas": filas,
        "n_comparables": len(comparables),
        "n_solo_a": len([f for f in filas if f["solo_a"]]),
        "n_solo_b": len([f for f in filas if f["solo_b"]]),
        "solo_a": [f["metrica"] for f in filas if f["solo_a"]],
        "solo_b": [f["metrica"] for f in filas if f["solo_b"]],
        "sin_datos_en_ninguno": [f["metrica"] for f in filas
                                 if f["n_a"] == 0 and f["n_b"] == 0],
    }


def _desglose_partidos(doc: dict[str, Any]) -> str:
    """Texto corto: 'N con datos (X oficiales + Y amistosos)'."""
    partidos = doc.get("partidos", [])
    total = len(partidos)
    amistosos = sum(1 for p in partidos if p.get("es_amistoso"))
    oficiales = total - amistosos
    if amistosos:
        return f"{total} con datos ({oficiales} oficiales + {amistosos} amistosos)"
    return f"{total} oficiales"


def _alertas_umbral(res_a: dict[str, Any], res_b: dict[str, Any],
                    umbral_min: float = 70.0,
                    etiqueta_contexto: str = "") -> list[dict[str, Any]]:
    """
    Alertas de rendimiento con umbral, calculadas sobre los bloques que se le pasen
    (normalmente: equipo A como LOCAL y equipo B como VISITANTE).
      • "porcentaje": métricas porcentuales cuyo promedio alcanza `umbral_min` y,
        además, un porcentaje alto de partidos lo supera.
      • "referencia": métricas de dominio posicional/ofensivo que superan el
        umbral de referencia declarado en UMBRALES_REFERENCIA (el umbral se publica
        en el informe, no es una probabilidad).
    """
    etiquetas = _etiquetas()
    alertas: list[dict[str, Any]] = []
    for resumen in (res_a, res_b):
        for clave, datos in resumen["metricas"].items():
            if not datos.get("n") or datos.get("promedio") is None:
                continue
            promedio = datos["promedio"]
            base = {
                "equipo": resumen["equipo"], "contexto": resumen["contexto"],
                "clave": clave, "metrica": etiquetas.get(clave, clave),
                "promedio": promedio, "n": datos["n"],
                "min": datos["min"], "max": datos["max"],
                "escenario": etiqueta_contexto,
            }
            if clave in PORCENTAJES_AUTENTICOS and promedio >= umbral_min:
                pct_partidos = datos.get(f"pct_ge_{int(umbral_min)}")
                alertas.append({
                    **base, "tipo": "porcentaje", "umbral": umbral_min,
                    "pct_partidos_sobre_umbral": pct_partidos,
                    "detalle": (f"promedio {_fmt(promedio)}% en {datos['n']} partidos; "
                                f"{pct_partidos}% de ellos superó el {int(umbral_min)}%"),
                })
            elif clave in UMBRALES_REFERENCIA:
                ref = UMBRALES_REFERENCIA[clave]
                if promedio >= ref["umbral"]:
                    alertas.append({
                        **base, "tipo": "referencia", "umbral": ref["umbral"],
                        "pct_partidos_sobre_umbral": None,
                        "detalle": (f"{ref['etiqueta']} (promedio {_fmt(promedio)} "
                                    f"en {datos['n']} partidos)"),
                    })
    alertas.sort(key=lambda x: (0 if x["tipo"] == "porcentaje" else 1, -x["promedio"]))
    return alertas


# ═══════════════════════════════════════════════════════════════════════════════
# INFORME MARKDOWN CONSOLIDADO
# ═══════════════════════════════════════════════════════════════════════════════

def generar_markdown_h2h(analisis: dict[str, Any]) -> str:
    a = analisis["equipo_a"]
    b = analisis["equipo_b"]
    cobertura = analisis["cobertura"]
    nombre_a = cobertura["equipo_a"] or a["equipo_solicitado"]
    nombre_b = cobertura["equipo_b"] or b["equipo_solicitado"]
    lineas: list[str] = []

    lineas.append(f"# Enfrentamiento directo — {nombre_a} vs {nombre_b}")
    lineas.append(f"**Generado**: {analisis['generado_en']} ({analisis['zona_horaria']})  ")
    lineas.append(f"**Fuente de datos**: {analisis['fuente']}  ")
    con_relleno = any((meta.get("_doc") or {}).get("incluir_amistosos")
                      for meta in (a, b))
    lineas.append(f"**Ventana solicitada**: últimos {analisis['limite']} partidos con datos por "
                  f"equipo (amistosos "
                  f"{'usados solo como relleno si faltan oficiales' if con_relleno else 'excluidos'})  ")
    def _marca_auditoria(meta: dict[str, Any]) -> str:
        horas = meta.get("antiguedad_horas")
        return f"`{meta['origen']}`" + (f" (hace {horas} h)" if horas is not None else "")

    lineas.append(f"**Auditorías**: {nombre_a} → {_marca_auditoria(a)} · "
                  f"{nombre_b} → {_marca_auditoria(b)}  ")
    lineas.append("")

    # Trazabilidad: de qué archivo salió cada dataset
    lineas.append("**Archivos de auditoría utilizados**")
    lineas.append("")
    for meta in (a, b):
        ruta = meta.get("archivo") or "—"
        etiqueta = meta.get("equipo_solicitado", "?")
        lineas.append(f"- {etiqueta}: `{ruta}` ({meta.get('origen')})")
    lineas.append("")

    # ── 0. Avisos de honestidad de datos ───────────────────────────────────
    avisos: list[str] = []
    for meta in (a, b):
        if meta.get("advertencias"):
            for adv in meta["advertencias"]:
                avisos.append(f"**{meta['equipo_solicitado']}**: {adv}")
        if meta.get("motivo_rescaneo") and meta.get("origen") == "reescaneada":
            avisos.append(f"**{meta['equipo_solicitado']}**: auditoría regenerada "
                          f"({meta['motivo_rescaneo']}).")
    if avisos:
        lineas.append("> ⚠️ **Notas de datos**")
        for adv in avisos:
            lineas.append(f"> - {adv}")
        lineas.append("")

    # ── 1. COBERTURA COMBINADA ─────────────────────────────────────────────
    lineas.append("---")
    lineas.append("## 1. Cobertura de métricas combinada")
    lineas.append("")
    lineas.append(f"- **{nombre_a}**: {cobertura['partidos_a']} partidos auditados "
                  f"({_desglose_partidos(a.get('_doc') or {})})")
    lineas.append(f"- **{nombre_b}**: {cobertura['partidos_b']} partidos auditados "
                  f"({_desglose_partidos(b.get('_doc') or {})})")
    lineas.append(f"- **Métricas comparables en ambos**: {cobertura['n_comparables']} "
                  f"de {len(cobertura['filas'])}")
    if cobertura["solo_a"]:
        lineas.append(f"- ⚠️ Solo disponibles para {nombre_a}: {', '.join(cobertura['solo_a'])}")
    if cobertura["solo_b"]:
        lineas.append(f"- ⚠️ Solo disponibles para {nombre_b}: {', '.join(cobertura['solo_b'])}")
    if cobertura["sin_datos_en_ninguno"]:
        lineas.append(f"- ❌ No publicadas por la fuente para ninguno: "
                      f"{', '.join(cobertura['sin_datos_en_ninguno'])}")
    lineas.append("")
    lineas.append(f"| Métrica | {nombre_a} | {nombre_b} | Comparable |")
    lineas.append("|---|---|---|---|")
    for fila in cobertura["filas"]:
        marca = "✅" if fila["comparable"] else ("➖ sin datos en uno" if not (
            fila["n_a"] == 0 and fila["n_b"] == 0) else "❌")
        lineas.append(f"| {fila['metrica']} | {fila['pct_a']}% ({fila['n_a']}) | "
                      f"{fila['pct_b']}% ({fila['n_b']}) | {marca} |")
    lineas.append("")

    # ── 2. MATRIZ DE ENFRENTAMIENTO DIRECTO ────────────────────────────────
    lineas.append("---")
    lineas.append("## 2. Matriz de enfrentamiento directo")
    lineas.append("")
    lineas.append("Promedios de los partidos oficiales auditados. `Local` = partidos en que el "
                  "equipo jugó en casa; `Visitante` = fuera. Los nulos indican que la fuente no "
                  "publica esa métrica.")
    lineas.append("")

    for categoria in CATEGORIAS_ORDEN:
        claves_cat = [d.clave for d in CATALOGO_METRICAS if d.categoria == categoria]
        # Solo se muestran las filas con algún dato en cualquiera de los 4 bloques
        bloques = {
            "a_local": analisis["resumenes"]["a_local"],
            "a_fuera": analisis["resumenes"]["a_fuera"],
            "b_local": analisis["resumenes"]["b_local"],
            "b_fuera": analisis["resumenes"]["b_fuera"],
        }
        filas_con_dato = [
            clave for clave in claves_cat
            if any(bloques[k]["metricas"][clave]["n"] for k in bloques)
        ]
        if not filas_con_dato:
            continue

        lineas.append(f"### {categoria}")
        lineas.append("")
        lineas.append(f"| Métrica | {nombre_a} Local | {nombre_a} Visitante | "
                      f"{nombre_b} Local | {nombre_b} Visitante |")
        lineas.append("|---|---|---|---|---|")
        for clave in filas_con_dato:
            etiqueta = _etiquetas().get(clave, clave)
            celdas = []
            for k in ("a_local", "a_fuera", "b_local", "b_fuera"):
                datos = bloques[k]["metricas"][clave]
                celdas.append("—" if datos["n"] == 0 else _fmt(datos["promedio"]))
            lineas.append(f"| {etiqueta} | " + " | ".join(celdas) + " |")
        lineas.append("")
        linea_n = (f"*Partidos por bloque* — {nombre_a}: "
                   f"{bloques['a_local']['n_partidos']} local / {bloques['a_fuera']['n_partidos']} "
                   f"visitante · {nombre_b}: {bloques['b_local']['n_partidos']} local / "
                   f"{bloques['b_fuera']['n_partidos']} visitante.")
        lineas.append(linea_n)
        lineas.append("")

    # Cruces directos ya registrados
    lineas.append("### Cruces directos registrados en los datasets")
    lineas.append("")
    if analisis["cruces_directos"]:
        lineas.append("| Fecha | Competición | Local | Resultado | Visitante |")
        lineas.append("|---|---|---|---|---|")
        for c in analisis["cruces_directos"]:
            marcador = (f"{_fmt(c['goles_local'])}-{_fmt(c['goles_visitante'])}"
                        if c.get("goles_local") is not None else "—")
            lineas.append(f"| {c['fecha']} | {c['torneo']} | {c['local']} | "
                          f"**{marcador}** | {c['visitante']} |")
    else:
        lineas.append("_No hay enfrentamientos directos entre ambos equipos dentro de la ventana "
                      "de partidos auditados. La comparación se basa en los promedios, no en un "
                      "historial de cruces._")
    lineas.append("")

    # ── 3. ANÁLISIS DE PROBABILIDADES Y MERCADOS ───────────────────────────
    lineas.append("---")
    lineas.append("## 3. Análisis de probabilidades y mercados (umbrales ≥ 70%)")
    lineas.append("")
    lineas.append("> Los valores son **frecuencias observadas** en los partidos auditados, no "
                  "probabilidades de apuesta calculadas. El umbral del 70% se aplica a métricas "
                  "porcentuales auténticas (posesión, precisión de pase, cruces, duelos). Las "
                  "métricas de dominio usan el umbral de referencia que se indica en cada fila. "
                  "El análisis se hace **por escenario de cruce**: el equipo en su condición de "
                  "local frente al otro en su condición de visitante, tal como pide el flujo.")
    lineas.append("")

    for i, esc in enumerate(analisis.get("escenarios", []), 1):
        resumenes = analisis["resumenes"]
        bloque_a = resumenes[esc["a"]]
        bloque_b = resumenes[esc["b"]]
        lineas.append(f"### 3.{i} Escenario: {esc['titulo']}")
        lineas.append("")
        lineas.append(f"- **{bloque_a['equipo']} como local**: {bloque_a['n_partidos']} partidos "
                      f"oficiales en casa")
        lineas.append(f"- **{bloque_b['equipo']} como visitante**: {bloque_b['n_partidos']} "
                      f"partidos oficiales fuera")
        lineas.append("")

        pct = [x for x in esc["alertas"] if x["tipo"] == "porcentaje"]
        ref = [x for x in esc["alertas"] if x["tipo"] == "referencia"]

        if pct:
            lineas.append("**Métricas porcentuales con promedio ≥ 70% en este escenario**")
            lineas.append("")
            lineas.append("| Equipo | Condición | Métrica | Promedio | Rango | Partidos | "
                          "% de partidos ≥ 70% |")
            lineas.append("|---|---|---|---|---|---|---|")
            for x in pct:
                lineas.append(f"| {x['equipo']} | {x['contexto']} | {x['metrica']} | "
                              f"**{_fmt(x['promedio'])}%** | {_fmt(x['min'])}–{_fmt(x['max'])} | "
                              f"{x['n']} | {_fmt(x['pct_partidos_sobre_umbral'], 1)}% |")
            lineas.append("")
        else:
            lineas.append("_Ninguna métrica porcentual alcanza el 70% de promedio en este "
                          "escenario._")
            lineas.append("")

        if ref:
            lineas.append("**Métricas de dominio con umbral de referencia superado**")
            lineas.append("")
            lineas.append("| Equipo | Condición | Métrica | Promedio | Umbral de referencia | "
                          "Rango | Partidos |")
            lineas.append("|---|---|---|---|---|---|---|")
            for x in ref:
                etiqueta_umbral = UMBRALES_REFERENCIA[x["clave"]]["etiqueta"]
                lineas.append(f"| {x['equipo']} | {x['contexto']} | {x['metrica']} | "
                              f"**{_fmt(x['promedio'])}** | {etiqueta_umbral} | "
                              f"{_fmt(x['min'])}–{_fmt(x['max'])} | {x['n']} |")
            lineas.append("")
        else:
            lineas.append("_Ninguna métrica de dominio supera su umbral de referencia en este "
                          "escenario._")
            lineas.append("")

        # Comparación directa de este escenario, métrica a métrica
        lineas.append("**Comparación directa en este escenario**")
        lineas.append("")
        comparaciones = [
            ("posesion_pct", "Posesión", "%"),
            ("pases_precision_pct", "Precisión de pase", "%"),
            ("remates_totales", "Remates totales", ""),
            ("remates_puerta", "Remates a puerta", ""),
            ("grandes_ocasiones", "Grandes ocasiones", ""),
            ("corners", "Córneres", ""),
            ("toques_area_rival", "Toques en el área rival", ""),
            ("duelos_suelo_pct", "Duelos de suelo ganados", "%"),
            ("duelos_aereos_pct", "Duelos aéreos ganados", "%"),
            ("xg", "Goles esperados (xG)", ""),
            ("xgot", "xGOT", ""),
            ("xa_equipo", "Asistencias esperadas (xA)", ""),
        ]
        hay = False
        for clave, etiqueta, sufijo in comparaciones:
            v_a = bloque_a["metricas"].get(clave, {}).get("promedio")
            v_b = bloque_b["metricas"].get(clave, {}).get("promedio")
            if v_a is None and v_b is None:
                continue
            hay = True
            if v_a is not None and v_b is not None and abs(v_a - v_b) > 1e-9:
                ganador = bloque_a["equipo"] if v_a > v_b else bloque_b["equipo"]
                ventaja = f" → ventaja de **{ganador}**"
            else:
                ventaja = " → sin diferencia medible" if v_a is not None and v_b is not None else ""
            lineas.append(f"- **{etiqueta}**: {bloque_a['equipo']} {_fmt(v_a)}{sufijo} · "
                          f"{bloque_b['equipo']} {_fmt(v_b)}{sufijo}{ventaja}")
        if not hay:
            lineas.append("- _Sin métricas comparables entre estos dos bloques._")
        lineas.append("")

    lineas.append("---")
    lineas.append("*Informe generado automáticamente a partir de las auditorías históricas de "
                  "ambos equipos. Las métricas ausentes se muestran como «—» y no se imputan "
                  "valores. Este documento es análisis estadístico, no una recomendación de "
                  "apuesta: juega de manera responsable.*")
    return "\n".join(lineas)


# ═══════════════════════════════════════════════════════════════════════════════
# ORQUESTACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

def generar_h2h(
    equipo_a: str,
    equipo_b: str,
    limite: int = LIMITE_PARTIDOS_POR_DEFECTO,
    frescura_horas: float = FRESCURA_HORAS_POR_DEFECTO,
    forzar: bool = False,
    headless: bool = True,
    directorio: str | None = None,
    directorio_salida: str | None = None,
    progreso: Any = None,
    liga: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """
    Ejecuta el flujo H2H completo y escribe el informe consolidado.
    Devuelve el dict de análisis (incluye `rutas`).
    """
    def avisar(msg: str) -> None:
        print(f"[H2H] {msg}")
        if callable(progreso):
            try:
                progreso(msg)
            except Exception:
                pass

    liga_a, limpio_a = extraer_liga_de_texto(equipo_a or "")
    liga_b, limpio_b = extraer_liga_de_texto(equipo_b or "")
    equipo_a = (limpio_a or equipo_a or "").strip()
    equipo_b = (limpio_b or equipo_b or "").strip()
    liga_ctx = liga_a or liga_b or liga

    ahora = datetime.now(timezone.utc).astimezone()
    analisis: dict[str, Any] = {
        "generado_en": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "zona_horaria": config.TIMEZONE,
        "limite": limite,
        "fuente": None,
        "equipo_a": {}, "equipo_b": {},
        "resumenes": {}, "cobertura": {}, "alertas": [], "cruces_directos": [],
        "rutas": {},
    }

    # 1) Auditorías (reutilizar o crear)
    avisar(f"equipo A: «{equipo_a}» → buscando auditoría…")
    doc_a, meta_a = cargar_o_crear_auditoria(
        equipo_a, limite=limite, frescura_horas=frescura_horas, forzar=forzar,
        headless=headless, directorio=directorio, progreso=avisar, rival=equipo_b,
        liga=liga_ctx)
    meta_a["equipo_solicitado"] = equipo_a
    meta_a["advertencias"] = doc_a.get("advertencias", [])
    meta_a["_doc"] = doc_a
    avisar(f"equipo A: {meta_a['origen']} "
           f"({(doc_a.get('resumen') or {}).get('partidos_con_datos')} partidos con datos)")

    avisar(f"equipo B: «{equipo_b}» → buscando auditoría…")
    doc_b, meta_b = cargar_o_crear_auditoria(
        equipo_b, limite=limite, frescura_horas=frescura_horas, forzar=forzar,
        headless=headless, directorio=directorio, progreso=avisar, rival=doc_a.get("equipo") or equipo_a,
        liga=liga_ctx)
    meta_b["equipo_solicitado"] = equipo_b
    meta_b["advertencias"] = doc_b.get("advertencias", [])
    meta_b["_doc"] = doc_b
    avisar(f"equipo B: {meta_b['origen']} "
           f"({(doc_b.get('resumen') or {}).get('partidos_con_datos')} partidos con datos)")

    analisis["equipo_a"], analisis["equipo_b"] = meta_a, meta_b
    analisis["fuente"] = doc_a.get("fuente") or doc_b.get("fuente")

    # 2) Agregaciones
    comparables = [d.clave for d in CATALOGO_METRICAS
                   if any(valores_de_equipo(p, d.clave) is not None for p in doc_a.get("partidos", []))
                   and any(valores_de_equipo(p, d.clave) is not None for p in doc_b.get("partidos", []))]
    analisis["claves_comparables"] = comparables
    avisar(f"{len(comparables)} métricas comparables entre ambos equipos")

    resumenes = {
        "a_local": resumir(doc_a, "casa"),
        "a_fuera": resumir(doc_a, "fuera"),
        "a_global": resumir(doc_a),
        "b_local": resumir(doc_b, "casa"),
        "b_fuera": resumir(doc_b, "fuera"),
        "b_global": resumir(doc_b),
    }
    analisis["resumenes"] = resumenes

    analisis["cobertura"] = _cobertura_combinada(doc_a, doc_b)
    # Análisis de umbrales por ESCENARIO de cruce: cada equipo en la condición en
    # que jugaría el partido (local de un lado, visitante del otro).
    analisis["escenarios"] = [
        {
            "titulo": f"{resumenes['a_local']['equipo']} en casa vs "
                      f"{resumenes['b_fuera']['equipo']} fuera",
            "a": "a_local", "b": "b_fuera",
            "alertas": _alertas_umbral(resumenes["a_local"], resumenes["b_fuera"],
                                       etiqueta_contexto="local/visitante"),
        },
        {
            "titulo": f"{resumenes['b_local']['equipo']} en casa vs "
                      f"{resumenes['a_fuera']['equipo']} fuera",
            "a": "b_local", "b": "a_fuera",
            "alertas": _alertas_umbral(resumenes["b_local"], resumenes["a_fuera"],
                                       etiqueta_contexto="local/visitante"),
        },
    ]
    analisis["alertas"] = [x for esc in analisis["escenarios"] for x in esc["alertas"]]
    analisis["cruces_directos"] = _cruces_directos(doc_a, doc_b)

    # 3) Informe consolidado
    nombre_a = analisis["cobertura"]["equipo_a"] or equipo_a
    nombre_b = analisis["cobertura"]["equipo_b"] or equipo_b
    base_dir = directorio_salida or os.path.join(H2H_DIR, ahora.strftime("%Y-%m-%d"))
    os.makedirs(base_dir, exist_ok=True)
    raiz = os.path.join(base_dir, f"auditoria_{_slug(nombre_a)}_vs_{_slug(nombre_b)}_"
                                  f"{ahora.strftime('%Y-%m-%d_%H%M%S')}")
    analisis["rutas"] = {
        "markdown": raiz + ".md",
        "json": raiz + ".json",
    }
    with open(analisis["rutas"]["markdown"], "w", encoding="utf-8") as f:
        f.write(generar_markdown_h2h(analisis))
    # El JSON no debe arrastrar los documentos completos de cada auditoría
    # (ya están en su propio archivo): se exporta una copia sin `_doc`.
    exportable = dict(analisis)
    exportable["equipo_a"] = {k: v for k, v in analisis["equipo_a"].items() if k != "_doc"}
    exportable["equipo_b"] = {k: v for k, v in analisis["equipo_b"].items() if k != "_doc"}
    with open(analisis["rutas"]["json"], "w", encoding="utf-8") as f:
        json.dump(exportable, f, ensure_ascii=False, indent=1, default=str)

    avisar(f"informe consolidado escrito: {analisis['rutas']['markdown']}")
    return analisis


def resumen_telegram(analisis: dict[str, Any]) -> str:
    """Resumen compacto para el chat de Telegram."""
    cov = analisis["cobertura"]
    nombre_a = cov["equipo_a"] or analisis["equipo_a"]["equipo_solicitado"]
    nombre_b = cov["equipo_b"] or analisis["equipo_b"]["equipo_solicitado"]
    r = analisis["resumenes"]
    partes = [
        f"🤝 ENFRENTAMIENTO DIRECTO: {nombre_a} vs {nombre_b}",
        "━" * 28,
        f"• {nombre_a}: {_desglose_partidos(analisis['equipo_a'].get('_doc') or {})} "
        f"({analisis['equipo_a']['origen']})",
        f"• {nombre_b}: {_desglose_partidos(analisis['equipo_b'].get('_doc') or {})} "
        f"({analisis['equipo_b']['origen']})",
        f"• Métricas comparables: {cov['n_comparables']}/{len(cov['filas'])}",
        f"• Cruces directos en los datasets: {len(analisis['cruces_directos'])}",
        "━" * 28,
    ]

    # Rutas de las auditorías usadas: para que el usuario sepa dónde mirar
    for meta in (analisis["equipo_a"], analisis["equipo_b"]):
        ruta = meta.get("archivo")
        if ruta:
            partes.append(f"📁 Auditoría de {meta['equipo_solicitado']} ({meta['origen']}):")
            partes.append(f"   {os.path.basename(str(ruta))}")
    partes.append("━" * 28)

    pares = [("posesion_pct", "Posesión", "%"), ("pases_precision_pct", "Precisión pase", "%"),
             ("remates_totales", "Remates", ""), ("remates_puerta", "A puerta", ""),
             ("grandes_ocasiones", "Grandes ocasiones", ""), ("xg", "xG", ""), ("xgot", "xGOT", "")]

    for esc in analisis.get("escenarios", []):
        propio = r[esc["a"]]
        rival = r[esc["b"]]
        partes.append(f"🏠 {esc['titulo']}:")
        for clave, etiqueta, sufijo in pares:
            vp = propio["metricas"].get(clave, {}).get("promedio")
            vr = rival["metricas"].get(clave, {}).get("promedio")
            if vp is None and vr is None:
                continue
            flecha = ""
            if vp is not None and vr is not None and abs(vp - vr) > 1e-9:
                flecha = f"  ← {propio['equipo'] if vp > vr else rival['equipo']}"
            partes.append(f"   {etiqueta}: {_fmt(vp)}{sufijo} vs {_fmt(vr)}{sufijo}{flecha}")

    pct = [x for x in analisis["alertas"] if x["tipo"] == "porcentaje"]
    partes.append("━" * 28)
    if pct:
        partes.append("📈 MÉTRICAS ≥ 70% (frecuencia observada, por escenario):")
        for x in pct[:6]:
            partes.append(f"   • {x['equipo']} ({x['contexto']}): {x['metrica']} "
                          f"{_fmt(x['promedio'])}% en {x['n']} partidos")
    else:
        partes.append("📈 Sin métricas porcentuales con promedio ≥ 70% en ningún escenario.")
    partes.append("━" * 28)
    partes.append("📎 Adjunto el informe consolidado con la matriz completa.")
    return "\n".join(partes)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRADA POR LÍNEA DE COMANDOS
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Enfrentamiento directo (H2H) entre dos equipos auditados")
    parser.add_argument("cruces", nargs="+",
                        help='Formato: "Equipo A vs Equipo B" (o "A - B")')
    parser.add_argument("--limite", type=int, default=LIMITE_PARTIDOS_POR_DEFECTO,
                        help=f"Partidos oficiales por equipo (por defecto {LIMITE_PARTIDOS_POR_DEFECTO})")
    parser.add_argument("--frescura-horas", type=float, default=FRESCURA_HORAS_POR_DEFECTO,
                        help="Antigüedad máxima aceptada antes de reescanear una auditoría")
    parser.add_argument("--forzar", action="store_true",
                        help="Ignora las auditorías existentes y reescanea ambos equipos")
    parser.add_argument("--visible", action="store_true", help="Navegador en pantalla (depuración)")
    args = parser.parse_args()

    texto = " ".join(args.cruces)
    equipos = extraer_equipos(texto)
    if not equipos:
        print(f'❌ No se detectó un cruce en {texto!r}. Usa el formato "Equipo A vs Equipo B".')
        return 2
    equipo_a, equipo_b = equipos

    try:
        analisis = generar_h2h(equipo_a, equipo_b, limite=args.limite,
                              frescura_horas=args.frescura_horas, forzar=args.forzar,
                              headless=not args.visible)
    except Exception as e:
        print(f"\n❌ El enfrentamiento falló: {type(e).__name__}: {e}")
        return 1

    cov = analisis["cobertura"]
    print("\n" + "=" * 72)
    print(f"  ENFRENTAMIENTO: {cov['equipo_a']} vs {cov['equipo_b']}")
    print(f"  Partidos: {cov['partidos_a']} vs {cov['partidos_b']} (con datos)")
    print(f"    {cov['equipo_a']}: "
          f"{_desglose_partidos(analisis['equipo_a'].get('_doc') or {})}")
    print(f"    {cov['equipo_b']}: "
          f"{_desglose_partidos(analisis['equipo_b'].get('_doc') or {})}")
    print(f"  Cobertura: {cov['n_comparables']}/{len(cov['filas'])} métricas comparables")
    print(f"  Cruces directos registrados: {len(analisis['cruces_directos'])}")
    for esc in analisis.get("escenarios", []):
        n_pct = len([x for x in esc["alertas"] if x["tipo"] == "porcentaje"])
        n_ref = len([x for x in esc["alertas"] if x["tipo"] == "referencia"])
        print(f"  Escenario «{esc['titulo']}»: {n_pct} métricas ≥ 70%, "
              f"{n_ref} umbrales de referencia superados")
    print("  Archivos:")
    for tipo, ruta in analisis["rutas"].items():
        print(f"    - {tipo:9} {ruta}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
