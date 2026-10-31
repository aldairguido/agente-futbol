"""
agente_futbol/notificador_telegram.py
───────────────────────────────────────────────────────────────────────────────
Notificador de Telegram para el Agente de Apuestas de Fútbol.

Responsabilidades:
  • Leer TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID desde config / .env.
  • Extraer específicamente la sección VIP de "🏆 TICKETS RECOMENDADOS DEL DÍA"
    del archivo Markdown generado.
  • Formatear el contenido para lectura óptima en smartphones (Telegram).
  • Enviar mensaje vía POST a la API oficial de Telegram con requests.
  • Gestionar límite de 4096 caracteres y fallback seguro ante caracteres especiales.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import sys
import os
import re
from typing import Any
import requests

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config


def extraer_seccion_tickets(ruta_informe: str) -> str:
    """
    Lee el archivo de informe Markdown y extrae exclusivamente el bloque de
    '🏆 TICKETS RECOMENDADOS DEL DÍA'.
    """
    if not os.path.exists(ruta_informe):
        print(f"[TELEGRAM] ⚠️ No existe el archivo de informe: {ruta_informe}")
        return ""

    try:
        with open(ruta_informe, "r", encoding="utf-8") as f:
            contenido = f.read()

        inicio = contenido.find("## 🏆 TICKETS RECOMENDADOS DEL DÍA")
        if inicio == -1:
            print("[TELEGRAM] ℹ️ Sección de tickets VIP no encontrada en el informe.")
            return ""

        # Buscar el fin de la sección (el siguiente '---' o '## 📊')
        fin = contenido.find("\n---", inicio + 35)
        if fin == -1:
            fin = contenido.find("\n## 📊", inicio + 35)
        if fin == -1:
            fin = contenido.find("\n## Inventario", inicio + 35)
        if fin == -1:
            fin = len(contenido)

        seccion = contenido[inicio:fin].strip()
        return seccion

    except Exception as e:
        print(f"[TELEGRAM] ❌ Error extrayendo sección de tickets: {e}")
        return ""


def extraer_resumen_jornada(ruta_informe: str) -> str:
    """
    Lee el archivo de informe Markdown y extrae el bloque de
    '## 📊 Resumen Ejecutivo y Auditoría Cuantitativa'.
    """
    if not os.path.exists(ruta_informe):
        return ""
    try:
        with open(ruta_informe, "r", encoding="utf-8") as f:
            contenido = f.read()

        inicio = contenido.find("## 📊 Resumen")
        if inicio == -1:
            inicio = contenido.find("## Resumen")
        if inicio == -1:
            return ""

        fin = contenido.find("## 🏆 TICKETS", inicio)
        if fin == -1:
            fin = contenido.find("\n---", inicio + 20)
        if fin == -1:
            fin = inicio + 800

        return contenido[inicio:fin].strip()
    except Exception as e:
        print(f"[TELEGRAM] ⚠️ Error extrayendo resumen de jornada: {e}")
        return ""


def formatear_para_telegram(texto_seccion: str, fecha: str = "", texto_resumen: str = "") -> str:
    """
    Adapta el contenido de la sección VIP a un formato visual limpio, compacto
    y perfectamente legible en Telegram para celular.
    """
    if not texto_seccion:
        return ""

    lineas_out: list[str] = []

    lineas_out.append("🏆 *INFORME CUANTITATIVO DE APUESTAS (+EV TRADER)*")
    if fecha:
        lineas_out.append(f"📅 *Jornada:* `{fecha}`")
    lineas_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━")

    if texto_resumen:
        m_partidos = re.search(r"Partidos auditados.*?\*\*(\d+)\*\*", texto_resumen)
        m_mkts = re.search(r"Mercados totales examinados.*?\*\*(\d+)\*\*", texto_resumen)
        m_desc = re.search(r"Mercados descartados.*?\*\*(\d+)\*\*", texto_resumen)
        m_sec = re.search(r"Opciones secundarias.*?\*\*(\d+)\*\*", texto_resumen)
        m_diam = re.search(r"Tickets Diamante Aprobados.*?\*\*(\d+)\*\*", texto_resumen)

        lineas_out.append("📊 *EMBUDO Y AUDITORÍA DE MERCADOS:*")
        if m_partidos:
            lineas_out.append(f"• ⚽ *Partidos evaluados:* `{m_partidos.group(1)}`")
        if m_mkts:
            lineas_out.append(f"• 🔢 *Mercados examinados:* `{m_mkts.group(1)}`")
        if m_desc:
            lineas_out.append(f"• 🔴 *Descartados (+EV < 5%):* `{m_desc.group(1)}`")
        if m_sec:
            lineas_out.append(f"• 🟢 *Opciones secundarias (tendencia):* `{m_sec.group(1)}` _(no operables)_")
        if m_diam:
            lineas_out.append(f"• 💎 *TICKETS DIAMANTE (+EV VALIDADOS):* *`{m_diam.group(1)}`*")
        lineas_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        lineas_out.append("💡 _Aclaración: Solo se muestran los Tickets Diamante. Las opciones secundarias 'verdes' cumplen tendencias estadísticas pero no superaron el margen matemático exigido (+EV >= +5.0%)._\n")
    else:
        lineas_out.append("🛡️ *Estrategia de Bankroll & Riesgo:*")
        lineas_out.append("• *💎 Filtro Diamante:* Ineficiencia matemática (+EV >= +5.0%) y Quarter-Kelly")
        lineas_out.append("• *⚡ Parlays x2:* Cuota combinada ~ 2.00 sin riesgo cruzado")
        lineas_out.append("• *🔒 Aislamiento:* Cero partidos duplicados entre tickets")
        lineas_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    # Extraer y formatear Simples Diamante
    if "### 💎 Apuestas Simples Diamante" in texto_seccion:
        lineas_out.append("💎 *TICKETS DIAMANTE (+EV VALIDADOS)*\n")
        
        filas_diamante_ev = re.findall(
            r"\|\s*💎\s*\*\*(.*?)\*\*\s*\|\s*\*\*(.*?)\*\*\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*\*\*(.*?)\*\*\s*\|\s*\*\*(.*?)\*\*\s*\|\s*✅\s*(.*?)\s*\|\s*\*\*(.*?)\*\*\s*\|\s*`(.*?)`\s*\|",
            texto_seccion
        )
        filas_diamante_clasicas = re.findall(
            r"\|\s*💎\s*\*\*(.*?)\*\*\s*\|\s*\*\*(.*?)\*\*\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*\*\*(.*?)\*\*\s*\|\s*\*\*(.*?)\*\*\s*\|\s*✅\s*(.*?)\s*\|\s*`(.*?)`\s*\|",
            texto_seccion
        )

        if filas_diamante_ev:
            for t_id, partido, comp, mkt, sel, cuota, h2h, ev, stake in filas_diamante_ev:
                lineas_out.append(f"👉 *{t_id.strip()}:* *{partido.strip()}*")
                lineas_out.append(f"   🏆 *Torneo:* {comp.strip()}")
                lineas_out.append(f"   🎯 *Mercado:* {mkt.strip()}")
                lineas_out.append(f"   🎲 *Selección:* *{sel.strip()}*")
                lineas_out.append(f"   💰 *Cuota Betsson:* `{cuota.strip()}`")
                lineas_out.append("")
                lineas_out.append("   📐 *Métricas Cuantitativas de Poisson (+EV):*")
                lineas_out.append(f"   • Probabilidad Real (True Prob): `{h2h.strip()}`")
                try:
                    c_num = float(cuota.strip())
                    p_impl_str = f"{(1.0 / c_num):.1%}" if c_num > 1.0 else "N/D"
                except Exception:
                    p_impl_str = "N/D"
                lineas_out.append(f"   • Probabilidad Implícita (Cuota): `{p_impl_str}`")
                lineas_out.append(f"   • Ventaja Matemática: *{ev.strip()}*")
                lineas_out.append(f"   • Gestión de Capital (Quarter-Kelly): `{stake.strip()}`")
                lineas_out.append("")
                lineas_out.append("   📝 *Observaciones & Contexto Secundario:*")
                m_just = re.search(rf"> \*\*Justificación {re.escape(t_id.strip())}\*\*:\s*(.*?)(?=\n> \*\*Justificación|\n\n|\Z)", texto_seccion, re.DOTALL)
                if not m_just:
                    m_just = re.search(rf"- \*\*Análisis Técnico\*\*:\s*(.*?)(?=\n- |\n\n|\Z)", texto_seccion, re.DOTALL)
                if m_just:
                    lineas_out.append(f"   • Análisis Técnico: {m_just.group(1).strip()}")
                else:
                    lineas_out.append("   • Tendencia oficial validada.")
                lineas_out.append("   ─────────────────────────────\n")
        elif filas_diamante_clasicas:
            for t_id, partido, comp, mkt, sel, cuota, h2h, stake in filas_diamante_clasicas:
                lineas_out.append(f"👉 *{t_id.strip()}:* *{partido.strip()}*")
                lineas_out.append(f"   🏆 *Torneo:* {comp.strip()}")
                lineas_out.append(f"   🎯 *Mercado:* {mkt.strip()}")
                lineas_out.append(f"   🎲 *Selección:* *{sel.strip()}*")
                lineas_out.append(f"   💰 *Cuota Betsson:* `{cuota.strip()}`")
                lineas_out.append("")
                lineas_out.append("   📐 *Métricas de Valor:*")
                lineas_out.append(f"   • Probabilidad / H2H: `{h2h.strip()}`")
                lineas_out.append(f"   • Gestión de Capital: `{stake.strip()}`")
                lineas_out.append("   ─────────────────────────────\n")
        else:
            lineas_out.append("ℹ️ _Sin selecciones simples que superen el filtro hoy._\n")

    # Extraer y formatear Parlays Dobles
    if "### ⚡ Parlays / Combinadas Dobles" in texto_seccion:
        lineas_out.append("⚡ *PARLAYS / COMBINADAS DOBLES (~2.00)*\n")
        bloques_parlay = re.split(r"#### 🎫 TICKET\s*", texto_seccion)[1:]

        if bloques_parlay:
            for bp in bloques_parlay:
                primer_linea = bp.split("\n")[0]
                m_cab = re.search(r"(PARLAY-\d+|[A-Z0-9_-]+)\s*—\s*Cuota Combinada:\s*\*\*(.*?)\*\*", primer_linea)
                p_id = m_cab.group(1).strip() if m_cab else "PARLAY"
                p_cuota = m_cab.group(2).strip() if m_cab else "?"

                nombre_ticket = f"TICKET {p_id}" if "TICKET" not in p_id else p_id
                lineas_out.append(f"🎫 *{nombre_ticket}* — *Cuota Total:* `{p_cuota}`")

                patas = re.findall(
                    r"\|\s*Pata\s*\d+\s*\|\s*\*\*(.*?)\*\*\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*\*\*(.*?)\*\*\s*\|\s*`(.*?)`\s*\|",
                    bp
                )
                for i, (partido, comp, mkt, sel, c_pata) in enumerate(patas, 1):
                    lineas_out.append(f"   🔹 *Pata {i}:* {partido.strip()} ({comp.strip()})")
                    lineas_out.append(f"      👉 {mkt.strip()}: *{sel.strip()}* @ `{c_pata.strip()}`")

                m_stake = re.search(r"\*\s*\*\*Stake Sugerido\*\*:\s*`(.*?)`", bp)
                if m_stake:
                    lineas_out.append(f"   💵 *Stake:* `{m_stake.group(1).strip()}`")
                lineas_out.append("   ─────────────────────────────\n")
        else:
            lineas_out.append("ℹ️ _Sin combinadas seguras disponibles hoy._\n")

    lineas_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    lineas_out.append("⚠️ _Análisis estadístico automatizado. No constituye garantía de resultado. Juega responsablemente._")

    return "\n".join(lineas_out)


def enviar_mensaje_telegram(mensaje: str, parse_mode: str = "Markdown") -> bool:
    """
    Envía un mensaje a Telegram mediante la API oficial usando requests.
    Maneja división en bloques si excede 4000 caracteres y fallback sin formato
    si la sintaxis Markdown tiene discrepancias.
    """
    token   = (config.TELEGRAM_BOT_TOKEN or "").strip()
    chat_id = (config.TELEGRAM_CHAT_ID or "").strip()

    if not token or not chat_id:
        print("[TELEGRAM] ℹ️ Notificaciones inactivas: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados en .env")
        return False

    url_api = f"https://api.telegram.org/bot{token}/sendMessage"

    # Telegram impone un límite estricto de 4096 caracteres
    chunks = []
    max_len = 3900
    if len(mensaje) <= max_len:
        chunks = [mensaje]
    else:
        partes = mensaje.split("\n\n")
        chunk_actual = ""
        for p in partes:
            if len(chunk_actual) + len(p) + 2 <= max_len:
                chunk_actual += (p + "\n\n")
            else:
                if chunk_actual:
                    chunks.append(chunk_actual.strip())
                chunk_actual = p + "\n\n"
        if chunk_actual:
            chunks.append(chunk_actual.strip())

    todos_enviados = True

    for i, chk in enumerate(chunks, 1):
        payload = {
            "chat_id":                  chat_id,
            "text":                     chk,
            "parse_mode":               parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url_api, json=payload, timeout=12)
            if resp.status_code == 200:
                print(f"[TELEGRAM] ✅ Mensaje {i}/{len(chunks)} enviado con éxito a Telegram.")
            else:
                # Fallback: reintentar sin parse_mode si falla por formato
                print(f"[TELEGRAM] ⚠️ Error {resp.status_code} ({resp.text}). Reintentando sin formato...")
                payload.pop("parse_mode", None)
                resp_fallback = requests.post(url_api, json=payload, timeout=12)
                if resp_fallback.status_code == 200:
                    print(f"[TELEGRAM] ✅ Mensaje {i}/{len(chunks)} entregado con fallback de texto plano.")
                else:
                    print(f"[TELEGRAM] ❌ Falló entrega a Telegram: {resp_fallback.text}")
                    todos_enviados = False

        except Exception as e:
            print(f"[TELEGRAM] ❌ Excepción de conexión al enviar a Telegram: {e}")
            todos_enviados = False

    return todos_enviados


def enviar_informe_tickets(ruta_informe: str, fecha_objetivo: str = "") -> bool:
    """
    Función principal de integración para agente.py:
    Extrae los tickets VIP, los formatea y los despacha al teléfono del usuario.
    """
    seccion = extraer_seccion_tickets(ruta_informe)
    if not seccion:
        print("[TELEGRAM] ⏩ Sin sección VIP de tickets. Omitiendo envío.")
        return False

    # Verificar que existan tickets reales y no solo mensajes de 'vacío'
    tiene_diamante = "DIAMANTE-" in seccion
    tiene_parlays  = "PARLAY-" in seccion

    if not (tiene_diamante or tiene_parlays):
        print("[TELEGRAM] ⏩ No se generaron tickets válidos en esta jornada. Omitiendo notificación.")
        return False

    resumen = extraer_resumen_jornada(ruta_informe)
    mensaje_formateado = formatear_para_telegram(seccion, fecha_objetivo, resumen)
    return enviar_mensaje_telegram(mensaje_formateado, parse_mode="Markdown")
