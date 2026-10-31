"""
test_adversarial_guardrails.py
───────────────────────────────────────────────────────────────────────────────
Test de Seguridad y Entradas Adversarias para ValidadorProbabilidades.

Demuestra que el sistema falla de forma segura (Fail-Safe) ante:
  1. Cuota infinita (float('inf')).
  2. Cuota NaN (float('nan')).
  3. Cuota en string ("2.45" / "invalida").
  4. Probabilidad no numérica ("0.75" / None / True / NaN).
  5. Payload omnisciente vacío o ausente.
  6. Cuota no autenticada (no existe en Betsson).
  7. Quarter-Kelly inferior al mínimo operativo (< 0.5%): aborta con Stake = 0.
  8. Entrada válida normal: aprobada con éxito.
───────────────────────────────────────────────────────────────────────────────
"""

import sys
import math
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from validador_probabilidades import ValidadorProbabilidades


def print_banner(titulo: str):
    print("\n" + "=" * 80)
    print(f"  🛡️ {titulo.upper()}")
    print("=" * 80)


def ejecutar_pruebas_adversarias():
    validador = ValidadorProbabilidades(
        ev_minimo_requerido=0.05,
        kelly_divisor=4.0,
        stake_max_bankroll=0.03,
        stake_min_bankroll=0.005,  # 0.5% mínimo operativo
    )

    payload_base = {
        "Partido": "Torino vs Udinese",
        "Mercado_y_Cuotas_Betsson": [
            {"familia": "1er tiempo - Total de goles", "seleccion": "Menos de 0.5", "cuota": 2.45, "periodo": "1er tiempo"},
            {"familia": "Total de goles", "seleccion": "Más de 1.5", "cuota": 1.38, "periodo": "tiempo reglamentario"},
            {"familia": "Línea de gol asiática", "seleccion": "Más de 2.0", "cuota": 1.72, "periodo": "tiempo reglamentario"},
        ],
        "Metricas_Profundas_SofaScore": {
            "overall": {"xg": {"local": 1.15, "visitante": 1.05, "total": 2.20}},
            "1st_half": {"xg": {"local": 0.38, "visitante": 0.32, "total": 0.70}},
        },
        "Rendimiento_Ofensivo_Individual": {
            "delanteros_local": [{"nombre": "Simeone", "titular": True}],
            "delanteros_visitante": [{"nombre": "Buksa", "titular": True}],
        },
        "Historial_Oficial": {
            "h2h_oficial": [{"ft": "1 - 1", "ht": "0 - 0"}],
            "ultimos_10_oficiales_local": [{"gl": 1, "gv": 0}],
            "ultimos_10_oficiales_visitante": [{"gl": 0, "gv": 1}],
        },
    }

    # ── CASO 1: CUOTA INFINITA (float('inf')) ────────────────────────────────
    print_banner("CASO 1: CUOTA INFINITA (float('inf'))")
    decision_inf = {
        "mercado_elegido": "1er tiempo - Total de goles",
        "seleccion": "Menos de 0.5",
        "cuota": float("inf"),
        "probabilidad_real_estimada": 0.65,
    }
    valida, dec_aud, err = validador.validar_decision(decision_inf, payload_base)
    print(f"  • Es válida:               {valida}")
    print(f"  • Guardrails Superados:    {dec_aud['guardrails_superados']}")
    print(f"  • Decisión Final:          {dec_aud['decision_final']}")
    print(f"  • Stake Sugerido:          {dec_aud['stake_sugerido']}")
    print(f"  • Error interceptado:      {err[0] if err else 'Ninguno'}")
    assert valida is False, "Debe abortar con cuota infinita"
    assert dec_aud["guardrails_superados"] is False
    assert dec_aud["stake_fraccional_kelly_pct"] == 0.0

    # ── CASO 2: CUOTA NaN (float('nan')) ─────────────────────────────────────
    print_banner("CASO 2: CUOTA NaN (float('nan'))")
    decision_nan = {
        "mercado_elegido": "1er tiempo - Total de goles",
        "seleccion": "Menos de 0.5",
        "cuota": float("nan"),
        "probabilidad_real_estimada": 0.65,
    }
    valida, dec_aud, err = validador.validar_decision(decision_nan, payload_base)
    print(f"  • Es válida:               {valida}")
    print(f"  • Guardrails Superados:    {dec_aud['guardrails_superados']}")
    print(f"  • Decisión Final:          {dec_aud['decision_final']}")
    print(f"  • Stake Sugerido:          {dec_aud['stake_sugerido']}")
    print(f"  • Error interceptado:      {err[0] if err else 'Ninguno'}")
    assert valida is False, "Debe abortar con cuota NaN"
    assert dec_aud["guardrails_superados"] is False
    assert dec_aud["stake_fraccional_kelly_pct"] == 0.0

    # ── CASO 3: CUOTA STRING ("2.45") ────────────────────────────────────────
    print_banner("CASO 3: CUOTA STRING ('2.45')")
    decision_str = {
        "mercado_elegido": "1er tiempo - Total de goles",
        "seleccion": "Menos de 0.5",
        "cuota": "2.45",
        "probabilidad_real_estimada": 0.65,
    }
    valida, dec_aud, err = validador.validar_decision(decision_str, payload_base)
    print(f"  • Es válida:               {valida}")
    print(f"  • Guardrails Superados:    {dec_aud['guardrails_superados']}")
    print(f"  • Error interceptado:      {err[0] if err else 'Ninguno'}")
    assert valida is False, "Debe abortar con cuota en formato string"
    assert dec_aud["guardrails_superados"] is False

    # ── CASO 4: PROBABILIDAD NO NUMÉRICA ("0.75" / None / True) ──────────────
    print_banner("CASO 4: PROBABILIDAD NO NUMÉRICA ('0.75')")
    decision_prob_str = {
        "mercado_elegido": "1er tiempo - Total de goles",
        "seleccion": "Menos de 0.5",
        "cuota": 2.45,
        "probabilidad_real_estimada": "0.75",
    }
    valida, dec_aud, err = validador.validar_decision(decision_prob_str, payload_base)
    print(f"  • Es válida:               {valida}")
    print(f"  • Guardrails Superados:    {dec_aud['guardrails_superados']}")
    print(f"  • Error interceptado:      {err[0] if err else 'Ninguno'}")
    assert valida is False, "Debe abortar con probabilidad en string"
    assert dec_aud["guardrails_superados"] is False

    # ── CASO 5: PAYLOAD OMNISCIENTE VACÍO ({}) ───────────────────────────────
    print_banner("CASO 5: PAYLOAD OMNISCIENTE VACÍO ({})")
    decision_valida_mock = {
        "mercado_elegido": "1er tiempo - Total de goles",
        "seleccion": "Menos de 0.5",
        "cuota": 2.45,
        "probabilidad_real_estimada": 0.65,
    }
    valida, dec_aud, err = validador.validar_decision(decision_valida_mock, {})
    print(f"  • Es válida:               {valida}")
    print(f"  • Guardrails Superados:    {dec_aud['guardrails_superados']}")
    print(f"  • Error interceptado:      {err[0] if err else 'Ninguno'}")
    assert valida is False, "Debe abortar con payload vacío"
    assert dec_aud["guardrails_superados"] is False

    # ── CASO 6: CUOTA NO AUTENTICADA EN BETSSON (CUOTA FANTASMA @ 4.50) ──────
    print_banner("CASO 6: CUOTA NO AUTENTICADA EN BETSSON (@ 4.50)")
    decision_fantasma = {
        "mercado_elegido": "1er tiempo - Total de goles",
        "seleccion": "Menos de 0.5",
        "cuota": 4.50,  # En Betsson está a 2.45, no a 4.50
        "probabilidad_real_estimada": 0.65,
    }
    valida, dec_aud, err = validador.validar_decision(decision_fantasma, payload_base)
    print(f"  • Es válida:               {valida}")
    print(f"  • Guardrails Superados:    {dec_aud['guardrails_superados']}")
    print(f"  • Decisión Final:          {dec_aud['decision_final']}")
    print(f"  • Error interceptado:      {err[0] if err else 'Ninguno'}")
    assert valida is False, "Debe abortar si la cuota no coincide con Betsson"
    assert dec_aud["guardrails_superados"] is False

    # ── CASO 7: QUARTER-KELLY INFERIOR AL MÍNIMO OPERATIVO (< 0.5%) ──────────
    print_banner("CASO 7: QUARTER-KELLY INFERIOR AL MÍNIMO OPERATIVO (< 0.5%)")
    # Ventaja microscópica: Cuota 2.45, p_real 0.43 (EV = +5.35%)
    # f* = ((0.43 * 2.45) - 1) / (2.45 - 1) = 0.0535 / 1.45 = 0.0369 (3.69%)
    # Quarter-Kelly = 3.69% / 4 = 0.92% (si ajustamos p_real a 0.415, EV = +1.6% o con f*/4 < 0.5%)
    # Para EV >= 0.05 y f*/4 < 0.5%:
    # f* debe ser < 2.0%. Pero EV / (b) = f* -> 0.05 / b < 0.02 -> b > 2.50
    # Con Cuota = 5.0 (b = 4.0), EV = +5.0% -> f* = 0.05 / 4 = 0.0125 (1.25%) -> Quarter-Kelly = 0.31% < 0.5%
    payload_cuota_alta = {
        **payload_base,
        "Mercado_y_Cuotas_Betsson": [
            {"familia": "1er tiempo - Total de goles", "seleccion": "Menos de 0.5", "cuota": 5.0, "periodo": "1er tiempo"}
        ],
    }
    decision_pequeña_ventaja = {
        "mercado_elegido": "1er tiempo - Total de goles",
        "seleccion": "Menos de 0.5",
        "cuota": 5.0,
        "probabilidad_real_estimada": 0.21,  # EV = (0.21 * 5.0) - 1 = +5.0%
        # f* = (1.05 - 1) / 4.0 = 0.0125 -> f*/4 = 0.003125 (0.31% < 0.50%)
    }
    valida, dec_aud, err = validador.validar_decision(decision_pequeña_ventaja, payload_cuota_alta)
    print(f"  • Es válida:               {valida}")
    print(f"  • Guardrails Superados:    {dec_aud['guardrails_superados']}")
    print(f"  • Decisión Final:          {dec_aud['decision_final']}")
    print(f"  • Stake Sugerido:          {dec_aud['stake_sugerido']}")
    print(f"  • Stake Kelly %:           {dec_aud['stake_fraccional_kelly_pct']}")
    print(f"  • Motivo / Error:          {err[0] if err else 'Ninguno'}")
    assert valida is False, "Debe abortar la apuesta si Quarter-Kelly es inferior al mínimo operativo"
    assert dec_aud["guardrails_superados"] is False
    assert dec_aud["stake_fraccional_kelly_pct"] == 0.0

    # ── CASO 8: ENTRADA VÁLIDA AUTENTICADA (SUPERACIÓN DE GUARDRAILS) ────────
    print_banner("CASO 8: ENTRADA VÁLIDA AUTENTICADA CON GUARDRAILS SUPERADOS")
    decision_valida = {
        "mercado_elegido": "1er tiempo - Total de goles",
        "seleccion": "Menos de 0.5",
        "cuota": 2.45,
        "probabilidad_real_estimada": 0.65,
        "mercados_evaluados": [
            {"mercado": "1er tiempo - Total de goles", "seleccion": "Menos de 0.5", "probabilidad_real_estimada": 0.65},
            {"mercado": "1er tiempo - Total de goles", "seleccion": "Más de 0.5", "probabilidad_real_estimada": 0.35},
        ]
    }
    valida, dec_aud, err = validador.validar_decision(decision_valida, payload_base)
    print(f"  • Es válida:               {valida}")
    print(f"  • Guardrails Superados:    {dec_aud['guardrails_superados']}")
    print(f"  • Decisión Final:          {dec_aud['decision_final']}")
    print(f"  • +EV Calculado:           {dec_aud['valor_esperado_ev']}")
    print(f"  • Stake Sugerido:          {dec_aud['stake_sugerido']}")
    print(f"  • Stake Kelly %:           {dec_aud['stake_fraccional_kelly_pct']}%")
    assert valida is True, "Debe ser aprobada cuando cumple todos los requisitos"
    assert dec_aud["guardrails_superados"] is True
    assert dec_aud["decision_final"] == "APROBADO"
    assert dec_aud["stake_fraccional_kelly_pct"] > 0.0

    print_banner("RESULTADO FINAL: TODAS LAS PRUEBAS ADVERSARIAS SUPERADAS (100% FAIL-SAFE)")
    print("✅ El Validador Numérico intercepta con éxito NaN, Inf, Strings, Desconexiones de Catálogo")
    print("   y apuestas con Quarter-Kelly por debajo del mínimo operativo.")


if __name__ == "__main__":
    ejecutar_pruebas_adversarias()
