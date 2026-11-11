"""
auditoria_comparable.py
───────────────────────────────────────────────────────────────────────────────
Puntos 44 y 45: Emparejamiento por Rango de Recencia, Vista Comparable Simétrica
y Prohibición de Ejecutar Modelos sobre Subconjuntos Rellenados (Secciones 3 y 4).
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import copy
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple


RUTA_MODELOS_VALIDADOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modelos_validados.yaml")


def cargar_modelos_validados(ruta: Optional[str] = None) -> Dict[str, Any]:
    """
    Carga el archivo modelos_validados.yaml con un parser seguro y determinista
    que no depende de librerías externas.
    """
    path = ruta or RUTA_MODELOS_VALIDADOS
    if not os.path.isfile(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        contenido = f.read()

    modelos: Dict[str, Any] = {}
    modelo_actual: Optional[str] = None
    seccion_actual: Optional[str] = None

    for linea in contenido.splitlines():
        linea_limpia = linea.split("#")[0].rstrip()
        if not linea_limpia.strip():
            continue

        indent = len(linea_limpia) - len(linea_limpia.lstrip())
        texto = linea_limpia.strip()

        # Nivel raíz: "modelos:"
        if texto == "modelos:":
            continue

        # Nivel 1: "  red_recursiva_45_dcn:"
        if indent == 2 and texto.endswith(":"):
            modelo_actual = texto[:-1].strip()
            modelos[modelo_actual] = {}
            seccion_actual = None
            continue

        if not modelo_actual:
            continue

        # Nivel 2: lista o clave-valor dentro de modelo
        if texto.startswith("- "):
            val = texto[2:].strip().strip("\"'")
            if seccion_actual and isinstance(modelos[modelo_actual].get(seccion_actual), list):
                modelos[modelo_actual][seccion_actual].append(val)
            continue

        if ":" in texto:
            k, v = texto.split(":", 1)
            k = k.strip().strip("\"'")
            v = v.strip()
            if not v:
                seccion_actual = k
                modelos[modelo_actual][k] = []
            else:
                seccion_actual = None
                if v.startswith("[") and v.endswith("]"):
                    items = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
                    # Convertir a int si todos son dígitos
                    if items and all(re.match(r"^\d+$", it) for it in items):
                        modelos[modelo_actual][k] = [int(it) for it in items]
                    else:
                        modelos[modelo_actual][k] = items
                elif v.lower() == "true":
                    modelos[modelo_actual][k] = True
                elif v.lower() == "false":
                    modelos[modelo_actual][k] = False
                elif re.match(r"^-?\d+$", v):
                    modelos[modelo_actual][k] = int(v)
                elif re.match(r"^-?\d+\.\d+$", v):
                    modelos[modelo_actual][k] = float(v)
                else:
                    modelos[modelo_actual][k] = v.strip("\"'")

    return modelos


def es_celda_observada_valida(
    fila: Dict[str, Any],
    periodo: str,
    partido: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Reglas estrictas de validación de celda observada (Fase 3 / Fallback):
    1. None no cuenta: si equipo es None, no cuenta como observado.
    2. Cero publicado sí cuenta: 0 o 0.0 cuenta como métrica observada real.
    3. estimado=True no suma a observadas: si estimado/es_estimado es True o procedencia es 'estimado', no suma.
    4. En 1T/2T sin procedencia por celda verificada: no se acredita cobertura de mitades
       (evita validar repartos sintéticos 50/50 o archivos sin trazabilidad de mitades).
    """
    if not isinstance(fila, dict):
        return False
    val = fila.get("equipo")
    if val is None:
        return False
    if fila.get("estimado") or fila.get("es_estimado"):
        return False

    proc_fila = str(fila.get("procedencia") or fila.get("fuente") or "").lower().strip()
    if proc_fila in ("estimado", "estimacion", "estimacion_modelo", "sintetico", "reparto_50_50", "imputado"):
        return False

    if periodo in ("1T", "2T"):
        proc_celda = proc_fila
        if not proc_celda and isinstance(partido, dict):
            proc_root = partido.get("procedencia")
            if isinstance(proc_root, dict):
                proc_per = proc_root.get(periodo)
                if isinstance(proc_per, dict):
                    proc_celda = str(proc_per.get("equipo", {}).get(fila.get("clave"), "")).lower().strip()
                if not proc_celda:
                    proc_celda = str(proc_root.get("equipo", {}).get(fila.get("clave"), "")).lower().strip()
            elif isinstance(proc_root, str):
                proc_celda = proc_root.lower().strip()

        if not proc_celda or proc_celda in (
            "sin_procedencia", "legado_sin_procedencia", "estimado", "sintetico", "reparto_50_50", "desconocido"
        ):
            return False

    return True


def emparejar_historicos_por_recencia(
    partidos_a: List[Dict[str, Any]],
    partidos_b: List[Dict[str, Any]],
    max_filas: int = 15,
) -> List[Dict[str, Any]]:
    """
    Sección 3: Empareja ambos historiales fila por fila del más reciente al más antiguo (1 a 15).
    Aplica la intersección independiente por periodo:
        claves_comunes[i, periodo] = claves_observadas_A[i, periodo] ∩ claves_observadas_B[i, periodo]
    """
    emparejamientos: List[Dict[str, Any]] = []
    n_filas = max(len(partidos_a), len(partidos_b), max_filas)
    n_filas = min(n_filas, max_filas)

    for i in range(n_filas):
        pa = partidos_a[i] if i < len(partidos_a) else None
        pb = partidos_b[i] if i < len(partidos_b) else None

        info_fila: Dict[str, Any] = {
            "fila": i + 1,
            "partido_a": pa,
            "partido_b": pb,
            "hay_partido_a": pa is not None,
            "hay_partido_b": pb is not None,
            "periodos": {},
        }

        # Analizar cada periodo
        for per in ("FT", "1T", "2T"):
            obs_a: Set[str] = set()
            obs_b: Set[str] = set()
            if pa:
                res_a = (pa.get("resumen_claves") or {}).get(per, {}).get("equipo", [])
                if res_a:
                    obs_a = set(res_a)
                else:
                    obs_a = {
                        f.get("clave")
                        for f in (pa.get("periodos", {}).get(per, {}).get("filas", []))
                        if f.get("clave") and es_celda_observada_valida(f, per, pa)
                    }
            if pb:
                res_b = (pb.get("resumen_claves") or {}).get(per, {}).get("equipo", [])
                if res_b:
                    obs_b = set(res_b)
                else:
                    obs_b = {
                        f.get("clave")
                        for f in (pb.get("periodos", {}).get(per, {}).get("filas", []))
                        if f.get("clave") and es_celda_observada_valida(f, per, pb)
                    }

            comunes = sorted(obs_a & obs_b)
            exclusivas_a = sorted(obs_a - obs_b)
            exclusivas_b = sorted(obs_b - obs_a)

            tiene_m_a = pa.get("goles_equipo") is not None or (isinstance(pa.get("marcador"), dict) and pa.get("marcador", {}).get("equipo") is not None) if pa else False
            tiene_m_b = pb.get("goles_equipo") is not None or (isinstance(pb.get("marcador"), dict) and pb.get("marcador", {}).get("equipo") is not None) if pb else False

            if pa is None or pb is None:
                uso = "Sin partido emparejado"
            elif comunes:
                uso = f"A y B usan las mismas {len(comunes)} claves"
            elif tiene_m_a and tiene_m_b and per == "FT":
                uso = "Solo historial de resultados, si hay marcador"
            else:
                uso = f"Sin comparación estadística en {per} para esta fila"

            info_fila["periodos"][per] = {
                "a_observado": len(obs_a),
                "b_observado": len(obs_b),
                "claves_comunes": comunes,
                "n_comunes": len(comunes),
                "exclusivas_a": exclusivas_a,
                "exclusivas_b": exclusivas_b,
                "uso": uso,
            }

        emparejamientos.append(info_fila)

    return emparejamientos


def construir_vista_comparable(
    auditoria_a: Dict[str, Any],
    auditoria_b: Dict[str, Any],
    min_claves_ft_fila: int = 5,
    max_filas: int = 15,
) -> Dict[str, Any]:
    """
    Construye la Vista Comparable simétrica hacia el motor cuantitativo:
      1. El objeto contiene exclusivamente las claves comunes de cada par fila por fila.
      2. No muta la extracción original (se preserva intacta).
      3. Invertir el orden (B vs A) produce exactamente la misma intersección de claves comunes.
    """
    partidos_a = auditoria_a.get("partidos", []) or []
    partidos_b = auditoria_b.get("partidos", []) or []

    emparejamientos = emparejar_historicos_por_recencia(partidos_a, partidos_b, max_filas=max_filas)

    # Proyección inmutable simétrica
    proyeccion_a: List[Dict[str, Any]] = []
    proyeccion_b: List[Dict[str, Any]] = []

    # Métricas agregadas de pares con dato
    n_pares_con_dato: Dict[str, int] = {}
    filas_con_claves_comunes_suficientes = 0

    for idx, emp in enumerate(emparejamientos):
        comunes_ft = emp["periodos"]["FT"]["claves_comunes"]
        if len(comunes_ft) >= min_claves_ft_fila:
            filas_con_claves_comunes_suficientes += 1

        for c in comunes_ft:
            n_pares_con_dato[c] = n_pares_con_dato.get(c, 0) + 1

        # Construir copia limpia para A conteniendo solo comunes
        pa = emp["partido_a"]
        if pa:
            pa_clean = copy.deepcopy(pa)
            periodos_clean = {}
            for per, mat in (pa.get("periodos") or {}).items():
                comunes_per = set(emp["periodos"].get(per, {}).get("claves_comunes", []))
                filas_filtradas = [
                    f for f in (mat.get("filas") or [])
                    if f.get("clave") in comunes_per
                ]
                periodos_clean[per] = {"filas": filas_filtradas}
            pa_clean["periodos"] = periodos_clean
            proyeccion_a.append(pa_clean)

        # Construir copia limpia para B conteniendo solo comunes
        pb = emp["partido_b"]
        if pb:
            pb_clean = copy.deepcopy(pb)
            periodos_clean = {}
            for per, mat in (pb.get("periodos") or {}).items():
                comunes_per = set(emp["periodos"].get(per, {}).get("claves_comunes", []))
                filas_filtradas = [
                    f for f in (mat.get("filas") or [])
                    if f.get("clave") in comunes_per
                ]
                periodos_clean[per] = {"filas": filas_filtradas}
            pb_clean["periodos"] = periodos_clean
            proyeccion_b.append(pb_clean)

    # Calcular para cada modelo validado el número de pares con insumos completos
    modelos_cfg = cargar_modelos_validados()
    modelos_habilitados: Dict[str, bool] = {}
    motivos_bloqueo_modelos: Dict[str, str] = {}
    pares_completos_por_modelo: Dict[str, int] = {}

    for mod_key, mod_info in modelos_cfg.items():
        ok, motivo, n_comp = verificar_compatibilidad_modelo(
            mod_key,
            emparejamientos,
            mod_info=mod_info,
        )
        modelos_habilitados[mod_key] = ok
        pares_completos_por_modelo[mod_key] = n_comp
        if not ok:
            motivos_bloqueo_modelos[mod_key] = motivo

    return {
        "emparejamientos": emparejamientos,
        "vista_a": proyeccion_a,
        "vista_b": proyeccion_b,
        "n_filas_emparejadas": len([e for e in emparejamientos if e["hay_partido_a"] and e["hay_partido_b"]]),
        "n_filas_con_claves_ft_suficientes": filas_con_claves_comunes_suficientes,
        "min_claves_ft_requeridas": min_claves_ft_fila,
        "n_pares_con_dato": n_pares_con_dato,
        "modelos_habilitados": modelos_habilitados,
        "motivos_bloqueo_modelos": motivos_bloqueo_modelos,
        "pares_completos_por_modelo": pares_completos_por_modelo,
    }


def verificar_compatibilidad_modelo(
    nombre_modelo: str,
    emparejamientos: List[Dict[str, Any]],
    mod_info: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str, int]:
    """
    Sección 4: Verifica si un modelo cuantitativo cuenta con insumos completos y pares suficientes.
    Prohíbe ejecutar la red de 45 variables si faltan variables en alguna fila emparejada.
    """
    if mod_info is None:
        modelos_cfg = cargar_modelos_validados()
        mod_info = modelos_cfg.get(nombre_modelo)
        if not mod_info:
            return False, f"El modelo '{nombre_modelo}' no figura en modelos_validados.yaml", 0

    vars_req = set(mod_info.get("variables_requeridas") or [])
    min_pares = int(mod_info.get("min_pares_con_dato", 8))
    periodo_req = str(mod_info.get("periodo") or "FT")
    min_claves_per = int(mod_info.get(f"min_claves_comunes_{periodo_req.lower()}", 1))

    # Regla dura Red 45-DCN: no permite subconjuntos rellenados ni imputación
    if nombre_modelo == "red_recursiva_45_dcn":
        pares_45 = 0
        for emp in emparejamientos:
            if not emp["hay_partido_a"] or not emp["hay_partido_b"]:
                continue
            comunes_ft = set(emp["periodos"]["FT"]["claves_comunes"])
            if len(comunes_ft) >= 45 and vars_req.issubset(comunes_ft):
                pares_45 += 1
        if pares_45 < min_pares:
            return (
                False,
                f"Red 45-DCN bloqueada: se requieren 45 claves comunes en {min_pares} pares completos (hay {pares_45})",
                pares_45,
            )
        return True, "Red 45-DCN compatible con 15 pares completos de 45 variables", pares_45

    # Sub-modelo condicional sin xG (Decisión D1): requiere interruptor encendido, validación cronológica superada
    # y función ejecutable efectivamente implementada en el sistema (Fail-Closed)
    if nombre_modelo == "submodelo_goles_sin_xg":
        if not mod_info.get("permitir_goles_sin_xg", False) or not mod_info.get("validacion_cronologica_superada", False):
            return (
                False,
                "Sub-modelo de goles sin xG bloqueado: interruptor apagado (permitir_goles_sin_xg=false) o validación cronológica walk-forward pendiente",
                0,
            )
        func_path = mod_info.get("funcion_ejecutar", "")
        func_existe = False
        if func_path and "." in func_path:
            mod_name, func_name = func_path.rsplit(".", 1)
            try:
                import importlib
                mod_obj = importlib.import_module(mod_name)
                func_existe = hasattr(mod_obj, func_name) and callable(getattr(mod_obj, func_name))
            except Exception:
                func_existe = False
        if not func_existe:
            return (
                False,
                f"Sub-modelo de goles sin xG bloqueado: sub-modelo sin xG no implementado (función '{func_path}' no encontrada en el sistema)",
                0,
            )

    # Regla base coherente D1 para modelos de mitad (1T/2T) dependientes de goles FT:
    if mod_info.get("depende_de_modelo_goles_ft"):
        pares_xg_ft = sum(
            1 for emp in emparejamientos
            if emp["hay_partido_a"] and emp["hay_partido_b"]
            and "xg" in set(emp["periodos"]["FT"]["claves_comunes"])
        )
        if mod_info.get("requiere_xg_estricto", True) and pares_xg_ft < min_pares:
            modelos_cfg = cargar_modelos_validados()
            submod = modelos_cfg.get("submodelo_goles_sin_xg") or {}
            sub_path = submod.get("funcion_ejecutar", "")
            sub_func_existe = False
            if sub_path and "." in sub_path:
                mod_name, func_name = sub_path.rsplit(".", 1)
                try:
                    import importlib
                    mod_obj = importlib.import_module(mod_name)
                    sub_func_existe = hasattr(mod_obj, func_name) and callable(getattr(mod_obj, func_name))
                except Exception:
                    sub_func_existe = False
            submod_on = bool(
                submod.get("permitir_goles_sin_xg")
                and submod.get("validacion_cronologica_superada")
                and sub_func_existe
            )
            if not submod_on:
                if submod.get("permitir_goles_sin_xg") and not sub_func_existe:
                    motivo_sub = "sub-modelo sin xG no implementado"
                else:
                    motivo_sub = "sub-modelo sin xG no validado (permitir_goles_sin_xg=false)"
                return (
                    False,
                    f"Modelo de {periodo_req} bloqueado por coherencia con FT: falta xG oficial y {motivo_sub}",
                    pares_xg_ft,
                )

    # Conteo de pares con insumos completos para el modelo específico
    n_pares_completos = 0
    for emp in emparejamientos:
        if not emp["hay_partido_a"] or not emp["hay_partido_b"]:
            continue
        pa = emp["partido_a"]
        pb = emp["partido_b"]

        # Si el modelo requiere 'goles', verificar que ambos partidos tengan marcador
        if "goles" in vars_req:
            tiene_m_a = pa.get("goles_equipo") is not None or (isinstance(pa.get("marcador"), dict) and pa.get("marcador", {}).get("equipo") is not None)
            tiene_m_b = pb.get("goles_equipo") is not None or (isinstance(pb.get("marcador"), dict) and pb.get("marcador", {}).get("equipo") is not None)
            if not (tiene_m_a and tiene_m_b):
                continue

        vars_stats = {v for v in vars_req if v != "goles"}
        if nombre_modelo == "poisson_bivariado_goles" and not mod_info.get("requiere_xg_estricto", True):
            vars_stats = {v for v in vars_stats if v != "xg"}
        comunes_per = set(emp["periodos"][periodo_req]["claves_comunes"])
        if vars_stats.issubset(comunes_per) and len(comunes_per) >= min_claves_per:
            n_pares_completos += 1

    if n_pares_completos < min_pares:
        faltantes = [v for v in sorted(vars_req) if v != "goles"]
        msg_faltantes = " (falta xG oficial)" if ("xg" in faltantes and n_pares_completos == 0) else ""
        return (
            False,
            f"Cobertura insuficiente para {mod_info.get('nombre', nombre_modelo)}: {n_pares_completos}/{min_pares} pares completos requeridos{msg_faltantes}",
            n_pares_completos,
        )

    return True, f"Modelo {mod_info.get('nombre', nombre_modelo)} habilitado con {n_pares_completos} pares", n_pares_completos


def generar_tabla_comparable_markdown(emparejamientos: List[Dict[str, Any]]) -> str:
    """
    Genera la tabla Markdown obligatoria de la Sección 3 para /auditar:
    | Fila por recencia | Periodo | A observado | B observado | Claves comunes | Uso en el análisis |
    """
    lineas = [
        "| Fila por recencia | Periodo | A observado | B observado | Claves comunes | Uso en el análisis |",
        "| :--- | :---: | :---: | :---: | :---: | :--- |",
    ]
    for emp in emparejamientos:
        fila_n = emp["fila"]
        for per in ("FT", "1T", "2T"):
            det = emp["periodos"][per]
            a_obs = f"{det['a_observado']}/45"
            b_obs = f"{det['b_observado']}/45"
            comunes = str(det["n_comunes"])
            uso = det["uso"]
            lineas.append(f"| {fila_n} | {per} | {a_obs} | {b_obs} | {comunes} | {uso} |")

    return "\n".join(lineas)
