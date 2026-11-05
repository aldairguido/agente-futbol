import sys
sys.stdout.reconfigure(encoding='utf-8')

from motor_recursivo_45_dcn import (
    obtener_tier_coef,
    obtener_tier_coef_liga,
    ejecutar_bucle_recursivo_infinito_hasta_convergencia,
)
from cuadro_especialista import (
    construir_perfil_especialista_equipo,
    _organizar_sec1_para_cuadro,
)
from motor_mercados import (
    seleccionar_top_5_jugadas_recomendadas,
    es_seleccion_competitiva_ticket,
    filtrar_top_opciones_ticket,
)

print("="*60)
print("TEST 1: TIER COEFICIENTS Y DESCUENTO LIGA HÚNGARA")
print("="*60)

t_nbi = obtener_tier_coef_liga("NB I")
t_otp = obtener_tier_coef_liga("OTP Bank Liga")
t_hung = obtener_tier_coef_liga("Hungary Nemzeti Bajnokság")
print(f"Tier NB I: {t_nbi} (esperado 0.65)")
print(f"Tier OTP Bank: {t_otp} (esperado 0.65)")
assert t_nbi == 0.65, f"Expected 0.65 for NB I, got {t_nbi}"
assert t_otp == 0.65, f"Expected 0.65 for OTP Bank Liga, got {t_otp}"

t_milan = obtener_tier_coef(nombre_equipo="AC Milan", competicion_str="UEFA Champions League")
t_fradi = obtener_tier_coef(nombre_equipo="Ferencváros", competicion_str="UEFA Champions League")
print(f"Tier Milan (en UCL): {t_milan} (esperado 1.00)")
print(f"Tier Ferencváros (en UCL): {t_fradi} (esperado 0.65)")
assert t_milan == 1.00, f"Expected 1.00 for Milan, got {t_milan}"
assert t_fradi == 0.65, f"Expected 0.65 for Ferencváros, got {t_fradi}"

print("\n" + "="*60)
print("TEST 2: RATINGS 0-100 CON RATIO_TIER")
print("="*60)

# Muestra simulada donde Ferencváros tiene números domésticos inflados (0.75 Gc, 8100s/gol)
bloque_fradi = {
    "equipo": "Ferencváros",
    "contexto": "global",
    "metricas": {
        "xg": 1.45,
        "xgot": 1.50,
        "remates_totales": 13.0,
        "remates_puerta": 4.5,
        "goles_favor": 1.60,
        "goles_contra": 0.75,
        "xgot_enfrentado": 0.85,
        "goles_evitados": 0.15,
        "paradas": 3.0,
        "toques_area_rival": 22.0,
        "posesion_pct": 52.0,
        "despejes": 18.0,
        "intercepciones": 8.0,
        "recuperaciones": 48.0,
    }
}
bloque_milan = {
    "equipo": "AC Milan",
    "contexto": "global",
    "metricas": {
        "xg": 1.85,
        "xgot": 1.80,
        "remates_totales": 16.0,
        "remates_puerta": 5.5,
        "goles_favor": 1.80,
        "goles_contra": 1.10,
        "xgot_enfrentado": 1.15,
        "goles_evitados": 0.05,
        "paradas": 3.0,
        "toques_area_rival": 28.0,
        "posesion_pct": 56.0,
        "despejes": 16.0,
        "intercepciones": 9.0,
        "recuperaciones": 50.0,
    }
}

ratio_milan = 1.00
ratio_fradi = 0.65 / 1.00 # 0.65

perfil_milan = construir_perfil_especialista_equipo(bloque_milan, ratio_tier=ratio_milan)
perfil_fradi = construir_perfil_especialista_equipo(bloque_fradi, ratio_tier=ratio_fradi)

ov_milan = perfil_milan["ratings_0_100"]["Overall_Hybrid_Rating"]
ov_fradi = perfil_fradi["ratings_0_100"]["Overall_Hybrid_Rating"]
def_milan = perfil_milan["ratings_0_100"]["Final_Defense_Strength"]
def_fradi = perfil_fradi["ratings_0_100"]["Final_Defense_Strength"]

print(f"Overall Milan: {ov_milan} vs Ferencváros: {ov_fradi}")
print(f"Defense Milan: {def_milan} vs Ferencváros: {def_fradi}")
print(f"Seg/Gol Concedido Ferencváros ajustado: {perfil_fradi['tempo_segundos']['Avg_Seconds_Between_Goals_Conceded']} s (crudo era 7200 s)")

assert ov_milan > ov_fradi, f"Milan Overall ({ov_milan}) must be > Ferencváros ({ov_fradi})"
assert def_milan > def_fradi, f"Milan Defense ({def_milan}) must be > Ferencváros ({def_fradi})"
assert perfil_fradi['tempo_segundos']['Avg_Seconds_Between_Goals_Conceded'] < 6000, "Ferencváros seg/gol concedido must be discounted"

print("\n" + "="*60)
print("TEST 3: LAMBDA RED RECURSIVA (MILAN VS FERENCVÁROS)")
print("="*60)

res_red = ejecutar_bucle_recursivo_infinito_hasta_convergencia(
    perfil_local=perfil_milan,
    perfil_visita=perfil_fradi,
    lam_base_local=1.85,
    lam_base_visita=0.95,
    partidos_15_local=[],
    partidos_15_visita=[],
    n_simulaciones_mc=1000,
)
lam_red_milan = res_red["lambda_local_recursivo"]
lam_red_fradi = res_red["lambda_visita_recursivo"]

print(f"λ Red Milan: {lam_red_milan} vs Ferencváros: {lam_red_fradi}")
assert lam_red_milan > lam_red_fradi, f"Milan lambda ({lam_red_milan}) must be > Ferencváros ({lam_red_fradi})"
assert lam_red_milan >= 1.65, f"Milan should have robust attacking lambda >= 1.65 (got {lam_red_milan})"

print("\n" + "="*60)
print("TEST 4: SECCIÓN 1 DEL SÚPER-CUADRO (GATEKEEPER Y 5 CATEGORÍAS)")
print("="*60)

# Simulamos selecciones donde hay -6.5 y -5.5 (líneas basura) y selecciones competitivas
sels_simuladas = [
    {"mercado": "total_goles", "evento": "under_6_5", "linea": 6.5, "probabilidad": 1.00, "estado": "mantiene"},
    {"mercado": "total_goles", "evento": "under_5_5", "linea": 5.5, "probabilidad": 0.98, "estado": "mantiene"},
    {"mercado": "total_goles", "evento": "over_1_5", "linea": 1.5, "probabilidad": 0.82, "estado": "mantiene"},
    {"mercado": "total_goles", "evento": "under_3_5", "linea": 3.5, "probabilidad": 0.76, "estado": "mantiene"},
    {"mercado": "goles_equipo", "evento": "over_1_5", "linea": 1.5, "equipo": "AC Milan", "probabilidad": 0.74, "estado": "mantiene"},
    {"mercado": "corners", "evento": "over_8_5", "linea": 8.5, "probabilidad": 0.77, "estado": "mantiene"},
    {"mercado": "tarjetas", "evento": "over_2_5", "linea": 2.5, "probabilidad": 0.81, "estado": "mantiene"},
    {"mercado": "doble_oportunidad", "evento": "1x", "probabilidad": 0.88, "estado": "mantiene"},
    {"mercado": "ganador", "evento": "1", "probabilidad": 0.72, "estado": "mantiene"},
]

sec1_org = _organizar_sec1_para_cuadro(None, sels_simuladas, "AC Milan", "Ferencváros")
categorias_en_sec1 = [c["categoria"] for c in sec1_org]
pildoras_labels = [p["lbl"] for c in sec1_org for p in c["pildoras"]]

print("Categorías en Sección 1 del Cuadro:")
for c in sec1_org:
    pills_str = ", ".join(f"{p['lbl']} ({p['pct']})" for p in c['pildoras'])
    print(f"  • {c['categoria']} | {c['ambito']} -> [{pills_str}]")

assert "-6.5 Goles" not in pildoras_labels, "ERROR: -6.5 Goles MUST NOT be in Sección 1"
assert "-5.5 Goles" not in pildoras_labels, "ERROR: -5.5 Goles MUST NOT be in Sección 1"
assert "GOLES PARTIDO" in categorias_en_sec1, "ERROR: GOLES PARTIDO missing"
assert "GOLES POR EQUIPO" in categorias_en_sec1, "ERROR: GOLES POR EQUIPO missing"
assert "CÓRNERES" in categorias_en_sec1, "ERROR: CÓRNERES missing"
assert "TARJETAS" in categorias_en_sec1, "ERROR: TARJETAS missing"
assert "HÁNDICAP / 1X2 / DC" in categorias_en_sec1, "ERROR: HÁNDICAP / 1X2 / DC missing"

print("\n" + "="*60)
print("TEST 5: PANTEÓN DE CRUCES XAI EN TOP 5 PODIO")
print("="*60)

class MotorMock:
    def __init__(self):
        self.matriz_ft = [[0.05]*5 for _ in range(5)]
        self.matriz_ft[2][0] = 0.25 # Milan 2-0
        self.matriz_ft[2][1] = 0.15 # Milan 2-1
        self.xai_factores = {
            "mu_local_casa": {
                "goles_favor": 1.80, "xg": 1.85, "xgot": 1.90, "toques_area_rival": 28.0,
                "despejes": 16.0, "corners": 5.8, "cruces_pct": 24.0, "remates_dentro_area": 8.5,
                "bloqueos": 2.2, "faltas": 11.0, "duelos_suelo_pct": 52.0, "pases_entre_lineas": 26.0,
                "entradas_pct": 15.0, "goles_evitados": 0.05, "pases_total": 450.0
            },
            "mu_visita_fuera": {
                "goles_favor": 0.95, "xg": 0.90, "xgot": 0.85, "toques_area_rival": 14.0,
                "despejes": 22.0, "corners": 3.8, "cruces_pct": 16.0, "remates_dentro_area": 5.0,
                "bloqueos": 3.2, "faltas": 13.5, "duelos_suelo_pct": 48.0, "pases_entre_lineas": 18.0,
                "entradas_pct": 13.0, "goles_evitados": -0.10, "pases_total": 360.0
            }
        }
        self.recursivo = {
            "lambda_local_recursivo": 1.82,
            "lambda_visita_recursivo": 0.78,
            "mu_local_casa": self.xai_factores["mu_local_casa"],
            "mu_visita_fuera": self.xai_factores["mu_visita_fuera"]
        }
    def _mu_corners(self, lado):
        return 5.8 if lado == "local" else 3.8
    def _mu_tarjetas(self, tipo):
        return 4.2

top5 = seleccionar_top_5_jugadas_recomendadas(sels_simuladas, motor=MotorMock(), eq_l="AC Milan", eq_v="Ferencváros")
print(f"Top 5 selecciones encontradas: {len(top5)}")
for jug in top5:
    print(f"  #{jug['rank']} [{jug['categoria_podio']}] {jug['etiqueta']} ({jug['probabilidad_pct']})")
    print(f"     -> {jug['respaldo_xai']}")

respaldos = " ".join(j["respaldo_xai"] for j in top5)
assert "Cruce Letalidad" in respaldos or "Cruce Asedio" in respaldos, "ERROR: Cruce Letalidad o Asedio missing in Top 5"
assert "Cruce [" in respaldos, "ERROR: Panteón de Cruces syntax [Var A ⊗ Var B] missing in Top 5"

print("\n" + "="*60)
print("¡TODOS LOS TESTS DE AUDITORÍA PASARON SATISFACTORIAMENTE!")
print("="*60)
