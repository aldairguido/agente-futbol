"""
agente_futbol/bot_interactivo.py
───────────────────────────────────────────────────────────────────────────────
Bot Interactivo Bidireccional de Telegram (Control Remoto On-Demand).

Permite interactuar con el Agente Cuantitativo desde el smartphone:
  1. Seguridad y Acceso Restringido:
     - Solo responde a comandos emitidos desde el TELEGRAM_CHAT_ID autorizado.
     - Cualquier intento externo es rechazado automáticamente.
  2. Comandos Principales:
     - /estado: Confirma que el motor está Online, con modo Fail-Safe activo,
       criterio Quarter-Kelly y versión del modelo.
     - /analizar_vivo: Dispara el barrido cuantitativo on-demand de Betsson
       en un hilo asíncrono desacoplado, audita ineficiencias con el Validador
       de Python (+EV >= +5.0%) y reporta los tickets Diamante o notifica si
       no hubo valor.
     - /auditar <Equipo>: Auditoría Histórica Profunda. Extrae hasta 15 partidos
       oficiales finalizados del equipo (los amistosos se descartan y, si un
       partido no trae estadísticas, el escaneo continúa hacia atrás para no
       quedarse corto) con su matriz completa de estadísticas avanzadas (xG,
       xGOT, xA, posesión, remates, ataques, pases, defensa y portería)
       desglosada en FT / 1T / 2T para el equipo y su rival, y devuelve informe
       Markdown + CSV estructurado + JSON.
     - /h2h <Equipo A> vs <Equipo B>: Enfrentamiento directo. Localiza las
       auditorías de ambos equipos (y las regenera si faltan o están viejas) y las
       consolida en un único informe: cobertura combinada, matriz cruzada y
       umbrales ≥ 70% por escenario (local de uno vs visitante del otro).
       Ejemplo: `/h2h galatasaray vs barcelona` (solo se activa con comando `/`).
     - /ayuda: Despliega la guía de comandos.
  3. Desacoplamiento:
     - Opera como demonio en segundo plano (infinity_polling).
     - No bloquea ni interfiere con las ejecuciones programadas por lotes.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import re
import sys
import time
import functools
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# Forzar UTF-8 en stdout y stderr de Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import telebot
from dotenv import load_dotenv

import config

# Se importa a NIVEL DE MÓDULO: los hilos de /mercados y /evaluar usan `servicio`,
# y un import local dentro de una sola función dejaba al otro hilo sin el nombre
# (NameError: name 'servicio' is not defined).
import servicio_mercados as servicio

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)
load_dotenv(override=True)

TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or getattr(config, "TELEGRAM_BOT_TOKEN", "")
).strip().strip('"').strip("'")
CHAT_ID_AUTORIZADO = (
    os.getenv("TELEGRAM_CHAT_ID")
    or getattr(config, "TELEGRAM_CHAT_ID", "")
).strip().strip('"').strip("'")

print(f"[BOT TELEGRAM] 🔑 Token leído (primeros 5 caracteres): '{TOKEN[:5]}' (longitud: {len(TOKEN)})")

# Variable global de control de concurrencia
lock_analisis = threading.Lock()
analisis_en_curso = False
auditoria_en_curso = False


def verificar_credenciales() -> bool:
    """Verifica si el token y chat ID están presentes en las variables de entorno."""
    if not TOKEN:
        print("[BOT TELEGRAM] ❌ ERROR CRÍTICO: 'TELEGRAM_BOT_TOKEN' no está configurado en .env.")
        print("[BOT TELEGRAM] 💡 Para configurarlo:")
        print(f"   1. Abre el archivo .env en {os.path.join(config.BASE_DIR, '.env')}")
        print("   2. Coloca tu token obtenido de @BotFather en TELEGRAM_BOT_TOKEN=<tu_token>")
        print("   3. Coloca tu chat ID numérico en TELEGRAM_CHAT_ID=<tu_chat_id>")
        return False

    if not CHAT_ID_AUTORIZADO:
        print("[BOT TELEGRAM] ⚠️ ADVERTENCIA: 'TELEGRAM_CHAT_ID' no está configurado en .env.")
        print("   El bot iniciará pero solo podrá operar una vez que vincules tu Chat ID.")
    return True


# Inicializar instancia de TeleBot (permite registro de handlers y pruebas unitarias aun sin .env configurado)
bot = telebot.TeleBot(TOKEN if TOKEN else "0000000000:AA_PLACEHOLDER_TOKEN_BOT_TELEGRAM", parse_mode="Markdown", use_class_middlewares=True)


def normalizar_comando(texto: str) -> str:
    """
    Normaliza el comando entrante para teclados móviles (iOS / Android):
      - Elimina puntos o espacios accidentales antes de '/' (ej. '../ mercados' -> '/mercados')
      - Elimina espacios tras '/' (ej. '/ conciencia' -> '/conciencia')
      - Convierte el comando a minúsculas (ej. '/Conciencia' -> '/conciencia')
      - Preserva argumentos posteriores con su capitalización original intacta.
    """
    if not texto:
        return ""
    texto_strip = str(texto).strip()
    if re.match(r"^[.\s]+/", texto_strip):
        texto_strip = re.sub(r"^[.\s]+(?=/)", "", texto_strip)
    if not texto_strip.startswith("/"):
        return texto_strip
    texto_limpio = re.sub(r"^/\s+", "/", texto_strip)
    partes = texto_limpio.split(maxsplit=1)
    comando = partes[0].lower().split("@")[0]
    resto = (" " + partes[1]) if len(partes) > 1 else ""
    return comando + resto


from telebot.handler_backends import BaseMiddleware


class NormalizadorComandosMiddleware(BaseMiddleware):
    def __init__(self):
        self.update_sensitive = True
        self.update_types = ["message"]

    def pre_process_message(self, message, data):
        if getattr(message, "text", None):
            message.text = normalizar_comando(message.text)

    def post_process_message(self, message, data, exception=None):
        pass


bot.setup_middleware(NormalizadorComandosMiddleware())


def _enviar_texto_plano(bot_inst, destino, texto: str, es_reply: bool = False):
    """
    Envía SIN formato, garantizando que la clave parse_mode no viaje en la petición.

    Detalle importante de pyTelegramBotAPI:
        parse_mode = self.parse_mode if (parse_mode is None) else parse_mode
    Es decir, pasar `parse_mode=None` NO quita el formato: restaura el "Markdown"
    por defecto de la instancia y el envío vuelve a fallar con el mismo texto.
    Por eso se pasa la cadena vacía (falsy, se omite del payload) Y se desactiva
    temporalmente el parse_mode del bot, restaurándolo después.
    """
    original = getattr(bot_inst, "parse_mode", None)
    try:
        bot_inst.parse_mode = None
        if es_reply:
            return bot_inst.reply_to(destino, texto, parse_mode="")
        return bot_inst.send_message(destino, texto, parse_mode="")
    finally:
        bot_inst.parse_mode = original


def enviar_seguro(bot_inst, destino, texto: str, es_reply: bool = False,
                  markdown: bool = False):
    """
    Envía un mensaje a Telegram de forma fiable.
    Si el mensaje excede los 4000 caracteres, lo fragmenta automáticamente en partes
    [1/N], [2/N] para cumplir el límite de 4096 caracteres de Telegram sin truncar.
    Con markdown=True intenta Markdown; si falla, envía en texto plano.
    """
    if not texto:
        return None
    from servicio_mercados import partir_mensaje
    partes = partir_mensaje(str(texto), limite=4000)
    ultimo_res = None
    for parte in partes:
        if markdown:
            try:
                if es_reply:
                    ultimo_res = bot_inst.reply_to(destino, parte, parse_mode="Markdown")
                else:
                    ultimo_res = bot_inst.send_message(destino, parte, parse_mode="Markdown")
                continue
            except Exception as err_md:
                print(f"[BOT TELEGRAM] ⚠️ Markdown rechazado ({err_md}); "
                      f"reenviando en texto plano...")
        try:
            ultimo_res = _enviar_texto_plano(bot_inst, destino, parte, es_reply=es_reply)
        except Exception as err_fatal:
            print(f"[BOT TELEGRAM] ❌ Error fatal enviando mensaje a Telegram: {err_fatal}")
            return None
    return ultimo_res


def solo_autorizado(func):
    """
    Decorador de seguridad estricta:
    Bloquea cualquier mensaje que no provenga del TELEGRAM_CHAT_ID registrado en .env.
    Normaliza el texto del mensaje para soportar teclados móviles (/ Conciencia -> /conciencia).
    """
    @functools.wraps(func)
    def wrapper(message, *args, **kwargs):
        if getattr(message, "text", None):
            message.text = normalizar_comando(message.text)
        id_emisor = str(message.chat.id).strip()
        id_esperado = str(CHAT_ID_AUTORIZADO).strip()

        if not id_esperado:
            # Si el chat ID no está en .env, informamos al usuario de su propio ID para que lo configure
            enviar_seguro(
                bot,
                message,
                f"⚠️ *Chat ID no configurado en el servidor.*\n\n"
                f"Tu Chat ID actual es: `{id_emisor}`\n"
                f"Copia este número y colócalo en el archivo `.env`:\n"
                f"`TELEGRAM_CHAT_ID={id_emisor}`",
                es_reply=True
            )
            return

        if id_emisor != id_esperado:
            print(f"[BOT TELEGRAM] ⛔ Intento de acceso NO AUTORIZADO desde Chat ID: {id_emisor}")
            enviar_seguro(
                bot,
                message,
                "⛔ *Acceso denegado.* Este bot cuantitativo es de uso privado y exclusivo de su operador.",
                es_reply=True
            )
            return

        return func(message, *args, **kwargs)
    return wrapper


# ═══════════════════════════════════════════════════════════════════════════════
# MANEJADORES DE COMANDOS
# ═══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["start", "ayuda", "help"])
@solo_autorizado
def cmd_ayuda(message):
    texto = (
        "🤖 *CEREBRO CUANTITATIVO — CONTROL REMOTO PRIVADO*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Bienvenido al panel de control bidireccional del motor de apuestas.\n\n"
        "📋 *Comandos Disponibles:*\n\n"
        "🔹 `/estado`\n"
        "   Verifica si el motor está online, con modo Fail-Safe activo,\n"
        "   estado emocional, madurez adaptativa (XP) y parámetros de bankroll.\n\n"
        "🔹 `/analizar\\_vivo`\n"
        "   Dispara un barrido on-demand en Betsson, audita cuotas y genera\n"
        "   los tickets Diamante (+EV >= +5.0%) con Fraccional Kelly.\n\n"
        "🔹 `/resultado <equipo> <exito|fracaso>`\n"
        "   Registra el resultado de un partido para que el agente aprenda,\n"
        "   evolucione su madurez y calibre su estado emocional.\n"
        "   Ejemplo: `/resultado Torino exito` o `/resultado Real Madrid fracaso`\n\n"
        "🔹 `/auditar <Nombre del Equipo>`\n"
        "   Auditoría Histórica Profunda: extrae la matriz completa de los últimos\n"
        "   15 partidos oficiales del equipo (xG, xGOT, xA, posesión, remates,\n"
        "   ataques, pases, defensa y portería) desglosada en FT / 1T / 2T para el\n"
        "   equipo y su rival. Los amistosos se descartan y, si un partido no trae\n"
        "   estadísticas, el escaneo sigue hacia atrás hasta completar el objetivo.\n"
        "   Devuelve el informe en Markdown y el CSV crudo.\n"
        "   Ejemplo: `/auditar Barcelona` — opcional: `/auditar Barcelona limite:5`\n\n"
        "🔹 `/h2h <Equipo A> vs <Equipo B>`\n"
        "   Enfrentamiento directo. Reutiliza (o genera) las auditorías de ambos\n"
        "   equipos y las consolida en UN ÚNICO informe comparativo: cobertura de\n"
        "   métricas combinada, matriz cruzada (local de uno vs visitante del otro)\n"
        "   y análisis de umbrales ≥ 70%.\n"
        "   Ejemplo: `/h2h galatasaray vs barcelona`\n\n"
        "🔹 `/mercados <Equipo A> vs <Equipo B>`\n"
        "   Analiza los 30 mercados del catálogo y genera un Ticket Gráfico (PNG)\n"
        "   con diseño deportivo estructurado para móvil. Agrupa por categorías\n"
        "   (goles, córners, tarjetas, hándicaps) con compresión horizontal de escaleras\n"
        "   y sección inferior de opciones sensibles a los supuestos.\n"
        "   Opciones: `/mercados A vs B umbral:0.75 limite:10 borrador`\n\n"
        "🔹 `/evaluar`\n"
        "   Auditoría de aciertos: verifica los resultados reales de los partidos\n"
        "   pronosticados y comprueba cuántas selecciones se cumplieron, por banda de\n"
        "   probabilidad original (70-75%, 75-80%, ... 95-100%) y por mercado.\n"
        "   Muestra también los fallos y las tasas por debajo del 70%.\n"
        "   `/evaluar borrador` prepara el resumen sin enviarlo.\n\n"
        "🔹 `/conciencia` o `/memoria`\n"
        "   Muestra el diario de memoria vivencial, estado emocional y aprendizajes.\n"
        "   También acepta cruces: `/conciencia Equipo A vs Equipo B`\n\n"
        "🔹 `/agente <consulta o duelo>` (o `/antigravity`, `/ia`, `/pensar`)\n"
        "   Activa el Núcleo Agéntico Antigravity (bucle ReAct + Equipo de Sub-Agentes:\n"
        "   Explorador, Gatekeeper, Cuantitativo Poisson/Dixon-Coles y Metacognitivo).\n"
        "   Ejemplo: `/agente analiza Barcelona vs Real Madrid` o `/agente mejores mercados`\n\n"
        "🔹 `/diagnosticar` (o `/autodiagnostico`, `/salud`, `/reparar`)\n"
        "   Ejecuta el autodiagnóstico AST de todos los módulos `.py`, verifica las 9\n"
        "   directivas de `AGENTS.md`, audita el aislamiento y sincroniza la memoria.\n\n"
        "🔹 `/ayuda`\n"
        "   Muestra este mensaje explicativo.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ *Modo Paper Trading activo* — Criterio Quarter-Kelly (f/4)."
    )
    enviar_seguro(bot, message, texto, es_reply=True)


@bot.message_handler(commands=["estado", "status"])
@solo_autorizado
def cmd_estado(message):
    global analisis_en_curso
    estado_barrido = "⏳ En ejecución activa" if analisis_en_curso else "🟢 En reposo / Listo"

    from agente_consciente import obtener_agente_consciente
    conciencia = obtener_agente_consciente()
    brier_val = (getattr(conciencia, "evaluacion_real", {}) or {}).get("brier")
    brier_str = f" | Brier: `{brier_val:.4f}`" if isinstance(brier_val, (int, float)) else ""
    pool_modelos = len(getattr(getattr(conciencia, "motor_neuronal", None), "modelos", []) or [1])

    texto = (
        "🤖 *AGENTE CUANTITATIVO AUTÓNOMO — ESTADO DEL MOTOR*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Estado del Demonio:* 🟢 Online (Listener Activo)\n"
        f"• *Núcleo Agéntico:* 🧠 Antigravity ReAct + 4 Sub-Agentes Activos\n"
        f"• *Barrido en curso:* {estado_barrido}\n"
        f"• *Estado Emocional:* `{conciencia.estado_adaptativo.upper()}`\n"
        f"• *Fase Cognitiva:* `{getattr(conciencia, 'fase_cognitiva', 'reposo').upper()}`\n"
        f"• *Nivel de Madurez:* `{conciencia.nivel_madurez} XP`\n"
        f"• *Memoria Vivencial:* {conciencia.aciertos} aciertos | {conciencia.errores} fallos "
        f"(Win Rate: `{conciencia.win_rate:.1%}`{brier_str})\n"
        "• *Modo Fail-Safe:* 🛡️ ACTIVO (Anti-NaN/Inf, Kolmogorov 100%)\n"
        "• *Autenticación:* 🔐 Verificación en tiempo real contra Betsson\n"
        "• *Filtro de Ineficiencia:* 🎯 +EV >= +5.0% (Margen estricto)\n"
        "• *Staking Management:* 📐 Quarter-Kelly (f/4) sin inflado artificial\n"
        "• *Liquidación:* ⚡ Soporte formal de PUSH (Devolución) en Líneas Asiáticas\n"
        "• *Motor de Contingencia:* 📊 Distribución de Poisson Formal\n"
        f"• *Modelo LLM:* 🧠 Google Gemini `{getattr(conciencia, 'modelo', getattr(config, 'GEMINI_MODEL', 'gemini-2.5-flash'))}` "
        f"(Pool rotativo: {pool_modelos} modelos)\n"
        "• *Modo Operativo:* 🧪 Paper Trading (Simulación de Capital)\n"
        f"• *Zona Horaria:* 🕒 `{config.TIMEZONE}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 Envía /agente <consulta o duelo> para activar el razonamiento multi-agente.\n"
        "💡 Envía /diagnosticar para auditar y auto-reparar código, matrices y memoria.\n"
        "💡 Envía /conciencia <Equipo A> vs <Equipo B> para el diagnóstico metacognitivo."
    )
    enviar_seguro(bot, message, texto, es_reply=True)


def _extraer_liga_de_comando_raw(texto: str) -> dict[str, Any] | None:
    """Extrae metadatos de liga/competición si el usuario los indicó al inicio del comando."""
    from fuentes_xg import extraer_liga_de_texto
    texto_norm = normalizar_comando(texto or "")
    cuerpo = re.sub(r"^/\w+(?:@\w+)?\s*", "", texto_norm or "", flags=re.I).strip()
    liga_info, _ = extraer_liga_de_texto(cuerpo)
    return liga_info


def _parsear_argumentos_conciencia(texto: str) -> tuple[str | None, str | None]:
    """
    Extrae equipos opcionales del comando /conciencia:
      /conciencia                           -> (None, None)
      /conciencia Real Madrid               -> ('Real Madrid', None)
      /conciencia Espanyol vs Atlético      -> ('Espanyol', 'Atlético')
    """
    from fuentes_xg import extraer_liga_de_texto
    texto_norm = normalizar_comando(texto or "")
    cuerpo = re.sub(
        r"^/(?:conciencia|memoria|madurez)(?:@\w+)?",
        "", texto_norm or "", flags=re.I
    ).strip().strip("\"'")
    if not cuerpo:
        return None, None
    _, cuerpo_sin_liga = extraer_liga_de_texto(cuerpo)
    cuerpo = (cuerpo_sin_liga or cuerpo).strip()
    partes = re.split(r"\s+(?:vs\.?|contra|h2h|-)\s+", cuerpo, maxsplit=1, flags=re.I)
    partes = [p.strip().strip("\"'") for p in partes if p.strip()]
    if len(partes) >= 2:
        _, a_limpio = extraer_liga_de_texto(partes[0])
        _, b_limpio = extraer_liga_de_texto(partes[1])
        return (a_limpio or partes[0]).strip(), (b_limpio or partes[1]).strip()
    return partes[0], None


@bot.message_handler(commands=["conciencia", "memoria", "madurez"])
@solo_autorizado
def cmd_conciencia(message):
    from agente_consciente import obtener_agente_consciente
    conciencia = obtener_agente_consciente()
    eq_a, eq_b = _parsear_argumentos_conciencia(getattr(message, "text", "") or "")
    texto = conciencia.obtener_resumen_conciencia(equipo_a=eq_a, equipo_b=eq_b, usar_llm=bool(eq_a and eq_b))
    enviar_seguro(bot, message, texto, es_reply=True)


@bot.message_handler(commands=["agente", "antigravity", "ia", "pensar"])
@solo_autorizado
def cmd_agente(message):
    """
    Invoca el Núcleo Agéntico Antigravity (Orquestador ReAct + Equipo de Sub-Agentes:
    Explorador FotMob/Flashscore, Gatekeeper Matemático, Cuantitativo Poisson y Metacognitivo).
    Solo se activa mediante comando explícito con '/'.
    """
    from agente_consciente import obtener_agente_consciente
    conciencia = obtener_agente_consciente()
    texto_msg = normalizar_comando(getattr(message, "text", "") or "")
    cuerpo = re.sub(r"^/(?:agente|antigravity|ia|pensar)(?:@\w+)?", "", texto_msg, flags=re.I).strip()
    resultado_mision = conciencia.ejecutar_mision_agentica(cuerpo or "Resumen estratégico y mejores mercados", usar_llm=True)
    enviar_seguro(bot, message, resultado_mision["respuesta"], es_reply=True)


@bot.message_handler(commands=["diagnosticar", "autodiagnostico", "salud", "reparar"])
@solo_autorizado
def cmd_diagnosticar(message):
    """
    Ejecuta el SubagenteAutodiagnostico + SubagenteGatekeeper sobre todo D:\\agente_futbol:
    verifica compilación AST de todos los módulos .py, aislamiento total de D:\\jarvis,
    activación exclusiva por comandos '/', reglas de AGENTS.md y sincroniza la memoria.
    """
    from agente_consciente import obtener_agente_consciente
    conciencia = obtener_agente_consciente()
    resultado_mision = conciencia.ejecutar_mision_agentica("diagnosticar integridad del sistema", usar_llm=False)
    enviar_seguro(bot, message, resultado_mision["respuesta"], es_reply=True)


@bot.message_handler(commands=["resultado", "acierto", "fallo", "exito", "fracaso"])
@solo_autorizado
def cmd_resultado(message):
    """
    Canal de aprendizaje bidireccional para el Agente Consciente.
    Sintaxis oficial: /resultado <equipo> <exito|fracaso>
    Ejemplos:
      - /resultado Torino exito
      - /resultado Real Madrid fracaso
      - /resultado "Bayern Munich" exito
    """
    from agente_consciente import obtener_agente_consciente
    conciencia = obtener_agente_consciente()

    texto_msg = normalizar_comando(getattr(message, "text", "") or "").strip()
    tokens = texto_msg.split()
    if not tokens:
        return

    cmd = tokens[0].lower().split("@")[0].replace("/", "")
    args = tokens[1:]

    # Manejo de aliases directos (/acierto <equipo>, /fallo <equipo>)
    if cmd in ("acierto", "exito"):
        if not args:
            enviar_seguro(
                bot,
                message,
                f"ℹ️ *Uso:* `/{cmd} <equipo>`\n*Ejemplo:* `/{cmd} Torino`",
                es_reply=True
            )
            return
        equipo = " ".join(args).strip("\"'")
        resultado_real = "exito"
    elif cmd in ("fallo", "fracaso"):
        if not args:
            enviar_seguro(
                bot,
                message,
                f"ℹ️ *Uso:* `/{cmd} <equipo>`\n*Ejemplo:* `/{cmd} Arsenal`",
                es_reply=True
            )
            return
        equipo = " ".join(args).strip("\"'")
        resultado_real = "fracaso"
    else:
        # Comando estándar: /resultado <equipo> <exito|fracaso>
        if len(args) < 2:
            enviar_seguro(
                bot,
                message,
                "ℹ️ *Uso del comando:* `/resultado <equipo> <exito|fracaso>`\n\n"
                "📌 *Ejemplos:*\n"
                "• `/resultado Torino exito`\n"
                "• `/resultado Real Madrid fracaso`\n"
                "• `/resultado \"Bayern Munich\" exito`",
                es_reply=True
            )
            return

        sinonimos_exito = {"exito", "éxito", "ganada", "win", "verde", "acierto", "gana", "won"}
        sinonimos_fracaso = {"fracaso", "perdida", "loss", "rojo", "fallo", "pierde", "lost"}

        ultimo = args[-1].lower().strip("\"'")
        primero = args[0].lower().strip("\"'")

        if ultimo in sinonimos_exito:
            resultado_real = "exito"
            equipo = " ".join(args[:-1]).strip("\"'")
        elif ultimo in sinonimos_fracaso:
            resultado_real = "fracaso"
            equipo = " ".join(args[:-1]).strip("\"'")
        elif primero in sinonimos_exito:
            resultado_real = "exito"
            equipo = " ".join(args[1:]).strip("\"'")
        elif primero in sinonimos_fracaso:
            resultado_real = "fracaso"
            equipo = " ".join(args[1:]).strip("\"'")
        else:
            enviar_seguro(
                bot,
                message,
                "⚠️ *Error de sintaxis:* El resultado debe ser `exito` o `fracaso`.\n\n"
                "📌 *Formato:* `/resultado <equipo> <exito|fracaso>`\n"
                "• Ejemplo: `/resultado Torino exito`\n"
                "• Ejemplo: `/resultado Arsenal fracaso`",
                es_reply=True
            )
            return

    if not equipo:
        enviar_seguro(
            bot,
            message,
            "⚠️ *Error:* Debes indicar el nombre del equipo o partido.\n\n"
            "Ejemplo: `/resultado Torino exito`",
            es_reply=True
        )
        return

    try:
        # Ejecutar asimilación de la experiencia en la memoria persistente
        conciencia.asimilar_resultado_apuesta(
            partido=equipo,
            mercado="General",
            cuota=0.0,
            resultado_real=resultado_real
        )

        icono = "🟢 ÉXITO (Apuesta Ganada)" if resultado_real == "exito" else "🔴 FRACASO (Apuesta Perdida)"
        
        texto_confirmacion = (
            "🧠 *APRENDIZAJE ASIMILADO CON ÉXITO*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Equipo / Partido:* `{equipo}`\n"
            f"• *Resultado Registrado:* {icono}\n"
            f"• *Nivel de Madurez Actual:* `{conciencia.nivel_madurez} XP`\n"
            f"• *Nuevo Estado Emocional:* `{conciencia.estado_adaptativo.upper()}`\n"
            f"• *Identidad Cognitiva:* _{conciencia.identidad_base}_\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Balance Histórico:* {conciencia.aciertos} aciertos | {conciencia.errores} fallos (Win Rate: `{conciencia.win_rate:.1%}`)\n"
            "💾 *Persistencia:* Guardado en disco (`memoria_vivencial.json`)."
        )
        enviar_seguro(bot, message, texto_confirmacion, es_reply=True)
        print(f"[BOT TELEGRAM] ✅ Resultado asimilado: {equipo} -> {resultado_real}. Madurez: {conciencia.nivel_madurez}, Estado: {conciencia.estado_adaptativo}")

    except Exception as e:
        print(f"[BOT TELEGRAM] ❌ Error asimilando resultado: {e}")
        enviar_seguro(
            bot,
            message,
            f"❌ *Error al registrar en la memoria vivencial:*\n`{str(e)}`",
            es_reply=True
        )


@bot.message_handler(commands=["analizar_vivo", "scan", "analizar"])
@solo_autorizado
def cmd_analizar_vivo(message):
    global analisis_en_curso

    with lock_analisis:
        if analisis_en_curso:
            enviar_seguro(
                bot,
                message,
                "⚠️ *Ya existe un barrido cuantitativo en curso.* Por favor espera a que finalice.",
                es_reply=True
            )
            return
        analisis_en_curso = True

    chat_id = message.chat.id
    enviar_seguro(
        bot,
        message,
        "⏳ *Iniciando barrido cuantitativo on-demand en Betsson Perú...*\n\n"
        "El agente está escaneando la cartelera, extrayendo xG y aplicando los "
        "guardrails matemáticos de Poisson y Fraccional Kelly. Te avisaré tan pronto termine.",
        es_reply=True
    )

    # Ejecutar en hilo desacoplado para mantener el bot receptivo
    t = threading.Thread(
        target=_hilo_ejecutar_barrido,
        args=(chat_id,),
        daemon=True
    )
    t.start()


def _hilo_ejecutar_barrido(chat_id: int | str):
    """Ejecuta el barrido de agente.py y formatea los resultados para Telegram."""
    global analisis_en_curso
    try:
        print("[BOT TELEGRAM] 🚀 Iniciando barrido on-demand solicitado por el usuario...")
        import agente
        from gestor_tickets import GestorTickets

        # Ejecutar barrido en modo headless y sin despachar notificación automática duplicada
        resultados_finales, tickets_resumen, ruta_informe = agente.ejecutar_barrido(
            url_especifica=None,
            fecha=None,
            headless=True,
            notificar_telegram_automatico=False,
        )

        simples_diamante = tickets_resumen.get("simples_diamante", [])
        parlays = tickets_resumen.get("parlays_duplas", [])

        total_partidos = len(resultados_finales)
        total_mercados = sum(len(r.get("mercados_disponibles", [])) for r in resultados_finales)
        total_descartados = sum(len(r.get("descartados", [])) for r in resultados_finales)
        total_verdes_brutos = sum(len([c for c in r.get("candidatos", []) if c.get("semaforo") == "VERDE"]) for r in resultados_finales)
        cant_diamantes = len(simples_diamante)
        cant_secundarios = max(0, total_verdes_brutos - cant_diamantes)

        from agente_consciente import obtener_agente_consciente
        conciencia = obtener_agente_consciente()

        if not simples_diamante and not parlays:
            msg_vacio = (
                "🛑 *Barrido finalizado. Cero Tickets Diamante (+EV < +5.0%).*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📊 *EMBUDO Y AUDITORÍA DE MERCADOS:*\n"
                f"• ⚽ *Partidos evaluados:* `{total_partidos}`\n"
                f"• 🔢 *Mercados examinados en Betsson:* `{total_mercados}`\n"
                f"• 🔴 *Descartados (sin valor / +EV < 5%):* `{total_descartados}`\n"
                f"• 🟢 *Opciones secundarias (tendencia):* `{cant_secundarios}` _(no operables)_\n"
                f"• 💎 *Tickets Diamante aprobados:* `0`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🛡️ *Preservación de Capital:* Ningún mercado alcanzó simultáneamente la ventaja "
                "matemática mínima (+EV >= +5.0% bajo Poisson bivariado y Quarter-Kelly >= 0.5%). "
                "Las opciones secundarias por tendencia histórica se omiten para evitar riesgos innecesarios."
            )
            enviar_seguro(bot, chat_id, msg_vacio)
            return

        # Formatear reporte de tickets aprobados con embudo y jerarquía limpia
        partes_msg: list[str] = []
        partes_msg.append("🏆 *INFORME CUANTITATIVO DE APUESTAS (+EV TRADER)*")
        partes_msg.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        partes_msg.append("📊 *EMBUDO Y AUDITORÍA DE MERCADOS:*")
        partes_msg.append(f"• ⚽ *Partidos evaluados:* `{total_partidos}`")
        partes_msg.append(f"• 🔢 *Mercados examinados en Betsson:* `{total_mercados}`")
        partes_msg.append(f"• 🔴 *Descartados (sin valor / +EV < 5%):* `{total_descartados}`")
        partes_msg.append(f"• 🟢 *Opciones secundarias (tendencia):* `{cant_secundarios}` _(sin margen +EV suficiente)_")
        partes_msg.append(f"• 💎 *TICKETS DIAMANTE (+EV VALIDADOS):* *`{cant_diamantes}`*")
        partes_msg.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        partes_msg.append("💡 _Aclaración: Solo se publican como tickets los Diamantes (+EV >= +5.0% y Quarter-Kelly). Las opciones secundarias 'verdes' cumplen tendencia histórica pero no superaron el filtro estricto de ventaja matemática._\n")

        if simples_diamante:
            partes_msg.append("💎 *TICKETS DIAMANTE (+EV VALIDADOS)*\n")
            for s in simples_diamante:
                t_id = str(s.get('id_ticket', '')).replace('_', '\\_')
                partes_msg.append(f"👉 *{t_id}:* *{s['partido']}*")
                partes_msg.append(f"   🏆 *Torneo:* {s['competicion']}")
                partes_msg.append(f"   🎯 *Mercado:* {s['mercado']}")
                partes_msg.append(f"   🎲 *Selección:* *{s['seleccion']}*")
                partes_msg.append(f"   💰 *Cuota Betsson:* `{s['cuota']}`")
                partes_msg.append("")
                partes_msg.append("   📐 *Métricas Cuantitativas de Poisson (+EV):*")

                p_real = s.get("probabilidad_real_estimada") or s.get("h2h_ratio_pct", "N/D")
                p_real_str = f"{p_real:.1%}" if isinstance(p_real, float) else str(p_real)
                c_val = float(s.get("cuota", 0.0) or 0.0)
                p_impl_str = f"{(1.0 / c_val):.1%}" if c_val > 1.0 else "N/D"

                partes_msg.append(f"   • Probabilidad Real (True Prob): `{p_real_str}`")
                partes_msg.append(f"   • Probabilidad Implícita (Cuota): `{p_impl_str}`")
                partes_msg.append(f"   • Ventaja Matemática: *{s.get('valor_esperado_ev', 'N/D')}*")
                partes_msg.append(f"   • Gestión de Capital: `{s['stake_sugerido']}`")
                partes_msg.append("")
                partes_msg.append("   📝 *Observaciones & Contexto Secundario:*")
                partes_msg.append(f"   • Respaldo Histórico: {s.get('frecuencia_h2h', 'Tendencia oficial verificada')}")
                if s.get("motivo"):
                    partes_msg.append(f"   • Análisis Técnico: {s['motivo']}")
                if s.get("estado_consciente"):
                    partes_msg.append(f"   • Estado Cognitivo: `{str(s['estado_consciente']).upper()}` (Madurez: `{s.get('nivel_madurez', '1.0')} XP`)")
                partes_msg.append("   ─────────────────────────────\n")

        if parlays:
            partes_msg.append("⚡ *PARLAYS COMBINADAS DOBLES (~2.00)*\n")
            for p in parlays:
                p_id = str(p.get('id_ticket', '')).replace('_', '\\_')
                partes_msg.append(f"🎫 *{p_id}* — *Cuota Total:* `{p['cuota_total']}`")
                for i, leg in enumerate(p["patas"], 1):
                    partes_msg.append(f"   🔹 Pata {i}: {leg['partido']} ({leg['competicion']})")
                    partes_msg.append(f"      👉 {leg['mercado']}: *{leg['seleccion']}* @ `{leg['cuota']}`")
                partes_msg.append("")
                partes_msg.append("   📐 *Métricas de Valor:*")
                partes_msg.append(f"   • Probabilidad Combinada: `{p.get('prob_combinada', 'N/D')}`")
                partes_msg.append(f"   • Ventaja Estimada: *{p.get('valor_esperado_ev', 'N/D')}*")
                partes_msg.append(f"   • Stake Sugerido: `{p['stake_sugerido']}`")
                if p.get("justificacion"):
                    partes_msg.append(f"   📝 Justificación: {p['justificacion']}")
                partes_msg.append("   ─────────────────────────────\n")

        partes_msg.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        partes_msg.append(f"🧠 *Estado Emocional:* `{conciencia.estado_adaptativo.upper()}` | *Madurez:* `{conciencia.nivel_madurez} XP`")
        partes_msg.append(f"💡 *Identidad:* _{conciencia.identidad_base}_")
        partes_msg.append("⚠️ Análisis cuantitativo automatizado. Juega de manera responsable.")

        mensaje_final = "\n".join(partes_msg)
        enviar_seguro(bot, chat_id, mensaje_final)
        print("[BOT TELEGRAM] ✅ Reporte on-demand enviado con éxito a Telegram.")

    except Exception as e:
        print(f"[BOT TELEGRAM] ❌ Error durante el barrido on-demand: {e}")
        try:
            enviar_seguro(
                bot,
                chat_id,
                f"❌ *Error durante la ejecución del barrido on-demand:*\n`{str(e)}`"
            )
        except Exception:
            pass

    finally:
        with lock_analisis:
            analisis_en_curso = False


# ═══════════════════════════════════════════════════════════════════════════════
# AUDITORÍA HISTÓRICA PROFUNDA  (/auditar)
# ═══════════════════════════════════════════════════════════════════════════════

def _parsear_argumentos_auditoria(texto: str) -> tuple[str, str, str, int, int | None]:
    """
    Extrae la modalidad, equipos y los límites opcionales del comando /auditar.
    Soporta prefijo opcional de liga/competición (ej. '/auditar bundesliga mainz vs friburgo').
    Modalidades:
      - 'h2h':        /auditar [liga] Espanyol h2h Atlético de Madrid
      - 'cruce':      /auditar [liga] Espanyol vs Atlético de Madrid (o 'contra', '-')
      - 'individual': /auditar [liga] Barcelona
    Devuelve (modalidad, equipo_a, equipo_b, limite, minimo_con_datos).
    """
    import auditoria_historica as _ah
    from fuentes_xg import extraer_liga_de_texto

    texto_norm = normalizar_comando(texto or "")
    cuerpo = re.sub(r"^/(?:auditar|auditoria|auditor[ií]a|historial)(@\w+)?", "", texto_norm or "", flags=re.I).strip()
    limite = _ah.LIMITE_PARTIDOS_POR_DEFECTO
    minimo: int | None = None

    def _extraer(patron: str) -> int | None:
        nonlocal cuerpo
        m = re.search(patron, cuerpo, flags=re.I)
        if not m:
            return None
        valor = max(1, min(40, int(m.group(1))))
        cuerpo = (cuerpo[:m.start()] + cuerpo[m.end():]).strip()
        return valor

    valor_min = _extraer(r"(?:minimo|m[ií]nimo|min)[:=]\s*(\d{1,2})")
    if valor_min is not None:
        minimo = valor_min

    valor_lim = _extraer(r"(?:limite|l[ií]mite|n)[:=]\s*(\d{1,2})")
    if valor_lim is not None:
        limite = valor_lim
    else:
        m2 = re.search(r"\s(\d{1,2})\s*$", cuerpo)
        if m2:
            limite = max(1, min(40, int(m2.group(1))))
            cuerpo = cuerpo[:m2.start()].strip()

    if minimo is None:
        minimo = limite

    cuerpo = cuerpo.strip().strip('"').strip("'").strip()
    _, cuerpo_sin_liga = extraer_liga_de_texto(cuerpo)
    if cuerpo_sin_liga:
        cuerpo = cuerpo_sin_liga.strip().strip('"').strip("'").strip()

    # 1. Modalidad B: H2H si contiene " h2h "
    m_h2h = re.search(r"\s+h2h\s+", cuerpo, flags=re.I)
    if m_h2h:
        eq_a = cuerpo[:m_h2h.start()].strip().strip('"').strip("'").strip()
        eq_b = cuerpo[m_h2h.end():].strip().strip('"').strip("'").strip()
        _, eq_a_l = extraer_liga_de_texto(eq_a)
        _, eq_b_l = extraer_liga_de_texto(eq_b)
        return "h2h", (eq_a_l or eq_a).strip(), (eq_b_l or eq_b).strip(), limite, minimo

    # 2. Modalidad A: Cruce (vs, contra, -, etc.)
    m_cruce = re.search(r"\s+(?:vs\.?|v|contra|x|@|-|–|—)\s+", cuerpo, flags=re.I)
    if m_cruce:
        eq_a = cuerpo[:m_cruce.start()].strip().strip('"').strip("'").strip()
        eq_b = cuerpo[m_cruce.end():].strip().strip('"').strip("'").strip()
        _, eq_a_l = extraer_liga_de_texto(eq_a)
        _, eq_b_l = extraer_liga_de_texto(eq_b)
        return "cruce", (eq_a_l or eq_a).strip(), (eq_b_l or eq_b).strip(), limite, minimo

    # 3. Individual
    return "individual", cuerpo, "", limite, minimo


@bot.message_handler(commands=["auditar", "auditoria", "auditoría", "historial"])
@solo_autorizado
def cmd_auditar(message):
    """
    Comando de Informe de Matriz Bajo Demanda (/auditar).
    Modalidad A: /auditar [liga] Equipo A vs Equipo B (30 matrices completas: 15 Local + 15 Visitante).
    Modalidad B: /auditar [liga] Equipo A h2h Equipo B (N matrices reales del H2H 2024-2026, cero inventados).
    Individual:  /auditar [liga] Equipo (15 matrices completas oficiales).
    """
    global auditoria_en_curso

    texto_msg = getattr(message, "text", "") or ""
    liga_info = _extraer_liga_de_comando_raw(texto_msg)
    modalidad, eq_a, eq_b, limite, minimo = _parsear_argumentos_auditoria(texto_msg)
    if not eq_a:
        enviar_seguro(
            bot, message,
            "📋 *Uso del comando bajo demanda `/auditar`:*\n\n"
            "🅰️ *Modalidad A — Auditoría de 30 Matrices (Local vs Visita):*\n"
            "  `/auditar Espanyol vs Atlético de Madrid`\n"
            "  `/auditar bundesliga mainz vs friburgo`\n"
            "  _Genera 15 matrices oficiales del Local + 15 matrices oficiales del Visitante (30 en total). "
            "Excluye 100% de amistosos y desglosa las 45 métricas en FT, 1T y 2T sin nulos._\n\n"
            "🅱️ *Modalidad B — Auditoría H2H Puro (2024–2026):*\n"
            "  `/auditar Espanyol h2h Atlético de Madrid`\n"
            "  _Genera N matrices individuales para los N enfrentamientos reales del H2H "
            "(cero partidos inventados o simulados)._\n\n"
            "👤 *Modalidad Individual:*\n"
            "  `/auditar Barcelona`\n"
            "  _Genera las 15 matrices de los últimos 15 partidos oficiales del club._",
            es_reply=True,
        )
        return

    with lock_analisis:
        if auditoria_en_curso:
            enviar_seguro(
                bot, message,
                "⚠️ *Ya hay una auditoría en curso.* Espera a que termine antes de lanzar otra.",
                es_reply=True,
            )
            return
        auditoria_en_curso = True

    linea_liga = f"• Liga / Competición indicada: *{liga_info['nombre']}*\n" if liga_info else ""

    if modalidad == "h2h":
        enviar_seguro(
            bot, message,
            f"🔬 *AUDITORÍA H2H PROFUNDA (MODALIDAD B) — «{eq_a} h2h {eq_b}»*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{linea_liga}"
            "• Ventana temporal: 2024-01-01 a 2026-12-31 (estricta)\n"
            "• Filtro de integridad: Únicamente enfrentamientos reales (cero inventados o simulados)\n"
            "• Periodos por partido: `FT · 1T · 2T`\n"
            "• Catálogo: 45 métricas oficiales (01 a 45) desglosadas por periodo sin nulos\n"
            "• Salida: Documento Markdown con las matrices completas de cada duelo\n\n"
            "⏳ Extrayendo datos directos. Te aviso al terminar.",
            es_reply=True,
        )
        hilo = threading.Thread(target=_hilo_ejecutar_auditoria_h2h,
                                args=(message.chat.id, eq_a, eq_b, liga_info), daemon=True)
        hilo.start()
        return

    if modalidad == "cruce":
        enviar_seguro(
            bot, message,
            f"🔬 *AUDITORÍA DE 30 MATRICES (MODALIDAD A) — «{eq_a} vs {eq_b}»*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{linea_liga}"
            f"• Local: *{eq_a}* (15 partidos 100% oficiales)\n"
            f"• Visitante: *{eq_b}* (15 partidos 100% oficiales)\n"
            "• Filtro anti-amistosos: Exclusión absoluta (cero amistosos en el cómputo; backfill hacia atrás)\n"
            "• Total: 30 matrices completas individuales (15 + 15)\n"
            "• Periodos por partido: `FT · 1T · 2T`\n"
            "• Catálogo: 45 métricas oficiales (01 a 45) sin valores nulos ni inventados\n"
            "• Salida: Documento Markdown completo adjunto para descarga\n\n"
            "⏳ Extrayendo las 30 matrices oficiales. Te aviso al terminar.",
            es_reply=True,
        )
        hilo = threading.Thread(target=_hilo_ejecutar_auditoria_cruce,
                                args=(message.chat.id, eq_a, eq_b, limite, minimo, liga_info), daemon=True)
        hilo.start()
        return

    # Individual
    enviar_seguro(
        bot, message,
        f"🔬 *AUDITORÍA HISTÓRICA PROFUNDA — «{eq_a}»*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{linea_liga}"
        f"• Objetivo: `{minimo}` partidos oficiales *con datos* (máximo `{limite}` auditados)\n"
        "• Filtro: amistosos descartados por completo; si un partido viene sin\n"
        "  estadísticas, el escaneo continúa hacia atrás hasta completar el objetivo\n"
        "• Periodos por partido: `FT · 1T · 2T`\n"
        "• Métricas por matriz: 45 métricas oficiales (01 a 45) sin nulos\n"
        "• Salidas: informe Markdown + CSV estructurado + JSON completo\n\n"
        "⏳ Extrayendo datos. Te aviso al terminar.",
        es_reply=True,
    )
    hilo = threading.Thread(target=_hilo_ejecutar_auditoria,
                            args=(message.chat.id, eq_a, limite, minimo, liga_info), daemon=True)
    hilo.start()


def _hilo_ejecutar_auditoria_cruce(chat_id: int | str, eq_local: str, eq_visita: str,
                                   limite: int = 15, minimo: int | None = None,
                                   liga: dict[str, Any] | None = None) -> None:
    """Ejecuta la auditoría de 30 matrices (Modalidad A: 15 Local + 15 Visita)."""
    global auditoria_en_curso
    try:
        print(f"[BOT TELEGRAM] 🔬 Auditoría Modalidad A solicitada: {eq_local} vs {eq_visita}")
        import auditoria_historica as ah
        from fuentes_xg import _inferir_meta_rival_desde_texto

        ultimo_aviso = {"t": 0.0}

        def progreso(msg: str) -> None:
            ahora = time.time()
            if "[1/" in msg or ahora - ultimo_aviso["t"] > 45:
                ultimo_aviso["t"] = ahora
                if "[1/" in msg or msg.startswith("extrayendo"):
                    enviar_seguro(bot, chat_id, f"⏳ {msg}")

        rival_loc: Any = eq_visita
        if liga:
            base_vis = _inferir_meta_rival_desde_texto(eq_visita) or {"nombre": eq_visita}
            rival_loc = {**base_vis, **liga}

        doc_local = ah.auditar_equipo(eq_local, limite=limite, minimo_con_datos=minimo,
                                      headless=True, progreso=progreso, rival=rival_loc)
        rival_vis: Any = doc_local.get("equipo") or eq_local
        if liga and isinstance(rival_vis, dict):
            rival_vis = {**rival_vis, **liga}
        elif liga:
            rival_vis = dict(liga)
        doc_visita = ah.auditar_equipo(eq_visita, limite=limite, minimo_con_datos=minimo,
                                       headless=True, progreso=progreso, rival=rival_vis)

        contenido_md, ruta_md = ah.generar_informe_matrices_30(doc_local, doc_visita)

        res_loc = doc_local.get("resumen", {})
        res_vis = doc_visita.get("resumen", {})
        nom_l = (doc_local.get("equipo") or {}).get("nombre", eq_local)
        nom_v = (doc_visita.get("equipo") or {}).get("nombre", eq_visita)

        n_loc_real = int(res_loc.get("partidos_con_datos", len(doc_local.get("partidos") or [])) or 0)
        n_vis_real = int(res_vis.get("partidos_con_datos", len(doc_visita.get("partidos") or [])) or 0)
        n_tot_real = n_loc_real + n_vis_real
        partes_md_a = ah.obtener_partes_informe_md(ruta_md)
        n_archivos_a = len(partes_md_a)

        partes = [
            f"✅ *AUDITORÍA DE {n_tot_real} MATRICES COMPLETADA (MODALIDAD A)*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• Partido: *{nom_l}* (Local) vs *{nom_v}* (Visitante)",
            f"• Total de matrices generadas: *{n_tot_real} matrices individuales ({n_archivos_a} parte(s) < 30 KB)*",
            f"  – *{nom_l}*: {n_loc_real} partido(s) 100% oficiales (0 amistosos)",
            f"  – *{nom_v}*: {n_vis_real} partido(s) 100% oficiales (0 amistosos)",
            f"• Desglose multitiempo: `FT · 1T · 2T` en cada partido",
            f"• Catálogo oficial: 45 métricas (01 a 45) con distinción entre 0 observado y sin_dato",
            f"• Fusión de fuentes: FotMob (primaria) + Flashscore (secundaria)",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📎 _Adjunto el informe Markdown en {n_archivos_a} parte(s) (< 30 KB por archivo) para auditar cada dato._",
        ]
        enviar_seguro(bot, chat_id, "\n".join(partes))

        try:
            for info_p in partes_md_a:
                k_p = info_p["parte"]
                tot_p = info_p["total_partes"]
                ruta_p = info_p["ruta"]
                with open(ruta_p, "rb") as fp:
                    bytes_p = fp.read()
                enviar_documento_seguro(
                    chat_id, bytes_p, os.path.basename(ruta_p),
                    caption=f"📄 Parte {k_p}/{tot_p}: Auditoría {nom_l} vs {nom_v} ({len(bytes_p)} bytes)"
                )
        except Exception as e_doc:
            print(f"[BOT TELEGRAM] ⚠️ No se pudo enviar documento de matrices Modalidad A: {e_doc}")

        print(f"[BOT TELEGRAM] ✅ Auditoría {n_tot_real} matrices ({nom_l} vs {nom_v}) despachada en {n_archivos_a} parte(s): {ruta_md}")

    except Exception as e:
        print(f"[BOT TELEGRAM] ❌ Error en auditoría de 30 matrices: {e}")
        try:
            enviar_seguro(bot, chat_id, f"❌ *La auditoría de 30 matrices falló:*\n`{type(e).__name__}: {e}`")
        except Exception:
            pass
    finally:
        with lock_analisis:
            auditoria_en_curso = False


def _hilo_ejecutar_auditoria_h2h(chat_id: int | str, eq_a: str, eq_b: str,
                                 liga: dict[str, Any] | None = None) -> None:
    """Ejecuta la auditoría H2H de N matrices reales (Modalidad B)."""
    global auditoria_en_curso
    try:
        print(f"[BOT TELEGRAM] 🔬 Auditoría Modalidad B (H2H) solicitada: {eq_a} h2h {eq_b}")
        import auditoria_historica as ah
        import cuadro_h2h_2024_2026 as ch
        from fuentes_xg import _inferir_meta_rival_desde_texto

        ultimo_aviso = {"t": 0.0}

        def progreso(msg: str) -> None:
            ahora = time.time()
            if "[1/" in msg or ahora - ultimo_aviso["t"] > 45:
                ultimo_aviso["t"] = ahora
                if "[1/" in msg or msg.startswith("extrayendo") or "H2H" in msg:
                    enviar_seguro(bot, chat_id, f"⏳ {msg}")

        rival_a: Any = eq_b
        if liga:
            base_b = _inferir_meta_rival_desde_texto(eq_b) or {"nombre": eq_b}
            rival_a = {**base_b, **liga}

        doc_a = ah.auditar_equipo(eq_a, limite=15, minimo_con_datos=15,
                                  headless=True, progreso=progreso, rival=rival_a)
        rival_b: Any = doc_a.get("equipo") or eq_a
        if liga and isinstance(rival_b, dict):
            rival_b = {**rival_b, **liga}
        elif liga:
            rival_b = dict(liga)
        doc_b = ah.auditar_equipo(eq_b, limite=15, minimo_con_datos=15,
                                  headless=True, progreso=progreso, rival=rival_b)

        res_h2h = ch.recolectar_duelos_h2h_2024_2026(
            doc_a, doc_b, buscar_en_red=True, headless=True, aviso=progreso
        )

        n_duelos = res_h2h.get("n_duelos", 0)
        nom_a = (doc_a.get("equipo") or {}).get("nombre", eq_a)
        nom_b = (doc_b.get("equipo") or {}).get("nombre", eq_b)

        if n_duelos == 0:
            enviar_seguro(
                bot, chat_id,
                f"ℹ️ *Sin enfrentamientos directos oficiales entre {nom_a} y {nom_b} "
                f"en la ventana temporal 2024–2026.*\n\n"
                f"Regla del sistema: Se prohíbe terminantemente inventar, simular o sustituir "
                f"partidos con otros rivales."
            )
            return

        lista_partidos = (res_h2h.get("doc_h2h_a") or {}).get("partidos") or []
        contenido_md, ruta_md = ah.generar_informe_matrices_h2h(
            res_h2h["doc_h2h_a"], res_h2h["doc_h2h_b"], lista_partidos
        )

        partes = [
            f"✅ *AUDITORÍA H2H COMPLETADA (MODALIDAD B)*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• Cruce directo: *{nom_a}* vs *{nom_b}*",
            f"• Enfrentamientos directos reales (2024–2026): `{n_duelos}` partido(s)",
            f"• Prohibición de inventar datos: Cero partidos fabricados o sustituidos",
            f"• Matrices entregadas: `{n_duelos}` matrices completas individuales",
            f"• Desglose multitiempo: `FT · 1T · 2T` (45 métricas del 01 al 45)",
            f"• Historial verificado de enfrentamientos:",
        ]
        for d in res_h2h.get("lista_duelos", []):
            partes.append(f"  – {d.get('fecha')}: {d.get('local')} {d.get('goles_local')}-{d.get('goles_visitante')} {d.get('visitante')} ({d.get('torneo')})")
        partes.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        partes.append(f"📎 _Adjunto el informe Markdown con las {n_duelos} matrices completas._")

        enviar_seguro(bot, chat_id, "\n".join(partes))

        try:
            partes_md_b = ah.obtener_partes_informe_md(ruta_md)
            for info_p in partes_md_b:
                k_p = info_p["parte"]
                tot_p = info_p["total_partes"]
                ruta_p = info_p["ruta"]
                with open(ruta_p, "rb") as fp:
                    bytes_p = fp.read()
                enviar_documento_seguro(
                    chat_id, bytes_p, os.path.basename(ruta_p),
                    caption=f"📄 Parte {k_p}/{tot_p}: Auditoría H2H ({nom_a} vs {nom_b}) - {n_duelos} matrices ({len(bytes_p)} bytes)"
                )
        except Exception as e_doc:
            print(f"[BOT TELEGRAM] ⚠️ No se pudo enviar documento H2H: {e_doc}")

        print(f"[BOT TELEGRAM] ✅ Auditoría H2H ({nom_a} vs {nom_b}, {n_duelos} matrices) despachada: {ruta_md}")

    except Exception as e:
        print(f"[BOT TELEGRAM] ❌ Error en auditoría H2H: {e}")
        try:
            enviar_seguro(bot, chat_id, f"❌ *La auditoría H2H falló:*\n`{type(e).__name__}: {e}`")
        except Exception:
            pass
    finally:
        with lock_analisis:
            auditoria_en_curso = False


def _hilo_ejecutar_auditoria(chat_id: int | str, equipo: str, limite: int,
                             minimo: int | None = None,
                             liga: dict[str, Any] | None = None) -> None:
    """Ejecuta la auditoría histórica individual en segundo plano y despacha los resultados."""
    global auditoria_en_curso
    try:
        print(f"[BOT TELEGRAM] 🔬 Auditoría histórica solicitada: {equipo} ({limite} partidos)")
        import auditoria_historica as ah

        ultimo_aviso = {"t": 0.0}

        def progreso(msg: str) -> None:
            ahora = time.time()
            if "[1/" in msg or ahora - ultimo_aviso["t"] > 45:
                ultimo_aviso["t"] = ahora
                if "[1/" in msg or msg.startswith("extrayendo"):
                    enviar_seguro(bot, chat_id, f"⏳ {msg}")

        auditoria = ah.auditar_equipo(equipo, limite=limite, headless=True, progreso=progreso,
                                      minimo_con_datos=minimo, rival=liga)
        rutas = ah.exportar_auditoria(auditoria)
        resumen = auditoria.get("resumen", {})
        escaneo = auditoria.get("escaneo", {})

        # Generar también el informe de matrices individuales (< 30 KB por parte)
        ruta_mat_ind = rutas.get("markdown_matrices") or rutas.get("markdown")
        try:
            _, ruta_mat_ind = ah.generar_informe_matrices_equipo(auditoria, ruta_mat_ind)
        except Exception as e_mat:
            print(f"[BOT TELEGRAM] ⚠️ No se pudo generar informe de matrices individuales: {e_mat}")

        # 1) Resumen ejecutivo en el chat
        partes = [
            f"✅ *AUDITORÍA COMPLETADA — {auditoria['equipo']['nombre']}*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• Fuente de datos: `{auditoria.get('fuente')}`",
            f"• Objetivo: `{resumen.get('minimo_con_datos')}` partidos oficiales con datos "
            f"→ {'✅ ALCANZADO' if resumen.get('objetivo_alcanzado') else '⚠️ NO ALCANZADO'}",
            f"• Partidos con datos: `{resumen.get('partidos_con_datos')}/{resumen.get('partidos_auditados')}`",
            f"• Métricas con dato: `{resumen.get('metricas_con_datos')}/{resumen.get('metricas_solicitadas')}`",
            f"• Amistosos descartados: `{escaneo.get('amistosos_excluidos')}` "
            f"({escaneo.get('paginas_escaneadas')} página(s) de calendario escaneadas)",
        ]
        if escaneo.get("compensacion_necesaria"):
            partes.append(f"• Compensación: `{escaneo.get('partidos_sin_datos')}` partido(s) sin "
                          f"estadísticas descartados, el escaneo siguió hacia atrás")
        if resumen.get("objetivo_alcanzado") is False:
            partes.append("")
            partes.append("🛑 *El historial de la fuente no alcanza el objetivo solicitado.*")
            partes.append(f"   Motivo: {escaneo.get('motivo_parada') or 'historial agotado'}")
            partes.append("   No se entregan partidos de relleno ni valores inventados.")
        ausentes = resumen.get("metricas_sin_datos_en_ningun_partido") or []
        if ausentes:
            partes.append("")
            partes.append("⚠️ *No publicadas por la fuente* (quedan nulas, no se inventan):")
            for a in ausentes:
                partes.append(f"   – {a}")
        partes.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        partes.append("📎 _Adjunto el informe en Markdown (< 30 KB por parte) con las matrices y el CSV crudo._")
        enviar_seguro(bot, chat_id, "\n".join(partes))

        # 2) Informe Markdown (partes < 30 KB)
        try:
            partes_md_eq = ah.obtener_partes_informe_md(ruta_mat_ind) if ruta_mat_ind else []
            if partes_md_eq:
                for info_p in partes_md_eq:
                    k_p = info_p["parte"]
                    tot_p = info_p["total_partes"]
                    ruta_p = info_p["ruta"]
                    with open(ruta_p, "rb") as fp:
                        bytes_p = fp.read()
                    enviar_documento_seguro(
                        chat_id, bytes_p, os.path.basename(ruta_p),
                        caption=f"📄 Parte {k_p}/{tot_p}: Informe de auditoría (matrices FT / 1T / 2T)"
                    )
            else:
                with open(rutas["markdown"], "r", encoding="utf-8") as f:
                    contenido_md = f.read()
                enviar_documento_seguro(chat_id, contenido_md.encode("utf-8"),
                                        os.path.basename(rutas["markdown"]),
                                        caption="📄 Informe de auditoría (matrices FT / 1T / 2T)")
        except Exception as e:
            print(f"[BOT TELEGRAM] ⚠️ No se pudo enviar el informe Markdown: {e}")

        # 3) CSV estructurado
        for clave, descripcion in (("csv", "🧾 CSV largo (1 fila por métrica)"),
                                   ("csv_matriz", "📊 CSV matriz (para pandas)")):
            try:
                with open(rutas[clave], "rb") as f:
                    enviar_documento_seguro(chat_id, f.read(), os.path.basename(rutas[clave]),
                                            caption=descripcion)
            except Exception as e:
                print(f"[BOT TELEGRAM] ⚠️ No se pudo enviar {clave}: {e}")

        print(f"[BOT TELEGRAM] ✅ Auditoría de {equipo} despachada. Archivos: {rutas}")

    except Exception as e:
        print(f"[BOT TELEGRAM] ❌ Error en la auditoría histórica: {type(e).__name__}: {e}")
        try:
            enviar_seguro(bot, chat_id,
                          f"❌ *La auditoría de «{equipo}» falló:*\n`{type(e).__name__}: {e}`")
        except Exception:
            pass
    finally:
        with lock_analisis:
            auditoria_en_curso = False


def enviar_documento_seguro(chat_id: int | str, contenido: bytes, nombre: str,
                            caption: str = "") -> bool:
    """
    Envía un archivo a Telegram de forma fiable.

    El pie de foto va en TEXTO PLANO por defecto: las rutas y nombres de archivo con
    guiones bajos rompen el parseo Markdown (HTTP 400 «can't parse entities»). El
    reintento desactiva de verdad el parse_mode de la instancia, porque pasar
    `parse_mode=None` lo restaura en pyTelegramBotAPI.
    """
    try:
        bot.send_document(chat_id, (nombre, contenido, "text/plain"),
                          caption=caption, parse_mode="Markdown")
        return True
    except Exception as e1:
        print(f"[BOT TELEGRAM] ⚠️ Pie de foto con Markdown rechazado ({e1}); "
              f"reenviando en texto plano…")
    original = getattr(bot, "parse_mode", None)
    try:
        bot.parse_mode = None
        bot.send_document(chat_id, (nombre, contenido, "text/plain"), caption=caption)
        return True
    except Exception as e2:
        print(f"[BOT TELEGRAM] ❌ Error fatal enviando documento: {e2}")
        return False
    finally:
        bot.parse_mode = original


# ═══════════════════════════════════════════════════════════════════════════════
# ENFRENTAMIENTO DIRECTO  (/h2h  o  texto "Equipo A vs Equipo B")
# ═══════════════════════════════════════════════════════════════════════════════

def _parsear_argumentos_h2h(texto: str) -> tuple[str, str, int]:
    """
    Extrae los dos equipos y el límite desde el texto del comando.
    Acepta el formato cruzado con 'vs', 'v', '-', 'x', '@' o 'contra', prefijos
    de liga/competición ('/h2h bundesliga mainz vs friburgo') y el límite en
    cualquier posición: 'limite:N' o un número suelto al final.
    """
    import auditoria_h2h as _h2h
    import auditoria_historica as _ah

    texto_norm = normalizar_comando(texto or "")
    cuerpo = re.sub(r"^/(?:h2h|vs|comparar|comparativa|duelo)(@\w+)?", "", texto_norm or "",
                    flags=re.I).strip()
    limite = _ah.LIMITE_PARTIDOS_POR_DEFECTO

    # 1) límite explícito: "limite:10", "limite 10"
    m = re.search(r"\b(?:limite|l[ií]mite|n)\s*[:=]?\s*(\d{1,2})\b", cuerpo, flags=re.I)
    if m:
        limite = max(1, min(40, int(m.group(1))))
        cuerpo = (cuerpo[:m.start()] + " " + cuerpo[m.end():]).strip()
    else:
        # 2) número suelto al final del texto
        m2 = re.search(r"\s(\d{1,2})\s*$", cuerpo)
        if m2:
            limite = max(1, min(40, int(m2.group(1))))
            cuerpo = cuerpo[:m2.start()].strip()

    # 3) número suelto justo antes del separador ("Barcelona 10 vs Sevilla")
    m3 = re.search(r"\s(\d{1,2})\s+(?=(?:vs\.?|v|contra|x|-|–|—)\s)", cuerpo, flags=re.I)
    if m3 and limite == _ah.LIMITE_PARTIDOS_POR_DEFECTO:
        limite = max(1, min(40, int(m3.group(1))))
        cuerpo = (cuerpo[:m3.start()] + " " + cuerpo[m3.end():]).strip()

    equipos = _h2h.extraer_equipos(cuerpo)
    if not equipos:
        return "", "", limite
    return equipos[0], equipos[1], limite


def _es_peticion_h2h(message) -> tuple[str, str, int] | None:
    """
    Devuelve (A, B, límite) únicamente si el mensaje invoca explícitamente
    el comando /h2h (o sus alias con barra: /vs, /comparar, /comparativa, /duelo).
    El bot nunca se activa con texto libre sin comando '/'.
    """
    texto_raw = (getattr(message, "text", "") or "").strip()
    if not texto_raw:
        return None
    texto = normalizar_comando(texto_raw)
    es_comando_h2h = re.match(r"^/(?:h2h|vs|comparar|comparativa|duelo)\b", texto, flags=re.I)
    if not es_comando_h2h:
        return None
    a, b, limite = _parsear_argumentos_h2h(texto)
    if a and b:
        return a, b, limite
    return None


@bot.message_handler(commands=["h2h", "vs", "comparar", "comparativa", "duelo"])
@solo_autorizado
def cmd_h2h(message):
    """Enfrentamiento directo entre dos equipos (auditorías unificadas)."""
    peticion = _es_peticion_h2h(message)
    if not peticion:
        enviar_seguro(
            bot, message,
            "📋 *Uso correcto:*\n`/h2h [liga] <Equipo A> vs <Equipo B>`\n\n"
            "Ejemplos:\n"
            "• `/h2h Real Madrid vs Roma`\n"
            "• `/h2h bundesliga mainz vs friburgo`\n"
            "• `/h2h copa libertadores Boca Juniors vs River Plate`\n"
            "• `/h2h Barcelona vs Sevilla limite:10`\n\n"
            "El bot reutiliza las auditorías históricas ya generadas y, si no existen "
            "o están desactualizadas, lanza el scraper. Después consolida todo en un "
            "único informe comparativo.",
            es_reply=True,
        )
        return
    liga_info = _extraer_liga_de_comando_raw(getattr(message, "text", "") or "")
    _lanzar_h2h(message.chat.id, *peticion, liga=liga_info)


def _lanzar_h2h(chat_id: int | str, equipo_a: str, equipo_b: str, limite: int,
                liga: dict[str, Any] | None = None) -> None:
    """Valida concurrencia y lanza el H2H en un hilo desacoplado."""
    global auditoria_en_curso

    with lock_analisis:
        if auditoria_en_curso:
            enviar_seguro(
                bot, chat_id,
                "⚠️ *Ya hay una auditoría o un enfrentamiento en curso.* "
                "Espera a que termine antes de lanzar otro.",
            )
            return
        auditoria_en_curso = True

    linea_liga = f"• Liga / Competición indicada: *{liga['nombre']}*\n" if liga else ""
    enviar_seguro(
        bot, chat_id,
        f"🤝 *ENFRENTAMIENTO DIRECTO — «{equipo_a}» vs «{equipo_b}»*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{linea_liga}"
        f"• Ventana: hasta `{limite}` partidos oficiales por equipo (amistosos fuera)\n"
        "• Paso 1: buscar las auditorías ya generadas de ambos equipos\n"
        "• Paso 2: reescanear solo las que falten o estén desactualizadas\n"
        "• Paso 3: consolidar cobertura, matriz comparativa y umbrales ≥ 70%\n"
        "• Salida: *un único* informe Markdown consolidado\n\n"
        "⏳ Si hay que reescanear, puede tardar un par de minutos. Te aviso.",
    )
    hilo = threading.Thread(target=_hilo_ejecutar_h2h,
                            args=(chat_id, equipo_a, equipo_b, limite, liga), daemon=True)
    hilo.start()


def _hilo_ejecutar_h2h(chat_id: int | str, equipo_a: str, equipo_b: str, limite: int,
                       liga: dict[str, Any] | None = None) -> None:
    """Ejecuta el flujo H2H y despacha el informe consolidado."""
    global auditoria_en_curso
    try:
        print(f"[BOT TELEGRAM] 🤝 H2H solicitado: {equipo_a} vs {equipo_b} ({limite} partidos)")
        import auditoria_h2h as h2h

        ultimo = {"t": 0.0}

        def progreso(msg: str) -> None:
            ahora = time.time()
            if ("reescaneada" in msg or "reutilizada" in msg or "[1/" in msg
                    or ahora - ultimo["t"] > 60):
                ultimo["t"] = ahora
                if "reutilizada" in msg or "reescaneada" in msg:
                    enviar_seguro(bot, chat_id, f"⏳ {msg}")

        analisis = h2h.generar_h2h(equipo_a, equipo_b, limite=limite, progreso=progreso, liga=liga)

        # 1) Resumen clave al chat
        enviar_seguro(bot, chat_id, h2h.resumen_telegram(analisis))

        # 2) Informe consolidado adjunto
        ruta_md = analisis["rutas"]["markdown"]
        try:
            with open(ruta_md, "r", encoding="utf-8") as f:
                contenido = f.read()
            enviar_documento_seguro(
                chat_id, contenido.encode("utf-8"), os.path.basename(ruta_md),
                caption="📄 Informe consolidado del enfrentamiento")
        except Exception as e:
            print(f"[BOT TELEGRAM] ⚠️ No se pudo enviar el informe H2H: {e}")
            enviar_seguro(bot, chat_id, f"ℹ️ Informe guardado en:\n`{ruta_md}`")

        print(f"[BOT TELEGRAM] ✅ H2H despachado: {analisis['rutas']}")

    except Exception as e:
        print(f"[BOT TELEGRAM] ❌ Error en el H2H: {type(e).__name__}: {e}")
        try:
            enviar_seguro(bot, chat_id,
                          f"❌ *El enfrentamiento «{equipo_a} vs {equipo_b}» falló:*\n"
                          f"`{type(e).__name__}: {e}`")
        except Exception:
            pass
    finally:
        with lock_analisis:
            auditoria_en_curso = False


# ═══════════════════════════════════════════════════════════════════════════════
# ANÁLISIS DE MERCADOS CON FILTRO POR PROBABILIDAD  (/mercados)
# ═══════════════════════════════════════════════════════════════════════════════

def _parsear_argumentos_mercados(texto: str) -> tuple[str, str, float, int, bool]:
    """
    Extrae equipos, umbral y límite del comando de mercados.
    Acepta: '/mercados A vs B', '/mercados bundesliga mainz vs friburgo',
            '/mercados la liga ea sports barcelona vs real madrid',
            '/mercados A vs B umbral:0.75', '... limite:10',
            '... borrador' (no envía, solo prepara el análisis).
    Devuelve (equipo_a, equipo_b, umbral, limite, solo_borrador).
    """
    import auditoria_h2h as _h2h
    import auditoria_historica as _ah

    texto_norm = normalizar_comando(texto or "")
    cuerpo = re.sub(r"^/(?:mercados|mercado|probabilidades|analizar_mercados)(@\w+)?", "",
                    texto_norm or "", flags=re.I).strip()
    umbral = 0.70
    limite = _ah.LIMITE_PARTIDOS_POR_DEFECTO
    solo_borrador = bool(re.search(r"\bborrador\b", cuerpo, flags=re.I))
    cuerpo = re.sub(r"\bborrador\b", " ", cuerpo, flags=re.I)

    def extraer(patron: str, convertir):
        nonlocal cuerpo
        m = re.search(patron, cuerpo, flags=re.I)
        if not m:
            return None
        valor = convertir(m.group(1))
        cuerpo = (cuerpo[:m.start()] + " " + cuerpo[m.end():]).strip()
        return valor

    valor_umbral = extraer(r"(?:umbral|threshold|u)\s*[:=]\s*(\d+(?:[.,]\d+)?)",
                           lambda v: float(v.replace(",", ".")))
    if valor_umbral is not None:
        # Acepta tanto 0.75 como 75
        umbral = valor_umbral / 100.0 if valor_umbral > 1.0 else valor_umbral
    valor_limite = extraer(r"(?:limite|l[ií]mite|n)\s*[:=]\s*(\d{1,2})", lambda v: int(v))
    if valor_limite is not None:
        limite = max(1, min(40, valor_limite))

    equipos = _h2h.extraer_equipos(cuerpo)
    if not equipos:
        return "", "", umbral, limite, solo_borrador
    return equipos[0], equipos[1], umbral, limite, solo_borrador


@bot.message_handler(commands=["mercados", "mercado", "probabilidades", "analizar_mercados"])
@solo_autorizado
def cmd_mercados(message):
    """Analiza el catálogo de mercados y envía solo las selecciones > umbral."""
    texto_msg = getattr(message, "text", "") or ""
    liga_info = _extraer_liga_de_comando_raw(texto_msg)
    a, b, umbral, limite, borrador = _parsear_argumentos_mercados(texto_msg)
    if not a or not b:
        enviar_seguro(
            bot, message,
            "📋 *Uso correcto:*\n`/mercados [liga] <Equipo A> vs <Equipo B>`\n\n"
            "Ejemplos:\n"
            "• `/mercados bundesliga mainz vs friburgo`\n"
            "• `/mercados la liga ea sports barcelona vs real madrid`\n"
            "• `/mercados premier league fc fulham vs hull city`\n"
            "• `/mercados champions league real madrid vs bayern`\n"
            "• `/mercados copa libertadores boca juniors vs river plate`\n"
            "• `/mercados Barcelona vs Sevilla limite:10 umbral:0.75`\n\n"
            "Reutiliza (o genera) los reportes de ambos equipos en su liga exacta, calcula los "
            "30 mercados del catálogo con el modelo declarado, evalúa la sensibilidad de cada "
            "selección y envía únicamente las que superan el umbral central.",
            es_reply=True,
        )
        return
    _lanzar_mercados(message.chat.id, a, b, umbral, limite, borrador, liga=liga_info)


def _lanzar_mercados(chat_id: int | str, equipo_a: str, equipo_b: str,
                     umbral: float, limite: int, borrador: bool = False,
                     liga: dict[str, Any] | None = None) -> None:
    """
    Acuse inmediato + lanzamiento del análisis en un hilo aparte.

    El acuse se envía ANTES de cualquier trabajo pesado, para que el usuario sepa
    que el comando fue reconocido mientras el scraper trabaja en segundo plano.
    """
    global auditoria_en_curso
    with lock_analisis:
        if auditoria_en_curso:
            # Nunca se deja al usuario sin respuesta: se explica y se le dice qué hacer
            enviar_seguro(
                bot, chat_id,
                "⏳ *Hay un análisis en curso en este momento.*\n"
                "Solo proceso uno a la vez para no sobrecargar el scraper. "
                "En cuanto termine, vuelve a enviar el comando y lo atiendo.")
            return
        auditoria_en_curso = True

    from resolvedor_entidades import resolver as _resolver_ent, formatear_mensaje_entidades_verificadas
    nombre_liga_acuse = liga.get("nombre", "") if isinstance(liga, dict) else (str(liga) if liga else "")
    res_acuse = _resolver_ent(nombre_liga_acuse, equipo_a, equipo_b)
    linea_entidades = formatear_mensaje_entidades_verificadas(
        res_acuse.equipo_local or equipo_a,
        res_acuse.pais_iso or "INT",
        res_acuse.equipo_visita or equipo_b,
        res_acuse.pais_iso or "INT",
        nombre_liga_acuse or res_acuse.liga or "Oficial",
    )
    linea_liga = f"• Liga / Competición: *{liga['nombre']}*\n" if liga else ""
    # ── ACUSE INMEDIATO (antes de tocar el scraper) ─────────────────────────
    enviar_seguro(
        bot, chat_id,
        f"⏳ *Recibido.* Extrayendo historial de {limite} partidos y procesando "
        f"matrices para *{res_acuse.equipo_local or equipo_a}* vs *{res_acuse.equipo_visita or equipo_b}*... Esto tomará un momento.\n"
        f"{linea_entidades}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{linea_liga}"
        f"• Umbral del filtro central: `{umbral:.0%}` (estricto, sin redondeo)\n"
        "• Catálogo: 30 mercados (goles FT/1T/2T, córners, tarjetas, hándicaps, "
        "ambos anotan, combinaciones)\n"
        "• Sensibilidad: 9 escenarios de goles y 9 de conteos\n\n"
        "Te envío el resultado en cuanto esté listo.",
    )
    print(f"[BOT TELEGRAM] ⏳ Acuse enviado para {equipo_a} vs {equipo_b}")

    hilo = threading.Thread(target=_hilo_ejecutar_mercados,
                            args=(chat_id, equipo_a, equipo_b, umbral, limite, borrador, liga),
                            daemon=True)
    hilo.start()


def _hilo_ejecutar_mercados(chat_id: int | str, equipo_a: str, equipo_b: str,
                            umbral: float, limite: int, borrador: bool,
                            liga: dict[str, Any] | None = None) -> None:
    """Ejecuta el análisis de mercados y despacha el mensaje filtrado."""
    global auditoria_en_curso
    try:
        print(f"[BOT TELEGRAM] 🎯 Mercados solicitados: {equipo_a} vs {equipo_b} "
              f"(umbral {umbral:.0%})")
        import servicio_mercados as servicio

        ultimo = {"t": 0.0}

        def progreso(msg: str) -> None:
            ahora = time.time()
            if "Entidades verificadas:" in msg:
                enviar_seguro(bot, chat_id, msg)
                return
            if "reutilizada" in msg or "reescaneada" in msg or ahora - ultimo["t"] > 90:
                ultimo["t"] = ahora
                if "reutilizada" in msg or "reescaneada" in msg:
                    enviar_seguro(bot, chat_id, f"⏳ {msg}")

        preparado = servicio.preparar_analisis(
            equipo_a, equipo_b, limite=limite, umbral=umbral, progreso=progreso, liga=liga)
        analisis = preparado["analisis"]

        # ── SNAPSHOT INMUTABLE, ANTES DE ENVIAR ────────────────────────────
        # Guarda TODAS las selecciones estimables (también las inferiores al
        # umbral) para poder evaluar el modelo completo, y marca aparte las
        # publicadas. El pasado no se reescribe: si ya existe, no se toca.
        try:
            import evaluacion_pronosticos as evaluacion
            snapshot = evaluacion.snapshot_desde_analisis(preparado)
            ruta_snap = evaluacion.guardar_snapshot(snapshot)
            print(f"[BOT TELEGRAM] 🗂️  Snapshot de pronóstico guardado: {ruta_snap} "
                  f"({len(snapshot.selecciones)} estimables, "
                  f"{len(snapshot.selecciones_publicadas)} > umbral)")
        except Exception as e_snap:
            # Un fallo al registrar no debe impedir entregar el pronóstico,
            # pero queda visible en consola.
            print(f"[BOT TELEGRAM] ⚠️ No se pudo guardar el snapshot de pronóstico: "
                  f"{type(e_snap).__name__}: {e_snap}")

        if borrador:
            ruta_img_txt = f"\n• Ticket PNG: `{preparado['rutas'].get('imagen')}`" if preparado['rutas'].get('imagen') else ""
            ruta_val_txt = (f"\n• Ticket de Valor PNG: `{preparado['rutas'].get('ticket_valor')}`"
                            if preparado['rutas'].get('ticket_valor') else "")
            ruta_cuadro_txt = (f"\n• Cuadro Especialista PNG: `{preparado['rutas'].get('cuadro')}`"
                               if preparado['rutas'].get('cuadro') else "")
            ruta_h2h_txt = (f"\n• Cuadro H2H (2024–2026) PNG: `{preparado['rutas'].get('cuadro_h2h')}`"
                            if preparado['rutas'].get('cuadro_h2h')
                            else f"\n• H2H (2024–2026): `{preparado.get('estado_h2h_2024_2026') or 'Sin enfrentamientos directos en 2024–2026'}`")
            enviar_seguro(
                bot, chat_id,
                f"📝 *Modo borrador*: análisis listo, nada enviado.\n"
                f"• Selecciones > {umbral:.0%}: `{len(analisis.selecciones_filtradas)}`\n"
                f"• Informe: `{preparado['rutas']['informe']}`{ruta_img_txt}{ruta_val_txt}{ruta_cuadro_txt}{ruta_h2h_txt}")
            return

        resultado = servicio.enviar_analisis(
            preparado,
            enviar_foto_fn=servicio.enviar_fn_telegram_foto,
            chat_id=chat_id,
            nueva_ejecucion=True)
        if not resultado.get("enviado"):
            # Texto plano: la ruta lleva guiones bajos que rompen Markdown
            ruta_guardada = preparado['rutas'].get('imagen') or preparado['rutas']['informe']
            motivo = resultado.get('motivo', '')
            if "ya confirmado previamente" in motivo:
                enviar_seguro(
                    bot, chat_id,
                    f"ℹ️ Este análisis ya fue generado y enviado previamente a Telegram.\n"
                    f"• Ticket: {preparado['rutas'].get('imagen')}\n"
                    f"• Informe completo: {preparado['rutas']['informe']}")
            else:
                enviar_seguro(
                    bot, chat_id,
                    f"⚠️ No se pudo enviar el análisis:\n{motivo}\n\n"
                    f"El análisis y el ticket gráfico quedan guardados en:\n"
                    f"{ruta_guardada}")
        else:
            print(f"[BOT TELEGRAM] ✅ Ticket gráfico de mercados enviado exitosamente como PNG "
                  f"(id {preparado['id_trabajo']})")
            if resultado.get("ticket_valor_enviado"):
                print(f"[BOT TELEGRAM] ✅ Ticket de Valor (+EV) enviado como segunda imagen "
                      f"({preparado['rutas'].get('ticket_valor')})")
            if resultado.get("cuadro_enviado"):
                print(f"[BOT TELEGRAM] ✅ Cuadro Especialista enviado como tercera imagen "
                      f"({preparado['rutas'].get('cuadro')})")
            elif resultado.get("motivo_cuadro") or preparado['rutas'].get('cuadro') is None:
                # El ticket ya llegó: se avisa de lo que falta, sin fingir un envío doble.
                detalle = resultado.get("motivo_cuadro") or "no se pudo generar la imagen"
                ruta_c = preparado['rutas'].get('cuadro') or "—"
                enviar_seguro(
                    bot, chat_id,
                    f"ℹ️ El ticket se envió, pero el Cuadro Especialista no: {detalle}\n"
                    f"Ruta esperada: {ruta_c}")

            if resultado.get("cuadro_h2h_enviado"):
                print(f"[BOT TELEGRAM] ✅ Cuadro H2H (2024–2026) enviado como lámina independiente "
                      f"({preparado['rutas'].get('cuadro_h2h')})")
            elif resultado.get("estado_h2h_2024_2026") == "Sin enfrentamientos directos en 2024–2026":
                print(f"[BOT TELEGRAM] ℹ️ {equipo_a} vs {equipo_b}: Sin enfrentamientos directos en 2024–2026")
                enviar_seguro(bot, chat_id, "ℹ️ H2H (2024–2026): Sin enfrentamientos directos en 2024–2026.")

    except Exception as e:
        print(f"[BOT TELEGRAM] ❌ Error en el análisis de mercados: {type(e).__name__}: {e}")
        try:
            enviar_seguro(bot, chat_id,
                          f"❌ El análisis de «{equipo_a} vs {equipo_b}» falló:\n"
                          f"{type(e).__name__}: {e}")
        except Exception:
            pass
    finally:
        with lock_analisis:
            auditoria_en_curso = False


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUACIÓN DE ACIERTOS Y CALIBRACIÓN  (/evaluar)
# ═══════════════════════════════════════════════════════════════════════════════

def _parsear_argumentos_evaluar(texto: str) -> tuple[list[str] | None, bool, str]:
    """
    Extrae filtro de equipos y modo borrador del comando /evaluar.
    Formatos aceptados:
        /evaluar                        -> todos, borrador=False
        /evaluar borrador               -> todos, borrador=True
        /evaluar Roma                   -> ['Roma'], borrador=False
        /evaluar Roma borrador          -> ['Roma'], borrador=True
        /evaluar Roma vs Real Madrid    -> ['Roma', 'Real Madrid'], borrador=False
        /evaluar Roma vs Real Madrid borrador -> ['Roma', 'Real Madrid'], borrador=True
    """
    from fuentes_xg import extraer_liga_de_texto
    texto_norm = normalizar_comando(texto or "")
    cuerpo = re.sub(
        r"^/(?:evaluar|evaluacion|evaluación|aciertos|calibracion|calibración)(?:@\w+)?",
        "", texto_norm or "", flags=re.I
    ).strip()
    borrador = bool(re.search(r"\bborrador\b", cuerpo, flags=re.I))
    cuerpo = re.sub(r"\bborrador\b", " ", cuerpo, flags=re.I).strip()
    cuerpo = cuerpo.strip().strip('"').strip("'").strip()
    if not cuerpo:
        return None, borrador, ""
    _, cuerpo_sin_liga = extraer_liga_de_texto(cuerpo)
    cuerpo = (cuerpo_sin_liga or cuerpo).strip()
    partes = re.split(r"\s+vs\.?\s+", cuerpo, flags=re.I)
    partes = [p.strip() for p in partes if p.strip()]
    if len(partes) >= 2:
        return [partes[0], partes[1]], borrador, f"{partes[0]} vs {partes[1]}"
    return [cuerpo], borrador, cuerpo


@bot.message_handler(commands=["evaluar", "evaluacion", "evaluación", "aciertos",
                               "calibracion", "calibración"])
@solo_autorizado
def cmd_evaluar(message):
    """
    Evalúa los pronósticos ya emitidos contra sus resultados reales.

    Uso:
        /evaluar                         → evalúa todos los pronósticos guardados
        /evaluar <Equipo>                → filtra exclusivamente por un equipo (ej. /evaluar Roma)
        /evaluar <Eq A> vs <Eq B>        → filtra por enfrentamiento (ej. /evaluar Roma vs Real Madrid)
        /evaluar [filtro] borrador       → prepara el resumen sin enviarlo
    """
    chat_id = message.chat.id
    filtro_equipos, borrador, texto_filtro = _parsear_argumentos_evaluar(getattr(message, "text", "") or "")

    if texto_filtro:
        enviar_seguro(
            bot, chat_id,
            f"⏳ *Recibido.* Filtrando y verificando pronósticos para *{texto_filtro}*... "
            f"Esto tomará un momento.")
        print(f"[BOT TELEGRAM] ⏳ Acuse de evaluación enviado para '{texto_filtro}' (borrador={borrador})")
    else:
        enviar_seguro(
            bot, chat_id,
            "⏳ *Recibido.* Verificando resultados finales de los partidos pronosticados "
            "y evaluando cada selección contra su probabilidad original... "
            "Esto tomará un momento.")
        print(f"[BOT TELEGRAM] ⏳ Acuse de evaluación general enviado (borrador={borrador})")

    hilo = threading.Thread(target=_hilo_ejecutar_evaluacion,
                            args=(chat_id, borrador, filtro_equipos, texto_filtro), daemon=True)
    hilo.start()


def _hilo_ejecutar_evaluacion(chat_id, borrador: bool,
                              filtro_equipos: list[str] | None = None,
                              texto_filtro: str = "") -> None:
    """Ejecuta la evaluación y despacha el resumen."""
    try:
        import evaluacion_pronosticos as evaluacion
        import servicio_mercados as servicio

        def progreso(msg: str) -> None:
            print(f"[EVALUACIÓN] {msg}")

        preparado = evaluacion.preparar_evaluacion(
            aviso=progreso,
            enviar_media_fn=servicio.enviar_fn_telegram_foto,
            filtro_equipos=filtro_equipos)

        # Manejo de vacíos: sin datos para el filtro solicitado
        if preparado.get("sin_datos_filtro"):
            enviar_seguro(bot, chat_id, preparado["mensaje"])
            return

        informe = preparado["informe"]
        evidencia = informe.evidencia

        if borrador:
            filtro_txt = f" ({preparado['filtro']})" if preparado.get("filtro") else ""
            enviar_seguro(
                bot, chat_id,
                f"📝 Modo borrador: evaluación lista{filtro_txt}, nada enviado.\n"
                f"Ejecución: {preparado['id_ejecucion']}\n"
                f"Partidos únicos: {evidencia['partidos_unicos']}\n"
                f"Evaluables: {evidencia['selecciones_evaluables']}\n"
                f"Imágenes de tabla: {len(preparado.get('imagenes') or [])}\n"
                f"Informe: {preparado['rutas'].get('informe_md')}")
            return

        resultado = evaluacion.enviar_resumen(
            preparado, enviar_fn=servicio.enviar_fn_telegram, chat_id=chat_id,
            imagenes=preparado.get("imagenes"))
        if not resultado.get("enviado"):
            enviar_seguro(
                bot, chat_id,
                f"⚠️ La evaluación se completó, pero el resumen no se envió:\n"
                f"{resultado.get('motivo')}\n\n"
                f"Estado: {resultado.get('estado')}\n"
                f"Informe completo: {preparado['rutas'].get('informe_md')}")
        else:
            print(f"[BOT TELEGRAM] ✅ Evaluación enviada "
                  f"(id {resultado.get('id_ejecucion')}, "
                  f"mensaje {resultado.get('message_id')}, "
                  f"{resultado.get('imagenes_enviadas')} imagen(es))")

    except Exception as e:
        print(f"[BOT TELEGRAM] ❌ Error en la evaluación: {type(e).__name__}: {e}")
        try:
            enviar_seguro(bot, chat_id,
                          f"❌ La evaluación falló:\n{type(e).__name__}: {e}")
        except Exception:
            pass


@bot.message_handler(func=lambda m: getattr(m, "text", "") and bool(re.match(r"^[.\s]*/", str(m.text).strip())))
@solo_autorizado
def cmd_fallback_o_desconocido(message):
    """
    Enrutador de respaldo resiliente:
    Si un comando con formato no estándar (mayúsculas o espacios del teclado móvil)
    no fue capturado por los filtros de telebot, este manejador lo normaliza y lo
    despacha directamente a su función oficial. Si es desconocido, informa al usuario.
    """
    txt_norm = normalizar_comando(getattr(message, "text", "") or "")
    message.text = txt_norm
    partes = txt_norm.split(maxsplit=1)
    cmd = partes[0].lower().split("@")[0] if partes else ""

    if cmd in ("/conciencia", "/memoria", "/madurez"):
        return cmd_conciencia(message)
    if cmd in ("/agente", "/antigravity", "/ia", "/pensar"):
        return cmd_agente(message)
    if cmd in ("/diagnosticar", "/autodiagnostico", "/salud", "/reparar"):
        return cmd_diagnosticar(message)
    if cmd in ("/estado", "/status"):
        return cmd_estado(message)
    if cmd in ("/ayuda", "/start", "/help"):
        return cmd_ayuda(message)
    if cmd in ("/analizar_vivo", "/scan", "/analizar"):
        return cmd_analizar_vivo(message)
    if cmd in ("/auditar", "/auditoria", "/auditoría", "/historial"):
        return cmd_auditar(message)
    if cmd in ("/h2h", "/vs", "/comparar", "/comparativa", "/duelo"):
        return cmd_h2h(message)
    if cmd in ("/mercados", "/mercado", "/probabilidades", "/analizar_mercados"):
        return cmd_mercados(message)
    if cmd in ("/evaluar", "/evaluacion", "/evaluación", "/aciertos", "/calibracion", "/calibración"):
        return cmd_evaluar(message)
    if cmd in ("/resultado", "/acierto", "/fallo", "/exito", "/fracaso"):
        return cmd_resultado(message)

    enviar_seguro(
        bot,
        message,
        f"❓ *Comando `{cmd}` no reconocido.*\n\n"
        "Usa `/ayuda` para consultar el catálogo de comandos interactivos disponibles.",
        es_reply=True
    )


class KeepAliveHandler(BaseHTTPRequestHandler):
    """Manejador HTTP ligero para Keep-Alive 24/7 en Hugging Face Spaces / Render / UptimeRobot."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"200 OK - Agente Futbol 24/7 Activo")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        # Silenciar logs repetitivos de healthcheck para mantener limpia la consola
        return


def iniciar_servidor_keep_alive(host: str = "0.0.0.0", port: int | None = None) -> threading.Thread:
    """
    Inicia en segundo plano (daemon=True) un servidor HTTP ligero en 0.0.0.0:PORT
    (por defecto 7860) que responde '200 OK - Agente Futbol 24/7 Activo' en '/'.
    """
    puerto = int(port if port is not None else os.getenv("PORT", "7860"))

    def _servir():
        try:
            httpd = HTTPServer((host, puerto), KeepAliveHandler)
            print(f"[KEEP-ALIVE HTTP] 🌐 Servidor activo en http://{host}:{puerto}/ (200 OK - Agente Futbol 24/7 Activo)")
            httpd.serve_forever()
        except Exception as exc:
            print(f"[KEEP-ALIVE HTTP] ⚠️ No se pudo iniciar en puerto {puerto}: {exc}")

    hilo = threading.Thread(target=_servir, name="KeepAliveHTTP", daemon=True)
    hilo.start()
    return hilo


def iniciar_servidor_bot():
    """Bucle infinito de escucha de Telegram (Long Polling resiliente e inmortal)."""
    if not verificar_credenciales():
        sys.exit(1)

    iniciar_servidor_keep_alive()

    print("\n" + "=" * 70)
    print("  🤖 BOT INTERACTIVO DE TELEGRAM — DEMONIO EN LÍNEA")
    print("=" * 70)
    print(f"  • Chat ID Autorizado: {CHAT_ID_AUTORIZADO or 'No configurado (pendiente)'}")
    print(f"  • Keep-Alive HTTP:    0.0.0.0:{os.getenv('PORT', '7860')} (200 OK - Agente Futbol 24/7 Activo)")
    print("  • Comandos activos:   /agente, /diagnosticar, /estado, /analizar_vivo, /auditar, /h2h, /mercados, /evaluar, /resultado, /conciencia, /ayuda")
    print("  • Normalizador móvil: Activo (insensible a mayúsculas y espacios tras /)")
    print("  • Modo de ejecución:  Escucha continua (Long Polling Resiliente)")
    print("  • Presiona Ctrl + C en esta consola para detener el demonio.")
    print("=" * 70 + "\n")

    # Timeouts explícitos para todas las conexiones HTTP subyacentes de Telegram
    telebot.apihelper.CONNECT_TIMEOUT = 15
    telebot.apihelper.READ_TIMEOUT = 30
    telebot.apihelper.RETRY_ON_ERROR = True

    reintentos = 0
    while True:
        try:
            # Si una desconexión previa activó stop_polling internamente en TeleBot, lo limpiamos para reintentar
            if hasattr(bot, "_TeleBot__stop_polling"):
                bot._TeleBot__stop_polling.clear()
            # Los handlers de trabajo pesado (/auditar, /h2h, /mercados) devuelven
            # el control de inmediato lanzando un hilo propio, por lo que este
            # bucle de recepción no se bloquea y el acuse llega al instante.
            bot.infinity_polling(timeout=20, long_polling_timeout=25, skip_pending=False)
            time.sleep(2)
        except KeyboardInterrupt:
            print("\n🛑 Deteniendo bot interactivo a petición del operador...")
            break
        except Exception as e:
            reintentos += 1
            espera = min(30, 2 + reintentos * 2)
            print(f"[BOT TELEGRAM] ⚠️ Error en polling ({type(e).__name__}: {e}). Reconectando en {espera}s (intento {reintentos})...")
            time.sleep(espera)


if __name__ == "__main__":
    iniciar_servidor_bot()
