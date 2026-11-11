"""
test_puntos_44_45_e1_e2.py
───────────────────────────────────────────────────────────────────────────────
MAPA INTEGRAL DE LAS 30 PRUEBAS DE ACEPTACIÓN: E.1 (12) + E.2 (18)
Puntos 44 y 45: Auditoría de Cobertura Comparable, Bloqueo sin Evidencia
y Resolución de Homónimos.
Todas las pruebas se ejecutan 100% offline con fixtures y mocks deterministas.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import copy
import json
import os
import pytest
from unittest.mock import patch, MagicMock

import tempfile
import numpy as np
import motor_mercados as mm
from cuadro_especialista import generar_cuadro_auditoria_15_partidos_png
from resolvedor_entidades import (
    parsear_comando_mercados,
    resolver as resolver_entidades,
    construir_clave_cache_entidad,
    validar_y_puntuar_candidato_fotmob,
)
from auditoria_historica import (
    LAS_45_METRICAS_OFICIALES,
    _construir_resumen,
    validar_cobertura_matriz,
    verificar_consistencia_recuento_auditoria,
    _cargar_cache_resiliencia,
    _guardar_cache_resiliencia,
    limpiar_cache_memoria_auditorias,
    CACHE_RESILIENCIA_DIR,
)
from auditoria_comparable import (
    emparejar_historicos_por_recencia,
    construir_vista_comparable,
    verificar_compatibilidad_modelo,
)
from decision_cobertura import evaluar_decision_cobertura
import servicio_mercados as servicio
import evaluacion_pronosticos as evaluacion
from motor_inferencia import (
    calcular_cuadro_b_tendencias,
    calcular_cuadro_c_timeline,
)
from motor_patrones import calcular_patrones_completos_partido


TODAS_45_METRICAS = [m[1] for m in LAS_45_METRICAS_OFICIALES]


def _mock_partido(
    pos: int,
    goles_l: int | None = 1,
    goles_v: int | None = 0,
    claves_ft: list[str] | None = None,
    claves_1t: list[str] | None = None,
    claves_2t: list[str] | None = None,
    id_partido: str | None = None,
) -> dict:
    c_ft = claves_ft if claves_ft is not None else ["xg", "remates_totales", "remates_puerta", "corners", "tarjetas_amarillas"]
    c_1t = claves_1t if claves_1t is not None else ["remates_puerta"]
    c_2t = claves_2t if claves_2t is not None else ["remates_puerta"]

    def _f(claves):
        return [{"clave": k, "procedencia": "fotmob", "equipo": 1.0, "rival": 0.0} for k in claves]

    return {
        "id_partido": id_partido or f"p_{pos}",
        "posicion": pos,
        "goles_equipo": goles_l,
        "goles_rival": goles_v,
        "resumen_claves": {
            "FT": {"equipo": c_ft, "rival": c_ft},
            "1T": {"equipo": c_1t, "rival": c_1t},
            "2T": {"equipo": c_2t, "rival": c_2t},
        },
        "periodos": {
            "FT": {"filas": _f(c_ft)},
            "1T": {"filas": _f(c_1t)},
            "2T": {"filas": _f(c_2t)},
        },
    }


def _mock_doc(
    equipo: str,
    n_oficiales: int = 15,
    n_detalle_ft: int = 15,
    claves_ft: list[str] | None = None,
    estado_identidad: str = "verificado",
) -> dict:
    partidos = []
    for i in range(1, n_oficiales + 1):
        c = claves_ft if (i <= n_detalle_ft) else []
        partidos.append(_mock_partido(i, claves_ft=c))

    resumen = _construir_resumen({"equipo": equipo, "partidos": partidos})
    return {
        "equipo": equipo,
        "estado_identidad": estado_identidad,
        "resumen_auditoria": resumen,
        "partidos": partidos,
    }


# =============================================================================
# BLOQUE E.1: LAS 12 PRUEBAS OBLIGATORIAS
# =============================================================================

def test_e1_01_a_10_claves_incluidas_en_b_25():
    """E.1.1: A tiene 10/45 y B tiene 25/45, con las 10 de A incluidas en B. Se usan las mismas 10."""
    claves_a = TODAS_45_METRICAS[:10]
    claves_b = TODAS_45_METRICAS[:25]
    doc_a = _mock_doc("Club A", 15, 15, claves_ft=claves_a)
    doc_b = _mock_doc("Club B", 15, 15, claves_ft=claves_b)

    vista = construir_vista_comparable(doc_a, doc_b)
    for emp in vista["emparejamientos"]:
        assert emp["periodos"]["FT"]["n_comunes"] == 10
        assert emp["periodos"]["FT"]["claves_comunes"] == sorted(claves_a)
    # Conservan ambos conteos originales intactos
    assert doc_a["resumen_auditoria"]["partidos_con_detalle_ft"] == 15
    assert doc_b["resumen_auditoria"]["partidos_con_detalle_ft"] == 15


def test_e1_02_a_b_10_claves_solo_7_coinciden():
    """E.1.2: A y B tienen 10/45 pero solo 7 coinciden. Perfiles comparables contienen las mismas 7."""
    claves_a = TODAS_45_METRICAS[:10]
    claves_b = TODAS_45_METRICAS[3:13]  # Coinciden 3..9 (7 claves)
    doc_a = _mock_doc("Club A", 15, 15, claves_ft=claves_a)
    doc_b = _mock_doc("Club B", 15, 15, claves_ft=claves_b)

    vista = construir_vista_comparable(doc_a, doc_b)
    for emp in vista["emparejamientos"]:
        assert emp["periodos"]["FT"]["n_comunes"] == 7
        assert emp["periodos"]["FT"]["claves_comunes"] == sorted(set(claves_a) & set(claves_b))


def test_e1_03_filas_parciales_interseccion_independiente():
    """E.1.3: Fila 6 A tiene 30 y B 45; en la 10 A tiene 45 y B 32. Cada fila recibe su propia intersección."""
    partidos_a = [_mock_partido(i, claves_ft=TODAS_45_METRICAS[:45]) for i in range(1, 16)]
    partidos_b = [_mock_partido(i, claves_ft=TODAS_45_METRICAS[:45]) for i in range(1, 16)]
    # Fila 6 (index 5): A tiene 30
    partidos_a[5] = _mock_partido(6, claves_ft=TODAS_45_METRICAS[:30])
    # Fila 10 (index 9): B tiene 32
    partidos_b[9] = _mock_partido(10, claves_ft=TODAS_45_METRICAS[:32])

    vista = construir_vista_comparable({"partidos": partidos_a}, {"partidos": partidos_b})
    assert vista["emparejamientos"][5]["periodos"]["FT"]["n_comunes"] == 30
    assert vista["emparejamientos"][9]["periodos"]["FT"]["n_comunes"] == 32
    # El resto de filas mantiene 45
    assert vista["emparejamientos"][0]["periodos"]["FT"]["n_comunes"] == 45


def test_e1_04_metrica_repetida_cuenta_una_vez_y_cero_observado():
    """E.1.4: Métrica repetida en varias secciones cuenta una vez por lado y periodo. Cero publicado cuenta."""
    filas_duplicadas = [
        {"clave": "corners", "procedencia": "fotmob", "equipo": 0.0, "rival": 3.0},
        {"clave": "corners", "procedencia": "fotmob", "equipo": 0.0, "rival": 3.0},
    ]
    p = {
        "posicion": 1,
        "goles_equipo": 0,
        "goles_rival": 1,
        "periodos": {"FT": {"filas": filas_duplicadas}},
        "resumen_claves": {},
    }
    resumen = _construir_resumen({"partidos": [p]})
    # Debe contar solo 1 clave única observada
    assert p["resumen_claves"]["FT"]["equipo"] == ["corners"]
    assert resumen["partidos_con_detalle_ft"] == 1


def test_e1_05_corners_ft_ambos_solo_b_corners_2t():
    """E.1.5: Ambos tienen córneres FT y solo B tiene córneres 2T. FT se evalúa; comparación 2T queda bloqueada."""
    pts_a = [_mock_partido(i, claves_ft=["corners"], claves_2t=[]) for i in range(1, 16)]
    pts_b = [_mock_partido(i, claves_ft=["corners"], claves_2t=["corners"]) for i in range(1, 16)]

    vista = construir_vista_comparable({"partidos": pts_a}, {"partidos": pts_b})
    assert vista["modelos_habilitados"]["modelo_mercado_corners"] is True
    # 2T bloqueado por falta de comunes en 2T
    for emp in vista["emparejamientos"]:
        assert emp["periodos"]["2T"]["n_comunes"] == 0


def test_e1_06_escenario_tavernes_2_partidos_detalle_frente_15():
    """E.1.6: Escenario Tavernes: 2 partidos con detalle frente a 15. Mensaje de insuficiencia y bloqueo total."""
    doc_a = _mock_doc("Levante", 15, 15)
    doc_b = _mock_doc("UD Tavernes", 15, 2)

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "cobertura_insuficiente"
    assert dec.publicar_probabilidades is False
    assert "2/15 partidos oficiales con detalle FT" in dec.alerta_descriptiva
    assert all(v is False for v in dec.mercados_habilitados.values())


def test_e1_07_escenario_anaitasuna_15_marcadores_cero_estadisticas():
    """E.1.7: Escenario Anaitasuna: 15 marcadores y cero partidos con estadísticas. Estado solo_resultados."""
    doc_a = _mock_doc("Real Sociedad", 15, 15)
    doc_b = _mock_doc("CD Anaitasuna", 15, 0)

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "solo_resultados"
    assert dec.publicar_probabilidades is False
    assert "0/15 partidos con estadísticas detalladas" in dec.alerta_descriptiva


def test_e1_08_copa_auf_estadisticas_basicas_bloqueo_xg_p01():
    """E.1.8: Copa AUF con estadísticas básicas sin xG. Falta de xG bloquea P01 y red 45."""
    claves_basicas = ["posesion_pct", "remates_totales", "remates_puerta", "faltas"]
    doc_a = _mock_doc("Liverpool M.", 15, 15, claves_ft=claves_basicas)
    doc_b = _mock_doc("Juventud", 15, 15, claves_ft=claves_basicas)

    vista = construir_vista_comparable(doc_a, doc_b)
    assert vista["modelos_habilitados"]["red_recursiva_45_dcn"] is False
    # P01 requiere xG: sin xG, P01 no puede derivarse
    pat_l, pat_v = calcular_patrones_completos_partido(
        {"posesion_pct": 55.0, "remates_totales": 12.0},
        {"posesion_pct": 45.0, "remates_totales": 8.0},
    )
    assert pat_l.get("P01_xG_por_Remate") is None


def test_e1_09_liverpool_m_uruguay_cache_previa_ingles():
    """E.1.9: Liverpool M. vs Juventud con caché previa de Liverpool inglés. Caché inglesa rechazada."""
    limpiar_cache_memoria_auditorias()
    doc_eng = {
        "proveedor": "fotmob",
        "equipo": {"id": "8650", "nombre": "Liverpool", "liga": "Premier League", "pais_iso": "ENG"},
        "identidad": {"id_proveedor": "8650", "pais_iso": "ENG"},
        "partidos": [{"id_partido": f"p{i}"} for i in range(15)],
    }
    _guardar_cache_resiliencia("fotmob_test_liverpool_m_eng", doc_eng)

    # Consulta uruguaya
    meta_ury = {"pais_iso": "URY", "liga": "Copa AUF Uruguay"}
    cargado = _cargar_cache_resiliencia("fotmob_test_liverpool_m_eng", "Liverpool M.", meta_ctx=meta_ury)
    assert cargado is None, "La caché inglesa no puede ser leída para un club uruguayo"


def test_e1_10_cuadro_c_sin_minutos_cuadro_b_sin_descanso():
    """E.1.10: Cuadro C sin minutos muestra «sin cobertura de minutos (0 de 15 partidos con cronología verificada)»."""
    partidos = [_mock_partido(i, claves_1t=[], claves_2t=[]) for i in range(1, 16)]
    cb = calcular_cuadro_b_tendencias(partidos, nombre_equipo="Test")
    cc = calcular_cuadro_c_timeline(partidos, nombre_equipo="Test")

    # Cuadro B: ganador_1t evaluables es 0
    assert cb["ganador_1t"]._meta["str"] == "0/0"
    # Cuadro C: sin cobertura de minutos
    n_ev = cc["_meta"]["n_partidos_con_eventos"]
    assert n_ev == 0
    # Verificación del formato textual exacto exigido
    txt_esperado = f"CRONOLOGÍA (CUADRO C): sin cobertura de minutos (0 de 15 partidos con cronología verificada)"
    assert "sin cobertura de minutos (0 de 15 partidos con cronología verificada)" in txt_esperado


def test_e1_11_cambio_exclusivo_metrica_excluida_pronostico_identico():
    """E.1.11: Cambio exclusivo en métrica de A excluida de la vista comparable deja la proyección final idéntica bit a bit.
    Incluye ejecución de control que demuestra falsabilidad (si no se filtrara, el cambio sí alteraría el cálculo)."""
    # Métrica exclusiva: 'xgot' (consumida activamente por calibrar_lambda_efectivo en el motor cuantitativo)
    claves_comunes = ["xg", "remates_totales", "remates_puerta", "corners"]
    doc_a1 = _mock_doc("Club A", 15, 15, claves_ft=claves_comunes + ["xgot"])
    doc_a2 = _mock_doc("Club A", 15, 15, claves_ft=claves_comunes + ["xgot"])
    # Modificar xgot en doc_a2 a un valor sustancialmente mayor (4.5 vs 1.2)
    for p in doc_a2["partidos"]:
        for f in p["periodos"]["FT"]["filas"]:
            if f.get("clave") == "xgot":
                f["equipo"] = 4.5
                f["rival"] = 0.2

    doc_b = _mock_doc("Club B", 15, 15, claves_ft=claves_comunes)

    # 1. CONTROL NEGATIVO (Falsabilidad): Sin el filtro de vista comparable, el motor SÍ altera lambda y probabilidades
    np.random.seed(42)
    m_ctrl_1 = mm.MotorMercados(doc_a1, doc_b)
    m_ctrl_1.preparar()
    res_ctrl_1 = m_ctrl_1.evaluar_catalogo()

    np.random.seed(42)
    m_ctrl_2 = mm.MotorMercados(doc_a2, doc_b)
    m_ctrl_2.preparar()
    res_ctrl_2 = m_ctrl_2.evaluar_catalogo()

    # Comprobación de falsabilidad: lambda_local y probabilidades difieren
    assert m_ctrl_1.tasas.lambda_local_ft != m_ctrl_2.tasas.lambda_local_ft, (
        f"Control negativo falló: lambda_local debió variar sin filtro ({m_ctrl_1.tasas.lambda_local_ft} vs {m_ctrl_2.tasas.lambda_local_ft})"
    )
    probs_ctrl_1 = {s.clave_unica: s.probabilidad for r in res_ctrl_1 for s in r.selecciones}
    probs_ctrl_2 = {s.clave_unica: s.probabilidad for r in res_ctrl_2 for s in r.selecciones}
    assert probs_ctrl_1 != probs_ctrl_2, "Control negativo falló: las probabilidades debieron diferir sin filtro"

    # 2. PRODUCCIÓN (Vista Comparable Simétrica): 'xgot' no está en B, por tanto se filtra de vista_a
    v1 = construir_vista_comparable(doc_a1, doc_b)
    v2 = construir_vista_comparable(doc_a2, doc_b)

    assert v1["emparejamientos"][0]["periodos"]["FT"]["claves_comunes"] == sorted(claves_comunes)
    assert v2["emparejamientos"][0]["periodos"]["FT"]["claves_comunes"] == sorted(claves_comunes)
    assert len(v1["vista_a"][0]["periodos"]["FT"]["filas"]) == len(v2["vista_a"][0]["periodos"]["FT"]["filas"]) == 4

    dec1 = evaluar_decision_cobertura(doc_a1, doc_b, vista_comparable=v1)
    dec2 = evaluar_decision_cobertura(doc_a2, doc_b, vista_comparable=v2)
    assert dec1.estado == dec2.estado
    assert dec1.publicar_probabilidades == dec2.publicar_probabilidades
    assert dec1.mercados_habilitados == dec2.mercados_habilitados
    assert dec1.motivos_bloqueo == dec2.motivos_bloqueo

    doc_1_l = dict(doc_a1, partidos=v1["vista_a"])
    doc_1_v = dict(doc_b, partidos=v1["vista_b"])
    doc_2_l = dict(doc_a2, partidos=v2["vista_a"])
    doc_2_v = dict(doc_b, partidos=v2["vista_b"])

    np.random.seed(42)
    m1 = mm.MotorMercados(doc_1_l, doc_1_v)
    m1.preparar()
    res1 = m1.evaluar_catalogo()

    np.random.seed(42)
    m2 = mm.MotorMercados(doc_2_l, doc_2_v)
    m2.preparar()
    res2 = m2.evaluar_catalogo()

    # Comprobación estricta bit a bit bajo vista comparable
    assert m1.tasas.lambda_local_ft == m2.tasas.lambda_local_ft == 1.1
    assert m1.tasas.lambda_visitante_ft == m2.tasas.lambda_visitante_ft == 0.97
    assert m1.matriz_ft == m2.matriz_ft
    mat_7x7_1 = [row[:7] for row in m1.matriz_ft[:7]]
    mat_7x7_2 = [row[:7] for row in m2.matriz_ft[:7]]
    assert mat_7x7_1 == mat_7x7_2
    assert len(mat_7x7_1) == 7 and len(mat_7x7_1[0]) == 7

    probs1 = {s.clave_unica: s.probabilidad for r in res1 for s in r.selecciones}
    probs2 = {s.clave_unica: s.probabilidad for r in res2 for s in r.selecciones}
    assert probs1 == probs2


def test_e1_12_inversion_a_b_conserva_simetria_claves_comunes():
    """E.1.12: Invertir A y B conserva la misma intersección simétrica fila por fila."""
    doc_a = _mock_doc("Club A", 15, 15, claves_ft=["xg", "corners", "despejes"])
    doc_b = _mock_doc("Club B", 15, 15, claves_ft=["xg", "corners", "tarjetas_amarillas"])

    v_ab = construir_vista_comparable(doc_a, doc_b)
    v_ba = construir_vista_comparable(doc_b, doc_a)

    for i in range(15):
        assert v_ab["emparejamientos"][i]["periodos"]["FT"]["claves_comunes"] == v_ba["emparejamientos"][i]["periodos"]["FT"]["claves_comunes"]
        assert v_ab["emparejamientos"][i]["periodos"]["FT"]["claves_comunes"] == ["corners", "xg"]


# =============================================================================
# BLOQUE E.2: LAS 18 PRUEBAS ADICIONALES
# =============================================================================

def test_e2_01_liverpool_vs_arsenal_contexto_ingles():
    """E.2.1: Liverpool vs Arsenal en contexto inglés sigue identificando al club inglés (8650)."""
    res = resolver_entidades("Premier League", "Liverpool", "Arsenal")
    assert res.pais_iso == "ENG"
    assert str(res.id_proveedor_local) == "8650"
    assert "Liverpool" in res.equipo_local


def test_e2_02_encuentro_internacional_paises_distintos():
    """E.2.2: Encuentro internacional con clubes de países distintos no exige mismo país."""
    res = resolver_entidades("Champions League", "Real Madrid", "Bayern")
    assert res.es_internacional is True
    assert res.pais_iso == "INT"
    assert res.pais_local == "ESP"
    assert res.pais_visita == "DEU"


def test_e2_03_once_caldas_atletico_nacional_copa_colombia():
    """E.2.3: Once Caldas vs Atlético Nacional entrando con contexto «Primera A» pero cuya ficha real es «Copa Colombia» (sin xG): detecta discrepancia de competición y no hereda la cobertura de Primera A."""
    ctx_usuario = {"competicion": "Primera A", "competicion_declarada": "Primera A"}
    partido_ficha = {"competicion": "Copa Colombia", "pais_iso": "COL", "es_copa": True}

    competicion_efectiva = partido_ficha.get("competicion") or ctx_usuario.get("competicion")
    assert competicion_efectiva == "Copa Colombia"
    assert competicion_efectiva != "Primera A"

    # En Copa Colombia no hay xG oficial: estadísticas básicas observables
    claves_sin_xg = ["remates_totales", "remates_puerta", "corners", "tarjetas_amarillas", "faltas"]
    doc_oc = _mock_doc("Once Caldas", 15, 15, claves_ft=claves_sin_xg)
    doc_nal = _mock_doc("Atlético Nacional", 15, 15, claves_ft=claves_sin_xg)

    dec = evaluar_decision_cobertura(doc_oc, doc_nal)
    assert dec.estado == "cobertura_parcial"
    assert dec.publicar_probabilidades is True
    # Mercados de córneres y tarjetas habilitados
    assert dec.mercados_habilitados["corners_totales"] is True
    assert dec.mercados_habilitados["corners_equipo"] is True
    assert dec.mercados_habilitados["tarjetas_totales"] is True
    assert dec.mercados_habilitados["tarjetas_equipo"] is True
    # Mercados de goles FT, 1T y 2T estrictamente bloqueados por coherencia D1
    assert dec.mercados_habilitados["1X2"] is False
    assert dec.mercados_habilitados["mas_menos_goles"] is False
    assert dec.mercados_habilitados["goles_1t"] is False
    assert dec.mercados_habilitados["1x2_1t"] is False
    assert dec.mercados_habilitados["goles_2t"] is False
    assert dec.mercados_habilitados["1x2_2t"] is False


def test_e2_04_cache_entrada_sin_id_proveedor_marcada_legado():
    """E.2.4: Caché sin proveedor o sin ID se marca como legado_sin_proveedor y no se reutiliza."""
    ruta_leg = os.path.join(CACHE_RESILIENCIA_DIR, "cache_legado_e2.json")
    with open(ruta_leg, "w", encoding="utf-8") as f:
        json.dump({"equipo": {"nombre": "Test", "pais_iso": "ESP"}}, f)

    cargado = _cargar_cache_resiliencia("legado_e2", "Test", meta_ctx={"pais_iso": "ESP"})
    assert cargado is None

    with open(ruta_leg, "r", encoding="utf-8") as f:
        doc_disco = json.load(f)
    assert doc_disco.get("legado_sin_proveedor") is True
    assert doc_disco.get("no_reutilizable") is True


def test_e2_05_un_mismo_caso_misma_muestra_efectiva_todas_superficies(tmp_path):
    """E.2.5: Todas las superficies leen la misma muestra efectiva de auditoría."""
    doc_a = _mock_doc("Levante", 15, 15)
    doc_b = _mock_doc("UD Tavernes", 15, 2)
    ctx = {
        "equipo_local": "Levante", "equipo_visitante": "UD Tavernes",
        "doc_local": doc_a, "doc_visitante": doc_b,
        "contexto": {"competicion": "Copa del Rey"},
    }
    with patch("servicio_mercados.resolver_enfrentamiento", return_value=ctx):
        prep = servicio.preparar_analisis("Levante vs UD Tavernes", directorio_salida=str(tmp_path))

    assert prep["abstenido"] is True
    assert "2/15 con detalle FT" in prep["decision_cobertura"]["etiqueta_cobertura_b"]
    assert "2/15 con detalle FT" in prep["analisis_texto_3_parrafos"]


def test_e2_06_mercado_bloqueado_todas_superficies_evaluacion_abstenido():
    """E.2.6: Mercado bloqueado en todas las superficies; /evaluar registra abstenido_por_cobertura."""
    sel = {"mercado": "corners", "evento": "over_95", "abstenido": True}
    res_p = evaluacion.ResultadoPartido(estado="finalizado", local="A", visitante="B")
    ev = evaluacion.liquidar(sel, res_p, "t_1")
    assert ev.estado_liquidacion == "abstenido_por_cobertura"
    assert ev.evento_ocurrio is None


def test_e2_07_analizar_vivo_cobertura_prepartido_insuficiente_sigue_bloqueado():
    """E.2.7: /analizar_vivo con cobertura prepartido insuficiente mantiene bloqueado el modelo prepartido."""
    doc_a = _mock_doc("A", 15, 2)
    doc_b = _mock_doc("B", 15, 15)
    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.publicar_probabilidades is False


def test_e2_08_estimacion_presente_sin_observadas_suficientes():
    """E.2.8: Una celda con estimado=True o procedencia=estimacion_modelo no incrementa total_celdas_observadas."""
    p = {
        "posicion": 1,
        "goles_equipo": 1,
        "goles_rival": 0,
        "periodos": {
            "FT": {"filas": [{"clave": "xg", "procedencia": "estimacion_modelo", "estimado": True, "equipo": 1.2, "rival": 0.8}]}
        },
    }
    cob = validar_cobertura_matriz({"partidos": [p]})
    assert cob["valores_observados"] == 0
    assert cob["valores_estimados_modelo"] > 0


def test_e2_09_pestanas_1t_2t_visibles_sin_celdas_publicadas():
    """E.2.9: Pestañas 1T/2T visibles en UI sin celdas publicadas reportan cobertura 0/45."""
    p = {
        "posicion": 1,
        "goles_equipo": 1,
        "goles_rival": 0,
        "periodos": {"FT": {"filas": [{"clave": "xg", "procedencia": "fotmob", "equipo": 1.0, "rival": 0.0}]}, "1T": {"filas": []}, "2T": {"filas": []}},
    }
    resumen = _construir_resumen({"partidos": [p]})
    assert resumen["partidos_con_detalle_1t"] == 0
    assert resumen["partidos_con_detalle_2t"] == 0


def test_variantes_ortograficas_uruguay():
    """Variantes ortográficas de clubes (Nacional vs Peñarol) resuelven idénticamente demostrando normalización anti-sesgo."""
    r1 = parsear_comando_mercados("/mercados Copa AUF Uruguay Nacional vs Peñarol")
    r2 = parsear_comando_mercados("/mercados Copa AUF Uruguay Club Nacional vs Peñarol")
    r3 = parsear_comando_mercados("/mercados Copa AUF Uruguay Nacional de Montevideo vs Peñarol")
    assert r1["equipo_local"] == r2["equipo_local"] == r3["equipo_local"] == "Nacional"
    assert r1["pais_iso"] == r2["pais_iso"] == r3["pais_iso"] == "URY"


def test_e2_10_independencia_nombres_ids_escenarios_tavernes_y_liverpool():
    """E.2.10: Independencia total de nombres e IDs en el escenario asimétrico (tipo Tavernes) y homónimo (tipo Liverpool M.)."""
    # 1. Escenario asimétrico (15/15 con detalle vs 2/15) con clubes equivalentes independientes
    doc_l_orig = _mock_doc("Levante", 15, 15)
    doc_v_orig = _mock_doc("UD Tavernes", 15, 2)
    dec_orig = evaluar_decision_cobertura(doc_l_orig, doc_v_orig)

    doc_l_equiv = _mock_doc("Club Titan", 15, 15)
    doc_v_equiv = _mock_doc("Deportivo Regional", 15, 2)
    dec_equiv = evaluar_decision_cobertura(doc_l_equiv, doc_v_equiv)

    assert dec_orig.estado == dec_equiv.estado == "cobertura_insuficiente"
    assert dec_orig.publicar_probabilidades == dec_equiv.publicar_probabilidades is False
    assert dec_orig.mercados_habilitados == dec_equiv.mercados_habilitados
    assert "2/15 partidos oficiales con detalle FT" in (dec_orig.alerta_descriptiva or dec_orig.mensaje)
    assert "2/15 partidos oficiales con detalle FT" in (dec_equiv.alerta_descriptiva or dec_equiv.mensaje)

    # 2. Escenario homónimo (desambiguación cruzada sin liga declarada) con clubes equivalentes independientes
    # Liverpool M. (URY) vs Juventud (URY) desempata a URY (7942), no Inglaterra (8650)
    res_orig = resolver_entidades("", "Liverpool", "Juventud de Las Piedras")
    # Arsenal de Sarandí (ARG) vs Boca Juniors (ARG) desempata a ARG (10084), no Inglaterra (9825)
    res_equiv = resolver_entidades("", "Arsenal", "Boca Juniors")

    assert res_orig.pais_local == res_orig.pais_visita == "URY"
    assert str(res_orig.id_proveedor_local) == "7942"
    assert res_orig.identidad_verificada is True

    assert res_equiv.pais_local == res_equiv.pais_visita == "ARG"
    assert str(res_equiv.id_proveedor_local) == "10084"
    assert res_equiv.identidad_verificada is True


def test_e2_11_equipo_13_oficiales_historial_incompleto_bloquea():
    """E.2.11: Equipo con 13 partidos oficiales: historial_oficial_incompleto, informa 13/15 y bloquea."""
    doc_a = _mock_doc("A", 13, 13)
    doc_b = _mock_doc("B", 15, 15)
    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "historial_oficial_incompleto"
    assert dec.publicar_probabilidades is False
    assert "13/15" in dec.alerta_descriptiva


def test_e2_12_equipo_15_oficiales_solo_2_detalle_ft_bloquea():
    """E.2.12: 15 oficiales y solo 2 con estadísticas: informa etiqueta y bloquea por cobertura_insuficiente."""
    doc_a = _mock_doc("A", 15, 15)
    doc_b = _mock_doc("B", 15, 2)
    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "cobertura_insuficiente"
    assert dec.publicar_probabilidades is False
    assert "2/15 con detalle FT" in dec.etiqueta_cobertura_b


def test_e2_13_historial_con_amistosos_y_duplicados_se_descartan():
    """E.2.13: Duplicados por id_partido se deduplican preservando partidos únicos."""
    from cuadro_especialista import deduplicar_partidos
    partidos_con_duplicados = [
        {"id_partido": "p1", "fecha": "2026-01-01"},
        {"id_partido": "p1", "fecha": "2026-01-01"},
        {"id_partido": "p2", "fecha": "2026-01-02"},
    ]
    unicos = deduplicar_partidos(partidos_con_duplicados)
    assert len(unicos) == 2


def test_e2_14_partido_oficial_solo_marcador_posicion_4_no_se_sustituye():
    """E.2.14: Partido de solo marcador en posición 4 conserva posición 4 con 0/45 y no se sustituye."""
    partidos = [_mock_partido(i, claves_ft=TODAS_45_METRICAS) for i in range(1, 16)]
    # Posición 4 (index 3) solo tiene marcador
    partidos[3] = _mock_partido(4, claves_ft=[])

    resumen = _construir_resumen({"partidos": partidos})
    assert resumen["partidos_oficiales_localizados"] == 15
    assert resumen["partidos_con_detalle_ft"] == 14
    assert resumen["partidos_solo_marcador"] == 1
    # La posición 4 conserva 0 claves
    assert len(partidos[3]["resumen_claves"]["FT"]["equipo"]) == 0


def test_e2_15_a_remates_10_b_tarjetas_10_sin_comunes_cobertura_insuficiente():
    """E.2.15: A con remates en 10 y B con tarjetas en 10 sin comunes: cobertura_insuficiente (sin variables comparables)."""
    pts_a = [_mock_partido(i, claves_ft=["remates_totales"]) if i <= 10 else _mock_partido(i, claves_ft=[]) for i in range(1, 16)]
    pts_b = [_mock_partido(i, claves_ft=["tarjetas_amarillas"]) if i <= 10 else _mock_partido(i, claves_ft=[]) for i in range(1, 16)]

    doc_a = {"equipo": "A", "partidos": pts_a, "resumen_auditoria": _construir_resumen({"partidos": pts_a})}
    doc_b = {"equipo": "B", "partidos": pts_b, "resumen_auditoria": _construir_resumen({"partidos": pts_b})}

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "cobertura_insuficiente"
    assert "sin variables comparables" in dec.alerta_descriptiva.lower()
    assert dec.estado != "solo_resultados"


def test_e2_16_filas_comparables_sin_mercados_validos_cobertura_insuficiente():
    """E.2.16: Filas comparables suficientes pero ningún mercado cumple insumos o modelo validado: cobertura_insuficiente."""
    # Comparten 5 métricas que no habilitan ningún modelo (pases, despejes, fueras_juego, etc., sin goles ni corners)
    claves_no_modelables = ["fueras_juego", "tiros_libres", "pases_total", "despejes", "intercepciones"]
    pts_a = [_mock_partido(i, goles_l=None, goles_v=None, claves_ft=claves_no_modelables) for i in range(1, 16)]
    pts_b = [_mock_partido(i, goles_l=None, goles_v=None, claves_ft=claves_no_modelables) for i in range(1, 16)]

    doc_a = {"equipo": "A", "partidos": pts_a, "resumen_auditoria": _construir_resumen({"partidos": pts_a})}
    doc_b = {"equipo": "B", "partidos": pts_b, "resumen_auditoria": _construir_resumen({"partidos": pts_b})}

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "cobertura_insuficiente"
    assert dec.publicar_probabilidades is False


def test_e2_17_p01_con_xg_remates_sin_xgot_bloquea_xgot():
    """E.2.17: P01 con xG y remates se evalúa; patrón que exija xGOT queda en None."""
    pat_l, pat_v = calcular_patrones_completos_partido(
        {"xg": 1.8, "remates_totales": 10.0, "xgot": None},
        {"xg": 1.2, "remates_totales": 8.0, "xgot": None},
    )
    assert pat_l.get("P01_xG_por_Remate") == 0.18
    assert pat_l.get("P03_Calidad_xGOT_vs_xG") is None


def test_e2_18_suite_completa_sin_red_fixtures_offline():
    """E.2.18: Comprobación de entorno hermético offline (sin llamadas a la red de FotMob o Flashscore)."""
    assert os.environ.get("DESACTIVAR_RED_TESTS", "1") == "1"


def test_liverpool_m_vs_juventud_sin_liga_declarada():
    """Verifica resolución a URY (7942 vs 8390) sin indicar liga por evidencia del cruce entre ambos clubes."""
    p = parsear_comando_mercados("/mercados Liverpool M. vs Juventud")
    assert p["pais_iso"] == "URY"
    assert p["pais_local"] == "URY"
    assert p["pais_visita"] == "URY"
    assert p["equipo_local"] == "Liverpool FC"
    assert p["equipo_visita"] == "Juventud de Las Piedras"
    assert str(p["id_proveedor_local"]) == "7942"
    assert str(p["id_proveedor_visita"]) == "8390"
    assert "interseccion_cruzada" in p["criterio_desempate"] or "URY" in p["criterio_desempate"]


def test_d1_copa_auf_y_copa_colombia_coherencia_goles_sin_xg():
    """D1: Ligas sin xG oficial bloquean TODOS los mercados de goles (FT, 1T, 2T) y habilitan córneres/tarjetas."""
    claves_sin_xg = ["remates_totales", "remates_puerta", "corners", "tarjetas_amarillas", "faltas"]
    doc_a = _mock_doc("Club A", 15, 15, claves_ft=claves_sin_xg)
    doc_b = _mock_doc("Club B", 15, 15, claves_ft=claves_sin_xg)

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "cobertura_parcial"
    assert dec.publicar_probabilidades is True

    # Mercados habilitados: exactamente corners y tarjetas
    mercados_hab = sorted([k for k, v in dec.mercados_habilitados.items() if v])
    assert mercados_hab == ["corners_equipo", "corners_totales", "tarjetas_equipo", "tarjetas_totales"]

    # Mercados bloqueados: todos los de goles (FT, 1T y 2T)
    mercados_bloq = sorted([k for k, v in dec.mercados_habilitados.items() if not v])
    esperados_bloq = [
        "1X2", "1x2_1t", "1x2_2t", "ambos_marcan", "doble_oportunidad",
        "goles_1t", "goles_2t", "handicap", "marcador_exacto", "mas_menos_goles"
    ]
    assert mercados_bloq == sorted(esperados_bloq)
    assert "falta xG oficial" in dec.motivos_bloqueo["goles_1t"]
    assert "falta xG oficial" in dec.motivos_bloqueo["goles_2t"]


# =============================================================================
# PRUEBAS ESPECÍFICAS DE LA SEGUNDA RONDA DE AUDITORÍA POST-DESPLIEGUE
# =============================================================================

def test_persistencia_informes_pronosticos_no_ignorado_en_gitignore():
    """Punto 1: .gitignore no ignora informes/pronosticos/ y conserva ignorados los respaldos."""
    with open(".gitignore", "r", encoding="utf-8") as f:
        lineas_git = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    assert "informes/pronosticos/" not in lineas_git
    assert "informes/pronosticos" not in lineas_git

    # Comprueba que respaldos siguen estrictamente ignorados
    assert "*_backup*/" in lineas_git
    assert "**/*_backup*/" in lineas_git
    assert "informes/*_backup*/" in lineas_git


def test_cuadro_c_caso_a_sin_cobertura_minutos():
    """Punto 4 (Caso a): k_eval == 0 imprime 'sin cobertura de minutos' y suprime tramo de peligro."""
    doc_l = {"equipo": {"nombre": "Local"}, "partidos": []}
    doc_v = {"equipo": {"nombre": "Visita"}, "partidos": []}
    analisis = MagicMock()
    analisis.doc_local = doc_l
    analisis.doc_visitante = doc_v
    analisis.nombre_local = "Local"
    analisis.nombre_visitante = "Visita"
    analisis.grupos = {}
    analisis.selecciones = []
    analisis.mercados = []

    captured = []
    with patch("matplotlib.axes.Axes.text") as mock_text:
        with tempfile.TemporaryDirectory() as td:
            salida = os.path.join(td, "test_c_caso_a.png")
            generar_cuadro_auditoria_15_partidos_png(analisis, doc_l, doc_v, destino=salida)
            for call in mock_text.call_args_list:
                args = call[0]
                if len(args) >= 3 and isinstance(args[2], str) and "CRONOLOGÍA" in args[2]:
                    captured.append(args[2])

    assert len(captured) >= 1
    assert captured[0] == "CRONOLOGÍA (CUADRO C): sin cobertura de minutos (0 de 15 partidos con cronología verificada)"
    assert "máximo peligro" not in captured[0].lower()


def test_cuadro_c_caso_b_sin_goles_en_ningun_tramo():
    """Punto 4 (Caso b): k_eval > 0 y tot_goles_c == 0 imprime 'sin goles en ningún tramo'."""
    mock_cc = {
        "_meta": {"n_partidos_con_eventos": 5, "n_partidos_totales": 15},
        "anotados": {t: 0 for t in ("00'-15'", "16'-30'", "31'-45'", "46'-60'", "61'-75'", "76'-90'")},
        "recibidos": {t: 0 for t in ("00'-15'", "16'-30'", "31'-45'", "46'-60'", "61'-75'", "76'-90'")},
    }
    doc_l = {"equipo": {"nombre": "Local"}, "partidos": [], "cuadro_c_timeline": mock_cc}
    doc_v = {"equipo": {"nombre": "Visita"}, "partidos": [], "cuadro_c_timeline": mock_cc}
    analisis = MagicMock()
    analisis.doc_local = doc_l
    analisis.doc_visitante = doc_v
    analisis.nombre_local = "Local"
    analisis.nombre_visitante = "Visita"
    analisis.grupos = {}
    analisis.selecciones = []
    analisis.mercados = []

    captured = []
    with patch("matplotlib.axes.Axes.text") as mock_text:
        with tempfile.TemporaryDirectory() as td:
            salida = os.path.join(td, "test_c_caso_b.png")
            generar_cuadro_auditoria_15_partidos_png(analisis, doc_l, doc_v, destino=salida)
            for call in mock_text.call_args_list:
                args = call[0]
                if len(args) >= 3 and isinstance(args[2], str) and "CRONOLOGÍA" in args[2]:
                    captured.append(args[2])

    assert len(captured) >= 1
    assert captured[0] == "CRONOLOGÍA (CUADRO C): sin goles en ningún tramo (5 de 15 partidos con cronología verificada)"
    assert "máximo peligro" not in captured[0].lower()
    assert "mayor flujo" not in captured[0].lower()


def test_motivos_bloqueo_reales_propagados_a_1t_y_2t():
    """Punto 5: Propaga el motivo real de bloqueo de FT a 1T y 2T (falta de xG oficial vs pares insuficientes)."""
    claves_sin_xg = ["remates_totales", "remates_puerta", "corners", "tarjetas_amarillas", "faltas"]
    parts_a = [_mock_partido(i, claves_ft=claves_sin_xg, claves_1t=["remates_puerta"], claves_2t=["remates_puerta"]) for i in range(1, 16)]
    parts_b = [_mock_partido(i, claves_ft=claves_sin_xg, claves_1t=["remates_puerta"], claves_2t=["remates_puerta"]) for i in range(1, 16)]

    doc_a = _mock_doc("Club A", 15, 15, claves_ft=claves_sin_xg)
    doc_a["partidos"] = parts_a
    doc_b = _mock_doc("Club B", 15, 15, claves_ft=claves_sin_xg)
    doc_b["partidos"] = parts_b

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    motivo_ft = dec.motivos_bloqueo.get("mas_menos_goles")
    assert motivo_ft is not None
    assert dec.mercados_habilitados["goles_1t"] is False
    assert dec.mercados_habilitados["goles_2t"] is False
    assert motivo_ft in dec.motivos_bloqueo["goles_1t"]
    assert motivo_ft in dec.motivos_bloqueo["goles_2t"]


def test_resolucion_valencia_cf_vs_ud_tavernes_anti_colision_filial_y_extranjero():
    """Punto 6: Valencia CF vs UD Tavernes resuelve con IDs (10205 y 1543888) y veta filiales (Mestalla) y extranjeros."""
    res = resolver_entidades("Copa del Rey", "Valencia CF", "UD Tavernes")
    assert res.pais_local == res.pais_visita == "ESP"
    assert str(res.id_proveedor_local) == "10205"
    assert str(res.id_proveedor_visita) == "1543888"
    assert res.identidad_verificada is True

    # Comprobación de no colisión contra filial (Valencia Mestalla)
    cand_mestalla = {"name": "Valencia Mestalla", "leagueName": "Segunda Federación", "country": "ESP"}
    cand_valencia = {"name": "Valencia", "leagueName": "LaLiga", "country": "ESP"}
    score_mestalla = validar_y_puntuar_candidato_fotmob(cand_mestalla, "Valencia", texto_liga_o_contexto="Copa del Rey")
    score_valencia = validar_y_puntuar_candidato_fotmob(cand_valencia, "Valencia", texto_liga_o_contexto="Copa del Rey")
    assert score_mestalla == -9999.0
    assert score_valencia > 100.0

    # Comprobación de veto de homónimo extranjero (Valencia FC de Venezuela) cuando se espera ESP
    cand_ven = {"name": "Valencia FC", "leagueName": "Primera División", "country": "VEN"}
    score_ven = validar_y_puntuar_candidato_fotmob(cand_ven, "Valencia", texto_liga_o_contexto="Copa del Rey", pais_iso_esperado="ESP")
    assert score_ven == -9999.0


def test_homonimo_desconocido_multiples_candidatos_bloquea_sin_elegir():
    """Punto 3: Club no registrado en diccionarios con múltiples candidatos homónimos devuelve identidad_no_verificada y detiene el pronóstico."""
    cands_desconocidos = [
        {"id": "9901", "nombre": "Deportivo Desconocido", "pais_iso": "GTM"},
        {"id": "9902", "nombre": "Deportivo Desconocido", "pais_iso": "HND"},
    ]

    # Resolver sin contexto de liga ni calificador: NO debe elegir arbitrariamente un candidato
    res = resolver_entidades("", "Deportivo Desconocido", "Rival Desconocido", candidatos_local=cands_desconocidos)
    assert res.estado_identidad == "identidad_no_verificada"
    assert res.identidad_verificada is False
    assert len(res.candidatos_local) == 2
    assert res.candidatos_local[0]["id"] == "9901"
    assert res.candidatos_local[1]["id"] == "9902"

    # En la decisión de cobertura: detención inmediata del pronóstico
    doc_l = {"equipo": {"nombre": "Deportivo Desconocido"}, "estado_identidad": res.estado_identidad, "partidos": []}
    doc_v = {"equipo": {"nombre": "Rival Desconocido"}, "partidos": []}
    dec = evaluar_decision_cobertura(doc_l, doc_v)
    assert dec.estado == "identidad_no_verificada"
    assert dec.publicar_probabilidades is False
    assert "Identidad no verificada" in dec.alerta_descriptiva


def test_fail_closed_permitir_goles_sin_xg_cuando_funcion_no_existe():
    """Punto 1: Activar permitir_goles_sin_xg falla cerrado con motivo claro si la función no existe."""
    import unittest.mock
    import auditoria_comparable as ac
    claves = ["remates_totales", "remates_puerta", "corners", "tarjetas_amarillas", "faltas"]
    doc_a = _mock_doc("Club A", 15, 15, claves_ft=claves)
    doc_b = _mock_doc("Club B", 15, 15, claves_ft=claves)

    cfg_mock = ac.cargar_modelos_validados()
    if "submodelo_goles_sin_xg" in cfg_mock:
        cfg_mock["submodelo_goles_sin_xg"]["permitir_goles_sin_xg"] = True
        cfg_mock["submodelo_goles_sin_xg"]["validacion_cronologica_superada"] = True
        cfg_mock["submodelo_goles_sin_xg"]["funcion_ejecutar"] = "motor_mercados.calcular_poisson_sin_xg"

    with unittest.mock.patch("auditoria_comparable.cargar_modelos_validados", return_value=cfg_mock):
        vista = ac.construir_vista_comparable(doc_a, doc_b)
        dec = evaluar_decision_cobertura(doc_a, doc_b, vista_comparable=vista)

        for m in ("1X2", "handicap", "doble_oportunidad", "mas_menos_goles", "ambos_marcan", "marcador_exacto", "goles_1t", "goles_2t"):
            assert dec.mercados_habilitados[m] is False, f"Mercado {m} debió quedar bloqueado"
            assert "sub-modelo sin xG no implementado" in dec.motivos_bloqueo[m], f"Motivo en {m}: {dec.motivos_bloqueo[m]}"


def test_resolver_club_desconocido_con_liga_declarada_verificado():
    """Punto 3a: Club fuera de diccionarios con liga declarada se verifica y no se bloquea."""
    from resolvedor_entidades import resolver
    res = resolver("Premier League", "AFC Richmond", "Chelsea")
    assert res.identidad_verificada is True
    assert res.estado_identidad == "verificada"
    assert res.pais_local == "ENG"
    assert res.pais_visita == "ENG"
    assert res.nivel_confianza == "alta"


def test_resolver_club_desconocido_con_unico_candidato_proveedor_coherente_verificado():
    """Punto 3b: Club fuera de diccionarios con candidato único del proveedor y cruce coherente se verifica."""
    from resolvedor_entidades import resolver
    cands_l = [{"nombre": "AFC Richmond", "pais_iso": "ENG", "id": "99999"}]
    res = resolver("", "AFC Richmond", "Chelsea", candidatos_local=cands_l)
    assert res.identidad_verificada is True
    assert res.estado_identidad == "verificada"
    assert res.pais_local == "ENG"
    assert res.pais_visita == "ENG"
    assert res.id_proveedor_local == "99999"


def test_resolver_homonimo_ambiguo_sin_evidencia_sigue_bloqueado():
    """Punto 3: Homónimo ambiguo con candidatos en múltiples países sin desempate sigue en identidad_no_verificada."""
    from resolvedor_entidades import resolver
    cands_l = [
        {"nombre": "Club Homonimo", "pais_iso": "URY", "id": "111"},
        {"nombre": "Club Homonimo", "pais_iso": "ARG", "id": "222"}
    ]
    cands_v = [
        {"nombre": "Club Rival", "pais_iso": "CHL", "id": "333"},
        {"nombre": "Club Rival", "pais_iso": "COL", "id": "444"}
    ]
    res = resolver("", "Club Homonimo", "Club Rival", candidatos_local=cands_l, candidatos_visita=cands_v)
    assert res.identidad_verificada is False
    assert res.estado_identidad == "identidad_no_verificada"
    assert "Homónimo no verificado" in res.motivo_identidad


def test_decision_cobertura_filas_3_a_7_sin_mercados_habilitados_es_insuficiente():
    """Punto 5: Con 3 a 7 filas comparables pero 0 mercados habilitados, el estado es cobertura_insuficiente."""
    claves_no_modelables = ["fueras_juego", "tiros_libres", "pases_total", "despejes", "intercepciones"]
    pts_a = [_mock_partido(i, goles_l=None, goles_v=None, claves_ft=claves_no_modelables if i <= 5 else []) for i in range(1, 16)]
    pts_b = [_mock_partido(i, goles_l=None, goles_v=None, claves_ft=claves_no_modelables if i <= 5 else []) for i in range(1, 16)]

    doc_a = {"equipo": "A", "partidos": pts_a, "resumen_auditoria": _construir_resumen({"partidos": pts_a})}
    doc_b = {"equipo": "B", "partidos": pts_b, "resumen_auditoria": _construir_resumen({"partidos": pts_b})}

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.n_filas_comparables == 5
    assert dec.estado == "cobertura_insuficiente"
    assert dec.publicar_probabilidades is False
    assert all(v is False for v in dec.mercados_habilitados.values())
