"""Restricción de uso: filtro previo, mensajes fijos, límites y verificador de cifras.

Es una defensa en capas, no una garantía al 100%: lo claramente ajeno se responde aquí
con un mensaje fijo (sin llamar al modelo) y lo ambiguo pasa al modelo, que tiene sus
propias reglas. Las palabras clave de cada categoría y dataset vienen del catálogo; los
textos y teléfonos del protocolo de crisis, de config/crisis.toml (ver crisis.py).
"""
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from catalogo import Catalogo
from crisis import ProtocoloCrisis
from normalizar import texto_busqueda

SESION_VALIDA = re.compile(r"^[A-Za-z0-9\-_]{8,64}$")


@dataclass
class Rechazo:
    tipo: str        # fuera_de_alcance | instrucciones | consejo_medico | privacidad | proximamente | no_disponible | saludo | agradecimiento
    regla: str       # qué patrón lo activó (para la bitácora)
    respuesta: str   # mensaje fijo para la persona
    contactos: bool = False   # el mensaje incluye líneas de apoyo (el widget las muestra como enlaces)


# ---------- Patrones (se comparan sin acentos y en minúsculas) ----------

def _p(*patrones: str) -> list[re.Pattern]:
    return [re.compile(p) for p in patrones]


PATRONES_INSTRUCCIONES = _p(
    r"\b(ignora|ignorar|olvida|olvidar|omite|salta(te)?)\b.{0,40}\b(instrucciones|reglas|indicaciones|restricciones|prompt)\b",
    r"\b(system|sistema)\s*prompt\b", r"\bprompt del sistema\b",
    r"\b(muestra|muestrame|ensena|ensename|dime|revela|revelame|repite|imprime|cual es|cuales son)\b.{0,30}\b(tu|tus|el|las)\s+(prompt|instrucciones|reglas internas|configuracion)\b",
    r"\btus instrucciones\b", r"\btu prompt\b",
    r"\b(actua|actuar|comportate|finge|fingir|haz de cuenta)\b.{0,20}\b(como|que eres|ser)\b",
    r"\b(a partir de ahora|desde ahora)\b.{0,20}\beres\b", r"\bmodo (desarrollador|developer|dios|sin restricciones)\b",
    r"\bjailbreak\b", r"\bdan\b.{0,10}\bmode\b", r"\bignore (all|previous|your|the) (instructions|rules)\b",
)

PATRONES_CONSEJO_MEDICO = _p(
    r"\bme duele\b", r"\bme (siento|duele|arde|pica|sangran?)\b",
    r"\btengo (dolor|fiebre|tos|gripa|gripe|diarrea|sintomas|comezon|nauseas|vomito|mareos?|caries|sarro|una? (bola|mancha|herida|caries|muela picada|infeccion|absceso))\b",
    r"\bmis sintomas\b", r"\b(que|cual) (medicamento|medicina|pastilla|antibiotico|remedio|tratamiento)\b",
    r"\b(que|cuanto) (debo|puedo|tengo que) tomar\b", r"\bque (me )?(recomiendas|recetas|aconsejas)\b",
    r"\bcomo (me )?(curo|alivio|quito)\b", r"\bcomo se (cura|trata|quita|alivia)\b",
    r"\b(tratamiento|cura|remedio)s? (para|contra|de) (la |el |mi |una |un )?\w+",
    r"\b(diagnosticame|diagnostica|que enfermedad tengo|estoy enfermo|estoy enferma)\b",
    r"\bdosis\b", r"\bes grave (si|que)\b", r"\bdebo ir al (medico|doctor|hospital|dentista|odontologo|odontologa)\b",
    # Consejos dentales: cuidados, productos y tratamientos.
    r"\b(me|le|nos) (saco|sacan|quito|quitan|extraigo|extraen|arreglo|arreglan|tapo|tapan) (la |una |el |un |mi |su )?(muela|diente|caries)\b",
    r"\b(que|cual|cuales) (pasta|cepillo|enjuague|hilo)s?( dental(es)?| de dientes)?\b",
    r"\b(cada cuando|cuantas veces) (debo|hay que|me tengo que|se deben?|tengo que)\b",
    r"\b(debo|tengo que|hay que) (cepillar(me)?|lavar(me)?) (los )?dientes\b",
    r"\b(me conviene|necesito|debo)( ponerme| usar| hacerme)? (brackets|frenos|ortodoncia|una endodoncia|una protesis|una limpieza)\b",
)

PATRONES_PRIVACIDAD = _p(
    r"\bid[_ ]?consulta\b", r"\bid de (la )?consulta\b", r"\b(consulta|registro|fila)s? individual(es)?\b",
    r"\b(nombre|direccion|telefono|curp|domicilio|expediente)\b.{0,20}\b(paciente|persona|de quien)\b",
    r"\bquien (es|fue|era) (el|la) (paciente|persona)\b", r"\bdatos (personales|de (una|la|esa) persona)\b",
    r"\bfilas? (completas|crudas|originales)\b", r"\b(descarga|dame|muestrame|pasame) (la base|el csv|todos los registros|los registros)\b",
    r"\b(un|una|el|la) (hombre|mujer|senor|senora|nino|nina|paciente|persona|joven)\b.{0,15}\bde \d{1,3} anos\b.{0,60}\b(el|del) \d{1,2} de [a-z]+",
)

PATRONES_FUERA_DE_ALCANCE = _p(
    r"\breceta (de|para) (cocina|pastel|comida|postre|pizza|tacos|galletas|pan|carne|pollo|pescado|sopa)\b",
    r"\b(como (se )?(cocina|prepara|hornea)|ingredientes)\b",
    r"\b(python|javascript|java|html|css|c\+\+|php|sql|programar|programacion|algoritmo|script|codigo fuente)\b",
    r"\b(escribe|escribeme|redacta|hazme|haz)\b.{0,15}\b(codigo|programa|ensayo|poema|cuento|carta|cancion|resumen|tarea|historia)\b",
    r"\b(mi|una|la|esta) tarea\b", r"\b(ensayo|chiste|poema|cancion|cuento|adivinanza)\b",
    r"\bresuelve\b", r"\becuacion\b", r"\btraduce\b",
    r"\b(presidente|presidenta|gobernador|gobernadora|elecciones?|candidat[oa]s?|partido politico|diputad[oa]s?|senador(a|es)?|alcalde(sa)?|votar|politica|politico)\b",
    r"\bque (opinas|piensas)\b", r"\btu opinion\b",
    r"\b(futbol|pelicula|horoscopo|clima|bitcoin|criptomoneda|apuestas?|novia|novio)\b",
)

SALUDOS = re.compile(r"^(hola|holi|buenas|buenos dias|buenas tardes|buenas noches|que tal|hey|saludos|hi|hello)[\s!.,¡¿?]*$")
AGRADECIMIENTOS = re.compile(r"^(muchas )?(gracias|grax|ok gracias|perfecto gracias|mil gracias)[\s!.,]*$")

# Palabras generales que indican que se habla de datos (además del vocabulario del catálogo).
PALABRAS_DE_DATOS = {
    "cuantos", "cuantas", "cuanto", "cuanta", "numero", "total", "totales", "datos", "dato", "grafica", "graficas",
    "graficar", "grafico", "porcentaje", "proporcion", "promedio", "mediana", "comparar", "compara", "comparacion",
    "tendencia", "evolucion", "mes", "meses", "mensual", "ano", "anos", "periodo", "municipio", "municipios",
    "estado", "comun", "comunes", "frecuente", "frecuentes", "ranking", "principales", "top",
    "estadistica", "estadisticas", "cifras", "cifra", "registro", "registros", "conjunto", "dataset", "fuente",
    "disponible", "disponibles", "consultar", "informacion", "edad", "edades", "sexo", "mujeres", "hombres",
}


class Guardrails:
    def __init__(self, catalogo: Catalogo, protocolo: ProtocoloCrisis | None = None):
        self.catalogo = catalogo
        self.protocolo = protocolo or ProtocoloCrisis.cargar()
        self.vocabulario: set[str] = set()
        self.claves_datasets: set[str] = set()
        # Lenguaje que nunca debe salir en una respuesta y palabras de los temas que piden línea de apoyo.
        self.lenguaje_evitar: set[str] = set()
        self.palabras_apoyo: set[str] = set()
        for ds in catalogo.datasets.values():
            self.vocabulario |= set(ds.get("vocabulario", []))
            self.claves_datasets |= {texto_busqueda(p) for p in ds.get("palabras_clave", [])}
            self.lenguaje_evitar |= {texto_busqueda(p) for p in ds.get("lenguaje_evitar", [])}
            for tema in ds.get("temas_restringidos", []):
                if tema.get("linea_apoyo"):
                    self.palabras_apoyo |= {texto_busqueda(p) for p in tema.get("palabras", [])}
        self.claves_proximamente = {c["nombre"]: [texto_busqueda(p) for p in c.get("palabras_clave", [])]
                                    for c in catalogo.proximamente()}
        # Patrones de las claves de código (de dataset.toml), para no mostrarlas si no se piden.
        self.patrones_clave = []
        for ds in catalogo.datasets.values():
            for c in ds["columnas"]:
                if c["tipo"] == "codigo" and c.get("patron"):
                    self.patrones_clave.append(re.compile(r"\b" + c["patron"].strip("^$") + r"\b"))

    # ---------- Mensajes fijos ----------

    def _ejemplos(self) -> str:
        ejemplos = [p for ds in self.catalogo.datasets.values() for p in ds.get("preguntas_ejemplo", [])][:3]
        return "\n".join(f"- {e}" for e in ejemplos)

    def _disponibles(self) -> str:
        return "; ".join(f"{ds['categoria_nombre']}: {ds['nombre']} ({ds.get('periodo', {}).get('texto', '')})"
                         for ds in self.catalogo.datasets.values())

    def _minimos_privacidad(self) -> str:
        """'5 consultas (10 en Salud Mental)': el mínimo de privacidad de cada dataset, sin escribirlo a mano."""
        por_k = defaultdict(list)
        for ds in self.catalogo.datasets.values():
            por_k[int(ds.get("privacidad", {}).get("minimo_por_grupo", 5))].append(ds)
        if not por_k:
            return "muy pocos registros"
        comun = max(por_k, key=lambda k: len(por_k[k]))
        texto = f"{comun} {por_k[comun][0]['unidad']['plural']}"
        otros = [f"{k} en {', '.join(d['nombre'] for d in ds)}" for k, ds in sorted(por_k.items()) if k != comun]
        return texto + (f" ({'; '.join(otros)})" if otros else "")

    def es_salud_mental(self, mensaje: str) -> bool:
        """¿La pregunta es de salud mental? (para responder con la orientación del protocolo)."""
        t = self.protocolo.normalizar(mensaje)
        return any(p.search(t) for p in self.protocolo.temas_salud_mental)

    def mensaje(self, tipo: str, mensaje_usuario: str = "", **extra) -> str:
        if tipo in ("crisis", "tercero", "orientacion", "despues_de_crisis"):
            return self.protocolo.mensaje(tipo)
        if tipo == "consejo_medico" and self.es_salud_mental(mensaje_usuario):
            return self.protocolo.mensaje("orientacion")
        base_datos = f"Por ahora tengo estos datos: {self._disponibles()}."
        ejemplos = f"Puedes preguntarme, por ejemplo:\n{self._ejemplos()}"
        if tipo == "instrucciones":
            return ("No puedo compartir ni cambiar mis instrucciones. Solo ayudo a buscar, consultar, entender y graficar "
                    f"los datos abiertos de Nuevo León.\n\n{base_datos}\n\n{ejemplos}")
        if tipo == "consejo_medico":
            return ("No puedo dar diagnósticos, recomendar tratamientos o medicamentos, ni interpretar síntomas: solo explico datos. "
                    "Si tienes una molestia de salud, acude con un profesional de salud o a tu centro de salud más cercano."
                    f"\n\n{ejemplos}")
        if tipo == "privacidad":
            return ("Por privacidad no puedo mostrar datos de una persona ni de una consulta individual: solo totales "
                    f"agrupados, y los grupos con menos de {self._minimos_privacidad()} se ocultan. Sí puedo darte, "
                    f"por ejemplo, el número de consultas por municipio, por mes o por grupo de edad.\n\n{ejemplos}")
        if tipo == "proximamente":
            return (f"Los datos de {extra['categoria']} todavía no están disponibles en este prototipo; llegarán próximamente."
                    f"\n\n{base_datos}\n\n{ejemplos}")
        if tipo == "no_disponible":
            return (f"Ese dato no está disponible para consultas: {extra['motivos']} Por eso no lo uso para dar cifras."
                    f"\n\n{ejemplos}")
        if tipo == "saludo":
            return (f"¡Hola! Puedo ayudarte a consultar y entender los datos abiertos de Nuevo León. {base_datos}\n\n{ejemplos}")
        if tipo == "agradecimiento":
            return "¡Con gusto! Si quieres, pregúntame otra cosa sobre los datos."
        if tipo == "error_cifras":
            return ("No pude verificar que todas las cifras de la respuesta salieran de los datos, así que prefiero no mostrarla. "
                    "Intenta preguntarlo de otra forma o de manera más concreta.")
        if tipo == "error_lenguaje":
            return ("No pude redactar la respuesta con un lenguaje adecuado y respetuoso, así que prefiero no mostrarla. "
                    "Intenta preguntarlo de otra forma.")
        # fuera_de_alcance
        return ("Solo puedo ayudarte a buscar, consultar, entender y graficar los datos abiertos de Nuevo León. "
                f"{base_datos}\n\n{ejemplos}")

    def rechazo(self, tipo: str, regla: str, mensaje_usuario: str = "", **extra) -> Rechazo:
        respuesta = self.mensaje(tipo, mensaje_usuario, **extra)
        return Rechazo(tipo, regla, respuesta, contactos=self.protocolo.linea_vida_en(respuesta))

    # ---------- Protocolo de crisis y temas sensibles ----------

    def revisar_crisis(self, mensaje: str) -> Rechazo | None:
        """Malestar propio, ideas de hacerse daño, preocupación por otra persona o petición de terapia."""
        d = self.protocolo.detectar(mensaje)
        if d is None:
            return None
        return Rechazo(d.tipo, d.regla, self.protocolo.mensaje(d.tipo), contactos=True)

    def menciona_tema_apoyo(self, mensaje: str) -> bool:
        """¿La pregunta trata de un tema que lleva línea de apoyo (por ejemplo, suicidio o autolesión)?"""
        t = texto_busqueda(mensaje)
        return any(re.search(rf"\b{re.escape(p)}\b", t) for p in self.palabras_apoyo)

    def lenguaje_inadecuado(self, texto: str) -> list[str]:
        """Frases de lenguaje_evitar que aparecen en la respuesta (se comparan sin acentos)."""
        t = texto_busqueda(texto)
        return sorted(p for p in self.lenguaje_evitar if re.search(rf"\b{re.escape(p)}\b", t))

    # ---------- Filtro previo ----------

    def revisar(self, mensaje: str, hay_historial: bool = False) -> Rechazo | None:
        """Devuelve un Rechazo con mensaje fijo, o None si la pregunta pasa al modelo."""
        t = texto_busqueda(mensaje)
        palabras = set(re.split(r"[^a-z0-9ñ]+", t))
        if not t.strip(" ¿?¡!.,"):
            return self.rechazo("fuera_de_alcance", "vacio")
        if SALUDOS.match(t):
            return self.rechazo("saludo", "saludo")
        if AGRADECIMIENTOS.match(t):
            return self.rechazo("agradecimiento", "agradecimiento")
        for tipo, patrones in (("instrucciones", PATRONES_INSTRUCCIONES), ("privacidad", PATRONES_PRIVACIDAD),
                               ("consejo_medico", PATRONES_CONSEJO_MEDICO), ("fuera_de_alcance", PATRONES_FUERA_DE_ALCANCE)):
            for p in patrones:
                if p.search(t):
                    return self.rechazo(tipo, p.pattern[:60], mensaje_usuario=mensaje)

        # Columnas no disponibles (p. ej. peso o altura) cuando se pide una estadística sobre ellas.
        estadistica = re.search(r"\b(promedio|media|mediana|cuanto (pesan|pesa|miden|mide)|distribucion|imc|indice de masa)\b", t)
        motivos = []
        varios = len(self.catalogo.datasets) > 1
        for ds in self.catalogo.datasets.values():
            for c in ds["columnas"]:
                if not c["expuesta"] and c.get("sinonimos") and estadistica:
                    if any(re.search(rf"\b{re.escape(texto_busqueda(s))}\b", t) for s in c["sinonimos"]):
                        # Con varios datasets, cada motivo dice de cuál es (pueden ser distintos).
                        donde = f" ({ds['nombre']})" if varios else ""
                        motivos.append(f"{c['alias']}{donde}: {c.get('motivo', '')}")
        if motivos:
            return self.rechazo("no_disponible", "columna_no_disponible", motivos=" ".join(motivos))

        # Categorías sin datos ("próximamente"), salvo que la pregunta también nombre un dataset activo.
        menciona_activo = any(re.search(rf"\b{re.escape(k)}\b", t) for k in self.claves_datasets)
        for categoria, claves in self.claves_proximamente.items():
            if not menciona_activo and any(re.search(rf"\b{re.escape(k)}\b", t) for k in claves):
                return self.rechazo("proximamente", f"categoria:{categoria}", categoria=categoria)

        # ¿Trata de datos? Debe tener al menos una palabra del catálogo o de datos en general.
        if palabras & (self.vocabulario | PALABRAS_DE_DATOS) or menciona_activo:
            return None
        # Seguimiento corto dentro de una conversación ("¿y en julio?", "hazlo en gráfica").
        if hay_historial and len(t) <= 80:
            return None
        return self.rechazo("fuera_de_alcance", "sin_relacion_con_datos")


# ---------- Marcadores que devuelve el modelo ----------

MARCADORES = {"[[CRISIS]]": "crisis", "[[FUERA_DE_ALCANCE]]": "fuera_de_alcance", "[[CONSEJO_MEDICO]]": "consejo_medico",
              "[[PRIVACIDAD]]": "privacidad"}
# Frases del system prompt que nunca deben aparecer en una respuesta.
FRASES_DEL_PROMPT = ("reglas obligatorias", "system prompt", "[[fuera", "[[consejo", "[[privacidad", "[[crisis",
                     "como responder:")


def detectar_marcador(texto: str) -> str | None:
    for marcador, tipo in MARCADORES.items():
        if marcador in texto:
            return tipo
    bajo = texto_busqueda(texto)
    if any(f in bajo for f in FRASES_DEL_PROMPT):
        return "instrucciones"
    return None


# Cualquier marcador o marcador de posición que el modelo invente: [[GRAFICA_AQUI]], {{grafica}}, [Gráfica].
_MARCADOR = re.compile(r"\[\[[^\[\]\n]{1,80}\]\]|\{\{[^{}\n]{1,80}\}\}|\[(?:gr[aá]fic[ao]|tabla|chart|figura)[^\]\n]{0,40}\]",
                       re.I)
_DE_GRAFICA = re.compile(r"gr[aá]fic|chart|tabla|figura|imagen", re.I)


def quitar_marcadores(texto: str) -> tuple[str, bool]:
    """Quita del texto todo marcador interno. Devuelve (texto limpio, si había un marcador de gráfica)."""
    marcadores = _MARCADOR.findall(texto or "")
    limpio = _MARCADOR.sub("", texto or "")
    limpio = re.sub(r"[ \t]+\n", "\n", limpio)
    limpio = re.sub(r"\n{3,}", "\n\n", limpio)
    limpio = re.sub(r"[ \t]{2,}", " ", limpio).strip()
    return limpio, any(_DE_GRAFICA.search(m) for m in marcadores)


def pide_claves(mensaje: str) -> bool:
    """¿La persona pidió claves o códigos? (entonces se pueden mostrar)."""
    return bool(re.search(r"\b(clave|claves|codigo|codigos|cie|cie-10|cie10)\b", texto_busqueda(mensaje)))


def quitar_claves(texto: str, patrones: list[re.Pattern]) -> str:
    """Quita claves de código que el modelo agregue al texto: '(I10X)', '(claves E110 a E119)', '- E119: …'."""
    if not patrones:
        return texto
    clave = "(?:" + "|".join(p.pattern for p in patrones) + ")"
    # Paréntesis que solo contienen claves (y palabras de apoyo como "clave", "CIE-10", "a", "y").
    relleno = r"(?:\s|,|;|:|\by\b|\ba\b|\bal\b|\bde\b|\bclaves?\b|\bc[oó]digos?\b|\bCIE-?10\b)*"
    texto = re.sub(rf"\s*\({relleno}{clave}(?:{relleno}{clave})*{relleno}\)", "", texto, flags=re.I)
    # Claves al inicio de un renglón o de una lista: "- I10X: Hipertensión" → "- Hipertensión".
    texto = re.sub(rf"(?m)^(\s*(?:[-•]\s*)?){clave}\s*(?:[:\-–—]\s*)", r"\1", texto)
    return texto


def limpiar_formato(texto: str) -> str:
    """Quita formato Markdown que el widget no interpreta (negritas y encabezados)."""
    texto = re.sub(r"\*\*(.+?)\*\*", r"\1", texto)
    texto = re.sub(r"__(.+?)__", r"\1", texto)
    texto = re.sub(r"(?m)^\s*#+\s*", "", texto)
    texto = re.sub(r"(?m)^\s*\*\s+", "- ", texto)
    return texto.strip()


# ---------- Verificador de cifras ----------

_NUMERO = re.compile(r"(?<![\w.,\-])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?:\s*(mil|millones?)\b)?", re.I)


def extraer_numeros(texto: str) -> list[tuple[str, float, int, float]]:
    """Números del texto: (original, valor, decimales, multiplicador). '71,610' → 71610; '20.3' → 20.3."""
    numeros = []
    for m in _NUMERO.finditer(str(texto)):
        entero, decimales, escala = m.group(1), m.group(2) or "", (m.group(3) or "").lower()
        valor = float(entero.replace(",", "") + ("." + decimales if decimales else ""))
        mult = 1_000_000 if escala.startswith("millon") else 1000 if escala == "mil" else 1
        numeros.append((m.group(0).strip(), valor, len(decimales), mult))
    return numeros


def numeros_de(objeto) -> set[float]:
    """Todos los números de un resultado de herramienta (incluidos los que van dentro de textos)."""
    encontrados = set()
    if isinstance(objeto, bool) or objeto is None:
        return encontrados
    if isinstance(objeto, (int, float)):
        encontrados.add(float(objeto))
    elif isinstance(objeto, str):
        encontrados |= {v * m for _, v, _, m in extraer_numeros(objeto)}
    elif isinstance(objeto, dict):
        for v in objeto.values():
            encontrados |= numeros_de(v)
    elif isinstance(objeto, (list, tuple, set)):
        for v in objeto:
            encontrados |= numeros_de(v)
    return encontrados


def verificar_cifras(texto: str, permitidos: set[float]) -> list[str]:
    """Cifras del texto que no salen de los resultados de herramientas (lista vacía = todo verificado).

    Se aceptan redondeos ('20.3' para 20.28, '72 mil' para 71,610), números pequeños (0 a 12, para
    listas y conteos de elementos) y años escritos sin separador (1990 a 2035)."""
    sin_verificar = []
    for original, valor, decimales, mult in extraer_numeros(texto):
        if mult == 1 and decimales == 0 and (valor <= 12 or ("," not in original and 1990 <= valor <= 2035)):
            continue
        ok = False
        for p in permitidos:
            if mult > 1:
                # '72 mil' o '71.6 mil': se acepta si es el redondeo de una cifra permitida.
                if abs(p / mult - valor) <= 0.5 * 10 ** (-decimales) + 1e-9:
                    ok = True
            elif abs(round(p, decimales) - valor) < 1e-9 or abs(p - valor) < 1e-9:
                ok = True
            if ok:
                break
        if not ok:
            sin_verificar.append(original)
    return sin_verificar


# ---------- Límites de uso ----------

class LimitadorUso:
    """Rate limit por minuto (por sesión y por IP) y tope de mensajes por sesión, en memoria."""

    def __init__(self, por_minuto: int, por_sesion: int):
        self.por_minuto = por_minuto
        self.por_sesion = por_sesion
        self._ventanas: dict[str, deque] = defaultdict(deque)
        self._mensajes: dict[str, int] = defaultdict(int)
        self._candado = threading.Lock()

    def permitir(self, sesion: str, ip: str, ahora: float | None = None) -> str | None:
        """None si se permite; si no, 'rate_limit' o 'limite_sesion'."""
        ahora = time.monotonic() if ahora is None else ahora
        with self._candado:
            if self._mensajes[sesion] >= self.por_sesion:
                return "limite_sesion"
            for clave in (f"s:{sesion}", f"ip:{ip}"):
                ventana = self._ventanas[clave]
                while ventana and ahora - ventana[0] > 60:
                    ventana.popleft()
                if len(ventana) >= self.por_minuto:
                    return "rate_limit"
            for clave in (f"s:{sesion}", f"ip:{ip}"):
                self._ventanas[clave].append(ahora)
            self._mensajes[sesion] += 1
            return None
