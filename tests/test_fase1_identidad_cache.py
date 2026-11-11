"""
tests/test_fase1_identidad_cache.py
───────────────────────────────────────────────────────────────────────────────
Fase 1: Verificación de Identidad Previa, Resolución Conjunta de Entidades,
Manejo de Calificadores, Competición de Copa/Internacional y Caché Canónica con ID.
───────────────────────────────────────────────────────────────────────────────
"""

import pytest
from resolvedor_entidades import (
    resolver,
    parsear_comando_mercados,
    construir_clave_cache_entidad,
    formatear_mensaje_entidades_verificadas,
    extraer_calificadores_nombre,
    resolver_equipo_individual,
    FOTMOB_IDS_CONOCIDOS,
)
from auditoria_historica import (
    _cargar_cache_resiliencia,
    _guardar_cache_resiliencia,
    guardar_auditoria_en_memoria,
    obtener_auditoria_en_memoria,
    limpiar_cache_memoria_auditorias,
)


def test_liverpool_m_vs_juventud_resuelve_uruguay():
    """Liverpool M. vs Juventud en contexto uruguayo resuelve a Liverpool FC [URY] y Juventud de Las Piedras [URY]."""
    res = resolver("", "Liverpool M.", "Juventud")
    assert res.equipo_local == "Liverpool FC"
    assert res.equipo_visita == "Juventud de Las Piedras"
    assert res.pais_iso == "URY"
    assert res.pais_local == "URY"
    assert res.pais_visita == "URY"
    assert res.identidad_verificada is True
    assert res.estado_identidad == "verificada"
    assert res.id_proveedor_local == "7942"
    assert res.id_proveedor_visita == "8390"
    assert "calificador" in res.criterio_desempate or "interseccion" in res.criterio_desempate


def test_liverpool_vs_juventud_sin_calificador_resuelve_uruguay():
    """Ante Liverpool vs Juventud sin calificador 'M.', el rival Juventud (URY) desempata hacia Liverpool FC [URY]."""
    res = resolver("", "Liverpool", "Juventud")
    assert res.equipo_local == "Liverpool FC"
    assert res.equipo_visita == "Juventud de Las Piedras"
    assert res.pais_iso == "URY"
    assert res.pais_local == "URY"
    assert res.pais_visita == "URY"
    assert res.identidad_verificada is True


def test_liverpool_vs_arsenal_resuelve_inglaterra():
    """Ante Liverpool vs Arsenal, el cruce de ambos clubes en Premier League resuelve a Inglaterra [ENG]."""
    res = resolver("", "Liverpool", "Arsenal")
    assert res.equipo_local == "Liverpool"
    assert res.equipo_visita == "Arsenal"
    assert res.pais_iso == "ENG"
    assert res.pais_local == "ENG"
    assert res.pais_visita == "ENG"
    assert res.id_proveedor_local == "8650"
    assert res.id_proveedor_visita == "9825"
    assert res.identidad_verificada is True


def test_copa_colombia_preserva_copa_no_hereda_primera_a():
    """Copa Colombia Once Caldas vs Atlético Nacional preserva la competición de copa y no hereda Primera A."""
    parsed = parsear_comando_mercados("/mercados Copa Colombia Once Caldas vs Atletico Nacional")
    assert parsed["liga"] == "Copa Colombia"
    assert parsed["es_copa"] is True
    assert parsed["pais_iso"] == "COL"
    assert parsed["equipo_local"] == "Once Caldas"
    assert parsed["equipo_visita"] == "Atlético Nacional"
    assert parsed["id_proveedor_local"] == "10091"
    assert parsed["id_proveedor_visita"] == "10093"


def test_champions_league_internacional_no_fuerza_mismo_pais():
    """En duelos internacionales (Champions League), no se obliga a que ambos clubes pertenezcan al mismo país."""
    res = resolver("Champions League", "Real Madrid", "Bayern Munich")
    assert res.equipo_local == "Real Madrid"
    assert res.equipo_visita == "FC Bayern München"
    assert res.pais_iso == "INT"
    assert res.pais_local == "ESP"
    assert res.pais_visita == "DEU"
    assert res.es_internacional is True

    msg = formatear_mensaje_entidades_verificadas(
        res.equipo_local, res.pais_local, res.equipo_visita, res.pais_visita, res.liga
    )
    assert "[ESP]" in msg
    assert "[DEU]" in msg
    assert "Champions League" in msg


def test_identidad_no_verificada_en_club_desconocido():
    """Clubes inexistentes o con ambigüedad no resuelta devuelven estado 'identidad_no_verificada'."""
    res = resolver("", "EquipoFantasmaInexistente123", "OtroClubDesconocido456")
    assert res.identidad_verificada is False
    assert res.estado_identidad == "identidad_no_verificada"
    assert "No se pudo verificar la identidad" in res.motivo_identidad
    assert res.nivel_confianza == "nula"


def test_clave_cache_entidad_diferenciada_por_id_y_pais():
    """Las claves canónicas de caché para entidades homónimas (Liverpool URY vs Liverpool ENG) jamás colisionan."""
    clave_ury = construir_clave_cache_entidad("Liverpool FC", "URY", False, False)
    clave_eng = construir_clave_cache_entidad("Liverpool", "ENG", False, False)
    assert clave_ury == "fotmob_7942_URY_False_False"
    assert clave_eng == "fotmob_8650_ENG_False_False"

    # Clave con proveedor explícito Flashscore
    clave_flash = construir_clave_cache_entidad("7942", "URY", False, False, proveedor="flashscore")
    assert clave_flash == "flashscore_7942_URY_False_False"
    assert clave_ury != clave_eng

    # Clave con competición de copa
    clave_copa = construir_clave_cache_entidad("Once Caldas", "COL", False, False, competicion="Copa Colombia")
    clave_liga = construir_clave_cache_entidad("Once Caldas", "COL", False, False, competicion="Primera A")
    assert "copa_colombia" in clave_copa
    assert clave_copa != clave_liga


def test_cache_resiliencia_rechaza_pais_incompatible():
    """Una caché de Liverpool Inglaterra [ENG] es rechazada y marcada como no reutilizable al consultar Liverpool [URY]."""
    limpiar_cache_memoria_auditorias()

    doc_liverpool_eng = {
        "equipo": {"id": "8650", "nombre": "Liverpool", "liga": "Premier League", "pais_iso": "ENG"},
        "identidad": {"id_proveedor": "8650", "nombre_canonico": "Liverpool", "pais_iso": "ENG"},
        "partidos": [{"id_partido": f"p{i}", "fecha": "2026-01-01"} for i in range(15)],
        "resumen": {"partidos_con_datos": 15},
    }
    # Guardar bajo clave de Inglaterra y slug genérico liverpool
    guardar_auditoria_en_memoria("8650_ENG_False_False", doc_liverpool_eng)
    guardar_auditoria_en_memoria("liverpool", doc_liverpool_eng)
    _guardar_cache_resiliencia("test_liverpool_eng", doc_liverpool_eng)

    import json
    import os
    from auditoria_historica import CACHE_RESILIENCIA_DIR

    # 1. Entrada antigua sin proveedor (legado_sin_proveedor)
    ruta_legado = os.path.join(CACHE_RESILIENCIA_DIR, "cache_legado_sin_prov.json")
    with open(ruta_legado, "w", encoding="utf-8") as f:
        json.dump({"equipo": {"nombre": "Liverpool", "pais_iso": "ENG"}}, f)

    doc_legado = _cargar_cache_resiliencia("legado_sin_prov", "Liverpool", meta_ctx={"pais_iso": "ENG"})
    assert doc_legado is None, "Entrada antigua sin proveedor no debe ser reutilizada"
    with open(ruta_legado, "r", encoding="utf-8") as f:
        doc_legado_disco = json.load(f)
    assert doc_legado_disco.get("legado_sin_proveedor") is True
    assert doc_legado_disco.get("no_reutilizable") is True

    # 2. Entrada con proveedor FotMob y país incompatible
    doc_liverpool_eng["proveedor"] = "fotmob"
    _guardar_cache_resiliencia("fotmob_test_liverpool_eng", doc_liverpool_eng)
    meta_ury = {"pais_iso": "URY", "liga": "Primera División Uruguay"}
    doc_cargado = _cargar_cache_resiliencia("fotmob_test_liverpool_eng", "Liverpool", meta_ctx=meta_ury)
    assert doc_cargado is None
    with open(os.path.join(CACHE_RESILIENCIA_DIR, "cache_fotmob_test_liverpool_eng.json"), "r", encoding="utf-8") as f:
        doc_disco = json.load(f)
    assert doc_disco.get("antiguo") is True
    assert doc_disco.get("no_reutilizable") is True

    # 3. Con contexto de Inglaterra (ENG) y proveedor válido, la caché sí se reutiliza
    meta_eng = {"pais_iso": "ENG", "liga": "Premier League"}
    doc_eng_ok = {
        "proveedor": "fotmob",
        "equipo": {"id": "8650", "nombre": "Liverpool", "liga": "Premier League", "pais_iso": "ENG"},
        "identidad": {"id_proveedor": "8650", "nombre_canonico": "Liverpool", "pais_iso": "ENG"},
        "partidos": [{"id_partido": f"p{i}", "fecha": "2026-01-01"} for i in range(15)],
        "resumen": {"partidos_con_datos": 15},
    }
    _guardar_cache_resiliencia("fotmob_test_liverpool_eng_valido", doc_eng_ok)
    res_eng = _cargar_cache_resiliencia("fotmob_test_liverpool_eng_valido", "Liverpool", meta_ctx=meta_eng)
    assert res_eng is not None
    assert res_eng["equipo"]["pais_iso"] == "ENG"
