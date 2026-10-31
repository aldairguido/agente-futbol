"""
agente_futbol/config.py
Parámetros globales del agente. Editar aquí para ajustar comportamiento.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

RUTA_BASE    = Path(__file__).resolve().parent
BASE_DIR     = str(RUTA_BASE)
INFORME_DIR  = str(RUTA_BASE / "informes")

load_dotenv(RUTA_BASE / ".env", override=True)
load_dotenv(override=True)

# ─── Zona horaria y ventana de partidos ──────────────────────────────────────
TIMEZONE = "America/Lima"
# El agente analiza partidos cuya hora de inicio esté en esta ventana (hora local)
VENTANA_INICIO_HORA = 0    # 00:00
VENTANA_FIN_HORA    = 23   # 23:59  (cubre todo el día)

# ─── Filtro de cuotas ────────────────────────────────────────────────────────
CUOTA_MINIMA = 1.30        # La cuota debe ser ESTRICTAMENTE mayor que este valor

# ─── Muestras por equipo ─────────────────────────────────────────────────────
MUESTRA_GENERAL    = 10    # Últimos N partidos oficiales (cualquier sede)
MUESTRA_CONDICION  = 5     # Últimos N oficiales en la misma condición (casa/fuera)
MUESTRA_H2H        = 5     # Últimos N oficiales entre ambos equipos

# ─── Modelo de IA (Google Gemini API) ─────────────────────────────────────────
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_MODEL = "gpt-4o"
OPENAI_MAX_TOKENS = 4096

# ─── URLs base ───────────────────────────────────────────────────────────────
BETSSON_URL    = "https://www.betsson.pe/es/apuestas-deportivas/futbol"
FLASHSCORE_URL = "https://www.flashscore.com"
SOFASCORE_URL  = "https://www.sofascore.com"

# ─── Timeouts del navegador (milisegundos) ───────────────────────────────────
TIMEOUT_NAVEGACION  = 30_000   # 30 segundos para cargar una página
TIMEOUT_ELEMENTO    = 15_000   # 15 segundos para esperar un elemento

# ─── Pausa entre solicitudes (segundos) ──────────────────────────────────────
# Respetar los servidores; no hacer peticiones en ráfaga
PAUSA_ENTRE_PARTIDOS = 2.0
PAUSA_ENTRE_PAGINAS  = 1.0

# ─── Configuración Telegram ──────────────────────────────────────────────────
# Lee TELEGRAM_TOKEN (o TELEGRAM_BOT_TOKEN) y TELEGRAM_CHAT_ID estrictamente desde el entorno o .env local
TELEGRAM_TOKEN     = (os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip().strip('"').strip("'")
TELEGRAM_BOT_TOKEN = TELEGRAM_TOKEN
TELEGRAM_CHAT_ID   = (os.getenv("TELEGRAM_CHAT_ID") or "").strip().strip('"').strip("'")

