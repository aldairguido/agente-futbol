# AUDITORÍA DE PRONÓSTICOS Y CALIBRACIÓN

Generado: 2026-10-14 16:42:02

## 1. Resultados verificados

| Partido | Estado | Marcador | 1T | Córneres L/V | Amarillas L/V | Fuente |
|---|---|---|---|---|---|---|
| Roma vs Real Madrid | finalizado | 3-4 | 1-1 | 5/9 | 3/1 | fotmob |

## 2. Acierto por banda de probabilidad original

| Banda | Publicadas | Evaluables | Partidos | Ganadas | Perdidas | Devueltas | Pendientes | Acierto real | Prob. media | Brecha (pp) |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.70-0.75 | 14 | 9 | 1 | 6 | 3 | 0 | 5 | 66,7% | 72,8% | -6,1 |
| 0.75-0.80 | 30 | 13 | 1 | 8 | 5 | 0 | 17 | 61,5% | 77,8% | -16,2 |
| 0.80-0.85 | 20 | 12 | 1 | 8 | 4 | 0 | 8 | 66,7% | 82,0% | -15,3 |
| 0.85-0.90 | 17 | 8 | 1 | 5 | 3 | 0 | 9 | 62,5% | 87,5% | -25,0 |
| 0.90-0.95 | 25 | 11 | 1 | 7 | 4 | 0 | 14 | 63,6% | 92,8% | -29,1 |
| 0.95-1.00 | 107 | 55 | 1 | 45 | 10 | 0 | 52 | 81,8% | 98,5% | -16,7 |

## 3. Acumulados (se superponen: no se suman)

| Acumulado | Evaluables | Acierto real | Prob. media | Brecha (pp) |
|---|---|---|---|---|
| p>70% | 108 | 73,1% | 90,6% | -17,5 |
| p>=85% | 74 | 77,0% | 96,4% | -19,4 |
| p>=90% | 66 | 78,8% | 97,5% | -18,7 |
| p>=95% | 55 | 81,8% | 98,5% | -16,7 |

## 4. Desglose por familia de mercado

| Familia | Evaluables | Partidos | Acierto real | Prob. media | Brecha (pp) |
|---|---|---|---|---|---|
| Total de goles | 59 | 1 | 79,7% | 92,2% | -12,5 |
| Total de tiros de esquina | 14 | 1 | 71,4% | 89,3% | -17,9 |
| Real Madrid - Total de tiros de esquina | 11 | 1 | 36,4% | 89,4% | -53,0 |
| Roma - Tiros de esquina | 10 | 1 | 40,0% | 90,9% | -50,9 |
| Tarjetas | 7 | 1 | 100,0% | 89,6% | +10,4 |
| Hándicap | 4 | 1 | 100,0% | 87,2% | +12,8 |
| Gol en ambos tiempos | 2 | 1 | 100,0% | 77,6% | +22,4 |
| Doble oportunidad | 1 | 1 | 100,0% | 76,0% | +24,0 |

## 4.1 Desglose por EQUIPO

Todas las selecciones que hablan de un equipo, sumando sus partidos como local y como visitante. Los mercados del partido (sin equipo) van aparte.

| Equipo | Condición | Prob. media | Evaluables | Aciertos | Acierto real | Brecha (pp) | Partidos | Pendientes |
|---|---|---|---|---|---|---|---|---|
| Partido (sin equipo) | partido | 88,5% | 45 | 35 | 77,8% | -10,7 | 1 | 43 |
| Real Madrid | fuera | 91,9% | 32 | 21 | 65,6% | -26,2 | 1 | 31 |
| Roma | casa | 92,4% | 31 | 23 | 74,2% | -18,2 | 1 | 31 |

## 4.2 Desglose por EQUIPO y CONDICIÓN

| Equipo · condición | Prob. media | Evaluables | Aciertos | Acierto real | Brecha (pp) |
|---|---|---|---|---|---|
| Partido (sin equipo) · partido | 88,5% | 45 | 35 | 77,8% | -10.7 |
| Real Madrid · fuera | 91,9% | 32 | 21 | 65,6% | -26.2 |
| Roma · casa | 92,4% | 31 | 23 | 74,2% | -18.2 |

## 4.3 Localía (casa frente a fuera)

| Condición | Prob. media | Evaluables | Aciertos | Acierto real | Brecha (pp) |
|---|---|---|---|---|---|
| casa | 92,4% | 31 | 23 | 74,2% | -18,2 |
| fuera | 91,9% | 32 | 21 | 65,6% | -26,2 |
| partido | 88,5% | 45 | 35 | 77,8% | -10,7 |

## 5. Desglose por periodo y por sensibilidad

**Periodo**

- 1T: 20 evaluables, acierto 100,0%, prob. media 93,1%
- 2T: 20 evaluables, acierto 70,0%, prob. media 92,9%
- FT: 68 evaluables, acierto 66,2%, prob. media 89,2%

**Sensibilidad**

- mantiene: 102 evaluables, acierto 73,5%, prob. media 91,7%
- sensible: 6 evaluables, acierto 66,7%, prob. media 72,6%

## 6. Tabla de calibración

```
  anunciado -> observado            (n / partidos)
  --------------------------------------------------------------------------
   72.8% ->  66.7%  |                           O P           |  n=9    partidos=1
   77.8% ->  61.5%  |                         O     P         |  n=13   partidos=1
   82.0% ->  66.7%  |                           O     P       |  n=12   partidos=1
   87.5% ->  62.5%  |                         O         P     |  n=8    partidos=1
   92.8% ->  63.6%  |                         O           P   |  n=11   partidos=1
   98.5% ->  81.8%  |                                 O     P |  n=55   partidos=1
  --------------------------------------------------------------------------
  P = probabilidad anunciada · O = frecuencia observada · X = coinciden
```

Brier binario: 0.2227201084067738 (n=108). Referencia de climatología: 0.19641632373113854.

## 7. Conteo bruto frente a promedio equilibrado por partido

- Selecciones brutas evaluables: 108
- Partidos únicos: 1
- Acierto bruto (por selección): 73,1%
- Acierto equilibrado (media de partidos): 73,1%
- Brecha equilibrada: -17.5 pp
- El conteo bruto responde «cuántas selecciones acertaron»; el equilibrado evita que un partido con muchas líneas domine. La incertidumbre respeta la agrupación por partido.

## 8. Auditoría de la banda 100%

- Selecciones anunciadas al ~100%: 11
- Exactamente 1,0: 0
- Fallos en esa banda: 0
- Hallazgo: Los valores ~100% proceden de líneas extremas con probabilidad casi unitaria, no de un 1.0 exacto.

## 9. Evidencia y limitaciones

- EVIDENCIA INSUFICIENTE PARA UN INTERVALO ESTABLE: 1 partidos únicos (mínimo 30) para 108 selecciones evaluables. Las métricas son descriptivas de esta tanda y no permiten concluir calibración: las líneas de un mismo partido están correlacionadas y no son ensayos independientes.

### 9.1 Versiones del mismo pronóstico

- Regla aplicada (fijada antes de ver resultados): **ultima_publicada**
- Partidos con varias emisiones: 1
- Selecciones repetidas descartadas: 103

- **roma_vs_realmadrid_sinfecha**: 2 emisiones, 211 selecciones emitidas en total, 108 únicas. Versión usada: `c056e997d152456e` (primera: `6ada5cd1eba43a3e`, última: `c056e997d152456e`)
  - `6ada5cd1eba43a3e` generado 2026-10-14 12:20:25 (105 publicadas)
  - `c056e997d152456e` generado 2026-10-14 14:07:10 (106 publicadas)

- Limitaciones de trazabilidad:
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - Roma vs Real Madrid: pronóstico 2026-10-14 15:56:41 NO anterior al inicio 2026-10-14T21:00:45Z (el pronóstico es posterior: no se evalúa como acierto)
  - La probabilidad original NO está redondeada en el archivo de mercados, pero el mensaje de Telegram mostró el valor redondeado a 1 decimal: se conservan ambos.
  - El archivo histórico no registra la hora exacta de publicación en Telegram; se usa como cota superior la marca de generación.
  - No registra la fecha de inicio del partido: la comprobación «pronóstico anterior al inicio» se resuelve después, al consultar el resultado real.
