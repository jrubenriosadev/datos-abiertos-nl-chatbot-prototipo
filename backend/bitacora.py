"""Bitácora local en logs/ (JSONL): preguntas rechazadas y errores, sin datos personales.

- La sesión se guarda como un hash corto, nunca el identificador original.
- No se guarda la IP.
- El texto se redacta (correos, teléfonos, CURP, números largos, claves) y se recorta.
"""
import datetime as dt
import hashlib
import json
import re
import threading
from pathlib import Path

import config

_candado = threading.Lock()
_carpeta: Path | None = None   # None = config.LOGS_DIR (las pruebas usan una carpeta temporal)
MAX_TEXTO = 200


def configurar(carpeta: Path | None):
    global _carpeta
    _carpeta = carpeta

_REDACCIONES = [
    (re.compile(r"AIza[0-9A-Za-z\-_]{20,}"), "[clave]"),                      # claves de Google
    (re.compile(r"(?i)(api[_-]?key|key|token)\s*[=:]\s*\S+"), r"\1=[clave]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[correo]"),
    (re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b", re.I), "[curp]"),
    (re.compile(r"\b[A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3}\b", re.I), "[rfc]"),
    (re.compile(r"\+?\d[\d\s\-()]{8,}\d"), "[telefono]"),
    (re.compile(r"\b\d{6,}\b"), "[numero]"),
]


def redactar(texto: str) -> str:
    texto = str(texto or "")
    for patron, reemplazo in _REDACCIONES:
        texto = patron.sub(reemplazo, texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:MAX_TEXTO] + ("…" if len(texto) > MAX_TEXTO else "")


def hash_sesion(sesion: str) -> str:
    return hashlib.sha256(str(sesion or "").encode()).hexdigest()[:12]


def _escribir(nombre: str, registro: dict, carpeta: Path | None = None):
    carpeta = carpeta or _carpeta or config.LOGS_DIR
    registro = {"fecha": dt.datetime.now().isoformat(timespec="seconds"), **registro}
    with _candado:
        carpeta.mkdir(parents=True, exist_ok=True)
        with open(carpeta / nombre, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def registrar_rechazo(tipo: str, regla: str, mensaje: str, sesion: str, carpeta: Path | None = None):
    _escribir("rechazos.jsonl", {"tipo": tipo, "regla": regla, "sesion": hash_sesion(sesion),
                                 "texto": redactar(mensaje)}, carpeta)


def registrar_apoyo(tipo: str, regla: str, sesion: str, carpeta: Path | None = None):
    """Protocolo de crisis: solo el tipo y la regla que lo activó. Nunca se guarda el texto del mensaje."""
    _escribir("apoyo.jsonl", {"tipo": tipo, "regla": regla, "sesion": hash_sesion(sesion)}, carpeta)


def registrar_error(contexto: str, error: BaseException | str, sesion: str = "", carpeta: Path | None = None):
    _escribir("errores.jsonl", {"contexto": contexto,
                                "tipo_error": type(error).__name__ if isinstance(error, BaseException) else "mensaje",
                                "detalle": redactar(str(error)), "sesion": hash_sesion(sesion) if sesion else ""}, carpeta)
