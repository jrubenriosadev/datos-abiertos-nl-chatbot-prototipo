"""Protocolo de crisis: detección en el backend ANTES de llamar al modelo y mensajes fijos.

Los textos, los teléfonos y las frases de detección viven en config/crisis.toml; aquí solo está
la forma de buscarlas. El texto se normaliza igual que las frases (sin acentos, letras repetidas,
abreviaturas de chat y plegado fonético) y las palabras largas se corrigen si traen una letra de
más, de menos o cambiada. Solo se buscan frases en primera persona, para que las preguntas
estadísticas ("¿cuántas consultas por depresión hubo?") se respondan con datos.
"""
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import config
from normalizar import texto_busqueda

MENSAJES_REQUERIDOS = ("crisis", "tercero", "orientacion", "linea_apoyo", "despues_de_crisis")
LISTAS_DETECCION = ("riesgo", "tercero", "malestar", "orientacion")
# Qué mensaje se responde según la lista que coincidió (en este orden de prioridad).
RESPUESTA_DE = {"riesgo": "crisis", "tercero": "tercero", "malestar": "crisis", "orientacion": "orientacion"}
MACROS = {"<palabras>": r"(?:[a-z]+ ){0,5}"}
MAX_TEXTO = 2000   # solo se revisan los primeros caracteres (el mensaje se limita después)


class ErrorProtocolo(Exception):
    """La configuración del protocolo falta o no es válida: el servidor no debe arrancar."""


@dataclass
class Deteccion:
    tipo: str    # crisis | tercero | orientacion
    regla: str   # lista y número de la frase que coincidió (para la bitácora; nunca el texto)


def plegar(texto: str) -> str:
    """Plegado fonético del español escrito: 'Kiero suisidarme' y 'quiero suicidarme' quedan iguales."""
    t = re.sub(r"qu(?=[ei])", "k", texto)
    t = t.replace("q", "k")
    t = re.sub(r"c(?=[ei])", "s", t)
    t = re.sub(r"c(?=[aou])", "k", t)
    t = t.replace("z", "s").replace("v", "b").replace("ll", "y")
    t = re.sub(r"(?<!c)h", "", t)
    return re.sub(r"([a-z])\1+", r"\1", t)   # letras repetidas: "muuuy" → "muy", "rr" → "r"


def _distancia(a: str, b: str, tope: int) -> int:
    """Distancia de edición (Levenshtein); devuelve tope + 1 en cuanto se pasa."""
    if abs(len(a) - len(b)) > tope:
        return tope + 1
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + (ca != cb)))
        if min(actual) > tope:
            return tope + 1
        previa = actual
    return previa[-1]


class ProtocoloCrisis:
    def __init__(self, datos: dict):
        contactos = datos.get("contactos", {})
        if not contactos or not all(c.get("telefono") and c.get("nombre") for c in contactos.values()):
            raise ErrorProtocolo("Cada contacto de [contactos] necesita 'nombre' y 'telefono'.")
        self.contactos = [{"nombre": c["nombre"], "telefono": c["telefono"], "detalle": c.get("detalle", "")}
                          for c in contactos.values()]
        espacio = {k: SimpleNamespace(**v) for k, v in contactos.items()}
        mensajes = datos.get("mensajes", {})
        faltan = [m for m in MENSAJES_REQUERIDOS if not mensajes.get(m)]
        if faltan:
            raise ErrorProtocolo(f"Faltan mensajes en [mensajes]: {faltan}.")
        try:
            self.mensajes = {k: v.format_map(espacio).strip() for k, v in mensajes.items()}
        except (KeyError, AttributeError) as e:
            raise ErrorProtocolo(f"Un mensaje menciona un contacto o campo que no existe: {e}.") from None

        norm = datos.get("normalizacion", {})
        self.abreviaturas = {texto_busqueda(k): texto_busqueda(v) for k, v in norm.get("abreviaturas", {}).items()}
        self.corregibles = {plegar(texto_busqueda(p)) for p in norm.get("corregibles", [])}

        deteccion = datos.get("deteccion", {})
        faltan = [l for l in LISTAS_DETECCION if not deteccion.get(l)]
        if faltan:
            raise ErrorProtocolo(f"Faltan listas en [deteccion]: {faltan}.")
        self.excepciones = [self._compilar(p) for p in deteccion.get("excepciones", [])]
        self.listas = {nombre: [self._compilar(p) for p in deteccion[nombre]] for nombre in LISTAS_DETECCION}
        # Palabras que indican una pregunta de salud mental (el consejo médico se responde con 'orientacion').
        self.temas_salud_mental = [self._compilar(p) for p in deteccion.get("temas_salud_mental", [])]

    @classmethod
    def cargar(cls, ruta: Path = config.RUTA_CRISIS) -> "ProtocoloCrisis":
        if not ruta.exists():
            raise ErrorProtocolo(f"No existe {ruta}: el protocolo de crisis es obligatorio.")
        try:
            return cls(tomllib.loads(ruta.read_text(encoding="utf-8")))
        except tomllib.TOMLDecodeError as e:
            raise ErrorProtocolo(f"{ruta.name} no es TOML válido: {e}.") from None

    @staticmethod
    def _compilar(patron: str) -> re.Pattern:
        """La frase se pliega igual que el texto; las macros (<palabras>) se ponen después del plegado."""
        # Sin letra después, una "c" no se plegaría como en el texto ("esquizofrenic[oa]" nunca coincidiría).
        if re.search(r"c(?![a-z])", texto_busqueda(patron)):
            raise ErrorProtocolo(f"En {patron!r} hay una 'c' sin letra después: escribe las palabras completas.")
        p = plegar(texto_busqueda(patron))
        for macro, regex in MACROS.items():
            p = p.replace(macro, regex)
        try:
            return re.compile(rf"(?<![a-z])(?:{p})(?![a-z])")
        except re.error as e:
            raise ErrorProtocolo(f"Frase de detección no válida {patron!r}: {e}.") from None

    # ---------- Normalización ----------

    def _corregir(self, token: str) -> str:
        """Corrige una palabra larga mal escrita hacia una de 'corregibles' (si solo hay una candidata)."""
        if len(token) < 6 or token in self.corregibles:
            return token
        tope = 2 if len(token) >= 9 else 1
        candidatas = [c for c in self.corregibles if _distancia(token, c, tope) <= tope]
        # "matarse" no es "matarme": no se cambia la persona del pronombre.
        candidatas = [c for c in candidatas if not (c.endswith("me") and token[-2:] in ("se", "te", "lo", "la", "le")
                                                    and token[:-2] == c[:-2])]
        return candidatas[0] if len(candidatas) == 1 else token

    def normalizar(self, texto: str) -> str:
        t = re.sub(r"[^a-z0-9ñ]+", " ", texto_busqueda(str(texto or "")[:MAX_TEXTO]))
        tokens = []
        for token in t.split():
            for parte in self.abreviaturas.get(token, token).split():
                tokens.append(self._corregir(plegar(parte)))
        return " ".join(tokens)

    # ---------- Detección ----------

    def detectar(self, mensaje: str) -> Deteccion | None:
        t = self.normalizar(mensaje)
        for p in self.excepciones:
            t = p.sub(" ", t)
        for nombre in LISTAS_DETECCION:
            for i, p in enumerate(self.listas[nombre]):
                if p.search(t):
                    return Deteccion(RESPUESTA_DE[nombre], f"{nombre}:{i}")
        return None

    def mensaje(self, tipo: str) -> str:
        return self.mensajes[tipo]

    @property
    def linea_apoyo(self) -> str:
        return self.mensajes["linea_apoyo"]

    def linea_vida_en(self, texto: str) -> bool:
        """¿El texto incluye alguno de los teléfonos del protocolo?"""
        return any(c["telefono"] in (texto or "") for c in self.contactos)

    def con_linea_apoyo(self, texto: str) -> str:
        """Agrega la línea de apoyo al final (una sola vez)."""
        if self.linea_apoyo in texto:
            return texto
        return f"{texto.rstrip()}\n\n{self.linea_apoyo}"
