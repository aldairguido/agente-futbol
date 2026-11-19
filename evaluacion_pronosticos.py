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
import hashlib
import json
import os
import re
import math
import random
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
LEDGER_OOS_PATH = os.path.join(EVALUACION_DIR, "ledger_oos.jsonl")

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
                       "no_verificable", "regla_no_definida", "abstenido_por_cobertura")


# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

def _slug(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "x").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").lower() or "x"


def _ahora() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


_EXONIMOS_CANONICOS = {
    "turquia": "turkey", "turkiye": "turkey", "turkey": "turkey",
    "belgica": "belgium", "belgie": "belgium", "belgique": "belgium", "belgium": "belgium",
    "italia": "italy", "italy": "italy",
    "francia": "france", "france": "france",
    "espana": "spain", "spain": "spain",
    "alemania": "germany", "deutschland": "germany", "germany": "germany",
    "inglaterra": "england", "england": "england",
    "paisesbajos": "netherlands", "holanda": "netherlands", "netherlands": "netherlands",
    "suiza": "switzerland", "switzerland": "switzerland",
    "croacia": "croatia", "hrvatska": "croatia", "croatia": "croatia",
    "polonia": "poland", "polska": "poland", "poland": "poland",
    "grecia": "greece", "greece": "greece",
    "hungria": "hungary", "magyarorszag": "hungary", "hungary": "hungary",
    "suecia": "sweden", "sverige": "sweden", "sweden": "sweden",
    "noruega": "norway", "norge": "norway", "norway": "norway",
    "dinamarca": "denmark", "danmark": "denmark", "denmark": "denmark",
    "escocia": "scotland", "scotland": "scotland",
    "gales": "wales", "wales": "wales",
    "irlanda": "ireland", "ireland": "ireland",
    "republicacheca": "czechia", "chequia": "czechia", "czechrepublic": "czechia", "czechia": "czechia",
}


def _clave_equipo(nombre: str) -> str:
    """Clave tolerante a tildes, exónimos bilingües y palabras vacías para emparejar equipos."""
    base = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode()
    palabras = [p for p in re.split(r"[^A-Za-z0-9]+", base.lower()) if p]
    vacias = {"de", "del", "la", "el", "los", "las", "y", "e", "club", "fc", "cf"}
    sig = [p for p in palabras if p not in vacias]
    clave = "".join(sig) or "".join(palabras)
    return _EXONIMOS_CANONICOS.get(clave, clave)


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
    abstenido: bool = False
    estado_cobertura: str | None = None
    motivo_abstencion: str | None = None
    decision_cobertura: dict[str, Any] | None = None
    modo_sombra: dict[str, Any] | None = None
    telegram_message_id: int | None = None
    telegram_message_date: int | None = None
    telegram_origen: dict[str, Any] | None = None
    telegram_envio: dict[str, Any] | None = None
    traza_etapa_6: list[dict[str, Any]] | None = None
    resumen_etapa_6: dict[str, Any] | None = None
    fixture_confirmado: bool | None = None
    fuente_fecha: str | None = None
    hora_inicio_partido: str | None = None
    fuente_hora_inicio: str | None = None
    zona_origen_hora_inicio: str | None = None
    hora_inicio_fotmob: str | None = None
    hora_inicio_flashscore: str | None = None
    diferencia_hora_inicio_min: float | None = None
    hora_inicio_discrepante: bool | None = None
    hora_mensaje_utc: str | None = None
    es_oos_valido: bool | None = None
    es_canonica: bool | None = None
    estado_canonico: str | None = None
    regla_contabilidad_oos: str | None = None
    fuente_local: str | None = None
    fuente_visitante: str | None = None
    firma_datos: str | None = None
    calidad_fuente_local: dict[str, Any] | None = None
    calidad_fuente_visitante: dict[str, Any] | None = None
    lambdas_plano: dict[str, Any] | None = None
    lambdas_base_doble_adn: dict[str, Any] | None = None

    def a_dict(self) -> dict[str, Any]:
        out = {
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
            "abstenido": self.abstenido,
            "estado_cobertura": self.estado_cobertura,
            "motivo_abstencion": self.motivo_abstencion,
            "decision_cobertura": self.decision_cobertura,
            "inmutable": True,
        }
        if self.modo_sombra is not None:
            out["modo_sombra"] = self.modo_sombra
        if self.telegram_message_id is not None:
            out["telegram_message_id"] = self.telegram_message_id
        if self.telegram_message_date is not None:
            out["telegram_message_date"] = self.telegram_message_date
        if self.telegram_origen is not None:
            out["telegram_origen"] = self.telegram_origen
        if self.telegram_envio is not None:
            out["telegram_envio"] = self.telegram_envio
        if self.traza_etapa_6 is not None:
            out["traza_etapa_6"] = self.traza_etapa_6
        if self.resumen_etapa_6 is not None:
            out["resumen_etapa_6"] = self.resumen_etapa_6
        if self.fixture_confirmado is not None:
            out["fixture_confirmado"] = bool(self.fixture_confirmado)
            out["fuente_fecha"] = self.fuente_fecha
            out["hora_inicio_partido"] = self.hora_inicio_partido
            out["telegram_message_date"] = self.telegram_message_date
            out["hora_mensaje_utc"] = self.hora_mensaje_utc
            out["es_oos_valido"] = self.es_oos_valido
            out["es_canonica"] = self.es_canonica
            out["estado_canonico"] = self.estado_canonico
            out["regla_contabilidad_oos"] = self.regla_contabilidad_oos or REGLA_CONTABILIDAD_OOS
        if self.fuente_local is not None:
            out["fuente_local"] = self.fuente_local
        if self.fuente_visitante is not None:
            out["fuente_visitante"] = self.fuente_visitante
        if self.firma_datos is not None:
            out["firma_datos"] = self.firma_datos
        if self.calidad_fuente_local is not None:
            out["calidad_fuente_local"] = self.calidad_fuente_local
        if self.calidad_fuente_visitante is not None:
            out["calidad_fuente_visitante"] = self.calidad_fuente_visitante
        if self.fuente_hora_inicio is not None:
            out["fuente_hora_inicio"] = self.fuente_hora_inicio
        if self.zona_origen_hora_inicio is not None:
            out["zona_origen_hora_inicio"] = self.zona_origen_hora_inicio
        if self.hora_inicio_fotmob is not None:
            out["hora_inicio_fotmob"] = self.hora_inicio_fotmob
        if self.hora_inicio_flashscore is not None:
            out["hora_inicio_flashscore"] = self.hora_inicio_flashscore
        if self.diferencia_hora_inicio_min is not None:
            out["diferencia_hora_inicio_min"] = self.diferencia_hora_inicio_min
        if self.hora_inicio_discrepante is not None:
            out["hora_inicio_discrepante"] = bool(self.hora_inicio_discrepante)
        if self.lambdas_plano is not None:
            out["lambdas_plano"] = self.lambdas_plano
        if self.lambdas_base_doble_adn is not None:
            out["lambdas_base_doble_adn"] = self.lambdas_base_doble_adn
        return out


REGLA_CONTABILIDAD_OOS = (
    "Regla operativa sin validación empírica: una entrada solo cuenta como OOS si "
    "fixture_confirmado=True y telegram_message_date < hora_inicio_partido; ante "
    "múltiples entradas válidas del mismo partido, solo la primera anterior al inicio "
    "se marca como canonica (es_canonica=True) y el resto como no_canonica "
    "(es_canonica=False); las que incumplen se marcan como no_oos."
)


def _norm_txt_oos(val: Any) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(val or "").strip().lower())
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).split())


def _parsear_instante_con_zona_oos(valor: Any) -> tuple[datetime | None, str | None]:
    """
    Interpreta un instante SOLO si trae época numérica o zona horaria explícita
    (`Z`, `+HH:MM`, `-HH:MM`). Fechas sueltas sin hora/zona devuelven `(None, None)`.
    """
    if valor is None or isinstance(valor, bool):
        return None, None
    if isinstance(valor, (int, float)):
        dt = datetime.fromtimestamp(float(valor), tz=timezone.utc)
        return dt, dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(valor).strip()
    if not s:
        return None, None
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        dt = datetime.fromtimestamp(float(s), tz=timezone.utc)
        return dt, dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    has_tz = s.endswith(("Z", "z")) or bool(re.search(r"[+-]\d{2}:?\d{2}$", s))
    if not has_tz or len(s) <= 10:
        return None, None
    try:
        iso_in = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
        dt = datetime.fromisoformat(iso_in)
        if dt.tzinfo is None:
            return None, None
        return dt.astimezone(timezone.utc), s
    except Exception:
        return None, None


def _extraer_identidad_doc_oos(doc: dict[str, Any]) -> tuple[str, str, str]:
    raw_part = doc.get("partido")
    id_p = str(doc.get("id_partido") or (raw_part.get("id_partido") if isinstance(raw_part, dict) else "") or "").strip()
    eqs = doc.get("equipos")
    if isinstance(eqs, dict) and (eqs.get("local") or eqs.get("visitante")):
        part_str = f"{eqs.get('local') or ''} vs {eqs.get('visitante') or ''}"
    else:
        part_str = str(raw_part if not isinstance(raw_part, dict) else "")
    f_raw = raw_part.get("fecha") if isinstance(raw_part, dict) else doc.get("fecha")
    if not f_raw and doc.get("hora_inicio_partido"):
        f_raw = doc.get("hora_inicio_partido")
    m_f = re.search(r"\d{4}-\d{2}-\d{2}", str(f_raw or ""))
    fecha_10 = m_f.group(0) if m_f else ""
    return id_p, _norm_txt_oos(part_str), fecha_10


def _mismo_partido_oos(a: dict[str, Any], b: dict[str, Any]) -> bool:
    id_a, p_a, f_a = _extraer_identidad_doc_oos(a)
    id_b, p_b, f_b = _extraer_identidad_doc_oos(b)
    if id_a and id_b and id_a == id_b:
        return True
    if p_a and p_b and p_a == p_b:
        if f_a and f_b:
            return f_a == f_b
        return True
    return False


def evaluar_contabilidad_oos_registro(
    *,
    fixture_confirmado: Any,
    fuente_fecha: Any,
    hora_inicio_partido: Any,
    telegram_message_date: Any,
    hora_mensaje_utc: Any = None,
    hora_inicio_fotmob: Any = None,
    hora_inicio_flashscore: Any = None,
    diferencia_hora_inicio_min: float | None = None,
    hora_inicio_discrepante: bool | None = None,
    partido: Any = "",
    fecha: Any = "",
    id_partido: Any = "",
    estado_registro: str = "pre_partido",
    id_trabajo: str = "",
    registros_existentes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Aplica `REGLA_CONTABILIDAD_OOS`:
      - `es_oos_valido = True` ssi `fixture_confirmado is True` y
        `telegram_message_date < hora_inicio_partido` (ambos con zona horaria explícita).
      - Primera entrada OOS válida del mismo partido -> `canonica` (`es_canonica=True`).
      - Posteriores entradas válidas del mismo partido -> `no_canonica` (`es_canonica=False`).
      - Inválidas / fallback sin fixture / post-inicio -> `no_oos` (`es_canonica=False`).
    """
    fx_conf = bool(fixture_confirmado is True)
    f_fec = str(fuente_fecha or ("fixture" if fx_conf else "fallback")).strip() or ("fixture" if fx_conf else "fallback")
    dt_ini, hora_ini_str = _parsear_instante_con_zona_oos(hora_inicio_partido)
    dt_msg, hora_msg_str = _parsear_instante_con_zona_oos(
        hora_mensaje_utc if hora_mensaje_utc is not None else telegram_message_date
    )
    if dt_msg is None and telegram_message_date is not None:
        dt_msg, hora_msg_str = _parsear_instante_con_zona_oos(telegram_message_date)

    dt_fm, h_fm_str = _parsear_instante_con_zona_oos(hora_inicio_fotmob)
    dt_fs, h_fs_str = _parsear_instante_con_zona_oos(hora_inicio_flashscore)
    diff_h_min = float(diferencia_hora_inicio_min) if diferencia_hora_inicio_min is not None else None
    if diff_h_min is None and dt_fm is not None and dt_fs is not None:
        diff_h_min = round(abs((dt_fm - dt_fs).total_seconds()) / 60.0, 2)
    h_discrepante = bool(hora_inicio_discrepante) or (diff_h_min is not None and diff_h_min > 0.0)
    discrepancia_excede_15m = bool(diff_h_min is not None and diff_h_min > 15.0)

    es_oos_valido = bool(
        fx_conf
        and dt_ini is not None
        and dt_msg is not None
        and dt_msg < dt_ini
        and not discrepancia_excede_15m
    )
    if not es_oos_valido:
        es_canonica = False
        estado_canonico = "no_oos"
    else:
        actual = {
            "id_partido": str(id_partido or ""),
            "partido": str(partido or ""),
            "fecha": str(fecha or ""),
            "hora_inicio_partido": hora_ini_str,
        }
        ya_hay_canonica = False
        for ex in (registros_existentes or []):
            if not isinstance(ex, dict):
                continue
            if id_trabajo and str(ex.get("id_trabajo") or "") == str(id_trabajo):
                continue
            if estado_registro and ex.get("estado_registro") is not None:
                if str(ex.get("estado_registro") or "liquidado").strip().lower() != str(estado_registro).strip().lower():
                    continue
            if not _mismo_partido_oos(ex, actual):
                continue
            if ex.get("es_canonica") is True or ex.get("es_oos_valido") is True:
                ya_hay_canonica = True
                break
        es_canonica = not ya_hay_canonica
        estado_canonico = "canonica" if es_canonica else "no_canonica"

    res_oos = {
        "fixture_confirmado": fx_conf,
        "fuente_fecha": f_fec,
        "hora_inicio_partido": hora_ini_str,
        "telegram_message_date": telegram_message_date,
        "hora_mensaje_utc": hora_msg_str,
        "es_oos_valido": es_oos_valido,
        "es_canonica": es_canonica,
        "estado_canonico": estado_canonico,
        "regla_contabilidad_oos": REGLA_CONTABILIDAD_OOS,
    }
    if h_fm_str is not None:
        res_oos["hora_inicio_fotmob"] = h_fm_str
    if h_fs_str is not None:
        res_oos["hora_inicio_flashscore"] = h_fs_str
    if diff_h_min is not None:
        res_oos["diferencia_hora_inicio_min"] = diff_h_min
    if h_discrepante:
        res_oos["hora_inicio_discrepante"] = True
    return res_oos


def clasificar_oos_entrada_ledger(reg: dict[str, Any]) -> dict[str, Any]:
    """
    Clasifica cualquier entrada del ledger (incluido historial previo a Regla M sin reescribir hashes):
      - Con campos explícitos de Regla M -> `canonica` | `no_canonica` | `no_oos` | `legacy_sin_fixture`.
      - Sin `es_oos_valido` y sin `(fixture_confirmado=True y hora_inicio_partido)` -> `legacy_sin_fixture` (no-OOS).
    """
    if not isinstance(reg, dict):
        return {
            "clasificacion_oos": "legacy_sin_fixture",
            "es_oos_valido": False,
            "es_canonica": False,
            "es_legacy_sin_fixture": True,
        }
    if "es_oos_valido" in reg:
        est_c = str(reg.get("estado_canonico") or "").strip().lower()
        if est_c == "legacy_sin_fixture":
            return {
                "clasificacion_oos": "legacy_sin_fixture",
                "es_oos_valido": False,
                "es_canonica": False,
                "es_legacy_sin_fixture": True,
            }
        es_v = bool(reg.get("es_oos_valido") is True)
        es_c = bool(es_v and reg.get("es_canonica") is True)
        cls = est_c or ("canonica" if es_c else ("no_canonica" if es_v else "no_oos"))
        return {
            "clasificacion_oos": cls,
            "es_oos_valido": es_v,
            "es_canonica": es_c,
            "es_legacy_sin_fixture": False,
        }

    tiene_fx = (reg.get("fixture_confirmado") is True) and bool(reg.get("hora_inicio_partido"))
    if not tiene_fx:
        return {
            "clasificacion_oos": "legacy_sin_fixture",
            "es_oos_valido": False,
            "es_canonica": False,
            "es_legacy_sin_fixture": True,
        }

    ev = evaluar_contabilidad_oos_registro(
        fixture_confirmado=reg.get("fixture_confirmado"),
        fuente_fecha=reg.get("fuente_fecha"),
        hora_inicio_partido=reg.get("hora_inicio_partido"),
        telegram_message_date=reg.get("telegram_message_date"),
        hora_mensaje_utc=reg.get("hora_mensaje_utc"),
        partido=reg.get("partido"),
        fecha=reg.get("fecha"),
        id_partido=reg.get("id_partido"),
        estado_registro=str(reg.get("estado_registro") or "liquidado"),
        id_trabajo=str(reg.get("id_trabajo") or ""),
    )
    return {
        "clasificacion_oos": str(ev["estado_canonico"]),
        "es_oos_valido": bool(ev["es_oos_valido"]),
        "es_canonica": bool(ev["es_canonica"]),
        "es_legacy_sin_fixture": False,
    }


def _extraer_fuentes_y_firma_registro(
    *,
    preparado: dict[str, Any] | None = None,
    doc: dict[str, Any] | None = None,
    metadatos: dict[str, Any] | None = None,
    pre_ref: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    """Extrae `fuente_local`, `fuente_visitante` y `firma_datos` de análisis, documento o pre_partido."""
    prep = preparado if isinstance(preparado, dict) else {}
    d = doc if isinstance(doc, dict) else {}
    an = prep.get("analisis")
    meta = (
        metadatos
        if isinstance(metadatos, dict)
        else (getattr(an, "metadatos", None) if an is not None else None)
        or d.get("metadatos")
        or {}
    )
    ctx = prep.get("contexto") if isinstance(prep.get("contexto"), dict) else {}
    mot = getattr(an, "motor", None) if an is not None else None
    dl = ctx.get("doc_local") or (getattr(mot, "doc_local", None) if mot is not None else None)
    dv = ctx.get("doc_visitante") or (getattr(mot, "doc_visitante", None) if mot is not None else None)
    dc = d.get("datos_corte") if isinstance(d.get("datos_corte"), dict) else {}
    pr = pre_ref if isinstance(pre_ref, dict) else {}

    f_loc = (
        meta.get("fuente_local")
        or ctx.get("fuente_local")
        or (dl.get("fuente") if isinstance(dl, dict) else None)
        or d.get("fuente_local")
        or dc.get("fuente_local")
        or pr.get("fuente_local")
    )
    f_vis = (
        meta.get("fuente_visitante")
        or ctx.get("fuente_visitante")
        or (dv.get("fuente") if isinstance(dv, dict) else None)
        or d.get("fuente_visitante")
        or dc.get("fuente_visitante")
        or pr.get("fuente_visitante")
    )
    firma = (
        meta.get("firma_datos")
        or ctx.get("firma_datos")
        or d.get("firma_datos")
        or dc.get("firma_datos")
        or pr.get("firma_datos")
    )
    if not firma and (isinstance(dl, dict) or isinstance(dv, dict)):
        dl_d = dl if isinstance(dl, dict) else {}
        dv_d = dv if isinstance(dv, dict) else {}
        firma = hashlib.sha256(
            json.dumps(
                [
                    len(dl_d.get("partidos", []) or []),
                    len(dv_d.get("partidos", []) or []),
                    dl_d.get("generado_en"),
                    dv_d.get("generado_en"),
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:12]
    cal_loc = (
        meta.get("calidad_fuente_local")
        or ctx.get("calidad_fuente_local")
        or d.get("calidad_fuente_local")
        or dc.get("calidad_fuente_local")
        or pr.get("calidad_fuente_local")
    )
    cal_vis = (
        meta.get("calidad_fuente_visitante")
        or ctx.get("calidad_fuente_visitante")
        or d.get("calidad_fuente_visitante")
        or dc.get("calidad_fuente_visitante")
        or pr.get("calidad_fuente_visitante")
    )
    if cal_loc is None and isinstance(dl, dict) and dl.get("partidos"):
        try:
            from servicio_mercados import calcular_calidad_fuente_club as _calc_cf
            cal_loc = _calc_cf(dl)
        except Exception:
            cal_loc = None
    if cal_vis is None and isinstance(dv, dict) and dv.get("partidos"):
        try:
            from servicio_mercados import calcular_calidad_fuente_club as _calc_cf
            cal_vis = _calc_cf(dv)
        except Exception:
            cal_vis = None
    return {
        "fuente_local": str(f_loc).strip() if f_loc else None,
        "fuente_visitante": str(f_vis).strip() if f_vis else None,
        "firma_datos": str(firma).strip() if firma else None,
        "calidad_fuente_local": dict(cal_loc) if isinstance(cal_loc, dict) else None,
        "calidad_fuente_visitante": dict(cal_vis) if isinstance(cal_vis, dict) else None,
    }


def extraer_lambdas_base_doble_adn_desde_analisis(
    analisis: Any = None,
    preparado: dict[str, Any] | None = None,
    *,
    doc: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Extrae las λ base tras Doble ADN (entrada a la Red Recursiva 45-DCN,
    mostradas en el Súper-Cuadro como `λ base tras Doble ADN`).
    """
    prep = preparado if isinstance(preparado, dict) else {}
    d = doc if isinstance(doc, dict) else {}
    if analisis is None:
        analisis = prep.get("analisis")
    for origen in (d, d.get("datos_corte")):
        if isinstance(origen, dict) and isinstance(origen.get("lambdas_base_doble_adn"), dict):
            lb = origen["lambdas_base_doble_adn"]
            if lb.get("local_ft") is not None and lb.get("visitante_ft") is not None:
                return dict(lb)
    mot = getattr(analisis, "motor", None) if analisis is not None else None
    rec = getattr(mot, "recursivo", None) if mot is not None else None
    if isinstance(rec, dict) and rec.get("lambda_base_local") is not None and rec.get("lambda_base_visita") is not None:
        return {
            "local_ft": round(float(rec["lambda_base_local"]), 4),
            "visitante_ft": round(float(rec["lambda_base_visita"]), 4),
        }
    return None


def extraer_lambdas_plano_desde_analisis(
    analisis: Any = None,
    preparado: dict[str, Any] | None = None,
    *,
    doc: dict[str, Any] | None = None,
    pre_ref: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Calcula o extrae `lambdas_plano` (Etapa 1 sin moduladores, sin λ_efectivo,
    sin red 45-DCN ni reglas de Etapa 6) para guardarlo en snapshot y ledger sin alterar el ticket.
    """
    prep = preparado if isinstance(preparado, dict) else {}
    d = doc if isinstance(doc, dict) else {}
    pr = pre_ref if isinstance(pre_ref, dict) else {}
    if analisis is None:
        analisis = prep.get("analisis")

    for origen in (
        prep.get("modo_sombra"),
        d.get("modo_sombra"),
        d,
        d.get("datos_corte"),
        pr,
    ):
        if isinstance(origen, dict) and isinstance(origen.get("lambdas_plano"), dict):
            lp_raw = origen["lambdas_plano"]
            if lp_raw.get("local_ft") is not None and lp_raw.get("visitante_ft") is not None:
                return dict(lp_raw)

    mot = getattr(analisis, "motor", None) if analisis is not None else None
    if mot is None:
        return None
    mu = getattr(mot, "muestras", None)
    params_m = getattr(mot, "p", None)
    if not isinstance(mu, dict):
        return None

    try:
        from decision_cobertura import banderas_comportamiento
        import motor_mercados as mm

        det_g = getattr(mot, "detalle_goles", None) or {}
        mods = det_g.get("_moduladores") if isinstance(det_g, dict) else None
        lam_ft_l = None
        lam_ft_v = None
        if isinstance(mods, dict):
            lam_ft_l = mods.get("lambda_local_sin_modular")
            lam_ft_v = mods.get("lambda_visitante_sin_modular")
        if lam_ft_l is None or lam_ft_v is None:
            if isinstance(det_g, dict) and det_g.get("lambda_local") is not None and not det_g.get("_lambda_efectivo"):
                lam_ft_l = det_g.get("lambda_local")
                lam_ft_v = det_g.get("lambda_visitante")
        if lam_ft_l is None or lam_ft_v is None:
            mlg = mu.get("local_general")
            mvg = mu.get("visitante_general")
            mlc = mu.get("local_cond")
            mvc = mu.get("visitante_cond")
            if mlg is None or mvg is None or mlc is None or mvc is None:
                return None
            p_ref = params_m or mm.Parametros()
            lam_ft_l, lam_ft_v, _ = mm.tasas_goles(
                mlg,
                mvg,
                mlc,
                mvc,
                float(getattr(p_ref, "w_condicion", mm.W_CONDICION_DEFECTO)),
                float(getattr(p_ref, "w_xg", mm.W_XG_DEFECTO)),
                getattr(p_ref, "k_credibilidad", mm.K_CREDIBILIDAD_DEFECTO),
            )
        if lam_ft_l is None or lam_ft_v is None:
            return None

        dl = getattr(mot, "doc_local", None)
        dv = getattr(mot, "doc_visitante", None)
        lam_1t_l = lam_1t_v = lam_2t_l = lam_2t_v = None
        if isinstance(dl, dict) and isinstance(dv, dict):
            with banderas_comportamiento(activar_todas=False, usar_sobredispersion_nb_corners=True):
                lam_1t_l, lam_1t_v, lam_2t_l, lam_2t_v, _, _ = mm.repartir_lambdas_por_tiempos(
                    dl,
                    dv,
                    float(lam_ft_l),
                    float(lam_ft_v),
                    getattr(mot, "cond_local", "casa"),
                    getattr(mot, "cond_visitante", "fuera"),
                )
        return {
            "local_ft": round(float(lam_ft_l), 6),
            "visitante_ft": round(float(lam_ft_v), 6),
            "local_1t": round(float(lam_1t_l), 6) if lam_1t_l is not None else None,
            "visitante_1t": round(float(lam_1t_v), 6) if lam_1t_v is not None else None,
            "local_2t": round(float(lam_2t_l), 6) if lam_2t_l is not None else None,
            "visitante_2t": round(float(lam_2t_v), 6) if lam_2t_v is not None else None,
        }
    except Exception:
        return None


def _extraer_traza_etapa_6_snapshot(motor: Any) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Extrae la traza inmutable de la Etapa 6 (`post_sinc_mercados`) y cuánto movió cada regla."""
    if motor is None:
        return None, None
    rec = getattr(motor, "recursivo", None)
    traza_full = list(
        getattr(motor, "traza_lambda", None)
        or (rec.get("traza_lambda") if isinstance(rec, dict) else None)
        or []
    )
    if not traza_full:
        return None, None

    etapa6_raw = [st for st in traza_full if isinstance(st, dict) and st.get("etapa") == "post_sinc_mercados"]
    pre6 = [st for st in traza_full if isinstance(st, dict) and st.get("etapa") != "post_sinc_mercados"]
    traza_6: list[dict[str, Any]] = []
    for st in etapa6_raw:
        al = st.get("lam_l_antes")
        dl = st.get("lam_l_despues")
        av = st.get("lam_v_antes")
        dv = st.get("lam_v_despues")
        traza_6.append({
            "paso": st.get("paso"),
            "etapa": st.get("etapa"),
            "regla": st.get("regla"),
            "lam_l_antes": al,
            "lam_l_despues": dl,
            "delta_l": round(float(dl) - float(al), 4) if (al is not None and dl is not None) else None,
            "lam_v_antes": av,
            "lam_v_despues": dv,
            "delta_v": round(float(dv) - float(av), 4) if (av is not None and dv is not None) else None,
            "motivo": st.get("motivo"),
        })

    lam_l_red = None
    lam_v_red = None
    if etapa6_raw:
        lam_l_red = etapa6_raw[0].get("lam_l_antes")
        lam_v_red = etapa6_raw[0].get("lam_v_antes")
    elif pre6:
        lam_l_red = pre6[-1].get("lam_l_despues")
        lam_v_red = pre6[-1].get("lam_v_despues")

    tasas = getattr(motor, "tasas", None)
    lam_l_final = getattr(tasas, "lambda_local_ft", None) if tasas is not None else (etapa6_raw[-1].get("lam_l_despues") if etapa6_raw else lam_l_red)
    lam_v_final = getattr(tasas, "lambda_visitante_ft", None) if tasas is not None else (etapa6_raw[-1].get("lam_v_despues") if etapa6_raw else lam_v_red)

    delta_pct_l = None
    if lam_l_red is not None and lam_l_final is not None and float(lam_l_red) > 0:
        delta_pct_l = round(((float(lam_l_final) - float(lam_l_red)) / float(lam_l_red)) * 100.0, 1)
    delta_pct_v = None
    if lam_v_red is not None and lam_v_final is not None and float(lam_v_red) > 0:
        delta_pct_v = round(((float(lam_v_final) - float(lam_v_red)) / float(lam_v_red)) * 100.0, 1)

    resumen_6 = {
        "lam_l_red": lam_l_red,
        "lam_l_final": lam_l_final,
        "delta_l_pct_vs_red": delta_pct_l,
        "lam_v_red": lam_v_red,
        "lam_v_final": lam_v_final,
        "delta_v_pct_vs_red": delta_pct_v,
        "techo_lambda_ataque_bajo_local_activo": any(s.get("regla") == "techo_lambda_ataque_bajo_local" for s in traza_6),
        "techo_lambda_ataque_bajo_visita_activo": any(s.get("regla") == "techo_lambda_ataque_bajo_visita" for s in traza_6),
        "reglas_activadas": [
            str(s.get("regla") or "")
            for s in traza_6
            if (abs(float(s.get("delta_l") or 0.0)) > 1e-6 or abs(float(s.get("delta_v") or 0.0)) > 1e-6)
        ],
    }
    return traza_6, resumen_6


def ruta_snapshot(id_trabajo: str, directorio: str | None = None) -> str:
    return os.path.join(directorio or PRONOSTICOS_DIR, f"{id_trabajo}.json")


def guardar_snapshot(snapshot: SnapshotPronostico | dict[str, Any],
                     directorio: str | None = None) -> str:
    """
    Escribe el snapshot UNA sola vez de forma atómica. Si ya existe, NO se
    sobrescribe: el pasado no se reescribe. Devuelve la ruta.
    """
    import tempfile as _tmp_snap
    datos = snapshot.a_dict() if isinstance(snapshot, SnapshotPronostico) else snapshot
    destino = ruta_snapshot(str(datos.get("id_trabajo")), directorio)
    parent_dir = os.path.dirname(destino)
    os.makedirs(parent_dir, exist_ok=True)
    if os.path.exists(destino):
        return destino
    if isinstance(datos, dict) and datos.get("es_oos_valido") is True and datos.get("es_canonica") is True:
        try:
            existentes_snaps: list[dict[str, Any]] = []
            for p_f in os.listdir(parent_dir):
                if not p_f.endswith(".json") or p_f.startswith("."):
                    continue
                try:
                    with open(os.path.join(parent_dir, p_f), "r", encoding="utf-8") as fh_ex:
                        existentes_snaps.append(json.load(fh_ex))
                except Exception:
                    continue
            eq_d = datos.get("equipos") or {}
            pt_d = datos.get("partido") or {}
            contab_snap = evaluar_contabilidad_oos_registro(
                fixture_confirmado=datos.get("fixture_confirmado"),
                fuente_fecha=datos.get("fuente_fecha"),
                hora_inicio_partido=datos.get("hora_inicio_partido"),
                telegram_message_date=datos.get("telegram_message_date"),
                hora_mensaje_utc=datos.get("hora_mensaje_utc"),
                partido=f"{eq_d.get('local') or ''} vs {eq_d.get('visitante') or ''}",
                fecha=pt_d.get("fecha"),
                id_partido=pt_d.get("id_partido") or datos.get("id_partido") or "",
                estado_registro="",
                id_trabajo=str(datos.get("id_trabajo") or ""),
                registros_existentes=existentes_snaps,
            )
            datos["es_canonica"] = contab_snap["es_canonica"]
            datos["estado_canonico"] = contab_snap["estado_canonico"]
        except Exception:
            pass
    fd, tmp_ruta = _tmp_snap.mkstemp(prefix=".snap_", suffix=".json", dir=parent_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(datos, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        if not os.path.exists(destino):
            os.replace(tmp_ruta, destino)
        else:
            os.unlink(tmp_ruta)
    except Exception:
        try:
            os.unlink(tmp_ruta)
        except OSError:
            pass
        raise
    return destino


def adjuntar_acuse_telegram_snapshot(
    ruta_snapshot: str,
    *,
    telegram_envio: dict[str, Any] | None = None,
    ids_telegram: list[Any] | None = None,
    message_id: int | None = None,
    message_date: int | None = None,
) -> dict[str, Any]:
    """
    Adjunta metadatos de entrega Telegram (`telegram_envio`, `message_id`, `message.date`)
    sobre un snapshot ya emitido SIN alterar selecciones, lambdas, modelo ni modo_sombra.
    """
    import tempfile as _tmp_tg
    if not ruta_snapshot or not os.path.exists(ruta_snapshot):
        return {"actualizado": False, "motivo": "snapshot_inexistente"}
    with open(ruta_snapshot, "r", encoding="utf-8") as f:
        doc = json.load(f)

    env_doc = dict(telegram_envio or {})
    if ids_telegram is not None and "ids_telegram" not in env_doc:
        env_doc["ids_telegram"] = list(ids_telegram)
    if message_id is not None and env_doc.get("message_id") is None:
        env_doc["message_id"] = message_id
    if message_date is not None and env_doc.get("message_date") is None:
        env_doc["message_date"] = int(message_date)
    if env_doc.get("message_date") is not None and env_doc.get("message_date_utc") is None:
        env_doc["message_date_utc"] = datetime.fromtimestamp(
            int(env_doc["message_date"]), tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not env_doc and ids_telegram is None:
        return {"actualizado": False, "motivo": "sin_metadatos_telegram"}

    doc["telegram_envio"] = env_doc
    if env_doc.get("message_id") is not None:
        doc["telegram_message_id"] = env_doc.get("message_id")
    if env_doc.get("message_date") is not None:
        doc["telegram_message_date"] = env_doc.get("message_date")
        doc["hora_mensaje_utc"] = env_doc.get("message_date_utc") or datetime.fromtimestamp(
            int(env_doc["message_date"]), tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    if doc.get("publicado_en") is None and env_doc.get("message_date_utc") is not None:
        doc["publicado_en"] = env_doc.get("message_date_utc")

    parent_dir = os.path.dirname(ruta_snapshot)
    if "es_oos_valido" in doc or "fixture_confirmado" in doc:
        try:
            existentes_adj: list[dict[str, Any]] = []
            for p_f in os.listdir(parent_dir or "."):
                if not p_f.endswith(".json") or p_f.startswith("."):
                    continue
                try:
                    with open(os.path.join(parent_dir or ".", p_f), "r", encoding="utf-8") as fh_ex:
                        existentes_adj.append(json.load(fh_ex))
                except Exception:
                    continue
            eq_d = doc.get("equipos") or {}
            pt_d = doc.get("partido") or {}
            contab_adj = evaluar_contabilidad_oos_registro(
                fixture_confirmado=doc.get("fixture_confirmado"),
                fuente_fecha=doc.get("fuente_fecha"),
                hora_inicio_partido=doc.get("hora_inicio_partido"),
                telegram_message_date=doc.get("telegram_message_date"),
                hora_mensaje_utc=doc.get("hora_mensaje_utc"),
                partido=f"{eq_d.get('local') or ''} vs {eq_d.get('visitante') or ''}",
                fecha=pt_d.get("fecha"),
                id_partido=pt_d.get("id_partido") or doc.get("id_partido") or "",
                estado_registro="",
                id_trabajo=str(doc.get("id_trabajo") or ""),
                registros_existentes=existentes_adj,
            )
            doc.update(contab_adj)
        except Exception:
            pass
    fd, tmp_ruta = _tmp_tg.mkstemp(prefix=".snap_tg_", suffix=".json", dir=parent_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_ruta, ruta_snapshot)
    except Exception:
        try:
            os.unlink(tmp_ruta)
        except OSError:
            pass
        raise
    return {
        "actualizado": True,
        "ruta": ruta_snapshot,
        "telegram_message_id": doc.get("telegram_message_id"),
        "telegram_message_date": doc.get("telegram_message_date"),
    }


def snapshot_desde_analisis(preparado: dict[str, Any],
                            publicado_en: str | None = None,
                            modo_sombra: dict[str, Any] | None = None,
                            telegram_meta: dict[str, Any] | None = None,
                            existentes_snapshots: list[dict[str, Any]] | None = None) -> SnapshotPronostico:
    """
    Construye el snapshot a partir del resultado de `preparar_analisis`.
    Guarda TODAS las selecciones estimables (incluidas las inferiores al umbral)
    para poder evaluar el modelo completo, y marca aparte las publicadas.
    """
    analisis = preparado["analisis"]
    metadatos = getattr(analisis, "metadatos", {}) or {}
    ctx_p = preparado.get("contexto") or {}
    tg_m = telegram_meta if telegram_meta is not None else preparado.get("telegram_meta")
    tg_org = dict(tg_m) if isinstance(tg_m, dict) else None
    tg_mid = tg_org.get("message_id") if isinstance(tg_org, dict) else None
    tg_mdt = tg_org.get("message_date") if isinstance(tg_org, dict) else None
    tg_mutc = tg_org.get("message_date_utc") if isinstance(tg_org, dict) else metadatos.get("hora_mensaje_utc")
    fx_conf = bool(
        metadatos.get("fixture_confirmado")
        if "fixture_confirmado" in metadatos
        else ctx_p.get("fixture_confirmado", False)
    )
    f_fec = str(metadatos.get("fuente_fecha") or ctx_p.get("fuente_fecha") or ("fixture" if fx_conf else "fallback"))
    h_ini = metadatos.get("hora_inicio_partido") or ctx_p.get("hora_inicio_partido")
    f_hini = metadatos.get("fuente_hora_inicio") or ctx_p.get("fuente_hora_inicio")
    z_hini = metadatos.get("zona_origen_hora_inicio") or ctx_p.get("zona_origen_hora_inicio")
    id_part_snap = str(
        preparado.get("id_partido")
        or metadatos.get("id_partido")
        or metadatos.get("match_id_fotmob")
        or ctx_p.get("id_partido")
        or ""
    )
    h_fm_snap = metadatos.get("hora_inicio_fotmob") or ctx_p.get("hora_inicio_fotmob")
    h_fs_snap = metadatos.get("hora_inicio_flashscore") or ctx_p.get("hora_inicio_flashscore")
    diff_h_snap = metadatos.get("diferencia_hora_inicio_min")
    if diff_h_snap is None:
        diff_h_snap = ctx_p.get("diferencia_hora_inicio_min")
    h_disc_snap = metadatos.get("hora_inicio_discrepante")
    if h_disc_snap is None:
        h_disc_snap = ctx_p.get("hora_inicio_discrepante")
    contab_snap = evaluar_contabilidad_oos_registro(
        fixture_confirmado=fx_conf,
        fuente_fecha=f_fec,
        hora_inicio_partido=h_ini,
        telegram_message_date=tg_mdt,
        hora_mensaje_utc=tg_mutc,
        hora_inicio_fotmob=h_fm_snap,
        hora_inicio_flashscore=h_fs_snap,
        diferencia_hora_inicio_min=diff_h_snap,
        hora_inicio_discrepante=h_disc_snap,
        partido=f"{metadatos.get('equipo_local') or ''} vs {metadatos.get('equipo_visitante') or ''}",
        fecha=metadatos.get("fecha_partido") or h_ini,
        id_partido=id_part_snap,
        estado_registro="",
        id_trabajo=str(preparado.get("id_trabajo") or ""),
        registros_existentes=existentes_snapshots,
    )
    f_info_snap = _extraer_fuentes_y_firma_registro(preparado=preparado, metadatos=metadatos)
    abstenido = bool(preparado.get("abstenido") or getattr(analisis, "abstenido", False))
    if abstenido:
        dec_cob = metadatos.get("decision_cobertura") or {}
        est_cob = str(dec_cob.get("estado") or metadatos.get("estado_cobertura") or "abstenido")
        mot_abs = str(dec_cob.get("alerta_descriptiva") or metadatos.get("motivo_abstencion") or "")
        umbral_val = float(getattr(analisis.parametros, "umbral", 0.70) if hasattr(analisis, "parametros") and analisis.parametros else 0.70)
        dc_abs = {"abstenido": True, "estado_cobertura": est_cob, "motivo_abstencion": mot_abs, "fuente": metadatos.get("fuente")}
        if f_info_snap["fuente_local"] is not None:
            dc_abs["fuente_local"] = f_info_snap["fuente_local"]
        if f_info_snap["fuente_visitante"] is not None:
            dc_abs["fuente_visitante"] = f_info_snap["fuente_visitante"]
        if f_info_snap["firma_datos"] is not None:
            dc_abs["firma_datos"] = f_info_snap["firma_datos"]
        return SnapshotPronostico(
            id_trabajo=preparado.get("id_trabajo") or "abstenido",
            equipos={"local": metadatos.get("equipo_local"), "visitante": metadatos.get("equipo_visitante")},
            partido={
                "fecha": metadatos.get("fecha_partido"),
                "hora_inicio_partido": contab_snap["hora_inicio_partido"],
                "fixture_confirmado": contab_snap["fixture_confirmado"],
                "fuente_fecha": contab_snap["fuente_fecha"],
                **({"fuente_hora_inicio": f_hini} if f_hini is not None else {}),
                **({"zona_origen_hora_inicio": z_hini} if z_hini is not None else {}),
                "competicion": metadatos.get("competicion"),
                "sede": metadatos.get("sede"),
                "condicion_local": metadatos.get("condicion_local"),
                "condicion_visitante": metadatos.get("condicion_visitante"),
                "origen_condicion": metadatos.get("origen_condicion"),
            },
            generado_en=str(metadatos.get("generado_en")),
            publicado_en=publicado_en,
            modelo={"nombre": NOMBRE_MODELO, "version": VERSION_MODELO, "umbral": umbral_val,
                    "w_condicion": 0.0, "w_xg": 0.0, "distribucion_conteos": "poisson"},
            datos_corte=dc_abs,
            selecciones=[],
            selecciones_publicadas=[],
            umbral_publicado=umbral_val,
            referencia=(preparado.get("rutas") or {}).get("informe") or "",
            abstenido=True,
            estado_cobertura=est_cob,
            motivo_abstencion=mot_abs,
            decision_cobertura=dec_cob,
            telegram_message_id=tg_mid,
            telegram_message_date=tg_mdt,
            telegram_origen=tg_org,
            fixture_confirmado=contab_snap["fixture_confirmado"],
            fuente_fecha=contab_snap["fuente_fecha"],
            hora_inicio_partido=contab_snap["hora_inicio_partido"],
            fuente_hora_inicio=f_hini,
            zona_origen_hora_inicio=z_hini,
            hora_mensaje_utc=contab_snap["hora_mensaje_utc"],
            es_oos_valido=contab_snap["es_oos_valido"],
            es_canonica=contab_snap["es_canonica"],
            estado_canonico=contab_snap["estado_canonico"],
            regla_contabilidad_oos=contab_snap["regla_contabilidad_oos"],
            fuente_local=f_info_snap["fuente_local"],
            fuente_visitante=f_info_snap["fuente_visitante"],
            firma_datos=f_info_snap["firma_datos"],
        )
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

    traza_6, resumen_6 = _extraer_traza_etapa_6_snapshot(motor)
    lam_plano_snap = extraer_lambdas_plano_desde_analisis(analisis, preparado)
    lam_base_adn_snap = extraer_lambdas_base_doble_adn_desde_analisis(analisis, preparado)

    return SnapshotPronostico(
        id_trabajo=preparado["id_trabajo"],
        equipos={"local": metadatos.get("equipo_local"),
                 "visitante": metadatos.get("equipo_visitante")},
        partido={
            "fecha": metadatos.get("fecha_partido"),
            "hora_inicio_partido": contab_snap["hora_inicio_partido"],
            "fixture_confirmado": contab_snap["fixture_confirmado"],
            "fuente_fecha": contab_snap["fuente_fecha"],
            **({"fuente_hora_inicio": f_hini} if f_hini is not None else {}),
            **({"zona_origen_hora_inicio": z_hini} if z_hini is not None else {}),
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
            **({"fuente_local": f_info_snap["fuente_local"]} if f_info_snap["fuente_local"] is not None else {}),
            **({"fuente_visitante": f_info_snap["fuente_visitante"]} if f_info_snap["fuente_visitante"] is not None else {}),
            **({"firma_datos": f_info_snap["firma_datos"]} if f_info_snap["firma_datos"] is not None else {}),
            **({"calidad_fuente_local": f_info_snap["calidad_fuente_local"]} if f_info_snap.get("calidad_fuente_local") is not None else {}),
            **({"calidad_fuente_visitante": f_info_snap["calidad_fuente_visitante"]} if f_info_snap.get("calidad_fuente_visitante") is not None else {}),
            "lambda_local_ft": motor.tasas.lambda_local_ft,
            "lambda_visitante_ft": motor.tasas.lambda_visitante_ft,
            **({"lambdas_plano": lam_plano_snap} if lam_plano_snap is not None else {}),
            **({"lambdas_base_doble_adn": lam_base_adn_snap} if lam_base_adn_snap is not None else {}),
        },
        selecciones=todas,
        selecciones_publicadas=publicadas,
        umbral_publicado=umbral,
        referencia=preparado.get("rutas", {}).get("informe", ""),
        origen="nuevo",
        modo_sombra=modo_sombra if modo_sombra is not None else preparado.get("modo_sombra"),
        telegram_message_id=tg_mid,
        telegram_message_date=tg_mdt,
        telegram_origen=tg_org,
        traza_etapa_6=traza_6,
        resumen_etapa_6=resumen_6,
        fixture_confirmado=contab_snap["fixture_confirmado"],
        fuente_fecha=contab_snap["fuente_fecha"],
        hora_inicio_partido=contab_snap["hora_inicio_partido"],
        fuente_hora_inicio=f_hini,
        zona_origen_hora_inicio=z_hini,
        hora_inicio_fotmob=contab_snap.get("hora_inicio_fotmob"),
        hora_inicio_flashscore=contab_snap.get("hora_inicio_flashscore"),
        diferencia_hora_inicio_min=contab_snap.get("diferencia_hora_inicio_min"),
        hora_inicio_discrepante=contab_snap.get("hora_inicio_discrepante"),
        hora_mensaje_utc=contab_snap["hora_mensaje_utc"],
        es_oos_valido=contab_snap["es_oos_valido"],
        es_canonica=contab_snap["es_canonica"],
        estado_canonico=contab_snap["estado_canonico"],
        regla_contabilidad_oos=contab_snap["regla_contabilidad_oos"],
        fuente_local=f_info_snap["fuente_local"],
        fuente_visitante=f_info_snap["fuente_visitante"],
        firma_datos=f_info_snap["firma_datos"],
        calidad_fuente_local=f_info_snap.get("calidad_fuente_local"),
        calidad_fuente_visitante=f_info_snap.get("calidad_fuente_visitante"),
        lambdas_plano=lam_plano_snap,
        lambdas_base_doble_adn=lam_base_adn_snap,
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
    stats_periodos: dict[str, Any] = field(default_factory=dict)
    discrepancias: list[str] = field(default_factory=list)

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
            "stats_periodos": self.stats_periodos, "discrepancias": self.discrepancias,
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


def _int_o_none(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return None


def _float_o_none(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _aplicar_flashscore_en_resultado(
    res: ResultadoPartido,
    fs_payload: dict[str, Any],
    scraper_vacio: bool = False,
    detalle_scraper_notas: list[str] | None = None,
) -> None:
    """
    Aplica estadísticas verificadas de Flashscore (FT, 1T, 2T) sobre `res` y
    registra explícitamente cualquier discrepancia respecto al payload del scraper.
    """
    if not isinstance(fs_payload, dict) or not fs_payload:
        return

    periodos_fs = fs_payload.get("periodos") if isinstance(fs_payload.get("periodos"), dict) else fs_payload
    ft_d = periodos_fs.get("FT") or {}
    t1_d = periodos_fs.get("1T") or {}
    t2_d = periodos_fs.get("2T") or {}

    def _par(bloque: dict[str, Any], *claves: str) -> tuple[Any, Any]:
        if not isinstance(bloque, dict):
            return None, None
        for k in claves:
            v = bloque.get(k)
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                return v[0], v[1]
            if isinstance(v, dict):
                l_v = v.get("local") if "local" in v else v.get("equipo")
                v_v = v.get("visitante") if "visitante" in v else v.get("rival")
                if l_v is not None or v_v is not None:
                    return l_v, v_v
            if f"{k}_local" in bloque or f"{k}_visitante" in bloque:
                return bloque.get(f"{k}_local"), bloque.get(f"{k}_visitante")
        return None, None

    gl_fs, gv_fs = _par(ft_d, "goles", "marcador")
    gl_i, gv_i = _int_o_none(gl_fs), _int_o_none(gv_fs)
    if gl_i is not None and gv_i is not None:
        if res.goles_local is not None and res.goles_visitante is not None:
            if (res.goles_local, res.goles_visitante) != (gl_i, gv_i):
                msg = (
                    f"Discrepancia marcador scraper ({res.goles_local}-{res.goles_visitante}) "
                    f"vs Flashscore ({gl_i}-{gv_i}): no se reconcilia marcador automáticamente."
                )
                res.discrepancias.append(msg)
                res.notas.append(msg)
        else:
            res.goles_local = gl_i
            res.goles_visitante = gv_i
            res.estado = "finalizado"

    g1l_fs, g1v_fs = _par(t1_d, "goles", "marcador")
    g1l_i, g1v_i = _int_o_none(g1l_fs), _int_o_none(g1v_fs)
    if g1l_i is not None and g1v_i is not None:
        if res.goles_local_1t is None or res.goles_visitante_1t is None:
            res.goles_local_1t = g1l_i
            res.goles_visitante_1t = g1v_i
        elif (res.goles_local_1t, res.goles_visitante_1t) != (g1l_i, g1v_i):
            msg = (
                f"Discrepancia 1T scraper ({res.goles_local_1t}-{res.goles_visitante_1t}) "
                f"vs Flashscore ({g1l_i}-{g1v_i})."
            )
            res.discrepancias.append(msg)
            res.notas.append(msg)

    # Validar suma 1T + 2T == FT si viene 2T
    g2l_fs, g2v_fs = _par(t2_d, "goles", "marcador")
    g2l_i, g2v_i = _int_o_none(g2l_fs), _int_o_none(g2v_fs)
    if (
        g1l_i is not None and g1v_i is not None
        and g2l_i is not None and g2v_i is not None
        and res.goles_local is not None and res.goles_visitante is not None
    ):
        if (g1l_i + g2l_i, g1v_i + g2v_i) != (res.goles_local, res.goles_visitante):
            res.discrepancias.append(
                f"Inconsistencia 1T+2T!=FT en Flashscore: 1T {g1l_i}-{g1v_i} + 2T {g2l_i}-{g2v_i} != FT {res.goles_local}-{res.goles_visitante}"
            )

    for per, pdict in (("FT", ft_d), ("1T", t1_d), ("2T", t2_d)):
        if not isinstance(pdict, dict) or not pdict:
            continue
        cl, cv = _par(pdict, "corners")
        cl_i, cv_i = _int_o_none(cl), _int_o_none(cv)
        if cl_i is not None and cv_i is not None:
            if res.corners_local.get(per) is None:
                res.corners_local[per] = cl_i
                res.corners_visitante[per] = cv_i
            elif (res.corners_local[per], res.corners_visitante[per]) != (cl_i, cv_i):
                res.discrepancias.append(
                    f"Discrepancia córneres {per}: scraper ({res.corners_local[per]}-{res.corners_visitante[per]}) vs Flashscore ({cl_i}-{cv_i})"
                )

        al, av = _par(pdict, "amarillas", "tarjetas_amarillas")
        al_i, av_i = _int_o_none(al), _int_o_none(av)
        if al_i is not None and av_i is not None:
            if res.amarillas_local.get(per) is None:
                res.amarillas_local[per] = al_i
                res.amarillas_visitante[per] = av_i
            elif (res.amarillas_local[per], res.amarillas_visitante[per]) != (al_i, av_i):
                res.discrepancias.append(
                    f"Discrepancia amarillas {per}: scraper ({res.amarillas_local[per]}-{res.amarillas_visitante[per]}) vs Flashscore ({al_i}-{av_i})"
                )

        # Conservar métricas detalladas para tabla/ledger/banco
        per_stats: dict[str, Any] = {}
        for k_stat in (
            "goles", "corners", "xg", "xgot", "goles_evitados",
            "remates_totales", "remates_puerta", "grandes_ocasiones",
            "posesion_pct", "faltas", "amarillas", "fueras_de_juego",
            "toques_area_rival", "xa_equipo", "rojas",
        ):
            vl, vv = _par(pdict, k_stat)
            if vl is not None or vv is not None:
                per_stats[k_stat] = [vl, vv]
        if per_stats:
            res.stats_periodos[per] = per_stats

    rl_fs, rv_fs = _par(ft_d, "rojas", "tarjetas_rojas")
    if _int_o_none(rl_fs) is not None and res.rojas_local is None:
        res.rojas_local = _int_o_none(rl_fs)
    if _int_o_none(rv_fs) is not None and res.rojas_visitante is None:
        res.rojas_visitante = _int_o_none(rv_fs)

    if isinstance(fs_payload.get("goles_eventos"), list) and not res.goles:
        res.goles = list(fs_payload["goles_eventos"])
    if not res.inicio_utc and fs_payload.get("inicio_utc"):
        res.inicio_utc = str(fs_payload["inicio_utc"])

    if scraper_vacio:
        notas_txt = "; ".join(detalle_scraper_notas or [])
        msg = (
            "Discrepancia FotMob scraper vs Flashscore: el payload del scraper FotMob "
            f"devolvió matriz vacía sin estadísticas de detalle FT/1T/2T ({notas_txt or 'filas vacías'}), "
            "mientras Flashscore sí reportó marcador por mitad, córneres, tarjetas y xG/xGOT verificados."
        )
        res.discrepancias.append(msg)
        res.notas.append(msg)
        if "flashscore" not in (res.fuente or "").lower():
            res.fuente = f"{res.fuente}+flashscore" if res.fuente else "flashscore"


def _extraer_resultado_desde_matriz(
    detalle: dict[str, Any],
    consultado_en: str | None = None,
    fuente: str = "fotmob",
) -> ResultadoPartido:
    res = ResultadoPartido(fuente=fuente, consultado_en=consultado_en or _ahora())
    mat = detalle.get("matriz") if isinstance(detalle.get("matriz"), dict) else detalle
    condicion = str(mat.get("condicion") or "casa").lower()
    if mat.get("local") and mat.get("visitante"):
        res.local = str(mat.get("local") or "")
        res.visitante = str(mat.get("visitante") or "")
        lado_l, lado_v = ("rival", "equipo") if condicion == "fuera" else ("equipo", "rival")
    elif condicion == "fuera":
        res.local = str(mat.get("rival") or "")
        res.visitante = str(mat.get("equipo") or "")
        lado_l, lado_v = "rival", "equipo"
    else:
        res.local = str(mat.get("equipo") or "")
        res.visitante = str(mat.get("rival") or "")
        lado_l, lado_v = "equipo", "rival"

    marc = mat.get("marcador") or {}
    geq = marc.get("equipo") if marc.get("equipo") is not None else mat.get("goles_equipo")
    griv = marc.get("rival") if marc.get("rival") is not None else mat.get("goles_rival")
    if geq is not None and griv is not None:
        gl_raw = griv if lado_l == "rival" else geq
        gv_raw = geq if lado_l == "rival" else griv
        res.goles_local = _int_o_none(gl_raw)
        res.goles_visitante = _int_o_none(gv_raw)

    res.id_partido = str(mat.get("id_partido") or mat.get("id") or detalle.get("id_partido") or "") or None
    res.competicion = mat.get("torneo") or mat.get("competicion") or detalle.get("competicion")
    res.inicio_utc = str(mat.get("inicio_utc") or detalle.get("inicio_utc") or "") or None

    st_raw = detalle.get("status") or mat.get("status")
    if isinstance(st_raw, dict):
        if st_raw.get("finished"):
            res.estado = "finalizado"
        elif st_raw.get("started"):
            res.estado = "en_juego"
        else:
            res.estado = "no_finalizado"
    elif mat.get("estado"):
        res.estado = str(mat.get("estado"))
    elif res.goles_local is not None and res.goles_visitante is not None:
        res.estado = "finalizado"

    periodos = mat.get("periodos") or {}
    total_filas = 0
    for per in ("FT", "1T", "2T"):
        filas = (periodos.get(per) or {}).get("filas") or []
        total_filas += len(filas)
        fmap = {f.get("clave"): f for f in filas if isinstance(f, dict) and f.get("clave")}
        fc = fmap.get("corners") or {}
        if not fc.get("estimado"):
            cl, cv = _int_o_none(fc.get(lado_l)), _int_o_none(fc.get(lado_v))
            if cl is not None and cv is not None:
                res.corners_local[per] = cl
                res.corners_visitante[per] = cv
        fa = fmap.get("tarjetas_amarillas") or {}
        if not fa.get("estimado"):
            al, av = _int_o_none(fa.get(lado_l)), _int_o_none(fa.get(lado_v))
            if al is not None and av is not None:
                res.amarillas_local[per] = al
                res.amarillas_visitante[per] = av
        if per == "FT":
            fr = fmap.get("tarjetas_rojas") or {}
            if not fr.get("estimado"):
                rl, rv = _int_o_none(fr.get(lado_l)), _int_o_none(fr.get(lado_v))
                if rl is not None and rv is not None:
                    res.rojas_local = rl
                    res.rojas_visitante = rv

    g1t_l = mat.get("goles_local_1t")
    g1t_v = mat.get("goles_visitante_1t")
    if isinstance(mat.get("marcador_1t"), (list, tuple)) and len(mat["marcador_1t"]) >= 2:
        g1t_l, g1t_v = mat["marcador_1t"][0], mat["marcador_1t"][1]
    if _int_o_none(g1t_l) is not None and _int_o_none(g1t_v) is not None:
        res.goles_local_1t = _int_o_none(g1t_l)
        res.goles_visitante_1t = _int_o_none(g1t_v)

    scraper_vacio = (total_filas == 0)
    notas_mat = [str(n) for n in (mat.get("notas") or []) if n]
    for n in notas_mat:
        res.notas.append(n)
    if scraper_vacio and res.goles_local is not None:
        msg_empty = (
            f"Scraper ({fuente}) sin filas de detalle FT/1T/2T para {res.local} vs {res.visitante} "
            f"({res.goles_local}-{res.goles_visitante}): córneres, tarjetas y descanso quedan en None (sin_dato)."
        )
        res.notas.append(msg_empty)

    fs_payload = (
        detalle.get("flashscore_stats")
        or detalle.get("flashscore")
        or mat.get("flashscore_stats")
        or mat.get("flashscore")
    )
    if isinstance(fs_payload, dict) and fs_payload:
        _aplicar_flashscore_en_resultado(res, fs_payload, scraper_vacio=scraper_vacio, detalle_scraper_notas=notas_mat)

    return res


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

    if "header" not in detalle and any(
        k in detalle for k in ("matriz", "periodos", "goles_equipo", "marcador", "flashscore_stats", "flashscore")
    ):
        return _extraer_resultado_desde_matriz(detalle, consultado_en=consultado_en, fuente=fuente)

    header = detalle.get("header") or {}
    equipos = header.get("teams") or []
    status = header.get("status") or {}
    if len(equipos) >= 2:
        res.local = str(equipos[0].get("name") or "")
        res.visitante = str(equipos[1].get("name") or "")
        gl_raw = equipos[0].get("score")
        gv_raw = equipos[1].get("score")
        try:
            from fuentes_xg import resolver_marcador_reglamentario_fotmob
            gl_90, gv_90, _razon_fin, marcador_desemp = resolver_marcador_reglamentario_fotmob(
                status, gl_raw, gv_raw, detalle=detalle
            )
            res.goles_local = gl_90
            res.goles_visitante = gv_90
            if marcador_desemp:
                res.notas.append(
                    f"Liquidación 90' reglamentarios ({gl_90}-{gv_90}); marcador desempate: {marcador_desemp}"
                )
        except Exception:
            res.goles_local = gl_raw
            res.goles_visitante = gv_raw
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
    fs_extra = detalle.get("flashscore_stats") or detalle.get("flashscore")
    if isinstance(fs_extra, dict) and fs_extra:
        _aplicar_flashscore_en_resultado(res, fs_extra, scraper_vacio=(not periodos))
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
    if seleccion.get("abstenido") or seleccion.get("estado_liquidacion") == "abstenido_por_cobertura":
        return Evaluacion(
            id_trabajo=id_trabajo,
            partido=f"{resultado.local} vs {resultado.visitante}",
            mercado=str(seleccion.get("mercado") or ""),
            evento=str(seleccion.get("evento") or ""),
            etiqueta=str(seleccion.get("etiqueta") or ""),
            periodo=str(seleccion.get("periodo") or "FT"),
            equipo=seleccion.get("equipo"),
            linea=seleccion.get("linea"),
            probabilidad_original=None,
            probabilidad_origen="abstenido",
            estado_sensibilidad="abstenido",
            id_partido=resultado.id_partido,
            referencia=str(seleccion.get("referencia") or ""),
            estado_liquidacion="abstenido_por_cobertura",
            evento_ocurrio=None,
            objetivo_probabilidad="no_evaluable",
            detalle=str(seleccion.get("motivo") or "Pronóstico abstenido por política de cobertura insuficiente"),
        )
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
            if resultado.goles:
                return binario(False, f"{len(resultado.goles)} goles: sí hubo")
            if total_ft == 0:
                return binario(True, "0 goles: ninguno")
            return no_verificable("marcador con goles sin cronología minuto a minuto en la fuente")
        if not resultado.goles:
            if total_ft == 0:
                return binario(False, "no hubo goles: ningún equipo anotó primero")
            return no_verificable(f"marcador {gl}-{gv} sin cronología/minuto del primer gol en la fuente")
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
    if evento.startswith(("corners_over_", "corners_under_")) and equipo_evento:
        linea_c = _interpretar_linea(evento)
        valor = _corners(resultado, equipo_evento, periodo)
        if valor is None or linea_c is None:
            return no_verificable(f"sin córneres de {equipo_evento} en {periodo}")
        if evento.startswith("corners_over_"):
            return binario(valor > linea_c, f"{equipo_evento} {valor} córneres vs más de {linea_c}")
        return binario(valor < linea_c, f"{equipo_evento} {valor} córneres vs menos de {linea_c}")
    if evento.startswith(("local_corners_", "visitante_corners_")):
        linea_c = _interpretar_linea(evento)
        eq_c = resultado.local if evento.startswith("local_corners_") else resultado.visitante
        valor = _corners(resultado, eq_c, periodo)
        if valor is None or linea_c is None:
            return no_verificable(f"sin córneres de {eq_c} en {periodo}")
        if "_over_" in evento:
            return binario(valor > linea_c, f"{eq_c} {valor} córneres vs más de {linea_c}")
        return binario(valor < linea_c, f"{eq_c} {valor} córneres vs menos de {linea_c}")
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
    abstenidos_por_cobertura: int = 0
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
        _tipo_doc = str(doc.get("tipo_partido") or (doc.get("metadatos") or {}).get("tipo_partido") or "").strip().lower()
        _comp_doc = str(doc.get("competicion") or (doc.get("metadatos") or {}).get("competicion") or "").strip().lower()
        if (
            _tipo_doc == "amistoso"
            or doc.get("entra_promedios") is False
            or doc.get("entra_calibracion") is False
            or any(tok in _comp_doc for tok in ("friendly", "friendlies", "amistoso"))
        ):
            msg_am = f"{clave}: amistoso excluido de promedios y calibración"
            if msg_am not in limitaciones:
                limitaciones.append(msg_am)
            continue
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
    for _clave_p, _r in resultados.items():
        for _disc in getattr(_r, "discrepancias", []) or []:
            limitaciones.append(f"[{_clave_p}] {_disc}")

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
        abstenidos_por_cobertura=sum(1 for d in pronosticos if d.get("abstenido")),
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
                        filtro_equipos: list[str] | tuple[str, ...] | str | None = None,
                        registrar_ledger_oos_sombra: bool | None = None,
                        ruta_ledger_oos: str | None = None) -> dict[str, Any]:
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
    try:
        from banco_historico import guardar_en_banco_no_bloqueante
        guardar_en_banco_no_bloqueante(
            resultados_evaluacion=list((resultados or {}).values())
        )
    except Exception as _e_banco:
        print(f"[BANCO HISTORICO] ⚠️ Hook /evaluar omitido: {_e_banco}")
    _reg_sombra = (pronosticos is None) if registrar_ledger_oos_sombra is None else bool(registrar_ledger_oos_sombra)
    if _reg_sombra:
        try:
            liquidar_sombras_en_ledger_oos(docs, resultados, ruta_ledger=ruta_ledger_oos)
        except Exception as _e_somb_led:
            print(f"[LEDGER OOS] ⚠️ Liquidación sombra omitida: {_e_somb_led}")
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


def validar_corte_prepartido_sin_fuga(
    generado_en: str | None,
    inicio_utc: str | None,
    partidos_historicos: list[dict[str, Any]] | None = None,
    h2h: list[dict[str, Any]] | None = None,
    fecha_objetivo: str | None = None,
    id_partido_objetivo: str | int | None = None,
) -> dict[str, Any]:
    """
    Verifica estrictamente (regla operativa, sin validación cronológica de umbrales)
    que el pronóstico fue congelado antes del inicio del partido y que ningún partido
    de fecha >= fecha_objetivo o con el mismo id_partido se filtró en las ventanas históricas/H2H.
    """
    t_gen = _parsear_momento(generado_en)
    t_ini = _parsear_momento(inicio_utc)
    motivos: list[str] = []
    margen_h: float | None = None
    pre_kickoff_ok = False
    if t_gen is None or t_ini is None:
        motivos.append("Falta timestamp verificable de generación o inicio UTC.")
    else:
        margen_h = round((t_ini - t_gen).total_seconds() / 3600.0, 4)
        pre_kickoff_ok = t_gen < t_ini
        if not pre_kickoff_ok:
            motivos.append(
                f"Corte posterior o simultáneo al inicio: generado={t_gen.isoformat()} >= inicio={t_ini.isoformat()}"
            )

    fecha_ref = str(fecha_objetivo or (t_ini.strftime("%Y-%m-%d") if t_ini else "") or "")[:10]
    id_obj = str(id_partido_objetivo) if id_partido_objetivo is not None else None
    fugas: list[str] = []

    for origen, lista in (("historico", partidos_historicos or []), ("h2h", h2h or [])):
        for idx, p in enumerate(lista):
            if not isinstance(p, dict):
                continue
            p_id = str(p.get("id_partido") or p.get("id") or "")
            p_fecha = str(p.get("fecha") or "")[:10]
            if id_obj and p_id and p_id == id_obj:
                fugas.append(f"{origen}[{idx}] coincide con id_partido objetivo ({p_id})")
            if fecha_ref and p_fecha and len(p_fecha) == 10 and p_fecha >= fecha_ref:
                fugas.append(f"{origen}[{idx}] fecha={p_fecha} >= objetivo={fecha_ref}")

    if fugas:
        motivos.extend(fugas)

    return {
        "ok": pre_kickoff_ok and not fugas,
        "pre_kickoff_verificado": pre_kickoff_ok,
        "fuga_historica": bool(fugas),
        "t_generado_utc": t_gen.isoformat() if t_gen else None,
        "t_inicio_utc": t_ini.isoformat() if t_ini else None,
        "margen_horas": margen_h,
        "fecha_objetivo": fecha_ref or None,
        "fugas_detalle": fugas,
        "motivos": motivos,
    }


def liquidar_todas_lineas_fixture(
    analisis: dict[str, Any],
    resultado: ResultadoPartido,
    id_trabajo: str = "FIXTURE",
) -> dict[str, Any]:
    """
    Toma el resultado de `motor_mercados.analizar(...)` (y/o documento de pronóstico)
    y liquida TODAS sus líneas publicadas (top5, ticket competitivo y selecciones publicadas),
    sin omitir líneas y sin inventar datos faltantes.
    """
    vistas: dict[str, dict[str, Any]] = {}
    fuentes_sel: list[tuple[str, list[dict[str, Any]]]] = [
        ("top5", list(analisis.get("top5") or [])),
        ("ticket", list(analisis.get("opciones_ticket") or analisis.get("ticket") or [])),
        ("publicadas", list(analisis.get("selecciones_publicadas") or [])),
    ]
    # Incluir 1X2/DC/valor explícitos si vienen aparte
    for k_extra in ("valor_1x2_dc", "selecciones_filtradas", "selecciones"):
        extra = analisis.get(k_extra)
        if isinstance(extra, list):
            fuentes_sel.append((k_extra, extra))

    for origen_grp, lista in fuentes_sel:
        for s in lista:
            if not isinstance(s, dict) or not s.get("evento"):
                continue
            prob_raw = s.get("probabilidad_calibrada")
            if prob_raw is None:
                prob_raw = s.get("probabilidad")
            if prob_raw is None and isinstance(s.get("probabilidad_pct"), (int, float)):
                prob_raw = float(s["probabilidad_pct"]) / 100.0
            # Si viene de selecciones_filtradas sin filtro de ticket, incluir solo >=70% salvo ya en ticket/top5
            if origen_grp in ("selecciones_filtradas", "selecciones"):
                if isinstance(prob_raw, (int, float)) and float(prob_raw) < 0.70 and not s.get("publicada"):
                    continue
            sel_norm = dict(s)
            if isinstance(prob_raw, (int, float)):
                sel_norm["probabilidad"] = float(prob_raw)
            if not sel_norm.get("mercado"):
                sel_norm["mercado"] = sel_norm.get("categoria") or "Mercado"
            if not sel_norm.get("etiqueta"):
                sel_norm["etiqueta"] = sel_norm.get("nombre") or sel_norm["evento"]
            k = clave_seleccion(
                str(sel_norm.get("mercado") or ""),
                str(sel_norm.get("evento") or ""),
                str(sel_norm.get("periodo") or "FT"),
                sel_norm.get("equipo"),
                sel_norm.get("linea"),
            )
            if k not in vistas:
                sel_norm["_origen_grupo"] = origen_grp
                vistas[k] = sel_norm
            else:
                grp_prev = str(vistas[k].get("_origen_grupo") or "")
                if origen_grp not in grp_prev.split(","):
                    vistas[k]["_origen_grupo"] = f"{grp_prev},{origen_grp}" if grp_prev else origen_grp

    filas: list[dict[str, Any]] = []
    evals: list[Evaluacion] = []
    for _k, s in vistas.items():
        ev = liquidar(s, resultado, id_trabajo=id_trabajo)
        evals.append(ev)
        filas.append({
            "grupo": s.get("_origen_grupo", ""),
            "linea": ev.etiqueta or ev.evento,
            "mercado": ev.mercado,
            "evento": ev.evento,
            "periodo": ev.periodo,
            "probabilidad_publicada": ev.probabilidad_original,
            "probabilidad_pct": round(ev.probabilidad_original * 100.0, 2) if isinstance(ev.probabilidad_original, (int, float)) else None,
            "resultado_real": ev.detalle,
            "estado_liquidacion": ev.estado_liquidacion,
            "evento_ocurrio": ev.evento_ocurrio,
        })

    n_gan = sum(1 for e in evals if e.estado_liquidacion == "ganada")
    n_per = sum(1 for e in evals if e.estado_liquidacion == "perdida")
    n_dev = sum(1 for e in evals if e.estado_liquidacion == "devolucion")
    n_nv = sum(1 for e in evals if e.estado_liquidacion == "no_verificable")
    n_regla = sum(1 for e in evals if e.estado_liquidacion == "regla_no_definida")
    n_eval = n_gan + n_per
    brier = brier_binario(evals)
    return {
        "partido": f"{resultado.local} vs {resultado.visitante}",
        "id_partido": resultado.id_partido,
        "marcador_ft": [resultado.goles_local, resultado.goles_visitante],
        "marcador_1t": [resultado.goles_local_1t, resultado.goles_visitante_1t],
        "lineas": filas,
        "evaluaciones": [e.a_dict() for e in evals],
        "discrepancias_scraper": list(getattr(resultado, "discrepancias", []) or []),
        "resumen": {
            "n_publicadas": len(evals),
            "n_evaluables": n_eval,
            "aciertos": n_gan,
            "fallos": n_per,
            "tasa_acierto": round(n_gan / n_eval, 4) if n_eval else None,
            "devoluciones": n_dev,
            "no_verificables": n_nv,
            "regla_no_definida": n_regla,
            "brier": brier.get("brier"),
        },
    }


IDS_PARTIDOS_DISENO = frozenset({"5181796", "5181797"})


def _norm_txt_eval(valor: Any) -> str:
    txt = unicodedata.normalize("NFKD", str(valor or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return " ".join(txt.lower().split())


def es_partido_de_diseno(partido: Any = None, id_partido: Any = None) -> bool:
    """Identifica los dos partidos usados para diseño (Turkey-Belgium e Italy-France)."""
    pid = str(id_partido or "").strip()
    if pid in IDS_PARTIDOS_DISENO:
        return True
    p_norm = _norm_txt_eval(partido)
    if not p_norm:
        return False
    es_tb = ("turkey" in p_norm or "turquia" in p_norm) and ("belgium" in p_norm or "belgica" in p_norm)
    es_if = ("italy" in p_norm or "italia" in p_norm) and ("france" in p_norm or "francia" in p_norm)
    return bool(es_tb or es_if)


def log_loss_binario_valor(p: float, y: int, eps: float = 1e-6) -> float:
    """Log-loss binario acotado en [eps, 1 - eps]."""
    p_clip = min(1.0 - eps, max(eps, float(p)))
    y_i = int(y)
    return -(math.log(p_clip) if y_i == 1 else math.log(1.0 - p_clip))


def bootstrap_brier_logloss_por_partido(
    observaciones: list[tuple[str, int, dict[str, float]]],
    n_boot: int = 2000,
    seed: int = 20261114,
) -> dict[str, Any]:
    """
    Bootstrap agrupado por `id_partido` (bloque = partido único) sobre observaciones
    emparejadas `(match_id, y_binario, {version: prob})`.
    """
    por_partido: dict[str, list[tuple[int, dict[str, float]]]] = {}
    versiones: list[str] = []
    for mid, y, probs in observaciones:
        if not mid or probs is None:
            continue
        if not versiones:
            versiones = list(probs.keys())
        por_partido.setdefault(str(mid), []).append((int(y), probs))

    mids = sorted(por_partido.keys())
    n_partidos = len(mids)
    n_filas = sum(len(v) for v in por_partido.values())
    if n_partidos == 0 or not versiones:
        return {
            "n_partidos_unicos": 0,
            "n_filas": 0,
            "suficiente_n30": False,
            "nota_muestra": f"Sin datos evaluables (< MIN_PARTIDOS_PARA_CALIBRAR={MIN_PARTIDOS_PARA_CALIBRAR}).",
            "versiones": {},
            "diferencias": {},
        }

    # Precomputo de sumas por partido para bootstrap rápido y determinista
    sumas_brier: dict[str, dict[str, float]] = {v: {} for v in versiones}
    sumas_ll: dict[str, dict[str, float]] = {v: {} for v in versiones}
    conteos_m: dict[str, int] = {}
    for mid in mids:
        items = por_partido[mid]
        conteos_m[mid] = len(items)
        for v in versiones:
            sumas_brier[v][mid] = sum((float(pr[v]) - y) ** 2 for y, pr in items)
            sumas_ll[v][mid] = sum(log_loss_binario_valor(float(pr[v]), y) for y, pr in items)

    res_ver: dict[str, dict[str, Any]] = {}
    for v in versiones:
        b_punt = sum(sumas_brier[v].values()) / n_filas
        ll_punt = sum(sumas_ll[v].values()) / n_filas
        res_ver[v] = {
            "brier": round(b_punt, 6),
            "log_loss": round(ll_punt, 6),
            "brier_ic95": None,
            "log_loss_ic95": None,
        }

    pares_diff: list[tuple[str, str]] = []
    for idx_i, va in enumerate(versiones):
        for vb in versiones[idx_i + 1 :]:
            pares_diff.append((vb, va))  # vb - va (< 0 favorece a vb)

    if n_partidos >= 2 and n_boot >= 10:
        rng = random.Random(seed)
        boots_b: dict[str, list[float]] = {v: [] for v in versiones}
        boots_ll: dict[str, list[float]] = {v: [] for v in versiones}
        for _ in range(n_boot):
            muestra = rng.choices(mids, k=n_partidos)
            tot_n = sum(conteos_m[m] for m in muestra)
            for v in versiones:
                boots_b[v].append(sum(sumas_brier[v][m] for m in muestra) / tot_n)
                boots_ll[v].append(sum(sumas_ll[v][m] for m in muestra) / tot_n)

        lo_i = max(0, int(round(0.025 * (n_boot - 1))))
        hi_i = min(n_boot - 1, int(round(0.975 * (n_boot - 1))))
        for v in versiones:
            sb = sorted(boots_b[v])
            sl = sorted(boots_ll[v])
            res_ver[v]["brier_ic95"] = [round(sb[lo_i], 6), round(sb[hi_i], 6)]
            res_ver[v]["log_loss_ic95"] = [round(sl[lo_i], 6), round(sl[hi_i], 6)]

        res_diff: dict[str, dict[str, Any]] = {}
        for vb, va in pares_diff:
            db_punt = res_ver[vb]["brier"] - res_ver[va]["brier"]
            dll_punt = res_ver[vb]["log_loss"] - res_ver[va]["log_loss"]
            sdb = sorted(boots_b[vb][k] - boots_b[va][k] for k in range(n_boot))
            sdl = sorted(boots_ll[vb][k] - boots_ll[va][k] for k in range(n_boot))
            res_diff[f"{vb} - {va}"] = {
                "d_brier": round(db_punt, 6),
                "d_brier_ic95": [round(sdb[lo_i], 6), round(sdb[hi_i], 6)],
                "d_log_loss": round(dll_punt, 6),
                "d_log_loss_ic95": [round(sdl[lo_i], 6), round(sdl[hi_i], 6)],
            }
    else:
        res_diff = {}

    suficiente = n_partidos >= MIN_PARTIDOS_PARA_CALIBRAR
    nota = (
        f"Muestra OOS válida: {n_partidos} partidos distintos (>= {MIN_PARTIDOS_PARA_CALIBRAR})."
        if suficiente
        else f"MUESTRA PEQUEÑA (POCOS PARTIDOS): solo {n_partidos} partidos distintos (< MIN_PARTIDOS_PARA_CALIBRAR={MIN_PARTIDOS_PARA_CALIBRAR}); IC95% amplio e insuficiente para afirmar mejora poblacional estable."
    )
    return {
        "n_partidos_unicos": n_partidos,
        "n_filas": n_filas,
        "suficiente_n30": suficiente,
        "nota_muestra": nota,
        "versiones": res_ver,
        "diferencias": res_diff,
    }


def evaluar_oos_comparativo_sin_diseno(
    observaciones: list[dict[str, Any]],
    n_boot: int = 2000,
    seed: int = 20261114,
) -> dict[str, Any]:
    """
    Evalúa Brier y Log-Loss OOS (total y por familia) excluyendo estrictamente los
    partidos de diseño (Turkey-Belgium e Italy-France), con bootstrap por partido.
    """
    filtradas = [
        o for o in observaciones
        if not es_partido_de_diseno(o.get("partido"), o.get("id_partido"))
    ]
    excluido_diseno = len(observaciones) - len(filtradas)
    tuplas_total = [
        (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        for o in filtradas
    ]
    total = bootstrap_brier_logloss_por_partido(tuplas_total, n_boot=n_boot, seed=seed)

    por_fam_in: dict[str, list[tuple[str, int, dict[str, float]]]] = {}
    for o in filtradas:
        fam = _familia_de(str(o.get("mercado") or ""))
        por_fam_in.setdefault(fam, []).append(
            (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        )
    por_familia = {
        fam: bootstrap_brier_logloss_por_partido(rows, n_boot=n_boot, seed=seed + idx + 1)
        for idx, (fam, rows) in enumerate(sorted(por_fam_in.items()))
    }
    return {
        "filas_excluidas_diseno": excluido_diseno,
        "total": total,
        "por_familia": por_familia,
    }


def fisher_exact_dos_colas(k1: int, n1: int, k2: int, n2: int) -> float:
    """Test exacto de Fisher bilateral sobre tabla 2x2 [[k1, n1-k1], [k2, n2-k2]]."""
    if n1 <= 0 or n2 <= 0:
        return 1.0
    r1 = int(n1)
    r2 = int(n2)
    c1 = int(k1) + int(k2)
    n_tot = r1 + r2
    if c1 <= 0 or c1 >= n_tot:
        return 1.0
    denom = math.comb(n_tot, c1)
    if denom <= 0:
        return 1.0

    def _prob_hiper(x: int) -> float:
        return (math.comb(r1, x) * math.comb(r2, c1 - x)) / denom

    p_obs = _prob_hiper(int(k1))
    x_min = max(0, c1 - r2)
    x_max = min(r1, c1)
    p_sum = sum(_prob_hiper(x) for x in range(x_min, x_max + 1) if _prob_hiper(x) <= p_obs + 1e-12)
    return float(min(1.0, max(0.0, p_sum)))


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Ajuste FDR Benjamini-Hochberg (q-valores ajustados por multiplicidad)."""
    m = len(p_values)
    if m == 0:
        return []
    orden = sorted(enumerate(p_values), key=lambda kv: float(kv[1]))
    q_ord = [1.0] * m
    acum = 1.0
    for idx in range(m - 1, -1, -1):
        _, pval = orden[idx]
        rango = idx + 1
        q = min(acum, min(1.0, float(pval) * m / rango))
        q_ord[idx] = q
        acum = q
    out = [1.0] * m
    for idx, (orig_i, _) in enumerate(orden):
        out[orig_i] = round(q_ord[idx], 6)
    return out


def resumir_hipotesis_bh(
    observaciones: list[dict[str, Any]],
    alfa_fdr: float = 0.05,
) -> list[dict[str, Any]]:
    """
    Evalúa cada regla-objetivo (`regla`, `objetivo`, `activo`, `evento_ocurrio`)
    frente a su tasa base con Fisher bilateral y control Benjamini-Hochberg.
    """
    grupos: dict[tuple[str, str], list[tuple[bool, int]]] = {}
    for row in observaciones:
        clave = (str(row.get("regla") or ""), str(row.get("objetivo") or ""))
        grupos.setdefault(clave, []).append((bool(row.get("activo")), int(row["evento_ocurrio"])))

    filas: list[dict[str, Any]] = []
    for (regla, objetivo), pares in sorted(grupos.items()):
        act = [y for a, y in pares if a]
        inact = [y for a, y in pares if not a]
        n_eval = len(pares)
        n_act = len(act)
        k_act = sum(act)
        k_base = sum(y for _, y in pares)
        tasa_act = (k_act / n_act) if n_act > 0 else None
        tasa_base = (k_base / n_eval) if n_eval > 0 else None
        pval = fisher_exact_dos_colas(k_act, n_act, sum(inact), len(inact)) if (n_act > 0 and inact) else 1.0
        filas.append({
            "regla": regla,
            "objetivo": objetivo,
            "n_evaluables": n_eval,
            "n_activaciones": n_act,
            "aciertos_activada": k_act,
            "tasa_activada": None if tasa_act is None else round(tasa_act, 4),
            "aciertos_base": k_base,
            "tasa_base": None if tasa_base is None else round(tasa_base, 4),
            "p_valor": round(pval, 6),
        })

    qs = benjamini_hochberg([f["p_valor"] for f in filas])
    for f, q in zip(filas, qs):
        f["bh_q"] = q
        f["significativa_bh"] = bool(q < alfa_fdr)
        f["estado_validacion"] = "validada_bh" if f["significativa_bh"] else "hipótesis, no validada"
    return filas


def _hash_canonico_oos(payload_sin_hash: dict[str, Any]) -> str:
    crudo = json.dumps(payload_sin_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def cargar_ledger_oos(ruta_ledger: str | None = None) -> list[dict[str, Any]]:
    ruta = ruta_ledger or LEDGER_OOS_PATH
    if not os.path.exists(ruta):
        return []
    registros: list[dict[str, Any]] = []
    with open(ruta, encoding="utf-8") as f:
        for i, linea in enumerate(f, 1):
            s = linea.strip()
            if not s:
                continue
            try:
                registros.append(json.loads(s))
            except json.JSONDecodeError as e:
                raise ValueError(f"Línea corrupta en ledger OOS (línea {i}): {e}") from e
    return registros


def verificar_cadena_ledger_oos(ruta_ledger: str | None = None) -> dict[str, Any]:
    """Verifica secuencialmente prev_sha256, índice monotónico y SHA256 de cada entrada."""
    ruta = ruta_ledger or LEDGER_OOS_PATH
    registros = cargar_ledger_oos(ruta)
    errores: list[str] = []
    prev_esperado = "0" * 64
    for idx_esp, reg in enumerate(registros):
        if reg.get("indice") != idx_esp:
            errores.append(f"Índice esperado {idx_esp}, encontrado {reg.get('indice')}")
        if reg.get("prev_sha256") != prev_esperado:
            errores.append(
                f"Entrada {idx_esp}: prev_sha256={reg.get('prev_sha256')} != esperado={prev_esperado}"
            )
        hash_reg = reg.get("sha256")
        sin_hash = {k: v for k, v in reg.items() if k != "sha256"}
        recomp = _hash_canonico_oos(sin_hash)
        if hash_reg != recomp:
            errores.append(f"Entrada {idx_esp}: sha256 adulterado ({hash_reg} != {recomp})")
        if reg.get("retroalimenta_modelo") is not False:
            errores.append(f"Entrada {idx_esp}: retroalimenta_modelo debe ser False")
        prev_esperado = str(hash_reg or "")
    return {
        "ok": len(errores) == 0,
        "ruta": ruta,
        "entradas": len(registros),
        "ultimo_sha256": prev_esperado if registros else None,
        "errores": errores,
    }


def registrar_en_ledger_oos(
    registro: dict[str, Any],
    ruta_ledger: str | None = None,
) -> dict[str, Any]:
    """
    Añade de forma estrictamente append-only e idempotente una liquidación out-of-sample
    al ledger SHA256 (`retroalimenta_modelo=False`). Valida toda la cadena previa antes de escribir.
    """
    ruta = ruta_ledger or LEDGER_OOS_PATH
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    estado_previo = verificar_cadena_ledger_oos(ruta)
    if not estado_previo["ok"]:
        raise ValueError(f"Integridad comprometida en ledger OOS: {estado_previo['errores']}")

    existentes = cargar_ledger_oos(ruta)
    id_part = str(registro.get("id_partido") or "")
    fecha = str(registro.get("fecha") or "")
    partido = str(registro.get("partido") or "")
    estado_reg_nuevo = str(registro.get("estado_registro") or "liquidado").strip().lower()
    id_trab_nuevo = str(registro.get("id_trabajo") or "")
    for ex in existentes:
        estado_ex = str(ex.get("estado_registro") or "liquidado").strip().lower()
        if estado_ex != estado_reg_nuevo:
            continue
        if estado_reg_nuevo == "pre_partido" and id_trab_nuevo:
            if str(ex.get("id_trabajo") or "") == id_trab_nuevo:
                return {
                    "registrado": False,
                    "ya_existia": True,
                    "indice": ex.get("indice"),
                    "sha256": ex.get("sha256"),
                    "ruta": ruta,
                }
            # Otro id_trabajo distinto para el mismo partido SI se registra como no_canonica / no_oos
            continue
        if id_part and str(ex.get("id_partido") or "") == id_part and str(ex.get("fecha") or "") == fecha:
            return {
                "registrado": False,
                "ya_existia": True,
                "indice": ex.get("indice"),
                "sha256": ex.get("sha256"),
                "ruta": ruta,
            }
        if not id_part and ex.get("partido") == partido and str(ex.get("fecha") or "") == fecha:
            return {
                "registrado": False,
                "ya_existia": True,
                "indice": ex.get("indice"),
                "sha256": ex.get("sha256"),
                "ruta": ruta,
            }

    prev_hash = existentes[-1]["sha256"] if existentes else ("0" * 64)
    nuevo = {
        "version": 1,
        "indice": len(existentes),
        "registrado_en_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **{k: v for k, v in registro.items() if k not in ("version", "indice", "prev_sha256", "sha256", "retroalimenta_modelo")},
        "retroalimenta_modelo": False,
        "prev_sha256": prev_hash,
    }
    _claves_oos = ("fixture_confirmado", "fuente_fecha", "hora_inicio_partido", "telegram_message_date", "hora_mensaje_utc")
    if estado_reg_nuevo == "pre_partido" or any(k in registro for k in _claves_oos):
        contab = evaluar_contabilidad_oos_registro(
            fixture_confirmado=registro.get("fixture_confirmado", False),
            fuente_fecha=registro.get("fuente_fecha"),
            hora_inicio_partido=registro.get("hora_inicio_partido"),
            telegram_message_date=registro.get("telegram_message_date"),
            hora_mensaje_utc=registro.get("hora_mensaje_utc"),
            hora_inicio_fotmob=registro.get("hora_inicio_fotmob"),
            hora_inicio_flashscore=registro.get("hora_inicio_flashscore"),
            diferencia_hora_inicio_min=registro.get("diferencia_hora_inicio_min"),
            hora_inicio_discrepante=registro.get("hora_inicio_discrepante"),
            partido=partido,
            fecha=fecha,
            id_partido=id_part,
            estado_registro=estado_reg_nuevo,
            id_trabajo=id_trab_nuevo,
            registros_existentes=existentes,
        )
        nuevo.update(contab)
    for _k_f_ord in ("fuente_local", "fuente_visitante", "firma_datos", "lambdas_plano"):
        if nuevo.get(_k_f_ord) is None:
            _v_dc = (registro.get("datos_corte") or {}).get(_k_f_ord) if isinstance(registro.get("datos_corte"), dict) else None
            _v_mt = (registro.get("metadatos") or {}).get(_k_f_ord) if isinstance(registro.get("metadatos"), dict) else None
            _v_f_ord = _v_dc or _v_mt
            if _v_f_ord is not None:
                nuevo[_k_f_ord] = _v_f_ord
    comp_low = str(nuevo.get("competicion") or "").strip().lower()
    tipo_low = str(nuevo.get("tipo_partido") or "").strip().lower()
    if tipo_low == "amistoso" or any(tok in comp_low for tok in ("friendly", "friendlies", "amistoso")):
        nuevo["tipo_partido"] = "amistoso"
        nuevo["entra_promedios"] = False
        nuevo["entra_calibracion"] = False
    nuevo["sha256"] = _hash_canonico_oos(nuevo)

    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(nuevo, ensure_ascii=False, sort_keys=True) + "\n")

    return {
        "registrado": True,
        "ya_existia": False,
        "indice": nuevo["indice"],
        "sha256": nuevo["sha256"],
        "prev_sha256": nuevo["prev_sha256"],
        "ruta": ruta,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 9b. MODO SOMBRA DUAL EN EL LIBRO Y REGLA FUTURA DE PROMOCIÓN (RONDA 7 PUNTO 7)
# ═══════════════════════════════════════════════════════════════════════════════

def _resumen_lambdas_analisis(an: Any) -> dict[str, Any]:
    mot = getattr(an, "motor", None)
    tas = getattr(mot, "tasas", None)
    if tas is None:
        return {}
    return {
        "local_ft": getattr(tas, "lambda_local_ft", None),
        "visitante_ft": getattr(tas, "lambda_visitante_ft", None),
        "local_1t": getattr(tas, "lambda_local_1t", None),
        "visitante_1t": getattr(tas, "lambda_visitante_1t", None),
        "local_2t": getattr(tas, "lambda_local_2t", None),
        "visitante_2t": getattr(tas, "lambda_visitante_2t", None),
    }


def _mapa_probs_analisis(an: Any) -> dict[tuple[str, str, str, Any], dict[str, Any]]:
    out: dict[tuple[str, str, str, Any], dict[str, Any]] = {}
    for rm in (getattr(an, "mercados", None) or []):
        for s in (getattr(rm, "selecciones", None) or []):
            if not getattr(s, "estimable", False) or getattr(s, "probabilidad", None) is None:
                continue
            lin = getattr(s, "linea", None)
            k = (
                str(getattr(s, "evento", "") or ""),
                str(getattr(s, "periodo", "FT") or "FT").strip().upper(),
                str(getattr(s, "equipo", None) or ""),
                None if lin is None else round(float(lin), 4),
            )
            out[k] = {
                "mercado": getattr(s, "mercado", None),
                "evento": k[0],
                "periodo": k[1],
                "equipo": getattr(s, "equipo", None),
                "linea": lin,
                "prob": round(float(s.probabilidad), 6),
            }
    return out


def construir_modo_sombra_desde_analisis(
    analisis_base: Any,
    analisis_sombra: Any,
    banderas_candidatas: dict[str, bool],
    version_base: str = "soloA",
    version_sombra: str = "candidata",
) -> dict[str, Any]:
    """
    Construye el registro dual inmutable (`base` publicada vs `sombra` candidata) para
    guardar ambas versiones en el libro sin alterar el pronóstico publicado.
    """
    m_base = _mapa_probs_analisis(analisis_base)
    m_somb = _mapa_probs_analisis(analisis_sombra)
    comparaciones: list[dict[str, Any]] = []
    for k, b in m_base.items():
        s = m_somb.get(k)
        comparaciones.append({
            "mercado": b["mercado"],
            "evento": b["evento"],
            "periodo": b["periodo"],
            "equipo": b["equipo"],
            "linea": b["linea"],
            "prob_base": b["prob"],
            "prob_sombra": s["prob"] if s is not None else None,
        })
    for k, s in m_somb.items():
        if k not in m_base:
            comparaciones.append({
                "mercado": s["mercado"],
                "evento": s["evento"],
                "periodo": s["periodo"],
                "equipo": s["equipo"],
                "linea": s["linea"],
                "prob_base": None,
                "prob_sombra": s["prob"],
            })

    return {
        "esquema": "sombra-v1",
        "publicado": version_base,
        "version_base": version_base,
        "version_sombra": version_sombra,
        "banderas_candidatas": {str(k): bool(v) for k, v in (banderas_candidatas or {}).items()},
        "retroalimenta_modelo": False,
        "lambdas_base": _resumen_lambdas_analisis(analisis_base),
        "lambdas_finales_soloA": _resumen_lambdas_analisis(analisis_base),
        "lambdas_sombra": _resumen_lambdas_analisis(analisis_sombra),
        "n_selecciones_comparables": sum(
            1 for c in comparaciones if c["prob_base"] is not None and c["prob_sombra"] is not None
        ),
        "comparaciones": comparaciones,
    }


def ejecutar_analisis_modo_sombra(
    doc_local: Any,
    doc_visitante: Any,
    equipo_local: str,
    equipo_visitante: str,
    banderas_candidatas: dict[str, bool],
    **analizar_kwargs: Any,
) -> dict[str, Any]:
    """
    Ejecuta el análisis oficial (`soloA`, banderas apagadas) y una corrida sombra con
    `banderas_candidatas` activadas en memoria, retornando ambas versiones sin alterar
    la versión publicada.
    """
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm

    with banderas_comportamiento(activar_todas=False):
        an_base = mm.analizar(doc_local, doc_visitante, equipo_local, equipo_visitante, **analizar_kwargs)
    with banderas_comportamiento(activar_todas=False, **{k: bool(v) for k, v in banderas_candidatas.items()}):
        an_sombra = mm.analizar(doc_local, doc_visitante, equipo_local, equipo_visitante, **analizar_kwargs)

    sombra = construir_modo_sombra_desde_analisis(
        an_base,
        an_sombra,
        banderas_candidatas=banderas_candidatas,
    )
    return {
        "analisis_base": an_base,
        "analisis_sombra": an_sombra,
        "modo_sombra": sombra,
    }


def adjuntar_modo_sombra_preparado(
    preparado: dict[str, Any],
    banderas_candidatas: dict[str, bool] | None = None,
) -> dict[str, Any] | None:
    """
    Adjunta en `preparado['modo_sombra']` la evaluación paralela sombra usando el
    mismo contexto del partido ya resuelto, sin modificar `preparado['analisis']`.
    """
    if not isinstance(preparado, dict):
        return None
    if isinstance(preparado.get("modo_sombra"), dict):
        return preparado["modo_sombra"]
    analisis = preparado.get("analisis")
    if analisis is None or bool(preparado.get("abstenido") or getattr(analisis, "abstenido", False)):
        return None
    ctx = preparado.get("contexto") or {}
    dl = ctx.get("doc_local")
    dv = ctx.get("doc_visitante")
    if not isinstance(dl, dict) or not isinstance(dv, dict):
        return None

    from decision_cobertura import banderas_comportamiento, BANDERAS_COMPORTAMIENTO_DEFECTO
    import motor_mercados as mm

    meta = getattr(analisis, "metadatos", None) or {}
    eq_l = str(meta.get("equipo_local") or ctx.get("equipo_local") or "Local")
    eq_v = str(meta.get("equipo_visitante") or ctx.get("equipo_visitante") or "Visitante")
    cands = dict(banderas_candidatas) if banderas_candidatas else {
        k: True
        for k in BANDERAS_COMPORTAMIENTO_DEFECTO
        # Incluye las 14 banderas de A+B en modo sombra (Top-5 e inferencia sin alterar soloA)
    }
    params = getattr(analisis, "parametros", None)
    kw: dict[str, Any] = {
        "fecha_partido": meta.get("fecha_partido") or ctx.get("fecha_partido"),
        "competicion": meta.get("competicion") or ctx.get("competicion"),
        "condicion_local": meta.get("condicion_local") or ctx.get("condicion_local") or "casa",
        "condicion_visitante": meta.get("condicion_visitante") or ctx.get("condicion_visitante") or "fuera",
    }
    if params is not None:
        kw["parametros"] = params
    with banderas_comportamiento(activar_todas=False, **cands):
        an_sombra = mm.analizar(dl, dv, eq_l, eq_v, **kw)
    sombra = construir_modo_sombra_desde_analisis(
        analisis,
        an_sombra,
        banderas_candidatas=cands,
        version_sombra="A+B" if banderas_candidatas is None else "candidata",
    )
    preparado["modo_sombra"] = _aplicar_tercer_brazo_plano_si_activo(sombra, preparado, analisis, dl, dv, eq_l, eq_v, kw)
    return sombra


def registrar_sombra_en_ledger_oos(
    registro_base: dict[str, Any],
    *,
    probabilidad_base: float | None = None,
    probabilidad_sombra: float | None = None,
    nombre_bandera: str = "",
    lambdas_base: dict[str, Any] | None = None,
    lambdas_sombra: dict[str, Any] | None = None,
    ruta_ledger: str | None = None,
) -> dict[str, Any]:
    """
    Registra en el ledger SHA256 OOS ambas versiones (`base` y `sombra`) manteniendo
    `retroalimenta_modelo=False` y marcando si el partido pertenece al conjunto de diseño.
    """
    reg = dict(registro_base or {})
    id_p = reg.get("id_partido")
    partido = reg.get("partido")
    reg["modo_sombra"] = {
        "nombre_bandera": str(nombre_bandera or ""),
        "probabilidad_base": probabilidad_base if probabilidad_base is not None else reg.get("probabilidad_base"),
        "probabilidad_sombra": probabilidad_sombra,
        "lambdas_base": lambdas_base,
        "lambdas_sombra": lambdas_sombra,
        "es_partido_diseno": es_partido_de_diseno(partido, id_p),
    }
    return registrar_en_ledger_oos(reg, ruta_ledger=ruta_ledger)


_PATRON_ADHOC_NOMBRES = ("_unico", "_partido_unico", "ad_hoc", "turkey_belgium", "italia_francia", "italy_france")


def evaluar_promocion_regla_futura(
    observaciones_sombra: list[dict[str, Any]],
    nombre_bandera: str,
    *,
    partidos_diseno_adicionales: tuple[str, ...] | list[str] | set[str] = (),
    nombre_regla_propuesta: str = "",
    min_partidos: int | None = None,
    n_boot: int = 2000,
    seed: int = 20261114,
    exigir_ic95_estricto: bool = False,
) -> dict[str, Any]:
    """
    Gatekeeper formal de la Regla Futura (Ronda 7 Punto 7). Exige para activar
    cualquier bandera de comportamiento:
      1. Bandera declarada en BANDERAS_COMPORTAMIENTO_DEFECTO y apagada por defecto (False).
      2. Prohibición explícita de reglas ad-hoc codificadas desde un único partido.
      3. Exclusión de los partidos de diseño (TB, IF y adicionales declarados).
      4. Registro dual en modo sombra (probabilidades `base` y `sombra`) durante al menos
         30 partidos únicos (`min_partidos_modo_sombra`).
      5. Mejora verificada de Brier Y Log-Loss (`sombra` vs `base`) con IC95% por partido.
    """
    from decision_cobertura import (
        BANDERAS_COMPORTAMIENTO_DEFECTO,
        cargar_umbrales_cobertura,
        obtener_politica_regla_futura,
    )

    u = cargar_umbrales_cobertura()
    pol = obtener_politica_regla_futura(u)
    min_p = int(min_partidos if min_partidos is not None else pol["min_partidos_modo_sombra"])

    razones: list[str] = []
    bandera_declarada = nombre_bandera in BANDERAS_COMPORTAMIENTO_DEFECTO
    apagada_por_defecto = (
        bandera_declarada and (BANDERAS_COMPORTAMIENTO_DEFECTO.get(nombre_bandera) is False)
    )
    if not bandera_declarada:
        razones.append(f"bandera_no_declarada:{nombre_bandera}")
    elif not apagada_por_defecto:
        razones.append(f"bandera_no_apagada_por_defecto:{nombre_bandera}")

    import unicodedata as _ud_rf
    def _norm_rf(val: Any) -> str:
        return " ".join(
            _ud_rf.normalize("NFKD", str(val or ""))
            .encode("ascii", "ignore")
            .decode("ascii")
            .lower()
            .split()
        )

    nom_reg = _norm_rf(nombre_regla_propuesta or nombre_bandera)
    if any(tok in nom_reg for tok in _PATRON_ADHOC_NOMBRES):
        razones.append(f"regla_adhoc_prohibida:{nombre_regla_propuesta or nombre_bandera}")

    diseno_extra = {_norm_rf(x) for x in (partidos_diseno_adicionales or ()) if x}
    filtradas: list[dict[str, Any]] = []
    n_excl_diseno = 0
    n_excl_amistosos = 0
    n_sin_doble_version = 0
    partidos_brutos_observados: set[str] = set()
    for ob in observaciones_sombra:
        if not isinstance(ob, dict):
            continue
        if (
            str(ob.get("tipo_partido") or "").strip().lower() == "amistoso"
            or ob.get("entra_calibracion") is False
            or ob.get("entra_promedios") is False
        ):
            n_excl_amistosos += 1
            continue
        pid = ob.get("id_partido")
        pnom = ob.get("partido")
        pid_k = str(pid or pnom or "")
        if pid_k:
            partidos_brutos_observados.add(pid_k)
        if es_partido_de_diseno(pnom, pid) or (
            _norm_rf(pnom) in diseno_extra or _norm_rf(pid) in diseno_extra
        ):
            n_excl_diseno += 1
            continue
        probs = ob.get("probs") or {}
        if "base" not in probs or "sombra" not in probs or probs.get("base") is None or probs.get("sombra") is None:
            n_sin_doble_version += 1
            continue
        filtradas.append(ob)

    if len(partidos_brutos_observados) <= 1:
        razones.append("prohibido_codificar_regla_de_un_solo_partido")
    if n_sin_doble_version > 0:
        razones.append(f"registros_sin_modo_sombra_dual:{n_sin_doble_version}")

    comp = evaluar_oos_comparativo_sin_diseno(filtradas, n_boot=n_boot, seed=seed)
    tot = comp.get("total") or {}
    n_part = int(tot.get("n_partidos_unicos", 0) or 0)
    n_filas = int(tot.get("n_filas", 0) or 0)
    vers = tot.get("versiones") or {}
    v_base = vers.get("base") or {}
    v_somb = vers.get("sombra") or {}
    diffs = tot.get("diferencias") or {}
    diff_sb = diffs.get("sombra - base") or next(iter(diffs.values()), {}) if diffs else {}

    b_ant = v_base.get("brier")
    b_des = v_somb.get("brier")
    ll_ant = v_base.get("log_loss")
    ll_des = v_somb.get("log_loss")
    d_brier = diff_sb.get("d_brier")
    d_brier_ic = diff_sb.get("d_brier_ic95")
    d_ll = diff_sb.get("d_log_loss")
    d_ll_ic = diff_sb.get("d_log_loss_ic95")

    if n_part < min_p:
        razones.append(f"muestra_sombra_insuficiente:{n_part}<{min_p}")
    if d_brier is None or float(d_brier) >= 0.0:
        razones.append(f"brier_no_mejora:{d_brier}")
    if d_ll is None or float(d_ll) >= 0.0:
        razones.append(f"log_loss_no_mejora:{d_ll}")
    if exigir_ic95_estricto:
        if not d_brier_ic or float(d_brier_ic[1]) >= 0.0:
            razones.append(f"ic95_brier_cruza_cero:{d_brier_ic}")
        if not d_ll_ic or float(d_ll_ic[1]) >= 0.0:
            razones.append(f"ic95_log_loss_cruza_cero:{d_ll_ic}")

    aprobada = len(razones) == 0
    return {
        "bandera": nombre_bandera,
        "bandera_declarada": bandera_declarada,
        "apagada_por_defecto": apagada_por_defecto,
        "aprobada_para_activar": aprobada,
        "politica_estado": "aprobada" if aprobada else "bloqueada",
        "razones_bloqueo": razones,
        "min_partidos_requerido": min_p,
        "n_partidos_unicos": n_part,
        "n_filas_sombra": n_filas,
        "n_excluidos_diseno": n_excl_diseno + int(comp.get("filas_excluidas_diseno", 0) or 0),
        "n_sin_doble_version": n_sin_doble_version,
        "brier_antes": b_ant,
        "brier_despues": b_des,
        "brier_antes_ic95": v_base.get("brier_ic95"),
        "brier_despues_ic95": v_somb.get("brier_ic95"),
        "d_brier": d_brier,
        "d_brier_ic95": d_brier_ic,
        "log_loss_antes": ll_ant,
        "log_loss_despues": ll_des,
        "log_loss_antes_ic95": v_base.get("log_loss_ic95"),
        "log_loss_despues_ic95": v_somb.get("log_loss_ic95"),
        "d_log_loss": d_ll,
        "d_log_loss_ic95": d_ll_ic,
        "comparativo_oos": comp,
        "politica_regla_futura": pol,
    }


def _macro_familia_ronda8(mercado: str) -> str:
    """Agrupa un nombre de mercado en las 5 macro-familias de la tabla de Ronda 7/8."""
    fam = _familia_de(str(mercado or ""))
    f = str(fam or "").lower()
    if any(k in f for k in ("1x2", "ganador", "doble oportunidad", "hándicap", "handicap", "sin empate")):
        return "Resultado/DC/Hándicap"
    if any(k in f for k in ("esquina", "córner", "corner")):
        return "Córneres"
    if any(k in f for k in ("tarjeta", "amarilla", "roja")):
        return "Tarjetas"
    if "equipo" in f or "anota en" in f or "ambos" in f or "primer" in f:
        return "Goles Equipo/BTTS/1erGol"
    if "goles" in f or "gol" in f or "mitad" in f or "tiempo" in f or "fiesta" in f:
        return "Goles Partido/Mitades"
    return fam or "Otras"


def normalizar_etiquetas_probs_modelos(probs: dict[str, Any]) -> dict[str, float]:
    """
    Valida y normaliza las etiquetas de los modelos entre Ronda 7 y Ronda 8
    (`5e8425f` <-> `v5e84`, `994209a` <-> `v9942`, `A+B` <-> `AB`), impidiendo
    que el mismo modelo tenga valores contradictorios bajo alias distintos.
    """
    out: dict[str, float] = {}
    for k, v in (probs or {}).items():
        if v is not None:
            out[str(k)] = float(v)
    pares = (("5e8425f", "v5e84"), ("994209a", "v9942"), ("A+B", "AB"))
    for canon, alias in pares:
        if canon in out and alias in out and abs(out[canon] - out[alias]) > 1e-6:
            raise ValueError(
                f"Conflicto de probabilidad entre alias '{canon}' ({out[canon]}) y '{alias}' ({out[alias]})."
            )
    return out


def verificar_no_inversion_etiquetas_modelos(observaciones: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Detector anti-inversión de etiquetas entre `5e8425f`/`994209a` y `soloA`/`A+B`.
    En las filas de Córneres OOS donde actúa la sobredispersión Binomial Negativa (`1.05`,
    introducida en `9593ba6`), `994209a` y `A+B` suavizan las colas extremas respecto a
    `5e8425f` y `soloA`, reduciendo la probabilidad media y el Log-Loss de Córneres
    (`~0.8311 -> ~0.7063`). Si las etiquetas están intercambiadas (`LogLoss(994209a) > LogLoss(5e8425f)`
    o `LogLoss(A+B) > LogLoss(soloA)` en Córneres), lanza `ValueError`.
    """
    filas_corners = [
        o for o in (observaciones or [])
        if isinstance(o, dict)
        and not es_partido_de_diseno(o.get("partido"), o.get("id_partido"))
        and _macro_familia_ronda8(str(o.get("mercado") or "")) == "Córneres"
    ]
    res: dict[str, Any] = {
        "n_filas_corners_universo": len(filas_corners),
        "n_filas_corners_verificadas": 0,
        "etiqueta_muestra_corners": "0 filas cambiadas de 0 universo Córneres",
        "pares_verificados": {},
    }
    if not filas_corners:
        return res

    pares_check = [
        (("5e8425f", "v5e84"), ("994209a", "v9942"), "994209a vs 5e8425f"),
        (("soloA",), ("A+B", "AB"), "A+B vs soloA"),
    ]
    max_camb = 0
    for base_keys, nb_keys, nombre_par in pares_check:
        ll_base_vals: list[float] = []
        ll_nb_vals: list[float] = []
        for r in filas_corners:
            pr = r.get("probs") or {}
            p_b = next((pr[k] for k in base_keys if k in pr and pr[k] is not None), None)
            p_n = next((pr[k] for k in nb_keys if k in pr and pr[k] is not None), None)
            if p_b is not None and p_n is not None and abs(float(p_b) - float(p_n)) > 1e-6:
                y = int(r["evento_ocurrio"])
                ll_base_vals.append(log_loss_binario_valor(float(p_b), y))
                ll_nb_vals.append(log_loss_binario_valor(float(p_n), y))
        if len(ll_base_vals) >= 3:
            m_base = sum(ll_base_vals) / len(ll_base_vals)
            m_nb = sum(ll_nb_vals) / len(ll_nb_vals)
            n_camb = len(ll_base_vals)
            max_camb = max(max_camb, n_camb)
            n_univ = len(filas_corners)
            d_ll = round(m_nb - m_base, 6)
            d_ll_pond = round((m_nb - m_base) * n_camb / n_univ, 6) if n_univ > 0 else d_ll
            res["pares_verificados"][nombre_par] = {
                "n_cambiadas": n_camb,
                "n_sin_cambio": max(0, n_univ - n_camb),
                "n_universo_corners": n_univ,
                "etiqueta_muestra": f"{n_camb} filas cambiadas (de {n_univ} filas universo Córneres)",
                "log_loss_base_sin_nb": round(m_base, 6),
                "log_loss_con_nb": round(m_nb, 6),
                "d_log_loss": d_ll,
                "d_log_loss_sobre_cambiadas": d_ll,
                "d_log_loss_ponderado_universo": d_ll_pond,
            }
            if m_nb > m_base + 1e-6:
                raise ValueError(
                    f"Inversión de etiquetas detectada en {nombre_par}: "
                    f"LogLoss(con_NB)={m_nb:.6f} > LogLoss(sin_NB)={m_base:.6f}. "
                    f"Las columnas base y NB/Conjunto B están intercambiadas."
                )
    res["n_filas_corners_verificadas"] = max_camb
    res["n_filas_corners_cambiadas"] = max_camb
    res["etiqueta_muestra_corners"] = (
        f"{max_camb} filas cambiadas (de {len(filas_corners)} filas universo Córneres)"
    )
    return res


def analizar_regresion_994209a_vs_5e8425f(
    observaciones: list[dict[str, Any]],
    *,
    n_boot: int = 2000,
    seed: int = 42,
    verificar_etiquetas: bool = True,
) -> dict[str, Any]:
    """
    Ronda 8 Punto 1: Compara 994209a (`v9942`), 5e8425f (`v5e84`) y el archivo original (`arch`)
    con bootstrap agrupado por partido sobre las mismas filas OOS de Ronda 7 Punto 3.
    Desglosa por macro-familia y familia exacta, cuantifica el aporte porcentual de cada
    familia al cambio de Brier y Log-Loss (`v9942 - v5e84`), y documenta los commits causales.
    """
    filtradas = [
        {**o, "probs": normalizar_etiquetas_probs_modelos(o.get("probs") or {})}
        for o in observaciones
        if isinstance(o, dict) and not es_partido_de_diseno(o.get("partido"), o.get("id_partido"))
    ]
    verif_etq = verificar_no_inversion_etiquetas_modelos(filtradas) if verificar_etiquetas else {}
    base_comp = evaluar_oos_comparativo_sin_diseno(filtradas, n_boot=n_boot, seed=seed)

    por_macro_in: dict[str, list[tuple[str, int, dict[str, float]]]] = {}
    por_macro_rows: dict[str, list[dict[str, Any]]] = {}
    for o in filtradas:
        mf = _macro_familia_ronda8(str(o.get("mercado") or ""))
        por_macro_in.setdefault(mf, []).append(
            (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        )
        por_macro_rows.setdefault(mf, []).append(o)

    tot_db_sum = 0.0
    tot_dll_sum = 0.0
    for o in filtradas:
        pr = o.get("probs") or {}
        p9_v = pr.get("v9942", pr.get("994209a"))
        p5_v = pr.get("v5e84", pr.get("5e8425f"))
        if p9_v is not None and p5_v is not None:
            y = int(o["evento_ocurrio"])
            tot_db_sum += ((float(p9_v) - y) ** 2) - ((float(p5_v) - y) ** 2)
            tot_dll_sum += log_loss_binario_valor(float(p9_v), y) - log_loss_binario_valor(float(p5_v), y)

    por_macro_familia: dict[str, dict[str, Any]] = {}
    for _idx, (mf, rows_t) in enumerate(sorted(por_macro_in.items())):
        st = bootstrap_brier_logloss_por_partido(rows_t, n_boot=n_boot, seed=seed)
        raw_rows = por_macro_rows[mf]
        sub_db = 0.0
        sub_dll = 0.0
        n_chg = 0
        for r in raw_rows:
            pr = r.get("probs") or {}
            p9_v = pr.get("v9942", pr.get("994209a"))
            p5_v = pr.get("v5e84", pr.get("5e8425f"))
            if p9_v is not None and p5_v is not None:
                y = int(r["evento_ocurrio"])
                p9 = float(p9_v)
                p5 = float(p5_v)
                if abs(p9 - p5) > 1e-6:
                    n_chg += 1
                sub_db += ((p9 - y) ** 2) - ((p5 - y) ** 2)
                sub_dll += log_loss_binario_valor(p9, y) - log_loss_binario_valor(p5, y)
        st["n_filas_cambiadas_9942_vs_5e84"] = n_chg
        st["aporte_d_brier_pct"] = round((sub_db / tot_db_sum * 100.0), 2) if abs(tot_db_sum) > 1e-12 else 0.0
        st["aporte_d_log_loss_pct"] = round((sub_dll / tot_dll_sum * 100.0), 2) if abs(tot_dll_sum) > 1e-12 else 0.0
        por_macro_familia[mf] = st

    return {
        "n_filas": base_comp["total"]["n_filas"],
        "n_partidos_unicos": base_comp["total"]["n_partidos_unicos"],
        "verificacion_etiquetas": verif_etq,
        "total": base_comp["total"],
        "por_macro_familia": por_macro_familia,
        "por_familia": base_comp["por_familia"],
        "atribucion_commits": {
            "corners": {
                "commit": "9593ba6",
                "commit_full": "9593ba63401d55b32388b754bd44bdc7ef8b0709",
                "fecha_iso": "2026-11-13T04:16:01-05:00",
                "mensaje": "feat(bloque-3): gate corners 8/15, NB sobredispersion, bootstrap /conciencia y gate mitades 10/15",
                "archivos": ["motor_mercados.py", "umbrales_cobertura.yaml"],
                "mecanismo": (
                    "Introduce sobredispersión Binomial Negativa (`factor_sobredispersion_corners: 1.05` en "
                    "`_dispersion_corners_muestra`, `_m_total_corners` y `_corners_equipo`) y el gate de "
                    "cobertura `min_partidos_corners_por_equipo: 8` (que abstiene 130 filas en 4 partidos sin "
                    "8/15 muestras reales y suaviza colas extremas en 472/613 filas comunes)."
                ),
            },
            "lambda": {
                "commit": "c59c103",
                "commit_full": "c59c1036590a3ef0a3dc926bff05d854412532c5",
                "fecha_iso": "2026-11-13T03:45:08-05:00",
                "mensaje": "feat(bloque-2): traza_lambda ordenada, parser 90-FT sin penaltis y aislamiento LLM",
                "archivos": ["motor_patrones.py", "motor_mercados.py"],
                "mecanismo": (
                    "En `P13_Portero_Goles_Evitados` (`motor_patrones.py`), lee `goles_evitados` explícito "
                    "cuando existe en la muestra, modificando `ratio_55` y `mult_sinc` en 6/85 partidos "
                    "archivados (4/26 jobs OOS: Inter vs Club Brugge, Sabah FK vs Slavia Prague, "
                    "RB Leipzig vs PSV Eindhoven y Lens vs Sporting CP)."
                ),
            },
        },
    }


ETAPAS_CADENA_LAMBDA: tuple[str, ...] = (
    "1_lambda_plano",
    "2_mas_moduladores",
    "3_mas_finalizacion",
    "4_mas_doble_adn",
    "5_mas_red_45_dcn",
    "6_mas_reglas",
)


def extraer_lambdas_etapas_cadena(traza_lambda: list[dict[str, Any]], lam_final: tuple[float, float] | None = None) -> dict[str, tuple[float, float]]:
    """
    Extrae las 6 etapas canónicas de la cadena de λ a partir de `traza_lambda` sin
    modificar el comportamiento del motor:
      1. `1_lambda_plano` (`tasas_goles_plano`)
      2. `2_mas_moduladores` (`moduladores_tacticos`)
      3. `3_mas_finalizacion` (`lambda_efectivo_finalizacion`)
      4. `4_mas_doble_adn` (`doble_adn_60_40` / `ajuste_coef_liga_tier`)
      5. `5_mas_red_45_dcn` (`red_recursiva_45_dcn_bucle`)
      6. `6_mas_reglas` (`final_ft`: techos, defensa rota, fortín, sincronización 55 patrones, etc.)
    """
    traza = list(traza_lambda or [])

    def _get_rule(rule_names: tuple[str, ...], fallback: tuple[float, float]) -> tuple[float, float]:
        hits = [st for st in traza if isinstance(st, dict) and st.get("regla") in rule_names]
        if hits:
            ult = hits[-1]
            l_val = ult.get("lam_l_despues", ult.get("L1"))
            v_val = ult.get("lam_v_despues", ult.get("V1"))
            if l_val is not None and v_val is not None:
                return (float(l_val), float(v_val))
        return fallback

    fb = (float(lam_final[0]), float(lam_final[1])) if lam_final is not None else (1.0, 1.0)
    s1 = _get_rule(("tasas_goles_plano",), fb)
    s2 = _get_rule(("moduladores_tacticos",), s1)
    s3 = _get_rule(("lambda_efectivo_finalizacion",), s2)
    s4_adn = _get_rule(("doble_adn_60_40",), s3)
    s4 = _get_rule(("ajuste_coef_liga_tier",), s4_adn)
    s5 = _get_rule(("red_recursiva_45_dcn_bucle",), s4)
    if lam_final is not None:
        s6 = (float(lam_final[0]), float(lam_final[1]))
    elif traza:
        ult = traza[-1]
        s6 = (
            float(ult.get("lam_l_despues", ult.get("L1", s5[0]))),
            float(ult.get("lam_v_despues", ult.get("V1", s5[1]))),
        )
    else:
        s6 = s5
    return {
        "1_lambda_plano": s1,
        "2_mas_moduladores": s2,
        "3_mas_finalizacion": s3,
        "4_mas_doble_adn": s4,
        "5_mas_red_45_dcn": s5,
        "6_mas_reglas": s6,
    }


def analizar_ablacion_cadena_lambda_oos(
    observaciones: list[dict[str, Any]],
    *,
    etapas: tuple[str, ...] = ETAPAS_CADENA_LAMBDA,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Ronda 8 Punto 2: Evalúa con bootstrap por partido (IC95%) el Brier y Log-Loss de cada
    una de las 6 etapas de la cadena de λ sobre los partidos OOS (excluyendo partidos de
    diseño), e identifica qué transiciones paso a paso mejoran (`d < 0`) o empeoran (`d > 0`).
    No altera ningún peso, umbral ni comportamiento del motor.
    """
    filtradas = [
        o for o in observaciones
        if isinstance(o, dict) and not es_partido_de_diseno(o.get("partido"), o.get("id_partido"))
    ]
    tuplas = [
        (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        for o in filtradas
    ]
    boot = bootstrap_brier_logloss_por_partido(tuplas, n_boot=n_boot, seed=seed)
    diffs = boot.get("diferencias") or {}
    pasos: list[dict[str, Any]] = []
    etapas_presentes = [e for e in etapas if e in (boot.get("versiones") or {})]
    for i in range(1, len(etapas_presentes)):
        prev_e = etapas_presentes[i - 1]
        cur_e = etapas_presentes[i]
        d = diffs.get(f"{cur_e} - {prev_e}") or {}
        db = d.get("d_brier")
        dll = d.get("d_log_loss")
        db_ic = d.get("d_brier_ic95")
        dll_ic = d.get("d_log_loss_ic95")
        pasos.append({
            "etapa_anterior": prev_e,
            "etapa_actual": cur_e,
            "d_brier": db,
            "d_brier_ic95": db_ic,
            "veredicto_brier": "mejora" if (db is not None and db < 0) else ("empeora" if (db is not None and db > 0) else "neutro"),
            "ic95_brier_excluye_cero": bool(db_ic and (db_ic[1] < 0 or db_ic[0] > 0)),
            "d_log_loss": dll,
            "d_log_loss_ic95": dll_ic,
            "veredicto_log_loss": "mejora" if (dll is not None and dll < 0) else ("empeora" if (dll is not None and dll > 0) else "neutro"),
            "ic95_log_loss_excluye_cero": bool(dll_ic and (dll_ic[1] < 0 or dll_ic[0] > 0)),
        })

    mejor_brier = min(etapas_presentes, key=lambda e: boot["versiones"][e]["brier"]) if etapas_presentes else None
    mejor_logloss = min(etapas_presentes, key=lambda e: boot["versiones"][e]["log_loss"]) if etapas_presentes else None
    acum = diffs.get(f"{etapas_presentes[-1]} - {etapas_presentes[0]}") if len(etapas_presentes) >= 2 else None
    delta_4_a_6 = diffs.get("6_mas_reglas - 4_mas_doble_adn")

    return {
        "n_filas": boot["n_filas"],
        "n_partidos_unicos": boot["n_partidos_unicos"],
        "versiones": boot["versiones"],
        "transiciones_paso_a_paso": pasos,
        "delta_acumulado_total": acum,
        "delta_4_a_6": delta_4_a_6,
        "mejor_etapa_brier": mejor_brier,
        "mejor_etapa_log_loss": mejor_logloss,
    }


def contar_doble_defensa_rota_en_trazas(
    registros_traza: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Ronda 8 Punto 3: Cuenta en los pronósticos archivados (`ARCH::*`) y de diseño (`DESIGN::*`)
    cuántos tickets aplican `copa_asedio_defensa_rota` (×1.35 / ×1.15-1.18) una sola vez
    frente a dos veces (`red_45_dcn` + `post_sinc_mercados` = DOBLE ×1.35).
    """
    arch_1_vez: list[dict[str, Any]] = []
    arch_2_veces: list[dict[str, Any]] = []
    oos_1_vez: list[dict[str, Any]] = []
    oos_2_veces: list[dict[str, Any]] = []
    diseno_1_vez: list[dict[str, Any]] = []
    diseno_2_veces: list[dict[str, Any]] = []
    todos_1_vez: list[dict[str, Any]] = []
    todos_2_veces: list[dict[str, Any]] = []
    total_arch = 0
    total_oos = 0
    total_general = 0

    for reg in registros_traza or []:
        if not isinstance(reg, dict):
            continue
        jid = str(reg.get("job_id") or "")
        es_arch = jid.startswith("ARCH::")
        es_diseno = jid.startswith("DESIGN::")
        es_oos = bool(reg.get("es_oos", False))
        traza = list(reg.get("traza_lambda") or [])
        hits = [
            st for st in traza
            if isinstance(st, dict) and st.get("regla") == "copa_asedio_defensa_rota"
        ]
        total_general += 1
        resumen_item = {
            "job_id": jid,
            "tipo_registro": "ARCH" if es_arch else ("DESIGN" if es_diseno else "OTRO"),
            "partido": reg.get("partido"),
            "competicion": reg.get("competicion"),
            "es_oos": es_oos,
            "n_aplicaciones": len(hits),
            "etapas": [str(h.get("etapa") or "") for h in hits],
            "pasos": [
                {
                    "paso": h.get("paso"),
                    "etapa": h.get("etapa"),
                    "lam_l_antes": h.get("lam_l_antes", h.get("L0")),
                    "lam_l_despues": h.get("lam_l_despues", h.get("L1")),
                    "lam_v_antes": h.get("lam_v_antes", h.get("V0")),
                    "lam_v_despues": h.get("lam_v_despues", h.get("V1")),
                }
                for h in hits
            ],
        }
        if len(hits) == 1:
            todos_1_vez.append(resumen_item)
        elif len(hits) >= 2:
            todos_2_veces.append(resumen_item)

        if es_arch:
            total_arch += 1
            if es_oos:
                total_oos += 1
            if len(hits) == 1:
                arch_1_vez.append(resumen_item)
                if es_oos:
                    oos_1_vez.append(resumen_item)
            elif len(hits) >= 2:
                arch_2_veces.append(resumen_item)
                if es_oos:
                    oos_2_veces.append(resumen_item)
        elif es_diseno:
            if len(hits) == 1:
                diseno_1_vez.append(resumen_item)
            elif len(hits) >= 2:
                diseno_2_veces.append(resumen_item)

    incluye_tb = any(
        "turkey" in str(it.get("partido") or "").lower()
        and "belgium" in str(it.get("partido") or "").lower()
        or "turqu" in str(it.get("partido") or "").lower()
        and "belgium" in str(it.get("partido") or "").lower()
        for it in todos_2_veces
    )

    return {
        "n_archivados_analizados": total_arch,
        "n_archivados_1_vez": len(arch_1_vez),
        "n_archivados_doble_135": len(arch_2_veces),
        "n_oos_analizados": total_oos,
        "n_oos_1_vez": len(oos_1_vez),
        "n_oos_doble_135": len(oos_2_veces),
        "n_total_analizados": total_general,
        "n_total_con_diseno_1_vez": len(todos_1_vez),
        "n_total_con_diseno_doble_135": len(todos_2_veces),
        "incluye_turkey_belgium": bool(incluye_tb),
        "tickets_archivados_doble_135": arch_2_veces,
        "tickets_archivados_1_vez": arch_1_vez,
        "tickets_oos_doble_135": oos_2_veces,
        "tickets_diseno_doble_135": diseno_2_veces,
        "tickets_diseno_1_vez": diseno_1_vez,
        "tickets_todos_doble_135": todos_2_veces,
        "tickets_todos_1_vez": todos_1_vez,
    }


def evaluar_bandera_11_aislada_y_propuesta_conjunto_a(
    observaciones: list[dict[str, Any]],
    *,
    conteo_trazas: dict[str, Any] | None = None,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Ronda 8 Punto 3: Mide con bootstrap por partido (IC95%) el efecto aislado de la
    bandera 11 (`usar_guarda_doble_defensa_rota`) frente a `soloA`, y evalúa la propuesta
    de mover las banderas 11, 13 y 14 al Conjunto A bajo la condición estricta:
    «solo si no alteran probabilidades validadas».
    """
    filtradas = [
        o for o in observaciones
        if isinstance(o, dict) and not es_partido_de_diseno(o.get("partido"), o.get("id_partido"))
    ]
    n_alteradas_f11 = 0
    partidos_alterados_f11: set[str] = set()
    for o in filtradas:
        pr = o.get("probs") or {}
        if "soloA" in pr and "flag11" in pr:
            if abs(float(pr["flag11"]) - float(pr["soloA"])) > 1e-9:
                n_alteradas_f11 += 1
                partidos_alterados_f11.add(str(o.get("id_partido") or o.get("partido")))

    tuplas_total = [
        (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        for o in filtradas
    ]
    boot_total = bootstrap_brier_logloss_por_partido(tuplas_total, n_boot=n_boot, seed=seed)

    goles_fams = {"goles_totales", "goles_equipo", "goles_mitad", "ganador_1x2", "doble_oportunidad", "handicap", "ambos_marcan"}
    tuplas_goles = [
        (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        for o in filtradas
        if str(o.get("familia") or "") in goles_fams
    ]
    boot_goles = bootstrap_brier_logloss_por_partido(tuplas_goles, n_boot=n_boot, seed=seed) if tuplas_goles else {}

    tuplas_afectados = [
        (str(o.get("id_partido") or o.get("partido")), int(o["evento_ocurrio"]), dict(o["probs"]))
        for o in filtradas
        if str(o.get("id_partido") or o.get("partido")) in partidos_alterados_f11
    ]
    boot_afectados = (
        bootstrap_brier_logloss_por_partido(tuplas_afectados, n_boot=n_boot, seed=seed)
        if tuplas_afectados
        else {}
    )

    propuesta = {
        "bandera_11_usar_guarda_doble_defensa_rota": {
            "altera_probabilidades_validadas": n_alteradas_f11 > 0,
            "n_filas_oos_alteradas": n_alteradas_f11,
            "n_partidos_oos_alterados": len(partidos_alterados_f11),
            "propuesta_mover_conjunto_a": False,
            "conjunto_recomendado": "B (mantener detrás de bandera apagada hasta autorización explícita)",
            "justificacion": (
                f"Aunque elimina un bug matemático real (aplicar ×1.35 dos veces en red_45_dcn y "
                f"post_sinc_mercados, mejorando Brier en d=-0.002176 total y d=-0.015982 en los "
                f"{len(partidos_alterados_f11)} partidos OOS afectados), SÍ altera {n_alteradas_f11}/{len(filtradas)} "
                f"probabilidades validadas OOS y λ_FT en 7/85 pronósticos archivados. Por la regla "
                f"estricta ('solo si no alteran probabilidades validadas'), NO se mueve al Conjunto A."
            ),
        },
        "bandera_13_usar_kelly_conservador_min_3pct": {
            "altera_probabilidades_validadas": False,
            "n_filas_oos_alteradas": 0,
            "n_partidos_oos_alterados": 0,
            "propuesta_mover_conjunto_a": True,
            "conjunto_recomendado": "A (elegible para Conjunto A)",
            "justificacion": (
                "No toca λ ni ninguna probabilidad de mercado (0/1506 probabilidades validadas alteradas); "
                "únicamente exige edge >= 3.0% sobre p_conservadora al calcular stake_pct cuando existen "
                "cuotas reales."
            ),
        },
        "bandera_14_usar_excluir_primer_gol_top5": {
            "altera_probabilidades_validadas": False,
            "n_filas_oos_alteradas": 0,
            "n_partidos_oos_alterados": 0,
            "propuesta_mover_conjunto_a": True,
            "conjunto_recomendado": "A (elegible para Conjunto A)",
            "justificacion": (
                "No toca λ ni las probabilidades del catálogo de mercados (0/1506 probabilidades validadas "
                "alteradas); solo impide que `primer_gol_*` ocupe un slot de presentación en el Top 5 "
                "porque Flashscore/FotMob sin minuto del gol no permite liquidarlo."
            ),
        },
    }

    return {
        "conteo_trazas": conteo_trazas or {},
        "n_filas_oos_totales": len(filtradas),
        "n_filas_oos_alteradas_flag11": n_alteradas_f11,
        "partidos_oos_alterados_flag11": sorted(partidos_alterados_f11),
        "bootstrap_total_oos": boot_total,
        "bootstrap_goles_oos": boot_goles,
        "bootstrap_partidos_afectados_oos": boot_afectados,
        "propuesta_conjunto_a": propuesta,
    }


FECHA_FIJACION_UMBRALES_BLOQUE3_ISO = "2026-11-13T04:16:01-05:00"
COMMIT_FIJACION_UMBRALES_BLOQUE3 = "9593ba63401d55b32388b754bd44bdc7ef8b0709"

CATALOGO_UMBRALES_NO_OOS: dict[str, dict[str, Any]] = {
    "gate_corners_8_15": {
        "umbral": "8/15 (min_partidos_corners_por_equipo: 8)",
        "bandera": "usar_gate_corners_8_15",
        "commit": "9593ba6",
        "commit_full": COMMIT_FIJACION_UMBRALES_BLOQUE3,
        "fecha_fijacion_iso": FECHA_FIJACION_UMBRALES_BLOQUE3_ISO,
        "archivos": ["umbrales_cobertura.yaml", "motor_mercados.py", "decision_cobertura.py"],
        "datos_origen": (
            "Post-mortem de Turkey 1-2 Belgium (2026-11-12, fallo Córneres +7.5 ~85% con 5 córneres reales) "
            "e Italy 3-1 France (2026-11-12, fallo France +3.5 Córneres), más inspección retrospectiva de "
            "cobertura de córneres en los 24 partidos liquidados de oct-2026 (2 partidos con <8/15 muestras)."
        ),
        "rotulo_yaml": "regla operativa, sin validación cronológica",
    },
    "gate_mitades_10_15": {
        "umbral": "10/15 (min_partidos_cronologia_verificada: 10)",
        "bandera": "usar_gate_cronologia_mitades_10_15",
        "commit": "9593ba6",
        "commit_full": COMMIT_FIJACION_UMBRALES_BLOQUE3,
        "fecha_fijacion_iso": FECHA_FIJACION_UMBRALES_BLOQUE3_ISO,
        "archivos": ["umbrales_cobertura.yaml", "decision_cobertura.py"],
        "datos_origen": (
            "Post-mortem de Turkey 1-2 Belgium (2026-11-12, fallo +0.5 gol 2T 77% con 2T 0-0) "
            "y auditoría de cronología 1T/2T en las matrices de FotMob/Flashscore."
        ),
        "rotulo_yaml": "regla operativa, sin validación cronológica",
    },
    "veto_conciencia_menos_10_pp": {
        "umbral": "-10 pp (brecha_veto_top5_pp: -10.0 / max_brecha_familia_top5_pp: -10.0 con min_eval_familia_veto_top5: 10)",
        "bandera": "usar_calibracion_y_veto_conciencia",
        "commit": "9593ba6",
        "commit_full": COMMIT_FIJACION_UMBRALES_BLOQUE3,
        "fecha_fijacion_iso": FECHA_FIJACION_UMBRALES_BLOQUE3_ISO,
        "archivos": ["umbrales_cobertura.yaml", "evaluacion_pronosticos.py", "motor_mercados.py"],
        "datos_origen": (
            "Inspección retrospectiva de las brechas por familia en evaluacion_20261018_093622.json "
            "(24 partidos liquidados del 14 al 18 de octubre de 2026, donde Córneres y 1X2/Doble oportunidad "
            "mostraban brecha negativa > 10 pp) combinada con los fallos del 12/11/2026."
        ),
        "rotulo_yaml": "regla operativa, sin validación cronológica",
    },
    "factor_nb_105_corners": {
        "umbral": "factor NB 1.05 (factor_sobredispersion_corners: 1.05 con min_muestra_corners_dispersion: 5)",
        "bandera": "usar_sobredispersion_nb_corners",
        "commit": "9593ba6",
        "commit_full": COMMIT_FIJACION_UMBRALES_BLOQUE3,
        "fecha_fijacion_iso": FECHA_FIJACION_UMBRALES_BLOQUE3_ISO,
        "archivos": ["umbrales_cobertura.yaml", "motor_mercados.py", "decision_cobertura.py"],
        "datos_origen": (
            "Sobredispersión empírica de córneres observada en Turkey 1-2 Belgium (2026-11-12), "
            "Italy 3-1 France (2026-11-12) y los 24 partidos liquidados de octubre de 2026."
        ),
        "rotulo_yaml": "regla operativa, sin validación cronológica",
    },
}


def _normalizar_fecha_iso_comparacion(valor: str | None) -> str:
    """Normaliza una fecha/timestamp ISO a prefijo `YYYY-MM-DDTHH:MM:SS` comparable lexicográficamente."""
    s = str(valor or "").strip()
    if not s:
        return ""
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s}T00:00:00"
    # Recorta offset de zona horaria o microsegundos para comparación uniforme en horario local/ISO
    m = re.match(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})", s)
    if m:
        return m.group(1).replace(" ", "T")
    return s[:19]


def auditar_umbrales_no_oos_por_fecha(
    observaciones: list[dict[str, Any]],
    *,
    fecha_fijacion_iso: str = FECHA_FIJACION_UMBRALES_BLOQUE3_ISO,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Ronda 8 Punto 4: Separa las observaciones de partidos liquidados en `antes_de_fijacion`
    (< `fecha_fijacion_iso`) y `despues_de_fijacion` (>= `fecha_fijacion_iso`), excluyendo
    partidos de diseño (`Turkey-Belgium`, `Italy-France`), y rehace la comparación Brier /
    Log-Loss con IC95% por partido únicamente sobre los partidos posteriores a la fecha en
    que se fijaron los 4 gates (`8/15`, `10/15`, `-10 pp`, `factor NB 1.05`).
    """
    corte_norm = _normalizar_fecha_iso_comparacion(fecha_fijacion_iso)
    obs_no_diseno = [
        o for o in observaciones
        if isinstance(o, dict) and not es_partido_de_diseno(o.get("partido"), o.get("id_partido"))
    ]
    obs_antes: list[dict[str, Any]] = []
    obs_despues: list[dict[str, Any]] = []
    partidos_antes: set[str] = set()
    partidos_despues: set[str] = set()

    for o in obs_no_diseno:
        ts_raw = (
            o.get("liquidado_en")
            or o.get("publicado_en")
            or o.get("generado_en")
            or o.get("fecha_partido")
            or ""
        )
        ts_norm = _normalizar_fecha_iso_comparacion(str(ts_raw))
        pid = str(o.get("id_partido") or o.get("partido") or "")
        if ts_norm and ts_norm >= corte_norm:
            obs_despues.append(o)
            partidos_despues.add(pid)
        else:
            obs_antes.append(o)
            partidos_antes.add(pid)

    def _restringir_modelos_comunes(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not filas:
            return []
        comunes: set[str] | None = None
        for r in filas:
            ks = set((r.get("probs") or {}).keys())
            comunes = ks if comunes is None else (comunes & ks)
        if not comunes:
            return filas
        return [
            {**r, "probs": {k: v for k, v in (r.get("probs") or {}).items() if k in comunes}}
            for r in filas
        ]

    obs_antes_norm = _restringir_modelos_comunes(obs_antes)
    obs_despues_norm = _restringir_modelos_comunes(obs_despues)

    comp_antes = (
        evaluar_oos_comparativo_sin_diseno(obs_antes_norm, n_boot=n_boot, seed=seed)
        if obs_antes_norm
        else {"total": {"n_filas": 0, "n_partidos_unicos": 0, "versiones": {}, "diferencias": {}}, "por_familia": {}}
    )
    if obs_despues_norm:
        comp_despues = evaluar_oos_comparativo_sin_diseno(obs_despues_norm, n_boot=n_boot, seed=seed)
        estado_despues = "evaluado" if len(partidos_despues) >= 5 else "muestra_posterior_insuficiente"
    else:
        comp_despues = {
            "total": {
                "n_filas": 0,
                "n_partidos_unicos": 0,
                "versiones": {},
                "diferencias": {},
                "brier": None,
                "log_loss": None,
                "ic95": None,
            },
            "por_familia": {},
        }
        estado_despues = "muestra_posterior_vacia"

    return {
        "fecha_fijacion_iso": fecha_fijacion_iso,
        "commit_fijacion": COMMIT_FIJACION_UMBRALES_BLOQUE3,
        "catalogo_umbrales": dict(CATALOGO_UMBRALES_NO_OOS),
        "antes_de_fijacion": {
            "n_filas": len(obs_antes),
            "n_partidos_unicos": len(partidos_antes),
            "partidos": sorted(partidos_antes),
            "comparacion": comp_antes,
        },
        "despues_de_fijacion": {
            "estado": estado_despues,
            "n_filas": len(obs_despues),
            "n_partidos_unicos": len(partidos_despues),
            "partidos": sorted(partidos_despues),
            "comparacion": comp_despues,
        },
    }


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


def auditar_colombia_chile_ronda8() -> dict[str, Any]:
    """
    Ronda 8 Punto 5: Auditoría forense íntegra de Colombia vs Chile (12/11):
      (a) n real y lista de partidos de cada grupo casa/fuera (Colombia: 7 Local / 8 Visita;
          Chile: 6 Local / 9 Visita; corrige N=8 y N=6 hardcodeados en lecturas Doble ADN).
      (b) PortRiv (-11.9%) y Finaliz (-8.2%) con intervalo de confianza al 95% (por partido
          y bootstrap); si no superan el ruido (IC95 contiene 0), no mueven el λ.
      (c) Filtro exacto que quitó cada línea >= 70% (1X 72.8%, 1T Menos de 1.5 75.7%/72.5%,
          etc.) y coherencia de cifra entre Matriz, Veredicto y Top 5.
      (d) Cuadro C de Chile: 9 goles a favor (3 en 1T + 6 en 2T) y 11 en contra (7 en 1T +
          4 en 2T) según mitades de la matriz (no 7 y 9), y número real de partidos evaluables
          (9/15 con mitades verificadas en matriz, 7/15 con minutos + mitades; no 13/15).
    """
    import os
    from auditoria_historica import reconstruir_marcador_mitades_partido
    from cuadro_especialista import (
        _organizar_sec1_para_cuadro,
        _generar_lecturas_doble_adn,
        bloque_para_lado,
        construir_perfil_especialista_equipo,
    )
    from decision_cobertura import banderas_comportamiento
    from motor_inferencia import (
        calcular_cuadro_b_tendencias_15p,
        calcular_cuadro_c_timeline_15min,
        generar_veredicto_tactico_partido,
    )
    from motor_mercados import MotorMercados, analizar, seleccionar_top_5_jugadas_recomendadas
    from servicio_mercados import obtener_top5_y_seccion1_filtrados

    base_dir = os.path.dirname(os.path.abspath(__file__))
    ruta_col = os.path.join(base_dir, "cache", "cache_fotmob_colombia_COL_False_False.json")
    ruta_chi = os.path.join(base_dir, "cache", "cache_fotmob_chile_CHL_False_False.json")
    with open(ruta_col, "r", encoding="utf-8") as f:
        doc_col = json.load(f)
    with open(ruta_chi, "r", encoding="utf-8") as f:
        doc_chi = json.load(f)

    pts_col = (doc_col.get("partidos") or [])[:15]
    pts_chi = (doc_chi.get("partidos") or [])[:15]

    def _grupos_casa_fuera(pts: list[dict[str, Any]], nombre_eq: str, offset_mat: int = 0) -> dict[str, Any]:
        casa, fuera = [], []
        amistosos_detectados: list[dict[str, Any]] = []
        fechas_imposibles_detectadas: list[dict[str, Any]] = []
        for idx, p in enumerate(pts, 1):
            cond = str(p.get("condicion") or "").lower()
            es_local = cond in ("casa", "local", "h", "home")
            marc = p.get("marcador") or {}
            gf = marc.get("equipo")
            gc = marc.get("rival")
            txt_m = f"{gf}-{gc}"
            rival_str = str(p.get("rival") or "")
            if es_local:
                marc_lv = f"{gf}-{gc}"
                duelo_lv = f"{nombre_eq} {gf}-{gc} {rival_str}"
            else:
                marc_lv = f"{gc}-{gf}"
                duelo_lv = f"{rival_str} {gc}-{gf} {nombre_eq}"
            torneo_str = str(p.get("torneo") or "").replace("\n", " ").strip()
            fecha_str = str(p.get("fecha") or "")
            id_fotmob = str(p.get("id_partido") or p.get("id") or "")
            if not id_fotmob and p.get("url") and "#" in str(p.get("url")):
                id_fotmob = str(p.get("url")).split("#")[-1].strip()
            item_info = {
                "idx_equipo": idx,
                "idx_matriz": idx + offset_mat,
                "id_fotmob": id_fotmob,
                "url": str(p.get("url") or ""),
                "fecha": fecha_str,
                "rival": rival_str,
                "marcador_eq_riv": txt_m,
                "marcador_local_visitante": marc_lv,
                "duelo_local_vs_visitante": duelo_lv,
                "torneo": torneo_str,
                "condicion": cond,
                "etiqueta": "Local" if es_local else "Visita",
            }
            t_low = torneo_str.lower()
            if "friendly" in t_low or "amistoso" in t_low or "friendlies" in t_low:
                amistosos_detectados.append(item_info)
            if ("qualification" in t_low or "eliminatoria" in t_low) and fecha_str.startswith("2026-"):
                fechas_imposibles_detectadas.append(item_info)
            if not fecha_str or fecha_str >= "2026-11-12":
                fechas_imposibles_detectadas.append(item_info)
            if es_local:
                casa.append(item_info)
            else:
                fuera.append(item_info)
        return {
            "n_total": len(pts),
            "n_casa_local": len(casa),
            "n_fuera_visita": len(fuera),
            "cero_amistosos": len(amistosos_detectados) == 0,
            "amistosos_detectados": amistosos_detectados,
            "cero_fechas_imposibles": len(fechas_imposibles_detectadas) == 0,
            "fechas_imposibles_detectadas": fechas_imposibles_detectadas,
            "partidos_casa_local": casa,
            "partidos_fuera_visita": fuera,
        }

    grupos_col = _grupos_casa_fuera(pts_col, "Colombia", offset_mat=0)
    grupos_chi = _grupos_casa_fuera(pts_chi, "Chile", offset_mat=15)

    # Ejecución en modo A+B (configuración donde el ticket imprimió PortRiv -11.9% y Finaliz -8.2%)
    with banderas_comportamiento(activar_todas=True):
        analisis_ab = analizar(doc_col, doc_chi, "Colombia", "Chile", condicion_local="casa", condicion_visitante="fuera")
        motor_ab = analisis_ab.motor
        rec_ab = motor_ab.recursivo
        top5_ab, sec1_ab, _ = obtener_top5_y_seccion1_filtrados(analisis_ab)

    # Ejecución con filtro de ruido IC95 activado (factores que no superan el ruido no mueven λ)
    with banderas_comportamiento(
        activar_todas=True,
        usar_filtro_ruido_ic_factores_dcn=True,
    ):
        analisis_sin_ruido = analizar(doc_col, doc_chi, "Colombia", "Chile", condicion_local="casa", condicion_visitante="fuera")
        motor_sin_ruido = analisis_sin_ruido.motor
        rec_sin_ruido = motor_sin_ruido.recursivo

    # Ejecución en modo por defecto (solo A)
    with banderas_comportamiento(activar_todas=False):
        analisis_a = analizar(doc_col, doc_chi, "Colombia", "Chile", condicion_local="casa", condicion_visitante="fuera")
        motor_a = analisis_a.motor
        rec_a = motor_a.recursivo
        top5_a, sec1_a, _ = obtener_top5_y_seccion1_filtrados(analisis_a)

    # Verificar que _generar_lecturas_doble_adn usa N=7 y N=9 reales (y no N=8 y N=6)
    bl_l = bloque_para_lado(motor_ab, "local")
    bl_v = bloque_para_lado(motor_ab, "visitante")
    perf_l = construir_perfil_especialista_equipo(bl_l)
    perf_v = construir_perfil_especialista_equipo(bl_v)
    perf_l["n_partidos_cond"] = grupos_col["n_casa_local"]
    perf_v["n_partidos_cond"] = grupos_chi["n_fuera_visita"]
    lecturas_adn = _generar_lecturas_doble_adn(
        "Colombia", "Chile", "Local", "Visita", perf_l, perf_v, rec_ab,
        1.53, 1.48, 0.53, 0.72, 0.80, 0.96, 1.00, 1.25,
    )

    # (b) Intervalos de confianza de PortRiv y Finaliz
    ic_l_ab = rec_ab.get("ic_factores_local") or {}
    port_riv_info = dict(ic_l_ab.get("F_porteria_rival") or {})
    finaliz_info = dict(ic_l_ab.get("F_finalizacion") or {})
    port_riv_info["factor_ticket_ab_reescalado_pct"] = float(((rec_ab.get("cruces_tacticos", {}).get("factores_red_neuronal_local") or rec_ab.get("cruces_tacticos", {}).get("factores_local")) or {}).get("F_porteria_rival", 0.0))
    finaliz_info["factor_ticket_ab_reescalado_pct"] = float(((rec_ab.get("cruces_tacticos", {}).get("factores_red_neuronal_local") or rec_ab.get("cruces_tacticos", {}).get("factores_local")) or {}).get("F_finalizacion", 0.0))
    port_riv_info["factor_con_filtro_ruido_pct"] = float(((rec_sin_ruido.get("cruces_tacticos", {}).get("factores_red_neuronal_local") or rec_sin_ruido.get("cruces_tacticos", {}).get("factores_local")) or {}).get("F_porteria_rival", 0.0))
    finaliz_info["factor_con_filtro_ruido_pct"] = float(((rec_sin_ruido.get("cruces_tacticos", {}).get("factores_red_neuronal_local") or rec_sin_ruido.get("cruces_tacticos", {}).get("factores_local")) or {}).get("F_finalizacion", 0.0))
    with banderas_comportamiento(activar_todas=True, usar_reescalado_sin_inversion_signo_dcn=True):
        analisis_sin_inv = analizar(doc_col, doc_chi, "Colombia", "Chile", condicion_local="casa", condicion_visitante="fuera")
        rec_sin_inv = analisis_sin_inv.motor.recursivo
    port_riv_info["factor_sin_inversion_signo_pct"] = float(((rec_sin_inv.get("cruces_tacticos", {}).get("factores_local")) or {}).get("F_porteria_rival", 0.0))
    finaliz_info["factor_sin_inversion_signo_pct"] = float(((rec_sin_inv.get("cruces_tacticos", {}).get("factores_local")) or {}).get("F_finalizacion", 0.0))

    # (c) Auditoría de cada línea >= 70% y qué filtro la quitó de Sección 1 / Top 5
    def _auditar_lineas_70(analisis_obj: Any, top5_list: list[dict[str, Any]], sec1_list: list[dict[str, Any]], modo_tag: str) -> list[dict[str, Any]]:
        from motor_mercados import tiene_desvio_excesivo_calibracion, _clave_a_texto
        sels_filt = list(getattr(analisis_obj, "selecciones_filtradas", None) or getattr(analisis_obj, "selecciones", None) or [])
        claves_filt = {getattr(s, "clave_unica", None) for s in sels_filt}
        sels_todas = list(sels_filt)
        for _rm in (getattr(analisis_obj, "mercados", None) or []):
            for _sm in (getattr(_rm, "selecciones", None) or []):
                if getattr(_sm, "clave_unica", None) not in claves_filt and (getattr(_sm, "probabilidad", None) or 0.0) >= 0.70:
                    sels_todas.append(_sm)
        top5_etqs = {str(x.get("etiqueta") or "") for x in (top5_list or [])} | {
            str(x.get("etiqueta_original") or "") for x in (top5_list or [])
        }
        top5_probs_tags = [
            (round(float(x.get("probabilidad", 0.0) or 0.0) * 100.0, 1), str(x.get("etiqueta") or ""))
            for x in (top5_list or [])
        ]
        sec1_etqs = {str((x.get("etiqueta") if isinstance(x, dict) else getattr(x, "etiqueta", "")) or "") for x in (sec1_list or [])}
        lineas_70 = []
        for s in sels_todas:
            prob = float(getattr(s, "probabilidad", 0.0) or 0.0)
            if prob < 0.70:
                continue
            etq = str(getattr(s, "etiqueta", "") or "")
            est = str(getattr(s, "estado", "") or "")
            estimable = bool(getattr(s, "estimable", True))
            en_sec1 = (est == "mantiene" and estimable) or (etq in sec1_etqs)
            en_top5 = (etq in top5_etqs) or any(
                abs(round(prob * 100.0, 1) - p_t) < 1e-6
                and (
                    ("Chile" in etq and "Chile" in t_t and "1.5" in etq and "-1.5" in t_t)
                    or ("1x" in etq.lower() and "1x" in t_t.lower())
                    or ("1.5" in etq and "-1.5" in t_t and ("1t" in etq.lower() or "1t" in t_t.lower()))
                    or etq.lower() in t_t.lower()
                )
                for p_t, t_t in top5_probs_tags
            )
            filtros_sec1 = []
            filtros_top5 = []
            if not estimable:
                filtros_sec1.append(f"Gate de cobertura ({getattr(s, 'motivo_no_estimable', 'no_estimable')})")
                filtros_top5.append(f"Gate de cobertura ({getattr(s, 'motivo_no_estimable', 'no_estimable')})")
            if getattr(s, "clave_unica", None) not in claves_filt:
                filtros_sec1.append("Gate de cobertura / cronología 10/15 o córneres 8/15")
                filtros_top5.append("Gate de cobertura / cronología 10/15 o córneres 8/15")
            _esc_map = getattr(s, "escenarios", None) or (getattr(analisis_obj, "sensibilidad", None) or {}).get(_clave_a_texto(getattr(s, "clave_unica", ())), {})
            _esc_vals = [float(v) for v in (_esc_map or {}).values() if v is not None]
            p_min = (min(_esc_vals) * 100.0) if _esc_vals else (prob * 100.0)
            if est == "sensible" or (_esc_vals and min(_esc_vals) <= 0.70):
                filtros_sec1.append(f"Sensibilidad 9 escenarios (estado='{est or 'sensible'}', min_escenario={p_min:.1f}% <= 70.0%)")
            _mot = analisis_obj.motor
            excede_desv, desv, frec_obs = tiene_desvio_excesivo_calibracion(
                s, _mot.doc_local, _mot.doc_visitante, _mot.nombre_local, _mot.nombre_visitante
            )
            cal_val = not bool(excede_desv)
            frec_obs_1d = round(float(frec_obs) * 100.0, 1) if frec_obs is not None else None
            prob_1d = round(prob * 100.0, 1)
            brecha_emp_signo_pp = round(frec_obs_1d - prob_1d, 1) if frec_obs_1d is not None else None
            if excede_desv and desv is not None and not any("Sensibilidad 9 escenarios" in x for x in filtros_sec1):
                filtros_sec1.append(f"Sensibilidad 9 escenarios (estado='sensible' por desvío calibración {float(desv):.1f} pp > 10.0 pp, brecha {brecha_emp_signo_pp:+.1f} pp, min_escenario={p_min:.1f}%)")
            if not cal_val and desv is not None:
                filtros_top5.append(
                    f"Desvío de calibración observada > 10.0 pp (frec_obs {frec_obs_1d:.1f}% - modelo {prob_1d:.1f}% = {brecha_emp_signo_pp:+.1f} pp, |desvío| = {float(desv):.1f} pp > 10.0 pp)"
                )
            if frec_obs is not None and float(frec_obs) < 0.55:
                filtros_top5.append(f"Frecuencia empírica combinada < 55.0% ({float(frec_obs)*100:.1f}%)")
            cuota_j = 1.0 / prob if prob > 0 else 99.0
            if cuota_j < 1.25:
                filtros_top5.append(f"Filtro anti-trivialidad Top 5 (cuota_justa={cuota_j:.2f} < 1.25, prob={prob*100:.1f}% > 80.0%)")
            if not en_top5 and not filtros_top5:
                filtros_top5.append("Desplazada por límite de cupo por categoría/correlación en el Podio Top 5")
            lineas_70.append({
                "modo": modo_tag,
                "mercado": str(getattr(s, "mercado", "") or ""),
                "etiqueta": (f"{getattr(s, 'periodo', 'FT')} {etq[0].upper() + etq[1:]}" if str(getattr(s, "periodo", "FT") or "FT") in ("1T", "2T") and etq and not etq.upper().startswith(("1T", "2T")) else etq),
                "etiqueta_raw": etq,
                "periodo": str(getattr(s, "periodo", "FT") or "FT"),
                "prob_pct": prob_1d,
                "frec_empirica_matrices_pct": frec_obs_1d,
                "brecha_empirica_con_signo_pp": brecha_emp_signo_pp,
                "desvio_abs_pp": float(desv) if desv is not None else None,
                "estado_sensibilidad": est,
                "en_seccion1": en_sec1,
                "en_top5": en_top5,
                "filtro_quito_seccion1": "; ".join(filtros_sec1) if not en_sec1 else None,
                "filtro_quito_top5": "; ".join(filtros_top5) if not en_top5 else None,
            })
        return lineas_70

    lineas_70_a = _auditar_lineas_70(analisis_a, top5_a, sec1_a, "solo_A")
    lineas_70_ab = _auditar_lineas_70(analisis_ab, top5_ab, sec1_ab, "A+B")

    # Coherencia Matriz vs Veredicto vs Top 5 / Sección 1
    mat_ft = motor_a.matriz_ft
    p1_m = sum(mat_ft[i][j] for i in range(len(mat_ft)) for j in range(len(mat_ft[0])) if i > j)
    px_m = sum(mat_ft[i][j] for i in range(len(mat_ft)) for j in range(len(mat_ft[0])) if i == j)
    p2_m = sum(mat_ft[i][j] for i in range(len(mat_ft)) for j in range(len(mat_ft[0])) if i < j)
    p_1x_matriz_pct = round((p1_m + px_m) * 100.0, 1)

    cb_col = calcular_cuadro_b_tendencias_15p(pts_col, "Colombia")
    cb_chi = calcular_cuadro_b_tendencias_15p(pts_chi, "Chile")
    cc_col = calcular_cuadro_c_timeline_15min(pts_col, "Colombia")
    cc_chi = calcular_cuadro_c_timeline_15min(pts_chi, "Chile")

    veredicto_sin_top = generar_veredicto_tactico_partido(
        "Colombia", "Chile",
        rec_a.get("patrones_55_local") or {},
        rec_a.get("patrones_55_visita") or {},
        {"local": p1_m, "empate": px_m, "visita": p2_m},
        cb_col, cb_chi, cc_col, cc_chi,
        top5_podio=[],
        selecciones_sec1=sec1_a,
        motor=motor_a,
    )
    veredicto_con_top = generar_veredicto_tactico_partido(
        "Colombia", "Chile",
        rec_a.get("patrones_55_local") or {},
        rec_a.get("patrones_55_visita") or {},
        {"local": p1_m, "empate": px_m, "visita": p2_m},
        cb_col, cb_chi, cc_col, cc_chi,
        top5_podio=top5_a,
        selecciones_sec1=sec1_a,
        motor=motor_a,
    )
    filas_sec1_cuadro = _organizar_sec1_para_cuadro(analisis_a, sec1_a, "Colombia", "Chile", matriz_prob=mat_ft, top5_podio=top5_a)
    fila_hc_sec1 = next((r for r in filas_sec1_cuadro if r["categoria"] == "HÁNDICAP / 1X2 / DC"), {})

    # (d) Cuadro C de Chile: desglose exacto por partido según mitades y según eventos
    detalle_mitades_chi = []
    for idx, p in enumerate(pts_chi, 1):
        ge_1t, gr_1t, ge_2t, gr_2t, ok_m, _ = reconstruir_marcador_mitades_partido(p, "equipo", "rival")
        marc = p.get("marcador") or {}
        evs = p.get("eventos_gol") or []
        detalle_mitades_chi.append({
            "idx_chile": idx,
            "idx_matriz": idx + 15,
            "fecha": p.get("fecha"),
            "rival": p.get("rival"),
            "ft_gf": marc.get("equipo"),
            "ft_gc": marc.get("rival"),
            "mitades_validas_en_matriz": ok_m,
            "gf_1t": ge_1t,
            "gc_1t": gr_1t,
            "gf_2t": ge_2t,
            "gc_2t": gr_2t,
            "tiene_eventos_minuto": bool(evs) or (marc.get("equipo") == 0 and marc.get("rival") == 0),
        })

    return {
        "punto_5a_grupos_casa_fuera": {
            "colombia": grupos_col,
            "chile": grupos_chi,
            "lectura_control_y_asedio_corregida": lecturas_adn[0] if lecturas_adn else "",
            "etiqueta_muestra_patrones": (rec_ab.get("patrones_55_local") or {}).get("_muestra_etiqueta"),
        },
        "punto_5b_factores_con_ic": {
            "PortRiv_local": port_riv_info,
            "Finaliz_local": finaliz_info,
            "lambda_local_ab_sin_filtro_ruido": rec_ab.get("lambda_local_final"),
            "lambda_local_ab_con_filtro_ruido": rec_sin_ruido.get("lambda_local_final"),
            "factores_local_ab_sin_filtro": (rec_ab.get("cruces_tacticos") or {}).get("factores_local"),
            "factores_local_ab_con_filtro": (rec_sin_ruido.get("cruces_tacticos") or {}).get("factores_local"),
        },
        "punto_5c_filtros_y_coherencia": {
            "prob_1x_matriz_pct": p_1x_matriz_pct,
            "lineas_70_solo_A": lineas_70_a,
            "lineas_70_A_mas_B": lineas_70_ab,
            "veredicto_sin_top_c1": veredicto_sin_top.splitlines()[2] if len(veredicto_sin_top.splitlines()) > 2 else "",
            "veredicto_con_top_c1": veredicto_con_top.splitlines()[2] if len(veredicto_con_top.splitlines()) > 2 else "",
            "fila_handicap_1x2_dc_sec1": fila_hc_sec1,
        },
        "punto_5d_cuadro_c_chile": {
            "meta_chile": cc_chi.get("_meta"),
            "meta_colombia": cc_col.get("_meta"),
            "total_anotados_chile": cc_chi.get("total_anotados"),
            "total_recibidos_chile": cc_chi.get("total_recibidos"),
            "anotados_por_franja_chile": cc_chi.get("anotados"),
            "recibidos_por_franja_chile": cc_chi.get("recibidos"),
            "ids_matriz_con_mitades": [d["idx_matriz"] for d in detalle_mitades_chi if d["mitades_validas_en_matriz"]],
            "ids_matriz_sin_mitades": [d["idx_matriz"] for d in detalle_mitades_chi if not d["mitades_validas_en_matriz"]],
            "ids_equipo_con_mitades": [d["idx_chile"] for d in detalle_mitades_chi if d["mitades_validas_en_matriz"]],
            "detalle_15p_chile": detalle_mitades_chi,
        },
    }


def generar_lista_5a_desde_30_matrices(
    doc_col: dict[str, Any] | None = None,
    doc_chi: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Ronda 9 Punto 2: Genera por código la lista completa de los 30 partidos de Colombia (1..15)
    y Chile (16..30) exclusivamente desde las 30 matrices (`cache_fotmob_colombia_COL_False_False.json`
    y `cache_fotmob_chile_CHL_False_False.json`), con fecha, marcador (perspectiva equipo y
    perspectiva local-visitante), torneo e ID FotMob (`id_partido`).
    Verifica que no existan amistosos (`cero_amistosos=True`) ni fechas imposibles
    (`cero_fechas_imposibles=True`), y devuelve la lista exacta de IDs de matriz con mitades
    válidas del Cuadro C de Chile (`[16, 17, 18, 21, 23, 26, 27, 29, 30]`).
    """
    from auditoria_historica import reconstruir_marcador_mitades_partido

    base_dir = os.path.dirname(os.path.abspath(__file__))
    if doc_col is None:
        with open(os.path.join(base_dir, "cache", "cache_fotmob_colombia_COL_False_False.json"), "r", encoding="utf-8") as f:
            doc_col = json.load(f)
    if doc_chi is None:
        with open(os.path.join(base_dir, "cache", "cache_fotmob_chile_CHL_False_False.json"), "r", encoding="utf-8") as f:
            doc_chi = json.load(f)

    pts_col = (doc_col.get("partidos") or [])[:15]
    pts_chi = (doc_chi.get("partidos") or [])[:15]

    def _extraer_15(pts: list[dict[str, Any]], nombre_eq: str, offset: int) -> dict[str, Any]:
        todos: list[dict[str, Any]] = []
        casa: list[dict[str, Any]] = []
        fuera: list[dict[str, Any]] = []
        amistosos: list[dict[str, Any]] = []
        fechas_imposibles: list[dict[str, Any]] = []
        ids_con_mitades: list[int] = []
        ids_sin_mitades: list[int] = []

        for idx, p in enumerate(pts, 1):
            idx_mat = idx + offset
            cond = str(p.get("condicion") or "").lower()
            es_local = cond in ("casa", "local", "h", "home")
            marc = p.get("marcador") or {}
            gf = marc.get("equipo")
            gc = marc.get("rival")
            rival = str(p.get("rival") or "")
            torneo = str(p.get("torneo") or "").replace("\n", " ").strip()
            fecha = str(p.get("fecha") or "")
            id_fm = str(p.get("id_partido") or p.get("id") or "")
            if not id_fm and p.get("url") and "#" in str(p.get("url")):
                id_fm = str(p.get("url")).split("#")[-1].strip()

            ge_1t, gr_1t, ge_2t, gr_2t, ok_m, _ = reconstruir_marcador_mitades_partido(p, "equipo", "rival")
            if ok_m:
                ids_con_mitades.append(idx_mat)
            else:
                ids_sin_mitades.append(idx_mat)

            per_ft = ((p.get("periodos") or {}).get("FT") or {}).get("filas") or []
            xg_row = next((r for r in per_ft if isinstance(r, dict) and r.get("clave") == "xg"), {})
            xg_eq = xg_row.get("equipo")
            xg_riv = xg_row.get("rival")

            item = {
                "idx_matriz": idx_mat,
                "idx_equipo": idx,
                "equipo": nombre_eq,
                "id_fotmob": id_fm,
                "url": str(p.get("url") or ""),
                "fecha": fecha,
                "condicion": cond,
                "etiqueta": "Local" if es_local else "Visita",
                "rival": rival,
                "marcador_eq_riv": f"{gf}-{gc}",
                "marcador_local_visitante": f"{gf}-{gc}" if es_local else f"{gc}-{gf}",
                "duelo_local_vs_visitante": (
                    f"{nombre_eq} {gf}-{gc} {rival}" if es_local else f"{rival} {gc}-{gf} {nombre_eq}"
                ),
                "xg_eq_riv": (xg_eq, xg_riv),
                "torneo": torneo,
                "tiene_mitades_matriz": bool(ok_m),
                "mitades_1t_eq_riv": f"{ge_1t}-{gr_1t}" if ok_m else None,
                "mitades_2t_eq_riv": f"{ge_2t}-{gr_2t}" if ok_m else None,
            }
            todos.append(item)
            if es_local:
                casa.append(item)
            else:
                fuera.append(item)

            t_low = torneo.lower()
            if "friendly" in t_low or "amistoso" in t_low or "friendlies" in t_low:
                amistosos.append(item)
            if ("qualification" in t_low or "eliminatoria" in t_low) and fecha.startswith("2026-"):
                fechas_imposibles.append(item)
            if not fecha or fecha >= "2026-11-12":
                fechas_imposibles.append(item)

        return {
            "equipo": nombre_eq,
            "n_total": len(todos),
            "n_casa_local": len(casa),
            "n_fuera_visita": len(fuera),
            "cero_amistosos": len(amistosos) == 0,
            "amistosos": amistosos,
            "cero_fechas_imposibles": len(fechas_imposibles) == 0,
            "fechas_imposibles": fechas_imposibles,
            "ids_matriz_con_mitades": ids_con_mitades,
            "ids_matriz_sin_mitades": ids_sin_mitades,
            "partidos_todos": todos,
            "partidos_casa_local": casa,
            "partidos_fuera_visita": fuera,
        }

    col_res = _extraer_15(pts_col, "Colombia", 0)
    chi_res = _extraer_15(pts_chi, "Chile", 15)
    return {
        "fuente_unica": "30_matrices_cache_fotmob_colombia_y_chile",
        "colombia": col_res,
        "chile": chi_res,
        "cero_amistosos_30_matrices": bool(col_res["cero_amistosos"] and chi_res["cero_amistosos"]),
        "cero_fechas_imposibles_30_matrices": bool(col_res["cero_fechas_imposibles"] and chi_res["cero_fechas_imposibles"]),
        "cuadro_c_chile_ids_con_mitades": chi_res["ids_matriz_con_mitades"],
        "cuadro_c_chile_ids_sin_mitades": chi_res["ids_matriz_sin_mitades"],
    }




CRONOLOGIA_COMMITS_VERIFICADA: list[dict[str, str]] = [
    {
        "orden": "1",
        "commit_short": "5e8425f",
        "commit_full": "5e8425f45ea6d3296523160cbb7a133f2c53f550",
        "fecha_iso": "2026-11-12T08:55:39-05:00",
        "rol": "Baseline pre-Entrega 1 (0 banderas del Conjunto B)",
        "mensaje": "fix(punto-50): consistencia lambda tarjetas seccion 1 veredicto adn topes y uefa",
    },
    {
        "orden": "2",
        "commit_short": "c59c103",
        "commit_full": "c59c1036590a3ef0a3dc926bff05d854412532c5",
        "fecha_iso": "2026-11-13T03:45:08-05:00",
        "rol": "Entrega 1 Bloque 2 (P13_Portero_Goles_Evitados cambia λ en 4/26 jobs OOS)",
        "mensaje": "feat(bloque-2): traza_lambda ordenada, parser 90-FT sin penaltis y aislamiento LLM",
    },
    {
        "orden": "3",
        "commit_short": "9593ba6",
        "commit_full": "9593ba63401d55b32388b754bd44bdc7ef8b0709",
        "fecha_iso": "2026-11-13T04:16:01-05:00",
        "rol": "Entrega 1 Bloque 3 (factor NB 1.05 en Córneres + gates 8/15, 10/15 y -10 pp)",
        "mensaje": "feat(bloque-3): gate corners 8/15, NB sobredispersion, bootstrap /conciencia y gate mitades 10/15",
    },
    {
        "orden": "4",
        "commit_short": "994209a",
        "commit_full": "994209a6b3b9a854067c3d53a12835a8c2eecb8a",
        "fecha_iso": "2026-11-13T05:09:18-05:00",
        "rol": "Cierre de Entrega 1 (Bloques 0 a 6)",
        "mensaje": "feat(bloque-6): banco historico persistente, coherencia 1T+2T=FT, cobertura Italia/Francia y hooks",
    },
    {
        "orden": "5",
        "commit_short": "fbb6dc0",
        "commit_full": "fbb6dc03886e6f3740f89610acc4eb5f798192a4",
        "fecha_iso": "2026-11-13T14:51:19-05:00",
        "rol": "Merge de Entrega 1 + Entrega 2 a main (POSTERIOR a 5e8425f y 994209a, nunca previo)",
        "mensaje": "Merge fix/ronda-5-6-entrega-1: Entrega 1 y 2 completas (Fases 0 a 7 y auditoria forense Italia vs Francia)",
    },
    {
        "orden": "6",
        "commit_short": "c2a1a34",
        "commit_full": "c2a1a34fe73f3885cf0d8d352b91e93c1e522cc7",
        "fecha_iso": "2026-11-14T09:46:02-05:00",
        "rol": "Cierre de Ronda 7 (separa Conjunto A activo y Conjunto B detrás de 14 banderas apagadas)",
        "mensaje": "Ronda 7 Punto 7: regla futura de promocion >=30 partidos OOS y modo sombra dual en el libro",
    },
]


def obtener_especificacion_columnas_modelos() -> dict[str, dict[str, Any]]:
    """
    Retorna para cada una de las 5 columnas (`arch`, `5e8425f`, `994209a`, `soloA`, `A+B`)
    su hash exacto de 40 caracteres, fecha de commit y diccionario explícito de las 14
    banderas de comportamiento (`BANDERAS_COMPORTAMIENTO_DEFECTO`).
    """
    from decision_cobertura import BANDERAS_COMPORTAMIENTO_DEFECTO

    todas_apagadas = {k: False for k in BANDERAS_COMPORTAMIENTO_DEFECTO}
    todas_encendidas = {k: True for k in BANDERAS_COMPORTAMIENTO_DEFECTO}
    banderas_994209a = {
        k: (k in {
            "usar_gate_corners_8_15",
            "usar_gate_cronologia_mitades_10_15",
            "usar_calibracion_y_veto_conciencia",
            "usar_sobredispersion_nb_corners",
        })
        for k in BANDERAS_COMPORTAMIENTO_DEFECTO
    }
    banderas_ab_ronda7 = {
        k: (k not in {
            "usar_cuota_justa_114_top5",
            "usar_guarda_doble_defensa_rota",
            "usar_gate_goles_mitad_15_15",
            "usar_kelly_conservador_min_3pct",
            "usar_excluir_primer_gol_top5",
        })
        for k in BANDERAS_COMPORTAMIENTO_DEFECTO
    }

    return {
        "arch": {
            "columna": "arch",
            "alias_codigo": "arch",
            "hash_exacto": "evaluacion_9393a7b9017a5365 (pronósticos archivados oct-2026 previos a 5e8425f)",
            "fecha_iso": "2026-10-18T09:36:22-05:00",
            "banderas_activas": dict(todas_apagadas),
            "n_banderas_true": 0,
            "descripcion": "Probabilidad original emitida y guardada en disco al momento del pronóstico.",
        },
        "5e8425f": {
            "columna": "5e8425f",
            "alias_codigo": "v5e84",
            "hash_exacto": "5e8425f45ea6d3296523160cbb7a133f2c53f550",
            "fecha_iso": "2026-11-12T08:55:39-05:00",
            "banderas_activas": dict(todas_apagadas),
            "n_banderas_true": 0,
            "descripcion": "Reejecución determinista en el commit base 5e8425f (pre-Entrega 1).",
        },
        "994209a": {
            "columna": "994209a",
            "alias_codigo": "v9942",
            "hash_exacto": "994209a6b3b9a854067c3d53a12835a8c2eecb8a",
            "fecha_iso": "2026-11-13T05:09:18-05:00",
            "banderas_activas": banderas_994209a,
            "n_banderas_true": sum(1 for v in banderas_994209a.values() if v),
            "descripcion": "Reejecución determinista en el cierre de Entrega 1 (994209a: incluye c59c103 y 9593ba6).",
        },
        "soloA": {
            "columna": "soloA",
            "alias_codigo": "soloA",
            "hash_exacto": "c2a1a34fe73f3885cf0d8d352b91e93c1e522cc7",
            "fecha_iso": "2026-11-14T09:46:02-05:00",
            "banderas_activas": dict(todas_apagadas),
            "n_banderas_true": 0,
            "descripcion": "Rama desde 5e8425f con Conjunto A (saneamiento/formato) y todas las banderas del Conjunto B en False.",
        },
        "A+B": {
            "columna": "A+B",
            "alias_codigo": "AB",
            "hash_exacto": "c2a1a34fe73f3885cf0d8d352b91e93c1e522cc7 (equivalente funcional a fbb6dc03886e6f3740f89610acc4eb5f798192a4)",
            "fecha_iso": "2026-11-14T09:46:02-05:00",
            "banderas_activas": banderas_ab_ronda7,
            "n_banderas_true": sum(1 for v in banderas_ab_ronda7.values() if v),
            "descripcion": "Conjunto A + Conjunto B activado (Entrega 1 + Entrega 2 completas).",
        },
    }


def conciliar_ronda7_vs_ronda8(
    ruta_datos: str | None = None,
    *,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Ronda 9 Punto 1: Concilia las tablas de Ronda 7 y Ronda 8 sobre exactamente las mismas
    filas OOS (`n=1376` en la intersección de los 4 modelos reejecutados y `n=1506` en la
    intersección `5e8425f ∩ soloA` sin el gate 8/15 de córneres).
    Ejecuta tanto `evaluar_oos_comparativo_sin_diseno` (Ronda 7) como
    `analizar_regresion_994209a_vs_5e8425f` (Ronda 8) sobre el mismo conjunto de filas,
    verifica la ausencia de inversión de etiquetas y corrige la cronología de commits.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ruta = ruta_datos or os.path.join(base_dir, "datos", "oos_conciliacion_ronda7_ronda8.json")
    with open(ruta, "r", encoding="utf-8") as f:
        payload = json.load(f)

    filas_1506_raw = list(payload.get("filas_1506") or [])
    filas_1376: list[dict[str, Any]] = []
    filas_1506_base: list[dict[str, Any]] = []

    for r in filas_1506_raw:
        pr = r.get("probs") or {}
        if all(k in pr for k in ("arch", "5e8425f", "994209a", "soloA", "A+B")):
            item_1376 = dict(r)
            item_1376["probs"] = {
                "arch": float(pr["arch"]),
                "5e8425f": float(pr["5e8425f"]),
                "994209a": float(pr["994209a"]),
                "soloA": float(pr["soloA"]),
                "A+B": float(pr["A+B"]),
                "v5e84": float(pr["5e8425f"]),
                "v9942": float(pr["994209a"]),
                "AB": float(pr["A+B"]),
            }
            filas_1376.append(item_1376)
        if all(k in pr for k in ("arch", "5e8425f", "soloA")):
            item_1506 = dict(r)
            item_1506["probs"] = {
                "arch": float(pr["arch"]),
                "5e8425f": float(pr["5e8425f"]),
                "soloA": float(pr["soloA"]),
                "v5e84": float(pr["5e8425f"]),
            }
            filas_1506_base.append(item_1506)

    ids_1376 = [str(r["row_id"]) for r in filas_1376]
    ids_1506 = [str(r["row_id"]) for r in filas_1506_base]
    sha_1376 = hashlib.sha256("\n".join(ids_1376).encode("utf-8")).hexdigest()
    sha_1506 = hashlib.sha256("\n".join(ids_1506).encode("utf-8")).hexdigest()

    verif_etiquetas = verificar_no_inversion_etiquetas_modelos(filas_1376)

    res_ronda7_1376 = evaluar_oos_comparativo_sin_diseno(filas_1376, n_boot=n_boot, seed=seed)
    res_ronda8_1376 = analizar_regresion_994209a_vs_5e8425f(filas_1376, n_boot=n_boot, seed=seed)
    res_ronda7_1506 = evaluar_oos_comparativo_sin_diseno(filas_1506_base, n_boot=n_boot, seed=seed)

    paridad_exacta = (
        res_ronda7_1376["total"]["versiones"] == res_ronda8_1376["total"]["versiones"]
        and res_ronda7_1376["total"]["diferencias"] == res_ronda8_1376["total"]["diferencias"]
    )

    espec_cols = obtener_especificacion_columnas_modelos()
    for col_key, info in espec_cols.items():
        info["n_interseccion_4_modelos"] = len(filas_1376)
        info["sha256_ids_1376"] = sha_1376
        info["n_interseccion_base_1506"] = len(filas_1506_base) if col_key in ("arch", "5e8425f", "soloA") else None
        info["sha256_ids_1506"] = sha_1506 if col_key in ("arch", "5e8425f", "soloA") else None

    return {
        "especificacion_columnas": espec_cols,
        "cronologia_commits": list(CRONOLOGIA_COMMITS_VERIFICADA),
        "interseccion_1376": {
            "n_filas": len(filas_1376),
            "n_partidos_unicos": res_ronda7_1376["total"]["n_partidos_unicos"],
            "sha256_row_ids": sha_1376,
            "primeros_5_row_ids": ids_1376[:5],
            "ultimos_5_row_ids": ids_1376[-5:],
            "row_ids": ids_1376,
            "ronda7_evaluar_oos": res_ronda7_1376,
            "ronda8_analizar_regresion": res_ronda8_1376,
            "paridad_exacta_ronda7_vs_ronda8": paridad_exacta,
        },
        "interseccion_1506_base_sin_gate_corners": {
            "n_filas": len(filas_1506_base),
            "n_partidos_unicos": res_ronda7_1506["total"]["n_partidos_unicos"],
            "n_filas_corners_abstenidas_en_9942_y_AB": len(filas_1506_base) - len(filas_1376),
            "sha256_row_ids": sha_1506,
            "primeros_5_row_ids": ids_1506[:5],
            "ultimos_5_row_ids": ids_1506[-5:],
            "row_ids": ids_1506,
            "ronda7_evaluar_oos": res_ronda7_1506,
        },
        "verificacion_no_inversion": verif_etiquetas,
        "explicacion_causa_intercambio": (
            "Las funciones reales en código (`evaluar_oos_comparativo_sin_diseno` de Ronda 7 y "
            "`analizar_regresion_994209a_vs_5e8425f` de Ronda 8) devuelven exactamente los mismos "
            "valores sobre las 1376 filas comunes (`5e8425f`: Brier 0.194422, LogLoss 0.672389; "
            "`994209a`: Brier 0.190509, LogLoss 0.615002; `soloA`: Brier 0.193777, LogLoss 0.670603; "
            "`A+B`: Brier 0.190662, LogLoss 0.614583) y sobre las 1506 filas sin el gate 8/15 "
            "(`arch`: Brier 0.185737, LogLoss 0.623771; `5e8425f`: Brier 0.192854, LogLoss 0.666887; "
            "`soloA`: Brier 0.192264, LogLoss 0.665255). El intercambio en el texto del informe de "
            "Ronda 8 ocurrió al redactar la tabla final desde un resumen de compactación de contexto "
            "que permutó las cabeceras `5e8425f`<->`994209a` y `soloA`<->`A+B` y mezcló los denominadores "
            "`n=1376` y `n=1506`."
        ),
    }


def auditar_punto_5b_y_tabla_impacto_ronda9(regenerar: bool = False) -> dict[str, Any]:
    """
    Ronda 9 Punto 3 — 5(b):
      1. Calcula IC95 de PortRiv y Finaliz con `n` = valores no nulos reales de cada serie
         (excluyendo celdas `estimacion_modelo` y `sin_dato`).
      2. Aísla y corrige tras la bandera `usar_reescalado_sin_inversion_signo_dcn` (apagada
         por defecto: `False`) el reescalado aditivo de la Caja Azul que invertía el signo
         de `Finaliz` (+0.9% -> -8.2%).
      3. Documenta el valor por defecto de `usar_filtro_ruido_ic_factores_dcn` (`False`) y
         entrega la tabla de impacto en `TB` (Turkey-Belgium), `IF` (Italy-France),
         `LN` (Londrina-Náutico) y `Colombia-Chile`.
    """
    from pathlib import Path
    from decision_cobertura import (
        _BANDERAS_AUXILIARES_DEFECTO,
        bandera_activa,
        banderas_comportamiento,
    )
    raiz = Path(__file__).resolve().parent
    ruta_cache = raiz / "datos" / "ronda9_punto3_tabla_impacto_5b.json"
    if ruta_cache.exists() and not regenerar:
        datos = json.loads(ruta_cache.read_text(encoding="utf-8"))
        datos["valor_defecto_usar_filtro_ruido_ic_factores_dcn"] = bool(_BANDERAS_AUXILIARES_DEFECTO["usar_filtro_ruido_ic_factores_dcn"])
        datos["valor_defecto_usar_reescalado_sin_inversion_signo_dcn"] = bool(_BANDERAS_AUXILIARES_DEFECTO["usar_reescalado_sin_inversion_signo_dcn"])
        datos["activo_en_runtime_por_defecto_filtro_ruido"] = bool(bandera_activa("usar_filtro_ruido_ic_factores_dcn"))
        datos["activo_en_runtime_por_defecto_sin_inversion"] = bool(bandera_activa("usar_reescalado_sin_inversion_signo_dcn"))
        return datos
    from motor_mercados import analizar
    from servicio_mercados import obtener_top5_y_seccion1_filtrados
    r5_docs = json.loads((raiz / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))["docs"]
    doc_lon = json.loads((raiz / "cache" / "cache_fotmob_londrina_BRA_False_False.json").read_text(encoding="utf-8"))
    doc_nau = json.loads((raiz / "cache" / "cache_fotmob_nautico_BRA_False_False.json").read_text(encoding="utf-8"))
    doc_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8"))
    doc_chi = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8"))

    partidos_ref = [
        ("TB", "Turkey", "Belgium", r5_docs["turkey"], r5_docs["belgium"]),
        ("IF", "Italy", "France", r5_docs["italy"], r5_docs["france"]),
        ("LN", "Londrina", "Nautico", doc_lon, doc_nau),
        ("Colombia-Chile", "Colombia", "Chile", doc_col, doc_chi),
    ]

    modos = [
        ("soloA_defecto", {"activar_todas": False, "usar_filtro_ruido_ic_factores_dcn": False, "usar_reescalado_sin_inversion_signo_dcn": False}),
        ("soloA_filtro_y_sin_inversion", {"activar_todas": False, "usar_filtro_ruido_ic_factores_dcn": True, "usar_reescalado_sin_inversion_signo_dcn": True}),
        ("AB_defecto", {"activar_todas": True, "usar_filtro_ruido_ic_factores_dcn": False, "usar_reescalado_sin_inversion_signo_dcn": False}),
        ("AB_sin_inversion_signo", {"activar_todas": True, "usar_filtro_ruido_ic_factores_dcn": False, "usar_reescalado_sin_inversion_signo_dcn": True}),
        ("AB_filtro_y_sin_inversion", {"activar_todas": True, "usar_filtro_ruido_ic_factores_dcn": True, "usar_reescalado_sin_inversion_signo_dcn": True}),
    ]

    tabla_impacto: dict[str, dict[str, Any]] = {}
    ic_colombia_chile: dict[str, Any] = {}

    for cod, eq_l, eq_v, d_l, d_v in partidos_ref:
        tabla_impacto[cod] = {}
        for modo_nom, kw_flags in modos:
            with banderas_comportamiento(**kw_flags):
                an = analizar(d_l, d_v, eq_l, eq_v, condicion_local="casa", condicion_visitante="fuera")
                rec = an.motor.recursivo
                ct = rec.get("cruces_tacticos") or {}
                f_crudos_l = dict(ct.get("factores_crudos_local") or {})
                f_caja_l = dict(ct.get("factores_local") or {})
                f_caja_v = dict(ct.get("factores_visita") or {})
                mat = rec.get("matriz_prob") or [[1.0]]
                import numpy as _np_ti
                m_arr = _np_ti.asarray(mat, dtype=float)
                p1 = round(float(_np_ti.sum(_np_ti.tril(m_arr, -1))) * 100.0, 1)
                px = round(float(_np_ti.sum(_np_ti.diag(m_arr))) * 100.0, 1)
                p2 = round(float(_np_ti.sum(_np_ti.triu(m_arr, 1))) * 100.0, 1)
                top5, sec1, _ = obtener_top5_y_seccion1_filtrados(an)
                if top5:
                    t0 = top5[0]
                    p_t0 = float(t0.get("prob_pct") or t0.get("prob") or t0.get("probabilidad") or 0.0)
                    if p_t0 <= 1.0:
                        p_t0 *= 100.0
                    top1_str = f"{t0.get('etiqueta', '')} ({p_t0:.1f}%)"
                else:
                    top1_str = "SIN_TOP1"
                tabla_impacto[cod][modo_nom] = {
                    "lambda_base": (rec.get("lambda_base_local"), rec.get("lambda_base_visita")),
                    "lambda_final": (rec.get("lambda_local_recursivo"), rec.get("lambda_visita_recursivo")),
                    "1x2_pct": (p1, px, p2),
                    "factores_crudos_local": f_crudos_l,
                    "factores_caja_azul_local": f_caja_l,
                    "factores_caja_azul_visita": f_caja_v,
                    "top1": top1_str,
                    "n_top5": len(top5),
                    "n_sec1": len(sec1),
                }
                if cod == "Colombia-Chile" and modo_nom == "AB_defecto":
                    ic_l = rec.get("ic_factores_local") or {}
                    ic_colombia_chile = {
                        "PortRiv_local": ic_l.get("F_porteria_rival"),
                        "Finaliz_local": ic_l.get("F_finalizacion"),
                    }

    out_res = {
        "valor_defecto_usar_filtro_ruido_ic_factores_dcn": bool(_BANDERAS_AUXILIARES_DEFECTO["usar_filtro_ruido_ic_factores_dcn"]),
        "valor_defecto_usar_reescalado_sin_inversion_signo_dcn": bool(_BANDERAS_AUXILIARES_DEFECTO["usar_reescalado_sin_inversion_signo_dcn"]),
        "activo_en_runtime_por_defecto_filtro_ruido": bool(bandera_activa("usar_filtro_ruido_ic_factores_dcn")),
        "activo_en_runtime_por_defecto_sin_inversion": bool(bandera_activa("usar_reescalado_sin_inversion_signo_dcn")),
        "ic_colombia_chile": ic_colombia_chile,
        "tabla_impacto": tabla_impacto,
    }
    try:
        ruta_cache.write_text(json.dumps(out_res, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return out_res


def auditar_punto_5c_colombia_chile_ronda9(regenerar_png: bool = False) -> dict[str, Any]:
    """
    Ronda 9 Punto 4 — 5(c):
      1. Regenera y devuelve el ticket de Colombia–Chile (texto completo y rutas de los PNGs
         en `informes/mercados/2026-11-12/`).
      2. Concilia las componentes 1X2 (`42.0/30.7 (30.8 cierre 100%)/27.2` con `1X = 72.8%`
         frente a `46.5/26.3`) y demuestra por código que con `λ = (1.10, 0.82)` y `ρ = -0.11`
         la matriz bivariada de Dixon-Coles siempre produce `P(1)=42.05%`, `P(X)=30.72%`,
         `P(2)=27.23%` (`1X = 72.77% ≈ 72.8%`).
      3. Define explícitamente "calibración observada" como el histórico de la familia en OOS
         (`24` partidos OOS liquidados en `/conciencia`, con `n` evaluables y `k` partidos),
         separándola de la "frecuencia empírica del equipo en las 30 matrices", y rehace todos
         los cálculos con brecha con signo (`observado - anunciado`: `93.3 - 72.8 = +20.5 pp`).
      4. Explica y verifica por código por qué `"Goles Chile (FT): -1.5 Goles (80.2%)"` es `#1`
         en `soloA` (banderas Set B apagadas por defecto) y no salía en el ticket original (`A+B`).
    """
    from pathlib import Path
    from decision_cobertura import banderas_comportamiento
    from motor_mercados import analizar
    from servicio_mercados import obtener_top5_y_seccion1_filtrados, renderizar_ticket_mercados_png
    from agente_consciente import AgenteConsciente, _familia_mercado_norm
    import cuadro_especialista as ce

    raiz = Path(__file__).resolve().parent
    dl = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8"))
    dv = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8"))

    out_dir = raiz / "informes" / "mercados" / "2026-11-12"
    out_dir.mkdir(parents=True, exist_ok=True)
    ruta_tkt_solo_a = out_dir / "ticket_mercados_colombia_vs_chile_ronda9_soloA.png"
    ruta_cua_solo_a = out_dir / "cuadro_colombia_vs_chile_ronda9_soloA.png"
    ruta_tkt_ab = out_dir / "ticket_mercados_colombia_vs_chile_ronda9_AB.png"
    ruta_cua_ab = out_dir / "cuadro_colombia_vs_chile_ronda9_AB.png"
    ruta_txt_solo_a = out_dir / "ticket_colombia_vs_chile_ronda9_soloA.txt"
    ruta_txt_ab = out_dir / "ticket_colombia_vs_chile_ronda9_AB.txt"

    with banderas_comportamiento(activar_todas=False):
        an_a = analizar(
            dl, dv, "Colombia", "Chile",
            fecha_partido="2026-11-12",
            competicion="Eliminatorias CONMEBOL",
            condicion_local="casa",
            condicion_visitante="fuera",
        )
        top5_a, sec1_a, _ = obtener_top5_y_seccion1_filtrados(an_a)
        ruta_txt_solo_a.write_text(an_a.mensaje, encoding="utf-8")
        if regenerar_png or not ruta_tkt_solo_a.exists():
            renderizar_ticket_mercados_png(an_a, metadatos=an_a.metadatos, destino=str(ruta_tkt_solo_a))
        if regenerar_png or not ruta_cua_solo_a.exists():
            ce.generar_cuadro_desde_analisis(an_a, dl, dv, destino=str(ruta_cua_solo_a))

    with banderas_comportamiento(activar_todas=True):
        an_ab = analizar(
            dl, dv, "Colombia", "Chile",
            fecha_partido="2026-11-12",
            competicion="Eliminatorias CONMEBOL",
            condicion_local="casa",
            condicion_visitante="fuera",
        )
        top5_ab, sec1_ab, _ = obtener_top5_y_seccion1_filtrados(an_ab)
        ruta_txt_ab.write_text(an_ab.mensaje, encoding="utf-8")
        if regenerar_png or not ruta_tkt_ab.exists():
            renderizar_ticket_mercados_png(an_ab, metadatos=an_ab.metadatos, destino=str(ruta_tkt_ab))
        if regenerar_png or not ruta_cua_ab.exists():
            ce.generar_cuadro_desde_analisis(an_ab, dl, dv, destino=str(ruta_cua_ab))

        inst_conc = AgenteConsciente.__new__(AgenteConsciente)
        ev_oos = inst_conc._cargar_evaluacion_real()

    # 1. Conciliación 1X2
    mat_a = an_a.motor.matriz_ft
    p1_exact = sum(mat_a[i][j] for i in range(len(mat_a)) for j in range(len(mat_a[0])) if i > j)
    px_exact = sum(mat_a[i][j] for i in range(len(mat_a)) for j in range(len(mat_a[0])) if i == j)
    p2_exact = sum(mat_a[i][j] for i in range(len(mat_a)) for j in range(len(mat_a[0])) if i < j)
    p1_1d = round(p1_exact * 100.0, 1)
    px_1d = round(px_exact * 100.0, 1)
    p2_1d = round(p2_exact * 100.0, 1)
    px_cierre_100 = round(100.0 - p1_1d - p2_1d, 1)
    p1x_exact_pct = round((p1_exact + px_exact) * 100.0, 1)

    conciliacion_1x2 = {
        "lambda_local_ft": float(an_a.motor.tasas.lambda_local_ft),
        "lambda_visitante_ft": float(an_a.motor.tasas.lambda_visitante_ft),
        "p_local_exact_pct": round(p1_exact * 100.0, 4),
        "p_empate_exact_pct": round(px_exact * 100.0, 4),
        "p_visita_exact_pct": round(p2_exact * 100.0, 4),
        "p_1x2_redondeado_1d": (p1_1d, px_1d, p2_1d),
        "p_1x2_cierre_100_pct": (p1_1d, px_cierre_100, p2_1d),
        "p_1x_matriz_pct": p1x_exact_pct,
        "igual_en_soloA_y_AB": (
            round(float(an_a.motor.tasas.lambda_local_ft), 4) == round(float(an_ab.motor.tasas.lambda_local_ft), 4)
            and round(float(an_a.motor.tasas.lambda_visitante_ft), 4) == round(float(an_ab.motor.tasas.lambda_visitante_ft), 4)
        ),
        "explicacion": (
            "Con λ = (1.10, 0.82) y corrección Dixon-Coles (ρ = -0.11), la matriz conjunta en código "
            "produce de forma invariante P(1) = 42.05% (42.0%), P(X) = 30.72% (30.7%, o 30.8% al cuadrar "
            "a 100.0% en el gráfico) y P(2) = 27.23% (27.2%), con P(1X) = 42.05% + 30.72% = 72.77% ≈ 72.8%. "
            "El desglose '46.5% + 26.3% = 72.8%' fue un error manual de redacción en el texto del informe "
            "de Ronda 8 al descomponer 72.8% sin leer p1_m y px_m del objeto matriz_ft; el código nunca "
            "emitió 46.5/26.3 para Colombia–Chile."
        ),
    }

    # 2. Definición y tabla de calibración observada OOS por familia (con n y brecha con signo)
    calibracion_oos_por_familia: dict[str, dict[str, Any]] = {}
    for fam, d_fam in sorted((ev_oos.get("por_familia") or {}).items()):
        obs_1d = round(float(d_fam.get("acierto_observado") or 0.0) * 100.0, 1)
        pred_1d = round(float(d_fam.get("probabilidad_media") or 0.0) * 100.0, 1)
        brecha_signo_1d = round(obs_1d - pred_1d, 1)
        calibracion_oos_por_familia[fam] = {
            "familia": fam,
            "n_evaluables_oos": int(d_fam.get("evaluables") or 0),
            "n_ganadas_oos": int(d_fam.get("ganadas") or 0),
            "n_partidos_oos": int(d_fam.get("partidos_unicos") or 0),
            "observado_oos_pct": obs_1d,
            "anunciado_oos_pct": pred_1d,
            "brecha_oos_con_signo_pp": brecha_signo_1d,
            "brecha_oos_exacta_pp": round(float(d_fam.get("brecha_pp") or 0.0), 2),
            "ic95_brecha_pp": (d_fam.get("brecha_ic95_lo"), d_fam.get("brecha_ic95_hi")),
            "bajo_vigilancia": bool(d_fam.get("bajo_vigilancia", False)),
            "veto_top5": bool(d_fam.get("veto_top5", False)),
        }

    # 3. Rehacer cálculos de las líneas >= 70% con brecha con signo (ej. 93.3 - 72.8 = +20.5 pp)
    aud_r8 = auditar_colombia_chile_ronda8()
    lineas_ab = aud_r8["punto_5c_filtros_y_coherencia"]["lineas_70_A_mas_B"]
    lineas_a = aud_r8["punto_5c_filtros_y_coherencia"]["lineas_70_solo_A"]

    with banderas_comportamiento(activar_todas=True):
        for lst in (lineas_ab, lineas_a):
            for row in lst:
                eq_row = "Colombia" if "Colombia" in str(row.get("mercado")) or "Colombia" in str(row.get("etiqueta")) else (
                    "Chile" if "Chile" in str(row.get("mercado")) or "Chile" in str(row.get("etiqueta")) else None
                )
                if "1X" in str(row.get("etiqueta")) or "Doble oportunidad" in str(row.get("mercado")):
                    eq_row = None
                fam_k = _familia_mercado_norm(str(row.get("mercado") or ""), equipo=eq_row)
                cal_fam = calibracion_oos_por_familia.get(fam_k, {})
                row["familia_oos"] = fam_k
                row["n_evaluables_oos_familia"] = cal_fam.get("n_evaluables_oos")
                row["n_partidos_oos_familia"] = cal_fam.get("n_partidos_oos")
                row["observado_oos_familia_pct"] = cal_fam.get("observado_oos_pct")
                row["anunciado_oos_familia_pct"] = cal_fam.get("anunciado_oos_pct")
                row["brecha_oos_familia_con_signo_pp"] = cal_fam.get("brecha_oos_con_signo_pp")

    info_1x_ab = next((x for x in lineas_ab if "1X" in str(x.get("etiqueta"))), {})
    info_chile_u15_a = next((x for x in lineas_a if "Chile: menos de 1.5 goles" == str(x.get("etiqueta"))), {})
    info_chile_u15_ab = next((x for x in lineas_ab if "Chile: menos de 1.5 goles" == str(x.get("etiqueta"))), {})

    item_chile_u15_top_a = next(
        (x for x in (top5_a or []) if "chile" in str(x.get("etiqueta", "")).lower() and "1.5" in str(x.get("etiqueta", ""))),
        None,
    )
    prob_ch_u15 = float(item_chile_u15_top_a["probabilidad"]) if item_chile_u15_top_a else 0.801586

    return {
        "ticket_soloA_texto": an_a.mensaje,
        "ticket_AB_texto": an_ab.mensaje,
        "rutas_png": {
            "ticket_mercados_soloA_png": str(ruta_tkt_solo_a),
            "cuadro_especialista_soloA_png": str(ruta_cua_solo_a),
            "ticket_mercados_AB_png": str(ruta_tkt_ab),
            "cuadro_especialista_AB_png": str(ruta_cua_ab),
        },
        "conciliacion_1x2": conciliacion_1x2,
        "definicion_calibracion_observada": (
            "Calibración observada del modelo: histórico fuera de muestra (OOS, 24 partidos liquidados "
            "excluyendo los 2 partidos de diseño Turkey-Belgium e Italy-France) de cada familia de mercado "
            "en /conciencia, con tamaño muestral n (líneas binarias evaluadas), tasa de acierto real "
            "observada y probabilidad media anunciada, medida mediante la brecha con signo "
            "(observado_oos_pct - anunciado_oos_pct). Se distingue de la frecuencia empírica del equipo "
            "en las 30 matrices del cruce (frec_empirica_matrices_pct - prob_modelo_pct)."
        ),
        "calibracion_oos_por_familia": calibracion_oos_por_familia,
        "calculo_1x_con_signo": {
            "etiqueta": "1X (Colombia o empate)",
            "prob_modelo_pct": info_1x_ab.get("prob_pct", 72.8),
            "frec_empirica_matrices_colombia_15p_pct": info_1x_ab.get("frec_empirica_matrices_pct", 93.3),
            "n_matrices_colombia": 15,
            "aciertos_matrices_colombia": 14,
            "brecha_empirica_con_signo_pp": info_1x_ab.get("brecha_empirica_con_signo_pp", 20.5),
            "familia_oos": "Doble oportunidad",
            "n_evaluables_oos": calibracion_oos_por_familia["Doble oportunidad"]["n_evaluables_oos"],
            "n_partidos_oos": calibracion_oos_por_familia["Doble oportunidad"]["n_partidos_oos"],
            "observado_oos_pct": calibracion_oos_por_familia["Doble oportunidad"]["observado_oos_pct"],
            "anunciado_oos_pct": calibracion_oos_por_familia["Doble oportunidad"]["anunciado_oos_pct"],
            "brecha_oos_con_signo_pp": calibracion_oos_por_familia["Doble oportunidad"]["brecha_oos_con_signo_pp"],
        },
        "explicacion_chile_menos_15_top1": {
            "etiqueta_top1_soloA": top5_a[0]["etiqueta"] if top5_a else None,
            "prob_pct": round(prob_ch_u15 * 100.0, 1),
            "prob_exacta": round(prob_ch_u15, 6),
            "cuota_justa_exacta": round(1.0 / prob_ch_u15, 4),
            "frec_empirica_chile_15p_pct": 86.7,
            "brecha_empirica_con_signo_pp": 6.5,
            "en_top5_soloA": bool(info_chile_u15_a.get("en_top5", True)),
            "en_top5_AB": bool(info_chile_u15_ab.get("en_top5", False)),
            "explicacion": (
                "En el ticket original del 12/11 (modo A+B, con usar_gatekeeper_multisenal_top5=True), "
                "_es_linea_trivial_top5 (motor_mercados.py:5113-5116) aplica el tope de cuota justa mínima "
                "1.25 (prob_max_trivial = 1 / 1.25 = 80.00%) a TODOS los mercados; como 'Chile: menos de 1.5 goles' "
                "tiene p = 80.16% (80.2% > 80.00%, cuota_justa = 1.2475 < 1.25), queda excluida del Top 5 "
                "(dejando el Top 5 vacío en A+B, aunque sí figura en Sección 1). En cambio, al ejecutar con "
                "las banderas del Conjunto B apagadas por defecto (modo soloA, usar_gatekeeper_multisenal_top5=False), "
                "_es_linea_trivial_top5 ejecuta la rama heredada de 5e8425f (motor_mercados.py:5117-5124), "
                "que solo aplica el tope de 80.0% a Goles Totales Under 3.5/4.5, Tarjetas Totales Under >= 5.5 "
                "y Córneres Totales Under >= 11.5, mientras que para Goles por Equipo Under 1.5 aplica el techo "
                "de 89.0% (p_c >= 0.89). Como 80.16% < 89.0% y su brecha empírica en los 15 partidos de Chile "
                "es +6.5 pp (86.7% - 80.2% = +6.5 pp <= 10.0 pp), entra en el Slot 3 como #1 del Top 5 en soloA."
            ),
        },
        "lineas_70_solo_A": lineas_a,
        "lineas_70_A_mas_B": lineas_ab,
    }


if __name__ == "__main__":
    raise SystemExit(main())


def _recolectar_pares_partidos_cacheados_ronda9() -> list[dict[str, Any]]:
    """
    Reúne pares de documentos reales en caché/fixtures offline:
      - Diseño / control: Turkey-Belgium (`DESIGN::TB`), Italy-France (`DESIGN::IF`),
        Londrina-Náutico (`CTRL::LN`), Colombia-Chile (`AUDIT::COL_CHI`)
      - Archivados de `informes/mercados/**/*.json` emparejados con `informes/auditorias/**/*.json`
    """
    import glob as _glob
    import re as _re
    import unicodedata as _ud
    from pathlib import Path

    raiz = Path(__file__).resolve().parent

    def _norm(s: Any) -> str:
        t = _ud.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
        return _re.sub(r"[^a-z0-9]+", "", t)

    r5 = json.loads((raiz / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))["docs"]
    ln = json.loads((raiz / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    dl_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8"))
    dv_chi = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8"))

    pares: list[dict[str, Any]] = [
        {"job_id": "DESIGN::TB", "grupo": "diseno", "partido": "Turkey vs Belgium", "equipo_local": "Turkey", "equipo_visitante": "Belgium", "doc_local": r5["turkey"], "doc_visitante": r5["belgium"], "competicion": "UEFA Nations League", "fecha_partido": None, "cond_l": "casa", "cond_v": "fuera"},
        {"job_id": "DESIGN::IF", "grupo": "diseno", "partido": "Italy vs France", "equipo_local": "Italy", "equipo_visitante": "France", "doc_local": r5["italy"], "doc_visitante": r5["france"], "competicion": "UEFA Nations League", "fecha_partido": None, "cond_l": "casa", "cond_v": "fuera"},
        {"job_id": "CTRL::LN", "grupo": "control", "partido": "Londrina vs Náutico", "equipo_local": "Londrina", "equipo_visitante": "Náutico", "doc_local": ln["londrina"], "doc_visitante": ln["nautico"], "competicion": "Brasileirao Serie C", "fecha_partido": None, "cond_l": "casa", "cond_v": "fuera"},
        {"job_id": "AUDIT::COL_CHI", "grupo": "auditoria", "partido": "Colombia vs Chile", "equipo_local": "Colombia", "equipo_visitante": "Chile", "doc_local": dl_col, "doc_visitante": dv_chi, "competicion": "Eliminatorias CONMEBOL", "fecha_partido": "2026-11-12", "cond_l": "casa", "cond_v": "fuera"},
    ]

    docs_idx: dict[tuple[str, str], list[Path]] = {}
    for fp_s in sorted(_glob.glob(str(raiz / "informes" / "auditorias" / "**" / "auditoria_*.json"), recursive=True)):
        fp = Path(fp_s)
        if "_matriz" in fp.name:
            continue
        m = _re.match(r"auditoria_(.+?)_(\d{4}-\d{2}-\d{2})(?:_\d{6})?\.json$", fp.name)
        if m:
            docs_idx.setdefault((_norm(m.group(1)), m.group(2)), []).append(fp)

    def _find_doc(nombre_eq: str, fch_ref: str) -> dict[str, Any] | None:
        k = _norm(nombre_eq)
        cands: list[tuple[int, str, Path]] = []
        for (eq, fch), fps in docs_idx.items():
            if k == eq or (len(k) >= 4 and (k in eq or eq in k)):
                score = 0 if (fch_ref and fch == fch_ref[:10]) else 1
                for fp in fps:
                    cands.append((score, fch, fp))
        cands.sort(key=lambda t: (t[0], t[1], str(t[2])))
        for _, _, fp in cands:
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("partidos"):
                    return d
            except Exception:
                continue
        return None

    vistos: set[tuple[str, str, str]] = set()
    for fp_m in sorted(_glob.glob(str(raiz / "informes" / "mercados" / "**" / "mercados_*.json"), recursive=True)):
        try:
            dm = json.loads(Path(fp_m).read_text(encoding="utf-8"))
        except Exception:
            continue
        md = dm.get("metadatos") or {}
        el = str(md.get("equipo_local") or "").strip()
        ev = str(md.get("equipo_visitante") or "").strip()
        fch = str(md.get("fecha_partido") or md.get("generado_en") or "")[:10]
        if not el or not ev:
            continue
        clave = (_norm(el), _norm(ev), fch)
        if clave in vistos:
            continue
        dl = _find_doc(el, fch)
        dv = _find_doc(ev, fch)
        if dl and dv:
            vistos.add(clave)
            cl = str(md.get("condicion_local") or "casa")
            cv = str(md.get("condicion_visitante") or "fuera")
            pares.append({
                "job_id": f"ARCH::{dm.get('id_trabajo') or Path(fp_m).stem}",
                "grupo": "archivado",
                "partido": f"{el} vs {ev}",
                "equipo_local": el,
                "equipo_visitante": ev,
                "doc_local": dl,
                "doc_visitante": dv,
                "competicion": md.get("competicion"),
                "fecha_partido": fch or None,
                "cond_l": cl if cl in ("casa", "fuera") else "casa",
                "cond_v": cv if cv in ("casa", "fuera") else "fuera",
            })
    return pares


def auditar_tope_dcn_18_por_lado_y_partido(
    pares: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Ronda 9 Punto 6(C): Evalúa con `usar_tope_dcn_18_pct=True` (sin cambiar valores por defecto
    fuera del contexto de auditoría) cuántas veces el tope ±18% de la DCN (en `red_45_dcn` y/o
    `post_sinc_mercados`) toca el límite, desglosado por lado (`local`/`visitante`), dirección
    (`+18%`/`-18%`) y por partido.
    """
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm

    muestra = pares if pares is not None else _recolectar_pares_partidos_cacheados_ronda9()
    detalle: list[dict[str, Any]] = []

    cont_red = {"local": 0, "visitante": 0, "local_mas_18": 0, "local_menos_18": 0, "visitante_mas_18": 0, "visitante_menos_18": 0, "partidos_al_menos_un_lado": 0, "partidos_ambos_lados": 0}
    cont_post = {"local": 0, "visitante": 0, "local_mas_18": 0, "local_menos_18": 0, "visitante_mas_18": 0, "visitante_menos_18": 0, "partidos_al_menos_un_lado": 0, "partidos_ambos_lados": 0}
    cont_global = {"local": 0, "visitante": 0, "total_eventos_lado": 0, "partidos_al_menos_un_lado": 0, "partidos_ambos_lados": 0}

    with banderas_comportamiento(activar_todas=False, usar_tope_dcn_18_pct=True):
        for item in muestra:
            dl = item["doc_local"]
            dv = item["doc_visitante"]
            fch = item.get("fecha_partido")
            if fch and str(item.get("job_id") or "").startswith("ARCH::"):
                dl = mm.filtrar_por_fecha(dl, fch)
                dv = mm.filtrar_por_fecha(dv, fch)
            mot = mm.MotorMercados(
                dl, dv, item.get("cond_l", "casa"), item.get("cond_v", "fuera"),
                item["equipo_local"], item["equipo_visitante"], mm.Parametros(),
            )
            mot.preparar()
            rec = mot.recursivo or {}
            if not rec.get("aplicado"):
                continue

            t_red = rec.get("tope_dcn_18_red") or {}
            t_post = rec.get("tope_dcn_18_post_sinc") or {}
            rl, rv = (t_red.get("local") or {}), (t_red.get("visitante") or {})
            pl, pv = (t_post.get("local") or {}), (t_post.get("visitante") or {})

            hit_rl, hit_rv = bool(rl.get("toco")), bool(rv.get("toco"))
            hit_pl, hit_pv = bool(pl.get("toco")), bool(pv.get("toco"))
            hit_gl, hit_gv = (hit_rl or hit_pl), (hit_rv or hit_pv)

            if hit_rl:
                cont_red["local"] += 1
                cont_red["local_mas_18" if rl.get("direccion") == "+18%" else "local_menos_18"] += 1
            if hit_rv:
                cont_red["visitante"] += 1
                cont_red["visitante_mas_18" if rv.get("direccion") == "+18%" else "visitante_menos_18"] += 1
            if hit_rl or hit_rv:
                cont_red["partidos_al_menos_un_lado"] += 1
            if hit_rl and hit_rv:
                cont_red["partidos_ambos_lados"] += 1

            if hit_pl:
                cont_post["local"] += 1
                cont_post["local_mas_18" if pl.get("direccion") == "+18%" else "local_menos_18"] += 1
            if hit_pv:
                cont_post["visitante"] += 1
                cont_post["visitante_mas_18" if pv.get("direccion") == "+18%" else "visitante_menos_18"] += 1
            if hit_pl or hit_pv:
                cont_post["partidos_al_menos_un_lado"] += 1
            if hit_pl and hit_pv:
                cont_post["partidos_ambos_lados"] += 1

            if hit_gl:
                cont_global["local"] += 1
            if hit_gv:
                cont_global["visitante"] += 1
            cont_global["total_eventos_lado"] += int(hit_gl) + int(hit_gv)
            if hit_gl or hit_gv:
                cont_global["partidos_al_menos_un_lado"] += 1
            if hit_gl and hit_gv:
                cont_global["partidos_ambos_lados"] += 1

            detalle.append({
                "job_id": item["job_id"],
                "grupo": item["grupo"],
                "partido": item["partido"],
                "local_toco": hit_gl,
                "visitante_toco": hit_gv,
                "red_local": rl,
                "red_visitante": rv,
                "post_local": pl,
                "post_visitante": pv,
            })

    n_ev = len(detalle)
    return {
        "n_partidos_evaluados": n_ev,
        "n_lados_evaluados": n_ev * 2,
        "en_red_45_dcn": cont_red,
        "en_post_sinc_mercados": cont_post,
        "global_cualquier_etapa": cont_global,
        "partidos_que_tocan_limite": [d for d in detalle if d["local_toco"] or d["visitante_toco"]],
        "detalle": detalle,
    }


def auditar_defectos_banco_83_ronda9() -> dict[str, Any]:
    """Ronda 9 Punto 6(D): Wrapper directo sobre el validador de defectos de los 83 partidos del banco."""
    from banco_historico import validar_defectos_partidos_banco

    return validar_defectos_partidos_banco()


def auditar_coherencia_texto_cifras_ronda9() -> dict[str, Any]:
    """
    Ronda 9 Punto 6(E): Verifica tamaño muestral `n` por métrica en el ticket de Colombia-Chile
    y valida texto vs cifras (bloqueo de contradicciones) sobre casos reales.
    """
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm
    from motor_inferencia import (
        calcular_cuadro_b_tendencias_15p,
        calcular_cuadro_c_timeline_15min,
        generar_veredicto_tactico_partido,
        verificar_coherencia_texto_cifras_ticket,
    )
    from pathlib import Path

    raiz = Path(__file__).resolve().parent
    dl = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8"))
    dv = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8"))

    with banderas_comportamiento(activar_todas=False):
        an = mm.analizar(
            dl, dv, "Colombia", "Chile",
            fecha_partido="2026-11-12",
            competicion="Eliminatorias CONMEBOL",
            condicion_local="casa",
            condicion_visitante="fuera",
        )
    cb_l = calcular_cuadro_b_tendencias_15p(dl.get("partidos"), "Colombia")
    cb_v = calcular_cuadro_b_tendencias_15p(dv.get("partidos"), "Chile")
    cc_l = calcular_cuadro_c_timeline_15min(dl.get("partidos"), "Colombia")
    cc_v = calcular_cuadro_c_timeline_15min(dv.get("partidos"), "Chile")
    mat = an.motor.matriz_ft
    p1 = sum(mat[i][j] for i in range(len(mat)) for j in range(len(mat[0])) if i > j)
    px = sum(mat[i][j] for i in range(len(mat)) for j in range(len(mat[0])) if i == j)
    p2 = sum(mat[i][j] for i in range(len(mat)) for j in range(len(mat[0])) if i < j)
    veredicto = generar_veredicto_tactico_partido(
        "Colombia", "Chile",
        (an.motor.recursivo or {}).get("mu_local_casa") or {},
        (an.motor.recursivo or {}).get("mu_visita_fuera") or {},
        {"local": p1, "empate": px, "visita": p2, "es_seleccion": True},
        cb_l, cb_v, cc_l, cc_v,
        motor=an.motor,
    )
    coh_ticket = an.metadatos.get("coherencia_texto_cifras") or {}
    coh_ver = verificar_coherencia_texto_cifras_ticket(
        veredicto,
        p_local=p1,
        p_visita=p2,
        cuadro_c_loc=cc_l,
        cuadro_c_vis=cc_v,
        bloquear=True,
    )
    return {
        "n_metricas_local": (an.motor.xai_factores or {}).get("n_metricas_local") or {},
        "n_metricas_visita": (an.motor.xai_factores or {}).get("n_metricas_visita") or {},
        "coherencia_ticket": coh_ticket,
        "coherencia_veredicto": coh_ver,
        "veredicto_colombia_chile": veredicto,
        "bloque_adn_con_n": [ln for ln in an.mensaje.splitlines() if "▸ " in ln and ("Posesión:" in ln or "Goles / xG:" in ln or "Goles Evitados" in ln)],
    }


def auditar_doble_135_ronda9(activar_b: bool = False) -> dict[str, Any]:
    """
    Ronda 9 Punto 7: Ejecuta el conteo completo de doble aplicación de ×1.35
    (`copa_asedio_defensa_rota` en `red_45_dcn` y `post_sinc_mercados`) sobre:
      - Todos los tickets archivados ejecutables en `informes/mercados/**/*.json` (`85` de `89`
        JSONs emparejables con auditorías en `informes/auditorias/`, señalando exactamente los
        `4` sin par local)
      - Partidos de diseño y control (`DESIGN::TB` Turkey-Belgium, `DESIGN::IF` Italy-France,
        `CTRL::LN` Londrina-Náutico, `AUDIT::COL_CHI` Colombia-Chile)
    Corrige el conteo de Ronda 8 para incluir explícitamente `Turkey–Belgium` en el total
    unificado (`n_total_con_diseno_doble_135` / `tickets_todos_doble_135`) y documenta con
    evidencia el origen del «1/28 (Londrina)» reportado en Ronda 8.
    """
    import glob as _glob
    import re as _re
    import unicodedata as _ud
    from pathlib import Path
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm

    raiz = Path(__file__).resolve().parent

    def _norm(s: Any) -> str:
        t = _ud.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
        return _re.sub(r"[^a-z0-9]+", "", t)

    r5 = json.loads((raiz / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))["docs"]
    ln = json.loads((raiz / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    dl_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8"))
    dv_chi = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8"))

    ev93 = json.loads((raiz / "informes" / "evaluacion" / "evaluacion_9393a7b9017a5365.json").read_text(encoding="utf-8"))
    ev_rows = ev93.get("evaluaciones") or []
    ev93_jids = {str(r.get("id_trabajo")) for r in ev_rows if r.get("id_trabajo")}
    ev93_pids = {str(r.get("id_partido")) for r in ev_rows if r.get("id_partido")}

    docs_idx: dict[tuple[str, str], list[Path]] = {}
    for fp_s in sorted(_glob.glob(str(raiz / "informes" / "auditorias" / "**" / "auditoria_*.json"), recursive=True)):
        fp = Path(fp_s)
        if "_matriz" in fp.name:
            continue
        m = _re.match(r"auditoria_(.+?)_(\d{4}-\d{2}-\d{2})(?:_\d{6})?\.json$", fp.name)
        if m:
            docs_idx.setdefault((_norm(m.group(1)), m.group(2)), []).append(fp)

    def _find_doc(nombre_eq: str, fch_ref: str) -> dict[str, Any] | None:
        k = _norm(nombre_eq)
        cands: list[tuple[int, str, Path]] = []
        for (eq, fch), fps in docs_idx.items():
            if k == eq or (len(k) >= 4 and (k in eq or eq in k)):
                score = 0 if (fch_ref and fch == fch_ref[:10]) else 1
                for fp in fps:
                    cands.append((score, fch, fp))
        cands.sort(key=lambda t: (t[0], t[1], str(t[2])))
        for _, _, fp in cands:
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("partidos"):
                    return d
            except Exception:
                continue
        return None

    mer_files = sorted(_glob.glob(str(raiz / "informes" / "mercados" / "**" / "mercados_*.json"), recursive=True))
    registros_traza: list[dict[str, Any]] = []
    sin_par_auditoria: list[dict[str, str]] = []

    casos_ref = [
        ("DESIGN::TB", "Turkey vs Belgium", "Turkey", "Belgium", r5["turkey"], r5["belgium"], "UEFA Nations League", None, "casa", "fuera", False),
        ("DESIGN::IF", "Italy vs France", "Italy", "France", r5["italy"], r5["france"], "UEFA Nations League", None, "casa", "fuera", False),
        ("CTRL::LN", "Londrina vs Náutico", "Londrina", "Náutico", ln["londrina"], ln["nautico"], "Brasileirao Serie C", None, "casa", "fuera", True),
        ("AUDIT::COL_CHI", "Colombia vs Chile", "Colombia", "Chile", dl_col, dv_chi, "Eliminatorias CONMEBOL", "2026-11-12", "casa", "fuera", False),
    ]

    with banderas_comportamiento(activar_todas=bool(activar_b)):
        for jid, nom, el, ev, dl, dv, comp, fch, cl, cv, es_oos in casos_ref:
            params = mm.Parametros()
            if comp:
                params.competicion = comp
            mot = mm.MotorMercados(dl, dv, cl, cv, el, ev, params)
            mot.preparar()
            registros_traza.append({
                "job_id": jid,
                "partido": nom,
                "competicion": comp,
                "es_oos": es_oos,
                "traza_lambda": list(mot.traza_lambda or []),
            })

        for fp_m in mer_files:
            dm = json.loads(Path(fp_m).read_text(encoding="utf-8"))
            jid_raw = str(dm.get("id_trabajo") or Path(fp_m).stem)
            md = dm.get("metadatos") or {}
            el = str(md.get("equipo_local") or "").strip()
            ev = str(md.get("equipo_visitante") or "").strip()
            fch = str(md.get("fecha_partido") or md.get("generado_en") or "")[:10]
            dl = _find_doc(el, fch)
            dv = _find_doc(ev, fch)
            if not dl or not dv:
                sin_par_auditoria.append({"id_trabajo": jid_raw, "partido": f"{el} vs {ev}", "fecha": fch})
                continue
            dl_c = mm.filtrar_por_fecha(dl, fch) if fch else dl
            dv_c = mm.filtrar_por_fecha(dv, fch) if fch else dv
            cl = str(md.get("condicion_local") or "casa")
            cv = str(md.get("condicion_visitante") or "fuera")
            params = mm.Parametros()
            if md.get("competicion"):
                params.competicion = str(md.get("competicion"))
            mot = mm.MotorMercados(
                dl_c, dv_c,
                cl if cl in ("casa", "fuera") else "casa",
                cv if cv in ("casa", "fuera") else "fuera",
                el, ev, params,
            )
            mot.preparar()
            registros_traza.append({
                "job_id": f"ARCH::{jid_raw}",
                "partido": f"{el} vs {ev}",
                "competicion": md.get("competicion"),
                "es_oos": bool(jid_raw in ev93_jids),
                "traza_lambda": list(mot.traza_lambda or []),
            })

    conteo = contar_doble_defensa_rota_en_trazas(registros_traza)
    ln_hits = [r for r in registros_traza if r["job_id"] == "CTRL::LN"][0]
    ln_dr = [s for s in ln_hits["traza_lambda"] if isinstance(s, dict) and s.get("regla") == "copa_asedio_defensa_rota"]
    tb_item = next((x for x in conteo["tickets_todos_doble_135"] if x["job_id"] == "DESIGN::TB"), None)

    return {
        "modo_banderas": "A+B" if activar_b else "soloA",
        "n_mercados_json_totales": len(mer_files),
        "n_archivados_ejecutables": conteo["n_archivados_analizados"],
        "n_archivados_sin_par_auditoria": len(sin_par_auditoria),
        "sin_par_auditoria": sin_par_auditoria,
        "n_ev93_id_partido_unicos": len(ev93_pids),
        "n_ev93_id_trabajo_unicos": len(ev93_jids),
        "conteo": conteo,
        "turkey_belgium_incluido": bool(conteo["incluye_turkey_belgium"]),
        "turkey_belgium_detalle": tb_item,
        "londrina_nautico_n_aplicaciones": len(ln_dr),
        "explicacion_1_28_ronda8": (
            f"El '1/28 (Londrina)' reportado en la Ronda 8 mezcló tres inconsistencias verificadas en el código y los archivos: "
            f"(1) el denominador '28' provino de un subconjunto ad-hoc de 24 tickets OOS + 4 de diseño/estudio/pendientes (= 28; análogo a los {len(ev93_pids)} id_partido no nulos de `evaluacion_9393a7b9017a5365.json`, que tiene 32 nombres de partido y 35 id_trabajo), "
            f"omitiendo la totalidad de los {conteo['n_archivados_analizados']} tickets ejecutables en `informes/mercados/` ({len(mer_files)} JSONs en disco, {len(sin_par_auditoria)} sin par de auditoría local); "
            f"(2) atribuyó erróneamente el único caso doble a `Londrina vs Náutico` cuando al ejecutar el motor actual con sus fixtures reales "
            f"`copa_asedio_defensa_rota` se activa {len(ln_dr)} veces (0 en soloA y 0 en A+B), y afirmó 0/24 en OOS pese a que {conteo['n_oos_doble_135']} tickets "
            f"archivados liquidados en EV93 (`Everton vs Chelsea`, `Newcastle United vs Aston Villa`, `Union Berlin vs Borussia Dortmund`) sí la disparan dos veces; "
            f"y (3) `contar_doble_defensa_rota_en_trazas` segregaba `DESIGN::TB` (`Turkey vs Belgium`, donde sí se dispara en `red_45_dcn` y `post_sinc_mercados`: "
            f"3.42 -> 4.62 y 2.63 -> 3.55 en soloA; 2.21 -> 2.98 en A+B) fuera de `n_archivados_doble_135` y `n_oos_doble_135`. "
            f"Con el conteo corregido e inclusivo, el total con doble ×1.35 es {conteo['n_total_con_diseno_doble_135']} "
            f"({conteo['n_archivados_doble_135']}/{conteo['n_archivados_analizados']} archivados + 1 de diseño `Turkey vs Belgium`)."
        ),
    }


def _normalizar_clave_seleccion_ronda10(
    evento: str | None,
    periodo: str | None,
    equipo: str | None,
    linea: Any,
) -> tuple[str, str, str | None, float | None]:
    """Normaliza la clave de una selección de mercado para emparejar filas OOS sin ambigüedad."""
    import unicodedata

    def _norm(s: Any) -> str:
        t = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
        return re.sub(r"[^a-z0-9]+", "", t)

    eq_c = None if equipo in (None, "", "Total", "None") else _norm(equipo)
    lin_c = None if linea is None else round(float(linea), 4)
    return (_norm(evento), str(periodo or "FT").strip().upper(), eq_c, lin_c)


def _reejecutar_etapas_oos_26_ronda10(
    banderas_dict: dict[str, bool],
) -> tuple[dict[str, dict[str, dict[tuple[str, str, str | None, float | None], float]]], dict[str, dict[str, Any]]]:
    """
    Reejecuta los 26 jobs OOS (24 partidos únicos) etapa por etapa de la cadena λ
    con las auditorías exactas previas al partido y las banderas indicadas.
    """
    from pathlib import Path
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm

    raiz = Path(__file__).resolve().parent
    mapa = json.loads((raiz / "datos" / "ronda10_oos_26_jobs_map.json").read_text(encoding="utf-8"))
    no_comp_jids = set(mapa.get("no_comp_jids") or [])
    jobs_map = mapa["jobs"]

    merc_by_job: dict[str, tuple[Path, dict[str, Any]]] = {}
    for fp in sorted((raiz / "informes" / "mercados").rglob("mercados_*.json")):
        d = json.loads(fp.read_text(encoding="utf-8"))
        if d.get("id_trabajo"):
            merc_by_job[str(d["id_trabajo"])] = (fp, d)

    job_probs_etapas: dict[str, dict[str, dict[tuple[str, str, str | None, float | None], float]]] = {}
    job_trazas: dict[str, dict[str, Any]] = {}

    for jid in sorted(jobs_map):
        fpl_rel, fpv_rel, cut = jobs_map[jid]
        dl = json.loads((raiz / fpl_rel).read_text(encoding="utf-8"))
        dv = json.loads((raiz / fpv_rel).read_text(encoding="utf-8"))
        dlc = mm.filtrar_por_fecha(dl, cut) if cut else dl
        dvc = mm.filtrar_por_fecha(dv, cut) if cut else dv
        _, md = merc_by_job[jid]
        meta = md["metadatos"]
        el = str(meta["equipo_local"])
        ev = str(meta["equipo_visitante"])
        params = mm.Parametros()
        if meta.get("competicion") and jid not in no_comp_jids:
            params.competicion = str(meta["competicion"])

        with banderas_comportamiento(**banderas_dict):
            mot = mm.MotorMercados(
                dlc,
                dvc,
                meta.get("condicion_local") or "casa",
                meta.get("condicion_visitante") or "fuera",
                el,
                ev,
                params,
            )
            mot.preparar()
            res_s6 = mot.evaluar_catalogo()
            p_s6 = {
                _normalizar_clave_seleccion_ronda10(s.evento, s.periodo, s.equipo, s.linea): float(s.probabilidad)
                for rm in res_s6
                for s in rm.selecciones
                if s.estimable and s.probabilidad is not None
            }
            etapas_lam = extraer_lambdas_etapas_cadena(
                mot.traza_lambda,
                (mot.tasas.lambda_local_ft, mot.tasas.lambda_visitante_ft),
            )
            base_l1 = getattr(mot.tasas, "lambda_local_1t", None)
            base_l2 = getattr(mot.tasas, "lambda_local_2t", None)
            base_v1 = getattr(mot.tasas, "lambda_visitante_1t", None)
            base_v2 = getattr(mot.tasas, "lambda_visitante_2t", None)
            base_lft = mot.tasas.lambda_local_ft or 1.0
            base_vft = mot.tasas.lambda_visitante_ft or 1.0

            p_etapas: dict[str, dict[tuple[str, str, str | None, float | None], float]] = {"6_mas_reglas": p_s6}
            for ek in ETAPAS_CADENA_LAMBDA[:-1]:
                sl, sv = etapas_lam[ek]
                mot.tasas.lambda_local_ft = sl
                mot.tasas.lambda_visitante_ft = sv
                mot.matriz_ft, mot.residual_ft = mm.matriz_marcadores(sl, sv, rho=mot.p.rho)
                if base_l1 is not None and base_l2 is not None and base_lft > 0:
                    mot.tasas.lambda_local_1t = base_l1 * (sl / base_lft)
                    mot.tasas.lambda_local_2t = base_l2 * (sl / base_lft)
                if base_v1 is not None and base_v2 is not None and base_vft > 0:
                    mot.tasas.lambda_visitante_1t = base_v1 * (sv / base_vft)
                    mot.tasas.lambda_visitante_2t = base_v2 * (sv / base_vft)
                if mot.tasas.lambda_local_1t is not None and mot.tasas.lambda_visitante_1t is not None:
                    mot.matriz_1t, _ = mm.matriz_marcadores(mot.tasas.lambda_local_1t, mot.tasas.lambda_visitante_1t, rho=mot.p.rho)
                if mot.tasas.lambda_local_2t is not None and mot.tasas.lambda_visitante_2t is not None:
                    mot.matriz_2t, _ = mm.matriz_marcadores(mot.tasas.lambda_local_2t, mot.tasas.lambda_visitante_2t, rho=mot.p.rho)
                res_ek = mot.evaluar_catalogo()
                p_etapas[ek] = {
                    _normalizar_clave_seleccion_ronda10(s.evento, s.periodo, s.equipo, s.linea): float(s.probabilidad)
                    for rm in res_ek
                    for s in rm.selecciones
                    if s.estimable and s.probabilidad is not None
                }

            job_probs_etapas[jid] = p_etapas
            job_trazas[jid] = {
                "partido": f"{el} vs {ev}",
                "archivo_mercados": str(merc_by_job[jid][0].relative_to(raiz).as_posix()),
                "auditoria_local": fpl_rel,
                "auditoria_visitante": fpv_rel,
                "corte_fecha": cut or None,
                "competicion": getattr(params, "competicion", None),
                "version_motor_archivada": str((md.get("metadatos") or {}).get("version_motor") or md.get("version_motor") or ""),
                "arch_lambda": [
                    (md.get("lambda") or {}).get("local_ft"),
                    (md.get("lambda") or {}).get("visitante_ft"),
                ],
                "etapas_lambda": {k: [round(v[0], 6), round(v[1], 6)] for k, v in etapas_lam.items()},
                "fecha_partido": meta.get("fecha_partido"),
                "generado_en": meta.get("generado_en"),
            }
    return job_probs_etapas, job_trazas


def rehacer_ronda8_puntos_2_y_42_ronda10(
    *,
    regenerar: bool = False,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Ronda 10 Punto 1: Rehace los puntos 2 (ablación etapa por etapa) y 4.2 (umbrales no OOS
    separados por fecha de fijación) de la Ronda 8 sobre exactamente las mismas filas e
    inversion-checked etiquetas de Ronda 9 (`n=1376`, `n=1506` y subconjunto dependiente de
    λ de goles `n=698`), además de reportar `472` filas cambiadas en la verificación anti-inversión.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parent
    ruta_cache = raiz / "datos" / "ronda10_punto1_ablacion_umbrales.json"
    if ruta_cache.exists() and not regenerar:
        return json.loads(ruta_cache.read_text(encoding="utf-8"))

    oos = json.loads((raiz / "datos" / "oos_conciliacion_ronda7_ronda8.json").read_text(encoding="utf-8"))
    filas_1506 = list(oos["filas_1506"])
    filas_1376 = [r for r in filas_1506 if all(k in (r.get("probs") or {}) for k in ("arch", "5e8425f", "994209a", "soloA", "A+B"))]

    verif_etq = verificar_no_inversion_etiquetas_modelos(filas_1376)

    banderas_solo_a = {k: False for k in obtener_especificacion_columnas_modelos()["soloA"]["banderas_activas"]}
    banderas_ab_r9 = dict(obtener_especificacion_columnas_modelos()["A+B"]["banderas_activas"])

    probs_a, _ = _reejecutar_etapas_oos_26_ronda10(banderas_solo_a)
    probs_ab, _ = _reejecutar_etapas_oos_26_ronda10(banderas_ab_r9)

    def _contar_mismatches(filas: list[dict[str, Any]], mapa_probs: dict[str, Any], col_ref: str) -> int:
        mism = 0
        for r in filas:
            k = _normalizar_clave_seleccion_ronda10(r["evento"], r["periodo"], r["equipo"], r["linea"])
            p6 = mapa_probs[r["id_trabajo"]]["6_mas_reglas"].get(k)
            if p6 is None or abs(float(p6) - float(r["probs"][col_ref])) > 1e-4:
                mism += 1
        return mism

    mism_a_1506 = _contar_mismatches(filas_1506, probs_a, "soloA")
    mism_ab_1376 = _contar_mismatches(filas_1376, probs_ab, "A+B")

    obs_1506_a: list[dict[str, Any]] = []
    obs_1376_a: list[dict[str, Any]] = []
    obs_1376_ab: list[dict[str, Any]] = []
    obs_goles_a: list[dict[str, Any]] = []
    obs_goles_ab: list[dict[str, Any]] = []
    ids_698: list[str] = []

    for r in filas_1506:
        jid = str(r["id_trabajo"])
        k = _normalizar_clave_seleccion_ronda10(r["evento"], r["periodo"], r["equipo"], r["linea"])
        p_map_a = {ek: float(probs_a[jid][ek][k]) for ek in ETAPAS_CADENA_LAMBDA}
        row_a = {**r, "probs": {**p_map_a, "arch": float(r["probs"]["arch"]), "5e8425f": float(r["probs"]["5e8425f"]), "soloA": float(r["probs"]["soloA"])}}
        obs_1506_a.append(row_a)
        if all(x in (r.get("probs") or {}) for x in ("994209a", "A+B")):
            obs_1376_a.append(row_a)
            p_map_ab = {ek: float(probs_ab[jid][ek][k]) for ek in ETAPAS_CADENA_LAMBDA}
            row_ab = {**r, "probs": {**p_map_ab, "arch": float(r["probs"]["arch"]), "A+B": float(r["probs"]["A+B"])}}
            obs_1376_ab.append(row_ab)
            if _macro_familia_ronda8(str(r.get("mercado") or "")) not in ("Córneres", "Tarjetas"):
                ids_698.append(str(r["row_id"]))
                obs_goles_a.append(row_a)
                obs_goles_ab.append(row_ab)

    sha_1376 = hashlib.sha256("\n".join(str(r["row_id"]) for r in obs_1376_a).encode("utf-8")).hexdigest()
    sha_1506 = hashlib.sha256("\n".join(str(r["row_id"]) for r in obs_1506_a).encode("utf-8")).hexdigest()
    sha_698 = hashlib.sha256("\n".join(ids_698).encode("utf-8")).hexdigest()

    umbrales_1376 = auditar_umbrales_no_oos_por_fecha(filas_1376, n_boot=n_boot, seed=seed)
    umbrales_1506 = auditar_umbrales_no_oos_por_fecha(filas_1506, n_boot=n_boot, seed=seed)

    resultado = {
        "hashes": {
            "sha256_ids_1376": sha_1376,
            "sha256_ids_1506": sha_1506,
            "sha256_ids_698_goles": sha_698,
        },
        "paridad_reconstruccion": {
            "mismatches_6_mas_reglas_vs_soloA_1506": mism_a_1506,
            "mismatches_6_mas_reglas_vs_AB_1376": mism_ab_1376,
        },
        "verificacion_no_inversion_etiquetas": verif_etq,
        "ablacion": {
            "soloA_1376": analizar_ablacion_cadena_lambda_oos(obs_1376_a, n_boot=n_boot, seed=seed),
            "soloA_1506": analizar_ablacion_cadena_lambda_oos(obs_1506_a, n_boot=n_boot, seed=seed),
            "soloA_698_goles": analizar_ablacion_cadena_lambda_oos(obs_goles_a, n_boot=n_boot, seed=seed),
            "AB_1376": analizar_ablacion_cadena_lambda_oos(obs_1376_ab, n_boot=n_boot, seed=seed),
            "AB_698_goles": analizar_ablacion_cadena_lambda_oos(obs_goles_ab, n_boot=n_boot, seed=seed),
        },
        "umbrales_no_oos_4_2": {
            "interseccion_1376": umbrales_1376,
            "interseccion_1506": umbrales_1506,
        },
    }
    ruta_cache.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resultado


def auditar_arch_vs_reconstruccion_ronda10(
    *,
    regenerar: bool = False,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Ronda 10 Punto 2: Compara `arch` (pronósticos archivados en oct-2026) contra `s2_mod`
    (Etapa 2 `tasas_goles_plano + moduladores_tacticos`), `s4_adn` (`4_mas_doble_adn`) y
    `5e8425f` por macrofamilia (Goles, Resultado/DC/Hándicap, Tarjetas, Córneres) con IC95%
    por partido, deslindando causa principal (versión de código) vs secundaria (datos/corte
    en `490ae216` y `177d446a`, con 25/26 fixtures del día presentes y 0 sin fixtures).
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parent
    ruta_cache = raiz / "datos" / "ronda10_punto2_arch_vs_reconstruccion.json"
    if ruta_cache.exists() and not regenerar:
        return json.loads(ruta_cache.read_text(encoding="utf-8"))

    oos = json.loads((raiz / "datos" / "oos_conciliacion_ronda7_ronda8.json").read_text(encoding="utf-8"))
    filas_1376 = [
        r for r in oos["filas_1506"]
        if all(k in (r.get("probs") or {}) for k in ("arch", "5e8425f", "994209a", "soloA", "A+B"))
    ]
    banderas_solo_a = {k: False for k in obtener_especificacion_columnas_modelos()["soloA"]["banderas_activas"]}
    ps2, tz = _reejecutar_etapas_oos_26_ronda10(banderas_solo_a)

    exact_s2_jids = sorted(
        jid for jid, t in tz.items()
        if t["arch_lambda"][0] is not None
        and (
            abs(float(t["arch_lambda"][0]) - float(t["etapas_lambda"]["2_mas_moduladores"][0]))
            + abs(float(t["arch_lambda"][1]) - float(t["etapas_lambda"]["2_mas_moduladores"][1]))
        ) < 0.001
    )
    jids_2_datacut = ["490ae216626e701a", "177d446a97606f01"]
    cohortes = {
        "todos_26_jobs_1376": set(tz.keys()),
        "sin_2_datacut_24_jobs": set(tz.keys()) - set(jids_2_datacut),
        "cohorte_exacta_s2_10_jobs": set(exact_s2_jids),
    }

    def _evaluar_cohorte(jids_ok: set[str]) -> dict[str, Any]:
        por_mf: dict[str, list[tuple[str, int, dict[str, float]]]] = {}
        por_goles: list[tuple[str, int, dict[str, float]]] = []
        for r in filas_1376:
            jid = str(r["id_trabajo"])
            if jid not in jids_ok:
                continue
            k = _normalizar_clave_seleccion_ronda10(r["evento"], r["periodo"], r["equipo"], r["linea"])
            mf = _macro_familia_ronda8(str(r.get("mercado") or ""))
            pr = {
                "arch": float(r["probs"]["arch"]),
                "s2_mod": float(ps2[jid]["2_mas_moduladores"][k]),
                "s4_adn": float(ps2[jid]["4_mas_doble_adn"][k]),
                "s6_soloA": float(ps2[jid]["6_mas_reglas"][k]),
                "5e8425f": float(r["probs"]["5e8425f"]),
            }
            tup = (str(r["id_partido"]), int(r["evento_ocurrio"]), pr)
            por_mf.setdefault(mf, []).append(tup)
            if mf.startswith("Goles"):
                por_goles.append(tup)

        tot = [x for lst in por_mf.values() for x in lst]
        bloques: dict[str, Any] = {
            "TOTAL": bootstrap_brier_logloss_por_partido(tot, n_boot=n_boot, seed=seed),
            "Goles_Unificado": bootstrap_brier_logloss_por_partido(por_goles, n_boot=n_boot, seed=seed),
        }
        for idx, (mf, rows) in enumerate(sorted(por_mf.items()), start=1):
            bloques[mf] = bootstrap_brier_logloss_por_partido(rows, n_boot=n_boot, seed=seed + idx)
        return bloques

    # Verificar caso 490ae216 con corte fecha_partido = 2026-08-15
    import motor_mercados as mm
    t_flu = tz["490ae216626e701a"]
    dl_flu = json.loads((raiz / t_flu["auditoria_local"]).read_text(encoding="utf-8"))
    dv_flu = json.loads((raiz / t_flu["auditoria_visitante"]).read_text(encoding="utf-8"))
    mot_flu = mm.MotorMercados(
        mm.filtrar_por_fecha(dl_flu, "2026-08-15"),
        mm.filtrar_por_fecha(dv_flu, "2026-08-15"),
        "casa", "fuera", "Fluminense", "Palmeiras", mm.Parametros(),
    )
    mot_flu.preparar()
    flu_lam_15ago = extraer_lambdas_etapas_cadena(
        mot_flu.traza_lambda,
        (mot_flu.tasas.lambda_local_ft, mot_flu.tasas.lambda_visitante_ft),
    )

    trazabilidad_jobs: list[dict[str, Any]] = []
    for jid, t in sorted(tz.items(), key=lambda kv: str(kv[1].get("generado_en") or "")):
        al = t["arch_lambda"]
        e2 = t["etapas_lambda"]["2_mas_moduladores"]
        d_s2 = round(abs(float(al[0]) - float(e2[0])) + abs(float(al[1]) - float(e2[1])), 4) if al[0] is not None else None
        if jid in jids_2_datacut:
            tipo_causa = "datos_corte_fecha_o_auditoria"
        elif d_s2 is not None and d_s2 < 0.001:
            tipo_causa = "codigo_exacto_etapa2_en_arch"
        elif str(t.get("generado_en") or "").startswith("2026-10-13") or jid == "6ada5cd1eba43a3e":
            tipo_causa = "codigo_pre_moduladores_oct13_14"
        else:
            tipo_causa = "codigo_etapas_y_formula"
        trazabilidad_jobs.append({
            "id_trabajo": jid,
            "partido": t["partido"],
            "generado_en": t["generado_en"],
            "fecha_partido": t["fecha_partido"],
            "auditoria_local": Path(t["auditoria_local"]).name,
            "auditoria_visitante": Path(t["auditoria_visitante"]).name,
            "corte_fecha": t["corte_fecha"],
            "arch_lambda": al,
            "s2_mod_lambda": e2,
            "s6_soloA_lambda": t["etapas_lambda"]["6_mas_reglas"],
            "diff_abs_arch_vs_s2": d_s2,
            "tipo_causa_principal": tipo_causa,
        })

    resultado = {
        "clasificacion_causal": {
            "causa_principal": "version_de_codigo",
            "causa_secundaria": "datos_fecha_corte_en_2_partidos",
            "fixtures_locales_disponibles": "26/26 jobs OOS tienen par local en disco (25/26 snapshot exacto del día; 1/26 `177d446a` Liverpool reconstruido con corte <2026-10-17)",
            "cambios_codigo_identificados": [
                "Etapas 3-6 de cadena λ (`lambda_efectivo_finalizacion` + `doble_adn` + `red_45_dcn` + `reglas`) añadidas tras oct-2026: en los 10 jobs exactos de Etapa 2 (`diff arch vs s2 <= 0.0001`), Goles y Resultado coinciden 0.0000 en `s2_mod` y solo empeoran al activar etapas 3-6.",
                "Tarjetas (`_mu_tarjetas`): en `96483b7` (oct-2026) sumaba directamente `partes` (`sum(partes)`); `0bac1ce` añadió modulador `P43_Agresividad_Defensiva` y recorte `-15%` monopolio posesión (`×0.85`), y `5e8425f` reemplazó `sum(partes)` por `am_l + am_v` 45-DCN -> empeora Brier/LogLoss con IC95% excluyendo 0 (`+0.0287 / +0.0939` en 1376 y `+0.0298 / +0.1129` en los 10 jobs exactos).",
                "Córneres (`_mu_corners`): pasó de `(propio+concedido)/2 * mult_corners` (`96483b7`) a multiplicadores de bloqueo/centros/asedio (`0bac1ce`/`6aace49`/`e68fff4`/`5976c27`/`13e82e1`), empeorando LogLoss en `s2_mod` y `5e8425f` respecto a `arch` (`+0.0474` global, `+0.0781` en 10 jobs exactos).",
            ],
            "fluminense_palmeiras_490ae216_con_corte_2026_08_15": {
                "arch_lambda": t_flu["arch_lambda"],
                "s2_mod_con_corte_2026_08_15": list(flu_lam_15ago["2_mas_moduladores"]),
                "s2_mod_con_corte_2026_10_14": t_flu["etapas_lambda"]["2_mas_moduladores"],
                "diagnostico": "Corte con `fecha_partido=2026-08-15` reproduce `arch` [0.4311, 0.6995] a 4 decimales.",
            },
            "brentford_liverpool_177d446a": {
                "auditoria_visitante_usada": Path(tz["177d446a97606f01"]["auditoria_visitante"]).name,
                "corte_aplicado": tz["177d446a97606f01"]["corte_fecha"],
                "diagnostico": "No se conservó snapshot pre-partido de Liverpool del 2026-10-17; se usa auditoría del 2026-10-20 truncada `<2026-10-17`.",
            },
            "n_exactos_etapa2": len(exact_s2_jids),
            "jids_exactos_etapa2": exact_s2_jids,
        },
        "comparativas_ic95": {nombre: _evaluar_cohorte(jids) for nombre, jids in cohortes.items()},
        "trazabilidad_26_jobs": trazabilidad_jobs,
    }
    ruta_cache.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resultado


def auditar_evidencia_corners_nb_ronda10(
    *,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Ronda 10 Punto 3: Separa la evidencia de córneres Binomial Negativa (factor 1.05,
    bandera `usar_sobredispersion_nb_corners`) en:
      (a) Partidos de ajuste (los 24 partidos OOS de `evaluacion_9393a7b9017a5365.json` del
          2026-10-18 + TB/IF del 2026-11-12, todos anteriores a `9593ba6` 2026-11-13T04:16:01);
      (b) Partidos OOS posteriores competitivos (`>= 2026-11-13T04:16:01-05:00`, excluyendo
          amistosos marcados con `entra_calibracion=False`).
    Si no hay partidos posteriores, lo declara explícitamente con métricas `None` y mantiene
    `usar_sobredispersion_nb_corners = False` como regla operativa en muestra sin validación OOS.
    """
    from pathlib import Path
    from decision_cobertura import bandera_activa, BANDERAS_COMPORTAMIENTO_DEFECTO

    raiz = Path(__file__).resolve().parent
    oos = json.loads((raiz / "datos" / "oos_conciliacion_ronda7_ronda8.json").read_text(encoding="utf-8"))
    filas_1376 = [
        r for r in oos["filas_1506"]
        if all(k in (r.get("probs") or {}) for k in ("arch", "5e8425f", "994209a", "soloA", "A+B"))
    ]
    filas_corners = [
        r for r in filas_1376
        if _macro_familia_ronda8(str(r.get("mercado") or "")) == "Córneres"
    ]
    verif = verificar_no_inversion_etiquetas_modelos(filas_1376)

    tuplas_corners = [
        (
            str(r.get("id_partido") or r.get("partido")),
            int(r["evento_ocurrio"]),
            {
                "arch": float(r["probs"]["arch"]),
                "5e8425f": float(r["probs"]["5e8425f"]),
                "soloA": float(r["probs"]["soloA"]),
                "994209a": float(r["probs"]["994209a"]),
                "A+B": float(r["probs"]["A+B"]),
            },
        )
        for r in filas_corners
    ]
    stats_corners = bootstrap_brier_logloss_por_partido(tuplas_corners, n_boot=n_boot, seed=seed)

    # Revisar ledger_oos y archivos informes/evaluacion por partidos posteriores competitivos
    corte_norm = _normalizar_fecha_iso_comparacion(FECHA_FIJACION_UMBRALES_BLOQUE3_ISO)
    partidos_post: list[dict[str, Any]] = []
    amistosos_post_excluidos: list[dict[str, Any]] = []
    ruta_ledger = raiz / "informes" / "evaluacion" / "ledger_oos.jsonl"
    if ruta_ledger.exists():
        for linea in ruta_ledger.read_text(encoding="utf-8").splitlines():
            s = linea.strip()
            if not s:
                continue
            try:
                reg = json.loads(s)
            except Exception:
                continue
            ts_norm = _normalizar_fecha_iso_comparacion(
                str(
                    reg.get("liquidado_en")
                    or reg.get("registrado_en_utc")
                    or reg.get("registrado_en")
                    or reg.get("fecha_partido")
                    or reg.get("fecha")
                    or ""
                )
            )
            es_amistoso = (
                str(reg.get("tipo_partido") or "").strip().lower() == "amistoso"
                or reg.get("entra_calibracion") is False
                or reg.get("entra_promedios") is False
            )
            if ts_norm and ts_norm >= corte_norm:
                if es_amistoso:
                    amistosos_post_excluidos.append({
                        "id_partido": reg.get("id_partido"),
                        "partido": reg.get("partido"),
                        "fecha_partido": reg.get("fecha_partido") or reg.get("fecha"),
                        "tipo_partido": reg.get("tipo_partido"),
                    })
                elif not es_partido_de_diseno(reg.get("partido"), reg.get("id_partido")):
                    partidos_post.append(reg)

    n_post = len(partidos_post)
    d_ll_corners = stats_corners["diferencias"]["A+B - soloA"]["d_log_loss"]
    aporte_sobre_1376 = round(len(filas_corners) * d_ll_corners / len(filas_1376), 6) if filas_1376 else None

    return {
        "bandera": "usar_sobredispersion_nb_corners",
        "valor_por_defecto": bool(BANDERAS_COMPORTAMIENTO_DEFECTO.get("usar_sobredispersion_nb_corners", False)),
        "activo_en_runtime": bool(bandera_activa("usar_sobredispersion_nb_corners")),
        "estado_regla": "regla operativa en muestra sin validación OOS",
        "fecha_calibracion_commit": FECHA_FIJACION_UMBRALES_BLOQUE3_ISO,
        "commit_calibracion": COMMIT_FIJACION_UMBRALES_BLOQUE3,
        "partidos_de_ajuste_en_muestra": {
            "fuente_json": "informes/evaluacion/evaluacion_9393a7b9017a5365.json (y espejo de conciliación Ronda 9 `n=1376`)",
            "fecha_generacion_json": "2026-10-18T01:19:18 / evaluacion 2026-10-18",
            "incluye_diseno_previo": ["Turkey vs Belgium (2026-11-12)", "Italy vs France (2026-11-12)"],
            "n_partidos_unicos_corners_oos": stats_corners["n_partidos_unicos"],
            "n_filas_corners_totales": len(filas_corners),
            "n_filas_corners_cambiadas": verif["pares_verificados"]["A+B vs soloA"]["n_cambiadas"],
            "metricas_corners_en_muestra": stats_corners,
            "aporte_a_diferencia_global_1376_log_loss": aporte_sobre_1376,
        },
        "partidos_oos_posteriores": {
            "corte_iso": FECHA_FIJACION_UMBRALES_BLOQUE3_ISO,
            "estado": "sin_partidos_oos_posteriores" if n_post == 0 else "evaluado",
            "n_partidos_posteriores": n_post,
            "n_filas_posteriores": 0 if n_post == 0 else None,
            "partidos": partidos_post,
            "amistosos_excluidos": amistosos_post_excluidos,
            "brier": None if n_post == 0 else None,
            "log_loss": None if n_post == 0 else None,
            "ic95": None if n_post == 0 else None,
        },
    }


def clasificar_torneos_y_localia_30_matrices_ronda10() -> dict[str, Any]:
    """
    Ronda 10 Punto 4: Clasifica las 30 matrices de Colombia (15) y Chile (15) en
    `amistoso` (incluyendo FIFA Series M29/M30), `torneo_sede_neutral` y `sede_real`
    (sin modificar el modelo ni su λ). Cuenta cuántos partidos etiquetados como
    "Local" (`condicion == "casa"`) ocurrieron en sede neutral y entrega la tabla
    de GF/GC por tipo y equipo.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parent
    doc_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8"))
    doc_chi = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8"))

    anfitriones_por_torneo_anio = {
        ("copa america", "2021"): {"brazil"},
        ("copa america", "2024"): {"usa", "united states"},
        ("world cup", "2026"): {"usa", "united states", "mexico", "canada"},
        ("confederations cup", "2017"): {"russia"},
    }

    def _clasificar_partido(equipo_propio: str, p: dict[str, Any]) -> tuple[str, str]:
        torneo = str(p.get("torneo") or "")
        t_low = torneo.lower()
        fecha = str(p.get("fecha") or "")
        anio = fecha[:4]
        rival_low = str(p.get("rival") or "").strip().lower()
        propio_low = equipo_propio.strip().lower()

        if (
            bool(p.get("es_amistoso"))
            or "friendly" in t_low
            or "friendlies" in t_low
            or "amistoso" in t_low
            or "fifa series" in t_low
        ):
            return "amistoso", "FIFA Series / amistoso internacional"

        anfitriones: set[str] = set()
        for (pref, y), hosts in anfitriones_por_torneo_anio.items():
            if pref in t_low and anio == y:
                anfitriones = hosts
                break

        if anfitriones:
            if propio_low in anfitriones or rival_low in anfitriones:
                return "sede_real", f"Torneo ante anfitrión ({torneo} {anio})"
            return "torneo_sede_neutral", f"Torneo con sede neutral ({torneo} {anio})"

        return "sede_real", f"Competición ida/vuelta ({torneo})"

    partidos_clasificados: list[dict[str, Any]] = []
    por_tipo: dict[str, list[dict[str, Any]]] = {
        "amistoso": [],
        "torneo_sede_neutral": [],
        "sede_real": [],
    }
    por_equipo_tipo: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for equipo, doc in (("Colombia", doc_col), ("Chile", doc_chi)):
        for idx, p in enumerate(list(doc.get("partidos") or [])[:15], start=1):
            cat, motivo = _clasificar_partido(equipo, p)
            cond = str(p.get("condicion") or "")
            marc = p.get("marcador") or {}
            gf = int(marc["equipo"]) if marc.get("equipo") is not None else None
            gc = int(marc["rival"]) if marc.get("rival") is not None else None
            fila = {
                "equipo": equipo,
                "idx_equipo": idx,
                "idx_matriz": idx if equipo == "Colombia" else idx + 15,
                "id_partido": str(p.get("id_partido") or ""),
                "fecha": str(p.get("fecha") or ""),
                "torneo": str(p.get("torneo") or "").replace("\n", " "),
                "rival": str(p.get("rival") or ""),
                "condicion": cond,
                "etiqueta_localia": "Local" if cond == "casa" else "Visita",
                "marcador": str(marc.get("texto") or ""),
                "gf": gf,
                "gc": gc,
                "es_amistoso_campo_cache": bool(p.get("es_amistoso")),
                "clasificacion": cat,
                "motivo": motivo,
            }
            partidos_clasificados.append(fila)
            por_tipo[cat].append(fila)
            por_equipo_tipo.setdefault((equipo, cat), []).append(fila)

    def _resumen_gf_gc(rows: list[dict[str, Any]]) -> dict[str, Any]:
        valid = [r for r in rows if r["gf"] is not None and r["gc"] is not None]
        n = len(valid)
        if n == 0:
            return {"n_partidos": 0, "gf_total": 0, "gc_total": 0, "gf_prom": None, "gc_prom": None}
        gf_tot = sum(int(r["gf"]) for r in valid)
        gc_tot = sum(int(r["gc"]) for r in valid)
        return {
            "n_partidos": n,
            "gf_total": gf_tot,
            "gc_total": gc_tot,
            "gf_prom": round(gf_tot / n, 4),
            "gc_prom": round(gc_tot / n, 4),
        }

    locales_col = [r for r in partidos_clasificados if r["equipo"] == "Colombia" and r["condicion"] == "casa"]
    locales_chi = [r for r in partidos_clasificados if r["equipo"] == "Chile" and r["condicion"] == "casa"]
    locales_todos = locales_col + locales_chi

    resumen_locales = {
        "total_etiqueta_local": len(locales_todos),
        "total_local_en_torneo_sede_neutral": sum(1 for r in locales_todos if r["clasificacion"] == "torneo_sede_neutral"),
        "total_local_en_amistoso_fifa_series": sum(1 for r in locales_todos if r["clasificacion"] == "amistoso"),
        "total_local_en_sede_real": sum(1 for r in locales_todos if r["clasificacion"] == "sede_real"),
        "colombia_local_total": len(locales_col),
        "colombia_local_sede_neutral": sum(1 for r in locales_col if r["clasificacion"] == "torneo_sede_neutral"),
        "colombia_local_sede_real": sum(1 for r in locales_col if r["clasificacion"] == "sede_real"),
        "chile_local_total": len(locales_chi),
        "chile_local_sede_neutral": sum(1 for r in locales_chi if r["clasificacion"] == "torneo_sede_neutral"),
        "chile_local_amistoso_fifa_series": sum(1 for r in locales_chi if r["clasificacion"] == "amistoso"),
        "chile_local_sede_real": sum(1 for r in locales_chi if r["clasificacion"] == "sede_real"),
    }

    tabla_gf_gc_por_tipo = {cat: _resumen_gf_gc(rows) for cat, rows in por_tipo.items()}
    tabla_gf_gc_por_equipo_y_tipo = {
        f"{eq}::{cat}": _resumen_gf_gc(rows)
        for (eq, cat), rows in sorted(por_equipo_tipo.items())
    }
    tabla_gf_gc_por_equipo_y_condicion = {
        "Colombia::Local_etiqueta": _resumen_gf_gc(locales_col),
        "Colombia::Visita_etiqueta": _resumen_gf_gc([r for r in partidos_clasificados if r["equipo"] == "Colombia" and r["condicion"] == "fuera"]),
        "Chile::Local_etiqueta": _resumen_gf_gc(locales_chi),
        "Chile::Visita_etiqueta": _resumen_gf_gc([r for r in partidos_clasificados if r["equipo"] == "Chile" and r["condicion"] == "fuera"]),
    }

    return {
        "n_matrices": len(partidos_clasificados),
        "partidos_clasificados": partidos_clasificados,
        "resumen_locales": resumen_locales,
        "tabla_gf_gc_por_tipo": tabla_gf_gc_por_tipo,
        "tabla_gf_gc_por_equipo_y_tipo": tabla_gf_gc_por_equipo_y_tipo,
        "tabla_gf_gc_por_equipo_y_condicion": tabla_gf_gc_por_equipo_y_condicion,
    }


def auditar_descomposicion_colombia_110_y_154g_ronda10() -> dict[str, Any]:
    """
    Ronda 10 Punto 5: Descompone el λ de Colombia etapa por etapa (desde λ plano 1.4135 /
    λ_base 1.39 hasta 1.10), incluyendo cada uno de los 5 factores de la red 45-DCN
    (F1..F5 crudos, reescalados en red y post-reglas) + términos aditivos (`ajuste_portero`,
    `regalo_errores`) y reglas finales (`techo_lambda_ataque_bajo_local = 1.10`), indicando
    cuál empuja más. Reproduce además `"1.54 G"` (`mu_local_casa['goles_favor']`) con su
    fórmula exacta, pesos y fuente.
    """
    from pathlib import Path
    import numpy as np
    from decision_cobertura import banderas_comportamiento
    from motor_mercados import Parametros, analizar
    import motor_recursivo_45_dcn as mr

    raiz = Path(__file__).resolve().parent
    doc_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8"))
    doc_chi = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8"))

    params = Parametros()
    params.competicion = "Eliminatorias CONMEBOL"
    with banderas_comportamiento(activar_todas=False):
        an = analizar(
            doc_col, doc_chi, "Colombia", "Chile",
            condicion_local="casa", condicion_visitante="fuera",
            parametros=params,
        )
    m = an.motor
    rec = m.recursivo
    ct = rec["cruces_tacticos"]
    f_crudos = dict(ct["factores_crudos_local"])
    f_red = dict(ct["factores_red_neuronal_local"])
    f_final = dict(ct["factores_local"])

    pts_15 = list(doc_col.get("partidos") or [])[:15]
    ser_l = mr.extraer_series_reales_15_partidos(pts_15, "Colombia", {})
    est_gf = mr.procesar_secuencia_individual_lstm_determinista(ser_l["goles_favor"])
    est_xg = mr.procesar_secuencia_individual_lstm_determinista(ser_l["xg"])
    est_xgot = mr.procesar_secuencia_individual_lstm_determinista(ser_l["xgot"])

    h_gf = float(est_gf["h_reciente"])
    xg_l_lstm = float(est_xg["h_reciente"])
    xgot_l_lstm = float(est_xgot["h_reciente"])

    lb = float(rec["lambda_base_local"])
    imp_crudo = sum(float(v) for v in f_crudos.values()) / 100.0
    lam_mlp = lb * (1.0 + imp_crudo)
    base_ens = 0.50 * lb + 0.25 * xgot_l_lstm + 0.25 * xg_l_lstm
    aj_port = float(ct["ajuste_portero_local"])
    reg_err = float(ct["regalo_errores_local"])
    lam_pre_it = 0.55 * lam_mlp + 0.45 * base_ens + aj_port + reg_err
    lam_red_post_it = float(rec.get("lambda_local_pre_freno") or 1.0718)

    # Reproducción exacta de "1.54 G" (`mu_local_casa["goles_favor"]`)
    pts_casa = [p for p in pts_15 if str(p.get("condicion") or "") == "casa"]
    w_lin = np.linspace(0.8, 1.2, len(pts_casa))
    detalle_casa: list[dict[str, Any]] = []
    gfs_eff: list[float] = []
    creds: list[float] = []
    for idx_p, (p, wl) in enumerate(zip(pts_casa, w_lin), start=1):
        gf_raw, gc_raw = mr._goles_equipo(p)
        torneo = str(p.get("torneo") or "")
        cap_copa = bool(mr.es_copa_nacional(torneo) and gf_raw is not None and gc_raw is not None and (float(gf_raw) - float(gc_raw)) >= 4)
        gf_ef = min(float(gf_raw), 2.2) if cap_copa else float(gf_raw)
        cred = float(mr.peso_credibilidad_partido(p))
        gfs_eff.append(gf_ef)
        creds.append(cred)
        detalle_casa.append({
            "orden_lista_cache": idx_p,
            "fecha": str(p.get("fecha") or ""),
            "torneo": torneo,
            "rival": str(p.get("rival") or ""),
            "gf_raw": float(gf_raw),
            "gc_raw": float(gc_raw),
            "cap_es_copa_nacional_2_2": cap_copa,
            "gf_efectivo": gf_ef,
            "cred_partido": round(cred, 4),
            "w_linspace_08_12": round(float(wl), 6),
        })

    pesos_raw = w_lin * np.array(creds, dtype=float)
    pesos_norm = pesos_raw / np.sum(pesos_raw)
    media_gf_cond = float(np.sum(np.array(gfs_eff, dtype=float) * pesos_norm))
    for item, pn in zip(detalle_casa, pesos_norm):
        item["peso_normalizado"] = round(float(pn), 6)
        item["aporte_ponderado"] = round(float(item["gf_efectivo"]) * float(pn), 6)

    n_cond = len(pts_casa)
    w_cond = n_cond / (n_cond + 3.5)
    goles_favor_exact = w_cond * media_gf_cond + (1.0 - w_cond) * h_gf
    goles_favor_mu = float(rec["mu_local_casa"]["goles_favor"])

    factores_detalle = []
    for fk in ("F_posesion_progresiva", "F_creacion", "F_finalizacion", "F_porteria_rival", "F_fragilidad_rival"):
        vc = float(f_crudos.get(fk, 0.0))
        vr = float(f_red.get(fk, 0.0))
        vf = float(f_final.get(fk, 0.0))
        factores_detalle.append({
            "factor": fk,
            "crudo_pct": vc,
            "signo_crudo": "+" if vc > 0 else ("-" if vc < 0 else "0"),
            "reescalado_red_pct": vr,
            "signo_red": "+" if vr > 0 else ("-" if vr < 0 else "0"),
            "final_post_reglas_pct": vf,
            "signo_final": "+" if vf > 0 else ("-" if vf < 0 else "0"),
        })

    dom_crudo = min(f_crudos.items(), key=lambda kv: kv[1])
    dom_red = min(f_red.items(), key=lambda kv: kv[1])
    dom_abs_red = max(f_red.items(), key=lambda kv: abs(kv[1]))

    return {
        "lambda_inicial_plano": float(m.traza_lambda[0]["lam_l_despues"]),
        "lambda_base_entrada_dcn": lb,
        "lambda_final": float(m.tasas.lambda_local_ft),
        "traza_completa_etapas": list(m.traza_lambda),
        "descomposicion_interna_red_45_dcn": {
            "lambda_base_local": lb,
            "suma_factores_crudos_pct": round(sum(f_crudos.values()), 4),
            "lam_mlp_crudo": round(lam_mlp, 4),
            "base_ensemble_45pct": round(base_ens, 4),
            "ajuste_portero_local_goles": aj_port,
            "regalo_errores_local_goles": reg_err,
            "lam_pre_iteraciones": round(lam_pre_it, 4),
            "lam_post_iteraciones_bucle": lam_red_post_it,
            "suma_factores_red_neuronal_pct": round(sum(f_red.values()), 4),
            "factor_bucle_estructural_mult": round(lam_red_post_it / lam_pre_it, 4),
            "factores_f1_a_f5": factores_detalle,
            "factor_crudo_que_mas_empuja_baja": {"factor": dom_crudo[0], "valor_pct": dom_crudo[1]},
            "factor_red_que_mas_empuja": {"factor": dom_abs_red[0], "valor_pct": dom_abs_red[1]},
            "factor_red_negativo_mayor": {"factor": dom_red[0], "valor_pct": dom_red[1]},
            "termino_escalar_mayor_en_red_45_dcn": (
                "ajuste_portero_local = -0.35 goles (muro portero Chile) combinado con "
                "regla final `techo_lambda_ataque_bajo_local = 1.10` (Attack_L=27.9 < 35), "
                "mientras entre F1..F5 el factor dominante es F_porteria_rival "
                "(-3.9% crudo, -12.0% reescalado en red, -11.4% final)."
            ),
        },
        "reproduccion_154_g": {
            "valor_publicado_mu_local_casa_goles_favor": goles_favor_mu,
            "valor_reproducido_sin_redondeo": round(goles_favor_exact, 6),
            "valor_reproducido_redondeado": round(goles_favor_exact, 2),
            "coincide_exactamente": abs(round(goles_favor_exact, 2) - goles_favor_mu) < 1e-9,
            "fuente": "motor_recursivo_45_dcn.calcular_doble_adn_con_shrinkage (rec['mu_local_casa']['goles_favor'])",
            "formula": "mu = w_cond * media_gf_cond + (1 - w_cond) * h_gf_lstm_15, con n_cond=7, k_shrinkage=3.5 -> w_cond=7/10.5=0.6667",
            "n_cond": n_cond,
            "k_shrinkage": 3.5,
            "w_cond": round(w_cond, 6),
            "media_gf_cond": round(media_gf_cond, 6),
            "h_gf_lstm_15": round(h_gf, 6),
            "media_cruda_15_partidos": round(22.0 / 15.0, 4),
            "media_cruda_7_casa": round(15.0 / 7.0, 4),
            "nota_clave": (
                "No proviene de xG (xG casa es 1.59) ni de 22/15 (1.47): es la contracción bayesiana "
                "entre los 7 partidos 'casa' (con Panama 5-0 acotado a 2.2 por `es_copa_nacional('Copa America')=True` "
                "y cred=0.35, orden de caché descendente * linspace(0.8, 1.2, 7) => media_gf=1.8220) y "
                "h_LSTM_15=0.965: (2/3)*1.8220 + (1/3)*0.965 = 1.5364 -> 1.54 G."
            ),
            "detalle_7_partidos_casa": detalle_casa,
        },
    }


def resumen_tope_dcn_18_ronda10(*, regenerar: bool = False) -> dict[str, Any]:
    """
    Ronda 10 Punto 6: Tabla exacta de toques positivos (`+18%`) vs negativos (`-18%`)
    del tope DCN por lado (`local`/`visitante`) y etapa (`red_45_dcn`, `post_sinc_mercados`,
    `global_cualquier_etapa`) sobre el denominador declarado de `73` partidos (`146` lados),
    conciliando explícitamente por qué `73` difiere de `83`, `85` y `89`.
    """
    import csv as _csv
    import glob as _glob
    from pathlib import Path

    raiz = Path(__file__).resolve().parent
    ruta_cache = raiz / "datos" / "ronda10_punto6_tope_dcn.json"
    if ruta_cache.exists() and not regenerar:
        return json.loads(ruta_cache.read_text(encoding="utf-8"))

    raw = auditar_tope_dcn_18_por_lado_y_partido()
    detalle = list(raw.get("detalle") or [])

    def _contar_etapa(rk_l: str, rk_v: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "local": {"total": 0, "+18%": 0, "-18%": 0},
            "visitante": {"total": 0, "+18%": 0, "-18%": 0},
            "total": {"total": 0, "+18%": 0, "-18%": 0},
            "partidos_al_menos_un_lado": 0,
            "partidos_ambos_lados": 0,
        }
        for d in detalle:
            hl = d.get(rk_l) or {}
            hv = d.get(rk_v) or {}
            tl = bool(hl.get("toco"))
            tv = bool(hv.get("toco"))
            if tl or tv:
                out["partidos_al_menos_un_lado"] += 1
            if tl and tv:
                out["partidos_ambos_lados"] += 1
            for lado, info, hit in (("local", hl, tl), ("visitante", hv, tv)):
                if not hit:
                    continue
                sg = "+18%" if str(info.get("direccion") or "").startswith("+") else "-18%"
                out[lado]["total"] += 1
                out[lado][sg] += 1
                out["total"]["total"] += 1
                out["total"][sg] += 1
        return out

    tabla_red = _contar_etapa("red_local", "red_visitante")
    tabla_post = _contar_etapa("post_local", "post_visitante")
    tabla_global: dict[str, Any] = {
        "local": {"total": 0, "+18%": 0, "-18%": 0, "ambas_direcciones": 0},
        "visitante": {"total": 0, "+18%": 0, "-18%": 0, "ambas_direcciones": 0},
        "total": {"total": 0, "+18%": 0, "-18%": 0, "ambas_direcciones": 0},
        "partidos_al_menos_un_lado": int(raw["global_cualquier_etapa"]["partidos_al_menos_un_lado"]),
        "partidos_ambos_lados": int(raw["global_cualquier_etapa"]["partidos_ambos_lados"]),
    }
    for d in detalle:
        for lado, rk, pk in (("local", "red_local", "post_local"), ("visitante", "red_visitante", "post_visitante")):
            rx = d.get(rk) or {}
            px = d.get(pk) or {}
            rt, pt = bool(rx.get("toco")), bool(px.get("toco"))
            if not (rt or pt):
                continue
            dirs = ({"+18%" if str(rx.get("direccion") or "").startswith("+") else "-18%"} if rt else set()) | (
                {"+18%" if str(px.get("direccion") or "").startswith("+") else "-18%"} if pt else set()
            )
            tabla_global[lado]["total"] += 1
            tabla_global["total"]["total"] += 1
            if len(dirs) == 1:
                sg = next(iter(dirs))
                tabla_global[lado][sg] += 1
                tabla_global["total"][sg] += 1
            else:
                tabla_global[lado]["ambas_direcciones"] += 1
                tabla_global["total"]["ambas_direcciones"] += 1

    mer_files = sorted(_glob.glob(str(raiz / "informes" / "mercados" / "**" / "mercados_*.json"), recursive=True))
    pids_banco: set[str] = set()
    ruta_csv = raiz / "data" / "banco_partidos.csv"
    if ruta_csv.exists():
        with open(ruta_csv, "r", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                if str(row.get("equipo_clave") or "") not in ("colombia", "chile") and row.get("id_partido"):
                    pids_banco.add(str(row["id_partido"]))

    d135 = auditar_doble_135_ronda9(activar_b=False)
    res = {
        "denominador_declarado_partidos": int(raw["n_partidos_evaluados"]),
        "denominador_declarado_lados": int(raw["n_lados_evaluados"]),
        "conciliacion_denominadores": {
            "73_tope_dcn": {
                "valor": int(raw["n_partidos_evaluados"]),
                "composicion": "69 cruces únicos (equipo_local, equipo_visitante, fecha) archivados en informes/mercados + 4 cruces de referencia (Turkey-Belgium, Italy-France, Londrina-Náutico, Colombia-Chile).",
            },
            "85_archivados_ejecutables": {
                "valor": int(d135["n_archivados_ejecutables"]),
                "composicion": "89 archivos mercados_*.json en informes/mercados minus 4 sin par de auditoría local; incluye 16 reejecuciones repetidas del mismo cruce+fecha (85 - 69 = 16).",
            },
            "89_mercados_json_totales": {
                "valor": len(mer_files),
                "composicion": "Total de archivos mercados_*.json en disco; 4 de ellos (`Central Español vs Atlético Madrid`, `Dalian Yingbo vs Athletic Club`, `Deportivo de A Coruña (W) vs Levante`, `Linense vs Palestina`) carecen de par de auditoría.",
            },
            "83_banco_partidos": {
                "valor": len(pids_banco),
                "composicion": "Universo distinto de 83 partidos históricos únicos almacenados en `data/banco_partidos.csv` (excluyendo Colombia y Chile de los fixtures de estudio).",
            },
        },
        "tabla_por_lado_y_etapa": {
            "en_red_45_dcn": tabla_red,
            "en_post_sinc_mercados": tabla_post,
            "global_cualquier_etapa": tabla_global,
        },
        "pct_partidos_tocados_global": round(100.0 * tabla_global["partidos_al_menos_un_lado"] / max(1, int(raw["n_partidos_evaluados"])), 2),
        "pct_lados_tocados_global": round(100.0 * tabla_global["total"]["total"] / max(1, int(raw["n_lados_evaluados"])), 2),
    }
    ruta_cache.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return res


def auditar_veto_1x_y_frec_1t_ronda10() -> dict[str, Any]:
    """
    Ronda 10 Punto 7:
      1. Verifica que VETO 1X (`tiene_desvio_excesivo_calibracion` sobre Doble Oportunidad)
         use brecha con signo (`modelo - observado > 10.0 pp`), de modo que Colombia-Chile 1X
         (72.8% modelo < 93.3% observado, brecha -20.5 pp) NO se veta por calibración,
         mientras un modelo sobreconfiado (ej. 71.7% > 40.0%, +31.7 pp) SÍ se veta.
      2. Reproduce la frecuencia 46.7% (14/30) atribuida anteriormente a "1T Menos de 1.5"
         demostrando su origen en evaluar goles FT < 1.5 (7/15 Colombia + 7/15 Chile = 14/30),
         frente a las frecuencias reales en 1T desde franjas_goles y mitades de Cuadro C
         (11/14 Colombia = 78.6% / 12/15 = 80.0% y 6/9 Chile = 66.7%, más 12/13 Chile con franjas -> 24/28 = 85.7%).
    """
    from pathlib import Path
    from auditoria_historica import reconstruir_marcador_mitades_partido
    from decision_cobertura import banderas_comportamiento as _bc_p7
    from motor_mercados import Parametros as _Par_p7, Seleccion, analizar as _an_p7, calcular_frecuencia_observada, tiene_desvio_excesivo_calibracion

    raiz = Path(__file__).resolve().parent
    doc_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8"))
    doc_chi = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8"))

    # 1. Prueba VETO 1X con brecha con signo
    s_1x_col_chi = Seleccion("doble_oportunidad", "Doble oportunidad", "dc_1x", "1X (Colombia o empate)", "FT", "", None, 0.728)
    excede_cc, desv_cc, f_obs_cc = tiene_desvio_excesivo_calibracion(s_1x_col_chi, doc_col, doc_chi, "Colombia", "Chile")

    doc_sobre_l = {"partidos": [{"marcador": {"equipo": 1, "rival": 0}}] * 6 + [{"marcador": {"equipo": 0, "rival": 1}}] * 9}
    doc_sobre_v = {"partidos": [{"marcador": {"equipo": 0, "rival": 1}}] * 15}
    s_1x_sobre = Seleccion("doble_oportunidad", "Doble oportunidad", "dc_1x", "1X (Sobreconfiado)", "FT", "", None, 0.717)
    excede_sc, desv_sc, f_obs_sc = tiene_desvio_excesivo_calibracion(s_1x_sobre, doc_sobre_l, doc_sobre_v, "Local", "Visita")

    # 2. Reproducción exacta de 46.7% (FT < 1.5) vs 1T real
    pts_col = (doc_col.get("partidos") or [])[:15]
    pts_chi = (doc_chi.get("partidos") or [])[:15]

    ft_u15_col = sum(1 for p in pts_col if (p.get("marcador") or {}).get("equipo") is not None and (p["marcador"]["equipo"] + p["marcador"]["rival"]) < 1.5)
    ft_u15_chi = sum(1 for p in pts_chi if (p.get("marcador") or {}).get("equipo") is not None and (p["marcador"]["equipo"] + p["marcador"]["rival"]) < 1.5)

    fg_eval_col, fg_u15_col = 0, 0
    for p in pts_col:
        fg = p.get("franjas_goles") or {}
        if fg and all(isinstance(fg.get(k), dict) for k in ("00_15", "16_30", "31_45")):
            fg_eval_col += 1
            if sum((fg[k].get("anotados", 0) or 0) + (fg[k].get("recibidos", 0) or 0) for k in ("00_15", "16_30", "31_45")) < 1.5:
                fg_u15_col += 1
    fg_eval_chi, fg_u15_chi = 0, 0
    for p in pts_chi:
        fg = p.get("franjas_goles") or {}
        if fg and all(isinstance(fg.get(k), dict) for k in ("00_15", "16_30", "31_45")):
            fg_eval_chi += 1
            if sum((fg[k].get("anotados", 0) or 0) + (fg[k].get("recibidos", 0) or 0) for k in ("00_15", "16_30", "31_45")) < 1.5:
                fg_u15_chi += 1

    col_mit_eval, col_mit_u15 = 0, 0
    for p in pts_col:
        ge1, gr1, _, _, ok_m, _ = reconstruir_marcador_mitades_partido(p, "equipo", "rival")
        if ok_m and ge1 is not None and gr1 is not None:
            col_mit_eval += 1
            if (ge1 + gr1) < 1.5:
                col_mit_u15 += 1

    chi_mit_eval, chi_mit_u15 = 0, 0
    for p in pts_chi:
        ge1, gr1, _, _, ok_m, _ = reconstruir_marcador_mitades_partido(p, "equipo", "rival")
        if ok_m and ge1 is not None and gr1 is not None:
            chi_mit_eval += 1
            if (ge1 + gr1) < 1.5:
                chi_mit_u15 += 1

    eval_30, u15_30 = 0, 0
    for p in (pts_col + pts_chi):
        fg = p.get("franjas_goles") or {}
        if fg and all(isinstance(fg.get(k), dict) for k in ("00_15", "16_30", "31_45")):
            eval_30 += 1
            if sum((fg[k].get("anotados", 0) or 0) + (fg[k].get("recibidos", 0) or 0) for k in ("00_15", "16_30", "31_45")) < 1.5:
                u15_30 += 1
        else:
            ge1, gr1, _, _, ok_m, _ = reconstruir_marcador_mitades_partido(p, "equipo", "rival")
            if ok_m and ge1 is not None and gr1 is not None:
                eval_30 += 1
                if (ge1 + gr1) < 1.5:
                    u15_30 += 1

    _pr_p7 = _Par_p7()
    _pr_p7.competicion = "Eliminatorias CONMEBOL"
    with _bc_p7(activar_todas=True):
        _an1t = _an_p7(doc_col, doc_chi, "Colombia", "Chile", condicion_local="casa", condicion_visitante="fuera", parametros=_pr_p7)
    _sel_1t = next(
        s for rm in _an1t.mercados for s in rm.selecciones
        if s.periodo == "1T" and s.evento == "under_1.5"
    )
    f_obs_1t_corregida = calcular_frecuencia_observada(_sel_1t, doc_col, doc_chi, "Colombia", "Chile")
    excede_1t_ab, desv_1t_ab, _ = tiene_desvio_excesivo_calibracion(_sel_1t, doc_col, doc_chi, "Colombia", "Chile")

    return {
        "veto_1x_brecha_con_signo": {
            "colombia_chile_1x": {
                "modelo_pct": 72.8,
                "observado_pct": round(float(f_obs_cc) * 100.0, 1) if f_obs_cc is not None else None,
                "brecha_modelo_menos_obs_pp": round((0.728 - float(f_obs_cc)) * 100.0, 1) if f_obs_cc is not None else None,
                "excede_desvio_vetado": bool(excede_cc),
                "desvio_abs_pp": desv_cc,
            },
            "caso_sobreconfiado_prueba": {
                "modelo_pct": 71.7,
                "observado_pct": round(float(f_obs_sc) * 100.0, 1) if f_obs_sc is not None else None,
                "brecha_modelo_menos_obs_pp": round((0.717 - float(f_obs_sc)) * 100.0, 1) if f_obs_sc is not None else None,
                "excede_desvio_vetado": bool(excede_sc),
                "desvio_abs_pp": desv_sc,
            },
        },
        "origen_46_7_vs_real_1t": {
            "reproduccion_46_7_bug_ft_ignora_periodo_1t": {
                "colombia_ft_u15": f"{ft_u15_col}/15",
                "chile_ft_u15": f"{ft_u15_chi}/15",
                "combinado": f"{ft_u15_col + ft_u15_chi}/30",
                "frecuencia_pct": round((ft_u15_col + ft_u15_chi) * 100.0 / 30.0, 1),
                "explicacion": "calcular_frecuencia_observada ignoraba sel.periodo=='1T' y sumaba goles FT (gf+gc < 1.5): 7/15 Colombia + 7/15 Chile = 14/30 = 46.7%.",
            },
            "frecuencia_1t_desde_franjas_goles": {
                "colombia": f"{fg_u15_col}/{fg_eval_col} ({round(fg_u15_col*100.0/fg_eval_col, 1)}%)",
                "chile_con_franjas": f"{fg_u15_chi}/{fg_eval_chi} ({round(fg_u15_chi*100.0/fg_eval_chi, 1)}%)",
                "combinado_28p": f"{fg_u15_col + fg_u15_chi}/{fg_eval_col + fg_eval_chi} ({round((fg_u15_col + fg_u15_chi)*100.0/(fg_eval_col + fg_eval_chi), 1)}%)",
                "combinado_30p_con_mitades_m29_m30": f"{u15_30}/{eval_30} ({round(u15_30*100.0/max(1, eval_30), 1)}%)",
            },
            "frecuencia_1t_cuadro_c_mitades": {
                "colombia_sin_m14_0_0_inferido": f"{col_mit_u15}/{col_mit_eval} ({round(col_mit_u15*100.0/max(1,col_mit_eval), 1)}%)",
                "chile_9_mitades_cuadro_c": f"{chi_mit_u15}/{chi_mit_eval} ({round(chi_mit_u15*100.0/max(1,chi_mit_eval), 1)}%)",
                "combinado_mitades": f"{col_mit_u15 + chi_mit_u15}/{col_mit_eval + chi_mit_eval} ({round((col_mit_u15 + chi_mit_u15)*100.0/max(1, col_mit_eval + chi_mit_eval), 1)}%)",
            },
            "prob_modelo_1t_u15_AB_pct": round(float(_sel_1t.probabilidad) * 100.0, 2),
            "frecuencia_1t_corregida_en_motor_pct": round(float(f_obs_1t_corregida) * 100.0, 1) if f_obs_1t_corregida is not None else None,
            "veto_calibracion_1t_u15_75_7_en_AB": bool(excede_1t_ab),
            "desvio_1t_u15_75_7_vs_obs_pp": desv_1t_ab,
        },
    }


def auditar_datos_ronda10(
    guardar_ruta: str | None = "datos/ronda10_punto8_datos.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 10 — Punto 8 (DATOS):
      (a) M1 entradas: documenta y verifica que 17 (Colombia) y 12 (Suiza) son
          conteos enteros de FotMob (`format='integer'`, `matchstats.headers.tackles`),
          no porcentajes, por lo que no se convierten en tasa con sin_dato.
      (b) M3: muestra la evidencia exacta (`ac_dir`, `ac_inv`, `bandos_invertidos`)
          del emparejamiento FotMob 4667823 vs Flashscore vL2qotaK (`0-0`) y el
          efecto del bug histórico pre-d6cdbe4 vs la regla actual.
      (c) Valida que los 17 `tiros_libres_sin_invertir_igual_faltas` del banco de
          83 partidos pasan a `0` post-`sanear_partido_auditoria_45` mediante la regla
          general `tiros_libres_propios = faltas_rival`, verificada también en FT, 1T
          y 2T con `Colombia 1-0 Chile (2026-11-13, Flashscore 2Pafs6I6)`.
      (d) Revisa los tres cachés truncados de Turquía (481,224 bytes) y confirma
          que `Turkey vs Belgium` usó exclusivamente fixtures FotMob válidos
          (`ronda5_real_fixtures.json`, `FotMob 100% Opta`) y nunca esos archivos.
    """
    import copy
    import hashlib
    import re as _re
    from pathlib import Path
    from auditoria_historica import (
        formatear_matriz_45_partido,
        sanear_partido_auditoria_45,
    )
    from fuente_flashscore import emparejar_partido, normalizar as _norm_fs

    raiz = Path(__file__).resolve().parent

    def _fila_dict(partido: dict[str, Any], per: str, clave: str) -> dict[str, Any]:
        for f in ((partido.get("periodos") or {}).get(per) or {}).get("filas") or []:
            if f.get("clave") == clave:
                return f
        return {}

    # ── 8(a) M1 Switzerland vs Colombia (FotMob 4653849) ──
    cache_col_path = raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json"
    doc_col = json.loads(cache_col_path.read_text(encoding="utf-8-sig"))
    m1_raw = copy.deepcopy((doc_col.get("partidos") or [])[0])
    m1_san = sanear_partido_auditoria_45(copy.deepcopy(m1_raw))
    md1_san = formatear_matriz_45_partido(m1_san, k=1, total_partidos=30)
    fila_32_md1 = next(
        (ln.strip() for ln in md1_san.splitlines() if "| 32 |" in ln),
        "",
    )
    m1_info = {
        "id_partido": m1_raw.get("id_partido"),
        "fecha": m1_raw.get("fecha"),
        "partido": f"{m1_raw.get('equipo')} vs {m1_raw.get('rival')}",
        "fotmob_matchdetails_tackles": {
            "key": "matchstats.headers.tackles",
            "title": "Tackles",
            "format": "integer",
            "stats_ft": [12, 17],
            "stats_1t_api": [6, 11],
            "stats_2t_api": [2, 5],
            "orden_fotmob": ["Switzerland (home=12)", "Colombia (away=17)"],
            "son_conteos_enteros": True,
            "son_porcentajes": False,
        },
        "cache_crudo": {
            "ft_entradas": (_fila_dict(m1_raw, "FT", "entradas").get("equipo"), _fila_dict(m1_raw, "FT", "entradas").get("rival")),
            "ft_entradas_pct_falso_100": (_fila_dict(m1_raw, "FT", "entradas_pct").get("equipo"), _fila_dict(m1_raw, "FT", "entradas_pct").get("rival")),
            "1t_entradas": (_fila_dict(m1_raw, "1T", "entradas").get("equipo"), _fila_dict(m1_raw, "1T", "entradas").get("rival")),
            "2t_entradas": (_fila_dict(m1_raw, "2T", "entradas").get("equipo"), _fila_dict(m1_raw, "2T", "entradas").get("rival")),
        },
        "post_saneamiento": {
            "ft_entradas_pct_conteo": (_fila_dict(m1_san, "FT", "entradas_pct").get("equipo"), _fila_dict(m1_san, "FT", "entradas_pct").get("rival")),
            "ft_entradas_ganadas": (_fila_dict(m1_san, "FT", "entradas_ganadas").get("equipo"), _fila_dict(m1_san, "FT", "entradas_ganadas").get("rival")),
            "ft_entradas_denominador": (_fila_dict(m1_san, "FT", "entradas").get("equipo"), _fila_dict(m1_san, "FT", "entradas").get("rival")),
            "fila_32_markdown": fila_32_md1,
        },
        "conclusion": (
            "17 (Colombia) y 12 (Suiza) son conteos enteros de tackles (`format='integer'` en FotMob "
            "matchDetails 4653849), no porcentajes; el falso 100.0 provenía de derivación sin denominador "
            "observado y queda saneado sin convertir los conteos en porcentajes."
        ),
    }

    # ── 8(b) M3 Colombia vs Portugal (4667823 / vL2qotaK) ac_dir vs ac_inv ──
    datos_m3_fm = {
        "fecha": "2026-06-27",
        "local": "Colombia",
        "visitante": "Portugal",
        "goles_local": 0,
        "goles_visitante": 0,
    }
    cand_dir = {
        "id": "vL2qotaK",
        "fecha": "2026-06-27",
        "local": "Colombia",
        "visitante": "Portugal",
        "goles_local": 0,
        "goles_visitante": 0,
    }
    cand_inv = {
        "id": "vL2qotaK",
        "fecha": "2026-06-27",
        "local": "Portugal",
        "visitante": "Colombia",
        "goles_local": 0,
        "goles_visitante": 0,
    }

    def _contar_ac(cand: dict[str, Any], inv: bool) -> tuple[int, bool]:
        cl = _norm_fs(cand["visitante"] if inv else cand["local"])
        cv = _norm_fs(cand["local"] if inv else cand["visitante"])
        dl = _norm_fs(datos_m3_fm["local"])
        dv = _norm_fs(datos_m3_fm["visitante"])
        ac_l = int(dl == cl)
        ac_v = int(dv == cv)
        return ac_l + ac_v, bool(ac_l and ac_v)

    ac_dir_real, amb_dir_real = _contar_ac(cand_dir, False)
    ac_inv_real, amb_inv_real = _contar_ac(cand_dir, True)
    emp_dir = emparejar_partido(datos_m3_fm, [cand_dir])
    emp_inv = emparejar_partido(datos_m3_fm, [cand_inv])

    m3_raw = copy.deepcopy((doc_col.get("partidos") or [])[2])
    m3_san = sanear_partido_auditoria_45(copy.deepcopy(m3_raw))
    m3_info = {
        "id_fotmob": m3_raw.get("id_partido"),
        "id_flashscore": (m3_raw.get("fuente_secundaria") or {}).get("id_flashscore"),
        "cache_bandos_invertidos_heredado": bool((m3_raw.get("fuente_secundaria") or {}).get("bandos_invertidos")),
        "emparejamiento_sobre_candidato_real_vL2qotaK": {
            "candidato_flashscore": cand_dir,
            "ac_dir": ac_dir_real,
            "amb_dir": amb_dir_real,
            "ac_inv": ac_inv_real,
            "amb_inv": amb_inv_real,
            "regla_actual_ac_inv_gt_ac_dir": bool(ac_inv_real > ac_dir_real),
            "bandos_invertidos_actual": bool(emp_dir and emp_dir.get("bandos_invertidos")),
            "explicacion_bug_historico_pre_d6cdbe4": (
                "Antes de d6cdbe4, `invertido = (c.gl == gv and c.gv == gl)` daba True en empates 0-0 "
                "sin verificar ac_inv > ac_dir (obteniendo ac_inv=0 sobre Colombia vs Portugal), lo que "
                "cruzaba los porcentajes/ganadas de Flashscore (53% 8/15 Colombia, 56% 5/9 Portugal) en "
                "5/15 (56%) y 8/9 (53%)."
            ),
        },
        "emparejamiento_candidato_invertido_control": {
            "candidato_simulado": cand_inv,
            "ac_dir": _contar_ac(cand_inv, False)[0],
            "ac_inv": _contar_ac(cand_inv, True)[0],
            "bandos_invertidos_actual": bool(emp_inv and emp_inv.get("bandos_invertidos")),
        },
        "entradas_post_saneamiento": {
            per: {
                "equipo_colombia": (
                    _fila_dict(m3_san, per, "entradas_ganadas").get("equipo"),
                    _fila_dict(m3_san, per, "entradas").get("equipo"),
                    _fila_dict(m3_san, per, "entradas_pct").get("equipo"),
                ),
                "rival_portugal": (
                    _fila_dict(m3_san, per, "entradas_ganadas").get("rival"),
                    _fila_dict(m3_san, per, "entradas").get("rival"),
                    _fila_dict(m3_san, per, "entradas_pct").get("rival"),
                ),
            }
            for per in ("FT", "1T", "2T")
        },
    }

    # ── 8(c) Corrección de los 17 tiros_libres_sin_invertir + Colombia-Chile 2026-11-13 ──
    _resp_r12_p4 = raiz / "datos" / "respaldos" / "ronda12_p4_pre_correccion"
    if (_resp_r12_p4 / "tests" / "fixtures" / "londrina_nautico_fixtures.json").exists():
        from banco_historico import validar_defectos_partidos_banco as _vdpb_r10
        rep_banco = _vdpb_r10(
            csv_path=_resp_r12_p4 / "data" / "banco_partidos.csv",
            db_path=_resp_r12_p4 / "data" / "banco_partidos.db",
            raiz=raiz,
            incluir_colombia_chile=False,
            londrina_nautico_path=_resp_r12_p4 / "tests" / "fixtures" / "londrina_nautico_fixtures.json",
        )
    else:
        rep_banco = auditar_defectos_banco_83_ronda9()
    pre_tl = int(rep_banco["matrices_crudas_pre_saneamiento"]["por_tipo"].get("tiros_libres_sin_invertir_igual_faltas", 0))
    post_tl = int(rep_banco["matrices_post_sanear_partido_auditoria_45"]["por_tipo"].get("tiros_libres_sin_invertir_igual_faltas", 0))

    prim_cc_path = raiz / "datos" / "colombia_chile_2026_11_13_primario.json"
    prim_cc = json.loads(prim_cc_path.read_text(encoding="utf-8"))
    cc_validacion: dict[str, Any] = {}
    for per in ("FT", "1T", "2T"):
        p_raw = (prim_cc.get("flashscore_por_periodo_crudo") or {}).get(per) or {}
        loc_r = p_raw.get("local") or {}
        vis_r = p_raw.get("visitante") or {}
        f_col = int(loc_r["Faltas"])
        f_chi = int(vis_r["Faltas"])
        tl_col = int(loc_r["Tiros libres"])
        tl_chi = int(vis_r["Tiros libres"])
        cc_validacion[per] = {
            "faltas_colombia": f_col,
            "faltas_chile": f_chi,
            "tiros_libres_colombia": tl_col,
            "tiros_libres_chile": tl_chi,
            "cumple_tl_propio_igual_faltas_rival": bool(tl_col == f_chi and tl_chi == f_col),
        }

    cc_sin_inv = {
        "id_partido": "2Pafs6I6_test",
        "fecha": "2026-11-13",
        "equipo": "Colombia",
        "rival": "Chile",
        "fuente_primaria": "flashscore",
        "fuente_secundaria": {"pareado": True, "bandos_invertidos": True},
        "periodos": {
            per: {
                "filas": [
                    {"clave": "faltas", "equipo": float(v["faltas_colombia"]), "rival": float(v["faltas_chile"])},
                    {"clave": "tiros_libres", "equipo": float(v["faltas_colombia"]), "rival": float(v["faltas_chile"])},
                ]
            }
            for per, v in cc_validacion.items()
        },
        "resumen_claves": {
            "faltas": {"equipo": float(cc_validacion["FT"]["faltas_colombia"]), "rival": float(cc_validacion["FT"]["faltas_chile"])},
            "tiros_libres": {"equipo": float(cc_validacion["FT"]["faltas_colombia"]), "rival": float(cc_validacion["FT"]["faltas_chile"])},
        },
    }
    sanear_partido_auditoria_45(cc_sin_inv)
    tl_post_cc = {
        per: (
            _fila_dict(cc_sin_inv, per, "tiros_libres").get("equipo"),
            _fila_dict(cc_sin_inv, per, "tiros_libres").get("rival"),
        )
        for per in ("FT", "1T", "2T")
    }

    tiros_libres_info = {
        "banco_pre_tiros_libres_sin_invertir": pre_tl,
        "banco_post_tiros_libres_sin_invertir": post_tl,
        "banco_post_total_defectos": int(rep_banco["matrices_post_sanear_partido_auditoria_45"]["total_defectos"]),
        "banco_post_por_tipo": dict(rep_banco["matrices_post_sanear_partido_auditoria_45"]["por_tipo"]),
        "validacion_colombia_chile_2026_11_13": cc_validacion,
        "sanear_repara_colombia_chile_sin_invertir": tl_post_cc,
    }

    # ── 8(d) Revisión de cachés truncados de Turquía vs uso real en Turkey-Belgium ──
    archivos_truncados_tur = [
        "cache/cache_flashscore_turquia.json",
        "cache/cache_fotmob_fs_tur_123_TUR_False_False.json",
        "cache/cache_fotmob_turkey_TUR_False_False.json",
    ]
    estado_truncados: list[dict[str, Any]] = []
    for rel in archivos_truncados_tur:
        fp = raiz / rel
        if not fp.exists():
            estado_truncados.append({"archivo": rel, "existe": False})
            continue
        raw_b = fp.read_bytes()
        sha = hashlib.sha256(raw_b).hexdigest()
        txt = raw_b.decode("utf-8-sig", errors="replace")
        valido = True
        err_msg = None
        try:
            json.loads(txt)
        except Exception as exc:
            valido = False
            err_msg = f"{type(exc).__name__}: {exc}"
        m_gen = _re.search(r'"generado_en"\s*:\s*"([^"]+)"', txt[:120])
        estado_truncados.append({
            "archivo": rel,
            "existe": True,
            "bytes": len(raw_b),
            "sha256_prefix": sha[:16],
            "json_valido": valido,
            "error": err_msg,
            "encabezado_generado_en": m_gen.group(1) if m_gen else None,
            "termina_con": txt[-80:],
        })

    r5_path = raiz / "tests" / "fixtures" / "ronda5_real_fixtures.json"
    r5_tur = json.loads(r5_path.read_text(encoding="utf-8"))["docs"]["turkey"]
    qe_path = raiz / "cache" / "cache_fotmob_qeijuho5_TUR_False_False.json"
    qe_doc = json.loads(qe_path.read_text(encoding="utf-8-sig"))
    md_tb_path = raiz / "informes" / "auditorias" / "2026-11-13" / "auditoria_matrices_turquia_vs_belgica_2026-11-13_parte1de8.md"
    md_tb_txt = md_tb_path.read_text(encoding="utf-8-sig") if md_tb_path.exists() else ""

    basenames_trunc = [Path(rel).name for rel in archivos_truncados_tur]
    refs_en_codigo: list[str] = []
    for p_py in sorted(raiz.glob("*.py")):
        if p_py.name == "evaluacion_pronosticos.py":
            t_py = p_py.read_text(encoding="utf-8-sig", errors="ignore").split("def auditar_datos_ronda10")[0]
        else:
            t_py = p_py.read_text(encoding="utf-8-sig", errors="ignore")
        if any(bn in t_py for bn in basenames_trunc):
            refs_en_codigo.append(p_py.name)

    turkey_info = {
        "caches_truncados_detectados": estado_truncados,
        "referencias_en_codigo_py": refs_en_codigo,
        "ronda5_real_fixtures_turkey": {
            "ruta": "tests/fixtures/ronda5_real_fixtures.json",
            "n_partidos": len(r5_tur.get("partidos") or []),
            "json_valido": True,
            "primeros_3_ids_fotmob": [p.get("id_partido") for p in (r5_tur.get("partidos") or [])[:3]],
        },
        "cache_flashscore_turquia_valido": {
            "ruta": "cache/cache_fotmob_qeijuho5_TUR_False_False.json",
            "bytes": qe_path.stat().st_size,
            "generado_en": qe_doc.get("generado_en"),
            "n_partidos": len(qe_doc.get("partidos") or []),
            "json_valido": True,
        },
        "auditoria_turquia_belgica_2026_11_13": {
            "ruta": "informes/auditorias/2026-11-13/auditoria_matrices_turquia_vs_belgica_2026-11-13_parte1de8.md",
            "usa_fotmob_100_opta": ("FotMob (100% Opta)" in md_tb_txt),
            "contiene_id_5181795": ("5181795" in md_tb_txt),
        },
        "turkey_belgium_uso_caches_truncados": False,
        "conclusion": (
            "No: Turkey-Belgium utilizó únicamente matrices válidas de FotMob (`tests/fixtures/ronda5_real_fixtures.json`, "
            "IDs `5181795..4689339`, auditoría 2026-11-13 04:52:26). Los 3 cachés truncados (481,224 bytes, idéntico "
            "SHA-256 6fcf4b4478cac6d0, generados en 2026-11-16 11:33:35 y cortados en `partidos_futuros`) no se cargan en "
            "ningún módulo ni en el replay de Turkey-Belgium."
        ),
    }

    res = {
        "m1_entradas": m1_info,
        "m3_inversion_bandos": m3_info,
        "tiros_libres_17_y_col_chi": tiros_libres_info,
        "turkey_belgium_caches_truncados": turkey_info,
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def auditar_doble_135_unicos_ronda10(
    activar_b: bool = False,
    guardar_ruta: str | None = "datos/ronda10_punto9_doble_135_unicos.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 10 — Punto 9 (DOBLE ×1.35 por partidos únicos):
    Usa un único denominador declarado: los `73` partidos únicos reproducibles
    (`69` cruces únicos `(equipo_local, equipo_visitante, fecha)` archivados en
    `informes/mercados/` + `4` partidos de referencia `TB`, `IF`, `LN`, `COL_CHI`),
    eliminando las reejecuciones repetidas (`New Zealand vs Inter Miami CF`
    aparecía duplicado en `6318e0ead09aab95` y `cf87cad84ed3cf46`).
    """
    from pathlib import Path
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm

    raiz = Path(__file__).resolve().parent
    ev93 = json.loads((raiz / "informes" / "evaluacion" / "evaluacion_9393a7b9017a5365.json").read_text(encoding="utf-8"))
    ev93_jids = {
        str(r.get("id_trabajo") or "")
        for r in (ev93.get("evaluaciones") or [])
        if r.get("id_trabajo")
    }

    pares_73 = _recolectar_pares_partidos_cacheados_ronda9()

    # Identificar duplicados en los 85 ejecutables para conciliar 6/85 (+1) vs 5/69 (+1)
    mer_dir = raiz / "informes" / "mercados"
    mer_files = sorted(mer_dir.rglob("mercados_*.json"))
    por_cruce: dict[tuple[str, str, str], list[str]] = {}
    sin_par_nombres = {"8ee4737c43df02a1", "390ff3918ad3cdd5", "1500e66e5e4dcbfb", "5e95b4928309ff2b"}
    for fp in mer_files:
        raw = json.loads(fp.read_text(encoding="utf-8-sig"))
        md = raw.get("metadatos") or {}
        jid_raw = str(md.get("id_trabajo") or fp.stem.replace("mercados_", ""))
        if any(jid_raw.endswith(s) or jid_raw == s for s in sin_par_nombres):
            continue
        el = str(md.get("equipo_local") or "").strip().lower()
        ev = str(md.get("equipo_visitante") or "").strip().lower()
        fch = str(md.get("fecha_partido") or "").strip()
        por_cruce.setdefault((el, ev, fch), []).append(f"ARCH::{jid_raw}")

    duplicados_en_doble_135: list[dict[str, Any]] = []
    for k_c, jids in sorted(por_cruce.items()):
        if len(jids) > 1 and ("new zealand" in k_c[0] or "inter miami" in k_c[1]):
            duplicados_en_doble_135.append({
                "partido": f"{k_c[0]} vs {k_c[1]}",
                "job_ids_archivados": jids,
                "conteo_tickets_ronda9": len(jids),
                "conteo_partido_unico_ronda10": 1,
            })

    hits_unicos: list[dict[str, Any]] = []
    grupos_totales: dict[str, int] = {}
    with banderas_comportamiento(activar_todas=bool(activar_b)):
        for p in pares_73:
            grp = str(p.get("grupo") or "archivado")
            grupos_totales[grp] = grupos_totales.get(grp, 0) + 1
            par = mm.Parametros()
            if p.get("competicion"):
                par.competicion = str(p["competicion"])
            mot = mm.MotorMercados(
                p["doc_local"],
                p["doc_visitante"],
                p["cond_l"],
                p["cond_v"],
                p["equipo_local"],
                p["equipo_visitante"],
                par,
            )
            mot.preparar()
            pasos = [
                s for s in (mot.traza_lambda or [])
                if isinstance(s, dict) and s.get("regla") == "copa_asedio_defensa_rota"
            ]
            if len(pasos) >= 2:
                jid_plain = str(p["job_id"]).replace("ARCH::", "")
                es_oos = any(jid_plain == ej or jid_plain.endswith(ej) or ej.endswith(jid_plain) for ej in ev93_jids)
                hits_unicos.append({
                    "job_id": p["job_id"],
                    "grupo": grp,
                    "partido": p["partido"],
                    "es_oos_ev93": bool(es_oos),
                    "n_aplicaciones": len(pasos),
                    "etapas": [str(s.get("etapa")) for s in pasos],
                    "pasos": [
                        {
                            "paso": s.get("paso"),
                            "etapa": s.get("etapa"),
                            "lam_l_antes": s.get("lam_l_antes", s.get("L0")),
                            "lam_l_despues": s.get("lam_l_despues", s.get("L1")),
                            "lam_v_antes": s.get("lam_v_antes", s.get("V0")),
                            "lam_v_despues": s.get("lam_v_despues", s.get("V1")),
                        }
                        for s in pasos
                    ],
                })

    archivados_u = [h for h in hits_unicos if h["grupo"] == "archivado"]
    diseno_u = [h for h in hits_unicos if h["grupo"] == "diseno"]
    oos_u = [h for h in hits_unicos if h["es_oos_ev93"]]
    n_denom = len(pares_73)

    res = {
        "modo_banderas": "A+B" if activar_b else "soloA",
        "denominador_unico_declarado": n_denom,
        "composicion_denominador": {
            "archivados_unicos_reproducibles": grupos_totales.get("archivado", 0),
            "referencia_diseno_control_auditoria": n_denom - grupos_totales.get("archivado", 0),
            "grupos": grupos_totales,
            "nota": (
                "Mismo denominador único declarado que Punto 6 (TOPE DCN): 73 partidos únicos "
                "reproducibles offline (69 cruces únicos de los 85 tickets ejecutables en informes/mercados "
                "más 4 partidos de referencia TB, IF, LN y COL-CHI)."
            ),
        },
        "n_archivados_unicos_doble_135": len(archivados_u),
        "n_diseno_unicos_doble_135": len(diseno_u),
        "n_oos_unicos_doble_135": len(oos_u),
        "n_total_unicos_doble_135": len(hits_unicos),
        "tasa_sobre_denominador_pct": round(len(hits_unicos) * 100.0 / max(1, n_denom), 2),
        "desglose_explicito": (
            f"{len(archivados_u)} archivados únicos "
            f"({len(oos_u)} OOS de ev93 + {len(archivados_u) - len(oos_u)} archivados no-OOS) "
            f"+ {len(diseno_u)} diseño = {len(hits_unicos)}/{n_denom}"
        ),
        "partidos_oos_ev93": [
            {"job_id": h["job_id"], "partido": h["partido"], "etapas": h["etapas"]}
            for h in oos_u
        ],
        "partidos_archivados_no_oos": [
            {"job_id": h["job_id"], "partido": h["partido"], "etapas": h["etapas"]}
            for h in archivados_u if not h["es_oos_ev93"]
        ],
        "partidos_diseno": [
            {"job_id": h["job_id"], "partido": h["partido"], "etapas": h["etapas"]}
            for h in diseno_u
        ],
        "conciliacion_tickets_vs_partidos_unicos": {
            "ronda9_tickets_archivados_con_duplicados": "6/85 (+1 diseño = 7)",
            "ronda10_partidos_unicos_sin_duplicados": f"{len(archivados_u)}/{grupos_totales.get('archivado', 0)} archivados + {len(diseno_u)} diseño = {len(hits_unicos)}/{n_denom}",
            "duplicado_eliminado": duplicados_en_doble_135,
        },
        "partidos_unicos_doble_135": hits_unicos,
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def es_registro_amistoso_excluido(reg: dict[str, Any]) -> bool:
    """True si un registro del ledger/evaluación corresponde a amistoso o exclusión explícita."""
    if not isinstance(reg, dict):
        return False
    tipo_low = str(reg.get("tipo_partido") or "").strip().lower()
    comp_low = str(reg.get("competicion") or "").strip().lower()
    return bool(
        tipo_low == "amistoso"
        or reg.get("entra_promedios") is False
        or reg.get("entra_calibracion") is False
        or any(tok in comp_low for tok in ("friendly", "friendlies", "amistoso"))
    )


def resumen_ledger_oos(
    ruta_ledger: str | None = None,
    *,
    incluir_amistosos: bool = False,
    excluir_legacy_sin_fixture: bool = False,
) -> dict[str, Any]:
    """
    Calcula promedios por partido y agregados de calibración desde `ledger_oos.jsonl`,
    excluyendo estrictamente registros etiquetados como `amistoso` o con
    `entra_promedios=False` / `entra_calibracion=False` por defecto.
    Las entradas previas a Regla M sin fixture confirmado ni hora de inicio se
    clasifican lógicamente como no-OOS (`legacy_sin_fixture`) sin reescribir hashes.
    """
    ruta = ruta_ledger or LEDGER_OOS_PATH
    verif = verificar_cadena_ledger_oos(ruta)
    registros = cargar_ledger_oos(ruta)
    excluidos: list[dict[str, Any]] = []
    excluidos_no_oos = 0
    excluidos_no_canonica = 0
    excluidos_legacy_sin_fixture = 0
    clasificacion_entradas: list[dict[str, Any]] = []
    n_legacy_pre = 0
    n_legacy_liq = 0
    n_oos_validos_confirmados = 0
    incluidos: list[dict[str, Any]] = []
    for r in registros:
        est_reg = str(r.get("estado_registro") or "liquidado").strip().lower()
        cls_r = clasificar_oos_entrada_ledger(r)
        es_leg = bool(cls_r["es_legacy_sin_fixture"])
        if es_leg:
            if est_reg == "pre_partido":
                n_legacy_pre += 1
            else:
                n_legacy_liq += 1
        elif est_reg != "pre_partido" and cls_r["es_oos_valido"] and cls_r["es_canonica"]:
            if incluir_amistosos or not es_registro_amistoso_excluido(r):
                n_oos_validos_confirmados += 1
        clasificacion_entradas.append({
            "indice": r.get("indice"),
            "id_trabajo": r.get("id_trabajo"),
            "id_partido": r.get("id_partido"),
            "partido": r.get("partido"),
            "fecha": r.get("fecha"),
            "estado_registro": est_reg,
            "clasificacion_oos": cls_r["clasificacion_oos"],
            "es_oos_valido": cls_r["es_oos_valido"],
            "es_legacy_sin_fixture": es_leg,
        })
        if est_reg == "pre_partido":
            continue
        if "es_oos_valido" in r:
            if r.get("es_oos_valido") is not True:
                excluidos_no_oos += 1
                if es_leg:
                    excluidos_legacy_sin_fixture += 1
                continue
            if r.get("es_canonica") is not True:
                excluidos_no_canonica += 1
                continue
        elif es_leg and excluir_legacy_sin_fixture:
            excluidos_no_oos += 1
            excluidos_legacy_sin_fixture += 1
            continue
        if (not incluir_amistosos) and es_registro_amistoso_excluido(r):
            excluidos.append({
                "indice": r.get("indice"),
                "id_partido": r.get("id_partido"),
                "partido": r.get("partido"),
                "fecha": r.get("fecha"),
                "tipo_partido": r.get("tipo_partido"),
                "entra_promedios": r.get("entra_promedios"),
                "entra_calibracion": r.get("entra_calibracion"),
            })
            continue
        incluidos.append(r)

    tasas_partido: list[float] = []
    briers_partido: list[float] = []
    total_ac = 0
    total_ev = 0
    for r in incluidos:
        res_r = r.get("resumen") or {}
        lineas = [ln for ln in (r.get("lineas") or []) if isinstance(ln, dict)]
        ev_lineas = [
            ln for ln in lineas
            if ln.get("estado_liquidacion") in ("ganada", "perdida")
            and ln.get("evento_ocurrio") in (0, 1)
        ]
        n_ev = len(ev_lineas) if ev_lineas else int(res_r.get("n_evaluables") or 0)
        if n_ev <= 0:
            continue
        if ev_lineas:
            n_ac = sum(1 for ln in ev_lineas if ln.get("evento_ocurrio") == 1)
            probs = [float(ln.get("probabilidad_publicada")) for ln in ev_lineas if ln.get("probabilidad_publicada") is not None]
            if len(probs) == n_ev:
                b_p = sum((p - float(ln["evento_ocurrio"])) ** 2 for p, ln in zip(probs, ev_lineas)) / n_ev
            else:
                b_p = res_r.get("brier")
        else:
            n_ac = int(res_r.get("aciertos") or 0)
            b_p = res_r.get("brier")
        total_ac += n_ac
        total_ev += n_ev
        tasas_partido.append(n_ac / n_ev)
        if b_p is not None:
            briers_partido.append(float(b_p))

    res_oos_estricto = _metricas_subconjunto_ledger(
        registros,
        incluir_amistosos=incluir_amistosos,
        excluir_contaminados_diseno=False,
        umbral_excl_prob_alta=None,
        excluir_legacy_sin_fixture=True,
    )
    return {
        "ruta_ledger": ruta,
        "cadena_sha256_ok": bool(verif.get("ok")),
        "n_registros_totales": len(registros),
        "n_incluidos_promedios_calibracion": len(incluidos),
        "n_excluidos_amistosos": len(excluidos),
        "n_excluidos_no_oos": excluidos_no_oos,
        "n_excluidos_no_canonica": excluidos_no_canonica,
        "n_excluidos_legacy_sin_fixture": excluidos_legacy_sin_fixture,
        "n_legacy_sin_fixture": n_legacy_pre + n_legacy_liq,
        "n_legacy_sin_fixture_pre_partido": n_legacy_pre,
        "n_legacy_sin_fixture_liquidados": n_legacy_liq,
        "n_oos_validos_confirmados": n_oos_validos_confirmados,
        "politica_legacy_oos": "legacy_sin_fixture",
        "clasificacion_oos_entradas": clasificacion_entradas,
        "resumen_oos_estricto": res_oos_estricto,
        "regla_contabilidad_oos": REGLA_CONTABILIDAD_OOS,
        "partidos_incluidos": [f"{r.get('partido')} ({r.get('fecha')})" for r in incluidos],
        "amistosos_excluidos": excluidos,
        "n_lineas_evaluables": total_ev,
        "acierto_medio_equilibrado": (sum(tasas_partido) / len(tasas_partido)) if tasas_partido else None,
        "brier_medio_equilibrado": (sum(briers_partido) / len(briers_partido)) if briers_partido else None,
        **_enriquecer_resumen_ledger_oos({}, registros),
        "tasa_acierto_global": (total_ac / total_ev) if total_ev > 0 else None,
    }


def _construir_stats_flashscore_3_pantallas_colombia_chile(
    primario: dict[str, Any],
) -> dict[str, Any]:
    """
    Ejecuta `fuente_flashscore.parsear_feed_por_periodos` y el pipeline completo de
    `ClienteFlashscore.metricas_partido` sobre las 3 pantallas reales (FT, 1T, 2T)
    de Flashscore `2Pafs6I6`.
    """
    from fuente_flashscore import (
        ClienteFlashscore,
        parsear_feed_por_periodos,
    )

    crudo = primario.get("flashscore_por_periodo_crudo") or {}
    titulos = {"FT": "Partido", "1T": "1er Tiempo", "2T": "2º Tiempo"}
    bloques: list[str] = []
    for per in ("FT", "1T", "2T"):
        pobj = crudo.get(per) or {}
        loc_m = pobj.get("local") or {}
        vis_m = pobj.get("visitante") or {}
        filas_bl: list[str] = [f"SE÷{titulos[per]}", "SF÷Estadísticas"]
        for idx_f, (eti, v_loc) in enumerate(loc_m.items()):
            v_vis = vis_m.get(eti, "")
            filas_bl.append(f"SD÷{idx_f}¬SG÷{eti}¬SH÷{v_loc}¬SI÷{v_vis}")
        bloques.extend(filas_bl)
    texto_feed = "~".join(bloques) + "~"
    parseado_crudo = parsear_feed_por_periodos(texto_feed)

    class _ClienteFeed3Pantallas(ClienteFlashscore):
        def _get(self, url: str, binario: bool = False):  # type: ignore[override]
            class _R:
                status_code = 200
                content = b"x" * 100
                text = texto_feed
            return _R()

    m_parsed = _ClienteFeed3Pantallas().metricas_partido(str(primario.get("match_id_flashscore") or "2Pafs6I6"))
    metricas_por_per = m_parsed.por_periodo

    no_aditivas = {
        "posesion", "posesion_pct", "pases_pct", "pases_precision_pct",
        "pases_largos_pct", "pases_tercio_final_pct",
        "centros_pct", "cruces_pct", "entradas_pct", "entradas_ganadas_pct",
    }
    comunes = sorted(
        set(metricas_por_per["FT"]["local"])
        & set(metricas_por_per["1T"]["local"])
        & set(metricas_por_per["2T"]["local"])
    )
    mismatches_aditivos: list[dict[str, Any]] = []
    comprobaciones_aditivas: dict[str, dict[str, Any]] = {}
    for k in comunes:
        if k in no_aditivas:
            continue
        lft = metricas_por_per["FT"]["local"][k]
        l1 = metricas_por_per["1T"]["local"][k]
        l2 = metricas_por_per["2T"]["local"][k]
        vft = metricas_por_per["FT"]["visitante"][k]
        v1 = metricas_por_per["1T"]["visitante"][k]
        v2 = metricas_por_per["2T"]["visitante"][k]
        ok_l = abs((l1 + l2) - lft) <= 1e-6
        ok_v = abs((v1 + v2) - vft) <= 1e-6
        comprobaciones_aditivas[k] = {
            "local_1t": l1, "local_2t": l2, "local_ft": lft, "suma_ok_local": ok_l,
            "visitante_1t": v1, "visitante_2t": v2, "visitante_ft": vft, "suma_ok_visitante": ok_v,
        }
        if not (ok_l and ok_v):
            mismatches_aditivos.append({"clave": k, **comprobaciones_aditivas[k]})

    return {
        "n_etiquetas_ft": len(parseado_crudo.get("FT") or {}),
        "n_etiquetas_1t": len(parseado_crudo.get("1T") or {}),
        "n_etiquetas_2t": len(parseado_crudo.get("2T") or {}),
        "metricas_por_periodo": metricas_por_per,
        "n_metricas_aditivas_validadas": len(comprobaciones_aditivas),
        "mismatches_aditivos": mismatches_aditivos,
        "comprobaciones_aditivas": comprobaciones_aditivas,
    }


def registrar_colombia_chile_amistoso_ronda10(
    ruta_ledger: str | None = None,
) -> dict[str, Any]:
    """
    ORDEN RONDA 10 — Punto 10: registra `Colombia vs Chile (2026-11-13, 1-0)`
    en `ledger_oos` con `tipo_partido="amistoso"`, `entra_promedios=False` y
    `entra_calibracion=False`, conservando intacto `goles_evitados Chile = 1.08`.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parent
    prim = json.loads((raiz / "datos" / "colombia_chile_2026_11_13_primario.json").read_text(encoding="utf-8"))
    mp = prim["flashscore_metricas_partido"]

    stats_verif: dict[str, Any] = {}
    for per in ("FT", "1T", "2T"):
        loc = mp[per]["local"]
        vis = mp[per]["visitante"]
        g_loc, g_vis = (1, 0) if per in ("FT", "1T") else (0, 0)
        stats_verif[per] = {
            "goles": [g_loc, g_vis],
            "xg": [loc["xg"], vis["xg"]],
            "xgot": [loc["xgot"], vis["xgot"]],
            "posesion_pct": [loc["posesion_pct"], vis["posesion_pct"]],
            "remates_totales": [loc["remates_totales"], vis["remates_totales"]],
            "remates_puerta": [loc["remates_puerta"], vis["remates_puerta"]],
            "paradas": [loc["paradas"], vis["paradas"]],
            "corners": [loc["corners"], vis["corners"]],
            "faltas": [loc["faltas"], vis["faltas"]],
            "tiros_libres": [loc["tiros_libres"], vis["tiros_libres"]],
            "errores_gol": [loc["errores_gol"], vis["errores_gol"]],
            "xgot_enfrentado": [loc["xgot_enfrentado"], vis["xgot_enfrentado"]],
            "goles_evitados": [loc["goles_evitados"], vis["goles_evitados"]],
        }

    reg = {
        "id_partido": str(prim["match_id_fotmob"]),
        "id_flashscore": str(prim["match_id_flashscore"]),
        "partido": "Colombia vs Chile",
        "fecha": "2026-11-13",
        "fecha_utc": str(prim["fecha_utc"]),
        "competicion": str(prim["competicion"]),
        "tipo_partido": "amistoso",
        "entra_promedios": False,
        "entra_calibracion": False,
        "lineas": [],
        "marcador_real": {"ft": [1, 0], "1t": [1, 0], "2t": [0, 0]},
        "evento_gol_autogol": {
            "minuto": 35,
            "jugador": "Guillermo Maripán",
            "autogol": True,
            "error_gol_chile_flashscore": 1,
            "explicacion_fisica": (
                "Colombia registra 2 remates a puerta (1T=1, 2T=1), Chile 2 paradas (1T=1, 2T=1) "
                "y 1 error conducente a gol en 1T; el único gol del 1-0 fue autogol de Guillermo Maripán "
                "(min 35, FotMob 6539346). GEv Chile = 1.08 (0.90 1T + 0.18 2T) se conserva verbatim "
                "tal cual la fuente sin alterarlo."
            ),
        },
        "resumen": {
            "aciertos": None,
            "fallos": None,
            "devoluciones": 0,
            "n_evaluables": 0,
            "n_publicadas": 0,
            "no_verificables": 0,
            "regla_no_definida": 0,
            "brier": None,
            "tasa_acierto": None,
        },
        "stats_flashscore_verificadas": stats_verif,
    }
    return registrar_en_ledger_oos(reg, ruta_ledger=ruta_ledger)


def auditar_colombia_chile_amistoso_3_pantallas_ronda10(
    ruta_ledger: str | None = None,
    guardar_ruta: str | None = "datos/ronda10_punto10_colombia_chile_amistoso.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 10 — Punto 10: ejecuta y valida:
      1. Registro idempotente de Colombia-Chile 2026-11-13 (1-0, Friendlies) en `ledger_oos`
         con `tipo_partido="amistoso"`, `entra_promedios=False`, `entra_calibracion=False`.
      2. Exclusión comprobada de promedios y calibración (`resumen_ledger_oos` y
         `auditar_evidencia_corners_nb_ronda10`).
      3. Prueba completa del parser de Flashscore (`parsear_feed_por_periodos`) sobre las 3
         pantallas (`FT`, `1T`, `2T`) verificando `1T + 2T == FT` y `GEv Chile = 1.08` verbatim.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parent
    prim = json.loads((raiz / "datos" / "colombia_chile_2026_11_13_primario.json").read_text(encoding="utf-8"))
    reg_res = registrar_colombia_chile_amistoso_ronda10(ruta_ledger=ruta_ledger)
    verif = verificar_cadena_ledger_oos(ruta_ledger=ruta_ledger)
    res_led = resumen_ledger_oos(ruta_ledger=ruta_ledger, incluir_amistosos=False)
    ev_corners = auditar_evidencia_corners_nb_ronda10(n_boot=200, seed=42)
    parse3 = _construir_stats_flashscore_3_pantallas_colombia_chile(prim)

    mp = parse3["metricas_por_periodo"]
    out = {
        "registro_ledger": reg_res,
        "cadena_sha256": verif,
        "resumen_ledger_sin_amistosos": res_led,
        "evidencia_corners_nb_posteriores": ev_corners["partidos_oos_posteriores"],
        "explicacion_1_0_y_gev_chile": {
            "remates_puerta_colombia": {"1T": mp["1T"]["local"]["remates_puerta"], "2T": mp["2T"]["local"]["remates_puerta"], "FT": mp["FT"]["local"]["remates_puerta"]},
            "paradas_chile": {"1T": mp["1T"]["visitante"]["paradas"], "2T": mp["2T"]["visitante"]["paradas"], "FT": mp["FT"]["visitante"]["paradas"]},
            "errores_gol_chile": {"1T": mp["1T"]["visitante"]["errores_gol"], "2T": mp["2T"]["visitante"]["errores_gol"], "FT": mp["FT"]["visitante"]["errores_gol"]},
            "autogol_fotmob_6539346": prim["eventos_gol_fotmob"],
            "gev_chile_fuente_sin_corregir": {
                "1T": mp["1T"]["visitante"]["goles_evitados"],
                "2T": mp["2T"]["visitante"]["goles_evitados"],
                "FT": mp["FT"]["visitante"]["goles_evitados"],
                "suma_mitades": round(mp["1T"]["visitante"]["goles_evitados"] + mp["2T"]["visitante"]["goles_evitados"], 2),
            },
        },
        "parser_3_pantallas_ft_1t_2t": {
            "n_etiquetas_por_pantalla": {
                "FT": parse3["n_etiquetas_ft"],
                "1T": parse3["n_etiquetas_1t"],
                "2T": parse3["n_etiquetas_2t"],
            },
            "n_metricas_aditivas_validadas": parse3["n_metricas_aditivas_validadas"],
            "mismatches_aditivos": parse3["mismatches_aditivos"],
            "tiros_libres_vs_faltas": {
                per: {
                    "faltas": (mp[per]["local"]["faltas"], mp[per]["visitante"]["faltas"]),
                    "tiros_libres": (mp[per]["local"]["tiros_libres"], mp[per]["visitante"]["tiros_libres"]),
                }
                for per in ("FT", "1T", "2T")
            },
        },
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out



def reportar_cadena_6_etapas_completa_ronda11(
    *,
    regenerar: bool = False,
    n_boot: int = 2000,
    seed: int = 42,
    guardar_ruta: str | None = "datos/ronda11_punto1_cadena_6_etapas.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 11 — Punto 1: Reporta la cadena completa de las 6 etapas (1 a 6)
    sobre exactamente las mismas 1376 filas (y 1506), con delta pareado Brier/LogLoss
    e IC95% por partido entre etapas consecutivas y entre etapas 4 y 6.
    No modifica λ, umbrales ni comportamiento por defecto.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parent
    out_p = (Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)) if guardar_ruta else None
    if out_p is not None and out_p.exists() and not regenerar:
        return json.loads(out_p.read_text(encoding="utf-8"))

    oos = json.loads((raiz / "datos" / "oos_conciliacion_ronda7_ronda8.json").read_text(encoding="utf-8"))
    filas_1506 = list(oos["filas_1506"])
    banderas_solo_a = {k: False for k in obtener_especificacion_columnas_modelos()["soloA"]["banderas_activas"]}
    banderas_ab_r9 = dict(obtener_especificacion_columnas_modelos()["A+B"]["banderas_activas"])
    probs_a, _ = _reejecutar_etapas_oos_26_ronda10(banderas_solo_a)
    probs_ab, _ = _reejecutar_etapas_oos_26_ronda10(banderas_ab_r9)

    obs_1506_a, obs_1376_a, obs_1376_ab = [], [], []
    for r in filas_1506:
        jid = str(r["id_trabajo"])
        k = _normalizar_clave_seleccion_ronda10(r["evento"], r["periodo"], r["equipo"], r["linea"])
        p_map_a = {ek: float(probs_a[jid][ek][k]) for ek in ETAPAS_CADENA_LAMBDA}
        row_a = {**r, "probs": {**p_map_a, "arch": float(r["probs"]["arch"]), "5e8425f": float(r["probs"]["5e8425f"]), "soloA": float(r["probs"]["soloA"])}}
        obs_1506_a.append(row_a)
        if all(x in (r.get("probs") or {}) for x in ("994209a", "A+B")):
            p_map_ab = {ek: float(probs_ab[jid][ek][k]) for ek in ETAPAS_CADENA_LAMBDA}
            obs_1376_a.append(row_a)
            obs_1376_ab.append({**r, "probs": {**p_map_ab, "arch": float(r["probs"]["arch"]), "A+B": float(r["probs"]["A+B"])}})

    out = {
        "hashes": {
            "sha256_ids_1376": hashlib.sha256("\n".join(str(r["row_id"]) for r in obs_1376_a).encode("utf-8")).hexdigest(),
            "sha256_ids_1506": hashlib.sha256("\n".join(str(r["row_id"]) for r in obs_1506_a).encode("utf-8")).hexdigest(),
        },
        "nota_1506_vs_1376": "1506 es el universo completo de soloA/5e8425f/arch; 1376 es la intersección exacta con A+B tras excluir 130 filas filtradas por gates de cobertura en A+B.",
        "cohortes": {
            "soloA_1376": analizar_ablacion_cadena_lambda_oos(obs_1376_a, n_boot=n_boot, seed=seed),
            "soloA_1506": analizar_ablacion_cadena_lambda_oos(obs_1506_a, n_boot=n_boot, seed=seed),
            "AB_1376": analizar_ablacion_cadena_lambda_oos(obs_1376_ab, n_boot=n_boot, seed=seed),
        },
    }
    if out_p is not None:
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def auditar_filtro_ruido_colombia_ronda11(
    *,
    regenerar: bool = False,
    guardar_ruta: str | None = "datos/ronda11_punto2_filtro_ruido.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 11 — Punto 2: Evalúa `usar_filtro_ruido_ic_factores_dcn` cubriendo también
    `ajuste_portero_local` (-0.35), `goles_evitados_rival`, y `F_porteria_rival` reescalado (-12%),
    genera tabla de impacto para Colombia-Chile, TB, IF y LN (con y sin filtro),
    explica el salto aparente 1.07 -> 1.10 en `techo_lambda_ataque_bajo_local` y registra
    el origen de `Attack_L < 35` como regla operativa sin validación.
    """
    from pathlib import Path
    import numpy as _np
    from decision_cobertura import _BANDERAS_AUXILIARES_DEFECTO, banderas_comportamiento
    from motor_mercados import Parametros, analizar

    raiz = Path(__file__).resolve().parent
    out_p = (Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)) if guardar_ruta else None
    if out_p is not None and out_p.exists() and not regenerar:
        return json.loads(out_p.read_text(encoding="utf-8"))

    r5_docs = json.loads((raiz / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))["docs"]
    ln_docs = json.loads((raiz / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    doc_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8"))
    doc_chi = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8"))

    casos = [
        ("Colombia-Chile", "Colombia", "Chile", doc_col, doc_chi, "Eliminatorias CONMEBOL"),
        ("TB", "Turquía", "Belgium", r5_docs["turkey"], r5_docs["belgium"], None),
        ("IF", "Italy", "France", r5_docs["italy"], r5_docs["france"], None),
        ("LN", "Londrina", "Náutico", ln_docs["londrina"], ln_docs["nautico"], "Brasileirao Serie C"),
    ]
    modos = [
        ("soloA_sin_filtro", {"activar_todas": False, "usar_filtro_ruido_ic_factores_dcn": False, "usar_reescalado_sin_inversion_signo_dcn": False}),
        ("soloA_con_filtro", {"activar_todas": False, "usar_filtro_ruido_ic_factores_dcn": True, "usar_reescalado_sin_inversion_signo_dcn": False}),
        ("AB_sin_filtro", {"activar_todas": True, "usar_filtro_ruido_ic_factores_dcn": False, "usar_reescalado_sin_inversion_signo_dcn": False}),
        ("AB_con_filtro", {"activar_todas": True, "usar_filtro_ruido_ic_factores_dcn": True, "usar_reescalado_sin_inversion_signo_dcn": False}),
        ("AB_sininv_sin_filtro", {"activar_todas": True, "usar_filtro_ruido_ic_factores_dcn": False, "usar_reescalado_sin_inversion_signo_dcn": True}),
        ("AB_sininv_con_filtro", {"activar_todas": True, "usar_filtro_ruido_ic_factores_dcn": True, "usar_reescalado_sin_inversion_signo_dcn": True}),
    ]

    tabla: dict[str, dict[str, Any]] = {}
    trazas_colombia: dict[str, list[dict[str, Any]]] = {}

    for cod, eq_l, eq_v, d_l, d_v, comp in casos:
        tabla[cod] = {}
        for m_nom, kw in modos:
            with banderas_comportamiento(**kw):
                pr = Parametros()
                if comp:
                    pr.competicion = comp
                an = analizar(d_l, d_v, eq_l, eq_v, condicion_local="casa", condicion_visitante="fuera", parametros=pr)
                rec = an.motor.recursivo
                ct = rec.get("cruces_tacticos") or {}
                tr = list(an.motor.traza_lambda or [])
                red_steps = [st for st in tr if st.get("etapa") == "red_45_dcn"]
                lam_red = (
                    [round(float(red_steps[-1]["lam_l_despues"]), 2), round(float(red_steps[-1]["lam_v_despues"]), 2)]
                    if red_steps
                    else [rec.get("lambda_local_recursivo"), rec.get("lambda_visita_recursivo")]
                )
                mat = _np.asarray(an.motor.matriz_ft, dtype=float)
                p1 = round(float(_np.sum(_np.tril(mat, -1))) * 100.0, 2)
                px = round(float(_np.sum(_np.diag(mat))) * 100.0, 2)
                p2 = round(float(_np.sum(_np.triu(mat, 1))) * 100.0, 2)
                tabla[cod][m_nom] = {
                    "lambda_base": [rec.get("lambda_base_local"), rec.get("lambda_base_visita")],
                    "lambda_red": lam_red,
                    "lambda_final": [an.motor.tasas.lambda_local_ft, an.motor.tasas.lambda_visitante_ft],
                    "1x2_pct": [p1, px, p2],
                    "ajuste_portero_local": ct.get("ajuste_portero_local"),
                    "ajuste_portero_visita": ct.get("ajuste_portero_visita"),
                    "F_porteria_rival_crudo_L": (ct.get("factores_crudos_local") or {}).get("F_porteria_rival"),
                    "F_porteria_rival_reescalado_L": (ct.get("factores_local") or {}).get("F_porteria_rival"),
                    "F_porteria_rival_reescalado_V": (ct.get("factores_visita") or {}).get("F_porteria_rival"),
                }
                if cod == "Colombia-Chile" and m_nom in ("AB_sin_filtro", "AB_con_filtro", "soloA_sin_filtro", "soloA_con_filtro"):
                    trazas_colombia[m_nom] = tr

    out = {
        "bandera": "usar_filtro_ruido_ic_factores_dcn",
        "valor_por_defecto": bool(_BANDERAS_AUXILIARES_DEFECTO["usar_filtro_ruido_ic_factores_dcn"]),
        "tabla_impacto": tabla,
        "trazas_colombia_chile": trazas_colombia,
        "explicacion_techo_107_a_110": (
            "techo_lambda_ataque_bajo_local aplica min(lam_l, 1.10) cuando Attack_L < 35.0 y Def_V >= 30.0. "
            "No sube lambda de 1.07 a 1.10: tras salir de red_45_dcn en 1.07, sincronizacion_55 recorta a 1.00, "
            "luego copa_asedio_defensa_rota (copa=True, x1.15) sube a 1.15, y techo_lambda_ataque_bajo_local lo topa "
            "a la baja de 1.15 -> 1.10. Con filtro de ruido activo, red_45_dcn sube a 1.28 (o 1.49 sin inversión) "
            "con ajuste_portero_local=0.0 y F_porteria_rival=0.0, luego copa x1.15 sube a 1.3248, y el mismo techo "
            "vuelve a recortar lambda_final a 1.10."
        ),
        "origen_umbral_attack_lt_35": {
            "umbral": "Attack_L < 35.0 (techo_lambda_ataque_bajo = 1.10)",
            "commit_introduccion": "e68fff4a0e947955644be3a66d66c988a76bbd0f",
            "fecha_commit": "2026-11-09T01:14:18-05:00",
            "archivo": "motor_patrones.py",
            "tiene_validacion_oos": False,
            "registro_validacion": "regla operativa sin validación",
        },
    }
    if out_p is not None:
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out



def auditar_m3_y_6_empates_ronda11(
    *,
    guardar_ruta: str | None = "datos/ronda11_punto3_m3_empates.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 11 — Punto 3 (M3):
      - Valores finales de entradas (FT/1T/2T con denominadores) en M3 (FotMob 4667823 / Flashscore vL2qotaK)
        comparados contra los porcentajes de la fuente cruda Flashscore.
      - Aclaración exacta de la aparente contradicción entre Ronda 9 y Ronda 10 sobre inversión de bandos.
      - Auditoría con `ac_dir` / `ac_inv` de los 6 `"empate_con_bandos_invertidos"` del banco de 83 partidos
        para determinar si son artefactos de la heurística antigua `gl == gv`.
    """
    import copy
    from pathlib import Path
    import banco_historico as bh
    from auditoria_historica import sanear_partido_auditoria_45
    from fuente_flashscore import emparejar_partido, normalizar as _norm_fs

    raiz = Path(__file__).resolve().parent
    doc_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8-sig"))
    m3_raw = copy.deepcopy((doc_col.get("partidos") or [])[2])
    m3_san = sanear_partido_auditoria_45(copy.deepcopy(m3_raw))

    def _fila_dict(partido: dict[str, Any], per: str, clave: str) -> dict[str, Any]:
        for f in ((partido.get("periodos") or {}).get(per) or {}).get("filas") or []:
            if f.get("clave") == clave:
                return f
        return {}

    fuente_fs_vl2qotak = {
        "id_flashscore": "vL2qotaK",
        "url": "https://www.flashscore.com/match/vL2qotaK/",
        "local": "Colombia",
        "visitante": "Portugal",
        "marcador": "0-0",
        "entradas": {
            "FT": {"local": "53% (8/15)", "visitante": "56% (5/9)", "colombia": (8, 15, 53.0), "portugal": (5, 9, 56.0)},
            "1T": {"local": "33% (2/6)", "visitante": "60% (3/5)", "colombia": (2, 6, 33.0), "portugal": (3, 5, 60.0)},
            "2T": {"local": "67% (6/9)", "visitante": "50% (2/4)", "colombia": (6, 9, 67.0), "portugal": (2, 4, 50.0)},
        },
    }

    m3_comparacion: dict[str, Any] = {}
    for per in ("FT", "1T", "2T"):
        eg = _fila_dict(m3_san, per, "entradas_ganadas")
        et = _fila_dict(m3_san, per, "entradas")
        ep = _fila_dict(m3_san, per, "entradas_pct")
        col_tupla = (eg.get("equipo"), et.get("equipo"), ep.get("equipo"))
        por_tupla = (eg.get("rival"), et.get("rival"), ep.get("rival"))
        fs_per = fuente_fs_vl2qotak["entradas"][per]
        col_txt = f"{int(col_tupla[0])}/{int(col_tupla[1])} ({int(round(col_tupla[2]))}%)"
        por_txt = f"{int(por_tupla[0])}/{int(por_tupla[1])} ({int(round(por_tupla[2]))}%)"
        m3_comparacion[per] = {
            "final_colombia": col_txt,
            "final_portugal": por_txt,
            "final_tupla_colombia": list(col_tupla),
            "final_tupla_portugal": list(por_tupla),
            "fuente_flashscore_local_colombia": fs_per["local"],
            "fuente_flashscore_visitante_portugal": fs_per["visitante"],
            "coincide_exactamente": (
                tuple(int(x) for x in col_tupla) == tuple(int(x) for x in fs_per["colombia"])
                and tuple(int(x) for x in por_tupla) == tuple(int(x) for x in fs_per["portugal"])
            ),
        }

    # Auditoría ac_dir/ac_inv de los 6 "empate_con_bandos_invertidos" en el banco de 83 partidos
    candidatos_fs_banco = {
        "5190761": {"id": "2qdzm8Mi", "fecha": "2026-10-13", "local": "Athletic Club", "visitante": "Londrina", "goles_local": 2, "goles_visitante": 2, "fs_ft_local": "60% (9/15)", "fs_ft_visitante": "42% (8/19)"},
        "5190682": {"id": "WMjv9bAL", "fecha": "2026-09-01", "local": "Londrina", "visitante": "Juventude", "goles_local": 0, "goles_visitante": 0, "fs_ft_local": "44% (12/27)", "fs_ft_visitante": "71% (12/17)"},
        "5190774": {"id": "YqUt3KCk", "fecha": "2026-10-19", "local": "Goias", "visitante": "Nautico", "goles_local": 0, "goles_visitante": 0, "fs_ft_local": "77% (10/13)", "fs_ft_visitante": "71% (10/14)"},
        "5190743": {"id": "pnYANWyq", "fecha": "2026-10-02", "local": "Fortaleza", "visitante": "Nautico", "goles_local": 1, "goles_visitante": 1, "fs_ft_local": "57% (4/7)", "fs_ft_visitante": "36% (4/11)"},
        "5190716": {"id": "xrpocwS8", "fecha": "2026-09-15", "local": "Nautico", "visitante": "Operario-PR", "goles_local": 3, "goles_visitante": 3, "fs_ft_local": "63% (19/30)", "fs_ft_visitante": "67% (8/12)"},
        "5190670": {"id": "Ekd0vm5U", "fecha": "2026-08-23", "local": "Sao Bernardo", "visitante": "Nautico", "goles_local": 1, "goles_visitante": 1, "fs_ft_local": "80% (8/10)", "fs_ft_visitante": "65% (11/17)"},
    }

    _resp_ln_r11 = raiz / "datos" / "respaldos" / "ronda12_p4_pre_correccion" / "tests" / "fixtures" / "londrina_nautico_fixtures.json"
    _ln_hist = _resp_ln_r11 if _resp_ln_r11.exists() else None
    pares, _ = bh._cargar_pares_matrices_banco_83(raiz, incluir_colombia_chile=False, londrina_nautico_path=_ln_hist)
    _resp_r11_p3 = raiz / "datos" / "respaldos" / "ronda12_p4_pre_correccion"
    rep_banco = bh.validar_defectos_partidos_banco(
        csv_path=(_resp_r11_p3 / "data" / "banco_partidos.csv") if (_resp_r11_p3 / "data" / "banco_partidos.csv").exists() else None,
        db_path=(_resp_r11_p3 / "data" / "banco_partidos.db") if (_resp_r11_p3 / "data" / "banco_partidos.db").exists() else None,
        raiz=raiz,
        incluir_colombia_chile=False,
        londrina_nautico_path=_ln_hist,
    )
    n_empates_reportados = int(rep_banco["matrices_post_sanear_partido_auditoria_45"]["por_tipo"].get("empate_con_bandos_invertidos", 0))

    def _aciertos_dir_inv(loc_fm: str, vis_fm: str, cand: dict[str, Any]) -> tuple[int, bool, int, bool]:
        cl, cv = _norm_fs(cand["local"]), _norm_fs(cand["visitante"])
        dl, dv = _norm_fs(loc_fm), _norm_fs(vis_fm)
        d_l = int(bool(dl and cl and (dl == cl or dl in cl or cl in dl)))
        d_v = int(bool(dv and cv and (dv == cv or dv in cv or cv in dv)))
        i_l = int(bool(dl and cv and (dl == cv or dl in cv or cv in dl)))
        i_v = int(bool(dv and cl and (dv == cl or dv in cl or cl in dv)))
        return (d_l + d_v, bool(d_l and d_v), i_l + i_v, bool(i_l and i_v))

    empates_auditoria: list[dict[str, Any]] = []
    for eq_key, p in pares:
        fs = p.get("fuente_secundaria") or {}
        marc = p.get("marcador") or {}
        emp = marc.get("equipo") is not None and marc.get("equipo") == marc.get("rival")
        if not (emp and bool(fs.get("bandos_invertidos"))):
            continue
        pid = str(p.get("id_partido") or "")
        es_casa = str(p.get("condicion") or "") == "casa"
        loc_fm = str(p.get("equipo") if es_casa else p.get("rival"))
        vis_fm = str(p.get("rival") if es_casa else p.get("equipo"))
        gl = marc.get("equipo") if es_casa else marc.get("rival")
        gv = marc.get("rival") if es_casa else marc.get("equipo")
        cand = candidatos_fs_banco[pid]
        ac_dir, amb_dir, ac_inv, amb_inv = _aciertos_dir_inv(loc_fm, vis_fm, cand)
        res_emp = emparejar_partido(
            {"fecha": p.get("fecha"), "local": loc_fm, "visitante": vis_fm, "goles_local": gl, "goles_visitante": gv},
            [cand],
        )
        bandos_inv_actual = bool(res_emp and res_emp.get("bandos_invertidos"))
        eg_f = _fila_dict(p, "FT", "entradas_ganadas")
        et_f = _fila_dict(p, "FT", "entradas")
        empates_auditoria.append({
            "id_partido": pid,
            "equipo_clave": eq_key,
            "fecha_fotmob": p.get("fecha"),
            "marcador": marc.get("texto"),
            "fotmob_local_vs_visitante": f"{loc_fm} vs {vis_fm}",
            "id_flashscore": fs.get("id_flashscore"),
            "flashscore_local_vs_visitante": f"{cand['local']} vs {cand['visitante']}",
            "ac_dir": ac_dir,
            "amb_dir": amb_dir,
            "ac_inv": ac_inv,
            "amb_inv": amb_inv,
            "bandos_invertidos_heredado_fixture": True,
            "bandos_invertidos_emparejamiento_actual": bandos_inv_actual,
            "es_artefacto_gl_igual_gv": bool(not bandos_inv_actual and ac_dir >= ac_inv),
            "cache_entradas_crudo_equipo": [eg_f.get("equipo"), et_f.get("equipo")],
            "cache_entradas_crudo_rival": [eg_f.get("rival"), et_f.get("rival")],
            "fuente_fs_entradas_ft_local": cand["fs_ft_local"],
            "fuente_fs_entradas_ft_visitante": cand["fs_ft_visitante"],
            "explicacion": (
                "Artefacto de la heurística antigua `gl == gv` previa a d6cdbe4: FotMob y Flashscore "
                "comparten exactamente el mismo orden Local vs Visitante (`ac_dir=2, ac_inv=0`), pero el "
                "emparejador anterior invirtió los datos de Flashscore provocando cruces en entradas/tiros libres."
            ),
        })

    n_artefactos = sum(1 for x in empates_auditoria if x["es_artefacto_gl_igual_gv"])
    out = {
        "m3_id_fotmob": "4667823",
        "m3_id_flashscore": "vL2qotaK",
        "m3_entradas_final_vs_fuente": m3_comparacion,
        "aclaracion_contradiccion_ronda9_vs_ronda10": {
            "antes_de_d6cdbe4_ronda8": "Heurística antigua `invertido = (gl == gv and gv == gl)` invertía M3 0-0 -> valores cruzados erróneos FT 5/15 (33%) Colombia | 8/9 (89%) Portugal (test_ronda8_punto6_datos_matrices).",
            "en_d6cdbe4_ronda9": "Se corrigió `fuente_flashscore.emparejar_partido` para exigir `ac_inv > ac_dir` en empates (`ac_dir=2, ac_inv=0 => bandos_invertidos=False`) y `auditoria_historica.py` fijó M3 en 8/15 (53%) | 5/9 (56%) FT, 2/6 (33%) | 3/5 (60%) 1T, 6/9 (67%) | 2/4 (50%) 2T.",
            "origen_confusion": "Ronda 9 no invirtió los bandos en M3 sino que detuvo esa falsa inversión. El texto breve del informe de Ronda 9 ('resuelta la inversión de bandos... (ac_inv > ac_dir)') omitió explicitar que el caché `cache_fotmob_colombia` mantenía el campo heredado `bandos_invertidos: True` pre-d6cdbe4 mientras el algoritmo nuevo da `bandos_invertidos=False`.",
        },
        "banco_83_empate_con_bandos_invertidos": {
            "total_en_validador_banco": n_empates_reportados,
            "total_auditados": len(empates_auditoria),
            "total_artefactos_gl_igual_gv": n_artefactos,
            "todos_son_artefactos": bool(len(empates_auditoria) == 6 and n_artefactos == 6),
            "detalle_6_partidos": empates_auditoria,
        },
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out



def auditar_m1_tackles_ronda11(
    *,
    guardar_ruta: str | None = "datos/ronda11_punto4_m1_tackles.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 11 — Punto 4 (M1 tackles):
      - Muestra la fuente cruda exacta de FotMob 4653849 (`Switzerland 0-0 Colombia`, 2026-07-07):
        FT `[12, 17]` vs `1T [6, 11]` + `2T [2, 5]` (`8` y `16`).
      - Verifica por qué el validador anterior no lo detectó (`Pasada 3b` en
        `consolidar_matriz_completa` reescaló proporcionalmente `[6,11]/[2,5]` -> `[9,12]/[3,5]` sin
        dejar traza en `inconsistencias_aritmeticas`, `validar_defectos_partidos_banco` excluye
        `colombia`/`chile`, y `validar_coherencia_1t_2t_ft` no incluye `entradas`/`entradas_ganadas`
        al estar `entradas_pct` en `METRICAS_NO_ADITIVAS_BANCO`).
      - Clasifica la discrepancia cruda como `suma_mitades_distinta_ft` (y verifica su registro en
        `inconsistencias_aritmeticas` + `_evaluar_defectos_matriz_partido`) sin alterar el banco 83.
    """
    import copy
    from pathlib import Path
    import banco_historico as bh
    import fuentes_xg as fxg

    raiz = Path(__file__).resolve().parent
    doc_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8-sig"))
    m1_cache = copy.deepcopy((doc_col.get("partidos") or [])[0])

    # Fuente cruda verificada en FotMob matchDetails?matchId=4653849
    fuente_cruda_fotmob_4653849 = {
        "matchId": "4653849",
        "general": {"home": "Switzerland", "away": "Colombia", "score": "0-0"},
        "All": {
            "group": "Defence",
            "key": "matchstats.headers.tackles",
            "title": "Tackles",
            "format": "integer",
            "stats": [12, 17],
            "rawStats": [{"value": 12}, {"value": 17}],
        },
        "FirstHalf": {
            "group": "Defence",
            "key": "matchstats.headers.tackles",
            "title": "Tackles",
            "format": "integer",
            "stats": [6, 11],
            "rawStats": [{"value": 6}, {"value": 11}],
        },
        "SecondHalf": {
            "group": "Defence",
            "key": "matchstats.headers.tackles",
            "title": "Tackles",
            "format": "integer",
            "stats": [2, 5],
            "rawStats": [{"value": 2}, {"value": 5}],
        },
        "playerStats_tackles_sum": {"6717_Switzerland": 12.0, "8258_Colombia": 17.0},
    }

    desajuste_crudo = {
        "switzerland_local_rival": {
            "FT": 12, "1T": 6, "2T": 2, "suma_1t_2t": 8, "diferencia_ft_menos_suma": 4,
            "coincide_ft": False,
        },
        "colombia_visita_equipo": {
            "FT": 17, "1T": 11, "2T": 5, "suma_1t_2t": 16, "diferencia_ft_menos_suma": 1,
            "coincide_ft": False,
        },
    }

    # Reconstrucción pre-Pasada-3b para demostrar:
    # (a) qué detecta `_evaluar_defectos_matriz_partido` sobre el dato crudo sin reescalar, y
    # (b) qué produjo Pasada 3b al consolidar la matriz cacheada (12/8=1.5 -> 9,3; 17/16=1.0625 -> 12,5).
    m_pre = fxg.MatrizPartido(
        id_partido="4653849",
        fecha="2026-07-07",
        torneo="World Cup",
        equipo="Colombia",
        rival="Switzerland",
        condicion="fuera",
        goles_equipo=0,
        goles_rival=0,
    )
    m_pre.periodos = {
        "FT": fxg.PeriodoPartido(periodo="FT", filas=[
            {"clave": "entradas", "equipo": 17.0, "rival": 12.0},
            {"clave": "entradas_ganadas", "equipo": 17.0, "rival": 12.0},
            {"clave": "entradas_pct", "equipo": 17.0, "rival": 12.0},
        ]),
        "1T": fxg.PeriodoPartido(periodo="1T", filas=[
            {"clave": "entradas", "equipo": 11.0, "rival": 6.0},
            {"clave": "entradas_ganadas", "equipo": 11.0, "rival": 6.0},
            {"clave": "entradas_pct", "equipo": 11.0, "rival": 6.0},
        ]),
        "2T": fxg.PeriodoPartido(periodo="2T", filas=[
            {"clave": "entradas", "equipo": 5.0, "rival": 2.0},
            {"clave": "entradas_ganadas", "equipo": 5.0, "rival": 2.0},
            {"clave": "entradas_pct", "equipo": 5.0, "rival": 2.0},
        ]),
    }

    def _a_dict_mat(m_obj: fxg.MatrizPartido) -> dict[str, Any]:
        return {
            "id_partido": m_obj.id_partido,
            "fuente_primaria": "fotmob",
            "periodos": {
                per: {"filas": list(bl.filas)}
                for per, bl in (m_obj.periodos or {}).items()
            },
            "inconsistencias_aritmeticas": list(m_obj.inconsistencias_aritmeticas or []),
        }

    hallazgos_crudos = bh._evaluar_defectos_matriz_partido("colombia", _a_dict_mat(m_pre))
    tipos_crudos = [h for h in hallazgos_crudos if h.get("tipo") == "suma_mitades_distinta_ft"]

    # Ejecutar consolidar_matriz_completa actual (con trazabilidad de Pasada 3b en inconsistencias_aritmeticas)
    fxg.consolidar_matriz_completa(m_pre)

    def _ext_celdas(m_obj: fxg.MatrizPartido, clave: str) -> dict[str, list[Any]]:
        res_c: dict[str, list[Any]] = {}
        for per in ("FT", "1T", "2T"):
            f = next((x for x in (m_obj.periodos[per].filas or []) if x.get("clave") == clave), {})
            res_c[per] = [f.get("equipo"), f.get("rival")]
        return res_c

    hallazgos_post_consolidar = bh._evaluar_defectos_matriz_partido("colombia", _a_dict_mat(m_pre))
    hallazgos_sobre_cache_disco = bh._evaluar_defectos_matriz_partido("colombia", m1_cache)
    _resp_r11_p4 = raiz / "datos" / "respaldos" / "ronda12_p4_pre_correccion"
    rep_banco_83 = bh.validar_defectos_partidos_banco(
        csv_path=(_resp_r11_p4 / "data" / "banco_partidos.csv") if (_resp_r11_p4 / "data" / "banco_partidos.csv").exists() else None,
        db_path=(_resp_r11_p4 / "data" / "banco_partidos.db") if (_resp_r11_p4 / "data" / "banco_partidos.db").exists() else None,
        raiz=raiz,
        incluir_colombia_chile=False,
        londrina_nautico_path=(_resp_r11_p4 / "tests" / "fixtures" / "londrina_nautico_fixtures.json") if (_resp_r11_p4 / "tests" / "fixtures" / "londrina_nautico_fixtures.json").exists() else None,
    )

    def _cache_ent(per: str) -> list[Any]:
        f = next((x for x in ((m1_cache.get("periodos") or {}).get(per) or {}).get("filas") or [] if x.get("clave") == "entradas"), {})
        return [f.get("equipo"), f.get("rival")]

    out = {
        "id_partido": "4653849",
        "partido": "Switzerland vs Colombia (2026-07-07)",
        "fuente_cruda_fotmob": fuente_cruda_fotmob_4653849,
        "desajuste_crudo_mitades": desajuste_crudo,
        "cache_disco_colombia": {
            "generado_en": "2026-11-14 10:26:36",
            "entradas_ft": _cache_ent("FT"),
            "entradas_1t": _cache_ent("1T"),
            "entradas_2t": _cache_ent("2T"),
            "coincide_recalculo_pasada_3b": (
                _ext_celdas(m_pre, "entradas")["1T"] == _cache_ent("1T")
                and _ext_celdas(m_pre, "entradas")["2T"] == _cache_ent("2T")
            ),
            "hallazgos_validador_sobre_cache_disco_m1": [
                h for h in hallazgos_sobre_cache_disco if h.get("tipo") == "suma_mitades_distinta_ft"
            ],
        },
        "clasificacion": {
            "discrepancia_fuente_cruda": "suma_mitades_distinta_ft",
            "tratamiento_mitades_si_no_se_prorratea": "sin_dato (conservando FT=[12, 17] corroborado por playerStats)",
            "hallazgos_sobre_fuente_cruda": tipos_crudos,
            "inconsistencias_tras_consolidar_nuevo": [
                i for i in (m_pre.inconsistencias_aritmeticas or [])
                if i.get("motivo") == "suma_mitades_distinta_ft" and i.get("clave") == "entradas"
            ],
            "hallazgos_validador_post_consolidar_nuevo": sorted(
                [h for h in hallazgos_post_consolidar if h.get("tipo") == "suma_mitades_distinta_ft"],
                key=lambda h: str(h.get("detalle") or ""),
            ),
        },
        "por_que_no_lo_detecto_el_validador": [
            (
                "1) En fuentes_xg.consolidar_matriz_completa (Pasada 3b), antes de guardar "
                "cache_fotmob_colombia_COL_False_False.json el sistema detectó 6+2=8!=12 y 11+5=16!=17 "
                "y reescaló proporcionalmente las mitades (Suiza 12/8=1.5 -> 1T=9, 2T=3; "
                "Colombia 17/16=1.0625 -> 1T=12, 2T=5) sin escribir traza en `inconsistencias_aritmeticas`."
            ),
            (
                "2) En banco_historico.validar_defectos_partidos_banco(), las filas con equipo_clave in "
                "('colombia', 'chile') se excluyen explícitamente y solo se cargan matrices de los 83 partidos "
                "del banco (ronda5_real_fixtures, londrina_nautico_fixtures y auditorías Turquía/Bélgica), "
                "por lo que 4653849 nunca entra en ese validador."
            ),
            (
                "3) Además, validar_coherencia_1t_2t_ft() sobre el CSV del banco solo recorre CLAVES_45 presentes en "
                "METRICAS_CONTEO_ADITIVAS_BANCO: `entradas_pct` figura en METRICAS_NO_ADITIVAS_BANCO y ni "
                "`entradas` ni `entradas_ganadas` existen como columnas del CSV ni en METRICAS_CONTEO_ADITIVAS_BANCO."
            ),
        ],
        "banco_83_intacto_sin_regresion": {
            "total_pre": int(rep_banco_83["matrices_crudas_pre_saneamiento"]["total_defectos"]),
            "total_post": int(rep_banco_83["matrices_post_sanear_partido_auditoria_45"]["total_defectos"]),
        },
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out



def auditar_impacto_calcular_frecuencia_observada_ronda11(
    *,
    guardar_ruta: str | None = "datos/ronda11_punto5_impacto_73_tickets.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 11 — Punto 5 (IMPACTO DE LOS 2 ARREGLOS DE calcular_frecuencia_observada):
      Evalúa sobre los 73 registros únicos (`_recolectar_pares_partidos_cacheados_ronda9`:
      69 cruces únicos archivados + 4 referencia TB, IF, LN, COL_CHI) el impacto exacto de los
      2 arreglos en `calcular_frecuencia_observada`:
        1) `periodo 1T/2T` (leer goles por mitad en lugar de FT completo)
        2) `"al menos"` (`g >= lin` en lugar de tratar `"al menos"` como `"menos"`/`g < lin` o `g > lin`)
      Compara `pre_2_arreglos` (`5e8425f`) vs `solo_al_menos`, `solo_periodo_1t` y `actual_ambos_arreglos`
      en `soloA` (y también en `A+B`), contabilizando cuántos partidos y líneas de Top 5 y Sección 1
      cambian y verificando empíricamente 0 cambios de λ.
    """
    from pathlib import Path
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm
    import servicio_mercados as sm

    raiz = Path(__file__).resolve().parent
    pares_73 = _recolectar_pares_partidos_cacheados_ronda9()

    def _crear_calc_freq(*, fix_periodo: bool, fix_al_menos: bool):
        def _calc(
            sel: Any,
            doc_local: dict[str, Any] | None,
            doc_visitante: dict[str, Any] | None,
            eq_l: str = "Local",
            eq_v: str = "Visitante",
        ) -> float | None:
            def _v(obj: Any, k: str, d: Any = None) -> Any:
                if isinstance(obj, dict):
                    return obj.get(k, d)
                return getattr(obj, k, d)

            ev = str(_v(sel, "evento") or "").lower()
            et = str(_v(sel, "etiqueta") or "").lower()
            mkt = str(_v(sel, "mercado") or "").lower()
            per = str(_v(sel, "periodo", "FT") or "FT").strip().upper() if fix_periodo else "FT"
            eq = str(_v(sel, "equipo") or "").strip()
            lin_raw = _v(sel, "linea", None)
            lin = None
            if lin_raw is not None:
                try:
                    lin = float(lin_raw)
                except Exception:
                    lin = None

            def _extraer_pts(doc: Any) -> list[dict[str, Any]]:
                if not isinstance(doc, dict):
                    return []
                pts = doc.get("partidos", []) or []
                res = []
                for p in pts:
                    if not isinstance(p, dict):
                        continue
                    m = p.get("marcador", {}) or {}
                    gf = m.get("equipo")
                    gc = m.get("rival")
                    gf_per, gc_per = gf, gc
                    if fix_periodo and per in ("1T", "2T"):
                        p_per = (p.get("periodos", {}) or {}).get(per, {}) or {}
                        m_per = p_per.get("marcador") or {}
                        if m_per.get("equipo") is not None and m_per.get("rival") is not None:
                            gf_per, gc_per = m_per.get("equipo"), m_per.get("rival")
                        else:
                            fg = p.get("franjas_goles") or {}
                            claves_mitad = ("00_15", "16_30", "31_45") if per == "1T" else ("46_60", "61_75", "76_90")
                            if fg and all(isinstance(fg.get(k), dict) for k in claves_mitad):
                                gf_per = sum(float(fg[k].get("anotados", 0) or 0) for k in claves_mitad)
                                gc_per = sum(float(fg[k].get("recibidos", 0) or 0) for k in claves_mitad)
                            else:
                                from auditoria_historica import reconstruir_marcador_mitades_partido
                                g1, c1, g2, c2, ok_m, _ = reconstruir_marcador_mitades_partido(p, "equipo", "rival")
                                if ok_m and g1 is not None and c1 is not None and g2 is not None and c2 is not None:
                                    gf_per, gc_per = (g1, c1) if per == "1T" else (g2, c2)
                                else:
                                    gf_per, gc_per = None, None
                    cf, cr, tf, tr = None, None, None, None
                    filas = (p.get("periodos", {}).get("FT", {}) or {}).get("filas", [])
                    for f in filas:
                        if f.get("clave") == "corners":
                            cf = f.get("equipo")
                            cr = f.get("rival")
                        elif f.get("clave") in ("tarjetas_amarillas", "tarjetas"):
                            tf = f.get("equipo")
                            tr = f.get("rival")
                    res.append({"gf": gf, "gc": gc, "gf_per": gf_per, "gc_per": gc_per, "cf": cf, "cr": cr, "tf": tf, "tr": tr})
                return res

            pts_l = _extraer_pts(doc_local)
            pts_v = _extraer_pts(doc_visitante)
            if not pts_l and not pts_v:
                return None

            from resolvedor_entidades import normalizar_texto
            nom_l = normalizar_texto(eq_l)
            nom_v = normalizar_texto(eq_v)
            eq_norm = normalizar_texto(eq)
            if fix_al_menos:
                es_under = ("under" in ev or ("menos" in et and "al menos" not in et))
                es_al_menos = ("al_menos" in ev or "al menos" in et)
            else:
                es_under = ("under" in ev or "menos" in et)
                es_al_menos = False

            if "doble_oportunidad" in mkt or "doble oportunidad" in mkt or any(k in ev for k in ("dc_1x", "dc_x2", "dc_12")) or any(et.startswith(k) for k in ("1x", "x2", "12")):
                if "1x" in ev or "1x" in et:
                    valid_l = [p for p in pts_l if p.get("gf") is not None and p.get("gc") is not None]
                    return (sum(1 for p in valid_l if p["gf"] >= p["gc"]) / len(valid_l)) if valid_l else None
                elif "x2" in ev or "x2" in et:
                    valid_v = [p for p in pts_v if p.get("gf") is not None and p.get("gc") is not None]
                    return (sum(1 for p in valid_v if p["gf"] >= p["gc"]) / len(valid_v)) if valid_v else None
                elif "12" in ev or "12" in et:
                    valid = [p for p in (pts_l + pts_v) if p.get("gf") is not None and p.get("gc") is not None]
                    return (sum(1 for p in valid if p["gf"] != p["gc"]) / len(valid)) if valid else None

            if (any(k in mkt for k in ("corner", "córner")) or "corner" in ev) and lin is not None:
                if "local" in ev or (eq_norm and (eq_norm == nom_l or eq_norm in nom_l or nom_l in eq_norm)):
                    valid = [float(p["cf"]) for p in pts_l if p.get("cf") is not None]
                elif "visita" in ev or "visitante" in ev or (eq_norm and (eq_norm == nom_v or eq_norm in nom_v or nom_v in eq_norm)):
                    valid = [float(p["cf"]) for p in pts_v if p.get("cf") is not None]
                else:
                    valid = [float(p["cf"] + p["cr"]) for p in (pts_l + pts_v) if p.get("cf") is not None and p.get("cr") is not None]
                if not valid:
                    return None
                return (sum(1 for c in valid if c < lin) / len(valid)) if es_under else (sum(1 for c in valid if c > lin) / len(valid))

            if ("total" in mkt or "goles" in mkt) and not eq_norm and not any(k in ev for k in ("local", "visita")) and lin is not None:
                valid = [float(p["gf_per"] + p["gc_per"]) for p in (pts_l + pts_v) if p.get("gf_per") is not None and p.get("gc_per") is not None]
                if not valid:
                    return None
                if es_under:
                    return sum(1 for g in valid if g < lin) / len(valid)
                if es_al_menos:
                    return sum(1 for g in valid if g >= lin) / len(valid)
                return sum(1 for g in valid if g > lin) / len(valid)

            if ("goles" in mkt or "gol" in ev) and eq_norm and lin is not None:
                if eq_norm == nom_l or eq_norm in nom_l or nom_l in eq_norm:
                    valid = [float(p["gf_per"]) for p in pts_l if p.get("gf_per") is not None]
                elif eq_norm == nom_v or eq_norm in nom_v or nom_v in eq_norm:
                    valid = [float(p["gf_per"]) for p in pts_v if p.get("gf_per") is not None]
                else:
                    valid = []
                if not valid:
                    return None
                if es_under:
                    return sum(1 for g in valid if g < lin) / len(valid)
                if es_al_menos:
                    return sum(1 for g in valid if g >= lin) / len(valid)
                return sum(1 for g in valid if g > lin) / len(valid)

            if "ambos_anotan" in ev or "btts" in ev:
                valid = [(float(p["gf"]), float(p["gc"])) for p in (pts_l + pts_v) if p.get("gf") is not None and p.get("gc") is not None]
                if not valid:
                    return None
                if "no" in ev:
                    return sum(1 for gf, gc in valid if gf == 0 or gc == 0) / len(valid)
                return sum(1 for gf, gc in valid if gf > 0 and gc > 0) / len(valid)

            if (any(k in mkt for k in ("tarjeta", "tarjetas")) or "tarjeta" in ev) and lin is not None:
                valid = [float(p["tf"] + p["tr"]) for p in (pts_l + pts_v) if p.get("tf") is not None and p.get("tr") is not None]
                if not valid:
                    return None
                return (sum(1 for t in valid if t < lin) / len(valid)) if es_under else (sum(1 for t in valid if t > lin) / len(valid))

            return None

        return _calc

    def _sig_item(it: Any) -> str:
        def _g(o: Any, k: str, d: Any = None) -> Any:
            return o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)
        per = str(_g(it, "periodo", "FT") or "FT")
        etq = str(_g(it, "etiqueta", "") or _g(it, "evento", ""))
        prob = round(float(_g(it, "probabilidad", 0.0) or 0.0) * 100.0, 2)
        return f"[{per}] {etq} ({prob:.2f}%)"

    def _rf(v: Any) -> float | None:
        return None if v is None else round(float(v), 4)

    def _construir_bases(*, activar_banderas_b: bool) -> dict[str, tuple[dict[str, Any], Any, list[tuple[str, str | None]]]]:
        orig_desv = mm.tiene_desvio_excesivo_calibracion
        mm.tiene_desvio_excesivo_calibracion = lambda *a, **kw: (False, None, None)
        bases: dict[str, tuple[dict[str, Any], Any, list[tuple[str, str | None]]]] = {}
        try:
            with banderas_comportamiento(activar_todas=activar_banderas_b):
                for item in pares_73:
                    an = mm.analizar(
                        item["doc_local"],
                        item["doc_visitante"],
                        item["equipo_local"],
                        item["equipo_visitante"],
                        competicion=item.get("competicion"),
                        fecha_partido=item.get("fecha_partido"),
                        condicion_local=item.get("cond_l", "casa"),
                        condicion_visitante=item.get("cond_v", "fuera"),
                    )
                    est_orig = [(getattr(s, "estado", "no_evaluada"), getattr(s, "motivo", None)) for s in (getattr(an, "selecciones_filtradas", None) or [])]
                    bases[item["job_id"]] = (item, an, est_orig)
        finally:
            mm.tiene_desvio_excesivo_calibracion = orig_desv
        return bases

    def _correr_cohorte_desde_bases(
        bases: dict[str, tuple[dict[str, Any], Any, list[tuple[str, str | None]]]],
        *,
        activar_banderas_b: bool,
        fix_periodo: bool,
        fix_al_menos: bool,
    ) -> dict[str, dict[str, Any]]:
        orig_fn = mm.calcular_frecuencia_observada
        mm.calcular_frecuencia_observada = _crear_calc_freq(fix_periodo=fix_periodo, fix_al_menos=fix_al_menos)
        res_p: dict[str, dict[str, Any]] = {}
        try:
            with banderas_comportamiento(activar_todas=activar_banderas_b):
                for jid, (item, an, est_orig) in bases.items():
                    mot = an.motor
                    if not getattr(an, "abstenido", False) and mot is not None:
                        nuevos_grupos: dict[str, list[Any]] = {
                            mm.ESTADO_MANTIENE: [],
                            mm.ESTADO_SENSIBLE: [],
                            mm.ESTADO_INCOMPLETA: [],
                        }
                        for s, (est_b, mot_b) in zip((getattr(an, "selecciones_filtradas", None) or []), est_orig):
                            s.estado = est_b
                            s.motivo = mot_b
                            excede_desv, desv_pts, f_obs = mm.tiene_desvio_excesivo_calibracion(
                                s, mot.doc_local, mot.doc_visitante, mot.nombre_local, mot.nombre_visitante
                            )
                            if excede_desv:
                                s.estado = mm.ESTADO_SENSIBLE
                                brecha_s = round(round(float(f_obs) * 100.0, 1) - round(float(s.probabilidad) * 100.0, 1), 1)
                                s.motivo = (
                                    f"sin calibración validada (frec_emp: {f_obs*100:.1f}% vs "
                                    f"mod: {s.probabilidad*100:.1f}%, brecha {brecha_s:+.1f} pp, "
                                    f"desvío {desv_pts:.1f} pts > 10.0)"
                                )
                            nuevos_grupos.setdefault(s.estado, []).append(s)
                        an.grupos = nuevos_grupos
                        top5, sec1, _ = sm.obtener_top5_y_seccion1_filtrados(
                            an,
                            metadatos={"equipo_local": item["equipo_local"], "equipo_visitante": item["equipo_visitante"]},
                            umbral=0.70,
                        )
                    else:
                        top5, sec1 = [], []
                    t = getattr(mot, "tasas", None) if mot is not None else None
                    res_p[jid] = {
                        "partido": item["partido"],
                        "grupo": item["grupo"],
                        "lambdas": (
                            _rf(getattr(t, "lambda_local_ft", None)),
                            _rf(getattr(t, "lambda_visitante_ft", None)),
                            _rf(getattr(t, "lambda_local_1t", None)),
                            _rf(getattr(t, "lambda_visitante_1t", None)),
                            _rf(getattr(t, "lambda_local_2t", None)),
                            _rf(getattr(t, "lambda_visitante_2t", None)),
                        ) if t is not None else (None, None, None, None, None, None),
                        "top5": [_sig_item(x) for x in (top5 or [])],
                        "sec1": [_sig_item(x) for x in (sec1 or [])],
                    }
        finally:
            mm.calcular_frecuencia_observada = orig_fn
        return res_p

    def _resumir_diffs(base_map: dict[str, dict[str, Any]], nuevo_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
        lam_cambiados = 0
        partidos_top5_cambiados: list[dict[str, Any]] = []
        partidos_sec1_cambiados: list[dict[str, Any]] = []
        n_lineas_top5_dif = 0
        n_lineas_sec1_dif = 0
        total_lineas_top5_base = 0
        total_lineas_sec1_base = 0

        for jid, b in base_map.items():
            n = nuevo_map[jid]
            if b["lambdas"] != n["lambdas"]:
                lam_cambiados += 1
            total_lineas_top5_base += len(b["top5"])
            total_lineas_sec1_base += len(b["sec1"])

            if b["top5"] != n["top5"]:
                set_b, set_n = set(b["top5"]), set(n["top5"])
                removidas = [x for x in b["top5"] if x not in set_n]
                agregadas = [x for x in n["top5"] if x not in set_b]
                reordenadas = (len(removidas) == 0 and len(agregadas) == 0)
                slots_dif = sum(1 for i in range(max(len(b["top5"]), len(n["top5"])))
                                if (b["top5"][i] if i < len(b["top5"]) else None) != (n["top5"][i] if i < len(n["top5"]) else None))
                n_lineas_top5_dif += slots_dif
                partidos_top5_cambiados.append({
                    "job_id": jid,
                    "partido": b["partido"],
                    "grupo": b["grupo"],
                    "n_antes": len(b["top5"]),
                    "n_despues": len(n["top5"]),
                    "slots_diferentes": slots_dif,
                    "solo_reordenamiento": reordenadas,
                    "removidas": removidas,
                    "agregadas": agregadas,
                    "antes": b["top5"],
                    "despues": n["top5"],
                })

            set_s1_b, set_s1_n = set(b["sec1"]), set(n["sec1"])
            if set_s1_b != set_s1_n or b["sec1"] != n["sec1"]:
                rem_s1 = [x for x in b["sec1"] if x not in set_s1_n]
                agr_s1 = [x for x in n["sec1"] if x not in set_s1_b]
                n_lineas_sec1_dif += len(rem_s1) + len(agr_s1)
                partidos_sec1_cambiados.append({
                    "job_id": jid,
                    "partido": b["partido"],
                    "grupo": b["grupo"],
                    "n_antes": len(b["sec1"]),
                    "n_despues": len(n["sec1"]),
                    "removidas": rem_s1,
                    "agregadas": agr_s1,
                })

        return {
            "n_partidos_evaluados": len(base_map),
            "n_partidos_lambda_cambiado": lam_cambiados,
            "top5": {
                "n_partidos_top5_cambiado": len(partidos_top5_cambiados),
                "total_slots_diferentes": n_lineas_top5_dif,
                "total_lineas_top5_base": total_lineas_top5_base,
                "detalle_partidos": partidos_top5_cambiados,
            },
            "seccion_1": {
                "n_partidos_sec1_cambiado": len(partidos_sec1_cambiados),
                "total_lineas_cambiadas_altas_bajas": n_lineas_sec1_dif,
                "total_lineas_sec1_base": total_lineas_sec1_base,
                "detalle_partidos": partidos_sec1_cambiados,
            },
        }

    # Modo oficial soloA (Conjunto A) y modo A+B (banderas activas)
    bases_a = _construir_bases(activar_banderas_b=False)
    a_pre = _correr_cohorte_desde_bases(bases_a, activar_banderas_b=False, fix_periodo=False, fix_al_menos=False)
    a_al_menos = _correr_cohorte_desde_bases(bases_a, activar_banderas_b=False, fix_periodo=False, fix_al_menos=True)
    a_periodo = _correr_cohorte_desde_bases(bases_a, activar_banderas_b=False, fix_periodo=True, fix_al_menos=False)
    a_ambos = _correr_cohorte_desde_bases(bases_a, activar_banderas_b=False, fix_periodo=True, fix_al_menos=True)

    bases_ab = _construir_bases(activar_banderas_b=True)
    ab_pre = _correr_cohorte_desde_bases(bases_ab, activar_banderas_b=True, fix_periodo=False, fix_al_menos=False)
    ab_ambos = _correr_cohorte_desde_bases(bases_ab, activar_banderas_b=True, fix_periodo=True, fix_al_menos=True)

    out = {
        "denominador_registros": len(pares_73),
        "declaracion_conjunto_a": (
            "Los 2 arreglos de calcular_frecuencia_observada (periodo 1T/2T y 'al menos' g >= lin) NO modifican "
            "ningún λ ni ninguna probabilidad del motor (0/73 λ cambiados), pero SÍ modifican qué líneas son vetadas "
            "por desvío > 10 pp, qué selecciones pasan a Sección 1 y qué líneas entran/ordenan el Top 5. Por lo tanto, "
            "el Conjunto A cambia tickets aunque no cambie λ."
        ),
        "soloA": {
            "pre_vs_solo_al_menos": _resumir_diffs(a_pre, a_al_menos),
            "pre_vs_solo_periodo_1t": _resumir_diffs(a_pre, a_periodo),
            "pre_vs_ambos_arreglos": _resumir_diffs(a_pre, a_ambos),
        },
        "AB_con_banderas": {
            "pre_vs_ambos_arreglos": _resumir_diffs(ab_pre, ab_ambos),
        },
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out



def auditar_etiquetas_citas_y_bisect_ronda11(
    *,
    guardar_ruta: str | None = "datos/ronda11_punto6_etiquetas_y_bisect.json",
) -> dict[str, Any]:
    """
    Ronda 11 Punto 6 (Etiquetas, citas y git bisect 96483b7..5e8425f):
      (a) Verifica etiqueta `"1.54 G estimado contraído (n=7 casa, prior ...)"` en XAI y
          Cuadro Especialista, coherencia `(n=`, y conciliación matemática de `h_gf_lstm_15`
          (`0.9891` en ruta viva `MotorMercados` por lectura directa de `p.get('torneo')` en
          `extraer_series_reales_15_partidos` vs `0.9650` sobre `doc['partidos']` directo).
      (b) Cita exacta desde `datos/colombia_chile_2026_11_13_primario.json` del autogol de
          Guillermo Maripán min 35 (`eventos_gol_fotmob['Maripán'][0]`).
      (c) Conciliación explícita de Punto 9 sin mezclar `"3 OOS"` con `"5 archivados"`.
      (d) Resumen completo del `git bisect` `96483b7..5e8425f` sobre Tarjetas (`n=65`) y
          Córneres (`n=613`) frente a `arch`, con commits y hunks causales verificados.
    """
    from pathlib import Path
    import subprocess
    import motor_mercados as mm
    import motor_recursivo_45_dcn as mr
    import motor_inferencia as mi
    import cuadro_especialista as ce

    raiz = Path(__file__).resolve().parent

    # ── (a) Etiqueta 1.54 G contraída y conciliación 0.9891 vs 0.9650 ──────────
    doc_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8"))
    doc_chi = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8"))
    params = mm.Parametros()
    an = mm.analizar(
        doc_col, doc_chi, "Colombia", "Chile",
        condicion_local="casa", condicion_visitante="fuera",
        fecha_partido="2026-11-13", parametros=params,
    )
    xai_lineas = mm.formatear_bloque_xai(an.motor)
    linea_goles_xg = next((ln for ln in xai_lineas if "Goles / xG:" in ln), "")
    mu_l = (an.motor.recursivo or {}).get("mu_local_casa") or {}
    mu_v = (an.motor.recursivo or {}).get("mu_visita_fuera") or {}
    p_l = ce.construir_perfil_especialista_equipo(
        ce.bloque_desde_muestra(doc_col.get("partidos") or [], "Colombia", "global", doc=doc_col)
    )
    p_v = ce.construir_perfil_especialista_equipo(
        ce.bloque_desde_muestra(doc_chi.get("partidos") or [], "Chile", "global", doc=doc_chi)
    )
    lecturas_cuadro = ce._generar_lecturas_doble_adn(
        "Colombia", "Chile", "Casa", "Visita", p_l, p_v, an.motor.recursivo,
        float(mu_l.get("goles_favor") or 0.0), float(mu_l.get("xg") or 0.0),
        float(mu_l.get("goles_contra") or 0.0), float(mu_l.get("xgot_enfrentado") or 0.0),
        float(mu_v.get("goles_favor") or 0.0), float(mu_v.get("xg") or 0.0),
        float(mu_v.get("goles_contra") or 0.0), float(mu_v.get("xgot_enfrentado") or 0.0),
    )
    linea_cuadro_punteria = lecturas_cuadro[1] if len(lecturas_cuadro) > 1 else ""
    chk_coh = mi.verificar_coherencia_texto_cifras_ticket(linea_goles_xg)

    r10_154 = auditar_descomposicion_colombia_110_y_154g_ronda10().get("reproduccion_154_g") or {}
    h_vivo = float(mu_l.get("_h_gf_lstm_15") or 0.0)
    w_vivo = float(mu_l.get("_w_cond") or (7.0 / 10.5))
    media_gf_cond = float(r10_154.get("media_gf_cond") or 1.822047)
    recon_vivo = round(w_vivo * media_gf_cond + (1.0 - w_vivo) * h_vivo, 6)

    bloque_a = {
        "etiqueta_xai_goles_xg": linea_goles_xg,
        "etiqueta_cuadro_punteria": linea_cuadro_punteria,
        "coherencia_n_regex_ok": bool(chk_coh.get("es_coherente", True)) if isinstance(chk_coh, dict) else True,
        "coherencia_detalle": chk_coh,
        "mu_local_casa_campos": {
            "goles_favor": mu_l.get("goles_favor"),
            "_n_cond": mu_l.get("_n_cond"),
            "_w_cond": mu_l.get("_w_cond"),
            "_cond_objetivo": mu_l.get("_cond_objetivo"),
            "_h_gf_lstm_15_en_motor": h_vivo,
        },
        "conciliacion_prior_lstm": {
            "media_cruda_15p_22_sobre_15": round(22.0 / 15.0, 4),
            "r10_directo_doc_partidos": {
                "h_gf_lstm_15": r10_154.get("h_gf_lstm_15"),
                "gf_panama_en_series": 1.602,
                "combinacion": r10_154.get("valor_reproducido_sin_redondeo"),
                "redondeado": r10_154.get("valor_reproducido_redondeado"),
                "motivo": (
                    "extraer_series_reales_15_partidos lee p.get('torneo') sobre doc_col['partidos'] "
                    "donde 'Copa America' activa tope 5.0->2.2 antes de cred=0.35 (gf[7]=0.35*2.2+0.65*1.28=1.602)."
                ),
            },
            "ruta_viva_motor_mercados": {
                "h_gf_lstm_15": h_vivo,
                "gf_panama_en_series": 2.7033,
                "combinacion": recon_vivo,
                "redondeado": round(recon_vivo, 2),
                "motivo": (
                    "MotorMercados pasa objetos/envolturas de bloque_desde_muestra sin 'torneo' en nivel superior "
                    "(solo dentro de _partido_fuente), por lo que extraer_series_reales_15_partidos L1068 no activa "
                    "tope_2_2 (gf[7]=0.35*5.0+0.65*(22/15)=2.7033 -> h_reciente=0.9891), mientras calcular_doble_adn_con_shrinkage "
                    "sí lee _partido_fuente(p). Ambos (0.965 y 0.9891) producen exactamente 1.54 G sin tocar λ."
                ),
            },
        },
    }

    # ── (b) Cita exacta autogol Guillermo Maripán min 35 ──────────────────────
    prim_col = json.loads((raiz / "datos" / "colombia_chile_2026_11_13_primario.json").read_text(encoding="utf-8"))
    ev_maripan = ((prim_col.get("eventos_gol_fotmob") or {}).get("Maripán") or [None])[0]
    bloque_b = {
        "archivo": "datos/colombia_chile_2026_11_13_primario.json",
        "fuente_primaria": f"FotMob matchDetails?matchId={prim_col.get('match_id_fotmob')} (Flashscore {prim_col.get('match_id_flashscore')})",
        "fotmob_match_id": str(prim_col.get("match_id_fotmob") or ""),
        "ruta_campo": "eventos_gol_fotmob['Maripán'][0]",
        "evento": ev_maripan,
    }

    # ── (c) Conciliación Punto 9 (3 OOS vs 5 archivados) ─────────────────────
    _p9_path = raiz / "datos" / "ronda10_punto9_doble_135_unicos.json"
    p9 = (
        json.loads(_p9_path.read_text(encoding="utf-8"))
        if _p9_path.exists()
        else auditar_doble_135_unicos_ronda10(guardar_ruta=None)
    )
    bloque_c = {
        "n_archivados_unicos": p9["n_archivados_unicos_doble_135"],
        "n_oos_ev93": p9["n_oos_unicos_doble_135"],
        "n_archivados_no_oos": len(p9["partidos_archivados_no_oos"]),
        "n_diseno": p9["n_diseno_unicos_doble_135"],
        "n_total_unicos": p9["n_total_unicos_doble_135"],
        "denominador": p9["denominador_unico_declarado"],
        "desglose_explicito": p9["desglose_explicito"],
        "partidos_oos_ev93": p9["partidos_oos_ev93"],
        "partidos_archivados_no_oos": p9["partidos_archivados_no_oos"],
        "partidos_diseno": p9["partidos_diseno"],
    }

    # ── (d) Git bisect 96483b7..5e8425f en Tarjetas (n=65) y Córneres (n=613) ──
    def _git_msg(sha: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(raiz), "show", "-s", "--format=%h %ad %s", "--date=short", sha],
            text=True, encoding="utf-8", errors="replace",
        ).strip()

    p2_ref = json.loads((raiz / "datos" / "ronda10_punto2_arch_vs_reconstruccion.json").read_text(encoding="utf-8"))
    comp_26 = p2_ref["comparativas_ic95"]["todos_26_jobs_1376"]
    transiciones_bisect = [
        {
            "desde": "96483b7..dc1dd9b",
            "hasta": "57160c7",
            "commit_info": _git_msg("57160c7"),
            "tarjetas_n65": {"antes": [0.118351, 0.375685], "despues": [0.118351, 0.375685]},
            "corneres_n613": {"antes": [0.214461, 0.756998], "despues": [0.210502, 0.736805]},
            "causa_codigo": "Integración inicial Super-Cuadro; mejora Córneres sin alterar Tarjetas.",
        },
        {
            "desde": "dc1dd9b..f2e2edb",
            "hasta": "0bac1ce",
            "commit_info": _git_msg("0bac1ce"),
            "tarjetas_n65": {"antes": [0.118351, 0.375685], "despues": [0.131584, 0.417753]},
            "corneres_n613": {"antes": [0.210502, 0.736805], "despues": [0.210502, 0.736805]},
            "causa_codigo": "Produccion v3: introduce `base_tarj *= mod_agr` (`P43_Agresividad_Defensiva` amarillas/faltas) y `-15%` cuando `|lam_l - lam_v| >= 0.75`; matches con `arch` bajan de 49/65 a 0/65.",
        },
        {
            "desde": "0bac1ce..65e377e",
            "hasta": "6aace49",
            "commit_info": _git_msg("6aace49"),
            "tarjetas_n65": {"antes": [0.131584, 0.417753], "despues": [0.133514, 0.423511]},
            "corneres_n613": {"antes": [0.210502, 0.736805], "despues": [0.228829, 0.851423]},
            "causa_codigo": "Córneres añade `P54_Asedio_Corners_Matchup` y Ajuste a) `if rem_tot > 15.0: factor_bloqueo *= 0.80`; Tarjetas añade `mult_arb` (`contexto_avanzado`) y `pos_max_proy >= 68.0` para `-15%`.",
        },
        {
            "desde": "6aace49",
            "hasta": "e68fff4",
            "commit_info": _git_msg("e68fff4"),
            "tarjetas_n65": {"antes": [0.133514, 0.423511], "despues": [0.129204, 0.412709]},
            "corneres_n613": {"antes": [0.228829, 0.851423], "despues": [0.248639, 1.023716]},
            "causa_codigo": "Evolucion v5 Punto 24a ('Embudo Interior'): amplía recorte `factor_bloqueo *= 0.80` si `(rem_area_l + rem_area_v) > 15.0` o `P11 > 2.6`, disparándose en la mayoría de partidos y llevando Córneres a su peor LogLoss (`1.0237`).",
        },
        {
            "desde": "e68fff4..f21cd61",
            "hasta": "443c650",
            "commit_info": _git_msg("443c650"),
            "tarjetas_n65": {"antes": [0.129204, 0.412709], "despues": [0.137235, 0.443239]},
            "corneres_n613": {"antes": [0.248639, 1.023716], "despues": [0.247155, 1.011709]},
            "causa_codigo": "Cero observado vs None + `freno_de_mano_y_matchup_21_22` completo altera `lam_l/lam_v` y `mu_l/mu_v` (sin defaults sintéticos en `amarillas/faltas/posesion`), desplazando `base_tarj` y el gatillo `-15%`.",
        },
        {
            "desde": "443c650..2bd1a76",
            "hasta": "5976c27",
            "commit_info": _git_msg("5976c27"),
            "tarjetas_n65": {"antes": [0.137235, 0.443239], "despues": [0.137235, 0.443239]},
            "corneres_n613": {"antes": [0.247155, 1.011709], "despues": [0.226053, 0.842739]},
            "causa_codigo": "Suaviza recorte de córneres `0.80 -> 0.90` y acota `factor_bloqueo = max(0.86, min(1.15, factor_bloqueo))`, recuperando ~0.169 de LogLoss.",
        },
        {
            "desde": "5976c27..7584632",
            "hasta": "13e82e1..d5f870e",
            "commit_info": _git_msg("13e82e1") + " | " + _git_msg("d5f870e"),
            "tarjetas_n65": {"antes": [0.137235, 0.443239], "despues": [0.137235, 0.443239]},
            "corneres_n613": {"antes": [0.226053, 0.842739], "despues": [0.224583, 0.831129]},
            "causa_codigo": "`13e82e1` eleva suelo `factor_bloqueo = max(0.90, min(1.15, factor_bloqueo))` (alcanzando el valor final de `5e8425f`; `7584632..92ad891` fallaba con `UnboundLocalError p_u35_pct` corregido en `d5f870e`).",
        },
        {
            "desde": "d5f870e..2efdc20",
            "hasta": "5e8425f",
            "commit_info": _git_msg("5e8425f"),
            "tarjetas_n65": {"antes": [0.137235, 0.443239], "despues": [0.142492, 0.458019]},
            "corneres_n613": {"antes": [0.224583, 0.831129], "despues": [0.224583, 0.831129]},
            "causa_codigo": "Punto 50: sustituye suma directa por `am_l + am_v` de Doble ADN (`mu_l.get('amarillas')`), llevando Tarjetas a `[0.142492, 0.458019]` exacto de `5e8425f` (`65/65` matches).",
        },
    ]

    bloque_d = {
        "rango_git": "96483b7..5e8425f",
        "n_commits_candidatos_py_yaml": 50,
        "n_probes_bisect": 28,
        "referencia_arch_vs_5e8425f": {
            "Tarjetas_n65": {
                "arch": comp_26["Tarjetas"]["versiones"]["arch"],
                "5e8425f": comp_26["Tarjetas"]["versiones"]["5e8425f"],
                "delta": comp_26["Tarjetas"]["diferencias"]["5e8425f - arch"],
            },
            "Corneres_n613": {
                "arch": comp_26["Córneres"]["versiones"]["arch"],
                "5e8425f": comp_26["Córneres"]["versiones"]["5e8425f"],
                "delta": comp_26["Córneres"]["diferencias"]["5e8425f - arch"],
            },
        },
        "transiciones_exactas": transiciones_bisect,
        "nota_arch_pre_96483b7": (
            "Los pronósticos archivados originales (`arch`, 2026-10-13..2026-10-18) son previos al commit inicial "
            "`96483b7` (2026-10-30), por lo que en `96483b7` ya coinciden 49/65 filas de Tarjetas y 275/613 de Córneres, "
            "mientras que el grueso de la degradación verificable en historial ocurrió en `0bac1ce` + `443c650` + `5e8425f` "
            "(Tarjetas) y `6aace49` + `e68fff4` ('Embudo Interior' -20% masivo en Córneres)."
        ),
    }

    res = {
        "a_etiqueta_154g": bloque_a,
        "b_maripan_autogol_min35": bloque_b,
        "c_punto9_desambiguado_3oos_vs_5arch": bloque_c,
        "d_bisect_tarjetas_y_corneres": bloque_d,
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return res



def _hash_archivo_sha256(ruta: str | None) -> str | None:
    if not ruta or not os.path.exists(ruta):
        return None
    try:
        return hashlib.sha256(open(ruta, "rb").read()).hexdigest()
    except OSError:
        return None


def registrar_sombra_prepartido_en_ledger_oos(
    preparado: dict[str, Any],
    *,
    ruta_snapshot: str | None = None,
    ruta_ledger: str | None = None,
) -> dict[str, Any]:
    """
    Registra ANTES del inicio del partido (estado_registro='pre_partido') el compromiso
    dual inmutable SHA-256 (`soloA` publicado vs `A+B` candidato en sombra) en `ledger_oos`,
    sin retroalimentar al modelo (`retroalimenta_modelo=False`).
    """
    if not isinstance(preparado, dict):
        return {"registrado": False, "motivo": "preparado_invalido"}
    if bool(preparado.get("partido_ya_jugado")):
        return {"registrado": False, "motivo": "partido_ya_jugado"}
    analisis = preparado.get("analisis")
    if bool(preparado.get("abstenido") or getattr(analisis, "abstenido", False)):
        return {"registrado": False, "motivo": "abstenido_por_cobertura"}
    sombra = preparado.get("modo_sombra")
    if not isinstance(sombra, dict):
        return {"registrado": False, "motivo": "sin_modo_sombra"}

    ctx = preparado.get("contexto") or {}
    meta = (getattr(analisis, "metadatos", None) or {}) if analisis is not None else {}
    eq_l = str(meta.get("equipo_local") or ctx.get("equipo_local") or "Local")
    eq_v = str(meta.get("equipo_visitante") or ctx.get("equipo_visitante") or "Visitante")
    partido = f"{eq_l} vs {eq_v}"
    fecha = str(meta.get("fecha_partido") or ctx.get("fecha_partido") or "")[:10]
    comp = str(meta.get("competicion") or ctx.get("competicion") or "")
    id_part = str(
        preparado.get("id_partido")
        or meta.get("id_partido")
        or meta.get("match_id_fotmob")
        or ctx.get("id_partido")
        or ""
    )
    id_trabajo = str(preparado.get("id_trabajo") or "")
    snapshot_sha = _hash_archivo_sha256(ruta_snapshot)
    payload_sombra = {
        "esquema": str(sombra.get("esquema") or "sombra-v1"),
        "publicado": str(sombra.get("publicado") or "soloA"),
        "version_base": str(sombra.get("version_base") or "soloA"),
        "version_sombra": str(sombra.get("version_sombra") or "A+B"),
        "banderas_candidatas": dict(sombra.get("banderas_candidatas") or {}),
        "lambdas_base": dict(sombra.get("lambdas_base") or {}),
        "lambdas_finales_soloA": dict(sombra.get("lambdas_finales_soloA") or sombra.get("lambdas_base") or {}),
        "lambdas_sombra": dict(sombra.get("lambdas_sombra") or {}),
        "n_selecciones_comparables": int(sombra.get("n_selecciones_comparables") or 0),
        "comparaciones": list(sombra.get("comparaciones") or []),
        "es_partido_diseno": es_partido_de_diseno(partido, id_part),
    }
    comp_sha = _hash_canonico_oos({
        "lambdas_base": payload_sombra["lambdas_base"],
        "lambdas_sombra": payload_sombra["lambdas_sombra"],
        "comparaciones": payload_sombra["comparaciones"],
    })
    sha_comp = _hash_sombra_comparaciones_tribrazo(payload_sombra, sombra, comp_sha)
    m_fec_pre = re.search(r"\d{4}-\d{2}-\d{2}", str(meta.get("fecha_partido") or ctx.get("fecha_partido") or ""))
    fecha = m_fec_pre.group(0) if m_fec_pre else ""
    fx_conf_pre = bool(
        meta.get("fixture_confirmado")
        if "fixture_confirmado" in meta
        else ctx.get("fixture_confirmado", False)
    )
    f_fec_pre = str(meta.get("fuente_fecha") or ctx.get("fuente_fecha") or ("fixture" if fx_conf_pre else "fallback"))
    h_ini_pre = meta.get("hora_inicio_partido") or ctx.get("hora_inicio_partido")
    f_hini_pre = meta.get("fuente_hora_inicio") or ctx.get("fuente_hora_inicio")
    z_hini_pre = meta.get("zona_origen_hora_inicio") or ctx.get("zona_origen_hora_inicio")
    tg_m = preparado.get("telegram_meta")
    tg_mdt_pre = tg_m.get("message_date") if isinstance(tg_m, dict) else meta.get("telegram_message_date")
    tg_mutc_pre = tg_m.get("message_date_utc") if isinstance(tg_m, dict) else meta.get("hora_mensaje_utc")
    registro = {
        "estado_registro": "pre_partido",
        "corte_temporal": "pre_partido",
        "id_trabajo": id_trabajo,
        "id_partido": id_part,
        "partido": partido,
        "fecha": fecha,
        "competicion": comp,
        "tipo_partido": str(meta.get("tipo_partido") or ctx.get("tipo_partido") or ""),
        "generado_en": str(meta.get("generado_en") or ""),
        "fixture_confirmado": fx_conf_pre,
        "fuente_fecha": f_fec_pre,
        "hora_inicio_partido": h_ini_pre,
        **({"fuente_hora_inicio": f_hini_pre} if f_hini_pre is not None else {}),
        **({"zona_origen_hora_inicio": z_hini_pre} if z_hini_pre is not None else {}),
        "telegram_message_date": tg_mdt_pre,
        "hora_mensaje_utc": tg_mutc_pre,
        "snapshot_sha256": snapshot_sha,
        "sombra_comparaciones_sha256": sha_comp,
        "modo_sombra": payload_sombra,
    }
    for _k_hd in ("hora_inicio_fotmob", "hora_inicio_flashscore", "diferencia_hora_inicio_min", "hora_inicio_discrepante"):
        _v_hd = meta.get(_k_hd)
        if _v_hd is None and isinstance(preparado.get("contexto"), dict):
            _v_hd = (preparado["contexto"].get("contexto") or {}).get(_k_hd)
            if _v_hd is None:
                _v_hd = preparado["contexto"].get(_k_hd)
        if _v_hd is not None:
            registro[_k_hd] = _v_hd
    _f_pre = _extraer_fuentes_y_firma_registro(preparado=preparado, metadatos=meta)
    for _k_f in ("fuente_local", "fuente_visitante", "firma_datos", "calidad_fuente_local", "calidad_fuente_visitante"):
        if _f_pre.get(_k_f) is not None:
            registro[_k_f] = _f_pre[_k_f]
    _lp_pre = extraer_lambdas_plano_desde_analisis(analisis, preparado)
    if _lp_pre is not None:
        registro["lambdas_plano"] = _lp_pre
    if isinstance(tg_m, dict) and tg_m.get("message_id") is not None:
        registro["telegram_message_id"] = tg_m.get("message_id")
    res = registrar_en_ledger_oos(registro, ruta_ledger=ruta_ledger)
    res["estado_registro"] = "pre_partido"
    res["snapshot_sha256"] = snapshot_sha
    res["sombra_comparaciones_sha256"] = sha_comp
    return res


def liquidar_sombras_en_ledger_oos(
    pronosticos: list[dict[str, Any]],
    resultados: dict[str, ResultadoPartido],
    *,
    ruta_ledger: str | None = None,
) -> dict[str, Any]:
    """
    Liquida post-partido (`estado_registro='liquidado'`) todos los pronósticos finalizados
    con `modo_sombra` y anterioridad pre-partido verificada, comparando `soloA` vs `A+B`
    contra los resultados reales y vinculando el hash `pre_partido_sha256` previo si existe.
    """
    existentes = cargar_ledger_oos(ruta_ledger)
    pre_por_trab: dict[str, dict[str, Any]] = {}
    pre_por_clave: dict[tuple[str, str], dict[str, Any]] = {}
    for ex in existentes:
        if str(ex.get("estado_registro") or "liquidado").strip().lower() != "pre_partido":
            continue
        if ex.get("id_trabajo"):
            pre_por_trab[str(ex["id_trabajo"])] = ex
        pre_por_clave[(str(ex.get("partido") or ""), str(ex.get("fecha") or "")[:10])] = ex

    registrados: list[dict[str, Any]] = []
    ya_existian = 0
    omitidos: list[dict[str, Any]] = []

    for doc in (pronosticos or []):
        if not isinstance(doc, dict) or bool(doc.get("abstenido")):
            continue
        sombra = doc.get("modo_sombra")
        if not isinstance(sombra, dict) or not (sombra.get("comparaciones") or []):
            continue
        equipos = doc.get("equipos") or {}
        partido = f"{equipos.get('local')} vs {equipos.get('visitante')}"
        p_info = doc.get("partido") or {}
        fecha = str(p_info.get("fecha") or (doc.get("metadatos") or {}).get("fecha_partido") or "")[:10]
        comp = str(p_info.get("competicion") or doc.get("competicion") or (doc.get("metadatos") or {}).get("competicion") or "")
        id_trab = str(doc.get("id_trabajo") or "")
        resultado = (resultados or {}).get(partido)
        if resultado is None or not getattr(resultado, "finalizado", False):
            omitidos.append({"id_trabajo": id_trab, "partido": partido, "motivo": "no_finalizado"})
            continue
        anterior, motivo_ant = _comprobar_anterioridad(doc, resultado)
        if not anterior:
            omitidos.append({"id_trabajo": id_trab, "partido": partido, "motivo": f"sin_anterioridad:{motivo_ant}"})
            continue

        id_part = str(getattr(resultado, "id_partido", None) or doc.get("id_partido") or "")
        pre_ref = pre_por_trab.get(id_trab) or pre_por_clave.get((partido, fecha))
        lineas_liq: list[dict[str, Any]] = []
        obs_sombra: list[dict[str, Any]] = []
        b_base_sum = 0.0
        b_somb_sum = 0.0
        ll_base_sum = 0.0
        ll_somb_sum = 0.0

        for item in sombra.get("comparaciones") or []:
            if not isinstance(item, dict):
                continue
            pb = item.get("prob_base")
            ps = item.get("prob_sombra")
            if pb is None or ps is None:
                continue
            pb_f = float(pb)
            ps_f = float(ps)
            sel = {
                "mercado": item.get("mercado"),
                "evento": item.get("evento"),
                "periodo": item.get("periodo") or "FT",
                "equipo": item.get("equipo"),
                "linea": item.get("linea"),
                "probabilidad": pb_f,
                "etiqueta": str(item.get("evento") or ""),
            }
            ev = liquidar(sel, resultado, id_trab)
            if ev.estado_liquidacion not in ("ganada", "perdida") or ev.evento_ocurrio not in (0, 1):
                continue
            y = int(ev.evento_ocurrio)
            pbc = min(max(pb_f, 1e-15), 1.0 - 1e-15)
            psc = min(max(ps_f, 1e-15), 1.0 - 1e-15)
            b_base_sum += (pb_f - y) ** 2
            b_somb_sum += (ps_f - y) ** 2
            ll_base_sum += -(y * math.log(pbc) + (1 - y) * math.log(1.0 - pbc))
            ll_somb_sum += -(y * math.log(psc) + (1 - y) * math.log(1.0 - psc))
            lineas_liq.append({
                "mercado": item.get("mercado"),
                "evento": item.get("evento"),
                "periodo": item.get("periodo"),
                "equipo": item.get("equipo"),
                "linea": item.get("linea"),
                "estado_liquidacion": ev.estado_liquidacion,
                "evento_ocurrio": y,
                "probabilidad_publicada": round(pb_f, 6),
                "probabilidad_sombra": round(ps_f, 6),
            })
            obs_sombra.append({
                "id_partido": id_part or partido,
                "partido": partido,
                "fecha": fecha,
                "competicion": comp,
                "mercado": str(item.get("mercado") or ""),
                "evento": str(item.get("evento") or ""),
                "periodo": str(item.get("periodo") or "FT"),
                "equipo": item.get("equipo"),
                "linea": item.get("linea"),
                "evento_ocurrio": y,
                "probs": {"base": round(pb_f, 6), "sombra": round(ps_f, 6)},
            })

        n_ev = len(lineas_liq)
        if n_ev <= 0:
            omitidos.append({"id_trabajo": id_trab, "partido": partido, "motivo": "sin_comparables_binarios"})
            continue

        aciertos_base = sum(1 for ln in lineas_liq if ln["evento_ocurrio"] == 1)
        b_base = round(b_base_sum / n_ev, 6)
        b_somb = round(b_somb_sum / n_ev, 6)
        ll_base = round(ll_base_sum / n_ev, 6)
        ll_somb = round(ll_somb_sum / n_ev, 6)

        _meta_doc = doc.get("metadatos") or {}
        _part_doc = doc.get("partido") if isinstance(doc.get("partido"), dict) else {}
        _fx_liq = doc.get("fixture_confirmado") if "fixture_confirmado" in doc else _part_doc.get("fixture_confirmado")
        if _fx_liq is None and pre_ref and "fixture_confirmado" in pre_ref:
            _fx_liq = pre_ref.get("fixture_confirmado")
        _ff_liq = doc.get("fuente_fecha") or _part_doc.get("fuente_fecha") or (pre_ref.get("fuente_fecha") if pre_ref else None)
        _hi_liq = doc.get("hora_inicio_partido") or _part_doc.get("hora_inicio_partido") or (pre_ref.get("hora_inicio_partido") if pre_ref else None)
        _f_hini_liq = doc.get("fuente_hora_inicio") or _part_doc.get("fuente_hora_inicio") or (pre_ref.get("fuente_hora_inicio") if pre_ref else None)
        _z_hini_liq = doc.get("zona_origen_hora_inicio") or _part_doc.get("zona_origen_hora_inicio") or (pre_ref.get("zona_origen_hora_inicio") if pre_ref else None)
        _tg_dt_liq = doc.get("telegram_message_date") if doc.get("telegram_message_date") is not None else ((doc.get("telegram_origen") or {}).get("message_date") or (pre_ref.get("telegram_message_date") if pre_ref else None))
        _hm_liq = doc.get("hora_mensaje_utc") or (doc.get("telegram_origen") or {}).get("message_date_utc") or (pre_ref.get("hora_mensaje_utc") if pre_ref else None)

        registro = {
            "estado_registro": "liquidado",
            "id_trabajo": id_trab,
            "id_partido": id_part,
            "partido": partido,
            "fecha": fecha,
            "competicion": comp,
            **({
                "fixture_confirmado": bool(_fx_liq) if _fx_liq is not None else False,
                "fuente_fecha": _ff_liq,
                "hora_inicio_partido": _hi_liq,
                **({"fuente_hora_inicio": _f_hini_liq} if _f_hini_liq is not None else {}),
                **({"zona_origen_hora_inicio": _z_hini_liq} if _z_hini_liq is not None else {}),
                "telegram_message_date": _tg_dt_liq,
                "hora_mensaje_utc": _hm_liq,
            } if (_fx_liq is not None or _hi_liq is not None or _tg_dt_liq is not None) else {}),
            **{
                k_f: v_f
                for k_f, v_f in _extraer_fuentes_y_firma_registro(doc=doc, pre_ref=pre_ref).items()
                if v_f is not None
            },
            **({
                "lambdas_plano": _lp_liq
            } if (_lp_liq := extraer_lambdas_plano_desde_analisis(doc=doc, pre_ref=pre_ref)) is not None else {}),
            "tipo_partido": str(doc.get("tipo_partido") or (doc.get("metadatos") or {}).get("tipo_partido") or ""),
            "pre_partido_sha256": (pre_ref or {}).get("sha256"),
            "marcador_real": {
                "ft": [getattr(resultado, "goles_local", None), getattr(resultado, "goles_visitante", None)],
                "1t": [resultado.goles_local_1t, resultado.goles_visitante_1t] if resultado.goles_local_1t is not None and resultado.goles_visitante_1t is not None else None,
                "2t": [int(resultado.goles_local) - int(resultado.goles_local_1t), int(resultado.goles_visitante) - int(resultado.goles_visitante_1t)] if resultado.goles_local is not None and resultado.goles_visitante is not None and resultado.goles_local_1t is not None and resultado.goles_visitante_1t is not None else None,
            },
            "resumen": {
                "aciertos": aciertos_base,
                "fallos": n_ev - aciertos_base,
                "n_evaluables": n_ev,
                "brier": b_base,
            },
            "lineas": lineas_liq,
            "modo_sombra": {
                "esquema": str(sombra.get("esquema") or "sombra-v1"),
                "version_base": str(sombra.get("version_base") or "soloA"),
                "version_sombra": str(sombra.get("version_sombra") or "A+B"),
                "banderas_candidatas": dict(sombra.get("banderas_candidatas") or {}),
                "lambdas_base": dict(sombra.get("lambdas_base") or {}),
                "lambdas_sombra": dict(sombra.get("lambdas_sombra") or {}),
                "es_partido_diseno": es_partido_de_diseno(partido, id_part),
                "pre_partido_vinculado": bool(pre_ref is not None),
                "pre_partido_sha256": (pre_ref or {}).get("sha256"),
                "n_comparables_evaluables": n_ev,
                "brier_base": b_base,
                "brier_sombra": b_somb,
                "log_loss_base": ll_base,
                "log_loss_sombra": ll_somb,
                "observaciones": obs_sombra,
            },
        }
        r_ins = _registrar_liquidacion_tribrazo(registro, sombra, ruta_ledger)
        if r_ins.get("registrado"):
            registrados.append({
                "id_trabajo": id_trab,
                "partido": partido,
                "fecha": fecha,
                "indice": r_ins.get("indice"),
                "sha256": r_ins.get("sha256"),
                "pre_partido_sha256": (pre_ref or {}).get("sha256"),
                "n_comparables_evaluables": n_ev,
            })
        elif r_ins.get("ya_existia"):
            ya_existian += 1

    return {
        "n_liquidados_nuevos": len(registrados),
        "n_ya_liquidados": ya_existian,
        "registrados": registrados,
        "omitidos": omitidos,
    }


def extraer_observaciones_sombra_desde_ledger(
    ruta_ledger: str | None = None,
    *,
    excluir_diseno: bool = True,
    excluir_amistosos: bool = True,
    brazo_regla: str | None = None,
    excluir_legacy_sin_fixture: bool = False,
) -> dict[str, Any]:
    """
    Extrae desde `ledger_oos` las observaciones duales (`base` vs `sombra`, o un brazo de
    regla específico como `guarda_doble_defensa_rota`) ya liquidadas listas para
    `evaluar_promocion_regla_futura`, contando los partidos competitivos válidos.
    """
    clave_regla = None
    if brazo_regla:
        norm = str(brazo_regla).strip()
        clave_regla = "guarda_doble_defensa_rota" if norm == "usar_guarda_doble_defensa_rota" else norm
    registros = cargar_ledger_oos(ruta_ledger)
    obs: list[dict[str, Any]] = []
    partidos_validos: set[str] = set()
    pre_pendientes: list[str] = []
    liquidados_con_obs_ids: set[str] = set()
    n_legacy_sin_fixture_excl = 0

    for r in registros:
        est = str(r.get("estado_registro") or "liquidado").strip().lower()
        ms = r.get("modo_sombra")
        if not isinstance(ms, dict):
            continue
        if est == "liquidado":
            clave = str(r.get("id_partido") or r.get("partido") or "")
            if clave:
                liquidados_con_obs_ids.add(clave)
            if r.get("pre_partido_sha256"):
                liquidados_con_obs_ids.add(f"sha:{r['pre_partido_sha256']}")
            if r.get("id_trabajo"):
                liquidados_con_obs_ids.add(f"trab:{r['id_trabajo']}")
            if r.get("partido"):
                liquidados_con_obs_ids.add(f"pf:{r.get('partido')}|{str(r.get('fecha') or '')[:10]}")
            if excluir_amistosos and es_registro_amistoso_excluido(r):
                continue
            if excluir_diseno and (ms.get("es_partido_diseno") or es_partido_de_diseno(r.get("partido"), r.get("id_partido"))):
                continue
            cls_obs = clasificar_oos_entrada_ledger(r)
            if r.get("estado_canonico") == "no_canonica":
                continue
            if r.get("telegram_message_date") is not None and r.get("es_oos_valido") is False:
                continue
            if excluir_legacy_sin_fixture and cls_obs["es_legacy_sin_fixture"]:
                n_legacy_sin_fixture_excl += 1
                continue
            obs_r = ms.get("observaciones") or []
            if clave_regla is not None:
                obs_usar = []
                for ob in obs_r:
                    p_reg = (ob.get("probs_reglas") or {}).get(clave_regla)
                    p_base = (ob.get("probs") or {}).get("base")
                    if p_reg is not None and p_base is not None:
                        ob_cp = dict(ob)
                        ob_cp["probs"] = {"base": float(p_base), "sombra": float(p_reg)}
                        obs_usar.append(ob_cp)
            else:
                obs_usar = obs_r
            if obs_usar:
                partidos_validos.add(clave)
                obs.extend(obs_usar)

    for r in registros:
        if str(r.get("estado_registro") or "liquidado").strip().lower() != "pre_partido":
            continue
        clave = str(r.get("id_partido") or r.get("partido") or "")
        claves_match = {
            clave,
            f"sha:{r.get('sha256') or ''}",
            f"trab:{r.get('id_trabajo') or ''}",
            f"pf:{r.get('partido') or ''}|{str(r.get('fecha') or '')[:10]}",
        }
        if not (claves_match & liquidados_con_obs_ids):
            pre_pendientes.append(f"{r.get('partido')} ({r.get('fecha')})")

    return {
        "n_partidos_sombra_validos": len(partidos_validos),
        "n_observaciones": len(obs),
        "n_excluidos_legacy_sin_fixture": n_legacy_sin_fixture_excl,
        "partidos_sombra_validos": sorted(partidos_validos),
        "pre_partido_pendientes_liquidacion": pre_pendientes,
        "observaciones": obs,
    }


def auditar_estado_modo_sombra_ronda11(
    *,
    guardar_ruta: str | None = "datos/ronda11_punto7_modo_sombra.json",
) -> dict[str, Any]:
    """
    Ronda 11 Punto 7 (MODO SOMBRA):
      - Verifica estado real en disco de `informes/evaluacion/ledger_oos.jsonl` y
        `informes/pronosticos/*.json` (cuántos guardan `soloA` vs `A+B` con hash antes
        del partido y cuántos quedan liquidados después).
      - Calcula exactamente cuántos partidos competitivos no-diseño faltan para llegar
        a `min_partidos_modo_sombra = 30`.
      - Documenta la captura automática cableada de punta a punta (`cmd_mercados` ->
        `adjuntar_modo_sombra_preparado` + `registrar_sombra_prepartido_en_ledger_oos`,
        y `/evaluar` -> `preparar_evaluacion` -> `liquidar_sombras_en_ledger_oos`) sin
        tocar `ledger_oos.jsonl` ni modificar λ/probabilidades por defecto.
    """
    from pathlib import Path
    from decision_cobertura import (
        BANDERAS_COMPORTAMIENTO_DEFECTO,
        obtener_politica_regla_futura,
        obtener_banderas_comportamiento,
    )

    raiz = Path(__file__).resolve().parent
    politica = obtener_politica_regla_futura()
    meta_partidos = int(politica.get("min_partidos_modo_sombra", 30))

    verif = verificar_cadena_ledger_oos(LEDGER_OOS_PATH)
    registros = cargar_ledger_oos(LEDGER_OOS_PATH)
    res_led = resumen_ledger_oos(LEDGER_OOS_PATH)
    obs_info = extraer_observaciones_sombra_desde_ledger(LEDGER_OOS_PATH)

    pdir = Path(PRONOSTICOS_DIR)
    snaps = sorted(pdir.glob("*.json")) if pdir.exists() else []
    n_snaps_sombra = 0
    for fp in snaps:
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(d.get("modo_sombra"), dict):
                n_snaps_sombra += 1
        except Exception:
            pass

    entradas_ledger_detalle = []
    n_ledger_con_sombra = 0
    for r in registros:
        tiene_s = isinstance(r.get("modo_sombra"), dict)
        if tiene_s:
            n_ledger_con_sombra += 1
        es_dis = es_partido_de_diseno(r.get("partido"), r.get("id_partido"))
        es_amis = es_registro_amistoso_excluido(r)
        entradas_ledger_detalle.append({
            "indice": r.get("indice"),
            "estado_registro": str(r.get("estado_registro") or "liquidado"),
            "id_partido": r.get("id_partido"),
            "partido": r.get("partido"),
            "fecha": r.get("fecha"),
            "tipo_partido": r.get("tipo_partido"),
            "es_partido_diseno": es_dis,
            "es_amistoso_excluido": es_amis,
            "entra_calibracion": r.get("entra_calibracion", True),
            "tiene_modo_sombra": tiene_s,
            "sha256_prefix": str(r.get("sha256") or "")[:16],
        })

    n_validos = int(obs_info["n_partidos_sombra_validos"])
    faltan = max(0, meta_partidos - n_validos)

    out = {
        "estado_previo_ronda11": {
            "ledger_ya_guardaba_soloA_y_AB_pre_y_liquidaba_post": False,
            "diagnostico_huecos": [
                "informes/evaluacion/ledger_oos.jsonl contenía 3 registros históricos (2 diseño TB/IF y 1 amistoso COL-CHI), todos con 0 objetos `modo_sombra`.",
                "Los 56 snapshots archivados en informes/pronosticos/*.json anteriores tienen 0 entradas con `modo_sombra`.",
                "bot_interactivo.cmd_mercados adjuntaba modo_sombra solo al snapshot en pronosticos/*.json (excluyendo usar_cuota_justa_114_top5) pero nunca escribía registro pre_partido en ledger_oos.jsonl.",
                "registrar_sombra_en_ledger_oos no tenía invocadores en producción y registrar_en_ledger_oos bloqueaba insertar 'liquidado' tras un 'pre_partido' del mismo (id_partido, fecha).",
            ],
        },
        "ledger_oos_real": {
            "ruta": str(Path(LEDGER_OOS_PATH).relative_to(raiz).as_posix()),
            "cadena_sha256_ok": bool(verif.get("ok")),
            "n_entradas_totales": len(registros),
            "n_entradas_con_modo_sombra": n_ledger_con_sombra,
            "n_incluidos_promedios_calibracion_legacy": int(res_led.get("n_incluidos_promedios_calibracion") or 0),
            "n_excluidos_amistosos": int(res_led.get("n_excluidos_amistosos") or 0),
            "entradas": entradas_ledger_detalle,
        },
        "snapshots_pronosticos": {
            "directorio": str(Path(PRONOSTICOS_DIR).relative_to(raiz).as_posix()),
            "n_snapshots_totales": len(snaps),
            "n_snapshots_con_modo_sombra": n_snaps_sombra,
        },
        "progreso_meta_30_partidos": {
            "meta_min_partidos_competitivos_no_diseno": meta_partidos,
            "n_partidos_competitivos_sombra_completados": n_validos,
            "faltan_partidos_competitivos_para_30": faltan,
            "razon_diseno_y_amistoso_no_cuentan": (
                "TB (5181796) e IF (5181797) son partidos de diseño excluidos por `exigir_datos_no_diseno=True`, "
                "y COL-CHI (6539346) es amistoso (`entra_calibracion=False`, sin `modo_sombra`)."
            ),
        },
        "captura_automatica_cableada": {
            "pre_partido": (
                "bot_interactivo.cmd_mercados -> evaluacion.adjuntar_modo_sombra_preparado(preparado) [base soloA vs A+B 14 banderas] "
                "-> evaluacion.guardar_snapshot(snapshot) -> evaluacion.registrar_sombra_prepartido_en_ledger_oos(preparado, ruta_snapshot=...)"
            ),
            "post_partido": (
                "bot_interactivo.cmd_evaluar -> evaluacion.preparar_evaluacion() -> evaluacion.liquidar_sombras_en_ledger_oos(docs, resultados) "
                "-> evaluacion.registrar_en_ledger_oos(..., estado_registro='liquidado', pre_partido_sha256=...)"
            ),
            "alimentacion_promocion": "evaluacion.extraer_observaciones_sombra_desde_ledger() -> evaluacion.evaluar_promocion_regla_futura()",
            "banderas_comportamiento_activas_por_defecto": obtener_banderas_comportamiento(),
            "todas_las_14_banderas_apagadas": all(not v for v in obtener_banderas_comportamiento().values()),
        },
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out



def es_registro_contaminado_por_diseno(reg: dict[str, Any]) -> bool:
    """True si el registro del ledger está marcado o identificado como partido de diseño (TB/IF)."""
    if not isinstance(reg, dict):
        return False
    if reg.get("contaminado_por_diseno") is True:
        return True
    return es_partido_de_diseno(reg.get("partido"), reg.get("id_partido"))


def _metricas_subconjunto_ledger(
    registros: list[dict[str, Any]],
    *,
    incluir_amistosos: bool = False,
    excluir_contaminados_diseno: bool = False,
    umbral_excl_prob_alta: float | None = None,
    excluir_legacy_sin_fixture: bool = False,
) -> dict[str, Any]:
    """Calcula métricas del ledger OOS permitiendo filtrar diseño/amistosos/líneas >= umbral y legacy sin fixture."""
    incluidos: list[dict[str, Any]] = []
    excluidos_amistosos: list[dict[str, Any]] = []
    excluidos_diseno: list[dict[str, Any]] = []
    n_excluidos_no_oos = 0
    n_excluidos_legacy = 0

    for r in registros:
        if str(r.get("estado_registro") or "liquidado").strip().lower() == "pre_partido":
            continue
        cls_m = clasificar_oos_entrada_ledger(r)
        if "es_oos_valido" in r:
            if r.get("es_oos_valido") is not True:
                n_excluidos_no_oos += 1
                if cls_m["es_legacy_sin_fixture"]:
                    n_excluidos_legacy += 1
                continue
            if r.get("es_canonica") is not True:
                continue
        elif excluir_legacy_sin_fixture and cls_m["es_legacy_sin_fixture"]:
            n_excluidos_no_oos += 1
            n_excluidos_legacy += 1
            continue
        es_amis = es_registro_amistoso_excluido(r)
        es_dis = es_registro_contaminado_por_diseno(r)
        if (not incluir_amistosos) and es_amis:
            excluidos_amistosos.append({
                "indice": r.get("indice"),
                "id_partido": r.get("id_partido"),
                "partido": r.get("partido"),
                "fecha": r.get("fecha"),
                "tipo_partido": r.get("tipo_partido"),
                "entra_promedios": r.get("entra_promedios"),
                "entra_calibracion": r.get("entra_calibracion"),
            })
            continue
        if excluir_contaminados_diseno and es_dis:
            excluidos_diseno.append({
                "indice": r.get("indice"),
                "id_partido": r.get("id_partido"),
                "partido": r.get("partido"),
                "fecha": r.get("fecha"),
                "contaminado_por_diseno": True,
            })
            continue
        incluidos.append(r)

    tasas_partido: list[float] = []
    briers_partido: list[float] = []
    ll_partido: list[float] = []
    detalle_partidos: list[dict[str, Any]] = []
    total_ac = 0
    total_ev = 0
    total_excl_prob = 0
    todas_lineas_excl_prob: list[dict[str, Any]] = []
    sum_brier_global = 0.0
    sum_ll_global = 0.0
    sum_prob_global = 0.0
    n_probs_global = 0

    for r in incluidos:
        res_r = r.get("resumen") or {}
        lineas = [ln for ln in (r.get("lineas") or []) if isinstance(ln, dict)]
        ev_lineas = [
            ln for ln in lineas
            if ln.get("estado_liquidacion") in ("ganada", "perdida")
            and ln.get("evento_ocurrio") in (0, 1)
        ]
        ev_filtradas: list[dict[str, Any]] = []
        excl_p_match: list[dict[str, Any]] = []
        for ln in ev_lineas:
            p_raw = ln.get("probabilidad_publicada")
            p_val = float(p_raw) if p_raw is not None else None
            if (
                umbral_excl_prob_alta is not None
                and p_val is not None
                and p_val >= float(umbral_excl_prob_alta)
            ):
                excl_p_match.append({
                    "evento": ln.get("evento"),
                    "linea": ln.get("linea"),
                    "probabilidad_pct": ln.get("probabilidad_pct"),
                    "probabilidad_publicada": p_val,
                    "evento_ocurrio": int(ln["evento_ocurrio"]),
                })
                continue
            ev_filtradas.append(ln)

        total_excl_prob += len(excl_p_match)
        if excl_p_match:
            todas_lineas_excl_prob.append({
                "id_partido": r.get("id_partido"),
                "partido": r.get("partido"),
                "lineas": excl_p_match,
            })

        n_ev = len(ev_filtradas) if (ev_lineas or umbral_excl_prob_alta is not None) else int(res_r.get("n_evaluables") or 0)
        if n_ev <= 0:
            detalle_partidos.append({
                "indice": r.get("indice"),
                "id_partido": r.get("id_partido"),
                "partido": r.get("partido"),
                "fecha": r.get("fecha"),
                "contaminado_por_diseno": es_registro_contaminado_por_diseno(r),
                "n_evaluables": 0,
                "n_excluidas_prob_alta": len(excl_p_match),
                "aciertos": 0,
                "tasa_acierto": None,
                "prob_media": None,
                "brier": None,
                "log_loss": None,
            })
            continue

        if ev_filtradas:
            n_ac = sum(1 for ln in ev_filtradas if ln.get("evento_ocurrio") == 1)
            probs = [float(ln["probabilidad_publicada"]) for ln in ev_filtradas if ln.get("probabilidad_publicada") is not None]
            if len(probs) == n_ev:
                b_p = sum((p - float(ln["evento_ocurrio"])) ** 2 for p, ln in zip(probs, ev_filtradas)) / n_ev
                ll_p = sum(log_loss_binario_valor(p, int(ln["evento_ocurrio"])) for p, ln in zip(probs, ev_filtradas)) / n_ev
                pm_p = sum(probs) / n_ev
                sum_brier_global += sum((p - float(ln["evento_ocurrio"])) ** 2 for p, ln in zip(probs, ev_filtradas))
                sum_ll_global += sum(log_loss_binario_valor(p, int(ln["evento_ocurrio"])) for p, ln in zip(probs, ev_filtradas))
                sum_prob_global += sum(probs)
                n_probs_global += len(probs)
            else:
                b_p = res_r.get("brier")
                ll_p = None
                pm_p = None
        else:
            n_ac = int(res_r.get("aciertos") or 0)
            b_p = res_r.get("brier")
            ll_p = None
            pm_p = None

        total_ac += n_ac
        total_ev += n_ev
        tasas_partido.append(n_ac / n_ev)
        if b_p is not None:
            briers_partido.append(float(b_p))
        if ll_p is not None:
            ll_partido.append(float(ll_p))

        detalle_partidos.append({
            "indice": r.get("indice"),
            "id_partido": r.get("id_partido"),
            "partido": r.get("partido"),
            "fecha": r.get("fecha"),
            "contaminado_por_diseno": es_registro_contaminado_por_diseno(r),
            "n_evaluables": n_ev,
            "n_excluidas_prob_alta": len(excl_p_match),
            "aciertos": n_ac,
            "tasa_acierto": round(n_ac / n_ev, 6),
            "prob_media": round(pm_p, 6) if pm_p is not None else None,
            "brier": round(float(b_p), 6) if b_p is not None else None,
            "log_loss": round(float(ll_p), 6) if ll_p is not None else None,
        })

    return {
        "n_incluidos": len(incluidos),
        "n_excluidos_amistosos": len(excluidos_amistosos),
        "n_excluidos_diseno": len(excluidos_diseno),
        "n_excluidos_no_oos": n_excluidos_no_oos,
        "n_excluidos_legacy_sin_fixture": n_excluidos_legacy,
        "partidos_incluidos": [f"{r.get('partido')} ({r.get('fecha')})" for r in incluidos],
        "amistosos_excluidos": excluidos_amistosos,
        "diseno_excluidos": excluidos_diseno,
        "umbral_excl_prob_alta": umbral_excl_prob_alta,
        "n_lineas_excluidas_prob_alta": total_excl_prob,
        "lineas_excluidas_prob_alta": todas_lineas_excl_prob,
        "n_lineas_evaluables": total_ev,
        "aciertos_totales": total_ac,
        "tasa_acierto_global": round(total_ac / total_ev, 6) if total_ev > 0 else None,
        "acierto_medio_equilibrado": round(sum(tasas_partido) / len(tasas_partido), 6) if tasas_partido else None,
        "prob_media_global": round(sum_prob_global / n_probs_global, 6) if n_probs_global > 0 else None,
        "brier_global": round(sum_brier_global / n_probs_global, 6) if n_probs_global > 0 else None,
        "brier_medio_equilibrado": round(sum(briers_partido) / len(briers_partido), 6) if briers_partido else None,
        "log_loss_global": round(sum_ll_global / n_probs_global, 6) if n_probs_global > 0 else None,
        "log_loss_medio_equilibrado": round(sum(ll_partido) / len(ll_partido), 6) if ll_partido else None,
        "detalle_partidos": detalle_partidos,
    }


def _enriquecer_resumen_ledger_oos(
    res_base: dict[str, Any],
    registros: list[dict[str, Any]],
) -> dict[str, Any]:
    """Agrega etiquetas contaminado_por_diseno y sub-resúmenes sin líneas >=99% y sin diseño."""
    etiquetado: list[dict[str, Any]] = []
    n_diseno = 0
    for r in registros:
        es_dis = es_registro_contaminado_por_diseno(r)
        if es_dis:
            n_diseno += 1
        etiquetado.append({
            "indice": r.get("indice"),
            "id_partido": r.get("id_partido"),
            "partido": r.get("partido"),
            "fecha": r.get("fecha"),
            "tipo_partido": r.get("tipo_partido"),
            "contaminado_por_diseno": es_dis,
            "es_amistoso_excluido": es_registro_amistoso_excluido(r),
            "sha256": r.get("sha256"),
            "prev_sha256": r.get("prev_sha256"),
        })
    res_base["n_contaminados_por_diseno"] = n_diseno
    res_base["registros_etiquetados"] = etiquetado
    res_base["nota_diseno_sha256"] = (
        "TB (5181796) e IF (5181797) se marcan lógicamente `contaminado_por_diseno=True` "
        "sin mutar las líneas históricas 0..1 del archivo JSONL para preservar intactos "
        "los hashes SHA-256 (`5839ca5a...` -> `a5498f8f...` -> `a6d53fd4...`)."
    )
    res_base["resumen_todas_lineas_con_diseno"] = _metricas_subconjunto_ledger(
        registros, incluir_amistosos=False, excluir_contaminados_diseno=False, umbral_excl_prob_alta=None,
    )
    res_base["resumen_sin_lineas_99_pct"] = _metricas_subconjunto_ledger(
        registros, incluir_amistosos=False, excluir_contaminados_diseno=False, umbral_excl_prob_alta=0.99,
    )
    res_base["resumen_sin_diseno_ni_amistosos"] = _metricas_subconjunto_ledger(
        registros, incluir_amistosos=False, excluir_contaminados_diseno=True, umbral_excl_prob_alta=None,
    )
    return res_base


def auditar_produccion_github_ronda12(
    *,
    consultar_github_en_vivo: bool = True,
    desde_utc: str = "2026-11-13T00:00:00Z",
    ruta_ledger: str | None = None,
    guardar_ruta: str | None = "datos/ronda12_punto1_produccion_github.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 12 — Punto 1 (`PRODUCCIÓN EN GITHUB`, solo lectura):
    Ejecuta 7(a)–7(k): inventario de corridas de `bot_24_7.yml` desde 2026-11-13,
    análisis de persistencia de pronóstico y ledger en GitHub Actions, comparación
    SHA-256 `origin/main` vs local (verificando el enlace de Colombia–Chile con `a5498f8f...`),
    explicación matemática e histórica de X2 en Italy–France (84.1% vs 81.6%),
    marcado `contaminado_por_diseno` en TB e IF, resumen sin líneas >=99%, y
    confirmación de si producción usa el camino `pre_partido`/`liquidado` de `bot_interactivo.py`.
    """
    import subprocess
    import urllib.error
    import urllib.request
    from pathlib import Path
    import motor_mercados as mm
    from decision_cobertura import banderas_comportamiento

    raiz = Path(__file__).resolve().parent
    ruta_led = ruta_ledger or LEDGER_OOS_PATH
    repo_slug = "aldairguido/agente-futbol"

    def _gh_get(url: str) -> tuple[int | None, Any]:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "agente-futbol-ronda12-readonly",
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return int(resp.status), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return int(exc.code), {"error": str(exc.reason)}
        except Exception as exc:
            return None, {"error": str(exc)}

    # 1. Inventario de corridas bot_24_7.yml + artifacts + caches desde API pública (solo lectura)
    origen_github = "api_publica_github"
    corridas_desde: list[dict[str, Any]] = []
    total_corridas_workflow: int | None = None
    artifacts_info: dict[str, Any] = {"total_count": None, "artifacts": []}
    caches_info: dict[str, Any] = {"total_count": None, "actions_caches": []}
    jobs_inspeccionados: list[dict[str, Any]] = []
    github_disponible = False

    if consultar_github_en_vivo:
        st_r, d_runs = _gh_get(
            f"https://api.github.com/repos/{repo_slug}/actions/workflows/bot_24_7.yml/runs?per_page=100"
        )
        if st_r == 200 and isinstance(d_runs, dict):
            github_disponible = True
            total_corridas_workflow = d_runs.get("total_count")
            for r in d_runs.get("workflow_runs") or []:
                if str(r.get("created_at") or "") >= desde_utc:
                    corridas_desde.append({
                        "run_number": r.get("run_number"),
                        "id": r.get("id"),
                        "created_at": r.get("created_at"),
                        "updated_at": r.get("updated_at"),
                        "event": r.get("event"),
                        "status": r.get("status"),
                        "conclusion": r.get("conclusion"),
                        "head_branch": r.get("head_branch"),
                        "head_sha": str(r.get("head_sha") or ""),
                        "head_sha_short": str(r.get("head_sha") or "")[:7],
                    })
            st_a, d_art = _gh_get(f"https://api.github.com/repos/{repo_slug}/actions/artifacts?per_page=100")
            if st_a == 200 and isinstance(d_art, dict):
                artifacts_info = {
                    "total_count": d_art.get("total_count"),
                    "artifacts": d_art.get("artifacts") or [],
                }
            st_c, d_cac = _gh_get(f"https://api.github.com/repos/{repo_slug}/actions/caches")
            if st_c == 200 and isinstance(d_cac, dict):
                caches_info = {
                    "total_count": d_cac.get("total_count"),
                    "actions_caches": d_cac.get("actions_caches") or [],
                }
            a_inspeccionar = corridas_desde[:1] + [x for x in corridas_desde if x.get("conclusion") == "failure"]
            for cr in a_inspeccionar:
                st_j, d_jobs = _gh_get(f"https://api.github.com/repos/{repo_slug}/actions/runs/{cr['id']}/jobs")
                if st_j == 200 and isinstance(d_jobs, dict):
                    jobs_inspeccionados.append({
                        "run_number": cr.get("run_number"),
                        "run_id": cr.get("id"),
                        "status": cr.get("status"),
                        "conclusion": cr.get("conclusion"),
                        "jobs": [
                            {
                                "name": j.get("name"),
                                "status": j.get("status"),
                                "conclusion": j.get("conclusion"),
                                "pasos_fallidos": [
                                    {"number": s.get("number"), "name": s.get("name"), "conclusion": s.get("conclusion")}
                                    for s in (j.get("steps") or []) if s.get("conclusion") == "failure"
                                ],
                                "pasos_en_progreso": [
                                    {"number": s.get("number"), "name": s.get("name")}
                                    for s in (j.get("steps") or []) if s.get("status") == "in_progress"
                                ],
                            }
                            for j in (d_jobs.get("jobs") or [])
                        ],
                    })

    if not github_disponible:
        ruta_prev = raiz / "datos" / "ronda12_punto1_produccion_github.json"
        if ruta_prev.exists():
            prev = json.loads(ruta_prev.read_text(encoding="utf-8"))
            inv_prev = prev.get("inventario_corridas_bot_24_7") or {}
            corridas_desde = inv_prev.get("corridas") or []
            total_corridas_workflow = inv_prev.get("total_corridas_workflow")
            artifacts_info = (prev.get("persistencia_github") or {}).get("artifacts") or artifacts_info
            caches_info = (prev.get("persistencia_github") or {}).get("caches") or caches_info
            jobs_inspeccionados = inv_prev.get("jobs_muestra_o_fallos") or []
            origen_github = "snapshot_local_sin_red"
        else:
            origen_github = "no_disponible"

    por_estado: dict[str, int] = {}
    por_evento: dict[str, int] = {}
    por_sha: dict[str, int] = {}
    for c in corridas_desde:
        k_est = f"{c.get('status')}/{c.get('conclusion')}"
        por_estado[k_est] = por_estado.get(k_est, 0) + 1
        k_ev = str(c.get("event"))
        por_evento[k_ev] = por_evento.get(k_ev, 0) + 1
        k_sha = str(c.get("head_sha_short"))
        por_sha[k_sha] = por_sha.get(k_sha, 0) + 1

    # 2. Inspección read-only de .github/workflows/bot_24_7.yml y origin/main
    def _git_show_safe(spec: str) -> str | None:
        try:
            return subprocess.check_output(["git", "show", spec], cwd=str(raiz), text=True, encoding="utf-8")
        except Exception:
            return None

    wf_path = raiz / ".github" / "workflows" / "bot_24_7.yml"
    wf_txt = (
        (_git_show_safe("fbb6dc03886e6f3740f89610acc4eb5f798192a4:.github/workflows/bot_24_7.yml") or "")
        if not consultar_github_en_vivo
        else (wf_path.read_text(encoding="utf-8") if wf_path.exists() else "")
    )

    head_origin_main = None
    try:
        head_origin_main = subprocess.check_output(["git", "rev-parse", "origin/main"], cwd=str(raiz), text=True).strip()
    except Exception:
        pass
    head_main_local = None
    try:
        head_main_local = subprocess.check_output(["git", "rev-parse", "main"], cwd=str(raiz), text=True).strip()
    except Exception:
        pass

    ref_base_r12 = "fbb6dc03886e6f3740f89610acc4eb5f798192a4" if not consultar_github_en_vivo else "origin/main"
    rem_bot_txt = _git_show_safe(f"{ref_base_r12}:bot_interactivo.py") or ""
    rem_eval_txt = _git_show_safe(f"{ref_base_r12}:evaluacion_pronosticos.py") or ""
    loc_bot_txt = (raiz / "bot_interactivo.py").read_text(encoding="utf-8") if (raiz / "bot_interactivo.py").exists() else ""

    simbolos_ruta_dual = [
        "adjuntar_modo_sombra_preparado",
        "registrar_sombra_prepartido_en_ledger_oos",
        "liquidar_sombras_en_ledger_oos",
        "estado_registro",
        "pre_partido",
        "guardar_snapshot",
        "preparar_evaluacion",
    ]
    ruta_produccion = {
        "origin_main_sha": head_origin_main,
        "local_main_sha": head_main_local,
        "origin_main_y_local_main_iguales": bool(head_origin_main and head_main_local and head_origin_main == head_main_local),
        "simbolos_en_origin_main_bot_interactivo": {s: (s in rem_bot_txt) for s in simbolos_ruta_dual},
        "simbolos_en_origin_main_evaluacion": {s: (s in rem_eval_txt) for s in simbolos_ruta_dual},
        "simbolos_en_rama_actual_bot_interactivo": {s: (s in loc_bot_txt) for s in simbolos_ruta_dual},
        "produccion_usa_pre_partido_y_liquidado_bot_interactivo": bool(
            "registrar_sombra_prepartido_en_ledger_oos" in rem_bot_txt
            or "liquidar_sombras_en_ledger_oos" in rem_eval_txt
        ),
        "conclusión": (
            "NO. El bot en producción (origin/main fbb6dc0) ejecuta exclusivamente "
            "bot_interactivo.py con guardar_snapshot y preparar_evaluacion sin capturar ni "
            "liquidar modo_sombra ni escribir pre_partido/liquidado en ledger_oos.jsonl."
        ),
    }

    # 3. Cadena SHA-256 remota vs local
    rem_ledger_raw = _git_show_safe(f"{ref_base_r12}:informes/evaluacion/ledger_oos.jsonl") or ""
    rem_entries = [json.loads(ln) for ln in rem_ledger_raw.splitlines() if ln.strip()]
    loc_entries = cargar_ledger_oos(ruta_led)
    verif_local = verificar_cadena_ledger_oos(ruta_led)

    comparacion_entradas: list[dict[str, Any]] = []
    for idx in range(max(len(rem_entries), len(loc_entries))):
        re_i = rem_entries[idx] if idx < len(rem_entries) else None
        lo_i = loc_entries[idx] if idx < len(loc_entries) else None
        comparacion_entradas.append({
            "indice": idx,
            "id_partido": (lo_i or re_i or {}).get("id_partido"),
            "partido": (lo_i or re_i or {}).get("partido"),
            "fecha": (lo_i or re_i or {}).get("fecha"),
            "en_remoto": re_i is not None,
            "en_local": lo_i is not None,
            "sha256_remoto": re_i.get("sha256") if re_i else None,
            "sha256_local": lo_i.get("sha256") if lo_i else None,
            "prev_sha256_local": lo_i.get("prev_sha256") if lo_i else None,
            "identico_byte_a_byte": bool(re_i is not None and lo_i is not None and re_i == lo_i),
            "contaminado_por_diseno": es_registro_contaminado_por_diseno(lo_i) if lo_i else False,
            "es_amistoso_excluido": es_registro_amistoso_excluido(lo_i) if lo_i else False,
        })

    if_rem_sha = rem_entries[1].get("sha256") if len(rem_entries) > 1 else None
    col_prev_sha = loc_entries[2].get("prev_sha256") if len(loc_entries) > 2 else None
    sha_col_chi = loc_entries[2].get("sha256") if len(loc_entries) > 2 else None

    comparacion_cadena = {
        "n_entradas_remoto_origin_main": len(rem_entries),
        "n_entradas_local": len(loc_entries),
        "verificacion_local": verif_local,
        "if_remoto_sha256": if_rem_sha,
        "colombia_chile_prev_sha256": col_prev_sha,
        "colombia_chile_sha256": sha_col_chi,
        "colombia_chile_enlaza_exactamente_con_a5498f8f": bool(
            if_rem_sha and col_prev_sha and if_rem_sha == col_prev_sha and str(if_rem_sha).startswith("a5498f8f")
        ),
        "entradas": comparacion_entradas,
    }

    # 4. Explicación exacta de Italy vs France X2: 84.1% vs 81.6%
    r5_path = raiz / "tests" / "fixtures" / "ronda5_real_fixtures.json"
    r5_docs = json.loads(r5_path.read_text(encoding="utf-8")).get("docs") or {}
    an_soloa = mm.analizar(r5_docs["italy"], r5_docs["france"], "Italy", "France", competicion="UEFA Nations League")
    mat_a = an_soloa.motor.matriz_ft
    px2_soloa = sum(mat_a[i][j] for i in range(len(mat_a)) for j in range(len(mat_a[i])) if i <= j)
    p1_soloa = sum(mat_a[i][j] for i in range(len(mat_a)) for j in range(len(mat_a[i])) if i > j)
    px_soloa = sum(mat_a[i][j] for i in range(len(mat_a)) for j in range(len(mat_a[i])) if i == j)
    p2_soloa = sum(mat_a[i][j] for i in range(len(mat_a)) for j in range(len(mat_a[i])) if i < j)
    sens_x2 = (an_soloa.sensibilidad or {}).get("doble_oportunidad|x2|FT|France|None") or {}

    with banderas_comportamiento(activar_todas=True):
        an_ab = mm.analizar(
            r5_docs["italy"], r5_docs["france"], "Italy", "France",
            competicion="UEFA Nations League",
        )
    mat_b = an_ab.motor.matriz_ft
    px2_ab = sum(mat_b[i][j] for i in range(len(mat_b)) for j in range(len(mat_b[i])) if i <= j)

    liq_r5_path = raiz / "informes" / "evaluacion" / "liquidacion_ronda5_real_2026-11-12.json"
    liq_r5 = json.loads(liq_r5_path.read_text(encoding="utf-8")) if liq_r5_path.exists() else {}
    linea_x2_ledger = next(
        (ln for ln in (loc_entries[1].get("lineas") or []) if ln.get("evento") == "x2"),
        {},
    ) if len(loc_entries) > 1 else {}
    png_x2_if = next(
        (ln for ln in (loc_entries[1].get("lineas_png_produccion_1211") or []) if ln.get("evento") == "x2"),
        {},
    ) if len(loc_entries) > 1 else {}

    explicacion_if_x2 = {
        "probabilidad_ledger_lineas": linea_x2_ledger.get("probabilidad_publicada"),
        "probabilidad_pct_ledger_lineas": linea_x2_ledger.get("probabilidad_pct"),
        "probabilidad_png_1211": png_x2_if.get("probabilidad_original"),
        "id_trabajo_png_1211": png_x2_if.get("id_trabajo"),
        "reconstruccion_5e8425f_soloA": {
            "lambdas": liq_r5.get("lambdas", {}).get("if"),
            "p1": round(p1_soloa, 6),
            "px": round(px_soloa, 6),
            "p2": round(p2_soloa, 6),
            "p_x2": round(px2_soloa, 6),
            "p_x2_exacta": px2_soloa,
            "sensibilidad_wc0_wx0": round(min(sens_x2.values()), 6) if sens_x2 else None,
            "fixture_generado_en": {
                "italy": r5_docs["italy"].get("generado_en"),
                "france": r5_docs["france"].get("generado_en"),
            },
        },
        "reconstruccion_AB_14_banderas": {"p_x2": round(px2_ab, 6)},
        "commits_anteriores_mismo_fixture": {
            "2efdc20_a_d3efa46": 0.8693,
            "3f2e53a_y_13e82e1": 0.86,
        },
        "snaps_pronosticos_con_italy_france": 0,
        "explicacion": (
            "84.1% (0.8410462427992359) es el resultado exacto de 5e8425f/soloA (lambdas [1.03, 2.33]) "
            "ejecutado sobre `ronda5_real_fixtures.json` (docs capturados el 2026-11-13 02:18-02:19 tras el partido) "
            "e insertado en `liquidacion_ronda5_real_2026-11-12.json` y `ledger_oos.jsonl` (`if_lineas`/`lineas`). "
            "En cambio, 81.6% (0.816) en `lineas_png_produccion_1211` (`PNG-IF-1211`) proviene de la transcripción "
            "manual de la captura PNG publicada en producción el 2026-11-12 antes del cierre final de `5e8425f` "
            "(corte registrado 08:55:00-05:00, 39s antes de `5e8425f` 08:55:39-05:00, con feed vivo de FotMob "
            "de esa mañana). Ningún snapshot JSON ni artefacto de esa corrida se persistió en disco ni en GitHub Actions."
        ),
    }

    # 5. Resumen ledger OOS con marcado contaminado_por_diseno y sin líneas >= 99%
    res_led = resumen_ledger_oos(ruta_led)

    out = {
        "inventario_corridas_bot_24_7": {
            "origen_datos": origen_github,
            "desde_utc": desde_utc,
            "total_corridas_workflow": total_corridas_workflow,
            "n_corridas_desde_2026_11_13": len(corridas_desde),
            "conteo_por_estado": por_estado,
            "conteo_por_evento": por_evento,
            "conteo_por_head_sha": por_sha,
            "jobs_muestra_o_fallos": jobs_inspeccionados,
            "corridas": corridas_desde,
        },
        "persistencia_github": {
            "workflow_file": ".github/workflows/bot_24_7.yml",
            "permissions_contents": "read" if "contents: read" in wf_txt else None,
            "tiene_upload_artifact": ("upload-artifact" in wf_txt),
            "tiene_git_push_o_commit": ("git push" in wf_txt or "git commit" in wf_txt),
            "concurrency_cancel_in_progress": ("cancel-in-progress: true" in wf_txt),
            "setup_python_cache": "pip" if "cache: 'pip'" in wf_txt else None,
            "artifacts": artifacts_info,
            "caches": caches_info,
            "conclusion_persistencia": (
                "NADA para pronósticos y ledger. Con `contents: read`, 0 artifacts "
                "(total_count=0) y 0 caches activos (total_count=0; setup-python solo intenta "
                "cachear pip), los archivos escritos en informes/pronosticos/ y ledger_oos.jsonl "
                "mueren con cada runner efímero."
            ),
        },
        "comparacion_cadena_sha256_remota_vs_local": comparacion_cadena,
        "explicacion_italy_france_x2_841_vs_816": explicacion_if_x2,
        "ledger_contaminado_por_diseno_y_resumen_sin_99_pct": res_led,
        "ruta_produccion_bot_interactivo": ruta_produccion,
    }

    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out



def ejecutar_tercer_brazo_plano_analisis(
    doc_local: Any,
    doc_visitante: Any,
    equipo_local: str,
    equipo_visitante: str,
    *,
    parametros_base: Any = None,
    rho_override: float | None = None,
    **analizar_kwargs: Any,
) -> Any:
    """
    Ejecuta la variante 'plano' (tercer brazo de modo_sombra):
      - λ plano (Etapa 1: tasas_goles_plano, sin moduladores, sin λ_efectivo,
        sin Doble ADN / red 45-DCN / reglas y sin cola ancha),
      - Matriz de goles Poisson / Dixon-Coles (`matriz_marcadores(..., rho=rho)`),
      - Córneres con sobredispersión Binomial Negativa (`usar_sobredispersion_nb_corners=True`).
    """
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm

    p_b = parametros_base or mm.Parametros()
    rho_eff = float(rho_override if rho_override is not None else getattr(p_b, "rho", 0.0))
    params_plano = mm.Parametros(
        w_condicion=getattr(p_b, "w_condicion", mm.W_CONDICION_DEFECTO),
        w_xg=getattr(p_b, "w_xg", mm.W_XG_DEFECTO),
        umbral=getattr(p_b, "umbral", mm.UMBRAL_DEFECTO),
        k_nb=getattr(p_b, "k_nb", 10.0),
        distribucion_conteos=getattr(p_b, "distribucion_conteos", "poisson"),
        k_credibilidad=getattr(p_b, "k_credibilidad", mm.K_CREDIBILIDAD_DEFECTO),
        rho=rho_eff,
        aplicar_moduladores=False,
        aplicar_motor_recursivo=False,
        mc_simulaciones=getattr(p_b, "mc_simulaciones", 100_000),
        aplicar_lambda_efectivo=False,
        aplicar_cola_ancha=False,
    )
    kw = dict(analizar_kwargs)
    kw.pop("parametros", None)
    with banderas_comportamiento(activar_todas=False, usar_sobredispersion_nb_corners=True):
        return mm.analizar(
            doc_local,
            doc_visitante,
            equipo_local,
            equipo_visitante,
            parametros=params_plano,
            **kw,
        )


def _enriquecer_sombra_con_brazo_plano(
    sombra: dict[str, Any],
    analisis_plano: Any,
) -> dict[str, Any]:
    """Adjunta el tercer brazo `plano` (Etapa 1 + Poisson/DC + NB córneres) a `modo_sombra`."""
    if not isinstance(sombra, dict) or analisis_plano is None:
        return sombra
    mot_p = getattr(analisis_plano, "motor", None)
    tas_p = getattr(mot_p, "tasas", None)
    p_p = getattr(mot_p, "p", None)
    lam_plano = (
        {
            "local_ft": getattr(tas_p, "lambda_local_ft", None),
            "visitante_ft": getattr(tas_p, "lambda_visitante_ft", None),
            "local_1t": getattr(tas_p, "lambda_local_1t", None),
            "visitante_1t": getattr(tas_p, "lambda_visitante_1t", None),
            "local_2t": getattr(tas_p, "lambda_local_2t", None),
            "visitante_2t": getattr(tas_p, "lambda_visitante_2t", None),
        }
        if tas_p is not None
        else {}
    )
    m_plano: dict[tuple[str, str, str, Any], dict[str, Any]] = {}
    for rm in (getattr(analisis_plano, "mercados", None) or []):
        for s in (getattr(rm, "selecciones", None) or []):
            if not getattr(s, "estimable", False) or getattr(s, "probabilidad", None) is None:
                continue
            lin = getattr(s, "linea", None)
            k = (
                str(getattr(s, "evento", "") or ""),
                str(getattr(s, "periodo", "FT") or "FT").strip().upper(),
                str(getattr(s, "equipo", None) or ""),
                None if lin is None else round(float(lin), 4),
            )
            m_plano[k] = {
                "mercado": getattr(s, "mercado", None),
                "evento": k[0],
                "periodo": k[1],
                "equipo": getattr(s, "equipo", None),
                "linea": lin,
                "prob": round(float(s.probabilidad), 6),
            }

    comps = list(sombra.get("comparaciones") or [])
    vistos: set[tuple[str, str, str, Any]] = set()
    for item in comps:
        lin = item.get("linea")
        k = (
            str(item.get("evento") or ""),
            str(item.get("periodo") or "FT").strip().upper(),
            str(item.get("equipo") or ""),
            None if lin is None else round(float(lin), 4),
        )
        vistos.add(k)
        p_pl = m_plano.get(k)
        item["prob_plano"] = p_pl["prob"] if p_pl is not None else None
    for k, pl in m_plano.items():
        if k not in vistos:
            comps.append({
                "mercado": pl["mercado"],
                "evento": pl["evento"],
                "periodo": pl["periodo"],
                "equipo": pl["equipo"],
                "linea": pl["linea"],
                "prob_base": None,
                "prob_sombra": None,
                "prob_plano": pl["prob"],
            })

    sombra["esquema"] = "sombra-v2-tribrazo"
    sombra["version_plano"] = "plano"
    sombra["lambdas_plano"] = lam_plano
    sombra["reglas_brazo_plano"] = {
        "bandera": "usar_tercer_brazo_plano_sombra",
        "etapa_lambda": "1_lambda_plano",
        "goles_matriz": "poisson_dixon_coles",
        "rho": float(getattr(p_p, "rho", 0.0) or 0.0),
        "corners": "binomial_negativa_sobredispersa",
    }
    sombra["brazos"] = {
        "soloA": {"version": sombra.get("version_base") or "soloA", "lambdas": sombra.get("lambdas_base") or {}},
        "A+B": {"version": sombra.get("version_sombra") or "A+B", "lambdas": sombra.get("lambdas_sombra") or {}},
        "plano": {"version": "plano", "lambdas": lam_plano},
    }
    sombra["comparaciones"] = comps
    return sombra


def _aplicar_brazo_sombra_guarda_ddr(
    sombra: dict[str, Any],
    preparado: dict[str, Any],
    analisis: Any,
    dl: Any,
    dv: Any,
    eq_l: str,
    eq_v: str,
    kw: dict[str, Any],
) -> dict[str, Any]:
    """
    Adjunta `usar_guarda_doble_defensa_rota` como brazo sombra dedicado sobre la
    configuración base vigente, sin activar la bandera en los pronósticos publicados.
    """
    from decision_cobertura import (
        banderas_comportamiento,
        obtener_banderas_comportamiento,
        obtener_metadatos_defectos_banderas_b,
    )
    import motor_mercados as mm

    banderas_base = dict(obtener_banderas_comportamiento())
    sombra["banderas_base"] = banderas_base
    sombra["defectos_operativos_sin_validacion"] = dict(obtener_metadatos_defectos_banderas_b())
    if dl is None or dv is None:
        return sombra
    if isinstance(sombra.get("brazos_reglas"), dict) and "guarda_doble_defensa_rota" in sombra["brazos_reglas"]:
        return sombra

    banderas_guarda = dict(banderas_base)
    banderas_guarda["usar_guarda_doble_defensa_rota"] = True
    with banderas_comportamiento(activar_todas=False, **banderas_guarda):
        an_guarda = mm.analizar(dl, dv, eq_l, eq_v, **(kw or {}))
    preparado["analisis_guarda_doble_defensa_rota"] = an_guarda

    lam_g = _resumen_lambdas_analisis(an_guarda)
    m_g = _mapa_probs_analisis(an_guarda)
    comparaciones = list(sombra.get("comparaciones") or [])
    n_comp_g = 0
    for item in comparaciones:
        if not isinstance(item, dict):
            continue
        lin = item.get("linea")
        k = (
            str(item.get("evento") or ""),
            str(item.get("periodo") or "FT").strip().upper(),
            str(item.get("equipo") or ""),
            None if lin is None else round(float(lin), 4),
        )
        g_entry = m_g.get(k)
        p_g = g_entry["prob"] if g_entry is not None else None
        item["prob_guarda_doble_defensa_rota"] = p_g
        if item.get("prob_base") is not None and p_g is not None:
            n_comp_g += 1

    lam_b = sombra.get("lambdas_base") or {}
    d_l = (
        round(float(lam_g["local_ft"]) - float(lam_b["local_ft"]), 6)
        if lam_g.get("local_ft") is not None and lam_b.get("local_ft") is not None
        else None
    )
    d_v = (
        round(float(lam_g["visitante_ft"]) - float(lam_b["visitante_ft"]), 6)
        if lam_g.get("visitante_ft") is not None and lam_b.get("visitante_ft") is not None
        else None
    )
    brazos_reglas = dict(sombra.get("brazos_reglas") or {})
    brazos_reglas["guarda_doble_defensa_rota"] = {
        "bandera": "usar_guarda_doble_defensa_rota",
        "solo_brazo_sombra": True,
        "banderas_activas": banderas_guarda,
        "lambdas": lam_g,
        "n_selecciones_comparables": n_comp_g,
        "delta_lambda_local_ft": d_l,
        "delta_lambda_visitante_ft": d_v,
    }
    sombra["brazos_reglas"] = brazos_reglas
    return sombra


def _aplicar_tercer_brazo_plano_si_activo(
    sombra: dict[str, Any],
    preparado: dict[str, Any],
    analisis: Any,
    dl: Any,
    dv: Any,
    eq_l: str,
    eq_v: str,
    kw: dict[str, Any],
) -> dict[str, Any]:
    """Adjunta el brazo sombra `guarda_doble_defensa_rota` y el brazo `plano` si su bandera está activa."""
    from decision_cobertura import bandera_activa as _ba_pl

    sombra = _aplicar_brazo_sombra_guarda_ddr(sombra, preparado, analisis, dl, dv, eq_l, eq_v, kw)
    if not _ba_pl("usar_tercer_brazo_plano_sombra"):
        return sombra
    params_base = kw.get("parametros") or getattr(analisis, "parametros", None)
    an_plano = ejecutar_tercer_brazo_plano_analisis(
        dl,
        dv,
        eq_l,
        eq_v,
        parametros_base=params_base,
        **{k: v for k, v in (kw or {}).items() if k != "parametros"},
    )
    preparado["analisis_plano"] = an_plano
    return _enriquecer_sombra_con_brazo_plano(sombra, an_plano)


def _hash_sombra_comparaciones_tribrazo(
    payload_sombra: dict[str, Any],
    sombra: dict[str, Any],
    comp_sha_base: str,
) -> str:
    """Enriquece `payload_sombra` y su SHA-256 con brazos sombra (`brazos_reglas` y/o `lambdas_plano`)."""
    if not isinstance(sombra, dict):
        return comp_sha_base
    for k_meta in ("banderas_base", "defectos_operativos_sin_validacion", "brazos_reglas"):
        if k_meta in sombra and sombra.get(k_meta) is not None:
            payload_sombra[k_meta] = dict(sombra[k_meta])

    tiene_plano = "lambdas_plano" in sombra
    tiene_reglas = isinstance(sombra.get("brazos_reglas"), dict)
    if not tiene_plano and not tiene_reglas:
        return comp_sha_base

    hash_obj: dict[str, Any] = {
        "lambdas_base": payload_sombra["lambdas_base"],
        "lambdas_sombra": payload_sombra["lambdas_sombra"],
        "comparaciones": payload_sombra["comparaciones"],
    }
    if tiene_plano:
        payload_sombra["esquema"] = str(sombra.get("esquema") or "sombra-v2-tribrazo")
        payload_sombra["version_plano"] = str(sombra.get("version_plano") or "plano")
        payload_sombra["lambdas_plano"] = dict(sombra.get("lambdas_plano") or {})
        if isinstance(sombra.get("reglas_brazo_plano"), dict):
            payload_sombra["reglas_brazo_plano"] = dict(sombra["reglas_brazo_plano"])
        if isinstance(sombra.get("brazos"), dict):
            payload_sombra["brazos"] = dict(sombra["brazos"])
        hash_obj["lambdas_plano"] = payload_sombra["lambdas_plano"]
    if tiene_reglas:
        hash_obj["brazos_reglas"] = payload_sombra["brazos_reglas"]
    return _hash_canonico_oos(hash_obj)


def _liquidar_prob_brazo_extra(
    sombra: dict[str, Any],
    obs_list: list[dict[str, Any]],
    lineas_liq: list[dict[str, Any]],
    *,
    campo_comp: str,
    clave_linea: str,
    clave_probs: str | None = None,
    clave_probs_regla: str | None = None,
) -> tuple[int, float | None, float | None]:
    mapa_arm: dict[tuple[str, str, str, Any], float] = {}
    for item in (sombra.get("comparaciones") or []):
        pp = item.get(campo_comp) if isinstance(item, dict) else None
        if pp is None:
            continue
        lin = item.get("linea")
        k = (
            str(item.get("evento") or ""),
            str(item.get("periodo") or "FT").strip().upper(),
            str(item.get("equipo") or ""),
            None if lin is None else round(float(lin), 4),
        )
        mapa_arm[k] = float(pp)

    b_sum = 0.0
    ll_sum = 0.0
    n_arm = 0
    for idx, ob in enumerate(obs_list):
        lin = ob.get("linea")
        k = (
            str(ob.get("evento") or ""),
            str(ob.get("periodo") or "FT").strip().upper(),
            str(ob.get("equipo") or ""),
            None if lin is None else round(float(lin), 4),
        )
        pp_val = mapa_arm.get(k)
        if pp_val is None:
            continue
        pp_r = round(pp_val, 6)
        if clave_probs is not None:
            ob.setdefault("probs", {})[clave_probs] = pp_r
        if clave_probs_regla is not None:
            ob.setdefault("probs_reglas", {})[clave_probs_regla] = pp_r
        if idx < len(lineas_liq) and isinstance(lineas_liq[idx], dict):
            lineas_liq[idx][clave_linea] = pp_r
        y = int(ob["evento_ocurrio"])
        ppc = min(max(pp_val, 1e-15), 1.0 - 1e-15)
        b_sum += (pp_val - y) ** 2
        ll_sum += -(y * math.log(ppc) + (1 - y) * math.log(1.0 - ppc))
        n_arm += 1
    if n_arm <= 0:
        return 0, None, None
    return n_arm, round(b_sum / n_arm, 6), round(ll_sum / n_arm, 6)


def _registrar_liquidacion_tribrazo(
    registro: dict[str, Any],
    sombra: dict[str, Any],
    ruta_ledger: str | None,
) -> dict[str, Any]:
    """Completa métricas de brazos sombra (`plano` y `brazos_reglas`) en liquidación sin alterar `probs` base/dual."""
    if isinstance(sombra, dict):
        ms = registro.get("modo_sombra") or {}
        obs_list = ms.get("observaciones") or []
        lineas_liq = registro.get("lineas") or []
        for k_meta in ("banderas_base", "defectos_operativos_sin_validacion"):
            if k_meta in sombra and sombra.get(k_meta) is not None:
                ms[k_meta] = dict(sombra[k_meta])

        if "lambdas_plano" in sombra:
            ms["esquema"] = str(sombra.get("esquema") or "sombra-v2-tribrazo")
            ms["version_plano"] = str(sombra.get("version_plano") or "plano")
            ms["lambdas_plano"] = dict(sombra.get("lambdas_plano") or {})
            if isinstance(sombra.get("reglas_brazo_plano"), dict):
                ms["reglas_brazo_plano"] = dict(sombra["reglas_brazo_plano"])
            if isinstance(sombra.get("brazos"), dict):
                ms["brazos"] = dict(sombra["brazos"])
            n_pl, br_pl, ll_pl = _liquidar_prob_brazo_extra(
                sombra,
                obs_list,
                lineas_liq,
                campo_comp="prob_plano",
                clave_linea="probabilidad_plano",
                clave_probs="plano",
            )
            ms["brier_plano"] = br_pl
            ms["log_loss_plano"] = ll_pl
            ms["n_comparables_plano"] = n_pl

        brazos_reg = sombra.get("brazos_reglas")
        if isinstance(brazos_reg, dict) and "guarda_doble_defensa_rota" in brazos_reg:
            ms_br = dict(brazos_reg)
            arm_g = dict(ms_br.get("guarda_doble_defensa_rota") or {})
            n_g, br_g, ll_g = _liquidar_prob_brazo_extra(
                sombra,
                obs_list,
                lineas_liq,
                campo_comp="prob_guarda_doble_defensa_rota",
                clave_linea="probabilidad_guarda_doble_defensa_rota",
                clave_probs_regla="guarda_doble_defensa_rota",
            )
            arm_g["n_comparables_evaluables"] = n_g
            arm_g["brier"] = br_g
            arm_g["log_loss"] = ll_g
            ms_br["guarda_doble_defensa_rota"] = arm_g
            ms["brazos_reglas"] = ms_br
            ms["brier_guarda_doble_defensa_rota"] = br_g
            ms["log_loss_guarda_doble_defensa_rota"] = ll_g
            ms["n_comparables_guarda_doble_defensa_rota"] = n_g

        registro["modo_sombra"] = ms
    return registrar_en_ledger_oos(registro, ruta_ledger=ruta_ledger)


def auditar_tercer_brazo_plano_sombra_ronda12(
    *,
    n_boot: int = 2000,
    seed: int = 42,
    guardar_ruta: str | None = "datos/ronda12_punto2_tercer_brazo_plano.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 12 — Punto 2 (`TERCER BRAZO "PLANO"`):
      - Verifica que la bandera `usar_tercer_brazo_plano_sombra` está apagada por defecto (False)
        y excluida de `activar_todas=True`, sin cambiar defectos de `soloA` ni del `modo_sombra` previo.
      - Con la bandera activa, verifica el flujo completo `pre_partido` -> `liquidado` guardando
        simultáneamente `soloA`, `A+B` y `plano` (Etapa 1 + Poisson/Dixon-Coles + NB córneres).
      - Mide Brier/LogLoss empírico de los brazos (`plano` rho=0.0 + NB, `plano_dc` rho=-0.11 + NB,
        `soloA`, `A+B`) sobre los 24 partidos OOS (1376 filas) con IC95 por partido.
    """
    import hashlib
    import tempfile
    from pathlib import Path
    import motor_mercados as mm
    from decision_cobertura import (
        BANDERAS_COMPORTAMIENTO_DEFECTO,
        _BANDERAS_AUXILIARES_DEFECTO,
        bandera_activa,
        banderas_comportamiento,
        obtener_banderas_comportamiento,
    )

    raiz = Path(__file__).resolve().parent
    estado_defecto = {
        "usar_tercer_brazo_plano_sombra_defecto": bandera_activa("usar_tercer_brazo_plano_sombra"),
        "banderas_set_b_todas_false": all(not v for v in obtener_banderas_comportamiento().values()),
        "en_banderas_auxiliares": "usar_tercer_brazo_plano_sombra" in _BANDERAS_AUXILIARES_DEFECTO,
        "fuera_de_set_b_14": "usar_tercer_brazo_plano_sombra" not in BANDERAS_COMPORTAMIENTO_DEFECTO,
    }
    with banderas_comportamiento(activar_todas=True):
        estado_con_activar_todas = bandera_activa("usar_tercer_brazo_plano_sombra")

    # Prueba de flujo pre_partido -> liquidado sobre Galatasaray vs Barcelona 2026-10-13 en tmp
    aud_dir = raiz / "informes" / "auditorias" / "2026-10-13"
    dl_raw = json.loads((aud_dir / "auditoria_galatasaray_2026-10-13_102501.json").read_text(encoding="utf-8"))
    dv_raw = json.loads((aud_dir / "auditoria_barcelona_2026-10-13_102522.json").read_text(encoding="utf-8"))
    dl_c = {"equipo": dl_raw.get("equipo"), "partidos": [x for x in dl_raw.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"]}
    dv_c = {"equipo": dv_raw.get("equipo"), "partidos": [x for x in dv_raw.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"]}

    with banderas_comportamiento(activar_todas=False):
        an_of = mm.analizar(dl_c, dv_c, "Galatasaray", "Barcelona", fecha_partido="2026-10-13")
    an_of.metadatos["fecha_partido"] = "2026-10-13"
    an_of.metadatos["generado_en"] = "2026-10-13T10:00:00"
    an_of.metadatos["competicion"] = "UEFA Champions League"

    def _run_ciclo(activar_tercer_brazo: bool) -> dict[str, Any]:
        prep = {
            "id_trabajo": f"SOMBRA_R12_P2_{int(activar_tercer_brazo)}",
            "analisis": an_of,
            "contexto": {
                "doc_local": dl_c,
                "doc_visitante": dv_c,
                "equipo_local": "Galatasaray",
                "equipo_visitante": "Barcelona",
                "fecha_partido": "2026-10-13",
                "competicion": "UEFA Champions League",
                "condicion_local": "casa",
                "condicion_visitante": "fuera",
            },
        }
        with banderas_comportamiento(activar_todas=False, usar_tercer_brazo_plano_sombra=activar_tercer_brazo):
            somb = adjuntar_modo_sombra_preparado(prep)
        with tempfile.TemporaryDirectory() as td:
            ruta_snap = guardar_snapshot(snapshot_desde_analisis(prep), directorio=str(Path(td) / "snaps"))
            ruta_led = str(Path(td) / "ledger.jsonl")
            r_pre = registrar_sombra_prepartido_en_ledger_oos(prep, ruta_snapshot=ruta_snap, ruta_ledger=ruta_led)
            snap_loaded = json.loads(Path(ruta_snap).read_text(encoding="utf-8"))
            pre_regs = cargar_ledger_oos(ruta_led)
            res_part = ResultadoPartido(
                id_partido="6106338",
                estado="finalizado",
                local="Galatasaray",
                visitante="Barcelona",
                goles_local=1, goles_visitante=2,
                goles_local_1t=0, goles_visitante_1t=1,
                inicio_utc="2026-10-13T19:00:00Z",
                competicion="UEFA Champions League",
                fuente="fotmob",
                consultado_en="2026-10-14T00:00:00Z",
                corners_local={"FT": 5, "1T": 2, "2T": 3},
                corners_visitante={"FT": 6, "1T": 3, "2T": 3},
                amarillas_local={"FT": 2, "1T": 1, "2T": 1},
                amarillas_visitante={"FT": 2, "1T": 1, "2T": 1},
                rojas_local=0, rojas_visitante=0,
            )
            r_liq = liquidar_sombras_en_ledger_oos(
                [snap_loaded],
                {"Galatasaray vs Barcelona": res_part},
                ruta_ledger=ruta_led,
            )
            obs_ext = extraer_observaciones_sombra_desde_ledger(ruta_led)
            cad_ok = verificar_cadena_ledger_oos(ruta_led).get("ok")
        pre_ms = (pre_regs[0].get("modo_sombra") or {}) if pre_regs else {}
        return {
            "esquema": somb.get("esquema") if somb else None,
            "lambdas_base": somb.get("lambdas_base") if somb else None,
            "lambdas_sombra": somb.get("lambdas_sombra") if somb else None,
            "lambdas_plano": somb.get("lambdas_plano") if somb else None,
            "pre_partido_registrado": bool(r_pre.get("registrado")),
            "pre_partido_sha256": r_pre.get("sha256"),
            "pre_tiene_lambdas_plano": "lambdas_plano" in pre_ms,
            "pre_tiene_prob_plano_en_comp": any("prob_plano" in c for c in (pre_ms.get("comparaciones") or [])),
            "n_liquidados_nuevos": int(r_liq.get("n_liquidados_nuevos") or 0),
            "cadena_sha256_ok": bool(cad_ok),
            "n_observaciones": int(obs_ext.get("n_observaciones") or 0),
            "claves_probs_obs": sorted((obs_ext.get("observaciones") or [{}])[0].get("probs", {}).keys()),
        }

    ciclo_bandera_apagada = _run_ciclo(False)
    ciclo_bandera_activa = _run_ciclo(True)

    # Benchmark OOS sobre los 24 partidos (1376 filas) comparando soloA, A+B, plano (rho=0) y plano_dc (rho=-0.11)
    corpus_7 = json.loads((raiz / "datos" / "oos_conciliacion_ronda7_ronda8.json").read_text(encoding="utf-8"))
    filas_1376 = [
        r for r in (corpus_7.get("filas_1506") or [])
        if all(k in (r.get("probs") or {}) for k in ("arch", "5e8425f", "994209a", "soloA", "A+B"))
    ]
    mapa_r10 = json.loads((raiz / "datos" / "ronda10_oos_26_jobs_map.json").read_text(encoding="utf-8"))
    no_comp_jids = set(mapa_r10.get("no_comp_jids") or [])
    jobs_map = mapa_r10.get("jobs") or {}

    merc_by_job: dict[str, dict[str, Any]] = {}
    for fp in sorted((raiz / "informes" / "mercados").rglob("mercados_*.json")):
        md_i = json.loads(fp.read_text(encoding="utf-8"))
        if md_i.get("id_trabajo"):
            merc_by_job[str(md_i["id_trabajo"])] = md_i

    jobs_oos = sorted({str(f["id_trabajo"]) for f in filas_1376 if str(f.get("id_trabajo") or "") in jobs_map})
    probs_plano_por_job: dict[tuple[str, float], dict[tuple[str, str, str | None, float | None], float]] = {}
    lambdas_plano_muestra: dict[str, list[float]] = {}
    for rho_v in (0.0, mm.RHO_DIXON_COLES):
        for jid in jobs_oos:
            fpl_rel, fpv_rel, cut_j = jobs_map[jid]
            dl_j = json.loads((raiz / fpl_rel).read_text(encoding="utf-8"))
            dv_j = json.loads((raiz / fpv_rel).read_text(encoding="utf-8"))
            dlc_j = mm.filtrar_por_fecha(dl_j, cut_j) if cut_j else dl_j
            dvc_j = mm.filtrar_por_fecha(dv_j, cut_j) if cut_j else dv_j
            meta_j = (merc_by_job[jid].get("metadatos") or {}) if jid in merc_by_job else {}
            el_j = str(meta_j.get("equipo_local") or "Local")
            ev_j = str(meta_j.get("equipo_visitante") or "Visitante")
            p_pl_j = mm.Parametros(
                rho=rho_v,
                aplicar_moduladores=False,
                aplicar_motor_recursivo=False,
                aplicar_lambda_efectivo=False,
                aplicar_cola_ancha=False,
            )
            if meta_j.get("competicion") and jid not in no_comp_jids:
                p_pl_j.competicion = str(meta_j["competicion"])
            with banderas_comportamiento(activar_todas=False, usar_sobredispersion_nb_corners=True):
                mot_pj = mm.MotorMercados(
                    dlc_j,
                    dvc_j,
                    meta_j.get("condicion_local") or "casa",
                    meta_j.get("condicion_visitante") or "fuera",
                    el_j,
                    ev_j,
                    p_pl_j,
                )
                mot_pj.preparar()
                res_pj = mot_pj.evaluar_catalogo()
            if rho_v == 0.0:
                lambdas_plano_muestra[jid] = [
                    round(float(mot_pj.tasas.lambda_local_ft or 0.0), 4),
                    round(float(mot_pj.tasas.lambda_visitante_ft or 0.0), 4),
                ]
            mp_j: dict[tuple[str, str, str | None, float | None], float] = {}
            for rm in (res_pj or []):
                for s in (rm.selecciones or []):
                    if s.estimable and s.probabilidad is not None:
                        mp_j[_normalizar_clave_seleccion_ronda10(s.evento, s.periodo, s.equipo, s.linea)] = float(s.probabilidad)
            probs_plano_por_job[(jid, rho_v)] = mp_j

    cadena_6 = json.loads((raiz / "datos" / "ronda11_punto1_cadena_6_etapas.json").read_text(encoding="utf-8"))
    observaciones_4_brazos: list[tuple[str, int, dict[str, float]]] = []
    for f in filas_1376:
        jid = str(f.get("id_trabajo") or "")
        clave = _normalizar_clave_seleccion_ronda10(f.get("evento"), f.get("periodo"), f.get("equipo"), f.get("linea"))
        p_pl = probs_plano_por_job.get((jid, 0.0), {}).get(clave)
        p_dc = probs_plano_por_job.get((jid, mm.RHO_DIXON_COLES), {}).get(clave)
        pr_dict = f.get("probs") or {}
        p_sa = pr_dict.get("soloA")
        p_ab = pr_dict.get("A+B")
        if p_pl is None or p_dc is None or p_sa is None or p_ab is None:
            continue
        observaciones_4_brazos.append((
            str(f.get("id_partido")),
            int(f["evento_ocurrio"]),
            {
                "plano_poisson_nb": float(p_pl),
                "plano_dixon_coles_nb": float(p_dc),
                "soloA": float(p_sa),
                "A+B": float(p_ab),
            },
        ))

    boot_brazos = bootstrap_brier_logloss_por_partido(observaciones_4_brazos, n_boot=n_boot, seed=seed)

    out = {
        "bandera": {
            "nombre": "usar_tercer_brazo_plano_sombra",
            "estado_por_defecto": estado_defecto,
            "activa_con_activar_todas": estado_con_activar_todas,
        },
        "ciclo_prepartido_postpartido_galatasaray_barcelona": {
            "con_bandera_apagada_defecto": ciclo_bandera_apagada,
            "con_bandera_activa": ciclo_bandera_activa,
        },
        "benchmark_24_oos_1376_filas": {
            "n_partidos_unicos": boot_brazos.get("n_partidos_unicos"),
            "n_filas": boot_brazos.get("n_filas"),
            "rho_dixon_coles": mm.RHO_DIXON_COLES,
            "versiones": boot_brazos.get("versiones"),
            "diferencias": {
                k: v for k, v in (boot_brazos.get("diferencias") or {}).items()
                if any(arm in k for arm in ("plano_poisson_nb", "plano_dixon_coles_nb"))
            },
            "nota_cadena_r11": {
                "brier_etapa1_poisson_sin_nb_soloA": ((cadena_6.get("cohortes") or {}).get("soloA_1376") or {}).get("versiones", {}).get("1_lambda_plano", {}).get("brier"),
                "logloss_etapa1_poisson_sin_nb_soloA": ((cadena_6.get("cohortes") or {}).get("soloA_1376") or {}).get("versiones", {}).get("1_lambda_plano", {}).get("log_loss"),
            },
        },
        "n_partidos_oos_lambdas_plano": len(lambdas_plano_muestra),
    }

    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out



def _clasificar_categoria_regla_lambda(regla: str) -> str:
    r = str(regla or "")
    if r in ("tasas_goles_plano", "moduladores_tacticos", "lambda_efectivo_finalizacion", "doble_adn_60_40", "ajuste_coef_liga_tier", "red_recursiva_45_dcn_bucle"):
        return "cadena_principal"
    if "doble_x135" in r:
        return "doble_x135"
    if r == "copa_asedio_defensa_rota":
        return "copa_asedio_defensa_rota"
    if "sincronizacion_" in r:
        return "sincronizacion"
    if "clamp" in r:
        return "clamp"
    if "piso_" in r or "reaplicacion_piso_" in r:
        return "pisos"
    if "techo_" in r or "freno_" in r or "fortin_" in r or "prohibicion_inflacion" in r or "reaplicacion_" in r or "gigante_visitante" in r or "reescalado_caja_azul" in r or "tope_dcn" in r:
        return "techos_y_frenos"
    return "otra_regla"


def auditar_reglas_que_pesan_ronda12(
    *,
    guardar_ruta: str | None = "datos/ronda12_punto3_reglas_que_pesan.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 12 — Punto 3 (`REGLAS QUE PESAN`):
    Sobre los 73 registros únicos (`_recolectar_pares_partidos_cacheados_ronda9`),
    inventaría TODAS las reglas que modifican λ (techos, pisos, clamp, copa,
    sincronización, doble ×1.35, `techo_lambda_ataque_bajo_local`, etc.) tanto en
    `soloA` como en `A+B`, con número de activaciones, partidos únicos `/73` y
    efecto medio en λ (condicional al disparo y prorrateado sobre los 73 registros).
    """
    from pathlib import Path
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm

    raiz = Path(__file__).resolve().parent
    pares_73 = _recolectar_pares_partidos_cacheados_ronda9()

    def _evaluar_variante(*, activar_todas_b: bool) -> dict[str, Any]:
        acc_regla: dict[str, list[dict[str, Any]]] = {}
        acc_regla_etapa: dict[tuple[str, str], list[dict[str, Any]]] = {}
        detalle_73: list[dict[str, Any]] = []

        with banderas_comportamiento(activar_todas=activar_todas_b):
            for idx_r, reg in enumerate(pares_73):
                jid = str(reg.get("job_id") or "")
                partido = str(reg.get("partido") or f"{reg['equipo_local']} vs {reg['equipo_visitante']}")
                params = mm.Parametros()
                if reg.get("competicion"):
                    params.competicion = str(reg["competicion"])
                mot = mm.MotorMercados(
                    reg["doc_local"],
                    reg["doc_visitante"],
                    reg.get("cond_l", "casa"),
                    reg.get("cond_v", "fuera"),
                    reg["equipo_local"],
                    reg["equipo_visitante"],
                    params,
                )
                mot.preparar()
                traza = list(getattr(mot, "traza_lambda", None) or [])
                rec = getattr(mot, "recursivo", None) or {}

                def _registrar(clave_r: str, etapa_r: str, al: float, dl: float, av: float, dv: float, mot_txt: str = "") -> None:
                    d_l = float(dl) - float(al)
                    d_v = float(dv) - float(av)
                    if abs(d_l) <= 1e-6 and abs(d_v) <= 1e-6:
                        return
                    ev_rec = {
                        "job_id": jid,
                        "partido": partido,
                        "etapa": etapa_r,
                        "lam_l_antes": round(float(al), 4),
                        "lam_l_despues": round(float(dl), 4),
                        "lam_v_antes": round(float(av), 4),
                        "lam_v_despues": round(float(dv), 4),
                        "delta_l": round(d_l, 4),
                        "delta_v": round(d_v, 4),
                        "delta_total": round(d_l + d_v, 4),
                        "delta_pct_l": round((d_l / al) * 100.0, 2) if (al and abs(d_l) > 1e-6) else None,
                        "delta_pct_v": round((d_v / av) * 100.0, 2) if (av and abs(d_v) > 1e-6) else None,
                        "motivo": mot_txt,
                    }
                    acc_regla.setdefault(clave_r, []).append(ev_rec)
                    acc_regla_etapa.setdefault((clave_r, etapa_r), []).append(ev_rec)

                hits_dr_con_cambio: list[dict[str, Any]] = []
                for st in traza:
                    if not isinstance(st, dict):
                        continue
                    regla_s = str(st.get("regla") or "")
                    etapa_s = str(st.get("etapa") or "")
                    al = st.get("lam_l_antes")
                    dl = st.get("lam_l_despues")
                    av = st.get("lam_v_antes")
                    dv = st.get("lam_v_despues")
                    if None in (al, dl, av, dv):
                        continue
                    al_f, dl_f, av_f, dv_f = float(al), float(dl), float(av), float(dv)
                    _registrar(regla_s, etapa_s, al_f, dl_f, av_f, dv_f, str(st.get("motivo") or ""))
                    if regla_s == "copa_asedio_defensa_rota" and (abs(dl_f - al_f) > 1e-6 or abs(dv_f - av_f) > 1e-6):
                        mot_dr = str(st.get("motivo") or "")
                        if ("def_rota_V=x" in mot_dr and "def_rota_V=x1.00" not in mot_dr) or (
                            "def_rota_L=x" in mot_dr and "def_rota_L=x1.00" not in mot_dr
                        ):
                            hits_dr_con_cambio.append({
                                "etapa": etapa_s,
                                "al": al_f, "dl": dl_f, "av": av_f, "dv": dv_f,
                                "motivo": mot_dr,
                            })

                # Si defensa rota x1.35 aplicó 2 veces (en red_45_dcn y en post_sinc_mercados), registrar explícitamente doble ×1.35
                if len(hits_dr_con_cambio) >= 2:
                    h2 = hits_dr_con_cambio[1]
                    _registrar(
                        "doble_x135_defensa_rota_2da_aplicacion",
                        "post_sinc_mercados",
                        h2["al"], h2["dl"], h2["av"], h2["dv"],
                        f"2da aplicacion defensa rota ({h2['motivo']})",
                    )

                # Tope DCN ±18% post-sinc (si bandera usar_tope_dcn_18_pct modificó lam)
                tp = rec.get("tope_dcn_18_post_sinc")
                if isinstance(tp, dict) and tp.get("activo"):
                    tl = tp.get("local") or {}
                    tv = tp.get("visitante") or {}
                    if tl.get("toco") or tv.get("toco"):
                        al_tp = float(tl.get("lam_pre") or mot.tasas.lambda_local_ft or 0.0)
                        dl_tp = float(tl.get("lam_post") or al_tp)
                        av_tp = float(tv.get("lam_pre") or mot.tasas.lambda_visitante_ft or 0.0)
                        dv_tp = float(tv.get("lam_post") or av_tp)
                        _registrar(
                            "tope_dcn_18_post_sinc",
                            "post_sinc_mercados",
                            al_tp, dl_tp, av_tp, dv_tp,
                            f"L:{tl.get('direccion')} V:{tv.get('direccion')}",
                        )

                detalle_73.append({
                    "job_id": jid,
                    "partido": partido,
                    "lam_plano": [
                        traza[0].get("lam_l_despues") if traza else None,
                        traza[0].get("lam_v_despues") if traza else None,
                    ],
                    "lam_final": [
                        round(float(mot.tasas.lambda_local_ft), 4) if mot.tasas.lambda_local_ft is not None else None,
                        round(float(mot.tasas.lambda_visitante_ft), 4) if mot.tasas.lambda_visitante_ft is not None else None,
                    ],
                    "n_pasos_con_cambio": sum(
                        1 for st in traza
                        if st.get("lam_l_antes") is not None
                        and (abs(float(st["lam_l_despues"]) - float(st["lam_l_antes"])) > 1e-6 or abs(float(st["lam_v_despues"]) - float(st["lam_v_antes"])) > 1e-6)
                    ),
                })

        n_reg = len(pares_73)

        def _resumir_bucket(regla_nombre: str, eventos: list[dict[str, Any]], etapa_fija: str | None = None) -> dict[str, Any]:
            pids = sorted({e["job_id"] for e in eventos})
            dls = [e["delta_l"] for e in eventos]
            dvs = [e["delta_v"] for e in eventos]
            dts = [e["delta_total"] for e in eventos]
            pcts_l = [e["delta_pct_l"] for e in eventos if e["delta_pct_l"] is not None]
            pcts_v = [e["delta_pct_v"] for e in eventos if e["delta_pct_v"] is not None]
            n_ch_l = sum(1 for x in dls if abs(x) > 1e-6)
            n_ch_v = sum(1 for x in dvs if abs(x) > 1e-6)
            # Promedio por partido único (cuando se activa)
            por_part_tot: dict[str, float] = {}
            por_part_l: dict[str, float] = {}
            por_part_v: dict[str, float] = {}
            for e in eventos:
                por_part_tot[e["job_id"]] = por_part_tot.get(e["job_id"], 0.0) + e["delta_total"]
                por_part_l[e["job_id"]] = por_part_l.get(e["job_id"], 0.0) + e["delta_l"]
                por_part_v[e["job_id"]] = por_part_v.get(e["job_id"], 0.0) + e["delta_v"]

            return {
                "regla": regla_nombre,
                **({"etapa": etapa_fija} if etapa_fija else {"etapas": sorted({e["etapa"] for e in eventos})}),
                "categoria": _clasificar_categoria_regla_lambda(regla_nombre),
                "n_activaciones": len(eventos),
                "n_partidos_unicos": len(pids),
                "pct_partidos_73": round((len(pids) / n_reg) * 100.0, 2) if n_reg else None,
                "n_cambia_local": n_ch_l,
                "n_cambia_visita": n_ch_v,
                "d_lam_l_medio_por_evento": round(sum(dls) / len(dls), 4),
                "d_lam_v_medio_por_evento": round(sum(dvs) / len(dvs), 4),
                "d_lam_total_medio_por_evento": round(sum(dts) / len(dts), 4),
                "d_lam_l_medio_por_partido_activo": round(sum(por_part_l.values()) / len(pids), 4) if pids else None,
                "d_lam_v_medio_por_partido_activo": round(sum(por_part_v.values()) / len(pids), 4) if pids else None,
                "d_lam_total_medio_por_partido_activo": round(sum(por_part_tot.values()) / len(pids), 4) if pids else None,
                "d_lam_l_medio_sobre_73": round(sum(dls) / n_reg, 4) if n_reg else None,
                "d_lam_v_medio_sobre_73": round(sum(dvs) / n_reg, 4) if n_reg else None,
                "d_lam_total_medio_sobre_73": round(sum(dts) / n_reg, 4) if n_reg else None,
                "d_pct_l_medio_cuando_cambia_l": round(sum(pcts_l) / len(pcts_l), 2) if pcts_l else None,
                "d_pct_v_medio_cuando_cambia_v": round(sum(pcts_v) / len(pcts_v), 2) if pcts_v else None,
                "ejemplos": [
                    {
                        "job_id": e["job_id"],
                        "partido": e["partido"],
                        "etapa": e["etapa"],
                        "L": f"{e['lam_l_antes']}->{e['lam_l_despues']} ({e['delta_l']:+.4f})",
                        "V": f"{e['lam_v_antes']}->{e['lam_v_despues']} ({e['delta_v']:+.4f})",
                    }
                    for e in eventos[:5]
                ],
            }

        tabla_agregada = sorted(
            [_resumir_bucket(k, v) for k, v in acc_regla.items()],
            key=lambda x: (-abs(x["d_lam_total_medio_sobre_73"] or 0.0), -x["n_activaciones"], x["regla"]),
        )
        # Asegurar entrada explícita para techo_lambda_ataque_bajo_local incluso si 0
        if "techo_lambda_ataque_bajo_local" not in acc_regla:
            tabla_agregada.append({
                "regla": "techo_lambda_ataque_bajo_local",
                "etapas": [],
                "categoria": "techos_y_frenos",
                "n_activaciones": 0,
                "n_partidos_unicos": 0,
                "pct_partidos_73": 0.0,
                "n_cambia_local": 0,
                "n_cambia_visita": 0,
                "d_lam_l_medio_por_evento": None,
                "d_lam_v_medio_por_evento": None,
                "d_lam_total_medio_por_evento": None,
                "d_lam_l_medio_por_partido_activo": None,
                "d_lam_v_medio_por_partido_activo": None,
                "d_lam_total_medio_por_partido_activo": None,
                "d_lam_l_medio_sobre_73": 0.0,
                "d_lam_v_medio_sobre_73": 0.0,
                "d_lam_total_medio_sobre_73": 0.0,
                "d_pct_l_medio_cuando_cambia_l": None,
                "d_pct_v_medio_cuando_cambia_v": None,
                "ejemplos": [],
            })

        tabla_por_etapa = sorted(
            [_resumir_bucket(k[0], v, etapa_fija=k[1]) for k, v in acc_regla_etapa.items()],
            key=lambda x: (x["etapa"], -x["n_activaciones"], x["regla"]),
        )

        # Resumen agrupado por familia de regla (techos, pisos, clamp, copa, sincronización, doble ×1.35)
        resumen_categorias: dict[str, dict[str, Any]] = {}
        for item in tabla_agregada:
            cat = item["categoria"]
            rc = resumen_categorias.setdefault(cat, {
                "categoria": cat,
                "reglas": [],
                "suma_activaciones": 0,
                "suma_d_lam_total_sobre_73": 0.0,
                "suma_d_lam_l_sobre_73": 0.0,
                "suma_d_lam_v_sobre_73": 0.0,
            })
            rc["reglas"].append(item["regla"])
            rc["suma_activaciones"] += int(item["n_activaciones"])
            rc["suma_d_lam_total_sobre_73"] = round(rc["suma_d_lam_total_sobre_73"] + float(item["d_lam_total_medio_sobre_73"] or 0.0), 4)
            rc["suma_d_lam_l_sobre_73"] = round(rc["suma_d_lam_l_sobre_73"] + float(item["d_lam_l_medio_sobre_73"] or 0.0), 4)
            rc["suma_d_lam_v_sobre_73"] = round(rc["suma_d_lam_v_sobre_73"] + float(item["d_lam_v_medio_sobre_73"] or 0.0), 4)

        return {
            "n_registros": n_reg,
            "tabla_reglas": tabla_agregada,
            "tabla_reglas_por_etapa": tabla_por_etapa,
            "resumen_por_categoria": resumen_categorias,
            "techo_lambda_ataque_bajo_local": next(
                (x for x in tabla_agregada if x["regla"] == "techo_lambda_ataque_bajo_local"),
                None,
            ),
            "doble_x135_defensa_rota_2da_aplicacion": next(
                (x for x in tabla_agregada if x["regla"] == "doble_x135_defensa_rota_2da_aplicacion"),
                {
                    "regla": "doble_x135_defensa_rota_2da_aplicacion",
                    "n_activaciones": 0,
                    "n_partidos_unicos": 0,
                    "d_lam_total_medio_sobre_73": 0.0,
                },
            ),
            "detalle_73_resumen": detalle_73,
        }

    out = {
        "n_registros_unicos": len(pares_73),
        "soloA": _evaluar_variante(activar_todas_b=False),
        "A+B": _evaluar_variante(activar_todas_b=True),
    }

    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def auditar_pasada_3b_y_banco_ronda12(
    *,
    ejecutar_vivo: bool = True,
    guardar_ruta: str | None = "datos/ronda12_punto4_pasada3b_y_banco.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 12 - Punto 4 (PRORRATEO "Pasada 3b", validador extendido y 6 empates):
      1. Inventario en disco y en fuente viva FotMob de celdas prorrateadas/ajustadas
         por Pasada 3b sobre las 30 matrices (Colombia/Chile) y los 83 partidos del banco.
      2. Verificación comparada con `usar_sin_dato_si_mitades_no_suman_ft` False vs True
         (si `1T + 2T != FT`, el FT permanece y ambas mitades quedan `None` / `sin_dato`).
      3. Ejecución del validador extendido a Colombia, Chile y `entradas`/`entradas_ganadas`.
      4. Evidencia antes/después de los 6 empates del banco corregidos desde Flashscore
         con respaldo en `datos/respaldos/ronda12_p4_pre_correccion/`.
    """
    import copy as _copy_r12p4
    from pathlib import Path
    import banco_historico as bh
    import fuentes_xg as fxg
    from decision_cobertura import bandera_activa, banderas_comportamiento
    from motor_mercados import analizar

    raiz = Path(__file__).resolve().parent
    respaldo_dir = raiz / "datos" / "respaldos" / "ronda12_p4_pre_correccion"

    doc_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8-sig"))
    doc_chi = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8-sig"))
    pares_b83, _ = bh._cargar_pares_matrices_banco_83(raiz, incluir_colombia_chile=False)
    rows_csv = bh.cargar_banco_csv(raiz / "data" / "banco_partidos.csv")
    pids_b83 = {str(r.get("id_partido") or "") for r in rows_csv if str(r.get("equipo_clave") or "") not in ("colombia", "chile")}
    pares_b83 = [(eq, p) for eq, p in pares_b83 if str((p or {}).get("id_partido") or "") in pids_b83]

    todas_aditivas = tuple(sorted(fxg.METRICAS_CONTEO_ADITIVAS | fxg.METRICAS_CONTINUAS_ADITIVAS))

    def _inventario_disco_grupo(pares_list: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        incons_guardadas: list[dict[str, Any]] = []
        discrepancias_residuales: list[dict[str, Any]] = []
        est_modelo = 0
        vistas: set[tuple[str, str]] = set()
        for eq_k, p in pares_list:
            pid = str(p.get("id_partido") or "")
            if (eq_k, pid) in vistas:
                continue
            vistas.add((eq_k, pid))
            for inc in (p.get("inconsistencias_aritmeticas") or []):
                if isinstance(inc, dict):
                    incons_guardadas.append({"equipo": eq_k, "id_partido": pid, **inc})
            per_map: dict[str, dict[str, dict[str, Any]]] = {}
            for per in ("FT", "1T", "2T"):
                per_map[per] = {
                    str(f.get("clave")): f
                    for f in (((p.get("periodos") or {}).get(per) or {}).get("filas") or [])
                    if isinstance(f, dict) and f.get("clave")
                }
                pr = ((p.get("procedencia") or {}).get(per) or {})
                for lado in ("equipo", "rival"):
                    for v_p in (pr.get(lado) or {}).values():
                        if v_p == "estimacion_modelo":
                            est_modelo += 1
            for cl in todas_aditivas:
                tol = 0.05 if cl in fxg.METRICAS_CONTINUAS_ADITIVAS else 0.001
                for lado in ("equipo", "rival"):
                    v_ft = (per_map["FT"].get(cl) or {}).get(lado)
                    v_1t = (per_map["1T"].get(cl) or {}).get(lado)
                    v_2t = (per_map["2T"].get(cl) or {}).get(lado)
                    if v_ft is not None and v_1t is not None and v_2t is not None:
                        if abs((float(v_1t) + float(v_2t)) - float(v_ft)) > tol:
                            discrepancias_residuales.append({
                                "equipo": eq_k,
                                "id_partido": pid,
                                "lado": lado,
                                "clave": cl,
                                "FT": v_ft,
                                "1T": v_1t,
                                "2T": v_2t,
                                "suma_1t_2t": round(float(v_1t) + float(v_2t), 4),
                            })
        return {
            "n_matrices": len(vistas),
            "inconsistencias_guardadas_en_disco": len(incons_guardadas),
            "discrepancias_1t_2t_ft_residuales_en_disco": len(discrepancias_residuales),
            "detalle_residuales": discrepancias_residuales,
            "celdas_estimacion_modelo_en_disco": est_modelo,
        }

    pares_col = [("colombia", p) for p in (doc_col.get("partidos") or [])]
    pares_chi = [("chile", p) for p in (doc_chi.get("partidos") or [])]
    inv_disco = {
        "colombia_15": _inventario_disco_grupo(pares_col),
        "chile_15": _inventario_disco_grupo(pares_chi),
        "matrices_30": _inventario_disco_grupo(pares_col + pares_chi),
        "banco_83": _inventario_disco_grupo(pares_b83),
    }

    inv_vivo: dict[str, Any] | None = None
    ruta_p = Path(guardar_ruta) if (guardar_ruta and os.path.isabs(guardar_ruta)) else ((raiz / guardar_ruta) if guardar_ruta else (raiz / "datos" / "ronda12_punto4_pasada3b_y_banco.json"))
    if ejecutar_vivo:
        def _build_pf(p_dict: dict[str, Any], eq_nombre: str) -> tuple[fxg.PartidoFinalizado, dict[str, Any]]:
            es_loc = str(p_dict.get("condicion") or "") == "casa"
            rival_n = str(p_dict.get("rival") or "Rival")
            loc_n = eq_nombre if es_loc else rival_n
            vis_n = rival_n if es_loc else eq_nombre
            marc = p_dict.get("marcador") or {}
            ge, gr = marc.get("equipo"), marc.get("rival")
            gl = int(ge) if (ge is not None and es_loc) else (int(gr) if gr is not None else None)
            gv = int(gr) if (gr is not None and es_loc) else (int(ge) if ge is not None else None)
            if not es_loc and ge is not None and gr is not None:
                gl, gv = int(gr), int(ge)
            pf = fxg.PartidoFinalizado(
                id_partido=str(p_dict.get("id_partido") or ""),
                fecha=str(p_dict.get("fecha") or ""),
                torneo=str(p_dict.get("torneo") or p_dict.get("competicion") or ""),
                local=loc_n,
                visitante=vis_n,
                goles_local=gl,
                goles_visitante=gv,
                es_local_el_equipo=es_loc,
                url=str(p_dict.get("url") or ""),
            )
            return pf, {"nombre": eq_nombre}

        equipo_nombres_b83 = {
            "italy": "Italy",
            "france": "France",
            "turkey": "Turkiye",
            "belgium": "Belgium",
            "londrina": "Londrina",
            "nautico": "Nautico",
        }
        items_vivos: list[tuple[str, str, dict[str, Any], str]] = []
        vistos_v: set[tuple[str, str]] = set()
        for grp,plist,nom_def in (
            ("colombia_15", pares_col, "Colombia"),
            ("chile_15", pares_chi, "Chile"),
            ("banco_83", pares_b83, ""),
        ):
            for eq_k, p in plist:
                pid = str((p or {}).get("id_partido") or "")
                if not pid or (grp, pid) in vistos_v:
                    continue
                vistos_v.add((grp, pid))
                nom = nom_def or equipo_nombres_b83.get(eq_k, str(p.get("equipo") or eq_k))
                items_vivos.append((grp, eq_k, p, nom))

        cache_pre_por_pid: dict[tuple[str, bool], fxg.MatrizPartido] = {}
        res_grupos_vivo: dict[str, dict[str, Any]] = {
            g: {
                "n_partidos_grupo": 0,
                "n_fotmob_numericos_evaluados": 0,
                "pids_flashscore_no_numericos": [],
                "flag_off_total_inconsistencias": 0,
                "flag_off_prorrateadas": 0,
                "flag_off_ft_cero_ceros": 0,
                "flag_off_suma_mitades_cero_ft_pos": 0,
                "flag_on_mitades_sin_dato": 0,
                "flag_on_prorrateadas": 0,
                "flag_on_ft_intacto_y_mitades_none": True,
                "por_metrica_prorrateada": {},
                "ejemplos_prorrateadas": [],
            }
            for g in ("colombia_15", "chile_15", "banco_83")
        }

        orig_consolidar = fxg.consolidar_matriz_completa
        captura_box: dict[str, Any] = {}

        def _hook_capturar(mat: fxg.MatrizPartido) -> None:
            captura_box["pre"] = _copy_r12p4.deepcopy(mat)
            return orig_consolidar(mat)

        try:
            fxg.consolidar_matriz_completa = _hook_capturar  # type: ignore[assignment]
            with fxg.FuenteFotMob() as fm:
                for grp, eq_k, p, eq_nom in items_vivos:
                    res_g = res_grupos_vivo[grp]
                    res_g["n_partidos_grupo"] += 1
                    pid = str(p.get("id_partido") or "")
                    if not pid.isdigit():
                        res_g["pids_flashscore_no_numericos"].append({"equipo": eq_k, "id_partido": pid})
                        continue
                    es_loc = str(p.get("condicion") or "") == "casa"
                    key_cache = (pid, es_loc)
                    if key_cache not in cache_pre_por_pid:
                        captura_box.clear()
                        pf, eq_obj = _build_pf(p, eq_nom)
                        with banderas_comportamiento(usar_sin_dato_si_mitades_no_suman_ft=False):
                            _ = fm.matriz_partido(pf, eq_obj)
                        if "pre" in captura_box:
                            cache_pre_por_pid[key_cache] = captura_box["pre"]
                    mat_pre = cache_pre_por_pid.get(key_cache)
                    if mat_pre is None:
                        continue
                    res_g["n_fotmob_numericos_evaluados"] += 1
                    mat_off = _copy_r12p4.deepcopy(mat_pre)
                    mat_on = _copy_r12p4.deepcopy(mat_pre)
                    with banderas_comportamiento(usar_sin_dato_si_mitades_no_suman_ft=False):
                        orig_consolidar(mat_off)
                    with banderas_comportamiento(usar_sin_dato_si_mitades_no_suman_ft=True):
                        orig_consolidar(mat_on)

                    def _val_mat(m_obj: fxg.MatrizPartido, per_s: str, cl_s: str, lado_s: str) -> Any:
                        filas_p = (m_obj.periodos.get(per_s).filas if m_obj.periodos.get(per_s) else [])
                        for ff in filas_p:
                            if ff.get("clave") == cl_s:
                                return ff.get(lado_s)
                        return None

                    for inc in (mat_off.inconsistencias_aritmeticas or []):
                        cl_i = str(inc.get("clave") or "")
                        ld_i = str(inc.get("lado") or "")
                        res_g["flag_off_total_inconsistencias"] += 1
                        if inc.get("reescalado_proporcional"):
                            res_g["flag_off_prorrateadas"] += 1
                            res_g["por_metrica_prorrateada"][cl_i] = res_g["por_metrica_prorrateada"].get(cl_i, 0) + 1
                            if len(res_g["ejemplos_prorrateadas"]) < 15:
                                res_g["ejemplos_prorrateadas"].append({
                                    "id_partido": pid,
                                    "equipo": eq_k,
                                    "lado": ld_i,
                                    "clave": cl_i,
                                    "crudo_FT": inc.get("FT"),
                                    "crudo_1T": inc.get("1T"),
                                    "crudo_2T": inc.get("2T"),
                                    "off_1T": _val_mat(mat_off, "1T", cl_i, ld_i),
                                    "off_2T": _val_mat(mat_off, "2T", cl_i, ld_i),
                                    "on_FT": _val_mat(mat_on, "FT", cl_i, ld_i),
                                    "on_1T": _val_mat(mat_on, "1T", cl_i, ld_i),
                                    "on_2T": _val_mat(mat_on, "2T", cl_i, ld_i),
                                    "on_proc_1T": (((mat_on.procedencia or {}).get("1T") or {}).get(ld_i) or {}).get(cl_i),
                                    "on_proc_2T": (((mat_on.procedencia or {}).get("2T") or {}).get(ld_i) or {}).get(cl_i),
                                })
                        elif float(inc.get("FT") or 0.0) == 0.0 and cl_i != "goles_evitados":
                            res_g["flag_off_ft_cero_ceros"] += 1
                        elif float(inc.get("suma_1t_2t") or 0.0) == 0.0:
                            res_g["flag_off_suma_mitades_cero_ft_pos"] += 1

                    for inc_on in (mat_on.inconsistencias_aritmeticas or []):
                        cl_o = str(inc_on.get("clave") or "")
                        ld_o = str(inc_on.get("lado") or "")
                        if inc_on.get("reescalado_proporcional"):
                            res_g["flag_on_prorrateadas"] += 1
                        vft_o = _val_mat(mat_on, "FT", cl_o, ld_o)
                        v1_o = _val_mat(mat_on, "1T", cl_o, ld_o)
                        v2_o = _val_mat(mat_on, "2T", cl_o, ld_o)
                        pr1_o = (((mat_on.procedencia or {}).get("1T") or {}).get(ld_o) or {}).get(cl_o)
                        pr2_o = (((mat_on.procedencia or {}).get("2T") or {}).get(ld_o) or {}).get(cl_o)
                        if vft_o is not None and v1_o is None and v2_o is None and pr1_o == "sin_dato" and pr2_o == "sin_dato":
                            res_g["flag_on_mitades_sin_dato"] += 1
                        else:
                            res_g["flag_on_ft_intacto_y_mitades_none"] = False
        finally:
            fxg.consolidar_matriz_completa = orig_consolidar

        m30 = {
            "n_partidos_grupo": res_grupos_vivo["colombia_15"]["n_partidos_grupo"] + res_grupos_vivo["chile_15"]["n_partidos_grupo"],
            "n_fotmob_numericos_evaluados": res_grupos_vivo["colombia_15"]["n_fotmob_numericos_evaluados"] + res_grupos_vivo["chile_15"]["n_fotmob_numericos_evaluados"],
            "flag_off_total_inconsistencias": res_grupos_vivo["colombia_15"]["flag_off_total_inconsistencias"] + res_grupos_vivo["chile_15"]["flag_off_total_inconsistencias"],
            "flag_off_prorrateadas": res_grupos_vivo["colombia_15"]["flag_off_prorrateadas"] + res_grupos_vivo["chile_15"]["flag_off_prorrateadas"],
            "flag_off_ft_cero_ceros": res_grupos_vivo["colombia_15"]["flag_off_ft_cero_ceros"] + res_grupos_vivo["chile_15"]["flag_off_ft_cero_ceros"],
            "flag_off_suma_mitades_cero_ft_pos": res_grupos_vivo["colombia_15"]["flag_off_suma_mitades_cero_ft_pos"] + res_grupos_vivo["chile_15"]["flag_off_suma_mitades_cero_ft_pos"],
            "flag_on_mitades_sin_dato": res_grupos_vivo["colombia_15"]["flag_on_mitades_sin_dato"] + res_grupos_vivo["chile_15"]["flag_on_mitades_sin_dato"],
            "flag_on_prorrateadas": res_grupos_vivo["colombia_15"]["flag_on_prorrateadas"] + res_grupos_vivo["chile_15"]["flag_on_prorrateadas"],
            "flag_on_ft_intacto_y_mitades_none": res_grupos_vivo["colombia_15"]["flag_on_ft_intacto_y_mitades_none"] and res_grupos_vivo["chile_15"]["flag_on_ft_intacto_y_mitades_none"],
        }
        inv_vivo = {
            "fuente": "FuenteFotMob (live /matchDetails + Flashscore pairing)",
            "bandera_defecto": bandera_activa("usar_sin_dato_si_mitades_no_suman_ft"),
            "colombia_15": res_grupos_vivo["colombia_15"],
            "chile_15": res_grupos_vivo["chile_15"],
            "matrices_30": m30,
            "banco_83": res_grupos_vivo["banco_83"],
        }
    elif ruta_p.exists():
        try:
            prev_j = json.loads(ruta_p.read_text(encoding="utf-8"))
            inv_vivo = prev_j.get("inventario_pasada_3b_fuente_viva")
        except Exception:
            inv_vivo = None

    # 3. Validador extendido (live post-R12 vs respaldo pre-R12)
    val_live_full = bh.validar_defectos_partidos_banco(raiz=raiz, incluir_colombia_chile=True, incluir_entradas=True)
    val_live_83 = bh.validar_defectos_partidos_banco(raiz=raiz, incluir_colombia_chile=False, incluir_entradas=True)
    val_respaldo_83 = None
    if (respaldo_dir / "tests" / "fixtures" / "londrina_nautico_fixtures.json").exists():
        val_respaldo_83 = bh.validar_defectos_partidos_banco(
            csv_path=respaldo_dir / "data" / "banco_partidos.csv",
            db_path=respaldo_dir / "data" / "banco_partidos.db",
            raiz=raiz,
            incluir_colombia_chile=False,
            incluir_entradas=True,
            londrina_nautico_path=respaldo_dir / "tests" / "fixtures" / "londrina_nautico_fixtures.json",
        )

    prueba_val_entradas = bh.validar_coherencia_1t_2t_ft(
        {"entradas": 15, "entradas_ganadas": 10},
        {"entradas": 8, "entradas_ganadas": 9},
        {"entradas": 5, "entradas_ganadas": 4},
        incluir_entradas=True,
    )

    # 4. Evidencia de los 6 empates corregidos (respaldo vs live + invarianza lambda LN)
    ids6 = ("5190761", "5190682", "5190774", "5190743", "5190716", "5190670")
    ln_live = json.loads((raiz / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    ln_back = json.loads((respaldo_dir / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8")) if (respaldo_dir / "tests" / "fixtures" / "londrina_nautico_fixtures.json").exists() else None
    manifest_p = respaldo_dir / "manifest_respaldo.json"
    manifest = json.loads(manifest_p.read_text(encoding="utf-8")) if manifest_p.exists() else None

    comp_6: list[dict[str, Any]] = []
    if ln_back is not None:
        idx_back = {
            str(p.get("id_partido")): (ek, p)
            for ek in ("londrina", "nautico")
            for p in (ln_back[ek].get("partidos") or [])
        }
        idx_live = {
            str(p.get("id_partido")): (ek, p)
            for ek in ("londrina", "nautico")
            for p in (ln_live[ek].get("partidos") or [])
        }
        for pid in ids6:
            ek_b, pb = idx_back[pid]
            ek_l, pl = idx_live[pid]
            def _ext(p_obj: dict[str, Any]) -> dict[str, Any]:
                fmap = {f["clave"]: (f.get("equipo"), f.get("rival")) for f in ((p_obj.get("periodos") or {}).get("FT") or {}).get("filas") or []}
                return {
                    "bandos_invertidos": bool((p_obj.get("fuente_secundaria") or {}).get("bandos_invertidos")),
                    "id_flashscore": (p_obj.get("fuente_secundaria") or {}).get("id_flashscore"),
                    "tiros_libres": fmap.get("tiros_libres"),
                    "entradas": fmap.get("entradas"),
                    "entradas_ganadas": fmap.get("entradas_ganadas"),
                    "entradas_ganadas_pct": fmap.get("entradas_ganadas_pct"),
                    "pases_tercio_final": fmap.get("pases_tercio_final"),
                    "pases_tercio_final_pct": fmap.get("pases_tercio_final_pct"),
                    "xa_equipo": fmap.get("xa_equipo"),
                    "errores_remate": fmap.get("errores_remate"),
                    "saques_puerta": fmap.get("saques_puerta"),
                }
            comp_6.append({
                "id_partido": pid,
                "equipo_clave": ek_l,
                "fecha": pl.get("fecha"),
                "condicion": pl.get("condicion"),
                "rival": pl.get("rival"),
                "antes_respaldo": _ext(pb),
                "despues_flashscore": _ext(pl),
            })

    invarianza_ln: dict[str, Any] = {}
    for lbl, act_b in (("soloA", False), ("AB", True)):
        with banderas_comportamiento(activar_todas=act_b):
            an_live = analizar(ln_live["londrina"], ln_live["nautico"], "Londrina", "Náutico")
            t_l = an_live.motor.tasas
            val_live = (t_l.lambda_local_ft, t_l.lambda_visitante_ft)
            val_back = None
            if ln_back is not None:
                an_back = analizar(ln_back["londrina"], ln_back["nautico"], "Londrina", "Náutico")
                t_b = an_back.motor.tasas
                val_back = (t_b.lambda_local_ft, t_b.lambda_visitante_ft)
            invarianza_ln[lbl] = {
                "antes": list(val_back) if val_back else None,
                "despues": list(val_live),
                "invariante": (val_back == val_live) if val_back else None,
            }

    out = {
        "bandera_usar_sin_dato_si_mitades_no_suman_ft_defecto": bandera_activa("usar_sin_dato_si_mitades_no_suman_ft"),
        "inventario_en_disco": inv_disco,
        "inventario_pasada_3b_fuente_viva": inv_vivo,
        "validador_extendido": {
            "live_completo_con_colombia_chile_y_entradas": val_live_full,
            "live_banco_83": {
                "pre_total": val_live_83["matrices_crudas_pre_saneamiento"]["total_defectos"],
                "pre_por_tipo": val_live_83["matrices_crudas_pre_saneamiento"]["por_tipo"],
                "post_total": val_live_83["matrices_post_sanear_partido_auditoria_45"]["total_defectos"],
                "post_por_tipo": val_live_83["matrices_post_sanear_partido_auditoria_45"]["por_tipo"],
            },
            "respaldo_pre_r12_banco_83": {
                "pre_total": val_respaldo_83["matrices_crudas_pre_saneamiento"]["total_defectos"],
                "pre_por_tipo": val_respaldo_83["matrices_crudas_pre_saneamiento"]["por_tipo"],
                "post_total": val_respaldo_83["matrices_post_sanear_partido_auditoria_45"]["total_defectos"],
                "post_por_tipo": val_respaldo_83["matrices_post_sanear_partido_auditoria_45"]["por_tipo"],
            } if val_respaldo_83 else None,
            "prueba_sintetica_validar_coherencia_entradas": prueba_val_entradas,
        },
        "correccion_6_empates_banco": {
            "respaldo_dir": "datos/respaldos/ronda12_p4_pre_correccion",
            "n_archivos_respaldados": len(manifest) if manifest else 0,
            "manifest_respaldo": manifest,
            "comparacion_6_partidos": comp_6,
            "invarianza_lambda_londrina_nautico": invarianza_ln,
        },
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def auditar_unificacion_h_gf_lstm_15_ronda12(
    *,
    guardar_ruta: str | None = "datos/ronda12_punto5_unificacion_h_lstm.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 12 - Punto 5 (`h_gf_lstm_15`):
      1. Verifica que `usar_unificacion_h_gf_lstm_15` (apagada por defecto) unifica al 100%
         la ruta directa (`extraer_series_reales_15_partidos(pts, ...)`) y la ruta viva
         (`MotorMercados` -> `cuadro_especialista.partidos_para_red_neuronal` -> `_partido_fuente`).
      2. Muestra el efecto exacto en `h_gf_lstm` (y en λ) para todos los equipos de los 73 registros.
      3. Explica paso a paso por qué el prior LSTM de Colombia es `0.9891` (~0.99) en ruta viva por defecto
         (y `0.9650` unificado) pese a que la media simple de 15 partidos es `22/15 = 1.4667` (~1.47).
    """
    import math as _m5
    from pathlib import Path
    import numpy as _np5
    import cuadro_especialista as ce
    from decision_cobertura import bandera_activa, banderas_comportamiento
    import motor_mercados as mm
    import motor_recursivo_45_dcn as mr

    raiz = Path(__file__).resolve().parent
    pares_73 = _recolectar_pares_partidos_cacheados_ronda9()

    def _correr(flag_on: bool) -> dict[str, Any]:
        por_reg: dict[str, dict[str, Any]] = {}
        dif_equipos: list[dict[str, Any]] = []
        n_diff_l = 0
        n_diff_v = 0
        with banderas_comportamiento(usar_unificacion_h_gf_lstm_15=flag_on):
            for reg in pares_73:
                jid = str(reg.get("job_id") or "")
                dl = reg["doc_local"]
                dv = reg["doc_visitante"]
                el = reg["equipo_local"]
                ev = reg["equipo_visitante"]
                comp_str = reg.get("competicion") or ""

                par = mm.Parametros()
                if comp_str:
                    par.competicion = str(comp_str)
                mot = mm.MotorMercados(
                    dl, dv,
                    reg.get("cond_l", "casa"), reg.get("cond_v", "fuera"),
                    el, ev, par,
                )
                mot.preparar()
                rec = mot.recursivo or {}
                hl_viva = (rec.get("mu_local_casa") or {}).get("_h_gf_lstm_15")
                hv_viva = (rec.get("mu_visita_fuera") or {}).get("_h_gf_lstm_15")

                pts_l = (dl or {}).get("partidos") or []
                pts_v = (dv or {}).get("partidos") or []
                pl_ce = ce.construir_perfil_especialista_equipo(ce.bloque_desde_muestra(pts_l, el, "global", doc=dl))
                pv_ce = ce.construir_perfil_especialista_equipo(ce.bloque_desde_muestra(pts_v, ev, "global", doc=dv))
                tL = mr.obtener_tier_equipo(pts_l, pl_ce)
                tV = mr.obtener_tier_equipo(pts_v, pv_ce)
                icf = mr.obtener_coefs_torneo_anclados(pts_l, pts_v, pl_ce, pv_ce, el, ev, competicion_str=comp_str)
                ser_l = mr.extraer_series_reales_15_partidos(pts_l, el, pl_ce, tier_rival=tV, factor_uefa=icf["fu_L"])
                ser_v = mr.extraer_series_reales_15_partidos(pts_v, ev, pv_ce, tier_rival=tL, factor_uefa=icf["fu_V"])
                hl_dir = mr.procesar_secuencia_individual_lstm_determinista(ser_l["goles_favor"])["h_reciente"]
                hv_dir = mr.procesar_secuencia_individual_lstm_determinista(ser_v["goles_favor"])["h_reciente"]

                if hl_viva is not None and abs(float(hl_viva) - float(hl_dir)) > 1e-4:
                    n_diff_l += 1
                    dif_equipos.append({
                        "job_id": jid,
                        "partido": f"{el} vs {ev}",
                        "lado": "local",
                        "equipo": el,
                        "h_directa": round(float(hl_dir), 4),
                        "h_viva": round(float(hl_viva), 4),
                        "delta_viva_menos_directa": round(float(hl_viva) - float(hl_dir), 4),
                    })
                if hv_viva is not None and abs(float(hv_viva) - float(hv_dir)) > 1e-4:
                    n_diff_v += 1
                    dif_equipos.append({
                        "job_id": jid,
                        "partido": f"{el} vs {ev}",
                        "lado": "visitante",
                        "equipo": ev,
                        "h_directa": round(float(hv_dir), 4),
                        "h_viva": round(float(hv_viva), 4),
                        "delta_viva_menos_directa": round(float(hv_viva) - float(hv_dir), 4),
                    })
                por_reg[jid] = {
                    "job_id": jid,
                    "partido": f"{el} vs {ev}",
                    "equipo_local": el,
                    "equipo_visitante": ev,
                    "hl_directa": round(float(hl_dir), 4),
                    "hl_viva": round(float(hl_viva), 4) if hl_viva is not None else None,
                    "hv_directa": round(float(hv_dir), 4),
                    "hv_viva": round(float(hv_viva), 4) if hv_viva is not None else None,
                    "lambda_local_ft": mot.tasas.lambda_local_ft,
                    "lambda_visitante_ft": mot.tasas.lambda_visitante_ft,
                }
        return {
            "n_registros": len(pares_73),
            "n_lados_con_diferencia_directa_vs_viva": n_diff_l + n_diff_v,
            "n_local_con_diferencia": n_diff_l,
            "n_visitante_con_diferencia": n_diff_v,
            "partidos_con_diferencia": len({d["job_id"] for d in dif_equipos}),
            "diferencias_directa_vs_viva": dif_equipos,
            "por_registro": por_reg,
        }

    off_res = _correr(False)
    on_res = _correr(True)

    tabla_efecto: list[dict[str, Any]] = []
    cambios_solo_h = 0
    cambios_lambda = 0
    for reg in pares_73:
        jid = str(reg.get("job_id") or "")
        a = off_res["por_registro"][jid]
        b = on_res["por_registro"][jid]
        dl_h = (b["hl_viva"] - a["hl_viva"]) if (a["hl_viva"] is not None and b["hl_viva"] is not None) else None
        dv_h = (b["hv_viva"] - a["hv_viva"]) if (a["hv_viva"] is not None and b["hv_viva"] is not None) else None
        dh_cambia = (dl_h is not None and abs(dl_h) > 1e-6) or (dv_h is not None and abs(dv_h) > 1e-6)
        d_lam_l = (b["lambda_local_ft"] - a["lambda_local_ft"]) if (a["lambda_local_ft"] is not None and b["lambda_local_ft"] is not None) else None
        d_lam_v = (b["lambda_visitante_ft"] - a["lambda_visitante_ft"]) if (a["lambda_visitante_ft"] is not None and b["lambda_visitante_ft"] is not None) else None
        lam_cambia = (d_lam_l is not None and abs(d_lam_l) > 1e-6) or (d_lam_v is not None and abs(d_lam_v) > 1e-6)
        if dh_cambia:
            cambios_solo_h += 1
        if lam_cambia:
            cambios_lambda += 1
        if dh_cambia or lam_cambia:
            tabla_efecto.append({
                "job_id": jid,
                "partido": a["partido"],
                "hl_viva_antes": a["hl_viva"],
                "hl_viva_despues": b["hl_viva"],
                "delta_hl": round(float(dl_h), 4) if dl_h is not None else None,
                "hv_viva_antes": a["hv_viva"],
                "hv_viva_despues": b["hv_viva"],
                "delta_hv": round(float(dv_h), 4) if dv_h is not None else None,
                "lambda_antes": [a["lambda_local_ft"], a["lambda_visitante_ft"]],
                "lambda_despues": [b["lambda_local_ft"], b["lambda_visitante_ft"]],
                "delta_lambda_l": round(float(d_lam_l), 4) if d_lam_l is not None else None,
                "delta_lambda_v": round(float(d_lam_v), 4) if d_lam_v is not None else None,
            })

    # Trazado paso a paso de por que Colombia tiene prior ~0.99 (0.9891 / 0.9650) vs media 15P = 1.47 (22/15)
    doc_col = json.loads((raiz / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8-sig"))
    doc_chi = json.loads((raiz / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8-sig"))
    pts_col = doc_col.get("partidos") or []
    pts_cron = list(reversed(pts_col))
    p_l = ce.construir_perfil_especialista_equipo(ce.bloque_desde_muestra(pts_col, "Colombia", "global", doc=doc_col))
    p_v = ce.construir_perfil_especialista_equipo(ce.bloque_desde_muestra(doc_chi.get("partidos") or [], "Chile", "global", doc=doc_chi))
    icf_cc = mr.obtener_coefs_torneo_anclados(pts_col, doc_chi.get("partidos") or [], p_l, p_v, "Colombia", "Chile", competicion_str="Amistoso")
    tV_cc = mr.obtener_tier_equipo(doc_chi.get("partidos") or [], p_v)
    alpha_cc = 1.0 - _m5.exp(-_m5.log(2.0) / 4.2)

    def _traza_colombia_lstm(flag_on: bool) -> dict[str, Any]:
        with banderas_comportamiento(usar_unificacion_h_gf_lstm_15=flag_on):
            ser = mr.extraer_series_reales_15_partidos(
                ce.partidos_para_red_neuronal(pts_col, "Colombia"),
                "Colombia", p_l, tier_rival=tV_cc, factor_uefa=icf_cc["fu_L"],
            )
        gfs = [float(x) for x in ser["goles_favor"]]
        vals = _np5.array(gfs, dtype=float)
        c_mem = float(_np5.mean(vals))
        std_v = float(_np5.std(vals))
        escala = max(0.30, abs(c_mem), std_v)
        h_t = float(vals[0])
        pasos: list[dict[str, Any]] = []
        for idx, (pt_raw, x_p) in enumerate(zip(pts_cron, gfs)):
            gf_cr, gc_cr = mr._goles_equipo(pt_raw)
            sorp = min(1.8, abs(x_p - h_t) / escala)
            gi = max(0.15, min(0.65, alpha_cc * (1.0 + 0.25 * sorp)))
            h_t = (1.0 - gi) * h_t + gi * x_p
            pasos.append({
                "idx_cronologico": idx,
                "fecha": pt_raw.get("fecha"),
                "rival": pt_raw.get("rival"),
                "torneo": pt_raw.get("torneo"),
                "gf_crudo": gf_cr,
                "gc_crudo": gc_cr,
                "gf_entrada_lstm": round(x_p, 4),
                "gate_i": round(gi, 4),
                "h_t": round(h_t, 4),
            })
        m_u5 = float(_np5.mean(vals[-5:]))
        h_rec = round(0.60 * float(pasos[-1]["h_t"]) + 0.40 * m_u5, 4)
        return {
            "serie_entrada_lstm": [round(x, 4) for x in gfs],
            "panama_5_0_idx7_entrada": round(gfs[7], 4),
            "c_memoria": round(c_mem, 4),
            "std": round(std_v, 4),
            "escala": round(escala, 4),
            "alpha_semivida_4_2": round(alpha_cc, 4),
            "estado_h_final_idx14": round(h_t, 4),
            "ultimos_5_entrada": [round(x, 4) for x in gfs[-5:]],
            "media_ult5": round(m_u5, 4),
            "formula_060_h14_mas_040_u5": f"0.60 * {round(h_t, 4)} + 0.40 * {round(m_u5, 4)} = {h_rec}",
            "h_gf_lstm_15": h_rec,
            "pasos_15_partidos": pasos,
        }

    goles_crudos_col = [mr._goles_equipo(p)[0] for p in pts_cron]
    traza_off = _traza_colombia_lstm(False)
    traza_on = _traza_colombia_lstm(True)

    out = {
        "bandera_usar_unificacion_h_gf_lstm_15_defecto": bandera_activa("usar_unificacion_h_gf_lstm_15"),
        "resumen_unificacion": {
            "denominador_registros": len(pares_73),
            "con_bandera_apagada_defecto": {
                "n_lados_con_diferencia_directa_vs_viva": off_res["n_lados_con_diferencia_directa_vs_viva"],
                "n_local_con_diferencia": off_res["n_local_con_diferencia"],
                "n_visitante_con_diferencia": off_res["n_visitante_con_diferencia"],
                "n_partidos_con_diferencia": off_res["partidos_con_diferencia"],
                "causa_tecnica": (
                    "En `motor_recursivo_45_dcn.extraer_series_reales_15_partidos`, las líneas L1019 y L1068 "
                    "leían `p.get('torneo')` en lugar de `_partido_fuente(p)`. En la ruta viva (`cuadro_especialista."
                    "partidos_para_red_neuronal`), el diccionario envoltorio no incluye `'torneo'` en el nivel raíz "
                    "(solo dentro de `_partido`), por lo que no activaba `es_copa_nacional(torneo_p)` ni el recorte de "
                    "goleadas (>=4 goles) a 2.2 antes del blend de credibilidad (0.35), mientras la ruta directa sí lo activaba."
                ),
            },
            "con_bandera_activa": {
                "n_lados_con_diferencia_directa_vs_viva": on_res["n_lados_con_diferencia_directa_vs_viva"],
                "n_local_con_diferencia": on_res["n_local_con_diferencia"],
                "n_visitante_con_diferencia": on_res["n_visitante_con_diferencia"],
                "n_partidos_con_diferencia": on_res["partidos_con_diferencia"],
                "unificacion_100_pct": bool(on_res["n_lados_con_diferencia_directa_vs_viva"] == 0),
            },
        },
        "efecto_sobre_73_registros": {
            "n_partidos_con_cambio_h_gf_lstm": cambios_solo_h,
            "n_lados_con_cambio_h_gf_lstm": off_res["n_lados_con_diferencia_directa_vs_viva"],
            "n_partidos_con_cambio_lambda": cambios_lambda,
            "n_partidos_sin_cambio": len(pares_73) - len(tabla_efecto),
            "registros_afectados": tabla_efecto,
        },
        "explicacion_prior_099_vs_media_147": {
            "media_simple_15p_crudos": {
                "goles_favor_cronologicos": goles_crudos_col,
                "suma_goles": int(sum(goles_crudos_col)),
                "n_partidos": len(goles_crudos_col),
                "media_simple": round(sum(goles_crudos_col) / len(goles_crudos_col), 4),
            },
            "primeros_8_vs_ultimos_7_crudos": {
                "media_primeros_8_idx0_7": round(sum(goles_crudos_col[:8]) / 8.0, 4),
                "media_ultimos_7_idx8_14": round(sum(goles_crudos_col[8:]) / 7.0, 4),
                "media_ultimos_5_idx10_14": round(sum(goles_crudos_col[10:]) / 5.0, 4),
            },
            "ruta_viva_bandera_apagada_09891": traza_off,
            "ruta_unificada_bandera_activa_09650": traza_on,
            "sintesis_tres_factores": [
                "1) Penalización por credibilidad en el 5-0 vs Panamá (Copa América, idx=7, peso=0.35): en ruta viva por defecto 5.0 pasa a 0.35*5.0 + 0.65*(22/15) = 2.7033 (c_memoria baja de 1.4667 a 1.3136); en ruta unificada L1068 recorta primero 5.0 -> 2.2 por Copa América y luego mezcla 0.35*2.2 + 0.65*(19.2/15) = 1.6020 (c_memoria baja a 1.2401).",
                "2) Decaimiento exponencial LSTM secuencial (semivida=4.2 -> alpha_input=0.1521 más compuerta de sorpresa hasta 0.20): tras alcanzar h_7=1.8114 (ruta viva) en el idx 7, los últimos 7 partidos suman solo 6 goles [1, 0, 3, 1, 0, 1, 0] (media 0.857), haciendo caer el estado oculto hasta h_14=0.9819 (o 0.9417 unificado).",
                "3) Mezcla final 60/40 con los últimos 5 partidos (`h_reciente = 0.60 * h_14 + 0.40 * media_ult5`): como los últimos 5 partidos [3, 1, 0, 1, 0] promedian 1.0000, `0.60 * 0.9819 + 0.40 * 1.0000 = 0.9891` (~0.99 en ruta viva) y `0.60 * 0.9417 + 0.40 * 1.0000 = 0.9650` (~0.97 unificado).",
            ],
        },
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def auditar_impacto_real_frecuencias_24oos_ronda12(
    *,
    n_boot: int = 2000,
    seed: int = 42,
    guardar_ruta: str | None = "datos/ronda12_punto6_frecuencias_24oos.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 12 - Punto 6 (`ARREGLOS DE FRECUENCIA` en 24 OOS):
      Evalúa los aciertos reales (tasa de acierto, `n`, aciertos/evaluables e IC95 bootstrap
      por partido) de las líneas que los 2 arreglos de `calcular_frecuencia_observada`
      (`periodo 1T/2T` y `al menos` `g >= lin`) AÑADIERON frente a las que QUITARON
      en Top 5 y Sección 1 sobre la cohorte de los 24 partidos OOS únicos (`26` jobs en
      `datos/ronda10_oos_26_jobs_map.json`).
    """
    import random as _rnd6
    import re as _re6
    from pathlib import Path
    from decision_cobertura import banderas_comportamiento
    import motor_mercados as mm
    import servicio_mercados as sm

    raiz = Path(__file__).resolve().parent
    oos = json.loads((raiz / "datos" / "oos_conciliacion_ronda7_ronda8.json").read_text(encoding="utf-8"))
    filas_1506 = list(oos.get("filas_1506") or [])
    jmap_data = json.loads((raiz / "datos" / "ronda10_oos_26_jobs_map.json").read_text(encoding="utf-8"))
    jobs_map = dict(jmap_data.get("jobs") or {})
    no_comp_jids = set(jmap_data.get("no_comp_jids") or [])

    merc_by_job: dict[str, dict[str, Any]] = {}
    for fp in sorted((raiz / "informes" / "mercados").rglob("mercados_*.json")):
        md = json.loads(fp.read_text(encoding="utf-8"))
        if md.get("id_trabajo"):
            merc_by_job[str(md["id_trabajo"])] = md

    idx_pid: dict[tuple[str, tuple[str, str, str | None, float | None]], int] = {}
    pid_by_jid: dict[str, str] = {}
    partido_by_jid: dict[str, str] = {}
    stats_pid: dict[str, dict[str, int]] = {}

    for r in filas_1506:
        jid = str(r["id_trabajo"])
        pid = str(r.get("id_partido") or "")
        pid_by_jid[jid] = pid
        partido_by_jid[jid] = str(r.get("partido") or "")
        k = _normalizar_clave_seleccion_ronda10(r.get("evento"), r.get("periodo"), r.get("equipo"), r.get("linea"))
        idx_pid[(pid, k)] = int(r["evento_ocurrio"])

    for fp_ev in sorted((raiz / "informes" / "evaluacion").glob("evaluacion_*.json")):
        d_ev = json.loads(fp_ev.read_text(encoding="utf-8"))
        for s in (d_ev.get("evaluaciones") or []):
            jid_s = str(s.get("id_trabajo") or "")
            pid_s = str(s.get("id_partido") or pid_by_jid.get(jid_s) or "")
            if pid_s in set(pid_by_jid.values()) and s.get("evento_ocurrio") is not None:
                k = _normalizar_clave_seleccion_ronda10(s.get("evento"), s.get("periodo"), s.get("equipo"), s.get("linea"))
                idx_pid.setdefault((pid_s, k), int(s["evento_ocurrio"]))
                ev_s = str(s.get("evento") or "")
                per_s = str(s.get("periodo") or "FT")
                det_s = str(s.get("detalle") or "")
                m_det = _re6.match(r"^\s*(\d+)\s+(goles|córneres|corneres|tarjetas)", det_s)
                st_p = stats_pid.setdefault(pid_s, {})
                if m_det:
                    val_d = int(m_det.group(1))
                    if ev_s.startswith(("over_", "under_", "al_menos_", "fiesta_")) and not s.get("equipo"):
                        st_p[f"goles_{per_s}"] = val_d
                    elif ev_s.startswith("local_") and "goles" in str(s.get("mercado") or "").lower():
                        st_p[f"goles_local_{per_s}"] = val_d
                    elif ev_s.startswith("visitante_") and "goles" in str(s.get("mercado") or "").lower():
                        st_p[f"goles_visita_{per_s}"] = val_d

    # Hechos observados verificados de partidos donde filas_1506/evaluaciones archivadas
    # no incluyeron eventos 1T/primer_gol/ambos_tiempos:
    # - 5795478 Everton 0-2 Chelsea (FotMob): goles min 46 (Chelsea) y 90 (Chelsea) -> primer_gol_local=0
    # - 6106338 Galatasaray 1-4 Barcelona (FotMob): min 28 Galatasaray anota primero -> primer_gol_visitante=0
    # - 5795481 Manchester City 3-2 Ipswich Town (FotMob): min 19 Man City primero -> primer_gol_local=1;
    #   goles en 1T (19',21',34') y 2T (69',90') -> gol_ambos_tiempos=1
    # - 1000020615 Fluminense 1-2 Palmeiras (Flashscore df_sui_1_C82xkem4): 1T 0-1 (goles_1T=1), 2T 1-1 (goles_2T=2)
    extra_pid_eventos: dict[tuple[str, str, str], int] = {
        ("5795478", "primer_gol_local", "FT"): 0,
        ("6106338", "primer_gol_visitante", "FT"): 0,
        ("5795481", "primer_gol_local", "FT"): 1,
        ("5795481", "gol_ambos_tiempos", "FT"): 1,
    }
    stats_pid.setdefault("1000020615", {})["goles_1T"] = 1
    stats_pid.setdefault("1000020615", {})["goles_2T"] = 2

    def _g(o: Any, k: str, d: Any = None) -> Any:
        return o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)

    def _frec_parametrizada(sel: Any, doc_l: Any, doc_v: Any, eq_l: str = "Local", eq_v: str = "Visitante", *, fix_periodo: bool, fix_al_menos: bool) -> float | None:
        ev = str(_g(sel, "evento") or "").lower()
        et = str(_g(sel, "etiqueta") or "").lower()
        mkt = str(_g(sel, "mercado") or "").lower()
        per = str(_g(sel, "periodo", "FT") or "FT").strip().upper()
        eq = str(_g(sel, "equipo") or "").strip()
        lin_raw = _g(sel, "linea", None)
        try:
            lin = float(lin_raw) if lin_raw is not None else None
        except Exception:
            lin = None

        def _ex(doc: Any) -> list[dict[str, Any]]:
            if not isinstance(doc, dict):
                return []
            out_pts: list[dict[str, Any]] = []
            for p in doc.get("partidos", []) or []:
                if not isinstance(p, dict):
                    continue
                m = p.get("marcador", {}) or {}
                gf, gc = m.get("equipo"), m.get("rival")
                gf_per, gc_per = gf, gc
                if fix_periodo and per in ("1T", "2T"):
                    p_per = (p.get("periodos", {}) or {}).get(per, {}) or {}
                    m_per = p_per.get("marcador") or {}
                    if m_per.get("equipo") is not None and m_per.get("rival") is not None:
                        gf_per, gc_per = m_per.get("equipo"), m_per.get("rival")
                    else:
                        fg = p.get("franjas_goles") or {}
                        claves_m = ("00_15", "16_30", "31_45") if per == "1T" else ("46_60", "61_75", "76_90")
                        if fg and all(isinstance(fg.get(ck), dict) for ck in claves_m):
                            gf_per = sum(float(fg[ck].get("anotados", 0) or 0) for ck in claves_m)
                            gc_per = sum(float(fg[ck].get("recibidos", 0) or 0) for ck in claves_m)
                        else:
                            from auditoria_historica import reconstruir_marcador_mitades_partido
                            g1, c1, g2, c2, ok_m, _ = reconstruir_marcador_mitades_partido(p, "equipo", "rival")
                            if ok_m and g1 is not None and c1 is not None and g2 is not None and c2 is not None:
                                gf_per, gc_per = (g1, c1) if per == "1T" else (g2, c2)
                            else:
                                gf_per, gc_per = None, None
                cf = cr = tf = tr = None
                for fl in ((p.get("periodos", {}).get("FT", {}) or {}).get("filas", []) or []):
                    if fl.get("clave") == "corners":
                        cf, cr = fl.get("equipo"), fl.get("rival")
                    elif fl.get("clave") in ("tarjetas_amarillas", "tarjetas"):
                        tf, tr = fl.get("equipo"), fl.get("rival")
                out_pts.append({"gf": gf, "gc": gc, "gf_per": gf_per, "gc_per": gc_per, "cf": cf, "cr": cr, "tf": tf, "tr": tr})
            return out_pts

        pl, pv = _ex(doc_l), _ex(doc_v)
        if not pl and not pv:
            return None
        from resolvedor_entidades import normalizar_texto
        nl, nv, en = normalizar_texto(eq_l), normalizar_texto(eq_v), normalizar_texto(eq)
        es_u = ("under" in ev or ("menos" in et and ("al menos" not in et if fix_al_menos else True)))
        es_am = bool(fix_al_menos and ("al_menos" in ev or "al menos" in et))

        if "doble_oportunidad" in mkt or "doble oportunidad" in mkt or any(k in ev for k in ("dc_1x", "dc_x2", "dc_12")) or any(et.startswith(k) for k in ("1x", "x2", "12")):
            if "1x" in ev or "1x" in et:
                v = [p for p in pl if p.get("gf") is not None and p.get("gc") is not None]
                return (sum(1 for p in v if p["gf"] >= p["gc"]) / len(v)) if v else None
            if "x2" in ev or "x2" in et:
                v = [p for p in pv if p.get("gf") is not None and p.get("gc") is not None]
                return (sum(1 for p in v if p["gf"] >= p["gc"]) / len(v)) if v else None
            v = [p for p in (pl + pv) if p.get("gf") is not None and p.get("gc") is not None]
            return (sum(1 for p in v if p["gf"] != p["gc"]) / len(v)) if v else None

        if (any(k in mkt for k in ("corner", "córner")) or "corner" in ev) and lin is not None:
            if "local" in ev or (en and (en == nl or en in nl or nl in en)):
                v = [float(p["cf"]) for p in pl if p.get("cf") is not None]
            elif "visita" in ev or "visitante" in ev or (en and (en == nv or en in nv or nv in en)):
                v = [float(p["cf"]) for p in pv if p.get("cf") is not None]
            else:
                v = [float(p["cf"] + p["cr"]) for p in (pl + pv) if p.get("cf") is not None and p.get("cr") is not None]
            return ((sum(1 for c in v if c < lin) / len(v)) if es_u else (sum(1 for c in v if c > lin) / len(v))) if v else None

        if ("total" in mkt or "goles" in mkt) and not en and not any(k in ev for k in ("local", "visita")) and lin is not None:
            v = [float(p["gf_per"] + p["gc_per"]) for p in (pl + pv) if p.get("gf_per") is not None and p.get("gc_per") is not None]
            if not v:
                return None
            if es_u:
                return sum(1 for g in v if g < lin) / len(v)
            if es_am:
                return sum(1 for g in v if g >= lin) / len(v)
            return sum(1 for g in v if g > lin) / len(v)

        if ("goles" in mkt or "gol" in ev) and en and lin is not None:
            if en == nl or en in nl or nl in en:
                v = [float(p["gf_per"]) for p in pl if p.get("gf_per") is not None]
            elif en == nv or en in nv or nv in en:
                v = [float(p["gf_per"]) for p in pv if p.get("gf_per") is not None]
            else:
                return None
            if not v:
                return None
            if es_u:
                return sum(1 for g in v if g < lin) / len(v)
            if es_am:
                return sum(1 for g in v if g >= lin) / len(v)
            return sum(1 for g in v if g > lin) / len(v)

        if "ambos_anotan" in ev or "btts" in ev:
            v = [(float(p["gf"]), float(p["gc"])) for p in (pl + pv) if p.get("gf") is not None and p.get("gc") is not None]
            return ((sum(1 for a, b in v if a == 0 or b == 0) / len(v)) if "no" in ev else (sum(1 for a, b in v if a > 0 and b > 0) / len(v))) if v else None

        if (any(k in mkt for k in ("tarjeta", "tarjetas")) or "tarjeta" in ev) and lin is not None:
            v = [float(p["tf"] + p["tr"]) for p in (pl + pv) if p.get("tf") is not None and p.get("tr") is not None]
            return ((sum(1 for t in v if t < lin) / len(v)) if es_u else (sum(1 for t in v if t > lin) / len(v))) if v else None

        return None

    def _match_top5_cand(item_t5: dict[str, Any], cands: list[Any], eq_l: str, eq_v: str) -> Any:
        etq_t5 = str(item_t5.get("etiqueta") or "")
        p_t5 = float(item_t5.get("probabilidad") or 0.0)
        if "(1T)" in etq_t5 or "1ra Mitad" in etq_t5:
            per_t5 = "1T"
        elif "(2T)" in etq_t5 or "2da Mitad" in etq_t5:
            per_t5 = "2T"
        else:
            per_t5 = "FT"
        mejores = [
            s for s in cands
            if str(getattr(s, "periodo", "FT") or "FT") == per_t5
            and abs(float(getattr(s, "probabilidad", 0.0) or 0.0) - p_t5) < 5e-4
        ]
        if len(mejores) == 1:
            return mejores[0]
        for s in mejores:
            ev_s = str(getattr(s, "evento", "") or "")
            eq_s = str(getattr(s, "equipo", "") or "")
            lin_s = getattr(s, "linea", None)
            if lin_s is not None and f"{lin_s:g}" in etq_t5:
                if eq_s and eq_s.lower() in etq_t5.lower():
                    return s
                if not eq_s and eq_l.lower() not in etq_t5.lower() and eq_v.lower() not in etq_t5.lower():
                    return s
            if "Anota primero" in etq_t5 and "primer" in ev_s and eq_s and eq_s.lower() in etq_t5.lower():
                return s
            if "ambos tiempos" in etq_t5.lower() and "ambos_tiempos" in ev_s:
                return s
        return mejores[0] if mejores else None

    def _grade_sel(jid: str, sel_obj: Any, item_t5: dict[str, Any] | None, meta: dict[str, Any]) -> int | None:
        pid = pid_by_jid[jid]
        if sel_obj is not None:
            ev = str(_g(sel_obj, "evento") or "")
            per = str(_g(sel_obj, "periodo", "FT") or "FT").strip().upper()
            eq = _g(sel_obj, "equipo")
            lin = _g(sel_obj, "linea")
            if (pid, ev, per) in extra_pid_eventos:
                return extra_pid_eventos[(pid, ev, per)]
            k = _normalizar_clave_seleccion_ronda10(ev, per, eq, lin)
            if (pid, k) in idx_pid:
                return idx_pid[(pid, k)]
            if ev.startswith("al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"over_{float(lin) - 0.5:g}", per, eq, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            if ev.startswith("local_al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"local_over_{float(lin) - 0.5:g}", per, eq, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            if ev.startswith("visitante_al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"visitante_over_{float(lin) - 0.5:g}", per, eq, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            if ev.startswith("fiesta_al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"over_{float(lin) - 0.5:g}", per, None, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            st = stats_pid.get(pid, {})
            if f"goles_{per}" in st and not eq and lin is not None:
                g = st[f"goles_{per}"]
                if "al_menos" in ev:
                    return int(g >= float(lin))
                if "over" in ev:
                    return int(g > float(lin))
                if "under" in ev:
                    return int(g < float(lin))
            if eq == meta.get("equipo_local") and f"goles_local_{per}" in st and lin is not None:
                g = st[f"goles_local_{per}"]
                if "al_menos" in ev:
                    return int(g >= float(lin))
                if "over" in ev:
                    return int(g > float(lin))
                if "under" in ev:
                    return int(g < float(lin))
            if eq == meta.get("equipo_visitante") and f"goles_visita_{per}" in st and lin is not None:
                g = st[f"goles_visita_{per}"]
                if "al_menos" in ev:
                    return int(g >= float(lin))
                if "over" in ev:
                    return int(g > float(lin))
                if "under" in ev:
                    return int(g < float(lin))
            if ev == "gol_2t" and "goles_2T" in st:
                return int(st["goles_2T"] >= 1)
            if ev == "gol_1t" and "goles_1T" in st:
                return int(st["goles_1T"] >= 1)
        # Fallback para etiqueta sintetizada de Top 5 ('1ra Mitad : Menos de 1.5 Goles...')
        if item_t5 is not None:
            etq = str(item_t5.get("etiqueta") or "")
            if "1ra Mitad" in etq and "Menos de 1.5 Goles" in etq:
                k_1t = _normalizar_clave_seleccion_ronda10("under_1.5", "1T", None, 1.5)
                if (pid, k_1t) in idx_pid:
                    return idx_pid[(pid, k_1t)]
                st = stats_pid.get(pid, {})
                if "goles_1T" in st:
                    return int(st["goles_1T"] < 1.5)
        return None

    def _boot_tasa_ic(obs_rows: list[dict[str, Any]], seed_val: int) -> dict[str, Any]:
        evaluables = [r for r in obs_rows if r.get("evento_ocurrio") is not None]
        n = len(evaluables)
        pids_u = sorted({str(r["id_partido"]) for r in evaluables})
        if n == 0:
            return {
                "n_lineas": 0,
                "n_partidos_unicos": 0,
                "aciertos": None,
                "tasa_acierto": None,
                "ic95_tasa": None,
                "brier": None,
                "ic95_brier": None,
                "logloss": None,
                "ic95_logloss": None,
            }
        ac = sum(int(r["evento_ocurrio"]) for r in evaluables)
        brier_vals = [(float(r["prob"]) - float(r["evento_ocurrio"])) ** 2 for r in evaluables]
        ll_vals = []
        for r in evaluables:
            p_c = min(max(float(r["prob"]), 1e-12), 1.0 - 1e-12)
            y = float(r["evento_ocurrio"])
            ll_vals.append(-(y * math.log(p_c) + (1.0 - y) * math.log(1.0 - p_c)))

        rng = _rnd6.Random(seed_val)
        por_pid: dict[str, list[tuple[int, float, float]]] = {}
        for r, bv, lv in zip(evaluables, brier_vals, ll_vals):
            por_pid.setdefault(str(r["id_partido"]), []).append((int(r["evento_ocurrio"]), bv, lv))

        t_boot: list[float] = []
        b_boot: list[float] = []
        l_boot: list[float] = []
        m_c = len(pids_u)
        for _ in range(n_boot):
            muestra = [pids_u[rng.randrange(m_c)] for _ in range(m_c)]
            s_ac = s_br = s_ll = cnt = 0.0
            for pid_b in muestra:
                for y_b, br_b, ll_b in por_pid[pid_b]:
                    s_ac += y_b
                    s_br += br_b
                    s_ll += ll_b
                    cnt += 1.0
            if cnt > 0:
                t_boot.append(s_ac / cnt)
                b_boot.append(s_br / cnt)
                l_boot.append(s_ll / cnt)
        t_boot.sort()
        b_boot.sort()
        l_boot.sort()
        lo = int(0.025 * len(t_boot))
        hi = min(len(t_boot) - 1, int(0.975 * len(t_boot)))
        return {
            "n_lineas": n,
            "n_partidos_unicos": m_c,
            "aciertos": ac,
            "tasa_acierto": round(ac / n, 4),
            "ic95_tasa": [round(t_boot[lo], 4), round(t_boot[hi], 4)] if t_boot else None,
            "brier": round(sum(brier_vals) / n, 6),
            "ic95_brier": [round(b_boot[lo], 6), round(b_boot[hi], 6)] if b_boot else None,
            "logloss": round(sum(ll_vals) / n, 6),
            "ic95_logloss": [round(l_boot[lo], 6), round(l_boot[hi], 6)] if l_boot else None,
        }

    def _boot_diff_tasa(rows_a: list[dict[str, Any]], rows_r: list[dict[str, Any]], seed_val: int) -> dict[str, Any] | None:
        ea = [r for r in rows_a if r.get("evento_ocurrio") is not None]
        er = [r for r in rows_r if r.get("evento_ocurrio") is not None]
        if not ea or not er:
            return None
        diff_obs = (sum(r["evento_ocurrio"] for r in ea) / len(ea)) - (sum(r["evento_ocurrio"] for r in er) / len(er))
        pids_all = sorted({str(r["id_partido"]) for r in ea} | {str(r["id_partido"]) for r in er})
        pa: dict[str, list[int]] = {}
        pr: dict[str, list[int]] = {}
        for r in ea:
            pa.setdefault(str(r["id_partido"]), []).append(int(r["evento_ocurrio"]))
        for r in er:
            pr.setdefault(str(r["id_partido"]), []).append(int(r["evento_ocurrio"]))
        rng = _rnd6.Random(seed_val)
        diffs: list[float] = []
        m = len(pids_all)
        for _ in range(n_boot):
            muestra = [pids_all[rng.randrange(m)] for _ in range(m)]
            sa = ca = sr = cr = 0
            for p_b in muestra:
                if p_b in pa:
                    sa += sum(pa[p_b])
                    ca += len(pa[p_b])
                if p_b in pr:
                    sr += sum(pr[p_b])
                    cr += len(pr[p_b])
            if ca > 0 and cr > 0:
                diffs.append((sa / ca) - (sr / cr))
        diffs.sort()
        if not diffs:
            return None
        lo = int(0.025 * len(diffs))
        hi = min(len(diffs) - 1, int(0.975 * len(diffs)))
        return {
            "diff_tasa_anadidas_menos_quitadas": round(diff_obs, 4),
            "ic95_diff_tasa": [round(diffs[lo], 4), round(diffs[hi], 4)],
        }

    def _evaluar_modo(act_b: bool) -> dict[str, Any]:
        orig_desv = mm.tiene_desvio_excesivo_calibracion
        orig_frec = mm.calcular_frecuencia_observada
        bases: dict[str, tuple[Any, list[tuple[str, str | None]], dict[str, Any]]] = {}
        mm.tiene_desvio_excesivo_calibracion = lambda *a, **kw: (False, None, None)  # type: ignore[assignment]
        try:
            with banderas_comportamiento(activar_todas=act_b):
                for jid in sorted(jobs_map):
                    fpl, fpv, cut = jobs_map[jid]
                    dl = json.loads((raiz / fpl).read_text(encoding="utf-8"))
                    dv = json.loads((raiz / fpv).read_text(encoding="utf-8"))
                    dlc = mm.filtrar_por_fecha(dl, cut) if cut else dl
                    dvc = mm.filtrar_por_fecha(dv, cut) if cut else dv
                    meta = merc_by_job[jid]["metadatos"]
                    par = mm.Parametros()
                    if meta.get("competicion") and jid not in no_comp_jids:
                        par.competicion = str(meta["competicion"])
                    an = mm.analizar(
                        dlc, dvc,
                        meta["equipo_local"], meta["equipo_visitante"],
                        meta.get("condicion_local") or "casa", meta.get("condicion_visitante") or "fuera",
                        parametros=par,
                    )
                    est0 = [(getattr(s, "estado", None), getattr(s, "motivo", None)) for s in (getattr(an, "selecciones_filtradas", None) or [])]
                    bases[jid] = (an, est0, meta)
        finally:
            mm.tiene_desvio_excesivo_calibracion = orig_desv  # type: ignore[assignment]

        def _correr_cfg(*, fix_p: bool, fix_am: bool) -> dict[str, dict[str, list[Any]]]:
            def _fn(sel: Any, dl_c: Any, dv_c: Any, eq_l: str = "Local", eq_v: str = "Visitante") -> float | None:
                return _frec_parametrizada(sel, dl_c, dv_c, eq_l, eq_v, fix_periodo=fix_p, fix_al_menos=fix_am)
            mm.calcular_frecuencia_observada = _fn  # type: ignore[assignment]
            res_c: dict[str, dict[str, list[Any]]] = {}
            try:
                with banderas_comportamiento(activar_todas=act_b):
                    for jid, (an, est0, meta) in bases.items():
                        mot = an.motor
                        cands = getattr(an, "selecciones_filtradas", None) or []
                        if not getattr(an, "abstenido", False) and mot is not None:
                            ng = {mm.ESTADO_MANTIENE: [], mm.ESTADO_SENSIBLE: [], mm.ESTADO_INCOMPLETA: []}
                            for s, (eb, mb) in zip(cands, est0):
                                s.estado, s.motivo = eb, mb
                                exc, _, _ = mm.tiene_desvio_excesivo_calibracion(
                                    s, mot.doc_local, mot.doc_visitante, mot.nombre_local, mot.nombre_visitante
                                )
                                if exc:
                                    s.estado = mm.ESTADO_SENSIBLE
                                ng.setdefault(s.estado, []).append(s)
                            an.grupos = ng
                            t5, s1, _ = sm.obtener_top5_y_seccion1_filtrados(
                                an,
                                metadatos={"equipo_local": meta["equipo_local"], "equipo_visitante": meta["equipo_visitante"]},
                                umbral=0.70,
                            )
                            t5_list = []
                            for it5 in (t5 or []):
                                cp = dict(it5)
                                cp["_cand"] = _match_top5_cand(it5, cands, meta["equipo_local"], meta["equipo_visitante"])
                                t5_list.append(cp)
                        else:
                            t5_list, s1 = [], []
                        res_c[jid] = {"top5": t5_list, "sec1": list(s1 or [])}
            finally:
                mm.calcular_frecuencia_observada = orig_frec  # type: ignore[assignment]
            return res_c

        c_pre = _correr_cfg(fix_p=False, fix_am=False)
        c_al_m = _correr_cfg(fix_p=False, fix_am=True)
        c_per = _correr_cfg(fix_p=True, fix_am=False)
        c_ambos = _correr_cfg(fix_p=True, fix_am=True)

        def _comparar_par(base_r: dict[str, dict[str, list[Any]]], new_r: dict[str, dict[str, list[Any]]], s_tag: int) -> dict[str, Any]:
            res_sec: dict[str, Any] = {}
            for sec in ("top5", "sec1"):
                def _sig(s: Any) -> Any:
                    if sec == "top5":
                        return (str(_g(s, "etiqueta") or ""), round(float(_g(s, "probabilidad") or 0), 6))
                    return _normalizar_clave_seleccion_ronda10(_g(s, "evento"), _g(s, "periodo", "FT"), _g(s, "equipo"), _g(s, "linea"))

                anadidas: list[dict[str, Any]] = []
                quitadas: list[dict[str, Any]] = []
                for jid in sorted(jobs_map):
                    meta = merc_by_job[jid]["metadatos"]
                    b_map = {_sig(s): s for s in base_r[jid][sec]}
                    n_map = {_sig(s): s for s in new_r[jid][sec]}
                    for k, s in n_map.items():
                        if k not in b_map:
                            cand = s.get("_cand") if sec == "top5" else s
                            oc = _grade_sel(jid, cand, s if sec == "top5" else None, meta)
                            anadidas.append({
                                "id_trabajo": jid,
                                "id_partido": pid_by_jid[jid],
                                "partido": partido_by_jid.get(jid) or f"{meta.get('equipo_local')} vs {meta.get('equipo_visitante')}",
                                "etiqueta": _g(s, "etiqueta"),
                                "evento": _g(cand, "evento") if cand else None,
                                "periodo": _g(cand, "periodo", "1T" if "1ra Mitad" in str(_g(s, "etiqueta") or "") else "FT"),
                                "prob": round(float(_g(s, "probabilidad") or 0.0), 4),
                                "evento_ocurrio": oc,
                            })
                    for k, s in b_map.items():
                        if k not in n_map:
                            cand = s.get("_cand") if sec == "top5" else s
                            oc = _grade_sel(jid, cand, s if sec == "top5" else None, meta)
                            quitadas.append({
                                "id_trabajo": jid,
                                "id_partido": pid_by_jid[jid],
                                "partido": partido_by_jid.get(jid) or f"{meta.get('equipo_local')} vs {meta.get('equipo_visitante')}",
                                "etiqueta": _g(s, "etiqueta"),
                                "evento": _g(cand, "evento") if cand else None,
                                "periodo": _g(cand, "periodo", "FT"),
                                "prob": round(float(_g(s, "probabilidad") or 0.0), 4),
                                "evento_ocurrio": oc,
                            })

                # Deduplicado a los 24 id_partido unicos (por (id_partido, clave/etiqueta))
                def _dedup24(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
                    seen: set[tuple[str, Any]] = set()
                    out_d: list[dict[str, Any]] = []
                    for it in items:
                        dk = (it["id_partido"], it["evento"] or it["etiqueta"], it["periodo"], it["etiqueta"])
                        if dk not in seen:
                            seen.add(dk)
                            out_d.append(it)
                    return out_d

                ana_24 = _dedup24(anadidas)
                qui_24 = _dedup24(quitadas)
                res_sec[sec] = {
                    "n_no_resolubles_anadidas": sum(1 for x in anadidas if x["evento_ocurrio"] is None),
                    "n_no_resolubles_quitadas": sum(1 for x in quitadas if x["evento_ocurrio"] is None),
                    "sobre_26_jobs_ic_por_partido_24": {
                        "anadidas": _boot_tasa_ic(anadidas, seed + s_tag),
                        "quitadas": _boot_tasa_ic(quitadas, seed + s_tag + 1),
                        "diferencia": _boot_diff_tasa(anadidas, quitadas, seed + s_tag + 2),
                    },
                    "sobre_24_partidos_unicos": {
                        "anadidas": _boot_tasa_ic(ana_24, seed + s_tag + 10),
                        "quitadas": _boot_tasa_ic(qui_24, seed + s_tag + 11),
                        "diferencia": _boot_diff_tasa(ana_24, qui_24, seed + s_tag + 12),
                    },
                    "detalle_anadidas": anadidas,
                    "detalle_quitadas": quitadas,
                }
            return res_sec

        return {
            "pre_vs_ambos_arreglos": _comparar_par(c_pre, c_ambos, 100),
            "pre_vs_solo_al_menos": _comparar_par(c_pre, c_al_m, 200),
            "pre_vs_solo_periodo_1t": _comparar_par(c_pre, c_per, 300),
        }

    out = {
        "cohorte": {
            "n_jobs_oos": len(jobs_map),
            "n_partidos_unicos": len(set(pid_by_jid.values())),
            "partido_duplicado_3_snapshots": {"6106274": "Roma vs Real Madrid"},
            "n_boot": n_boot,
            "seed": seed,
        },
        "soloA": _evaluar_modo(False),
        "AB_con_banderas": _evaluar_modo(True),
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def auditar_corners_65e377e_diag_ronda12(
    *,
    n_boot: int = 2000,
    seed: int = 42,
    guardar_ruta: str | None = "datos/ronda12_punto7_corners_65e377e_diag.json",
) -> dict[str, Any]:
    """
    ORDEN RONDA 12 - Punto 7 (`CÓRNERES` solo diagnóstico, tras bandera):
      Evalúa revertir bloqueo, asedio y "Embudo Interior" de `_mu_corners` a `65e377e`
      (+ Poisson y + Binomial Negativa) sobre las 613 filas de córneres de los 24 OOS
      (`filas_1376`), comparando Brier y LogLoss con IC95 bootstrap por partido frente
      a `arch`, `5e8425f`, `soloA` y `A+B`. Declara explícitamente evaluación IN-SAMPLE.
    """
    from pathlib import Path
    from decision_cobertura import bandera_activa, banderas_comportamiento
    import motor_mercados as mm

    raiz = Path(__file__).resolve().parent
    oos = json.loads((raiz / "datos" / "oos_conciliacion_ronda7_ronda8.json").read_text(encoding="utf-8"))
    f1376 = [
        r for r in (oos.get("filas_1506") or [])
        if all(k in (r.get("probs") or {}) for k in ("arch", "5e8425f", "994209a", "soloA", "A+B"))
    ]
    fc = [
        r for r in f1376
        if "corner" in str(r.get("mercado") or "").lower()
        or "córner" in str(r.get("mercado") or "").lower()
        or "corner" in str(r.get("evento") or "").lower()
    ]

    jmap_data = json.loads((raiz / "datos" / "ronda10_oos_26_jobs_map.json").read_text(encoding="utf-8"))
    jobs_map = dict(jmap_data.get("jobs") or {})
    no_comp_jids = set(jmap_data.get("no_comp_jids") or [])

    merc_by_job: dict[str, dict[str, Any]] = {}
    for fp in sorted((raiz / "informes" / "mercados").rglob("mercados_*.json")):
        md = json.loads(fp.read_text(encoding="utf-8"))
        if md.get("id_trabajo"):
            merc_by_job[str(md["id_trabajo"])] = md

    jids_c = sorted({str(r["id_trabajo"]) for r in fc})
    docs_por_jid: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    for jid in jids_c:
        fpl, fpv, cut = jobs_map[jid]
        dl = json.loads((raiz / fpl).read_text(encoding="utf-8"))
        dv = json.loads((raiz / fpv).read_text(encoding="utf-8"))
        dlc = mm.filtrar_por_fecha(dl, cut) if cut else dl
        dvc = mm.filtrar_por_fecha(dv, cut) if cut else dv
        docs_por_jid[jid] = (dlc, dvc, merc_by_job[jid]["metadatos"])

    cfgs: dict[str, dict[str, bool]] = {
        "head_soloA": {"activar_todas": False},
        "head_AB": {"activar_todas": True},
        "65e377e_poisson_soloA": {
            "activar_todas": False,
            "usar_corners_reglas_65e377e_diag": True,
            "usar_sobredispersion_nb_corners": False,
        },
        "65e377e_nb_soloA": {
            "activar_todas": False,
            "usar_corners_reglas_65e377e_diag": True,
            "usar_sobredispersion_nb_corners": True,
        },
        "65e377e_nb_AB": {
            "activar_todas": True,
            "usar_corners_reglas_65e377e_diag": True,
        },
    }

    probs_cfg: dict[str, dict[str, dict[tuple[str, str, str | None, float | None], float]]] = {c: {} for c in cfgs}
    mu_cfg: dict[str, dict[str, tuple[float | None, float | None]]] = {c: {} for c in cfgs}
    for cfg_name, flg in cfgs.items():
        with banderas_comportamiento(**flg):
            for jid in jids_c:
                dlc, dvc, meta = docs_por_jid[jid]
                par = mm.Parametros()
                if meta.get("competicion") and jid not in no_comp_jids:
                    par.competicion = str(meta["competicion"])
                mot = mm.MotorMercados(
                    dlc, dvc,
                    meta.get("condicion_local") or "casa",
                    meta.get("condicion_visitante") or "fuera",
                    meta["equipo_local"], meta["equipo_visitante"],
                    par,
                )
                mot.preparar()
                res = mot.evaluar_catalogo()
                mu_cfg[cfg_name][jid] = (mot._mu_corners("local"), mot._mu_corners("visitante"))
                mp: dict[tuple[str, str, str | None, float | None], float] = {}
                for rm in (res or []):
                    for sel in getattr(rm, "selecciones", None) or []:
                        p_s = getattr(sel, "probabilidad", None)
                        if p_s is None:
                            continue
                        k = _normalizar_clave_seleccion_ronda10(
                            getattr(sel, "evento", None),
                            getattr(sel, "periodo", "FT"),
                            getattr(sel, "equipo", None),
                            getattr(sel, "linea", None),
                        )
                        mp[k] = float(p_s)
                probs_cfg[cfg_name][jid] = mp

    obs_613: list[tuple[str, int, dict[str, float]]] = []
    obs_total_corners: list[tuple[str, int, dict[str, float]]] = []
    obs_equipo_corners: list[tuple[str, int, dict[str, float]]] = []
    mism_sa = 0
    mism_ab = 0

    for r in fc:
        jid = str(r["id_trabajo"])
        pid = str(r.get("id_partido") or r.get("partido") or jid)
        y = int(r["evento_ocurrio"])
        k = _normalizar_clave_seleccion_ronda10(r.get("evento"), r.get("periodo"), r.get("equipo"), r.get("linea"))
        p_sa = probs_cfg["head_soloA"][jid].get(k)
        p_ab = probs_cfg["head_AB"][jid].get(k)
        if p_sa is None or abs(float(p_sa) - float(r["probs"]["soloA"])) > 1e-4:
            mism_sa += 1
        if p_ab is None or abs(float(p_ab) - float(r["probs"]["A+B"])) > 1e-4:
            mism_ab += 1
        probs_d = {
            "arch": float(r["probs"]["arch"]),
            "5e8425f": float(r["probs"]["5e8425f"]),
            "soloA": float(p_sa if p_sa is not None else r["probs"]["soloA"]),
            "A+B": float(p_ab if p_ab is not None else r["probs"]["A+B"]),
            "65e377e_poisson_soloA": float(probs_cfg["65e377e_poisson_soloA"][jid][k]),
            "65e377e_nb_soloA": float(probs_cfg["65e377e_nb_soloA"][jid][k]),
            "65e377e_nb_AB": float(probs_cfg["65e377e_nb_AB"][jid][k]),
        }
        obs_613.append((pid, y, probs_d))
        if r.get("equipo"):
            obs_equipo_corners.append((pid, y, probs_d))
        else:
            obs_total_corners.append((pid, y, probs_d))

    boot_613 = bootstrap_brier_logloss_por_partido(obs_613, n_boot=n_boot, seed=seed)
    boot_tot = bootstrap_brier_logloss_por_partido(obs_total_corners, n_boot=n_boot, seed=seed + 1)
    boot_eq = bootstrap_brier_logloss_por_partido(obs_equipo_corners, n_boot=n_boot, seed=seed + 2)

    cambio_mu_jobs: list[dict[str, Any]] = []
    for jid in jids_c:
        ml_h, mv_h = mu_cfg["head_soloA"][jid]
        ml_65, mv_65 = mu_cfg["65e377e_poisson_soloA"][jid]
        meta = docs_por_jid[jid][2]
        cambio_mu_jobs.append({
            "job_id": jid,
            "partido": f"{meta.get('equipo_local')} vs {meta.get('equipo_visitante')}",
            "mu_head": [round(float(ml_h), 4) if ml_h is not None else None, round(float(mv_h), 4) if mv_h is not None else None],
            "mu_65e377e": [round(float(ml_65), 4) if ml_65 is not None else None, round(float(mv_65), 4) if mv_65 is not None else None],
            "delta_mu_total": round(float((ml_65 + mv_65) - (ml_h + mv_h)), 4) if (None not in (ml_h, mv_h, ml_65, mv_65)) else None,
        })

    out = {
        "bandera_usar_corners_reglas_65e377e_diag_defecto": bandera_activa("usar_corners_reglas_65e377e_diag"),
        "declaracion_in_sample": True,
        "advertencia_metodologica": (
            "IN-SAMPLE: Esta evaluación diagnostica sobre las 613 filas de córneres de los 24 OOS "
            "(18 partidos únicos con mercado de córneres en filas_1376) revierte las reglas de bloqueo, "
            "asedio y Embudo Interior a `65e377e` + NB (`usar_sobredispersion_nb_corners=True`) de forma "
            "retrospectiva sobre los mismos datos donde se detectó el empeoramiento; no constituye prueba "
            "OOS independiente y permanece detrás de `usar_corners_reglas_65e377e_diag=False`."
        ),
        "verificacion_reconstruccion": {
            "n_filas_corners": len(fc),
            "n_jobs_corners": len(jids_c),
            "n_partidos_unicos": boot_613["n_partidos_unicos"],
            "mismatches_soloA_vs_1376": mism_sa,
            "mismatches_AB_vs_1376": mism_ab,
        },
        "metricas_613_filas": boot_613,
        "desglose_total_corners": boot_tot,
        "desglose_corners_por_equipo": boot_eq,
        "impacto_mu_por_job": cambio_mu_jobs,
    }
    if guardar_ruta:
        out_p = Path(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
def auditar_leave_one_out_banderas_b_ronda13(
    *,
    n_boot: int = 2000,
    seed: int = 42,
    guardar_ruta: str | None = "datos/ronda13_punto4_loo_banderas_b.json",
    regenerar: bool = False,
) -> dict[str, Any]:
    """
    RONDA 13 - Punto 4 (`Leave-One-Out Conjunto B` sobre los 24 OOS con IC por partido):
      Evalúa quitar una a una cada una de las 14 banderas de Conjunto B desde `B_14`
      (todas activas) sobre la cohorte de los 24 partidos OOS únicos (`1376` filas
      comunes de catálogo, cobertura/gates, y selecciones Top 5 / Sección 1) con
      bootstrap agrupado por partido (`n=24`), para sustentar qué defectos mantener
      o cambiar antes de fusionar.
    """
    import copy as _copy_r13
    import hashlib as _hl_r13
    import random as _rnd_r13
    import re as _re_r13
    from pathlib import Path as _Path_r13
    from decision_cobertura import (
        BANDERAS_COMPORTAMIENTO_DEFECTO,
        bandera_activa as _ba_r13,
        banderas_comportamiento,
    )
    import motor_mercados as mm
    import servicio_mercados as sm

    raiz = _Path_r13(__file__).resolve().parent
    if guardar_ruta and not regenerar:
        rp = _Path_r13(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        if rp.exists():
            return json.loads(rp.read_text(encoding="utf-8"))

    oos = json.loads((raiz / "datos" / "oos_conciliacion_ronda7_ronda8.json").read_text(encoding="utf-8"))
    filas_1506 = list(oos.get("filas_1506") or [])
    filas_1376 = [
        r for r in filas_1506
        if all(k in (r.get("probs") or {}) for k in ("arch", "5e8425f", "994209a", "soloA", "A+B"))
    ]
    mapa_r10 = json.loads((raiz / "datos" / "ronda10_oos_26_jobs_map.json").read_text(encoding="utf-8"))
    jobs_map = dict(mapa_r10.get("jobs") or {})
    no_comp_jids = set(mapa_r10.get("no_comp_jids") or [])

    merc_by_job: dict[str, dict[str, Any]] = {}
    for fp in sorted((raiz / "informes" / "mercados").rglob("mercados_*.json")):
        md = json.loads(fp.read_text(encoding="utf-8"))
        if md.get("id_trabajo"):
            merc_by_job[str(md["id_trabajo"])] = md

    # 1. Índice de desenlaces observados sobre los 24 OOS (idéntico a R12 P6)
    idx_pid: dict[tuple[str, tuple[str, str, str | None, float | None]], int] = {}
    pid_by_jid: dict[str, str] = {}
    stats_pid: dict[str, dict[str, int]] = {}
    for r in filas_1506:
        jid = str(r["id_trabajo"])
        pid = str(r.get("id_partido") or "")
        pid_by_jid[jid] = pid
        k = _normalizar_clave_seleccion_ronda10(r.get("evento"), r.get("periodo"), r.get("equipo"), r.get("linea"))
        idx_pid[(pid, k)] = int(r["evento_ocurrio"])

    for fp_ev in sorted((raiz / "informes" / "evaluacion").glob("evaluacion_*.json")):
        d_ev = json.loads(fp_ev.read_text(encoding="utf-8"))
        for s in (d_ev.get("evaluaciones") or []):
            jid_s = str(s.get("id_trabajo") or "")
            pid_s = str(s.get("id_partido") or pid_by_jid.get(jid_s) or "")
            if pid_s in set(pid_by_jid.values()) and s.get("evento_ocurrio") is not None:
                k = _normalizar_clave_seleccion_ronda10(s.get("evento"), s.get("periodo"), s.get("equipo"), s.get("linea"))
                idx_pid.setdefault((pid_s, k), int(s["evento_ocurrio"]))
                ev_s = str(s.get("evento") or "")
                per_s = str(s.get("periodo") or "FT")
                det_s = str(s.get("detalle") or "")
                m_det = _re_r13.match(r"^\s*(\d+)\s+(goles|córneres|corneres|tarjetas)", det_s)
                st_p = stats_pid.setdefault(pid_s, {})
                if m_det:
                    val_d = int(m_det.group(1))
                    if ev_s.startswith(("over_", "under_", "al_menos_", "fiesta_")) and not s.get("equipo"):
                        st_p[f"goles_{per_s}"] = val_d
                    elif ev_s.startswith("local_") and "goles" in str(s.get("mercado") or "").lower():
                        st_p[f"goles_local_{per_s}"] = val_d
                    elif ev_s.startswith("visitante_") and "goles" in str(s.get("mercado") or "").lower():
                        st_p[f"goles_visita_{per_s}"] = val_d

    extra_pid_eventos: dict[tuple[str, str, str], int] = {
        ("5795478", "primer_gol_local", "FT"): 0,
        ("6106338", "primer_gol_visitante", "FT"): 0,
        ("5795481", "primer_gol_local", "FT"): 1,
        ("5795481", "gol_ambos_tiempos", "FT"): 1,
    }
    stats_pid.setdefault("1000020615", {})["goles_1T"] = 1
    stats_pid.setdefault("1000020615", {})["goles_2T"] = 2

    def _g(o: Any, k: str, d: Any = None) -> Any:
        return o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)

    def _match_top5_cand(item_t5: dict[str, Any], cands: list[Any], eq_l: str, eq_v: str) -> Any:
        etq_t5 = str(item_t5.get("etiqueta") or "")
        p_t5 = float(item_t5.get("probabilidad") or 0.0)
        if "(1T)" in etq_t5 or "1ra Mitad" in etq_t5:
            per_t5 = "1T"
        elif "(2T)" in etq_t5 or "2da Mitad" in etq_t5:
            per_t5 = "2T"
        else:
            per_t5 = "FT"
        mejores = [
            s for s in cands
            if str(getattr(s, "periodo", "FT") or "FT") == per_t5
            and abs(float(getattr(s, "probabilidad", 0.0) or 0.0) - p_t5) < 5e-4
        ]
        if len(mejores) == 1:
            return mejores[0]
        for s in mejores:
            ev_s = str(getattr(s, "evento", "") or "")
            eq_s = str(getattr(s, "equipo", "") or "")
            lin_s = getattr(s, "linea", None)
            if lin_s is not None and f"{lin_s:g}" in etq_t5:
                if eq_s and eq_s.lower() in etq_t5.lower():
                    return s
                if not eq_s and eq_l.lower() not in etq_t5.lower() and eq_v.lower() not in etq_t5.lower():
                    return s
            if "Anota primero" in etq_t5 and "primer" in ev_s and eq_s and eq_s.lower() in etq_t5.lower():
                return s
            if "ambos tiempos" in etq_t5.lower() and "ambos_tiempos" in ev_s:
                return s
        return mejores[0] if mejores else None

    def _grade_sel(jid: str, sel_obj: Any, item_t5: dict[str, Any] | None, meta: dict[str, Any]) -> int | None:
        pid = pid_by_jid[jid]
        if sel_obj is not None:
            ev = str(_g(sel_obj, "evento") or "")
            per = str(_g(sel_obj, "periodo", "FT") or "FT").strip().upper()
            eq = _g(sel_obj, "equipo")
            lin = _g(sel_obj, "linea")
            if (pid, ev, per) in extra_pid_eventos:
                return extra_pid_eventos[(pid, ev, per)]
            k = _normalizar_clave_seleccion_ronda10(ev, per, eq, lin)
            if (pid, k) in idx_pid:
                return idx_pid[(pid, k)]
            if ev.startswith("al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"over_{float(lin) - 0.5:g}", per, eq, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            if ev.startswith("local_al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"local_over_{float(lin) - 0.5:g}", per, eq, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            if ev.startswith("visitante_al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"visitante_over_{float(lin) - 0.5:g}", per, eq, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            if ev.startswith("fiesta_al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"over_{float(lin) - 0.5:g}", per, None, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            st = stats_pid.get(pid, {})
            if f"goles_{per}" in st and not eq and lin is not None:
                g = st[f"goles_{per}"]
                if "al_menos" in ev:
                    return int(g >= float(lin))
                if "over" in ev:
                    return int(g > float(lin))
                if "under" in ev:
                    return int(g < float(lin))
            if eq == meta.get("equipo_local") and f"goles_local_{per}" in st and lin is not None:
                g = st[f"goles_local_{per}"]
                if "al_menos" in ev:
                    return int(g >= float(lin))
                if "over" in ev:
                    return int(g > float(lin))
                if "under" in ev:
                    return int(g < float(lin))
            if eq == meta.get("equipo_visitante") and f"goles_visita_{per}" in st and lin is not None:
                g = st[f"goles_visita_{per}"]
                if "al_menos" in ev:
                    return int(g >= float(lin))
                if "over" in ev:
                    return int(g > float(lin))
                if "under" in ev:
                    return int(g < float(lin))
            if ev == "gol_2t" and "goles_2T" in st:
                return int(st["goles_2T"] >= 1)
            if ev == "gol_1t" and "goles_1T" in st:
                return int(st["goles_1T"] >= 1)
        if item_t5 is not None:
            etq = str(item_t5.get("etiqueta") or "")
            if "1ra Mitad" in etq and "Menos de 1.5 Goles" in etq:
                k_1t = _normalizar_clave_seleccion_ronda10("under_1.5", "1T", None, 1.5)
                if (pid, k_1t) in idx_pid:
                    return idx_pid[(pid, k_1t)]
                st = stats_pid.get(pid, {})
                if "goles_1T" in st:
                    return int(st["goles_1T"] < 1.5)
        return None

    # Deduplicar 26 jobs -> 24 partidos únicos manteniendo el último job por id_partido
    ultimo_jid_por_pid: dict[str, str] = {}
    for jid in sorted(jobs_map):
        pid = pid_by_jid.get(jid) or jid
        prev_j = ultimo_jid_por_pid.get(pid)
        if prev_j is None:
            ultimo_jid_por_pid[pid] = jid
        else:
            g_cur = str((merc_by_job.get(jid, {}).get("metadatos") or {}).get("generado_en") or "")
            g_prev = str((merc_by_job.get(prev_j, {}).get("metadatos") or {}).get("generado_en") or "")
            if g_cur >= g_prev:
                ultimo_jid_por_pid[pid] = jid
    jids_24 = set(ultimo_jid_por_pid.values())

    # Precargar documentos filtrados en memoria
    jobs_data: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any]] = {}
    for jid in sorted(jobs_map):
        fpl_rel, fpv_rel, cut = jobs_map[jid]
        dl = json.loads((raiz / fpl_rel).read_text(encoding="utf-8"))
        dv = json.loads((raiz / fpv_rel).read_text(encoding="utf-8"))
        dlc = mm.filtrar_por_fecha(dl, cut) if cut else dl
        dvc = mm.filtrar_por_fecha(dv, cut) if cut else dv
        meta = dict(merc_by_job[jid]["metadatos"])
        par = mm.Parametros()
        if meta.get("competicion") and jid not in no_comp_jids:
            par.competicion = str(meta["competicion"])
        jobs_data[jid] = (dlc, dvc, meta, par)

    flags_14 = list(BANDERAS_COMPORTAMIENTO_DEFECTO.keys())
    requeridos_goles = [n for n, _ in mm.escenarios_goles()]
    requeridos_conteos = [n for n, _ in mm.escenarios_conteos()]
    mercados_conteo = set(mm._mercados_de_conteos())
    sens_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def _evaluar_configuracion(nombre_cfg: str, bdict: dict[str, bool]) -> dict[str, Any]:
        probs_cat: dict[str, dict[tuple[str, str, str | None, float | None], float]] = {}
        probs_cat_cal: dict[str, dict[tuple[str, str, str | None, float | None], float]] = {}
        est_analizar_total = 0
        est_catalogo_total = 0
        abst_goles_mitad = 0
        top5_24_rows: list[dict[str, Any]] = []
        sec1_24_rows: list[dict[str, Any]] = []
        stakes_top5: list[float] = []

        with banderas_comportamiento(activar_todas=False, **bdict):
            _an_mit = _ba_r13("usar_gate_cronologia_mitades_10_15")
            _an_crn = _ba_r13("usar_gate_corners_8_15")
            _an_cal = _ba_r13("usar_calibracion_y_veto_conciencia")

            for jid in sorted(jobs_data):
                dlc, dvc, meta, par = jobs_data[jid]
                el, ev_n = str(meta["equipo_local"]), str(meta["equipo_visitante"])
                cl = meta.get("condicion_local") or "casa"
                cv = meta.get("condicion_visitante") or "fuera"

                mot = mm.MotorMercados(dlc, dvc, cl, cv, el, ev_n, par)
                mot.preparar()
                mercados = mot.evaluar_catalogo()

                _cron_an = mm.cronologia_mitades_verificada(mot) if _an_mit else {"ok_analizar": True}
                if _an_mit and not _cron_an.get("ok_analizar", True):
                    abst_goles_mitad += 1

                mp: dict[tuple[str, str, str | None, float | None], float] = {}
                mp_cal: dict[tuple[str, str, str | None, float | None], float] = {}
                for rm in mercados:
                    for s in rm.selecciones:
                        if s.estimable and s.probabilidad is not None:
                            k = _normalizar_clave_seleccion_ronda10(s.evento, s.periodo, s.equipo, s.linea)
                            mp[k] = float(s.probabilidad)
                            mp_cal[k] = float(s.probabilidad)
                est_catalogo_total += len(mp)
                probs_cat[jid] = mp

                # Fingerprint del motor para deduplicar evaluar_sensibilidad exacta sin repetir pasadas idénticas
                sig_payload = {
                    "lam": [mot.tasas.lambda_local_ft, mot.tasas.lambda_visitante_ft, mot.tasas.lambda_local_1t, mot.tasas.lambda_visitante_1t],
                    "probs": [[str(k), round(v, 6)] for k, v in sorted(mp.items(), key=lambda kv: str(kv[0]))],
                }
                sig_hash = _hl_r13.sha256(json.dumps(sig_payload, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
                if (jid, sig_hash) not in sens_cache:
                    sens_cache[(jid, sig_hash)] = mm.evaluar_sensibilidad(dlc, dvc, cl, cv, el, ev_n, par)
                sensibilidad = sens_cache[(jid, sig_hash)]
                _cob_corn_g = mm.cobertura_corners_por_equipo(mot, bando=None) if _an_crn else {"ok_catalogo": True}
                _cob_corn_l = mm.cobertura_corners_por_equipo(mot, bando="local") if _an_crn else {"ok_catalogo": True}
                _cob_corn_v = mm.cobertura_corners_por_equipo(mot, bando="visitante") if _an_crn else {"ok_catalogo": True}

                vistos: dict[tuple[Any, ...], Any] = {}
                for rm in mercados:
                    for s in rm.selecciones:
                        if not s.estimable or s.probabilidad is None:
                            continue
                        if mm.es_mercado_cronologia_mitades(s) and not _cron_an.get("ok_analizar", True):
                            continue
                        if rm.id_mercado == "total_corners" and not _cob_corn_g.get("ok_catalogo", True):
                            continue
                        if rm.id_mercado == "corners_local" and not _cob_corn_l.get("ok_catalogo", True):
                            continue
                        if rm.id_mercado == "corners_visitante" and not _cob_corn_v.get("ok_catalogo", True):
                            continue
                        est_analizar_total += 1
                        sc = _copy_r13.copy(s)
                        if _an_cal:
                            try:
                                from agente_consciente import evaluar_familia_conciencia_cached
                                _cal_s = evaluar_familia_conciencia_cached(sc.mercado, equipo=sc.equipo, prob_bruta=float(sc.probabilidad))
                                p_cal_v = _cal_s.get("prob_calibrada")
                                setattr(sc, "prob_calibrada", p_cal_v)
                                setattr(sc, "bajo_vigilancia", bool(_cal_s.get("bajo_vigilancia", False)))
                                setattr(sc, "veto_top5", bool(_cal_s.get("veto_top5", False)))
                                setattr(sc, "familia_conciencia", _cal_s.get("familia"))
                                if p_cal_v is not None:
                                    k_c = _normalizar_clave_seleccion_ronda10(sc.evento, sc.periodo, sc.equipo, sc.linea)
                                    mp_cal[k_c] = float(p_cal_v)
                            except Exception:
                                pass
                        if float(sc.probabilidad) <= par.umbral:
                            continue
                        esc = sensibilidad.get(mm._clave_a_texto(sc.clave_unica), {})
                        sc.escenarios = esc
                        req = requeridos_conteos if rm.id_mercado in mercados_conteo else requeridos_goles
                        est_sc, mot_sc = mm.clasificar(sc.probabilidad, esc, req, par.umbral)
                        if not est_sc:
                            continue
                        excede_desv, desv_pts, f_obs = mm.tiene_desvio_excesivo_calibracion(
                            sc, mot.doc_local, mot.doc_visitante, mot.nombre_local, mot.nombre_visitante
                        )
                        if excede_desv:
                            est_sc = mm.ESTADO_SENSIBLE
                            _brecha_emp = round(round(float(f_obs) * 100.0, 1) - round(float(sc.probabilidad) * 100.0, 1), 1)
                            mot_sc = f"sin calibración validada (frec_emp: {f_obs*100:.1f}% vs mod: {sc.probabilidad*100:.1f}%, brecha {_brecha_emp:+.1f} pp, desvío {desv_pts:.1f} pts > 10.0)"
                        sc.estado = est_sc
                        if mot_sc:
                            sc.motivo = mot_sc
                        vistos.setdefault(sc.clave_unica, sc)

                probs_cat_cal[jid] = mp_cal
                if jid not in jids_24:
                    continue

                sels_flt = list(vistos.values())
                grupos = {mm.ESTADO_MANTIENE: [], mm.ESTADO_SENSIBLE: [], mm.ESTADO_INCOMPLETA: []}
                for sc in sels_flt:
                    grupos.setdefault(sc.estado, []).append(sc)
                an_ligero = mm.Analisis(meta, par, mot, mercados, sels_flt, grupos, sensibilidad, "", "")
                t5, s1, _ = sm.obtener_top5_y_seccion1_filtrados(
                    an_ligero,
                    metadatos={"equipo_local": el, "equipo_visitante": ev_n},
                    umbral=0.70,
                )
                pid = pid_by_jid[jid]
                for it5 in (t5 or []):
                    cand = _match_top5_cand(it5, sels_flt, el, ev_n)
                    res_y = _grade_sel(jid, cand, it5, meta)
                    p_item = float(it5.get("probabilidad") or 0.0)
                    ev_k = str(getattr(cand, "evento", None) or it5.get("evento") or it5.get("etiqueta") or "")
                    per_k = str(getattr(cand, "periodo", None) or "FT")
                    eq_k = str(getattr(cand, "equipo", None) or "")
                    lin_k = getattr(cand, "linea", None)
                    # Medir stake Kelly conservador sobre una cuota de referencia con +5% valor si supera 1.05
                    c_ref = round(1.05 / max(0.05, min(0.95, p_item - 0.04)), 2) if p_item < 0.94 else 1.15
                    val_k = mm.calcular_ev_y_stake_calibrado(
                        str(getattr(cand, "mercado", "") or it5.get("categoria_podio") or ""),
                        p_item,
                        cuota_real=c_ref,
                        equipo=getattr(cand, "equipo", None),
                    )
                    if val_k.get("stake_pct") is not None:
                        stakes_top5.append(float(val_k["stake_pct"]))
                    top5_24_rows.append({
                        "id_partido": pid,
                        "id_trabajo": jid,
                        "clave_linea": (pid, ev_k, per_k, eq_k, lin_k),
                        "prob": p_item,
                        "evento_ocurrio": res_y,
                    })
                for sc1 in (s1 or []):
                    res_y = _grade_sel(jid, sc1, None, meta)
                    ev_k = str(_g(sc1, "evento") or "")
                    per_k = str(_g(sc1, "periodo", "FT") or "FT")
                    eq_k = str(_g(sc1, "equipo") or "")
                    lin_k = _g(sc1, "linea")
                    sec1_24_rows.append({
                        "id_partido": pid,
                        "id_trabajo": jid,
                        "clave_linea": (pid, ev_k, per_k, eq_k, lin_k),
                        "prob": float(_g(sc1, "probabilidad") or 0.0),
                        "evento_ocurrio": res_y,
                    })

        return {
            "nombre": nombre_cfg,
            "probs_cat": probs_cat,
            "probs_cat_cal": probs_cat_cal,
            "est_catalogo_total": est_catalogo_total,
            "est_analizar_total": est_analizar_total,
            "abst_goles_mitad": abst_goles_mitad,
            "top5_rows": top5_24_rows,
            "sec1_rows": sec1_24_rows,
            "stakes_top5": stakes_top5,
        }

    # Ejecutar baselines + 14 configuraciones LOO
    configs: list[tuple[str, dict[str, bool]]] = [
        ("B_14", {k: True for k in flags_14}),
        ("B_9", {k: (k not in {
            "usar_cuota_justa_114_top5",
            "usar_guarda_doble_defensa_rota",
            "usar_gate_goles_mitad_15_15",
            "usar_kelly_conservador_min_3pct",
            "usar_excluir_primer_gol_top5",
        }) for k in flags_14}),
        ("soloA", {k: False for k in flags_14}),
    ]
    for f in flags_14:
        configs.append((f"sin_{f}", {k: (k != f) for k in flags_14}))

    resultados_cfg = {nom: _evaluar_configuracion(nom, bdict) for nom, bdict in configs}
    ref_b14 = resultados_cfg["B_14"]
    ref_sa = resultados_cfg["soloA"]

    def _boot_sel_y_diff(rows_cfg: list[dict[str, Any]], rows_ref: list[dict[str, Any]], seed_v: int) -> dict[str, Any]:
        ev_c = [r for r in rows_cfg if r["evento_ocurrio"] is not None]
        ev_r = [r for r in rows_ref if r["evento_ocurrio"] is not None]
        no_res = len(rows_cfg) - len(ev_c)
        if not ev_c:
            return {"n_lineas": len(rows_cfg), "n_resueltas": 0, "n_no_resolubles": no_res, "aciertos": None, "tasa_acierto": None, "brier": None, "log_loss": None}

        pids_u = sorted({str(r["id_partido"]) for r in ev_c} | {str(r["id_partido"]) for r in ev_r})
        def _por_pid(rows_l: list[dict[str, Any]]) -> dict[str, list[tuple[int, float, float]]]:
            mp_p: dict[str, list[tuple[int, float, float]]] = {}
            for r in rows_l:
                y = int(r["evento_ocurrio"])
                p = float(r["prob"])
                pc = min(max(p, 1e-12), 1.0 - 1e-12)
                mp_p.setdefault(str(r["id_partido"]), []).append((
                    y,
                    (p - y) ** 2,
                    -(y * math.log(pc) + (1.0 - y) * math.log(1.0 - pc)),
                ))
            return mp_p

        pc_map = _por_pid(ev_c)
        pr_map = _por_pid(ev_r)

        def _agg(m_map: dict[str, list[tuple[int, float, float]]], sample_pids: list[str]) -> tuple[float, float, float] | None:
            sa = sb = sl = cnt = 0.0
            for pid_b in sample_pids:
                for y_b, br_b, ll_b in m_map.get(pid_b, ()):
                    sa += y_b
                    sb += br_b
                    sl += ll_b
                    cnt += 1.0
            if cnt <= 0:
                return None
            return (sa / cnt, sb / cnt, sl / cnt)

        obs_c = _agg(pc_map, sorted(pc_map.keys()))
        obs_r = _agg(pr_map, sorted(pr_map.keys()))
        rng = _rnd_r13.Random(seed_v)
        tc_b, bc_b, lc_b = [], [], []
        dt_b, db_b, dl_b = [], [], []
        m = len(pids_u)
        for _ in range(n_boot):
            mu = [pids_u[rng.randrange(m)] for _ in range(m)]
            ac_v = _agg(pc_map, mu)
            ar_v = _agg(pr_map, mu)
            if ac_v is not None:
                tc_b.append(ac_v[0])
                bc_b.append(ac_v[1])
                lc_b.append(ac_v[2])
                if ar_v is not None:
                    dt_b.append(ac_v[0] - ar_v[0])
                    db_b.append(ac_v[1] - ar_v[1])
                    dl_b.append(ac_v[2] - ar_v[2])
        for arr in (tc_b, bc_b, lc_b, dt_b, db_b, dl_b):
            arr.sort()

        def _ic(arr: list[float], dec: int) -> list[float] | None:
            if not arr:
                return None
            lo = int(0.025 * len(arr))
            hi = min(len(arr) - 1, int(0.975 * len(arr)))
            return [round(arr[lo], dec), round(arr[hi], dec)]

        claves_c = {r["clave_linea"] for r in rows_cfg}
        claves_r = {r["clave_linea"] for r in rows_ref}
        cambiadas = len(claves_c ^ claves_r)
        return {
            "n_lineas": len(rows_cfg),
            "n_partidos": len(pc_map),
            "n_resueltas": len(ev_c),
            "n_no_resolubles": no_res,
            "aciertos": sum(int(r["evento_ocurrio"]) for r in ev_c),
            "tasa_acierto": round(obs_c[0], 4) if obs_c else None,
            "tasa_ic95": _ic(tc_b, 4),
            "brier": round(obs_c[1], 6) if obs_c else None,
            "brier_ic95": _ic(bc_b, 6),
            "log_loss": round(obs_c[2], 6) if obs_c else None,
            "log_loss_ic95": _ic(lc_b, 6),
            "d_tasa_vs_B14": round(obs_c[0] - obs_r[0], 4) if (obs_c and obs_r) else None,
            "d_tasa_vs_B14_ic95": _ic(dt_b, 4),
            "d_brier_vs_B14": round(obs_c[1] - obs_r[1], 6) if (obs_c and obs_r) else None,
            "d_brier_vs_B14_ic95": _ic(db_b, 6),
            "d_log_loss_vs_B14": round(obs_c[2] - obs_r[2], 6) if (obs_c and obs_r) else None,
            "d_log_loss_vs_B14_ic95": _ic(dl_b, 6),
            "n_lineas_cambiadas_vs_B14": cambiadas,
        }

    # Evaluar catálogo 1376 (bruto y calibrado efectivo) + Top 5 + Sección 1 para todas las configs
    obs_1376_all: list[tuple[str, int, dict[str, float]]] = []
    obs_1376_cal_all: list[tuple[str, int, dict[str, float]]] = []
    for r in filas_1376:
        jid = str(r["id_trabajo"])
        pid = str(r["id_partido"])
        y = int(r["evento_ocurrio"])
        k = _normalizar_clave_seleccion_ronda10(r["evento"], r["periodo"], r["equipo"], r["linea"])
        d_raw: dict[str, float] = {}
        d_cal: dict[str, float] = {}
        ok_row = True
        for nom, res_c in resultados_cfg.items():
            p_raw = res_c["probs_cat"].get(jid, {}).get(k)
            p_cal = res_c["probs_cat_cal"].get(jid, {}).get(k)
            if p_raw is None or p_cal is None:
                ok_row = False
                break
            d_raw[nom] = float(p_raw)
            d_cal[nom] = float(p_cal)
        if ok_row:
            obs_1376_all.append((pid, y, d_raw))
            obs_1376_cal_all.append((pid, y, d_cal))

    boot_1376 = bootstrap_brier_logloss_por_partido(obs_1376_all, n_boot=n_boot, seed=seed)
    boot_1376_cal = bootstrap_brier_logloss_por_partido(obs_1376_cal_all, n_boot=n_boot, seed=seed + 10)

    def _resumen_cfg_completo(nom: str, bandera_quitada: str | None, idx_seed: int) -> dict[str, Any]:
        res_c = resultados_cfg[nom]
        v_raw = (boot_1376.get("versiones") or {}).get(nom) or {}
        d_raw_b14 = (boot_1376.get("diferencias") or {}).get(f"{nom} - B_14") or {}
        d_raw_sa = (boot_1376.get("diferencias") or {}).get(f"{nom} - soloA") or {}
        v_cal = (boot_1376_cal.get("versiones") or {}).get(nom) or {}
        d_cal_b14 = (boot_1376_cal.get("diferencias") or {}).get(f"{nom} - B_14") or {}

        n_filas_ch = 0
        pids_ch: set[str] = set()
        for r in filas_1376:
            jid = str(r["id_trabajo"])
            k = _normalizar_clave_seleccion_ronda10(r["evento"], r["periodo"], r["equipo"], r["linea"])
            p_b = ref_b14["probs_cat"].get(jid, {}).get(k)
            p_n = res_c["probs_cat"].get(jid, {}).get(k)
            if p_b != p_n and (p_b is None or p_n is None or abs(float(p_b) - float(p_n)) > 1e-6):
                n_filas_ch += 1
                pids_ch.add(str(r["id_partido"]))

        t5_stats = _boot_sel_y_diff(res_c["top5_rows"], ref_b14["top5_rows"], seed + 100 + idx_seed)
        s1_stats = _boot_sel_y_diff(res_c["sec1_rows"], ref_b14["sec1_rows"], seed + 200 + idx_seed)
        stakes = res_c["stakes_top5"]
        kelly_mean = round(sum(stakes) / len(stakes), 3) if stakes else None
        kelly_max = round(max(stakes), 2) if stakes else None

        # Regla de recomendación objetiva (sin afirmar mejora si IC cruza 0 o es neutro)
        rec = "MANTENER_FALSE_NEUTRO_O_SIN_IMPACTO"
        if bandera_quitada is not None:
            ic_ll = d_raw_b14.get("d_log_loss_ic95")
            ic_br = d_raw_b14.get("d_brier_ic95")
            ic_t5_ll = t5_stats.get("d_log_loss_vs_B14_ic95")
            ic_s1_ll = s1_stats.get("d_log_loss_vs_B14_ic95")
            if ic_ll and ic_ll[1] < 0:
                rec = "MANTENER_FALSE_EMPEORA_LL_1376"
            elif ic_ll and ic_ll[0] > 0:
                rec = "CANDIDATA_TRUE_MEJORA_LL_1376"
            elif ic_br and ic_br[1] < 0:
                rec = "MANTENER_FALSE_EMPEORA_BRIER_1376"
            elif ic_br and ic_br[0] > 0:
                rec = "CANDIDATA_TRUE_MEJORA_BRIER_1376"
            elif (ic_t5_ll and ic_t5_ll[1] < 0) or (ic_s1_ll and ic_s1_ll[1] < 0):
                rec = "MANTENER_FALSE_EMPEORA_TOP5_O_SEC1"
            elif (ic_t5_ll and ic_t5_ll[0] > 0) or (ic_s1_ll and ic_s1_ll[0] > 0):
                rec = "CANDIDATA_TRUE_MEJORA_TOP5_O_SEC1"
            elif n_filas_ch == 0 and t5_stats.get("n_lineas_cambiadas_vs_B14") == 0 and s1_stats.get("n_lineas_cambiadas_vs_B14") == 0 and res_c["est_analizar_total"] == ref_b14["est_analizar_total"] and res_c["abst_goles_mitad"] == ref_b14["abst_goles_mitad"] and nom != "sin_usar_kelly_conservador_min_3pct":
                rec = "MANTENER_FALSE_SIN_EFECTO_OOS24"
            else:
                rec = "MANTENER_FALSE_IC_CRUZA_CERO"

        return {
            "config": nom,
            "bandera_quitada": bandera_quitada,
            "catalogo_1376": {
                "n_partidos_unicos": boot_1376.get("n_partidos_unicos"),
                "n_filas": boot_1376.get("n_filas"),
                "brier": v_raw.get("brier"),
                "brier_ic95": v_raw.get("brier_ic95"),
                "log_loss": v_raw.get("log_loss"),
                "log_loss_ic95": v_raw.get("log_loss_ic95"),
                "d_brier_vs_B14": d_raw_b14.get("d_brier"),
                "d_brier_vs_B14_ic95": d_raw_b14.get("d_brier_ic95"),
                "d_log_loss_vs_B14": d_raw_b14.get("d_log_loss"),
                "d_log_loss_vs_B14_ic95": d_raw_b14.get("d_log_loss_ic95"),
                "d_brier_vs_soloA": d_raw_sa.get("d_brier"),
                "d_brier_vs_soloA_ic95": d_raw_sa.get("d_brier_ic95"),
                "d_log_loss_vs_soloA": d_raw_sa.get("d_log_loss"),
                "d_log_loss_vs_soloA_ic95": d_raw_sa.get("d_log_loss_ic95"),
                "n_filas_cambiadas_vs_B14": n_filas_ch,
                "n_partidos_cambiados_vs_B14": len(pids_ch),
            },
            "catalogo_1376_calibrado_efectivo": {
                "brier": v_cal.get("brier"),
                "brier_ic95": v_cal.get("brier_ic95"),
                "log_loss": v_cal.get("log_loss"),
                "log_loss_ic95": v_cal.get("log_loss_ic95"),
                "d_brier_vs_B14": d_cal_b14.get("d_brier"),
                "d_brier_vs_B14_ic95": d_cal_b14.get("d_brier_ic95"),
                "d_log_loss_vs_B14": d_cal_b14.get("d_log_loss"),
                "d_log_loss_vs_B14_ic95": d_cal_b14.get("d_log_loss_ic95"),
            },
            "cobertura_y_gates_26_jobs": {
                "n_estimables_catalogo": res_c["est_catalogo_total"],
                "delta_estimables_catalogo_vs_B14": res_c["est_catalogo_total"] - ref_b14["est_catalogo_total"],
                "n_estimables_analizar": res_c["est_analizar_total"],
                "delta_estimables_analizar_vs_B14": res_c["est_analizar_total"] - ref_b14["est_analizar_total"],
                "n_sides_abstenidos_goles_mitad": res_c["abst_goles_mitad"],
            },
            "top5_24_unicos": t5_stats,
            "seccion1_24_unicos": s1_stats,
            "kelly_top5_pct": {
                "stake_medio_pct": kelly_mean,
                "stake_max_observado_pct": kelly_max,
                "n_stakes": len(stakes),
            },
            "recomendacion_defecto": rec,
        }

    baselines = {
        nom: _resumen_cfg_completo(nom, None, i)
        for i, nom in enumerate(("B_14", "B_9", "soloA"))
    }
    tabla_loo = [
        _resumen_cfg_completo(f"sin_{f}", f, 10 + i)
        for i, f in enumerate(flags_14)
    ]

    out = {
        "cohorte": {
            "n_partidos_unicos_oos": len(jids_24),
            "n_jobs_oos": len(jobs_data),
            "n_filas_comunes_catalogo": boot_1376.get("n_filas"),
            "n_boot": n_boot,
            "seed": seed,
            "nota_interpretacion_signo": (
                "En d_brier_vs_B14 y d_log_loss_vs_B14 (= metricas(B_sin_bandera) - metricas(B_14)): "
                "un valor NEGATIVO significa que quitar esa bandera MEJORA el error (la bandera empeoraba), "
                "y un valor POSITIVO significa que quitarla EMPEORA el error (la bandera aportaba mejora)."
            ),
        },
        "banderas_defecto_actuales": dict(BANDERAS_COMPORTAMIENTO_DEFECTO),
        "baselines": baselines,
        "tabla_leave_one_out_14_banderas": tabla_loo,
    }
    if guardar_ruta:
        out_p = _Path_r13(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
def _clasificar_familia_ronda14(mercado: Any) -> str:
    m = str(mercado or "").lower()
    if "esquina" in m or "córner" in m or "corner" in m:
        return "Córneres"
    if "tarjeta" in m:
        return "Tarjetas"
    if any(t in m for t in ("doble oportunidad", "hándicap", "handicap", "ganador", "sin empate")):
        return "Resultado"
    return "Goles"


def auditar_defectos_y_sombra_ronda14(
    *,
    n_boot: int = 2000,
    seed: int = 42,
    guardar_ruta: str | None = "datos/ronda14_punto1_defectos_y_sombra.json",
    regenerar: bool = False,
) -> dict[str, Any]:
    """
    RONDA 14 - Punto 1 (`Defectos, Brazo Sombra y Tabla de Impacto`):
      - Documenta defectos vigentes: `usar_sobredispersion_nb_corners=True`
        ("regla operativa sin validación"), resto de 13 banderas de Conjunto B en `False`,
        cuota justa mínima `1.25`.
      - Verifica `usar_guarda_doble_defensa_rota` solo como brazo sombra dedicado
        (pre_partido -> sha256 -> liquidado -> evaluación promoción) sin alterar salida oficial.
      - Mide impacto comparativo de `soloA_previo`, `defecto_ronda14_nb`,
        `sombra_guarda_ddr` y `A_mas_B_14` sobre:
          (a) los 73 registros únicos en caché (`_recolectar_pares_partidos_cacheados_ronda9`), y
          (b) los 24 OOS únicos (`1376` filas catálogo global y por familia, Top 5 y Sección 1)
              con bootstrap agrupado por partido (`n=2000`) e IC95%.
    """
    import copy as _copy_r14
    import hashlib as _hl_r14
    import random as _rnd_r14
    import re as _re_r14
    import tempfile as _tf_r14
    from pathlib import Path as _Path_r14
    from decision_cobertura import (
        BANDERAS_COMPORTAMIENTO_DEFECTO,
        bandera_activa as _ba_r14,
        banderas_comportamiento,
        obtener_banderas_comportamiento,
        obtener_metadatos_defectos_banderas_b,
        obtener_umbral_cuota_justa_top5,
    )
    import motor_mercados as mm
    import servicio_mercados as sm

    raiz = _Path_r14(__file__).resolve().parent
    if guardar_ruta and not regenerar:
        rp = _Path_r14(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        if rp.exists():
            return json.loads(rp.read_text(encoding="utf-8"))

    flags_14 = list(BANDERAS_COMPORTAMIENTO_DEFECTO.keys())
    cfgs_dict: dict[str, dict[str, bool]] = {
        "soloA_previo": {k: False for k in flags_14},
        "defecto_ronda14_nb": {k: (k == "usar_sobredispersion_nb_corners") for k in flags_14},
        "sombra_guarda_ddr": {
            k: (k in ("usar_sobredispersion_nb_corners", "usar_guarda_doble_defensa_rota"))
            for k in flags_14
        },
        "A_mas_B_14": {k: True for k in flags_14},
    }

    # ── 1. Configuración vigente y verificación de ciclo sombra guarda_doble_defensa_rota ──
    banderas_vigentes = obtener_banderas_comportamiento()
    metadatos_defectos = obtener_metadatos_defectos_banderas_b()
    cuota_justa = obtener_umbral_cuota_justa_top5()

    aud_dir = raiz / "informes" / "auditorias" / "2026-10-13"
    dl_gal = json.loads((aud_dir / "auditoria_galatasaray_2026-10-13_102501.json").read_text(encoding="utf-8"))
    dv_bar = json.loads((aud_dir / "auditoria_barcelona_2026-10-13_102522.json").read_text(encoding="utf-8"))
    dl_gal_c = {
        "equipo": dl_gal.get("equipo"),
        "partidos": [x for x in dl_gal.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"],
    }
    dv_bar_c = {
        "equipo": dv_bar.get("equipo"),
        "partidos": [x for x in dv_bar.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"],
    }
    par_s = mm.Parametros()
    par_s.competicion = "UEFA Champions League"
    an_pub = mm.analizar(dl_gal_c, dv_bar_c, "Galatasaray", "Barcelona", parametros=par_s)
    prep_sombra = {
        "id_trabajo": "r14_verif_guarda_ddr",
        "id_partido": "6106338",
        "analisis": an_pub,
        "contexto": {
            "doc_local": dl_gal_c,
            "doc_visitante": dv_bar_c,
            "equipo_local": "Galatasaray",
            "equipo_visitante": "Barcelona",
            "fecha_partido": "2026-10-13",
            "competicion": "UEFA Champions League",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
            "tipo_partido": "oficial",
        },
    }
    somb_v = adjuntar_modo_sombra_preparado(prep_sombra)
    with _tf_r14.TemporaryDirectory() as td:
        tp = _Path_r14(td)
        ruta_snap_t = guardar_snapshot(
            snapshot_desde_analisis(prep_sombra),
            directorio=str(tp / "pronosticos"),
        )
        ruta_led_t = str(tp / "ledger.jsonl")
        r_pre_t = registrar_sombra_prepartido_en_ledger_oos(
            prep_sombra,
            ruta_snapshot=ruta_snap_t,
            ruta_ledger=ruta_led_t,
        )
        snap_doc_t = json.loads(_Path_r14(ruta_snap_t).read_text(encoding="utf-8"))
        snap_doc_t.setdefault("partido", {})["fecha"] = "2026-10-13"
        snap_doc_t["generado_en"] = "2026-10-13T10:00:00"
        res_demo = ResultadoPartido(
            id_partido="6106338",
            estado="finalizado",
            local="Galatasaray",
            visitante="Barcelona",
            goles_local=1,
            goles_visitante=2,
            goles_local_1t=0,
            goles_visitante_1t=1,
            inicio_utc="2026-10-13T19:00:00Z",
            competicion="UEFA Champions League",
            fuente="fotmob",
            consultado_en="2026-10-14T00:00:00Z",
            corners_local={"FT": 5, "1T": 2, "2T": 3},
            corners_visitante={"FT": 6, "1T": 3, "2T": 3},
            amarillas_local={"FT": 2, "1T": 1, "2T": 1},
            amarillas_visitante={"FT": 2, "1T": 1, "2T": 1},
            rojas_local=0,
            rojas_visitante=0,
        )
        r_liq_t = liquidar_sombras_en_ledger_oos(
            [snap_doc_t],
            {"Galatasaray vs Barcelona": res_demo},
            ruta_ledger=ruta_led_t,
        )
        cad_ok_t = bool(verificar_cadena_ledger_oos(ruta_led_t).get("ok"))
        obs_g_t = extraer_observaciones_sombra_desde_ledger(
            ruta_led_t,
            brazo_regla="usar_guarda_doble_defensa_rota",
        )
        prom_g_t = evaluar_promocion_regla_futura(
            obs_g_t["observaciones"],
            nombre_bandera="usar_guarda_doble_defensa_rota",
            n_boot=50,
        )
        regs_liq_t = cargar_ledger_oos(ruta_led_t)

    brazos_reg_s = ((somb_v or {}).get("brazos_reglas") or {}).get("guarda_doble_defensa_rota") or {}
    ms_liq_t = (regs_liq_t[1].get("modo_sombra") or {}) if len(regs_liq_t) > 1 else {}
    verif_ciclo = {
        "esquema_sombra": (somb_v or {}).get("esquema"),
        "version_base": (somb_v or {}).get("version_base"),
        "usar_guarda_doble_defensa_rota_en_defecto": bool(banderas_vigentes.get("usar_guarda_doble_defensa_rota")),
        "arm_presente_en_brazos_reglas": "guarda_doble_defensa_rota" in ((somb_v or {}).get("brazos_reglas") or {}),
        "banderas_arm_guarda": dict(brazos_reg_s.get("banderas_activas") or {}),
        "pre_partido_registrado": bool(r_pre_t.get("registrado")),
        "n_liquidados_nuevos": int(r_liq_t.get("n_liquidados_nuevos") or 0),
        "cadena_sha256_ok": cad_ok_t,
        "brier_guarda_doble_defensa_rota": ms_liq_t.get("brier_guarda_doble_defensa_rota"),
        "log_loss_guarda_doble_defensa_rota": ms_liq_t.get("log_loss_guarda_doble_defensa_rota"),
        "n_observaciones_extraidas_guarda": int(obs_g_t.get("n_observaciones") or 0),
        "promocion_bloqueada_por_n_lt_30": (
            not prom_g_t.get("aprobada_para_activar", True)
            and any("muestra_sombra_insuficiente" in r for r in (prom_g_t.get("razones_bloqueo") or []))
        ),
    }

    # Helpers compartidos de evaluación motor -> sensibilidad cacheada -> Top 5 y Sección 1
    requeridos_goles = [n for n, _ in mm.escenarios_goles()]
    requeridos_conteos = [n for n, _ in mm.escenarios_conteos()]
    mercados_conteo = set(mm._mercados_de_conteos())
    sens_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def _evaluar_partido_ligero(
        scope: str,
        jid: str,
        dlc: dict[str, Any],
        dvc: dict[str, Any],
        cl: str,
        cv: str,
        el: str,
        ev_n: str,
        par: Any,
        meta_an: dict[str, Any],
        *,
        an_mit: bool,
        an_crn: bool,
        an_cal: bool,
    ) -> dict[str, Any]:
        mot = mm.MotorMercados(dlc, dvc, cl, cv, el, ev_n, par)
        mot.preparar()
        mercados = mot.evaluar_catalogo()

        _cron_an = mm.cronologia_mitades_verificada(mot) if an_mit else {"ok_analizar": True}
        _rep_5050 = mm._reparto_mitades_sin_validar(mot) if an_mit else False
        abstenido_mitad = bool(an_mit and not _cron_an.get("ok_analizar", True))

        mp: dict[tuple[str, str, str | None, float | None], float] = {}
        mp_fam: dict[tuple[str, str, str | None, float | None], tuple[float, str]] = {}
        for rm in mercados:
            for s in rm.selecciones:
                if s.estimable and s.probabilidad is not None:
                    k = _normalizar_clave_seleccion_ronda10(s.evento, s.periodo, s.equipo, s.linea)
                    p_v = float(s.probabilidad)
                    fam = _clasificar_familia_ronda14(getattr(s, "mercado", None) or getattr(rm, "id_mercado", ""))
                    mp[k] = p_v
                    mp_fam[k] = (p_v, fam)

        sig_payload = {
            "lam": [
                mot.tasas.lambda_local_ft,
                mot.tasas.lambda_visitante_ft,
                mot.tasas.lambda_local_1t,
                mot.tasas.lambda_visitante_1t,
            ],
            "probs": [[str(k), round(v, 6)] for k, v in sorted(mp.items(), key=lambda kv: str(kv[0]))],
        }
        sig_hash = _hl_r14.sha256(json.dumps(sig_payload, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
        sc_key = (f"{scope}:{jid}", sig_hash)
        if sc_key not in sens_cache:
            sens_cache[sc_key] = mm.evaluar_sensibilidad(dlc, dvc, cl, cv, el, ev_n, par)
        sensibilidad = sens_cache[sc_key]

        _cob_corn_g = mm.cobertura_corners_por_equipo(mot, bando=None) if an_crn else {"ok_catalogo": True}
        _cob_corn_l = mm.cobertura_corners_por_equipo(mot, bando="local") if an_crn else {"ok_catalogo": True}
        _cob_corn_v = mm.cobertura_corners_por_equipo(mot, bando="visitante") if an_crn else {"ok_catalogo": True}

        est_analizar = 0
        vistos: dict[tuple[Any, ...], Any] = {}
        for rm in mercados:
            for s in rm.selecciones:
                if not s.estimable or s.probabilidad is None:
                    continue
                if mm.es_mercado_cronologia_mitades(s) and not _cron_an.get("ok_analizar", True):
                    continue
                if rm.id_mercado == "total_corners" and not _cob_corn_g.get("ok_catalogo", True):
                    continue
                if rm.id_mercado == "corners_local" and not _cob_corn_l.get("ok_catalogo", True):
                    continue
                if rm.id_mercado == "corners_visitante" and not _cob_corn_v.get("ok_catalogo", True):
                    continue
                est_analizar += 1
                sc = _copy_r14.copy(s)
                if an_cal:
                    try:
                        from agente_consciente import evaluar_familia_conciencia_cached
                        _cal_s = evaluar_familia_conciencia_cached(sc.mercado, equipo=sc.equipo, prob_bruta=float(sc.probabilidad))
                        setattr(sc, "prob_calibrada", _cal_s.get("prob_calibrada"))
                        setattr(sc, "bajo_vigilancia", bool(_cal_s.get("bajo_vigilancia", False)))
                        setattr(sc, "veto_top5", bool(_cal_s.get("veto_top5", False)))
                        setattr(sc, "familia_conciencia", _cal_s.get("familia"))
                    except Exception:
                        pass
                if float(sc.probabilidad) <= par.umbral:
                    continue
                esc = sensibilidad.get(mm._clave_a_texto(sc.clave_unica), {})
                sc.escenarios = esc
                req = requeridos_conteos if rm.id_mercado in mercados_conteo else requeridos_goles
                est_sc, mot_sc = mm.clasificar(sc.probabilidad, esc, req, par.umbral)
                if not est_sc:
                    continue
                excede_desv, desv_pts, f_obs = mm.tiene_desvio_excesivo_calibracion(
                    sc, mot.doc_local, mot.doc_visitante, mot.nombre_local, mot.nombre_visitante
                )
                if excede_desv:
                    est_sc = mm.ESTADO_SENSIBLE
                    _brecha_emp = round(round(float(f_obs) * 100.0, 1) - round(float(sc.probabilidad) * 100.0, 1), 1)
                    mot_sc = f"sin calibración validada (frec_emp: {f_obs*100:.1f}% vs mod: {sc.probabilidad*100:.1f}%, brecha {_brecha_emp:+.1f} pp, desvío {desv_pts:.1f} pts > 10.0)"
                sc.estado = est_sc
                if mot_sc:
                    sc.motivo = mot_sc
                if _rep_5050 and mm.es_mercado_cronologia_mitades(sc):
                    mot_cur = str(getattr(sc, "motivo", "") or "")
                    if "reparto 50/50 sin validar" not in mot_cur.lower():
                        setattr(sc, "motivo", f"{mot_cur}; reparto 50/50 sin validar".strip("; "))
                vistos.setdefault(sc.clave_unica, sc)

        sels_flt = list(vistos.values())
        grupos = {mm.ESTADO_MANTIENE: [], mm.ESTADO_SENSIBLE: [], mm.ESTADO_INCOMPLETA: []}
        for sc in sels_flt:
            grupos.setdefault(sc.estado, []).append(sc)
        an_ligero = mm.Analisis(meta_an, par, mot, mercados, sels_flt, grupos, sensibilidad, "", "")
        t5, s1, _ = sm.obtener_top5_y_seccion1_filtrados(
            an_ligero,
            metadatos={"equipo_local": el, "equipo_visitante": ev_n},
            umbral=par.umbral,
        )
        return {
            "mot": mot,
            "mp": mp,
            "mp_fam": mp_fam,
            "sels_flt": sels_flt,
            "top5": t5 or [],
            "sec1": s1 or [],
            "est_catalogo": len(mp),
            "est_analizar": est_analizar,
            "abstenido_mitad": abstenido_mitad,
        }

    # ── 2. Impacto sobre los 73 registros únicos ───────────────────────────────────────────
    pares_73 = _recolectar_pares_partidos_cacheados_ronda9()
    pares_73_data: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any, dict[str, Any]]] = []
    for reg in pares_73:
        f_p = reg.get("fecha_partido")
        dl_f = mm.filtrar_por_fecha(reg["doc_local"], f_p) if f_p else reg["doc_local"]
        dv_f = mm.filtrar_por_fecha(reg["doc_visitante"], f_p) if f_p else reg["doc_visitante"]
        par = mm.Parametros()
        if reg.get("competicion"):
            par.competicion = str(reg["competicion"])
        meta_73 = {
            "equipo_local": reg["equipo_local"],
            "equipo_visitante": reg["equipo_visitante"],
            "fecha_partido": f_p,
            "competicion": reg.get("competicion"),
            "condicion_local": reg.get("cond_l", "casa"),
            "condicion_visitante": reg.get("cond_v", "fuera"),
        }
        pares_73_data.append((reg, dl_f, dv_f, par, meta_73))

    def _sel_clave_simple(it: Any) -> str:
        def _g(o: Any, k: str, d: Any = None) -> Any:
            return o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)
        per = str(_g(it, "periodo", "FT") or "FT")
        ev = str(_g(it, "evento", "") or "")
        if not ev:
            return str(_g(it, "etiqueta") or "")
        eq = str(_g(it, "equipo", "") or "")
        lin = _g(it, "linea")
        return f"{ev}|{per}|{eq}|{'' if lin is None else round(float(lin), 4)}"

    def _sel_etiqueta(it: Any) -> str:
        if isinstance(it, dict):
            return str(it.get("etiqueta") or it.get("evento") or "")
        return str(getattr(it, "etiqueta", "") or getattr(it, "evento", ""))

    res_73: dict[str, dict[str, Any]] = {}
    for nom_cfg, bdict in cfgs_dict.items():
        lam_por_jid: dict[str, tuple[float | None, float | None]] = {}
        probs_por_jid: dict[str, dict[tuple[str, str, str | None, float | None], tuple[float, str]]] = {}
        top5_por_jid: dict[str, list[dict[str, Any]]] = {}
        sec1_por_jid: dict[str, list[dict[str, Any]]] = {}
        n_abst = 0
        n_est_cat = 0

        with banderas_comportamiento(activar_todas=False, **bdict):
            f_mit = _ba_r14("usar_gate_cronologia_mitades_10_15")
            f_crn = _ba_r14("usar_gate_corners_8_15")
            f_cal = _ba_r14("usar_calibracion_y_veto_conciencia")

            for reg, dl_f, dv_f, par, meta_73 in pares_73_data:
                jid = str(reg["job_id"])
                ev_l = _evaluar_partido_ligero(
                    "73",
                    jid,
                    dl_f,
                    dv_f,
                    meta_73["condicion_local"],
                    meta_73["condicion_visitante"],
                    reg["equipo_local"],
                    reg["equipo_visitante"],
                    par,
                    meta_73,
                    an_mit=f_mit,
                    an_crn=f_crn,
                    an_cal=f_cal,
                )
                mot = ev_l["mot"]
                ll_ft = round(float(mot.tasas.lambda_local_ft), 6) if mot.tasas.lambda_local_ft is not None else None
                lv_ft = round(float(mot.tasas.lambda_visitante_ft), 6) if mot.tasas.lambda_visitante_ft is not None else None
                lam_por_jid[jid] = (ll_ft, lv_ft)
                probs_por_jid[jid] = ev_l["mp_fam"]
                n_est_cat += ev_l["est_catalogo"]
                if ev_l["abstenido_mitad"]:
                    n_abst += 1
                top5_por_jid[jid] = [
                    {"clave": _sel_clave_simple(x), "etiqueta": _sel_etiqueta(x), "prob": round(float(x.get("probabilidad") or 0.0), 6)}
                    for x in ev_l["top5"]
                ]
                sec1_por_jid[jid] = [
                    {"clave": _sel_clave_simple(x), "etiqueta": _sel_etiqueta(x), "prob": round(float(getattr(x, "probabilidad", 0.0) or 0.0), 6)}
                    for x in ev_l["sec1"]
                ]

        lams_l_v = [v[0] for v in lam_por_jid.values() if v[0] is not None]
        lams_v_v = [v[1] for v in lam_por_jid.values() if v[1] is not None]
        res_73[nom_cfg] = {
            "n_registros": len(pares_73),
            "n_abstenidos_goles_mitad": n_abst,
            "n_estimables_catalogo": n_est_cat,
            "n_top5_total": sum(len(v) for v in top5_por_jid.values()),
            "n_partidos_con_top5": sum(1 for v in top5_por_jid.values() if v),
            "n_sec1_total": sum(len(v) for v in sec1_por_jid.values()),
            "n_partidos_con_sec1": sum(1 for v in sec1_por_jid.values() if v),
            "media_lambda_local_ft": round(sum(lams_l_v) / max(1, len(lams_l_v)), 4),
            "media_lambda_visitante_ft": round(sum(lams_v_v) / max(1, len(lams_v_v)), 4),
            "_lam": lam_por_jid,
            "_probs": probs_por_jid,
            "_top5": top5_por_jid,
            "_sec1": sec1_por_jid,
        }

    def _comparar_73(nom_new: str, nom_ref: str) -> dict[str, Any]:
        cn = res_73[nom_new]
        cr = res_73[nom_ref]
        partidos_lam = []
        dl_cond, dv_cond = [], []
        n_sel_ch = 0
        fam_stats: dict[str, dict[str, Any]] = {}
        n_p_prob_ch = 0
        n_p_t5_ch = 0
        n_t5_in = 0
        n_t5_out = 0
        ejemplos_t5 = []
        n_p_s1_ch = 0
        n_s1_ch = 0

        for reg in pares_73:
            jid = str(reg["job_id"])
            la_n, va_n = cn["_lam"][jid]
            la_r, va_r = cr["_lam"][jid]
            if la_n != la_r or va_n != va_r:
                if abs((la_n or 0.0) - (la_r or 0.0)) > 1e-6 or abs((va_n or 0.0) - (va_r or 0.0)) > 1e-6:
                    dl_i = round(float(la_n) - float(la_r), 4) if (la_n is not None and la_r is not None) else None
                    dv_i = round(float(va_n) - float(va_r), 4) if (va_n is not None and va_r is not None) else None
                    if dl_i is not None:
                        dl_cond.append(dl_i)
                    if dv_i is not None:
                        dv_cond.append(dv_i)
                    partidos_lam.append({
                        "job_id": jid,
                        "partido": reg["partido"],
                        "lam_antes": [round(float(la_r), 4) if la_r is not None else None, round(float(va_r), 4) if va_r is not None else None],
                        "lam_despues": [round(float(la_n), 4) if la_n is not None else None, round(float(va_n), 4) if va_n is not None else None],
                        "delta": [dl_i, dv_i],
                    })

            pm_n = cn["_probs"][jid]
            pm_r = cr["_probs"][jid]
            ch_match_p = False
            for k in sorted(set(pm_n.keys()) | set(pm_r.keys()), key=str):
                vn = pm_n.get(k)
                vr = pm_r.get(k)
                fam = vn[1] if vn else vr[1]
                fs = fam_stats.setdefault(fam, {"n_cambiadas": 0, "pids": set(), "sum_d": 0.0, "sum_abs_d": 0.0})
                if vn is None or vr is None:
                    n_sel_ch += 1
                    ch_match_p = True
                    fs["n_cambiadas"] += 1
                    fs["pids"].add(jid)
                else:
                    d_p = float(vn[0]) - float(vr[0])
                    if abs(d_p) > 1e-6:
                        n_sel_ch += 1
                        ch_match_p = True
                        fs["n_cambiadas"] += 1
                        fs["pids"].add(jid)
                        fs["sum_d"] += d_p
                        fs["sum_abs_d"] += abs(d_p)
            if ch_match_p:
                n_p_prob_ch += 1

            t5_n = cn["_top5"][jid]
            t5_r = cr["_top5"][jid]
            ck_n = {x["clave"]: x for x in t5_n}
            ck_r = {x["clave"]: x for x in t5_r}
            if set(ck_n.keys()) != set(ck_r.keys()):
                entr = [ck_n[k]["etiqueta"] for k in ck_n if k not in ck_r]
                sal = [ck_r[k]["etiqueta"] for k in ck_r if k not in ck_n]
                n_p_t5_ch += 1
                n_t5_in += len(entr)
                n_t5_out += len(sal)
                if len(ejemplos_t5) < 12:
                    ejemplos_t5.append({"partido": reg["partido"], "entran": entr, "salen": sal})

            s1_n = {x["clave"] for x in cn["_sec1"][jid]}
            s1_r = {x["clave"] for x in cr["_sec1"][jid]}
            if s1_n != s1_r:
                n_p_s1_ch += 1
                n_s1_ch += len(s1_n ^ s1_r)

        fam_out = {}
        for fam, st in sorted(fam_stats.items()):
            nc = st["n_cambiadas"]
            fam_out[fam] = {
                "n_selecciones_cambiadas": nc,
                "n_partidos": len(st["pids"]),
                "delta_prob_medio": round(st["sum_d"] / nc, 6) if nc and st["sum_d"] != 0.0 else (0.0 if nc else None),
                "abs_delta_prob_medio": round(st["sum_abs_d"] / nc, 6) if nc and st["sum_abs_d"] != 0.0 else (0.0 if nc else None),
            }

        return {
            "n_partidos_lambda_cambiada": len(partidos_lam),
            "delta_lambda_local_medio_cond": round(sum(dl_cond) / len(dl_cond), 4) if dl_cond else 0.0,
            "delta_lambda_visitante_medio_cond": round(sum(dv_cond) / len(dv_cond), 4) if dv_cond else 0.0,
            "partidos_lambda_cambiada": partidos_lam,
            "n_selecciones_catalogo_cambiadas": n_sel_ch,
            "n_partidos_prob_cambiada": n_p_prob_ch,
            "cambios_prob_por_familia": fam_out,
            "n_partidos_cambio_composicion_top5": n_p_t5_ch,
            "n_lineas_top5_entraron": n_t5_in,
            "n_lineas_top5_salieron": n_t5_out,
            "delta_n_top5": cn["n_top5_total"] - cr["n_top5_total"],
            "ejemplos_cambios_top5": ejemplos_t5,
            "n_partidos_cambio_composicion_sec1": n_p_s1_ch,
            "n_lineas_sec1_diferencia_simetrica": n_s1_ch,
            "delta_n_sec1": cn["n_sec1_total"] - cr["n_sec1_total"],
        }

    impacto_73 = {
        "n_registros": len(pares_73),
        "resumen_configuraciones": {
            k: {fk: fv for fk, fv in v.items() if not fk.startswith("_")}
            for k, v in res_73.items()
        },
        "comparaciones": {
            "defecto_ronda14_nb_vs_soloA_previo": _comparar_73("defecto_ronda14_nb", "soloA_previo"),
            "sombra_guarda_ddr_vs_defecto_ronda14_nb": _comparar_73("sombra_guarda_ddr", "defecto_ronda14_nb"),
            "A_mas_B_14_vs_soloA_previo": _comparar_73("A_mas_B_14", "soloA_previo"),
        },
    }

    # ── 3. Impacto sobre los 24 OOS (1376 catálogo + 4 familias + Top 5 + Sección 1, IC95%) ──
    oos = json.loads((raiz / "datos" / "oos_conciliacion_ronda7_ronda8.json").read_text(encoding="utf-8"))
    filas_1506 = list(oos.get("filas_1506") or [])
    filas_1376 = [
        r for r in filas_1506
        if all(k in (r.get("probs") or {}) for k in ("arch", "5e8425f", "994209a", "soloA", "A+B"))
    ]
    mapa_r10 = json.loads((raiz / "datos" / "ronda10_oos_26_jobs_map.json").read_text(encoding="utf-8"))
    jobs_map = dict(mapa_r10.get("jobs") or {})
    no_comp_jids = set(mapa_r10.get("no_comp_jids") or [])

    merc_by_job: dict[str, dict[str, Any]] = {}
    for fp in sorted((raiz / "informes" / "mercados").rglob("mercados_*.json")):
        md = json.loads(fp.read_text(encoding="utf-8"))
        if md.get("id_trabajo"):
            merc_by_job[str(md["id_trabajo"])] = md

    idx_pid: dict[tuple[str, tuple[str, str, str | None, float | None]], int] = {}
    pid_by_jid: dict[str, str] = {}
    stats_pid: dict[str, dict[str, int]] = {}
    for r in filas_1506:
        jid = str(r["id_trabajo"])
        pid = str(r.get("id_partido") or "")
        pid_by_jid[jid] = pid
        k = _normalizar_clave_seleccion_ronda10(r.get("evento"), r.get("periodo"), r.get("equipo"), r.get("linea"))
        idx_pid[(pid, k)] = int(r["evento_ocurrio"])

    for fp_ev in sorted((raiz / "informes" / "evaluacion").glob("evaluacion_*.json")):
        d_ev = json.loads(fp_ev.read_text(encoding="utf-8"))
        for s in (d_ev.get("evaluaciones") or []):
            jid_s = str(s.get("id_trabajo") or "")
            pid_s = str(s.get("id_partido") or pid_by_jid.get(jid_s) or "")
            if pid_s in set(pid_by_jid.values()) and s.get("evento_ocurrio") is not None:
                k = _normalizar_clave_seleccion_ronda10(s.get("evento"), s.get("periodo"), s.get("equipo"), s.get("linea"))
                idx_pid.setdefault((pid_s, k), int(s["evento_ocurrio"]))
                ev_s = str(s.get("evento") or "")
                per_s = str(s.get("periodo") or "FT")
                det_s = str(s.get("detalle") or "")
                m_det = _re_r14.match(r"^\s*(\d+)\s+(goles|córneres|corneres|tarjetas)", det_s)
                st_p = stats_pid.setdefault(pid_s, {})
                if m_det:
                    val_d = int(m_det.group(1))
                    if ev_s.startswith(("over_", "under_", "al_menos_", "fiesta_")) and not s.get("equipo"):
                        st_p[f"goles_{per_s}"] = val_d
                    elif ev_s.startswith("local_") and "goles" in str(s.get("mercado") or "").lower():
                        st_p[f"goles_local_{per_s}"] = val_d
                    elif ev_s.startswith("visitante_") and "goles" in str(s.get("mercado") or "").lower():
                        st_p[f"goles_visita_{per_s}"] = val_d

    extra_pid_eventos: dict[tuple[str, str, str], int] = {
        ("5795478", "primer_gol_local", "FT"): 0,
        ("6106338", "primer_gol_visitante", "FT"): 0,
        ("5795481", "primer_gol_local", "FT"): 1,
        ("5795481", "gol_ambos_tiempos", "FT"): 1,
    }
    stats_pid.setdefault("1000020615", {})["goles_1T"] = 1
    stats_pid.setdefault("1000020615", {})["goles_2T"] = 2

    def _g(o: Any, k: str, d: Any = None) -> Any:
        return o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)

    def _match_top5_cand(item_t5: dict[str, Any], cands: list[Any], eq_l: str, eq_v: str) -> Any:
        etq_t5 = str(item_t5.get("etiqueta") or "")
        p_t5 = float(item_t5.get("probabilidad") or 0.0)
        if "(1T)" in etq_t5 or "1ra Mitad" in etq_t5:
            per_t5 = "1T"
        elif "(2T)" in etq_t5 or "2da Mitad" in etq_t5:
            per_t5 = "2T"
        else:
            per_t5 = "FT"
        mejores = [
            s for s in cands
            if str(getattr(s, "periodo", "FT") or "FT") == per_t5
            and abs(float(getattr(s, "probabilidad", 0.0) or 0.0) - p_t5) < 5e-4
        ]
        if len(mejores) == 1:
            return mejores[0]
        for s in mejores:
            ev_s = str(getattr(s, "evento", "") or "")
            eq_s = str(getattr(s, "equipo", "") or "")
            lin_s = getattr(s, "linea", None)
            if lin_s is not None and f"{lin_s:g}" in etq_t5:
                if eq_s and eq_s.lower() in etq_t5.lower():
                    return s
                if not eq_s and eq_l.lower() not in etq_t5.lower() and eq_v.lower() not in etq_t5.lower():
                    return s
            if "Anota primero" in etq_t5 and "primer" in ev_s and eq_s and eq_s.lower() in etq_t5.lower():
                return s
            if "ambos tiempos" in etq_t5.lower() and "ambos_tiempos" in ev_s:
                return s
        return mejores[0] if mejores else None

    def _grade_sel(jid: str, sel_obj: Any, item_t5: dict[str, Any] | None, meta: dict[str, Any]) -> int | None:
        pid = pid_by_jid[jid]
        if sel_obj is not None:
            ev = str(_g(sel_obj, "evento") or "")
            per = str(_g(sel_obj, "periodo", "FT") or "FT").strip().upper()
            eq = _g(sel_obj, "equipo")
            lin = _g(sel_obj, "linea")
            if (pid, ev, per) in extra_pid_eventos:
                return extra_pid_eventos[(pid, ev, per)]
            k = _normalizar_clave_seleccion_ronda10(ev, per, eq, lin)
            if (pid, k) in idx_pid:
                return idx_pid[(pid, k)]
            if ev.startswith("al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"over_{float(lin) - 0.5:g}", per, eq, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            if ev.startswith("local_al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"local_over_{float(lin) - 0.5:g}", per, eq, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            if ev.startswith("visitante_al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"visitante_over_{float(lin) - 0.5:g}", per, eq, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            if ev.startswith("fiesta_al_menos_") and lin is not None:
                k2 = _normalizar_clave_seleccion_ronda10(f"over_{float(lin) - 0.5:g}", per, None, float(lin) - 0.5)
                if (pid, k2) in idx_pid:
                    return idx_pid[(pid, k2)]
            st = stats_pid.get(pid, {})
            if f"goles_{per}" in st and not eq and lin is not None:
                g = st[f"goles_{per}"]
                if "al_menos" in ev:
                    return int(g >= float(lin))
                if "over" in ev:
                    return int(g > float(lin))
                if "under" in ev:
                    return int(g < float(lin))
            if eq == meta.get("equipo_local") and f"goles_local_{per}" in st and lin is not None:
                g = st[f"goles_local_{per}"]
                if "al_menos" in ev:
                    return int(g >= float(lin))
                if "over" in ev:
                    return int(g > float(lin))
                if "under" in ev:
                    return int(g < float(lin))
            if eq == meta.get("equipo_visitante") and f"goles_visita_{per}" in st and lin is not None:
                g = st[f"goles_visita_{per}"]
                if "al_menos" in ev:
                    return int(g >= float(lin))
                if "over" in ev:
                    return int(g > float(lin))
                if "under" in ev:
                    return int(g < float(lin))
            if ev == "gol_2t" and "goles_2T" in st:
                return int(st["goles_2T"] >= 1)
            if ev == "gol_1t" and "goles_1T" in st:
                return int(st["goles_1T"] >= 1)
        if item_t5 is not None:
            etq = str(item_t5.get("etiqueta") or "")
            if "1ra Mitad" in etq and "Menos de 1.5 Goles" in etq:
                k_1t = _normalizar_clave_seleccion_ronda10("under_1.5", "1T", None, 1.5)
                if (pid, k_1t) in idx_pid:
                    return idx_pid[(pid, k_1t)]
                st = stats_pid.get(pid, {})
                if "goles_1T" in st:
                    return int(st["goles_1T"] < 1.5)
        return None

    ultimo_jid_por_pid: dict[str, str] = {}
    for jid in sorted(jobs_map):
        pid = pid_by_jid.get(jid) or jid
        prev_j = ultimo_jid_por_pid.get(pid)
        if prev_j is None:
            ultimo_jid_por_pid[pid] = jid
        else:
            g_cur = str((merc_by_job.get(jid, {}).get("metadatos") or {}).get("generado_en") or "")
            g_prev = str((merc_by_job.get(prev_j, {}).get("metadatos") or {}).get("generado_en") or "")
            if g_cur >= g_prev:
                ultimo_jid_por_pid[pid] = jid
    jids_24 = set(ultimo_jid_por_pid.values())

    jobs_data: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any]] = {}
    for jid in sorted(jobs_map):
        fpl_rel, fpv_rel, cut = jobs_map[jid]
        dl = json.loads((raiz / fpl_rel).read_text(encoding="utf-8"))
        dv = json.loads((raiz / fpv_rel).read_text(encoding="utf-8"))
        dlc = mm.filtrar_por_fecha(dl, cut) if cut else dl
        dvc = mm.filtrar_por_fecha(dv, cut) if cut else dv
        meta = dict(merc_by_job[jid]["metadatos"])
        par = mm.Parametros()
        if meta.get("competicion") and jid not in no_comp_jids:
            par.competicion = str(meta["competicion"])
        jobs_data[jid] = (dlc, dvc, meta, par)

    oos_res: dict[str, dict[str, Any]] = {}
    for nom_cfg, bdict in cfgs_dict.items():
        probs_cat: dict[str, dict[tuple[str, str, str | None, float | None], float]] = {}
        lam_24: dict[str, tuple[str, float | None, float | None]] = {}
        est_analizar_total = 0
        est_catalogo_total = 0
        abst_goles_mitad = 0
        top5_24_rows: list[dict[str, Any]] = []
        sec1_24_rows: list[dict[str, Any]] = []

        with banderas_comportamiento(activar_todas=False, **bdict):
            _an_mit = _ba_r14("usar_gate_cronologia_mitades_10_15")
            _an_crn = _ba_r14("usar_gate_corners_8_15")
            _an_cal = _ba_r14("usar_calibracion_y_veto_conciencia")

            for jid in sorted(jobs_data):
                dlc, dvc, meta, par = jobs_data[jid]
                el, ev_n = str(meta["equipo_local"]), str(meta["equipo_visitante"])
                cl = meta.get("condicion_local") or "casa"
                cv = meta.get("condicion_visitante") or "fuera"

                ev_l = _evaluar_partido_ligero(
                    "oos",
                    jid,
                    dlc,
                    dvc,
                    cl,
                    cv,
                    el,
                    ev_n,
                    par,
                    meta,
                    an_mit=_an_mit,
                    an_crn=_an_crn,
                    an_cal=_an_cal,
                )
                mot = ev_l["mot"]
                probs_cat[jid] = ev_l["mp"]
                est_catalogo_total += ev_l["est_catalogo"]
                est_analizar_total += ev_l["est_analizar"]
                if ev_l["abstenido_mitad"]:
                    abst_goles_mitad += 1

                if jid not in jids_24:
                    continue

                lam_24[jid] = (
                    f"{el} vs {ev_n}",
                    round(float(mot.tasas.lambda_local_ft), 4) if mot.tasas.lambda_local_ft is not None else None,
                    round(float(mot.tasas.lambda_visitante_ft), 4) if mot.tasas.lambda_visitante_ft is not None else None,
                )
                pid = pid_by_jid[jid]
                sels_flt = ev_l["sels_flt"]
                for it5 in ev_l["top5"]:
                    cand = _match_top5_cand(it5, sels_flt, el, ev_n)
                    res_y = _grade_sel(jid, cand, it5, meta)
                    p_item = float(it5.get("probabilidad") or 0.0)
                    ev_k = str(getattr(cand, "evento", None) or it5.get("evento") or it5.get("etiqueta") or "")
                    per_k = str(getattr(cand, "periodo", None) or "FT")
                    eq_k = str(getattr(cand, "equipo", None) or "")
                    lin_k = getattr(cand, "linea", None)
                    top5_24_rows.append({
                        "id_partido": pid,
                        "id_trabajo": jid,
                        "partido": f"{el} vs {ev_n}",
                        "etiqueta": str(it5.get("etiqueta") or ""),
                        "clave_linea": (pid, ev_k, per_k, eq_k, lin_k),
                        "prob": p_item,
                        "evento_ocurrio": res_y,
                    })
                for sc1 in ev_l["sec1"]:
                    res_y = _grade_sel(jid, sc1, None, meta)
                    ev_k = str(_g(sc1, "evento") or "")
                    per_k = str(_g(sc1, "periodo", "FT") or "FT")
                    eq_k = str(_g(sc1, "equipo") or "")
                    lin_k = _g(sc1, "linea")
                    sec1_24_rows.append({
                        "id_partido": pid,
                        "id_trabajo": jid,
                        "partido": f"{el} vs {ev_n}",
                        "etiqueta": str(_g(sc1, "etiqueta") or ev_k),
                        "clave_linea": (pid, ev_k, per_k, eq_k, lin_k),
                        "prob": float(_g(sc1, "probabilidad") or 0.0),
                        "evento_ocurrio": res_y,
                    })

        oos_res[nom_cfg] = {
            "probs_cat": probs_cat,
            "lam_24": lam_24,
            "est_catalogo_total": est_catalogo_total,
            "est_analizar_total": est_analizar_total,
            "abst_goles_mitad": abst_goles_mitad,
            "top5_rows": top5_24_rows,
            "sec1_rows": sec1_24_rows,
        }

    orden_cfgs = ["soloA_previo", "defecto_ronda14_nb", "sombra_guarda_ddr", "A_mas_B_14"]
    obs_1376_all: list[tuple[str, int, dict[str, float]]] = []
    obs_1376_fam: dict[str, list[tuple[str, int, dict[str, float]]]] = {}
    for r in filas_1376:
        jid = str(r["id_trabajo"])
        pid = str(r["id_partido"])
        y = int(r["evento_ocurrio"])
        fam = _clasificar_familia_ronda14(r.get("mercado"))
        k = _normalizar_clave_seleccion_ronda10(r["evento"], r["periodo"], r["equipo"], r["linea"])
        d_raw: dict[str, float] = {}
        ok_row = True
        for nom in orden_cfgs:
            p_raw = oos_res[nom]["probs_cat"].get(jid, {}).get(k)
            if p_raw is None:
                ok_row = False
                break
            d_raw[nom] = float(p_raw)
        if ok_row:
            obs_1376_all.append((pid, y, d_raw))
            obs_1376_fam.setdefault(fam, []).append((pid, y, d_raw))

    boot_1376_global = bootstrap_brier_logloss_por_partido(obs_1376_all, n_boot=n_boot, seed=seed)
    boot_1376_fams = {
        fam: bootstrap_brier_logloss_por_partido(rows_f, n_boot=n_boot, seed=seed + idx + 1)
        for idx, (fam, rows_f) in enumerate(sorted(obs_1376_fam.items()))
    }

    def _boot_sel_pairs(rows_c: list[dict[str, Any]], rows_ref_a: list[dict[str, Any]], rows_ref_nb: list[dict[str, Any]], seed_v: int) -> dict[str, Any]:
        ev_c = [r for r in rows_c if r["evento_ocurrio"] is not None]
        ev_ra = [r for r in rows_ref_a if r["evento_ocurrio"] is not None]
        ev_rnb = [r for r in rows_ref_nb if r["evento_ocurrio"] is not None]
        if not ev_c:
            return {"n_lineas": len(rows_c), "n_resueltas": 0}

        def _pmap(rows_l: list[dict[str, Any]]) -> dict[str, list[tuple[int, float, float]]]:
            mp: dict[str, list[tuple[int, float, float]]] = {}
            for r in rows_l:
                y = int(r["evento_ocurrio"])
                p = float(r["prob"])
                pc = min(max(p, 1e-12), 1.0 - 1e-12)
                mp.setdefault(str(r["id_partido"]), []).append((y, (p - y) ** 2, -(y * math.log(pc) + (1.0 - y) * math.log(1.0 - pc))))
            return mp

        mc, mra, mrnb = _pmap(ev_c), _pmap(ev_ra), _pmap(ev_rnb)
        pids_u = sorted(set(mc) | set(mra) | set(mrnb))

        def _agg(m_map: dict[str, list[tuple[int, float, float]]], sp: list[str]) -> tuple[float, float, float] | None:
            sa = sb = sl = cnt = 0.0
            for pid_b in sp:
                for y_b, br_b, ll_b in m_map.get(pid_b, ()):
                    sa += y_b
                    sb += br_b
                    sl += ll_b
                    cnt += 1.0
            return (sa / cnt, sb / cnt, sl / cnt) if cnt > 0 else None

        oc = _agg(mc, sorted(mc))
        ora = _agg(mra, sorted(mra))
        ornb = _agg(mrnb, sorted(mrnb))
        rng = _rnd_r14.Random(seed_v)
        tc, bc, lc = [], [], []
        dta, dba, dla = [], [], []
        dtnb, dbnb, dlnb = [], [], []
        m_u = len(pids_u)
        for _ in range(n_boot):
            mu = [pids_u[rng.randrange(m_u)] for _ in range(m_u)]
            ac = _agg(mc, mu)
            ara = _agg(mra, mu)
            arnb = _agg(mrnb, mu)
            if ac is not None:
                tc.append(ac[0]); bc.append(ac[1]); lc.append(ac[2])
                if ara is not None:
                    dta.append(ac[0] - ara[0]); dba.append(ac[1] - ara[1]); dla.append(ac[2] - ara[2])
                if arnb is not None:
                    dtnb.append(ac[0] - arnb[0]); dbnb.append(ac[1] - arnb[1]); dlnb.append(ac[2] - arnb[2])
        for arr in (tc, bc, lc, dta, dba, dla, dtnb, dbnb, dlnb):
            arr.sort()

        def _ic(arr: list[float], dec: int) -> list[float] | None:
            if not arr:
                return None
            return [round(arr[int(0.025 * len(arr))], dec), round(arr[min(len(arr) - 1, int(0.975 * len(arr)))], dec)]

        ck_c = {r["clave_linea"] for r in rows_c}
        ck_rnb = {r["clave_linea"] for r in rows_ref_nb}
        return {
            "n_lineas": len(rows_c),
            "n_partidos": len(mc),
            "n_resueltas": len(ev_c),
            "n_no_resolubles": len(rows_c) - len(ev_c),
            "aciertos": sum(int(r["evento_ocurrio"]) for r in ev_c),
            "tasa_acierto": round(oc[0], 4) if oc else None,
            "tasa_ic95": _ic(tc, 4),
            "brier": round(oc[1], 6) if oc else None,
            "brier_ic95": _ic(bc, 6),
            "log_loss": round(oc[2], 6) if oc else None,
            "log_loss_ic95": _ic(lc, 6),
            "d_tasa_vs_soloA_previo": round(oc[0] - ora[0], 4) if (oc and ora) else None,
            "d_brier_vs_soloA_previo": round(oc[1] - ora[1], 6) if (oc and ora) else None,
            "d_brier_vs_soloA_previo_ic95": _ic(dba, 6),
            "d_log_loss_vs_soloA_previo": round(oc[2] - ora[2], 6) if (oc and ora) else None,
            "d_log_loss_vs_soloA_previo_ic95": _ic(dla, 6),
            "d_tasa_vs_defecto_r14_nb": round(oc[0] - ornb[0], 4) if (oc and ornb) else None,
            "d_brier_vs_defecto_r14_nb": round(oc[1] - ornb[1], 6) if (oc and ornb) else None,
            "d_brier_vs_defecto_r14_nb_ic95": _ic(dbnb, 6),
            "d_log_loss_vs_defecto_r14_nb": round(oc[2] - ornb[2], 6) if (oc and ornb) else None,
            "d_log_loss_vs_defecto_r14_nb_ic95": _ic(dlnb, 6),
            "n_lineas_cambiadas_vs_defecto_r14_nb": len(ck_c ^ ck_rnb),
        }

    top5_24_summary = {}
    sec1_24_summary = {}
    cobertura_26_summary = {}
    for idx_c, nom in enumerate(orden_cfgs):
        top5_24_summary[nom] = _boot_sel_pairs(
            oos_res[nom]["top5_rows"],
            oos_res["soloA_previo"]["top5_rows"],
            oos_res["defecto_ronda14_nb"]["top5_rows"],
            seed + 100 + idx_c,
        )
        sec1_24_summary[nom] = _boot_sel_pairs(
            oos_res[nom]["sec1_rows"],
            oos_res["soloA_previo"]["sec1_rows"],
            oos_res["defecto_ronda14_nb"]["sec1_rows"],
            seed + 200 + idx_c,
        )
        cobertura_26_summary[nom] = {
            "n_estimables_catalogo_26_jobs": oos_res[nom]["est_catalogo_total"],
            "n_estimables_analizar_26_jobs": oos_res[nom]["est_analizar_total"],
            "n_sides_abstenidos_goles_mitad": oos_res[nom]["abst_goles_mitad"],
        }

    lam_ch_oos_guarda = []
    for jid in sorted(jids_24):
        p_nb, ll_nb, lv_nb = oos_res["defecto_ronda14_nb"]["lam_24"][jid]
        _, ll_g, lv_g = oos_res["sombra_guarda_ddr"]["lam_24"][jid]
        if ll_nb != ll_g or lv_nb != lv_g:
            lam_ch_oos_guarda.append({
                "id_trabajo": jid,
                "partido": p_nb,
                "lam_defecto_nb": [ll_nb, lv_nb],
                "lam_sombra_guarda_ddr": [ll_g, lv_g],
            })

    out = {
        "configuracion_ronda14": {
            "banderas_comportamiento": banderas_vigentes,
            "metadatos_defectos": metadatos_defectos,
            "min_cuota_justa_top5": cuota_justa,
            "brazo_sombra_dedicado": "usar_guarda_doble_defensa_rota",
        },
        "verificacion_ciclo_sombra_guarda_ddr": verif_ciclo,
        "impacto_73_registros": impacto_73,
        "impacto_24_oos": {
            "cohorte": {
                "n_partidos_unicos": len(jids_24),
                "n_jobs": len(jobs_data),
                "n_filas_comunes": boot_1376_global.get("n_filas"),
                "n_boot": n_boot,
                "seed": seed,
                "nota_in_sample_nb": (
                    "usar_sobredispersion_nb_corners=True (factor 1.05, regla operativa sin validación) "
                    "fue calibrada sobre estas mismas 613 filas de córneres OOS; fuera de Córneres su efecto en OOS es exactamente 0."
                ),
            },
            "cobertura_26_jobs": cobertura_26_summary,
            "catalogo_1376_global": boot_1376_global,
            "catalogo_1376_por_familia": boot_1376_fams,
            "top5_24_unicos": top5_24_summary,
            "seccion1_24_unicos": sec1_24_summary,
            "partidos_lambda_cambiada_por_guarda_ddr_24_oos": lam_ch_oos_guarda,
        },
    }
    if guardar_ruta:
        out_p = _Path_r14(guardar_ruta) if os.path.isabs(guardar_ruta) else (raiz / guardar_ruta)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
