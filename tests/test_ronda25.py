"""
tests/test_ronda25.py — Pruebas de la Ronda 25 (Bloques 1 a 10).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════════════════════
# BLOQUE 1: KeyError 'informe' en bot_interactivo.py para abstenciones
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "estado_cobertura,motivo,rutas,con_texto",
    [
        ("falso_homonimo_extranjero", "FALSO HOMÓNIMO EXTRANJERO detectado en visita.", {}, True),
        ("datos_desactualizados", "Datos desactualizados (> 60 días).", {}, True),
        ("cobertura_insuficiente", "Cobertura insuficiente: menos de 10 partidos.", {}, False),
        ("historial_oficial_incompleto", "Historial oficial incompleto (< 15 partidos).", {}, True),
        ("identidad_no_verificada", "Identidad no verificada para el club visitante.", {}, False),
        ("desajuste_categoria_femenino", "Se resolvió un equipo femenino (W) sin haberlo solicitado.", {}, True),
    ],
)
def test_b1_abstencion_rutas_vacias_envia_mensaje_sin_keyerror(
    estado_cobertura: str, motivo: str, rutas: dict, con_texto: bool
):
    import bot_interactivo as bi
    import servicio_mercados as sm

    analisis_stub = SimpleNamespace(
        metadatos={
            "equipo_local": "Club A",
            "equipo_visitante": "Club B",
            "competicion": "LaLiga",
            "fecha_partido": "2026-11-22",
            "generado_en": "2026-11-22 12:00:00",
            "abstenido": True,
            "estado_cobertura": estado_cobertura,
            "motivo_abstencion": motivo,
            "decision_cobertura": {"estado": estado_cobertura, "alerta_descriptiva": motivo},
            "fuente": "Guarda Calidad de Datos",
        },
        parametros=SimpleNamespace(umbral=0.70),
        selecciones_filtradas=[],
        mercados=[],
        abstenido=True,
    )
    texto_3p = f"🚨 PRONÓSTICO ABSTENIDO ({estado_cobertura.upper()})\n\n{motivo}" if con_texto else ""
    preparado_fake = {
        "analisis": analisis_stub,
        "id_trabajo": f"test_b1_{estado_cobertura[:8]}",
        "rutas": dict(rutas),
        "contexto": {"equipo_local": "Club A", "equipo_visitante": "Club B"},
        "abstenido": True,
        "estado_cobertura": estado_cobertura,
        "motivo_abstencion": motivo,
        "decision_cobertura": {"estado": estado_cobertura},
        "analisis_texto_3_parrafos": texto_3p,
        "ruta_imagen": None,
    }

    enviados: list[str] = []

    def fake_enviar_seguro(_bot, _chat_id, texto, **kwargs):
        enviados.append(str(texto))
        return SimpleNamespace(message_id=999, date=1795350000)

    with (
        patch.object(sm, "preparar_analisis", return_value=preparado_fake),
        patch.object(bi, "enviar_seguro", side_effect=fake_enviar_seguro),
        patch.object(bi, "_encolar_persistencia_segura"),
        patch("evaluacion_pronosticos.guardar_snapshot", return_value=""),
        patch("evaluacion_pronosticos.guardar_inputs_reproducibles", return_value={"guardado": False}),
        patch("evaluacion_pronosticos.registrar_sombra_prepartido_en_ledger_oos", return_value={"registrado": False}),
    ):
        bi._hilo_ejecutar_mercados(
            chat_id=12345,
            equipo_a="Club A",
            equipo_b="Club B",
            umbral=0.70,
            limite=15,
            borrador=False,
        )

    assert enviados, f"Debe enviar mensaje de abstención para {estado_cobertura}"
    assert not any("KeyError" in m or "falló" in m for m in enviados), f"No debe lanzar KeyError: {enviados}"
    assert any(motivo in m and "ABSTENIDO" in m.upper() for m in enviados), f"Mensaje sin motivo: {enviados}"


def test_b1_fallback_envio_fallido_sin_informe_no_lanza_keyerror():
    """Si enviar_analisis devuelve enviado=False con rutas={}, tampoco hay KeyError."""
    import bot_interactivo as bi
    import servicio_mercados as sm

    analisis_stub = SimpleNamespace(
        metadatos={"equipo_local": "A", "equipo_visitante": "B", "generado_en": "2026-11-22 12:00:00"},
        parametros=SimpleNamespace(umbral=0.70),
        selecciones_filtradas=[],
        mercados=[],
        abstenido=False,
    )
    preparado_fake = {
        "analisis": analisis_stub,
        "id_trabajo": "test_b1_no_inf",
        "rutas": {},
        "contexto": {"equipo_local": "A", "equipo_visitante": "B"},
        "abstenido": False,
        "ruta_imagen": None,
    }
    enviados: list[str] = []

    with (
        patch.object(sm, "preparar_analisis", return_value=preparado_fake),
        patch.object(sm, "enviar_analisis", return_value={"enviado": False, "motivo": "fallo red"}),
        patch.object(bi, "enviar_seguro", side_effect=lambda _b, _c, t, **kw: enviados.append(str(t))),
        patch.object(bi, "_encolar_persistencia_segura"),
        patch("evaluacion_pronosticos.guardar_snapshot", return_value=""),
        patch("evaluacion_pronosticos.guardar_inputs_reproducibles", return_value={"guardado": False}),
        patch("evaluacion_pronosticos.registrar_sombra_prepartido_en_ledger_oos", return_value={"registrado": False}),
    ):
        bi._hilo_ejecutar_mercados(chat_id=12345, equipo_a="A", equipo_b="B", umbral=0.70, limite=15, borrador=False)

    assert any("fallo red" in m and "no disponible" in m for m in enviados)
    assert not any("KeyError" in m for m in enviados)


# ═══════════════════════════════════════════════════════════════════════════════
# BLOQUE 2: _parsear_momento / _comprobar_anterioridad (UTC y unix)
# ═══════════════════════════════════════════════════════════════════════════════

def test_b2_parsear_momento_sin_zona_es_utc_y_acepta_unix():
    import evaluacion_pronosticos as ep
    from datetime import datetime, timezone

    t_naive = ep._parsear_momento("2026-11-20 18:26:42")
    assert t_naive == datetime(2026, 11, 20, 18, 26, 42, tzinfo=timezone.utc)

    t_unix = ep._parsear_momento(1795199254)
    assert t_unix == datetime(2026, 11, 20, 18, 27, 34, tzinfo=timezone.utc)


def test_b2_anterioridad_casos_reales_athletic_y_puebla():
    import evaluacion_pronosticos as ep

    # Caso real 1: Athletic Club vs Espanyol (generado 2026-11-20 18:26:42 UTC, inicio 20:00Z / 21:02:34Z)
    doc_ath = {
        "generado_en": "2026-11-20 18:26:42",
        "hora_inicio_partido": "2026-11-20T20:00:00Z",
        "telegram_message_date": 1795199254,
        "telegram_envio": {"message_date": 1795199254, "message_date_utc": "2026-11-20T18:27:34Z"},
    }
    res_ath = ep.ResultadoPartido(
        id_partido="5868132", estado="finalizado", local="Athletic Club", visitante="Espanyol",
        goles_local=0, goles_visitante=1, inicio_utc="2026-11-20T21:02:34Z",
    )
    ok_ath, mot_ath = ep._comprobar_anterioridad(doc_ath, res_ath)
    assert ok_ath is True, mot_ath
    assert "telegram_message_date" in mot_ath

    # También sin telegram_message_date: generado_en naive (UTC) < inicio 20:00Z
    doc_ath_solo_gen = {"generado_en": "2026-11-20 18:26:42"}
    res_ath_20 = ep.ResultadoPartido(
        id_partido="5868132", estado="finalizado", local="Athletic Club", visitante="Espanyol",
        goles_local=0, goles_visitante=1, inicio_utc="2026-11-20T20:00:00Z",
    )
    ok_gen, mot_gen = ep._comprobar_anterioridad(doc_ath_solo_gen, res_ath_20)
    assert ok_gen is True, mot_gen

    # Caso real 2: Puebla vs Atlético de San Luis
    doc_pue = {
        "generado_en": "2026-11-20 19:17:26",
        "hora_inicio_partido": "2026-11-21T01:00:00Z",
        "telegram_message_date": 1795202298,
        "telegram_envio": {"message_date": 1795202298, "message_date_utc": "2026-11-20T19:18:18Z"},
    }
    res_pue = ep.ResultadoPartido(
        id_partido="1000014643", estado="finalizado", local="Puebla", visitante="Atlético de San Luis",
        goles_local=1, goles_visitante=1, inicio_utc="2026-11-21T02:00:50Z",
    )
    ok_pue, mot_pue = ep._comprobar_anterioridad(doc_pue, res_pue)
    assert ok_pue is True, mot_pue

    # Caso post-inicio: telegram_message_date posterior al inicio -> rechazado
    doc_post = {
        "generado_en": "2026-11-20 18:00:00",
        "hora_inicio_partido": "2026-11-20T20:00:00Z",
        "telegram_message_date": 1795205400,  # 20:10Z > 20:00Z
    }
    ok_post, _ = ep._comprobar_anterioridad(doc_post, res_ath_20)
    assert ok_post is False


# =====================================================================
# BLOQUE 3: Resolutor — límites de palabra y guarda equipo (W)
# =====================================================================

@pytest.mark.parametrize(
    "texto,esperado_fem,esperado_juv",
    [
        ("bundesliga fc bayern münchen 1. fc köln", False, False),
        ("laliga fc barcelona vs villarreal", False, False),
        ("LaLiga FC Barcelona", False, False),
        ("liga f barcelona vs real madrid", True, False),
        ("frauen-bundesliga bayern vs wolfsburg", True, False),
        ("bayern (w) vs chelsea (w)", True, False),
        ("liga femenina peru", True, False),
        ("barcelona u19 vs bayern u19", False, True),
        ("superliga fc copenhagen", False, False),
    ],
)
def test_b3_detectar_categoria_limites_de_palabra(texto, esperado_fem, esperado_juv):
    from resolvedor_entidades import detectar_categoria_desde_texto

    fem, juv = detectar_categoria_desde_texto(texto)
    assert fem is esperado_fem, f"Para {texto!r}: fem={fem}, esperado={esperado_fem}"
    assert juv is esperado_juv, f"Para {texto!r}: juv={juv}, esperado={esperado_juv}"


def test_b3_guarda_abstencion_si_resuelve_equipo_w_sin_pedirlo(monkeypatch):
    import servicio_mercados as sm

    fake_contexto = {
        "equipo_local": "Bayern München (W)",
        "equipo_visitante": "1. FC Köln (W)",
        "doc_local": {
            "equipo": {"nombre": "Bayern München (W)", "liga": "Frauen-Bundesliga", "pais_iso": "DEU"},
            "partidos": [],
        },
        "doc_visitante": {
            "equipo": {"nombre": "1. FC Köln (W)", "liga": "Frauen-Bundesliga", "pais_iso": "DEU"},
            "partidos": [],
        },
        "contexto": {
            "competicion": "Bundesliga",
            "pais_iso": "DEU",
            "fecha_partido": "2026-11-25",
            "fixture_confirmado": False,
        },
    }
    monkeypatch.setattr(sm, "resolver_enfrentamiento", lambda *a, **k: fake_contexto)

    res = sm.preparar_analisis(
        "FC Bayern München",
        "1. FC Köln",
        liga={"nombre": "Bundesliga", "pais_iso": "DEU"},
    )
    assert res["abstenido"] is True
    assert res["estado_cobertura"] == "desajuste_categoria_femenino"
    assert "femenino" in res["motivo_abstencion"].lower()
    assert res["rutas"] == {}


# =====================================================================
# BLOQUE 4: validar_coherencia_competitiva_local_visita — Barcelona vs Villareal
# =====================================================================

def test_b4_equipo_sin_historial_ni_ligas_no_es_falso_homonimo_extranjero():
    from auditoria_historica import validar_coherencia_competitiva_local_visita

    audit_barcelona = {
        "equipo": {"id": "8634", "nombre": "Barcelona", "liga": "LaLiga", "pais_iso": "ESP"},
        "partidos": [
            {"torneo": "LaLiga", "rival": "Real Madrid"},
            {"torneo": "Champions League", "rival": "PSG"},
        ],
    }
    audit_villareal_sin_resolver = {
        "equipo": {"id": None, "nombre": "Villareal", "liga": "", "pais_iso": "UNK"},
        "partidos": [],
    }
    res = validar_coherencia_competitiva_local_visita(
        audit_barcelona, audit_villareal_sin_resolver, pais_iso="ESP"
    )
    assert res["estado"] != "FALSO HOMÓNIMO EXTRANJERO"
    assert res["estado"] == "SIN_HISTORIAL_COMPARABLE"
    assert res["falso_homonimo"] is None
    assert res["coherente"] is True


def test_b4_falso_homonimo_extranjero_real_sigue_detectandose():
    from auditoria_historica import validar_coherencia_competitiva_local_visita

    audit_local = {
        "equipo": {"id": "1", "nombre": "Aurora", "liga": "Division Profesional", "pais_iso": "BOL"},
        "partidos": [{"torneo": "Division Profesional", "rival": "Bolivar"}],
    }
    audit_visita_extranjero = {
        "equipo": {"id": "2", "nombre": "CD Universitario", "liga": "LPF Apertura", "pais_iso": "PAN"},
        "partidos": [{"torneo": "LPF Apertura", "rival": "Tauro"}],
    }
    res = validar_coherencia_competitiva_local_visita(
        audit_local, audit_visita_extranjero, pais_iso="BOL"
    )
    assert res["coherente"] is False
    assert res["estado"] == "FALSO HOMÓNIMO EXTRANJERO"
    assert res["falso_homonimo"] == "visita"
