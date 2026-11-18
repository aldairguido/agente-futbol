"""Tests para ORDEN RONDA 11 — Punto 5 (Impacto de los 2 arreglos de
calcular_frecuencia_observada: periodo 1T/2T y 'al menos' g >= lin sobre los 73 registros).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from decision_cobertura import banderas_comportamiento
import motor_mercados as mm
import servicio_mercados as sm


def _frec_5e8425f(sel, doc_local, doc_visitante, eq_l="Local", eq_v="Visitante"):
    """Replica exacta de calcular_frecuencia_observada en 5e8425f (sin fix periodo 1T/2T ni 'al menos')."""
    def _v(obj, k, d=None):
        return obj.get(k, d) if isinstance(obj, dict) else getattr(obj, k, d)

    ev = str(_v(sel, "evento") or "").lower()
    et = str(_v(sel, "etiqueta") or "").lower()
    mkt = str(_v(sel, "mercado") or "").lower()
    eq = str(_v(sel, "equipo") or "").strip()
    lin_raw = _v(sel, "linea", None)
    try:
        lin = float(lin_raw) if lin_raw is not None else None
    except Exception:
        lin = None

    def _extraer_pts(doc):
        if not isinstance(doc, dict):
            return []
        res = []
        for p in doc.get("partidos", []) or []:
            if not isinstance(p, dict):
                continue
            m = p.get("marcador", {}) or {}
            gf, gc = m.get("equipo"), m.get("rival")
            cf, cr, tf, tr = None, None, None, None
            for f in (p.get("periodos", {}).get("FT", {}) or {}).get("filas", []):
                if f.get("clave") == "corners":
                    cf, cr = f.get("equipo"), f.get("rival")
                elif f.get("clave") in ("tarjetas_amarillas", "tarjetas"):
                    tf, tr = f.get("equipo"), f.get("rival")
            res.append({"gf": gf, "gc": gc, "cf": cf, "cr": cr, "tf": tf, "tr": tr})
        return res

    pts_l = _extraer_pts(doc_local)
    pts_v = _extraer_pts(doc_visitante)
    if not pts_l and not pts_v:
        return None
    from resolvedor_entidades import normalizar_texto
    nom_l, nom_v, eq_norm = normalizar_texto(eq_l), normalizar_texto(eq_v), normalizar_texto(eq)
    es_under = ("under" in ev or "menos" in et)

    if "doble_oportunidad" in mkt or "doble oportunidad" in mkt or any(k in ev for k in ("dc_1x", "dc_x2", "dc_12")) or any(et.startswith(k) for k in ("1x", "x2", "12")):
        if "1x" in ev or "1x" in et:
            valid = [p for p in pts_l if p.get("gf") is not None and p.get("gc") is not None]
            return (sum(1 for p in valid if p["gf"] >= p["gc"]) / len(valid)) if valid else None
        if "x2" in ev or "x2" in et:
            valid = [p for p in pts_v if p.get("gf") is not None and p.get("gc") is not None]
            return (sum(1 for p in valid if p["gf"] >= p["gc"]) / len(valid)) if valid else None
        valid = [p for p in (pts_l + pts_v) if p.get("gf") is not None and p.get("gc") is not None]
        return (sum(1 for p in valid if p["gf"] != p["gc"]) / len(valid)) if valid else None

    if (any(k in mkt for k in ("corner", "córner")) or "corner" in ev) and lin is not None:
        if "local" in ev or (eq_norm and (eq_norm == nom_l or eq_norm in nom_l or nom_l in eq_norm)):
            valid = [float(p["cf"]) for p in pts_l if p.get("cf") is not None]
        elif "visita" in ev or "visitante" in ev or (eq_norm and (eq_norm == nom_v or eq_norm in nom_v or nom_v in eq_norm)):
            valid = [float(p["cf"]) for p in pts_v if p.get("cf") is not None]
        else:
            valid = [float(p["cf"] + p["cr"]) for p in (pts_l + pts_v) if p.get("cf") is not None and p.get("cr") is not None]
        if not valid:
            return None
        return (sum(1 for c in valid if c < lin) / len(valid)) if es_under else (sum(1 for c in valid if c > lin) / len(valid))

    if ("total" in mkt or "goles" in mkt) and not eq_norm and not any(k in ev for k in ("local", "visita")) and lin is not None:
        valid = [float(p["gf"] + p["gc"]) for p in (pts_l + pts_v) if p.get("gf") is not None and p.get("gc") is not None]
        if not valid:
            return None
        return (sum(1 for g in valid if g < lin) / len(valid)) if es_under else (sum(1 for g in valid if g > lin) / len(valid))

    if ("goles" in mkt or "gol" in ev) and eq_norm and lin is not None:
        if eq_norm == nom_l or eq_norm in nom_l or nom_l in eq_norm:
            valid = [float(p["gf"]) for p in pts_l if p.get("gf") is not None]
        elif eq_norm == nom_v or eq_norm in nom_v or nom_v in eq_norm:
            valid = [float(p["gf"]) for p in pts_v if p.get("gf") is not None]
        else:
            valid = []
        if not valid:
            return None
        return (sum(1 for g in valid if g < lin) / len(valid)) if es_under else (sum(1 for g in valid if g > lin) / len(valid))

    if "ambos_anotan" in ev or "btts" in ev:
        valid = [(float(p["gf"]), float(p["gc"])) for p in (pts_l + pts_v) if p.get("gf") is not None and p.get("gc") is not None]
        if not valid:
            return None
        return (sum(1 for gf, gc in valid if gf == 0 or gc == 0) / len(valid)) if "no" in ev else (sum(1 for gf, gc in valid if gf > 0 and gc > 0) / len(valid))

    if (any(k in mkt for k in ("tarjeta", "tarjetas")) or "tarjeta" in ev) and lin is not None:
        valid = [float(p["tf"] + p["tr"]) for p in (pts_l + pts_v) if p.get("tf") is not None and p.get("tr") is not None]
        if not valid:
            return None
        return (sum(1 for t in valid if t < lin) / len(valid)) if es_under else (sum(1 for t in valid if t > lin) / len(valid))

    return None


def test_ronda11_p5_impacto_73_registros_y_declaracion_conjunto_a():
    p_json = Path(ROOT) / "datos" / "ronda11_punto5_impacto_73_tickets.json"
    assert p_json.exists()
    rep = json.loads(p_json.read_text(encoding="utf-8"))

    assert rep["denominador_registros"] == 73
    assert "cambia tickets aunque no cambie λ" in rep["declaracion_conjunto_a"]

    solo_a = rep["soloA"]
    assert solo_a["pre_vs_solo_al_menos"]["n_partidos_lambda_cambiado"] == 0
    assert solo_a["pre_vs_solo_periodo_1t"]["n_partidos_lambda_cambiado"] == 0
    assert solo_a["pre_vs_ambos_arreglos"]["n_partidos_lambda_cambiado"] == 0

    ambos = solo_a["pre_vs_ambos_arreglos"]
    assert ambos["n_partidos_evaluados"] == 73
    assert ambos["top5"]["n_partidos_top5_cambiado"] == 29
    assert ambos["top5"]["total_slots_diferentes"] == 55
    assert ambos["seccion_1"]["n_partidos_sec1_cambiado"] == 62
    assert ambos["seccion_1"]["total_lineas_cambiadas_altas_bajas"] == 310

    assert solo_a["pre_vs_solo_al_menos"]["top5"]["n_partidos_top5_cambiado"] == 1
    assert solo_a["pre_vs_solo_al_menos"]["seccion_1"]["n_partidos_sec1_cambiado"] == 48
    assert solo_a["pre_vs_solo_periodo_1t"]["top5"]["n_partidos_top5_cambiado"] == 29
    assert solo_a["pre_vs_solo_periodo_1t"]["seccion_1"]["n_partidos_sec1_cambiado"] == 55

    ab = rep["AB_con_banderas"]["pre_vs_ambos_arreglos"]
    assert ab["n_partidos_lambda_cambiado"] == 0
    assert ab["top5"]["n_partidos_top5_cambiado"] == 4
    assert ab["seccion_1"]["n_partidos_sec1_cambiado"] == 45


def test_ronda11_p5_reproduccion_viva_tb_y_colombia_chile_sin_cambio_lambda():
    r5 = json.loads((Path(ROOT) / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))["docs"]
    dl_col = json.loads((Path(ROOT) / "cache" / "cache_fotmob_colombia_COL_False_False.json").read_text(encoding="utf-8-sig"))
    dv_chi = json.loads((Path(ROOT) / "cache" / "cache_fotmob_chile_CHL_False_False.json").read_text(encoding="utf-8-sig"))

    def _run(dl, dv, el, ev, comp, fp=None):
        an = mm.analizar(dl, dv, el, ev, competicion=comp, fecha_partido=fp, condicion_local="casa", condicion_visitante="fuera")
        t5, s1, _ = sm.obtener_top5_y_seccion1_filtrados(an, metadatos={"equipo_local": el, "equipo_visitante": ev}, umbral=0.70)
        return (an.motor.tasas.lambda_local_ft, an.motor.tasas.lambda_visitante_ft), [x["etiqueta"] for x in t5], [x.etiqueta for x in s1]

    with banderas_comportamiento(activar_todas=False):
        lam_tb_new, t5_tb_new, s1_tb_new = _run(r5["turkey"], r5["belgium"], "Turkey", "Belgium", "UEFA Nations League")
        lam_cc_new, t5_cc_new, s1_cc_new = _run(dl_col, dv_chi, "Colombia", "Chile", "Eliminatorias CONMEBOL", "2026-11-12")

        orig_fn = mm.calcular_frecuencia_observada
        mm.calcular_frecuencia_observada = _frec_5e8425f
        try:
            lam_tb_old, t5_tb_old, s1_tb_old = _run(r5["turkey"], r5["belgium"], "Turkey", "Belgium", "UEFA Nations League")
            lam_cc_old, t5_cc_old, s1_cc_old = _run(dl_col, dv_chi, "Colombia", "Chile", "Eliminatorias CONMEBOL", "2026-11-12")
        finally:
            mm.calcular_frecuencia_observada = orig_fn

    assert lam_tb_new == lam_tb_old
    assert lam_cc_new == lam_cc_old
    assert t5_tb_new != t5_tb_old
    assert s1_tb_new != s1_tb_old
    assert len(t5_cc_old) == 2 and len(t5_cc_new) == 4
    assert len(s1_cc_old) == 12 and len(s1_cc_new) == 14
