"""
diagnostico_fix.py — extrae outerHTML de un event-row y el JSON de view/v1
para corregir los selectores y el parser de betsson.py
"""
from playwright.sync_api import sync_playwright, Response
from playwright_stealth.stealth import Stealth
import json, time, pathlib

stealth   = Stealth()
view_raw  = []

def on_resp(resp: Response):
    try:
        if "widgets/view" in resp.url and resp.ok and "json" in resp.headers.get("content-type",""):
            body = resp.body()
            if body:
                view_raw.append(json.loads(body))
    except Exception:
        pass

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False, slow_mo=60)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        locale="es-PE", viewport={"width":1280,"height":900}
    )
    page = ctx.new_page()
    stealth.apply_stealth_sync(page)
    page.on("response", on_resp)

    page.goto("https://www.betsson.pe/es/apuestas-deportivas/futbol",
              wait_until="networkidle", timeout=40000)
    time.sleep(3)

    # Clic en tab Mañana
    for label in ("Mañana","Tomorrow"):
        try:
            btn = page.query_selector(f"button:has-text('{label}'), [class*='tab']:has-text('{label}')")
            if btn:
                btn.click()
                time.sleep(2)
                break
        except Exception:
            pass

    time.sleep(2)

    # 1. outerHTML del primer event-row con contenido
    rows = page.query_selector_all("[class*='event-row']")
    print(f"Total event-rows: {len(rows)}")
    for i, row in enumerate(rows[:5]):
        txt = row.inner_text().strip()
        if not txt or len(txt) < 10:
            continue
        outer = row.evaluate("el => el.outerHTML")
        print(f"\n--- ROW {i+1} outerHTML (primeros 1500 chars) ---")
        print(outer[:1500])

        # Atributos del elemento raiz
        attrs = row.evaluate("""el => {
            let r={};
            for(let a of el.attributes){ r[a.name]=a.value; }
            return r;
        }""")
        print(f"\nAtributos: {attrs}")
        break  # solo el primero con contenido

    # 2. Estructura del primer widget de view/v1
    if view_raw:
        data = view_raw[0]
        widgets = data.get("data", {}).get("widgets", [])
        print(f"\n--- VIEW API: {len(widgets)} widgets ---")
        for w in widgets[:3]:
            print(f"\nWidget key: {w.get('key')}")
            inner = w.get("data", {}).get("data", {})
            print(f"  fixtureGroupings type: {type(inner.get('fixtureGroupings'))}")
            print(f"  items count: {len(inner.get('items',[]))}")
            items = inner.get("items", [])
            for item in items[:3]:
                label = item.get("label","")
                sub   = item.get("items", [])
                print(f"    item label='{label}', sub-items={len(sub)}")
                if sub:
                    first = sub[0]
                    print(f"      first sub keys: {list(first.keys())[:10]}")
                    print(f"      first sub preview: {str(first)[:300]}")
    else:
        print("\nView API no interceptada")

    # 3. Buscar atributo con eventId en los rows
    print("\n--- BUSCANDO eventId en atributos DOM ---")
    for i, row in enumerate(rows[:20]):
        attrs = row.evaluate("""el => {
            let r={};
            for(let a of el.attributes){ r[a.name]=a.value; }
            return r;
        }""")
        event_attrs = {k:v for k,v in attrs.items() if 'event' in k.lower() or 'fixture' in k.lower() or 'id' in k.lower()}
        if event_attrs:
            print(f"  row {i}: {event_attrs}")

    # Ruta relativa a este archivo: funciona desde cualquier unidad
    pathlib.Path(pathlib.Path(__file__).resolve().parent / "view_raw.json").write_text(
        json.dumps(view_raw[:2], ensure_ascii=False, indent=2)[:300000],
        encoding="utf-8"
    )
    print("\nGuardado view_raw.json")
    browser.close()
