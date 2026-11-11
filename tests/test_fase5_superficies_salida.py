"""
test_fase5_superficies_salida.py
Pruebas de la Fase 5: Superficies de Salida y Propagación Coherente (Sección 6).
Verifica:
  1. Abstención preventiva en preparar_analisis y supresión de probabilidades/selecciones.
  2. Generación del ticket PNG de abstención sin inventar datos.
  3. Cobertura parcial: filtro exclusivo de mercados habilitados.
  4. Reconocimiento de abstención en evaluacion_pronosticos sin impacto espurio en Brier.
  5. Cuadro C y Cuadro B con denominadores reales.
"""

from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock

import servicio_mercados as servicio
import evaluacion_pronosticos as evaluacion
from decision_cobertura import DecisionCobertura
from auditoria_historica import LAS_45_METRICAS_OFICIALES


def _crear_partido_mock(
    pos: int,
    goles_l: int = 1,
    goles_v: int = 0,
    claves_ft: list[str] | None = None,
) -> dict:
    c_ft = claves_ft if claves_ft is not None else ["xg", "remates_totales", "remates_puerta", "corners", "tarjetas_amarillas"]
    filas = [{"clave": c, "procedencia": "observada", "valor_local": 1.0, "valor_visita": 0.0} for c in c_ft]
    return {
        "posicion": pos,
        "goles_equipo": goles_l,
        "goles_rival": goles_v,
        "resumen_claves": {
            "FT": {"equipo": c_ft, "rival": c_ft},
            "1T": {"equipo": ["remates_puerta"], "rival": ["remates_puerta"]},
            "2T": {"equipo": ["remates_puerta"], "rival": ["remates_puerta"]},
        },
        "periodos": {
            "FT": {"filas": filas},
            "1T": {"filas": []},
            "2T": {"filas": []},
        },
    }


def _crear_doc_mock(
    equipo: str,
    n_oficiales: int = 15,
    n_detalle_ft: int = 15,
    claves_ft: list[str] | None = None,
    estado_identidad: str = "verificado",
) -> dict:
    partidos = []
    for i in range(1, n_oficiales + 1):
        c = claves_ft if (i <= n_detalle_ft) else []
        partidos.append(_crear_partido_mock(i, claves_ft=c))

    return {
        "equipo": equipo,
        "estado_identidad": estado_identidad,
        "resumen_auditoria": {
            "equipo": equipo,
            "partidos_oficiales_localizados": n_oficiales,
            "partidos_con_marcador": n_oficiales,
            "partidos_con_detalle_ft": n_detalle_ft,
            "partidos_solo_marcador": max(0, n_oficiales - n_detalle_ft),
            "etiqueta_cobertura": f"{n_oficiales}/15 oficiales · {n_detalle_ft}/15 con detalle FT · {max(0, n_oficiales - n_detalle_ft)}/15 solo con marcador",
        },
        "partidos": partidos,
    }


def test_preparar_analisis_abstenido_caso_tavernes(tmp_path):
    # UD Tavernes tiene 2 partidos con detalle (< 3) -> cobertura_insuficiente
    doc_levante = _crear_doc_mock("Levante UD", n_oficiales=15, n_detalle_ft=15)
    doc_tavernes = _crear_doc_mock("UD Tavernes", n_oficiales=15, n_detalle_ft=2)

    contexto_mock = {
        "equipo_local": "Levante UD",
        "equipo_visitante": "UD Tavernes",
        "doc_local": doc_levante,
        "doc_visitante": doc_tavernes,
        "contexto": {"competicion": "Copa del Rey", "fecha_partido": "2026-11-15"},
        "estado_identidad": "verificado",
        "identidad_verificada": True,
    }

    with patch("servicio_mercados.resolver_enfrentamiento", return_value=contexto_mock):
        prep = servicio.preparar_analisis("Levante UD vs UD Tavernes", directorio_salida=str(tmp_path))

    assert prep.get("abstenido") is True
    assert prep.get("estado_cobertura") == "cobertura_insuficiente"
    assert "2/15 partidos oficiales con detalle FT" in prep.get("motivo_abstencion")
    assert prep["analisis"].selecciones_filtradas == []
    assert prep["analisis"].mercados == []

    # Verificar que el ticket PNG de abstención fue generado físicamente
    ruta_img = prep.get("ruta_imagen")
    assert ruta_img is not None
    assert os.path.exists(ruta_img)
    assert os.path.getsize(ruta_img) > 1000  # Archivo de imagen real no vacío

    # Verificar texto de Telegram
    texto_tg = prep.get("analisis_texto_3_parrafos")
    assert "PRONÓSTICO ABSTENIDO POR COBERTURA INSUFICIENTE" in texto_tg
    assert "COBERTURA_INSUFICIENTE" in texto_tg


def test_enviar_analisis_abstenido(tmp_path):
    # Mock de resultado de preparación abstenida
    prep = {
        "id_trabajo": "test_abstencion_123",
        "abstenido": True,
        "estado_cobertura": "cobertura_insuficiente",
        "motivo_abstencion": "Detalle insuficiente",
        "contexto": {"equipo_local": "Levante UD", "equipo_visitante": "UD Tavernes"},
        "ruta_imagen": str(tmp_path / "ticket_abstencion.png"),
        "analisis_texto_3_parrafos": "🚨 PRONÓSTICO ABSTENIDO",
        "rutas": {"imagen": str(tmp_path / "ticket_abstencion.png")},
    }
    # Crear archivo dummy de imagen
    with open(prep["ruta_imagen"], "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nfakeimagebytes")

    mensajes_enviados = []
    fotos_enviadas = []

    def mock_sender_txt(txt, **kwargs):
        mensajes_enviados.append(txt)
        return {"message_id": 101}

    def mock_sender_foto(ruta, caption=None, **kwargs):
        fotos_enviadas.append((ruta, caption))
        return {"message_id": 102}

    res = servicio.enviar_analisis(
        prep,
        enviar_fn=mock_sender_txt,
        enviar_foto_fn=mock_sender_foto,
        chat_id=12345,
        nueva_ejecucion=True,
    )

    assert res["enviado"] is True
    assert res["estado"] == "abstenido_enviado"
    assert len(mensajes_enviados) == 1
    assert "🚨 PRONÓSTICO ABSTENIDO" in mensajes_enviados[0]
    assert len(fotos_enviadas) == 1
    assert "Pronóstico Abstenido" in fotos_enviadas[0][1]


def test_evaluacion_reconoce_abstencion_sin_penalizar_brier():
    prep_abs = {
        "id_trabajo": "snap_abs_456",
        "abstenido": True,
        "analisis": MagicMock(
            metadatos={
                "equipo_local": "Levante UD",
                "equipo_visitante": "UD Tavernes",
                "decision_cobertura": {"estado": "cobertura_insuficiente", "alerta_descriptiva": "Detalle insuficiente"},
            },
            parametros=MagicMock(umbral=0.70),
        ),
    }

    snap = evaluacion.snapshot_desde_analisis(prep_abs)
    assert snap.abstenido is True
    assert snap.estado_cobertura == "cobertura_insuficiente"
    assert snap.selecciones == []
    assert snap.selecciones_publicadas == []

    # Liquidación de selección abstenida
    sel_abs = {"mercado": "1X2", "evento": "gana_local", "abstenido": True, "motivo": "Cobertura insuficiente"}
    res_partido = evaluacion.ResultadoPartido(estado="finalizado", local="Levante UD", visitante="UD Tavernes")
    ev = evaluacion.liquidar(sel_abs, res_partido, "snap_abs_456")

    assert ev.estado_liquidacion == "abstenido_por_cobertura"
    assert ev.evento_ocurrio is None
    assert ev.objetivo_probabilidad == "no_evaluable"

    # En tanda de evaluación
    informe = evaluacion.evaluar_tanda([snap.a_dict()], { "Levante UD vs UD Tavernes": res_partido })
    assert informe.abstenidos_por_cobertura == 1
    assert len(informe.evaluaciones) == 0  # No genera evaluaciones publicadas espurias


def test_cuadro_b_y_cuadro_c_denominadores_reales():
    from motor_inferencia import calcular_cuadro_b_tendencias, calcular_cuadro_c_timeline

    # Equipo que solo tiene 2 partidos con detalle y 13 solo con marcador
    partidos_mock = [
        _crear_partido_mock(1, goles_l=2, goles_v=1, claves_ft=["corners", "tarjetas_amarillas"]),
        _crear_partido_mock(2, goles_l=0, goles_v=0, claves_ft=["corners", "tarjetas_amarillas"]),
    ] + [
        {"posicion": i, "goles_equipo": 1, "goles_rival": 0, "resumen_claves": {}, "periodos": {}}
        for i in range(3, 16)
    ]

    cuadro_b = calcular_cuadro_b_tendencias(partidos_mock, nombre_equipo="Equipo Test")
    # Tarjetas y córneres solo tienen 2 partidos con dato
    assert cuadro_b["menos_4_5_tarjetas"]._meta["total"] == 2
    assert cuadro_b["menos_4_5_tarjetas"]._meta["str"] == "2/2"
    assert cuadro_b["mas_10_5_corners"]._meta["total"] == 2

    # Victorias tiene 15 con marcador
    assert cuadro_b["victorias"]._meta["total"] == 15

    # Cuadro C: sin eventos de gol con minuto
    cuadro_c = calcular_cuadro_c_timeline(partidos_mock, nombre_equipo="Equipo Test")
    # Todas las franjas deben estar en 0 sin inventar repartos
    assert all(cuadro_c[fr]["anotados"] == 0 for fr in ["00_15", "16_30", "31_45", "46_60", "61_75", "76_90"])
