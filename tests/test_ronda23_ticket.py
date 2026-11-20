"""Tests de regresión de Ronda 23: P26 en Veredicto #4, Veredicto Final sin 12,
Jugada Óptima sin fallback bajo umbral y Veredicto #1 coherente con mercado abierto."""
from __future__ import annotations

from types import SimpleNamespace


# ── 1. P26 del Veredicto #4 pertenece al equipo de mkt_2t, no al favorito 1X2 ──


def test_r23_p1_p26_veredicto_4_usa_equipo_de_seleccion_cincinnati():
    from motor_inferencia import generar_veredicto_tactico_partido

    ver = generar_veredicto_tactico_partido(
        nombre_local="Orlando City",
        nombre_visita="FC Cincinnati",
        # Favorito 1X2 es Orlando City (local 42.2% vs visita 38.9%)
        probs={"local": 0.422, "empate": 0.189, "visita": 0.389, "lambda_local": 2.25, "lambda_visita": 2.12},
        ratings_loc={"Final_Attack_Strength": 70.0},
        ratings_vis={"Final_Attack_Strength": 68.0},
        patrones_loc={"posesion_pct": 51.0, "P26_Poder_Cierre_2T": 0.590},
        patrones_vis={"posesion_pct": 49.0, "P26_Poder_Cierre_2T": 0.774},
        cuadro_b_loc={"sin_derrotas": 10, "victorias": 6, "n_partidos": 15},
        cuadro_b_vis={"sin_derrotas": 11, "victorias": 8, "n_partidos": 15},
        cuadro_c_loc={"_meta": {"n_partidos_con_eventos": 15}},
        cuadro_c_vis={"_meta": {"n_partidos_con_eventos": 15}},
        top5_podio=[
            {"etiqueta": "Gol en ambos tiempos (FT)", "probabilidad": 0.80, "estado": "mantiene", "periodo": "FT"},
            {
                "etiqueta": "FC Cincinnati: más de 0.5 goles (2T)",
                "mercado": "FC Cincinnati - Total de goles",
                "evento": "visitante_over_0.5",
                "equipo": "FC Cincinnati",
                "periodo": "2T",
                "probabilidad": 0.73,
                "estado": "mantiene",
            },
        ],
        para_canvas=True,
    )
    assert "77% de poder de cierre P26 (FC Cincinnati)" in ver
    assert "59% de poder de cierre P26" not in ver


# ── 2. Veredicto Final excluye 12 y si nada supera 70% devuelve sin recomendación y stake 0% ──


def test_r23_p2_veredicto_final_excluye_12_y_no_recomienda_bajo_umbral():
    import informe as inf

    # Caso Orlando–Cincinnati: p_1=0.422, p_x=0.189, p_2=0.389 -> 12=81.1%, 1X=61.1%, X2=57.8%
    mat = [[0.0] * 3 for _ in range(3)]
    mat[1][0] = 0.422  # local
    mat[0][0] = 0.189  # empate
    mat[0][1] = 0.389  # visita

    motor_stub = SimpleNamespace(
        nombre_local="Orlando City",
        nombre_visitante="FC Cincinnati",
        matriz_ft=mat,
        tasas=SimpleNamespace(lambda_local_ft=2.25, lambda_visitante_ft=2.12),
        recursivo={},
        doc_local={"partidos": []},
        doc_visitante={"partidos": []},
    )
    an_stub = SimpleNamespace(
        metadatos={"equipo_local": "Orlando City", "equipo_visitante": "FC Cincinnati"},
        motor=motor_stub,
        parametros=SimpleNamespace(umbral=0.70),
        selecciones_filtradas=[],
        grupos={"mantiene": [], "sensible": []},
    )

    filas = inf.construir_filas_ticket_valor(an_stub, cuotas_flashscore={})
    assert all("12 —" not in f["mercado"] and not f["mercado"].startswith("12 ") for f in filas)

    filas_leg = inf._construir_filas_ticket_valor_legacy_5e8(an_stub, cuotas_flashscore={})
    assert all("12 —" not in f["mercado"] and not f["mercado"].startswith("12 ") for f in filas_leg)

    texto_3p = inf.redactar_analisis_3_parrafos(an_stub, cuotas_flashscore={})
    assert "12 —" not in texto_3p
    assert "Stake 0%" in texto_3p
    assert "No hay recomendación" in texto_3p


# ── 3. JUGADA ÓPTIMA TE APUESTO: sin fallback 37.3% y sin "óptima"/"te apuesto" < 70% ──


def test_r23_p3_clusters_marcador_sin_jugada_optima_en_partido_abierto_437():
    import math
    from motor_mercados import clusters_marcador

    # Matriz Poisson FT con λ_L=2.25, λ_V=2.12 (λ_total=4.37, p_u35 ≈ 37.3%)
    lam_l, lam_v = 2.25, 2.12
    mat = [
        [
            (math.exp(-lam_l) * (lam_l ** i) / math.factorial(i))
            * (math.exp(-lam_v) * (lam_v ** j) / math.factorial(j))
            for j in range(10)
        ]
        for i in range(10)
    ]
    cl = clusters_marcador(mat, "Orlando City", "FC Cincinnati")
    assert "Menos de 3.5 goles" not in cl["jugada_optima"]
    assert cl["jugada_optima"] == "Sin jugada óptima ≥70%"
    assert "JUGADA ÓPTIMA" not in cl["linea_pie"]


def test_r23_p3_clusters_marcador_bajo_umbral_no_dice_optima_ni_te_apuesto():
    import math
    from motor_mercados import clusters_marcador

    # Caso donde entra una rama elif (p. ej. p_dc_val ≈ 67.5% < 70%): no puede decir "óptima" ni "te apuesto"
    lam_l, lam_v = 1.60, 1.35
    mat = [
        [
            (math.exp(-lam_l) * (lam_l ** i) / math.factorial(i))
            * (math.exp(-lam_v) * (lam_v ** j) / math.factorial(j))
            for j in range(10)
        ]
        for i in range(10)
    ]
    cl = clusters_marcador(mat, "Local FC", "Visita FC")
    assert cl["jugada_optima"] == "Sin jugada óptima ≥70%"
    assert "Sin jugada óptima ≥70%" in cl["linea_pie"]
    assert "JUGADA ÓPTIMA" not in cl["linea_pie"]


# ── 4. Veredicto #1 coherente para mercado abierto (gol en ambos tiempos) ──


def test_r23_p4_veredicto_1_gol_en_ambos_tiempos_no_dice_prioriza_cautela():
    from motor_inferencia import generar_veredicto_tactico_partido

    ver = generar_veredicto_tactico_partido(
        nombre_local="Orlando City",
        nombre_visita="FC Cincinnati",
        probs={"local": 0.422, "empate": 0.189, "visita": 0.389, "lambda_local": 2.25, "lambda_visita": 2.12},
        ratings_loc={"Final_Attack_Strength": 70.0},
        ratings_vis={"Final_Attack_Strength": 68.0},
        patrones_loc={"posesion_pct": 51.0, "P26_Poder_Cierre_2T": 0.590},
        patrones_vis={"posesion_pct": 49.0, "P26_Poder_Cierre_2T": 0.774},
        cuadro_b_loc={"sin_derrotas": 10, "victorias": 6, "n_partidos": 15},
        cuadro_b_vis={"sin_derrotas": 11, "victorias": 8, "n_partidos": 15},
        cuadro_c_loc={"_meta": {"n_partidos_con_eventos": 15}},
        cuadro_c_vis={"_meta": {"n_partidos_con_eventos": 15}},
        top5_podio=[{"etiqueta": "Gol en ambos tiempos (FT)", "probabilidad": 0.80, "estado": "mantiene"}],
        para_canvas=True,
    )
    assert "prioriza cautela" not in ver
    assert "partido abierto (4.37 goles esperados)" in ver
