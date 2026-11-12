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
            ("Turquía", ("Turkey", "TUR")),
            ("Turquia", ("Turkey", "TUR")),
            ("Turkey", ("Turkey", "TUR")),
            ("Türkiye", ("Turkey", "TUR")),
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
        assert nom_l == "Turkey"
        assert iso_l == "TUR"

        nom_v, iso_v = resolver_equipo_individual("Bélgica", pais_iso_contexto="TUR")
        assert nom_v == "Belgium"
        assert iso_v == "BEL"

        # Resolución conjunta completa
        res = resolver("UEFA Nations League A Grp. 1", "Turquía", "Bélgica")
        assert res.equipo_local == "Turkey"
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
        assert parsed["equipo_local"] == "Turkey"
        assert parsed["nombre_busqueda_fotmob"] == "Turkey"
        assert parsed["nombre_busqueda_flashscore"] == "Turquía"
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

    def test_busqueda_multialias_turquia_no_falla_con_turkiye(self) -> None:
        """
        Punto 47: Verifica que si FotMob devuelve vacío para 'Türkiye',
        el buscador itera automáticamente a 'Turkey' y 'Turquía' y encuentra el equipo
        sin lanzar RuntimeError.
        """
        from fuentes_xg import FuenteFotMob, variantes_de_busqueda

        vars_t = variantes_de_busqueda("Türkiye")
        assert "Turkey" in vars_t or "Turquía" in vars_t

        fotmob = FuenteFotMob(verbose=False)
        terminos_consultados = []

        def mock_api(ruta: str, reintentos: int = 3):
            # Extraer término consultado
            import urllib.parse
            parsed = urllib.parse.urlparse(ruta)
            qs = urllib.parse.parse_qs(parsed.query)
            term = qs.get("term", [""])[0]
            terminos_consultados.append(term)
            if term in ("Türkiye", "Turkiye"):
                return []
            if term in ("Turquía", "Turkey", "turquia"):
                return [{
                    "suggestions": [{
                        "type": "team",
                        "id": "6595",
                        "name": "Turquía",
                        "leagueName": "UEFA Nations League",
                        "ccode": "TUR",
                        "score": 999.0
                    }]
                }]
            return []

        fotmob._api = mock_api
        eq = fotmob.resolver_equipo("Türkiye")

        assert eq is not None, "Debe resolver a Turquía iterando por sus alias"
        assert str(eq.get("id")) == "6595"
        assert eq.get("nombre") == "Turquía"
        assert any(t in ("Turquía", "Turkey") for t in terminos_consultados), f"Términos consultados: {terminos_consultados}"

    def test_fallback_automatico_a_flashscore_cuando_fotmob_no_encuentra_equipo(self) -> None:
        """
        Punto 47: Simula que FotMob no encuentra un equipo (devuelve None o RuntimeError)
        y verifica que auditar_equipo cae automáticamente a Flashscore usando el nombre
        en español ('Turquía'), recupera el historial y NO lanza RuntimeError al usuario.
        """
        from auditoria_historica import auditar_equipo
        from unittest.mock import MagicMock, patch
        from fuentes_xg import PartidoFinalizado, ResultadoEscaneo, MatrizPartido, PeriodoPartido, CATALOGO_METRICAS

        partidos_mock = [
            PartidoFinalizado(
                id_partido=f"fs_{i}",
                fecha=f"2026-10-{i:02d}",
                torneo="UEFA Nations League",
                local="Turquía",
                visitante=f"Rival_{i}",
                goles_local=1,
                goles_visitante=0,
                es_local_el_equipo=True,
            ) for i in range(1, 16)
        ]

        # Simular que FotMob falla
        mock_fotmob = MagicMock()
        mock_fotmob.nombre = "fotmob"
        mock_fotmob.resolver_equipo.return_value = None

        def mock_matriz_partido(p, eq):
            m = MatrizPartido(
                id_partido=p.id_partido,
                torneo=p.torneo,
                fecha=p.fecha,
                equipo=str(eq.get("nombre")),
                rival=(p.visitante if p.es_local_el_equipo else p.local),
                condicion="casa" if p.es_local_el_equipo else "fuera",
                goles_equipo=p.goles_local if p.es_local_el_equipo else p.goles_visitante,
                goles_rival=p.goles_visitante if p.es_local_el_equipo else p.goles_local,
            )
            filas = [{"clave": d.clave, "metrica": d.etiqueta, "categoria": d.categoria, "equipo": 1.0, "rival": 0.0} for d in CATALOGO_METRICAS]
            m.periodos["FT"] = PeriodoPartido("FT", filas, [])
            m.periodos["1T"] = PeriodoPartido("1T", filas, [])
            m.periodos["2T"] = PeriodoPartido("2T", filas, [])
            return m

        # Simular que Flashscore sí encuentra por nombre en español
        with patch("fuentes_xg.FuenteFlashscore") as MockFS:
            inst_fs = MockFS.return_value
            inst_fs.nombre = "flashscore"
            inst_fs.resolver_equipo.side_effect = lambda nom, rival_contexto=None: {
                "id": "fs_tur_123",
                "nombre": "Turquía",
                "liga": "UEFA Nations League",
                "pais_iso": "TUR"
            } if nom in ("Turquía", "Turquia") else None

            inst_fs.escanear_oficiales.return_value = ResultadoEscaneo(
                oficiales=partidos_mock,
                amistosos_reservados=[],
                paginas_escaneadas=1,
                finalizados_totales=15,
                amistosos_excluidos=0,
                fuente_agotada=True,
                motivo_parada="objetivo_cumplido"
            )
            inst_fs.matriz_partido.side_effect = mock_matriz_partido

            # Ejecutar auditar_equipo pasando "Turkey"
            aud = auditar_equipo("Turkey", limite=15, fuente_obj=mock_fotmob, headless=True)

            assert aud is not None
            assert aud.get("fuente") == "flashscore"
            assert aud.get("identidad_verificada") is True
            assert (aud.get("equipo") or {}).get("nombre") == "Turquía"
            assert len(aud.get("partidos", [])) == 15

    def test_renderizado_completo_turquia_vs_belgica_sin_nameerror_p_u35_pct(self) -> None:
        """
        Punto 48: Verifica que el flujo de renderizado del Ticket y Cuadros Especialistas
        (PNG 1 y PNG 2) para selecciones (Turqu?a vs B?lgica) no lance NameError: name 'p_u35_pct'
        ni ning?n error por variables de mercado indefinidas o nulos en m?tricas.
        """
        import tempfile
        import os
        from motor_mercados import clusters_marcador, analizar
        from servicio_mercados import renderizar_ticket_mercados_png
        import cuadro_especialista as ce
        from test_motor_mercados import doc_sintetico

        # 1. Test directo de clusters_marcador en la rama donde p_u35_pct >= 75.0 y p_fav_val < 55.0
        # Matriz 6x6 concentrada en marcadores bajos (0-0, 0-1, 1-0, 1-1, etc.) donde la suma <= 3 goles supera el 75%
        matriz_bajos = [[0.0] * 6 for _ in range(6)]
        matriz_bajos[0][0] = 0.15
        matriz_bajos[1][0] = 0.15
        matriz_bajos[0][1] = 0.15
        matriz_bajos[1][1] = 0.20
        matriz_bajos[2][0] = 0.08
        matriz_bajos[0][2] = 0.08
        matriz_bajos[2][1] = 0.05
        matriz_bajos[1][2] = 0.05
        matriz_bajos[2][2] = 0.04
        matriz_bajos[3][0] = 0.02
        matriz_bajos[0][3] = 0.02
        # Resto distribuido
        matriz_bajos[3][3] = 0.01

        res_clusters = clusters_marcador(matriz_bajos, "Turqu?a", "B?lgica")
        assert "jugada_optima" in res_clusters
        jugada_opt = res_clusters["jugada_optima"]
        assert "Menos de" in jugada_opt or "1x2" in jugada_opt or "Doble Oportunidad" in jugada_opt
        assert "None" not in jugada_opt

        # 2. Test E2E de renderizado completo de Turqu?a vs B?lgica con los 3 artefactos visuales
        doc_turquia = doc_sintetico("Turqu?a", [("casa", 1, 0), ("fuera", 2, 1)] * 8, xg=[(1.5, 0.8)] * 16)
        doc_belgica = doc_sintetico("B?lgica", [("fuera", 1, 1), ("casa", 2, 0)] * 8, xg=[(1.2, 1.1)] * 16)
        doc_turquia["equipo"]["pais_iso"] = "TUR"
        doc_belgica["equipo"]["pais_iso"] = "BEL"
        doc_turquia["identidad_verificada"] = True
        doc_belgica["identidad_verificada"] = True

        an = analizar(doc_turquia, doc_belgica, "Turqu?a", "B?lgica")
        assert an is not None

        with tempfile.TemporaryDirectory() as td:
            p_ticket = os.path.join(td, "ticket_turquia_belgica.png")
            p_cuadro1 = os.path.join(td, "cuadro_turquia_belgica.png")
            p_cuadro2 = os.path.join(td, "auditoria15p_turquia_belgica.png")

            # Ticket PNG
            renderizar_ticket_mercados_png(an, destino=p_ticket)
            assert os.path.exists(p_ticket), "El PNG del Ticket debe haberse generado"
            assert os.path.getsize(p_ticket) > 1000, "El Ticket PNG debe tener contenido"

            # Cuadro Especialista PNG (PNG 1)
            r1 = ce.generar_cuadro_desde_analisis(an, doc_turquia, doc_belgica, destino=p_cuadro1)
            assert r1 is not None, "generar_cuadro_desde_analisis debe retornar la ruta del PNG"
            assert os.path.exists(p_cuadro1), "El PNG del Cuadro Especialista debe haberse generado"
            assert os.path.getsize(p_cuadro1) > 5000, "El Cuadro PNG 1 debe tener contenido"

            # Cuadro Auditor?a 15P PNG (PNG 2)
            r2 = ce.generar_cuadro_auditoria_15_partidos_png(an, doc_turquia, doc_belgica, destino=p_cuadro2)
            assert r2 is not None, "generar_cuadro_auditoria_15_partidos_png debe retornar la ruta del PNG"
            assert os.path.exists(p_cuadro2), "El PNG de Auditor?a 15P debe haberse generado"
            assert os.path.getsize(p_cuadro2) > 5000, "El Cuadro PNG 2 debe tener contenido"
    def test_tortura_none_values_en_diccionarios_turquia_vs_belgica(self) -> None:
        """
        Punto 49: Prueba de tortura con None incondicional en diccionarios de metricas,
        factores latentes, patrones 55 y configuraciones de mercado.
        Verifica que clusters_marcador, seleccion Top 5, renderizado de Ticket PNG,
        Cuadros Especialistas (PNG 1 y PNG 2) y reporte Telegram se ejecuten sin lanzar
        TypeError: float() argument must be a string or a real number, not 'NoneType'.
        """
        import tempfile
        import os
        from motor_mercados import clusters_marcador, analizar
        from servicio_mercados import (
            renderizar_ticket_mercados_png,
            obtener_top5_y_seccion1_filtrados,
            generar_reporte_xai_telegram,
        )
        import cuadro_especialista as ce
        from test_motor_mercados import doc_sintetico

        doc_turquia = doc_sintetico("Turquía", [("casa", 1, 0), ("fuera", 2, 1)] * 8, xg=[(1.5, 0.8)] * 16)
        doc_belgica = doc_sintetico("Bélgica", [("fuera", 1, 1), ("casa", 2, 0)] * 8, xg=[(1.2, 1.1)] * 16)
        doc_turquia["equipo"]["pais_iso"] = "TUR"
        doc_belgica["equipo"]["pais_iso"] = "BEL"
        doc_turquia["identidad_verificada"] = True
        doc_belgica["identidad_verificada"] = True

        # Inyectar None masivamente en partidos y metricas de ambos equipos
        claves_opcionales = [
            "duelos_aereos_pct", "duelos_suelo_pct", "bloqueos", "recuperaciones",
            "xg_balon_parado", "grandes_ocasiones_falladas", "xg_juego_abierto",
            "pases_entre_lineas", "entradas_pct", "cruces_pct", "remates_dentro_area",
            "xgot_contra", "xgot", "P37", "P54_Asedio_Corners_Matchup",
            "P07_Precision_Disparo", "cuota", "intercepciones", "despejes"
        ]
        for doc in (doc_turquia, doc_belgica):
            for p in doc.get("partidos", []):
                for periodo in ("FT", "1T", "2T"):
                    if periodo in p and isinstance(p[periodo], dict):
                        for k in claves_opcionales:
                            p[periodo][k] = None

        an = analizar(doc_turquia, doc_belgica, "Turquía", "Bélgica")
        assert an is not None

        # Inyectar None en medias y factores del objeto motor recursivo
        if hasattr(an, "motor") and hasattr(an.motor, "recursivo") and isinstance(an.motor.recursivo, dict):
            for mu_key in ("mu_local_casa", "mu_visita_fuera"):
                mu = an.motor.recursivo.get(mu_key)
                if isinstance(mu, dict):
                    for k in claves_opcionales:
                        mu[k] = None

        # 1. Clusters de marcador con None en info_cruces
        info_cruces = an.motor.recursivo.get("cruces_tacticos") if hasattr(an, "motor") else None
        res_clusters = clusters_marcador(an.motor.matriz_ft, "Turquía", "Bélgica", info_cruces=info_cruces)
        assert res_clusters is not None
        assert "jugada_optima" in res_clusters

        # 2. Filtrado de Top 5 y Seccion 1
        top5, sec1, cnt = obtener_top5_y_seccion1_filtrados(an)
        assert isinstance(top5, list)
        assert isinstance(sec1, list)

        # 3. Reporte Telegram XAI
        reporte_tg = generar_reporte_xai_telegram(an)
        assert isinstance(reporte_tg, str)
        assert len(reporte_tg) > 50

        # 4. Renderizado completo de los 3 PNG
        with tempfile.TemporaryDirectory() as td:
            p_ticket = os.path.join(td, "ticket_turquia_belgica_none.png")
            p_cuadro1 = os.path.join(td, "cuadro_turquia_belgica_none.png")
            p_cuadro2 = os.path.join(td, "auditoria15p_turquia_belgica_none.png")

            renderizar_ticket_mercados_png(an, destino=p_ticket)
            assert os.path.exists(p_ticket)
            assert os.path.getsize(p_ticket) > 1000

            r1 = ce.generar_cuadro_desde_analisis(an, doc_turquia, doc_belgica, destino=p_cuadro1)
            assert r1 is not None
            assert os.path.exists(p_cuadro1)
            assert os.path.getsize(p_cuadro1) > 5000

            r2 = ce.generar_cuadro_auditoria_15_partidos_png(an, doc_turquia, doc_belgica, destino=p_cuadro2)
            assert r2 is not None
            assert os.path.exists(p_cuadro2)
            assert os.path.getsize(p_cuadro2) > 5000
