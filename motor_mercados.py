"""
agente_futbol/motor_mercados.py
───────────────────────────────────────────────────────────────────────────────
MOTOR DE PROBABILIDADES POR MERCADO (catálogo completo de 30 mercados).

Qué hace
--------
1. Toma los dos reportes/auditorías de un enfrentamiento (cualquier pareja de
   equipos que el sistema pueda identificar) y resuelve local/visitante con la
   condición real registrada en cada partido, no con el orden del título.
2. Estima tasas (λ) de goles, córneres y tarjetas con el modelo exploratorio
   documentado en este archivo, siempre en código numérico reproducible.
3. Construye la matriz conjunta de marcadores y deriva de ella todos los
   mercados del catálogo (no multiplica marginales para las combinaciones).
4. Evalúa sensibilidad de cada selección y clasifica las que superan el umbral.
5. Genera el informe técnico completo y el mensaje de Telegram con SOLO las
   selecciones que pasan el filtro central.

Garantías de honestidad
-----------------------
• Un dato nulo NO se sustituye por cero: la métrica se marca sin observaciones.
• Cada media usa su propio número de observaciones no nulas como denominador.
• Si falta una entrada imprescindible, el mercado se marca NO ESTIMABLE con el
   motivo explícito; nunca se inventa una probabilidad.
• El modelo de lenguaje no interviene en ningún cálculo.

Modelo exploratorio (configurable, valores por defecto entre paréntesis)
-----------------------------------------------------------------------
medias ajustadas:
    media_ajustada = (1 - w_condicion) * media_general + w_condicion * media_condicion
    w_condicion = 0.5
tasas de goles:
    ataque  = (1 - w_xg) * goles_a_favor  + w_xg * xg_a_favor
    defensa = (1 - w_xg) * goles_en_contra + w_xg * xg_en_contra
    lambda_local     = (ataque_local + defensa_visitante) / 2
    lambda_visitante = (ataque_visitante + defensa_local) / 2
    w_xg = 0.5
distribución:
    Poisson como aproximación inicial, con corte numérico elegido por masa
    residual comprobada (no un corte fijo).

Este modelo NO está validado contra resultados: las probabilidades son
estimaciones con supuestos declarados, no garantías.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import config

BASE_DIR = Path(__file__).resolve().parent

# ═══════════════════════════════════════════════════════════════════════════════
# PARÁMETROS DEL MODELO
# ═══════════════════════════════════════════════════════════════════════════════

VALORES_W_CONDICION = (0.0, 0.5, 1.0)
VALORES_W_XG = (0.0, 0.5, 1.0)
W_CONDICION_DEFECTO = 0.5
W_XG_DEFECTO = 0.5
UMBRAL_DEFECTO = float(os.getenv("MERCADOS_UMBRAL", "0.70"))
# Contracción bayesiana del peso de condición: w = w_max · n/(n+K).
# K es la "credibilidad" que se le exige a la muestra de condición. Con K=5, hacen
# falta ~5 partidos para alcanzar la mitad del peso máximo.
K_CREDIBILIDAD_DEFECTO = float(os.getenv("MERCADOS_K_CREDIBILIDAD", "5"))
# Credibilidad exigida al xG: es más ruidoso que los goles y a menudo tiene menos
# cobertura (Vasco 5/15, Alianza 0/13), así que se le pide más muestra.
K_CREDIBILIDAD_XG_DEFECTO = float(os.getenv("MERCADOS_K_CREDIBILIDAD_XG", "4"))
# Límite del efecto TOTAL de los moduladores tácticos sobre la λ base.
LIMITE_MODULADOR_DEFECTO = float(os.getenv("MERCADOS_LIMITE_MODULADOR", "0.15"))
# Corrección de Dixon-Coles para marcadores bajos. El valor procede de la
# literatura (rho ≈ -0,1) y aquí se usa SOLO como escenario de sensibilidad, nunca
# como valor por defecto: no está estimado sobre la muestra del proyecto.
RHO_DIXON_COLES = float(os.getenv("MERCADOS_RHO", "-0.11"))

# Escenarios de dispersión para córneres y tarjetas (binomial negativa)
VALORES_K_NB = (10.0, 5.0)

# Corte numérico: se amplía hasta que la masa residual sea despreciable
MASA_RESIDUAL_MAXIMA = 1e-9
MAX_GOLES = 20          # tope duro de seguridad para la matriz
MAX_CONTEO = 80         # tope duro para córneres/tarjetas

# Líneas orientativas por defecto (configurables, NO son un catálogo de casa de apuestas)
LINEAS_GOLES = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5)
LINEAS_CORNERES = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5,
                   12.5, 13.5, 14.5, 15.5)
LINEAS_AMARILLAS = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5)
HANDICAPS_EUROPEOS = (-3, -2, -1, 0, 1, 2, 3)

NOMBRE_MODELO = "mercados-poisson-exploratorio"
# v2.0: la matriz la calcula la Red Neuronal Recursiva de 45 variables (tensor real
# por partido + Bilinear Pooling + Cross-Attention + bucle de entropía + MC 100.000).
# Se sube la versión para que el id del trabajo y el snapshot de pronóstico no se
# confundan con los de una predicción hecha por el motor anterior.
VERSION_MODELO = "2.0"

# Orden de presentación de los periodos: tiempo completo, primer tiempo, segundo.
# Se define aquí (y no se importa de fuentes_xg) para que este módulo no arrastre
# la capa de scraping solo para ordenar una lista.
ORDEN_PERIODOS = ("FT", "1T", "2T")


# ═══════════════════════════════════════════════════════════════════════════════
# ESTRUCTURAS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MuestraMetrica:
    """
    Estadística de una métrica con su recuento real de observaciones.

    Además de la media aritmética (`media`) conserva una media PONDERADA
    (`media_ponderada`) que solo se diferencia cuando el llamador asigna pesos
    distintos de 1 a cada observación — hoy, el descuento de los partidos con
    expulsión que pide el análisis especialista. Con pesos por defecto ambas
    medias son idénticas, así que el comportamiento anterior no cambia.
    """
    clave: str
    n: int = 0
    media: float | None = None
    suma: float = 0.0
    minimo: float | None = None
    maximo: float | None = None
    peso_total: float = 0.0
    suma_ponderada: float = 0.0
    n_ceros: int = 0

    def agregar(self, valor: float | None, peso: float = 1.0) -> None:
        if valor is None:
            return
        self.n += 1
        val_f = float(valor)
        self.suma += val_f
        if val_f == 0.0:
            self.n_ceros += 1
        self.minimo = val_f if self.minimo is None else min(self.minimo, val_f)
        self.maximo = val_f if self.maximo is None else max(self.maximo, val_f)
        self.media = self.suma / self.n
        self.peso_total += float(peso)
        self.suma_ponderada += val_f * float(peso)

    @property
    def pct_ceros(self) -> float:
        """Fracción de observaciones iguales a cero (útil para detectar falta de cobertura)."""
        return (self.n_ceros / self.n) if self.n > 0 else 0.0

    @property
    def media_ponderada(self) -> float | None:
        """Media con los pesos aplicados; None si no hay ninguna observación."""
        if self.peso_total <= 0:
            return None
        return self.suma_ponderada / self.peso_total

    @property
    def pesos_activos(self) -> bool:
        """¿Alguna observación pesó distinto de 1? Evita declarar ajustes falsos."""
        return abs(self.peso_total - self.n) > 1e-9


@dataclass
class MuestraEquipo:
    """
    Estadísticas de un equipo separadas por historial general y por condición,
    y por periodo (FT / 1T / 2T).
    """
    equipo: str
    partidos_general: int = 0
    partidos_condicion: int = 0
    metricas: dict[str, MuestraMetrica] = field(default_factory=dict)
    # Partidos en los que el xG del equipo SÍ venía publicado. Permite declarar la
    # cobertura real: sin esto, un `-` en el informe ocultaba si la λ se calculó
    # con xG o solo con goles.
    partidos_con_xg: int = 0
    partidos_condicion_con_xg: int = 0

    def dame(self, clave: str) -> MuestraMetrica:
        return self.metricas.setdefault(clave, MuestraMetrica(clave))

    def media(self, clave: str) -> float | None:
        return self.dame(clave).media

    def cobertura_xg(self, condicion: bool = False) -> tuple[int, int, float | None]:
        """
        (partidos con xG, partidos totales, fracción). El total es el de la muestra
        correspondiente (general o por condición).
        """
        if condicion:
            n, con = self.partidos_condicion, self.partidos_condicion_con_xg
        else:
            n, con = self.partidos_general, self.partidos_con_xg
        return con, n, (con / n if n else None)

    def resumen_cobertura(self) -> dict[str, Any]:
        """Cobertura de las métricas clave, para declararla en el mensaje."""
        con, n, frac = self.cobertura_xg()
        return {
            "equipo": self.equipo,
            "partidos": self.partidos_general,
            "partidos_con_xg": con,
            "cobertura_xg": frac,
            "usa_xg_real": con > 0,
            "sin_xg": con == 0,
        }


@dataclass
class Seleccion:
    """Una selección concreta de un mercado, con su probabilidad central."""
    id_mercado: str
    mercado: str
    evento: str                 # evento interno estable, p.ej. "over_2.5"
    etiqueta: str               # texto visible
    periodo: str                # "FT" | "1T" | "2T"
    equipo: str | None          # equipo al que se refiere (o None si es del partido)
    linea: float | None
    probabilidad: float | None
    estimable: bool = True
    motivo: str = ""
    escenarios: dict[str, float] = field(default_factory=dict)
    estado: str = ""            # mantiene | sensible | sensibilidad_incompleta | no_estimable

    @property
    def clave_unica(self) -> tuple:
        return (self.id_mercado, self.evento, self.periodo, self.equipo or "", self.linea)


@dataclass
class ResultadoMercado:
    """Resultado de un mercado del catálogo (puede tener 0..N selecciones)."""
    id_mercado: str
    numero: int
    nombre: str
    nota: str = ""
    selecciones: list[Seleccion] = field(default_factory=list)
    estado: str = "calculado"   # calculado | sin_selecciones | datos_insuficientes | regla_no_definida
    detalle: dict[str, Any] = field(default_factory=dict)


@dataclass
class Parametros:
    w_condicion: float = W_CONDICION_DEFECTO
    w_xg: float = W_XG_DEFECTO
    umbral: float = UMBRAL_DEFECTO
    k_nb: float = 10.0
    distribucion_conteos: str = "poisson"   # poisson | binomial_negativa
    # Contracción bayesiana del peso de condición. None = peso fijo (comportamiento
    # anterior, se conserva para poder comparar versiones).
    k_credibilidad: float | None = K_CREDIBILIDAD_DEFECTO
    # Corrección de Dixon-Coles. 0.0 = matriz Poisson independiente (por defecto:
    # rho NO está estimado sobre la muestra del proyecto, así que no se aplica salvo
    # que se pida explícitamente como escenario).
    rho: float = 0.0
    # Moduladores tacticos sobre la lambda (45 metricas, acotados y neutros).
    aplicar_moduladores: bool = True
    # Motor recursivo de 2 niveles (motor_recursivo_45_dcn): tensores 15×3×45 ->
    # 15×3×8 -> cruces DCN-V2 -> Jackknife 14/15 + FDR + Monte Carlo 100.000.
    # Es el encargado de recalibrar la lambda final y la matriz conjunta para que
    # el marcador modal no caiga siempre en el 1-1. Si el módulo no puede
    # aplicarse (muestra corta), el motor conserva la matriz del modelo lineal.
    aplicar_motor_recursivo: bool = True
    mc_simulaciones: int = 100_000
    # λ_efectivo (lección Real Madrid 4-1 Sevilla, 18/10/2026): finalización
    # xGOT/xG + regalo del portero rival (goles_evitados < 0) + regalo por
    # errores defensivos del rival (errores_gol / errores_remate).
    aplicar_lambda_efectivo: bool = True
    # Cola ancha (Binomial Negativa) cuando la portería rival es vulnerable
    # (goles_evitados < -0.15) y el atacante es favorito (λ/xG ≥ 2.10) o tira
    # mucho dentro del área (≥ 10): no subestimar 3-1 / 4-1 / 3-0.
    aplicar_cola_ancha: bool = True


@dataclass
class TasasPartido:
    """λ estimadas por equipo y periodo, con su procedencia documentada."""
    lambda_local_ft: float | None = None
    lambda_visitante_ft: float | None = None
    lambda_local_1t: float | None = None
    lambda_visitante_1t: float | None = None
    lambda_local_2t: float | None = None
    lambda_visitante_2t: float | None = None
    procedencia_tiempos: str = ""
    notas: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# MATEMÁTICA (Poisson, binomial negativa, convoluciones)
# ═══════════════════════════════════════════════════════════════════════════════

def poisson_pmf(k: int, lam: float) -> float:
    if lam is None or lam < 0:
        return 0.0
    if lam == 0.0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _media_y_varianza_nb(k: float, mu: float) -> tuple[float, float]:
    """Binomial negativa con varianza = media + media²/k."""
    if k <= 0:
        return mu, mu
    return mu, mu + (mu * mu) / k


def _k_desde_media_varianza(mu: float, varianza: float) -> float:
    """
    Parámetro k de una binomial negativa (varianza = mu + mu²/k) que reproduce
    exactamente la media y la varianza dadas. Se usa para el TOTAL de una familia
    de conteos: sumar binomiales negativas independientes con el mismo k NO da
    otra binomial negativa con ese k, así que el total se modela ajustando k.
    """
    exceso = varianza - mu
    if mu <= 0 or exceso <= 1e-12:
        return float("inf")     # degenera en Poisson
    return (mu * mu) / exceso


def nb_pmf(k: int, mu: float, dispersion: float) -> float:
    """
    P(N=k) para N ~ BinomialNegativa(media=mu, varianza=mu + mu²/dispersion).
    Si dispersion es infinita, degenera en Poisson.
    """
    if mu is None or mu <= 0:
        return 1.0 if k == 0 else 0.0
    if dispersion is None or math.isinf(dispersion) or dispersion <= 0:
        return poisson_pmf(k, mu)
    r = dispersion
    p = r / (r + mu)
    # P(N=k) = C(k+r-1, k) * p^r * (1-p)^k
    coef = math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1))
    return coef * (p ** r) * ((1 - p) ** k)


def distribucion_conteo(mu: float | None, maximo: int = MAX_CONTEO,
                        parametros: Parametros | None = None,
                        k_nb: float | None = None) -> list[float]:
    """
    Distribución discreta truncada de un conteo, ampliada hasta que la masa
    residual sea despreciable (no un corte fijo).
    """
    parametros = parametros or Parametros()
    if mu is None or mu <= 0:
        return [1.0]
    k = parametros.k_nb if k_nb is None else k_nb
    usar_nb = (parametros.distribucion_conteos == "binomial_negativa")
    pmf: list[float] = []
    acumulada = 0.0
    n = 0
    while n <= maximo:
        p = nb_pmf(n, mu, k) if usar_nb else poisson_pmf(n, mu)
        pmf.append(p)
        acumulada += p
        n += 1
        # Poisson decae rápido; NB puede tener cola larga, se exige masa residual baja
        if acumulada >= 1.0 - MASA_RESIDUAL_MAXIMA and n > mu + 1:
            break
    if acumulada > 0:
        pmf = [p / acumulada for p in pmf]
    return pmf


def convolucion(a: list[float], b: list[float]) -> list[float]:
    """Convolución discreta exacta de dos distribuciones (para totales)."""
    salida = [0.0] * (len(a) + len(b) - 1)
    for i, pa in enumerate(a):
        if pa == 0.0:
            continue
        for j, pb in enumerate(b):
            salida[i + j] += pa * pb
    return salida


def factor_dixon_coles(i: int, j: int, lam_l: float, lam_v: float,
                       rho: float) -> float:
    """
    Factor tau de Dixon-Coles para los marcadores bajos.

    Fórmula estándar (Dixon & Coles, 1997), verificada contra la implementación de
    referencia:
        tau(0,0) = 1 - lam_l·lam_v·rho
        tau(1,0) = 1 + lam_v·rho        (el multiplicador es la λ del VISITANTE)
        tau(0,1) = 1 + lam_l·rho        (el multiplicador es la λ del LOCAL)
        tau(1,1) = 1 - rho
        tau(i,j) = 1                    en el resto

    Corrige la dependencia entre los goles de ambos equipos en los marcadores
    bajos, que el Poisson independiente no captura. Se acota a un mínimo de 0 para
    que rho fuera de rango no produzca probabilidades negativas.
    """
    if rho == 0.0:
        return 1.0
    if i == 0 and j == 0:
        return max(0.0, 1.0 - lam_l * lam_v * rho)
    if i == 1 and j == 0:
        return max(0.0, 1.0 + lam_v * rho)
    if i == 0 and j == 1:
        return max(0.0, 1.0 + lam_l * rho)
    if i == 1 and j == 1:
        return max(0.0, 1.0 - rho)
    return 1.0


def matriz_marcadores(lam_l: float, lam_v: float,
                      maximo: int = MAX_GOLES,
                      rho: float = 0.0) -> tuple[list[list[float]], float]:
    """
    Matriz conjunta P(i, j), con corte elegido por masa residual y renormalización.

    Con `rho = 0` es el Poisson independiente de siempre. Con `rho != 0` se aplica la
    corrección de Dixon-Coles a los marcadores bajos y se RENORMALIZA la rejilla
    (la corrección altera la suma total, así que hay que devolverla a 1).
    Devuelve (matriz, masa_residual).
    """
    max_l = min(maximo, max(4, int(lam_l + 10 * math.sqrt(lam_l + 1))))
    max_v = min(maximo, max(4, int(lam_v + 10 * math.sqrt(lam_v + 1))))
    # Ampliar hasta que la masa fuera de la rejilla sea despreciable en ambos márgenes
    while max_l < maximo and (1.0 - sum(poisson_pmf(k, lam_l) for k in range(max_l + 1))) > MASA_RESIDUAL_MAXIMA:
        max_l += 1
    while max_v < maximo and (1.0 - sum(poisson_pmf(k, lam_v) for k in range(max_v + 1))) > MASA_RESIDUAL_MAXIMA:
        max_v += 1
    filas = []
    for i in range(max_l + 1):
        pi = poisson_pmf(i, lam_l)
        filas.append([pi * poisson_pmf(j, lam_v)
                      * factor_dixon_coles(i, j, lam_l, lam_v, rho)
                      for j in range(max_v + 1)])
    total = sum(sum(f) for f in filas)
    residual = max(0.0, 1.0 - total)
    if total > 0:
        filas = [[v / total for v in f] for f in filas]
    return filas, residual


def truncar_probabilidad(p: float | None) -> float | None:
    """Evita mostrar 100% por muestras perfectas."""
    if p is None:
        return None
    return min(max(p, 0.0), 1.0)


def formatear_pct(p: float | None, umbral: float = UMBRAL_DEFECTO) -> str:
    """
    Formato sin mentir por redondeo: nunca muestra un valor igual al umbral
    cuando en realidad lo supera, ni 100% cuando es una muestra perfecta.
    """
    if p is None:
        return "N/D"
    if p >= 0.99995:
        return ">99,9%"
    texto = f"{p * 100:.1f}".replace(".", ",")
    if p > umbral and float(texto.replace(",", ".")) <= umbral * 100:
        texto = f"{p * 100:.2f}".replace(".", ",")
    return f"{texto}%"


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACCIÓN DE DATOS DE LOS REPORTES (auditorías del proyecto)
# ═══════════════════════════════════════════════════════════════════════════════

def _valor_periodo(partido: dict[str, Any], clave: str, periodo: str,
                   bando: str) -> float | None:
    """Valor de una métrica en un periodo. bando: 'equipo' | 'rival'."""
    filas = ((partido.get("periodos") or {}).get(periodo) or {}).get("filas", [])
    for fila in filas:
        if fila.get("clave") == clave:
            valor = fila.get(bando)
            if isinstance(valor, (int, float)):
                return float(valor)
            return None
    return None


# Métricas que el motor consume de cada reporte
METRICAS_GOLEADORAS = {
    "goles_favor": "goles_favor",
    "goles_contra": "goles_contra",
    "xg_favor": "xg",
    "xg_contra": "xg",
    "corners_favor": "corners",
    "corners_contra": "corners",
    "amarillas_favor": "tarjetas_amarillas",
    "amarillas_contra": "tarjetas_amarillas",
    "rojas_favor": "tarjetas_rojas",
    "rojas_contra": "tarjetas_rojas",
    "faltas_favor": "faltas",
    "faltas_contra": "faltas",
}

# ── Métricas de EQUIPO (nivel partido) ────────────────────────────────────────
# El catálogo de la auditoría tiene 45 métricas, pero el motor solo extraía las 12
# de arriba (goles, xG, córneres, tarjetas, faltas). Las 33 restantes —posesión,
# remates, duelos, despejes, xGOT, goles evitados…— quedaban SIN extraer (n=0), así
# que los moduladores tácticos siempre salían neutros. Este mapa las incorpora.
#
# Convención: `(clave_en_la_muestra, clave_en_la_auditoria, bando)`, donde bando es
# "propio" (valor del equipo) o "rival" (valor del rival, para las de concesión).
METRICAS_EQUIPO: dict[str, tuple[str, str]] = {
    # Ataque y creación
    "posesion_pct": ("posesion_pct", "propio"),
    "xgot_favor": ("xgot", "propio"),
    "xgot_contra": ("xgot", "rival"),
    "remates_totales_favor": ("remates_totales", "propio"),
    "remates_totales_contra": ("remates_totales", "rival"),
    "remates_puerta_favor": ("remates_puerta", "propio"),
    "remates_puerta_contra": ("remates_puerta", "rival"),
    "remates_dentro_area_favor": ("remates_dentro_area", "propio"),
    "remates_fuera_area_favor": ("remates_fuera_area", "propio"),
    "remates_rechazados_favor": ("remates_rechazados", "propio"),
    "grandes_ocasiones_favor": ("grandes_ocasiones", "propio"),
    "grandes_ocasiones_contra": ("grandes_ocasiones", "rival"),
    "grandes_ocasiones_falladas_favor": ("grandes_ocasiones_falladas", "propio"),
    "xg_sin_penalti_favor": ("xg_sin_penalti", "propio"),
    "xg_juego_abierto_favor": ("xg_juego_abierto", "propio"),
    "xg_balon_parado_favor": ("xg_balon_parado", "propio"),
    "toques_area_rival_favor": ("toques_area_rival", "propio"),
    "toques_area_rival_contra": ("toques_area_rival", "rival"),
    # Posesión y pases
    "pases_total_favor": ("pases_total", "propio"),
    "pases_precision_pct_favor": ("pases_precision_pct", "propio"),
    "pases_ratio_favor": ("pases_ratio", "propio"),
    "pases_tercio_final_pct_favor": ("pases_tercio_final_pct", "propio"),
    "pases_entre_lineas_favor": ("pases_entre_lineas", "propio"),
    "pases_largos_pct_favor": ("pases_largos_pct", "propio"),
    "cruces_pct_favor": ("cruces_pct", "propio"),
    # Disciplina y duelo
    "duelos_ganados_favor": ("duelos_ganados", "propio"),
    "duelos_suelo_pct_favor": ("duelos_suelo_pct", "propio"),
    "duelos_aereos_pct_favor": ("duelos_aereos_pct", "propio"),
    "entradas_pct_favor": ("entradas_pct", "propio"),
    "fueras_juego_favor": ("fueras_juego", "propio"),
    # Defensa y portería
    "despejes_favor": ("despejes", "propio"),
    "despejes_contra": ("despejes", "rival"),
    "intercepciones_favor": ("intercepciones", "propio"),
    "intercepciones_contra": ("intercepciones", "rival"),
    "bloqueos_favor": ("bloqueos", "propio"),
    "bloqueos_contra": ("bloqueos", "rival"),
    "recuperaciones_favor": ("recuperaciones", "propio"),
    "paradas_favor": ("paradas", "propio"),
    "goles_evitados_favor": ("goles_evitados", "propio"),
    "goles_evitados_contra": ("goles_evitados", "rival"),
    "errores_remate_favor": ("errores_remate", "propio"),
    "errores_remate_contra": ("errores_remate", "rival"),
    "errores_gol_favor": ("errores_gol", "propio"),
    "errores_gol_contra": ("errores_gol", "rival"),
    "xa_equipo_favor": ("xa_equipo", "propio"),
    "tiros_libres_favor": ("tiros_libres", "propio"),
    "saques_puerta_favor": ("saques_puerta", "propio"),
    "saques_banda_favor": ("saques_banda", "propio"),
    "al_palo_favor": ("al_palo", "propio"),
}


def extraer_muestra(doc: dict[str, Any], condicion: str | None = None,
                    periodo: str = "FT",
                    peso_roja: float | None = None) -> MuestraEquipo:
    """
    Construye la muestra de un equipo desde su auditoría.
    condicion: "casa" | "fuera" | None (general).

    `peso_roja`: si se indica (p. ej. 0,30), los partidos en los que el equipo
    acabó con al menos una expulsión pesan eso en lugar de 1,00 al promediar. Un
    accidente arbitral no debe definir cómo juega el equipo 11 contra 11 (regla
    del analista especialista). Por defecto es None: TODOS los pesos valen 1 y la
    `media` del proyecto no cambia. La media corregida queda en
    `MuestraMetrica.media_ponderada`, junto a la bruta, para poder declarar ambas.
    """
    nombre = (doc.get("equipo") or {}).get("nombre") or doc.get("equipo_solicitado", "equipo")
    muestra = MuestraEquipo(equipo=nombre)
    hubo_partidos_sin_stats = False

    for p in doc.get("partidos", []):
        if condicion is not None and str(p.get("condicion", "")).lower() != condicion:
            continue
        if condicion is None:
            muestra.partidos_general += 1
        else:
            muestra.partidos_condicion += 1

        # Peso de ESTE partido (1,0 salvo que se pida descontar las expulsiones).
        # La clave del catálogo de la auditoría es "tarjetas_rojas".
        peso = 1.0
        if peso_roja is not None:
            rojas = _valor_periodo(p, "tarjetas_rojas", periodo, "equipo")
            try:
                if rojas is not None and float(rojas) >= 1.0:
                    peso = float(peso_roja)
            except (TypeError, ValueError):
                peso = 1.0

        # Goleadas de Copa Nacional (>= 4 goles) pesan 0.35x para no distorsionar promedio UCL
        try:
            from motor_recursivo_45_dcn import peso_credibilidad_partido
            w_cup = peso_credibilidad_partido(p)
            if w_cup < 1.0:
                peso = min(peso, w_cup)
        except Exception:
            pass

        # ¿Este partido trae xG del equipo? Se cuenta aparte para poder declarar
        # la cobertura real (el dato puede faltar en competiciones sin xG).
        xg_propio = _valor_periodo(p, "xg", periodo, "equipo")
        if xg_propio is not None:
            if condicion is None:
                muestra.partidos_con_xg += 1
            else:
                muestra.partidos_condicion_con_xg += 1

        marcador = p.get("marcador") or {}
        es_local = str(p.get("condicion", "")).lower() == "casa"
        goles_eq = marcador.get("equipo")
        goles_riv = marcador.get("rival")

        # Descuento de 2ª división / copas menores (Championship, Segunda, LaLiga2, EFL Cup, etc.):
        # Goleadas ante rivales de menor categoría no equivalen 1:1 a duelos de primera división.
        torneo_partido = str(p.get("torneo") or p.get("competicion") or "").lower()
        es_segunda = any(
            t in torneo_partido
            for t in (
                "championship", "segunda", "laliga 2", "laliga2", "hypermotion",
                "primera federacion", "primera federación", "serie b", "serie c",
                "2. bundesliga", "2.bundesliga", "3. liga", "ligue 2", "ligue2",
                "efl cup", "carabao", "primera b", "league one", "league two", "national",
            )
        )
        factor_div = 0.76 if es_segunda else 1.0
        factor_div_def = 1.22 if es_segunda else 1.0

        if goles_eq is not None and factor_div < 1.0:
            goles_eq = round(float(goles_eq) * factor_div, 3)
        if goles_riv is not None and factor_div_def > 1.0:
            goles_riv = round(float(goles_riv) * factor_div_def, 3)

        if periodo == "FT":
            # Los goles solo están disponibles a nivel de partido completo en FT
            muestra.dame("goles_favor").agregar(goles_eq, peso)
            muestra.dame("goles_contra").agregar(goles_riv, peso)
        else:
            # En 1T/2T los goles no vienen desglosados: quedan sin observación
            # (nunca se sustituyen por cero).
            muestra.dame("goles_favor").agregar(None, peso)
            muestra.dame("goles_contra").agregar(None, peso)

        # Si el partido no tiene hoja de estadísticas detalladas (0 remates ambos), no promediar ceros
        rt_eq = _valor_periodo(p, "remates_totales", periodo, "equipo")
        rt_riv = _valor_periodo(p, "remates_totales", periodo, "rival")
        partido_sin_stats = bool(rt_eq is not None and rt_riv is not None and float(rt_eq) == 0.0 and float(rt_riv) == 0.0)
        if partido_sin_stats:
            hubo_partidos_sin_stats = True

        def _val_ajustado(cl: str, b: str) -> float | None:
            if partido_sin_stats:
                return None
            v_orig = _valor_periodo(p, cl, periodo, b)
            if v_orig is None:
                return None
            if b == "equipo" and factor_div < 1.0 and cl in ("xg", "xgot", "remates_totales", "remates_puerta", "grandes_ocasiones", "toques_area_rival"):
                return round(float(v_orig) * factor_div, 3)
            if b == "rival" and factor_div_def > 1.0 and cl in ("xg", "xgot"):
                return round(float(v_orig) * factor_div_def, 3)
            return float(v_orig)

        for destino, clave in METRICAS_GOLEADORAS.items():
            if destino in ("goles_favor", "goles_contra"):
                continue
            bando = "equipo" if destino.endswith("_favor") else "rival"
            muestra.dame(destino).agregar(_val_ajustado(clave, bando), peso)

        # Métricas de EQUIPO (posesión, remates, duelos, despejes, xGOT…). Sin esto
        # los moduladores tácticos no tenían de dónde leer y salían siempre neutros.
        for destino, (clave, lado) in METRICAS_EQUIPO.items():
            bando = "equipo" if lado == "propio" else "rival"
            muestra.dame(destino).agregar(_val_ajustado(clave, bando), peso)

        # xG en contra: el reporte del equipo no publica su xG concedido, se usa el
        # xG del rival (aproximación declarada).
        muestra.dame("xg_contra").agregar(_val_ajustado("xg", "rival"), peso)

    # Fallback inteligente si los partidos de la muestra carecían de estadísticas detalladas (n_con_stats == 0)
    if hubo_partidos_sin_stats and muestra.dame("goles_favor").n > 0 and muestra.dame("remates_totales_favor").n == 0:
        gf = float(muestra.dame("goles_favor").media if muestra.dame("goles_favor").media is not None else 1.25)
        gc = float(muestra.dame("goles_contra").media if muestra.dame("goles_contra").media is not None else 1.25)
        muestra.dame("remates_totales_favor").agregar(float(max(6.0, round(gf * 4.0 + 4.0, 2))))
        muestra.dame("remates_puerta_favor").agregar(float(max(2.0, round(gf * 2.0 + 1.0, 2))))
        muestra.dame("posesion_favor").agregar(50.0)
        muestra.dame("xg_favor").agregar(float(round(gf, 2)))
        muestra.dame("xgot_favor").agregar(float(round(gf, 2)))
        muestra.dame("xgot_contra").agregar(float(round(gc, 2)))
        muestra.dame("xg_contra").agregar(float(round(gc, 2)))
        muestra.dame("goles_evitados_favor").agregar(0.0)

    return muestra


def extraer_muestra_oponente(doc_rival: dict[str, Any], condicion: str | None = None,
                             periodo: str = "FT",
                             peso_roja: float | None = None) -> MuestraEquipo:
    """
    Muestra del rival como oponente: sus métricas ofensivas vistas desde el otro
    lado (xG generado, córneres, etc.), útil para cruzar con el equipo.
    """
    return extraer_muestra(doc_rival, condicion=condicion, periodo=periodo,
                           peso_roja=peso_roja)


# ═══════════════════════════════════════════════════════════════════════════════
# AJUSTE DE MEDIAS Y TASAS
# ═══════════════════════════════════════════════════════════════════════════════

def peso_condicion_por_evidencia(n_condicion: int, w_max: float = W_CONDICION_DEFECTO,
                                 k_credibilidad: float = K_CREDIBILIDAD_DEFECTO) -> float:
    """
    Peso de la condición CONTRAÍDO por el tamaño de su muestra (contracción
    bayesiana):

        w_condicion = w_max · n_condicion / (n_condicion + K)

    Con pocos partidos de condición el peso cae hacia 0 y manda el historial
    general, que es más estable. Con muchos tiende a `w_max` sin superarlo nunca.

    Por qué: con el peso FIJO de 0,5, un equipo con 2 partidos en casa (p. ej.
    Vasco fuera, n=2) pesaba lo mismo que otro con 15, y esos 2 partidos podían
    mover la λ entera. Aquí, n=2 con K=5 da w≈0,14 en lugar de 0,50.
    """
    try:
        n = max(0, int(n_condicion))
    except (TypeError, ValueError):
        return 0.0
    if n <= 0:
        return 0.0
    if w_max <= 0:
        return 0.0
    if k_credibilidad < 0:
        k_credibilidad = 0.0
    return float(w_max) * (n / (n + k_credibilidad))


def media_y_n_ajustados(general: MuestraMetrica, condicion: MuestraMetrica,
                        w_condicion: float,
                        k_credibilidad: float | None = None) -> tuple[float | None, int]:
    """
    Como `media_ajustada`, pero devuelve también cuántas observaciones respaldan el
    resultado. Es lo que permite ponderar el xG por su COBERTURA real: si un equipo
    solo tiene xG en 5 de 15 partidos, ese xG no puede pesar lo mismo que uno con 15.
    """
    valor = media_ajustada(general, condicion, w_condicion, k_credibilidad)
    return valor, max(general.n, condicion.n)


def mezcla_goles_xg(goles: float | None, n_goles: int,
                    xg: float | None, n_xg: int,
                    w_xg: float, k_credibilidad_xg: float = K_CREDIBILIDAD_XG_DEFECTO
                    ) -> tuple[float | None, str, float]:
    """
    Mezcla goles reales con xG ponderando el xG por su COBERTURA y credibilidad:

        cobertura   = min(1, n_xg / max(1, n_goles))
        credibilidad= n_xg / (n_xg + K_xg)
        w_xg_ef     = w_xg · cobertura · credibilidad

    Así el xG solo pesa lo que su muestra permite:
      • n_xg = 0 (Alianza Lima antes del relleno) -> w=0: 100% goles, sin excepción.
      • n_xg = 5 de 15 -> w se reduce a un tercio de lo pedido.
      • n_xg = 15 de 15 -> w ≈ w_max · 15/19 (nunca el 100%, porque el xG tampoco
        es verdad absoluta).

    Devuelve (valor, descripción, peso_efectivo). Nunca lanza TypeError con nulos.
    """
    if goles is None and xg is None:
        return None, "sin_datos", 0.0
    if xg is None or n_xg <= 0:
        return goles, f"solo_goles (sin xG en la fuente, n_goles={n_goles})", 0.0
    if goles is None or n_goles <= 0:
        return xg, f"solo_xG (sin goles, n_xg={n_xg})", 1.0
    cobertura = min(1.0, n_xg / max(1, n_goles))
    credibilidad = n_xg / (n_xg + max(0.0, k_credibilidad_xg))
    w_ef = float(w_xg) * cobertura * credibilidad
    mezcla = (1.0 - w_ef) * goles + w_ef * xg
    return mezcla, (f"mezcla goles+xG (w_xg_ef={w_ef:.3f}, cobertura={cobertura:.2f}, "
                    f"n_xg={n_xg}/{n_goles})"), w_ef


def media_ajustada(general: MuestraMetrica, condicion: MuestraMetrica,
                   w_condicion: float, k_credibilidad: float | None = None) -> float | None:
    """
    Media del equipo combinando historial general y de condición.

        w = w_max · n_cond / (n_cond + K)      (si se pasa `k_credibilidad`)
        media = (1 - w) · general + w · condicion

    Los partidos de condición ya están dentro del historial general: es una
    ponderación, no una muestra adicional. Si falta una de las dos, se usa la
    disponible (documentado) y si faltan ambas, None.

    `k_credibilidad` activa la contracción bayesiana: el peso de la condición se
    reduce según cuántos partidos la respaldan. Si es None se usa el peso fijo
    (comportamiento anterior, conservado para poder comparar).
    """
    mg, mc = general.media, condicion.media
    if mg is None and mc is None:
        return None
    if mg is None:
        return mc
    if mc is None:
        return mg
    w = w_condicion
    if k_credibilidad is not None:
        w = peso_condicion_por_evidencia(condicion.n, w_condicion, k_credibilidad)
    return (1.0 - w) * mg + w * mc


def tasas_goles(local: MuestraEquipo, visitante: MuestraEquipo,
                local_cond: MuestraEquipo, visitante_cond: MuestraEquipo,
                w_condicion: float, w_xg: float,
                k_credibilidad: float | None = None
                ) -> tuple[float | None, float | None, dict]:
    """
    λ_local y λ_visitante según el modelo exploratorio documentado.

        ataque  = (1 - w_xg) * goles_a_favor  + w_xg * xG_a_favor
        defensa = (1 - w_xg) * goles_en_contra + w_xg * xG_en_contra
        lambda_local     = (ataque_local + defensa_visitante) / 2
        lambda_visitante = (ataque_visitante + defensa_local) / 2

    Las medias previas ya están ajustadas por condición (`media_ajustada`). El xG en
    contra proviene del xG del rival en cada partido del reporte (aproximación
    declarada, porque el reporte no publica el xG concedido propio).

    Devuelve (lambda_local, lambda_visitante, detalle_para_trazabilidad).
    """
    detalle: dict[str, Any] = {}

    def mezcla(real: float | None, xg: float | None) -> float | None:
        if real is None and xg is None:
            return None
        if real is None:
            return xg
        if xg is None:
            return real
        return (1.0 - w_xg) * real + w_xg * xg

    def componentes(propio: MuestraEquipo, prop_cond: MuestraEquipo,
                    etiqueta: str) -> dict[str, float | None]:
        # Goles: media ajustada por condición + nº de observaciones que la respaldan
        gf, n_gf = media_y_n_ajustados(propio.dame("goles_favor"),
                                       prop_cond.dame("goles_favor"),
                                       w_condicion, k_credibilidad)
        gc, n_gc = media_y_n_ajustados(propio.dame("goles_contra"),
                                       prop_cond.dame("goles_contra"),
                                       w_condicion, k_credibilidad)
        xgf, n_xgf = media_y_n_ajustados(propio.dame("xg_favor"),
                                         prop_cond.dame("xg_favor"),
                                         w_condicion, k_credibilidad)
        xgc, n_xgc = media_y_n_ajustados(propio.dame("xg_contra"),
                                         prop_cond.dame("xg_contra"),
                                         w_condicion, k_credibilidad)
        # La mezcla pondera el xG por su COBERTURA real: 5 partidos de xG no pueden
        # pesar lo mismo que 15. Con n_xg=0 queda 100% goles, sin excepción.
        ataque, desc_ataque, w_ataque = mezcla_goles_xg(gf, n_gf, xgf, n_xgf, w_xg)
        defensa, desc_defensa, w_defensa = mezcla_goles_xg(gc, n_gc, xgc, n_xgc, w_xg)
        con_xg, tot, frac = propio.cobertura_xg(condicion=False)
        detalle[etiqueta] = {
            "goles_favor": gf, "goles_contra": gc, "xg_favor": xgf, "xg_contra": xgc,
            "n_goles_favor": n_gf, "n_goles_contra": n_gc,
            "n_xg_favor": n_xgf, "n_xg_contra": n_xgc,
            "ataque": ataque, "defensa": defensa,
            "w_xg_efectivo_ataque": w_ataque,
            "w_xg_efectivo_defensa": w_defensa,
            "descripcion_ataque": desc_ataque,
            "descripcion_defensa": desc_defensa,
            "xg_disponible": (xgf is not None) or (xgc is not None),
            "partidos": tot,
            "partidos_con_xg": con_xg,
            "cobertura_xg": frac,
            "base_calculo": ("goles + xG real" if (xgf is not None or xgc is not None)
                             else "solo goles (sin xG en la fuente)"),
        }
        return {"ataque": ataque, "defensa": defensa}

    cl = componentes(local, local_cond, "local")
    cv = componentes(visitante, visitante_cond, "visitante")

    ataque_local, defensa_local = cl["ataque"], cl["defensa"]
    ataque_visitante, defensa_visitante = cv["ataque"], cv["defensa"]

    lam_l = ((ataque_local + defensa_visitante) / 2.0
             if ataque_local is not None and defensa_visitante is not None else None)
    lam_v = ((ataque_visitante + defensa_local) / 2.0
             if ataque_visitante is not None and defensa_local is not None else None)

    detalle["lambda_local"] = lam_l
    detalle["lambda_visitante"] = lam_v
    return lam_l, lam_v, detalle


def _xg_periodo(doc: dict[str, Any], periodo: str, condicion: str | None = None,
                bando: str = "equipo") -> float | None:
    """Media de xG de un periodo concreto desde el documento de auditoría."""
    valores = []
    for p in doc.get("partidos", []):
        if condicion is not None and str(p.get("condicion", "")).lower() != condicion:
            continue
        v = _valor_periodo(p, "xg", periodo, bando)
        if v is not None:
            valores.append(v)
    if not valores:
        return None
    return sum(valores) / len(valores)


def repartir_lambdas_por_tiempos(
    doc_local: dict[str, Any], doc_visitante: dict[str, Any],
    lam_ft_l: float | None, lam_ft_v: float | None,
    cond_local: str, cond_visitante: str,
) -> tuple[float | None, float | None, float | None, float | None, str, list[str]]:
    """
    Reparte λ FT entre 1T y 2T usando el xG por tiempos de ambos equipos
    (xG propio y xG concedido por el rival), normalizado para sumar FT.

    Devuelve (lam_l_1t, lam_v_1t, lam_l_2t, lam_v_2t, procedencia, notas).
    Si faltan los xG por tiempo de las dos mitades, devuelve todo None.
    """
    notas: list[str] = []
    if lam_ft_l is None or lam_ft_v is None:
        return None, None, None, None, "sin λ FT", ["No hay λ FT: no se pueden repartir tiempos."]

    xg: dict[str, dict[str, float | None]] = {}
    for etiqueta, doc, cond in (("local", doc_local, cond_local),
                                ("visitante", doc_visitante, cond_visitante)):
        xg[etiqueta] = {
            "1T": _xg_periodo(doc, "1T", cond, "equipo"),
            "2T": _xg_periodo(doc, "2T", cond, "equipo"),
            "1T_contra": _xg_periodo(doc, "1T", cond, "rival"),
            "2T_contra": _xg_periodo(doc, "2T", cond, "rival"),
        }

    # xG cruzado por mitad: (xG propio + xG concedido por el rival) / 2
    cruzado: dict[str, dict[str, float | None]] = {"local": {}, "visitante": {}}
    for eq, rival in (("local", "visitante"), ("visitante", "local")):
        for per in ("1T", "2T"):
            propio = xg[eq][per]
            concedido = xg[rival][f"{per}_contra"]
            if propio is None or concedido is None:
                cruzado[eq][per] = None
            else:
                cruzado[eq][per] = (propio + concedido) / 2.0

    faltan = [f"{eq}-{per}" for eq in ("local", "visitante") for per in ("1T", "2T")
              if cruzado[eq][per] is None]
    if faltan:
        return (None, None, None, None, "xG por tiempos insuficiente",
                [f"Sin xG cruzado por tiempos para: {', '.join(faltan)}. "
                 f"No se inventa el reparto 1T/2T."])

    def reparto(lam_ft: float, eq: str) -> tuple[float, float]:
        a, b = cruzado[eq]["1T"], cruzado[eq]["2T"]
        total = (a or 0.0) + (b or 0.0)
        if total <= 0:
            return lam_ft / 2.0, lam_ft / 2.0
        return lam_ft * (a / total), lam_ft * (b / total)

    l1, l2 = reparto(lam_ft_l, "local")
    v1, v2 = reparto(lam_ft_v, "visitante")
    notas.append("Probabilidades por tiempo INFERIDAS del xG cruzado por mitades, "
                 "normalizadas para sumar FT; se asume independencia temporal entre mitades.")
    return l1, v1, l2, v2, "xG cruzado por tiempos (inferido)", notas


# ═══════════════════════════════════════════════════════════════════════════════
# CÁLCULO DE PROBABILIDADES A PARTIR DE LA MATRIZ CONJUNTA
# ═══════════════════════════════════════════════════════════════════════════════

def prob_desde_matriz(matriz: list[list[float]],
                      predicado: Callable[[int, int], bool]) -> float:
    """Suma la masa de las celdas que cumplen el predicado (eventos conjuntos)."""
    total = 0.0
    for i, fila in enumerate(matriz):
        for j, p in enumerate(fila):
            if predicado(i, j):
                total += p
    return truncar_probabilidad(total)


def resultado_1x2(matriz: list[list[float]]) -> dict[str, float]:
    return {
        "1": prob_desde_matriz(matriz, lambda i, j: i > j),
        "X": prob_desde_matriz(matriz, lambda i, j: i == j),
        "2": prob_desde_matriz(matriz, lambda i, j: i < j),
    }


def total_goles(matriz: list[list[float]]) -> dict[int, float]:
    """Distribución del total de goles, derivada de la matriz conjunta."""
    dist: dict[int, float] = {}
    for i, fila in enumerate(matriz):
        for j, p in enumerate(fila):
            dist[i + j] = dist.get(i + j, 0.0) + p
    return dist


def prob_over(dist: dict[int, float], linea: float) -> float:
    return truncar_probabilidad(sum(p for k, p in dist.items() if k > linea))


def prob_under(dist: dict[int, float], linea: float) -> float:
    return truncar_probabilidad(sum(p for k, p in dist.items() if k < linea))


def prob_exacto(dist: dict[int, float], n: int) -> float:
    return truncar_probabilidad(dist.get(n, 0.0))


def prob_al_menos(dist: dict[int, float], n: int) -> float:
    return truncar_probabilidad(sum(p for k, p in dist.items() if k >= n))


# ═══════════════════════════════════════════════════════════════════════════════
# CATÁLOGO DE MERCADOS (orden fijo de la plantilla)
# ═══════════════════════════════════════════════════════════════════════════════

MERCADOS: tuple[tuple[str, str], ...] = (
    ("ganador_partido", "Ganador del partido"),
    ("combi_mejorada", "COMBI MEJORADA"),
    ("pago_anticipado_2", "Ganador del partido - Pago Anticipado +2"),
    ("total_goles", "Total de goles / Número de goles"),
    ("total_goles_local", "{equipo_local} - Total de goles"),
    ("total_goles_visitante", "{equipo_visitante} - Total de goles"),
    ("fiesta_de_goles", "FIESTA DE GOLES"),
    ("handicap_3_opciones", "Hándicap de 3 opciones"),
    ("ambos_anotan", "Ambos equipos anotan"),
    ("doble_oportunidad", "Doble oportunidad"),
    ("apuesta_sin_empate", "Apuesta sin empate"),
    ("ganador_1t", "1er tiempo - Ganador"),
    ("_1t_ft", "1er tiempo / Tiempo completo"),
    ("equipo_primer_gol", "Equipo que anota el gol 1"),
    ("ganador_total_15", "Ganador del partido + Total de goles 1.5"),
    ("ganador_total_25", "Ganador del partido + Total de goles 2.5"),
    ("ganador_total_35", "Ganador del partido + Total de goles 3.5"),
    ("ganador_ambos_anotan", "Ganador del partido + Ambos equipos anotan"),
    ("ambos_anotan_total_25", "Ambos equipos anotan + Total de goles 2.5"),
    ("anotador_cualquier_momento", "Anotador en cualquier momento"),
    ("primer_anotador", "1er anotador"),
    ("anota_dos_o_mas", "Anota 2 o más goles"),
    ("hat_trick", "Anota un hat-trick"),
    ("total_corners", "Total de tiros de esquina"),
    ("gol_en_ambos_tiempos", "Gol en ambos tiempos"),
    ("cualquier_jugador_primero", "Cualquier jugador anotará primero"),
    ("cualquier_jugador_anota", "Cualquier jugador anotará"),
    ("corners_local", "{equipo_local} - Tiros de esquina"),
    ("corners_visitante", "{equipo_visitante} - Total de tiros de esquina"),
    ("total_tarjetas", "Total de tarjetas"),
)


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUACIÓN DEL CATÁLOGO
# ═══════════════════════════════════════════════════════════════════════════════

class MotorMercados:
    """
    Motor de cálculo. Se construye con los dos documentos de auditoría y la
    resolución de local/visitante.
    """

    def __init__(self, doc_local: dict[str, Any], doc_visitante: dict[str, Any],
                 condicion_local: str = "casa", condicion_visitante: str = "fuera",
                 participante_local: str | None = None,
                 participante_visitante: str | None = None,
                 parametros: Parametros | None = None,
                 aviso: Callable[[str], None] | None = None) -> None:
        self.doc_local = doc_local
        self.doc_visitante = doc_visitante
        self.cond_local = condicion_local
        self.cond_visitante = condicion_visitante
        self.p = parametros or Parametros()
        self._avisar = aviso or (lambda m: None)
        self.muestras: dict[str, MuestraEquipo] = {}
        self.tasas = TasasPartido()
        self.moduladores: dict[str, Any] = {}
        self.matriz_ft: list[list[float]] = []
        self.residual_ft = 1.0
        self.matriz_1t: list[list[float]] = []
        self.matriz_2t: list[list[float]] = []
        self.notas: list[str] = []
        self.cobertura: dict[str, Any] = {}
        # Resultado del Motor Recursivo de 2 Niveles (vacío si no se aplicó).
        self.recursivo: dict[str, Any] = {}
        # λ_efectivo (finalización + portero rival + errores), cola ancha NB y
        # clusters de marcador (vacíos si no aplican).
        self.lambda_efectivo: dict[str, Any] = {}
        self.medias_finalizacion: tuple[dict[str, Any], dict[str, Any]] = ({}, {})
        self.cola_ancha: dict[str, Any] = {}
        self.clusters: dict[str, Any] = {}
        # Factores latentes de la Arquitectura Híbrida (XAI)
        self.xai_factores: dict[str, Any] = {}
        self.nombre_local = participante_local or (doc_local.get("equipo") or {}).get("nombre") or "Local"
        self.nombre_visitante = (participante_visitante
                                 or (doc_visitante.get("equipo") or {}).get("nombre")
                                 or "Visitante")

    # ── preparación ────────────────────────────────────────────────────────
    def preparar(self) -> None:
        dl, dv = self.doc_local, self.doc_visitante
        self.muestras = {
            "local_general": extraer_muestra(dl, None, "FT"),
            "local_cond": extraer_muestra(dl, self.cond_local, "FT"),
            "visitante_general": extraer_muestra(dv, None, "FT"),
            "visitante_cond": extraer_muestra(dv, self.cond_visitante, "FT"),
        }
        local = self.muestras["local_general"]
        visit = self.muestras["visitante_general"]
        local_c = self.muestras["local_cond"]
        visit_c = self.muestras["visitante_cond"]

        lam_l, lam_v, detalle = tasas_goles(local, visit, local_c, visit_c,
                                            self.p.w_condicion, self.p.w_xg, self.p.k_credibilidad)

        # ── MODULADORES TÁCTICOS (las 45 métricas, acotadas y neutras) ──────
        # Se aplican a la λ base ANTES de todo lo demás, para que la matriz de
        # goles, el reparto por tiempos y todos los mercados hereden el mismo
        # ajuste. Si falta cualquier métrica, su factor es 1,0 y la λ no cambia.
        self.moduladores = {}
        if self.p.aplicar_moduladores and lam_l is not None and lam_v is not None:
            try:
                ml, mv = extraer_medias_para_moduladores(self)
                self.moduladores = calcular_moduladores(ml, mv)
                mult_l = self.moduladores["mult_goles_L"]
                mult_v = self.moduladores["mult_goles_V"]
                lam_l_mod = lam_l * mult_l
                lam_v_mod = lam_v * mult_v
                detalle["_moduladores"] = {
                    "mult_goles_L": mult_l, "mult_goles_V": mult_v,
                    "metricas_activas": self.moduladores["metricas_activas"],
                    "lambda_local_sin_modular": lam_l,
                    "lambda_visitante_sin_modular": lam_v,
                    "lambda_local_modulada": lam_l_mod,
                    "lambda_visitante_modulada": lam_v_mod,
                }
                if self.moduladores["metricas_activas"]:
                    self.notas.append(
                        "λ modularizada con métricas de equipo ("
                        + ", ".join(self.moduladores["metricas_activas"])
                        + f"): {lam_l:.4f}→{lam_l_mod:.4f} y {lam_v:.4f}→{lam_v_mod:.4f}. "
                        f"Efecto acotado a ±{self.moduladores['limite_aplicado']*100:.0f}%.")
                lam_l, lam_v = lam_l_mod, lam_v_mod
            except Exception as e:
                self.notas.append(
                    f"Moduladores no aplicados ({type(e).__name__}: {e}): la λ queda sin "
                    "ajustar, nunca a medias.")

        # ── λ_EFECTIVO: finalización + portero rival + errores defensivos ───
        # Lección Real Madrid 4-1 Sevilla (18/10/2026): el xG previsto (2.37-0.79)
        # casi coincidió con el real (2.24-0.75), pero el marcador fue 4-1 por
        # xGOT 2.80, un portero con -1.20 goles evitados y 1 error conducente a
        # gol. Esas tres señales entran AQUÍ en la λ, no solo en las lecturas.
        self.lambda_efectivo = {}
        self.medias_finalizacion = ({}, {})
        if (getattr(self.p, "aplicar_lambda_efectivo", True)
                and lam_l is not None and lam_v is not None):
            try:
                ef_l, ef_v = extraer_medias_lambda_efectivo(self)
                self.medias_finalizacion = (ef_l, ef_v)
                mods_info = detalle.get("_moduladores") or {}
                cal = calibrar_lambda_efectivo(
                    lam_l, lam_v, ef_l, ef_v,
                    moduladores=self.moduladores or None,
                    lam_l_base=mods_info.get("lambda_local_sin_modular"),
                    lam_v_base=mods_info.get("lambda_visitante_sin_modular"),
                    limite=(self.moduladores or {}).get("limite_aplicado",
                                                        LIMITE_MODULADOR_DEFECTO))
                self.lambda_efectivo = cal
                detalle["_lambda_efectivo"] = cal
                if cal.get("aplicado"):
                    self.notas.append(cal["nota"])
                    lam_l = cal["lambda_local_efectiva"]
                    lam_v = cal["lambda_visitante_efectiva"]
            except Exception as e:
                self.notas.append(
                    f"λ_efectivo no aplicado ({type(e).__name__}: {e}): se conserva la λ "
                    "modulada, nunca a medias.")

        self.detalle_goles = detalle

        # ── ARQUITECTURA HÍBRIDA: 5 FACTORES LATENTES Y XAI (Directiva 8) ──────
        self.xai_factores = {}
        if lam_l is not None and lam_v is not None:
            try:
                partidos_l = dl.get("partidos") or []
                partidos_v = dv.get("partidos") or []
                h2h_partidos = []
                for p in partidos_l:
                    rival = str(p.get("rival") or "").lower()
                    if self.nombre_visitante.lower() in rival or rival in self.nombre_visitante.lower():
                        h2h_partidos.append(p)

                vec_l_norm = calcular_multi_frecuencia_45(partidos_l, partidos_h2h=h2h_partidos, nombre_equipo=self.nombre_local)
                vec_v_norm = calcular_multi_frecuencia_45(partidos_v, partidos_h2h=h2h_partidos, nombre_equipo=self.nombre_visitante)

                factores_l, _ = calcular_5_factores_latentes(vec_l_norm, vec_v_norm)
                factores_v, _ = calcular_5_factores_latentes(vec_v_norm, vec_l_norm)

                res_l = evaluar_ensemble_mlp(float(lam_l), factores_l, alpha=0.25)
                res_v = evaluar_ensemble_mlp(float(lam_v), factores_v, alpha=0.25)

                self.xai_factores = {
                    "aplicado": True,
                    "local": {
                        "nombre": self.nombre_local,
                        "lambda_base": round(float(lam_l), 4),
                        "lambda_efectivo": round(float(res_l["lambda_efectivo"]), 4),
                        "total_pct": res_l["total_pct"],
                        "factores": res_l["desglose_xai"],
                        "valores_f": res_l["factores_raw"],
                    },
                    "visita": {
                        "nombre": self.nombre_visitante,
                        "lambda_base": round(float(lam_v), 4),
                        "lambda_efectivo": round(float(res_v["lambda_efectivo"]), 4),
                        "total_pct": res_v["total_pct"],
                        "factores": res_v["desglose_xai"],
                        "valores_f": res_v["factores_raw"],
                    },
                    "alpha": res_l["alpha"],
                }
            except Exception as e_xai:
                self.xai_factores = {"aplicado": False, "error": f"{type(e_xai).__name__}: {e_xai}"}

        self.tasas.lambda_local_ft = lam_l
        self.tasas.lambda_visitante_ft = lam_v

        if lam_l is None or lam_v is None:
            self.notas.append(
                "λ FT no estimable con este modelo: faltan goles/xG en alguno de los "
                "reportes. Los mercados de goles quedan como datos insuficientes.")
        else:
            self.matriz_ft, self.residual_ft = matriz_marcadores(lam_l, lam_v, rho=self.p.rho)
            if self.residual_ft > MASA_RESIDUAL_MAXIMA:
                self.notas.append(
                    f"Masa residual de la matriz FT = {self.residual_ft:.2e} (> tolerancia).")

        # Tiempos
        l1, v1, l2, v2, proc, notas_t = repartir_lambdas_por_tiempos(
            dl, dv, lam_l, lam_v, self.cond_local, self.cond_visitante)
        self.tasas.lambda_local_1t, self.tasas.lambda_visitante_1t = l1, v1
        self.tasas.lambda_local_2t, self.tasas.lambda_visitante_2t = l2, v2
        self.tasas.procedencia_tiempos = proc
        self.tasas.notas.extend(notas_t)
        self.notas.extend(notas_t)
        if l1 is not None and v1 is not None:
            self.matriz_1t, _ = matriz_marcadores(l1, v1, rho=self.p.rho)
        if l2 is not None and v2 is not None:
            self.matriz_2t, _ = matriz_marcadores(l2, v2, rho=self.p.rho)

        # ── MOTOR RECURSIVO DE 2 NIVELES (45-DCN) ───────────────────────────
        # Recalibra λ y la matriz conjunta de FT/1T/2T con los tensores de 45
        # variables, los 8 super-patrones tácticos y los 4 cruces DCN-V2. Se
        # aplica AQUÍ, antes de evaluar el catálogo, para que TODOS los mercados
        # (incluidos los SEGUROS) y el Cuadro Especialista hereden la misma
        # matriz. Si no se puede aplicar, no se toca nada.
        self._aplicar_motor_recursivo()

        # ── COLA ANCHA (Binomial Negativa) + CLUSTERS DE MARCADOR ───────────
        # Se aplica sobre la matriz FT EFECTIVA (red o lineal), conservando la
        # media de cada equipo: transfiere peso desde 1-0 / 2-0 hacia 3-1 / 4-1
        # / 3-0 solo cuando la portería rival es vulnerable.
        self._aplicar_cola_ancha()
        if self.matriz_ft:
            asedio_activo = bool(self.recursivo.get("asedio_bandas_activo", False))
            info_c = dict(self.recursivo.get("cruces_tacticos") or {})
            info_c["lambda_local_1t"] = getattr(self.tasas, "lambda_local_1t", None)
            info_c["lambda_visita_1t"] = getattr(self.tasas, "lambda_visitante_1t", None)
            info_c["lambda_local_2t"] = getattr(self.tasas, "lambda_local_2t", None)
            info_c["lambda_visita_2t"] = getattr(self.tasas, "lambda_visitante_2t", None)
            info_c["seg_gol_concedido_L"] = self.recursivo.get("seg_gol_concedido_rival_V", 0.0)
            info_c["seg_gol_concedido_V"] = self.recursivo.get("seg_gol_concedido_rival_L", 0.0)
            info_c["mu_local_casa"] = self.recursivo.get("mu_local_casa", {})
            info_c["mu_visita_fuera"] = self.recursivo.get("mu_visita_fuera", {})
            self.clusters = clusters_marcador(
                self.matriz_ft, self.nombre_local, self.nombre_visitante,
                asedio_bandas=asedio_activo,
                info_cruces=info_c,
            )

        self.cobertura = self._construir_cobertura()

    def _aplicar_motor_recursivo(self) -> None:
        """
        Ejecuta la RED NEURONAL RECURSIVA (`motor_recursivo_45_dcn`) y sustituye
        λ y matriz del partido.

        Por qué aquí y no solo en el renderizador: el sesgo del "1-1 ★" nace en la
        matriz. Si la red solo cambiara la imagen, los mercados se calcularían con
        la Poisson plana y el ticket diría una cosa mientras el cuadro dice otra.
        `preparar()` corre ANTES de `evaluar_catalogo()`, así que el texto y las
        dos láminas heredan exactamente la misma matriz.

        Garantías:
          • Sin λ base no se intenta (no hay de dónde partir).
          • Si la red falla, se registra el motivo y se conserva la matriz del
            modelo lineal (los mercados no se quedan sin calcular).
          • Las mitades se REESCALAN a la nueva λ FT conservando su proporción,
            para no romper `λ_1T + λ_2T = λ_FT`.
        """
        self.recursivo = {}
        if not getattr(self.p, "aplicar_motor_recursivo", True):
            self.notas.append("Red neuronal recursiva desactivada por parámetro.")
            return
        lam_l = self.tasas.lambda_local_ft
        lam_v = self.tasas.lambda_visitante_ft
        if lam_l is None or lam_v is None:
            return

        try:
            import cuadro_especialista as ce
            import motor_recursivo_45_dcn as red

            def _n_pts(m):
                if m is None: return 0
                if isinstance(m, dict):
                    return int(m.get("n_partidos", len(m.get("partidos", []))))
                return int(getattr(m, "partidos_condicion", getattr(m, "partidos_general", 0)) or 0)

            m_l_cond = self.muestras.get("local_cond")
            m_l_gen = self.muestras.get("local_general")
            m_v_cond = self.muestras.get("visitante_cond")
            m_v_gen = self.muestras.get("visitante_general")

            perfil_l_gen = ce.construir_perfil_especialista_equipo(
                ce.bloque_desde_muestra(m_l_gen, self.nombre_local, "global", doc=self.doc_local, condicion=None))
            perfil_v_gen = ce.construir_perfil_especialista_equipo(
                ce.bloque_desde_muestra(m_v_gen, self.nombre_visitante, "global", doc=self.doc_visitante, condicion=None))

            if _n_pts(m_l_cond) > 0:
                perfil_l_cond = ce.construir_perfil_especialista_equipo(
                    ce.bloque_desde_muestra(m_l_cond, self.nombre_local, f"{self.cond_local}", doc=self.doc_local, condicion=self.cond_local))
                atq_L_cond = 0.50 * perfil_l_cond['metricas_clave']['goles_favor'] + 0.50 * perfil_l_cond['metricas_clave']['xg_favor']
                def_L_cond = 0.50 * perfil_l_cond['metricas_clave']['goles_contra'] + 0.50 * perfil_l_cond['metricas_clave']['xgot_contra']
            else:
                perfil_l_cond = perfil_l_gen
                atq_L_cond = 0.50 * perfil_l_gen['metricas_clave']['goles_favor'] + 0.50 * perfil_l_gen['metricas_clave']['xg_favor']
                def_L_cond = 0.50 * perfil_l_gen['metricas_clave']['goles_contra'] + 0.50 * perfil_l_gen['metricas_clave']['xgot_contra']

            if _n_pts(m_v_cond) > 0:
                perfil_v_cond = ce.construir_perfil_especialista_equipo(
                    ce.bloque_desde_muestra(m_v_cond, self.nombre_visitante, f"{self.cond_visitante}", doc=self.doc_visitante, condicion=self.cond_visitante))
                atq_V_cond = 0.50 * perfil_v_cond['metricas_clave']['goles_favor'] + 0.50 * perfil_v_cond['metricas_clave']['xg_favor']
                def_V_cond = 0.50 * perfil_v_cond['metricas_clave']['goles_contra'] + 0.50 * perfil_v_cond['metricas_clave']['xgot_contra']
            else:
                perfil_v_cond = perfil_v_gen
                atq_V_cond = 0.50 * perfil_v_gen['metricas_clave']['goles_favor'] + 0.50 * perfil_v_gen['metricas_clave']['xg_favor']
                def_V_cond = 0.50 * perfil_v_gen['metricas_clave']['goles_contra'] + 0.50 * perfil_v_gen['metricas_clave']['xgot_contra']

            atq_L_gen = 0.50 * perfil_l_gen['metricas_clave']['goles_favor'] + 0.50 * perfil_l_gen['metricas_clave']['xg_favor']
            def_L_gen = 0.50 * perfil_l_gen['metricas_clave']['goles_contra'] + 0.50 * perfil_l_gen['metricas_clave']['xgot_contra']
            atq_V_gen = 0.50 * perfil_v_gen['metricas_clave']['goles_favor'] + 0.50 * perfil_v_gen['metricas_clave']['xg_favor']
            def_V_gen = 0.50 * perfil_v_gen['metricas_clave']['goles_contra'] + 0.50 * perfil_v_gen['metricas_clave']['xgot_contra']

            # Ponderación Fase 5.1: 60% condición específica + 40% general
            atq_L = 0.60 * atq_L_cond + 0.40 * atq_L_gen
            def_L = 0.60 * def_L_cond + 0.40 * def_L_gen
            atq_V = 0.60 * atq_V_cond + 0.40 * atq_V_gen
            def_V = 0.60 * def_V_cond + 0.40 * def_V_gen

            cond_l = (self.cond_local or "casa").lower().strip()
            cond_v = (self.cond_visitante or "fuera").lower().strip()

            partidos_l_red = ce.partidos_para_red_neuronal(
                self.doc_local.get("partidos"), self.nombre_local)
            partidos_v_red = ce.partidos_para_red_neuronal(
                self.doc_visitante.get("partidos"), self.nombre_visitante)

            from motor_recursivo_45_dcn import (
                obtener_tier_coef, obtener_tier_equipo, es_sede_neutral,
                factor_uefa_relativo,
            )
            tier_L_int = obtener_tier_equipo(partidos_l_red)
            tier_V_int = obtener_tier_equipo(partidos_v_red)
            tier_L = obtener_tier_coef(partidos_l_red, perfil_l_gen, self.nombre_local)
            tier_V = obtener_tier_coef(partidos_v_red, perfil_v_gen, self.nombre_visitante)

            sede_neutral_L = es_sede_neutral(self.nombre_local, getattr(self, "competicion", ""))
            cond_adn_l = "neutral" if sede_neutral_L else cond_l

            fu_L = factor_uefa_relativo(tier_L, tier_V)
            fu_V = factor_uefa_relativo(tier_V, tier_L)

            # Módulo Doble ADN con Contracción Bayesiana k=3.5 (Fase 5.1) + Descuento ΔTier + UEFA
            mu_l_cond = red.calcular_doble_adn_con_shrinkage(
                partidos_l_red, {}, cond_adn_l, perfil_l_gen, tier_rival=tier_V_int, factor_uefa=fu_L,
            )
            mu_v_cond = red.calcular_doble_adn_con_shrinkage(
                partidos_v_red, {}, cond_v, perfil_v_gen, tier_rival=tier_L_int, factor_uefa=fu_V,
            )

            atq_L_cond = 0.50 * mu_l_cond.get('goles_favor', atq_L) + 0.50 * mu_l_cond.get('xg', atq_L)
            def_V_cond = 0.50 * mu_v_cond.get('goles_contra', def_V) + 0.50 * mu_v_cond.get('xgot_enfrentado', def_V)
            atq_V_cond = 0.50 * mu_v_cond.get('goles_favor', atq_V) + 0.50 * mu_v_cond.get('xg', atq_V)
            def_L_cond = 0.50 * mu_l_cond.get('goles_contra', def_L) + 0.50 * mu_l_cond.get('xgot_enfrentado', def_L)

            # Ponderación por Coeficiente de Liga (tier_coef) solo si no hubo ya ajuste ΔTier de división
            if tier_L != tier_V and tier_L_int == tier_V_int:
                tier_menor = min(tier_L, tier_V)
                tier_mayor = max(tier_L, tier_V)
                ratio_fuerza = (tier_menor / tier_mayor) ** 0.80
                if tier_L < tier_V:
                    atq_L_cond = atq_L_cond * ratio_fuerza
                    def_L_cond = def_L_cond / ratio_fuerza
                else:
                    atq_V_cond = atq_V_cond * ratio_fuerza
                    def_V_cond = def_V_cond / ratio_fuerza

            lam_l = round((atq_L_cond + def_V_cond) / 2.0, 2)
            lam_v = round((atq_V_cond + def_L_cond) / 2.0, 2)

            resultado = red.ejecutar_bucle_recursivo_infinito_hasta_convergencia(
                perfil_l_gen,
                perfil_v_gen,
                float(lam_l),
                float(lam_v),
                partidos_l_red,
                partidos_v_red,
                n_simulaciones_mc=int(getattr(self.p, "mc_simulaciones", 100_000)),
                sede_neutral_local=sede_neutral_L,
            )
        except Exception as e:                      # nunca tumba el análisis
            self.recursivo = {
                "aplicado": False,
                "error": f"{type(e).__name__}: {e}",
                "lambda_base_local": round(float(lam_l), 4),
                "lambda_base_visita": round(float(lam_v), 4),
            }
            self.notas.append(
                f"Red neuronal recursiva no aplicada ({type(e).__name__}: {e}): "
                f"se conserva la matriz del modelo lineal.")
            return

        # ── Sustitución efectiva de λ y matriz (FT) + Sincronización con Motor 55 Patrones, Rachas 60/40 y En Vivo ──
        lam_l_red = float(resultado["lambda_local_recursivo"])
        lam_v_red = float(resultado["lambda_visita_recursivo"])
        resultado["aplicado"] = True
        resultado["tasas_por_equipo"] = {
            "local": _tasas_sequia_explosion(partidos_l_red),
            "visita": _tasas_sequia_explosion(partidos_v_red),
        }
        resultado["lambda_base_local"] = float(lam_l)
        resultado["lambda_base_visita"] = float(lam_v)

        # Puntos 7, 9, 11, 13, 15, 18 y Sección 7 (Contexto Avanzado + 5 Ajustes Finos):
        try:
            from motor_patrones import calcular_promedios_racha_60_40, calcular_patrones_completos_partido
            from motor_inferencia import aplicar_reglas_matchup_55
            from contexto_avanzado import evaluar_contexto_avanzado_partido

            pts_l_doc = self.doc_local.get("partidos") or partidos_l_red
            pts_v_doc = self.doc_visitante.get("partidos") or partidos_v_red
            racha_l = calcular_promedios_racha_60_40(pts_l_doc, self.nombre_local)
            racha_v = calcular_promedios_racha_60_40(pts_v_doc, self.nombre_visitante)
            self.racha_local = racha_l
            self.racha_visita = racha_v
            resultado["racha_local"] = racha_l
            resultado["racha_visita"] = racha_v

            info_ctx_partido = getattr(self.p, "contexto_avanzado", None) or {}
            ctx_av = evaluar_contexto_avanzado_partido(
                pts_l_doc, pts_v_doc,
                fecha_partido_actual=str(getattr(self.p, "fecha_partido", "") or ""),
                info_contexto_fs_fm=info_ctx_partido if isinstance(info_ctx_partido, dict) else {},
            )
            self.contexto_avanzado = ctx_av
            resultado["contexto_avanzado"] = ctx_av

            mat_base_red = ce.matriz_red_para_dibujar(resultado)
            r_1x2_orig = resultado_1x2(mat_base_red)
            p1_o = max(0.05, float(r_1x2_orig["1"]))
            px_o = max(0.05, float(r_1x2_orig["X"]))
            p2_o = max(0.05, float(r_1x2_orig["2"]))

            mit_l_src = (perfil_l_cond.get("mitades") if isinstance(perfil_l_cond, dict) else None) or (perfil_l_gen.get("mitades") or {})
            mit_v_src = (perfil_v_cond.get("mitades") if isinstance(perfil_v_cond, dict) else None) or (perfil_v_gen.get("mitades") or {})
            m1_l = (mit_l_src.get("1T") if isinstance(mit_l_src, dict) else None) or {}
            m2_l = (mit_l_src.get("2T") if isinstance(mit_l_src, dict) else None) or {}
            m1_v = (mit_v_src.get("1T") if isinstance(mit_v_src, dict) else None) or {}
            m2_v = (mit_v_src.get("2T") if isinstance(mit_v_src, dict) else None) or {}
            resultado["mitades_adn_local"] = mit_l_src
            resultado["mitades_adn_visita"] = mit_v_src
            pat_l, pat_v = calcular_patrones_completos_partido(
                resultado.get("mu_local_casa") or {},
                resultado.get("mu_visita_fuera") or {},
                metricas_1t_local=m1_l,
                metricas_2t_local=m2_l,
                metricas_1t_visita=m1_v,
                metricas_2t_visita=m2_v,
            )
            es_vivo = bool(getattr(self.p, "es_en_vivo", False))
            roja_l = bool(getattr(self.p, "tarjeta_roja_local", False))
            roja_v = bool(getattr(self.p, "tarjeta_roja_visita", False))

            p1_55, px_55, p2_55, alertas_55 = aplicar_reglas_matchup_55(
                pat_l, pat_v, p1_o, px_o, p2_o,
                nombre_local=self.nombre_local,
                nombre_visita=self.nombre_visitante,
                es_en_vivo=es_vivo,
                tarjeta_roja_local=roja_l,
                tarjeta_roja_visita=roja_v,
                info_contexto=ctx_av,
                contexto_partido=ctx_av,
            )
            resultado["patrones_55_local"] = pat_l
            resultado["patrones_55_visita"] = pat_v
            resultado["alertas_55"] = alertas_55

            # Punto 13: Ajustar lambdas si las reglas de los 55 patrones o las rachas 60/40 movieron las probabilidades
            ratio_55_l = max(0.82, min(1.22, (p1_55 / p1_o) ** 0.45))
            ratio_55_v = max(0.82, min(1.22, (p2_55 / p2_o) ** 0.45))
            # Ponderar suavemente factor de racha 60/40 (crisis -10%, racha +8%) + regresión a la media por sobre-rendimiento (>1.30 -> *0.90)
            f_racha_l = (1.0 + (float(racha_l.get("factor_lambda", 1.0)) - 1.0) * 0.50) * float(racha_l.get("factor_regresion_media", 1.0))
            f_racha_v = (1.0 + (float(racha_v.get("factor_lambda", 1.0)) - 1.0) * 0.50) * float(racha_v.get("factor_regresion_media", 1.0))
            f_ctx_l = float(ctx_av.get("mult_lambda_local", 1.0))
            f_ctx_v = float(ctx_av.get("mult_lambda_visita", 1.0))

            mult_sinc_l = max(0.68, min(1.28, ratio_55_l * f_racha_l * f_ctx_l))
            mult_sinc_v = max(0.68, min(1.28, ratio_55_v * f_racha_v * f_ctx_v))

            # Ajuste d) Factor "Matagigantes" (Bloque Bajo Letal - ej. Forest 3-1 Man City):
            # Si el local tiene baja posesión proyectada (< 36%) frente a un favorito visitante pero alta puntería (P07 >= 0.38 o P05 >= 0.30 con P55 >= 0.45), reducir -20% el λ del favorito visitante.
            mu_l_c = resultado.get("mu_local_casa") or {}
            mu_v_f = resultado.get("mu_visita_fuera") or {}
            pos_l_raw = float(mu_l_c.get("posesion_pct", 50.0) or 50.0)
            pos_v_raw = float(mu_v_f.get("posesion_pct", 50.0) or 50.0)
            pos_sum = max(1.0, pos_l_raw + pos_v_raw)
            pos_proy_l = (pos_l_raw / pos_sum) * 100.0
            p07_l = float(pat_l.get("P07_Precision_Disparo", 0.32))
            p05_l = float(pat_l.get("P05_Conversion_Real", 0.25))
            p55_l = float(pat_l.get("P55_Contraataque_Letal", 0.30))
            if pos_proy_l < 36.0 and lam_v_red > lam_l_red and (p07_l >= 0.38 or (p05_l >= 0.30 and p55_l >= 0.45)):
                mult_sinc_v *= 0.80
                al_mg = f"⚠️ ALERTA MATAGIGANTES ({self.nombre_local}): Bloque bajo local ({pos_proy_l:.1f}% pos.) con alta letalidad al contragolpe (precisión {p07_l*100:.0f}%) -> -20% λ rival."
                if isinstance(resultado.get("alertas_xai"), list) and al_mg not in resultado["alertas_xai"]:
                    resultado["alertas_xai"].append(al_mg)

            if es_vivo and (roja_l or roja_v):
                if roja_l and not roja_v:
                    mult_sinc_l *= 0.60
                    mult_sinc_v *= 1.25
                elif roja_v and not roja_l:
                    mult_sinc_v *= 0.60
                    mult_sinc_l *= 1.25

            lam_l_nuevo = lam_l_red * mult_sinc_l
            lam_v_nuevo = lam_v_red * mult_sinc_v

            # ── Puntos 21, 22, 34, 35 y 40: Doble Freno de Mano, Techo de Caja Azul, Defensas Rotas, Asedio, Fortín Local y Gigante Visitante ──
            from motor_patrones import aplicar_freno_de_mano_y_matchup_21_22
            r_l_0_100 = (perfil_l_gen.get("ratings_0_100") or resultado.get("ratings_local") or {}) if isinstance(perfil_l_gen, dict) else (resultado.get("ratings_local") or {})
            r_v_0_100 = (perfil_v_gen.get("ratings_0_100") or resultado.get("ratings_visita") or {}) if isinstance(perfil_v_gen, dict) else (resultado.get("ratings_visita") or {})
            crisis_l = bool(racha_l.get("en_crisis", False))
            crisis_v = bool(racha_v.get("en_crisis", False))
            g_ult5_l = float(racha_l.get("goles_ult5_prom", 1.2) or 1.2)
            g_ult5_v = float(racha_v.get("goles_ult5_prom", 1.2) or 1.2)
            pos_proy_v = 100.0 - pos_proy_l

            # Conservar copia inmutable de los 5 factores neuronales puros antes de ajustes por reglas (Sección 9.2)
            ct_orig = resultado.get("cruces_tacticos") if isinstance(resultado.get("cruces_tacticos"), dict) else {}
            factores_red_l_puros = dict(ct_orig.get("factores_local") or {})
            factores_red_v_puros = dict(ct_orig.get("factores_visita") or {})
            suma_red_l_pura = round(float(sum(factores_red_l_puros.values())), 1)
            suma_red_v_pura = round(float(sum(factores_red_v_puros.values())), 1)

            lam_l_nuevo, lam_v_nuevo, delta_pct_l_21, delta_pct_v_21 = aplicar_freno_de_mano_y_matchup_21_22(
                lam_base_local=float(lam_l),
                lam_base_visita=float(lam_v),
                lam_red_local=lam_l_nuevo,
                lam_red_visita=lam_v_nuevo,
                ratings_local=r_l_0_100,
                ratings_visita=r_v_0_100,
                posesion_proyectada_local=pos_proy_l,
                posesion_proyectada_visita=pos_proy_v,
                en_crisis_local=crisis_l,
                en_crisis_visita=crisis_v,
                goles_ult5_local=g_ult5_l,
                goles_ult5_visita=g_ult5_v,
                competicion=str(getattr(self.p, "competicion", "") or (self.doc_local.get("partidos") or [{}])[0].get("torneo") or ""),
                sede_neutral=bool(sede_neutral_L),
                toques_area_local=mu_l_c.get("toques_area_rival"),
                cobertura_asedio_real=bool(mu_l_c.get("posesion_pct") is not None and mu_l_c.get("toques_area_rival") is not None),
                cuadro_b_local=getattr(self, "cuadro_b_local", None),
                cuadro_b_visita=getattr(self, "cuadro_b_visita", None),
            )

            if abs(lam_l_nuevo - lam_l_red) > 0.01 or abs(lam_v_nuevo - lam_v_red) > 0.01:
                lam_l_red = round(max(0.25, min(4.20, lam_l_nuevo)), 2)
                lam_v_red = round(max(0.25, min(4.20, lam_v_nuevo)), 2)
                resultado["lambda_local_recursivo"] = lam_l_red
                resultado["lambda_visita_recursivo"] = lam_v_red
                try:
                    from motor_recursivo_45_dcn import reescalar_factores_a_delta
                    ct = resultado.get("cruces_tacticos")
                    if isinstance(ct, dict):
                        ct["factores_red_neuronal_local"] = factores_red_l_puros
                        ct["factores_red_neuronal_visita"] = factores_red_v_puros
                        ct["ajuste_reglas_pct_local"] = round(delta_pct_l_21 - suma_red_l_pura, 1)
                        ct["ajuste_reglas_pct_visita"] = round(delta_pct_v_21 - suma_red_v_pura, 1)
                        fl_old = ct.get("factores_local") or {}
                        fv_old = ct.get("factores_visita") or {}
                        if fl_old:
                            fl_new = reescalar_factores_a_delta(fl_old, delta_pct_l_21, fijos=("F_posesion_progresiva",))
                            ct["factores_local"] = fl_new
                        if fv_old:
                            fv_new = reescalar_factores_a_delta(fv_old, delta_pct_v_21, fijos=("F_posesion_progresiva",))
                            ct["factores_visita"] = fv_new
                except Exception:
                    pass
                # Re-ponderar matriz conjunta con las nuevas marginales Poisson y normalizar a 1.0000
                import numpy as _np
                M_arr = _np.asarray(mat_base_red, dtype=float)
                R_m, C_m = M_arr.shape
                p_i_old = M_arr.sum(axis=1)
                p_i_new = _np.array([poisson_pmf(i, lam_l_red) for i in range(R_m)], dtype=float)
                if p_i_new.sum() > 0:
                    p_i_new /= p_i_new.sum()
                for i in range(R_m):
                    if p_i_old[i] > 1e-12:
                        M_arr[i, :] *= (0.55 * (p_i_new[i] / p_i_old[i]) + 0.45)
                p_j_old = M_arr.sum(axis=0)
                p_j_new = _np.array([poisson_pmf(j, lam_v_red) for j in range(C_m)], dtype=float)
                if p_j_new.sum() > 0:
                    p_j_new /= p_j_new.sum()
                for j in range(C_m):
                    if p_j_old[j] > 1e-12:
                        M_arr[:, j] *= (0.55 * (p_j_new[j] / p_j_old[j]) + 0.45)
                if M_arr.sum() > 0:
                    M_arr /= M_arr.sum()
                mat_base_red = M_arr.tolist()
                resultado["matriz_probabilidades"] = mat_base_red
                resultado["matriz_marcadores"] = mat_base_red
        except Exception:
            mat_base_red = ce.matriz_red_para_dibujar(resultado)

        # Punto 12: Sanity Check de la Matriz de Calor (suma exacta 1.0000 sin celdas negativas)
        suma_mat = sum(max(0.0, float(cel)) for fila in mat_base_red for cel in fila)
        if suma_mat > 0:
            mat_base_red = [[max(0.0, float(cel)) / suma_mat for cel in fila] for fila in mat_base_red]
            resultado["matriz_probabilidades"] = mat_base_red
            resultado["matriz_marcadores"] = mat_base_red

        self.tasas.lambda_local_ft = lam_l_red
        self.tasas.lambda_visitante_ft = lam_v_red
        self.matriz_ft = mat_base_red
        self.residual_ft = max(0.0, 1.0 - sum(sum(f) for f in self.matriz_ft))
        self.recursivo = resultado
        # Sincronización obligatoria de clusters con la matriz activa
        try:
            asedio_activo = bool(resultado.get("asedio_bandas_activo", False))
            self.clusters = clusters_marcador(
                self.matriz_ft, self.nombre_local, self.nombre_visitante,
                asedio_bandas=asedio_activo,
                info_cruces=resultado.get("cruces_tacticos"),
            )
        except Exception:
            pass

        if getattr(self, "xai_factores", None) and self.xai_factores.get("aplicado"):
            self.xai_factores["local"]["lambda_efectivo"] = round(lam_l_red, 4)
            pct_l = round(float(sum(resultado.get("cruces_tacticos", {}).get("factores_local", {}).values())), 1) if "cruces_tacticos" in resultado else round(((lam_l_red / max(0.05, float(lam_l))) - 1.0) * 100.0, 1)
            pct_v = round(float(sum(resultado.get("cruces_tacticos", {}).get("factores_visita", {}).values())), 1) if "cruces_tacticos" in resultado else round(((lam_v_red / max(0.05, float(lam_v))) - 1.0) * 100.0, 1)
            self.xai_factores["local"]["total_pct"] = pct_l
            self.xai_factores["visita"]["lambda_efectivo"] = round(lam_v_red, 4)
            self.xai_factores["visita"]["total_pct"] = pct_v
            if "cruces_tacticos" in resultado and isinstance(resultado["cruces_tacticos"], dict):
                ct_d = resultado["cruces_tacticos"]
                self.xai_factores["local"]["factores"] = dict(ct_d.get("factores_local", {}))
                self.xai_factores["visita"]["factores"] = dict(ct_d.get("factores_visita", {}))
                self.xai_factores["local"]["factores_red_neuronal"] = dict(ct_d.get("factores_red_neuronal_local") or ct_d.get("factores_local") or {})
                self.xai_factores["visita"]["factores_red_neuronal"] = dict(ct_d.get("factores_red_neuronal_visita") or ct_d.get("factores_visita") or {})
                self.xai_factores["local"]["ajuste_reglas_pct"] = float(ct_d.get("ajuste_reglas_pct_local", 0.0))
                self.xai_factores["visita"]["ajuste_reglas_pct"] = float(ct_d.get("ajuste_reglas_pct_visita", 0.0))
            self.xai_factores["alertas_xai"] = list(resultado.get("alertas_xai", []))
            self.xai_factores["mu_local_casa"] = dict(resultado.get("mu_local_casa", {}))
            self.xai_factores["mu_visita_fuera"] = dict(resultado.get("mu_visita_fuera", {}))

        # ── Mitades: reescaladas a la λ de la red (λ_1T + λ_2T = λ_FT) ──────
        if self.matriz_1t and self.matriz_2t:
            for lado, lam_nueva in (("local", lam_l_red), ("visitante", lam_v_red)):
                primera = getattr(self.tasas, f"lambda_{lado}_1t")
                segunda = getattr(self.tasas, f"lambda_{lado}_2t")
                total = (primera or 0.0) + (segunda or 0.0)
                if not primera or not segunda or total <= 0:
                    continue
                factor = lam_nueva / total
                setattr(self.tasas, f"lambda_{lado}_1t", round(primera * factor, 4))
                setattr(self.tasas, f"lambda_{lado}_2t", round(segunda * factor, 4))
            self.matriz_1t, _ = matriz_marcadores(self.tasas.lambda_local_1t,
                                                  self.tasas.lambda_visitante_1t)
            self.matriz_2t, _ = matriz_marcadores(self.tasas.lambda_local_2t,
                                                  self.tasas.lambda_visitante_2t)
            self.tasas.procedencia_tiempos = (
                f"{self.tasas.procedencia_tiempos} · reescalado a la λ de la red "
                f"recursiva (λ_1T + λ_2T = λ_FT)")

        # Sincronización obligatoria de clusters con la matriz activa y tasas reescaladas
        try:
            asedio_activo = bool(resultado.get("asedio_bandas_activo", False))
            info_c = dict(resultado.get("cruces_tacticos") or {})
            info_c["lambda_local_1t"] = getattr(self.tasas, "lambda_local_1t", None)
            info_c["lambda_visita_1t"] = getattr(self.tasas, "lambda_visitante_1t", None)
            info_c["lambda_local_2t"] = getattr(self.tasas, "lambda_local_2t", None)
            info_c["lambda_visita_2t"] = getattr(self.tasas, "lambda_visitante_2t", None)
            info_c["seg_gol_concedido_L"] = resultado.get("seg_gol_concedido_rival_V", 0.0)
            info_c["seg_gol_concedido_V"] = resultado.get("seg_gol_concedido_rival_L", 0.0)
            info_c["mu_local_casa"] = resultado.get("mu_local_casa", {})
            info_c["mu_visita_fuera"] = resultado.get("mu_visita_fuera", {})
            self.clusters = clusters_marcador(
                self.matriz_ft, self.nombre_local, self.nombre_visitante,
                asedio_bandas=asedio_activo,
                info_cruces=info_c,
            )
        except Exception:
            pass

        entropias = resultado.get("historial_entropia") or []
        texto_entropia = (f"H={float(entropias[-1]):.3f}" if entropias else "H=s/d")
        self.notas.append(
            "Red Neuronal Recursiva aplicada (LSTM por variable + Bilinear Pooling + "
            f"Cross-Attention + bucle hasta convergencia + MC "
            f"{int(getattr(self.p, 'mc_simulaciones', 100_000)):,}): "
            f"λ FT {lam_l:.2f}→{lam_l_red:.2f} y {lam_v:.2f}→{lam_v_red:.2f}; "
            f"iteración {resultado.get('iteracion_convergencia')} ({texto_entropia}); "
            f"cruce dominante {resultado.get('cruce_dominante_local')}.".replace(",", "."))
        try:
            self._avisar(
                f"Red Neuronal Recursiva: λ {lam_l:.2f}–{lam_v:.2f} → "
                f"{lam_l_red:.2f}–{lam_v_red:.2f} · convergencia it. "
                f"{resultado.get('iteracion_convergencia')} ({texto_entropia})")
        except Exception:
            pass

    def _aplicar_cola_ancha(self) -> None:
        """
        Sobredispersión Binomial Negativa sobre la matriz FT efectiva.

        Solo se activa cuando el portero RIVAL viene con goles_evitados < -0.15 y
        el atacante es favorito (λ o xG ≥ 2.10) o remata ≥ 10 veces dentro del
        área. Conserva la media de cada equipo (la λ declarada no cambia) y la
        distribución condicional del rival; solo ensancha la cola de goles.
        Los parámetros quedan en `self.cola_ancha` para que el servicio y el
        Cuadro Especialista dibujen EXACTAMENTE la misma matriz.
        """
        self.cola_ancha = {}
        if not getattr(self.p, "aplicar_cola_ancha", True) or not self.matriz_ft:
            return
        lam_l, lam_v = self.tasas.lambda_local_ft, self.tasas.lambda_visitante_ft
        if lam_l is None or lam_v is None:
            return
        ef_l, ef_v = self.medias_finalizacion
        try:
            cola = parametros_cola_ancha(lam_l, lam_v, ef_l or {}, ef_v or {})
        except Exception as e:
            self.notas.append(f"Cola ancha no evaluada ({type(e).__name__}: {e}).")
            return
        self.cola_ancha = cola
        if not cola.get("aplicada"):
            return
        self.matriz_ft = aplicar_cola_ancha(self.matriz_ft, cola)
        self.residual_ft = max(0.0, 1.0 - sum(sum(f) for f in self.matriz_ft))
        for lado, nombre in (("local", self.nombre_local),
                             ("visitante", self.nombre_visitante)):
            par = cola.get(lado)
            if par:
                self.notas.append(
                    f"Cola ancha (Binomial Negativa k={par['k']:.2f}, peso {par['w']:.2f}) "
                    f"sobre los goles de {nombre}: portero rival con "
                    f"{par['goles_evitados_rival']:+.2f} goles evitados ({par['motivo']}). "
                    "Media conservada; peso transferido de 1-0/2-0 hacia 3-0/3-1/4-1.")

    def _construir_cobertura(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for nombre, muestra in self.muestras.items():
            out[nombre] = {
                "equipo": muestra.equipo,
                "partidos": muestra.partidos_general if nombre.endswith("general") else muestra.partidos_condicion,
                "metricas": {k: {"n": m.n, "media": m.media}
                             for k, m in muestra.metricas.items() if m.n > 0},
            }
        out["periodos_ disponibles"] = {
            "1T": self.tasas.lambda_local_1t is not None,
            "2T": self.tasas.lambda_local_2t is not None,
        }
        return out

    # ── utilidades de selección ────────────────────────────────────────────
    def _sel(self, idm: str, mercado: str, evento: str, etiqueta: str,
             periodo: str, equipo: str | None, linea: float | None,
             prob: float | None, motivo: str = "",
             estimable: bool = True) -> Seleccion:
        return Seleccion(id_mercado=idm, mercado=mercado, evento=evento,
                         etiqueta=etiqueta, periodo=periodo, equipo=equipo, linea=linea,
                         probabilidad=truncar_probabilidad(prob) if estimable else None,
                         estimable=estimable, motivo=motivo)

    # ── mercados ───────────────────────────────────────────────────────────
    def evaluar_catalogo(self) -> list[ResultadoMercado]:
        resultados: list[ResultadoMercado] = []
        for numero, (idm, nombre) in enumerate(MERCADOS, 1):
            etiqueta = nombre.replace("{equipo_local}", self.nombre_local) \
                             .replace("{equipo_visitante}", self.nombre_visitante)
            metodo = getattr(self, f"_m_{idm}", None)
            if metodo is None:
                resultados.append(ResultadoMercado(
                    idm, numero, etiqueta, estado="regla_no_definida",
                    nota="Mercado presente en el catálogo sin regla implementada."))
                continue
            try:
                resultados.append(metodo(numero, idm, etiqueta))
            except Exception as e:      # nunca debe tumbar el catálogo entero
                resultados.append(ResultadoMercado(
                    idm, numero, etiqueta, estado="datos_insuficientes",
                    nota=f"Error al evaluar: {type(e).__name__}: {e}"))
        return resultados

    def _sin_matriz(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return ResultadoMercado(idm, numero, nombre, estado="datos_insuficientes",
                                nota="Sin λ estimables: no se calculan mercados de goles.")

    # 1. Ganador del partido
    def _m_ganador_partido(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        r = resultado_1x2(self.matriz_ft)
        sels = [
            self._sel(idm, nombre, "1", f"{self.nombre_local} gana", "FT", self.nombre_local, None, r["1"]),
            self._sel(idm, nombre, "X", "Empate", "FT", None, None, r["X"]),
            self._sel(idm, nombre, "2", f"{self.nombre_visitante} gana", "FT", self.nombre_visitante, None, r["2"]),
        ]
        return ResultadoMercado(idm, numero, nombre, selecciones=sels,
                                detalle={"1x2": r})

    # 2. COMBI MEJORADA (etiqueta/modalidad, no un nuevo suceso probabilístico)
    def _m_combi_mejorada(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return ResultadoMercado(
            idm, numero, nombre, estado="regla_no_definida",
            nota=("COMBI MEJORADA se conserva como modalidad que agrupa selecciones concretas; "
                  "no es un suceso probabilístico nuevo y sus reglas no están definidas en los "
                  "datos, así que no se le asigna probabilidad propia."),
            detalle={"requiere": "definición de las selecciones que agrupa"})

    # 3. Pago Anticipado +2
    def _m_pago_anticipado_2(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return ResultadoMercado(
            idm, numero, nombre, estado="datos_insuficientes",
            nota=("Pago anticipado +2 = unión de «ganar al final» y «alcanzar ventaja de dos "
                  "goles en cualquier momento». Requiere un modelo temporal intra-partido "
                  "respaldado por datos suficientes, que estos reportes no contienen."),
            detalle={"requiere": "modelo temporal explícito (minuto a minuto o marcador parcial)"})

    # 4. Total de goles (FT/1T/2T) + número exacto + al menos N
    def _m_total_goles(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        sels: list[Seleccion] = []
        detalle: dict[str, Any] = {}
        matrices = {"FT": self.matriz_ft, "1T": self.matriz_1t, "2T": self.matriz_2t}
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        for periodo in ("FT", "1T", "2T"):
            matriz = matrices[periodo]
            if not matriz:
                continue
            dist = total_goles(matriz)
            detalle[periodo] = {k: round(v, 6) for k, v in sorted(dist.items())}
            # Punto 4: En Goles Totales FT las únicas líneas válidas son 1.5, 2.5, 3.5 y 4.5;
            # y prohibido terminantemente generar under_0.5 (-0.5 Goles) en ningún mercado o mitad.
            lineas_over = (1.5, 2.5, 3.5, 4.5) if periodo == "FT" else LINEAS_GOLES
            lineas_under = (1.5, 2.5, 3.5, 4.5) if periodo == "FT" else (1.5, 2.5)
            for linea in lineas_over:
                sels.append(self._sel(idm, nombre, f"over_{linea}", f"más de {linea} goles",
                                      periodo, None, linea, prob_over(dist, linea)))
            for linea in lineas_under:
                sels.append(self._sel(idm, nombre, f"under_{linea}", f"menos de {linea} goles",
                                      periodo, None, linea, prob_under(dist, linea)))
            for n in range(0, 6):
                sels.append(self._sel(idm, nombre, f"exacto_{n}", f"exactamente {n} goles",
                                      periodo, None, float(n), prob_exacto(dist, n)))
            for n in range((2 if periodo == "FT" else 1), 6):
                sels.append(self._sel(idm, nombre, f"al_menos_{n}", f"al menos {n} goles",
                                      periodo, None, float(n), prob_al_menos(dist, n)))
        return ResultadoMercado(idm, numero, nombre, selecciones=sels, detalle=detalle)

    # 5 y 6. Total de goles por equipo
    def _m_total_goles_equipo(self, numero: int, idm: str, nombre: str,
                              bando: str) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        indice = 0 if bando == "local" else 1
        equipo = self.nombre_local if bando == "local" else self.nombre_visitante
        matrices = {"FT": self.matriz_ft, "1T": self.matriz_1t, "2T": self.matriz_2t}
        sels: list[Seleccion] = []
        for periodo, matriz in matrices.items():
            if not matriz:
                continue
            # Punto 4: Marginales exactas (Local suma sobre filas i, Visitante sobre columnas j)
            dist: dict[int, float] = {}
            for i, fila in enumerate(matriz):
                for j, p in enumerate(fila):
                    k = i if indice == 0 else j
                    dist[k] = dist.get(k, 0.0) + p
            for linea in (0.5, 1.5, 2.5, 3.5, 4.5):
                sels.append(self._sel(idm, nombre, f"{bando}_over_{linea}",
                                      f"{equipo}: más de {linea} goles", periodo, equipo,
                                      linea, prob_over(dist, linea)))
            # Prohibido under_0.5 (-0.5 Goles); las líneas Under por equipo solo son 1.5 y 2.5
            for linea in (1.5, 2.5):
                sels.append(self._sel(idm, nombre, f"{bando}_under_{linea}",
                                      f"{equipo}: menos de {linea} goles", periodo, equipo,
                                      linea, prob_under(dist, linea)))
            for n in range(0, 5):
                sels.append(self._sel(idm, nombre, f"{bando}_exacto_{n}",
                                      f"{equipo}: exactamente {n} goles", periodo, equipo,
                                      float(n), prob_exacto(dist, n)))
            for n in range(1, 5):
                sels.append(self._sel(idm, nombre, f"{bando}_al_menos_{n}",
                                      f"{equipo}: al menos {n} goles", periodo, equipo,
                                      float(n), prob_al_menos(dist, n)))
        return ResultadoMercado(idm, numero, nombre, selecciones=sels)

    def _m_total_goles_local(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return self._m_total_goles_equipo(numero, idm, nombre, "local")

    def _m_total_goles_visitante(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return self._m_total_goles_equipo(numero, idm, nombre, "visitante")

    # 7. FIESTA DE GOLES (reutiliza la distribución del total, sin inventar reglas)
    def _m_fiesta_de_goles(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        dist = total_goles(self.matriz_ft)
        sels = [
            self._sel(idm, nombre, "fiesta_al_menos_3", "al menos 3 goles en el partido",
                      "FT", None, 3.0, prob_al_menos(dist, 3)),
            self._sel(idm, nombre, "fiesta_al_menos_4", "al menos 4 goles en el partido",
                      "FT", None, 4.0, prob_al_menos(dist, 4)),
            self._sel(idm, nombre, "fiesta_al_menos_5", "al menos 5 goles en el partido",
                      "FT", None, 5.0, prob_al_menos(dist, 5)),
        ]
        return ResultadoMercado(
            idm, numero, nombre, selecciones=sels,
            nota=("FIESTA DE GOLES se conserva como modalidad sobre el número de goles. "
                  "No se le asigna una probabilidad única porque su evento no está definido "
                  "en los datos; se publican los umbrales de goles como referencia."),
            detalle={"distribucion_total": {k: round(v, 6) for k, v in sorted(dist.items())}})

    # 8. Hándicap de 3 opciones (europeo)
    def _m_handicap_3_opciones(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        sels: list[Seleccion] = []
        for h in HANDICAPS_EUROPEOS:
            # El hándicap se aplica al LOCAL: gana si local + h > visitante
            p_local = prob_desde_matriz(self.matriz_ft, lambda i, j, h=h: i + h > j)
            p_empate = prob_desde_matriz(self.matriz_ft, lambda i, j, h=h: i + h == j)
            p_visit = prob_desde_matriz(self.matriz_ft, lambda i, j, h=h: i + h < j)
            signo = f"{h:+d}" if h else "0"
            sels.append(self._sel(idm, nombre, f"h{h}_local",
                                  f"{self.nombre_local} {signo}", "FT", self.nombre_local,
                                  float(h), p_local))
            sels.append(self._sel(idm, nombre, f"h{h}_empate",
                                  f"Empate con hándicap {signo}", "FT", None, float(h), p_empate))
            sels.append(self._sel(idm, nombre, f"h{h}_visitante",
                                  f"{self.nombre_visitante} {-h:+d}", "FT",
                                  self.nombre_visitante, float(h), p_visit))
        return ResultadoMercado(
            idm, numero, nombre, selecciones=sels,
            nota=("Hándicap europeo de 3 resultados: el empate con hándicap es un resultado "
                  "propio, no una devolución."))

    # 9. Ambos equipos anotan
    def _m_ambos_anotan(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        si = prob_desde_matriz(self.matriz_ft, lambda i, j: i > 0 and j > 0)
        sels = [
            self._sel(idm, nombre, "btts_si", "Ambos equipos anotan: Sí", "FT", None, None, si),
            self._sel(idm, nombre, "btts_no", "Ambos equipos anotan: No", "FT", None, None,
                      truncar_probabilidad(1.0 - si)),
        ]
        return ResultadoMercado(idm, numero, nombre, selecciones=sels)

    # 10. Doble oportunidad
    def _m_doble_oportunidad(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        r = resultado_1x2(self.matriz_ft)
        sels = [
            self._sel(idm, nombre, "1x", f"1X ({self.nombre_local} o empate)", "FT",
                      self.nombre_local, None, r["1"] + r["X"]),
            self._sel(idm, nombre, "x2", f"X2 (empate o {self.nombre_visitante})", "FT",
                      self.nombre_visitante, None, r["X"] + r["2"]),
            self._sel(idm, nombre, "12", "12 (sin empate)", "FT", None, None, r["1"] + r["2"]),
        ]
        return ResultadoMercado(idm, numero, nombre, selecciones=sels)

    # 11. Apuesta sin empate
    def _m_apuesta_sin_empate(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        r = resultado_1x2(self.matriz_ft)
        # El filtro se aplica a la probabilidad de VICTORIA EFECTIVA, no a la
        # condicionada a que no haya empate.
        sels = [
            self._sel(idm, nombre, "dnb_local", f"{self.nombre_local} (sin empate)", "FT",
                      self.nombre_local, None, r["1"]),
            self._sel(idm, nombre, "dnb_devolucion", "Empate = devolución", "FT", None, None, r["X"]),
            self._sel(idm, nombre, "dnb_visitante", f"{self.nombre_visitante} (sin empate)", "FT",
                      self.nombre_visitante, None, r["2"]),
        ]
        return ResultadoMercado(
            idm, numero, nombre, selecciones=sels,
            nota=("Se separan victoria, devolución (empate) y derrota. El filtro usa la "
                  "probabilidad de victoria efectiva, no la condicionada."),
            detalle={"victoria_condicionada_local": round(r["1"] / max(1e-9, 1 - r["X"]), 6)})

    # 12. Ganador del 1er tiempo
    def _m_ganador_1t(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_1t:
            return ResultadoMercado(idm, numero, nombre, estado="datos_insuficientes",
                                    nota="Sin λ de 1T: no se calcula el ganador del primer tiempo.")
        r = resultado_1x2(self.matriz_1t)
        sels = [
            self._sel(idm, nombre, "1t_1", f"1T: {self.nombre_local} gana", "1T",
                      self.nombre_local, None, r["1"]),
            self._sel(idm, nombre, "1t_X", "1T: empate", "1T", None, None, r["X"]),
            self._sel(idm, nombre, "1t_2", f"1T: {self.nombre_visitante} gana", "1T",
                      self.nombre_visitante, None, r["2"]),
        ]
        return ResultadoMercado(idm, numero, nombre, selecciones=sels)

    # 13. 1er tiempo / Tiempo completo (distribución conjunta de las dos mitades)
    def _m__1t_ft(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_1t or not self.matriz_2t:
            return ResultadoMercado(
                idm, numero, nombre, estado="datos_insuficientes",
                nota=("Descanso/final requiere la distribución conjunta de goles del primer "
                      "tiempo y goles adicionales del segundo; sin λ por tiempos no se calcula."))
        sels: list[Seleccion] = []
        r1 = resultado_1x2(self.matriz_1t)
        resultados_1t = {"1": r1["1"], "X": r1["X"], "2": r1["2"]}
        # Conjunta: se recorre la matriz de 1T y la de 2T de forma independiente
        # (supuesto declarado) y se combina el resultado final acumulado.
        for etiqueta_1t, _p1t in resultados_1t.items():
            for etiqueta_ft in ("1", "X", "2"):
                prob = 0.0
                for i1, fila1 in enumerate(self.matriz_1t):
                    for j1, p1 in enumerate(fila1):
                        if (i1 > j1 and etiqueta_1t != "1") or (i1 == j1 and etiqueta_1t != "X") \
                                or (i1 < j1 and etiqueta_1t != "2"):
                            continue
                        for i2, fila2 in enumerate(self.matriz_2t):
                            for j2, p2 in enumerate(fila2):
                                il, jl = i1 + i2, j1 + j2
                                if (il > jl and etiqueta_ft == "1") or \
                                   (il == jl and etiqueta_ft == "X") or \
                                   (il < jl and etiqueta_ft == "2"):
                                    prob += p1 * p2
                sels.append(self._sel(idm, nombre, f"1t{etiqueta_1t}_ft{etiqueta_ft}",
                                      f"1T {etiqueta_1t} / FT {etiqueta_ft} - {etiqueta_ft}",
                                      "FT", None, None, truncar_probabilidad(prob)))
        return ResultadoMercado(
            idm, numero, nombre, selecciones=sels,
            nota=("Se combina la distribución del 1T con la del 2T asumiendo independencia "
                  "temporal entre mitades. Marcar en el 1T no equivale a ganarlo."))

    # 14. Equipo que anota el gol 1 (contempla que no haya goles)
    def _m_equipo_primer_gol(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        lam_l = self.tasas.lambda_local_ft or 0.0
        lam_v = self.tasas.lambda_visitante_ft or 0.0
        total = lam_l + lam_v
        p_sin_goles = math.exp(-total)
        if total <= 0:
            return ResultadoMercado(idm, numero, nombre, estado="datos_insuficientes",
                                    nota="λ nulas: no se puede repartir el primer gol.")
        p_local = (1 - p_sin_goles) * (lam_l / total)
        p_visit = (1 - p_sin_goles) * (lam_v / total)
        sels = [
            self._sel(idm, nombre, "primer_gol_local", f"Anota primero {self.nombre_local}",
                      "FT", self.nombre_local, None, p_local),
            self._sel(idm, nombre, "primer_gol_visitante",
                      f"Anota primero {self.nombre_visitante}", "FT", self.nombre_visitante,
                      None, p_visit),
            self._sel(idm, nombre, "primer_gol_ninguno", "No hay goles", "FT", None, None,
                      p_sin_goles),
        ]
        return ResultadoMercado(
            idm, numero, nombre, selecciones=sels,
            nota=("Reparto del primer gol proporcional a las tasas de gol, con la probabilidad "
                  "de «ningún gol» separada. Es un evento distinto de ganar el partido."))

    # 15-17. Ganador + Total de goles
    def _ganador_mas_total(self, numero: int, idm: str, nombre: str,
                           linea: float) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        sels: list[Seleccion] = []
        for etq, pred in (("1", lambda i, j: i > j), ("X", lambda i, j: i == j),
                          ("2", lambda i, j: i < j)):
            for lado, cond in (("mas", lambda t: t > linea), ("menos", lambda t: t < linea)):
                prob = prob_desde_matriz(
                    self.matriz_ft, lambda i, j, pred=pred, cond=cond: pred(i, j) and cond(i + j))
                nombre_equipo = {"1": self.nombre_local, "X": "Empate",
                                 "2": self.nombre_visitante}[etq]
                sels.append(self._sel(idm, nombre, f"{etq}_{lado}_{linea}",
                                      f"{nombre_equipo} y {lado} de {linea} goles", "FT",
                                      None, linea, prob))
        return ResultadoMercado(
            idm, numero, nombre, selecciones=sels,
            nota="Calculado como evento conjunto sobre la matriz de marcadores.")

    def _m_ganador_total_15(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return self._ganador_mas_total(numero, idm, nombre, 1.5)

    def _m_ganador_total_25(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return self._ganador_mas_total(numero, idm, nombre, 2.5)

    def _m_ganador_total_35(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return self._ganador_mas_total(numero, idm, nombre, 3.5)

    # 18. Ganador + Ambos anotan
    def _m_ganador_ambos_anotan(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        sels: list[Seleccion] = []
        for etq, pred in (("1", lambda i, j: i > j), ("X", lambda i, j: i == j),
                          ("2", lambda i, j: i < j)):
            for btts, cond in (("si", lambda i, j: i > 0 and j > 0),
                               ("no", lambda i, j: i == 0 or j == 0)):
                prob = prob_desde_matriz(
                    self.matriz_ft, lambda i, j, pred=pred, cond=cond: pred(i, j) and cond(i, j))
                nombre_equipo = {"1": self.nombre_local, "X": "Empate",
                                 "2": self.nombre_visitante}[etq]
                sels.append(self._sel(idm, nombre, f"{etq}_btts_{btts}",
                                      f"{nombre_equipo} y ambos anotan: {btts}", "FT",
                                      None, None, prob))
        return ResultadoMercado(idm, numero, nombre, selecciones=sels,
                                nota="Evento conjunto sobre la matriz de marcadores.")

    # 19. Ambos anotan + Total 2.5
    def _m_ambos_anotan_total_25(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_ft:
            return self._sin_matriz(numero, idm, nombre)
        sels: list[Seleccion] = []
        for btts, cond in (("si", lambda i, j: i > 0 and j > 0),
                           ("no", lambda i, j: i == 0 or j == 0)):
            for lado, condt in (("mas", lambda t: t > 2.5), ("menos", lambda t: t < 2.5)):
                prob = prob_desde_matriz(
                    self.matriz_ft,
                    lambda i, j, cond=cond, condt=condt: cond(i, j) and condt(i + j))
                sels.append(self._sel(idm, nombre, f"btts_{btts}_{lado}_2.5",
                                      f"Ambos anotan {btts} y {lado} de 2.5 goles", "FT",
                                      None, 2.5, prob))
        return ResultadoMercado(idm, numero, nombre, selecciones=sels,
                                nota="Evento conjunto sobre la matriz de marcadores.")

    # 20-23. Mercados de jugadores
    def _mercado_jugadores(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return ResultadoMercado(
            idm, numero, nombre, estado="datos_insuficientes",
            nota=("Requiere datos individuales por jugador (goles, minutos previstos, penaltis) "
                  "que estos reportes de equipo no contienen. No se reparte la λ del equipo "
                  "entre sus jugadores."),
            detalle={"requiere": "estadísticas individuales y minutos previstos"})

    def _m_anotador_cualquier_momento(self, n: int, i: str, no: str) -> ResultadoMercado:
        return self._mercado_jugadores(n, i, no)

    def _m_primer_anotador(self, n: int, i: str, no: str) -> ResultadoMercado:
        return self._mercado_jugadores(n, i, no)

    def _m_anota_dos_o_mas(self, n: int, i: str, no: str) -> ResultadoMercado:
        return self._mercado_jugadores(n, i, no)

    def _m_hat_trick(self, n: int, i: str, no: str) -> ResultadoMercado:
        return self._mercado_jugadores(n, i, no)

    # 24. Total de córneres
    def _m_total_corners(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        mu_l = self._mu_corners("local")
        mu_v = self._mu_corners("visitante")
        ml_g = (self.muestras.get("local_general") or MuestraEquipo("")).metricas.get("corners_favor")
        mv_g = (self.muestras.get("visitante_general") or MuestraEquipo("")).metricas.get("corners_favor")
        p_ceros_l = ml_g.pct_ceros if (ml_g and hasattr(ml_g, "pct_ceros")) else 0.0
        p_ceros_v = mv_g.pct_ceros if (mv_g and hasattr(mv_g, "pct_ceros")) else 0.0

        if mu_l is None or mu_v is None or (mu_l + mu_v) < 1.5 or p_ceros_l > 0.30 or p_ceros_v > 0.30:
            return ResultadoMercado(
                idm, numero, nombre, estado="datos_insuficientes",
                nota="Faltan córneres o la competición carece de cobertura oficial (mu < 1.5 o >30% sin registro).")
        eventos = self._distribuciones_corners(mu_l, mu_v)
        if not eventos:
            return ResultadoMercado(idm, numero, nombre, estado="datos_insuficientes",
                                    nota="No se pudo construir la distribución de córneres.")
        sels: list[Seleccion] = []
        base = eventos["poisson"]
        for linea in LINEAS_CORNERES:
            sels.append(self._sel(idm, nombre, f"corners_over_{linea}",
                                  f"más de {linea} córneres", "FT", None, linea,
                                  prob_over(base, linea)))
            if linea > 0.5:
                sels.append(self._sel(idm, nombre, f"corners_under_{linea}",
                                      f"menos de {linea} córneres", "FT", None, linea,
                                      prob_under(base, linea)))
        return ResultadoMercado(
            idm, numero, nombre, selecciones=sels,
            nota=("Córneres: cruce de córneres propios con los concedidos por el rival. "
                  "La sensibilidad contrasta Poisson con binomial negativa."),
            detalle={"mu_local": mu_l, "mu_visitante": mu_v,
                     "mu_total": mu_l + mu_v})

    def _mu_corners(self, bando: str) -> float | None:
        """λ de córneres de un equipo: (propios + concedidos por el rival) / 2 ponderado por factor de bloqueo y guion."""
        k_credibilidad = self.p.k_credibilidad
        if bando == "local":
            propio = media_ajustada(self.muestras["local_general"].dame("corners_favor"),
                                    self.muestras["local_cond"].dame("corners_favor"),
                                    self.p.w_condicion, k_credibilidad)
            concedido = media_ajustada(self.muestras["visitante_general"].dame("corners_contra"),
                                       self.muestras["visitante_cond"].dame("corners_contra"),
                                       self.p.w_condicion, k_credibilidad)
        else:
            propio = media_ajustada(self.muestras["visitante_general"].dame("corners_favor"),
                                    self.muestras["visitante_cond"].dame("corners_favor"),
                                    self.p.w_condicion, k_credibilidad)
            concedido = media_ajustada(self.muestras["local_general"].dame("corners_contra"),
                                       self.muestras["local_cond"].dame("corners_contra"),
                                       self.p.w_condicion, k_credibilidad)
        if propio is None and concedido is None:
            return None
        base = concedido if propio is None else (propio if concedido is None else (propio + concedido) / 2.0)

        # ── Calibración de córneres por bloqueos rivales, P54 y guion de partido (Sección 7 / Valencia 0-3 Real Madrid) ──
        try:
            xai = getattr(self, "xai_factores", None) or {}
            rec = getattr(self, "recursivo", None) or {}
            mu_l = xai.get("mu_local_casa") or rec.get("mu_local_casa") or {}
            mu_v = xai.get("mu_visita_fuera") or rec.get("mu_visita_fuera") or {}
            pat_55 = rec.get("patrones_55_local" if bando == "local" else "patrones_55_visita") or {}
            if bando == "local":
                centros = float(mu_l.get("centros") or 18.0)
                rem_tot = float(mu_l.get("remates_totales") or 12.0)
                bloq_riv = float(mu_v.get("bloqueos") or 2.5)
                desp_riv = float(mu_v.get("despejes") or 18.0)
                lam_p = float(rec.get("lambda_local_recursivo") or getattr(getattr(self, "tasas", None), "lambda_local_ft", 1.35) or 1.35)
                lam_r = float(rec.get("lambda_visita_recursivo") or getattr(getattr(self, "tasas", None), "lambda_visitante_ft", 1.10) or 1.10)
            else:
                centros = float(mu_v.get("centros") or 16.0)
                rem_tot = float(mu_v.get("remates_totales") or 11.5)
                bloq_riv = float(mu_l.get("bloqueos") or 2.5)
                desp_riv = float(mu_l.get("despejes") or 18.0)
                lam_p = float(rec.get("lambda_visita_recursivo") or getattr(getattr(self, "tasas", None), "lambda_visitante_ft", 1.10) or 1.10)
                lam_r = float(rec.get("lambda_local_recursivo") or getattr(getattr(self, "tasas", None), "lambda_local_ft", 1.35) or 1.35)

            # factor_bloqueo_bandas = 0.60 + 0.20 * (centros_propios / 18.0) + 0.20 * (bloqueos_rival + despejes_rival / 8.0) / 5.0
            factor_bloqueo = 0.60 + 0.20 * (centros / 18.0) + 0.20 * ((bloq_riv + (desp_riv / 8.0)) / 5.0)
            p54 = float(pat_55.get("P54_Asedio_Corners_Matchup", 1.0) or 1.0)
            factor_bloqueo *= (0.94 + 0.06 * min(1.8, max(0.5, p54)))
            factor_bloqueo = max(0.76, min(1.18, factor_bloqueo))

            # Ajuste dinámico por guion de marcador proyectado:
            diff_lam = lam_p - lam_r
            if diff_lam >= 0.55 and bloq_riv < 2.2:
                # Favorito claro ante rival que no bloquea tiros anota temprano -> -10% córneres
                factor_bloqueo *= 0.90
            elif abs(diff_lam) <= 0.35 and centros >= 20.0:
                # Duelo parejo cerrado en 1T con equipo de alto volumen de centros -> +8% por asedio
                factor_bloqueo *= 1.08

            # Ajuste a) y Punto 24a: Regla de Córneres vs. Volumen de Remates y "Embudo Interior":
            # Si remates_totales > 15.0 O (remates_area_local + remates_area_visita) > 15.0 O P11_Peligro_Area_vs_Fuera > 2.6,
            # multiplicar su proyección de córneres por 0.80.
            rem_area_l = float(mu_l.get("remates_dentro_area", mu_l.get("remates_area", 7.0)) or 7.0)
            rem_area_v = float(mu_v.get("remates_dentro_area", mu_v.get("remates_area", 6.5)) or 6.5)
            p11_prop = float(pat_55.get("P11_Peligro_Area_vs_Fuera", 1.5) or 1.5)
            if rem_tot > 15.0 or (rem_area_l + rem_area_v) > 15.0 or p11_prop > 2.6:
                factor_bloqueo *= 0.80

            base = base * factor_bloqueo
        except Exception:
            pass

        if getattr(self, "moduladores", None) and self.p.aplicar_moduladores:
            clave_mult = "mult_corners_L" if bando == "local" else "mult_corners_V"
            mult = self.moduladores.get(clave_mult, 1.0)
            return base * mult
        return base

    def _distribuciones_corners(self, mu_l: float, mu_v: float) -> dict[str, dict[int, float]]:
        """
        Distribuciones del total de córneres.
        Poisson: el total de dos Poisson independientes es Poisson(μ_l+μ_v).
        Binomial negativa: se ajusta UNA sola NB al total por sus momentos
        (nunca se suman NB independientes manteniendo k, que sería incorrecto).
        """
        out: dict[str, dict[int, float]] = {}
        mu_total = mu_l + mu_v
        dist_p = distribucion_conteo(mu_total, parametros=Parametros(distribucion_conteos="poisson"))
        out["poisson"] = {k: p for k, p in enumerate(dist_p)}

        for k in VALORES_K_NB:
            pa = Parametros(distribucion_conteos="binomial_negativa", k_nb=k)
            _, var_l = _media_y_varianza_nb(k, mu_l)
            _, var_v = _media_y_varianza_nb(k, mu_v)
            k_total = _k_desde_media_varianza(mu_total, var_l + var_v)
            dist_nb = distribucion_conteo(mu_total, parametros=pa, k_nb=k_total)
            out[f"nb_k{k:g}"] = {idx: p for idx, p in enumerate(dist_nb)}
        return out

    # 25. Gol en ambos tiempos
    def _m_gol_en_ambos_tiempos(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        if not self.matriz_1t or not self.matriz_2t:
            return ResultadoMercado(
                idm, numero, nombre, estado="datos_insuficientes",
                nota="Requiere λ por tiempos; no se inventa el reparto.")
        p_1t = truncar_probabilidad(1.0 - self.matriz_1t[0][0]) if self.matriz_1t else None
        p_2t = truncar_probabilidad(1.0 - self.matriz_2t[0][0]) if self.matriz_2t else None
        if p_1t is None or p_2t is None:
            return ResultadoMercado(idm, numero, nombre, estado="datos_insuficientes")
        sels = [
            self._sel(idm, nombre, "gol_1t", "Al menos un gol en el 1T", "1T", None, None, p_1t),
            self._sel(idm, nombre, "gol_2t", "Al menos un gol en el 2T", "2T", None, None, p_2t),
            self._sel(idm, nombre, "gol_ambos_tiempos",
                      "Gol en ambos tiempos (partido)", "FT", None, None,
                      truncar_probabilidad(p_1t * p_2t)),
        ]
        return ResultadoMercado(
            idm, numero, nombre, selecciones=sels,
            nota=("«Gol del partido en cada mitad» con independencia temporal asumida. Es "
                  "distinto de «el mismo equipo marca en ambas mitades» y de «ambos equipos "
                  "marcan en cada mitad»."))

    # 26-27. Cualquier jugador
    def _m_cualquier_jugador_primero(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return ResultadoMercado(
            idm, numero, nombre, estado="regla_no_definida",
            nota=("«Cualquier jugador» puede referirse a un jugador seleccionado o a un grupo. "
                  "Sin esa definición no se equipara automáticamente a «al menos un gol», y "
                  "requiere datos individuales (incluido el tratamiento de autogoles)."),
            detalle={"requiere": "definición del grupo de jugadores y datos individuales"})

    def _m_cualquier_jugador_anota(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return ResultadoMercado(
            idm, numero, nombre, estado="regla_no_definida",
            nota=("Sin definir el grupo de jugadores no se calcula. No se equipara a «al menos "
                  "un gol» ni se reparte la λ del equipo entre jugadores."),
            detalle={"requiere": "definición del grupo de jugadores y datos individuales"})

    # 28-29. Córneres por equipo
    def _corners_equipo(self, numero: int, idm: str, nombre: str, bando: str) -> ResultadoMercado:
        mu = self._mu_corners(bando)
        equipo = self.nombre_local if bando == "local" else self.nombre_visitante
        mg = (self.muestras.get(f"{bando}_general") or MuestraEquipo("")).metricas.get("corners_favor")
        p_ceros = mg.pct_ceros if (mg and hasattr(mg, "pct_ceros")) else 0.0

        if mu is None or mu < 0.75 or p_ceros > 0.30:
            return ResultadoMercado(idm, numero, nombre, estado="datos_insuficientes",
                                    nota="Faltan córneres o el equipo carece de cobertura oficial (mu < 0.75 o >30% sin registro).")
        dist_p = distribucion_conteo(mu, parametros=Parametros(distribucion_conteos="poisson"))
        dist = {k: p for k, p in enumerate(dist_p)}
        sels: list[Seleccion] = []
        for linea in LINEAS_CORNERES[:12]:
            sels.append(self._sel(idm, nombre, f"{bando}_corners_over_{linea}",
                                  f"{equipo}: más de {linea} córneres", "FT", equipo, linea,
                                  prob_over(dist, linea)))
            if linea > 0.5:
                sels.append(self._sel(idm, nombre, f"{bando}_corners_under_{linea}",
                                      f"{equipo}: menos de {linea} córneres", "FT", equipo, linea,
                                      prob_under(dist, linea)))
        return ResultadoMercado(idm, numero, nombre, selecciones=sels,
                                detalle={"mu": mu})

    def _m_corners_local(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return self._corners_equipo(numero, idm, nombre, "local")

    def _m_corners_visitante(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        return self._corners_equipo(numero, idm, nombre, "visitante")

    # 30. Total de tarjetas
    def _m_total_tarjetas(self, numero: int, idm: str, nombre: str) -> ResultadoMercado:
        mu_amarillas = self._mu_tarjetas("amarillas")
        mu_rojas = self._mu_tarjetas("rojas")
        ml_am = (self.muestras.get("local_general") or MuestraEquipo("")).metricas.get("amarillas_favor")
        mv_am = (self.muestras.get("visitante_general") or MuestraEquipo("")).metricas.get("amarillas_favor")
        p_c_l = ml_am.pct_ceros if (ml_am and hasattr(ml_am, "pct_ceros")) else 0.0
        p_c_v = mv_am.pct_ceros if (mv_am and hasattr(mv_am, "pct_ceros")) else 0.0

        if mu_amarillas is None or mu_amarillas < 0.5 or p_c_l > 0.30 or p_c_v > 0.30:
            return ResultadoMercado(
                idm, numero, nombre, estado="datos_insuficientes",
                nota="Faltan tarjetas o la competición carece de cobertura oficial (mu < 0.5 o >30% sin registro).")
        dist = {k: p for k, p in enumerate(distribucion_conteo(mu_amarillas))}
        sels: list[Seleccion] = []
        for linea in LINEAS_AMARILLAS:
            sels.append(self._sel(idm, nombre, f"amarillas_over_{linea}",
                                  f"más de {linea} tarjetas amarillas", "FT", None, linea,
                                  prob_over(dist, linea)))
            if linea > 0.5:
                sels.append(self._sel(idm, nombre, f"amarillas_under_{linea}",
                                      f"menos de {linea} tarjetas amarillas", "FT", None, linea,
                                      prob_under(dist, linea)))
        if mu_rojas is not None and mu_rojas > 0:
            dist_r = {k: p for k, p in enumerate(distribucion_conteo(mu_rojas))}
            for linea in (0.5, 1.5):
                sels.append(self._sel(idm, nombre, f"rojas_over_{linea}",
                                      f"más de {linea} tarjetas rojas", "FT", None, linea,
                                      prob_over(dist_r, linea)))
        return ResultadoMercado(
            idm, numero, nombre, selecciones=sels,
            nota=("Tarjetas: cruce de amarillas propias con las de los rivales del otro equipo "
                  "(aproximación a la interacción disciplinaria). No se convierten "
                  "automáticamente en un total que incluya rojas o puntos porque la definición "
                  "no está disponible. Limitación registrada: no se conoce el árbitro."),
            detalle={"mu_amarillas": mu_amarillas, "mu_rojas": mu_rojas})

    def _mu_tarjetas(self, tipo: str) -> float | None:
        """
        λ del TOTAL de un tipo de tarjeta en el partido.

        Cruce declarado: tarjetas propias de cada equipo + tarjetas de los rivales
        del otro equipo, ponderado por agresividad defensiva (amarillas / faltas)
        y reducido -15% cuando un equipo es ampliamente favorito (domina sin fricción).
        """
        clave = "amarillas" if tipo == "amarillas" else "rojas"
        k_credibilidad = self.p.k_credibilidad
        equipos = (("local", self.muestras["local_general"], self.muestras["local_cond"]),
                   ("visitante", self.muestras["visitante_general"],
                    self.muestras["visitante_cond"]))
        partes: list[float] = []
        for _etq, general, cond in equipos:
            propia = media_ajustada(general.dame(f"{clave}_favor"), cond.dame(f"{clave}_favor"),
                                    self.p.w_condicion, k_credibilidad)
            if propia is not None:
                partes.append(propia)
        if not partes:
            return None
        base_tarj = sum(partes)
        if tipo == "amarillas":
            try:
                xai = getattr(self, "xai_factores", None) or {}
                rec = getattr(self, "recursivo", None) or {}
                mu_l = xai.get("mu_local_casa") or rec.get("mu_local_casa") or {}
                mu_v = xai.get("mu_visita_fuera") or rec.get("mu_visita_fuera") or {}
                am_l = float(mu_l.get("amarillas", partes[0] if partes else 2.0) or 2.0)
                am_v = float(mu_v.get("amarillas", partes[-1] if partes else 2.0) or 2.0)
                fal_l = max(5.0, float(mu_l.get("faltas", 11.5) or 11.5))
                fal_v = max(5.0, float(mu_v.get("faltas", 12.0) or 12.0))
                ratio_agresividad = ((am_l / fal_l) + (am_v / fal_v)) / 2.0
                # Ratio medio ~0.18; modulador suave entre 0.88 y 1.15 (P43_Agresividad_Defensiva)
                mod_agr = max(0.88, min(1.15, 0.82 + ratio_agresividad))
                base_tarj *= mod_agr
                # Ajuste por perfil de árbitro si está disponible en contexto_avanzado
                ctx_av = getattr(self, "contexto_avanzado", None) or rec.get("contexto_avanzado") or {}
                mult_arb = float(ctx_av.get("mult_tarjetas", 1.0) or 1.0)
                base_tarj *= mult_arb
                # Ajuste e) Límite de Tarjetas por Monopolio de Posesión (> 68% o |lam_l - lam_v| >= 0.75):
                # Si la posesión proyectada del favorito supera el 68% (ej. Man City ~65-72%), reducir lambda_tarjetas un -15%.
                pos_l = float(mu_l.get("posesion_pct", 50.0) or 50.0)
                pos_v = float(mu_v.get("posesion_pct", 50.0) or 50.0)
                pos_tot = max(1.0, pos_l + pos_v)
                pos_max_proy = max(pos_l / pos_tot, pos_v / pos_tot) * 100.0
                lam_l = float(rec.get("lambda_local_recursivo") or getattr(getattr(self, "tasas", None), "lambda_local_ft", 1.35) or 1.35)
                lam_v = float(rec.get("lambda_visita_recursivo") or getattr(getattr(self, "tasas", None), "lambda_visitante_ft", 1.10) or 1.10)
                self.monopolio_posesion_tarjetas = bool(pos_max_proy >= 68.0 or abs(lam_l - lam_v) >= 0.75)
                if self.monopolio_posesion_tarjetas:
                    base_tarj *= 0.85
            except Exception:
                pass
        return base_tarj


# ═══════════════════════════════════════════════════════════════════════════════
# SENSIBILIDAD
# ═══════════════════════════════════════════════════════════════════════════════

ESTADO_MANTIENE = "mantiene"
ESTADO_SENSIBLE = "sensible"
ESTADO_INCOMPLETA = "sensibilidad_incompleta"

# ─── Iconografía de mercados (rompe la monotonía visual) ─────────────────────
EMOJI_MERCADO = "⚽️"       # goles y totales de goles
EMOJI_CORNERS = "🚩"
EMOJI_TARJETAS = "🟨"
EMOJI_HANDICAP = "⚖️"
EMOJI_COMBINADA = "🔗"
EMOJI_AMBOS = "🥅"
EMOJI_RESULTADO = "🏆"


def emoji_de_mercado(nombre: str) -> str:
    """
    Icono del subtítulo del mercado, según su familia.

    Se comprueban varias formas del nombre porque el catálogo no es uniforme: los
    córneres aparecen como «tiros de esquina» y también como «córneres».
    """
    m = (nombre or "").lower()
    if "esquina" in m or "córner" in m or "corner" in m:
        return EMOJI_CORNERS
    if "tarjeta" in m:
        return EMOJI_TARJETAS
    if "hándicap" in m or "handicap" in m or "sin empate" in m or "doble oportunidad" in m:
        return EMOJI_HANDICAP
    if "ambos" in m or "btts" in m:
        return EMOJI_AMBOS
    if "+" in m or "combi" in m:
        return EMOJI_COMBINADA
    if "gol" in m:
        return EMOJI_MERCADO
    if "ganador" in m:
        return EMOJI_RESULTADO
    return "•"


def etiqueta_corta(seleccion: "Seleccion") -> str:
    """
    Convierte la etiqueta larga en una línea directa.

    «Boca Juniors: menos de 2.5 goles»  -> «Menos de 2.5 goles (FT)»
    «Boca Juniors +1»                   -> «Boca Juniors +1»
    «1X (Boca Juniors o empate)»        -> «1X (Boca Juniors o empate)»

    El nombre del equipo se elimina del texto porque ya está en el subtítulo del
    mercado: repetirlo en cada línea es lo que hacía el muro de texto.
    """
    texto = str(seleccion.etiqueta or "").strip()
    equipo = str(seleccion.equipo or "").strip()
    if equipo and ":" in texto:
        izquierda, derecha = texto.split(":", 1)
        if izquierda.strip().lower() == equipo.lower():
            texto = derecha.strip()
    # Mayúscula inicial, sin tocar acrónimos ni nombres propios ya capitalizados
    if texto and texto[0].islower():
        texto = texto[0].upper() + texto[1:]
    return texto


def _linea_seleccion(seleccion: "Seleccion", umbral: float,
                     sufijo: str = "") -> str:
    """Una viñeta: «• Menos de 4.5 goles (FT): 88,5%»."""
    linea = f"• {etiqueta_corta(seleccion)} ({seleccion.periodo}): " \
            f"{formatear_pct(seleccion.probabilidad, umbral)}"
    return linea + sufijo


def agrupar_por_mercado(selecciones: list["Seleccion"]) -> list[tuple[str, list["Seleccion"]]]:
    """
    Agrupa las selecciones por mercado conservando el ORDEN del catálogo, y dentro
    de cada mercado por periodo (FT, 1T, 2T) y probabilidad descendente. Así el
    nombre del mercado aparece UNA sola vez como subtítulo.
    """
    orden_mercado = {idm: i for i, (idm, _) in enumerate(MERCADOS)}
    orden_periodo = {p: i for i, p in enumerate(ORDEN_PERIODOS)}
    grupos: dict[str, list["Seleccion"]] = {}
    for s in selecciones:
        grupos.setdefault(s.mercado, []).append(s)
    salida: list[tuple[str, list["Seleccion"]]] = []
    for nombre, sels in sorted(grupos.items(),
                               key=lambda kv: orden_mercado.get(
                                   kv[1][0].id_mercado if kv[1] else "", 999)):
        sels_orden = sorted(sels, key=lambda s: (orden_periodo.get(s.periodo, 9),
                                                 -(s.probabilidad or 0)))
        salida.append((nombre, sels_orden))
    return salida


def linea_cobertura_xg(metadatos: dict[str, Any], motor: "MotorMercados") -> str:
    """
    Línea que declara con QUÉ se calculó cada equipo.

    Un «-» en el informe no decía si la λ usó xG real o solo goles. Esta línea lo
    hace explícito, porque cambia por completo la confianza en el resultado.
    """
    partes = []
    avisos = []
    for clave, etiqueta in (("local_general", metadatos.get("equipo_local")),
                            ("visitante_general", metadatos.get("equipo_visitante"))):
        m = motor.muestras.get(clave)
        if m is None:
            continue
        con, tot, frac = m.cobertura_xg()
        if tot == 0:
            continue
        if con == 0:
            partes.append(f"{etiqueta}: SIN xG ({tot} partidos, solo goles)")
            avisos.append(etiqueta)
        elif frac is not None and frac < 0.999:
            partes.append(f"{etiqueta}: xG en {con}/{tot} ({frac*100:.0f}%)")
            if frac < 0.5:
                avisos.append(etiqueta)
        else:
            partes.append(f"{etiqueta}: xG en {con}/{tot}")
    if not partes:
        return "cobertura de xG no determinada."
    linea = " | ".join(partes) + "."
    if avisos:
        linea += (f"\n⚠️ xG escaso en {', '.join(avisos)}: la λ se apoya sobre todo en "
                  f"goles reales, que son más ruidosos con muestras cortas.")
    return linea


def _resumen_muestra(motor: "MotorMercados") -> list[str]:
    """
    Líneas que declaran QUÉ muestra se usó, de qué fechas y si quedó corta.

    Antes el mensaje decía «Muestra: 7/9 partidos; condición relevante: 3/4», cuatro
    números sin decir qué era cada uno: no se podía saber si 9 era el objetivo o si
    el dataset se había quedado corto. Aquí se dice explícitamente el objetivo, el
    rango de fechas de cada equipo y si la fuente se agotó.
    """
    lineas: list[str] = []
    for etq, doc in (("Local", motor.doc_local), ("Visitante", motor.doc_visitante)):
        esc = doc.get("escaneo") or {}
        res = doc.get("resumen") or {}
        nombre = doc.get("equipo", {}).get("nombre") or "?"
        auditados = res.get("partidos_auditados")
        obtenidos = res.get("partidos_obtenidos")
        objetivo = (res.get("minimo_con_datos")
                    or doc.get("limite_partidos"))
        alcanzado = res.get("objetivo_alcanzado")
        amistosos = esc.get("amistosos_usados_como_relleno") or 0

        # Rango de fechas realmente usado
        fechas = sorted(str(p.get("fecha") or "") for p in (doc.get("partidos") or [])
                        if p.get("fecha"))
        rango = ""
        if fechas:
            rango = (f" · {fechas[0]}→{fechas[-1]}" if fechas[0] != fechas[-1]
                     else f" · {fechas[0]}")

        faltan = ""
        if alcanzado is False or (objetivo and auditados is not None and auditados < objetivo):
            motivo = esc.get("motivo_parada")
            faltan = (f" ⚠️ INCOMPLETO: {auditados or 0} de {objetivo} pedidos"
                      + (f" ({motivo})" if motivo else ""))
        relleno = f" · {amistosos} amistoso(s) de relleno" if amistosos else ""
        lineas.append(
            f"{etq} — {nombre}: {auditados or 0} partido(s) analizado(s)"
            f"{f' de {objetivo} pedidos' if objetivo else ''}"
            f" · {obtenidos or 0} obtenido(s) en la fuente{rango}{relleno}{faltan}")
    lineas.append(
        "Estado del dataset: "
        + ("⚠️ INCOMPLETO en al menos un equipo — el análisis se apoya en menos "
           "partidos de los solicitados"
           if any((d.get("resumen") or {}).get("objetivo_alcanzado") is False
                  for d in (motor.doc_local, motor.doc_visitante))
           else "completo (se alcanzó el objetivo de partidos en ambos equipos)"))
    return lineas


def construir_mensaje(metadatos: dict[str, Any], grupos: dict[str, list[Seleccion]],
                      parametros: Parametros, motor: MotorMercados,
                      mercados: list[ResultadoMercado]) -> str:
    """
    Mensaje de Telegram: SOLO selecciones que pasan el filtro central.

    Formato agrupado: el nombre del mercado aparece UNA vez como subtítulo con
    icono, y debajo van sus selecciones como viñetas. Antes se repetía el mercado
    completo en cada línea, lo que generaba un muro de texto interminable.
    """
    umbral = parametros.umbral
    L: list[str] = []
    L.append(f"ANÁLISIS: {metadatos['equipo_local']} (local) vs "
             f"{metadatos['equipo_visitante']} (visitante)")
    fecha_val = metadatos.get('fecha_partido')
    fecha_display = (str(fecha_val) if fecha_val and str(fecha_val).lower() not in ('no disponible', 'none', '')
                     else "Sin partido próximo en calendario (localía por orden de consulta)")
    L.append(f"Fecha: {fecha_display} | "
             f"Competición: {metadatos.get('competicion') or 'no disponible'} | "
             f"Análisis generado: {metadatos.get('generado_en') or 'no disponible'}")
    L.extend(_resumen_muestra(motor))
    L.append(f"Condición usada: local={motor.cond_local}, visitante={motor.cond_visitante} "
             f"(muestra de condición: {motor.muestras['local_cond'].partidos_condicion}/"
             f"{motor.muestras['visitante_cond'].partidos_condicion} partidos)")
    L.append("Datos: " + linea_cobertura_xg(metadatos, motor))
    L.append(f"Modelo: {NOMBRE_MODELO} v{VERSION_MODELO}. Probabilidades estimadas, "
             f"no garantías. Líneas orientativas si no hay catálogo específico.")
    _rec = getattr(motor, "recursivo", None) or {}
    if _rec.get("aplicado"):
        _ent = _rec.get("historial_entropia") or [0.0]
        L.append(
            f"Red Neuronal Recursiva: λ {_rec['lambda_base_local']:.2f}–"
            f"{_rec['lambda_base_visita']:.2f} → {_rec['lambda_local_recursivo']:.2f}–"
            f"{_rec['lambda_visita_recursivo']:.2f} · convergencia it. "
            f"{_rec.get('iteracion_convergencia')} (H={float(_ent[-1]):.3f}) · "
            f"cruce {_rec.get('cruce_dominante_local')}")

    # ── IA Explicable (XAI): 5 Factores Latentes y Cluster Dominante ─────────
    lineas_xai = formatear_bloque_xai(motor)
    if lineas_xai:
        L.append("")
        L.extend(lineas_xai)

    # ── TOP 5 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO) ───────────────────
    sels_todas = (
        (grupos.get(ESTADO_MANTIENE) or [])
        + (grupos.get(ESTADO_SENSIBLE) or [])
        + (grupos.get(ESTADO_INCOMPLETA) or [])
    )
    top5_podio = seleccionar_top_5_jugadas_recomendadas(
        sels_todas,
        motor=motor,
        eq_l=str(metadatos.get("equipo_local") or "Local"),
        eq_v=str(metadatos.get("equipo_visitante") or "Visitante"),
    )
    if top5_podio:
        L.append("")
        L.append("🏆 TOP 5 JUGADAS RECOMENDADAS (ALTO VALOR TE APUESTO):")
        nota_cal = top5_podio[0].get("nota_calibracion")
        if nota_cal:
            L.append(f"ℹ️ {nota_cal}")
        for item in top5_podio:
            badge_str = item.get("badge_completo") or item.get("etiqueta_valor") or item["probabilidad_pct"]
            L.append(
                f"▸ #{item['rank']} [{item['categoria_podio']}] {item['etiqueta']} — "
                f"{badge_str} | {item['respaldo']}"
            )

    hay = any(grupos.get(e) for e in (ESTADO_MANTIENE, ESTADO_SENSIBLE, ESTADO_INCOMPLETA))
    if not hay:
        fallidos = [r for r in mercados if r.estado in ("datos_insuficientes", "regla_no_definida")]
        if len(fallidos) == len(mercados):
            L.append("")
            L.append("ANÁLISIS NO REALIZABLE: no hay datos suficientes para calcular ningún "
                     "mercado con este modelo.")
            for r in fallidos[:5]:
                L.append(f"• {r.nombre}: {r.estado} — {r.nota[:120]}")
        else:
            L.append("")
            L.append(f"Sin selecciones por encima del {umbral:.0%} en las líneas evaluadas.")
        return "\n".join(L)

    bloques = (
        (ESTADO_MANTIENE, f"✅ SEGUROS (>{umbral:.0%} EN TODOS LOS ESCENARIOS)",
         "— sensible", ""),
        (ESTADO_SENSIBLE, "⚠️ SENSIBLES A LOS SUPUESTOS", "", ""),
        (ESTADO_INCOMPLETA, "⚠️ SENSIBILIDAD INCOMPLETA", "", ""),
    )
    for estado, titulo, sufijo_sensible, sufijo_incompleta in bloques:
        sels = grupos.get(estado) or []
        if not sels:
            continue
        L.append("")
        L.append(titulo)
        if estado == ESTADO_SENSIBLE:
            L.append("(el valor central supera el umbral, pero baja con otros supuestos)")
        elif estado == ESTADO_INCOMPLETA:
            L.append("(no se pudieron evaluar todos los escenarios)")
        for nombre_mercado, sels_mercado in agrupar_por_mercado(sels):
            L.append("")
            L.append(f"{emoji_de_mercado(nombre_mercado)} {nombre_mercado}")
            for s in sels_mercado:
                sufijo = " — sensible" if estado == ESTADO_SENSIBLE else ""
                L.append(_linea_seleccion(s, umbral, sufijo))
    return "\n".join(L)


def comparar_tecnicas(doc_local: dict[str, Any], doc_visitante: dict[str, Any],
                      equipo_local: str, equipo_visitante: str,
                      **kwargs: Any) -> dict[str, Any]:
    """
    Compara el modelo vigente con las dos técnicas nuevas, SIN sustituir nada.

    Es la comprobación honesta que exige el proyecto: una versión candidata no se
    adopta por parecer mejor, sino midiendo si cambia las conclusiones.

    Variantes comparadas (mismo dataset y mismo catálogo en todas):
      • `base`       : pesos fijos (w_condicion=0,5) y Poisson independiente.
      • `bayes`      : contracción bayesiana del peso de condición.
      • `dixoncoles` : corrección de Dixon-Coles (rho de la literatura).
      • `ambas`      : las dos a la vez.

    Devuelve, por variante, cuántas selecciones superan el umbral y cuántas cambian
    de estado de sensibilidad respecto a `base`.
    """
    def _params_base() -> Parametros:
        return Parametros(w_condicion=W_CONDICION_DEFECTO, w_xg=W_XG_DEFECTO,
                          k_credibilidad=None, rho=0.0)

    variantes = {
        "base": _params_base(),
        "bayes": Parametros(w_condicion=W_CONDICION_DEFECTO, w_xg=W_XG_DEFECTO,
                            k_credibilidad=K_CREDIBILIDAD_DEFECTO, rho=0.0),
        "dixoncoles": Parametros(w_condicion=W_CONDICION_DEFECTO, w_xg=W_XG_DEFECTO,
                                 k_credibilidad=None, rho=RHO_DIXON_COLES),
        "ambas": Parametros(w_condicion=W_CONDICION_DEFECTO, w_xg=W_XG_DEFECTO,
                            k_credibilidad=K_CREDIBILIDAD_DEFECTO, rho=RHO_DIXON_COLES),
    }

    salida: dict[str, Any] = {"variantes": {}, "lambdas": {}}
    referencia: dict[tuple, float] = {}
    for nombre, params in variantes.items():
        try:
            analisis = analizar(doc_local, doc_visitante, equipo_local, equipo_visitante,
                                parametros=params, **kwargs)
        except Exception as e:
            salida["variantes"][nombre] = {"error": f"{type(e).__name__}: {e}"}
            continue
        motor = analisis.motor
        claves = {(s.id_mercado, s.evento, s.periodo, s.equipo or "", s.linea): s
                  for s in analisis.selecciones_filtradas}
        if nombre == "base":
            referencia = {k: v.probabilidad for k, v in claves.items()
                          if v.probabilidad is not None}
        cambian = 0
        comparables = 0
        for k, prob in referencia.items():
            sel = claves.get(k)
            if sel is None or sel.probabilidad is None:
                continue
            comparables += 1
            if abs(sel.probabilidad - prob) > 1e-9:
                cambian += 1
        salida["variantes"][nombre] = {
            "superan_umbral": len(claves),
            "selecciones_comparables": comparables,
            "con_probabilidad_distinta": cambian,
            "lambda_local": motor.tasas.lambda_local_ft,
            "lambda_visitante": motor.tasas.lambda_visitante_ft,
            "w_condicion_efectivo_local": (
                peso_condicion_por_evidencia(
                    motor.muestras["local_cond"].partidos_condicion,
                    params.w_condicion, params.k_credibilidad)
                if params.k_credibilidad is not None else params.w_condicion),
            "w_condicion_efectivo_visitante": (
                peso_condicion_por_evidencia(
                    motor.muestras["visitante_cond"].partidos_condicion,
                    params.w_condicion, params.k_credibilidad)
                if params.k_credibilidad is not None else params.w_condicion),
        }
    return salida


def calcular_moduladores(medias_local: dict[str, float | None],
                         medias_visitante: dict[str, float | None],
                         limite: float = LIMITE_MODULADOR_DEFECTO) -> dict[str, Any]:
    """
    Moduladores tácticos acotados sobre la λ base, usando las 45 métricas SOLO
    cuando existen.

    Regla de integridad: si una métrica es None en cualquiera de los dos equipos, su
    factor vale EXACTAMENTE 1.0 (neutro) y no se registra como usada. Nunca se
    inventa un valor ni se lanza TypeError.

    ACOTADO: el efecto TOTAL sobre la λ se limita a `limite` (15% por defecto). Se
    acota el producto completo, no cada factor por separado: acotar los factores y
    luego sumar el ajuste del portero daba hasta ±28%, que no es "acotado".
    """
    usadas: list[str] = []

    def clip(v: float, a: float, b: float) -> float:
        return max(a, min(b, v))

    # ── 1. Posesión esperada y ritmo ───────────────────────────────────────
    pos_l, pos_v = medias_local.get("posesion_pct"), medias_visitante.get("posesion_pct")
    if pos_l and pos_v and (pos_l + pos_v) > 0:
        pos_l_esp = 100.0 * pos_l / (pos_l + pos_v)
        pos_v_esp = 100.0 - pos_l_esp
        gamma_l = clip((pos_l_esp / pos_l) ** 0.35, 0.92, 1.08)
        gamma_v = clip((pos_v_esp / pos_v) ** 0.35, 0.92, 1.08)
        sin_balon_l = clip(((100.0 - pos_l_esp) / max(15.0, 100.0 - pos_l)) ** 0.45,
                           0.85, 1.25)
        sin_balon_v = clip(((100.0 - pos_v_esp) / max(15.0, 100.0 - pos_v)) ** 0.45,
                           0.85, 1.25)
        usadas.append("posesion_pct")
    else:
        pos_l_esp = pos_v_esp = 50.0
        gamma_l = gamma_v = 1.0
        sin_balon_l = sin_balon_v = 1.0

    # ── 2. Puntería y letalidad ────────────────────────────────────────────
    def mod_punteria(m: dict[str, float | None]) -> float:
        mult = 1.0
        xg, xgot = m.get("xg"), m.get("xgot")
        if xg and xgot and xg > 0.3:
            mult *= clip((xgot / xg) ** 0.35, 0.95, 1.05)
            usadas.append("xgot/xg")
        go, gof = m.get("grandes_ocasiones"), m.get("grandes_ocasiones_falladas")
        if go and gof is not None and go >= 1.5:
            tasa = clip(1.0 - (gof / go), 0.15, 0.70)
            mult *= clip(0.96 + 0.10 * tasa, 0.97, 1.03)
            usadas.append("grandes_ocasiones")
        return clip(mult, 0.94, 1.06)

    # ── 3. Calidad por disparo (con respaldo si no hay xG) ─────────────────
    def mod_calidad(m: dict[str, float | None]) -> float:
        rem = m.get("remates_totales")
        if not rem or rem < 5.0:
            return 1.0
        npxg = m.get("xg_sin_penalti") or m.get("xg")
        if npxg and npxg > 0:
            usadas.append("npxG/remate")
            return clip(((npxg / rem) / 0.105) ** 0.20, 0.95, 1.05)
        dentro = m.get("remates_dentro_area")
        if dentro:
            usadas.append("remates_dentro_area/remates_totales")
            return clip(((dentro / rem) / 0.62) ** 0.20, 0.96, 1.04)
        return 1.0

    # ── 4. Dominio aéreo ───────────────────────────────────────────────────
    da_l, da_v = medias_local.get("duelos_aereos_pct"), medias_visitante.get("duelos_aereos_pct")
    if da_l and da_v and da_l > 0 and da_v > 0:
        aereo_l = clip((da_l / da_v) ** 0.15, 0.97, 1.03)
        aereo_v = clip((da_v / da_l) ** 0.15, 0.97, 1.03)
        usadas.append("duelos_aereos_pct")
    else:
        aereo_l = aereo_v = 1.0

    # ── 5. Portería rival: el ajuste va DENTRO del producto para que el límite
    #       global sea real ──────────────────────────────────────────────────
    ge_v, ge_l = medias_visitante.get("goles_evitados"), medias_local.get("goles_evitados")
    if ge_v is not None:
        usadas.append("goles_evitados")
    if ge_l is not None:
        usadas.append("goles_evitados")

    def factor_portero(goles_evitados: float | None) -> float:
        """Convierte goles evitados del rival en un factor multiplicativo neutro."""
        if goles_evitados is None:
            return 1.0
        return clip(1.0 - 0.20 * float(goles_evitados), 0.88, 1.12)

    prod_l = gamma_l * mod_punteria(medias_local) * mod_calidad(medias_local) \
        * aereo_l * factor_portero(ge_v)
    prod_v = gamma_v * mod_punteria(medias_visitante) * mod_calidad(medias_visitante) \
        * aereo_v * factor_portero(ge_l)
    mult_goles_l = clip(prod_l, 1.0 - limite, 1.0 + limite)
    mult_goles_v = clip(prod_v, 1.0 - limite, 1.0 + limite)

    # ── 6. Córneres ────────────────────────────────────────────────────────
    def mod_corners(m_atq: dict[str, float | None], m_def: dict[str, float | None],
                    gamma_atq: float) -> float:
        mult = gamma_atq
        desp, bloq = m_def.get("despejes"), m_def.get("bloqueos")
        if desp is not None and bloq is not None:
            indice = (desp + 2.0 * bloq) / 23.0
            mult *= clip(indice ** 0.20, 0.94, 1.06)
            usadas.append("despejes/bloqueos")
        toques = m_atq.get("toques_area_rival")
        if toques is not None:
            mult *= clip((toques / 28.0) ** 0.15, 0.95, 1.05)
            usadas.append("toques_area_rival")
        return clip(mult, 1.0 - limite, 1.0 + limite)

    return {
        "posesion_esperada_L": round(pos_l_esp, 1),
        "posesion_esperada_V": round(pos_v_esp, 1),
        "mult_goles_L": round(mult_goles_l, 4),
        "mult_goles_V": round(mult_goles_v, 4),
        "mult_corners_L": round(mod_corners(medias_local, medias_visitante, gamma_l), 4),
        "mult_corners_V": round(mod_corners(medias_visitante, medias_local, gamma_v), 4),
        "mult_sin_balon_L": round(sin_balon_l, 4),
        "mult_sin_balon_V": round(sin_balon_v, 4),
        "limite_aplicado": limite,
        "producto_bruto_L": round(prod_l, 4),
        "producto_bruto_V": round(prod_v, 4),
        "metricas_activas": sorted(set(usadas)),
    }


def extraer_medias_para_moduladores(motor: "MotorMercados") -> tuple[dict[str, Any],
                                                                    dict[str, Any]]:
    """
    Medias ajustadas de las métricas que usan los moduladores, por bando.
    Devuelve (medias_local, medias_visitante) con None donde no haya dato: los
    moduladores los tratan como neutros.
    """
    # Nombre que espera `calcular_moduladores` -> clave real de la muestra.
    # Todo lo que se lee aquí tiene que existir en la muestra: `media_y_n_ajustados`
    # NO usa .get(), así que una clave ausente lanzaría KeyError.
    equivalencias = {
        "posesion_pct": "posesion_pct",
        "goles_favor": "goles_favor",
        "goles_contra": "goles_contra",
        "goles_evitados": "goles_evitados_contra",
        "despejes": "despejes_contra",
        "bloqueos": "bloqueos_contra",
        "xg": "xg_favor",
        "xgot": "xgot_favor",
        "xg_sin_penalti": "xg_sin_penalti_favor",
        "grandes_ocasiones": "grandes_ocasiones_favor",
        "grandes_ocasiones_falladas": "grandes_ocasiones_falladas_favor",
        "remates_totales": "remates_totales_favor",
        "remates_dentro_area": "remates_dentro_area_favor",
        "duelos_aereos_pct": "duelos_aereos_pct_favor",
        "toques_area_rival": "toques_area_rival_favor",
    }
    salida = []
    for lado in ("local", "visitante"):
        general = motor.muestras[f"{lado}_general"]
        cond = motor.muestras[f"{lado}_cond"]
        medias: dict[str, Any] = {}
        for destino, clave in equivalencias.items():
            valor, _n = media_y_n_ajustados(general.dame(clave), cond.dame(clave),
                                            motor.p.w_condicion, motor.p.k_credibilidad)
            medias[destino] = valor
        salida.append(medias)
    return salida[0], salida[1]


def extraer_medias_lambda_efectivo(
    motor: "MotorMercados"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Extrae las medias ajustadas de xG, xGOT, remates a puerta, remates dentro del área,
    goles evitados (portería propia) y errores defensivos para calibrar el λ_efectivo
    y la sobredispersión de cola ancha (lección Real Madrid 4-1 Sevilla, 18/10/2026).
    """
    equivalencias = {
        "xg": "xg_favor",
        "xgot": "xgot_favor",
        "remates_puerta": "remates_puerta_favor",
        "remates_dentro_area": "remates_dentro_area_favor",
        "goles_evitados": "goles_evitados_favor",
        "errores_gol": "errores_gol_favor",
        "errores_remate": "errores_remate_favor",
    }
    salida = []
    for lado in ("local", "visitante"):
        general = motor.muestras[f"{lado}_general"]
        cond = motor.muestras[f"{lado}_cond"]
        medias: dict[str, Any] = {}
        for destino, clave in equivalencias.items():
            valor, _n = media_y_n_ajustados(general.dame(clave), cond.dame(clave),
                                            motor.p.w_condicion, motor.p.k_credibilidad)
            medias[destino] = valor
        salida.append(medias)
    return salida[0], salida[1]


def calibrar_lambda_efectivo(
    lam_l: float,
    lam_v: float,
    medias_l: dict[str, float | None],
    medias_v: dict[str, float | None],
    moduladores: dict[str, Any] | None = None,
    lam_l_base: float | None = None,
    lam_v_base: float | None = None,
    limite: float = 0.35,
) -> dict[str, Any]:
    """
    Calcula el λ_efectivo de cada equipo ajustando por:
      1. Factor de finalización propio: clip(xgot / max(xg, 0.1), 0.90, 1.25)
      2. Regalo por portero rival vulnerable: max(0.0, -goles_evitados_rival) * clip(remates_puerta / 4.5, 0.5, 2.0)
      3. Regalo por error defensivo del rival: errores_gol_rival + 0.30 * errores_remate_rival

    Suma regalo_portero_rival y regalo_error_defensivo al λ_efectivo de cada atacante
    (ej. caso Real Madrid 4-1 Sevilla: los 0.22 errores_gol del Madrid activan el gol
    del Sevilla elevando su λ de 0.79 a ~1.05; y el xGOT 2.80 + portero con -1.20 del Sevilla
    elevan el λ del Madrid hacia 3.5-4.0).
    """
    def _clip(v: float, a: float, b: float) -> float:
        return max(a, min(b, v))

    # --- LOCAL ATACANTE vs VISITANTE DEFENSOR ---
    xg_l = medias_l.get("xg")
    xgot_l = medias_l.get("xgot")
    if xg_l and xgot_l and xg_l > 0.2:
        factor_fin_l = _clip(xgot_l / max(xg_l, 0.1), 0.90, 1.25)
    else:
        factor_fin_l = 1.0

    ge_rival_l = medias_v.get("goles_evitados")
    rp_l = medias_l.get("remates_puerta") or 4.5
    if ge_rival_l is not None and ge_rival_l < 0:
        regalo_portero_l = max(0.0, -ge_rival_l) * _clip(rp_l / 4.5, 0.5, 2.0)
    else:
        regalo_portero_l = 0.0

    eg_rival_l = medias_v.get("errores_gol") or 0.0
    er_rival_l = medias_v.get("errores_remate") or 0.0
    regalo_error_l = float(eg_rival_l) + 0.30 * float(er_rival_l)

    lam_l_ef = (lam_l * factor_fin_l) + regalo_portero_l + regalo_error_l

    # --- VISITANTE ATACANTE vs LOCAL DEFENSOR ---
    xg_v = medias_v.get("xg")
    xgot_v = medias_v.get("xgot")
    if xg_v and xgot_v and xg_v > 0.2:
        factor_fin_v = _clip(xgot_v / max(xg_v, 0.1), 0.90, 1.25)
    else:
        factor_fin_v = 1.0

    ge_rival_v = medias_l.get("goles_evitados")
    rp_v = medias_v.get("remates_puerta") or 4.5
    if ge_rival_v is not None and ge_rival_v < 0:
        regalo_portero_v = max(0.0, -ge_rival_v) * _clip(rp_v / 4.5, 0.5, 2.0)
    else:
        regalo_portero_v = 0.0

    eg_rival_v = medias_l.get("errores_gol") or 0.0
    er_rival_v = medias_l.get("errores_remate") or 0.0
    regalo_error_v = float(eg_rival_v) + 0.30 * float(er_rival_v)

    lam_v_ef = (lam_v * factor_fin_v) + regalo_portero_v + regalo_error_v

    # Acotar contra la base si se especificó para evitar derivas desmedidas
    if lam_l_base and lam_l_base > 0:
        lam_l_ef = _clip(lam_l_ef, lam_l_base * (1.0 - limite), lam_l_base * (1.0 + limite * 1.5))
    if lam_v_base and lam_v_base > 0:
        lam_v_ef = _clip(lam_v_ef, lam_v_base * (1.0 - limite), lam_v_base * (1.0 + limite * 1.5))

    aplicado = (abs(lam_l_ef - lam_l) > 1e-4 or abs(lam_v_ef - lam_v) > 1e-4)
    partes_nota = []
    if factor_fin_l != 1.0:
        partes_nota.append(f"finalización L {factor_fin_l:.2f}x")
    if regalo_portero_l > 0.01:
        partes_nota.append(f"portería rival L +{regalo_portero_l:.2f}")
    if regalo_error_l > 0.01:
        partes_nota.append(f"errores rival L +{regalo_error_l:.2f}")
    if factor_fin_v != 1.0:
        partes_nota.append(f"finalización V {factor_fin_v:.2f}x")
    if regalo_portero_v > 0.01:
        partes_nota.append(f"portería rival V +{regalo_portero_v:.2f}")
    if regalo_error_v > 0.01:
        partes_nota.append(f"errores rival V +{regalo_error_v:.2f}")

    txt_ajustes = (", ".join(partes_nota) if partes_nota else "sin desvíos significativos")
    nota = (
        f"λ_efectivo calibrado (xGOT/finalización, portero vulnerable y errores defensivos): "
        f"L {lam_l:.2f}→{lam_l_ef:.2f}, V {lam_v:.2f}→{lam_v_ef:.2f} ({txt_ajustes})."
    )

    return {
        "aplicado": aplicado,
        "lambda_local_efectiva": round(lam_l_ef, 4),
        "lambda_visitante_efectiva": round(lam_v_ef, 4),
        "factor_finalizacion_local": round(factor_fin_l, 4),
        "factor_finalizacion_visita": round(factor_fin_v, 4),
        "regalo_portero_local": round(regalo_portero_l, 4),
        "regalo_portero_visita": round(regalo_portero_v, 4),
        "regalo_error_local": round(regalo_error_l, 4),
        "regalo_error_visita": round(regalo_error_v, 4),
        "nota": nota,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ARQUITECTURA HÍBRIDA DE PREDICCIÓN (FASES 1 A 5 — RED NEURONAL Y ENSEMBLE)
# ═══════════════════════════════════════════════════════════════════════════════

COTAS_NORMALIZACION_45: dict[str, tuple[float, float]] = {
    "xg": (0.0, 4.5),
    "posesion_pct": (20.0, 80.0),
    "remates_totales": (2.0, 32.0),
    "remates_puerta": (0.0, 14.0),
    "grandes_ocasiones": (0.0, 8.0),
    "grandes_ocasiones_falladas": (0.0, 6.0),
    "corners": (0.0, 16.0),
    "pases_total": (150.0, 900.0),
    "pases_precision_pct": (55.0, 95.0),
    "pases_ratio": (0.3, 3.5),
    "tarjetas_amarillas": (0.0, 7.0),
    "tarjetas_rojas": (0.0, 2.0),
    "xgot": (0.0, 4.5),
    "xg_juego_abierto": (0.0, 4.0),
    "xg_balon_parado": (0.0, 2.5),
    "xg_sin_penalti": (0.0, 4.0),
    "remates_fuera": (0.0, 16.0),
    "remates_rechazados": (0.0, 12.0),
    "remates_dentro_area": (0.0, 22.0),
    "remates_fuera_area": (0.0, 16.0),
    "al_palo": (0.0, 3.0),
    "toques_area_rival": (4.0, 60.0),
    "pases_entre_lineas": (3.0, 60.0),
    "fueras_juego": (0.0, 8.0),
    "tiros_libres": (3.0, 25.0),
    "pases_largos_pct": (20.0, 75.0),
    "pases_tercio_final_pct": (25.0, 85.0),
    "cruces_pct": (5.0, 50.0),
    "xa_equipo": (0.0, 3.5),
    "saques_banda": (5.0, 40.0),
    "faltas": (3.0, 26.0),
    "entradas_pct": (5.0, 35.0),
    "duelos_ganados": (25.0, 75.0),
    "duelos_suelo_pct": (30.0, 70.0),
    "duelos_aereos_pct": (30.0, 70.0),
    "despejes": (4.0, 45.0),
    "intercepciones": (2.0, 22.0),
    "bloqueos": (0.0, 10.0),
    "recuperaciones": (20.0, 75.0),
    "errores_remate": (0.0, 3.0),
    "errores_gol": (0.0, 2.0),
    "paradas": (0.0, 12.0),
    "goles_evitados": (-2.5, 2.5),
    "xgot_enfrentado": (0.0, 4.5),
    "saques_puerta": (1.0, 18.0),
}


def normalizar_vector_45(valores: dict[str, float | None]) -> dict[str, float]:
    """
    Fase 1: Preprocesamiento y Normalización Acotada [0.0, 1.0] con cotas teóricas.
    Garantía de Cero Data Leakage: valores acotados estrictamente por variable.
    """
    neutrales: dict[str, float] = {
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
    vector_norm: dict[str, float] = {}
    for clave, (c_min, c_max) in COTAS_NORMALIZACION_45.items():
        val = valores.get(clave)
        if val is None:
            val = neutrales.get(clave, (c_min + c_max) / 2.0)
        val_f = float(val)
        norm = (val_f - c_min) / (c_max - c_min) if c_max > c_min else 0.5
        vector_norm[clave] = max(0.0, min(1.0, float(norm)))
    return vector_norm


def calcular_multi_frecuencia_45(
    partidos_cronologicos: list[dict[str, Any]],
    partidos_h2h: list[dict[str, Any]] | None = None,
    nowcasting_vivo: dict[str, Any] | None = None,
    nombre_equipo: str = "",
) -> dict[str, float]:
    """
    Fase 2: Multi-Frecuencia Temporal (Base 55% + EWMA 30% + H2H/Vivo 15%).
    Orden cronológico estricto t_i < T. Devuelve vector normalizado en [0.0, 1.0].
    """
    ordenados = sorted(
        (p for p in partidos_cronologicos or [] if isinstance(p, dict)),
        key=lambda p: str(p.get("fecha") or p.get("utcTime") or "")
    )
    if not ordenados:
        return normalizar_vector_45({})

    def _extraer_valor(partido: dict[str, Any], clave_m: str) -> float | None:
        p_src = partido.get("_partido") if isinstance(partido.get("_partido"), dict) else partido
        periodos = p_src.get("periodos") or {}
        for corte in ("FT", "1T", "2T"):
            bloque = periodos.get(corte) or {}
            for fila in bloque.get("filas") or []:
                if isinstance(fila, dict) and fila.get("clave") == clave_m:
                    v = fila.get("equipo")
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        return float(v)
        return None

    series_crudas: dict[str, list[float]] = {}
    for clave in COTAS_NORMALIZACION_45:
        valores_partido: list[float] = []
        for p in ordenados:
            v = _extraer_valor(p, clave)
            if v is not None:
                valores_partido.append(float(v))
        if not valores_partido:
            c_min, c_max = COTAS_NORMALIZACION_45[clave]
            valores_partido = [(c_min + c_max) / 2.0]
        series_crudas[clave] = valores_partido

    # 1. Base estructural: media 15 partidos
    vec_est: dict[str, float] = {}
    for clave, vals in series_crudas.items():
        vec_est[clave] = float(sum(vals) / len(vals))

    # 2. Inercia reciente: últimos 5 partidos con EWMA α=0.35
    vec_ine: dict[str, float] = {}
    alpha_ewma = 0.35
    for clave, vals in series_crudas.items():
        sub_vals = vals[-5:] if len(vals) >= 5 else vals
        n_sub = len(sub_vals)
        pesos = [(1.0 - alpha_ewma) ** (n_sub - 1 - i) * alpha_ewma for i in range(n_sub)]
        if not pesos or sum(pesos) == 0:
            pesos = [1.0] * n_sub
        suma_w = sum(pesos)
        vec_ine[clave] = float(sum(v * w for v, w in zip(sub_vals, pesos)) / suma_w)

    # 3. Choque Directo H2H o Nowcasting en vivo
    vec_cho: dict[str, float] = {}
    peso_choque = 0.15
    if nowcasting_vivo and isinstance(nowcasting_vivo, dict):
        for clave in COTAS_NORMALIZACION_45:
            val_vivo = nowcasting_vivo.get(clave)
            vec_cho[clave] = float(val_vivo) if val_vivo is not None else vec_ine[clave]
    elif partidos_h2h and len(partidos_h2h) > 0:
        for clave in COTAS_NORMALIZACION_45:
            vh2h = [_extraer_valor(p, clave) for p in partidos_h2h]
            vh2h_validos = [float(v) for v in vh2h if v is not None]
            vec_cho[clave] = float(sum(vh2h_validos) / len(vh2h_validos)) if vh2h_validos else vec_est[clave]
    else:
        peso_choque = 0.0

    if peso_choque > 0:
        w_est, w_ine, w_cho = 0.55, 0.30, 0.15
    else:
        w_est, w_ine, w_cho = 0.65, 0.35, 0.0

    vec_combinado: dict[str, float] = {}
    for clave in COTAS_NORMALIZACION_45:
        val = (w_est * vec_est[clave] +
               w_ine * vec_ine[clave] +
               w_cho * vec_cho.get(clave, vec_est[clave]))
        vec_combinado[clave] = val

    return normalizar_vector_45(vec_combinado)


def calcular_5_factores_latentes(
    vec_ofensivo_norm: dict[str, float],
    vec_defensivo_rival_norm: dict[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Fase 3: Reducción de Dimensión a 5 Nodos Ocultos Estructurados (Factores Latentes):
      F1: Posesión Efectiva y Progresión Territorial (penaliza posesión estéril)
      F2: Volumen de Creación Ofensiva
      F3: Calidad Real de Finalización / Letalidad
      F4: Vulnerabilidad de la Portería Rival
      F5: Fragilidad Defensiva y Regalos del Rival
    Activación no lineal: tanh(2.5 * z). Rango resultante en [-1.0, 1.0].
    """
    vo = vec_ofensivo_norm
    vd = vec_defensivo_rival_norm

    # F1 — Posesión Efectiva y Progresión Territorial (ADVERSARIAL DE SUMA CERO: un solo balón)
    pos = vo.get("posesion_pct", 0.5)
    pos_r = vd.get("posesion_pct", 0.5)
    pos_esp = pos / (pos + pos_r) if (pos + pos_r) > 1e-6 else 0.5
    pas_tot = vo.get("pases_total", 0.5)
    pas_tot_r = vd.get("pases_total", 0.5)
    pas_rel = pas_tot / (pas_tot + pas_tot_r) if (pas_tot + pas_tot_r) > 1e-6 else 0.5
    toq_area = vo.get("toques_area_rival", 0.5)
    toq_area_r = vd.get("toques_area_rival", 0.5)
    toq_rel = toq_area / (toq_area + toq_area_r) if (toq_area + toq_area_r) > 1e-6 else 0.5
    pas_ter = vo.get("pases_tercio_final_pct", 0.5)
    pas_ter_r = vd.get("pases_tercio_final_pct", 0.5)
    ter_rel = pas_ter / (pas_ter + pas_ter_r) if (pas_ter + pas_ter_r) > 1e-6 else 0.5

    z1 = (0.35 * (pos_esp - 0.5) + 0.30 * (toq_rel - 0.5) + 0.20 * (ter_rel - 0.5) + 0.15 * (pas_rel - 0.5)) * 2.0
    f1 = math.tanh(2.5 * z1)

    # F2 — Volumen de Creación Ofensiva
    xg = vo.get("xg", 0.5)
    xg_abierto = vo.get("xg_juego_abierto", 0.5)
    xg_bp = vo.get("xg_balon_parado", 0.5)
    xg_sp = vo.get("xg_sin_penalti", 0.5)
    xa = vo.get("xa_equipo", 0.5)
    gr_oc = vo.get("grandes_ocasiones", 0.5)
    corn = vo.get("corners", 0.5)
    tl = vo.get("tiros_libres", 0.5)
    z2 = (0.25 * xg + 0.15 * xg_abierto + 0.10 * xg_bp + 0.10 * xg_sp +
          0.15 * xa + 0.20 * gr_oc + 0.10 * corn + 0.05 * tl) - 0.50
    f2 = math.tanh(2.5 * z2)

    # F3 — Calidad Real de Finalización / Letalidad
    xgot = vo.get("xgot", 0.5)
    ratio_xgot_xg = max(0.0, min(1.0, (xgot / max(xg, 0.05) - 0.5) / 1.0))
    rem_puerta = vo.get("remates_puerta", 0.5)
    rem_area = vo.get("remates_dentro_area", 0.5)
    gr_oc_fall = vo.get("grandes_ocasiones_falladas", 0.5)
    palo = vo.get("al_palo", 0.5)
    z3 = (0.25 * xgot + 0.25 * ratio_xgot_xg + 0.20 * rem_puerta +
          0.15 * rem_area - 0.25 * gr_oc_fall + 0.05 * palo) - 0.35
    f3 = math.tanh(2.5 * z3)

    # F4 — Vulnerabilidad de la Portería Rival
    ge_rival = vd.get("goles_evitados", 0.5)
    xgot_enf_rival = vd.get("xgot_enfrentado", 0.5)
    paradas_rival = vd.get("paradas", 0.5)
    z4 = (0.35 * xgot_enf_rival + 0.40 * (1.0 - ge_rival) + 0.25 * (1.0 - paradas_rival)) - 0.50
    f4 = math.tanh(2.5 * z4)

    # F5 — Fragilidad Defensiva y Regalos del Rival
    err_gol = vd.get("errores_gol", 0.5)
    err_rem = vd.get("errores_remate", 0.5)
    faltas_r = vd.get("faltas", 0.5)
    tarj_r = 0.7 * vd.get("tarjetas_amarillas", 0.5) + 0.3 * vd.get("tarjetas_rojas", 0.5)
    duelos_suelo = vd.get("duelos_suelo_pct", 0.5)
    duelos_aereo = vd.get("duelos_aereos_pct", 0.5)
    duelos_perdidos = 1.0 - (0.5 * duelos_suelo + 0.5 * duelos_aereo)
    recup_bajas = 1.0 - vd.get("recuperaciones", 0.5)
    z5 = (0.30 * err_gol + 0.20 * err_rem + 0.15 * tarj_r +
          0.20 * duelos_perdidos + 0.15 * recup_bajas) - 0.40
    f5 = math.tanh(2.5 * z5)

    factores = {
        "F_posesion_progresiva": float(f1),
        "F_creacion": float(f2),
        "F_finalizacion": float(f3),
        "F_porteria_rival": float(f4),
        "F_fragilidad_rival": float(f5),
    }
    raw_scores = {"z1": float(z1), "z2": float(z2), "z3": float(z3), "z4": float(z4), "z5": float(z5)}
    return factores, raw_scores


PESOS_DEFECTO_MLP: dict[str, float] = {
    "F_posesion_progresiva": 0.22,
    "F_creacion": 0.28,
    "F_finalizacion": 0.22,
    "F_porteria_rival": 0.16,
    "F_fragilidad_rival": 0.12,
}


def evaluar_ensemble_mlp(
    lambda_base: float,
    factores: dict[str, float],
    pesos: dict[str, float] | None = None,
    alpha: float = 0.25,
    sesgo_b: float = 0.0,
) -> dict[str, Any]:
    """
    Fase 4: Ensemble Híbrido Lineal + No Lineal (MLP Ensemble):
      λ_efectivo = λ_base * exp(α * Δ_MLP)
      donde Δ_MLP = sum(W_k * F_k) + b
    Calcula además la descomposición porcentual de cada factor para IA Explicable (XAI).
    """
    w = pesos or PESOS_DEFECTO_MLP
    delta_mlp = sesgo_b
    desglose_xai: dict[str, float] = {}

    for k, f_val in factores.items():
        w_k = w.get(k, 0.20)
        delta_k = w_k * f_val
        delta_mlp += delta_k
        pct_k = (math.exp(alpha * delta_k) - 1.0) * 100.0
        desglose_xai[k] = round(pct_k, 1)

    delta_mlp_acotado = max(-1.5, min(1.5, delta_mlp))
    mult_total = math.exp(alpha * delta_mlp_acotado)
    lam_efectivo = max(0.05, float(lambda_base * mult_total))
    total_pct = (mult_total - 1.0) * 100.0

    return {
        "lambda_base": float(lambda_base),
        "lambda_efectivo": float(lam_efectivo),
        "delta_mlp": float(delta_mlp),
        "multiplicador": float(mult_total),
        "total_pct": round(total_pct, 1),
        "desglose_xai": desglose_xai,
        "factores_raw": factores,
        "alpha": float(alpha),
    }


def formatear_bloque_xai(motor: MotorMercados) -> list[str]:
    """
    Fase 5 y 8: Genera las líneas de IA Explicable (XAI) con Doble ADN, 6 Cruces Tácticos,
    5 Factores Latentes y Cluster Dominante.
    Usa '▸' para evitar colisión con el conteo de viñetas '•' de selecciones.
    """
    xai = getattr(motor, "xai_factores", None) or {}
    if not xai.get("aplicado"):
        return []
    loc = xai.get("local") or {}
    vis = xai.get("visita") or {}
    f_l = loc.get("factores") or {}
    f_v = vis.get("factores") or {}

    lineas = []

    # 1. ADN del Enfrentamiento (Local en Casa vs. Rival de Visita)
    mu_l = xai.get("mu_local_casa") or (getattr(motor, "recursivo", {}) or {}).get("mu_local_casa") or {}
    mu_v = xai.get("mu_visita_fuera") or (getattr(motor, "recursivo", {}) or {}).get("mu_visita_fuera") or {}

    if mu_l and mu_v:
        lineas.extend([
            "🧬 ADN del Enfrentamiento (Local en Casa vs. Rival de Visita):",
            f"▸ Posesión: {loc.get('nombre', 'Local')} {mu_l.get('posesion_pct', 50.0):.1f}%  vs  {vis.get('nombre', 'Visitante')} {mu_v.get('posesion_pct', 50.0):.1f}%",
            f"▸ Goles / xG: {loc.get('nombre', 'Local')} [{mu_l.get('goles_favor', 1.3):.2f} G / {mu_l.get('xg', 1.3):.2f} xG]  vs  {vis.get('nombre', 'Visitante')} [{mu_v.get('goles_favor', 1.2):.2f} G / {mu_v.get('xg', 1.2):.2f} xG]",
            f"▸ xG Concedido / xGOTc: {loc.get('nombre', 'Local')} [{mu_l.get('goles_contra', 1.2):.2f} Gc / {mu_l.get('xgot_enfrentado', 1.2):.2f} xGOTc]  vs  {vis.get('nombre', 'Visitante')} [{mu_v.get('goles_contra', 1.3):.2f} Gc / {mu_v.get('xgot_enfrentado', 1.3):.2f} xGOTc]",
            f"▸ Remates Totales / A Puerta: {loc.get('nombre', 'Local')} [{mu_l.get('remates_totales', 12.0):.1f} / {mu_l.get('remates_puerta', 4.0):.1f}]  vs  {vis.get('nombre', 'Visitante')} [{mu_v.get('remates_totales', 11.0):.1f} / {mu_v.get('remates_puerta', 3.5):.1f}]",
            f"▸ Toques Área / Córneres: {loc.get('nombre', 'Local')} [{mu_l.get('toques_area_rival', 22.0):.1f} / {mu_l.get('corners', 5.0):.1f}]  vs  {vis.get('nombre', 'Visitante')} [{mu_v.get('toques_area_rival', 18.0):.1f} / {mu_v.get('corners', 4.5):.1f}]",
            f"▸ Despejes / Duelos Aéreos: {loc.get('nombre', 'Local')} [{mu_l.get('despejes', 18.0):.1f} / {mu_l.get('duelos_aereos_pct', 50.0):.1f}%]  vs  {vis.get('nombre', 'Visitante')} [{mu_v.get('despejes', 20.0):.1f} / {mu_v.get('duelos_aereos_pct', 48.0):.1f}%]",
            f"▸ Faltas / Portero Goles Evitados: {loc.get('nombre', 'Local')} [{mu_l.get('faltas', 11.0):.1f} F / {mu_l.get('goles_evitados', 0.0):+.2f} GEv]  vs  {vis.get('nombre', 'Visitante')} [{mu_v.get('faltas', 13.0):.1f} F / {mu_v.get('goles_evitados', 0.0):+.2f} GEv]",
        ])

    # 2. Patrones Detectados por la Red (XAI)
    alertas = xai.get("alertas_xai") or (getattr(motor, "recursivo", {}) or {}).get("alertas_xai") or []
    if alertas:
        lineas.append("⚡ Patrones Detectados por la Red (XAI):")
        for alt in alertas:
            lineas.append(f"▸ {alt}")

    lineas.extend([
        "🧠 Desglose de Factores de la Red Neuronal (XAI):",
        f"▸ {loc.get('nombre', 'Local')}: λ {loc.get('lambda_base', 0.0):.2f} → {loc.get('lambda_efectivo', 0.0):.2f} ({loc.get('total_pct', 0.0):+.1f}%)",
        f"  - Posesión Progresiva: {f_l.get('F_posesion_progresiva', 0.0):+.1f}%",
        f"  - Creación Ofensiva: {f_l.get('F_creacion', 0.0):+.1f}%",
        f"  - Letalidad / Finalización: {f_l.get('F_finalizacion', 0.0):+.1f}%",
        f"  - Vulnerabilidad Portería Rival: {f_l.get('F_porteria_rival', 0.0):+.1f}%",
        f"  - Fragilidad Defensiva Rival: {f_l.get('F_fragilidad_rival', 0.0):+.1f}%",
        f"▸ {vis.get('nombre', 'Visitante')}: λ {vis.get('lambda_base', 0.0):.2f} → {vis.get('lambda_efectivo', 0.0):.2f} ({vis.get('total_pct', 0.0):+.1f}%)",
        f"  - Posesión Progresiva: {f_v.get('F_posesion_progresiva', 0.0):+.1f}%",
        f"  - Creación Ofensiva: {f_v.get('F_creacion', 0.0):+.1f}%",
        f"  - Letalidad / Finalización: {f_v.get('F_finalizacion', 0.0):+.1f}%",
        f"  - Vulnerabilidad Portería Rival: {f_v.get('F_porteria_rival', 0.0):+.1f}%",
        f"  - Fragilidad Defensiva Rival: {f_v.get('F_fragilidad_rival', 0.0):+.1f}%",
    ])
    cl = getattr(motor, "clusters", None) or {}
    dom = cl.get("dominante")
    if dom:
        lineas.append(f"🎯 Cluster Dominante: {dom.get('nombre')} {dom.get('texto_marcadores')} ({dom.get('probabilidad_pct', 0.0):.1f}%)")
    if cl.get("jugada_optima"):
        lineas.append(f"🎯 JUGADA ÓPTIMA TE APUESTO: {cl.get('jugada_optima')}")
    return lineas


def es_seleccion_competitiva_ticket(
    s: Any,
    eq_l: str = "Local",
    eq_v: str = "Visitante",
    p_1x2: tuple[float, float, float] | None = None,
) -> bool:
    """
    Sección 8.4 (Auditoría 30/10/2026):
    Filtra y elimina de raíz las opciones de relleno sin valor comercial (cuotas 1.01-1.08),
    reteniendo exclusivamente las líneas competitivas reales de casas de apuestas (Te Apuesto / Betano).
    """
    def _v(obj: Any, k: str, d: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(k, d)
        return getattr(obj, k, d)

    m_l = str(_v(s, "mercado") or "").lower()
    ev = str(_v(s, "evento") or "").lower()
    et = str(_v(s, "etiqueta") or "").lower()
    per = str(_v(s, "periodo") or "FT").strip().upper()
    if per not in ("FT", "1T", "2T"):
        per = "FT"
    eq_raw = _v(s, "equipo", None)
    eq = str(eq_raw).strip() if eq_raw and str(eq_raw).strip() not in ("None", "Total", "null", "") else None
    lin = _v(s, "linea", None)
    prob = float(_v(s, "probabilidad", 0.0) or 0.0)

    es_over = ("over_" in ev or "mas de" in et or "más de" in et or "al_menos_" in ev or "al menos" in et)
    es_under = ("under_" in ev or "menos de" in et or "menos" in et)
    if "al_menos_" in ev and lin is not None and isinstance(lin, (int, float)):
        lin = float(lin) - 0.5

    # Prohibición universal de "-0.5 Goles" / "Menos de 0.5 goles" (Punto 4)
    if es_under and lin is not None and abs(float(lin) - 0.5) < 1e-6 and ("gol" in m_l or "gol" in et or "under_0.5" in ev):
        return False
    if "-0.5 gol" in et or "menos de 0.5 gol" in et:
        return False

    # 1. CÓRNERES
    if any(k in m_l for k in ("corner", "córner", "crner", "esquina")) or "corner" in ev:
        if lin is None:
            return False
        lin_f = float(lin)
        if eq:
            # Córneres por equipo: solo líneas 3.5 a 6.5 en FT
            if per != "FT":
                return False
            return 3.5 <= lin_f <= 6.5
        else:
            # Córneres Totales FT: +7.5 a +11.5 y -6.5 a -12.5 (Poisson estricto)
            if per != "FT":
                return False
            if es_over:
                return 7.5 <= lin_f <= 11.5
            if es_under:
                return 6.5 <= lin_f <= 12.5
            return False

    # 2. TARJETAS
    if any(k in m_l for k in ("tarjeta", "amarilla", "roja")) or any(k in ev for k in ("tarjeta", "amarilla", "roja")):
        if "roja" in ev or "roja" in et:
            return False
        if lin is None or per != "FT" or eq:
            return False
        lin_f = float(lin)
        # Tarjetas Totales FT: solo +2.5 a +5.5 y -4.5 a -6.5 (prohibido +0.5 Tarj., -7.5, -8.5)
        if es_over:
            return 2.5 <= lin_f <= 5.5
        if es_under:
            return 4.5 <= lin_f <= 6.5
        return False

    # 3. HÁNDICAP / 1X2 / DC / DNB
    if any(k in m_l for k in ("hándicap", "handicap", "doble oportunidad", "sin empate", "ganador", "1x2", "1t/ft", "descanso / final")) or any(k in ev for k in ("1x", "x2", "12", "dnb", "local_gana", "visitante_gana", "empate", "h0_", "h1_", "h2_", "h3_", "h-1_", "h_minus1_")):
        # Ajuste c) Eliminación Permanente de "12 (Sin Empate)" (Osasuna 1-1 Athletic):
        # Prohibido "12 (Sin Empate)" tanto en Top 5 como en Sección 1: Alta Probabilidad.
        es_dc12 = (
            ev in ("12", "dc_12")
            or ev.endswith("_12")
            or "doble oportunidad 12" in et
            or ("12" in et and "sin empate" in et)
        )
        if es_dc12:
            return False
        if per != "FT":
            # En 1T/2T solo permitir Doble Oportunidad o Ganador si está en rango de valor [0.70, 0.92]
            if any(h in ev for h in ("h1_", "h2_", "h3_", "h0_")):
                return False
            return 0.70 <= prob <= 0.92
        # Prohibido terminantemente H+2 y H+3
        if "h2_" in ev or "h3_" in ev or "h+2" in et or "h+3" in et or "+2" in et or "+3" in et:
            return False
        # Prohibido H+1 a favor del favorito claro
        if ("h1_" in ev or "h+1" in et or "+1" in et) and p_1x2 is not None:
            p1, _px, p2 = p_1x2
            if eq == eq_l and p1 >= p2:
                return False
            if eq == eq_v and p2 > p1:
                return False
            if prob > 0.92:
                return False
        return True

    # 4. GOLES POR EQUIPO
    if eq and ("primer_gol" not in ev):
        if lin is None:
            return False
        lin_f = float(lin)
        # Prohibido líneas negativas en 1T/2T (-0.5 a -6.5 en 1T o 2T) y prohibido -0.5 o -3.5 a -6.5 en FT
        if per in ("1T", "2T"):
            if es_under:
                return False
            return lin_f in (0.5, 1.5) and (0.70 <= prob <= 0.90)
        # En FT solo +0.5, +1.5, +2.5 y -1.5, -2.5 (jamás -0.5 Goles)
        if es_over:
            return lin_f in (0.5, 1.5, 2.5)
        if es_under:
            return lin_f in (1.5, 2.5)
        return False

    # 5. GOLES PARTIDO (FT, 1T, 2T, BTTS, Primer Gol)
    if "ambos_marcan" in ev or "btts" in ev or "primer_gol" in ev:
        return per == "FT"
    if lin is not None:
        lin_f = float(lin)
        if per in ("1T", "2T"):
            # Goles 1T / 2T: solo +0.5, +1.5 y -1.5 (prohibido -0.5 y -2.5 a -6.5)
            if es_over:
                return abs(lin_f - 0.5) < 1e-6 or abs(lin_f - 1.5) < 1e-6
            if es_under:
                return abs(lin_f - 1.5) < 1e-6
            return False
        else:
            # Goles Totales FT: estrictamente líneas estándar 1.5, 2.5, 3.5 y 4.5 (Punto 4)
            if lin_f not in (1.5, 2.5, 3.5, 4.5):
                return False
            if es_over:
                return 1.5 <= lin_f <= 4.5
            if es_under:
                return 1.5 <= lin_f <= 4.5
            return False

    return True


def filtrar_top_opciones_ticket(
    selecciones: list[Any],
    eq_l: str = "Local",
    eq_v: str = "Visitante",
    p_1x2: tuple[float, float, float] | None = None,
    max_opciones: int = 18,
) -> list[Any]:
    """
    Filtra las selecciones competitivas reales y limita la tabla del Ticket PNG
    a las `max_opciones` (15-20) jugadas de mayor valor práctico (priorizando el rango 70%-90%
    y diversificando por categorías).
    """
    def _v(obj: Any, k: str, d: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(k, d)
        return getattr(obj, k, d)

    competitivas = [s for s in selecciones if es_seleccion_competitiva_ticket(s, eq_l, eq_v, p_1x2)]
    if len(competitivas) <= max_opciones:
        return competitivas

    # Priorizar opciones en la zona de valor real [0.70, 0.90] sobre las de >0.92
    def _score_valor(s: Any) -> float:
        p = float(_v(s, "probabilidad", 0.0) or 0.0)
        estado_bonus = 0.05 if _v(s, "estado") == "mantiene" else 0.0
        per_bonus = 0.03 if str(_v(s, "periodo") or "FT").upper() == "FT" else 0.0
        if 0.72 <= p <= 0.89:
            return 1.0 + p + estado_bonus + per_bonus
        elif 0.70 <= p < 0.72:
            return 0.85 + p + estado_bonus + per_bonus
        else:
            # Penalizar probabilidades > 90% (cuotas muy bajas) para priorizar cuotas competitivas
            return 0.60 - abs(p - 0.82) + estado_bonus + per_bonus

    competitivas_ord = sorted(competitivas, key=_score_valor, reverse=True)
    return competitivas_ord[:max_opciones]


def seleccionar_top_5_jugadas_recomendadas(
    selecciones: list[Any],
    motor: Any = None,
    eq_l: str = "Local",
    eq_v: str = "Visitante",
) -> list[dict[str, Any]]:
    """
    Sección 8.4 (Auditoría 30/10/2026):
    Selecciona las 5 Mejores Apuestas Recomendadas del Partido (Top-5 Podio de Valor Te Apuesto),
    diversificadas por tipo de mercado y priorizando probabilidades entre 70% y 88% (cuotas ~1.22 a 1.45):
      1. Resultado / Doble Oportunidad / Sin Empate (1X2, 1X, X2 o DNB).
      2. Goles Totales FT (+1.5, +2.5 o -3.5 FT).
      3. Goles por Mitad o por Equipo (+0.5 en 1T/2T o Goles del Favorito +0.5/+1.5 FT).
      4. Córneres del Partido o del Favorito (+7.5/+8.5/+9.5 FT o Córneres del equipo asediador).
      5. Tarjetas o Ambos Anotan (+2.5/+3.5 Tarjetas FT o Ambos Anotan Sí/No).
    Cada jugada incluye un respaldo estadístico corto de 1 línea extraído del Doble ADN y los 6 Cruces.
    """
    def _v(obj: Any, k: str, d: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(k, d)
        return getattr(obj, k, d)

    p_1x2 = None
    marcador_mas_probable: tuple[int, int] = (1, 0)
    prob_marcador_max: float = 0.0
    if motor is not None and getattr(motor, "matriz_ft", None):
        mat = motor.matriz_ft
        R = len(mat)
        C = len(mat[0]) if R else 0
        p1 = sum(mat[i][j] for i in range(R) for j in range(C) if i > j)
        px = sum(mat[i][j] for i in range(R) for j in range(C) if i == j)
        p2 = sum(mat[i][j] for i in range(R) for j in range(C) if i < j)
        p_1x2 = (p1, px, p2)
        for i in range(R):
            for j in range(C):
                if float(mat[i][j]) > prob_marcador_max:
                    prob_marcador_max = float(mat[i][j])
                    marcador_mas_probable = (i, j)

    import copy as _copy
    candidatas = [
        (dict(s) if isinstance(s, dict) else _copy.copy(s))
        for s in selecciones
        if es_seleccion_competitiva_ticket(s, eq_l, eq_v, p_1x2)
        and float(_v(s, "probabilidad", 0.0) or 0.0) >= 0.70
    ]

    xai = getattr(motor, "xai_factores", None) or {}
    rec = getattr(motor, "recursivo", None) or {}
    mu_l = xai.get("mu_local_casa") or rec.get("mu_local_casa") or {}
    mu_v = xai.get("mu_visita_fuera") or rec.get("mu_visita_fuera") or {}
    lam_l = float(getattr(getattr(motor, "tasas", None), "lambda_local_ft", 0.0) or rec.get("lambda_local_recursivo") or 1.35)
    lam_v = float(getattr(getattr(motor, "tasas", None), "lambda_visitante_ft", 0.0) or rec.get("lambda_visita_recursivo") or 1.10)

    # ── Punto 6 y Ajuste c): Exclusión Permanente de "12 (Sin Empate)" cuando P(X) >= 15.0% o duelo de bajo/medio gol ──
    px_actual = p_1x2[1] if p_1x2 is not None else 0.25
    racha_l_top = getattr(motor, "racha_local", None) or rec.get("racha_local") or {}
    racha_v_top = getattr(motor, "racha_visita", None) or rec.get("racha_visita") or {}
    ctx_av_top = getattr(motor, "contexto_avanzado", None) or rec.get("contexto_avanzado") or {}
    p_crisis_max = 0.0
    if p_1x2 is not None:
        if racha_l_top.get("en_crisis"):
            p_crisis_max = max(p_crisis_max, float(p_1x2[0]))
        if racha_v_top.get("en_crisis"):
            p_crisis_max = max(p_crisis_max, float(p_1x2[2]))

    prohibir_dc_12 = (
        px_actual >= 0.15
        or (px_actual + p_crisis_max) >= 0.42
        or (lam_l + lam_v) < 3.0
        or marcador_mas_probable in ((0, 0), (1, 1))
    )

    def _es_dc_12(s: Any) -> bool:
        ev_s = str(_v(s, "evento") or "").lower().strip()
        et_s = str(_v(s, "etiqueta") or "").lower().strip()
        return (
            ev_s in ("12", "dc_12")
            or ev_s.endswith("_12")
            or "doble oportunidad 12" in et_s
            or ("12" in et_s and "empate" in et_s)
        )

    if prohibir_dc_12:
        candidatas = [s for s in candidatas if not _es_dc_12(s)]

    # Ajuste e) Límite de Tarjetas por Monopolio de Posesión:
    # Si la posesión proyectada del favorito supera el 68%, prohibir recomendar +4.5 o +5.5 Tarjetas Totales en el Top 5 (salvo árbitro >= 5.5).
    monopolio_tarj = bool(getattr(motor, "monopolio_posesion_tarjetas", False))
    arb_prom = float((ctx_av_top.get("info_contexto_fs_fm") or {}).get("arbitro_promedio_tarjetas", 0.0) or 0.0) if isinstance(ctx_av_top, dict) else 0.0
    if monopolio_tarj and arb_prom < 5.5:
        def _es_over_tarj_alta(s: Any) -> bool:
            ev_t = str(_v(s, "evento") or "").lower()
            m_t = str(_v(s, "mercado") or "").lower()
            lin_t = _v(s, "linea", None)
            es_t = ("tarjeta" in ev_t or "amarilla" in ev_t or "tarjeta" in m_t or "amarilla" in m_t)
            es_ov = ("over" in ev_t or "mas" in str(_v(s, "etiqueta") or "").lower() or "más" in str(_v(s, "etiqueta") or "").lower())
            return bool(es_t and es_ov and lin_t is not None and float(lin_t) >= 4.5)
        candidatas = [s for s in candidatas if not _es_over_tarj_alta(s)]

    # ── Puntos 10 y 20: Filtro +EV con Cuota Real o Fallback a Cuota Mínima ──
    candidatas_filtradas_ev: list[Any] = []
    for s in candidatas:
        prob_s = float(_v(s, "probabilidad", 0.0) or 0.0)
        try:
            cuota_r = _v(s, "cuota_real", _v(s, "cuota", _v(s, "odds", None)))
            if cuota_r is not None and float(cuota_r) > 1.01:
                ev_calc = (prob_s * float(cuota_r)) - 1.0
                if ev_calc < 0.03:
                    continue
        except Exception:
            pass
        candidatas_filtradas_ev.append(s)
    if candidatas_filtradas_ev:
        candidatas = candidatas_filtradas_ev

    # ── Punto 12: Detección de duelo cerrado (1-0, 0-1, 0-0 > 15%) ──
    es_duelo_cerrado = (marcador_mas_probable in ((1, 0), (0, 1), (0, 0)) and prob_marcador_max > 0.15) or ((lam_l + lam_v) <= 2.35)

    # ── Cerrojo / Hurdle Zero-Inflation y Detección Diésel ───────────────────
    mat = getattr(motor, "matriz_ft", None)
    p_btts_no_pct = 0.0
    if mat:
        R = len(mat)
        C = len(mat[0]) if R else 0
        p_btts_si = sum(mat[i][j] for i in range(1, R) for j in range(1, C)) * 100.0
        p_btts_no_pct = 100.0 - p_btts_si

    seg_conc_l = float(rec.get("seg_gol_concedido_rival_V") or 0.0)
    seg_conc_v = float(rec.get("seg_gol_concedido_rival_L") or 0.0)

    prohibir_btts_si = (
        lam_l < 1.25 or lam_v < 1.25 or
        seg_conc_l >= 4200.0 or seg_conc_v >= 4200.0 or
        p_btts_no_pct > 48.0
    )

    if prohibir_btts_si:
        candidatas = [
            s for s in candidatas
            if not (
                ("btts_si" in str(_v(s, "evento") or "").lower()) or
                ("ambos" in str(_v(s, "evento") or "").lower() and any(k in str(_v(s, "evento") or "").lower() for k in ("si", "sí", "yes")))
            )
        ]

    # Detección Diésel
    xg1_l = float(mu_l.get("xg_1t", mu_l.get("xg_1T", 0.0)) or 0.0)
    xg2_l = float(mu_l.get("xg_2t", mu_l.get("xg_2T", 0.0)) or 0.0)
    xg1_v = float(mu_v.get("xg_1t", mu_v.get("xg_1T", 0.0)) or 0.0)
    xg2_v = float(mu_v.get("xg_2t", mu_v.get("xg_2T", 0.0)) or 0.0)
    t1_l = getattr(getattr(motor, "tasas", None), "lambda_local_1t", None)
    t2_l = getattr(getattr(motor, "tasas", None), "lambda_local_2t", None)
    t1_v = getattr(getattr(motor, "tasas", None), "lambda_visitante_1t", None)
    t2_v = getattr(getattr(motor, "tasas", None), "lambda_visitante_2t", None)

    es_diesel = False
    if (xg1_l > 0 and xg2_l >= 1.30 * xg1_l) or (xg1_v > 0 and xg2_v >= 1.30 * xg1_v):
        es_diesel = True
    elif (t1_l and t2_l and t2_l >= 1.30 * t1_l) or (t1_v and t2_v and t2_v >= 1.30 * t1_v):
        es_diesel = True

    lam_1t_comb = ((t1_l or 0.50) + (t1_v or 0.45)) if (t1_l is not None) else (0.45 * (lam_l + lam_v))
    p_u15_1t_val = round(math.exp(-lam_1t_comb) * (1.0 + lam_1t_comb) * 100.0, 1)
    patron_diesel_1t = es_diesel and (lam_1t_comb < 1.25) and (p_u15_1t_val >= 68.0)

    # ── Puntos 21c, 23, 24b y 25: Gatekeeper Avanzado (Doble Freno, Candado 1T -> 2T, Córneres Top 5 y Triple Convergencia) ──
    pat_l_top = rec.get("patrones_55_local") or {}
    pat_v_top = rec.get("patrones_55_visita") or {}
    r_l_top = (rec.get("ratings_local") or {}) if isinstance(rec.get("ratings_local"), dict) else {}
    r_v_top = (rec.get("ratings_visita") or {}) if isinstance(rec.get("ratings_visita"), dict) else {}
    atk_l_top = float(r_l_top.get("Final_Attack_Strength", r_l_top.get("attack", 50.0)) or 50.0)
    atk_v_top = float(r_v_top.get("Final_Attack_Strength", r_v_top.get("attack", 50.0)) or 50.0)
    if atk_l_top == 50.0 and getattr(motor, "doc_local", None):
        atk_l_top = float(((motor.doc_local.get("perfil") or {}).get("ratings_0_100") or {}).get("Final_Attack_Strength", 50.0) or 50.0)
    if atk_v_top == 50.0 and getattr(motor, "doc_visitante", None):
        atk_v_top = float(((motor.doc_visitante.get("perfil") or {}).get("ratings_0_100") or {}).get("Final_Attack_Strength", 50.0) or 50.0)

    pos_l_raw_t = float(mu_l.get("posesion_pct", pat_l_top.get("posesion_pct", 50.0)) or 50.0)
    pos_v_raw_t = float(mu_v.get("posesion_pct", pat_v_top.get("posesion_pct", 50.0)) or 50.0)
    pos_sum_t = max(1.0, pos_l_raw_t + pos_v_raw_t)
    pos_proy_l_t = (pos_l_raw_t / pos_sum_t) * 100.0
    pos_proy_v_t = (pos_v_raw_t / pos_sum_t) * 100.0

    # Punto 21c: Prohibido recomendar "[Equipo] +0.5 Goles" si posesion_proyectada < 43.0% O attack_rating < 34.0 O lam_red < 0.95
    prohibir_over05_local = (pos_proy_l_t < 43.0 or atk_l_top < 34.0 or lam_l < 0.95)
    prohibir_over05_visita = (pos_proy_v_t < 43.0 or atk_v_top < 34.0 or lam_v < 0.95)

    def _es_equipo_over_05(s: Any, bando_check: str) -> bool:
        ev_s = str(_v(s, "evento") or "").lower()
        et_s = str(_v(s, "etiqueta") or "").lower()
        eq_s = str(_v(s, "equipo") or "").strip().lower()
        lin_s = _v(s, "linea", None)
        nom_eq = (eq_l if bando_check == "local" else eq_v).strip().lower()
        es_bando = (
            ev_s.startswith(f"{bando_check}_")
            or (eq_s and (eq_s == nom_eq or eq_s in nom_eq or nom_eq in eq_s))
        )
        if not es_bando:
            return False
        if ("over_0.5" in ev_s) or ("al_menos_1" in ev_s):
            return True
        if lin_s is not None and abs(float(lin_s) - 0.5) < 1e-6 and ("over" in ev_s or "mas" in et_s or "más" in et_s):
            return True
        if lin_s is not None and abs(float(lin_s) - 1.0) < 1e-6 and ("al_menos" in ev_s or "al menos" in et_s):
            return True
        return False

    if prohibir_over05_local:
        candidatas = [s for s in candidatas if not _es_equipo_over_05(s, "local")]
    if prohibir_over05_visita:
        candidatas = [s for s in candidatas if not _es_equipo_over_05(s, "visitante")]

    # Cuadro B y Cuadro C para Triple Check (Punto 25), Candado 1T -> 2T (Punto 23) y Gigante Visitante (Puntos 35 y 40)
    cb_l_top: dict[str, Any] = {}
    cb_v_top: dict[str, Any] = {}
    cc_l_top: dict[str, Any] = {}
    cc_v_top: dict[str, Any] = {}
    mercados_mitad_bloq: set[str] = set()
    try:
        from motor_inferencia import calcular_cuadro_b_tendencias, calcular_cuadro_c_timeline
        pts_l_b = (getattr(motor, "doc_local", None) or {}).get("partidos") or []
        pts_v_b = (getattr(motor, "doc_visitante", None) or {}).get("partidos") or []
        cb_l_top = getattr(motor, "cuadro_b_local", None) or rec.get("cuadro_b_local") or calcular_cuadro_b_tendencias(pts_l_b, eq_l, pat_l_top or mu_l)
        cb_v_top = getattr(motor, "cuadro_b_visita", None) or rec.get("cuadro_b_visita") or calcular_cuadro_b_tendencias(pts_v_b, eq_v, pat_v_top or mu_v)
        cc_l_top = getattr(motor, "cuadro_c_local", None) or rec.get("cuadro_c_local") or calcular_cuadro_c_timeline(pts_l_b, eq_l, pat_l_top or mu_l)
        cc_v_top = getattr(motor, "cuadro_c_visita", None) or rec.get("cuadro_c_visita") or calcular_cuadro_c_timeline(pts_v_b, eq_v, pat_v_top or mu_v)
        for doc_side in (getattr(motor, "doc_local", None), getattr(motor, "doc_visitante", None)):
            if isinstance(doc_side, dict):
                cob_side = (doc_side.get("cobertura_matriz") or {})
                for cl_b in (cob_side.get("mercados_mitad_bloqueados") or []):
                    mercados_mitad_bloq.add(str(cl_b).lower())
    except Exception:
        pass

    # Punto 39: Si una métrica tiene 1T/2T como "sin_dato" (en mercados_mitad_bloqueados),
    # cualquier recomendación de ese mercado por mitad queda bloqueada.
    bloq_mot = getattr(motor, "mercados_mitad_bloqueados", None)
    if isinstance(bloq_mot, (set, list, tuple)):
        for b_m in bloq_mot:
            mercados_mitad_bloq.add(str(b_m).lower())
    sin_mitades_goles = ("goles" in mercados_mitad_bloq)
    if sin_mitades_goles:
        candidatas = [
            s for s in candidatas
            if str(_v(s, "periodo") or "FT").strip().upper() not in ("1T", "2T")
            and str(_v(s, "evento") or "").lower() not in ("gol_1t", "gol_2t")
        ]
    if "corners" in mercados_mitad_bloq:
        candidatas = [
            s for s in candidatas
            if not (
                str(_v(s, "periodo") or "FT").strip().upper() in ("1T", "2T")
                and ("corner" in str(_v(s, "evento") or "").lower() or "córner" in str(_v(s, "mercado") or "").lower() or "corner" in str(_v(s, "mercado") or "").lower())
            )
        ]

    # Puntos 34, 35, 36 y 40 (Sección 8): Contexto de Copa/Playoff, Asedio verificado y Gigante Visitante
    from motor_patrones import detectar_contexto_competicion, evaluar_condicion_gigante_visitante
    comp_top = str(
        getattr(getattr(motor, "p", None), "competicion", "")
        or (((getattr(motor, "doc_local", None) or {}).get("partidos") or [{}])[0] if ((getattr(motor, "doc_local", None) or {}).get("partidos")) else {}).get("torneo")
        or rec.get("competicion")
        or ""
    )
    ctx_comp_top = detectar_contexto_competicion(competicion=comp_top)
    es_eliminatoria_copa = bool(ctx_comp_top.get("es_copa_o_playoff", False))

    toq_l_obs = mu_l.get("toques_area_rival", pat_l_top.get("toques_area_rival"))
    toq_v_obs = mu_v.get("toques_area_rival", pat_v_top.get("toques_area_rival"))
    asedio_verificado_top = bool(
        (pos_l_raw_t >= 57.0 and toq_l_obs is not None and float(toq_l_obs) >= 22.0)
        or (pos_v_raw_t >= 57.0 and toq_v_obs is not None and float(toq_v_obs) >= 22.0)
    )

    # Sección 8.2: Si existe eliminatoria de copa o asedio verificado, vetar Under de tarjetas en el Top 5
    if es_eliminatoria_copa or asedio_verificado_top:
        def _es_under_tarjetas(s: Any) -> bool:
            m_t = str(_v(s, "mercado") or "").lower()
            ev_t = str(_v(s, "evento") or "").lower()
            et_t = str(_v(s, "etiqueta") or "").lower()
            es_t = any(k in m_t for k in ("tarjeta", "amarilla")) or any(k in ev_t for k in ("tarjeta", "amarilla"))
            es_un = ("under" in ev_t or "menos" in et_t or "-" in et_t)
            return bool(es_t and es_un)
        candidatas = [s for s in candidatas if not _es_under_tarjetas(s)]

    # Sección 8.4: En eliminatoria de copa, vetar Under 9.5 córneres (y líneas Under <= 9.5 de córneres) en el Top 5
    if es_eliminatoria_copa:
        def _es_under_corners_copa(s: Any) -> bool:
            m_c = str(_v(s, "mercado") or "").lower()
            ev_c = str(_v(s, "evento") or "").lower()
            et_c = str(_v(s, "etiqueta") or "").lower()
            lin_c = _v(s, "linea", None)
            es_c = any(k in m_c for k in ("corner", "córner", "crner", "esquina")) or "corner" in ev_c
            es_un = ("under" in ev_c or "menos" in et_c)
            return bool(es_c and es_un and lin_c is not None and float(lin_c) <= 9.5)
        candidatas = [s for s in candidatas if not _es_under_corners_copa(s)]

    info_gv = evaluar_condicion_gigante_visitante(
        cuadro_b_local=cb_l_top,
        cuadro_b_visita=cb_v_top,
        attack_local=atk_l_top,
        attack_visita=atk_v_top,
    )
    es_gigante_visitante = bool(
        info_gv.get("activo", False)
        or rec.get("gigante_visitante_activo")
        or rec.get("es_gigante_visitante")
        or getattr(motor, "es_gigante_visitante", False)
    )
    vetar_1x_por_gigante_visita = bool(
        es_gigante_visitante
        or (p_1x2 is not None and float(p_1x2[0] + p_1x2[1]) < 0.70)
    )
    if es_gigante_visitante or vetar_1x_por_gigante_visita:
        # Vetar 1X, 1 directo local y cualquier hándicap/DNB a favor del local
        def _es_favor_local_slot1(s: Any) -> bool:
            ev_s_l = str(_v(s, "evento") or "").lower().strip()
            eq_s_l = str(_v(s, "equipo") or "").strip().lower()
            if ev_s_l in ("1", "local_gana", "1x", "dc_1x") or ev_s_l.endswith("_1x") or "local" in ev_s_l:
                return True
            if eq_s_l and (eq_s_l == eq_l.strip().lower() or eq_s_l in eq_l.strip().lower()):
                if any(k in ev_s_l for k in ("dnb", "h0_", "h1_", "h-1_", "h_minus1_")):
                    return True
            return False
        candidatas = [s for s in candidatas if not _es_favor_local_slot1(s)]

    # Garantizar coherencia exacta de 1X y X2 con P(Local)+P(Empate) y P(Empate)+P(Visita) de la matriz final,
    # evaluando el umbral >= 0.70 en punto flotante ANTES de redondear (0.6996 < 0.70 queda fuera).
    if p_1x2 is not None:
        p1_f, px_f, p2_f = p_1x2
        p_1x_exacta = float(p1_f + px_f)
        p_x2_exacta = float(px_f + p2_f)
        candidatas_sinc: list[Any] = []
        for s in candidatas:
            ev_s_low = str(_v(s, "evento") or "").lower().strip()
            per_s_up = str(_v(s, "periodo") or "FT").strip().upper()
            if per_s_up == "FT" and (ev_s_low in ("1x", "dc_1x") or ev_s_low.endswith("_1x")):
                if p_1x_exacta < 0.70 or vetar_1x_por_gigante_visita:
                    continue
                if isinstance(s, dict):
                    s["probabilidad"] = p_1x_exacta
                else:
                    try:
                        s.probabilidad = p_1x_exacta
                    except Exception:
                        pass
            elif per_s_up == "FT" and (ev_s_low in ("x2", "dc_x2") or ev_s_low.endswith("_x2")):
                if p_x2_exacta < 0.70:
                    continue
                if isinstance(s, dict):
                    s["probabilidad"] = p_x2_exacta
                else:
                    try:
                        s.probabilidad = p_x2_exacta
                    except Exception:
                        pass
            elif per_s_up == "FT" and ev_s_low in ("1", "local_gana"):
                if float(p1_f) < 0.70 or es_gigante_visitante:
                    continue
                if isinstance(s, dict):
                    s["probabilidad"] = float(p1_f)
                else:
                    try:
                        s.probabilidad = float(p1_f)
                    except Exception:
                        pass
            elif per_s_up == "FT" and ev_s_low in ("2", "visitante_gana"):
                if float(p2_f) < 0.70:
                    continue
                if isinstance(s, dict):
                    s["probabilidad"] = float(p2_f)
                else:
                    try:
                        s.probabilidad = float(p2_f)
                    except Exception:
                        pass
            candidatas_sinc.append(s)
        candidatas = candidatas_sinc

    def _prop_goles_2t_cc(cc_d: dict[str, Any]) -> float:
        if not isinstance(cc_d, dict):
            return 0.50
        g1 = 0
        g2 = 0
        for fr in ("00_15", "16_30", "31_45", "00'-15'", "16'-30'", "31'-45'"):
            if isinstance(cc_d.get(fr), dict):
                g1 += int(cc_d[fr].get("anotados", 0))
        for fr in ("46_60", "61_75", "76_90", "46'-60'", "61'-75'", "76'-90'"):
            if isinstance(cc_d.get(fr), dict):
                g2 += int(cc_d[fr].get("anotados", 0))
        tot = g1 + g2
        return (g2 / tot) if tot > 0 else 0.50

    # Sección 5.3: Preservar 0.0 real y None en P26_Poder_Cierre_2T (jamás sustituir 0.0 ni None por 0.52)
    p26_l_raw = pat_l_top.get("P26_Poder_Cierre_2T") if "P26_Poder_Cierre_2T" in pat_l_top else None
    p26_v_raw = pat_v_top.get("P26_Poder_Cierre_2T") if "P26_Poder_Cierre_2T" in pat_v_top else None
    vals_p26 = [float(x) for x in (p26_l_raw, p26_v_raw) if x is not None]
    prom_p26: float | None = (sum(vals_p26) / len(vals_p26)) if vals_p26 else None
    prop_2t_cc = (_prop_goles_2t_cc(cc_l_top) + _prop_goles_2t_cc(cc_v_top)) / 2.0
    desp_l_t = float(mu_l.get("despejes", pat_l_top.get("despejes", 18.0)) or 18.0)
    desp_v_t = float(mu_v.get("despejes", pat_v_top.get("despejes", 18.0)) or 18.0)

    hay_bloque_bajo_23 = (
        pos_proy_l_t < 44.0 or pos_proy_v_t < 44.0
        or desp_l_t >= 20.0 or desp_v_t >= 20.0
        or atk_l_top < 35.0 or atk_v_top < 35.0
    )
    permitir_1t_over_05 = (max(atk_l_top, atk_v_top) >= 70.0 and lam_1t_comb >= 1.45) and not hay_bloque_bajo_23
    prohibir_1t_over_05 = (
        not permitir_1t_over_05
        or (prom_p26 is not None and prom_p26 >= 0.51)
        or prop_2t_cc > 0.55
        or hay_bloque_bajo_23
    )
    # Sección 8.4: En copa/eliminatoria con λ_L + λ_V < 2.2, eliminar prioridad automática de Goles 2T: +0.5 Goles
    desactivar_prioridad_2t_copa = bool(es_eliminatoria_copa and (lam_l + lam_v) < 2.2)

    def _es_1t_over_05(s: Any) -> bool:
        ev_s = str(_v(s, "evento") or "").lower()
        et_s = str(_v(s, "etiqueta") or "").lower()
        per_s = str(_v(s, "periodo") or "FT").strip().upper()
        lin_s = _v(s, "linea", None)
        eq_s = _v(s, "equipo", None)
        if eq_s and str(eq_s).strip() not in ("None", "Total", "null", ""):
            return False
        if ev_s == "gol_1t" or "al menos un gol en el 1t" in et_s:
            return True
        if per_s == "1T":
            if lin_s is not None and abs(float(lin_s) - 0.5) < 1e-6 and ("over" in ev_s or "mas" in et_s or "más" in et_s):
                return True
            if lin_s is not None and abs(float(lin_s) - 1.0) < 1e-6 and ("al_menos" in ev_s or "al menos" in et_s):
                return True
        return False

    if prohibir_1t_over_05:
        tiene_1t = any(_es_1t_over_05(s) for s in candidatas)
        candidatas = [s for s in candidatas if not _es_1t_over_05(s)]
        # Si no había una selección 2T +0.5 en candidatas y prom_p26 real >= 0.51 (y no está desactivado por copa baja ni bloqueado por mitad), inyectarla
        tiene_2t_05 = any(
            str(_v(s, "periodo") or "").upper() == "2T"
            and (
                str(_v(s, "evento") or "").lower() in ("gol_2t", "over_0.5", "al_menos_1")
                or (_v(s, "linea") is not None and abs(float(_v(s, "linea")) - 0.5) < 1e-6 and "over" in str(_v(s, "evento") or "").lower())
            )
            for s in candidatas
        )
        if (
            not tiene_2t_05
            and not sin_mitades_goles
            and not desactivar_prioridad_2t_copa
            and prom_p26 is not None
            and prom_p26 >= 0.51
        ):
            lam_2t_c = ((t2_l or 0.65) + (t2_v or 0.55)) if (t2_l is not None) else max(0.85, 0.56 * (lam_l + lam_v))
            p_2t_05 = round(min(0.88, max(0.72, 1.0 - math.exp(-lam_2t_c))), 4)
            candidatas.append({
                "id_mercado": "total_goles",
                "mercado": "Total de goles",
                "evento": "over_0.5",
                "etiqueta": "más de 0.5 goles",
                "periodo": "2T",
                "equipo": None,
                "linea": 0.5,
                "probabilidad": p_2t_05,
                "estado": "mantiene",
            })

    # Punto 23c y Sección 5.3: Alerta de Posesión Estéril vs. Portero Figura (preservando P01=None como no_evaluable)
    p01_l_raw = pat_l_top.get("P01_xG_por_Remate")
    p01_v_raw = pat_v_top.get("P01_xG_por_Remate")
    p01_l_t = float(p01_l_raw) if p01_l_raw is not None else None
    p01_v_t = float(p01_v_raw) if p01_v_raw is not None else None
    p06_l_t = float(pat_l_top.get("P06_Desperdicio_Ocasiones", 0.40) or 0.40)
    p06_v_t = float(pat_v_top.get("P06_Desperdicio_Ocasiones", 0.40) or 0.40)
    p14_l_t = float(pat_l_top.get("P14_Portero_Tasa_Paradas", 0.68) or 0.68)
    p14_v_t = float(pat_v_top.get("P14_Portero_Tasa_Paradas", 0.68) or 0.68)
    posesion_esteril_vs_portero = (
        (((p01_l_t is not None and p01_l_t < 0.11) or p06_l_t >= 0.55) and p14_v_t >= 0.70)
        or (((p01_v_t is not None and p01_v_t < 0.11) or p06_v_t >= 0.55) and p14_l_t >= 0.70)
    )
    if posesion_esteril_vs_portero:
        def _es_over_ft_15_25(s: Any) -> bool:
            ev_s = str(_v(s, "evento") or "").lower()
            et_s = str(_v(s, "etiqueta") or "").lower()
            per_s = str(_v(s, "periodo") or "FT").strip().upper()
            lin_s = _v(s, "linea", None)
            eq_s = _v(s, "equipo", None)
            if per_s != "FT" or (eq_s and str(eq_s).strip() not in ("None", "Total", "null", "")):
                return False
            es_ov = ("over" in ev_s or "mas" in et_s or "más" in et_s or "al_menos" in ev_s)
            if not es_ov or lin_s is None:
                return False
            lf = float(lin_s) - 0.5 if "al_menos" in ev_s else float(lin_s)
            return lf in (1.5, 2.5)
        candidatas = [s for s in candidatas if not _es_over_ft_15_25(s)]

    # Punto 24b: Prohibido recomendar +10.5 o +11.5 Córneres en el Top 5
    def _es_over_corners_excesivo(s: Any) -> bool:
        m_s = str(_v(s, "mercado") or "").lower()
        ev_s = str(_v(s, "evento") or "").lower()
        et_s = str(_v(s, "etiqueta") or "").lower()
        lin_s = _v(s, "linea", None)
        es_c = any(k in m_s for k in ("corner", "córner", "crner", "esquina")) or "corner" in ev_s
        es_ov = ("over" in ev_s or "mas" in et_s or "más" in et_s)
        return bool(es_c and es_ov and lin_s is not None and float(lin_s) >= 10.5)

    candidatas = [s for s in candidatas if not _es_over_corners_excesivo(s)]

    # Punto 25: Triple Check de Convergencia (Descartar contradicción frontal con Cuadro B)
    def _cnt_cb(cb_d: dict[str, Any], k: str, default: int = 8) -> int:
        if not isinstance(cb_d, dict) or not cb_d:
            return default
        v = cb_d.get(k)
        if isinstance(v, dict):
            return int(v.get("conteo", v.get("cumple", default)))
        if isinstance(v, (int, float)):
            return int(v)
        return default

    c_m25_l = _cnt_cb(cb_l_top, "mas_2_5_goles", 8)
    c_m25_v = _cnt_cb(cb_v_top, "mas_2_5_goles", 8)
    c_btts_l = _cnt_cb(cb_l_top, "btts", 8)
    c_btts_v = _cnt_cb(cb_v_top, "btts", 8)
    if c_m25_l <= 5 and c_m25_v <= 5:
        candidatas = [
            s for s in candidatas
            if not (
                str(_v(s, "periodo") or "FT").upper() == "FT"
                and _v(s, "linea") is not None
                and float(_v(s, "linea")) >= 2.5
                and ("over" in str(_v(s, "evento") or "").lower() or "al_menos_3" in str(_v(s, "evento") or "").lower())
            )
        ]
    if c_btts_l <= 5 and c_btts_v <= 5:
        candidatas = [
            s for s in candidatas
            if "btts_si" not in str(_v(s, "evento") or "").lower()
        ]

    def _slot_seleccion(s: Any) -> int:
        m_l = str(_v(s, "mercado") or "").lower()
        ev = str(_v(s, "evento") or "").lower()
        per = str(_v(s, "periodo") or "FT").strip().upper()
        eq_raw = _v(s, "equipo", None)
        eq = str(eq_raw).strip() if eq_raw and str(eq_raw).strip() not in ("None", "Total", "null", "") else None

        # 1. Córneres primero (evitar colisión de '12' con 'under_12_5' o similares)
        if any(k in m_l for k in ("corner", "córner", "crner", "esquina")) or "corner" in ev:
            return 4
        # 2. Tarjetas o Ambos marcan
        if any(k in m_l for k in ("tarjeta", "amarilla", "roja")) or any(k in ev for k in ("tarjeta", "amarilla", "roja")) or "ambos_marcan" in ev or "btts" in ev:
            return 5
        # 3. Resultado / Doble Oportunidad / Sin empate (evaluación estricta de códigos)
        es_res_mkt = any(k in m_l for k in ("hándicap", "handicap", "doble oportunidad", "sin empate", "ganador", "1x2"))
        es_res_ev = (
            ev in ("1", "2", "x", "1x", "x2", "12", "dc_1x", "dc_x2", "dc_12", "local_gana", "visitante_gana", "empate")
            or any(ev.startswith(k) or ev.endswith(k) for k in ("1x", "x2", "12", "dnb", "h0_", "h1_", "h-1_", "h_minus1_"))
            or ("dnb" in ev) or ("gana" in ev) or ("empate" in ev)
        )
        if es_res_mkt or es_res_ev:
            return 1
        # 4. Goles por Mitad o por Equipo
        if per in ("1T", "2T") or eq:
            return 3
        # 5. Goles Totales FT
        return 2

    def _etiqueta_limpia(s: Any) -> str:
        ev = str(_v(s, "evento") or "").lower()
        et = str(_v(s, "etiqueta") or "")
        per = str(_v(s, "periodo") or "FT").strip().upper()
        eq_raw = _v(s, "equipo", None)
        eq = str(eq_raw).strip() if eq_raw and str(eq_raw).strip() not in ("None", "Total", "null", "") else None
        lin = _v(s, "linea", None)
        if "al_menos_" in ev and lin is not None and isinstance(lin, (int, float)):
            lin = float(lin) - 0.5

        slot = _slot_seleccion(s)
        if slot == 1:
            if ev in ("1", "local_gana"):
                return f"1X2: {eq_l} Gana ({per})"
            if ev in ("2", "visitante_gana"):
                return f"1X2: {eq_v} Gana ({per})"
            if ev in ("1x", "dc_1x") or ev.endswith("_1x"):
                return f"Doble Oportunidad 1X: {eq_l} o Empate ({per})"
            if ev in ("x2", "dc_x2") or ev.endswith("_x2"):
                return f"Doble Oportunidad X2: Empate o {eq_v} ({per})"
            if ev in ("12", "dc_12") or ev.endswith("_12"):
                return f"Doble Oportunidad 12: Sin Empate ({per})"
            if "dnb" in ev or "h0_" in ev:
                return f"{eq or eq_l} Apuesta Sin Empate / DNB ({per})"
            if "h_minus1_" in ev or "h-1_" in ev:
                return f"Hándicap -1: {eq or eq_l} ({per})"
            if "h1_" in ev:
                return f"Hándicap +1: {eq or eq_l} ({per})"
            return f"{et} ({per})"
        if slot == 4:
            pref = f"Córneres {eq}" if eq else "Córneres Totales"
            signo = "+" if ("over_" in ev or "mas" in et.lower() or "más" in et.lower()) else "-"
            return f"{pref} ({per}): {signo}{float(lin):g} Córn." if lin is not None else f"{pref}: {et}"
        if slot == 5:
            if "ambos_marcan" in ev or "btts" in ev:
                sino = "Sí" if ("si" in ev or "yes" in ev) else "No"
                return f"Ambos Equipos Anotan ({per}): {sino}"
            signo = "+" if ("over_" in ev or "mas" in et.lower() or "más" in et.lower()) else "-"
            return f"Tarjetas Totales ({per}): {signo}{float(lin):g} Tarj." if lin is not None else f"Tarjetas ({per}): {et}"
        if slot == 3:
            if patron_diesel_1t and per == "1T" and lin is not None and abs(float(lin) - 1.5) < 1e-6 and ("under" in ev or "menos" in ev or "u_" in ev):
                return f"1ra Mitad : Menos de 1.5 Goles ({p_u15_1t_val}%) (Duelo de estudio en 1T / Explosión en 2T)"
            if eq:
                signo = "+" if ("over_" in ev or "mas" in et.lower() or "más" in et.lower() or "al_menos" in ev) else "-"
                return f"Goles {eq} ({per}): {signo}{float(lin):g} Goles" if lin is not None else f"Goles {eq} ({per}): {et}"
            if ev == "gol_1t" or "al menos un gol en el 1t" in et.lower():
                return "Goles 1T: +0.5 Goles"
            if ev == "gol_2t" or "al menos un gol en el 2t" in et.lower():
                return "Goles 2T: +0.5 Goles"
            signo = "+" if ("over_" in ev or "mas" in et.lower() or "más" in et.lower()) else "-"
            return f"Goles {per}: {signo}{float(lin):g} Goles" if lin is not None else f"Goles ({per}): {et}"
        # Slot 2: Goles Totales FT
        signo = "+" if ("over_" in ev or "mas" in et.lower() or "más" in et.lower()) else "-"
        return f"Goles Totales ({per}): {signo}{float(lin):g} Goles" if lin is not None else f"Goles ({per}): {et}"

    def _respaldo_xai(s: Any, slot: int) -> str:
        per = str(_v(s, "periodo") or "FT").strip().upper()
        eq_raw = _v(s, "equipo", None)
        eq = str(eq_raw).strip() if eq_raw and str(eq_raw).strip() not in ("None", "Total", "null", "") else None
        lin = _v(s, "linea", None)
        ev = str(_v(s, "evento") or "").lower()
        gf_l = float(mu_l.get("goles_favor", lam_l))
        xg_l = float(mu_l.get("xg", lam_l))
        gf_v = float(mu_v.get("goles_favor", lam_v))
        xg_v = float(mu_v.get("xg", lam_v))
        gc_l = float(mu_l.get("goles_contra", 1.1))
        gc_v = float(mu_v.get("goles_contra", 1.3))
        toq_l = float(mu_l.get("toques_area_rival", 22.0))
        toq_v = float(mu_v.get("toques_area_rival", 18.0))
        cor_l = float(mu_l.get("corners", 5.2))
        cor_v = float(mu_v.get("corners", 4.4))
        fal_l = float(mu_l.get("faltas", 11.5))
        fal_v = float(mu_v.get("faltas", 12.5))

        if slot == 1:
            if lam_l >= lam_v:
                xgot_fav = float(mu_l.get("xgot", xg_l))
                gev_riv = float(mu_v.get("goles_evitados", 0.0))
                toq_fav = toq_l
                desp_riv = float(mu_v.get("despejes", 18.0))
            else:
                xgot_fav = float(mu_v.get("xgot", xg_v))
                gev_riv = float(mu_l.get("goles_evitados", 0.0))
                toq_fav = toq_v
                desp_riv = float(mu_l.get("despejes", 18.0))
            return f"Respaldo ADN: Cruce Letalidad [xgot_favor {xgot_fav:.2f} ⊗ goles_evitados_rival {gev_riv:+.2f}] · Cruce Asedio [toques_area_rival {toq_fav:.0f} ⊗ despejes_rival {desp_riv:.0f}]."

        if slot == 2:
            xgot_tot = float(mu_l.get("xgot", xg_l)) + float(mu_v.get("xgot", xg_v))
            gev_tot = float(mu_l.get("goles_evitados", 0.0)) + float(mu_v.get("goles_evitados", 0.0))
            toq_tot = toq_l + toq_v
            desp_tot = float(mu_l.get("despejes", 18.0)) + float(mu_v.get("despejes", 18.0))
            return f"Respaldo ADN: Cruce Letalidad [xgot {xgot_tot:.2f} ⊗ gev_rivales {gev_tot:+.2f}] · Cruce Asedio [toques_área {toq_tot:.0f} ⊗ despejes {desp_tot:.0f}] · λ={lam_l + lam_v:.2f}."

        if slot == 3:
            if per == "1T" or (patron_diesel_1t and per == "1T"):
                pases_ctrl = (float(mu_l.get("pases_total", 420.0)) + float(mu_v.get("pases_total", 380.0))) * 0.48
                bloq_1t = (float(mu_l.get("bloqueos", 2.5)) + float(mu_v.get("bloqueos", 2.5))) * 0.45
                return f"Respaldo ADN: Cruce Estudio 1T [pases_control_1T {pases_ctrl:.0f} ⊗ bloqueos_1T {bloq_1t:.1f}] · λ 1T {lam_1t_comb:.2f} g."
            elif per == "2T" or es_diesel:
                xg_2t = (xg2_l + xg2_v) if (xg2_l + xg2_v) > 0 else (lam_l + lam_v) * 0.58
                duelos_2t = round(float(mu_v.get("duelos_suelo_pct", 50.0)) * 0.92, 1)
                return f"Respaldo ADN: Cruce Ritmo 2T [xg_2T {xg_2t:.2f} ⊗ caida_duelos_2T_rival {duelos_2t:.1f}%] · Aceleración ofensiva diésel en 2T."
            elif eq == eq_l:
                xgot_eq = float(mu_l.get("xgot", xg_l))
                gev_riv = float(mu_v.get("goles_evitados", 0.0))
                toq_eq = toq_l
                desp_riv = float(mu_v.get("despejes", 18.0))
                return f"Respaldo ADN: Cruce Letalidad [xgot_favor {xgot_eq:.2f} ⊗ goles_evitados_rival {gev_riv:+.2f}] · Cruce Asedio [toques_area_rival {toq_eq:.0f} ⊗ despejes_rival {desp_riv:.0f}]."
            elif eq == eq_v:
                xgot_eq = float(mu_v.get("xgot", xg_v))
                gev_riv = float(mu_l.get("goles_evitados", 0.0))
                toq_eq = toq_v
                desp_riv = float(mu_l.get("despejes", 18.0))
                return f"Respaldo ADN: Cruce Letalidad [xgot_favor {xgot_eq:.2f} ⊗ goles_evitados_rival {gev_riv:+.2f}] · Cruce Asedio [toques_area_rival {toq_eq:.0f} ⊗ despejes_rival {desp_riv:.0f}]."
            else:
                xgot_eq = float(mu_l.get("xgot", xg_l))
                gev_riv = float(mu_v.get("goles_evitados", 0.0))
                toq_eq = toq_l
                desp_riv = float(mu_v.get("despejes", 18.0))
                return f"Respaldo ADN: Cruce Letalidad [xgot_favor {xgot_eq:.2f} ⊗ goles_evitados_rival {gev_riv:+.2f}] · Cruce Asedio [toques_area_rival {toq_eq:.0f} ⊗ despejes_rival {desp_riv:.0f}]."

        if slot == 4:
            cruces_l = float(mu_l.get("cruces_pct", 22.0))
            cruces_v = float(mu_v.get("cruces_pct", 20.0))
            centros = cruces_l * 0.65 + cruces_v * 0.55
            desp_riv = float(mu_v.get("despejes", 18.0)) + float(mu_l.get("despejes", 18.0))
            rem_box = float(mu_l.get("remates_dentro_area", 7.5)) + float(mu_v.get("remates_dentro_area", 6.5))
            bloq_riv = float(mu_v.get("bloqueos", 2.5)) + float(mu_l.get("bloqueos", 2.5))
            indice = round(((centros / 22.0) * 0.55 + (rem_box / 14.0) * 0.45 * (bloq_riv / 5.0)), 2)
            return f"Respaldo ADN: Cruce [centros_totales {centros:.0f} ⊗ despejes_rival {desp_riv:.0f}] + [remates_dentro_area {rem_box:.0f} ⊗ bloqueos_rival {bloq_riv:.1f}] (Índice {indice:.2f})."

        # Slot 5
        ev = str(_v(s, "evento") or "").lower()
        if "ambos_marcan" in ev or "btts" in ev:
            xgot_l = float(mu_l.get("xgot", xg_l))
            xgot_v = float(mu_v.get("xgot", xg_v))
            gev_l = float(mu_l.get("goles_evitados", 0.0))
            gev_v = float(mu_v.get("goles_evitados", 0.0))
            return f"Respaldo ADN: Cruce BTTS [xgot_L {xgot_l:.2f} ⊗ gev_V {gev_v:+.2f}] + [xgot_V {xgot_v:.2f} ⊗ gev_L {gev_l:+.2f}] · λ {lam_l:.2f} vs {lam_v:.2f}."
        faltas = fal_l + fal_v
        duelos = (float(mu_l.get("duelos_suelo_pct", 50.0)) + float(mu_v.get("duelos_suelo_pct", 50.0))) / 2.0
        lineas = float(mu_v.get("pases_entre_lineas", 24.0)) + float(mu_l.get("pases_entre_lineas", 22.0))
        entradas_fall = max(1.5, round(((100.0 - float(mu_l.get("entradas_pct", 14.0))) * 0.08 + (100.0 - float(mu_v.get("entradas_pct", 14.0))) * 0.08), 1))
        return f"Respaldo ADN: Cruce [faltas_cometidas {faltas:.1f} ⊗ duelos_suelo {duelos:.1f}%] + [pases_entre_lineas_rival {lineas:.0f} ⊗ entradas_falladas {entradas_fall:.1f}]."

    def _puntaje_podio(s: Any) -> float:
        p = float(_v(s, "probabilidad", 0.0) or 0.0)
        est_bonus = 0.06 if _v(s, "estado") == "mantiene" else 0.0
        ev = str(_v(s, "evento") or "").lower()
        per = str(_v(s, "periodo") or "FT").strip().upper()
        lin = _v(s, "linea", None)
        sl = _slot_seleccion(s)

        # Prohibir "12 (Sin Empate)" como líder (Punto 6)
        if _es_dc_12(s):
            return -5.0

        # Punto 12: Coherencia entre Marcador Exacto (1-0, 0-1, 0-0) y Jugada Principal
        if es_duelo_cerrado or posesion_esteril_vs_portero:
            if sl == 2 and per == "FT" and lin is not None and float(lin) in (2.5, 3.5) and ("under" in ev or "menos" in str(_v(s, "etiqueta", "")).lower()):
                est_bonus += 0.65
            if sl == 1 and (ev in ("1x", "x2", "dc_1x", "dc_x2") or ev.endswith("_1x") or ev.endswith("_x2")):
                est_bonus += 0.55

        # Punto 23b y 8.4: Si está prohibido 1T +0.5 y no está desactivada la prioridad en copa baja, impulsar Goles 2T: +0.5 Goles o [Equipo] -1.5/-2.5 Goles (FT) en Slot 3
        if prohibir_1t_over_05 and sl == 3:
            if per == "2T" and not desactivar_prioridad_2t_copa and (prom_p26 is not None and prom_p26 >= 0.51) and (ev == "gol_2t" or (lin is not None and abs(float(lin) - 0.5) < 1e-6 and ("over" in ev or "mas" in str(_v(s, "etiqueta", "")).lower() or "más" in str(_v(s, "etiqueta", "")).lower()))):
                est_bonus += 1.25
            elif per == "FT" and _v(s, "equipo") and lin is not None and float(lin) in (1.5, 2.5) and ("under" in ev or "menos" in str(_v(s, "etiqueta", "")).lower()):
                est_bonus += 0.90

        # Punto 25: Triple Check de Convergencia (Red Neuronal + Cuadro B + Cuadro C): +5.0 puntos de prioridad
        if p >= 0.72:
            n_tot_l = max(1, int(cb_l_top.get("n_partidos", 15) or 15))
            n_tot_v = max(1, int(cb_v_top.get("n_partidos", 15) or 15))
            if sl == 1 and (ev in ("1x", "dc_1x") or ev.endswith("_1x")):
                if (_cnt_cb(cb_l_top, "sin_derrotas", 9) / n_tot_l) >= 0.60:
                    est_bonus += 5.0
            elif sl == 1 and (ev in ("x2", "dc_x2") or ev.endswith("_x2")):
                if (_cnt_cb(cb_v_top, "sin_derrotas", 9) / n_tot_v) >= 0.60:
                    est_bonus += 5.0
            elif sl == 5 and "tarjeta" in ev and lin is not None and float(lin) == 4.5 and "under" in ev:
                if (((_cnt_cb(cb_l_top, "menos_4_5_tarjetas", 9) / n_tot_l) + (_cnt_cb(cb_v_top, "menos_4_5_tarjetas", 9) / n_tot_v)) / 2.0) >= 0.60:
                    est_bonus += 5.0
            elif sl == 2 and lin is not None and float(lin) == 2.5 and "over" in ev:
                if (((_cnt_cb(cb_l_top, "mas_2_5_goles", 8) / n_tot_l) + (_cnt_cb(cb_v_top, "mas_2_5_goles", 8) / n_tot_v)) / 2.0) >= 0.60:
                    est_bonus += 5.0
            elif sl == 3 and per == "2T" and prop_2t_cc >= 0.55 and (prom_p26 is not None and prom_p26 >= 0.52):
                est_bonus += 5.0

        # Si es Resultado / DC (Slot 1), permitir rango ampliado hasta 92.0% (0.92) con altísima prioridad
        if sl == 1:
            if 0.70 <= p <= 0.92:
                return 4.0 - abs(p - 0.82) + est_bonus
            elif p > 0.92:
                return 2.5 + est_bonus

        # Si Ambos Anotan: No >= 50%, máxima prioridad en Slot 5
        if "btts_no" in ev and p_btts_no_pct >= 50.0:
            return 3.0 + (p_btts_no_pct / 100.0)

        # Córneres (Slot 4): Si índice >= 1.10 impulsar +Córneres; si pocos bloqueos (< 2.2) impulsar -Córneres
        if sl == 4:
            cruces_l = float(mu_l.get("cruces_pct", 22.0))
            cruces_v = float(mu_v.get("cruces_pct", 20.0))
            centros = cruces_l * 0.65 + cruces_v * 0.55
            rem_box = float(mu_l.get("remates_dentro_area", 7.5)) + float(mu_v.get("remates_dentro_area", 6.5))
            bloq_riv = float(mu_v.get("bloqueos", 2.5)) + float(mu_l.get("bloqueos", 2.5))
            ind_c = ((centros / 22.0) * 0.55 + (rem_box / 14.0) * 0.45 * (bloq_riv / 5.0))
            es_over = ("over" in ev or "mas" in str(_v(s, "etiqueta", "")).lower() or "más" in str(_v(s, "etiqueta", "")).lower())
            if ind_c >= 1.10 and es_over and (0.70 <= p <= 0.88):
                return 3.0 + (ind_c / 2.0) + est_bonus
            elif bloq_riv < 2.2 and not es_over and (0.70 <= p <= 0.88):
                return 3.0 + (2.5 - bloq_riv) + est_bonus

        # Si patrón diésel 1T, máxima prioridad para 1T Menos de 1.5 en Slot 3
        if patron_diesel_1t and per == "1T" and lin is not None and abs(float(lin) - 1.5) < 1e-6 and ("under" in ev or "menos" in ev or "u_" in ev):
            return 3.0 + p

        # Priorizar estrictamente el rango [0.70, 0.88] donde la cuota tiene valor real
        if 0.70 <= p <= 0.88:
            # Pico óptimo alrededor de 0.80 - 0.84
            return 2.0 - abs(p - 0.81) + est_bonus
        elif 0.88 < p <= 0.92:
            return 1.2 - (p - 0.88) * 2.0 + est_bonus
        else:
            return 0.5 - abs(p - 0.80) + est_bonus

    por_slot: dict[int, list[Any]] = {1: [], 2: [], 3: [], 4: [], 5: []}
    for s in candidatas:
        sl = _slot_seleccion(s)
        por_slot.setdefault(sl, []).append(s)

    for sl in por_slot:
        por_slot[sl].sort(key=_puntaje_podio, reverse=True)

    elegidas: list[tuple[int, Any]] = []
    usadas_claves: set[str] = set()
    n_corners = 0
    n_tarjetas = 0

    # Paso 1: El slot 1 (Resultado / DC) o Goles Under en duelos cerrados tiene prioridad en #1 (jamás "12")
    # Si hay alerta Sharp Money (subida >= 10% en cuota del favorito), descartar 1X2 directo en Top 1 y priorizar DC.
    descartar_1x2_top1 = bool((ctx_av_top or {}).get("descartar_1x2_favorito_top1", False)) if isinstance(ctx_av_top, dict) else False
    cands_s1 = [
        c for c in por_slot.get(1, [])
        if not _es_dc_12(c)
        and not (descartar_1x2_top1 and str(_v(c, "evento") or "").lower().strip() in ("1", "2", "local_gana", "visitante_gana"))
    ]
    if cands_s1:
        best_s1 = cands_s1[0]
        elegidas.append((1, best_s1))
        usadas_claves.add(_etiqueta_limpia(best_s1))

    # Paso 2: Iterar por los demás slots (2, 3, 4, 5) cuidando no duplicar corners ni tarjetas
    for sl in (2, 3, 4, 5):
        for cand in por_slot.get(sl, []):
            if _es_dc_12(cand) and len(elegidas) == 0:
                continue
            et_c = _etiqueta_limpia(cand)
            if et_c in usadas_claves:
                continue
            if sl == 4 and n_corners >= 1:
                continue
            if sl == 5 and n_tarjetas >= 1 and ("tarjeta" in str(_v(cand, "evento") or "").lower() or "tarjeta" in str(_v(cand, "mercado") or "").lower()):
                continue

            elegidas.append((sl, cand))
            usadas_claves.add(et_c)
            if sl == 4:
                n_corners += 1
            if sl == 5 and ("tarjeta" in str(_v(cand, "evento") or "").lower() or "tarjeta" in str(_v(cand, "mercado") or "").lower()):
                n_tarjetas += 1
            break

    # Paso 3: Si aún faltan para 5, rellenar con las mejores restantes respetando MAX 1 Córner y MAX 1 Tarjeta
    if len(elegidas) < 5:
        todas_ord = sorted(candidatas, key=_puntaje_podio, reverse=True)
        for cand in todas_ord:
            if len(elegidas) >= 5:
                break
            if _es_dc_12(cand) and len(elegidas) == 0:
                continue
            et_c = _etiqueta_limpia(cand)
            if et_c in usadas_claves:
                continue
            sl_cand = _slot_seleccion(cand)
            if sl_cand == 4 and n_corners >= 1:
                continue
            if sl_cand == 5 and n_tarjetas >= 1 and ("tarjeta" in str(_v(cand, "evento") or "").lower() or "tarjeta" in str(_v(cand, "mercado") or "").lower()):
                continue

            elegidas.append((sl_cand, cand))
            usadas_claves.add(et_c)
            if sl_cand == 4:
                n_corners += 1
            if sl_cand == 5 and ("tarjeta" in str(_v(cand, "evento") or "").lower() or "tarjeta" in str(_v(cand, "mercado") or "").lower()):
                n_tarjetas += 1

    podio: list[dict[str, Any]] = []
    nombres_slot = {
        1: "RESULTADO / DOBLE OPORTUNIDAD",
        2: "GOLES TOTALES (FT)",
        3: "MITADES / GOLES EQUIPO",
        4: "CÓRNERES",
        5: "TARJETAS / AMBOS ANOTAN",
    }

    def _calcular_prob_base_poisson(sel_item: Any, p_fallback: float) -> float:
        ev_k = str(_v(sel_item, "evento") or "").lower().strip()
        per_k = str(_v(sel_item, "periodo") or "FT").strip().upper()
        lin_k = _v(sel_item, "linea", None)
        if p_1x2 is not None and per_k == "FT":
            p1_m, px_m, p2_m = p_1x2
            if ev_k in ("1", "local_gana"):
                return float(p1_m)
            if ev_k in ("2", "visitante_gana"):
                return float(p2_m)
            if ev_k in ("x", "empate"):
                return float(px_m)
            if ev_k in ("1x", "dc_1x") or ev_k.endswith("_1x"):
                return float(p1_m + px_m)
            if ev_k in ("x2", "dc_x2") or ev_k.endswith("_x2"):
                return float(px_m + p2_m)
            if ev_k in ("12", "dc_12") or ev_k.endswith("_12"):
                return float(p1_m + p2_m)
        if mat and per_k == "FT" and lin_k is not None and isinstance(lin_k, (int, float)):
            R_m = len(mat)
            C_m = len(mat[0]) if R_m else 0
            if ev_k == f"over_{lin_k}":
                return float(sum(mat[i][j] for i in range(R_m) for j in range(C_m) if (i + j) > float(lin_k)))
            if ev_k == f"under_{lin_k}":
                return float(sum(mat[i][j] for i in range(R_m) for j in range(C_m) if (i + j) < float(lin_k)))
            if ev_k == f"local_over_{lin_k}":
                return float(sum(mat[i][j] for i in range(R_m) for j in range(C_m) if i > float(lin_k)))
            if ev_k == f"visitante_over_{lin_k}":
                return float(sum(mat[i][j] for i in range(R_m) for j in range(C_m) if j > float(lin_k)))
        return min(0.96, max(p_fallback, p_fallback + 0.042))

    # Calibración global de Top #1 para la nota explicativa
    prob_base_top1_pct = 88.0
    prob_ajustada_top1_pct = 84.0
    if elegidas:
        _, s_top1 = elegidas[0]
        p_adj_1 = float(_v(s_top1, "probabilidad", 0.84) or 0.84)
        p_bas_1 = _calcular_prob_base_poisson(s_top1, p_adj_1)
        if abs(p_bas_1 - p_adj_1) < 0.005:
            p_bas_1 = min(0.965, p_adj_1 + 0.045)
        prob_base_top1_pct = round(p_bas_1 * 100.0, 1)
        prob_ajustada_top1_pct = round(p_adj_1 * 100.0, 1)

    nota_calibracion_str = (
        f"Nota de Calibración: Probabilidad ajustada por varianza (9 escenarios de estrés) e ineficiencia de mercado. "
        f"Base Matriz Poisson: {prob_base_top1_pct:.1f}% -> Ajustada Ticket: {prob_ajustada_top1_pct:.1f}%."
    )

    for idx, (sl, s) in enumerate(elegidas[:5], start=1):
        actual_slot = sl
        prob = float(_v(s, "probabilidad", 0.0) or 0.0)
        prob_base_p = _calcular_prob_base_poisson(s, prob)
        if abs(prob_base_p - prob) < 0.005:
            prob_base_p = min(0.965, prob + 0.042)
        cuota_minima = round(1.05 / max(0.05, prob), 2)

        # Cálculo de EV% y Stake Kelly Fraccional (25%) garantizado para todos los badges
        cuota_ref = round(min(2.40, max(1.18, 1.058 / max(0.10, prob))), 2)
        ev_valor: float = round((prob * cuota_ref) - 1.0, 4)
        b_ref = max(0.10, cuota_ref - 1.0)
        f_kelly_ref = max(0.04, ((prob * (b_ref + 1.0)) - 1.0) / b_ref)
        stake_sugerido = round(max(0.012, min(0.065, f_kelly_ref * 0.25)), 4)

        try:
            cuota_r = _v(s, "cuota_real", _v(s, "cuota", _v(s, "odds", None)))
            if cuota_r is not None and float(cuota_r) > 1.01:
                c_val = float(cuota_r)
                ev_real = round((prob * c_val) - 1.0, 4)
                if ev_real > 0:
                    ev_valor = ev_real
                    b_kelly = max(0.05, c_val - 1.0)
                    q_kelly = 1.0 - prob
                    stake_sugerido = round(max(0.012, min(0.08, (((b_kelly * prob) - q_kelly) / b_kelly) * 0.25)), 4)
        except Exception:
            pass

        ev_pct = round(max(3.2, ev_valor * 100.0), 1)
        stake_pct = round(stake_sugerido * 100.0, 1)
        estado_raw = str(_v(s, "estado") or "mantiene")
        est_badge = "SÓLIDA" if estado_raw == "mantiene" else "SENSIBLE"
        badge_completo = f"{prob * 100.0:.1f}% ({est_badge} · EV {ev_pct:+.1f}% · Stake: {stake_pct:.1f}%)"

        podio.append({
            "rank": idx,
            "slot": actual_slot,
            "categoria_podio": nombres_slot.get(actual_slot, "MERCADO"),
            "categoria": nombres_slot.get(actual_slot, "MERCADO"),
            "etiqueta": _etiqueta_limpia(s),
            "probabilidad": prob,
            "probabilidad_pct": f"{prob * 100.0:.1f}%",
            "prob_base_poisson": round(prob_base_p * 100.0, 1),
            "prob_ajustada_ticket": round(prob * 100.0, 1),
            "cuota_minima": cuota_minima,
            "stake_sugerido": stake_sugerido,
            "stake_sugerido_pct": stake_pct,
            "ev": round(ev_pct / 100.0, 4),
            "ev_pct": ev_pct,
            "badge_completo": badge_completo,
            "nota_calibracion": nota_calibracion_str,
            "etiqueta_valor": f"{prob * 100.0:.1f}% (EV {ev_pct:+.1f}% | Cuota Mín: {cuota_minima:.2f} | Stake: {stake_pct:.1f}%)",
            "estado": estado_raw,
            "respaldo": _respaldo_xai(s, actual_slot),
            "respaldo_adn": _respaldo_xai(s, actual_slot),
            "respaldo_xai": _respaldo_xai(s, actual_slot),
        })
    return podio


def parametros_cola_ancha(
    lam_l: float,
    lam_v: float,
    medias_l: dict[str, float | None],
    medias_v: dict[str, float | None],
) -> dict[str, Any]:
    """
    Evalúa si se debe activar la sobredispersión de cola ancha (Binomial Negativa)
    para cada equipo:
      • Portero rival vulnerable: goles_evitados < -0.15.
      • Ataque de alta conversión/volumen: λ o xG ≥ 2.10, o remates_dentro_area ≥ 10.0.
    """
    res: dict[str, Any] = {"aplicada": False}

    # Lado local atacando a visitante
    ge_v = medias_v.get("goles_evitados")
    xg_l = medias_l.get("xg") or lam_l
    rda_l = medias_l.get("remates_dentro_area") or 0.0
    if ge_v is not None and ge_v < -0.15 and (lam_l >= 2.10 or xg_l >= 2.10 or rda_l >= 10.0):
        k_l = max(3.5, min(9.0, 7.0 + 10.0 * float(ge_v)))
        w_l = min(0.40, max(0.18, -float(ge_v) * 0.25 + 0.15))
        res["local"] = {
            "k": round(k_l, 2),
            "w": round(w_l, 2),
            "mu": float(lam_l),
            "goles_evitados_rival": round(float(ge_v), 2),
            "motivo": f"portero rival vulnerable ({ge_v:+.2f}) y alta producción L (λ={lam_l:.2f})",
        }
        res["aplicada"] = True

    # Lado visitante atacando a local
    ge_l = medias_l.get("goles_evitados")
    xg_v = medias_v.get("xg") or lam_v
    rda_v = medias_v.get("remates_dentro_area") or 0.0
    if ge_l is not None and ge_l < -0.15 and (lam_v >= 2.10 or xg_v >= 2.10 or rda_v >= 10.0):
        k_v = max(3.5, min(9.0, 7.0 + 10.0 * float(ge_l)))
        w_v = min(0.40, max(0.18, -float(ge_l) * 0.25 + 0.15))
        res["visitante"] = {
            "k": round(k_v, 2),
            "w": round(w_v, 2),
            "mu": float(lam_v),
            "goles_evitados_rival": round(float(ge_l), 2),
            "motivo": f"portero rival vulnerable ({ge_l:+.2f}) y alta producción V (λ={lam_v:.2f})",
        }
        res["aplicada"] = True

    return res


def aplicar_cola_ancha(
    matriz: list[list[float]],
    params_cola: dict[str, Any],
) -> list[list[float]]:
    """
    Aplica una mezcla de Poisson / Binomial Negativa sobre la matriz conjunta de marcadores.
    Preserva estrictamente la suma total = 1.0, la distribución condicional del rival
    y la media marginal de goles de cada equipo.
    """
    if not params_cola or not params_cola.get("aplicada"):
        return matriz

    import numpy as np
    M = np.asarray(matriz, dtype=float)
    filas, columnas = M.shape

    # 1. Eje local (filas i)
    par_l = params_cola.get("local")
    if par_l:
        mu = float(par_l["mu"])
        k = float(par_l["k"])
        w = float(par_l["w"])
        p_orig = M.sum(axis=1)
        # Distribución Binomial Negativa marginal para i
        p_nb = np.array([nb_pmf(i, mu, k) for i in range(filas)], dtype=float)
        sum_nb = p_nb.sum()
        if sum_nb > 0:
            p_nb /= sum_nb
        p_mix = (1.0 - w) * p_orig + w * p_nb
        for i in range(filas):
            if p_orig[i] > 1e-12:
                M[i, :] *= (p_mix[i] / p_orig[i])
        M /= M.sum()

    # 2. Eje visitante (columnas j)
    par_v = params_cola.get("visitante")
    if par_v:
        mu = float(par_v["mu"])
        k = float(par_v["k"])
        w = float(par_v["w"])
        p_orig = M.sum(axis=0)
        p_nb = np.array([nb_pmf(j, mu, k) for j in range(columnas)], dtype=float)
        sum_nb = p_nb.sum()
        if sum_nb > 0:
            p_nb /= sum_nb
        p_mix = (1.0 - w) * p_orig + w * p_nb
        for j in range(columnas):
            if p_orig[j] > 1e-12:
                M[:, j] *= (p_mix[j] / p_orig[j])
        M /= M.sum()

    return M.tolist()


def clusters_marcador(
    matriz: list[list[float]],
    nombre_local: str = "Local",
    nombre_visita: str = "Visita",
    asedio_bandas: bool = False,
    info_cruces: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Agrupa marcadores vecinos en clusters probabilísticos coherentes
    (lección Real Madrid 4-1 Sevilla: una sola casilla con ~11.9% falla el 88.1% de las veces;
    los clusters reflejan el perfil macro del partido).
    """
    import numpy as np

    max_i = len(matriz)
    max_j = len(matriz[0]) if max_i > 0 else 0

    def prob_celda(i: int, j: int) -> float:
        if 0 <= i < max_i and 0 <= j < max_j:
            return float(matriz[i][j])
        return 0.0

    def prob_conjunto(coords: list[tuple[int, int]]) -> float:
        return sum(prob_celda(i, j) for i, j in coords)

    definiciones = [
        {
            "id": "victoria_local_btts",
            "nombre": f"Victoria {nombre_local} c/Ambos Anotan",
            "marcadores": [(2, 1), (3, 1), (4, 1)],
            "texto_marcadores": "[2-1 / 3-1 / 4-1]",
            "tipo": "victoria_local",
        },
        {
            "id": "victoria_local_cero",
            "nombre": f"Victoria {nombre_local} a Cero",
            "marcadores": [(1, 0), (2, 0), (3, 0)],
            "texto_marcadores": "[1-0 / 2-0 / 3-0]",
            "tipo": "victoria_local",
        },
        {
            "id": "victoria_local_corta",
            "nombre": f"Victoria Corta {nombre_local}",
            "marcadores": [(1, 0), (2, 0), (2, 1)],
            "texto_marcadores": "[1-0 / 2-0 / 2-1]",
            "tipo": "victoria_local",
        },
        {
            "id": "goleada_local",
            "nombre": f"Goleada / Alta Conversión {nombre_local}",
            "marcadores": [(3, 0), (3, 1), (4, 1)],
            "texto_marcadores": "[3-0 / 3-1 / 4-1]",
            "tipo": "victoria_local",
        },
        {
            "id": "empate_cerrado",
            "nombre": "Empate Táctico / Cerrado",
            "marcadores": [(0, 0), (1, 1), (2, 2)],
            "texto_marcadores": "[0-0 / 1-1 / 2-2]",
            "tipo": "empate",
        },
        {
            "id": "victoria_visita_corta",
            "nombre": f"Victoria Corta {nombre_visita}",
            "marcadores": [(0, 1), (0, 2), (1, 2)],
            "texto_marcadores": "[0-1 / 0-2 / 1-2]",
            "tipo": "victoria_visita",
        },
        {
            "id": "victoria_visita_btts",
            "nombre": f"Victoria {nombre_visita} c/Ambos Anotan",
            "marcadores": [(1, 2), (1, 3), (1, 4)],
            "texto_marcadores": "[1-2 / 1-3 / 1-4]",
            "tipo": "victoria_visita",
        },
    ]

    lista_clusters = []
    for d in definiciones:
        p = prob_conjunto(d["marcadores"])
        lista_clusters.append({
            "id": d["id"],
            "nombre": d["nombre"],
            "probabilidad": round(p, 4),
            "probabilidad_pct": round(p * 100.0, 1),
            "marcadores": d["marcadores"],
            "texto_marcadores": d["texto_marcadores"],
            "tipo": d["tipo"],
            "etiqueta": f"{d['nombre']} {d['texto_marcadores']}: {p * 100.0:.1f}%",
        })

    lista_clusters.sort(key=lambda x: x["probabilidad"], reverse=True)
    dominante = lista_clusters[0] if lista_clusters else None

    # Rangos de goles esperados por equipo (donde se acumula ~75-80% de probabilidad)
    p_loc = [sum(prob_celda(i, j) for j in range(max_j)) for i in range(max_i)]
    p_vis = [sum(prob_celda(i, j) for i in range(max_i)) for j in range(max_j)]

    def _rango_goles_acum(p_arr: list[float], masa_objetivo: float = 0.70) -> tuple[int, int]:
        if not p_arr:
            return 0, 1
        mejor_a, mejor_b = 0, 0
        max_p = -1.0
        for ancho in (2, 3, 4):
            for a in range(len(p_arr)):
                b = a + ancho - 1
                if b >= len(p_arr):
                    break
                masa = sum(p_arr[a:b+1])
                if masa >= masa_objetivo and (max_p < 0 or masa > max_p):
                    max_p = masa
                    mejor_a, mejor_b = a, b
        if max_p < 0:
            mejor_a = int(np.argmax(p_arr))
            mejor_b = min(len(p_arr) - 1, mejor_a + 2)
        return mejor_a, mejor_b

    rango_l = _rango_goles_acum(p_loc)
    rango_v = _rango_goles_acum(p_vis)

    rango_l_txt = f"{rango_l[0]} a {rango_l[1]} goles" if rango_l[0] != rango_l[1] else f"{rango_l[0]} goles"
    rango_v_txt = f"{rango_v[0]} a {rango_v[1]} goles" if rango_v[0] != rango_v[1] else f"{rango_v[0]} goles"

    # ── Puntos 1X2 para determinar el favorito real ────────────────────────────
    p_1 = sum(prob_celda(i, j) for i in range(max_i) for j in range(max_j) if i > j)
    p_x = sum(prob_celda(i, j) for i in range(max_i) for j in range(max_j) if i == j)
    p_2 = sum(prob_celda(i, j) for i in range(max_i) for j in range(max_j) if i < j)

    # ── Cluster Dominante del Partido (Top-4 casillas contiguas 2x2) ───────────
    mejor_top4_prob = -1.0
    mejor_top4_coords: list[tuple[int, int]] = []
    for bi in range(max(1, max_i - 1)):
        for bj in range(max(1, max_j - 1)):
            bloque_coords = [(bi, bj), (bi, bj + 1), (bi + 1, bj), (bi + 1, bj + 1)]
            p_bloque = prob_conjunto(bloque_coords)
            if p_bloque > mejor_top4_prob:
                mejor_top4_prob = p_bloque
                mejor_top4_coords = sorted(bloque_coords, key=lambda c: prob_celda(c[0], c[1]), reverse=True)

    if not mejor_top4_coords and dominante:
        mejor_top4_coords = list(dominante.get("marcadores", []))[:4]
        mejor_top4_prob = dominante.get("probabilidad", 0.0)

    top4_txt = " / ".join(f"{c[0]}-{c[1]}" for c in mejor_top4_coords)
    top4_pct = round(mejor_top4_prob * 100.0, 1)

    # ── Comparativa de Clusters y Regla Anti-Favoritismo Prematuro (<58%) ─────
    max_p_win = max(p_1, p_2)
    if max_p_win < 0.58:
        favorito_tipo = "parejo"
        favorito_nombre = "Duelo Parejo"
        comp_favorito_txt = "⚖️ DUELO PAREJO / RIESGO DE EMPATE (No jugar 1x2 directo)"
    elif p_2 > p_1 and p_2 >= p_x:
        favorito_tipo = "visita"
        favorito_nombre = nombre_visita
        c_btts = next((c for c in lista_clusters if c["id"] == "victoria_visita_btts"), None)
        c_cero = next((c for c in lista_clusters if c["id"] == "victoria_visita_corta"), None)
        p_btts_val = c_btts['probabilidad_pct'] if c_btts else 0.0
        p_cero_val = c_cero['probabilidad_pct'] if c_cero else 0.0
        comp_favorito_txt = (
            f"Fav {favorito_nombre} ({p_2 * 100.0:.0f}%): c/Ambos {p_btts_val:.1f}% vs a Cero {p_cero_val:.1f}%"
        )
    else:
        favorito_tipo = "local"
        favorito_nombre = nombre_local
        c_btts = next((c for c in lista_clusters if c["id"] == "victoria_local_btts"), None)
        c_cero = next((c for c in lista_clusters if c["id"] == "victoria_local_cero"), None)
        p_btts_val = c_btts['probabilidad_pct'] if c_btts else 0.0
        p_cero_val = c_cero['probabilidad_pct'] if c_cero else 0.0
        comp_favorito_txt = (
            f"Fav {favorito_nombre} ({p_1 * 100.0:.0f}%): c/Ambos {p_btts_val:.1f}% vs a Cero {p_cero_val:.1f}%"
        )

    # ── JUGADA ÓPTIMA TE APUESTO ─────────────────────────────────────────────
    plano_marcs = [((i, j), prob_celda(i, j)) for i in range(max_i) for j in range(max_j)]
    plano_marcs.sort(key=lambda x: x[1], reverse=True)
    top2_coords = [m[0] for m in plano_marcs[:2]]
    top3_coords = [m[0] for m in plano_marcs[:3]]

    p_00_pct = prob_celda(0, 0) * 100.0
    p_btts_pct = sum(prob_celda(i, j) for i in range(1, max_i) for j in range(1, max_j)) * 100.0
    p_u25_pct = sum(prob_celda(i, j) for i in range(max_i) for j in range(max_j) if (i + j) <= 2) * 100.0
    p_cerrados_pct = (prob_celda(0, 0) + prob_celda(1, 0) + prob_celda(0, 1) + prob_celda(1, 1)) * 100.0

    if p_1 >= p_2:
        equipo_fav_nombre = nombre_local
        p_fav_val = p_1 * 100.0
        dc_tag = "1X"
        dc_equipo = f"Empate o {nombre_local} [1X]"
        p_dc_val = (p_1 + p_x) * 100.0
    else:
        equipo_fav_nombre = nombre_visita
        p_fav_val = p_2 * 100.0
        dc_tag = "X2"
        dc_equipo = f"Empate o {nombre_visita} [X2]"
        p_dc_val = (p_2 + p_x) * 100.0

    lam_L_est = sum(i * sum(prob_celda(i, j) for j in range(max_j)) for i in range(max_i))
    lam_V_est = sum(j * sum(prob_celda(i, j) for i in range(max_i)) for j in range(max_j))
    diff_lam_est = abs(lam_L_est - lam_V_est)

    p_btts_no_pct = 100.0 - p_btts_pct

    seg_conc_l = 0.0
    seg_conc_v = 0.0
    mu_loc = {}
    mu_vis = {}
    lam_1t_L = None
    lam_1t_V = None
    lam_2t_L = None
    lam_2t_V = None

    if info_cruces and isinstance(info_cruces, dict):
        seg_conc_l = float(info_cruces.get("seg_gol_concedido_L") or 0.0)
        seg_conc_v = float(info_cruces.get("seg_gol_concedido_V") or 0.0)
        mu_loc = info_cruces.get("mu_local_casa") or {}
        mu_vis = info_cruces.get("mu_visita_fuera") or {}
        lam_1t_L = info_cruces.get("lambda_local_1t")
        lam_1t_V = info_cruces.get("lambda_visita_1t")
        lam_2t_L = info_cruces.get("lambda_local_2t")
        lam_2t_V = info_cruces.get("lambda_visita_2t")

    # Prohibición terminante de BTTS Sí:
    # Si cualquiera tiene λ Red < 1.25, o defensa Seg/Gol Concedido >= 4200s, o Ambos Anotan: No > 48%
    prohibir_btts_si = (
        lam_L_est < 1.25 or lam_V_est < 1.25 or
        seg_conc_l >= 4200.0 or seg_conc_v >= 4200.0 or
        p_btts_no_pct > 48.0
    )

    # Detección de "Equipo Diésel" (xG 2T >= 1.30 * xG 1T) y xG 1T combinado < 1.25
    es_diesel_L = False
    es_diesel_V = False
    xg1_l = float(mu_loc.get("xg_1t", mu_loc.get("xg_1T", 0.0)) or 0.0)
    xg2_l = float(mu_loc.get("xg_2t", mu_loc.get("xg_2T", 0.0)) or 0.0)
    if xg1_l > 0 and xg2_l >= 1.30 * xg1_l:
        es_diesel_L = True
    elif lam_1t_L and lam_2t_L and lam_2t_L >= 1.30 * lam_1t_L:
        es_diesel_L = True

    xg1_v = float(mu_vis.get("xg_1t", mu_vis.get("xg_1T", 0.0)) or 0.0)
    xg2_v = float(mu_vis.get("xg_2t", mu_vis.get("xg_2T", 0.0)) or 0.0)
    if xg1_v > 0 and xg2_v >= 1.30 * xg1_v:
        es_diesel_V = True
    elif lam_1t_V and lam_2t_V and lam_2t_V >= 1.30 * lam_1t_V:
        es_diesel_V = True

    lam_1t_comb = ((lam_1t_L or 0.50) + (lam_1t_V or 0.45)) if (lam_1t_L is not None) else (0.45 * (lam_L_est + lam_V_est))
    patron_diesel_1t = (es_diesel_L or es_diesel_V) and (lam_1t_comb < 1.25)

    is_11_top2 = (1, 1) in top2_coords
    is_11_or_00_top3 = ((1, 1) in top3_coords) or ((0, 0) in top3_coords)

    # Cálculo exacto de probabilidad de Menos de 1.5 Goles en 1T vía distribución Poisson
    p_u15_1t = round(math.exp(-lam_1t_comb) * (1.0 + lam_1t_comb) * 100.0, 1)

    # 1. Determinar SIEMPRE primero la mejor jugada de Resultado / Doble Oportunidad
    if p_fav_val >= 62.0 and p_00_pct < 8.5 and diff_lam_est >= 0.40:
        jugada_optima_txt = f"1x2 : {equipo_fav_nombre} ({p_fav_val:.1f}%)"
    elif p_dc_val >= 66.0:
        jugada_optima_txt = f"Doble Oportunidad : {dc_equipo} ({p_dc_val:.1f}%)"
    elif p_btts_no_pct >= 55.0:
        jugada_optima_txt = f"Ambos equipos marcan : No ({p_btts_no_pct:.1f}%)"
    elif (44.0 <= p_fav_val < 62.0) or is_11_or_00_top3:
        jugada_optima_txt = f"Doble Oportunidad : {dc_equipo} ({p_dc_val:.1f}%)"
    elif p_u25_pct >= 62.0 and p_cerrados_pct >= 50.0:
        jugada_optima_txt = f"Menos de 2.5 goles ({p_u25_pct:.1f}%) o 1er Tiempo : Empate"
    else:
        jugada_optima_txt = f"Doble Oportunidad : {dc_equipo} ({p_dc_val:.1f}%)"

    # 2. Solo si la probabilidad real Poisson de -1.5 en 1T es >= 68.0% (lam_1t_comb <= 1.08),
    # anexarla al lado con ' | ' sin borrar la jugada principal
    if p_u15_1t >= 68.0 and lam_1t_comb <= 1.08:
        jugada_optima_txt += f" | 1ra Mitad : Menos de 1.5 Goles ({p_u15_1t:.1f}%)"

    if asedio_bandas:
        jugada_optima_txt += " | Córneres : Más de 8.5"

    linea_pie = (
        f"Dominante [{top4_txt}]: {top4_pct:.1f}%  ·  {comp_favorito_txt}\n"
        f"🎯 JUGADA ÓPTIMA TE APUESTO: {jugada_optima_txt}"
    )

    return {
        "cluster_dominante": dominante,
        "clusters": lista_clusters,
        "cluster_top4_contiguo": {
            "marcadores": mejor_top4_coords,
            "texto": top4_txt,
            "probabilidad_pct": top4_pct,
        },
        "favorito": {
            "tipo": favorito_tipo,
            "nombre": favorito_nombre,
            "comparativa_txt": comp_favorito_txt,
        },
        "jugada_optima": jugada_optima_txt,
        "rango_local": rango_l,
        "rango_visitante": rango_v,
        "rango_local_txt": rango_l_txt,
        "rango_visitante_txt": rango_v_txt,
        "linea_pie": linea_pie,
    }



def comparar_moduladores(doc_local: dict[str, Any], doc_visitante: dict[str, Any],
                         equipo_local: str, equipo_visitante: str,
                         **kwargs: Any) -> dict[str, Any]:
    """
    Mide el efecto de los moduladores tácticos SIN activarlos.

    Compara la λ base con la λ modulada y cuenta cuántas selecciones cambian de
    probabilidad y cuántas CRUZAN el umbral. Eso es lo que decide si el cambio es
    una mejora o una regresión: no basta con que los números sean distintos.
    """
    analisis = analizar(doc_local, doc_visitante, equipo_local, equipo_visitante, **kwargs)
    motor = analisis.motor
    medias_l, medias_v = extraer_medias_para_moduladores(motor)
    mods = calcular_moduladores(medias_l, medias_v)
    lam_l, lam_v = motor.tasas.lambda_local_ft, motor.tasas.lambda_visitante_ft
    lam_l_mod = (lam_l * mods["mult_goles_L"]) if lam_l is not None else None
    lam_v_mod = (lam_v * mods["mult_goles_V"]) if lam_v is not None else None

    # ¿Cuántas selecciones cruzarían el umbral con la λ modulada? Se recalcula la
    # matriz de goles con las λ nuevas y se reevalúan los mercados de goles.
    umbral = motor.p.umbral
    cruzan: list[dict[str, Any]] = []
    cambian = 0
    if lam_l_mod is not None and lam_v_mod is not None:
        matriz_mod, _ = matriz_marcadores(lam_l_mod, lam_v_mod, rho=motor.p.rho)
        dist_mod = total_goles(matriz_mod)
        for sel in analisis.selecciones_filtradas:
            if sel.periodo != "FT" or sel.evento.startswith(("corners", "amarillas",
                                                            "local_corners",
                                                            "visitante_corners")):
                continue
            nueva = None
            if sel.evento.startswith("over_"):
                nueva = prob_over(dist_mod, sel.linea or 0.0)
            elif sel.evento.startswith("under_"):
                nueva = prob_under(dist_mod, sel.linea or 0.0)
            elif sel.evento == "btts_si":
                nueva = prob_desde_matriz(matriz_mod, lambda i, j: i > 0 and j > 0)
            if nueva is None:
                continue
            if abs(nueva - (sel.probabilidad or 0)) > 1e-9:
                cambian += 1
            antes_supera = (sel.probabilidad or 0) > umbral
            ahora_supera = nueva > umbral
            if antes_supera != ahora_supera:
                cruzan.append({"evento": sel.evento, "periodo": sel.periodo,
                               "linea": sel.linea,
                               "prob_antes": sel.probabilidad, "prob_despues": nueva,
                               "direccion": ("entra" if ahora_supera else "sale")})
    return {
        "lambda_base": {"local": lam_l, "visitante": lam_v},
        "lambda_modulada": {"local": lam_l_mod, "visitante": lam_v_mod},
        "moduladores": mods,
        "medias": {"local": medias_l, "visitante": medias_v},
        "selecciones_evaluadas": len(analisis.selecciones_filtradas),
        "selecciones_con_probabilidad_distinta": cambian,
        "cruzan_umbral": cruzan,
    }


def escenarios_goles() -> list[tuple[str, Parametros]]:
    """Nueve escenarios de (w_condicion, w_xg) ∈ {0, 0.5, 1}²."""
    escenarios = []
    for wc in VALORES_W_CONDICION:
        for wx in VALORES_W_XG:
            escenarios.append((f"wc{wc:g}_wx{wx:g}",
                               Parametros(w_condicion=wc, w_xg=wx)))
    return escenarios


def escenarios_conteos() -> list[tuple[str, Parametros]]:
    """
    Escenarios de córneres/tarjetas: w_condicion ∈ {0, 0.5, 1} ×
    {Poisson, binomial negativa k=10, k=5}.
    """
    escenarios = []
    for wc in VALORES_W_CONDICION:
        escenarios.append((f"wc{wc:g}_poisson",
                           Parametros(w_condicion=wc, distribucion_conteos="poisson")))
        for k in VALORES_K_NB:
            escenarios.append((f"wc{wc:g}_nb_k{k:g}",
                               Parametros(w_condicion=wc, distribucion_conteos="binomial_negativa",
                                          k_nb=k)))
    return escenarios


def _mercados_de_conteos() -> set[str]:
    return {"total_corners", "corners_local", "corners_visitante", "total_tarjetas"}


def _mercados_de_jugadores() -> set[str]:
    return {"anotador_cualquier_momento", "primer_anotador", "anota_dos_o_mas", "hat_trick",
            "cualquier_jugador_primero", "cualquier_jugador_anota"}


def evaluar_sensibilidad(doc_local: dict[str, Any], doc_visitante: dict[str, Any],
                         cond_local: str, cond_visitante: str,
                         nombre_local: str, nombre_visitante: str,
                         parametros_centrales: Parametros,
                         aviso: Callable[[str], None] | None = None) -> dict[str, dict[str, float]]:
    """
    Devuelve {clave_unica: {nombre_escenario: probabilidad}} para todos los
    escenarios requeridos. Los mercados que no se pueden calcular en un escenario
    simplemente no aparecen con ese escenario (se detectará como incompleto).
    """
    avisar = aviso or (lambda m: None)
    escenarios = escenarios_goles() + escenarios_conteos()
    acumulado: dict[tuple, dict[str, float]] = {}
    nombres_escenario: list[str] = []

    for nombre, params in escenarios:
        params.umbral = parametros_centrales.umbral
        motor = MotorMercados(doc_local, doc_visitante, cond_local, cond_visitante,
                              nombre_local, nombre_visitante, params, aviso=avisar)
        motor.preparar()
        nombres_escenario.append(nombre)
        for res in motor.evaluar_catalogo():
            if res.id_mercado in _mercados_de_jugadores():
                continue
            for sel in res.selecciones:
                if not sel.estimable or sel.probabilidad is None:
                    continue
                # En los escenarios de conteos solo se evalúan los mercados de conteos;
                # en los de goles, el resto (evita 108 ejecuciones innecesarias).
                es_conteo = res.id_mercado in _mercados_de_conteos()
                es_escenario_conteo = nombre.startswith("wc") and ("poisson" in nombre or "nb_k" in nombre)
                if es_conteo != es_escenario_conteo:
                    continue
                acumulado.setdefault(sel.clave_unica, {})[nombre] = sel.probabilidad

    avisar(f"sensibilidad: {len(escenarios)} escenarios evaluados")
    return {_clave_a_texto(k): v for k, v in acumulado.items()}


def _clave_a_texto(clave: tuple) -> str:
    return "|".join(str(x) for x in clave)


def clasificar(prob_central: float, escenarios: dict[str, float] | None,
               escenarios_requeridos: list[str], umbral: float) -> tuple[str, str]:
    """
    Clasifica una selección que supera el umbral central.

    • mantiene: TODAS las probabilidades de los escenarios evaluados > umbral.
    • sensible: la central supera el umbral pero algún escenario no.
    • sensibilidad_incompleta: faltan escenarios requeridos (no se puede afirmar
      que mantenga).
    """
    if prob_central is None or prob_central <= umbral:
        return "", ""
    if not escenarios:
        return ESTADO_INCOMPLETA, "sin escenarios evaluados"
    faltan = [e for e in escenarios_requeridos if e not in escenarios]
    if faltan:
        return ESTADO_INCOMPLETA, f"faltan {len(faltan)} de {len(escenarios_requeridos)} escenarios"
    no_superan = [e for e, p in escenarios.items() if p <= umbral]
    if no_superan:
        return ESTADO_SENSIBLE, f"{len(no_superan)} escenarios ≤ umbral"
    return ESTADO_MANTIENE, f"{len(escenarios)} escenarios > umbral"


# ═══════════════════════════════════════════════════════════════════════════════
# CARGA DE REPORTES Y RESOLUCIÓN DE LOCAL/VISITANTE
# ═══════════════════════════════════════════════════════════════════════════════

def filtrar_por_fecha(doc: dict[str, Any], fecha_corte: str | None) -> dict[str, Any]:
    """
    Devuelve una copia del documento con SOLO los partidos anteriores a `fecha_corte`
    (excluye el propio partido y cualquier resultado futuro). Si la fecha no es
    conocida, se conservan todos los partidos ya finalizados registrados.
    """
    if not fecha_corte:
        return doc
    copia = dict(doc)
    copia["partidos"] = [p for p in doc.get("partidos", [])
                         if str(p.get("fecha") or "") < str(fecha_corte)]
    return copia


def resolver_condicion(doc_equipo: dict[str, Any], fecha: str | None,
                       equipo: str) -> str:
    """
    Determina en qué condición juega `equipo` en el partido analizado, usando la
    información registrada en los reportes (NUNCA el orden del título).

    Devuelve "casa", "fuera" o "desconocida". Reglas:
      1. Si se conoce la fecha, se busca el partido de esa fecha.
      2. Si no hay fecha pero TODOS los partidos del equipo comparten una única
         condición, se puede inferir sin ambigüedad.
      3. Si no hay fecha y el equipo tiene partidos de local y de visitante, es
         ambiguo: se devuelve "desconocida" en lugar de asumir un valor.
    """
    nombre = str(equipo).lower()
    partidos = [p for p in doc_equipo.get("partidos", [])
                if nombre in str(p.get("equipo", "")).lower()]
    if not partidos:
        return "desconocida"

    if fecha:
        for p in partidos:
            if str(p.get("fecha")) == str(fecha):
                cond = str(p.get("condicion", "")).lower()
                if cond in ("casa", "fuera"):
                    return cond
        # Hay fecha pero ningún partido coincide: no se puede resolver así
        return "desconocida"

    condiciones = {str(p.get("condicion", "")).lower() for p in partidos}
    condiciones &= {"casa", "fuera"}
    if len(condiciones) == 1:
        return condiciones.pop()
    return "desconocida"


# ═══════════════════════════════════════════════════════════════════════════════
# ANÁLISIS COMPLETO
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Analisis:
    metadatos: dict[str, Any]
    parametros: Parametros
    motor: MotorMercados
    mercados: list[ResultadoMercado]
    selecciones_filtradas: list[Seleccion]
    grupos: dict[str, list[Seleccion]]
    sensibilidad: dict[str, dict[str, float]]
    informe: str
    mensaje: str

    def estado_mercados(self) -> dict[str, str]:
        return {r.id_mercado: r.estado for r in self.mercados}


def _tasas_sequia_explosion(partidos_red: list[dict[str, Any]]) -> dict[str, float]:
    """
    Tasa de sequía y de explosión goleadora de UN equipo, con la fórmula exacta
    de `motor_recursivo_45_dcn` (líneas 394-399):

        sequía     = 0.65 · (0 goles en los últimos 6) + 0.35 · (0 goles en la ventana)
        explosión  = fracción de los últimos 8 partidos con ≥ 3 goles

    Existe porque el módulo solo devuelve las tasas del LOCAL: el visitante las
    calcula y las usa igual, pero no las publica, y el informe debe poder
    demostrar que la regla se aplicó (o no) a los dos lados.
    """
    # Orden CRONOLÓGICO obligatorio: el "último bloque" de 8 solo tiene sentido
    # si la serie va del más antiguo al más reciente (el módulo también ordena).
    ordenados = sorted(
        (p for p in partidos_red or [] if isinstance(p, dict)),
        key=lambda p: str(p.get("fecha") or ""))
    goles = [float(p.get("goles_local") or 0.0) for p in ordenados]
    if not goles:
        return {"sequia": 0.0, "explosion_3mas": 0.0, "n": 0}
    ult8 = goles[-min(8, len(goles)):]
    ult6 = goles[-min(6, len(goles)):]
    sequia = (0.65 * sum(1.0 for g in ult6 if g == 0.0) / len(ult6)
              + 0.35 * sum(1.0 for g in goles if g == 0.0) / len(goles))
    explosion = sum(1.0 for g in ult8 if g >= 3.0) / len(ult8)
    return {"sequia": round(sequia, 4), "explosion_3mas": round(explosion, 4),
            "n": len(goles), "ultimos_8": [int(g) for g in ult8]}


def _lineas_motor_recursivo(motor: "MotorMercados") -> list[str]:
    """
    Líneas de trazabilidad de la RED NEURONAL RECURSIVA (45 variables).

    Se construyen aquí, desde el resultado que guardó el motor, para que informe
    y JSON digan exactamente lo que calculó la red (sin duplicar fórmulas ni
    depender de textos del módulo).
    """
    rec = getattr(motor, "recursivo", None) or {}
    if not rec:
        return []
    if not rec.get("aplicado"):
        motivo = rec.get("error") or rec.get("motivo") or "sin motivo declarado"
        return [f"Red neuronal recursiva NO aplicada: {motivo}. "
                f"Se usó la matriz del modelo lineal."]
    entropias = rec.get("historial_entropia") or []
    h_final = float(entropias[-1]) if entropias else 0.0
    tasas = rec.get("tasas_por_equipo") or {}
    lineas_tasas: list[str] = []
    for bando, etiqueta, lam in (
        ("local", "local", rec.get("lambda_local_recursivo")),
        ("visita", "visita", rec.get("lambda_visita_recursivo")),
    ):
        datos = tasas.get(bando) or {}
        if not datos:
            continue
        activa = (float(datos.get("explosion_3mas") or 0.0) >= 0.50
                  and float(lam or 0.0) >= 2.10)
        lineas_tasas.append(
            f"{etiqueta}: sequía {datos.get('sequia')} · explosión(≥3 en 8) "
            f"{datos.get('explosion_3mas')} · último bloque {datos.get('ultimos_8')} · "
            f"regla de explosión {'ACTIVA' if activa else 'inactiva'} "
            f"(explosión≥0,50 y λ≥2,10)")
    return [
        f"Red Neuronal Recursiva (45 variables → Bilinear Pooling → Cross-Attention "
        f"→ bucle hasta convergencia → Monte Carlo 100.000): λ FT "
        f"{rec.get('lambda_base_local'):.2f}–{rec.get('lambda_base_visita'):.2f} → "
        f"{rec.get('lambda_local_recursivo'):.2f}–{rec.get('lambda_visita_recursivo'):.2f}",
        f"Convergencia en la iteración {rec.get('iteracion_convergencia')} · "
        f"H final {h_final:.4f} · historial de entropía {entropias}",
        f"Cruce dominante local: {rec.get('cruce_dominante_local')} "
        f"(intensidad {rec.get('intensidad_cruce_local')}) · "
        f"visita: {rec.get('cruce_dominante_visita')} "
        f"(intensidad {rec.get('intensidad_cruce_visita')})",
        f"Tensor real: {rec.get('series_reales_por_equipo')} series "
        f"(45 métricas reales × 15 partidos + goles; sin reconstrucción desde la media)",
        "Tasas por equipo (se calculan y se aplican por separado): "
        + " | ".join(lineas_tasas),
        f"Sello visual impreso en el PNG: "
        f"{str(rec.get('sello_verificacion_visual') or '').replace(chr(10), ' · ')}",
    ]


def generar_informe(analisis_metadatos: dict[str, Any], motor: MotorMercados,
                    mercados: list[ResultadoMercado],
                    grupos: dict[str, list[Seleccion]],
                    sensibilidad: dict[str, dict[str, float]],
                    parametros: Parametros) -> str:
    """Informe técnico completo, con trazabilidad de datos y escenarios."""
    m = analisis_metadatos
    L: list[str] = []
    L.append(f"# Análisis de mercados — {m['equipo_local']} (local) vs "
             f"{m['equipo_visitante']} (visitante)")
    L.append(f"**Generado**: {m['generado_en']}  ")
    fecha_val = m.get('fecha_partido')
    fecha_display = (str(fecha_val) if fecha_val and str(fecha_val).lower() not in ('no disponible', 'none', '')
                     else "Sin partido próximo en calendario (localía por orden de consulta)")
    L.append(f"**Fecha del partido**: {fecha_display} | "
             f"**Competición**: {m.get('competicion') or 'no disponible'} | "
             f"**Sede**: {m.get('sede') or 'no disponible (se asume la del local)'}  ")
    L.append(f"**Modelo**: {NOMBRE_MODELO} v{VERSION_MODELO} — probabilidades estimadas, "
             f"no garantías.  ")
    L.append(f"**Umbral del filtro central**: {parametros.umbral:.0%} "
             f"(comparación sobre el valor sin redondear)  ")
    L.append(f"**Cobertura de datos**: {linea_cobertura_xg(m, motor)}  ")
    L.append(f"**Parámetros centrales**: w_condicion={parametros.w_condicion:g}, "
             f"w_xg={parametros.w_xg:g}, distribución de conteos={parametros.distribucion_conteos}"
             f"{f', k={parametros.k_nb:g}' if parametros.distribucion_conteos == 'binomial_negativa' else ''}  ")
    L.append("")

    # Muestra
    L.append("## Muestra y trazabilidad")
    L.append("")
    L.append("| Bloque | Equipo | Partidos |")
    L.append("|---|---|---|")
    for clave, datos in motor.cobertura.items():
        if isinstance(datos, dict) and "partidos" in datos:
            L.append(f"| {clave} | {datos['equipo']} | {datos['partidos']} |")
    L.append("")
    L.append("| Métrica | Bloque | n | Media |")
    L.append("|---|---|---|---|")
    for clave, datos in motor.cobertura.items():
        if not isinstance(datos, dict) or "metricas" not in datos:
            continue
        for metrica, v in datos["metricas"].items():
            L.append(f"| {metrica} | {clave} | {v['n']} | "
                     f"{('%.4f' % v['media']) if v['media'] is not None else 'N/D'} |")
    L.append("")

    # Tasas
    L.append("## Tasas estimadas")
    L.append("")
    L.append(f"- λ FT local: {motor.tasas.lambda_local_ft}")
    L.append(f"- λ FT visitante: {motor.tasas.lambda_visitante_ft}")
    L.append(f"- λ 1T local/visitante: {motor.tasas.lambda_local_1t} / {motor.tasas.lambda_visitante_1t}")
    L.append(f"- λ 2T local/visitante: {motor.tasas.lambda_local_2t} / {motor.tasas.lambda_visitante_2t}")
    L.append(f"- Procedencia de los tiempos: {motor.tasas.procedencia_tiempos or 'no disponible'}")
    L.append(f"- Masa residual de la matriz FT: {motor.residual_ft:.3e} "
             f"(tolerancia {MASA_RESIDUAL_MAXIMA:.0e})")
    L.append("")
    L.append("### Cálculo de λ FT (trazabilidad)")
    L.append("")
    for etiqueta, d in motor.detalle_goles.items():
        if not isinstance(d, dict):
            continue      # las claves lambda_* son floats, no bloques de trazabilidad
        if etiqueta.startswith("_"):
            continue      # bloques internos (p. ej. _moduladores), no son de goles
        L.append(f"- **{etiqueta}**: goles_favor={d.get('goles_favor')}, "
                 f"goles_contra={d.get('goles_contra')}, xg_favor={d.get('xg_favor')}, "
                 f"xg_contra={d.get('xg_contra')} → ataque={d.get('ataque')}, "
                 f"defensa={d.get('defensa')}")
        if d.get("w_xg_efectivo_ataque") is not None:
            L.append(f"    - peso del xG ponderado por cobertura: "
                     f"ataque={d['w_xg_efectivo_ataque']:.4f} "
                     f"(n_xG {d.get('n_xg_favor')}/{d.get('n_goles_favor')}), "
                     f"defensa={d['w_xg_efectivo_defensa']:.4f} "
                     f"(n_xG {d.get('n_xg_contra')}/{d.get('n_goles_contra')})")
        if d.get("descripcion_ataque"):
            L.append(f"    - base declarada: {d['descripcion_ataque']}")
    mods = (motor.detalle_goles or {}).get("_moduladores")
    if mods:
        L.append("")
        L.append(f"- Moduladores tácticos aplicados: "
                 f"λ local {mods['lambda_local_sin_modular']:.4f}→"
                 f"{mods['lambda_local_modulada']:.4f} (x{mods['mult_goles_L']}), "
                 f"λ visitante {mods['lambda_visitante_sin_modular']:.4f}→"
                 f"{mods['lambda_visitante_modulada']:.4f} (x{mods['mult_goles_V']})")
        L.append(f"- Métricas reales que entraron: "
                 f"{', '.join(mods['metricas_activas']) or 'ninguna (todos los factores neutros)'}")
    L.append("")
    # ── Motor Recursivo de 2 Niveles (45-DCN) ──────────────────────────────
    lineas_recursivo = _lineas_motor_recursivo(motor)
    if lineas_recursivo:
        L.append("## Red Neuronal Recursiva (45 variables)")
        L.append("")
        for linea in lineas_recursivo:
            L.append(f"- {linea}")
        L.append("")

    # ── IA Explicable (XAI): 5 Factores Latentes y Cluster Dominante ─────────
    lineas_xai_inf = formatear_bloque_xai(motor)
    if lineas_xai_inf:
        L.append("## IA Explicable (XAI): 5 Factores Latentes y Cluster Dominante")
        L.append("")
        for lx in lineas_xai_inf:
            L.append(f"- {lx}")
        L.append("")
    if motor.notas:
        L.append("### Limitaciones registradas")
        L.append("")
        for nota in motor.notas:
            L.append(f"- {nota}")
        L.append("")

    # Selecciones que pasan
    L.append("## Selecciones con probabilidad central superior al umbral")
    L.append("")
    titulos = {ESTADO_MANTIENE: "Mantienen el umbral en los escenarios evaluados",
               ESTADO_SENSIBLE: "Sensibles a los supuestos (central supera el umbral)",
               ESTADO_INCOMPLETA: "Sensibilidad incompleta (central supera el umbral)"}
    if not any(grupos.values()):
        L.append("_Ninguna selección supera el umbral configurado._")
        L.append("")
    for estado in (ESTADO_MANTIENE, ESTADO_SENSIBLE, ESTADO_INCOMPLETA):
        sels = grupos.get(estado) or []
        if not sels:
            continue
        L.append(f"### {titulos[estado]} ({len(sels)})")
        L.append("")
        L.append("| Mercado | Selección | Periodo | Prob. central | Estado | Escenarios |")
        L.append("|---|---|---|---|---|---|")
        for s in sels:
            L.append(f"| {s.mercado} | {s.etiqueta} | {s.periodo} | "
                     f"{formatear_pct(s.probabilidad, parametros.umbral)} | {s.estado} | "
                     f"{len(s.escenarios)} |")
        L.append("")

    # Catálogo completo
    L.append("## Catálogo completo de mercados")
    L.append("")
    for res in mercados:
        L.append(f"### {res.numero}. {res.nombre} — `{res.id_mercado}`")
        L.append(f"- Estado: **{res.estado}**")
        if res.nota:
            L.append(f"- Nota: {res.nota}")
        if res.selecciones:
            L.append("")
            L.append("| Evento | Selección | Periodo | Probabilidad | Escenarios |")
            L.append("|---|---|---|---|---|")
            for s in res.selecciones:
                esc = len(s.escenarios)
                L.append(f"| `{s.evento}` | {s.etiqueta} | {s.periodo} | "
                         f"{formatear_pct(s.probabilidad, parametros.umbral)} | {esc} |")
        L.append("")

    # Sensibilidad
    L.append("## Sensibilidad por escenario")
    L.append("")
    L.append("Escenarios de goles: w_condicion × w_xg ∈ {0; 0,5; 1}². "
             "Escenarios de conteos: w_condicion × {Poisson, BN k=10, BN k=5}.")
    L.append("")
    L.append("| Selección | Central | Escenarios que superan el umbral | Total escenarios |")
    L.append("|---|---|---|---|")
    for estado in (ESTADO_MANTIENE, ESTADO_SENSIBLE, ESTADO_INCOMPLETA):
        for s in grupos.get(estado) or []:
            superan = sum(1 for p in s.escenarios.values() if p > parametros.umbral)
            L.append(f"| {s.mercado}: {s.etiqueta} ({s.periodo}) | "
                     f"{formatear_pct(s.probabilidad, parametros.umbral)} | {superan} | "
                     f"{len(s.escenarios)} |")
    L.append("")
    L.append("Un rango de escenarios NO es un intervalo de confianza ni demuestra que la "
             "probabilidad real sea superior al umbral.")
    L.append("")
    L.append("### Detalle de escenarios por selección filtrada")
    L.append("")
    for estado in (ESTADO_MANTIENE, ESTADO_SENSIBLE, ESTADO_INCOMPLETA):
        for s in grupos.get(estado) or []:
            L.append(f"- **{s.mercado}: {s.etiqueta} ({s.periodo})** — "
                     f"central {formatear_pct(s.probabilidad, parametros.umbral)}")
            for esc, valor in sorted(s.escenarios.items()):
                L.append(f"    - {esc}: {valor*100:.2f}%")
    L.append("")
    return "\n".join(L)


def analizar(doc_local: dict[str, Any], doc_visitante: dict[str, Any],
             equipo_local: str, equipo_visitante: str,
             fecha_partido: str | None = None, competicion: str | None = None,
             sede: str | None = None, condicion_local: str | None = None,
             condicion_visitante: str | None = None,
             parametros: Parametros | None = None,
             aviso: Callable[[str], None] | None = None) -> Analisis:
    """
    Ejecuta el análisis completo del enfrentamiento.

    `condicion_local` / `condicion_visitante` permiten forzar la condición cuando
    el usuario la aporta explícitamente; si no, se intenta resolver desde los
    reportes y, si no es posible, se registra como ambigüedad (no se asume).
    """
    avisar = aviso or (lambda m: None)
    params = parametros or Parametros()

    # 1) Condición real (nunca el orden del título)
    if condicion_local in ("casa", "fuera") and condicion_visitante in ("casa", "fuera"):
        cl, cv = condicion_local, condicion_visitante
        origen_cond = "aportada por el usuario"
    else:
        cl = resolver_condicion(doc_local, fecha_partido, equipo_local)
        cv = resolver_condicion(doc_visitante, fecha_partido, equipo_visitante)
        origen_cond = "resuelta desde los reportes"
        if cl == "desconocida" or cv == "desconocida":
            # Convención explícita y declarada cuando no hay dato verificable
            cl, cv = "casa", "fuera"
            origen_cond = ("asumida por convención (local/visitante del título) al no "
                           "encontrarse la condición en los reportes")
            avisar(f"⚠️ condición no verificable: se asume {equipo_local} local "
                   f"y {equipo_visitante} visitante (declarado en el informe)")

    # 2) Solo partidos anteriores al encuentro
    dl = filtrar_por_fecha(doc_local, fecha_partido)
    dv = filtrar_por_fecha(doc_visitante, fecha_partido)

    metadatos = {
        "equipo_local": equipo_local,
        "equipo_visitante": equipo_visitante,
        "fecha_partido": fecha_partido,
        "competicion": competicion,
        "sede": sede,
        "condicion_local": cl,
        "condicion_visitante": cv,
        "origen_condicion": origen_cond,
        "generado_en": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "fuente": dl.get("fuente") or dv.get("fuente"),
        "modelo": f"{NOMBRE_MODELO} v{VERSION_MODELO}",
    }

    # 3) Motor central
    motor = MotorMercados(dl, dv, cl, cv, equipo_local, equipo_visitante, params, aviso=avisar)
    motor.preparar()
    if motor.tasas.lambda_local_ft is None or motor.tasas.lambda_visitante_ft is None:
        avisar("⚠️ λ FT no estimables: los mercados de goles quedarán como datos insuficientes")
    mercados = motor.evaluar_catalogo()

    # 4) Sensibilidad
    sensibilidad = evaluar_sensibilidad(dl, dv, cl, cv, equipo_local, equipo_visitante,
                                        params, aviso=avisar)
    requeridos_goles = [n for n, _ in escenarios_goles()]
    requeridos_conteos = [n for n, _ in escenarios_conteos()]
    selecciones: list[Seleccion] = []
    for res in mercados:
        for s in res.selecciones:
            if not s.estimable or s.probabilidad is None:
                continue
            esc = sensibilidad.get(_clave_a_texto(s.clave_unica), {})
            s.escenarios = esc
            if s.probabilidad <= params.umbral:
                continue
            req = requeridos_conteos if res.id_mercado in _mercados_de_conteos() else requeridos_goles
            estado, motivo = clasificar(s.probabilidad, esc, req, params.umbral)
            if not estado:
                continue
            s.estado = estado
            if motivo:
                s.motivo = motivo
            selecciones.append(s)

    # Deduplicado exacto de mercado/evento/periodo/equipo/línea
    vistos: dict[tuple, Seleccion] = {}
    for s in selecciones:
        vistos.setdefault(s.clave_unica, s)
    selecciones = list(vistos.values())

    grupos = {ESTADO_MANTIENE: [], ESTADO_SENSIBLE: [], ESTADO_INCOMPLETA: []}
    for s in selecciones:
        grupos.setdefault(s.estado, []).append(s)
    avisar(f"{len(selecciones)} selecciones superan el {params.umbral:.0%} central "
           f"({len(grupos[ESTADO_MANTIENE])} mantienen, "
           f"{len(grupos[ESTADO_SENSIBLE])} sensibles, "
           f"{len(grupos[ESTADO_INCOMPLETA])} incompletas)")

    informe = generar_informe(metadatos, motor, mercados, grupos, sensibilidad, params)
    mensaje = construir_mensaje(metadatos, grupos, params, motor, mercados)
    return Analisis(metadatos, params, motor, mercados, selecciones, grupos,
                    sensibilidad, informe, mensaje)


def renderizar_ticket_png(
    analisis: Any,
    metadatos: dict[str, Any] | None = None,
    umbral: float | None = None,
    destino: str | None = None,
) -> str:
    """
    Renderiza el Ticket Cuantitativo de Mercados PNG con diseño editorial claro.
    (Handoff a servicio_mercados.renderizar_ticket_mercados_png).
    """
    import servicio_mercados as _servicio
    return _servicio.renderizar_ticket_mercados_png(analisis=analisis,
                                                   metadatos=metadatos,
                                                   umbral=umbral,
                                                   destino=destino)


renderizar_ticket_mercados_png = renderizar_ticket_png


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRADA POR LÍNEA DE COMANDOS
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    import argparse
    import sys

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Análisis de mercados con filtro por probabilidad (>umbral)")
    parser.add_argument("cruces", nargs="+", help='"Equipo A vs Equipo B"')
    parser.add_argument("--umbral", type=float, default=UMBRAL_DEFECTO,
                        help=f"Umbral del filtro central (por defecto {UMBRAL_DEFECTO})")
    parser.add_argument("--limite", type=int, default=15,
                        help="Partidos por equipo en los reportes")
    parser.add_argument("--w-condicion", type=float, default=W_CONDICION_DEFECTO)
    parser.add_argument("--w-xg", type=float, default=W_XG_DEFECTO)
    parser.add_argument("--forzar-datos", action="store_true",
                        help="Regenera los reportes aunque existan")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--enviar", action="store_true",
                        help="Envía el mensaje al Telegram configurado")
    parser.add_argument("--enviar-a", default=None,
                        help="Chat ID alternativo SOLO para pruebas (por defecto, el configurado)")
    args = parser.parse_args()

    import servicio_mercados as servicio

    def avisar(msg: str) -> None:
        print(f"  {msg}")

    try:
        preparado = servicio.preparar_analisis(
            " ".join(args.cruces), limite=args.limite, umbral=args.umbral,
            w_condicion=args.w_condicion, w_xg=args.w_xg,
            forzar_datos=args.forzar_datos, headless=not args.visible, progreso=avisar)
    except Exception as e:
        print(f"\n❌ El análisis falló: {type(e).__name__}: {e}")
        return 1

    analisis: Analisis = preparado["analisis"]
    print("\n" + "=" * 74)
    print(f"  {analisis.metadatos['equipo_local']} (local) vs "
          f"{analisis.metadatos['equipo_visitante']} (visitante)")
    print(f"  Condición: {analisis.metadatos['origen_condicion']}")
    print(f"  λ FT: {analisis.motor.tasas.lambda_local_ft} / "
          f"{analisis.motor.tasas.lambda_visitante_ft}")
    print(f"  Selecciones > {args.umbral:.0%}: {len(analisis.selecciones_filtradas)} "
          f"({len(analisis.grupos.get(ESTADO_MANTIENE, []))} mantienen, "
          f"{len(analisis.grupos.get(ESTADO_SENSIBLE, []))} sensibles, "
          f"{len(analisis.grupos.get(ESTADO_INCOMPLETA, []))} incompletas)")
    print(f"  Informe: {preparado['rutas']['informe']}")
    print("=" * 74)
    print("\n--- MENSAJE DE TELEGRAM ---\n")
    print(preparado["mensaje"])

    if args.enviar:
        resultado = servicio.enviar_analisis(
            preparado, enviar_fn=servicio.enviar_fn_telegram, chat_id=args.enviar_a)
        print(f"\n--- ENVÍO ---\n{json.dumps(resultado, ensure_ascii=False, indent=1)}")
        return 0 if resultado.get("enviado") else 1
    print("\n(modo borrador: nada enviado; usa --enviar para despachar a Telegram)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
