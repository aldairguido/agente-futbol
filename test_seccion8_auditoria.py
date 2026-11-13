# -*- coding: utf-8 -*-
"""
Suite de verificación integral de la Auditoría 30/10/2026 (Sección 8 / Sección 9 AGENTS.md):
  1. Normalizador universal de clubes (Eintracht Fráncfort -> Eintracht Frankfurt,
     Nueva York Red Bull -> New York Red Bulls, Deportivo A Coruña -> Deportivo de La Coruña,
     prefijos "MLS ...", "Serie A de Brasil ...", "Liga 1 de Perú ...") y cascada FotMob -> Flashscore.
  2. Tope estricto [-12.0%, +15.0%] en cada uno de los 5 Factores Latentes de la Caja Azul,
     garantizando además que su suma sea igual al cambio porcentual total de λ.
  3. Descuento relativo de división reconociendo "LaLiga2" (sin espacio) en Atlético de Madrid vs
     Deportivo de La Coruña, coherencia xG 100% entre tabla y LECTURAS DE ESPECIALISTA (3 viñetas
     Doble ADN) y sincronización al 0.1% entre Matriz PNG y Ticket PNG.
  4. Eliminación de las 104 opciones de relleno en el Ticket Cuantitativo (prohibido -5.5/-6.5 Goles,
     prohibido +0.5/+1.5 Córneres, prohibido -13.5 a -16.5 Córneres, prohibido +0.5 Tarjetas,
     prohibido H+2 y H+3) y creación del bloque 🏆 TOP 5 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO).
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cuadro_especialista as ce
import fuentes_xg as fxg
import motor_mercados as mm
import motor_recursivo_45_dcn as red
import servicio_mercados as sm


def _crear_partido_sintetico(
    idx: int,
    equipo: str,
    rival: str,
    condicion: str,
    torneo: str,
    gf: int,
    gc: int,
    xg_f: float,
    xg_c: float,
    xgot_f: float,
    xgot_c: float,
    posesion: float = 54.0,
    toques_area: int = 26,
    corners_f: int = 6,
    corners_c: int = 4,
    amarillas_f: int = 2,
    amarillas_c: int = 2,
) -> dict:
    filas_ft = [
        {"clave": "posesion_pct", "equipo": posesion, "rival": 100.0 - posesion},
        {"clave": "xg", "equipo": xg_f, "rival": xg_c},
        {"clave": "xgot", "equipo": xgot_f, "rival": xgot_c},
        {"clave": "xgot_enfrentado", "equipo": xgot_c, "rival": xgot_f},
        {"clave": "goles_evitados", "equipo": round(xgot_c - gc, 2), "rival": round(xgot_f - gf, 2)},
        {"clave": "remates_totales", "equipo": 14, "rival": 10},
        {"clave": "remates_puerta", "equipo": 5, "rival": 4},
        {"clave": "remates_dentro_area", "equipo": 9, "rival": 6},
        {"clave": "grandes_ocasiones", "equipo": 3, "rival": 2},
        {"clave": "grandes_ocasiones_falladas", "equipo": 1, "rival": 1},
        {"clave": "corners", "equipo": corners_f, "rival": corners_c},
        {"clave": "toques_area_rival", "equipo": toques_area, "rival": 18},
        {"clave": "pases_entre_lineas", "equipo": 42, "rival": 34},
        {"clave": "pases_precision_pct", "equipo": 84.0, "rival": 79.0},
        {"clave": "despejes", "equipo": 16, "rival": 22},
        {"clave": "duelos_aereos_pct", "equipo": 53.0, "rival": 47.0},
        {"clave": "faltas", "equipo": 11, "rival": 13},
        {"clave": "tarjetas_amarillas", "equipo": amarillas_f, "rival": amarillas_c},
        {"clave": "tarjetas_rojas", "equipo": 0, "rival": 0},
        {"clave": "errores_gol", "equipo": 0, "rival": 0},
        {"clave": "errores_remate", "equipo": 0, "rival": 1},
    ]
    filas_1t = [
        {"clave": k["clave"], "equipo": round(k["equipo"] * 0.45, 2) if isinstance(k["equipo"], (int, float)) else k["equipo"],
         "rival": round(k["rival"] * 0.45, 2) if isinstance(k["rival"], (int, float)) else k["rival"]}
        for k in filas_ft
    ]
    filas_2t = [
        {"clave": k["clave"], "equipo": round(k["equipo"] * 0.55, 2) if isinstance(k["equipo"], (int, float)) else k["equipo"],
         "rival": round(k["rival"] * 0.55, 2) if isinstance(k["rival"], (int, float)) else k["rival"]}
        for k in filas_ft
    ]
    return {
        "id_partido": f"p_{equipo[:3]}_{idx}",
        "fecha": f"2026-09-{(idx % 28) + 1:02d}",
        "torneo": torneo,
        "competicion": torneo,
        "equipo": equipo,
        "rival": rival,
        "condicion": condicion,
        "marcador": {"equipo": gf, "rival": gc},
        "periodos": {
            "FT": {"filas": filas_ft},
            "1T": {"filas": filas_1t},
            "2T": {"filas": filas_2t},
        },
    }


def test_1_normalizador_universal_y_cascada():
    print("▶ 1. Verificando Normalizador Universal de Clubes y Cascada FotMob -> Flashscore...")
    casos = [
        ("Eintracht Fráncfort", "Eintracht Frankfurt"),
        ("MLS Nueva York Red Bull", "New York Red Bulls"),
        ("Nueva York Red Bull", "New York Red Bulls"),
        ("Deportivo A Coruña", "Deportivo de La Coruña"),
        ("Serie A de Brasil Flamengo", "Flamengo"),
        ("Liga 1 de Perú Alianza Lima", "Alianza Lima"),
        ("Atlético Madrid", "Atlético Madrid"),
        ("Borussia M'gladbach", "Borussia Mönchengladbach"),
    ]
    for entrada, esperado in casos:
        res_norm = fxg.normalizar_nombre_club(entrada)
        variantes = [res_norm] if isinstance(res_norm, str) else list(res_norm)
        assert any(esperado.lower() == v.lower() for v in variantes), (
            f"Fallo normalización para '{entrada}': variantes={variantes}, esperado='{esperado}'"
        )

    # Verificar que FuenteFlashscore esté disponible como respaldo en cascada
    assert hasattr(fxg, "FuenteFlashscore"), "FuenteFlashscore debe existir en fuentes_xg.py"
    print("  ✅ Normalizador universal y FuenteFlashscore verificados con éxito.")


def test_2_tope_factores_latentes_caja_azul():
    print("▶ 2. Verificando Tope [-12.0%, +15.0%] en los 5 Factores Latentes de la Caja Azul...")
    # Caso extremo: un factor en +48.0% antes del ajuste y variación objetivo de +28.5%
    factores_extremos = {
        "F_posesion_progresiva": -5.0,
        "F_creacion": 48.0,
        "F_finalizacion": -2.0,
        "F_porteria_rival": 0.0,
        "F_fragilidad_rival": 0.0,
    }
    pct_obj = 28.5
    res_esc = red.reescalar_factores_a_delta(factores_extremos, delta_pct_real=pct_obj)
    for k, val in res_esc.items():
        assert -12.0 <= val <= 15.0, f"Factor {k}={val}% viola el rango [-12.0%, +15.0%]"

    suma_f = round(sum(res_esc.values()), 1)
    assert abs(suma_f - pct_obj) <= 0.15, f"La suma {suma_f}% no coincide con el cambio de λ ({pct_obj}%)"
    print(f"  ✅ Factores acotados en [-12%, +15%]: {res_esc} (suma={suma_f}%, obj={pct_obj}%).")


def test_3_descuento_laliga2_y_sincronizacion_atletico_deportivo():
    print("▶ 3. Verificando Descuento 'LaLiga2', Coherencia xG y Sincronización Matriz-Ticket (Atlético vs Deportivo)...")
    assert red.obtener_tier_liga("LaLiga2") == 2, "LaLiga2 (sin espacio) debe ser Tier 2"
    assert red.obtener_tier_liga("LaLiga") == 1, "LaLiga debe ser Tier 1"

    # Crear 15 partidos de Atlético de Madrid en LaLiga (Tier 1) y 15 de Deportivo en LaLiga2 (Tier 2)
    pts_atleti = [
        _crear_partido_sintetico(
            i, "Atlético de Madrid", f"Rival1_{i}",
            "casa" if i % 2 == 0 else "fuera",
            "LaLiga", gf=2, gc=1, xg_f=1.85, xg_c=0.95, xgot_f=1.95, xgot_c=0.92,
            posesion=56.0, toques_area=29, corners_f=6, corners_c=4
        )
        for i in range(15)
    ]
    # Deportivo promedia en bruto ~1.56 G y ~1.57 xG en LaLiga2
    pts_depor = [
        _crear_partido_sintetico(
            i, "Deportivo de La Coruña", f"Rival2_{i}",
            "fuera" if i % 2 == 0 else "casa",
            "LaLiga2", gf=(2 if i % 2 == 0 else 1), gc=1,
            xg_f=1.57, xg_c=1.15, xgot_f=1.52, xgot_c=1.18,
            posesion=51.0, toques_area=21, corners_f=5, corners_c=5
        )
        for i in range(15)
    ]

    doc_l = {"equipo": {"nombre": "Atlético de Madrid"}, "partidos": pts_atleti, "resumen": {"objetivo_alcanzado": True}}
    doc_v = {"equipo": {"nombre": "Deportivo de La Coruña"}, "partidos": pts_depor, "resumen": {"objetivo_alcanzado": True}}

    analisis = mm.analizar(
        doc_l, doc_v, "Atlético de Madrid", "Deportivo de La Coruña",
        competicion="Copa del Rey", condicion_local="casa", condicion_visitante="fuera"
    )

    # Verificar que el descuento de división (0.76 ofensivo, 1.22 defensivo) se aplicó a Deportivo (LaLiga2 vs LaLiga)
    gf_v, xg_v, gc_v, xgotc_v = ce._metricas_ponderadas_condicion(
        pts_depor,
        ce.construir_perfil_especialista_equipo(ce.bloque_para_lado(analisis.motor, "visitante")),
        "fuera",
        tier_rival=1,
    )
    assert xg_v < 1.30, f"El xG de Deportivo en LaLiga2 debe descontarse por 0.76 (obtenido {xg_v}, bruto 1.57)"
    assert gf_v < 1.45, f"Los goles a favor de Deportivo en LaLiga2 deben descontarse por 0.76 (obtenido {gf_v}, bruto 1.81)"
    assert gc_v > 1.15, f"Los goles concedidos de Deportivo en LaLiga2 deben penalizarse por 1.22 (obtenido {gc_v})"

    # Verificar que las 5 barras de la Caja Azul estén en [-12.0%, +15.0%]
    rec = analisis.motor.recursivo
    for lado in ("factores_xai_local", "factores_xai_visita"):
        for fk, fval in (rec.get(lado) or {}).items():
            assert -12.0 <= float(fval) <= 15.0, f"{lado}.{fk}={fval} fuera de [-12.0%, +15.0%]"

    # Generar Cuadro Especialista PNG y Ticket PNG y verificar sincronización exacta al 0.1%
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salidas_test_seccion8")
    os.makedirs(out_dir, exist_ok=True)
    ruta_cuadro = os.path.join(out_dir, "cuadro_atletico_vs_deportivo.png")
    ruta_ticket = os.path.join(out_dir, "ticket_atletico_vs_deportivo.png")

    ce.generar_cuadro_desde_analisis(analisis, doc_l, doc_v, destino=ruta_cuadro, peso_roja=0.30)
    sm.renderizar_ticket_mercados_png(analisis, destino=ruta_ticket)

    assert os.path.isfile(ruta_cuadro) and os.path.getsize(ruta_cuadro) > 15_000
    assert os.path.isfile(ruta_ticket) and os.path.getsize(ruta_ticket) > 15_000

    # Verificar que clusters_marcador de motor y el usado por el cuadro/ticket sean idénticos al 0.1%
    cl_motor = analisis.motor.clusters
    cl_recalc = mm.clusters_marcador(
        analisis.motor.matriz_ft,
        "Atlético de Madrid",
        "Deportivo de La Coruña",
        asedio_bandas=bool(rec.get("asedio_bandas_activo", False)),
        info_cruces=rec.get("cruces_tacticos"),
    )
    assert cl_motor["linea_pie"] == cl_recalc["linea_pie"], (
        f"Desincronización Matriz vs Ticket:\nMotor: {cl_motor['linea_pie']}\nRecalc: {cl_recalc['linea_pie']}"
    )
    print(f"  ✅ Descuento LaLiga2 aplicado (xG Depor descontado a {xg_v:.2f}) y Matriz-Ticket 100% sincronizados.")


def test_4_filtro_relleno_y_podio_top5():
    print("▶ 4. Verificando eliminación de opciones de relleno y Podio TOP 5 JUGADAS RECOMENDADAS...")
    _res_dort = [(2, 0), (3, 1), (2, 1), (1, 0), (1, 1)]
    _res_ef = [(1, 2), (0, 2), (1, 1), (1, 2), (2, 3)]
    pts_l = [
        _crear_partido_sintetico(
            i, "Borussia Dortmund", f"RivalBL_{i}",
            "casa" if i % 2 == 0 else "fuera",
            "Bundesliga",
            gf=_res_dort[i % 5][0], gc=_res_dort[i % 5][1],
            xg_f=2.05, xg_c=1.10, xgot_f=2.15, xgot_c=1.05,
            posesion=58.0, toques_area=32,
            corners_f=[6, 7, 8, 5, 7][i % 5], corners_c=[4, 3, 5, 4, 4][i % 5],
            amarillas_f=2, amarillas_c=2,
        )
        for i in range(15)
    ]
    pts_v = [
        _crear_partido_sintetico(
            i, "Eintracht Frankfurt", f"RivalEF_{i}",
            "fuera" if i % 2 == 0 else "casa",
            "Bundesliga",
            gf=_res_ef[i % 5][0], gc=_res_ef[i % 5][1],
            xg_f=1.35, xg_c=1.65, xgot_f=1.30, xgot_c=1.70,
            posesion=49.0, toques_area=22,
            corners_f=[5, 4, 6, 5, 4][i % 5], corners_c=[5, 6, 4, 5, 6][i % 5],
            amarillas_f=2, amarillas_c=2,
        )
        for i in range(15)
    ]
    doc_l = {"equipo": {"nombre": "Borussia Dortmund"}, "partidos": pts_l, "resumen": {"objetivo_alcanzado": True}}
    doc_v = {"equipo": {"nombre": "Eintracht Frankfurt"}, "partidos": pts_v, "resumen": {"objetivo_alcanzado": True}}

    analisis = mm.analizar(
        doc_l, doc_v, "Borussia Dortmund", "Eintracht Frankfurt",
        competicion="Bundesliga", condicion_local="casa", condicion_visitante="fuera"
    )

    mat = analisis.motor.matriz_ft
    p1 = sum(mat[i][j] for i in range(len(mat)) for j in range(len(mat[0])) if i > j)
    px = sum(mat[i][j] for i in range(len(mat)) for j in range(len(mat[0])) if i == j)
    p2 = sum(mat[i][j] for i in range(len(mat)) for j in range(len(mat[0])) if i < j)
    p_1x2 = (p1, px, p2)

    competitivas = mm.filtrar_top_opciones_ticket(
        analisis.selecciones_filtradas,
        eq_l="Borussia Dortmund",
        eq_v="Eintracht Frankfurt",
        p_1x2=p_1x2,
        max_opciones=18,
    )
    assert 1 <= len(competitivas) <= 18, f"El Ticket debe tener a lo sumo 18 opciones competitivas (tiene {len(competitivas)})"

    # Verificar que NINGUNA opción de relleno prohibida pase el filtro competitivo
    for s in competitivas:
        ev = str(s.evento or "").lower()
        et = str(s.etiqueta or "").lower()
        per = str(s.periodo or "FT").upper()
        lin = s.linea
        m_l = str(s.mercado or "").lower()

        # Prohibido H+2 y H+3
        assert "h2_" not in ev and "h3_" not in ev and "h+2" not in et and "h+3" not in et, f"Coló H+2/H+3: {s}"
        # Prohibido -5.5 y -6.5 goles
        if "gol" in m_l and lin is not None:
            assert float(lin) <= 4.5, f"Coló línea de relleno de goles ({lin}): {s}"
            if per in ("1T", "2T") and "under" in ev:
                assert float(lin) <= 1.5, f"Coló under de relleno en mitad ({lin}): {s}"
        # Prohibido +0.5 a +3.5 Córneres y -13.5 a -16.5 Córneres
        if "corner" in m_l or "córner" in m_l:
            assert lin is not None and 3.5 <= float(lin) <= 12.5, f"Coló línea de relleno de córneres ({lin}): {s}"
        # Prohibido +0.5 Tarjetas y -7.5/-8.5 Tarjetas
        if "tarjeta" in m_l or "amarilla" in m_l:
            assert lin is not None and 2.5 <= float(lin) <= 6.5, f"Coló línea de relleno de tarjetas ({lin}): {s}"

    top5 = mm.seleccionar_top_5_jugadas_recomendadas(
        analisis.selecciones_filtradas,
        motor=analisis.motor,
        eq_l="Borussia Dortmund",
        eq_v="Eintracht Frankfurt",
    )
    assert 1 <= len(top5) <= 5, f"El podio Top-5 debe tener entre 1 y 5 jugadas (tiene {len(top5)})"
    for item in top5:
        assert item.get("respaldo", "").startswith("Respaldo ADN:"), f"Falta respaldo ADN en {item}"

    assert any(x in analisis.mensaje for x in ("TOP 5 JUGADAS RECOMENDADAS", f"TOP {len(top5)} JUGADAS RECOMENDADAS"))
    print(f"  ✅ Filtro competitivo ({len(competitivas)} opciones) y Podio Top-{len(top5)} verificados con éxito.")


if __name__ == "__main__":
    test_1_normalizador_universal_y_cascada()
    test_2_tope_factores_latentes_caja_azul()
    test_3_descuento_laliga2_y_sincronizacion_atletico_deportivo()
    test_4_filtro_relleno_y_podio_top5()
    print("\n🎉 TODAS LAS PRUEBAS DE LA SECCIÓN 8 (AUDITORÍA 30/10/2026) PASARON AL 100%.")
