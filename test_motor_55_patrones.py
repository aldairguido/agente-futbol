from __future__ import annotations
import math
import os
import sys
from typing import Any, Dict

from motor_patrones import (
    calcular_55_patrones_equipo,
    calcular_55_patrones_matchup,
    calcular_patrones_completos_partido,
)
from motor_inferencia import (
    aplicar_reglas_matchup_55,
    calcular_cuadro_b_tendencias_15p,
    calcular_cuadro_c_timeline_15min,
    generar_informe_narrativo_xai,
)
import motor_mercados as mm
from servicio_mercados import obtener_top5_y_seccion1_filtrados, renderizar_ticket_mercados_png
from cuadro_especialista import (
    _organizar_sec1_para_cuadro,
    generar_cuadro_desde_analisis,
    generar_cuadro_auditoria_15_partidos_png,
)


# ═══════════════════════════════════════════════════════════════════════════════
# DATOS REALES DE AUDITORÍA: MILAN VS FERENCVÁROS (CUADRO A, CUADRO B Y CUADRO C)
# ═══════════════════════════════════════════════════════════════════════════════

CUADRO_A_MILAN_GEN: Dict[str, float] = {
    "goles_favor": 1.67, "goles_contra": 0.73, "xg": 1.85, "xgot": 1.72,
    "xg_contra": 1.05, "xgot_contra": 1.04, "posesion_pct": 55.2,
    "remates_totales": 15.6, "remates_puerta": 5.8, "remates_fuera": 5.9,
    "remates_bloqueados": 3.9, "remates_dentro_area": 10.8, "remates_fuera_area": 4.8,
    "grandes_ocasiones": 3.1, "grandes_ocasiones_falladas": 1.7, "remates_palo": 0.4,
    "toques_area_rival": 28.4, "corners_favor": 5.9, "corners_contra": 3.8,
    "centros_totales": 18.5, "centros_completados": 4.8, "centros_precision_pct": 25.9,
    "pases_totales": 485.0, "pases_completados": 428.0, "pases_precision_pct": 88.2,
    "pases_tercio_final": 118.0, "pases_tercio_final_completados": 92.0, "pases_tercio_final_pct": 78.0,
    "pases_largos_totales": 38.0, "pases_largos_completados": 22.0, "pases_largos_pct": 57.9,
    "pases_clave": 12.1, "asistencias": 1.25, "xa": 1.42,
    "regates_totales": 16.5, "regates_completados": 8.9, "regates_exito_pct": 53.9,
    "duelos_totales": 96.0, "duelos_ganados": 51.5, "duelos_ganados_pct": 53.6,
    "duelos_suelo_ganados": 38.0, "duelos_aereos_totales": 25.0, "duelos_aereos_ganados": 13.5, "duelos_aereos_pct": 54.0,
    "entradas_totales": 16.8, "entradas_ganadas": 10.6, "intercepciones": 8.4,
    "despejes": 15.2, "recuperaciones": 49.5, "errores_tiro": 0.27, "errores_gol": 0.07,
    "paradas_portero": 2.9, "goles_evitados": 0.31, "saques_puerta": 6.4,
    "faltas_cometidas": 11.4, "faltas_recibidas": 12.8, "tarjetas_amarillas": 1.87, "tarjetas_rojas": 0.07,
    "fueras_juego": 1.8, "saques_banda": 17.5, "tiros_libres": 12.2,
}

CUADRO_A_MILAN_CASA: Dict[str, float] = {
    **CUADRO_A_MILAN_GEN,
    "goles_favor": 1.62, "goles_contra": 0.78, "xg": 1.89, "xgot": 1.78,
    "xgot_contra": 1.04, "posesion_pct": 56.8, "toques_area_rival": 30.2, "corners_favor": 6.2,
}

CUADRO_A_FERENC_GEN: Dict[str, float] = {
    "goles_favor": 2.33, "goles_contra": 0.73, "xg": 1.98, "xgot": 2.05,
    "xg_contra": 0.92, "xgot_contra": 0.85, "posesion_pct": 58.4,
    "remates_totales": 15.2, "remates_puerta": 6.3, "remates_fuera": 5.4,
    "remates_bloqueados": 3.5, "remates_dentro_area": 10.2, "remates_fuera_area": 5.0,
    "grandes_ocasiones": 3.3, "grandes_ocasiones_falladas": 1.5, "remates_palo": 0.3,
    "toques_area_rival": 26.8, "corners_favor": 5.8, "corners_contra": 3.9,
    "centros_totales": 19.2, "centros_completados": 5.1, "centros_precision_pct": 26.6,
    "pases_totales": 468.0, "pases_completados": 401.0, "pases_precision_pct": 85.7,
    "pases_tercio_final": 112.0, "pases_tercio_final_completados": 84.0, "pases_tercio_final_pct": 75.0,
    "pases_largos_totales": 42.0, "pases_largos_completados": 24.0, "pases_largos_pct": 57.1,
    "pases_clave": 11.6, "asistencias": 1.60, "xa": 1.50,
    "regates_totales": 15.8, "regates_completados": 8.4, "regates_exito_pct": 53.2,
    "duelos_totales": 98.0, "duelos_ganados": 52.8, "duelos_ganados_pct": 53.9,
    "duelos_suelo_ganados": 37.5, "duelos_aereos_totales": 28.0, "duelos_aereos_ganados": 15.3, "duelos_aereos_pct": 54.6,
    "entradas_totales": 16.2, "entradas_ganadas": 10.1, "intercepciones": 8.1,
    "despejes": 16.8, "recuperaciones": 51.2, "errores_tiro": 0.33, "errores_gol": 0.13,
    "paradas_portero": 2.6, "goles_evitados": 0.12, "saques_puerta": 6.8,
    "faltas_cometidas": 12.1, "faltas_recibidas": 11.9, "tarjetas_amarillas": 1.93, "tarjetas_rojas": 0.07,
    "fueras_juego": 2.0, "saques_banda": 18.4, "tiros_libres": 11.8,
}

CUADRO_A_FERENC_VISITA: Dict[str, float] = {
    **CUADRO_A_FERENC_GEN,
    "goles_favor": 1.38, "goles_contra": 1.15, "xg": 1.34, "xgot": 1.38,
    "xgot_contra": 1.19, "posesion_pct": 51.2, "toques_area_rival": 21.4, "corners_favor": 4.9,
}

CUADRO_B_MILAN = {
    "victorias": {"cumple": 9, "total": 15, "pct": 60.0, "str": "9/15"},
    "sin_derrotas": {"cumple": 13, "total": 15, "pct": 86.7, "str": "13/15"},
    "mas_25_goles": {"cumple": 7, "total": 15, "pct": 46.7, "str": "7/15"},
    "btts_si": {"cumple": 7, "total": 15, "pct": 46.7, "str": "7/15"},
    "primero_marcar": {"cumple": 10, "total": 15, "pct": 66.7, "str": "10/15"},
    "ganador_1t": {"cumple": 6, "total": 15, "pct": 40.0, "str": "6/15"},
    "menos_45_tarjetas": {"cumple": 11, "total": 15, "pct": 73.3, "str": "11/15"},
    "mas_105_corners": {"cumple": 6, "total": 15, "pct": 40.0, "str": "6/15"},
}

CUADRO_B_FERENC = {
    "victorias": {"cumple": 12, "total": 15, "pct": 80.0, "str": "12/15"},
    "sin_derrotas": {"cumple": 12, "total": 15, "pct": 80.0, "str": "12/15"},
    "mas_25_goles": {"cumple": 9, "total": 15, "pct": 60.0, "str": "9/15"},
    "btts_si": {"cumple": 7, "total": 15, "pct": 46.7, "str": "7/15"},
    "primero_marcar": {"cumple": 11, "total": 15, "pct": 73.3, "str": "11/15"},
    "ganador_1t": {"cumple": 8, "total": 15, "pct": 53.3, "str": "8/15"},
    "menos_45_tarjetas": {"cumple": 10, "total": 15, "pct": 66.7, "str": "10/15"},
    "mas_105_corners": {"cumple": 7, "total": 15, "pct": 46.7, "str": "7/15"},
}

CUADRO_C_MILAN = {
    "anotados": {"00'-15'": 3, "16'-30'": 4, "31'-45'": 4, "46'-60'": 4, "61'-75'": 3, "76'-90'": 7},
    "recibidos": {"00'-15'": 1, "16'-30'": 2, "31'-45'": 2, "46'-60'": 2, "61'-75'": 1, "76'-90'": 3},
}

CUADRO_C_FERENC = {
    "anotados": {"00'-15'": 4, "16'-30'": 5, "31'-45'": 6, "46'-60'": 6, "61'-75'": 5, "76'-90'": 9},
    "recibidos": {"00'-15'": 1, "16'-30'": 2, "31'-45'": 2, "46'-60'": 2, "61'-75'": 1, "76'-90'": 3},
}


def _crear_partido_sintetico(
    idx: int, equipo: str, rival: str, torneo: str, condicion: str,
    gf: int, gc: int, xg_f: float, xgot_f: float, xgot_c: float,
    posesion: float, tiros: int, sot: int, corners: int, amarillas: int
) -> dict:
    gf_1t = gf // 2
    gf_2t = gf - gf_1t
    gc_1t = gc // 2
    gc_2t = gc - gc_1t
    filas_ft = [
        {"clave": "posesion", "equipo": posesion, "rival": 100.0 - posesion},
        {"clave": "xg", "equipo": xg_f, "rival": xgot_c},
        {"clave": "xgot", "equipo": xgot_f, "rival": xgot_c},
        {"clave": "xgot_enfrentado", "equipo": xgot_c, "rival": xgot_f},
        {"clave": "goles_evitados", "equipo": round(xgot_c - gc, 2), "rival": round(xgot_f - gf, 2)},
        {"clave": "remates_totales", "equipo": tiros, "rival": 10},
        {"clave": "remates_puerta", "equipo": sot, "rival": 3},
        {"clave": "remates_fuera", "equipo": max(1, tiros - sot - 2), "rival": 4},
        {"clave": "remates_bloqueados", "equipo": 2, "rival": 3},
        {"clave": "remates_area", "equipo": max(2, tiros - 4), "rival": 6},
        {"clave": "remates_fuera_area", "equipo": 4, "rival": 4},
        {"clave": "grandes_ocasiones", "equipo": 3, "rival": 1},
        {"clave": "grandes_ocasiones_falladas", "equipo": 1, "rival": 1},
        {"clave": "al_palo", "equipo": 0, "rival": 0},
        {"clave": "toques_area_rival", "equipo": 26, "rival": 16},
        {"clave": "corners", "equipo": corners, "rival": 4},
        {"clave": "faltas", "equipo": 11, "rival": 12},
        {"clave": "tarjetas_amarillas", "equipo": amarillas, "rival": 2},
        {"clave": "tarjetas_rojas", "equipo": 0, "rival": 0},
        {"clave": "fueras_de_juego", "equipo": 2, "rival": 2},
        {"clave": "pases_precisos", "equipo": 410, "rival": 320},
        {"clave": "pases_totales", "equipo": 475, "rival": 395},
        {"clave": "pases_pct", "equipo": 86.3, "rival": 81.0},
        {"clave": "pases_campo_rival", "equipo": 115, "rival": 80},
        {"clave": "pases_largos_precisos", "equipo": 22, "rival": 18},
        {"clave": "pases_largos_pct", "equipo": 58.0, "rival": 50.0},
        {"clave": "centros_precisos", "equipo": 5, "rival": 3},
        {"clave": "centros_pct", "equipo": 26.0, "rival": 22.0},
        {"clave": "regates_exitosos", "equipo": 9, "rival": 6},
        {"clave": "regates_pct", "equipo": 54.0, "rival": 48.0},
        {"clave": "duelos_ganados", "equipo": 52, "rival": 45},
        {"clave": "duelos_suelo_ganados", "equipo": 38, "rival": 33},
        {"clave": "duelos_aereos_ganados", "equipo": 14, "rival": 12},
        {"clave": "duelos_aereos_pct", "equipo": 54.0, "rival": 46.0},
        {"clave": "entradas_ganadas", "equipo": 11, "rival": 10},
        {"clave": "intercepciones", "equipo": 8, "rival": 9},
        {"clave": "despejes", "equipo": 15, "rival": 21},
        {"clave": "recuperaciones", "equipo": 50, "rival": 46},
        {"clave": "paradas_portero", "equipo": 3, "rival": max(1, sot - gf)},
        {"clave": "saques_puerta", "equipo": 6, "rival": 8},
        {"clave": "saques_banda", "equipo": 17, "rival": 18},
    ]
    filas_1t = []
    filas_2t = []
    for f in filas_ft:
        k = f["clave"]
        if "pct" in k or k == "posesion":
            filas_1t.append(dict(f))
            filas_2t.append(dict(f))
        elif isinstance(f["equipo"], int):
            e1 = f["equipo"] // 2
            r1 = f["rival"] // 2
            filas_1t.append({"clave": k, "equipo": e1, "rival": r1})
            filas_2t.append({"clave": k, "equipo": f["equipo"] - e1, "rival": f["rival"] - r1})
        else:
            e1 = round(f["equipo"] * 0.45, 2)
            r1 = round(f["rival"] * 0.45, 2)
            filas_1t.append({"clave": k, "equipo": e1, "rival": r1})
            filas_2t.append({"clave": k, "equipo": round(f["equipo"] - e1, 2), "rival": round(f["rival"] - r1, 2)})

    return {
        "id_partido": f"mf_{equipo[:3].lower()}_{idx:02d}",
        "fecha": f"2026-09-{(idx % 28) + 1:02d}",
        "torneo": torneo,
        "competicion": torneo,
        "condicion": condicion,
        "local": equipo if condicion == "casa" else rival,
        "visitante": rival if condicion == "casa" else equipo,
        "rival": rival,
        "marcador": {"equipo": gf, "rival": gc},
        "marcador_1t": {"equipo": gf_1t, "rival": gc_1t},
        "marcador_2t": {"equipo": gf_2t, "rival": gc_2t},
        "goles_local": gf if condicion == "casa" else gc,
        "goles_visitante": gc if condicion == "casa" else gf,
        "periodos": {
            "FT": {"filas": filas_ft},
            "1T": {"filas": filas_1t},
            "2T": {"filas": filas_2t},
        },
    }


def test_55_patrones_y_super_cuadro_milan_ferencvaros():
    # 1. Validar Motor de 55 Patrones Exactos (P01 a P55)
    patrones_local, patrones_visita = calcular_patrones_completos_partido(
        CUADRO_A_MILAN_CASA,
        CUADRO_A_FERENC_VISITA,
    )

    claves_p_local = [k for k in patrones_local.keys() if k.startswith("P") and k[1:3].isdigit()]
    claves_p_visita = [k for k in patrones_visita.keys() if k.startswith("P") and k[1:3].isdigit()]

    assert len(claves_p_local) == 55, f"Se esperaban 55 patrones en local, se obtuvieron {len(claves_p_local)}"
    assert len(claves_p_visita) == 55, f"Se esperaban 55 patrones en visita, se obtuvieron {len(claves_p_visita)}"
    assert len(patrones_local) >= 55 and len(patrones_visita) >= 55

    for k, v in patrones_local.items():
        assert isinstance(v, (int, float)) and not math.isnan(float(v)), f"NaN en {k} (local)"
    for k, v in patrones_visita.items():
        assert isinstance(v, (int, float)) and not math.isnan(float(v)), f"NaN en {k} (visita)"

    # 2. Validar Motor de Inferencia (Reglas + Narrador XAI de 5 bloques y 15 razones)
    p_loc_adj, p_emp_adj, p_vis_adj, alertas = aplicar_reglas_matchup_55(
        patrones_local, patrones_visita, 0.635, 0.205, 0.160, "Milan", "Ferencváros"
    )
    assert abs(p_loc_adj + p_emp_adj + p_vis_adj - 1.0) < 1e-3

    cb_milan = calcular_cuadro_b_tendencias_15p(nombre_equipo="Milan", tendencias_override=CUADRO_B_MILAN)
    cb_ferenc = calcular_cuadro_b_tendencias_15p(nombre_equipo="Ferencváros", tendencias_override=CUADRO_B_FERENC)
    cc_milan = calcular_cuadro_c_timeline_15min(nombre_equipo="Milan", metricas_resumen=CUADRO_A_MILAN_GEN)
    cc_ferenc = calcular_cuadro_c_timeline_15min(nombre_equipo="Ferencváros", metricas_resumen=CUADRO_A_FERENC_GEN)

    informe_xai = generar_informe_narrativo_xai(
        "Milan", "Ferencváros",
        patrones_local, patrones_visita,
        p_loc_adj, p_emp_adj, p_vis_adj,
        alertas, cb_milan, cb_ferenc, cc_milan, cc_ferenc
    )
    assert "TICKET DE ANÁLISIS PROFUNDO" in informe_xai
    assert "1. EL ESPEJISMO DEL ATAQUE" in informe_xai
    assert "2. LA MURALLA DEFENSIVA Y PORTERÍA" in informe_xai
    assert "3. EL FACTOR TIEMPO: FATIGA Y PUNCH" in informe_xai
    assert "4. EL CRUCE TÁCTICO LETAL" in informe_xai
    assert "5. LA TRAMPA DEL MERCADO" in informe_xai
    assert "Patrón #01" in informe_xai and "Patrón #55" in informe_xai

    # 3. Validar Súper-Cuadro Especialista y Sección 1 (Cero -6.5 Goles y 5 Categorías Completas)
    rivales_serie_a = [
        "Inter", "Juventus", "Napoli", "Atalanta", "Roma", "Lazio", "Fiorentina",
        "Bologna", "Torino", "Udinese", "Genoa", "Monza", "Verona", "Lecce", "Empoli"
    ]
    partidos_milan = []
    for i, riv in enumerate(rivales_serie_a, 1):
        cond = "casa" if i % 2 == 1 else "fuera"
        gf = 2 if i <= 9 else (1 if i <= 13 else 0)
        gc = 0 if i <= 7 else (1 if i <= 13 else 2)
        partidos_milan.append(
            _crear_partido_sintetico(i, "Milan", riv, "Serie A", cond, gf, gc, 1.85, 1.75, 1.04, 56.0, 15, 6, 6, 2)
        )

    rivales_nb1 = [
        "Puskás Akadémia", "Paks", "Fehérvár", "Debrecen", "MTK Budapest",
        "Újpest", "Diósgyőr", "Zalaegerszeg", "Kecskemét", "Nyíregyháza",
        "Győr", "Paks II", "Vasas", "Honvéd", "Mezőkövesd"
    ]
    partidos_ferenc = []
    for i, riv in enumerate(rivales_nb1, 1):
        cond = "fuera" if i % 2 == 1 else "casa"
        gf = 3 if (cond == "casa" and i <= 8) else (2 if i <= 12 else 1)
        gc = 0 if i <= 8 else (1 if i <= 13 else 2)
        partidos_ferenc.append(
            _crear_partido_sintetico(i, "Ferencváros", riv, "NB I", cond, gf, gc, 1.98, 2.05, 0.85, 58.0, 15, 6, 6, 2)
        )

    doc_milan = {
        "equipo": {"nombre": "Milan", "id": "milan_1"},
        "partidos": partidos_milan,
        "cuadro_b_tendencias": CUADRO_B_MILAN,
        "cuadro_c_timeline": CUADRO_C_MILAN,
    }
    doc_ferenc = {
        "equipo": {"nombre": "Ferencváros", "id": "ferenc_1"},
        "partidos": partidos_ferenc,
        "cuadro_b_tendencias": CUADRO_B_FERENC,
        "cuadro_c_timeline": CUADRO_C_FERENC,
    }

    analisis = mm.analizar(doc_milan, doc_ferenc, "Milan", "Ferencváros")
    top5, sec1_filtrada, cnt_val = obtener_top5_y_seccion1_filtrados(analisis)
    sec1_organizada = _organizar_sec1_para_cuadro(
        analisis, sec1_filtrada, "Milan", "Ferencváros", analisis.motor.matriz_ft
    )

    categorias_super_cuadro = [c["categoria"] for c in sec1_organizada]
    texto_seccion_1_super_cuadro = " | ".join(
        f"{c['categoria']}: " + ", ".join(f"{p['lbl']} {p['pct']}" for p in c["pildoras"])
        for c in sec1_organizada
    )

    assert "-6.5 Goles" not in texto_seccion_1_super_cuadro
    assert "-5.5 Goles" not in texto_seccion_1_super_cuadro
    assert all(
        cat in categorias_super_cuadro
        for cat in ["GOLES PARTIDO", "GOLES POR EQUIPO", "CÓRNERES", "TARJETAS", "HÁNDICAP / 1X2 / DC"]
    )

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "informes", "mercados")
    os.makedirs(out_dir, exist_ok=True)
    ruta_super_cuadro = os.path.join(out_dir, "super_cuadro_milan_vs_ferencvaros_55p.png")
    ruta_ticket = os.path.join(out_dir, "ticket_milan_vs_ferencvaros_55p.png")
    ruta_auditoria_15p = os.path.join(out_dir, "auditoria_15p_milan_vs_ferencvaros_55p.png")

    res_cuadro = generar_cuadro_desde_analisis(analisis, doc_milan, doc_ferenc, destino=ruta_super_cuadro)
    res_ticket = renderizar_ticket_mercados_png(analisis, destino=ruta_ticket)
    res_aud15 = generar_cuadro_auditoria_15_partidos_png(analisis, doc_milan, doc_ferenc, destino=ruta_auditoria_15p)

    assert res_cuadro and os.path.exists(res_cuadro)
    assert res_ticket and os.path.exists(res_ticket)
    assert res_aud15 and os.path.exists(res_aud15)


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    test_55_patrones_y_super_cuadro_milan_ferencvaros()
    print("OK: test_motor_55_patrones superado exitosamente.")
