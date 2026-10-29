"""Prueba del widget de chat (JavaScript) con Node y un DOM simulado. Se omite si no hay Node."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import config


@pytest.mark.skipif(shutil.which("node") is None, reason="Node no está instalado")
def test_widget_dibuja_respuestas_graficas_y_avisos():
    r = subprocess.run(["node", str(Path(__file__).with_name("widget_test.js"))], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_pagina_demo_conserva_aviso_y_no_usa_marca_oficial():
    html = (config.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert "Prototipo de propuesta" in html
    assert "no es el sitio oficial" in html
    assert html.count("Próximamente") == 2  # Educación y Seguridad
    assert "escudo" not in html.lower() and "logo" not in html.lower()
    # Las preguntas de ejemplo ya no son de temas sin datos.
    assert "data-ask=\"¿Cuántas escuelas" not in html and "denuncias el año pasado" not in html


def test_widget_no_usa_innerhtml_con_datos():
    """innerHTML solo se usa con íconos fijos (constantes ICON_*), nunca con texto del servidor."""
    js = (config.FRONTEND_DIR / "chat-widget.js").read_text(encoding="utf-8")
    usos = [l.strip() for l in js.splitlines() if ".innerHTML" in l and not l.strip().startswith("//")]
    assert usos
    for uso in usos:
        valor = uso.split("=", 1)[1].split("//")[0].strip().rstrip(";")
        assert re.fullmatch(r"ICON_[A-Z]+|state \? ICON_[A-Z]+ : ICON_[A-Z]+", valor), uso


def test_ventana_mas_grande_y_etiquetas_sin_recortar():
    css = (config.FRONTEND_DIR / "chat-widget.css").read_text(encoding="utf-8")
    panel = re.search(r"\.dchat-panel \{(.*?)\}", css, re.S).group(1)
    assert "min(560px, calc(100vw - 40px))" in panel and "min(780px" in panel  # sin salirse de la pantalla
    assert '.dchat-panel[data-size="grande"]' in css                             # modo ampliado
    for regla in ("dchat-bar-label", "dchat-col-label"):
        bloque = re.search(rf"\.{regla} \{{(.*?)\}}", css, re.S).group(1)
        assert "ellipsis" not in bloque and "white-space: normal" in bloque, regla
    movil = css[css.index("@media (max-width: 480px)"):]
    assert ".dchat-size { display: none; }" in movil and "100dvh" in movil        # celular: pantalla completa
