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
