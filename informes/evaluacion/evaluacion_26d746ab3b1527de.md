# AUDITORÍA DE PRONÓSTICOS Y CALIBRACIÓN

Generado: 2026-10-14 16:35:46

## 1. Resultados verificados

| Partido | Estado | Marcador | 1T | Córneres L/V | Amarillas L/V | Fuente |
|---|---|---|---|---|---|---|
| Roma vs Real Madrid | finalizado | 3-4 | 1-1 | 5/9 | 3/1 | fotmob |

## 2. Acierto por banda de probabilidad original

| Banda | Publicadas | Evaluables | Partidos | Ganadas | Perdidas | Devueltas | Pendientes | Acierto real | Prob. media | Brecha (pp) |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.70-0.75 | 2 | 1 | 1 | 1 | 0 | 0 | 1 | 100,0% | 71,2% | +28,8 |
| 0.75-0.80 | 6 | 3 | 1 | 0 | 3 | 0 | 3 | 0,0% | 78,4% | -78,4 |
| 0.80-0.85 | 6 | 3 | 1 | 3 | 0 | 0 | 3 | 100,0% | 80,7% | +19,3 |
| 0.85-0.90 | 6 | 3 | 1 | 2 | 1 | 0 | 3 | 66,7% | 86,8% | -20,1 |
| 0.90-0.95 | 7 | 3 | 1 | 2 | 1 | 0 | 4 | 66,7% | 93,4% | -26,8 |
| 0.95-1.00 | 35 | 18 | 1 | 15 | 3 | 0 | 17 | 83,3% | 98,6% | -15,3 |

## 3. Acumulados (se superponen: no se suman)

| Acumulado | Evaluables | Acierto real | Prob. media | Brecha (pp) |
|---|---|---|---|---|
| p>70% | 31 | 74,2% | 92,4% | -18,2 |
| p>=85% | 24 | 79,2% | 96,5% | -17,3 |
| p>=90% | 21 | 81,0% | 97,9% | -16,9 |
| p>=95% | 18 | 83,3% | 98,6% | -15,3 |

## 4. Desglose por familia de mercado

| Familia | Evaluables | Partidos | Acierto real | Prob. media | Brecha (pp) |
|---|---|---|---|---|---|
| Total de goles | 19 | 1 | 89,5% | 93,4% | -3,9 |
| Roma - Tiros de esquina | 10 | 1 | 40,0% | 90,9% | -50,9 |
| Hándicap | 2 | 1 | 100,0% | 90,5% | +9,5 |

## 4.1 Desglose por EQUIPO

Todas las selecciones que hablan de un equipo, sumando sus partidos como local y como visitante. Los mercados del partido (sin equipo) van aparte.

| Equipo | Condición | Prob. media | Evaluables | Aciertos | Acierto real | Brecha (pp) | Partidos | Pendientes |
|---|---|---|---|---|---|---|---|---|
| Roma | casa | 92,4% | 31 | 23 | 74,2% | -18,2 | 1 | 31 |

## 4.2 Desglose por EQUIPO y CONDICIÓN

| Equipo · condición | Prob. media | Evaluables | Aciertos | Acierto real | Brecha (pp) |
|---|---|---|---|---|---|
| Roma · casa | 92,4% | 31 | 23 | 74,2% | -18.2 |

## 4.3 Localía (casa frente a fuera)

| Condición | Prob. media | Evaluables | Aciertos | Acierto real | Brecha (pp) |
|---|---|---|---|---|---|
| casa | 92,4% | 31 | 23 | 74,2% | -18,2 |

## 5. Desglose por periodo y por sensibilidad

**Periodo**

- 1T: 6 evaluables, acierto 100,0%, prob. media 95,8%
- 2T: 6 evaluables, acierto 83,3%, prob. media 95,5%
- FT: 19 evaluables, acierto 63,2%, prob. media 90,3%

**Sensibilidad**

- mantiene: 31 evaluables, acierto 74,2%, prob. media 92,4%

## 6. Tabla de calibración

```
  anunciado -> observado            (n / partidos)
  --------------------------------------------------------------------------
   71.2% -> 100.0%  |                            P           O|  n=1    partidos=1
   78.4% ->   0.0%  |O                              P         |  n=3    partidos=1
   80.7% -> 100.0%  |                                P       O|  n=3    partidos=1
   86.8% ->  66.7%  |                           O       P     |  n=3    partidos=1
   93.4% ->  66.7%  |                           O         P   |  n=3    partidos=1
   98.6% ->  83.3%  |                                 O     P |  n=18   partidos=1
  --------------------------------------------------------------------------
  P = probabilidad anunciada · O = frecuencia observada · X = coinciden
```

Brier binario: 0.21541873217478277 (n=31). Referencia de climatología: 0.1914672216441207.

## 7. Conteo bruto frente a promedio equilibrado por partido

- Selecciones brutas evaluables: 31
- Partidos únicos: 1
- Acierto bruto (por selección): 74,2%
- Acierto equilibrado (media de partidos): 74,2%
- Brecha equilibrada: -18.2 pp
- El conteo bruto responde «cuántas selecciones acertaron»; el equilibrado evita que un partido con muchas líneas domine. La incertidumbre respeta la agrupación por partido.

## 8. Auditoría de la banda 100%

- Selecciones anunciadas al ~100%: 4
- Exactamente 1,0: 0
- Fallos en esa banda: 0
- Hallazgo: Los valores ~100% proceden de líneas extremas con probabilidad casi unitaria, no de un 1.0 exacto.

## 9. Evidencia y limitaciones

- EVIDENCIA INSUFICIENTE PARA UN INTERVALO ESTABLE: 1 partidos únicos (mínimo 30) para 31 selecciones evaluables. Las métricas son descriptivas de esta tanda y no permiten concluir calibración: las líneas de un mismo partido están correlacionadas y no son ensayos independientes.

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
