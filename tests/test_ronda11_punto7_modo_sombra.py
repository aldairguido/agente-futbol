# -*- coding: utf-8 -*-
"""
Ronda 11 Punto 7 (MODO SOMBRA):
Verifica determinísticamente:
  1. Estado real del libro (`ledger_oos.jsonl`: 3 entradas sin `modo_sombra`; `56` snapshots sin
     `modo_sombra`) -> `0/30` completados, **faltan 30** partidos competitivos no-diseño.
  2. Todas las 14 banderas de comportamiento siguen apagadas por defecto (`soloA`).
  3. Ciclo de vida completo pre-partido -> post-partido en `tmp_path`:
       - Pre-partido: `adjuntar_modo_sombra_preparado` (`soloA` vs `A+B` 14 banderas) ->
         `guardar_snapshot` -> `registrar_sombra_prepartido_en_ledger_oos` con hash SHA-256.
       - `resumen_ledger_oos` ignora registros `pre_partido` pendientes.
       - Post-partido: `preparar_evaluacion(..., registrar_ledger_oos_sombra=True, ruta_ledger_oos=...)`
         liquida automáticamente en `ledger_oos` (`estado_registro='liquidado'`, `pre_partido_sha256`
         encadenado, cadena SHA-256 intacta) y alimenta `extraer_observaciones_sombra_desde_ledger`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_cobertura as dc
import evaluacion_pronosticos as ep
import motor_mercados as mm


def test_ronda11_punto7_estado_real_y_banderas_apagadas():
    with dc.banderas_comportamiento(activar_todas=False):
        rep = ep.auditar_estado_modo_sombra_ronda11(guardar_ruta=None)

    assert rep["estado_previo_ronda11"]["ledger_ya_guardaba_soloA_y_AB_pre_y_liquidaba_post"] is False
    assert rep["ledger_oos_real"]["cadena_sha256_ok"] is True
    assert rep["ledger_oos_real"]["n_entradas_totales"] == 3
    assert rep["ledger_oos_real"]["n_entradas_con_modo_sombra"] == 0
    assert rep["snapshots_pronosticos"]["n_snapshots_totales"] == 56
    assert rep["snapshots_pronosticos"]["n_snapshots_con_modo_sombra"] == 0
    assert rep["progreso_meta_30_partidos"]["meta_min_partidos_competitivos_no_diseno"] == 30
    assert rep["progreso_meta_30_partidos"]["n_partidos_competitivos_sombra_completados"] == 0
    assert rep["progreso_meta_30_partidos"]["faltan_partidos_competitivos_para_30"] == 30
    assert rep["captura_automatica_cableada"]["todas_las_14_banderas_apagadas"] is True

    src_bot = (ROOT / "bot_interactivo.py").read_text(encoding="utf-8")
    assert "registrar_sombra_prepartido_en_ledger_oos" in src_bot
    assert "adjuntar_modo_sombra_preparado" in src_bot

    disco = json.loads((ROOT / "datos" / "ronda11_punto7_modo_sombra.json").read_text(encoding="utf-8"))
    assert disco == rep


def test_ronda11_punto7_ciclo_prepartido_y_liquidacion_postpartido(tmp_path, monkeypatch):
    aud = ROOT / "informes" / "auditorias" / "2026-10-13"
    dl = json.loads((aud / "auditoria_galatasaray_2026-10-13_102501.json").read_text(encoding="utf-8"))
    dv = json.loads((aud / "auditoria_barcelona_2026-10-13_102522.json").read_text(encoding="utf-8"))
    dl_c = {"equipo": dl.get("equipo"), "partidos": [x for x in dl.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"]}
    dv_c = {"equipo": dv.get("equipo"), "partidos": [x for x in dv.get("partidos", []) if str(x.get("fecha") or "")[:10] < "2026-10-13"]}

    with dc.banderas_comportamiento(activar_todas=False):
        an_of = mm.analizar(dl_c, dv_c, "Galatasaray", "Barcelona", fecha_partido="2026-10-13")

    an_of.metadatos["fecha_partido"] = "2026-10-13"
    an_of.metadatos["generado_en"] = "2026-10-13T10:00:00"
    an_of.metadatos["competicion"] = "UEFA Champions League"

    preparado = {
        "id_trabajo": "SOMBRA_R11_GAL_BAR",
        "analisis": an_of,
        "rutas": {"informe": str(tmp_path / "inf.md")},
        "contexto": {
            "doc_local": dl_c,
            "doc_visitante": dv_c,
            "equipo_local": "Galatasaray",
            "equipo_visitante": "Barcelona",
            "fecha_partido": "2026-10-13",
            "competicion": "UEFA Champions League",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
        },
    }
    sombra = ep.adjuntar_modo_sombra_preparado(preparado)
    assert sombra is not None
    assert sombra["publicado"] == "soloA"
    assert sombra["version_sombra"] == "A+B"
    assert len(sombra["banderas_candidatas"]) == len(dc.BANDERAS_COMPORTAMIENTO_DEFECTO)
    assert all(sombra["banderas_candidatas"].values())

    snap = ep.snapshot_desde_analisis(preparado)
    ruta_snap = ep.guardar_snapshot(snap, directorio=str(tmp_path / "pronosticos"))
    snap_sha_real = hashlib.sha256(Path(ruta_snap).read_bytes()).hexdigest()

    ruta_led = str(tmp_path / "ledger_oos.jsonl")
    r_pre = ep.registrar_sombra_prepartido_en_ledger_oos(
        preparado, ruta_snapshot=ruta_snap, ruta_ledger=ruta_led
    )
    assert r_pre["registrado"] is True
    assert r_pre["estado_registro"] == "pre_partido"
    assert r_pre["snapshot_sha256"] == snap_sha_real

    # Idempotente en segunda llamada pre_partido
    r_pre_dup = ep.registrar_sombra_prepartido_en_ledger_oos(
        preparado, ruta_snapshot=ruta_snap, ruta_ledger=ruta_led
    )
    assert r_pre_dup["registrado"] is False and r_pre_dup["ya_existia"] is True

    # El pre_partido aún no cuenta como partido liquidado en resumen_ledger_oos
    res_pre = ep.resumen_ledger_oos(ruta_led)
    assert res_pre["cadena_sha256_ok"] is True
    assert res_pre["n_incluidos_promedios_calibracion"] == 0

    # Simular post-partido real liquidado automáticamente vía preparar_evaluacion (sin escribir fuera de tmp_path)
    snap_dict = json.loads(Path(ruta_snap).read_text(encoding="utf-8"))
    snap_dict["partido"]["fecha"] = "2026-10-13"
    snap_dict["generado_en"] = "2026-10-13T10:00:00"
    res_obj = ep.ResultadoPartido(
        id_partido="6106338",
        estado="finalizado",
        local="Galatasaray",
        visitante="Barcelona",
        goles_local=1,
        goles_visitante=3,
        goles_local_1t=0,
        goles_visitante_1t=1,
        inicio_utc="2026-10-13T19:00:00Z",
        competicion="UEFA Champions League",
        fuente="fotmob",
        consultado_en="2026-10-14T00:00:00Z",
        corners_local={"FT": 4, "1T": 2, "2T": 2},
        corners_visitante={"FT": 6, "1T": 3, "2T": 3},
        amarillas_local={"FT": 2, "1T": 1, "2T": 1},
        amarillas_visitante={"FT": 2, "1T": 1, "2T": 1},
        rojas_local=0,
        rojas_visitante=0,
    )

    monkeypatch.setattr(ep, "verificar_resultados", lambda docs, **kw: {"Galatasaray vs Barcelona": res_obj})
    monkeypatch.setattr(ep, "guardar_informe", lambda inf, nombre=None: {"informe_md": str(tmp_path / "ev.md"), "informe_json": str(tmp_path / "ev.json")})
    monkeypatch.setattr(ep, "generar_imagenes_informe", lambda inf: [])

    prep_eval = ep.preparar_evaluacion(
        pronosticos=[snap_dict],
        fuente=object(),
        registrar_ledger_oos_sombra=True,
        ruta_ledger_oos=ruta_led,
    )
    assert prep_eval["estado"] == "resumen_preparado"

    # Re-evaluar no debe duplicar ni romper la cadena SHA-256
    ep.preparar_evaluacion(
        pronosticos=[snap_dict],
        fuente=object(),
        registrar_ledger_oos_sombra=True,
        ruta_ledger_oos=ruta_led,
    )

    ver = ep.verificar_cadena_ledger_oos(ruta_led)
    assert ver["ok"] is True
    assert ver["entradas"] == 2
    filas = ep.cargar_ledger_oos(ruta_led)
    assert [f.get("estado_registro") for f in filas] == ["pre_partido", "liquidado"]
    assert filas[1]["pre_partido_sha256"] == filas[0]["sha256"]
    assert filas[1]["marcador_real"]["ft"] == [1, 3]
    assert filas[1]["modo_sombra"]["version_base"] == "soloA"
    assert filas[1]["modo_sombra"]["version_sombra"] == "A+B"
    assert filas[1]["modo_sombra"]["pre_partido_vinculado"] is True

    obs_led = ep.extraer_observaciones_sombra_desde_ledger(ruta_led)
    assert obs_led["n_partidos_sombra_validos"] == 1
    assert obs_led["pre_partido_pendientes_liquidacion"] == []
    prom = ep.evaluar_promocion_regla_futura(obs_led["observaciones"], nombre_bandera="usar_tope_dcn_18_pct", n_boot=50)
    assert prom["aprobada_para_activar"] is False
    assert any("muestra_sombra_insuficiente:1<30" in r for r in prom["razones_bloqueo"])
