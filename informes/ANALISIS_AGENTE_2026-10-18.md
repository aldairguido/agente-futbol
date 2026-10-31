# ANÁLISIS INTEGRAL DEL AGENTE DE FÚTBOL — `D:\agente_futbol`
**Fecha:** 2026-10-18 · **Alcance:** 34 módulos de producto (32.686 líneas) + 11 suites de prueba (5.749 líneas) · **Método:** lectura de código, ejecución de pruebas, consulta de artefactos reales de `informes/` y verificación de la coherencia con `AGENTS.md`.

---

## 1. Qué es, en una frase

Es un **sistema local (no un servicio cloud) que convierte estadísticas reales de fútbol en probabilidades, tickets y láminas PNG, y los entrega por Telegram**; además se autoevalúa, barre cuotas de Betsson buscando +EV y mantiene una capa "cognitiva" narrativa.

No es una demo: hay datos reales, artefactos de 5 días de uso (417 auditorías, 27 pronósticos congelados, 14 informes de evaluación), un bot corriendo ahora mismo y 31 pruebas verdes en el motor principal.

---

## 2. Mapa de capacidades (qué puede hacer)

| Capacidad | Módulo principal | Estado verificado |
|---|---|---|
| Extraer 45 métricas (FT/1T/2T) de FotMob + relleno Flashscore | `fuentes_xg.py` (3.520 l.) + `fuente_flashscore.py` | ✅ funciona · ⚠️ rellena con derivados |
| Auditoría de 15 partidos oficiales por equipo, sin amistosos | `auditoria_historica.py` | ✅ funciona · ⚠️ 29 % de equipos no llega a 15 |
| Enfrentamientos directos 2024–2026 sin inventar duelos | `cuadro_h2h_2024_2026.py`, `auditoria_h2h.py` | ✅ funciona y está probado |
| Modelo de probabilidades (Poisson bayesiano + moduladores 45 métricas) | `motor_mercados.py` | ✅ funciona · 31/31 tests |
| Motor recursivo 2 niveles 45-DCN (tensores, DCN-V2, Jackknife, FDR, MC 100.000) | `motor_recursivo_45_dcn.py` | ✅ nuevo, probado y conectado |
| 30 mercados con sensibilidad por escenarios y "SEGUROS" | `motor_mercados.py` | ✅ funciona |
| Ticket PNG editorial + Cuadro Especialista + Cuadro H2H | `servicio_mercados.py`, `cuadro_especialista.py`, `cuadro_h2h_2024_2026.py` | ✅ renderiza (3 láminas) |
| Entrega Telegram con autorización por chat, acuse y dedup | `bot_interactivo.py`, `servicio_mercados.py` | ✅ funciona · ⚠️ dedup estricto y texto sin formato |
| Barrido de cuotas Betsson + +EV (Quarter-Kelly, EV ≥ 5 %) | `betsson.py`, `agente.py`, `razonador_cuantitativo.py`, `validador_probabilidades.py` | ✅ funciona · ⚠️ usa su PROPIO modelo |
| Evaluación posterior, calibración y Brier | `evaluacion_pronosticos.py` | ✅ funciona y es honesto · ⚠️ muestra insuficiente |
| "Conciencia", madurez, núcleo Antigravity, sub-agentes | `agente_consciente.py`, `nucleo_antigravity.py` | ✅ ejecuta · ⚠️ no decide nada |
| Autodiagnóstico AST + reparación | `nucleo_antigravity.py` | ✅ funciona |

---

## 3. Los flujos reales

1. **`/auditar Equipo A vs Equipo B`** → 30 matrices (15+15) en Markdown + CSV → resumen en chat + documento.
2. **`/auditar A h2h B`** → solo los N duelos reales 2024–2026 (cero inventados).
3. **`/mercados A vs B`** → auditorías → λ → matriz → 30 mercados → **3 PNG** (ticket, cuadro especialista, H2H) con pie corto. El listado de selecciones existe en `analisis.mensaje` pero **no se envía** (la regla del proyecto es no volcar matrices al chat; el efecto es que las probabilidades solo se leen en la imagen).
4. **`/h2h A vs B`** → informe consolidado de duelos directos.
5. **`/evaluar`** → congela pronósticos (snapshot inmutable), verifica resultados, calcula bandas, Brier y calibración; 2 PNG de tablas.
6. **`/analizar_vivo`** → barrido de Betsson, cuotas reales, +EV y tickets Diamante.
7. **`/agente`, `/diagnosticar`, `/conciencia`, `/estado`, `/resultado`** → capa cognitiva y de estado.

---

## 4. Lo que está verificado que funciona

- **Motor de mercados: 31/31 pruebas** (`test_motor_mercados.py`), incluidas matriz normalizada, 1X2, catálogo 30 mercados, sensibilidad, dedup, PNG, cuadros y el motor recursivo.
- **9 suites en verde** (ejecutadas hoy, código de salida 0):

| Suite | Resultado |
|---|---|
| `test_motor_mercados.py` | **31/31 OK** |
| `test_bot_interactivo.py` | OK (12 bloques, mocks de Telegram) |
| `test_evaluacion.py` | **25/25 OK** |
| `test_auditoria_historica.py` | **21/21 OK** (con datos) |
| `test_fuente_flashscore.py` | **11/11 OK** |
| `test_cerebro.py` | OK |
| `test_motor_cuantitativo.py` | OK |
| `test_adversarial_guardrails.py` | OK (NaN/Inf/strings/EV/Kelly) |
| `test_filtro_virtual.py` | OK (excluye eFootball) |

- **2 suites en rojo** (ambas de integración del camino "experto/+EV"):
  - `test_prueba_partido_real.py` → `KeyError: 'goles_anotados_prom'` (el test lee una clave que `analizador_experto` ya no emite).
  - `test_sofascore_torino_udinese.py` → `TypeError: '>=' not supported between 'str' and 'int'`: el LLM devolvió `nivel_confianza` como texto y el código lo compara con 8. **El mismo patrón está en producción** (`agente.py:272`), así que `/analizar_vivo` puede fallar con "❌ Error" cuando Gemini responde.
- **Motor recursivo**: el 1-1 dejó de ser el marcador modal en 7 de 7 enfrentamientos reales probados; `λ_1T + λ_2T = λ_FT`; matriz normalizada; ρ adaptativo positivo en duelos asimétricos.
- **H2H**: prohibición de falsificación probada (Espanyol–Atlético = exactamente 4 duelos reales).
- **Entrega**: acuse antes de scrapear, 3 láminas, dedup por hash y estado en `registro_envios.json`.
- **Seguridad**: solo responde al `TELEGRAM_CHAT_ID` autorizado.
- **Degradación limpia**: sin Gemini/OpenAI funciona igual (fallback determinista) y `/mercados` no usa LLM.
- **La evaluación es honesta**: el propio informe declara `evidencia_suficiente: false` con 24 partidos únicos.

---

## 5. Problemas reales, por prioridad

### P0 — Afectan a la validez de lo que se publica

**1. La consolidación inventa valores y borra la trazabilidad.**
`fuentes_xg.py` rellena métricas ausentes con identidades que no son el dato:
`tiros_libres = faltas_rival + fueras_juego` (:2942), `saques_puerta = remates_fuera_rival − al_palo` (:2932), `pases_entre_lineas = 5 % del tercio final` (:2949), posesión 50/50 (:2914), duelos 50,0 (:3179). Después reescribe la procedencia (`sin_dato` → `derivado`) y vacía `metricas_no_disponibles` (:3235-3249).
La regla 2 del AGENTS.md (`cero None/null/sin_dato`) se cumple **en apariencia**: la auditoría del 18-10 tiene 0 nulos y 11,6 % de ceros, pero también `pases_entre_lineas` con **76,7 % de partidos donde `1T+2T ≠ FT`** (p. ej. Real Madrid: FT 79 vs mitades 3+1). Las auditorías viejas, en cambio, tenían 34 % de nulos reales.

**2. El "15 partidos" no siempre se cumple.**
De 101 equipos auditados: 74 con 15 partidos, **27 con 7–13**, y **29 %** declara `objetivo_alcanzado: false` ("la fuente no ofrece más páginas de calendario"). Los mercados se calculan igual con esa muestra corta; el motor recursivo además exige ≥10 para mezclar EWMA, así que en 1 de cada 3 equipos esa parte no entra.

**3. Hay dos motores de probabilidad distintos y no hablan entre sí.**
`/mercados` usa `motor_mercados` (+45 métricas, bayesiano, recursivo). `/analizar_vivo` y los tickets +EV usan `razonador_cuantitativo._motor_cuantitativo_matematico` (:305-321) con coeficientes fijos (local ×1,08 / visita ×0,92, xG 70/30, H2H 70/30). **El mismo partido puede tener dos probabilidades distintas**, y los tickets Diamante no usan ni las 45 métricas ni el motor recursivo.

**4. El "83,8 % de acierto / 2.040 ganadas" es un artefacto estadístico.**
`memoria_vivencial.json` y la identidad del agente lo presentan como calibración demostrada. La realidad (verificada en `informes/evaluacion/evaluacion_9393a7b9017a5365.json`):
- 2.435 selecciones evaluables proceden de **24 partidos únicos** (101,5 selecciones por partido, todas correlacionadas por la misma matriz).
- **62,9 %** de esas selecciones tienen p ≥ 0,90 (líneas triviales tipo "menos de 6,5 goles"), que aciertan por construcción.
- Brier 0,1302 frente a **0,1359 de la climatología**: la mejora es de 0,006, prácticamente nada.
- La "top-1 por partido" acierta 24/24 → confirma que el agregado mide lo trivial, no habilidad.
- El propio módulo lo dice ("evidencia insuficiente, mínimo 30 partidos"), pero `agente_consciente.py:644-668` convierte esos números en "seguro y calibrado (+EV validado)".

**5. El camino +EV tipa mal la respuesta del LLM y puede romper `/analizar_vivo`.**
`razonador_cuantitativo.py:157` hace `json.loads` de lo que devuelve Gemini sin coercionar tipos, y `agente.py:272` compara `decision_quant.get("nivel_confianza", 0) >= 8`. Si el modelo responde `"8"` (texto), salta `TypeError` y el hilo del bot responde "❌ Error en el barrido" en lugar de tickets. Está reproducido por `test_sofascore_torino_udinese.py:126`.

### P1 — Afectan a la experiencia y a la operación

**6. Todos los mensajes se envían en texto plano aunque están escritos en Markdown.** `enviar_seguro(..., markdown=False)` por defecto y `enviar_fn_telegram(parse_mode=None)` → los `*asteriscos*` y `_guiones_` se ven literales en Telegram. Es deliberado (evita el 400) pero el texto nunca se adaptó.

**7. El dedup no se puede reiniciar y confunde reenvío con fallo.** `servicio:1271` bloquea si `envio_confirmado`, y el bot **nunca** pasa `nueva_ejecucion=True` (`bot:1521-1524`, `1674-1676`) → pedir el mismo partido otra vez se reporta como "fallo de envío".

**8. Tres comandos no parten el mensaje** (>4.096 caracteres): `/analizar_vivo` (`bot:687`), `/conciencia` (`:371`) y el resumen de `/evaluar`. `partir_mensaje` existe pero solo se usa en el fallback de `/mercados`.

**9. No hay higiene de artefactos.** `informes/` = **144,4 MB** (417 auditorías, 259 JSON, 68 PNG); `registro_envios.json` guarda el mensaje completo de 56 trabajos. No existe ningún `os.remove`/limpieza ni rotación.

**10. Concurrencia y backoff.** Dos locks distintos permiten `/mercados` y `/analizar_vivo` a la vez escribiendo `registro_envios.json` con read-modify-write sin lock (`servicio:371-378`). Y `reintentos` nunca se resetea: tras 15 fallos el backoff queda fijo en 30 s (`bot:1765-1783`).

**11. Credenciales y control de versiones.** `.env` en claro (OpenAI, Gemini, Betsson email/password, token de Telegram) y **no hay `.gitignore` ni repositorio**: si algún día se versiona, se filtran las claves.

### P2 — Deuda y fragilidad

12. **Capas decorativas**: el Índice de Confianza Cognitiva se calcula *después* del análisis y solo se anexa a un `.md` que `/mercados` no envía (`servicio:1156-1174`); `agente_consciente` y `validador_probabilidades` no se importan en `motor_mercados` (0 referencias). `/conciencia` y `/agente` no alteran ninguna probabilidad ni stake.
13. **Frágil por diseño**: selectores y `x-fsign` de Flashscore hardcodeados (`fuente_flashscore.py:38-43`), esperas fijas de 2–2,5 s, `playwright-stealth`, 2 reintentos sin backoff (`fuentes_xg.py:1636-1648`), `except Exception` que confunde "fallo de red" con "sin dato".
14. **Duplicación**: `flashscore.py` (legacy, Playwright) convive con `fuente_flashscore.py` (API ninja); `sofascore.py` hace de scraper y de adaptador. `test_prueba_partido_real.py` está desactualizado respecto al esquema de `analizador_experto`.
15. **Ambigüedad de catálogo**: 71 claves en `CATALOGO_METRICAS` para 45 canónicas (26 alias) y `pases_ratio` se guarda como texto compuesto.
16. **Comandos sin documentar** en `/ayuda`: `/status`, `/scan`, `/duelo`, `/historial`, `/memoria`, `/madurez`, `/ia`, `/pensar`.

---

## 6. Coherencia con `AGENTS.md`

| Regla | Estado | Evidencia |
|---|---|---|
| 1. Fusión FotMob + Flashscore, cero huecos | ⚠️ Parcial | Fusiona, pero rellena con derivados (`fuentes_xg.py:2932-2954`) |
| 2. Tolerancia cero a null/None/NaN | ❌ En apariencia | 0 nulos en 18-10, **34 % en auditorías del 13-10**; se logra imputando |
| 3. `/auditar` siempre 15 + 15 oficiales | ❌ Parcial | 29 % de equipos con 7–13 partidos (`resumen.objetivo_alcanzado=false`) |
| 4. Catálogo de 45 métricas | ✅ | 45 canónicas presentes (+26 alias) |
| 5. Informe con 6 subtablas y tabla resumen | ✅ | `/auditar` genera MD con esa estructura |
| 6. Resolución de equipos anti-homonimia | ✅ | `prueba_fecha_por_id_y_declaracion_sin_calendario` |
| 7. Resiliencia del bot (normalización) | ⚠️ Parcial | Normaliza bien; 3 comandos sin partición |
| 8. Estética del ticket PNG | ✅ | Paleta clara, tabla de 3 columnas, sin emojis rotos |
| 9. Protocolo de verificación | ⚠️ Parcial | 31/31 + 8 suites verdes, pero **2 suites de integración en rojo** (`test_prueba_partido_real`, `test_sofascore_torino_udinese`) |

---

## 7. ¿Tiene sentido? (juicio)

**Como ingeniería: sí.** Es un sistema real, con datos reales, salidas verificables (PNG, MD, JSON), entrega por Telegram, evaluación posterior y 31 pruebas verdes. La arquitectura está bien separada (fuente → auditoría → modelo → render → entrega) y sabe degradar (sin LLM, sin red de IA, sin matplotlib).

**Como predictor: no está demostrado.** La evidencia disponible es de 24 partidos, con selecciones correlacionadas y un Brier indistinguible de la climatología. El sistema publica "SEGUROS >70 %" que en su mayoría son líneas triviales (menos de 6,5 goles, más de 0,5 goles). Eso no es un error de código: es un problema de **diseño de la evidencia** (contar 100 selecciones por partido como si fueran 100 ensayos independientes).

**El motor recursivo 45-DCN mejora la coherencia interna** (elimina el 1-1 fijo, respeta `1T+2T=FT`, declara sus límites y su muestra), pero **hereda los datos imputados**: `pases_entre_lineas` inventado alimenta directamente el patrón "Puñalada Vertical".

**La capa "conciencia" es narrativa**: no cambia probabilidades, umbrales ni stakes. Es un resumen de estado, no un mecanismo de decisión. Su identidad ("2.040 aciertos verificados") contradice el propio informe de evaluación.

---

## 8. Plan de corrección (orden recomendado)

**Quick wins (1–2 días)**
1. Separar hecho de relleno: guardar `procedencia` por celda, no vaciar `metricas_no_disponibles`, y que el motor ignore las métricas derivadas (tiros_libres, saques_puerta, pases_entre_lineas en mitades).
2. Coercionar la salida del LLM en `razonador_cuantitativo` (`int(nivel_confianza)`, `float(probabilidad)`), arreglar `agente.py:272` y poner al día `test_prueba_partido_real.py`.
3. `parse_mode`: enviar en HTML con escape (o limpiar el Markdown del texto).
4. Reenvío: pasar `nueva_ejecucion=True` cuando el usuario repita el comando, y distinguir "ya enviado" de "fallo".
5. Partir mensajes largos en `/analizar_vivo`, `/conciencia` y `/evaluar`.
6. `.gitignore` + rotación de `informes/` (por antigüedad y tamaño).

**Estructural (1–2 semanas)**
7. **Un solo motor**: `/analizar_vivo` debe calcular λ y matriz con `motor_mercados` (y su motor recursivo) y usar `razonador_cuantitativo` solo para EV/Kelly.
8. **Evidencia real**: subir el mínimo a 100–200 partidos, contar **una** selección por partido/familia (o ponderar por partido), y publicar en `/estado` el Brier frente a la climatología con su n.
9. Alinear la identidad de `agente_consciente` con `evidencia_suficiente` (si el informe dice insuficiente, el estado no puede decir "calibrado").
10. Cerrar el acceso a datos: único punto de lectura de métricas con validación de coherencia `1T+2T=FT` **antes** de modelar, y fallo explícito (no imputación silenciosa).

---

*Documento generado a partir de la auditoría del código, la ejecución de las suites y el análisis de los artefactos de `informes/`. No se modificó ningún archivo del proyecto durante el análisis.*
