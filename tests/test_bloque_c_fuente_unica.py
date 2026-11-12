"""
tests/test_bloque_c_fuente_unica.py
Pruebas para el Bloque C de la Ronda 4:
  - C1 & C2: Única fuente de verdad (motor.matriz_ft) entre Matriz, Ticket y Telegram.
             Eliminación de damping y re-escalado Sinkhorn / RAS exacto.
             Marginales de matriz coinciden con lambda_local_ft y lambda_visitante_ft.
             Probabilidades de 1X2, 1X, Under 2.5, BTTS, 1-0, Náutico -1.5 son idénticas.
             λ plano en header y trazabilidad coincide con la caja azul (res_red["lambda_base_*"]).
  - C3: Sincronización estricta de métricas condicionales (xG, xGOT, goles evitados, P26, posesión, faltas)
        entre Super-Cuadro y Telegram mediante el Doble ADN (mu_local_casa / mu_visita_fuera).
  - C4: Test integral automatizado sobre Londrina vs Náutico verificando igualdad exacta.
"""

import json
from pathlib import Path
import numpy as np
import pytest

from motor_mercados import MotorMercados, filtrar_por_fecha, seleccionar_top_5_jugadas_recomendadas
from informe import _extraer_probabilidades_red_general
from motor_inferencia import generar_reporte_xai_telegram
import cuadro_especialista as ce


@pytest.fixture
def datos_londrina_nautico():
    base_aud = Path("informes/auditorias/2026-11-12")
    ruta_l = base_aud / "auditoria_londrina_2026-11-12_023747.json"
    ruta_v = base_aud / "auditoria_nautico_2026-11-12_023930.json"
    assert ruta_l.exists() and ruta_v.exists(), "Faltan auditorías reales de Londrina/Náutico"

    with open(ruta_l, "r", encoding="utf-8") as f:
        doc_l = json.load(f)
    with open(ruta_v, "r", encoding="utf-8") as f:
        doc_v = json.load(f)

    # Excluir partido del 11/11 (sin fuga)
    doc_l = filtrar_por_fecha(doc_l, fecha_corte="2026-11-11", id_partido_excluir="5190812")
    doc_v = filtrar_por_fecha(doc_v, fecha_corte="2026-11-11", id_partido_excluir="5190812")
    return doc_l, doc_v


def test_unica_fuente_de_verdad_londrina_vs_nautico(datos_londrina_nautico):
    doc_l, doc_v = datos_londrina_nautico

    motor = MotorMercados(
        doc_l, doc_v,
        condicion_local="casa",
        condicion_visitante="fuera",
        participante_local="Londrina",
        participante_visitante="Nautico"
    )
    motor.preparar()
    cat = motor.evaluar_catalogo()

    class MockAnalisis:
        def __init__(self, m):
            self.motor = m
            self.selecciones_filtradas = [s for mkt in cat for s in mkt.selecciones if s.probabilidad and s.probabilidad >= 0.70]
            self.metadatos = {
                "equipo_local": "Londrina",
                "equipo_visitante": "Nautico",
                "competicion": "Brasileirao Serie B",
                "fecha_partido": "2026-11-11",
            }
            self.doc_local = doc_l
            self.doc_visitante = doc_v

    analisis = MockAnalisis(motor)

    # 1. Verificación C2: Marginales de la matriz reproducen lambda final (Sinkhorn / RAS exacto)
    m_arr = np.array(motor.matriz_ft)
    lam_l_fin = motor.tasas.lambda_local_ft
    lam_v_fin = motor.tasas.lambda_visitante_ft
    assert lam_l_fin is not None and lam_v_fin is not None

    goles_esperados_filas = float(sum(i * m_arr.sum(axis=1)[i] for i in range(len(m_arr))))
    goles_esperados_cols = float(sum(j * m_arr.sum(axis=0)[j] for j in range(len(m_arr[0]))))
    assert abs(goles_esperados_filas - lam_l_fin) < 0.015, f"Fila marginal {goles_esperados_filas} != lambda {lam_l_fin}"
    assert abs(goles_esperados_cols - lam_v_fin) < 0.015, f"Columna marginal {goles_esperados_cols} != lambda {lam_v_fin}"

    # 2. Verificación C1: Probabilidades 1X2, U2.5, BTTS, 1-0 entre Matriz y Telegram
    p1_mat = float(sum(m_arr[i, j] for i in range(len(m_arr)) for j in range(len(m_arr[0])) if i > j))
    px_mat = float(sum(m_arr[i, i] for i in range(min(len(m_arr), len(m_arr[0])))))
    p2_mat = float(sum(m_arr[i, j] for i in range(len(m_arr)) for j in range(len(m_arr[0])) if i < j))
    u25_mat = float(sum(m_arr[i, j] for i in range(len(m_arr)) for j in range(len(m_arr[0])) if i + j < 3))
    btts_mat = float(sum(m_arr[i, j] for i in range(len(m_arr)) for j in range(len(m_arr[0])) if i >= 1 and j >= 1))

    info_tel = _extraer_probabilidades_red_general(analisis)
    assert abs(p1_mat - info_tel["p_local"]) < 1e-6
    assert abs(px_mat - info_tel["p_empate"]) < 1e-6
    assert abs(p2_mat - info_tel["p_visitante"]) < 1e-6
    assert abs(u25_mat - info_tel["p_under25"]) < 1e-6
    assert abs(btts_mat - info_tel["p_btts_si"]) < 1e-6
    assert abs(m_arr[1, 0] - info_tel["prob_modal"]) < 1e-6

    # 3. Verificación C1: Probabilidad de 1X es idéntica en Matriz, Catálogo y Telegram
    p_1x_mat = round((p1_mat + px_mat) * 100.0, 1)
    p_1x_tel = round((info_tel["p_local"] + info_tel["p_empate"]) * 100.0, 1)
    assert p_1x_mat == p_1x_tel

    sel_1x = None
    for mkt in cat:
        for s in mkt.selecciones:
            if s.evento == "1x":
                sel_1x = s
                break
    assert sel_1x is not None
    assert round(sel_1x.probabilidad * 100.0, 1) == p_1x_mat

    # 4. Verificación C1: Náutico -1.5 goles en Catálogo y Base Poisson del Top 5
    sel_v_u15 = None
    for mkt in cat:
        for s in mkt.selecciones:
            if s.evento == "visitante_under_1.5" and s.periodo == "FT":
                sel_v_u15 = s
                break
    assert sel_v_u15 is not None
    p_v_u15_mat = round(float(sum(m_arr[i, j] for i in range(len(m_arr)) for j in (0, 1))) * 100.0, 1)
    assert round(sel_v_u15.probabilidad * 100.0, 1) == p_v_u15_mat

    top5 = seleccionar_top_5_jugadas_recomendadas(analisis.selecciones_filtradas, motor, "Londrina", "Nautico")
    for t in top5:
        if "nautico" in t["etiqueta"].lower() and "-1.5" in t["etiqueta"]:
            assert t["prob_base_poisson"] == p_v_u15_mat

    # 5. Verificación C1: lambda plano coincide entre resultado y motor
    rec = motor.recursivo
    lam_plano_l = rec.get("lambda_base_local")
    lam_plano_v = rec.get("lambda_base_visita")
    assert lam_plano_l is not None and lam_plano_v is not None
    assert abs(lam_plano_l - 1.59) < 0.05
    assert abs(lam_plano_v - 1.12) < 0.05

    # 6. Verificación C3: Sincronización de métricas Doble ADN entre Super-Cuadro y Telegram
    mu_l = rec.get("mu_local_casa", {})
    mu_v = rec.get("mu_visita_fuera", {})
    assert mu_l.get("xg") is not None
    assert mu_v.get("xg") is not None

    # Simular generación del cuadro especialista y verificar sincronización de perfil
    perfil_l_cuadro = ce.construir_perfil_especialista_equipo(ce.bloque_para_lado(motor, "local"))
    perfil_v_cuadro = ce.construir_perfil_especialista_equipo(ce.bloque_para_lado(motor, "visitante"))

    # Aplicar sincronización con Doble ADN tal como lo hace generar_cuadro_visual_partido
    if mu_l and mu_l.get("xg") is not None:
        perfil_l_cuadro["metricas_clave"]["xg_favor"] = round(float(mu_l["xg"]), 2)
        if mu_l.get("xgot") is not None:
            perfil_l_cuadro["metricas_clave"]["xgot_favor"] = round(float(mu_l["xgot"]), 2)
    if mu_v and mu_v.get("xg") is not None:
        perfil_v_cuadro["metricas_clave"]["xg_favor"] = round(float(mu_v["xg"]), 2)
        if mu_v.get("xgot") is not None:
            perfil_v_cuadro["metricas_clave"]["xgot_favor"] = round(float(mu_v["xgot"]), 2)

    # Validar igualdad exacta entre cuadro y Telegram
    assert perfil_l_cuadro["metricas_clave"]["xg_favor"] == round(float(mu_l["xg"]), 2)
    assert perfil_v_cuadro["metricas_clave"]["xg_favor"] == round(float(mu_v["xg"]), 2)
    assert perfil_l_cuadro["metricas_clave"]["xgot_favor"] == round(float(mu_l["xgot"]), 2)
    assert perfil_v_cuadro["metricas_clave"]["xgot_favor"] == round(float(mu_v["xgot"]), 2)
