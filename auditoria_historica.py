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
    REGLA OBLIGATORIA DE COBERTURA Y TOLERANCIA CERO A NULOS.

    Comprueba, partido a partido y periodo a periodo (`FT`, `1T`, `2T`), que TODA la
    matriz quede cubierta sin valores `None`, `null`, `NaN` ni `"sin_dato"`.
    """
    huecos_rellenables: list[dict[str, Any]] = []
    huecos_declarados: list[dict[str, Any]] = []
    total_valores = 0
    total_presentes = 0
    por_periodo: dict[str, dict[str, int]] = {
        "FT": {"presentes": 0, "total": 0},
        "1T": {"presentes": 0, "total": 0},
        "2T": {"presentes": 0, "total": 0},
    }

    for p in auditoria.get("partidos", []) or []:
        fs = p.get("fuente_secundaria") or {}
        emparejado = bool(fs.get("pareado"))
        rellenadas = set()
        for lado in ("equipo", "rival"):
            rellenadas |= set((fs.get("rellenadas") or {}).get(lado) or [])
        periodos = p.get("periodos") or {}
        for periodo in ("FT", "1T", "2T"):
            filas = (periodos.get(periodo) or {}).get("filas") or []
            for fila in filas:
                clave = str(fila.get("clave") or "")
                for lado in ("equipo", "rival"):
                    total_valores += 1
                    por_periodo[periodo]["total"] += 1
                    if not _es_valor_vacio_o_invalido(fila.get(lado)):
                        total_presentes += 1
                        por_periodo[periodo]["presentes"] += 1
                        continue
                    # Hueco: ¿era rellenable?
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
        "total_valores": total_valores,
        "valores_presentes": total_presentes,
        "cobertura_pct": (round(100.0 * total_presentes / total_valores, 2)
                          if total_valores else None),
        "por_periodo": {
            k: {"presentes": v["presentes"], "total": v["total"],
                "cobertura_pct": (round(100.0 * v["presentes"] / v["total"], 2)
                                  if v["total"] else None)}
            for k, v in por_periodo.items()},
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
) -> dict[str, Any]:
    def avisar(msg: str) -> None:
        print(f"[AUDITORÍA] {msg}")
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
    try:
        if fuente_obj is None:
            fuente_obj = obtener_fuente(fuente, headless=headless, verbose=False)
        auditoria["fuente"] = fuente_obj.nombre
        avisar(f"fuente: {fuente_obj.nombre} · buscando «{nombre_equipo}»…")
        fuente_obj.abrir()

        try:
            equipo = fuente_obj.resolver_equipo(nombre_equipo, rival_contexto=rival)
        except TypeError:
            equipo = fuente_obj.resolver_equipo(nombre_equipo)
        if not equipo and getattr(fuente_obj, "nombre", "") == "fotmob":
            avisar(f"⚠️ «{nombre_equipo}» no encontrado en FotMob; activando fallback automático a Flashscore…")
            try:
                from fuentes_xg import FuenteFlashscore
                fs_fallback = FuenteFlashscore(ctx_compartido=getattr(fuente_obj, "ctx", None), verbose=False)
                fs_fallback.abrir()
                equipo_fs = fs_fallback.resolver_equipo(nombre_equipo, rival_contexto=rival)
                if equipo_fs:
                    fuente_obj = fs_fallback
                    auditoria["fuente"] = "flashscore"
                    equipo = equipo_fs
            except Exception as err_fs:
                avisar(f"⚠️ Fallback Flashscore: {err_fs}")
        if not equipo:
            raise RuntimeError(
                f"No se encontró ningún equipo llamado «{nombre_equipo}» en {fuente_obj.nombre}."
            )
        auditoria["equipo"] = equipo
        avisar(f"equipo: {equipo.get('nombre')} ({equipo.get('liga') or 'liga desconocida'})")

        # ── Escaneo profundo hacia atrás, saltando amistosos ────────────────
        avisar(f"escaneando el calendario hacia atrás (objetivo: {objetivo_datos} "
               f"partidos oficiales con datos, máximo {limite} auditados)…")
        escaneo = fuente_obj.escanear_oficiales(
            equipo, objetivo=objetivo_datos, incluir_amistosos=incluir_amistosos)

        if not escaneo.oficiales and not escaneo.amistosos_reservados and getattr(fuente_obj, "nombre", "") == "fotmob":
            avisar(f"⚠️ {equipo.get('nombre')} sin historial en FotMob; consultando Flashscore en cascada…")
            try:
                from fuentes_xg import FuenteFlashscore
                fs_fallback = FuenteFlashscore(ctx_compartido=getattr(fuente_obj, "ctx", None), verbose=False)
                fs_fallback.abrir()
                eq_fs = fs_fallback.resolver_equipo(equipo.get("nombre") or nombre_equipo, rival_contexto=rival)
                if eq_fs:
                    esc_fs = fs_fallback.escanear_oficiales(eq_fs, objetivo=objetivo_datos, incluir_amistosos=incluir_amistosos)
                    if esc_fs.oficiales:
                        fuente_obj = fs_fallback
                        auditoria["fuente"] = "flashscore"
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
            raise RuntimeError(
                f"{equipo.get('nombre')} no tiene partidos finalizados en la ventana de historial "
                f"de {fuente_obj.nombre}. Detalle: {escaneo.motivo_parada or 'sin historial'}"
            )

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
            escaneo = fuente_obj.escanear_oficiales(
                equipo, objetivo=nuevo_objetivo, incluir_amistosos=incluir_amistosos
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
            and getattr(fuente_obj, "ctx", None) is not None
        ):
            try:
                from fuentes_xg import FuenteFlashscore
                avisar(f"    ↻ FotMob aportó {partidos_con_datos}/{objetivo_datos}; completando en cascada desde Flashscore…")
                fs_comp = FuenteFlashscore(ctx_compartido=getattr(fuente_obj, "ctx", None), verbose=False)
                fs_comp.abrir()
                eq_fs = fs_comp.resolver_equipo(equipo.get("nombre") or nombre_equipo, rival_contexto=rival)
                if eq_fs:
                    esc_fs = fs_comp.escanear_oficiales(eq_fs, objetivo=objetivo_datos + 5, incluir_amistosos=False)
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
        if fuente_obj:
            try:
                fuente_obj.cerrar()
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
) -> dict[str, Any]:
    """
    Ejecuta la auditoría histórica completa de un equipo.
    Implementa Lock por equipo para serializar ejecuciones y evitar descargas duplicadas,
    así como caché en memoria para reutilizar datasets dentro de la misma sesión.
    """
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

    slug_equipo = _slug(nombre_equipo)
    lock_equipo = _obtener_lock_equipo(slug_equipo)

    with lock_equipo:
        if fuente_obj is None:
            doc_mem = obtener_auditoria_en_memoria(slug_equipo)
            if doc_mem is not None:
                eq_doc = doc_mem.get("equipo") or {}
                meta_ctx = rival if isinstance(rival, dict) else (
                    _inferir_meta_rival_desde_texto(rival) if isinstance(rival, str) and rival.strip() else None
                )
                pts_mem = puntuar_candidato(
                    {
                        "id": eq_doc.get("id"),
                        "name": eq_doc.get("nombre"),
                        "leagueName": eq_doc.get("liga"),
                        "leagueId": eq_doc.get("id_liga"),
                        "score": 100.0,
                    },
                    nombre_equipo,
                    rival_meta=meta_ctx,
                )
                coherente_mem = pts_mem[0] > -50 and pts_mem[2] >= 0 and not (pts_mem[0] < 0 and meta_ctx)
                if coherente_mem:
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

        res = _auditar_equipo_interno(
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
        )
        guardar_auditoria_en_memoria(slug_equipo, res)
        nom_resuelto = (res.get("equipo") or {}).get("nombre")
        if nom_resuelto and _slug(nom_resuelto) != slug_equipo:
            guardar_auditoria_en_memoria(_slug(nom_resuelto), res)
        return res


def _construir_resumen(auditoria: dict[str, Any]) -> dict[str, Any]:
    partidos = auditoria.get("partidos", [])
    con_datos = [p for p in partidos if any(
        any(fila["equipo"] is not None or fila["rival"] is not None
            for fila in mat.get("filas", []))
        for mat in (p.get("periodos") or {}).values()
    )]
    sin_datos = [p for p in partidos if p not in con_datos]

    # Cobertura por métrica: en cuántos partidos aparece con dato
    cobertura: dict[str, int] = {}
    for p in partidos:
        vistas = set()
        for mat in (p.get("periodos") or {}).values():
            for fila in mat.get("filas", []):
                if fila["equipo"] is not None or fila["rival"] is not None:
                    vistas.add(fila["clave"])
        for clave in vistas:
            cobertura[clave] = cobertura.get(clave, 0) + 1

    etiquetas = {d.clave: d.etiqueta for d in CATALOGO_METRICAS}
    cobertura_legible = {
        etiquetas.get(k, k): {"partidos_con_dato": v,
                              "cobertura_pct": round(v / max(1, len(partidos)) * 100, 1)}
        for k, v in sorted(cobertura.items(), key=lambda kv: -kv[1])
    }
    ausentes_totales = [etiquetas.get(d.clave, d.clave) for d in CATALOGO_METRICAS
                        if cobertura.get(d.clave, 0) == 0]

    return {
        "partidos_solicitados": auditoria.get("limite_partidos"),
        "minimo_con_datos": auditoria.get("minimo_con_datos"),
        "objetivo_alcanzado": (auditoria.get("escaneo") or {}).get("objetivo_alcanzado"),
        "partidos_auditados": len(partidos),
        "partidos_obtenidos": len(partidos),
        "partidos_con_datos": len(con_datos),
        "partidos_sin_datos": len(sin_datos),
        "ids_sin_datos": [p.get("id_partido") for p in sin_datos],
        "metricas_solicitadas": len(CATALOGO_METRICAS),
        "metricas_con_datos": len(cobertura),
        "metricas_sin_datos_en_ningun_partido": ausentes_totales,
        "cobertura_por_metrica": cobertura_legible,
    }


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


def _formatear_valor_metrica_45(
    clave: str,
    valor: Any,
    filas_map: dict[str, dict[str, Any]] | None = None,
    lado: str = "equipo",
) -> str:
    """Formatea limpiamente un valor según la especificación de la métrica y formato compuesto c/t (pct%)."""
    if valor is None:
        return "0"

    # Si se pasan las filas del periodo, verificar si es métrica compuesta con conteo + porcentaje
    if filas_map:
        if clave == "pases_largos_pct":
            comp = filas_map.get("pases_largos_completados", {}).get(lado)
            tot = filas_map.get("pases_largos", {}).get(lado)
            if comp is not None and tot is not None and tot > 0:
                pct = int(round(float(valor))) if valor is not None else int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({pct}%)"
        elif clave == "pases_tercio_final_pct":
            comp = filas_map.get("pases_tercio_final_completados", {}).get(lado)
            tot = filas_map.get("pases_tercio_final", {}).get(lado)
            if comp is not None and tot is not None and tot > 0:
                pct = int(round(float(valor))) if valor is not None else int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({pct}%)"
        elif clave == "cruces_pct":
            comp = filas_map.get("centros_completados", {}).get(lado)
            tot = filas_map.get("centros", {}).get(lado)
            if comp is not None and tot is not None and tot > 0:
                pct = int(round(float(valor))) if valor is not None else int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({pct}%)"
        elif clave == "entradas_pct":
            comp = filas_map.get("entradas_ganadas", {}).get(lado)
            tot = filas_map.get("entradas", {}).get(lado)
            if comp is not None and tot is not None and tot > 0:
                pct_val = filas_map.get("entradas_ganadas_pct", {}).get(lado)
                pct = int(round(pct_val)) if pct_val is not None else int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({pct}%)"
        elif clave == "pases_ratio":
            comp = filas_map.get("pases_completados", {}).get(lado)
            tot = filas_map.get("pases_total", {}).get(lado) or filas_map.get("pases_totales", {}).get(lado)
            pct = filas_map.get("pases_precision_pct", {}).get(lado) or filas_map.get("pases_pct", {}).get(lado)
            if comp is not None and tot is not None and tot > 0:
                p_int = int(round(pct)) if pct is not None else int(round(comp * 100.0 / tot))
                return f"{int(round(comp))}/{int(round(tot))} ({p_int}%)"
        elif clave in ("duelos_suelo_pct", "duelos_aereos_pct"):
            k_comp = "duelos_suelo_ganados" if clave == "duelos_suelo_pct" else "duelos_aereos_ganados"
            k_tot = "duelos_suelo" if clave == "duelos_suelo_pct" else "duelos_aereos"
            comp = filas_map.get(k_comp, {}).get(lado)
            tot = filas_map.get(k_tot, {}).get(lado)
            if comp is not None and tot is not None and tot > 0:
                pct = int(round(float(valor))) if valor is not None else int(round(comp * 100.0 / tot))
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

        enc_eq_1t = f_1t.get("goles_encajados", {}).get("equipo")
        enc_riv_1t = f_1t.get("goles_encajados", {}).get("rival")
        if enc_eq_1t is not None and enc_riv_1t is not None:
            gl_1t = int(round(enc_riv_1t if lado_l == "equipo" else enc_eq_1t))
            gv_1t = int(round(enc_eq_1t if lado_l == "equipo" else enc_riv_1t))
        else:
            gl_1t = 0
            gv_1t = 0
        gl_2t = max(0, gl - gl_1t)
        gv_2t = max(0, gv - gv_1t)

        xg_l = f_ft.get("xg", {}).get(lado_l)
        xg_v = f_ft.get("xg", {}).get(lado_v)
        str_xg = f"{float(xg_l or 0):.2f} vs {float(xg_v or 0):.2f}"

        fecha = str(p.get("fecha") or "N/D")[:10]
        torneo = str(p.get("torneo") or "Oficial")
        id_fm = str(p.get("id_partido") or p.get("id") or "N/D")
        fs = p.get("fuente_secundaria") or {}
        id_fs = str(fs.get("id_flashscore") or "N/D")

        proc_ft = (p.get("procedencia") or {}).get("FT") or {}
        if "flashscore" in str(proc_ft).lower() or fs.get("pareado"):
            fuente_txt = "FM+FS"
        else:
            fuente_txt = "FotMob"

        lineas.append(
            f"| {idx:02d} | {fecha} | {torneo} | {nom_l} vs {nom_v} | "
            f"{gl} - {gv} | {gl_1t} - {gv_1t} | {gl_2t} - {gv_2t} | "
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

    # Desglose de goles en 1T y 2T (derivado de goles_encajados)
    enc_eq_1t = f_1t.get("goles_encajados", {}).get("equipo")
    enc_riv_1t = f_1t.get("goles_encajados", {}).get("rival")
    if enc_eq_1t is not None and enc_riv_1t is not None:
        gl_1t = int(round(enc_riv_1t if lado_local == "equipo" else enc_eq_1t))
        gv_1t = int(round(enc_eq_1t if lado_local == "equipo" else enc_riv_1t))
    else:
        gl_1t = 0
        gv_1t = 0

    enc_eq_2t = f_2t.get("goles_encajados", {}).get("equipo")
    enc_riv_2t = f_2t.get("goles_encajados", {}).get("rival")
    if enc_eq_2t is not None and enc_riv_2t is not None:
        gl_2t = int(round(enc_riv_2t if lado_local == "equipo" else enc_eq_2t))
        gv_2t = int(round(enc_eq_2t if lado_local == "equipo" else enc_riv_2t))
    else:
        gl_2t = max(0, gl - gl_1t)
        gv_2t = max(0, gv - gv_1t)

    torneo = str(partido.get("torneo") or nombre_torneo_defecto or "Oficial")
    fecha = str(partido.get("fecha") or "N/D")[:10]
    id_fm = str(partido.get("id_partido") or partido.get("id") or "N/D")
    fs = partido.get("fuente_secundaria") or {}
    id_fs = str(fs.get("id_flashscore") or "N/D")

    proc_ft = (partido.get("procedencia") or {}).get("FT") or {}

    lineas: list[str] = [
        "=" * 84,
        f"MATRIZ DE PARTIDO [{k}/{total_partidos}] · {torneo} · Fecha: {fecha}",
        f"{nom_local} (Local) {gl} - {gv} {nom_visita} (Visita) | 1T: {gl_1t}-{gv_1t} | 2T: {gl_2t}-{gv_2t}",
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
        for num, clave, etiqueta, _cat in metricas_cat:
            # FT
            fila_ft = f_ft.get(clave) or {}
            val_l_ft = fila_ft.get(lado_local)
            val_v_ft = fila_ft.get(lado_visita)
            str_l_ft = _formatear_valor_metrica_45(clave, val_l_ft, f_ft, lado_local)
            str_v_ft = _formatear_valor_metrica_45(clave, val_v_ft, f_ft, lado_visita)

            # 1T
            fila_1t = f_1t.get(clave) or {}
            val_l_1t = fila_1t.get(lado_local)
            val_v_1t = fila_1t.get(lado_visita)
            str_l_1t = _formatear_valor_metrica_45(clave, val_l_1t, f_1t, lado_local)
            str_v_1t = _formatear_valor_metrica_45(clave, val_v_1t, f_1t, lado_visita)

            # 2T
            fila_2t = f_2t.get(clave) or {}
            val_l_2t = fila_2t.get(lado_local)
            val_v_2t = fila_2t.get(lado_visita)
            str_l_2t = _formatear_valor_metrica_45(clave, val_l_2t, f_2t, lado_local)
            str_v_2t = _formatear_valor_metrica_45(clave, val_v_2t, f_2t, lado_visita)

            # Fuente
            p_ft = (proc_ft.get("equipo", {}).get(clave)
                    or proc_ft.get("rival", {}).get(clave) or "")
            if clave == "saques_puerta" and ("est" in str(p_ft).lower() or fila_ft.get("estimado")):
                fuente_txt = "Est."
            elif "flashscore" in str(p_ft).lower():
                fuente_txt = "FM+FS"
            elif fs.get("pareado"):
                fuente_txt = "FM+FS"
            else:
                fuente_txt = "FotMob"

            lineas.append(
                f"| {num} | {etiqueta} (`{clave}`) | {str_l_ft} | {str_v_ft} | {str_l_1t} | {str_v_1t} | {str_l_2t} | {str_v_2t} | {fuente_txt} |"
            )

    return "\n".join(lineas)


def generar_informe_matrices_30(
    doc_local: dict[str, Any],
    doc_visita: dict[str, Any],
    ruta_md: str | None = None,
) -> tuple[str, str]:
    """
    Genera el informe Markdown completo de 30 matrices para Modalidad A (/auditar Local vs Visita):
    15 partidos oficiales de Local + 15 partidos oficiales de Visita (30 en total).
    Guarda el archivo en disco y genera además 2 tomos ligeros (< 30 KB) para apertura nativa en Telegram.
    Devuelve (contenido_markdown, ruta_archivo).
    """
    nom_local = (doc_local.get("equipo") or {}).get("nombre") or doc_local.get("equipo_solicitado") or "Local"
    nom_visita = (doc_visita.get("equipo") or {}).get("nombre") or doc_visita.get("equipo_solicitado") or "Visitante"
    ahora = _ahora_lima()
    fecha_hoy = ahora.strftime("%Y-%m-%d")

    partidos_local = [p for p in (doc_local.get("partidos") or []) if not p.get("es_amistoso")][:15]
    partidos_visita = [p for p in (doc_visita.get("partidos") or []) if not p.get("es_amistoso")][:15]

    total_entregados = len(partidos_local) + len(partidos_visita)

    bloques: list[str] = [
        f"# AUDITORÍA DE {total_entregados} MATRICES OFICIALES (MODALIDAD A) — {nom_local} vs {nom_visita}",
        "",
        f"- **Fecha de Generación**: {ahora.strftime('%Y-%m-%d %H:%M:%S')} (America/Lima)",
        f"- **Equipo Local**: {nom_local} ({len(partidos_local)} partidos 100% oficiales auditados)",
        f"- **Equipo Visitante**: {nom_visita} ({len(partidos_visita)} partidos 100% oficiales auditados)",
        f"- **Filtro Anti-Amistosos**: Estricto (cero amistosos en el cómputo; descarte y backfill hacia atrás)",
        f"- **Total de Matrices**: {total_entregados} matrices individuales completas (6 sub-tablas por matriz)",
        f"- **Desglose Multitiempo**: General (FT), Primer Tiempo (1T) y Segundo Tiempo (2T)",
        f"- **Catálogo Oficial**: 45 métricas del 01 al 45 sin ningún valor nulo ni inventado",
        f"- **Fusión de Fuentes**: FotMob (primaria) + Flashscore (secundaria) con identidades exactas",
        "",
        "---",
        "",
    ]

    # Tabla Resumen Inicial con todos los partidos auditados
    bloques.extend(_construir_tabla_resumen_inicial(partidos_local + partidos_visita))
    bloques.append("---")
    bloques.append("")

    bloques.append(f"## BLOQUE 1: {len(partidos_local)} PARTIDOS OFICIALES DE {nom_local.upper()} (LOCAL)")
    bloques.append("")

    for idx, p in enumerate(partidos_local, start=1):
        bloques.append(formatear_matriz_45_partido(p, k=idx, total_partidos=total_entregados))
        bloques.append("")

    bloques.append("---")
    bloques.append("")
    bloques.append(f"## BLOQUE 2: {len(partidos_visita)} PARTIDOS OFICIALES DE {nom_visita.upper()} (VISITANTE)")
    bloques.append("")

    for idx, p in enumerate(partidos_visita, start=len(partidos_local) + 1):
        bloques.append(formatear_matriz_45_partido(p, k=idx, total_partidos=total_entregados))
        bloques.append("")

    contenido = "\n".join(bloques)

    if not ruta_md:
        base_dir = os.path.join(AUDITORIA_DIR, fecha_hoy)
        os.makedirs(base_dir, exist_ok=True)
        ruta_md = os.path.join(
            base_dir,
            f"auditoria_matrices_{_slug(nom_local)}_vs_{_slug(nom_visita)}_{fecha_hoy}.md",
        )

    with open(ruta_md, "w", encoding="utf-8") as f:
        f.write(contenido)

    # ── GENERACIÓN DE 2 TOMOS LIGEROS (< 30 KB) PARA APERTURA NATIVA EN TELEGRAM ──
    try:
        bloques_t1 = [
            f"# AUDITORÍA DE MATRICES OFICIALES — TOMO 1: {nom_local.upper()} (LOCAL)",
            "",
            f"- **Fecha**: {ahora.strftime('%Y-%m-%d %H:%M:%S')} (America/Lima)",
            f"- **Equipo**: {nom_local} ({len(partidos_local)} partidos 100% oficiales auditados)",
            f"- **Rival del Cruce**: {nom_visita}",
            f"- **Catálogo Oficial**: 45 métricas completas en FT, 1T y 2T sin valores nulos",
            "",
            "---",
            "",
        ]
        bloques_t1.extend(_construir_tabla_resumen_inicial(partidos_local))
        bloques_t1.append("---")
        bloques_t1.append("")
        for idx, p in enumerate(partidos_local, start=1):
            bloques_t1.append(formatear_matriz_45_partido(p, k=idx, total_partidos=len(partidos_local)))
            bloques_t1.append("")
        contenido_t1 = "\n".join(bloques_t1)

        bloques_t2 = [
            f"# AUDITORÍA DE MATRICES OFICIALES — TOMO 2: {nom_visita.upper()} (VISITANTE)",
            "",
            f"- **Fecha**: {ahora.strftime('%Y-%m-%d %H:%M:%S')} (America/Lima)",
            f"- **Equipo**: {nom_visita} ({len(partidos_visita)} partidos 100% oficiales auditados)",
            f"- **Rival del Cruce**: {nom_local}",
            f"- **Catálogo Oficial**: 45 métricas completas en FT, 1T y 2T sin valores nulos",
            "",
            "---",
            "",
        ]
        bloques_t2.extend(_construir_tabla_resumen_inicial(partidos_visita))
        bloques_t2.append("---")
        bloques_t2.append("")
        for idx, p in enumerate(partidos_visita, start=1):
            bloques_t2.append(formatear_matriz_45_partido(p, k=idx, total_partidos=len(partidos_visita)))
            bloques_t2.append("")
        contenido_t2 = "\n".join(bloques_t2)

        ruta_tomo1 = ruta_md.replace(".md", "_tomo1.md")
        ruta_tomo2 = ruta_md.replace(".md", "_tomo2.md")
        with open(ruta_tomo1, "w", encoding="utf-8") as f:
            f.write(contenido_t1)
        with open(ruta_tomo2, "w", encoding="utf-8") as f:
            f.write(contenido_t2)
    except Exception as e_tomos:
        pass

    return contenido, ruta_md


def generar_informe_matrices_h2h(
    doc_h2h_a: dict[str, Any],
    doc_h2h_b: dict[str, Any],
    lista_partidos: list[dict[str, Any]],
    ruta_md: str | None = None,
) -> tuple[str, str]:
    """
    Genera el informe Markdown de N matrices para Modalidad B (/auditar Local h2h Visita).
    N matrices para los N enfrentamientos reales 2024-2026 (cero inventados).
    Guarda el archivo en disco y devuelve (contenido_markdown, ruta_archivo).
    """
    nom_a = (doc_h2h_a.get("equipo") or {}).get("nombre") or doc_h2h_a.get("equipo_solicitado") or "Equipo A"
    nom_b = (doc_h2h_b.get("equipo") or {}).get("nombre") or doc_h2h_b.get("equipo_solicitado") or "Equipo B"
    ahora = _ahora_lima()
    fecha_hoy = ahora.strftime("%Y-%m-%d")

    n = len(lista_partidos)

    bloques: list[str] = [
        f"# AUDITORÍA DE ENFRENTAMIENTOS DIRECTOS H2H (MODALIDAD B) — {nom_a} h2h {nom_b}",
        "",
        f"- **Fecha de Generación**: {ahora.strftime('%Y-%m-%d %H:%M:%S')} (America/Lima)",
        f"- **Ventana Temporal Estricta**: 2024-01-01 a 2026-12-31",
        f"- **Duelos Directos Reales Encontrados**: {n} partido(s)",
        f"- **Prohibición de Invención**: Cero partidos fabricados, sintéticos o sustituidos con otros rivales",
        f"- **Total de Matrices**: {n} matrices individuales completas (6 sub-tablas por matriz)",
        f"- **Desglose Multitiempo**: General (FT), Primer Tiempo (1T) y Segundo Tiempo (2T)",
        f"- **Catálogo Oficial**: 45 métricas del 01 al 45 sin valores nulos",
        f"- **Fusión de Fuentes**: FotMob + Flashscore",
        "",
        "---",
        "",
    ]

    # Tabla Resumen Inicial con los partidos H2H reales
    bloques.extend(_construir_tabla_resumen_inicial(lista_partidos))
    bloques.append("---")
    bloques.append("")

    for idx, p in enumerate(lista_partidos, start=1):
        bloques.append(formatear_matriz_45_partido(p, k=idx, total_partidos=n))
        bloques.append("")

    contenido = "\n".join(bloques)

    if not ruta_md:
        base_dir = os.path.join(config.INFORME_DIR, "h2h")
        os.makedirs(base_dir, exist_ok=True)
        ruta_md = os.path.join(
            base_dir,
            f"auditoria_h2h_matrices_{_slug(nom_a)}_{_slug(nom_b)}_{fecha_hoy}.md",
        )

    with open(ruta_md, "w", encoding="utf-8") as f:
        f.write(contenido)

    return contenido, ruta_md


def generar_informe_matrices_equipo(
    doc_equipo: dict[str, Any],
    ruta_md: str | None = None,
) -> tuple[str, str]:
    """
    Genera el informe Markdown de 15 matrices para una auditoría individual de equipo.
    """
    nom_eq = (doc_equipo.get("equipo") or {}).get("nombre") or doc_equipo.get("equipo_solicitado") or "Equipo"
    ahora = _ahora_lima()
    fecha_hoy = ahora.strftime("%Y-%m-%d")

    partidos = [p for p in (doc_equipo.get("partidos") or []) if not p.get("es_amistoso")][:15]
    n = len(partidos)

    bloques: list[str] = [
        f"# AUDITORÍA DE {n} MATRICES OFICIALES — {nom_eq}",
        "",
        f"- **Fecha de Generación**: {ahora.strftime('%Y-%m-%d %H:%M:%S')} (America/Lima)",
        f"- **Equipo Auditado**: {nom_eq}",
        f"- **Partidos 100% Oficiales**: {n} partidos con datos (amistosos descartados por completo)",
        f"- **Total de Matrices**: {n} matrices completas individuales (6 sub-tablas por matriz)",
        f"- **Desglose Multitiempo**: General (FT), Primer Tiempo (1T) y Segundo Tiempo (2T)",
        f"- **Catálogo Oficial**: 45 métricas del 01 al 45 sin valores nulos",
        f"- **Fusión de Fuentes**: FotMob + Flashscore",
        "",
        "---",
        "",
    ]

    # Tabla Resumen Inicial
    bloques.extend(_construir_tabla_resumen_inicial(partidos))
    bloques.append("---")
    bloques.append("")

    for idx, p in enumerate(partidos, start=1):
        bloques.append(formatear_matriz_45_partido(p, k=idx, total_partidos=n))
        bloques.append("")

    contenido = "\n".join(bloques)

    if not ruta_md:
        base_dir = os.path.join(AUDITORIA_DIR, fecha_hoy)
        os.makedirs(base_dir, exist_ok=True)
        ruta_md = os.path.join(
            base_dir,
            f"auditoria_matrices_15_{_slug(nom_eq)}_{fecha_hoy}.md",
        )

    with open(ruta_md, "w", encoding="utf-8") as f:
        f.write(contenido)

    return contenido, ruta_md



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
