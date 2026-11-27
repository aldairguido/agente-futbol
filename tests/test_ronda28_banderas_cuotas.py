"""
tests/test_ronda28_banderas_cuotas.py
───────────────────────────────────────────────────────────────────────────────
Pruebas de Ronda 28:
  1. Tres banderas auxiliares en decision_cobertura.py:
     - usar_cuotas_mercado_captura (default True)
     - usar_cuotas_en_ticket (default False)
     - usar_cierre_remoto_cuotas_en_evaluar (default False)
  2. Con usar_cuotas_mercado_captura=False no se hace ninguna consulta de cuotas
     y la salida es idéntica a ebbe25e.
  3. Con usar_cuotas_en_ticket=False (default), aunque se capturen cuotas en
     cuotas_mercado para el snapshot, el Ticket de Valor, el párrafo de valor y
     el Veredicto salen idénticos a ebbe25e (sin cuotas ni EV reales, sin O/U 2.5 ni BTTS).
  4. Con usar_cierre_remoto_cuotas_en_evaluar=False (default), /evaluar no consulta
     cierre remoto ni reporta CLV.
  5. Pruebas de fallo: Flashscore y FotMob caídos, timeout, JSON malformado y partido
     sin emparejar -> /mercados termina bien, sin cuotas (None, nunca 0), latencia
     acotada y sin excepciones visibles al usuario.
"""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_cobertura as dc
import evaluacion_pronosticos as ep
from fuente_flashscore import ClienteFlashscore
import informe as inf_mod
import motor_mercados as mm
import servicio_mercados as sm


def _cargar_fixture_londrina_nautico() -> tuple[dict, dict]:
    fx = json.loads((ROOT / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    return fx["londrina"], fx["nautico"]


def _contexto_resuelto_ln(doc_l: dict, doc_v: dict) -> dict:
    from datetime import datetime, timedelta, timezone

    manana_utc = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT20:00:00Z")
    f_ultimo = max(
        (str(p.get("fecha") or "")[:10] for p in (doc_l.get("partidos") or []) if p.get("fecha")),
        default="2026-11-01",
    )
    return {
        "equipo_local": "Londrina",
        "equipo_visitante": "Náutico",
        "doc_local": doc_l,
        "doc_visitante": doc_v,
        "contexto": {
            "competicion": "Série C",
            "pais_iso": "BRA",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
            "fecha_partido": f_ultimo,
            "fecha_corte": f_ultimo,
            "hora_inicio_partido": manana_utc,
            "fixture_confirmado": True,
            "id_partido_fotmob": "9999991",
            "coherencia_competitiva": {"coherente": True, "estado": "MISMA_COMPETICION"},
        },
    }


def test_r28_defectos_y_aislamiento_tres_banderas_auxiliares():
    assert dc._BANDERAS_AUXILIARES_DEFECTO["usar_cuotas_mercado_captura"] is True
    assert dc._BANDERAS_AUXILIARES_DEFECTO["usar_cuotas_en_ticket"] is False
    assert dc._BANDERAS_AUXILIARES_DEFECTO["usar_cierre_remoto_cuotas_en_evaluar"] is False

    assert dc.bandera_activa("usar_cuotas_mercado_captura") is True
    assert dc.bandera_activa("usar_cuotas_en_ticket") is False
    assert dc.bandera_activa("usar_cierre_remoto_cuotas_en_evaluar") is False

    # activar_todas=True NO altera las 3 banderas auxiliares
    with dc.banderas_comportamiento(activar_todas=True):
        assert dc.bandera_activa("usar_cuotas_mercado_captura") is True
        assert dc.bandera_activa("usar_cuotas_en_ticket") is False
        assert dc.bandera_activa("usar_cierre_remoto_cuotas_en_evaluar") is False


def _aislar_offline_aux(monkeypatch) -> None:
    monkeypatch.setattr("banco_historico.guardar_en_banco_no_bloqueante", lambda *a, **k: None)
    monkeypatch.setattr(
        "cuadro_h2h_2024_2026.generar_cuadro_h2h_desde_analisis",
        lambda *a, **k: {"ruta": None, "n_duelos": 0, "estado": "Sin enfrentamientos directos en 2024–2026", "duelos": []},
    )
    monkeypatch.setattr("utilidades_seguridad.obtener_sello_build", lambda *a, **k: "build test-r28 · 2026-11-24 00:00")


def test_r28_captura_false_no_hace_ninguna_consulta_de_cuotas(tmp_path, monkeypatch):
    doc_l, doc_v = _cargar_fixture_londrina_nautico()
    monkeypatch.setattr(sm, "resolver_enfrentamiento", lambda *a, **k: _contexto_resuelto_ln(doc_l, doc_v))
    _aislar_offline_aux(monkeypatch)

    llamadas_red = []

    def prohibido(*args, **kwargs):
        llamadas_red.append((args, kwargs))
        raise AssertionError("No debe consultarse ninguna fuente de cuotas con usar_cuotas_mercado_captura=False")

    monkeypatch.setattr(ClienteFlashscore, "extraer_cuotas", prohibido)
    monkeypatch.setattr(ClienteFlashscore, "buscar_evento_programado_feed_diario", prohibido)
    monkeypatch.setattr(ClienteFlashscore, "_extraer_cuotas_fotmob_match_odds", prohibido)

    with dc.banderas_comportamiento(usar_cuotas_mercado_captura=False, usar_cuotas_en_ticket=False):
        prep = sm.preparar_analisis("Londrina", "Náutico", directorio_salida=str(tmp_path))

    assert llamadas_red == []
    assert prep.get("abstenido") is not True
    assert "cuotas_mercado" not in prep["analisis"].metadatos
    assert "todo Stake del Ticket de Valor es hipotético a cuota mínima, sin validar" in (
        prep.get("analisis_texto_3_parrafos") or ""
    )
    filas_tv = inf_mod.construir_filas_ticket_valor(prep["analisis"], None)
    assert all("Cuota Mín. +EV:" in f["recomendacion"] and f["cuota_flashscore"] is None for f in filas_tv)


def test_r28_defecto_captura_en_snapshot_pero_ticket_y_veredicto_identicos_a_ebbe25e(tmp_path, monkeypatch):
    doc_l, doc_v = _cargar_fixture_londrina_nautico()
    monkeypatch.setattr(sm, "resolver_enfrentamiento", lambda *a, **k: _contexto_resuelto_ln(doc_l, doc_v))
    _aislar_offline_aux(monkeypatch)

    cuotas_mock = {
        "local": 1.85,
        "empate": 3.40,
        "visitante": 4.20,
        "1x": 1.20,
        "x2": 1.92,
        "12": 1.30,
        "casa": "Betsson",
        "fuente_cuotas": "flashscore_graphql",
        "id_flashscore_cuotas": "pItlFEBk",
        "n_casas": 7,
        "capturado_en_utc": "2099-11-30T10:00:00Z",
        "dropping_odds": False,
        "pct_drop": 0.0,
        "mercados": {
            "local": 1.85,
            "empate": 3.40,
            "visitante": 4.20,
            "over_2.5": 2.05,
            "under_2.5": 1.75,
            "btts_si": 1.90,
            "btts_no": 1.85,
        },
    }

    # 1) Salida de referencia con captura=False (equivalente exacto a ebbe25e sin cuotas)
    dir_ref = tmp_path / "ref_sin_cuotas"
    dir_ref.mkdir()
    with dc.banderas_comportamiento(usar_cuotas_mercado_captura=False, usar_cuotas_en_ticket=False):
        prep_ref = sm.preparar_analisis("Londrina", "Náutico", directorio_salida=str(dir_ref))

    # 2) Salida con banderas por defecto (captura=True, ticket=False) y cuotas reales disponibles
    monkeypatch.setattr(
        ClienteFlashscore,
        "buscar_evento_programado_feed_diario",
        lambda self, *a, **k: {"id_flashscore": "pItlFEBk", "home_participant_id": "H1", "away_participant_id": "A1"},
    )
    monkeypatch.setattr(ClienteFlashscore, "extraer_cuotas", lambda self, *a, **k: dict(cuotas_mock))

    dir_def = tmp_path / "def_con_captura"
    dir_def.mkdir()
    prep_def = sm.preparar_analisis("Londrina", "Náutico", directorio_salida=str(dir_def))

    # El snapshot / metadatos SÍ conserva cuotas_mercado
    assert prep_def["analisis"].metadatos["cuotas_mercado"]["casa"] == "Betsson"

    # Pero el párrafo de valor, el informe .md y las filas del Ticket de Valor son IDÉNTICOS a ebbe25e
    assert prep_def["analisis_texto_3_parrafos"] == prep_ref["analisis_texto_3_parrafos"]
    filas_tv_def = inf_mod.construir_filas_ticket_valor(prep_def["analisis"], None)
    assert all(f["cuota_flashscore"] is None and f["cuota_flashscore_str"] == "N/D" for f in filas_tv_def)
    assert not any("2.5 goles" in f["mercado"] or "Ambos anotan" in f["mercado"] for f in filas_tv_def)


def test_r28_evaluar_por_defecto_no_consulta_cierre_remoto_ni_reporta_clv(monkeypatch):
    llamadas_cierre = []

    def prohibido_cierre(*args, **kwargs):
        llamadas_cierre.append((args, kwargs))
        raise AssertionError("No debe consultar cierre remoto con usar_cierre_remoto_cuotas_en_evaluar=False")

    monkeypatch.setattr(ClienteFlashscore, "extraer_cuotas", prohibido_cierre)

    snap_oos = {
        "id_trabajo": "oos_r28_1",
        "partido": {"local": "Bodø/Glimt", "visitante": "LASK", "fecha": "2026-11-24"},
        "fixture_confirmado": True,
        "hora_inicio_partido": "2026-11-24T17:45:00Z",
        "telegram_envio": {"message_id": 1, "message_date": 1795500000, "message_date_utc": "2026-11-24T06:00:00Z"},
        "es_oos_valido": True,
        "es_canonica": True,
        "estado_canonico": "canonica",
        "cuotas_cierre": {"local": 1.45, "empate": 5.00, "visitante": 6.20},
        "modo_sombra": {
            "comparaciones": [
                {"mercado": "Ganador del partido", "evento": "1", "periodo": "FT", "equipo": "Bodø/Glimt", "linea": None, "prob_base": 0.68},
                {"mercado": "Ganador del partido", "evento": "X", "periodo": "FT", "equipo": None, "linea": None, "prob_base": 0.17},
                {"mercado": "Ganador del partido", "evento": "2", "periodo": "FT", "equipo": "LASK", "linea": None, "prob_base": 0.15},
            ]
        },
        "cuotas_mercado": {
            "local": 1.65,
            "empate": 4.50,
            "visitante": 5.50,
            "casa": "Betsson",
            "id_flashscore_cuotas": "pItlFEBk",
            "capturado_en_utc": "2026-11-24T06:00:00Z",
            "mercados": {"local": 1.65, "empate": 4.50, "visitante": 5.50},
        },
    }
    resultados = {
        "Bodø/Glimt vs LASK": ep.ResultadoPartido(
            id_partido="6106317", estado="finalizado", local="Bodø/Glimt", visitante="LASK", goles_local=2, goles_visitante=1
        )
    }

    # Por defecto (usar_cierre_remoto_cuotas_en_evaluar=False): 0 llamadas remotas y CLV=None en evaluar_tanda y resumen_telegram
    inf_ev = ep.evaluar_tanda([snap_oos], resultados)
    res_def = inf_ev.resumen_mercado_y_clv
    txt_tg = ep.resumen_telegram(inf_ev)
    assert llamadas_cierre == []
    assert res_def["n_partidos_oos_con_cuotas"] == 1
    assert res_def["n_partidos_oos_con_clv"] == 0
    assert res_def["clv_devig_medio_pct"] is None
    assert "usar_cierre_remoto_cuotas_en_evaluar=False" in res_def["nota_metodologica"]
    assert "CLV" not in txt_tg


@pytest.mark.parametrize(
    "modo_fallo",
    ["ambos_caidos", "timeout", "json_malformado_y_ceros", "partido_sin_emparejar"],
)
def test_r28_fallos_red_timeout_json_y_sin_emparejar_terminan_bien_con_none(
    modo_fallo: str, tmp_path, monkeypatch
):
    ClienteFlashscore._CACHE_FEED_DIARIO.clear()
    doc_l, doc_v = _cargar_fixture_londrina_nautico()
    monkeypatch.setattr(sm, "resolver_enfrentamiento", lambda *a, **k: _contexto_resuelto_ln(doc_l, doc_v))
    _aislar_offline_aux(monkeypatch)

    if modo_fallo == "ambos_caidos":
        def fake_fs_get(self, url, **kw):
            raise ConnectionError("Flashscore 503 Service Unavailable")

        def fake_urlopen(req, timeout=None):
            raise ConnectionError("FotMob 503 Service Unavailable")

    elif modo_fallo == "timeout":
        def fake_fs_get(self, url, **kw):
            time.sleep(0.05)
            raise socket.timeout("timed out")

        def fake_urlopen(req, timeout=None):
            time.sleep(0.05)
            raise TimeoutError("FotMob timed out")

    elif modo_fallo == "json_malformado_y_ceros":
        def fake_fs_get(self, url, **kw):
            if "f_1_" in url:
                return SimpleNamespace(
                    status_code=200,
                    text="ZA÷BRA~AA÷badId123¬AB÷1¬AE÷Londrina¬JA÷H1¬AF÷Nautico¬JB÷A1~" + ("x" * 220),
                )
            if "pq_graphql" in url:
                return SimpleNamespace(status_code=200, text="{malformed_json_truncated...")
            return SimpleNamespace(status_code=200, text="<table><tr><td>0</td><td>0.0</td><td>0</td></tr></table>")

        class _BadFotMobResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"odds":{"matchfactMarkets":[{"selections":[{"name":"1","decimal":"0"},{"name":"x","decimal":"0.0"},{"name":"2","decimal":"-1"}]}]}}'

        def fake_urlopen(req, timeout=None):
            return _BadFotMobResp()

    else:  # partido_sin_emparejar
        def fake_fs_get(self, url, **kw):
            if "f_1_" in url:
                return SimpleNamespace(
                    status_code=200,
                    text="ZA÷ENG~AA÷other123¬AB÷1¬AE÷Arsenal¬JA÷H9¬AF÷Chelsea¬JB÷A9~" + ("x" * 220),
                )
            return SimpleNamespace(status_code=404, text="")

        class _EmptyFotMobResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"odds":{"matchfactMarkets":[]}}'

        def fake_urlopen(req, timeout=None):
            return _EmptyFotMobResp()

    monkeypatch.setattr(ClienteFlashscore, "_get", fake_fs_get)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    # También probar directamente extraer_cuotas para verificar que devuelve None (nunca 0)
    cli = ClienteFlashscore(timeout=0.2)
    t0 = time.perf_counter()
    c_direct = cli.extraer_cuotas("", id_fotmob="9999991", equipo_local="Londrina", equipo_visitante="Náutico")
    dt_direct = time.perf_counter() - t0
    assert dt_direct < 1.5
    assert c_direct["local"] is None
    assert c_direct["empate"] is None
    assert c_direct["visitante"] is None
    assert c_direct["casa"] is None

    # Y probar preparar_analisis completo (incluso con usar_cuotas_en_ticket=True): sin excepciones ni 0s
    out_dir = tmp_path / modo_fallo
    out_dir.mkdir()
    t1 = time.perf_counter()
    with dc.banderas_comportamiento(usar_cuotas_mercado_captura=True, usar_cuotas_en_ticket=True):
        prep = sm.preparar_analisis("Londrina", "Náutico", directorio_salida=str(out_dir))
    dt_prep = time.perf_counter() - t1

    assert dt_prep < 15.0
    assert prep.get("abstenido") is not True
    assert "cuotas_mercado" not in prep["analisis"].metadatos
    texto_3p = prep.get("analisis_texto_3_parrafos") or ""
    assert "Traceback" not in texto_3p and "Error" not in texto_3p
    filas_tv = inf_mod.construir_filas_ticket_valor(prep["analisis"], None)
    assert all(f["cuota_flashscore"] is None and f["cuota_flashscore"] != 0 for f in filas_tv)
