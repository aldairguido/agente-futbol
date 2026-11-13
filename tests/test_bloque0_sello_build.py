"""
tests/test_bloque0_sello_build.py
Pruebas del Bloque 0.2:
  - Formato y exactitud del sello "build <hash corto> · fecha".
  - Presencia obligatoria del sello en los PNG (Ticket, Ticket Valor, Cuadro Especialista,
    Cuadro Auditoría 15P, Ticket de Abstención) y en los textos de Telegram.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import matplotlib.figure
from PIL import ImageDraw

import cuadro_especialista as ce
import informe as informe_mod
import motor_inferencia as mi
import motor_mercados as mm
import servicio_mercados as servicio
from utilidades_seguridad import obtener_hash_corto_git, obtener_sello_build


ROOT = Path(__file__).resolve().parent.parent


def _cargar_auditoria_man_city_nottingham() -> tuple[dict, dict]:
    p_loc = ROOT / "informes/auditorias/2026-11-07/auditoria_manchester_city_2026-11-07_190812.json"
    p_vis = ROOT / "informes/auditorias/2026-11-07/auditoria_nottingham_forest_2026-11-07_190714.json"
    doc_l = json.loads(p_loc.read_text(encoding="utf-8"))
    doc_v = json.loads(p_vis.read_text(encoding="utf-8"))
    return doc_l, doc_v


def test_formato_sello_build_coincide_con_git():
    hash_git = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(ROOT),
        text=True,
    ).strip()
    assert obtener_hash_corto_git(str(ROOT)) == hash_git[:7]
    sello = obtener_sello_build(str(ROOT))
    assert re.match(rf"^build {re.escape(hash_git[:7])} · \d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}$", sello), sello


def test_sello_build_en_textos_telegram_y_pngs(tmp_path):
    doc_l, doc_v = _cargar_auditoria_man_city_nottingham()
    analisis = mm.analizar(
        doc_l,
        doc_v,
        "Manchester City",
        "Nottingham Forest",
        fecha_partido="2026-11-20",
        competicion="Premier League",
    )
    hash_corto = obtener_hash_corto_git(str(ROOT))
    patron_sello = re.compile(rf"build {re.escape(hash_corto)} · \d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}")

    # 1. Textos de Telegram
    assert patron_sello.search(analisis.mensaje), analisis.mensaje[:300]
    txt_3_parrafos = informe_mod.redactar_analisis_3_parrafos(
        analisis, doc_local=doc_l, doc_visitante=doc_v
    )
    assert patron_sello.search(txt_3_parrafos), txt_3_parrafos[-200:]
    txt_xai = mi.generar_reporte_xai_telegram(
        "Manchester City",
        "Nottingham Forest",
        {},
        {},
        {"local": 0.65, "empate": 0.20, "visita": 0.15},
        [],
        {},
        {},
        {},
        {},
    )
    assert patron_sello.search(txt_xai), txt_xai[:300]

    # 2. Captura de textos dibujados en PNGs
    textos_pil: list[str] = []
    orig_pil_text = ImageDraw.ImageDraw.text

    def spy_pil_text(self, xy, text, *args, **kwargs):
        textos_pil.append(str(text))
        return orig_pil_text(self, xy, text, *args, **kwargs)

    textos_mpl: list[str] = []
    orig_fig_text = matplotlib.figure.Figure.text

    def spy_fig_text(self, x, y, s, *args, **kwargs):
        textos_mpl.append(str(s))
        return orig_fig_text(self, x, y, s, *args, **kwargs)

    with patch.object(ImageDraw.ImageDraw, "text", spy_pil_text), patch.object(
        matplotlib.figure.Figure, "text", spy_fig_text
    ):
        ruta_ticket = servicio.renderizar_ticket_mercados_png(
            analisis, destino=str(tmp_path / "ticket.png")
        )
        assert Path(ruta_ticket).is_file() and Path(ruta_ticket).stat().st_size > 1000
        assert any(patron_sello.search(t) for t in textos_pil), "Falta sello build en Ticket PNG"

        textos_pil.clear()
        analisis_abs = type("Abst", (), {
            "metadatos": {
                "equipo_local": "Equipo A",
                "equipo_visitante": "Equipo B",
                "competicion": "Liga",
                "estado_cobertura": "cobertura_insuficiente",
                "etiqueta_cobertura_local": "0/15",
                "etiqueta_cobertura_visitante": "0/15",
                "motivo_abstencion": "Prueba de abstención",
            },
            "abstenido": True,
            "selecciones_filtradas": [],
            "grupos": {"mantiene": [], "sensible": []},
            "motor": None,
        })()
        ruta_abs = servicio.renderizar_ticket_mercados_png(
            analisis_abs, destino=str(tmp_path / "ticket_abs.png")
        )
        assert Path(ruta_abs).is_file()
        assert any(patron_sello.search(t) for t in textos_pil), "Falta sello build en Ticket Abstención PNG"

        textos_mpl.clear()
        ruta_cuadro = ce.generar_cuadro_desde_analisis(
            analisis, doc_l, doc_v, destino=str(tmp_path / "cuadro.png")
        )
        assert Path(ruta_cuadro).is_file()
        assert any(patron_sello.search(t) for t in textos_mpl), "Falta sello build en Cuadro Especialista PNG"

        textos_mpl.clear()
        ruta_aud = ce.generar_cuadro_auditoria_15_partidos_png(
            analisis, doc_l, doc_v, destino=str(tmp_path / "aud15p.png")
        )
        assert Path(ruta_aud).is_file()
        assert any(patron_sello.search(t) for t in textos_mpl), "Falta sello build en Cuadro Auditoría 15P PNG"

        textos_mpl.clear()
        ruta_val = informe_mod.generar_ticket_valor_png(
            analisis,
            cuotas_flashscore={"local": 1.65, "empate": 4.0, "visitante": 5.2},
            destino=str(tmp_path / "ticket_valor.png"),
        )
        assert Path(ruta_val).is_file()
        assert any(patron_sello.search(t) for t in textos_mpl), "Falta sello build en Ticket Valor PNG"
