"""
Módulo Independiente: Cuadro Especialista de Enfrentamientos Directos (H2H Puro 2024–2026).
===========================================================================================
- Totalmente SEPARADO de la muestra de 15 partidos generales de cada equipo (no mezcla datos).
- Filtra estrictamente los enfrentamientos directos entre Equipo A y Equipo B desde 2024-01-01 hasta 2026.
- Extrae las 45 estadísticas de cada duelo directo (orientando por ID de equipo sin importar quién fue local
  en cada cruce de 2024-2026) y construye un Cuadro Especialista H2H exclusivo (Ratings 0-100 en duelos directos,
  Segundos por Tiro/Gol en H2H, Lista de Marcadores 2024-2026, Radiógrafo 1T/2T en H2H y Mapa de Calor H2H).
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import config
from cuadro_especialista import (
    PESO_PARTIDO_CON_ROJA,
    bloque_desde_muestra,
    construir_perfil_especialista_equipo,
    matriz_red_para_dibujar as red_para_dibujar,
    perfil_mitades,
    partidos_para_red_neuronal,
)
from fuentes_xg import (
    CATALOGO_METRICAS,
    FuenteFotMob,
    PartidoFinalizado,
    _goles_desde_texto,
    consolidar_matriz_completa,
    es_competicion_amistosa,
)
import motor_mercados as mm
import motor_recursivo_45_dcn as motor_rec


ANIO_MINIMO_H2H = 2024
ANIO_MAXIMO_H2H = 2026
MENSAJE_SIN_H2H_2024_2026 = "H2H Directo 2024–2026: 0 partidos (Comparativa contra rivales de nivel equivalente)"

# Temporadas que pueden contener partidos jugados entre 2024-01-01 y 2026-12-31.
# Nota: "2023/2024" se inspecciona porque su segunda vuelta ocurre entre enero y mayo
# de 2024 (p. ej. Real Betis 2-4 Barcelona del 2024-01-21), y luego el filtro estricto
# `filtrar_enfrentamientos_2024_2026` descarta cualquier partido jugado en 2023 o antes.
TEMPORADAS_BUSQUEDA_H2H = (
    "2026/2027",
    "2026",
    "2025/2026",
    "2025",
    "2024/2025",
    "2024",
    "2023/2024",
)

BASE_DIR = Path(__file__).resolve().parent
CACHE_H2H_DIR = str(Path(config.INFORME_DIR) / "h2h" / "cache_h2h_2024_2026")


def construir_matriz_dixon_coles(
    lam_a: float,
    lam_b: float,
    rho: float = -0.08,
    max_goles: int = 15,
) -> List[List[float]]:
    """
    Construye la matriz de probabilidad conjunta P(Goles_A = i, Goles_B = j)
    con la corrección de dependencia de Dixon-Coles para marcadores bajos (0-0, 1-0, 0-1, 1-1).
    """
    matriz, _ = mm.matriz_marcadores(float(lam_a), float(lam_b), maximo=max_goles, rho=float(rho))
    return matriz


def filtrar_enfrentamientos_2024_2026(
    partidos_h2h: List[Dict[str, Any]],
    anio_min: int = ANIO_MINIMO_H2H,
    anio_max: int = ANIO_MAXIMO_H2H,
    fecha_corte: str | None = None,
    id_partido_excluir: str | None = None,
) -> List[Dict[str, Any]]:
    """
    Filtra únicamente los enfrentamientos directos terminados cuya fecha esté entre
    el 1 de enero de 2024 y el cierre de 2026 (descartando 2023 o anteriores y amistosos).
    Excluye cualquier partido con ID igual a `id_partido_excluir` o con fecha igual o posterior
    a `fecha_corte` (A3: cero fuga de información del partido objetivo).
    Ordena de más reciente a más antiguo.
    """
    validos: List[Dict[str, Any]] = []
    vistos = set()

    iso_corte = None
    if fecha_corte:
        m_c = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", str(fecha_corte))
        if m_c:
            iso_corte = m_c.group(1)
        elif re.match(r"^\d{4}-\d{2}-\d{2}", str(fecha_corte)):
            iso_corte = str(fecha_corte)[:10]

    for p in partidos_h2h:
        if not isinstance(p, dict):
            continue
        if p.get("es_amistoso") or es_competicion_amistosa(str(p.get("torneo") or "")):
            continue
        m_id = p.get("id") or p.get("match_id") or p.get("id_partido")
        if id_partido_excluir and m_id and str(m_id) == str(id_partido_excluir):
            continue
        fecha_raw = str(p.get("fecha") or p.get("utcTime") or "")[:10]
        if len(fecha_raw) < 4 or not fecha_raw[:4].isdigit():
            continue
        if iso_corte and fecha_raw >= iso_corte:
            continue
        anio = int(fecha_raw[:4])
        if anio_min <= anio <= anio_max:
            clave_dedup = str(m_id) if m_id is not None and str(m_id) != "" else (
                f"{fecha_raw}|{_clave_equipo(p.get('local') or p.get('equipo'))}|"
                f"{_clave_equipo(p.get('visitante') or p.get('rival'))}"
            )
            if clave_dedup in vistos:
                continue
            vistos.add(clave_dedup)
            validos.append(p)

    validos.sort(key=lambda x: str(x.get("fecha") or x.get("utcTime") or ""), reverse=True)
    return validos


# ═══════════════════════════════════════════════════════════════════════════════
# EMPAREJAMIENTO Y ORIENTACIÓN POR IDENTIDAD DE CLUB (TEAM_ID / NOMBRE)
# ═══════════════════════════════════════════════════════════════════════════════

def _clave_equipo(nombre: Any) -> str:
    """Clave normalizada sin tildes ni palabras vacías para comparar nombres de club."""
    base = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode()
    palabras = [p for p in re.split(r"[^A-Za-z0-9]+", base.lower()) if p]
    # NUNCA incluir palabras distintivas de clubes (ej. 'real', 'atletico', 'inter', 'city', 'united', 'sporting')
    # en palabras vacías porque provocan colisiones críticas (ej. Real Madrid colapsando a 'madrid').
    vacias = {"de", "del", "la", "el", "los", "las", "y", "e", "da", "do", "dos", "das",
              "club", "fc", "cf"}
    sig = [p for p in palabras if p not in vacias]
    return "".join(sig) or "".join(palabras)


def _conjunto_claves_equipo(doc: dict[str, Any] | None, nombre_explicito: str = "") -> set[str]:
    """Reúne todas las claves normalizadas posibles de un equipo (nombre, solicitado, alternativas)."""
    claves: set[str] = set()
    if nombre_explicito:
        claves.add(_clave_equipo(nombre_explicito))
    if isinstance(doc, dict):
        eq = doc.get("equipo") or {}
        for cand in (
            eq.get("nombre"),
            doc.get("equipo_solicitado"),
            *(eq.get("alternativas") or []),
        ):
            if cand:
                claves.add(_clave_equipo(cand))
    claves.discard("")
    return claves


def _coincide_equipo(nombre: Any, id_cand: Any, id_objetivo: str, claves_objetivo: set[str]) -> bool:
    """
    Determina si un participante (por ID de club o nombre) corresponde al equipo objetivo.

    REGLA DE ORO DE IDENTIDAD:
    1. Si ambos IDs están presentes y no vacíos (distintos de '0'), la coincidencia de IDs
       es la ÚNICA verdad. Si son distintos, devuelve False inmediatamente (jamás pasa a texto).
    2. Si alguno de los IDs falta, compara la clave normalizada del nombre.
       Solo se acepta igualdad EXACTA (cl == co). Queda TERMINANTEMENTE PROHIBIDO el matching
       por subcadena suelta ('cl in co' o 'co in cl') porque contamina rivales como
       'Real Madrid' ('realmadrid') con 'Atlético Madrid' ('atleticomadrid').
    """
    id_cand_str = str(id_cand).strip() if id_cand is not None else ""
    id_obj_str = str(id_objetivo).strip() if id_objetivo is not None else ""

    # 1. Comparación por ID si ambos están disponibles
    if id_cand_str and id_obj_str and id_cand_str != "0" and id_obj_str != "0":
        return id_cand_str == id_obj_str

    # 2. Comparación por nombre en ausencia de IDs
    cl = _clave_equipo(nombre)
    if not cl:
        return False
    for co in claves_objetivo:
        if cl == co:
            return True
    return False


def _goles_de_partido_auditado(p: dict[str, Any]) -> tuple[int | None, int | None]:
    """Extrae (goles_equipo, goles_rival) de un registro de partido de auditoría."""
    ge = p.get("goles_equipo")
    gr = p.get("goles_rival")
    if ge is None or gr is None:
        marc = p.get("marcador") or {}
        if ge is None:
            ge = marc.get("equipo")
        if gr is None:
            gr = marc.get("rival")
    try:
        ge_i = int(ge) if ge is not None else None
    except (TypeError, ValueError):
        ge_i = None
    try:
        gr_i = int(gr) if gr is not None else None
    except (TypeError, ValueError):
        gr_i = None
    return ge_i, gr_i


def orientar_partido_h2h_a_equipo(
    partido: dict[str, Any],
    nombre_objetivo: str,
    nombre_rival: str,
    es_objetivo_el_equipo_actual: bool,
    id_objetivo: str = "",
    id_rival_objetivo: str = "",
) -> dict[str, Any]:
    """
    Devuelve una copia del partido cuyas 45+ métricas en FT, 1T y 2T están
    orientadas de modo que `"equipo"` sea SIEMPRE `nombre_objetivo` y `"rival"`
    sea SIEMPRE `nombre_rival`, sin importar quién jugó en casa o fuera en ese duelo.
    """
    copia = copy.deepcopy(partido)
    ge, gr = _goles_de_partido_auditado(copia)
    cond_actual = str(copia.get("condicion") or "casa").lower()
    es_casa_actual = (cond_actual == "casa")

    if es_objetivo_el_equipo_actual:
        cond_nueva = "casa" if es_casa_actual else "fuera"
        ge_nuevo, gr_nuevo = ge, gr
        id_eq_nuevo = id_objetivo or copia.get("id_equipo")
        id_riv_nuevo = id_rival_objetivo or copia.get("id_rival")
    else:
        cond_nueva = "fuera" if es_casa_actual else "casa"
        ge_nuevo, gr_nuevo = gr, ge
        # Invertir equipo <-> rival en todas las filas de todos los periodos (FT, 1T, 2T)
        periodos = copia.get("periodos") or {}
        for per_nombre, bloque in periodos.items():
            if not isinstance(bloque, dict):
                continue
            for fila in (bloque.get("filas") or []):
                if isinstance(fila, dict):
                    fila["equipo"], fila["rival"] = fila.get("rival"), fila.get("equipo")
        id_eq_nuevo = id_objetivo or copia.get("id_rival")
        id_riv_nuevo = id_rival_objetivo or copia.get("id_equipo")

    copia["equipo"] = nombre_objetivo
    copia["rival"] = nombre_rival
    if id_eq_nuevo:
        copia["id_equipo"] = str(id_eq_nuevo)
    if id_riv_nuevo:
        copia["id_rival"] = str(id_riv_nuevo)
    copia["condicion"] = cond_nueva
    copia["es_local"] = (cond_nueva == "casa")
    copia["goles_equipo"] = ge_nuevo
    copia["goles_rival"] = gr_nuevo
    copia["marcador"] = {
        "equipo": ge_nuevo,
        "rival": gr_nuevo,
        "texto": (f"{ge_nuevo}-{gr_nuevo}"
                  if ge_nuevo is not None and gr_nuevo is not None else None),
    }
    return copia


def _resumen_duelo_desde_partido_orientado_a(
    partido_a: dict[str, Any],
    nombre_a: str,
    nombre_b: str,
) -> dict[str, Any]:
    """
    Construye la entrada para `lista_duelos_2024_2026` preservando quién fue local
    y quién fue visitante en ese enfrentamiento histórico concreto.
    """
    ge_a, gr_b = _goles_de_partido_auditado(partido_a)
    fue_local_a = str(partido_a.get("condicion") or "").lower() == "casa"
    if fue_local_a:
        local, visitante = nombre_a, nombre_b
        gl, gv = ge_a, gr_b
    else:
        local, visitante = nombre_b, nombre_a
        gl, gv = gr_b, ge_a

    pid = str(partido_a.get("id_partido") or partido_a.get("id") or "")
    torneo = _abreviar_torneo(str(partido_a.get("torneo") or "Oficial"))
    return {
        "id": pid,
        "id_partido": pid,
        "fecha": str(partido_a.get("fecha") or "")[:10],
        "torneo": torneo,
        "local": local,
        "visitante": visitante,
        "goles_local": gl if gl is not None else 0,
        "goles_visitante": gv if gv is not None else 0,
    }


def _abreviar_torneo(torneo: str) -> str:
    """Compacta nombres largos de torneo para que quepan 3 marcadores por línea sin solaparse."""
    t = " ".join((torneo or "").split()).strip()
    if not t:
        return "Liga"
    mapa = {
        "laliga": "LL",
        "primera division": "LL",
        "copa del rey": "CDR",
        "supercopa de espana": "SCE",
        "premier league": "PL",
        "fa cup": "FAC",
        "efl cup": "EFL",
        "carabao cup": "EFL",
        "serie a": "SA",
        "coppa italia": "CI",
        "bundesliga": "BUN",
        "dfb-pokal": "DFB",
        "ligue 1": "L1",
        "champions league": "UCL",
        "uefa champions league": "UCL",
        "europa league": "UEL",
        "uefa europa league": "UEL",
        "conference league": "UECL",
        "copa libertadores": "LIB",
        "conmebol libertadores": "LIB",
        "copa sudamericana": "SUD",
        "conmebol sudamericana": "SUD",
        "brasileirao": "BRA",
        "copa do brasil": "CDB",
    }
    tl = _sin_acentos_simple(t).lower()
    for k, abrev in mapa.items():
        if tl == k or tl.startswith(k + " "):
            return abrev
    if len(t) > 14:
        palabras = [p for p in re.split(r"[^A-Za-z0-9]+", t) if p]
        if len(palabras) >= 2:
            return "".join(p[0].upper() for p in palabras[:4])
        return t[:12]
    return t


def _sin_acentos_simple(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()


# ═══════════════════════════════════════════════════════════════════════════════
# DESCUBRIMIENTO Y EXTRACCIÓN DE DUELOS H2H (2024–2026)
# ═══════════════════════════════════════════════════════════════════════════════

def _ruta_cache_partido_h2h(id_partido: str) -> str:
    os.makedirs(CACHE_H2H_DIR, exist_ok=True)
    seguro = re.sub(r"[^A-Za-z0-9_-]+", "_", str(id_partido))
    return os.path.join(CACHE_H2H_DIR, f"partido_{seguro}.json")


def _cargar_partido_cacheado(id_partido: str) -> dict[str, Any] | None:
    ruta = _ruta_cache_partido_h2h(id_partido)
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            datos = json.load(f)
        if isinstance(datos, dict) and (datos.get("periodos") or {}).get("FT"):
            return datos
    except Exception:
        return None
    return None


def _guardar_partido_cacheado(id_partido: str, partido_dict: dict[str, Any]) -> None:
    try:
        ruta = _ruta_cache_partido_h2h(id_partido)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(partido_dict, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _ruta_cache_indice_h2h(id_a: str, id_b: str) -> str:
    os.makedirs(CACHE_H2H_DIR, exist_ok=True)
    par = "_".join(sorted([re.sub(r"[^A-Za-z0-9]+", "", str(id_a)),
                           re.sub(r"[^A-Za-z0-9]+", "", str(id_b))]))
    return os.path.join(CACHE_H2H_DIR, f"indice_h2h_2024_2026_{par}.json")


def _buscar_fixtures_h2h_en_fotmob(
    fuente: FuenteFotMob,
    doc_a: dict[str, Any],
    doc_b: dict[str, Any],
    nombre_a: str,
    nombre_b: str,
    aviso: Any = None,
) -> list[PartidoFinalizado]:
    """
    Busca todos los enfrentamientos directos oficiales terminados entre Equipo A y Equipo B
    en el rango 2024–2026 combinando:
      1. El bloque `content.h2h.matches` de FotMob (`/matchDetails`), si existe un partido
         común o programado entre ambos.
      2. Las temporadas `2023/2024`, `2024`, `2024/2025`, `2025`, `2025/2026`, `2026`,
         `2026/2027` de `/api/leagues` para las competiciones de ambos clubes.
      3. El listado de resultados de Flashscore para verificar marcadores y fechas.
    """
    avisar = aviso or (lambda m: None)
    eq_a = doc_a.get("equipo") or {}
    eq_b = doc_b.get("equipo") or {}
    tid_a = str(eq_a.get("id") or "")
    tid_b = str(eq_b.get("id") or "")
    if not tid_a or not tid_b:
        return []

    claves_a = _conjunto_claves_equipo(doc_a, nombre_a)
    claves_b = _conjunto_claves_equipo(doc_b, nombre_b)
    encontrados: dict[str, PartidoFinalizado] = {}

    def _registrar_fixture_bruto(f: dict[str, Any], torneo_defecto: str = "") -> None:
        if not isinstance(f, dict):
            return
        estado = f.get("status") or {}
        if not estado.get("finished", True):
            return
        pid = str(f.get("id") or f.get("matchId") or "")
        if not pid or pid in encontrados:
            return
        fecha = str(estado.get("utcTime") or f.get("utcTime") or f.get("time") or "")[:10]
        if len(fecha) < 4 or not fecha[:4].isdigit():
            return
        anio = int(fecha[:4])
        if not (ANIO_MINIMO_H2H <= anio <= ANIO_MAXIMO_H2H):
            return

        home = f.get("home") or {}
        away = f.get("away") or {}
        home_id = str(home.get("id") or "").strip()
        away_id = str(away.get("id") or "").strip()
        home_name = str(home.get("name") or "")
        away_name = str(away.get("name") or "")

        # EXIGENCIA ESTRICTA: El duelo debe ser exclusiva y fehacientemente entre Equipo A y Equipo B.
        # Si ambos IDs están presentes en el fixture y en los metadatos de los clubes:
        if home_id and away_id and tid_a and tid_b and home_id != "0" and away_id != "0":
            if {home_id, away_id} != {tid_a, tid_b}:
                return
            es_a_local = (home_id == tid_a)
            es_b_local = (home_id == tid_b)
        else:
            es_a_local = (_coincide_equipo(home_name, home_id, tid_a, claves_a)
                          and _coincide_equipo(away_name, away_id, tid_b, claves_b))
            es_b_local = (_coincide_equipo(home_name, home_id, tid_b, claves_b)
                          and _coincide_equipo(away_name, away_id, tid_a, claves_a))
            if not (es_a_local or es_b_local):
                return

        torneo = str(
            (f.get("tournament") or {}).get("name")
            or (f.get("league") or {}).get("name")
            or torneo_defecto
            or ""
        )
        if es_competicion_amistosa(torneo):
            return

        gl, gv = home.get("score"), away.get("score")
        if not isinstance(gl, int) or not isinstance(gv, int):
            gl_s, gv_s = _goles_desde_texto(estado.get("scoreStr") or f.get("scoreStr"))
            if not isinstance(gl, int):
                gl = gl_s
            if not isinstance(gv, int):
                gv = gv_s
        if gl is None or gv is None:
            return

        encontrados[pid] = PartidoFinalizado(
            id_partido=pid,
            fecha=fecha,
            torneo=torneo or "Oficial",
            local=home_name or (nombre_a if es_a_local else nombre_b),
            visitante=away_name or (nombre_b if es_a_local else nombre_a),
            goles_local=int(gl),
            goles_visitante=int(gv),
            es_local_el_equipo=bool(es_a_local),
            url=str(f.get("pageUrl") or ""),
            extra={"marcador_texto": f"{gl} - {gv}", "home_id": home_id, "away_id": away_id},
        )

    # 1) Bloque H2H directo de FotMob (/matchDetails) si existe algún partido común
    #    (ya jugado o próximo programado en `partidos_futuros`).
    ids_comunes: list[str] = []
    ids_a = {str(p.get("id_partido")) for p in (doc_a.get("partidos") or []) if p.get("id_partido")}
    ids_b = {str(p.get("id_partido")) for p in (doc_b.get("partidos") or []) if p.get("id_partido")}
    ids_comunes.extend(sorted(ids_a & ids_b))
    for fut in (doc_a.get("partidos_futuros") or []) + (doc_b.get("partidos_futuros") or []):
        if not isinstance(fut, dict):
            continue
        hid, aid = str(fut.get("home_id") or ""), str(fut.get("away_id") or "")
        if {hid, aid} == {tid_a, tid_b} and fut.get("id_partido"):
            ids_comunes.append(str(fut["id_partido"]))

    for id_ref in ids_comunes[:2]:
        try:
            det = fuente._api(f"/matchDetails?matchId={id_ref}")
            h2h_matches = (((det or {}).get("content") or {}).get("h2h") or {}).get("matches") or []
            for m in h2h_matches:
                _registrar_fixture_bruto(m)
            if h2h_matches:
                break
        except Exception:
            continue

    # 2) Ventana principal (/teams) de ambos equipos
    for tid in (tid_a, tid_b):
        try:
            datos_t = fuente._api(f"/teams?id={tid}&ccode3=PER&lang={fuente.idioma}")
            fixtures = ((((datos_t or {}).get("fixtures") or {}).get("allFixtures")) or {}).get("fixtures") or []
            for f in fixtures:
                _registrar_fixture_bruto(f, str((f.get("tournament") or {}).get("name") or ""))
        except Exception:
            continue

    # 3) Temporadas 2024–2026 en `/api/leagues` para las ligas donde compite el Equipo A
    try:
        ligas_a = dict(fuente._ligas_del_equipo(tid_a) or {})
        ligas_b = dict(fuente._ligas_del_equipo(tid_b) or {})
        # Priorizar las ligas compartidas entre ambos equipos
        ligas_compartidas = [lid for lid in ligas_a if lid in ligas_b]
        if not ligas_compartidas:
            ligas_compartidas = list(ligas_a.keys())[:4]

        for lid in ligas_compartidas:
            nombre_liga = ligas_a.get(lid) or ligas_b.get(lid) or ""
            if es_competicion_amistosa(nombre_liga):
                continue
            temporadas_disp = fuente._temporadas_de_liga(lid) or []
            temporadas_a_revisar = [
                t for t in temporadas_disp
                if any(obj in str(t) for obj in ("2024", "2025", "2026"))
            ]
            for temp in temporadas_a_revisar[:4]:
                for f, n_torneo in fuente._partidos_de_liga(lid, temp, tid_a):
                    _registrar_fixture_bruto(f, n_torneo or nombre_liga)
    except Exception as e:
        avisar(f"aviso al revisar temporadas 2024–2026 en ligas: {e}")

    lista = sorted(encontrados.values(), key=lambda x: x.fecha, reverse=True)
    return lista


def _asegurar_pareo_flashscore_en_partidos_h2h(
    partidos: list[dict[str, Any]],
    doc_a: dict[str, Any],
    doc_b: dict[str, Any],
    aviso: Any = None,
) -> None:
    """
    Garantiza que ningún partido del H2H quede con 'Flashscore: N/D'.
    Si un partido carece de id_flashscore o pareo, consulta el feed H2H (df_hh_1)
    a partir de cualquier ID de Flashscore conocido en la sesión para emparejar
    y fusionar las métricas de Flashscore en FT, 1T y 2T.
    """
    faltantes = [
        p for p in partidos
        if not (p.get("fuente_secundaria") or {}).get("id_flashscore")
        or (p.get("fuente_secundaria") or {}).get("id_flashscore") in ("N/D", "None", "")
        or not (p.get("fuente_secundaria") or {}).get("pareado")
    ]
    if not faltantes:
        try:
            from fuentes_xg import consolidar_matriz_completa, MatrizPartido
            for p in partidos:
                matriz_obj = MatrizPartido.desde_dict(p)
                consolidar_matriz_completa(matriz_obj)
                p.update(matriz_obj.a_dict())
                pid = str(p.get("id_partido") or p.get("id") or "")
                if pid:
                    _guardar_partido_cacheado(pid, p)
        except Exception:
            pass
        return

    # Buscar cualquier ID de Flashscore conocido
    id_ref = None
    for p in partidos:
        mid = (p.get("fuente_secundaria") or {}).get("id_flashscore")
        if mid and mid not in ("N/D", "None", ""):
            id_ref = mid
            break
    if not id_ref:
        for p in (doc_a.get("partidos") or []) + (doc_b.get("partidos") or []):
            mid = (p.get("fuente_secundaria") or {}).get("id_flashscore")
            if mid and mid not in ("N/D", "None", ""):
                id_ref = mid
                break

    if not id_ref:
        return

    try:
        from fuente_flashscore import (
            ClienteFlashscore, candidatos_desde_feed_hh, emparejar_partido,
        )
        from fuentes_xg import consolidar_matriz_completa, MatrizPartido
        c = ClienteFlashscore()
        candidatos = candidatos_desde_feed_hh(id_ref, cliente=c)
        if not candidatos:
            return

        for p in faltantes:
            cond = str(p.get("condicion") or "").lower()
            es_local = (cond == "casa")
            nom_eq = p.get("equipo") or ""
            nom_riv = p.get("rival") or ""
            nom_loc = nom_eq if es_local else nom_riv
            nom_vis = nom_riv if es_local else nom_eq

            marcador = p.get("marcador") or {}
            g_eq = marcador.get("equipo") if marcador.get("equipo") is not None else p.get("goles_equipo")
            g_riv = marcador.get("rival") if marcador.get("rival") is not None else p.get("goles_rival")
            gl = g_eq if es_local else g_riv
            gv = g_riv if es_local else g_eq

            consulta = {
                "fecha": str(p.get("fecha") or "")[:10],
                "local": nom_loc,
                "visitante": nom_vis,
                "goles_local": gl,
                "goles_visitante": gv,
            }
            res_emp = emparejar_partido(consulta, candidatos)
            if not res_emp:
                continue

            cand = res_emp["candidato"]
            mid_fs = cand["id"]
            invertido = res_emp.get("bandos_invertidos", False)

            metricas_fs = c.metricas_partido(mid_fs)
            p["fuente_secundaria"] = {
                "pareado": True,
                "id_flashscore": mid_fs,
                "bandos_invertidos": invertido,
                "periodos_rellenados": metricas_fs.periodos_disponibles,
            }

            periodos_dict = p.setdefault("periodos", {})
            for per in ("FT", "1T", "2T"):
                bloque_per = periodos_dict.setdefault(per, {"filas": []})
                filas_list = bloque_per.setdefault("filas", [])
                filas_por_clave = {f["clave"]: f for f in filas_list}

                bando_eq = ("visitante" if invertido else "local") if es_local else ("local" if invertido else "visitante")
                bando_riv = ("local" if invertido else "visitante") if es_local else ("visitante" if invertido else "local")

                fs_eq = metricas_fs.metricas_de(per, bando_eq)
                fs_riv = metricas_fs.metricas_de(per, bando_riv)

                for k_fs, v_fs in fs_eq.items():
                    if k_fs in filas_por_clave:
                        if filas_por_clave[k_fs].get("equipo") is None:
                            filas_por_clave[k_fs]["equipo"] = v_fs
                    else:
                        nueva_f = {"clave": k_fs, "metrica": k_fs, "categoria": "GENERAL", "equipo": v_fs, "rival": None}
                        filas_list.append(nueva_f)
                        filas_por_clave[k_fs] = nueva_f

                for k_fs, v_fs in fs_riv.items():
                    if k_fs in filas_por_clave:
                        if filas_por_clave[k_fs].get("rival") is None:
                            filas_por_clave[k_fs]["rival"] = v_fs
                    else:
                        nueva_f = {"clave": k_fs, "metrica": k_fs, "categoria": "GENERAL", "equipo": None, "rival": v_fs}
                        filas_list.append(nueva_f)
                        filas_por_clave[k_fs] = nueva_f

            matriz_obj = MatrizPartido.desde_dict(p)
            consolidar_matriz_completa(matriz_obj)
            p_actualizado = matriz_obj.a_dict()
            p.update(p_actualizado)
            p["fuente_secundaria"]["id_flashscore"] = mid_fs
            p["fuente_secundaria"]["pareado"] = True

            pid = str(p.get("id_partido") or p.get("id") or "")
            if pid:
                _guardar_partido_cacheado(pid, p)

            if callable(aviso):
                try:
                    aviso(f"H2H Flashscore emparejado: {p.get('fecha')} -> ID: {mid_fs}")
                except Exception:
                    pass

        for p in partidos:
            try:
                matriz_obj = MatrizPartido.desde_dict(p)
                consolidar_matriz_completa(matriz_obj)
                p.update(matriz_obj.a_dict())
                pid = str(p.get("id_partido") or p.get("id") or "")
                if pid:
                    _guardar_partido_cacheado(pid, p)
            except Exception:
                pass
    except Exception as e_h2h_fs:
        if callable(aviso):
            try:
                aviso(f"aviso en pareo Flashscore H2H: {e_h2h_fs}")
            except Exception:
                pass


def recolectar_duelos_h2h_2024_2026(
    doc_a: dict[str, Any],
    doc_b: dict[str, Any],
    nombre_a: str | None = None,
    nombre_b: str | None = None,
    fuente_obj: Any = None,
    buscar_en_red: bool | None = None,
    headless: bool = True,
    frescura_horas: float = 12.0,
    aviso: Any = None,
    fecha_corte: str | None = None,
    id_partido_excluir: str | None = None,
) -> dict[str, Any]:
    """
    Recolecta todos los enfrentamientos directos oficiales entre Equipo A y Equipo B
    en la ventana estricta 2024–2026 (`2024-01-01` .. `2026-12-31`), extrae sus 45+
    estadísticas completas (FT, 1T y 2T) y orienta cada partido por identidad del club
    para que `doc_h2h_a` promedie siempre al Equipo A y `doc_h2h_b` al Equipo B.

    Totalmente independiente de los 15 partidos generales: no altera `doc_a` ni `doc_b`.
    """
    avisar = aviso or (lambda m: None)
    nom_a = nombre_a or (doc_a.get("equipo") or {}).get("nombre") or doc_a.get("equipo_solicitado") or "Equipo A"
    nom_b = nombre_b or (doc_b.get("equipo") or {}).get("nombre") or doc_b.get("equipo_solicitado") or "Equipo B"
    id_a = str((doc_a.get("equipo") or {}).get("id") or "")
    id_b = str((doc_b.get("equipo") or {}).get("id") or "")
    claves_a = _conjunto_claves_equipo(doc_a, nom_a)
    claves_b = _conjunto_claves_equipo(doc_b, nom_b)

    # Mapa id_partido -> partido ya orientado al Equipo A
    partidos_orientados_a: dict[str, dict[str, Any]] = {}

    # ── Paso 1: Buscar duelos directos 2024-2026 que ya estén en `doc_a` o `doc_b` ──
    ids_en_a = {str(p.get("id_partido")): p for p in (doc_a.get("partidos") or []) if p.get("id_partido")}
    ids_en_b = {str(p.get("id_partido")): p for p in (doc_b.get("partidos") or []) if p.get("id_partido")}

    # 1a) Partidos en doc_a cuyo rival sea Equipo B (o cuyo id_partido esté en doc_b)
    for p in (doc_a.get("partidos") or []):
        if p.get("es_amistoso") or es_competicion_amistosa(str(p.get("torneo") or "")):
            continue
        pid = str(p.get("id_partido") or p.get("id") or "")
        id_riv = str(p.get("id_rival") or "").strip()
        if id_riv and id_b and id_riv != "0" and id_b != "0":
            es_h2h = (id_riv == id_b)
        else:
            es_h2h = (pid and pid in ids_en_b) or _coincide_equipo(p.get("rival"), p.get("id_rival"), id_b, claves_b)
        if not es_h2h:
            continue
        if not filtrar_enfrentamientos_2024_2026([p], fecha_corte=fecha_corte, id_partido_excluir=id_partido_excluir):
            continue
        clave = pid or f"{p.get('fecha')}_{nom_a}_{nom_b}"
        partidos_orientados_a[clave] = orientar_partido_h2h_a_equipo(
            p, nom_a, nom_b, es_objetivo_el_equipo_actual=True,
            id_objetivo=id_a, id_rival_objetivo=id_b,
        )

    # 1b) Partidos en doc_b cuyo rival sea Equipo A y aún no estén registrados
    for p in (doc_b.get("partidos") or []):
        if p.get("es_amistoso") or es_competicion_amistosa(str(p.get("torneo") or "")):
            continue
        pid = str(p.get("id_partido") or p.get("id") or "")
        id_riv = str(p.get("id_rival") or "").strip()
        if id_riv and id_a and id_riv != "0" and id_a != "0":
            es_h2h = (id_riv == id_a)
        else:
            es_h2h = (pid and pid in ids_en_a) or _coincide_equipo(p.get("rival"), p.get("id_rival"), id_a, claves_a)
        if not es_h2h:
            continue
        if not filtrar_enfrentamientos_2024_2026([p], fecha_corte=fecha_corte, id_partido_excluir=id_partido_excluir):
            continue
        clave = pid or f"{p.get('fecha')}_{nom_a}_{nom_b}"
        if clave not in partidos_orientados_a:
            partidos_orientados_a[clave] = orientar_partido_h2h_a_equipo(
                p, nom_a, nom_b, es_objetivo_el_equipo_actual=False,
                id_objetivo=id_a, id_rival_objetivo=id_b,
            )

    # 1c) Si el documento incluye una lista explícita `duelos_h2h` / `h2h_partidos` (útil en tests o precargas)
    for contenedor, es_de_a in ((doc_a, True), (doc_b, False)):
        for clave_extra in ("duelos_h2h", "h2h_partidos"):
            for p in (contenedor.get(clave_extra) or []):
                if not isinstance(p, dict) or not filtrar_enfrentamientos_2024_2026([p]):
                    continue
                pid = str(p.get("id_partido") or p.get("id") or "")
                clave = pid or f"{p.get('fecha')}_{ p.get('local') }_{ p.get('visitante') }"
                if clave in partidos_orientados_a:
                    continue
                if "periodos" in p:
                    eq_p = p.get("equipo")
                    es_actual_a = _coincide_equipo(eq_p, None, id_a, claves_a) if eq_p else es_de_a
                    partidos_orientados_a[clave] = orientar_partido_h2h_a_equipo(
                        p, nom_a, nom_b, es_objetivo_el_equipo_actual=es_actual_a,
                        id_objetivo=id_a, id_rival_objetivo=id_b,
                    )

    # ── Paso 2: Ampliación en FotMob + Flashscore para cubrir todo 2024–2026 ──
    if buscar_en_red is None:
        # En producción (auditorías reales de FotMob) se activa automáticamente;
        # en pruebas unitarias sintéticas (`fuente="falsa"` / `"sintetica"`) queda offline.
        es_fotmob_real = (
            str(doc_a.get("fuente") or "").lower() == "fotmob"
            and str(doc_b.get("fuente") or "").lower() == "fotmob"
            and id_a.isdigit() and id_b.isdigit()
        )
        buscar_en_red = es_fotmob_real or (fuente_obj is not None)

    if buscar_en_red and id_a and id_b:
        ruta_idx = _ruta_cache_indice_h2h(id_a, id_b)
        fixtures_cache: list[dict[str, Any]] | None = None
        if os.path.exists(ruta_idx) and fuente_obj is None:
            try:
                edad_h = (time.time() - os.path.getmtime(ruta_idx)) / 3600.0
                if edad_h <= frescura_horas:
                    with open(ruta_idx, "r", encoding="utf-8") as f:
                        datos_idx = json.load(f)
                    if isinstance(datos_idx, dict) and isinstance(datos_idx.get("fixtures"), list):
                        fixtures_cache = datos_idx["fixtures"]
            except Exception:
                fixtures_cache = None

        # Verificar si todos los fixtures del índice cacheado ya tienen su matriz en disco o memoria
        necesita_red = fixtures_cache is None
        if fixtures_cache is not None:
            fixtures_validos_cache = []
            for fc in fixtures_cache:
                f_loc = fc.get("local")
                f_vis = fc.get("visitante")
                f_hid = str(fc.get("id_local") or fc.get("home_id") or "").strip()
                f_aid = str(fc.get("id_visitante") or fc.get("away_id") or "").strip()
                if f_hid and f_aid and id_a and id_b and f_hid != "0" and f_aid != "0":
                    if {f_hid, f_aid} != {id_a, id_b}:
                        continue
                else:
                    c_loc_a = _coincide_equipo(f_loc, f_hid, id_a, claves_a)
                    c_vis_b = _coincide_equipo(f_vis, f_aid, id_b, claves_b)
                    c_loc_b = _coincide_equipo(f_loc, f_hid, id_b, claves_b)
                    c_vis_a = _coincide_equipo(f_vis, f_aid, id_a, claves_a)
                    if not ((c_loc_a and c_vis_b) or (c_loc_b and c_vis_a)):
                        continue
                fixtures_validos_cache.append(fc)
            fixtures_cache = fixtures_validos_cache

            for fc in fixtures_cache:
                pid_c = str(fc.get("id_partido") or "")
                if pid_c and pid_c not in partidos_orientados_a:
                    cached_p = _cargar_partido_cacheado(pid_c)
                    if cached_p is not None:
                        eq_p = cached_p.get("equipo")
                        riv_p = cached_p.get("rival")
                        id_eq = str(cached_p.get("id_equipo") or "").strip()
                        id_riv = str(cached_p.get("id_rival") or "").strip()

                        es_a_eq = _coincide_equipo(eq_p, id_eq, id_a, claves_a)
                        es_b_riv = _coincide_equipo(riv_p, id_riv, id_b, claves_b)
                        es_b_eq = _coincide_equipo(eq_p, id_eq, id_b, claves_b)
                        es_a_riv = _coincide_equipo(riv_p, id_riv, id_a, claves_a)

                        if es_a_eq and es_b_riv:
                            partidos_orientados_a[pid_c] = orientar_partido_h2h_a_equipo(
                                cached_p, nom_a, nom_b, es_objetivo_el_equipo_actual=True,
                                id_objetivo=id_a, id_rival_objetivo=id_b,
                            )
                        elif es_b_eq and es_a_riv:
                            partidos_orientados_a[pid_c] = orientar_partido_h2h_a_equipo(
                                cached_p, nom_a, nom_b, es_objetivo_el_equipo_actual=False,
                                id_objetivo=id_a, id_rival_objetivo=id_b,
                            )
                    else:
                        necesita_red = True

        if necesita_red and buscar_en_red:
            propia = False
            fuente = fuente_obj
            try:
                if fuente is None:
                    fuente = FuenteFotMob(headless=headless, verbose=True)
                    fuente.abrir()
                    propia = True

                fixtures_h2h = _buscar_fixtures_h2h_en_fotmob(
                    fuente, doc_a, doc_b, nom_a, nom_b, aviso=avisar
                )
                # Persistir índice de duelos 2024–2026 encontrados
                try:
                    with open(ruta_idx, "w", encoding="utf-8") as f:
                        json.dump({
                            "generado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "id_a": id_a, "id_b": id_b,
                            "equipo_a": nom_a, "equipo_b": nom_b,
                            "fixtures": [
                                {
                                    "id_partido": pf.id_partido,
                                    "fecha": pf.fecha,
                                    "torneo": pf.torneo,
                                    "local": pf.local,
                                    "visitante": pf.visitante,
                                    "home_id": getattr(pf, "extra", {}).get("home_id", "") if hasattr(pf, "extra") else "",
                                    "away_id": getattr(pf, "extra", {}).get("away_id", "") if hasattr(pf, "extra") else "",
                                    "goles_local": pf.goles_local,
                                    "goles_visitante": pf.goles_visitante,
                                    "es_local_el_equipo": pf.es_local_el_equipo,
                                    "url": pf.url,
                                }
                                for pf in fixtures_h2h
                            ],
                        }, f, ensure_ascii=False, indent=1)
                except Exception:
                    pass

                equipo_a_meta = dict(doc_a.get("equipo") or {"id": id_a, "nombre": nom_a})
                equipo_a_meta.setdefault("id", id_a)
                equipo_a_meta.setdefault("nombre", nom_a)

                for pf in fixtures_h2h:
                    if pf.id_partido in partidos_orientados_a:
                        continue
                    cached_p = _cargar_partido_cacheado(pf.id_partido)
                    if cached_p is not None:
                        eq_p = cached_p.get("equipo")
                        riv_p = cached_p.get("rival")
                        id_eq = str(cached_p.get("id_equipo") or "").strip()
                        id_riv = str(cached_p.get("id_rival") or "").strip()

                        es_a_eq = _coincide_equipo(eq_p, id_eq, id_a, claves_a)
                        es_b_riv = _coincide_equipo(riv_p, id_riv, id_b, claves_b)
                        es_b_eq = _coincide_equipo(eq_p, id_eq, id_b, claves_b)
                        es_a_riv = _coincide_equipo(riv_p, id_riv, id_a, claves_a)

                        if es_a_eq and es_b_riv:
                            partidos_orientados_a[pf.id_partido] = orientar_partido_h2h_a_equipo(
                                cached_p, nom_a, nom_b, es_objetivo_el_equipo_actual=True,
                                id_objetivo=id_a, id_rival_objetivo=id_b,
                            )
                            continue
                        elif es_b_eq and es_a_riv:
                            partidos_orientados_a[pf.id_partido] = orientar_partido_h2h_a_equipo(
                                cached_p, nom_a, nom_b, es_objetivo_el_equipo_actual=False,
                                id_objetivo=id_a, id_rival_objetivo=id_b,
                            )
                            continue
                    try:
                        avisar(f"H2H 2024–2026: extrayendo 45 métricas de {pf.fecha} "
                                f"({pf.local} {pf.goles_local}-{pf.goles_visitante} {pf.visitante})")
                        matriz = fuente.matriz_partido(pf, equipo_a_meta)
                        consolidar_matriz_completa(matriz)
                        p_dict = matriz.a_dict()
                        _guardar_partido_cacheado(pf.id_partido, p_dict)
                        partidos_orientados_a[pf.id_partido] = orientar_partido_h2h_a_equipo(
                            p_dict, nom_a, nom_b, es_objetivo_el_equipo_actual=True,
                            id_objetivo=id_a, id_rival_objetivo=id_b,
                        )
                    except Exception as e_mat:
                        avisar(f"aviso: no se pudo extraer matriz H2H de {pf.id_partido}: {e_mat}")
            except Exception as e_red:
                avisar(f"aviso en búsqueda de H2H 2024–2026: {e_red}")
            finally:
                if propia and fuente is not None:
                    try:
                        fuente.cerrar()
                    except Exception:
                        pass

    # ── Paso 3: Filtrado final 2024–2026 y verificación exhaustiva de integridad H2H ──
    partidos_candidatos = filtrar_enfrentamientos_2024_2026(
        list(partidos_orientados_a.values()), ANIO_MINIMO_H2H, ANIO_MAXIMO_H2H
    )

    lista_partidos_a: list[dict[str, Any]] = []
    for p in partidos_candidatos:
        eq = p.get("equipo")
        riv = p.get("rival")
        id_eq = str(p.get("id_equipo") or "").strip()
        id_riv = str(p.get("id_rival") or "").strip()
        c_a = _coincide_equipo(eq, id_eq, id_a, claves_a)
        c_b = _coincide_equipo(riv, id_riv, id_b, claves_b)
        if c_a and c_b:
            lista_partidos_a.append(p)

    if not lista_partidos_a:
        return {
            "n_duelos": 0,
            "sin_enfrentamientos": True,
            "estado": MENSAJE_SIN_H2H_2024_2026,
            "lista_duelos": [],
            "doc_h2h_a": None,
            "doc_h2h_b": None,
        }

    # Asegurar que ningún partido del H2H quede con 'Flashscore: N/D'
    _asegurar_pareo_flashscore_en_partidos_h2h(lista_partidos_a, doc_a, doc_b, aviso=avisar)

    lista_partidos_b = [
        orientar_partido_h2h_a_equipo(p_a, nom_b, nom_a, es_objetivo_el_equipo_actual=False)
        for p_a in lista_partidos_a
    ]
    lista_duelos = [
        _resumen_duelo_desde_partido_orientado_a(p_a, nom_a, nom_b)
        for p_a in lista_partidos_a
    ]

    doc_h2h_a = {
        "equipo": {"id": id_a, "nombre": nom_a},
        "equipo_solicitado": nom_a,
        "contexto": "H2H 2024-2026",
        "rango_anios": [ANIO_MINIMO_H2H, ANIO_MAXIMO_H2H],
        "partidos": lista_partidos_a,
    }
    doc_h2h_b = {
        "equipo": {"id": id_b, "nombre": nom_b},
        "equipo_solicitado": nom_b,
        "contexto": "H2H 2024-2026",
        "rango_anios": [ANIO_MINIMO_H2H, ANIO_MAXIMO_H2H],
        "partidos": lista_partidos_b,
    }

    return {
        "n_duelos": len(lista_partidos_a),
        "sin_enfrentamientos": False,
        "estado": f"{len(lista_partidos_a)} enfrentamiento(s) directo(s) en 2024–2026",
        "lista_duelos": lista_duelos,
        "doc_h2h_a": doc_h2h_a,
        "doc_h2h_b": doc_h2h_b,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PONDERACIÓN BAYESIANA DE LOCALÍA EN H2H (CASA VS FUERA)
# ═══════════════════════════════════════════════════════════════════════════════

def media_contraida_bayesiana(
    prom_general: float,
    n_general: int,
    prom_condicion: float,
    n_condicion: int,
    w_max: float = 0.55,
    k_credibilidad: float = 3.0,
) -> float:
    """
    Contracción Bayesiana entre el H2H general y el H2H en la misma condición de hoy:
        w_ef = w_max * (n_cond / (n_cond + K))
        mezcla = (1 - w_ef) * prom_general + w_ef * prom_condicion
    """
    if n_condicion <= 0:
        return float(prom_general)
    w_ef = float(w_max) * (float(n_condicion) / (float(n_condicion) + float(k_credibilidad)))
    return (1.0 - w_ef) * float(prom_general) + w_ef * float(prom_condicion)


def ponderar_h2h_por_localia(
    bloque_h2h_general: Dict[str, Any],
    bloque_h2h_condicion: Dict[str, Any],
    condicion_etiqueta: str,
) -> Dict[str, Any]:
    """
    Combina las métricas de un equipo en todos los duelos H2H 2024-2026 con su rendimiento
    en los duelos donde jugó en la misma localía (casa o fuera), aplicando contracción bayesiana.
    """
    res = json.loads(json.dumps(bloque_h2h_general))
    res["contexto"] = condicion_etiqueta
    res["n_condicion"] = bloque_h2h_condicion.get("n_partidos", 0)
    m_gen = bloque_h2h_general.get("metricas", {})
    m_cond = bloque_h2h_condicion.get("metricas", {})

    for k, nodo_g in m_gen.items():
        prom_g = nodo_g.get("promedio") if isinstance(nodo_g, dict) else nodo_g
        n_g = (nodo_g.get("n") if isinstance(nodo_g, dict) else None) or bloque_h2h_general.get("n_partidos", 1)
        nodo_c = m_cond.get(k)
        prom_c = nodo_c.get("promedio") if isinstance(nodo_c, dict) else nodo_c
        n_c = (nodo_c.get("n") if isinstance(nodo_c, dict) else None) or bloque_h2h_condicion.get("n_partidos", 0)

        if prom_g is not None and prom_c is not None and n_c > 0:
            mezcla = media_contraida_bayesiana(
                float(prom_g), int(n_g), float(prom_c), int(n_c), w_max=0.55, k_credibilidad=3.0
            )
            res["metricas"][k] = {"n": n_g, "promedio": round(mezcla, 2)}
    return res


# ═══════════════════════════════════════════════════════════════════════════════
# RENDERIZADO DEL CUADRO ESPECIALISTA H2H (2024–2026)
# ═══════════════════════════════════════════════════════════════════════════════

def generar_cuadro_visual_h2h_2024_2026(
    perfil_local_h2h: Dict[str, Any],
    perfil_visita_h2h: Dict[str, Any],
    lista_duelos_2024_2026: List[Dict[str, Any]],
    lam_local_h2h: float,
    lam_visita_h2h: float,
    matriz_h2h: List[List[float]],
    ruta_salida_png: Path | str,
    n_local_en_casa: int = 0,
    n_visita_fuera: int = 0,
    lectura_mitades_local: str = "",
    lectura_mitades_visita: str = "",
    nota_modelo: str = "",
) -> Path:
    """
    Genera la lámina PNG exclusiva de ENFRENTAMIENTOS DIRECTOS (2024-2026),
    totalmente independiente del análisis general de 15 partidos, con distinción
    explícita de Local (casa) vs Visita (fuera).
    """
    ruta_salida_png = Path(ruta_salida_png)
    eq_L = perfil_local_h2h["equipo"]
    eq_V = perfil_visita_h2h["equipo"]
    n_h2h = len(lista_duelos_2024_2026)
    rL = perfil_local_h2h["ratings_0_100"]
    rV = perfil_visita_h2h["ratings_0_100"]
    tL = perfil_local_h2h["tempo_segundos"]
    tV = perfil_visita_h2h["tempo_segundos"]

    fig = plt.figure(figsize=(12.5, 12.8), dpi=180, facecolor="#F8FAFC")
    gs = fig.add_gridspec(2, 1, height_ratios=[1.28, 2.00], hspace=0.22, left=0.07, right=0.95, top=0.885, bottom=0.07)

    # Encabezado en 3 niveles limpios sin ninguna superposición
    fig.text(
        0.07, 0.962,
        "ENFRENTAMIENTOS DIRECTOS (H2H PURO 2024–2026)   ·   CONDICIÓN: LOCAL (CASA) VS VISITA (FUERA)   ·   MÓDULO INDEPENDIENTE",
        fontsize=8.8, fontweight="bold", color="#0369A1", family="sans-serif"
    )
    fig.text(
        0.07, 0.926,
        f"{eq_L} (Local) vs {eq_V} (Visita)",
        fontsize=20.0, fontweight="bold", color="#0F172A", family="sans-serif"
    )
    fig.text(
        0.07, 0.900,
        f"Goles Esperados H2H (λ): {lam_local_h2h:.2f} – {lam_visita_h2h:.2f}   ·   Muestra H2H: {n_h2h} duelos directos en 2024–2026 ({n_local_en_casa} con {eq_L} en casa y {n_h2h - n_local_en_casa} con {eq_V} en casa)",
        fontsize=9.6, fontweight="bold", color="#334155", family="sans-serif"
    )

    ax_top = fig.add_subplot(gs[0])
    ax_top.axis("off")

    columnas = [
        "EQUIPO (CONDICIÓN HOY)",
        "OVERALL\nH2H (0-100)",
        "ATTACK\nH2H (0-100)",
        "DEFENSE\nH2H (0-100)",
        "PRESSING\nH2H (0-100)",
        "SEG / TIRO\nA FAVOR",
        "SEG / GOL\nA FAVOR",
        "SEG / TIRO\nCONCEDIDO",
        "SEG / GOL\nCONCEDIDO",
        "xG / xGOT\nEN H2H",
    ]
    filas_datos = [
        [
            f"{eq_L} (casa / local)\n[{n_local_en_casa} casa · {n_h2h} tot]",
            f"{rL['Overall_Hybrid_Rating']:.1f}",
            f"{rL['Final_Attack_Strength']:.1f}",
            f"{rL['Final_Defense_Strength']:.1f}",
            f"{rL['Pressing_Intensity_Score']:.1f}",
            f"{tL['Avg_Seconds_Between_Shots']:.0f} s",
            f"{tL['Avg_Seconds_Between_Goals']:.0f} s",
            f"{tL['Avg_Seconds_Between_Shots_Conceded']:.0f} s",
            f"{tL['Avg_Seconds_Between_Goals_Conceded']:.0f} s",
            f"{perfil_local_h2h['metricas_clave']['xg_favor']:.2f} / {perfil_local_h2h['metricas_clave']['xgot_favor']:.2f}",
        ],
        [
            f"{eq_V} (fuera / visita)\n[{n_visita_fuera} fuera · {n_h2h} tot]",
            f"{rV['Overall_Hybrid_Rating']:.1f}",
            f"{rV['Final_Attack_Strength']:.1f}",
            f"{rV['Final_Defense_Strength']:.1f}",
            f"{rV['Pressing_Intensity_Score']:.1f}",
            f"{tV['Avg_Seconds_Between_Shots']:.0f} s",
            f"{tV['Avg_Seconds_Between_Goals']:.0f} s",
            f"{tV['Avg_Seconds_Between_Shots_Conceded']:.0f} s",
            f"{tV['Avg_Seconds_Between_Goals_Conceded']:.0f} s",
            f"{perfil_visita_h2h['metricas_clave']['xg_favor']:.2f} / {perfil_visita_h2h['metricas_clave']['xgot_favor']:.2f}",
        ],
    ]

    tabla = ax_top.table(
        cellText=filas_datos,
        colLabels=columnas,
        loc="upper center",
        cellLoc="center",
        bbox=[0.0, 0.50, 1.0, 0.49],
    )
    tabla.auto_set_font_size(False)
    tabla.set_fontsize(8.8)

    anchos_col = [0.18, 0.084, 0.084, 0.084, 0.084, 0.094, 0.094, 0.094, 0.094, 0.108]
    for (row, col), celda in tabla.get_celld().items():
        celda.set_width(anchos_col[col])
        celda.set_edgecolor("#CBD5E1")
        if row == 0:
            celda.set_facecolor("#0F172A")
            celda.get_text().set_color("#FFFFFF")
            celda.get_text().set_fontweight("bold")
            celda.set_fontsize(8.0)
        else:
            celda.set_facecolor("#FFFFFF" if row == 1 else "#F1F5F9")
            if col in (0, 1, 2, 3):
                celda.get_text().set_fontweight("bold")
                celda.get_text().set_color("#0F172A")

    items_txt = [
        f"{d['fecha']} ({d['torneo']}): {d['local']} [L] {d['goles_local']}-{d['goles_visitante']} {d['visitante']} [V]"
        for d in lista_duelos_2024_2026[:6]
    ]
    linea_duelos_1 = "   ·   ".join(items_txt[:3])
    linea_duelos_2 = "   ·   ".join(items_txt[3:6])

    ax_top.text(
        0.0, 0.39,
        "HISTORIAL DE ENFRENTAMIENTOS INCLUIDOS (2024–2026 · [L]=LOCAL / [V]=VISITA · EXCLUIDOS ≤ 2023):",
        fontsize=8.3, fontweight="bold", color="#0369A1", transform=ax_top.transAxes
    )
    ax_top.text(
        0.0, 0.30,
        linea_duelos_1,
        fontsize=8.1, fontweight="normal", color="#1E293B", transform=ax_top.transAxes
    )
    if linea_duelos_2:
        ax_top.text(
            0.0, 0.22,
            linea_duelos_2,
            fontsize=8.1, fontweight="normal", color="#1E293B", transform=ax_top.transAxes
        )

    nota_L = perfil_local_h2h["hallazgos_especialista"][0] if perfil_local_h2h.get("hallazgos_especialista") else _resumen_defecto_h2h(perfil_local_h2h)
    nota_V = perfil_visita_h2h["hallazgos_especialista"][0] if perfil_visita_h2h.get("hallazgos_especialista") else _resumen_defecto_h2h(perfil_visita_h2h)
    ax_top.text(
        0.0, 0.12,
        f"• Lectura H2H {eq_L} (Local): {nota_L}",
        fontsize=8.2, color="#1E293B", transform=ax_top.transAxes
    )
    ax_top.text(
        0.0, 0.04,
        f"• Lectura H2H {eq_V} (Visita): {nota_V}",
        fontsize=8.2, color="#1E293B", transform=ax_top.transAxes
    )
    if lectura_mitades_local or lectura_mitades_visita:
        ax_top.text(
            0.0, -0.04,
            f"• Mitades H2H (1T/2T): {lectura_mitades_local}  |  {lectura_mitades_visita}",
            fontsize=8.0, fontweight="bold", color="#334155", transform=ax_top.transAxes
        )

    ax_mat = fig.add_subplot(gs[1])
    max_g = 6
    sub_mat = np.zeros((max_g + 1, max_g + 1))
    for i in range(max_g + 1):
        for j in range(max_g + 1):
            if i < len(matriz_h2h) and j < len(matriz_h2h[i]):
                sub_mat[i, j] = matriz_h2h[i][j] * 100.0

    ax_mat.imshow(sub_mat, cmap="Blues", aspect="auto", vmin=0.0, vmax=max(10.0, float(sub_mat.max()) * 1.05))

    plano = [(sub_mat[i, j], i, j) for i in range(max_g + 1) for j in range(max_g + 1)]
    plano.sort(reverse=True)
    top3_coords = {(i, j) for _, i, j in plano[:3]}

    ax_mat.set_xticks(range(max_g + 1))
    ax_mat.set_yticks(range(max_g + 1))
    ax_mat.set_xticklabels([str(g) for g in range(max_g + 1)], fontsize=10.5, fontweight="bold", color="#334155")
    ax_mat.set_yticklabels([str(g) for g in range(max_g + 1)], fontsize=10.5, fontweight="bold", color="#334155")
    ax_mat.xaxis.tick_top()
    ax_mat.xaxis.set_label_position("top")
    ax_mat.set_xlabel(f"Goles de {eq_V} (Visita / fuera)", fontsize=11.5, fontweight="bold", color="#0F172A", labelpad=10)
    ax_mat.set_ylabel(f"Goles de {eq_L} (Local / casa)", fontsize=11.5, fontweight="bold", color="#0F172A", labelpad=10)

    umbral_color = float(sub_mat.max()) * 0.55
    for i in range(max_g + 1):
        for j in range(max_g + 1):
            val_pct = sub_mat[i, j]
            estrella = " ★" if (i, j) in top3_coords else ""
            texto_celda = f"{val_pct:.1f}{estrella}" if val_pct >= 0.1 else "·"
            color_txt = "#FFFFFF" if val_pct >= umbral_color else "#0F172A"
            peso_txt = "bold" if (val_pct >= 5.0 or (i, j) in top3_coords) else "normal"
            ax_mat.text(j, i, texto_celda, ha="center", va="center", color=color_txt, fontsize=10.2, fontweight=peso_txt)

    ax_mat.set_xticks(np.arange(-0.5, max_g + 1, 1), minor=True)
    ax_mat.set_yticks(np.arange(-0.5, max_g + 1, 1), minor=True)
    ax_mat.grid(which="minor", color="#F8FAFC", linestyle="-", linewidth=2.0)
    ax_mat.tick_params(which="minor", bottom=False, left=False, top=False)
    for spine in ax_mat.spines.values():
        spine.set_visible(False)

    p_1 = sum(matriz_h2h[i][j] for i in range(len(matriz_h2h)) for j in range(len(matriz_h2h[i])) if i > j) * 100
    p_x = sum(matriz_h2h[i][j] for i in range(len(matriz_h2h)) for j in range(len(matriz_h2h[i])) if i == j) * 100
    p_2 = sum(matriz_h2h[i][j] for i in range(len(matriz_h2h)) for j in range(len(matriz_h2h[i])) if i < j) * 100
    p_btts = sum(matriz_h2h[i][j] for i in range(1, len(matriz_h2h)) for j in range(1, len(matriz_h2h[i]))) * 100
    p_o25 = sum(matriz_h2h[i][j] for i in range(len(matriz_h2h)) for j in range(len(matriz_h2h[i])) if (i + j) > 2.5) * 100

    fig.text(
        0.51, 0.022,
        f"Matriz H2H (2024–2026):  Local {eq_L} {p_1:.1f}%  ·  Empate {p_x:.1f}%  ·  "
        f"Visita {eq_V} {p_2:.1f}%  ·  Ambos Anotan {p_btts:.1f}%  ·  +2.5 Goles {p_o25:.1f}%",
        ha="center", fontsize=8.8, fontweight="bold", color="#0F172A",
        bbox=dict(boxstyle="round,pad=0.42", facecolor="#E0F2FE", edgecolor="#0284C7", lw=1)
    )
    if nota_modelo:
        # Declara QUÉ calculó la matriz que se está viendo (Dixon-Coles de rho fijo
        # o el Motor Recursivo 45-DCN con Monte Carlo). Sin esto, el pie no
        # permitiría auditar de dónde sale el 1-1 que se dibuja.
        fig.text(0.51, 0.004, nota_modelo, ha="center", fontsize=7.4,
                 color="#475569", style="italic")

    ruta_salida_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(ruta_salida_png, dpi=180)
    plt.close(fig)
    return ruta_salida_png


def _resumen_defecto_h2h(perfil: Dict[str, Any]) -> str:
    """Lectura descriptiva de respaldo cuando ningún umbral extremo salta en el H2H."""
    mc = perfil.get("metricas_clave") or {}
    gf = mc.get("goles_favor", 0.0)
    xg = mc.get("xg_favor", 0.0)
    sot = mc.get("remates_puerta", 0.0)
    pos = mc.get("posesion_pct", 50.0)
    return (
        f"Promedia {gf:.2f} goles y {xg:.2f} xG en duelos directos, con {sot:.1f} remates "
        f"a puerta y {pos:.1f}% de posesión media."
    )


def _formatear_lectura_mitades_h2h(equipo: str, mitades: dict[str, Any] | None) -> str:
    """Resumen compacto del radiógrafo 1T/2T en los duelos directos H2H."""
    if not mitades or not mitades.get("1T"):
        return f"{equipo}: sin desglose 1T/2T"
    d1 = mitades.get("1T") or {}
    d2 = mitades.get("2T") or {}
    x1, x2 = d1.get("xg"), d2.get("xg")
    s1, s2 = d1.get("segundos_por_tiro"), d2.get("segundos_por_tiro")
    perfil = mitades.get("perfil_corto") or mitades.get("perfil") or "Estable"
    if x1 is not None and x2 is not None:
        return f"{equipo}: 1T {x1:.2f} xG -> 2T {x2:.2f} xG ({perfil})"
    if s1 is not None and s2 is not None:
        return f"{equipo}: 1T {s1:.0f}s/tiro -> 2T {s2:.0f}s/tiro ({perfil})"
    return f"{equipo}: {perfil}"


def _calcular_lambdas_h2h(
    doc_h2h_a: dict[str, Any],
    doc_h2h_b: dict[str, Any],
    muestra_a: mm.MuestraEquipo,
    muestra_b: mm.MuestraEquipo,
    muestra_a_casa: mm.MuestraEquipo | None = None,
    muestra_b_fuera: mm.MuestraEquipo | None = None,
) -> tuple[float, float]:
    """
    Calcula λ_local y λ_visita exclusivamente a partir de la muestra de enfrentamientos
    directos 2024–2026 (ponderando condición casa vs fuera con Contracción Bayesiana
    + moduladores tácticos de las 45 métricas).
    """
    m_a_c = muestra_a_casa if (muestra_a_casa is not None and muestra_a_casa.partidos_general > 0) else muestra_a
    m_b_c = muestra_b_fuera if (muestra_b_fuera is not None and muestra_b_fuera.partidos_general > 0) else muestra_b

    lam_a, lam_b, _ = mm.tasas_goles(
        muestra_a, muestra_b, m_a_c, m_b_c,
        w_condicion=0.55, w_xg=0.5, k_credibilidad=3.0
    )
    if lam_a is None:
        gf_a = m_a_c.media("goles_favor") or muestra_a.media("goles_favor")
        lam_a = float(gf_a) if gf_a is not None else 1.20
    if lam_b is None:
        gf_b = m_b_c.media("goles_favor") or muestra_b.media("goles_favor")
        lam_b = float(gf_b) if gf_b is not None else 1.00

    # Aplicar moduladores tácticos sobre las 45 métricas del H2H si están disponibles
    try:
        motor_h2h = mm.MotorMercados(
            doc_h2h_a, doc_h2h_b,
            condicion_local="casa", condicion_visitante="fuera",
            parametros=mm.Parametros(w_condicion=0.55, w_xg=0.5, k_credibilidad=3.0, aplicar_moduladores=True),
        )
        motor_h2h.preparar()
        if motor_h2h.tasas.lambda_local_ft is not None and motor_h2h.tasas.lambda_visitante_ft is not None:
            lam_a = float(motor_h2h.tasas.lambda_local_ft)
            lam_b = float(motor_h2h.tasas.lambda_visitante_ft)
    except Exception:
        pass

    return max(0.15, float(lam_a)), max(0.15, float(lam_b))


def _slug(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "equipo").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").lower() or "equipo"


def generar_cuadro_h2h_desde_analisis(
    analisis: Any,
    doc_local: dict[str, Any],
    doc_visitante: dict[str, Any],
    destino: str | Path | None = None,
    directorio: str | None = None,
    peso_roja: float | None = PESO_PARTIDO_CON_ROJA,
    buscar_en_red: bool | None = None,
    fuente_obj: Any = None,
    aviso: Any = None,
    fecha_corte: str | None = None,
    id_partido_excluir: str | None = None,
) -> dict[str, Any]:
    """
    Punto de entrada para `/mercados`:
      1. Recolecta todos los enfrentamientos directos entre Equipo A y Equipo B en 2024–2026.
      2. Si hay 0 enfrentamientos en 2024–2026, declara 'Sin enfrentamientos directos en 2024–2026'
         y omite el gráfico H2H (`ruta = None`).
      3. Si hay >= 1 enfrentamientos en 2024–2026, extrae las 45 métricas orientadas por ID/nombre
         de club, separa y pondera por localía (casa vs fuera) mediante Contracción Bayesiana
         y genera `cuadro_h2h_directo_2024_2026_*.png`.
    """
    avisar = aviso or (lambda m: None)
    motor = getattr(analisis, "motor", None)
    nom_a = (getattr(motor, "nombre_local", None)
             or (doc_local.get("equipo") or {}).get("nombre")
             or doc_local.get("equipo_solicitado")
             or "Equipo Local")
    nom_b = (getattr(motor, "nombre_visitante", None)
             or (doc_visitante.get("equipo") or {}).get("nombre")
             or doc_visitante.get("equipo_solicitado")
             or "Equipo Visitante")

    info_h2h = recolectar_duelos_h2h_2024_2026(
        doc_local, doc_visitante,
        nombre_a=nom_a, nombre_b=nom_b,
        fuente_obj=fuente_obj,
        buscar_en_red=buscar_en_red,
        aviso=avisar,
        fecha_corte=fecha_corte,
        id_partido_excluir=id_partido_excluir,
    )

    n_duelos = info_h2h["n_duelos"]
    if n_duelos == 0 or not info_h2h["doc_h2h_a"] or not info_h2h["doc_h2h_b"]:
        avisar(MENSAJE_SIN_H2H_2024_2026)
        return {
            "ruta": None,
            "n_duelos": 0,
            "sin_enfrentamientos": True,
            "estado": MENSAJE_SIN_H2H_2024_2026,
            "duelos": [],
        }

    doc_h2h_a = info_h2h["doc_h2h_a"]
    doc_h2h_b = info_h2h["doc_h2h_b"]
    lista_duelos = info_h2h["lista_duelos"]

    ponderada = peso_roja is not None

    # Muestras H2H General y H2H Condición para Local (casa) y Visita (fuera)
    muestra_a_gen = mm.extraer_muestra(doc_h2h_a, condicion=None, periodo="FT", peso_roja=peso_roja)
    muestra_a_casa = mm.extraer_muestra(doc_h2h_a, condicion="casa", periodo="FT", peso_roja=peso_roja)

    muestra_b_gen = mm.extraer_muestra(doc_h2h_b, condicion=None, periodo="FT", peso_roja=peso_roja)
    muestra_b_fuera = mm.extraer_muestra(doc_h2h_b, condicion="fuera", periodo="FT", peso_roja=peso_roja)

    n_local_en_casa = len([p for p in doc_h2h_a["partidos"] if str(p.get("condicion") or "").lower() == "casa"])
    n_visita_fuera = len([p for p in doc_h2h_b["partidos"] if str(p.get("condicion") or "").lower() == "fuera"])

    bloque_a_gen = bloque_desde_muestra(muestra_a_gen, equipo=nom_a, contexto="casa",
                                        n_partidos=n_duelos, ponderada=ponderada)
    bloque_a_casa = bloque_desde_muestra(muestra_a_casa, equipo=nom_a, contexto="casa",
                                         n_partidos=n_local_en_casa, ponderada=ponderada)

    bloque_b_gen = bloque_desde_muestra(muestra_b_gen, equipo=nom_b, contexto="fuera",
                                        n_partidos=n_duelos, ponderada=ponderada)
    bloque_b_fuera = bloque_desde_muestra(muestra_b_fuera, equipo=nom_b, contexto="fuera",
                                         n_partidos=n_visita_fuera, ponderada=ponderada)

    # Ponderación Bayesiana de Localía dentro del H2H
    bloque_a_pond = ponderar_h2h_por_localia(bloque_a_gen, bloque_a_casa, "casa")
    bloque_b_pond = ponderar_h2h_por_localia(bloque_b_gen, bloque_b_fuera, "fuera")

    perfil_a = construir_perfil_especialista_equipo(bloque_a_pond)
    perfil_b = construir_perfil_especialista_equipo(bloque_b_pond)
    if not perfil_a.get("hallazgos_especialista"):
        perfil_a["hallazgos_especialista"] = [_resumen_defecto_h2h(perfil_a)]
    if not perfil_b.get("hallazgos_especialista"):
        perfil_b["hallazgos_especialista"] = [_resumen_defecto_h2h(perfil_b)]

    mit_a = perfil_mitades(doc_h2h_a, None)
    mit_b = perfil_mitades(doc_h2h_b, None)
    lectura_mit_a = _formatear_lectura_mitades_h2h(f"{nom_a} (Local H2H)", mit_a)
    lectura_mit_b = _formatear_lectura_mitades_h2h(f"{nom_b} (Visita H2H)", mit_b)

    lam_a_h2h, lam_b_h2h = _calcular_lambdas_h2h(
        doc_h2h_a, doc_h2h_b, muestra_a_gen, muestra_b_gen,
        muestra_a_casa=muestra_a_casa, muestra_b_fuera=muestra_b_fuera,
    )

    # ── Red Neuronal Recursiva sobre la muestra H2H ──────────────────────
    # Sustituye la matriz Dixon-Coles de rho fijo (la que premia el 1-1) por la
    # matriz de la red, con la serie real de goles de los duelos directos.
    lam_a_final, lam_b_final = lam_a_h2h, lam_b_h2h
    matriz_h2h = construir_matriz_dixon_coles(lam_a_h2h, lam_b_h2h, rho=-0.08)
    nota_modelo_h2h = "Dixon-Coles ρ=-0,08 sobre la muestra H2H"
    info_recursivo: Dict[str, Any] = {}
    try:
        info_recursivo = motor_rec.ejecutar_bucle_recursivo_infinito_hasta_convergencia(
            perfil_a,
            perfil_b,
            float(lam_a_h2h),
            float(lam_b_h2h),
            partidos_para_red_neuronal(doc_h2h_a.get("partidos"), nom_a),
            partidos_para_red_neuronal(doc_h2h_b.get("partidos"), nom_b),
        )
        info_recursivo["aplicado"] = True
        lam_a_final = float(info_recursivo["lambda_local_recursivo"])
        lam_b_final = float(info_recursivo["lambda_visita_recursivo"])
        matriz_h2h = red_para_dibujar(info_recursivo)
        entropias = info_recursivo.get("historial_entropia") or [0.0]
        nota_modelo_h2h = (
            f"Red Neuronal Recursiva · conv. it. "
            f"{info_recursivo.get('iteracion_convergencia')} "
            f"(H={float(entropias[-1]):.3f}) · λ {lam_a_h2h:.2f}-{lam_b_h2h:.2f} → "
            f"{lam_a_final:.2f}-{lam_b_final:.2f}")
    except Exception as e_rec:
        info_recursivo = {"aplicado": False, "error": f"{type(e_rec).__name__}: {e_rec}"}
        avisar(f"Red neuronal recursiva en H2H no aplicada ({type(e_rec).__name__}: "
               f"{e_rec}); se conserva la matriz Dixon-Coles del módulo H2H.")

    if not destino:
        base = directorio or os.path.join(config.INFORME_DIR, "h2h", datetime.now().strftime("%Y-%m-%d"))
        os.makedirs(base, exist_ok=True)
        sello = datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = os.path.join(
            base,
            f"cuadro_h2h_directo_2024_2026_{_slug(nom_a)}_vs_{_slug(nom_b)}_{sello}.png",
        )

    ruta_png = generar_cuadro_visual_h2h_2024_2026(
        perfil_a, perfil_b, lista_duelos,
        lam_a_final, lam_b_final, matriz_h2h,
        Path(destino),
        n_local_en_casa=n_local_en_casa,
        n_visita_fuera=n_visita_fuera,
        lectura_mitades_local=lectura_mit_a,
        lectura_mitades_visita=lectura_mit_b,
        nota_modelo=nota_modelo_h2h,
    )

    return {
        "ruta": str(ruta_png),
        "n_duelos": n_duelos,
        "n_local_en_casa": n_local_en_casa,
        "n_visita_fuera": n_visita_fuera,
        "sin_enfrentamientos": False,
        "estado": info_h2h["estado"],
        "duelos": lista_duelos,
        "lambda_a_h2h": lam_a_final,
        "lambda_b_h2h": lam_b_final,
        "lambda_a_h2h_dixon_coles": lam_a_h2h,
        "lambda_b_h2h_dixon_coles": lam_b_h2h,
        "motor_recursivo": info_recursivo,
        "nota_modelo": nota_modelo_h2h,
        "perfil_a_h2h": perfil_a,
        "perfil_b_h2h": perfil_b,
    }


if __name__ == "__main__":
    duelos_barca_betis_todos = [
        {"id": 1, "fecha": "2026-05-17", "torneo": "LL", "local": "Barcelona", "visitante": "Real Betis", "goles_local": 3, "goles_visitante": 1},
        {"id": 2, "fecha": "2025-12-06", "torneo": "LL", "local": "Real Betis", "visitante": "Barcelona", "goles_local": 3, "goles_visitante": 5},
        {"id": 3, "fecha": "2025-04-05", "torneo": "LL", "local": "Barcelona", "visitante": "Real Betis", "goles_local": 1, "goles_visitante": 1},
        {"id": 4, "fecha": "2025-01-15", "torneo": "CDR", "local": "Barcelona", "visitante": "Real Betis", "goles_local": 5, "goles_visitante": 1},
        {"id": 5, "fecha": "2024-12-07", "torneo": "LL", "local": "Real Betis", "visitante": "Barcelona", "goles_local": 2, "goles_visitante": 2},
        {"id": 6, "fecha": "2024-01-21", "torneo": "LL", "local": "Real Betis", "visitante": "Barcelona", "goles_local": 2, "goles_visitante": 4},
        {"id": 7, "fecha": "2023-09-16", "torneo": "LL", "local": "Barcelona", "visitante": "Real Betis", "goles_local": 5, "goles_visitante": 0},
    ]

    duelos_2024_2026 = filtrar_enfrentamientos_2024_2026(duelos_barca_betis_todos, 2024, 2026)
    duelos_barca_en_casa = [d for d in duelos_2024_2026 if d["local"] == "Barcelona"]
    duelos_betis_fuera = [d for d in duelos_2024_2026 if d["visitante"] == "Real Betis"]

    bloque_barca_h2h_gen = {
        "equipo": "Barcelona",
        "contexto": "casa",
        "n_partidos": len(duelos_2024_2026),
        "metricas": {
            "goles_favor": {"promedio": 3.33}, "xg": {"promedio": 2.85}, "xgot": {"promedio": 3.18},
            "xg_sin_penalti": {"promedio": 2.68}, "remates_totales": {"promedio": 18.5}, "remates_puerta": {"promedio": 8.2},
            "grandes_ocasiones": {"promedio": 5.2}, "grandes_ocasiones_falladas": {"promedio": 2.1}, "toques_area_rival": {"promedio": 41.0},
            "posesion_pct": {"promedio": 66.5}, "pases_total": {"promedio": 615.0}, "pases_tercio_final_pct": {"promedio": 78.0},
            "pases_entre_lineas": {"promedio": 96.0}, "xa_equipo": {"promedio": 2.05}, "xgot_enfrentado": {"promedio": 1.52},
            "goles_evitados": {"promedio": -0.15}, "paradas": {"promedio": 2.8}, "bloqueos": {"promedio": 1.8},
            "despejes": {"promedio": 15.5}, "intercepciones": {"promedio": 7.8}, "recuperaciones": {"promedio": 51.0},
            "entradas_pct": {"promedio": 16.5}, "duelos_suelo_pct": {"promedio": 54.5}, "duelos_aereos_pct": {"promedio": 58.0},
            "errores_gol": {"promedio": 0.17}, "errores_remate": {"promedio": 0.33}, "tarjetas_rojas": {"promedio": 0.0},
        },
    }
    bloque_barca_h2h_casa = {
        "equipo": "Barcelona",
        "contexto": "casa",
        "n_partidos": len(duelos_barca_en_casa),
        "metricas": {
            "goles_favor": {"promedio": 3.00}, "xg": {"promedio": 2.95}, "xgot": {"promedio": 3.25},
            "remates_totales": {"promedio": 20.3}, "remates_puerta": {"promedio": 8.7}, "xgot_enfrentado": {"promedio": 1.05},
            "goles_evitados": {"promedio": 0.05}, "posesion_pct": {"promedio": 69.0},
        },
    }

    bloque_betis_h2h_gen = {
        "equipo": "Real Betis",
        "contexto": "fuera",
        "n_partidos": len(duelos_2024_2026),
        "metricas": {
            "goles_favor": {"promedio": 1.67}, "xg": {"promedio": 1.44}, "xgot": {"promedio": 1.52},
            "xg_sin_penalti": {"promedio": 1.28}, "remates_totales": {"promedio": 11.2}, "remates_puerta": {"promedio": 4.5},
            "grandes_ocasiones": {"promedio": 2.5}, "grandes_ocasiones_falladas": {"promedio": 1.2}, "toques_area_rival": {"promedio": 21.5},
            "posesion_pct": {"promedio": 33.5}, "pases_total": {"promedio": 310.0}, "pases_tercio_final_pct": {"promedio": 38.0},
            "pases_entre_lineas": {"promedio": 49.0}, "xa_equipo": {"promedio": 1.02}, "xgot_enfrentado": {"promedio": 3.18},
            "goles_evitados": {"promedio": -0.15}, "paradas": {"promedio": 4.8}, "bloqueos": {"promedio": 3.8},
            "despejes": {"promedio": 26.5}, "intercepciones": {"promedio": 9.2}, "recuperaciones": {"promedio": 44.0},
            "entradas_pct": {"promedio": 15.0}, "duelos_suelo_pct": {"promedio": 45.5}, "duelos_aereos_pct": {"promedio": 42.0},
            "errores_gol": {"promedio": 0.33}, "errores_remate": {"promedio": 0.50}, "tarjetas_rojas": {"promedio": 0.17},
        },
    }
    bloque_betis_h2h_fuera = {
        "equipo": "Real Betis",
        "contexto": "fuera",
        "n_partidos": len(duelos_betis_fuera),
        "metricas": {
            "goles_favor": {"promedio": 1.00}, "xg": {"promedio": 1.08}, "xgot": {"promedio": 1.05},
            "remates_totales": {"promedio": 9.3}, "remates_puerta": {"promedio": 3.7}, "xgot_enfrentado": {"promedio": 3.25},
            "goles_evitados": {"promedio": 0.25}, "posesion_pct": {"promedio": 31.0},
        },
    }

    barca_pond = ponderar_h2h_por_localia(bloque_barca_h2h_gen, bloque_barca_h2h_casa, "casa")
    betis_pond = ponderar_h2h_por_localia(bloque_betis_h2h_gen, bloque_betis_h2h_fuera, "fuera")

    p_barca = construir_perfil_especialista_equipo(barca_pond)
    p_betis = construir_perfil_especialista_equipo(betis_pond)

    lam_barca = 2.98
    lam_betis = 1.24
    mat_h2h = construir_matriz_dixon_coles(lam_barca, lam_betis, rho=-0.08)

    out_h2h = Path(config.INFORME_DIR) / "h2h" / "cuadro_h2h_directo_2024_2026_barcelona_vs_real_betis.png"
    generar_cuadro_visual_h2h_2024_2026(
        p_barca, p_betis, duelos_2024_2026, lam_barca, lam_betis, mat_h2h, out_h2h,
        n_local_en_casa=len(duelos_barca_en_casa),
        n_visita_fuera=len(duelos_betis_fuera),
        lectura_mitades_local="Barcelona (Local H2H): 1T 1.25 xG -> 2T 1.60 xG (crece en 2T)",
        lectura_mitades_visita="Real Betis (Visita H2H): encaja el 65% de los goles tras el descanso",
    )
    print("Regenerado sin superposicion OK:", out_h2h)
