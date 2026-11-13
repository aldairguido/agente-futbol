"""
Tests de Bloque 1 (Ronda 5+6):
Coherencia multi-superficie, encabezados Flashscore, limite cuota justa >= 1.25 en Top 5,
etiquetas honestas sin cuotas, leyenda 'red de factores con pesos fijos' y H2H informativo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

import cuadro_especialista as ce
import fuente_flashscore as ffs
import fuentes_xg as fxg
import motor_inferencia as mi
import motor_mercados as mm
import motor_patrones as mp
import servicio_mercados as sm


FIXTURE_R5 = Path(__file__).parent / "fixtures" / "ronda5_real_fixtures.json"
FIXTURE_LN = Path(__file__).parent / "fixtures" / "londrina_nautico_fixtures.json"


def _cargar_r5() -> dict:
    return json.loads(FIXTURE_R5.read_text(encoding="utf-8"))["docs"]


def _cargar_ln() -> dict:
    return json.loads(FIXTURE_LN.read_text(encoding="utf-8"))


def test_1_1_formula_unica_p13_empates_y_lambda_2t_y_etiqueta_muestra():
    # 1.1.a: P13 respeta 'goles_evitados' explicito cuando existe, o deriva xGOT_enf - Goles_c si es None
    d_explicit = {"xgot_enfrentado": 1.96, "goles_contra": 1.93, "goles_evitados": -0.038}
    p_exp = mp.calcular_55_patrones_equipo(d_explicit)
    assert p_exp["P13_Portero_Goles_Evitados"] == pytest.approx(-0.04, abs=0.001)

    d_fallback = {"xgot_enfrentado": 1.96, "goles_contra": 1.93, "goles_evitados": None}
    p_fb = mp.calcular_55_patrones_equipo(d_fallback)
    assert p_fb["P13_Portero_Goles_Evitados"] == pytest.approx(0.03, abs=0.001)

    # 1.1.b: Empate tecnico en P13 (<0.015 GeV) y P39 (<0.5 pp) en tarjetas del Super-Cuadro y alertas
    mu_empate = {
        "xgot_enfrentado": 1.2,
        "goles_contra": 1.54,
        "goles_evitados": -0.34,
        "pases_largos_completados": 55.6,
        "pases_largos": 100.0,
    }
    tarj = ce._construir_4_alertas_motor_55(
        {},
        {},
        None,
        "Turquía",
        "Bélgica",
        1.0,
        1.5,
        25,
        25,
        50,
        50,
        50,
        res_red={"mu_local_casa": mu_empate, "mu_visita_fuera": mu_empate},
    )
    t13 = next(t for t in tarj if "13" in t["codigo"])
    t39 = next(t for t in tarj if "39" in t["codigo"])
    assert "Empate técnico (sin ventaja)" in t13["detalle"]
    assert "Empate técnico (sin ventaja)" in t39["detalle"]

    _, _, _, al13 = mi.aplicar_reglas_matchup_55(
        {"P13_Portero_Goles_Evitados": -0.34},
        {"P13_Portero_Goles_Evitados": -0.34},
        0.35, 0.25, 0.40, "Turquía", "Bélgica",
    )
    assert any("Empate técnico" in a for a in al13 if "Patrón #13" in a)


@pytest.mark.parametrize(
    "clave_pair,eq_l,eq_v,comp,fecha",
    [
        (("turkey", "belgium"), "Turquía", "Bélgica", "UEFA Nations League", "2026-11-12"),
        (("italy", "france"), "Italia", "Francia", "UEFA Nations League", "2026-11-12"),
        (("londrina", "nautico"), "Londrina", "Náutico", "Brasileirão Série B", "2026-11-11"),
    ],
)
def test_1_2_y_1_3_y_1_4_y_1_7_y_1_8_coherencia_multi_superficie_reales(tmp_path, clave_pair, eq_l, eq_v, comp, fecha):
    if clave_pair[0] in ("turkey", "italy"):
        docs = _cargar_r5()
    else:
        docs = _cargar_ln()
    doc_l = docs[clave_pair[0]]
    doc_v = docs[clave_pair[1]]

    an = mm.analizar(doc_l, doc_v, eq_l, eq_v, competicion=comp, fecha_partido=fecha)
    top5, sec1, sec2 = sm.obtener_top5_y_seccion1_filtrados(an, metadatos=an.metadatos, umbral=0.70)

    # Regla 1.7: sin cuotas reales, ninguna jugada en Top 5 puede tener cuota justa < 1.25 (p > 0.80)
    for it in top5:
        if it.get("ev_pct") is None and it.get("ev") is None:
            assert float(it["probabilidad"]) <= 0.800001, f"{eq_l} vs {eq_v}: Top 5 violo cuota justa >= 1.25 -> {it}"
            assert float(it.get("cuota_justa", 9.99)) >= 1.25

    # Regla 1.8: sin cuotas reales, prohibido afirmar ALTO VALOR o ineficiencia de mercado
    assert "ALTO VALOR" not in an.mensaje
    if top5:
        assert "ALTA PROBABILIDAD DEL MODELO" in an.mensaje
        assert "e ineficiencia de mercado" not in str(top5[0].get("nota_calibracion") or "")

    # Regla 1.1: GeV / P13 identicos entre mu_doble_adn y patrones_55
    rec = an.motor.recursivo or {}
    p55_l = rec.get("patrones_55_local") or {}
    p55_v = rec.get("patrones_55_visita") or {}
    mu_l = rec.get("mu_local_casa") or {}
    mu_v = rec.get("mu_visita_fuera") or {}
    if mu_l.get("goles_evitados") is not None and p55_l.get("P13_Portero_Goles_Evitados") is not None:
        assert p55_l["P13_Portero_Goles_Evitados"] == pytest.approx(round(float(mu_l["goles_evitados"]), 2), abs=0.015)
    if mu_v.get("goles_evitados") is not None and p55_v.get("P13_Portero_Goles_Evitados") is not None:
        assert p55_v["P13_Portero_Goles_Evitados"] == pytest.approx(round(float(mu_v["goles_evitados"]), 2), abs=0.015)

    # Regla 1.1 & 1.4: SEC1 no mete pildoras (2T) bajo ambito sin '2T'
    sec1_fmt = ce._organizar_sec1_para_cuadro(an, sec1, eq_l, eq_v, top5_podio=top5)
    for cat in sec1_fmt:
        for pil in cat["pildoras"]:
            lbl = pil["lbl"]
            if "(2T)" in lbl and "Sin líneas" not in lbl:
                assert "2T" in cat["ambito"], f"{lbl} quedo bajo ambito incorrecto: {cat['ambito']}"
            if "(1T)" in lbl and "Sin líneas" not in lbl:
                assert "1T" in cat["ambito"], f"{lbl} quedo bajo ambito incorrecto: {cat['ambito']}"

    # Regla 1.1 & 1.3: Telegram XAI + Veredicto coherentes con lam 2T, tarjetas SEC1 y muestra real
    tg_xai = sm.generar_reporte_xai_telegram(an, doc_local=doc_l, doc_visitante=doc_v)
    assert "[15P general]" not in tg_xai
    lam_2t_sum = round(float(an.motor.tasas.lambda_local_2t) + float(an.motor.tasas.lambda_visitante_2t), 2)
    assert f"{lam_2t_sum:.2f} λ 2T proyectado" in tg_xai

    tarj_sec1 = next((c for c in sec1_fmt if "TARJETA" in c["categoria"]), None)
    tiene_tarj_valida = bool(tarj_sec1 and any("Sin líneas" not in p["lbl"] for p in tarj_sec1["pildoras"]))
    if tiene_tarj_valida:
        assert "Sin líneas de tarjetas validadas" not in tg_xai
    else:
        assert "Sin líneas de tarjetas validadas >= 70%" in tg_xai

    # Regla 1.5, 1.8, 1.9, 1.10: Generacion limpia de los 3 PNGs
    an.metadatos["h2h_reciente_ticket"] = sm.construir_resumen_h2h_ticket(
        doc_l, doc_v, eq_l, eq_v, fecha_objetivo=fecha
    )
    p_tkt = tmp_path / "ticket.png"
    p_c1 = tmp_path / "cuadro1.png"
    p_c2 = tmp_path / "cuadro2.png"
    assert sm.renderizar_ticket_mercados_png(an, metadatos=an.metadatos, umbral=0.70, destino=str(p_tkt))
    assert ce.generar_cuadro_desde_analisis(an, doc_l, doc_v, destino=str(p_c1))
    assert ce.generar_cuadro_auditoria_15_partidos_png(an, doc_local=doc_l, doc_visita=doc_v, destino=str(p_c2))
    for p_img in (p_tkt, p_c1, p_c2):
        assert p_img.exists() and p_img.stat().st_size > 10_000


def test_1_3_veredicto_tarjetas_lee_pildoras_validadas_sec1():
    sec1_con_tarj = [
        {
            "categoria": "TARJETAS",
            "ambito": "Tiempo Completo (FT)",
            "pildoras": [{"lbl": "-4.5 Tarj.", "pct": "76%", "prob": 0.76}],
        }
    ]
    v_con = mi.generar_veredicto_tactico_partido(
        "Italia",
        "Francia",
        {"posesion_pct": 42.0, "faltas": 11.2, "P39_Estilo_Directo": 0.45, "xg_2t": 0.9, "corners": 4.2},
        {"posesion_pct": 58.0, "faltas": 10.1, "P39_Estilo_Directo": 0.48, "xg_2t": 1.0, "corners": 5.1},
        {"local": 0.30, "empate": 0.25, "visita": 0.45},
        {}, {}, {}, {},
        selecciones_sec1=sec1_con_tarj,
    )
    assert "-4.5 Tarj." in v_con and "Sin líneas de tarjetas validadas" not in v_con

    sec1_sin_tarj = [
        {
            "categoria": "TARJETAS",
            "ambito": "Tiempo Completo (FT)",
            "pildoras": [{"lbl": "Sin líneas validadas >= 70%", "pct": "-", "prob": 0.0}],
        }
    ]
    v_sin = mi.generar_veredicto_tactico_partido(
        "Italia",
        "Francia",
        {"posesion_pct": 42.0, "faltas": 11.2, "P39_Estilo_Directo": 0.45, "xg_2t": 0.9, "corners": 4.2},
        {"posesion_pct": 58.0, "faltas": 10.1, "P39_Estilo_Directo": 0.48, "xg_2t": 1.0, "corners": 5.1},
        {"local": 0.30, "empate": 0.25, "visita": 0.45},
        {}, {}, {}, {},
        selecciones_sec1=sec1_sin_tarj,
    )
    assert "Sin líneas de tarjetas validadas >= 70%" in v_sin


def test_1_4_sec1_incluye_todos_los_ambitos_incluyendo_2t():
    sels = [
        {"mercado": "Total de goles", "evento": "under_4.5", "etiqueta": "menos de 4.5 goles", "periodo": "FT", "linea": 4.5, "probabilidad": 0.88, "estado": "mantiene"},
        {"mercado": "Total de goles", "evento": "under_1.5", "etiqueta": "menos de 1.5 goles", "periodo": "1T", "linea": 1.5, "probabilidad": 0.78, "estado": "mantiene"},
        {"mercado": "Total de goles", "evento": "over_0.5", "etiqueta": "más de 0.5 goles", "periodo": "2T", "linea": 0.5, "probabilidad": 0.85, "estado": "mantiene"},
    ]
    org = ce._organizar_sec1_para_cuadro(None, sels, "Italia", "Francia")
    goles_cat = next(c for c in org if c["categoria"] == "GOLES PARTIDO")
    assert "Tiempo Completo (FT)" in goles_cat["ambito"]
    assert "1er Tiempo (1T)" in goles_cat["ambito"]
    assert "2º Tiempo (2T)" in goles_cat["ambito"]


def test_1_5_sin_truncado_18_ni_58_y_render_sin_colision(tmp_path):
    partido_largo = {
        "fecha": "2026-10-12",
        "torneo": "UEFA Nations League A Grp. 1",
        "equipo": "Bosnia and Herzegovina",
        "rival": "Northern Ireland Republic",
        "condicion": "casa",
        "marcador": {"local": 2, "visitante": 1},
    }
    linea = ce.formatear_linea_partido_15(1, partido_largo)
    assert "UEFA Nations League A Grp. 1" in linea
    assert "Bosnia and Herzegovina" in linea
    assert "Northern Ireland Republic" in linea

    docs = _cargar_r5()
    an = mm.analizar(docs["turkey"], docs["belgium"], "Turquía", "Bélgica", competicion="Liga Personalizada", fecha_partido="2026-11-12")
    an.metadatos["advertencia_liga"] = "Declarada: Liga Inventada Muy Larga De Prueba | Detectada: UEFA Nations League A"
    p_c2 = tmp_path / "cuadro2_adv.png"
    ce.generar_cuadro_auditoria_15_partidos_png(an, doc_local=docs["turkey"], doc_visita=docs["belgium"], destino=str(p_c2))
    assert p_c2.exists() and Image.open(p_c2).size[0] > 1000


def test_1_6_flashscore_header_league_amistosos_region_y_backfill():
    # Region geografica no equivale a competicion; competicion equivalente si
    assert fxg._es_region_geografica("Europe") is True
    assert fxg._es_region_geografica("World") is True
    assert fxg._es_region_geografica("UEFA Nations League") is False
    assert sm.son_ligas_equivalentes("UEFA Nations League", "Europe") is False
    assert sm.son_ligas_equivalentes("Copa América", "South America") is False
    assert sm.son_ligas_equivalentes("UEFA Nations League", "Nations League - League A") is True

    # Parser de filas HTML de Flashscore leyendo headerLeague__title y rechazando Friendly International
    class _FakeNode:
        def __init__(self, text="", cls="", attrs=None, children=None):
            self._text = text
            self._cls = cls
            self._attrs = attrs or {}
            self._children = children or {}

        def inner_text(self):
            return self._text

        def get_attribute(self, k):
            if k == "class":
                return self._cls
            return self._attrs.get(k)

        def query_selector(self, sel):
            return self._children.get(sel)

    class _FakeLocator:
        def __init__(self):
            self.first = self

        def is_visible(self, timeout=0):
            return False

        def count(self):
            return 0

        def click(self, *args, **kwargs):
            pass

    class _FakePage:
        def __init__(self, nodes, torneo_map):
            self._nodes = nodes
            self._torneo_map = torneo_map

        def goto(self, *args, **kwargs):
            pass

        def wait_for_selector(self, *args, **kwargs):
            pass

        def locator(self, sel):
            return _FakeLocator()

        def query_selector_all(self, sel):
            return self._nodes

        def wait_for_timeout(self, ms):
            pass

        def evaluate(self, js, *args):
            return self._torneo_map

        def close(self):
            pass

    class _FakeContext:
        def __init__(self, page):
            self._page = page

        def new_page(self):
            return self._page

    def _row(mid: str, dt: str, home: str, away: str, sh: str, sa: str) -> _FakeNode:
        return _FakeNode(
            text=f"{dt}\n{home}\n{away}\n{sh}\n{sa}",
            cls="event__match event__match--static",
            attrs={"id": f"g_1_{mid}"},
            children={
                "a.eventRowLink": _FakeNode(attrs={"href": f"/partido/futbol/{home}/{away}/?mid={mid}"}),
                "a[href]": _FakeNode(attrs={"href": f"/partido/futbol/{home}/{away}/?mid={mid}"}),
                "a[href*='mid=']": _FakeNode(attrs={"href": f"/partido/futbol/{home}/{away}/?mid={mid}"}),
            },
        )

    nodes = [_row("AM1", "10.10. 18:00", "Turkey", "Albania", "2", "0")]
    torneo_map = {"AM1": "EUROPE: Friendly International"}
    for idx in range(16):
        mid = f"OF{idx}"
        nodes.append(_row(mid, f"{15 - (idx % 10):02d}.09. 18:00", "Turkey", f"Rival{idx}", "2", "1"))
        torneo_map[mid] = "EUROPE: UEFA Nations League - League A"

    extractor = ffs.ListadoEquipoFlashscore(max_partidos=20)
    cand = {"id": "QeijuHo5", "url": "turkey", "nombre": "Turkey"}
    listado = extractor._leer_listado(cand, contexto=_FakeContext(_FakePage(nodes, torneo_map)))
    assert listado[0]["id"] == "AM1"
    assert "Friendly International" in listado[0]["torneo"]
    oficiales = [r for r in listado if not fxg._es_torneo_amistoso(r["torneo"])]
    assert len(oficiales) == 16
    assert all(r["id"] != "AM1" for r in oficiales)
    assert all("Nations League" in r["torneo"] for r in oficiales)


def test_1_7_regla_cuota_justa_min_125_y_excepcion_cuota_real_ev():
    # Todos los mercados >= 0.8001 sin cuota real quedan excluidos del Top 5
    sels_triviales = [
        {"mercado": "Total de goles", "evento": "under_4.5", "etiqueta": "menos de 4.5 goles", "periodo": "FT", "linea": 4.5, "probabilidad": 0.91, "estado": "mantiene"},
        {"mercado": "Doble oportunidad", "evento": "dc_x2", "etiqueta": "X2", "periodo": "FT", "probabilidad": 0.84, "estado": "mantiene"},
        {"mercado": "Sin empate", "evento": "dnb_2", "etiqueta": "Bélgica DNB", "periodo": "FT", "probabilidad": 0.83, "estado": "mantiene"},
        {"mercado": "Total de goles", "evento": "gol_2t", "etiqueta": "Al menos un gol en el 2T", "periodo": "2T", "linea": 0.5, "probabilidad": 0.85, "estado": "mantiene"},
        {"mercado": "Total de tarjetas", "evento": "amarillas_under_5.5", "etiqueta": "menos de 5.5 tarjetas", "periodo": "FT", "linea": 5.5, "probabilidad": 0.89, "estado": "mantiene"},
        {"mercado": "Total de goles", "evento": "under_2.5", "etiqueta": "menos de 2.5 goles", "periodo": "FT", "linea": 2.5, "probabilidad": 0.78, "estado": "mantiene"},
    ]
    top5 = mm.seleccionar_top_5_jugadas_recomendadas(sels_triviales, motor=None, eq_l="Turquía", eq_v="Bélgica")
    assert len(top5) == 1
    assert top5[0]["probabilidad"] == pytest.approx(0.78, abs=1e-4)

    # Con cuota real que genera EV positivo (>= +3%), si puede entrar aunque p > 0.80
    sels_con_ev = [dict(sels_triviales[1], cuota_real=1.30)] + sels_triviales[5:]
    top5_ev = mm.seleccionar_top_5_jugadas_recomendadas(sels_con_ev, motor=None, eq_l="Turquía", eq_v="Bélgica")
    assert any(it.get("ev_pct") is not None and it["ev_pct"] > 0 for it in top5_ev)


def test_1_9_y_1_10_leyenda_red_pesos_fijos_y_h2h_sin_alterar_matriz(tmp_path):
    docs = _cargar_r5()
    doc_it = docs["italy"]
    doc_fr = docs["france"]

    h2h_txt = sm.construir_resumen_h2h_ticket(doc_it, doc_fr, "Italia", "Francia", fecha_objetivo="2026-11-12")
    assert "Francia 1-1 Italia (02/10/2026)" in h2h_txt

    an = mm.analizar(doc_it, doc_fr, "Italia", "Francia", competicion="UEFA Nations League", fecha_partido="2026-11-12")
    mat_antes = json.dumps(an.motor.matriz_ft)
    an.metadatos["h2h_reciente_ticket"] = h2h_txt
    p_tkt = tmp_path / "ticket_if.png"
    sm.renderizar_ticket_mercados_png(an, metadatos=an.metadatos, umbral=0.70, destino=str(p_tkt))
    assert p_tkt.exists()
    assert json.dumps(an.motor.matriz_ft) == mat_antes

    # Verificar cadenas textuales en codigo fuente renderizado para 1.9
    src_sm = Path("servicio_mercados.py").read_text(encoding="utf-8")
    src_ce = Path("cuadro_especialista.py").read_text(encoding="utf-8")
    assert "red de factores con pesos fijos" in src_sm
    assert "red de factores con pesos fijos" in src_ce
