# -*- coding: utf-8 -*-
"""
Suite forense determinista de la Entrega 2 (Fases 3 a 7) sobre los 6 bugs de
Italia vs Francia (2026-11-13) y el anclaje de regresion con Turquia vs Belgica.
100% offline: utiliza exclusivamente tests/fixtures/ronda5_real_fixtures.json y
fixtures sinteticos declarados.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import auditoria_historica as ah
import cuadro_especialista as ce
import decision_cobertura as dc
import fuentes_xg as fxg
import motor_mercados as mm
import motor_patrones as mp
import motor_recursivo_45_dcn as mr45
import servicio_mercados as sm


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "ronda5_real_fixtures.json"


def _cargar_docs_reales() -> dict[str, dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload["docs"]


def _partido_sintetico(
    *,
    pid: str,
    fecha: str,
    condicion: str,
    equipo: str,
    rival: str,
    competicion: str = "UEFA Nations League A Grp. 1",
    gf: int = 1,
    gc: int = 1,
    ft: dict[str, Any] | None = None,
    p1: dict[str, Any] | None = None,
    p2: dict[str, Any] | None = None,
    ft_rival: dict[str, Any] | None = None,
    p1_rival: dict[str, Any] | None = None,
    p2_rival: dict[str, Any] | None = None,
    fuente: str = "fotmob",
) -> dict[str, Any]:
    def _mk_periodo(per: str, eq_vals: dict[str, Any] | None, rv_vals: dict[str, Any] | None) -> dict[str, Any]:
        eq_d = dict(eq_vals or {})
        rv_d = dict(rv_vals or {})
        claves = sorted(set(eq_d.keys()) | set(rv_d.keys()))
        filas = []
        for k in claves:
            v_e = eq_d.get(k)
            v_r = rv_d.get(k)
            filas.append(
                {
                    "clave": k,
                    "equipo": v_e,
                    "rival": v_r,
                    "fuente": fuente,
                    "estimado": False,
                    "procedencia_equipo": fuente if v_e is not None else "sin_dato",
                    "procedencia_rival": fuente if v_r is not None else "sin_dato",
                }
            )
        return {"filas": filas}

    return {
        "id_partido": pid,
        "id_flashscore": f"fs_{pid}",
        "flashscore_pareado": True,
        "fecha": fecha,
        "condicion": condicion,
        "equipo": equipo,
        "rival": rival,
        "competicion": competicion,
        "fuente_primaria": fuente,
        "goles_equipo": gf,
        "goles_rival": gc,
        "marcador": {"equipo": gf, "rival": gc},
        "periodos": {
            "FT": _mk_periodo("FT", ft, ft_rival),
            "1T": _mk_periodo("1T", p1, p1_rival),
            "2T": _mk_periodo("2T", p2, p2_rival),
        },
    }


def _doc_sintetico(nombre: str, pais: str, partidos: list[dict[str, Any]], fuente: str = "fotmob") -> dict[str, Any]:
    return {
        "equipo": {"nombre": nombre, "pais": pais},
        "fuente": fuente,
        "partidos": partidos,
    }


# ---------------------------------------------------------------------------
# FASE 3: Anti-lookahead temporal, 45 metricas None/sin_dato, cronologia FotMob
# ---------------------------------------------------------------------------

def test_fase3_anti_lookahead_cutoff_y_filtrar_por_fecha() -> None:
    assert fxg.filtrar_partido_por_cutoff(
        "2026-10-14", id_partido="ok_old", fecha_corte="2026-11-12", exclude_match_ids=["target_1"]
    ) is True
    assert fxg.filtrar_partido_por_cutoff(
        "2026-11-12", id_partido="same_day", fecha_corte="2026-11-12"
    ) is False
    assert fxg.filtrar_partido_por_cutoff(
        "2026-11-15", id_partido="future_match", fecha_corte="2026-11-12"
    ) is False
    assert fxg.filtrar_partido_por_cutoff(
        "2026-10-14", id_partido="target_1", fecha_corte="2026-11-12", exclude_match_ids=["target_1"]
    ) is False
    assert fxg.filtrar_partido_por_cutoff(
        "2026-11-12",
        id_partido="early_same_day",
        utc_time="2026-11-12T17:00:00Z",
        cutoff_timestamp="2026-11-12T19:45:00Z",
    ) is True
    assert fxg.filtrar_partido_por_cutoff(
        "2026-11-12",
        id_partido="exact_kickoff",
        utc_time="2026-11-12T19:45:00Z",
        cutoff_timestamp="2026-11-12T19:45:00Z",
    ) is False

    doc = _doc_sintetico(
        "Italia",
        "ITA",
        [
            _partido_sintetico(pid="p_hist_1", fecha="2026-09-09", condicion="casa", equipo="Italia", rival="Israel"),
            _partido_sintetico(pid="p_target", fecha="2026-11-11", condicion="casa", equipo="Italia", rival="Francia"),
            _partido_sintetico(pid="p_same_day", fecha="2026-11-12", condicion="fuera", equipo="Italia", rival="Belgica"),
            _partido_sintetico(pid="p_future", fecha="2026-11-16", condicion="casa", equipo="Italia", rival="Francia"),
        ],
    )
    filtrado = mm.filtrar_por_fecha(doc, fecha_corte="2026-11-12", id_partido_excluir="p_target")
    ids = [p["id_partido"] for p in filtrado["partidos"]]
    assert ids == ["p_hist_1"]


def test_fase3_45_metricas_sin_dato_nunca_cero_ni_est_sintetico() -> None:
    assert len(ah.LAS_45_METRICAS_OFICIALES) == 45

    assert ah._formatear_valor_metrica_45("xg", None) == "sin_dato"
    assert ah._formatear_valor_metrica_45(
        "saques_puerta", 6, procedencia_celda="estimacion_modelo", fuente_partido="fotmob"
    ) == "sin_dato"
    assert ah._formatear_valor_metrica_45(
        "duelos_suelo_pct", 0, fuente_partido="flashscore", procedencia_celda="flashscore"
    ) == "sin_dato"
    assert ah._formatear_valor_metrica_45(
        "xg_balon_parado", 0.0, fuente_partido="flashscore", procedencia_celda="flashscore"
    ) == "sin_dato"

    filas_map_copia = {
        "xg": {"clave": "xg", "equipo": 1.45, "fuente": "flashscore"},
        "xg_balon_parado": {"clave": "xg_balon_parado", "equipo": 0.0, "fuente": "flashscore"},
        "xg_juego_abierto": {"clave": "xg_juego_abierto", "equipo": 1.45, "fuente": "flashscore"},
        "xg_sin_penalti": {"clave": "xg_sin_penalti", "equipo": 1.45, "fuente": "flashscore"},
    }
    assert ah._formatear_valor_metrica_45(
        "xg_juego_abierto", 1.45, filas_map_copia, "equipo", fuente_partido="flashscore"
    ) == "sin_dato"
    assert ah._formatear_valor_metrica_45(
        "xg_sin_penalti", 1.45, filas_map_copia, "equipo", fuente_partido="flashscore"
    ) == "sin_dato"

    assert ah._formatear_valor_metrica_45(
        "corners", 0, fuente_partido="fotmob", procedencia_celda="fotmob"
    ) == "0"

    partido = _partido_sintetico(
        pid="mat_1",
        fecha="2026-10-14",
        condicion="casa",
        equipo="Italia",
        rival="Francia",
        ft={"xg": 1.62, "corners": 0, "saques_puerta": None, "xg_balon_parado": None},
        p1={"xg": 0.55, "corners": 0, "saques_puerta": None},
        p2={"xg": 1.07, "corners": 0, "saques_puerta": None},
        ft_rival={"xg": 0.91, "corners": 3, "saques_puerta": None, "xg_balon_parado": None},
        p1_rival={"xg": 0.40, "corners": 1, "saques_puerta": None},
        p2_rival={"xg": 0.51, "corners": 2, "saques_puerta": None},
    )
    partido["periodos"]["FT"]["filas"].append(
        {
            "clave": "saques_puerta",
            "equipo": 5,
            "rival": 4,
            "fuente": "estimacion_modelo",
            "estimado": True,
            "procedencia_equipo": "estimacion_modelo",
            "procedencia_rival": "estimacion_modelo",
        }
    )
    md = ah.formatear_matriz_45_partido(partido, k=1, total_partidos=1)
    assert "| Est." not in md
    assert "Est." not in md
    assert "| 45 | Saques de puerta (`saques_puerta`) | sin_dato | sin_dato | sin_dato | sin_dato | sin_dato | sin_dato | sin_dato |" in md


def test_fase3_cronologia_fotmob_separa_prorroga_y_penaltis() -> None:
    estado = {"reason": {"short": "Pen", "longKey": "finished_after_penalties"}}
    detalle = {
        "content": {
            "matchFacts": {
                "events": {
                    "events": [
                        {"type": "Goal", "time": 28, "timeStr": "28", "isHome": True},
                        {"type": "Goal", "time": 74, "timeStr": "74", "isHome": False},
                        {"type": "Half", "halfStrShort": "ET", "period": "ExtraTimeFirstHalf"},
                        {"type": "Goal", "time": 103, "timeStr": "103", "isHome": True, "period": "ExtraTimeFirstHalf"},
                        {"type": "Half", "halfStrShort": "PEN", "period": "PenaltyShootout"},
                        {"type": "Goal", "time": 121, "timeStr": "PEN", "isHome": True, "isShootoutGoal": True, "isPenaltyShootoutEvent": True, "period": "PenaltyShootout"},
                        {"type": "Goal", "time": 122, "timeStr": "PEN", "isHome": False, "isShootoutGoal": True, "isPenaltyShootoutEvent": True, "period": "PenaltyShootout"},
                    ]
                }
            }
        }
    }
    gl_90, gv_90, razon, desemp = fxg.resolver_marcador_reglamentario_fotmob(
        estado, raw_gl=5, raw_gv=4, detalle=detalle
    )
    assert (gl_90, gv_90) == (1, 1)
    assert razon == "Pen"
    assert desemp is not None and "5-4" in desemp

    evs, franjas, fuente = fxg._extraer_eventos_gol_fotmob(
        detalle["content"],
        detalle=detalle,
        es_local=True,
        goles_equipo=1,
        goles_rival=1,
    )
    assert evs is not None and len(evs) == 2
    assert franjas is not None
    assert franjas["16_30"]["anotados"] == 1
    assert franjas["61_75"]["recibidos"] == 1
    assert sum(franjas[k]["anotados"] + franjas[k]["recibidos"] for k in franjas) == 2
    assert fuente != "sin_dato"


# ---------------------------------------------------------------------------
# FASE 4: Tensor multi-periodo 65%/35% y clasificacion 2T por xG en Italia
# ---------------------------------------------------------------------------

def test_fase4_tensor_65_35_y_perfil_mitades_italia() -> None:
    docs = _cargar_docs_reales()
    italy_doc = docs["italy"]
    france_doc = docs["france"]

    t_it = mp.construir_tensor_multi_periodo_equipo(italy_doc, "casa")
    t_fr = mp.construir_tensor_multi_periodo_equipo(france_doc, "fuera")

    assert t_it["n_condicion"] == 7
    assert t_fr["n_condicion"] == 6
    assert abs(t_it["ratios_mitades"]["ratio_2t_1t_xg"] - 2.053) < 0.01
    xg_1t_it = t_it["tensor"]["1T"]["condicion"]["xg"]["media"]
    xg_2t_it = t_it["tensor"]["2T"]["condicion"]["xg"]["media"]
    assert xg_2t_it > xg_1t_it
    assert abs(xg_2t_it - 1.5857) < 0.01

    xg_gen_2t = t_it["tensor"]["2T"]["general"]["xg"]["media"]
    xg_mix_2t = t_it["mezcla_65_35"]["2T"]["xg"]["media"]
    assert abs(xg_mix_2t - round(0.65 * xg_2t_it + 0.35 * xg_gen_2t, 4)) < 0.002

    mit_it = ce.perfil_mitades(italy_doc, "casa")
    assert mit_it["modo_muestra"] == "mezcla 65% casa + 35% general"
    assert mit_it["ratio_2t_1t_xg"] >= 1.95
    assert "diésel" in mit_it["perfil"].lower()

    mit_fr = ce.perfil_mitades(france_doc, "fuera")
    assert mit_fr["modo_muestra"] == "mezcla 65% fuera + 35% general"
    assert mit_fr["ratio_2t_1t_xg"] < mit_it["ratio_2t_1t_xg"]


# ---------------------------------------------------------------------------
# FASE 5: Anclaje coeficiente torneo actual, tope +-18% DCN y Seg/Gol Concedido
# ---------------------------------------------------------------------------

def test_fase5_anclaje_coef_torneo_actual_uefa_vs_brecha_divisiones() -> None:
    docs = _cargar_docs_reales()
    coefs_uefa = mr45.obtener_coefs_torneo_anclados(
        docs["italy"]["partidos"],
        docs["france"]["partidos"],
        nombre_L="Italy",
        nombre_V="France",
        competicion_str="UEFA Nations League A Grp. 1",
    )
    assert coefs_uefa["comparten_torneo"] is True
    assert coefs_uefa["aplica_coef_liga"] is False
    assert coefs_uefa["fu_L"] == 1.0
    assert coefs_uefa["fu_V"] == 1.0
    assert 0.90 <= coefs_uefa["ratio_raw"] <= 1.10

    coefs_gap = mr45.obtener_coefs_torneo_anclados(
        [{"torneo": "Cuarta Division Amateur", "competicion": "Cuarta Division Amateur"}],
        [{"torneo": "Premier League", "competicion": "Premier League"}],
        nombre_L="ClubAmateur",
        nombre_V="TopClub",
        competicion_str="Champions League",
    )
    assert coefs_gap["comparten_torneo"] is False
    assert coefs_gap["aplica_coef_liga"] is True
    assert coefs_gap["coef_L"] < coefs_gap["coef_V"]
    assert (coefs_gap["coef_V"] - coefs_gap["coef_L"]) > 0.15


def test_fase5_tope_18_doble_conteo_y_seg_gol_concedido_italia_francia() -> None:
    docs = _cargar_docs_reales()
    an_if = mm.analizar(
        docs["italy"], docs["france"], "Italy", "France", competicion="UEFA Nations League"
    )
    rec_if = an_if.motor.recursivo
    assert rec_if["aplicado"] is True
    assert rec_if["seg_gol_concedido_propio_L"] != rec_if["seg_gol_concedido_propio_V"]
    assert abs(rec_if["seg_gol_concedido_propio_L"] - 3901.7) < 0.2
    assert abs(rec_if["seg_gol_concedido_propio_V"] - 5471.1) < 0.2
    assert rec_if["seg_gol_concedido_rival_L"] == rec_if["seg_gol_concedido_propio_V"]
    assert rec_if["seg_gol_concedido_rival_V"] == rec_if["seg_gol_concedido_propio_L"]

    # Antes de Ronda 5+6 Francia salia a 2.22 vs Italia 1.10; corregido mantiene 1.31 vs 1.79
    assert abs(an_if.motor.tasas.lambda_local_ft - 1.31) <= 0.02
    assert abs(an_if.motor.tasas.lambda_visitante_ft - 1.79) <= 0.02

    # Regresion DCN tope +-18% cuando no hay alineacion unánime de las 5 dimensiones
    lam_b_l = float(rec_if["lambda_base_local"])
    lam_b_v = float(rec_if["lambda_base_visita"])
    if not rec_if["alineado_5_dims_local"] and not rec_if.get("freno_bajista_dcn_local"):
        pct_l = abs((an_if.motor.tasas.lambda_local_ft - lam_b_l) / max(0.1, lam_b_l)) * 100.0
        assert pct_l <= 18.5
    if not rec_if["alineado_5_dims_visita"] and not rec_if.get("freno_bajista_dcn_visita"):
        pct_v = abs((an_if.motor.tasas.lambda_visitante_ft - lam_b_v) / max(0.1, lam_b_v)) * 100.0
        assert pct_v <= 18.5

    an_tb = mm.analizar(
        docs["turkey"], docs["belgium"], "Turkey", "Belgium", competicion="UEFA Nations League"
    )
    rec_tb = an_tb.motor.recursivo
    assert abs(an_tb.motor.tasas.lambda_local_ft - 1.10) <= 0.02
    assert abs(an_tb.motor.tasas.lambda_visitante_ft - 2.98) <= 0.02
    assert rec_tb["seg_gol_concedido_rival_L"] == rec_tb["seg_gol_concedido_propio_V"]
    assert rec_tb["seg_gol_concedido_rival_V"] == rec_tb["seg_gol_concedido_propio_L"]


# ---------------------------------------------------------------------------
# FASE 6: Motor de micropatrones combinatorios multi-periodo por mitades
# ---------------------------------------------------------------------------

def test_fase6_micropatrones_reales_italia_francia_y_turquia_belgica() -> None:
    docs = _cargar_docs_reales()
    mp_if = mp.evaluar_micropatrones_combinatorios(
        docs["italy"], docs["france"], eq_l="Italy", eq_v="France"
    )
    assert "MP01_ASEDIO_2T_LOCAL" in mp_if["codigos_activos"]
    assert "MP02_FRENO_CORNERS_VISITA" in mp_if["codigos_activos"]
    assert "doble_oportunidad:x2" in mp_if["vetos_mercados"]
    assert "corners_visita:over_3.5" in mp_if["vetos_mercados"]
    assert "total_goles:over_1.5" in mp_if["senales_favor"]

    mp_tb = mp.evaluar_micropatrones_combinatorios(
        docs["turkey"], docs["belgium"], eq_l="Turkey", eq_v="Belgium"
    )
    # Turquia NO alcanza el xG 2T condicionado requerido para MP01 (anti falsos positivos)
    assert "MP01_ASEDIO_2T_LOCAL" not in mp_tb["codigos_activos"]
    assert "MP02_FRENO_CORNERS_VISITA" in mp_tb["codigos_activos"]


def test_fase6_micropatrones_sinteticos_mp03_mp04_y_manejo_none() -> None:
    partidos_l_mp03 = [
        _partido_sintetico(
            pid=f"l3_{i}",
            fecha=f"2026-10-{10+i:02d}",
            condicion="casa" if i < 4 else "fuera",
            equipo="Alfa",
            rival=f"R{i}",
            ft={"xg": 1.45, "posesion_pct": 52.0, "remates_totales": 12.0, "corners": 5.0},
            p1={"xg": 0.45, "remates_totales": 4.0},
            p2={"xg": 1.00, "remates_totales": 8.0},
            ft_rival={"corners": 3.0},
        )
        for i in range(6)
    ]
    partidos_v_mp03 = [
        _partido_sintetico(
            pid=f"v3_{i}",
            fecha=f"2026-10-{10+i:02d}",
            condicion="fuera" if i < 4 else "casa",
            equipo="Beta",
            rival=f"S{i}",
            ft={"xg": 1.35, "posesion_pct": 48.0, "remates_totales": 10.0, "corners": 4.0},
            p1={"xg": 0.50, "remates_totales": 4.0},
            p2={"xg": 0.85, "remates_totales": 6.0},
            ft_rival={"corners": 5.0},
        )
        for i in range(6)
    ]
    res_mp03 = mp.evaluar_micropatrones_combinatorios(
        _doc_sintetico("Alfa", "ITA", partidos_l_mp03),
        _doc_sintetico("Beta", "FRA", partidos_v_mp03),
        eq_l="Alfa",
        eq_v="Beta",
    )
    assert "MP03_1T_CONTENIDO_EXPLOSION_2T" in res_mp03["codigos_activos"]
    assert "goles_1t:over_0.5" in res_mp03["vetos_mercados"]
    assert "goles_2t:over_0.5" in res_mp03["senales_favor"]

    partidos_l_mp04 = [
        _partido_sintetico(
            pid=f"l4_{i}",
            fecha=f"2026-10-{10+i:02d}",
            condicion="casa" if i < 4 else "fuera",
            equipo="Dom",
            rival=f"M{i}",
            ft={"xg": 1.05, "posesion_pct": 63.0, "remates_totales": 14.0, "despejes": 11.0, "corners": 6.0},
            p1={"xg": 0.50, "remates_totales": 6.0},
            p2={"xg": 0.55, "remates_totales": 8.0},
            ft_rival={"corners": 4.0},
        )
        for i in range(6)
    ]
    partidos_v_mp04 = [
        _partido_sintetico(
            pid=f"v4_{i}",
            fecha=f"2026-10-{10+i:02d}",
            condicion="fuera" if i < 4 else "casa",
            equipo="Muro",
            rival=f"N{i}",
            ft={
                "xg": 0.85,
                "posesion_pct": 37.0,
                "remates_totales": 7.0,
                "despejes": 26.0,
                "goles_evitados": 0.35,
                "corners": 3.0,
            },
            p1={"xg": 0.40, "remates_totales": 3.0},
            p2={"xg": 0.45, "remates_totales": 4.0},
            ft_rival={"corners": 6.0},
        )
        for i in range(6)
    ]
    res_mp04 = mp.evaluar_micropatrones_combinatorios(
        _doc_sintetico("Dom", "ITA", partidos_l_mp04),
        _doc_sintetico("Muro", "FRA", partidos_v_mp04),
        eq_l="Dom",
        eq_v="Muro",
    )
    assert "MP04_DOMINIO_ESTERIL_VS_MURO" in res_mp04["codigos_activos"]
    assert "total_goles:under_2.5" in res_mp04["senales_favor"]
    assert "total_goles:over_2.5" in res_mp04["vetos_mercados"]

    # Ningun colapso ni fabricacion de ceros cuando las metricas vienen como None
    partidos_none = [
        _partido_sintetico(
            pid=f"n_{i}",
            fecha=f"2026-10-{10+i:02d}",
            condicion="casa" if i < 4 else "fuera",
            equipo="SinDatoA",
            rival=f"S{i}",
            ft={"xg": None, "corners": None, "grandes_ocasiones": None, "despejes": None},
            p1={"xg": None, "remates_totales": None},
            p2={"xg": None, "remates_totales": None},
            ft_rival={"corners": None},
        )
        for i in range(5)
    ]
    res_none = mp.evaluar_micropatrones_combinatorios(
        _doc_sintetico("SinDatoA", "ITA", partidos_none),
        _doc_sintetico("SinDatoB", "FRA", partidos_none),
        eq_l="SinDatoA",
        eq_v="SinDatoB",
    )
    assert res_none["codigos_activos"] == []
    assert all(p["activo"] is False for p in res_none["todos"])


# ---------------------------------------------------------------------------
# FASE 7: Gatekeeper multi-senal Top 5, umbrales_cobertura.yaml e integracion
# ---------------------------------------------------------------------------

def test_fase7_umbrales_cobertura_yaml_regla_operativa() -> None:
    umb = dc.cargar_umbrales_cobertura()
    assert umb["max_desvio_frecuencia_observada_pts"] == 10.0
    assert umb["min_cuota_justa_no_trivial_top5"] == 1.25
    assert umb["peso_prob_modelo_gatekeeper"] == 0.60
    assert umb["peso_tasa_empirica_gatekeeper"] == 0.40
    assert umb["umbral_promocion_empirica_top5"] == 0.85
    assert umb["min_corners_visita_freno"] == 3.0
    assert umb["max_corners_concedidos_local_freno"] == 5.0

    yaml_txt = (Path(__file__).resolve().parent.parent / "umbrales_cobertura.yaml").read_text(encoding="utf-8")
    assert "umbrales:" in yaml_txt
    assert "peso_prob_modelo_gatekeeper: 0.60" in yaml_txt
    assert "# regla operativa, sin validación cronológica" in yaml_txt


def test_fase7_gatekeeper_integracion_italia_francia_y_turquia_belgica() -> None:
    docs = _cargar_docs_reales()

    # 1. Caso historico de fallo: Italia vs Francia
    an_if = mm.analizar(
        docs["italy"], docs["france"], "Italy", "France", competicion="UEFA Nations League"
    )
    top5_if, s1_if, n_elim_if = sm.obtener_top5_y_seccion1_filtrados(an_if, an_if.metadatos)
    assert n_elim_if >= 1
    assert len(top5_if) >= 2

    etiquetas_if = [t["etiqueta"].lower() for t in top5_if] + [s.etiqueta.lower() for s in s1_if]
    assert not any("x2" in et for et in etiquetas_if)
    assert not any("france" in et and "3.5" in et and "córner" in et for et in etiquetas_if)
    assert any("-11.5 córn." in t["etiqueta"].lower() and t.get("respaldo_micropatron") for t in top5_if)
    for item in top5_if:
        assert item["score_gatekeeper"] is not None
        assert float(item["probabilidad"]) >= 0.70

    # 2. Regresion positiva: Turquia vs Belgica mantiene ganador y DNB/corners validos
    an_tb = mm.analizar(
        docs["turkey"], docs["belgium"], "Turkey", "Belgium", competicion="UEFA Nations League"
    )
    top5_tb, s1_tb, _ = sm.obtener_top5_y_seccion1_filtrados(an_tb, an_tb.metadatos)
    assert len(top5_tb) >= 3
    assert "belgium gana" in top5_tb[0]["etiqueta"].lower()
    assert len(s1_tb) >= 4


def test_fase7_gatekeeper_vetos_y_score_compuesto_sintetico() -> None:
    partidos_l = [
        _partido_sintetico(
            pid=f"gl_{i}",
            fecha=f"2026-10-{i+1:02d}",
            condicion="casa" if i < 10 else "fuera",
            equipo="Italy",
            rival=f"R{i}",
            gf=2 if i < 13 else 0,
            gc=1 if i < 13 else 1,
            ft={"xg": 1.8, "corners": 6 if i < 13 else 9, "grandes_ocasiones": 3},
            p1={"xg": 0.6, "remates_totales": 5, "corners": 3},
            p2={"xg": 1.2, "remates_totales": 10, "corners": 3 if i < 13 else 6},
            ft_rival={"xg": 0.9, "corners": 4 if i < 13 else 5},
        )
        for i in range(15)
    ]
    partidos_v = [
        _partido_sintetico(
            pid=f"gv_{i}",
            fecha=f"2026-10-{i+1:02d}",
            condicion="fuera" if i < 10 else "casa",
            equipo="France",
            rival=f"S{i}",
            gf=1 if i < 13 else 0,
            gc=1,
            ft={"xg": 1.2, "corners": 3 if i < 13 else 7},
            p1={"xg": 0.7, "remates_totales": 6, "corners": 2},
            p2={"xg": 0.5, "remates_totales": 4, "corners": 1 if i < 13 else 5},
            ft_rival={"xg": 1.1, "corners": 5 if i < 13 else 6},
        )
        for i in range(15)
    ]
    dl = _doc_sintetico("Italy", "ITA", partidos_l)
    dv = _doc_sintetico("France", "FRA", partidos_v)
    motor_mock = mm.MotorMercados(dl, dv)
    motor_mock.preparar()
    motor_mock.micropatrones_combinatorios = {
        "activos": [
            {"codigo": "MP01_ASEDIO_2T_LOCAL", "activo": True},
            {"codigo": "MP02_FRENO_CORNERS_VISITA", "activo": True},
        ],
        "codigos_activos": ["MP01_ASEDIO_2T_LOCAL", "MP02_FRENO_CORNERS_VISITA"],
        "senales_favor": ["total_corners:under_11.5", "total_goles:over_1.5"],
        "senales_contra": ["corners_visita:over_3.5", "doble_oportunidad:x2"],
    }

    s_veto_x2 = mm.Seleccion(
        id_mercado="doble_oportunidad",
        mercado="Doble oportunidad",
        evento="x2",
        etiqueta="X2 (empate o France)",
        periodo="FT",
        equipo=None,
        linea=None,
        probabilidad=0.84,
    )
    s_veto_corn = mm.Seleccion(
        id_mercado="corners_visitante",
        mercado="France - Total de tiros de esquina",
        evento="visitante_corners_over_3.5",
        etiqueta="France: más de 3.5 córneres",
        periodo="FT",
        equipo="France",
        linea=3.5,
        probabilidad=0.82,
    )
    s_ok_u_corn = mm.Seleccion(
        id_mercado="total_corners",
        mercado="Total de tiros de esquina",
        evento="corners_under_11.5",
        etiqueta="menos de 11.5 córneres",
        periodo="FT",
        equipo=None,
        linea=11.5,
        probabilidad=0.78,
    )
    s_ok_o15 = mm.Seleccion(
        id_mercado="total_goles",
        mercado="Total de goles / Número de goles",
        evento="over_1.5",
        etiqueta="más de 1.5 goles",
        periodo="FT",
        equipo=None,
        linea=1.5,
        probabilidad=0.785,
    )

    vet_x2, _, cod_x2 = mm.es_seleccion_vetada_por_micropatrones(
        s_veto_x2, motor=motor_mock, eq_l="Italy", eq_v="France"
    )
    vet_c, _, cod_c = mm.es_seleccion_vetada_por_micropatrones(
        s_veto_corn, motor=motor_mock, eq_l="Italy", eq_v="France"
    )
    assert vet_x2 is True and "MP01_ASEDIO_2T_LOCAL" in cod_x2
    assert vet_c is True and "MP02_FRENO_CORNERS_VISITA" in cod_c

    top5 = mm.seleccionar_top_5_jugadas_recomendadas(
        [s_veto_x2, s_veto_corn, s_ok_u_corn, s_ok_o15],
        motor=motor_mock,
        eq_l="Italy",
        eq_v="France",
    )
    etqs = [x["etiqueta"].lower() for x in top5]
    assert not any("x2" in e for e in etqs)
    assert not any("3.5 córneres" in e for e in etqs)
    assert len(top5) == 2
    assert all(x["respaldo_micropatron"] is True for x in top5)
    assert all(x["frecuencia_observada"] is not None and float(x["frecuencia_observada"]) >= 0.85 for x in top5)
