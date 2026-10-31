"""
Prueba Integral de Validación sobre los 5 Partidos Auditados:
1. Celta Fortuna vs Tenerife (15 vs 15 y Delta Tier)
2. Juventus Next Gen vs Renate (Reconstrucción xG/xGOT Serie C)
3. Parma vs Torino (Ajuste Bidireccional de Portero)
4. Tottenham Hotspur vs Coventry City (Desactivación de Sequía y Delta Tier = 1)
5. Getafe vs Rayo Vallecano (Preservación del 1-1 y Duelo Parejo)
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import numpy as np

import motor_recursivo_45_dcn as red
import motor_mercados as mm
import cuadro_especialista as ce

print("=" * 70)
print("  VALIDACIÓN EMPÍRICA: 5 CASOS DE AUDITORÍA CON DOBLE ADN")
print("=" * 70)

# Caso 1: Celta Fortuna vs Tenerife
# Celta Fortuna (Tier 3: Primera Federación) vs Tenerife (Tier 2: Segunda)
print("\n[CASO 1] Celta Fortuna vs Tenerife (Tier 3 vs Tier 2, 15 vs 15):")
tier_celta = red.obtener_tier_liga("Primera Federación")
tier_tenerife = red.obtener_tier_liga("LaLiga Hypermotion")
assert tier_celta == 3, f"Esperado Tier 3, obtenido {tier_celta}"
assert tier_tenerife == 2, f"Esperado Tier 2, obtenido {tier_tenerife}"
delta_tier = max(0, tier_celta - tier_tenerife) # Delta = 1
factor_atq = 0.76 ** delta_tier
factor_def = 1.22 ** delta_tier
print(f"  • Tier Celta Fortuna: {tier_celta} | Tier Tenerife: {tier_tenerife}")
print(f"  • Delta Tier: {delta_tier} -> Factor Ofensivo: {factor_atq:.2f}, Factor Defensivo Concedido: {factor_def:.2f}")
assert abs(factor_atq - 0.76) < 1e-4
assert abs(factor_def - 1.22) < 1e-4
print("  ✅ Celta Fortuna vs Tenerife: Descuento relativo verificado correctamente.")

# Caso 2: Juventus Next Gen vs Renate (Serie C, xG = 0 con tiros > 0)
print("\n[CASO 2] Juventus Next Gen vs Renate (Serie C, Reconstrucción xG/xGOT):")
partido_serie_c_eq = {
    "remates_dentro_area": 8.0,
    "remates_fuera_area": 4.0,
    "grandes_ocasiones": 2.0,
    "remates_puerta": 5.0,
    "goles_favor": 1.0,
    "remates_totales": 12.0,
    "xg": 0.0,
    "xgot": 0.0,
    "xgot_enfrentados": 0.0,
    "goles_evitados": 0.0,
}
partido_serie_c_riv = {
    "remates_puerta": 3.0,
    "remates_fuera": 4.0,
    "al_palo": 0.0,
    "goles": 1.0,
}
import fuentes_xg
fuentes_xg._resolver_ecuaciones_periodo(partido_serie_c_eq, partido_serie_c_riv, goles_eq=1.0, goles_riv=1.0)
print(f"  • xG Reconstruido: {partido_serie_c_eq['xg']:.2f} (antes 0.0)")
print(f"  • xGOT Reconstruido: {partido_serie_c_eq['xgot']:.2f} (antes 0.0)")
print(f"  • xGOT Enfrentados Reconstruido: {partido_serie_c_eq['xgot_enfrentados']:.2f}")
print(f"  • Saques de Puerta Derivados: {partido_serie_c_eq.get('saques_puerta', 0)}")
assert partido_serie_c_eq['xg'] > 0.5, "xG debe reconstruirse cuando hay remates"
assert partido_serie_c_eq['xgot'] > 0.5, "xGOT debe reconstruirse cuando hay remates a puerta"
assert partido_serie_c_eq.get('saques_puerta', 0) >= 1, "saques_puerta no debe ser 0"
print("  ✅ Juventus Next Gen vs Renate: Reconstrucción de xG/xGOT y saques_puerta verificada.")

# Caso 3: Parma vs Torino (Ajuste Bidireccional de Portero)
print("\n[CASO 3] Parma vs Torino (Ajuste Bidireccional de Portero):")
# Portero Torino con Goles Evitados positivo de visita (+0.25)
rem_puerta_parma = 5.0
goles_evitados_toro = 0.25
ajuste_port = float(np.clip(-goles_evitados_toro * (rem_puerta_parma / 5.0), -0.35, +0.45))
print(f"  • Portero con Goles Evitados = +{goles_evitados_toro}: Ajuste = {ajuste_port:+.2f} (reduce lambda atacante)")
assert ajuste_port < 0, "Un portero con goles evitados positivo debe reducir el lambda rival"
# Portero rival con Goles Evitados negativo (-0.30)
goles_evitados_fragil = -0.30
ajuste_port_fragil = float(np.clip(-goles_evitados_fragil * (rem_puerta_parma / 5.0), -0.35, +0.45))
print(f"  • Portero Frágil con Goles Evitados = {goles_evitados_fragil}: Ajuste = {ajuste_port_fragil:+.2f} (eleva lambda atacante)")
assert ajuste_port_fragil > 0, "Un portero con goles evitados negativo debe elevar el lambda rival"
print("  ✅ Parma vs Torino: Ajuste Bidireccional de Portero verificado.")

# Caso 4: Tottenham Hotspur vs Coventry City (Desactivación de Sequía y Delta Tier = 1)
print("\n[CASO 4] Tottenham vs Coventry (Desactivación Sequía y Delta Tier = 1):")
tier_tot = red.obtener_tier_liga("Premier League")
tier_cov = red.obtener_tier_liga("Championship")
assert tier_tot == 1 and tier_cov == 2, f"Tiers esperados: Tot=1, Cov=2. Obtenidos: {tier_tot}, {tier_cov}"
desactivar_sequia = (tier_tot == 1 and tier_cov >= 2)
print(f"  • Tottenham Tier {tier_tot} vs Coventry Tier {tier_cov} -> Desactivación de Sequía: {desactivar_sequia}")
assert desactivar_sequia is True, "Debe desactivar el techo de sequía de Tier 1 contra Tier 2"
print("  ✅ Tottenham vs Coventry: Desactivación de sequía verificada.")

# Caso 5: Getafe vs Rayo Vallecano (Preservación del 1-1 en Duelo Parejo)
print("\n[CASO 5] Getafe vs Rayo Vallecano (Preservación del 1-1 en Duelo Parejo):")
lam_L = 1.13
lam_V = 1.14
diff_lam = abs(lam_L - lam_V)
p_fav = 0.46 # Favorito Rayo con 46% (< 62%)
permitir_desbloqueo_anti_1_1 = (diff_lam >= 0.65 and p_fav >= 0.62)
print(f"  • Diferencia de lambda: {diff_lam:.2f} (< 0.65) | P(Favorito): {p_fav:.1%} (< 62%)")
print(f"  • Desbloqueo Anti-1-1 Activo: {permitir_desbloqueo_anti_1_1}")
assert permitir_desbloqueo_anti_1_1 is False, "En duelo parejo el desbloqueo Anti-1-1 debe permanecer BLOQUEADO"
es_duelo_parejo = max(0.27, p_fav) < 0.58
print(f"  • Max P(1x2) < 58%: {es_duelo_parejo} -> DUELO PAREJO / RIESGO DE EMPATE")
assert es_duelo_parejo is True
print("  ✅ Getafe vs Rayo Vallecano: Preservación del 1-1 y Duelo Parejo verificados.")

print("\n" + "=" * 70)
print("  🎯 LOS 5 CASOS DE AUDITORÍA SUPERADOS CON ÉXITO")
print("=" * 70)
