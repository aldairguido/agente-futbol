"""
Tests para ORDEN RONDA 9 — Punto 6 (Pendientes de la Adenda):
  (C) Tope DCN 18%: conteo de veces que toca el límite ±18% por lado y por partido.
  (D) Validador de defectos sobre los 83 partidos del banco (por tipo y por fuente).
  (E) Tamaño muestral `n` por métrica en el ticket + verificación automática texto vs cifras
      bloqueando contradicciones ('duelo parejo' >= 10 pp, 'al final' con 76'-90' = 0,
      'todos los mercados validados superan 70%' cuando jugada óptima < 70%).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from decision_cobertura import bandera_activa, banderas_comportamiento
from evaluacion_pronosticos import (
    _recolectar_pares_partidos_cacheados_ronda9,
    auditar_coherencia_texto_cifras_ronda9,
    auditar_defectos_banco_83_ronda9,
    auditar_tope_dcn_18_por_lado_y_partido,
)
import motor_inferencia as mi
import motor_mercados as mm


def test_r9_p6c_tope_dcn_18_por_lado_y_partido_sobre_referencias_y_73_cacheados():
    # Las banderas siguen apagadas por defecto fuera de la auditoría
    assert bandera_activa("usar_tope_dcn_18_pct") is False

    # 1) Subconjunto rápido de referencia (4 partidos: TB, IF, LN, COL-CHI)
    pares_73 = _recolectar_pares_partidos_cacheados_ronda9()
    assert len(pares_73) >= 70
    ref_4 = pares_73[:4]
    rep_4 = auditar_tope_dcn_18_por_lado_y_partido(ref_4)
    assert rep_4["n_partidos_evaluados"] == 4
    assert rep_4["n_lados_evaluados"] == 8

    por_id = {d["job_id"]: d for d in rep_4["detalle"]}
    assert (por_id["DESIGN::TB"]["local_toco"], por_id["DESIGN::TB"]["visitante_toco"]) == (True, True)
    assert por_id["DESIGN::TB"]["red_local"]["direccion"] == "-18%"
    assert por_id["DESIGN::TB"]["red_visitante"]["direccion"] == "+18%"
    assert (por_id["DESIGN::IF"]["local_toco"], por_id["DESIGN::IF"]["visitante_toco"]) == (False, True)
    assert por_id["DESIGN::IF"]["red_visitante"]["direccion"] == "+18%"
    assert (por_id["CTRL::LN"]["local_toco"], por_id["CTRL::LN"]["visitante_toco"]) == (False, False)
    assert (por_id["AUDIT::COL_CHI"]["local_toco"], por_id["AUDIT::COL_CHI"]["visitante_toco"]) == (True, False)
    assert por_id["AUDIT::COL_CHI"]["red_local"]["direccion"] == "-18%"

    # 2) Barrido completo de todos los partidos en caché offline (73 partidos)
    rep_all = auditar_tope_dcn_18_por_lado_y_partido(pares_73)
    assert rep_all["n_partidos_evaluados"] == 73
    assert rep_all["n_lados_evaluados"] == 146
    assert rep_all["en_red_45_dcn"]["local"] == 16
    assert rep_all["en_red_45_dcn"]["visitante"] == 23
    assert rep_all["en_post_sinc_mercados"]["local"] == 8
    assert rep_all["en_post_sinc_mercados"]["visitante"] == 15
    assert rep_all["global_cualquier_etapa"]["local"] == 17
    assert rep_all["global_cualquier_etapa"]["visitante"] == 27
    assert rep_all["global_cualquier_etapa"]["partidos_al_menos_un_lado"] == 37


def test_r9_p6d_validador_defectos_83_partidos_banco():
    rep = auditar_defectos_banco_83_ronda9()
    assert rep["n_partidos_unicos_banco_csv"] == 83
    assert rep["n_pares_equipo_partido_banco_csv"] == 94
    assert rep["n_partidos_unicos_con_matriz_fuente"] == 81

    # En el CSV normalizado del banco hay 1 discrepancia (5067299 remates rechazados)
    assert rep["banco_csv_normalizado"]["total_defectos"] == 1
    assert rep["banco_csv_normalizado"]["por_tipo"]["suma_mitades_distinta_ft"] == 1

    # En las matrices crudas de los partidos del banco se detectan los 5 tipos reales por fuente
    pre = rep["matrices_crudas_pre_saneamiento"]
    assert pre["total_defectos"] == 152
    assert pre["partidos_unicos_con_defecto"] == 59
    assert pre["por_tipo"]["empate_con_bandos_invertidos"] == 6
    assert pre["por_tipo"]["entradas_ganadas_mayor_intentadas"] == 6
    assert pre["por_tipo"]["falso_denominador_100"] == 122
    assert pre["por_tipo"]["tiros_libres_sin_invertir_igual_faltas"] == 17
    assert pre["por_fuente"]["fotmob"] == 121
    assert pre["por_fuente"]["fotmob+flashscore"] == 31

    # Post-saneamiento elimina falso_denominador_100 y entradas_ganadas>intentadas
    post = rep["matrices_post_sanear_partido_auditoria_45"]
    assert post["total_defectos"] == 24
    assert "falso_denominador_100" not in post["por_tipo"]
    assert "entradas_ganadas_mayor_intentadas" not in post["por_tipo"]


def test_r9_p6e_bloqueo_contradicciones_texto_vs_cifras():
    # 1) Bloquea "duelo parejo" cuando |P(1) - P(2)| >= 10 pp
    with pytest.raises(ValueError, match="duelo parejo"):
        mi.verificar_coherencia_texto_cifras_ticket(
            "1. Duelo parejo / Ligera ventaja para Colombia (o empate):",
            p_local=0.420,
            p_visita=0.272,
            bloquear=True,
        )

    # 2) Bloquea goles "al final" cuando 76'-90' suma 0 goles
    cc_cero = {
        "anotados": {"00'-15'": 1, "76'-90'": 0},
        "recibidos": {"00'-15'": 1, "76'-90'": 0},
    }
    with pytest.raises(ValueError, match="al final"):
        mi.verificar_coherencia_texto_cifras_ticket(
            "2. Cuidado con los goles de Equipo A al final: máxima alerta en el 76'-90'.",
            cuadro_c_loc=cc_cero,
            cuadro_c_vis=cc_cero,
            bloquear=True,
        )

    # 3) Bloquea "todos los mercados validados superan 70%" cuando la jugada óptima < 70%
    with pytest.raises(ValueError, match="superan 70%"):
        mi.verificar_coherencia_texto_cifras_ticket(
            "JUGADA ÓPTIMA TE APUESTO: Menos de 2.5 goles (69.9%)\n"
            "Verificación Cuantitativa: Todos los mercados validados superan el 70% matemático.",
            jugada_optima_pct=69.9,
            total_validadas=5,
            bloquear=True,
        )

    # 4) Bloquea bloque ADN sin n por métrica cuando requerir_n_en_adn=True
    with pytest.raises(ValueError, match="tamaños de muestra"):
        mi.verificar_coherencia_texto_cifras_ticket(
            "🧬 ADN del Enfrentamiento (Local en Casa vs. Rival de Visita):\n▸ Posesión: A 55.0% vs B 45.0%",
            requerir_n_en_adn=True,
            bloquear=True,
        )

    # 5) Emisor de veredicto ya no produce 'al final' con 76'-90'=0 ni 'Duelo parejo' con >=10 pp
    ver_cero = mi.generar_veredicto_tactico_partido(
        "EquipoA", "EquipoB",
        {"xg": 1.4}, {"xg": 0.9},
        {"local": 0.44, "empate": 0.28, "visita": 0.28},
        {"victorias": 8, "sin_derrotas": 12, "n_partidos": 15},
        {"victorias": 4, "sin_derrotas": 8, "n_partidos": 15},
        {"_meta": {"n_partidos_con_eventos": 15}, "anotados": {"00'-15'": 3, "76'-90'": 0}, "recibidos": {"00'-15'": 1, "76'-90'": 0}},
        {"_meta": {"n_partidos_con_eventos": 15}, "anotados": {"00'-15'": 2, "76'-90'": 0}, "recibidos": {"00'-15'": 2, "76'-90'": 0}},
    )
    assert "Duelo parejo" not in ver_cero
    assert "al final" not in ver_cero.lower()
    coh_v = mi.verificar_coherencia_texto_cifras_ticket(
        ver_cero, p_local=0.44, p_visita=0.28, flujo_76_90=0, bloquear=True,
    )
    assert coh_v["es_coherente"] is True


def test_r9_p6e_ticket_colombia_chile_muestra_n_real_y_clusters_no_duelo_parejo():
    rep = auditar_coherencia_texto_cifras_ronda9()
    assert rep["n_metricas_local"]["xg"] == {"n_cond": 6, "n_gen": 11}
    assert rep["n_metricas_visita"]["goles_evitados"] == {"n_cond": 3, "n_gen": 5}
    assert rep["coherencia_ticket"]["es_coherente"] is True
    assert rep["coherencia_veredicto"]["es_coherente"] is True

    adn_txt = "\n".join(rep["bloque_adn_con_n"])
    assert "Colombia 57.5% (n=7c/15g)" in adn_txt
    assert "1.59 xG (n=6c/11g)" in adn_txt
    assert "1.12 xG (n=3c/5g)" in adn_txt
    assert "+0.31 GEv (n=3c/5g)" in adn_txt
    assert "Ligera ventaja para Colombia" in rep["veredicto_colombia_chile"]
    assert "Duelo parejo" not in rep["veredicto_colombia_chile"]

    # clusters_marcador con 42.0% vs 27.2% (14.8 pp) no devuelve 'Duelo Parejo'
    mat = [[0.15, 0.10, 0.04, 0.01], [0.18, 0.11, 0.04, 0.01], [0.13, 0.07, 0.03, 0.01], [0.06, 0.03, 0.02, 0.01]]
    cl = mm.clusters_marcador(mat, "Colombia", "Chile")
    assert cl["favorito"]["nombre"] != "Duelo Parejo"
    assert "DUELO PAREJO" not in cl["linea_pie"]
