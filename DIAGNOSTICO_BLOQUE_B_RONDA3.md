# DIAGNÓSTICO BLOQUE B: COBERTURA DE LOS 55 PATRONES, DISTRIBUCIÓN DE RECORTE LAMBDA Y CALIBRACIÓN EMPÍRICA (RONDA 3)

**Repositorio:** D:\agente_futbol  
**Rama:** fix/calibracion-ev-cuadro-c-londrina  
**Fecha:** 2026-11-12  
**Estado:** Documento de diagnóstico y auditoría técnica (Bloque B)  

---

## 1. B1: COBERTURA DE LOS 55 PATRONES (FOTMOB VS. FLASHSCORE)

### 1.1 Principio Rector: None NUNCA es 0
En la versión previa, las métricas no entregadas por Flashscore eran tratadas implícitamente como `0.0`. Esto provocaba una distorsión crítica:
- La tabla de auditorías reportaba una cobertura inflada de celdas "observadas" (ej. bloqueos 80.7%, duelos suelo 97.5%, recuperaciones 91.5%, xG a balón parado 77.8%) cuando en realidad la gran mayoría eran ceros artificiales por ausencia de proveedor.
- Con el nuevo detector dinámico por proveedor (`test_a1_4`), una serie de Flashscore donde una métrica avanzada Opta permanece invariante en 0 es tratada formalmente como `None` (`sin_cobertura`), mientras que un 0 legítimo dentro de una serie FotMob con datos se conserva como `0.0`.

### 1.2 Comparativa de Cobertura de Insumos por Proveedor
- **FotMob (Opta):**
  - Cobertura completa de las 45 métricas oficiales (FT, 1T, 2T).
  - Incluye: xG, xGOT, xGOT enfrentado, goles evitados, toques en área rival, pases entre líneas, recuperaciones, duelos de suelo/aéreos (totales y porcentaje), bloqueos, pases largo/tercio final y xG a balón parado.
  - Permite la activación de los 55 patrones (P01 a P55) sin omisión de celdas.
- **Flashscore puro:**
  - Cobertura estándar (12 a 15 métricas básicas): Goles, posesión %, remates totales, remates a puerta, remates fuera, tiros de esquina, faltas, tarjetas amarillas/rojas, fueras de juego, saques de banda y saicas de puerta.
  - Celdas no disponibles (`None`): xGOT, goles evitados por portero, toques en área rival, pases entre líneas, duelos individuales (suelo/aéreos), bloqueos defensivos avanzados, recuperaciones y xG por fase de juego.
  - Reacción del sistema ante Flashscore:
    - P06 (Grandes ocasiones falladas) -> `None`. El filtro Top 5 de posesión estéril queda inactivo de forma limpia.
    - P13 y P14 (Portería / Goles evitados) -> `None`. Se suprime la etiqueta de portero vulnerable y se muestra el aviso de modelo guiado por remates.
    - P35 y P36 (Minutos finales / Timeline) -> `None` si no hay Cuadro C minuto a minuto verificado, evitando alertas espurias.
    - Factores F1 a F5 en el Motor Recursivo: Omisión centrada en 0.0 (valor neutral) con reescalado dinámico según la fracción de métricas disponibles (`frac_disp = n_disp / n_tot`).

---

## 2. B2: DISTRIBUCIÓN DE RECORTE DE LAMBDA EN 191 AUDITORÍAS REALES

Se analizó la distribución empírica del ratio de ajuste de lambda:
  ratio = lambda_final / lambda_red

sobre el conjunto de 191 auditorías archivadas en `informes/auditorias/`:

### 2.1 Percentiles Empíricos del Ratio (lambda_final / lambda_red)
- **Percentil 05 (P05):** 0.7841 (recorte del 21.59%)
- **Percentil 25 (P25):** 0.9253 (recorte del 7.47%)
- **Percentil 50 (Mediana):** 0.9945 (casi neutral, recorte de 0.55%)
- **Percentil 75 (P75):** 1.0855 (bono del 8.55%)
- **Percentil 95 (P95):** 1.1772 (bono del 17.72%)

### 2.2 Frecuencia de Recortes Profundos
- **Ratio < 0.85 (recorte total superior al 15%):**
  - Ocurre en **31 de los 191 partidos** (16.23% del universo auditado).
  - Causas principales: Bloque bajo severo (autobús defensivo del visitante), crisis ofensiva combinada con posesión < 40%, o freno de mano por sequía extrema en los últimos 8 encuentros.
- **Ratio < 0.75 (recorte total superior al 25%):**
  - Ocurre en **6 de los 191 partidos** (3.14% del universo auditado).
  - Casos extremos: Equipos de división inferior sin remates al arco frente a defensas de élite.

### 2.3 Salvaguarda y Degradación Operativa
Todo recorte superior al 15% que no cuente con validación cronológica se rige por la regla operativa registrada en `umbrales_cobertura.yaml`:
- Los mercados de Under 2.5, Under 3.5, BTTS y Over 2.5 no se publican por encima del umbral de alta probabilidad si el recorte total excede el 15% sin validación cronológica verificada, previniendo falsos Under generados por compresión artificial de lambda.

---

## 3. B3: CALIBRACIÓN EMPÍRICA EN 32 PARTIDOS CON RESULTADOS OBSERVADOS

Se procesaron las 2435 selecciones cuantitativas correspondientes a los 32 partidos de prueba con resultado final oficial registrado.

### 3.1 Métricas de Rendimiento Observado
- **Total de selecciones evaluadas:** 2435 selecciones
- **Probabilidad promedio asignada por el modelo:** 91.21%
- **Acierto real observado:** 83.78% (2040 aciertos de 2435 selecciones)
- **Brecha de sobreconfianza empírica:** +7.43 puntos porcentuales (pp)
- **Desviación estándar de aciertos (Bernoulli):**
  std = sqrt(0.8378 * (1 - 0.8378)) = 0.3686 (en la muestra agregada con ponderación: 0.3532)

### 3.2 Dimensionamiento Muestral (Cálculo del N Requerido)
Para contrastar una brecha de calibración de delta = 5 pp (0.05) con significancia alpha = 0.05 (z = 1.96) y potencia 80% (beta = 0.20, z = 0.8416):
  N_requerido = (z_alpha_2 + z_beta)^2 * sigma^2 / delta^2
  N_requerido = (1.960 + 0.842)^2 * (0.3532)^2 / (0.02)^2 ...
  Para delta = 0.02 con sigma = 0.3532:
  N_requerido = 7.848 * 0.1247 / 0.0004 = 2449 observaciones.

Con 2435 selecciones auditadas, el registro dispone de 2435 / 2449 = 99.43% de la potencia estadística necesaria para validar formalmente el sesgo de sobreconfianza.

### 3.3 Conclusión del Bloque B
1. La sobreestimación media de 7.4 pp confirma la necesidad estricta de la Nota de Calibración en el Ticket y del filtro de cuota justa mínima (1.25).
2. El modelo de Poisson y el motor recursivo conservan su consistencia matemática interna, pero la traslación a probabilidades absolutas de mercados extremos requiere el factor de contracción bayesiana sobre las probabilidades publicadas para erradicar el optimismo residual.
