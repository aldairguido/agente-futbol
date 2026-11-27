from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import decision_cobertura as dc
import fuente_flashscore as ff
import motor_inferencia as mi
import motor_mercados as mm
import servicio_mercados as sm


PARES_10_LIGAS_DISTINTAS = [
    ("Arsenal", "9825", "ENG", "Arsenal Sarandí", "10081", "ARG"),
    ("Barcelona", "8634", "ESP", "Barcelona SC", "8352", "ECU"),
    ("Liverpool", "8650", "ENG", "Liverpool Montevideo", "10256", "URU"),
    ("Nacional", "10255", "URU", "Atlético Nacional", "8342", "COL"),
    ("River Plate", "10076", "ARG", "River Plate Montevideo", "10257", "URU"),
    ("Racing Santander", "8696", "ESP", "Racing Club", "10080", "ARG"),
    ("Sporting Gijón", "9869", "ESP", "Sporting CP", "9768", "POR"),
    ("Guadalajara", "9901", "ESP", "Chivas Guadalajara", "6618", "MEX"),
    ("Everton", "8668", "ENG", "Everton Viña del Mar", "8412", "CHI"),
    ("Unión", "10084", "ARG", "Union Berlin", "8149", "DEU"),
]


def _doc_club_con_futuro(
    nombre: str,
    club_id: str,
    *,
    home_nombre: str,
    home_id: str,
    away_nombre: str,
    away_id: str,
    con_ids_en_fixture: bool = True,
) -> dict:
    return {
        "equipo": {"id": club_id if con_ids_en_fixture else "", "nombre": nombre},
        "partidos": [],
        "partidos_futuros": [
            {
                "id_partido": "9100001",
                "home_id": home_id if con_ids_en_fixture else "",
                "away_id": away_id if con_ids_en_fixture else "",
                "home_nombre": home_nombre,
                "away_nombre": away_nombre,
                "utcTime": "2026-12-15T20:00:00Z",
                "competicion": "Copa Internacional",
                "estado": "Not Started",
            }
        ],
    }


def test_r31_bloque1_inter_vs_internacional_ambos_ordenes_y_bandera():
    # Orden 1: consulta Inter (8636) vs Internacional (8702), cuando el fixture real tiene Internacional en casa
    for con_ids in (True, False):
        dl = _doc_club_con_futuro(
            "Inter", "8636",
            home_nombre="Internacional", home_id="8702",
            away_nombre="Inter", away_id="8636",
            con_ids_en_fixture=con_ids,
        )
        dv = {"equipo": {"id": "8702" if con_ids else "", "nombre": "Internacional"}, "partidos": [], "partidos_futuros": []}
        res = sm._partido_futuro_en_comun(dl, dv, "Inter", "Internacional")
        assert res is not None
        assert res["condicion_local"] == "fuera", f"Inter debe ser fuera (con_ids={con_ids}): {res}"
        assert res["condicion_visitante"] == "casa", f"Internacional debe ser casa (con_ids={con_ids}): {res}"

        # Orden 2: consulta Internacional (8702) vs Inter (8636), mismo fixture (Internacional en casa)
        res_inv = sm._partido_futuro_en_comun(dv, dl, "Internacional", "Inter")
        assert res_inv is not None
        assert res_inv["condicion_local"] == "casa"
        assert res_inv["condicion_visitante"] == "fuera"

        # Orden 3: fixture real tiene Inter en casa e Internacional fuera
        dl_casa = _doc_club_con_futuro(
            "Inter", "8636",
            home_nombre="Inter", home_id="8636",
            away_nombre="Internacional", away_id="8702",
            con_ids_en_fixture=con_ids,
        )
        res_casa = sm._partido_futuro_en_comun(dl_casa, dv, "Inter", "Internacional")
        assert res_casa is not None
        assert res_casa["condicion_local"] == "casa"
        assert res_casa["condicion_visitante"] == "fuera"
        res_casa_inv = sm._partido_futuro_en_comun(dv, dl_casa, "Internacional", "Inter")
        assert res_casa_inv is not None
        assert res_casa_inv["condicion_local"] == "fuera"
        assert res_casa_inv["condicion_visitante"] == "casa"

    # Metadatos de partido ya jugado sin futuros: Inter (fuera) vs Internacional (casa)
    doc_inter_jug = {
        "equipo": {"id": "8636", "nombre": "Inter"},
        "partidos": [{
            "id_partido": "77701", "fecha": "2026-11-10", "condicion": "fuera",
            "rival": "Internacional", "goles_favor": 1, "goles_contra": 2,
            "marcador": {"equipo": 1, "rival": 2, "texto": "2 - 1"},
        }],
    }
    doc_intl_jug = {
        "equipo": {"id": "8702", "nombre": "Internacional"},
        "partidos": [{
            "id_partido": "77701", "fecha": "2026-11-10", "condicion": "casa",
            "rival": "Inter", "goles_favor": 2, "goles_contra": 1,
            "marcador": {"equipo": 2, "rival": 1, "texto": "2 - 1"},
        }],
    }
    meta_jug = sm._metadatos_del_encuentro(doc_inter_jug, doc_intl_jug, "Inter", "Internacional")
    assert meta_jug["condicion_local"] == "fuera"
    assert meta_jug["condicion_visitante"] == "casa"
    meta_jug_inv = sm._metadatos_del_encuentro(doc_intl_jug, doc_inter_jug, "Internacional", "Inter")
    assert meta_jug_inv["condicion_local"] == "casa"
    assert meta_jug_inv["condicion_visitante"] == "fuera"

    # Con bandera en False reproduce el comportamiento previo
    with dc.banderas_comportamiento(usar_emparejamiento_exacto_sin_subcadena=False):
        dl_no_id = _doc_club_con_futuro(
            "Inter", "8636",
            home_nombre="Internacional", home_id="8702",
            away_nombre="Inter", away_id="8636",
            con_ids_en_fixture=False,
        )
        dv_no_id = {"equipo": {"id": "", "nombre": "Internacional"}, "partidos": [], "partidos_futuros": []}
        res_legacy = sm._partido_futuro_en_comun(dl_no_id, dv_no_id, "Inter", "Internacional")
        assert res_legacy["condicion_local"] == "casa"


def test_r31_bloque1_real_madrid_sociedad_athletic_atletico_y_10_pares():
    pares_todos = [
        ("Real Madrid", "8633", "ESP", "Real Sociedad", "8560", "ESP"),
        ("Athletic Club", "8315", "ESP", "Atlético", "9906", "ESP"),
        *PARES_10_LIGAS_DISTINTAS,
    ]
    assert len(PARES_10_LIGAS_DISTINTAS) == 10

    cli_fs = ff.ClienteFlashscore()
    for nom_a, id_a, _pais_a, nom_b, id_b, _pais_b in pares_todos:
        for con_ids in (True, False):
            # Caso donde B es local y A es visitante en el fixture programado
            doc_a = _doc_club_con_futuro(
                nom_a, id_a,
                home_nombre=nom_b, home_id=id_b,
                away_nombre=nom_a, away_id=id_a,
                con_ids_en_fixture=con_ids,
            )
            doc_b = {"equipo": {"id": id_b if con_ids else "", "nombre": nom_b}, "partidos": [], "partidos_futuros": []}

            r_ab = sm._partido_futuro_en_comun(doc_a, doc_b, nom_a, nom_b)
            assert r_ab is not None, f"Fallo en {nom_a} vs {nom_b} (con_ids={con_ids})"
            assert r_ab["condicion_local"] == "fuera", f"{nom_a} vs {nom_b} (con_ids={con_ids}) invirtió localía"
            assert r_ab["condicion_visitante"] == "casa"

            r_ba = sm._partido_futuro_en_comun(doc_b, doc_a, nom_b, nom_a)
            assert r_ba is not None
            assert r_ba["condicion_local"] == "casa"
            assert r_ba["condicion_visitante"] == "fuera"

        # Flashscore _parecido y buscar_evento_programado_feed_diario no deben confundir A con B
        assert ff._parecido(nom_a, nom_b) is False, f"_parecido({nom_a}, {nom_b}) dio True"
        assert ff._parecido(nom_b, nom_a) is False, f"_parecido({nom_b}, {nom_a}) dio True"

        cli_fs._CACHE_FEED_DIARIO.clear()
        feed_txt = (
            f"ZA÷LIGA OFICIAL DE PRUEBA RONDA 31~AA÷ID_BA¬AD÷1795557600¬AB÷1¬AE÷{nom_b}¬JA÷HB¬AF÷{nom_a}¬JB÷AA~"
            f"ZA÷LIGA OFICIAL DE PRUEBA RONDA 31~AA÷ID_AB¬AD÷1795557600¬AB÷1¬AE÷{nom_a}¬JA÷HA¬AF÷{nom_b}¬JB÷AB~"
            + ("~" * 60)
        )
        with patch.object(cli_fs, "_get", return_value=SimpleNamespace(status_code=200, text=feed_txt)):
            ev_ab = cli_fs.buscar_evento_programado_feed_diario(nom_a, nom_b, dias=(0,))
            ev_ba = cli_fs.buscar_evento_programado_feed_diario(nom_b, nom_a, dias=(0,))
        assert ev_ab is not None and ev_ab["id_flashscore"] == "ID_AB", f"Feed {nom_a} vs {nom_b}: {ev_ab}"
        assert ev_ba is not None and ev_ba["id_flashscore"] == "ID_BA", f"Feed {nom_b} vs {nom_a}: {ev_ba}"


def test_r31_bloque1_motor_mercados_e_inferencia_sin_subcadenas():
    # _es_mismo_equipo en motor_inferencia no debe confundir Inter con Internacional ni Athletic con Atlético
    assert mi._es_mismo_equipo("Inter", "Internacional") is False
    assert mi._es_mismo_equipo("Internacional", "Inter") is False
    assert mi._es_mismo_equipo("Athletic Club", "Atlético") is False
    assert mi._es_mismo_equipo("Real Madrid", "Real Sociedad") is False

    # calcular_frecuencia_observada en motor_mercados: selección de Internacional no debe leer historial de Inter
    doc_inter = {
        "equipo": {"id": "8636", "nombre": "Inter"},
        "partidos": [
            {"gf": 3, "gc": 0, "periodos": {"FT": {"filas": [{"clave": "corners", "equipo": 8, "rival": 2}]}}}
            for _ in range(15)
        ],
    }
    doc_intl = {
        "equipo": {"id": "8702", "nombre": "Internacional"},
        "partidos": [
            {"gf": 0, "gc": 2, "periodos": {"FT": {"filas": [{"clave": "corners", "equipo": 2, "rival": 7}]}}}
            for _ in range(15)
        ],
    }
    sel_vis_corners = SimpleNamespace(
        mercado="Córneres por equipo",
        evento="over_4.5",
        etiqueta="Córneres Internacional (FT): +4.5 Córn.",
        equipo="Internacional",
        periodo="FT",
        linea=4.5,
    )
    tasa = mm.calcular_frecuencia_observada(sel_vis_corners, doc_inter, doc_intl, "Inter", "Internacional")
    assert tasa == 0.0


def test_r31_bloque2_veredicto_sin_fallbacks_numericos_y_tope_099_solo_presentacion():
    import informe

    ver_vacio = mi.generar_veredicto_tactico_partido(
        nombre_local="EqA",
        nombre_visita="EqB",
        patrones_loc={},
        patrones_vis={},
        probs={"local": 0.45, "empate": 0.25, "visita": 0.30},
        cuadro_b_loc={},
        cuadro_b_vis={},
        cuadro_c_loc={},
        cuadro_c_vis={},
        ratings_loc={},
        ratings_vis={},
    )
    prohibidos = ("68.2", "52.1", "8/15", "8 victorias", "50.0%", "62.5", "11.0", "11.5", "3.7", "1.50")
    for tok in prohibidos:
        assert tok not in ver_vacio, f"Fallback numérico {tok!r} apareció en Veredicto vacío:\n{ver_vacio}"
    assert "sin dato" in ver_vacio

    rep_xai_vacio = mi.generar_reporte_xai_telegram(
        nombre_local="EqA",
        nombre_visita="EqB",
        patrones_loc={},
        patrones_vis={},
        probs={"local": 0.45, "empate": 0.25, "visita": 0.30},
        alertas=[],
        cuadro_b_loc={},
        cuadro_b_vis={},
        cuadro_c_loc={},
        cuadro_c_vis={},
        ratings_loc={},
        ratings_vis={},
    )
    for tok in (*prohibidos, "0/0", "0.0 tiros por gol"):
        assert tok not in rep_xai_vacio, f"Fallback {tok!r} apareció en reporte XAI vacío:\n{rep_xai_vacio}"
    assert "sin dato" in rep_xai_vacio

    cb_vacio = mi.calcular_cuadro_b_tendencias_15p([], "SinDatos")
    assert cb_vacio["n_partidos"] == 0
    assert cb_vacio["victorias"]["sin_dato"] is True
    assert cb_vacio["victorias"]["str"] == "sin dato"

    # Con bandera False restaura los fallbacks antiguos
    with dc.banderas_comportamiento(usar_sin_dato_sin_fallbacks_veredicto=False):
        ver_leg = mi.generar_veredicto_tactico_partido(
            "EqA", "EqB", {}, {}, {"local": 0.45, "empate": 0.25, "visita": 0.30}, {}, {}, {}, {}
        )
        assert "68.2" in ver_leg and "50.0%" in ver_leg and "3.7" in ver_leg and "1.50" in ver_leg

    # Demostración de que el tope 0.99 de informe._extraer_probabilidades_red_general es solo de presentación
    # y no altera las probabilidades reales del catálogo de mercados del motor (doble_oportunidad).
    mat_extrema = [[0.0] * 5 for _ in range(5)]
    mat_extrema[2][0] = 0.90
    mat_extrema[1][1] = 0.095
    mat_extrema[0][2] = 0.005
    r1x2 = mm.resultado_1x2(mat_extrema)
    assert abs((r1x2["1"] + r1x2["X"]) - 0.995) < 1e-9  # sin recorte 0.99 en el motor

    an_stub = SimpleNamespace(
        metadatos={"equipo_local": "EqA", "equipo_visitante": "EqB", "competicion": "LaLiga"},
        motor=SimpleNamespace(matriz_ft=mat_extrema, tasas=SimpleNamespace(lambda_local_ft=2.5, lambda_visitante_ft=0.2)),
        mercados=[],
        selecciones_filtradas=[],
    )
    info_pres = informe._extraer_probabilidades_red_general(an_stub)
    assert info_pres["p_1x"] == 0.99
    assert abs((r1x2["1"] + r1x2["X"]) - 0.995) < 1e-9


def test_r31_bloque3_log_consultas_jsonl_append_only_persistencia_y_reinicio_285m(monkeypatch, tmp_path):
    import json
    import subprocess
    import bot_interactivo as bi
    import persistencia_produccion as pp

    ws_a = tmp_path / "runner_antes_reinicio"
    ws_b = tmp_path / "runner_despues_reinicio_285m"
    remoto_priv = tmp_path / "remoto_datos_privado.git"
    remoto_pub = tmp_path / "remoto_publico.git"
    for p in (ws_a, ws_b, remoto_priv, remoto_pub):
        p.mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init", "--bare", str(remoto_priv)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(remoto_pub)], check=True, capture_output=True)
    for ws in (ws_a, ws_b):
        subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(ws), "remote", "add", "origin", str(remoto_pub)], check=True, capture_output=True)

    log_jsonl_a = ws_a / pp.RUTA_LOG_CONSULTAS_JSONL_REL
    log_res_a = ws_a / pp.RUTA_LOG_CONSULTAS_RESUMEN_REL
    monkeypatch.setattr(sm, "LOG_CONSULTAS_JSONL", str(log_jsonl_a))
    monkeypatch.setattr(sm, "LOG_CONSULTAS_ABSTENCIONES", str(log_res_a))
    sm.reiniciar_contador_abstenciones_consultas()

    # 1) Consulta OK
    sm.registrar_consulta_log_abstenciones(
        equipo_local="Athletic Club",
        equipo_visitante="Espanyol",
        competicion="LaLiga",
        pais_iso="ESP",
        abstenido=False,
        estado_cobertura="cobertura_suficiente",
        motivo_abstencion=None,
        id_trabajo="id_ok_1",
    )
    # 2) Abstención temprana en bot_interactivo.cmd_mercados por liga ambigua ("serie a")
    mensajes_bot = []
    monkeypatch.setattr(bi, "enviar_seguro", lambda _b, _m, txt, **kw: mensajes_bot.append(txt))
    monkeypatch.setattr(bi, "_encolar_persistencia_segura", lambda **kw: kw)
    msg_fake = SimpleNamespace(
        text="/mercados serie a Milan vs Inter",
        chat=SimpleNamespace(id=12345),
        from_user=SimpleNamespace(id=12345),
    )
    # Llamar función interna decorada o con chat autorizado
    monkeypatch.setattr(bi, "CHAT_ID_AUTORIZADO", "12345")
    bi.cmd_mercados(msg_fake)
    assert mensajes_bot, "cmd_mercados debe responder desambiguación en liga ambigua"

    # 3) Abstención temprana en preparar_analisis por partido_ya_jugado
    monkeypatch.setattr(
        sm,
        "resolver_enfrentamiento",
        lambda *a, **k: {
            "equipo_local": "Real Madrid",
            "equipo_visitante": "Celta Vigo",
            "doc_local": {"equipo": {"nombre": "Real Madrid"}, "partidos": []},
            "doc_visitante": {"equipo": {"nombre": "Celta Vigo"}, "partidos": []},
            "contexto": {
                "competicion": "LaLiga",
                "pais_iso": "ESP",
                "partido_ya_jugado": True,
                "fecha_jugado": "2026-11-21",
                "resultado_partido": "Real Madrid 3 - 1 Celta Vigo",
            },
        },
    )
    res_jug = sm.preparar_analisis("Real Madrid", "Celta Vigo")
    assert res_jug["partido_ya_jugado"] is True

    # 4) Abstención por falso_homonimo_extranjero
    sm.registrar_consulta_log_abstenciones(
        equipo_local="Villarreal",
        equipo_visitante="Barcelona SC",
        competicion="LaLiga",
        pais_iso="ESP",
        abstenido=True,
        estado_cobertura="falso_homonimo_extranjero",
        motivo_abstencion="FALSO HOMÓNIMO EXTRANJERO detectado en visita",
        id_trabajo="id_fh_4",
    )

    assert log_jsonl_a.exists()
    lineas_a = [json.loads(ln) for ln in log_jsonl_a.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lineas_a) == 4
    estados_a = [x["estado"] for x in lineas_a]
    assert estados_a == ["cobertura_suficiente", "liga_ambigua", "partido_ya_jugado", "falso_homonimo_extranjero"]
    for row in lineas_a:
        for campo in ("fecha_utc", "hora_lima", "partido", "estado", "motivo", "build"):
            assert campo in row and str(row[campo]).strip() != "", f"Falta {campo} en {row}"
        assert row["fecha_utc"].endswith("Z")
        assert "America/Lima" in row["hora_lima"]

    # 5) Persistir hacia la rama privada produccion-datos
    cfg_a = pp.ConfigPersistencia(
        workspace_dir=ws_a,
        remote="origin",
        remote_url=str(remoto_priv),
        rama_datos="produccion-datos",
        habilitada=True,
    )
    res_push_a = pp.sincronizar_y_push_datos(cfg_a, mensaje_commit="datos(test): log consultas antes de reinicio")
    assert res_push_a["ok"] is True and res_push_a["pushed"] is True

    # 6) Simular reinicio de 285 min en un runner limpio (ws_b) e hidratar desde produccion-datos
    log_jsonl_b = ws_b / pp.RUTA_LOG_CONSULTAS_JSONL_REL
    log_res_b = ws_b / pp.RUTA_LOG_CONSULTAS_RESUMEN_REL
    monkeypatch.setattr(sm, "LOG_CONSULTAS_JSONL", str(log_jsonl_b))
    monkeypatch.setattr(sm, "LOG_CONSULTAS_ABSTENCIONES", str(log_res_b))

    cfg_b = pp.ConfigPersistencia(
        workspace_dir=ws_b,
        remote="origin",
        remote_url=str(remoto_priv),
        rama_datos="produccion-datos",
        habilitada=True,
    )
    res_hyd_b = pp.hidratar_desde_remoto_datos(cfg_b)
    assert res_hyd_b["ok"] is True
    assert log_jsonl_b.exists()

    snap_post_reinicio = sm.simular_reinicio_proceso_log_consultas(
        ruta_jsonl=str(log_jsonl_b), ruta_resumen=str(log_res_b)
    )
    assert snap_post_reinicio["total_consultas"] == 4
    assert snap_post_reinicio["total_ok"] == 1
    assert snap_post_reinicio["total_abstenidas"] == 3
    assert snap_post_reinicio["por_estado_cobertura"]["liga_ambigua"] == 1
    assert snap_post_reinicio["por_estado_cobertura"]["partido_ya_jugado"] == 1
    assert snap_post_reinicio["por_motivo"]["FALSO_HOMONIMO"] == 1

    # 7) Nueva consulta tras el reinicio se añade en append-only sin perder las 4 anteriores
    sm.registrar_consulta_log_abstenciones(
        equipo_local="Athletic Unido",
        equipo_visitante="Barcelona",
        competicion="Champions League",
        pais_iso="INT",
        abstenido=True,
        estado_cobertura="pais_ambiguo",
        motivo_abstencion="PAIS_AMBIGUO detectado",
        id_trabajo="id_pa_5",
    )
    lineas_b = [json.loads(ln) for ln in log_jsonl_b.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lineas_b) == 5
    assert sm.obtener_contador_abstenciones_consultas()["total_consultas"] == 5
    assert sm.obtener_contador_abstenciones_consultas()["por_motivo"]["PAIS_AMBIGUO"] == 1


def test_r31_bloque4_excepcion_en_cuotas_no_anula_ticket_ni_3_parrafos(monkeypatch, tmp_path):
    import json
    from fuente_flashscore import ClienteFlashscore

    root = Path(__file__).resolve().parents[1]
    fx = json.loads((root / "tests" / "fixtures" / "londrina_nautico_fixtures.json").read_text(encoding="utf-8"))
    dl = copy.deepcopy(fx["londrina"])
    dv = copy.deepcopy(fx["nautico"])
    for d in (dl, dv):
        for idx, p in enumerate(d.get("partidos") or []):
            p["fecha"] = f"2026-11-{20 - (idx % 15):02d}"

    fake_ctx = {
        "equipo_local": "Londrina",
        "equipo_visitante": "Náutico",
        "doc_local": dl,
        "doc_visitante": dv,
        "contexto": {
            "competicion": "Brasileirao Serie C",
            "pais_iso": "BRA",
            "condicion_local": "casa",
            "condicion_visitante": "fuera",
            "fecha_partido": "2026-12-05",
            "hora_inicio_partido": "2026-12-05T20:00:00Z",
            "fixture_confirmado": True,
            "id_partido": "999111",
            "id_flashscore_partido": "",
        },
        "identidad_verificada": True,
        "estado_identidad": "verificada",
    }

    class _DummyConciencia:
        def diagnosticar_enfrentamiento(self, *a, **k):
            return {"indice_confianza_cognitiva": 0.5}
        def formatear_bloque_metacognitivo_md(self, *a, **k):
            return ""

    monkeypatch.setattr(sm, "resolver_enfrentamiento", lambda *a, **k: copy.deepcopy(fake_ctx))
    monkeypatch.setattr(sm, "marcar_estado", lambda *a, **k: None)
    monkeypatch.setattr(sm, "MERCADOS_DIR", str(tmp_path / "mer"))
    monkeypatch.setattr(sm, "H2H_DIR", str(tmp_path / "h2h"))
    monkeypatch.setattr(sm, "LOG_CONSULTAS_ABSTENCIONES", str(tmp_path / "mer" / "log_abs.json"))
    monkeypatch.setattr(sm, "LOG_CONSULTAS_JSONL", str(tmp_path / "mer" / "log_cons.jsonl"))
    monkeypatch.setattr(sm, "REGISTRO_ENVIOS", str(tmp_path / "mer" / "reg_env.json"))
    monkeypatch.setattr("banco_historico.guardar_en_banco_no_bloqueante", lambda *a, **k: None)
    monkeypatch.setattr("agente_consciente.obtener_agente_consciente", lambda: _DummyConciencia())
    monkeypatch.setattr(
        "cuadro_h2h_2024_2026.generar_cuadro_h2h_desde_analisis",
        lambda *a, **k: {"ruta": None, "n_duelos": 0, "estado": "Sin enfrentamientos directos en 2024–2026", "duelos": []},
    )

    for idx, exc_cls in enumerate((TimeoutError, RuntimeError, ValueError, KeyError)):
        out_d = tmp_path / f"case_{idx}"
        out_d.mkdir(parents=True, exist_ok=True)
        with patch.object(ClienteFlashscore, "buscar_evento_programado_feed_diario", side_effect=exc_cls("fallo feed")), \
             patch.object(ClienteFlashscore, "extraer_cuotas", side_effect=exc_cls("fallo extraer_cuotas")):
            prep = sm.preparar_analisis("Londrina", "Náutico", directorio_salida=str(out_d))
        assert prep.get("abstenido") is not True
        ruta_tv = prep.get("ruta_ticket_valor") or (prep.get("rutas") or {}).get("ticket_valor")
        assert ruta_tv is not None and Path(ruta_tv).exists(), f"Ticket Valor PNG anulado con {exc_cls.__name__}"
        txt_3p = prep.get("analisis_texto_3_parrafos") or ""
        assert len(txt_3p) > 0, f"Texto 3 párrafos vacío con {exc_cls.__name__}"
        assert prep.get("cuotas_flashscore") is None


def test_r31_bloque5_etiqueta_matriz_final_cronologia_raw_y_antiguedad():
    # 1) Etiqueta "Matriz final (45-DCN+Sinkhorn+NB)" y conteo real n_partidos_con_eventos_raw
    cc_loc = {
        "sin_dato": True,
        "_meta": {
            "n_partidos_con_eventos": 0,
            "n_partidos_con_eventos_raw": 2,
            "n_partidos_totales": 15,
            "sin_dato": True,
        },
    }
    cc_vis = {
        "sin_dato": True,
        "_meta": {
            "n_partidos_con_eventos": 0,
            "n_partidos_con_eventos_raw": 1,
            "n_partidos_totales": 15,
            "sin_dato": True,
        },
    }
    ver = mi.generar_veredicto_tactico_partido(
        "Athletic Club",
        "Espanyol",
        {},
        {},
        {"local": 0.562, "empate": 0.251, "visita": 0.187},
        {},
        {},
        cc_loc,
        cc_vis,
    )
    assert "Matriz final (45-DCN+Sinkhorn+NB):" in ver
    assert "Base Matriz Poisson:" not in ver
    assert "(1/15 partidos con cronología verificada)" in ver

    rep_xai = mi.generar_reporte_xai_telegram(
        "Athletic Club",
        "Espanyol",
        {},
        {},
        {"local": 0.562, "empate": 0.251, "visita": 0.187},
        [],
        {},
        {},
        cc_loc,
        cc_vis,
    )
    assert "1/15 partidos" in rep_xai

    # Con bandera False vuelve a "Base Matriz Poisson" y 0/15
    with dc.banderas_comportamiento(usar_etiqueta_matriz_final_y_cronologia_raw=False):
        ver_leg = mi.generar_veredicto_tactico_partido(
            "Athletic Club",
            "Espanyol",
            {},
            {},
            {"local": 0.562, "empate": 0.251, "visita": 0.187},
            {},
            {},
            cc_loc,
            cc_vis,
        )
        assert "Base Matriz Poisson:" in ver_leg
        assert "(0/15 partidos con cronología verificada)" in ver_leg

    # 2) evaluar_antiguedad_datos aclara "hasta la fecha del partido YYYY-MM-DD"
    doc_l = {"equipo": "Celta Vigo", "partidos": [{"fecha": "2026-10-15"}]}
    doc_v = {"equipo": "Sevilla", "partidos": [{"fecha": "2026-11-20"}]}
    ev_ant = sm.evaluar_antiguedad_datos(doc_l, doc_v, fecha_referencia="2026-11-25", max_dias=30)
    assert ev_ant["desactualizado"] is True
    assert "41 días de antigüedad hasta la fecha del partido 2026-11-25 > 30 días" in ev_ant["motivo"]


def test_r31_bloque6_umbral_unificado_sin_tolerancia_ni_redondeo():
    # Regla única p >= 0.70 sin redondear y sin tolerancia -1e-6
    for p_val, esperado in (
        (0.695, False),
        (0.6999, False),
        (0.699999, False),
        (0.70, True),
        (0.7001, True),
        (0.705, True),
    ):
        assert dc.supera_umbral_probabilidad(p_val, 0.70) is esperado

        # 1) motor_mercados.clasificar
        esc_ok = {"e1": p_val, "e2": 0.75}
        est_cls, _ = mm.clasificar(p_val, esc_ok, ["e1", "e2"], 0.70)
        assert (est_cls == mm.ESTADO_MANTIENE) is esperado

        # 2) seleccionar_top_5_jugadas_recomendadas
        sel_item = {
            "id_mercado": "doble_oportunidad",
            "mercado": "Doble oportunidad",
            "evento": "1x",
            "etiqueta": "1X (EqA o Empate)",
            "periodo": "FT",
            "equipo": "EqA",
            "linea": None,
            "probabilidad": p_val,
            "estado": "mantiene" if esperado else "sensible",
        }
        top5 = mm.seleccionar_top_5_jugadas_recomendadas([sel_item], eq_l="EqA", eq_v="EqB")
        assert (len(top5) == 1) is esperado

        # 3) calcular_seccion1_unificada
        s1_u = sm.calcular_seccion1_unificada(
            None,
            metadatos={"equipo_local": "EqA", "equipo_visitante": "EqB"},
            umbral=0.70,
            sec1_data=[dict(sel_item)],
            eq_l="EqA",
            eq_v="EqB",
        )
        assert (len(s1_u["mantiene_raw"]) == 1) is esperado

        # 4) motor_inferencia.formatear_pick_segun_estado y _esta_en_seccion_2
        p_pct = p_val * 100.0
        txt_pick = mi.formatear_pick_segun_estado("Ambos marcan: Sí", p_pct, "mantiene", umbral_pct=70.0)
        assert ("Por debajo del umbral" not in txt_pick) is esperado
        en_s2 = mi._esta_en_seccion_2(
            "1x",
            prob_pct=p_pct,
            selecciones_sec2=[{"evento": "1x", "periodo": "FT", "probabilidad": p_val, "estado": "sensible"}],
            umbral_pct=70.0,
        )
        assert en_s2 is esperado

        # 5) Ninguna probabilidad < 0.70 se formatea como "70%" ni "70,0%"
        if not esperado:
            pct_str = mm.formatear_pct(p_val, 0.70)
            assert pct_str not in ("70%", "70,0%", "70.0%"), f"Redondeo engañoso al umbral para {p_val}: {pct_str}"
            _, pild_pct, _ = sm.formatear_pildora_seccion1(sel_item, "GOLES PARTIDO", "EqA", "EqB")
            assert pild_pct != "70%", f"Píldora redondeó {p_val} a 70%"


def test_r31_bloque7_cache_auditoria_incluye_corte_ventanas_distintas(monkeypatch):
    import auditoria_historica as ah

    ah.limpiar_cache_memoria_auditorias()
    llamadas_internas = []

    # Generamos un calendario sintético de 25 partidos hacia atrás desde 2026-11-24
    from datetime import date, timedelta
    todos_partidos = []
    d0 = date(2026, 11, 24)
    for i in range(25):
        fecha_p = (d0 - timedelta(days=i * 2)).isoformat()
        todos_partidos.append(
            {
                "id_partido": f"m_{i+1}",
                "fecha": fecha_p,
                "rival": f"Rival_{i+1}",
                "torneo": "LaLiga",
                "goles_equipo": 2,
                "goles_rival": 1,
                "condicion": "casa",
                "periodos": {"FT": {"filas": [{"clave": "goles", "equipo": 2, "rival": 1}] * 6}},
            }
        )

    def _fake_auditar_interno(**kwargs):
        fc = str(kwargs.get("fecha_corte") or "9999-12-31")
        llamadas_internas.append(fc)
        pts_ventana = [dict(p) for p in todos_partidos if p["fecha"] < fc][:15]
        return {
            "equipo": {
                "id": "8633",
                "nombre": "Real Madrid",
                "liga": "LaLiga",
                "id_liga": 87,
                "pais_iso": "ESP",
            },
            "partidos": pts_ventana,
            "resumen": {"partidos_con_datos": len(pts_ventana)},
            "identidad_verificada": True,
        }

    monkeypatch.setattr("fuentes_xg.ejecutar_sync_seguro", lambda fn, **kw: _fake_auditar_interno(**kw))

    # 1) Primera consulta con fecha_corte="2026-11-05" (ventana más antigua)
    doc_corte_1 = ah.auditar_equipo("Real Madrid", limite=15, fecha_corte="2026-11-05")
    fechas_1 = [p["fecha"] for p in doc_corte_1["partidos"]]
    assert len(fechas_1) == 15
    assert max(fechas_1) < "2026-11-05"

    # 2) Segunda consulta del MISMO club con fecha_corte="2026-11-25" (ventana más reciente) en el mismo proceso
    doc_corte_2 = ah.auditar_equipo("Real Madrid", limite=15, fecha_corte="2026-11-25")
    fechas_2 = [p["fecha"] for p in doc_corte_2["partidos"]]
    assert len(fechas_2) == 15
    assert fechas_2 != fechas_1, "La caché devolvió la misma ventana para fechas de corte distintas"
    assert max(fechas_2) > "2026-11-05"
    assert len(llamadas_internas) == 2

    # 3) Tercera consulta repitiendo fecha_corte="2026-11-05" reutiliza su entrada exacta en memoria
    doc_corte_1_hit = ah.auditar_equipo("Real Madrid", limite=15, fecha_corte="2026-11-05")
    assert [p["fecha"] for p in doc_corte_1_hit["partidos"]] == fechas_1
    assert len(llamadas_internas) == 2


def test_r31_bloque8_banderas_sombra_a1_a2_apagadas_por_defecto():
    # Por defecto ambas banderas están apagadas (False) para mantener invariancia absoluta en producción
    assert dc.bandera_activa("usar_correccion_a1_sin_duplicar_xg_contra") is False
    assert dc.bandera_activa("usar_correccion_a2_media_ponderada") is False

    # Construimos un documento con 15 partidos donde el partido 1 tiene tarjeta roja (peso_roja < 1)
    partidos = []
    for i in range(15):
        es_roja = i == 0
        xg_riv = 2.50 if es_roja else 1.00
        goles_eq = 3 if es_roja else 1
        partidos.append(
            {
                "id_partido": f"p_{i+1}",
                "fecha": f"2026-11-{20 - i:02d}",
                "condicion": "casa" if i % 2 == 0 else "fuera",
                "goles_equipo": goles_eq,
                "goles_rival": 1,
                "marcador": {"equipo": goles_eq, "rival": 1},
                "periodos": {
                    "FT": {
                        "filas": [
                            {"clave": "xg", "equipo": 1.20, "rival": xg_riv},
                            {"clave": "tarjetas_rojas", "equipo": 1 if es_roja else 0, "rival": 0},
                        ]
                    }
                },
            }
        )
    doc = {"equipo": {"nombre": "ClubTest", "liga": "LaLiga"}, "partidos": partidos}

    # 1) Modo producción (banderas False): xg_contra tiene n=30 (duplicado) y media_ajustada usa media simple
    m_prod = mm.extraer_muestra(doc, condicion=None, periodo="FT", peso_roja=0.25)
    assert m_prod.dame("xg_contra").n == 30
    ma_prod = mm.media_ajustada(m_prod.dame("goles_favor"), m_prod.dame("goles_favor"), 0.5)
    assert abs(ma_prod - m_prod.dame("goles_favor").media) < 1e-12

    # 2) Modo sombra corregido (A1=True, A2=True): xg_contra tiene n=15 y media_ajustada usa media_ponderada
    with dc.banderas_comportamiento(
        usar_correccion_a1_sin_duplicar_xg_contra=True,
        usar_correccion_a2_media_ponderada=True,
    ):
        m_corr = mm.extraer_muestra(doc, condicion=None, periodo="FT", peso_roja=0.25)
        assert m_corr.dame("xg_contra").n == 15
        ma_corr = mm.media_ajustada(m_corr.dame("goles_favor"), m_corr.dame("goles_favor"), 0.5)
        assert abs(ma_corr - m_corr.dame("goles_favor").media_ponderada) < 1e-12
        assert ma_corr < ma_prod







