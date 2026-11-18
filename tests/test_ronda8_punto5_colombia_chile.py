"""
Tests de verificación de Ronda 8 — Punto 5:
Auditoría forense de Colombia vs Chile (12/11):
  (a) n real y lista de partidos de cada grupo casa/fuera (Colombia: 7 Local / 8 Visita;
      Chile: 6 Local / 9 Visita; corrige N=8 y N=6 en lecturas de Doble ADN y etiqueta 7C/9V).
  (b) PortRiv (-11.9%) y Finaliz (-8.2%) con intervalo de confianza al 95%; ambos contienen 0
      (no superan el ruido) y cuando se activa el filtro de ruido IC95 no mueven el λ.
  (c) Filtros exactos que quitaron cada línea >= 70% (1X 72.8%, 1T Menos de 1.5 75.7%/72.5%)
      y coherencia exacta de cifra entre Matriz, Veredicto y Top 5 / Sección 1.
  (d) Cuadro C de Chile: 9 goles a favor (3 en 1T + 6 en 2T) y 11 en contra (7 en 1T + 4 en 2T)
      según mitades verificadas en la matriz (no 7 y 9), y conteo real de partidos evaluables
      (9/15 según mitades verificadas en matriz y 7/15 con minutos + mitades, nunca 13/15).
"""

from __future__ import annotations

from evaluacion_pronosticos import auditar_colombia_chile_ronda8


def test_ronda8_punto5_colombia_chile_completo():
    res = auditar_colombia_chile_ronda8()

    # ── 5(a): n real y lista de partidos casa/fuera ──────────────────────────
    p5a = res["punto_5a_grupos_casa_fuera"]
    col = p5a["colombia"]
    chi = p5a["chile"]
    assert col["n_total"] == 15
    assert col["n_casa_local"] == 7
    assert col["n_fuera_visita"] == 8
    assert len(col["partidos_casa_local"]) == 7
    assert len(col["partidos_fuera_visita"]) == 8

    assert chi["n_total"] == 15
    assert chi["n_casa_local"] == 6
    assert chi["n_fuera_visita"] == 9
    assert len(chi["partidos_casa_local"]) == 6
    assert len(chi["partidos_fuera_visita"]) == 9

    lectura_v1 = p5a["lectura_control_y_asedio_corregida"]
    assert "[Local N=7] vs [Visita N=9]" in lectura_v1
    assert "[Local N=8]" not in lectura_v1
    assert "[Visita N=6]" not in lectura_v1
    assert p5a["etiqueta_muestra_patrones"] == "Doble ADN pond. (7C/9V)"

    # ── 5(b): PortRiv y Finaliz con intervalo de confianza al 95% ───────────
    p5b = res["punto_5b_factores_con_ic"]
    port_riv = p5b["PortRiv_local"]
    finaliz = p5b["Finaliz_local"]

    # En A+B reescalado el ticket mostró PortRiv = -11.9% y Finaliz = -8.2% (sin reescalar: -3.9% y +0.9%)
    assert abs(port_riv["factor_ticket_ab_reescalado_pct"] - (-11.9)) <= 0.2
    assert abs(finaliz["factor_ticket_ab_reescalado_pct"] - (-8.2)) <= 0.2
    assert abs(port_riv["factor_sin_reescalar_pct"] - (-3.9)) <= 0.2
    assert abs(finaliz["factor_sin_reescalar_pct"] - 0.9) <= 0.2

    # Ambos intervalos de confianza al 95% incluyen el 0 -> no superan el ruido
    ic_port_cond = port_riv["insumo_cond_ic95"]
    ic_port_15p = port_riv["insumo_15p_ic95"]
    assert ic_port_cond[0] < 0.0 < ic_port_cond[1]
    assert ic_port_15p[0] < 0.0 < ic_port_15p[1]
    assert port_riv["supera_ruido"] is False

    ic_fin_cond = finaliz["insumo_cond_ic95"]
    ic_fin_15p = finaliz["insumo_15p_ic95"]
    assert ic_fin_cond[0] < 0.0 < ic_fin_cond[1]
    assert ic_fin_15p[0] < 0.0 < ic_fin_15p[1]
    assert finaliz["supera_ruido"] is False

    # Con el filtro de ruido IC95 activo, ninguno de los dos mueve el λ (quedan en 0.0%)
    assert port_riv["factor_con_filtro_ruido_pct"] == 0.0
    assert finaliz["factor_con_filtro_ruido_pct"] == 0.0

    # ── 5(c): Filtros que quitaron líneas >= 70% y coherencia de cifras ─────
    p5c = res["punto_5c_filtros_y_coherencia"]
    assert abs(p5c["prob_1x_matriz_pct"] - 72.8) <= 0.1

    lineas_ab = {x["etiqueta"]: x for x in p5c["lineas_70_A_mas_B"]}
    # En Ronda 10 Punto 7 se corrige VETO 1X (brecha con signo) y frecuencia 1T real:
    # 1X (72.8% < 93.3%, brecha -20.5 pp) ya no recibe veto por calibración y entra a Sección 1;
    # 1T Menos de 1.5 (75.7% vs 85.7% real en 1T) entra a Sección 1 y Top 5 en A+B.
    assert "1X (Colombia o empate)" in lineas_ab
    info_1x = lineas_ab["1X (Colombia o empate)"]
    assert abs(info_1x["prob_pct"] - 72.8) <= 0.1
    assert info_1x["en_seccion1"] is True
    assert info_1x["en_top5"] is True

    assert "1T Menos de 1.5 goles" in lineas_ab
    info_u15_1t = lineas_ab["1T Menos de 1.5 goles"]
    assert abs(info_u15_1t["prob_pct"] - 75.7) <= 0.2
    assert info_u15_1t["en_seccion1"] is True
    assert info_u15_1t["en_top5"] is True

    # El veredicto sin Top 5 usa exactamente 72.8% (y nunca 72.0% por el antiguo -4.5 pp)
    assert "72.8%" in p5c["veredicto_sin_top_c1"]
    assert "72.0% (Base Matriz Poisson: 72.8%)" not in p5c["veredicto_sin_top_c1"]
    # Y cuando usa el Top #1 (ahora 1X al 72.8% tras corregir VETO 1X en Ronda 10), usa la misma cifra en ambos lados
    assert "72.8% (Base Matriz Poisson: 72.8%)" in p5c["veredicto_con_top_c1"]

    # Y la fila HÁNDICAP / 1X2 / DC de Sección 1 refleja 1X (Colombia o X) validada por brecha con signo
    pildoras_hc = p5c["fila_handicap_1x2_dc_sec1"].get("pildoras") or []
    assert len(pildoras_hc) >= 1
    assert "1X" in pildoras_hc[0]["lbl"]

    # ── 5(d): Cuadro C de Chile: 9 GF y 11 GC según mitades, no 7 y 9; evaluables reales ──
    p5d = res["punto_5d_cuadro_c_chile"]
    meta_chi = p5d["meta_chile"]
    assert p5d["total_anotados_chile"] == 9
    assert p5d["total_recibidos_chile"] == 11
    assert meta_chi["goles_favor_mitades"] == 9
    assert meta_chi["goles_1t_favor_mitades"] == 3
    assert meta_chi["goles_2t_favor_mitades"] == 6
    assert meta_chi["goles_contra_mitades"] == 11
    assert meta_chi["goles_1t_contra_mitades"] == 7
    assert meta_chi["goles_2t_contra_mitades"] == 4
    assert meta_chi["n_partidos_con_mitades"] == 9
    assert meta_chi["n_partidos_con_eventos_y_mitades"] == 7
    assert meta_chi["n_partidos_con_eventos"] == 9
    assert meta_chi["n_partidos_con_eventos"] != 13
