"""
tests/test_bloque_b_ronda4.py
Pruebas para el Bloque B de la Ronda 4:
  - B1: Torneo real verificado (Série B, leagueId 8814) desde los partidos reales.
  - B2: Generación e impresión de 'Declarada: X | Detectada: Y' en consola, metadatos, Telegram y láminas.
"""

import pytest
import servicio_mercados as sm


def test_advertencia_liga_discrepante(monkeypatch):
    """
    B2: Si la liga declarada difiere de la liga detectada de los partidos reales,
    se propaga advertencia_liga con formato 'Declarada: X | Detectada: Y'.
    """
    doc_l = {
        "equipo": {"nombre": "Londrina", "liga": "Série B", "id_liga": 8814},
        "partidos": [{"id_partido": "5190799", "torneo": "Série B", "fecha": "2026-11-05"}],
        "partidos_futuros": [],
    }
    doc_v = {
        "equipo": {"nombre": "Náutico", "liga": "Série B", "id_liga": 8814},
        "partidos": [{"id_partido": "5190806", "torneo": "Série B", "fecha": "2026-11-08"}],
        "partidos_futuros": [],
    }

    # Mock de cargar_o_crear_auditoria para evitar llamadas de red
    def mock_cargar(nombre, **kwargs):
        if "londrina" in nombre.lower():
            return doc_l, {"fuente": "cache"}
        return doc_v, {"fuente": "cache"}

    monkeypatch.setattr(sm, "cargar_o_crear_auditoria", mock_cargar)

    # Declarada Serie C pero detectada Série B en los partidos
    res = sm.resolver_enfrentamiento("Londrina vs Náutico", liga={"nombre": "Serie C"})
    ctx = res.get("contexto", {})
    adv = ctx.get("advertencia_liga")
    assert adv is not None
    assert "Declarada: Serie C" in adv
    assert "Detectada: S" in adv and "rie B" in adv
