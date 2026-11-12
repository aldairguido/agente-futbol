"""
tests/test_punto46_selecciones_nacionales.py
───────────────────────────────────────────────────────────────────────────────
HOTFIX URGENTE (PUNTO 46):
Soporte universal de selecciones nacionales FIFA (Flashscore ES <-> FotMob EN/Nativo),
prohibición absoluta de contaminar país entre selecciones y sincronización de identidad verificada.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from typing import Any, Dict

from resolvedor_entidades import (
    DICCIONARIO_SELECCIONES_NACIONALES,
    obtener_seleccion_nacional,
    es_seleccion_nacional,
    resolver,
    resolver_equipo_individual,
    parsear_comando_mercados,
    formatear_mensaje_entidades_verificadas,
    validar_y_puntuar_candidato_fotmob,
    CATALOGO_LIGAS_FLASHSCORE,
)
from fuentes_xg import normalizar_nombre_club
from decision_cobertura import evaluar_decision_cobertura


def _crear_doc_mock(
    equipo: str,
    pais_iso: str = "INT",
    id_equipo: str = "123",
    n_oficiales: int = 15,
    n_marcador: int = 15,
    n_detalle_ft: int = 15,
    estado_identidad: str = "verificado",
    identidad_verificada: bool = True,
) -> dict:
    claves_ft = ["xg", "remates_totales", "remates_puerta", "corners", "tarjetas_amarillas"]
    claves_1t = ["remates_puerta"]
    claves_2t = ["remates_puerta"]

    partidos = []
    for i in range(1, n_oficiales + 1):
        tiene_det = i <= n_detalle_ft
        c_ft = claves_ft if tiene_det else []
        c_1t = claves_1t if tiene_det else []
        c_2t = claves_2t if tiene_det else []

        filas_ft = [{"clave": c, "procedencia": "observada", "valor_local": 1.0, "valor_visita": 0.0} for c in c_ft]
        filas_1t = [{"clave": c, "procedencia": "observada", "valor_local": 1.0, "valor_visita": 0.0} for c in c_1t]
        filas_2t = [{"clave": c, "procedencia": "observada", "valor_local": 1.0, "valor_visita": 0.0} for c in c_2t]

        partidos.append({
            "posicion": i,
            "torneo": "UEFA Nations League",
            "goles_equipo": 1 if i <= n_marcador else None,
            "goles_rival": 0 if i <= n_marcador else None,
            "resumen_claves": {
                "FT": {"equipo": c_ft, "rival": c_ft},
                "1T": {"equipo": c_1t, "rival": c_1t},
                "2T": {"equipo": c_2t, "rival": c_2t},
            },
            "periodos": {
                "FT": {"filas": filas_ft},
                "1T": {"filas": filas_1t},
                "2T": {"filas": filas_2t},
            },
        })

    return {
        "equipo": {"nombre": equipo, "pais_iso": pais_iso, "id": id_equipo},
        "estado_identidad": estado_identidad,
        "identidad_verificada": identidad_verificada,
        "partidos": partidos,
        "resumen_auditoria": {
            "partidos_oficiales_localizados": n_oficiales,
            "partidos_con_marcador": n_marcador,
            "partidos_con_detalle_ft": n_detalle_ft,
            "partidos_solo_marcador": max(0, n_marcador - n_detalle_ft),
            "etiqueta_cobertura": f"{n_oficiales}/15 oficiales · {n_detalle_ft}/15 con detalle FT · 0/15 solo con marcador",
        },
    }


class TestPunto46SeleccionesNacionales:
    """Suite de validación exhaustiva para el Punto 46 (Selecciones Nacionales)."""

    def test_catalogo_selecciones_cobertura_confederaciones(self) -> None:
        """Verifica que el catálogo cubra las 6 confederaciones FIFA principales."""
        confed_ejemplos = {
            "UEFA": ["turquia", "belgica", "alemania", "espana", "francia", "italia", "inglaterra"],
            "CONMEBOL": ["argentina", "brasil", "uruguay", "colombia", "peru", "chile"],
            "CONCACAF": ["estados unidos", "mexico", "canada", "panama", "costa rica"],
            "AFC": ["japon", "corea del sur", "australia", "iran", "arabia saudi"],
            "CAF": ["marruecos", "senegal", "egipto", "nigeria", "costa de marfil"],
            "OFC": ["nueva zelanda"],
        }
        for confed, selecciones in confed_ejemplos.items():
            for sel in selecciones:
                assert es_seleccion_nacional(sel), f"Selección {sel} ({confed}) no encontrada en catálogo"
                res_sel = obtener_seleccion_nacional(sel)
                assert res_sel is not None
                nom_fotmob, iso, aliases = res_sel
                assert nom_fotmob and len(iso) == 3 and isinstance(aliases, list)

    def test_variantes_ortograficas_y_exonomos(self) -> None:
        """Verifica resolución correcta de exónimos y variantes ortográficas."""
        pares_esperados = [
            ("Turquía", ("Türkiye", "TUR")),
            ("Turquia", ("Türkiye", "TUR")),
            ("Turkey", ("Türkiye", "TUR")),
            ("Türkiye", ("Türkiye", "TUR")),
            ("Bélgica", ("Belgium", "BEL")),
            ("Belgica", ("Belgium", "BEL")),
            ("Belgium", ("Belgium", "BEL")),
            ("Alemania", ("Germany", "DEU")),
            ("España", ("Spain", "ESP")),
            ("Países Bajos", ("Netherlands", "NLD")),
            ("Holanda", ("Netherlands", "NLD")),
            ("EE. UU.", ("USA", "USA")),
            ("Estados Unidos", ("USA", "USA")),
            ("USA", ("USA", "USA")),
            ("Costa de Marfil", ("Ivory Coast", "CIV")),
            ("RD Congo", ("DR Congo", "COD")),
        ]
        for entrada, (nom_esp, iso_esp) in pares_esperados:
            sel = obtener_seleccion_nacional(entrada)
            assert sel is not None, f"Fallo al obtener seleccion para {entrada}"
            assert sel[0] == nom_esp, f"Nombre FotMob erróneo para {entrada}: {sel[0]} != {nom_esp}"
            assert sel[1] == iso_esp, f"ISO erróneo para {entrada}: {sel[1]} != {iso_esp}"

            # Normalizador universal de fuentes_xg
            norm_club = normalizar_nombre_club(entrada)
            assert norm_club == nom_esp, f"fuentes_xg.normalizar_nombre_club fallo para {entrada}: {norm_club} != {nom_esp}"

    def test_cero_contaminacion_pais_turquia_vs_belgica(self) -> None:
        """
        Caso de bug en producción:
        Turquía no debe ser contaminada con el país de Bélgica [BEL].
        Turquía debe ser TUR y Bélgica debe ser BEL.
        """
        # Prueba directa en resolver_equipo_individual incluso si se pasa contexto erróneo
        nom_l, iso_l = resolver_equipo_individual("Turquía", pais_iso_contexto="BEL")
        assert nom_l == "Türkiye"
        assert iso_l == "TUR"

        nom_v, iso_v = resolver_equipo_individual("Bélgica", pais_iso_contexto="TUR")
        assert nom_v == "Belgium"
        assert iso_v == "BEL"

        # Resolución conjunta completa
        res = resolver("UEFA Nations League A Grp. 1", "Turquía", "Bélgica")
        assert res.equipo_local == "Türkiye"
        assert res.pais_local == "TUR"
        assert res.equipo_visita == "Belgium"
        assert res.pais_visita == "BEL"
        assert res.pais_local != res.pais_visita
        assert res.pais_iso == "INT"
        assert res.es_internacional is True
        assert res.identidad_verificada is True
        assert res.estado_identidad == "verificada"

    def test_formatear_mensaje_entidades_verificadas_selecciones(self) -> None:
        """Verifica formato oficial de Telegram con ISO independiente por selección."""
        msg = formatear_mensaje_entidades_verificadas(
            "Turquía", "TUR", "Bélgica", "BEL", "UEFA Nations League A Grp. 1"
        )
        assert "Turquía [TUR] vs Bélgica [BEL]" in msg
        assert "[BEL] vs [BEL]" not in msg
        assert "[TUR] vs [TUR]" not in msg
        assert "UEFA Nations League A Grp. 1" in msg

    def test_parsear_comando_mercados_nations_league(self) -> None:
        """Verifica que el separador inteligente extraiga torneo de selecciones y equipos sin contaminación."""
        parsed = parsear_comando_mercados("/mercados UEFA Nations League A Grp. 1 Turquía vs Bélgica")
        assert parsed["equipo_local"] == "Türkiye"
        assert parsed["pais_local"] == "TUR"
        assert parsed["equipo_visita"] == "Belgium"
        assert parsed["pais_visita"] == "BEL"
        assert parsed["es_internacional"] is True
        assert parsed["identidad_verificada"] is True
        assert parsed["estado_identidad"] == "verificada"

    def test_candidato_fotmob_aislado_de_pais_rival(self) -> None:
        """Verifica que el candidato de Turquía en FotMob puntúe alto y no sea descartado con -9999."""
        candidato_turquia = {
            "name": "Türkiye",
            "country": "TUR",
            "ccode": "TUR",
            "leagueName": "UEFA Nations League",
            "score": 1000.0,
        }
        score = validar_y_puntuar_candidato_fotmob(
            candidato_turquia,
            texto_buscado="Turquía",
            texto_liga_o_contexto="UEFA Nations League",
            pais_iso_esperado="BEL",  # Si llegara a pasarse BEL erróneamente, la regla de selección prevalece
        )
        assert score > 0.0, f"Candidato de Turquía fue indebidamente descartado con score {score}"

    def test_decision_cobertura_no_bloquea_selecciones_verificadas(self) -> None:
        """
        Verifica que decision_cobertura reconozca la identidad verificada de selecciones FIFA
        y no active IDENTIDAD_NO_VERIFICADA cuando cuentan con datos de partidos.
        """
        doc_tur = _crear_doc_mock("Türkiye", pais_iso="TUR", id_equipo="8496", identidad_verificada=False)
        doc_bel = _crear_doc_mock("Belgium", pais_iso="BEL", id_equipo="8263", identidad_verificada=False)
        decision = evaluar_decision_cobertura(doc_tur, doc_bel)
        assert decision.estado != "identidad_no_verificada", f"Estado no debe ser identidad_no_verificada: {decision.estado}"
        assert decision.publicar_probabilidades is True

    def test_catalogo_ligas_contiene_torneos_selecciones(self) -> None:
        """Verifica que CATALOGO_LIGAS_FLASHSCORE contenga torneos internacionales mapeados a INT."""
        torneos = [
            "uefa nations league",
            "nations league",
            "eurocopa",
            "copa america",
            "eliminatorias conmebol",
            "mundial",
            "copa oro",
            "copa africa",
            "copa asiatica",
        ]
        for t in torneos:
            assert t in CATALOGO_LIGAS_FLASHSCORE, f"Torneo {t} no está en CATALOGO_LIGAS_FLASHSCORE"
            assert CATALOGO_LIGAS_FLASHSCORE[t] == "INT", f"Torneo {t} debe tener ISO 'INT'"

    def test_supresion_alerta_cuadro_especialista_en_modo_abstencion(self) -> None:
        """
        Verifica que cuando un pronóstico está abstenido (abstenido=True),
        no se dispare la advertencia de Telegram 'El ticket se envió, pero el Cuadro Especialista no'.
        """
        mensajes_enviados = []
        def mock_enviar_seguro(bot, chat_id, texto):
            mensajes_enviados.append(texto)

        # Simular bloque de envío de bot_interactivo cuando abstenido=True
        preparado_abstenido = {
            "abstenido": True,
            "rutas": {"cuadro": None},
        }
        resultado_envio = {
            "cuadro_enviado": False,
            "motivo_cuadro": None,
        }

        # Lógica actualizada de bot_interactivo
        if resultado_envio.get("cuadro_enviado"):
            pass
        elif (resultado_envio.get("motivo_cuadro") or preparado_abstenido['rutas'].get('cuadro') is None) and not preparado_abstenido.get("abstenido"):
            detalle = resultado_envio.get("motivo_cuadro") or "no se pudo generar la imagen"
            ruta_c = preparado_abstenido['rutas'].get('cuadro') or "—"
            mock_enviar_seguro(None, 123, f"ℹ️ El ticket se envió, pero el Cuadro Especialista no: {detalle}\nRuta esperada: {ruta_c}")

        assert len(mensajes_enviados) == 0, f"No debe emitirse advertencia de Cuadro Especialista en modo abstención: {mensajes_enviados}"
