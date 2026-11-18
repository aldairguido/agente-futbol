import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_interactivo as bi
import evaluacion_pronosticos as ep
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


def _init_repos_publico_y_privado(
    tmp_path: Path,
    *,
    rama_defecto_privado: str = "main",
    con_commit_privado: bool = False,
) -> tuple[Path, Path, Path]:
    rem_pub = tmp_path / "publico_origin.git"
    rem_pub.mkdir(parents=True, exist_ok=True)
    _git(rem_pub, "init", "--bare")

    seed_pub = tmp_path / "_seed_pub"
    seed_pub.mkdir(parents=True, exist_ok=True)
    _git(seed_pub, "init")
    _git(seed_pub, "config", "user.name", "Seed Pub")
    _git(seed_pub, "config", "user.email", "seed@pub.local")
    (seed_pub / "README.md").write_text("repo publico\n", encoding="utf-8")
    _git(seed_pub, "add", "README.md")
    _git(seed_pub, "commit", "-m", "init public main")
    _git(seed_pub, "branch", "-M", "main")
    _git(seed_pub, "remote", "add", "origin", str(rem_pub))
    _git(seed_pub, "push", "-u", "origin", "main")

    rem_priv = tmp_path / "privado_datos.git"
    rem_priv.mkdir(parents=True, exist_ok=True)
    _git(rem_priv, "init", "--bare")
    if con_commit_privado:
        seed_priv = tmp_path / "_seed_priv"
        seed_priv.mkdir(parents=True, exist_ok=True)
        _git(seed_priv, "init")
        _git(seed_priv, "config", "user.name", "Seed Priv")
        _git(seed_priv, "config", "user.email", "seed@priv.local")
        (seed_priv / "README.md").write_text("datos\n", encoding="utf-8")
        _git(seed_priv, "add", "README.md")
        _git(seed_priv, "commit", "-m", "init private default")
        _git(seed_priv, "branch", "-M", rama_defecto_privado)
        _git(seed_priv, "remote", "add", "origin", str(rem_priv))
        _git(seed_priv, "push", "-u", "origin", rama_defecto_privado)
        _git(rem_priv, "symbolic-ref", "HEAD", f"refs/heads/{rama_defecto_privado}")

    runner = tmp_path / "runner"
    _git(tmp_path, "clone", str(rem_pub), str(runner))
    _git(runner, "config", "user.name", "Bot R15")
    _git(runner, "config", "user.email", "bot@r15.local")
    (runner / "informes" / "pronosticos").mkdir(parents=True, exist_ok=True)
    (runner / "informes" / "evaluacion").mkdir(parents=True, exist_ok=True)
    return rem_pub, rem_priv, runner


def _fake_conciencia():
    return SimpleNamespace(
        estado_adaptativo="calibrado",
        fase_cognitiva="reposo",
        nivel_madurez=42,
        aciertos=3,
        errores=0,
        win_rate=1.0,
        evaluacion_real={"brier": 0.19},
        modelo="gemini-2.5-flash",
        motor_neuronal=SimpleNamespace(modelos=[1]),
    )


def test_ronda15_falta_remoto_url_desactiva_nunca_usa_github_token_y_alerta_estado(
    tmp_path, monkeypatch, capsys
):
    _, _, runner = _init_repos_publico_y_privado(tmp_path)
    monkeypatch.setenv("PERSISTENCIA_PRODUCCION_ACTIVA", "1")
    monkeypatch.delenv("PERSISTENCIA_REMOTO_URL", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_NO_USAR_ESTE_TOKEN_PUBLICO_12345")

    cfg = pp.ConfigPersistencia.desde_entorno(runner)
    out_log = capsys.readouterr().out
    assert cfg.habilitada is False
    assert cfg.codigo_estado == "desactivada_falta_remoto_url"
    assert "PERSISTENCIA_REMOTO_URL" in out_log
    assert "ghp_NO_USAR_ESTE_TOKEN_PUBLICO_12345" not in out_log

    # Intentar sincronizar/hidratar debe bloquearse antes de tocar git
    res_hid = pp.hidratar_desde_remoto_datos(cfg)
    assert res_hid["ok"] is False
    assert res_hid["codigo_error"] == "desactivada_falta_remoto_url"
    res_sync = pp.sincronizar_y_push_datos(cfg, mensaje_commit="no debe ocurrir")
    assert res_sync["ok"] is False and res_sync["pushed"] is False

    # Cableado en bot_interactivo: cola None, /estado visible y aviso único en Telegram
    bi.reiniciar_estado_persistencia()
    bi.configurar_persistencia_bot(cfg=None, cola=None)
    monkeypatch.setattr(bi, "BASE_DIR", runner)
    monkeypatch.setattr(bi, "TOKEN", "999999:TG_FAKE_BOT_TOKEN")
    monkeypatch.setattr(bi, "CHAT_ID_AUTORIZADO", "123456")

    alertas = []
    respuestas_estado = []
    monkeypatch.setattr(bi.bot, "send_message", lambda cid, txt, **kw: alertas.append((cid, txt)))
    monkeypatch.setattr(bi, "enviar_seguro", lambda _b, _m, txt, **kw: respuestas_estado.append(txt))
    import agente_consciente as ac
    monkeypatch.setattr(ac, "obtener_agente_consciente", _fake_conciencia)

    assert bi.obtener_cola_persistencia() is None
    assert len(alertas) == 1
    assert "PERSISTENCIA_REMOTO_URL" in alertas[0][1]

    # Llamadas repetidas a /estado muestran la advertencia pero NO duplican alerta Telegram
    bi.cmd_estado(SimpleNamespace(chat=SimpleNamespace(id=123456)))
    bi.cmd_estado(SimpleNamespace(chat=SimpleNamespace(id=123456)))
    assert len(alertas) == 1
    assert len(respuestas_estado) == 2
    assert "⚠️ Desactivada" in respuestas_estado[0]
    assert "PERSISTENCIA_REMOTO_URL" in respuestas_estado[0]
    bi.reiniciar_estado_persistencia()


def test_ronda15_rechaza_url_igual_origin_variantes_y_reporta_en_estado(
    tmp_path, monkeypatch
):
    rem_pub, _, runner = _init_repos_publico_y_privado(tmp_path)
    # Simula un repo clonado de GitHub público y comprueba variantes HTTPS/SSH/sin .git
    _git(runner, "remote", "set-url", "origin", "https://github.com/aldairguido/agente-futbol.git")
    variantes_iguales = [
        "https://github.com/aldairguido/agente-futbol.git",
        "https://github.com/aldairguido/agente-futbol",
        "https://github.com/aldairguido/agente-futbol/",
        "git@github.com:aldairguido/agente-futbol.git",
    ]
    for url_var in variantes_iguales:
        monkeypatch.setenv("PERSISTENCIA_PRODUCCION_ACTIVA", "1")
        monkeypatch.setenv("PERSISTENCIA_REMOTO_URL", url_var)
        cfg = pp.ConfigPersistencia.desde_entorno(runner)
        assert cfg.habilitada is False, url_var
        assert cfg.codigo_estado == "rechazada_url_igual_origin", url_var

    # También rechaza coincidencia exacta contra un remoto bare local (origin == rem_pub)
    _git(runner, "remote", "set-url", "origin", str(rem_pub))
    monkeypatch.setenv("PERSISTENCIA_REMOTO_URL", str(rem_pub))
    cfg_loc = pp.ConfigPersistencia.desde_entorno(runner)
    assert cfg_loc.habilitada is False
    assert cfg_loc.codigo_estado == "rechazada_url_igual_origin"

    res_smoke = pp.ejecutar_smoke_prueba(runner, remote_url=str(rem_pub), rama_pruebas="pruebas-persistencia-datos")
    assert res_smoke["ok"] is False
    assert res_smoke["codigo_error"] == "rechazada_url_igual_origin"
    assert "refs/heads/pruebas-persistencia-datos" not in _git(rem_pub, "for-each-ref", "--format=%(refname)", "refs/heads")

    bi.reiniciar_estado_persistencia()
    bi.configurar_persistencia_bot(cfg=None, cola=None)
    monkeypatch.setattr(bi, "BASE_DIR", runner)
    alertas = []
    monkeypatch.setattr(bi, "_alerta_persistencia_fn", alertas.append)
    st = bi.obtener_estado_persistencia()
    assert st["estado"] == "rechazada_url_igual_origin"
    assert "⛔ Rechazada" in bi.formatear_linea_estado_persistencia()
    assert len(alertas) == 1
    bi.reiniciar_estado_persistencia()


def test_ronda15_guardia_impide_rama_por_defecto_estatica_y_dinamica(tmp_path):
    _, rem_priv_tronco, runner = _init_repos_publico_y_privado(
        tmp_path,
        rama_defecto_privado="tronco",
        con_commit_privado=True,
    )

    # Guard estática para main / master / principal
    for rama_proh in ("main", "Main", "master", "principal", "PRINCIPAL"):
        cfg_p = pp.ConfigPersistencia(
            workspace_dir=runner,
            remote="datos",
            remote_url=str(rem_priv_tronco),
            rama_datos=rama_proh,
            habilitada=True,
        )
        ok, cod, _ = pp.validar_config_persistencia(cfg_p)
        assert ok is False and cod == "rechazada_rama_prohibida"

    # Guard dinámica contra HEAD remoto real ('tronco')
    cfg_dyn = pp.ConfigPersistencia(
        workspace_dir=runner,
        remote="datos",
        remote_url=str(rem_priv_tronco),
        rama_datos="tronco",
        habilitada=True,
    )
    hid_dyn = pp.hidratar_desde_remoto_datos(cfg_dyn)
    assert hid_dyn["ok"] is False
    assert hid_dyn["codigo_error"] == "rechazada_rama_prohibida"

    res_smoke = pp.ejecutar_smoke_prueba(runner, remote_url=str(rem_priv_tronco), rama_pruebas="tronco")
    assert res_smoke["ok"] is False
    assert res_smoke["hidratacion"]["codigo_error"] == "rechazada_rama_prohibida"

    try:
        pp.inicializar_produccion_limpia(runner, remote_url=str(rem_priv_tronco), rama_datos="main")
        raised = False
    except ValueError:
        raised = True
    assert raised is True


def test_ronda15_hidratacion_remoto_valido_sin_rama_y_aviso_unico_ante_fallo_auth(
    tmp_path, monkeypatch
):
    _, rem_priv, runner = _init_repos_publico_y_privado(tmp_path, con_commit_privado=False)

    # 1) Remoto privado válido pero SIN la rama produccion-datos todavía -> ok=True, rama_existe=False
    cfg_ok_vacio = pp.ConfigPersistencia(
        workspace_dir=runner,
        remote="datos",
        remote_url=str(rem_priv),
        rama_datos="produccion-datos",
        habilitada=True,
        backoff_base_s=0.02,
        max_reintentos_push=1,
    )
    hid_vacio = pp.hidratar_desde_remoto_datos(cfg_ok_vacio)
    assert hid_vacio["ok"] is True
    assert hid_vacio["rama_existe"] is False
    assert hid_vacio["n_pronosticos_hidratados"] == 0
    assert hid_vacio["ledger"]["n_final"] == 0

    bi.reiniciar_estado_persistencia()
    alertas = []
    monkeypatch.setattr(bi, "_alerta_persistencia_fn", alertas.append)
    st_hid = bi.registrar_resultado_persistencia(hid_vacio, contexto="hidratacion_inicial", rama_datos="produccion-datos")
    assert st_hid["estado"] == "ok"
    assert alertas == []

    # 2) Fallo de remoto (simula URL privada inalcanzable / token vencido): alerta EXACTAMENTE 1 vez y censura secretos
    tok_secreto = "github_pat_SECRETO_VENCIDO_R15_ABCDE"
    bad_url = "https://github.com/aldairguido/repo-privado-inexistente-r15.git"
    monkeypatch.setenv("PERSISTENCIA_REMOTO_TOKEN", tok_secreto)
    monkeypatch.setenv("PERSISTENCIA_REMOTO_URL", bad_url)
    cfg_bad = pp.ConfigPersistencia(
        workspace_dir=runner,
        remote="datos",
        remote_url=str(tmp_path / "remoto_que_no_existe_y_falla.git"),
        remote_token=tok_secreto,
        rama_datos="produccion-datos",
        habilitada=True,
        backoff_base_s=0.02,
        max_reintentos_push=1,
    )
    hid_bad = pp.hidratar_desde_remoto_datos(cfg_bad)
    assert hid_bad["ok"] is False
    assert hid_bad["codigo_error"] == "error_remoto_auth_o_red"
    assert tok_secreto not in (hid_bad.get("error") or "")

    bi.reiniciar_estado_persistencia()
    bi.configurar_persistencia_bot(cfg=cfg_bad)
    cola = bi.obtener_cola_persistencia()
    assert cola is not None

    # Simulamos error de hidratación + dos fallos consecutivos en cola
    bi.registrar_resultado_persistencia(
        {"ok": False, "error": f"fatal auth token={tok_secreto} on {bad_url}"},
        contexto="hidratacion_inicial",
        rama_datos="produccion-datos",
    )
    r1 = cola.encolar(tipo="mercados_pre", id_trabajo="j1", mensaje_commit="fallo 1", esperar=True, timeout=15.0)
    r2 = cola.encolar(tipo="mercados_pre", id_trabajo="j2", mensaje_commit="fallo 2", esperar=True, timeout=15.0)
    assert r1["ok"] is False and r2["ok"] is False

    # Solo UNA alerta en todo el episodio de fallo, sin token ni URL privada
    assert len(alertas) == 1
    assert tok_secreto not in alertas[0]
    assert "repo-privado-inexistente-r15" not in alertas[0]

    st_err = bi.obtener_estado_persistencia()
    assert st_err["estado"] == "error"
    assert st_err["fallos_consecutivos"] == 3
    linea_est = bi.formatear_linea_estado_persistencia()
    assert "🔴 FALLO" in linea_est
    assert tok_secreto not in linea_est

    # 3) Recuperación cuando el remoto vuelve a estar disponible -> limpia fallo y rearma alertas
    cola.cfg.remote_url = str(rem_priv)
    r_ok = cola.encolar(
        tipo="mercados_pre",
        id_trabajo="j_ok",
        mensaje_commit="recuperado",
        esperar=True,
        timeout=15.0,
    )
    cola.cerrar(timeout=5.0)
    assert r_ok["ok"] is True
    st_rec = bi.obtener_estado_persistencia()
    assert st_rec["estado"] == "ok"
    assert st_rec["fallos_consecutivos"] == 0
    assert st_rec["aviso_error_enviado"] is False
    assert "🟢 Activa" in bi.formatear_linea_estado_persistencia()
    bi.configurar_persistencia_bot(cola=None)
    bi.reiniciar_estado_persistencia()


def test_ronda15_inicializar_produccion_limpia_filtra_pruebas_e_idempotente(tmp_path):
    rem_pub, rem_priv, runner = _init_repos_publico_y_privado(tmp_path)

    # Construimos un ledger base igual al real + 1 fila sintética de prueba encadenada
    ledger_real = Path(ROOT) / "informes" / "evaluacion" / "ledger_oos.jsonl"
    raw_lines = [ln for ln in ledger_real.read_text(encoding="utf-8").splitlines() if ln.strip()]
    dst_ledger = runner / "informes" / "evaluacion" / "ledger_oos.jsonl"
    dst_ledger.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")

    # Añadimos una fila ficticia que debe ser excluida por inicializar_produccion_limpia
    pp.reconstruir_ledger_desde_remoto(
        ledger_real.read_text(encoding="utf-8"),
        entradas_extra=[
            {
                "estado_registro": "simulado_prueba",
                "es_registro_prueba_mentira": True,
                "id_trabajo": "smoke_mentira_x",
                "id_partido": "SMOKE_X",
                "partido": "Equipo Prueba A vs Equipo Prueba B",
                "fecha": "2026-11-17",
            }
        ],
        destino_path=dst_ledger,
    )
    assert len(ep.cargar_ledger_oos(str(dst_ledger))) == len(raw_lines) + 1

    res_init = pp.inicializar_produccion_limpia(
        runner,
        remote="datos",
        remote_url=str(rem_priv),
        rama_datos="produccion-datos",
    )
    assert res_init["ok"] is True
    assert res_init["creada"] is True
    assert res_init["ya_existia"] is False
    assert res_init["n_entradas"] == 3
    assert res_init["partidos"] == [
        "Turkiye vs Belgium",
        "Italy vs France",
        "Colombia vs Chile",
    ]

    # El origin público NO debe haber recibido produccion-datos
    heads_pub = _git(rem_pub, "for-each-ref", "--format=%(refname)", "refs/heads")
    assert "produccion-datos" not in heads_pub

    # Clonar produccion-datos del remoto privado: solo el ledger limpio y cadena SHA-256 intacta
    verif = tmp_path / "verif_prod"
    _git(tmp_path, "clone", "-b", "produccion-datos", str(rem_priv), str(verif))
    archivos_rama = sorted(
        p.relative_to(verif).as_posix()
        for p in verif.rglob("*")
        if p.is_file() and ".git" not in p.parts
    )
    assert archivos_rama == ["informes/evaluacion/ledger_oos.jsonl"]
    ver_cad = ep.verificar_cadena_ledger_oos(str(verif / "informes" / "evaluacion" / "ledger_oos.jsonl"))
    assert ver_cad["ok"] is True and ver_cad["entradas"] == 3
    regs = ep.cargar_ledger_oos(str(verif / "informes" / "evaluacion" / "ledger_oos.jsonl"))
    assert all(not r.get("es_registro_prueba_mentira") for r in regs)

    # Segunda ejecución es idempotente y no sobrescribe
    res_init_2 = pp.inicializar_produccion_limpia(
        runner,
        remote="datos",
        remote_url=str(rem_priv),
        rama_datos="produccion-datos",
    )
    assert res_init_2["ok"] is True
    assert res_init_2["creada"] is False
    assert res_init_2["ya_existia"] is True


def test_ronda15_ledger_local_base_limpio_y_bot_24_7_sin_tocar():
    ledger_real = Path(ROOT) / "informes" / "evaluacion" / "ledger_oos.jsonl"
    ver = ep.verificar_cadena_ledger_oos(str(ledger_real))
    assert ver["ok"] is True
    assert ver["entradas"] == 3
    regs = ep.cargar_ledger_oos(str(ledger_real))
    assert [r.get("partido") for r in regs] == [
        "Turkiye vs Belgium",
        "Italy vs France",
        "Colombia vs Chile",
    ]
    assert all(not r.get("es_registro_prueba_mentira") for r in regs)

    bot_main = subprocess.check_output(
        ["git", "show", "main:.github/workflows/bot_24_7.yml"],
        cwd=ROOT,
        encoding="utf-8",
    ).replace("\r\n", "\n")
    bot_disk = (Path(ROOT) / ".github" / "workflows" / "bot_24_7.yml").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert bot_main == bot_disk


def test_ronda15_auth_git_anula_extraheader_heredado_y_censura_anotacion_ci(
    tmp_path, monkeypatch, capsys
):
    import base64
    import http.server
    import threading

    seen_auths = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen_auths.append(self.headers.get_all("Authorization") or [])
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ws = tmp_path / "ws_checkout"
        ws.mkdir(parents=True, exist_ok=True)
        _git(ws, "init")
        _git(ws, "config", f"http.http://127.0.0.1:{port}/.extraheader", "AUTHORIZATION: basic PUBLIC_GH_TOKEN_B64")

        tok_priv = "github_pat_PRIVADO_NO_DEBE_FILTRAR_123"
        url_priv = f"http://127.0.0.1:{port}/privado/datos.git"
        monkeypatch.setenv("PERSISTENCIA_REMOTO_TOKEN", tok_priv)
        monkeypatch.setenv("PERSISTENCIA_REMOTO_URL", url_priv)
        cfg = pp.ConfigPersistencia(
            workspace_dir=ws,
            remote="datos",
            remote_url=url_priv,
            remote_token=tok_priv,
            habilitada=True,
        )
        ok_h, _, err_h = pp.detectar_rama_defecto_remota(ws, cfg, url_priv)
        assert ok_h is False
        assert err_h is not None
        assert tok_priv not in err_h
        assert "127.0.0.1" not in err_h

        assert len(seen_auths) >= 1
        assert seen_auths[0] == [
            "Basic " + base64.b64encode(f"x-access-token:{tok_priv}".encode("utf-8")).decode("ascii")
        ]

        pp._emitir_anotacion_ci_segura(
            "init_prod",
            {"ok": False, "codigo_error": "error_remoto_auth_o_red", "error": f"fallo {tok_priv} en {url_priv}"},
        )
        out_ci = capsys.readouterr().out
        assert "::error::init_prod" in out_ci
        assert tok_priv not in out_ci
        assert url_priv not in out_ci
    finally:
        srv.shutdown()


def test_ronda16_ssh_deploy_key_git_ssh_command_censura_y_rechazo_origin(
    tmp_path, monkeypatch, capsys
):
    import re
    import stat

    _, _, runner = _init_repos_publico_y_privado(tmp_path)

    # 1. URL SSH equivalente a origin (tanto si origin es HTTPS como si origin es SSH) se rechaza
    _git(runner, "remote", "set-url", "origin", "https://github.com/aldairguido/agente-futbol.git")
    for url_ssh_eq in (
        "git@github.com:aldairguido/agente-futbol.git",
        "git@github.com:aldairguido/agente-futbol",
        "ssh://git@github.com/aldairguido/agente-futbol.git",
    ):
        monkeypatch.setenv("PERSISTENCIA_PRODUCCION_ACTIVA", "1")
        monkeypatch.setenv("PERSISTENCIA_REMOTO_URL", url_ssh_eq)
        cfg_eq = pp.ConfigPersistencia.desde_entorno(runner)
        assert cfg_eq.habilitada is False, url_ssh_eq
        assert cfg_eq.codigo_estado == "rechazada_url_igual_origin", url_ssh_eq

    _git(runner, "remote", "set-url", "origin", "git@github.com:aldairguido/agente-futbol.git")
    for url_eq_2 in (
        "git@github.com:aldairguido/agente-futbol.git",
        "https://github.com/aldairguido/agente-futbol.git",
    ):
        monkeypatch.setenv("PERSISTENCIA_REMOTO_URL", url_eq_2)
        cfg_eq2 = pp.ConfigPersistencia.desde_entorno(runner)
        assert cfg_eq2.habilitada is False, url_eq_2
        assert cfg_eq2.codigo_estado == "rechazada_url_igual_origin", url_eq_2

    # 2. _censurar_secreto y bot_interactivo._censurar_token cubren claves privadas SSH y URLs SSH
    fake_ssh_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
        "QyNTUxOQAAACD9fakesecretline1234567890ABCDEFG==\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    ssh_url_priv = "git@github.com:aldairguido/agente-futbol-datos.git"
    monkeypatch.setenv("PERSISTENCIA_REMOTO_SSH_KEY", fake_ssh_key)
    monkeypatch.setenv("PERSISTENCIA_REMOTO_URL", ssh_url_priv)

    texto_fuga = (
        f"Error autenticando en {ssh_url_priv} con clave:\n{fake_ssh_key}\n"
        "Linea suelta: QyNTUxOQAAACD9fakesecretline1234567890ABCDEFG=="
    )
    censurado = pp._censurar_secreto(texto_fuga, None, ssh_url_priv, fake_ssh_key)
    assert "BEGIN OPENSSH PRIVATE KEY" not in censurado
    assert "QyNTUxOQAAACD9fakesecretline1234567890ABCDEFG==" not in censurado
    assert "agente-futbol-datos" not in censurado
    assert "***SSH_PRIVATE_KEY***" in censurado
    assert "[remoto-datos]" in censurado

    censurado_bot = bi._censurar_token(texto_fuga)
    assert "BEGIN OPENSSH PRIVATE KEY" not in censurado_bot
    assert "QyNTUxOQAAACD9fakesecretline1234567890ABCDEFG==" not in censurado_bot
    assert "agente-futbol-datos" not in censurado_bot

    # 3. _run_git_con_auth en modo SSH usa GIT_SSH_COMMAND con clave temporal 0600 borrada al terminar
    captura_ssh: dict[str, object] = {}
    real_run = subprocess.run

    def _spy_run(cmd, *args, **kwargs):
        env_call = kwargs.get("env") or {}
        if isinstance(cmd, list) and cmd and cmd[0] == "git" and "GIT_SSH_COMMAND" in env_call:
            ssh_cmd = str(env_call["GIT_SSH_COMMAND"])
            captura_ssh["git_ssh_command"] = ssh_cmd
            m_key = re.search(r'-i\s+"([^"]+)"', ssh_cmd)
            assert m_key is not None, ssh_cmd
            kpath = Path(m_key.group(1))
            captura_ssh["key_path"] = kpath
            captura_ssh["existia_durante_run"] = kpath.exists()
            captura_ssh["contenido_durante_run"] = kpath.read_text(encoding="utf-8")
            captura_ssh["modo_durante_run"] = stat.S_IMODE(kpath.stat().st_mode)
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=128,
                stdout="",
                stderr=f"Permission denied (publickey) for {ssh_url_priv} with {fake_ssh_key}",
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy_run)
    cfg_ssh = pp.ConfigPersistencia(
        workspace_dir=runner,
        remote="datos",
        remote_url=ssh_url_priv,
        remote_ssh_key=fake_ssh_key,
        rama_datos="produccion-datos",
        habilitada=True,
    )
    cp_ssh = pp._run_git_con_auth(
        runner,
        cfg_ssh,
        "ls-remote",
        "--symref",
        ssh_url_priv,
        "HEAD",
        target_url=ssh_url_priv,
        check=False,
    )
    assert cp_ssh.returncode == 128
    assert "BEGIN OPENSSH PRIVATE KEY" not in cp_ssh.stderr
    assert "agente-futbol-datos" not in cp_ssh.stderr
    assert captura_ssh.get("existia_durante_run") is True
    assert str(captura_ssh.get("contenido_durante_run")).strip() == fake_ssh_key.strip()
    assert "StrictHostKeyChecking=accept-new" in str(captura_ssh.get("git_ssh_command"))
    assert "IdentitiesOnly=yes" in str(captura_ssh.get("git_ssh_command"))
    if os.name != "nt":
        assert captura_ssh.get("modo_durante_run") == 0o600
    kpath_final = captura_ssh.get("key_path")
    assert isinstance(kpath_final, Path) and not kpath_final.exists()

    # 4. Anotación CI y workflow incluyen PERSISTENCIA_REMOTO_SSH_KEY sin filtrar la clave
    capsys.readouterr()
    pp._emitir_anotacion_ci_segura(
        "init_prod",
        {"ok": False, "codigo_error": "error_remoto_auth_o_red", "error": f"fallo {fake_ssh_key} en {ssh_url_priv}"},
    )
    out_anot = capsys.readouterr().out
    assert "has_ssh=1" in out_anot
    assert "BEGIN OPENSSH PRIVATE KEY" not in out_anot
    assert "agente-futbol-datos" not in out_anot

    wf_txt = (Path(ROOT) / ".github" / "workflows" / "test_persistencia_datos.yml").read_text(encoding="utf-8")
    assert "secrets.PERSISTENCIA_REMOTO_SSH_KEY" in wf_txt
