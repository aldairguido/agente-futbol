"""Tests de verificación Ronda 19 (fix/ronda-18-tickets)."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import evaluacion_pronosticos as ep
import motor_mercados as mm
import servicio_mercados as sm


_CLAVES_FT = (
    ("xg", 1.35, 0.95),
    ("posesion_pct", 54.0, 46.0),
    ("remates_totales", 13.0, 9.0),
    ("remates_puerta", 5.0, 3.0),
    ("corners", 5.0, 4.0),
    ("faltas", 12.0, 11.0),
    ("tarjetas_amarillas", 2.0, 2.0),
    ("xgot", 1.25, 0.85),
    ("duelos_ganados", 49.0, 44.0),
    ("intercepciones", 9.0, 8.0),
    ("despejes", 17.0, 20.0),
    ("saques_puerta", 7.0, 9.0),
)


def _fila_partido_completo(
    idx: int,
    *,
    fuente: str,
    gf1: int = 1,
    gf2: int = 0,
    gc1: int = 0,
    gc2: int = 1,
    con_minutos: bool = False,
) -> dict:
    filas_ft = [{"clave": k, "equipo": eq, "rival": riv} for k, eq, riv in _CLAVES_FT]
    filas_1t = [
        {"clave": "xg", "equipo": 0.65, "rival": 0.45},
        {"clave": "posesion_pct", "equipo": 53.0, "rival": 47.0},
        {"clave": "corners", "equipo": 2.0, "rival": 2.0},
        {"clave": "faltas", "equipo": 6.0, "rival": 5.0},
        {"clave": "tarjetas_amarillas", "equipo": 1.0, "rival": 1.0},
        {"clave": "xgot_enfrentado", "equipo": float(gc1), "rival": float(gf1)},
        {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
    ]
    filas_2t = [
        {"clave": "xg", "equipo": 0.70, "rival": 0.50},
        {"clave": "posesion_pct", "equipo": 55.0, "rival": 45.0},
        {"clave": "corners", "equipo": 3.0, "rival": 2.0},
        {"clave": "faltas", "equipo": 6.0, "rival": 6.0},
        {"clave": "tarjetas_amarillas", "equipo": 1.0, "rival": 1.0},
        {"clave": "xgot_enfrentado", "equipo": float(gc2), "rival": float(gf2)},
        {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
    ]
    d = {
        "id_partido": f"{fuente}_{idx}",
        "fecha": f"2026-10-{min(28, 1 + idx):02d}",
        "torneo": "BRAZIL: Serie A" if fuente == "flashscore" else "Série A",
        "rival": f"Rival_{idx}",
        "condicion": "casa" if idx % 2 == 0 else "fuera",
        "marcador": {"equipo": gf1 + gf2, "rival": gc1 + gc2},
        "goles_equipo": gf1 + gf2,
        "goles_rival": gc1 + gc2,
        "resumen_claves": {
            "FT": {
                "equipo": [f["clave"] for f in filas_ft],
                "rival": [f["clave"] for f in filas_ft],
            },
            "1T": {
                "equipo": [f["clave"] for f in filas_1t],
                "rival": [f["clave"] for f in filas_1t],
            },
            "2T": {
                "equipo": [f["clave"] for f in filas_2t],
                "rival": [f["clave"] for f in filas_2t],
            },
        },
        "periodos": {
            "FT": {"filas": filas_ft},
            "1T": {"filas": filas_1t},
            "2T": {"filas": filas_2t},
        },
    }
    if con_minutos:
        d["eventos_gol"] = [{"minuto": 24 if gf1 else 70, "minuto_str": "24'", "lado": "equipo"}]
    return d


def _doc_equipo_sintetico(nombre: str, *, fuente: str, con_minutos: bool = False) -> dict:
    return {
        "equipo": {"id": f"id_{nombre}", "nombre": nombre, "pais_iso": "BRA"},
        "fuente": fuente,
        "pais_iso": "BRA",
        "competicion_objetivo": "Serie A",
        "generado_en": f"2026-11-18T05:00:00Z_{fuente}",
        "partidos": [
            _fila_partido_completo(i, fuente=fuente, con_minutos=con_minutos)
            for i in range(15)
        ],
    }


def test_bloque_1_fuente_por_equipo_y_firma_datos_en_snapshot_y_ledger() -> None:
    dl = _doc_equipo_sintetico("Corinthians", fuente="flashscore", con_minutos=False)
    dv = _doc_equipo_sintetico("Botafogo", fuente="fotmob", con_minutos=True)

    res_fake = {
        "equipo_local": "Corinthians",
        "equipo_visitante": "Botafogo",
        "doc_local": dl,
        "doc_visitante": dv,
        "meta_local": None,
        "meta_visitante": None,
        "contexto": {
            "fecha_partido": "2026-11-20",
            "hora_inicio_partido": "2026-11-20T22:00:00Z",
            "fixture_confirmado": True,
            "fuente_fecha": "fotmob_fixture",
            "competicion": "Série A (Brasil)",
            "pais_iso": "BRA",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
            "origen_condicion": "fixture_real",
            "sede": "Neo Química Arena",
            "id_partido_objetivo": "5109999",
        },
        "liga_solicitada": {"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
        "estado_identidad": "verificada",
        "motivo_identidad": "verificada en test",
        "identidad_verificada": True,
        "criterio_desempate": None,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        ruta_ledger = os.path.join(tmpdir, "ledger_oos.jsonl")
        ruta_snaps = os.path.join(tmpdir, "snapshots")

        with patch.object(sm, "resolver_enfrentamiento", return_value=res_fake):
            prep = sm.preparar_analisis(
                "Corinthians",
                "Botafogo",
                umbral=0.70,
                directorio_salida=tmpdir,
                liga={"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
            )

        assert not prep.get("abstenido")
        an = prep["analisis"]
        meta = an.metadatos
        assert meta.get("fuente_local") == "flashscore"
        assert meta.get("fuente_visitante") == "fotmob"
        firma_esp = sm.calcular_firma_datos(dl, dv)
        assert isinstance(firma_esp, str) and len(firma_esp) == 12
        assert meta.get("firma_datos") == firma_esp

        ep.adjuntar_modo_sombra_preparado(prep)
        tg_meta = {
            "message_id": 101,
            "message_date": 1795066740,
            "message_date_utc": "2026-11-18T05:39:00Z",
        }
        prep["telegram_meta"] = tg_meta
        snap = ep.snapshot_desde_analisis(
            prep,
            publicado_en="2026-11-18T05:39:00Z",
            modo_sombra=prep.get("modo_sombra"),
            telegram_meta=tg_meta,
        )
        snap_dict = snap.a_dict()
        assert snap_dict["fuente_local"] == "flashscore"
        assert snap_dict["fuente_visitante"] == "fotmob"
        assert snap_dict["firma_datos"] == firma_esp
        assert snap_dict["datos_corte"]["fuente_local"] == "flashscore"
        assert snap_dict["datos_corte"]["fuente_visitante"] == "fotmob"
        assert snap_dict["datos_corte"]["firma_datos"] == firma_esp

        ruta_snap = ep.guardar_snapshot(snap, directorio=ruta_snaps)
        with open(ruta_snap, "r", encoding="utf-8") as fh:
            guardado = json.load(fh)
        assert guardado["fuente_local"] == "flashscore"
        assert guardado["fuente_visitante"] == "fotmob"
        assert guardado["firma_datos"] == firma_esp

        res_pre = ep.registrar_sombra_prepartido_en_ledger_oos(
            prep,
            ruta_snapshot=ruta_snap,
            ruta_ledger=ruta_ledger,
        )
        assert res_pre["registrado"] is True

        # Caso directo con fuente en datos_corte vía registrar_en_ledger_oos
        res_dir = ep.registrar_en_ledger_oos(
            {
                "id_trabajo": "directo_b1",
                "id_partido": "9990001",
                "partido": "Club A vs Club B",
                "fecha": "2026-11-21",
                "estado_registro": "liquidado",
                "datos_corte": {
                    "fuente_local": "fotmob",
                    "fuente_visitante": "flashscore",
                    "firma_datos": "abcdef012345",
                },
                "lineas": [],
                "resumen": {"aciertos": 0, "fallos": 0, "n_evaluables": 0, "brier": None},
            },
            ruta_ledger=ruta_ledger,
        )
        assert res_dir["registrado"] is True

        # Caso liquidado desde snapshot en directorio
        res_obj = ep.ResultadoPartido(
            id_partido="5109999",
            estado="finalizado",
            local="Corinthians",
            visitante="Botafogo",
            inicio_utc="2026-11-20T22:00:00Z",
            goles_local=1,
            goles_visitante=1,
            goles_local_1t=1,
            goles_visitante_1t=0,
            corners_local={"FT": 5},
            corners_visitante={"FT": 4},
            amarillas_local={"FT": 2},
            amarillas_visitante={"FT": 2},
        )
        res_liq = ep.liquidar_sombras_en_ledger_oos(
            [guardado],
            {"Corinthians vs Botafogo": res_obj},
            ruta_ledger=ruta_ledger,
        )
        assert res_liq["n_liquidados_nuevos"] == 1, res_liq

        filas = ep.cargar_ledger_oos(ruta_ledger)
        assert len(filas) == 3
        assert filas[0]["fuente_local"] == "flashscore"
        assert filas[0]["fuente_visitante"] == "fotmob"
        assert filas[0]["firma_datos"] == firma_esp
        assert filas[1]["fuente_local"] == "fotmob"
        assert filas[1]["fuente_visitante"] == "flashscore"
        assert filas[1]["firma_datos"] == "abcdef012345"
        assert filas[2]["estado_registro"] == "liquidado"
        assert filas[2]["fuente_local"] == "flashscore"
        assert filas[2]["fuente_visitante"] == "fotmob"
        assert filas[2]["firma_datos"] == firma_esp

        verif = ep.verificar_cadena_ledger_oos(ruta_ledger)
        assert verif["ok"] is True, verif

        # Caso abstenido también conserva fuente_local, fuente_visitante y firma_datos
        res_abs = dict(res_fake)
        res_abs["doc_visitante"] = _doc_equipo_sintetico("Botafogo", fuente="fotmob", con_minutos=True)
        for p in res_abs["doc_visitante"]["partidos"]:
            p["periodos"] = {"FT": {"filas": [{"clave": "remates_fuera", "equipo": 1.0, "rival": 1.0}]}}
            p["resumen_claves"] = {"FT": {"equipo": ["remates_fuera"], "rival": ["remates_fuera"]}}
        with patch.object(sm, "resolver_enfrentamiento", return_value=res_abs):
            prep_abs = sm.preparar_analisis(
                "Corinthians",
                "Botafogo",
                umbral=0.70,
                directorio_salida=tmpdir,
                liga={"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
            )
        assert prep_abs.get("abstenido") is True
        snap_abs = ep.snapshot_desde_analisis(prep_abs).a_dict()
        assert snap_abs["fuente_local"] == "flashscore"
        assert snap_abs["fuente_visitante"] == "fotmob"
        assert snap_abs["firma_datos"] == sm.calcular_firma_datos(dl, res_abs["doc_visitante"])


def test_bloque_3_seccion2_siempre_visible_y_redaccion_unificada_sensible() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from PIL import ImageDraw
    import motor_inferencia as mi

    # Caso tipo ce5dbea6: 1X es sensible >= 70% pero tiene desvío > 10 pts (por lo que filtrar_top_opciones_ticket lo omite)
    s_mantiene = SimpleNamespace(
        id_mercado="goles_totales",
        mercado="Total de goles",
        evento="under_3.5",
        etiqueta="Menos de 3.5",
        periodo="FT",
        equipo=None,
        linea=3.5,
        probabilidad=0.88,
        estado="mantiene",
        motivo="",
    )
    s_1x_sensible = SimpleNamespace(
        id_mercado="doble_oportunidad",
        mercado="Doble oportunidad",
        evento="1x",
        etiqueta="1X",
        periodo="FT",
        equipo=None,
        linea=None,
        probabilidad=0.714,
        estado="sensible",
        motivo="sin calibración validada (desvío > 10.0)",
    )
    # Matriz con P(1)+P(X) = 0.714
    mat = [[0.0] * 4 for _ in range(4)]
    mat[1][0] = 0.422
    mat[0][0] = 0.292
    mat[0][1] = 0.286

    dl_bad = _doc_equipo_sintetico("Corinthians", fuente="flashscore")
    dv_bad = _doc_equipo_sintetico("Botafogo", fuente="fotmob")
    # Forzar ~86.7% empírico en under_3.5 (desvío 1.3 pts <= 10.0, sigue mantiene)
    # y ~0% en 1X para Corinthians (desvío > 10.0, replicando el filtro que ocultaba 1X en ce5dbea6)
    for idx_m, p in enumerate(dl_bad["partidos"]):
        if idx_m < 13:
            p["marcador"] = {"equipo": 0, "rival": 2}
            p["goles_equipo"], p["goles_rival"] = 0, 2
        else:
            p["marcador"] = {"equipo": 0, "rival": 4}
            p["goles_equipo"], p["goles_rival"] = 0, 4
    for idx_m, p in enumerate(dv_bad["partidos"]):
        if idx_m < 13:
            p["marcador"] = {"equipo": 2, "rival": 0}
            p["goles_equipo"], p["goles_rival"] = 2, 0
        else:
            p["marcador"] = {"equipo": 4, "rival": 0}
            p["goles_equipo"], p["goles_rival"] = 4, 0

    motor_mock = SimpleNamespace(
        matriz_ft=mat,
        doc_local=dl_bad,
        doc_visitante=dv_bad,
        tasas=SimpleNamespace(lambda_local_ft=1.15, lambda_visitante_ft=0.90, lambda_local_1t=0.5, lambda_visitante_1t=0.4, lambda_local_2t=0.65, lambda_visitante_2t=0.5),
        muestras={},
        recursivo={"aplicado": True, "iteracion_convergencia": 3},
        _grupos_resumen={"mantiene": [s_mantiene], "sensible": [s_1x_sensible]},
    )
    analisis_fake = SimpleNamespace(
        metadatos={
            "equipo_local": "Corinthians",
            "equipo_visitante": "Botafogo",
            "competicion": "Série A (Brasil)",
            "fecha_partido": "2026-11-20",
            "fuente_local": "flashscore",
            "fuente_visitante": "fotmob",
        },
        parametros=SimpleNamespace(umbral=0.70),
        motor=motor_mock,
        selecciones_filtradas=[s_mantiene, s_1x_sensible],
        grupos={"mantiene": [s_mantiene], "sensible": [s_1x_sensible], "incompleta": []},
    )

    res_u = sm.calcular_seccion1_unificada(analisis_fake)
    evs_m = {getattr(s, "evento", None) for s in res_u["mantiene_raw"]}
    evs_s = {getattr(s, "evento", None) for s in res_u["sensibles_raw"]}
    assert evs_m == {"under_3.5"}
    assert evs_s == {"1x"}
    assert res_u["total_validadas"] == 1

    # Verificar que renderizar_ticket_mercados_png dibuja Sección 2 sin usar "VALIDADAS" en Sección 2
    draw_texts: list[str] = []
    orig_draw_fn = ImageDraw.Draw

    def _cap_draw(im, *args, **kwargs):
        d = orig_draw_fn(im, *args, **kwargs)
        orig_txt = d.text

        def _cap_text(xy, text, *a, **kw):
            draw_texts.append(str(text))
            return orig_txt(xy, text, *a, **kw)

        d.text = _cap_text
        return d

    with tempfile.TemporaryDirectory() as tmpdir:
        out_png = os.path.join(tmpdir, "ticket_b3.png")
        with patch("PIL.ImageDraw.Draw", side_effect=_cap_draw):
            sm.renderizar_ticket_mercados_png(analisis_fake, destino=out_png)
        assert os.path.exists(out_png)

    assert any("SECCIÓN 2: SENSIBLES A LOS SUPUESTOS (>= 70%)" in t for t in draw_texts), draw_texts
    assert "LÍNEAS Y PROBABILIDADES VALIDADAS" in draw_texts
    assert "LÍNEAS Y PROBABILIDADES SENSIBLES" in draw_texts
    assert "1 opciones validadas" in draw_texts
    assert "1 opciones sensibles" in draw_texts

    # Verificar redacción unificada en Predicción Final (Telegram XAI) y Veredicto 4 Conclusiones
    cb_ok = {"sin_derrotas": 9, "victorias": 5, "n_partidos": 15}
    cc_none = {"sin_dato": True, "_meta": {"n_partidos_con_eventos": 0}}
    pat_vac = {"posesion_pct": 52.0, "faltas": 12.0, "corners": 5.0, "P26_Poder_Cierre_2T": 0.55, "_muestra_etiqueta": "Doble ADN pond."}
    txt_xai = mi.generar_reporte_xai_telegram(
        "Corinthians",
        "Botafogo",
        pat_vac,
        pat_vac,
        {"local": 0.422, "empate": 0.292, "visita": 0.286},
        [],
        cb_ok,
        cb_ok,
        cc_none,
        cc_none,
        top5_podio=[
            {"etiqueta": "Doble Oportunidad 1X: Corinthians o Empate", "evento": "1x", "probabilidad": 0.714, "estado": "sensible"},
            {"etiqueta": "Tarjetas Totales (FT): -6.5 Tarj.", "evento": "amarillas_under_6.5", "probabilidad": 0.739, "estado": "sensible"},
            {"etiqueta": "Goles 2T: +0.5 Goles", "evento": "gol_2t", "probabilidad": 0.720, "estado": "sensible"},
        ],
        selecciones_sec1=res_u["mantiene_raw"],
        selecciones_sec2=res_u["sensibles_raw"],
        motor=motor_mock,
    )
    assert "Predicción Final:* Inclinación estadística (sensible): Corinthians gana o empata (Doble Oportunidad 1X) (71.4%) — ver Sección 2" in txt_xai
    assert "Inclinación estadística (sensible): Doble Oportunidad 1X: Corinthians o Empate (71.4%) — ver Sección 2" in txt_xai
    assert "Inclinación estadística (sensible): Tarjetas Totales (FT): -6.5 Tarj. (73.9%) — ver Sección 2" in txt_xai
    assert "Inclinación estadística (sensible): Goles 2T: +0.5 Goles (72.0%) — ver Sección 2" in txt_xai
    assert "El agente valida" not in txt_xai
    assert "El modelo valida" not in txt_xai

    # Con estado='mantiene' y cronología completa, conserva redacción de selección validada
    cc_ok = {"sin_dato": False, "_meta": {"n_partidos_con_eventos": 15}, "anotados": {"76'-90'": 3}, "recibidos": {"76'-90'": 2}}
    txt_xai_ok = mi.generar_reporte_xai_telegram(
        "Corinthians",
        "Botafogo",
        pat_vac,
        pat_vac,
        {"local": 0.422, "empate": 0.292, "visita": 0.286},
        [],
        cb_ok,
        cb_ok,
        cc_ok,
        cc_ok,
        top5_podio=[
            {"etiqueta": "Doble Oportunidad 1X: Corinthians o Empate", "evento": "1x", "probabilidad": 0.714, "estado": "mantiene"},
            {"etiqueta": "Tarjetas Totales (FT): -6.5 Tarj.", "evento": "amarillas_under_6.5", "probabilidad": 0.739, "estado": "mantiene"},
            {"etiqueta": "Goles 2T: +0.5 Goles", "evento": "gol_2t", "probabilidad": 0.720, "estado": "mantiene"},
        ],
        selecciones_sec1=[s_1x_sensible],
    )
    assert "Probabilidad Ajustada: 71.4%" in txt_xai_ok
    assert "El agente valida 'Tarjetas Totales (FT): -6.5 Tarj.'" in txt_xai_ok
    assert "El modelo valida 'Goles 2T: +0.5 Goles'" in txt_xai_ok


def test_bloque_4_serie_ab_sin_pais_pide_brasil_o_italia_y_fuentes_en_encabezados() -> None:
    from types import SimpleNamespace
    from PIL import ImageDraw
    import fuentes_xg as fx
    import bot_interactivo as bi
    import cuadro_especialista as ce

    # 1. Ambigüedad "serie a/b" sin país vs con país
    assert fx.detectar_liga_ambigua_serie_ab("/mercados serie A Corinthians vs Botafogo") == "Serie A"
    assert fx.detectar_liga_ambigua_serie_ab("/mercados serie B Remo vs Chapecoense") == "Serie B"
    assert fx.detectar_liga_ambigua_serie_ab("Remo vs Chapecoense (serie b)") == "Serie B"
    assert fx.detectar_liga_ambigua_serie_ab("/mercados serie A Brasil Corinthians vs Botafogo") is None
    assert fx.detectar_liga_ambigua_serie_ab("/mercados serie A Italia Inter vs Milan") is None
    assert fx.detectar_liga_ambigua_serie_ab("/mercados serie b brasil Remo vs Chapecoense") is None

    # 2. cmd_mercados con "serie A" sin país responde en texto plano (sin botones, sin inferir, sin analizar)
    enviados: list[tuple[str, dict]] = []

    def _fake_enviar(bot_obj, chat_o_msg, texto, **kwargs):
        enviados.append((str(texto), dict(kwargs)))

    for cmd_amb in (
        "/mercados serie A Corinthians vs Botafogo",
        "/mercados serie B Remo vs Chapecoense",
    ):
        enviados.clear()
        msg = SimpleNamespace(chat=SimpleNamespace(id="99999"), text=cmd_amb)
        with patch.object(bi, "CHAT_ID_AUTORIZADO", "99999"), \
             patch.object(bi, "enviar_seguro", side_effect=_fake_enviar), \
             patch.object(bi, "_lanzar_mercados") as m_lanzar:
            bi.cmd_mercados(msg)
        assert not m_lanzar.called
        assert len(enviados) == 1
        txt_rep, kw_rep = enviados[0]
        assert "Brasil" in txt_rep and "Italia" in txt_rep
        assert "reply_markup" not in kw_rep

    # Con país explícito sí ejecuta _lanzar_mercados
    enviados.clear()
    msg_bra = SimpleNamespace(chat=SimpleNamespace(id="99999"), text="/mercados serie A Brasil Corinthians vs Botafogo")
    with patch.object(bi, "CHAT_ID_AUTORIZADO", "99999"), \
         patch.object(bi, "enviar_seguro", side_effect=_fake_enviar), \
         patch.object(bi, "_lanzar_mercados") as m_lanzar_ok, \
         patch.object(bi, "_extraer_telegram_meta_mensaje", return_value={}):
        bi.cmd_mercados(msg_bra)
    assert m_lanzar_ok.called
    assert len(enviados) == 0

    # 3. Encabezados muestran la fuente de cada club (Telegram, Ticket PNG, Súper-Cuadro, Auditoría 15P)
    dl = _doc_equipo_sintetico("Corinthians", fuente="flashscore", con_minutos=False)
    dv = _doc_equipo_sintetico("Botafogo", fuente="fotmob", con_minutos=True)
    res_fake = {
        "equipo_local": "Corinthians",
        "equipo_visitante": "Botafogo",
        "doc_local": dl,
        "doc_visitante": dv,
        "meta_local": None,
        "meta_visitante": None,
        "contexto": {
            "fecha_partido": "2026-11-20",
            "hora_inicio_partido": "2026-11-20T22:00:00Z",
            "fixture_confirmado": True,
            "fuente_fecha": "fotmob_fixture",
            "competicion": "Série A (Brasil)",
            "pais_iso": "BRA",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
            "origen_condicion": "fixture_real",
            "sede": "Neo Química Arena",
            "id_partido_objetivo": "5109998",
        },
        "liga_solicitada": {"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
        "estado_identidad": "verificada",
        "motivo_identidad": "test",
        "identidad_verificada": True,
        "criterio_desempate": None,
    }
    draw_texts: list[str] = []
    orig_draw_fn = ImageDraw.Draw

    def _cap_draw(im, *args, **kwargs):
        d = orig_draw_fn(im, *args, **kwargs)
        orig_txt = d.text

        def _cap_text(xy, text, *a, **kw):
            draw_texts.append(str(text))
            return orig_txt(xy, text, *a, **kw)

        d.text = _cap_text
        return d

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(sm, "resolver_enfrentamiento", return_value=res_fake), \
             patch("PIL.ImageDraw.Draw", side_effect=_cap_draw):
            prep = sm.preparar_analisis(
                "Corinthians",
                "Botafogo",
                umbral=0.70,
                directorio_salida=tmpdir,
                liga={"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
            )
        msg_tel = prep["analisis"].mensaje
        esp_fuentes = "Fuentes: Corinthians: flashscore · Botafogo: fotmob"
        assert esp_fuentes in msg_tel
        assert any(esp_fuentes in t for t in draw_texts), draw_texts

        # Verificar Súper-Cuadro PNG y Auditoría 15P PNG (textos fig.text capturados vía matplotlib Figure.text)
        fig_texts: list[str] = []
        import matplotlib.figure as mfig
        orig_fig_text = mfig.Figure.text

        def _cap_fig_text(self_fig, x, y, s, *args, **kwargs):
            fig_texts.append(str(s))
            return orig_fig_text(self_fig, x, y, s, *args, **kwargs)

        with patch.object(mfig.Figure, "text", new=_cap_fig_text):
            ruta_sc = os.path.join(tmpdir, "super_cuadro_b4.png")
            ce.generar_cuadro_desde_analisis(prep["analisis"], destino=ruta_sc)
            ruta_aud = os.path.join(tmpdir, "auditoria_15p_b4.png")
            ce.generar_cuadro_auditoria_15_partidos_png(prep["analisis"], destino=ruta_aud)

        assert os.path.exists(ruta_sc) and os.path.exists(ruta_aud)
        ocurs = [t for t in fig_texts if esp_fuentes in t]
        assert len(ocurs) >= 2, fig_texts

    # Ticket abstenido también muestra las fuentes por club en encabezado
    draw_abs: list[str] = []

    def _cap_draw_abs(im, *args, **kwargs):
        d = orig_draw_fn(im, *args, **kwargs)
        orig_txt = d.text

        def _cap_text(xy, text, *a, **kw):
            draw_abs.append(str(text))
            return orig_txt(xy, text, *a, **kw)

        d.text = _cap_text
        return d

    an_abs = SimpleNamespace(
        metadatos={
            "equipo_local": "Corinthians",
            "equipo_visitante": "Botafogo",
            "competicion": "Serie A",
            "fecha_partido": "2026-11-20",
            "abstenido": True,
            "estado_cobertura": "cobertura_insuficiente",
            "fuente_local": "flashscore",
            "fuente_visitante": "fotmob",
        },
        abstenido=True,
        motor=SimpleNamespace(doc_local=dl, doc_visitante=dv),
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        out_abs = os.path.join(tmpdir, "ticket_abs_b4.png")
        with patch("PIL.ImageDraw.Draw", side_effect=_cap_draw_abs):
            sm.renderizar_ticket_mercados_png(an_abs, destino=out_abs)
        assert any("Fuentes: Corinthians: flashscore · Botafogo: fotmob" in t for t in draw_abs), draw_abs


def test_bloque_5_lambdas_plano_en_snapshot_y_ledger_sin_alterar_ticket_ni_banderas() -> None:
    from decision_cobertura import bandera_activa

    assert bandera_activa("usar_tercer_brazo_plano_sombra") is False

    dl = _doc_equipo_sintetico("Corinthians", fuente="flashscore", con_minutos=True)
    dv = _doc_equipo_sintetico("Botafogo", fuente="fotmob", con_minutos=True)
    res_fake = {
        "equipo_local": "Corinthians",
        "equipo_visitante": "Botafogo",
        "doc_local": dl,
        "doc_visitante": dv,
        "meta_local": None,
        "meta_visitante": None,
        "contexto": {
            "fecha_partido": "2026-11-22",
            "hora_inicio_partido": "2026-11-22T22:00:00Z",
            "fixture_confirmado": True,
            "fuente_fecha": "fotmob_fixture",
            "competicion": "Série A (Brasil)",
            "pais_iso": "BRA",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
            "origen_condicion": "fixture_real",
            "sede": "Neo Química Arena",
            "id_partido_objetivo": "5109977",
        },
        "liga_solicitada": {"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
        "estado_identidad": "verificada",
        "motivo_identidad": "test",
        "identidad_verificada": True,
        "criterio_desempate": None,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        ruta_ledger = os.path.join(tmpdir, "ledger_b5.jsonl")
        ruta_snaps = os.path.join(tmpdir, "snaps_b5")
        with patch.object(sm, "resolver_enfrentamiento", return_value=res_fake):
            prep = sm.preparar_analisis(
                "Corinthians",
                "Botafogo",
                umbral=0.70,
                directorio_salida=tmpdir,
                liga={"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
            )
        assert not prep.get("abstenido")
        an = prep["analisis"]
        lam_ft_antes = (an.motor.tasas.lambda_local_ft, an.motor.tasas.lambda_visitante_ft)

        # La bandera sigue apagada tras el análisis y las selecciones/lambdas no se alteran
        assert bandera_activa("usar_tercer_brazo_plano_sombra") is False
        lp = ep.extraer_lambdas_plano_desde_analisis(an, prep)
        assert isinstance(lp, dict)
        for k in ("local_ft", "visitante_ft", "local_1t", "visitante_1t", "local_2t", "visitante_2t"):
            assert k in lp and lp[k] is not None
        assert (an.motor.tasas.lambda_local_ft, an.motor.tasas.lambda_visitante_ft) == lam_ft_antes

        ep.adjuntar_modo_sombra_preparado(prep)
        assert "lambdas_plano" not in prep["modo_sombra"]

        tg_meta = {"message_id": 505, "message_date": 1795066740, "message_date_utc": "2026-11-18T05:39:00Z"}
        prep["telegram_meta"] = tg_meta
        snap = ep.snapshot_desde_analisis(
            prep,
            publicado_en="2026-11-18T05:39:00Z",
            modo_sombra=prep.get("modo_sombra"),
            telegram_meta=tg_meta,
        )
        snap_d = snap.a_dict()
        assert snap_d["lambdas_plano"] == lp
        assert snap_d["datos_corte"]["lambdas_plano"] == lp

        ruta_snap = ep.guardar_snapshot(snap, directorio=ruta_snaps)
        with open(ruta_snap, "r", encoding="utf-8") as fh:
            guardado = json.load(fh)
        assert guardado["lambdas_plano"] == lp

        r_pre = ep.registrar_sombra_prepartido_en_ledger_oos(
            prep, ruta_snapshot=ruta_snap, ruta_ledger=ruta_ledger
        )
        assert r_pre["registrado"] is True

        res_obj = ep.ResultadoPartido(
            id_partido="5109977",
            estado="finalizado",
            local="Corinthians",
            visitante="Botafogo",
            inicio_utc="2026-11-22T22:00:00Z",
            goles_local=1,
            goles_visitante=0,
            goles_local_1t=0,
            goles_visitante_1t=0,
            corners_local={"FT": 5},
            corners_visitante={"FT": 4},
            amarillas_local={"FT": 2},
            amarillas_visitante={"FT": 2},
        )
        r_liq = ep.liquidar_sombras_en_ledger_oos(
            [guardado], {"Corinthians vs Botafogo": res_obj}, ruta_ledger=ruta_ledger
        )
        assert r_liq["n_liquidados_nuevos"] == 1

        filas = ep.cargar_ledger_oos(ruta_ledger)
        assert len(filas) == 2
        assert filas[0]["lambdas_plano"] == lp
        assert "lambdas_plano" not in filas[0]["modo_sombra"]
        assert filas[1]["lambdas_plano"] == lp
        assert "lambdas_plano" not in filas[1]["modo_sombra"]
        assert ep.verificar_cadena_ledger_oos(ruta_ledger)["ok"] is True


def test_bloque_6_legacy_sin_fixture_clasificacion_oos_y_resumen() -> None:
    import hashlib
    from pathlib import Path

    # Construir un ledger mixto en disco sin alterar sus hashes originales:
    #   0: TB histórico (sin fixture_confirmado ni hora_inicio_partido)
    #   1: bec34ed5 pre_partido previo a M (Remo vs Chapecoense, sin fixture_confirmado ni hora_inicio_partido)
    #   2: 06a85843 pre_partido previo a M (Grêmio vs Bahia, sin fixture_confirmado ni hora_inicio_partido)
    #   3: liquidado previo a M sin fixture_confirmado ni es_oos_valido
    #   4: liquidado post-M OOS válido (canonica)
    def _firmar_linea(reg_in: dict, idx: int, prev_sha: str) -> dict:
        rec = dict(reg_in)
        rec["version"] = 1
        rec["indice"] = idx
        rec["retroalimenta_modelo"] = False
        rec["prev_sha256"] = prev_sha
        canon = json.dumps(
            {k: v for k, v in rec.items() if k != "sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        rec["sha256"] = hashlib.sha256(canon).hexdigest()
        return rec

    legacy_regs_raw = [
        {
            "id_partido": "5181796",
            "partido": "Turkiye vs Belgium",
            "fecha": "2026-11-12",
            "competicion": "UEFA Nations League",
            "resumen": {"aciertos": 52, "fallos": 6, "n_evaluables": 58, "brier": 0.0966945},
        },
        {
            "estado_registro": "pre_partido",
            "corte_temporal": "pre_partido",
            "id_trabajo": "bec34ed530d241a6",
            "id_partido": "",
            "partido": "Remo vs Chapecoense",
            "fecha": "2026-11-18",
            "competicion": "serie A Brasil",
            "telegram_message_date": 1795039913,
            "modo_sombra": {"esquema": "sombra-v1", "comparaciones": []},
        },
        {
            "estado_registro": "pre_partido",
            "corte_temporal": "pre_partido",
            "id_trabajo": "06a85843e007b41b",
            "id_partido": "",
            "partido": "Grêmio vs Bahia",
            "fecha": "2026-11-19",
            "competicion": "serie A Brasil",
            "telegram_message_date": 1795040070,
            "modo_sombra": {"esquema": "sombra-v1", "comparaciones": []},
        },
        {
            "estado_registro": "liquidado",
            "id_trabajo": "legacy_liq_sin_fx",
            "id_partido": "7770001",
            "partido": "Club X vs Club Y",
            "fecha": "2026-11-18",
            "competicion": "Serie A",
            "resumen": {"aciertos": 3, "fallos": 1, "n_evaluables": 4, "brier": 0.2},
            "modo_sombra": {
                "esquema": "sombra-v1",
                "observaciones": [
                    {
                        "id_partido": "7770001",
                        "partido": "Club X vs Club Y",
                        "familia": "goles_totales",
                        "evento_ocurrio": 1,
                        "probs": {"base": 0.8, "sombra": 0.78},
                    }
                ],
            },
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        ruta_led = os.path.join(tmpdir, "ledger_mixto_b6.jsonl")
        firmadas = []
        prev = "0" * 64
        for idx, rr in enumerate(legacy_regs_raw):
            f_rec = _firmar_linea(rr, idx, prev)
            prev = f_rec["sha256"]
            firmadas.append(f_rec)
        Path(ruta_led).write_text(
            "\n".join(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in firmadas) + "\n",
            encoding="utf-8",
        )
        hashes_legacy_antes = [x["sha256"] for x in firmadas]
        lineas_legacy_antes = Path(ruta_led).read_text(encoding="utf-8").splitlines()

        # Añadir entrada post-M OOS válida (canonica) y verificar hashes históricos intactos
        ep.registrar_en_ledger_oos(
            {
                "estado_registro": "liquidado",
                "id_trabajo": "post_m_valido",
                "id_partido": "8880001",
                "partido": "Corinthians vs Botafogo",
                "fecha": "2026-11-22",
                "fixture_confirmado": True,
                "fuente_fecha": "fotmob_fixture",
                "hora_inicio_partido": "2026-11-22T22:00:00Z",
                "telegram_message_date": 1795200000,
                "hora_mensaje_utc": "2026-11-20T05:00:00Z",
                "resumen": {"aciertos": 5, "fallos": 0, "n_evaluables": 5, "brier": 0.04},
                "modo_sombra": {
                    "esquema": "sombra-v1",
                    "observaciones": [
                        {
                            "id_partido": "8880001",
                            "partido": "Corinthians vs Botafogo",
                            "familia": "goles_totales",
                            "evento_ocurrio": 1,
                            "probs": {"base": 0.85, "sombra": 0.82},
                        }
                    ],
                },
            },
            ruta_ledger=ruta_led,
        )

        # No reescribe hashes legacy en disco
        lineas_despues = Path(ruta_led).read_text(encoding="utf-8").splitlines()
        assert lineas_despues[:4] == lineas_legacy_antes
        regs_disk = ep.cargar_ledger_oos(ruta_led)
        assert [r["sha256"] for r in regs_disk[:4]] == hashes_legacy_antes
        assert ep.verificar_cadena_ledger_oos(ruta_led)["ok"] is True

        # Clasificación individual de bec34ed5 y 06a85843 como legacy_sin_fixture (no-OOS)
        c_bec = ep.clasificar_oos_entrada_ledger(regs_disk[1])
        c_gre = ep.clasificar_oos_entrada_ledger(regs_disk[2])
        c_post = ep.clasificar_oos_entrada_ledger(regs_disk[4])
        assert c_bec["clasificacion_oos"] == "legacy_sin_fixture" and c_bec["es_oos_valido"] is False
        assert c_gre["clasificacion_oos"] == "legacy_sin_fixture" and c_gre["es_oos_valido"] is False
        assert c_post["clasificacion_oos"] == "canonica" and c_post["es_oos_valido"] is True

        # resumen_ledger_oos reporta correctamente ambos conteos OOS y resumen_oos_estricto
        res_def = ep.resumen_ledger_oos(ruta_led)
        assert res_def["cadena_sha256_ok"] is True
        assert res_def["politica_legacy_oos"] == "legacy_sin_fixture"
        assert res_def["n_legacy_sin_fixture"] == 4
        assert res_def["n_legacy_sin_fixture_pre_partido"] == 2
        assert res_def["n_legacy_sin_fixture_liquidados"] == 2
        assert res_def["n_oos_validos_confirmados"] == 1
        assert res_def["resumen_oos_estricto"]["n_incluidos"] == 1
        assert res_def["resumen_oos_estricto"]["n_excluidos_legacy_sin_fixture"] == 2
        assert res_def["resumen_oos_estricto"]["n_excluidos_no_oos"] == 2
        assert res_def["resumen_oos_estricto"]["partidos_incluidos"] == ["Corinthians vs Botafogo (2026-11-22)"]

        # Modo estricto explícito también excluye legacy_sin_fixture en conteo principal y en sombra
        res_est = ep.resumen_ledger_oos(ruta_led, excluir_legacy_sin_fixture=True)
        assert res_est["n_incluidos_promedios_calibracion"] == 1
        assert res_est["n_excluidos_legacy_sin_fixture"] == 2
        assert res_est["n_excluidos_no_oos"] == 2
        assert res_est["partidos_incluidos"] == ["Corinthians vs Botafogo (2026-11-22)"]

        obs_est = ep.extraer_observaciones_sombra_desde_ledger(ruta_led, excluir_legacy_sin_fixture=True)
        assert obs_est["n_partidos_sombra_validos"] == 1
        assert obs_est["n_excluidos_legacy_sin_fixture"] == 1
        assert obs_est["partidos_sombra_validos"] == ["8880001"]


def test_bloque_7_compatibilidad_ab30550_verificar_cadena_ledger_oos() -> None:
    import hashlib
    import subprocess
    from pathlib import Path

    # Código exacto de _hash_canonico_oos / verificar_cadena_ledger_oos en main ab30550
    def _hash_ab30550(payload_sin_hash: dict) -> str:
        crudo = json.dumps(payload_sin_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(crudo.encode("utf-8")).hexdigest()

    def _verificar_ab30550(ruta_ledger: str) -> dict:
        if not os.path.exists(ruta_ledger):
            registros = []
        else:
            registros = [
                json.loads(s)
                for s in Path(ruta_ledger).read_text(encoding="utf-8").splitlines()
                if s.strip()
            ]
        errores: list[str] = []
        prev_esperado = "0" * 64
        for idx_esp, reg in enumerate(registros):
            if reg.get("indice") != idx_esp:
                errores.append(f"Índice esperado {idx_esp}, encontrado {reg.get('indice')}")
            if reg.get("prev_sha256") != prev_esperado:
                errores.append(f"Entrada {idx_esp}: prev_sha256={reg.get('prev_sha256')} != esperado={prev_esperado}")
            hash_reg = reg.get("sha256")
            sin_hash = {k: v for k, v in reg.items() if k != "sha256"}
            recomp = _hash_ab30550(sin_hash)
            if hash_reg != recomp:
                errores.append(f"Entrada {idx_esp}: sha256 adulterado ({hash_reg} != {recomp})")
            if reg.get("retroalimenta_modelo") is not False:
                errores.append(f"Entrada {idx_esp}: retroalimenta_modelo debe ser False")
            prev_esperado = str(hash_reg or "")
        return {
            "ok": len(errores) == 0,
            "ruta": ruta_ledger,
            "entradas": len(registros),
            "ultimo_sha256": prev_esperado if registros else None,
            "errores": errores,
        }

    # Si main ab30550 está disponible en git local, verificar también la función cargada directo de ab30550
    raiz_repo = Path(__file__).resolve().parents[1]
    fn_git_ab30550 = None
    proc = subprocess.run(
        ["git", "-C", str(raiz_repo), "show", "ab30550:evaluacion_pronosticos.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode == 0 and "def verificar_cadena_ledger_oos" in proc.stdout:
        src_lines = proc.stdout.splitlines()
        # Extraer exactamente _hash_canonico_oos, cargar_ledger_oos y verificar_cadena_ledger_oos
        i0 = next(i for i, ln in enumerate(src_lines) if ln.startswith("def _hash_canonico_oos("))
        i1 = next(i for i, ln in enumerate(src_lines) if ln.startswith("def registrar_en_ledger_oos("))
        snippet = "\n".join(src_lines[i0:i1])
        ns = {
            "Any": object,
            "json": json,
            "hashlib": hashlib,
            "os": os,
            "LEDGER_OOS_PATH": "",
        }
        exec(compile(snippet, "ab30550:evaluacion_pronosticos.py", "exec"), ns)
        fn_git_ab30550 = ns["verificar_cadena_ledger_oos"]

    dl = _doc_equipo_sintetico("Corinthians", fuente="flashscore", con_minutos=True)
    dv = _doc_equipo_sintetico("Botafogo", fuente="fotmob", con_minutos=True)
    res_fake = {
        "equipo_local": "Corinthians",
        "equipo_visitante": "Botafogo",
        "doc_local": dl,
        "doc_visitante": dv,
        "meta_local": None,
        "meta_visitante": None,
        "contexto": {
            "fecha_partido": "2026-11-23",
            "hora_inicio_partido": "2026-11-23T22:00:00Z",
            "fixture_confirmado": True,
            "fuente_fecha": "fotmob_fixture",
            "competicion": "Série A (Brasil)",
            "pais_iso": "BRA",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
            "origen_condicion": "fixture_real",
            "sede": "Neo Química Arena",
            "id_partido_objetivo": "5109966",
        },
        "liga_solicitada": {"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
        "estado_identidad": "verificada",
        "motivo_identidad": "test",
        "identidad_verificada": True,
        "criterio_desempate": None,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        ruta_led = os.path.join(tmpdir, "ledger_retro_ab30550.jsonl")
        ruta_snaps = os.path.join(tmpdir, "snaps_b7")

        # 0: Entrada legacy previa a R18/R19 (sin campos nuevos)
        r_leg = ep.registrar_en_ledger_oos(
            {
                "id_partido": "5181796",
                "partido": "Turkiye vs Belgium",
                "fecha": "2026-11-12",
                "resumen": {"aciertos": 52, "fallos": 6, "n_evaluables": 58, "brier": 0.0967},
            },
            ruta_ledger=ruta_led,
        )
        assert r_leg["registrado"] is True

        # 1 y 2: Entradas formato nuevo R18+R19 (pre_partido y liquidado con fuente_local/visitante/firma/lambdas_plano/fixture)
        with patch.object(sm, "resolver_enfrentamiento", return_value=res_fake):
            prep = sm.preparar_analisis(
                "Corinthians",
                "Botafogo",
                umbral=0.70,
                directorio_salida=tmpdir,
                liga={"nombre": "Série A (Brasil)", "pais_iso": "BRA"},
            )
        ep.adjuntar_modo_sombra_preparado(prep)
        tg_meta = {"message_id": 707, "message_date": 1795066740, "message_date_utc": "2026-11-18T05:39:00Z"}
        prep["telegram_meta"] = tg_meta
        snap = ep.snapshot_desde_analisis(
            prep,
            publicado_en="2026-11-18T05:39:00Z",
            modo_sombra=prep.get("modo_sombra"),
            telegram_meta=tg_meta,
        )
        ruta_snap = ep.guardar_snapshot(snap, directorio=ruta_snaps)
        guardado = json.loads(Path(ruta_snap).read_text(encoding="utf-8"))
        assert ep.registrar_sombra_prepartido_en_ledger_oos(prep, ruta_snapshot=ruta_snap, ruta_ledger=ruta_led)["registrado"] is True
        res_obj = ep.ResultadoPartido(
            id_partido="5109966",
            estado="finalizado",
            local="Corinthians",
            visitante="Botafogo",
            inicio_utc="2026-11-23T22:00:00Z",
            goles_local=2,
            goles_visitante=1,
            goles_local_1t=1,
            goles_visitante_1t=0,
            corners_local={"FT": 6},
            corners_visitante={"FT": 3},
            amarillas_local={"FT": 2},
            amarillas_visitante={"FT": 2},
        )
        assert ep.liquidar_sombras_en_ledger_oos([guardado], {"Corinthians vs Botafogo": res_obj}, ruta_ledger=ruta_led)["n_liquidados_nuevos"] == 1

        filas = ep.cargar_ledger_oos(ruta_led)
        assert len(filas) == 3
        for idx_n in (1, 2):
            for campo in ("fuente_local", "fuente_visitante", "firma_datos", "lambdas_plano", "fixture_confirmado", "es_oos_valido"):
                assert campo in filas[idx_n] and filas[idx_n][campo] is not None

        ver_act = ep.verificar_cadena_ledger_oos(ruta_led)
        ver_ab = _verificar_ab30550(ruta_led)
        assert ver_act["ok"] is True and ver_act["errores"] == [] and ver_act["entradas"] == 3
        assert ver_ab["ok"] is True and ver_ab["errores"] == [] and ver_ab["entradas"] == 3
        assert ver_ab["ultimo_sha256"] == ver_act["ultimo_sha256"]
        if fn_git_ab30550 is not None:
            ver_git = fn_git_ab30550(ruta_led)
            assert ver_git["ok"] is True and ver_git["errores"] == [] and ver_git["entradas"] == 3
            assert ver_git["ultimo_sha256"] == ver_act["ultimo_sha256"]
