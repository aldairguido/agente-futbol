"""Tests reales para el Bloque 6 (banco histórico, coherencia 1T+2T==FT, cobertura y hooks)."""
from __future__ import annotations

import copy
import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import auditoria_historica as ah
import banco_historico as bh
import evaluacion_pronosticos as ep
import servicio_mercados as sm

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

STATS_FLASHSCORE_TB: dict[str, dict[str, Any]] = {
    "FT": {
        "goles": [1, 2],
        "corners": [2, 3],
        "xg": [0.52, 1.85],
        "xgot": [1.41, 2.23],
        "goles_evitados": [0.23, 0.41],
        "remates_totales": [10, 12],
        "remates_puerta": [7, 6],
        "grandes_ocasiones": [2, 3],
        "posesion_pct": [53, 47],
        "faltas": [12, 14],
        "amarillas": [2, 0],
        "fueras_de_juego": [3, 0],
        "toques_area_rival": [23, 33],
        "xa_equipo": [0.63, 1.77],
    },
    "1T": {
        "goles": [1, 2],
        "corners": [1, 0],
        "xg": [0.07, 1.39],
        "xgot": [0.29, 1.90],
        "goles_evitados": [-0.10, -0.71],
        "remates_totales": [2, 5],
        "posesion_pct": [58, 42],
        "faltas": [6, 4],
    },
    "2T": {
        "goles": [0, 0],
        "corners": [1, 3],
        "xg": [0.45, 0.46],
        "xgot": [1.12, 0.33],
        "goles_evitados": [0.33, 1.12],
        "remates_totales": [8, 7],
        "posesion_pct": [48, 52],
        "faltas": [6, 10],
    },
}

STATS_FLASHSCORE_IF: dict[str, dict[str, Any]] = {
    "FT": {
        "goles": [3, 1],
        "corners": [4, 3],
        "xg": [2.35, 1.63],
        "xgot": [1.85, 1.39],
        "goles_evitados": [0.39, -1.15],
        "remates_totales": [11, 18],
        "remates_puerta": [5, 5],
        "grandes_ocasiones": [4, 4],
        "posesion_pct": [35, 65],
        "faltas": [10, 18],
        "amarillas": [2, 2],
    },
    "1T": {
        "goles": [1, 1],
        "corners": [2, 1],
        "xg": [0.31, 1.20],
        "xgot": [0.68, 0.84],
        "goles_evitados": [-0.16, -0.32],
        "remates_totales": [4, 10],
        "corners_check": None,
        "faltas": [5, 9],
        "amarillas": [1, 1],
    },
    "2T": {
        "goles": [2, 0],
        "corners": [2, 2],
        "xg": [2.04, 0.43],
        "xgot": [1.17, 0.55],
        "goles_evitados": [0.55, -0.83],
        "remates_totales": [7, 8],
        "grandes_ocasiones": [3, 0],
        "faltas": [5, 9],
        "amarillas": [1, 1],
    },
}


def _cargar_r5_y_ln() -> tuple[dict[str, Any], dict[str, Any]]:
    r5 = json.loads((FIXTURES_DIR / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))
    ln = json.loads((FIXTURES_DIR / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    return r5, ln


def test_catalogo_45_y_provenancia_fotmob_vs_flashscore() -> None:
    claves_catalogo = tuple(cl for _, cl, _, _ in bh.CATALOGO_45_METRICAS)
    claves_oficiales = tuple(cl for _, cl, _, _ in ah.LAS_45_METRICAS_OFICIALES)
    assert len(claves_catalogo) == 45
    assert claves_catalogo == claves_oficiales

    cat = bh.CATALOGO_PROVENANCIA_45
    assert set(cat.keys()) == set(claves_oficiales)
    # 3 métricas exclusivas de Flashscore que FotMob no publica
    for cl in ("tiros_libres", "errores_remate", "saques_puerta"):
        assert cat[cl]["en_fotmob"] is False
        assert cat[cl]["en_flashscore"] is True
    # Sub-métricas xG solo en FotMob (no mapeadas en Flashscore GraphQL)
    for cl in ("xg_juego_abierto", "xg_balon_parado", "xg_sin_penalti"):
        assert cat[cl]["en_fotmob"] is True
        assert cat[cl]["en_flashscore"] is False


def test_deduplicacion_y_roundtrip_sqlite_csv_sin_dato_nunca_cero(tmp_path: Path) -> None:
    r5, ln = _cargar_r5_y_ln()
    db_path = tmp_path / "banco_partidos.db"
    csv_path = tmp_path / "banco_partidos.csv"

    filas: list[dict[str, Any]] = []
    for eq in ("turkey", "belgium", "italy", "france"):
        filas.extend(bh.construir_filas_desde_documento(r5["docs"][eq], equipo_clave=eq))
    for eq in ("londrina", "nautico"):
        filas.extend(bh.construir_filas_desde_documento(ln[eq], equipo_clave=eq))

    filas.extend(
        bh.construir_filas_desde_stats_flashscore(
            id_partido="5181796",
            fecha="2026-11-12",
            competicion="UEFA Nations League",
            local_nombre="Turkiye",
            visitante_nombre="Belgium",
            stats_periodos=STATS_FLASHSCORE_TB,
            pais_local="TUR",
            pais_visitante="BEL",
            id_flashscore="Flashscore_TB_20261112",
            inicio_utc="2026-11-12T17:00:00Z",
        )
    )
    filas.extend(
        bh.construir_filas_desde_stats_flashscore(
            id_partido="5181797",
            fecha="2026-11-12",
            competicion="UEFA Nations League",
            local_nombre="Italy",
            visitante_nombre="France",
            stats_periodos=STATS_FLASHSCORE_IF,
            pais_local="ITA",
            pais_visitante="FRA",
            id_flashscore="Flashscore_IF_20261112",
            inicio_utc="2026-11-12T19:45:00Z",
        )
    )

    res1 = bh.guardar_filas_banco(filas, db_path=db_path, csv_path=csv_path)
    assert res1["insertadas"] == 282
    assert res1["total_filas_db"] == 282

    # Reingesta idéntica + con duplicados internos: cero duplicados
    res2 = bh.guardar_filas_banco(filas + filas[:15], db_path=db_path, csv_path=csv_path)
    assert res2["insertadas"] == 0
    assert res2["total_filas_db"] == 282

    # SQLite: faltante es NULL (None), 0 legítimo se conserva como 0
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        fila_tb_2t = dict(
            conn.execute(
                "SELECT * FROM partidos_periodos WHERE id_partido='5181796' AND equipo_clave='turkey' AND periodo='2T'"
            ).fetchone()
        )
        assert fila_tb_2t["goles_favor"] == 0
        assert fila_tb_2t["goles_contra"] == 0
        assert fila_tb_2t["tarjetas_amarillas"] is None  # no reportada en 2T -> NULL, nunca 0
        assert fila_tb_2t["tiros_libres"] is None
        assert fila_tb_2t["goles_evitados"] == pytest.approx(0.33)
    finally:
        conn.close()

    # CSV: faltante serializa literalmente como "sin_dato", 0 legítimo como "0"
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        csv_rows = list(csv.DictReader(fh))
    assert len(csv_rows) == 282
    row_csv_tb_2t = next(
        r for r in csv_rows
        if r["id_partido"] == "5181796" and r["equipo_clave"] == "turkey" and r["periodo"] == "2T"
    )
    assert row_csv_tb_2t["goles_favor"] == "0"
    assert row_csv_tb_2t["tarjetas_amarillas"] == "sin_dato"
    assert row_csv_tb_2t["tiros_libres"] == "sin_dato"

    # Round-trip del parser CSV devuelve None para "sin_dato" y 0 para "0"
    parsed_csv = bh.cargar_banco_csv(csv_path)
    row_parsed = next(
        r for r in parsed_csv
        if r["id_partido"] == "5181796" and r["equipo_clave"] == "turkey" and r["periodo"] == "2T"
    )
    assert row_parsed["goles_favor"] == 0
    assert row_parsed["tarjetas_amarillas"] is None


def test_validacion_1t_2t_ft_real_y_fallos_inyectados() -> None:
    filas_tb = bh.construir_filas_desde_stats_flashscore(
        id_partido="5181796",
        fecha="2026-11-12",
        competicion="UEFA Nations League",
        local_nombre="Turkiye",
        visitante_nombre="Belgium",
        stats_periodos=STATS_FLASHSCORE_TB,
    )
    filas_if = bh.construir_filas_desde_stats_flashscore(
        id_partido="5181797",
        fecha="2026-11-12",
        competicion="UEFA Nations League",
        local_nombre="Italy",
        visitante_nombre="France",
        stats_periodos=STATS_FLASHSCORE_IF,
    )
    for grupo in (filas_tb, filas_if):
        assert len(grupo) == 6
        assert all(f["coherencia_1t_2t_ft"] == "OK" for f in grupo)
        assert all(f["discrepancias_aditividad"] is None for f in grupo)

    # Discrepancias inyectadas
    f_ft = {"goles_favor": 2, "corners": 5, "xg": 1.50, "remates_totales": 10}
    f_1t = {"goles_favor": 1, "corners": 2, "xg": 0.70, "remates_totales": 6}
    f_2t = {"goles_favor": 1, "corners": 2, "xg": 0.74, "remates_totales": 0}  # 2+2!=5, 0.70+0.74 residuo=0.06>=0.05, 6+0!=10
    val = bh.validar_coherencia_1t_2t_ft(f_ft, f_1t, f_2t)
    assert val["es_coherente"] is False
    assert val["estado"] == "CON_DISCREPANCIAS"
    assert set(val["variables_con_error"]) == {"corners", "xg", "remates_totales"}

    # Overshoot parcial cuando falta la otra mitad
    val_overshoot = bh.validar_coherencia_1t_2t_ft(
        {"remates_totales": 4, "goles_evitados": 0.23},
        {"remates_totales": 6, "goles_evitados": -0.10},
        {"remates_totales": None, "goles_evitados": 0.33},
    )
    assert val_overshoot["es_coherente"] is False
    assert val_overshoot["variables_con_error"] == ["remates_totales"]


def test_mapa_cobertura_italia_francia_excluye_estimacion_modelo(tmp_path: Path) -> None:
    r5, _ = _cargar_r5_y_ln()
    db_path = tmp_path / "banco_it_fr.db"
    csv_path = tmp_path / "banco_it_fr.csv"

    filas = (
        bh.construir_filas_desde_documento(r5["docs"]["italy"], equipo_clave="italy")
        + bh.construir_filas_desde_documento(r5["docs"]["france"], equipo_clave="france")
    )
    bh.guardar_filas_banco(filas, db_path=db_path, csv_path=csv_path)

    mapa_it = bh.generar_mapa_cobertura_equipo("italy", db_path=db_path, max_partidos=15)
    mapa_fr = bh.generar_mapa_cobertura_equipo("france", db_path=db_path, max_partidos=15)

    assert mapa_it["n_partidos"] == 15
    assert mapa_fr["n_partidos"] == 15
    assert len(mapa_it["variables"]) == 47  # 2 goles + 45 métricas
    assert len(mapa_fr["variables"]) == 47

    it_idx = {v["clave"]: v for v in mapa_it["variables"]}
    fr_idx = {v["clave"]: v for v in mapa_fr["variables"]}

    assert (it_idx["goles_favor"]["FT"], it_idx["goles_favor"]["1T"], it_idx["goles_favor"]["2T"]) == (15, 15, 15)
    assert (it_idx["tiros_libres"]["FT"], it_idx["tiros_libres"]["1T"], it_idx["tiros_libres"]["2T"]) == (0, 0, 0)
    assert (it_idx["saques_puerta"]["FT"], it_idx["saques_puerta"]["1T"], it_idx["saques_puerta"]["2T"]) == (0, 0, 0)
    assert (it_idx["pases_entre_lineas"]["FT"], it_idx["pases_entre_lineas"]["1T"]) == (0, 0)
    assert (fr_idx["pases_entre_lineas"]["FT"], fr_idx["pases_entre_lineas"]["1T"]) == (1, 0)
    assert (it_idx["errores_gol"]["FT"], it_idx["errores_gol"]["1T"], it_idx["errores_gol"]["2T"]) == (8, 5, 5)
    assert (fr_idx["errores_gol"]["FT"], fr_idx["errores_gol"]["1T"], fr_idx["errores_gol"]["2T"]) == (3, 2, 2)
    assert (it_idx["xa_equipo"]["FT"], it_idx["xa_equipo"]["1T"], it_idx["xa_equipo"]["2T"]) == (15, 0, 0)

    md_it = bh.formatear_mapa_cobertura_markdown(mapa_it)
    assert "tiros_libres" in md_it and "0/15" in md_it and "estimacion_modelo" in md_it


def test_hooks_no_bloqueantes_mercados_auditar_evaluar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r5, ln = _cargar_r5_y_ln()
    db_path = tmp_path / "hooks.db"
    csv_path = tmp_path / "hooks.csv"
    monkeypatch.setenv("AGENTE_FUTBOL_BANCO_DB", str(db_path))
    monkeypatch.setenv("AGENTE_FUTBOL_BANCO_CSV", str(csv_path))

    # 1. Hook /auditar (generar_informe_matrices_30)
    ruta_md = tmp_path / "auditoria_it_fr.md"
    ah.generar_informe_matrices_30(r5["docs"]["italy"], r5["docs"]["france"], ruta_md=str(ruta_md))
    rows_aud = bh.cargar_banco_sqlite(db_path)
    assert len(rows_aud) == 90  # 15*3 + 15*3

    # 2. Hook /mercados (preparar_analisis real con resolver_enfrentamiento mockeado)
    ctx_fake = {
        "doc_local": copy.deepcopy(r5["docs"]["turkey"]),
        "doc_visitante": copy.deepcopy(r5["docs"]["belgium"]),
        "equipo_local": "Turkiye",
        "equipo_visitante": "Belgium",
        "contexto": {
            "fecha_partido": "2026-11-12",
            "competicion": "UEFA Nations League",
            "pais_local": "TUR",
            "pais_visitante": "BEL",
        },
    }
    monkeypatch.setattr(sm, "resolver_enfrentamiento", lambda *a, **kw: ctx_fake)
    sm.preparar_analisis(
        "Turkiye", "Belgium",
        directorio_salida=str(tmp_path / "sal_mercados"),
    )
    rows_mkt = bh.cargar_banco_sqlite(db_path)
    assert len(rows_mkt) == 180  # 90 (it+fr) + 90 (tr+be)

    # 3. Hook /evaluar (preparar_evaluacion con fixture TB/IF reales)
    payload_tb = copy.deepcopy(r5["partidos_objetivo"]["turkey_belgium"])
    payload_tb["flashscore_stats"] = STATS_FLASHSCORE_TB
    res_tb = ep.extraer_resultado(payload_tb, fuente="fotmob")
    monkeypatch.setattr(ep, "verificar_resultados", lambda *a, **kw: {"Turkiye vs Belgium": res_tb})
    monkeypatch.setattr(ep, "generar_imagenes_informe", lambda *a, **kw: [])
    monkeypatch.setattr(ep, "guardar_informe", lambda *a, **kw: {"json": str(tmp_path / "ev.json")})
    pron_tb = [{
        "id_trabajo": "TEST-TB-B6",
        "equipos": {"local": "Turkiye", "visitante": "Belgium"},
        "fecha": "2026-11-12",
        "competicion": "UEFA Nations League",
        "selecciones_filtradas": [
            {"mercado": "Goles", "evento": "gol_1t", "etiqueta": "+0.5 gol 1T",
             "periodo": "1T", "probabilidad": 0.72, "estado": "mantiene"},
        ],
    }]
    ep.preparar_evaluacion(pronosticos=pron_tb, fuente=object())
    rows_ev = bh.cargar_banco_sqlite(db_path)
    assert len(rows_ev) == 186  # + 6 filas (5181796 turkey & belgium FT/1T/2T)
    tb_settled = next(
        r for r in rows_ev
        if r["id_partido"] == "5181796" and r["equipo_clave"] == "turkey" and r["periodo"] == "FT"
    )
    assert tb_settled["goles_favor"] == 1
    assert tb_settled["goles_contra"] == 2
    assert tb_settled["corners"] == 2

    # 4. Seguridad no bloqueante ante ruta inválida (no lanza excepción)
    bad_path = tmp_path / "archivo_no_dir"
    bad_path.write_text("bloqueado", encoding="utf-8")
    out_err = bh.guardar_en_banco_no_bloqueante(
        doc_local=r5["docs"]["turkey"],
        db_path=bad_path / "sub.db",
        csv_path=bad_path / "sub.csv",
    )
    assert out_err is None

    # 5. Aislamiento bajo pytest y normalización de doc["equipo"] como dict
    monkeypatch.delenv("AGENTE_FUTBOL_BANCO_DB", raising=False)
    monkeypatch.delenv("AGENTE_FUTBOL_BANCO_CSV", raising=False)
    assert bh.guardar_en_banco_no_bloqueante(doc_local=r5["docs"]["turkey"]) is None

    ln_doc = copy.deepcopy(ln["londrina"])
    filas_ln = bh.construir_filas_desde_documento(ln_doc)
    assert filas_ln
    assert all(f["equipo_nombre"] == "Londrina" and f["equipo_clave"] == "londrina" for f in filas_ln)

