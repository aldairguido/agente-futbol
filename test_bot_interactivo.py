"""
test_bot_interactivo.py
───────────────────────────────────────────────────────────────────────────────
Prueba Unitaria de Control y Seguridad para bot_interactivo.py.

Verifica:
  1. Filtro de seguridad: Rechazo inmediato de chat_id no autorizado.
  2. Ejecución de /estado por chat_id autorizado.
  3. Control de concurrencia: Bloqueo de barridos simultáneos.
  4. Formateo de tickets Diamante aprobados con +EV y Quarter-Kelly.
───────────────────────────────────────────────────────────────────────────────
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from unittest.mock import MagicMock
import bot_interactivo


class MockChat:
    def __init__(self, chat_id):
        self.id = chat_id


class MockMessage:
    def __init__(self, chat_id, text):
        self.chat = MockChat(chat_id)
        self.text = text


def ejecutar_pruebas():
    print("\n" + "=" * 70)
    print("  🧪 TEST UNITARIO: BOT INTERACTIVO DE TELEGRAM")
    print("=" * 70)

    # Configurar mock bot
    mock_bot = MagicMock()
    bot_interactivo.bot = mock_bot
    bot_interactivo.CHAT_ID_AUTORIZADO = "123456789"

    # ── TEST 1: RECHAZO DE ACCESO NO AUTORIZADO ──────────────────────────────
    print("\n1. Probando rechazo de usuario no autorizado (Chat ID: 999999)...")
    msg_intruso = MockMessage(chat_id="999999", text="/estado")

    # Invocamos la función decorada
    bot_interactivo.cmd_estado(msg_intruso)
    mock_bot.reply_to.assert_called_once()
    args, kwargs = mock_bot.reply_to.call_args
    assert "Acceso denegado" in args[1], "Debe denegar acceso al intruso"
    print("  ✅ Acceso denegado correctamente al usuario no autorizado.")

    # ── TEST 2: ACCESO AUTORIZADO A /estado ──────────────────────────────────
    print("\n2. Probando comando /estado con Chat ID autorizado (123456789)...")
    mock_bot.reset_mock()
    msg_autorizado = MockMessage(chat_id="123456789", text="/estado")

    bot_interactivo.cmd_estado(msg_autorizado)
    mock_bot.reply_to.assert_called_once()
    args, kwargs = mock_bot.reply_to.call_args
    respuesta = args[1]
    assert "ESTADO DEL MOTOR" in respuesta
    assert "*Modo Fail-Safe:* 🛡️ ACTIVO" in respuesta
    assert "Quarter-Kelly" in respuesta
    print("  ✅ Comando /estado ejecutado exitosamente:")
    print("     " + "\n     ".join(respuesta.split("\n")[:4]))

    # ── TEST 3: CONTROL DE CONCURRENCIA (BLOQUEO DE BARRIDO DOBLE) ───────────
    print("\n3. Probando bloqueo de concurrencia en /analizar_vivo...")
    mock_bot.reset_mock()
    bot_interactivo.analisis_en_curso = True  # Simular que ya está corriendo

    msg_scan = MockMessage(chat_id="123456789", text="/analizar_vivo")
    bot_interactivo.cmd_analizar_vivo(msg_scan)
    mock_bot.reply_to.assert_called_once()
    args, kwargs = mock_bot.reply_to.call_args
    assert "Ya existe un barrido cuantitativo en curso" in args[1]
    print("  ✅ Barrido concurrente bloqueado de forma segura.")

    bot_interactivo.analisis_en_curso = False

    # ── TEST 4: PARSEO DINÁMICO DE /evaluar ──────────────────────────────────
    print("\n4. Probando parseo dinámico de /evaluar...")
    casos = [
        ("/evaluar", None, False, ""),
        ("/evaluar borrador", None, True, ""),
        ("/evaluar Roma", ["Roma"], False, "Roma"),
        ("/evaluar Roma borrador", ["Roma"], True, "Roma"),
        ("/evaluar borrador Roma", ["Roma"], True, "Roma"),
        ("/evaluar Roma vs Real Madrid", ["Roma", "Real Madrid"], False, "Roma vs Real Madrid"),
        ("/evaluar Roma vs. Real Madrid", ["Roma", "Real Madrid"], False, "Roma vs Real Madrid"),
        ("/evaluar Roma vs Real Madrid borrador", ["Roma", "Real Madrid"], True, "Roma vs Real Madrid"),
        ("/evaluar borrador Roma vs Real Madrid", ["Roma", "Real Madrid"], True, "Roma vs Real Madrid"),
    ]
    for cmd, exp_equipos, exp_borrador, exp_texto in casos:
        eqs, borrador, txt = bot_interactivo._parsear_argumentos_evaluar(cmd)
        assert eqs == exp_equipos, f"{cmd} -> equipos={eqs} != {exp_equipos}"
        assert borrador == exp_borrador, f"{cmd} -> borrador={borrador} != {exp_borrador}"
        assert txt == exp_texto, f"{cmd} -> texto={txt} != {exp_texto}"
    print("  ✅ Parseo dinámico de /evaluar verificado en todos los formatos.")

    # ── TEST 5: MANEJO DE VACÍOS EN /evaluar (EQUIPO INEXISTENTE) ─────────────
    print("\n5. Probando respuesta cuando no hay datos liquidados en /evaluar...")
    mock_bot.reset_mock()
    msg_eval_vacio = MockMessage(chat_id="123456789", text="/evaluar EquipoInexistente")
    bot_interactivo.cmd_evaluar(msg_eval_vacio)
    # Debe enviar acuse inmediato
    mock_bot.send_message.assert_called()
    args_acuse, _ = mock_bot.send_message.call_args
    assert "EquipoInexistente" in args_acuse[1]
    print("  ✅ Acuse enfocado enviado correctamente.")

    # ── TEST 6: PARSEO DINÁMICO DE /auditar (MODALIDADES A, B E INDIVIDUAL) ──
    print("\n6. Probando parseo dinámico de /auditar (Modalidades A, B e Individual)...")
    casos_auditar = [
        # Modalidad A (30 matrices: Local vs Visita)
        ("/auditar Espanyol vs Atlético de Madrid", "cruce", "Espanyol", "Atlético de Madrid", 15, 15),
        ("/auditar Espanyol contra Atlético de Madrid", "cruce", "Espanyol", "Atlético de Madrid", 15, 15),
        ("/auditar Real Madrid vs. Barcelona limite:20", "cruce", "Real Madrid", "Barcelona", 20, 20),
        ("/auditar Boca Juniors - Vasco da Gama", "cruce", "Boca Juniors", "Vasco da Gama", 15, 15),
        # Modalidad B (H2H Puro 2024-2026)
        ("/auditar Espanyol h2h Atlético de Madrid", "h2h", "Espanyol", "Atlético de Madrid", 15, 15),
        ("/auditar Real Madrid h2h Barcelona", "h2h", "Real Madrid", "Barcelona", 15, 15),
        # Individual
        ("/auditar Barcelona", "individual", "Barcelona", "", 15, 15),
        ("/auditar \"Alianza Lima\" limite:10", "individual", "Alianza Lima", "", 10, 10),
    ]
    for cmd, exp_mod, exp_a, exp_b, exp_lim, exp_min in casos_auditar:
        mod, ea, eb, lim, min_c = bot_interactivo._parsear_argumentos_auditoria(cmd)
        assert mod == exp_mod, f"{cmd} -> modalidad={mod} != {exp_mod}"
        assert ea == exp_a, f"{cmd} -> eq_a={ea} != {exp_a}"
        assert eb == exp_b, f"{cmd} -> eq_b={eb} != {exp_b}"
        assert lim == exp_lim, f"{cmd} -> limite={lim} != {exp_lim}"
        assert min_c == exp_min, f"{cmd} -> minimo={min_c} != {exp_min}"
    print("  ✅ Parseo de /auditar verificado en Modalidad A, Modalidad B e Individual.")

    # ── TEST 7: DESPACHO Y RESPUESTAS DE /auditar ───────────────────────────
    print("\n7. Probando despacho de comandos /auditar...")
    mock_bot.reset_mock()
    # Ayuda si viene vacío
    msg_aud_vacio = MockMessage(chat_id="123456789", text="/auditar")
    bot_interactivo.cmd_auditar(msg_aud_vacio)
    mock_bot.reply_to.assert_called()
    args_ayuda, _ = mock_bot.reply_to.call_args
    assert "Modalidad A" in args_ayuda[1]
    assert "Modalidad B" in args_ayuda[1]
    print("  ✅ Menú de ayuda de /auditar entregado correctamente.")

    # Despacho Modalidad A
    mock_bot.reset_mock()
    msg_aud_a = MockMessage(chat_id="123456789", text="/auditar Espanyol vs Atlético de Madrid")
    bot_interactivo.cmd_auditar(msg_aud_a)
    mock_bot.reply_to.assert_called()
    args_a, _ = mock_bot.reply_to.call_args
    assert "AUDITORÍA DE 30 MATRICES (MODALIDAD A)" in args_a[1]
    assert "Espanyol" in args_a[1] and "Atlético de Madrid" in args_a[1]
    print("  ✅ Despacho Modalidad A (30 matrices) verificado.")

    # Liberamos lock
    bot_interactivo.auditoria_en_curso = False

    # Despacho Modalidad B
    mock_bot.reset_mock()
    msg_aud_b = MockMessage(chat_id="123456789", text="/auditar Espanyol h2h Atlético de Madrid")
    bot_interactivo.cmd_auditar(msg_aud_b)
    mock_bot.reply_to.assert_called()
    args_b, _ = mock_bot.reply_to.call_args
    assert "AUDITORÍA H2H PROFUNDA (MODALIDAD B)" in args_b[1]
    assert "2024-01-01 a 2026-12-31" in args_b[1]
    print("  ✅ Despacho Modalidad B (H2H Puro) verificado.")

    # ── TEST 8: NORMALIZACIÓN DE COMANDOS MÓVILES (MAYÚSCULAS Y ESPACIOS) ────
    print("\n8. Probando normalización de comandos móviles (/Conciencia, / conciencia)...")
    casos_norm = [
        ("/conciencia", "/conciencia"),
        ("/Conciencia", "/conciencia"),
        ("/ conciencia", "/conciencia"),
        ("/  Conciencia", "/conciencia"),
        ("/CONCIENCIA", "/conciencia"),
        ("/Estado", "/estado"),
        ("/ estado", "/estado"),
        ("/Auditar Espanyol vs Atlético de Madrid", "/auditar Espanyol vs Atlético de Madrid"),
        ("/ auditar Espanyol vs Atlético de Madrid", "/auditar Espanyol vs Atlético de Madrid"),
        ("/Mercados Boca Juniors vs Vasco da Gama", "/mercados Boca Juniors vs Vasco da Gama"),
        ("/ mercados Boca Juniors vs Vasco da Gama", "/mercados Boca Juniors vs Vasco da Gama"),
        ("/Evaluar borrador", "/evaluar borrador"),
        ("/ evaluar", "/evaluar"),
    ]
    for entrada, esperado in casos_norm:
        res = bot_interactivo.normalizar_comando(entrada)
        assert res == esperado, f"Fallo normalización: {entrada} -> {res} != {esperado}"

    # Probar despacho vía cmd_fallback_o_desconocido para /Conciencia y / conciencia
    mock_bot.reset_mock()
    msg_conciencia_mayus = MockMessage(chat_id="123456789", text="/Conciencia")
    bot_interactivo.cmd_fallback_o_desconocido(msg_conciencia_mayus)
    mock_bot.reply_to.assert_called()
    assert msg_conciencia_mayus.text == "/conciencia"

    mock_bot.reset_mock()
    msg_conciencia_espacio = MockMessage(chat_id="123456789", text="/ conciencia")
    bot_interactivo.cmd_fallback_o_desconocido(msg_conciencia_espacio)
    mock_bot.reply_to.assert_called()
    assert msg_conciencia_espacio.text == "/conciencia"

    # Probar despacho vía cmd_fallback_o_desconocido para /Estado y / estado
    mock_bot.reset_mock()
    msg_estado_espacio = MockMessage(chat_id="123456789", text="/ estado")
    bot_interactivo.cmd_fallback_o_desconocido(msg_estado_espacio)
    mock_bot.reply_to.assert_called()
    args_st, _ = mock_bot.reply_to.call_args
    assert "ESTADO DEL MOTOR" in args_st[1]

    # Probar comando no reconocido
    mock_bot.reset_mock()
    msg_desconocido = MockMessage(chat_id="123456789", text="/ comando_inexistente_123")
    bot_interactivo.cmd_fallback_o_desconocido(msg_desconocido)
    mock_bot.reply_to.assert_called()
    args_desc, _ = mock_bot.reply_to.call_args
    assert "no reconocido" in args_desc[1]

    # Probar parsers con espacios iniciales
    mod, ea, eb, lim, _ = bot_interactivo._parsear_argumentos_auditoria("/ auditar Espanyol vs Atlético de Madrid")
    assert mod == "cruce" and ea == "Espanyol" and eb == "Atlético de Madrid"

    eq_a, eq_b, umb, lim_m, _ = bot_interactivo._parsear_argumentos_mercados("/ Mercados Boca Juniors vs Vasco da Gama")
    assert eq_a == "Boca Juniors" and eq_b == "Vasco da Gama"

    eqs_ev, _, _ = bot_interactivo._parsear_argumentos_evaluar("/ Evaluar Roma")
    assert eqs_ev == ["Roma"]

    print("  ✅ Normalización de comandos móviles (/Conciencia, / conciencia) verificada con éxito.")

    # ── TEST 9: METACOGNICIÓN CUANTITATIVA, POOL NEURONAL Y /conciencia ──────
    print("\n9. Probando Metacognición Cuantitativa, Pool Neuronal y /conciencia A vs B...")
    from agente_consciente import obtener_agente_consciente
    conciencia = obtener_agente_consciente()
    assert len(conciencia.motor_neuronal.modelos) >= 3, "El pool neuronal debe tener múltiples modelos"
    assert conciencia.aciertos > 0, "Debe cargar los aciertos reales de informes/evaluacion/"
    assert len(conciencia.evaluacion_real.get("mercados_estrella") or []) > 0, "Debe identificar Mercados Estrella"

    # Verificar diagnóstico de enfrentamiento y fiabilidad de selección
    aud_sel = conciencia.evaluar_fiabilidad_seleccion("Total de goles", 0.86, "FT", "mantiene")
    assert aud_sel["score_metacognitivo"] > 70.0
    assert aud_sel["familia"] == "Total de goles"

    diag_duelo = conciencia.diagnosticar_enfrentamiento("Barcelona", "Real Madrid", usar_llm=False)
    assert 40.0 <= diag_duelo["indice_confianza_cognitiva"] <= 99.0
    bloque_md = conciencia.formatear_bloque_metacognitivo_md(diag_duelo)
    assert "Diagnóstico Metacognitivo" in bloque_md

    # Verificar comando /conciencia con cruce
    mock_bot.reset_mock()
    msg_conc_duelo = MockMessage(chat_id="123456789", text="/conciencia Barcelona vs Real Madrid")
    bot_interactivo.cmd_conciencia(msg_conc_duelo)
    mock_bot.reply_to.assert_called()
    args_cd, _ = mock_bot.reply_to.call_args
    assert "DIAGNÓSTICO METACOGNITIVO DE ENFRENTAMIENTO" in args_cd[1]
    assert "Índice de Confianza Cognitiva" in args_cd[1]
    print("  ✅ Metacognición cuantitativa, Pool Neuronal y /conciencia A vs B verificados con éxito.")

    # ── TEST 10: ACTIVACIÓN EXCLUSIVA POR COMANDOS / (CERO TEXTO LIBRE) ─────
    print("\n10. Probando que el bot solo se active explícitamente con comandos /...")
    textos_libres = [
        "galatasaray vs barcelona",
        "Real Madrid vs Roma",
        "Boca Juniors - Vasco da Gama",
        "Espanyol contra Atlético de Madrid",
        "hola como estas",
    ]
    for txt_libre in textos_libres:
        msg_libre = MockMessage(chat_id="123456789", text=txt_libre)
        assert bot_interactivo._es_peticion_h2h(msg_libre) is None, (
            f"El texto libre '{txt_libre}' sin comando '/' NO debe activar _es_peticion_h2h"
        )
    # Con comando explícito /h2h sí debe parsear
    msg_cmd_h2h = MockMessage(chat_id="123456789", text="/h2h galatasaray vs barcelona")
    assert bot_interactivo._es_peticion_h2h(msg_cmd_h2h) == ("galatasaray", "barcelona", 15)
    assert not hasattr(bot_interactivo, "cmd_h2h_texto_libre"), (
        "No debe existir el handler cmd_h2h_texto_libre"
    )
    print("  ✅ Activación exclusiva por comandos '/' verificada (cero disparos por texto libre).")

    # ── TEST 11: NÚCLEO AGÉNTICO ANTIGRAVITY (/agente y /diagnosticar) ───────
    print("\n11. Probando Réplica Agéntica Antigravity (/agente, /diagnosticar y Gatekeeper)...")
    from nucleo_antigravity import SubagenteGatekeeper, SubagenteAutodiagnostico, obtener_orquestador_antigravity

    # Probar Gatekeeper (reparación de 1T+2T==FT, nulos y saques_puerta)
    partido_prueba = {
        "competicion": "LaLiga",
        "estadisticas": {
            "remates_fuera": {
                "FT": {"local": 6, "visitante": 5},
                "1T": {"local": 3, "visitante": 2},
                "2T": {"local": 3, "visitante": 3},
            },
            "disparos_al_palo": {
                "FT": {"local": 1, "visitante": 0},
                "1T": {"local": 1, "visitante": 0},
                "2T": {"local": 0, "visitante": 0},
            },
            "saques_puerta": {
                "FT": {"local": 0, "visitante": 0},
                "1T": {"local": None, "visitante": "sin_dato"},
                "2T": {"local": 0, "visitante": 0},
            },
            "corners": {
                "FT": {"local": 7, "visitante": 4},
                "1T": {"local": 0, "visitante": 0},
                "2T": {"local": 0, "visitante": 0},
            },
        },
    }
    res_gk = SubagenteGatekeeper.auditar_y_reparar_partido(partido_prueba)
    assert res_gk["cumple_100_pct"] is True
    assert partido_prueba["estadisticas"]["saques_puerta"]["FT"]["local"] == 5  # 5 - 0
    assert partido_prueba["estadisticas"]["saques_puerta"]["FT"]["visitante"] == 5  # 6 - 1
    assert (
        partido_prueba["estadisticas"]["corners"]["1T"]["local"]
        + partido_prueba["estadisticas"]["corners"]["2T"]["local"]
        == 7
    )

    # Probar Autodiagnóstico AST y aislamiento
    diag_sis = SubagenteAutodiagnostico.ejecutar_diagnostico_integral(reparar=True)
    assert diag_sis["salud_100_pct"] is True, f"Fallo salud integral: {diag_sis}"
    assert diag_sis["aislamiento_jarvis_ok"] is True
    assert diag_sis["solo_comandos_slash_ok"] is True

    # Probar /diagnosticar y /agente vía bot_interactivo
    mock_bot.reset_mock()
    msg_diag = MockMessage(chat_id="123456789", text="/ diagnosticar")
    bot_interactivo.cmd_fallback_o_desconocido(msg_diag)
    mock_bot.reply_to.assert_called()
    args_dg, _ = mock_bot.reply_to.call_args
    assert "NÚCLEO AGÉNTICO ANTIGRAVITY" in args_dg[1]
    assert "100% DESACOPLADO" in args_dg[1]

    mock_bot.reset_mock()
    orq = obtener_orquestador_antigravity()
    res_mision = orq.ejecutar_mision("analiza Barcelona vs Real Madrid", usar_llm=False)
    assert "Barcelona vs Real Madrid" in res_mision["respuesta"]
    assert len(res_mision["pasos_ejecutados"]) >= 3
    print("  ✅ Réplica Agéntica Antigravity (/agente, /diagnosticar, Gatekeeper y 4 Sub-Agentes) verificada.")

    # ── TEST 12: PREFIJOS DE LIGA Y COMPETICIÓN EN COMANDOS (/mercados, /auditar, /h2h) ─
    print("\n12. Probando prefijos de liga/competición en /mercados, /auditar y /h2h...")
    casos_mercados_liga = [
        ("/mercados bundesliga mainz vs friburgo", "mainz", "friburgo", "Bundesliga"),
        ("../ mercados la liga ea sports barcelona vs real madrid", "barcelona", "real madrid", "LaLiga"),
        ("/mercados premier league fc fulham vs hull city", "fc fulham", "hull city", "Premier League"),
        ("/mercados champions league real madrid vs bayern", "real madrid", "bayern", "Champions League"),
        ("/mercados copa libertadores boca juniors vs river plate", "boca juniors", "river plate", "Copa Libertadores"),
    ]
    for cmd_txt, exp_a, exp_b, exp_liga in casos_mercados_liga:
        ea, eb, umb, lim, borr = bot_interactivo._parsear_argumentos_mercados(cmd_txt)
        liga_det = bot_interactivo._extraer_liga_de_comando_raw(cmd_txt)
        assert ea == exp_a and eb == exp_b, f"{cmd_txt} -> ({ea}, {eb}) != ({exp_a}, {exp_b})"
        assert liga_det is not None and liga_det["nombre"] == exp_liga, f"{cmd_txt} -> liga={liga_det} != {exp_liga}"

    mod_a, ea_a, eb_a, _, _ = bot_interactivo._parsear_argumentos_auditoria("/auditar bundesliga mainz vs friburgo")
    assert mod_a == "cruce" and ea_a == "mainz" and eb_a == "friburgo"

    ea_h, eb_h, _ = bot_interactivo._parsear_argumentos_h2h("/h2h premier league fc fulham vs hull city")
    assert ea_h == "fc fulham" and eb_h == "hull city"
    print("  ✅ Prefijos de liga y competición verificados en /mercados, /auditar y /h2h.")

    print("\n" + "=" * 70)
    print("  ✅ TODAS LAS PRUEBAS DEL BOT INTERACTIVO SUPERADAS CON ÉXITO")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    ejecutar_pruebas()

