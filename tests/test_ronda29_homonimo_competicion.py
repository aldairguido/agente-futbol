# -*- coding: utf-8 -*-
"""
tests/test_ronda29_homonimo_competicion.py
───────────────────────────────────────────────────────────────────────────────
Pruebas de la Ronda 29 (corrección del guardián FALSO_HOMONIMO_EXTRANJERO):
  1. Bodø/Glimt–LASK, Galatasaray–Aston Villa y Sabah FK–Barcelona ya no abstienen
     por el guardián y producen pronóstico completo.
  2. Casos de homónimo real siguen absteniéndose, incluido el de Villarreal y un
     "Barcelona" de otro país con historial incoherente.
  3. Invariancia en partidos domésticos frente a 477525a (λ, probabilidades, Top 5
     y Sección 1/2 idénticos).
  4. Bandera auxiliar `usar_guardian_homonimo_por_competicion` (default True).
  5. Ningún nombre de liga ambiguo asigna país sin otro dato que lo confirme; si
     sigue ambiguo, abstiene con PAIS_AMBIGUO (nunca FALSO_HOMONIMO).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auditoria_historica import (
    resolver_pais_equipo_auditado,
    validar_coherencia_competitiva_local_visita,
)
import decision_cobertura as dc
from fuentes_xg import _inferir_meta_rival_desde_texto, _pais_de_liga
import motor_mercados as mm
from resolvedor_entidades import (
    es_nombre_liga_ambiguo,
    normalizar_pais_iso,
    resolver,
    resolver_pais_desde_liga,
)
import servicio_mercados as sm


def _cargar_json_auditoria(rel_path: str) -> dict:
    return json.loads((ROOT / rel_path).read_text(encoding="utf-8"))


def _rejuvenecer_fechas(doc: dict, fecha_base: str = "2026-11-20") -> dict:
    """Ajusta las fechas de los 15 partidos para que sean frescas respecto a fecha_partido=2026-11-24."""
    clon = copy.deepcopy(doc)
    for idx, p in enumerate(clon.get("partidos") or []):
        if isinstance(p, dict):
            dia = max(1, 20 - idx)
            p["fecha"] = f"2026-11-{dia:02d}"
    return clon


def _construir_doc_lask_desde_plantilla(plantilla: dict) -> dict:
    """
    Construye el documento de auditoría de LASK (id=9977, id_liga=38, liga='Bundesliga' de Austria)
    con los mismos torneos que en producción (['Bundesliga', 'Champions League', 'Cup'])
    y rivales domésticos de la Bundesliga de Austria.
    """
    doc = _rejuvenecer_fechas(plantilla)
    doc["equipo"] = {
        "id": "9977",
        "nombre": "LASK",
        "nombre_solicitado": "LASK",
        "liga": "Bundesliga",
        "id_liga": 38,
        "pais_iso": "DEU",
        "alternativas": ["LASK Linz"],
        "variante_busqueda": "tabla_comun:LASK",
        "ambiguous": False,
    }
    torneos_rivales = [
        ("Bundesliga", "Hartberg"),
        ("Bundesliga", "Ried"),
        ("Champions League", "Slovan Bratislava"),
        ("Bundesliga", "WSG Tirol"),
        ("Cup", "Ried"),
        ("Bundesliga", "Salzburg"),
        ("Champions League", "Sporting CP"),
        ("Bundesliga", "Austria Lustenau"),
        ("Champions League", "Liverpool"),
        ("Bundesliga", "Rapid Wien"),
        ("Bundesliga", "Hartberg"),
        ("Bundesliga", "Sturm Graz"),
        ("Champions League", "AEK Athens"),
        ("Cup", "Deutschlandsberger SC"),
        ("Bundesliga", "Wolfsberger AC"),
    ]
    for idx, p in enumerate((doc.get("partidos") or [])[:15]):
        t_nom, r_nom = torneos_rivales[idx % len(torneos_rivales)]
        p["torneo"] = t_nom
        p["equipo"] = "LASK"
        p["rival"] = r_nom
    return doc


def _aislar_entorno_offline(monkeypatch) -> None:
    monkeypatch.setattr("banco_historico.guardar_en_banco_no_bloqueante", lambda *a, **k: None)
    monkeypatch.setattr(
        "cuadro_h2h_2024_2026.generar_cuadro_h2h_desde_analisis",
        lambda *a, **k: {"ruta": None, "n_duelos": 0, "estado": "Sin enfrentamientos directos en 2024–2026", "duelos": []},
    )
    monkeypatch.setattr(
        "fuente_flashscore.ClienteFlashscore.buscar_evento_programado_feed_diario",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "fuente_flashscore.ClienteFlashscore.extraer_cuotas",
        lambda *a, **k: {"local": None, "empate": None, "visitante": None},
    )


def test_r29_bandera_auxiliar_defecto_true_y_aislamiento():
    assert dc._BANDERAS_AUXILIARES_DEFECTO["usar_guardian_homonimo_por_competicion"] is True
    assert dc.bandera_activa("usar_guardian_homonimo_por_competicion") is True
    with dc.banderas_comportamiento(activar_todas=True):
        assert dc.bandera_activa("usar_guardian_homonimo_por_competicion") is True
    with dc.banderas_comportamiento(usar_guardian_homonimo_por_competicion=False):
        assert dc.bandera_activa("usar_guardian_homonimo_por_competicion") is False


def test_r29_ningun_nombre_de_liga_ambiguo_asigna_pais_sin_confirmacion_y_abstiene_pais_ambiguo(monkeypatch, tmp_path):
    _aislar_entorno_offline(monkeypatch)
    ligas_ambiguas = [
        "Premier League",
        "Bundesliga",
        "1. Bundesliga",
        "2. Bundesliga",
        "Super League",
        "Superliga",
        "Primera División",
        "Segunda División",
        "Primera A",
        "Primera B",
        "División Profesional",
        "Serie A",
        "Serie B",
        "Serie C",
        "Ligue 1",
        "Ligue 2",
        "Liga 1",
        "Liga 2",
        "Primera Nacional",
        "Liga Nacional",
        "1. Liga",
        "2. Liga",
        "3. Liga",
        "Prva Liga",
        "First Division",
        "Premier Division",
        "National League",
        "Championship",
        "Premiership",
        "Cup",
        "Copa",
    ]
    for nombre_liga in ligas_ambiguas:
        assert es_nombre_liga_ambiguo(nombre_liga) is True, f"Debe ser ambigua: {nombre_liga}"
        assert normalizar_pais_iso(nombre_liga) == "", f"{nombre_liga} no debe asignar país sin confirmación"
        assert resolver_pais_desde_liga(nombre_liga) == "", f"{nombre_liga} no debe asignar país sin confirmación"
        assert _pais_de_liga(nombre_liga) == "", f"_pais_de_liga({nombre_liga!r}) no debe asignar país sin confirmación"
        meta_inf = _inferir_meta_rival_desde_texto(nombre_liga)
        if meta_inf is not None:
            assert not meta_inf.get("pais_iso"), f"_inferir_meta_rival_desde_texto({nombre_liga!r}) no debe asignar pais_iso"
            assert not meta_inf.get("pais"), f"_inferir_meta_rival_desde_texto({nombre_liga!r}) no debe asignar pais"

    # Con dato confirmatorio (id_liga o ccode de FotMob) sí resuelve al país exacto:
    assert resolver_pais_desde_liga("Premier League", id_liga=47) == "ENG"
    assert resolver_pais_desde_liga("Premier League", id_liga=262) == "AZE"
    assert resolver_pais_desde_liga("Bundesliga", id_liga=54) == "DEU"
    assert resolver_pais_desde_liga("Bundesliga", id_liga=38) == "AUT"
    assert resolver_pais_desde_liga("Super League", id_liga=69) == "CHE"
    assert resolver_pais_desde_liga("Super League", id_liga=135) == "GRC"
    assert resolver_pais_desde_liga("Primera División", ccode="URY") == "URY"
    assert resolver_pais_desde_liga("Primera División", id_liga=273) == "CHL"

    # Si un equipo trae únicamente nombre de liga ambiguo sin ccode, sin id_liga y sin rivales conocidos -> PAIS_AMBIGUO
    doc_amb_l = {
        "equipo": {"id": "999991", "nombre": "Club Ambiguo Norte", "liga": "Premier League"},
        "partidos": [{"fecha": f"2026-11-{20 - i:02d}", "torneo": "Premier League", "rival": f"Rival Genérico {i}"} for i in range(15)],
    }
    doc_amb_v = {
        "equipo": {"id": "999992", "nombre": "Club Ambiguo Sur", "liga": "Bundesliga"},
        "partidos": [{"fecha": f"2026-11-{20 - i:02d}", "torneo": "Bundesliga", "rival": f"Otro Genérico {i}"} for i in range(15)],
    }
    res_p_l = resolver_pais_equipo_auditado(doc_amb_l)
    assert res_p_l["pais_iso"] == ""
    assert res_p_l["ambiguo"] is True

    coh_amb = validar_coherencia_competitiva_local_visita(doc_amb_l, doc_amb_v, pais_iso="INT", liga_pref="Premier League")
    assert coh_amb["coherente"] is False
    assert coh_amb["estado"] == "PAIS_AMBIGUO"
    assert coh_amb["falso_homonimo"] is None

    monkeypatch.setattr(
        sm,
        "resolver_enfrentamiento",
        lambda *a, **k: {
            "equipo_local": "Club Ambiguo Norte",
            "equipo_visitante": "Club Ambiguo Sur",
            "doc_local": doc_amb_l,
            "doc_visitante": doc_amb_v,
            "contexto": {
                "competicion": "Premier League",
                "pais_iso": "",
                "fecha_partido": "2026-11-24",
                "coherencia_competitiva": dict(coh_amb),
            },
        },
    )
    prep_amb = sm.preparar_analisis("Club Ambiguo Norte", "Club Ambiguo Sur", directorio_salida=str(tmp_path))
    assert prep_amb["abstenido"] is True
    assert prep_amb["estado_cobertura"] == "pais_ambiguo"
    assert "PAIS_AMBIGUO" in prep_amb["motivo_abstencion"]
    assert prep_amb["estado_cobertura"] != "falso_homonimo_extranjero"


def test_r29_tres_partidos_champions_ya_no_abstienen_por_guardian_y_producen_pronostico(monkeypatch, tmp_path):
    _aislar_entorno_offline(monkeypatch)

    doc_bodo = _rejuvenecer_fechas(_cargar_json_auditoria("informes/auditorias/2026-11-04/auditoria_bod_glimt_2026-11-04_042147.json"))
    doc_lask = _construir_doc_lask_desde_plantilla(doc_bodo)
    doc_gala = _rejuvenecer_fechas(_cargar_json_auditoria("informes/auditorias/2026-11-04/auditoria_galatasaray_2026-11-04_041436.json"))
    doc_aston = _rejuvenecer_fechas(_cargar_json_auditoria("informes/auditorias/2026-11-04/auditoria_aston_villa_2026-11-04_043132.json"))
    doc_sabah = _rejuvenecer_fechas(_cargar_json_auditoria("informes/auditorias/2026-10-20/auditoria_sabah_fk_2026-10-20_031838.json"))
    doc_barca = _rejuvenecer_fechas(_cargar_json_auditoria("informes/auditorias/2026-11-23/auditoria_barcelona_2026-11-23_140609.json"))

    # Verificar resolución exacta de país propio (Sabah FK -> AZE, nunca ENG; LASK -> AUT, nunca DEU)
    assert resolver_pais_equipo_auditado(doc_bodo)["pais_iso"] == "NOR"
    assert resolver_pais_equipo_auditado(doc_lask)["pais_iso"] == "AUT"
    assert resolver_pais_equipo_auditado(doc_gala)["pais_iso"] == "TUR"
    assert resolver_pais_equipo_auditado(doc_aston)["pais_iso"] == "ENG"
    assert resolver_pais_equipo_auditado(doc_sabah)["pais_iso"] == "AZE"
    assert resolver_pais_equipo_auditado(doc_barca)["pais_iso"] == "ESP"

    tres_partidos = [
        ("Bodø/Glimt", "LASK", doc_bodo, doc_lask, "NOR", "AUT"),
        ("Galatasaray", "Aston Villa", doc_gala, doc_aston, "TUR", "ENG"),
        ("Sabah FK", "Barcelona", doc_sabah, doc_barca, "AZE", "ESP"),
    ]

    for eq_l, eq_v, dl, dv, iso_l_esp, iso_v_esp in tres_partidos:
        # 1) Con la bandera apagada (comportamiento 477525a): abstenía por FALSO HOMÓNIMO EXTRANJERO
        with dc.banderas_comportamiento(usar_guardian_homonimo_por_competicion=False):
            coh_antes = validar_coherencia_competitiva_local_visita(dl, dv, pais_iso="INT", liga_pref="Champions League")
            assert coh_antes["coherente"] is False
            assert coh_antes["estado"] == "FALSO HOMÓNIMO EXTRANJERO"

            out_sub_false = tmp_path / f"false_{eq_l}_{eq_v}".replace("/", "_")
            out_sub_false.mkdir(parents=True, exist_ok=True)
            monkeypatch.setattr(
                sm,
                "resolver_enfrentamiento",
                lambda *a, _el=eq_l, _ev=eq_v, _dl=dl, _dv=dv, _coh=coh_antes, **k: {
                    "equipo_local": _el,
                    "equipo_visitante": _ev,
                    "doc_local": _dl,
                    "doc_visitante": _dv,
                    "contexto": {
                        "competicion": "Champions League",
                        "pais_iso": "INT",
                        "condicion_local": "casa",
                        "condicion_visitante": "fuera",
                        "fecha_partido": "2026-11-25",
                        "fecha_corte": "2026-11-25",
                        "hora_inicio_partido": "2026-11-25T20:00:00Z",
                        "fixture_confirmado": True,
                        "coherencia_competitiva": dict(_coh),
                    },
                },
            )
            prep_false = sm.preparar_analisis(eq_l, eq_v, directorio_salida=str(out_sub_false))
            assert prep_false.get("abstenido") is True, f"{eq_l} vs {eq_v} con bandera=False debe volver a abstenerse"
            assert prep_false.get("estado_cobertura") == "falso_homonimo_extranjero"
            assert "FALSO HOMÓNIMO EXTRANJERO" in str(prep_false.get("motivo_abstencion") or "")

        # 2) Con la bandera encendida por defecto (Ronda 29): coherente=True, cada equipo validado en su propio país
        coh_despues = validar_coherencia_competitiva_local_visita(dl, dv, pais_iso="INT", liga_pref="Champions League")
        assert coh_despues["coherente"] is True, f"{eq_l} vs {eq_v} no debe abstener: {coh_despues}"
        assert coh_despues["estado"] == "OK"
        assert coh_despues["falso_homonimo"] is None
        assert coh_despues["pais_esperado_local"] == iso_l_esp
        assert coh_despues["pais_esperado_visita"] == iso_v_esp

        # 3) preparar_analisis completo produce pronóstico sin abstener por el guardián
        out_sub = tmp_path / f"{eq_l}_{eq_v}".replace("/", "_")
        out_sub.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            sm,
            "resolver_enfrentamiento",
            lambda *a, _el=eq_l, _ev=eq_v, _dl=dl, _dv=dv, _coh=coh_despues, **k: {
                "equipo_local": _el,
                "equipo_visitante": _ev,
                "doc_local": _dl,
                "doc_visitante": _dv,
                "contexto": {
                    "competicion": "Champions League",
                    "pais_iso": "INT",
                    "condicion_local": "casa",
                    "condicion_visitante": "fuera",
                    "fecha_partido": "2026-11-25",
                    "fecha_corte": "2026-11-25",
                    "hora_inicio_partido": "2026-11-25T20:00:00Z",
                    "fixture_confirmado": True,
                    "coherencia_competitiva": dict(_coh),
                },
            },
        )
        prep = sm.preparar_analisis(eq_l, eq_v, directorio_salida=str(out_sub))
        assert prep.get("abstenido") is not True, f"{eq_l} vs {eq_v} se abstuvo: {prep.get('motivo_abstencion')}"
        assert prep.get("estado_cobertura") != "falso_homonimo_extranjero"
        assert prep["analisis"].motor.tasas.lambda_local_ft > 0
        assert prep["analisis"].motor.tasas.lambda_visitante_ft > 0
        assert len(prep["analisis"].selecciones_filtradas) > 0


def test_r29_homonimos_reales_siguen_absteniendose_incluido_villarreal(monkeypatch, tmp_path):
    _aislar_entorno_offline(monkeypatch)

    doc_barca_esp = _rejuvenecer_fechas(_cargar_json_auditoria("informes/auditorias/2026-11-23/auditoria_barcelona_2026-11-23_140609.json"))
    doc_sabah_aze = _rejuvenecer_fechas(_cargar_json_auditoria("informes/auditorias/2026-10-20/auditoria_sabah_fk_2026-10-20_031838.json"))

    # 1) Caso Villarreal: homónimo extranjero de Villarreal (en liga extranjera con rivales extranjeros) contra Barcelona en LaLiga
    doc_villarreal_homonimo_ext = {
        "equipo": {"id": "777001", "nombre": "Villarreal", "liga": "LPF Apertura", "pais_iso": "PAN"},
        "partidos": [
            {"fecha": f"2026-11-{20 - i:02d}", "torneo": "LPF Apertura", "rival": "Tauro" if i % 2 == 0 else "Plaza Amador"}
            for i in range(15)
        ],
    }
    coh_vil_ext = validar_coherencia_competitiva_local_visita(
        doc_barca_esp, doc_villarreal_homonimo_ext, pais_iso="ESP", liga_pref="LaLiga"
    )
    assert coh_vil_ext["coherente"] is False
    assert coh_vil_ext["estado"] == "FALSO HOMÓNIMO EXTRANJERO"
    assert coh_vil_ext["falso_homonimo"] == "visita"

    monkeypatch.setattr(
        sm,
        "resolver_enfrentamiento",
        lambda *a, **k: {
            "equipo_local": "Barcelona",
            "equipo_visitante": "Villarreal",
            "doc_local": doc_barca_esp,
            "doc_visitante": doc_villarreal_homonimo_ext,
            "contexto": {"competicion": "LaLiga", "pais_iso": "ESP", "fecha_partido": "2026-11-24"},
        },
    )
    res_vil_ext = sm.preparar_analisis("Barcelona", "Villarreal", directorio_salida=str(tmp_path / "vil_ext"))
    assert res_vil_ext["abstenido"] is True
    assert res_vil_ext["estado_cobertura"] == "falso_homonimo_extranjero"

    # 2) Caso Villarreal legítimo [ESP] contra un homónimo extranjero de Barcelona (Barcelona SC [ECU] con historial de LigaPro)
    doc_villarreal_esp = copy.deepcopy(doc_barca_esp)
    doc_villarreal_esp["equipo"] = {"id": "10205", "nombre": "Villarreal", "liga": "LaLiga", "id_liga": 87, "pais_iso": "ESP"}
    doc_barcelona_ecu = {
        "equipo": {"id": "555001", "nombre": "Barcelona", "liga": "LigaPro", "id_liga": 246, "pais_iso": "ECU"},
        "partidos": [
            {"fecha": f"2026-11-{20 - i:02d}", "torneo": "LigaPro", "rival": "Emelec" if i % 2 == 0 else "LDU Quito"}
            for i in range(15)
        ],
    }
    coh_bar_ecu_dom = validar_coherencia_competitiva_local_visita(
        doc_villarreal_esp, doc_barcelona_ecu, pais_iso="ESP", liga_pref="LaLiga"
    )
    assert coh_bar_ecu_dom["coherente"] is False
    assert coh_bar_ecu_dom["estado"] == "FALSO HOMÓNIMO EXTRANJERO"
    assert coh_bar_ecu_dom["falso_homonimo"] == "visita"

    # 3) Caso "Barcelona" de otro país (Ecuador) o con historial incoherente en Champions League (Sabah FK vs Barcelona)
    coh_bar_ecu_ucl = validar_coherencia_competitiva_local_visita(
        doc_sabah_aze, doc_barcelona_ecu, pais_iso="INT", liga_pref="Champions League"
    )
    assert coh_bar_ecu_ucl["coherente"] is False
    assert coh_bar_ecu_ucl["estado"] == "FALSO HOMÓNIMO EXTRANJERO"
    assert coh_bar_ecu_ucl["falso_homonimo"] == "visita"

    # Y si el encabezado decía pais_iso="ESP" pero su historial de 15 partidos es de LigaPro (Ecuador) -> historial incoherente
    doc_barcelona_incoherente = copy.deepcopy(doc_barcelona_ecu)
    doc_barcelona_incoherente["equipo"]["pais_iso"] = "ESP"
    doc_barcelona_incoherente["equipo"]["liga"] = "LaLiga"
    coh_bar_incoh = validar_coherencia_competitiva_local_visita(
        doc_sabah_aze, doc_barcelona_incoherente, pais_iso="INT", liga_pref="Champions League"
    )
    assert coh_bar_incoh["coherente"] is False
    assert coh_bar_incoh["estado"] == "FALSO HOMÓNIMO EXTRANJERO"
    assert coh_bar_incoh["falso_homonimo"] == "visita"

    # 4) Caso snapshot 36fdc40d9f517d45 (Barcelona vs Villareal sin resolver, 0 partidos): sigue absteniéndose en preparar_analisis
    doc_villareal_vacio = {"equipo": {"id": None, "nombre": "Villareal", "liga": "", "pais_iso": "UNK"}, "partidos": []}
    monkeypatch.setattr(
        sm,
        "resolver_enfrentamiento",
        lambda *a, **k: {
            "equipo_local": "Barcelona",
            "equipo_visitante": "Villareal",
            "doc_local": doc_barca_esp,
            "doc_visitante": doc_villareal_vacio,
            "contexto": {"competicion": "LaLiga", "pais_iso": "ESP", "fecha_partido": "2026-11-24"},
        },
    )
    res_vil_vacio = sm.preparar_analisis("Barcelona", "Villareal", directorio_salida=str(tmp_path / "vil_vacio"))
    assert res_vil_vacio["abstenido"] is True

    # 5) Caso explícito Ronda 29b: Barcelona SC de Ecuador contra un equipo español (Villarreal / Real Madrid)
    rivales_ecu = ["Emelec", "LDU Quito", "Independiente del Valle", "El Nacional", "Aucas"]
    doc_barcelona_sc_ecu = {
        "equipo": {"id": "555002", "nombre": "Barcelona SC", "liga": "LigaPro", "id_liga": 246, "pais_iso": "ECU"},
        "partidos": [
            {"fecha": f"2026-11-{20 - i:02d}", "torneo": "LigaPro", "rival": rivales_ecu[i % len(rivales_ecu)]}
            for i in range(15)
        ],
    }
    coh_bsc_vs_esp = validar_coherencia_competitiva_local_visita(
        doc_villarreal_esp, doc_barcelona_sc_ecu, pais_iso="ESP", liga_pref="LaLiga"
    )
    assert coh_bsc_vs_esp["coherente"] is False
    assert coh_bsc_vs_esp["estado"] == "FALSO HOMÓNIMO EXTRANJERO"
    assert coh_bsc_vs_esp["falso_homonimo"] == "visita"

    monkeypatch.setattr(
        sm,
        "resolver_enfrentamiento",
        lambda *a, **k: {
            "equipo_local": "Villarreal",
            "equipo_visitante": "Barcelona SC",
            "doc_local": doc_villarreal_esp,
            "doc_visitante": doc_barcelona_sc_ecu,
            "contexto": {"competicion": "LaLiga", "pais_iso": "ESP", "fecha_partido": "2026-11-24"},
        },
    )
    res_bsc_esp = sm.preparar_analisis("Villarreal", "Barcelona SC", directorio_salida=str(tmp_path / "bsc_esp"))
    assert res_bsc_esp["abstenido"] is True
    assert res_bsc_esp["estado_cobertura"] == "falso_homonimo_extranjero"

    # 6) Caso explícito Ronda 29b: Villarreal con historial brasileño contra un equipo español
    rivales_bra = ["Flamengo", "Palmeiras", "Corinthians", "São Paulo", "Londrina", "Náutico"]
    doc_villarreal_historial_bra = copy.deepcopy(doc_barca_esp)
    doc_villarreal_historial_bra["equipo"] = {
        "id": "777002",
        "nombre": "Villarreal",
        "liga": "Brasileirão Série A",
        "pais_iso": "BRA",
    }
    for idx, p in enumerate((doc_villarreal_historial_bra.get("partidos") or [])[:15]):
        p["fecha"] = f"2026-11-{20 - idx:02d}"
        p["torneo"] = "Brasileirão Série A" if idx % 3 != 0 else "Copa do Brasil"
        p["equipo"] = "Villarreal"
        p["rival"] = rivales_bra[idx % len(rivales_bra)]

    coh_vil_bra = validar_coherencia_competitiva_local_visita(
        doc_barca_esp, doc_villarreal_historial_bra, pais_iso="ESP", liga_pref="LaLiga"
    )
    assert coh_vil_bra["coherente"] is False
    assert coh_vil_bra["estado"] == "FALSO HOMÓNIMO EXTRANJERO"
    assert coh_vil_bra["falso_homonimo"] == "visita"

    # Incluso si el encabezado del Villarreal brasileño fingiera ser de LaLiga [ESP] en Champions League,
    # su historial brasileño de 15 partidos es incoherente con ESP y el guardián lo abstiene:
    doc_villarreal_bra_camuflado = copy.deepcopy(doc_villarreal_historial_bra)
    doc_villarreal_bra_camuflado["equipo"] = {
        "id": "777003",
        "nombre": "Villarreal",
        "liga": "LaLiga",
        "id_liga": 87,
        "pais_iso": "ESP",
    }
    coh_vil_bra_ucl = validar_coherencia_competitiva_local_visita(
        doc_sabah_aze, doc_villarreal_bra_camuflado, pais_iso="INT", liga_pref="Champions League"
    )
    assert coh_vil_bra_ucl["coherente"] is False
    assert coh_vil_bra_ucl["estado"] == "FALSO HOMÓNIMO EXTRANJERO"
    assert coh_vil_bra_ucl["falso_homonimo"] == "visita"

    monkeypatch.setattr(
        sm,
        "resolver_enfrentamiento",
        lambda *a, **k: {
            "equipo_local": "Barcelona",
            "equipo_visitante": "Villarreal",
            "doc_local": doc_barca_esp,
            "doc_visitante": doc_villarreal_historial_bra,
            "contexto": {"competicion": "LaLiga", "pais_iso": "ESP", "fecha_partido": "2026-11-24"},
        },
    )
    res_vil_bra = sm.preparar_analisis("Barcelona", "Villarreal", directorio_salida=str(tmp_path / "vil_bra"))
    assert res_vil_bra["abstenido"] is True
    assert res_vil_bra["estado_cobertura"] == "falso_homonimo_extranjero"

    # 7) Caso explícito Ronda 29b: Club con liga ambigua y sin ccode ni id de liga -> PAIS_AMBIGUO
    doc_club_liga_ambigua_sin_ids = {
        "equipo": {"id": None, "nombre": "Athletic Unido", "liga": "Primera División"},
        "partidos": [
            {"fecha": f"2026-11-{20 - i:02d}", "torneo": "Primera División", "rival": f"Deportivo Sin Catalogar {i}"}
            for i in range(15)
        ],
    }
    res_pais_amb = resolver_pais_equipo_auditado(doc_club_liga_ambigua_sin_ids)
    assert res_pais_amb["pais_iso"] == ""
    assert res_pais_amb["ambiguo"] is True

    coh_club_amb_ucl = validar_coherencia_competitiva_local_visita(
        doc_club_liga_ambigua_sin_ids, doc_barca_esp, pais_iso="INT", liga_pref="Champions League"
    )
    assert coh_club_amb_ucl["coherente"] is False
    assert coh_club_amb_ucl["estado"] == "PAIS_AMBIGUO"
    assert coh_club_amb_ucl["falso_homonimo"] is None

    monkeypatch.setattr(
        sm,
        "resolver_enfrentamiento",
        lambda *a, **k: {
            "equipo_local": "Athletic Unido",
            "equipo_visitante": "Barcelona",
            "doc_local": doc_club_liga_ambigua_sin_ids,
            "doc_visitante": doc_barca_esp,
            "contexto": {
                "competicion": "Champions League",
                "pais_iso": "INT",
                "fecha_partido": "2026-11-24",
                "coherencia_competitiva": dict(coh_club_amb_ucl),
            },
        },
    )
    res_club_amb = sm.preparar_analisis("Athletic Unido", "Barcelona", directorio_salida=str(tmp_path / "club_amb"))
    assert res_club_amb["abstenido"] is True
    assert res_club_amb["estado_cobertura"] == "pais_ambiguo"
    assert "PAIS_AMBIGUO" in str(res_club_amb.get("motivo_abstencion") or "")


def test_r29_invariancia_partidos_domesticos_frente_a_477525a():
    fx_ln = json.loads((ROOT / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    fx_r5 = json.loads((ROOT / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))

    casos = [
        (fx_ln["londrina"], fx_ln["nautico"], "Londrina", "Náutico", "Brasileirao Serie C", "BRA"),
        (fx_r5["docs"]["italy"], fx_r5["docs"]["france"], "Italy", "France", "UEFA Nations League", "INT"),
        (fx_r5["docs"]["turkey"], fx_r5["docs"]["belgium"], "Turkey", "Belgium", "UEFA Nations League", "INT"),
    ]
    for dl, dv, eq_l, eq_v, comp, iso_c in casos:
        with dc.banderas_comportamiento(usar_guardian_homonimo_por_competicion=False):
            an_ref = mm.analizar(dl, dv, eq_l, eq_v, competicion=comp)
            t5_ref, s1_ref, _ = sm.obtener_top5_y_seccion1_filtrados(an_ref, an_ref.metadatos)
            sf_ref = [(s.etiqueta, s.estado, round(float(s.probabilidad), 10)) for s in an_ref.selecciones_filtradas]

        an_new = mm.analizar(dl, dv, eq_l, eq_v, competicion=comp)
        t5_new, s1_new, _ = sm.obtener_top5_y_seccion1_filtrados(an_new, an_new.metadatos)
        sf_new = [(s.etiqueta, s.estado, round(float(s.probabilidad), 10)) for s in an_new.selecciones_filtradas]
        coh = validar_coherencia_competitiva_local_visita(dl, dv, pais_iso=iso_c, liga_pref=comp)

        assert coh["coherente"] is True
        assert an_new.motor.tasas.lambda_local_ft == an_ref.motor.tasas.lambda_local_ft
        assert an_new.motor.tasas.lambda_visitante_ft == an_ref.motor.tasas.lambda_visitante_ft
        assert an_new.motor.matriz_ft == an_ref.motor.matriz_ft
        assert [(x["etiqueta"], round(float(x["probabilidad"]), 10)) for x in t5_new] == [
            (x["etiqueta"], round(float(x["probabilidad"]), 10)) for x in t5_ref
        ]
        assert len(s1_new) == len(s1_ref)
        assert sf_new == sf_ref
