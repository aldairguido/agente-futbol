# ESPECIFICACIÓN DE DISEÑO BLOQUE C: REGISTRO ADAPTATIVO, DELTA_P, SPRT Y PROCEDENCIA DE DATOS (RONDA 3)

**Repositorio:** D:\agente_futbol  
**Rama:** fix/calibracion-ev-cuadro-c-londrina  
**Fecha:** 2026-11-12  
**Estado:** Especificación de arquitectura y diseño metodológico (Bloque C - Solo Texto)  
**Regla Estricta:** Diseño primero; no se implementa en código sin autorización expresa del usuario.  

---

## 1. C1: NORMALIZACIÓN SOBRE LA SUMA DE PESOS DE TODOS LOS PATRONES DEL CATÁLOGO

### 1.1 El Fallo del Diseño Anterior
En formulaciones previas, normalizar los factores exclusivamente sobre la suma de pesos de los patrones *activos* introducía una distorsión de escala: si en un partido se activaba un único patrón con peso w=1.0, su efecto absorbía el 100% del rango disponible, magnificando de forma espuria señales locales.

### 1.2 Formulación Matemática Corregida
Sea C el conjunto completo de todos los patrones definidos en el catálogo oficial (M patrones en total):
  C = {P_1, P_2, ..., P_M}
Cada patrón P_i posee un peso regulatorio preestablecido w_i > 0.
La suma total de pesos del catálogo es una constante fija del modelo:
  W_total = sum_{i=1}^M w_i

Para cualquier partido k, cada patrón produce una señal acotada c_i(k) in [-M_c, +M_c], donde c_i(k) = 0.0 si el patrón está inactivo, si sus insumos son None, o si la condición táctica no se cumple.
La variación total del factor latente F es:
  Delta_F(k) = [ sum_{i in Activos} w_i * c_i(k) ] / W_total

Dado que la suma se divide siempre entre W_total (la suma de todos los patrones del catálogo, activos e inactivos):
  sum_{i in Activos} w_i / W_total <= 1.0 (garantizado en todo momento)

### 1.3 Demostración Numérica: Aislamiento contra Inflación por Cruces Correlacionados
Supongamos un factor gobernado inicialmente por 2 patrones:
- Patrón 1 (antiguo, señal ofensiva): w_1 = 1.0, señal observada c_1 = +0.10 (+10.0%).
- Patrón 2 (neutro en este partido): w_2 = 1.0, señal observada c_2 = 0.0.
- Suma de pesos del catálogo: W_total = 1.0 + 1.0 = 2.0.
- Contribución inicial del factor:
    Delta_F_inicial = (1.0 * 0.10 + 1.0 * 0.0) / 2.0 = 0.10 / 2.0 = +0.050 (+5.0%).
- Peso efectivo del Patrón 1 en el factor: w_1 / W_total = 1.0 / 2.0 = 50.0%.

Se descubre e incorpora un Patrón 3 (cruce nuevo correlacionado con el Patrón 1):
- Patrón 3 (nuevo): w_3 = 0.8. Al estar correlacionado con el Patrón 1, detecta también la misma tendencia positiva: c_3 = +0.10.
- La nueva suma de pesos del catálogo completo es:
    W_total_nuevo = 1.0 + 1.0 + 0.8 = 2.8.
- Contribución del factor con el catálogo ampliado:
    Delta_F_nuevo = (1.0 * 0.10 + 1.0 * 0.0 + 0.8 * 0.10) / 2.8 = (0.10 + 0.08) / 2.8 = 0.18 / 2.8 = +0.0643 (+6.43%).
- Nuevo peso efectivo del Patrón 1:
    w_1 / W_total_nuevo = 1.0 / 2.8 = 35.71%.
- Nuevo peso efectivo del Patrón 3:
    w_3 / W_total_nuevo = 0.8 / 2.8 = 28.57%.

**Conclusión Matemática de la Demostración:**
1. El peso efectivo del patrón viejo cayó automáticamente del 50.0% al 35.71%.
2. La variación final no se duplicó (no pasó de +5.0% a +10.0%), sino que creció moderadamente a +6.43%, reflejando el incremento en evidencia pero diluyendo la concentración de riesgo.
3. Si el Patrón 3 hubiese arrojado señal neutral (c_3 = 0.0), el factor total habría decrecido automáticamente a 0.10 / 2.8 = +3.57%, castigando la falta de confirmación del nuevo cruce.

---

## 2. C2: DEFINICIÓN FORMAL, UNIDADES Y RANGO DE DELTA_P

### 2.1 Definición Matemática
`Delta_P` es la variación absoluta de probabilidad (diferencia aritmética en la escala continua [0, 1]) que un ajuste táctico, modulador o patrón introduce respecto a la probabilidad generada por el modelo cuantitativo base (Poisson bivariado calibrado):
  Delta_P = P_ajustada - P_base

Donde:
- `P_base`: Probabilidad del mercado en [0.0, 1.0] calculada por la matriz de marcadores Poisson con contracción bayesiana antes del ajuste.
- `P_ajustada`: Probabilidad final del mercado en [0.0, 1.0] tras aplicar el ajuste o la red recursiva.

### 2.2 Unidades y Rango Operativo
- **Unidad formal:** Probabilidad continua en [0.0, 1.0] (adimensional). Para visualización en reportes y tickets, se expresa en puntos porcentuales (pp), donde 0.050 equivale a +5.0 pp.
- **Rango matemático teórico:** [-1.0, +1.0] (o [-100.0 pp, +100.0 pp]).
- **Rango regulatorio admisible en el sistema:**
  Todo patrón individual en el catálogo está estrictamente acotado:
    -0.15 <= Delta_P <= +0.15  (-15.0 pp a +15.0 pp)
  La variación combinada total sobre cualquier selección no puede desplazar la probabilidad base en más de +/-20.0 pp (`|Delta_P_total| <= 0.20`), evitando que un modelo probabilístico riguroso sea dominado por ajustes heurísticos.

### 2.3 Ejemplo Numérico
- Mercado: `Goles Náutico (FT): -1.5 Goles` (Under 1.5 visitante).
- `P_base` (Poisson con lambda_V=0.90): 0.7725 (77.25%).
- Ajuste táctico aplicado: Freno de mano por sequía severa (8 partidos con solo 3 goles anotados) ajusta lambda_V a 0.60.
- `P_ajustada` (Poisson con lambda_V=0.60): 0.8353 (83.53%).
- `Delta_P = 0.8353 - 0.7725 = +0.0628 (+6.28 pp)`.
- Verificación de límites: |+0.0628| <= 0.15 (admisible dentro de la regla operativa).

---

## 3. C3: SUSTITUCIÓN DE BRIER CON N=30 POR SPRT CON LOG-LOSS BERNOULLI Y CONTROL FDR

### 3.1 Deficiencias del Enfoque Previo (Brier Score con N=30)
Evaluar la incorporación de un nuevo patrón mediante el Brier score sobre una ventana corta de 30 partidos presenta graves problemas:
1. N=30 carece de potencia estadística para discernir ventajas sutiles de calibración (ruido muestral dominante).
2. Alta vulnerabilidad a la secuencia temporal (efecto racha local).
3. No controla el error de tipo I (falsos descubrimientos) cuando se prueban múltiples cruces simultáneamente.

### 3.2 Formulación del Sequential Probability Ratio Test (SPRT)
Para cada partido k en el que el patrón candidato P_cand se activa, se observa el resultado binario real del mercado y_k in {0, 1} (1 = selección acertada, 0 = fallada).
- Hipótesis nula H_0: El patrón no aporta valor predictivo genuino; la probabilidad real se describe mejor con el modelo base P_0,k.
- Hipótesis alternativa H_1: El patrón mejora la calibración; la probabilidad real se describe con la predicción del patrón P_1,k.

La pérdida logarítmica de Bernoulli (log-loss) para el partido k es:
  L(p, y_k) = - [ y_k * ln(p) + (1 - y_k) * ln(1 - p) ]

El estadístico de razón de verosimilitud logarítmica acumulado hasta el partido n es:
  Lambda_n = sum_{k=1}^n [ L(P_0,k, y_k) - L(P_1,k, y_k) ]
           = sum_{k=1}^n [ y_k * ln(P_1,k / P_0,k) + (1 - y_k) * ln((1 - P_1,k) / (1 - P_0,k)) ]

### 3.3 Límites de Decisión de Wald
Fijando un nivel de significancia alpha = 0.01 (tasa de falsos positivos 1%) y una potencia 1 - beta = 0.95 (beta = 0.05):
  Limite_Superior (A) = ln((1 - beta) / alpha) = ln(0.95 / 0.01) = ln(95) = +4.5539
  Limite_Inferior (B) = ln(beta / (1 - alpha)) = ln(0.05 / 0.99) = ln(0.0505) = -2.9857

Reglas de decisión secuencial en cada partido k:
1. Si Lambda_n >= +4.5539: Se acepta H_1 (patrón estadísticamente validado).
2. Si Lambda_n <= -2.9857: Se rechaza H_1 permanentemente (el patrón se descarta por degradar la log-loss).
3. Si -2.9857 < Lambda_n < +4.5539: Estado indeciso; se continúa acumulando partidos.

### 3.4 Convivencia con N >= 210 y Control FDR (Benjamini-Hochberg)
Para garantizar solidez institucional e impedir promociones espurias:
1. **Barrera de Madurez Mínima (N >= 210):**
   Aunque Lambda_n cruce el límite superior A tempranamente (por ejemplo en el partido 45 debido a una racha favorable), el patrón NO puede ingresar al catálogo oficial de producción hasta haber acumulado al menos N_efectivo >= 210 activaciones cronológicas independientes fuera de muestra.
2. **Corrección por Tasa de Falso Descubrimiento (FDR):**
   Al auditar simultáneamente un lote de M cruces candidatos (ej. M = 50 hipótesis de patrones), los p-valores asintóticos derivados de los estadísticos SPRT se corrigen mediante el procedimiento de Benjamini-Hochberg:
     p_(1) <= p_(2) <= ... <= p_(M)
   Se selecciona el umbral crítico:
     k_max = max { i : p_(i) <= (i / M) * q* }
   con q* = 0.05.
   Solo los cruces con rango i <= k_max son homologados para producción.

---

## 4. C4: TRATAMIENTO DE PROCEDENCIA DE DATOS (FOTMOB VS. FLASHSCORE)

### 4.1 El Dilema de la Información Asimétrica
Si un equipo posee el 80% de sus partidos históricos provenientes de Flashscore (sin datos de toques en área, pases entre líneas, xGOT ni goles evitados), surge la cuestión de cómo debe el registro adaptativo evaluar un patrón que requiere tales métricas.

### 4.2 Decisión Metodológica y Justificación
Se establecen tres reglas inmutables:
1. **PROHIBICIÓN TOTAL DE IMPUTACIÓN:**
   Bajo ninguna circunstancia se imputan valores sintéticos (0.0, medias de liga o regresiones) en métricas no observadas para forzar la activación de un patrón.
2. **Exclusión de Partidos sin Cobertura en la Verificación del Patrón:**
   Para un patrón que consume un vector de métricas M_req:
   - En cada partido histórico k, se calcula la cobertura booleana:
       gamma_k = 1.0 si TODAS las métricas de M_req están observadas, 0.0 si falta al menos una.
   - Si gamma_k == 0.0:
     El patrón emite estrictamente `None`.
     Ese partido aporta 0.0 al estadístico SPRT Lambda_n (término nulo: L(P_0,k, y_k) - L(P_0,k, y_k) = 0).
3. **Penalización Bayesiana por Falta de Cobertura en el Tamaño Efectivo (N_efectivo):**
   El tamaño de muestra del candidato no se cuenta en partidos calendáricos brutos, sino en partidos con cobertura integral:
     N_efectivo = sum_{k=1}^K gamma_k
   Si un equipo tiene 15 partidos históricos pero 12 provienen de Flashscore sin métricas Opta, su N_efectivo para ese cruce es únicamente 3.
   En consecuencia, el candidato necesitará 210 partidos con cobertura completa de FotMob (Opta) para alcanzar la barrera de madurez N >= 210.
   Esto impide matemáticamente que un cruce sea validado o incorporado basándose en partidos donde la señal no existía.
