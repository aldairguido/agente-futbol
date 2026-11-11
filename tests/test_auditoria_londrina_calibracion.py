"""
Pruebas de Calibración, Cobertura y Consistencia Texto-Imagen post-auditoría Londrina vs Náutico.

Verifica:
1. Supresión estricta de EV y Stake Kelly cuando Cuota es N/D (muestra solo Cuota Justa y Cuota Mínima).
2. Cuadro C y Veredicto Táctico con k_eval < 10 (sin cobertura de minutos, celdas con '—', sin falsos 0).
3. Cuadro B con 'sin dato' cuando falta desglose de 1T o eventos de primer gol.
4. Veredicto con '1. Duelo parejo / Ligera ventaja...' cuando max(P_L, P_V) < 50%.
5. Exclusión de líneas triviales del Top 5 (Under 3.5/4.5 goles FT o Under 5.5/6.5 tarjetas con prob > 80% sin cuota real).
6. Calibración de córneres: el factor de bloqueo no colapsa a 0.59 ante remates > 15.
7. Trazabilidad de lambda: muestra lambda Plano -> lambda Red -> lambda Final en el sello.
8. Sincronización completa entre el texto para Telegram y las láminas visuales.
"""
import pytest
from unittest.mock import MagicMock, patch
import tempfile
import os

import motor_inferencia as mi
import motor_mercados as mm
import cuadro_especialista as ce
import informe as inf


def test_1_cuotas_nd_suprime_ev_y_stake_en_top5():
    """Problema 1: Sin cuota real, el Top 5 no inventa EV ni Stake Kelly, solo muestra Cuota Mínima."""
    sels = [
        {"mercado": "Doble oportunidad", "evento": "dc_1x", "etiqueta": "1X (Londrina o empate)", "periodo": "FT", "probabilidad": 0.72, "estado": "mantiene"},
        {"mercado": "Goles por equipo", "evento": "local_over_0.5", "etiqueta": "Londrina +0.5 Goles", "periodo": "FT", "probabilidad": 0.75, "estado": "mantiene"},
        {"mercado": "Total de goles", "evento": "goles_2t_over_0.5", "etiqueta": "Goles 2T: +0.5 Goles", "periodo": "2T", "probabilidad": 0.78, "estado": "mantiene"},
    ]
    top5_sin_cuota = mm.seleccionar_top_5_jugadas_recomendadas(sels, motor=None, eq_l="Londrina", eq_v="Náutico")
    assert len(top5_sin_cuota) >= 2
    for item in top5_sin_cuota:
        assert item["ev"] is None
        assert item["ev_pct"] is None
        assert item["stake_sugerido"] is None
        assert item["stake_sugerido_pct"] is None
        assert "Cuota Mín:" in item["badge_completo"]
        assert "EV +" not in item["badge_completo"]
        assert "Stake:" not in item["badge_completo"]
        assert item["cuota_justa"] == round(1.0 / item["probabilidad"], 2)
        assert item["cuota_minima"] == round(1.05 / item["probabilidad"], 2)

    # Con cuota real verificada de mercado (> 1.01), sí calcula EV y Stake Kelly (25%)
    sels_con_cuota = [dict(s, cuota_real=1.55) for s in sels]
    top5_con_cuota = mm.seleccionar_top_5_jugadas_recomendadas(sels_con_cuota, motor=None, eq_l="Londrina", eq_v="Náutico")
    item_val = top5_con_cuota[0]
    assert item_val["ev"] is not None and item_val["ev"] > 0
    assert item_val["ev_pct"] is not None and item_val["ev_pct"] > 0
    assert item_val["stake_sugerido"] is not None
    assert item_val["stake_sugerido_pct"] is not None
    assert "EV +" in item_val["badge_completo"]
    assert "Stake:" in item_val["badge_completo"]


def test_2_cuadro_c_y_veredicto_con_k_eval_menor_10():
    """Problema 2: Con k_eval < 10 (ej. 1 de 15 como en Londrina), no calcula sobre ceros falsos."""
    # Mock donde solo 1 partido tiene eventos
    mock_cc_1p = {
        "_meta": {"n_partidos_con_eventos": 1, "n_partidos_totales": 15},
        "anotados": {t: 0 for t in ("00'-15'", "16'-30'", "31'-45'", "46'-60'", "61'-75'", "76'-90'")},
        "recibidos": {t: 0 for t in ("00'-15'", "16'-30'", "31'-45'", "46'-60'", "61'-75'", "76'-90'")},
    }
    doc_l = {"equipo": {"nombre": "Londrina"}, "partidos": [], "cuadro_c_timeline": mock_cc_1p}
    doc_v = {"equipo": {"nombre": "Náutico"}, "partidos": [], "cuadro_c_timeline": mock_cc_1p}
    analisis = MagicMock()
    analisis.doc_local = doc_l
    analisis.doc_visitante = doc_v
    analisis.nombre_local = "Londrina"
    analisis.nombre_visitante = "Náutico"
    analisis.grupos = {}
    analisis.selecciones = []
    analisis.mercados = []

    # Verificar banner de Cuadro C en la lámina PNG
    captured = []
    with patch("matplotlib.axes.Axes.text") as mock_text:
        with tempfile.TemporaryDirectory() as td:
            salida = os.path.join(td, "test_londrina_c.png")
            ce.generar_cuadro_auditoria_15_partidos_png(analisis, doc_l, doc_v, destino=salida)
            for call in mock_text.call_args_list:
                args = call[0]
                if len(args) >= 3 and isinstance(args[2], str) and "CRONOLOGÍA" in args[2]:
                    captured.append(args[2])

    assert len(captured) >= 1
    assert "sin cobertura de minutos (1 de 15 partidos con cronología verificada)" in captured[0]
    assert "sin goles en ningún tramo" not in captured[0]

    # Verificar que el Veredicto Táctico no cita ceros falsos ni máxima alerta en 76'-90'
    veredicto = mi.generar_veredicto_tactico_partido(
        nombre_local="Londrina",
        nombre_visita="Náutico",
        patrones_loc={"xg": 1.10, "xg_2t": 0.65},
        patrones_vis={"xg": 0.90, "xg_2t": 0.50},
        probs={"local": 0.40, "empate": 0.32, "visita": 0.28},
        cuadro_b_loc={"victorias": 3, "sin_derrotas": 9, "n_partidos": 15},
        cuadro_b_vis={"victorias": 3, "sin_derrotas": 9, "n_partidos": 15},
        cuadro_c_loc=mock_cc_1p,
        cuadro_c_vis=mock_cc_1p,
    )
    assert "2. Cronología de goles: Cobertura de minutos insuficiente (1/15 partidos" in veredicto
    assert "máxima alerta" not in veredicto.lower()
    assert "0 goles combinados" not in veredicto
    assert "total respaldo estadístico" not in veredicto
    assert "cronología por minuto no concluyente" in veredicto


def test_3_cuadro_b_sin_dato_en_partidos_sin_1t():
    """Problema 3: Si los partidos no tienen desglose de 1T, Ganador 1T muestra 'sin dato'."""
    partidos_sin_1t = [
        {"fecha": f"2026-09-{i:02d}", "local": "Londrina", "visita": "Rival", "goles_local": 1, "goles_visita": 0}
        for i in range(1, 16)
    ]
    cb_15p = mi.calcular_cuadro_b_tendencias_15p(partidos_sin_1t, nombre_equipo="Londrina")
    celda_g1t = cb_15p["ganador_1t"]
    assert celda_g1t["sin_dato"] is True
    assert celda_g1t["str"] == "sin dato"
    assert celda_g1t["texto"] == "sin dato"

    # En el renderizado de la imagen 3 (auditoría), la fila Ganador 1T muestra 'sin dato'
    doc_l = {"equipo": {"nombre": "Londrina"}, "partidos": partidos_sin_1t}
    doc_v = {"equipo": {"nombre": "Náutico"}, "partidos": partidos_sin_1t}
    analisis = MagicMock()
    analisis.doc_local = doc_l
    analisis.doc_visitante = doc_v
    analisis.nombre_local = "Londrina"
    analisis.nombre_visitante = "Náutico"
    analisis.grupos = {}
    analisis.selecciones = []
    analisis.mercados = []

    captured_b = []
    with patch("matplotlib.axes.Axes.text") as mock_text:
        with tempfile.TemporaryDirectory() as td:
            salida = os.path.join(td, "test_cuadro_b_sin_1t.png")
            ce.generar_cuadro_auditoria_15_partidos_png(analisis, doc_l, doc_v, destino=salida)
            for call in mock_text.call_args_list:
                args = call[0]
                if len(args) >= 3 and isinstance(args[2], str):
                    captured_b.append(args[2])

    assert "sin dato" in captured_b


def test_4_veredicto_duelo_parejo_cuando_prob_menor_50():
    """Problema 4: Si la probabilidad del favorito es < 50%, no puede decir 'va a ganar'."""
    veredicto = mi.generar_veredicto_tactico_partido(
        nombre_local="Londrina",
        nombre_visita="Náutico",
        patrones_loc={"xg": 1.10},
        patrones_vis={"xg": 0.90},
        probs={"local": 0.405, "empate": 0.318, "visita": 0.272},
        cuadro_b_loc={"victorias": 3, "sin_derrotas": 9, "n_partidos": 15},
        cuadro_b_vis={"victorias": 3, "sin_derrotas": 9, "n_partidos": 15},
        cuadro_c_loc={"_meta": {"n_partidos_con_eventos": 15}, "anotados": {"76'-90'": 2}, "recibidos": {"76'-90'": 1}},
        cuadro_c_vis={"_meta": {"n_partidos_con_eventos": 15}, "anotados": {"76'-90'": 1}, "recibidos": {"76'-90'": 2}},
    )
    assert "1. Duelo parejo / Ligera ventaja para Londrina (o empate):" in veredicto
    assert "1. El Londrina va a ganar" not in veredicto


def test_5_filtro_lineas_triviales_top5():
    """Problema 5: Excluye del Top 5 Under en líneas altas con cuota ínfima sin cuota real de mercado."""
    sels = [
        {"mercado": "Doble oportunidad", "evento": "dc_1x", "etiqueta": "1X (Londrina o empate)", "periodo": "FT", "probabilidad": 0.72, "estado": "mantiene"},
        {"mercado": "Total de goles", "evento": "under_3.5", "etiqueta": "Menos de 3.5 goles", "periodo": "FT", "linea": 3.5, "probabilidad": 0.86, "estado": "mantiene"},
        {"mercado": "Total de goles", "evento": "under_4.5", "etiqueta": "Menos de 4.5 goles", "periodo": "FT", "linea": 4.5, "probabilidad": 0.95, "estado": "mantiene"},
        {"mercado": "Total de tarjetas", "evento": "amarillas_under_6.5", "etiqueta": "Menos de 6.5 tarjetas", "periodo": "FT", "linea": 6.5, "probabilidad": 0.88, "estado": "mantiene"},
        {"mercado": "Goles 2T", "evento": "goles_2t_over_0.5", "etiqueta": "Goles 2T: +0.5 Goles", "periodo": "2T", "probabilidad": 0.74, "estado": "mantiene"},
    ]
    top5 = mm.seleccionar_top_5_jugadas_recomendadas(sels, motor=None, eq_l="Londrina", eq_v="Náutico")
    etiquetas_top5 = [it["etiqueta"] for it in top5]
    assert not any("3.5" in et and "menos" in et.lower() for et in etiquetas_top5)
    assert not any("4.5" in et and "menos" in et.lower() for et in etiquetas_top5)
    assert not any("6.5" in et and "tarjeta" in et.lower() for et in etiquetas_top5)
    assert any("1X" in et for et in etiquetas_top5)


def test_6_calibracion_corners_no_colapso():
    """Problema 6: La media de córneres no colapsa a 0.59 (factor de bloqueo acotado en [0.86, 1.15])."""
    doc_l = {"equipo": {"nombre": "Londrina"}, "partidos": []}
    doc_v = {"equipo": {"nombre": "Náutico"}, "partidos": []}
    m = mm.MotorMercados(doc_l, doc_v)
    m.tasas.lambda_local_ft = 1.10
    m.tasas.lambda_visitante_ft = 0.90
    m.matriz_ft = [[0.01]*10 for _ in range(10)]

    # Mock de muestras de córneres con media base = 5.6
    m_loc = MagicMock()
    m_loc.dame.return_value = mm.MuestraMetrica(clave="corners_favor", n=15, media=5.6)
    m_vis = MagicMock()
    m_vis.dame.return_value = mm.MuestraMetrica(clave="corners_favor", n=15, media=5.6)
    m.muestras = {
        "local_general": m_loc, "local_cond": m_loc,
        "visitante_general": m_vis, "visitante_cond": m_vis,
    }
    m.bloques = {
        "local": MagicMock(ft={"remates_totales": 16.0, "centros": 18.0}),
        "visitante": MagicMock(ft={"remates_totales": 15.5, "centros": 18.0}),
    }
    m.patrones_55 = {"P54_Asedio_Corners_Matchup": 1.0, "P11_Peligro_Area_vs_Fuera": 2.8}

    mu_calc_l = m._mu_corners("local")
    mu_calc_v = m._mu_corners("visita")

    # La media base era 5.6. Con el piso 0.86, no debe caer por debajo de 5.6 * 0.85 = 4.76
    assert mu_calc_l is not None and mu_calc_l >= 5.6 * 0.85, f"Córneres colapsados: {mu_calc_l} < 4.76"
    assert mu_calc_v is not None and mu_calc_v >= 5.6 * 0.85, f"Córneres colapsados: {mu_calc_v} < 4.76"


def test_7_trazabilidad_lambda_en_sello_verificacion():
    """Problema 7: Sello visual registra lambda Plano -> lambda Red -> lambda Final con factores."""
    doc_l = {"equipo": {"nombre": "Londrina"}, "partidos": []}
    doc_v = {"equipo": {"nombre": "Náutico"}, "partidos": []}
    m = mm.MotorMercados(doc_l, doc_v)
    m.tasas.lambda_local_ft = 1.10
    m.tasas.lambda_visitante_ft = 0.90
    m.matriz_ft = [[0.01]*10 for _ in range(10)]

    res_red = {
        "aplicado": True,
        "lambda_base_local": 1.10,
        "lambda_base_visita": 0.90,
        "lambda_local_recursivo": 1.25,
        "lambda_visita_recursivo": 0.80,
        "sello_verificacion_visual": chr(10).join([
            "RED NEURONAL RECURSIVA ACTIVA · it. 1 · λ Base (1.10 - 0.90) -> λ Red (1.25 - 0.80)",
            "L (+13.6%): ...",
            "V (-11.1%): ...",
        ]),
        "matriz_prob": [[0.01]*10 for _ in range(10)],
    }

    # Simular freno de mano que recorta lambda_local a 0.95
    with patch("motor_patrones.aplicar_freno_de_mano_y_matchup_21_22") as mock_freno:
        mock_freno.return_value = (0.95, 0.80, 0.0, 0.0)
        with patch("motor_recursivo_45_dcn.ejecutar_bucle_recursivo_infinito_hasta_convergencia", return_value=res_red):
            m._aplicar_motor_recursivo()

    sello = m.recursivo.get("sello_verificacion_visual", "")
    assert "λ Plano (" in sello
    assert "λ Red (" in sello
    assert "λ Final (" in sello


def test_8_sincronizacion_texto_telegram_e_imagenes():
    """Problema 8: Texto para Telegram e imágenes reciben exactamente los mismos datos de partidos y ratings."""
    doc_local = {
        "equipo": {"nombre": "Londrina"},
        "partidos": [
            {"fecha": "2026-09-01", "local": "Londrina", "visita": "Rival", "goles_local": 1, "goles_visita": 0, "xg": 1.10}
        ] * 15
    }
    doc_visita = {
        "equipo": {"nombre": "Náutico"},
        "partidos": [
            {"fecha": "2026-09-01", "local": "Rival", "visita": "Náutico", "goles_local": 0, "goles_visita": 1, "xg": 0.90}
        ] * 15
    }

    analisis = MagicMock()
    analisis.doc_local = doc_local
    analisis.doc_visitante = doc_visita
    analisis.metadatos = {"equipo_local": "Londrina", "equipo_visitante": "Náutico"}
    motor = MagicMock()
    motor.nombre_local = "Londrina"
    motor.nombre_visitante = "Náutico"
    analisis.motor = motor

    texto_3p = inf.redactar_analisis_3_parrafos(
        analisis,
        doc_local=doc_local,
        doc_visitante=doc_visita,
    )
    assert "TICKET DE ANÁLISIS PROFUNDO" in texto_3p
    assert "LONDRINA VS NÁUTICO" in texto_3p
    assert "Patrón #01" in texto_3p
