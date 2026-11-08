


---

## 7. NIVEL TOP 1%: ARQUITECTURA DUAL FOTMOB <-> FLASHSCORE, LOS 5 MÓDULOS DE CONTEXTO (`contexto_avanzado.py`) Y LOS 5 AJUSTES MATEMÁTICOS FINOS
- **Arquitectura Dual FotMob $\leftrightarrow$ Flashscore:** `validar_identidad_club` y failover automático FotMob $\to$ Flashscore sin requerir `ctx` compartido.
- **5 Módulos de Contexto (`contexto_avanzado.py`):** Fatiga (`<= 3 días` $\to$ `-15%` $\lambda$), Motivación (`zona_muerta` `-10%`, `alta_urgencia` `+6%`), Bajas Clave (`sin_goleador_titular` `-20%`, `sin_portero_titular` `+15%` rival), Árbitro (`>= 5.5` `+18%`, `<= 3.4` `-15%` tarjetas) y Sharp Money (subida $\ge 10\%$ descarta 1X2 directo en Top 1).
- **5 Ajustes Matemáticos Finos:**
  1) Córneres `* 0.80` cuando `remates_totales > 15.0`.
  2) Freno de Mano / Techo de $\lambda$ en crisis (`<= 0.60` si `Attack < 20` y crisis; `<= 1.10` si `[EN CRISIS]` o `Attack < 35`).
  3) Eliminación permanente de `"12 (Sin Empate)"` en Top 5 y Sección 1 cuando `P(X) >= 15%`.
  4) Factor Matagigantes (`pos_local < 36%` + alta puntería $\to$ `-20%` $\lambda$ visitante favorito) y regresión `-10%` por sobre-rendimiento (`goles_ult5 / xg_ult5 > 1.30`).
  5) Límite de tarjetas `-15%` por monopolio de posesión (`>= 68%`) y exclusión de `+4.5 Tarjetas` en Top 5.
