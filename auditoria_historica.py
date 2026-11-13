"""
agente_futbol/auditoria_historica.py
───────────────────────────────────────────────────────────────────────────────
MÓDULO DE AUDITORÍA HISTÓRICA PROFUNDA (Deep Historical Audit)

Objetivo: para un equipo dado, extraer los últimos N (por defecto 10) partidos
FINALIZADOS y construir, por partido, la matriz completa de estadísticas
avanzadas (xG, xGOT, xA, posesión, remates, ataques, pases, defensa, portería)
desglosada en 3 periodos (FT / 1T / 2T) para el EQUIPO auditado y su RIVAL.

Uso como biblioteca:
    from auditoria_historica import auditar_equipo, exportar_auditoria
    auditoria = auditar_equipo("Barcelona", limite=10)
    rutas = exportar_auditoria(auditoria)

Uso como script:
    python auditoria_historica.py "Barcelona"
    python auditoria_historica.py "Alianza Lima" --limite 10 --fuente fotmob --visible

Salidas (en informes/auditorias/AAAA-MM-DD/):
    auditoria_<equipo>_<fecha>.json   estructura completa anidada
    auditoria_<equipo>_<fecha>.csv    formato largo (1 fila por métrica)
    auditoria_<equipo>_<fecha>_matriz.csv   formato ancho (métricas x periodo, listo para pandas)
    auditoria_<equipo>_<fecha>.md     informe legible con la matriz por partido

Principio de honestidad de datos: si una métrica solicitada no existe en la
fuente, queda como nulo y se documenta en `metricas_no_disponibles`. Nunca se
inventan valores.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from utilidades_seguridad import _safe_float, _safe_int, _SafeFloat, _SafeInt

float = _SafeFloat
int = _SafeInt

import argparse
import csv
import json
import os
import re
import sys
import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz
from fuentes_xg import _pais_de_liga

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import config
from fuentes_xg import (
    CATALOGO_METRICAS,
    CATEGORIAS_ORDEN,
    ORDEN_PERIODOS,
    FuenteXG,
    MatrizPartido,
    _inferir_meta_rival_desde_texto,
    extraer_liga_de_texto,
    obtener_fuente,
    puntuar_candidato,
)

BASE_DIR = Path(__file__).resolve().parent
AUDITORIA_DIR = str(Path(config.INFORME_DIR) / "auditorias")
LIMITE_PARTIDOS_POR_DEFECTO = int(os.getenv("AUDITORIA_LIMITE", "15"))
# Valores reales mínimos que debe traer un partido para contarlo como "con datos".
# Por debajo de esto se considera que la fuente no publicó su matriz (típico en
# amistosos o partidos antiguos) y el escaneo continúa hacia atrás.
MIN_VALORES_PARA_CONTAR = 5

# Columnas del CSV largo
COLUMNAS_CSV = ["equipo_auditado", "n_partido", "id_partido", "fecha", "torneo", "condicion",
                "es_local", "rival", "goles_equipo", "goles_rival", "periodo", "categoria",
                "metrica", "clave", "valor_equipo", "valor_rival", "disponible"]

# Antigüedad máxima (horas) que se considera "auditoría fresca" antes de reescanear.
FRESCURA_HORAS_POR_DEFECTO = float(os.getenv("AUDITORIA_FRESCURA_HORAS", "6"))

# Bloqueo concurrente por equipo y caché en memoria para evitar descargas simultáneas duplicadas
_LOCK_GLOBAL_LOCKS = threading.Lock()
_LOCKS_EQUIPO: dict[str, threading.Lock] = {}
_CACHE_MEMORIA_AUDITORIAS: dict[str, dict[str, Any]] = {}


def _obtener_lock_equipo(equipo_slug: str) -> threading.Lock:
    """Devuelve un Lock exclusivo por equipo para serializar auditorías y evitar descargas duplicadas."""
    with _LOCK_GLOBAL_LOCKS:
        if equipo_slug not in _LOCKS_EQUIPO:
            _LOCKS_EQUIPO[equipo_slug] = threading.Lock()
        return _LOCKS_EQUIPO[equipo_slug]


def obtener_auditoria_en_memoria(equipo_slug: str) -> dict[str, Any] | None:
    """Recupera la auditoría en memoria si ya fue procesada en la sesión actual."""
    with _LOCK_GLOBAL_LOCKS:
        return _CACHE_MEMORIA_AUDITORIAS.get(equipo_slug)


def guardar_auditoria_en_memoria(equipo_slug: str, doc: dict[str, Any]) -> None:
    """Guarda una copia de la auditoría en memoria para que [MERCADOS] y otros módulos la reutilicen."""
    with _LOCK_GLOBAL_LOCKS:
        _CACHE_MEMORIA_AUDITORIAS[equipo_slug] = doc


def limpiar_cache_memoria_auditorias() -> None:
    """Limpia la caché en memoria (útil en pruebas)."""
    with _LOCK_GLOBAL_LOCKS:
        _CACHE_MEMORIA_AUDITORIAS.clear()


def eliminar_cache_equipo(nombre_o_doc: Any, pais_iso: str = "") -> int:
    """
    Invalida inmediatamente cualquier entrada en memoria y en disco (cache/ e informes/auditorias/)
    asociada a un equipo (p.ej. cuando se detecta un FALSO HOMÓNIMO EXTRANJERO como CD Universitario [PAN]).
    """
    eliminados = 0
    nombres: set[str] = set()
    ids_eq: set[str] = set()
    claves_cache: set[str] = set()

    if isinstance(nombre_o_doc, dict):
        eq = nombre_o_doc.get("equipo") if isinstance(nombre_o_doc.get("equipo"), dict) else nombre_o_doc
        for k in ("nombre", "name", "nombre_solicitado"):
            if (eq or {}).get(k):
                nombres.add(str(eq[k]).strip())
        if nombre_o_doc.get("equipo_solicitado"):
            nombres.add(str(nombre_o_doc["equipo_solicitado"]).strip())
        if (eq or {}).get("id"):
            ids_eq.add(str(eq["id"]).strip())
        if (eq or {}).get("clave_cache"):
            claves_cache.add(str(eq["clave_cache"]).strip())
    elif isinstance(nombre_o_doc, str) and nombre_o_doc.strip():
        nombres.add(nombre_o_doc.strip())

    slugs: set[str] = set()
    for n in list(nombres):
        s = re.sub(
            r"[^A-Za-z0-9]+",
            "_",
            unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode(),
        ).strip("_").lower()
        if s:
            slugs.add(s)
        try:
            from resolvedor_entidades import construir_clave_cache_entidad
            for iso_c in {pais_iso, "INT", "BOL", "PAN", "PER", "HND", "ARG"}:
                if iso_c:
                    claves_cache.add(construir_clave_cache_entidad(n, iso_c, False, False))
        except Exception:
            pass

    with _LOCK_GLOBAL_LOCKS:
        for k_mem in list(_CACHE_MEMORIA_AUDITORIAS.keys()):
            doc_m = _CACHE_MEMORIA_AUDITORIAS.get(k_mem) or {}
            eq_m = doc_m.get("equipo") or {}
            id_m = str(eq_m.get("id") or "")
            nom_m = str(eq_m.get("nombre") or "").strip()
            slug_m = re.sub(
                r"[^A-Za-z0-9]+",
                "_",
                unicodedata.normalize("NFKD", nom_m).encode("ascii", "ignore").decode(),
            ).strip("_").lower()
            if (
                k_mem in slugs
                or k_mem in claves_cache
                or (id_m and id_m in ids_eq)
                or (slug_m and slug_m in slugs)
            ):
                _CACHE_MEMORIA_AUDITORIAS.pop(k_mem, None)
                eliminados += 1

    # Limpiar archivos de caché de resiliencia en disco (D:\agente_futbol\cache\)
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
    for s in slugs:
        ruta_c = os.path.join(cache_dir, f"cache_{s}.json")
        if os.path.isfile(ruta_c):
            try:
                os.remove(ruta_c)
                eliminados += 1
            except Exception:
                pass

    return eliminados


class ResultadoCoherenciaCompetitiva(dict):
    """
    Resultado de `validar_coherencia_competitiva_local_visita`.
    Se comporta como dict y también evalúa a True/False en contextos booleanos
    según `self['coherente']`.
    """

    def __bool__(self) -> bool:
        return bool(self.get("coherente", False))

    def __getattr__(self, item: str) -> Any:
        if item in self:
            return self[item]
        raise AttributeError(item)


def validar_coherencia_competitiva_local_visita(
    audit_local: dict[str, Any],
    audit_visita: dict[str, Any],
    pais_iso: str = "INT",
    liga_pref: Any = None,
) -> ResultadoCoherenciaCompetitiva:
    """
    COMPUERTA OBLIGATORIA DE COHERENCIA DE LIGA Y RIVALES COMUNES (Sección 3):
    Verifica inmediatamente después de obtener los 15 partidos de ambos equipos que,
    si el partido NO es un torneo internacional (`pais_iso != "INT"`):
      - Extrae el conjunto de nombres de ligas (`ligas_local` vs `ligas_visita`) y
        el conjunto de rivales (`rivales_local` vs `rivales_visita`) de los 15 partidos.
      - Si `ligas_local` y `ligas_visita` tienen 0 ligas en común Y `rivales_local` y
        `rivales_visita` tienen 0 rivales en común (como Aurora en 'Bolivia' vs
        CD Universitario en 'LPF Apertura'):
        1) Marca como `FALSO HOMÓNIMO EXTRANJERO` al equipo cuya liga no coincida con `pais_iso`/`liga_pref`.
        2) Invalida su caché inmediatamente (`eliminar_cache_equipo`).
    """
    from resolvedor_entidades import normalizar_pais_iso, normalizar_texto

    iso_esp = normalizar_pais_iso(pais_iso or (liga_pref.get("pais_iso") if isinstance(liga_pref, dict) else str(liga_pref or ""))) or "INT"

    def _extraer_ligas_y_rivales(doc: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
        ligas_norm: set[str] = set()
        ligas_raw: set[str] = set()
        rivales_norm: set[str] = set()
        if not isinstance(doc, dict):
            return ligas_norm, rivales_norm, ligas_raw
        eq = doc.get("equipo") if isinstance(doc.get("equipo"), dict) else {}
        liga_eq = str((eq or {}).get("liga") or (eq or {}).get("leagueName") or "").strip()
        if liga_eq and liga_eq.lower() not in ("desconocida", "oficial"):
            ligas_raw.add(liga_eq)
            ligas_norm.add(normalizar_texto(liga_eq))
        for p in (doc.get("partidos") or [])[:15]:
            if not isinstance(p, dict):
                continue
            t = str(p.get("torneo") or p.get("competicion") or p.get("liga") or "").strip()
            if t:
                ligas_raw.add(t)
                ligas_norm.add(normalizar_texto(t))
            r = str(p.get("rival") or "").strip()
            if r:
                rivales_norm.add(normalizar_texto(r))
        return ligas_norm, rivales_norm, ligas_raw

    ligas_l, rivales_l, ligas_raw_l = _extraer_ligas_y_rivales(audit_local)
    ligas_v, rivales_v, ligas_raw_v = _extraer_ligas_y_rivales(audit_visita)

    eq_l = (audit_local.get("equipo") or {}) if isinstance(audit_local, dict) else {}
    eq_v = (audit_visita.get("equipo") or {}) if isinstance(audit_visita, dict) else {}
    nom_l = normalizar_texto(str(eq_l.get("nombre") or eq_l.get("name") or ""))
    nom_v = normalizar_texto(str(eq_v.get("nombre") or eq_v.get("name") or ""))

    ligas_comunes = {x for x in (ligas_l & ligas_v) if x}
    rivales_comunes = {x for x in (rivales_l & rivales_v) if x}
    if nom_l and nom_l in rivales_v:
        rivales_comunes.add(nom_l)
    if nom_v and nom_v in rivales_l:
        rivales_comunes.add(nom_v)

    if iso_esp == "INT":
        # Incluso si pais_iso se pasó vacío/INT, inferirlo si alguno de los dos equipos tiene país nacional claro
        iso_inf_l = normalizar_pais_iso(eq_l.get("pais_iso") or eq_l.get("liga") or next(iter(ligas_raw_l), ""))
        iso_inf_v = normalizar_pais_iso(eq_v.get("pais_iso") or eq_v.get("liga") or next(iter(ligas_raw_v), ""))
        if iso_inf_l and iso_inf_l != "INT" and iso_inf_v and iso_inf_v != "INT" and iso_inf_l != iso_inf_v:
            iso_esp = iso_inf_l

    if iso_esp == "INT":
        return ResultadoCoherenciaCompetitiva({
            "coherente": True,
            "estado": "OK",
            "falso_homonimo": None,
            "equipo_invalido": None,
            "ligas_local": sorted(ligas_raw_l),
            "ligas_visita": sorted(ligas_raw_v),
            "ligas_comunes": sorted(ligas_comunes),
            "rivales_comunes": sorted(rivales_comunes),
            "motivo": "Torneo internacional o sin restricción nacional.",
        })

    def _coincide_pais_equipo(eq_d: dict[str, Any], ligas_raw_s: set[str]) -> bool:
        isos_detectados: set[str] = set()
        for l_txt in ligas_raw_s:
            iso_l = normalizar_pais_iso(l_txt)
            if not iso_l:
                from fuentes_xg import _pais_de_liga
                iso_l = normalizar_pais_iso(_pais_de_liga(l_txt))
            if iso_l and iso_l != "INT":
                isos_detectados.add(iso_l)
        iso_eq = normalizar_pais_iso(eq_d.get("pais_iso") or eq_d.get("pais") or eq_d.get("ccode") or "")
        if iso_eq and iso_eq != "INT" and not isos_detectados:
            isos_detectados.add(iso_eq)
        if not isos_detectados:
            return True
        return iso_esp in isos_detectados

    ok_pais_l = _coincide_pais_equipo(eq_l, ligas_raw_l)
    ok_pais_v = _coincide_pais_equipo(eq_v, ligas_raw_v)

    # Condición de rechazo: 0 ligas en común Y 0 rivales en común
    sin_ligas_ni_rivales_comunes = (len(ligas_comunes) == 0 and len(rivales_comunes) == 0)
    # Excepción únicamente para mocks sintéticos vacíos donde ambos equipos son del mismo país y no traen rivales ni torneos en partidos
    partidos_tienen_metadatos = bool(
        rivales_l or rivales_v
        or any((p.get("torneo") or p.get("competicion")) for p in (audit_local.get("partidos") or []) if isinstance(p, dict))
        or any((p.get("torneo") or p.get("competicion")) for p in (audit_visita.get("partidos") or []) if isinstance(p, dict))
    )

    if (sin_ligas_ni_rivales_comunes and (partidos_tienen_metadatos or not ok_pais_l or not ok_pais_v)) or (not ok_pais_l) or (not ok_pais_v):
        # Si ambos pertenecen explícitamente al mismo país (p.ej. Copa Nacional Primera A vs Torneo BetPlay en COL)
        # y al menos uno no es de una liga extranjera, verificar si alguno tiene liga extranjera incompatible
        if ok_pais_l and ok_pais_v and not rivales_l and not rivales_v and ligas_raw_l and ligas_raw_v:
            from fuentes_xg import _pais_de_liga
            p_l = {_pais_de_liga(x) for x in ligas_raw_l if _pais_de_liga(x)}
            p_v = {_pais_de_liga(x) for x in ligas_raw_v if _pais_de_liga(x)}
            if p_l and p_v and p_l == p_v:
                return ResultadoCoherenciaCompetitiva({
                    "coherente": True,
                    "estado": "OK",
                    "falso_homonimo": None,
                    "equipo_invalido": None,
                    "ligas_local": sorted(ligas_raw_l),
                    "ligas_visita": sorted(ligas_raw_v),
                    "ligas_comunes": sorted(ligas_comunes),
                    "rivales_comunes": sorted(rivales_comunes),
                    "motivo": "Mismo país verificado en categorías nacionales.",
                })

        if not ok_pais_v and ok_pais_l:
            lado_invalido = "visita"
            doc_inv = audit_visita
            eq_inv_nom = str(eq_v.get("nombre") or eq_v.get("name") or "Visitante")
        elif not ok_pais_l and ok_pais_v:
            lado_invalido = "local"
            doc_inv = audit_local
            eq_inv_nom = str(eq_l.get("nombre") or eq_l.get("name") or "Local")
        else:
            lado_invalido = "visita"
            doc_inv = audit_visita
            eq_inv_nom = str(eq_v.get("nombre") or eq_v.get("name") or "Visitante")

        eliminar_cache_equipo(doc_inv, pais_iso=iso_esp)
        return ResultadoCoherenciaCompetitiva({
            "coherente": False,
            "estado": "FALSO HOMÓNIMO EXTRANJERO",
            "falso_homonimo": lado_invalido,
            "equipo_invalido": eq_inv_nom,
            "ligas_local": sorted(ligas_raw_l),
            "ligas_visita": sorted(ligas_raw_v),
            "ligas_comunes": sorted(ligas_comunes),
            "rivales_comunes": sorted(rivales_comunes),
            "motivo": (
                f"FALSO HOMÓNIMO EXTRANJERO detectado en {lado_invalido} ({eq_inv_nom}): "
                f"0 ligas en común ({sorted(ligas_raw_l)} vs {sorted(ligas_raw_v)}) y "
                f"0 rivales en común para país esperado {iso_esp}."
            ),
        })

    return ResultadoCoherenciaCompetitiva({
        "coherente": True,
        "estado": "OK",
        "falso_homonimo": None,
        "equipo_invalido": None,
        "ligas_local": sorted(ligas_raw_l),
        "ligas_visita": sorted(ligas_raw_v),
        "ligas_comunes": sorted(ligas_comunes),
        "rivales_comunes": sorted(rivales_comunes),
        "motivo": "Coherencia competitiva validada.",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

def _es_valor_vacio_o_invalido(v: Any) -> bool:
    """Devuelve True si el valor es None, NaN, 'null' o 'sin_dato'."""
    if v is None:
        return True
    if isinstance(v, float) and v != v:
        return True
    if isinstance(v, str) and v.strip().lower() in ("", "none", "null", "nan", "sin_dato"):
        return True
    return False


def validar_cobertura_matriz(auditoria: dict[str, Any]) -> dict[str, Any]:
    """
    Puntos 37 y 39: Control estricto de cobertura y procedencia de las
    45 métricas × 3 periodos (`FT`, `1T`, `2T`) × 2 lados (`equipo`, `rival`) = 270 celdas
    por partido (135 por equipo).

    Distingue:
      - `fotmob`: dato publicado originalmente por FotMob.
      - `flashscore`: dato publicado por Flashscore (`df_st_1` / GraphQL).
      - `derivado`: dato deducido por identidad aritmética exacta (`1T + 2T == FT`,
        `posesion_eq + posesion_riv == 100%`, `totales - a_puerta - fuera`).
      - `sin_dato`: celda ausente (`None`) que NO se inventa con reparto 50/50.
        Si `1T` o `2T` quedan en `"sin_dato"`, la métrica se añade a
        `mercados_mitad_bloqueados`.
    """
    huecos_rellenables: list[dict[str, Any]] = []
    huecos_declarados: list[dict[str, Any]] = []
    total_valores = 0
    total_presentes = 0
    total_observados = 0
    total_estimados_modelo = 0
    por_periodo: dict[str, dict[str, int]] = {
        "FT": {"presentes": 0, "observados": 0, "total": 0},
        "1T": {"presentes": 0, "observados": 0, "total": 0},
        "2T": {"presentes": 0, "observados": 0, "total": 0},
    }
    procedencia_conteo: dict[str, int] = {
        "fotmob": 0,
        "flashscore": 0,
        "derivado": 0,
        "estimacion_modelo": 0,
        "sin_dato": 0,
    }
    procedencia_4_niveles: dict[str, int] = {
        "dato_publicado": 0,
        "derivacion_aritmetica": 0,
        "estimacion_modelo": 0,
        "dato_ausente": 0,
    }
    mercados_mitad_bloqueados_set: set[str] = set()
    partidos_lista = auditoria.get("partidos", []) or []
    n_partidos = len(partidos_lista)
    claves_45_canonicas = {cl for _, cl, _, _ in LAS_45_METRICAS_OFICIALES}

    for p in partidos_lista:
        fs = p.get("fuente_secundaria") or {}
        emparejado = bool(fs.get("pareado"))
        rellenadas = set()
        for lado in ("equipo", "rival"):
            rellenadas |= set((fs.get("rellenadas") or {}).get(lado) or [])
        periodos = p.get("periodos") or {}
        proc_partido = p.get("procedencia") if isinstance(p.get("procedencia"), dict) else {}
        for periodo in ("FT", "1T", "2T"):
            filas_raw = (periodos.get(periodo) or {}).get("filas") or []
            # Filtrar a las 45 métricas oficiales si el catálogo extendido está presente
            filas_45 = [f for f in filas_raw if str(f.get("clave") or "") in claves_45_canonicas]
            filas = filas_45 if len(filas_45) == 45 else filas_raw
            proc_per = proc_partido.get(periodo) if isinstance(proc_partido.get(periodo), dict) else {}
            for fila in filas:
                clave = str(fila.get("clave") or "")
                for lado in ("equipo", "rival"):
                    total_valores += 1
                    por_periodo[periodo]["total"] += 1
                    val_celda = fila.get(lado)
                    proc_lado = proc_per.get(lado) if isinstance(proc_per.get(lado), dict) else (
                        proc_partido.get(lado) if isinstance(proc_partido.get(lado), dict) else {}
                    )
                    etiqueta_proc = str((proc_lado or {}).get(clave) or "").lower().strip()
                    if not etiqueta_proc and fila.get("procedencia"):
                        etiqueta_proc = str(fila.get("procedencia") or "").lower().strip()
                    if fila.get("estimado"):
                        etiqueta_proc = "estimacion_modelo"

                    if not _es_valor_vacio_o_invalido(val_celda):
                        if etiqueta_proc == "estimacion_modelo":
                            total_estimados_modelo += 1
                            procedencia_conteo["estimacion_modelo"] += 1
                            procedencia_4_niveles["estimacion_modelo"] += 1
                            if periodo in ("1T", "2T") and clave:
                                mercados_mitad_bloqueados_set.add(clave)
                            continue
                        total_presentes += 1
                        total_observados += 1
                        por_periodo[periodo]["presentes"] += 1
                        por_periodo[periodo]["observados"] += 1
                        if etiqueta_proc == "derivado":
                            procedencia_conteo["derivado"] += 1
                            procedencia_4_niveles["derivacion_aritmetica"] += 1
                        elif etiqueta_proc == "flashscore" or clave in rellenadas:
                            procedencia_conteo["flashscore"] += 1
                            procedencia_4_niveles["dato_publicado"] += 1
                        else:
                            procedencia_conteo["fotmob"] += 1
                            procedencia_4_niveles["dato_publicado"] += 1
                        continue

                    procedencia_conteo["sin_dato"] += 1
                    procedencia_4_niveles["dato_ausente"] += 1
                    if periodo in ("1T", "2T") and clave:
                        mercados_mitad_bloqueados_set.add(clave)

                    # Hueco: ¿era rellenable en FT teniendo Flashscore pareado?
                    rellenable = (periodo == "FT" and emparejado
                                  and clave not in rellenadas)
                    entrada = {
                        "id_partido": p.get("id_partido"),
                        "fecha": p.get("fecha"),
                        "periodo": periodo,
                        "clave": clave,
                        "lado": lado,
                        "emparejado_flashscore": emparejado,
                    }
                    if rellenable:
                        huecos_rellenables.append(entrada)
                    else:
                        huecos_declarados.append(entrada)

    # Agrupar los declarados por métrica+periodo, para no listar 900 entradas
    resumen_declarados: dict[str, int] = {}
    for h in huecos_declarados:
        k = f"{h['clave']} ({h['periodo']})"
        resumen_declarados[k] = resumen_declarados.get(k, 0) + 1
    resumen_rellenables: dict[str, int] = {}
    for h in huecos_rellenables:
        resumen_rellenables[h["clave"]] = resumen_rellenables.get(h["clave"], 0) + 1

    nulos_totales = len(huecos_rellenables) + len(huecos_declarados)
    return {
        "version_cobertura": "v43.0",
        "celdas_por_equipo": 135,
        "celdas_por_partido": 270,
        "celdas_esperadas_totales": 270 * max(1, n_partidos) if n_partidos else 270,
        "total_valores": total_valores,
        "valores_presentes": total_presentes,
        "valores_observados": total_observados,
        "valores_estimados_modelo": total_estimados_modelo,
        "cobertura_pct": (round(100.0 * total_presentes / total_valores, 2)
                          if total_valores else None),
        "cobertura_observada_pct": (round(100.0 * total_observados / total_valores, 2)
                                    if total_valores else None),
        "por_periodo": {
            k: {"presentes": v["presentes"], "observados": v["observados"], "total": v["total"],
                "cobertura_pct": (round(100.0 * v["presentes"] / v["total"], 2)
                                  if v["total"] else None)}
            for k, v in por_periodo.items()},
        "procedencia_conteo": procedencia_conteo,
        "procedencia_4_niveles": procedencia_4_niveles,
        "mercados_mitad_bloqueados": sorted(mercados_mitad_bloqueados_set),
        "huecos_rellenables": resumen_rellenables,
        "huecos_rellenables_total": len(huecos_rellenables),
        "huecos_declarados": resumen_declarados,
        "huecos_declarados_total": len(huecos_declarados),
        "nulos_detectados": nulos_totales,
        "cero_nulos": nulos_totales == 0,
        "conforme": len(huecos_rellenables) == 0,
    }


def _slug(texto: str) -> str:
    """Nombre de archivo seguro a partir del nombre del equipo."""
    base = unicodedata.normalize("NFKD", texto or "equipo").encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").lower()
    return base or "equipo"


def _ahora_lima() -> datetime:
    return datetime.now(pytz.timezone(config.TIMEZONE))


def _fmt(valor: Any) -> str:
    """Formato legible para el informe markdown."""
    if valor is None:
        return "—"
    if isinstance(valor, float):
        if valor.is_integer():
            return str(int(valor))
        return f"{valor:.2f}".rstrip("0").rstrip(".")
    return str(valor)


# ═══════════════════════════════════════════════════════════════════════════════
# AUDITORÍA
# ═══════════════════════════════════════════════════════════════════════════════

def _valores_reales_de_matriz(matriz: MatrizPartido) -> int:
    """Cuenta cuántos valores reales (no nulos) trajo la matriz de un partido."""
    return sum(
        1
        for mat in matriz.periodos.values()
        for fila in mat.filas
        if fila.get("equipo") is not None or fila.get("rival") is not None
    )


def _contar_partidos_con_datos(doc: dict[str, Any]) -> int:
    """Partidos con valores reales en un documento de auditoría ya cargado."""
    total = 0
    for p in doc.get("partidos", []):
        if any(fila.get("equipo") is not None or fila.get("rival") is not None
               for mat in (p.get("periodos") or {}).values()
               for fila in mat.get("filas", [])):
            total += 1
    return total


def _filtrar_partidos_doc_cutoff(
    partidos: list[dict[str, Any]] | None,
    *,
    fecha_corte: Any = None,
    id_partido_excluir: Any = None,
    cutoff_timestamp: Any = None,
    exclude_match_ids: Any = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Filtra lista de dict de partidos usando `filtrar_partido_por_cutoff`."""
    from fuentes_xg import filtrar_partido_por_cutoff
    pts = list(partidos or [])
    if fecha_corte is None and cutoff_timestamp is None and id_partido_excluir is None and exclude_match_ids is None:
        return pts, False
    filtrados = []
    hubo_excluidos = False
    for p in pts:
        if not isinstance(p, dict):
            continue
        pid = p.get("id_partido") or p.get("id")
        fec = p.get("fecha")
        utc_t = p.get("utc_time") or (p.get("extra") or {}).get("utc_time") if isinstance(p.get("extra"), dict) else None
        if filtrar_partido_por_cutoff(
            fec, pid, utc_time=utc_t,
            fecha_corte=fecha_corte,
            cutoff_timestamp=cutoff_timestamp,
            id_partido_excluir=id_partido_excluir,
            exclude_match_ids=exclude_match_ids,
        ):
            filtrados.append(p)
        else:
            hubo_excluidos = True
    return filtrados, hubo_excluidos


def _llamar_escanear_oficiales(
    fuente: Any,
    equipo: dict[str, Any],
    *,
    objetivo: int,
    incluir_amistosos: bool = False,
    fecha_corte: Any = None,
    id_partido_excluir: Any = None,
    cutoff_timestamp: Any = None,
    exclude_match_ids: Any = None,
) -> Any:
    """Invoca `escanear_oficiales` con cutoff si existe y posfiltra de respaldo."""
    from fuentes_xg import filtrar_partido_por_cutoff
    hay_cutoff = any(v is not None for v in (fecha_corte, id_partido_excluir, cutoff_timestamp, exclude_match_ids))
    if not hay_cutoff:
        return fuente.escanear_oficiales(equipo, objetivo=objetivo, incluir_amistosos=incluir_amistosos)
    try:
        esc = fuente.escanear_oficiales(
            equipo,
            objetivo=objetivo,
            incluir_amistosos=incluir_amistosos,
            fecha_corte=fecha_corte,
            id_partido_excluir=id_partido_excluir,
            cutoff_timestamp=cutoff_timestamp,
            exclude_match_ids=exclude_match_ids,
        )
    except TypeError:
        esc = fuente.escanear_oficiales(equipo, objetivo=objetivo, incluir_amistosos=incluir_amistosos)
    if getattr(esc, "oficiales", None):
        esc.oficiales = [
            pf for pf in esc.oficiales
            if filtrar_partido_por_cutoff(
                getattr(pf, "fecha", None),
                getattr(pf, "id_partido", None),
                utc_time=(getattr(pf, "extra", None) or {}).get("utc_time") if isinstance(getattr(pf, "extra", None), dict) else None,
                fecha_corte=fecha_corte,
                cutoff_timestamp=cutoff_timestamp,
                id_partido_excluir=id_partido_excluir,
                exclude_match_ids=exclude_match_ids,
            )
        ]
    return esc


def _auditar_equipo_interno(
    nombre_equipo: str,
    limite: int = LIMITE_PARTIDOS_POR_DEFECTO,
    fuente: str | None = None,
    headless: bool = True,
    incluir_amistosos: bool = False,
    minimo_con_datos: int | None = None,
    progreso: Any = None,
    fuente_obj: FuenteXG | None = None,
    mejor_previo: dict[str, Any] | None = None,
    permitir_relleno_amistoso: bool = False,
    rival: str | dict[str, Any] | None = None,
    fecha_corte: Any = None,
    id_partido_excluir: Any = None,
    cutoff_timestamp: Any = None,
    exclude_match_ids: Any = None,
) -> dict[str, Any]:
    def avisar(msg: str) -> None:
        try:
            print(f"[AUDITORÍA] {msg}")
        except UnicodeEncodeError:
            print(f"[AUDITORÍA] {msg.encode('ascii', 'replace').decode('ascii')}")
        if callable(progreso):
            try:
                progreso(msg)
            except Exception:
                pass

    nombre_equipo = (nombre_equipo or "").strip()
    if not nombre_equipo:
        raise ValueError("Debes indicar un nombre de equipo.")

    liga_pref, nombre_sin_liga = extraer_liga_de_texto(nombre_equipo)
    if liga_pref and nombre_sin_liga:
        nombre_equipo = nombre_sin_liga.strip()
        if rival is None:
            rival = dict(liga_pref)
        elif isinstance(rival, dict):
            rival = {**rival, **liga_pref}
        elif isinstance(rival, str) and rival.strip():
            base_riv = _inferir_meta_rival_desde_texto(rival.strip()) or {"nombre": rival.strip()}
            rival = {**base_riv, **liga_pref}

    limite = max(1, int(limite))
    objetivo_datos = max(1, int(minimo_con_datos if minimo_con_datos is not None else limite))

    auditoria: dict[str, Any] = {
        "generado_en": _ahora_lima().strftime("%Y-%m-%d %H:%M:%S"),
        "zona_horaria": config.TIMEZONE,
        "equipo_solicitado": nombre_equipo,
        "limite_partidos": limite,
        "minimo_con_datos": objetivo_datos,
        "incluir_amistosos": incluir_amistosos,
        "fuente": None,
        "equipo": None,
        "partidos": [],
        "resumen": {},
        "escaneo": {"permitir_relleno_amistoso": bool(permitir_relleno_amistoso)},
        "advertencias": [],
    }

    fuente_obj: FuenteXG | None = fuente_obj
    fuente_primaria_abierta: FuenteXG | None = None
    try:
        if fuente_obj is None:
            fuente_obj = obtener_fuente(fuente, headless=headless, verbose=False)
        auditoria["fuente"] = fuente_obj.nombre
        avisar(f"fuente: {fuente_obj.nombre} · buscando «{nombre_equipo}»…")
        fuente_obj.abrir()
        fuente_primaria_abierta = fuente_obj

        try:
            try:
                equipo = fuente_obj.resolver_equipo(nombre_equipo, rival_contexto=rival)
            except TypeError:
                equipo = fuente_obj.resolver_equipo(nombre_equipo)
        except Exception as err_res:
            avisar(f"⚠️ Error al resolver «{nombre_equipo}» en {getattr(fuente_obj, 'nombre', 'fuente')}: {err_res}")
            equipo = None

        if not equipo and getattr(fuente_obj, "nombre", "") == "fotmob":
            avisar(f"⚠️ «{nombre_equipo}» no encontrado en FotMob; activando fallback automático a Flashscore…")
            try:
                from fuentes_xg import FuenteFlashscore
                from resolvedor_entidades import obtener_nombre_flashscore, obtener_seleccion_nacional
                fs_fallback = FuenteFlashscore(ctx_compartido=getattr(fuente_obj, "ctx", None), verbose=False)
                fs_fallback.abrir()

                nom_fs_primario = obtener_nombre_flashscore(nombre_equipo) or nombre_equipo
                sel_fs = obtener_seleccion_nacional(nombre_equipo)
                candidatos_nombres_fs = [nom_fs_primario, nombre_equipo]
                if sel_fs:
                    candidatos_nombres_fs.extend(sel_fs[2])

                vistos_fs = set()
                equipo_fs = None
                for term_fs in candidatos_nombres_fs:
                    t_clean = str(term_fs).strip()
                    if not t_clean or t_clean.lower() in vistos_fs:
                        continue
                    vistos_fs.add(t_clean.lower())
                    try:
                        equipo_fs = fs_fallback.resolver_equipo(t_clean, rival_contexto=rival)
                    except TypeError:
                        equipo_fs = fs_fallback.resolver_equipo(t_clean)
                    except Exception:
                        equipo_fs = None
                    if equipo_fs:
                        break

                if equipo_fs:
                    fuente_obj = fs_fallback
                    auditoria["fuente"] = "flashscore"
                    auditoria["proveedor"] = "flashscore"
                    auditoria["identidad_verificada"] = True
                    equipo = equipo_fs
            except Exception as err_fs:
                avisar(f"⚠️ Fallback Flashscore: {err_fs}")

        if not equipo:
            auditoria["equipo"] = {
                "id": None,
                "nombre": nombre_equipo,
                "liga": "",
                "pais_iso": "UNK",
            }
            auditoria["identidad_verificada"] = False
            auditoria["estado_identidad"] = "identidad_no_verificada"
            auditoria["motivo_identidad"] = f"No se encontró ningún equipo llamado «{nombre_equipo}» ni en FotMob ni en Flashscore tras probar todos sus alias."
            auditoria["partidos"] = []
            auditoria["advertencias"].append(auditoria["motivo_identidad"])
            return auditoria

        auditoria["equipo"] = equipo
        avisar(f"equipo: {equipo.get('nombre')} ({equipo.get('liga') or 'liga desconocida'})")

        # ── Escaneo profundo hacia atrás, saltando amistosos ────────────────
        avisar(f"escaneando el calendario hacia atrás (objetivo: {objetivo_datos} "
               f"partidos oficiales con datos, máximo {limite} auditados)…")
        escaneo = _llamar_escanear_oficiales(
            fuente_obj, equipo, objetivo=objetivo_datos, incluir_amistosos=incluir_amistosos,
            fecha_corte=fecha_corte, id_partido_excluir=id_partido_excluir,
            cutoff_timestamp=cutoff_timestamp, exclude_match_ids=exclude_match_ids)

        if not escaneo.oficiales and not escaneo.amistosos_reservados and getattr(fuente_obj, "nombre", "") == "fotmob":
            avisar(f"⚠️ {equipo.get('nombre')} sin historial en FotMob; consultando Flashscore en cascada…")
            try:
                from fuentes_xg import FuenteFlashscore
                from resolvedor_entidades import obtener_nombre_flashscore, obtener_seleccion_nacional
                fs_fallback = FuenteFlashscore(ctx_compartido=getattr(fuente_obj, "ctx", None), verbose=False)
                fs_fallback.abrir()

                nom_fs_p = obtener_nombre_flashscore(equipo.get("nombre") or nombre_equipo) or (equipo.get("nombre") or nombre_equipo)
                sel_fs2 = obtener_seleccion_nacional(equipo.get("nombre") or nombre_equipo)
                cands_fs2 = [nom_fs_p, equipo.get("nombre") or nombre_equipo]
                if sel_fs2:
                    cands_fs2.extend(sel_fs2[2])

                eq_fs = None
                vistos_fs2 = set()
                for t_fs in cands_fs2:
                    t_c = str(t_fs).strip()
                    if not t_c or t_c.lower() in vistos_fs2:
                        continue
                    vistos_fs2.add(t_c.lower())
                    try:
                        eq_fs = fs_fallback.resolver_equipo(t_c, rival_contexto=rival)
                    except TypeError:
                        eq_fs = fs_fallback.resolver_equipo(t_c)
                    except Exception:
                        eq_fs = None
                    if eq_fs:
                        break

                if eq_fs:
                    esc_fs = _llamar_escanear_oficiales(
                        fs_fallback, eq_fs, objetivo=objetivo_datos, incluir_amistosos=incluir_amistosos,
                        fecha_corte=fecha_corte, id_partido_excluir=id_partido_excluir,
                        cutoff_timestamp=cutoff_timestamp, exclude_match_ids=exclude_match_ids)
                    if esc_fs.oficiales:
                        fuente_obj = fs_fallback
                        auditoria["fuente"] = "flashscore"
                        auditoria["proveedor"] = "flashscore"
                        auditoria["identidad_verificada"] = True
                        equipo = eq_fs
                        auditoria["equipo"] = eq_fs
                        escaneo = esc_fs
            except Exception as err_fs2:
                avisar(f"⚠️ Fallback historial Flashscore: {err_fs2}")

        def _actualizar_meta_escaneo(esc) -> None:
            auditoria["escaneo"] = {
                "paginas_escaneadas": esc.paginas_escaneadas,
                "finalizados_en_la_fuente": esc.finalizados_totales,
                "oficiales_encontrados": len(esc.oficiales),
                "amistosos_excluidos": esc.amistosos_excluidos,
                "fuente_agotada": esc.fuente_agotada,
                "motivo_parada": esc.motivo_parada,
                "cola_historica_revisada": bool(getattr(esc, "cola_historica_revisada", False)),
                "permitir_relleno_amistoso": bool(permitir_relleno_amistoso),
            }

        _actualizar_meta_escaneo(escaneo)
        avisar(f"{len(escaneo.oficiales)} partidos oficiales disponibles "
               f"({escaneo.amistosos_excluidos} amistosos descartados, "
               f"{escaneo.paginas_escaneadas} página(s) de calendario)")

        if not escaneo.oficiales and not escaneo.amistosos_reservados:
            auditoria["identidad_verificada"] = False
            auditoria["estado_identidad"] = "identidad_no_verificada"
            auditoria["motivo_identidad"] = (
                f"{equipo.get('nombre')} no tiene partidos finalizados en la ventana de historial "
                f"de {fuente_obj.nombre}. Detalle: {escaneo.motivo_parada or 'sin historial'}"
            )
            auditoria["partidos"] = []
            auditoria["advertencias"].append(auditoria["motivo_identidad"])
            return auditoria

        # ── Fase 1: extracción de partidos OFICIALES con compensación ───────
        # Un partido sin datos NO se cuenta ni se incluye en `auditoria["partidos"]`,
        # y NO detiene nada: se sigue con el anterior hacia atrás (solicitando más
        # páginas a `escanear_oficiales` si fuese necesario) hasta reunir exactamente
        # `objetivo_datos` (o `limite`) partidos oficiales con datos, o agotar la fuente.
        partidos_con_datos = 0
        vacios: list[str] = []
        procesados_ids: set[str] = set()
        procesados_claves: set[tuple[str, str, str]] = set()
        rondas_backfill = 0
        MAX_RONDAS_BACKFILL = 4

        while True:
            hubo_nuevos = False
            for partido in escaneo.oficiales:
                if len(auditoria["partidos"]) >= limite or partidos_con_datos >= objetivo_datos:
                    break
                p_id = str(getattr(partido, "id_partido", "") or "")
                p_fec = str(getattr(partido, "fecha", "") or "")
                p_loc = str(getattr(partido, "local", "") or "").lower().strip()
                p_vis = str(getattr(partido, "visitante", "") or "").lower().strip()
                p_clave = (p_fec, p_loc, p_vis)
                from fuentes_xg import filtrar_partido_por_cutoff
                if not filtrar_partido_por_cutoff(
                    p_fec, p_id,
                    utc_time=(getattr(partido, "extra", None) or {}).get("utc_time") if isinstance(getattr(partido, "extra", None), dict) else None,
                    fecha_corte=fecha_corte, cutoff_timestamp=cutoff_timestamp,
                    id_partido_excluir=id_partido_excluir, exclude_match_ids=exclude_match_ids,
                ):
                    continue

                # Deduplicación estricta por match_id y por (fecha, local, visitante)
                if (p_id and p_id in procesados_ids) or (any(p_clave) and p_clave in procesados_claves):
                    continue
                if p_id:
                    procesados_ids.add(p_id)
                if any(p_clave):
                    procesados_claves.add(p_clave)

                hubo_nuevos = True
                idx_vis = len(auditoria["partidos"]) + 1
                avisar(f"[{idx_vis}/{limite}] {partido.local} vs {partido.visitante} "
                       f"({partido.fecha}) · {partido.torneo}")
                try:
                    matriz = fuente_obj.matriz_partido(partido, equipo)
                except Exception as e:
                    matriz = MatrizPartido(
                        id_partido=partido.id_partido, fecha=partido.fecha, torneo=partido.torneo,
                        equipo=str(equipo.get("nombre")),
                        rival=(partido.visitante if partido.es_local_el_equipo else partido.local),
                        condicion="casa" if partido.es_local_el_equipo else "fuera",
                        goles_equipo=None, goles_rival=None, error=f"{type(e).__name__}: {e}",
                    )
                    avisar(f"    ⚠️  error en el partido {partido.id_partido}: {e}")

                n_valores = _valores_reales_de_matriz(matriz)
                if n_valores < MIN_VALORES_PARA_CONTAR:
                    vacios.append(matriz.id_partido)
                    avisar(f"    ⚠️  sin datos ({n_valores} valores): se descarta y se compensa "
                           "con el siguiente partido oficial hacia atrás")
                    continue

                partidos_con_datos += 1
                d = matriz.a_dict()
                d["n_partido"] = len(auditoria["partidos"]) + 1
                d["valores_reales"] = n_valores
                d["es_amistoso"] = False
                d["condicion"] = "casa" if d.get("es_local") or d.get("condicion") == "casa" else "fuera"
                try:
                    from motor_recursivo_45_dcn import obtener_tier_liga
                    d["tier_competicion"] = obtener_tier_liga(d.get("torneo") or "")
                except Exception:
                    d["tier_competicion"] = 1
                auditoria["partidos"].append(d)

            if (
                len(auditoria["partidos"]) >= limite
                or partidos_con_datos >= objetivo_datos
                or escaneo.fuente_agotada
                or not hubo_nuevos
                or rondas_backfill >= MAX_RONDAS_BACKFILL
            ):
                break

            rondas_backfill += 1
            faltantes = max(1, objetivo_datos - partidos_con_datos)
            nuevo_objetivo = len(escaneo.oficiales) + faltantes + 5
            avisar(f"    ↻ backfill hacia atrás: solicitando hasta {nuevo_objetivo} "
                   f"oficiales para compensar {len(vacios)} partido(s) sin datos…")
            escaneo = _llamar_escanear_oficiales(
                fuente_obj, equipo, objetivo=nuevo_objetivo, incluir_amistosos=incluir_amistosos,
                fecha_corte=fecha_corte, id_partido_excluir=id_partido_excluir,
                cutoff_timestamp=cutoff_timestamp, exclude_match_ids=exclude_match_ids,
            )
            _actualizar_meta_escaneo(escaneo)

        # ── Fase 2: relleno opcional con amistosos ──────────────────────────
        # SOLO si el usuario lo pidió y los oficiales no alcanzaron el objetivo.
        # Se procesan en su propio bucle para poder marcarlos siempre como amistosos.
        #
        # REGLA DEL PROYECTO: el objetivo son SIEMPRE 15 partidos OFICIALES. Un
        # «9 oficiales + 6 amistosos» NO es un 15 válido, así que sumar amistosos
        # está prohibido por defecto. Para habilitarlo haría falta una autorización
        # explícita en el propio documento (`permitir_relleno_amistoso`), de modo que
        # no pueda colarse por una mala configuración aguas arriba.
        amistosos_usados = 0
        relleno_autorizado = bool(incluir_amistosos) and bool(
            (auditoria.get("escaneo") or {}).get("permitir_relleno_amistoso", False))
        if incluir_amistosos and not relleno_autorizado:
            avisar("    ℹ️  relleno con amistosos NO aplicado: el objetivo son partidos "
                   "oficiales; no se suman amistosos para llegar a 15")
        if relleno_autorizado and partidos_con_datos < objetivo_datos:
            for partido in escaneo.amistosos_reservados:
                if partidos_con_datos >= objetivo_datos or len(auditoria["partidos"]) >= limite:
                    break
                try:
                    matriz = fuente_obj.matriz_partido(partido, equipo)
                except Exception as e:
                    avisar(f"    ⚠️  amistoso {partido.id_partido} ilegible: {e}")
                    continue
                n_valores = _valores_reales_de_matriz(matriz)
                if n_valores < MIN_VALORES_PARA_CONTAR:
                    continue
                d = matriz.a_dict()
                d["n_partido"] = len(auditoria["partidos"]) + 1
                d["valores_reales"] = n_valores
                d["es_amistoso"] = True
                d["notas"] = list(d.get("notas") or []) + [
                    "Partido AMISTOSO incluido como relleno: la fuente no tenía suficientes "
                    "partidos oficiales para alcanzar el objetivo solicitado."
                ]
                auditoria["partidos"].append(d)
                partidos_con_datos += 1
                amistosos_usados += 1
                avisar(f"    ➕ relleno con amistoso: {partido.local} vs {partido.visitante} "
                       f"({partido.fecha}) · {n_valores} valores")

        auditoria["escaneo"]["amistosos_usados_como_relleno"] = amistosos_usados
        auditoria["escaneo"]["partidos_auditados"] = len(auditoria["partidos"])
        auditoria["escaneo"]["partidos_sin_datos"] = len(vacios)
        auditoria["escaneo"]["ids_sin_datos"] = vacios
        auditoria["escaneo"]["compensacion_necesaria"] = len(vacios) > 0
        auditoria["escaneo"]["objetivo_alcanzado"] = partidos_con_datos >= objetivo_datos

        # ── Backfill en cascada con Flashscore si FotMob entregó < objetivo_datos ──
        if (
            partidos_con_datos < objetivo_datos
            and getattr(fuente_obj, "nombre", "") == "fotmob"
        ):
            try:
                from fuentes_xg import FuenteFlashscore
                avisar(f"    ↻ FotMob aportó {partidos_con_datos}/{objetivo_datos}; completando en cascada desde Flashscore…")
                fs_comp = FuenteFlashscore(ctx_compartido=getattr(fuente_obj, "ctx", None), verbose=False)
                fs_comp.abrir()
                eq_fs = fs_comp.resolver_equipo(equipo.get("nombre") or nombre_equipo, rival_contexto=rival)
                if eq_fs:
                    esc_fs = _llamar_escanear_oficiales(
                        fs_comp, eq_fs, objetivo=objetivo_datos + 5, incluir_amistosos=False,
                        fecha_corte=fecha_corte, id_partido_excluir=id_partido_excluir,
                        cutoff_timestamp=cutoff_timestamp, exclude_match_ids=exclude_match_ids)
                    for p_fs in esc_fs.oficiales:
                        if len(auditoria["partidos"]) >= limite or partidos_con_datos >= objetivo_datos:
                            break
                        p_id = str(getattr(p_fs, "id_partido", "") or "")
                        p_fec = str(getattr(p_fs, "fecha", "") or "")
                        p_loc = str(getattr(p_fs, "local", "") or "").lower().strip()
                        p_vis = str(getattr(p_fs, "visitante", "") or "").lower().strip()
                        p_clave = (p_fec, p_loc, p_vis)
                        if (p_id and p_id in procesados_ids) or (any(p_clave) and p_clave in procesados_claves):
                            continue
                        # Evitar duplicar por fecha +/- 1 día si ya está en procesados_claves
                        if any(abs((datetime.strptime(p_fec, "%Y-%m-%d") - datetime.strptime(f_ex, "%Y-%m-%d")).days) <= 1
                               for (f_ex, _, _) in procesados_claves if f_ex and p_fec):
                            continue
                        try:
                            mat_fs = fs_comp.matriz_partido(p_fs, eq_fs)
                            n_val_fs = _valores_reales_de_matriz(mat_fs)
                            if n_val_fs >= MIN_VALORES_PARA_CONTAR:
                                procesados_ids.add(p_id)
                                procesados_claves.add(p_clave)
                                partidos_con_datos += 1
                                d_fs = mat_fs.a_dict()
                                d_fs["n_partido"] = len(auditoria["partidos"]) + 1
                                d_fs["valores_reales"] = n_val_fs
                                d_fs["es_amistoso"] = False
                                d_fs["condicion"] = "casa" if d_fs.get("es_local") or d_fs.get("condicion") == "casa" else "fuera"
                                auditoria["partidos"].append(d_fs)
                        except Exception:
                            continue
                    auditoria["escaneo"]["partidos_auditados"] = len(auditoria["partidos"])
                    auditoria["escaneo"]["objetivo_alcanzado"] = partidos_con_datos >= objetivo_datos
            except Exception as err_comp:
                avisar(f"    ⚠️ Backfill Flashscore no disponible: {err_comp}")

        # ── Próximos partidos programados ──────────────────────────────────
        # Se guardan para que el análisis de un cruce pueda encontrar la fecha y la
        # localía reales cruzando IDs de equipo, en vez de quedar en «no disponible»
        # cuando los dos equipos no se han enfrentado en el historial.
        try:
            auditoria["partidos_futuros"] = fuente_obj.partidos_futuros(equipo)
        except Exception as e:
            auditoria["partidos_futuros"] = []
            avisar(f"    ⚠️  próximos partidos no disponibles: {type(e).__name__}: {e}")

        if partidos_con_datos < objetivo_datos:
            oficiales_con_datos = partidos_con_datos - amistosos_usados
            faltan = objetivo_datos - partidos_con_datos
            aviso = (f"Objetivo no alcanzado: se pidieron {objetivo_datos} partidos con datos y el "
                     f"historial disponible arrojó {partidos_con_datos} "
                     f"({oficiales_con_datos} oficiales")
            aviso += (f" + {amistosos_usados} amistosos de relleno). " if amistosos_usados
                      else "). ")
            aviso += (f"Faltaron {faltan}. Motivo: {escaneo.motivo_parada or 'historial agotado'}. "
                      f"La fuente no tiene más partidos que ofrecer; no se inventan datos.")
            auditoria["advertencias"].append(aviso)
            avisar(f"⚠️  objetivo no alcanzable: {partidos_con_datos}/{objetivo_datos} "
                   f"({escaneo.motivo_parada or 'historial agotado'})")
        elif vacios:
            avisar(f"✅ objetivo alcanzado con compensación: {partidos_con_datos} partidos con datos "
                   f"({len(vacios)} descartados por falta de datos)")

    except Exception as e:
        auditoria["advertencias"].append(f"Auditoría interrumpida: {type(e).__name__}: {e}")
        avisar(f"❌ {type(e).__name__}: {e}")
        raise
    finally:
        for f_cerrar in {id(x): x for x in (fuente_obj, fuente_primaria_abierta) if x is not None}.values():
            try:
                f_cerrar.cerrar()
            except Exception:
                pass

    try:
        from cuadro_especialista import deduplicar_partidos
        auditoria["partidos"] = deduplicar_partidos(auditoria.get("partidos") or [])
    except Exception:
        pass

    auditoria["resumen"] = _construir_resumen(auditoria)

    # ── Protección: no degradar un dataset mejor que ya teníamos ────────────
    if mejor_previo:
        previos = _contar_partidos_con_datos(mejor_previo)
        nuevos = auditoria["resumen"].get("partidos_con_datos") or 0
        if previos > nuevos:
            nota = (f"Se conservó la auditoría anterior porque aportaba más partidos con datos "
                    f"({previos} frente a {nuevos} del escaneo nuevo).")
            mejor_previo.setdefault("advertencias", []).append(nota)
            avisar(f"↩️  {nota}")
            return mejor_previo

    avisar(f"auditoría completa: {auditoria['resumen'].get('partidos_con_datos')} partidos con datos "
           f"de {len(auditoria['partidos'])} extraídos")
    return auditoria


CACHE_RESILIENCIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")


def _guardar_cache_resiliencia(slug: str, doc: dict[str, Any]) -> None:
    r"""Guarda copia de respaldo de los últimos 15 partidos validados en cache/."""
    try:
        os.makedirs(CACHE_RESILIENCIA_DIR, exist_ok=True)
        prov = str(doc.get("proveedor") or "fotmob").strip().lower()
        if not slug.startswith("fotmob_") and not slug.startswith("flashscore_"):
            slug_prov = f"{prov}_{slug}"
        else:
            slug_prov = slug
        doc["proveedor"] = prov
        ruta = os.path.join(CACHE_RESILIENCIA_DIR, f"cache_{slug_prov}.json")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
    except Exception:
        pass

def _cargar_cache_resiliencia(slug: str, nombre_equipo: str, meta_ctx: Any = None) -> dict[str, Any] | None:
    meta_ctx = dict(meta_ctx) if isinstance(meta_ctx, dict) else {"pais_iso": "INT", "liga": "Desconocida", "es_femenino": False, "es_juvenil": False}
    try:
        from resolvedor_entidades import normalizar_pais_iso
        ruta = os.path.join(CACHE_RESILIENCIA_DIR, f"cache_{slug}.json")
        if not os.path.isfile(ruta):
            if not slug.startswith("fotmob_") and not slug.startswith("flashscore_"):
                ruta_prov = os.path.join(CACHE_RESILIENCIA_DIR, f"cache_fotmob_{slug}.json")
                if os.path.isfile(ruta_prov):
                    ruta = ruta_prov
                else:
                    slug_simple = _slug(nombre_equipo)
                    ruta_simple = os.path.join(CACHE_RESILIENCIA_DIR, f"cache_{slug_simple}.json")
                    if os.path.isfile(ruta_simple):
                        ruta = ruta_simple
                    else:
                        return None
            else:
                slug_simple = _slug(nombre_equipo)
                ruta_simple = os.path.join(CACHE_RESILIENCIA_DIR, f"cache_{slug_simple}.json")
                if os.path.isfile(ruta_simple):
                    ruta = ruta_simple
                else:
                    return None
        with open(ruta, "r", encoding="utf-8") as f:
            doc = json.load(f)

        base_nom = os.path.basename(ruta)
        tiene_prefijo_proveedor = base_nom.startswith("cache_fotmob_") or base_nom.startswith("cache_flashscore_")
        tiene_proveedor_doc = bool(doc.get("proveedor") in ("fotmob", "flashscore"))

        # Punto 44 y 45 (Etapa 1.2): Las entradas antiguas sin proveedor se marcan como
        # antiguas (legado_sin_proveedor) y no se reutilizan; no se borran.
        if not tiene_prefijo_proveedor or not tiene_proveedor_doc:
            doc["legado_sin_proveedor"] = True
            doc["antiguo"] = True
            doc["no_reutilizable"] = True
            try:
                with open(ruta, "w", encoding="utf-8") as f_out:
                    json.dump(doc, f_out, ensure_ascii=False)
            except Exception:
                pass
            return None
        eq_doc = doc.get("equipo") or {}
        ident_doc = doc.get("identidad") or {}
        id_prov = eq_doc.get("id") or ident_doc.get("id_proveedor")
        pais_doc = eq_doc.get("pais_iso") or ident_doc.get("pais_iso") or _pais_de_liga(eq_doc.get("liga"))

        pais_esperado = normalizar_pais_iso(meta_ctx.get("pais_iso"))
        # Si el país del archivo contradice el país esperado, NO reutilizar (ej. Liverpool Inglaterra en consulta de Uruguay)
        if pais_esperado and pais_esperado != "INT" and pais_doc and pais_doc != "INT" and pais_doc != pais_esperado:
            doc["antiguo"] = True
            doc["no_reutilizable"] = True
            try:
                with open(ruta, "w", encoding="utf-8") as f_out:
                    json.dump(doc, f_out, ensure_ascii=False)
            except Exception:
                pass
            return None

        # Si el archivo carece de ID de proveedor y país en una consulta con país requerido, marcarlo antiguo y NO reutilizar
        if not id_prov and not pais_doc and pais_esperado and pais_esperado != "INT":
            doc["antiguo"] = True
            doc["no_reutilizable"] = True
            try:
                with open(ruta, "w", encoding="utf-8") as f_out:
                    json.dump(doc, f_out, ensure_ascii=False)
            except Exception:
                pass
            return None

        tiene_ctx_real = bool(
            (meta_ctx.get("pais_iso") and meta_ctx.get("pais_iso") != "INT")
            or (meta_ctx.get("liga") and meta_ctx.get("liga") != "Desconocida")
            or meta_ctx.get("pais")
        )
        pts = puntuar_candidato(
            {
                "id": eq_doc.get("id"),
                "name": eq_doc.get("nombre"),
                "leagueName": eq_doc.get("liga"),
                "leagueId": eq_doc.get("id_liga"),
                "score": 100.0,
            },
            nombre_equipo,
            rival_meta=meta_ctx if tiene_ctx_real else None,
        )
        if pts[0] < 0 or pts[1] == 0 or (pts[4] < 0 and tiene_ctx_real):
            return None
        aviso = "[Aviso: Datos leídos desde caché local por intermitencia temporal del proveedor]"
        advs = doc.setdefault("advertencias", [])
        if aviso not in advs:
            advs.append(aviso)
        doc["desde_cache_resiliencia"] = True
        return doc
    except Exception:
        return None


def auditar_equipo(
    nombre_equipo: str,
    limite: int = LIMITE_PARTIDOS_POR_DEFECTO,
    fuente: str | None = None,
    headless: bool = True,
    incluir_amistosos: bool = False,
    minimo_con_datos: int | None = None,
    progreso: Any = None,
    fuente_obj: FuenteXG | None = None,
    mejor_previo: dict[str, Any] | None = None,
    permitir_relleno_amistoso: bool = False,
    rival: str | dict[str, Any] | None = None,
    fecha_corte: Any = None,
    id_partido_excluir: Any = None,
    cutoff_timestamp: Any = None,
    exclude_match_ids: Any = None,
) -> dict[str, Any]:
    """
    Ejecuta la auditoría histórica completa de un equipo.
    Implementa Lock por equipo para serializar ejecuciones y evitar descargas duplicadas,
    así como caché en memoria y caché de resiliencia en disco (D:\\agente_futbol\\cache\\).
    """
    meta_ctx: dict[str, Any] = {
        "pais_iso": "INT",
        "liga": "Desconocida",
        "es_femenino": False,
        "es_juvenil": False,
    }
    liga_pref, nombre_sin_liga = extraer_liga_de_texto(nombre_equipo or "")
    if liga_pref and nombre_sin_liga:
        nombre_equipo = nombre_sin_liga.strip()
        if rival is None:
            rival = dict(liga_pref)
        elif isinstance(rival, dict):
            rival = {**rival, **liga_pref}
        elif isinstance(rival, str) and rival.strip():
            base_riv = _inferir_meta_rival_desde_texto(rival.strip()) or {"nombre": rival.strip()}
            rival = {**base_riv, **liga_pref}

    if isinstance(rival, dict):
        for k, v in rival.items():
            if v is not None and v != "":
                meta_ctx[k] = v
    elif isinstance(rival, str) and rival.strip():
        inf_riv = _inferir_meta_rival_desde_texto(rival.strip()) or {"nombre": rival.strip()}
        for k, v in inf_riv.items():
            if v is not None and v != "":
                meta_ctx[k] = v
    if isinstance(liga_pref, dict):
        for k, v in liga_pref.items():
            if v is not None and v != "":
                meta_ctx[k] = v

    from resolvedor_entidades import construir_clave_cache_entidad, detectar_categoria_desde_texto, resolver_equipo_individual
    nom_res_eq, iso_res_eq = resolver_equipo_individual(
        nombre_equipo,
        pais_iso_contexto=str(meta_ctx.get("pais_iso") or "") if meta_ctx.get("pais_iso") != "INT" else None,
    )
    if iso_res_eq and meta_ctx.get("pais_iso") in (None, "", "INT"):
        meta_ctx["pais_iso"] = iso_res_eq

    pais_iso_ctx = (
        (meta_ctx.get("pais_iso") if meta_ctx.get("pais_iso") != "INT" else "")
        or meta_ctx.get("pais")
        or (liga_pref or {}).get("pais_iso")
        or (liga_pref or {}).get("pais")
        or iso_res_eq
        or "INT"
    )
    es_fem_ctx, es_juv_ctx = detectar_categoria_desde_texto(
        f"{nombre_equipo} {meta_ctx.get('liga', '') if meta_ctx.get('liga') != 'Desconocida' else ''} {meta_ctx.get('nombre', '')}"
    )
    meta_ctx["es_femenino"] = bool(meta_ctx.get("es_femenino") or es_fem_ctx)
    meta_ctx["es_juvenil"] = bool(meta_ctx.get("es_juvenil") or es_juv_ctx)

    clave_cache_entidad = construir_clave_cache_entidad(
        nombre_equipo, str(pais_iso_ctx), meta_ctx["es_femenino"], meta_ctx["es_juvenil"]
    )
    slug_equipo = _slug(nombre_equipo)
    lock_equipo = _obtener_lock_equipo(clave_cache_entidad)
    tiene_ctx_real = bool(
        (meta_ctx.get("pais_iso") and meta_ctx.get("pais_iso") != "INT")
        or (meta_ctx.get("liga") and meta_ctx.get("liga") != "Desconocida")
        or meta_ctx.get("pais")
    )

    with lock_equipo:
        if fuente_obj is None:
            doc_mem = obtener_auditoria_en_memoria(clave_cache_entidad)
            if doc_mem is None and slug_equipo:
                cand_mem = obtener_auditoria_en_memoria(slug_equipo)
                if cand_mem:
                    cand_pais = (cand_mem.get("equipo") or {}).get("pais_iso") or (cand_mem.get("identidad") or {}).get("pais_iso")
                    if pais_iso_ctx not in ("", "INT") and cand_pais and cand_pais != pais_iso_ctx:
                        doc_mem = None
                    else:
                        doc_mem = cand_mem
            if doc_mem is not None:
                eq_doc = doc_mem.get("equipo") or {}
                pts_mem = puntuar_candidato(
                    {
                        "id": eq_doc.get("id"),
                        "name": eq_doc.get("nombre"),
                        "leagueName": eq_doc.get("liga"),
                        "leagueId": eq_doc.get("id_liga"),
                        "score": 100.0,
                    },
                    nombre_equipo,
                    rival_meta=meta_ctx if tiene_ctx_real else None,
                )
                coherente_mem = pts_mem[0] >= 0 and pts_mem[1] > 0 and not (pts_mem[4] < 0 and tiene_ctx_real)
                _, mem_fuga = _filtrar_partidos_doc_cutoff(
                    doc_mem.get("partidos") or [],
                    fecha_corte=fecha_corte, id_partido_excluir=id_partido_excluir,
                    cutoff_timestamp=cutoff_timestamp, exclude_match_ids=exclude_match_ids,
                )
                if coherente_mem and not mem_fuga:
                    partidos_mem = doc_mem.get("partidos") or []
                    res_mem = doc_mem.get("resumen") or {}
                    con_datos_mem = res_mem.get("partidos_con_datos") or len(partidos_mem)
                    objetivo_datos = max(1, int(minimo_con_datos if minimo_con_datos is not None else limite))
                    if con_datos_mem >= objetivo_datos:
                        if callable(progreso):
                            try:
                                progreso(f"reutilizando auditoría en memoria para «{nombre_equipo}» ({con_datos_mem} partidos con datos)")
                            except Exception:
                                pass
                        return doc_mem

        try:
            from fuentes_xg import ejecutar_sync_seguro
            kw_extra_cut: dict[str, Any] = {}
            if fecha_corte is not None:
                kw_extra_cut["fecha_corte"] = fecha_corte
            if id_partido_excluir is not None:
                kw_extra_cut["id_partido_excluir"] = id_partido_excluir
            if cutoff_timestamp is not None:
                kw_extra_cut["cutoff_timestamp"] = cutoff_timestamp
            if exclude_match_ids is not None:
                kw_extra_cut["exclude_match_ids"] = exclude_match_ids
            res = ejecutar_sync_seguro(
                _auditar_equipo_interno,
                nombre_equipo=nombre_equipo,
                limite=limite,
                fuente=fuente,
                headless=headless,
                incluir_amistosos=incluir_amistosos,
                minimo_con_datos=minimo_con_datos,
                progreso=progreso,
                fuente_obj=fuente_obj,
                mejor_previo=mejor_previo,
                permitir_relleno_amistoso=permitir_relleno_amistoso,
                rival=rival,
                **kw_extra_cut,
            )
        except Exception:
            doc_resiliencia = _cargar_cache_resiliencia(clave_cache_entidad, nombre_equipo, meta_ctx=meta_ctx) or _cargar_cache_resiliencia(slug_equipo, nombre_equipo, meta_ctx=meta_ctx)
            if doc_resiliencia is not None:
                pts_r, fuga_r = _filtrar_partidos_doc_cutoff(
                    doc_resiliencia.get("partidos") or [],
                    fecha_corte=fecha_corte, id_partido_excluir=id_partido_excluir,
                    cutoff_timestamp=cutoff_timestamp, exclude_match_ids=exclude_match_ids,
                )
                if fuga_r:
                    doc_resiliencia = dict(doc_resiliencia)
                    doc_resiliencia["partidos"] = pts_r
                return doc_resiliencia
            raise

        eq_res = res.get("equipo") or {}
        clave_id_oficial = eq_res.get("clave_cache") or construir_clave_cache_entidad(
            eq_res.get("id") or eq_res.get("nombre") or nombre_equipo,
            str(eq_res.get("pais_iso") or pais_iso_ctx or ""),
            bool(eq_res.get("es_femenino", es_fem_ctx)),
            bool(eq_res.get("es_juvenil", es_juv_ctx)),
            competicion=meta_ctx.get("liga") or "",
        )
        res["identidad"] = {
            "id_proveedor": str(eq_res.get("id") or ""),
            "nombre_canonico": str(eq_res.get("nombre") or nombre_equipo),
            "pais_iso": str(eq_res.get("pais_iso") or pais_iso_ctx or ""),
            "competicion": str(meta_ctx.get("liga") or ""),
            "es_femenino": bool(eq_res.get("es_femenino", es_fem_ctx)),
            "es_juvenil": bool(eq_res.get("es_juvenil", es_juv_ctx)),
            "clave_cache": clave_id_oficial,
            "criterio_desempate": meta_ctx.get("criterio_desempate", "directo"),
            "confianza": "alta",
        }
        res["estado_identidad"] = "verificada"
        guardar_auditoria_en_memoria(clave_id_oficial, res)
        guardar_auditoria_en_memoria(clave_cache_entidad, res)
        _guardar_cache_resiliencia(clave_id_oficial, res)
        _guardar_cache_resiliencia(clave_cache_entidad, res)
        nom_resuelto = eq_res.get("nombre")
        if nom_resuelto and _slug(nom_resuelto) != slug_equipo:
            guardar_auditoria_en_memoria(_slug(nom_resuelto), res)
            _guardar_cache_resiliencia(_slug(nom_resuelto), res)
        return res


def verificar_consistencia_recuento_auditoria(resumen: dict[str, Any], partidos: list[dict[str, Any]]) -> bool:
    """
    Compara los conteos de resumen (localizados, con marcador, con detalle FT, 1T, 2T, solo marcador)
    contra los registros originales fila por fila. Falla si difieren.
    """
    n_partidos = len(partidos)
    if resumen.get("partidos_auditados") != n_partidos:
        return False
    if resumen.get("partidos_oficiales_localizados") != n_partidos:
        return False
    con_marcador = sum(1 for p in partidos if p.get("goles_equipo") is not None and p.get("goles_rival") is not None)
    if resumen.get("partidos_con_marcador") != con_marcador:
        return False
    con_ft = sum(1 for p in partidos if len((p.get("resumen_claves") or {}).get("FT", {}).get("equipo", [])) >= 1)
    if resumen.get("partidos_con_detalle_ft") != con_ft:
        return False
    con_1t = sum(1 for p in partidos if len((p.get("resumen_claves") or {}).get("1T", {}).get("equipo", [])) >= 1)
    if resumen.get("partidos_con_detalle_1t") != con_1t:
        return False
    con_2t = sum(1 for p in partidos if len((p.get("resumen_claves") or {}).get("2T", {}).get("equipo", [])) >= 1)
    if resumen.get("partidos_con_detalle_2t") != con_2t:
        return False
    con_solo = sum(1 for p in partidos if (p.get("goles_equipo") is not None and p.get("goles_rival") is not None) and len((p.get("resumen_claves") or {}).get("FT", {}).get("equipo", [])) == 0)
    if resumen.get("partidos_solo_marcador") != con_solo:
        return False
    return True


def _construir_resumen(auditoria: dict[str, Any]) -> dict[str, Any]:
    partidos = auditoria.get("partidos", [])
    n_partidos = len(partidos)

    # 1. Analizar cada fila por periodo y claves observadas (máx 45 por periodo)
    claves_45_oficiales = {cl for _, cl, _, _ in LAS_45_METRICAS_OFICIALES}
    con_marcador_lista = []
    con_detalle_ft_lista = []
    con_detalle_1t_lista = []
    con_detalle_2t_lista = []
    con_detalle_1t_2t_lista = []
    solo_marcador_lista = []

    cobertura: dict[str, int] = {}

    for p in partidos:
        resumen_claves_p: dict[str, Any] = {}
        periodos = p.get("periodos") or {}

        ge = p.get("goles_equipo")
        gr = p.get("goles_rival")
        tiene_marcador = (ge is not None and gr is not None)
        if tiene_marcador:
            con_marcador_lista.append(p)

        for periodo in ("FT", "1T", "2T"):
            mat = periodos.get(periodo) or {}
            filas = mat.get("filas") or []
            obs_eq = set()
            obs_rv = set()
            for fila in filas:
                if not isinstance(fila, dict):
                    continue
                k = str(fila.get("clave") or "").strip()
                if not k or k not in claves_45_oficiales:
                    continue
                v_eq = fila.get("equipo")
                v_rv = fila.get("rival")
                es_est = bool(fila.get("estimado"))
                proc = str(fila.get("procedencia") or "").lower()

                if v_eq is not None and not es_est and proc not in ("sin_dato", "dato_ausente", "estimacion_modelo"):
                    obs_eq.add(k)
                if v_rv is not None and not es_est and proc not in ("sin_dato", "dato_ausente", "estimacion_modelo"):
                    obs_rv.add(k)

            resumen_claves_p[periodo] = {
                "equipo": sorted(obs_eq),
                "rival": sorted(obs_rv),
                "k_45_equipo": len(obs_eq),
                "k_45_rival": len(obs_rv),
            }
            if periodo == "FT":
                for k_obs in obs_eq:
                    cobertura[k_obs] = cobertura.get(k_obs, 0) + 1

        p["resumen_claves"] = resumen_claves_p

        tiene_ft = (resumen_claves_p.get("FT", {}).get("k_45_equipo", 0) > 0)
        tiene_1t = (resumen_claves_p.get("1T", {}).get("k_45_equipo", 0) > 0)
        tiene_2t = (resumen_claves_p.get("2T", {}).get("k_45_equipo", 0) > 0)

        if tiene_ft:
            con_detalle_ft_lista.append(p)
        if tiene_1t:
            con_detalle_1t_lista.append(p)
        if tiene_2t:
            con_detalle_2t_lista.append(p)
        if tiene_1t and tiene_2t:
            con_detalle_1t_2t_lista.append(p)
        if tiene_marcador and not tiene_ft:
            solo_marcador_lista.append(p)

    con_datos = con_detalle_ft_lista
    sin_datos = [p for p in partidos if p not in con_datos]

    partidos_oficiales_localizados = n_partidos
    partidos_con_marcador = len(con_marcador_lista)
    partidos_con_detalle_ft = len(con_detalle_ft_lista)
    partidos_con_detalle_1t = len(con_detalle_1t_lista)
    partidos_con_detalle_2t = len(con_detalle_2t_lista)
    partidos_con_detalle_1t_2t = len(con_detalle_1t_2t_lista)
    partidos_solo_marcador = len(solo_marcador_lista)

    historial_incompleto = (partidos_oficiales_localizados < 15)

    if historial_incompleto:
        etiqueta_resumen = f"{partidos_oficiales_localizados}/15 oficiales (historial_oficial_incompleto)"
    else:
        etiqueta_resumen = f"15/15 oficiales · {partidos_con_detalle_ft}/15 con detalle FT · {partidos_solo_marcador}/15 solo con marcador"

    etiquetas = {d.clave: d.etiqueta for d in CATALOGO_METRICAS}
    cobertura_legible = {
        etiquetas.get(k, k): {
            "partidos_con_dato": v,
            "cobertura_pct": round(v / max(1, n_partidos) * 100, 1),
        }
        for k, v in sorted(cobertura.items(), key=lambda kv: -kv[1])
    }
    ausentes_totales = [
        etiquetas.get(d.clave, d.clave)
        for d in CATALOGO_METRICAS
        if cobertura.get(d.clave, 0) == 0
    ]

    resumen = {
        "partidos_solicitados": auditoria.get("limite_partidos"),
        "minimo_con_datos": auditoria.get("minimo_con_datos"),
        "objetivo_alcanzado": (auditoria.get("escaneo") or {}).get("objetivo_alcanzado"),
        "partidos_auditados": n_partidos,
        "partidos_obtenidos": n_partidos,
        "partidos_oficiales_localizados": partidos_oficiales_localizados,
        "partidos_con_marcador": partidos_con_marcador,
        "partidos_con_detalle_ft": partidos_con_detalle_ft,
        "partidos_con_detalle_1t": partidos_con_detalle_1t,
        "partidos_con_detalle_2t": partidos_con_detalle_2t,
        "partidos_con_detalle_1t_2t": partidos_con_detalle_1t_2t,
        "partidos_solo_marcador": partidos_solo_marcador,
        "historial_oficial_incompleto": historial_incompleto,
        "etiqueta_resumen_cobertura": etiqueta_resumen,
        "partidos_con_datos": len(con_datos),
        "partidos_sin_datos": len(sin_datos),
        "ids_sin_datos": [p.get("id_partido") for p in sin_datos],
        "metricas_solicitadas": len(CATALOGO_METRICAS),
        "metricas_con_datos": len(cobertura),
        "metricas_sin_datos_en_ningun_partido": ausentes_totales,
        "cobertura_por_metrica": cobertura_legible,
    }

    # Verificación estricta contra registros originales
    assert verificar_consistencia_recuento_auditoria(resumen, partidos), (
        f"Inconsistencia en los conteos de auditoría contra registros originales: "
        f"auditados={n_partidos}, con_marcador={partidos_con_marcador}, con_ft={partidos_con_detalle_ft}"
    )

    return resumen


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORTACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

def exportar_auditoria(auditoria: dict[str, Any], directorio: str | None = None,
                       sufijo_tiempo: str | None = None) -> dict[str, str]:
    """
    Escribe las 4 salidas de la auditoría y devuelve sus rutas.

    sufijo_tiempo:
      • None (por defecto) → nombre base `auditoria_<equipo>_<fecha>`.
      • ""                 → añade la hora actual (evita sobrescribir auditorías
                             anteriores del mismo equipo y día).
      • "HHMMSS"           → usa ese sufijo concreto.

    No falla si falta pandas: la matriz ancha se escribe con csv estándar.
    """
    equipo = (auditoria.get("equipo") or {}).get("nombre") or auditoria.get("equipo_solicitado", "equipo")
    ahora = _ahora_lima()
    fecha = ahora.strftime("%Y-%m-%d")
    base_dir = directorio or os.path.join(AUDITORIA_DIR, fecha)
    os.makedirs(base_dir, exist_ok=True)
    if sufijo_tiempo is None:
        marca = ""
    elif sufijo_tiempo == "":
        marca = "_" + ahora.strftime("%H%M%S")
    else:
        marca = "_" + sufijo_tiempo
    raiz = os.path.join(base_dir, f"auditoria_{_slug(equipo)}_{fecha}{marca}")

    rutas = {"json": raiz + ".json", "csv": raiz + ".csv",
             "csv_matriz": raiz + "_matriz.csv", "markdown": raiz + ".md"}

    with open(rutas["json"], "w", encoding="utf-8") as f:
        json.dump(auditoria, f, ensure_ascii=False, indent=1)

    _escribir_csv_largo(auditoria, rutas["csv"])
    _escribir_csv_ancho(auditoria, rutas["csv_matriz"])
    with open(rutas["markdown"], "w", encoding="utf-8") as f:
        f.write(generar_markdown(auditoria))
    try:
        from banco_historico import guardar_en_banco_no_bloqueante
        guardar_en_banco_no_bloqueante(doc_local=auditoria)
    except Exception as _e_banco:
        print(f"[BANCO HISTORICO] ⚠️ Hook exportar_auditoria omitido: {_e_banco}")
    return rutas


def _escribir_csv_largo(auditoria: dict[str, Any], ruta: str) -> None:
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_CSV)
        w.writeheader()
        for p in auditoria.get("partidos", []):
            for periodo in ORDEN_PERIODOS:
                mat = (p.get("periodos") or {}).get(periodo) or {}
                for fila in mat.get("filas", []):
                    w.writerow({
                        "equipo_auditado": p.get("equipo"),
                        "n_partido": p.get("n_partido"),
                        "id_partido": p.get("id_partido"),
                        "fecha": p.get("fecha"),
                        "torneo": p.get("torneo"),
                        "condicion": p.get("condicion"),
                        "es_local": (p.get("condicion") == "casa"),
                        "rival": p.get("rival"),
                        "goles_equipo": p.get("goles_equipo"),
                        "goles_rival": p.get("goles_rival"),
                        "periodo": periodo,
                        "categoria": fila.get("categoria"),
                        "metrica": fila.get("metrica"),
                        "clave": fila.get("clave"),
                        "valor_equipo": fila.get("equipo"),
                        "valor_rival": fila.get("rival"),
                        "disponible": (fila.get("equipo") is not None or fila.get("rival") is not None),
                    })


def _escribir_csv_ancho(auditoria: dict[str, Any], ruta: str) -> None:
    """
    Matriz ancha: 1 fila por partido, columnas <clave>_eq_<periodo> y <clave>_riv_<periodo>.
    Es el formato pensado para pandas: pd.read_csv(ruta).
    """
    claves = [d.clave for d in CATALOGO_METRICAS]
    columnas = (["n_partido", "id_partido", "fecha", "torneo", "condicion", "es_local",
                 "equipo", "rival", "goles_equipo", "goles_rival"]
                + [f"{c}_eq_{p}" for p in ORDEN_PERIODOS for c in claves]
                + [f"{c}_riv_{p}" for p in ORDEN_PERIODOS for c in claves])
    filas_salida = []
    for p in auditoria.get("partidos", []):
        fila = {k: p.get(k) for k in ("n_partido", "id_partido", "fecha", "torneo",
                                      "condicion", "equipo", "rival",
                                      "goles_equipo", "goles_rival")}
        fila["es_local"] = (p.get("condicion") == "casa")
        for periodo in ORDEN_PERIODOS:
            mat = (p.get("periodos") or {}).get(periodo) or {}
            por_clave = {f["clave"]: f for f in mat.get("filas", [])}
            for clave in claves:
                f = por_clave.get(clave, {})
                fila[f"{clave}_eq_{periodo}"] = f.get("equipo")
                fila[f"{clave}_riv_{periodo}"] = f.get("rival")
        filas_salida.append(fila)

    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        w.writerows(filas_salida)


# ═══════════════════════════════════════════════════════════════════════════════
# INFORME MARKDOWN
# ═══════════════════════════════════════════════════════════════════════════════

def _resumen_procedencia(auditoria: dict[str, Any]) -> dict[str, Any]:
    """
    Agrega la procedencia de todos los valores del dataset.

    Devuelve qué métricas se rellenaron desde la fuente secundaria o se derivaron,
    en cuántos partidos, y la identidad del club en esa fuente. Solo cuenta lo que
    realmente se rellenó (no las claves que ya venían de FotMob).
    """
    etiquetas = {d.clave: d.etiqueta for d in CATALOGO_METRICAS}
    rellenadas: dict[str, dict[str, Any]] = {}
    partidos_con_relleno = 0
    identidad = ""
    for p in auditoria.get("partidos", []) or []:
        fs = p.get("fuente_secundaria") or {}
        if fs.get("pareado"):
            if not identidad:
                identidad = (f"id `{fs.get('id_flashscore')}`"
                             + (" (bandos invertidos respecto a la fuente)"
                                if fs.get("bandos_invertidos") else ""))
        r = (fs.get("rellenadas") or {})
        claves_partido: set[str] = set()
        for lado in ("equipo", "rival"):
            for clave in (r.get(lado) or []):
                claves_partido.add(clave)
        if claves_partido:
            partidos_con_relleno += 1
        # Procedencia efectiva por clave (se toma la del primer lado donde conste)
        proc = ((p.get("procedencia") or {}).get("FT") or {})
        for clave in claves_partido:
            procedencia = (proc.get("equipo", {}).get(clave)
                           or proc.get("rival", {}).get(clave) or "flashscore")
            entrada = rellenadas.setdefault(clave, {
                "etiqueta": etiquetas.get(clave, clave),
                "procedencia": procedencia, "partidos": 0})
            entrada["partidos"] += 1
    return {
        "rellenadas": rellenadas,
        "partidos_con_relleno": partidos_con_relleno,
        "hay_fuente_secundaria": bool(identidad),
        "identidad": identidad or "no resuelta",
    }


def generar_markdown(auditoria: dict[str, Any]) -> str:
    """Informe consolidado: una matriz por partido, con FT / 1T / 2T lado a lado."""
    equipo = (auditoria.get("equipo") or {}).get("nombre") or auditoria.get("equipo_solicitado")
    resumen = auditoria.get("resumen") or {}
    lineas: list[str] = []

    lineas.append(f"# Auditoría Histórica Profunda — {equipo}")
    lineas.append(f"**Generado**: {auditoria.get('generado_en')} ({auditoria.get('zona_horaria')})  ")
    lineas.append(f"**Fuente de datos**: {auditoria.get('fuente')}  ")
    lineas.append(f"**Partidos solicitados**: {auditoria.get('limite_partidos')} · "
                  f"**obtenidos**: {resumen.get('partidos_obtenidos')} · "
                  f"**con datos**: {resumen.get('partidos_con_datos')}  ")
    lineas.append(f"**Amistosos**: {'incluidos como relleno si faltan oficiales' if auditoria.get('incluir_amistosos') else 'excluidos'}  ")
    escaneo = auditoria.get("escaneo") or {}
    if escaneo:
        relleno = escaneo.get("amistosos_usados_como_relleno") or 0
        lineas.append(
            f"**Escaneo hacia atrás**: {escaneo.get('paginas_escaneadas')} página(s) de calendario · "
            f"{escaneo.get('finalizados_en_la_fuente')} finalizados en la fuente · "
            f"{escaneo.get('amistosos_excluidos')} amistosos detectados"
            + (f" · {relleno} usados como relleno" if relleno else "") + "  ")
    if (auditoria.get("equipo") or {}).get("liga"):
        lineas.append(f"**Competición principal**: {(auditoria['equipo']).get('liga')}  ")
    lineas.append("")

    if resumen.get("objetivo_alcanzado") is False:
        lineas.append(
            f"> 🛑 **Dataset incompleto, declarado como tal**: se pidieron "
            f"{resumen.get('minimo_con_datos')} partidos oficiales con datos y el historial "
            f"disponible arrojó {resumen.get('partidos_con_datos')}. "
            f"Motivo: {escaneo.get('motivo_parada') or 'historial agotado'}. "
            f"No se han añadido partidos de relleno ni valores inventados.")
        lineas.append("")
    elif escaneo.get("compensacion_necesaria"):
        lineas.append(
            f"> ✅ **Compensación aplicada**: se descartaron "
            f"{escaneo.get('partidos_sin_datos')} partido(s) sin estadísticas y el escaneo "
            f"continuó hacia atrás hasta completar {resumen.get('partidos_con_datos')} "
            f"partidos oficiales con datos.")
        lineas.append("")

    if resumen.get("metricas_sin_datos_en_ningun_partido"):
        lineas.append("> ⚠️ **Métricas solicitadas que esta fuente NO publica** "
                      "(quedan como nulo en todo el dataset): "
                      + ", ".join(resumen["metricas_sin_datos_en_ningun_partido"]))
        lineas.append("")

    lineas.append("## Cobertura de métricas")
    lineas.append("")
    lineas.append("| Métrica | Partidos con dato | Cobertura |")
    lineas.append("|---|---|---|")
    for metrica, datos in (resumen.get("cobertura_por_metrica") or {}).items():
        lineas.append(f"| {metrica} | {datos['partidos_con_dato']} | {datos['cobertura_pct']}% |")
    lineas.append("")

    # ── Procedencia de los datos ───────────────────────────────────────────
    # De dónde salió cada valor: fotmob (primaria), flashscore (relleno),
    # derivado (cociente aritmético) o sin_dato (no lo publica ninguna).
    proc_global = _resumen_procedencia(auditoria)
    if proc_global["hay_fuente_secundaria"] or proc_global["rellenadas"]:
        lineas.append("## Procedencia de los datos")
        lineas.append("")
        lineas.append("**Fuente primaria**: FotMob (nunca se sobrescribe). "
                      "**Fuente secundaria**: Flashscore, solo para rellenar huecos.")
        lineas.append("")
        lineas.append("| Métrica | Procedencia | Partidos |")
        lineas.append("|---|---|---|")
        for clave, datos in sorted(proc_global["rellenadas"].items(),
                                   key=lambda kv: -kv[1]["partidos"]):
            lineas.append(f"| {datos['etiqueta']} | `{datos['procedencia']}` | "
                          f"{datos['partidos']} |")
        lineas.append("")
        lineas.append(f"- Partidos con relleno desde Flashscore: "
                      f"**{proc_global['partidos_con_relleno']}** de "
                      f"{len(auditoria.get('partidos') or [])}")
        lineas.append(f"- Métricas rellenadas: **{len(proc_global['rellenadas'])}**")
        lineas.append(f"- Identidad en Flashscore: {proc_global['identidad']}")
        lineas.append("")
        lineas.append("> Los valores con procedencia `derivado` son cocientes "
                      "aritméticos sobre datos reales de la fuente, no estimaciones. "
                      "Lo que no publica ninguna fuente sigue siendo nulo.")
        lineas.append("")

    for p in auditoria.get("partidos", []):
        marcador = p.get("marcador") or {}
        local_marca = "Local" if p.get("condicion") == "casa" else "Visitante"
        lineas.append("---")
        lineas.append(f"## {p.get('n_partido')}. {p.get('equipo')} {_fmt(marcador.get('equipo'))}"
                      f"-{_fmt(marcador.get('rival'))} {p.get('rival')}")
        lineas.append("")
        lineas.append(f"- **Fecha**: {p.get('fecha')}")
        lineas.append(f"- **Competición**: {p.get('torneo')}")
        lineas.append(f"- **Condición del equipo auditado**: {p.get('condicion')} ({local_marca})")
        lineas.append(f"- **ID de partido**: `{p.get('id_partido')}`")
        if p.get("url"):
            lineas.append(f"- **Fuente**: https://www.fotmob.com{p['url']}"
                          if str(p["url"]).startswith("/") else f"- **Fuente**: {p['url']}")
        fs = p.get("fuente_secundaria") or {}
        if fs.get("pareado"):
            relleno_eq = (fs.get("rellenadas") or {}).get("equipo", [])
            relleno_riv = (fs.get("rellenadas") or {}).get("rival", [])
            n_rell = len(set(relleno_eq + relleno_riv))
            lineas.append(f"- **Fuente secundaria**: Flashscore (`{fs.get('id_flashscore')}`, {n_rell} métricas rellenadas)")
        if p.get("error"):
            lineas.append(f"- ⚠️ **Error**: {p['error']}")
        for nota in p.get("notas", []) or []:
            lineas.append(f"- ℹ️ {nota}")
        lineas.append("")

        periodos_presentes = [per for per in ORDEN_PERIODOS if per in (p.get("periodos") or {})]
        if not periodos_presentes:
            lineas.append("_Sin datos de periodos para este partido._")
            lineas.append("")
            continue

        for categoria in CATEGORIAS_ORDEN:
            filas_cat = []
            for periodo in periodos_presentes:
                mat = (p.get("periodos") or {}).get(periodo) or {}
                filas_cat.append({f["clave"]: f for f in mat.get("filas", [])
                                  if f.get("categoria") == categoria})
            hay_algo = any(f.get("equipo") is not None or f.get("rival") is not None
                           for mapa in filas_cat for f in mapa.values())
            if not hay_algo:
                continue

            lineas.append(f"### {categoria}")
            lineas.append("")
            cabecera = "| Métrica | " + " | ".join(
                f"{per} {p.get('equipo')} | {per} {p.get('rival')}" for per in periodos_presentes) + " |"
            lineas.append(cabecera)
            lineas.append("|---" * (1 + 2 * len(periodos_presentes)) + "|")
            for definicion in CATALOGO_METRICAS:
                if definicion.categoria != categoria:
                    continue
                celdas = []
                for mapa in filas_cat:
                    f = mapa.get(definicion.clave)
                    celdas.append(_fmt(f.get("equipo")) if f else "—")
                    celdas.append(_fmt(f.get("rival")) if f else "—")
                lineas.append(f"| {definicion.etiqueta} | " + " | ".join(celdas) + " |")
            lineas.append("")

    lineas.append("---")
    lineas.append("*Los valores nulos (—) indican que la fuente no publica esa métrica para ese "
                  "partido o periodo. No se han imputado ni estimado valores.*")
    return "\n".join(lineas)


# ═══════════════════════════════════════════════════════════════════════════════
# INFORME DETALLADO DE MATRICES BAJO DEMANDA (/auditar MODALIDADES A Y B)
# ═══════════════════════════════════════════════════════════════════════════════

LAS_45_METRICAS_OFICIALES: tuple[tuple[str, str, str, str], ...] = (
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


def reconstruir_marcador_mitades_partido(
    partido: dict[str, Any],
    lado_local: str,
    lado_visita: str,
) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int], bool, str]:
    """
    Punto 1.1 (Auditoría Integral):
    Reconstruye el marcador oficial de 1T y 2T vía métricas de portería observadas:
      goles_recibidos(lado, p) = round(xgot_enfrentado(lado, p) - goles_evitados(lado, p))
    (goles del Local en p = goles recibidos por Visita; goles de Visita en p = goles recibidos por Local).
    Valida estrictamente 1T + 2T == FT para ambos bandos.
    Devuelve (gl_1t, gv_1t, gl_2t, gv_2t, es_valido, etiqueta).
    """
    marcador = partido.get("marcador") or {}
    ge = marcador.get("equipo") if marcador.get("equipo") is not None else partido.get("goles_equipo")
    gr = marcador.get("rival") if marcador.get("rival") is not None else partido.get("goles_rival")
    if lado_local == "equipo":
        gl_ft = int(ge or 0)
        gv_ft = int(gr or 0)
    else:
        gl_ft = int(gr or 0)
        gv_ft = int(ge or 0)

    per = partido.get("periodos") or {}
    f_1t = {f["clave"]: f for f in (per.get("1T") or {}).get("filas", [])}
    f_2t = {f["clave"]: f for f in (per.get("2T") or {}).get("filas", [])}

    # 1T
    xgot_v_1t = f_1t.get("xgot_enfrentado", {}).get(lado_visita)
    gev_v_1t = f_1t.get("goles_evitados", {}).get(lado_visita)
    xgot_l_1t = f_1t.get("xgot_enfrentado", {}).get(lado_local)
    gev_l_1t = f_1t.get("goles_evitados", {}).get(lado_local)

    # 2T
    xgot_v_2t = f_2t.get("xgot_enfrentado", {}).get(lado_visita)
    gev_v_2t = f_2t.get("goles_evitados", {}).get(lado_visita)
    xgot_l_2t = f_2t.get("xgot_enfrentado", {}).get(lado_local)
    gev_l_2t = f_2t.get("goles_evitados", {}).get(lado_local)

    operands = [xgot_v_1t, gev_v_1t, xgot_l_1t, gev_l_1t, xgot_v_2t, gev_v_2t, xgot_l_2t, gev_l_2t]
    if any(op is None for op in operands):
        return None, None, None, None, False, "sin_dato"

    diff_gl1 = xgot_v_1t - gev_v_1t
    diff_gv1 = xgot_l_1t - gev_l_1t
    diff_gl2 = xgot_v_2t - gev_v_2t
    diff_gv2 = xgot_l_2t - gev_l_2t

    # Tolerancia estricta de residuo: menos de 0.05 del entero más cercano
    if any(abs(d - round(d)) >= 0.05 for d in (diff_gl1, diff_gv1, diff_gl2, diff_gv2)):
        return None, None, None, None, False, "sin_dato"

    gl_1t = int(round(diff_gl1))
    gv_1t = int(round(diff_gv1))
    gl_2t = int(round(diff_gl2))
    gv_2t = int(round(diff_gv2))

    if gl_1t < 0 or gv_1t < 0 or gl_2t < 0 or gv_2t < 0:
        return None, None, None, None, False, "sin_dato"

    if (gl_1t + gl_2t == gl_ft) and (gv_1t + gv_2t == gv_ft):
        return gl_1t, gv_1t, gl_2t, gv_2t, True, "[derivado_aritmético]"
    else:
        return None, None, None, None, False, "sin_dato"


_METRICAS_XG_SIN_SINTETICO = frozenset({
    "xg", "xgot", "xg_juego_abierto", "xg_balon_parado",
    "xg_sin_penalti", "xgot_enfrentado", "xa_equipo", "goles_evitados",
    "saques_puerta", "tiros_libres", "pases_entre_lineas",
})


def _es_procedencia_sintetica(fila: dict[str, Any] | None, procedencia_celda: str = "") -> bool:
    f = fila or {}
    if bool(f.get("estimado")):
        return True
    s = f"{procedencia_celda} {f.get('fuente', '')}".lower()
    return any(tok in s for tok in ("estimacion_modelo", "sintetic", "reconstruid", "estimado"))


def _es_tag_flashscore(tag: str) -> bool:
    s = str(tag or "").strip().lower()
    if not s or _es_procedencia_sintetica(None, s):
        return False
    if "flashscore" in s:
        return True
    return bool(re.search(r"(?:^|[^a-z0-9])fs(?:[^a-z0-9]|$)", s))


def _es_tag_fotmob(tag: str) -> bool:
    s = str(tag or "").strip().lower()
    if not s or _es_procedencia_sintetica(None, s):
        return False
    if "fotmob" in s:
        return True
    return bool(re.search(r"(?:^|[^a-z0-9])fm(?:[^a-z0-9]|$)", s))


def _formatear_valor_metrica_45(
    clave: str,
    valor: Any,
    filas_map: dict[str, dict[str, Any]] | None = None,
    lado: str = "equipo",
    fuente_partido: str = "",
    procedencia_celda: str = "",
) -> str:
    """Formatea limpiamente un valor según la especificación de la métrica y formato compuesto c/t (pct%)."""
    if valor is None:
        return "sin_dato"
    fila_actual = (filas_map or {}).get(clave) if filas_map else None
    if clave in _METRICAS_XG_SIN_SINTETICO and _es_procedencia_sintetica(fila_actual, procedencia_celda):
        return "sin_dato"

    # Detector de métricas no cubiertas según proveedor:
    # Si la fuente es Flashscore o no hay cobertura Opta, los ceros constantes de métricas no reportadas se tratan como sin_dato.
    # Si el proveedor es FotMob y sí reporta la métrica, un 0 individual se preserva si la serie tiene valores positivos.
    es_flashscore = (
        "flashscore" in str(fuente_partido or "").lower()
        or (filas_map and any("flashscore" in str(f.get("fuente", "")).lower() for f in filas_map.values()))
    )
    METRICAS_NO_CUBIERTAS_FLASHSCORE = frozenset({
        "grandes_ocasiones_falladas", "xg_balon_parado",
        "duelos_suelo_pct", "duelos_aereos_pct", "bloqueos", "recuperaciones"
    })
    if (es_flashscore or not fuente_partido) and clave in METRICAS_NO_CUBIERTAS_FLASHSCORE:
        if valor == 0 or valor == 0.0 or str(valor).strip() in ("0", "0.0", "0.00"):
            return "sin_dato"

    # Punto 1.4: xg_juego_abierto y xg_sin_penalti como copias idénticas sin desglose -> sin_dato
    if filas_map and clave in ("xg_juego_abierto", "xg_sin_penalti"):
        xg_ft = filas_map.get("xg", {}).get(lado)
        bp_ft = filas_map.get("xg_balon_parado", {}).get(lado)
        if xg_ft is not None and valor == xg_ft and (bp_ft is None or bp_ft == 0.0 or bp_ft == "sin_dato"):
            return "sin_dato"

    # Si se pasan las filas del periodo, verificar si es métrica compuesta con conteo + porcentaje
    if filas_map:
        if clave == "pases_largos_pct":
            comp = filas_map.get("pases_largos_completados", {}).get(lado)
            tot = filas_map.get("pases_largos", {}).get(lado)
            if comp is not None and tot is not None and tot > 0:
                pct = int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({pct}%)"
        elif clave == "pases_tercio_final_pct":
            comp_f = filas_map.get("pases_tercio_final_completados") or {}
            tot_f = filas_map.get("pases_tercio_final") or {}
            comp = comp_f.get(lado)
            tot = tot_f.get(lado)
            # Evitar falsos "40/100 (40%)" heredados de fuentes sin denominador real
            es_falso_100 = (
                tot is not None and abs(float(tot) - 100.0) < 1e-6
                and comp is not None and valor is not None
                and abs(float(comp) - float(valor)) < 0.5
                and not es_flashscore
            )
            if comp is not None and tot is not None and tot > 0 and not es_falso_100 and not _es_procedencia_sintetica(tot_f):
                pct = int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({pct}%)"
        elif clave == "cruces_pct":
            comp = filas_map.get("centros_completados", {}).get(lado)
            tot = filas_map.get("centros", {}).get(lado)
            if comp is not None and tot is not None and tot > 0:
                pct = int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({pct}%)"
        elif clave == "entradas_pct":
            comp_f = filas_map.get("entradas_ganadas") or {}
            tot_f = filas_map.get("entradas") or {}
            comp = comp_f.get(lado)
            tot = tot_f.get(lado)
            pct_e = (filas_map.get("entradas_ganadas_pct") or {}).get(lado)
            es_falso_100_ent = (
                comp is not None and tot is not None and not es_flashscore
                and abs(float(comp) - float(tot)) < 1e-6 and float(comp) >= 5.0
                and (pct_e is None or abs(float(pct_e) - 100.0) < 1e-6)
            )
            if comp is not None and tot is not None and tot > 0 and not es_falso_100_ent and not _es_procedencia_sintetica(tot_f):
                pct = int(round(pct_e)) if pct_e is not None else int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({pct}%)"
        elif clave == "pases_ratio":
            comp = filas_map.get("pases_completados", {}).get(lado)
            tot = filas_map.get("pases_totales", {}).get(lado) or filas_map.get("pases_total", {}).get(lado)
            pct = filas_map.get("pases_precision_pct", {}).get(lado) or filas_map.get("pases_pct", {}).get(lado)
            if comp is not None and tot is not None and tot > 0:
                if pct is not None and ((float(pct) < 99.5 and abs(float(comp) - float(tot)) < 0.5) or float(comp) > float(tot)):
                    comp = float(round(float(tot) * float(pct) / 100.0))
                p_int = int(round(pct)) if pct is not None else int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({p_int}%)"
        elif clave in ("duelos_suelo_pct", "duelos_aereos_pct"):
            k_comp = "duelos_suelo_ganados" if clave == "duelos_suelo_pct" else "duelos_aereos_ganados"
            k_tot = "duelos_suelo" if clave == "duelos_suelo_pct" else "duelos_aereos"
            comp = filas_map.get(k_comp, {}).get(lado)
            tot = filas_map.get(k_tot, {}).get(lado)
            if comp is not None and tot is not None and tot > 0:
                pct = int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({pct}%)"

    if isinstance(valor, str):
        return valor.strip()
    try:
        vf = float(valor)
    except (ValueError, TypeError):
        return str(valor)

    if clave in ("posesion_pct", "pases_precision_pct"):
        return f"{int(round(vf))}%"

    if clave in (
        "xg", "xgot", "xg_juego_abierto", "xg_balon_parado", "xg_sin_penalti",
        "xa_equipo", "xgot_enfrentado", "goles_evitados",
    ):
        return f"{vf:.2f}"

    if clave in (
        "remates_totales", "remates_puerta", "grandes_ocasiones", "grandes_ocasiones_falladas",
        "corners", "pases_total", "tarjetas_amarillas", "tarjetas_rojas",
        "remates_fuera", "remates_rechazados", "remates_dentro_area", "remates_fuera_area",
        "al_palo", "toques_area_rival", "pases_entre_lineas", "fueras_juego", "tiros_libres",
        "saques_banda", "faltas", "duelos_ganados", "despejes", "intercepciones", "bloqueos",
        "recuperaciones", "errores_remate", "errores_gol", "paradas", "saques_puerta",
    ):
        return str(int(round(vf)))

    if vf.is_integer():
        return str(int(vf))
    return f"{vf:.1f}%" if vf <= 100 else f"{vf:.1f}"


def _construir_tabla_resumen_inicial(partidos: list[dict[str, Any]]) -> list[str]:
    """
    Construye la Tabla Resumen Inicial de todos los partidos del informe.
    """
    lineas: list[str] = [
        "## TABLA RESUMEN DE PARTIDOS AUDITADOS",
        "",
        "| # | Fecha | Competición | Partido (Local vs Visita) | Resultado FT | 1er Tiempo | 2º Tiempo | xG Total (L vs V) | ID FotMob | ID Flashscore | Fuente Usada |",
        "|---|-------|-------------|---------------------------|--------------|------------|-----------|-------------------|-----------|---------------|--------------|",
    ]
    for idx, p in enumerate(partidos, start=1):
        cond = str(p.get("condicion") or "").lower()
        if cond == "casa":
            nom_l = p.get("equipo") or "Local"
            nom_v = p.get("rival") or "Visitante"
            lado_l = "equipo"
            lado_v = "rival"
        else:
            nom_l = p.get("rival") or "Local"
            nom_v = p.get("equipo") or "Visitante"
            lado_l = "rival"
            lado_v = "equipo"

        m = p.get("marcador") or {}
        ge = m.get("equipo") if m.get("equipo") is not None else p.get("goles_equipo") or 0
        gr = m.get("rival") if m.get("rival") is not None else p.get("goles_rival") or 0
        gl = ge if lado_l == "equipo" else gr
        gv = gr if lado_l == "equipo" else ge

        pers = p.get("periodos") or {}
        f_ft = {f["clave"]: f for f in (pers.get("FT") or {}).get("filas", [])}
        f_1t = {f["clave"]: f for f in (pers.get("1T") or {}).get("filas", [])}
        f_2t = {f["clave"]: f for f in (pers.get("2T") or {}).get("filas", [])}

        # Desglose de goles en 1T y 2T vía reconstrucción matemática estricta
        gl_1t_val, gv_1t_val, gl_2t_val, gv_2t_val, mitades_validas, _ = reconstruir_marcador_mitades_partido(p, lado_l, lado_v)
        if mitades_validas:
            str_1t = f"{gl_1t_val} - {gv_1t_val}"
            str_2t = f"{gl_2t_val} - {gv_2t_val}"
        else:
            str_1t = "sin_dato"
            str_2t = "sin_dato"

        proc_ft = (p.get("procedencia") or {}).get("FT") or {}
        fila_xg_ft = f_ft.get("xg") or {}
        xg_l = fila_xg_ft.get(lado_l)
        xg_v = fila_xg_ft.get(lado_v)
        proc_xg_l = str((proc_ft.get(lado_l) or {}).get("xg") or "") if isinstance(proc_ft, dict) else ""
        proc_xg_v = str((proc_ft.get(lado_v) or {}).get("xg") or "") if isinstance(proc_ft, dict) else ""
        s_xg_l = "sin_dato" if (xg_l is None or _es_procedencia_sintetica(fila_xg_ft, proc_xg_l)) else f"{float(xg_l):.2f}"
        s_xg_v = "sin_dato" if (xg_v is None or _es_procedencia_sintetica(fila_xg_ft, proc_xg_v)) else f"{float(xg_v):.2f}"
        str_xg = f"{s_xg_l} vs {s_xg_v}"

        fecha = str(p.get("fecha") or "N/D")[:10]
        torneo = str(p.get("torneo") or "Oficial")
        id_fm = str(p.get("id_partido") or p.get("id") or "N/D")
        fs = p.get("fuente_secundaria") or {}
        id_fs = str(fs.get("id_flashscore") or "N/D")

        es_fm = (id_fm != "N/D" and bool(id_fm))
        es_fs = (id_fs != "N/D" and bool(id_fs) and (fs.get("pareado") is not False))

        celdas_fs = 0
        celdas_fm = 0
        for per_k in ("FT", "1T", "2T"):
            p_dict = (p.get("procedencia") or {}).get(per_k) or {}
            for lado_k in ("equipo", "rival"):
                d_lado = p_dict.get(lado_k) if isinstance(p_dict, dict) else {}
                if isinstance(d_lado, dict):
                    for v_src in d_lado.values():
                        if es_fs and _es_tag_flashscore(str(v_src)):
                            celdas_fs += 1
                        elif _es_tag_fotmob(str(v_src)):
                            celdas_fm += 1

        if celdas_fs > 0 and celdas_fm > 0:
            fuente_txt = "FM+FS"
        elif celdas_fs > 0 and celdas_fm == 0:
            fuente_txt = "Flashscore"
        elif celdas_fm > 0 and celdas_fs == 0:
            fuente_txt = "FotMob (100% Opta)"
        elif es_fm and not es_fs:
            fuente_txt = "FotMob (100% Opta)"
        elif es_fs and not es_fm:
            fuente_txt = "Flashscore"
        elif es_fm:
            fuente_txt = "FotMob (100% Opta)"
        elif es_fs:
            fuente_txt = "Flashscore"
        else:
            fuente_txt = "N/D"

        lineas.append(
            f"| {idx:02d} | {fecha} | {torneo} | {nom_l} vs {nom_v} | "
            f"{gl} - {gv} | {str_1t} | {str_2t} | "
            f"{str_xg} | {id_fm} | {id_fs} | {fuente_txt} |"
        )
    lineas.append("")
    return lineas


def formatear_matriz_45_partido(
    partido: dict[str, Any],
    k: int = 1,
    total_partidos: int = 1,
    nombre_torneo_defecto: str = "",
) -> str:
    """
    Formatea una única matriz de partido con las 45 métricas oficiales en FT, 1T y 2T
    dividida en 6 sub-tablas Markdown (Principales, Disparos, Ataques, Pases, Defensa, Portería)
    con columnas explícitas para Local y Visita.
    """
    condicion = str(partido.get("condicion") or "").lower()
    if condicion == "casa":
        lado_local = "equipo"
        lado_visita = "rival"
        nom_local = partido.get("equipo") or "Local"
        nom_visita = partido.get("rival") or "Visitante"
    else:
        lado_local = "rival"
        lado_visita = "equipo"
        nom_local = partido.get("rival") or "Local"
        nom_visita = partido.get("equipo") or "Visitante"

    marcador = partido.get("marcador") or {}
    goles_eq = marcador.get("equipo") if marcador.get("equipo") is not None else partido.get("goles_equipo")
    goles_riv = marcador.get("rival") if marcador.get("rival") is not None else partido.get("goles_rival")
    if lado_local == "equipo":
        gl = int(goles_eq or 0)
        gv = int(goles_riv or 0)
    else:
        gl = int(goles_riv or 0)
        gv = int(goles_eq or 0)

    periodos = partido.get("periodos") or {}
    f_ft = {f["clave"]: f for f in (periodos.get("FT") or {}).get("filas", [])}
    f_1t = {f["clave"]: f for f in (periodos.get("1T") or {}).get("filas", [])}
    f_2t = {f["clave"]: f for f in (periodos.get("2T") or {}).get("filas", [])}

    # Desglose de goles en 1T y 2T vía reconstrucción matemática estricta
    gl_1t, gv_1t, gl_2t, gv_2t, mitades_validas, tag_mitades = reconstruir_marcador_mitades_partido(partido, lado_local, lado_visita)
    if mitades_validas:
        mitades_str = f"1T: {gl_1t}-{gv_1t} | 2T: {gl_2t}-{gv_2t} {tag_mitades}"
    else:
        mitades_str = "1T: sin_dato | 2T: sin_dato"

    torneo = str(partido.get("torneo") or nombre_torneo_defecto or "Oficial")
    fecha = str(partido.get("fecha") or "N/D")[:10]
    id_fm = str(partido.get("id_partido") or partido.get("id") or "N/D")
    fs = partido.get("fuente_secundaria") or {}
    id_fs = str(fs.get("id_flashscore") or "N/D")

    proc_ft = (partido.get("procedencia") or {}).get("FT") or {}
    proc_1t = (partido.get("procedencia") or {}).get("1T") or {}
    proc_2t = (partido.get("procedencia") or {}).get("2T") or {}

    lineas: list[str] = [
        "=" * 84,
        f"MATRIZ DE PARTIDO [{k}/{total_partidos}] · {torneo} · Fecha: {fecha}",
        f"{nom_local} (Local) {gl} - {gv} {nom_visita} (Visita) | {mitades_str}",
        f"IDs Verificables -> FotMob: {id_fm} | Flashscore: {id_fs}",
        "=" * 84,
    ]

    CATEGORIAS_SUBTABLAS = [
        ("PRINCIPALES", "1. Estadísticas Principales (01–12)"),
        ("DISPAROS", "2. Disparos (13–21)"),
        ("ATAQUES", "3. Ataques (22–25)"),
        ("PASES", "4. Pases (26–30)"),
        ("DEFENSA", "5. Defensa (31–41)"),
        ("PORTERÍA", "6. Portería / Atajadas (42–45)"),
    ]

    for cat_id, cat_titulo in CATEGORIAS_SUBTABLAS:
        lineas.append("")
        lineas.append(f"### {cat_titulo} — Matriz [{k}/{total_partidos}] ({fecha})")
        lineas.append("")
        lineas.append("| # | Métrica Oficial (`Clave`) | FT: Local (L) | FT: Visita (V) | 1T: Local (L) | 1T: Visita (V) | 2T: Local (L) | 2T: Visita (V) | Fuente |")
        lineas.append("|---|---------------------------|---------------|----------------|---------------|----------------|---------------|----------------|--------|")

        metricas_cat = [m for m in LAS_45_METRICAS_OFICIALES if m[3] == cat_id]
        fuente_partido_base = str(partido.get("fuente") or ("fotmob" if (id_fm != "N/D" and id_fm) else "flashscore"))
        fs_pareado_valido = (id_fs != "N/D" and bool(id_fs) and (fs.get("pareado") is not False))

        for num, clave, etiqueta, _cat in metricas_cat:
            p_l_ft = str((proc_ft.get(lado_local) or {}).get(clave) or "")
            p_v_ft = str((proc_ft.get(lado_visita) or {}).get(clave) or "")
            p_l_1t = str((proc_1t.get(lado_local) or {}).get(clave) or "")
            p_v_1t = str((proc_1t.get(lado_visita) or {}).get(clave) or "")
            p_l_2t = str((proc_2t.get(lado_local) or {}).get(clave) or "")
            p_v_2t = str((proc_2t.get(lado_visita) or {}).get(clave) or "")

            # FT
            fila_ft = f_ft.get(clave) or {}
            val_l_ft = fila_ft.get(lado_local)
            val_v_ft = fila_ft.get(lado_visita)
            str_l_ft = _formatear_valor_metrica_45(clave, val_l_ft, f_ft, lado_local, fuente_partido=p_l_ft or fuente_partido_base, procedencia_celda=p_l_ft)
            str_v_ft = _formatear_valor_metrica_45(clave, val_v_ft, f_ft, lado_visita, fuente_partido=p_v_ft or fuente_partido_base, procedencia_celda=p_v_ft)

            # 1T
            fila_1t = f_1t.get(clave) or {}
            val_l_1t = fila_1t.get(lado_local)
            val_v_1t = fila_1t.get(lado_visita)
            str_l_1t = _formatear_valor_metrica_45(clave, val_l_1t, f_1t, lado_local, fuente_partido=p_l_1t or fuente_partido_base, procedencia_celda=p_l_1t)
            str_v_1t = _formatear_valor_metrica_45(clave, val_v_1t, f_1t, lado_visita, fuente_partido=p_v_1t or fuente_partido_base, procedencia_celda=p_v_1t)

            # 2T
            fila_2t = f_2t.get(clave) or {}
            val_l_2t = fila_2t.get(lado_local)
            val_v_2t = fila_2t.get(lado_visita)
            str_l_2t = _formatear_valor_metrica_45(clave, val_l_2t, f_2t, lado_local, fuente_partido=p_l_2t or fuente_partido_base, procedencia_celda=p_l_2t)
            str_v_2t = _formatear_valor_metrica_45(clave, val_v_2t, f_2t, lado_visita, fuente_partido=p_v_2t or fuente_partido_base, procedencia_celda=p_v_2t)

            # Fuente real de la fila
            tags_fila = [p_l_ft, p_v_ft, p_l_1t, p_v_1t, p_l_2t, p_v_2t]
            src_str_row = " ".join(t for t in tags_fila if t).lower()
            row_has_fs = fs_pareado_valido and any(_es_tag_flashscore(t) for t in tags_fila)
            row_has_fm = any(_es_tag_fotmob(t) for t in tags_fila)
            row_solo_est = bool(tags_fila and any(_es_procedencia_sintetica(fila_ft, t) for t in tags_fila)) and not row_has_fm and not row_has_fs

            todos_sin_dato = all(s == "sin_dato" for s in [str_l_ft, str_v_ft, str_l_1t, str_v_1t, str_l_2t, str_v_2t])
            if todos_sin_dato or row_solo_est:
                fuente_txt = "sin_dato"
            elif id_fm == "N/D" or not id_fm:
                fuente_txt = "Flashscore" if (id_fs != "N/D" and bool(id_fs)) else "sin_dato"
            elif row_has_fs and row_has_fm:
                fuente_txt = "FM+FS"
            elif row_has_fs and not row_has_fm:
                fuente_txt = "Flashscore"
            elif row_has_fm and not row_has_fs:
                fuente_txt = "FotMob"
            elif not fs_pareado_valido:
                fuente_txt = "FotMob"
            else:
                fuente_txt = "FotMob"

            lineas.append(
                f"| {num} | {etiqueta} (`{clave}`) | {str_l_ft} | {str_v_ft} | {str_l_1t} | {str_v_1t} | {str_l_2t} | {str_v_2t} | {fuente_txt} |"
            )

    return "\n".join(lineas)


MAX_BYTES_MARKDOWN_TELEGRAM = 30000
_REGISTRO_PARTES_MD: dict[str, list[dict[str, Any]]] = {}


def _dividir_bloque_gigante_por_categorias(bloque_matriz: str, max_bytes: int = 26000) -> list[str]:
    """Si una matriz individual supera el presupuesto, la divide entre categorías 1..3 y 4..6."""
    if len(bloque_matriz.encode("utf-8")) <= max_bytes:
        return [bloque_matriz]
    marcador_cat4 = "### 4. Pases (26–30)"
    if marcador_cat4 in bloque_matriz:
        idx = bloque_matriz.index(marcador_cat4)
        lineas_enc = bloque_matriz[:idx].splitlines()[:5]
        enc_rep = "\n".join(lineas_enc) + "\n\n"
        p1 = bloque_matriz[:idx].rstrip()
        p2 = enc_rep + bloque_matriz[idx:]
        return [p1, p2]
    lineas = bloque_matriz.splitlines()
    mitad = max(1, len(lineas) // 2)
    return ["\n".join(lineas[:mitad]), "\n".join(lineas[mitad:])]


def particionar_bloques_markdown_30kb(
    titulo_base: str,
    metadatos_lineas: list[str],
    tabla_resumen_lineas: list[str],
    secciones_bloques: list[tuple[str, list[str]]],
    ruta_md_base: str,
    max_bytes: int = MAX_BYTES_MARKDOWN_TELEGRAM,
) -> list[dict[str, Any]]:
    """
    Sección 10: Particiona dinámicamente cualquier informe Markdown en archivos de disco
    cuyo tamaño real en bytes UTF-8 sea estrictamente `< 30,000 bytes` (`os.path.getsize(r) < 30000`),
    sin cortar ninguna tabla a mitad de bloque.
    """
    items_planos: list[tuple[str, str]] = []
    for subtitulo_sec, lista_matrices in secciones_bloques:
        for idx_m, mat_str in enumerate(lista_matrices):
            pref = f"{subtitulo_sec}\n\n" if idx_m == 0 and subtitulo_sec else ""
            for sub_bloque in _dividir_bloque_gigante_por_categorias(pref + mat_str, max_bytes=max_bytes - 4500):
                items_planos.append((subtitulo_sec, sub_bloque))

    if not items_planos:
        items_planos = [("", "_Sin partidos registrados en este bloque._")]

    resumen_str = "\n".join(tabla_resumen_lineas) + "\n---\n\n" if tabla_resumen_lineas else ""
    meta_str = "\n".join(metadatos_lineas) + "\n\n---\n\n"

    # Empaquetar secuencialmente garantizando margen para el encabezado de Parte k de M
    presupuesto_cuerpo = max_bytes - 1200
    grupos_cuerpo: list[str] = []
    actual_lineas: list[str] = []

    for idx_it, (_sec, bloque_txt) in enumerate(items_planos):
        incluir_resumen = (len(grupos_cuerpo) == 0 and not actual_lineas)
        pref_candidato = (meta_str + (resumen_str if incluir_resumen else "")) if not actual_lineas else ""
        if incluir_resumen and len((pref_candidato + bloque_txt).encode("utf-8")) > presupuesto_cuerpo:
            # Si la tabla resumen + primera matriz supera el límite, poner la tabla resumen en la Parte 1 sola o reducir prefijo
            if len((meta_str + resumen_str).encode("utf-8")) <= presupuesto_cuerpo:
                grupos_cuerpo.append(meta_str + resumen_str.rstrip())
                pref_candidato = meta_str
            else:
                pref_candidato = meta_str

        candidato_str = ("\n\n".join(actual_lineas) + "\n\n" + bloque_txt) if actual_lineas else (pref_candidato + bloque_txt)
        if len(candidato_str.encode("utf-8")) <= presupuesto_cuerpo or not actual_lineas:
            if not actual_lineas:
                actual_lineas.append(pref_candidato + bloque_txt)
            else:
                actual_lineas.append(bloque_txt)
        else:
            grupos_cuerpo.append("\n\n".join(actual_lineas))
            actual_lineas = [meta_str + bloque_txt]

    if actual_lineas:
        grupos_cuerpo.append("\n\n".join(actual_lineas))

    total_partes = len(grupos_cuerpo)
    os.makedirs(os.path.dirname(os.path.abspath(ruta_md_base)), exist_ok=True)
    partes_info: list[dict[str, Any]] = []
    raiz, ext = os.path.splitext(ruta_md_base)
    if not ext:
        ext = ".md"

    for k, cuerpo in enumerate(grupos_cuerpo, start=1):
        sufijo_titulo = f" (Parte {k} de {total_partes})" if total_partes > 1 else ""
        encabezado = f"# {titulo_base}{sufijo_titulo}\n\n"
        texto_final = encabezado + cuerpo.strip() + "\n"
        # Salvaguarda final estricta < max_bytes
        raw_bytes = texto_final.encode("utf-8")
        if len(raw_bytes) >= max_bytes:
            raw_bytes = raw_bytes[: max_bytes - 64]
            texto_final = raw_bytes.decode("utf-8", errors="ignore").rsplit("\n", 1)[0] + "\n"

        ruta_k = ruta_md_base if k == 1 else f"{raiz}_parte{k}de{total_partes}{ext}"
        with open(ruta_k, "w", encoding="utf-8", newline="\n") as f:
            f.write(texto_final)
        # Si k == 1 y hay más de 1 parte, guardar también copia con nombre explícito _parte1deM
        if total_partes > 1 and k == 1:
            ruta_p1_expl = f"{raiz}_parte1de{total_partes}{ext}"
            with open(ruta_p1_expl, "w", encoding="utf-8", newline="\n") as f1:
                f1.write(texto_final)

        tam_disco = os.path.getsize(ruta_k)
        partes_info.append({
            "parte": k,
            "total_partes": total_partes,
            "ruta": ruta_k,
            "bytes_utf8": len(texto_final.encode("utf-8")),
            "bytes_disco": tam_disco,
            "contenido": texto_final,
        })

    _REGISTRO_PARTES_MD[os.path.abspath(ruta_md_base)] = partes_info
    _REGISTRO_PARTES_MD[ruta_md_base] = partes_info
    return partes_info


def obtener_partes_informe_md(ruta_md: str) -> list[dict[str, Any]]:
    """Devuelve la lista ordenada de partes `< 30,000 bytes` asociadas a `ruta_md`."""
    if not ruta_md:
        return []
    partes = _REGISTRO_PARTES_MD.get(os.path.abspath(ruta_md)) or _REGISTRO_PARTES_MD.get(ruta_md)
    if partes:
        return partes
    if os.path.exists(ruta_md):
        with open(ruta_md, "r", encoding="utf-8") as f:
            cont = f.read()
        return [{
            "parte": 1,
            "total_partes": 1,
            "ruta": ruta_md,
            "bytes_utf8": len(cont.encode("utf-8")),
            "bytes_disco": os.path.getsize(ruta_md),
            "contenido": cont,
        }]
    return []


def generar_informe_matrices_30(
    doc_local: dict[str, Any],
    doc_visita: dict[str, Any],
    ruta_md: str | None = None,
    fecha_corte: Any = None,
    id_partido_excluir: Any = None,
    cutoff_timestamp: Any = None,
    exclude_match_ids: Any = None,
) -> tuple[str, str]:
    """
    Genera el informe Markdown completo para Modalidad A (/auditar Local vs Visita):
    N_L partidos oficiales de Local + N_V partidos oficiales de Visita.
    Todos los archivos escritos en disco cumplen estrictamente `< 30,000 bytes` UTF-8.
    Devuelve `(contenido_primera_parte, ruta_archivo_base)`.
    """
    nom_local = (doc_local.get("equipo") or {}).get("nombre") or doc_local.get("equipo_solicitado") or "Local"
    nom_visita = (doc_visita.get("equipo") or {}).get("nombre") or doc_visita.get("equipo_solicitado") or "Visitante"
    ahora = _ahora_lima()
    fecha_hoy = ahora.strftime("%Y-%m-%d")

    pl_brutos, _ = _filtrar_partidos_doc_cutoff(
        [p for p in (doc_local.get("partidos") or []) if not p.get("es_amistoso")],
        fecha_corte=fecha_corte, id_partido_excluir=id_partido_excluir,
        cutoff_timestamp=cutoff_timestamp, exclude_match_ids=exclude_match_ids,
    )
    pv_brutos, _ = _filtrar_partidos_doc_cutoff(
        [p for p in (doc_visita.get("partidos") or []) if not p.get("es_amistoso")],
        fecha_corte=fecha_corte, id_partido_excluir=id_partido_excluir,
        cutoff_timestamp=cutoff_timestamp, exclude_match_ids=exclude_match_ids,
    )
    partidos_local = pl_brutos[:15]
    partidos_visita = pv_brutos[:15]
    n_loc = len(partidos_local)
    n_vis = len(partidos_visita)
    total_entregados = n_loc + n_vis

    if not ruta_md:
        base_dir = os.path.join(AUDITORIA_DIR, fecha_hoy)
        os.makedirs(base_dir, exist_ok=True)
        ruta_md = os.path.join(
            base_dir,
            f"auditoria_matrices_{_slug(nom_local)}_vs_{_slug(nom_visita)}_{fecha_hoy}.md",
        )

    titulo = f"AUDITORÍA DE {total_entregados} MATRICES OFICIALES (MODALIDAD A) — {nom_local} vs {nom_visita}"
    meta_lineas = [
        f"- **Fecha de Generación**: {ahora.strftime('%Y-%m-%d %H:%M:%S')} (America/Lima)",
        f"- **Equipo Local**: {nom_local} ({n_loc} partido(s) 100% oficiales auditados)",
        f"- **Equipo Visitante**: {nom_visita} ({n_vis} partido(s) 100% oficiales auditados)",
        f"- **Filtro Anti-Amistosos**: Estricto (cero amistosos en el cómputo; descarte y backfill hacia atrás)",
        f"- **Total de Matrices**: {total_entregados} matrices individuales ({n_loc} de {nom_local} + {n_vis} de {nom_visita})",
        f"- **Desglose Multitiempo**: General (FT), Primer Tiempo (1T) y Segundo Tiempo (2T)",
        f"- **Catálogo Oficial**: 45 métricas del 01 al 45 con distinción entre 0 observado y sin_dato",
    ]
    resumen_lineas = _construir_tabla_resumen_inicial(partidos_local + partidos_visita)
    mats_local = [
        formatear_matriz_45_partido(p, k=idx, total_partidos=total_entregados)
        for idx, p in enumerate(partidos_local, start=1)
    ]
    mats_visita = [
        formatear_matriz_45_partido(p, k=idx, total_partidos=total_entregados)
        for idx, p in enumerate(partidos_visita, start=n_loc + 1)
    ]
    secciones = [
        (f"## BLOQUE 1: {n_loc} PARTIDO(S) OFICIALES DE {nom_local.upper()} (LOCAL)", mats_local),
        (f"## BLOQUE 2: {n_vis} PARTIDO(S) OFICIALES DE {nom_visita.upper()} (VISITANTE)", mats_visita),
    ]
    partes = particionar_bloques_markdown_30kb(
        titulo_base=titulo,
        metadatos_lineas=meta_lineas,
        tabla_resumen_lineas=resumen_lineas,
        secciones_bloques=secciones,
        ruta_md_base=ruta_md,
        max_bytes=MAX_BYTES_MARKDOWN_TELEGRAM,
    )
    try:
        from banco_historico import guardar_en_banco_no_bloqueante
        guardar_en_banco_no_bloqueante(doc_local=doc_local, doc_visitante=doc_visita)
    except Exception as _e_banco:
        print(f"[BANCO HISTORICO] ⚠️ Hook matrices_30 omitido: {_e_banco}")
    return partes[0]["contenido"], ruta_md


def generar_informe_matrices_h2h(
    doc_h2h_a: dict[str, Any],
    doc_h2h_b: dict[str, Any],
    lista_partidos: list[dict[str, Any]],
    ruta_md: str | None = None,
) -> tuple[str, str]:
    """
    Genera el informe Markdown de N matrices para Modalidad B (/auditar Local h2h Visita).
    Todos los archivos escritos en disco cumplen estrictamente `< 30,000 bytes` UTF-8.
    """
    nom_a = (doc_h2h_a.get("equipo") or {}).get("nombre") or doc_h2h_a.get("equipo_solicitado") or "Equipo A"
    nom_b = (doc_h2h_b.get("equipo") or {}).get("nombre") or doc_h2h_b.get("equipo_solicitado") or "Equipo B"
    ahora = _ahora_lima()
    fecha_hoy = ahora.strftime("%Y-%m-%d")

    n = len(lista_partidos)
    if not ruta_md:
        base_dir = os.path.join(config.INFORME_DIR, "h2h")
        os.makedirs(base_dir, exist_ok=True)
        ruta_md = os.path.join(
            base_dir,
            f"auditoria_h2h_matrices_{_slug(nom_a)}_{_slug(nom_b)}_{fecha_hoy}.md",
        )

    titulo = f"AUDITORÍA DE ENFRENTAMIENTOS DIRECTOS H2H (MODALIDAD B) — {nom_a} h2h {nom_b}"
    meta_lineas = [
        f"- **Fecha de Generación**: {ahora.strftime('%Y-%m-%d %H:%M:%S')} (America/Lima)",
        f"- **Ventana Temporal Estricta**: 2024-01-01 a 2026-12-31",
        f"- **Duelos Directos Reales Encontrados**: {n} partido(s)",
        f"- **Prohibición de Invención**: Cero partidos fabricados, sintéticos o sustituidos con otros rivales",
        f"- **Total de Matrices**: {n} matrices individuales completas (6 sub-tablas por matriz)",
        f"- **Desglose Multitiempo**: General (FT), Primer Tiempo (1T) y Segundo Tiempo (2T)",
    ]
    resumen_lineas = _construir_tabla_resumen_inicial(lista_partidos)
    mats = [
        formatear_matriz_45_partido(p, k=idx, total_partidos=n)
        for idx, p in enumerate(lista_partidos, start=1)
    ]
    partes = particionar_bloques_markdown_30kb(
        titulo_base=titulo,
        metadatos_lineas=meta_lineas,
        tabla_resumen_lineas=resumen_lineas,
        secciones_bloques=[("", mats)],
        ruta_md_base=ruta_md,
        max_bytes=MAX_BYTES_MARKDOWN_TELEGRAM,
    )
    try:
        from banco_historico import guardar_en_banco_no_bloqueante
        guardar_en_banco_no_bloqueante(doc_local=doc_h2h_a, doc_visitante=doc_h2h_b)
    except Exception as _e_banco:
        print(f"[BANCO HISTORICO] ⚠️ Hook matrices_h2h omitido: {_e_banco}")
    return partes[0]["contenido"], ruta_md


def generar_informe_matrices_equipo(
    doc_equipo: dict[str, Any],
    ruta_md: str | None = None,
) -> tuple[str, str]:
    """
    Genera el informe Markdown de N matrices para una auditoría individual de equipo,
    particionando automáticamente en archivos `< 30,000 bytes` en disco.
    """
    nom_eq = (doc_equipo.get("equipo") or {}).get("nombre") or doc_equipo.get("equipo_solicitado") or "Equipo"
    ahora = _ahora_lima()
    fecha_hoy = ahora.strftime("%Y-%m-%d")

    partidos = [p for p in (doc_equipo.get("partidos") or []) if not p.get("es_amistoso")][:15]
    n = len(partidos)

    if not ruta_md:
        base_dir = os.path.join(AUDITORIA_DIR, fecha_hoy)
        os.makedirs(base_dir, exist_ok=True)
        ruta_md = os.path.join(
            base_dir,
            f"auditoria_matrices_{n}_{_slug(nom_eq)}_{fecha_hoy}.md",
        )

    titulo = f"AUDITORÍA DE {n} MATRICES OFICIALES — {nom_eq}"
    meta_lineas = [
        f"- **Fecha de Generación**: {ahora.strftime('%Y-%m-%d %H:%M:%S')} (America/Lima)",
        f"- **Equipo Auditado**: {nom_eq}",
        f"- **Partidos 100% Oficiales**: {n} partido(s) con datos (amistosos descartados por completo)",
        f"- **Total de Matrices**: {n} matrices completas individuales (6 sub-tablas por matriz)",
        f"- **Desglose Multitiempo**: General (FT), Primer Tiempo (1T) y Segundo Tiempo (2T)",
    ]
    resumen_lineas = _construir_tabla_resumen_inicial(partidos)
    mats = [
        formatear_matriz_45_partido(p, k=idx, total_partidos=n)
        for idx, p in enumerate(partidos, start=1)
    ]
    partes = particionar_bloques_markdown_30kb(
        titulo_base=titulo,
        metadatos_lineas=meta_lineas,
        tabla_resumen_lineas=resumen_lineas,
        secciones_bloques=[("", mats)],
        ruta_md_base=ruta_md,
        max_bytes=MAX_BYTES_MARKDOWN_TELEGRAM,
    )
    try:
        from banco_historico import guardar_en_banco_no_bloqueante
        guardar_en_banco_no_bloqueante(doc_local=doc_equipo)
    except Exception as _e_banco:
        print(f"[BANCO HISTORICO] ⚠️ Hook matrices_equipo omitido: {_e_banco}")
    return partes[0]["contenido"], ruta_md



# ═══════════════════════════════════════════════════════════════════════════════
# ENTRADA POR LÍNEA DE COMANDOS
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Auditoría Histórica Profunda de un equipo")
    parser.add_argument("equipo", help="Nombre del equipo (ej. \"Barcelona\")")
    parser.add_argument("--limite", type=int, default=LIMITE_PARTIDOS_POR_DEFECTO,
                        help=f"Máximo de partidos oficiales a auditar "
                             f"(por defecto {LIMITE_PARTIDOS_POR_DEFECTO})")
    parser.add_argument("--minimo-con-datos", type=int, default=None,
                        help="Partidos oficiales CON DATOS a alcanzar; si un partido viene vacío "
                             "el escaneo sigue hacia atrás (por defecto, igual a --limite)")
    parser.add_argument("--max-paginas", type=int, default=8,
                        help="Tope de páginas de calendario a escanear hacia atrás (por defecto 8)")
    parser.add_argument("--fuente", default=None, choices=["fotmob", "sofascore"],
                        help="Fuente de datos (por defecto fotmob)")
    parser.add_argument("--visible", action="store_true",
                        help="Muestra el navegador en pantalla (depuración)")
    parser.add_argument("--incluir-amistosos", action="store_true",
                        help="Rellena con amistosos SOLO si los partidos oficiales no alcanzan "
                             "el objetivo (quedan marcados como amistosos en el dataset)")
    parser.add_argument("--sobrescribir-base", action="store_true",
                        help="Escribe con el nombre base (auditoria_<equipo>_<fecha>) en vez de "
                             "con marca de tiempo. PISA la auditoría base de ese equipo y día.")
    args = parser.parse_args()

    try:
        auditoria = auditar_equipo(args.equipo, limite=args.limite, fuente=args.fuente,
                                   headless=not args.visible,
                                   incluir_amistosos=args.incluir_amistosos,
                                   minimo_con_datos=args.minimo_con_datos)
    except Exception as e:
        print(f"\n❌ La auditoría falló: {type(e).__name__}: {e}")
        return 1

    rutas = exportar_auditoria(auditoria, sufijo_tiempo="" if not args.sobrescribir_base else None)
    resumen = auditoria.get("resumen", {})
    escaneo = auditoria.get("escaneo", {})
    print("\n" + "=" * 70)
    print(f"  AUDITORÍA DE {auditoria['equipo']['nombre']} COMPLETADA")
    print(f"  Objetivo:            {resumen.get('minimo_con_datos')} partidos con datos "
          f"({'ALCANZADO' if resumen.get('objetivo_alcanzado') else 'NO ALCANZADO'})")
    print(f"  Partidos auditados:  {resumen.get('partidos_con_datos')} con datos de "
          f"{resumen.get('partidos_auditados')}")
    print(f"  Desglose:            {escaneo.get('oficiales_encontrados', 0)} oficiales disponibles · "
          f"{escaneo.get('amistosos_excluidos', 0)} amistosos detectados · "
          f"{escaneo.get('amistosos_usados_como_relleno', 0)} usados como relleno")
    if escaneo.get("partidos_sin_datos"):
        print(f"  Compensación:        {escaneo.get('partidos_sin_datos')} partido(s) sin datos, "
              f"se siguió hacia atrás")
    if resumen.get("objetivo_alcanzado") is False:
        print(f"  ⚠️  Motivo:            {escaneo.get('motivo_parada')}")
    print(f"  Métricas con datos:  {resumen.get('metricas_con_datos')}/{resumen.get('metricas_solicitadas')}")
    if resumen.get("metricas_sin_datos_en_ningun_partido"):
        print(f"  No publicadas por la fuente: "
              f"{', '.join(resumen['metricas_sin_datos_en_ningun_partido'])}")
    for aviso in auditoria.get("advertencias", []):
        print(f"  ⚠️  {aviso}")
    print("  Archivos generados:")
    for tipo, ruta in rutas.items():
        print(f"    - {tipo:10} {ruta}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
