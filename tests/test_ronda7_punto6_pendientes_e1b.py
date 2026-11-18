# -*- coding: utf-8 -*-
"""
Ronda 7 - Punto 6: Auditoria integral de los pendientes de Entrega 1b (a)..(i).

Todo cambio que altere lambda, probabilidades, Top 5 o reglas de stake queda
detras de banderas explicitamente apagadas por defecto:
  - usar_guarda_doble_defensa_rota (b)
  - usar_gate_goles_mitad_15_15 (c)
  - usar_kelly_conservador_min_3pct (e)
  - usar_excluir_primer_gol_top5 (f)
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import sys
import unicodedata

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from decision_cobertura import banderas_comportamiento, bandera_activa
import evaluacion_pronosticos as ep
import motor_mercados as mm
import motor_patrones as mp
import servicio_mercados as sm


FIXTURES_R5 = json.loads((ROOT / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))


def _norm_s(s: object) -> str:
    return " ".join(
        unicodedata.normalize("NFKD", str(s or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .split()
    )


def _poisson_pmf(lam: float, k: int) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _prob_poisson_simple_row(row: dict, lam_l: float, lam_v: float, eq_l: str, eq_v: str) -> float | None:
    m_s = _norm_s(row.get("mercado"))
    ev = _norm_s(row.get("evento"))
    if any(w in m_s or w in ev for w in ("corner", "tarjeta", "amarilla", "roja")):
        return None
    eq_r = _norm_s(row.get("equipo"))
    per = str(row.get("periodo") or "FT").strip().upper()
    if per != "FT":
        return None
    lin = row.get("linea")
    eq_ln = _norm_s(eq_l)
    max_g = 15
    if ev in ("1", "local_gana"):
        return sum(_poisson_pmf(lam_l, i) * _poisson_pmf(lam_v, j) for i in range(max_g) for j in range(max_g) if i > j)
    if ev in ("x", "empate"):
        return sum(_poisson_pmf(lam_l, i) * _poisson_pmf(lam_v, j) for i in range(max_g) for j in range(max_g) if i == j)
    if ev in ("2", "visitante_gana"):
        return sum(_poisson_pmf(lam_l, i) * _poisson_pmf(lam_v, j) for i in range(max_g) for j in range(max_g) if i < j)
    if ev in ("1x", "dc_1x"):
        return sum(_poisson_pmf(lam_l, i) * _poisson_pmf(lam_v, j) for i in range(max_g) for j in range(max_g) if i >= j)
    if ev in ("x2", "dc_x2"):
        return sum(_poisson_pmf(lam_l, i) * _poisson_pmf(lam_v, j) for i in range(max_g) for j in range(max_g) if i <= j)
    if ev in ("12", "dc_12"):
        return sum(_poisson_pmf(lam_l, i) * _poisson_pmf(lam_v, j) for i in range(max_g) for j in range(max_g) if i != j)
    if "btts_si" in ev or ev == "ambos_marcan_si":
        return (1.0 - math.exp(-lam_l)) * (1.0 - math.exp(-lam_v))
    if "btts_no" in ev or ev == "ambos_marcan_no":
        return 1.0 - (1.0 - math.exp(-lam_l)) * (1.0 - math.exp(-lam_v))
    ev_tail = ev.removeprefix("local_").removeprefix("visitante_")
    es_eq = bool((eq_r and eq_r not in ("total", "none")) or ev.startswith("local_") or ev.startswith("visitante_"))
    lam_obj = (
        (lam_l if (ev.startswith("local_") or eq_r == eq_ln or eq_ln in eq_r or eq_r in eq_ln) else lam_v)
        if es_eq
        else (lam_l + lam_v)
    )
    if ev_tail.startswith("al_menos_") and lin is not None:
        k_max = int(math.floor(float(lin) - 0.5))
        p_le = sum(_poisson_pmf(lam_obj, k) for k in range(max(0, k_max) + 1))
        return 1.0 - p_le
    if (ev_tail.startswith("over_") or ev_tail.startswith("under_")) and lin is not None:
        k_max = int(math.floor(float(lin)))
        p_le = sum(_poisson_pmf(lam_obj, k) for k in range(k_max + 1))
        return p_le if ev_tail.startswith("under_") else (1.0 - p_le)
    return None


def test_punto6a_causa_regresion_p13_5e8425f_vs_994209a():
    """
    (a) Cuando goles_evitados explicito difiere de xgot_enfrentado - goles_contra,
    la unificacion P13 (994209a) usa el goles_evitados explicito en lugar de
    xgot_enfrentado - goles_contra (como ocurrio en 6/85 partidos archivados).
    """
    stats = {
        "goles_contra": 1.10,
        "xgot_enfrentado": 1.45,
        "goles_evitados": 0.12,
        "xg": 1.50,
        "goles": 1.40,
        "posesion_pct": 54.0,
        "remates_totales": 13.0,
        "remates_puerta": 5.0,
    }
    p55 = mp.calcular_55_patrones_equipo(stats)
    assert p55["P13_Portero_Goles_Evitados"] == 0.12
    assert p55["P13_Portero_Goles_Evitados"] != round(1.45 - 1.10, 2)

    stats_sin_ge = dict(stats)
    stats_sin_ge.pop("goles_evitados")
    p55_deriv = mp.calcular_55_patrones_equipo(stats_sin_ge)
    assert p55_deriv["P13_Portero_Goles_Evitados"] == round(1.45 - 1.10, 2)


def test_punto6b_guarda_doble_defensa_rota_135_en_turkey_belgium():
    """
    (b) Verifica que en Turkey vs Belgium:
      - Con usar_guarda_doble_defensa_rota=False (defecto/soloA), x1.35 aplica en DCN y otra vez en post_sinc -> V=3.55.
      - Con usar_guarda_doble_defensa_rota=True, no re-multiplica en post_sinc -> V=2.63 (soloA) o V=2.21 (AB).
    """
    dl = FIXTURES_R5["docs"]["turkey"]
    dv = FIXTURES_R5["docs"]["belgium"]

    with banderas_comportamiento(activar_todas=False):
        assert bandera_activa("usar_guarda_doble_defensa_rota") is False
        an_off = mm.analizar(dl, dv, "Turkey", "Belgium", condicion_local="casa", condicion_visitante="fuera")
        pasos_dr_off = [s for s in an_off.motor.traza_lambda if s.get("regla") == "copa_asedio_defensa_rota"]
        assert len(pasos_dr_off) == 2
        assert round(an_off.motor.tasas.lambda_visitante_ft, 2) == 3.55

    with banderas_comportamiento(activar_todas=False, usar_guarda_doble_defensa_rota=True):
        an_on = mm.analizar(dl, dv, "Turkey", "Belgium", condicion_local="casa", condicion_visitante="fuera")
        pasos_post_on = [
            s for s in an_on.motor.traza_lambda
            if s.get("etapa") == "post_sinc_mercados" and s.get("regla") == "copa_asedio_defensa_rota"
        ]
        assert pasos_post_on == []
        assert round(an_on.motor.tasas.lambda_visitante_ft, 2) == 2.63

    with banderas_comportamiento(activar_todas=True, usar_guarda_doble_defensa_rota=True):
        an_ab_guard = mm.analizar(dl, dv, "Turkey", "Belgium", condicion_local="casa", condicion_visitante="fuera")
        assert round(an_ab_guard.motor.tasas.lambda_visitante_ft, 2) == 2.21


def test_punto6c_gate_mitades_goles_por_mitad_15_15():
    """
    (c) Verifica que cronologia_mitades_verificada reporta goles por mitad (15/15)
    independientemente de eventos por minuto, y bajo usar_gate_goles_mitad_15_15=True
    valida por marcador_1t/marcador_2t (15/15) aun sin cuadro_c_timeline.
    """
    dl = FIXTURES_R5["docs"]["turkey"]
    dv = FIXTURES_R5["docs"]["belgium"]
    an_rf = mm.analizar(dl, dv, "Turkey", "Belgium")
    info_rf = mm.cronologia_mitades_verificada(an_rf.motor)
    assert info_rf["n_ev_local"] == 0
    assert info_rf["n_goles_mitad_local"] == 0
    assert info_rf["goles_mitad_15_15_ok"] is False

    # Construir 15 partidos con marcador real 1T/2T por mitad pero SIN cronologia por minuto
    def _partidos_15_mitades():
        out = []
        for idx in range(15):
            out.append({
                "id_partido": f"gm_{idx}",
                "fecha": f"2026-10-{idx + 1:02d}",
                "marcador_1t": {"local": 1, "visitante": 0},
                "marcador_2t": {"local": 0, "visitante": 1},
            })
        return out

    class _DummyMotorMitades:
        doc_local = {"partidos": _partidos_15_mitades()}
        doc_visitante = {"partidos": _partidos_15_mitades()}
        cuadro_c_local = None
        cuadro_c_visita = None

    m_gm = _DummyMotorMitades()
    with banderas_comportamiento(activar_todas=False, usar_gate_cronologia_mitades_10_15=True):
        cron_min = mm.cronologia_mitades_verificada(m_gm)
        assert cron_min["n_goles_mitad_local"] == 15
        assert cron_min["n_goles_mitad_visitante"] == 15
        assert cron_min["goles_mitad_15_15_ok"] is True
        assert cron_min["ok"] is False  # el gate por minutos aun bloquea porque n_ev == 0

    with banderas_comportamiento(activar_todas=False, usar_gate_goles_mitad_15_15=True):
        cron_gm = mm.cronologia_mitades_verificada(m_gm)
        assert cron_gm["ok"] is True
        assert cron_gm["k_eval"] == 15
        assert cron_gm["k_min"] == 15
        assert cron_gm["modo_gate_mitades"] == "goles_por_mitad_15_15"


def test_punto6d_reparto_2t_turkey_belgium_vs_banco():
    """
    (d) Explica con precision que en TB el reparto 2T usado es 0.4916 (2.2565 / 4.59)
    porque P26_Poder_Cierre_2T de Belgium fuera es 0.4376, frente a 0.5872 (165/281)
    observado en los 83 partidos unicos del banco.
    """
    dl = FIXTURES_R5["docs"]["turkey"]
    dv = FIXTURES_R5["docs"]["belgium"]
    an = mm.analizar(dl, dv, "Turkey", "Belgium", condicion_local="casa", condicion_visitante="fuera")
    t = an.motor.tasas
    share_2t_tb = (t.lambda_local_2t + t.lambda_visitante_2t) / (t.lambda_local_ft + t.lambda_visitante_ft)
    assert share_2t_tb == pytest.approx(0.4916, abs=0.005)
    p55_v = (an.motor.recursivo or {}).get("patrones_55_visita") or {}
    assert float(p55_v["P26_Poder_Cierre_2T"]) == pytest.approx(0.4376, abs=0.005)

    rows = list(csv.DictReader((ROOT / "data" / "banco_partidos.csv").open(encoding="utf-8")))
    by_per = {}
    for r in rows:
        by_per.setdefault(r.get("periodo"), {})[r["id_partido"]] = r
    g2_u = sum(float(x["goles_favor"]) + float(x["goles_contra"]) for x in by_per["2T"].values())
    gft_u = sum(float(x["goles_favor"]) + float(x["goles_contra"]) for x in by_per["FT"].values())
    assert len(by_per["FT"]) == 83
    assert g2_u == 165.0 and gft_u == 281.0
    assert (g2_u / gft_u) == pytest.approx(0.5872, abs=1e-4)


def test_punto6e_stake_kelly_conservador_tope_3pct():
    """
    (e) Verifica tope 8% heredado vs tope 3% + min(f_quarter_bruta, f_quarter_cal)
    cuando usar_kelly_conservador_min_3pct=True.
    """
    with banderas_comportamiento(activar_todas=False):
        res_def = mm.calcular_ev_y_stake_calibrado("Doble oportunidad", 0.78, cuota_real=2.50)
        assert res_def["stake_sugerido"] > 0.03
        assert res_def["stake_sugerido"] <= 0.08

    with banderas_comportamiento(activar_todas=True, usar_kelly_conservador_min_3pct=True):
        res_cons = mm.calcular_ev_y_stake_calibrado("Doble oportunidad", 0.78, cuota_real=2.50)
        p_cal = float(res_cons["prob_calibrada"])
        b = 2.50 - 1.0
        fq_bruta = (((b * 0.78) - (1.0 - 0.78)) / b) * 0.25
        fq_cal = (((b * p_cal) - (1.0 - p_cal)) / b) * 0.25
        esperado = round(max(0.012, min(0.03, min(fq_bruta, fq_cal))), 4)
        assert res_cons["stake_sugerido"] == esperado
        assert res_cons["stake_sugerido"] <= 0.03

        # Si EV bruto > 0 pero EV calibrado <= 0 -> stake 0
        res_espurio = mm.calcular_ev_y_stake_calibrado("Ganador del partido", 0.78, cuota_real=1.32)
        assert res_espurio["stake_sugerido"] == 0.0


def test_punto6f_primer_gol_no_liquidable_excluible_del_top5():
    """
    (f) Anota primero liquida no_verificable sin cronologia; sigue en Top 5 AB de TB
    con bandera apagada y se excluye cuando usar_excluir_primer_gol_top5=True.
    """
    sel_pg = {
        "id_mercado": "equipo_primer_gol",
        "mercado": "Equipo que anota el gol 1",
        "evento": "primer_gol_visitante",
        "etiqueta": "Anota primero Belgium",
        "periodo": "FT",
        "equipo": "Belgium",
        "linea": None,
        "probabilidad": 0.718,
    }
    res_part = ep.extraer_resultado(dict(FIXTURES_R5["partidos_objetivo"]["turkey_belgium"]), fuente="fotmob")
    ev_liq = ep.liquidar(sel_pg, res_part, "PUNTO6-PG")
    assert ev_liq.estado_liquidacion == "no_verificable"

    dl = FIXTURES_R5["docs"]["turkey"]
    dv = FIXTURES_R5["docs"]["belgium"]
    with banderas_comportamiento(activar_todas=True):
        an_off = mm.analizar(dl, dv, "Turkey", "Belgium", condicion_local="casa", condicion_visitante="fuera")
        top5_off, _, _ = sm.obtener_top5_y_seccion1_filtrados(an_off, metadatos={"equipo_local": "Turkey", "equipo_visitante": "Belgium"}, umbral=0.70)
        assert any("anota primero" in str(x.get("etiqueta") or "").lower() for x in top5_off)

    with banderas_comportamiento(activar_todas=True, usar_excluir_primer_gol_top5=True):
        an_on = mm.analizar(dl, dv, "Turkey", "Belgium", condicion_local="casa", condicion_visitante="fuera")
        top5_on, _, _ = sm.obtener_top5_y_seccion1_filtrados(an_on, metadatos={"equipo_local": "Turkey", "equipo_visitante": "Belgium"}, umbral=0.70)
        assert not any("anota primero" in str(x.get("etiqueta") or "").lower() for x in top5_on)


def test_punto6h_y_6i_flashscore_historial_y_lambda_plano_vs_final():
    """
    (h) Tasa real de emparejamiento Flashscore en banco_partidos.csv (31/83 = 37.3% unicos; 34/94 = 36.2% filas).
    (i) Emparejamiento por equipos+fecha OOS evaluando lambda_plano vs lambda_final (soloA/AB).
    """
    rows = [r for r in csv.DictReader((ROOT / "data" / "banco_partidos.csv").open(encoding="utf-8")) if r.get("periodo") == "FT"]
    assert len(rows) == 94
    assert sum(1 for r in rows if r.get("flashscore_pareado") == "1") == 34
    uniq = {r["id_partido"]: r for r in rows}
    assert len(uniq) == 83
    assert sum(1 for r in uniq.values() if r.get("flashscore_pareado") == "1") == 31
    assert sum(1 for r in uniq.values() if r.get("coherencia_1t_2t_ft") == "OK") == 82

    # (i) Evaluacion comparativa lambda_plano vs soloA vs AB sobre partidos reales no-diseno con emparejamiento por equipos+fecha
    ev93 = json.loads((ROOT / "informes" / "evaluacion" / "evaluacion_9393a7b9017a5365.json").read_text(encoding="utf-8"))["evaluaciones"]
    aud = ROOT / "informes" / "auditorias" / "2026-10-13"
    pares = [
        ("193a259f96e34e49", "Galatasaray vs Barcelona", "Galatasaray", "Barcelona", "2026-10-13",
         json.loads((aud / "auditoria_galatasaray_2026-10-13_102501.json").read_text(encoding="utf-8")),
         json.loads((aud / "auditoria_barcelona_2026-10-13_102522.json").read_text(encoding="utf-8"))),
        ("b9491017b83abfab", "Villarreal vs Napoli", "Villarreal", "Napoli", "2026-10-13",
         json.loads((aud / "auditoria_villarreal_2026-10-13_114102.json").read_text(encoding="utf-8")),
         json.loads((aud / "auditoria_napoli_2026-10-13_114129.json").read_text(encoding="utf-8"))),
    ]

    observaciones = []
    for jid, nombre, eq_l, eq_v, fecha_str, dl, dv in pares:
        dl_c = {"equipo": dl.get("equipo"), "partidos": [x for x in dl.get("partidos", []) if str(x.get("fecha") or "")[:10] < fecha_str]}
        dv_c = {"equipo": dv.get("equipo"), "partidos": [x for x in dv.get("partidos", []) if str(x.get("fecha") or "")[:10] < fecha_str]}
        with banderas_comportamiento(activar_todas=False):
            an_a = mm.analizar(dl_c, dv_c, eq_l, eq_v, fecha_partido=fecha_str, condicion_local="casa", condicion_visitante="fuera")
        with banderas_comportamiento(activar_todas=True):
            an_ab = mm.analizar(dl_c, dv_c, eq_l, eq_v, fecha_partido=fecha_str, condicion_local="casa", condicion_visitante="fuera")

        tr_pl = next(s for s in an_a.motor.traza_lambda if s.get("regla") == "tasas_goles_plano")
        lam_pl_l = float(tr_pl["lam_l_despues"])
        lam_pl_v = float(tr_pl["lam_v_despues"])
        pa_map = {(_norm_s(s.evento), str(s.periodo or "FT").upper(), _norm_s(s.equipo), None if s.linea is None else round(float(s.linea), 4)): float(s.probabilidad)
                  for rm in an_a.mercados for s in rm.selecciones if s.estimable and s.probabilidad is not None}
        pab_map = {(_norm_s(s.evento), str(s.periodo or "FT").upper(), _norm_s(s.equipo), None if s.linea is None else round(float(s.linea), 4)): float(s.probabilidad)
                   for rm in an_ab.mercados for s in rm.selecciones if s.estimable and s.probabilidad is not None}

        # Emparejar filas de ev93 por equipo+partido+fecha (y trabajo)
        par_norm = _norm_s(nombre)
        for r in ev93:
            if not bool(r.get("evaluable_binario")) or r.get("estado_liquidacion") not in ("ganada", "perdida"):
                continue
            if _norm_s(r.get("partido")) != par_norm:
                continue
            p_pl = _prob_poisson_simple_row(r, lam_pl_l, lam_pl_v, eq_l, eq_v)
            k = (_norm_s(r.get("evento")), str(r.get("periodo") or "FT").upper(), _norm_s(r.get("equipo")), None if r.get("linea") is None else round(float(r["linea"]), 4))
            if p_pl is not None and k in pa_map and k in pab_map:
                observaciones.append({
                    "id_partido": str(r.get("id_partido") or jid),
                    "partido": nombre,
                    "mercado": r.get("mercado"),
                    "evento_ocurrio": int(r["evento_ocurrio"]),
                    "probs": {"plano": p_pl, "soloA": pa_map[k], "AB": pab_map[k]},
                })

    assert len(observaciones) >= 10
    rep = ep.evaluar_oos_comparativo_sin_diseno(observaciones, n_boot=200, seed=2026)
    assert rep["total"]["n_partidos_unicos"] == 2
    assert rep["total"]["versiones"]["plano"]["brier"] > 0.0
    assert rep["total"]["versiones"]["soloA"]["brier"] > 0.0
    assert rep["total"]["versiones"]["AB"]["brier"] > 0.0
