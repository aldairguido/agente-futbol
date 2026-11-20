"""
agente_futbol/servicio_mercados.py
───────────────────────────────────────────────────────────────────────────────
PUENTE ENTRE EL FLUJO EXISTENTE Y EL MOTOR DE MERCADOS.

Responsabilidades:
  1. Resolver un enfrentamiento "Equipo A vs Equipo B" a partir de las auditorías
     ya generadas. Si no existen o están desactualizadas, reutiliza el scraper
     del proyecto (`auditoria_h2h.cargar_o_crear_auditoria`) con la misma lógica
     de frescura, filtro anti-amistosos y búsqueda hacia atrás.
  2. Decidir local/visitante con la condición real registrada, la fecha del
     encuentro y los metadatos disponibles (nunca por el orden del título).
  3. Ejecutar `motor_mercados.analizar` y persistir el informe técnico.
  4. Enviar a Telegram el mensaje filtrado, con registro de estado de envío y
     protección contra duplicados confirmados.

Estados separados (requisito de fiabilidad):
    analisis_completado → mensaje_preparado → envio_confirmado
Un fallo de Telegram NO pierde el análisis ni obliga a recalcular estadísticas.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from utilidades_seguridad import _safe_float, _safe_int, _SafeFloat, _SafeInt

float = _SafeFloat
int = _SafeInt

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from auditoria_h2h import (H2H_DIR, buscar_auditoria, cargar_o_crear_auditoria,
                           extraer_equipos, extraer_equipos_y_liga)
from auditoria_historica import (
    LIMITE_PARTIDOS_POR_DEFECTO,
    eliminar_cache_equipo,
    validar_coherencia_competitiva_local_visita,
)
from fuentes_xg import FuenteXG, extraer_liga_de_texto, resolver_pareja_misma_liga
from motor_mercados import (
    NOMBRE_MODELO,
    VERSION_MODELO,
    Analisis,
    Parametros,
    analizar,
)

BASE_DIR = Path(__file__).resolve().parent
MERCADOS_DIR = str(Path(config.INFORME_DIR) / "mercados")
REGISTRO_ENVIOS = str(Path(MERCADOS_DIR) / "registro_envios.json")
LIMITE_TELEGRAM = 4000        # margen bajo el límite de 4096 de la API


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


# ═══════════════════════════════════════════════════════════════════════════════
# RESOLUCIÓN DEL ENFRENTAMIENTO Y ZONA HORARIA OFICIAL AMERICA/LIMA (PUNTO 42)
# ═══════════════════════════════════════════════════════════════════════════════

def formatear_etiqueta_fecha_encabezado(meta: dict[str, Any] | None) -> str:
    """
    Bloque G (Ronda 18): Etiqueta única de fecha para encabezados de Ticket PNG,
    Súper-Cuadro y Auditoría 15P:
      - Con fixture confirmado: `"Partido: <fecha y hora de inicio con zona>"`
      - Sin fixture confirmado: `"Sin partido programado encontrado"` (nunca la fecha de hoy)
    """
    m = meta if isinstance(meta, dict) else {}
    etq = str(m.get("etiqueta_fecha") or "").strip()
    if etq.startswith(("Partido:", "Sin partido programado")):
        return etq
    if bool(m.get("sin_partido_proximo")) or (m.get("es_fecha_analisis") is True and not bool(m.get("fixture_confirmado"))):
        return "Sin partido programado encontrado"
    h_loc = str(m.get("hora_inicio_local") or "").strip()
    if h_loc:
        return f"Partido: {h_loc}"
    h_utc = str(m.get("hora_inicio_partido") or "").strip()
    if h_utc:
        try:
            from datetime import timedelta
            tz_lima = timezone(timedelta(hours=-5), name="America/Lima")
            s_c = h_utc.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s_c)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return f"Partido: {dt.astimezone(tz_lima).strftime('%Y-%m-%d %H:%M')} America/Lima"
        except Exception:
            pass
    f_raw = str(m.get("fecha_partido") or "").strip()
    f_low = f_raw.lower()
    if (
        not f_raw
        or f_low in ("none", "null", "no disponible", "s/f")
        or f_low.startswith(("análisis del", "analisis del", "sin partido"))
    ):
        return "Sin partido programado encontrado"
    if bool(m.get("fixture_confirmado")) or re.match(r"^\d{4}-\d{2}-\d{2}$", f_raw[:10]):
        zona = str(m.get("zona_horaria") or "America/Lima")
        return f"Partido: {f_raw[:10]} (hora s/d, {zona})"
    return "Sin partido programado encontrado"


def resolver_fecha_encuentro_lima(
    fecha_utc_o_iso: str | None = None,
    es_partido_programado: bool = True,
    ahora_utc: datetime | None = None,
    estado_partido: str | None = None,
) -> dict[str, Any]:
    """
    Punto 42 y Sección 7: Resuelve la fecha oficial del encuentro en `America/Lima` (UTC-5).
    - Soporta timestamps ISO/UTC con hora (`2026-11-05T02:00:00Z` -> `2026-11-04` en Lima),
      y fechas sin hora (`YYYY-MM-DD`, `DD/MM/YYYY`, `DD.MM.YYYY`) conservando la fecha
      publicada sin aplicar una conversión UTC ficticia.
    - Si el encuentro está marcado como aplazado/pospuesto/cancelado, no lo trata como
      partido activo del día y devuelve `partido_activo=False`.
    - Incluye `alcance_mercado="90_minutos_reglamentarios"` (separado de clasificación/prórroga/penaltis).
    """
    from datetime import timedelta
    tz_lima = timezone(timedelta(hours=-5), name="America/Lima")
    base_now = (ahora_utc or datetime.now(timezone.utc)).astimezone(tz_lima)
    hoy_lima = base_now.strftime("%Y-%m-%d")

    raw = str(fecha_utc_o_iso or "").strip()
    est_txt = f"{estado_partido or ''} {raw}".lower()
    if any(k in est_txt for k in ("aplazado", "postponed", "cancelled", "cancelado", "suspendido", "abandoned")):
        etiqueta_apl = "Sin partido programado encontrado (aplazado)"
        return {
            "fecha_partido": None,
            "etiqueta_fecha": etiqueta_apl,
            "hora_inicio_partido": None,
            "hora_inicio_local": None,
            "fixture_confirmado": False,
            "fuente_fecha": "fallback",
            "zona_horaria": "America/Lima",
            "es_fecha_analisis": True,
            "partido_activo": False,
            "estado_encuentro": "aplazado",
            "conversion_utc_aplicada": False,
            "alcance_mercado": "90_minutos_reglamentarios",
        }

    if raw and raw.lower() not in ("none", "null", "no disponible", "s/f") and not raw.lower().startswith(("análisis del", "analisis del", "sin partido programado")):
        try:
            # 1) Timestamp con hora explícita -> convertir a America/Lima
            if "T" in raw or ":" in raw:
                s_clean = raw.replace("Z", "+00:00")
                if " " in s_clean and "T" not in s_clean and re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", s_clean):
                    s_clean = s_clean.replace(" ", "T", 1)
                dt = datetime.fromisoformat(s_clean)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_utc = dt.astimezone(timezone.utc)
                dt_lima = dt.astimezone(tz_lima)
                f_str = dt_lima.strftime("%Y-%m-%d")
                h_loc = f"{dt_lima.strftime('%Y-%m-%d %H:%M')} America/Lima"
                if es_partido_programado:
                    return {
                        "fecha_partido": f_str,
                        "etiqueta_fecha": f"Partido: {h_loc}",
                        "hora_inicio_partido": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "hora_inicio_local": h_loc,
                        "fixture_confirmado": True,
                        "fuente_fecha": "fixture",
                        "zona_horaria": "America/Lima",
                        "es_fecha_analisis": False,
                        "partido_activo": True,
                        "estado_encuentro": "programado",
                        "conversion_utc_aplicada": True,
                        "alcance_mercado": "90_minutos_reglamentarios",
                    }
            # 2) Fecha sin hora YYYY-MM-DD -> conservar fecha publicada sin conversión UTC ficticia
            elif re.match(r"^\d{4}-\d{2}-\d{2}$", raw[:10]):
                f_str = raw[:10]
                if es_partido_programado:
                    return {
                        "fecha_partido": f_str,
                        "etiqueta_fecha": f"Partido: {f_str} (hora s/d, America/Lima)",
                        "hora_inicio_partido": None,
                        "hora_inicio_local": f"{f_str} (hora s/d, America/Lima)",
                        "fixture_confirmado": True,
                        "fuente_fecha": "fixture",
                        "zona_horaria": "America/Lima",
                        "es_fecha_analisis": False,
                        "partido_activo": True,
                        "estado_encuentro": "programado",
                        "conversion_utc_aplicada": False,
                        "alcance_mercado": "90_minutos_reglamentarios",
                    }
            # 3) Fecha sin hora DD/MM/YYYY o DD.MM.YYYY -> normalizar a YYYY-MM-DD sin conversión UTC ficticia
            else:
                m_dmy = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$", raw[:10].strip())
                if m_dmy and es_partido_programado:
                    dd, mm_n, yyyy = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
                    f_str = f"{yyyy:04d}-{mm_n:02d}-{dd:02d}"
                    return {
                        "fecha_partido": f_str,
                        "etiqueta_fecha": f"Partido: {f_str} (hora s/d, America/Lima)",
                        "hora_inicio_partido": None,
                        "hora_inicio_local": f"{f_str} (hora s/d, America/Lima)",
                        "fixture_confirmado": True,
                        "fuente_fecha": "fixture",
                        "zona_horaria": "America/Lima",
                        "es_fecha_analisis": False,
                        "partido_activo": True,
                        "estado_encuentro": "programado",
                        "conversion_utc_aplicada": False,
                        "alcance_mercado": "90_minutos_reglamentarios",
                    }
        except Exception:
            pass

    _ = hoy_lima
    etiqueta_sin = "Sin partido programado encontrado"
    return {
        "fecha_partido": None,
        "etiqueta_fecha": etiqueta_sin,
        "hora_inicio_partido": None,
        "hora_inicio_local": None,
        "fixture_confirmado": False,
        "fuente_fecha": "fallback",
        "zona_horaria": "America/Lima",
        "es_fecha_analisis": True,
        "partido_activo": False,
        "estado_encuentro": "analisis_sin_fecha_futura",
        "conversion_utc_aplicada": False,
        "alcance_mercado": "90_minutos_reglamentarios",
    }


def _partido_futuro_en_comun(doc_local: dict[str, Any],
                             doc_visitante: dict[str, Any],
                             equipo_local: str, equipo_visitante: str
                             ) -> dict[str, Any] | None:
    """
    Busca el PRÓXIMO enfrentamiento programado entre los dos equipos en el calendario
    que la fuente ya trae en cada reporte (`partidos_futuros`).

    Por qué hace falta: `_metadatos_del_encuentro` solo puede cruzar partidos YA
    JUGADOS por `id_partido`. Si los dos equipos no se han enfrentado en el historial
    (caso Roma vs Real Madrid), no hay partido común y la fecha quedaba en None.

    El cruce se hace por ID de equipo, no por nombre: «Roma» contra «AS Roma» fallaba
    al comparar texto. Se admite cualquiera de los dos órdenes de localía.
    """
    def futuros(doc: dict[str, Any]) -> list[dict[str, Any]]:
        for clave in ("partidos_futuros", "proximos_partidos", "fixtures_futuros"):
            valor = doc.get(clave)
            if isinstance(valor, list):
                return valor
        return []

    def id_de(doc: dict[str, Any]) -> str:
        return str((doc.get("equipo") or {}).get("id") or "")

    id_l, id_v = id_de(doc_local), id_de(doc_visitante)

    nom_l = {
        _clave_equipo(equipo_local),
        _clave_equipo(str((doc_local.get("equipo") or {}).get("nombre") or "")),
    } | {_clave_equipo(a) for a in (doc_local.get("equipo") or {}).get("alternativas") or []}
    nom_l.discard("")

    nom_v = {
        _clave_equipo(equipo_visitante),
        _clave_equipo(str((doc_visitante.get("equipo") or {}).get("nombre") or "")),
    } | {_clave_equipo(a) for a in (doc_visitante.get("equipo") or {}).get("alternativas") or []}
    nom_v.discard("")

    def coincide_bando(bid: str, bnom: str, target_id: str, target_noms: set[str]) -> bool:
        if target_id and bid and str(bid) == str(target_id):
            return True
        cl = _clave_equipo(bnom)
        if not cl:
            return False
        for tn in target_noms:
            if cl == tn or cl in tn or tn in cl:
                return True
        return False

    candidatos: list[tuple[str, dict[str, Any]]] = []
    vistos: set[str] = set()
    fixtures_todos = futuros(doc_local) + futuros(doc_visitante)
    for f in fixtures_todos:
        home_id = str(f.get("home_id") or "")
        away_id = str(f.get("away_id") or "")
        home_nom = str(f.get("home_nombre") or "")
        away_nom = str(f.get("away_nombre") or "")

        # Verificar si f es el enfrentamiento en orden directo (Local juega en casa)
        es_directo = (coincide_bando(home_id, home_nom, id_l, nom_l) and
                      coincide_bando(away_id, away_nom, id_v, nom_v))
        # O en orden invertido (Local juega fuera)
        es_invertido = (coincide_bando(home_id, home_nom, id_v, nom_v) and
                        coincide_bando(away_id, away_nom, id_l, nom_l))

        if not (es_directo or es_invertido):
            continue

        fecha_raw = str(f.get("utcTime") or f.get("fecha_iso") or f.get("fecha") or "")
        estado_f = str(f.get("estado") or f.get("status") or f.get("reason") or "")
        info_f = resolver_fecha_encuentro_lima(fecha_raw, es_partido_programado=True, estado_partido=estado_f)
        if not info_f.get("partido_activo", True):
            continue
        fecha = str(info_f["fecha_partido"])
        if not fecha or info_f.get("es_fecha_analisis"):
            continue
        clave = str(f.get("id_partido") or f"{fecha}|{home_id}|{away_id}")
        if clave in vistos:
            continue
        vistos.add(clave)
        es_casa = es_directo
        cand_meta: dict[str, Any] = {
            "id_partido": str(f.get("id_partido") or ""),
            "fecha_partido": fecha,
            "etiqueta_fecha": info_f["etiqueta_fecha"],
            "hora_inicio_partido": info_f.get("hora_inicio_partido"),
            "hora_inicio_local": info_f.get("hora_inicio_local"),
            "fixture_confirmado": True,
            "fuente_fecha": "fixture",
            "zona_horaria": "America/Lima",
            "es_fecha_analisis": False,
            "partido_activo": True,
            "alcance_mercado": "90_minutos_reglamentarios",
            "competicion": f.get("competicion"),
            "ronda": f.get("ronda"),
            "etapa": f.get("etapa"),
            "formato": f.get("formato"),
            "condicion_local": "casa" if es_casa else "fuera",
            "condicion_visitante": "fuera" if es_casa else "casa",
            "local_es": "local" if es_casa else "visitante",
            "evidencia_condicion": ("próximo enfrentamiento cruzado por ID numérico "
                                    "y nombre en el calendario de la fuente (America/Lima)"),
        }
        if info_f.get("hora_inicio_partido"):
            cand_meta["fuente_hora_inicio"] = str(f.get("fuente_hora") or "fotmob_fixture")
            cand_meta["zona_origen_hora_inicio"] = str(f.get("zona_origen") or "UTC")
        if f.get("id_flashscore"):
            cand_meta["id_flashscore_partido"] = str(f.get("id_flashscore"))
        candidatos.append((str(info_f.get("hora_inicio_partido") or fecha), cand_meta))
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: x[0])          # el más próximo primero
    return candidatos[0][1]


def obtener_hora_inicio_fotmob_match_details(id_partido: str, timeout: int = 5) -> str | None:
    """Consulta `/api/data/matchDetails?matchId={id_partido}` en FotMob y devuelve `utcTime` ISO UTC."""
    pid = str(id_partido or "").strip()
    if not pid or not pid.isdigit():
        return None
    try:
        import urllib.request
        url = f"https://www.fotmob.com/api/data/matchDetails?matchId={pid}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            datos = json.loads(resp.read().decode("utf-8"))
        cand = (
            (datos.get("general") or {}).get("matchTimeUTCDate")
            or ((datos.get("header") or {}).get("status") or {}).get("utcTime")
        )
        s = str(cand or "").strip()
        if s and ("T" in s or ":" in s):
            return s
    except Exception:
        return None
    return None


def _fecha_utc_compatible_fixture(utc_iso: str, fecha_esperada: str | None) -> bool:
    """Permite ±2 días por desfase UTC/America_Lima frente a la fecha del fixture si existe."""
    if not fecha_esperada:
        return True
    try:
        from datetime import date
        d_esp = date.fromisoformat(str(fecha_esperada)[:10])
        d_enc = date.fromisoformat(str(utc_iso)[:10])
        return abs((d_esp - d_enc).days) <= 2
    except Exception:
        return True


def comparar_horas_inicio_fuentes(
    hora_fotmob: str | None,
    hora_flashscore: str | None,
    *,
    tolerancia_min: float = 15.0,
) -> dict[str, Any]:
    """
    Compara las horas de inicio UTC reportadas por FotMob y Flashscore.
    Si difieren, marca `hora_inicio_discrepante=True` y calcula `diferencia_hora_inicio_min`.
    Si la diferencia supera `tolerancia_min` (15 min), marca `excede_tolerancia_oos=True`.
    """
    def _parse_utc(v: str | None):
        if not v:
            return None
        s = str(v).strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return None
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    dt_fm = _parse_utc(hora_fotmob)
    dt_fs = _parse_utc(hora_flashscore)
    h_fm = dt_fm.strftime("%Y-%m-%dT%H:%M:%SZ") if dt_fm else (str(hora_fotmob).strip() if hora_fotmob else None)
    h_fs = dt_fs.strftime("%Y-%m-%dT%H:%M:%SZ") if dt_fs else (str(hora_flashscore).strip() if hora_flashscore else None)

    diff_min: float | None = None
    discrepante = False
    excede_oos = False
    if dt_fm is not None and dt_fs is not None:
        diff_min = round(abs((dt_fm - dt_fs).total_seconds()) / 60.0, 2)
        discrepante = diff_min > 0.0
        excede_oos = diff_min > float(tolerancia_min)
    return {
        "hora_inicio_fotmob": h_fm,
        "hora_inicio_flashscore": h_fs,
        "diferencia_hora_inicio_min": diff_min,
        "hora_inicio_discrepante": discrepante,
        "excede_tolerancia_oos": excede_oos,
    }


def resolver_hora_inicio_encuentro(
    contexto: dict[str, Any],
    *,
    equipo_local: str = "",
    equipo_visitante: str = "",
    id_flashscore: str | None = None,
    cliente_fs: Any = None,
    fetcher_fotmob: Any = None,
) -> dict[str, Any]:
    """
    Completa `hora_inicio_partido` (ISO UTC), `hora_inicio_local` (`America/Lima`) y
    su procedencia (`fuente_hora_inicio`, `zona_origen_hora_inicio`) usando Flashscore y
    FotMob antes de emitir el ticket, snapshot y registro en ledger OOS.
    Cuando ambas fuentes devuelven hora, guarda ambas (`hora_inicio_fotmob`,
    `hora_inicio_flashscore`, `diferencia_hora_inicio_min`, `hora_inicio_discrepante`).
    Si no se obtiene hora real con zona, mantiene `hora_inicio_partido=None` (no-OOS).
    """
    if not isinstance(contexto, dict):
        return contexto
    if contexto.get("partido_ya_jugado") or contexto.get("omitir_busqueda_hora_red"):
        if contexto.get("hora_inicio_partido"):
            contexto.setdefault("fuente_hora_inicio", "fotmob_fixture")
            contexto.setdefault("zona_origen_hora_inicio", "UTC")
            contexto.setdefault("zona_horaria", "America/Lima")
        else:
            contexto.setdefault("fuente_hora_inicio", "no_disponible")
        return contexto

    fecha_esp = str(contexto.get("fecha_partido") or "").strip() or None
    if fecha_esp and not re.match(r"^\d{4}-\d{2}-\d{2}", fecha_esp):
        fecha_esp = None

    utc_fotmob: str | None = None
    fuente_fotmob: str | None = None
    utc_flashscore: str | None = None
    fuente_flashscore: str | None = None
    zona_flashscore = "UTC"
    id_fs_resuelto = str(id_flashscore or contexto.get("id_flashscore_partido") or "").strip() or None

    # Si contexto ya trae hora_inicio_partido (p. ej. FotMob fixture), registrarla como FotMob
    if contexto.get("hora_inicio_partido"):
        f_prev = str(contexto.get("fuente_hora_inicio") or "fotmob_fixture").strip()
        if "flashscore" in f_prev.lower():
            utc_flashscore = str(contexto["hora_inicio_partido"])
            fuente_flashscore = f_prev
        else:
            utc_fotmob = str(contexto["hora_inicio_partido"])
            fuente_fotmob = f_prev
        contexto.setdefault("fuente_hora_inicio", f_prev)
        contexto.setdefault("zona_origen_hora_inicio", "UTC")
        contexto.setdefault("zona_horaria", "America/Lima")

    if contexto.get("hora_inicio_fotmob") and not utc_fotmob:
        utc_fotmob = str(contexto["hora_inicio_fotmob"])
        fuente_fotmob = "fotmob"
    if contexto.get("hora_inicio_flashscore") and not utc_flashscore:
        utc_flashscore = str(contexto["hora_inicio_flashscore"])
        fuente_flashscore = "flashscore"

    # 1) Flashscore: ID próximo explícito o feed diario f_1_0_{dia} (AD/DC en epoch UTC)
    if not utc_flashscore and ((equipo_local and equipo_visitante) or id_fs_resuelto):
        try:
            fs_cli = cliente_fs
            if fs_cli is None:
                from fuente_flashscore import ClienteFlashscore
                fs_cli = ClienteFlashscore(timeout=5)
            if id_fs_resuelto:
                utc_id = fs_cli.hora_inicio_desde_id(id_fs_resuelto)
                if utc_id and _fecha_utc_compatible_fixture(utc_id, fecha_esp):
                    utc_flashscore = utc_id
                    fuente_flashscore = "flashscore_dc"
            if not utc_flashscore and equipo_local and equipo_visitante:
                fx_fs = fs_cli.buscar_fixture_programado(
                    equipo_local,
                    equipo_visitante,
                    fecha_objetivo=fecha_esp,
                )
                if fx_fs and fx_fs.get("utcTime") and _fecha_utc_compatible_fixture(fx_fs["utcTime"], fecha_esp):
                    utc_flashscore = str(fx_fs["utcTime"])
                    fuente_flashscore = str(fx_fs.get("fuente") or "flashscore")
                    zona_flashscore = str(fx_fs.get("zona_origen") or "UTC")
                    if fx_fs.get("id_flashscore"):
                        id_fs_resuelto = str(fx_fs["id_flashscore"])
        except Exception:
            pass

    # 2) FotMob matchDetails cuando no tenemos hora FotMob y hay id_partido numérico
    id_p = str(contexto.get("id_partido") or "").strip()
    if not utc_fotmob and id_p and id_p.isdigit():
        try:
            fn_fm = fetcher_fotmob or obtener_hora_inicio_fotmob_match_details
            utc_fm = fn_fm(id_p)
            if utc_fm and _fecha_utc_compatible_fixture(utc_fm, fecha_esp):
                utc_fotmob = str(utc_fm)
                fuente_fotmob = "fotmob_match_details"
        except Exception:
            pass

    # Comparar ambas fuentes si están presentes (Bloque 6 Ronda 22)
    comp_h = comparar_horas_inicio_fuentes(utc_fotmob, utc_flashscore, tolerancia_min=15.0)
    if comp_h["hora_inicio_fotmob"] is not None:
        contexto["hora_inicio_fotmob"] = comp_h["hora_inicio_fotmob"]
    if comp_h["hora_inicio_flashscore"] is not None:
        contexto["hora_inicio_flashscore"] = comp_h["hora_inicio_flashscore"]
    if comp_h["diferencia_hora_inicio_min"] is not None:
        contexto["diferencia_hora_inicio_min"] = comp_h["diferencia_hora_inicio_min"]
    if comp_h["hora_inicio_discrepante"]:
        contexto["hora_inicio_discrepante"] = True

    if contexto.get("hora_inicio_partido"):
        if id_fs_resuelto:
            contexto["id_flashscore_partido"] = id_fs_resuelto
        return contexto

    utc_resuelta = utc_flashscore or utc_fotmob
    fuente_resuelta = fuente_flashscore if utc_flashscore else fuente_fotmob
    zona_resuelta = zona_flashscore if utc_flashscore else "UTC"

    if utc_resuelta:
        info = resolver_fecha_encuentro_lima(utc_resuelta, es_partido_programado=True)
        if info.get("hora_inicio_partido"):
            contexto["fecha_partido"] = info["fecha_partido"]
            contexto["etiqueta_fecha"] = info["etiqueta_fecha"]
            contexto["hora_inicio_partido"] = info["hora_inicio_partido"]
            contexto["hora_inicio_local"] = info["hora_inicio_local"]
            contexto["fixture_confirmado"] = True
            contexto["fuente_fecha"] = "fixture"
            contexto["zona_horaria"] = "America/Lima"
            contexto["es_fecha_analisis"] = False
            contexto["partido_activo"] = True
            contexto["sin_partido_proximo"] = False
            contexto["fuente_hora_inicio"] = fuente_resuelta or "fixture"
            contexto["zona_origen_hora_inicio"] = zona_resuelta
            if id_fs_resuelto:
                contexto["id_flashscore_partido"] = id_fs_resuelto
            return contexto

    if id_fs_resuelto:
        contexto["id_flashscore_partido"] = id_fs_resuelto
    contexto.setdefault("fuente_hora_inicio", "no_disponible")
    return contexto


def _metadatos_del_encuentro(doc_local: dict[str, Any], doc_visitante: dict[str, Any],
                             equipo_local: str, equipo_visitante: str) -> dict[str, Any]:
    """
    Punto 42: Resuelve la fecha y competición del enfrentamiento en `America/Lima`.
      1. PRÓXIMO enfrentamiento programado en el calendario (`partidos_futuros`),
         convirtiendo su timestamp UTC a `America/Lima`.
      2. Si no existe partido futuro programado en el calendario, PROHIBIDO reciclar la
         fecha de un enfrentamiento pasado (`2025-11-28`, `2026-03-07`, etc.) como si
         fuera la fecha del partido actual: se rotula explícitamente
         `"Análisis del YYYY-MM-DD"` en `America/Lima` y se comparte el mismo valor
         en Foto 1 y Foto 2.
    """
    # 1) Próximo enfrentamiento programado: es el cruce que se está consultando.
    proximo = _partido_futuro_en_comun(doc_local, doc_visitante,
                                       equipo_local, equipo_visitante)
    if proximo:
        return proximo

    info_analisis = resolver_fecha_encuentro_lima(None, es_partido_programado=False)

    por_id: dict[str, dict[str, Any]] = {}
    for doc, equipo in ((doc_local, equipo_local), (doc_visitante, equipo_visitante)):
        for p in doc.get("partidos", []):
            pid = str(p.get("id_partido"))
            por_id.setdefault(pid, {})[equipo.lower()] = p

    nombre_l = equipo_local.lower()
    nombre_v = equipo_visitante.lower()
    for pid, bandos in por_id.items():
        claves = list(bandos.keys())
        tiene_l = any(nombre_l in k or k in nombre_l for k in claves)
        tiene_v = any(nombre_v in k or k in nombre_v for k in claves)
        if tiene_l and tiene_v and len(bandos) >= 2:
            pl = bandos.get(claves[0])
            pv = bandos.get(claves[1])
            cond_l = None
            cond_v = None
            for k, p in bandos.items():
                if nombre_l in k or k in nombre_l:
                    cond_l = str(p.get("condicion", "")).lower()
                if nombre_v in k or k in nombre_v:
                    cond_v = str(p.get("condicion", "")).lower()

            m_l = pl.get("marcador") or {}
            m_v = pv.get("marcador") or {}
            g_l = None
            g_v = None
            if cond_l == "casa":
                g_l = pl.get("goles_favor") if pl.get("goles_favor") is not None else m_l.get("equipo")
                g_v = pl.get("goles_contra") if pl.get("goles_contra") is not None else m_l.get("rival")
            elif cond_l == "fuera":
                g_v = pl.get("goles_favor") if pl.get("goles_favor") is not None else m_l.get("equipo")
                g_l = pl.get("goles_contra") if pl.get("goles_contra") is not None else m_l.get("rival")
            elif cond_v == "casa":
                g_v = pv.get("goles_favor") if pv.get("goles_favor") is not None else m_v.get("equipo")
                g_l = pv.get("goles_contra") if pv.get("goles_contra") is not None else m_v.get("rival")
            elif cond_v == "fuera":
                g_l = pv.get("goles_favor") if pv.get("goles_favor") is not None else m_v.get("equipo")
                g_v = pv.get("goles_contra") if pv.get("goles_contra") is not None else m_v.get("rival")
            if (g_l is None or g_v is None) and m_l.get("texto") and "-" in str(m_l.get("texto")):
                parts = str(m_l["texto"]).split("-")
                try:
                    g_l, g_v = int(parts[0].strip()), int(parts[1].strip())
                except Exception:
                    pass

            res_txt = f"{equipo_local} {g_l} - {g_v} {equipo_visitante}" if (g_l is not None and g_v is not None) else (m_l.get("texto") or "Finalizado")
            fecha_jug = pl.get("fecha") or pv.get("fecha")

            return {
                "id_partido": pid,
                "partido_ya_jugado": True,
                "resultado_partido": res_txt,
                "goles_local": g_l,
                "goles_visitante": g_v,
                "fecha_jugado": fecha_jug,
                "fecha_partido": info_analisis["fecha_partido"],
                "etiqueta_fecha": info_analisis["etiqueta_fecha"],
                "hora_inicio_partido": None,
                "hora_inicio_local": None,
                "fixture_confirmado": False,
                "fuente_fecha": "fallback",
                "fecha_ultimo_h2h": fecha_jug,
                "zona_horaria": "America/Lima",
                "es_fecha_analisis": True,
                "partido_activo": False,
                "alcance_mercado": "90_minutos_reglamentarios",
                "competicion": pl.get("torneo") or pv.get("torneo"),
                "condicion_local": cond_l,
                "condicion_visitante": cond_v,
                "evidencia_condicion": "condición por enfrentamiento previo registrado (partido ya jugado)",
                "sin_partido_proximo": True,
            }

    def mas_reciente(doc: dict[str, Any]) -> dict[str, Any] | None:
        partidos = sorted(doc.get("partidos", []), key=lambda p: str(p.get("fecha") or ""),
                          reverse=True)
        return partidos[0] if partidos else None

    reciente_l, reciente_v = mas_reciente(doc_local), mas_reciente(doc_visitante)
    return {
        "id_partido": None,
        "fecha_partido": info_analisis["fecha_partido"],
        "etiqueta_fecha": info_analisis["etiqueta_fecha"],
        "hora_inicio_partido": None,
        "hora_inicio_local": None,
        "fixture_confirmado": False,
        "fuente_fecha": "fallback",
        "zona_horaria": "America/Lima",
        "es_fecha_analisis": True,
        "partido_activo": False,
        "alcance_mercado": "90_minutos_reglamentarios",
        "competicion": ((reciente_l or {}).get("torneo") or (reciente_v or {}).get("torneo")),
        "condicion_local": None,
        "condicion_visitante": None,
        "evidencia_condicion": ("Sin partido próximo en calendario (localía por orden "
                                "de consulta)"),
        "sin_partido_proximo": True,
    }


def construir_resumen_h2h_ticket(
    doc_local: dict[str, Any] | None,
    doc_visitante: dict[str, Any] | None,
    equipo_local: str,
    equipo_visitante: str,
    fecha_objetivo: str | None = None,
    info_h2h: dict[str, Any] | None = None,
) -> str:
    """Extrae enfrentamientos directos recientes (< fecha_objetivo) de forma puramente informativa para el Ticket."""
    from fuentes_xg import _sin_acentos, variantes_de_busqueda
    from resolvedor_entidades import (
        obtener_nombre_flashscore,
        obtener_nombre_fotmob,
        obtener_seleccion_nacional,
    )

    def _toks(n: str) -> set[str]:
        s = _sin_acentos(str(n or "")).lower().strip()
        als = {s}
        try:
            for v in variantes_de_busqueda(n) or []:
                als.add(v)
        except Exception:
            pass
        try:
            sel = obtener_seleccion_nacional(n)
            if sel:
                als.add(sel[0])
                als.update(sel[2] or [])
            als.add(obtener_nombre_flashscore(n))
            als.add(obtener_nombre_fotmob(n))
        except Exception:
            pass
        return {_sin_acentos(str(a)).lower().strip() for a in als if a and len(_sin_acentos(str(a)).strip()) >= 3}

    t_l = _toks(equipo_local)
    t_v = _toks(equipo_visitante)
    fecha_lim = str(fecha_objetivo or "")[:10]

    def _mismo(nombre: str, toks: set[str]) -> bool:
        s = _sin_acentos(str(nombre or "")).lower().strip()
        return any(s == tk or tk in s or s in tk for tk in toks if len(tk) >= 3)

    encontrados: list[tuple[str, str, str, int, int]] = []
    vistos: set[tuple[str, str, str]] = set()
    candidatos = list((info_h2h or {}).get("duelos") or [])
    for doc in (doc_local, doc_visitante):
        if isinstance(doc, dict):
            candidatos.extend(doc.get("partidos") or [])

    for p in candidatos:
        if not isinstance(p, dict):
            continue
        f_p = str(p.get("fecha") or "")[:10]
        if fecha_lim and len(f_p) == 10 and f_p >= fecha_lim:
            continue
        marc = p.get("marcador") if isinstance(p.get("marcador"), dict) else {}
        if "equipo" in p and "rival" in p:
            loc = p.get("equipo").get("nombre") if isinstance(p.get("equipo"), dict) else str(p.get("equipo") or "")
            vis = p.get("rival").get("nombre") if isinstance(p.get("rival"), dict) else str(p.get("rival") or "")
            es_fuera = str(p.get("condicion") or "").lower() in ("fuera", "visitante", "away")
            g_eq = p.get("goles_equipo") if p.get("goles_equipo") is not None else marc.get("equipo")
            g_riv = p.get("goles_rival") if p.get("goles_rival") is not None else marc.get("rival")
            if es_fuera:
                loc, vis = vis, loc
                gl = p.get("goles_local") if p.get("goles_local") is not None else marc.get("local")
                gv = p.get("goles_visitante") if p.get("goles_visitante") is not None else marc.get("visitante")
                if gl is None or gv is None:
                    gl, gv = g_riv, g_eq
            else:
                gl = p.get("goles_local") if p.get("goles_local") is not None else marc.get("local")
                gv = p.get("goles_visitante") if p.get("goles_visitante") is not None else marc.get("visitante")
                if gl is None or gv is None:
                    gl, gv = g_eq, g_riv
        else:
            loc = p.get("local").get("nombre") if isinstance(p.get("local"), dict) else str(p.get("local") or "")
            vis = p.get("visitante").get("nombre") if isinstance(p.get("visitante"), dict) else str(p.get("visitante") or "")
            gl = p.get("goles_local") if p.get("goles_local") is not None else marc.get("local")
            gv = p.get("goles_visitante") if p.get("goles_visitante") is not None else marc.get("visitante")
        if gl is None or gv is None:
            continue
        es_lv = _mismo(loc, t_l) and _mismo(vis, t_v)
        es_vl = _mismo(loc, t_v) and _mismo(vis, t_l)
        if not (es_lv or es_vl):
            continue
        loc_disp = equipo_local if _mismo(loc, t_l) else equipo_visitante
        vis_disp = equipo_visitante if _mismo(vis, t_v) else equipo_local
        clave = (f_p, _sin_acentos(loc_disp).lower(), _sin_acentos(vis_disp).lower())
        if clave in vistos:
            continue
        vistos.add(clave)
        encontrados.append((f_p, loc_disp, vis_disp, int(gl), int(gv)))

    if not encontrados:
        return ""
    encontrados.sort(key=lambda x: x[0], reverse=True)
    partes = []
    for f_p, loc, vis, gl, gv in encontrados[:2]:
        try:
            y, m, d = f_p.split("-")
            f_es = f"{d}/{m}/{y}"
        except Exception:
            f_es = f_p
        partes.append(f"{loc} {gl}-{gv} {vis} ({f_es})")
    return "H2H Reciente (informativo): " + " · ".join(partes)


_SINONIMOS_PAIS_LIGA = {
    "brazil": "brasil",
    "spain": "espana",
    "italy": "italia",
    "england": "inglaterra",
    "uk": "inglaterra",
    "germany": "alemania",
    "france": "francia",
    "netherlands": "holanda",
    "belgium": "belgica",
    "scotland": "escocia",
    "switzerland": "suiza",
    "turkey": "turquia",
    "turkiye": "turquia",
    "morocco": "marruecos",
}


def son_ligas_equivalentes(liga_decl: str, liga_det: str) -> bool:
    """
    Determina si la competición declarada y la detectada corresponden al mismo torneo
    o a la misma familia continental de selecciones (Punto 50), normalizando mayúsculas,
    acentos, signos como ':' final y sinónimos de país ('Brasil'/'BRAZIL').
    """
    if not liga_decl or not liga_det:
        return True
    from fuentes_xg import _sin_acentos
    s_decl = _sin_acentos(str(liga_decl)).lower().strip().rstrip(":").strip()
    s_det = _sin_acentos(str(liga_det)).lower().strip().rstrip(":").strip()
    if s_decl == s_det:
        return True

    def _raiz(t: str) -> str:
        s = _sin_acentos(str(t)).lower().strip().rstrip(":").strip()
        toks = [
            _SINONIMOS_PAIS_LIGA.get(w, w)
            for w in re.split(r"[^a-z0-9]+", s)
            if w
        ]
        s_norm = "".join(toks)
        s_norm = s_norm.replace("brasileirao", "brasileir").replace("brasileiro", "brasileir")
        s_norm = s_norm.replace("campeonato", "")
        return s_norm

    r1 = _raiz(s_decl)
    r2 = _raiz(s_det)
    if not r1 or not r2:
        return True
    if r1 == r2 or r1 in r2 or r2 in r1:
        return True

    from fuentes_xg import _es_region_geografica
    if _es_region_geografica(s_decl) or _es_region_geografica(s_det):
        return False

    # Familias estrictas de COMPETICIONES de selecciones (nunca confundir regiones como "Europe" con un torneo)
    familias = [
        # UEFA Nations League (sin "europe"/"europa")
        {"nationsleague", "uefanationsleague"},
        # UEFA Euro / Eurocopa
        {"eurocopa", "uefaeuro"},
        # Eliminatorias UEFA
        {"eliminatoriasuefa", "worldcupqualuefa", "worldcupqualificationuefa"},
        # Copa América (sin "southamerica"/"sudamerica")
        {"copaamerica"},
        # Eliminatorias CONMEBOL
        {
            "eliminatoriasconmebol",
            "eliminatoriassudamericanas",
            "clasificacionconmebol",
            "worldcupclasificacionconmebol",
            "worldcupqualsouthamerica",
            "worldcupqualificationsouthamerica",
        },
        # CONCACAF Nations League / Copa Oro
        {"concacafnationsleague", "copaoro", "goldcup"},
        # Copa Africana / Asiática
        {"copaafrica", "africancupofnations", "copaasiatica", "asiancup"},
        # FIFA World Cup (sin región "world" genérica ni confundir con Word/Club WC)
        {"worldcup", "copadelmundo", "mundial", "fifaworldcup", "worldchampionship"},
    ]
    toks_decl = {w for w in re.split(r"[\s\-_./()\[\]:]+", s_decl) if w}
    toks_det = {w for w in re.split(r"[\s\-_./()\[\]:]+", s_det) if w}
    toks_decl.add(r1)
    toks_det.add(r2)

    for fam in familias:
        decl_in_fam = any(m in toks_decl or any(m in t for t in toks_decl) for m in fam)
        det_in_fam = any(m in toks_det or any(m in t for t in toks_det) for m in fam)
        if decl_in_fam and det_in_fam:
            return True

    return False


def resolver_enfrentamiento(
    texto_o_equipo_a: str,
    equipo_b: str | None = None,
    limite: int = LIMITE_PARTIDOS_POR_DEFECTO,
    frescura_horas: float | None = None,
    forzar: bool = False,
    headless: bool = True,
    fuente_obj: FuenteXG | None = None,
    progreso: Any = None,
    liga: dict[str, Any] | str | None = None,
    fecha_corte: Any = None,
    id_partido_excluir: Any = None,
    cutoff_timestamp: Any = None,
    exclude_match_ids: Any = None,
) -> dict[str, Any]:
    """
    Resuelve un enfrentamiento y devuelve los documentos de ambos equipos con sus
    metadatos. Acepta "A vs B", "[Liga] A vs B" en un solo argumento o dos argumentos separados.
    """
    def avisar(msg: str) -> None:
        # Evitar duplicar en stdout los logs detallados por partido que auditar_equipo ya imprime como [AUDITORÍA]
        if not re.search(r"^\[\d+/\d+\]", msg.strip()) and not msg.startswith("    "):
            print(f"[MERCADOS] {msg}")
        if callable(progreso):
            try:
                progreso(msg)
            except Exception:
                pass

    meta_ctx: dict[str, Any] = {
        "pais_iso": "INT",
        "liga": "Desconocida",
        "es_femenino": False,
        "es_juvenil": False,
    }

    liga_extraida: dict[str, Any] | None = None
    if equipo_b is None:
        res_eq = extraer_equipos_y_liga(texto_o_equipo_a)
        if not res_eq:
            raise ValueError(
                f"No se detectó un enfrentamiento en {texto_o_equipo_a!r}. "
                f"Usa el formato \"Equipo A vs Equipo B\".")
        equipo_local, equipo_visitante, liga_extraida = res_eq
    else:
        liga_a, eq_a_limpio = extraer_liga_de_texto(texto_o_equipo_a)
        liga_b, eq_b_limpio = extraer_liga_de_texto(equipo_b)
        equipo_local = (eq_a_limpio or texto_o_equipo_a).strip()
        equipo_visitante = (eq_b_limpio or equipo_b).strip()
        liga_extraida = liga_a or liga_b

    liga_final = liga or liga_extraida

    # PASO #1 OBLIGATORIO: Puente Bilingüe Flashscore -> FotMob (resolvedor_entidades)
    from resolvedor_entidades import resolver as _resolver_entidades, formatear_mensaje_entidades_verificadas
    nombre_liga_txt = (
        liga_final.get("nombre") if isinstance(liga_final, dict)
        else (str(liga_final) if liga_final else "")
    ) or ""
    res_puente = _resolver_entidades(nombre_liga_txt, equipo_local, equipo_visitante)
    if res_puente.equipo_local:
        equipo_local = res_puente.equipo_local
    if res_puente.equipo_visita:
        equipo_visitante = res_puente.equipo_visita
    if isinstance(liga_final, dict):
        liga_final = dict(liga_final)
        if res_puente.pais_iso and not liga_final.get("pais_iso"):
            liga_final["pais_iso"] = res_puente.pais_iso
        liga_final["es_femenino"] = bool(liga_final.get("es_femenino") or res_puente.es_femenino)
        liga_final["es_juvenil"] = bool(liga_final.get("es_juvenil") or res_puente.es_juvenil)
    elif res_puente.pais_iso or res_puente.es_femenino or res_puente.es_juvenil:
        liga_final = {
            "nombre": nombre_liga_txt or res_puente.liga or "",
            "liga": nombre_liga_txt or res_puente.liga or "",
            "pais_iso": res_puente.pais_iso,
            "es_femenino": res_puente.es_femenino,
            "es_juvenil": res_puente.es_juvenil,
        }

    kwargs: dict[str, Any] = {"limite": limite, "forzar": forzar, "headless": headless,
                              "progreso": avisar}
    if frescura_horas is not None:
        kwargs["frescura_horas"] = frescura_horas
    if liga_final is not None:
        kwargs["liga"] = liga_final
    if fecha_corte is not None:
        kwargs["fecha_corte"] = fecha_corte
    if id_partido_excluir is not None:
        kwargs["id_partido_excluir"] = id_partido_excluir
    if cutoff_timestamp is not None:
        kwargs["cutoff_timestamp"] = cutoff_timestamp
    if exclude_match_ids is not None:
        kwargs["exclude_match_ids"] = exclude_match_ids

    etiqueta_liga = ""
    if isinstance(liga_final, dict) and liga_final.get("nombre"):
        etiqueta_liga = f" [{liga_final['nombre']}]"
    elif isinstance(liga_final, str) and liga_final.strip():
        etiqueta_liga = f" [{liga_final.strip()}]"

    avisar(f"resolviendo «{equipo_local}» vs «{equipo_visitante}»{etiqueta_liga}")
    from fuentes_xg import ejecutar_sync_seguro
    doc_a, meta_a = None, None
    try:
        doc_a, meta_a = ejecutar_sync_seguro(
            cargar_o_crear_auditoria, equipo_local, rival=equipo_visitante, **kwargs
        )
    except (RuntimeError, LookupError, Exception) as err_a:
        avisar(f"⚠️ Error cargando auditoría de local «{equipo_local}»: {err_a}")
        nom_fs_l = getattr(res_puente, "nombre_busqueda_flashscore", "") or getattr(res_puente, "equipo_local", "")
        if nom_fs_l and nom_fs_l != equipo_local:
            try:
                doc_a, meta_a = ejecutar_sync_seguro(
                    cargar_o_crear_auditoria, nom_fs_l, rival=equipo_visitante, **kwargs
                )
            except Exception as err_a2:
                avisar(f"⚠️ Fallback con nombre Flashscore local falló: {err_a2}")

    if not doc_a:
        doc_a = {
            "equipo": {"nombre": equipo_local, "pais_iso": res_puente.pais_local or "UNK"},
            "partidos": [],
            "identidad_verificada": False,
            "estado_identidad": "identidad_no_verificada",
            "motivo_identidad": f"No se pudo obtener datos para «{equipo_local}»",
        }
        meta_a = {}

    # PASO #2 OBLIGATORIO: Resolución cruzada por tabla y calendario del rival (League-Peer & Fixture Graph Resolver)
    ctx_rival_para_b: dict[str, Any] = dict(doc_a.get("equipo") or {})
    if doc_a.get("partidos"):
        ctx_rival_para_b["partidos"] = doc_a.get("partidos")
    if doc_a.get("partidos_futuros"):
        ctx_rival_para_b["partidos_futuros"] = doc_a.get("partidos_futuros")
    par_b = resolver_pareja_misma_liga(
        equipo_local,
        equipo_visitante,
        pais_iso=str(ctx_rival_para_b.get("pais_iso") or res_puente.pais_iso or ""),
        liga_pref=liga_final,
        doc_referencia=doc_a,
    )
    if par_b and par_b.get("nombre"):
        equipo_visitante = str(par_b["nombre"])
        if par_b.get("id"):
            ctx_rival_para_b.setdefault("pares_misma_liga", []).insert(0, par_b)

    # Si no se pasó cutoff explícito, detectar próximo cruce en calendario del local
    # para garantizar cero lookahead en doc_b y filtrar fugas en ambos documentos.
    if fecha_corte is None and cutoff_timestamp is None and id_partido_excluir is None:
        for pf_item in (doc_a.get("partidos_futuros") or []):
            if not isinstance(pf_item, dict):
                continue
            riv_nom = f"{pf_item.get('home_nombre', '')} {pf_item.get('away_nombre', '')}".strip().lower()
            if equipo_visitante.lower() in riv_nom:
                if pf_item.get("id_partido") and "id_partido_excluir" not in kwargs:
                    kwargs["id_partido_excluir"] = str(pf_item["id_partido"])
                if pf_item.get("fecha") and "fecha_corte" not in kwargs and "cutoff_timestamp" not in kwargs:
                    kwargs["fecha_corte"] = str(pf_item["fecha"])[:10]
                break

    doc_b, meta_b = None, None
    try:
        doc_b, meta_b = ejecutar_sync_seguro(
            cargar_o_crear_auditoria, equipo_visitante, rival=ctx_rival_para_b or equipo_local, **kwargs
        )
    except (RuntimeError, LookupError, Exception) as err_b:
        avisar(f"⚠️ Error cargando auditoría de visita «{equipo_visitante}»: {err_b}")
        nom_fs_v = getattr(res_puente, "nombre_busqueda_flashscore_visita", "") or getattr(res_puente, "equipo_visita", "")
        if nom_fs_v and nom_fs_v != equipo_visitante:
            try:
                doc_b, meta_b = ejecutar_sync_seguro(
                    cargar_o_crear_auditoria, nom_fs_v, rival=ctx_rival_para_b or equipo_local, **kwargs
                )
            except Exception as err_b2:
                avisar(f"⚠️ Fallback con nombre Flashscore visita falló: {err_b2}")

    if not doc_b:
        doc_b = {
            "equipo": {"nombre": equipo_visitante, "pais_iso": res_puente.pais_visita or "UNK"},
            "partidos": [],
            "identidad_verificada": False,
            "estado_identidad": "identidad_no_verificada",
            "motivo_identidad": f"No se pudo obtener datos para «{equipo_visitante}»",
        }
        meta_b = {}

    # PASO #3 OBLIGATORIO: Auditoría de coherencia de liga y rivales comunes antes de generar el ticket
    pais_iso_esperado = (
        res_puente.pais_iso
        or (liga_final.get("pais_iso") if isinstance(liga_final, dict) else "")
        or (doc_a.get("equipo") or {}).get("pais_iso")
        or "INT"
    )
    coherencia = validar_coherencia_competitiva_local_visita(
        doc_a, doc_b, pais_iso=str(pais_iso_esperado), liga_pref=liga_final
    )
    if not coherencia.get("coherente", True):
        avisar(f"⚠️ {coherencia.get('motivo')}")
        kwargs_reintento = {**kwargs, "forzar": True}
        if coherencia.get("falso_homonimo") == "visita":
            eliminar_cache_equipo(doc_b, pais_iso=str(pais_iso_esperado))
            eliminar_cache_equipo(equipo_visitante, pais_iso=str(pais_iso_esperado))
            ligas_permitidas = list(coherencia.get("ligas_local") or [])
            ctx_reintento_b = {
                **ctx_rival_para_b,
                "pais_iso": str(pais_iso_esperado),
                "liga_forzada": True,
                "keywords_liga": ligas_permitidas,
            }
            if ligas_permitidas and not ctx_reintento_b.get("liga"):
                ctx_reintento_b["liga"] = ligas_permitidas[0]
            par_reintento = resolver_pareja_misma_liga(
                equipo_local,
                equipo_visitante,
                pais_iso=str(pais_iso_esperado),
                liga_pref=ctx_reintento_b,
                doc_referencia=doc_a,
            )
            nom_reintento_b = str(par_reintento.get("nombre") if par_reintento else equipo_visitante)
            doc_b, meta_b = ejecutar_sync_seguro(
                cargar_o_crear_auditoria, nom_reintento_b, rival=ctx_reintento_b, **kwargs_reintento
            )
        elif coherencia.get("falso_homonimo") == "local":
            eliminar_cache_equipo(doc_a, pais_iso=str(pais_iso_esperado))
            eliminar_cache_equipo(equipo_local, pais_iso=str(pais_iso_esperado))
            ligas_permitidas = list(coherencia.get("ligas_visita") or [])
            ctx_reintento_a = {
                **dict(doc_b.get("equipo") or {}),
                "partidos": doc_b.get("partidos") or [],
                "partidos_futuros": doc_b.get("partidos_futuros") or [],
                "pais_iso": str(pais_iso_esperado),
                "liga_forzada": True,
                "keywords_liga": ligas_permitidas,
            }
            if ligas_permitidas and not ctx_reintento_a.get("liga"):
                ctx_reintento_a["liga"] = ligas_permitidas[0]
            par_reintento = resolver_pareja_misma_liga(
                equipo_visitante,
                equipo_local,
                pais_iso=str(pais_iso_esperado),
                liga_pref=ctx_reintento_a,
                doc_referencia=doc_b,
            )
            nom_reintento_a = str(par_reintento.get("nombre") if par_reintento else equipo_local)
            doc_a, meta_a = ejecutar_sync_seguro(
                cargar_o_crear_auditoria, nom_reintento_a, rival=ctx_reintento_a, **kwargs_reintento
            )
        # Re-evaluar coherencia tras el reintento: si persiste FALSO HOMÓNIMO EXTRANJERO,
        # se propaga en el contexto para abstener en preparar_analisis_mercados.
        coherencia = validar_coherencia_competitiva_local_visita(
            doc_a, doc_b, pais_iso=str(pais_iso_esperado), liga_pref=liga_final
        )

    nombre_a = (doc_a.get("equipo") or {}).get("nombre") or equipo_local
    nombre_b = (doc_b.get("equipo") or {}).get("nombre") or equipo_visitante
    pais_iso_a = (doc_a.get("equipo") or {}).get("pais_iso") or res_puente.pais_iso or (doc_a.get("equipo") or {}).get("liga") or ""
    pais_iso_b = (doc_b.get("equipo") or {}).get("pais_iso") or res_puente.pais_iso or (doc_b.get("equipo") or {}).get("liga") or ""
    liga_verif = (
        nombre_liga_txt
        or (doc_a.get("equipo") or {}).get("liga")
        or (doc_b.get("equipo") or {}).get("liga")
        or "Oficial"
    )
    avisar(formatear_mensaje_entidades_verificadas(nombre_a, str(pais_iso_a), nombre_b, str(pais_iso_b), str(liga_verif)))

    contexto = _metadatos_del_encuentro(doc_a, doc_b, nombre_a, nombre_b)
    contexto["coherencia_competitiva"] = dict(coherencia) if isinstance(coherencia, dict) else {}
    if not contexto.get("partido_ya_jugado"):
        resolver_hora_inicio_encuentro(
            contexto,
            equipo_local=nombre_a,
            equipo_visitante=nombre_b,
        )
    # Blindaje anti-fuga temporal sobre doc_a y doc_b antes de calcular patrones o cuotas
    eff_corte = cutoff_timestamp if cutoff_timestamp is not None else fecha_corte
    eff_excl = exclude_match_ids if exclude_match_ids is not None else id_partido_excluir
    if eff_corte is None and eff_excl is None and contexto.get("partido_activo"):
        eff_corte = contexto.get("fecha_partido")
        eff_excl = contexto.get("id_partido")
    if eff_corte is not None or eff_excl is not None:
        from auditoria_historica import _filtrar_partidos_doc_cutoff
        pts_a_f, fuga_a = _filtrar_partidos_doc_cutoff(
            doc_a.get("partidos") or [],
            fecha_corte=eff_corte if cutoff_timestamp is None else fecha_corte,
            cutoff_timestamp=cutoff_timestamp,
            id_partido_excluir=id_partido_excluir if exclude_match_ids is None else eff_excl,
            exclude_match_ids=exclude_match_ids if exclude_match_ids is not None else eff_excl,
        )
        if fuga_a:
            doc_a = dict(doc_a)
            doc_a["partidos"] = pts_a_f
        pts_b_f, fuga_b = _filtrar_partidos_doc_cutoff(
            doc_b.get("partidos") or [],
            fecha_corte=eff_corte if cutoff_timestamp is None else fecha_corte,
            cutoff_timestamp=cutoff_timestamp,
            id_partido_excluir=id_partido_excluir if exclude_match_ids is None else eff_excl,
            exclude_match_ids=exclude_match_ids if exclude_match_ids is not None else eff_excl,
        )
        if fuga_b:
            doc_b = dict(doc_b)
            doc_b["partidos"] = pts_b_f
    if isinstance(liga_final, dict) and liga_final.get("nombre"):
        if res_puente.es_copa or liga_final.get("liga_forzada") or not contexto.get("competicion"):
            contexto["competicion"] = liga_final["nombre"]
    elif isinstance(liga_final, str) and liga_final.strip():
        if res_puente.es_copa or not contexto.get("competicion"):
            contexto["competicion"] = liga_final.strip()

    # Detección y advertencia visible de liga discrepante (A3/B2)
    liga_decl_raw = (nombre_liga_txt or (liga_final.get("nombre") if isinstance(liga_final, dict) else str(liga_final or ""))).strip()
    torneos_reales = [str(p.get("torneo") or "").strip() for p in (doc_a.get("partidos") or []) if p.get("torneo")]
    liga_partidos = torneos_reales[0] if torneos_reales else ""
    from fuentes_xg import _es_region_geografica
    _cands_liga_det = [
        liga_partidos,
        (doc_a.get("equipo") or {}).get("liga"),
        getattr(res_puente, "liga", None),
        contexto.get("competicion"),
    ]
    liga_det_raw = next(
        (str(x).strip().rstrip(":").strip() for x in _cands_liga_det if str(x or "").strip() and not _es_region_geografica(str(x))),
        "",
    )
    liga_decl_limpia = liga_decl_raw.rstrip(":").strip()
    if liga_decl_limpia and liga_det_raw and not son_ligas_equivalentes(liga_decl_limpia, liga_det_raw):
        adv_liga = f"Declarada: {liga_decl_limpia} | Detectada: {liga_det_raw}"
        avisar(f"⚠️ Advertencia de Liga: {adv_liga}")
        contexto["advertencia_liga"] = adv_liga

    avisar(f"condición: {nombre_a}={contexto.get('condicion_local')} · "
           f"{nombre_b}={contexto.get('condicion_visitante')} "
           f"({contexto.get('evidencia_condicion')})")

    # Sincronización post-descarga de identidad verificada:
    # Si res_puente no verificó identidad o si los documentos demuestran identidad fehaciente
    # (por coincidir con el catálogo de selecciones FIFA o por contar con >= 15 partidos e ID de proveedor):
    from resolvedor_entidades import obtener_seleccion_nacional
    id_prov_a = (doc_a.get("equipo") or {}).get("id") or (meta_a or {}).get("id_equipo")
    id_prov_b = (doc_b.get("equipo") or {}).get("id") or (meta_b or {}).get("id_equipo")
    n_partidos_a = len(doc_a.get("partidos") or [])
    n_partidos_b = len(doc_b.get("partidos") or [])

    es_sel_a = bool(obtener_seleccion_nacional(nombre_a) or obtener_seleccion_nacional(equipo_local))
    es_sel_b = bool(obtener_seleccion_nacional(nombre_b) or obtener_seleccion_nacional(equipo_visitante))

    verificado_post_a = bool(es_sel_a or (id_prov_a and n_partidos_a >= 15))
    verificado_post_b = bool(es_sel_b or (id_prov_b and n_partidos_b >= 15))

    estado_identidad_final = res_puente.estado_identidad
    identidad_verificada_final = res_puente.identidad_verificada
    motivo_identidad_final = res_puente.motivo_identidad

    if not identidad_verificada_final:
        if (verificado_post_a and verificado_post_b and n_partidos_a > 0 and n_partidos_b > 0) or (es_sel_a and es_sel_b and n_partidos_a > 0 and n_partidos_b > 0):
            identidad_verificada_final = True
            estado_identidad_final = "verificada"
            motivo_identidad_final = "Identidad verificada por catálogo de selecciones FIFA o descarga de historial oficial con ID verificable."
            contexto["identidad_verificada"] = True
            contexto["estado_identidad"] = "verificada"

    # Si algún equipo falló fehacientemente o no tiene partidos suficientes
    if doc_a.get("identidad_verificada") is False or doc_b.get("identidad_verificada") is False or n_partidos_a == 0 or n_partidos_b == 0:
        identidad_verificada_final = False
        estado_identidad_final = "identidad_no_verificada"
        motivo_identidad_final = (
            doc_a.get("motivo_identidad") or doc_b.get("motivo_identidad") or
            f"Historial insuficiente: local con {n_partidos_a} partidos y visita con {n_partidos_b} partidos."
        )
        contexto["identidad_verificada"] = False
        contexto["estado_identidad"] = "identidad_no_verificada"

    return {
        "equipo_local": nombre_a,
        "equipo_visitante": nombre_b,
        "doc_local": doc_a,
        "doc_visitante": doc_b,
        "meta_local": meta_a,
        "meta_visitante": meta_b,
        "contexto": contexto,
        "liga_solicitada": liga_final,
        "estado_identidad": estado_identidad_final,
        "motivo_identidad": motivo_identidad_final,
        "identidad_verificada": identidad_verificada_final,
        "criterio_desempate": res_puente.criterio_desempate,
        "nivel_confianza": res_puente.nivel_confianza,
        "ambiguedad_condicion": (contexto.get("condicion_local") is None
                                 or contexto.get("condicion_visitante") is None),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ANÁLISIS + INFORME
# ═══════════════════════════════════════════════════════════════════════════════

# Firma del MOTOR de probabilidades. Entra en el id del trabajo para que un cambio
# de motor (hoy: la Red Neuronal Recursiva de 45 variables) no reutilice el id —ni
# el snapshot de pronóstico— de una predicción hecha con el motor anterior. Sin
# esto, el snapshot nuevo se descartaba por "ya existe" y la evaluación seguía
# midiendo las probabilidades viejas.
FIRMA_MOTOR = "red-neuronal-recursiva-45-r24"


def calcular_firma_datos(doc_local: dict[str, Any] | None, doc_visitante: dict[str, Any] | None) -> str:
    """Calcula la firma determinista SHA-256 corta (12 hex) sobre los historiales cargados."""
    dl = doc_local if isinstance(doc_local, dict) else {}
    dv = doc_visitante if isinstance(doc_visitante, dict) else {}
    return hashlib.sha256(
        json.dumps(
            [
                len(dl.get("partidos", []) or []),
                len(dv.get("partidos", []) or []),
                dl.get("generado_en"),
                dv.get("generado_en"),
            ],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]


def calcular_calidad_fuente_club(doc: dict[str, Any] | None) -> dict[str, int]:
    """
    Calcula métricas de calidad de fuente para un club sobre sus 15 partidos:
    - `n_claves_no_nulas`: número de claves estadísticas distintas con al menos un valor no nulo.
    - `n_eventos_gol`: número total de eventos de gol (con minuto/cronología) registrados.
    """
    if not isinstance(doc, dict):
        return {"n_claves_no_nulas": 0, "n_eventos_gol": 0}
    claves_no_nulas: set[str] = set()
    n_eventos_gol = 0
    for p in (doc.get("partidos") or [])[:15]:
        if not isinstance(p, dict):
            continue
        periodos = p.get("periodos") or {}
        if isinstance(periodos, dict):
            for per_val in periodos.values():
                filas = (per_val.get("filas") or []) if isinstance(per_val, dict) else []
                for fila in filas:
                    if not isinstance(fila, dict):
                        continue
                    cl = str(fila.get("clave") or "").strip()
                    if cl and (fila.get("equipo") is not None or fila.get("rival") is not None):
                        claves_no_nulas.add(cl)
        for k_ev in ("goles_eventos", "eventos_gol", "goles", "minutos_gol"):
            evs = p.get(k_ev)
            if isinstance(evs, list) and evs:
                n_eventos_gol += len(evs)
                break
    return {
        "n_claves_no_nulas": len(claves_no_nulas),
        "n_eventos_gol": int(n_eventos_gol),
    }


def extraer_fuentes_y_firma_datos(
    doc_local: dict[str, Any] | None,
    doc_visitante: dict[str, Any] | None,
) -> dict[str, Any]:
    """Devuelve fuente por club (`fuente_local`, `fuente_visitante`), `firma_datos` y calidad por club."""
    dl = doc_local if isinstance(doc_local, dict) else {}
    dv = doc_visitante if isinstance(doc_visitante, dict) else {}
    f_loc = str(dl.get("fuente") or "").strip() or None
    f_vis = str(dv.get("fuente") or "").strip() or None
    return {
        "fuente_local": f_loc,
        "fuente_visitante": f_vis,
        "firma_datos": calcular_firma_datos(dl, dv),
        "calidad_fuente_local": calcular_calidad_fuente_club(dl),
        "calidad_fuente_visitante": calcular_calidad_fuente_club(dv),
    }


# Regla operativa sin validación empírica (Ronda 22, Bloque 2):
# Si el partido más reciente de los 15 de cualquiera de los clubes supera N=60 días
# de antigüedad respecto a la fecha de referencia, el sistema se abstiene por
# "datos_desactualizados" en lugar de proyectar con muestras obsoletas.
MAX_DIAS_ANTIGUEDAD_ULTIMO_PARTIDO = 60
ETIQUETA_REGLA_ANTIGUEDAD = "regla operativa sin validación"


def _ultima_fecha_iso_partidos(doc: dict[str, Any] | None) -> str | None:
    """Extrae la fecha ISO YYYY-MM-DD más reciente de los 15 partidos de un documento."""
    if not isinstance(doc, dict):
        return None
    fechas: list[str] = []
    for p in (doc.get("partidos") or [])[:15]:
        if not isinstance(p, dict):
            continue
        f_raw = str(p.get("fecha") or "").strip()[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f_raw):
            fechas.append(f_raw)
    return max(fechas) if fechas else None


def extraer_fechas_datos_hasta(
    doc_local: dict[str, Any] | None = None,
    doc_visitante: dict[str, Any] | None = None,
    metadatos: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    """Devuelve última fecha de partido por club y etiqueta consolidada `datos_hasta`."""
    meta = metadatos if isinstance(metadatos, dict) else {}
    f_l = str(meta.get("datos_hasta_local") or "").strip()[:10] or _ultima_fecha_iso_partidos(doc_local)
    f_v = str(meta.get("datos_hasta_visitante") or "").strip()[:10] or _ultima_fecha_iso_partidos(doc_visitante)
    if f_l and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f_l):
        f_l = None
    if f_v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f_v):
        f_v = None
    if f_l and f_v:
        etq = f_l if f_l == f_v else f"{f_l} / {f_v}"
    elif f_l or f_v:
        etq = f_l or f_v
    else:
        etq_m = str(meta.get("datos_hasta") or "").strip()
        etq = etq_m or None
    return {
        "datos_hasta_local": f_l,
        "datos_hasta_visitante": f_v,
        "datos_hasta": etq,
    }


def evaluar_antiguedad_datos(
    doc_local: dict[str, Any] | None,
    doc_visitante: dict[str, Any] | None,
    *,
    fecha_referencia: str | None = None,
    max_dias: int = MAX_DIAS_ANTIGUEDAD_ULTIMO_PARTIDO,
) -> dict[str, Any]:
    """
    Guarda de antigüedad (regla operativa sin validación):
    si el último partido de los 15 de algún club es más viejo que `max_dias` días
    respecto a `fecha_referencia` (YYYY-MM-DD), devuelve `desactualizado=True` con
    estado `datos_desactualizados`.
    """
    from datetime import date as _date

    ref_date: _date
    f_ref_s = str(fecha_referencia or "").strip()[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f_ref_s):
        try:
            ref_date = _date.fromisoformat(f_ref_s)
        except Exception:
            ref_date = datetime.now(timezone.utc).date()
    else:
        ref_date = datetime.now(timezone.utc).date()

    fechas = extraer_fechas_datos_hasta(doc_local, doc_visitante)
    f_l = fechas["datos_hasta_local"]
    f_v = fechas["datos_hasta_visitante"]
    dias_l = (ref_date - _date.fromisoformat(f_l)).days if f_l else None
    dias_v = (ref_date - _date.fromisoformat(f_v)).days if f_v else None

    _eq_l_raw = (doc_local or {}).get("equipo") if isinstance(doc_local, dict) else None
    eq_l = str(_eq_l_raw.get("nombre") if isinstance(_eq_l_raw, dict) else (_eq_l_raw or "Local"))
    _eq_v_raw = (doc_visitante or {}).get("equipo") if isinstance(doc_visitante, dict) else None
    eq_v = str(_eq_v_raw.get("nombre") if isinstance(_eq_v_raw, dict) else (_eq_v_raw or "Visitante"))
    motivos: list[str] = []
    if dias_l is not None and dias_l > max_dias:
        motivos.append(f"{eq_l}: último partido {f_l} ({dias_l} días de antigüedad > {max_dias} días)")
    if dias_v is not None and dias_v > max_dias:
        motivos.append(f"{eq_v}: último partido {f_v} ({dias_v} días de antigüedad > {max_dias} días)")

    desact = bool(motivos)
    motivo = (
        f"Datos desactualizados ({ETIQUETA_REGLA_ANTIGUEDAD}, umbral N={max_dias} días): "
        + "; ".join(motivos)
        if desact
        else ""
    )
    return {
        "desactualizado": desact,
        "estado": "datos_desactualizados" if desact else "ok",
        "motivo": motivo,
        "max_dias": int(max_dias),
        "etiqueta_regla": ETIQUETA_REGLA_ANTIGUEDAD,
        "fecha_referencia": ref_date.isoformat(),
        "datos_hasta_local": f_l,
        "datos_hasta_visitante": f_v,
        "datos_hasta": fechas["datos_hasta"],
        "dias_local": dias_l,
        "dias_visitante": dias_v,
    }


def formatear_fuentes_clubes_encabezado(
    metadatos: dict[str, Any] | None = None,
    doc_local: dict[str, Any] | None = None,
    doc_visitante: dict[str, Any] | None = None,
    *,
    equipo_local: str | None = None,
    equipo_visitante: str | None = None,
) -> str:
    """Devuelve el texto de fuente por club y fecha 'Datos hasta' para encabezados."""
    meta = metadatos if isinstance(metadatos, dict) else {}
    dl = doc_local if isinstance(doc_local, dict) else {}
    dv = doc_visitante if isinstance(doc_visitante, dict) else {}
    eq_l = str(equipo_local or meta.get("equipo_local") or (dl.get("equipo") or {}).get("nombre") or "Local").strip()
    eq_v = str(equipo_visitante or meta.get("equipo_visitante") or (dv.get("equipo") or {}).get("nombre") or "Visitante").strip()
    f_l = str(meta.get("fuente_local") or dl.get("fuente") or "").strip()
    f_v = str(meta.get("fuente_visitante") or dv.get("fuente") or "").strip()
    partes: list[str] = []
    if f_l or f_v:
        partes.append(f"Fuentes: {eq_l}: {f_l or 'sin_dato'} · {eq_v}: {f_v or 'sin_dato'}")
        if f_l.lower() == "flashscore" or f_v.lower() == "flashscore":
            partes.append("datos reducidos")
    fechas_dh = extraer_fechas_datos_hasta(dl, dv, meta)
    if fechas_dh.get("datos_hasta"):
        partes.append(f"Datos hasta: {fechas_dh['datos_hasta']}")
    return " · ".join(partes)


def formatear_encabezado_partido_y_fuentes_telegram(
    metadatos: dict[str, Any] | None = None,
    doc_local: dict[str, Any] | None = None,
    doc_visitante: dict[str, Any] | None = None,
    *,
    equipo_local: str | None = None,
    equipo_visitante: str | None = None,
) -> tuple[str, str]:
    """Devuelve `(linea_partido, linea_fuentes)` consistentes para textos de Telegram."""
    meta = metadatos if isinstance(metadatos, dict) else {}
    dl = doc_local if isinstance(doc_local, dict) else {}
    dv = doc_visitante if isinstance(doc_visitante, dict) else {}
    partido_etq = formatear_etiqueta_fecha_encabezado(meta)
    if not partido_etq.startswith("Partido:"):
        partido_etq = f"Partido: {partido_etq}"
    fuentes_etq = formatear_fuentes_clubes_encabezado(
        meta, dl, dv, equipo_local=equipo_local, equipo_visitante=equipo_visitante
    )
    if not fuentes_etq:
        eq_l = str(equipo_local or meta.get("equipo_local") or (dl.get("equipo") or {}).get("nombre") or "Local").strip()
        eq_v = str(equipo_visitante or meta.get("equipo_visitante") or (dv.get("equipo") or {}).get("nombre") or "Visitante").strip()
        f_gen = str(meta.get("fuente") or "").strip()
        val_def = f_gen if f_gen else "sin_dato"
        fuentes_etq = f"Fuentes: {eq_l}: {val_def} · {eq_v}: {val_def}"
    return partido_etq, fuentes_etq


def identificar_trabajo(analisis_metadatos: dict[str, Any], doc_local: dict[str, Any],
                        doc_visitante: dict[str, Any]) -> str:
    """
    Identificador estable del trabajo: partido + fecha + versión de datos + versión
    del modelo + firma del motor. Sirve para no repetir envíos ya confirmados y
    para distinguir un reintento técnico de una nueva solicitud explícita.
    """
    firma_datos = calcular_firma_datos(doc_local, doc_visitante)
    base = "|".join([
        str(analisis_metadatos.get("equipo_local", "")).lower(),
        str(analisis_metadatos.get("equipo_visitante", "")).lower(),
        str(analisis_metadatos.get("fecha_partido") or "sin_fecha"),
        firma_datos,
        f"{NOMBRE_MODELO}-{VERSION_MODELO}",
        FIRMA_MOTOR,
    ])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


import threading

_lock_registro = threading.Lock()


def _leer_registro() -> dict[str, Any]:
    with _lock_registro:
        try:
            with open(REGISTRO_ENVIOS, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"envios": {}}


def _escribir_registro(registro: dict[str, Any]) -> None:
    with _lock_registro:
        os.makedirs(os.path.dirname(REGISTRO_ENVIOS), exist_ok=True)
        # Poda automática: conservar como máximo los últimos 100 trabajos para evitar inflación de disco
        envios = registro.get("envios", {})
        if len(envios) > 100:
            claves = sorted(envios.keys(), key=lambda k: envios[k].get("actualizado_en", ""))
            for k in claves[:-100]:
                envios.pop(k, None)
        with open(REGISTRO_ENVIOS, "w", encoding="utf-8") as f:
            json.dump(registro, f, ensure_ascii=False, indent=1)


def estado_envio_previo(id_trabajo: str) -> dict[str, Any] | None:
    return (_leer_registro().get("envios") or {}).get(id_trabajo)


def marcar_estado(id_trabajo: str, estado: str, extra: dict[str, Any] | None = None) -> None:
    registro = _leer_registro()
    entrada = registro.setdefault("envios", {}).setdefault(id_trabajo, {"historial": []})
    entrada["estado"] = estado
    entrada["actualizado_en"] = _ahora()
    if extra:
        extra_limpio = dict(extra)
        # Higiene de datos: evitar almacenar textos masivos de mensajes en el registro JSON
        if "mensaje" in extra_limpio and isinstance(extra_limpio["mensaje"], str):
            extra_limpio["mensaje_preview"] = extra_limpio["mensaje"][:160] + "…"
            extra_limpio.pop("mensaje", None)
        entrada.update(extra_limpio)
    entrada["historial"].append({"estado": estado, "en": entrada["actualizado_en"]})
    _escribir_registro(registro)


# ═══════════════════════════════════════════════════════════════════════════════
# RENDERIZADO DE TICKET GRÁFICO (PNG) PARA /mercados
# ═══════════════════════════════════════════════════════════════════════════════

_RUTAS_FUENTES_TICKET = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/verdana.ttf", "C:/Windows/Fonts/verdanab.ttf"),
)


def _cargar_fuentes_ticket(tamano: int = 15, bold: bool = False):
    """Devuelve fuente truetype instalada en el sistema o fallback de PIL."""
    from PIL import ImageFont
    for normal, negrita in _RUTAS_FUENTES_TICKET:
        ruta = negrita if bold else normal
        try:
            if os.path.exists(ruta):
                return ImageFont.truetype(ruta, tamano)
        except Exception:
            continue
    return ImageFont.load_default()


def _sanear_para_canvas(texto: str) -> str:
    """Elimina emojis Unicode no soportados por las fuentes del canvas para erradicar rectángulos rotos."""
    if not texto:
        return ""
    t = (
        texto.replace("🎯 JUGADA ÓPTIMA TE APUESTO:", ">> JUGADA OPTIMA TE APUESTO:")
             .replace("🎯 JUGADA OPTIMA TE APUESTO:", ">> JUGADA OPTIMA TE APUESTO:")
             .replace("🎯 JUGADA ÓPTIMA:", ">> JUGADA OPTIMA:")
             .replace("🎯 JUGADA OPTIMA:", ">> JUGADA OPTIMA:")
             .replace("🎯", ">>")
             .replace("⚖️ DUELO PAREJO / RIESGO DE EMPATE", "[DUELO PAREJO / RIESGO DE EMPATE]")
             .replace("⚖ DUELO PAREJO / RIESGO DE EMPATE", "[DUELO PAREJO / RIESGO DE EMPATE]")
             .replace("⚖️", "[ALERTA]")
             .replace("⚖", "[ALERTA]")
             .replace("🔥", "[RACHA]")
             .replace("⚡", "[VELOCIDAD]")
             .replace("⚠️", "[!]")
             .replace("🛡️", "[DEF]")
             .replace("🛡", "[DEF]")
             .replace("⊗", "x")
             .replace("★", "*")
             .replace("☆", "*")
    )
    # Filtrar cualquier caracter de bloque de emojis o símbolos gráficos no admitidos
    t = "".join(c for c in t if ord(c) < 0x2600 or c in "•·→←—–")
    return t.strip()


ORDEN_CATEGORIAS_SECCION1 = [
    "GOLES PARTIDO",
    "GOLES POR EQUIPO",
    "CÓRNERES",
    "TARJETAS",
    "HÁNDICAP / 1X2 / DC",
]


def _val_sel(obj: Any, clave: str, defecto: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(clave, defecto)
    return getattr(obj, clave, defecto)


def clasificar_categoria_ambito_seccion1(s: Any) -> tuple[str, str]:
    m_l = str(_val_sel(s, "mercado") or "").lower()
    e_l = str(_val_sel(s, "evento") or "").lower()
    eq_raw = _val_sel(s, "equipo", None)
    eq = str(eq_raw).strip() if eq_raw and str(eq_raw).strip() not in ("None", "Total", "null", "") else None
    per = str(_val_sel(s, "periodo") or "FT").strip().upper()
    if per not in ("FT", "1T", "2T"):
        per = "FT"
    per_nombre = "Tiempo Completo (FT)" if per == "FT" else ("1er Tiempo (1T)" if per == "1T" else "2º Tiempo (2T)")

    if any(k in m_l for k in ("corner", "córner", "crner", "esquina")) or "corner" in e_l:
        cat = "CÓRNERES"
        amb = f"{eq} · Total ({per})" if (eq and per == "FT") else (f"{eq} · {per}" if eq else per_nombre)
        return cat, amb

    if any(k in m_l for k in ("tarjeta", "amarilla", "roja")) or any(k in e_l for k in ("tarjeta", "amarilla", "roja")):
        cat = "TARJETAS"
        amb = f"{eq} · Total ({per})" if (eq and per == "FT") else (f"{eq} · {per}" if eq else per_nombre)
        return cat, amb

    if any(k in m_l for k in ("hándicap", "handicap", "doble oportunidad", "sin empate", "ganador", "1x2", "1t/ft", "descanso / final")) or any(k in e_l for k in ("1x", "x2", "12", "dnb", "local_gana", "visitante_gana", "empate")):
        cat = "HÁNDICAP / 1X2 / DC"
        amb = per_nombre
        return cat, amb

    if eq:
        cat = "GOLES POR EQUIPO"
        amb = f"{eq} · Total ({per})" if per == "FT" else f"{eq} · {per}"
        return cat, amb

    return "GOLES PARTIDO", per_nombre


def formatear_pildora_seccion1(s: Any, cat: str, eq_l: str, eq_v: str) -> tuple[str, str, float]:
    ev = str(_val_sel(s, "evento") or "").lower()
    et = str(_val_sel(s, "etiqueta") or "")
    lin = _val_sel(s, "linea", None)
    prob = float(_val_sel(s, "probabilidad", 0.0) or 0.0)
    pct = f"{prob:.0%}"

    try:
        from decision_cobertura import bandera_activa as _ba_cal_s1
        if _ba_cal_s1("usar_calibracion_y_veto_conciencia"):
            p_cal_att = _val_sel(s, "prob_calibrada", None)
            vig_att = bool(_val_sel(s, "bajo_vigilancia", False) or _val_sel(s, "veto_top5", False))
            if p_cal_att is None:
                from agente_consciente import evaluar_familia_conciencia_cached
                info_c = evaluar_familia_conciencia_cached(str(_val_sel(s, "mercado") or ""), equipo=_val_sel(s, "equipo", None), prob_bruta=prob)
                p_cal_att = info_c.get("prob_calibrada")
                vig_att = bool(info_c.get("bajo_vigilancia") or info_c.get("veto_top5"))
            if vig_att and p_cal_att is not None:
                pct = f"{prob:.0%} (cal {float(p_cal_att):.0%})"
    except Exception:
        pass

    eq_raw_p = _val_sel(s, "equipo", None)
    eq_p = str(eq_raw_p).strip() if eq_raw_p and str(eq_raw_p).strip() not in ("None", "Total", "null", "") else None
    per_p = str(_val_sel(s, "periodo") or "FT").strip().upper()
    if per_p not in ("FT", "1T", "2T"):
        per_p = "FT"

    if cat == "GOLES POR EQUIPO":
        if lin is not None and abs(float(lin) - 0.5) < 1e-6 and ("under_" in ev or "menos de" in et.lower()):
            return "", "", 0.0
        eq_nombre = eq_p if eq_p else eq_l
        if "over_" in ev or "mas de" in et.lower() or "más de" in et.lower():
            lbl = f"{eq_nombre} +{lin:g} Goles ({per_p})" if lin is not None else f"{eq_nombre} {et}"
        elif "under_" in ev or "menos de" in et.lower():
            lbl = f"{eq_nombre} -{lin:g} Goles ({per_p})" if lin is not None else f"{eq_nombre} {et}"
        elif "al_menos_" in ev or "al menos" in et.lower():
            if lin is not None and isinstance(lin, (int, float)):
                lbl = f"{eq_nombre} +{float(lin) - 0.5:g} Goles ({per_p})"
            else:
                lbl = f"{eq_nombre} {et}"
        elif "primer_gol" in ev:
            lbl = f"1er Gol: {eq_nombre}"
        else:
            lbl = f"{eq_nombre} {et}"
        return lbl, pct, prob

    if "GOL" in cat:
        if lin is not None and abs(float(lin) - 0.5) < 1e-6 and ("under_" in ev or "menos de" in et.lower()):
            return "", "", 0.0
        suf_p = f" ({per_p})" if per_p in ("1T", "2T") else ""
        if ev == "gol_1t" or "al menos un gol en el 1t" in et.lower():
            lbl = "+0.5 Goles (1T)"
        elif ev == "gol_2t" or "al menos un gol en el 2t" in et.lower():
            lbl = "+0.5 Goles (2T)"
        elif "over_" in ev or "mas de" in et.lower() or "más de" in et.lower():
            lbl = f"+{lin:g} Goles{suf_p}" if lin is not None else et
        elif "under_" in ev or "menos de" in et.lower():
            lbl = f"-{lin:g} Goles{suf_p}" if lin is not None else et
        elif "al_menos_" in ev or "al menos" in et.lower():
            if lin is not None and isinstance(lin, (int, float)):
                lbl = f"+{float(lin) - 0.5:g} Goles{suf_p}"
            else:
                lbl = et
        elif "primer_gol" in ev:
            lbl = f"1er Gol: {eq_p or eq_l}"
        elif "ambos_marcan" in ev or "btts" in ev:
            lbl = "Marcan Ambos" if ("si" in ev or "yes" in ev) else "No Marcan Ambos"
        else:
            lbl = et
    elif "CORNER" in cat or "CRNER" in cat or "CÓRNER" in cat:
        if lin is not None and float(lin) <= 0.5 and ("under_" in ev or "menos" in et.lower()):
            return "", "", 0.0
        pref_c = eq_p if eq_p else "Total"
        if "over_" in ev or "mas de" in et.lower() or "más" in et.lower():
            lbl = f"{pref_c} +{lin:g} Córn." if lin is not None else et
        elif "under_" in ev or "menos" in et.lower():
            lbl = f"{pref_c} -{lin:g} Córn." if lin is not None else et
        else:
            lbl = et
        return lbl, pct, prob
    elif "TARJETA" in cat:
        if lin is not None and float(lin) <= 0.5 and ("under_" in ev or "menos" in et.lower()):
            return "", "", 0.0
        if "roja" in ev or "roja" in et.lower():
            lbl = f"+{lin:g} Roja" if lin is not None else "Tarjeta Roja"
        elif "over_" in ev or "mas de" in et.lower() or "mas" in et.lower():
            lbl = f"+{lin:g} Tarj." if lin is not None else et
        elif "under_" in ev or "menos" in et.lower():
            lbl = f"-{lin:g} Tarj." if lin is not None else et
        else:
            lbl = et
    else:
        if ev in ("1", "local_gana"):
            lbl = f"{eq_l} Gana"
        elif ev in ("2", "visitante_gana"):
            lbl = f"{eq_v} Gana"
        elif ev in ("x", "empate"):
            lbl = "Empate"
        elif ev == "1x":
            lbl = f"1X ({eq_l} o X)"
        elif ev == "x2":
            lbl = f"X2 (X o {eq_v})"
        elif ev == "12":
            lbl = "12 (Sin Empate)"
        elif "dnb" in ev:
            lbl = f"{_val_sel(s, 'equipo') or eq_l} DNB"
        elif "h0_" in ev:
            lbl = f"H0 {_val_sel(s, 'equipo') or eq_l}"
        elif "h1_" in ev:
            lbl = f"H+1 {_val_sel(s, 'equipo') or eq_l}"
        elif "h2_" in ev:
            lbl = f"H+2 {_val_sel(s, 'equipo') or eq_l}"
        elif "h3_" in ev:
            lbl = f"H+3 {_val_sel(s, 'equipo') or eq_l}"
        elif "h_minus1_" in ev or "h-1_" in ev:
            lbl = f"H-1 {_val_sel(s, 'equipo') or eq_l}"
        else:
            lbl = et

    for eq_nom in (eq_l, eq_v):
        if lbl.startswith(eq_nom + ":"):
            lbl = lbl[len(eq_nom) + 1:].strip()
        elif lbl.startswith(eq_nom + " - "):
            lbl = lbl[len(eq_nom) + 3:].strip()
    return lbl, pct, prob


def calcular_seccion1_unificada(
    analisis: Any,
    metadatos: dict[str, Any] | None = None,
    umbral: float | None = None,
    *,
    sec1_data: list[Any] | None = None,
    eq_l: str | None = None,
    eq_v: str | None = None,
    matriz_prob: list[list[float]] | None = None,
    top5_podio: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Fuente única de verdad compartida entre Telegram/Ticket y Súper-Cuadro para:
      - Top 5 Podio
      - Sección 1 (`estado == 'mantiene'` + Punto 27 + verificación `p_1x2`)
      - Sección 2 (`estado != 'mantiene'`)
      - Conteo exacto `total_validadas` (sin truncar píldoras ni contar placeholders).
    """
    meta = metadatos or _val_sel(analisis, "metadatos", {}) or {}
    params = _val_sel(analisis, "parametros", None)
    umbral_val = umbral if umbral is not None else _val_sel(params, "umbral", 0.70)
    if isinstance(umbral_val, (int, float)) and umbral_val > 1.0:
        umbral_val = umbral_val / 100.0
    umbral_val = float(umbral_val or 0.70)

    if not eq_l:
        eq_l_raw = meta.get("equipo_local") or "Local"
        eq_l = str(eq_l_raw.get("nombre") or eq_l_raw.get("nombre_oficial") or "Local") if isinstance(eq_l_raw, dict) else str(eq_l_raw)
    if not eq_v:
        eq_v_raw = meta.get("equipo_visitante") or "Visitante"
        eq_v = str(eq_v_raw.get("nombre") or eq_v_raw.get("nombre_oficial") or "Visitante") if isinstance(eq_v_raw, dict) else str(eq_v_raw)

    if sec1_data is not None:
        sels_raw = list(sec1_data)
    else:
        sels_raw = list(_val_sel(analisis, "selecciones_filtradas", []) or [])
        if not sels_raw and analisis is not None and hasattr(analisis, "grupos"):
            g = getattr(analisis, "grupos", {}) or {}
            sels_raw = list(g.get("mantiene", []) or []) + list(g.get("sensible", []) or [])
        if not sels_raw and analisis is not None and hasattr(analisis, "selecciones_validadas"):
            sels_raw = list(getattr(analisis, "selecciones_validadas", []) or [])

    motor = getattr(analisis, "motor", None) if analisis is not None else None
    bloquear_corners = False
    bloquear_tarjetas = False
    if motor and hasattr(motor, "_mu_corners"):
        try:
            mu_cl = motor._mu_corners("local")
            mu_cv = motor._mu_corners("visitante")
            ml_g = motor.muestras.get("local_general").metricas.get("corners_favor") if getattr(motor, "muestras", None) else None
            mv_g = motor.muestras.get("visitante_general").metricas.get("corners_favor") if getattr(motor, "muestras", None) else None
            p_ceros_l = ml_g.pct_ceros if (ml_g and hasattr(ml_g, "pct_ceros")) else 0.0
            p_ceros_v = mv_g.pct_ceros if (mv_g and hasattr(mv_g, "pct_ceros")) else 0.0
            if mu_cl is None or mu_cv is None or (mu_cl + mu_cv) < 1.5 or p_ceros_l > 0.30 or p_ceros_v > 0.30:
                bloquear_corners = True
        except Exception:
            pass
    if motor and hasattr(motor, "_mu_tarjetas"):
        try:
            mu_am = motor._mu_tarjetas("amarillas")
            ml_am = motor.muestras.get("local_general").metricas.get("amarillas_favor") if getattr(motor, "muestras", None) else None
            mv_am = motor.muestras.get("visitante_general").metricas.get("amarillas_favor") if getattr(motor, "muestras", None) else None
            p_c_l = ml_am.pct_ceros if (ml_am and hasattr(ml_am, "pct_ceros")) else 0.0
            p_c_v = mv_am.pct_ceros if (mv_am and hasattr(mv_am, "pct_ceros")) else 0.0
            if mu_am is None or mu_am < 0.5 or p_c_l > 0.30 or p_c_v > 0.30:
                bloquear_tarjetas = True
        except Exception:
            pass

    from motor_mercados import (
        es_seleccion_competitiva_ticket,
        es_seleccion_vetada_por_micropatrones,
        filtrar_top_opciones_ticket,
        seleccionar_top_5_jugadas_recomendadas,
    )

    mat_ref = matriz_prob or (getattr(motor, "matriz_ft", None) if motor is not None else None) or (getattr(analisis, "matriz_ft", None) if analisis is not None else None)
    p_1x2 = None
    if mat_ref:
        try:
            R = len(mat_ref)
            C = len(mat_ref[0]) if R else 0
            p1 = sum(float(mat_ref[i][j]) for i in range(R) for j in range(C) if i > j)
            px = sum(float(mat_ref[i][j]) for i in range(R) for j in range(C) if i == j)
            p2 = sum(float(mat_ref[i][j]) for i in range(R) for j in range(C) if i < j)
            p_1x2 = (p1, px, p2)
        except Exception:
            p_1x2 = None

    def _permitida_sel(s: Any) -> bool:
        cat_s, _ = clasificar_categoria_ambito_seccion1(s)
        if bloquear_corners and cat_s == "CÓRNERES":
            return False
        if bloquear_tarjetas and cat_s == "TARJETAS":
            return False
        if sec1_data is None:
            if cat_s == "TARJETAS" and getattr(motor, "hay_tension_asedio_tarjetas", False):
                ev_s = str(_val_sel(s, "evento") or "").lower()
                et_s = str(_val_sel(s, "etiqueta") or "").lower()
                if "under" in ev_s or "menos" in et_s or "-" in et_s:
                    return False
            if es_seleccion_vetada_por_micropatrones(s, motor=motor, eq_l=eq_l, eq_v=eq_v)[0]:
                return False
        return es_seleccion_competitiva_ticket(s, eq_l, eq_v, p_1x2, motor=motor)

    sels_validas = [
        s for s in sels_raw
        if float(_val_sel(s, "probabilidad") or 0.0) >= (umbral_val - 1e-6) and _permitida_sel(s)
    ]

    if top5_podio is None:
        top5_podio_out = seleccionar_top_5_jugadas_recomendadas(sels_validas, motor=motor, eq_l=eq_l, eq_v=eq_v)
    else:
        top5_podio_out = list(top5_podio)

    if sec1_data is not None and len(sels_validas) <= 20:
        sels_top_tabla = list(sels_validas)
    else:
        sels_top_tabla = filtrar_top_opciones_ticket(sels_validas, eq_l=eq_l, eq_v=eq_v, p_1x2=p_1x2, max_opciones=18, motor=motor)

    def _es_mantiene(s: Any) -> bool:
        est = _val_sel(s, "estado", None)
        if est is None and sec1_data is not None:
            return True
        return str(est or "").strip().lower() == "mantiene"

    def _sinc_dc_con_p1x2(lista_sels_in: list[Any]) -> list[Any]:
        if p_1x2 is None:
            return list(lista_sels_in)
        p1_m, px_m, p2_m = p_1x2
        out_s: list[Any] = []
        for s_m in lista_sels_in:
            ev_m = str(_val_sel(s_m, "evento") or "").lower().strip()
            per_m = str(_val_sel(s_m, "periodo") or "FT").strip().upper()
            if per_m == "FT" and (ev_m in ("1x", "dc_1x") or ev_m.endswith("_1x")):
                p_real_1x = round(p1_m + px_m, 6)
                if p_real_1x < umbral_val:
                    continue
                if isinstance(s_m, dict):
                    s_m["probabilidad"] = p_real_1x
                else:
                    try:
                        s_m.probabilidad = p_real_1x
                    except Exception:
                        pass
            elif per_m == "FT" and (ev_m in ("x2", "dc_x2") or ev_m.endswith("_x2")):
                p_real_x2 = round(px_m + p2_m, 6)
                if p_real_x2 < umbral_val:
                    continue
                if isinstance(s_m, dict):
                    s_m["probabilidad"] = p_real_x2
                else:
                    try:
                        s_m.probabilidad = p_real_x2
                    except Exception:
                        pass
            out_s.append(s_m)
        return out_s

    mantiene_raw = _sinc_dc_con_p1x2([s for s in sels_top_tabla if _es_mantiene(s)])
    _claves_mantiene = {
        (
            str(_val_sel(s, "id_mercado") or ""),
            str(_val_sel(s, "evento") or "").lower(),
            str(_val_sel(s, "periodo") or "FT").upper(),
            str(_val_sel(s, "equipo") or ""),
            _val_sel(s, "linea"),
        )
        for s in mantiene_raw
    }
    sensibles_cand = [
        s for s in sels_validas
        if not _es_mantiene(s)
        and (
            str(_val_sel(s, "id_mercado") or ""),
            str(_val_sel(s, "evento") or "").lower(),
            str(_val_sel(s, "periodo") or "FT").upper(),
            str(_val_sel(s, "equipo") or ""),
            _val_sel(s, "linea"),
        ) not in _claves_mantiene
    ]
    sensibles_raw = _sinc_dc_con_p1x2(sensibles_cand)

    tiene_res_sec1 = any(clasificar_categoria_ambito_seccion1(s)[0] == "HÁNDICAP / 1X2 / DC" for s in mantiene_raw)
    if not tiene_res_sec1 and top5_podio_out:
        for item_t5 in top5_podio_out:
            prob_t5 = float(item_t5.get("probabilidad", 0.0) or 0.0)
            if (
                int(item_t5.get("slot", 0) or 0) == 1
                and prob_t5 >= umbral_val
                and str(item_t5.get("categoria_podio") or "") == "RESULTADO / DOBLE OPORTUNIDAD"
            ):
                et_t5 = str(item_t5.get("etiqueta") or "")
                ev_t5 = "1x" if "1x" in et_t5.lower() else ("x2" if "x2" in et_t5.lower() else ("1" if eq_l.lower() in et_t5.lower() else "2"))
                if p_1x2 is not None:
                    if ev_t5 == "1x":
                        prob_t5 = round(p_1x2[0] + p_1x2[1], 6)
                    elif ev_t5 == "x2":
                        prob_t5 = round(p_1x2[1] + p_1x2[2], 6)
                    elif ev_t5 == "1":
                        prob_t5 = round(p_1x2[0], 6)
                    elif ev_t5 == "2":
                        prob_t5 = round(p_1x2[2], 6)
                if prob_t5 < umbral_val:
                    continue
                mantiene_raw.append({
                    "id_mercado": "doble_oportunidad" if ev_t5 in ("1x", "x2") else "ganador_partido",
                    "mercado": "Doble oportunidad" if ev_t5 in ("1x", "x2") else "Ganador del partido",
                    "evento": ev_t5,
                    "etiqueta": et_t5,
                    "periodo": "FT",
                    "equipo": eq_l if ev_t5 in ("1", "1x") else eq_v,
                    "linea": None,
                    "probabilidad": prob_t5,
                    "estado": "mantiene",
                })
                break

    # Deduplicación canónica de píldoras (sin truncar a 4 ni omitir líneas válidas)
    def _orden_amb(amb: str) -> int:
        if amb == "Tiempo Completo (FT)":
            return 0
        if amb == "1er Tiempo (1T)":
            return 1
        if amb == "2º Tiempo (2T)":
            return 2
        if eq_l in amb:
            return 11 if "1T" in amb else (12 if "2T" in amb else 10)
        if eq_v in amb:
            return 21 if "1T" in amb else (22 if "2T" in amb else 20)
        return 30

    agrupado: dict[str, dict[str, list[dict[str, Any]]]] = {c: {} for c in ORDEN_CATEGORIAS_SECCION1}
    mantiene_filtrada: list[Any] = []
    vistos_global_cat: dict[str, set[str]] = {c: set() for c in ORDEN_CATEGORIAS_SECCION1}

    candidatos_fmt: list[tuple[str, str, str, str, float, Any]] = []
    for s in mantiene_raw:
        cat_s, amb_s = clasificar_categoria_ambito_seccion1(s)
        lbl_s, pct_s, prob_s = formatear_pildora_seccion1(s, cat_s, eq_l, eq_v)
        if not lbl_s:
            continue
        candidatos_fmt.append((cat_s, amb_s, lbl_s, pct_s, prob_s, s))

    # Ordenar por probabilidad descendente antes de deduplicar por etiqueta dentro de cada categoría
    candidatos_fmt.sort(key=lambda x: -x[4])
    for cat_s, amb_s, lbl_s, pct_s, prob_s, s in candidatos_fmt:
        if lbl_s in vistos_global_cat.setdefault(cat_s, set()):
            continue
        vistos_global_cat[cat_s].add(lbl_s)
        mantiene_filtrada.append(s)
        agrupado.setdefault(cat_s, {}).setdefault(amb_s, []).append({
            "lbl": lbl_s,
            "pct": pct_s,
            "prob": prob_s,
            "ambito": amb_s,
        })

    _orden_canon_amb = ("Tiempo Completo (FT)", "1er Tiempo (1T)", "2º Tiempo (2T)")
    sec1_por_categoria: list[dict[str, Any]] = []
    total_validadas = 0
    for cat_s in ORDEN_CATEGORIAS_SECCION1:
        ambs_d = agrupado.get(cat_s, {})
        filas_cat: list[dict[str, Any]] = []
        pildoras_cat: list[dict[str, Any]] = []
        for amb_s in sorted(ambs_d.keys(), key=_orden_amb):
            pills_amb = sorted(ambs_d[amb_s], key=lambda x: -x["prob"])
            filas_cat.append({"ambito": amb_s, "pildoras": pills_amb})
            pildoras_cat.extend(pills_amb)
        pildoras_cat.sort(key=lambda x: -x["prob"])
        total_validadas += len(pildoras_cat)

        ambs_presentes = []
        for ac in _orden_canon_amb:
            if any(p.get("ambito") == ac for p in pildoras_cat):
                ambs_presentes.append(ac)
        for p in pildoras_cat:
            ao = p.get("ambito")
            if ao and ao not in ambs_presentes:
                ambs_presentes.append(ao)
        amb_str = " · ".join(ambs_presentes) if ambs_presentes else "Tiempo Completo (FT)"
        sec1_por_categoria.append({
            "categoria": cat_s,
            "ambito": amb_str,
            "filas": filas_cat,
            "pildoras": pildoras_cat,
        })

    return {
        "top5_podio": top5_podio_out,
        "mantiene_raw": mantiene_raw,
        "sensibles_raw": sensibles_raw,
        "sec1_por_categoria": sec1_por_categoria,
        "total_validadas": total_validadas,
        "eq_l": eq_l,
        "eq_v": eq_v,
        "umbral": umbral_val,
        "bloquear_corners": bloquear_corners,
        "bloquear_tarjetas": bloquear_tarjetas,
    }


def obtener_top5_y_seccion1_filtrados(
    analisis,
    metadatos = None,
    umbral = None,
):
    """
    Extrae el Top-5 Podio, las selecciones exactas de Sección 1 y su conteo canónico
    usando `calcular_seccion1_unificada` (misma fuente que Ticket PNG y Súper-Cuadro).
    Devuelve (top5_podio, mantiene_raw, total_validadas).
    """
    res = calcular_seccion1_unificada(analisis, metadatos=metadatos, umbral=umbral)
    return res["top5_podio"], res["mantiene_raw"], res["total_validadas"]


def renderizar_ticket_mercados_png(
    analisis: Any,
    metadatos: dict[str, Any] | None = None,
    umbral: float | None = None,
    destino: str | None = None,
) -> str:
    """
    Renderiza el Ticket Cuantitativo de Mercados con diseño editorial claro y profesional:
      • Paleta editorial clara (#F7F9FC, #131D31, #1A6BA8, #E3F2FD, #E8F5EE, #FEF6E4).
      • Estructura en tabla de 3 columnas: CATEGORÍA | ÁMBITO / PERIODO | LÍNEAS Y PROBABILIDADES VALIDADAS.
      • Celda izquierda combinada para la categoría y filas alternadas con bordes #D1DBE6.
      • Píldoras redondeadas (chips) con porcentaje en negrita, ordenadas de mayor a menor probabilidad.
      • Erradicación total de emojis Unicode (cero glifos rotos [▯]).
      • Sección 1 (Alta Probabilidad >= 70%) y Sección 2 (Sensibles a los Supuestos 70%-75%).

    Cierra explícitamente los descriptores para evitar bloqueos en Windows.
    Devuelve la ruta absoluta del archivo PNG generado.
    """
    from PIL import Image, ImageDraw

    def _val(obj: Any, clave: str, defecto: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(clave, defecto)
        return getattr(obj, clave, defecto)

    meta = metadatos or _val(analisis, "metadatos", {}) or {}
    params = _val(analisis, "parametros", None)
    umbral_val = (umbral if umbral is not None else _val(params, "umbral", 0.70))
    if isinstance(umbral_val, (int, float)) and umbral_val > 1.0:
        umbral_val = umbral_val / 100.0

    eq_l = str(meta.get("equipo_local") or "Local")
    eq_v = str(meta.get("equipo_visitante") or "Visitante")
    partido_titulo = f"{eq_l} vs {eq_v}"
    competicion = str(meta.get("competicion") or "Competición oficial")
    fecha = formatear_etiqueta_fecha_encabezado(meta)

    sels_raw = _val(analisis, "selecciones_filtradas", []) or []
    if not sels_raw and hasattr(analisis, "grupos"):
        g = getattr(analisis, "grupos", {})
        sels_raw = (g.get("mantiene", []) + g.get("sensible", []))

    def _get_w(txt: str, font: Any) -> int:
        try:
            bb = font.getbbox(txt)
            return bb[2] - bb[0]
        except Exception:
            return len(txt) * 8

    # Fuentes tipográficas limpias del sistema
    f_title = _cargar_fuentes_ticket(24, bold=True)
    f_sub = _cargar_fuentes_ticket(13, bold=True)
    f_meta = _cargar_fuentes_ticket(12, bold=False)
    f_badge_hdr = _cargar_fuentes_ticket(13, bold=True)
    f_banner_tit = _cargar_fuentes_ticket(14, bold=True)
    f_banner_sub = _cargar_fuentes_ticket(11, bold=False)
    f_banner_cnt = _cargar_fuentes_ticket(12, bold=True)
    f_tbl_hdr = _cargar_fuentes_ticket(12, bold=True)
    f_cat = _cargar_fuentes_ticket(12, bold=True)
    f_amb = _cargar_fuentes_ticket(12, bold=True)
    f_pill_lbl = _cargar_fuentes_ticket(11, bold=False)
    f_pill_pct = _cargar_fuentes_ticket(11, bold=True)
    f_footer = _cargar_fuentes_ticket(11, bold=True)
    f_empty = _cargar_fuentes_ticket(12, bold=False)

    while _get_w(partido_titulo, f_title) > 620 and getattr(f_title, "size", 24) > 18:
        f_title = _cargar_fuentes_ticket(getattr(f_title, "size", 24) - 2, bold=True)

    abstenido = bool(meta.get("abstenido") or getattr(analisis, "abstenido", False))
    if abstenido:
        # Renderizar Ticket Oficial de Abstención Preventiva (Puntos 44 y 45)
        w_img, h_img = 1000, 520
        im = Image.new("RGB", (w_img, h_img), "#F7F9FC")
        draw = ImageDraw.Draw(im)

        # Header
        draw.rectangle([0, 0, w_img, 95], fill="#131D31")
        draw.text((30, 20), partido_titulo, font=f_title, fill="#FFFFFF")
        _mot_abs = getattr(analisis, "motor", None)
        _fuentes_abs = _sanear_para_canvas(formatear_fuentes_clubes_encabezado(
            meta,
            getattr(_mot_abs, "doc_local", None),
            getattr(_mot_abs, "doc_visitante", None),
            equipo_local=eq_l,
            equipo_visitante=eq_v,
        ))
        draw.text((30, 54 if _fuentes_abs else 60), f"{competicion} · {fecha}", font=f_meta, fill="#94A3B8")
        if _fuentes_abs:
            draw.text((30, 74), _fuentes_abs, font=f_meta, fill="#CBD5E1")

        # Badge AUDITORÍA en esquina superior derecha
        w_b = _get_w("AUDITORÍA DE COBERTURA D.1", f_badge_hdr) + 24
        draw.rounded_rectangle([w_img - w_b - 30, 30, w_img - 30, 65], radius=6, fill="#1E3A8A")
        draw.text((w_img - w_b - 18, 38), "AUDITORÍA DE COBERTURA D.1", font=f_badge_hdr, fill="#FFFFFF")
        from utilidades_seguridad import obtener_sello_build as _obtener_sello_build
        _sello_abs = _sanear_para_canvas(_obtener_sello_build())
        draw.text((w_img - 30 - _get_w(_sello_abs, f_meta), 76), _sello_abs, font=f_meta, fill="#475569")

        # Tarjeta de Alerta de Abstención
        est_cob = str(meta.get("estado_cobertura") or "cobertura_insuficiente").upper()
        etq_l = str(meta.get("etiqueta_cobertura_local") or "Historial auditado")
        etq_v = str(meta.get("etiqueta_cobertura_visitante") or "Historial auditado")
        mot_abs = str(meta.get("motivo_abstencion") or "Muestra estadística insuficiente")

        # Card container
        draw.rounded_rectangle([30, 115, w_img - 30, 445], radius=8, fill="#FEF2F2", outline="#F87171", width=2)

        # Banner interno rojo
        draw.rounded_rectangle([45, 130, w_img - 45, 175], radius=6, fill="#DC2626")
        draw.text((60, 142), f"PRONÓSTICO ABSTENIDO: {est_cob}", font=f_banner_tit, fill="#FFFFFF")

        # Etiquetas de cobertura de los clubes
        draw.text((60, 195), f"• {eq_l}:", font=f_sub, fill="#1E293B")
        draw.text((60 + _get_w(f"• {eq_l}:", f_sub) + 10, 195), etq_l, font=f_meta, fill="#334155")

        draw.text((60, 225), f"• {eq_v}:", font=f_sub, fill="#1E293B")
        draw.text((60 + _get_w(f"• {eq_v}:", f_sub) + 10, 225), etq_v, font=f_meta, fill="#334155")

        # Línea divisoria
        draw.line([60, 260, w_img - 60, 260], fill="#FECACA", width=1)

        # Justificación técnica
        draw.text((60, 275), "JUSTIFICACIÓN TÉCNICA:", font=f_sub, fill="#991B1B")

        palabras = mot_abs.split()
        lineas_mot = []
        cur_l = []
        for p in palabras:
            cur_l.append(p)
            if _get_w(" ".join(cur_l), f_meta) > 860:
                cur_l.pop()
                lineas_mot.append(" ".join(cur_l))
                cur_l = [p]
        if cur_l:
            lineas_mot.append(" ".join(cur_l))

        y_txt = 300
        for l_m in lineas_mot[:3]:
            draw.text((60, y_txt), l_m, font=f_meta, fill="#1F2937")
            y_txt += 22

        # Directiva de garantía
        draw.rounded_rectangle([60, 375, w_img - 60, 425], radius=6, fill="#FFFBEB", outline="#FCD34D", width=1)
        draw.text((75, 385), "GARANTÍA CUANTITATIVA (Puntos 44 y 45):", font=f_banner_sub, fill="#92400E")
        draw.text((75, 403), "Prohibido calcular o publicar probabilidades cuantitativas o matrices de Poisson sin evidencia suficiente.", font=f_meta, fill="#78350F")

        # Footer
        draw.text((30, 475), "AGENTE CUANTITATIVO DE FÚTBOL · MÓDULO DE DECISIÓN DE COBERTURA D.1 · PREVENCIÓN DE ALUCINACIONES", font=f_footer, fill="#64748B")

        ruta_salida = destino or os.path.join(MERCADOS_DIR, f"ticket_abstenido_{_slug(eq_l)}_vs_{_slug(eq_v)}.png")
        os.makedirs(os.path.dirname(os.path.abspath(ruta_salida)), exist_ok=True)
        im.save(ruta_salida, format="PNG")
        return ruta_salida

    _formatear_pildora = lambda s, cat: formatear_pildora_seccion1(s, cat, eq_l, eq_v)
    _clasificar_categoria_ambito = clasificar_categoria_ambito_seccion1
    ORDEN_CATEGORIAS = ORDEN_CATEGORIAS_SECCION1

    def _orden_ambito(amb: str) -> int:
        if amb == "Tiempo Completo (FT)":
            return 0
        if amb == "1er Tiempo (1T)":
            return 1
        if amb == "2º Tiempo (2T)":
            return 2
        if eq_l in amb:
            if "1T" in amb:
                return 11
            if "2T" in amb:
                return 12
            return 10
        if eq_v in amb:
            if "1T" in amb:
                return 21
            if "2T" in amb:
                return 22
            return 20
        return 30

    ANCHO_CANVAS = 960
    PAD_X = 28
    ANCHO_TABLA = ANCHO_CANVAS - PAD_X * 2 # 904
    W_COL1 = 180
    W_COL2 = 194
    W_COL3 = ANCHO_TABLA - W_COL1 - W_COL2 # 530
    X_COL1 = PAD_X
    X_COL2 = PAD_X + W_COL1
    X_COL3 = PAD_X + W_COL1 + W_COL2
    X_LIMIT_PILLS = X_COL3 + W_COL3 - 10

    def _organizar_bloque(lista_sels: list[Any], es_seccion_1: bool = False) -> list[dict[str, Any]]:
        agrupado: dict[str, dict[str, list[Any]]] = {c: {} for c in ORDEN_CATEGORIAS}
        for s in lista_sels:
            cat, amb = _clasificar_categoria_ambito(s)
            agrupado.setdefault(cat, {}).setdefault(amb, []).append(s)

        res_cats: list[dict[str, Any]] = []
        for cat in ORDEN_CATEGORIAS:
            ambs_dict = agrupado.get(cat, {})
            if not ambs_dict:
                if cat == "TARJETAS" and es_seccion_1:
                    lbl_sin = "Sin líneas validadas >= 70%"
                    w_lbl = _get_w(lbl_sin, f_pill_lbl)
                    pill_w = 8 + w_lbl + 8
                    pildora_sin = {
                        "lbl": lbl_sin,
                        "pct": "",
                        "prob": 0.0,
                        "w": pill_w,
                        "w_lbl": w_lbl,
                        "w_pct": 0,
                        "sin_lineas": True,
                    }
                    filas_cat = [{
                        "ambito": "Tiempo Completo (FT)",
                        "pildoras": [pildora_sin],
                        "lineas_pills": [[pildora_sin]],
                        "alto": 38,
                    }]
                    res_cats.append({
                        "categoria": cat,
                        "filas": filas_cat,
                        "alto_total": 38,
                    })
                continue

            filas_cat: list[dict[str, Any]] = []
            for amb in sorted(ambs_dict.keys(), key=_orden_ambito):
                items = ambs_dict[amb]
                if es_seccion_1:
                    pildoras_raw = [_formatear_pildora(s, cat) for s in items]
                else:
                    from motor_mercados import formatear_motivo_degradacion_seccion2 as _fmt_mot_s2
                    pildoras_raw = []
                    for _s_it in items:
                        _lbl_s2, _pct_s2, _prob_s2 = _formatear_pildora(_s_it, cat)
                        if _lbl_s2:
                            _mot_s2 = _fmt_mot_s2(_s_it, umbral=umbral_val, corto=True)
                            _lbl_s2 = f"{_lbl_s2} [{_mot_s2}]"
                        pildoras_raw.append((_lbl_s2, _pct_s2, _prob_s2))
                pildoras_raw.sort(key=lambda x: -x[2])

                vistas_et = set()
                pildoras: list[dict[str, Any]] = []
                for lbl, pct, prob in pildoras_raw:
                    if not lbl:
                        continue
                    clave_dup = (lbl, pct)
                    if clave_dup in vistas_et:
                        continue
                    vistas_et.add(clave_dup)
                    w_lbl = _get_w(lbl, f_pill_lbl)
                    w_pct = _get_w(pct, f_pill_pct)
                    pill_w = 8 + w_lbl + 5 + w_pct + 8
                    pildoras.append({
                        "lbl": lbl,
                        "pct": pct,
                        "prob": prob,
                        "w": pill_w,
                        "w_lbl": w_lbl,
                        "w_pct": w_pct,
                    })

                if not pildoras:
                    continue

                cur_x = X_COL3 + 10
                lineas_pills: list[list[dict[str, Any]]] = [[]]
                for p in pildoras:
                    if cur_x + p["w"] > X_LIMIT_PILLS and lineas_pills[-1]:
                        lineas_pills.append([])
                        cur_x = X_COL3 + 10
                    lineas_pills[-1].append(p)
                    cur_x += p["w"] + 6

                n_lineas = max(1, len(lineas_pills))
                alto_fila = max(38, 8 + n_lineas * 24 + (n_lineas - 1) * 6 + 8)

                filas_cat.append({
                    "ambito": amb,
                    "pildoras": pildoras,
                    "lineas_pills": lineas_pills,
                    "alto": alto_fila,
                })

            if filas_cat:
                alto_total_cat = sum(f["alto"] for f in filas_cat)
                res_cats.append({
                    "categoria": cat,
                    "filas": filas_cat,
                    "alto_total": alto_total_cat,
                })
        return res_cats

    motor = getattr(analisis, "motor", None)
    if True:
        from motor_mercados import clusters_marcador

        res_sec1_uni = calcular_seccion1_unificada(
            analisis,
            metadatos=meta,
            umbral=umbral_val,
            eq_l=eq_l,
            eq_v=eq_v,
        )
        top5_podio = res_sec1_uni["top5_podio"]
        mantiene_raw = res_sec1_uni["mantiene_raw"]
        sensibles_raw = res_sec1_uni["sensibles_raw"]

        def _construir_sec1_desde_canonico(sec1_cats: list[dict[str, Any]]) -> list[dict[str, Any]]:
            res_c: list[dict[str, Any]] = []
            for cat_b in sec1_cats:
                cat = cat_b["categoria"]
                filas_src = cat_b.get("filas") or []
                if not filas_src:
                    if cat == "TARJETAS":
                        lbl_sin = "Sin líneas validadas >= 70%"
                        w_lbl = _get_w(lbl_sin, f_pill_lbl)
                        pildora_sin = {
                            "lbl": lbl_sin, "pct": "", "prob": 0.0,
                            "w": 8 + w_lbl + 8, "w_lbl": w_lbl, "w_pct": 0, "sin_lineas": True,
                        }
                        res_c.append({
                            "categoria": cat,
                            "filas": [{
                                "ambito": "Tiempo Completo (FT)",
                                "pildoras": [pildora_sin],
                                "lineas_pills": [[pildora_sin]],
                                "alto": 38,
                            }],
                            "alto_total": 38,
                        })
                    continue
                filas_out: list[dict[str, Any]] = []
                for fl in filas_src:
                    pills_medidas: list[dict[str, Any]] = []
                    for p_it in (fl.get("pildoras") or []):
                        lbl_p = p_it["lbl"]
                        pct_p = p_it["pct"]
                        w_lbl = _get_w(lbl_p, f_pill_lbl)
                        w_pct = _get_w(pct_p, f_pill_pct)
                        pills_medidas.append({
                            "lbl": lbl_p,
                            "pct": pct_p,
                            "prob": p_it["prob"],
                            "w": 8 + w_lbl + 5 + w_pct + 8,
                            "w_lbl": w_lbl,
                            "w_pct": w_pct,
                        })
                    if not pills_medidas:
                        continue
                    cur_x = X_COL3 + 10
                    lineas_pills: list[list[dict[str, Any]]] = [[]]
                    for p_m in pills_medidas:
                        if cur_x + p_m["w"] > X_LIMIT_PILLS and lineas_pills[-1]:
                            lineas_pills.append([])
                            cur_x = X_COL3 + 10
                        lineas_pills[-1].append(p_m)
                        cur_x += p_m["w"] + 6
                    n_lin = max(1, len(lineas_pills))
                    filas_out.append({
                        "ambito": fl["ambito"],
                        "pildoras": pills_medidas,
                        "lineas_pills": lineas_pills,
                        "alto": max(38, 8 + n_lin * 24 + (n_lin - 1) * 6 + 8),
                    })
                if filas_out:
                    res_c.append({
                        "categoria": cat,
                        "filas": filas_out,
                        "alto_total": sum(f["alto"] for f in filas_out),
                    })
            return res_c

        sec1_data = _construir_sec1_desde_canonico(res_sec1_uni["sec1_por_categoria"])
        sec2_data = _organizar_bloque(sensibles_raw, es_seccion_1=False)
        total_validadas_canonico = int(res_sec1_uni["total_validadas"])

        clusters_info = getattr(motor, "clusters", None)
        if not clusters_info and hasattr(analisis, "matriz_ft"):
            try:
                clusters_info = clusters_marcador(
                    getattr(analisis, "matriz_ft"),
                    eq_l,
                    eq_v,
                    selecciones_sec1_2=list(mantiene_raw) + list(sensibles_raw),
                )
            except Exception:
                clusters_info = None

        linea_cluster = (clusters_info or {}).get("linea_pie", "")

        h2h_tkt_txt = str(meta.get("h2h_reciente_ticket") or "").strip()
        if not h2h_tkt_txt and (getattr(motor, "doc_local", None) or getattr(motor, "doc_visitante", None)):
            h2h_tkt_txt = construir_resumen_h2h_ticket(
                getattr(motor, "doc_local", None),
                getattr(motor, "doc_visitante", None),
                eq_l,
                eq_v,
                fecha_objetivo=str(meta.get("fecha_partido") or ""),
            )
            if h2h_tkt_txt:
                meta["h2h_reciente_ticket"] = h2h_tkt_txt
        meta_parts_pre = []
        if competicion:
            meta_parts_pre.append(f"Competición: {competicion}")
        if meta.get("advertencia_liga"):
            adv_str = str(meta["advertencia_liga"])
            partes_adv = adv_str.split("|")
            p_decl = partes_adv[0].replace("Declarada:", "").strip() if len(partes_adv) > 0 else ""
            p_det = partes_adv[1].replace("Detectada:", "").strip() if len(partes_adv) > 1 else ""
            if not son_ligas_equivalentes(p_decl, p_det):
                meta_parts_pre.append(f"⚠️ {meta['advertencia_liga']}")
        if fecha:
            if fecha.startswith(("Partido:", "Sin partido programado")):
                meta_parts_pre.append(fecha)
            else:
                meta_parts_pre.append(f"Fecha: {fecha}")
        _fuentes_hdr_tkt = formatear_fuentes_clubes_encabezado(
            meta,
            getattr(motor, "doc_local", None),
            getattr(motor, "doc_visitante", None),
            equipo_local=eq_l,
            equipo_visitante=eq_v,
        )
        if _fuentes_hdr_tkt:
            meta_parts_pre.append(_fuentes_hdr_tkt)
        meta_parts_pre.append("Modelo: Poisson bivariado con contracción bayesiana")
        _rec_ticket = (getattr(getattr(analisis, "motor", None), "recursivo", None) or {})
        if _rec_ticket.get("aplicado"):
            meta_parts_pre[-1] = (
                f"Modelo: red de factores con pesos fijos "
                f"(conv. it. {_rec_ticket.get('iteracion_convergencia')})"
            )
        meta_txt_full = _sanear_para_canvas("   ·   ".join(meta_parts_pre))
        meta_lineas: list[str] = []
        if _get_w(meta_txt_full, f_meta) <= ANCHO_TABLA:
            meta_lineas = [meta_txt_full]
        else:
            cur_m = ""
            for mp in meta_parts_pre:
                mp_s = _sanear_para_canvas(mp)
                cand_m = f"{cur_m}   ·   {mp_s}" if cur_m else mp_s
                if cur_m and _get_w(cand_m, f_meta) > ANCHO_TABLA:
                    meta_lineas.append(cur_m)
                    cur_m = mp_s
                else:
                    cur_m = cand_m
            if cur_m:
                meta_lineas.append(cur_m)
        alto_header = 118 + max(0, len(meta_lineas) - 1) * 18 + (20 if h2h_tkt_txt else 0)
        alto_banner = 44
        alto_tbl_hdr = 32
        alto_footer = 56 if linea_cluster else 40
        gap_seccion = 18

        alto_podio = 0
        if top5_podio:
            alto_podio = alto_banner + 6 + len(top5_podio) * 44 + 8

        alto_sec1 = alto_banner + 6 + alto_tbl_hdr
        if not sec1_data:
            alto_sec1 += 50
        else:
            alto_sec1 += sum(c["alto_total"] for c in sec1_data)

        alto_sec2 = 0
        if sec2_data:
            alto_sec2 = alto_banner + 6 + alto_tbl_hdr + sum(c["alto_total"] for c in sec2_data)

        ALTO_CANVAS = (
            alto_header
            + (alto_podio + gap_seccion if alto_podio else 0)
            + alto_sec1
            + (alto_sec2 + gap_seccion if alto_sec2 else 0)
            + gap_seccion
            + alto_footer
            + 24
        )

    # Paleta Editorial Clara Obligatoria
    C_BG = (247, 249, 252)              # #F7F9FC
    C_TITLE = (15, 23, 42)              # #0F172A
    C_COBALT = (26, 107, 168)           # #1A6BA8
    C_NAVY_HDR = (19, 29, 49)           # #131D31
    C_BORDER = (209, 219, 230)          # #D1DBE6
    C_BORDER_DARK = (176, 192, 208)     # #B0C0D0
    C_ROW_EVEN = (255, 255, 255)        # #FFFFFF
    C_ROW_ODD = (242, 246, 251)         # #F2F6FB
    C_TXT_MUTED = (100, 116, 139)       # #64748B
    C_TXT_DARK = (18, 26, 38)           # #121A26
    C_TXT_WHITE = (255, 255, 255)

    # Sección 1 (Verde Esmeralda)
    C_SEC1_BANNER_BG = (232, 245, 238)      # #E8F5EE
    C_SEC1_BANNER_BORDER = (46, 148, 98)    # #2E9462
    C_SEC1_BANNER_TXT = (19, 108, 64)       # #136C40
    C_SEC1_PILL_BG = (236, 250, 242)        # #ECFAF2
    C_SEC1_PILL_BORDER = (165, 222, 190)    # #A5DEBE
    C_SEC1_PILL_TXT = (18, 26, 38)          # #121A26
    C_SEC1_PILL_PCT = (19, 108, 64)         # #136C40

    # Sección 2 (Ámbar Claro)
    C_SEC2_BANNER_BG = (254, 246, 228)      # #FEF6E4
    C_SEC2_BANNER_BORDER = (217, 130, 24)   # #D98218
    C_SEC2_BANNER_TXT = (154, 84, 6)        # #9A5406
    C_SEC2_PILL_BG = (255, 249, 235)        # #FFF9EB
    C_SEC2_PILL_BORDER = (242, 201, 138)    # #F2C98A
    C_SEC2_PILL_TXT = (18, 26, 38)          # #121A26
    C_SEC2_PILL_PCT = (154, 84, 6)          # #9A5406

    # Badges institucionales y pie
    C_SKY_BG = (227, 242, 253)          # #E3F2FD
    C_SKY_BORDER = (88, 166, 220)       # #58A6DC
    C_SKY_TXT = (26, 107, 168)          # #1A6BA8

    img = Image.new("RGB", (ANCHO_CANVAS, ALTO_CANVAS), C_BG)
    d = ImageDraw.Draw(img)

    try:
        # 1. Cabecera institucional
        y_c = 22
        d.text((PAD_X, y_c), partido_titulo, font=f_title, fill=C_TITLE)

        badge_txt = f"Umbral Cuantitativo >= {umbral_val:.0%}"
        bw = _get_w(badge_txt, f_badge_hdr) + 24
        bx = ANCHO_CANVAS - PAD_X - bw
        by = y_c + 2
        d.rounded_rectangle([bx, by, bx + bw, by + 30], radius=6, fill=C_SKY_BG, outline=C_SKY_BORDER, width=1)
        d.text((bx + 12, by + 6), badge_txt, font=f_badge_hdr, fill=C_SKY_TXT)

        y_c += 36
        d.text((PAD_X, y_c), "TICKET CUANTITATIVO DE ALTA PROBABILIDAD", font=f_sub, fill=C_COBALT)
        from utilidades_seguridad import obtener_sello_build as _obtener_sello_build
        _sello_tkt = _sanear_para_canvas(_obtener_sello_build())
        d.text((ANCHO_CANVAS - PAD_X - _get_w(_sello_tkt, f_meta), y_c + 2), _sello_tkt, font=f_meta, fill=C_TXT_MUTED)

        y_c += 22
        for idx_ml, ml_txt in enumerate(meta_lineas):
            if idx_ml > 0:
                y_c += 18
            d.text((PAD_X, y_c), ml_txt, font=f_meta, fill=C_TXT_MUTED)
        if h2h_tkt_txt:
            y_c += 20
            d.text((PAD_X, y_c), _sanear_para_canvas(h2h_tkt_txt), font=f_meta, fill=C_COBALT)

        y_c += 32
        d.line([PAD_X, y_c, PAD_X + ANCHO_TABLA, y_c], fill=C_BORDER, width=1)
        y_c += 12

        # 1.5 Bloque destacado: TOP 5 JUGADAS RECOMENDADAS
        if top5_podio:
            C_PODIO_BG = (227, 242, 253)        # #E3F2FD
            C_PODIO_BORDER = (26, 107, 168)     # #1A6BA8
            C_PODIO_TXT = (19, 29, 49)          # #131D31
            d.rounded_rectangle([PAD_X, y_c, PAD_X + ANCHO_TABLA, y_c + alto_banner],
                                radius=6, fill=C_PODIO_BG, outline=C_PODIO_BORDER, width=1)
            _t5_tiene_cuota = any(isinstance(it, dict) and (it.get("ev_pct") is not None or it.get("ev") is not None) for it in top5_podio)
            _suf_tkt = "(ALTO VALOR TE APUESTO)" if _t5_tiene_cuota else "(ALTA PROBABILIDAD DEL MODELO)"
            tit_podio_hdr = f"TOP {len(top5_podio)} JUGADAS RECOMENDADAS {_suf_tkt}" if len(top5_podio) < 5 else f"TOP 5 JUGADAS RECOMENDADAS {_suf_tkt}"
            d.text((PAD_X + 16, y_c + 8), tit_podio_hdr, font=f_banner_tit, fill=C_PODIO_TXT)
            cnt_podio_txt = f"Top {len(top5_podio)} Selección Élite"
            cw_p = _get_w(cnt_podio_txt, f_banner_cnt) + 18
            cx_p = PAD_X + ANCHO_TABLA - 14 - cw_p
            cy_p = y_c + 9

            nota_cal_podio = str(top5_podio[0].get("nota_calibracion") or "Podio diversificado (70%-88% prob.) con respaldo estadístico del Doble ADN.")
            max_w_sub_p = max(250, cx_p - (PAD_X + 16) - 12)
            if _get_w(nota_cal_podio, f_banner_sub) > max_w_sub_p:
                nota_cal_podio = nota_cal_podio.replace(
                    "Probabilidad ajustada por varianza (9 escenarios de estrés) e ineficiencia de mercado. ",
                    "Ajustada por varianza (9 escenarios): ",
                )
            d.text((PAD_X + 16, y_c + 26), nota_cal_podio, font=f_banner_sub, fill=C_TXT_MUTED)
            d.rounded_rectangle([cx_p, cy_p, cx_p + cw_p, cy_p + 26], radius=4, fill=(255, 255, 255), outline=C_PODIO_BORDER, width=1)
            d.text((cx_p + 9, cy_p + 5), cnt_podio_txt, font=f_banner_cnt, fill=C_COBALT)

            y_c += alto_banner + 6

            for idx_p, item in enumerate(top5_podio):
                row_h = 44
                bg_row = C_ROW_EVEN if (idx_p % 2 == 0) else C_ROW_ODD
                d.rectangle([PAD_X, y_c, PAD_X + ANCHO_TABLA, y_c + row_h], fill=bg_row, outline=C_BORDER, width=1)
                d.rectangle([PAD_X, y_c, PAD_X + 5, y_c + row_h], fill=C_COBALT)

                # Medalla numérica #1..#5
                rank_txt = f"#{item['rank']}"
                d.rounded_rectangle([PAD_X + 14, y_c + 9, PAD_X + 44, y_c + 35], radius=5, fill=C_NAVY_HDR)
                d.text((PAD_X + 20, y_c + 14), rank_txt, font=f_cat, fill=C_TXT_WHITE)

                # Píldora de probabilidad + EV% + Stake Kelly 25% a la derecha
                pct_str = item["probabilidad_pct"]
                est_str = "SÓLIDA" if item.get("estado") == "mantiene" else "SENSIBLE"
                ev_raw = item.get("ev_pct")
                stk_raw = item.get("stake_sugerido_pct")
                if ev_raw is not None and stk_raw is not None:
                    badge_p_txt = str(item.get("badge_completo") or f"{pct_str} ({est_str} · EV {float(ev_raw):+.1f}% · Stake: {float(stk_raw):.1f}%)")
                else:
                    c_min = _safe_float(item.get("cuota_minima"), 1.05 / max(0.01, _safe_float(item.get("probabilidad"), 0.70)))
                    badge_p_txt = str(item.get("badge_completo") or f"{pct_str} ({est_str} · Cuota Mín: {c_min:.2f})")
                bw_p = _get_w(badge_p_txt, f_pill_pct) + 20
                bx_p = PAD_X + ANCHO_TABLA - 14 - bw_p
                by_p = y_c + 10

                # Etiqueta de la jugada y categoría (truncar respaldo antes del badge)
                jug_txt = _sanear_para_canvas(f"[{item['categoria_podio']}]  {item['etiqueta']}")
                resp_txt = _sanear_para_canvas(str(item.get("respaldo") or ""))
                max_w_resp = max(220, bx_p - (PAD_X + 54) - 14)
                while _get_w(resp_txt, f_banner_sub) > max_w_resp and len(resp_txt) > 30:
                    resp_txt = resp_txt[:-6].rstrip() + "..."
                d.text((PAD_X + 54, y_c + 6), jug_txt, font=f_amb, fill=C_TITLE)
                d.text((PAD_X + 54, y_c + 24), resp_txt, font=f_banner_sub, fill=C_TXT_MUTED)

                p_bg = C_SEC1_PILL_BG if item.get("estado") == "mantiene" else C_SEC2_PILL_BG
                p_bd = C_SEC1_PILL_BORDER if item.get("estado") == "mantiene" else C_SEC2_PILL_BORDER
                p_fg = C_SEC1_PILL_PCT if item.get("estado") == "mantiene" else C_SEC2_PILL_PCT
                d.rounded_rectangle([bx_p, by_p, bx_p + bw_p, by_p + 24], radius=5, fill=p_bg, outline=p_bd, width=1)
                d.text((bx_p + 10, by_p + 4), badge_p_txt, font=f_pill_pct, fill=p_fg)

                y_c += row_h

            y_c += 8 + gap_seccion

        def _dibujar_seccion(
            y_inicio: int,
            titulo_sec: str,
            subtitulo_sec: str,
            conteo_txt: str,
            datos_sec: list[dict[str, Any]],
            c_banner_bg: tuple[int, int, int],
            c_banner_border: tuple[int, int, int],
            c_banner_txt: tuple[int, int, int],
            c_pill_bg: tuple[int, int, int],
            c_pill_border: tuple[int, int, int],
            c_pill_pct: tuple[int, int, int],
            accent_cat_color: tuple[int, int, int],
            titulo_col3: str = "LÍNEAS Y PROBABILIDADES VALIDADAS",
        ) -> int:
            y = y_inicio

            # Banner de sección
            d.rounded_rectangle([PAD_X, y, PAD_X + ANCHO_TABLA, y + alto_banner],
                                radius=6, fill=c_banner_bg, outline=c_banner_border, width=1)
            d.text((PAD_X + 16, y + 8), titulo_sec, font=f_banner_tit, fill=c_banner_txt)
            d.text((PAD_X + 16, y + 26), subtitulo_sec, font=f_banner_sub, fill=C_TXT_MUTED)

            cw = _get_w(conteo_txt, f_banner_cnt) + 18
            cx = PAD_X + ANCHO_TABLA - 14 - cw
            cy = y + 9
            d.rounded_rectangle([cx, cy, cx + cw, cy + 26], radius=4, fill=(255, 255, 255), outline=c_banner_border, width=1)
            d.text((cx + 9, cy + 5), conteo_txt, font=f_banner_cnt, fill=c_banner_txt)

            y += alto_banner + 6

            # Encabezado de tabla (Azul Marino #131D31)
            d.rectangle([PAD_X, y, PAD_X + ANCHO_TABLA, y + alto_tbl_hdr], fill=C_NAVY_HDR)
            d.text((PAD_X + 14, y + 8), "CATEGORÍA", font=f_tbl_hdr, fill=C_TXT_WHITE)
            d.text((X_COL2 + 14, y + 8), "ÁMBITO / PERIODO", font=f_tbl_hdr, fill=C_TXT_WHITE)
            d.text((X_COL3 + 14, y + 8), titulo_col3, font=f_tbl_hdr, fill=C_TXT_WHITE)
            y += alto_tbl_hdr

            if not datos_sec:
                h_empty = 50
                d.rectangle([PAD_X, y, PAD_X + ANCHO_TABLA, y + h_empty], fill=C_ROW_EVEN, outline=C_BORDER, width=1)
                msg_empty = f"Ninguna selección superó el umbral configurado (>= {umbral_val:.0%}) en los escenarios evaluados."
                d.text((PAD_X + 24, y + 17), msg_empty, font=f_empty, fill=C_TXT_MUTED)
                return y + h_empty

            idx_fila_global = 0
            for bloque_cat in datos_sec:
                cat_nombre = bloque_cat["categoria"]
                filas = bloque_cat["filas"]
                cat_h = bloque_cat["alto_total"]
                y_cat_inicio = y

                # Filas individuales (Col 2 y Col 3)
                y_curr_fila = y
                for f_data in filas:
                    f_h = f_data["alto"]
                    color_bg = C_ROW_EVEN if (idx_fila_global % 2 == 0) else C_ROW_ODD
                    idx_fila_global += 1

                    d.rectangle([X_COL2, y_curr_fila, PAD_X + ANCHO_TABLA, y_curr_fila + f_h], fill=color_bg)

                    # Texto Ámbito / Periodo (Col 2): partir en 2 líneas si supera el ancho de columna
                    amb_txt = f_data["ambito"]
                    if _get_w(amb_txt, f_amb) > (W_COL2 - 18) and " · " in amb_txt:
                        amb_l1, amb_l2 = amb_txt.split(" · ", 1)
                        y_amb = y_curr_fila + max(4, (f_h - 30) // 2)
                        d.text((X_COL2 + 12, y_amb), amb_l1, font=f_amb, fill=C_TITLE)
                        d.text((X_COL2 + 12, y_amb + 15), f"· {amb_l2}", font=f_amb, fill=C_TXT_MUTED)
                    else:
                        h_amb = 14
                        y_amb = y_curr_fila + (f_h - h_amb) // 2
                        d.text((X_COL2 + 12, y_amb), amb_txt, font=f_amb, fill=C_TITLE)

                    # Píldoras redondeadas ordenadas (Col 3)
                    lineas_pills = f_data["lineas_pills"]
                    y_pills_start = y_curr_fila + 7
                    for idx_lin, p_line in enumerate(lineas_pills):
                        y_p = y_pills_start + idx_lin * (24 + 6)
                        cur_px = X_COL3 + 12
                        for p in p_line:
                            pw = p["w"]
                            if p.get("sin_lineas"):
                                d.rounded_rectangle([cur_px, y_p, cur_px + pw, y_p + 24],
                                                    radius=5, fill=(241, 245, 249), outline=(203, 213, 225), width=1)
                                d.text((cur_px + 8, y_p + 4), p["lbl"], font=f_pill_lbl, fill=(100, 116, 139))
                            else:
                                d.rounded_rectangle([cur_px, y_p, cur_px + pw, y_p + 24],
                                                    radius=5, fill=c_pill_bg, outline=c_pill_border, width=1)
                                d.text((cur_px + 8, y_p + 4), p["lbl"], font=f_pill_lbl, fill=C_TXT_DARK)
                                d.text((cur_px + 8 + p["w_lbl"] + 5, y_p + 4), p["pct"], font=f_pill_pct, fill=c_pill_pct)
                            cur_px += pw + 6

                    d.line([X_COL2, y_curr_fila + f_h, PAD_X + ANCHO_TABLA, y_curr_fila + f_h], fill=C_BORDER, width=1)
                    y_curr_fila += f_h

                # Celda Combinada de Categoría (Col 1)
                d.rectangle([X_COL1, y_cat_inicio, X_COL2, y_cat_inicio + cat_h], fill=C_ROW_EVEN)
                d.rectangle([X_COL1, y_cat_inicio, X_COL1 + 4, y_cat_inicio + cat_h], fill=accent_cat_color)
                h_cat = 14
                y_cat_txt = y_cat_inicio + (cat_h - h_cat) // 2
                d.text((X_COL1 + 14, y_cat_txt), cat_nombre, font=f_cat, fill=C_TITLE)

                d.line([X_COL1, y_cat_inicio, X_COL1, y_cat_inicio + cat_h], fill=C_BORDER, width=1)
                d.line([X_COL2, y_cat_inicio, X_COL2, y_cat_inicio + cat_h], fill=C_BORDER, width=1)
                d.line([X_COL3, y_cat_inicio, X_COL3, y_cat_inicio + cat_h], fill=C_BORDER, width=1)
                d.line([PAD_X + ANCHO_TABLA, y_cat_inicio, PAD_X + ANCHO_TABLA, y_cat_inicio + cat_h], fill=C_BORDER, width=1)

                d.line([X_COL1, y_cat_inicio + cat_h, PAD_X + ANCHO_TABLA, y_cat_inicio + cat_h], fill=C_BORDER_DARK, width=1)
                y = y_cat_inicio + cat_h

            return y

        # 2. Sección 1: Alta Probabilidad (excluye placeholders sin_lineas)
        total_pills_sec1 = sum(
            1 for cat in sec1_data for f in cat.get("filas", [])
            for p in f.get("pildoras", []) if not p.get("sin_lineas")
        )
        cnt1_txt = f"{total_pills_sec1} opciones validadas"
        y_c = _dibujar_seccion(
            y_inicio=y_c,
            titulo_sec="SECCIÓN 1: ALTA PROBABILIDAD",
            subtitulo_sec=("Mercados que superan el umbral cuantitativo en el modelo central y bajo estrés." if total_pills_sec1 > 0 else "Sin mercados que superan el umbral cuantitativo (>=70%) en el modelo central y bajo estrés."),
            conteo_txt=cnt1_txt,
            datos_sec=sec1_data,
            c_banner_bg=C_SEC1_BANNER_BG,
            c_banner_border=C_SEC1_BANNER_BORDER,
            c_banner_txt=C_SEC1_BANNER_TXT,
            c_pill_bg=C_SEC1_PILL_BG,
            c_pill_border=C_SEC1_PILL_BORDER,
            c_pill_pct=C_SEC1_PILL_PCT,
            accent_cat_color=C_COBALT,
        )

        # 3. Sección 2: Sensibles a los Supuestos
        if sec2_data:
            y_c += gap_seccion
            total_pills_sec2 = sum(len(f.get("pildoras", [])) for cat in sec2_data for f in cat.get("filas", []))
            cnt2_txt = f"{total_pills_sec2} opciones sensibles"
            y_c = _dibujar_seccion(
                y_inicio=y_c,
                titulo_sec=f"SECCIÓN 2: SENSIBLES A LOS SUPUESTOS (>= {umbral_val:.0%})",
                subtitulo_sec="Superan el umbral central (>=70%), pero su probabilidad varía bajo escenarios de estrés o desvío empírico.",
                conteo_txt=cnt2_txt,
                datos_sec=sec2_data,
                c_banner_bg=C_SEC2_BANNER_BG,
                c_banner_border=C_SEC2_BANNER_BORDER,
                c_banner_txt=C_SEC2_BANNER_TXT,
                c_pill_bg=C_SEC2_PILL_BG,
                c_pill_border=C_SEC2_PILL_BORDER,
                c_pill_pct=C_SEC2_PILL_PCT,
                accent_cat_color=C_SEC2_BANNER_BORDER,
                titulo_col3="LÍNEAS Y PROBABILIDADES SENSIBLES",
            )

        # 4. Pie de ticket
        y_c += gap_seccion
        d.rounded_rectangle([PAD_X, y_c, PAD_X + ANCHO_TABLA, y_c + alto_footer],
                            radius=6, fill=C_SKY_BG, outline=C_SKY_BORDER, width=1)
        foot_txt = "Criterio Cuantitativo: Probabilidades Poisson bivariado con contracción bayesiana y Dixon-Coles. Juega con responsabilidad."
        if _rec_ticket.get("aplicado"):
            foot_txt = ("Criterio Cuantitativo: red de factores con pesos fijos "
                        "(Bilinear Pooling + Cross-Attention + Monte Carlo 100.000). "
                        "Juega con responsabilidad.")
        y_foot_text = y_c + 8 if linea_cluster else y_c + 12
        d.text((PAD_X + 16, y_foot_text), foot_txt, font=f_footer, fill=C_SKY_TXT)
        if linea_cluster:
            d.text((PAD_X + 16, y_c + 28), _sanear_para_canvas(linea_cluster), font=f_footer, fill=C_NAVY_HDR)

        if destino is None:
            os.makedirs(MERCADOS_DIR, exist_ok=True)
            id_trabajo = _val(analisis, "id_trabajo") or _ahora().replace(" ", "_").replace(":", "")
            destino = os.path.join(MERCADOS_DIR, f"ticket_{_slug(eq_l)}_vs_{_slug(eq_v)}_{id_trabajo}.png")
        else:
            os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)

        img.save(destino, "PNG", optimize=True)
        return destino
    finally:
        img.close()
        import gc
        gc.collect()


renderizar_ticket_png = renderizar_ticket_mercados_png


def _resumen_motor_recursivo(analisis: Any) -> dict[str, Any]:
    """
    Resumen serializable de la RED NEURONAL RECURSIVA (45 variables).

    Se guarda en el JSON de datos del análisis: sin esto, el sesgo del "1-1 ★"
    podría volver sin que nada en disco delatara qué matriz usaron los mercados
    SEGUROS, el ticket y las dos láminas.
    """
    motor = getattr(analisis, "motor", None)
    rec = getattr(motor, "recursivo", None) or {}
    if not rec.get("aplicado"):
        return {"aplicado": False,
                "motivo": rec.get("error") or rec.get("motivo")
                          or "no aplicado (ver notas del motor)"}
    claves = (
        "lambda_base_local", "lambda_base_visita",
        "lambda_local_recursivo", "lambda_visita_recursivo",
        "iteracion_convergencia", "historial_entropia",
        "cruce_dominante_local", "cruce_dominante_visita",
        "intensidad_cruce_local", "intensidad_cruce_visita",
        "series_reales_por_equipo", "patrones_local", "patrones_visita",
        "tasa_explosion_3mas_local", "tasa_explosion_3mas_visita",
        "tasa_sequia_local", "tasa_sequia_visita",
        "tasas_por_equipo",
        "sello_verificacion_visual",
        "traza_lambda",
    )
    resumen = {clave: rec.get(clave) for clave in claves}
    resumen["aplicado"] = True
    resumen["matriz_es_la_de_los_mercados"] = True
    try:
        import motor_mercados as mm
        resumen["lineas_trazabilidad"] = mm._lineas_motor_recursivo(motor)
    except Exception:
        resumen["lineas_trazabilidad"] = []
    return resumen


def _verificar_red_neuronal(
    analisis: Any,
    doc_local: dict[str, Any],
    doc_visitante: dict[str, Any],
) -> dict[str, Any]:
    """
    Recalcula la RED NEURONAL RECURSIVA con la MISMA λ plana que usó el motor y
    certifica que el texto y la imagen comparten exactamente la misma matriz.

    Se llama ANTES de renderizar el ticket y las láminas. El motor ya la aplicó
    dentro de `preparar()` (que corre antes de `evaluar_catalogo`), así que esto
    es una comprobación determinista: mismo resultado o discrepancia declarada.
    """
    motor = getattr(analisis, "motor", None)
    if motor is None:
        return {"aplicado": False, "motivo": "analisis sin motor"}
    rec = getattr(motor, "recursivo", None) or {}
    if rec.get("aplicado"):
        # El motor ya aplicó la red recursiva y la sincronización de los 55 patrones,
        # rachas 60/40, contexto avanzado y freno de mano en crisis; se preserva íntegra.
        return dict(rec, diferencia_matriz=0.0)
    try:
        import cuadro_especialista as ce
        import motor_recursivo_45_dcn as red

        lam_l = ce._lambda_plano(motor, "local")
        lam_v = ce._lambda_plano(motor, "visita")
        if lam_l is None or lam_v is None:
            return {"aplicado": False, "motivo": "λ plana no disponible"}
        perfil_l = ce.construir_perfil_especialista_equipo(
            ce.bloque_desde_muestra(motor.muestras["local_general"],
                                    motor.nombre_local, "general"))
        perfil_v = ce.construir_perfil_especialista_equipo(
            ce.bloque_desde_muestra(motor.muestras["visitante_general"],
                                    motor.nombre_visitante, "general"))
        recalculo = red.ejecutar_bucle_recursivo_infinito_hasta_convergencia(
            perfil_l, perfil_v, float(lam_l), float(lam_v),
            ce.partidos_para_red_neuronal(doc_local.get("partidos"), motor.nombre_local),
            ce.partidos_para_red_neuronal(doc_visitante.get("partidos"),
                                          motor.nombre_visitante),
        )
    except Exception as e:
        print(f"[SERVICIO MERCADOS] ⚠️ No se pudo recalcular la red recursiva: "
              f"{type(e).__name__}: {e}")
        return {"aplicado": False, "motivo": f"{type(e).__name__}: {e}"}

    if not rec.get("aplicado"):
        # El motor no la aplicó: se aplica aquí para que JSON e imagen coincidan,
        # y se avisa de que los mercados ya se evaluaron con la matriz lineal.
        print("[SERVICIO MERCADOS] ⚠️ El motor no aplicó la red; se certifica aquí "
              "la matriz para las láminas (los mercados quedaron con la lineal).")
        motor.tasas.lambda_local_ft = float(recalculo["lambda_local_recursivo"])
        motor.tasas.lambda_visitante_ft = float(recalculo["lambda_visita_recursivo"])
        motor.matriz_ft = ce.matriz_red_para_dibujar(recalculo)
        motor.recursivo = dict(recalculo, aplicado=True,
                               lambda_base_local=round(float(lam_l), 4),
                               lambda_base_visita=round(float(lam_v), 4),
                               aviso="aplicada en servicio_mercados")
        try:
            import motor_mercados as mm
            asedio_act = bool(recalculo.get("asedio_bandas_activo", False))
            motor.clusters = mm.clusters_marcador(
                motor.matriz_ft, motor.nombre_local, motor.nombre_visitante,
                asedio_bandas=asedio_act,
                info_cruces=recalculo.get("cruces_tacticos"),
            )
        except Exception:
            pass
        return dict(motor.recursivo)

    # Misma entrada -> mismo resultado: se comprueba y, si difiere, manda el motor.
    diferencia = 0.0
    matriz_motor = motor.matriz_ft
    matriz_red = ce.matriz_red_para_dibujar(recalculo)
    for i in range(min(len(matriz_motor), len(matriz_red))):
        for j in range(min(len(matriz_motor[i]), len(matriz_red[i]))):
            diferencia = max(diferencia, abs(float(matriz_motor[i][j])
                                             - float(matriz_red[i][j])))
    if diferencia > 1e-9:
        print(f"[SERVICIO MERCADOS] ⚠️ La matriz del motor difiere de la red en "
              f"{diferencia:.2e}: se usa la de la red para texto e imagen.")
        motor.matriz_ft = ce.matriz_red_para_dibujar(recalculo)
        motor.tasas.lambda_local_ft = float(recalculo["lambda_local_recursivo"])
        motor.tasas.lambda_visitante_ft = float(recalculo["lambda_visita_recursivo"])
        motor.recursivo = dict(recalculo, aplicado=True,
                               lambda_base_local=round(float(lam_l), 4),
                               lambda_base_visita=round(float(lam_v), 4))
        try:
            import motor_mercados as mm
            asedio_act = bool(recalculo.get("asedio_bandas_activo", False))
            motor.clusters = mm.clusters_marcador(
                motor.matriz_ft, motor.nombre_local, motor.nombre_visitante,
                asedio_bandas=asedio_act,
                info_cruces=recalculo.get("cruces_tacticos"),
            )
        except Exception:
            pass
    return dict(rec, diferencia_matriz=diferencia)


def preparar_analisis(
    texto_o_equipo_a: str,
    equipo_b: str | None = None,
    limite: int = LIMITE_PARTIDOS_POR_DEFECTO,
    umbral: float | None = None,
    w_condicion: float | None = None,
    w_xg: float | None = None,
    forzar_datos: bool = False,
    headless: bool = True,
    directorio_salida: str | None = None,
    progreso: Any = None,
    liga: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """
    Flujo completo hasta «mensaje preparado»: resuelve reportes, analiza, guarda el
    informe técnico, genera el ticket gráfico PNG estructurado y deja el envío listo.
    NO envía nada.
    """
    contexto = resolver_enfrentamiento(texto_o_equipo_a, equipo_b, limite=limite,
                                       forzar=forzar_datos, headless=headless,
                                       progreso=progreso, liga=liga)
    params = Parametros(umbral=umbral if umbral is not None else Parametros().umbral,
                        w_condicion=(w_condicion if w_condicion is not None
                                     else Parametros().w_condicion),
                        w_xg=(w_xg if w_xg is not None else Parametros().w_xg))
    if params.distribucion_conteos == "binomial_negativa":
        params.distribucion_conteos = "poisson"   # la central usa Poisson (documentado)

    ctx = contexto["contexto"]
    if (
        isinstance(ctx, dict)
        and ctx.get("hora_inicio_partido") is None
        and not ctx.get("partido_ya_jugado")
        and (ctx.get("fixture_confirmado") or ctx.get("id_partido") or ctx.get("id_flashscore_partido"))
    ):
        resolver_hora_inicio_encuentro(
            ctx,
            equipo_local=str(contexto.get("equipo_local") or ""),
            equipo_visitante=str(contexto.get("equipo_visitante") or ""),
            id_flashscore=ctx.get("id_flashscore_partido"),
        )

    # ── Bloque A2: Si el partido objetivo ya se jugó o su hora de inicio ya pasó,
    # /mercados NO genera pronóstico prepartido: responde "partido ya jugado, usa /evaluar" y muestra el resultado.
    if ctx.get("partido_ya_jugado"):
        eq_l = contexto["equipo_local"]
        eq_v = contexto["equipo_visitante"]
        f_jug = ctx.get("fecha_jugado") or ctx.get("fecha_partido") or ""
        res_txt = ctx.get("resultado_partido")
        if not res_txt or res_txt == "Finalizado":
            g_l = ctx.get("goles_local")
            g_v = ctx.get("goles_visitante")
            if g_l is not None and g_v is not None:
                res_txt = f"{eq_l} {g_l} - {g_v} {eq_v}"
            else:
                res_txt = f"{eq_l} vs {eq_v}"

        texto_partido_jugado = (
            f"⚠️ Partido ya jugado, usa /evaluar\n"
            f"Resultado: {res_txt} ({f_jug} · Finalizado)"
        )
        meta_jugado = {
            "equipo_local": eq_l,
            "equipo_visitante": eq_v,
            "competicion": ctx.get("competicion", "Competición oficial"),
            "fecha_partido": f_jug,
            "generado_en": _ahora(),
            "abstenido": True,
            "partido_ya_jugado": True,
            "bloqueado_por_cobertura": False,
            "estado_cobertura": "partido_ya_jugado",
            "motivo_abstencion": "partido_ya_jugado",
            "resultado_partido": res_txt,
            "fuente": "Filtro prepartido (A2)",
            "red_neuronal": {"aplicado": False, "motivo": "Partido ya jugado"},
        }
        class _TasasZeroA2:
            lambda_local_ft = 0.0
            lambda_visitante_ft = 0.0
            lambda_local_1t = 0.0
            lambda_visitante_1t = 0.0
            lambda_local_2t = 0.0
            lambda_visitante_2t = 0.0
            procedencia_tiempos = "partido_ya_jugado"

        class _MotorZeroA2:
            tasas = _TasasZeroA2()
            residual_ft = 0.0
            matriz_ft = []
            muestras = {
                "local_general": type("M", (), {"partidos_general": []})(),
                "visitante_general": type("M", (), {"partidos_general": []})(),
                "local_cond": type("M", (), {"partidos_condicion": []})(),
                "visitante_cond": type("M", (), {"partidos_condicion": []})(),
            }

        analisis_jugado = type("AnalisisPartidoJugadoObj", (), {
            "metadatos": meta_jugado,
            "parametros": params,
            "motor": _MotorZeroA2(),
            "selecciones_filtradas": [],
            "mercados": [],
            "informe": texto_partido_jugado,
            "abstenido": True,
            "partido_ya_jugado": True,
            "estado_cobertura": "partido_ya_jugado",
            "motivo_abstencion": "partido_ya_jugado",
            "grupos": {"mantiene": [], "sensible": []},
        })()
        return {
            "analisis": analisis_jugado,
            "id_trabajo": "partido_ya_jugado",
            "rutas": {},
            "contexto": contexto,
            "abstenido": True,
            "partido_ya_jugado": True,
            "estado_cobertura": "partido_ya_jugado",
            "motivo_abstencion": "partido_ya_jugado",
            "decision_cobertura": {},
            "analisis_texto_3_parrafos": texto_partido_jugado,
            "ruta_imagen": None,
        }

    # ── Metadatos de fuente por club y firma de datos (Ronda 19 Bloque 1) ──
    _info_f_datos = extraer_fuentes_y_firma_datos(contexto.get("doc_local"), contexto.get("doc_visitante"))
    contexto["fuente_local"] = _info_f_datos["fuente_local"]
    contexto["fuente_visitante"] = _info_f_datos["fuente_visitante"]
    contexto["firma_datos"] = _info_f_datos["firma_datos"]
    contexto["calidad_fuente_local"] = _info_f_datos["calidad_fuente_local"]
    contexto["calidad_fuente_visitante"] = _info_f_datos["calidad_fuente_visitante"]
    _fechas_dh = extraer_fechas_datos_hasta(contexto.get("doc_local"), contexto.get("doc_visitante"), ctx)
    contexto["datos_hasta_local"] = _fechas_dh["datos_hasta_local"]
    contexto["datos_hasta_visitante"] = _fechas_dh["datos_hasta_visitante"]
    contexto["datos_hasta"] = _fechas_dh["datos_hasta"]

    # ── Ronda 22 Bloque 2: Abstención por FALSO HOMÓNIMO EXTRANJERO y datos_desactualizados ──
    _abstencion_calidad_estado: str | None = None
    _abstencion_calidad_motivo: str = ""
    _abstencion_calidad_detalle: dict[str, Any] = {}

    _coh_ctx = ctx.get("coherencia_competitiva")
    if not isinstance(_coh_ctx, dict) or not _coh_ctx:
        _pais_coh = str(
            ctx.get("pais_iso")
            or (liga.get("pais_iso") if isinstance(liga, dict) else "")
            or ""
        ).strip()
        if _pais_coh and _pais_coh.upper() != "INT":
            try:
                _coh_ctx = dict(
                    validar_coherencia_competitiva_local_visita(
                        contexto["doc_local"],
                        contexto["doc_visitante"],
                        pais_iso=_pais_coh,
                        liga_pref=liga,
                    )
                )
            except Exception:
                _coh_ctx = {}
    if (
        isinstance(_coh_ctx, dict)
        and not _coh_ctx.get("coherente", True)
        and str(_coh_ctx.get("estado") or "").strip().upper() == "FALSO HOMÓNIMO EXTRANJERO"
    ):
        _abstencion_calidad_estado = "falso_homonimo_extranjero"
        _abstencion_calidad_motivo = str(
            _coh_ctx.get("motivo")
            or "FALSO HOMÓNIMO EXTRANJERO detectado: los historiales no pertenecen a la misma liga ni país."
        )
        _abstencion_calidad_detalle = {"coherencia_competitiva": _coh_ctx}

    if _abstencion_calidad_estado is None:
        _f_ref_antig = (
            str(ctx.get("fecha_corte") or "").strip()[:10]
            or str(ctx.get("cutoff_timestamp") or "").strip()[:10]
            or (
                str(ctx.get("fecha_partido") or "").strip()[:10]
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(ctx.get("fecha_partido") or "").strip()[:10])
                else ""
            )
            or None
        )
        _antig = evaluar_antiguedad_datos(
            contexto.get("doc_local"),
            contexto.get("doc_visitante"),
            fecha_referencia=_f_ref_antig,
            max_dias=MAX_DIAS_ANTIGUEDAD_ULTIMO_PARTIDO,
        )
        contexto["antiguedad_datos"] = _antig
        if _antig.get("desactualizado"):
            _abstencion_calidad_estado = "datos_desactualizados"
            _abstencion_calidad_motivo = str(_antig.get("motivo") or "Datos desactualizados.")
            _abstencion_calidad_detalle = {"antiguedad_datos": _antig}

    if _abstencion_calidad_estado is not None:
        meta_abs_cal = {
            "equipo_local": contexto["equipo_local"],
            "equipo_visitante": contexto["equipo_visitante"],
            "competicion": ctx.get("competicion", "Competición oficial"),
            "fecha_partido": ctx.get("fecha_partido"),
            "etiqueta_fecha": ctx.get("etiqueta_fecha") or formatear_etiqueta_fecha_encabezado(ctx),
            "hora_inicio_partido": ctx.get("hora_inicio_partido"),
            "hora_inicio_local": ctx.get("hora_inicio_local"),
            **({"fuente_hora_inicio": ctx.get("fuente_hora_inicio")} if ctx.get("fuente_hora_inicio") else {}),
            **({"zona_origen_hora_inicio": ctx.get("zona_origen_hora_inicio")} if ctx.get("zona_origen_hora_inicio") else {}),
            "fixture_confirmado": bool(ctx.get("fixture_confirmado", False)),
            "fuente_fecha": ctx.get("fuente_fecha") or ("fixture" if ctx.get("fixture_confirmado") else "fallback"),
            "generado_en": _ahora(),
            "abstenido": True,
            "bloqueado_por_cobertura": True,
            "estado_cobertura": _abstencion_calidad_estado,
            "motivo_abstencion": _abstencion_calidad_motivo,
            "decision_cobertura": {"estado": _abstencion_calidad_estado, **_abstencion_calidad_detalle},
            "fuente": "Guarda Calidad de Datos",
            "fuente_local": _info_f_datos["fuente_local"],
            "fuente_visitante": _info_f_datos["fuente_visitante"],
            "firma_datos": _info_f_datos["firma_datos"],
            "datos_hasta_local": _fechas_dh["datos_hasta_local"],
            "datos_hasta_visitante": _fechas_dh["datos_hasta_visitante"],
            "datos_hasta": _fechas_dh["datos_hasta"],
            "red_neuronal": {"aplicado": False, "motivo": f"Abstenido ({_abstencion_calidad_estado})"},
            **_abstencion_calidad_detalle,
        }
        _p_cal_tg, _f_cal_tg = formatear_encabezado_partido_y_fuentes_telegram(
            meta_abs_cal,
            contexto.get("doc_local"),
            contexto.get("doc_visitante"),
            equipo_local=contexto["equipo_local"],
            equipo_visitante=contexto["equipo_visitante"],
        )
        texto_cal = (
            f"🚨 PRONÓSTICO ABSTENIDO ({_abstencion_calidad_estado.upper()})\n"
            f"📅 {_p_cal_tg}\n"
            f"📡 {_f_cal_tg}\n\n"
            f"{_abstencion_calidad_motivo}\n\n"
            f"⚠️ No se publican probabilidades ni selecciones hasta contar con historiales coherentes y vigentes.\n"
            f"🏷️ {__import__('utilidades_seguridad').obtener_sello_build()}"
        )
        analisis_abs_cal = type("AnalisisAbstenidoCalidadObj", (), {
            "metadatos": meta_abs_cal,
            "parametros": params,
            "selecciones_filtradas": [],
            "mercados": [],
            "informe": texto_cal,
            "mensaje": texto_cal,
            "abstenido": True,
            "estado_cobertura": _abstencion_calidad_estado,
            "motivo_abstencion": _abstencion_calidad_motivo,
            "grupos": {"mantiene": [], "sensible": []},
        })()
        id_trabajo_cal = identificar_trabajo(meta_abs_cal, contexto["doc_local"], contexto["doc_visitante"])
        return {
            "analisis": analisis_abs_cal,
            "id_trabajo": id_trabajo_cal,
            "rutas": {},
            "contexto": contexto,
            "abstenido": True,
            "estado_cobertura": _abstencion_calidad_estado,
            "motivo_abstencion": _abstencion_calidad_motivo,
            "decision_cobertura": meta_abs_cal["decision_cobertura"],
            "analisis_texto_3_parrafos": texto_cal,
            "ruta_imagen": None,
        }

    # ── Puntos 44 y 45: Decisión de Cobertura Comparable y Precedencia D.1 ──
    from decision_cobertura import evaluar_decision_cobertura
    doc_l_eval = dict(contexto["doc_local"])
    doc_v_eval = dict(contexto["doc_visitante"])
    if not contexto.get("identidad_verificada", True) or contexto.get("estado_identidad") == "identidad_no_verificada":
        from resolvedor_entidades import obtener_seleccion_nacional
        nom_l = (doc_l_eval.get("equipo") or {}).get("nombre") or contexto["equipo_local"]
        nom_v = (doc_v_eval.get("equipo") or {}).get("nombre") or contexto["equipo_visitante"]
        id_l = (doc_l_eval.get("equipo") or {}).get("id")
        id_v = (doc_v_eval.get("equipo") or {}).get("id")
        n_l = len(doc_l_eval.get("partidos") or [])
        n_v = len(doc_v_eval.get("partidos") or [])
        if (obtener_seleccion_nacional(nom_l) and obtener_seleccion_nacional(nom_v)) or (id_l and id_v and n_l >= 15 and n_v >= 15):
            doc_l_eval["estado_identidad"] = "verificada"
            doc_l_eval["identidad_verificada"] = True
            doc_v_eval["estado_identidad"] = "verificada"
            doc_v_eval["identidad_verificada"] = True
        else:
            doc_l_eval["estado_identidad"] = "identidad_no_verificada"
            doc_l_eval["identidad_verificada"] = False

    decision_cob = evaluar_decision_cobertura(doc_l_eval, doc_v_eval)

    if not decision_cob.publicar_probabilidades:
        # Estado de bloqueo (identidad_no_verificada, historial_oficial_incompleto, solo_resultados, cobertura_insuficiente)
        meta_abs = {
            "equipo_local": contexto["equipo_local"],
            "equipo_visitante": contexto["equipo_visitante"],
            "competicion": ctx.get("competicion", "Competición oficial"),
            "fecha_partido": ctx.get("fecha_partido"),
            "etiqueta_fecha": ctx.get("etiqueta_fecha") or formatear_etiqueta_fecha_encabezado(ctx),
            "hora_inicio_partido": ctx.get("hora_inicio_partido"),
            "hora_inicio_local": ctx.get("hora_inicio_local"),
            **({"fuente_hora_inicio": ctx.get("fuente_hora_inicio")} if ctx.get("fuente_hora_inicio") else {}),
            **({"zona_origen_hora_inicio": ctx.get("zona_origen_hora_inicio")} if ctx.get("zona_origen_hora_inicio") else {}),
            "fixture_confirmado": bool(ctx.get("fixture_confirmado", False)),
            "fuente_fecha": ctx.get("fuente_fecha") or ("fixture" if ctx.get("fixture_confirmado") else "fallback"),
            "generado_en": _ahora(),
            "abstenido": True,
            "bloqueado_por_cobertura": True,
            "estado_cobertura": decision_cob.estado,
            "motivo_abstencion": decision_cob.alerta_descriptiva,
            "decision_cobertura": decision_cob.a_dict(),
            "etiqueta_cobertura_local": decision_cob.etiqueta_cobertura_a,
            "etiqueta_cobertura_visitante": decision_cob.etiqueta_cobertura_b,
            "fuente": "Auditoría Cobertura D.1",
            "fuente_local": _info_f_datos["fuente_local"],
            "fuente_visitante": _info_f_datos["fuente_visitante"],
            "firma_datos": _info_f_datos["firma_datos"],
            "datos_hasta_local": _fechas_dh["datos_hasta_local"],
            "datos_hasta_visitante": _fechas_dh["datos_hasta_visitante"],
            "datos_hasta": _fechas_dh["datos_hasta"],
            "red_neuronal": {"aplicado": False, "motivo": f"Abstenido por cobertura ({decision_cob.estado})"},
        }

        informe_md = (
            f"# INFORME DE AUDITORÍA: ABSTENCIÓN POR COBERTURA INSUFICIENTE\n\n"
            f"**Encuentro:** {contexto['equipo_local']} vs {contexto['equipo_visitante']}\n"
            f"**Competición:** {ctx.get('competicion', 'Competición oficial')}\n"
            f"**Estado Oficial:** `{decision_cob.estado}`\n\n"
            f"### Cobertura de Historiales Oficiales:\n"
            f"- **{contexto['equipo_local']}:** {decision_cob.etiqueta_cobertura_a}\n"
            f"- **{contexto['equipo_visitante']}:** {decision_cob.etiqueta_cobertura_b}\n\n"
            f"### Justificación Técnica:\n"
            f"{decision_cob.alerta_descriptiva}\n\n"
            f"> **GARANTÍA CUANTITATIVA (Puntos 44 y 45):**\n"
            f"> Se suspende la publicación de probabilidades, matrices de Poisson, cuotas de valor y stakes Kelly "
            f"> para salvaguardar la integridad matemática del sistema ante la falta de evidencia homogénea suficiente.\n"
        )

        class _TasasZero:
            lambda_local_ft = 0.0
            lambda_visitante_ft = 0.0
            lambda_local_1t = 0.0
            lambda_visitante_1t = 0.0
            lambda_local_2t = 0.0
            lambda_visitante_2t = 0.0
            procedencia_tiempos = "abstenido_por_cobertura"

        class _MotorZero:
            tasas = _TasasZero()
            residual_ft = 0.0
            matriz_ft = []
            muestras = {
                "local_general": type("M", (), {"partidos_general": []})(),
                "visitante_general": type("M", (), {"partidos_general": []})(),
                "local_cond": type("M", (), {"partidos_condicion": []})(),
                "visitante_cond": type("M", (), {"partidos_condicion": []})(),
            }

        analisis_abstenido = type("AnalisisAbstenidoObj", (), {
            "metadatos": meta_abs,
            "parametros": params,
            "motor": _MotorZero(),
            "selecciones_filtradas": [],
            "mercados": [],
            "informe": informe_md,
            "abstenido": True,
            "estado_cobertura": decision_cob.estado,
            "motivo_abstencion": decision_cob.alerta_descriptiva,
            "grupos": {"mantiene": [], "sensible": []},
        })()

        id_trabajo = identificar_trabajo(meta_abs, contexto["doc_local"], contexto["doc_visitante"])
        fecha_dir = datetime.now().strftime("%Y-%m-%d")
        base_dir = directorio_salida or os.path.join(MERCADOS_DIR, fecha_dir)
        os.makedirs(base_dir, exist_ok=True)
        raiz = os.path.join(base_dir, f"mercados_{_slug(contexto['equipo_local'])}_vs_"
                                      f"{_slug(contexto['equipo_visitante'])}_{id_trabajo}")
        rutas = {"informe": raiz + ".md", "datos": raiz + ".json"}
        with open(rutas["informe"], "w", encoding="utf-8") as f:
            f.write(informe_md)
        with open(rutas["datos"], "w", encoding="utf-8") as f:
            json.dump({
                "metadatos": meta_abs,
                "id_trabajo": id_trabajo,
                "abstenido": True,
                "estado_cobertura": decision_cob.estado,
                "motivo_abstencion": decision_cob.alerta_descriptiva,
                "decision_cobertura": decision_cob.a_dict(),
                "selecciones_filtradas": [],
                "mercados": [],
            }, f, ensure_ascii=False, indent=1)

        ruta_imagen = raiz + ".png"
        try:
            renderizar_ticket_mercados_png(analisis_abstenido, metadatos=meta_abs,
                                           umbral=params.umbral, destino=ruta_imagen)
            rutas["imagen"] = ruta_imagen
        except Exception as e_img:
            print(f"[SERVICIO MERCADOS] ⚠️ No se pudo generar el ticket PNG de abstención: {e_img}")
            ruta_imagen = None

        _p_abs_tg, _f_abs_tg = formatear_encabezado_partido_y_fuentes_telegram(
            meta_abs,
            contexto.get("doc_local"),
            contexto.get("doc_visitante"),
            equipo_local=contexto["equipo_local"],
            equipo_visitante=contexto["equipo_visitante"],
        )
        texto_alerta = (
            f"🚨 PRONÓSTICO ABSTENIDO POR COBERTURA INSUFICIENTE\n"
            f"📅 {_p_abs_tg}\n"
            f"📡 {_f_abs_tg}\n"
            f"Estado Oficial: {decision_cob.estado.upper()}\n\n"
            f"• {contexto['equipo_local']}: {decision_cob.etiqueta_cobertura_a}\n"
            f"• {contexto['equipo_visitante']}: {decision_cob.etiqueta_cobertura_b}\n\n"
            f"{decision_cob.alerta_descriptiva}\n\n"
            f"⚠️ Por política cuantitativa estricta, no se publican probabilidades ni matrices sin evidencia estadística suficiente.\n"
            f"🏷️ {__import__('utilidades_seguridad').obtener_sello_build()}"
        )

        return {
            "analisis": analisis_abstenido,
            "id_trabajo": id_trabajo,
            "rutas": rutas,
            "contexto": contexto,
            "abstenido": True,
            "estado_cobertura": decision_cob.estado,
            "motivo_abstencion": decision_cob.alerta_descriptiva,
            "decision_cobertura": decision_cob.a_dict(),
            "analisis_texto_3_parrafos": texto_alerta,
            "ruta_imagen": ruta_imagen,
        }

    analisis: Analisis = analizar(
        contexto["doc_local"], contexto["doc_visitante"],
        contexto["equipo_local"], contexto["equipo_visitante"],
        fecha_partido=ctx.get("fecha_partido"),
        competicion=ctx.get("competicion"),
        sede=ctx.get("sede"),
        condicion_local=ctx.get("condicion_local"),
        condicion_visitante=ctx.get("condicion_visitante"),
        parametros=params, aviso=progreso,
        id_partido_excluir=ctx.get("id_partido"),
    )

    analisis.doc_local = contexto.get("doc_local")
    analisis.doc_visitante = contexto.get("doc_visitante")
    analisis.metadatos["fuente_local"] = _info_f_datos["fuente_local"]
    analisis.metadatos["fuente_visitante"] = _info_f_datos["fuente_visitante"]
    analisis.metadatos["firma_datos"] = _info_f_datos["firma_datos"]
    if ctx.get("advertencia_liga"):
        analisis.metadatos["advertencia_liga"] = ctx["advertencia_liga"]
    for _k_fec in ("etiqueta_fecha", "hora_inicio_partido", "hora_inicio_local", "fuente_hora_inicio", "zona_origen_hora_inicio", "hora_inicio_fotmob", "hora_inicio_flashscore", "diferencia_hora_inicio_min", "hora_inicio_discrepante", "id_flashscore_partido", "fixture_confirmado", "fuente_fecha", "es_fecha_analisis", "partido_activo", "datos_hasta_local", "datos_hasta_visitante", "datos_hasta", "ronda", "etapa", "formato"):
        if _k_fec in ctx:
            analisis.metadatos[_k_fec] = ctx.get(_k_fec)
    for _k_fix_opt in ("ronda", "etapa", "formato"):
        analisis.metadatos.setdefault(_k_fix_opt, ctx.get(_k_fix_opt) if _k_fix_opt in ctx else None)
    analisis.metadatos.setdefault("datos_hasta_local", _fechas_dh["datos_hasta_local"])
    analisis.metadatos.setdefault("datos_hasta_visitante", _fechas_dh["datos_hasta_visitante"])
    analisis.metadatos.setdefault("datos_hasta", _fechas_dh["datos_hasta"])
    analisis.metadatos["calidad_fuente_local"] = _info_f_datos["calidad_fuente_local"]
    analisis.metadatos["calidad_fuente_visitante"] = _info_f_datos["calidad_fuente_visitante"]
    analisis.metadatos.setdefault("fixture_confirmado", bool(ctx.get("fixture_confirmado", False)))
    analisis.metadatos.setdefault("fuente_fecha", "fixture" if analisis.metadatos.get("fixture_confirmado") else "fallback")
    analisis.metadatos.setdefault("etiqueta_fecha", formatear_etiqueta_fecha_encabezado(analisis.metadatos))

    analisis.metadatos["decision_cobertura"] = decision_cob.a_dict()
    from utilidades_seguridad import obtener_sello_build as _obtener_sello_build
    analisis.metadatos["build"] = _obtener_sello_build()
    analisis.metadatos["estado_cobertura"] = decision_cob.estado
    analisis.metadatos["etiqueta_cobertura_local"] = decision_cob.etiqueta_cobertura_a
    analisis.metadatos["etiqueta_cobertura_visitante"] = decision_cob.etiqueta_cobertura_b

    if decision_cob.estado == "cobertura_parcial":
        mercados_permitidos = {m for m, ok in decision_cob.mercados_habilitados.items() if ok}
        analisis.selecciones_filtradas = [
            s for s in analisis.selecciones_filtradas
            if getattr(s, "mercado", None) in mercados_permitidos
        ]
        if hasattr(analisis, "grupos") and isinstance(analisis.grupos, dict):
            for g_k in ("mantiene", "sensible"):
                if g_k in analisis.grupos:
                    analisis.grupos[g_k] = [
                        s for s in analisis.grupos[g_k]
                        if getattr(s, "mercado", None) in mercados_permitidos
                    ]

    # ── RED NEURONAL RECURSIVA: misma matriz para texto e imagen ─────────────
    # El motor ya la aplicó en `preparar()` (antes de evaluar los mercados); aquí
    # se recalcula con la misma λ plana y se certifica que coinciden. Así el
    # mensaje, el ticket y las dos láminas no pueden divergir.
    red_verificada = _verificar_red_neuronal(
        analisis, contexto["doc_local"], contexto["doc_visitante"])
    if red_verificada.get("aplicado"):
        analisis.metadatos["red_neuronal"] = {
            "lambda_local_recursivo": red_verificada.get("lambda_local_recursivo"),
            "lambda_visita_recursivo": red_verificada.get("lambda_visita_recursivo"),
            "lambda_base_local": red_verificada.get("lambda_base_local"),
            "lambda_base_visita": red_verificada.get("lambda_base_visita"),
            "iteracion_convergencia": red_verificada.get("iteracion_convergencia"),
            "sello_verificacion_visual": red_verificada.get("sello_verificacion_visual"),
        }
    else:
        analisis.metadatos["red_neuronal"] = {"aplicado": False,
                                              "motivo": red_verificada.get("motivo")}

    id_trabajo = identificar_trabajo(analisis.metadatos, contexto["doc_local"],
                                     contexto["doc_visitante"])
    fecha_dir = datetime.now().strftime("%Y-%m-%d")
    base_dir = directorio_salida or os.path.join(MERCADOS_DIR, fecha_dir)
    os.makedirs(base_dir, exist_ok=True)
    raiz = os.path.join(base_dir, f"mercados_{_slug(contexto['equipo_local'])}_vs_"
                                  f"{_slug(contexto['equipo_visitante'])}_{id_trabajo}")
    rutas = {"informe": raiz + ".md", "datos": raiz + ".json"}
    with open(rutas["informe"], "w", encoding="utf-8") as f:
        f.write(analisis.informe)
    with open(rutas["datos"], "w", encoding="utf-8") as f:
        json.dump({
            "metadatos": analisis.metadatos,
            "id_trabajo": id_trabajo,
            "parametros": {"umbral": params.umbral, "w_condicion": params.w_condicion,
                           "w_xg": params.w_xg,
                           "distribucion_conteos": params.distribucion_conteos},
            "lambda": {
                "local_ft": analisis.motor.tasas.lambda_local_ft,
                "visitante_ft": analisis.motor.tasas.lambda_visitante_ft,
                "local_1t": analisis.motor.tasas.lambda_local_1t,
                "visitante_1t": analisis.motor.tasas.lambda_visitante_1t,
                "local_2t": analisis.motor.tasas.lambda_local_2t,
                "visitante_2t": analisis.motor.tasas.lambda_visitante_2t,
                "procedencia_tiempos": analisis.motor.tasas.procedencia_tiempos,
            },
            "masa_residual_ft": analisis.motor.residual_ft,
            # Red Neuronal Recursiva: matriz EFECTIVA con la que se seleccionaron
            # los mercados SEGUROS y se dibujaron las dos láminas.
            "traza_lambda": (
                red_verificada.get("traza_lambda")
                or getattr(analisis.motor, "traza_lambda", [])
                or []
            ),
            "motor_recursivo": _resumen_motor_recursivo(analisis),
            "red_neuronal_recursiva": red_verificada,
            "selecciones_filtradas": [
                {"mercado": s.mercado, "evento": s.evento, "etiqueta": s.etiqueta,
                 "periodo": s.periodo, "equipo": s.equipo, "linea": s.linea,
                 "probabilidad": s.probabilidad, "estado": s.estado,
                 "escenarios": s.escenarios}
                for s in analisis.selecciones_filtradas
            ],
            "mercados": [
                {"id": r.id_mercado, "numero": r.numero, "nombre": r.nombre,
                 "estado": r.estado, "nota": r.nota,
                 "selecciones": [
                      {"evento": s.evento, "etiqueta": s.etiqueta, "periodo": s.periodo,
                       "equipo": s.equipo, "linea": s.linea,
                       "probabilidad": s.probabilidad, "estimable": s.estimable}
                      for s in r.selecciones]}
                for r in analisis.mercados
            ],
        }, f, ensure_ascii=False, indent=1)

    try:
        from banco_historico import guardar_en_banco_no_bloqueante
        guardar_en_banco_no_bloqueante(
            doc_local=contexto.get("doc_local"),
            doc_visitante=contexto.get("doc_visitante"),
        )
    except Exception as _e_banco:
        print(f"[BANCO HISTORICO] ⚠️ Hook /mercados omitido: {_e_banco}")

    _h2h_inf_tkt = construir_resumen_h2h_ticket(
        contexto.get("doc_local"),
        contexto.get("doc_visitante"),
        contexto["equipo_local"],
        contexto["equipo_visitante"],
        fecha_objetivo=contexto.get("fecha_partido"),
    )
    if _h2h_inf_tkt and isinstance(getattr(analisis, "metadatos", None), dict):
        analisis.metadatos["h2h_reciente_ticket"] = _h2h_inf_tkt

    ruta_imagen = raiz + ".png"
    try:
        renderizar_ticket_mercados_png(analisis, metadatos=analisis.metadatos,
                                       umbral=params.umbral, destino=ruta_imagen)
        rutas["imagen"] = ruta_imagen
    except Exception as e_img:
        print(f"[SERVICIO MERCADOS] ⚠️ No se pudo generar el ticket PNG: {e_img}")
        ruta_imagen = None

    # ── Cuadro Especialista (ratings 0-100 + tempo + mapa de calor Dixon-Coles) ──
    # Se guarda en informes/h2h/<fecha>/, como pidió el flujo, y se envía como
    # segunda imagen. Nunca interrumpe el análisis: si falla, queda el aviso.
    ruta_cuadro = None
    try:
        import cuadro_especialista as cuadro_mod

        carpeta_cuadro = os.path.join(H2H_DIR, fecha_dir)
        os.makedirs(carpeta_cuadro, exist_ok=True)
        destino_cuadro = os.path.join(
            carpeta_cuadro,
            f"cuadro_{_slug(contexto['equipo_local'])}_vs_"
            f"{_slug(contexto['equipo_visitante'])}_{id_trabajo}.png")
        ruta_cuadro = cuadro_mod.generar_cuadro_desde_analisis(
            analisis, contexto["doc_local"], contexto["doc_visitante"],
            destino=destino_cuadro, peso_roja=cuadro_mod.PESO_PARTIDO_CON_ROJA,
            aviso=progreso)
        if ruta_cuadro:
            rutas["cuadro"] = ruta_cuadro

        destino_auditoria_15p = os.path.join(
            carpeta_cuadro,
            f"auditoria_15p_{_slug(contexto['equipo_local'])}_vs_"
            f"{_slug(contexto['equipo_visitante'])}_{id_trabajo}.png")
        ruta_auditoria_15p = cuadro_mod.generar_cuadro_auditoria_15_partidos_png(
            analisis, contexto["doc_local"], contexto["doc_visitante"],
            destino=destino_auditoria_15p)
        if ruta_auditoria_15p:
            rutas["auditoria_15p"] = ruta_auditoria_15p
    except Exception as e_cuadro:
        print(f"[SERVICIO MERCADOS] ⚠️ No se pudo generar el Cuadro Especialista: "
              f"{type(e_cuadro).__name__}: {e_cuadro}")
        ruta_cuadro = None

    # ── Cuadro Especialista de Enfrentamientos Directos (H2H Puro 2024–2026) ──
    # Módulo totalmente separado: no mezcla sus datos con los 15 partidos generales.
    # Si tienen 0 enfrentamientos en 2024–2026, declara 'Sin enfrentamientos directos
    # en 2024–2026' y omite el gráfico H2H.
    ruta_cuadro_h2h = None
    info_h2h_2024_2026: dict[str, Any] = {
        "ruta": None,
        "n_duelos": 0,
        "estado": "Sin enfrentamientos directos en 2024–2026",
        "duelos": [],
    }
    try:
        import cuadro_h2h_2024_2026 as h2h_puro_mod

        carpeta_cuadro = os.path.join(H2H_DIR, fecha_dir)
        os.makedirs(carpeta_cuadro, exist_ok=True)
        destino_cuadro_h2h = os.path.join(
            carpeta_cuadro,
            f"cuadro_h2h_directo_2024_2026_{_slug(contexto['equipo_local'])}_vs_"
            f"{_slug(contexto['equipo_visitante'])}_{id_trabajo}.png")
        info_h2h_2024_2026 = h2h_puro_mod.generar_cuadro_h2h_desde_analisis(
            analisis, contexto["doc_local"], contexto["doc_visitante"],
            destino=destino_cuadro_h2h, peso_roja=h2h_puro_mod.PESO_PARTIDO_CON_ROJA,
            aviso=progreso,
            fecha_corte=ctx.get("fecha_partido"),
            id_partido_excluir=ctx.get("id_partido"))
        ruta_cuadro_h2h = info_h2h_2024_2026.get("ruta")
        if ruta_cuadro_h2h:
            rutas["cuadro_h2h"] = ruta_cuadro_h2h
        else:
            print(f"[SERVICIO MERCADOS] ℹ️ H2H 2024–2026: {info_h2h_2024_2026.get('estado')}")
    except Exception as e_h2h:
        print(f"[SERVICIO MERCADOS] ⚠️ No se pudo generar el Cuadro H2H 2024–2026: "
              f"{type(e_h2h).__name__}: {e_h2h}")
        ruta_cuadro_h2h = None

    # ── Extracción de Cuotas Flashscore, 4ta Imagen (Ticket de Valor) y Análisis de 3 Párrafos ──
    ruta_ticket_valor = None
    cuotas_flashscore: dict[str, float | None] = {"local": None, "empate": None, "visitante": None}
    id_fs_partido: str | None = None
    analisis_texto_3_parrafos = ""
    try:
        from fuente_flashscore import ClienteFlashscore, candidatos_desde_feed_hh
        import informe as informe_mod

        cliente_fs = ClienteFlashscore()
        # 1) Priorizar id_flashscore del encuentro programado; luego duelos o historial conocido
        id_fs_prog = str(ctx.get("id_flashscore_partido") or "").strip()
        if id_fs_prog and id_fs_prog not in ("N/D", "None", ""):
            id_fs_partido = id_fs_prog
        if not id_fs_partido:
            for p_cand in (info_h2h_2024_2026.get("duelos") or []) + (contexto["doc_local"].get("partidos") or []):
                sec = p_cand.get("fuente_secundaria") or {}
                mid_cand = str(sec.get("id_flashscore") or p_cand.get("id_flashscore") or "").strip()
                if mid_cand and mid_cand not in ("N/D", "None", ""):
                    id_fs_partido = mid_cand
                    break

        if id_fs_partido:
            # Intentar extraer cuotas del partido o de los candidatos H2H
            cuotas_try = cliente_fs.extraer_cuotas(id_fs_partido)
            if any(v is not None for v in cuotas_try.values()):
                cuotas_flashscore = cuotas_try
            else:
                try:
                    cands_hh = candidatos_desde_feed_hh(id_fs_partido, cliente=cliente_fs)
                    for ch in cands_hh[:5]:
                        cid = str(ch.get("id") or "").strip()
                        if cid and cid != id_fs_partido:
                            c_alt = cliente_fs.extraer_cuotas(cid)
                            if any(v is not None for v in c_alt.values()):
                                cuotas_flashscore = c_alt
                                id_fs_partido = cid
                                break
                except Exception:
                    pass

        destino_ticket_valor = os.path.join(
            base_dir,
            f"ticket_valor_{_slug(contexto['equipo_local'])}_vs_"
            f"{_slug(contexto['equipo_visitante'])}_{id_trabajo}.png",
        )
        ruta_ticket_valor = informe_mod.generar_ticket_valor_png(
            analisis,
            cuotas_flashscore=cuotas_flashscore,
            destino=destino_ticket_valor,
            id_flashscore=id_fs_partido,
        )
        if ruta_ticket_valor:
            rutas["ticket_valor"] = ruta_ticket_valor

        analisis_texto_3_parrafos = informe_mod.redactar_analisis_3_parrafos(
            analisis,
            info_h2h=info_h2h_2024_2026,
            cuotas_flashscore=cuotas_flashscore,
            doc_local=contexto.get("doc_local"),
            doc_visitante=contexto.get("doc_visitante"),
        )
        if analisis_texto_3_parrafos:
            with open(rutas["informe"], "a", encoding="utf-8") as f_md_val:
                f_md_val.write("\n\n## 🧠 Análisis Comparativo y Veredicto de Valor (+EV)\n\n"
                               + analisis_texto_3_parrafos + "\n")
    except Exception as e_val:
        print(f"[SERVICIO MERCADOS] ⚠️ No se pudo generar el Ticket de Valor / Análisis 3 párrafos: "
              f"{type(e_val).__name__}: {e_val}")

    # ── Diagnóstico Metacognitivo y Memoria Empírica (AgenteConsciente) ──
    diagnostico_conciencia: dict[str, Any] = {}
    bloque_metacognitivo_md = ""
    try:
        from agente_consciente import obtener_agente_consciente
        conciencia_inst = obtener_agente_consciente()
        diagnostico_conciencia = conciencia_inst.diagnosticar_enfrentamiento(
            contexto["equipo_local"],
            contexto["equipo_visitante"],
            analisis_mercados=analisis,
            info_h2h=info_h2h_2024_2026,
            usar_llm=False,
        )
        bloque_metacognitivo_md = conciencia_inst.formatear_bloque_metacognitivo_md(diagnostico_conciencia)
        if bloque_metacognitivo_md:
            with open(rutas["informe"], "a", encoding="utf-8") as f_md:
                f_md.write("\n" + bloque_metacognitivo_md)
    except Exception as e_conc:
        print(f"[SERVICIO MERCADOS] ⚠️ Diagnóstico metacognitivo omitido: {e_conc}")

    marcar_estado(id_trabajo, "analisis_completado",
                  {"rutas": rutas, "partido": f"{contexto['equipo_local']} vs "
                                               f"{contexto['equipo_visitante']}"})
    marcar_estado(id_trabajo, "mensaje_preparado",
                  {"mensaje": analisis.mensaje, "caracteres": len(analisis.mensaje),
                   "ruta_imagen": ruta_imagen, "ruta_cuadro": ruta_cuadro,
                   "ruta_cuadro_h2h": ruta_cuadro_h2h,
                   "estado_h2h_2024_2026": info_h2h_2024_2026.get("estado"),
                   "motor_recursivo": ("aplicado"
                                       if (getattr(analisis.motor, "recursivo", None) or {})
                                       .get("aplicado") else "no aplicado"),
                   "indice_confianza_cognitiva": diagnostico_conciencia.get("indice_confianza_cognitiva")})

    return {
        "id_trabajo": id_trabajo,
        "analisis": analisis,
        "rutas": rutas,
        "ruta_imagen": ruta_imagen,
        "ruta_ticket_valor": ruta_ticket_valor,
        "cuotas_flashscore": cuotas_flashscore,
        "id_flashscore": id_fs_partido,
        "analisis_texto_3_parrafos": analisis_texto_3_parrafos,
        "ruta_cuadro": ruta_cuadro,
        "ruta_auditoria_15p": ruta_auditoria_15p,
        "ruta_cuadro_h2h": ruta_cuadro_h2h,
        "h2h_2024_2026": info_h2h_2024_2026,
        "estado_h2h_2024_2026": info_h2h_2024_2026.get("estado"),
        "motor_recursivo": _resumen_motor_recursivo(analisis),
        "diagnostico_conciencia": diagnostico_conciencia,
        "contexto": contexto,
        "mensaje": analisis.mensaje,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ENVÍO A TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

def partir_mensaje(mensaje: str, limite: int = LIMITE_TELEGRAM) -> list[str]:
    """
    Divide el mensaje en partes numeradas respetando el orden y sin truncar:
    corta por líneas completas y, si una línea excediera el límite, la subdivide.
    """
    if len(mensaje) <= limite:
        return [mensaje]
    lineas = mensaje.split("\n")
    partes: list[str] = []
    actual: list[str] = []
    largo = 0
    for linea in lineas:
        while len(linea) > limite:
            # Línea imposible de meter entera: se subdivide sin perder texto
            if actual:
                partes.append("\n".join(actual))
                actual, largo = [], 0
            partes.append(linea[:limite])
            linea = linea[limite:]
        if largo + len(linea) + 1 > limite and actual:
            partes.append("\n".join(actual))
            actual, largo = [], 0
        actual.append(linea)
        largo += len(linea) + 1
    if actual:
        partes.append("\n".join(actual))
    if len(partes) <= 1:
        return partes
    total = len(partes)
    return [f"[{i}/{total}]\n{p}" for i, p in enumerate(partes, 1)]


def enviar_analisis(
    preparado: dict[str, Any],
    enviar_fn: Any | None = None,
    enviar_foto_fn: Any | None = None,
    chat_id: int | str | None = None,
    nueva_ejecucion: bool = False,
) -> dict[str, Any]:
    """
    Envía el ticket gráfico generado como imagen PNG a Telegram.

    Descarta la salida de texto tradicional y despacha send_photo con pie
    de foto conciso. Si se pasa un adaptador de prueba de texto (enviar_fn),
    también lo soporta para mantener compatibilidad con pruebas unitarias sintéticas.
    """
    id_trabajo = preparado["id_trabajo"]
    ruta_imagen = preparado.get("ruta_imagen") or (preparado.get("rutas") or {}).get("imagen")

    adaptador_foto = enviar_foto_fn
    if adaptador_foto is None and enviar_fn is enviar_fn_telegram_foto:
        adaptador_foto = enviar_fn_telegram_foto

    if adaptador_foto is None and enviar_fn is None:
        marcar_estado(id_trabajo, "mensaje_preparado",
                      {"aviso": "sin función de envío: ticket e informe listos"})
        return {"enviado": False,
                "motivo": "falta configuración de envío (adaptador no disponible). "
                          "El ticket PNG y el informe quedan listos.",
                "id_trabajo": id_trabajo, "estado": "mensaje_preparado"}

    previo = estado_envio_previo(id_trabajo)
    if previo and previo.get("estado") == "envio_confirmado" and not nueva_ejecucion and not id_trabajo.startswith("prueba_"):
        return {"enviado": False, "motivo": "envío ya confirmado previamente",
                "id_trabajo": id_trabajo, "estado": "envio_confirmado"}

    # Prioridad: Envío multimedia (Foto PNG) cuando se provee adaptador_foto o cuando
    # enviar_fn es el adaptador real de Telegram (o foto) y la imagen existe.
    usar_foto = (adaptador_foto is not None) or (
        ruta_imagen and os.path.exists(ruta_imagen) and
        (enviar_fn is None or enviar_fn is enviar_fn_telegram or enviar_fn is enviar_fn_telegram_foto)
    )

    if usar_foto and ruta_imagen and os.path.exists(ruta_imagen):
        foto_sender = adaptador_foto or enviar_fn_telegram_foto
        ctx = preparado.get("contexto") or {}
        eq_l = ctx.get("equipo_local") or "Local"
        eq_v = ctx.get("equipo_visitante") or "Visitante"
        analisis_obj = preparado.get("analisis")
        umbral_val = getattr(getattr(analisis_obj, "parametros", None), "umbral", 0.70)
        meta_an = dict(getattr(analisis_obj, "metadatos", {}) or {})
        adv_l_an = meta_an.get("advertencia_liga") or ctx.get("advertencia_liga")
        if preparado.get("abstenido"):
            caption = f"🚨 {eq_l} vs {eq_v} | Pronóstico Abstenido ({preparado.get('estado_cobertura', 'cobertura_insuficiente')})"
        else:
            caption = f"🎯 {eq_l} vs {eq_v} | Umbral >{umbral_val:.0%}"
        if adv_l_an:
            caption += f" | ⚠️ {adv_l_an}"

        marcar_estado(id_trabajo, "envio_en_curso", {"tipo": "foto", "ruta": ruta_imagen})
        try:
            ids_telegram: list[Any] = []
            fechas_telegram: list[int] = []
            eventos_envio_telegram: list[dict[str, Any]] = []

            def _registrar_respuesta_tg(etiqueta_parte: str, resp_tg: Any) -> None:
                if not isinstance(resp_tg, dict):
                    return
                mid = resp_tg.get("message_id")
                mdt = resp_tg.get("date")
                if mid is not None:
                    ids_telegram.append(mid)
                if mdt is not None:
                    try:
                        fechas_telegram.append(int(mdt))
                    except (TypeError, ValueError):
                        pass
                if mid is not None or mdt is not None:
                    eventos_envio_telegram.append({
                        "parte": etiqueta_parte,
                        "message_id": mid,
                        "message_date": int(mdt) if mdt is not None else None,
                    })

            # 0) Análisis de texto detallado (3 párrafos) ANTES de enviar las imágenes
            texto_3p = (preparado.get("analisis_texto_3_parrafos") or "").strip()
            if texto_3p:
                texto_sender = (
                    enviar_fn
                    if (enviar_fn is not None and enviar_fn is not enviar_fn_telegram_foto)
                    else (enviar_fn_telegram if foto_sender is enviar_fn_telegram_foto else None)
                )
                if texto_sender is not None:
                    try:
                        adv_txt = f"⚠️ {adv_l_an}\n\n" if adv_l_an else ""
                        encabezado_3p = adv_txt + f"🧠 ANÁLISIS CUANTITATIVO Y VEREDICTO DE VALOR — {eq_l} vs {eq_v}\n" + ("━" * 38) + "\n\n"
                        for parte_3p in partir_mensaje(encabezado_3p + texto_3p):
                            res_txt_3p = texto_sender(parte_3p, chat_id=chat_id)
                            _registrar_respuesta_tg("texto_3_parrafos", res_txt_3p)
                    except Exception as e_txt_3p:
                        print(f"[SERVICIO MERCADOS] ⚠️ Aviso al enviar análisis de 3 párrafos: {e_txt_3p}")

            # 1) Primera imagen: Ticket General de Mercados (o Ticket de Abstención)
            resultado = foto_sender(ruta_imagen, caption=caption, chat_id=chat_id)

            if preparado.get("abstenido"):
                _registrar_respuesta_tg("ticket_abstencion", resultado)
                tg_env_abs = {
                    "message_id": (eventos_envio_telegram[-1]["message_id"] if eventos_envio_telegram else (ids_telegram[-1] if ids_telegram else None)),
                    "message_date": (fechas_telegram[-1] if fechas_telegram else None),
                    "message_date_utc": (
                        datetime.fromtimestamp(int(fechas_telegram[-1]), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        if fechas_telegram else None
                    ),
                    "ids_telegram": ids_telegram,
                    "eventos": eventos_envio_telegram,
                }
                marcar_estado(id_trabajo, "envio_confirmado", {
                    "tipo": "foto_abstencion",
                    "ruta": ruta_imagen,
                    "ids_telegram": ids_telegram,
                    "telegram_envio": tg_env_abs,
                })
                return {
                    "enviado": True,
                    "id_trabajo": id_trabajo,
                    "estado": "abstenido_enviado",
                    "rutas": preparado.get("rutas"),
                    "ids_telegram": ids_telegram,
                    "telegram_envio": tg_env_abs,
                }
            if isinstance(resultado, dict):
                if not resultado.get("ok", True):
                    raise RuntimeError(resultado.get("error") or "envío de foto no confirmado")
                _registrar_respuesta_tg("ticket_principal", resultado)
            elif resultado is False:
                raise RuntimeError("el adaptador de foto devolvió False")

            # 2) Segunda imagen (justo después del ticket general): El Ticket de Valor (+EV Flashscore)
            ruta_ticket_valor = (preparado.get("ruta_ticket_valor")
                                 or (preparado.get("rutas") or {}).get("ticket_valor"))
            ticket_valor_enviado = False
            motivo_ticket_valor = ""
            if (ruta_ticket_valor and os.path.exists(ruta_ticket_valor)
                    and os.path.abspath(ruta_ticket_valor) != os.path.abspath(ruta_imagen)):
                try:
                    res_valor = foto_sender(
                        ruta_ticket_valor,
                        caption=f"💎 Ticket de Valor (+EV Flashscore vs Red 15P) | {eq_l} vs {eq_v}",
                        chat_id=chat_id,
                    )
                    if isinstance(res_valor, dict) and not res_valor.get("ok", True):
                        raise RuntimeError(res_valor.get("error") or "envío del Ticket de Valor no confirmado")
                    if res_valor is False:
                        raise RuntimeError("el adaptador devolvió False para el Ticket de Valor")
                    ticket_valor_enviado = True
                    _registrar_respuesta_tg("ticket_valor", res_valor)
                except Exception as e_val_send:
                    motivo_ticket_valor = f"{type(e_val_send).__name__}: {e_val_send}"
                    print(f"[SERVICIO MERCADOS] ⚠️ Ticket de Valor no enviado: {motivo_ticket_valor}")

            # 3) Tercera imagen: el Cuadro Especialista (ratings 0-100 + tempo + mapa de calor)
            ruta_cuadro = (preparado.get("ruta_cuadro")
                           or (preparado.get("rutas") or {}).get("cuadro"))
            cuadro_enviado = False
            motivo_cuadro = ""
            if (ruta_cuadro and os.path.exists(ruta_cuadro)
                    and os.path.abspath(ruta_cuadro) != os.path.abspath(ruta_imagen)):
                try:
                    res_cuadro = foto_sender(
                        ruta_cuadro,
                        caption=f"📊 Cuadro Especialista | {eq_l} vs {eq_v}",
                        chat_id=chat_id)
                    if isinstance(res_cuadro, dict) and not res_cuadro.get("ok", True):
                        raise RuntimeError(res_cuadro.get("error")
                                           or "envío del cuadro no confirmado")
                    if res_cuadro is False:
                        raise RuntimeError("el adaptador devolvió False para el cuadro")
                    cuadro_enviado = True
                    _registrar_respuesta_tg("cuadro_especialista", res_cuadro)
                except Exception as e_cuadro:
                    motivo_cuadro = f"{type(e_cuadro).__name__}: {e_cuadro}"
                    print(f"[SERVICIO MERCADOS] ⚠️ Cuadro Especialista no enviado: "
                          f"{motivo_cuadro}")

            # Tercera imagen (independiente): Cuadro Especialista de Enfrentamientos Directos
            # (H2H Puro 2024–2026). Si tienen 0 enfrentamientos en 2024–2026, se omite el gráfico
            # H2H y se declara 'Sin enfrentamientos directos en 2024–2026'.
            ruta_cuadro_h2h = (preparado["ruta_cuadro_h2h"]
                               if "ruta_cuadro_h2h" in preparado
                               else (preparado.get("rutas") or {}).get("cuadro_h2h"))
            info_h2h = preparado.get("h2h_2024_2026") or {}
            estado_h2h = (preparado.get("estado_h2h_2024_2026")
                          or info_h2h.get("estado")
                          or "Sin enfrentamientos directos en 2024–2026")
            cuadro_h2h_enviado = False
            motivo_cuadro_h2h = ""
            if (ruta_cuadro_h2h and os.path.exists(ruta_cuadro_h2h)
                    and os.path.abspath(ruta_cuadro_h2h) != os.path.abspath(ruta_imagen)):
                try:
                    n_duelos_h2h = info_h2h.get("n_duelos") or len(info_h2h.get("duelos") or [])
                    res_h2h = foto_sender(
                        ruta_cuadro_h2h,
                        caption=(f"⚔️ Enfrentamientos Directos (H2H Puro 2024–2026) | "
                                 f"{eq_l} vs {eq_v} ({n_duelos_h2h} duelos)"),
                        chat_id=chat_id)
                    if isinstance(res_h2h, dict) and not res_h2h.get("ok", True):
                        raise RuntimeError(res_h2h.get("error")
                                           or "envío del cuadro H2H no confirmado")
                    if res_h2h is False:
                        raise RuntimeError("el adaptador devolvió False para el cuadro H2H")
                    cuadro_h2h_enviado = True
                    _registrar_respuesta_tg("cuadro_h2h", res_h2h)
                except Exception as e_h2h_send:
                    motivo_cuadro_h2h = f"{type(e_h2h_send).__name__}: {e_h2h_send}"
                    print(f"[SERVICIO MERCADOS] ⚠️ Cuadro H2H 2024–2026 no enviado: "
                          f"{motivo_cuadro_h2h}")
            else:
                motivo_cuadro_h2h = estado_h2h

            # 5) Auditoría de los 15 Partidos Oficiales Únicos
            ruta_auditoria_15p = (preparado.get("ruta_auditoria_15p")
                                  or (preparado.get("rutas") or {}).get("auditoria_15p"))
            auditoria_15p_enviado = False
            motivo_auditoria_15p = ""
            if (ruta_auditoria_15p and os.path.exists(ruta_auditoria_15p)
                    and os.path.abspath(ruta_auditoria_15p) != os.path.abspath(ruta_imagen)):
                try:
                    res_15p = foto_sender(
                        ruta_auditoria_15p,
                        caption=f"📋 Auditoría de los 15 Partidos Oficiales Únicos | {eq_l} vs {eq_v}",
                        chat_id=chat_id)
                    if isinstance(res_15p, dict) and not res_15p.get("ok", True):
                        raise RuntimeError(res_15p.get("error") or "envío de auditoría 15P no confirmado")
                    if res_15p is False:
                        raise RuntimeError("el adaptador devolvió False para auditoría 15P")
                    auditoria_15p_enviado = True
                    _registrar_respuesta_tg("auditoria_15p", res_15p)
                except Exception as e_15p_send:
                    motivo_auditoria_15p = f"{type(e_15p_send).__name__}: {e_15p_send}"
                    print(f"[SERVICIO MERCADOS] ⚠️ Auditoría 15P no enviada: {motivo_auditoria_15p}")

            partes_enviadas = (
                1
                + (1 if ticket_valor_enviado else 0)
                + (1 if cuadro_enviado else 0)
                + (1 if cuadro_h2h_enviado else 0)
                + (1 if auditoria_15p_enviado else 0)
            )
            tg_envio = {
                "message_id": (eventos_envio_telegram[-1]["message_id"] if eventos_envio_telegram else (ids_telegram[-1] if ids_telegram else None)),
                "message_date": (fechas_telegram[-1] if fechas_telegram else None),
                "message_date_utc": (
                    datetime.fromtimestamp(int(fechas_telegram[-1]), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if fechas_telegram else None
                ),
                "ids_telegram": ids_telegram,
                "eventos": eventos_envio_telegram,
            }
            marcar_estado(id_trabajo, "envio_confirmado",
                          {"tipo": "foto", "ruta_imagen": ruta_imagen,
                           "ruta_ticket_valor": ruta_ticket_valor if ticket_valor_enviado else None,
                           "motivo_ticket_valor": motivo_ticket_valor,
                           "ruta_cuadro": ruta_cuadro if cuadro_enviado else None,
                           "motivo_cuadro": motivo_cuadro,
                           "ruta_cuadro_h2h": ruta_cuadro_h2h if cuadro_h2h_enviado else None,
                           "estado_h2h_2024_2026": estado_h2h,
                           "motivo_cuadro_h2h": motivo_cuadro_h2h,
                           "ruta_auditoria_15p": ruta_auditoria_15p if auditoria_15p_enviado else None,
                           "motivo_auditoria_15p": motivo_auditoria_15p,
                           "ids_telegram": ids_telegram,
                           "telegram_envio": tg_envio})
            return {"enviado": True, "tipo": "foto", "ruta_imagen": ruta_imagen,
                    "ruta_ticket_valor": ruta_ticket_valor,
                    "ticket_valor_enviado": ticket_valor_enviado,
                    "motivo_ticket_valor": motivo_ticket_valor,
                    "ruta_cuadro": ruta_cuadro, "cuadro_enviado": cuadro_enviado,
                    "motivo_cuadro": motivo_cuadro,
                    "ruta_cuadro_h2h": ruta_cuadro_h2h,
                    "cuadro_h2h_enviado": cuadro_h2h_enviado,
                    "estado_h2h_2024_2026": estado_h2h,
                    "motivo_cuadro_h2h": motivo_cuadro_h2h,
                    "ruta_auditoria_15p": ruta_auditoria_15p,
                    "auditoria_15p_enviado": auditoria_15p_enviado,
                    "motivo_auditoria_15p": motivo_auditoria_15p,
                    "ids_telegram": ids_telegram, "telegram_envio": tg_envio,
                    "partes": partes_enviadas,
                    "id_trabajo": id_trabajo, "estado": "envio_confirmado"}
        except Exception as e:
            marcar_estado(id_trabajo, "envio_fallido",
                          {"tipo": "foto", "error": f"{type(e).__name__}: {e}", "ruta": ruta_imagen})
            return {"enviado": False, "motivo": f"{type(e).__name__}: {e}",
                    "id_trabajo": id_trabajo, "estado": "envio_fallido"}

    # Fallback o tests sintéticos que prueban enviar_fn(parte, chat_id)
    mensaje = preparado.get("mensaje") or ""
    partes = partir_mensaje(mensaje)
    marcar_estado(id_trabajo, "envio_en_curso", {"partes": len(partes)})

    ids_telegram: list[Any] = []
    fechas_telegram: list[int] = []
    eventos_envio_telegram: list[dict[str, Any]] = []
    try:
        for parte in partes:
            resultado = enviar_fn(parte, chat_id)
            if isinstance(resultado, dict):
                if not resultado.get("ok", True):
                    raise RuntimeError(resultado.get("error") or "envío no confirmado")
                mid = resultado.get("message_id")
                mdt = resultado.get("date")
                if mid is not None:
                    ids_telegram.append(mid)
                if mdt is not None:
                    try:
                        fechas_telegram.append(int(mdt))
                    except (TypeError, ValueError):
                        pass
                if mid is not None or mdt is not None:
                    eventos_envio_telegram.append({
                        "parte": "texto",
                        "message_id": mid,
                        "message_date": int(mdt) if mdt is not None else None,
                    })
            elif resultado is False:
                raise RuntimeError("el adaptador devolvió False")
        tg_envio = {
            "message_id": (eventos_envio_telegram[-1]["message_id"] if eventos_envio_telegram else (ids_telegram[-1] if ids_telegram else None)),
            "message_date": (fechas_telegram[-1] if fechas_telegram else None),
            "message_date_utc": (
                datetime.fromtimestamp(int(fechas_telegram[-1]), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if fechas_telegram else None
            ),
            "ids_telegram": ids_telegram,
            "eventos": eventos_envio_telegram,
        }
        marcar_estado(id_trabajo, "envio_confirmado",
                      {"partes": len(partes), "ids_telegram": ids_telegram,
                       "telegram_envio": tg_envio,
                       "caracteres": len(mensaje)})
        return {"enviado": True, "partes": len(partes), "ids_telegram": ids_telegram,
                "telegram_envio": tg_envio,
                "id_trabajo": id_trabajo, "estado": "envio_confirmado"}
    except Exception as e:
        marcar_estado(id_trabajo, "envio_fallido",
                      {"error": f"{type(e).__name__}: {e}", "partes_enviadas": len(ids_telegram)})
        return {"enviado": False, "motivo": f"{type(e).__name__}: {e}",
                "id_trabajo": id_trabajo, "estado": "envio_fallido",
                "partes_confirmadas": len(ids_telegram)}


def enviar_fn_telegram(texto: str, chat_id: int | str | None = None,
                       parse_mode: str | None = None) -> dict[str, Any]:
    """
    Adaptador real de Telegram. Usa el mismo token y destinatario configurados del
    proyecto (config.TELEGRAM_BOT_TOKEN / config.TELEGRAM_CHAT_ID). Nunca toma el
    destinatario de los reportes ni de ningún contenido analizado.

    IMPORTANTE sobre `parse_mode`: la API de Telegram solo acepta las cadenas
    "Markdown", "MarkdownV2" o "HTML". Enviar la clave con valor nulo
    (`"parse_mode": null`) provoca HTTP 400 «unsupported parse_mode», así que
    cuando no se pide formato la clave se OMITE del payload (no se envía null).
    Por defecto este adaptador envía texto plano: el mensaje de mercados contiene
    caracteres como «>», «%» o «_» que romperían el parseo si se forzara Markdown.
    """
    import requests

    token = (config.TELEGRAM_BOT_TOKEN or "").strip()
    destino = str(chat_id or config.TELEGRAM_CHAT_ID or "").strip()
    if not token:
        return {"ok": False, "error": "falta TELEGRAM_BOT_TOKEN en la configuración"}
    if not destino:
        return {"ok": False, "error": "falta TELEGRAM_CHAT_ID en la configuración"}

    if parse_mode is not None and parse_mode not in ("Markdown", "MarkdownV2", "HTML"):
        return {"ok": False,
                "error": f"parse_mode inválido: {parse_mode!r}. "
                         f"Telegram solo acepta 'Markdown', 'MarkdownV2' o 'HTML'."}

    payload: dict[str, Any] = {"chat_id": destino, "text": texto}
    if parse_mode:
        # Solo se incluye cuando hay formato explícito y válido
        payload["parse_mode"] = parse_mode

    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json=payload, timeout=30)
        datos = r.json() if r.content else {}
        if r.status_code == 200 and datos.get("ok"):
            return {"ok": True, "message_id": (datos.get("result") or {}).get("message_id"), "date": (datos.get("result") or {}).get("date")}
        # 429: se respeta retry_after sin reintentar a ciegas
        if r.status_code == 429:
            return {"ok": False,
                    "error": f"rate limit: retry_after="
                             f"{(datos.get('parameters') or {}).get('retry_after')}"}
        return {"ok": False, "error": f"HTTP {r.status_code}: {str(datos)[:200]}"}
    except Exception as e:
        return {"ok": False, "error": f"timeout/red: {type(e).__name__}: {e}"}


def adaptador_simulado(registro: list[str] | None = None) -> Any:
    """Adaptador de prueba: registra los mensajes sin enviar nada a Telegram."""
    salida = registro if registro is not None else []

    def _enviar(texto: str, chat_id: Any = None) -> dict[str, Any]:
        salida.append(texto)
        return {"ok": True, "message_id": len(salida)}

    _enviar.enviados = salida      # type: ignore[attr-defined]
    return _enviar


def enviar_fn_telegram_foto(ruta: str, caption: str = "",
                            chat_id: int | str | None = None) -> dict[str, Any]:
    """
    Adaptador real para enviar una IMAGEN (las tablas de la evaluación).

    Usa el mismo token y destinatario configurados del proyecto. El pie de foto va
    en texto plano: se omite `parse_mode` para no repetir el fallo de
    «unsupported parse_mode».
    """
    import requests

    token = (config.TELEGRAM_BOT_TOKEN or "").strip()
    destino = str(chat_id or config.TELEGRAM_CHAT_ID or "").strip()
    if not token:
        return {"ok": False, "error": "falta TELEGRAM_BOT_TOKEN en la configuración"}
    if not destino:
        return {"ok": False, "error": "falta TELEGRAM_CHAT_ID en la configuración"}
    if not os.path.exists(ruta):
        return {"ok": False, "error": f"no existe la imagen: {ruta}"}
    if os.path.getsize(ruta) > 10 * 1024 * 1024:
        return {"ok": False, "error": "la imagen supera los 10 MB que admite Telegram"}

    try:
        with open(ruta, "rb") as fh:
            datos_form = {"chat_id": destino}
            if caption:
                datos_form["caption"] = caption[:1024]
            r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                              data=datos_form, files={"photo": fh}, timeout=60)
        datos = r.json() if r.content else {}
        if r.status_code == 200 and datos.get("ok"):
            return {"ok": True, "message_id": (datos.get("result") or {}).get("message_id"), "date": (datos.get("result") or {}).get("date")}
        if r.status_code == 429:
            return {"ok": False,
                    "error": f"rate limit: retry_after="
                             f"{(datos.get('parameters') or {}).get('retry_after')}"}
        return {"ok": False, "error": f"HTTP {r.status_code}: {str(datos)[:200]}"}
    except Exception as e:
        return {"ok": False, "error": f"timeout/red: {type(e).__name__}: {e}"}


def adaptador_simulado_foto(registro: list[str] | None = None) -> Any:
    """Adaptador de prueba para imágenes: registra sin enviar nada."""
    salida = registro if registro is not None else []

    def _enviar(ruta: str, caption: str = "", chat_id: Any = None) -> dict[str, Any]:
        salida.append(f"[foto] {ruta} | {caption}")
        return {"ok": True, "message_id": len(salida)}

    _enviar.enviados = salida      # type: ignore[attr-defined]
    return _enviar


def rotar_informes_antiguos(dias_max: int = 14, max_mb: float = 120.0) -> dict[str, int]:
    """
    Higiene de artefactos: limpia archivos temporales de informes/ que excedan
    `dias_max` o si el directorio supera `max_mb`. Preserva snapshots de evaluación.
    """
    base_dir = config.INFORME_DIR
    if not os.path.exists(base_dir):
        return {"eliminados": 0, "bytes_liberados": 0}

    ahora = time.time()
    segundos_max = dias_max * 86400
    eliminados = 0
    bytes_liberados = 0

    archivos = []
    total_bytes = 0
    for root, _, files in os.walk(base_dir):
        for f in files:
            p = os.path.join(root, f)
            try:
                tam = os.path.getsize(p)
                mtime = os.path.getmtime(p)
                total_bytes += tam
                archivos.append((mtime, tam, p))
            except Exception:
                pass

    limite_bytes = int(max_mb * 1024 * 1024)
    # 1. Eliminar archivos mayores a dias_max (excepto si están en evaluacion/)
    for mtime, tam, p in archivos:
        if "evaluacion" in p.lower():
            continue
        if (ahora - mtime) > segundos_max:
            try:
                os.remove(p)
                eliminados += 1
                bytes_liberados += tam
                total_bytes -= tam
            except Exception:
                pass

    # 2. Si todavía supera max_mb, eliminar los más antiguos primero
    if total_bytes > limite_bytes:
        archivos_restantes = sorted([a for a in archivos if os.path.exists(a[2]) and "evaluacion" not in a[2].lower()], key=lambda x: x[0])
        for mtime, tam, p in archivos_restantes:
            if total_bytes <= limite_bytes:
                break
            try:
                os.remove(p)
                eliminados += 1
                bytes_liberados += tam
                total_bytes -= tam
            except Exception:
                pass

    return {"eliminados": eliminados, "bytes_liberados": bytes_liberados}


def generar_reporte_xai_telegram(
    analisis: Any,
    doc_local: dict[str, Any] | None = None,
    doc_visitante: dict[str, Any] | None = None,
) -> str:
    """Genera el bloque narrativo XAI de análisis para Telegram."""
    from informe import generar_bloque_narrativo_xai_desde_analisis
    return generar_bloque_narrativo_xai_desde_analisis(
        analisis, doc_local=doc_local, doc_visitante=doc_visitante
    )

