"""Normalización de textos (regla 9): espacios, mayúsculas y diccionarios verificados.

Nunca se inventan acentos: solo se agregan con un diccionario explícito, y cada
entrada se verifica de forma mecánica (sin acentos ni signos debe ser idéntica a su clave).
"""
import re
import unicodedata

# Palabras que van en minúscula dentro de un nombre propio ("San Nicolás de los Garza").
CONECTORES_TITULO = {"de", "del", "la", "las", "los", "el", "y", "en"}
# Letras sueltas que en español son palabras (las demás letras sueltas son siglas: "tipo B").
LETRAS_PALABRA = {"y", "o", "e", "u", "a"}


def colapsar_espacios(texto: str) -> str:
    """Quita espacios al inicio y al final, y deja uno solo entre palabras."""
    return re.sub(r"\s+", " ", texto or "").strip()


def sin_acentos(texto: str) -> str:
    """Quita acentos y diéresis (también convierte ñ en n)."""
    descompuesto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def clave_comparacion(texto: str) -> str:
    """Forma canónica para comparar textos: mayúsculas, sin acentos y sin signos."""
    limpio = sin_acentos(texto).upper()
    return colapsar_espacios(re.sub(r"[^A-Z0-9]+", " ", limpio))


def texto_busqueda(texto: str) -> str:
    """Forma para buscar: minúsculas y sin acentos, conservando los signos."""
    return colapsar_espacios(sin_acentos(texto).lower())


def _es_sigla(token: str, siglas: set[str]) -> bool:
    """Un token se deja en mayúsculas si es una sigla conocida, si mezcla letras y
    números (B12, G6FD, COVID-19) o si es una letra suelta que no es palabra (tipo B)."""
    base = re.sub(r"[^\wÁÉÍÓÚÜÑáéíóúüñ\-]", "", token)
    if not base:
        return False
    if base.upper() in siglas or base.upper().split("-")[0] in siglas:
        return True
    if len(base) <= 5 and re.search(r"\d", base) and re.search(r"[A-Za-z]", base):
        return True
    return len(base) == 1 and base.isalpha() and base.lower() not in LETRAS_PALABRA


def formato_oracion(texto: str, siglas: set[str] | None = None) -> str:
    """'DIABETES MELLITUS TIPO 2' → 'Diabetes mellitus tipo 2' (conserva siglas)."""
    siglas = siglas or set()
    tokens = []
    for token in colapsar_espacios(texto).split(" "):
        tokens.append(token.upper() if _es_sigla(token, siglas) else token.lower())
    resultado = " ".join(tokens)
    return resultado[:1].upper() + resultado[1:]


def formato_titulo(texto: str, siglas: set[str] | None = None) -> str:
    """'SAN NICOLAS DE LOS GARZA' → 'San Nicolas de los Garza' (conectores en minúscula).

    Un conector justo después de una abreviatura empieza un nombre: 'C S U El Fraile'."""
    siglas = siglas or set()
    tokens = []
    previo_es_palabra = False
    for token in colapsar_espacios(texto).split(" "):
        # En un nombre propio, una letra suelta (salvo "y") es abreviatura: "C S U".
        if _es_sigla(token, siglas) or (len(token) == 1 and token.isalpha() and token.lower() != "y"):
            tokens.append(token.upper())
            previo_es_palabra = False
            continue
        if previo_es_palabra and token.lower() in CONECTORES_TITULO:
            tokens.append(token.lower())
        else:
            tokens.append(token[:1].upper() + token[1:].lower())
        previo_es_palabra = True
    return " ".join(tokens)


def tratar_doble_espacio(texto: str, modo: str) -> list[str]:
    """Divide o corrige según lo que significa el doble espacio en esa columna.

    - "coma": el doble espacio es una coma perdida ("TIPO 2  SIN MENCION" → "TIPO 2, SIN MENCION").
    - "segmento": separa partes de un nombre ("C S U  LA ASCENSION" → ["C S U", "LA ASCENSION"]).
    - "espacio": solo se colapsa.
    Devuelve una lista de segmentos para que cada uno se formatee por separado.
    """
    texto = (texto or "").strip()
    if modo == "coma":
        return [re.sub(r"(?<=\S) {2,}(?=\S)", ", ", texto)]
    if modo == "segmento":
        return [s for s in re.split(r" {2,}", texto) if s.strip()]
    return [texto]


class Diccionario:
    """Diccionario verificado: clave (texto de los datos) → nombre para mostrar.

    Al cargarlo se descartan las entradas que cambian palabras (solo se permiten
    acentos y signos). Las descartadas quedan en `rechazadas` para el reporte."""

    def __init__(self, nombre: str, valores: dict[str, str], fuente: str = ""):
        self.nombre = nombre
        self.fuente = fuente
        self.valores: dict[str, str] = {}
        self.rechazadas: list[tuple[str, str]] = []
        for clave, mostrar in valores.items():
            if clave_comparacion(clave) == clave_comparacion(mostrar):
                self.valores[clave_comparacion(clave)] = mostrar
            else:
                self.rechazadas.append((clave, mostrar))

    def buscar(self, texto: str) -> str | None:
        return self.valores.get(clave_comparacion(texto))

    def nombres(self) -> list[str]:
        return list(self.valores.values())


def normalizar_valor(texto: str, columna: dict, siglas: set[str],
                     diccionario: Diccionario | None = None) -> str:
    """Aplica la regla 9 a un valor según la configuración de su columna."""
    if texto is None:
        return None
    if diccionario is not None:
        encontrado = diccionario.buscar(texto)
        if encontrado:
            return encontrado
    segmentos = tratar_doble_espacio(texto, columna.get("doble_espacio", "espacio"))
    modo = columna.get("normalizar", "ninguno")
    if modo == "oracion":
        return formato_oracion(" ".join(segmentos), siglas)
    if modo == "titulo":
        # Cada segmento es un nombre propio: "C S U" + "La Ascension".
        return colapsar_espacios(" ".join(formato_titulo(s, siglas) for s in segmentos))
    return colapsar_espacios(" ".join(segmentos))
