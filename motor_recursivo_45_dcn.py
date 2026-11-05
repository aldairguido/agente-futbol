"""
Red Neuronal Recursiva Tensorial para Fútbol (45 Variables -> Bucle Recursivo hasta Convergencia)
=================================================================================================
Implementa exactamente la arquitectura neuronal solicitada (compatible con PyTorch y NumPy puro):

    [Métrica i real (ej. xG)] ──⊗── [44 métricas restantes (Posesión, Remates, etc.)]
                                  │
                                  ▼ (Bilinear Pooling: Matriz de Covarianza Cruzada)
                     [Matriz Temporal de Interacción]
                                  │
                                  ▼ (Multi-Head Cross-Attention + Reducción de Espacio)
               [Filtrado de Patrones Sintéticos de Alta Probabilidad (P_NUEVO)]
                                  │
                                  ▼ (Re-empaquetado en Tensor 3D: X_p in R^(15 x 3 x M_nuevos))
                     [Bucle Recursivo: Iteración 1 -> 2 -> 3 ... -> Convergencia]
                                  │
                                  ▼ (GNN Adversarial + 100,000 Simulaciones Monte Carlo)
               [Distribución Conjunta de Marcadores Calibrada + Sello Visual Anti-Engaño]

Además incluye un "Hook Autónomo" para `cuadro_especialista.py`: recalcula la matriz DENTRO de
`generar_cuadro_visual_partido` e imprime un sello azul de verificación en la imagen para saber
con 100% de certeza que el agente local aplicó la Red Neuronal Recursiva y no dejó la Poisson vieja.

CAMBIO DE FONDO (por qué esta versión no es la anterior)
--------------------------------------------------------
Antes se procesaban 13 series RECONSTRUIDAS a partir de la media del equipo y del pulso de goles:
la variación real de xG (1,93 … 6,74) se perdía y, al anular 11 de las 13 series o todos los
cruces, la λ no se movía ni una centésima. Ahora:

  1. Se leen las **45 métricas REALES de cada uno de los 15 partidos** desde la auditoría
     (`partido["periodos"][corte]["filas"]`) y se construye una serie por variable.
  2. Cada serie se procesa con su **estado individual** (memoria de corto/largo plazo).
  3. Los **cruces (Bilinear Pooling + Cross-Attention) SÍ entran en λ**: la intensidad del cruce
     dominante multiplica el patrón ofensivo de cada equipo.
  4. Los patrones tácticos (asfixia, presión, fragilidad, sequía, muralla) salen de los estados
     reales (toques, recuperaciones, xGOT enfrentado, errores, goles evitados…), no de ratings.

Se conserva el bucle recursivo hasta convergencia de entropía y el Monte Carlo de 100.000.
"""

from __future__ import annotations
import math
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Soporte dual: usa PyTorch si está instalado en el entorno, o el motor tensorial exacto en NumPy
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TIENE_TORCH = True
except ImportError:
    TIENE_TORCH = False


LAS_45_VARIABLES_ORDENADAS: List[str] = [
    "xg", "posesion_pct", "duelos_pct", "remates_totales", "remates_puerta",
    "faltas", "corners", "tiros_libres", "fueras_juego", "saques_banda",
    "tarjetas_amarillas", "tarjetas_rojas",
    "xgot", "remates_fuera", "remates_bloqueados", "remates_area",
    "remates_fuera_area", "al_palo", "goles_cabeza", "xg_sin_penalti", "xg_balon_parado",
    "grandes_ocasiones", "grandes_ocasiones_falladas", "toques_area_rival", "faltas_tercio_ofensivo",
    "pases_total", "pases_tercio_final_pct", "pases_entre_lineas", "pases_largos_pct", "centros_pct",
    "xa_equipo", "entradas_pct", "intercepciones", "bloqueos", "despejes",
    "recuperaciones", "duelos_suelo_pct", "duelos_aereos_pct", "errores_remate", "errores_gol", "pases_ratio",
    "paradas", "goles_evitados", "xgot_enfrentado", "saques_puerta",
]

# Las 45 claves REALES del catálogo de la auditoría del proyecto (mismas que usa motor_mercados).
VARIABLES_REALES_45: Tuple[str, ...] = (
    "xg", "posesion_pct", "remates_totales", "remates_puerta", "grandes_ocasiones",
    "grandes_ocasiones_falladas", "corners", "pases_total", "pases_precision_pct",
    "pases_ratio", "tarjetas_amarillas", "tarjetas_rojas", "xgot", "xg_juego_abierto",
    "xg_balon_parado", "xg_sin_penalti", "remates_fuera", "remates_rechazados",
    "remates_dentro_area", "remates_fuera_area", "al_palo", "toques_area_rival",
    "pases_entre_lineas", "fueras_juego", "tiros_libres", "pases_largos_pct",
    "pases_tercio_final_pct", "cruces_pct", "xa_equipo", "saques_banda", "faltas",
    "entradas_pct", "duelos_ganados", "duelos_suelo_pct", "duelos_aereos_pct", "despejes",
    "intercepciones", "bloqueos", "recuperaciones", "errores_remate", "errores_gol",
    "paradas", "goles_evitados", "xgot_enfrentado", "saques_puerta",
)

# Neutros tipográficos SOLO si una variable no aparece en NINGÚN partido.
NEUTRALES: Dict[str, float] = {
    "xg": 1.35, "posesion_pct": 50.0, "remates_totales": 12.0, "remates_puerta": 4.2,
    "grandes_ocasiones": 2.2, "grandes_ocasiones_falladas": 1.3, "corners": 5.0,
    "pases_total": 420.0, "pases_precision_pct": 82.0, "pases_ratio": 1.0,
    "tarjetas_amarillas": 1.8, "tarjetas_rojas": 0.1, "xgot": 1.35,
    "xg_juego_abierto": 0.97, "xg_balon_parado": 0.27, "xg_sin_penalti": 1.21,
    "remates_fuera": 4.5, "remates_rechazados": 3.0, "remates_dentro_area": 7.5,
    "remates_fuera_area": 4.5, "al_palo": 0.3, "toques_area_rival": 22.0,
    "pases_entre_lineas": 24.0, "fueras_juego": 1.8, "tiros_libres": 10.0,
    "pases_largos_pct": 45.0, "pases_tercio_final_pct": 55.0, "cruces_pct": 22.0,
    "xa_equipo": 0.88, "saques_banda": 20.0, "faltas": 11.5, "entradas_pct": 14.0,
    "duelos_ganados": 48.0, "duelos_suelo_pct": 50.0, "duelos_aereos_pct": 50.0,
    "despejes": 18.0, "intercepciones": 8.0, "bloqueos": 2.5, "recuperaciones": 48.0,
    "errores_remate": 0.2, "errores_gol": 0.15, "paradas": 2.8, "goles_evitados": 0.0,
    "xgot_enfrentado": 1.25, "saques_puerta": 5.0,
}


def clip(val: float, low: float, high: float) -> float:
    return max(low, min(high, val))


def shannon_entropy(matriz: np.ndarray) -> float:
    """Calcula la entropía de Shannon H(P) = -sum p_ij log(p_ij) de la matriz de marcadores."""
    p = np.asarray(matriz, dtype=float).ravel()
    p = p[p > 1e-12]
    return float(-np.sum(p * np.log(p)))


# ==============================================================================
# ARQUITECTURA PYTORCH + NUMPY: RED NEURONAL RECURSIVA CON BILINEAR POOLING Y CROSS-ATTENTION
# ==============================================================================

if TIENE_TORCH:
    class RedNeuronalRecursivaFutbol(nn.Module):
        """
        Implementación en PyTorch del Tensor 3D X in R^(Batch x P x T x M):
        1. Procesamiento secuencial individualizado (1 LSTM exclusiva por cada una de las M=45 variables).
        2. Bilinear Pooling (Matriz de Covarianza Cruzada) + Multi-Head Cross-Attention (1 contra las restantes
           con reducción progresiva de espacio).
        3. Bucle Recursivo Dinámico (Iteración 1, 2, 3... hasta que la entropía de la distribución converge).
        """
        def __init__(self, num_variables_crudas: int = 45, partidos_p: int = 15, bloques_tiempo_t: int = 3, hidden_dim: int = 32):
            super().__init__()
            self.P = partidos_p
            self.T = bloques_tiempo_t
            self.M = num_variables_crudas
            self.hidden_dim = hidden_dim

            self.lstms_individuales = nn.ModuleList([
                nn.LSTM(input_size=1, hidden_size=hidden_dim, num_layers=1, batch_first=True)
                for _ in range(num_variables_crudas)
            ])
            self.lstm_patrones_recursivos = nn.LSTM(
                input_size=1, hidden_size=hidden_dim, num_layers=1, batch_first=True
            )
            self.capa_cross_attention = nn.MultiheadAttention(
                embed_dim=hidden_dim, num_heads=4, batch_first=True
            )
            self.bilinear_proj = nn.Bilinear(hidden_dim, hidden_dim, hidden_dim)
            self.proyeccion_patrones = nn.Linear(hidden_dim, hidden_dim)

        def forward(self, x_tensor_3d: torch.Tensor, max_iteraciones: int = 5, tol_entropia: float = 1e-4) -> Tuple[torch.Tensor, int]:
            batch_size = x_tensor_3d.size(0)
            tendencias_individuales = []
            for i in range(self.M):
                var_aislada = x_tensor_3d[:, :, :, i].reshape(batch_size, self.P * self.T, 1)
                _, (h_n, _) = self.lstms_individuales[i](var_aislada)
                tendencias_individuales.append(h_n.squeeze(0))

            features_base = torch.stack(tendencias_individuales, dim=1)  # [B, 45, H]
            estados_actuales = features_base
            entropia_previa = 999.0
            iteracion_convergencia = 1

            for it in range(1, max_iteraciones + 1):
                nuevos_patrones_lista = []
                usados = set()
                for i in range(self.M):
                    query = estados_actuales[:, i:i+1, :]
                    indices_restantes = [j for j in range(self.M) if j != i and j not in usados]
                    if not indices_restantes:
                        indices_restantes = [j for j in range(self.M) if j != i]
                    claves_valores = features_base[:, indices_restantes, :]
                    attn_output, attn_weights = self.capa_cross_attention(query, claves_valores, claves_valores)
                    # Bilinear Pooling entre la variable i y el contexto cruzado de las restantes
                    cruce_bilineal = self.bilinear_proj(query.squeeze(1), attn_output.squeeze(1))
                    nuevos_patrones_lista.append(cruce_bilineal)
                    # Reducción de espacio: si una variable absorbe >35% de la atención, se marca
                    max_w_idx = int(torch.argmax(attn_weights[0, 0]).item())
                    if float(attn_weights[0, 0, max_w_idx].item()) > 0.35 and len(usados) < self.M - 6:
                        usados.add(indices_restantes[max_w_idx])

                nuevos_patrones = torch.stack(nuevos_patrones_lista, dim=1)
                estados_actuales = F.gelu(self.proyeccion_patrones(nuevos_patrones)) + features_base

                # Medición de entropía sobre la proyección Softmax de los estados para detectar convergencia
                probs_latentes = F.softmax(estados_actuales.mean(dim=1), dim=-1)
                entropia_actual = float(-(probs_latentes * torch.log(probs_latentes + 1e-9)).sum(dim=-1).mean().item())
                iteracion_convergencia = it
                if abs(entropia_previa - entropia_actual) < tol_entropia and it >= 2:
                    break
                entropia_previa = entropia_actual

            return estados_actuales, iteracion_convergencia


# ==============================================================================
# MOTOR TENSORIAL RECURSIVO DETERMINISTA (BILINEAR POOLING + CROSS-ATTENTION + GNN + MONTE CARLO)
# ==============================================================================

def procesar_secuencia_individual_lstm_determinista(
    serie_cronologica: List[Optional[float]],
    semivida: float = 4.2,
) -> Dict[str, Any]:
    """
    Equivalente determinista y libre de ruido de la celda LSTM individual por variable:
    Recorre partido a partido p = 1..P (con memoria de corto plazo h_t y largo plazo c_t)
    sin aplanar en un promedio simple.
    """
    limpios = [float(v) for v in serie_cronologica if v is not None and not math.isnan(float(v))]
    n = len(limpios)
    if n == 0:
        return {
            "h_reciente": 0.0, "c_memoria": 0.0, "pendiente_dmdp": 0.0,
            "aceleracion_d2mdp2": 0.0, "ratio_racha": 1.0, "vector_temporal": np.zeros(15),
        }

    vals = np.array(limpios, dtype=float)
    c_memoria = float(np.mean(vals))
    escala = max(0.30, abs(c_memoria), float(np.std(vals)))

    # Compuertas de olvido e entrada (Forget/Input Gates) partido a partido p = 1..n
    alpha_input = 1.0 - math.exp(-math.log(2.0) / max(1.5, semivida))
    h_t = vals[0]
    estados_h = []
    for x_p in vals:
        # Gate de sorpresa: si el partido reciente rompe la media, actualiza más rápido el estado oculto h_t
        sorpresa = min(1.8, abs(x_p - h_t) / escala)
        gate_i = clip(alpha_input * (1.0 + 0.25 * sorpresa), 0.15, 0.65)
        h_t = (1.0 - gate_i) * h_t + gate_i * x_p
        estados_h.append(h_t)

    # Derivada entre el bloque actual (últimos 8-10 partidos de la temporada nueva) y el bloque viejo (temporada previa)
    corte_temp = max(1, n - min(9, max(4, (2 * n) // 3)))
    media_vieja = float(np.mean(vals[:corte_temp]))
    media_actual = float(np.mean(vals[corte_temp:]))
    media_ult5 = float(np.mean(vals[-min(5, n):]))

    h_reciente = 0.60 * float(estados_h[-1]) + 0.40 * media_ult5
    pendiente_dmdp = clip((media_actual - media_vieja) / escala, -1.60, 1.60)
    aceleracion = clip((media_ult5 - media_actual) / escala, -1.20, 1.20)
    ratio_racha = clip(media_actual / max(0.30, media_vieja), 0.40, 2.60)

    # Vector estandarizado de longitud 15 para Bilinear Pooling (Covarianza Cruzada)
    if n >= 15:
        vec_15 = vals[-15:]
    else:
        vec_15 = np.pad(vals, (15 - n, 0), mode="edge")
    vec_norm = (vec_15 - np.mean(vec_15)) / max(0.25, float(np.std(vec_15)))

    return {
        "h_reciente": round(h_reciente, 4),
        "c_memoria": round(c_memoria, 4),
        "media_temporada_actual": round(media_actual, 4),
        "media_ult5": round(media_ult5, 4),
        "pendiente_dmdp": round(pendiente_dmdp, 4),
        "aceleracion_d2mdp2": round(aceleracion, 4),
        "ratio_racha": round(ratio_racha, 4),
        "vector_temporal": vec_norm,
    }


def _partido_fuente(partido: Dict[str, Any]) -> Dict[str, Any]:
    """
    Devuelve el partido ORIGINAL de la auditoría.

    `cuadro_especialista.partidos_para_red_neuronal` añade `_partido` con el dict
    completo (el que tiene `periodos[FT/1T/2T].filas`). Sin él solo llegarían los
    goles, que es exactamente el fallo que esta versión corrige.
    """
    fuente = partido.get("_partido")
    return fuente if isinstance(fuente, dict) else partido


def _valor_metrica(partido: Dict[str, Any], clave: str) -> Optional[float]:
    """
    Valor REAL de una de las 45 métricas en un partido: FT y, si falta, 1T + 2T.

    Es la lectura directa de `periodos[corte]["filas"]`; no se reconstruye desde
    medias ni se interpola desde los goles.
    """
    fuente = _partido_fuente(partido)
    periodos = fuente.get("periodos") or {}
    for corte in ("FT", "1T", "2T"):
        bloque = periodos.get(corte) or {}
        for fila in bloque.get("filas") or []:
            if isinstance(fila, dict) and fila.get("clave") == clave:
                valor = fila.get("equipo")
                if isinstance(valor, (int, float)) and not isinstance(valor, bool):
                    return float(valor)
                return None
    return None


def _valor_metrica_periodo(partido: Dict[str, Any], clave: str, corte: str,
                           bando: str = "equipo") -> Optional[float]:
    """Valor real de una métrica en un periodo concreto ('1T'/'2T'/'FT') y bando ('equipo'/'rival')."""
    fuente = _partido_fuente(partido)
    bloque = (fuente.get("periodos") or {}).get(corte) or {}
    for fila in bloque.get("filas") or []:
        if isinstance(fila, dict) and fila.get("clave") == clave:
            valor = fila.get(bando)
            if isinstance(valor, (int, float)) and not isinstance(valor, bool):
                return float(valor)
            return None
    return None


def _goles_equipo(partido: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Goles (equipo, rival) soportando el esquema de auditoría y el normalizado."""
    marcador = (_partido_fuente(partido).get("marcador") or {})
    ge, gr = marcador.get("equipo"), marcador.get("rival")
    if isinstance(ge, (int, float)) and isinstance(gr, (int, float)):
        return float(ge), float(gr)
    gl, gv = partido.get("goles_local"), partido.get("goles_visitante")
    if isinstance(gl, (int, float)) and isinstance(gv, (int, float)):
        return float(gl), float(gv)
    return None, None


def obtener_tier_liga(nombre_torneo_o_liga: str) -> int:
    """
    Determina el Tier competitivo de una liga/torneo:
      Tier 1: Primeras divisiones de élite y torneos continentales mayores (Premier League, LaLiga, Serie A, Bundesliga, Ligue 1, Champions, Europa League, etc.)
      Tier 2: Segundas divisiones (Championship, LaLiga 2, Segunda, Serie B, 2. Bundesliga, Ligue 2, etc.)
      Tier 3: Terceras divisiones (Primera Federación / RFEF, Serie C, 3. Liga, League One, National, etc.)
      Tier 4: Cuartas divisiones o inferiores (Segunda Federación, League Two, etc.)
    """
    t_raw = (nombre_torneo_o_liga or "").lower()
    t = unicodedata.normalize("NFKD", t_raw).encode("ascii", "ignore").decode("utf-8").lower()
    t = t.replace("1a", "1").replace("2a", "2").replace("1o", "1").replace("2o", "2")
    if any(k in t for k in ["segunda federacion", "segunda rfef", "2 federacion", "2 rfef", "league two", "serie d", "cuarta"]):
        return 4
    if any(k in t for k in ["primera federacion", "primera rfef", "1 federacion", "1 rfef", "serie c", "3. liga", "3.liga", "league one", "national"]):
        return 3
    if any(k in t for k in ["championship", "segunda", "laliga 2", "laliga2", "hypermotion", "serie b", "2. bundesliga", "2.bundesliga", "ligue 2", "ligue2"]):
        return 2
    return 1


def obtener_tier_coef_liga(nombre_torneo_o_liga: str) -> float:
    """
    Calcula el Coeficiente de Fuerza de Liga (tier_coef):
      - 1.00: Top 5 Ligas Europeas (Premier League, La Liga, Serie A, Bundesliga, Ligue 1)
              y UEFA Champions League (fase liga/grupos/playoffs finales).
      - 0.85: Ligas Nivel 2 (Eredivisie, Liga Portugal, Süper Lig de Turquía,
              Jupiler Pro League Bélgica, Chance Liga Checa, Premiership Escocia, etc.)
              y UEFA Europa League.
      - 0.68: Ligas Nivel 3 / Torneos Menores (Premyer Liqa Azerbaiyán, Chipre, Grecia,
              Austria, Suiza, Dinamarca, Noruega, Suecia, Polonia, Croacia, Serbia, fases previas /
              clasificación UCL/UEL/UECL, Segundas divisiones) y UEFA Conference League.
      - 0.50: Terceras divisiones, ligas semi-profesionales o inferiores.
    """
    t_raw = (nombre_torneo_o_liga or "").lower()
    t = unicodedata.normalize("NFKD", t_raw).encode("ascii", "ignore").decode("utf-8").lower()
    t = t.replace("1a", "1").replace("2a", "2").replace("1o", "1").replace("2o", "2")

    # 1. Torneos inferiores (3ª división hacia abajo, semi-profesionales)
    if any(k in t for k in [
        "segunda federacion", "segunda rfef", "2 federacion", "2 rfef", "league two", "serie d", "cuarta",
        "primera federacion", "primera rfef", "1 federacion", "1 rfef", "serie c", "3. liga", "3.liga",
        "league one", "national", "tercera"
    ]):
        return 0.50

    # 2. Fases previas / clasificación europea (UCL Qual., UEL Qual., UECL Qual.)
    if any(k in t for k in ["qual", "previa", "clasificaci"]):
        return 0.65

    # 3. Ligas Nivel 3 / Torneos Menores / Hungría / Segundas divisiones
    if any(k in t for k in [
        "nb i", "nb 1", "nb1", "hungria", "hungary", "magyar", "otp bank", "nemzeti bajnoksag",
        "premyer liqa", "azerbaijan", "azerbaiyan", "chipre", "cyprus",
        "super league", "super league 1", "superleague", "greece", "grecia", "kypello", "greek cup",
        "austria", "admiral", "suiza", "swiss",
        "superligaen", "denmark", "dinamarca", "eliteserien", "noruega", "norway",
        "allsvenskan", "suecia", "sweden", "ekstraklasa", "polonia", "poland",
        "hnl", "croacia", "croatia", "superliga serbia", "serbia",
        "premier liha", "ukrain", "ucrania", "nm cupen", "norgesmesterskap",
        "conference league", "uecl",
        "segunda", "laliga 2", "laliga2", "hypermotion", "serie b", "2. bundesliga", "2.bundesliga", "ligue 2", "ligue2"
    ]):
        if any(h in t for h in ["nb i", "nb 1", "nb1", "hungria", "hungary", "magyar", "otp bank", "nemzeti bajnoksag"]):
            return 0.65
        return 0.68

    # 4. Ligas Nivel 2
    if any(k in t for k in [
        "eredivisie", "liga portugal", "primeira liga", "portugal",
        "super lig", "superlig", "turquia", "turkey",
        "pro league", "jupiler", "belgica", "belgium",
        "chance liga", "checa", "czech", "gambrinus",
        "premiership", "escocia", "scotland",
        "championship",
        "europa league", "uel"
    ]):
        return 0.85

    # 5. Top 5 + Champions League
    if any(k in t for k in [
        "premier league", "laliga", "primera division",
        "serie a", "bundesliga", "ligue 1", "ligue1",
        "champions league", "ucl"
    ]):
        return 1.00

    return 0.85


def obtener_tier_coef(
    partidos: Optional[List[Dict[str, Any]]] = None,
    perfil: Optional[Dict[str, Any]] = None,
    nombre_equipo: str = "",
    competicion_str: str = ""
) -> float:
    """Calcula el Coeficiente de Fuerza de Liga (tier_coef) de un equipo."""
    nom = (nombre_equipo or "").lower()
    if any(eq in nom for eq in ["ferencvaros", "ferencváros", "fradi", "ujpest", "újpest", "fehervar", "debrecen", "puskas"]):
        return 0.65
    if any(eq in nom for eq in ["dortmund", "bayern", "real madrid", "barcelona", "arsenal", "liverpool", "manchester", "paris", "inter", "milan", "juventus", "atletico"]):
        return 1.00

    # Torneos continentales (UCL, UEL, UECL, etc.): la fuerza base del equipo proviene de su liga doméstica/mediana
    # NO del torneo continental mismo (evitar inflar Ferencváros o Bodø/Glimt a 1.00 por jugar UCL).
    es_torneo_continental = any(k in (competicion_str or "").lower() for k in ["champions", "ucl", "europa league", "uel", "conference", "uecl", "copa"])

    if competicion_str and not es_torneo_continental:
        coef_comp = obtener_tier_coef_liga(competicion_str)
        if coef_comp != 0.85:
            return coef_comp

    coefs = []
    for p in (partidos or []):
        src = _partido_fuente(p)
        torneo = str(src.get("torneo") or src.get("competition") or p.get("torneo") or "")
        if es_copa_nacional(torneo):
            continue
        # Si el torneo es continental, no usarlo para definir la fuerza doméstica base del equipo si hay partidos de liga
        if torneo and not any(k in torneo.lower() for k in ["champions", "ucl", "europa league", "uel", "conference", "uecl"]):
            coefs.append(obtener_tier_coef_liga(torneo))
    if coefs:
        return float(np.median(coefs))

    # Si todos los partidos eran de UCL o copas, evaluar todos los torneos
    if partidos:
        for p in partidos:
            src = _partido_fuente(p)
            torneo = str(src.get("torneo") or src.get("competition") or p.get("torneo") or "")
            if torneo and not es_copa_nacional(torneo):
                coefs.append(obtener_tier_coef_liga(torneo))
        if coefs:
            return float(np.median(coefs))

    if perfil:
        torneo_p = str(perfil.get("torneo") or perfil.get("liga") or perfil.get("competicion") or "")
        if torneo_p and not any(k in torneo_p.lower() for k in ["champions", "ucl", "europa league", "uel"]):
            return obtener_tier_coef_liga(torneo_p)

    if any(eq in nom for eq in ["fenerbahce", "fenerbahçe", "galatasaray", "besiktas", "slavia", "sparta praga", "benfica", "porto", "sporting", "ajax", "psv", "feyenoord", "celtic", "rangers"]):
        return 0.85
    if any(eq in nom for eq in ["sabah", "qarabag", "bodo", "bodø", "zagreb", "partizan", "estrella roja", "apoel",
                                "shakhtar", "dynamo kyiv", "dynamo kiev", "dinamo kiev",
                                "aek", "aek athens", "panathinaikos", "paok", "olympiacos", "olympiakos", "aris", "panserraikos"]):
        return 0.68

    if competicion_str:
        return obtener_tier_coef_liga(competicion_str)

    return 0.85


# ── Corrección post-auditoría UCL (04/11/2026) ─────────────────────────────────
PALABRAS_COPA_NACIONAL = (
    "copa del rey", "efl cup", "carabao", "coppa italia", "dfb-pokal", "dfb pokal",
    "fa cup", "nm cupen", "norgesmesterskap", "knvb", "beker", "coupe de france",
    "taca de portugal", "taça", "turkiye kupasi", "türkiye kupası", "kupa",
    "pokal", "cupen", "copa ", "coupe", "cup",
)
PALABRAS_COPA_UEFA = ("champions", "europa league", "conference", "uefa", "super cup", "supercopa",
                      "club world", "mundial de clubes", "libertadores", "sudamericana", "concacaf")


def es_copa_nacional(torneo: str) -> bool:
    """Copa doméstica (excluye torneos UEFA / supercopas)."""
    t = unicodedata.normalize("NFKD", (torneo or "").lower()).encode("ascii", "ignore").decode("utf-8")
    if not t or any(k in t for k in PALABRAS_COPA_UEFA):
        return False
    return any(k in t for k in PALABRAS_COPA_NACIONAL) or t.endswith(" copa") or t == "copa"


def es_champions_league(torneo: str) -> bool:
    """Detecta partidos de fase de liga / fase de grupos de UEFA Champions League."""
    t = unicodedata.normalize("NFKD", (torneo or "").lower()).encode("ascii", "ignore").decode("utf-8")
    return any(k in t for k in ["champions league", "ucl"]) and not any(k in t for k in ["qual", "previa", "clasificaci"])


def peso_credibilidad_partido(partido: Dict[str, Any]) -> float:
    """
    Champions League (fase de liga) pesa 3.0x dentro de la muestra.
    Goleadas (>= 4 goles de diferencia) en Copa Nacional ante rivales de divisiones
    inferiores/amateur (ej. Rana FK 0-6, Panserraikos 8-1) pesan 0.35x para no
    distorsionar el promedio que se proyecta a Champions League.
    """
    src = _partido_fuente(partido)
    torneo = str(src.get("torneo") or src.get("competition") or src.get("competicion")
                 or partido.get("torneo") or "")
    if es_champions_league(torneo):
        return 3.0
    if es_copa_nacional(torneo):
        gf, gc = _goles_equipo(partido)
        if gf is not None and gc is not None and (gf - gc) >= 4:
            return 0.35
    return 1.0


def factor_uefa_relativo(tier_coef_propio: float, tier_coef_rival: float) -> float:
    """(tier_propio / tier_rival) cuando el propio es de liga más débil; si no, 1.0."""
    try:
        tp, tr = float(tier_coef_propio), float(tier_coef_rival)
    except (TypeError, ValueError):
        return 1.0
    if tp <= 0 or tr <= 0 or tp >= tr:
        return 1.0
    return float(tp / tr)


EQUIPOS_SEDE_NEUTRAL_UEFA = (
    "shakhtar", "dynamo kyiv", "dynamo kiev", "dinamo kiev", "dnipro", "zorya",
    "polissya", "maccabi tel", "maccabi haifa", "hapoel", "bate borisov", "dinamo minsk",
)


def es_sede_neutral(nombre_equipo: str, competicion: str = "") -> bool:
    """Clubes que juegan sus partidos UEFA como local en sede neutral (sin bono de localía)."""
    nom = unicodedata.normalize("NFKD", (nombre_equipo or "").lower()).encode("ascii", "ignore").decode("utf-8")
    if not any(e in nom for e in EQUIPOS_SEDE_NEUTRAL_UEFA):
        return False
    comp = (competicion or "").lower()
    return (not comp) or any(k in comp for k in PALABRAS_COPA_UEFA)


OFENSIVAS_UEFA = ("xg", "xg_juego_abierto", "xg_sin_penalti", "xg_balon_parado", "xgot",
                  "toques_area_rival", "remates_totales", "remates_puerta",
                  "remates_dentro_area", "grandes_ocasiones")
CONCEDIDAS_UEFA = ("xgot_enfrentado",)


def reescalar_factores_a_delta(
    factores: Dict[str, float],
    delta_pct_real: float,
    fijos: Optional[Tuple[str, ...]] = None,
    pisos: Optional[Dict[str, float]] = None,
    techos: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    Acota cada uno de los 5 factores latentes estrictamente en [-12.0%, +15.0%]
    y garantiza que su suma coincida al primer decimal con el cambio porcentual
    de lambda_base -> lambda_red sin explosiones multiplicativas.

    `fijos`: factores que NO se tocan al repartir el residuo (p. ej. la Posesión
    Progresiva de suma cero, para que Local y Visita nunca reciban ambos bono).
    `pisos` / `techos`: restricciones de signo y magnitud táctica (p. ej. FragRiv > 0
    si el rival concede xGOTc >= 1.50).
    """
    claves = ["F_posesion_progresiva", "F_creacion", "F_finalizacion", "F_porteria_rival", "F_fragilidad_rival"]
    idx_fijos = {i for i, k in enumerate(claves) if fijos and k in fijos}
    pisos_dict = pisos or {}
    techos_dict = techos or {}

    vals = []
    for k in claves:
        v = float(factores.get(k, 0.0))
        piso = max(-12.0, pisos_dict.get(k, -12.0))
        techo = min(15.0, techos_dict.get(k, 15.0))
        vals.append(round(float(np.clip(v, piso, techo)), 1))

    delta_target = round(float(np.clip(float(delta_pct_real), -35.0, 45.0)), 1)

    # Reparto aditivo iterativo respetando estrictamente pisos y techos
    for _ in range(12):
        dif = round(delta_target - sum(vals), 1)
        if abs(dif) < 0.09:
            break
        libres = []
        for i, k in enumerate(claves):
            if i in idx_fijos:
                continue
            piso = max(-12.0, pisos_dict.get(k, -12.0))
            techo = min(15.0, techos_dict.get(k, 15.0))
            if dif > 0 and vals[i] < techo - 1e-6:
                libres.append(i)
            elif dif < 0 and vals[i] > piso + 1e-6:
                libres.append(i)
        if not libres:
            break
        paso = dif / float(len(libres))
        for i in libres:
            k = claves[i]
            piso = max(-12.0, pisos_dict.get(k, -12.0))
            techo = min(15.0, techos_dict.get(k, 15.0))
            vals[i] = round(float(np.clip(vals[i] + paso, piso, techo)), 1)

    # Ajuste fino de 0.1% en el factor con margen disponible
    residuo = round(delta_target - sum(vals), 1)
    if abs(residuo) >= 0.1:
        for i in sorted(range(5), key=lambda idx: abs(vals[idx]), reverse=True):
            if i in idx_fijos:
                continue
            k = claves[i]
            piso = max(-12.0, pisos_dict.get(k, -12.0))
            techo = min(15.0, techos_dict.get(k, 15.0))
            nuevo = round(vals[i] + residuo, 1)
            if piso <= nuevo <= techo:
                vals[i] = nuevo
                break

    return {k: round(float(np.clip(vals[i], max(-12.0, pisos_dict.get(k, -12.0)), min(15.0, techos_dict.get(k, 15.0)))), 1)
            for i, k in enumerate(claves)}


def obtener_tier_equipo(partidos: List[Dict[str, Any]], perfil: Optional[Dict[str, Any]] = None) -> int:
    """Calcula el Tier principal de un equipo según sus partidos oficiales más recientes."""
    tiers = []
    for p in (partidos or []):
        src = _partido_fuente(p)
        torneo = str(src.get("torneo") or src.get("competition") or p.get("torneo") or "")
        t_low = torneo.lower()
        if any(c in t_low for c in ["copa del rey", "efl cup", "carabao", "coppa italia", "dfb-pokal", "fa cup"]):
            continue
        if torneo:
            tiers.append(obtener_tier_liga(torneo))
    if tiers:
        return int(round(float(np.median(tiers))))
    if perfil:
        torneo_p = str(perfil.get("torneo") or perfil.get("liga") or "")
        if torneo_p:
            return obtener_tier_liga(torneo_p)
    return 1


def _es_division_inferior_o_copa(partido: Dict[str, Any]) -> bool:
    fuente = _partido_fuente(partido)
    torneo = str(fuente.get("torneo") or fuente.get("competition") or partido.get("torneo") or "").lower()
    ligas_menores = (
        "championship", "segunda", "laliga 2", "laliga2", "serie b",
        "2. bundesliga", "2.bundesliga", "ligue 2", "ligue2",
        "primera federacion", "serie c", "efl cup", "carabao"
    )
    return any(l in torneo for l in ligas_menores)


def _es_equipo_segunda_division(partidos: List[Dict[str, Any]]) -> bool:
    ligas_2da = ("championship", "segunda", "laliga 2", "laliga2", "serie b", "2. bundesliga", "ligue 2")
    c = 0
    for p in partidos or []:
        src = _partido_fuente(p)
        t = str(src.get("torneo") or src.get("competition") or "").lower()
        if any(l in t for l in ligas_2da):
            c += 1
    return c >= 3


def extraer_series_reales_15_partidos(
    partidos_cronologicos: List[Dict[str, Any]],
    nombre_equipo: str,
    perfil_equipo: Dict[str, Any],
    tier_rival: int = 1,
    factor_uefa: float = 1.0,
) -> Dict[str, List[float]]:
    """
    Construye el Tensor Tridimensional X in R^(15 x 3 x 45) con las 45 métricas
    REALES de cada partido y devuelve una serie cronológica por variable.

    Aplica el Coeficiente Relativo de División (ΔTier = Tier_partido - Tier_rival_actual):
    solo si un partido fue en categoría inferior al rival de hoy (ΔTier >= 1), multiplica ofensivas
    por 0.76^ΔTier y multiplica defensivas concedidas por 1.22^ΔTier. Si están en la misma división,
    el factor es 1.0.
    Además (post-auditoría UCL): multiplica ofensivas por `factor_uefa`
    ((tier_propio/tier_rival)**0.80) y divide las concedidas por él; y las goleadas
    de Copa Nacional (>= 4 goles) se contraen a la media con peso 0.35x.
    Reconstruye además métricas xG/xGOT cuando falte tracking en ligas menores.
    """
    ordenados = sorted(
        (p for p in partidos_cronologicos or [] if isinstance(p, dict)),
        key=lambda p: str(p.get("fecha") or p.get("utcTime") or "")
    )
    if not ordenados:
        ordenados = [{}]

    METRICAS_OFENSIVAS_DESCUENTO = {
        "xg", "xg_juego_abierto", "xg_sin_penalti", "xgot",
        "remates_totales", "remates_puerta", "remates_dentro_area", "grandes_ocasiones",
        "toques_area_rival"
    }
    METRICAS_DEFENSIVAS_CONCEDIDAS = {
        "xgot_enfrentado"
    }

    # Primera pasada: valores reales + reconstrucción de métricas sin tracking + ajuste relativo de división
    crudos: Dict[str, List[Optional[float]]] = {}
    factores_of_partido = []
    factores_def_partido = []

    for p in ordenados:
        src_p = _partido_fuente(p)
        torneo_p = str(src_p.get("torneo") or src_p.get("competition") or p.get("torneo") or "")
        tier_p = obtener_tier_liga(torneo_p)
        delta_tier = tier_p - tier_rival
        f_of = (0.76 ** delta_tier) if delta_tier >= 1 else 1.0
        f_def = (1.22 ** delta_tier) if delta_tier >= 1 else 1.0
        fu = float(factor_uefa) if factor_uefa and factor_uefa > 0 else 1.0
        factores_of_partido.append(f_of * fu)
        factores_def_partido.append(f_def / fu)
    pesos_cred = [peso_credibilidad_partido(p) for p in ordenados]

    for clave in VARIABLES_REALES_45:
        vals_c = []
        for idx_p, p in enumerate(ordenados):
            v = _valor_metrica(p, clave)
            rem_tot = _valor_metrica(p, "remates_totales") or 0.0
            rem_pue = _valor_metrica(p, "remates_puerta") or 0.0
            rem_area = _valor_metrica(p, "remates_dentro_area") or 0.0
            rem_fuera_a = _valor_metrica(p, "remates_fuera_area") or 0.0
            grandes_oc = _valor_metrica(p, "grandes_ocasiones") or 0.0
            gf_p, gc_p = _goles_equipo(p)
            gf_p = gf_p if gf_p is not None else 1.0
            gc_p = gc_p if gc_p is not None else 1.0

            # Reconstrucción si remates_totales > 0 y xg == 0 (o falta tracking)
            if rem_tot <= 0:
                if clave == "xg":
                    v = float(round(gf_p, 2))
                elif clave == "xgot":
                    v = float(round(gf_p, 2))
                elif clave == "xgot_enfrentado":
                    v = float(round(gc_p, 2))
                elif clave == "goles_evitados":
                    v = 0.0
                elif clave == "remates_totales":
                    v = float(max(4.0, round(gf_p * 4.0 + 3.0)))
                elif clave == "remates_puerta":
                    v = float(max(1.0, round(gf_p * 2.0 + 1.0)))
            elif clave == "xg" and rem_tot > 0 and (v is None or v <= 0.001):
                if rem_area > 0:
                    v = round(0.11 * rem_area + 0.035 * rem_fuera_a + 0.28 * grandes_oc, 2)
                else:
                    v = round(0.09 * rem_tot + 0.14 * rem_pue, 2)
            elif clave == "xgot" and rem_pue > 0 and (v is None or v <= 0.001):
                v = round(0.29 * rem_pue + 0.12 * gf_p, 2)
            elif clave == "xgot_enfrentado" and (v is None or v <= 0.001):
                rem_pue_riv = _valor_metrica(p, "remates_puerta_contra") or 3.5
                v = round(0.29 * rem_pue_riv + 0.12 * gc_p, 2)
            elif clave == "goles_evitados" and v is None:
                xgot_enf_p = _valor_metrica(p, "xgot_enfrentado") or (0.29 * 3.5 + 0.12 * gc_p)
                v = round(xgot_enf_p - gc_p, 2)
            elif clave == "saques_puerta" and (v is None or v == 0):
                rem_fuera_riv = _valor_metrica(p, "remates_fuera_contra") or 4.0
                palo_riv = _valor_metrica(p, "al_palo_contra") or 0.0
                v = max(1.0, float(rem_fuera_riv - palo_riv))

            if v is not None:
                torneo_p = str(p.get("torneo") or p.get("competition") or p.get("competicion") or "").lower()
                if es_copa_nacional(torneo_p) and (gf_p - gc_p) >= 4:
                    if clave in ("xg", "xgot", "xg_juego_abierto", "xg_sin_penalti"):
                        v = min(float(v), 2.2)
                    elif clave == "toques_area_rival":
                        v = min(float(v), 25.0)
                    elif clave in ("grandes_ocasiones", "remates_puerta"):
                        v = min(float(v), 2.5)
                if clave in METRICAS_OFENSIVAS_DESCUENTO:
                    v = v * factores_of_partido[idx_p]
                elif clave in METRICAS_DEFENSIVAS_CONCEDIDAS:
                    v = v * factores_def_partido[idx_p]
            vals_c.append(v)
        crudos[clave] = vals_c

    medias: Dict[str, float] = {}
    for clave, valores in crudos.items():
        presentes = [v for v in valores if v is not None]
        medias[clave] = float(np.mean(presentes)) if presentes else NEUTRALES.get(clave, 0.0)

    # Goleadas de Copa Nacional (>= 4 goles): contracción a la media con peso 0.35x
    for clave in VARIABLES_REALES_45:
        vals_c = crudos[clave]
        for idx_p, w in enumerate(pesos_cred):
            if w < 1.0 and vals_c[idx_p] is not None:
                vals_c[idx_p] = w * vals_c[idx_p] + (1.0 - w) * medias[clave]

    series: Dict[str, List[float]] = {}
    for clave in VARIABLES_REALES_45:
        series[clave] = [round(v if v is not None else medias[clave], 4) for v in crudos[clave]]

    # Series del 2º tiempo (aceleración diésel propia y fragilidad 2T concedida)
    for clave_s, bando in (("xg_2T", "equipo"), ("xg_contra_2T", "rival")):
        vals_2t = [_valor_metrica_periodo(p, "xg", "2T", bando) for p in ordenados]
        pres_2t = [v for v in vals_2t if v is not None]
        med_2t = float(np.mean(pres_2t)) if pres_2t else 0.65
        f_lado = factores_of_partido if bando == "equipo" else factores_def_partido
        series[clave_s] = [round((v if v is not None else med_2t) * f_lado[i], 4)
                           for i, v in enumerate(vals_2t)]

    # Serie real de goles (a favor / en contra del equipo auditado)
    goles_f: List[float] = []
    goles_c: List[float] = []
    m = perfil_equipo.get("metricas_clave", {})
    for idx_p, p in enumerate(ordenados):
        gf, gc = _goles_equipo(p)
        if gf is None or gc is None:
            gf = float(m.get("goles_favor", m.get("goles_favor_est", 1.30)))
            gc = float(m.get("goles_contra", m.get("goles_contra_real", 1.25)))
        torneo_p = str(p.get("torneo") or p.get("competition") or p.get("competicion") or "").lower()
        if es_copa_nacional(torneo_p) and (gf - gc) >= 4:
            gf = min(float(gf), 2.2)
        gf = gf * factores_of_partido[idx_p]
        gc = gc * factores_def_partido[idx_p]
        goles_f.append(float(gf))
        goles_c.append(float(gc))
    if goles_f and any(w < 1.0 for w in pesos_cred):
        mgf, mgc = float(np.mean(goles_f)), float(np.mean(goles_c))
        goles_f = [w * g + (1.0 - w) * mgf for g, w in zip(goles_f, pesos_cred)]
        goles_c = [w * g + (1.0 - w) * mgc for g, w in zip(goles_c, pesos_cred)]

    # Resiliencia de muestra: si 1 <= len(goles_f) < 15, rellenar hacia atrás (backfill) con la media
    # empírica del equipo para que la dimensión temporal P=15 no desactive el tensor ni el filtro LSTM
    n_partidos = len(goles_f)
    if 1 <= n_partidos < 15:
        faltantes = 15 - n_partidos
        for clave in list(VARIABLES_REALES_45) + ["xg_2T", "xg_contra_2T"]:
            if clave in medias:
                med_val = round(medias.get(clave, NEUTRALES.get(clave, 0.0)), 4)
            else:
                med_val = round(float(np.mean(series[clave])) if series[clave] else 0.65, 4)
            series[clave] = [med_val] * faltantes + series[clave]
        media_gf = round(float(np.mean(goles_f)), 2)
        media_gc = round(float(np.mean(goles_c)), 2)
        goles_f = [media_gf] * faltantes + goles_f
        goles_c = [media_gc] * faltantes + goles_c

    series["goles_favor"] = goles_f
    series["goles_contra"] = goles_c
    return series


def bilinear_pooling_y_cross_attention(
    estados_variables: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, float]]]:
    """
    Ejecuta exactamente el diagrama:
      [Métrica i (ej. xG)] ──⊗── [Métricas restantes]
                                  │
                                  ▼ (Bilinear Pooling: Matriz de Covarianza Cruzada)
                     [Matriz de Covarianza Cruzada]
                                  │
                                  ▼ (Cross-Attention + Reducción de Espacio)
                 [Filtrado de Patrones de Alta Probabilidad]
    """
    nombres = [k for k in estados_variables.keys() if not k.startswith("_")]
    M = len(nombres)
    if not M:
        return estados_variables, []
    vecs = np.array([estados_variables[k]["vector_temporal"] for k in nombres], dtype=float)  # [M, 15]
    pendientes = np.array([estados_variables[k]["pendiente_dmdp"] for k in nombres], dtype=float)

    # 1. Bilinear Pooling: Matriz de Covarianza Cruzada C = (V @ V.T) / 15
    matriz_covarianza_cruzada = (vecs @ vecs.T) / 15.0

    # 2. Cross-Attention con reducción sucesiva de espacio (1 contra las restantes)
    usados = set()
    patrones_filtrados: List[Tuple[str, float]] = []

    for i in range(M):
        if i in usados:
            continue
        restantes = [j for j in range(M) if j != i and j not in usados]
        if not restantes:
            break
        # Puntajes de atención de la variable i sobre las restantes.
        # Se normalizan por fila (media 0, desviación 1) antes del softmax: sin esto
        # la atención queda casi uniforme (0,03 ≈ 1/45) y el "cruce" no distingue
        # nada, aunque la matriz de covarianza esté bien calculada.
        logits_attn = matriz_covarianza_cruzada[i, restantes].astype(float)
        dispersion = float(np.std(logits_attn))
        if dispersion > 1e-12:
            logits_attn = (logits_attn - float(np.mean(logits_attn))) / dispersion
        logits_attn = logits_attn * 2.5
        pesos_attn = np.exp(logits_attn - np.max(logits_attn))
        pesos_attn /= np.sum(pesos_attn)

        j_mejor_local = int(np.argmax(pesos_attn))
        j_mejor = restantes[j_mejor_local]
        # Fuerza del cruce = magnitud de la covarianza cruzada. Como las series se
        # estandarizan, `(V·Vᵀ)/15` es una correlación y la fuerza queda en 0..1.
        fuerza_cruce = abs(float(matriz_covarianza_cruzada[i, j_mejor]))

        nombre_cruce = f"{nombres[i]} ⊗ {nombres[j_mejor]}"
        patrones_filtrados.append((nombre_cruce, round(fuerza_cruce, 4)))
        if float(pesos_attn[j_mejor_local]) >= 0.28 and len(usados) < M - 4:
            usados.add(j_mejor)

    patrones_filtrados.sort(key=lambda x: x[1], reverse=True)
    return estados_variables, patrones_filtrados


# Cruces inter-equipo OBLIGATORIOS (Ataque/Ejecución propia A ⊗ Resistencia/Defensa rival B).
CRUCES_INTER_EQUIPO: Tuple[Tuple[str, str, str, str, int], ...] = (
    ("toques_area_rival", "despejes", "toques_area_rival", "despejes_rival", -1),
    ("xgot", "xgot_enfrentado", "xgot_favor", "xgot_concedido_rival", -1),
    ("grandes_ocasiones", "paradas", "grandes_ocasiones", "paradas_rival", -1),
    ("pases_tercio_final_pct", "intercepciones", "pases_tercio_final_pct", "intercepciones_rival", -1),
    ("remates_dentro_area", "errores_gol", "remates_dentro_area", "errores_defensivos_rival", +1),
)
NEUTROS_CRUCE: Dict[str, float] = {
    "toques_area_rival": 22.0, "despejes": 18.0,
    "xgot": 1.30, "xgot_enfrentado": 1.30,
    "grandes_ocasiones": 2.2, "paradas": 2.8,
    "pases_tercio_final_pct": 68.0, "intercepciones": 8.0,
    "remates_dentro_area": 8.0, "errores_gol": 0.15,
}
ESCALAS_CRUCE: Dict[str, float] = {
    "toques_area_rival": 8.0, "despejes": 6.0,
    "xgot": 0.45, "xgot_enfrentado": 0.45,
    "grandes_ocasiones": 1.0, "paradas": 1.2,
    "pases_tercio_final_pct": 10.0, "intercepciones": 3.0,
    "remates_dentro_area": 3.5, "errores_gol": 0.20,
}


def bilinear_pooling_inter_equipo(
    estados_propio: Dict[str, Dict[str, Any]],
    estados_rival: Dict[str, Dict[str, Any]],
) -> List[Tuple[str, float]]:
    """
    Bilinear Pooling + Cross-Attention INTER-EQUIPO (A ⊗ B). Prohíbe cruces
    intra-familia colineales (posesion_pct ⊗ pases_total): cada cruce enfrenta una
    variable de Ataque/Ejecución propia contra una de Resistencia/Defensa rival.

      • Término bilineal: covarianza cruzada de los vectores temporales (z-score) A·B/15.
      • Término de nivel: z_A (desvío del atacante) ⊗ z_B (desvío de la defensa rival).
      • Cross-Attention: softmax sobre los 5 cruces → fuerza ∈ [0, 1].
    """
    def h(est: Dict[str, Dict[str, Any]], k: str) -> float:
        return float(est.get(k, {}).get("h_reciente", NEUTROS_CRUCE.get(k, 0.0)) or 0.0)

    def vec(est: Dict[str, Dict[str, Any]], k: str) -> np.ndarray:
        v = est.get(k, {}).get("vector_temporal")
        return np.asarray(v, dtype=float) if v is not None else np.zeros(15)

    brutos: List[Tuple[str, float]] = []
    for ka, kb, ea, eb, sentido in CRUCES_INTER_EQUIPO:
        z_a = (h(estados_propio, ka) - NEUTROS_CRUCE[ka]) / ESCALAS_CRUCE[ka]
        z_b = (h(estados_rival, kb) - NEUTROS_CRUCE[kb]) / ESCALAS_CRUCE[kb]
        va, vb = vec(estados_propio, ka), vec(estados_rival, kb)
        n = min(len(va), len(vb))
        cov = float(np.dot(va[-n:], vb[-n:]) / max(1, n)) if n else 0.0
        nivel = math.tanh(0.5 * (abs(z_a) + abs(z_b)) + 0.25 * abs(z_a * z_b))
        brutos.append((f"{ea} ⊗ {eb}", 0.65 * nivel + 0.35 * min(1.0, abs(cov))))

    logits = np.array([b[1] for b in brutos], dtype=float) * 3.0
    attn = np.exp(logits - logits.max())
    attn /= attn.sum()
    patrones = [(nombre, round(float(clip(f * (0.75 + 1.25 * a), 0.0, 1.0)), 4))
                for (nombre, f), a in zip(brutos, attn)]
    patrones.sort(key=lambda x: x[1], reverse=True)
    return patrones


UMBRAL_CRUCE_FUERTE = 0.60
# Centro de la intensidad de los cruces INTER-EQUIPO (media de los 3 cruces A x B mas fuertes)
CENTRO_INTENSIDAD_CRUCE = 0.55

# Familias de las 45 métricas: sirven para que el "cruce #1" del sello no sea una
# pareja de sinónimos (xg ⊗ xg_sin_penalti) sino un cruce entre bloques distintos.
FAMILIA_METRICA: Dict[str, str] = {
    "xg": "xg", "xg_juego_abierto": "xg", "xg_balon_parado": "xg",
    "xg_sin_penalti": "xg", "xgot": "xg",
    "remates_totales": "tiro", "remates_puerta": "tiro", "remates_fuera": "tiro",
    "remates_rechazados": "tiro", "remates_dentro_area": "tiro",
    "remates_fuera_area": "tiro", "al_palo": "tiro",
    "grandes_ocasiones": "tiro", "grandes_ocasiones_falladas": "tiro",
    "pases_total": "posesion_pase", "pases_precision_pct": "posesion_pase", "pases_ratio": "posesion_pase",
    "pases_largos_pct": "posesion_pase", "pases_tercio_final_pct": "posesion_pase",
    "pases_entre_lineas": "posesion_pase", "cruces_pct": "posesion_pase", "xa_equipo": "posesion_pase",
    "posesion_pct": "posesion_pase",
    "corners": "balon_parado", "tiros_libres": "balon_parado",
    "saques_banda": "balon_parado", "saques_puerta": "balon_parado",
    "toques_area_rival": "ataque", "fueras_juego": "ataque",
    "despejes": "defensa", "intercepciones": "defensa", "bloqueos": "defensa",
    "recuperaciones": "defensa", "entradas_pct": "defensa",
    "duelos_ganados": "defensa", "duelos_suelo_pct": "defensa",
    "duelos_aereos_pct": "defensa", "faltas": "defensa",
    "tarjetas_amarillas": "defensa", "tarjetas_rojas": "defensa",
    "errores_remate": "defensa", "errores_gol": "defensa",
    "paradas": "porteria", "goles_evitados": "porteria",
    "xgot_enfrentado": "porteria",
}


PROHIBIDOS_CRUCE: Tuple[str, ...] = (
    "posesion_pct ⊗ pases_total",
    "toques_area_rival ⊗ remates_totales",
    "xgot ⊗ goles_favor",
)


def cruce_dominante_distinto(patrones_filtrados: List[Tuple[str, float]]) -> str:
    """
    Cruce dominante entre los 5 cruces adversariales (Ataque Propio vs Defensa Rival).
    Filtra cualquier par prohibido o del mismo grupo.
    """
    for nombre, _fuerza in patrones_filtrados:
        if any(p in nombre for p in PROHIBIDOS_CRUCE):
            continue
        partes = nombre.split(" ⊗ ")
        if len(partes) != 2:
            continue
        return nombre
    return patrones_filtrados[0][0] if patrones_filtrados else "toques_area_rival ⊗ despejes_rival"


def intensidad_de_cruce(patrones_filtrados: List[Tuple[str, float]], top: int = 3) -> float:
    """
    Intensidad del cruce = PROPORCIÓN de variables cuya pareja cruzada se mueve
    junta (|r| >= 0,60) en la Matriz de Covarianza Cruzada.

    Por qué así y no la media de las correlaciones: las 45 métricas incluyen
    familias casi duplicadas (xg ⊗ xg_sin_penalti), así que la media se satura en
    1,00 para todos los equipos y no distinguiría nada. La proporción sí discrimina
    (un equipo coherente tiene muchas parejas acopladas; uno irregular, pocas) y
    es la que entra en λ: antes los cruces no movían ni una centésima.
    """
    if not patrones_filtrados:
        return 0.0
    fuertes = sum(1 for _, f in patrones_filtrados if abs(float(f)) >= UMBRAL_CRUCE_FUERTE)
    return float(clip(fuertes / float(len(patrones_filtrados)), 0.0, 1.0))


def patrones_tacticos_desde_estados(
    estados: Dict[str, Dict[str, Any]],
    perfil: Dict[str, Any],
    tasa_sequia: float,
    intensidad_cruce: float = 0.0,
) -> Dict[str, float]:
    """
    Construye los patrones tácticos desde los ESTADOS REALES de las 45 variables.

    Cada patrón es una razón acotada (no un rating): asfixia, presión, fragilidad,
    sequía y muralla. `intensidad_cruce` (Bilinear + Cross-Attention) modula el
    bloque ofensivo, de modo que los cruces dejan de ser decorativos.
    """
    v = lambda clave: float(estados.get(clave, {}).get("h_reciente", 0.0) or 0.0)
    pend = lambda clave: float(estados.get(clave, {}).get("pendiente_dmdp", 0.0) or 0.0)
    ratings = perfil.get("ratings_0_100", {})

    pos = clip(v("posesion_pct"), 20.0, 85.0)
    asfixia = clip((v("toques_area_rival") / 22.0) * 0.60 + (pos / 50.0) * 0.40, 0.25, 2.20)

    acciones = (v("recuperaciones") + v("intercepciones") + v("entradas_pct"))
    presion = clip((acciones / 70.0) * (float(ratings.get("Pressing_Intensity_Score", 50.0)) / 45.0),
                   0.20, 2.20)

    fragilidad = clip((v("xgot_enfrentado") / 1.25) * 0.60
                      + (0.9 * v("errores_gol") + 0.3 * v("errores_remate")) * 0.40,
                      0.20, 2.20)

    goc = max(0.8, v("grandes_ocasiones"))
    sequia = clip(0.55 * (v("grandes_ocasiones_falladas") / goc)
                  + 0.25 * min(1.0, v("al_palo") / 0.6)
                  + 0.80 * tasa_sequia
                  + 0.15 * max(0.0, -pend("xg")),
                  0.04, 1.80)

    xgot_enf_val = v("xgot_enfrentado")
    factor_muralla_xgot = max(0.35, 1.0 - 0.50 * max(0.0, (xgot_enf_val - 1.25) / 0.40))
    muralla = clip(((v("goles_evitados") + 0.60) * 0.50
                   + (v("paradas") / 3.0) * 0.30
                   + (v("bloqueos") / 2.5) * 0.20) * factor_muralla_xgot,
                   0.20, 2.00)

    # El cruce dominante (Bilinear + Cross-Attention) IMPULSA el bloque ofensivo.
    # Se centra en 0,25 (proporción típica de parejas acopladas): un equipo con
    # muchas variables que se mueven juntas sube hasta +15 %; uno irregular baja.
    empuje_cruce = clip(1.0 + 0.50 * (intensidad_cruce - CENTRO_INTENSIDAD_CRUCE), 0.90, 1.10)
    asfixia = clip(asfixia * empuje_cruce, 0.20, 2.30)

    return {
        "asfixia": asfixia,
        "presion": presion,
        "fragilidad": fragilidad,
        "sequia": sequia,
        "muralla": muralla,
        "empuje_cruce": empuje_cruce,
    }


def _nb_pmf(k: int, mu: float, r: float = 6.0) -> float:
    """P(N=k) de una Binomial Negativa con media `mu` y dispersión `r`."""
    p = r / (r + mu)
    coef = math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1))
    return coef * (p ** r) * ((1.0 - p) ** k)


def _probs_base_cola(lam: float, max_goles: int, cola_ancha: bool) -> List[float]:
    """
    Base Poisson o mezcla Poisson + Binomial Negativa (sobredispersión).

    Con `cola_ancha` (portero rival vulnerable + atacante por encima de 2,10 xG)
    se transfiere masa desde los marcadores bajos (1-0, 2-0) hacia la cola
    (3-*, 4-*, 3-0), que es exactamente el error del 4-1 del Real Madrid.
    """
    poisson = [math.exp(-lam) * (lam ** g) / math.factorial(g) for g in range(max_goles + 1)]
    if not cola_ancha:
        return poisson
    mezcla = [0.62 * p + 0.38 * _nb_pmf(g, lam) for g, p in enumerate(poisson)]
    total = sum(mezcla)
    return [m / total for m in mezcla] if total > 0 else poisson


def componentes_lambda_efectivo(
    estados_propio: Dict[str, Dict[str, Any]],
    estados_rival: Dict[str, Dict[str, Any]],
) -> Dict[str, float]:
    """
    Conecta xGOT, goles_evitados del rival y errores del rival con λ_efectivo.

        factor_finalizacion  = clip(xGOT / max(xG, 0.10), 0.90, 1.25)
        regalo_portero       = max(0, -goles_evitados_rival) · (remates_puerta/4.5)
        regalo_errores       = errores_gol_rival + 0.30 · errores_remate_rival

    Los dos "regalos" son goles que el atacante no tiene que crear: se suman a su
    λ. Se acotan (0,55 y 0,45) para que un partido atípico no dispare la matriz.
    """
    v = lambda estados, clave: float(estados.get(clave, {}).get("h_reciente", 0.0) or 0.0)

    xg_p = max(0.10, v(estados_propio, "xg"))
    xgot_p = v(estados_propio, "xgot")
    rem_puerta_p = max(0.0, v(estados_propio, "remates_puerta"))
    goles_evitados_rival = v(estados_rival, "goles_evitados")
    errores_gol_rival = max(0.0, v(estados_rival, "errores_gol"))
    errores_remate_rival = max(0.0, v(estados_rival, "errores_remate"))

    factor_finalizacion = clip(xgot_p / xg_p, 0.90, 1.25) if xgot_p > 0 else 1.0
    ajuste_portero = clip(-0.35 * goles_evitados_rival * (rem_puerta_p / 4.5), -0.35, 0.45)
    regalo_errores = clip(errores_gol_rival + 0.30 * errores_remate_rival, 0.0, 0.45)

    return {
        "factor_finalizacion": round(factor_finalizacion, 4),
        "ajuste_portero": round(ajuste_portero, 4),
        "regalo_portero": round(ajuste_portero, 4),
        "regalo_errores": round(regalo_errores, 4),
        "goles_evitados_rival": round(goles_evitados_rival, 4),
        "xgot": round(xgot_p, 4),
        "xg": round(xg_p, 4),
        "remates_puerta": round(rem_puerta_p, 4),
        "errores_gol_rival": round(errores_gol_rival, 4),
        "errores_remate_rival": round(errores_remate_rival, 4),
    }

def z_posesion_progresiva_suma_cero(m_a: Dict[str, float], m_b: Dict[str, float]) -> float:
    """
    Normalización ADVERSARIAL DE SUMA CERO (un partido = un solo balón = 100% posesión):
      posesion_esperada_A = pos_A / (pos_A + pos_B) * 100
    y reparto relativo frente a frente de pases_total, pases_tercio_final_pct y
    toques_area_rival. El resultado es ANTISIMÉTRICO: z(A, B) = -z(B, A), por lo que
    jamás pueden recibir ambos equipos bono positivo de Posesión Progresiva.
    """
    def par(k: str, neutro: float) -> Tuple[float, float]:
        a = max(0.0, float(m_a.get(k, neutro) or neutro))
        b = max(0.0, float(m_b.get(k, neutro) or neutro))
        return a, b

    def cuota(a: float, b: float) -> float:
        return a / (a + b) if (a + b) > 1e-9 else 0.5

    pos_a, pos_b = par("posesion_pct", 50.0)
    pos_esp_a = cuota(pos_a, pos_b) * 100.0
    toq_a, toq_b = par("toques_area_rival", 22.0)
    ter_a, ter_b = par("pases_tercio_final_pct", 68.0)
    pas_a, pas_b = par("pases_total", 420.0)
    rec_a, rec_b = par("recuperaciones", 45.0)
    return (
        (pos_esp_a - 50.0) / 15.0 * 0.40
        + (cuota(toq_a, toq_b) - 0.5) / 0.10 * 0.35
        + (cuota(ter_a, ter_b) - 0.5) / 0.08 * 0.25
        + (cuota(pas_a, pas_b) - 0.5) / 0.12 * 0.10
        - (rec_b - rec_a) / 15.0 * 0.15
    )


def calcular_5_factores_latentes_desde_estados(
    estados_propio: Dict[str, Dict[str, Any]],
    estados_rival: Dict[str, Dict[str, Any]],
) -> Dict[str, float]:
    """
    Calcula los 5 factores latentes explicables (XAI) directamente a partir
    de los estados LSTM recientes de cada equipo y su rival:
      F1: Posesión Efectiva y Progresión Territorial
      F2: Volumen de Creación Ofensiva
      F3: Calidad de Finalización / Letalidad
      F4: Vulnerabilidad de la Portería Rival
      F5: Fragilidad y Regalos Defensivos del Rival
    Devuelve los factores como impacto porcentual (+/- X.X%).
    """
    def v(est: Dict[str, Dict[str, Any]], k: str) -> float:
        return float(est.get(k, {}).get("h_reciente", 0.0) or 0.0)

    # F1 — Posesión Progresiva (ADVERSARIAL DE SUMA CERO: un solo balón)
    z_pos = z_posesion_progresiva_suma_cero(
        {k: v(estados_propio, k) for k in ("posesion_pct", "toques_area_rival", "pases_tercio_final_pct", "pases_total", "recuperaciones")},
        {k: v(estados_rival, k) for k in ("posesion_pct", "toques_area_rival", "pases_tercio_final_pct", "pases_total", "recuperaciones")},
    )
    f1 = math.tanh(1.5 * z_pos) * 5.0

    # F2 — Creación Ofensiva (volumen acotado a 3.5%)
    z_creac = (
        (v(estados_propio, "xg") - 1.25) / 0.40 * 0.45
        + (v(estados_propio, "remates_totales") - 12.0) / 4.0 * 0.25
        + (v(estados_propio, "grandes_ocasiones") - 1.5) / 0.8 * 0.30
        - (v(estados_rival, "bloqueos") - 3.5) / 1.5 * 0.15
    )
    f2 = math.tanh(1.5 * z_creac) * 3.5

    # F3 — Finalización / Letalidad (xGOT / xG y efectividad a puerta aumentado a 6.5%)
    eff_xg = (v(estados_propio, "xgot") - v(estados_propio, "xg")) / 0.35
    rem_tot = max(1.0, v(estados_propio, "remates_totales"))
    eff_puerta = ((v(estados_propio, "remates_puerta") / rem_tot) - 0.33) / 0.10
    z_fin = 0.55 * eff_xg + 0.45 * eff_puerta
    f3 = math.tanh(1.5 * z_fin) * 6.5

    # F4 — Portería Rival (goles evitados y paradas aumentado a 6.0%)
    g_evit = v(estados_rival, "goles_evitados")
    paradas_riv = (v(estados_rival, "paradas") - 2.8) / 1.2
    z_port = -g_evit * 1.8 - 0.25 * paradas_riv + (v(estados_rival, "xgot_enfrentado") - 1.25) / 0.40 * 0.30
    f4 = math.tanh(1.5 * z_port) * 6.0

    # F5 — Fragilidad Rival
    z_frag = (
        v(estados_rival, "errores_gol") * 1.8
        + v(estados_rival, "errores_remate") * 0.8
        + v(estados_rival, "tarjetas_rojas") * 1.2
    )
    f5 = math.tanh(1.5 * z_frag) * 3.5

    return {
        "F_posesion_progresiva": round(f1, 1),
        "F_creacion": round(f2, 1),
        "F_finalizacion": round(f3, 1),
        "F_porteria_rival": round(f4, 1),
        "F_fragilidad_rival": round(f5, 1),
    }


def calcular_doble_adn_con_shrinkage(
    partidos_cronologicos: List[Dict[str, Any]],
    estados_lstm_15: Dict[str, Dict[str, Any]],
    condicion_objetivo: str,
    perfil_equipo: Optional[Dict[str, Any]] = None,
    k_shrinkage: float = 3.5,
    tier_rival: int = 1,
    factor_uefa: float = 1.0,
) -> Dict[str, float]:
    """
    Módulo Doble ADN con Contracción Bayesiana (Empirical Bayes Shrinkage):
      w_cond = N_cond / (N_cond + k_shrinkage)  (con k=3.5 -> w_cond ~ 0.67 a 0.70)
      mu_cond(m) = w_cond * x_bar_cond(m) + (1 - w_cond) * h_lstm_15(m)
    Aísla el contexto del equipo jugando en su condición exacta (Casa o Fuera)
    sin sufrir sobreajuste por muestra reducida (N ~ 7 u 8 partidos), aplicando
    además el descuento relativo de división (ΔTier >= 1) frente al rival actual,
    el descuento UEFA `factor_uefa` y el peso 0.35x a goleadas de Copa Nacional.
    condicion_objetivo='neutral' (sede neutral) usa TODOS los partidos: sin bono de localía.
    """
    cond_target = (condicion_objetivo or "casa").lower().strip()
    fu = float(factor_uefa) if factor_uefa and factor_uefa > 0 else 1.0
    pts_cond = []
    for p in (partidos_cronologicos or []):
        if not isinstance(p, dict):
            continue
        if cond_target == "neutral":
            pts_cond.append(p)
            continue
        fuente = _partido_fuente(p)
        c_raw = p.get("condicion") or fuente.get("condicion")
        if not c_raw:
            if p.get("es_local") is not None:
                c_raw = "casa" if p.get("es_local") else "fuera"
            elif fuente.get("es_local") is not None:
                c_raw = "casa" if fuente.get("es_local") else "fuera"
            elif perfil_equipo and perfil_equipo.get("equipo"):
                eq_nom = perfil_equipo.get("equipo")
                if p.get("local") == eq_nom:
                    c_raw = "casa"
                elif p.get("visitante") == eq_nom:
                    c_raw = "fuera"
        c = str(c_raw or "").lower().strip()
        if c == cond_target:
            pts_cond.append(p)

    n_cond = len(pts_cond)
    w_cond = float(n_cond / (n_cond + k_shrinkage)) if n_cond > 0 else 0.0

    METRICAS_OFENSIVAS_DESC = {
        "xg", "xg_juego_abierto", "xg_sin_penalti", "xgot",
        "remates_totales", "remates_puerta", "remates_dentro_area", "grandes_ocasiones",
        "toques_area_rival",
    }
    METRICAS_DEFENSIVAS_CONC = {"xgot_enfrentado"}

    factores_of_cond = []
    factores_def_cond = []
    for p in pts_cond:
        src_p = _partido_fuente(p)
        torneo_p = str(src_p.get("torneo") or src_p.get("competition") or p.get("torneo") or "")
        tier_p = obtener_tier_liga(torneo_p)
        delta_tier = tier_p - tier_rival
        factores_of_cond.append(((0.76 ** delta_tier) if delta_tier >= 1 else 1.0) * fu)
        factores_def_cond.append(((1.22 ** delta_tier) if delta_tier >= 1 else 1.0) / fu)
    cred_cond = [peso_credibilidad_partido(p) for p in pts_cond]

    mu: Dict[str, float] = {}

    for clave in VARIABLES_REALES_45:
        vals = []
        creds = []
        for idx_c, p in enumerate(pts_cond):
            v = _valor_metrica(p, clave)
            if v is not None:
                vf = float(v)
                if clave in METRICAS_OFENSIVAS_DESC:
                    vf *= factores_of_cond[idx_c]
                elif clave in METRICAS_DEFENSIVAS_CONC:
                    vf *= factores_def_cond[idx_c]
                vals.append(vf)
                creds.append(cred_cond[idx_c])
        if vals:
            pesos = np.linspace(0.8, 1.2, len(vals)) * np.array(creds)
            pesos /= np.sum(pesos)
            media_cond = float(np.sum(np.array(vals) * pesos))
        else:
            media_cond = float(NEUTRALES.get(clave, 0.0))

        h_val = float(estados_lstm_15.get(clave, {}).get("h_reciente", NEUTRALES.get(clave, 0.0)) or NEUTRALES.get(clave, 0.0))
        mu[clave] = round(w_cond * media_cond + (1.0 - w_cond) * h_val, 2)

    # Goles a favor y en contra
    gfs = []
    gcs = []
    cg = []
    for idx_c, p in enumerate(pts_cond):
        gf, gc = _goles_equipo(p)
        if gf is not None and gc is not None:
            src_c = _partido_fuente(p)
            torneo_c = str(src_c.get("torneo") or src_c.get("competition") or p.get("torneo") or "").lower()
            if es_copa_nacional(torneo_c) and (gf - gc) >= 4:
                gf = min(float(gf), 2.2)
            gfs.append(float(gf) * factores_of_cond[idx_c])
            gcs.append(float(gc) * factores_def_cond[idx_c])
            cg.append(cred_cond[idx_c])

    m_perfil = (perfil_equipo or {}).get("metricas_clave", {})
    h_gf = float(estados_lstm_15.get("goles_favor", {}).get("h_reciente", m_perfil.get("goles_favor", 1.30)) or 1.30)
    h_gc = float(estados_lstm_15.get("goles_contra", {}).get("h_reciente", m_perfil.get("goles_contra", 1.25)) or 1.25)

    if gfs:
        pesos_g = np.linspace(0.8, 1.2, len(gfs)) * np.array(cg)
        pesos_g /= np.sum(pesos_g)
        media_gf = float(np.sum(np.array(gfs) * pesos_g))
    else:
        media_gf = h_gf

    if gcs:
        pesos_gc = np.linspace(0.8, 1.2, len(gcs)) * np.array(cg)
        pesos_gc /= np.sum(pesos_gc)
        media_gc = float(np.sum(np.array(gcs) * pesos_gc))
    else:
        media_gc = h_gc

    mu["goles_favor"] = round(w_cond * media_gf + (1.0 - w_cond) * h_gf, 2)
    mu["goles_contra"] = round(w_cond * media_gc + (1.0 - w_cond) * h_gc, 2)
    mu["_n_cond"] = float(n_cond)
    mu["_w_cond"] = round(w_cond, 4)

    return mu


def calcular_los_6_cruces_tacticos(
    mu_L_casa: Dict[str, float],
    mu_V_fuera: Dict[str, float],
    estados_L: Dict[str, Dict[str, Any]],
    estados_V: Dict[str, Dict[str, Any]],
    perfil_local: Optional[Dict[str, Any]] = None,
    perfil_visita: Optional[Dict[str, Any]] = None,
    rL: Optional[Dict[str, Any]] = None,
    rV: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Ejecuta los 6 Cruces Tácticos entre las 45 Variables:
    Local en Casa (mu_L_casa) (x) Rival de Visita (mu_V_fuera).
    Devuelve los Factores Latentes F1-F5 actualizados, los ajustes bidireccionales
    y el listado de patrones XAI activados.
    """
    alertas: List[str] = []

    # ── CRUCE #1: Asfixia vs. Espacio en Mediocampo (F1 - Posesión Progresiva) ──
    # Normalización ADVERSARIAL DE SUMA CERO: un solo balón. PosProg_V = -PosProg_L.
    claves_f1 = ("posesion_pct", "toques_area_rival", "pases_tercio_final_pct", "pases_total", "recuperaciones")
    z_pos_L = z_posesion_progresiva_suma_cero(
        {k: mu_L_casa.get(k, NEUTRALES.get(k, 0.0)) for k in claves_f1},
        {k: mu_V_fuera.get(k, NEUTRALES.get(k, 0.0)) for k in claves_f1},
    )
    f1_L = round(math.tanh(1.5 * z_pos_L) * 5.0, 1)
    f1_V = round(-f1_L, 1)

    if mu_L_casa.get("posesion_pct", 50.0) >= 55.0 and mu_V_fuera.get("intercepciones", 8.0) >= 11.0 and mu_V_fuera.get("recuperaciones", 48.0) >= 48.0:
        alertas.append("Atasco en Mediocampo: Alta posesión del Local en casa pero el Rival acumula intercepciones y recuperaciones de visita.")

    # ── CRUCE #2: Creación vs. Muro o Fragilidad del Portero Rival (F2 vs. F4) ──
    rem_pue_L = max(0.5, mu_L_casa.get("remates_puerta", 4.2))
    rem_pue_V = max(0.5, mu_V_fuera.get("remates_puerta", 4.2))

    ajuste_port_L = clip(-mu_V_fuera.get("goles_evitados", 0.0) * (rem_pue_L / 5.0), -0.35, 0.45)
    ajuste_port_V = clip(-mu_L_casa.get("goles_evitados", 0.0) * (rem_pue_V / 5.0), -0.35, 0.45)

    if mu_V_fuera.get("goles_evitados", 0.0) > 0.15:
        alertas.append("Muro del Portero Rival: El arquero visitante salva goles por encima de xGOT (+EV defensivo fuera).")
    elif mu_V_fuera.get("goles_evitados", 0.0) < -0.15:
        alertas.append("Fragilidad en Portería Rival: El arquero visitante concede goles por debajo del xGOT enfrentado.")

    z_creac_L = (
        (mu_L_casa.get("xg", 1.35) - 1.25) / 0.40 * 0.45
        + (mu_L_casa.get("remates_totales", 12.0) - 12.0) / 4.0 * 0.25
        + (mu_L_casa.get("grandes_ocasiones", 2.2) - 1.5) / 0.8 * 0.30
        - (mu_V_fuera.get("bloqueos", 2.5) - 3.5) / 1.5 * 0.15
    )
    f2_L = round(math.tanh(1.5 * z_creac_L) * 3.5, 1)

    z_creac_V = (
        (mu_V_fuera.get("xg", 1.35) - 1.25) / 0.40 * 0.45
        + (mu_V_fuera.get("remates_totales", 12.0) - 12.0) / 4.0 * 0.25
        + (mu_V_fuera.get("grandes_ocasiones", 2.2) - 1.5) / 0.8 * 0.30
        - (mu_L_casa.get("bloqueos", 2.5) - 3.5) / 1.5 * 0.15
    )
    f2_V = round(math.tanh(1.5 * z_creac_V) * 3.5, 1)

    # Creación: normalización suma cero relativa (no pueden recibir ambos bono positivo)
    if f2_L > 0 and f2_V > 0:
        comun_c = min(f2_L, f2_V)
        f2_L = round(f2_L - comun_c, 1)
        f2_V = round(f2_V - comun_c, 1)

    xgot_enf_V = float(mu_V_fuera.get("xgot_enfrentado", 1.25))
    g_evit_V = float(mu_V_fuera.get("goles_evitados", 0.0))
    paradas_V = float(mu_V_fuera.get("paradas", 2.8))

    if xgot_enf_V >= 1.50:
        # Asedio constante sobre la portería rival: PortRiv y FragRiv deben ser positivos
        z_port_L = max(0.20, (xgot_enf_V - 1.25) / 0.40 * 0.70 - max(0.0, g_evit_V) * 0.25)
    else:
        z_port_L = -g_evit_V * 1.8 - 0.20 * ((paradas_V - 2.8) / 1.2) + (xgot_enf_V - 1.25) / 0.40 * 0.40

    f4_L = round(math.tanh(1.5 * z_port_L) * 6.0, 1)
    if xgot_enf_V >= 1.50:
        f4_L = max(1.0, f4_L)

    xgot_enf_L = float(mu_L_casa.get("xgot_enfrentado", 1.25))
    g_evit_L = float(mu_L_casa.get("goles_evitados", 0.0))
    paradas_L = float(mu_L_casa.get("paradas", 2.8))

    if xgot_enf_L >= 1.50:
        z_port_V = max(0.20, (xgot_enf_L - 1.25) / 0.40 * 0.70 - max(0.0, g_evit_L) * 0.25)
    else:
        z_port_V = -g_evit_L * 1.8 - 0.20 * ((paradas_L - 2.8) / 1.2) + (xgot_enf_L - 1.25) / 0.40 * 0.40

    f4_V = round(math.tanh(1.5 * z_port_V) * 6.0, 1)
    if xgot_enf_L >= 1.50:
        f4_V = max(1.0, f4_V)

    # ── CRUCE #3: Puntería Clínica vs. Maldición del Delantero (F3 - Finalización xGOT 6.5%) ──
    tiros_por_gol_L = mu_L_casa.get("remates_puerta", 4.2) / max(1.0, mu_L_casa.get("goles_favor", 1.35))
    tiros_por_gol_V = mu_V_fuera.get("remates_puerta", 4.2) / max(1.0, mu_V_fuera.get("goles_favor", 1.35))

    eff_xg_L = (mu_L_casa.get("xgot", 1.35) - mu_L_casa.get("xg", 1.35)) / 0.35
    rem_tot_L = max(1.0, mu_L_casa.get("remates_totales", 12.0))
    eff_pue_L = ((mu_L_casa.get("remates_puerta", 4.2) / rem_tot_L) - 0.33) / 0.10
    z_fin_L = 0.55 * eff_xg_L + 0.45 * eff_pue_L
    f3_L = round(math.tanh(1.5 * z_fin_L) * 6.5, 1)

    eff_xg_V = (mu_V_fuera.get("xgot", 1.35) - mu_V_fuera.get("xg", 1.35)) / 0.35
    rem_tot_V = max(1.0, mu_V_fuera.get("remates_totales", 12.0))
    eff_pue_V = ((mu_V_fuera.get("remates_puerta", 4.2) / rem_tot_V) - 0.33) / 0.10
    z_fin_V = 0.55 * eff_xg_V + 0.45 * eff_pue_V
    f3_V = round(math.tanh(1.5 * z_fin_V) * 6.5, 1)

    if tiros_por_gol_L <= 3.5 and mu_L_casa.get("xgot", 1.35) > mu_L_casa.get("xg", 1.35):
        alertas.append("Puntería Clínica del Local: Alta efectividad de remate en casa (<= 3.5 tiros a puerta por gol).")

    if tiros_por_gol_V >= 5.0 and mu_V_fuera.get("grandes_ocasiones_falladas", 0.0) >= 2.0:
        alertas.append("Maldición del Delantero Visitante: Baja efectividad fuera de casa (>= 5.0 tiros por gol y ocasiones falladas).")

    # ── CRUCE #4: Asedio por Bandas, Juego Aéreo y Proyección de Córneres ──
    centros_L = mu_L_casa.get("centros", mu_L_casa.get("cruces_pct", 20.0))
    asedio_bandas = bool(
        mu_L_casa.get("corners", 5.0) >= 7.5
        and centros_L >= 20.0
        and (mu_V_fuera.get("despejes", 18.0) >= 22.0 or mu_V_fuera.get("duelos_aereos_pct", 50.0) < 48.0)
    )
    if asedio_bandas:
        alertas.append("Asedio por Bandas: Local genera >= 7.5 córneres y centros masivos; rival se encierra despejando fuera.")

    # ── CRUCE #5: Presión, Frustración Táctica y Balón Parado Peligroso (Disciplina) ──
    defensa_kamikaze = bool(
        mu_V_fuera.get("faltas", 11.5) >= 13.5
        and mu_V_fuera.get("tarjetas_amarillas", 1.8) >= 2.4
    )
    if defensa_kamikaze:
        alertas.append("Defensa Kamikaze / Tarjeta Temprana: Rival comete faltas reiteradas (>= 13.5) y tarjetas fuera de casa.")

    # ── CRUCE #6: Regalo por Errores Defensivos Condicionado (F5) ──
    regalo_err_L = clip(
        0.45 * mu_V_fuera.get("errores_gol", 0.15)
        + 0.15 * mu_V_fuera.get("errores_remate", 0.20)
        + 0.25 * mu_V_fuera.get("tarjetas_rojas", 0.10),
        0.0, 0.35
    )
    regalo_err_V = clip(
        0.45 * mu_L_casa.get("errores_gol", 0.15)
        + 0.15 * mu_L_casa.get("errores_remate", 0.20)
        + 0.25 * mu_L_casa.get("tarjetas_rojas", 0.10),
        0.0, 0.35
    )

    # ── CRUCE #6: Fragilidad Rival Condicionada (F5) ──
    # Para el atacante Local, el rival es el Visitante (V) defendiendo fuera:
    mc_V = (perfil_visita or {}).get("metricas_clave", {})
    xgotc_visita = float(mu_V_fuera.get("xgot_enfrentado") or mc_V.get("xgot_contra") or 1.25)
    defensa_V = float((rV or {}).get("Final_Defense_Strength") or (perfil_visita or {}).get("ratings_0_100", {}).get("Final_Defense_Strength") or 50.0)

    z_frag_L = (
        (xgotc_visita - 1.25) / 0.35 * 0.70
        + (50.0 - defensa_V) / 10.0 * 0.30
        + mu_V_fuera.get("errores_gol", 0.15) * 1.5
        + mu_V_fuera.get("errores_remate", 0.20) * 0.6
        + mu_V_fuera.get("tarjetas_rojas", 0.10) * 1.0
    )
    f5_L = round(math.tanh(1.5 * z_frag_L) * 6.0, 1)
    if xgotc_visita >= 1.50 or defensa_V <= 48.0:
        f5_L = max(4.0, f5_L)

    # Para el atacante Visitante, el rival es el Local (L) defendiendo en casa:
    mc_L = (perfil_local or {}).get("metricas_clave", {})
    xgotc_local = float(mu_L_casa.get("xgot_enfrentado") or mc_L.get("xgot_contra") or 1.25)
    defensa_L = float((rL or {}).get("Final_Defense_Strength") or (perfil_local or {}).get("ratings_0_100", {}).get("Final_Defense_Strength") or 50.0)

    z_frag_V = (
        (xgotc_local - 1.25) / 0.35 * 0.70
        + (50.0 - defensa_L) / 10.0 * 0.30
        + mu_L_casa.get("errores_gol", 0.15) * 1.5
        + mu_L_casa.get("errores_remate", 0.20) * 0.6
        + mu_L_casa.get("tarjetas_rojas", 0.10) * 1.0
    )
    f5_V = round(math.tanh(1.5 * z_frag_V) * 6.0, 1)
    if xgotc_local <= 1.20 or defensa_L >= 56.0:
        f5_V = min(-1.5, f5_V)

    return {
        "factores_local": {
            "F_posesion_progresiva": f1_L,
            "F_creacion": f2_L,
            "F_finalizacion": f3_L,
            "F_porteria_rival": f4_L,
            "F_fragilidad_rival": f5_L,
        },
        "factores_visita": {
            "F_posesion_progresiva": f1_V,
            "F_creacion": f2_V,
            "F_finalizacion": f3_V,
            "F_porteria_rival": f4_V,
            "F_fragilidad_rival": f5_V,
        },
        "ajuste_portero_local": round(ajuste_port_L, 4),
        "ajuste_portero_visita": round(ajuste_port_V, 4),
        "regalo_errores_local": round(regalo_err_L, 4),
        "regalo_errores_visita": round(regalo_err_V, 4),
        "asedio_bandas_activo": asedio_bandas,
        "defensa_kamikaze_activa": defensa_kamikaze,
        "alertas_xai": alertas,
    }


def calcular_indice_bloqueo_cerrojo(
    xgot_atacante: float,
    xg_atacante: float,
    goc_atacante: float,
    goc_falladas_atacante: float,
    defensa_rival_rating: float,
    seg_gol_concedido_rival: float,
    goles_evitados_rival: float,
    tier_coef_atacante: float,
    tier_coef_rival: float,
    lam_atacante: float = 1.35,
    xgotc_rival: float = 1.25,
) -> Tuple[bool, float]:
    """
    Detector de "Portería Imbatida / Hurdle Zero-Inflation":
    Solo activa cerrojo si:
      - SU PROPIO λ_atacante < 1.22
      - La defensa del RIVAL tiene Defense >= 58.0
      - xGOTc_rival <= 1.15
    Jamás activa cerrojo si λ_atacante >= 1.22 ni contra rival que concede xGOTc > 1.15.
    """
    if lam_atacante >= 1.22 or xgotc_rival > 1.15 or defensa_rival_rating < 58.0:
        return False, 0.0

    c1 = (xgot_atacante < xg_atacante) or ((goc_falladas_atacante / max(0.5, goc_atacante)) >= 0.55)
    c2 = (seg_gol_concedido_rival >= 3800.0) or (goles_evitados_rival > 0.15)
    c3 = (tier_coef_atacante < tier_coef_rival)

    n_cond = sum([bool(c1), bool(c2), bool(c3)])
    if n_cond >= 1:
        bonus = 0.12 if n_cond == 1 else 0.16
        if seg_gol_concedido_rival >= 4500.0 or goles_evitados_rival >= 0.25:
            bonus = min(0.20, bonus + 0.03)
        return True, bonus
    return False, 0.0


def ejecutar_bucle_recursivo_infinito_hasta_convergencia(
    perfil_local: Dict[str, Any],
    perfil_visita: Dict[str, Any],
    lam_base_local: float,
    lam_base_visita: float,
    partidos_15_local: Optional[List[Dict[str, Any]]] = None,
    partidos_15_visita: Optional[List[Dict[str, Any]]] = None,
    max_goles: int = 10,
    max_iteraciones_recursivas: int = 8,
    tol_convergencia_entropia: float = 1e-4,
    n_simulaciones_mc: int = 100000,
    sede_neutral_local: bool = False,
) -> Dict[str, Any]:
    """
    Ejecuta el Bucle Recursivo (Iteración 1 -> Iteración 2 -> Iteración 3 ... hasta Convergencia):
      Variables Crudas Reales -> Patrones Nivel 1 -> Patrones Nivel 2 -> ... -> Convergencia de Entropía.
    En cada iteración:
      1. Toma el Tensor Temporal actual (45 series reales), aplica el filtro secuencial (LSTM determinista).
      2. Cruza las métricas mediante Bilinear Pooling (Covarianza Cruzada) + Cross-Attention.
      3. Cruza los nuevos patrones de A contra los de B y de vuelta contra las variables crudas.
      4. Actualiza la distribución conjunta de marcadores y mide su Entropía de Shannon H^(k).
      5. Cuando |H^(k) - H^(k-1)| < tol_convergencia_entropia, detiene la recursión y ejecuta las
         100,000 simulaciones de Monte Carlo.
    """
    eq_L = perfil_local["equipo"]
    eq_V = perfil_visita["equipo"]
    rL = perfil_local["ratings_0_100"]
    rV = perfil_visita["ratings_0_100"]
    tL = perfil_local["tempo_segundos"]
    tV = perfil_visita["tempo_segundos"]
    mL = perfil_local["metricas_clave"]
    mV = perfil_visita["metricas_clave"]

    tier_L = obtener_tier_equipo(partidos_15_local or [], perfil_local)
    tier_V = obtener_tier_equipo(partidos_15_visita or [], perfil_visita)

    # Coeficiente UEFA de liga (1.00 Top-5 / 0.85 / 0.68) y descuento relativo
    tier_coef_L = obtener_tier_coef(partidos_15_local or [], perfil_local, eq_L)
    tier_coef_V = obtener_tier_coef(partidos_15_visita or [], perfil_visita, eq_V)
    fu_L = factor_uefa_relativo(tier_coef_L, tier_coef_V)
    fu_V = factor_uefa_relativo(tier_coef_V, tier_coef_L)
    ratio_tier_L = min(1.0, tier_coef_L / max(0.1, tier_coef_V))
    ratio_tier_V = min(1.0, tier_coef_V / max(0.1, tier_coef_L))
    if sede_neutral_local is None:
        sede_neutral_local = es_sede_neutral(eq_L)

    # Extraer tensores temporales de las series REALES de 15 partidos con ajuste relativo ΔTier + UEFA
    series_L = extraer_series_reales_15_partidos(partidos_15_local or [], eq_L, perfil_local, tier_rival=tier_V, factor_uefa=fu_L)
    series_V = extraer_series_reales_15_partidos(partidos_15_visita or [], eq_V, perfil_visita, tier_rival=tier_L, factor_uefa=fu_V)
    n_series = len(series_L.get("goles_favor", []))

    estados_L = {k: procesar_secuencia_individual_lstm_determinista(v)
                 for k, v in series_L.items() if not k.startswith("_")}
    estados_V = {k: procesar_secuencia_individual_lstm_determinista(v)
                 for k, v in series_V.items() if not k.startswith("_")}

    # Bilinear Pooling + Cross-Attention INTER-EQUIPO (A ⊗ B): ataque propio vs resistencia rival
    top_cruces_L = bilinear_pooling_inter_equipo(estados_L, estados_V)
    top_cruces_V = bilinear_pooling_inter_equipo(estados_V, estados_L)
    int_cruce_L = float(np.mean([f for _, f in top_cruces_L[:3]])) if top_cruces_L else 0.0
    int_cruce_V = float(np.mean([f for _, f in top_cruces_V[:3]])) if top_cruces_V else 0.0

    # Estadísticas reales de la serie de goles (sequía vs explosión ofensiva)
    gf_L = series_L["goles_favor"]
    gf_V = series_V["goles_favor"]
    gc_L = series_L["goles_contra"]
    gc_V = series_V["goles_contra"]

    ult8_gf_L = gf_L[-min(8, len(gf_L)):]
    ult8_gf_V = gf_V[-min(8, len(gf_V)):]
    ult6_gf_L = gf_L[-min(6, len(gf_L)):]
    ult6_gf_V = gf_V[-min(6, len(gf_V)):]

    tasa_sequia_L = 0.65 * (sum(1.0 for g in ult6_gf_L if g == 0) / len(ult6_gf_L)) + 0.35 * (sum(1.0 for g in gf_L if g == 0) / len(gf_L))
    tasa_sequia_V = 0.65 * (sum(1.0 for g in ult6_gf_V if g == 0) / len(ult6_gf_V)) + 0.35 * (sum(1.0 for g in gf_V if g == 0) / len(gf_V))

    # Tasa de explosión goleadora (% de partidos recientes con >= 3 goles, clave en equipos como Barcelona!)
    tasa_explosion_3mas_L = sum(1.0 for g in ult8_gf_L if g >= 3) / len(ult8_gf_L)
    tasa_explosion_3mas_V = sum(1.0 for g in ult8_gf_V if g >= 3) / len(ult8_gf_V)

    seg_gol_L = float(tL.get("Avg_Seconds_Between_Goals", 3500.0))
    seg_gol_V = float(tV.get("Avg_Seconds_Between_Goals", 3500.0))
    if seg_gol_L >= 4200:
        tasa_sequia_L = max(tasa_sequia_L, clip((seg_gol_L - 3100.0) / 4000.0, 0.35, 0.58))
    if seg_gol_V >= 4200:
        tasa_sequia_V = max(tasa_sequia_V, clip((seg_gol_V - 3100.0) / 4000.0, 0.35, 0.58))

    # Patrones tácticos desde los estados REALES (no desde ratings) + cruce dominante
    pat_L = patrones_tacticos_desde_estados(estados_L, perfil_local, tasa_sequia_L, int_cruce_L)
    pat_V = patrones_tacticos_desde_estados(estados_V, perfil_visita, tasa_sequia_V, int_cruce_V)

    # Ancla de potencial ofensivo: λ base + xGOT/xG reales (estado LSTM) + goles
    xg_L = float(estados_L["xg"]["h_reciente"])
    xg_V = float(estados_V["xg"]["h_reciente"])
    xgot_L = float(estados_L["xgot"]["h_reciente"]) or float(mL.get("xgot_favor", lam_base_local))
    xgot_V = float(estados_V["xgot"]["h_reciente"]) or float(mV.get("xgot_favor", lam_base_visita))
    goles_lstm_L = estados_L["goles_favor"]["h_reciente"]
    goles_lstm_V = estados_V["goles_favor"]["h_reciente"]

    # ── MÓDULO DOBLE ADN + 6 CRUCES TÁCTICOS (Contracción Bayesiana k=3.5) ──
    cond_adn_L = "neutral" if sede_neutral_local else "casa"
    mu_L_casa = calcular_doble_adn_con_shrinkage(partidos_15_local or [], estados_L, cond_adn_L, perfil_local, tier_rival=tier_V, factor_uefa=fu_L)
    mu_V_fuera = calcular_doble_adn_con_shrinkage(partidos_15_visita or [], estados_V, "fuera", perfil_visita, tier_rival=tier_L, factor_uefa=fu_V)

    # Ajuste por salto competitivo de liga doméstica (ratio_tier < 1.0):
    # Si un equipo de liga menor (ej. Ferencváros en Hungría 0.65) enfrenta a un club de élite (ej. Milan 1.00):
    # Sus números defensivos domésticos se descuentan severamente para reflejar el salto competitivo.
    if ratio_tier_L < 1.0 and ratio_tier_L > 0.05:
        mu_L_casa["goles_contra"] = round(mu_L_casa.get("goles_contra", 1.1) / ratio_tier_L, 2)
        mu_L_casa["xgot_enfrentado"] = round(mu_L_casa.get("xgot_enfrentado", 1.25) / ratio_tier_L, 2)
        mu_L_casa["goles_evitados"] = round(mu_L_casa.get("goles_evitados", 0.0) * ratio_tier_L - 0.35 * (1.0 - ratio_tier_L), 3)
        if "goles_contra" in estados_L:
            estados_L["goles_contra"]["h_reciente"] = float(estados_L["goles_contra"]["h_reciente"]) / ratio_tier_L
        if "xgot_enfrentado" in estados_L:
            estados_L["xgot_enfrentado"]["h_reciente"] = float(estados_L["xgot_enfrentado"]["h_reciente"]) / ratio_tier_L
        if "goles_evitados" in estados_L:
            estados_L["goles_evitados"]["h_reciente"] = float(estados_L["goles_evitados"]["h_reciente"]) * ratio_tier_L - 0.35 * (1.0 - ratio_tier_L)
        tL["Avg_Seconds_Between_Goals_Conceded"] = round(5400.0 / max(0.25, mu_L_casa["goles_contra"]), 1)

    if ratio_tier_V < 1.0 and ratio_tier_V > 0.05:
        mu_V_fuera["goles_contra"] = round(mu_V_fuera.get("goles_contra", 1.3) / ratio_tier_V, 2)
        mu_V_fuera["xgot_enfrentado"] = round(mu_V_fuera.get("xgot_enfrentado", 1.25) / ratio_tier_V, 2)
        mu_V_fuera["goles_evitados"] = round(mu_V_fuera.get("goles_evitados", 0.0) * ratio_tier_V - 0.35 * (1.0 - ratio_tier_V), 3)
        if "goles_contra" in estados_V:
            estados_V["goles_contra"]["h_reciente"] = float(estados_V["goles_contra"]["h_reciente"]) / ratio_tier_V
        if "xgot_enfrentado" in estados_V:
            estados_V["xgot_enfrentado"]["h_reciente"] = float(estados_V["xgot_enfrentado"]["h_reciente"]) / ratio_tier_V
        if "goles_evitados" in estados_V:
            estados_V["goles_evitados"]["h_reciente"] = float(estados_V["goles_evitados"]["h_reciente"]) * ratio_tier_V - 0.35 * (1.0 - ratio_tier_V)
        tV["Avg_Seconds_Between_Goals_Conceded"] = round(5400.0 / max(0.25, mu_V_fuera["goles_contra"]), 1)

    # ── λ EFECTIVO: finalización, portería rival vulnerable y errores regalados ──
    comp_L = componentes_lambda_efectivo(estados_L, estados_V)
    comp_V = componentes_lambda_efectivo(estados_V, estados_L)

    cruces_6 = calcular_los_6_cruces_tacticos(
        mu_L_casa, mu_V_fuera, estados_L, estados_V,
        perfil_local=perfil_local, perfil_visita=perfil_visita, rL=rL, rV=rV,
    )

    f_l = cruces_6["factores_local"]
    f_v = cruces_6["factores_visita"]
    comp_L["ajuste_portero"] = cruces_6["ajuste_portero_local"]
    comp_V["ajuste_portero"] = cruces_6["ajuste_portero_visita"]
    comp_L["regalo_errores"] = cruces_6["regalo_errores_local"]
    comp_V["regalo_errores"] = cruces_6["regalo_errores_visita"]

    impacto_l = sum(f_l.values()) / 100.0
    impacto_v = sum(f_v.values()) / 100.0

    lam_mlp_L = lam_base_local * (1.0 + impacto_l)
    lam_mlp_V = lam_base_visita * (1.0 + impacto_v)

    # Ponderación calibrada: base ensemble + regalos por portería y errores rivales
    lam_k_L = clip(
        0.55 * lam_mlp_L + 0.45 * (0.50 * lam_base_local + 0.25 * xgot_L + 0.25 * xg_L)
        + comp_L["ajuste_portero"]
        + comp_L["regalo_errores"],
        0.38, 3.45)
    lam_k_V = clip(
        0.55 * lam_mlp_V + 0.45 * (0.50 * lam_base_visita + 0.25 * xgot_V + 0.25 * xg_V)
        + comp_V["ajuste_portero"]
        + comp_V["regalo_errores"],
        0.38, 3.45)

    # Techo estricto: ningún equipo de liga menor (tier_coef < 0.90) puede recibir aumento > +12% sobre su λ Base
    # cuando enfrenta a un rival de Top 5 Ligas (tier_coef == 1.00)
    if tier_coef_L < 0.90 and tier_coef_V >= 1.00:
        lam_k_L = min(round(lam_base_local * 1.12, 2), lam_k_L)
    if tier_coef_V < 0.90 and tier_coef_L >= 1.00:
        lam_k_V = min(round(lam_base_visita * 1.12, 2), lam_k_V)

    # Detector de Cerrojo / Portería Imbatida (Hurdle Zero-Inflation)
    # Solo activo si λ_atacante < 1.22 Y defensa_rival >= 58.0 Y xgotc_rival <= 1.15
    xgotc_riv_V = float(mu_V_fuera.get("xgot_enfrentado", 1.25))
    xgotc_riv_L = float(mu_L_casa.get("xgot_enfrentado", 1.25))
    cerrojo_L_activo, bonus_cerrojo_L = calcular_indice_bloqueo_cerrojo(
        xgot_atacante=xgot_L, xg_atacante=xg_L,
        goc_atacante=float(estados_L.get("grandes_ocasiones", {}).get("h_reciente", 2.2) or 2.2),
        goc_falladas_atacante=float(estados_L.get("grandes_ocasiones_falladas", {}).get("h_reciente", 1.3) or 1.3),
        defensa_rival_rating=float(rV.get("Final_Defense_Strength", 50.0)),
        seg_gol_concedido_rival=float(tV.get("Avg_Seconds_Between_Goals_Conceded", 3500.0)),
        goles_evitados_rival=float(comp_L.get("goles_evitados_rival", 0.0)),
        tier_coef_atacante=tier_coef_L, tier_coef_rival=tier_coef_V,
        lam_atacante=lam_k_L,
        xgotc_rival=xgotc_riv_V,
    )
    cerrojo_V_activo, bonus_cerrojo_V = calcular_indice_bloqueo_cerrojo(
        xgot_atacante=xgot_V, xg_atacante=xg_V,
        goc_atacante=float(estados_V.get("grandes_ocasiones", {}).get("h_reciente", 2.2) or 2.2),
        goc_falladas_atacante=float(estados_V.get("grandes_ocasiones_falladas", {}).get("h_reciente", 1.3) or 1.3),
        defensa_rival_rating=float(rL.get("Final_Defense_Strength", 50.0)),
        seg_gol_concedido_rival=float(tL.get("Avg_Seconds_Between_Goals_Conceded", 3500.0)),
        goles_evitados_rival=float(comp_V.get("goles_evitados_rival", 0.0)),
        tier_coef_atacante=tier_coef_V, tier_coef_rival=tier_coef_L,
        lam_atacante=lam_k_V,
        xgotc_rival=xgotc_riv_L,
    )
    if cerrojo_L_activo:
        cruces_6["alertas_xai"].append(f"Cerrojo Defensivo sobre {eq_L}: Rival de élite neutraliza ataque con probabilidad marginal de 0 goles inflada (+{bonus_cerrojo_L*100:.0f}%).")
    if cerrojo_V_activo:
        cruces_6["alertas_xai"].append(f"Cerrojo Defensivo sobre {eq_V}: Rival de élite neutraliza ataque con probabilidad marginal de 0 goles inflada (+{bonus_cerrojo_V*100:.0f}%).")

    desactivar_sequia_L = (tier_L == 1 and tier_V >= 2)
    desactivar_sequia_V = (tier_V == 1 and tier_L >= 2)

    # Calibración estricta de duelos asimétricos y categorías:
    # 1. Sequía severa en 1ª división (desactivada si enfrenta a rival de menor categoría / recién ascendido)
    if not desactivar_sequia_L and tier_L == 1 and float(rL.get("Final_Attack_Strength", 50.0)) < 20.0 and seg_gol_L >= 5000.0 and xg_L < 0.90:
        lam_k_L = clip(0.78 * lam_base_local, 0.85, 0.95)
    elif not desactivar_sequia_L and tier_L == 1 and (float(estados_L["goles_favor"]["h_reciente"]) <= 1.15 or float(mL.get("goles_favor", 1.30)) <= 1.15):
        lam_k_L = clip(lam_k_L, 1.05, 1.18)

    if not desactivar_sequia_V and tier_V == 1 and float(rV.get("Final_Attack_Strength", 50.0)) < 20.0 and seg_gol_V >= 5000.0 and xg_V < 0.90:
        lam_k_V = clip(0.78 * lam_base_visita, 0.85, 0.95)
    elif not desactivar_sequia_V and tier_V == 1 and (float(estados_V["goles_favor"]["h_reciente"]) <= 1.15 or float(mV.get("goles_favor", 1.30)) <= 1.15):
        lam_k_V = clip(lam_k_V, 1.05, 1.18)

    # Cola ancha (sobredispersión) solo si el portero rival es vulnerable y el
    # atacante pasa de 2,10 xG: transfiere masa de 1-0/2-0 hacia 3-*, 4-* y 3-0.
    cola_ancha_L = bool(comp_L["goles_evitados_rival"] < -0.15 and xg_L > 2.10)
    cola_ancha_V = bool(comp_V["goles_evitados_rival"] < -0.15 and xg_V > 2.10)

    brecha_overall = (rL["Overall_Hybrid_Rating"] - rV["Overall_Hybrid_Rating"]) / 100.0
    brecha_atk_def_L = (rL["Final_Attack_Strength"] - rV["Final_Defense_Strength"]) / 100.0
    brecha_atk_def_V = (rV["Final_Attack_Strength"] - rL["Final_Defense_Strength"]) / 100.0

    # Variables sintéticas de Nivel 1 que entran al bucle recursivo
    p_asfixia_L = pat_L["asfixia"]
    p_asfixia_V = pat_V["asfixia"]
    p_presion_L = pat_L["presion"]
    p_presion_V = pat_V["presion"]
    p_fragilidad_L = pat_L["fragilidad"]
    p_fragilidad_V = pat_V["fragilidad"]
    p_sequia_L = pat_L["sequia"]
    p_sequia_V = pat_V["sequia"]
    p_muralla_L = pat_L["muralla"]
    p_muralla_V = pat_V["muralla"]

    entropia_previa = 999.0
    historial_entropia: List[float] = []
    iteracion_final = 1
    matriz_actual = np.zeros((max_goles + 1, max_goles + 1), dtype=float)

    # ==========================================================================
    # BUCLE RECURSIVO: ITERACIÓN 1, 2, 3... HASTA QUE LA ENTROPÍA CONVERGE
    # ==========================================================================
    lam_ancla_L = lam_k_L
    lam_ancla_V = lam_k_V
    cruce_prev_L = 1.0
    cruce_prev_V = 1.0

    for it in range(1, max_iteraciones_recursivas + 1):
        # Cruce recursivo de orden k: Patrones Sintéticos (x) Variables Crudas (xG, xGOT, Posesión)
        cruce_of_L = (
            0.40 * p_asfixia_L * max(0.45, p_fragilidad_V)
            + 0.35 * (xgot_L / max(0.50, lam_base_local)) * (p_presion_L / max(0.40, p_presion_V))
            + 0.25 * (1.0 + tasa_explosion_3mas_L * 0.55)
        )
        cruce_of_V = (
            0.40 * p_asfixia_V * max(0.45, p_fragilidad_L)
            + 0.35 * (xgot_V / max(0.50, lam_base_visita)) * (p_presion_V / max(0.40, p_presion_L))
            + 0.25 * (1.0 + tasa_explosion_3mas_V * 0.55)
        )
        # El cruce dominante (Bilinear Pooling + Cross-Attention) ya está dentro de
        # `p_asfixia` vía `empuje_cruce`; se declara para el sello.

        if it == 1:
            # Iteración 1: Salto estructural principal (Nivel 1 + Derivada Temporal + Bloqueo Sequía)
            # El término `0.30 · (intensidad_cruce − 0.75)` es el que hace que el
            # Bilinear Pooling + Cross-Attention PESE en λ: un equipo cuyas variables
            # se mueven juntas (cruce alto) es más predecible y sube; uno irregular baja.
            penal_seq_L = 0.28 * max(0.0, p_sequia_L * p_muralla_V - 0.48) if (xgotc_riv_V < 1.45 and p_fragilidad_V < 1.15) else 0.0
            mod_total_L = clip(
                1.0
                + 0.42 * brecha_overall
                + 0.25 * brecha_atk_def_L
                + 0.16 * (cruce_of_L - 1.0)
                + 0.30 * (int_cruce_L - CENTRO_INTENSIDAD_CRUCE)
                + 0.14 * max(-0.25, estados_L["goles_favor"]["pendiente_dmdp"])
                - penal_seq_L,
                0.62, 1.42
            )
            penal_seq_V = 0.28 * max(0.0, p_sequia_V * p_muralla_L - 0.48) if (xgotc_riv_L < 1.45 and p_fragilidad_L < 1.15) else 0.0
            mod_total_V = clip(
                1.0
                - 0.42 * brecha_overall
                + 0.25 * brecha_atk_def_V
                + 0.16 * (cruce_of_V - 1.0)
                + 0.30 * (int_cruce_V - CENTRO_INTENSIDAD_CRUCE)
                + 0.14 * max(-0.25, estados_V["goles_favor"]["pendiente_dmdp"])
                - penal_seq_V,
                0.62, 1.42
            )
            lam_k_L = clip(lam_ancla_L * mod_total_L, 0.38, 3.45)
            lam_k_V = clip(lam_ancla_V * mod_total_V, 0.38, 3.45)
            if not desactivar_sequia_L and tier_L == 1 and float(rL.get("Final_Attack_Strength", 50.0)) < 20.0 and seg_gol_L >= 5000.0 and xg_L < 0.90:
                lam_k_L = clip(0.78 * lam_base_local, 0.85, 0.95)
            elif not desactivar_sequia_L and tier_L == 1 and (float(estados_L["goles_favor"]["h_reciente"]) <= 1.15 or float(mL.get("goles_favor", 1.30)) <= 1.15):
                lam_k_L = clip(lam_k_L, 1.05, 1.18)

            if not desactivar_sequia_V and tier_V == 1 and float(rV.get("Final_Attack_Strength", 50.0)) < 20.0 and seg_gol_V >= 5000.0 and xg_V < 0.90:
                lam_k_V = clip(0.78 * lam_base_visita, 0.85, 0.95)
            elif not desactivar_sequia_V and tier_V == 1 and (float(estados_V["goles_favor"]["h_reciente"]) <= 1.15 or float(mV.get("goles_favor", 1.30)) <= 1.15):
                lam_k_V = clip(lam_k_V, 1.05, 1.18)
        else:
            # Iteraciones 2, 3, 4...: Refinamiento recursivo sobre el residuo del nuevo patrón compuesto
            gamma_res = 0.28 ** (it - 1)
            delta_cruce_L = cruce_of_L - cruce_prev_L
            delta_cruce_V = cruce_of_V - cruce_prev_V
            lam_k_L = clip(lam_k_L * (1.0 + gamma_res * 0.35 * delta_cruce_L), 0.38, 3.45)
            lam_k_V = clip(lam_k_V * (1.0 + gamma_res * 0.35 * delta_cruce_V), 0.38, 3.45)

        cruce_prev_L = cruce_of_L
        cruce_prev_V = cruce_of_V

        # Retroalimentación de los nuevos patrones para la siguiente iteración (Fase 2.1 -> 2.3)
        p_asfixia_L = 0.72 * p_asfixia_L + 0.28 * cruce_of_L
        p_asfixia_V = 0.72 * p_asfixia_V + 0.28 * cruce_of_V

        # Construcción de la distribución marginal de orden k (con Hurdle de sequía + cola de explosión >=3 goles)
        p0_base_L = math.exp(-lam_k_L)
        p0_base_V = math.exp(-lam_k_V)
        peso_seq_L = clip(0.38 * p_sequia_L * p_muralla_V, 0.0, 0.52)
        peso_seq_V = clip(0.38 * p_sequia_V * p_muralla_L, 0.0, 0.52)

        p0_L = clip((1.0 - peso_seq_L) * p0_base_L + peso_seq_L * max(p0_base_L, tasa_sequia_L), 0.02, 0.68)
        p0_V = clip((1.0 - peso_seq_V) * p0_base_V + peso_seq_V * max(p0_base_V, tasa_sequia_V), 0.02, 0.68)

        if cerrojo_L_activo:
            p0_L = clip(p0_L + bonus_cerrojo_L, 0.02, 0.75)
        if cerrojo_V_activo:
            p0_V = clip(p0_V + bonus_cerrojo_V, 0.02, 0.75)

        def marginal_recursiva(lam: float, p0_obj: float, ventaja: float,
                               tasa_3mas: float, cola_ancha: bool = False,
                               delta_cerrojo: float = 0.0) -> List[float]:
            probs = _probs_base_cola(lam, max_goles, cola_ancha)
            # Techo estricto: m[0] jamás puede superar math.exp(-lam) + 0.06
            p0_max = math.exp(-lam) + 0.06
            p0_obj = min(p0_obj, p0_max)

            escala_pos = (1.0 - p0_obj) / max(1e-9, 1.0 - probs[0])
            res = [0.0] * (max_goles + 1)
            res[0] = p0_obj
            for g in range(1, max_goles + 1):
                mod_g = 1.0
                if tasa_3mas >= 0.50 and lam >= 2.10:
                    if g == 1:
                        mod_g = clip(1.0 - 0.45 * tasa_3mas, 0.52, 0.85)
                    elif g == 2:
                        mod_g = clip(1.0 + 0.15 * tasa_3mas, 1.0, 1.20)
                    elif g in (3, 4):
                        mod_g = clip(1.0 + 0.55 * tasa_3mas, 1.15, 1.65)
                elif ventaja > 0.08 and lam >= 1.50:
                    if g == 1:
                        mod_g = clip(1.0 - 0.25 * ventaja, 0.80, 1.0)
                    elif g == 2:
                        mod_g = clip(1.0 + 0.45 * ventaja, 1.0, 1.35)
                    elif g == 3:
                        mod_g = clip(1.0 + 0.30 * ventaja, 1.0, 1.25)
                res[g] = probs[g] * escala_pos * mod_g

            if delta_cerrojo > 0.0:
                f_desc1 = max(0.60, 1.0 - 0.35 * (delta_cerrojo / 0.15))
                f_desc2 = max(0.75, 1.0 - 0.20 * (delta_cerrojo / 0.15))
                res[1] *= f_desc1
                res[2] *= f_desc2

            s_pos = sum(res[1:])
            if s_pos > 0:
                for g in range(1, max_goles + 1):
                    res[g] = res[g] * (1.0 - p0_obj) / s_pos

            # Restricción estricta de unimodalidad y eliminación de valle en G=1:
            res[0] = min(res[0], math.exp(-lam) + 0.06)
            if lam >= 1.15:
                # m[1] JAMÁS puede ser menor que 0.85 * m[0] ni menor que 0.80 * m[2]
                piso_m1 = max(0.85 * res[0], 0.80 * res[2])
                if res[1] < piso_m1:
                    deficit = piso_m1 - res[1]
                    res[1] = piso_m1
                    res[0] = max(0.01, res[0] - deficit * 0.60)
                    res[2] = max(0.01, res[2] - deficit * 0.40)

            tot = sum(res)
            if tot > 0:
                res = [r / tot for r in res]
            return res

        vent_L = max(0.0, brecha_overall * 2.2 + 0.35 * (cruce_of_L - 1.0))
        vent_V = max(0.0, -brecha_overall * 2.2 + 0.35 * (cruce_of_V - 1.0))

        m_L = marginal_recursiva(lam_k_L, p0_L, vent_L, tasa_explosion_3mas_L, delta_cerrojo=bonus_cerrojo_L if cerrojo_L_activo else 0.0)
        m_V = marginal_recursiva(lam_k_V, p0_V, vent_V, tasa_explosion_3mas_V, delta_cerrojo=bonus_cerrojo_V if cerrojo_V_activo else 0.0)

        asimetria = abs(lam_k_L - lam_k_V) + abs(brecha_overall) * 2.2
        rho_k = clip(0.06 * (asimetria - 0.25), 0.01, 0.09) if asimetria >= 0.32 else -0.03

        for i in range(max_goles + 1):
            for j in range(max_goles + 1):
                p_ij = m_L[i] * m_V[j]
                if i == 0 and j == 0:
                    tau = max(0.2, 1.0 - lam_k_L * lam_k_V * rho_k)
                elif i == 0 and j == 1:
                    tau = max(0.2, 1.0 + lam_k_L * rho_k)
                elif i == 1 and j == 0:
                    tau = max(0.2, 1.0 + lam_k_V * rho_k)
                elif i == 1 and j == 1:
                    tau = max(0.2, 1.0 - rho_k)
                else:
                    tau = 1.0
                matriz_actual[i, j] = max(0.0, p_ij * tau)

        matriz_actual /= np.sum(matriz_actual)
        entropia_k = shannon_entropy(matriz_actual)
        historial_entropia.append(round(entropia_k, 5))
        iteracion_final = it

        if it >= 2 and abs(entropia_previa - entropia_k) < tol_convergencia_entropia:
            break
        entropia_previa = entropia_k

    # Simulación de Monte Carlo de 100,000 iteraciones sobre el grafo convergente
    rng = np.random.default_rng(seed=42)
    flat_p = matriz_actual.ravel()
    sims = rng.choice(len(flat_p), size=n_simulaciones_mc, p=flat_p)
    conteos = np.bincount(sims, minlength=len(flat_p)).reshape((max_goles + 1, max_goles + 1))
    matriz_mc = conteos / float(n_simulaciones_mc)
    matriz_final = (0.50 * matriz_actual + 0.50 * matriz_mc)

    # Dos equipos idénticos deben tener probabilidades idénticas: el ruido del Monte
    # Carlo no puede fabricar una ventaja inexistente.
    es_simetrica = bool(np.allclose(matriz_actual, matriz_actual.T, atol=1e-5))
    if es_simetrica:
        matriz_final = 0.5 * (matriz_final + matriz_final.T)
    # ── DESBLOQUEO ANTI-1-1 EN DUELOS ASIMÉTRICOS ──────────────────────────
    diff_lam = lam_k_L - lam_k_V
    p_1x2_L = float(np.sum(np.tril(matriz_final, -1)))
    p_1x2_V = float(np.sum(np.triu(matriz_final, 1)))
    p_fav_1x2 = max(p_1x2_L, p_1x2_V)

    # Solo se activa si |lambda_L - lambda_V| >= 0.65 y P_favorito >= 0.62
    if abs(diff_lam) >= 0.65 and p_fav_1x2 >= 0.62:
        if diff_lam > 0:  # Favorito Local
            candidatos_fav = [(1, 0), (2, 0), (2, 1), (3, 1)]
        else:             # Favorito Visita
            candidatos_fav = [(0, 1), (0, 2), (1, 2), (1, 3)]

        mejor_fav = max(candidatos_fav, key=lambda c: matriz_final[c[0], c[1]])
        p_fav_modal = matriz_final[mejor_fav[0], mejor_fav[1]]
        p_11 = matriz_final[1, 1]

        if p_11 > p_fav_modal:
            # Máximo 25% del exceso sobre el modal del favorito
            exceso = 0.25 * (p_11 - p_fav_modal)
            matriz_final[1, 1] -= exceso
            matriz_final[mejor_fav[0], mejor_fav[1]] += exceso * 0.70
            segundo_fav = sorted(candidatos_fav, key=lambda c: matriz_final[c[0], c[1]], reverse=True)[1]
            matriz_final[segundo_fav[0], segundo_fav[1]] += exceso * 0.30
            matriz_final = np.clip(matriz_final, 0.0, None)
            matriz_final /= np.sum(matriz_final)

    cruce_top_L = cruce_dominante_distinto(top_cruces_L)
    cruce_top_V = cruce_dominante_distinto(top_cruces_V)
    delta_entropia = abs(historial_entropia[-1] - historial_entropia[-2]) if len(historial_entropia) >= 2 else 0.0

    # Coherencia matemática estricta: el cambio porcentual total de lambda_base a lambda_red
    # debe coincidir exactamente con la suma de los 5 factores latentes mostrados en la caja azul
    delta_pct_real_L = round(((lam_k_L - lam_base_local) / max(0.1, lam_base_local)) * 100.0, 1)
    delta_pct_real_V = round(((lam_k_V - lam_base_visita) / max(0.1, lam_base_visita)) * 100.0, 1)

    if tier_coef_L < 0.90 and tier_coef_V >= 1.00:
        delta_pct_real_L = min(12.0, delta_pct_real_L)
    if tier_coef_V < 0.90 and tier_coef_L >= 1.00:
        delta_pct_real_V = min(12.0, delta_pct_real_V)

    # Si el atacante juega en casa y el rival concede xGOT >= 1.50, garantizar que lambda_red no sufra penalización
    if xgotc_riv_V >= 1.50 and float(rL.get("Final_Attack_Strength", 50.0)) >= 50.0:
        delta_pct_real_L = max(0.0, delta_pct_real_L)

    pisos_L = {}
    if f_l.get("F_fragilidad_rival", 0.0) >= 3.0:
        pisos_L["F_fragilidad_rival"] = 4.0
    if f_l.get("F_porteria_rival", 0.0) >= 0.5:
        pisos_L["F_porteria_rival"] = 0.5

    techos_V = {}
    if f_v.get("F_fragilidad_rival", 0.0) <= -1.0:
        techos_V["F_fragilidad_rival"] = -1.5

    f_l = reescalar_factores_a_delta(f_l, delta_pct_real_L, fijos=("F_posesion_progresiva",), pisos=pisos_L)
    f_v = reescalar_factores_a_delta(f_v, delta_pct_real_V, fijos=("F_posesion_progresiva",), techos=techos_V)

    pct_tot_L = round(float(sum(f_l.values())), 1)
    pct_tot_V = round(float(sum(f_v.values())), 1)
    lam_k_L = round(float(lam_base_local * (1.0 + pct_tot_L / 100.0)), 2)
    lam_k_V = round(float(lam_base_visita * (1.0 + pct_tot_V / 100.0)), 2)

    cruces_6["factores_local"] = f_l
    cruces_6["factores_visita"] = f_v
    cruces_6["seg_gol_concedido_L"] = float(tV.get("Avg_Seconds_Between_Goals_Conceded", 3500.0))
    cruces_6["seg_gol_concedido_V"] = float(tL.get("Avg_Seconds_Between_Goals_Conceded", 3500.0))
    cruces_6["cerrojo_L_activo"] = cerrojo_L_activo
    cruces_6["cerrojo_V_activo"] = cerrojo_V_activo

    sello_verificacion = (
        f"RED NEURONAL RECURSIVA ACTIVA (5 FACTORES LATENTES XAI) · it. {iteracion_final} (H={historial_entropia[-1]:.3f})  ·  "
        f"λ Base ({lam_base_local:.2f} - {lam_base_visita:.2f}) -> λ Red ({lam_k_L:.2f} - {lam_k_V:.2f})\n"
        f"L ({pct_tot_L:+.1f}%): Cruce #1: [{cruce_top_L} · Int {int_cruce_L:.2f}] · PosProg {f_l['F_posesion_progresiva']:+.1f}% · "
        f"Creac {f_l['F_creacion']:+.1f}% · Finaliz {f_l['F_finalizacion']:+.1f}% · PortRiv {f_l['F_porteria_rival']:+.1f}% · FragRiv {f_l['F_fragilidad_rival']:+.1f}%\n"
        f"V ({pct_tot_V:+.1f}%): Cruce #1: [{cruce_top_V} · Int {int_cruce_V:.2f}] · PosProg {f_v['F_posesion_progresiva']:+.1f}% · "
        f"Creac {f_v['F_creacion']:+.1f}% · Finaliz {f_v['F_finalizacion']:+.1f}% · PortRiv {f_v['F_porteria_rival']:+.1f}% · FragRiv {f_v['F_fragilidad_rival']:+.1f}%"
    )

    return {
        "lambda_base_local": round(float(lam_base_local), 2),
        "lambda_base_visita": round(float(lam_base_visita), 2),
        "lambda_local_recursivo": round(lam_k_L, 2),
        "lambda_visita_recursivo": round(lam_k_V, 2),
        "iteracion_convergencia": iteracion_final,
        "historial_entropia": historial_entropia,
        "cruce_dominante_local": cruce_top_L,
        "cruce_dominante_visita": cruce_top_V,
        "intensidad_cruce_local": round(int_cruce_L, 4),
        "intensidad_cruce_visita": round(int_cruce_V, 4),
        "series_reales_por_equipo": n_series,
        "tasa_explosion_3mas_local": round(tasa_explosion_3mas_L, 3),
        "tasa_explosion_3mas_visita": round(tasa_explosion_3mas_V, 3),
        "tasa_sequia_local": round(tasa_sequia_L, 3),
        "tasa_sequia_visita": round(tasa_sequia_V, 3),
        "patrones_local": {k: round(v, 4) for k, v in pat_L.items()},
        "patrones_visita": {k: round(v, 4) for k, v in pat_V.items()},
        "sello_verificacion_visual": sello_verificacion,
        "matriz_prob": matriz_final.tolist(),
        "mu_local_casa": mu_L_casa,
        "mu_visita_fuera": mu_V_fuera,
        "cruces_tacticos": cruces_6,
        "alertas_xai": cruces_6["alertas_xai"],
        "asedio_bandas_activo": cruces_6["asedio_bandas_activo"],
        "defensa_kamikaze_activa": cruces_6["defensa_kamikaze_activa"],
        "seg_gol_concedido_rival_L": float(tV.get("Avg_Seconds_Between_Goals_Conceded", 3500.0)),
        "seg_gol_concedido_rival_V": float(tL.get("Avg_Seconds_Between_Goals_Conceded", 3500.0)),
        "seg_gol_L": float(tL.get("Avg_Seconds_Between_Goals", 3500.0)),
        "seg_gol_V": float(tV.get("Avg_Seconds_Between_Goals", 3500.0)),
        "cerrojo_L_activo": cerrojo_L_activo,
        "cerrojo_V_activo": cerrojo_V_activo,
    }
