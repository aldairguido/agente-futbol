# DIRECTIVA MAESTRA Y ARQUITECTURA HÍBRIDA DEL AGENTE (AUDITORÍA DE 8 FASES + MÓDULO DOBLE ADN LOCAL/VISITA)

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
