"""Tests de verificación Ronda 19 (fix/ronda-18-tickets)."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import evaluacion_pronosticos as ep
import motor_mercados as mm
import servicio_mercados as sm


_CLAVES_FT = (
    ("xg", 1.35, 0.95),
    ("posesion_pct", 54.0, 46.0),
    ("remates_totales", 13.0, 9.0),
    ("remates_puerta", 5.0, 3.0),
    ("corners", 5.0, 4.0),
    ("faltas", 12.0, 11.0),
    ("tarjetas_amarillas", 2.0, 2.0),
    ("xgot", 1.25, 0.85),
    ("duelos_ganados", 49.0, 44.0),
    ("intercepciones", 9.0, 8.0),
    ("despejes", 17.0, 20.0),
    ("saques_puerta", 7.0, 9.0),
)


def _fila_partido_completo(
    idx: int,
    *,
    fuente: str,
    gf1: int = 1,
    gf2: int = 0,
    gc1: int = 0,
    gc2: int = 1,
    con_minutos: bool = False,
) -> dict:
    filas_ft = [{"clave": k, "equipo": eq, "rival": riv} for k, eq, riv in _CLAVES_FT]
    filas_1t = [
        {"clave": "xg", "equipo": 0.65, "rival": 0.45},
        {"clave": "posesion_pct", "equipo": 53.0, "rival": 47.0},
        {"clave": "corners", "equipo": 2.0, "rival": 2.0},
        {"clave": "faltas", "equipo": 6.0, "rival": 5.0},
        {"clave": "tarjetas_amarillas", "equipo": 1.0, "rival": 1.0},
        {"clave": "xgot_enfrentado", "equipo": float(gc1), "rival": float(gf1)},
        {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
    ]
    filas_2t = [
        {"clave": "xg", "equipo": 0.70, "rival": 0.50},
        {"clave": "posesion_pct", "equipo": 55.0, "rival": 45.0},
        {"clave": "corners", "equipo": 3.0, "rival": 2.0},
        {"clave": "faltas", "equipo": 6.0, "rival": 6.0},
        {"clave": "tarjetas_amarillas", "equipo": 1.0, "rival": 1.0},
        {"clave": "xgot_enfrentado", "equipo": float(gc2), "rival": float(gf2)},
        {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
    ]
    d = {
        "id_partido": f"{fuente}_{idx}",
        "fecha": f"2026-10-{min(28, 1 + idx):02d}",
        "torneo": "BRAZIL: Serie A" if fuente == "flashscore" else "Série A",
        "rival": f"Rival_{idx}",
        "condicion": "casa" if idx % 2 == 0 else "fuera",
        "marcador": {"equipo": gf1 + gf2, "rival": gc1 + gc2},
        "goles_equipo": gf1 + gf2,
        "goles_rival": gc1 + gc2,
        "resumen_claves": {
            "FT": {
                "equipo": [f["clave"] for f in filas_ft],
                "rival": [f["clave"] for f in filas_ft],
            },
            "1T": {
                "equipo": [f["clave"] for f in filas_1t],
                "rival": [f["clave"] for f in filas_1t],
            },
            "2T": {
                "equipo": [f["clave"] for f in filas_2t],
                "rival": [f["clave"] for f in filas_2t],
            },
        },
        "periodos": {
            "FT": {"filas": filas_ft},
            "1T": {"filas": filas_1t},
            "2T": {"filas": filas_2t},
        },
    }
    if con_minutos:
        d["eventos_gol"] = [{"minuto": 24 if gf1 else 70, "minuto_str": "24'", "lado": "equipo"}]
    return d


def _doc_equipo_sintetico(nombre: str, *, fuente: str, con_minutos: bool = False) -> dict:
    return {
        "equipo": {"id": f"id_{nombre}", "nombre": nombre, "pais_iso": "BRA"},
        "fuente": fuente,
        "pais_iso": "BRA",
        "competicion_objetivo": "Serie A",
        "generado_en": f"2026-11-18T05:00:00Z_{fuente}",
        "partidos": [
            _fila_partido_completo(i, fuente=fuente, con_minutos=con_minutos)
            for i in range(15)
        ],
    }


def test_bloque_1_fuente_por_equipo_y_firma_datos_en_snapshot_y_ledger() -> None:
    dl = _doc_equipo_sintetico("Corinthians", fuente="flashscore", con_minutos=False)
    dv = _doc_equipo_sintetico("Botafogo", fuente="fotmob", con_minutos=True)

    res_fake = {
        "equipo_local": "Corinthians",
        "equipo_visitante": "Botafogo",
        "doc_local": dl,
        "doc_visitante": dv,
        "meta_local": None,
        "meta_visitante": None,
        "contexto": {
            "fecha_partido": "2026-11-20",
            "hora_inicio_partido": "2026-11-20T22:00:00Z",
            "fixture_confirmado": True,
            "fuente_fecha": "fotmob_fixture",
            "competicion": "Série A (Brasil)",
            "pais_iso": "BRA",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
            "origen_condicion": "fixture_real",
            "sede": "Neo Química Arena",
            "id_partido_objetivo": "5109999",
        },
        "liga_solicitada": {"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
        "estado_identidad": "verificada",
        "motivo_identidad": "verificada en test",
        "identidad_verificada": True,
        "criterio_desempate": None,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        ruta_ledger = os.path.join(tmpdir, "ledger_oos.jsonl")
        ruta_snaps = os.path.join(tmpdir, "snapshots")

        with patch.object(sm, "resolver_enfrentamiento", return_value=res_fake):
            prep = sm.preparar_analisis(
                "Corinthians",
                "Botafogo",
                umbral=0.70,
                directorio_salida=tmpdir,
                liga={"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
            )

        assert not prep.get("abstenido")
        an = prep["analisis"]
        meta = an.metadatos
        assert meta.get("fuente_local") == "flashscore"
        assert meta.get("fuente_visitante") == "fotmob"
        firma_esp = sm.calcular_firma_datos(dl, dv)
        assert isinstance(firma_esp, str) and len(firma_esp) == 12
        assert meta.get("firma_datos") == firma_esp

        ep.adjuntar_modo_sombra_preparado(prep)
        tg_meta = {
            "message_id": 101,
            "message_date": 1795066740,
            "message_date_utc": "2026-11-18T05:39:00Z",
        }
        prep["telegram_meta"] = tg_meta
        snap = ep.snapshot_desde_analisis(
            prep,
            publicado_en="2026-11-18T05:39:00Z",
            modo_sombra=prep.get("modo_sombra"),
            telegram_meta=tg_meta,
        )
        snap_dict = snap.a_dict()
        assert snap_dict["fuente_local"] == "flashscore"
        assert snap_dict["fuente_visitante"] == "fotmob"
        assert snap_dict["firma_datos"] == firma_esp
        assert snap_dict["datos_corte"]["fuente_local"] == "flashscore"
        assert snap_dict["datos_corte"]["fuente_visitante"] == "fotmob"
        assert snap_dict["datos_corte"]["firma_datos"] == firma_esp

        ruta_snap = ep.guardar_snapshot(snap, directorio=ruta_snaps)
        with open(ruta_snap, "r", encoding="utf-8") as fh:
            guardado = json.load(fh)
        assert guardado["fuente_local"] == "flashscore"
        assert guardado["fuente_visitante"] == "fotmob"
        assert guardado["firma_datos"] == firma_esp

        res_pre = ep.registrar_sombra_prepartido_en_ledger_oos(
            prep,
            ruta_snapshot=ruta_snap,
            ruta_ledger=ruta_ledger,
        )
        assert res_pre["registrado"] is True

        # Caso directo con fuente en datos_corte vía registrar_en_ledger_oos
        res_dir = ep.registrar_en_ledger_oos(
            {
                "id_trabajo": "directo_b1",
                "id_partido": "9990001",
                "partido": "Club A vs Club B",
                "fecha": "2026-11-21",
                "estado_registro": "liquidado",
                "datos_corte": {
                    "fuente_local": "fotmob",
                    "fuente_visitante": "flashscore",
                    "firma_datos": "abcdef012345",
                },
                "lineas": [],
                "resumen": {"aciertos": 0, "fallos": 0, "n_evaluables": 0, "brier": None},
            },
            ruta_ledger=ruta_ledger,
        )
        assert res_dir["registrado"] is True

        # Caso liquidado desde snapshot en directorio
        res_obj = ep.ResultadoPartido(
            id_partido="5109999",
            estado="finalizado",
            local="Corinthians",
            visitante="Botafogo",
            inicio_utc="2026-11-20T22:00:00Z",
            goles_local=1,
            goles_visitante=1,
            goles_local_1t=1,
            goles_visitante_1t=0,
            corners_local={"FT": 5},
            corners_visitante={"FT": 4},
            amarillas_local={"FT": 2},
            amarillas_visitante={"FT": 2},
        )
        res_liq = ep.liquidar_sombras_en_ledger_oos(
            [guardado],
            {"Corinthians vs Botafogo": res_obj},
            ruta_ledger=ruta_ledger,
        )
        assert res_liq["n_liquidados_nuevos"] == 1, res_liq

        filas = ep.cargar_ledger_oos(ruta_ledger)
        assert len(filas) == 3
        assert filas[0]["fuente_local"] == "flashscore"
        assert filas[0]["fuente_visitante"] == "fotmob"
        assert filas[0]["firma_datos"] == firma_esp
        assert filas[1]["fuente_local"] == "fotmob"
        assert filas[1]["fuente_visitante"] == "flashscore"
        assert filas[1]["firma_datos"] == "abcdef012345"
        assert filas[2]["estado_registro"] == "liquidado"
        assert filas[2]["fuente_local"] == "flashscore"
        assert filas[2]["fuente_visitante"] == "fotmob"
        assert filas[2]["firma_datos"] == firma_esp

        verif = ep.verificar_cadena_ledger_oos(ruta_ledger)
        assert verif["ok"] is True, verif

        # Caso abstenido también conserva fuente_local, fuente_visitante y firma_datos
        res_abs = dict(res_fake)
        res_abs["doc_visitante"] = _doc_equipo_sintetico("Botafogo", fuente="fotmob", con_minutos=True)
        for p in res_abs["doc_visitante"]["partidos"]:
            p["periodos"] = {"FT": {"filas": [{"clave": "remates_fuera", "equipo": 1.0, "rival": 1.0}]}}
            p["resumen_claves"] = {"FT": {"equipo": ["remates_fuera"], "rival": ["remates_fuera"]}}
        with patch.object(sm, "resolver_enfrentamiento", return_value=res_abs):
            prep_abs = sm.preparar_analisis(
                "Corinthians",
                "Botafogo",
                umbral=0.70,
                directorio_salida=tmpdir,
                liga={"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
            )
        assert prep_abs.get("abstenido") is True
        snap_abs = ep.snapshot_desde_analisis(prep_abs).a_dict()
        assert snap_abs["fuente_local"] == "flashscore"
        assert snap_abs["fuente_visitante"] == "fotmob"
        assert snap_abs["firma_datos"] == sm.calcular_firma_datos(dl, res_abs["doc_visitante"])
