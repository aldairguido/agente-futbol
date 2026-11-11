"""
tests/test_fase2_extraccion_auditoria.py
───────────────────────────────────────────────────────────────────────────────
Fase 2: Extracción y Auditoría Fila por Fila (Sección 2).
Historial de 15 oficiales, historial_oficial_incompleto, conteos separados
(localizados, marcador, detalle FT, 1T, 2T, solo marcador), procedencia por
celda y verificación contra registros originales.
───────────────────────────────────────────────────────────────────────────────
"""

import pytest
from auditoria_historica import (
    _construir_resumen,
    verificar_consistencia_recuento_auditoria,
    LAS_45_METRICAS_OFICIALES,
)


def _crear_partido_mock(
    id_partido: str,
    con_marcador: bool = True,
    claves_ft: list[str] | None = None,
    claves_1t: list[str] | None = None,
    claves_2t: list[str] | None = None,
    es_amistoso: bool = False,
    procedencia: str = "fotmob",
    estimado: bool = False,
) -> dict:
    filas_ft = []
    for k in (claves_ft or []):
        filas_ft.append({
            "clave": k,
            "equipo": 5.0,
            "rival": 4.0,
            "procedencia": procedencia,
            "estimado": estimado,
        })
    filas_1t = []
    for k in (claves_1t or []):
        filas_1t.append({
            "clave": k,
            "equipo": 2.0,
            "rival": 1.0,
            "procedencia": procedencia,
            "estimado": estimado,
        })
    filas_2t = []
    for k in (claves_2t or []):
        filas_2t.append({
            "clave": k,
            "equipo": 3.0,
            "rival": 3.0,
            "procedencia": procedencia,
            "estimado": estimado,
        })

    return {
        "id_partido": id_partido,
        "fecha": "2026-03-01",
        "rival": "Rival FC",
        "condicion": "casa",
        "torneo": "Liga",
        "es_amistoso": es_amistoso,
        "goles_equipo": 2 if con_marcador else None,
        "goles_rival": 1 if con_marcador else None,
        "periodos": {
            "FT": {"filas": filas_ft},
            "1T": {"filas": filas_1t},
            "2T": {"filas": filas_2t},
        },
    }


def test_conteos_separados_15_oficiales_tipo_tavernes():
    """Caso tipo Tavernes: 15 oficiales, pero solo 2 con detalle FT y 13 solo con marcador."""
    partidos = []
    # 2 partidos con detalle estadístico (10 claves en FT, 5 en 1T y 2T)
    for i in range(2):
        partidos.append(_crear_partido_mock(
            id_partido=f"p_det_{i}",
            con_marcador=True,
            claves_ft=["remates_totales", "remates_puerta", "corners", "tarjetas_amarillas", "posesion_pct"],
            claves_1t=["remates_totales", "corners"],
            claves_2t=["remates_totales", "corners"],
        ))
    # 13 partidos solo con marcador (0 claves estadísticas)
    for i in range(13):
        partidos.append(_crear_partido_mock(
            id_partido=f"p_solo_{i}",
            con_marcador=True,
            claves_ft=[],
            claves_1t=[],
            claves_2t=[],
        ))

    auditoria = {
        "limite_partidos": 15,
        "equipo": {"nombre": "Tavernes"},
        "partidos": partidos,
    }
    resumen = _construir_resumen(auditoria)

    assert resumen["partidos_oficiales_localizados"] == 15
    assert resumen["partidos_con_marcador"] == 15
    assert resumen["partidos_con_detalle_ft"] == 2
    assert resumen["partidos_con_detalle_1t"] == 2
    assert resumen["partidos_con_detalle_2t"] == 2
    assert resumen["partidos_con_detalle_1t_2t"] == 2
    assert resumen["partidos_solo_marcador"] == 13
    assert resumen["historial_oficial_incompleto"] is False
    assert resumen["etiqueta_resumen_cobertura"] == "15/15 oficiales · 2/15 con detalle FT · 13/15 solo con marcador"


def test_historial_oficial_incompleto_13_de_15():
    """Si el club solo tiene 13 partidos oficiales, se declara historial_oficial_incompleto."""
    partidos = [_crear_partido_mock(f"p_{i}", con_marcador=True, claves_ft=["remates_totales"]) for i in range(13)]
    auditoria = {
        "limite_partidos": 15,
        "equipo": {"nombre": "Club Incompleto"},
        "partidos": partidos,
    }
    resumen = _construir_resumen(auditoria)

    assert resumen["partidos_oficiales_localizados"] == 13
    assert resumen["historial_oficial_incompleto"] is True
    assert "historial_oficial_incompleto" in resumen["etiqueta_resumen_cobertura"]
    assert "13/15" in resumen["etiqueta_resumen_cobertura"]


def test_caso_tipo_anaitasuna_cero_partidos_con_detalle():
    """Caso tipo Anaitasuna: 15 oficiales localizados, 15 con marcador, pero 0 con detalle estadístico."""
    partidos = [_crear_partido_mock(f"p_{i}", con_marcador=True, claves_ft=[]) for i in range(15)]
    auditoria = {
        "limite_partidos": 15,
        "equipo": {"nombre": "Anaitasuna"},
        "partidos": partidos,
    }
    resumen = _construir_resumen(auditoria)

    assert resumen["partidos_oficiales_localizados"] == 15
    assert resumen["partidos_con_marcador"] == 15
    assert resumen["partidos_con_detalle_ft"] == 0
    assert resumen["partidos_solo_marcador"] == 15
    assert resumen["historial_oficial_incompleto"] is False


def test_conteo_estricto_claves_unicas_sin_duplicar_rotulos():
    """Si una clave como 'remates_totales' aparece en múltiples secciones de la fuente, no suma doble."""
    filas_ft = [
        {"clave": "remates_totales", "equipo": 12.0, "rival": 8.0},
        {"clave": "remates_totales", "equipo": 12.0, "rival": 8.0},  # segundo rótulo idéntico
        {"clave": "corners", "equipo": 5.0, "rival": 3.0},
    ]
    p = {
        "id_partido": "p_dup",
        "goles_equipo": 1,
        "goles_rival": 0,
        "periodos": {"FT": {"filas": filas_ft}, "1T": {"filas": []}, "2T": {"filas": []}},
    }
    auditoria = {"limite_partidos": 1, "partidos": [p]}
    resumen = _construir_resumen(auditoria)

    assert p["resumen_claves"]["FT"]["k_45_equipo"] == 2
    assert p["resumen_claves"]["FT"]["equipo"] == ["corners", "remates_totales"]


def test_cero_publicado_cuenta_como_observacion():
    """Un 0 publicado (por ejemplo 0 tarjetas rojas o 0 xG) cuenta como dato observado."""
    filas_ft = [
        {"clave": "tarjetas_rojas", "equipo": 0.0, "rival": 0.0, "procedencia": "fotmob"},
    ]
    p = {
        "id_partido": "p_cero",
        "goles_equipo": 0,
        "goles_rival": 0,
        "periodos": {"FT": {"filas": filas_ft}, "1T": {"filas": []}, "2T": {"filas": []}},
    }
    auditoria = {"limite_partidos": 1, "partidos": [p]}
    resumen = _construir_resumen(auditoria)

    assert "tarjetas_rojas" in p["resumen_claves"]["FT"]["equipo"]
    assert resumen["partidos_con_detalle_ft"] == 1


def test_estimacion_modelo_no_incrementa_observadas():
    """Una fila marcada con estimado=True o procedencia='estimacion_modelo' no cuenta como observada."""
    filas_ft = [
        {"clave": "xg", "equipo": 1.45, "rival": 0.90, "estimado": True, "procedencia": "estimacion_modelo"},
    ]
    p = {
        "id_partido": "p_est",
        "goles_equipo": 1,
        "goles_rival": 1,
        "periodos": {"FT": {"filas": filas_ft}, "1T": {"filas": []}, "2T": {"filas": []}},
    }
    auditoria = {"limite_partidos": 1, "partidos": [p]}
    resumen = _construir_resumen(auditoria)

    assert "xg" not in p["resumen_claves"]["FT"]["equipo"]
    assert p["resumen_claves"]["FT"]["k_45_equipo"] == 0
    assert resumen["partidos_con_detalle_ft"] == 0
    assert resumen["partidos_solo_marcador"] == 1


def test_verificacion_consistencia_recuento_falla_si_difieren():
    """verificar_consistencia_recuento_auditoria falla si algún contador difiere de los registros originales."""
    partidos = [_crear_partido_mock("p1", con_marcador=True, claves_ft=["remates_totales"])]
    resumen_valido = {
        "partidos_auditados": 1,
        "partidos_oficiales_localizados": 1,
        "partidos_con_marcador": 1,
        "partidos_con_detalle_ft": 1,
        "partidos_con_detalle_1t": 0,
        "partidos_con_detalle_2t": 0,
        "partidos_solo_marcador": 0,
    }
    _construir_resumen({"partidos": partidos})  # llena p["resumen_claves"]
    assert verificar_consistencia_recuento_auditoria(resumen_valido, partidos) is True

    # Resumen adulterado con 2 partidos con detalle FT cuando solo hay 1
    resumen_falso = dict(resumen_valido, partidos_con_detalle_ft=2)
    assert verificar_consistencia_recuento_auditoria(resumen_falso, partidos) is False
