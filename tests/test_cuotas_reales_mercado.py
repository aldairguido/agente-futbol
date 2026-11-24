"""
tests/test_cuotas_reales_mercado.py
───────────────────────────────────────────────────────────────────────────────
Pruebas unitarias offline para la extracción gratuita de cuotas reales de mercado
(Flashscore GraphQL geoIpCode=PE con Betsson + consenso + de-vig, fallback
FotMob /api/data/matchOdds, Ticket de Valor y persistencia en snapshots).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fuente_flashscore import ClienteFlashscore, devig_probabilidades


def _payload_graphql_ejemplo() -> dict:
    return {
        "data": {
            "findOddsByEventId": {
                "eventId": "pItlFEBk",
                "settings": {
                    "bookmakers": [
                        {"bookmaker": {"id": 43, "name": "Betsson"}},
                        {"bookmaker": {"id": 16, "name": "bet365"}},
                        {"bookmaker": {"id": 417, "name": "Betano.pe"}},
                    ]
                },
                "odds": [
                    {
                        "bookmakerId": 43,
                        "bettingType": "HOME_DRAW_AWAY",
                        "bettingScope": "FULL_TIME",
                        "odds": [
                            {"eventParticipantId": "b94Fok0s", "value": "1.44", "opening": "1.72", "active": True},
                            {"eventParticipantId": "jHMMqTUg", "value": "6.10", "opening": "4.60", "active": True},
                            {"eventParticipantId": None, "value": "5.40", "opening": "4.05", "active": True},
                        ],
                    },
                    {
                        "bookmakerId": 16,
                        "bettingType": "HOME_DRAW_AWAY",
                        "bettingScope": "FULL_TIME",
                        "odds": [
                            {"eventParticipantId": "b94Fok0s", "value": "1.45", "opening": "1.70", "active": True},
                            {"eventParticipantId": "jHMMqTUg", "value": "6.00", "opening": "4.75", "active": True},
                            {"eventParticipantId": None, "value": "5.25", "opening": "4.10", "active": True},
                        ],
                    },
                    {
                        "bookmakerId": 417,
                        "bettingType": "HOME_DRAW_AWAY",
                        "bettingScope": "FULL_TIME",
                        "odds": [
                            {"eventParticipantId": "b94Fok0s", "value": "1.50", "opening": "1.67", "active": True},
                            {"eventParticipantId": "jHMMqTUg", "value": "5.70", "opening": "4.50", "active": True},
                            {"eventParticipantId": None, "value": "5.40", "opening": "4.30", "active": True},
                        ],
                    },
                    {
                        "bookmakerId": 43,
                        "bettingType": "OVER_UNDER",
                        "bettingScope": "FULL_TIME",
                        "odds": [
                            {"selection": "OVER", "handicap": {"value": "2.5"}, "value": "1.27", "opening": "1.43", "active": True},
                            {"selection": "UNDER", "handicap": {"value": "2.5"}, "value": "3.80", "opening": "2.85", "active": True},
                            {"selection": "OVER", "handicap": {"value": "3.5"}, "value": "1.72", "opening": "2.05", "active": True},
                            {"selection": "UNDER", "handicap": {"value": "3.5"}, "value": "2.12", "opening": "1.78", "active": True},
                        ],
                    },
                    {
                        "bookmakerId": 43,
                        "bettingType": "BOTH_TEAMS_TO_SCORE",
                        "bettingScope": "FULL_TIME",
                        "odds": [
                            {"bothTeamsToScore": True, "value": "1.39", "opening": "1.44", "active": True},
                            {"bothTeamsToScore": False, "value": "2.95", "opening": "2.72", "active": True},
                        ],
                    },
                    {
                        "bookmakerId": 43,
                        "bettingType": "DOUBLE_CHANCE",
                        "bettingScope": "FULL_TIME",
                        "odds": [
                            {"eventParticipantId": "b94Fok0s", "value": "1.13", "opening": "1.21", "active": True},
                            {"eventParticipantId": "jHMMqTUg", "value": "2.85", "opening": "2.15", "active": True},
                            {"eventParticipantId": None, "value": "1.14", "opening": "1.24", "active": True},
                        ],
                    },
                    {
                        "bookmakerId": 43,
                        "bettingType": "DRAW_NO_BET",
                        "bettingScope": "FULL_TIME",
                        "odds": [
                            {"eventParticipantId": "b94Fok0s", "value": "1.19", "opening": "1.32", "active": True},
                            {"eventParticipantId": "jHMMqTUg", "value": "4.80", "opening": "3.45", "active": True},
                        ],
                    },
                ],
            }
        }
    }


def test_devig_probabilidades_normaliza_1x2_y_binario():
    dv = devig_probabilidades({"1": 1.45, "X": 5.40, "2": 6.00})
    probs = dv["probs"]
    assert abs(sum(probs.values()) - 1.0) < 1e-5
    assert probs["1"] > probs["X"] > probs["2"]
    assert dv["overround_pct"] > 0.0

    dv_bin = devig_probabilidades({"over_2.5": 1.90, "under_2.5": 1.90})
    assert abs(dv_bin["probs"]["over_2.5"] - 0.5) < 1e-6
    assert abs(dv_bin["overround_pct"] - 5.26) < 0.05


def test_extraer_cuotas_graphql_prioriza_betsson_mapea_ja_jb_y_calcula_consenso():
    cli = ClienteFlashscore()
    payload = _payload_graphql_ejemplo()

    def fake_get(url, **kwargs):
        if "pq_graphql" in url:
            return SimpleNamespace(status_code=200, text=json.dumps(payload))
        return SimpleNamespace(status_code=404, text="")

    with patch.object(cli, "_get", side_effect=fake_get):
        res = cli.extraer_cuotas(
            "pItlFEBk",
            home_participant_id="b94Fok0s",
            away_participant_id="jHMMqTUg",
        )

    assert res["casa"] == "Betsson"
    assert res["fuente_cuotas"] == "flashscore_graphql"
    assert res["n_casas"] == 3
    assert res["local"] == 1.44
    assert res["empate"] == 5.40
    assert res["visitante"] == 6.10
    assert res["1x"] == 1.13
    assert res["x2"] == 2.85
    assert res["12"] == 1.14
    assert res["mercados"]["over_2.5"] == 1.27
    assert res["mercados"]["under_2.5"] == 3.80
    assert res["mercados"]["btts_si"] == 1.39
    assert res["mercados"]["btts_no"] == 2.95
    assert res["mercados"]["dnb_local"] == 1.19
    assert res["dropping_odds"] is True  # 1.72 -> 1.44 (-16.28% >= 10%)
    assert res["pct_drop"] >= 16.0
    # Consenso mediano entre 1.44, 1.45, 1.50 -> 1.45
    assert res["consenso_mercado"]["local"] == 1.45
    assert "1x2" in res["prob_mercado_devig"]
    assert "ou_2.5" in res["prob_mercado_devig"]
    assert "btts" in res["prob_mercado_devig"]
    assert abs(sum(res["prob_mercado_devig"]["1x2"]["probs"].values()) - 1.0) < 1e-5


def test_extraer_cuotas_fallback_fotmob_match_odds():
    cli = ClienteFlashscore()
    fm_payload = {
        "odds": {
            "matchfactMarkets": [
                {
                    "choices": [
                        {"name": "1", "decimal": "1.55"},
                        {"name": "x", "decimal": "3.75"},
                        {"name": "2", "decimal": "4.80"},
                    ]
                }
            ]
        }
    }

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(fm_payload).encode("utf-8")

    with (
        patch.object(cli, "_get", return_value=SimpleNamespace(status_code=404, text="")),
        patch("urllib.request.urlopen", return_value=_FakeResp()),
    ):
        res = cli.extraer_cuotas("", id_fotmob=6356390)

    assert res["casa"] == "FotMob Odds"
    assert res["fuente_cuotas"] == "fotmob_match_odds"
    assert res["local"] == 1.55
    assert res["empate"] == 3.75
    assert res["visitante"] == 4.80
    assert abs(sum(res["prob_mercado_devig"]["1x2"]["probs"].values()) - 1.0) < 1e-5


def test_buscar_evento_programado_feed_diario_resuelve_ja_jb():
    cli = ClienteFlashscore()
    cli._CACHE_FEED_DIARIO.clear()
    feed_fake = (
        "ZA÷CHILE: Copa Chile~AA÷pMUKiJe2¬AD÷1795557600¬AB÷1¬AE÷Coquimbo¬JA÷CMDwU4J0¬AF÷Union La Calera¬JB÷6PAVVQll~"
        "ZA÷GEORGIA: Erovnuli Liga~AA÷2ys7eyko¬AD÷1795532400¬AB÷1¬AE÷Gagra¬JA÷Ycs73Bdc¬AF÷Dinamo Tbilisi¬JB÷tOpF1kR9~"
    )
    with patch.object(cli, "_get", return_value=SimpleNamespace(status_code=200, text=feed_fake)):
        ev = cli.buscar_evento_programado_feed_diario("Coquimbo Unido", "Unión La Calera", dias=(0,))

    assert ev is not None
    assert ev["id_flashscore"] == "pMUKiJe2"
    assert ev["home_participant_id"] == "CMDwU4J0"
    assert ev["away_participant_id"] == "6PAVVQll"


def test_ticket_valor_y_snapshot_usan_cuotas_reales_sin_nd(tmp_path):
    import decision_cobertura as dc
    import informe
    import evaluacion_pronosticos as ep

    # Matriz 5x5 válida simple
    mat = [[0.0] * 5 for _ in range(5)]
    mat[2][0] = 0.25
    mat[2][1] = 0.25
    mat[1][0] = 0.15
    mat[1][1] = 0.15
    mat[0][1] = 0.10
    mat[3][1] = 0.10
    analisis_fake = SimpleNamespace(
        metadatos={
            "equipo_local": "Bodø/Glimt",
            "equipo_visitante": "LASK",
            "competicion": "Champions League",
        },
        motor=SimpleNamespace(
            matriz_ft=mat,
            tasas=SimpleNamespace(lambda_local_ft=1.95, lambda_visitante_ft=0.95),
        ),
        mercados=[],
        selecciones_filtradas=[],
    )
    cuotas_reales = {
        "local": 1.44,
        "empate": 5.40,
        "visitante": 6.10,
        "1x": 1.13,
        "x2": 2.85,
        "12": 1.14,
        "casa": "Betsson",
        "n_casas": 7,
        "dropping_odds": False,
        "mercados": {
            "local": 1.44,
            "empate": 5.40,
            "visitante": 6.10,
            "1x": 1.13,
            "x2": 2.85,
            "12": 1.14,
            "over_2.5": 1.27,
            "under_2.5": 3.80,
            "btts_si": 1.39,
            "btts_no": 2.95,
        },
    }

    # Por defecto (usar_cuotas_en_ticket=False): no añade O/U 2.5 ni BTTS (idéntico a ebbe25e)
    filas_def = informe.construir_filas_ticket_valor(analisis_fake, cuotas_reales)
    assert not any("2.5 goles" in f["mercado"] for f in filas_def)
    assert not any("Ambos anotan" in f["mercado"] for f in filas_def)

    # Con usar_cuotas_en_ticket=True: expone O/U 2.5 y BTTS y subtítulo con casa real
    with dc.banderas_comportamiento(usar_cuotas_en_ticket=True):
        filas = informe.construir_filas_ticket_valor(analisis_fake, cuotas_reales)
        assert len(filas) >= 6
        assert all(f["cuota_flashscore_str"] != "N/D" for f in filas)
        assert any("2.5 goles" in f["mercado"] for f in filas)
        assert any("Ambos anotan" in f["mercado"] for f in filas)

        png_out = str(tmp_path / "ticket_valor_test.png")
        ruta = informe.generar_ticket_valor_png(analisis_fake, cuotas_reales, destino=png_out, id_flashscore="pItlFEBk")
        assert Path(ruta).exists() and Path(ruta).stat().st_size > 1000

    # Snapshot compatibilidad hacia atrás y hacia adelante
    snap_con = ep.SnapshotPronostico(
        id_trabajo="t_cuotas_1",
        equipos={"local": "Bodø/Glimt", "visitante": "LASK"},
        partido={"fecha": "2026-11-24"},
        generado_en="2026-11-24 08:00:00",
        publicado_en="2026-11-24 08:00:00",
        modelo={"nombre": "test"},
        datos_corte={},
        umbral_publicado=0.70,
        selecciones=[],
        selecciones_publicadas=[],
        referencia="",
        cuotas_mercado=cuotas_reales,
    )
    d_con = snap_con.a_dict()
    assert d_con["cuotas_mercado"]["casa"] == "Betsson"

    snap_sin = ep.SnapshotPronostico(
        id_trabajo="t_cuotas_0",
        equipos={"local": "A", "visitante": "B"},
        partido={"fecha": "2026-11-24"},
        generado_en="2026-11-24 08:00:00",
        publicado_en="2026-11-24 08:00:00",
        modelo={"nombre": "test"},
        datos_corte={},
        umbral_publicado=0.70,
        selecciones=[],
        selecciones_publicadas=[],
        referencia="",
    )
    assert "cuotas_mercado" not in snap_sin.a_dict()
