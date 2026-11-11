"""
decision_cobertura.py
───────────────────────────────────────────────────────────────────────────────
Puntos 44 y 45: Función Pura de Cobertura y Precedencia Estricta D.1 (Sección 5).
Evalúa la cobertura de insumos y determina el estado oficial del cruce:
  1. identidad_no_verificada
  2. historial_oficial_incompleto
  3. solo_resultados
  4. cobertura_insuficiente
  5. cobertura_parcial
  6. cobertura_suficiente
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from auditoria_comparable import construir_vista_comparable


RUTA_UMBRALES_COBERTURA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "umbrales_cobertura.yaml"
)

DEFAULTS_UMBRALES = {
    "min_partidos_oficiales": 15,
    "min_partidos_con_detalle_por_equipo": 3,
    "min_filas_emparejadas": 3,
    "min_claves_comunes_ft": 5,
    "min_pares_mercado": 8,
    "min_pares_red_45": 15,
    "min_claves_comunes_red_45": 45,
}

CATALOGO_MERCADOS = [
    "1X2",
    "handicap",
    "doble_oportunidad",
    "mas_menos_goles",
    "ambos_marcan",
    "marcador_exacto",
    "corners_totales",
    "corners_equipo",
    "tarjetas_totales",
    "tarjetas_equipo",
    "goles_1t",
    "1x2_1t",
    "goles_2t",
    "1x2_2t",
]


def cargar_umbrales_cobertura(ruta: Optional[str] = None) -> Dict[str, Any]:
    """
    Carga umbrales_cobertura.yaml sin dependencias externas.
    """
    path = ruta or RUTA_UMBRALES_COBERTURA
    umbrales = dict(DEFAULTS_UMBRALES)
    if not os.path.isfile(path):
        return umbrales

    with open(path, "r", encoding="utf-8") as f:
        contenido = f.read()

    for linea in contenido.splitlines():
        linea_limpia = linea.split("#")[0].strip()
        if not linea_limpia or ":" not in linea_limpia:
            continue
        k, v = linea_limpia.split(":", 1)
        k = k.strip()
        v = v.strip()
        if re.match(r"^\d+$", v):
            umbrales[k] = int(v)
        elif re.match(r"^\d+\.\d+$", v):
            umbrales[k] = float(v)

    return umbrales


@dataclass
class DecisionCobertura:
    estado: str
    publicar_probabilidades: bool
    alerta_descriptiva: str
    etiqueta_cobertura_a: str
    etiqueta_cobertura_b: str
    mercados_habilitados: Dict[str, bool] = field(default_factory=dict)
    motivos_bloqueo: Dict[str, str] = field(default_factory=dict)
    modelos_activos: Dict[str, str] = field(default_factory=dict)
    n_filas_emparejadas: int = 0
    n_filas_comparables: int = 0
    detalles_decision: Dict[str, Any] = field(default_factory=dict)

    def a_dict(self) -> Dict[str, Any]:
        return {
            "estado": self.estado,
            "publicar_probabilidades": self.publicar_probabilidades,
            "alerta_descriptiva": self.alerta_descriptiva,
            "etiqueta_cobertura_a": self.etiqueta_cobertura_a,
            "etiqueta_cobertura_b": self.etiqueta_cobertura_b,
            "mercados_habilitados": self.mercados_habilitados,
            "motivos_bloqueo": self.motivos_bloqueo,
            "modelos_activos": self.modelos_activos,
            "n_filas_emparejadas": self.n_filas_emparejadas,
            "n_filas_comparables": self.n_filas_comparables,
            "detalles_decision": self.detalles_decision,
        }


def _extraer_metadatos_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    resumen = doc.get("resumen_auditoria") or doc.get("resumen") or doc
    nombre = doc.get("equipo") or doc.get("nombre") or resumen.get("equipo") or "Equipo"

    estado_identidad = doc.get("estado_identidad") or doc.get("estado") or "verificado"
    if doc.get("identidad_verificada") is False:
        estado_identidad = "identidad_no_verificada"

    partidos = doc.get("partidos") or []
    n_oficiales = resumen.get("partidos_oficiales_localizados")
    if n_oficiales is None:
        n_oficiales = len(partidos)

    n_marcador = resumen.get("partidos_con_marcador")
    if n_marcador is None:
        n_marcador = sum(1 for p in partidos if p.get("goles_equipo") is not None or p.get("marcador"))

    n_detalle_ft = resumen.get("partidos_con_detalle_ft")
    if n_detalle_ft is None:
        n_detalle_ft = sum(
            1 for p in partidos
            if len((p.get("resumen_claves") or {}).get("FT", {}).get("equipo", [])) > 0
            or len((p.get("periodos") or {}).get("FT", {}).get("filas", [])) > 0
        )

    n_solo_marcador = resumen.get("partidos_solo_marcador")
    if n_solo_marcador is None:
        n_solo_marcador = max(0, n_marcador - n_detalle_ft)

    etiqueta = resumen.get("etiqueta_cobertura") or (
        f"{n_oficiales}/15 oficiales · {n_detalle_ft}/15 con detalle FT · {n_solo_marcador}/15 solo con marcador"
    )

    return {
        "equipo": nombre,
        "estado_identidad": estado_identidad,
        "n_oficiales": n_oficiales,
        "n_marcador": n_marcador,
        "n_detalle_ft": n_detalle_ft,
        "n_solo_marcador": n_solo_marcador,
        "etiqueta": etiqueta,
        "partidos": partidos,
    }


def evaluar_decision_cobertura(
    doc_a: Dict[str, Any],
    doc_b: Dict[str, Any],
    vista_comparable: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> DecisionCobertura:
    """
    Función pura que implementa la precedencia estricta D.1:
      1. identidad_no_verificada
      2. historial_oficial_incompleto
      3. solo_resultados
      4. cobertura_insuficiente
      5. cobertura_parcial
      6. cobertura_suficiente
    """
    cfg = dict(DEFAULTS_UMBRALES)
    cfg.update(cargar_umbrales_cobertura())
    if config:
        cfg.update(config)

    meta_a = _extraer_metadatos_doc(doc_a)
    meta_b = _extraer_metadatos_doc(doc_b)

    eq_a = meta_a["equipo"]
    eq_b = meta_b["equipo"]
    etq_a = meta_a["etiqueta"]
    etq_b = meta_b["etiqueta"]

    # ─────────────────────────────────────────────────────────────────────────
    # ESTADO 1: identidad_no_verificada
    # ─────────────────────────────────────────────────────────────────────────
    if (
        meta_a["estado_identidad"] == "identidad_no_verificada"
        or meta_b["estado_identidad"] == "identidad_no_verificada"
    ):
        nom_no_verif = eq_a if meta_a["estado_identidad"] == "identidad_no_verificada" else eq_b
        return DecisionCobertura(
            estado="identidad_no_verificada",
            publicar_probabilidades=False,
            alerta_descriptiva=(
                f"Identidad no verificada para '{nom_no_verif}'. "
                "No fue posible confirmar con certeza la identidad del club o torneo. "
                "Se prohíbe calcular o publicar pronósticos cuantitativos."
            ),
            etiqueta_cobertura_a=etq_a,
            etiqueta_cobertura_b=etq_b,
            mercados_habilitados={m: False for m in CATALOGO_MERCADOS},
            motivos_bloqueo={m: f"Identidad no verificada para {nom_no_verif}" for m in CATALOGO_MERCADOS},
            detalles_decision={"meta_a": meta_a, "meta_b": meta_b},
        )

    # ─────────────────────────────────────────────────────────────────────────
    # ESTADO 2: historial_oficial_incompleto
    # ─────────────────────────────────────────────────────────────────────────
    min_oficiales = cfg["min_partidos_oficiales"]
    if meta_a["n_oficiales"] < min_oficiales or meta_b["n_oficiales"] < min_oficiales:
        return DecisionCobertura(
            estado="historial_oficial_incompleto",
            publicar_probabilidades=False,
            alerta_descriptiva=(
                f"Historial oficial incompleto: {meta_a['n_oficiales']}/{min_oficiales} partidos para {eq_a} "
                f"y {meta_b['n_oficiales']}/{min_oficiales} para {eq_b}. "
                f"Se requiere un historial completo de {min_oficiales} partidos oficiales por club."
            ),
            etiqueta_cobertura_a=etq_a,
            etiqueta_cobertura_b=etq_b,
            mercados_habilitados={m: False for m in CATALOGO_MERCADOS},
            motivos_bloqueo={m: "Historial oficial incompleto (< 15 partidos)" for m in CATALOGO_MERCADOS},
            detalles_decision={"meta_a": meta_a, "meta_b": meta_b},
        )

    # ─────────────────────────────────────────────────────────────────────────
    # ESTADO 3: solo_resultados (Caso tipo Anaitasuna: 0 detalle en algún equipo)
    # ─────────────────────────────────────────────────────────────────────────
    if meta_a["n_detalle_ft"] == 0 or meta_b["n_detalle_ft"] == 0:
        eq_sin_detalle = eq_a if meta_a["n_detalle_ft"] == 0 else eq_b
        return DecisionCobertura(
            estado="solo_resultados",
            publicar_probabilidades=False,
            alerta_descriptiva=(
                f"Solo resultados: el club '{eq_sin_detalle}' tiene 0/15 partidos con estadísticas detalladas. "
                "Únicamente se dispone de marcadores finales. "
                "Se prohíbe emitir probabilidades de mercados estadísticos sin evidencia."
            ),
            etiqueta_cobertura_a=etq_a,
            etiqueta_cobertura_b=etq_b,
            mercados_habilitados={m: False for m in CATALOGO_MERCADOS},
            motivos_bloqueo={m: f"0/15 partidos con estadísticas detalladas para {eq_sin_detalle}" for m in CATALOGO_MERCADOS},
            detalles_decision={"meta_a": meta_a, "meta_b": meta_b},
        )

    # Construir la vista comparable simétrica si no fue provista
    if vista_comparable is None:
        vista_comparable = construir_vista_comparable(
            doc_a, doc_b, min_claves_ft_fila=cfg["min_claves_comunes_ft"]
        )

    n_filas_emp = vista_comparable.get("n_filas_emparejadas", 0)
    n_filas_comp = vista_comparable.get("n_filas_con_claves_ft_suficientes", 0)
    modelos_hab = vista_comparable.get("modelos_habilitados", {})
    motivos_mod = vista_comparable.get("motivos_bloqueo_modelos", {})

    min_detalle = cfg["min_partidos_con_detalle_por_equipo"]
    min_filas_comp_req = cfg["min_filas_emparejadas"]
    min_claves_req = cfg["min_claves_comunes_ft"]

    # ─────────────────────────────────────────────────────────────────────────
    # ESTADO 4: cobertura_insuficiente
    # ─────────────────────────────────────────────────────────────────────────
    # 4a: Menos de min_partidos_con_detalle_por_equipo (Caso tipo Tavernes: 1 o 2 con detalle)
    if meta_a["n_detalle_ft"] < min_detalle or meta_b["n_detalle_ft"] < min_detalle:
        eq_insuf = eq_a if meta_a["n_detalle_ft"] < min_detalle else eq_b
        det_insuf = meta_a["n_detalle_ft"] if meta_a["n_detalle_ft"] < min_detalle else meta_b["n_detalle_ft"]
        return DecisionCobertura(
            estado="cobertura_insuficiente",
            publicar_probabilidades=False,
            alerta_descriptiva=(
                f"Cobertura insuficiente: '{eq_insuf}' cuenta con solo {det_insuf}/15 partidos oficiales con detalle FT "
                f"(mínimo requerido: {min_detalle}). "
                "Muestra estadística no representativa para proyectar mercados cuantitativos."
            ),
            etiqueta_cobertura_a=etq_a,
            etiqueta_cobertura_b=etq_b,
            mercados_habilitados={m: False for m in CATALOGO_MERCADOS},
            motivos_bloqueo={m: f"Detalle estadístico insuficiente ({det_insuf} < {min_detalle}) para {eq_insuf}" for m in CATALOGO_MERCADOS},
            n_filas_emparejadas=n_filas_emp,
            n_filas_comparables=n_filas_comp,
            detalles_decision={"motivo": "muestra_detalle_insuficiente", "meta_a": meta_a, "meta_b": meta_b},
        )

    # 4b: Sin variables comparables (menos de min_filas_emparejadas con >= min_claves_comunes_ft)
    if n_filas_comp < min_filas_comp_req:
        return DecisionCobertura(
            estado="cobertura_insuficiente",
            publicar_probabilidades=False,
            alerta_descriptiva=(
                f"Cobertura insuficiente (sin variables comparables): solo {n_filas_comp} filas emparejadas "
                f"comparten al menos {min_claves_req} claves comunes en FT (mínimo requerido: {min_filas_comp_req}). "
                "Los historiales de ambos clubes carecen de métricas homogéneas suficientes."
            ),
            etiqueta_cobertura_a=etq_a,
            etiqueta_cobertura_b=etq_b,
            mercados_habilitados={m: False for m in CATALOGO_MERCADOS},
            motivos_bloqueo={m: "Sin variables comparables suficientes entre ambos clubes" for m in CATALOGO_MERCADOS},
            n_filas_emparejadas=n_filas_emp,
            n_filas_comparables=n_filas_comp,
            detalles_decision={"motivo": "sin_variables_comparables", "n_filas_comparables": n_filas_comp},
        )

    # Mapeo de mercados a modelos y evaluación de viabilidad
    mercados_habilitados: Dict[str, bool] = {}
    motivos_bloqueo: Dict[str, str] = {}
    modelos_activos: Dict[str, str] = {}

    # Mercado 1X2 / Handicap / Doble Oportunidad:
    # Prioridad 1: red_recursiva_45_dcn (si tiene 15 pares de 45 métricas)
    # Alternativa validada: poisson_bivariado_goles (si tiene >= 8 pares de goles/xg/remates_puerta)
    if modelos_hab.get("red_recursiva_45_dcn"):
        for m in ("1X2", "handicap", "doble_oportunidad"):
            mercados_habilitados[m] = True
            modelos_activos[m] = "red_recursiva_45_dcn"
    elif modelos_hab.get("poisson_bivariado_goles"):
        for m in ("1X2", "handicap", "doble_oportunidad"):
            mercados_habilitados[m] = True
            modelos_activos[m] = "poisson_bivariado_goles"
    else:
        for m in ("1X2", "handicap", "doble_oportunidad"):
            mercados_habilitados[m] = False
            motivos_bloqueo[m] = motivos_mod.get("poisson_bivariado_goles") or motivos_mod.get("red_recursiva_45_dcn") or "Sin modelo de resultado habilitado"

    # Goles FT: poisson_bivariado_goles
    if modelos_hab.get("poisson_bivariado_goles"):
        for m in ("mas_menos_goles", "ambos_marcan", "marcador_exacto"):
            mercados_habilitados[m] = True
            modelos_activos[m] = "poisson_bivariado_goles"
    else:
        for m in ("mas_menos_goles", "ambos_marcan", "marcador_exacto"):
            mercados_habilitados[m] = False
            motivos_bloqueo[m] = motivos_mod.get("poisson_bivariado_goles") or "Insumos insuficientes para modelo bivariado de goles"

    # Córneres: modelo_mercado_corners
    if modelos_hab.get("modelo_mercado_corners"):
        for m in ("corners_totales", "corners_equipo"):
            mercados_habilitados[m] = True
            modelos_activos[m] = "modelo_mercado_corners"
    else:
        for m in ("corners_totales", "corners_equipo"):
            mercados_habilitados[m] = False
            motivos_bloqueo[m] = motivos_mod.get("modelo_mercado_corners") or "Menos de 8 pares con córneres observados"

    # Tarjetas: modelo_mercado_tarjetas
    if modelos_hab.get("modelo_mercado_tarjetas"):
        for m in ("tarjetas_totales", "tarjetas_equipo"):
            mercados_habilitados[m] = True
            modelos_activos[m] = "modelo_mercado_tarjetas"
    else:
        for m in ("tarjetas_totales", "tarjetas_equipo"):
            mercados_habilitados[m] = False
            motivos_bloqueo[m] = motivos_mod.get("modelo_mercado_tarjetas") or "Menos de 8 pares con tarjetas amarillas observadas"

    # Mitad 1T: modelo_mercado_1t
    if modelos_hab.get("modelo_mercado_1t"):
        for m in ("goles_1t", "1x2_1t"):
            mercados_habilitados[m] = True
            modelos_activos[m] = "modelo_mercado_1t"
    else:
        for m in ("goles_1t", "1x2_1t"):
            mercados_habilitados[m] = False
            motivos_bloqueo[m] = motivos_mod.get("modelo_mercado_1t") or "Menos de 8 pares con datos de 1T observados"

    # Mitad 2T: modelo_mercado_2t
    if modelos_hab.get("modelo_mercado_2t"):
        for m in ("goles_2t", "1x2_2t"):
            mercados_habilitados[m] = True
            modelos_activos[m] = "modelo_mercado_2t"
    else:
        for m in ("goles_2t", "1x2_2t"):
            mercados_habilitados[m] = False
            motivos_bloqueo[m] = motivos_mod.get("modelo_mercado_2t") or "Menos de 8 pares con datos de 2T observados"

    # ─────────────────────────────────────────────────────────────────────────
    # REGLA BASE COHERENTE D1 (Coherencia Estricta de Mercados de Goles):
    # Todos los mercados dependientes de goles (1X2, handicap, doble oportunidad,
    # más/menos FT, ambos marcan, marcador exacto, goles 1T, 1x2 1T, goles 2T y 1x2 2T)
    # se rigen por la misma regla estricta. Queda prohibido publicar goles_1t y
    # goles_2t cuando los goles FT están bloqueados por falta de xG.
    # ─────────────────────────────────────────────────────────────────────────
    goles_ft_habilitados = bool(mercados_habilitados.get("mas_menos_goles") or mercados_habilitados.get("1X2"))
    if not goles_ft_habilitados:
        motivo_ft = (
            motivos_bloqueo.get("mas_menos_goles")
            or motivos_bloqueo.get("1X2")
            or motivos_mod.get("poisson_bivariado_goles")
            or "Modelo de goles FT no habilitado"
        )
        for m in ("goles_1t", "1x2_1t"):
            mercados_habilitados[m] = False
            motivos_bloqueo[m] = f"Mercado de 1T bloqueado por coherencia estricta con FT: {motivo_ft}"
        for m in ("goles_2t", "1x2_2t"):
            mercados_habilitados[m] = False
            motivos_bloqueo[m] = f"Mercado de 2T bloqueado por coherencia estricta con FT: {motivo_ft}"

    # 4c: Si ningún mercado pudo habilitarse
    n_hab = sum(1 for v in mercados_habilitados.values() if v)
    if n_hab == 0:
        return DecisionCobertura(
            estado="cobertura_insuficiente",
            publicar_probabilidades=False,
            alerta_descriptiva=(
                "Cobertura insuficiente: ningún mercado del catálogo cumple los requisitos mínimos "
                "de insumos y pares con dato para ejecutar modelos cuantitativos validados."
            ),
            etiqueta_cobertura_a=etq_a,
            etiqueta_cobertura_b=etq_b,
            mercados_habilitados=mercados_habilitados,
            motivos_bloqueo=motivos_bloqueo,
            n_filas_emparejadas=n_filas_emp,
            n_filas_comparables=n_filas_comp,
            detalles_decision={"motivo": "cero_mercados_habilitados", "motivos_bloqueo": motivos_bloqueo},
        )

    # ─────────────────────────────────────────────────────────────────────────
    # ESTADO 5 vs 6: cobertura_parcial vs cobertura_suficiente
    # ─────────────────────────────────────────────────────────────────────────
    todos_habilitados = all(mercados_habilitados.get(m, False) for m in CATALOGO_MERCADOS)
    red_45_activa = bool(modelos_hab.get("red_recursiva_45_dcn", False))

    # Punto 44 y 45 (Ajuste Etapa 1.1): Cobertura suficiente no exige 45-DCN completa.
    # Exige que todos los mercados evaluados dispongan de su sub-modelo validado.
    if todos_habilitados:
        estado = "cobertura_suficiente"
        alerta = (
            "Cobertura suficiente: todos los mercados evaluados disponen de modelos cuantitativos validados "
            f"con insumos completos ({'Red 45-DCN activa' if red_45_activa else 'Poisson/Dixon-Coles bivariado activo'})."
        )
    else:
        estado = "cobertura_parcial"
        bloqueados = [m for m in CATALOGO_MERCADOS if not mercados_habilitados.get(m, False)]
        habilitados = [m for m in CATALOGO_MERCADOS if mercados_habilitados.get(m, False)]
        alerta = (
            f"Cobertura parcial: {len(habilitados)}/{len(CATALOGO_MERCADOS)} mercados habilitados con modelo validado. "
            f"Mercados bloqueados por cobertura de insumos: {', '.join(bloqueados)}."
        )

    return DecisionCobertura(
        estado=estado,
        publicar_probabilidades=True,
        alerta_descriptiva=alerta,
        etiqueta_cobertura_a=etq_a,
        etiqueta_cobertura_b=etq_b,
        mercados_habilitados=mercados_habilitados,
        motivos_bloqueo=motivos_bloqueo,
        modelos_activos=modelos_activos,
        n_filas_emparejadas=n_filas_emp,
        n_filas_comparables=n_filas_comp,
        detalles_decision={
            "red_45_activa": red_45_activa,
            "n_mercados_habilitados": n_hab,
            "n_mercados_totales": len(CATALOGO_MERCADOS),
        },
    )
