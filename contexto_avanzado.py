from __future__ import annotations
from typing import Any, Dict, List, Optional
import datetime
import json
import os
from pathlib import Path

CACHE_CUOTAS_APERTURA = Path(__file__).resolve().parent / "cache" / "cuotas_apertura.json"


def _calcular_dias_descanso(
    partidos: List[Dict[str, Any]],
    fecha_partido_actual: str,
    default_dias: int = 7,
) -> int:
    """Calcula los días de descanso entre el último partido jugado y la fecha del partido actual."""
    if not partidos or not fecha_partido_actual:
        return default_dias
    try:
        f_act_str = str(fecha_partido_actual).strip()[:10]
        if "/" in f_act_str:
            dt_act = datetime.datetime.strptime(f_act_str, "%d/%m/%Y").date()
        else:
            dt_act = datetime.datetime.strptime(f_act_str, "%Y-%m-%d").date()
    except Exception:
        return default_dias

    fechas_previas: List[datetime.date] = []
    for p in partidos:
        if not isinstance(p, dict):
            continue
        f_p = str(p.get("fecha") or p.get("utcTime") or "").strip()[:10]
        if not f_p:
            continue
        try:
            dt_p = (
                datetime.datetime.strptime(f_p, "%d/%m/%Y").date()
                if "/" in f_p
                else datetime.datetime.strptime(f_p, "%Y-%m-%d").date()
            )
            if dt_p < dt_act:
                fechas_previas.append(dt_p)
        except Exception:
            continue

    if not fechas_previas:
        return default_dias
    ultimo_dt = max(fechas_previas)
    diff = (dt_act - ultimo_dt).days
    return max(1, diff) if diff > 0 else default_dias


def registrar_o_comparar_cuota_apertura(
    clave_partido: str,
    cuota_actual_favorito: Optional[float],
    cuota_apertura_explicita: Optional[float] = None,
) -> tuple[Optional[float], Optional[float]]:
    """
    Guarda en caché la cuota de apertura de un partido y devuelve (cuota_apertura, cuota_actual).
    """
    if cuota_apertura_explicita is not None and float(cuota_apertura_explicita) > 1.01:
        return float(cuota_apertura_explicita), (float(cuota_actual_favorito) if cuota_actual_favorito else None)
    if cuota_actual_favorito is None or float(cuota_actual_favorito) <= 1.01 or not clave_partido:
        return None, None
    c_act = float(cuota_actual_favorito)
    try:
        os.makedirs(CACHE_CUOTAS_APERTURA.parent, exist_ok=True)
        datos: Dict[str, Any] = {}
        if CACHE_CUOTAS_APERTURA.is_file():
            with open(CACHE_CUOTAS_APERTURA, "r", encoding="utf-8") as f:
                datos = json.load(f) or {}
        if clave_partido in datos and isinstance(datos[clave_partido], (int, float)):
            c_ap = float(datos[clave_partido])
        else:
            c_ap = c_act
            datos[clave_partido] = c_ap
            with open(CACHE_CUOTAS_APERTURA, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False, indent=1)
        return c_ap, c_act
    except Exception:
        return c_act, c_act


def evaluar_contexto_avanzado_partido(
    partidos_local: List[Dict[str, Any]],
    partidos_visita: List[Dict[str, Any]],
    fecha_partido_actual: str,
    info_contexto_fs_fm: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Evalúa los 5 Módulos de Contexto Profesional (Fatiga, Motivación, Bajas/Alineaciones,
    Árbitro y Sharp Money / Movimiento de Cuotas) y devuelve los multiplicadores y alertas.
    """
    info = info_contexto_fs_fm if isinstance(info_contexto_fs_fm, dict) else {}
    mult_lambda_local = 1.0
    mult_lambda_visita = 1.0
    mult_pressing_local = 1.0
    mult_pressing_visita = 1.0
    mult_attack_local = 1.0
    mult_attack_visita = 1.0
    mult_defense_local = 1.0
    mult_defense_visita = 1.0
    mult_tarjetas = 1.0
    alertas_contexto: List[str] = []

    # 1. MÓDULO DE FATIGA (Fixture Congestion: <= 3 días de descanso tras Champions/Copa/Liga)
    dias_desc_local = info.get("dias_descanso_local")
    dias_desc_visita = info.get("dias_descanso_visita")
    if dias_desc_local is None:
        if fecha_partido_actual:
            dias_desc_local = _calcular_dias_descanso(partidos_local or [], fecha_partido_actual, 7)
        else:
            dias_desc_local = 7
    if dias_desc_visita is None:
        if fecha_partido_actual:
            dias_desc_visita = _calcular_dias_descanso(partidos_visita or [], fecha_partido_actual, 7)
        else:
            dias_desc_visita = 7

    if dias_desc_local is not None and int(dias_desc_local) <= 3:
        mult_lambda_local *= 0.85
        mult_pressing_local *= 0.80
        mult_attack_local *= 0.90
        alertas_contexto.append(
            f"⚠️ FATIGA LOCAL: Solo {int(dias_desc_local)} días de descanso (-15% λ, -20% Pressing, -10% Attack)."
        )
    if dias_desc_visita is not None and int(dias_desc_visita) <= 3:
        mult_lambda_visita *= 0.85
        mult_pressing_visita *= 0.80
        mult_attack_visita *= 0.90
        alertas_contexto.append(
            f"⚠️ FATIGA VISITANTE: Solo {int(dias_desc_visita)} días de descanso (-15% λ, -20% Pressing, -10% Attack)."
        )

    # 2. MÓDULO DE MOTIVACIÓN (Posición en Tabla y Jornada)
    mot_local = str(info.get("motivacion_local", "alta") or "alta").lower()
    mot_visita = str(info.get("motivacion_visita", "alta") or "alta").lower()
    if mot_local == "zona_muerta":
        mult_lambda_local *= 0.90
        alertas_contexto.append("📉 MOTIVACIÓN LOCAL: Mitad de tabla sin objetivo directo (-10% intensidad).")
    elif mot_local in ("alta_urgencia", "pelea_titulo_o_descenso"):
        mult_lambda_local *= 1.06
        alertas_contexto.append("🔥 MOTIVACIÓN LOCAL: Pelea directa por título/clasificación/permanencia (+6% intensidad).")

    if mot_visita == "zona_muerta":
        mult_lambda_visita *= 0.90
        alertas_contexto.append("📉 MOTIVACIÓN VISITA: Mitad de tabla sin objetivo directo (-10% intensidad).")
    elif mot_visita in ("alta_urgencia", "pelea_titulo_o_descenso"):
        mult_lambda_visita *= 1.06
        alertas_contexto.append("🔥 MOTIVACIÓN VISITA: Pelea directa por título/clasificación/permanencia (+6% intensidad).")

    # 3. MÓDULO DE LESIONES Y ALINEACIONES (Bajas de Goleador / Portero Titular en FotMob/Flashscore)
    bajas_local = (
        info.get("bajas_clave_local")
        if isinstance(info.get("bajas_clave_local"), dict)
        else (info.get("bajas_local") if isinstance(info.get("bajas_local"), dict) else {})
    )
    bajas_visita = (
        info.get("bajas_clave_visita")
        if isinstance(info.get("bajas_clave_visita"), dict)
        else (info.get("bajas_visita") if isinstance(info.get("bajas_visita"), dict) else {})
    )
    if bajas_local.get("sin_goleador_titular"):
        mult_lambda_local *= 0.80
        mult_attack_local *= 0.80
        alertas_contexto.append("🚑 BAJA OFENSIVA LOCAL: Ausencia del goleador referente (-20% Attack).")
    if bajas_local.get("sin_portero_titular"):
        mult_lambda_visita *= 1.15
        mult_defense_local *= 0.85
        alertas_contexto.append("🧤 BAJA PORTERÍA LOCAL: Ausencia del arquero titular (-15% Defensa).")
    if bajas_visita.get("sin_goleador_titular"):
        mult_lambda_visita *= 0.80
        mult_attack_visita *= 0.80
        alertas_contexto.append("🚑 BAJA OFENSIVA VISITA: Ausencia del goleador referente (-20% Attack).")
    if bajas_visita.get("sin_portero_titular"):
        mult_lambda_local *= 1.15
        mult_defense_visita *= 0.85
        alertas_contexto.append("🧤 BAJA PORTERÍA VISITA: Ausencia del arquero titular (-15% Defensa).")

    # 4. MÓDULO DEL ÁRBITRO (Referee Profile)
    prom_tarjetas_arbitro = info.get("arbitro_promedio_tarjetas")
    if prom_tarjetas_arbitro is not None:
        try:
            prom_arb = float(prom_tarjetas_arbitro)
            if prom_arb >= 5.5:
                mult_tarjetas *= 1.18
                alertas_contexto.append(
                    f"🟨 ÁRBITRO ESTRICTO: Promedia {prom_arb:.1f} tarjetas/partido (+18% proyección tarjetas)."
                )
            elif prom_arb <= 3.4:
                mult_tarjetas *= 0.85
                alertas_contexto.append(
                    f"🟩 ÁRBITRO PERMISIVO: Promedia {prom_arb:.1f} tarjetas/partido (-15% proyección tarjetas)."
                )
        except (TypeError, ValueError):
            prom_tarjetas_arbitro = None

    # 5. MÓDULO DE "SHARP MONEY" (Movimiento de Cuotas Apertura vs Actual)
    clave_partido = str(info.get("clave_partido") or "")
    cuota_ap_fav, cuota_act_fav = registrar_o_comparar_cuota_apertura(
        clave_partido=clave_partido,
        cuota_actual_favorito=info.get("cuota_actual_favorito"),
        cuota_apertura_explicita=info.get("cuota_apertura_favorito"),
    )
    alerta_sharp_money = False
    descartar_1x2_favorito_top1 = False
    if cuota_ap_fav and cuota_act_fav and float(cuota_ap_fav) > 1.05:
        variacion_pct = (float(cuota_act_fav) - float(cuota_ap_fav)) / float(cuota_ap_fav)
        if variacion_pct >= 0.10:
            alerta_sharp_money = True
            descartar_1x2_favorito_top1 = True
            alertas_contexto.append(
                f"💰 ALERTA SHARP MONEY: La cuota del favorito subió {variacion_pct*100:+.1f}% ({cuota_ap_fav:.2f} -> {cuota_act_fav:.2f}). "
                f"Posible rotación / Valor (+EV) en el equipo no favorito."
            )
        elif variacion_pct <= -0.10:
            alertas_contexto.append(
                f"💰 DINERO INTELIGENTE A FAVOR: La cuota del favorito cayó {variacion_pct*100:.1f}% ({cuota_ap_fav:.2f} -> {cuota_act_fav:.2f})."
            )

    return {
        "mult_lambda_local": round(mult_lambda_local, 4),
        "mult_lambda_visita": round(mult_lambda_visita, 4),
        "mult_pressing_local": round(mult_pressing_local, 4),
        "mult_pressing_visita": round(mult_pressing_visita, 4),
        "mult_attack_local": round(mult_attack_local, 4),
        "mult_attack_visita": round(mult_attack_visita, 4),
        "mult_defense_local": round(mult_defense_local, 4),
        "mult_defense_visita": round(mult_defense_visita, 4),
        "mult_tarjetas": round(mult_tarjetas, 4),
        "arbitro_promedio_tarjetas": float(prom_tarjetas_arbitro) if prom_tarjetas_arbitro is not None else None,
        "dias_descanso_local": int(dias_desc_local) if dias_desc_local is not None else 7,
        "dias_descanso_visita": int(dias_desc_visita) if dias_desc_visita is not None else 7,
        "alerta_sharp_money": alerta_sharp_money,
        "descartar_1x2_favorito_top1": descartar_1x2_favorito_top1,
        "alertas_contexto": alertas_contexto,
    }
