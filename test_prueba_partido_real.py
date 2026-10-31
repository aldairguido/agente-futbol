import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from playwright.sync_api import sync_playwright
import flashscore
import config
from analizador_experto import AnalizadorExperto

def probar_partido(local: str, visitante: str, liga: str):
    print(f"\n============================================================")
    print(f"  PRUEBA UNITARIA OFICIAL: {local} vs {visitante} ({liga})")
    print(f"============================================================")
    
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context()
        
        # 1. Búsqueda de equipos
        print(f"[1] Buscando equipos en Flashscore (con normalización y fuzzy matching)...")
        url_local = flashscore.buscar_equipo(ctx, local)
        url_visitante = flashscore.buscar_equipo(ctx, visitante)
        
        print(f"    -> Local '{local}': {url_local}")
        print(f"    -> Visitante '{visitante}': {url_visitante}")
        
        assert url_local is not None, f"Error: No se encontró URL para {local}"
        assert url_visitante is not None, f"Error: No se encontró URL para {visitante}"
        
        # 2. Muestra histórica de Local
        print(f"\n[2] Obteniendo historial de {local}...")
        gen_l = flashscore.obtener_resultados_equipo(ctx, url_local, config.MUESTRA_GENERAL, nombre_equipo=local)
        cond_l = flashscore.obtener_resultados_equipo(ctx, url_local, config.MUESTRA_CONDICION, condicion="casa", nombre_equipo=local)
        
        print(f"    -> Partidos generales obtenidos: {len(gen_l)}")
        print(f"    -> Partidos en condición (casa): {len(cond_l)}")
        if gen_l:
            p0 = gen_l[0]
            print(f"    -> Muestra partido 1: {p0.get('fecha')} | {p0.get('local')} vs {p0.get('visitante')} | Goles: {p0.get('goles_favor')}-{p0.get('goles_contra')} (Res: {p0.get('resultado')})")
            
        # 3. Muestra histórica de Visitante
        print(f"\n[3] Obteniendo historial de {visitante}...")
        gen_v = flashscore.obtener_resultados_equipo(ctx, url_visitante, config.MUESTRA_GENERAL, nombre_equipo=visitante)
        cond_v = flashscore.obtener_resultados_equipo(ctx, url_visitante, config.MUESTRA_CONDICION, condicion="fuera", nombre_equipo=visitante)
        
        print(f"    -> Partidos generales obtenidos: {len(gen_v)}")
        print(f"    -> Partidos en condición (fuera): {len(cond_v)}")
        if gen_v:
            p0 = gen_v[0]
            print(f"    -> Muestra partido 1: {p0.get('fecha')} | {p0.get('local')} vs {p0.get('visitante')} | Goles: {p0.get('goles_favor')}-{p0.get('goles_contra')} (Res: {p0.get('resultado')})")
            
        # 4. H2H oficial
        print(f"\n[4] Obteniendo H2H oficial entre {local} y {visitante}...")
        h2h = []
        url_partido_fs = flashscore.buscar_partido_h2h_url(ctx, url_local, visitante)
        if url_partido_fs:
            print(f"    -> Match fixture URL detectado: {url_partido_fs}")
            h2h = flashscore.obtener_h2h(ctx, url_partido_fs, local, visitante)
        if not h2h and gen_l:
            h2h = flashscore.extraer_h2h_desde_muestras(gen_l, visitante)
            
        print(f"    -> Partidos H2H oficiales extraídos: {len(h2h)}")
        for i, h in enumerate(h2h):
            print(f"       [{i+1}] {h.get('texto_raw', '').replace(chr(10), ' | ')[:120]}")
            
        b.close()
        
    # 5. Inyección en el Analizador Experto
    print(f"\n[5] Inyectando en Analizador Experto (Capa 1 y Capa 2)...")
    stats = {
        "local": {"general": gen_l, "condicion": cond_l},
        "visitante": {"general": gen_v, "condicion": cond_v},
        "h2h": h2h,
    }
    
    # Mercados de ejemplo de Betsson
    mercados_reales = [
        {"nombre": "Total de Goles", "seleccion": "más de 1.5", "cuota": 1.35, "disponible": True},
        {"nombre": "Ambos equipos anotan", "seleccion": "Sí", "cuota": 1.58, "disponible": True},
        {"nombre": "Ganador del partido", "seleccion": local, "cuota": 1.52, "disponible": True},
    ]
    
    analizador = AnalizadorExperto(cuota_minima=config.CUOTA_MINIMA)
    resultado = analizador.analizar(
        {"local": local, "visitante": visitante, "liga": liga},
        stats,
        mercados_reales
    )
    
    c1 = resultado["razonamiento_cognitivo"]["capa_1_h2h"]
    c2 = resultado["razonamiento_cognitivo"]["capa_2_contexto"]
    
    print("\n---------------- RESULTADOS DEL RAZONAMIENTO ----------------")
    print(f"CAPA 1 (H2H Oficial):")
    print(f"  • Total partidos oficiales: {c1.get('total_oficiales')}")
    print(f"  • Frecuencia Over 1.5:      {c1.get('over_1_5_frecuencia')} ({c1.get('over_1_5_conteo')})")
    print(f"  • Frecuencia BTTS:          {c1.get('btts_frecuencia')} ({c1.get('btts_conteo')})")
    print(f"  • Resumen:                  {c1.get('resumen')}")
    
    print(f"\nCAPA 2 (Contexto y Rendimiento):")
    print(f"  • {local} (Casa):")
    print(f"      - Victorias %:          {c2['local']['victorias_pct']:.0%}")
    print(f"      - GF promedio:          {c2['local']['goles_anotados_prom']}")
    print(f"      - GC promedio:          {c2['local']['goles_recibidos_prom']}")
    print(f"      - Over 1.5 %:           {c2['local']['over_1_5_pct']:.0%}")
    print(f"      - BTTS %:               {c2['local']['btts_pct']:.0%}")
    print(f"      - Jerarquía previa:     {c2['local']['ponderacion_rivales']}")
    print(f"  • {visitante} (Fuera):")
    print(f"      - Victorias %:          {c2['visitante']['victorias_pct']:.0%}")
    print(f"      - GF promedio:          {c2['visitante']['goles_anotados_prom']}")
    print(f"      - GC promedio:          {c2['visitante']['goles_recibidos_prom']}")
    print(f"      - Over 1.5 %:           {c2['visitante']['over_1_5_pct']:.0%}")
    print(f"      - BTTS %:               {c2['visitante']['btts_pct']:.0%}")
    print(f"      - Jerarquía previa:     {c2['visitante']['ponderacion_rivales']}")
    
    print(f"\nVEREDICTOS EMITIDOS:")
    print(f"  • Candidatos Verdes ({len(resultado['candidatos'])}):")
    for c in resultado["candidatos"]:
        print(f"      [VERDE] {c.get('mercado')} - {c.get('seleccion')} @ {c.get('cuota')} -> {c.get('motivo_semaforo')}")
    print(f"  • Descartados ({len(resultado['descartados'])}):")
    for d in resultado["descartados"]:
        print(f"      [ROJO] {d.get('seleccion')} @ {d.get('cuota')} -> {d.get('motivo_descarte')}")

if __name__ == "__main__":
    probar_partido("Atalanta", "Venezia", "Italia Serie A")
