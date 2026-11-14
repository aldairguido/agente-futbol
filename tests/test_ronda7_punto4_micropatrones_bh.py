# -*- coding: utf-8 -*-
"""
Ronda 7 - Punto 4: Evaluacion de micropatrones MP01..MP04 en el banco
(83 partidos unicos, 94 filas FT, pares compartidos y walk-forward),
origen de umbrales, tasas vs tasa base, ajuste Benjamini-Hochberg
y rotulacion obligatoria "hipótesis, no validada".
"""

from __future__ import annotations

from collections import defaultdict
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evaluacion_pronosticos as ep
import motor_patrones as mp


def _fnum(v):
    if v in (None, "", "sin_dato", "None", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def test_punto4_etiquetado_hipotesis_no_validada_y_origen_umbrales():
    fix = json.loads((ROOT / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))
    res = mp.evaluar_micropatrones_combinatorios(
        fix["docs"]["italy"], fix["docs"]["france"], eq_l="Italy", eq_v="France"
    )
    todos = res.get("todos") or []
    codigos = [p["codigo"] for p in todos]
    assert codigos == [
        "MP01_ASEDIO_2T_LOCAL",
        "MP02_FRENO_CORNERS_VISITA",
        "MP03_1T_CONTENIDO_EXPLOSION_2T",
        "MP04_DOMINIO_ESTERIL_VS_MURO",
    ]
    for p in todos:
        assert p["estado_validacion"] == "hipótesis, no validada"
        assert p["validada_cronologicamente"] is False
        assert isinstance(p.get("origen_umbrales"), str) and len(p["origen_umbrales"]) >= 15


def test_punto4_benjamini_hochberg_matematico_exacto():
    pvals = [0.005, 0.02, 0.04, 0.50]
    qs = ep.benjamini_hochberg(pvals)
    # m=4: [0.005*4/1=0.02, 0.02*4/2=0.04, 0.04*4/3=0.053333, 0.50*4/4=0.50]
    assert qs == [0.02, 0.04, 0.053333, 0.5]

    p_fisher = ep.fisher_exact_dos_colas(5, 5, 0, 5)
    assert abs(p_fisher - (1.0 / 126.0)) < 1e-6


def test_punto4_banco_83_partidos_y_bh_ninguna_regla_significativa():
    rows = list(csv.DictReader((ROOT / "data" / "banco_partidos.csv").open(encoding="utf-8")))
    by_team_pid = {}
    for r in rows:
        tk = (r.get("equipo_clave") or "").strip().lower()
        pid = str(r.get("id_partido") or "").strip()
        per = (r.get("periodo") or "").strip().upper()
        if tk and pid and per in ("FT", "1T", "2T"):
            by_team_pid.setdefault((tk, pid), {})[per] = r

    pids_ft = {pid for (_tk, pid), pmap in by_team_pid.items() if "FT" in pmap}
    assert len(pids_ft) == 83

    # Pares compartidos (11 partidos con local + visitante en banco)
    pid_pairs = defaultdict(list)
    for (tk, pid), per in by_team_pid.items():
        ft = per.get("FT") or {}
        if ft:
            pid_pairs[pid].append((tk, str(ft.get("condicion") or "").lower(), str(ft.get("fecha") or ""), per))

    team_matches = defaultdict(list)
    for (tk, pid), per in by_team_pid.items():
        ft = per.get("FT") or {}
        if ft:
            team_matches[tk].append((str(ft.get("fecha") or ""), pid, per))
    for tk in team_matches:
        team_matches[tk].sort(key=lambda x: (x[0], x[1]))

    def _build_doc(tk, pids_prior):
        partidos = []
        for pid in pids_prior:
            per = by_team_pid[(tk, pid)]
            ft, t1, t2 = per["FT"], per["1T"], per["2T"]
            filas = []
            for cv in ("corners", "goles_favor", "xg", "remates_totales", "grandes_ocasiones", "toques_area_rival", "posesion_pct", "despejes", "goles_evitados"):
                val = _fnum(ft.get(cv))
                if val is not None:
                    filas.append({"clave": cv, "equipo": val})
            partidos.append({
                "id_partido": pid,
                "fecha": ft.get("fecha"),
                "condicion": ft.get("condicion"),
                "periodos": {"FT": {"filas": filas}},
                "resumen_1t": {"xg": _fnum(t1.get("xg")), "grandes_ocasiones": _fnum(t1.get("grandes_ocasiones")), "toques_area_rival": _fnum(t1.get("toques_area_rival"))},
                "resumen_2t": {"xg": _fnum(t2.get("xg")), "grandes_ocasiones": _fnum(t2.get("grandes_ocasiones")), "toques_area_rival": _fnum(t2.get("toques_area_rival"))},
            })
        partidos.sort(key=lambda x: str(x.get("fecha") or ""), reverse=True)
        return {"equipo": {"nombre": tk}, "partidos": partidos}

    observaciones_pares_nodes = []
    pares_compartidos = 0
    for pid, entries in sorted(pid_pairs.items()):
        if len(entries) != 2:
            continue
        pares_compartidos += 1
        if pid in ("5181796", "5181797"):
            continue
        entries_ord = sorted(entries, key=lambda e: 0 if e[1] == "casa" else 1)
        (tk_l, _, f_l, per_l), (tk_v, _, f_v, per_v) = entries_ord
        pri_l = [p for f, p, _ in team_matches[tk_l] if f < f_l]
        pri_v = [p for f, p, _ in team_matches[tk_v] if f < f_v]
        mp_res = mp.evaluar_micropatrones_combinatorios(_build_doc(tk_l, pri_l), _build_doc(tk_v, pri_v), eq_l=tk_l, eq_v=tk_v)
        activos = set(mp_res["codigos_activos"])

        gl = _fnum(per_l["FT"].get("goles_favor"))
        gv = _fnum(per_l["FT"].get("goles_contra"))
        cl = _fnum(per_l["FT"].get("corners"))
        cv = _fnum(per_v["FT"].get("corners"))
        g1t = (_fnum(per_l["1T"].get("goles_favor")) or 0.0) + (_fnum(per_l["1T"].get("goles_contra")) or 0.0)
        g2t = (_fnum(per_l["2T"].get("goles_favor")) or 0.0) + (_fnum(per_l["2T"].get("goles_contra")) or 0.0)

        observaciones_pares_nodes.extend([
            {"regla": "MP01_ASEDIO_2T_LOCAL", "objetivo": "total_goles:over_1.5", "activo": "MP01_ASEDIO_2T_LOCAL" in activos, "evento_ocurrio": int(gl + gv > 1.5)},
            {"regla": "MP01_ASEDIO_2T_LOCAL", "objetivo": "doble_oportunidad:x2", "activo": "MP01_ASEDIO_2T_LOCAL" in activos, "evento_ocurrio": int(gv >= gl)},
            {"regla": "MP02_FRENO_CORNERS_VISITA", "objetivo": "corners_visita:over_3.5", "activo": "MP02_FRENO_CORNERS_VISITA" in activos, "evento_ocurrio": int(cv > 3.5)},
            {"regla": "MP02_FRENO_CORNERS_VISITA", "objetivo": "total_corners:under_11.5", "activo": "MP02_FRENO_CORNERS_VISITA" in activos, "evento_ocurrio": int(cl + cv < 11.5)},
            {"regla": "MP03_1T_CONTENIDO_EXPLOSION_2T", "objetivo": "goles_1t:over_0.5", "activo": "MP03_1T_CONTENIDO_EXPLOSION_2T" in activos, "evento_ocurrio": int(g1t > 0.5)},
            {"regla": "MP03_1T_CONTENIDO_EXPLOSION_2T", "objetivo": "goles_2t:over_0.5", "activo": "MP03_1T_CONTENIDO_EXPLOSION_2T" in activos, "evento_ocurrio": int(g2t > 0.5)},
            {"regla": "MP04_DOMINIO_ESTERIL_VS_MURO", "objetivo": "total_goles:under_2.5", "activo": "MP04_DOMINIO_ESTERIL_VS_MURO" in activos, "evento_ocurrio": int(gl + gv < 2.5)},
        ])

    assert pares_compartidos == 11
    tabla_bh = ep.resumir_hipotesis_bh(observaciones_pares_nodes, alfa_fdr=0.05)
    assert len(tabla_bh) == 7
    assert all(row["significativa_bh"] is False for row in tabla_bh)
    assert all(row["estado_validacion"] == "hipótesis, no validada" for row in tabla_bh)
    assert min(row["bh_q"] for row in tabla_bh) >= 0.90
