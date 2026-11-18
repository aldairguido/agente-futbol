"""Tests de verificación Ronda 18 (fix/ronda-18-tickets)."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import cuadro_especialista as ce
import motor_inferencia as mi
import motor_patrones as mp


def _partido_flashscore_sin_minutos(idx: int, gf1: int = 1, gf2: int = 1, gc1: int = 0, gc2: int = 1, con_mitad: bool = True) -> dict:
    periodos = {
        "FT": {"filas": [{"clave": "xg", "equipo": 1.1, "rival": 0.9} for _ in range(12)]},
    }
    if con_mitad:
        periodos["1T"] = {
            "filas": [
                {"clave": "xgot_enfrentado", "equipo": float(gc1), "rival": float(gf1)},
                {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
            ]
        }
        periodos["2T"] = {
            "filas": [
                {"clave": "xgot_enfrentado", "equipo": float(gc2), "rival": float(gf2)},
                {"clave": "goles_evitados", "equipo": 0.0, "rival": 0.0},
            ]
        }
    return {
        "id_partido": f"fs_{idx}",
        "fecha": f"2026-10-{min(28, 1 + idx):02d}",
        "torneo": "BRAZIL: Serie A",
        "rival": f"RivalFS{idx}",
        "condicion": "casa" if idx % 2 == 0 else "fuera",
        "marcador": {"equipo": gf1 + gf2, "rival": gc1 + gc2},
        "goles_equipo": gf1 + gf2,
        "goles_rival": gc1 + gc2,
        "resumen_claves": 20,
        "periodos": periodos,
    }


def _partido_fotmob_con_minutos(idx: int, minuto: int = 23) -> dict:
    return {
        "id_partido": f"fm_{idx}",
        "fecha": f"2026-10-{min(28, 1 + idx):02d}",
        "torneo": "Série A",
        "rival": f"RivalFM{idx}",
        "condicion": "fuera" if idx % 2 == 0 else "casa",
        "marcador": {"equipo": 1, "rival": 0},
        "goles_equipo": 1,
        "goles_rival": 0,
        "resumen_claves": 25,
        "eventos_gol": [{"minuto": minuto, "minuto_str": f"{minuto}'", "lado": "equipo"}],
        "periodos": {"FT": {"filas": [{"clave": "xg", "equipo": 1.3, "rival": 0.7} for _ in range(12)]}},
    }


def test_bloque_a_sin_minutos_franjas_none_y_suprime_patron35_alerta_veredicto() -> None:
    partidos_coritiba = [_partido_flashscore_sin_minutos(i, 1, 0, 0, 1, con_mitad=(i < 11)) for i in range(15)]
    partidos_botafogo = [_partido_fotmob_con_minutos(i, minuto=23 if i < 10 else 65) for i in range(15)]

    cc_cor = mi.calcular_cuadro_c_timeline_15min(partidos_coritiba, "Coritiba")
    assert cc_cor["sin_dato"] is True
    assert cc_cor["_meta"]["sin_dato"] is True
    assert cc_cor["_meta"]["n_partidos_con_eventos"] == 0
    assert cc_cor["total_anotados"] is None
    assert cc_cor["total_recibidos"] is None
    for fr_lbl in ("00'-15'", "16'-30'", "31'-45'", "46'-60'", "61'-75'", "76'-90'"):
        assert cc_cor["anotados"][fr_lbl] is None
        assert cc_cor["recibidos"][fr_lbl] is None

    cc_bot = mi.calcular_cuadro_c_timeline_15min(partidos_botafogo, "Botafogo")
    assert cc_bot["sin_dato"] is False
    assert cc_bot["_meta"]["n_partidos_con_eventos"] == 15
    assert cc_bot["anotados"]["16'-30'"] == 10
    assert cc_bot["anotados"]["61'-75'"] == 5
    assert cc_bot["total_anotados"] == 15

    pat_cor = mp.calcular_55_patrones_equipo({"xg": 1.1, "posesion_pct": 50.0, "faltas": 12.0}, timeline_goles=cc_cor)
    pat_bot = mp.calcular_55_patrones_equipo({"xg": 1.2, "posesion_pct": 52.0, "faltas": 11.0}, timeline_goles=cc_bot)
    assert pat_cor.get("P35_Peso_Goles_Tardios") is None

    _, _, _, alertas = mi.aplicar_reglas_matchup_55(pat_cor, pat_bot, 0.35, 0.30, 0.35, "Coritiba", "Botafogo")
    assert not any("Patrón #35" in a or "76'-90'" in a for a in alertas)

    rep_tel = mi.generar_reporte_xai_telegram(
        "Coritiba", "Botafogo", pat_cor, pat_bot, {"local": 0.35, "empate": 0.30, "visita": 0.35},
        alertas, {}, {}, cc_cor, cc_bot,
    )
    assert "Patrón #35" not in rep_tel
    assert "76'-90'" not in rep_tel

    ver = mi.generar_veredicto_tactico_partido(
        "Coritiba", "Botafogo", pat_cor, pat_bot, {"local": 0.35, "empate": 0.30, "visita": 0.35},
        {}, {}, cc_cor, cc_bot,
    )
    assert "76'-90'" not in ver
    assert "Cobertura de minutos insuficiente" in ver

    doc_l = {"equipo": {"nombre": "Coritiba"}, "partidos": partidos_coritiba}
    doc_v = {"equipo": {"nombre": "Botafogo"}, "partidos": partidos_botafogo}
    analisis = MagicMock()
    analisis.doc_local = doc_l
    analisis.doc_visitante = doc_v
    analisis.nombre_local = "Coritiba"
    analisis.nombre_visitante = "Botafogo"
    analisis.grupos = {}
    analisis.selecciones = []
    analisis.mercados = []
    analisis.motor = None

    textos = []
    with patch("matplotlib.axes.Axes.text") as mock_text:
        with tempfile.TemporaryDirectory() as td:
            ce.generar_cuadro_auditoria_15_partidos_png(analisis, doc_l, doc_v, destino=os.path.join(td, "aud.png"))
            for c in mock_text.call_args_list:
                if len(c[0]) >= 3 and isinstance(c[0][2], str):
                    textos.append(c[0][2])

    banner_cron = [t for t in textos if "CRONOLOGÍA" in t]
    assert banner_cron
    assert all("ALERTA CRONOLÓGICA" not in t for t in textos)
    assert all("76'-90'" not in t for t in textos if t != "76'-90'" and "TIMELINE DE GOLES" not in t)
    assert "sin cobertura de minutos" in banner_cron[0]
    assert "—" in textos
    assert "15" in textos and "10" in textos and "5" in textos


def test_bloque_b_fuente_unica_verdad_doble_adn_en_telegram_supercuadro_y_auditoria_15p() -> None:
    import informe as inf
    import motor_mercados as mm

    def _mk_doc(nombre: str, pos_c: float, pos_o: float, fal_c: float, fal_o: float, x1_c: float, x2_c: float, x1_o: float, x2_o: float) -> dict:
        pts = []
        for i in range(15):
            cond = "casa" if i < 8 else "fuera"
            is_c = (cond == "casa") if nombre == "Cruzeiro" else (cond == "fuera")
            pos = pos_c if is_c else pos_o
            fal = fal_c if is_c else fal_o
            x1 = x1_c if is_c else x1_o
            x2 = x2_c if is_c else x2_o
            pts.append({
                "id_partido": f"{nombre}_{i}",
                "fecha": f"2026-10-{min(28, i + 1):02d}",
                "torneo": "Série A",
                "rival": f"R{i}",
                "condicion": cond,
                "marcador": {"equipo": 1, "rival": 1},
                "goles_equipo": 1,
                "goles_rival": 1,
                "resumen_claves": 20,
                "periodos": {
                    "FT": {"filas": [
                        {"clave": "xg", "equipo": x1 + x2, "rival": 1.1},
                        {"clave": "posesion_pct", "equipo": pos, "rival": 100.0 - pos},
                        {"clave": "faltas", "equipo": fal, "rival": 12.0},
                        {"clave": "remates_totales", "equipo": 12.0, "rival": 10.0},
                        {"clave": "remates_puerta", "equipo": 4.0, "rival": 3.0},
                        {"clave": "corners", "equipo": 5.0, "rival": 4.0},
                        {"clave": "tarjetas_amarillas", "equipo": 2.0, "rival": 2.0},
                        {"clave": "goles_evitados", "equipo": 0.1, "rival": 0.0},
                        {"clave": "despejes", "equipo": 15.0, "rival": 14.0},
                        {"clave": "intercepciones", "equipo": 9.0, "rival": 8.0},
                    ]},
                    "1T": {"filas": [{"clave": "xg", "equipo": x1, "rival": 0.5}, {"clave": "posesion_pct", "equipo": pos, "rival": 100.0 - pos}]},
                    "2T": {"filas": [{"clave": "xg", "equipo": x2, "rival": 0.6}, {"clave": "posesion_pct", "equipo": pos, "rival": 100.0 - pos}]},
                },
            })
        return {"equipo": {"nombre": nombre, "id": nombre}, "partidos": pts}

    doc_l = _mk_doc("Cruzeiro", 55.0, 48.0, 12.5, 14.2, 0.45, 0.72, 0.30, 0.65)
    doc_v = _mk_doc("Palmeiras", 51.0, 46.0, 11.8, 13.5, 0.50, 0.55, 0.40, 0.60)
    analisis = mm.analizar(doc_l, doc_v, "Cruzeiro", "Palmeiras")
    rec = analisis.motor.recursivo
    canon_l = rec["patrones_55_local"]
    canon_v = rec["patrones_55_visita"]

    with patch("motor_inferencia.generar_informe_narrativo_xai") as mxai:
        mxai.return_value = "OK"
        inf.generar_bloque_narrativo_xai_desde_analisis(analisis, doc_l, doc_v)
        kw_tg = mxai.call_args
        p_l_tg = kw_tg.kwargs.get("patrones_local") if (kw_tg.kwargs and "patrones_local" in kw_tg.kwargs) else kw_tg[0][2]
        p_v_tg = kw_tg.kwargs.get("patrones_visita") if (kw_tg.kwargs and "patrones_visita" in kw_tg.kwargs) else kw_tg[0][3]

    al4 = ce._construir_4_alertas_motor_55(
        None, None, analisis, "Cruzeiro", "Palmeiras", 1.1, 1.0, 35.0, 30.0, 35.0, 45.0, 40.0,
        res_red=rec, mitades_local=ce.perfil_mitades(doc_l, None), mitades_vis=ce.perfil_mitades(doc_v, None),
    )

    with patch("motor_inferencia.generar_veredicto_tactico_partido") as mver:
        mver.return_value = "A\nB\n1. C1\n2. C2\n3. C3\n4. C4"
        with tempfile.TemporaryDirectory() as td:
            ce.generar_cuadro_auditoria_15_partidos_png(analisis, doc_l, doc_v, destino=os.path.join(td, "aud_b.png"))
        p_l_a15 = mver.call_args.kwargs["patrones_loc"]
        p_v_a15 = mver.call_args.kwargs["patrones_vis"]

    for clave in ("posesion_pct", "faltas", "P26_Poder_Cierre_2T", "_muestra_etiqueta"):
        assert p_l_tg[clave] == canon_l[clave]
        assert p_l_a15[clave] == canon_l[clave]
        assert p_v_tg[clave] == canon_v[clave]
        assert p_v_a15[clave] == canon_v[clave]

    al_p26 = next(a["detalle"] for a in al4 if a["codigo"] == "Patrón #26")
    assert f"{canon_l['P26_Poder_Cierre_2T']*100:.0f}%" in al_p26
    assert f"{canon_v['P26_Poder_Cierre_2T']*100:.0f}%" in al_p26

    # Caso sin Doble ADN pond. (solo 15P general): no debe etiquetar falsamente "Doble ADN pond."
    analisis_sin_adn = MagicMock()
    analisis_sin_adn.motor = MagicMock(recursivo={}, muestras={}, bloques={})
    pl_15, pv_15 = ce.obtener_patrones_55_canonicos(analisis_sin_adn)
    assert pl_15.get("_muestra_etiqueta") == "15P general"
    assert pv_15.get("_muestra_etiqueta") == "15P general"


def test_bloque_c_conclusion_4_no_cita_1t_y_sin_explosion_2t_en_1t() -> None:
    import motor_mercados as mm

    with open("motor_mercados.py", "r", encoding="utf-8") as f:
        src_mm = f.read()
    assert "Explosión en 2T" not in src_mm

    #Caso 1: Top 5 trae 1T Menos de 1.5 Goles (sin mercado de 2T) y FT Menos de 3.5 Goles
    top5_1t = [
        {"rank": 1, "etiqueta": "1ra Mitad : Menos de 1.5 Goles (80.0%) (Duelo de estudio en 1T)", "periodo": "1T", "evento": "under_1.5", "probabilidad": 0.80},
        {"rank": 2, "etiqueta": "Goles Totales (FT): -3.5 Goles", "periodo": "FT", "evento": "under_3.5", "probabilidad": 0.83},
    ]
    ver = mi.generar_veredicto_tactico_partido(
        "Coritiba", "Santos",
        {"xg": 1.0, "xg_2t": 0.55, "posesion_pct": 50.0, "faltas": 12.0, "P26_Poder_Cierre_2T": 0.54},
        {"xg": 1.1, "xg_2t": 0.60, "posesion_pct": 50.0, "faltas": 11.0, "P26_Poder_Cierre_2T": 0.56},
        {"local": 0.34, "empate": 0.30, "visita": 0.36},
        {"victorias": 5, "sin_derrotas": 10, "n_partidos": 15},
        {"victorias": 5, "sin_derrotas": 10, "n_partidos": 15},
        {"_meta": {"n_partidos_con_eventos": 15}, "anotados": {"76'-90'": 3}, "recibidos": {"76'-90'": 2}},
        {"_meta": {"n_partidos_con_eventos": 15}, "anotados": {"76'-90'": 4}, "recibidos": {"76'-90'": 2}},
        top5_podio=top5_1t,
    )
    linea_4 = [ln for ln in ver.splitlines() if ln.strip().startswith("4.")][0]
    assert "1ra Mitad" not in linea_4
    assert "Duelo de estudio en 1T" not in linea_4
    assert "La segunda mitad tendrá goles" not in linea_4
    assert "Sin líneas de 2T validadas >= 70%" in linea_4
    assert "Goles Totales (FT): -3.5 Goles" in linea_4

    # Caso 2: Top 5 trae Under en 2T -> NO debe titular "La segunda mitad tendrá goles"
    top5_u2t = [
        {"rank": 1, "etiqueta": "Goles 2T: -1.5 Goles", "periodo": "2T", "evento": "under_1.5", "probabilidad": 0.78},
    ]
    ver_u2t = mi.generar_veredicto_tactico_partido(
        "Coritiba", "Santos",
        {"xg": 1.0, "xg_2t": 0.45, "posesion_pct": 50.0, "faltas": 12.0, "P26_Poder_Cierre_2T": 0.54},
        {"xg": 1.1, "xg_2t": 0.50, "posesion_pct": 50.0, "faltas": 11.0, "P26_Poder_Cierre_2T": 0.56},
        {"local": 0.34, "empate": 0.30, "visita": 0.36},
        {"victorias": 5, "sin_derrotas": 10, "n_partidos": 15},
        {"victorias": 5, "sin_derrotas": 10, "n_partidos": 15},
        {"_meta": {"n_partidos_con_eventos": 15}, "anotados": {"76'-90'": 3}, "recibidos": {"76'-90'": 2}},
        {"_meta": {"n_partidos_con_eventos": 15}, "anotados": {"76'-90'": 4}, "recibidos": {"76'-90'": 2}},
        top5_podio=top5_u2t,
    )
    linea_4_u = [ln for ln in ver_u2t.splitlines() if ln.strip().startswith("4.")][0]
    assert "La segunda mitad tendrá goles" not in linea_4_u
    assert "Contención proyectada en la segunda mitad (2T)" in linea_4_u
    assert "Goles 2T: -1.5 Goles" in linea_4_u


def test_bloque_f_mensaje_cobertura_sin_variables_comparables_no_contradictorio() -> None:
    from decision_cobertura import evaluar_decision_cobertura

    doc_solo_a = {
        "identidad_verificada": True,
        "equipo": {"nombre": "Corinthians", "id": "cor1"},
        "partidos": [
            {
                "id_partido": f"a_{i}",
                "fecha": f"2026-10-{1 + i:02d}",
                "torneo": "BRAZIL: Serie A",
                "condicion": "casa",
                "marcador": {"equipo": 1, "rival": 0},
                "periodos": {"FT": {"filas": [{"clave": f"solo_a_{k}", "equipo": 1.0, "rival": 0.0} for k in range(6)]}},
            }
            for i in range(15)
        ],
    }
    doc_solo_b = {
        "identidad_verificada": True,
        "equipo": {"nombre": "Botafogo", "id": "bot1"},
        "partidos": [
            {
                "id_partido": f"b_{i}",
                "fecha": f"2026-10-{1 + i:02d}",
                "torneo": "Série A",
                "condicion": "fuera",
                "marcador": {"equipo": 1, "rival": 0},
                "periodos": {"FT": {"filas": [{"clave": f"solo_b_{k}", "equipo": 1.0, "rival": 0.0} for k in range(6)]}},
            }
            for i in range(15)
        ],
    }
    dec = evaluar_decision_cobertura(doc_solo_a, doc_solo_b)
    assert dec.estado == "cobertura_insuficiente"
    assert "sin variables comparables" in dec.alerta_descriptiva.lower()
    assert "solo 0 de 3 partidos emparejados requeridos comparten al menos 5 claves métricas comunes en FT" in dec.alerta_descriptiva
    assert "(mínimo requerido: 3)" not in dec.alerta_descriptiva


def test_bloque_g_encabezado_partido_con_zona_o_sin_partido_programado() -> None:
    import servicio_mercados as sm

    res_fix = sm.resolver_fecha_encuentro_lima("2026-11-19T00:30:00Z", es_partido_programado=True)
    assert res_fix["etiqueta_fecha"] == "Partido: 2026-11-18 19:30 America/Lima"
    assert res_fix["hora_inicio_partido"] == "2026-11-19T00:30:00Z"
    assert res_fix["fixture_confirmado"] is True
    assert res_fix["fuente_fecha"] == "fixture"

    res_no = sm.resolver_fecha_encuentro_lima(None, es_partido_programado=False)
    assert res_no["etiqueta_fecha"] == "Sin partido programado encontrado"
    assert res_no["fecha_partido"] is None
    assert res_no["fixture_confirmado"] is False
    assert res_no["fuente_fecha"] == "fallback"
    assert "Análisis del" not in res_no["etiqueta_fecha"]

    # Auditoría 15P PNG muestra la etiqueta completa con hora/zona o 'Sin partido programado encontrado'
    analisis = MagicMock()
    analisis.metadatos = dict(res_fix, equipo_local="Corinthians", equipo_visitante="Botafogo", competicion="Serie A")
    analisis.motor = None
    analisis.selecciones = []
    analisis.mercados = []
    doc_vacio = {"equipo": {"nombre": "X"}, "partidos": []}
    captured = []
    with patch("matplotlib.pyplot.Figure.text") as mock_ft:
        with tempfile.TemporaryDirectory() as td:
            ce.generar_cuadro_auditoria_15_partidos_png(analisis, doc_vacio, doc_vacio, destino=os.path.join(td, "g.png"))
            for c in mock_ft.call_args_list:
                if len(c[0]) >= 3 and isinstance(c[0][2], str):
                    captured.append(c[0][2])
    assert any("Partido: 2026-11-18 19:30 America/Lima" in t for t in captured)

    analisis_sin = MagicMock()
    analisis_sin.metadatos = dict(res_no, equipo_local="Cruzeiro", equipo_visitante="Palmeiras", competicion="Serie A")
    analisis_sin.motor = None
    analisis_sin.selecciones = []
    analisis_sin.mercados = []
    captured_sin = []
    with patch("matplotlib.pyplot.Figure.text") as mock_ft2:
        with tempfile.TemporaryDirectory() as td:
            ce.generar_cuadro_auditoria_15_partidos_png(analisis_sin, doc_vacio, doc_vacio, destino=os.path.join(td, "g2.png"))
            for c in mock_ft2.call_args_list:
                if len(c[0]) >= 3 and isinstance(c[0][2], str):
                    captured_sin.append(c[0][2])
    assert any("Sin partido programado encontrado" in t for t in captured_sin)
    assert all("Análisis del" not in t for t in captured_sin)


def test_bloque_i_repo_vacio_crea_main_placeholder_antes_de_produccion_datos_e_hidrata(tmp_path) -> None:
    import shutil
    import subprocess
    from pathlib import Path
    import persistencia_produccion as pp

    def _git(cwd: Path, *args: str) -> str:
        cp = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )
        return cp.stdout.strip()

    def _instalar_hook_github_vacio(rem_bare: Path) -> None:
        # Simular comportamiento de GitHub en repo vacío: la primera rama recibida pasa a ser HEAD por defecto
        hook_path = rem_bare / "hooks" / "post-receive"
        hook_path.write_text(
            "#!/bin/sh\n"
            "while read oldrev newrev refname; do\n"
            "  cur_head=$(git symbolic-ref HEAD 2>/dev/null || true)\n"
            "  if [ -z \"$cur_head\" ] || ! git show-ref --verify --quiet \"$cur_head\"; then\n"
            "    git symbolic-ref HEAD \"$refname\"\n"
            "  fi\n"
            "done\n",
            encoding="utf-8",
        )
        os.chmod(hook_path, 0o755)

    # Primero verificamos que la guarda SIGUE rechazando si la default remota fuera produccion-datos
    with pytest.raises(ValueError, match="prohibida"):
        pp.validar_rama_datos("produccion-datos", rama_defecto_remota="produccion-datos")

    rem_pub = tmp_path / "publico.git"
    rem_pub.mkdir(parents=True, exist_ok=True)
    _git(rem_pub, "init", "--bare")

    # Demostración de lo que fallaba antes: si en un repo vacío se sube primero `produccion-datos`,
    # queda como rama por defecto (HEAD) y `hidratar_desde_remoto_datos` la rechaza.
    rem_roto = tmp_path / "privado_sin_placeholder.git"
    rem_roto.mkdir(parents=True, exist_ok=True)
    _git(rem_roto, "init", "--bare")
    _instalar_hook_github_vacio(rem_roto)
    seed_roto = tmp_path / "seed_roto"
    seed_roto.mkdir(parents=True, exist_ok=True)
    _git(seed_roto, "init")
    _git(seed_roto, "config", "user.name", "Bot CI")
    _git(seed_roto, "config", "user.email", "bot@ci.local")
    _git(seed_roto, "checkout", "--orphan", "produccion-datos")
    (seed_roto / "README.md").write_text("directo\n", encoding="utf-8")
    _git(seed_roto, "add", "README.md")
    _git(seed_roto, "commit", "-m", "sin placeholder")
    _git(seed_roto, "push", str(rem_roto), "HEAD:refs/heads/produccion-datos")
    assert _git(rem_roto, "symbolic-ref", "HEAD") == "refs/heads/produccion-datos"

    runner_roto = tmp_path / "runner_roto"
    runner_roto.mkdir(parents=True, exist_ok=True)
    _git(runner_roto, "init")
    _git(runner_roto, "remote", "add", "origin", str(rem_pub))
    res_fallo = pp.hidratar_desde_remoto_datos(
        pp.ConfigPersistencia(
            workspace_dir=runner_roto,
            remote="datos",
            rama_datos="produccion-datos",
            remote_url=str(rem_roto),
            habilitada=True,
        )
    )
    assert res_fallo["ok"] is False
    assert res_fallo["codigo_error"] == "rechazada_rama_prohibida"

    # Ahora caso corregido: repo vacío -> inicializar_produccion_limpia -> hidratar_desde_remoto_datos
    rem_priv = tmp_path / "privado_vacio.git"
    rem_priv.mkdir(parents=True, exist_ok=True)
    _git(rem_priv, "init", "--bare")
    _instalar_hook_github_vacio(rem_priv)

    runner = tmp_path / "runner_ci"
    runner.mkdir(parents=True, exist_ok=True)
    _git(runner, "init")
    _git(runner, "config", "user.name", "Bot CI")
    _git(runner, "config", "user.email", "bot@ci.local")
    (runner / "README.md").write_text("publico\n", encoding="utf-8")
    _git(runner, "add", "README.md")
    _git(runner, "commit", "-m", "init public")
    _git(runner, "remote", "add", "origin", str(rem_pub))
    (runner / "informes" / "evaluacion").mkdir(parents=True, exist_ok=True)
    ledger_real = Path(__file__).resolve().parent.parent / "informes" / "evaluacion" / "ledger_oos.jsonl"
    shutil.copy2(ledger_real, runner / "informes" / "evaluacion" / "ledger_oos.jsonl")

    res_init = pp.inicializar_produccion_limpia(
        runner,
        remote="datos",
        remote_url=str(rem_priv),
        rama_datos="produccion-datos",
    )
    assert res_init["ok"] is True
    assert res_init["creada"] is True
    assert res_init["placeholder_creada"] is True
    assert res_init["rama_placeholder"] == "main"

    # HEAD del remoto privado quedó en main, NO en produccion-datos
    assert _git(rem_priv, "symbolic-ref", "HEAD") == "refs/heads/main"
    main_clone = tmp_path / "check_main"
    _git(tmp_path, "clone", "-b", "main", str(rem_priv), str(main_clone))
    assert (main_clone / "README.md").exists()

    # Ahora hidratar en un runner recién clonado debe pasar la guarda con ok=True
    runner2 = tmp_path / "runner_hidrata"
    runner2.mkdir(parents=True, exist_ok=True)
    _git(runner2, "init")
    _git(runner2, "remote", "add", "origin", str(rem_pub))
    cfg = pp.ConfigPersistencia(
        workspace_dir=runner2,
        remote="datos",
        rama_datos="produccion-datos",
        remote_url=str(rem_priv),
        habilitada=True,
    )
    ok_hid = pp.hidratar_desde_remoto_datos(cfg)
    assert ok_hid["ok"] is True
    assert ok_hid["ledger"]["n_final"] == 3
