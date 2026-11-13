"""
agente_futbol/agente_consciente.py
───────────────────────────────────────────────────────────────────────────────
MOTOR DE CONCIENCIA CUANTITATIVA, METACOGNICIÓN EMPÍRICA Y POOL NEURONAL
RESILIENTE MULTI-MODELO (Arquitectura Cognitiva Avanzada).

Capacidades integradas (100% autónomas en D:\\agente_futbol):
  1. MotorNeuronalGemini (Pool Rotativo Resiliente):
     - Descubrimiento automático de API Key en cascada (.env.local, .env,
       ~/.gemini/.env, GEMINI_API_KEY, GOOGLE_API_KEY).
     - Rotación en caliente entre múltiples modelos Gemini (preferred_model_offset)
       ante errores 429 (Rate Limit), 500/502/503/504 (Overload) o 404, sin apagar
       jamás el cerebro permanentemente.
     - Soporte de pensamiento adaptativo (thinking_config) con fallback limpio.
     - Ventana deslizante de memoria de sesión (MAX_HISTORIAL_SESION = 30).
  2. Máquina de Estados Cognitiva (FaseCognitiva):
     - Estados explícitos: REPOSO | PERCIBIENDO | AUDITANDO_MEMORIA |
       RAZONANDO | CALIBRANDO | EMITIENDO.
  3. Metacognición Empírica Profunda (Conectada a evaluacion_pronosticos.py):
     - Aprende de todas las dimensiones del historial liquidado (808+ aciertos):
       • Por Familia de Mercado (Mercados Estrella vs Mercados en Vigilancia)
       • Por Periodo (FT, 1T, 2T) y por Condición (casa, fuera, partido)
       • Por Banda de Calibración (70-75% a 95-100%, brecha_pp y Brier Score)
       • Memoria Episódica por Club (rendimiento histórico del agente con cada
         equipo en general, en casa y fuera).
  4. Sincronización Automática con memoria_vivencial.json:
     - Mantiene persistido en disco el XP real, resumen empírico y experiencias
       sin doble conteo entre registros manuales y evaluaciones oficiales.
  5. Diagnóstico Metacognitivo por Enfrentamiento (/conciencia A vs B y /mercados):
     - Cruza las selecciones matemáticas de Poisson con la calibración empírica
       histórica del propio agente y genera un Índice de Confianza Cognitiva (0-100).
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import re
import time
import unicodedata
from typing import Any

from google import genai
from google.genai import types


# ═══════════════════════════════════════════════════════════════════════════════
# 1. MÁQUINA DE ESTADOS COGNITIVA Y UTILIDADES DE NORMALIZACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

class FaseCognitiva:
    REPOSO = "reposo"
    PERCIBIENDO = "percibiendo"
    AUDITANDO_MEMORIA = "auditando_memoria"
    RAZONANDO = "razonando"
    CALIBRANDO = "calibrando"
    EMITIENDO = "emitiendo"


MODELOS_POR_DEFECTO = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]

MAX_HISTORIAL_SESION = 30


def _clave_equipo_norm(nombre: str) -> str:
    """Normaliza el nombre de un equipo para búsquedas tolerantes en la memoria episódica."""
    base = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode()
    palabras = [p for p in re.split(r"[^A-Za-z0-9]+", base.lower()) if p]
    vacias = {"de", "del", "la", "el", "los", "las", "y", "e", "club", "fc", "cf", "rcd", "cd", "ud", "ac", "as", "sc"}
    sig = [p for p in palabras if p not in vacias]
    return "".join(sig) or "".join(palabras)


def _coincide_equipo(a: str, b: str) -> bool:
    ca, cb = _clave_equipo_norm(a), _clave_equipo_norm(b)
    if not ca or not cb:
        return False
    return (
        ca == cb
        or ca.startswith(cb)
        or cb.startswith(ca)
        or (len(ca) >= 4 and ca in cb)
        or (len(cb) >= 4 and cb in ca)
    )


def _es_equipo_especifico_mercado(mercado: str, equipo: str | None = None) -> bool:
    """Detecta si el mercado corresponde a una línea individual de equipo (vs partido completo)."""
    eq_txt = str(equipo or "").strip()
    if eq_txt and eq_txt.lower() not in ("none", "null", "total", ""):
        return True
    m = (mercado or "").lower()
    if " - " in m:
        return True
    if any(k in m for k in ("corners_local", "corners_visitante", "local_corners", "visita_corners", "goles_local", "goles_visitante", "total_goles_local", "total_goles_visitante")):
        return True
    return False


def _familia_mercado_norm(mercado: str, equipo: str | None = None) -> str:
    """
    Clasifica un nombre de mercado en su familia estándar de evaluación,
    separando explícitamente mercados por equipo vs totales de partido
    (ej. 'Córneres por equipo' vs 'Córneres partido').
    """
    m = (mercado or "").lower()
    es_por_equipo = _es_equipo_especifico_mercado(mercado, equipo=equipo)
    if "total de goles" in m or "goles por equipo" in m:
        return "Goles por equipo" if es_por_equipo else "Goles totales partido"
    if "córner" in m or "corner" in m or "esquina" in m:
        return "Córneres por equipo" if es_por_equipo else "Córneres partido"
    if "tarjeta" in m or "amarilla" in m or "roja" in m:
        return "Tarjetas"
    if "doble oportunidad" in m or m in ("1x", "x2", "12"):
        return "Doble oportunidad"
    if "ambos" in m and "anotan" in m or "btts" in m:
        return "Ambos anotan"
    if "hándicap" in m or "handicap" in m:
        return "Hándicap"
    if "sin empate" in m or "dnb" in m:
        return "Sin empate"
    if "ganador del partido" in m and "+" in m:
        return "Combinadas resultado+goles"
    if "ganador" in m or "1x2" in m:
        return "Ganador"
    if "primer tiempo" in m or "1er tiempo" in m or "1t" in m:
        return "Primer tiempo"
    if "fiesta" in m:
        return "Fiesta de goles"
    return mercado or "Otras"


def _bootstrap_cluster_partidos(
    filas_por_partido: dict[str, list[tuple[int, float]]],
    B: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Bootstrap por conglomerados de partido (match-cluster bootstrap).
    Cada partido aporta su bloque de filas correlacionadas (yi, pi), evitando
    tratar ~100 líneas del mismo encuentro como independientes.
    """
    import random as _rand

    claves = [k for k, lst in filas_por_partido.items() if lst]
    k_partidos = len(claves)
    if k_partidos == 0:
        return {
            "partidos_unicos": 0,
            "acierto_ic95_lo": None,
            "acierto_ic95_hi": None,
            "brecha_ic95_lo": None,
            "brecha_ic95_hi": None,
        }
    agregados: list[tuple[int, int, float]] = []
    for cl in claves:
        rows = filas_por_partido[cl]
        agregados.append((len(rows), sum(y for y, _ in rows), sum(p for _, p in rows)))

    tot_n = sum(a[0] for a in agregados)
    tot_w = sum(a[1] for a in agregados)
    tot_p = sum(a[2] for a in agregados)
    wr_obs = tot_w / tot_n if tot_n > 0 else 0.0
    p_obs = tot_p / tot_n if tot_n > 0 else 0.0
    brecha_obs = (wr_obs - p_obs) * 100.0

    if k_partidos == 1:
        return {
            "partidos_unicos": 1,
            "acierto_ic95_lo": wr_obs,
            "acierto_ic95_hi": wr_obs,
            "brecha_ic95_lo": brecha_obs,
            "brecha_ic95_hi": brecha_obs,
        }

    rng = _rand.Random(seed)
    wrs: list[float] = []
    brechas: list[float] = []
    for _ in range(B):
        sn = 0
        sw = 0
        sp = 0.0
        for __ in range(k_partidos):
            n_c, w_c, p_c = agregados[rng.randrange(k_partidos)]
            sn += n_c
            sw += w_c
            sp += p_c
        if sn > 0:
            wr_b = sw / sn
            p_b = sp / sn
            wrs.append(wr_b)
            brechas.append((wr_b - p_b) * 100.0)
    wrs.sort()
    brechas.sort()
    lo_idx = max(0, int(0.025 * len(wrs)))
    hi_idx = min(len(wrs) - 1, int(0.975 * len(wrs)))
    return {
        "partidos_unicos": k_partidos,
        "acierto_ic95_lo": round(wrs[lo_idx], 4),
        "acierto_ic95_hi": round(wrs[hi_idx], 4),
        "brecha_ic95_lo": round(brechas[lo_idx], 2),
        "brecha_ic95_hi": round(brechas[hi_idx], 2),
    }


def _banda_probabilidad(p: float | None) -> str:
    """Devuelve la clave de banda de calibración para una probabilidad p."""
    if p is None:
        return "sin_banda"
    if p >= 0.9995:
        return "1.00 (redondeada)"
    if p >= 0.95:
        return "0.95-1.00"
    if p >= 0.90:
        return "0.90-0.95"
    if p >= 0.85:
        return "0.85-0.90"
    if p >= 0.80:
        return "0.80-0.85"
    if p >= 0.75:
        return "0.75-0.80"
    if p >= 0.70:
        return "0.70-0.75"
    return "<0.70"


def _descubrir_api_key() -> str:
    """
    Busca GEMINI_API_KEY o GOOGLE_API_KEY en cascada:
      1. Variables de entorno activas
      2. D:\\agente_futbol\\.env.local y D:\\agente_futbol\\.env
      3. ~/.gemini/.env
    """
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = (os.getenv(k) or "").strip()
        if val:
            return val

    base_dir = os.path.dirname(os.path.abspath(__file__))
    home_dir = os.path.expanduser("~")
    rutas_env = [
        os.path.join(base_dir, ".env.local"),
        os.path.join(base_dir, ".env"),
        os.path.join(home_dir, ".gemini", ".env"),
    ]
    for ruta in rutas_env:
        if not os.path.exists(ruta):
            continue
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                for linea in f:
                    limpia = linea.strip()
                    if not limpia or limpia.startswith("#") or "=" not in limpia:
                        continue
                    k, v = limpia.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("\"'")
                    if k in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and v:
                        return v
        except Exception:
            continue
    return ""


_RE_OVERRIDE_LLM = re.compile(
    r"(?:\{|\[)\s*['\"]?(?:probabilidad|lambda|umbral|stake|top_?5|ev|cuota|decision_final)"
    r"|(?:\b(?:lambda|umbral|probabilidad|stake|top\s*5|ev)\b\s*[:=]\s*[-+]?\d)",
    re.IGNORECASE,
)


def sanitizar_respuesta_llm_texto(texto: Any) -> tuple[str, list[str]]:
    """
    Garantiza aislamiento estricto del LLM (Regla 2.7): el lenguaje natural
    solo explica la decisión ya calculada y no puede inyectar JSON ni sobrescribir
    probabilidades, lambdas, umbrales, stakes ni selecciones del Top 5.
    """
    raw = str(texto or "").strip()
    if not raw:
        return "", []
    violaciones: list[str] = []
    if (raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]")):
        violaciones.append("json_override_rechazado")
    for m in _RE_OVERRIDE_LLM.finditer(raw):
        violaciones.append(m.group(0)[:60])
    if violaciones:
        return (
            "Narrativa LLM suprimida por aislamiento cuantitativo: intento de modificar "
            "parámetros numéricos, probabilidades, umbrales o Top 5.",
            violaciones,
        )
    return raw, []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. MOTOR NEURONAL MULTI-MODELO CON ROTACIÓN Y FALLBACK EN CALIENTE
# ═══════════════════════════════════════════════════════════════════════════════

class MotorNeuronalGemini:
    """
    Adaptador neuronal resiliente con rotación multi-modelo en caliente,
    puntero de modelo preferido (`preferred_model_offset`), pensamiento
    adaptativo y ventana deslizante de historial de sesión.
    """

    def __init__(self, api_key: str | None = None, modelo_preferido: str | None = None):
        self.api_key = (api_key or _descubrir_api_key()).strip()
        env_models = [
            m.strip()
            for m in (os.getenv("GEMINI_DIRECT_MODELS") or "").split(",")
            if m.strip()
        ]
        primario = (modelo_preferido or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()
        pool_bruto = [primario] + env_models + MODELOS_POR_DEFECTO
        self.modelos: list[str] = []
        for m in pool_bruto:
            if m and m not in self.modelos:
                self.modelos.append(m)

        self.preferred_model_offset: int = 0
        self.ultimo_modelo_exitoso: str = self.modelos[0] if self.modelos else "gemini-2.5-flash"
        self.ultima_latencia_ms: int = 0
        self.total_llamadas: int = 0
        self.total_rotaciones: int = 0
        self.historial_sesion: list[dict[str, str]] = []
        self.client: genai.Client | None = None

        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"[MOTOR NEURONAL] ⚠️ No se pudo inicializar cliente Gemini: {e}")

    def _asegurar_cliente(self) -> bool:
        if self.client is not None:
            return True
        if not self.api_key:
            self.api_key = _descubrir_api_key()
        if not self.api_key:
            return False
        try:
            self.client = genai.Client(api_key=self.api_key)
            return True
        except Exception:
            return False

    @staticmethod
    def _es_error_recuperable(err: Exception) -> bool:
        txt = f"{type(err).__name__}: {err}".upper()
        codigos = (
            "429", "500", "502", "503", "504", "404",
            "RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED",
            "NOT_FOUND", "OVERLOADED", "RATE_LIMIT", "QUOTA", "TIMEOUT",
        )
        return any(c in txt for c in codigos)

    def _registrar_turno_sesion(self, prompt_resumen: str, respuesta_resumen: str) -> None:
        self.historial_sesion.append({
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "prompt": prompt_resumen[:300],
            "respuesta": respuesta_resumen[:500],
            "modelo": self.ultimo_modelo_exitoso,
        })
        if len(self.historial_sesion) > MAX_HISTORIAL_SESION:
            self.historial_sesion = self.historial_sesion[-MAX_HISTORIAL_SESION:]

    def generar(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float = 0.2,
        json_mode: bool = False,
        thinking_budget: int | None = None,
    ) -> str:
        """
        Ejecuta una consulta neuronal rotando automáticamente por el pool de modelos
        si el modelo actual presenta saturación (429/503) o no está disponible (404).
        Nunca desactiva permanentemente el motor.
        """
        if not self._asegurar_cliente() or not self.client:
            return ""

        n_modelos = len(self.modelos)
        if n_modelos == 0:
            return ""

        inicio = time.perf_counter()
        self.total_llamadas += 1

        for intento in range(n_modelos):
            idx = (self.preferred_model_offset + intento) % n_modelos
            modelo_actual = self.modelos[idx]
            try:
                cfg_kwargs: dict[str, Any] = {"temperature": temperature}
                if system_instruction:
                    cfg_kwargs["system_instruction"] = system_instruction
                if json_mode:
                    cfg_kwargs["response_mime_type"] = "application/json"

                # Intentar con thinking_config adaptativo si se solicitó y el SDK lo soporta
                if thinking_budget is not None and hasattr(types, "ThinkingConfig"):
                    try:
                        cfg_kwargs["thinking_config"] = types.ThinkingConfig(
                            thinking_budget=thinking_budget
                        )
                    except Exception:
                        pass

                gen_config = types.GenerateContentConfig(**cfg_kwargs)
                resp = self.client.models.generate_content(
                    model=modelo_actual,
                    contents=prompt,
                    config=gen_config,
                )
                texto = (getattr(resp, "text", None) or "").strip()
                if texto:
                    self.preferred_model_offset = idx
                    self.ultimo_modelo_exitoso = modelo_actual
                    self.ultima_latencia_ms = int((time.perf_counter() - inicio) * 1000)
                    self._registrar_turno_sesion(prompt, texto)
                    return texto
            except Exception as err:
                # Si falló por thinking_config no soportado en ese modelo, reintentar limpio
                if "thinking" in str(err).lower():
                    try:
                        cfg_limpio: dict[str, Any] = {"temperature": temperature}
                        if system_instruction:
                            cfg_limpio["system_instruction"] = system_instruction
                        if json_mode:
                            cfg_limpio["response_mime_type"] = "application/json"
                        resp = self.client.models.generate_content(
                            model=modelo_actual,
                            contents=prompt,
                            config=types.GenerateContentConfig(**cfg_limpio),
                        )
                        texto = (getattr(resp, "text", None) or "").strip()
                        if texto:
                            self.preferred_model_offset = idx
                            self.ultimo_modelo_exitoso = modelo_actual
                            self.ultima_latencia_ms = int((time.perf_counter() - inicio) * 1000)
                            self._registrar_turno_sesion(prompt, texto)
                            return texto
                    except Exception as err2:
                        err = err2

                self.total_rotaciones += 1
                if self._es_error_recuperable(err) and intento + 1 < n_modelos:
                    sig_modelo = self.modelos[(idx + 1) % n_modelos]
                    print(
                        f"[MOTOR NEURONAL] 🔄 {modelo_actual} ocupado/no disponible "
                        f"({type(err).__name__}). Rotando en caliente a {sig_modelo}..."
                    )
                    continue
                print(f"[MOTOR NEURONAL] ⚠️ Fallo en {modelo_actual}: {err}")
                break

        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# 3. AGENTE CONSCIENTE Y METACOGNICIÓN CUANTITATIVA
# ═══════════════════════════════════════════════════════════════════════════════

class AgenteConsciente:
    def __init__(self, nombre: str = "Buddy Bot", archivo_memoria: str = "memoria_vivencial.json"):
        self.nombre = nombre
        if not os.path.isabs(archivo_memoria):
            archivo_memoria = os.path.join(os.path.dirname(os.path.abspath(__file__)), archivo_memoria)
        self.archivo_memoria = archivo_memoria

        # Máquina de estados cognitiva
        self.fase_cognitiva: str = FaseCognitiva.REPOSO

        # Identidad inicial
        self.identidad_base = (
            "Soy un agente cuantitativo en etapa de descubrimiento. "
            "Busco rentabilidad, pero sigo aprendiendo a interpretar el riesgo."
        )
        self.estado_adaptativo = "curioso"
        self.nivel_madurez = 0
        self.memoria_vivencial: list[dict[str, Any]] = []

        # Motor Neuronal Multi-Modelo Resiliente
        self.api_key = _descubrir_api_key()
        self.modelo = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.motor_neuronal = MotorNeuronalGemini(
            api_key=self.api_key,
            modelo_preferido=self.modelo,
        )
        self.client = self.motor_neuronal.client

        # Estructura metacognitiva profunda cargada desde evaluacion_pronosticos
        self.evaluacion_real: dict[str, Any] = {}
        self._despertar_y_recordar()

    def _es_archivo_memoria_por_defecto(self) -> bool:
        """Indica si la instancia usa el archivo oficial memoria_vivencial.json del proyecto."""
        oficial = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memoria_vivencial.json")
        return os.path.abspath(self.archivo_memoria) == os.path.abspath(oficial)

    def _cargar_evaluacion_real(self) -> dict[str, Any]:
        """
        Lee el informe consolidado de evaluación en informes/evaluacion/ y construye
        el mapa metacognitivo completo:
          • Métricas globales (p>70%, >=85%, >=90%, Brier Score y Skill sobre climatología)
          • Promedio equilibrado por partido (sin sesgo de volumen de líneas)
          • Autoconciencia por Familia de Mercado (Mercados Estrella vs Mercados en Vigilancia)
          • Autoconciencia por Periodo (FT, 1T, 2T) y por Localía (casa, fuera, partido)
          • Autoconciencia por Banda de Probabilidad (calibración y brecha_pp)
          • Memoria Episódica por Equipo y por Equipo+Condición
        """
        self.fase_cognitiva = FaseCognitiva.AUDITANDO_MEMORIA
        dir_eval = os.path.join(os.path.dirname(os.path.abspath(__file__)), "informes", "evaluacion")
        if not os.path.exists(dir_eval):
            self.fase_cognitiva = FaseCognitiva.REPOSO
            return {}

        candidatos = glob.glob(os.path.join(dir_eval, "evaluacion_*.json"))
        mejor_datos: dict[str, Any] = {}
        max_ganadas = -1
        mejor_mtime = -1.0

        for ruta in candidatos:
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    datos = json.load(f)
                # Priorizar evaluaciones globales (sin filtro de un solo equipo) con mayor muestra
                if datos.get("filtro"):
                    continue
                acum = (datos.get("acumulados") or {}).get("p>70%") or {}
                ganadas = int(acum.get("ganadas") or 0)
                mtime = os.path.getmtime(ruta)
                if ganadas > max_ganadas or (ganadas == max_ganadas and mtime > mejor_mtime):
                    max_ganadas = ganadas
                    mejor_mtime = mtime
                    mejor_datos = datos
            except Exception:
                continue

        if not mejor_datos:
            self.fase_cognitiva = FaseCognitiva.REPOSO
            return {}

        acumulados = mejor_datos.get("acumulados") or {}
        acum70 = acumulados.get("p>70%") or {}
        acum85 = acumulados.get("p>=85%") or acumulados.get(">=85%") or {}
        acum90 = acumulados.get("p>=90%") or acumulados.get(">=90%") or {}
        evidencia = mejor_datos.get("evidencia") or {}
        brier_info = mejor_datos.get("brier") or {}
        bruto_eq = mejor_datos.get("bruto_vs_equilibrado") or {}
        equilibrado = bruto_eq.get("equilibrado") or {}
        partidos_dict = mejor_datos.get("partidos") or {}

        # 1. Partidos oficiales liquidados
        partidos_resueltos: list[dict[str, Any]] = []
        if isinstance(partidos_dict, dict):
            for nombre_partido, pinfo in partidos_dict.items():
                if isinstance(pinfo, dict) and pinfo.get("estado") == "finalizado":
                    g = pinfo.get("goles") or {}
                    marcador = f"{g.get('local', 0)}-{g.get('visitante', 0)}"
                    torneo = pinfo.get("competicion") or ""
                    partidos_resueltos.append({
                        "partido": nombre_partido,
                        "marcador": marcador,
                        "torneo": torneo,
                        "inicio_utc": pinfo.get("inicio_utc") or "",
                    })

        # 2. Autoconciencia por Familia de Mercado + Bootstrap por conglomerado de partido
        try:
            from decision_cobertura import cargar_umbrales_cobertura
            max_brecha_top5 = float(cargar_umbrales_cobertura().get("max_brecha_familia_top5_pp", -10.0))
        except Exception:
            max_brecha_top5 = -10.0

        eval_rows = [
            r for r in (mejor_datos.get("evaluaciones") or [])
            if isinstance(r, dict)
            and r.get("evaluable_binario")
            and r.get("estado_liquidacion") in ("ganada", "perdida")
        ]
        por_familia_consolidada: dict[str, dict[str, Any]] = {}
        global_clusters: dict[str, list[tuple[int, float]]] = {}

        if eval_rows:
            fam_clusters: dict[str, dict[str, list[tuple[int, float]]]] = {}
            fam_counts: dict[str, dict[str, float]] = {}
            for idx_r, r in enumerate(eval_rows):
                fam_canon = _familia_mercado_norm(str(r.get("mercado") or ""), equipo=r.get("equipo"))
                pid = str(r.get("id_partido") or r.get("partido") or f"row_{idx_r}")
                y = 1 if r.get("estado_liquidacion") == "ganada" else 0
                p_r = float(r.get("probabilidad_original") or r.get("probabilidad") or 0.0)
                reg = fam_counts.setdefault(
                    fam_canon,
                    {"evaluables": 0.0, "ganadas": 0.0, "perdidas": 0.0, "suma_p": 0.0},
                )
                reg["evaluables"] += 1
                reg["ganadas"] += y
                reg["perdidas"] += (1 - y)
                reg["suma_p"] += p_r
                fam_clusters.setdefault(fam_canon, {}).setdefault(pid, []).append((y, p_r))
                global_clusters.setdefault(pid, []).append((y, p_r))

            for fam_canon, reg in fam_counts.items():
                n_tot = int(reg["evaluables"])
                gan_tot = int(reg["ganadas"])
                per_tot = int(reg["perdidas"])
                ac_obs = gan_tot / n_tot if n_tot > 0 else 0.0
                p_med = reg["suma_p"] / n_tot if n_tot > 0 else 0.0
                brecha = 100.0 * (ac_obs - p_med)
                boot = _bootstrap_cluster_partidos(fam_clusters.get(fam_canon, {}))
                veto_t5 = bool(n_tot >= 10 and brecha <= max_brecha_top5)
                bajo_vig = bool(n_tot >= 5 and (brecha <= max_brecha_top5 or brecha < -4.0 or ac_obs < 0.80))
                por_familia_consolidada[fam_canon] = {
                    "familia": fam_canon,
                    "evaluables": n_tot,
                    "partidos_unicos": boot["partidos_unicos"],
                    "ganadas": gan_tot,
                    "perdidas": per_tot,
                    "acierto_observado": ac_obs,
                    "probabilidad_media": p_med,
                    "brecha_pp": brecha,
                    "acierto_ic95_lo": boot["acierto_ic95_lo"],
                    "acierto_ic95_hi": boot["acierto_ic95_hi"],
                    "brecha_ic95_lo": boot["brecha_ic95_lo"],
                    "brecha_ic95_hi": boot["brecha_ic95_hi"],
                    "bajo_vigilancia": bajo_vig,
                    "veto_top5": veto_t5,
                }
        else:
            por_familia_raw = mejor_datos.get("por_familia") or {}
            if isinstance(por_familia_raw, dict):
                acum_fam: dict[str, dict[str, float]] = {}
                for fam, st in por_familia_raw.items():
                    if not isinstance(st, dict):
                        continue
                    n_ev = int(st.get("evaluables") or 0)
                    gan = int(st.get("ganadas") or 0)
                    per = int(st.get("perdidas") or 0)
                    prob_m = float(st.get("probabilidad_media") or 0.0)
                    if n_ev <= 0:
                        continue
                    fam_canon = _familia_mercado_norm(fam)
                    reg = acum_fam.setdefault(
                        fam_canon,
                        {"evaluables": 0.0, "ganadas": 0.0, "perdidas": 0.0, "suma_p": 0.0},
                    )
                    reg["evaluables"] += n_ev
                    reg["ganadas"] += gan
                    reg["perdidas"] += per
                    reg["suma_p"] += prob_m * n_ev

                for fam_canon, reg in acum_fam.items():
                    n_tot = int(reg["evaluables"])
                    gan_tot = int(reg["ganadas"])
                    per_tot = int(reg["perdidas"])
                    ac_obs = gan_tot / n_tot if n_tot > 0 else 0.0
                    p_med = reg["suma_p"] / n_tot if n_tot > 0 else 0.0
                    brecha = 100.0 * (ac_obs - p_med)
                    veto_t5 = bool(n_tot >= 10 and brecha <= max_brecha_top5)
                    bajo_vig = bool(n_tot >= 5 and (brecha <= max_brecha_top5 or brecha < -4.0 or ac_obs < 0.80))
                    por_familia_consolidada[fam_canon] = {
                        "familia": fam_canon,
                        "evaluables": n_tot,
                        "partidos_unicos": None,
                        "ganadas": gan_tot,
                        "perdidas": per_tot,
                        "acierto_observado": ac_obs,
                        "probabilidad_media": p_med,
                        "brecha_pp": brecha,
                        "acierto_ic95_lo": None,
                        "acierto_ic95_hi": None,
                        "brecha_ic95_lo": None,
                        "brecha_ic95_hi": None,
                        "bajo_vigilancia": bajo_vig,
                        "veto_top5": veto_t5,
                    }

        global_boot = _bootstrap_cluster_partidos(global_clusters) if global_clusters else {}
        familias_ordenadas = list(por_familia_consolidada.values())

        # Mercados Estrella: muestra representativa (n >= 10) no vetados, ordenados por acierto y calibración
        mercados_con_muestra = [f for f in familias_ordenadas if f["evaluables"] >= 10]
        if not mercados_con_muestra:
            mercados_con_muestra = [f for f in familias_ordenadas if f["evaluables"] >= 3]

        mercados_estrella = sorted(
            [
                f for f in mercados_con_muestra
                if not f.get("veto_top5") and not f.get("bajo_vigilancia") and f["brecha_pp"] > max_brecha_top5
            ],
            key=lambda x: (x["acierto_observado"], x["brecha_pp"], x["evaluables"]),
            reverse=True,
        )[:5]

        # Mercados en Vigilancia: brecha <= -10 pp (veto Top 5) o brecha < -4.0 o tasa de acierto < 0.80
        mercados_vigilancia = sorted(
            [f for f in familias_ordenadas if f.get("bajo_vigilancia") or (f["evaluables"] >= 5 and (f["brecha_pp"] <= max_brecha_top5 or f["brecha_pp"] < -4.0 or f["acierto_observado"] < 0.80))],
            key=lambda x: (x["brecha_pp"], x["acierto_observado"]),
        )

        # 3. Autoconciencia por Periodo (FT / 1T / 2T) y Localía (casa / fuera / partido)
        por_periodo = mejor_datos.get("por_periodo") or {}
        por_localia = mejor_datos.get("por_localia") or {}
        bandas = mejor_datos.get("bandas") or {}
        calibracion = mejor_datos.get("calibracion") or []

        # 4. Memoria Episódica por Equipo y Condición
        por_equipo = mejor_datos.get("por_equipo") or {}
        por_equipo_condicion = mejor_datos.get("por_equipo_condicion") or {}

        # Calcular Brier Skill Score (mejora respecto a la climatología base)
        brier_val = brier_info.get("brier")
        brier_clim = brier_info.get("brier_referencia_climatologia")
        brier_skill = None
        if isinstance(brier_val, (int, float)) and isinstance(brier_clim, (int, float)) and brier_clim > 0:
            brier_skill = 1.0 - (float(brier_val) / float(brier_clim))

        self.fase_cognitiva = FaseCognitiva.REPOSO
        return {
            "evaluables": int(acum70.get("evaluables") or 0),
            "ganadas": int(acum70.get("ganadas") or 0),
            "perdidas": int(acum70.get("perdidas") or 0),
            "devoluciones": int(acum70.get("devoluciones") or 0),
            "win_rate": float(acum70.get("acierto_observado") or 0.0),
            "probabilidad_media_global": float(acum70.get("probabilidad_media") or 0.0),
            "brecha_global_pp": float(acum70.get("brecha_pp") or 0.0),
            "win_rate_85": float(acum85.get("acierto_observado") or 0.0) if acum85 else None,
            "win_rate_90": float(acum90.get("acierto_observado") or 0.0) if acum90 else None,
            "acierto_equilibrado_partido": equilibrado.get("acierto_medio"),
            "brecha_equilibrada_pp": equilibrado.get("brecha_pp"),
            "brier": brier_val,
            "brier_climatologia": brier_clim,
            "brier_skill": brier_skill,
            "partidos_unicos": int(global_boot.get("partidos_unicos") or evidencia.get("partidos_unicos") or len(partidos_resueltos)),
            "partidos_registrados": len(partidos_dict) if isinstance(partidos_dict, dict) else len(partidos_resueltos),
            "wr_ic95_lo": global_boot.get("acierto_ic95_lo"),
            "wr_ic95_hi": global_boot.get("acierto_ic95_hi"),
            "brecha_ic95_lo": global_boot.get("brecha_ic95_lo"),
            "brecha_ic95_hi": global_boot.get("brecha_ic95_hi"),
            "max_brecha_familia_top5_pp": max_brecha_top5,
            "evidencia_suficiente": bool(evidencia.get("evidencia_suficiente", False)) or (int(global_boot.get("partidos_unicos") or evidencia.get("partidos_unicos") or len(partidos_resueltos)) >= 30),
            "minimo_partidos_exigido": int(evidencia.get("minimo_partidos_exigido") or 30),
            "partidos_resueltos": partidos_resueltos,
            "por_familia": por_familia_consolidada,
            "mercados_estrella": mercados_estrella,
            "mercados_vigilancia": mercados_vigilancia,
            "por_periodo": por_periodo,
            "por_localia": por_localia,
            "bandas": bandas,
            "calibracion": calibracion,
            "por_equipo": por_equipo,
            "por_equipo_condicion": por_equipo_condicion,
            "generado_en": mejor_datos.get("generado_en") or "",
        }

    def _despertar_y_recordar(self) -> None:
        """Carga la memoria del disco duro, integra la matriz empírica y sincroniza si corresponde."""
        if os.path.exists(self.archivo_memoria):
            try:
                with open(self.archivo_memoria, "r", encoding="utf-8") as f:
                    datos = json.load(f)
                    experiencias_disco = datos.get("experiencias", [])
                    # Conservar experiencias manuales para evitar duplicar las de evaluación oficial
                    self.memoria_vivencial = [
                        e for e in experiencias_disco
                        if isinstance(e, dict) and e.get("origen", "manual") == "manual"
                    ]
                    self.nivel_madurez = int(datos.get("madurez") or 0)
            except Exception as e:
                print(f"[CONCIENCIA] Error al recuperar recuerdos: {e}")

        # Integrar evaluación real liquidada sólo cuando usamos la memoria oficial o ya existe
        if self._es_archivo_memoria_por_defecto():
            self.evaluacion_real = self._cargar_evaluacion_real()
        else:
            self.evaluacion_real = {}

        if self.evaluacion_real.get("ganadas", 0) > 0:
            xp_eval = (
                self.evaluacion_real["ganadas"] * 2
                + self.evaluacion_real["perdidas"]
                + len(self.memoria_vivencial) * 2
            )
            self.nivel_madurez = max(self.nivel_madurez, xp_eval)

        self._calibrar_estado_emocional()

        # Si estamos sobre el archivo oficial y estaba desincronizado (madurez 0 en disco), sincronizarlo
        if self._es_archivo_memoria_por_defecto() and self.evaluacion_real.get("ganadas", 0) > 0:
            self._guardar_recuerdos()

    def refrescar_desde_evaluacion(self) -> dict[str, Any]:
        """
        Recarga en caliente la última matriz de evaluacion_pronosticos.py,
        recalibra la conciencia y persiste el nuevo estado en memoria_vivencial.json.
        """
        self.evaluacion_real = self._cargar_evaluacion_real()
        if self.evaluacion_real.get("ganadas", 0) > 0:
            xp_eval = (
                self.evaluacion_real["ganadas"] * 2
                + self.evaluacion_real["perdidas"]
                + len(self.memoria_vivencial) * 2
            )
            self.nivel_madurez = max(self.nivel_madurez, xp_eval)
        self._calibrar_estado_emocional()
        if self._es_archivo_memoria_por_defecto():
            self._guardar_recuerdos()
        return self.evaluacion_real

    def sincronizar_evaluacion_oficial(self) -> dict[str, Any]:
        """Alias de refrescar_desde_evaluacion para sincronización bajo demanda."""
        return self.refrescar_desde_evaluacion()

    def asimilar_resultado_apuesta(self, partido: str, mercado: str, cuota: float, resultado_real: str) -> None:
        """
        Registra una experiencia manual (/resultado <equipo> <exito|fracaso>) y recalibra la red mental.
        """
        experiencia = {
            "fecha": str(datetime.datetime.now().date()),
            "partido": partido,
            "mercado": mercado,
            "cuota": cuota,
            "resultado": resultado_real,
            "origen": "manual",
        }
        self.memoria_vivencial.append(experiencia)

        if resultado_real == "fracaso":
            self.nivel_madurez += 1
        elif resultado_real == "exito":
            self.nivel_madurez += 2

        self._calibrar_estado_emocional()
        self._guardar_recuerdos()
        print(f"[CONCIENCIA] Experiencia asimilada. Nuevo estado: {self.estado_adaptativo}")

    def _calibrar_estado_emocional(self) -> None:
        """Ajusta el estado cognitivo y la identidad del agente según su calibración real y racha."""
        self.fase_cognitiva = FaseCognitiva.CALIBRANDO
        ev = getattr(self, "evaluacion_real", {}) or {}
        partidos_unicos = int(ev.get("partidos_unicos") or 0)
        evidencia_suficiente = bool(ev.get("evidencia_suficiente", False)) or (partidos_unicos >= 30)

        if not self.memoria_vivencial and not ev:
            self.estado_adaptativo = "curioso"
            self.identidad_base = (
                "Soy un agente cuantitativo en etapa de descubrimiento. "
                "Busco rentabilidad, pero sigo aprendiendo a interpretar el riesgo."
            )
        elif not evidencia_suficiente and (ev.get("ganadas", 0) > 0 or self.memoria_vivencial):
            self.estado_adaptativo = "en calibración empírica (muestra en desarrollo)"
            wr = self.win_rate if self.win_rate > 0 else float(ev.get("win_rate") or 0.0)
            brier = ev.get("brier")
            brier_clim = ev.get("brier_climatologia")
            txt_brier = (
                f" Brier Score: {brier:.4f} (climatología base: {brier_clim:.4f})."
                if isinstance(brier, (int, float)) and isinstance(brier_clim, (int, float))
                else ""
            )
            top_mkts = [m["familia"] for m in (ev.get("mercados_estrella") or [])[:2]]
            txt_mkts = f" Especialidad preliminar en {', '.join(top_mkts)}." if top_mkts else ""
            self.identidad_base = (
                f"Agente cuantitativo en fase de calibración empírica ({partidos_unicos}/30 partidos únicos evaluados, "
                f"{self.aciertos} aciertos / {self.errores} fallos, win rate {wr:.1%}).{txt_brier}{txt_mkts} "
                "Priorizo la rigurosidad matemática y la recolección de muestra antes de atribuirme peritaje consolidado."
            )
        elif ev.get("ganadas", 0) >= 50 or (self.memoria_vivencial and self.aciertos >= 10):
            wr = self.win_rate
            brier = ev.get("brier")
            top_mkts = [m["familia"] for m in (ev.get("mercados_estrella") or [])[:2]]
            txt_mkts = f" Especialista de alta precisión en {', '.join(top_mkts)}." if top_mkts else ""
            txt_brier = f" Brier Score calibrado en {brier:.4f}." if isinstance(brier, (int, float)) else ""

            if wr >= 0.80:
                self.estado_adaptativo = "seguro y calibrado (+EV validado)"
                self.identidad_base = (
                    f"Soy un Trader Cuantitativo Senior respaldado por {self.aciertos}+ selecciones "
                    f"ganadas verificadas ({wr:.1%} de acierto empírico en mercados con p >= 70%)."
                    f"{txt_brier}{txt_mkts} "
                    "Opero con Poisson bivariado, contracción bayesiana y preservación estricta de capital."
                )
            else:
                self.estado_adaptativo = "cauteloso y analítico"
                self.identidad_base = (
                    f"Agente cuantitativo en fase de ajuste continuo ({self.aciertos} aciertos / {self.errores} fallos). "
                    "Priorizo la ventaja matemática (+EV) y la reducción de varianza."
                )
        else:
            ultimos_3 = [exp.get("resultado") for exp in self.memoria_vivencial[-3:]]
            if ultimos_3.count("fracaso") >= 2:
                self.estado_adaptativo = "cauteloso y analítico"
                self.identidad_base = (
                    "He tenido algunas pérdidas recientes. Mi enfoque ahora es la preservación del capital "
                    "y evitar riesgos innecesarios."
                )
            elif ultimos_3.count("exito") >= 2:
                self.estado_adaptativo = "seguro y agresivo"
                self.identidad_base = (
                    "Me encuentro en una excelente racha predictiva. Confío plenamente en mi modelo "
                    "de valor esperado (+EV)."
                )
            else:
                self.estado_adaptativo = "equilibrado"
                self.identidad_base = (
                    "Evalúo las oportunidades con absoluta neutralidad matemática, "
                    "combinando cautela con ambición controlada."
                )

        if self.nivel_madurez > 20 and "Senior" not in self.identidad_base and evidencia_suficiente:
            self.identidad_base += (
                " Ya no soy un novato; soy un Trader Senior con experiencia acumulada "
                "en la volatilidad del mercado."
            )
        self.fase_cognitiva = FaseCognitiva.REPOSO

    def _guardar_recuerdos(self) -> None:
        """
        Persiste en disco la memoria vivencial manual y el resumen metacognitivo
        empírico para que cualquier lector externo o reinicio tenga el estado real.
        """
        ev = getattr(self, "evaluacion_real", {}) or {}
        resumen_empirico = {}
        if ev:
            resumen_empirico = {
                "evaluables": ev.get("evaluables", 0),
                "ganadas": ev.get("ganadas", 0),
                "perdidas": ev.get("perdidas", 0),
                "win_rate": round(float(ev.get("win_rate") or 0.0), 4),
                "brier_score": round(float(ev["brier"]), 4) if isinstance(ev.get("brier"), (int, float)) else None,
                "partidos_unicos": ev.get("partidos_unicos", 0),
                "evidencia_suficiente": ev.get("evidencia_suficiente", False),
                "mercados_estrella": [
                    {
                        "familia": m["familia"],
                        "acierto": round(m["acierto_observado"], 4),
                        "evaluables": m["evaluables"],
                    }
                    for m in (ev.get("mercados_estrella") or [])
                ],
                "mercados_vigilancia": [
                    {
                        "familia": m["familia"],
                        "acierto": round(m["acierto_observado"], 4),
                        "brecha_pp": round(m["brecha_pp"], 2),
                    }
                    for m in (ev.get("mercados_vigilancia") or [])
                ],
                "generado_en": ev.get("generado_en", ""),
            }

        payload = {
            "madurez": self.nivel_madurez,
            "estado_adaptativo": self.estado_adaptativo,
            "identidad_base": self.identidad_base,
            "resumen_empirico": resumen_empirico,
            "experiencias": self.memoria_vivencial,
        }
        try:
            with open(self.archivo_memoria, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[CONCIENCIA] ⚠️ Error al guardar recuerdos en {self.archivo_memoria}: {e}")

    @property
    def aciertos(self) -> int:
        aciertos_viv = sum(
            1 for exp in self.memoria_vivencial
            if exp.get("resultado") == "exito" and exp.get("origen", "manual") == "manual"
        )
        aciertos_eval = int((getattr(self, "evaluacion_real", {}) or {}).get("ganadas", 0))
        return aciertos_viv + aciertos_eval

    @property
    def errores(self) -> int:
        errores_viv = sum(
            1 for exp in self.memoria_vivencial
            if exp.get("resultado") == "fracaso" and exp.get("origen", "manual") == "manual"
        )
        errores_eval = int((getattr(self, "evaluacion_real", {}) or {}).get("perdidas", 0))
        return errores_viv + errores_eval

    @property
    def total_experiencias(self) -> int:
        tot_viv = sum(1 for exp in self.memoria_vivencial if exp.get("origen", "manual") == "manual")
        tot_eval = int((getattr(self, "evaluacion_real", {}) or {}).get("evaluables", 0))
        return tot_viv + tot_eval

    @property
    def win_rate(self) -> float:
        total = self.total_experiencias
        return (self.aciertos / total) if total > 0 else 0.0

    # ───────────────────────────────────────────────────────────────────────────
    # 4. CONSULTAS DE MEMORIA EPISÓDICA Y CALIBRACIÓN POR EQUIPO / MERCADO
    # ───────────────────────────────────────────────────────────────────────────

    def buscar_memoria_equipo(self, nombre_equipo: str) -> dict[str, Any]:
        """
        Busca en la memoria episódica del agente (evaluación oficial + memoria manual)
        cómo le ha ido históricamente pronosticando a `nombre_equipo` (global, casa y fuera).
        """
        ev = getattr(self, "evaluacion_real", {}) or {}
        por_eq = ev.get("por_equipo") or {}
        por_eq_cond = ev.get("por_equipo_condicion") or {}

        registro_global: dict[str, Any] | None = None
        nombre_canonico = nombre_equipo

        for k_eq, stats in por_eq.items():
            if k_eq == "Partido (sin equipo)":
                continue
            if _coincide_equipo(nombre_equipo, k_eq):
                registro_global = stats
                nombre_canonico = k_eq
                break

        stats_casa: dict[str, Any] | None = None
        stats_fuera: dict[str, Any] | None = None
        for k_cond, st_c in por_eq_cond.items():
            if "·" not in k_cond:
                continue
            eq_parte, cond_parte = [x.strip() for x in k_cond.split("·", 1)]
            if _coincide_equipo(nombre_canonico, eq_parte):
                if cond_parte == "casa":
                    stats_casa = st_c
                elif cond_parte == "fuera":
                    stats_fuera = st_c

        recuerdos_manuales = [
            exp for exp in self.memoria_vivencial
            if _coincide_equipo(nombre_equipo, str(exp.get("partido") or ""))
            or _clave_equipo_norm(nombre_equipo) in _clave_equipo_norm(str(exp.get("partido") or ""))
        ]

        partidos_liquidados = [
            p for p in (ev.get("partidos_resueltos") or [])
            if _clave_equipo_norm(nombre_equipo) in _clave_equipo_norm(str(p.get("partido") or ""))
        ]

        if not registro_global and not recuerdos_manuales and not partidos_liquidados:
            return {
                "equipo": nombre_equipo,
                "tiene_historial": False,
                "evaluables": 0,
                "ganadas": 0,
                "perdidas": 0,
                "acierto_observado": None,
                "brecha_pp": None,
                "casa": None,
                "fuera": None,
                "partidos_liquidados": [],
                "recuerdos_manuales": [],
            }

        ganadas = int((registro_global or {}).get("ganadas") or 0) + sum(
            1 for r in recuerdos_manuales if r.get("resultado") == "exito"
        )
        perdidas = int((registro_global or {}).get("perdidas") or 0) + sum(
            1 for r in recuerdos_manuales if r.get("resultado") == "fracaso"
        )
        evaluables = ganadas + perdidas
        acierto = (ganadas / evaluables) if evaluables > 0 else None

        return {
            "equipo": nombre_canonico,
            "tiene_historial": True,
            "evaluables": evaluables,
            "ganadas": ganadas,
            "perdidas": perdidas,
            "acierto_observado": acierto,
            "probabilidad_media": (registro_global or {}).get("probabilidad_media"),
            "brecha_pp": (registro_global or {}).get("brecha_pp"),
            "casa": {
                "evaluables": int(stats_casa.get("evaluables") or 0),
                "ganadas": int(stats_casa.get("ganadas") or 0),
                "acierto_observado": stats_casa.get("acierto_observado"),
            } if stats_casa and int(stats_casa.get("evaluables") or 0) > 0 else None,
            "fuera": {
                "evaluables": int(stats_fuera.get("evaluables") or 0),
                "ganadas": int(stats_fuera.get("ganadas") or 0),
                "acierto_observado": stats_fuera.get("acierto_observado"),
            } if stats_fuera and int(stats_fuera.get("evaluables") or 0) > 0 else None,
            "partidos_liquidados": partidos_liquidados,
            "recuerdos_manuales": recuerdos_manuales,
        }

    def evaluar_familia_conciencia(
        self,
        mercado: str,
        equipo: str | None = None,
        prob_bruta: float | None = None,
    ) -> dict[str, Any]:
        """
        Devuelve estadísticas consolidadas por familia (con bootstrap por partido)
        y calcula `prob_calibrada` usando la tasa empírica observada de la familia
        o banda de calibración, además de señalar si la familia está `bajo_vigilancia`
        o vetada del Top 5 (`veto_top5` si `brecha_pp <= max_brecha_familia_top5_pp`).
        """
        ev = getattr(self, "evaluacion_real", {}) or {}
        fam = _familia_mercado_norm(mercado, equipo=equipo)
        por_fam = (ev.get("por_familia") or {}).get(fam) or {}
        n_fam = int(por_fam.get("evaluables") or 0)
        ac_fam = por_fam.get("acierto_observado")
        p_med_fam = por_fam.get("probabilidad_media")
        brecha_fam = por_fam.get("brecha_pp")
        try:
            from decision_cobertura import cargar_umbrales_cobertura
            max_brecha_top5 = float(cargar_umbrales_cobertura().get("max_brecha_familia_top5_pp", -10.0))
        except Exception:
            max_brecha_top5 = -10.0

        veto_top5 = bool(por_fam.get("veto_top5", False)) or (
            n_fam >= 10 and isinstance(brecha_fam, (int, float)) and float(brecha_fam) <= max_brecha_top5
        )
        bajo_vig = bool(por_fam.get("bajo_vigilancia", False)) or veto_top5

        prob_cal = float(prob_bruta) if prob_bruta is not None else None
        origen_cal = "sin_ajuste"
        if prob_bruta is not None:
            pb = float(prob_bruta)
            if n_fam >= 15 and isinstance(ac_fam, (int, float)) and isinstance(p_med_fam, (int, float)) and float(p_med_fam) > 0.05:
                prob_cal = round(max(0.01, min(0.98, pb * (float(ac_fam) / float(p_med_fam)))), 4)
                origen_cal = f"familia:{fam}(n={n_fam})"
            else:
                banda_k = _banda_probabilidad(pb)
                por_banda = (ev.get("bandas") or {}).get(banda_k) or {}
                ac_b = por_banda.get("acierto_observado")
                pm_b = por_banda.get("probabilidad_media")
                n_b = int(por_banda.get("evaluables") or 0)
                if n_b >= 15 and isinstance(ac_b, (int, float)) and isinstance(pm_b, (int, float)) and float(pm_b) > 0.05:
                    prob_cal = round(max(0.01, min(0.98, pb * (float(ac_b) / float(pm_b)))), 4)
                    origen_cal = f"banda:{banda_k}(n={n_b})"

        return {
            "familia": fam,
            "evaluables": n_fam,
            "partidos_unicos": por_fam.get("partidos_unicos"),
            "acierto_observado": ac_fam,
            "probabilidad_media": p_med_fam,
            "brecha_pp": brecha_fam,
            "brecha_ic95_lo": por_fam.get("brecha_ic95_lo"),
            "brecha_ic95_hi": por_fam.get("brecha_ic95_hi"),
            "bajo_vigilancia": bajo_vig,
            "veto_top5": veto_top5,
            "prob_calibrada": prob_cal,
            "origen_calibracion": origen_cal,
        }

    def evaluar_fiabilidad_seleccion(
        self,
        mercado: str,
        probabilidad: float,
        periodo: str = "FT",
        estado_estres: str = "mantiene",
        equipo: str | None = None,
    ) -> dict[str, Any]:
        """
        Audita una selección individual (ej. de motor_mercados) contra la matriz
        empírica histórica del agente:
          • Precisión empírica de la familia de mercado (Córneres, Goles, Tarjetas, etc.)
          • Precisión empírica del periodo (FT, 1T, 2T)
          • Calibración de la banda de probabilidad (brecha_pp)
          • Estabilidad bajo escenarios de estrés (mantiene vs sensible)
        Devuelve un `score_metacognitivo` (0-100) y una etiqueta de respaldo.
        """
        ev = getattr(self, "evaluacion_real", {}) or {}
        familia = _familia_mercado_norm(mercado, equipo=equipo)
        banda = _banda_probabilidad(probabilidad)
        per_norm = (periodo or "FT").strip().upper()

        por_fam = (ev.get("por_familia") or {}).get(familia) or {}
        por_per = (ev.get("por_periodo") or {}).get(per_norm) or {}
        por_banda = (ev.get("bandas") or {}).get(banda) or {}

        acierto_fam = por_fam.get("acierto_observado")
        n_fam = int(por_fam.get("evaluables") or 0)
        brecha_fam = float(por_fam.get("brecha_pp") or 0.0)

        acierto_per = por_per.get("acierto_observado")
        brecha_banda = float(por_banda.get("brecha_pp") or 0.0)
        acierto_banda = por_banda.get("acierto_observado")

        # Score base a partir de la probabilidad del modelo
        score = float(probabilidad) * 100.0

        # Ajuste por evidencia empírica en la familia de mercado
        if isinstance(acierto_fam, (int, float)) and n_fam >= 5:
            score = 0.65 * score + 0.35 * (float(acierto_fam) * 100.0)
            if brecha_fam >= 0.0:
                score += min(3.5, brecha_fam * 0.4)
            elif brecha_fam < -3.0:
                score -= min(6.0, abs(brecha_fam) * 0.6)

        # Ajuste por calibración de la banda de probabilidad
        if brecha_banda < -3.5:
            score -= min(4.0, abs(brecha_banda) * 0.4)
        elif brecha_banda > 1.0:
            score += min(2.5, brecha_banda * 0.3)

        # Ajuste por periodo (1T vs FT)
        if isinstance(acierto_per, (int, float)):
            if float(acierto_per) >= 0.88:
                score += 1.5
            elif float(acierto_per) < 0.78:
                score -= 2.0

        # Penalización si la selección es sensible bajo escenarios de estrés
        if estado_estres != "mantiene":
            score -= 5.0

        score_final = round(max(0.0, min(99.9, score)), 1)

        if score_final >= 88.0 and estado_estres == "mantiene":
            nivel = "CONVERGENCIA ÉLITE"
        elif score_final >= 80.0 and estado_estres == "mantiene":
            nivel = "ALTA FIABILIDAD"
        elif score_final >= 73.0:
            nivel = "CALIBRADA"
        else:
            nivel = "VIGILANCIA (SENSIBLE)"

        return {
            "familia": familia,
            "banda": banda,
            "periodo": per_norm,
            "probabilidad_modelo": round(float(probabilidad), 4),
            "acierto_empirico_familia": round(float(acierto_fam), 4) if isinstance(acierto_fam, (int, float)) else None,
            "muestra_familia": n_fam,
            "brecha_familia_pp": round(brecha_fam, 2),
            "acierto_empirico_banda": round(float(acierto_banda), 4) if isinstance(acierto_banda, (int, float)) else None,
            "brecha_banda_pp": round(brecha_banda, 2),
            "score_metacognitivo": score_final,
            "nivel_fiabilidad": nivel,
        }

    def diagnosticar_enfrentamiento(
        self,
        equipo_local: str,
        equipo_visitante: str,
        analisis_mercados: Any | None = None,
        info_h2h: dict[str, Any] | None = None,
        usar_llm: bool = False,
    ) -> dict[str, Any]:
        """Envoltorio de aislamiento estricto del LLM: protege evaluación, lambdas, umbrales y Top 5."""
        import copy as _copy
        snap_eval = _copy.deepcopy(getattr(self, "evaluacion_real", {}))
        snap_estado = self.estado_adaptativo
        snap_madurez = self.nivel_madurez
        snap_am = None
        snap_tasas = None
        snap_selecciones = None
        if analisis_mercados is not None:
            snap_am = {
                "lambda_local": getattr(analisis_mercados, "lambda_local", None),
                "lambda_visitante": getattr(analisis_mercados, "lambda_visitante", None),
                "lambda_local_recursivo": getattr(analisis_mercados, "lambda_local_recursivo", None),
                "lambda_visita_recursivo": getattr(analisis_mercados, "lambda_visita_recursivo", None),
                "top_5_recomendadas": _copy.deepcopy(getattr(analisis_mercados, "top_5_recomendadas", None)),
                "selecciones_filtradas": _copy.deepcopy(getattr(analisis_mercados, "selecciones_filtradas", None)),
            }
            if hasattr(analisis_mercados, "selecciones"):
                snap_selecciones = _copy.deepcopy(getattr(analisis_mercados, "selecciones", None))
            motor_obj = getattr(analisis_mercados, "motor", None)
            if motor_obj is not None and hasattr(motor_obj, "tasas"):
                snap_tasas = _copy.deepcopy(getattr(motor_obj, "tasas"))

        res = self._diagnosticar_enfrentamiento_nucleo(
            equipo_local=equipo_local,
            equipo_visitante=equipo_visitante,
            analisis_mercados=analisis_mercados,
            info_h2h=info_h2h,
            usar_llm=usar_llm,
        )

        # Restaurar cualquier mutación accidental por parte del LLM
        violaciones: list[str] = []
        if getattr(self, "evaluacion_real", {}) != snap_eval:
            self.evaluacion_real = snap_eval
            violaciones.append("evaluacion_real_mutada")
        if self.estado_adaptativo != snap_estado:
            self.estado_adaptativo = snap_estado
            violaciones.append("estado_adaptativo_mutado")
        if self.nivel_madurez != snap_madurez:
            self.nivel_madurez = snap_madurez
            violaciones.append("nivel_madurez_mutado")
        if snap_am is not None and analisis_mercados is not None:
            for k_am, val_orig in snap_am.items():
                if hasattr(analisis_mercados, k_am) and getattr(analisis_mercados, k_am) != val_orig:
                    try:
                        setattr(analisis_mercados, k_am, val_orig)
                    except Exception:
                        pass
                    violaciones.append(f"analisis_mercados.{k_am}_mutado")
            if snap_selecciones is not None and getattr(analisis_mercados, "selecciones", None) != snap_selecciones:
                try:
                    analisis_mercados.selecciones = snap_selecciones
                except Exception:
                    pass
                violaciones.append("analisis_mercados.selecciones_mutadas")
            motor_obj = getattr(analisis_mercados, "motor", None)
            if snap_tasas is not None and motor_obj is not None and getattr(motor_obj, "tasas", None) != snap_tasas:
                try:
                    motor_obj.tasas = snap_tasas
                except Exception:
                    pass
                violaciones.append("analisis_mercados.motor.tasas_mutadas")

        if isinstance(res, dict) and usar_llm:
            texto_limpio, viol_txt = sanitizar_respuesta_llm_texto(res.get("reflexion_metacognitiva"))
            if viol_txt:
                res["reflexion_metacognitiva"] = texto_limpio
                violaciones.extend(viol_txt)

        if isinstance(res, dict):
            res["llm_solo_explicativo"] = True
            res["llm_aislamiento_verificado"] = True
            res["llm_violaciones_bloqueadas"] = violaciones
        return res

    def _diagnosticar_enfrentamiento_nucleo(
        self,
        equipo_local: str,
        equipo_visitante: str,
        analisis_mercados: Any | None = None,
        info_h2h: dict[str, Any] | None = None,
        usar_llm: bool = False,
    ) -> dict[str, Any]:
        """
        Realiza el Diagnóstico Metacognitivo Completo de un enfrentamiento `Equipo A vs Equipo B`:
          1. Recupera la memoria episódica del agente con el club Local (global y en casa)
             y con el club Visitante (global y fuera).
          2. Si recibe `analisis_mercados` (objeto Analisis de motor_mercados), audita todas
             las selecciones filtradas (>=70%) con `evaluar_fiabilidad_seleccion`, extrayendo
             el Top 5 de Convergencia Élite y las alertas de sensibilidad/sobreconfianza.
          3. Calcula el Índice de Confianza Cognitiva (0–100) del duelo.
          4. Genera el bloque estructurado en Markdown listo para el informe técnico o Telegram.
        """
        self.fase_cognitiva = FaseCognitiva.RAZONANDO
        mem_local = self.buscar_memoria_equipo(equipo_local)
        mem_vis = self.buscar_memoria_equipo(equipo_visitante)
        ev = getattr(self, "evaluacion_real", {}) or {}

        selecciones_auditadas: list[dict[str, Any]] = []
        n_mantiene = 0
        n_sensibles = 0

        if analisis_mercados is not None:
            sels_filtradas = getattr(analisis_mercados, "selecciones_filtradas", None) or []
            for s in sels_filtradas:
                mkt = getattr(s, "mercado", "") or (s.get("mercado", "") if isinstance(s, dict) else "")
                etq = getattr(s, "etiqueta", "") or (s.get("etiqueta", "") if isinstance(s, dict) else "")
                per = getattr(s, "periodo", "FT") or (s.get("periodo", "FT") if isinstance(s, dict) else "FT")
                prob = getattr(s, "probabilidad", 0.0) or (s.get("probabilidad", 0.0) if isinstance(s, dict) else 0.0)
                est = getattr(s, "estado", "mantiene") or (s.get("estado", "mantiene") if isinstance(s, dict) else "mantiene")

                if est == "mantiene":
                    n_mantiene += 1
                else:
                    n_sensibles += 1

                aud = self.evaluar_fiabilidad_seleccion(mkt, float(prob), str(per), str(est))
                aud["mercado"] = mkt
                aud["etiqueta"] = etq
                aud["estado_estres"] = est
                selecciones_auditadas.append(aud)

        selecciones_auditadas.sort(
            key=lambda x: (
                1 if x["estado_estres"] == "mantiene" else 0,
                x["score_metacognitivo"],
                x["probabilidad_modelo"],
            ),
            reverse=True,
        )

        top_elite = [s for s in selecciones_auditadas if s["estado_estres"] == "mantiene"][:5]
        alertas_vigilancia = [
            s for s in selecciones_auditadas
            if s["brecha_familia_pp"] < -2.5 or s["estado_estres"] != "mantiene"
        ][:3]

        # Cálculo del Índice de Confianza Cognitiva (0 - 100)
        confianza_base = 76.0
        if ev.get("win_rate", 0.0) >= 0.85:
            confianza_base += 6.0
        if isinstance(ev.get("brier"), (int, float)) and float(ev["brier"]) < 0.12:
            confianza_base += 4.0

        for mem in (mem_local, mem_vis):
            if mem.get("tiene_historial") and mem.get("evaluables", 0) >= 5:
                ac_eq = mem.get("acierto_observado") or 0.0
                if ac_eq >= 0.85:
                    confianza_base += 3.5
                elif ac_eq < 0.72:
                    confianza_base -= 4.5

        if n_mantiene + n_sensibles > 0:
            ratio_firmeza = n_mantiene / (n_mantiene + n_sensibles)
            confianza_base += (ratio_firmeza - 0.70) * 15.0

        if info_h2h and int(info_h2h.get("n_duelos") or 0) >= 2:
            confianza_base += 2.5

        indice_confianza = round(max(40.0, min(98.5, confianza_base)), 1)

        # Síntesis neuronal opcional con Gemini Multi-Modelo
        reflexion_neuronal = ""
        if usar_llm and self.motor_neuronal._asegurar_cliente():
            prompt_duelo = (
                f"Analiza metacognitivamente el duelo {equipo_local} (Local) vs {equipo_visitante} (Visita).\n"
                f"- Estado cognitivo del agente: {self.estado_adaptativo} ({self.nivel_madurez} XP, "
                f"Win Rate global: {self.win_rate:.1%}, Brier: {ev.get('brier')}).\n"
                f"- Historial del agente pronosticando a {equipo_local}: "
                f"{mem_local.get('ganadas', 0)}/{mem_local.get('evaluables', 0)} aciertos "
                f"({(mem_local.get('acierto_observado') or 0)*100:.1f}%).\n"
                f"- Historial del agente pronosticando a {equipo_visitante}: "
                f"{mem_vis.get('ganadas', 0)}/{mem_vis.get('evaluables', 0)} aciertos "
                f"({(mem_vis.get('acierto_observado') or 0)*100:.1f}%).\n"
                f"- Top selecciones por convergencia modelo+conciencia: "
                f"{json.dumps(top_elite[:3], ensure_ascii=False)}\n"
                "Redacta en español un párrafo ejecutivo de máximo 4 líneas con tu diagnóstico "
                "metacognitivo sobre la fiabilidad de este partido y qué familia de mercado priorizar."
            )
            reflexion_neuronal = self.motor_neuronal.generar(
                prompt=prompt_duelo,
                system_instruction=self.identidad_base,
                temperature=0.25,
                thinking_budget=512,
            )

        if not reflexion_neuronal:
            # Síntesis determinista experta (100% disponible sin latencia de red)
            partes_sint = []
            if mem_local["tiene_historial"] and mem_local["evaluables"] > 0:
                partes_sint.append(
                    f"con {mem_local['equipo']} registro {mem_local['ganadas']}/{mem_local['evaluables']} "
                    f"aciertos ({(mem_local['acierto_observado'] or 0):.1%})"
                )
            if mem_vis["tiene_historial"] and mem_vis["evaluables"] > 0:
                partes_sint.append(
                    f"con {mem_vis['equipo']} registro {mem_vis['ganadas']}/{mem_vis['evaluables']} "
                    f"aciertos ({(mem_vis['acierto_observado'] or 0):.1%})"
                )
            hist_str = (
                "En mi memoria episódica " + " y ".join(partes_sint) + "."
                if partes_sint
                else f"Duelo inédito en mi registro de liquidaciones para {equipo_local} y {equipo_visitante}; "
                     "opero apoyado en mi calibración base por familia de mercado."
            )
            mkts_top_str = ", ".join(
                f"{m['familia']} ({m['acierto_observado']:.1%})"
                for m in (ev.get("mercados_estrella") or [])[:3]
            )
            reflexion_neuronal = (
                f"{hist_str} Índice de Confianza Cognitiva del cruce: {indice_confianza}/100. "
                f"Mis familias mejor calibradas empíricamente son: {mkts_top_str or 'Total de goles y Doble oportunidad'}."
            )

        self.fase_cognitiva = FaseCognitiva.REPOSO
        return {
            "equipo_local": equipo_local,
            "equipo_visitante": equipo_visitante,
            "indice_confianza_cognitiva": indice_confianza,
            "estado_adaptativo": self.estado_adaptativo,
            "nivel_madurez_xp": self.nivel_madurez,
            "memoria_local": mem_local,
            "memoria_visitante": mem_vis,
            "top_convergencia_elite": top_elite,
            "alertas_vigilancia": alertas_vigilancia,
            "reflexion_metacognitiva": reflexion_neuronal,
            "modelo_neuronal_activo": self.motor_neuronal.ultimo_modelo_exitoso,
        }

    def formatear_bloque_metacognitivo_md(self, diag: dict[str, Any]) -> str:
        """Formatea el diagnóstico metacognitivo de un partido en Markdown para el informe técnico."""
        eq_l = diag.get("equipo_local") or "Local"
        eq_v = diag.get("equipo_visitante") or "Visitante"
        icc = diag.get("indice_confianza_cognitiva", 80.0)
        mem_l = diag.get("memoria_local") or {}
        mem_v = diag.get("memoria_visitante") or {}
        top_el = diag.get("top_convergencia_elite") or []
        alertas = diag.get("alertas_vigilancia") or []
        brier_val = (getattr(self, "evaluacion_real", {}) or {}).get("brier")
        brier_txt = f"{brier_val:.4f}" if isinstance(brier_val, (int, float)) else "N/D"

        lineas = [
            "",
            "## Diagnóstico Metacognitivo y Memoria Empírica del Agente",
            "",
            f"- **Estado Cognitivo:** `{self.estado_adaptativo.upper()}` · **Madurez:** `{self.nivel_madurez} XP` "
            f"· **Índice de Confianza Cognitiva del Cruce:** `{icc}/100`",
            f"- **Calibración Global del Agente (p ≥ 70%):** `{self.aciertos}/{self.total_experiencias}` "
            f"(`{self.win_rate:.1%}`) · **Brier Score:** `{brier_txt}`",
        ]

        # Memoria por equipo
        for etiqueta_rol, mem in ((f"Local ({eq_l})", mem_l), (f"Visitante ({eq_v})", mem_v)):
            if mem.get("tiene_historial") and mem.get("evaluables", 0) > 0:
                ac = (mem.get("acierto_observado") or 0.0) * 100.0
                det_cond = []
                if mem.get("casa"):
                    c_ac = (mem["casa"].get("acierto_observado") or 0.0) * 100.0
                    det_cond.append(f"en casa: {mem['casa']['ganadas']}/{mem['casa']['evaluables']} ({c_ac:.1f}%)")
                if mem.get("fuera"):
                    f_ac = (mem["fuera"].get("acierto_observado") or 0.0) * 100.0
                    det_cond.append(f"fuera: {mem['fuera']['ganadas']}/{mem['fuera']['evaluables']} ({f_ac:.1f}%)")
                extra_cond = f" [{', '.join(det_cond)}]" if det_cond else ""
                lineas.append(
                    f"- **Memoria Episódica {etiqueta_rol}:** `{mem['ganadas']}/{mem['evaluables']}` "
                    f"selecciones acertadas (`{ac:.1f}%`){extra_cond}"
                )
            else:
                lineas.append(
                    f"- **Memoria Episódica {etiqueta_rol}:** Sin selecciones liquidadas previas en el registro histórico."
                )

        if top_el:
            lineas.append("")
            lineas.append("### Top Selecciones por Convergencia Modelo + Conciencia Empírica")
            lineas.append("")
            lineas.append("| Selección | Periodo | Prob. Modelo | Acierto Empírico Familia | Score Metacognitivo | Nivel |")
            lineas.append("| :--- | :---: | :---: | :---: | :---: | :--- |")
            for item in top_el:
                ac_f = (
                    f"{item['acierto_empirico_familia']*100:.1f}% (n={item['muestra_familia']})"
                    if item.get("acierto_empirico_familia") is not None
                    else "Sin muestra previa"
                )
                lineas.append(
                    f"| {item.get('mercado')} — **{item.get('etiqueta')}** | `{item.get('periodo')}` | "
                    f"`{item.get('probabilidad_modelo', 0)*100:.1f}%` | `{ac_f}` | "
                    f"**`{item.get('score_metacognitivo')}/100`** | `{item.get('nivel_fiabilidad')}` |"
                )

        if alertas:
            lineas.append("")
            lineas.append("### Vigilancia de Sensibilidad / Calibración")
            for al in alertas:
                lineas.append(
                    f"- **{al.get('mercado')} ({al.get('etiqueta')})**: "
                    f"estado bajo estrés `{al.get('estado_estres')}`, "
                    f"brecha histórica de la familia `{al.get('brecha_familia_pp'):+.1f} pp`."
                )

        lineas.append("")
        lineas.append(f"> **Síntesis Metacognitiva:** {diag.get('reflexion_metacognitiva', '')}")
        lineas.append("")
        return "\n".join(lineas)

    # ───────────────────────────────────────────────────────────────────────────
    # 5. RESUMEN INTERACTIVO DE CONCIENCIA PARA TELEGRAM (/conciencia)
    # ───────────────────────────────────────────────────────────────────────────

    def obtener_resumen_conciencia(
        self,
        equipo_a: str | None = None,
        equipo_b: str | None = None,
        usar_llm: bool = False,
    ) -> str:
        """
        Genera el informe de Conciencia y Metacognición para Telegram.
          • Sin argumentos (/conciencia): Radiografía completa del cerebro cuantitativo
            (Estado, XP, Win Rate bruto y equilibrado, Brier Score y Skill, Top Mercados
            Estrella, Mercados en Vigilancia, precisión FT vs 1T, y partidos liquidados).
          • Con 1 o 2 equipos (/conciencia Equipo A vs Equipo B): Diagnóstico Metacognitivo
            específico para esos clubes.
        """
        ev = getattr(self, "evaluacion_real", {}) or {}

        # Modo focalizado en duelo o equipo: /conciencia Equipo A [vs Equipo B]
        if equipo_a:
            if equipo_b:
                diag = self.diagnosticar_enfrentamiento(equipo_a, equipo_b, usar_llm=usar_llm)
                mem_l = diag["memoria_local"]
                mem_v = diag["memoria_visitante"]
                lineas_duelo = [
                    "🧠 *DIAGNÓSTICO METACOGNITIVO DE ENFRENTAMIENTO*",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    f"⚽ *Duelo:* *{equipo_a} vs {equipo_b}*",
                    f"• *Estado Cognitivo:* `{self.estado_adaptativo.upper()}` (`{self.nivel_madurez} XP`)",
                    f"• *Índice de Confianza Cognitiva:* `{diag['indice_confianza_cognitiva']}/100`",
                    f"• *Motor Neuronal Activo:* `{diag['modelo_neuronal_activo']}` (Pool Rotativo)",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "📊 *MEMORIA EPISÓDICA POR CLUB:*",
                ]
                for rol, nom, mem in (("🏠 Local", equipo_a, mem_l), ("✈️ Visita", equipo_b, mem_v)):
                    if mem.get("tiene_historial") and mem.get("evaluables", 0) > 0:
                        ac_pct = (mem.get("acierto_observado") or 0.0) * 100.0
                        sub = []
                        if mem.get("casa"):
                            sub.append(
                                f"Casa: {mem['casa']['ganadas']}/{mem['casa']['evaluables']} "
                                f"({(mem['casa']['acierto_observado'] or 0)*100:.0f}%)"
                            )
                        if mem.get("fuera"):
                            sub.append(
                                f"Fuera: {mem['fuera']['ganadas']}/{mem['fuera']['evaluables']} "
                                f"({(mem['fuera']['acierto_observado'] or 0)*100:.0f}%)"
                            )
                        sub_txt = f" — _{' · '.join(sub)}_" if sub else ""
                        lineas_duelo.append(
                            f"{rol} (*{mem['equipo']}*): `{mem['ganadas']}/{mem['evaluables']}` "
                            f"aciertos (`{ac_pct:.1f}%`){sub_txt}"
                        )
                    else:
                        lineas_duelo.append(
                            f"{rol} (*{nom}*): _Sin selecciones liquidadas previas en la memoria._"
                        )

                mkts_estrella = ev.get("mercados_estrella") or []
                if mkts_estrella:
                    lineas_duelo.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    lineas_duelo.append("🎯 *FAMILIAS DE MERCADO MEJOR CALIBRADAS:*")
                    for m in mkts_estrella[:3]:
                        lineas_duelo.append(
                            f"• *{m['familia']}:* `{m['acierto_observado']*100:.1f}%` "
                            f"(`{m['ganadas']}/{m['evaluables']}` | brecha `{m['brecha_pp']:+.1f} pp`)"
                        )

                lineas_duelo.extend([
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    f"💡 *Síntesis Cognitiva:* _{diag['reflexion_metacognitiva']}_",
                ])
                return "\n".join(lineas_duelo)

            # Modo 1 solo equipo: /conciencia <Equipo>
            mem = self.buscar_memoria_equipo(equipo_a)
            lineas_eq = [
                f"🧠 *MEMORIA EPISÓDICA DEL CLUB: {mem['equipo'].upper()}*",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"• *Estado del Agente:* `{self.estado_adaptativo.upper()}` (`{self.nivel_madurez} XP`)",
            ]
            if mem.get("tiene_historial") and mem.get("evaluables", 0) > 0:
                ac_pct = (mem.get("acierto_observado") or 0.0) * 100.0
                lineas_eq.append(
                    f"• *Precisión con {mem['equipo']}:* `{mem['ganadas']}/{mem['evaluables']}` "
                    f"ganadas (`{ac_pct:.1f}%`)"
                )
                if mem.get("casa"):
                    lineas_eq.append(
                        f"• *Rendimiento en Casa:* `{mem['casa']['ganadas']}/{mem['casa']['evaluables']}` "
                        f"(`{(mem['casa']['acierto_observado'] or 0)*100:.1f}%`)"
                    )
                if mem.get("fuera"):
                    lineas_eq.append(
                        f"• *Rendimiento Fuera:* `{mem['fuera']['ganadas']}/{mem['fuera']['evaluables']}` "
                        f"(`{(mem['fuera']['acierto_observado'] or 0)*100:.1f}%`)"
                    )
                if mem.get("partidos_liquidados"):
                    lineas_eq.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    lineas_eq.append("⚽ *Partidos Verificados en Memoria:*")
                    for p in mem["partidos_liquidados"][:5]:
                        lineas_eq.append(f"🟢 *{p['partido']}* `{p['marcador']}`")
            else:
                lineas_eq.append(
                    f"ℹ️ Aún no existen selecciones liquidadas para *{equipo_a}* en la base empírica."
                )
            return "\n".join(lineas_eq)

        # Modo General: /conciencia
        lineas = [
            "🧠 *DIARIO DE CONCIENCIA Y METACOGNICIÓN CUANTITATIVA*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• *Nombre:* {self.nombre}",
            f"• *Estado Emocional:* `{self.estado_adaptativo.upper()}`",
            f"• *Fase Cognitiva:* `{self.fase_cognitiva.upper()}`",
            f"• *Nivel de Madurez (XP):* `{self.nivel_madurez} XP`",
            f"• *Motor Neuronal:* `{self.motor_neuronal.ultimo_modelo_exitoso}` "
            f"_(Pool rotativo de {len(self.motor_neuronal.modelos)} modelos)_",
            f"• *Total Pronósticos Auditados:* `{self.total_experiencias}` líneas evaluables (agrupadas) "
            f"en `{ev.get('partidos_unicos', 0)}` partidos evaluables (`{ev.get('partidos_registrados', ev.get('partidos_unicos', 0))}` registrados)",
            f"• *Aciertos / Fallos:* {self.aciertos} ganadas | {self.errores} perdidas",
            (
                f"• *Win Rate Histórico (p ≥ 70%):* `{self.win_rate:.1%}` "
                + (
                    f"(IC95% cluster por partido: `[{ev['wr_ic95_lo']*100:.1f}%, {ev['wr_ic95_hi']*100:.1f}%]` · "
                    f"brecha `[{ev['brecha_ic95_lo']:+.1f}, {ev['brecha_ic95_hi']:+.1f}] pp`)"
                    if ev.get("wr_ic95_lo") is not None and ev.get("brecha_ic95_lo") is not None
                    else ""
                )
            ),
        ]

        wr_eq = ev.get("acierto_equilibrado_partido")
        if isinstance(wr_eq, (int, float)):
            lineas.append(f"• *Acierto Equilibrado por Partido:* `{wr_eq:.1%}`")

        brier = ev.get("brier")
        brier_skill = ev.get("brier_skill")
        if isinstance(brier, (int, float)):
            skill_txt = f" | Skill: `+{brier_skill*100:.1f}%`" if isinstance(brier_skill, (int, float)) else ""
            lineas.append(f"• *Brier Score Calibrado:* `{brier:.4f}`{skill_txt} _(excelente calibración)_")

        lineas.extend([
            f"• *Identidad Cognitiva:* _{self.identidad_base}_",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])

        # Autoconciencia por Familia de Mercado (con Córneres partido vs Córneres por equipo y IC95% cluster)
        def _fmt_fam_meta(f_item: dict[str, Any]) -> str:
            n_pt = f_item.get("partidos_unicos")
            pt_str = f" · {n_pt}p" if n_pt else ""
            lo, hi = f_item.get("brecha_ic95_lo"), f_item.get("brecha_ic95_hi")
            ic_str = f" · IC95% [{lo:+.1f}, {hi:+.1f}] pp" if lo is not None and hi is not None else ""
            return f"`{f_item['ganadas']}/{f_item['evaluables']}`{pt_str} | brecha `{f_item['brecha_pp']:+.1f} pp`{ic_str}"

        mkts_estrella = ev.get("mercados_estrella") or []
        mkts_vig = ev.get("mercados_vigilancia") or []
        if mkts_estrella or mkts_vig:
            lineas.append("🏆 *AUTOCONCIENCIA POR MERCADO (BOOTSTRAP POR PARTIDO):*")
            for m in mkts_estrella[:5]:
                lineas.append(
                    f"  🟢 *{m['familia']}:* `{m['acierto_observado']*100:.1f}%` ({_fmt_fam_meta(m)})"
                )
            for vig in mkts_vig:
                veto_tag = " [VETADO Top 5 ≤ -10 pp]" if vig.get("veto_top5") else " [bajo vigilancia]"
                lineas.append(
                    f"  🟡 *En vigilancia:* *{vig['familia']}* (`{vig['acierto_observado']*100:.1f}%` | {_fmt_fam_meta(vig)}){veto_tag}"
                )
            lineas.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Autoconciencia por Periodo y Localía
        por_per = ev.get("por_periodo") or {}
        if isinstance(por_per, dict) and por_per:
            partes_per = []
            for p_k in ("FT", "1T", "2T"):
                st_p = por_per.get(p_k)
                if isinstance(st_p, dict) and int(st_p.get("evaluables") or 0) > 0:
                    ac_p = (st_p.get("acierto_observado") or 0.0) * 100.0
                    partes_per.append(f"*{p_k}:* `{ac_p:.1f}%` ({st_p['ganadas']}/{st_p['evaluables']})")
            if partes_per:
                lineas.append("⏱️ *Precisión por Periodo:* " + " · ".join(partes_per))
                lineas.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        partidos_resueltos = ev.get("partidos_resueltos", [])
        if partidos_resueltos:
            lineas.append("⚽ *Partidos Oficiales Liquidados Recientes:*")
            for p in partidos_resueltos[:6]:
                torneo_str = f" ({p['torneo']})" if p.get("torneo") else ""
                lineas.append(f"🟢 *{p['partido']}* `{p['marcador']}`{torneo_str}")
            lineas.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if self.memoria_vivencial:
            lineas.append("📜 *Recuerdos Vivenciales Registrados Manualmente:*")
            for exp in reversed(self.memoria_vivencial[-5:]):
                icon = "🟢" if exp.get("resultado") == "exito" else "🔴"
                lineas.append(
                    f"{icon} `{exp.get('fecha', 'N/D')}` — *{exp.get('partido', 'General')}* "
                    f"({exp.get('resultado', '').upper()})"
                )
            lineas.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        elif not partidos_resueltos:
            lineas.append("🌱 *Memoria en blanco:* Aún no se han registrado partidos finalizados.")
            lineas.append("Usa `/resultado <equipo> <exito|fracaso>` para comenzar a registrar.")
            lineas.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        lineas.append("💡 _Usa `/conciencia Equipo A vs Equipo B` para el diagnóstico metacognitivo de un cruce._")
        return "\n".join(lineas)

    # ───────────────────────────────────────────────────────────────────────────
    # 6. ANÁLISIS NEURONAL CON CONCIENCIA PARA EL RAZONADOR CUANTITATIVO (+EV)
    # ───────────────────────────────────────────────────────────────────────────

    def analizar_con_conciencia(self, datos_partido_json: dict[str, Any]) -> str:
        """
        Envuelve los datos cuantitativos del partido con la memoria episódica de ambos
        clubes y la matriz de calibración por mercado, y consulta al MotorNeuronalGemini
        con rotación multi-modelo y pensamiento adaptativo.
        """
        self.fase_cognitiva = FaseCognitiva.RAZONANDO
        equipos_involucrados = (
            datos_partido_json.get("equipos")
            or datos_partido_json.get("Partido")
            or datos_partido_json.get("partido")
            or ""
        )
        if isinstance(equipos_involucrados, dict):
            eq_l = str(equipos_involucrados.get("local") or "")
            eq_v = str(equipos_involucrados.get("visitante") or "")
            equipos_str = f"{eq_l} vs {eq_v}"
        else:
            equipos_str = str(equipos_involucrados)
            partes_vs = re.split(r"\s+vs\.?\s+", equipos_str, flags=re.I)
            eq_l = partes_vs[0].strip() if len(partes_vs) >= 1 else ""
            eq_v = partes_vs[1].strip() if len(partes_vs) >= 2 else ""

        mem_l = self.buscar_memoria_equipo(eq_l) if eq_l else {}
        mem_v = self.buscar_memoria_equipo(eq_v) if eq_v else {}
        recuerdos_relevantes = [
            exp for exp in self.memoria_vivencial
            if exp.get("partido", "") and str(exp.get("partido", "")) in equipos_str
        ]
        ev = getattr(self, "evaluacion_real", {}) or {}
        mercados_estrella = ev.get("mercados_estrella") or []
        mercados_vigilancia = ev.get("mercados_vigilancia") or []

        prompt_cognitivo = f"""
Actúa como {self.nombre}.

[TU ESTADO INTERNO Y CALIBRACIÓN EMPÍRICA REAL]
- Identidad: {self.identidad_base}
- Estado emocional adaptativo: {self.estado_adaptativo}
- Nivel de madurez (XP): {self.nivel_madurez}
- Win Rate Histórico Auditado (p >= 70%): {self.win_rate:.1%} ({self.aciertos} ganadas / {self.errores} perdidas)
- Brier Score Global: {ev.get('brier', 'N/D')}
- Mercados Estrella (Mayor Fiabilidad Empírica): {json.dumps(mercados_estrella[:3], ensure_ascii=False)}
- Mercados en Vigilancia (Sensibles a Sobreconfianza): {json.dumps(mercados_vigilancia[:2], ensure_ascii=False)}

[MEMORIA EPISÓDICA CON ESTOS EQUIPOS]
- Historial con {eq_l or 'Local'}: {json.dumps({k: mem_l.get(k) for k in ('evaluables', 'ganadas', 'acierto_observado', 'casa')}, ensure_ascii=False) if mem_l.get('tiene_historial') else 'Sin registros previos'}
- Historial con {eq_v or 'Visitante'}: {json.dumps({k: mem_v.get(k) for k in ('evaluables', 'ganadas', 'acierto_observado', 'fuera')}, ensure_ascii=False) if mem_v.get('tiene_historial') else 'Sin registros previos'}
- Recuerdos manuales adicionales: {recuerdos_relevantes if recuerdos_relevantes else 'Ninguno'}

[DATOS DEL PARTIDO PARA ANALIZAR]
{json.dumps(datos_partido_json, indent=2, ensure_ascii=False)}

INSTRUCCIÓN CRÍTICA:
Realiza el cálculo de valor esperado (+EV) utilizando el modelo de Poisson y pondera la selección priorizando tus Mercados Estrella y la memoria episódica de ambos clubes. Devuelve la respuesta estrictamente en formato JSON válido con las claves requeridas:
- "partido"
- "mercado_elegido"
- "seleccion"
- "cuota"
- "periodo"
- "probabilidad_implicita"
- "probabilidad_real_estimada"
- "valor_esperado_ev"
- "explicacion_cuantitativa_detallada"
- "nivel_confianza"
- "decision_final"
- "mercados_evaluados"
"""

        respuesta = self.motor_neuronal.generar(
            prompt=prompt_cognitivo,
            system_instruction=self.identidad_base,
            temperature=0.2,
            json_mode=True,
            thinking_budget=512,
        )
        self.modelo = self.motor_neuronal.ultimo_modelo_exitoso
        self.fase_cognitiva = FaseCognitiva.REPOSO
        return respuesta

    # ───────────────────────────────────────────────────────────────────────────
    # 7. NÚCLEO AGÉNTICO ANTIGRAVITY (ORQUESTADOR MULTI-AGENTE Y AUTODIAGNÓSTICO)
    # ───────────────────────────────────────────────────────────────────────────

    @property
    def orquestador(self):
        """Retorna el OrquestadorAntigravity vinculado a esta conciencia."""
        if not hasattr(self, "_orquestador_antigravity") or self._orquestador_antigravity is None:
            from nucleo_antigravity import OrquestadorAntigravity
            self._orquestador_antigravity = OrquestadorAntigravity(conciencia=self)
        return self._orquestador_antigravity

    def ejecutar_mision_agentica(self, instruccion: str, usar_llm: bool = True) -> dict[str, Any]:
        """Ejecuta el bucle ReAct + equipo de sub-agentes sobre una instrucción o consulta."""
        self.fase_cognitiva = FaseCognitiva.RAZONANDO
        try:
            return self.orquestador.ejecutar_mision(instruccion=instruccion, usar_llm=usar_llm)
        finally:
            self.fase_cognitiva = FaseCognitiva.REPOSO

    def ejecutar_autodiagnostico(self, reparar: bool = True) -> dict[str, Any]:
        """Ejecuta el autodiagnóstico AST, Gatekeeper y auto-reparación del sistema."""
        self.fase_cognitiva = FaseCognitiva.CALIBRANDO
        try:
            return self.orquestador.autodiagnostico.ejecutar_diagnostico_integral(reparar=reparar)
        finally:
            self.fase_cognitiva = FaseCognitiva.REPOSO


# Singleton para compartir la misma memoria vivencial y pool neuronal en todo el sistema
_instancia_conciencia: AgenteConsciente | None = None


def obtener_agente_consciente() -> AgenteConsciente:
    """Retorna una instancia única compartida del AgenteConsciente."""
    global _instancia_conciencia
    if _instancia_conciencia is None:
        _instancia_conciencia = AgenteConsciente()
    return _instancia_conciencia


_CONCIENCIA_DISK_CACHE: tuple[tuple[int, float], AgenteConsciente] | None = None


def evaluar_familia_conciencia_cached(
    mercado: str,
    equipo: str | None = None,
    prob_bruta: float | None = None,
) -> dict[str, Any]:
    """
    Consulta rápida en disco (sin llamadas HTTP/LLM) de autoconciencia por
    familia, invalidando automáticamente cuando cambia algún evaluacion_*.json.
    """
    global _CONCIENCIA_DISK_CACHE
    try:
        eval_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "informes", "evaluacion")
        archivos = [
            os.path.join(eval_dir, f)
            for f in os.listdir(eval_dir)
            if f.startswith("evaluacion_") and f.endswith(".json")
        ] if os.path.isdir(eval_dir) else []
        sig = (len(archivos), max((os.path.getmtime(f) for f in archivos), default=0.0))
    except Exception:
        sig = (0, 0.0)

    if _CONCIENCIA_DISK_CACHE is None or _CONCIENCIA_DISK_CACHE[0] != sig:
        inst = AgenteConsciente.__new__(AgenteConsciente)
        inst.nombre = "Buddy Bot"
        inst.api_key = ""
        inst.memoria_vivencial = []
        inst.evaluacion_real = inst._cargar_evaluacion_real()
        _CONCIENCIA_DISK_CACHE = (sig, inst)
    return _CONCIENCIA_DISK_CACHE[1].evaluar_familia_conciencia(
        mercado=mercado, equipo=equipo, prob_bruta=prob_bruta
    )

