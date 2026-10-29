"""Dibuja una respuesta del backend con el widget real (Node + DOM simulado) y devuelve lo que se vio."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HAY_NODE = shutil.which("node") is not None
requiere_node = pytest.mark.skipif(not HAY_NODE, reason="Node no está instalado")


def dibujar(respuesta: dict) -> dict:
    """{grafica_dibujada, titulo, barras, etiquetas, tabla_dibujada, filas_tabla, texto_visible}."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(respuesta, f, ensure_ascii=False)
        ruta = f.name
    try:
        r = subprocess.run(["node", str(Path(__file__).with_name("dibujar_respuesta.js")), ruta],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)
    finally:
        Path(ruta).unlink(missing_ok=True)
