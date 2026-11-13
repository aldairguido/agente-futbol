"""Tests Bloque 5: liquidación real (Turkey-Belgium / Italia-Francia), corte prepartido, ledger OOS SHA256 y auditoría sin falso FM+FS ni xG sintético."""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import auditoria_historica as ah  # noqa: E402
import evaluacion_pronosticos as ep  # noqa: E402
import motor_mercados as mm  # noqa: E402


STATS_FLASHSCORE_TB = {
    "FT": {
        "goles": [1, 2],
        "corners": [2, 3],
        "xg": [0.52, 1.85],
        "xgot": [1.41, 2.23],
        "goles_evitados": [0.23, 0.41],
        "remates_totales": [10, 12],
        "remates_puerta": [7, 6],
        "grandes_ocasiones": [2, 3],
        "posesion_pct": [53, 47],
        "faltas": [12, 14],
        "amarillas": [2, 0],
        "fueras_de_juego": [3, 0],
        "toques_area_rival": [23, 33],
        "xa_equipo": [0.63, 1.77],
    },
    "1T": {
        "goles": [1, 2],
        "corners": [1, 0],
        "xg": [0.07, 1.39],
        "xgot": [0.29, 1.90],
        "goles_evitados": [-0.10, -0.71],
        "remates_totales": [2, 5],
        "posesion_pct": [58, 42],
        "faltas": [6, 4],
    },
    "2T": {
        "goles": [0, 0],
        "corners": [1, 3],
        "xg": [0.45, 0.46],
        "xgot": [1.12, 0.33],
        "goles_evitados": [0.33, 1.12],
        "remates_totales": [8, 7],
        "posesion_pct": [48, 52],
        "faltas": [6, 10],
    },
}

STATS_FLASHSCORE_IF = {
    "FT": {
        "goles": [3, 1],
        "corners": [4, 3],
        "xg": [2.35, 1.63],
        "xgot": [1.85, 1.39],
        "goles_evitados": [0.39, -1.15],
        "remates_totales": [11, 18],
        "remates_puerta": [5, 5],
        "grandes_ocasiones": [4, 4],
        "posesion_pct": [35, 65],
        "faltas": [10, 18],
        "amarillas": [2, 2],
    },
    "1T": {
        "goles": [1, 1],
        "corners": [2, 1],
        "xg": [0.31, 1.20],
        "xgot": [0.68, 0.84],
        "goles_evitados": [-0.16, -0.32],
        "remates_totales": [4, 10],
        "faltas": [5, 9],
        "amarillas": [1, 1],
    },
    "2T": {
        "goles": [2, 0],
        "corners": [2, 2],
        "xg": [2.04, 0.43],
        "xgot": [1.17, 0.55],
        "goles_evitados": [0.55, -0.83],
        "remates_totales": [7, 8],
        "grandes_ocasiones": [3, 0],
        "faltas": [5, 9],
        "amarillas": [1, 1],
    },
}


def _cargar_r5() -> dict:
    ruta = os.path.join(ROOT, "tests", "fixtures", "ronda5_real_fixtures.json")
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def _cargar_ln() -> dict:
    ruta = os.path.join(ROOT, "tests", "fixtures", "londrina_nautico_fixtures.json")
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def test_liquidar_corners_equipo_no_confunde_con_total_en_italy_vs_france():
    r5 = _cargar_r5()
    payload = copy.deepcopy(r5["partidos_objetivo"]["italy_france"])
    payload["flashscore_stats"] = STATS_FLASHSCORE_IF
    res = ep.extraer_resultado(payload, fuente="fotmob")

    assert res.corners_local["FT"] == 4
    assert res.corners_visitante["FT"] == 3

    ev_fr_over = ep.liquidar(
        {
            "mercado": "Visitante - Total de tiros de esquina",
            "evento": "visitante_corners_over_3.5",
            "etiqueta": "France: más de 3.5 córneres",
            "periodo": "FT",
            "equipo": "France",
            "linea": 3.5,
            "probabilidad": 0.7903,
        },
        res,
        "TEST-CORN",
    )
    assert ev_fr_over.estado_liquidacion == "perdida"
    assert ev_fr_over.evento_ocurrio == 0
    assert "3 córneres" in ev_fr_over.detalle

    ev_it_u55 = ep.liquidar(
        {
            "mercado": "Local - Tiros de esquina",
            "evento": "local_corners_under_5.5",
            "etiqueta": "Italy: menos de 5.5 córneres",
            "periodo": "FT",
            "equipo": "Italy",
            "linea": 5.5,
            "probabilidad": 0.7435,
        },
        res,
        "TEST-CORN",
    )
    assert ev_it_u55.estado_liquidacion == "ganada"
    assert ev_it_u55.evento_ocurrio == 1
    assert "4 córneres" in ev_it_u55.detalle


def test_liquidar_primer_gol_sin_cronologia_no_marca_derrota_falsa():
    r5 = _cargar_r5()
    payload = copy.deepcopy(r5["partidos_objetivo"]["turkey_belgium"])
    payload["flashscore_stats"] = STATS_FLASHSCORE_TB
    res = ep.extraer_resultado(payload, fuente="fotmob")

    # Marcador 1-2 sin eventos minuto a minuto -> no_verificable, nunca perdida
    assert res.goles == []
    ev = ep.liquidar(
        {
            "mercado": "Equipo que anota el gol 1",
            "evento": "primer_gol_visitante",
            "etiqueta": "Anota primero Belgium",
            "periodo": "FT",
            "equipo": "Belgium",
            "probabilidad": 0.7656,
        },
        res,
        "TEST-PG",
    )
    assert ev.estado_liquidacion == "no_verificable"
    assert ev.evento_ocurrio is None

    # En partido 0-0 sin goles sí se liquida como perdida
    res_cero = copy.deepcopy(res)
    res_cero.goles_local = 0
    res_cero.goles_visitante = 0
    ev_cero = ep.liquidar(
        {
            "mercado": "Equipo que anota el gol 1",
            "evento": "primer_gol_visitante",
            "etiqueta": "Anota primero Belgium",
            "periodo": "FT",
            "equipo": "Belgium",
            "probabilidad": 0.7656,
        },
        res_cero,
        "TEST-PG0",
    )
    assert ev_cero.estado_liquidacion == "perdida"


def test_extraer_resultado_scraper_vacio_reporta_discrepancia_y_reproduce_flashscore():
    r5 = _cargar_r5()

    # Solo payload vacío de FotMob (sin Flashscore)
    res_solo_fm = ep.extraer_resultado(r5["partidos_objetivo"]["turkey_belgium"], fuente="fotmob")
    assert res_solo_fm.finalizado is True
    assert res_solo_fm.local == "Turkiye"
    assert res_solo_fm.visitante == "Belgium"
    assert (res_solo_fm.goles_local, res_solo_fm.goles_visitante) == (1, 2)
    assert res_solo_fm.goles_local_1t is None
    assert res_solo_fm.corners_local == {}

    # Con Flashscore real encima del mismo payload del scraper
    payload_tb = copy.deepcopy(r5["partidos_objetivo"]["turkey_belgium"])
    payload_tb["flashscore_stats"] = STATS_FLASHSCORE_TB
    res_tb = ep.extraer_resultado(payload_tb, fuente="fotmob")
    assert (res_tb.goles_local, res_tb.goles_visitante) == (1, 2)
    assert (res_tb.goles_local_1t, res_tb.goles_visitante_1t) == (1, 2)
    assert res_tb.corners_local == {"FT": 2, "1T": 1, "2T": 1}
    assert res_tb.corners_visitante == {"FT": 3, "1T": 0, "2T": 3}
    assert res_tb.amarillas_local["FT"] == 2
    assert res_tb.amarillas_visitante["FT"] == 0
    assert res_tb.discrepancias, "Debe reportar discrepancia FotMob vacío vs Flashscore"
    assert "matriz vacía" in res_tb.discrepancias[0]

    payload_if = copy.deepcopy(r5["partidos_objetivo"]["italy_france"])
    payload_if["flashscore_stats"] = STATS_FLASHSCORE_IF
    res_if = ep.extraer_resultado(payload_if, fuente="fotmob")
    assert (res_if.goles_local, res_if.goles_visitante) == (3, 1)
    assert (res_if.goles_local_1t, res_if.goles_visitante_1t) == (1, 1)
    assert res_if.corners_local == {"FT": 4, "1T": 2, "2T": 2}
    assert res_if.corners_visitante == {"FT": 3, "1T": 1, "2T": 2}
    assert res_if.amarillas_local == {"FT": 2, "1T": 1, "2T": 1}
    assert res_if.amarillas_visitante == {"FT": 2, "1T": 1, "2T": 1}
    assert res_if.discrepancias


def test_liquidar_todas_lineas_reales_y_evaluar_tanda_sin_reglas_faltantes(tmp_path):
    r5 = _cargar_r5()

    an_tb = mm.analizar(
        r5["docs"]["turkey"], r5["docs"]["belgium"], "Turkiye", "Belgium",
        fecha_partido="2026-11-12", competicion="UEFA Nations League",
    )
    an_if = mm.analizar(
        r5["docs"]["italy"], r5["docs"]["france"], "Italy", "France",
        fecha_partido="2026-11-12", competicion="UEFA Nations League",
    )
    assert round(an_tb.motor.traza_lambda[-1]["lam_l_despues"], 2) == 1.04
    assert round(an_tb.motor.traza_lambda[-1]["lam_v_despues"], 2) == 3.55
    assert round(an_if.motor.traza_lambda[-1]["lam_l_despues"], 2) == 1.03
    assert round(an_if.motor.traza_lambda[-1]["lam_v_despues"], 2) == 2.33

    payload_tb = copy.deepcopy(r5["partidos_objetivo"]["turkey_belgium"])
    payload_tb["flashscore_stats"] = dict(STATS_FLASHSCORE_TB, inicio_utc="2026-11-12T17:00:00Z")
    res_tb = ep.extraer_resultado(payload_tb, fuente="fotmob")

    payload_if = copy.deepcopy(r5["partidos_objetivo"]["italy_france"])
    payload_if["flashscore_stats"] = dict(STATS_FLASHSCORE_IF, inicio_utc="2026-11-12T19:45:00Z")
    res_if = ep.extraer_resultado(payload_if, fuente="fotmob")

    def _pack(an_obj):
        sels = [
            {
                "mercado": s.mercado,
                "evento": s.evento,
                "etiqueta": s.etiqueta,
                "periodo": s.periodo,
                "equipo": s.equipo,
                "linea": s.linea,
                "probabilidad": s.probabilidad,
                "estado_sensibilidad": s.estado,
                "publicada": True,
            }
            for s in an_obj.selecciones_filtradas
        ]
        return {"selecciones_publicadas": sels}

    doc_tb = _pack(an_tb)
    doc_if = _pack(an_if)

    liq_tb = ep.liquidar_todas_lineas_fixture(doc_tb, res_tb, "TB-R5")
    liq_if = ep.liquidar_todas_lineas_fixture(doc_if, res_if, "IF-R5")

    assert liq_tb["resumen"]["regla_no_definida"] == 0
    assert liq_tb["resumen"]["n_publicadas"] == 59
    assert liq_tb["resumen"]["n_evaluables"] == 58
    assert liq_tb["resumen"]["aciertos"] == 52
    assert liq_tb["resumen"]["fallos"] == 6
    assert liq_tb["resumen"]["no_verificables"] == 1

    assert liq_if["resumen"]["regla_no_definida"] == 0
    assert liq_if["resumen"]["n_publicadas"] == 44
    assert liq_if["resumen"]["n_evaluables"] == 44
    assert liq_if["resumen"]["aciertos"] == 37
    assert liq_if["resumen"]["fallos"] == 7

    # Verificar que evaluar_tanda propaga discrepancias a limitaciones y liquida los snapshots
    snaps = [
        {
            "id_trabajo": "TB-R5",
            "equipos": {"local": "Turquía", "visitante": "Bélgica"},
            "partido": {"fecha": "2026-11-12", "competicion": "UEFA Nations League"},
            "generado_en": "2026-11-12 08:51:25",
            "publicado_en": "2026-11-12 08:51:25",
            "modelo": {"nombre": "red_recursiva", "version": "r5"},
            "datos_corte": {},
            "umbral_publicado": 0.70,
            "selecciones": doc_tb["selecciones_publicadas"],
            "selecciones_publicadas": doc_tb["selecciones_publicadas"],
            "referencia": "test_tb",
        },
        {
            "id_trabajo": "IF-R5",
            "equipos": {"local": "Italia", "visitante": "Francia"},
            "partido": {"fecha": "2026-11-12", "competicion": "UEFA Nations League"},
            "generado_en": "2026-11-12 08:55:00",
            "publicado_en": "2026-11-12 08:55:00",
            "modelo": {"nombre": "red_recursiva", "version": "r5"},
            "datos_corte": {},
            "umbral_publicado": 0.70,
            "selecciones": doc_if["selecciones_publicadas"],
            "selecciones_publicadas": doc_if["selecciones_publicadas"],
            "referencia": "test_if",
        },
    ]
    resultados = {
        "Turquía vs Bélgica": res_tb,
        "Italia vs Francia": res_if,
    }
    informe = ep.evaluar_tanda(snaps, resultados)
    assert len(informe.evaluaciones) == 59 + 44
    assert any("matriz vacía" in lim for lim in informe.limitaciones)
    assert sum(1 for e in informe.evaluaciones if e.estado_liquidacion == "ganada") == 89
    assert sum(1 for e in informe.evaluaciones if e.estado_liquidacion == "perdida") == 13
    assert sum(1 for e in informe.evaluaciones if e.estado_liquidacion == "no_verificable") == 1
    texto_tg = ep.resumen_telegram(informe)
    assert "Evaluables (binarias comparables): 101" in texto_tg
    assert "Eventos cumplidos: 88; no cumplidos: 13" in texto_tg


def test_corte_prepartido_bloquea_fuga_y_post_kickoff():
    r5 = _cargar_r5()
    hist_tb = r5["docs"]["turkey"]["partidos"] + r5["docs"]["belgium"]["partidos"]
    ok_res = ep.validar_corte_prepartido_sin_fuga(
        generado_en="2026-11-12 08:51:25",
        inicio_utc="2026-11-12T17:00:00Z",
        partidos_historicos=hist_tb,
        fecha_objetivo="2026-11-12",
        id_partido_objetivo="5181796",
    )
    assert ok_res["ok"] is True
    assert ok_res["pre_kickoff_verificado"] is True
    assert ok_res["fuga_historica"] is False

    post_res = ep.validar_corte_prepartido_sin_fuga(
        generado_en="2026-11-12T17:05:00Z",
        inicio_utc="2026-11-12T17:00:00Z",
        partidos_historicos=hist_tb,
        fecha_objetivo="2026-11-12",
    )
    assert post_res["ok"] is False
    assert post_res["pre_kickoff_verificado"] is False

    fuga_res = ep.validar_corte_prepartido_sin_fuga(
        generado_en="2026-11-12 08:51:25",
        inicio_utc="2026-11-12T17:00:00Z",
        partidos_historicos=hist_tb + [{"id_partido": "5181796", "fecha": "2026-11-12"}],
        fecha_objetivo="2026-11-12",
        id_partido_objetivo="5181796",
    )
    assert fuga_res["ok"] is False
    assert fuga_res["fuga_historica"] is True


def test_ledger_oos_sha256_append_only_idempotente_y_detecta_tampering(tmp_path):
    ruta = str(tmp_path / "ledger_oos.jsonl")
    r0 = ep.registrar_en_ledger_oos(
        {
            "id_partido": "5181796",
            "partido": "Turkiye vs Belgium",
            "fecha": "2026-11-12",
            "marcador_real": {"ft": [1, 2]},
            "resumen": {"aciertos": 52, "fallos": 6},
        },
        ruta_ledger=ruta,
    )
    assert r0["registrado"] is True
    assert r0["indice"] == 0
    assert r0["prev_sha256"] == "0" * 64

    # Idempotente por (id_partido, fecha)
    dup = ep.registrar_en_ledger_oos(
        {"id_partido": "5181796", "partido": "Turkiye vs Belgium", "fecha": "2026-11-12"},
        ruta_ledger=ruta,
    )
    assert dup["registrado"] is False
    assert dup["ya_existia"] is True
    assert dup["sha256"] == r0["sha256"]

    r1 = ep.registrar_en_ledger_oos(
        {
            "id_partido": "5181797",
            "partido": "Italy vs France",
            "fecha": "2026-11-12",
            "marcador_real": {"ft": [3, 1]},
            "resumen": {"aciertos": 37, "fallos": 7},
        },
        ruta_ledger=ruta,
    )
    assert r1["registrado"] is True
    assert r1["indice"] == 1
    assert r1["prev_sha256"] == r0["sha256"]

    ver = ep.verificar_cadena_ledger_oos(ruta)
    assert ver["ok"] is True
    assert ver["entradas"] == 2

    # Alterar el contenido de la primera entrada debe invalidar la cadena y bloquear nuevas escrituras
    with open(ruta, encoding="utf-8") as f:
        lineas = f.readlines()
    obj0 = json.loads(lineas[0])
    obj0["marcador_real"]["ft"] = [9, 9]
    lineas[0] = json.dumps(obj0, ensure_ascii=False, sort_keys=True) + "\n"
    with open(ruta, "w", encoding="utf-8") as f:
        f.writelines(lineas)

    ver_bad = ep.verificar_cadena_ledger_oos(ruta)
    assert ver_bad["ok"] is False
    assert ver_bad["errores"]
    with pytest.raises(ValueError, match="Integridad comprometida"):
        ep.registrar_en_ledger_oos(
            {"id_partido": "999", "partido": "X vs Y", "fecha": "2026-11-13"},
            ruta_ledger=ruta,
        )


def test_auditoria_historica_cero_falso_fmfs_y_cero_xg_sintetico(tmp_path):
    r5 = _cargar_r5()
    ln = _cargar_ln()

    # Caso sintético con id_flashscore N/D + tag espurio 'fs' + xG estimado + xG None
    p_sint = copy.deepcopy(r5["docs"]["turkey"]["partidos"][0])
    p_sint["fuente_secundaria"] = {"pareado": False, "id_flashscore": "N/D"}
    p_sint.setdefault("procedencia", {}).setdefault("FT", {}).setdefault("equipo", {})["xg"] = "estimacion_modelo"
    p_sint["procedencia"]["FT"]["equipo"]["posesion_pct"] = "fotmob+fs"
    for fila in (p_sint.get("periodos") or {}).get("FT", {}).get("filas", []):
        if fila.get("clave") == "xg":
            fila["equipo"] = 1.44
            fila["rival"] = None
            fila["estimado"] = True

    resumen = ah._construir_tabla_resumen_inicial([p_sint])
    fila_res = [l for l in resumen if l.startswith("| 01 |")][0]
    assert "| N/D | FotMob" in fila_res
    assert "FM+FS" not in fila_res
    assert "| sin_dato vs sin_dato |" in fila_res
    assert "0.00" not in fila_res

    bloque_md = ah.formatear_matriz_45_partido(p_sint, k=1, total_partidos=1)
    assert "| FM+FS |" not in bloque_md
    # xG sintético no se imprime numéricamente
    for lin in bloque_md.splitlines():
        if "| `xg` |" in lin:
            assert "1.44" not in lin
            assert "| sin_dato | sin_dato |" in lin

    # Generación completa de informes en tmp_path con fixtures reales TB, IF, LN
    for nombre, doc_a, doc_b in (
        ("tb", r5["docs"]["turkey"], r5["docs"]["belgium"]),
        ("if", r5["docs"]["italy"], r5["docs"]["france"]),
        ("ln", ln["londrina"], ln["nautico"]),
    ):
        ruta = str(tmp_path / f"auditoria_{nombre}.md")
        ah.generar_informe_matrices_30(doc_a, doc_b, ruta_md=ruta)
        for fn in os.listdir(tmp_path):
            if fn.startswith(f"auditoria_{nombre}") and fn.endswith(".md"):
                txt = open(tmp_path / fn, encoding="utf-8").read()
                for lin in txt.splitlines():
                    assert "| N/D | FM+FS |" not in lin
                for blk in txt.split("IDs Verificables -> ")[1:]:
                    if "Flashscore: N/D" in blk.splitlines()[0]:
                        cuerpo = blk.split("=" * 84)[0] if ("=" * 84) in blk else blk
                        for rl in cuerpo.splitlines():
                            assert not rl.endswith("| FM+FS |"), f"Falso FM+FS en {fn}: {rl}"
