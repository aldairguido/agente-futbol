"""
test_fase3_vista_comparable.py
Pruebas de la Fase 3: Emparejamiento por Rango de Recencia,
Vista Comparable Simétrica y Registro de Modelos Validados (Secciones 3 y 4).
"""

from __future__ import annotations

import copy
import pytest

from auditoria_comparable import (
    cargar_modelos_validados,
    emparejar_historicos_por_recencia,
    construir_vista_comparable,
    verificar_compatibilidad_modelo,
    generar_tabla_comparable_markdown,
)


def _crear_partido_mock(
    pos: int,
    goles_l: int = 1,
    goles_v: int = 0,
    claves_ft: list[str] | None = None,
    claves_1t: list[str] | None = None,
    claves_2t: list[str] | None = None,
) -> dict:
    claves_ft = claves_ft if claves_ft is not None else ["xg", "remates_totales", "remates_puerta", "corners", "tarjetas_amarillas"]
    claves_1t = claves_1t if claves_1t is not None else ["remates_puerta"]
    claves_2t = claves_2t if claves_2t is not None else ["remates_puerta"]

    def _filas(claves):
        return [{"clave": c, "procedencia": "observada", "valor_local": 1.0, "valor_visita": 0.0} for c in claves]

    return {
        "posicion": pos,
        "goles_equipo": goles_l,
        "goles_rival": goles_v,
        "resumen_claves": {
            "FT": {"equipo": claves_ft, "rival": claves_ft},
            "1T": {"equipo": claves_1t, "rival": claves_1t},
            "2T": {"equipo": claves_2t, "rival": claves_2t},
        },
        "periodos": {
            "FT": {"filas": _filas(claves_ft)},
            "1T": {"filas": _filas(claves_1t)},
            "2T": {"filas": _filas(claves_2t)},
        },
    }


def test_carga_modelos_validados_yaml():
    modelos = cargar_modelos_validados()
    assert "red_recursiva_45_dcn" in modelos
    assert "poisson_bivariado_goles" in modelos
    assert "modelo_mercado_corners" in modelos
    assert "modelo_mercado_tarjetas" in modelos
    assert "modelo_mercado_1t" in modelos
    assert "modelo_mercado_2t" in modelos

    # Comprobar restricciones de red 45
    red45 = modelos["red_recursiva_45_dcn"]
    assert red45["min_claves_comunes_ft"] == 45
    assert red45["min_pares_con_dato"] == 15
    assert red45["permite_imputacion"] is False
    assert len(red45["variables_requeridas"]) == 45


def test_simetria_inmutable_vista_comparable():
    # A tiene métricas estándar + posesion_pct + faltas
    # B tiene métricas estándar + despejes
    partidos_a = [
        _crear_partido_mock(
            i,
            claves_ft=["xg", "remates_totales", "remates_puerta", "corners", "posesion_pct", "faltas"],
            claves_1t=["remates_puerta", "corners"],
            claves_2t=["remates_puerta"],
        )
        for i in range(1, 16)
    ]
    partidos_b = [
        _crear_partido_mock(
            i,
            claves_ft=["xg", "remates_totales", "remates_puerta", "corners", "despejes"],
            claves_1t=["remates_puerta"],
            claves_2t=["remates_puerta", "corners"],
        )
        for i in range(1, 16)
    ]

    doc_a = {"equipo": "Club A", "partidos": partidos_a}
    doc_b = {"equipo": "Club B", "partidos": partidos_b}

    doc_a_copia = copy.deepcopy(doc_a)
    doc_b_copia = copy.deepcopy(doc_b)

    vista_ab = construir_vista_comparable(doc_a, doc_b)
    vista_ba = construir_vista_comparable(doc_b, doc_a)

    # 1. Comprobar no mutación
    assert doc_a == doc_a_copia, "La extracción de A no debe ser mutada"
    assert doc_b == doc_b_copia, "La extracción de B no debe ser mutada"

    # 2. Comprobar simetría estricta de claves comunes
    assert len(vista_ab["emparejamientos"]) == len(vista_ba["emparejamientos"]) == 15
    for idx in range(15):
        emp_ab = vista_ab["emparejamientos"][idx]
        emp_ba = vista_ba["emparejamientos"][idx]
        for per in ("FT", "1T", "2T"):
            comunes_ab = emp_ab["periodos"][per]["claves_comunes"]
            comunes_ba = emp_ba["periodos"][per]["claves_comunes"]
            assert comunes_ab == comunes_ba, f"Discrepancia en fila {idx+1} periodo {per}"

    # En FT las comunes deben ser exactamente: corners, remates_puerta, remates_totales, xg (4 claves)
    comunes_ft_0 = vista_ab["emparejamientos"][0]["periodos"]["FT"]["claves_comunes"]
    assert comunes_ft_0 == ["corners", "remates_puerta", "remates_totales", "xg"]
    # posesion_pct y faltas eran exclusivas de A; despejes era exclusiva de B
    assert "posesion_pct" not in comunes_ft_0
    assert "faltas" not in comunes_ft_0
    assert "despejes" not in comunes_ft_0

    # 3. Comprobar que en la proyección 'vista_a' y 'vista_b', solo figuran claves comunes
    for f in vista_ab["vista_a"][0]["periodos"]["FT"]["filas"]:
        assert f["clave"] in comunes_ft_0
    for f in vista_ab["vista_b"][0]["periodos"]["FT"]["filas"]:
        assert f["clave"] in comunes_ft_0


def test_prohibicion_red_45_sobre_subconjuntos_rellenados():
    # Caso 1: A tiene 45 métricas pero B solo tiene 10
    from auditoria_historica import LAS_45_METRICAS_OFICIALES
    todas_45 = [m[1] for m in LAS_45_METRICAS_OFICIALES]
    primeras_10 = todas_45[:10]

    partidos_a_45 = [_crear_partido_mock(i, claves_ft=todas_45) for i in range(1, 16)]
    partidos_b_10 = [_crear_partido_mock(i, claves_ft=primeras_10) for i in range(1, 16)]

    vista = construir_vista_comparable(
        {"partidos": partidos_a_45},
        {"partidos": partidos_b_10},
    )

    assert vista["modelos_habilitados"]["red_recursiva_45_dcn"] is False
    assert "Red 45-DCN bloqueada" in vista["motivos_bloqueo_modelos"]["red_recursiva_45_dcn"]
    assert vista["pares_completos_por_modelo"]["red_recursiva_45_dcn"] == 0

    # Caso 2: Ambos tienen 45 métricas completas en los 15 partidos
    partidos_b_45 = [_crear_partido_mock(i, claves_ft=todas_45) for i in range(1, 16)]
    vista_completa = construir_vista_comparable(
        {"partidos": partidos_a_45},
        {"partidos": partidos_b_45},
    )
    assert vista_completa["modelos_habilitados"]["red_recursiva_45_dcn"] is True
    assert vista_completa["pares_completos_por_modelo"]["red_recursiva_45_dcn"] == 15


def test_compatibilidad_poisson_bivariado_y_mercados_univariados():
    # Partidos con xG, remates_puerta y corners en 10 filas; faltan en 5
    partidos_a = [
        _crear_partido_mock(i, claves_ft=["xg", "remates_puerta", "corners"]) if i <= 10
        else _crear_partido_mock(i, claves_ft=[])
        for i in range(1, 16)
    ]
    partidos_b = [
        _crear_partido_mock(i, claves_ft=["xg", "remates_puerta", "corners"]) if i <= 12
        else _crear_partido_mock(i, claves_ft=[])
        for i in range(1, 16)
    ]

    vista = construir_vista_comparable(
        {"partidos": partidos_a},
        {"partidos": partidos_b},
    )

    # Bivariado de goles: requiere goles, xg, remates_puerta (mínimo 8 pares)
    # Aquí 10 filas tienen xg y remates_puerta en ambos
    assert vista["modelos_habilitados"]["poisson_bivariado_goles"] is True
    assert vista["pares_completos_por_modelo"]["poisson_bivariado_goles"] == 10

    # Corners: requiere corners (mínimo 8 pares) -> 10 pares disponibles
    assert vista["modelos_habilitados"]["modelo_mercado_corners"] is True
    assert vista["pares_completos_por_modelo"]["modelo_mercado_corners"] == 10

    # Tarjetas: requiere tarjetas_amarillas -> 0 disponibles
    assert vista["modelos_habilitados"]["modelo_mercado_tarjetas"] is False
    assert "Cobertura insuficiente" in vista["motivos_bloqueo_modelos"]["modelo_mercado_tarjetas"]
    assert vista["pares_completos_por_modelo"]["modelo_mercado_tarjetas"] == 0


def test_generacion_tabla_comparable_markdown():
    partidos_a = [_crear_partido_mock(i) for i in range(1, 16)]
    partidos_b = [_crear_partido_mock(i) for i in range(1, 16)]

    emparejamientos = emparejar_historicos_por_recencia(partidos_a, partidos_b)
    md = generar_tabla_comparable_markdown(emparejamientos)

    lineas = md.strip().split("\n")
    assert lineas[0] == "| Fila por recencia | Periodo | A observado | B observado | Claves comunes | Uso en el análisis |"
    assert lineas[1] == "| :--- | :---: | :---: | :---: | :---: | :--- |"
    # 15 filas x 3 periodos + 2 encabezados = 47 líneas
    assert len(lineas) == 47

    # Verificar que la primera fila contenga FT
    assert "| 1 | FT |" in lineas[2]
