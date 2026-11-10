"""
test_ucl_6_partidos.py
Verifica los 6 partidos de UCL tras las 5 correcciones matematicas.
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import servicio_mercados as servicio

partidos_ucl = [
    ("Barcelona", "Aston Villa"),
    ("Galatasaray", "VfB Stuttgart"),
    ("Shakhtar Donetsk", "Sporting CP"),
    ("Atlético Madrid", "Bayern München"),
    ("Bodø/Glimt", "Lille"),
    ("Feyenoord", "Inter"),
]


def ejecutar_verificacion_ucl_6() -> None:
    print("================================================================================", flush=True)
    print("INICIANDO VERIFICACION DE LOS 6 PARTIDOS DE CHAMPIONS LEAGUE", flush=True)
    print("================================================================================", flush=True)

    for eq_a, eq_b in partidos_ucl:
        print(f"\n>>> ANALIZANDO: {eq_a} vs {eq_b} (Champions League) ...", flush=True)
        try:
            prep = servicio.preparar_analisis(
                eq_a, eq_b,
                limite=15,
                umbral=0.70,
                forzar_datos=False,
                liga={"nombre": "Champions League"},
            )
            an = prep["analisis"]
            motor = getattr(an, "motor", None)
            meta = an.metadatos or {}
            rec = meta.get("red_neuronal", {})
            
            lam_b_l = rec.get("lambda_base_local", 0.0)
            lam_b_v = rec.get("lambda_base_visita", 0.0)
            lam_r_l = rec.get("lambda_local_recursivo", 0.0)
            lam_r_v = rec.get("lambda_visita_recursivo", 0.0)
            
            clusters = getattr(motor, "clusters", {}) or {}
            linea_pie = clusters.get("linea_pie", "")
            
            # P(Ambos Anotan)
            mat = getattr(motor, "matriz_ft", None)
            p_btts_si = 0.0
            p_btts_no = 0.0
            p_00 = 0.0
            if mat:
                R = len(mat)
                C = len(mat[0]) if R else 0
                p_btts_si = sum(mat[i][j] for i in range(1, R) for j in range(1, C)) * 100.0
                p_btts_no = 100.0 - p_btts_si
                p_00 = mat[0][0] * 100.0
                
            print(f"  [LAMBDAS] Base: {lam_b_l:.2f} - {lam_b_v:.2f}  -->  Red Recursiva: {lam_r_l:.2f} - {lam_r_v:.2f}", flush=True)
            print(f"  [PROBS] P(Ambos Anotan Sí): {p_btts_si:.1f}% | P(Ambos Anotan No): {p_btts_no:.1f}% | P(0-0): {p_00:.1f}%", flush=True)
            print(f"  [CLUSTERS / JUGADA OPTIMA]\n    {linea_pie}", flush=True)
            
            top5 = clusters.get("top5_podio") or []
            if not top5 and hasattr(motor, "selecciones_publicadas"):
                import motor_mercados as mm
                top5 = mm.seleccionar_top_5_jugadas_recomendadas(motor.mercados, motor.nombre_local, motor.nombre_visitante, motor=motor)
            print("  [TOP 5 RECOMENDADAS]:", flush=True)
            for jug in top5:
                print(f"    #{jug.get('rank')} [{jug.get('categoria_podio')}]: {jug.get('etiqueta')} ({jug.get('probabilidad_pct')})", flush=True)
                print(f"       -> {jug.get('respaldo')}", flush=True)

            if prep.get("ruta_cuadro"):
                print(f"  [IMAGEN CUADRO]: {prep['ruta_cuadro']}", flush=True)
            if prep.get("ruta_ticket_valor"):
                print(f"  [IMAGEN TICKET]: {prep['ruta_ticket_valor']}", flush=True)
                
        except Exception as e:
            print(f"  ERROR en {eq_a} vs {eq_b}: {e}", flush=True)
            import traceback
            traceback.print_exc()

    print("\n================================================================================")
    print("VERIFICACION COMPLETADA")
    print("================================================================================")


if __name__ == "__main__":
    ejecutar_verificacion_ucl_6()
