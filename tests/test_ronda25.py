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


# =====================================================================
# BLOQUE 5: Contabilidad OOS — exige telegram_envio (acuse) y abstenido
# =====================================================================

def test_b5_pre_partido_sin_acuse_no_es_oos_valido():
    import evaluacion_pronosticos as ep

    reg_sin_acuse = {
        "estado_registro": "pre_partido",
        "id_trabajo": "pre_sin_acuse_01",
        "partido": "Athletic Club vs Espanyol",
        "fecha": "2026-11-20",
        "fixture_confirmado": True,
        "fuente_fecha": "fixture",
        "hora_inicio_partido": "2026-11-20T20:00:00Z",
        "telegram_message_date": 1795199220,  # 2026-11-20T18:27:00Z (solo origen, sin telegram_envio)
        "es_oos_valido": True,  # simulando entrada histórica escrita antes de Ronda 25
        "es_canonica": True,
        "estado_canonico": "canonica",
    }
    cls = ep.clasificar_oos_entrada_ledger(reg_sin_acuse)
    assert cls["es_oos_valido"] is False
    assert cls["es_canonica"] is False
    assert cls["clasificacion_oos"] == "no_oos"

    # Al evaluar directamente sin telegram_envio también da False
    ev = ep.evaluar_contabilidad_oos_registro(
        fixture_confirmado=True,
        fuente_fecha="fixture",
        hora_inicio_partido="2026-11-20T20:00:00Z",
        telegram_message_date=1795199220,
        telegram_envio=None,
    )
    assert ev["es_oos_valido"] is False
    assert ev["estado_canonico"] == "no_oos"

    # Con acuse telegram_envio anterior al inicio sí es OOS válido y canónico
    reg_con_acuse = dict(reg_sin_acuse)
    reg_con_acuse["telegram_envio"] = {
        "message_id": 9001,
        "message_date": 1795199235,
        "message_date_utc": "2026-11-20T18:27:15Z",
    }
    cls_ok = ep.clasificar_oos_entrada_ledger(reg_con_acuse)
    assert cls_ok["es_oos_valido"] is True
    assert cls_ok["es_canonica"] is True
    assert cls_ok["clasificacion_oos"] == "canonica"


def test_b5_fcb37345419813b2_abstenido_y_sin_acuse():
    import evaluacion_pronosticos as ep

    # 1) Snapshot/registro abstenido como fcb37345419813b2 -> es_oos_valido=False, estado='abstenido'
    reg_fcb_abs = {
        "id_trabajo": "fcb37345419813b2",
        "partido": "Bayern München (W) vs 1. FC Köln (W)",
        "fecha": "2026-11-22",
        "fixture_confirmado": True,
        "fuente_fecha": "fixture",
        "hora_inicio_partido": "2026-11-22T14:30:00Z",
        "telegram_message_date": 1795350000,
        "abstenido": True,
        "estado_cobertura": "desajuste_categoria_femenino",
        "es_oos_valido": True,  # incluso si un registro histórico tenía True
        "es_canonica": True,
        "estado_canonico": "canonica",
    }
    cls_abs = ep.clasificar_oos_entrada_ledger(reg_fcb_abs)
    assert cls_abs["es_oos_valido"] is False
    assert cls_abs["es_canonica"] is False
    assert cls_abs["clasificacion_oos"] == "abstenido"

    ev_abs = ep.evaluar_contabilidad_oos_registro(
        fixture_confirmado=True,
        fuente_fecha="fixture",
        hora_inicio_partido="2026-11-22T14:30:00Z",
        telegram_message_date=1795350000,
        telegram_envio={"message_id": 77, "message_date": 1795350010, "message_date_utc": "2026-11-22T12:20:10Z"},
        abstenido=True,
    )
    assert ev_abs["es_oos_valido"] is False
    assert ev_abs["es_canonica"] is False
    assert ev_abs["estado_canonico"] == "abstenido"

    # 2) Entrada fcb37345419813b2 pre_partido sin acuse de envío -> es_oos_valido=False
    reg_fcb_sin_acuse = {
        "estado_registro": "pre_partido",
        "id_trabajo": "fcb37345419813b2",
        "partido": "Bayern München vs 1. FC Köln",
        "fecha": "2026-11-22",
        "fixture_confirmado": True,
        "fuente_fecha": "fixture",
        "hora_inicio_partido": "2026-11-22T14:30:00Z",
        "telegram_message_date": 1795350000,
        "es_oos_valido": True,
        "es_canonica": True,
        "estado_canonico": "canonica",
    }
    cls_sa = ep.clasificar_oos_entrada_ledger(reg_fcb_sin_acuse)
    assert cls_sa["es_oos_valido"] is False
    assert cls_sa["es_canonica"] is False
    assert cls_sa["clasificacion_oos"] == "no_oos"


# =====================================================================
# BLOQUE 6: resumen_telegram de /evaluar fragmentado <=4000 con reintento
# =====================================================================

def test_b6_enviar_resumen_largo_fragmenta_y_reintenta(tmp_path):
    import evaluacion_pronosticos as ep

    # Resumen > 6000 caracteres con saltos de línea
    lineas = [f"Partido {i:02d}: Local {i} vs Visitante {i} | Brier=0.1234 | Acierto 80.0% (8/10)" for i in range(1, 120)]
    resumen_largo = "\n".join(lineas)
    assert len(resumen_largo) > 6000

    enviados = []
    intentos_parte_2 = {"n": 0}

    def fake_enviar(texto, chat_id):
        if len(enviados) == 1 and intentos_parte_2["n"] == 0:
            intentos_parte_2["n"] += 1
            raise RuntimeError("timeout transitorio de Telegram")
        enviados.append(texto)
        return {"ok": True, "message_id": 1000 + len(enviados)}

    preparado = {
        "id_ejecucion": "eval_larga_r25",
        "mensaje": resumen_largo,
        "imagenes": [],
        "rutas": {},
    }
    ruta_reg = str(tmp_path / "registro_envios.json")
    res = ep.enviar_resumen(preparado, fake_enviar, chat_id=12345, ruta_registro=ruta_reg)

    assert res["enviado"] is True
    assert res["estado"] == "envio_confirmado"
    assert res["partes_enviadas"] >= 2
    assert len(enviados) == res["partes_enviadas"]
    assert all(len(t) <= 4000 for t in enviados)
    assert intentos_parte_2["n"] == 1
    assert len(res["message_ids"]) == len(enviados)


# =====================================================================
# BLOQUE 7: /estado — OOS válidos n/30, calibración y nunca +EV VALIDADO sin cuotas
# =====================================================================

def test_b7_estado_con_menos_de_30_oos_muestra_calibracion_y_oos_n_de_30():
    import bot_interactivo as bi
    from agente_consciente import AgenteConsciente

    ag = AgenteConsciente.__new__(AgenteConsciente)
    ag.memoria_vivencial = [{"resultado": "exito"}] * 20
    ag.nivel_madurez = 120
    ag.evaluacion_real = {
        "partidos_unicos": 45,  # incluye legacy/no-OOS
        "ganadas": 100,
        "perdidas": 10,
        "win_rate": 0.909,
        "partidos_oos_validos": 2,
        "ganadas_oos": 7,
        "perdidas_oos": 3,
        "win_rate_oos": 0.70,
        "brier_oos": 0.195,
        "cuotas_reales_validadas": False,
    }
    ag._calibrar_estado_emocional()
    assert ag.estado_adaptativo == "en calibración empírica (muestra en desarrollo)"
    assert "+EV" not in ag.estado_adaptativo.upper()

    enviados = []
    with (
        patch("agente_consciente.obtener_agente_consciente", return_value=ag),
        patch.object(bi, "enviar_seguro", side_effect=lambda b, m, txt, **k: enviados.append(txt)),
        patch.object(bi, "CHAT_ID_AUTORIZADO", "123"),
    ):
        msg = SimpleNamespace(chat=SimpleNamespace(id=123), from_user=SimpleNamespace(id=123))
        bi.cmd_estado(msg)

    assert len(enviados) == 1
    out = enviados[0]
    assert "OOS válidos: 2/30" in out
    assert "EN CALIBRACIÓN EMPÍRICA (MUESTRA EN DESARROLLO)" in out
    assert "7 aciertos | 3 fallos" in out
    assert "70.0%" in out
    assert "+EV VALIDADO" not in out


def test_b7_estado_con_35_oos_sin_cuotas_nunca_muestra_ev_validado():
    import bot_interactivo as bi
    from agente_consciente import AgenteConsciente

    ag = AgenteConsciente.__new__(AgenteConsciente)
    ag.memoria_vivencial = []
    ag.nivel_madurez = 500
    ag.evaluacion_real = {
        "partidos_oos_validos": 35,
        "ganadas_oos": 90,
        "perdidas_oos": 10,
        "win_rate_oos": 0.90,
        "brier_oos": 0.14,
        "cuotas_reales_validadas": False,
    }
    ag._calibrar_estado_emocional()
    assert "+EV VALIDADO" not in ag.estado_adaptativo.upper()

    enviados = []
    with (
        patch("agente_consciente.obtener_agente_consciente", return_value=ag),
        patch.object(bi, "enviar_seguro", side_effect=lambda b, m, txt, **k: enviados.append(txt)),
        patch.object(bi, "CHAT_ID_AUTORIZADO", "123"),
    ):
        msg = SimpleNamespace(chat=SimpleNamespace(id=123), from_user=SimpleNamespace(id=123))
        bi.cmd_estado(msg)

    assert len(enviados) == 1
    assert "OOS válidos: 35/30" in enviados[0]
    assert "+EV VALIDADO" not in enviados[0]


# =====================================================================
# BLOQUE 8: Campo explícito firma_motor en snapshot y ledger (opcional)
# =====================================================================

def test_b8_firma_motor_en_snapshot_y_ledger_compatible_hacia_atras(tmp_path):
    import evaluacion_pronosticos as ep
    from servicio_mercados import FIRMA_MOTOR

    # 1) SnapshotPronostico sin firma_motor omite la clave (retrocompatible)
    s_old = ep.SnapshotPronostico(
        id_trabajo="old_1",
        equipos={"local": "A", "visitante": "B"},
        partido={"fecha": "2026-11-22"},
        generado_en="2026-11-22T12:00:00Z",
        publicado_en=None,
        modelo={"nombre": "m", "version": "1"},
        datos_corte={},
        selecciones=[],
        selecciones_publicadas=[],
        umbral_publicado=0.70,
        referencia="",
    )
    assert "firma_motor" not in s_old.a_dict()

    # 2) snapshot_desde_analisis y registrar_sombra_prepartido_en_ledger_oos guardan firma_motor explícito
    motor_fake = SimpleNamespace(
        tasas=SimpleNamespace(lambda_local_ft=1.4, lambda_visitante_ft=1.1),
        muestras={
            "local_general": SimpleNamespace(partidos_general=15),
            "visitante_general": SimpleNamespace(partidos_general=15),
            "local_cond": SimpleNamespace(partidos_condicion=8),
            "visitante_cond": SimpleNamespace(partidos_condicion=8),
        },
        recursivo={},
    )
    an_fake = SimpleNamespace(
        metadatos={
            "equipo_local": "Club A",
            "equipo_visitante": "Club B",
            "fecha_partido": "2026-11-25",
            "competicion": "LaLiga",
            "generado_en": "2026-11-22T12:00:00Z",
            "fixture_confirmado": True,
            "hora_inicio_partido": "2026-11-25T20:00:00Z",
        },
        parametros=SimpleNamespace(umbral=0.70, w_condicion=0.5, w_xg=0.5, distribucion_conteos="poisson"),
        motor=motor_fake,
        mercados=[],
        selecciones_filtradas=[],
        abstenido=False,
    )
    prep = {
        "id_trabajo": "r25_fm_01",
        "analisis": an_fake,
        "contexto": dict(an_fake.metadatos),
        "rutas": {"informe": "dummy.md"},
        "telegram_envio": {"message_id": 55, "message_date": 1795350000, "message_date_utc": "2026-11-22T12:20:00Z"},
        "modo_sombra": {
            "esquema": "sombra-v2-tribrazo",
            "publicado": "soloA",
            "version_base": "soloA",
            "version_sombra": "A+B",
            "lambdas_base": {"local": 1.4, "visitante": 1.1},
            "lambdas_sombra": {"local": 1.4, "visitante": 1.1},
            "comparaciones": [],
            "n_selecciones_comparables": 0,
        },
    }
    snap = ep.snapshot_desde_analisis(prep)
    snap_d = snap.a_dict()
    assert snap_d["firma_motor"] == FIRMA_MOTOR

    ruta_led = str(tmp_path / "ledger_fm.jsonl")
    r_pre = ep.registrar_sombra_prepartido_en_ledger_oos(prep, ruta_ledger=ruta_led)
    assert r_pre["registrado"] is True
    regs = ep.cargar_ledger_oos(ruta_led)
    assert len(regs) == 1
    assert regs[0]["firma_motor"] == FIRMA_MOTOR
    assert ep.verificar_cadena_ledger_oos(ruta_led)["ok"] is True


# =====================================================================
# BLOQUE 9: reproducir_ab_desde_inputs — brazo base con NB corners=True (323/323)
# =====================================================================

def test_b9_reproducir_ab_desde_inputs_coincide_323_de_323_prob_base(tmp_path):
    import json
    from pathlib import Path as _P
    import evaluacion_pronosticos as ep
    import motor_mercados as mm
    from decision_cobertura import banderas_comportamiento

    raiz = _P(r"D:\agente_futbol")
    ln_docs = json.loads((raiz / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    doc_l = ln_docs["londrina"]
    doc_v = ln_docs["nautico"]

    # Corrida de producción (soloA con usar_sobredispersion_nb_corners=True)
    with banderas_comportamiento(activar_todas=False, usar_sobredispersion_nb_corners=True):
        an_prod = mm.analizar(
            doc_l, doc_v, "Londrina", "Náutico",
            condicion_local="casa", condicion_visitante="fuera",
            competicion="Série C",
        )
    an_prod.metadatos["fecha_partido"] = "2026-11-20"
    an_prod.metadatos["generado_en"] = "2026-11-20T12:00:00Z"
    preparado = {
        "id_trabajo": "r25_b9_repro",
        "analisis": an_prod,
        "contexto": {
            "equipo_local": "Londrina",
            "equipo_visitante": "Náutico",
            "doc_local": doc_l,
            "doc_visitante": doc_v,
            "fecha_partido": "2026-11-20",
            "competicion": "Série C",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
        },
        "rutas": {},
    }
    ep.adjuntar_modo_sombra_preparado(preparado)
    res_g = ep.guardar_inputs_reproducibles(preparado, directorio=str(tmp_path))
    assert res_g["ok"] is True

    rep = ep.reproducir_ab_desde_inputs(res_g["ruta"])
    comp_prod = {
        (c["mercado"], c["evento"], c.get("periodo"), c.get("equipo"), c.get("linea")): c["prob_base"]
        for c in preparado["modo_sombra"]["comparaciones"]
    }
    comp_rep = {
        (c["mercado"], c["evento"], c.get("periodo"), c.get("equipo"), c.get("linea")): c["prob_base"]
        for c in rep["modo_sombra"]["comparaciones"]
    }
    assert len(comp_prod) >= 323
    assert len(comp_rep) == len(comp_prod)
    coinciden = sum(1 for k, v in comp_prod.items() if k in comp_rep and abs(float(v) - float(comp_rep[k])) <= 1e-9)
    assert coinciden == len(comp_prod), f"Solo coincidieron {coinciden}/{len(comp_prod)} en prob_base"
    corners_keys = [k for k in comp_prod if "esquina" in k[0].lower() or "córner" in k[0].lower() or "corner" in k[0].lower()]
    assert len(corners_keys) > 0
    assert all(abs(float(comp_prod[k]) - float(comp_rep[k])) <= 1e-9 for k in corners_keys)


# =====================================================================
# BLOQUE 10: Cosméticos — λ base tras Doble ADN, Ticket de Valor PNG, Párrafo 2 H2H n<10
# =====================================================================

def test_b10_h2h_n_menor_10_usa_difiere_o_coincide_exploratorio():
    import informe as inf

    an_stub = SimpleNamespace(
        metadatos={"equipo_local": "Club A", "equipo_visitante": "Club B", "competicion": "LaLiga"},
        motor=SimpleNamespace(
            tasas=SimpleNamespace(lambda_local_ft=1.80, lambda_visitante_ft=1.20),
            matriz_ft=[[0.05 if (i + j) <= 2 else (0.70 if (i, j) == (2, 1) else 0.0) for j in range(5)] for i in range(5)],
        ),
        mercados=[],
        selecciones_filtradas=[],
        grupos={"mantiene": [], "sensible": []},
    )
    # Caso 1: difiere (modelo Over 2.5, H2H n=3 Under) -> "difiere (exploratorio)", NO "CONTRADICE"
    info_diff = {
        "n_duelos": 3,
        "duelos": [
            {"local": "Club A", "visitante": "Club B", "goles_local": 1, "goles_visitante": 0},
            {"local": "Club B", "visitante": "Club A", "goles_local": 1, "goles_visitante": 1},
            {"local": "Club A", "visitante": "Club B", "goles_local": 0, "goles_visitante": 1},
        ],
    }
    txt_diff = inf.redactar_analisis_3_parrafos(an_stub, info_h2h=info_diff, cuotas_flashscore={})
    p2_diff = [p for p in txt_diff.split("\n\n") if "2. Contraste con el Modelo H2H" in p][0]
    assert "difiere (exploratorio)" in p2_diff
    assert "CONTRADICE" not in p2_diff

    # Caso 2: coincide (modelo Over 2.5, H2H n=3 Over) -> "coincide (exploratorio)", NO "APOYA"
    info_coinc = {
        "n_duelos": 3,
        "duelos": [
            {"local": "Club A", "visitante": "Club B", "goles_local": 2, "goles_visitante": 1},
            {"local": "Club B", "visitante": "Club A", "goles_local": 2, "goles_visitante": 2},
            {"local": "Club A", "visitante": "Club B", "goles_local": 3, "goles_visitante": 1},
        ],
    }
    txt_coinc = inf.redactar_analisis_3_parrafos(an_stub, info_h2h=info_coinc, cuotas_flashscore={})
    p2_coinc = [p for p in txt_coinc.split("\n\n") if "2. Contraste con el Modelo H2H" in p][0]
    assert "coincide (exploratorio)" in p2_coinc
    assert "APOYA" not in p2_coinc


def test_b10_ticket_valor_png_envuelve_recomendacion_larga_sin_solapar(tmp_path):
    import informe as inf

    an_stub = SimpleNamespace(
        metadatos={"equipo_local": "Athletic Club", "equipo_visitante": "Espanyol", "competicion": "LaLiga"},
        motor=SimpleNamespace(
            tasas=SimpleNamespace(lambda_local_ft=1.43, lambda_visitante_ft=1.01),
            matriz_ft=None,
        ),
        mercados=[],
        selecciones_filtradas=[],
    )
    out_png = tmp_path / "ticket_valor_test.png"
    ruta = inf.generar_ticket_valor_png(an_stub, cuotas_flashscore={}, destino=str(out_png))
    assert Path(ruta).exists() and Path(ruta).stat().st_size > 1000


def test_b5_clasificar_ledger_resuelve_telegram_envio_y_abstenido_desde_snapshot(tmp_path):
    import json
    import evaluacion_pronosticos as ep

    snaps_dir = tmp_path / "pronosticos"
    eval_dir = tmp_path / "evaluacion"
    snaps_dir.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=True, exist_ok=True)

    # 1) Entrada pre_partido histórica sin telegram_envio inline, pero su snapshot SÍ tiene telegram_envio pre-inicio
    snap_ok = {
        "id_trabajo": "hist_13_ok",
        "fixture_confirmado": True,
        "hora_inicio_partido": "2026-11-19T22:30:00Z",
        "telegram_envio": {"message_id": 1668, "message_date": 1795079707, "message_date_utc": "2026-11-19T09:15:07Z"},
        "es_oos_valido": True,
        "es_canonica": True,
        "estado_canonico": "canonica",
    }
    (snaps_dir / "hist_13_ok.json").write_text(json.dumps(snap_ok), encoding="utf-8")

    reg_pre_hist = {
        "estado_registro": "pre_partido",
        "id_trabajo": "hist_13_ok",
        "partido": "Vitória vs RB Bragantino",
        "fecha": "2026-11-19",
        "fixture_confirmado": True,
        "hora_inicio_partido": "2026-11-19T22:30:00Z",
        "telegram_message_date": 1795079538,
        "es_oos_valido": True,
        "es_canonica": True,
        "estado_canonico": "canonica",
    }
    cls_ok = ep.clasificar_oos_entrada_ledger(reg_pre_hist, directorio_pronosticos=str(snaps_dir))
    assert cls_ok["clasificacion_oos"] == "canonica"
    assert cls_ok["es_oos_valido"] is True
    assert cls_ok["es_canonica"] is True

    # 2) Entrada histórica cuyo snapshot NO tiene telegram_envio -> no_oos
    snap_sin_ack = {
        "id_trabajo": "hist_sin_ack",
        "fixture_confirmado": True,
        "hora_inicio_partido": "2026-11-19T22:30:00Z",
        "es_oos_valido": True,
        "es_canonica": True,
        "estado_canonico": "canonica",
    }
    (snaps_dir / "hist_sin_ack.json").write_text(json.dumps(snap_sin_ack), encoding="utf-8")
    reg_sin_ack = {**reg_pre_hist, "id_trabajo": "hist_sin_ack"}
    cls_no = ep.clasificar_oos_entrada_ledger(reg_sin_ack, directorio_pronosticos=str(snaps_dir))
    assert cls_no["clasificacion_oos"] == "no_oos"
    assert cls_no["es_oos_valido"] is False
    assert cls_no["es_canonica"] is False

    # 3) Entrada histórica cuyo snapshot está abstenido -> abstenido
    snap_abs = {
        "id_trabajo": "hist_abs",
        "abstenido": True,
        "estado_cobertura": "datos_desactualizados",
        "es_oos_valido": True,
        "es_canonica": True,
    }
    (snaps_dir / "hist_abs.json").write_text(json.dumps(snap_abs), encoding="utf-8")
    reg_abs = {**reg_pre_hist, "id_trabajo": "hist_abs"}
    cls_abs = ep.clasificar_oos_entrada_ledger(reg_abs, directorio_pronosticos=str(snaps_dir))
    assert cls_abs["clasificacion_oos"] == "abstenido"
    assert cls_abs["es_oos_valido"] is False
    assert cls_abs["es_canonica"] is False
