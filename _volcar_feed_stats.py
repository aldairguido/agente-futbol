"""
Vuelca la estructura CRUDA del feed de estadisticas de Flashscore para ver si el
payload trae varios bloques (partido / 1er tiempo / 2do tiempo).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fuente_flashscore import ClienteFlashscore          # noqa: E402

MID = sys.argv[1] if len(sys.argv) > 1 else "2mAGw9Ti"
BASE = "https://global.flashscore.ninja/203/x/feed"

c = ClienteFlashscore()
r = c._get(f"{BASE}/df_st_1_{MID}")
cuerpo = getattr(r, "text", "") or ""
print(f"HTTP {r.status_code} | {len(cuerpo)} bytes")
print()

# El formato es: bloques separados por ~ ; dentro, pares clave÷valor separados por ¬
bloques = [b for b in cuerpo.split("~") if b.strip()]
print(f"BLOQUES (separados por ~): {len(bloques)}")
print()

for i, bloque in enumerate(bloques):
    pares = [p for p in bloque.split("¬") if p.strip()]
    claves = []
    for par in pares:
        if "÷" in par:
            claves.append(par.split("÷", 1)[0])
    print(f"--- BLOQUE {i} ({len(pares)} pares) ---")
    print(f"    claves: {claves[:26]}")
    # Mostrar los primeros pares completos para ver la forma
    for par in pares[:8]:
        print(f"       {par[:110]}")
    print()

# Buscar explicitamente indicios de periodo en TODO el payload
print("=" * 90)
print("BUSQUEDA DE PERIODOS EN EL PAYLOAD COMPLETO")
print("=" * 90)
for patron in ("1er Tiempo", "2º Tiempo", "1T", "2T", "Primera parte", "Segunda parte",
               "AC÷", "MT÷", "period"):
    n = cuerpo.count(patron)
    if n:
        print(f"   {patron!r}: {n} aparicion(es)")
        idx = cuerpo.find(patron)
        print(f"      contexto: ...{cuerpo[max(0,idx-90):idx+90]!r}...")
print()
print("primeros 400 caracteres del payload:")
print(repr(cuerpo[:400]))
