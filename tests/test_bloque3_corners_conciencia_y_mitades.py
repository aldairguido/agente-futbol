# -*- coding: utf-8 -*-
"""
Tests reales del Bloque 3 (Ronda 5+6):
  3.1d Sobredispersión empírica (var/media > 1.05) activa Binomial Negativa en córneres.
  3.2  Regla operativa min_partidos_corners_por_equipo = 8/15: bloquea Top 5, Validadas
       y emitir probabilidades altas cuando algún equipo tiene < 8/15 con dato real.
  3.3  /conciencia separa 'Córneres partido' vs 'Córneres por equipo', bootstrap por
       conglomerados de partido, veta familias con brecha <= -10 pp del Top 5 y muestra
       la probabilidad calibrada junto a la bruta bajo vigilancia.
  3.5  Etiqueta 'reparto 50/50 sin validar' en mercados de mitades sin respaldo cronológico.
  3.7  Gatekeeper min_partidos_cronologia_verificada = 10/15 con min(n_ev_l, n_ev_v)
       bloquea '2T +0.5' y demás mercados dependientes de mitades en Italia-Francia.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agente_consciente import AgenteConsciente, evaluar_familia_conciencia_cached
from cuadro_especialista import _organizar_sec1_para_cuadro
from decision_cobertura import cargar_umbrales_cobertura
from motor_mercados import (
    Parametros,
    Seleccion,
    _dispersion_corners_muestra,
    _linea_seleccion,
    _reparto_mitades_sin_validar,
    analizar,
    cobertura_corners_por_equipo,
    cronologia_mitades_verificada,
    es_seleccion_competitiva_ticket,
    filtrar_top_opciones_ticket,
    seleccionar_top_5_jugadas_recomendadas,
)


FIXTURES_R5 = json.loads((ROOT / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))


def _strip_corners_from_first_k(doc: dict, keep_n: int) -> dict:
    copia = copy.deepcopy(doc)
    kept = 0
    for p in copia.get("partidos") or []:
        filas = ((p.get("periodos") or {}).get("FT") or {}).get("filas") or []
        tiene_c = any(str(f.get("clave") or "").lower() in ("corners", "corners_favor") and f.get("equipo") is not None for f in filas)
        if not tiene_c:
            continue
        if kept < keep_n:
            kept += 1
        else:
            for f in filas:
                if str(f.get("clave") or "").lower() in ("corners", "corners_favor", "corners_contra"):
                    f["equipo"] = None
                    f["rival"] = None
    return copia


def test_3_1d_sobredispersion_corners_activa_binomial_negativa():
    dl = copy.deepcopy(FIXTURES_R5["docs"]["turkey"])
    dv = copy.deepcopy(FIXTURES_R5["docs"]["belgium"])

    disp_l = _dispersion_corners_muestra(dl, dv, bando="local")
    disp_v = _dispersion_corners_muestra(dl, dv, bando="visitante")
    assert disp_l["var_sobre_media"] > 1.05 and disp_l["sobredisperso"] is True
    assert disp_v["var_sobre_media"] > 1.05 and disp_v["sobredisperso"] is True

    a = analizar(dl, dv, "Turkey", "Belgium")
    assert (a.motor.tasas.lambda_local_ft, a.motor.tasas.lambda_visitante_ft) == (1.04, 3.55)

    mkts = {m.id_mercado: m for m in a.mercados}
    assert mkts["corners_local"].detalle.get("distribucion_activa") == "binomial_negativa"
    assert mkts["corners_visitante"].detalle.get("distribucion_activa") == "binomial_negativa"
    assert mkts["corners_local"].detalle.get("k_nb_efectiva") is not None
    assert mkts["corners_visitante"].detalle.get("k_nb_efectiva") is not None

    # Escenario de totales con sobredispersión empírica explícita (> 1.05): ambos docs con alta varianza
    dl_ov = copy.deepcopy(dv)
    for idx, p in enumerate(dl_ov["partidos"]):
        for f in ((p.get("periodos") or {}).get("FT") or {}).get("filas") or []:
            if str(f.get("clave") or "").lower() == "corners":
                f["equipo"] = 1 if idx % 2 == 0 else 13
                f["rival"] = 2 if idx % 2 == 0 else 8
    a_ov = analizar(dl_ov, dv, "Belgium", "Turkey")
    mkts_ov = {m.id_mercado: m for m in a_ov.mercados}
    det_tot = mkts_ov["total_corners"].detalle
    assert float(det_tot.get("var_sobre_media") or 0.0) > 1.05
    assert det_tot.get("distribucion_activa") == "binomial_negativa"
    assert det_tot.get("k_nb_efectiva") is not None


def test_3_2_corners_menor_8_de_15_bloqueado_en_catalogo_top5_y_validadas():
    umb = cargar_umbrales_cobertura()
    assert int(umb["min_partidos_corners_por_equipo"]) == 8

    dl_6 = _strip_corners_from_first_k(FIXTURES_R5["docs"]["turkey"], keep_n=6)
    dv_15 = copy.deepcopy(FIXTURES_R5["docs"]["belgium"])

    a = analizar(dl_6, dv_15, "Turkey", "Belgium")
    cob = cobertura_corners_por_equipo(a.motor)
    assert cob["n_local"] == 6 and cob["n_visitante"] == 15
    assert cob["ok_top"] is False and cob["ok_catalogo"] is False

    mkts = {m.id_mercado: m for m in a.mercados}
    assert mkts["total_corners"].estado == "datos_insuficientes"
    assert "6/15" in mkts["total_corners"].nota
    assert mkts["corners_local"].estado == "datos_insuficientes"
    assert "6/15" in mkts["corners_local"].nota

    # Nunca entra probabilidad alta ni Top 5 ni Validadas
    assert not any("corner" in s.id_mercado or "córner" in s.mercado.lower() for s in a.selecciones_filtradas)
    top5 = seleccionar_top_5_jugadas_recomendadas(a.selecciones_filtradas, motor=a.motor, eq_l="Turkey", eq_v="Belgium")
    assert not any("córn" in str(t.get("etiqueta") or "").lower() or "corner" in str(t.get("categoria") or "").lower() for t in top5)

    sel_sint = Seleccion(
        id_mercado="total_corners", mercado="Total de córneres", evento="corners_over_7.5",
        etiqueta="más de 7.5 córneres", periodo="FT", equipo=None, linea=7.5,
        probabilidad=0.86, estimable=True, estado="mantiene",
    )
    assert es_seleccion_competitiva_ticket(sel_sint, "Turkey", "Belgium", (0.20, 0.20, 0.60), motor=a.motor) is False
    sec1 = _organizar_sec1_para_cuadro(a, [sel_sint], "Turkey", "Belgium", a.motor.matriz_ft, top5)
    sec_corn = [c for c in sec1 if c["categoria"] == "CÓRNERES"][0]
    assert sec_corn["pildoras"][0]["lbl"].startswith("Sin líneas")


def test_3_3_conciencia_familias_bootstrap_veto_top5_y_prob_calibrada():
    umb = cargar_umbrales_cobertura()
    assert float(umb["max_brecha_familia_top5_pp"]) == -10.0

    ag = AgenteConsciente()

    ev = ag.evaluacion_real
    por_fam = ev["por_familia"]
    assert "Córneres partido" in por_fam
    assert "Córneres por equipo" in por_fam
    assert por_fam["Córneres por equipo"]["veto_top5"] is True
    assert por_fam["Córneres por equipo"]["brecha_pp"] <= -10.0
    assert por_fam["Córneres partido"]["veto_top5"] is False

    # Bootstrap por conglomerados de partido presente y < total líneas
    assert ev["partidos_unicos"] >= 20
    assert ev["wr_ic95_lo"] is not None and ev["wr_ic95_hi"] is not None
    assert por_fam["Córneres por equipo"]["brecha_ic95_lo"] is not None
    assert por_fam["Córneres por equipo"]["partidos_unicos"] == ev["partidos_unicos"] or por_fam["Córneres por equipo"]["partidos_unicos"] >= 15

    # No duplicar familia bajo vigilancia en mercados_estrella
    est_fams = {f["familia"] for f in ev["mercados_estrella"]}
    vig_fams = {f["familia"] for f in ev["mercados_vigilancia"]}
    assert est_fams.isdisjoint(vig_fams)

    resumen = ag.obtener_resumen_conciencia()
    assert "Córneres partido" in resumen
    assert "Córneres por equipo" in resumen
    assert "VETADO Top 5" in resumen
    assert "IC95%" in resumen

    # Verificar veto en Top 5 + publicación con probabilidad calibrada junto a la bruta bajo vigilancia
    dl = copy.deepcopy(FIXTURES_R5["docs"]["turkey"])
    dv = copy.deepcopy(FIXTURES_R5["docs"]["belgium"])
    a = analizar(dl, dv, "Turkey", "Belgium")
    top5 = seleccionar_top_5_jugadas_recomendadas(a.selecciones_filtradas, motor=a.motor, eq_l="Turkey", eq_v="Belgium")
    for item in top5:
        info = evaluar_familia_conciencia_cached(item.get("etiqueta", ""), prob_bruta=item["probabilidad"])
        assert item.get("familia_conciencia") != "Córneres por equipo"
        assert info is not None
        if item.get("bajo_vigilancia"):
            assert item.get("prob_calibrada") is not None
            assert "Cal " in item.get("badge_completo", "")

    # Córneres por equipo conservado fuera del Top 5 pero si se renderiza muestra calibrada
    sel_corn_eq = Seleccion(
        id_mercado="corners_visitante", mercado="Córneres por equipo - Visitante",
        evento="visitante_corners_over_3.5", etiqueta="Belgium: más de 3.5 córneres",
        periodo="FT", equipo="Belgium", linea=3.5, probabilidad=0.88, estimable=True, estado="mantiene",
    )
    info_ce = evaluar_familia_conciencia_cached(sel_corn_eq.mercado, equipo=sel_corn_eq.equipo, prob_bruta=0.88)
    assert info_ce["veto_top5"] is True and info_ce["prob_calibrada"] < 0.88
    setattr(sel_corn_eq, "prob_calibrada", info_ce["prob_calibrada"])
    setattr(sel_corn_eq, "bajo_vigilancia", True)
    txt_ln = _linea_seleccion(sel_corn_eq, 0.70)
    assert "calibrada:" in txt_ln

    sec1 = _organizar_sec1_para_cuadro(a, [sel_corn_eq], "Turkey", "Belgium", a.motor.matriz_ft, top5)
    sec_corn = [c for c in sec1 if c["categoria"] == "CÓRNERES"][0]
    assert any("cal " in p["pct"] for p in sec_corn["pildoras"])


def test_3_5_etiqueta_reparto_50_50_sin_validar_y_3_7_gatekeeper_italia_francia():
    dl = copy.deepcopy(FIXTURES_R5["docs"]["italy"])
    dv = copy.deepcopy(FIXTURES_R5["docs"]["france"])

    a = analizar(dl, dv, "Italy", "France")
    # Lambda intacta
    assert (a.motor.tasas.lambda_local_ft, a.motor.tasas.lambda_visitante_ft) == (1.03, 2.33)
    assert _reparto_mitades_sin_validar(a.motor) is True

    cron = cronologia_mitades_verificada(a.motor)
    assert cron["ok"] is False and cron["k_eval"] == 0 and cron["k_min"] == 10

    # Bloqueado en Top 5, Sección 1 (Validadas), Gatekeeper y selecciones_filtradas
    top5 = seleccionar_top_5_jugadas_recomendadas(a.selecciones_filtradas, motor=a.motor, eq_l="Italy", eq_v="France")
    for item in top5:
        assert "(2T)" not in item["etiqueta"] and "(1T)" not in item["etiqueta"]
        assert "2do tiempo" not in item["etiqueta"].lower() and "1er tiempo" not in item["etiqueta"].lower()

    assert not any(
        (s.periodo in ("1T", "2T") or s.id_mercado in ("ganador_1t", "_1t_ft", "gol_ambos_tiempos"))
        for s in a.selecciones_filtradas
    )

    sel_2t_85 = Seleccion(
        id_mercado="total_goles", mercado="Total de goles", evento="over_0.5",
        etiqueta="más de 0.5 goles", periodo="2T", equipo=None, linea=0.5,
        probabilidad=0.85, estimable=True, estado="mantiene",
    )
    sel_1t_gan = Seleccion(
        id_mercado="ganador_1t", mercado="Ganador del 1er tiempo", evento="1t_2",
        etiqueta="1T: France gana", periodo="1T", equipo="France", linea=None,
        probabilidad=0.75, estimable=True, estado="mantiene",
    )
    sel_1t_ft = Seleccion(
        id_mercado="_1t_ft", mercado="Descanso / Final", evento="1t2_ft2",
        etiqueta="1T 2 / FT 2 - 2", periodo="FT", equipo=None, linea=None,
        probabilidad=0.72, estimable=True, estado="mantiene",
    )
    sel_amb = Seleccion(
        id_mercado="gol_ambos_tiempos", mercado="Gol en ambos tiempos", evento="gol_ambos_tiempos",
        etiqueta="Gol en ambos tiempos (partido)", periodo="FT", equipo=None, linea=None,
        probabilidad=0.78, estimable=True, estado="mantiene",
    )
    for cand in (sel_2t_85, sel_1t_gan, sel_1t_ft, sel_amb):
        assert es_seleccion_competitiva_ticket(cand, "Italy", "France", (0.20, 0.25, 0.55), motor=a.motor) is False

    filt = filtrar_top_opciones_ticket([sel_2t_85, sel_1t_gan, sel_1t_ft, sel_amb], "Italy", "France", motor=a.motor)
    assert filt == []

    sec1 = _organizar_sec1_para_cuadro(a, [sel_2t_85], "Italy", "France", a.motor.matriz_ft, top5)
    for cat in sec1:
        for pill in cat["pildoras"]:
            assert "(2T)" not in pill["lbl"] and "(1T)" not in pill["lbl"]

    # Bug min/max: si un equipo tiene 12/15 eventos y el rival 0/15, k_eval debe ser 0 (no 12) y bloquear
    dl_asym = copy.deepcopy(dl)
    dl_asym["cuadro_c_timeline"] = {"_meta": {"n_partidos_con_eventos": 12}}
    dv_asym = copy.deepcopy(dv)
    dv_asym["cuadro_c_timeline"] = {"_meta": {"n_partidos_con_eventos": 0}}
    a_asym = analizar(dl_asym, dv_asym, "Italy", "France")
    cron_asym = cronologia_mitades_verificada(a_asym.motor)
    assert cron_asym["k_eval"] == 0 and cron_asym["ok"] is False

    # Formato explícito de etiqueta 50/50 sin validar
    setattr(sel_2t_85, "motivo", "reparto 50/50 sin validar")
    assert "reparto 50/50 sin validar" in _linea_seleccion(sel_2t_85, 0.70)
