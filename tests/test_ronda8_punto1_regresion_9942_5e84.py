from __future__ import annotations

from evaluacion_pronosticos import (
    _macro_familia_ronda8,
    analizar_regresion_994209a_vs_5e8425f,
)


def _sample_regresion_rows() -> list[dict]:
    """
    Construye un conjunto determinista de 6 partidos OOS (más 1 partido de diseño TB que
    debe ser excluido automáticamente) donde:
      - Córneres mejora fuertemente en v9942 frente a v5e84 por suavizado Binomial Negativa.
      - Goles/1X2 cambia levemente en v9942 por P13_Portero_Goles_Evitados.
      - arch supera a v5e84 en Goles/1X2 y Tarjetas por ausencia de inflación post-red.
    """
    rows = []
    partidos = [
        ("p1", "Galatasaray vs Barcelona"),
        ("p2", "Villarreal vs Elche"),
        ("p3", "Everton vs Chelsea"),
        ("p4", "Inter vs Club Brugge"),
        ("p5", "Lens vs Sporting CP"),
        ("p6", "Union Berlin vs Borussia Dortmund"),
    ]
    for idx, (pid, nom) in enumerate(partidos):
        # Córneres: v5e84 sobreconfiado en cola (0.92 cuando ocurre 0 en varios), v9942 suaviza a 0.70
        y_c = 0 if idx % 2 == 0 else 1
        p5_c = 0.88 if y_c == 0 else 0.78
        p9_c = 0.66 if y_c == 0 else 0.74
        par_c = 0.74 if y_c == 0 else 0.77
        rows.append({
            "id_partido": pid,
            "partido": nom,
            "mercado": "Total de tiros de esquina",
            "evento_ocurrio": y_c,
            "probs": {"arch": par_c, "v5e84": p5_c, "v9942": p9_c},
        })
        # Goles Partido/Mitades: arch mejor calibrado que v5e84; v9942 mejora ligeramente en p4/p5
        y_g = 1 if idx % 3 != 0 else 0
        par_g = 0.72 if y_g == 1 else 0.32
        p5_g = 0.65 if y_g == 1 else 0.42
        p9_g = (0.67 if idx in (3, 4) else 0.65) if y_g == 1 else (0.39 if idx in (3, 4) else 0.42)
        rows.append({
            "id_partido": pid,
            "partido": nom,
            "mercado": "Total de goles",
            "evento_ocurrio": y_g,
            "probs": {"arch": par_g, "v5e84": p5_g, "v9942": p9_g},
        })
        # Tarjetas: idénticas entre v5e84 y v9942, arch mejor
        rows.append({
            "id_partido": pid,
            "partido": nom,
            "mercado": "Total de tarjetas",
            "evento_ocurrio": 1,
            "probs": {"arch": 0.80, "v5e84": 0.72, "v9942": 0.72},
        })

    # Partido de diseño que debe quedar excluido
    rows.append({
        "id_partido": "tb_diseno",
        "partido": "Turkey vs Belgium",
        "mercado": "Total de tiros de esquina",
        "evento_ocurrio": 0,
        "probs": {"arch": 0.85, "v5e84": 0.85, "v9942": 0.10},
    })
    return rows


def test_macro_familia_ronda8_mapea_cinco_grupos() -> None:
    assert _macro_familia_ronda8("Total de tiros de esquina") == "Córneres"
    assert _macro_familia_ronda8("Total de goles") == "Goles Partido/Mitades"
    assert _macro_familia_ronda8("Doble oportunidad") == "Resultado/DC/Hándicap"
    assert _macro_familia_ronda8("Total de tarjetas") == "Tarjetas"
    assert _macro_familia_ronda8("Ambos equipos anotan") == "Goles Equipo/BTTS/1erGol"


def test_analizar_regresion_994209a_vs_5e8425f_desglosa_y_atribuye_commits() -> None:
    obs = _sample_regresion_rows()
    res = analizar_regresion_994209a_vs_5e8425f(obs, n_boot=300, seed=42)

    assert res["n_partidos_unicos"] == 6
    assert res["n_filas"] == 18  # 6 partidos * 3 mercados; Turkey vs Belgium excluido

    tot_v = res["total"]["versiones"]
    assert set(tot_v.keys()) == {"arch", "v5e84", "v9942"}
    assert tot_v["v9942"]["log_loss"] < tot_v["v5e84"]["log_loss"]
    assert tot_v["arch"]["brier"] < tot_v["v5e84"]["brier"]

    mf = res["por_macro_familia"]
    assert "Córneres" in mf
    assert "Goles Partido/Mitades" in mf
    assert "Tarjetas" in mf
    assert mf["Córneres"]["n_filas_cambiadas_9942_vs_5e84"] == 6
    assert mf["Tarjetas"]["n_filas_cambiadas_9942_vs_5e84"] == 0
    assert mf["Córneres"]["aporte_d_log_loss_pct"] > 80.0

    atrib = res["atribucion_commits"]
    assert atrib["corners"]["commit"] == "9593ba6"
    assert atrib["lambda"]["commit"] == "c59c103"
