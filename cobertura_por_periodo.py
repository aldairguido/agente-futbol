"""
COBERTURA POR PERIODO (FT / 1T / 2T): que metricas faltan y donde.

Responde a la pregunta: ¿se puede rellenar TODO en los tres periodos, o hay
metricas que la fuente secundaria no publica por tiempo?
Solo lee: no modifica nada.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import collections
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

RAIZ = str(BASE_DIR / "informes" / "auditorias")

# Cobertura por periodo: cuantos partidos tienen valor en cada periodo
cobertura: dict[str, dict[str, int]] = {}
faltantes_por_periodo: dict[str, collections.Counter] = {
    "FT": collections.Counter(), "1T": collections.Counter(), "2T": collections.Counter()}
n_partidos = 0
equipos: dict[str, dict[str, dict[str, int]]] = {}

for ruta in sorted(glob.glob(os.path.join(RAIZ, "**", "auditoria_*.json"),
                             recursive=True)):
    try:
        d = json.load(open(ruta, encoding="utf-8"))
    except Exception:
        continue
    nombre = (d.get("equipo") or {}).get("nombre") or "?"
    equipos.setdefault(nombre, {})
    for p in d.get("partidos", []):
        n_partidos += 1
        for periodo in ("FT", "1T", "2T"):
            bloque = (p.get("periodos") or {}).get(periodo) or {}
            filas = bloque.get("filas") or []
            if not filas:
                faltantes_por_periodo[periodo]["(periodo ausente)"] += 1
                continue
            for f in filas:
                clave = f["clave"]
                cobertura.setdefault(clave, {"FT": 0, "1T": 0, "2T": 0})
                tiene = f.get("equipo") is not None or f.get("rival") is not None
                if tiene:
                    cobertura[clave][periodo] += 1
                    equipos[nombre].setdefault(clave, {"FT": 0, "1T": 0, "2T": 0})
                    equipos[nombre][clave][periodo] += 1
                else:
                    faltantes_por_periodo[periodo][clave] += 1

print("=" * 104)
print(f"COBERTURA POR PERIODO — {n_partidos} partidos auditados")
print("=" * 104)
print()
print(f"{'metrica':32} {'FT':>10} {'1T':>10} {'2T':>10}   diagnostico")
print("-" * 104)
for clave in sorted(cobertura, key=lambda k: -cobertura[k]["FT"]):
    c = cobertura[clave]
    pct = lambda n: f"{100.0 * n / max(1, n_partidos):.0f}%"
    diag = ""
    if c["FT"] == 0:
        diag = "AUSENTE EN TODO"
    elif c["1T"] == 0 and c["2T"] == 0:
        diag = "solo TOTAL de partido (sin desglose por tiempos)"
    elif c["1T"] < c["FT"] * 0.5:
        diag = "parcial en 1T/2T"
    print(f"{clave:32} {pct(c['FT']):>10} {pct(c['1T']):>10} {pct(c['2T']):>10}   {diag}")

print()
print("=" * 104)
print("METRICAS QUE FALTAN, POR PERIODO (en cuantos partidos)")
print("=" * 104)
for periodo in ("FT", "1T", "2T"):
    print(f"\n  --- {periodo} ---")
    if not faltantes_por_periodo[periodo]:
        print("     (sin faltantes)")
        continue
    for clave, n in faltantes_por_periodo[periodo].most_common(12):
        print(f"     {clave:34} falta en {n} partido(s)")

print()
print("=" * 104)
print("RESUMEN")
print("=" * 104)
tot_ft = sum(c["FT"] for c in cobertura.values())
tot_1t = sum(c["1T"] for c in cobertura.values())
tot_2t = sum(c["2T"] for c in cobertura.values())
print(f"  valores presentes: FT={tot_ft} | 1T={tot_1t} | 2T={tot_2t}")
print(f"  metricas con cobertura TOTAL en FT : "
      f"{sum(1 for c in cobertura.values() if c['FT'] >= n_partidos)}/{len(cobertura)}")
print(f"  metricas con cobertura TOTAL en 1T : "
      f"{sum(1 for c in cobertura.values() if c['1T'] >= n_partidos)}/{len(cobertura)}")
print(f"  metricas con cobertura TOTAL en 2T : "
      f"{sum(1 for c in cobertura.values() if c['2T'] >= n_partidos)}/{len(cobertura)}")
