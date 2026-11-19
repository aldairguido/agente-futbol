from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _dummy_partidos(eq: str, riv: str, n: int = 15) -> list[dict]:
    pts = []
    for i in range(n):
        cond = "casa" if i % 2 == 0 else "fuera"
        pts.append({
            "id_partido": f"m_{eq[:3]}_{i}",
            "fecha": f"2026-10-{max(1, 28 - i):02d}",
            "torneo": "Serie A",
            "local": eq if cond == "casa" else riv,
            "visitante": riv if cond == "casa" else eq,
            "condicion": cond,
            "goles_favor": 2 if i % 2 == 0 else 1,
            "goles_contra": 1 if i % 3 == 0 else 0,
            "metricas": {
                "xg": 1.6, "xgot": 1.5, "remates_totales": 13, "remates_puerta": 5,
                "posesion_pct": 54.0, "corners": 5.5, "faltas": 12.0, "tarjetas_amarillas": 2.0,
            },
        })
    return pts


def test_punto5_hora_inicio_desde_fotmob_allfixtures_y_flashscore_y_no_oos(tmp_path: Path) -> None:
    import evaluacion_pronosticos as ep
    import fuente_flashscore as ff
    import fuentes_xg as fx
    import servicio_mercados as sm

    # 1. _normalizar_futuros preserva utcTime completa (UTC -> America/Lima UTC-5)
    futuros = fx._normalizar_futuros([
        {
            "id": 5103705,
            "status": {"finished": False, "utcTime": "2026-11-19T22:30:00.000Z"},
            "home": {"id": 10276, "name": "Vasco da Gama"},
            "away": {"id": 8702, "name": "Internacional"},
            "tournament": {"name": "Série A"},
        }
    ])
    assert len(futuros) == 1
    assert futuros[0]["utcTime"] == "2026-11-19T22:30:00.000Z"
    assert futuros[0]["zona_origen"] == "UTC"
    assert futuros[0]["fuente_hora"] == "fotmob_fixture"

    doc_l_fm = {
        "fuente": "fotmob",
        "equipo": {"id": "10276", "nombre": "Vasco da Gama", "pais_iso": "BRA", "liga": "Serie A"},
        "partidos": _dummy_partidos("Vasco da Gama", "Rival"),
        "partidos_futuros": futuros,
    }
    doc_v_fm = {
        "fuente": "fotmob",
        "equipo": {"id": "8702", "nombre": "Internacional", "pais_iso": "BRA", "liga": "Serie A"},
        "partidos": _dummy_partidos("Internacional", "Otro"),
        "partidos_futuros": [],
    }
    ctx_fm = sm._metadatos_del_encuentro(doc_l_fm, doc_v_fm, "Vasco da Gama", "Internacional")
    assert ctx_fm["hora_inicio_partido"] == "2026-11-19T22:30:00Z"
    assert ctx_fm["hora_inicio_local"] == "2026-11-19 17:30 America/Lima"
    assert ctx_fm["etiqueta_fecha"] == "Partido: 2026-11-19 17:30 America/Lima"
    assert ctx_fm["fuente_hora_inicio"] == "fotmob_fixture"
    assert ctx_fm["zona_origen_hora_inicio"] == "UTC"

    # 2. Flashscore feed diario (f_1_0_0) y dc_1_{id} resuelven hora cuando el caché traía solo YYYY-MM-DD
    epoch_vasco = "1795127400"  # 2026-11-19T22:30:00Z
    resp_f = SimpleNamespace(
        status_code=200,
        content=b"x" * 200,
        text=f"SA÷1¬~AA÷VasInt01¬AD÷{epoch_vasco}¬AB÷1¬AE÷Vasco da Gama¬AF÷Internacional¬AG÷¬AH÷¬~",
    )
    resp_dc = SimpleNamespace(
        status_code=200,
        content=b"x" * 100,
        text=f"DA÷1¬DC÷{epoch_vasco}¬DD÷{epoch_vasco}¬",
    )
    cli_fs = ff.ClienteFlashscore(timeout=2)
    with patch.object(cli_fs, "_get", return_value=resp_dc):
        assert cli_fs.hora_inicio_desde_id("VasInt01") == "2026-11-19T22:30:00Z"
    with patch.object(cli_fs, "_get", return_value=resp_f):
        fx_fs = cli_fs.buscar_fixture_programado("Vasco da Gama", "Internacional", fecha_objetivo="2026-11-19")
    assert fx_fs is not None
    assert fx_fs["id_flashscore"] == "VasInt01"
    assert fx_fs["utcTime"] == "2026-11-19T22:30:00Z"

    ctx_sin_hora = {
        "id_partido": "5103705",
        "fecha_partido": "2026-11-19",
        "etiqueta_fecha": "Partido: 2026-11-19 (hora s/d, America/Lima)",
        "hora_inicio_partido": None,
        "hora_inicio_local": "2026-11-19 (hora s/d, America/Lima)",
        "fixture_confirmado": True,
        "fuente_fecha": "fixture",
    }
    with patch.object(cli_fs, "_get", return_value=resp_f):
        sm.resolver_hora_inicio_encuentro(
            ctx_sin_hora,
            equipo_local="Vasco da Gama",
            equipo_visitante="Internacional",
            cliente_fs=cli_fs,
        )
    assert ctx_sin_hora["hora_inicio_partido"] == "2026-11-19T22:30:00Z"
    assert ctx_sin_hora["hora_inicio_local"] == "2026-11-19 17:30 America/Lima"
    assert ctx_sin_hora["etiqueta_fecha"] == "Partido: 2026-11-19 17:30 America/Lima"
    assert ctx_sin_hora["fuente_hora_inicio"] == "flashscore"
    assert ctx_sin_hora["zona_origen_hora_inicio"] == "UTC"
    assert ctx_sin_hora["id_flashscore_partido"] == "VasInt01"

    # 3. Respaldo FotMob matchDetails cuando Flashscore no responde
    ctx_fm_det = {
        "id_partido": "5103705",
        "fecha_partido": "2026-11-19",
        "hora_inicio_partido": None,
        "fixture_confirmado": True,
        "fuente_fecha": "fixture",
    }
    cli_vacio = ff.ClienteFlashscore(timeout=1)
    with patch.object(cli_vacio, "_get", side_effect=RuntimeError("offline")):
        sm.resolver_hora_inicio_encuentro(
            ctx_fm_det,
            equipo_local="Vasco da Gama",
            equipo_visitante="Internacional",
            cliente_fs=cli_vacio,
            fetcher_fotmob=lambda pid: "2026-11-19T22:30:00.000Z" if pid == "5103705" else None,
        )
    assert ctx_fm_det["hora_inicio_partido"] == "2026-11-19T22:30:00Z"
    assert ctx_fm_det["fuente_hora_inicio"] == "fotmob_match_details"
    assert ctx_fm_det["zona_origen_hora_inicio"] == "UTC"

    # 4. Persistencia en snapshot y ledger OOS: con hora -> OOS válido; sin hora -> no-OOS
    motor_min = SimpleNamespace(
        tasas=SimpleNamespace(lambda_local_ft=1.52, lambda_visitante_ft=1.05),
        muestras={
            "local_general": SimpleNamespace(partidos_general=_dummy_partidos("Vasco", "R", 15)),
            "visitante_general": SimpleNamespace(partidos_general=_dummy_partidos("Inter", "R", 15)),
            "local_cond": SimpleNamespace(partidos_condicion=_dummy_partidos("Vasco", "R", 8)),
            "visitante_cond": SimpleNamespace(partidos_condicion=_dummy_partidos("Inter", "R", 8)),
        },
        recursivo={"aplicado": False, "traza_lambda": [], "traza_etapa_6": []},
        doc_local=doc_l_fm,
        doc_visitante=doc_v_fm,
    )
    an_ok = SimpleNamespace(
        metadatos={
            "equipo_local": "Vasco da Gama",
            "equipo_visitante": "Internacional",
            "competicion": "Série A",
            "fecha_partido": "2026-11-19",
            "generado_en": "2026-11-19 00:42",
            "fixture_confirmado": True,
            "fuente_fecha": "fixture",
            "hora_inicio_partido": ctx_sin_hora["hora_inicio_partido"],
            "fuente_hora_inicio": ctx_sin_hora["fuente_hora_inicio"],
            "zona_origen_hora_inicio": ctx_sin_hora["zona_origen_hora_inicio"],
            "fuente_local": "fotmob",
            "fuente_visitante": "fotmob",
            "firma_datos": "abc123",
        },
        parametros=SimpleNamespace(umbral=0.70, w_condicion=0.5, w_xg=0.5, distribucion_conteos="poisson"),
        motor=motor_min,
        mercados=[],
        selecciones_filtradas=[],
        abstenido=False,
    )
    prep_ok = {
        "id_trabajo": "r21_ok_hora",
        "analisis": an_ok,
        "contexto": ctx_sin_hora,
        "rutas": {"informe": "inf.md"},
        "telegram_meta": {"message_id": 5001, "message_date": 1795066920, "message_date_utc": "2026-11-19T05:42:00Z"},
        "modo_sombra": {
            "esquema": "sombra-v1", "publicado": "soloA", "version_base": "soloA", "version_sombra": "A+B",
            "lambdas_base": {"local": 1.52, "visitante": 1.05}, "lambdas_sombra": {"local": 1.52, "visitante": 1.05},
            "comparaciones": [], "n_selecciones_comparables": 0,
        },
    }
    snap_ok = ep.snapshot_desde_analisis(prep_ok, telegram_meta=prep_ok["telegram_meta"])
    d_ok = snap_ok.a_dict()
    assert d_ok["hora_inicio_partido"] == "2026-11-19T22:30:00Z"
    assert d_ok["fuente_hora_inicio"] == "flashscore"
    assert d_ok["zona_origen_hora_inicio"] == "UTC"
    assert d_ok["partido"]["hora_inicio_partido"] == "2026-11-19T22:30:00Z"
    assert d_ok["es_oos_valido"] is True and d_ok["es_canonica"] is True

    ruta_led = str(tmp_path / "ledger.jsonl")
    res_led_ok = ep.registrar_sombra_prepartido_en_ledger_oos(prep_ok, ruta_ledger=ruta_led)
    assert res_led_ok["registrado"] is True

    # Caso sin hora disponible -> queda como no-OOS
    ctx_none = {"fecha_partido": "2026-11-19", "hora_inicio_partido": None, "fixture_confirmado": True, "omitir_busqueda_hora_red": True}
    sm.resolver_hora_inicio_encuentro(ctx_none)
    assert ctx_none["hora_inicio_partido"] is None
    an_no = SimpleNamespace(**{**an_ok.__dict__, "metadatos": {**an_ok.metadatos, "hora_inicio_partido": None, "fuente_hora_inicio": "no_disponible"}})
    prep_no = {**prep_ok, "id_trabajo": "r21_sin_hora", "analisis": an_no, "contexto": ctx_none}
    snap_no = ep.snapshot_desde_analisis(prep_no, telegram_meta=prep_no["telegram_meta"])
    d_no = snap_no.a_dict()
    assert d_no["hora_inicio_partido"] is None
    assert d_no["es_oos_valido"] is False and d_no["estado_canonico"] == "no_oos"
    ep.registrar_sombra_prepartido_en_ledger_oos(prep_no, ruta_ledger=ruta_led)

    regs = ep.cargar_ledger_oos(ruta_led)
    assert len(regs) == 2
    assert regs[0]["hora_inicio_partido"] == "2026-11-19T22:30:00Z"
    assert regs[0]["fuente_hora_inicio"] == "flashscore"
    assert regs[0]["zona_origen_hora_inicio"] == "UTC"
    assert regs[0]["es_oos_valido"] is True and regs[0]["es_canonica"] is True
    assert regs[1]["hora_inicio_partido"] is None
    assert regs[1]["es_oos_valido"] is False and regs[1]["estado_canonico"] == "no_oos"


def test_punto6_prediccion_final_distingue_bajo_umbral_sensible_sec2_y_explica_veredicto() -> None:
    from types import SimpleNamespace
    import motor_inferencia as mi

    # 1. formatear_pick_segun_estado distingue sensible (en Sección 2) vs sensible (fuera) vs < umbral (no validado)
    txt_sec2 = mi.formatear_pick_segun_estado("Goles 2T: +0.5 Goles", 72.0, "sensible", en_seccion_2=True)
    assert "sensible" in txt_sec2.lower() and "ver Sección 2" in txt_sec2
    assert "no validado" not in txt_sec2.lower()

    txt_fuera = mi.formatear_pick_segun_estado("Goles 2T: +0.5 Goles", 72.0, "sensible", en_seccion_2=False)
    assert "fuera de Sección 2" in txt_fuera and "no validado" in txt_fuera.lower()
    assert "ver Sección 2" not in txt_fuera

    txt_bajo = mi.formatear_pick_segun_estado("Gana Vasco da Gama (1x2 Directo)", 62.0, "sensible")
    assert "por debajo del umbral" in txt_bajo.lower()
    assert "no validado" in txt_bajo.lower()
    assert "ver Sección 2" not in txt_bajo
    assert "sensible" not in txt_bajo.lower()

    # 2. Caso Vasco 62.0%: aunque exista un evento terminado en `_1` sensible en sec2 (ej. al_menos_1),
    # 1x2 Directo 62.0% NUNCA se etiqueta sensible ni remite a Sección 2, y explica diferencia vs Jugada #1 del Veredicto
    cb = {"sin_derrotas": 9, "victorias": 8, "n_partidos": 15}
    cc_none = {"sin_dato": True, "_meta": {"n_partidos_con_eventos": 0}}
    pat = {"posesion_pct": 55.0, "faltas": 11.5, "corners": 6.0, "P26_Poder_Cierre_2T": 0.55, "_muestra_etiqueta": "Doble ADN pond."}
    s_otro_1 = SimpleNamespace(evento="local_al_menos_1", periodo="FT", estado="sensible", probabilidad=0.78)
    s_1x_ok = SimpleNamespace(evento="1x", periodo="FT", estado="mantiene", probabilidad=0.84)
    motor_m = SimpleNamespace(_grupos_resumen={"mantiene": [s_1x_ok], "sensible": [s_otro_1]})

    txt_vasco = mi.generar_reporte_xai_telegram(
        "Vasco da Gama",
        "Internacional",
        pat,
        pat,
        {"local": 0.620, "empate": 0.220, "visita": 0.160},
        [],
        cb,
        cb,
        cc_none,
        cc_none,
        top5_podio=[
            {"etiqueta": "Doble Oportunidad 1X: Vasco da Gama o Empate", "evento": "1x", "probabilidad": 0.840, "estado": "mantiene"},
        ],
        selecciones_sec1=[s_1x_ok],
        selecciones_sec2=[s_otro_1],
        motor=motor_m,
    )
    linea_pred = [ln for ln in txt_vasco.splitlines() if "Predicción Final" in ln][0]
    assert "Por debajo del umbral (70%, no validado): Gana Vasco da Gama (1x2 Directo) (62.0%)" in linea_pred
    assert "ver Sección 2" not in linea_pred
    assert "sensible" not in linea_pred.lower()
    assert "Diferencia con el Veredicto" in txt_vasco
    assert "Doble Oportunidad 1X: Vasco da Gama o Empate" in txt_vasco
