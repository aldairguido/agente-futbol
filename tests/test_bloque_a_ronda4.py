"""
tests/test_bloque_a_ronda4.py
Pruebas para el Bloque A de la Ronda 4:
  - Detección de partido ya jugado: respuesta "partido ya jugado, usa /evaluar" y resultado real.
  - Exclusión estricta del partido objetivo y corte temporal (cero fuga de información).
  - Exclusión de H2H con fecha >= corte.
"""

import json
from pathlib import Path
import pytest
from motor_mercados import filtrar_por_fecha
from cuadro_h2h_2024_2026 import recolectar_duelos_h2h_2024_2026, filtrar_enfrentamientos_2024_2026
import servicio_mercados as servicio


def test_partido_ya_jugado_bloquea_prepartido():
    """
    A2: Si el cruce objetivo ya se jugó, /mercados NO genera pronóstico prepartido:
    responde 'partido ya jugado, usa /evaluar' y muestra el resultado real.
    """
    # Crear dos docs con un partido finalizado común (Londrina 2 - 1 Náutico, id 5190812)
    p_comun_l = {
        "id_partido": "5190812",
        "fecha": "2026-11-11",
        "torneo": "Série B",
        "equipo": "Londrina",
        "rival": "Náutico",
        "condicion": "casa",
        "goles_favor": 2,
        "goles_contra": 1,
        "marcador": {"equipo": 2, "rival": 1, "texto": "2-1"},
    }
    p_comun_v = {
        "id_partido": "5190812",
        "fecha": "2026-11-11",
        "torneo": "Série B",
        "equipo": "Náutico",
        "rival": "Londrina",
        "condicion": "fuera",
        "goles_favor": 1,
        "goles_contra": 2,
        "marcador": {"equipo": 1, "rival": 2, "texto": "1-2"},
    }

    doc_l = {
        "equipo": {"nombre": "Londrina", "id": "298660"},
        "partidos": [p_comun_l],
        "partidos_futuros": [],
    }
    doc_v = {
        "equipo": {"nombre": "Náutico", "id": "2369"},
        "partidos": [p_comun_v],
        "partidos_futuros": [],
    }

    meta = servicio._metadatos_del_encuentro(doc_l, doc_v, "Londrina", "Náutico")
    assert meta.get("partido_ya_jugado") is True
    assert meta.get("id_partido") == "5190812"
    assert "Londrina 2 - 1 Náutico" in str(meta.get("resultado_partido"))

    # Verificar que preparar_analisis se abstiene y devuelve el texto reglamentario
    # Mocking resolver_enfrentamiento para usar estos docs
    def mock_resolver(*args, **kwargs):
        return {
            "equipo_local": "Londrina",
            "equipo_visitante": "Náutico",
            "doc_local": doc_l,
            "doc_visitante": doc_v,
            "contexto": meta,
            "meta_local": {},
            "meta_visitante": {},
            "identidad_verificada": True,
        }

    orig_resolver = servicio.resolver_enfrentamiento
    servicio.resolver_enfrentamiento = mock_resolver
    try:
        prep = servicio.preparar_analisis("Londrina", "Náutico")
        assert prep.get("partido_ya_jugado") is True
        assert prep.get("abstenido") is True
        txt = prep.get("analisis_texto_3_parrafos", "")
        assert "partido ya jugado, usa /evaluar" in txt.lower()
        assert "Londrina 2 - 1 Náutico" in txt
        assert "Finalizado" in txt
        # Cero probabilidades cuantitativas publicadas
        assert len(prep["analisis"].selecciones_filtradas) == 0
    finally:
        servicio.resolver_enfrentamiento = orig_resolver


def test_filtrar_por_fecha_excluye_partido_objetivo_y_posteriores():
    """
    A3: El partido objetivo y cualquier resultado con fecha >= fecha_corte quedan
    fuera de la muestra de partidos.
    """
    doc = {
        "equipo": {"nombre": "Londrina", "id": "298660"},
        "partidos": [
            {"id_partido": "5190812", "fecha": "2026-11-11", "rival": "Náutico"},
            {"id_partido": "5190799", "fecha": "2026-11-05", "rival": "Botafogo SP"},
            {"id_partido": "5190792", "fecha": "2026-11-01", "rival": "América-MG"},
            {"id_partido": "9999999", "fecha": "2026-11-15", "rival": "Futuro"},
        ],
    }

    # Corte al 11/11/2026 excluyendo 5190812
    filtrado = filtrar_por_fecha(doc, fecha_corte="2026-11-11", id_partido_excluir="5190812")
    pts = filtrado.get("partidos", [])
    ids = [p["id_partido"] for p in pts]

    assert "5190812" not in ids
    assert "9999999" not in ids
    assert ids == ["5190799", "5190792"]

    # Corte con string tipo "Análisis del 2026-11-11"
    filtrado_str = filtrar_por_fecha(doc, fecha_corte="Análisis del 2026-11-11", id_partido_excluir="5190812")
    ids_str = [p["id_partido"] for p in filtrado_str.get("partidos", [])]
    assert "5190812" not in ids_str
    assert "9999999" not in ids_str
    assert ids_str == ["5190799", "5190792"]


def test_h2h_excluye_partido_objetivo_y_fecha_corte():
    """
    A3: En H2H (2024–2026), el partido objetivo y cualquier enfrentamiento con fecha >= corte
    quedan fuera, evitando H2H circular.
    """
    partidos_h2h = [
        {"id_partido": "5190812", "fecha": "2026-11-11", "local": "Londrina", "visitante": "Náutico"},
        {"id_partido": "4000001", "fecha": "2025-06-15", "local": "Náutico", "visitante": "Londrina"},
    ]

    # Filtrar con fecha_corte 2026-11-11 y exclusión de 5190812
    validos = filtrar_enfrentamientos_2024_2026(
        partidos_h2h, fecha_corte="2026-11-11", id_partido_excluir="5190812"
    )
    ids_validos = [p["id_partido"] for p in validos]
    assert "5190812" not in ids_validos
    assert ids_validos == ["4000001"]

    # Si el único duelo registrado era el del 11/11/2026, el H2H queda en 0 duelos
    solo_5190812 = [partidos_h2h[0]]
    validos_cero = filtrar_enfrentamientos_2024_2026(
        solo_5190812, fecha_corte="2026-11-11", id_partido_excluir="5190812"
    )
    assert len(validos_cero) == 0
