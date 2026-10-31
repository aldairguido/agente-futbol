"""
Descubre el feed REAL de estadisticas por periodo de Flashscore.

Abre la pagina de estadisticas de un partido con Playwright, cambia el selector de
periodo (1T / 2T / FT) y captura las peticiones a *.flashscore.ninja para ver que
endpoint sirve el desglose por tiempos.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fuente_flashscore import USER_AGENT          # noqa: E402
from playwright.sync_api import sync_playwright   # noqa: E402

MID = sys.argv[1] if len(sys.argv) > 1 else "2mAGw9Ti"
URL = f"https://www.flashscore.es/partido/{MID}/#/estadisticas-del-partido/0"

capturas: list[dict] = []

with sync_playwright() as p:
    nav = p.chromium.launch(headless=True)
    ctx = nav.new_context(locale="es-ES", user_agent=USER_AGENT)
    pag = ctx.new_page()

    def al_pedir(peticion):
        u = peticion.url
        if "flashscore" in u and ("feed" in u or ".ninja" in u):
            capturas.append({"url": u, "headers": dict(peticion.headers),
                             "tipo": peticion.resource_type})

    pag.on("request", al_pedir)
    print(f"abriendo {URL}")
    try:
        pag.goto(URL, timeout=60000, wait_until="domcontentloaded")
        pag.wait_for_timeout(6000)
        # Buscar el selector de periodo en la pagina
        selectores = pag.query_selector_all("[class*='statistics'], [class*='period'], "
                                            "[class*='filter'], [class*='tab']")
        print(f"elementos tipo selector/periodo: {len(selectores)}")
        textos = []
        for el in selectores[:25]:
            try:
                t = el.inner_text().strip().replace("\n", " | ")[:70]
                if t:
                    textos.append(t)
            except Exception:
                pass
        print("textos de posibles selectores:")
        for t in textos[:15]:
            print(f"   {t!r}")
        # Intentar pulsar la opcion de primer tiempo
        for etiqueta in ("1ª parte", "1ª Parte", "Primer tiempo", "1T", "1st half"):
            try:
                el = pag.query_selector(f"text={etiqueta}")
                if el:
                    print(f"pulsando: {etiqueta!r}")
                    el.click()
                    pag.wait_for_timeout(4000)
                    break
            except Exception:
                continue
        # Y la de segundo tiempo
        for etiqueta in ("2ª parte", "2ª Parte", "Segundo tiempo", "2T", "2nd half"):
            try:
                el = pag.query_selector(f"text={etiqueta}")
                if el:
                    print(f"pulsando: {etiqueta!r}")
                    el.click()
                    pag.wait_for_timeout(4000)
                    break
            except Exception:
                continue
        pag.wait_for_timeout(2000)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {str(e)[:160]}")
    ctx.close()
    nav.close()

print()
print("=" * 100)
print(f"PETICIONES AL FEED CAPTURADAS: {len(capturas)}")
print("=" * 100)
patrones = Counter()
for c in capturas:
    base = re.sub(r"[A-Za-z0-9]{6,}", "{X}", c["url"].split("?")[0])
    patrones[base] += 1
for pat, n in patrones.most_common(25):
    print(f"   {n:4}x  {pat[:120]}")

print()
print("URLs que contienen indicios de periodo (st, half, ht, period):")
vistas = set()
for c in capturas:
    u = c["url"]
    if u in vistas:
        continue
    vistas.add(u)
    if re.search(r"(st_|half|_ht|period|\d_\d)", u):
        print(f"   {u[:150]}")
        if "x-fsign" in c["headers"]:
            print(f"      x-fsign: {c['headers']['x-fsign']}")
