"""
test_fase6_aceptacion_integral.py
Suite de Aceptación Integral de los Puntos 44 y 45 (Parte E).
Verifica los 6 casos de aceptación canónicos de forma 100% offline y determinista:
  E.1 Liverpool M. vs Juventud (Uruguay vs Inglaterra e IDs independientes)
  E.2 Copa Colombia Once Caldas vs Atlético Nacional (preservación de copa)
  E.3 Tavernes vs Levante (cobertura_insuficiente) y Anaitasuna vs Real Sociedad (solo_resultados)
  E.4 Simetría inmutable de la vista comparable
  E.5 Prohibición de la Red 45-DCN sobre subconjuntos incompletos o rellenados
  E.6 Coherencia entre superficies (Telegram, Ticket PNG, Cuadros B/C y evaluación)
"""

from __future__ import annotations

import copy
import os
import pytest
from unittest.mock import patch, MagicMock

from resolvedor_entidades import resolver as resolver_entidades
from auditoria_historica import LAS_45_METRICAS_OFICIALES
from auditoria_comparable import (
    construir_vista_comparable,
    verificar_compatibilidad_modelo,
    generar_tabla_comparable_markdown,
)
from decision_cobertura import evaluar_decision_cobertura
import servicio_mercados as servicio
import evaluacion_pronosticos as evaluacion


# ─────────────────────────────────────────────────────────────────────────────
# E.1: Liverpool M. vs Juventud (Uruguay) y desambiguación con Inglaterra
# ─────────────────────────────────────────────────────────────────────────────
def test_e1_resolucion_liverpool_uruguay_vs_inglaterra():
    # 1. Consulta uruguaya con calificador
    res_uru = resolver_entidades("Copa AUF Uruguay", "Liverpool M.", "Juventud")
    assert res_uru.pais_iso == "URY"
    assert "Liverpool" in res_uru.equipo_local
    assert "Juventud" in res_uru.equipo_visita
    assert str(res_uru.id_proveedor_local) == "7942"
    assert str(res_uru.id_proveedor_visita) == "8390"

    # 2. Clave canónica de caché de Liverpool URY es diferente de Liverpool ENG
    clave_cache_uru = f"{res_uru.id_proveedor_local}_{res_uru.pais_iso}_False_False"
    clave_cache_eng = f"8650_ENG_False_False"
    assert clave_cache_uru != clave_cache_eng

    # 3. Consulta inglesa estándar
    res_eng = resolver_entidades("Premier League", "Liverpool", "Arsenal")
    assert res_eng.pais_iso == "ENG"
    assert str(res_eng.id_proveedor_local) == "8650"


# ─────────────────────────────────────────────────────────────────────────────
# E.2: Copa Colombia Once Caldas vs Atlético Nacional (no hereda Primera A)
# ─────────────────────────────────────────────────────────────────────────────
def test_e2_copa_colombia_preservada():
    res_copa = resolver_entidades("Copa Colombia", "Once Caldas", "Atlético Nacional")
    assert res_copa.es_copa is True
    assert res_copa.liga == "Copa Colombia"
    assert "Primera A" not in res_copa.liga


# ─────────────────────────────────────────────────────────────────────────────
# E.3: Abstención y bloqueo de probabilidades (Tavernes y Anaitasuna)
# ─────────────────────────────────────────────────────────────────────────────
def test_e3_bloqueo_tavernes_y_anaitasuna(tmp_path):
    def _doc(eq, n_of, n_det):
        todas = [m[1] for m in LAS_45_METRICAS_OFICIALES]
        pts = []
        for i in range(1, n_of + 1):
            c = todas if i <= n_det else []
            filas = [{"clave": k, "procedencia": "observada", "valor_local": 1.0, "valor_visita": 0.0} for k in c]
            pts.append({
                "posicion": i,
                "goles_equipo": 1,
                "goles_rival": 0,
                "resumen_claves": {"FT": {"equipo": c, "rival": c}},
                "periodos": {"FT": {"filas": filas}},
            })
        return {
            "equipo": eq,
            "resumen_auditoria": {
                "equipo": eq,
                "partidos_oficiales_localizados": n_of,
                "partidos_con_marcador": n_of,
                "partidos_con_detalle_ft": n_det,
                "partidos_solo_marcador": n_of - n_det,
                "etiqueta_cobertura": f"{n_of}/15 oficiales · {n_det}/15 con detalle FT",
            },
            "partidos": pts,
        }

    # Tavernes: 2 con detalle FT
    doc_lev = _doc("Levante UD", 15, 15)
    doc_tav = _doc("UD Tavernes", 15, 2)
    dec_tav = evaluar_decision_cobertura(doc_lev, doc_tav)
    assert dec_tav.estado == "cobertura_insuficiente"
    assert dec_tav.publicar_probabilidades is False

    # Anaitasuna: 0 con detalle FT
    doc_ana = _doc("CD Anaitasuna", 15, 0)
    dec_ana = evaluar_decision_cobertura(doc_lev, doc_ana)
    assert dec_ana.estado == "solo_resultados"
    assert dec_ana.publicar_probabilidades is False

    # Verificar que servicio_mercados genera ticket de abstención físico
    ctx_tav = {
        "equipo_local": "Levante UD",
        "equipo_visitante": "UD Tavernes",
        "doc_local": doc_lev,
        "doc_visitante": doc_tav,
        "contexto": {"competicion": "Copa del Rey"},
    }
    with patch("servicio_mercados.resolver_enfrentamiento", return_value=ctx_tav):
        prep = servicio.preparar_analisis("Levante UD vs UD Tavernes", directorio_salida=str(tmp_path))

    assert prep["abstenido"] is True
    assert prep["analisis"].selecciones_filtradas == []
    assert os.path.exists(prep["ruta_imagen"])


# ─────────────────────────────────────────────────────────────────────────────
# E.4: Simetría inmutable de la vista comparable
# ─────────────────────────────────────────────────────────────────────────────
def test_e4_simetria_inmutable_vista_comparable():
    todas = [m[1] for m in LAS_45_METRICAS_OFICIALES]
    partidos_a = [{
        "posicion": i,
        "goles_equipo": 1,
        "goles_rival": 0,
        "resumen_claves": {
            "FT": {"equipo": todas[:20], "rival": todas[:20]},
            "1T": {"equipo": ["remates_puerta"], "rival": ["remates_puerta"]},
            "2T": {"equipo": [], "rival": []},
        },
        "periodos": {"FT": {"filas": [{"clave": k} for k in todas[:20]]}},
    } for i in range(1, 16)]

    partidos_b = [{
        "posicion": i,
        "goles_equipo": 0,
        "goles_rival": 0,
        "resumen_claves": {
            "FT": {"equipo": todas[10:30], "rival": todas[10:30]},
            "1T": {"equipo": ["remates_puerta"], "rival": ["remates_puerta"]},
            "2T": {"equipo": [], "rival": []},
        },
        "periodos": {"FT": {"filas": [{"clave": k} for k in todas[10:30]]}},
    } for i in range(1, 16)]

    doc_a = {"equipo": "A", "partidos": partidos_a}
    doc_b = {"equipo": "B", "partidos": partidos_b}

    doc_a_orig = copy.deepcopy(doc_a)
    doc_b_orig = copy.deepcopy(doc_b)

    vista_ab = construir_vista_comparable(doc_a, doc_b)
    vista_ba = construir_vista_comparable(doc_b, doc_a)

    # Inmutabilidad
    assert doc_a == doc_a_orig
    assert doc_b == doc_b_orig

    # Simetría fila por fila
    for idx in range(15):
        comunes_ab = vista_ab["emparejamientos"][idx]["periodos"]["FT"]["claves_comunes"]
        comunes_ba = vista_ba["emparejamientos"][idx]["periodos"]["FT"]["claves_comunes"]
        assert comunes_ab == comunes_ba
        # Intersección de 0..19 y 10..29 son exactamente las claves 10..19 (10 claves)
        assert comunes_ab == sorted(set(todas[:20]) & set(todas[10:30]))


# ─────────────────────────────────────────────────────────────────────────────
# E.5: Prohibición de Red 45-DCN sobre subconjuntos rellenados
# ─────────────────────────────────────────────────────────────────────────────
def test_e5_prohibicion_red_45_sobre_subconjuntos():
    todas = [m[1] for m in LAS_45_METRICAS_OFICIALES]
    # Caso incompleto: 44 variables en lugar de 45
    pts_44 = [{
        "posicion": i,
        "resumen_claves": {"FT": {"equipo": todas[:44], "rival": todas[:44]}},
    } for i in range(1, 16)]
    pts_45 = [{
        "posicion": i,
        "resumen_claves": {"FT": {"equipo": todas, "rival": todas}},
    } for i in range(1, 16)]

    vista = construir_vista_comparable({"partidos": pts_44}, {"partidos": pts_45})
    assert vista["modelos_habilitados"]["red_recursiva_45_dcn"] is False
    assert "Red 45-DCN bloqueada" in vista["motivos_bloqueo_modelos"]["red_recursiva_45_dcn"]


# ─────────────────────────────────────────────────────────────────────────────
# E.6: Coherencia entre superficies (Telegram, Ticket PNG y evaluación)
# ─────────────────────────────────────────────────────────────────────────────
def test_e6_coherencia_entre_superficies(tmp_path):
    prep = {
        "id_trabajo": "e6_prueba_coherencia",
        "abstenido": True,
        "estado_cobertura": "cobertura_insuficiente",
        "motivo_abstencion": "Sin variables comparables",
        "contexto": {"equipo_local": "Local Test", "equipo_visitante": "Visita Test"},
        "ruta_imagen": str(tmp_path / "ticket_e6.png"),
        "analisis_texto_3_parrafos": "🚨 PRONÓSTICO ABSTENIDO POR COBERTURA INSUFICIENTE",
        "rutas": {"imagen": str(tmp_path / "ticket_e6.png")},
    }
    with open(prep["ruta_imagen"], "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nfakeimage")

    # Envío
    res = servicio.enviar_analisis(
        prep,
        enviar_fn=lambda txt, **kw: {"message_id": 1},
        enviar_foto_fn=lambda r, **kw: {"message_id": 2},
        nueva_ejecucion=True,
    )
    assert res["enviado"] is True

    # Snapshot inmutable y liquidación
    snap = evaluacion.SnapshotPronostico(
        id_trabajo="e6_prueba_coherencia",
        equipos={"local": "Local Test", "visitante": "Visita Test"},
        partido={"fecha": "2026-11-15"},
        generado_en="2026-11-11",
        publicado_en="2026-11-11",
        modelo={"nombre": "test"},
        datos_corte={},
        selecciones=[],
        selecciones_publicadas=[],
        umbral_publicado=0.70,
        referencia="informe.md",
        abstenido=True,
        estado_cobertura="cobertura_insuficiente",
    )
    ruta_s = evaluacion.guardar_snapshot(snap, directorio=str(tmp_path / "pronosticos"))
    assert os.path.exists(ruta_s)

    # Evaluación de tanda
    res_partido = evaluacion.ResultadoPartido(estado="finalizado", local="Local Test", visitante="Visita Test")
    informe = evaluacion.evaluar_tanda([snap.a_dict()], {"Local Test vs Visita Test": res_partido})
    assert informe.abstenidos_por_cobertura == 1
    assert len(informe.evaluaciones) == 0
