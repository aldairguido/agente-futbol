# -*- coding: utf-8 -*-
"""
Tests reales del Bloque 2 (Ronda 5+6):
  2.1 Traza ordenada completa de lambda (`traza_lambda`) sin alterar lambda final.
  2.3 Auditoria Italia vs Francia (coeficiente liga/tier -> 1.23/1.72 -> 1.03/2.33).
  2.4 Separacion 90'-FT vs prorroga/penaltis (Bosnia 1-1 Italy vs France 4-6 England).
  2.6 Independencia de pesos no entrenados en 45-DCN y Brier inalterado.
  2.7 Aislamiento estricto del LLM frente a lambdas, probabilidades, umbrales, Top 5 y evaluacion.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agente_consciente import AgenteConsciente, sanitizar_respuesta_llm_texto
from fuente_flashscore import resolver_marcador_reglamentario_flashscore
from fuentes_xg import (
    FuenteFotMob,
    PartidoFinalizado,
    resolver_marcador_reglamentario_fotmob,
)
from motor_mercados import analizar
from razonador_cuantitativo import RazonadorCuantitativo


FIXTURES_R5 = json.loads((ROOT / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))
FIXTURES_LN = json.loads((ROOT / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))


def test_traza_lambda_ordenada_y_continua_sin_cambiar_lambdas():
    casos = [
        ("turkey_belgium", FIXTURES_R5["docs"]["turkey"], FIXTURES_R5["docs"]["belgium"], (1.04, 3.55)),
        ("italy_france", FIXTURES_R5["docs"]["italy"], FIXTURES_R5["docs"]["france"], (1.03, 2.33)),
        ("londrina_nautico", FIXTURES_LN["londrina"], FIXTURES_LN["nautico"], (1.14, 0.85)),
    ]
    for nombre, dl, dv, esperado in casos:
        a = analizar(dl, dv, dl["equipo"]["nombre"], dv["equipo"]["nombre"])
        assert (a.motor.tasas.lambda_local_ft, a.motor.tasas.lambda_visitante_ft) == esperado, nombre

        traza = a.motor.traza_lambda
        assert isinstance(traza, list) and len(traza) >= 8, f"{nombre}: traza muy corta"
        assert [s["paso"] for s in traza] == list(range(1, len(traza) + 1))

        # Continuidad: la salida de un paso coincide (salvo redondeo intermedio a 2 dec) con la entrada del siguiente
        for prev, nxt in zip(traza, traza[1:]):
            assert abs(float(prev["lam_l_despues"]) - float(nxt["lam_l_antes"])) <= 0.011, (nombre, prev, nxt)
            assert abs(float(prev["lam_v_despues"]) - float(nxt["lam_v_antes"])) <= 0.011, (nombre, prev, nxt)

        assert abs(float(traza[-1]["lam_l_despues"]) - esperado[0]) <= 0.01
        assert abs(float(traza[-1]["lam_v_despues"]) - esperado[1]) <= 0.01
        assert a.motor.recursivo.get("traza_lambda") == traza
        assert a.motor.detalle_goles.get("traza_lambda") == traza
        assert "### Traza ordenada de λ (`traza_lambda`)" in a.informe
        assert traza[0]["regla"] in a.informe
        assert traza[-1]["regla"] in a.informe


def test_italy_france_explica_coef_liga_y_baja_italia_francia():
    dl = FIXTURES_R5["docs"]["italy"]
    dv = FIXTURES_R5["docs"]["france"]
    a = analizar(dl, dv, "Italy", "France")
    etapas_reglas = [(s["etapa"], s["regla"]) for s in a.motor.traza_lambda]
    assert ("coef_liga", "ajuste_coef_liga_tier") in etapas_reglas
    paso_coef = [s for s in a.motor.traza_lambda if s["regla"] == "ajuste_coef_liga_tier"][0]
    assert float(paso_coef["lam_l_despues"]) < float(paso_coef["lam_l_antes"])
    assert float(paso_coef["lam_v_despues"]) > float(paso_coef["lam_v_antes"])
    assert "tier_coef=" in paso_coef["motivo"]
    assert ("red_45_dcn", "prohibicion_inflacion_dual") in etapas_reglas


def test_parser_90ft_flashscore_separa_penaltis_de_tiempo_reglamentario():
    # Caso Bosnia & Herzegovina vs Italy (Pen: 5-2 en tanda, 1-1 en 90')
    gl, gv, desemp = resolver_marcador_reglamentario_flashscore(
        ["5", "2", "(1)", "(1)"], estado_extra="Pen"
    )
    assert (gl, gv) == (1, 1)
    assert desemp and "5-2" in desemp

    # Caso prórroga AET con marcador 90' entre paréntesis
    gl_aet, gv_aet, des_aet = resolver_marcador_reglamentario_flashscore(
        ["2", "1", "(1)", "(1)"], estado_extra="AET"
    )
    assert (gl_aet, gv_aet) == (1, 1)
    assert des_aet and "AET" in des_aet

    # Caso France 4-6 England (90' reglamentarios reales sin penaltis ni prórroga)
    gl_n, gv_n, des_n = resolver_marcador_reglamentario_flashscore(
        ["4", "6"], estado_extra=""
    )
    assert (gl_n, gv_n) == (4, 6)
    assert des_n is None


def test_parser_90ft_fotmob_y_matriz_partido_no_inflan_2t_con_penaltis():
    # Verificar registro archivado afectado vs normal en fixtures
    p_bos = [p for p in FIXTURES_R5["docs"]["italy"]["partidos"] if str(p.get("id_partido")) == "5067299"][0]
    p_eng = [p for p in FIXTURES_R5["docs"]["france"]["partidos"] if str(p.get("id_partido")) == "4653857"][0]
    assert p_bos["marcador"]["texto"] == "2-5"  # bug pre-fix documentado en fixture histórico
    assert p_eng["marcador"]["texto"] == "4-6"

    # FotMob detail sintético fiel a Bosnia 1-1 Italy (Pen 5-2)
    detalle_pen = {
        "header": {
            "status": {
                "scoreStr": "5 - 2",
                "scoreBeforeExtraTime": "1 - 1",
                "reason": {"short": "Pen", "longKey": "afterpenalties"},
            }
        },
        "content": {
            "stats": {"Periods": {}},
            "shotmap": {
                "shots": [
                    {"eventType": "Goal", "teamId": "away", "period": "FirstHalf", "shotType": "Header"},
                    {"eventType": "Goal", "teamId": "home", "period": "PenaltyShootout", "shotType": "RightFoot"},
                ]
            },
            "matchFacts": {
                "events": {
                    "events": [
                        {"type": "Goal", "time": 24, "isHome": False},
                        {"type": "Half", "time": 45, "halfStrShort": "HT", "newScore": [0, 1]},
                        {"type": "Goal", "time": 78, "isHome": True},
                        {"type": "Half", "time": 90, "halfStrShort": "FT", "newScore": [1, 1]},
                        {"type": "Half", "time": 120, "halfStrShort": "Pen"},
                        {"type": "Goal", "time": 121, "isHome": True, "isShootoutGoal": True},
                    ]
                }
            },
        },
    }
    gl_90, gv_90, _raz, desemp = resolver_marcador_reglamentario_fotmob(
        detalle_pen["header"]["status"], 5, 2, detalle=detalle_pen
    )
    assert (gl_90, gv_90) == (1, 1)
    assert desemp and "5-2" in desemp

    # Caso France 4-6 England (90'-FT normal): NO altera 4-6
    detalle_90 = {
        "header": {"status": {"scoreStr": "4 - 6", "reason": {"short": "FT", "longKey": "fulltime"}}},
        "content": {"matchFacts": {"events": {"events": [{"type": "Half", "time": 45, "halfStrShort": "HT", "newScore": [0, 4]}]}}},
    }
    gl_r, gv_r, _raz_r, des_r = resolver_marcador_reglamentario_fotmob(
        detalle_90["header"]["status"], 4, 6, detalle=detalle_90
    )
    assert (gl_r, gv_r) == (4, 6)
    assert des_r is None

    # End-to-end FuenteFotMob.matriz_partido con cliente mockeado
    fuente = FuenteFotMob(pausa_entre_partidos=0.0, verbose=False)
    fuente._api = lambda ruta: detalle_pen
    fuente._rellenar_desde_flashscore = lambda *_a, **_kw: None
    partido = PartidoFinalizado(
        id_partido="5067299",
        fecha="2026-03-31",
        torneo="World Cup Qualifiers UEFA",
        local="Bosnia and Herzegovina",
        visitante="Italy",
        goles_local=5,
        goles_visitante=2,
        es_local_el_equipo=False,
        extra={"razon_fin": "Pen"},
    )
    mat = fuente.matriz_partido(partido, {"id": "away", "nombre": "Italy"})
    assert (mat.goles_equipo, mat.goles_rival) == (1, 1)
    idx_ft = {f["clave"]: f for f in mat.periodos["FT"].filas}
    idx_1t = {f["clave"]: f for f in mat.periodos["1T"].filas}
    idx_2t = {f["clave"]: f for f in mat.periodos["2T"].filas}
    assert idx_ft["goles_encajados"]["equipo"] == 1.0
    assert idx_1t["goles_encajados"]["equipo"] == 0.0
    assert idx_2t["goles_encajados"]["equipo"] == 1.0
    assert idx_1t["goles_cabeza"]["equipo"] == 1.0
    assert idx_2t["goles_cabeza"]["rival"] == 0.0  # ignoró PenaltyShootout


def test_aislamiento_estricto_llm_no_toca_numeros_umbrales_ni_top5(tmp_path):
    # 1) Sanitizer bloquea intentos JSON o de sobrescribir parámetros numéricos
    limpio, viol = sanitizar_respuesta_llm_texto('{"probabilidad_real_estimada": 0.99, "lambda_local": 5.0}')
    assert viol and "json_override_rechazado" in viol
    assert "0.99" not in limpio

    # 2) AgenteConsciente.diagnosticar_enfrentamiento con mock adversarial del LLM
    dl = FIXTURES_R5["docs"]["turkey"]
    dv = FIXTURES_R5["docs"]["belgium"]
    a = analizar(dl, dv, "Turkey", "Belgium")
    lam_l_orig = a.motor.tasas.lambda_local_ft
    lam_v_orig = a.motor.tasas.lambda_visitante_ft

    mem_tmp = tmp_path / "mem_test.json"
    mem_tmp.write_text('{"experiencias": [], "madurez": 0}', encoding="utf-8")
    ag = AgenteConsciente(archivo_memoria=str(mem_tmp))
    eval_orig = copy.deepcopy(ag.evaluacion_real)

    def _llm_adversarial(*_args, **_kwargs):
        # Intenta mutar el análisis y devolver JSON fraudulento
        a.motor.tasas.lambda_local_ft = 9.99
        return '{"probabilidad": 0.99, "lambda": 9.9, "stake": "40%", "top_5": ["INVENTADO"]}'

    ag.motor_neuronal._asegurar_cliente = lambda: True
    ag.motor_neuronal.generar = _llm_adversarial

    res = ag.diagnosticar_enfrentamiento("Turkey", "Belgium", analisis_mercados=a, usar_llm=True)
    assert res["llm_solo_explicativo"] is True
    assert res["llm_aislamiento_verificado"] is True
    assert ag.evaluacion_real == eval_orig
    assert a.motor.tasas.lambda_local_ft == lam_l_orig
    assert a.motor.tasas.lambda_visitante_ft == lam_v_orig
    assert "0.99" not in res["reflexion_metacognitiva"]

    # 3) RazonadorCuantitativo: Gemini malicioso no altera decision_final, prob, stake ni cuota
    raz = RazonadorCuantitativo()
    payload = {
        "Partido": "Turkey vs Belgium",
        "Cuotas_Casa": {"1": 3.4, "X": 3.5, "2": 2.05, "over_2.5": 1.72},
        "Poisson": {"lambda_local": 1.04, "lambda_visitante": 3.55},
    }
    raz.api_key = ""
    base = raz.analizar_partido(copy.deepcopy(payload))

    raz.api_key = "fake-key"
    raz._gemini_desactivado = False
    raz.conciencia.analizar_con_conciencia = lambda _p: json.dumps({
        "decision_final": "APROBADO",
        "mercado_elegido": "1X2",
        "seleccion": "Turkey gana",
        "cuota": 3.4,
        "probabilidad_real_estimada": 0.99,
        "stake_sugerido": "15.0%",
        "justificacion_tecnica": "probabilidad = 0.99 y lambda = 8.0",
    })
    con_llm = raz.analizar_partido(copy.deepcopy(payload))
    for k in ("decision_final", "mercado_elegido", "seleccion", "cuota", "probabilidad_real_estimada", "stake_sugerido", "valor_esperado_ev"):
        assert con_llm.get(k) == base.get(k), k
    assert con_llm.get("llm_solo_explicativo") is True
