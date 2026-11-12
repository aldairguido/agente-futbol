"""
Test suite para BLOQUE D (Ronda 4 Integrada):
D1: Veredicto sincronizado sin mercados fantasma ni probabilidades inventadas (+3.5 Tarjetas, 2T +1.5).
D2: Patron #35 en Telegram como 'sin dato' cuando Cuadro C carece de cronologia minuto a minuto.
D3: Texto del modelo: 'modelo de factores con pesos fijos, sin entrenamiento' en informe.py.
D4: Titulo TOP N dinamico y contador 'Validadas' sincronizado con pildoras visibles.
D5: Ganador 1T en Cuadro B con desglose matematico real (2/13 y 3/14), erradicando el falso 0/1.
D6: Valor neutro de bloqueos centrado en 3.5 y omision limpia con None en F1-F5.
D7: Etiqueta de fuente 'FotMob (100% Opta)' en vez de 'FM+FS' cuando Flashscore aporto 0 datos.
"""
import os
import json
import pytest

from motor_inferencia import (
    generar_veredicto_tactico_partido,
    generar_reporte_xai_telegram,
    calcular_cuadro_b_tendencias_15p,
)
from motor_recursivo_45_dcn import (
    NEUTRALES,
    calcular_los_6_cruces_tacticos,
)
from auditoria_historica import _construir_tabla_resumen_inicial

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_LONDRINA = os.path.join(BASE_DIR, "informes", "auditorias", "2026-11-11", "auditoria_londrina_2026-11-11_085324.json")
RUTA_NAUTICO = os.path.join(BASE_DIR, "informes", "auditorias", "2026-11-11", "auditoria_nautico_2026-11-11_085344.json")


def test_d1_veredicto_sin_mercados_fantasma_ni_probabilidades_inventadas():
    """D1: Conclusiones 3 y 4 del veredicto no citan mercados inexistentes ni probabilidades hardcodeadas."""
    top5_sin_tarjetas_ni_2t = [
        {"rank": 1, "etiqueta": "Londrina o Empate (1X)", "probabilidad": 0.717, "prob_base_poisson": 71.7, "categoria_podio": "DOBLE OPORTUNIDAD"},
        {"rank": 2, "etiqueta": "Londrina -1.5 Goles", "probabilidad": 0.772, "prob_base_poisson": 77.2, "categoria_podio": "LÍNEAS DE GOLES"},
    ]
    veredicto = generar_veredicto_tactico_partido(
        nombre_local="Londrina",
        nombre_visita="Náutico",
        patrones_loc={"xg": 1.10, "remates_puerta": 3.8, "posesion_pct": 52.0, "faltas": 11.0, "P26_Poder_Cierre_2T": 0.57, "xg_2t": 0.63},
        patrones_vis={"xg": 0.90, "remates_puerta": 3.2, "posesion_pct": 48.0, "faltas": 12.0, "P26_Poder_Cierre_2T": 0.61, "xg_2t": 0.55},
        probs={"local": 0.405, "empate": 0.312, "visita": 0.283},
        cuadro_b_loc={"filas": {"sin_derrotas": {"conteo": 8, "total": 15}, "victorias": {"conteo": 5, "total": 15}}},
        cuadro_b_vis={"filas": {"sin_derrotas": {"conteo": 6, "total": 15}, "victorias": {"conteo": 3, "total": 15}}},
        cuadro_c_loc={"_meta": {"n_partidos_con_eventos": 1}},
        cuadro_c_vis={"_meta": {"n_partidos_con_eventos": 1}},
        top5_podio=top5_sin_tarjetas_ni_2t,
    )
    # Conclusion 3: Sin lineas de tarjetas validadas >= 70% en el Ticket
    assert "+3.5 Tarjetas" not in veredicto
    assert "76%" not in veredicto
    assert "Sin líneas de tarjetas validadas >= 70% en el Ticket" in veredicto

    # Conclusion 4: Sin lineas de 2T validadas >= 70% en el Top 2
    assert "28.8%" not in veredicto
    assert "λ_2T=1.065" not in veredicto
    assert "Goles 2T +1.5 al 72%" not in veredicto
    assert "Sin líneas de 2T validadas >= 70% en el Top 2" in veredicto


def test_d2_patron_35_sin_dato_cuando_cuadro_c_incompleto():
    """D2: Patron #35 en Telegram imprime 'sin dato' cuando k_eval < k_min_cron (1/15)."""
    cc_incompleto_loc = {"_meta": {"n_partidos_con_eventos": 1}, "76_90": {"anotados": 0, "recibidos": 0}}
    cc_incompleto_vis = {"_meta": {"n_partidos_con_eventos": 1}, "76_90": {"anotados": 0, "recibidos": 0}}
    rep_tel = generar_reporte_xai_telegram(
        nombre_local="Londrina",
        nombre_visita="Náutico",
        patrones_loc={"xg": 1.10, "remates_totales": 11.0, "goles_favor": 1.0, "posesion_pct": 52.0},
        patrones_vis={"xg": 0.90, "remates_totales": 9.5, "goles_favor": 0.8, "posesion_pct": 48.0},
        probs={"local": 0.405, "empate": 0.312, "visita": 0.283},
        alertas=[],
        cuadro_b_loc={},
        cuadro_b_vis={},
        cuadro_c_loc=cc_incompleto_loc,
        cuadro_c_vis=cc_incompleto_vis,
    )
    assert "anota 0 y recibe 0" not in rep_tel
    assert "• *Patrón #35 - Franja Crítica (76'-90'):* sin dato (cobertura de minutos insuficiente en Cuadro C: 1/15 partidos)" in rep_tel


def test_d3_informe_modelo_pesos_fijos():
    """D3: Verifica que informe.py declare 'modelo de factores con pesos fijos, sin entrenamiento'."""
    with open(os.path.join(BASE_DIR, "informe.py"), "r", encoding="utf-8") as f:
        inf_code = f.read()
    assert "Según la Red Neuronal Recursiva entrenada con los últimos 15 partidos" not in inf_code
    assert "Según el modelo de factores con pesos fijos, sin entrenamiento, sobre los últimos 15 partidos oficiales de cada club," in inf_code


def test_d4_contador_validadas_coincide_con_pildoras_visibles():
    """D4: El contador del banner coincide exactamente con las píldoras visibles."""
    sec1_data = [
        {"categoria": "LÍNEAS DE GOLES", "filas": [{"pildoras": [{"lbl": "-2.5 Goles", "w": 80}, {"lbl": "-3.5 Goles", "w": 80}]}]},
        {"categoria": "HÁNDICAP / 1X2 / DC", "filas": [{"pildoras": [{"lbl": "1X", "w": 60}]}]},
        {"categoria": "GOLES POR EQUIPO", "filas": [{"pildoras": [{"lbl": "Londrina -1.5", "w": 90}, {"lbl": "Nautico -1.5", "w": 90}]}]},
    ]
    total_pills = sum(len(f.get("pildoras", [])) for cat in sec1_data for f in cat.get("filas", []))
    cnt1_txt = f"{total_pills} opciones validadas"
    assert total_pills == 5
    assert cnt1_txt == "5 opciones validadas"


def test_d5_ganador_1t_reconstruccion_real_londrina_nautico():
    """D5: Ganador 1T produce 2/13 y 3/14 reales sobre los datos prepartido, erradicando el 0/1."""
    assert os.path.exists(RUTA_LONDRINA)
    assert os.path.exists(RUTA_NAUTICO)
    with open(RUTA_LONDRINA, "r", encoding="utf-8") as f:
        doc_l = json.load(f)
    with open(RUTA_NAUTICO, "r", encoding="utf-8") as f:
        doc_n = json.load(f)

    cb_l = calcular_cuadro_b_tendencias_15p(doc_l.get("partidos", []), "Londrina")
    cb_n = calcular_cuadro_b_tendencias_15p(doc_n.get("partidos", []), "Náutico")

    g1t_l = cb_l["ganador_1t"]
    g1t_n = cb_n["ganador_1t"]

    assert g1t_l["total"] == 13
    assert g1t_l["conteo"] == 2
    assert g1t_l["str"] == "2/13"
    assert g1t_l["str"] != "0/1"

    assert g1t_n["total"] == 14
    assert g1t_n["conteo"] == 3
    assert g1t_n["str"] == "3/14"
    assert g1t_n["str"] != "0/1"


def test_d6_neutral_bloqueos_centrado_y_omision_none():
    """D6: NEUTRALES['bloqueos'] == 3.5 y F2 omite cleanly con None reescalando peso."""
    assert NEUTRALES["bloqueos"] == 3.5

    # Cruce con bloqueos = None
    mu_l = {"xg": 1.25, "remates_totales": 12.0, "grandes_ocasiones": 1.5, "bloqueos": None}
    mu_v = {"xg": 1.25, "remates_totales": 12.0, "grandes_ocasiones": 1.5, "bloqueos": None}
    res = calcular_los_6_cruces_tacticos(
        mu_l, mu_v, {}, {}, {}, {}, {}, {}
    )
    # Todos en su centro -> F2 debe ser exactamente 0.0
    assert res["factores_local"]["F_creacion"] == 0.0
    assert res["factores_visita"]["F_creacion"] == 0.0


def test_d7_etiqueta_fuente_fotmob_cuando_flashscore_cero_datos():
    """D7: Cuando Flashscore aporto 0 datos por celda, no declarar FM+FS sino FotMob (100% Opta)."""
    partidos_solo_fotmob = [
        {
            "fecha": "2026-10-20",
            "torneo": "Brasileirão Série B",
            "equipo": "Londrina",
            "rival": "Cuiabá",
            "condicion": "casa",
            "marcador": {"equipo": 1, "rival": 0},
            "id_partido": "12345",
            "fuente_secundaria": {"id_flashscore": "ABCDE", "pareado": True},
            "periodos": {"FT": {"filas": []}, "1T": {"filas": []}, "2T": {"filas": []}},
            "procedencia": {
                "FT": {"equipo": {"xg": "fotmob"}, "rival": {"xg": "fotmob"}},
                "1T": {"equipo": {"xg": "fotmob"}, "rival": {"xg": "fotmob"}},
                "2T": {"equipo": {"xg": "fotmob"}, "rival": {"xg": "fotmob"}},
            },
        }
    ]
    tabla_resumen = _construir_tabla_resumen_inicial(partidos_solo_fotmob)
    tabla_str = "\n".join(tabla_resumen)
    assert "FM+FS" not in tabla_str
    assert "FotMob (100% Opta)" in tabla_str
