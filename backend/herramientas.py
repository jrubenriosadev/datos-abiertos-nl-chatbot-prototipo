"""Herramientas que puede llamar el modelo, su declaración (esquema) y el estado de cada sesión.

Todo lo que devuelven es DATO para el modelo, nunca instrucciones. Cada respuesta usa un solo
dataset (todavía no hay cruces): un segundo dataset en el mismo turno se rechaza, y las cifras
que puede citar la respuesta son solo las del dataset que usó.
"""
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import bitacora
import graficas
from catalogo import Catalogo
from consultas import ErrorConsulta, MotorConsultas
from guardrails import numeros_de

MAX_RESULTADOS_SESION = 30
MAX_TURNOS_HISTORIAL = 6

_VALOR = {"anyOf": [{"type": "string"}, {"type": "number"},
                    {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "number"}]}}],
          "description": "Un valor, o una lista para 'en', 'no_en' y 'entre' ([desde, hasta])."}

DECLARACIONES = [
    {
        "name": "buscar_dataset",
        "description": "Busca en el catálogo los conjuntos de datos que sirven para una pregunta. "
                       "También indica qué categorías aún no tienen datos (próximamente).",
        "parameters": {"type": "object", "properties": {
            "consulta": {"type": "string", "description": "Tema o pregunta en palabras simples."}},
            "required": ["consulta"]},
    },
    {
        "name": "describir_dataset",
        "description": "Describe un conjunto de datos: columnas, valores, periodo cubierto, meses incompletos, "
                       "advertencias, reglas de interpretación y de privacidad. Llámala antes de consultar.",
        "parameters": {"type": "object", "properties": {
            "dataset_id": {"type": "string"}}, "required": ["dataset_id"]},
    },
    {
        "name": "buscar_valores",
        "description": "Busca valores válidos de una columna con muchos valores (por ejemplo, claves de enfermedad "
                       "o unidades médicas) a partir de una palabra. Devuelve claves y nombres, sin cifras.",
        "parameters": {"type": "object", "properties": {
            "dataset_id": {"type": "string"},
            "columna": {"type": "string", "description": "Alias de la columna, por ejemplo 'enfermedad'."},
            "texto": {"type": "string", "description": "Palabra o palabras a buscar, sin acentos de preferencia."}},
            "required": ["dataset_id", "columna", "texto"]},
    },
    {
        "name": "consultar_datos",
        "description": "Consulta estructurada sobre un conjunto de datos. Devuelve totales agrupados (nunca registros "
                       "individuales), un result_id para graficar y advertencias que debes repetir en tu respuesta.",
        "parameters": {"type": "object", "properties": {
            "dataset_id": {"type": "string"},
            "filtros": {"type": "array", "description": "Condiciones que se combinan con Y.", "items": {
                "type": "object", "properties": {
                    "columna": {"type": "string"},
                    "operador": {"type": "string", "enum": ["igual", "distinto", "en", "no_en", "entre", "mayor_igual", "menor_igual"]},
                    "valor": _VALOR},
                "required": ["columna", "operador", "valor"]}},
            "agrupar_por": {"type": "array", "items": {"type": "string"}, "maxItems": 2,
                            "description": "Hasta 2 columnas para agrupar (por ejemplo 'municipio' o 'mes')."},
            "agregaciones": {"type": "array", "items": {"type": "string"},
                             "description": "Por ejemplo 'conteo', 'proporcion', 'edad_promedio', 'edad_mediana', "
                                            "'conteo_distinto:unidad_medica'. Ver describir_dataset."},
            "proporcion_dentro_de": {"type": "string",
                                     "description": "Columna de agrupar_por que define el 100% de cada porcentaje "
                                                    "(por ejemplo 'municipio' para el porcentaje dentro de cada municipio)."},
            "proporcion_ignora_filtros": {"type": "array", "items": {"type": "string"},
                                          "description": "Columnas filtradas que NO se aplican al denominador del porcentaje "
                                                         "(por ejemplo 'enfermedad' para obtener qué % de las consultas son por esa enfermedad)."},
            "ordenar_por": {"type": "object", "properties": {
                "campo": {"type": "string"}, "direccion": {"type": "string", "enum": ["asc", "desc"]}}},
            "limite": {"type": "integer", "description": "Máximo de grupos a devolver (1 a 200)."}},
            "required": ["dataset_id"]},
    },
    {
        "name": "crear_grafica",
        "description": "Crea una gráfica de barras con los datos reales de un resultado de consultar_datos. "
                       "Tú solo eliges columnas, tipo y título; los valores los pone el sistema.",
        "parameters": {"type": "object", "properties": {
            "result_id": {"type": "string"},
            "eje_x": {"type": "string", "description": "Columna agrupada que va en las categorías (la misma de agrupar_por, "
                                                       "por ejemplo 'enfermedad' o 'mes'). Las etiquetas salen solas con el nombre legible."},
            "eje_y": {"type": "string", "description": "Columna numérica: 'conteo', 'porcentaje' u otra agregación."},
            "titulo": {"type": "string", "description": "Título breve y claro, sin cifras."},
            "tipo": {"type": "string", "enum": ["barras", "horizontales", "agrupadas", "apiladas"]},
            "serie": {"type": "string", "description": "Solo para agrupadas o apiladas: la segunda columna agrupada."}},
            "required": ["result_id", "eje_x", "eje_y", "titulo", "tipo"]},
    },
]


@dataclass
class Turno:
    """Lo que pasó en una pregunta: datasets consultados, advertencias, cifras y gráfica."""
    datasets: list[str] = field(default_factory=list)              # consultados o graficados (a lo más uno)
    advertencias: list[str] = field(default_factory=list)
    numeros: dict[str, set[float]] = field(default_factory=dict)   # dataset_id → cifras ("" = de ningún dataset)
    grafica: dict | None = None
    llamadas: list[str] = field(default_factory=list)
    ultimo_resultado: dict | None = None   # última consulta agrupada (para la tabla de respaldo)
    temas_sensibles: list[dict] = field(default_factory=list)   # temas restringidos que tocó la respuesta

    def agregar_temas(self, resultado: dict):
        for t in resultado.get("temas_sensibles", []):
            if t not in self.temas_sensibles:
                self.temas_sensibles.append(t)

    @property
    def pide_linea_apoyo(self) -> bool:
        return any(t.get("linea_apoyo") for t in self.temas_sensibles)


@dataclass
class EstadoSesion:
    historial: list[tuple[str, str]] = field(default_factory=list)   # (pregunta, respuesta) recientes
    resultados: OrderedDict = field(default_factory=OrderedDict)     # result_id → resultado
    numeros: dict[str, set[float]] = field(default_factory=dict)     # cifras ya verificadas, por dataset
    ultimo_dataset: str | None = None                                # el de la respuesta anterior (seguimientos)
    en_crisis: bool = False                                          # hubo un mensaje de crisis: toda respuesta lleva línea de apoyo
    candado: threading.Lock = field(default_factory=threading.Lock)
    ultimo_uso: float = field(default_factory=time.monotonic)

    def guardar_resultado(self, resultado: dict):
        self.resultados[resultado["result_id"]] = resultado
        while len(self.resultados) > MAX_RESULTADOS_SESION:
            self.resultados.popitem(last=False)

    def agregar_turno(self, pregunta: str, respuesta: str):
        self.historial.append((pregunta, respuesta))
        del self.historial[:-MAX_TURNOS_HISTORIAL]

    def cifras_permitidas(self, turno: Turno) -> set[float]:
        """Cifras que puede citar la respuesta: las del dataset que usó este turno (también de turnos
        anteriores sobre ese mismo dataset) y las que no son de ningún dataset. Las de otro dataset
        no se aceptan: así no se suman ni se comparan conjuntos distintos."""
        usados = (turno.datasets or [d for d in turno.numeros if d]
                  or ([self.ultimo_dataset] if self.ultimo_dataset else []))
        permitidas = set()
        for d in ["", *usados]:
            permitidas |= turno.numeros.get(d, set()) | self.numeros.get(d, set())
        return permitidas

    def cerrar_turno(self, turno: Turno):
        """Guarda las cifras verificadas del turno y recuerda su dataset para los seguimientos."""
        for d, cifras in turno.numeros.items():
            self.numeros.setdefault(d, set()).update(cifras)
        if turno.datasets:
            self.ultimo_dataset = turno.datasets[-1]


class AlmacenSesiones:
    """Sesiones en memoria; caducan tras una hora sin uso."""

    def __init__(self, caducidad_s: int = 3600, maximo: int = 2000):
        self.caducidad_s = caducidad_s
        self.maximo = maximo
        self._sesiones: dict[str, EstadoSesion] = {}
        self._candado = threading.Lock()

    def obtener(self, sesion: str) -> EstadoSesion:
        ahora = time.monotonic()
        with self._candado:
            for clave in [k for k, s in self._sesiones.items() if ahora - s.ultimo_uso > self.caducidad_s]:
                del self._sesiones[clave]
            if sesion not in self._sesiones and len(self._sesiones) >= self.maximo:
                mas_vieja = min(self._sesiones, key=lambda k: self._sesiones[k].ultimo_uso)
                del self._sesiones[mas_vieja]
            estado = self._sesiones.setdefault(sesion, EstadoSesion())
            estado.ultimo_uso = ahora
            return estado


class Herramientas:
    def __init__(self, motor: MotorConsultas, catalogo: Catalogo):
        self.motor = motor
        self.catalogo = catalogo

    def ejecutar(self, nombre: str, args: dict, turno: Turno, sesion: EstadoSesion, id_sesion: str = "") -> dict:
        """Ejecuta una herramienta. Nunca lanza excepciones: los errores vuelven como {'error': ...}."""
        args = dict(args or {})
        turno.llamadas.append(nombre)
        try:
            resultado = self._despachar(nombre, args, turno, sesion)
        except ErrorConsulta as e:
            resultado = e.como_dict()
        except Exception as e:  # error inesperado: se registra y se avisa sin detalles internos
            bitacora.registrar_error(f"herramienta:{nombre}", e, id_sesion)
            resultado = {"error": "Ocurrió un error interno al consultar los datos. Intenta con una consulta más simple."}
        # Las cifras se guardan con el dataset del que salieron (buscar_dataset y los errores no tienen uno).
        dataset = str(resultado.get("dataset_id") or "") if nombre != "buscar_dataset" else ""
        turno.numeros.setdefault(dataset, set()).update(numeros_de(resultado))
        return resultado

    def _un_solo_dataset(self, ds_id: str, turno: Turno):
        """Rechaza un segundo dataset en la misma respuesta (todavía no hay cruces entre datasets)."""
        if turno.datasets and ds_id in self.catalogo.datasets and ds_id not in turno.datasets:
            usado, otro = self.catalogo.obtener(turno.datasets[0])["nombre"], self.catalogo.obtener(ds_id)["nombre"]
            raise ErrorConsulta(
                f"Cada respuesta usa un solo conjunto de datos y en esta ya usaste '{usado}'. Todavía no se pueden "
                f"sumar, comparar ni cruzar conjuntos distintos: responde solo con '{usado}' y ofrece consultar "
                f"'{otro}' en una pregunta aparte.", tipo="un_solo_conjunto")

    def _despachar(self, nombre, args, turno, sesion) -> dict:
        if nombre == "buscar_dataset":
            return self.catalogo.buscar(str(args.get("consulta", "")))
        if nombre == "describir_dataset":
            desc = self.catalogo.describir(str(args.get("dataset_id", "")))
            if desc is None:
                raise ErrorConsulta(f"No existe el dataset {args.get('dataset_id')!r}.", list(self.catalogo.datasets))
            return desc
        if nombre == "buscar_valores":
            return self.motor.buscar_valores(args.get("dataset_id"), args.get("columna"), args.get("texto"))
        if nombre == "consultar_datos":
            permitidos = {"dataset_id", "filtros", "agrupar_por", "agregaciones", "proporcion_dentro_de",
                          "proporcion_ignora_filtros", "ordenar_por", "limite"}
            desconocidos = set(args) - permitidos
            if desconocidos:
                raise ErrorConsulta(f"Parámetros desconocidos: {sorted(desconocidos)}.", sorted(permitidos))
            self._un_solo_dataset(str(args.get("dataset_id", "")), turno)
            resultado = self.motor.consultar(**args)
            sesion.guardar_resultado(resultado)
            if resultado["agrupar_por"]:
                turno.ultimo_resultado = resultado
            if resultado["dataset_id"] not in turno.datasets:
                turno.datasets.append(resultado["dataset_id"])
            for a in resultado["advertencias"]:
                if a not in turno.advertencias:
                    turno.advertencias.append(a)
            turno.agregar_temas(resultado)
            return resultado
        if nombre == "crear_grafica":
            resultado = sesion.resultados.get(str(args.get("result_id", "")))
            if resultado is None:
                raise ErrorConsulta("Ese result_id no existe en esta conversación; vuelve a llamar a consultar_datos.")
            self._un_solo_dataset(resultado["dataset_id"], turno)
            grafica = graficas.crear_grafica(resultado, self.catalogo, args.get("eje_x"), args.get("eje_y", "conteo"),
                                             args.get("titulo", ""), args.get("tipo", "barras"), args.get("serie"))
            turno.grafica = grafica
            turno.agregar_temas(resultado)
            if resultado["dataset_id"] not in turno.datasets:
                turno.datasets.append(resultado["dataset_id"])
            return {"ok": True, "dataset_id": resultado["dataset_id"], "grafica": grafica["titulo"],
                    "barras": len(grafica["etiquetas"]), "nota": grafica.get("nota")}
        raise ErrorConsulta(f"No existe la herramienta {nombre!r}.", [d["name"] for d in DECLARACIONES])
