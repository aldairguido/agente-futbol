"""Tests para ORDEN RONDA 12 - Punto 4 (Prorrateo Pasada 3b, validador extendido y 6 empates)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import banco_historico as bh
from decision_cobertura import bandera_activa, banderas_comportamiento
import evaluacion_pronosticos as ep
import fuentes_xg as fxg


def test_ronda12_p4_bandera_pasada3b_sin_dato_y_alias():
    assert bandera_activa("usar_sin_dato_si_mitades_no_suman_ft") is False

    def _matriz_sintetica() -> fxg.MatrizPartido:
        mat = fxg.MatrizPartido(
            id_partido="9990001",
            fecha="2026-11-12",
            torneo="Test Cup",
            equipo="Local",
            rival="Rival",
            condicion="casa",
            goles_equipo=1,
            goles_rival=1,
        )
        filas_ft = [
            {"clave": "corners", "etiqueta": "Corners", "categoria": "A", "equipo": 10.0, "rival": 6.0},
            {"clave": "corneres", "etiqueta": "Corneres", "categoria": "A", "equipo": 10.0, "rival": 6.0},
            {"clave": "entradas", "etiqueta": "Entradas", "categoria": "D", "equipo": 17.0, "rival": 12.0},
        ]
        filas_1t = [
            {"clave": "corners", "etiqueta": "Corners", "categoria": "A", "equipo": 4.0, "rival": 3.0},
            {"clave": "corneres", "etiqueta": "Corneres", "categoria": "A", "equipo": 4.0, "rival": 3.0},
            {"clave": "entradas", "etiqueta": "Entradas", "categoria": "D", "equipo": 11.0, "rival": 5.0},
        ]
        filas_2t = [
            {"clave": "corners", "etiqueta": "Corners", "categoria": "A", "equipo": 2.0, "rival": 3.0},
            {"clave": "corneres", "etiqueta": "Corneres", "categoria": "A", "equipo": 2.0, "rival": 3.0},
            {"clave": "entradas", "etiqueta": "Entradas", "categoria": "D", "equipo": 5.0, "rival": 3.0},
        ]
        mat.periodos = {
            "FT": fxg.PeriodoPartido("FT", filas_ft, []),
            "1T": fxg.PeriodoPartido("1T", filas_1t, []),
            "2T": fxg.PeriodoPartido("2T", filas_2t, []),
        }
        return mat

    # Con bandera False (defecto): prorratea proporcionalmente y no cambia conducta
    m_off = _matriz_sintetica()
    with banderas_comportamiento(usar_sin_dato_si_mitades_no_suman_ft=False):
        fxg.consolidar_matriz_completa(m_off)
    map_off = {per: {f["clave"]: f for f in m_off.periodos[per].filas} for per in ("FT", "1T", "2T")}
    assert map_off["FT"]["entradas"]["equipo"] == 17.0
    assert map_off["1T"]["entradas"]["equipo"] + map_off["2T"]["entradas"]["equipo"] == 17.0
    assert any(inc.get("reescalado_proporcional") for inc in m_off.inconsistencias_aritmeticas)

    # Con bandera True: FT queda intacto y 1T/2T (incluyendo alias) pasan a None / sin_dato
    m_on = _matriz_sintetica()
    with banderas_comportamiento(usar_sin_dato_si_mitades_no_suman_ft=True):
        fxg.consolidar_matriz_completa(m_on)
    map_on = {per: {f["clave"]: f for f in m_on.periodos[per].filas} for per in ("FT", "1T", "2T")}
    assert map_on["FT"]["corners"]["equipo"] == 10.0
    assert map_on["FT"]["entradas"]["equipo"] == 17.0
    for cl_chk in ("corners", "corneres", "entradas"):
        assert map_on["1T"][cl_chk]["equipo"] is None
        assert map_on["2T"][cl_chk]["equipo"] is None
        assert m_on.procedencia["1T"]["equipo"][cl_chk] == "sin_dato"
        assert m_on.procedencia["2T"]["equipo"][cl_chk] == "sin_dato"
    assert not any(inc.get("reescalado_proporcional") for inc in m_on.inconsistencias_aritmeticas)
    # rival corners (3+3 == 6) debe seguir intacto
    assert map_on["1T"]["corners"]["rival"] == 3.0
    assert map_on["2T"]["corners"]["rival"] == 3.0


def test_ronda12_p4_validador_extendido_colombia_chile_entradas_y_6_empates():
    rep = ep.auditar_pasada_3b_y_banco_ronda12(ejecutar_vivo=False, guardar_ruta=None)
    assert rep["bandera_usar_sin_dato_si_mitades_no_suman_ft_defecto"] is False

    # Inventario vivo persistido de Pasada 3b en 30 matrices y 83 banco
    vivo = rep["inventario_pasada_3b_fuente_viva"]
    assert vivo is not None
    assert vivo["matrices_30"]["n_partidos_grupo"] == 30
    assert vivo["matrices_30"]["flag_off_prorrateadas"] == 147
    assert vivo["matrices_30"]["flag_on_prorrateadas"] == 0
    assert vivo["matrices_30"]["flag_on_ft_intacto_y_mitades_none"] is True
    assert vivo["banco_83"]["flag_off_prorrateadas"] == 337
    assert vivo["banco_83"]["flag_on_prorrateadas"] == 0
    assert vivo["banco_83"]["flag_on_ft_intacto_y_mitades_none"] is True

    # Validador extendido a Colombia, Chile y entradas
    val_full = rep["validador_extendido"]["live_completo_con_colombia_chile_y_entradas"]
    assert val_full["incluye_colombia_chile"] is True
    assert val_full["incluye_entradas"] is True
    assert val_full["n_partidos_unicos_colombia_chile"] == 30
    assert val_full["n_partidos_unicos_con_matriz_fuente"] == 111
    assert val_full["resumen_por_ambito"]["banco_83"]["pre_total_defectos"] == 123
    assert val_full["resumen_por_ambito"]["banco_83"]["post_total_defectos"] == 1
    assert "empate_con_bandos_invertidos" not in val_full["resumen_por_ambito"]["banco_83"]["pre_por_tipo"]
    assert val_full["resumen_por_ambito"]["colombia_chile_30"]["pre_total_defectos"] == 34
    assert val_full["resumen_por_ambito"]["colombia_chile_30"]["post_total_defectos"] == 4

    # Validar_coherencia_1t_2t_ft incluye entradas/entradas_ganadas y won <= attempted
    ps = rep["validador_extendido"]["prueba_sintetica_validar_coherencia_entradas"]
    assert ps["es_coherente"] is False
    assert "entradas" in ps["variables_con_error"]
    assert "entradas_ganadas" in ps["variables_con_error"]
    assert any(d["motivo"] == "entradas_ganadas_mayor_intentadas_1T" for d in ps["discrepancias"])

    # Correccion de los 6 empates con respaldo intacto
    c6 = rep["correccion_6_empates_banco"]
    assert c6["n_archivos_respaldados"] == 11
    assert len(c6["comparacion_6_partidos"]) == 6
    for item in c6["comparacion_6_partidos"]:
        assert item["antes_respaldo"]["bandos_invertidos"] is True
        assert item["despues_flashscore"]["bandos_invertidos"] is False
    assert rep["validador_extendido"]["respaldo_pre_r12_banco_83"]["pre_por_tipo"]["empate_con_bandos_invertidos"] == 6
    assert rep["validador_extendido"]["live_banco_83"]["pre_total"] == 123
    assert c6["invarianza_lambda_londrina_nautico"]["soloA"]["invariante"] is True
    assert c6["invarianza_lambda_londrina_nautico"]["AB"]["invariante"] is True

    # Archivo JSON persistido coincide con el reporte offline
    art = json.loads((Path(ROOT) / "datos" / "ronda12_punto4_pasada3b_y_banco.json").read_text(encoding="utf-8"))
    assert art["inventario_pasada_3b_fuente_viva"]["matrices_30"]["flag_off_prorrateadas"] == 147
    assert art["validador_extendido"]["live_banco_83"]["post_total"] == 1
