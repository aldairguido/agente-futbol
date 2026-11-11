"""
test_fase4_decision_cobertura.py
Pruebas de la Fase 4: Función Pura de Decisión de Cobertura y Precedencia Estricta D.1 (Sección 5).
"""

from __future__ import annotations

import pytest

from decision_cobertura import (
    evaluar_decision_cobertura,
    cargar_umbrales_cobertura,
    DecisionCobertura,
)
from auditoria_historica import LAS_45_METRICAS_OFICIALES


def _crear_doc_mock(
    equipo: str,
    n_oficiales: int = 15,
    n_marcador: int = 15,
    n_detalle_ft: int = 15,
    claves_ft: list[str] | None = None,
    claves_1t: list[str] | None = None,
    claves_2t: list[str] | None = None,
    estado_identidad: str = "verificado",
) -> dict:
    claves_ft = claves_ft if claves_ft is not None else ["xg", "remates_totales", "remates_puerta", "corners", "tarjetas_amarillas"]
    claves_1t = claves_1t if claves_1t is not None else ["remates_puerta"]
    claves_2t = claves_2t if claves_2t is not None else ["remates_puerta"]

    partidos = []
    for i in range(1, n_oficiales + 1):
        tiene_det = i <= n_detalle_ft
        c_ft = claves_ft if tiene_det else []
        c_1t = claves_1t if tiene_det else []
        c_2t = claves_2t if tiene_det else []

        filas_ft = [{"clave": c, "procedencia": "observada", "valor_local": 1.0, "valor_visita": 0.0} for c in c_ft]
        filas_1t = [{"clave": c, "procedencia": "observada", "valor_local": 1.0, "valor_visita": 0.0} for c in c_1t]
        filas_2t = [{"clave": c, "procedencia": "observada", "valor_local": 1.0, "valor_visita": 0.0} for c in c_2t]

        partidos.append({
            "posicion": i,
            "goles_equipo": 1 if i <= n_marcador else None,
            "goles_rival": 0 if i <= n_marcador else None,
            "resumen_claves": {
                "FT": {"equipo": c_ft, "rival": c_ft},
                "1T": {"equipo": c_1t, "rival": c_1t},
                "2T": {"equipo": c_2t, "rival": c_2t},
            },
            "periodos": {
                "FT": {"filas": filas_ft},
                "1T": {"filas": filas_1t},
                "2T": {"filas": filas_2t},
            },
        })

    n_solo_marcador = max(0, n_marcador - n_detalle_ft)
    return {
        "equipo": equipo,
        "estado_identidad": estado_identidad,
        "resumen_auditoria": {
            "equipo": equipo,
            "partidos_oficiales_localizados": n_oficiales,
            "partidos_con_marcador": n_marcador,
            "partidos_con_detalle_ft": n_detalle_ft,
            "partidos_solo_marcador": n_solo_marcador,
            "etiqueta_cobertura": f"{n_oficiales}/15 oficiales · {n_detalle_ft}/15 con detalle FT · {n_solo_marcador}/15 solo con marcador",
        },
        "partidos": partidos,
    }


def test_precedencia_1_identidad_no_verificada():
    # Si identidad no está verificada, debe prevalecer sobre cualquier otro déficit
    doc_a = _crear_doc_mock("Equipo Fantasma", n_oficiales=5, n_detalle_ft=0, estado_identidad="identidad_no_verificada")
    doc_b = _crear_doc_mock("Real Madrid", n_oficiales=15, n_detalle_ft=15)

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "identidad_no_verificada"
    assert dec.publicar_probabilidades is False
    assert "Identidad no verificada" in dec.alerta_descriptiva
    assert all(val is False for val in dec.mercados_habilitados.values())


def test_precedencia_2_historial_oficial_incompleto():
    # Identidad ok, pero un equipo tiene < 15 oficiales
    doc_a = _crear_doc_mock("Club A", n_oficiales=12, n_detalle_ft=10)
    doc_b = _crear_doc_mock("Club B", n_oficiales=15, n_detalle_ft=15)

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "historial_oficial_incompleto"
    assert dec.publicar_probabilidades is False
    assert "Historial oficial incompleto: 12/15" in dec.alerta_descriptiva


def test_precedencia_3_solo_resultados_caso_anaitasuna():
    # 15 oficiales con marcador, pero Anaitasuna tiene 0 con detalle estadístico
    doc_a = _crear_doc_mock("Real Sociedad", n_oficiales=15, n_marcador=15, n_detalle_ft=15)
    doc_b = _crear_doc_mock("CD Anaitasuna", n_oficiales=15, n_marcador=15, n_detalle_ft=0)

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "solo_resultados"
    assert dec.publicar_probabilidades is False
    assert "Solo resultados" in dec.alerta_descriptiva
    assert "CD Anaitasuna" in dec.alerta_descriptiva
    assert all(val is False for val in dec.mercados_habilitados.values())


def test_precedencia_4a_cobertura_insuficiente_caso_tavernes():
    # Tavernes tiene solo 2 partidos con detalle (< umbral de 3) y 13 solo con marcador
    doc_a = _crear_doc_mock("Levante UD", n_oficiales=15, n_marcador=15, n_detalle_ft=15)
    doc_b = _crear_doc_mock("UD Tavernes", n_oficiales=15, n_marcador=15, n_detalle_ft=2)

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "cobertura_insuficiente"
    assert dec.publicar_probabilidades is False
    assert "2/15 partidos oficiales con detalle FT" in dec.alerta_descriptiva
    assert "UD Tavernes" in dec.alerta_descriptiva


def test_precedencia_4b_cobertura_insuficiente_sin_variables_comparables():
    # Ambos tienen 15 partidos con detalle, pero no comparten claves comunes (claves disjuntas)
    doc_a = _crear_doc_mock("Club A", n_oficiales=15, n_detalle_ft=15, claves_ft=["xg", "posesion_pct", "pases_total", "faltas"])
    doc_b = _crear_doc_mock("Club B", n_oficiales=15, n_detalle_ft=15, claves_ft=["corners", "tarjetas_amarillas", "despejes", "paradas"])

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "cobertura_insuficiente"
    assert dec.publicar_probabilidades is False
    assert "sin variables comparables" in dec.alerta_descriptiva.lower()


def test_estado_5_cobertura_parcial_mercados_selectivos():
    # Ambos tienen goles, xg y remates_puerta en 15 partidos, pero no tienen corners ni tarjetas
    doc_a = _crear_doc_mock("Club A", n_oficiales=15, n_detalle_ft=15, claves_ft=["xg", "remates_totales", "remates_puerta", "posesion_pct", "faltas"])
    doc_b = _crear_doc_mock("Club B", n_oficiales=15, n_detalle_ft=15, claves_ft=["xg", "remates_totales", "remates_puerta", "posesion_pct", "faltas"])

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "cobertura_parcial"
    assert dec.publicar_probabilidades is True
    # Mercados de goles y 1X2 vía Poisson bivariado deben estar habilitados
    assert dec.mercados_habilitados["1X2"] is True
    assert dec.mercados_habilitados["mas_menos_goles"] is True
    assert dec.modelos_activos["1X2"] == "poisson_bivariado_goles"

    # Mercados de córneres y tarjetas deben estar bloqueados
    assert dec.mercados_habilitados["corners_totales"] is False
    assert dec.mercados_habilitados["tarjetas_totales"] is False
    assert "corners_totales" in dec.motivos_bloqueo


def test_estado_6_cobertura_suficiente_red_45_completa():
    todas_45 = [m[1] for m in LAS_45_METRICAS_OFICIALES]
    doc_a = _crear_doc_mock("Liverpool", n_oficiales=15, n_detalle_ft=15, claves_ft=todas_45, claves_1t=["remates_puerta"], claves_2t=["remates_puerta"])
    doc_b = _crear_doc_mock("Arsenal", n_oficiales=15, n_detalle_ft=15, claves_ft=todas_45, claves_1t=["remates_puerta"], claves_2t=["remates_puerta"])

    dec = evaluar_decision_cobertura(doc_a, doc_b)
    assert dec.estado == "cobertura_suficiente"
    assert dec.publicar_probabilidades is True
    assert dec.modelos_activos["1X2"] == "red_recursiva_45_dcn"
    assert all(dec.mercados_habilitados[m] is True for m in dec.mercados_habilitados)
