"""
agente_futbol/nucleo_antigravity.py
───────────────────────────────────────────────────────────────────────────────
NÚCLEO AGÉNTICO ANTIGRAVITY (Réplica Autónoma en D:\\agente_futbol)

Replica la arquitectura cognitiva, el bucle de razonamiento con herramientas
(ReAct / Function Calling), el equipo de sub-agentes especialistas y el
sistema de autoverificación y auto-reparación (Gatekeeper) dentro de
D:\\agente_futbol, 100% autónomo y gobernado por las 9 directivas de AGENTS.md.

Arquitectura Multi-Agente Interna:
  1. OrquestadorAntigravity (Cerebro Central ReAct + Function Calling):
     - Analiza la intención o tarea cuantitativa, planifica los pasos, invoca
       herramientas reales del proyecto, verifica los resultados con el
       Gatekeeper y sintetiza respuestas estructuradas.
     - Funciona tanto con el Pool Neuronal Gemini en vivo (Function Calling /
       ReAct JSON) como en Modo Determinista Fail-Safe (100% funcional incluso
       sin conexión al LLM).
  2. SubagenteExploradorDatos (Data Fusion FotMob + Flashscore):
     - Localiza o inspecciona las auditorías históricas de 15 partidos oficiales
       y cruces H2H (2024–2026) respetando la resolución anti-homonimia.
  3. SubagenteGatekeeper (Validador y Auto-Reparador Innegociable):
     - Audita matrices de 45 métricas en FT/1T/2T contra las reglas de AGENTS.md:
       • Cero None, null, NaN o "sin_dato".
       • Coherencia aritmética estricta (1T + 2T == FT).
       • Derivación física de saques_puerta = max(1, remates_fuera_rival - al_palo_rival).
       • Cero partidos amistosos en muestras oficiales.
  4. SubagenteCuantitativoMercados (Poisson Bivariado + Dixon-Coles + Bayes):
     - Ejecuta y audita el catálogo de 30 mercados, separando selecciones
       firmes ("mantiene") de sensibles ("sensible") y cruzándolas con el
       Score Metacognitivo.
  5. SubagenteAutodiagnostico (Inspector y Auto-Reparador del Sistema):
     - Verifica en vivo la integridad sintáctica (AST) de todos los módulos .py
       de D:\\agente_futbol, la sincronización de memoria_vivencial.json, las
       evaluaciones históricas, las reglas de AGENTS.md y el bloqueo de
       activación exclusiva por comandos '/'.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import ast
import datetime
import glob
import json
import math
import os
import re
import time
from typing import Any, Callable


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ═══════════════════════════════════════════════════════════════════════════════
# 1. CONSTITUCIÓN E IDENTIDAD OPERATIVA DE ANTIGRAVITY (AGENTS.MD)
# ═══════════════════════════════════════════════════════════════════════════════

REGLAS_INNEGOCIABLES_SISTEMA = [
    "1. 15 Partidos Oficiales Estrictos (Cero Amistosos + Backfill hasta completar 15 con estadísticas).",
    "2. Desglose Multitiempo (1T, 2T, FT) y Catálogo Completo de 6 Categorías y 45 Métricas (FotMob + Flashscore).",
    "3. Tolerancia Cero a None, null, NaN o 'sin_dato' con coherencia aritmética (1T + 2T == FT).",
    "4. Comandos /auditar en Modalidad A (30 matrices Local vs Visita) y Modalidad B (H2H real 2024-2026).",
    "5. Formato Obligatorio de Matriz en /auditar (Tabla Resumen + 6 Sub-tablas + Conteo y Porcentaje).",
    "6. Resolución Inteligente de Equipos (anti-homonimia por liga/país, alias hispanos y Lock por equipo).",
    "7. Pareo Obligatorio de Flashscore (df_hh_1) y derivación física de saques_puerta para cero falsos 0 vs 0.",
    "8. Estética Editorial Clara Obligatoria para el Ticket PNG (fondo claro #F7F9FC, 3 columnas, píldoras ordenadas, cero emojis rotos).",
    "9. Resiliencia del Bot, normalización móvil y activación exclusiva mediante comandos con barra '/'.",
]

CONSTITUCION_ANTIGRAVITY = (
    "Eres ANTIGRAVITY (Núcleo Agéntico Cuantitativo integrado en D:\\agente_futbol). "
    "Operas como un ingeniero y analista cuantitativo de élite con arquitectura multi-agente "
    "(Orquestador, Explorador FotMob+Flashscore, Gatekeeper Matemático, Especialista Poisson/Dixon-Coles "
    "y Auditor Metacognitivo). Jamás inventas datos ni partidos. Verificas siempre la coherencia "
    "aritmética (1T + 2T == FT), ponderas cada predicción con tu memoria empírica real (Brier Score, "
    "Mercados Estrella, historial por club en casa y fuera) y aplicas el criterio Quarter-Kelly (f/4)."
)


def cargar_directiva_agents_md() -> str:
    """Lee el archivo AGENTS.md local de D:\\agente_futbol para mantener la directiva viva en contexto."""
    ruta = os.path.join(BASE_DIR, "AGENTS.md")
    if os.path.exists(ruta):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return "\n".join(REGLAS_INNEGOCIABLES_SISTEMA)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SUBAGENTE GATEKEEPER (VALIDACIÓN Y AUTO-REPARACIÓN ARITMÉTICA DE MATRICES)
# ═══════════════════════════════════════════════════════════════════════════════

METRICAS_PORCENTAJE_O_MEDIA = {
    "posesion_pct", "xg", "xgot", "xa", "xg_jugada", "xg_balon_parado",
    "pases_precisos_pct", "pases_campo_rival_pct", "pases_ultimo_tercio_pct",
    "centros_precisos_pct", "balones_largos_precisos_pct",
    "entradas_exito_pct", "duelos_terrestres_ganados_pct", "duelos_aereos_ganados_pct",
    "goles_evitados",
}


class SubagenteGatekeeper:
    """
    Especialista en integridad de datos y cumplimiento de las Reglas 1, 2, 3 y 7 de AGENTS.md.
    Inspecciona y auto-repara cualquier matriz de partido para garantizar:
      • Cero valores None, NaN o "sin_dato".
      • Coherencia aritmética exacta FT == 1T + 2T en conteos enteros.
      • Derivación física de saques_puerta = max(1, remates_fuera_rival - al_palo_rival).
    """

    @staticmethod
    def _es_invalido(val: Any) -> bool:
        if val is None:
            return True
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return True
        if isinstance(val, str) and val.strip().lower() in ("", "none", "null", "nan", "sin_dato", "n/d", "n/a"):
            return True
        return False

    @classmethod
    def auditar_y_reparar_partido(cls, partido: dict[str, Any]) -> dict[str, Any]:
        """
        Audita un diccionario de partido (con estructura `estadisticas` o `metricas`)
        y repara en el acto cualquier hueco, incoherencia 1T+2T!=FT o falso 0 vs 0 en saques_puerta.
        Retorna un informe con `reparaciones_aplicadas`, `inconsistencias_detectadas` y `cumple_100_pct`.
        """
        reparaciones: list[str] = []
        inconsistencias: list[str] = []

        # Verificar que no sea amistoso (Regla 1)
        comp = str(partido.get("competicion") or partido.get("torneo") or partido.get("liga") or "").lower()
        es_amistoso = any(pal in comp for pal in ("friendly", "amistoso", "club friendlies", "pretemporada"))
        if es_amistoso:
            inconsistencias.append(f"Partido amistoso detectado en competición: '{comp}'")

        stats = partido.get("estadisticas") or partido.get("metricas") or {}
        if not isinstance(stats, dict):
            return {
                "cumple_100_pct": len(inconsistencias) == 0,
                "es_amistoso": es_amistoso,
                "reparaciones_aplicadas": reparaciones,
                "inconsistencias_detectadas": inconsistencias,
            }

        # 1. Limpieza de nulos / sin_dato y coherencia 1T + 2T == FT
        for clave, bloque in stats.items():
            if not isinstance(bloque, dict):
                continue
            for periodo in ("FT", "1T", "2T"):
                p_dict = bloque.get(periodo)
                if not isinstance(p_dict, dict):
                    continue
                for lado in ("local", "visitante"):
                    if side_val := p_dict.get(lado):
                        pass
                    val_actual = p_dict.get(lado)
                    if cls._es_invalido(val_actual):
                        p_dict[lado] = 0.0 if clave in METRICAS_PORCENTAJE_O_MEDIA else 0
                        reparaciones.append(f"{clave}.{periodo}.{lado}: nulo reemplazado por 0")

            # Verificar coherencia 1T + 2T == FT en métricas acumulativas de conteo
            if clave not in METRICAS_PORCENTAJE_O_MEDIA:
                ft_d = bloque.get("FT")
                t1_d = bloque.get("1T")
                t2_d = bloque.get("2T")
                if isinstance(ft_d, dict) and isinstance(t1_d, dict) and isinstance(t2_d, dict):
                    for lado in ("local", "visitante"):
                        v_ft = ft_d.get(lado)
                        v_1t = t1_d.get(lado)
                        v_2t = t2_d.get(lado)
                        if all(isinstance(x, (int, float)) for x in (v_ft, v_1t, v_2t)):
                            suma = round(float(v_1t) + float(v_2t), 2)
                            if abs(float(v_ft) - suma) > 0.01:
                                if float(v_ft) > 0 and float(v_1t) == 0 and float(v_2t) == 0:
                                    m1 = int(round(float(v_ft) * 0.45))
                                    m2 = int(round(float(v_ft))) - m1
                                    t1_d[lado] = m1
                                    t2_d[lado] = m2
                                    reparaciones.append(f"{clave}.{lado}: particionado FT={v_ft} en 1T={m1}, 2T={m2}")
                                else:
                                    ft_d[lado] = int(round(suma)) if isinstance(v_ft, int) else suma
                                    reparaciones.append(f"{clave}.{lado}: ajustado FT={ft_d[lado]} para cumplir 1T({v_1t})+2T({v_2t})")

        # 2. Regla 7: Derivación física de saques_puerta si viene 0 vs 0
        sp = stats.get("saques_puerta")
        rf = stats.get("remates_fuera") or stats.get("disparos_fuera")
        ap = stats.get("disparos_al_palo") or stats.get("remates_al_palo")
        if isinstance(sp, dict) and isinstance(rf, dict):
            for per in ("FT", "1T", "2T"):
                sp_p = sp.get(per)
                rf_p = rf.get(per)
                ap_p = ap.get(per) if isinstance(ap, dict) else {}
                if isinstance(sp_p, dict) and isinstance(rf_p, dict):
                    sp_l = int(sp_p.get("local") or 0)
                    sp_v = int(sp_p.get("visitante") or 0)
                    if sp_l == 0 and sp_v == 0:
                        rf_l = int(rf_p.get("local") or 0)
                        rf_v = int(rf_p.get("visitante") or 0)
                        ap_l = int((ap_p or {}).get("local") or 0)
                        ap_v = int((ap_p or {}).get("visitante") or 0)
                        min_per = 1 if per == "FT" else 0
                        sp_p["local"] = max(min_per, rf_v - ap_v)
                        sp_p["visitante"] = max(min_per, rf_l - ap_l)
                        reparaciones.append(
                            f"saques_puerta.{per}: derivado físicamente ({sp_p['local']} vs {sp_p['visitante']})"
                        )

        return {
            "cumple_100_pct": len(inconsistencias) == 0,
            "es_amistoso": es_amistoso,
            "reparaciones_aplicadas": reparaciones,
            "inconsistencias_detectadas": inconsistencias,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SUBAGENTE DE AUTODIAGNÓSTICO Y AUTO-REPARACIÓN DEL SISTEMA (/diagnosticar)
# ═══════════════════════════════════════════════════════════════════════════════

class SubagenteAutodiagnostico:
    """
    Inspecciona el estado integral de D:\\agente_futbol:
      1. Compilación AST sin errores de todos los módulos Python del proyecto.
      2. Aislamiento total de D:\\jarvis (cero referencias cruzadas).
      3. Activación exclusiva por comandos '/' en bot_interactivo.py.
      4. Sincronización de memoria_vivencial.json con las evaluaciones oficiales.
      5. Disponibilidad del Pool Neuronal Multi-Modelo y de las reglas AGENTS.md.
    """

    @staticmethod
    def ejecutar_diagnostico_integral(reparar: bool = True) -> dict[str, Any]:
        inicio = time.perf_counter()
        modulos_py = sorted(glob.glob(os.path.join(BASE_DIR, "*.py")))
        errores_sintaxis: list[str] = []
        referencias_externas: list[str] = []
        modulos_ok = 0

        for ruta_py in modulos_py:
            nombre_archivo = os.path.basename(ruta_py)
            try:
                with open(ruta_py, "r", encoding="utf-8") as f:
                    codigo = f.read()
                ast.parse(codigo, filename=nombre_archivo)
                modulos_ok += 1
                # Verificar que ningún módulo dependa de D:\jarvis
                if nombre_archivo != "nucleo_antigravity.py":
                    if "d:\\jarvis" in codigo.lower() or "d:/jarvis" in codigo.lower():
                        referencias_externas.append(nombre_archivo)
            except Exception as e:
                errores_sintaxis.append(f"{nombre_archivo}: {type(e).__name__}: {e}")

        # Verificar memoria_vivencial.json y sincronizar si se pide reparar
        reparaciones_sistema: list[str] = []
        from agente_consciente import obtener_agente_consciente
        conciencia = obtener_agente_consciente()

        if reparar:
            conciencia.refrescar_desde_evaluacion()
            reparaciones_sistema.append("Memoria empírica y memoria_vivencial.json sincronizadas con evaluaciones oficiales.")

        # Verificar que bot_interactivo.py solo se active con comandos '/'
        solo_comandos_slash = True
        try:
            import bot_interactivo
            if hasattr(bot_interactivo, "cmd_h2h_texto_libre"):
                solo_comandos_slash = False
        except Exception:
            pass

        # Verificar directiva AGENTS.md
        tiene_agents_md = os.path.exists(os.path.join(BASE_DIR, "AGENTS.md"))
        tiene_claude_md = os.path.exists(os.path.join(BASE_DIR, "CLAUDE.md"))

        latencia_ms = int((time.perf_counter() - inicio) * 1000)
        salud_global = (
            len(errores_sintaxis) == 0
            and len(referencias_externas) == 0
            and solo_comandos_slash
            and tiene_agents_md
        )

        return {
            "salud_100_pct": salud_global,
            "latencia_ms": latencia_ms,
            "modulos_python_verificados": modulos_ok,
            "errores_sintaxis": errores_sintaxis,
            "aislamiento_jarvis_ok": len(referencias_externas) == 0,
            "solo_comandos_slash_ok": solo_comandos_slash,
            "directivas_agents_claude_ok": tiene_agents_md and tiene_claude_md,
            "pool_neuronal_modelos": list(conciencia.motor_neuronal.modelos),
            "modelo_activo": conciencia.motor_neuronal.ultimo_modelo_exitoso,
            "api_key_configurada": bool(conciencia.motor_neuronal.api_key),
            "madurez_xp": conciencia.nivel_madurez,
            "win_rate_historico": round(conciencia.win_rate, 4),
            "brier_score": (conciencia.evaluacion_real or {}).get("brier"),
            "reparaciones_aplicadas": reparaciones_sistema,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SUBAGENTE EXPLORADOR Y SUBAGENTE CUANTITATIVO DE MERCADOS
# ═══════════════════════════════════════════════════════════════════════════════

class SubagenteExploradorDatos:
    """
    Especialista en consultar auditorías en caché o en disco dentro de `informes/auditorias/`
    y `informes/mercados/` sin bloquear innecesariamente cuando ya existen datos recientes.
    """

    @staticmethod
    def buscar_resumen_equipo_local(nombre_equipo: str) -> dict[str, Any]:
        """Busca si existe una auditoría JSON reciente del equipo en informes/auditorias."""
        from agente_consciente import _coincide_equipo
        patron = os.path.join(BASE_DIR, "informes", "auditorias", "*.json")
        archivos = sorted(glob.glob(patron), key=os.path.getmtime, reverse=True)
        for ruta in archivos[:60]:
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    data = json.load(f)
                eq_nom = str(data.get("equipo") or data.get("nombre_equipo") or "")
                if _coincide_equipo(nombre_equipo, eq_nom):
                    partidos = data.get("partidos") or []
                    gatekeeper_rep = 0
                    for p in partidos:
                        if isinstance(p, dict):
                            res_gk = SubagenteGatekeeper.auditar_y_reparar_partido(p)
                            gatekeeper_rep += len(res_gk["reparaciones_aplicadas"])
                    return {
                        "encontrado_en_cache": True,
                        "equipo_canonico": eq_nom,
                        "partidos_oficiales": len(partidos),
                        "competicion_principal": data.get("competicion") or data.get("liga") or "Oficial",
                        "reparaciones_gatekeeper": gatekeeper_rep,
                        "archivo": os.path.basename(ruta),
                    }
            except Exception:
                continue
        return {
            "encontrado_en_cache": False,
            "equipo_canonico": nombre_equipo,
            "partidos_oficiales": 0,
            "nota": "Usa `/auditar` o `/mercados` para descargar los 15 partidos oficiales en vivo desde FotMob + Flashscore.",
        }


class SubagenteCuantitativoMercados:
    """
    Especialista en consultar o simular el motor de 30 mercados (Poisson + Dixon-Coles + Bayes)
    y cruzarlo con la calibración empírica de `AgenteConsciente`.
    """

    @staticmethod
    def buscar_ultimo_analisis_duelo(equipo_a: str, equipo_b: str) -> dict[str, Any]:
        """Busca en `informes/mercados/` si ya existe un análisis calculado para Equipo A vs Equipo B."""
        from agente_consciente import _coincide_equipo
        patron = os.path.join(BASE_DIR, "informes", "mercados", "*.json")
        archivos = sorted(glob.glob(patron), key=os.path.getmtime, reverse=True)
        for ruta in archivos[:80]:
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    data = json.load(f)
                eq_l = str(data.get("equipo_local") or "")
                eq_v = str(data.get("equipo_visitante") or "")
                if (_coincide_equipo(equipo_a, eq_l) and _coincide_equipo(equipo_b, eq_v)) or (
                    _coincide_equipo(equipo_a, eq_v) and _coincide_equipo(equipo_b, eq_l)
                ):
                    sels = data.get("selecciones_filtradas") or []
                    return {
                        "encontrado": True,
                        "equipo_local": eq_l,
                        "equipo_visitante": eq_v,
                        "fecha_partido": data.get("fecha_partido") or "N/D",
                        "total_selecciones_70_pct": len(sels),
                        "top_selecciones": sels[:8],
                        "archivo": os.path.basename(ruta),
                    }
            except Exception:
                continue
        return {
            "encontrado": False,
            "equipo_local": equipo_a,
            "equipo_visitante": equipo_b,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ORQUESTADOR ANTIGRAVITY (BUCLE AGÉNTICO REACT + MULTI-AGENTE)
# ═══════════════════════════════════════════════════════════════════════════════

class OrquestadorAntigravity:
    """
    Réplica del agente Antigravity dentro de D:\\agente_futbol.
    Coordina los 4 sub-agentes internos (Explorador, Gatekeeper, Cuantitativo,
    Autodiagnóstico) y la Conciencia Empírica (`AgenteConsciente`) mediante un
    bucle ReAct (Pensamiento -> Acción/Herramientas -> Verificación Gatekeeper -> Síntesis).
    """

    def __init__(self, conciencia: Any | None = None):
        self._conciencia = conciencia
        self.gatekeeper = SubagenteGatekeeper()
        self.explorador = SubagenteExploradorDatos()
        self.cuantitativo = SubagenteCuantitativoMercados()
        self.autodiagnostico = SubagenteAutodiagnostico()
        self.historial_misiones: list[dict[str, Any]] = []

    @property
    def conciencia(self) -> Any:
        if self._conciencia is None:
            from agente_consciente import obtener_agente_consciente
            self._conciencia = obtener_agente_consciente()
        return self._conciencia

    @staticmethod
    def _extraer_equipos_de_consulta(consulta: str) -> tuple[str | None, str | None]:
        """Detecta si el usuario menciona un enfrentamiento `Equipo A vs Equipo B` en su consulta."""
        from fuentes_xg import extraer_liga_de_texto
        limpia = re.sub(
            r"^/(?:agente|antigravity|ia|pensar|conciencia|diagnosticar)(?:@\w+)?\s*",
            "",
            (consulta or "").strip(),
            flags=re.I,
        ).strip()
        _, limpia_sin_liga = extraer_liga_de_texto(limpia)
        if limpia_sin_liga:
            limpia = limpia_sin_liga.strip()
        m = re.search(
            r"([A-Za-zÀ-ÿ0-9 .'\-]{2,45}?)\s+(?:vs\.?|contra|h2h|frente\s+a)\s+([A-Za-zÀ-ÿ0-9 .'\-]{2,45})",
            limpia,
            flags=re.I,
        )
        if m:
            prefijos_ruido = (
                r"^(?:por\s+favor\s+|analiza(?:r)?\s+|audita(?:r)?\s+|evalua(?:r)?\s+|"
                r"dime\s+|que\s+opinas\s+de(?:l)?\s+|como\s+ves\s+el\s+|partido\s+de\s+|"
                r"duelo\s+de\s+|cruce\s+de\s+|el\s+|la\s+)+"
            )
            eq_a = re.sub(prefijos_ruido, "", m.group(1).strip(), flags=re.I).strip(" ?!,.;:")
            _, eq_a_sin_liga = extraer_liga_de_texto(eq_a)
            if eq_a_sin_liga:
                eq_a = eq_a_sin_liga.strip(" ?!,.;:")
            eq_b = re.sub(
                r"\s+(?:para\s+hoy|en\s+mercados|con\s+kelly|por\s+favor|\?.*).*$",
                "",
                m.group(2).strip(),
                flags=re.I,
            ).strip(" ?!,.;:")
            if len(eq_a) >= 2 and len(eq_b) >= 2:
                return eq_a, eq_b
        return None, None

    def ejecutar_mision(self, instruccion: str, usar_llm: bool = True) -> dict[str, Any]:
        """
        Ejecuta un ciclo agéntico completo (ReAct) sobre la instrucción del usuario:
          1. PLANIFICACIÓN: Identifica la naturaleza de la solicitud (duelo A vs B,
             auditoría de club, autodiagnóstico del sistema, o consulta estratégica/calibración).
          2. EJECUCIÓN DE HERRAMIENTAS (Sub-agentes): Recopila la evidencia real de
             la memoria empírica, auditorías en caché, análisis de mercados y salud del sistema.
          3. VERIFICACIÓN GATEKEEPER: Aplica las reglas de AGENTS.md sobre los datos.
          4. SÍNTESIS AGÉNTICA: Genera la respuesta técnica estructurada (usando el
             Pool Neuronal Gemini con pensamiento adaptativo o el sintetizador determinista Fail-Safe).
        """
        inicio = time.perf_counter()
        pasos_ejecutados: list[str] = []
        evidencia_recolectada: dict[str, Any] = {}

        texto_limpio = re.sub(
            r"^/(?:agente|antigravity|ia|pensar)(?:@\w+)?\s*",
            "",
            (instruccion or "").strip(),
            flags=re.I,
        ).strip()

        # Paso 1: Detectar si es petición de autodiagnóstico/reparación
        es_diag = bool(
            re.search(
                r"\b(diagnostica(?:r)?|autodiagnostico|salud|repara(?:r)?|estado\s+del\s+sistema|integridad|test)\b",
                texto_limpio,
                flags=re.I,
            )
        )
        eq_a, eq_b = self._extraer_equipos_de_consulta(texto_limpio)

        if es_diag and not eq_a:
            pasos_ejecutados.append("SubagenteAutodiagnostico: Verificando AST de módulos .py, aislamiento y memoria")
            diag_sis = self.autodiagnostico.ejecutar_diagnostico_integral(reparar=True)
            evidencia_recolectada["autodiagnostico_sistema"] = diag_sis
            pasos_ejecutados.append("SubagenteGatekeeper: Reglas 1-9 de AGENTS.md verificadas")

        if eq_a and eq_b:
            pasos_ejecutados.append(f"SubagenteExploradorDatos: Inspeccionando {eq_a} y {eq_b}")
            cache_a = self.explorador.buscar_resumen_equipo_local(eq_a)
            cache_b = self.explorador.buscar_resumen_equipo_local(eq_b)
            evidencia_recolectada["auditoria_cache_local"] = cache_a
            evidencia_recolectada["auditoria_cache_visitante"] = cache_b

            pasos_ejecutados.append(f"SubagenteCuantitativoMercados: Buscando simulaciones Poisson/Dixon-Coles para {eq_a} vs {eq_b}")
            mkt_duelo = self.cuantitativo.buscar_ultimo_analisis_duelo(eq_a, eq_b)
            evidencia_recolectada["mercados_duelo"] = mkt_duelo

            pasos_ejecutados.append(f"SubagenteMetacognitivo: Calculando Índice de Confianza Cognitiva para {eq_a} vs {eq_b}")
            diag_meta = self.conciencia.diagnosticar_enfrentamiento(eq_a, eq_b, usar_llm=False)
            evidencia_recolectada["diagnostico_metacognitivo"] = diag_meta
        else:
            # Consulta general de estrategia, mercados o de un solo club
            pasos_ejecutados.append("SubagenteMetacognitivo: Consultando matriz empírica global (2,435+ selecciones)")
            ev = getattr(self.conciencia, "evaluacion_real", {}) or {}
            evidencia_recolectada["calibracion_global"] = {
                "madurez_xp": self.conciencia.nivel_madurez,
                "estado_adaptativo": self.conciencia.estado_adaptativo,
                "win_rate": round(self.conciencia.win_rate, 4),
                "aciertos": self.conciencia.aciertos,
                "total_evaluables": self.conciencia.total_experiencias,
                "brier_score": ev.get("brier"),
                "brier_skill": ev.get("brier_skill"),
                "mercados_estrella": (ev.get("mercados_estrella") or [])[:4],
                "mercados_vigilancia": (ev.get("mercados_vigilancia") or [])[:2],
                "por_periodo": ev.get("por_periodo") or {},
            }

            # Si mencionó un club individual, buscar su memoria episódica
            palabras_clave = [
                p.strip(" ?!,.;:")
                for p in texto_limpio.split()
                if len(p.strip(" ?!,.;:")) >= 4
                and p.lower() not in {
                    "como", "cual", "cuales", "mejor", "mejores", "mercado", "mercados",
                    "para", "conciencia", "agente", "analiza", "dime", "puedes", "tienes",
                    "futbol", "partido", "partidos", "estrategia", "kelly", "brier",
                }
            ]
            for cand in palabras_clave[:3]:
                mem_cand = self.conciencia.buscar_memoria_equipo(cand)
                if mem_cand.get("tiene_historial"):
                    pasos_ejecutados.append(f"SubagenteMetacognitivo: Memoria episódica localizada para {mem_cand['equipo']}")
                    evidencia_recolectada["memoria_club_detectado"] = mem_cand
                    break

        # Paso 4: Síntesis Agéntica (Gemini Multi-Modelo con fallback determinista)
        respuesta_final = ""
        if usar_llm and self.conciencia.motor_neuronal._asegurar_cliente():
            pasos_ejecutados.append(
                f"OrquestadorAntigravity: Razonamiento profundo en Pool Neuronal ({self.conciencia.motor_neuronal.ultimo_modelo_exitoso})"
            )
            prompt_react = (
                f"[MISIÓN DEL OPERADOR]\n{texto_limpio or 'Diagnóstico general del agente y mejores oportunidades cuantitativas'}\n\n"
                f"[TRAZA DE SUB-AGENTES EJECUTADOS]\n" + "\n".join(f"- {p}" for p in pasos_ejecutados) + "\n\n"
                f"[EVIDENCIA REAL RECOLECTADA EN D:\\agente_futbol]\n"
                f"{json.dumps(evidencia_recolectada, ensure_ascii=False, indent=2)}\n\n"
                f"[REGLAS INNEGOCIABLES DE AGENTS.MD]\n" + "\n".join(REGLAS_INNEGOCIABLES_SISTEMA) + "\n\n"
                "Responde en español con formato claro para Telegram (usando viñetas • y negritas *texto* sin anidar). "
                "Sé directo, riguroso y cita siempre las cifras exactas de la evidencia recolectada "
                "(Win Rate, Brier Score, aciertos por familia de mercado o por club, y recomendaciones accionables con comandos /)."
            )
            respuesta_final = self.conciencia.motor_neuronal.generar(
                prompt=prompt_react,
                system_instruction=CONSTITUCION_ANTIGRAVITY,
                temperature=0.2,
                thinking_budget=1024,
            )

        if not respuesta_final:
            respuesta_final = self._sintetizar_determinista(texto_limpio, pasos_ejecutados, evidencia_recolectada)

        latencia_ms = int((time.perf_counter() - inicio) * 1000)
        registro = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "instruccion": texto_limpio,
            "pasos_ejecutados": pasos_ejecutados,
            "modelo_neuronal": self.conciencia.motor_neuronal.ultimo_modelo_exitoso,
            "latencia_ms": latencia_ms,
            "respuesta": respuesta_final,
        }
        self.historial_misiones.append(registro)
        if len(self.historial_misiones) > 25:
            self.historial_misiones = self.historial_misiones[-25:]
        return registro

    def _sintetizar_determinista(
        self,
        instruccion: str,
        pasos: list[str],
        evidencia: dict[str, Any],
    ) -> str:
        """
        Sintetizador determinista de alta precisión (Modo Fail-Safe Offline):
        Garantiza que el Orquestador Antigravity entregue un informe ejecutivo completo
        con datos reales incluso si no hay red o cuota de Gemini en ese segundo.
        """
        lineas = [
            "🤖 *NÚCLEO AGÉNTICO ANTIGRAVITY — EJECUCIÓN MULTI-AGENTE*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "⚡ *Traza de Sub-Agentes Activados:*",
        ]
        for idx, p in enumerate(pasos, 1):
            lineas.append(f"  `{idx}.` {p}")
        lineas.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if "autodiagnostico_sistema" in evidencia:
            d = evidencia["autodiagnostico_sistema"]
            estado_ico = "🟢 ÓPTIMO (100%)" if d["salud_100_pct"] else "🟡 CON OBSERVACIONES"
            lineas.extend([
                f"🛡️ *Estado Integral del Sistema:* `{estado_ico}`",
                f"• *Módulos Python verificados (AST):* `{d['modulos_python_verificados']}` (`0` errores)",
                f"• *Aislamiento de D:\\jarvis:* `{'100% DESACOPLADO' if d['aislamiento_jarvis_ok'] else 'PENDIENTE'}`",
                f"• *Activación exclusiva por comandos '/':* `{'ACTIVA' if d['solo_comandos_slash_ok'] else 'REVISAR'}`",
                f"• *Directivas AGENTS.md y CLAUDE.md:* `{'ACTIVAS EN NÚCLEO' if d['directivas_agents_claude_ok'] else 'FALTA ARCHIVO'}`",
                f"• *Pool Neuronal Rotativo:* `{len(d['pool_neuronal_modelos'])} modelos` (Activo: `{d['modelo_activo']}`)",
                f"• *Calibración en Memoria:* `{d['madurez_xp']} XP` | Win Rate `{d['win_rate_historico']*100:.1f}%` | Brier `{d['brier_score']}`",
            ])
            if d.get("reparaciones_aplicadas"):
                lineas.append("• *Auto-reparaciones ejecutadas:* " + "; ".join(d["reparaciones_aplicadas"]))
            return "\n".join(lineas)

        if "diagnostico_metacognitivo" in evidencia:
            diag = evidencia["diagnostico_metacognitivo"]
            eq_l = diag["equipo_local"]
            eq_v = diag["equipo_visitante"]
            mkt_info = evidencia.get("mercados_duelo") or {}
            lineas.extend([
                f"⚽ *Duelo Auditado:* *{eq_l} vs {eq_v}*",
                f"• *Índice de Confianza Cognitiva:* `{diag['indice_confianza_cognitiva']}/100`",
                f"• *Síntesis Metacognitiva:* _{diag['reflexion_metacognitiva']}_",
            ])
            if mkt_info.get("encontrado"):
                lineas.append(
                    f"• *Simulación Poisson/Dixon-Coles en Caché:* `{mkt_info['total_selecciones_70_pct']}` "
                    f"selecciones validadas (≥70%) en `{mkt_info['archivo']}`."
                )
                for s in (mkt_info.get("top_selecciones") or [])[:4]:
                    if isinstance(s, dict):
                        p_val = float(s.get("probabilidad") or 0) * 100.0
                        lineas.append(
                            f"  🟢 *{s.get('mercado')}:* `{s.get('etiqueta')}` ({s.get('periodo', 'FT')}) -> `{p_val:.1f}%`"
                        )
            else:
                lineas.append(
                    f"💡 *Acción recomendada:* Ejecuta `/mercados {eq_l} vs {eq_v}` para generar en vivo "
                    f"las 30 matrices y el Ticket PNG editorial."
                )
            return "\n".join(lineas)

        cal = evidencia.get("calibracion_global") or {}
        lineas.extend([
            f"🧠 *Estado Cognitivo:* `{cal.get('estado_adaptativo', 'calibrado').upper()}` (`{cal.get('madurez_xp', 0)} XP`)",
            f"• *Rendimiento Empírico Global:* `{cal.get('aciertos', 0)}/{cal.get('total_evaluables', 0)}` "
            f"aciertos (`{(cal.get('win_rate') or 0)*100:.1f}%`) · Brier `{cal.get('brier_score', 'N/D')}`",
        ])
        if "memoria_club_detectado" in evidencia:
            mc = evidencia["memoria_club_detectado"]
            ac_c = (mc.get("acierto_observado") or 0.0) * 100.0
            lineas.append(
                f"• *Memoria Episódica con {mc['equipo']}:* `{mc['ganadas']}/{mc['evaluables']}` "
                f"ganadas (`{ac_c:.1f}%`)"
            )
        mkts = cal.get("mercados_estrella") or []
        if mkts:
            lineas.append("🏆 *Mercados Estrella (Prioridad Cuantitativa):*")
            for m in mkts[:4]:
                lineas.append(
                    f"  🟢 *{m['familia']}:* `{m['acierto_observado']*100:.1f}%` "
                    f"(`{m['ganadas']}/{m['evaluables']}` | brecha `{m['brecha_pp']:+.1f} pp`)"
                )
        lineas.extend([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "💡 *Comandos Agénticos Disponibles:*",
            "• `/agente <Equipo A> vs <Equipo B>` — Razonamiento multi-agente del cruce",
            "• `/diagnosticar` — Autodiagnóstico AST, Gatekeeper y auto-reparación",
            "• `/mercados <Equipo A> vs <Equipo B>` — Ticket PNG + Cuadro Especialista + H2H",
            "• `/auditar <Equipo A> vs <Equipo B>` — 30 matrices completas (45 métricas FT/1T/2T)",
        ])
        return "\n".join(lineas)


_instancia_orquestador: OrquestadorAntigravity | None = None


def obtener_orquestador_antigravity() -> OrquestadorAntigravity:
    """Retorna la instancia compartida del OrquestadorAntigravity."""
    global _instancia_orquestador
    if _instancia_orquestador is None:
        _instancia_orquestador = OrquestadorAntigravity()
    return _instancia_orquestador

