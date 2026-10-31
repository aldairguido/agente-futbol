"""
Inspecciona los feeds REALES de Flashscore buscando el desglose por tiempos.
Muestra el contenido crudo para ver si hay etiquetas de 1T / 2T.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fuente_flashscore import ClienteFlashscore          # noqa: E402

MID = sys.argv[1] if len(sys.argv) > 1 else "2mAGw9Ti"
BASE = "https://global.flashscore.ninja/203/x/feed"

FEEDS = [
    ("df_st_1 (el que uso)", f"/df_st_1_{MID}"),
    ("df_dos_1", f"/df_dos_1_{MID}_"),
    ("df_dos_1 sin guion", f"/df_dos_1_{MID}"),
    ("df_sui_1 (incidentes)", f"/df_sui_1_{MID}"),
    ("df_mr_1", f"/df_mr_1_{MID}"),
    ("df_hi_1", f"/df_hi_1_{MID}"),
    ("dc_1", f"/dc_1_{MID}"),
]

c = ClienteFlashscore()
for nombre, ruta in FEEDS:
    try:
        r = c._get(BASE + ruta)
        cuerpo = getattr(r, "text", "") or ""
        print("=" * 100)
        print(f"{nombre}  ->  HTTP {r.status_code} | {len(cuerpo)} bytes")
        print("=" * 100)
        if not cuerpo or cuerpo == "0":
            print("   (vacio)")
            print()
            continue
        # Buscar indicios de periodo
        for patron, etq in ((r"1(?:st|ª|a)\s*(?:half|parte|tiempo)", "1ª PARTE"),
                            (r"2(?:nd|ª|a)\s*(?:half|parte|tiempo)", "2ª PARTE"),
                            (r"\bHT\b", "HT"),
                            (r"half", "half"),
                            (r"period", "period")):
            m = re.findall(patron, cuerpo, re.IGNORECASE)
            if m:
                print(f"   !!! indicio {etq}: {len(m)} coincidencia(s)")
        # Mostrar etiquetas legibles
        etiquetas = re.findall(r"SG÷([^¬~]{2,60})", cuerpo)
        if etiquetas:
            print(f"   etiquetas SG ({len(etiquetas)}):")
            for e in etiquetas[:18]:
                print(f"      {e}")
        else:
            # Volcar el inicio para inspeccion visual
            limpio = cuerpo.replace("¬", " | ").replace("~", "\n")
            print("   primeras lineas:")
            for linea in limpio.split("\n")[:12]:
                print(f"      {linea[:96]}")
        print()
    except Exception as e:
        print(f"{nombre}: ERROR {type(e).__name__}: {str(e)[:90]}")
        print()
