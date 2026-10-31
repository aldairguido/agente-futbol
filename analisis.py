"""
agente_futbol/analisis.py
Módulo de análisis con OpenAI GPT-4o.
Responsabilidades:
  - Recibir estadísticas recolectadas de Flashscore/Sofascore + mercados de Betsson
  - Aplicar los criterios de la especificación (§6–§9)
  - Asignar semáforo: Verde / Amarillo / Rojo / Gris
  - Devolver análisis estructurado listo para el informe
"""

import os
import json
from openai import OpenAI
from dotenv import load_dotenv
import config

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ─── Prompt del sistema ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """
Eres un agente analista de apuestas deportivas de fútbol. Tu trabajo es examinar
estadísticas reales de partidos y mercados disponibles en Betsson Perú, y determinar
qué selecciones tienen suficiente evidencia estadística para considerarse candidatas.

REGLAS ESTRICTAS que debes aplicar siempre:

1. CUOTA MÍNIMA: Solo evalúas selecciones con cuota ESTRICTAMENTE MAYOR que 1.30.
   Una cuota de 1.30 NO pasa. Una cuota de 1.31 sí pasa este requisito.

2. PARTIDOS OFICIALES: Excluye amistosos de todas las muestras. Liga, copas y
   competiciones continentales son válidas; amistosos no.

3. MUESTRAS:
   - General: últimos 10 partidos oficiales de cada equipo (cualquier sede)
   - Condición: últimos 5 en la misma condición (casa para local, fuera para visitante)
   - H2H: últimos 5 oficiales entre ambos equipos

4. AUSENTE ≠ CERO: Si un dato no está disponible, dilo. No inventes cifras.

5. SEMÁFORO:
   - VERDE: Cumple condiciones con información suficiente y evidencia consistente
   - AMARILLO: Evidencia contradictoria, alineación incierta o datos faltantes importantes
   - ROJO: Incumple una condición obligatoria o la evaluación no respalda la selección
   - GRIS: Dato o método necesario no disponible

6. METODOLOGÍA: En mercados complementarios sin modelado bivariado de Poisson, clasifica como "Basado en tendencia histórica y xG". Usa "frecuencia histórica" y "evidencia consistente".

7. ERRORES A EVITAR:
   - No confundir "más de 0.5 goles" con "más de 5.5 goles"
   - No multiplicar probabilidades de goles y resultado como si fueran independientes
   - No atribuir goles de un jugador anterior al jugador actual en esa posición
   - No borrar derrotas estadísticas por explicación retrospectiva
   - No elevar 5/5 histórico a certeza predictiva

8. POR MERCADO: Traduce cada selección a una regla verificable de acierto/pérdida/devolución.

FORMATO DE RESPUESTA: Devuelve SIEMPRE un JSON válido con esta estructura:
{
  "partido": "Local vs Visitante",
  "competicion": "nombre",
  "candidatos": [
    {
      "semaforo": "VERDE|AMARILLO|ROJO|GRIS",
      "mercado": "nombre exacto del mercado",
      "seleccion": "selección exacta",
      "cuota": 0.00,
      "periodo": "tiempo reglamentario|1T|2T",
      "regla_liquidacion": "descripción de cómo se liquida",
      "evidencia_favorable": ["punto 1", "punto 2"],
      "evidencia_contraria": ["punto 1"],
      "frecuencia_general": "X de Y partidos (N temporada actual, M anterior)",
      "frecuencia_condicion": "X de Y partidos en condición",
      "frecuencia_h2h": "X de Y encuentros H2H",
      "incertidumbres": ["alineación no confirmada", ...],
      "probabilidad": "Basado en tendencia histórica y xG",
      "motivo_semaforo": "explicación del estado asignado"
    }
  ],
  "descartados": [
    {
      "seleccion": "nombre",
      "cuota": 0.00,
      "motivo_descarte": "explicación"
    }
  ],
  "no_evaluables": [
    {
      "seleccion": "nombre",
      "motivo": "dato no disponible"
    }
  ],
  "advertencias": ["lista de alertas importantes sobre los datos"]
}
"""


from analizador_experto import AnalizadorExperto

# ─── Función principal de análisis ───────────────────────────────────────────

def analizar_partido(partido: dict, estadisticas: dict, mercados: list[dict]) -> dict:
    """
    Ejecuta el Razonamiento Cognitivo y Análisis Experto Deportivo en 4 capas.
    Si la API de OpenAI está disponible, la usa para enriquecer la síntesis;
    si no está disponible o arroja 429 (sin saldo), el motor experto en Python
    resuelve de forma determinista y completa la evaluación.

    partido: dict con local, visitante, liga, hora
    estadisticas: dict con muestras general, condicion, h2h y atacantes
    mercados: lista de selecciones con cuota > 1.30 ya pre-filtradas
    """
    if not mercados:
        return {
            "partido": f"{partido.get('local')} vs {partido.get('visitante')}",
            "candidatos": [],
            "descartados": [],
            "no_evaluables": [],
            "advertencias": ["Sin mercados con cuota > 1.30 disponibles en Betsson"],
        }

    # 1. Ejecución del motor experto multicapa
    motor = AnalizadorExperto(cuota_minima=config.CUOTA_MINIMA)
    resultado_experto = motor.analizar(partido, estadisticas, mercados)

    # 2. Intento opcional de enriquecimiento con OpenAI (si hay clave y saldo)
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return resultado_experto

    try:
        prompt_usuario = _construir_prompt(partido, estadisticas, mercados)
        respuesta = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            max_tokens=config.OPENAI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt_usuario},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        contenido = respuesta.choices[0].message.content
        resultado_llm = json.loads(contenido)
        # Combinar razonamiento cognitivo local con respuesta de LLM
        resultado_llm["razonamiento_cognitivo"] = resultado_experto.get("razonamiento_cognitivo", {})
        resultado_llm["mercados_disponibles"] = mercados
        return resultado_llm

    except Exception as e:
        print(f"[ANÁLISIS] ℹ️ Usando Motor Experto Cognitivo local (OpenAI omitido: {e})")
        return resultado_experto


# ─── Construcción del prompt ──────────────────────────────────────────────────

def _construir_prompt(partido: dict, estadisticas: dict,
                       mercados: list[dict]) -> str:
    """Formatea todos los datos en un prompt legible para el modelo."""

    local     = partido.get("local", "?")
    visitante = partido.get("visitante", "?")
    liga      = partido.get("liga", "?")
    hora      = partido.get("hora", "?")

    # Resumir mercados disponibles
    mercados_texto = "\n".join([
        f"  - [{m.get('familia','?')}] {m.get('seleccion','?')} | "
        f"Cuota: {m.get('cuota','?')} | Periodo: {m.get('periodo','?')}"
        for m in mercados[:50]  # máximo 50 mercados en el prompt
    ])

    # Resumir estadísticas
    stats_local    = estadisticas.get("local", {})
    stats_visitante = estadisticas.get("visitante", {})
    h2h            = estadisticas.get("h2h", [])
    atacantes_local    = estadisticas.get("atacantes_local", [])
    atacantes_visitante = estadisticas.get("atacantes_visitante", [])

    def _resumir_muestra(muestra: list, nombre_equipo: str, tipo: str) -> str:
        if not muestra:
            return f"  {nombre_equipo} ({tipo}): Sin datos disponibles\n"
        lineas = [f"  {nombre_equipo} ({tipo}): {len(muestra)} partidos\n"]
        for i, p in enumerate(muestra[:10], 1):
            lineas.append(f"    {i}. {p.get('texto_raw', '')[:100]}\n")
            if p.get("completo"):
                for stat, vals in list(p.get("completo", {}).items())[:10]:
                    if isinstance(vals, dict):
                        lineas.append(f"       {stat}: L={vals.get('local','?')} "
                                     f"V={vals.get('visitante','?')}\n")
        return "".join(lineas)

    def _resumir_atacantes(atacantes: list, nombre_equipo: str) -> str:
        if not atacantes:
            return f"  {nombre_equipo}: Sin datos de atacantes\n"
        lineas = [f"  {nombre_equipo} atacantes:\n"]
        for j in atacantes:
            lineas.append(
                f"    - {j.get('nombre','?')} ({j.get('posicion_funcional','?')}) | "
                f"Titular: {j.get('titular','?')} | "
                f"Min: {j.get('minutos','?')} | "
                f"Remates: {j.get('remates_totales','no disp.')} | "
                f"A puerta: {j.get('remates_a_puerta','no disp.')}\n"
            )
        return "".join(lineas)

    h2h_texto = ""
    for i, p in enumerate(h2h[:5], 1):
        h2h_texto += f"  {i}. {p.get('texto_raw', '')[:120]}\n"

    prompt = f"""
PARTIDO A ANALIZAR:
  {local} (local) vs {visitante} (visitante)
  Liga: {liga} | Hora: {hora} hora Lima

═══════════════════════════════════════════
MERCADOS DISPONIBLES EN BETSSON (cuota > {config.CUOTA_MINIMA}):
{mercados_texto if mercados_texto else "  Sin mercados disponibles"}

═══════════════════════════════════════════
ESTADÍSTICAS — MUESTRA GENERAL (últimos 10 oficiales):
{_resumir_muestra(stats_local.get('general', []), local, 'general')}
{_resumir_muestra(stats_visitante.get('general', []), visitante, 'general')}

ESTADÍSTICAS — CONDICIÓN (últimos 5 en casa/fuera):
{_resumir_muestra(stats_local.get('condicion', []), local, 'en casa')}
{_resumir_muestra(stats_visitante.get('condicion', []), visitante, 'fuera')}

═══════════════════════════════════════════
H2H — Últimos 5 oficiales entre ambos equipos:
{h2h_texto if h2h_texto else "  Sin datos H2H disponibles"}

═══════════════════════════════════════════
ATACANTES — Estadísticas individuales:
{_resumir_atacantes(atacantes_local, local)}
{_resumir_atacantes(atacantes_visitante, visitante)}

═══════════════════════════════════════════
INSTRUCCIONES:
1. Evalúa cada mercado disponible según las reglas del sistema.
2. Asigna semáforo Verde/Amarillo/Rojo/Gris.
3. No inventes datos que no estén arriba. Si faltan, dilo.
4. En mercados secundarios clasifica como "Basado en tendencia histórica y xG".
5. Devuelve JSON válido con la estructura especificada.
"""
    return prompt


# ─── Utilidades ──────────────────────────────────────────────────────────────

def _resultado_error(partido: dict, mensaje_error: str) -> dict:
    return {
        "partido": f"{partido.get('local','?')} vs {partido.get('visitante','?')}",
        "candidatos": [],
        "descartados": [],
        "no_evaluables": [],
        "advertencias": [f"Error en análisis: {mensaje_error}"],
    }
