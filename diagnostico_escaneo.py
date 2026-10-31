"""
DIAGNOSTICO DEL ESCANEO — por que Real Madrid se queda en 9 de 15.

Ejecutar:  python diagnostico_escaneo.py "Real Madrid"
           python diagnostico_escaneo.py "Vasco da Gama"   (control: este da 15)

No modifica nada: solo lee y reporta.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

NOMBRE = sys.argv[1] if len(sys.argv) > 1 else "Real Madrid"
SALIDA = str(BASE_DIR / "diag_resultado.txt")

lineas: list[str] = []


def w(t: str = "") -> None:
    lineas.append(str(t))
    print(t)


w("=" * 88)
w(f"DIAGNOSTICO DEL ESCANEO — {NOMBRE}")
w("=" * 88)

try:
    from fuentes_xg import (
        FuenteFotMob, es_competicion_amistosa, obtener_fuente,
    )
except Exception as e:
    w(f"FALLO importando fuentes_xg: {type(e).__name__}: {e}")
    w(traceback.format_exc())
    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    raise SystemExit(1)

fuente = None
try:
    fuente = obtener_fuente("fotmob", headless=True, verbose=False)
    fuente.abrir()
    w("[OK] navegador abierto")

    equipo = fuente.resolver_equipo(NOMBRE)
    w(f"[equipo] id={equipo.get('id')} nombre={equipo.get('nombre')!r} "
      f"liga={equipo.get('liga')!r} id_liga={equipo.get('id_liga')!r}")
    tid = str(equipo.get("id"))

    w("")
    w("--- 1) QUE DEVUELVE _ligas_del_equipo (la clave del problema) ---")
    try:
        ligas = fuente._ligas_del_equipo(tid)
        w(f"   ligas detectadas: {len(ligas)}")
        for k, v in ligas.items():
            w(f"      leagueId={k}  nombre={v!r}")
        if not ligas:
            w("   >>> VACIO: por esto la cola queda sin elementos <<<")
    except Exception as e:
        w(f"   ERROR: {type(e).__name__}: {e}")
        ligas = {}

    w("")
    w("--- 2) QUE TRAEN LOS FIXTURES DE /teams (crudo) ---")
    try:
        datos = fuente._api(f"/teams?id={tid}&ccode3=PER&lang={fuente.idioma}")
        fx = (((datos or {}).get("fixtures") or {}).get("allFixtures") or {}).get("fixtures") or []
        finalizados = [f for f in fx if (f.get("status") or {}).get("finished")]
        w(f"   fixtures en la ventana: {len(fx)} | finalizados: {len(finalizados)}")
        oficiales = 0
        amistosos = 0
        sin_leagueid = 0
        for f in finalizados:
            t = f.get("tournament") or {}
            nombre_t = str(t.get("name") or "")
            lid = t.get("leagueId") or t.get("id")
            if not lid:
                sin_leagueid += 1
            if es_competicion_amistosa(nombre_t):
                amistosos += 1
            else:
                oficiales += 1
        w(f"   oficiales: {oficiales} | amistosos: {amistosos}")
        w(f"   finalizados SIN leagueId ni id: {sin_leagueid}")
        w("   muestra de torneos (primeros 6):")
        for f in finalizados[:6]:
            t = f.get("tournament") or {}
            w(f"      tournament={json.dumps(t, ensure_ascii=False)[:150]}")
    except Exception as e:
        w(f"   ERROR: {type(e).__name__}: {e}")

    w("")
    w("--- 3) TEMPORADAS DE CADA LIGA ---")
    for k, v in (ligas or {}).items():
        try:
            temps = fuente._temporadas_de_liga(k)
            w(f"   {k} ({v}): {len(temps)} temporadas -> {temps[:4]}")
        except Exception as e:
            w(f"   {k}: ERROR {type(e).__name__}: {e}")

    w("")
    w("--- 4) ESCANEO REAL (objetivo 15) ---")
    res = fuente.escanear_oficiales(equipo, objetivo=15)
    w(f"   oficiales obtenidos : {len(res.oficiales)}")
    w(f"   paginas_escaneadas  : {res.paginas_escaneadas}")
    w(f"   finalizados_totales : {res.finalizados_totales}")
    w(f"   amistosos_excluidos : {res.amistosos_excluidos}")
    w(f"   fuente_agotada      : {res.fuente_agotada}")
    w(f"   motivo_parada       : {res.motivo_parada}")
    w("")
    w("   cola de ligas usada por el escaneo:")
    cola = getattr(fuente, "_cola_por_equipo", {}).get(tid, [])
    w(f"      elementos en la cola: {len(cola)}")
    for c in cola[:8]:
        w(f"         {c}")
    if not cola:
        w("      >>> COLA VACIA: el escaneo no puede pedir paginas siguientes <<<")
    w("")
    w("   partidos obtenidos:")
    for p in res.oficiales:
        w(f"      {p.fecha} | {p.local} {p.goles_local}-{p.goles_visitante} "
          f"{p.visitante} | {p.torneo}")

except Exception as e:
    w(f"FALLO: {type(e).__name__}: {e}")
    w(traceback.format_exc())
finally:
    try:
        if fuente is not None:
            fuente.cerrar()
    except Exception:
        pass

w("")
w("=" * 88)
w("FIN DEL DIAGNOSTICO")
w("=" * 88)

with open(SALIDA, "w", encoding="utf-8") as f:
    f.write("\n".join(lineas))
print(f"\n>>> Guardado tambien en: {SALIDA}")
