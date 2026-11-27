"""Tests de Ronda 14 Punto 1:
- Defectos vigentes: `usar_sobredispersion_nb_corners=True` ("regla operativa sin validación"),
  resto de 13 banderas del Conjunto B en False, cuota justa 1.25, banderas auxiliares False.
- Brazo sombra dedicado `usar_guarda_doble_defensa_rota` sin alterar salida oficial:
  ciclo pre_partido -> sha256 -> liquidado -> evaluación promoción.
- Impacto sobre los 73 registros y los 24 OOS (`datos/ronda14_punto1_defectos_y_sombra.json`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

import decision_cobertura as dc
import evaluacion_pronosticos as ep
import motor_mercados as mm


def test_ronda14_defectos_vigentes_y_documentacion():
    banderas = dc.obtener_banderas_comportamiento()
    assert len(banderas) == 14
    assert banderas["usar_sobredispersion_nb_corners"] is True
    for k, v in banderas.items():
        if k != "usar_sobredispersion_nb_corners":
            assert v is False, f"{k} debe permanecer en False"

    assert banderas["usar_guarda_doble_defensa_rota"] is False
    assert abs(dc.obtener_umbral_cuota_justa_top5() - 1.25) < 1e-9

    meta_def = dc.obtener_metadatos_defectos_banderas_b()
    assert meta_def.get("usar_sobredispersion_nb_corners") == "regla operativa sin validación"

    yaml_txt = (Path(ROOT) / "umbrales_cobertura.yaml").read_text(encoding="utf-8")
    assert "usar_sobredispersion_nb_corners: true  # regla operativa sin validación" in yaml_txt

    for aux in dc._BANDERAS_AUXILIARES_DEFECTO:
        if aux in (
            "usar_tercer_brazo_plano_sombra",
            "usar_cuotas_mercado_captura",
            "usar_guardian_homonimo_por_competicion",
            "usar_emparejamiento_exacto_sin_subcadena",
            "usar_sin_dato_sin_fallbacks_veredicto",
            "usar_registro_consultas_jsonl_persistente",
            "usar_aislamiento_try_cuotas_ticket",
            "usar_etiqueta_matriz_final_y_cronologia_raw",
            "usar_umbral_unificado_sin_tolerancia",
            "usar_cache_auditoria_con_corte",
        ):
            assert dc.bandera_activa(aux) is True
        else:
            assert dc.bandera_activa(aux) is False

    # La bandera activa por defecto queda bloqueada para autopromoción duplicada
    prom_nb = ep.evaluar_promocion_regla_futura(
        [{"id_partido": "x", "partido": "A vs B", "evento_ocurrio": 1, "probs": {"base": 0.5, "sombra": 0.6}}],
        nombre_bandera="usar_sobredispersion_nb_corners",
        n_boot=20,
    )
    assert prom_nb["aprobada_para_activar"] is False
    assert any("bandera_no_apagada_por_defecto:usar_sobredispersion_nb_corners" in r for r in prom_nb["razones_bloqueo"])


def test_ronda14_guarda_doble_defensa_rota_solo_como_brazo_sombra(tmp_path):
    r5 = json.loads((Path(ROOT) / "tests" / "fixtures" / "ronda5_real_fixtures.json").read_text(encoding="utf-8"))["docs"]
    an_of = mm.analizar(r5["turkey"], r5["belgium"], "Turkey", "Belgium", competicion="UEFA Nations League")
    # En la salida oficial NO se activa guarda_doble_defensa_rota (Visitante Belgium FT ~ 3.55)
    assert round(an_of.motor.tasas.lambda_visitante_ft, 2) == pytest.approx(3.55, abs=0.05)

    prep = {
        "id_trabajo": "r14_tb_guarda_ddr",
        "id_partido": "5181796",
        "analisis": an_of,
        "contexto": {
            "doc_local": r5["turkey"],
            "doc_visitante": r5["belgium"],
            "equipo_local": "Turkey",
            "equipo_visitante": "Belgium",
            "fecha_partido": "2026-10-10",
            "competicion": "UEFA Nations League",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
        },
    }
    somb = ep.adjuntar_modo_sombra_preparado(prep)
    assert somb is not None
    assert somb["esquema"] in ("sombra-v1", "sombra-v2-tribrazo")
    assert somb["version_base"] == "soloA"
    assert somb["banderas_base"]["usar_sobredispersion_nb_corners"] is True
    assert somb["banderas_base"]["usar_guarda_doble_defensa_rota"] is False
    assert somb["defectos_operativos_sin_validacion"]["usar_sobredispersion_nb_corners"] == "regla operativa sin validación"

    arm = somb["brazos_reglas"]["guarda_doble_defensa_rota"]
    assert arm["bandera"] == "usar_guarda_doble_defensa_rota"
    assert arm["solo_brazo_sombra"] is True
    assert arm["banderas_activas"]["usar_sobredispersion_nb_corners"] is True
    assert arm["banderas_activas"]["usar_guarda_doble_defensa_rota"] is True
    assert round(arm["lambdas"]["visitante_ft"], 2) == pytest.approx(2.63, abs=0.05)
    assert arm["delta_lambda_visitante_ft"] < -0.5

    # Publicado intacto y ciclo pre_partido -> sha256 -> liquidado con brazo guarda_doble_defensa_rota
    assert an_of.motor.tasas.lambda_visitante_ft == somb["lambdas_base"]["visitante_ft"]
    ruta_snap = ep.guardar_snapshot(ep.snapshot_desde_analisis(prep), directorio=str(tmp_path / "snaps"))
    ruta_led = str(tmp_path / "ledger.jsonl")
    r_pre = ep.registrar_sombra_prepartido_en_ledger_oos(prep, ruta_snapshot=ruta_snap, ruta_ledger=ruta_led)
    assert r_pre["registrado"] is True
    pre_rows = ep.cargar_ledger_oos(ruta_led)
    assert "guarda_doble_defensa_rota" in pre_rows[0]["modo_sombra"]["brazos_reglas"]

    snap_doc = json.loads(Path(ruta_snap).read_text(encoding="utf-8"))
    snap_doc.setdefault("partido", {})["fecha"] = "2026-10-10"
    snap_doc["generado_en"] = "2026-10-10T10:00:00"
    res_tb = ep.ResultadoPartido(
        id_partido="5181796",
        estado="finalizado",
        local="Turkey",
        visitante="Belgium",
        goles_local=1,
        goles_visitante=2,
        goles_local_1t=0,
        goles_visitante_1t=1,
        inicio_utc="2026-10-10T18:45:00Z",
        competicion="UEFA Nations League",
        fuente="fotmob",
        consultado_en="2026-10-11T00:00:00Z",
        corners_local={"FT": 4, "1T": 2, "2T": 2},
        corners_visitante={"FT": 7, "1T": 3, "2T": 4},
        amarillas_local={"FT": 2, "1T": 1, "2T": 1},
        amarillas_visitante={"FT": 2, "1T": 1, "2T": 1},
        rojas_local=0,
        rojas_visitante=0,
    )
    r_liq = ep.liquidar_sombras_en_ledger_oos(
        [snap_doc],
        {"Turkey vs Belgium": res_tb},
        ruta_ledger=ruta_led,
    )
    assert r_liq["n_liquidados_nuevos"] == 1
    assert ep.verificar_cadena_ledger_oos(ruta_led)["ok"] is True

    # Extracción estándar no rompe probs duales; extracción por brazo_regla aísla guarda_doble_defensa_rota
    obs_dual = ep.extraer_observaciones_sombra_desde_ledger(ruta_led, excluir_diseno=False)
    assert {"base", "sombra"}.issubset(set(obs_dual["observaciones"][0]["probs"].keys()))
    obs_ddr = ep.extraer_observaciones_sombra_desde_ledger(
        ruta_led,
        excluir_diseno=False,
        brazo_regla="usar_guarda_doble_defensa_rota",
    )
    assert obs_ddr["n_observaciones"] > 50
    prom_ddr = ep.evaluar_promocion_regla_futura(
        obs_ddr["observaciones"],
        nombre_bandera="usar_guarda_doble_defensa_rota",
        n_boot=50,
    )
    assert prom_ddr["aprobada_para_activar"] is False
    assert prom_ddr["n_excluidos_diseno"] == obs_ddr["n_observaciones"]
    assert any("muestra_sombra_insuficiente:0<30" in r for r in prom_ddr["razones_bloqueo"])


def test_ronda14_json_impacto_73_registros_y_24_oos():
    ruta = Path(ROOT) / "datos" / "ronda14_punto1_defectos_y_sombra.json"
    assert ruta.exists()
    rep = ep.auditar_defectos_y_sombra_ronda14(regenerar=False)

    ver = rep["verificacion_ciclo_sombra_guarda_ddr"]
    assert ver["usar_guarda_doble_defensa_rota_en_defecto"] is False
    assert ver["arm_presente_en_brazos_reglas"] is True
    assert ver["n_liquidados_nuevos"] == 1
    assert ver["cadena_sha256_ok"] is True
    assert ver["n_observaciones_extraidas_guarda"] > 200
    assert ver["promocion_bloqueada_por_n_lt_30"] is True

    i73 = rep["impacto_73_registros"]
    assert i73["n_registros"] == 73
    c_nb = i73["comparaciones"]["defecto_ronda14_nb_vs_soloA_previo"]
    assert c_nb["n_partidos_lambda_cambiada"] == 0
    assert c_nb["cambios_prob_por_familia"]["Goles"]["n_selecciones_cambiadas"] == 0
    assert c_nb["cambios_prob_por_familia"]["Resultado"]["n_selecciones_cambiadas"] == 0
    assert c_nb["cambios_prob_por_familia"]["Tarjetas"]["n_selecciones_cambiadas"] == 0
    assert c_nb["cambios_prob_por_familia"]["Córneres"]["n_partidos"] == 70

    c_ddr = i73["comparaciones"]["sombra_guarda_ddr_vs_defecto_ronda14_nb"]
    assert c_ddr["n_partidos_lambda_cambiada"] == 6
    assert c_ddr["cambios_prob_por_familia"]["Córneres"]["n_selecciones_cambiadas"] == 0

    oos = rep["impacto_24_oos"]
    assert oos["cohorte"]["n_partidos_unicos"] == 24
    assert oos["cohorte"]["n_jobs"] == 26
    assert oos["cohorte"]["n_filas_comunes"] == 1376
    fams = oos["catalogo_1376_por_familia"]
    for f in ("Goles", "Resultado", "Tarjetas"):
        d = fams[f]["diferencias"]["defecto_ronda14_nb - soloA_previo"]
        assert d["d_brier"] == 0.0
        assert d["d_log_loss"] == 0.0
    assert fams["Córneres"]["n_filas"] == 613
    assert fams["Córneres"]["diferencias"]["defecto_ronda14_nb - soloA_previo"]["d_log_loss"] < -0.10
    assert len(oos["partidos_lambda_cambiada_por_guarda_ddr_24_oos"]) == 3
