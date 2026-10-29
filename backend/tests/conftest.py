"""Fixtures compartidas: una ingesta real en una carpeta temporal (no toca build/)."""
import duckdb
import pytest

import config
import ingest
from catalogo import Catalogo


@pytest.fixture(scope="session")
def build_real(tmp_path_factory):
    """Ingesta completa de data/ en un build temporal."""
    destino = tmp_path_factory.mktemp("build")
    ingest.ingerir(config.DATA_DIR, destino)
    return destino


@pytest.fixture(scope="session")
def catalogo_real(build_real) -> Catalogo:
    return Catalogo.cargar(build_real)


@pytest.fixture(scope="session")
def con_real(build_real):
    con = duckdb.connect(str(build_real / config.NOMBRE_BASE), read_only=True)
    yield con
    con.close()


@pytest.fixture(scope="session")
def motor(con_real, catalogo_real):
    from consultas import MotorConsultas
    return MotorConsultas(con_real, catalogo_real, 200)


@pytest.fixture(scope="session")
def salud(catalogo_real) -> dict:
    """Entrada del catálogo del dataset Consulta General y Especialidades (hay más de un dataset de Salud)."""
    return catalogo_real.obtener("salud_consulta_general")


@pytest.fixture(scope="session")
def bucal(catalogo_real) -> dict:
    """Entrada del catálogo del dataset Salud Bucal."""
    return catalogo_real.obtener("salud_bucal")


@pytest.fixture(scope="session")
def mental(catalogo_real) -> dict:
    """Entrada del catálogo del dataset Salud Mental."""
    return catalogo_real.obtener("salud_mental")
