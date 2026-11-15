"""
Tests de verificación de Ronda 8 — Punto 6:
Saneamiento de datos en las 30 matrices de Colombia vs Chile e informe de métricas
copia o derivadas de otras:
  1. pases_total (#08) en partidos 29 y 30 (reconstruido desde pases_completados y %;
     1T + 2T == FT y pases_total > pases_completados).
  2. entradas ganadas <= intentadas en partido 3 (Colombia vs Portugal: 5/15 [33%] y 8/9 [89%]
     en FT, 3/6 [50%] y 4/5 [80%] en 1T, 2/9 [22%] y 4/4 [100%] en 2T; nunca 5/4 [125%]).
  3. tiros libres (#25) invertidos respecto a faltas (#31) en partido 3 (6/12 FT,
     4/3 1T, 2/9 2T).
  4. bloqueos (#38) en sin_dato (None) donde corresponde sin_dato en partidos 14, 19 y 22.
  5. centros por mitad (#28 cruces_pct) en partidos 1 (Suiza vs Colombia: 1T 1/4 y 1/9,
     2T 0/10 y 2/10, FT 1/14 y 3/19) y 3 (Colombia vs Portugal: 1T 0/4 y 0/8, 2T 3/7 y 2/5,
     FT 3/11 y 2/13).
  6. formato en #32 (entradas_pct) del partido 29: 4/4 (100%) y 7/7 (100%) en 1T,
     nunca el entero suelto "100".
  7. catálogo estructurado de qué otras métricas de las 45 son copia o derivada de otras.
"""

from __future__ import annotations

import json
import os

from auditoria_historica import (
    auditar_metricas_copia_o_derivadas_45,
    formatear_matriz_45_partido,
    sanear_documento_auditoria_45,
)


def _cargar_partidos_30() -> list[dict]:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base_dir, "cache", "cache_fotmob_colombia_COL_False_False.json"), "r", encoding="utf-8") as f:
        doc_col = sanear_documento_auditoria_45(json.load(f))
    with open(os.path.join(base_dir, "cache", "cache_fotmob_chile_CHL_False_False.json"), "r", encoding="utf-8") as f:
        doc_chi = sanear_documento_auditoria_45(json.load(f))
    return (doc_col.get("partidos") or [])[:15] + (doc_chi.get("partidos") or [])[:15]


def _fila(partido: dict, periodo: str, clave: str) -> dict:
    filas = ((partido.get("periodos") or {}).get(periodo) or {}).get("filas") or []
    for f in filas:
        if f.get("clave") == clave:
            return f
    return {}


def test_ronda8_punto6_pases_total_partidos_29_y_30():
    pts = _cargar_partidos_30()
    p29 = pts[28]  # Matriz [29/30]: New Zealand (rival/Local) vs Chile (equipo/Visita)
    p30 = pts[29]  # Matriz [30/30]: Chile (equipo/Local) vs Cape Verde (rival/Visita)

    # Partido 29: New Zealand (rival) FT=479 (1T=213, 2T=266), Chile (equipo) FT=278 (1T=133, 2T=145)
    assert _fila(p29, "FT", "pases_total")["rival"] == 479.0
    assert _fila(p29, "1T", "pases_total")["rival"] == 213.0
    assert _fila(p29, "2T", "pases_total")["rival"] == 266.0
    assert _fila(p29, "FT", "pases_total")["equipo"] == 278.0
    assert _fila(p29, "1T", "pases_total")["equipo"] == 133.0
    assert _fila(p29, "2T", "pases_total")["equipo"] == 145.0

    md29 = formatear_matriz_45_partido(p29, k=29, total_partidos=30)
    assert "| 08 | Pases totales (`pases_total`) | 479 | 278 | 213 | 133 | 266 | 145 |" in md29
    assert "| 10 | Pases (completados/totales) (`pases_ratio`) | 413/479 (86%) | 204/278 (73%) | 180/213 (85%) | 94/133 (71%) | 233/266 (88%) | 110/145 (76%) |" in md29

    # Partido 30: Chile (equipo) FT=419 (1T=228, 2T=191), Cape Verde (rival) FT=382 (1T=191, 2T=191)
    assert _fila(p30, "FT", "pases_total")["equipo"] == 419.0
    assert _fila(p30, "1T", "pases_total")["equipo"] == 228.0
    assert _fila(p30, "2T", "pases_total")["equipo"] == 191.0
    assert _fila(p30, "FT", "pases_total")["rival"] == 382.0
    assert _fila(p30, "1T", "pases_total")["rival"] == 191.0
    assert _fila(p30, "2T", "pases_total")["rival"] == 191.0

    md30 = formatear_matriz_45_partido(p30, k=30, total_partidos=30)
    assert "| 08 | Pases totales (`pases_total`) | 419 | 382 | 228 | 191 | 191 | 191 |" in md30
    assert "| 10 | Pases (completados/totales) (`pases_ratio`) | 356/419 (85%) | 317/382 (83%) | 201/228 (88%) | 160/191 (84%) | 155/191 (81%) | 157/191 (82%) |" in md30


def test_ronda8_punto6_partido_3_entradas_tiros_libres_y_centros():
    pts = _cargar_partidos_30()
    p3 = pts[2]  # Matriz [3/30]: Colombia vs Portugal (2026-06-27)
    md3 = formatear_matriz_45_partido(p3, k=3, total_partidos=30)

    # Entradas ganadas <= intentadas en FT, 1T y 2T y 1T + 2T == FT
    for per in ("FT", "1T", "2T"):
        eg = _fila(p3, per, "entradas_ganadas")
        et = _fila(p3, per, "entradas")
        for lado in ("equipo", "rival"):
            assert eg[lado] <= et[lado]

    assert "| 32 | Entradas (tackles ganados) (`entradas_pct`) | 5/15 (33%) | 8/9 (89%) | 3/6 (50%) | 4/5 (80%) | 2/9 (22%) | 4/4 (100%) |" in md3
    assert "| 5/4 (" not in md3
    assert "125%" not in md3

    # Tiros libres (#25) invertidos respecto a faltas (#31: 12/6 FT, 3/4 1T, 9/2 2T -> tiros_libres: 6/12 FT, 4/3 1T, 2/9 2T)
    assert "| 25 | Tiros libres directos (`tiros_libres`) | 6 | 12 | 4 | 3 | 2 | 9 |" in md3
    assert "| 31 | Faltas cometidas (`faltas`) | 12 | 6 | 3 | 4 | 9 | 2 |" in md3

    # Centros por mitad (#28) en partidos 1 y 3: 1T + 2T == FT
    assert "| 28 | Cruces / centros precisos (`cruces_pct`) | 3/11 (27%) | 2/13 (15%) | 0/4 (0%) | 0/8 (0%) | 3/7 (43%) | 2/5 (40%) |" in md3

    p1 = pts[0]  # Matriz [1/30]: Switzerland vs Colombia (2026-09-24)
    md1 = formatear_matriz_45_partido(p1, k=1, total_partidos=30)
    assert "| 28 | Cruces / centros precisos (`cruces_pct`) | 1/14 (7%) | 3/19 (16%) | 1/4 (25%) | 1/9 (11%) | 0/10 (0%) | 2/10 (20%) |" in md1


def test_ronda8_punto6_bloqueos_sin_dato_y_formato_100_partido_29():
    pts = _cargar_partidos_30()
    for idx_m in (14, 19, 22):
        p = pts[idx_m - 1]
        for per in ("FT", "1T", "2T"):
            bl = _fila(p, per, "bloqueos")
            assert bl.get("equipo") is None
            assert bl.get("rival") is None
        md = formatear_matriz_45_partido(p, k=idx_m, total_partidos=30)
        assert "| 38 | Bloqueos (`bloqueos`) | sin_dato | sin_dato | sin_dato | sin_dato | sin_dato | sin_dato | sin_dato |" in md

    # Formato "100" corregido en #32 del partido 29 (imprime 4/4 (100%) y 7/7 (100%), nunca "100" suelto)
    p29 = pts[28]
    md29 = formatear_matriz_45_partido(p29, k=29, total_partidos=30)
    assert "| 32 | Entradas (tackles ganados) (`entradas_pct`) | 12/13 (92%) | 12/15 (80%) | 4/4 (100%) | 7/7 (100%) | 8/9 (89%) | 5/8 (62%) |" in md29
    assert "| 100 | 100 |" not in md29


def test_ronda8_punto6_catalogo_metricas_copia_o_derivadas():
    cat = auditar_metricas_copia_o_derivadas_45()
    assert len(cat) >= 7
    claves = {x["clave"] for x in cat}
    assert "pases_ratio" in claves
    assert "xgot_enfrentado" in claves
    assert "goles_evitados" in claves
    assert "tiros_libres" in claves
    assert "remates_totales" in claves
    assert "duelos_ganados" in claves
