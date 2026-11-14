# -*- coding: utf-8 -*-
"""
Ronda 7 - Punto 3: Evaluacion OOS sin los dos partidos de diseno (Turkey-Belgium
e Italy-France), Brier y log-loss por familia y total con bootstrap por partido,
conteo de partidos unicos y advertencia de muestra pequena (< 30).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from decision_cobertura import banderas_comportamiento
import evaluacion_pronosticos as ep
import motor_mercados as mm


def _norm_s(s: object) -> str:
    return " ".join(
        unicodedata.normalize("NFKD", str(s or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .split()
    )


def test_punto3_excluye_diseno_y_calcula_bootstrap_por_partido():
    observaciones = [
        # Partidos de diseno que DEBEN excluirse por nombre o id_partido
        {"id_partido": "5181796", "partido": "Turkey vs Belgium", "mercado": "Total de goles", "evento_ocurrio": 1, "probs": {"5e84": 0.99, "soloA": 0.99, "AB": 0.01}},
        {"id_partido": "9999999", "partido": "Italia vs Francia", "mercado": "Total de goles", "evento_ocurrio": 1, "probs": {"5e84": 0.99, "soloA": 0.99, "AB": 0.01}},
        # Tres partidos OOS validos en familias distintas
        {"id_partido": "m1", "partido": "Equipo A vs Equipo B", "mercado": "Total de goles", "evento_ocurrio": 1, "probs": {"5e84": 0.70, "soloA": 0.75, "AB": 0.65}},
        {"id_partido": "m1", "partido": "Equipo A vs Equipo B", "mercado": "Total de córneres", "evento_ocurrio": 1, "probs": {"5e84": 0.60, "soloA": 0.60, "AB": 0.75}},
        {"id_partido": "m2", "partido": "Equipo C vs Equipo D", "mercado": "Total de goles", "evento_ocurrio": 0, "probs": {"5e84": 0.40, "soloA": 0.35, "AB": 0.45}},
        {"id_partido": "m2", "partido": "Equipo C vs Equipo D", "mercado": "Total de córneres", "evento_ocurrio": 0, "probs": {"5e84": 0.50, "soloA": 0.50, "AB": 0.30}},
        {"id_partido": "m3", "partido": "Equipo E vs Equipo F", "mercado": "Doble oportunidad", "evento_ocurrio": 1, "probs": {"5e84": 0.72, "soloA": 0.74, "AB": 0.70}},
    ]

    res = ep.evaluar_oos_comparativo_sin_diseno(observaciones, n_boot=400, seed=123)
    assert res["filas_excluidas_diseno"] == 2
    tot = res["total"]
    assert tot["n_partidos_unicos"] == 3
    assert tot["n_filas"] == 5
    assert tot["suficiente_n30"] is False
    assert "pocos partidos" in tot["nota_muestra"].lower()

    for ver in ("5e84", "soloA", "AB"):
        v = tot["versiones"][ver]
        assert v["brier_ic95"][0] <= v["brier"] <= v["brier_ic95"][1]
        assert v["log_loss_ic95"][0] <= v["log_loss"] <= v["log_loss_ic95"][1]

    assert "Córneres" in res["por_familia"]
    assert "Total de goles" in res["por_familia"]
    assert "Doble oportunidad" in res["por_familia"]
    assert res["por_familia"]["Total de goles"]["n_partidos_unicos"] == 2
    assert "AB - soloA" in tot["diferencias"]
    assert "d_brier_ic95" in tot["diferencias"]["AB - soloA"]


def test_punto3_archivo_real_ev93_partidos_unicos_y_sin_diseno():
    ruta_ev93 = ROOT / "informes" / "evaluacion" / "evaluacion_9393a7b9017a5365.json"
    datos = json.loads(ruta_ev93.read_text(encoding="utf-8"))["evaluaciones"]
    ev_bin = [
        e for e in datos
        if bool(e.get("evaluable_binario"))
        and e.get("probabilidad_original") is not None
        and e.get("evento_ocurrio") in (0, 1)
    ]
    assert len(ev_bin) == 2435

    diseno = [e for e in ev_bin if ep.es_partido_de_diseno(e.get("partido"), e.get("id_partido"))]
    assert len(diseno) == 0

    partidos_unicos = {e.get("id_partido") for e in ev_bin}
    assert len(partidos_unicos) == 24
    assert len(partidos_unicos) < ep.MIN_PARTIDOS_PARA_CALIBRAR

    obs_ev93 = [
        {
            "id_partido": e.get("id_partido"),
            "partido": e.get("partido"),
            "mercado": e.get("mercado"),
            "evento_ocurrio": int(e["evento_ocurrio"]),
            "probs": {"archivado": float(e["probabilidad_original"])},
        }
        for e in ev_bin
    ]
    res = ep.evaluar_oos_comparativo_sin_diseno(obs_ev93, n_boot=300, seed=20261114)
    assert res["total"]["n_partidos_unicos"] == 24
    assert res["total"]["n_filas"] == 2435
    assert res["total"]["suficiente_n30"] is False
    assert abs(res["total"]["versiones"]["archivado"]["brier"] - 0.130224) < 1e-4


def test_punto3_comparacion_real_soloA_vs_AB_en_partidos_no_diseno():
    # Comparacion real sobre Galatasaray vs Barcelona y Villarreal vs Napoli
    # verificando diferencias OOS sin incluir TB ni IF.
    aud = ROOT / "informes" / "auditorias" / "2026-10-13"
    pares = [
        (
            "193a259f96e34e49",
            "Galatasaray vs Barcelona",
            "Galatasaray",
            "Barcelona",
            json.loads((aud / "auditoria_galatasaray_2026-10-13_102501.json").read_text(encoding="utf-8")),
            json.loads((aud / "auditoria_barcelona_2026-10-13_102522.json").read_text(encoding="utf-8")),
        ),
        (
            "b9491017b83abfab",
            "Villarreal vs Napoli",
            "Villarreal",
            "Napoli",
            json.loads((aud / "auditoria_villarreal_2026-10-13_114102.json").read_text(encoding="utf-8")),
            json.loads((aud / "auditoria_napoli_2026-10-13_114129.json").read_text(encoding="utf-8")),
        ),
    ]

    ev93 = json.loads((ROOT / "informes" / "evaluacion" / "evaluacion_9393a7b9017a5365.json").read_text(encoding="utf-8"))["evaluaciones"]
    ev_por_jid = {}
    for row in ev93:
        if bool(row.get("evaluable_binario")) and row.get("estado_liquidacion") in ("ganada", "perdida"):
            ev_por_jid.setdefault(row.get("id_trabajo"), []).append(row)

    def _key(ev, per, eq, lin):
        eq_c = None if eq in (None, "", "Total", "None") else _norm_s(eq)
        lin_c = None if lin is None else round(float(lin), 4)
        return (_norm_s(ev), str(per or "FT").strip().upper(), eq_c, lin_c)

    observaciones = []
    for jid, nombre, eq_l, eq_v, dl, dv in pares:
        dl_c = {"equipo": dl.get("equipo"), "partidos": [x for x in dl.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"]}
        dv_c = {"equipo": dv.get("equipo"), "partidos": [x for x in dv.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"]}
        with banderas_comportamiento(activar_todas=False):
            an_a = mm.analizar(dl_c, dv_c, eq_l, eq_v, fecha_partido="2026-10-13", condicion_local="casa", condicion_visitante="fuera")
        with banderas_comportamiento(activar_todas=True):
            an_ab = mm.analizar(dl_c, dv_c, eq_l, eq_v, fecha_partido="2026-10-13", condicion_local="casa", condicion_visitante="fuera")

        assert (an_a.motor.tasas.lambda_visitante_ft != an_ab.motor.tasas.lambda_visitante_ft) or (
            an_a.motor.tasas.lambda_local_ft != an_ab.motor.tasas.lambda_local_ft
        )

        p_a = {
            _key(s.evento, s.periodo, s.equipo, s.linea): float(s.probabilidad)
            for rm in an_a.mercados for s in rm.selecciones
            if s.estimable and s.probabilidad is not None
        }
        p_ab = {
            _key(s.evento, s.periodo, s.equipo, s.linea): float(s.probabilidad)
            for rm in an_ab.mercados for s in rm.selecciones
            if s.estimable and s.probabilidad is not None
        }
        for r in ev_por_jid.get(jid, []):
            k = _key(r.get("evento"), r.get("periodo"), r.get("equipo"), r.get("linea"))
            if k in p_a and k in p_ab:
                observaciones.append({
                    "id_partido": str(r.get("id_partido") or jid),
                    "partido": nombre,
                    "mercado": r.get("mercado"),
                    "evento_ocurrio": int(r["evento_ocurrio"]),
                    "probs": {"soloA": p_a[k], "AB": p_ab[k]},
                })

    assert len(observaciones) >= 50
    rep = ep.evaluar_oos_comparativo_sin_diseno(observaciones, n_boot=200, seed=99)
    assert rep["total"]["n_partidos_unicos"] == 2
    assert rep["total"]["suficiente_n30"] is False
    assert "Córneres" in rep["por_familia"]
    assert "Total de goles" in rep["por_familia"]
