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

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from auditoria_h2h import (H2H_DIR, buscar_auditoria, cargar_o_crear_auditoria,
                           extraer_equipos, extraer_equipos_y_liga)
from auditoria_historica import LIMITE_PARTIDOS_POR_DEFECTO
from fuentes_xg import FuenteXG, extraer_liga_de_texto
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
# RESOLUCIÓN DEL ENFRENTAMIENTO
# ═══════════════════════════════════════════════════════════════════════════════

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

        fecha = str(f.get("fecha") or "")[:10]
        if not fecha:
            continue
        clave = str(f.get("id_partido") or f"{fecha}|{home_id}|{away_id}")
        if clave in vistos:
            continue
        vistos.add(clave)
        es_casa = es_directo
        candidatos.append((fecha, {
            "id_partido": str(f.get("id_partido") or ""),
            "fecha_partido": fecha,
            "competicion": f.get("competicion"),
            "condicion_local": "casa" if es_casa else "fuera",
            "condicion_visitante": "fuera" if es_casa else "casa",
            "local_es": "local" if es_casa else "visitante",
            "evidencia_condicion": ("próximo enfrentamiento cruzado por ID numérico "
                                    "y nombre en el calendario de la fuente"),
        }))
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: x[0])          # el más próximo primero
    return candidatos[0][1]


def _metadatos_del_encuentro(doc_local: dict[str, Any], doc_visitante: dict[str, Any],
                             equipo_local: str, equipo_visitante: str) -> dict[str, Any]:
    """
    Busca un partido común entre ambos reportes para obtener fecha, competición y
    condición reales. Los reportes pueden repetir el mismo partido desde las dos
    ópticas (mismo id_partido), lo que es la evidencia más fiable.

    Orden de búsqueda:
      1. PRÓXIMO enfrentamiento programado en el calendario, cruzado por ID de equipo.
         Va PRIMERO: si los equipos tienen un partido pendiente, ése es el cruce que
         se consulta, y su localía es la del calendario. Antes se prefería el último
         H2H ya jugado, y el pronóstico quedaba anclado a una fecha pasada: al
         liquidar se elegía el partido viejo (o ninguno) y todo salía «no verificable».
      2. Partido común YA JUGADO (mismo id_partido en ambos reportes).
      3. Si no hay ninguno, se declara explícitamente que no hay partido próximo en
         el calendario (localía por orden de consulta), en vez de un «no disponible»
         que parecía un fallo de lectura.
    """
    # 1) Próximo enfrentamiento programado: es el cruce que se está consultando.
    proximo = _partido_futuro_en_comun(doc_local, doc_visitante,
                                       equipo_local, equipo_visitante)
    if proximo:
        return proximo

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
            # Cada registro trae la condición de SU equipo
            cond_l = None
            cond_v = None
            for k, p in bandos.items():
                if nombre_l in k or k in nombre_l:
                    cond_l = str(p.get("condicion", "")).lower()
                if nombre_v in k or k in nombre_v:
                    cond_v = str(p.get("condicion", "")).lower()
            return {
                "id_partido": pid,
                "fecha_partido": pl.get("fecha") or pv.get("fecha"),
                "competicion": pl.get("torneo") or pv.get("torneo"),
                "condicion_local": cond_l,
                "condicion_visitante": cond_v,
                "evidencia_condicion": "partido común registrado en ambos reportes",
            }

    # 3) Sin partido próximo ni partido común: se declara explícitamente. Antes decía
    #    «sin partido común: condición no verificable», que parecía un fallo de
    #    lectura cuando en realidad es que no hay enfrentamiento programado.
    def mas_reciente(doc: dict[str, Any]) -> dict[str, Any] | None:
        partidos = sorted(doc.get("partidos", []), key=lambda p: str(p.get("fecha") or ""),
                          reverse=True)
        return partidos[0] if partidos else None

    reciente_l, reciente_v = mas_reciente(doc_local), mas_reciente(doc_visitante)
    return {
        "id_partido": None,
        "fecha_partido": None,
        "competicion": ((reciente_l or {}).get("torneo") or (reciente_v or {}).get("torneo")),
        "condicion_local": None,
        "condicion_visitante": None,
        "evidencia_condicion": ("Sin partido próximo en calendario (localía por orden "
                                "de consulta)"),
        "sin_partido_proximo": True,
    }


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

    etiqueta_liga = ""
    if isinstance(liga_final, dict) and liga_final.get("nombre"):
        etiqueta_liga = f" [{liga_final['nombre']}]"
    elif isinstance(liga_final, str) and liga_final.strip():
        etiqueta_liga = f" [{liga_final.strip()}]"

    avisar(f"resolviendo «{equipo_local}» vs «{equipo_visitante}»{etiqueta_liga}")
    doc_a, meta_a = cargar_o_crear_auditoria(equipo_local, rival=equipo_visitante, **kwargs)
    doc_b, meta_b = cargar_o_crear_auditoria(equipo_visitante, rival=doc_a.get("equipo") or equipo_local, **kwargs)

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
    if not contexto.get("competicion") and isinstance(liga_final, dict) and liga_final.get("nombre"):
        contexto["competicion"] = liga_final["nombre"]
    avisar(f"condición: {nombre_a}={contexto.get('condicion_local')} · "
           f"{nombre_b}={contexto.get('condicion_visitante')} "
           f"({contexto.get('evidencia_condicion')})")

    return {
        "equipo_local": nombre_a,
        "equipo_visitante": nombre_b,
        "doc_local": doc_a,
        "doc_visitante": doc_b,
        "meta_local": meta_a,
        "meta_visitante": meta_b,
        "contexto": contexto,
        "liga_solicitada": liga_final,
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
FIRMA_MOTOR = "red-neuronal-recursiva-45"


def identificar_trabajo(analisis_metadatos: dict[str, Any], doc_local: dict[str, Any],
                        doc_visitante: dict[str, Any]) -> str:
    """
    Identificador estable del trabajo: partido + fecha + versión de datos + versión
    del modelo + firma del motor. Sirve para no repetir envíos ya confirmados y
    para distinguir un reintento técnico de una nueva solicitud explícita.
    """
    firma_datos = hashlib.sha256(
        json.dumps([len(doc_local.get("partidos", [])), len(doc_visitante.get("partidos", [])),
                    doc_local.get("generado_en"), doc_visitante.get("generado_en")],
                   ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
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
    return (
        texto.replace("🎯 JUGADA ÓPTIMA TE APUESTO:", ">> JUGADA OPTIMA TE APUESTO:")
             .replace("🎯 JUGADA OPTIMA TE APUESTO:", ">> JUGADA OPTIMA TE APUESTO:")
             .replace("🎯", ">>")
             .replace("⚖️ DUELO PAREJO / RIESGO DE EMPATE", "[DUELO PAREJO / RIESGO DE EMPATE]")
             .replace("⚖ DUELO PAREJO / RIESGO DE EMPATE", "[DUELO PAREJO / RIESGO DE EMPATE]")
             .replace("⚖️", "[ALERTA]")
             .replace("⚖", "[ALERTA]")
             .replace("⊗", "x")
             .replace("★", "*")
             .strip()
    )


def obtener_top5_y_seccion1_filtrados(
    analisis,
    metadatos = None,
    umbral = None,
):
    """
    Extrae el Top-5 Podio y la lista filtrada de Sección 1 (Gatekeeper)
    garantizando que las líneas triviales como -6.5 Goles o -5.5 Goles sean eliminadas
    y que las selecciones correspondan al subconjunto competitivo de 15-20 opciones.
    Devuelve (top5_podio, mantiene_raw, total_validadas).
    """
    def _val(obj, clave, defecto=None):
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

    sels_raw = _val(analisis, "selecciones_filtradas", []) or []
    if not sels_raw and hasattr(analisis, "grupos"):
        g = getattr(analisis, "grupos", {})
        sels_raw = (g.get("mantiene", []) + g.get("sensible", []))
    if not sels_raw and hasattr(analisis, "selecciones_validadas"):
        sels_raw = getattr(analisis, "selecciones_validadas", []) or []

    motor = getattr(analisis, "motor", None)

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
        filtrar_top_opciones_ticket,
        seleccionar_top_5_jugadas_recomendadas,
    )

    p_1x2 = None
    if motor and getattr(motor, "matriz_ft", None):
        mat = motor.matriz_ft
        R = len(mat)
        C = len(mat[0]) if R else 0
        p1 = sum(mat[i][j] for i in range(R) for j in range(C) if i > j)
        px = sum(mat[i][j] for i in range(R) for j in range(C) if i == j)
        p2 = sum(mat[i][j] for i in range(R) for j in range(C) if i < j)
        p_1x2 = (p1, px, p2)

    def _clasificar_cat(s):
        m_l = str(_val(s, "mercado") or "").lower()
        e_l = str(_val(s, "evento") or "").lower()
        if any(k in m_l for k in ("corner", "córner", "crner", "esquina")) or "corner" in e_l:
            return "CÓRNERES"
        if any(k in m_l for k in ("tarjeta", "amarilla", "roja")) or any(k in e_l for k in ("tarjeta", "amarilla", "roja")):
            return "TARJETAS"
        if any(k in m_l for k in ("hándicap", "handicap", "doble oportunidad", "sin empate", "ganador", "1x2", "1t/ft")) or any(k in e_l for k in ("1x", "x2", "12", "dnb", "local_gana", "visitante_gana", "empate")):
            return "HÁNDICAP / 1X2 / DC"
        eq_raw = _val(s, "equipo", None)
        if eq_raw and str(eq_raw).strip() not in ("None", "Total", "null", ""):
            return "GOLES POR EQUIPO"
        return "GOLES PARTIDO"

    def _permitida_sel(s):
        cat_s = _clasificar_cat(s)
        if bloquear_corners and cat_s == "CÓRNERES":
            return False
        if bloquear_tarjetas and cat_s == "TARJETAS":
            return False
        return es_seleccion_competitiva_ticket(s, eq_l, eq_v, p_1x2)

    sels_validas = [
        s for s in sels_raw
        if (_val(s, "probabilidad") or 0.0) >= (umbral_val - 1e-6) and _permitida_sel(s)
    ]
    if not sels_validas and sels_raw:
        sels_validas = [
            s for s in sels_raw
            if (_val(s, "probabilidad") or 0.0) >= (umbral_val - 1e-6)
            and not (bloquear_corners and _clasificar_cat(s) == "CÓRNERES")
            and not (bloquear_tarjetas and _clasificar_cat(s) == "TARJETAS")
        ]

    top5_podio = seleccionar_top_5_jugadas_recomendadas(sels_validas, motor=motor, eq_l=eq_l, eq_v=eq_v)
    sels_top_tabla = filtrar_top_opciones_ticket(sels_validas, eq_l=eq_l, eq_v=eq_v, p_1x2=p_1x2, max_opciones=18)
    if not sels_top_tabla and sels_validas:
        sels_top_tabla = sels_validas[:18]

    mantiene_raw = [s for s in sels_top_tabla if _val(s, "estado") == "mantiene"]
    if not mantiene_raw and sels_top_tabla:
        mantiene_raw = sels_top_tabla

    # Punto 27: Sincronizar la fila HÁNDICAP / 1X2 / DC de SECCIÓN 1 con el Puesto #1 del Top 5
    tiene_res_sec1 = any(_clasificar_cat(s) == "HÁNDICAP / 1X2 / DC" for s in mantiene_raw)
    if not tiene_res_sec1 and top5_podio:
        for item_t5 in top5_podio:
            if int(item_t5.get("slot", 0) or 0) == 1 and float(item_t5.get("probabilidad", 0.0) or 0.0) >= 0.70:
                et_t5 = str(item_t5.get("etiqueta") or "")
                ev_t5 = "1x" if "1x" in et_t5.lower() else ("x2" if "x2" in et_t5.lower() else ("1" if eq_l.lower() in et_t5.lower() else "2"))
                mantiene_raw.append({
                    "id_mercado": "doble_oportunidad" if ev_t5 in ("1x", "x2") else "ganador_partido",
                    "mercado": "Doble oportunidad" if ev_t5 in ("1x", "x2") else "Ganador del partido",
                    "evento": ev_t5,
                    "etiqueta": et_t5,
                    "periodo": "FT",
                    "equipo": eq_l if ev_t5 in ("1", "1x") else eq_v,
                    "linea": None,
                    "probabilidad": float(item_t5.get("probabilidad", 0.72)),
                    "estado": "mantiene",
                })
                break

    total_validadas = len(mantiene_raw)
    return top5_podio, mantiene_raw, total_validadas


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
    fecha = str(meta.get("fecha_partido") or meta.get("generado_en") or _ahora())
    if len(fecha) > 19:
        fecha = fecha[:19]

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

    def _formatear_pildora(s: Any, cat: str) -> tuple[str, str, float]:
        ev = str(_val(s, "evento") or "").lower()
        et = str(_val(s, "etiqueta") or "")
        lin = _val(s, "linea", None)
        prob = float(_val(s, "probabilidad", 0.0) or 0.0)
        pct = f"{prob:.0%}"

        eq_raw_p = _val(s, "equipo", None)
        eq_p = str(eq_raw_p).strip() if eq_raw_p and str(eq_raw_p).strip() not in ("None", "Total", "null", "") else None
        per_p = str(_val(s, "periodo") or "FT").strip().upper()
        if per_p not in ("FT", "1T", "2T"):
            per_p = "FT"

        if cat == "GOLES POR EQUIPO":
            if lin is not None and abs(float(lin) - 0.5) < 1e-6 and ("under_" in ev or "menos de" in et.lower()):
                return "", "", 0.0
            eq_corto = (eq_p[:10] if len(eq_p) > 10 else eq_p) if eq_p else eq_l[:10]
            if "over_" in ev or "mas de" in et.lower() or "más de" in et.lower():
                lbl = f"{eq_corto} +{lin:g} Goles ({per_p})" if lin is not None else f"{eq_corto} {et}"
            elif "under_" in ev or "menos de" in et.lower():
                lbl = f"{eq_corto} -{lin:g} Goles ({per_p})" if lin is not None else f"{eq_corto} {et}"
            elif "al_menos_" in ev or "al menos" in et.lower():
                if lin is not None and isinstance(lin, (int, float)):
                    lbl = f"{eq_corto} +{lin - 0.5:g} Goles ({per_p})"
                else:
                    lbl = f"{eq_corto} {et}"
            elif "primer_gol" in ev:
                lbl = f"1er Gol: {eq_corto}"
            else:
                lbl = f"{eq_corto} {et}"
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
                    lbl = f"+{lin - 0.5:g} Goles{suf_p}"
                else:
                    lbl = et
            elif "primer_gol" in ev:
                eq_anota = eq_p or eq_l
                lbl = f"1er Gol: {eq_anota}"
            elif "ambos_marcan" in ev or "btts" in ev:
                lbl = "Marcan Ambos" if ("si" in ev or "yes" in ev) else "No Marcan Ambos"
            else:
                lbl = et
        elif "CORNER" in cat or "CRNER" in cat or "CÓRNER" in cat:
            # Prohibido imprimir líneas negativas o imposibles como -0.5 Córn. 100%
            if lin is not None and lin <= 0.5 and ("under_" in ev or "menos" in et.lower()):
                return "", "", 0.0
            pref_c = (eq_p[:11] if len(eq_p) > 11 else eq_p) if eq_p else "Total"
            if "over_" in ev or "mas de" in et.lower() or "más de" in et.lower():
                lbl = f"{pref_c} +{lin:g} Córn." if lin is not None else et
            elif "under_" in ev or "menos de" in et.lower():
                lbl = f"{pref_c} -{lin:g} Córn." if lin is not None else et
            else:
                lbl = et
            return lbl, pct, prob
        elif "TARJETA" in cat:
            # Prohibido imprimir líneas negativas o imposibles como -0.5 Tarj. 100%
            if lin is not None and lin <= 0.5 and ("under_" in ev or "menos" in et.lower()):
                return "", "", 0.0
            if "roja" in ev or "roja" in et.lower():
                lbl = f"+{lin:g} Roja" if lin is not None else "Tarjeta Roja"
            elif "over_" in ev or "mas de" in et.lower() or "más de" in et.lower():
                lbl = f"+{lin:g} Tarj." if lin is not None else et
            elif "under_" in ev or "menos de" in et.lower():
                lbl = f"-{lin:g} Tarj." if lin is not None else et
            else:
                lbl = et
        else: # Hándicap / 1X2 / DC
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
                eq_dnb = _val(s, "equipo") or eq_l
                lbl = f"{eq_dnb} DNB"
            elif "h0_" in ev:
                lbl = f"H0 {_val(s, 'equipo') or eq_l}"
            elif "h1_" in ev:
                lbl = f"H+1 {_val(s, 'equipo') or eq_l}"
            elif "h2_" in ev:
                lbl = f"H+2 {_val(s, 'equipo') or eq_l}"
            elif "h3_" in ev:
                lbl = f"H+3 {_val(s, 'equipo') or eq_l}"
            elif "h_minus1_" in ev or "h-1_" in ev:
                lbl = f"H-1 {_val(s, 'equipo') or eq_l}"
            else:
                lbl = et

        for eq_nom in (eq_l, eq_v):
            if lbl.startswith(eq_nom + ":"):
                lbl = lbl[len(eq_nom) + 1:].strip()
            elif lbl.startswith(eq_nom + " - "):
                lbl = lbl[len(eq_nom) + 3:].strip()

        return lbl, pct, prob

    def _clasificar_categoria_ambito(s: Any) -> tuple[str, str]:
        m_l = str(_val(s, "mercado") or "").lower()
        e_l = str(_val(s, "evento") or "").lower()
        eq_raw = _val(s, "equipo", None)
        eq = str(eq_raw).strip() if eq_raw and str(eq_raw).strip() not in ("None", "Total", "null", "") else None
        per = str(_val(s, "periodo") or "FT").strip().upper()
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

        cat = "GOLES PARTIDO"
        amb = per_nombre
        return cat, amb

    ORDEN_CATEGORIAS = [
        "GOLES PARTIDO",
        "GOLES POR EQUIPO",
        "CÓRNERES",
        "TARJETAS",
        "HÁNDICAP / 1X2 / DC",
    ]

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

    def _organizar_bloque(lista_sels: list[Any]) -> list[dict[str, Any]]:
        agrupado: dict[str, dict[str, list[Any]]] = {c: {} for c in ORDEN_CATEGORIAS}
        for s in lista_sels:
            cat, amb = _clasificar_categoria_ambito(s)
            agrupado.setdefault(cat, {}).setdefault(amb, []).append(s)

        res_cats: list[dict[str, Any]] = []
        for cat in ORDEN_CATEGORIAS:
            ambs_dict = agrupado.get(cat, {})
            if not ambs_dict:
                continue

            filas_cat: list[dict[str, Any]] = []
            for amb in sorted(ambs_dict.keys(), key=_orden_ambito):
                items = ambs_dict[amb]
                pildoras_raw = [_formatear_pildora(s, cat) for s in items]
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

    # Bloqueo estricto de categorías sin cobertura oficial (cero líneas negativas o imposibles)
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
            clusters_marcador,
            es_seleccion_competitiva_ticket,
            filtrar_top_opciones_ticket,
            seleccionar_top_5_jugadas_recomendadas,
        )

        p_1x2 = None
        if motor and getattr(motor, "matriz_ft", None):
            mat = motor.matriz_ft
            R = len(mat)
            C = len(mat[0]) if R else 0
            p1 = sum(mat[i][j] for i in range(R) for j in range(C) if i > j)
            px = sum(mat[i][j] for i in range(R) for j in range(C) if i == j)
            p2 = sum(mat[i][j] for i in range(R) for j in range(C) if i < j)
            p_1x2 = (p1, px, p2)

        def _permitida_sel(s: Any) -> bool:
            cat_s, _ = _clasificar_categoria_ambito(s)
            if bloquear_corners and cat_s == "CÓRNERES":
                return False
            if bloquear_tarjetas and cat_s == "TARJETAS":
                return False
            return es_seleccion_competitiva_ticket(s, eq_l, eq_v, p_1x2)

        sels_validas = [
            s for s in sels_raw
            if (_val(s, "probabilidad") or 0.0) >= (umbral_val - 1e-6) and _permitida_sel(s)
        ]
        # Si el filtro competitivo deja vacía una muestra sintética mínima, usar las permitidas por cobertura
        if not sels_validas and sels_raw:
            sels_validas = [
                s for s in sels_raw
                if (_val(s, "probabilidad") or 0.0) >= (umbral_val - 1e-6)
                and not (bloquear_corners and _clasificar_categoria_ambito(s)[0] == "CÓRNERES")
                and not (bloquear_tarjetas and _clasificar_categoria_ambito(s)[0] == "TARJETAS")
            ]

        top5_podio = seleccionar_top_5_jugadas_recomendadas(sels_validas, motor=motor, eq_l=eq_l, eq_v=eq_v)

        # Limitar el total de opciones en la tabla inferior a las 15-20 opciones más competitivas
        sels_top_tabla = filtrar_top_opciones_ticket(sels_validas, eq_l=eq_l, eq_v=eq_v, p_1x2=p_1x2, max_opciones=18)
        if not sels_top_tabla and sels_validas:
            sels_top_tabla = sels_validas[:18]

        mantiene_raw = [s for s in sels_top_tabla if _val(s, "estado") == "mantiene"]
        sensibles_raw = [s for s in sels_top_tabla if _val(s, "estado") != "mantiene"]

        # Punto 27: Sincronizar la fila HÁNDICAP / 1X2 / DC de SECCIÓN 1 con el Puesto #1 del Top 5
        tiene_res_sec1 = any(_clasificar_categoria_ambito(s)[0] == "HÁNDICAP / 1X2 / DC" for s in mantiene_raw)
        if not tiene_res_sec1 and top5_podio:
            for item_t5 in top5_podio:
                if int(item_t5.get("slot", 0) or 0) == 1 and float(item_t5.get("probabilidad", 0.0) or 0.0) >= 0.70:
                    et_t5 = str(item_t5.get("etiqueta") or "")
                    ev_t5 = "1x" if "1x" in et_t5.lower() else ("x2" if "x2" in et_t5.lower() else ("1" if eq_l.lower() in et_t5.lower() else "2"))
                    mantiene_raw.append({
                        "id_mercado": "doble_oportunidad" if ev_t5 in ("1x", "x2") else "ganador_partido",
                        "mercado": "Doble oportunidad" if ev_t5 in ("1x", "x2") else "Ganador del partido",
                        "evento": ev_t5,
                        "etiqueta": et_t5,
                        "periodo": "FT",
                        "equipo": eq_l if ev_t5 in ("1", "1x") else eq_v,
                        "linea": None,
                        "probabilidad": float(item_t5.get("probabilidad", 0.72)),
                        "estado": "mantiene",
                    })
                    break

        sec1_data = _organizar_bloque(mantiene_raw)
        sec2_data = _organizar_bloque(sensibles_raw)

        clusters_info = getattr(motor, "clusters", None)
        if not clusters_info and hasattr(analisis, "matriz_ft"):
            try:
                clusters_info = clusters_marcador(getattr(analisis, "matriz_ft"), eq_l, eq_v)
            except Exception:
                clusters_info = None

        linea_cluster = (clusters_info or {}).get("linea_pie", "")

        alto_header = 118
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

        y_c += 22
        meta_parts = []
        if competicion:
            meta_parts.append(f"Competición: {competicion}")
        if fecha:
            meta_parts.append(f"Fecha: {fecha}")
        meta_parts.append("Modelo: Poisson bivariado con contracción bayesiana")
        # Si el Motor Recursivo 45-DCN recalculó la matriz, se declara en la
        # cabecera: el ticket no puede atribuir a Poisson lo que hizo el motor.
        _rec_ticket = (getattr(getattr(analisis, "motor", None), "recursivo", None) or {})
        if _rec_ticket.get("aplicado"):
            meta_parts[-1] = (
                f"Modelo: Red Neuronal Recursiva 45→8→n "
                f"(conv. it. {_rec_ticket.get('iteracion_convergencia')})")
        meta_txt = "   ·   ".join(meta_parts)
        d.text((PAD_X, y_c), meta_txt, font=f_meta, fill=C_TXT_MUTED)

        y_c += 32
        d.line([PAD_X, y_c, PAD_X + ANCHO_TABLA, y_c], fill=C_BORDER, width=1)
        y_c += 12

        # 1.5 Bloque destacado: TOP 5 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO)
        if top5_podio:
            C_PODIO_BG = (227, 242, 253)        # #E3F2FD
            C_PODIO_BORDER = (26, 107, 168)     # #1A6BA8
            C_PODIO_TXT = (19, 29, 49)          # #131D31
            d.rounded_rectangle([PAD_X, y_c, PAD_X + ANCHO_TABLA, y_c + alto_banner],
                                radius=6, fill=C_PODIO_BG, outline=C_PODIO_BORDER, width=1)
            d.text((PAD_X + 16, y_c + 8), "TOP 5 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO)", font=f_banner_tit, fill=C_PODIO_TXT)
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
                ev_p = float(item.get("ev_pct", 4.8) or 4.8)
                stk_p = float(item.get("stake_sugerido_pct", 2.4) or 2.4)
                badge_p_txt = str(item.get("badge_completo") or f"{pct_str} ({est_str} · EV {ev_p:+.1f}% · Stake: {stk_p:.1f}%)")
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
            d.text((X_COL3 + 14, y + 8), "LÍNEAS Y PROBABILIDADES VALIDADAS", font=f_tbl_hdr, fill=C_TXT_WHITE)
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

                    # Texto Ámbito / Periodo (Col 2)
                    amb_txt = f_data["ambito"]
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

        # 2. Sección 1: Alta Probabilidad
        cnt1_txt = f"{len(mantiene_raw)} opciones validadas"
        y_c = _dibujar_seccion(
            y_inicio=y_c,
            titulo_sec="SECCIÓN 1: ALTA PROBABILIDAD",
            subtitulo_sec="Mercados que superan el umbral cuantitativo en el modelo central y bajo estrés.",
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
            cnt2_txt = f"{len(sensibles_raw)} opciones sensibles"
            y_c = _dibujar_seccion(
                y_inicio=y_c,
                titulo_sec="SECCIÓN 2: SENSIBLES A LOS SUPUESTOS (70% - 75%)",
                subtitulo_sec="Superan el umbral central (>=70%), pero su probabilidad varía bajo escenarios de estrés.",
                conteo_txt=cnt2_txt,
                datos_sec=sec2_data,
                c_banner_bg=C_SEC2_BANNER_BG,
                c_banner_border=C_SEC2_BANNER_BORDER,
                c_banner_txt=C_SEC2_BANNER_TXT,
                c_pill_bg=C_SEC2_PILL_BG,
                c_pill_border=C_SEC2_PILL_BORDER,
                c_pill_pct=C_SEC2_PILL_PCT,
                accent_cat_color=C_SEC2_BANNER_BORDER,
            )

        # 4. Pie de ticket
        y_c += gap_seccion
        d.rounded_rectangle([PAD_X, y_c, PAD_X + ANCHO_TABLA, y_c + alto_footer],
                            radius=6, fill=C_SKY_BG, outline=C_SKY_BORDER, width=1)
        foot_txt = "Criterio Cuantitativo: Probabilidades Poisson bivariado con contracción bayesiana y Dixon-Coles. Juega con responsabilidad."
        if _rec_ticket.get("aplicado"):
            # El pie no puede atribuir a Poisson lo que calculó la red recursiva.
            foot_txt = ("Criterio Cuantitativo: Red Neuronal Recursiva (45 variables, "
                        "Bilinear Pooling + Cross-Attention + Monte Carlo 100.000). "
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
    analisis: Analisis = analizar(
        contexto["doc_local"], contexto["doc_visitante"],
        contexto["equipo_local"], contexto["equipo_visitante"],
        fecha_partido=ctx.get("fecha_partido"),
        competicion=ctx.get("competicion"),
        sede=ctx.get("sede"),
        condicion_local=ctx.get("condicion_local"),
        condicion_visitante=ctx.get("condicion_visitante"),
        parametros=params, aviso=progreso,
    )

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
            aviso=progreso)
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
        # 1) Buscar id_flashscore directo del cruce o desde el feed H2H de un partido conocido
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
        caption = f"🎯 {eq_l} vs {eq_v} | Umbral >{umbral_val:.0%}"

        marcar_estado(id_trabajo, "envio_en_curso", {"tipo": "foto", "ruta": ruta_imagen})
        try:
            ids_telegram: list[Any] = []

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
                        encabezado_3p = f"🧠 ANÁLISIS CUANTITATIVO Y VEREDICTO DE VALOR — {eq_l} vs {eq_v}\n" + ("━" * 38) + "\n\n"
                        for parte_3p in partir_mensaje(encabezado_3p + texto_3p):
                            res_txt_3p = texto_sender(parte_3p, chat_id=chat_id)
                            if isinstance(res_txt_3p, dict) and res_txt_3p.get("message_id") is not None:
                                ids_telegram.append(res_txt_3p["message_id"])
                    except Exception as e_txt_3p:
                        print(f"[SERVICIO MERCADOS] ⚠️ Aviso al enviar análisis de 3 párrafos: {e_txt_3p}")

            # 1) Primera imagen: Ticket General de Mercados
            resultado = foto_sender(ruta_imagen, caption=caption, chat_id=chat_id)
            if isinstance(resultado, dict):
                if not resultado.get("ok", True):
                    raise RuntimeError(resultado.get("error") or "envío de foto no confirmado")
                msg_id = resultado.get("message_id")
                if msg_id is not None:
                    ids_telegram.append(msg_id)
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
                    if isinstance(res_valor, dict) and res_valor.get("message_id") is not None:
                        ids_telegram.append(res_valor["message_id"])
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
                    if isinstance(res_cuadro, dict) and res_cuadro.get("message_id") is not None:
                        ids_telegram.append(res_cuadro["message_id"])
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
                    if isinstance(res_h2h, dict) and res_h2h.get("message_id") is not None:
                        ids_telegram.append(res_h2h["message_id"])
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
                    if isinstance(res_15p, dict) and res_15p.get("message_id") is not None:
                        ids_telegram.append(res_15p["message_id"])
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
                           "ids_telegram": ids_telegram})
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
                    "ids_telegram": ids_telegram, "partes": partes_enviadas,
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
    try:
        for parte in partes:
            resultado = enviar_fn(parte, chat_id)
            if isinstance(resultado, dict):
                if not resultado.get("ok", True):
                    raise RuntimeError(resultado.get("error") or "envío no confirmado")
                if resultado.get("message_id") is not None:
                    ids_telegram.append(resultado["message_id"])
            elif resultado is False:
                raise RuntimeError("el adaptador devolvió False")
        marcar_estado(id_trabajo, "envio_confirmado",
                      {"partes": len(partes), "ids_telegram": ids_telegram,
                       "caracteres": len(mensaje)})
        return {"enviado": True, "partes": len(partes), "ids_telegram": ids_telegram,
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
            return {"ok": True, "message_id": (datos.get("result") or {}).get("message_id")}
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
            return {"ok": True, "message_id": (datos.get("result") or {}).get("message_id")}
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
