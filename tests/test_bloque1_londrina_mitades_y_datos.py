"""
Pruebas automatizadas del Bloque 1 sobre datos REALES de Londrina vs Náutico (no sintéticos).
Verifica:
  1.1. Marcador por mitades 1T/2T: 27 matrices válidas con etiqueta [derivado_aritmético]
       y exclusión estricta de [02] (Londrina 1-3 America MG), [09] (Londrina 1-2 Fortaleza)
       y [27] (Nautico 1-0 Botafogo SP). Denominadores de Ganador 1T: 2/13 (15.4%) y 3/14 (21.4%).
  1.2. 6 métricas sin cobertura tratadas como sin_dato (grandes_ocasiones_falladas, xg_balon_parado,
       duelos_suelo_pct, duelos_aereos_pct, bloqueos, recuperaciones).
  1.3. Partido [27/30] (Nautico 1-0 Botafogo SP) sin xG 1.00 sintético y cabecera de muestra '15 vs 14 con detalle'.
  1.4. Corrección de pases_ratio (466/534 87%) y pases_tercio_final_pct en 1T (75/98 77%) y 2T (61/84 73%) en Matriz [28/30].
"""
import json
import os
import pytest

from auditoria_historica import (
    reconstruir_marcador_mitades_partido,
    _formatear_valor_metrica_45,
    formatear_matriz_45_partido,
    _construir_tabla_resumen_inicial,
)
from motor_inferencia import (
    _extraer_goles_item,
    calcular_cuadro_b_tendencias_15p as analizar_desempeno_reciente,
    generar_veredicto_tactico_partido,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_LONDRINA = os.path.join(BASE_DIR, "informes", "auditorias", "2026-11-11", "auditoria_londrina_2026-11-11_085324.json")
RUTA_NAUTICO = os.path.join(BASE_DIR, "informes", "auditorias", "2026-11-11", "auditoria_nautico_2026-11-11_085344.json")


@pytest.fixture(scope="module")
def datos_londrina_nautico():
    assert os.path.exists(RUTA_LONDRINA), f"No existe archivo real de Londrina: {RUTA_LONDRINA}"
    assert os.path.exists(RUTA_NAUTICO), f"No existe archivo real de Náutico: {RUTA_NAUTICO}"
    with open(RUTA_LONDRINA, "r", encoding="utf-8") as f:
        data_l = json.load(f)
    with open(RUTA_NAUTICO, "r", encoding="utf-8") as f:
        data_n = json.load(f)
    partidos_l = data_l.get("partidos", [])
    partidos_n = data_n.get("partidos", [])
    assert len(partidos_l) == 15
    assert len(partidos_n) == 15
    return partidos_l, partidos_n, data_l, data_n


def test_1_1_marcador_mitades_27_validas_y_3_excluidas(datos_londrina_nautico):
    partidos_l, partidos_n, _, _ = datos_londrina_nautico
    todos = partidos_l + partidos_n  # 30 matrices en orden 1..30
    assert len(todos) == 30

    validos_cnt = 0
    inconsistentes_cnt = 0
    sin_datos_cnt = 0

    indices_excluidos = []

    for idx, p in enumerate(todos, start=1):
        cond = str(p.get("condicion") or "casa").lower()
        lado_l = "equipo" if cond in ("casa", "local") else "rival"
        lado_v = "rival" if cond in ("casa", "local") else "equipo"

        gl1, gv1, gl2, gv2, es_valido, tag = reconstruir_marcador_mitades_partido(p, lado_l, lado_v)

        if es_valido:
            validos_cnt += 1
            assert tag == "[derivado_aritmético]"
            assert gl1 is not None and gv1 is not None and gl2 is not None and gv2 is not None
        else:
            indices_excluidos.append(idx)
            assert tag == "sin_dato"
            assert gl1 is None and gv1 is None and gl2 is None and gv2 is None

    # Excluidos deben ser exactamente [02], [09] y [27]
    assert validos_cnt == 27
    assert len(indices_excluidos) == 3
    assert indices_excluidos == [2, 9, 27]

    # Verificar Cuadro B para Londrina: 2 victorias en 1T de 13 con datos válidos (15.4%)
    cb_l = analizar_desempeno_reciente(partidos_l, "Londrina")
    assert cb_l["ganador_1t"]["total"] == 13
    assert cb_l["ganador_1t"]["conteo"] == 2
    assert cb_l["ganador_1t"]["pct"] == 15.4

    # Verificar Cuadro B para Náutico: 3 victorias en 1T de 14 con datos válidos (21.4%)
    cb_n = analizar_desempeno_reciente(partidos_n, "Náutico")
    assert cb_n["ganador_1t"]["total"] == 14
    assert cb_n["ganador_1t"]["conteo"] == 3
    assert cb_n["ganador_1t"]["pct"] == 21.4


def test_1_2_metricas_sin_cobertura_como_sin_dato(datos_londrina_nautico):
    partidos_l, partidos_n, _, _ = datos_londrina_nautico
    metricas_sin_cob = [
        "grandes_ocasiones_falladas",
        "xg_balon_parado",
        "duelos_suelo_pct",
        "duelos_aereos_pct",
        "bloqueos",
        "recuperaciones",
    ]
    for p in (partidos_l + partidos_n):
        per = p.get("periodos") or {}
        for per_name in ("FT", "1T", "2T"):
            f_map = {f["clave"]: f for f in (per.get(per_name) or {}).get("filas", [])}
            for clave in metricas_sin_cob:
                for lado in ("equipo", "rival"):
                    val = f_map.get(clave, {}).get(lado)
                    formatted = _formatear_valor_metrica_45(clave, val, f_map, lado)
                    assert formatted == "sin_dato", f"Error en clave {clave} ({per_name}): esperado 'sin_dato', obtenido '{formatted}'"


def test_1_3_partido_27_sin_xg_sintetico_y_muestra_con_detalle(datos_londrina_nautico):
    _, partidos_n, _, _ = datos_londrina_nautico
    # Partido 12 de Náutico es el #27 total (2026-09-03 Nautico 1-0 Botafogo SP, ID UuT586Vo)
    p27 = partidos_n[11]
    assert p27.get("id_partido") == "UuT586Vo"

    # Verificar que el marcador oficial FT se conserva
    m = p27.get("marcador") or {}
    assert m.get("equipo") == 1
    assert m.get("rival") == 0

    # Las mitades deben ser sin_dato
    cond = str(p27.get("condicion") or "casa").lower()
    lado_l = "equipo" if cond in ("casa", "local") else "rival"
    lado_v = "rival" if cond in ("casa", "local") else "equipo"
    gl1, gv1, gl2, gv2, ok, _ = reconstruir_marcador_mitades_partido(p27, lado_l, lado_v)
    assert not ok
    assert gl1 is None and gv1 is None

    # En la matriz formateada, el encabezado debe reflejar 1T: sin_dato | 2T: sin_dato
    mat_txt = formatear_matriz_45_partido(p27, k=27, total_partidos=30)
    assert "1T: sin_dato | 2T: sin_dato" in mat_txt


def test_1_4_pases_ratio_y_tercio_final_matriz_28(datos_londrina_nautico):
    _, partidos_n, _, _ = datos_londrina_nautico
    # Matriz [28/30]: partido 13 de Náutico (2026-08-28 Nautico vs Athletic Club, id zwWBNcPE)
    p28 = partidos_n[12]
    assert p28.get("id_partido") == "zwWBNcPE"

    per = p28.get("periodos") or {}
    f_ft = {f["clave"]: f for f in (per.get("FT") or {}).get("filas", [])}
    f_1t = {f["clave"]: f for f in (per.get("1T") or {}).get("filas", [])}
    f_2t = {f["clave"]: f for f in (per.get("2T") or {}).get("filas", [])}

    # FT pases_ratio: 466 completados / 534 totales (87%)
    str_ratio_ft = _formatear_valor_metrica_45("pases_ratio", f_ft.get("pases_ratio", {}).get("equipo"), f_ft, "equipo")
    assert str_ratio_ft == "466/534 (87%)"

    # 1T pases_tercio_final_pct: 75 completados / 98 totales (77%)
    str_tercio_1t = _formatear_valor_metrica_45("pases_tercio_final_pct", f_1t.get("pases_tercio_final_pct", {}).get("equipo"), f_1t, "equipo")
    assert str_tercio_1t == "75/98 (77%)"

    # 2T pases_tercio_final_pct: 61 completados / 84 totales (73%)
    str_tercio_2t = _formatear_valor_metrica_45("pases_tercio_final_pct", f_2t.get("pases_tercio_final_pct", {}).get("equipo"), f_2t, "equipo")
    assert str_tercio_2t == "61/84 (73%)"


def test_1_5_veredicto_tactico_sin_mercados_ficticios(datos_londrina_nautico):
    # Cuando no hay mercado de tarjetas ni 2T validado >= 70%, el veredicto no inventa
    # "+1.5 Goles 2T (72%)" ni "+3.5 Tarjetas (76%)"
    veredicto = generar_veredicto_tactico_partido(
        nombre_local="Londrina",
        nombre_visita="Náutico",
        patrones_loc={"xg": 1.10, "xg_2t": 0.89, "posesion_pct": 53.1, "faltas": 13.9},
        patrones_vis={"xg": 0.90, "xg_2t": 0.60, "posesion_pct": 46.9, "faltas": 14.6},
        probs={"local": 0.40, "empate": 0.32, "visita": 0.28},
        cuadro_b_loc={"victorias": 3, "sin_derrotas": 9, "n_partidos": 15},
        cuadro_b_vis={"victorias": 3, "sin_derrotas": 9, "n_partidos": 15},
        cuadro_c_loc={},
        cuadro_c_vis={},
        top5_podio=[
            {"etiqueta": "Menos de 2.5 Goles (FT)", "probabilidad": 0.82},
            {"etiqueta": "Menos de 3.5 Goles (FT)", "probabilidad": 0.86},
        ],
    )
    # Prohibido "+1.5 Goles al 72%" y "+3.5 Tarjetas (FT) (76%)"
    assert "+1.5 Goles al 72%" not in veredicto
    assert "+3.5 Tarjetas (FT) (76%)" not in veredicto
    assert "Sin líneas de tarjetas validadas >= 70%" in veredicto
    assert "Sin líneas de 2T validadas >= 70%" in veredicto
    assert "[15P general]" in veredicto
