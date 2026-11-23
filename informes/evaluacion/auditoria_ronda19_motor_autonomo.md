# Auditoría Walk-Forward Ronda 19 — Motor Autónomo DPP / φTD / Topología / FAA

## Cuadro 1 — Embudo del Explorador Jerárquico (Órdenes 1 a 5)
- Partidos en banco: `83` | Evaluados walk-forward: `48`
- Variables con cobertura >= 60% por partido: `38.83`
- Hipótesis evaluadas (total / prom. por partido): `8851` / `184.4`
- Candidatos tras poda marginal (total / prom.): `2983` / `62.15`
- Distribución por orden (total): `{1: 1127, 2: 999, 3: 611, 4: 185, 5: 61}`
- Destilados por `PoolDPP-MAP` ($K=10$): `10.0` prom./partido
- Sobrevivientes a Benjamini-Hochberg FDR ($q < 0.05$): `0` (`0.0`/partido)

## Cuadro 2 — Eficiencia de `PoolDPP-MAP` en Patrones y en el Top 5
- Correlación media entre patrones (antes vs después de GreedyMAP): `0.2839` -> `0.1168` (`log det Gram = -1.0013`)
- Top 5 Correlación media (Actual vs `PoolDPP-MAP`): `0.325` -> `0.0694`
- Tasa de caída en bloque (>=3/5 fallos juntos): Actual `0.2917` vs DPP `0.125`
- Brier Top 5 Actual: `{'media': 0.214719, 'ic95_lo': 0.178206, 'ic95_hi': 0.252045, 'n_partidos': 48}` | Brier Top 5 DPP: `{'media': 0.20257, 'ic95_lo': 0.180991, 'ic95_hi': 0.226664, 'n_partidos': 48}`
- Tiempo medio de cómputo `GreedyMAP`: `0.884 ms`

## Cuadro 3 — Evaluación Distribucional `φTD` (Distancia de Cramér) y CVaR_25
- Lema 1 Goles (Poisson): exacto `0.423070` vs frecuencia Pareto `0.423109` (error `0.000039`)
- Lema 1 Córneres (NB): exacto `0.857644` vs frecuencia Pareto `0.857712` (error `0.000069`)
- Picks con `CVaR_25 >= 0.65`: `{'n_picks': 73, 'brier_medio': 0.092072, 'logloss_medio': 0.321761}`
- Picks con `CVaR_25 < 0.65`: `{'n_picks': 15, 'brier_medio': 0.136712, 'logloss_medio': 0.453712}`

## Cuadro 4 — Diagnóstico Topológico y Representation Probe
- Probe `AUC_sep` por familia: `{"1X2 (Local Gana)": {"objetivo": "Local Gana", "n_partidos": 83, "n_variables": 10, "auc_separacion": 0.495591, "tiene_poder_separador": false, "diagnostico": "espacio sin poder separador", "media_d_crit": 1.005823, "media_d_surf": 1.010189, "n_pares_opuestos": 1716, "n_pares_iguales": 1687}, "Doble Oportunidad (1X)": {"objetivo": "1X", "n_partidos": 83, "n_variables": 10, "auc_separacion": 0.5012, "tiene_poder_separador": false, "diagnostico": "espacio sin poder separador", "media_d_crit": 1.008983, "media_d_surf": 1.007114, "n_pares_opuestos": 1590, "n_pares_iguales": 1813}, "Goles FT (+2.5 FT)": {"objetivo": "+2.5 FT", "n_partidos": 83, "n_variables": 10, "auc_separacion": 0.546941, "tiene_poder_separador": true, "diagnostico": "espacio con poder separador", "media_d_crit": 1.045002, "media_d_surf": 0.975526, "n_pares_opuestos": 1590, "n_pares_iguales": 1813}, "Goles Mitades (+0.5 1T)": {"objetivo": "+0.5 1T", "n_partidos": 83, "n_variables": 10, "auc_separacion": 0.508892, "tiene_poder_separador": false, "diagnostico": "espacio sin poder separador", "media_d_crit": 1.014915, "media_d_surf": 1.003914, "n_pares_opuestos": 1260, "n_pares_iguales": 2143}, "Córneres FT (+8.5 Córn.)": {"objetivo": "+8.5 Córn.", "n_partidos": 83, "n_variables": 10, "auc_separacion": 0.648621, "tiene_poder_separador": true, "diagnostico": "espacio con poder separador", "media_d_crit": 1.123561, "media_d_surf": 0.897137, "n_pares_opuestos": 1666, "n_pares_iguales": 1737}, "Tarjetas FT (-4.5 Tarj.)": {"objetivo": "-4.5 Tarj.", "n_partidos": 83, "n_variables": 10, "auc_separacion": 0.597078, "tiene_poder_separador": true, "diagnostico": "espacio con poder separador", "media_d_crit": 1.093347, "media_d_surf": 0.947158, "n_pares_opuestos": 1416, "n_pares_iguales": 1987}}`
- Estratificación por `entrelazamiento_local`: `{"bajo_lt_050": {"n_partidos": 6, "brier_medio": 0.177985}, "medio_050_a_075": {"n_partidos": 14, "brier_medio": 0.216179}, "alto_ge_075": {"n_partidos": 28, "brier_medio": 0.211735}}`

## Cuadro 5 — Impacto de la Proyección Final Única `FAA`
- (i) Base sin FAA: Brier `{'media': 0.160511, 'ic95_lo': 0.141007, 'ic95_hi': 0.182921, 'n_partidos': 48}`, LogLoss `{'media': 0.490339, 'ic95_lo': 0.445522, 'ic95_hi': 0.541635, 'n_partidos': 48}`
- (ii) Núcleo + FAA: Brier `{'media': 0.160511, 'ic95_lo': 0.141007, 'ic95_hi': 0.182921, 'n_partidos': 48}`, $\Delta$Brier `{'media': 0.0, 'ic95_lo': 0.0, 'ic95_hi': 0.0, 'n_partidos': 48}`
- (iii) Núcleo + DPP L2 + FAA: Brier `{'media': 0.160424, 'ic95_lo': 0.141173, 'ic95_hi': 0.182609, 'n_partidos': 48}`, $\Delta$Brier `{'media': -8.7e-05, 'ic95_lo': -0.001996, 'ic95_hi': 0.001833, 'n_partidos': 48}`
- Violaciones de monotonicidad FAA: `0` | Violaciones simplex 1X2: `0`

## Cuadro 6 — Invariancia Total con Banderas Apagadas
- `todas_apagadas = True` | `invariancia_bit_a_bit_ok = True`
- $\Delta\lambda = 0.0$ | $\Delta\text{matriz} = 0.0$ | $\Delta\text{Top5} = 0.0$

## Cuadro 7 — Veredicto Honesto de Gobernanza
- Las 6 banderas de motor_autonomo_ronda19 permanecen en FALSE (Modo Sombra). La aduana BH-FDR (q < 0.05) y el bootstrap por partido confirman que en una ventana de 83 partidos históricos las conjunciones de orden alto deben mantenerse en observación hasta acumular >= 30 partidos prospectivos OOS en ledger_oos.jsonl.
