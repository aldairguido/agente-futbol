import math
import pytest


def _matriz_poisson(lam_l: float, lam_v: float, n: int = 10) -> list[list[float]]:
    return [
        [
            (math.exp(-lam_l) * (lam_l ** i) / math.factorial(i))
            * (math.exp(-lam_v) * (lam_v ** j) / math.factorial(j))
            for j in range(n)
        ]
        for i in range(n)
    ]


def test_r24_b1_jugada_optima_exige_70_y_pertenencia_sec1_2_y_sin_te_apuesto_en_md():
    from motor_mercados import clusters_marcador, analizar
    from test_motor_mercados import doc_sintetico

    # 1) Partido abierto donde ninguna candidata de clusters llega a 70%: "Sin jugada óptima ≥70%"
    mat_abierto = _matriz_poisson(2.25, 2.12)
    cl_abierto = clusters_marcador(mat_abierto, "Orlando City", "FC Cincinnati")
    assert cl_abierto["jugada_optima"] == "Sin jugada óptima ≥70%"
    assert "ℹ️ Sin jugada óptima ≥70%" in cl_abierto["linea_pie"]
    assert "TE APUESTO" not in cl_abierto["linea_pie"]

    # 2) Candidata >= 70% en matriz (p. ej. favorito claro o DC >= 70%), pero ausente de Sección 1 y 2
    mat_fav = _matriz_poisson(2.40, 0.55)
    cl_sin_sec = clusters_marcador(
        mat_fav,
        "Local FC",
        "Visita FC",
        selecciones_sec1_2=[{"evento": "corners_over_7.5", "periodo": "FT", "probabilidad": 0.80}],
    )
    assert cl_sin_sec["jugada_optima"] == "Sin jugada óptima ≥70%"
    assert "ℹ️ Sin jugada óptima ≥70%" in cl_sin_sec["linea_pie"]

    # 3) Misma matriz con la selección presente en Sección 1 o 2: sí emite jugada óptima sin "TE APUESTO"
    cl_con_sec = clusters_marcador(
        mat_fav,
        "Local FC",
        "Visita FC",
        selecciones_sec1_2=[{"evento": "1", "periodo": "FT", "probabilidad": 0.78}],
    )
    assert "1x2 : Local FC" in cl_con_sec["jugada_optima"]
    assert "🎯 JUGADA ÓPTIMA:" in cl_con_sec["linea_pie"]
    assert "TE APUESTO" not in cl_con_sec["linea_pie"]

    # 4) Informe .md generado por analizar() no contiene "TE APUESTO"
    doc_l = doc_sintetico("Local FC", [("casa", 2, 0), ("fuera", 2, 1)] * 8, xg=[(1.9, 0.7)] * 16)
    doc_v = doc_sintetico("Visita FC", [("fuera", 0, 2), ("casa", 1, 2)] * 8, xg=[(0.8, 1.8)] * 16)
    an = analizar(doc_l, doc_v, "Local FC", "Visita FC")
    assert "TE APUESTO" not in an.informe.upper()
