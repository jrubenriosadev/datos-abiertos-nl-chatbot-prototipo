"""Rutas del proyecto y configuración leída de .env.

La API key se lee de .env y nunca se imprime ni se escribe en otro lugar.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Raíz del proyecto (la carpeta que contiene backend/, data/ y frontend/).
RAIZ = Path(__file__).resolve().parent.parent
DATA_DIR = RAIZ / "data"
BUILD_DIR = RAIZ / "build"
LOGS_DIR = RAIZ / "logs"
FRONTEND_DIR = RAIZ / "frontend"
ASSETS_DIR = RAIZ / "assets"
CONFIG_DIR = RAIZ / "config"
RUTA_CRISIS = CONFIG_DIR / "crisis.toml"   # textos y teléfonos del protocolo de crisis

# Archivos generados por la ingesta.
NOMBRE_BASE = "datos.duckdb"
NOMBRE_CATALOGO = "catalog.json"
NOMBRE_REPORTE = "reporte_ingesta.md"

# Tope absoluto de filas por consulta, aunque .env pida más.
TOPE_FILAS = 200


def _entero(nombre: str, defecto: int, minimo: int = 1) -> int:
    try:
        return max(minimo, int(os.getenv(nombre, defecto)))
    except ValueError:
        return defecto


@dataclass(frozen=True)
class Ajustes:
    gemini_model: str = "gemini-3.1-flash-lite"
    # repr=False: la clave nunca aparece al imprimir o registrar los ajustes.
    gemini_api_key: str = field(default="", repr=False)
    host: str = "127.0.0.1"
    port: int = 8000
    allowed_origins: tuple[str, ...] = ("http://localhost:8000",)
    max_message_length: int = 500
    max_messages_per_session: int = 30
    rate_limit_per_minute: int = 20
    max_output_tokens: int = 1200
    max_tool_calls_per_turn: int = 6
    max_rows_per_query: int = 200

    @property
    def modelo_configurado(self) -> bool:
        return bool(self.gemini_api_key) and self.gemini_api_key != "pon_aqui_tu_clave"


def cargar_ajustes(ruta_env: Path = RAIZ / ".env") -> Ajustes:
    """Lee .env (sin sobrescribir variables ya definidas en el sistema)."""
    load_dotenv(ruta_env, override=False)
    origenes = tuple(o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
                     if o.strip() and o.strip().lower() != "null")  # el origen "null" nunca se permite
    return Ajustes(
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        host=os.getenv("HOST", "127.0.0.1").strip(),
        port=_entero("PORT", 8000),
        allowed_origins=origenes or ("http://localhost:8000", "http://127.0.0.1:8000"),
        max_message_length=_entero("MAX_MESSAGE_LENGTH", 500),
        max_messages_per_session=_entero("MAX_MESSAGES_PER_SESSION", 30),
        rate_limit_per_minute=_entero("RATE_LIMIT_PER_MINUTE", 20),
        max_output_tokens=_entero("MAX_OUTPUT_TOKENS", 1200, minimo=256),
        max_tool_calls_per_turn=_entero("MAX_TOOL_CALLS_PER_TURN", 6),
        max_rows_per_query=min(TOPE_FILAS, _entero("MAX_ROWS_PER_QUERY", 200)),
    )
