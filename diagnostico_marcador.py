"""
DIAGNOSTICO DEL MARCADOR — verifica DE DONDE se pueden sacar los goles que salen
None-None en los partidos de /api/leagues (temporada anterior).

Ejecutar:  python diagnostico_marcador.py "Real Madrid"
Solo lee: no modifica nada.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

NOMBRE = sys.argv[1] if len(sys.argv) > 1 else "Real Madrid"
SALIDA = str(BASE_DIR / "diag_marcador.txt")

lineas: list[str] = []


def w(t: str = "") -> None:
    lineas.append(str(t))
    print(t)


w("=" * 88)
w(f"DIAGNOSTICO DEL MARCADOR — {NOMBRE}")
w("=" * 88)

fuente = None
try:
    from fuentes_xg import obtener_fuente

    fuente = obtener_fuente("fotmob", headless=True, verbose=False)
    fuente.abrir()
    w("[OK] navegador abierto")

    equipo = fuente.resolver_equipo(NOMBRE)
    tid = str(equipo.get("id"))
    w(f"[equipo] {equipo.get('nombre')} id={tid} liga={equipo.get('liga')}")

    # Se busca una liga con temporada anterior, que es donde fallan los goles
    ligas = fuente._ligas_del_equipo(tid)
    w(f"[ligas] {ligas}")

    w("")
    w("--- PARTIDOS DE UNA TEMPORADA ANTERIOR VIA /api/leagues ---")
    for lid, nombre_liga in list(ligas.items())[:2]:
        temporadas = fuente._temporadas_de_liga(lid)
        if len(temporadas) < 2:
            continue
        temporada = temporadas[1]          # la anterior
        w("")
        w(f"### liga {lid} ({nombre_liga}) temporada {temporada}")
        url = f"/leagues?id={lid}&lang={fuente.idioma}&season={temporada.replace('/', '%2F')}"
        datos = fuente._api(url)
        if not isinstance(datos, dict):
            w("   respuesta no valida")
            continue
        fixtures = datos.get("fixtures") or {}
        am = fixtures.get("allMatches") or (datos.get("matches") or {}).get("allMatches") or []
        w(f"   partidos en la liga: {len(am)}")

        n_home_score = n_scorestr = n_ambos = 0
        mostrados = 0
        for f in am:
            estado = f.get("status") or {}
            if not estado.get("finished"):
                continue
            home = f.get("home") or {}
            away = f.get("away") or {}
            hs, as_ = home.get("score"), away.get("score")
            ss = estado.get("scoreStr")
            if isinstance(hs, int):
                n_home_score += 1
            if ss:
                n_scorestr += 1
            if (hs is None or as_ is None) and ss:
                n_ambos += 1
            if mostrados < 5:
                w(f"      {estado.get('utcTime', '')[:10]} | {home.get('name')} vs "
                  f"{away.get('name')}")
                w(f"         home.score={hs!r} away.score={as_!r} "
                  f"scoreStr={ss!r} reason={estado.get('reason')!r}")
                w(f"         CLAVES de status: {sorted(estado.keys())}")
                mostrados += 1
        w(f"   RESUMEN: con home.score={n_home_score} | con scoreStr={n_scorestr} "
          f"| None-None pero con scoreStr={n_ambos}")
        if n_ambos:
            w("   >>> EL MARCADOR SE PUEDE RECUPERAR DE scoreStr <<<")
        elif n_scorestr:
            w("   >>> scoreStr existe pero no cubre los casos None <<<")
        else:
            w("   >>> scoreStr NO viene: hay que buscar otra via <<<")

    w("")
    w("--- ¿EL HELPER DEL SCRIPT DE REPARACION SE USA? ---")
    import subprocess
    ruta = r"D:\agente_futbol\fuentes_xg.py"
    try:
        with open(ruta, encoding="utf-8") as f:
            codigo = f.read()
        w(f"   '_extraer_goles_fixture_fotmob' definido: "
          f"{'_extraer_goles_fixture_fotmob' in codigo}")
        w(f"   apariciones (1 = solo definicion, sin uso): "
          f"{codigo.count('_extraer_goles_fixture_fotmob')}")
    except Exception as e:
        w(f"   ERROR leyendo fuentes_xg.py: {e}")

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
with open(SALIDA, "w", encoding="utf-8") as f:
    f.write("\n".join(lineas))
print(f"\n>>> Guardado en: {SALIDA}")
