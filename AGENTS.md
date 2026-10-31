# DIRECTIVA MAESTRA Y ARQUITECTURA HÍBRIDA DEL AGENTE (FOTMOB + FLASHSCORE + MLP ENSEMBLE)

> **IMPORTANTE — REGLA PERMANENTE DEL PROYECTO (`D:\agente_futbol`):**
> Estas reglas son **innegociables, obligatorias y permanentes**. Ningún cambio futuro puede relajar, omitir ni desactivar ninguna de las condiciones descritas en este documento. Aplica de forma estricta y automática en todas las ejecuciones, auditorías, tests y comandos del bot (`/auditar`, `/analizar_vivo`, `/h2h`, `/mercados`, `/evaluar`, `/conciencia`, `/estado`). Está terminantemente prohibido dar por finalizada una tarea, entregar una matriz con huecos o emitir una predicción si no se cumple el 100% de esta especificación.

---

## 1. FUSIÓN AUTOMÁTICA E INMEDIATA: FOTMOB + FLASHSCORE (CERO HUECOS EN LA MATRIZ)

1. **Trabajo conjunto obligatorio:**
   - FotMob es la fuente primaria, pero carece de cobertura completa en ciertas ligas, partidos o desgloses por mitad (`1T`, `2T`). En el instante en que una métrica, periodo o partido no exista o venga incompleto en FotMob, Flashscore debe activarse automáticamente para cubrir cada hueco faltante.
2. **Prohibido dejar Flashscore: N/D cuando faltan datos:**
   - Si en un partido FotMob devuelve `0 vs 0` por omisión en métricas como `23. pases_entre_lineas`, `25. tiros_libres` o `45. saques_puerta`, debes buscar obligatoriamente el partido en Flashscore por fecha y nombres de equipos (con normalización de alias y coherencia de país/liga) para rellenar los valores reales antes de construir la matriz.
3. **Jerarquía de relleno por campo y por tiempo (FT, 1T, 2T):**
   - **Fuente Primaria (FotMob):** Extrae todas las métricas disponibles de las 45 oficiales.
   - **Fuente Secundaria Inmediata (Flashscore):** Rellena todos los campos vacíos en FT (Tiempo Completo), 1T (Primer Tiempo) y 2T (Segundo Tiempo) sin sobrescribir los datos válidos ya obtenidos de FotMob.
4. **Reconstrucción y Coherencia Aritmética:**
   - Si una métrica existe en dos de los tres periodos (`FT`, `1T`, `2T`), calcula el periodo restante de inmediato (`FT = 1T + 2T`, `1T = FT - 2T`, `2T = FT - 1T`).
   - Si `saques_puerta` sigue ausente tras consultar ambas fuentes, estímala de forma conservadora a partir de `remates_fuera` del rival ($\max(1, \text{remates\_fuera}_{\text{rival}} - \text{al\_palo}_{\text{rival}})$) y regístralo en la columna de fuente como `Est.`.
   - En las métricas `26. pases_largos_pct`, `27. pases_tercio_final_pct` y `28. cruces_pct`, formatea siempre `completados/totales (pct%)` cuando existan ambos valores.

---

## 2. TOLERANCIA CERO A NULL, NONE, NAN, "SIN_DATO" O 0 VS 0 FALSOS

1. **Prohibición absoluta de huecos:**
   - Ninguna matriz entregada al usuario ni pasada a `motor_mercados.py` puede contener `null`, `None`, `NaN`, cadenas vacías `""` o `"sin_dato"`.
2. **Resolución real (NUNCA inventada):**
   - Rellenar huecos significa extraer el dato real desde Flashscore cuando falta en FotMob (o viceversa) y aplicar identidades matemáticas exactas.
   - **JAMÁS** significa inventar partidos, simular marcadores ni rellenar con números aleatorios, promedios ficticios o sintéticos.
3. **Test de bloqueo (Gatekeeper):**
   - `test_fuente_flashscore.py` y `test_auditoria_historica.py` deben lanzar `AssertionError` si aparece un solo `None`, `null` o `"sin_dato"` en cualquiera de las 45 métricas de los partidos auditados.

---

## 3. COMANDOS DE INFORME DE MATRIZ BAJO DEMANDA (`/auditar`)

El informe detallado de matrices solo se genera cuando el usuario lo solicita explícitamente mediante `/auditar`:

### Modalidad A (`/auditar <Equipo Local> vs <Equipo Visitante>`):
- Entrega exactamente 15 partidos 100% oficiales del Equipo Local y 15 partidos 100% oficiales del Equipo Visitante (30 matrices completas en total: 15 + 15).
- **Exclusión estricta de amistosos:** Prohibido mezclar amistosos (nunca entregues 10 oficiales + 5 amistosos ni te detengas en 13 o 14 partidos). Si aparecen amistosos de clubes o pretemporada, descártalos y sigue buscando hacia atrás en el calendario hasta reunir 15 partidos oficiales verificados por equipo, informando cuántos amistosos se descartaron.

### Modalidad B (`/auditar <Equipo Local> h2h <Equipo Visitante>`):
- Entrega exactamente $N$ matrices correspondientes a los $N$ enfrentamientos reales del H2H en la ventana 2024–2026.
- **Prohibición absoluta de inventar partidos H2H:** Jamás fabriques fechas sintéticas ni rellenes con partidos jugados contra otros rivales.

---

## 4. CATÁLOGO OFICIAL E INNEGOCIABLE DE LAS 45 MÉTRICAS (FT, 1T Y 2T)

Cada matriz debe incluir las 45 métricas exactas agrupadas en sus 6 bloques oficiales:

1. **Estadísticas Principales (01–12):**
   - 01. `xg`
   - 02. `posesion_pct`
   - 03. `remates_totales`
   - 04. `remates_puerta`
   - 05. `grandes_ocasiones`
   - 06. `grandes_ocasiones_falladas`
   - 07. `corners`
   - 08. `pases_total`
   - 09. `pases_precision_pct`
   - 10. `pases_ratio`
   - 11. `tarjetas_amarillas`
   - 12. `tarjetas_rojas`
2. **Disparos (13–21):**
   - 13. `xgot`
   - 14. `xg_juego_abierto`
   - 15. `xg_balon_parado`
   - 16. `xg_sin_penalti`
   - 17. `remates_fuera`
   - 18. `remates_rechazados`
   - 19. `remates_dentro_area`
   - 20. `remates_fuera_area`
   - 21. `al_palo`
3. **Ataques (22–25):**
   - 22. `toques_area_rival`
   - 23. `pases_entre_lineas`
   - 24. `fueras_juego`
   - 25. `tiros_libres`
4. **Pases (26–30):**
   - 26. `pases_largos_pct`
   - 27. `pases_tercio_final_pct`
   - 28. `cruces_pct`
   - 29. `xa_equipo`
   - 30. `saques_banda`
5. **Defensa (31–41):**
   - 31. `faltas`
   - 32. `entradas_pct`
   - 33. `duelos_ganados`
   - 34. `duelos_suelo_pct`
   - 35. `duelos_aereos_pct`
   - 36. `despejes`
   - 37. `intercepciones`
   - 38. `bloqueos`
   - 39. `recuperaciones`
   - 40. `errores_remate`
   - 41. `errores_gol`
6. **Portería / Atajadas (42–45):**
   - 42. `paradas`
   - 43. `xgot_enfrentado`
   - 44. `goles_evitados`
   - 45. `saques_puerta`

---

## 5. FORMATO VISUAL DE TABLAS MARKDOWN Y APERTURA NATIVA EN TELEGRAM (/auditar)

1. **Tablas Markdown limpias:** Prohibido usar bloques `====` o tablas sin barras laterales `| ... |`. Divide cada matriz en sus 6 sub-tablas por categoría con columnas explícitas por equipo y periodo:
   `| # | Métrica Oficial (Clave) | FT: Local | FT: Visita | 1T: Local | 1T: Visita | 2T: Local | 2T: Visita | Fuente |`
2. **Apertura directa en Telegram Android (División en 2 tomos si > 30 KB):**
   - Si el archivo `.md` supera los 30 KB (como el reporte de 30 matrices), envía primero un resumen ejecutivo en el chat y ofrece o divide el envío en 2 tomos ligeros (< 30 KB cada uno):
     * **Tomo 1:** 15 matrices del Local.
     * **Tomo 2:** 15 matrices del Visitante.
   - De esta forma abren directamente dentro del visor interno nativo de Telegram sin requerir aplicaciones externas ni congelar el cliente.

---

## 6. ESTÉTICA EDITORIAL CLARA OBLIGATORIA PARA EL TICKET PNG (`motor_mercados.py` / `servicio_mercados.py`)

El renderizado PNG del Ticket Cuantitativo de Alta Probabilidad (`renderizar_ticket_png` / `/mercados`) debe aplicar siempre:
1. **Fondo general claro:** Gris perla `#F7F9FC` con tarjetas blancas `#FFFFFF` y bordes `#DCE4F0` / `#D1DBE6`.
2. **Encabezado:** Azul marino `#131D31` con barra superior cobalto `#1A6BA8`.
3. **Tabla estructurada de 3 columnas (CATEGORÍA | ÁMBITO / PERIODO | LÍNEAS Y PROBABILIDADES VALIDADAS):**
   - **Sección 1 ($\ge 70\%$):** Píldoras en verde esmeralda suave (`#ECFAF2`, borde `#A5DEBE` / `#A3E4C1`, texto `#136C40` / `#0F6841`).
   - **Sección 2 ($58\%–69\%$ / Sensibles):** Píldoras en ámbar suave (`#FFF9EB`, borde `#F2C98A` / `#F6D58E`, texto `#9A5406` / `#945305`).
   - **Cero glifos Unicode rotos (`▯`):** Sustituir emojis por etiquetas tipográficas institucionales limpias.

---

## 7. RESILIENCIA DEL BOT Y NORMALIZACIÓN DE COMANDOS (`bot_interactivo.py`)

Normaliza todo comando entrante con:
```python
re.sub(r"^/\s+", "/", texto.strip()).split()[0].lower()
```
para que `/Conciencia`, `/ conciencia`, `/Auditar`, `/Mercados`, etc., respondan siempre al instante.

---

## 8. ARQUITECTURA HÍBRIDA DE PREDICCIÓN: PREPROCESAMIENTO [0, 1], RED NEURONAL DE 5 FACTORES LATENTES, MULTI-FRECUENCIA, ENSEMBLE Y CALIBRACIÓN 80/20 (`motor_mercados.py` + `evaluacion.py`)

Esta arquitectura integra las investigaciones científicas (Sportmonks Neural Networks, Batista/Martins et al., Praveen et al.):

### Fase 1: Preprocesamiento Limpio y Normalización Acotada [0, 1] (NumPy / SciPy)
- **Normalización Min-Max / Sigmoid Scaling:** Escalar cada una de las 45 métricas oficiales al rango $[0.0, 1.0]$ usando cotas teóricas fijas por variable:
  $$x_{\text{norm}} = \text{clip}\left(\frac{x - \text{min\_teorico}}{\text{max\_teorico} - \text{min\_teorico}}, 0.0, 1.0\right)$$
- **Prohibición de fuga de datos (Data Leakage = 0):** Para predecir cualquier partido en fecha $T$, las entradas deben construirse exclusivamente con los 15 partidos 100% oficiales previos ($t_i < T$, cero amistosos) de cada equipo. Jamás usar estadísticas de partidos futuros.

### Fase 2: Multi-Frecuencia Temporal (Largo Plazo + Corto Plazo + Nowcasting en Vivo)
- **Frecuencia Estructural (15 partidos oficiales previos):** Captura la capacidad base del equipo en FT, 1T y 2T (peso **55%**).
- **Frecuencia de Inercia Reciente (Últimos 5 partidos oficiales con EWMA $\alpha=0.35$):** Captura racha de definición, fatiga o declive defensivo agudo (peso **30%**).
- **Frecuencia de Choque Directo / Tiempo Real (H2H 15% en pre-partido, o Nowcasting en vivo):** Cuando se ejecuta `/analizar_vivo`, el modelo inyecta el ritmo real del partido en curso (minuto actual, posesión, xG, xGOT, toques de área, tarjetas rojas y marcador) para recalcular dinámicamente $\lambda_{\text{restante}}$.

### Fase 3: Reducción de Dimensión a 5 Nodos Ocultos (Factores Latentes / Indicadores Adelantados)
La Capa Oculta Estructurada sintetiza las 45 métricas normalizadas en 5 Factores Latentes No Lineales (activación $\tanh$):
1. **$F_1$ — Posesión Efectiva y Progresión Territorial (`F_posesion_progresiva`):**
   - Inputs: 02. `posesion_pct`, 08. `pases_total`, 09. `pases_precision_pct`, 10. `pases_ratio`, 23. `pases_entre_lineas`, 27. `pases_tercio_final_pct`, 22. `toques_area_rival`.
   - Distingue posesión estéril (muchos pases sin pisar área) de posesión dañina (alta penetración y toques en área rival).
2. **$F_2$ — Volumen de Creación Ofensiva (`F_creacion`):**
   - Inputs: 01. `xg`, 14. `xg_juego_abierto`, 15. `xg_balon_parado`, 16. `xg_sin_penalti`, 29. `xa_equipo`, 05. `grandes_ocasiones`, 07. `corners`, 25. `tiros_libres`.
3. **$F_3$ — Calidad Real de Finalización / Letalidad (`F_finalizacion`):**
   - Inputs: 13. `xgot`, ratio `xgot / max(xg, 0.1)`, 04. `remates_puerta`, 19. `remates_dentro_area`, 06. `grandes_ocasiones_falladas` (peso negativo), 21. `al_palo`.
   - Detecta si los delanteros están colocando los tiros a las esquinas ($xGOT > xG$) o desperdiciando ocasiones claras.
4. **$F_4$ — Vulnerabilidad de la Portería Rival (`F_porteria_rival`):**
   - Inputs del rival: 43. `xgot_enfrentado`, 44. `goles_evitados` (negativo amplifica), 42. `paradas`.
   - Si el arquero rival tiene `goles_evitados < 0` (ej. -0.21/partido), amplifica directamente el $\lambda$ del atacante.
5. **$F_5$ — Fragilidad Defensiva y Regalos del Rival (`F_fragilidad_rival`):**
   - Inputs del rival: 41. `errores_gol`, 40. `errores_remate`, 31. `faltas`, 11. `tarjetas_amarillas`, 12. `tarjetas_rojas`, 34. `duelos_suelo_pct`, 35. `duelos_aereos_pct`, 38. `bloqueos`, 39. `recuperaciones`.
   - Penaliza defensas que regalan goles o remates por errores propios y eleva la probabilidad de Ambos Anotan (BTTS Sí).

### Fase 4: Ensemble Híbrido Lineal + No Lineal y Validación Cronológica 80% / 20%
- **Combinación Lineal + No Lineal:**
  $$\lambda_{\text{efectivo}} = \lambda_{\text{base}} \cdot \exp(\alpha \cdot \Delta_{\text{MLP}})$$
  donde $\Delta_{\text{MLP}} = \sum_{k=1}^5 W_k F_k + b$.
- **Entrenamiento y Validación Temporal 80% / 20% (en `evaluacion.py`):**
  - Ordenar partidos en orden estrictamente cronológico (cero shuffle).
  - 80% inicial: optimizar pesos con `scipy.optimize.minimize(..., method="L-BFGS-B")` minimizando Log-Loss + penalización L2 (Ridge).
  - 20% más reciente: medir Brier Score y Log-Loss frente a $\lambda_{\text{base}}$.
  - Si en el 20% de validación el Ensemble no supera a $\lambda_{\text{base}}$, contraer $\alpha$ automáticamente hacia $0.15–0.25$ (Shrinkage).

---

## 9. IA EXPLICABLE (XAI) Y CLUSTERS DE MARCADORES DOMINANTES

1. **Desglose XAI en el Reporte:**
   En cada predicción de `/mercados`, `/conciencia` o `/analizar_vivo`, incluir la subsección:
   ```
   🧠 Desglose de Factores de la Red Neuronal (XAI):
   • {Local}: λ {base} → {efectivo} ({total_pct:+.1f}%)
     - Posesión Progresiva: {pct1:+.1f}%
     - Creación Ofensiva: {pct2:+.1f}%
     - Letalidad / Finalización: {pct3:+.1f}%
     - Vulnerabilidad Portería Rival: {pct4:+.1f}%
     - Fragilidad Defensiva Rival: {pct5:+.1f}%
   • {Visita}: λ {base_v} → {efectivo_v} ({total_pct_v:+.1f}%)
     ...
   ```
2. **Clusters de Marcadores Dominantes:**
   - Reportar siempre el Cluster Dominante (agrupando los 3 marcadores contiguos de mayor probabilidad conjunta) en el texto y pie de imagen.
   - Filtrar las recomendaciones principales por probabilidad acumulada $\ge 70\%$.

---

## 10. PROTOCOLO DE VERIFICACIÓN ANTES DE RESPONDER

1. Separación de comandos en el bot (resúmenes en `/mercados` y matrices crudas solo en `/auditar`).
2. `/auditar Local vs Visitante` entrega las 30 matrices oficiales (15 + 15, cero amistosos, tomos < 30 KB).
3. `/auditar Local h2h Visitante` entrega exactamente las $N$ matrices reales del H2H 2024–2026.
4. Cero partidos inventados en H2H (ej. Espanyol vs Atlético de Madrid tiene exactamente 4 partidos reales en 2024–2026).
5. Las 45 métricas presentes en FT, 1T, 2T con coherencia aritmética `1T + 2T == FT` y cero nulos.
6. Ticket PNG renderizado con estética editorial clara (`#F7F9FC`), tabla de 3 columnas con píldoras ordenadas y cero emojis rotos.
7. Bot resiliente ante variaciones móviles (`/Conciencia`, `/ conciencia`) y demonio inmortal con reconexión activa.
8. Arquitectura Híbrida 5 Factores Latentes, Multi-Frecuencia, Ensemble y XAI operativa.
9. Validación cronológica 80/20 verificada en `evaluacion.py` con Brier Score Base vs Ensemble.

---

## 11. DIRECTIVA MAESTRA Y ARQUITECTURA HÍBRIDA DEL AGENTE (AUDITORÍA DE 8 FASES + MÓDULO DOBLE ADN LOCAL/VISITA)

> **IMPORTANTE — REGLA PERMANENTE DEL PROYECTO (`D:\agente_futbol`):**
> Estas reglas son **innegociables, obligatorias y permanentes**. Ver detalle íntegro en `D:\agente_futbol\AGENTS.md` y `D:\agente_futbol\CLAUDE.md`.
> Aplica de forma 100% general y dinámica para CUALQUIER equipo y liga (cero hardcoding) en las 8 Fases de la arquitectura (`fuentes_xg.py`, `auditoria_historica.py`, `motor_recursivo_45_dcn.py`, `motor_mercados.py`, `evaluacion.py`, `cuadro_especialista.py` y `bot_interactivo.py`).

---

## 1. CORRECCIONES A LA FASE 1: INGESTA 15 VS 15, CONDICIÓN Y RECONSTRUCCIÓN DE xG/xGOT

1. **Garantía Real de 15 vs 15 Partidos Oficiales (Cero 15 vs 11):**
   - Aunque la Fase 1.1 realiza backfill hasta completar 15 partidos, queda prohibido emitir muestras asimétricas incompletas (como 15 vs 11).
   - El motor bloquea la ejecución si `len(partidos_local) < 15` o `len(partidos_visita) < 15`, buscando en temporadas previas o en Flashscore hasta completar exactamente 15 partidos oficiales por club.
2. **Separación Estricta de Condición (`condicion = 'casa'` vs `condicion = 'fuera'`) y `tier_competicion`:**
   - Cada uno de los 15 partidos del tensor $X \in \mathbb{R}^{15 \times 3 \times 45}$ almacena obligatoriamente sus metadatos `condicion` (`'casa'` o `'fuera'`) y `tier_competicion` (Tier 1, 2, 3 o 4) antes de alimentar la red neuronal.
3. **Prohibición de Promediar xG = 0.00 o xGOT = 0.00 cuando Sí Hubo Remates:**
   - En ligas sin tracking de coordenadas avanzadas (Serie C, Primera Federación, Copas), donde `remates_totales > 0` pero `xg == 0` (o `xgot == 0`), se reconstruyen obligatoriamente en `fuentes_xg.py`:
     $$\text{xG} = \text{round}(0.11 \cdot \text{remates\_dentro\_area} + 0.035 \cdot \text{remates\_fuera\_area} + 0.28 \cdot \text{grandes\_ocasiones}, 2)$$
     (o $\text{round}(0.09 \cdot \text{remates\_totales} + 0.14 \cdot \text{remates\_puerta}, 2)$ si no hay desglose de área).
     $$\text{xGOT} = \text{round}(0.29 \cdot \text{remates\_puerta} + 0.12 \cdot \text{goles\_favor}, 2)$$
     $$\text{xGOT\_enfrentado} = \text{round}(0.29 \cdot \text{remates\_puerta\_rival} + 0.12 \cdot \text{goles\_contra}, 2)$$
     $$\text{goles\_evitados} = \text{round}(\text{xGOT\_enfrentado} - \text{goles\_contra}, 2)$$
4. **Higiene de Unidades y Derivación Física:**
   - Prohibido mezclar conteos absolutos con porcentajes en series temporales.
   - Derivación física de `saques_puerta`: $\max(1, \text{remates\_fuera}_{\text{rival}} - \text{al\_palo}_{\text{rival}})$ cuando venga en 0.

---

## 2. CORRECCIONES A LA FASE 2 Y 3: DESCUENTO RELATIVO DE DIVISIÓN Y TECHO DE SEQUÍA

1. **Descuento Relativo por Nivel de Liga (Tier Relativo):**
   - Cada partido histórico tiene asignado su nivel de competición:
     * **Tier 1:** Primeras divisiones de élite y torneos continentales (Premier League, LaLiga, Serie A, Champions, Europa League).
     * **Tier 2:** Segundas divisiones (Championship, LaLiga 2, Serie B, 2. Bundesliga).
     * **Tier 3:** Terceras divisiones (Primera Federación / 1ª RFEF, Serie C, 3. Liga, League One).
     * **Tier 4:** Cuartas divisiones o inferiores (Segunda Federación, League Two).
   - Ajuste relativo al rival actual ($\Delta\text{Tier} = \text{Tier}_{\text{partido}} - \text{Tier}_{\text{rival\_actual}}$):
     * Si un partido histórico proviene de una división inferior a la del rival actual ($\Delta\text{Tier} \ge 1$):
       - Multiplicar métricas ofensivas por $0.76^{\Delta\text{Tier}}$.
       - Multiplicar métricas defensivas concedidas (`goles_contra`, `xg_contra`, `xgot_enfrentado`, `remates_puerta_rival`) por $1.22^{\Delta\text{Tier}}$.
     * Si $\Delta\text{Tier} \le 0$, el factor es $1.00$.
2. **Corrección del Techo de Sequía Severa:**
   - Prohibido aplicar el techo $\lambda \in [0.85, 0.95]$ a un equipo de Tier 1 cuando enfrenta a un rival recién ascendido o cuya muestra provenga en $\ge 30\%$ de divisiones inferiores (Tier 2/3).

---

## 3. MÓDULO DE CONDICIONAMIENTO DE CONTEXTO ("DOBLE ADN": LOCAL EN CASA VS. VISITANTE FUERA)

1. **Aislamiento de Contexto con Contracción Bayesiana ($k = 3.5$):**
   - Toda métrica condicionada $m \in \{1..45\}$ del Local en Casa ($N_L \approx 7\text{–}8$) y del Visitante Fuera ($N_V \approx 7\text{–}8$) se contrae hacia el estado LSTM $h_{15}$ para erradicar sobreajuste por muestra pequeña:
     $$w_{\text{cond}} = \frac{N_{\text{cond}}}{N_{\text{cond}} + 3.5} \quad (w_{\text{cond}} \approx 0.67 \text{ a } 0.70)$$
     $$\mu_{L, \text{Casa}}(m) = w_{\text{cond}, L} \cdot \bar{x}_{L, \text{casa}}(m) + (1 - w_{\text{cond}, L}) \cdot h_{L, 15}(m)$$
     $$\mu_{V, \text{Fuera}}(m) = w_{\text{cond}, V} \cdot \bar{x}_{V, \text{fuera}}(m) + (1 - w_{\text{cond}, V}) \cdot h_{V, 15}(m)$$
2. **Nueva Fórmula de $\lambda_{\text{Base}}$ Condicionada por Localía (División Exacta entre 2.0):**
   - Cruce directo entre el ADN de Casa del Local y el ADN de Fuera del Visitante:
     $$\text{Atq}_{L, \text{Casa}} = 0.50 \cdot \text{Goles}_{\text{fav}, L(\text{Casa})} + 0.50 \cdot \text{xG}_{\text{fav}, L(\text{Casa})}$$
     $$\text{Def}_{V, \text{Fuera}} = 0.50 \cdot \text{Goles}_{\text{conc}, V(\text{Fuera})} + 0.50 \cdot \text{xGOT}_{\text{conc}, V(\text{Fuera})}$$
     $$\text{Atq}_{V, \text{Fuera}} = 0.50 \cdot \text{Goles}_{\text{fav}, V(\text{Fuera})} + 0.50 \cdot \text{xG}_{\text{fav}, V(\text{Fuera})}$$
     $$\text{Def}_{L, \text{Casa}} = 0.50 \cdot \text{Goles}_{\text{conc}, L(\text{Casa})} + 0.50 \cdot \text{xGOT}_{\text{conc}, L(\text{Casa})}$$
     $$\lambda_{\text{Base}, L} = \frac{\text{Atq}_{L, \text{Casa}} + \text{Def}_{V, \text{Fuera}}}{2.0}, \qquad \lambda_{\text{Base}, V} = \frac{\text{Atq}_{V, \text{Fuera}} + \text{Def}_{L, \text{Casa}}}{2.0}$$
   - Obligatorio dividir entre 2.0 (o multiplicar cada término por 0.25) para mantener la escala real de goles esperados.

---

## 4. LOS 6 CRUCES TÁCTICOS ENTRE LAS 45 VARIABLES ($\mu_{L, \text{Casa}} \otimes \mu_{V, \text{Fuera}}$)

1. **Cruce #1 — Asfixia vs. Espacio en Mediocampo (F1 — Posesión Progresiva):**
   - Cruce: `posesion_pct` + `pases_tercio_final_pct` + `pases_entre_lineas` (Local en Casa) $\otimes$ `intercepciones` + `recuperaciones` + `entradas_pct` (Rival de Visita).
   - Corrección de escala: $(\text{Pases3roFinal\%} - 68.0) / 10.0$ (prohibido restar 22).
   - Patrón XAI: Si Local tiene posesión $\ge 55\%$ en casa y el Visitante promedia $\ge 11$ intercepciones y $\ge 48$ recuperaciones fuera, activa alerta **"Atasco en Mediocampo"**.
2. **Cruce #2 — Creación vs. Muro o Fragilidad del Portero Rival (F2 vs. F4):**
   - Cruce: `xg` + `toques_area_rival` + `grandes_ocasiones` (Local en Casa) $\otimes$ `goles_evitados` + `paradas` + `xgot_enfrentado` (Rival de Visita).
   - Ajuste bidireccional acotado en $[-0.35, +0.45]$:
     $$\text{Ajuste}_{\text{port}, L} = \text{clip}\left(-\text{GolesEvitados}_{V(\text{Fuera})} \cdot \frac{\text{RemPuerta}_{L(\text{Casa})}}{5.0}, -0.35, +0.45\right)$$
   - Patrón XAI: Si `goles_evitados` $> +0.15$ fuera activa **"Muro del Portero Rival"** (reduce $\lambda_L$); si $< -0.15$ activa **"Fragilidad en Portería Rival"** (eleva $\lambda_L$).
3. **Cruce #3 — Puntería Clínica vs. Permisividad y Bloqueos (F3 — Finalización xGOT):**
   - Cruce: Tiros a puerta por gol ($\text{remates\_puerta} / \max(1, \text{goles})$) + `xgot/xg` + `grandes_ocasiones_falladas` $\otimes$ `bloqueos` + `remates_rechazados`.
   - Peso de F3 elevado al $6.5\%$.
   - Patrón XAI: Si equipo requiere $\le 3.5$ tiros a puerta por gol con $\text{xGOT} > \text{xG}$, activa **"Puntería Clínica"**; si requiere $\ge 5.0$ tiros a puerta por gol con $\ge 2.0$ grandes ocasiones falladas fuera, activa **"Maldición del Delantero"**.
4. **Cruce #4 — Asedio por Bandas, Juego Aéreo y Proyección de Córneres:**
   - Cruce: `corners` + `cruces_pct` + `toques_area_rival` (Local Casa) $\otimes$ `despejes` + `duelos_aereos_pct` (Rival Fuera).
   - Patrón XAI: Si Local promedia córneres $\ge 7.5$ y centros $\ge 20$, y el rival promedia $\ge 22$ despejes o $< 48\%$ duelos aéreos, activa **"Asedio por Bandas (Proyección Over 8.5 / 9.5 Córneres)"**.
5. **Cruce #5 — Presión, Frustración Táctica y Balón Parado Peligroso (Disciplina):**
   - Cruce: Posesión y toques de área del Local $\otimes$ `faltas` + `tarjetas_amarillas` + `tarjetas_rojas` del Visitante fuera.
   - Patrón XAI: Si el visitante comete $\ge 13.5$ faltas y $\ge 2.4$ amarillas fuera de casa, activa **"Defensa Kamikaze / Tarjeta Temprana"** (aumenta tiros libres y $\text{xG}_{\text{balon\_parado}}$).
6. **Cruce #6 — Cálculo Condicionado del Regalo Defensivo (F5):**
   - Fórmula acotada:
     $$\text{Regalo}_{\text{err}} = \text{clip}\left(0.45 \cdot \text{ErroresGol}_{\text{riv}(\text{cond})} + 0.15 \cdot \text{ErroresRemate}_{\text{riv}(\text{cond})} + 0.25 \cdot \text{Rojas}_{\text{riv}(\text{cond})}, 0.0, 0.35\right)$$

---

## 5. CORRECCIÓN AL BLOQUE DE DESBLOQUEO ANTI-1-1 (FASE 6.3)

1. **Prohibición de Desbloqueo en Partidos Parejos:**
   - Si $|\lambda_L - \lambda_V| < 0.65$ o $P(\text{Victoria Favorito}) < 62\%$: **Terminantemente prohibido restarle probabilidad a la casilla 1-1**.
2. **Transferencia Acotada Exclusiva para Favoritos Aplastantes:**
   - Solo si $|\lambda_L - \lambda_V| \ge 0.65$ y $P(\text{Victoria Favorito}) \ge 62\%$:
     * Transferir como máximo el $25\%$ del excedente:
       $$\Delta_{\text{trans}} = 0.25 \cdot \max(0, P(1, 1) - P(\text{Modal Favorito}))$$
     * Distribuir hacia $2\text{-}0$ ($60\%$) y $2\text{-}1$ ($40\%$) (o $0\text{-}2$ / $1\text{-}2$ si el favorito aplastante es visitante).
     * Jamás vaciar la casilla 1-1 por completo.

---

## 6. CORRECCIONES AL REPORTE TELEGRAM Y CUADRO ESPECIALISTA (FASE 8)

1. **Subsección 1 — ADN del Enfrentamiento (Lado a Lado):**
   - Presentar tabla o comparativa de Local en Casa ($N_L$) vs. Visitante Fuera ($N_V$): Posesión%, Goles Marcados, xG Generado, xG Concedido, Remates Totales / A Puerta, Toques en Área, Córneres, Centros, Despejes, Duelos Aéreos %, Faltas / Amarillas y Goles Evitados del Portero.
2. **Subsección 2 — Patrones Detectados por la Red (XAI):**
   - Imprimir las alertas dinámicas de los 6 cruces según se activen.
3. **Caja Gris — Duelo Parejo:**
   - Si $\max(P_L, P_V) < 58.0\%$, imprimir:
     `⚖️ DUELO PAREJO / RIESGO DE EMPATE (No jugar 1x2 directo)`
4. **Línea Obligatoria 🎯 JUGADA ÓPTIMA TE APUESTO:**
   - Si $P(\text{Favorito}) \ge 60\%$, $P(0\text{-}0) < 8.5\%$ y $|\lambda_L - \lambda_V| \ge 0.45$: Recomienda `1x2 : <Equipo Favorito> ({P_fav:.1f}%)`.
   - Si 1-1 está en el Top-2 con ★ y Ambos Anotan $\ge 51.0\%$: Recomienda `Ambos equipos marcan : Sí ({P_btts:.1f}%)`.
   - Si el favorito está entre $44\%$ y $59\%$ o rival promedia $\ge 22$ despejes o 1-1 / 0-0 en Top-3 con ★: Recomienda `Doble Oportunidad : Empate o <Equipo> [1X / X2] ({P_fav + P_emp:.1f}%)`.
   - Si Cruce #4 activa Asedio por Bandas: Añade mercado secundario destacado `Córneres : Más de 8.5 / 9.5 ({P_corners:.1f}%)`.
5. **Higiene en Canvas de Imagen:**
   - Sanitizar caracteres Unicode no soportados por tipografías del sistema (`🎯`, `⚖️`, `⚖`, `★` $\to$ `*`) mediante `_sanear_para_canvas` para erradicar rectángulos rotos `▯` en la imagen PNG.


---

## 7. AUDITORÍA INTEGRAL: REGLAS ANTI-CLONACIÓN, PROTECCIÓN SIN ESTADÍSTICAS Y SINCRONIZACIÓN MATRIZ-TICKET (CASO FENERBAHÇE W VS SLAVIA PRAGUE W)

1. **Anti-Clonación Estricta de Partidos:**
   - **Prohibición Absoluta de Duplicación Artificial:** Prohibido replicar filas (`[lista_L[0]] * faltan_L`) para llegar forzosamente a 15 partidos.
   - **Deduplicación Obligatoria:** Deduplicar por `id_partido` y por clave tupla `(fecha, equipo_local, equipo_visita)` en `auditoria_historica.py` y `cuadro_especialista.py`.
   - **Muestra Real con Contracción Bayesiana:** Si un equipo solo tiene $N < 15$ partidos únicos reales (ej. 3 partidos), se muestran y usan exactamente esos $N$ partidos, aplicando Contracción Bayesiana ($w = N / (N + 4.0)$), sin inventar ni clonar datos.

2. **Protección en Partidos sin Estadísticas (`remates_totales == 0`):**
   - **Reconstrucción Automática:** Si `remates_totales <= 0`, asignar `xg = float(goles_favor)`, `xgot = float(goles_favor)`, `xgot_enfrentados = float(goles_contra)` y `goles_evitados = 0.0` (neutral).
   - **Remates Base Coherentes:** Derivar `remates_totales = max(4.0, goles_favor * 4.0 + 3.0)` y `remates_puerta = max(1.0, goles_favor * 2.0 + 1.0)` para erradicar tiempos infinitos por tiro.
   - **Supresión de Falsos Porteros Vulnerables:** Si $>30\%$ de los partidos carecen de xGOT oficial, prohibido imprimir lecturas de portería vulnerable; imprimir `"Sin cobertura xGOT oficial en esta competición (modelo guiado por goles y remates)"`.
   - **Bloqueo de Mercados Córneres/Tarjetas sin Cobertura:** Si $\mu_{\text{córneres}} < 1.5$ o $\mu_{\text{tarjetas}} < 0.5$ (o $>30\%$ de ceros por omisión), marcar `estado="datos_insuficientes"`, ocultar las filas en el Ticket PNG y prohibir terminantemente imprimir selecciones como `-0.5 Córn. 100%` o `-0.5 Tarj. 100%`.

3. **Conexión Matemática Estricta de la Caja Azul con $\lambda_{\text{Red}}$:**
   - La variación porcentual total mostrada en el título de la Caja Azul (`L (+X.X%)` y `V (+Y.Y%)`) debe coincidir exactamente con la suma de los 5 factores latentes presentados (`pct_tot = sum(f.values())`). Erradicar recortes o clips opacos que alteren el resultado sin reflejarse en los factores.

4. **Sincronización Matriz-Ticket y Erradicación Total de `▯`:**
   - Inmediatamente después de actualizar `matriz_ft` con la Red Neuronal Recursiva en `motor_mercados.py`, recalcular obligatoriamente `self.clusters = clusters_marcador(...)` para que el pie del Ticket refleje la misma probabilidad y marcador modal dominante que la Matriz Conjunta.
   - Filtrar obligatoriamente todo texto dibujado en el canvas con `_sanear_para_canvas`, sustituyendo `🎯` por `>>`, `⚖️` por `[ALERTA]` o `[DUELO PAREJO / RIESGO DE EMPATE]` y `★` por `*`.

---

## 8. DIRECTIVA DEFINITIVA TRAS AUDITORÍA DE CHAMPIONS LEAGUE (SABAH BAKU VS BORUSSIA DORTMUND Y FENERBAHÇE VS SLAVIA PRAGA)

1. **Prohibición Estricta de Equipos Femeninos `(W)` o Juveniles en Prompts Senior:**
   - En el buscador de equipos de FotMob y Flashscore, descartar terminantemente candidatos o torneos que contengan `"(W)"`, `"Women"`, `"Femenino"`, `"Ladies"`, `"Fem."`, `"U18"`, `"U19"`, `"U20"`, `"U21"`, `"U23"`, `"Youth"`, `"II"`, `"Reserves"` o `"B"`, a menos que el usuario lo solicite explícitamente en su mensaje.
   - Preservar el nombre limpio pedido por el usuario (ej. `"Sabah Baku"`, `"Slavia Praga"`).

2. **Ponderación por Coeficiente de Liga (`tier_coef`) en Duelos Inter-Ligas / Europa:**
   - Coeficientes oficiales:
     * **Tier 1.00:** Top 5 Ligas Europeas (Premier League, LaLiga, Serie A, Bundesliga, Ligue 1) y UEFA Champions League (fase liga/grupos/playoffs).
     * **Tier 0.85:** Ligas Nivel 2 (Eredivisie, Liga Portugal, Süper Lig de Turquía, Jupiler Pro League Bélgica, Chance Liga Checa, Premiership Escocia, Championship) y UEFA Europa League.
     * **Tier 0.68:** Ligas Nivel 3 / Torneos Menores (Premyer Liqa Azerbaiyán, Chipre, Grecia, Austria, Suiza, Dinamarca, Noruega, Suecia, Polonia, Croacia, Serbia, fases previas / clasificación UCL/UEL/UECL, Segundas divisiones) y UEFA Conference League.
     * **Tier 0.50:** Terceras divisiones, ligas semi-profesionales o inferiores.
   - Si `tier_L != tier_V`, calcular el ratio relativo de fuerza:
     $$\text{ratio\_fuerza} = \left(\frac{\text{tier\_menor}}{\text{tier\_mayor}}\right)^{0.75}$$
     El equipo de liga menor multiplica su ataque por `ratio_fuerza` y su defensa concedida se divide por `ratio_fuerza` antes de calcular $\lambda_{\text{Base}}$.
   - Calcular $\lambda_{\text{Base}}$ como la media aritmética exacta:
     $$\lambda_{\text{base\_local}} = \frac{\text{atq}_{L,\text{ajustado}} + \text{def}_{V,\text{ajustada}}}{2.0}, \quad \lambda_{\text{base\_visita}} = \frac{\text{atq}_{V,\text{ajustado}} + \text{def}_{L,\text{ajustada}}}{2.0}$$
     ¡Eliminando completamente multiplicadores arbitrarios como 1.08 o 0.93!

3. **Blindaje ante Partidos sin Estadísticas y Erradicación de `-0.5` Córneres/Tarjetas:**
   - Si un partido tiene `remates_totales == 0` y `remates_totales_rival == 0`, se considera partido sin detalle estadístico: no aportar ceros al acumulador de `remates`, `posesion`, `xg`, `xgot`, córneres ni tarjetas.
   - Si un equipo no tiene ningún partido con estadísticas detalladas (`n_con_stats == 0`), aplicar el fallback neutral inteligente:
     $$\text{remates\_totales} = \max(6.0, \text{goles\_favor} \cdot 4.0 + 4.0), \quad \text{remates\_puerta} = \max(2.0, \text{goles\_favor} \cdot 2.0 + 1.0)$$
     $$\text{posesion} = 50.0, \quad \text{xg} = \text{goles\_favor}, \quad \text{xgot} = \text{goles\_favor}, \quad \text{xgot\_contra} = \text{goles\_contra}, \quad \text{goles\_evitados} = 0.0$$
   - En el Ticket Cuantitativo: Prohibido terminantemente incluir líneas $\le 0.5$ under (como `-0.5 Córn.` o `-0.5 Tarj.`). Si la media es $< 1.5$ o $>30\%$ ceros, bloquear la categoría con `datos_insuficientes`.

4. **Conexión Matemática al 100% en la Caja Azul (Re-escalado de Factores XAI):**
   - La variación total de $\lambda$ mostrada en el título de la Caja Azul (`L (delta_pct_real%)` y `V (delta_pct_real%)`) debe ser idéntica a la suma de los 5 factores latentes presentados (`PosProg`, `Creac`, `Finaliz`, `PortRiv`, `FragRiv`).
   - Aplicar `reescalar_factores_a_delta` para garantizar coherencia al primer decimal ($0.1\%$).

5. **Formato de Auditoría Limpio y sin Solapamiento:**
   - Diccionario `ABREV_TORNEOS` con truncamiento de torneo a máximo 18 caracteres.
   - Longitud estricta de cada línea de partido auditado a un máximo de 58 caracteres.
   - Tamaño de fuente `fontsize=7.3` para separar de forma limpia la columna izquierda de la columna derecha.

6. **Lecturas de Especialista Balanceadas y Canvas Higiénico:**
   - Imprimir obligatoriamente al menos 1 viñeta de `eq_L` y al menos 1 viñeta de `eq_V`, generando lectura informativa de rendimiento regular si la lista está vacía.
   - Sustituir cualquier `★` por `*` y procesar todo texto con `_sanear_para_canvas` para cero caracteres rotos `▯` en la imagen PNG.

---

## 9. AUDITORÍA 30/10/2026: EXTRACCIÓN MULTI-FUENTE AUTOMÁTICA, PODIO DE APUESTAS DE ALTO VALOR Y CALIBRACIÓN DE FACTORES LATENTES

1. **Extracción Multi-Fuente Automática (FotMob $\to$ Flashscore) y Normalización Universal de Clubes (`fuentes_xg.py`, `auditoria_historica.py`, `bot_interactivo.py`):**
   - **Separador limpio de Liga y Equipos:** Dividir el comando primero por `vs` (o `h2h`). Extraer el prefijo de liga únicamente al inicio de la parte izquierda (`bundesliga`, `la liga ea sports`, `laliga`, `mls`, `major league soccer`, `serie a de brasil`, `brasileirao`, `primera de chile`, `primera a de colombia`, `liga profesional de argentina`, `liga pro de ecuador`, `liga 1 de peru`, `premier league`, `serie a`, `ligue 1`, etc.) sin recortar ni alterar los nombres de los clubes.
   - **Normalizador Universal (`normalizar_nombre_club`):** Eliminar tildes/diacríticos (`Fráncfort` $\to$ `Frankfurt`, `Atlético` $\to$ `Atletico`, `Múnich` $\to$ `Munich`, `Colón` $\to$ `Colon`) y traducir exónimos y alias hispanos/MLS/Sudamérica:
     * `"eintracht francfort"` / `"francfort"` $\to$ `"Eintracht Frankfurt"`
     * `"nueva york red bull"` / `"ny red bulls"` / `"red bull nueva york"` $\to$ `"New York Red Bulls"`
     * `"inter miami"` / `"inter de miami"` $\to$ `"Inter Miami CF"`
     * `"nueva york city"` / `"nycfc"` $\to$ `"New York City FC"`
     * `"deportivo de a coruna"` / `"deportivo a coruna"` / `"deportivo la coruna"` $\to$ `"Deportivo La Coruna"`
     * Clubes de Sudamérica: `"sporting cristal"`, `"universitario"` ($\to$ `"Universitario de Deportes"`), `"alianza lima"`, `"melgar"`, `"cusco fc"`, `"colo colo"`, `"universidad de chile"`, `"universidad catolica"`, `"atletico nacional"`, `"millonarios"`, `"america de cali"`, `"junior"`, `"river plate"`, `"boca juniors"`, `"racing club"`, `"independiente del valle"`, `"ldu quito"`, `"barcelona sc"`, `"emelec"`, `"flamengo"`, `"palmeiras"`, `"botafogo"`, `"corinthians"`, `"sao paulo"`.
     * Si la búsqueda completa no devuelve resultados en FotMob, reintentar con el núcleo distintivo del nombre y similitud difusa (`difflib.SequenceMatcher >= 0.72`).
   - **Prohibición de `RuntimeError: No se encontró ningún equipo en fotmob`:** Si un equipo no aparece en FotMob o tiene $< 15$ partidos oficiales, activar automáticamente el fallback a `FuenteFlashscore` en el mismo flujo para completar los 15 partidos oficiales y sus estadísticas (`FT`, `1T`, `2T`).

2. **Rediseño del Ticket Cuantitativo: Filtro de Líneas Competitivas y `🏆 TOP 5 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO)` (`motor_mercados.py`, `servicio_mercados.py`):**
   - **Filtro Estricto de Líneas de Casa de Apuestas (Cero Relleno de Cuota 1.01):**
     * *Goles Totales FT:* Solo líneas entre `+1.5` y `+3.5`, y entre `-2.5` y `-4.5` (`+0.5 FT` solo si su probabilidad está entre $70\%$ y $88\%$; prohibido `-5.5`, `-6.5`).
     * *Goles 1T / 2T:* Solo `+0.5 Goles 1T/2T` y `-1.5 Goles 1T/2T` (prohibido `-2.5` a `-6.5` en mitades).
     * *Goles por Equipo:* Solo `+0.5`, `+1.5`, `-1.5` y `-2.5` (prohibido `-3.5` a `-6.5`).
     * *Córneres Totales FT:* Solo líneas competitivas entre `+7.5` y `+11.5`, y entre `-10.5` y `-12.5` (prohibido `+0.5` a `+3.5` y `-14.5`, `-15.5`). En *Córneres por Equipo*, solo líneas entre `3.5` y `6.5`.
     * *Tarjetas Totales FT:* Solo líneas entre `+2.5` y `+5.5`, y entre `-4.5` y `-6.5` (prohibido `+0.5 Tarj.`, `-7.5`, `-8.5`).
     * *Hándicap / 1X2 / DC:* Prohibido mostrar `H+2` o `H+3` a favor del favorito. Mostrar solo `1X2`, `Doble Oportunidad (1X / X2)`, `Sin Empate (DNB)` y `Hándicap -1 / +1`. Limitar el total del Ticket a un máximo de 15–20 opciones reales competitivas.
   - **Sección Superior Destacada `TOP 5 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO)`:**
     * Seleccionar arriba del todo hasta 5 apuestas reales diversificadas del partido (1 de Resultado/DC, 1 de Goles FT, 1 de Mitades/Goles por Equipo, 1 de Córneres y 1 de Tarjetas/Ambos Anotan), priorizando probabilidades validadas bajo estrés entre **70% y 88%**, con 1 línea de respaldo XAI basada en los 6 cruces tácticos.

3. **Tope Estricto $[-12.0\%, +15.0\%]$ en los 5 Factores Latentes de la Caja Azul (`motor_recursivo_45_dcn.py`):**
   - Prohibido dividir por `suma_raw` pequeño en `reescalar_factores_a_delta` produciendo explosiones como `PortRiv -111.7%`.
   - Acotar cada factor individual $F_k \in [-12.0\%, +15.0\%]$ con `np.clip(factor_pct, -12.0, 15.0)` y garantizar que ningún factor supere ese rango y que su suma directa sea exactamente el cambio porcentual de $\lambda_{\text{Base}} \to \lambda_{\text{Red}}$.

4. **Reconocimiento de `"LaLiga2"` en Descuento de División, 3 Lecturas de Especialista con los 6 Cruces y Sincronización Matriz-Ticket (`motor_mercados.py`, `cuadro_especialista.py`):**
   - Incluir `"laliga2"` (sin espacio), `"laliga 2"`, `"hypermotion"`, `"primera federacion"`, `"serie b"`, `"serie c"`, `"championship"`, `"primera b"` en todos los detectores de segunda/tercera división.
   - En `LECTURAS DE ESPECIALISTA`: usar exactamente el mismo `xG` y `xGOT` mostrado en la tabla superior y generar 3 viñetas tácticas por equipo basadas en los 6 cruces del Doble ADN (1. Control/Asedio/Córneres vs Despejes/Aéreos, 2. Puntería Tiros a puerta por gol y xGOT vs Goles Evitados del portero rival, 3. Disciplina/Faltas/Amarillas/Errores).
   - Unificar la matriz y `clusters_marcador` entre `MotorMercados` y `cuadro_especialista.py` para que `Dominante (...)` y `JUGADA OPTIMA TE APUESTO (...)` muestren exactamente el mismo porcentaje al $0.1\%$ en ambas imágenes.

