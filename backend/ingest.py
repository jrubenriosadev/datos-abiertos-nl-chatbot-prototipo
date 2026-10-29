"""Ingesta: recorre data/, carga cada dataset en DuckDB y genera el catálogo.

Cada subcarpeta de data/ con archivos CSV es un dataset. Necesita un dataset.toml
(configuración) y, de preferencia, un _FUENTES.md. Las reglas que comparten todos los
datasets de una categoría pueden ir en data/<categoría>/_comun.toml. Si algo no cuadra,
se reporta en build/reporte_ingesta.md en lugar de adivinar.

Uso:
    python backend/ingest.py              reconstruye la base y el catálogo
    python backend/ingest.py --si-cambio  solo si cambiaron los datos o la configuración
"""
import argparse
import calendar
import csv
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import statistics
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

import config
from normalizar import Diccionario, clave_comparacion, normalizar_valor, texto_busqueda

# Súbela cuando cambie la lógica de la ingesta para forzar una reingesta.
VERSION_INGESTA = 4

IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ID_DATASET = re.compile(r"^[a-z0-9_]+$")
OPERADORES_CONTROL = {"<", "<=", ">", ">=", "=", "<>"}
CODIFICACIONES_DUCKDB = {"utf-8": "utf-8", "utf-8-sig": "utf-8", "latin-1": "latin-1"}
CLAVES_COLUMNA = {"nombre", "alias", "tipo", "descripcion", "expuesta", "motivo", "cuasi_identificador",
                  "normalizar", "doble_espacio", "diccionario", "compuesta_con", "conteo_distinto",
                  "etiqueta", "buscable", "patron", "sinonimos", "unidad", "unificar", "guardar"}
MAX_VALORES_LISTADOS = 60  # columnas con pocos valores distintos se listan completas en el catálogo
# Lo que puede ir en data/<categoría>/_comun.toml (reglas compartidas por todos los datasets de la categoría).
CLAVES_COMUNES = {"reglas_interpretacion", "datos_no_disponibles", "advertencias_condicionales", "valores_especiales",
                  "siglas", "unidad", "privacidad", "completitud", "edad", "temas_restringidos",
                  "valores_no_disponibles", "lenguaje_evitar"}
DERIVADAS = {"fecha", "mes", "rango_edad"}
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
PALABRAS_VACIAS = {
    "para", "otro", "otra", "otros", "otras", "como", "sobre", "entre", "desde", "hasta", "segun",
    "sin", "con", "del", "las", "los", "una", "unos", "unas", "que", "esta", "este", "estos", "estas",
    "cual", "cuales", "parte", "tipo", "debido", "mencion", "especificada", "especificado",
    "especificadas", "especificados", "clasificados", "clasificadas", "sitio", "consecuencia",
    # Interrogativos y palabras funcionales: aparecen en cualquier pregunta, no indican el tema.
    "quien", "quienes", "donde", "cuando", "como", "porque", "pero", "tambien", "muy", "todo", "toda", "todos",
    "todas", "algo", "alguna", "alguno", "algunos", "algunas", "cada", "mismo", "misma", "ellos", "ellas",
    "sus", "son", "fue", "ser", "estar", "hay", "tiene", "tienen", "puede", "pueden", "hacer", "dice", "dijo",
}


# ---------- Utilidades ----------

def q(nombre: str) -> str:
    """Cita un identificador SQL después de validarlo (nunca viene del modelo)."""
    if not IDENTIFICADOR.match(nombre):
        raise ValueError(f"Identificador no válido: {nombre!r}")
    return f'"{nombre}"'


def literal(texto: str) -> str:
    """Literal de texto SQL escapado (solo para rutas de archivo en read_csv)."""
    return "'" + texto.replace("'", "''") + "'"


def fecha_legible(fecha: dt.date, con_anio: bool = True) -> str:
    texto = f"{fecha.day} de {MESES[fecha.month - 1]}"
    return f"{texto} de {fecha.year}" if con_anio else texto


def periodo_legible(inicio: dt.date, fin: dt.date) -> str:
    """'del 3 de mayo al 30 de septiembre de 2026'."""
    return f"del {fecha_legible(inicio, con_anio=inicio.year != fin.year)} al {fecha_legible(fin)}"


def nombre_mes(clave_mes: str) -> str:
    """'2026-09' → 'septiembre de 2026'."""
    anio, mes = clave_mes.split("-")
    return f"{MESES[int(mes) - 1]} de {anio}"


def miles(n: float) -> str:
    return f"{n:,.0f}"


def lista_legible(textos: list[str]) -> str:
    """['a', 'b', 'c'] → 'a, b y c'."""
    textos = list(textos)
    return textos[0] if len(textos) == 1 else ", ".join(textos[:-1]) + " y " + textos[-1]


def valor_en_regla(valor, regla: dict) -> bool:
    """¿El valor está en la regla? (una clave de 'claves' o que empiece con uno de 'prefijos')."""
    if valor is None:
        return False
    valor = str(valor)
    return valor in regla.get("claves", []) or any(valor.startswith(p) for p in regla.get("prefijos", []))


class Reporte:
    """Junta lo que no cuadra durante la ingesta (errores, avisos e información)."""

    def __init__(self):
        self.entradas: list[tuple[str, str, str]] = []  # (ámbito, nivel, texto)

    def agregar(self, ambito: str, nivel: str, texto: str):
        self.entradas.append((ambito, nivel, texto))

    def de(self, ambito: str) -> list[dict]:
        return [{"nivel": n, "texto": t} for a, n, t in self.entradas if a == ambito]


# ---------- Descubrimiento y lectura de configuración ----------

def descubrir_carpetas(data_dir: Path) -> list[Path]:
    """Cada carpeta que contiene al menos un CSV es un dataset."""
    return sorted({p.parent for p in data_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".csv"})


def cargar_toml(ruta: Path) -> dict:
    with open(ruta, "rb") as f:
        return tomllib.load(f)


def cargar_diccionarios(data_dir: Path, reporte: Reporte) -> dict[str, Diccionario]:
    diccionarios = {}
    carpeta = data_dir / "diccionarios"
    for ruta in sorted(carpeta.glob("*.toml")) if carpeta.exists() else []:
        datos = cargar_toml(ruta)
        dic = Diccionario(datos.get("nombre", ruta.stem), datos.get("valores", {}), datos.get("fuente", ""))
        for clave, mostrar in dic.rechazadas:
            reporte.agregar("general", "error",
                            f"Diccionario {ruta.name}: la entrada {clave!r} → {mostrar!r} cambia palabras, no solo acentos; se ignoró.")
        diccionarios[ruta.stem] = dic
    return diccionarios


def cargar_categorias(data_dir: Path) -> list[dict]:
    ruta = data_dir / "categorias.toml"
    return cargar_toml(ruta).get("categorias", []) if ruta.exists() else []


def cargar_comunes(data_dir: Path, reporte: Reporte) -> dict[str, dict]:
    """Reglas comunes de cada categoría (data/<id de categoría>/_comun.toml), si existen."""
    comunes = {}
    for ruta in sorted(data_dir.glob("*/_comun.toml")):
        nombre = f"{ruta.parent.name}/_comun.toml"
        try:
            datos = cargar_toml(ruta)
        except tomllib.TOMLDecodeError as e:
            reporte.agregar("general", "error", f"{nombre} no es TOML válido: {e}; se ignoró.")
            continue
        desconocidas = sorted(set(datos) - CLAVES_COMUNES)
        if desconocidas:
            reporte.agregar("general", "error", f"{nombre} tiene claves no permitidas {desconocidas}; se ignoraron "
                                                f"(solo se aceptan {sorted(CLAVES_COMUNES)}).")
        comunes[ruta.parent.name] = {k: v for k, v in datos.items() if k in CLAVES_COMUNES}
    return comunes


def unir_comunes(cfg: dict, comun: dict) -> dict:
    """Une las reglas comunes de la categoría a la configuración de un dataset.

    Listas: primero las comunes y luego las del dataset. Tablas: el dataset gana clave por clave."""
    unida = dict(cfg)
    for clave, valor in comun.items():
        propio = cfg.get(clave)
        if isinstance(valor, list):
            unida[clave] = valor + list(propio or [])
        elif isinstance(valor, dict):
            unida[clave] = {**valor, **(propio or {})}
        elif propio is None:
            unida[clave] = valor
    return unida


def leer_fuentes(carpeta: Path) -> dict:
    """Lee los campos '- **Campo:** valor' del _FUENTES.md (solo el primer nivel)."""
    ruta = carpeta / "_FUENTES.md"
    campos = {}
    if not ruta.exists():
        return campos
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^-\s+\*\*(.+?):?\*\*:?\s*(.*)$", linea)
        if m:
            campos[m.group(1).strip().rstrip(":")] = m.group(2).strip()
    return campos


def detectar_codificacion(ruta: Path, candidatas: list[str]) -> str | None:
    """Prueba las codificaciones en orden y devuelve la primera que decodifica todo el archivo."""
    contenido = ruta.read_bytes()
    for cod in candidatas:
        try:
            contenido.decode("utf-8-sig" if cod == "utf-8" else cod)
            return cod
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def leer_cabecera(ruta: Path, codificacion: str, separador: str) -> tuple[str, ...]:
    with open(ruta, encoding="utf-8-sig" if codificacion == "utf-8" else codificacion, newline="") as f:
        return tuple(c.strip() for c in next(csv.reader(f, delimiter=separador)))


def calcular_huella(data_dir: Path) -> str:
    """Huella de los datos, configuraciones y código de ingesta: si cambia, hay que reingerir."""
    h = hashlib.sha256(f"v{VERSION_INGESTA}".encode())
    for ruta in sorted(data_dir.rglob("*")):
        if ruta.is_file() and ruta.suffix.lower() in {".csv", ".toml", ".md"}:
            st = ruta.stat()
            h.update(f"{ruta.relative_to(data_dir).as_posix()}|{st.st_size}|{st.st_mtime_ns}".encode())
    for codigo in ("ingest.py", "normalizar.py"):
        h.update((Path(__file__).parent / codigo).read_bytes())
    return h.hexdigest()


# ---------- Completitud de los meses ----------

def evaluar_completitud(conteo_por_dia: dict[dt.date, int], inicio: dt.date, fin: dt.date,
                        umbral: float, meses_rezago: int, unidad_plural: str,
                        rezago_antes_de_hueco: bool = False) -> list[dict]:
    """Calcula filas por mes y marca los meses parciales, incompletos, sin datos o con posible rezago.

    - parcial: el periodo cubierto no abarca el mes completo.
    - sin_datos: no hay ningún registro en el mes (un hueco entre publicaciones).
    - incompleto: su promedio por día hábil es menor que `umbral` × la mediana de los meses previos.
    - posible_rezago: es uno de los `meses_rezago` meses más recientes o, si `rezago_antes_de_hueco`,
      el mes justo antes de un mes sin datos (en ambos casos termina una publicación y la captura suele atrasarse).
    """
    meses = []
    anio, mes = inicio.year, inicio.month
    while (anio, mes) <= (fin.year, fin.month):
        ultimo_dia = calendar.monthrange(anio, mes)[1]
        desde = max(inicio, dt.date(anio, mes, 1))
        hasta = min(fin, dt.date(anio, mes, ultimo_dia))
        filas = filas_habiles = dias_habiles = 0
        dia = desde
        while dia <= hasta:
            n = conteo_por_dia.get(dia, 0)
            filas += n
            if dia.weekday() < 5:
                dias_habiles += 1
                filas_habiles += n
            dia += dt.timedelta(days=1)
        meses.append({
            "mes": f"{anio:04d}-{mes:02d}",
            "nombre": nombre_mes(f"{anio:04d}-{mes:02d}"),
            "filas": filas,
            "desde": desde.isoformat(),
            "hasta": hasta.isoformat(),
            "promedio_dia_habil": round(filas_habiles / dias_habiles, 1) if dias_habiles else 0.0,
            "banderas": ["parcial"] if (desde.day != 1 or hasta.day != ultimo_dia) else [],
        })
        mes += 1
        if mes == 13:
            anio, mes = anio + 1, 1

    # Meses sin ningún registro: huecos entre publicaciones.
    for m in meses:
        if m["filas"] == 0:
            m["banderas"].append("sin_datos")
    # Meses incompletos: se comparan con la mediana de los meses previos con datos completos.
    for i, m in enumerate(meses):
        if "sin_datos" in m["banderas"]:
            continue
        previos = [p["promedio_dia_habil"] for p in meses[:i] if not {"incompleto", "sin_datos"} & set(p["banderas"])]
        if previos:
            mediana = statistics.median(previos)
            m["mediana_previa"] = round(mediana, 1)
            if mediana > 0 and m["promedio_dia_habil"] < umbral * mediana:
                m["banderas"].append("incompleto")
    def marcar_rezago(m, motivo):
        if not {"incompleto", "sin_datos", "posible_rezago"} & set(m["banderas"]):
            m["banderas"].append("posible_rezago")
            m["motivo_rezago"] = motivo
    for m in meses[-meses_rezago:] if meses_rezago > 0 else []:
        marcar_rezago(m, "recientes")
    if rezago_antes_de_hueco:
        for anterior, siguiente in zip(meses, meses[1:]):
            if "sin_datos" in siguiente["banderas"]:
                marcar_rezago(anterior, "antes_de_hueco")
    _notas_y_estado(meses, unidad_plural)
    return meses


def evaluar_cobertura(meses: list[dict], conteo_por_valor: dict[str, dict[str, int]], umbral: float,
                      peso_minimo: float, unidad_plural: str, minimo_previos: int = 3) -> list[dict]:
    """Marca como incompleto un mes en el que parte de la cobertura desaparece aunque el total no caiga tanto.

    Para cada valor de una columna (por ejemplo, cada municipio) que tuvo datos en el mes anterior con datos
    se espera la mediana de sus meses previos con datos (si tiene al menos `minimo_previos`); los valores que
    ya habían dejado de aparecer no cuentan. Si los que bajan a menos de `umbral` × lo esperado suman al menos
    `peso_minimo` de todo lo esperado en ese mes, el mes queda 'incompleto' y la nota dice qué valores bajaron.
    Modifica y devuelve `meses` (la salida de evaluar_completitud)."""
    claves = [m["mes"] for m in meses]
    for i, m in enumerate(meses):
        if "sin_datos" in m["banderas"]:
            continue
        anterior = next((claves[j] for j in range(i - 1, -1, -1) if "sin_datos" not in meses[j]["banderas"]), None)
        esperado_total, faltante, bajan = 0.0, 0.0, []
        for valor, serie in conteo_por_valor.items():
            previos = [serie.get(k, 0) for k in claves[:i] if serie.get(k, 0) > 0]
            if len(previos) < minimo_previos or not serie.get(anterior, 0):
                continue
            esperado = statistics.median(previos)
            actual = serie.get(m["mes"], 0)
            esperado_total += esperado
            if actual < umbral * esperado:
                faltante += esperado - actual
                bajan.append((esperado - actual, valor))
        if esperado_total and faltante / esperado_total >= peso_minimo:
            if "incompleto" not in m["banderas"]:
                m["banderas"].append("incompleto")
            m["cobertura_parcial"] = [v for _, v in sorted(bajan, key=lambda t: (-t[0], t[1]))]
    _notas_y_estado(meses, unidad_plural)
    return meses


def _notas_y_estado(meses: list[dict], unidad_plural: str):
    """Notas en lenguaje sencillo y estado final de cada mes (según sus banderas)."""
    for m in meses:
        notas = []
        nombre = m["nombre"][:1].upper() + m["nombre"][1:]
        if "sin_datos" in m["banderas"]:
            notas.append(f"No hay datos de {m['nombre']}: ese mes no viene en los archivos publicados. "
                         f"No significa que hubo cero {unidad_plural}.")
        elif "parcial" in m["banderas"]:
            d, h = dt.date.fromisoformat(m["desde"]), dt.date.fromisoformat(m["hasta"])
            notas.append(f"{nombre} solo incluye datos del {d.day} al {h.day} de {MESES[d.month - 1]}.")
        if "incompleto" in m["banderas"] and m.get("cobertura_parcial"):
            notas.append(
                f"{nombre} está incompleto: faltan o bajan mucho los registros de {lista_legible(m['cobertura_parcial'][:3])} "
                f"(probable rezago de captura). No lo compares con los demás meses.")
        elif "incompleto" in m["banderas"]:
            notas.append(
                f"{nombre} está incompleto: tiene en promedio {miles(m['promedio_dia_habil'])} {unidad_plural} "
                f"por día hábil, contra {miles(m['mediana_previa'])} en los meses anteriores (probable rezago de captura). "
                f"No lo compares con los demás meses.")
        if "posible_rezago" in m["banderas"] and "incompleto" not in m["banderas"]:
            if m.get("motivo_rezago") == "antes_de_hueco":
                notas.append(f"{nombre} es el último mes antes de un periodo sin datos y puede estar incompleto por "
                             f"rezago en la captura; tómalo con cautela.")
            else:
                notas.append(f"{nombre} es uno de los meses más recientes y puede estar incompleto por rezago en la "
                             f"captura; tómalo con cautela.")
        m["estado"] = ("sin_datos" if "sin_datos" in m["banderas"] else
                       "incompleto" if "incompleto" in m["banderas"] else
                       "posible_rezago" if "posible_rezago" in m["banderas"] else
                       "parcial" if "parcial" in m["banderas"] else "completo")
        m["nota"] = " ".join(notas)


# ---------- Ingesta de un dataset ----------

def validar_config(cfg: dict, carpeta: Path, ids_categorias: set[str]) -> list[str]:
    errores = []
    for clave in ("id", "categoria", "nombre", "descripcion", "columnas"):
        if clave not in cfg:
            errores.append(f"Falta '{clave}' en dataset.toml.")
    if "id" in cfg and not ID_DATASET.match(cfg["id"]):
        errores.append("El 'id' solo puede tener minúsculas, números y guion bajo.")
    if cfg.get("categoria") and cfg["categoria"] not in ids_categorias:
        errores.append(f"La categoría {cfg['categoria']!r} no existe en data/categorias.toml.")
    for c in cfg.get("columnas", []):
        desconocidas = set(c) - CLAVES_COLUMNA
        if desconocidas:
            errores.append(f"La columna {c.get('alias')!r} tiene claves desconocidas {sorted(desconocidas)} "
                           f"(¿una clave suelta quedó después de [[columnas]]?).")
        if c.get("tipo") not in ("categoria", "codigo", "numero"):
            errores.append(f"La columna {c.get('alias')!r} tiene un tipo no válido: {c.get('tipo')!r}.")
        if c.get("tipo") == "codigo" and not c.get("etiqueta"):
            errores.append(f"La columna {c.get('alias')!r} es de tipo codigo y le falta 'etiqueta'.")
    alias = [c.get("alias", "") for c in cfg.get("columnas", [])]
    for a in alias:
        if not IDENTIFICADOR.match(a or ""):
            errores.append(f"Alias de columna no válido: {a!r}.")
    if len(alias) != len(set(alias)):
        errores.append("Hay alias de columna repetidos.")
    reservados = DERIVADAS | {"rango_edad_orden", "archivo_origen"}
    for a in set(alias) & reservados:
        errores.append(f"El alias {a!r} está reservado para columnas derivadas.")
    # Mínimos de privacidad reforzados para ciertas combinaciones de columnas.
    for comb in cfg.get("privacidad", {}).get("combinaciones", []):
        listas = comb.get("columnas", [])
        if len(listas) < 2 or not all(isinstance(l, list) and l for l in listas):
            errores.append("Cada [[privacidad.combinaciones]] necesita 'columnas' con al menos dos listas de alias.")
        desconocidos = sorted({a for l in listas if isinstance(l, list) for a in l} - set(alias) - DERIVADAS)
        if desconocidos:
            errores.append(f"[[privacidad.combinaciones]] menciona columnas que no existen: {desconocidos}.")
        if not isinstance(comb.get("minimo_por_grupo"), int) or comb["minimo_por_grupo"] < 1:
            errores.append("Cada [[privacidad.combinaciones]] necesita 'minimo_por_grupo' (entero positivo).")
    # Cruces que se rechazan por privacidad.
    for bloqueo in cfg.get("privacidad", {}).get("bloqueos", []):
        listas = bloqueo.get("columnas", [])
        if len(listas) < 2 or not all(isinstance(l, list) and l for l in listas):
            errores.append("Cada [[privacidad.bloqueos]] necesita 'columnas' con al menos dos listas de alias.")
        desconocidos = sorted({a for l in listas if isinstance(l, list) for a in l} - set(alias) - DERIVADAS)
        if desconocidos:
            errores.append(f"[[privacidad.bloqueos]] menciona columnas que no existen: {desconocidos}.")
    por_alias = {c.get("alias"): c for c in cfg.get("columnas", [])}
    # Temas restringidos y valores que no se consultan por separado.
    for seccion in ("temas_restringidos", "valores_no_disponibles"):
        for regla in cfg.get(seccion, []):
            col = por_alias.get(regla.get("columna"))
            if not col or col.get("tipo") not in ("categoria", "codigo"):
                errores.append(f"[[{seccion}]] necesita 'columna' de tipo categoria o codigo (tiene {regla.get('columna')!r}).")
            if not (regla.get("claves") or regla.get("prefijos")):
                errores.append(f"[[{seccion}]] necesita 'claves' o 'prefijos'.")
            if seccion == "temas_restringidos":
                if not regla.get("id"):
                    errores.append("Cada [[temas_restringidos]] necesita 'id'.")
                otras = sorted(set(regla.get("columnas_permitidas", [])) - set(alias) - DERIVADAS)
                if otras:
                    errores.append(f"[[temas_restringidos]] {regla.get('id')!r} permite columnas que no existen: {otras}.")
            elif not regla.get("motivo"):
                errores.append("Cada [[valores_no_disponibles]] necesita 'motivo'.")
    for c in cfg.get("columnas", []):
        if c.get("guardar") is False and c.get("expuesta"):
            errores.append(f"La columna {c.get('alias')!r} no puede ser expuesta y no guardarse (guardar = false).")
    por_columna = cfg.get("completitud", {}).get("por_columna")
    if por_columna and por_columna.get("columna") not in alias:
        errores.append(f"[completitud] por_columna menciona una columna que no existe: {por_columna.get('columna')!r}.")
    return errores


def ingerir_dataset(con, carpeta: Path, cfg: dict, diccionarios: dict, reporte: Reporte) -> dict | None:
    ds_id = cfg["id"]
    tabla = f"ds_{ds_id}"
    arch = cfg.get("archivos", {})
    separador = arch.get("separador", ",")
    candidatas = arch.get("codificaciones", ["utf-8", "latin-1"])
    siglas = {s.upper() for s in cfg.get("siglas", [])}
    columnas_cfg = cfg["columnas"]
    col_fecha = cfg.get("fecha", {}).get("columna")
    formato_fecha = cfg.get("fecha", {}).get("formato", "%Y-%m-%d")
    col_id = cfg.get("identificador", {}).get("columna")
    unidad_plural = cfg.get("unidad", {}).get("plural", "registros")

    # 1. Archivos: exclusiones (documentación), codificación y cabecera.
    excluir = arch.get("excluir", [])
    documentacion = [r.name for r in sorted(carpeta.glob(arch.get("patron", "*.csv")))
                     if any(fnmatch.fnmatch(r.name, p) for p in excluir)]
    for nombre_doc in documentacion:
        reporte.agregar(ds_id, "info", f"{nombre_doc}: se tomó como documentación (archivos.excluir), no como datos.")
    archivos = [r for r in sorted(carpeta.glob(arch.get("patron", "*.csv"))) if r.name not in documentacion]
    # Equivalencias: nombres de columna distintos entre periodos que significan lo mismo.
    equivalencias = {k.strip(): v.strip() for k, v in arch.get("equivalencias", {}).items()}
    info_archivos = []
    for ruta in archivos:
        cod = detectar_codificacion(ruta, candidatas)
        if cod is None:
            reporte.agregar(ds_id, "error", f"{ruta.name}: no se pudo leer con {candidatas}; se excluyó.")
            continue
        original = leer_cabecera(ruta, cod, separador)
        info_archivos.append({"ruta": ruta, "archivo": ruta.name, "codificacion": cod, "cabecera_original": original,
                              "cabecera": tuple(equivalencias.get(c, c) for c in original)})
    if not info_archivos:
        reporte.agregar(ds_id, "error", "No hay archivos legibles; el dataset se omitió.")
        return None

    # Se exige el mismo conjunto de columnas (con las equivalencias aplicadas); el orden puede cambiar.
    referencia = Counter(frozenset(a["cabecera"]) for a in info_archivos).most_common(1)[0][0]
    cabecera = next(a["cabecera"] for a in info_archivos if frozenset(a["cabecera"]) == referencia)
    validos = []
    for a in info_archivos:
        if frozenset(a["cabecera"]) != referencia or len(a["cabecera"]) != len(set(a["cabecera"])):
            faltan = sorted(set(cabecera) - set(a["cabecera"]))
            sobran = sorted(set(a["cabecera"]) - set(cabecera))
            reporte.agregar(ds_id, "error",
                            f"{a['archivo']}: su cabecera no coincide con la de los demás archivos "
                            f"(faltan {faltan or 'ninguna'}, sobran {sobran or 'ninguna'}); se excluyó. "
                            f"Si es la misma columna con otro nombre, agrégala en [archivos.equivalencias].")
        else:
            validos.append(a)
    esquemas = Counter(a["cabecera_original"] for a in validos)
    if len(esquemas) > 1:
        reporte.agregar(ds_id, "info", f"Los archivos usan {len(esquemas)} cabeceras distintas, unificadas con "
                                       f"[archivos.equivalencias]: "
                                       + "; ".join(f"{n} archivos con {list(c)}" for c, n in esquemas.items()) + ".")
    requeridas = [c["nombre"] for c in columnas_cfg] + [x for x in (col_fecha, col_id) if x]
    requeridas += [c["etiqueta"] for c in columnas_cfg if c.get("etiqueta")]
    faltantes = [c for c in requeridas if c not in cabecera]
    if faltantes:
        reporte.agregar(ds_id, "error", f"La configuración menciona columnas que no están en los CSV: {faltantes}. Se omitió el dataset.")
        return None
    no_configuradas = [c for c in cabecera if c not in requeridas]
    if no_configuradas:
        reporte.agregar(ds_id, "aviso", f"Columnas del CSV sin configuración (no se cargan): {no_configuradas}.")
    invalidas = [c for c in cabecera if not IDENTIFICADOR.match(c)]
    if invalidas:
        reporte.agregar(ds_id, "error", f"Nombres de columna no válidos {invalidas}: agrega una equivalencia en "
                                        f"[archivos.equivalencias]. Se omitió el dataset.")
        return None

    # 2. Carga cruda (todo como texto). Cada grupo de archivos con la misma codificación y cabecera se lee
    #    con los nombres canónicos (names=…), así una columna como "clave_grupo_ enfermedad" queda bien nombrada.
    grupos_lectura = defaultdict(list)
    for a in validos:
        cod = CODIFICACIONES_DUCKDB.get(a["codificacion"], a["codificacion"])
        grupos_lectura[(cod, a["cabecera"])].append(a["ruta"].as_posix())
    columnas_sql = ", ".join(q(c) for c in cabecera)
    partes = []
    for (cod, nombres), rutas in grupos_lectura.items():
        lista = "[" + ", ".join(literal(r) for r in rutas) + "]"
        nombres_sql = "[" + ", ".join(literal(n) for n in nombres) + "]"
        partes.append(
            f"SELECT {columnas_sql}, regexp_extract(filename, '[^/\\\\]+$') AS archivo_origen "
            f"FROM read_csv({lista}, encoding={literal(cod)}, all_varchar=true, header=true, names={nombres_sql}, "
            f"delim={literal(separador)}, quote='\"', filename=true)")
    con.execute("CREATE OR REPLACE TEMP TABLE crudo AS " + " UNION ALL ".join(partes))
    total = con.execute("SELECT count(*) FROM crudo").fetchone()[0]

    # 3. Revisiones sobre los datos crudos.
    calidad = {"filas_leidas": total}
    # Filas idénticas en todas las columnas: solo pueden venir de partes encimadas; se eliminan y se reportan.
    distintas = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT {columnas_sql} FROM crudo)").fetchone()[0]
    calidad["filas_identicas_eliminadas"] = total - distintas
    if total > distintas and cfg.get("identificador", {}).get("eliminar_filas_identicas", True):
        con.execute(f"CREATE OR REPLACE TEMP TABLE crudo AS SELECT * EXCLUDE (_n) FROM ("
                    f"SELECT *, row_number() OVER (PARTITION BY {columnas_sql} ORDER BY archivo_origen) AS _n FROM crudo) "
                    f"WHERE _n = 1")
        reporte.agregar(ds_id, "aviso", f"Se eliminaron {miles(total - distintas)} filas idénticas repetidas entre archivos.")
        total = distintas
    elif total > distintas:
        calidad["filas_identicas_eliminadas"] = 0
        reporte.agregar(ds_id, "aviso", f"Hay {miles(total - distintas)} filas idénticas repetidas (no se eliminaron).")
    calidad["filas"] = total
    if col_id:
        repetidos = con.execute(f"SELECT count(*) - count(DISTINCT {q(col_id)}) FROM crudo").fetchone()[0]
        calidad["ids_repetidos"] = repetidos
        if repetidos and col_fecha:
            misma_fecha = con.execute(f"SELECT count(*) - count(DISTINCT ({q(col_id)}, {q(col_fecha)})) FROM crudo").fetchone()[0]
            calidad["ids_repetidos_misma_fecha"] = misma_fecha
            if misma_fecha:
                reporte.agregar(ds_id, "aviso", f"{miles(misma_fecha)} identificadores de '{col_id}' se repiten con la misma "
                                                f"fecha pero otros datos: revisar si son correcciones o duplicados.")
            reporte.agregar(ds_id, "info", f"{miles(repetidos)} identificadores de '{col_id}' se repiten con datos distintos "
                                           f"(la numeración se reinicia entre periodos); no se tratan como duplicados.")
        elif repetidos:
            reporte.agregar(ds_id, "aviso", f"Hay {miles(repetidos)} identificadores repetidos en '{col_id}'.")
    if col_fecha:
        malas = con.execute(
            f"SELECT count(*) FROM crudo WHERE try_strptime({q(col_fecha)}, ?) IS NULL", [formato_fecha]).fetchone()[0]
        calidad["fechas_invalidas"] = malas
        if malas:
            reporte.agregar(ds_id, "aviso", f"{miles(malas)} filas con fecha vacía o inválida (no cuentan para el periodo).")
    vacios = {}
    for c in cabecera:
        n = con.execute(f"SELECT count(*) FROM crudo WHERE {q(c)} IS NULL OR trim({q(c)}) = ''").fetchone()[0]
        if n:
            vacios[c] = {"filas": n, "porcentaje": round(100 * n / total, 2)}
    calidad["vacios"] = vacios

    # 4. Tablas de mapeo para normalizar textos (sobre valores distintos, no sobre todas las filas).
    por_alias = {c["alias"]: c for c in columnas_cfg}
    selects, joins = [], []
    mapas: dict[str, dict] = {}                      # alias → {texto original: texto normalizado}
    variantes_por_columna: dict[str, list] = {}      # alias → [(clave, descripción alternativa)]
    for c in columnas_cfg:
        nombre, alias, tipo = c["nombre"], c["alias"], c["tipo"]
        dic = diccionarios.get(c["diccionario"]) if c.get("diccionario") else None
        if c.get("diccionario") and dic is None:
            reporte.agregar(ds_id, "error", f"No existe el diccionario {c['diccionario']!r} (columna {alias}).")
        if tipo == "numero":
            no_num = con.execute(
                f"SELECT count(*) FROM crudo WHERE trim({q(nombre)}) <> '' AND try_cast({q(nombre)} AS DOUBLE) IS NULL"
            ).fetchone()[0]
            if no_num:
                reporte.agregar(ds_id, "aviso", f"'{nombre}': {miles(no_num)} valores no numéricos (se dejan vacíos).")
            no_entero = con.execute(
                f"SELECT count(*) FROM crudo WHERE try_cast({q(nombre)} AS DOUBLE) <> floor(try_cast({q(nombre)} AS DOUBLE))"
            ).fetchone()[0]
            tipo_sql = "DOUBLE" if no_entero else "BIGINT"
            selects.append(f"try_cast(try_cast(c.{q(nombre)} AS DOUBLE) AS {tipo_sql}) AS {q(alias)}")
        elif tipo == "codigo":
            etiqueta = c["etiqueta"]
            ultima = (f"max(try_strptime({q(col_fecha)}, {literal(formato_fecha)}))" if col_fecha else "NULL")
            filas = con.execute(
                f"SELECT upper(trim({q(nombre)})), {q(etiqueta)}, count(*), {ultima} FROM crudo "
                f"WHERE {q(nombre)} IS NOT NULL GROUP BY ALL").fetchall()
            por_clave = defaultdict(list)
            for clave, desc, n, fecha_max in filas:
                por_clave[clave].append((desc, n, fecha_max))
            # Si una clave tiene varias descripciones, se muestra la de los registros más recientes
            # (desempata la más frecuente); todas las variantes se guardan para la búsqueda.
            mapa, variantes, cambian_palabras, solo_forma = [], [], [], 0
            for clave, descs in por_clave.items():
                elegida = max(descs, key=lambda d: (d[2] or dt.datetime.min, d[1]))[0]
                principal = normalizar_valor(elegida, c, siglas, dic)
                mapa.append((clave, principal))
                if len(descs) > 1:
                    formas = {clave_comparacion(d[0]) for d in descs}
                    if len(formas) > 1:
                        cambian_palabras.append(f"{clave}: " + " / ".join(sorted({d[0] for d in descs})))
                    else:
                        solo_forma += 1
                    otras = {normalizar_valor(d[0], c, siglas, dic) for d in descs} - {principal}
                    variantes += [(clave, t) for t in sorted(otras)]
            if solo_forma:
                reporte.agregar(ds_id, "info", f"{solo_forma} claves de '{alias}' tienen descripciones que solo cambian en "
                                               f"acentos, espacios o signos; se muestra la más reciente.")
            if cambian_palabras:
                reporte.agregar(ds_id, "aviso", f"{len(cambian_palabras)} claves de '{alias}' tienen descripciones con "
                                                f"palabras distintas; se muestra la más reciente y se busca en todas. "
                                                f"Ejemplos: " + "; ".join(cambian_palabras[:10]) + ".")
            variantes_por_columna[alias] = variantes
            if c.get("patron"):
                raras = sorted(k for k in por_clave if not re.match(c["patron"], k))
                if raras:
                    reporte.agregar(ds_id, "aviso", f"{len(raras)} claves de '{alias}' no siguen el patrón esperado: {raras[:15]}.")
            _crear_mapa(con, f"map_{alias}", mapa)
            selects.append(f"upper(trim(c.{q(nombre)})) AS {q(alias)}")
            selects.append(f"m_{alias}.valor AS {q(alias + '_nombre')}")
            joins.append(f"LEFT JOIN map_{alias} m_{alias} ON upper(trim(c.{q(nombre)})) = m_{alias}.original")
        else:  # categoria
            frecuencias = dict(con.execute(f"SELECT {q(nombre)}, count(*) FROM crudo WHERE {q(nombre)} IS NOT NULL "
                                           f"GROUP BY 1").fetchall())
            distintos = list(frecuencias)
            if c.get("unificar") == "sin_signos":
                mapa = _unificar_sin_signos(frecuencias, c, siglas, dic, alias, ds_id, reporte)
            else:
                mapa = [(v, normalizar_valor(v, c, siglas, dic)) for v in distintos]
            mapas[alias] = dict(mapa)
            if dic is not None:
                sin_dic = sorted({v for v in distintos if dic.buscar(v) is None})
                if sin_dic:
                    reporte.agregar(ds_id, "aviso", f"Valores de '{alias}' que no están en el diccionario {c['diccionario']!r}: {sin_dic[:15]}.")
            _crear_mapa(con, f"map_{alias}", mapa)
            joins.append(f"LEFT JOIN map_{alias} m_{alias} ON c.{q(nombre)} = m_{alias}.original")
            if c.get("compuesta_con"):
                otra = c["compuesta_con"]
                selects.append(f"m_{alias}.valor || ' (' || m_{otra}.valor || ')' AS {q(alias)}")
            else:
                selects.append(f"m_{alias}.valor AS {q(alias)}")

    # Columnas derivadas: fecha ISO, mes y rangos de edad.
    if col_fecha:
        selects.insert(0, f"try_strptime(c.{q(col_fecha)}, {literal(formato_fecha)})::DATE AS fecha")
        selects.insert(1, f"strftime(try_strptime(c.{q(col_fecha)}, {literal(formato_fecha)}), '%Y-%m') AS mes")
    rangos = cfg.get("edad", {}).get("rangos", [])
    col_edad = cfg.get("edad", {}).get("columna")
    if rangos and col_edad:
        edad_sql = f"try_cast(c.{q(col_edad)} AS DOUBLE)"
        casos = " ".join(f"WHEN {edad_sql} BETWEEN {float(r[1])} AND {float(r[2])} THEN {literal(r[0])}" for r in rangos)
        orden = " ".join(f"WHEN {edad_sql} BETWEEN {float(r[1])} AND {float(r[2])} THEN {i}" for i, r in enumerate(rangos))
        selects.append(f"CASE {casos} END AS rango_edad")
        selects.append(f"CASE {orden} END AS rango_edad_orden")
    selects.insert(0, "c.archivo_origen AS archivo_origen")

    # 5. Tabla final (sin el identificador de registro).
    con.execute(f"CREATE OR REPLACE TABLE {q(tabla)} AS SELECT {', '.join(selects)} FROM crudo c {' '.join(joins)} "
                + ("ORDER BY fecha" if col_fecha else ""))
    # Descripciones alternativas de cada clave (solo para buscar_valores).
    con.execute(f"CREATE OR REPLACE TABLE {q(tabla + '_variantes')} (columna VARCHAR, clave VARCHAR, texto VARCHAR)")
    filas_variantes = [(a, k, t) for a, vs in variantes_por_columna.items() for k, t in vs]
    if filas_variantes:
        con.executemany(f"INSERT INTO {q(tabla + '_variantes')} VALUES (?, ?, ?)", filas_variantes)

    entrada = {
        "id": ds_id,
        "tabla": tabla,
        "carpeta": carpeta.relative_to(config.RAIZ).as_posix() if carpeta.is_relative_to(config.RAIZ) else carpeta.as_posix(),
        "filas": con.execute(f"SELECT count(*) FROM {q(tabla)}").fetchone()[0],
        "archivos": [],
        "archivos_excluidos": [a["archivo"] for a in info_archivos if a not in validos],
        "documentacion": documentacion,
        "calidad": calidad,
    }
    for a in validos:
        fila = con.execute(f"SELECT count(*), min(fecha), max(fecha) FROM {q(tabla)} WHERE archivo_origen = ?"
                           if col_fecha else f"SELECT count(*), NULL, NULL FROM {q(tabla)} WHERE archivo_origen = ?",
                           [a["archivo"]]).fetchone()
        entrada["archivos"].append({"archivo": a["archivo"], "codificacion": a["codificacion"], "filas": fila[0],
                                    "desde": fila[1].isoformat() if fila[1] else None,
                                    "hasta": fila[2].isoformat() if fila[2] else None})

    # 6. Periodo y completitud (calculados de los datos, nunca escritos a mano).
    if col_fecha:
        inicio, fin = con.execute(f"SELECT min(fecha), max(fecha) FROM {q(tabla)}").fetchone()
        if inicio:
            conteo = dict(con.execute(f"SELECT fecha, count(*) FROM {q(tabla)} WHERE fecha IS NOT NULL GROUP BY fecha").fetchall())
            comp = cfg.get("completitud", {})
            meses = evaluar_completitud(conteo, inicio, fin, comp.get("umbral_dia_habil", 0.5),
                                        comp.get("meses_rezago", 0), unidad_plural,
                                        comp.get("rezago_antes_de_hueco", False))
            por_columna = comp.get("por_columna")
            if por_columna:
                # Cobertura por valor (por ejemplo, municipios que dejan de reportar en un mes).
                serie = defaultdict(dict)
                for valor, mes, n in con.execute(f"SELECT {q(por_columna['columna'])}, mes, count(*) FROM {q(tabla)} "
                                                 f"WHERE mes IS NOT NULL AND {q(por_columna['columna'])} IS NOT NULL "
                                                 f"GROUP BY 1, 2").fetchall():
                    serie[valor][mes] = n
                evaluar_cobertura(meses, serie, por_columna.get("umbral", 0.5), por_columna.get("peso_minimo", 0.2),
                                  unidad_plural)
                for m in meses:
                    if m.get("cobertura_parcial"):
                        reporte.agregar(ds_id, "aviso", f"{m['nombre']}: faltan o bajan mucho los registros de "
                                                        f"{lista_legible(m['cobertura_parcial'])}; se marca como incompleto.")
            huecos = [m["nombre"] for m in meses if m["estado"] == "sin_datos"]
            texto = periodo_legible(inicio, fin) + (f" (sin datos de {', '.join(huecos)})" if huecos else "")
            entrada["periodo"] = {"inicio": inicio.isoformat(), "fin": fin.isoformat(), "texto": texto,
                                  "dias_con_datos": len(conteo), "meses_sin_datos": [m["mes"] for m in meses if m["estado"] == "sin_datos"]}
            entrada["meses"] = meses
            for nombre_hueco in huecos:
                reporte.agregar(ds_id, "aviso", f"No hay ningún registro de {nombre_hueco} (hueco dentro del periodo).")

    # 7. Columnas para el catálogo (valores distintos, vacíos y metadatos).
    privacidad = cfg.get("privacidad", {})
    k_minimo = int(privacidad.get("minimo_por_grupo", 5)) if privacidad.get("ocultar_valores_poco_frecuentes") else 0
    columnas = []
    for c in columnas_cfg:
        alias = c["alias"]
        col = {k: c.get(k) for k in ("alias", "tipo", "descripcion", "motivo", "sinonimos", "unidad", "patron")
               if c.get(k) is not None}
        col["columna_origen"] = c["nombre"]
        col["expuesta"] = bool(c.get("expuesta", False))
        col["guardada"] = c.get("guardar", True) is not False
        col["cuasi_identificador"] = bool(c.get("cuasi_identificador", False))
        col["conteo_distinto"] = bool(c.get("conteo_distinto", False))
        col["buscable"] = bool(c.get("buscable", False))
        col["vacios"] = vacios.get(c["nombre"], {"filas": 0, "porcentaje": 0.0})
        if not col["expuesta"] and col.get("motivo") and col["vacios"]["filas"]:
            # El % de vacíos se calcula en cada ingesta (nunca se escribe a mano en la configuración).
            col["motivo"] = f"{col['motivo'].rstrip('. ')}. Vacíos: {col['vacios']['porcentaje']:.1f}% de los registros."
        if c["tipo"] in ("categoria", "codigo"):
            col["n_distintos"] = con.execute(f"SELECT count(DISTINCT {q(alias)}) FROM {q(tabla)}").fetchone()[0]
            # Valores que no se listan: poco frecuentes (si el dataset lo pide) o no disponibles por configuración.
            ocultos = valores_ocultos(con, tabla, cfg, alias, k_minimo)
            if ocultos:
                col["valores_ocultos"] = len(ocultos)
            if col["n_distintos"] <= MAX_VALORES_LISTADOS:
                if c["tipo"] == "codigo":
                    filas = con.execute(f"SELECT DISTINCT {q(alias)}, {q(alias + '_nombre')} FROM {q(tabla)} "
                                        f"WHERE {q(alias)} IS NOT NULL ORDER BY 1").fetchall()
                    col["valores"] = [{"clave": k, "nombre": n} for k, n in filas if k not in ocultos]
                else:
                    filas = con.execute(f"SELECT DISTINCT {q(alias)} FROM {q(tabla)} WHERE {q(alias)} IS NOT NULL ORDER BY 1").fetchall()
                    col["valores"] = [f[0] for f in filas if f[0] not in ocultos]
        elif c["tipo"] == "numero":
            mn, mx = con.execute(f"SELECT min({q(alias)}), max({q(alias)}) FROM {q(tabla)}").fetchone()
            col["minimo"], col["maximo"] = mn, mx
        columnas.append(col)
    entrada["columnas"] = columnas
    derivadas = []
    if col_fecha:
        derivadas.append({"alias": "fecha", "tipo": "fecha", "descripcion": "Fecha (AAAA-MM-DD). Se puede filtrar por rango, pero no agrupar por día."})
        derivadas.append({"alias": "mes", "tipo": "mes", "descripcion": "Mes (AAAA-MM). Se puede filtrar y agrupar."})
    if rangos and col_edad:
        derivadas.append({"alias": "rango_edad", "tipo": "rango", "descripcion": "Grupo de edad.",
                          "valores": [r[0] for r in rangos]})
    entrada["derivadas"] = derivadas

    # 8. Controles configurables: calidad, valores esperados, jerarquías y nombres repetidos.
    entrada["calidad"]["controles"] = _controles_calidad(con, tabla, cfg, por_alias, ds_id, reporte)
    entrada["calidad"]["faltantes"] = _valores_esperados(con, tabla, cfg, diccionarios, ds_id, reporte)
    entrada["calidad"]["jerarquia"] = _controles_jerarquia(con, tabla, cfg, por_alias, ds_id, reporte)
    entrada["calidad"]["nombres_repetidos"] = _nombres_repetidos(con, cfg, mapas, ds_id, reporte)
    entrada["calidad"]["cobertura_parcial"] = _cobertura_parcial(con, tabla, cfg, entrada.get("periodo"), ds_id, reporte)
    entrada["vocabulario"] = _vocabulario(con, tabla, cfg, k_minimo)

    # 9. Columnas que solo sirven para los controles de la ingesta: se borran de la base.
    for c in columnas_cfg:
        if c.get("guardar") is False:
            con.execute(f"ALTER TABLE {q(tabla)} DROP COLUMN {q(c['alias'])}")
            if c["tipo"] == "codigo":
                con.execute(f"ALTER TABLE {q(tabla)} DROP COLUMN {q(c['alias'] + '_nombre')}")
            reporte.agregar(ds_id, "info", f"La columna '{c['alias']}' se usó en los controles de calidad y se borró de "
                                           f"la base (guardar = false).")
    return entrada


def valores_ocultos(con, tabla: str, cfg: dict, alias: str, k_minimo: int) -> set:
    """Valores de una columna que no se listan ni se pueden filtrar: los que tienen menos de `k_minimo`
    registros en todo el periodo (0 = no aplica) y los de [[valores_no_disponibles]]."""
    filas = con.execute(f"SELECT {q(alias)}, count(*) FROM {q(tabla)} WHERE {q(alias)} IS NOT NULL GROUP BY 1").fetchall()
    reglas = [r for r in cfg.get("valores_no_disponibles", []) if r.get("columna") == alias]
    return {v for v, n in filas if n < k_minimo or any(valor_en_regla(v, r) for r in reglas)}


def _crear_mapa(con, nombre: str, filas: list[tuple[str, str]]):
    con.execute(f"CREATE OR REPLACE TEMP TABLE {q(nombre)} (original VARCHAR, valor VARCHAR)")
    if filas:
        con.executemany(f"INSERT INTO {q(nombre)} VALUES (?, ?)", filas)


def _controles_calidad(con, tabla, cfg, por_alias, ds_id, reporte) -> list[dict]:
    resultados = []
    for ctrl in cfg.get("controles_calidad", []):
        condiciones, params = [], []
        for parte in (ctrl, ctrl.get("solo_si")):
            if not parte:
                continue
            if parte["columna"] not in por_alias or parte["operador"] not in OPERADORES_CONTROL:
                reporte.agregar(ds_id, "error", f"Control de calidad mal configurado: {ctrl.get('descripcion')}.")
                break
            condiciones.append(f"{q(parte['columna'])} {parte['operador']} ?")
            params.append(parte["valor"])
        else:
            n = con.execute(f"SELECT count(*) FROM {q(tabla)} WHERE {' AND '.join(condiciones)}", params).fetchone()[0]
            resultados.append({"descripcion": ctrl["descripcion"], "filas": n})
            if n:
                reporte.agregar(ds_id, "aviso", f"{ctrl['descripcion']}: {miles(n)} filas.")
    return resultados


def _valores_esperados(con, tabla, cfg, diccionarios, ds_id, reporte) -> list[dict]:
    resultados = []
    for esp in cfg.get("valores_esperados", []):
        dic = diccionarios.get(esp["diccionario"])
        if dic is None:
            continue
        presentes = {clave_comparacion(r[0]) for r in
                     con.execute(f"SELECT DISTINCT {q(esp['columna'])} FROM {q(tabla)}").fetchall() if r[0]}
        faltan = [n for n in dic.nombres() if clave_comparacion(n) not in presentes]
        resultados.append({"columna": esp["columna"], "esperados": len(dic.nombres()),
                           "presentes": len(presentes), "faltan": faltan})
        if faltan:
            reporte.agregar(ds_id, "aviso", f"'{esp['columna']}': hay {len(presentes)} de {len(dic.nombres())} valores esperados. Faltan: {', '.join(faltan)}.")
    return resultados


def _controles_jerarquia(con, tabla, cfg, por_alias, ds_id, reporte) -> list[dict]:
    resultados = []
    for ctrl in cfg.get("controles_jerarquia", []):
        padre, hija, n = ctrl["padre"], ctrl["hija"], int(ctrl.get("caracteres", 1))
        filas = con.execute(f"SELECT {q(padre)}, left({q(hija)}, ?), count(*) FROM {q(tabla)} GROUP BY ALL ORDER BY 1, 2", [n]).fetchall()
        por_padre = defaultdict(dict)
        for p, prefijo, cuenta in filas:
            por_padre[p][prefijo] = cuenta
        for p, prefijos in sorted(por_padre.items()):
            total = sum(prefijos.values())
            raros = {k: v for k, v in prefijos.items() if len(prefijos) > 1 and v / total < 0.02}
            resultados.append({"padre": p, "prefijos": prefijos, "minoritarios": raros})
            if raros:
                reporte.agregar(ds_id, "info", f"{ctrl['descripcion']}: en {padre} {p} hay prefijos minoritarios {raros} (de {miles(total)}); revisar.")
    return resultados


def _unificar_sin_signos(frecuencias: dict, c: dict, siglas, dic, alias, ds_id, reporte) -> list[tuple[str, str]]:
    """Agrupa las variantes que solo cambian en signos o espacios ('C.S.U. DR. COSS' = 'C S U  DR  COSS')
    y les da una sola escritura: la variante con más acentos (desempata la más frecuente), sin signos."""
    grupos = defaultdict(list)
    for valor, n in frecuencias.items():
        grupos[clave_comparacion(valor)].append((valor, n))
    mapa, unificados = [], []
    for variantes in grupos.values():
        elegida = max(variantes, key=lambda v: (sum(1 for ch in v[0] if ord(ch) > 127), v[1]))[0]
        limpia = re.sub(r"[^\wÁÉÍÓÚÜÑáéíóúüñ]+", " ", elegida).replace("_", " ")
        mostrar = normalizar_valor(limpia, {**c, "doble_espacio": "espacio"}, siglas, dic)
        mapa += [(v, mostrar) for v, _ in variantes]
        if len(variantes) > 1:
            unificados.append(f"{mostrar} ← " + " / ".join(sorted(v for v, _ in variantes)))
    if unificados:
        reporte.agregar(ds_id, "info", f"{len(unificados)} valores de '{alias}' tenían varias escrituras (solo cambian "
                                       f"signos o espacios) y se unificaron. Ejemplos: " + "; ".join(unificados[:8]) + ".")
    return mapa


def _cobertura_parcial(con, tabla, cfg, periodo, ds_id, reporte) -> list[dict]:
    """Valores de columnas unificadas que solo aparecen en parte del periodo (unidades que abren, cierran o
    cambian de nombre). Solo se reportan; no se fusionan a ciegas."""
    if not periodo:
        return []
    inicio, fin = dt.date.fromisoformat(periodo["inicio"]), dt.date.fromisoformat(periodo["fin"])
    resultados = []
    for c in cfg["columnas"]:
        if c.get("unificar") != "sin_signos":
            continue
        filas = con.execute(f"SELECT {q(c['alias'])}, min(fecha), max(fecha), count(*) FROM {q(tabla)} "
                            f"GROUP BY 1 HAVING min(fecha) > ? OR max(fecha) < ? ORDER BY 2",
                            [inicio + dt.timedelta(days=31), fin - dt.timedelta(days=31)]).fetchall()
        for valor, desde, hasta, n in filas:
            resultados.append({"columna": c["alias"], "valor": valor, "desde": desde.isoformat(),
                               "hasta": hasta.isoformat(), "filas": n})
        if filas:
            reporte.agregar(ds_id, "info", f"{len(filas)} valores de '{c['alias']}' solo aparecen en parte del periodo "
                                           f"(pueden haber abierto, cerrado o cambiado de nombre; no se fusionan). Ejemplos: "
                                           + "; ".join(f"{v} ({d} a {h})" for v, d, h, _ in filas[:10]) + ".")
    return resultados


def _nombres_repetidos(con, cfg, mapas, ds_id, reporte) -> list[dict]:
    """Nombres que aparecen en más de un valor de su columna compañera (p. ej. la misma unidad en dos municipios)."""
    resultados = []
    por_alias = {c["alias"]: c for c in cfg["columnas"]}
    for c in cfg["columnas"]:
        if not c.get("compuesta_con"):
            continue
        otra = por_alias[c["compuesta_con"]]
        filas = con.execute(f"SELECT {q(c['nombre'])}, {q(otra['nombre'])}, count(*) FROM crudo GROUP BY ALL").fetchall()
        por_nombre = defaultdict(dict)
        for nombre, compania, n in filas:
            mostrar, mostrar_otra = mapas[c["alias"]].get(nombre, nombre), mapas[otra["alias"]].get(compania, compania)
            por_nombre[mostrar][mostrar_otra] = por_nombre[mostrar].get(mostrar_otra, 0) + n
        for nombre, companias in sorted(por_nombre.items()):
            if len(companias) > 1:
                resultados.append({"nombre": nombre, "en": companias})
        if resultados:
            reporte.agregar(ds_id, "aviso",
                            f"{len(resultados)} nombres de '{c['alias']}' se repiten en varios valores de '{otra['alias']}' "
                            f"(se distinguen agregando el {otra['alias']} entre paréntesis): "
                            + "; ".join(f"{r['nombre']} en {', '.join(r['en'])}" for r in resultados) + ".")
    return resultados


def _vocabulario(con, tabla, cfg, k_minimo: int = 0) -> list[str]:
    """Palabras que indican que una pregunta trata de este dataset (para el filtro de alcance).
    No incluye las de valores ocultos (poco frecuentes o no disponibles)."""
    textos = list(cfg.get("palabras_clave", [])) + [cfg["nombre"]]
    for c in cfg["columnas"]:
        if not c.get("expuesta"):
            continue
        campo = c["alias"] + "_nombre" if c["tipo"] == "codigo" else c["alias"]
        if c["tipo"] in ("categoria", "codigo") and not c.get("compuesta_con"):
            ocultos = valores_ocultos(con, tabla, cfg, c["alias"], k_minimo)
            textos += [r[1] for r in con.execute(f"SELECT DISTINCT {q(c['alias'])}, {q(campo)} FROM {q(tabla)} "
                                                 f"WHERE {q(campo)} IS NOT NULL").fetchall() if r[0] not in ocultos]
    palabras = set()
    for t in textos:
        for p in re.split(r"[^a-z0-9ñ]+", texto_busqueda(t)):
            if len(p) >= 4 and not p.isdigit() and p not in PALABRAS_VACIAS:
                palabras.add(p)
    return sorted(palabras)


# ---------- Proceso completo ----------

def ingerir(data_dir: Path = config.DATA_DIR, build_dir: Path = config.BUILD_DIR) -> dict:
    """Reconstruye la base y el catálogo. Devuelve el catálogo."""
    build_dir.mkdir(parents=True, exist_ok=True)
    reporte = Reporte()
    diccionarios = cargar_diccionarios(data_dir, reporte)
    comunes = cargar_comunes(data_dir, reporte)
    categorias = cargar_categorias(data_dir)
    ids_categorias = {c["id"] for c in categorias}
    if not categorias:
        reporte.agregar("general", "error", "No existe data/categorias.toml o está vacío.")

    ruta_tmp = build_dir / (config.NOMBRE_BASE + ".tmp")
    for r in (ruta_tmp, Path(str(ruta_tmp) + ".wal")):
        r.unlink(missing_ok=True)
    con = duckdb.connect(str(ruta_tmp))
    datasets = []
    try:
        for carpeta in descubrir_carpetas(data_dir):
            ruta_cfg = carpeta / "dataset.toml"
            nombre_carpeta = carpeta.relative_to(data_dir).as_posix()
            if not ruta_cfg.exists():
                reporte.agregar("general", "error", f"{nombre_carpeta}: tiene CSV pero no dataset.toml; se omitió.")
                continue
            try:
                cfg = cargar_toml(ruta_cfg)
            except tomllib.TOMLDecodeError as e:
                reporte.agregar("general", "error", f"{nombre_carpeta}/dataset.toml no es TOML válido: {e}; se omitió.")
                continue
            comun = comunes.get(cfg.get("categoria"), {})
            cfg = unir_comunes(cfg, comun)
            errores = validar_config(cfg, carpeta, ids_categorias)
            if cfg.get("id") in {d["id"] for d in datasets}:
                errores.append(f"El id {cfg['id']!r} ya lo usa otro dataset.")
            if errores:
                for e in errores:
                    reporte.agregar("general", "error", f"{nombre_carpeta}: {e} Se omitió.")
                continue
            if not (carpeta / "_FUENTES.md").exists():
                reporte.agregar(cfg["id"], "aviso", "No hay _FUENTES.md junto a los CSV.")
            if comun:
                reporte.agregar(cfg["id"], "info", f"Se aplicaron las reglas comunes de {cfg['categoria']}/_comun.toml "
                                                   f"({len(comun.get('reglas_interpretacion', []))} reglas de interpretación).")
            try:
                entrada = ingerir_dataset(con, carpeta, cfg, diccionarios, reporte)
            except duckdb.Error as e:
                reporte.agregar(cfg["id"], "error", f"Error al leer los CSV: {e}. Se omitió.")
                continue
            if entrada is None:
                continue
            categoria = next(c for c in categorias if c["id"] == cfg["categoria"])
            entrada.update({
                "categoria": cfg["categoria"],
                "categoria_nombre": categoria.get("nombre", cfg["categoria"]),
                "nombre": cfg["nombre"],
                "descripcion": cfg["descripcion"],
                "institucion": cfg.get("institucion", ""),
                "fuente_url": cfg.get("fuente_url", ""),
                "fuente_original_url": cfg.get("fuente_original_url", ""),
                "cobertura_geografica": cfg.get("cobertura_geografica", ""),
                "prioridad": int(cfg.get("prioridad", 0)),
                "unidad": cfg.get("unidad", {"singular": "registro", "plural": "registros"}),
                "palabras_clave": cfg.get("palabras_clave", []),
                "preguntas_ejemplo": cfg.get("preguntas_ejemplo", []),
                "privacidad": cfg.get("privacidad", {}),
                "valores_especiales": cfg.get("valores_especiales", []),
                "advertencias_condicionales": cfg.get("advertencias_condicionales", []),
                "datos_no_disponibles": cfg.get("datos_no_disponibles", []),
                "reglas_interpretacion": cfg.get("reglas_interpretacion", []),
                "temas_restringidos": cfg.get("temas_restringidos", []),
                "valores_no_disponibles": cfg.get("valores_no_disponibles", []),
                "lenguaje_evitar": cfg.get("lenguaje_evitar", []),
                "fuentes_md": leer_fuentes(carpeta),
            })
            datasets.append(entrada)
        con.execute("CHECKPOINT")
    finally:
        con.close()

    # Reemplazo atómico: un servidor nunca ve una base a medio construir.
    ruta_base = build_dir / config.NOMBRE_BASE
    try:
        os.replace(ruta_tmp, ruta_base)
    except PermissionError:
        ruta_tmp.unlink(missing_ok=True)
        raise RuntimeError("La base de datos está en uso (¿el servidor sigue encendido?). Detén el servidor con "
                           "Ctrl+C y vuelve a arrancarlo: la reingesta se hace sola al iniciar.") from None

    for d in datasets:
        d["reporte"] = reporte.de(d["id"])
    catalogo = {
        "generado": dt.datetime.now().isoformat(timespec="seconds"),
        "huella": calcular_huella(data_dir),
        "version_ingesta": VERSION_INGESTA,
        "datasets": datasets,
        "categorias": [{
            "id": c["id"], "nombre": c.get("nombre", c["id"]), "descripcion": c.get("descripcion", ""),
            "palabras_clave": c.get("palabras_clave", []),
            "datasets": [d["id"] for d in datasets if d["categoria"] == c["id"]],
            "estado": "disponible" if any(d["categoria"] == c["id"] for d in datasets) else "proximamente",
        } for c in categorias],
        "reporte_general": reporte.de("general"),
    }
    (build_dir / config.NOMBRE_CATALOGO).write_text(json.dumps(catalogo, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (build_dir / config.NOMBRE_REPORTE).write_text(generar_reporte_md(catalogo), encoding="utf-8")
    return catalogo


def necesita_reingesta(data_dir: Path = config.DATA_DIR, build_dir: Path = config.BUILD_DIR) -> bool:
    ruta_cat = build_dir / config.NOMBRE_CATALOGO
    if not ruta_cat.exists() or not (build_dir / config.NOMBRE_BASE).exists():
        return True
    try:
        return json.loads(ruta_cat.read_text(encoding="utf-8")).get("huella") != calcular_huella(data_dir)
    except (json.JSONDecodeError, OSError):
        return True


def generar_reporte_md(catalogo: dict) -> str:
    """Reporte legible de la ingesta: qué se cargó y qué no cuadra."""
    lineas = [f"# Reporte de ingesta", "", f"Generado: {catalogo['generado']}", ""]
    iconos = {"error": "❌", "aviso": "⚠️", "info": "ℹ️"}
    if catalogo["reporte_general"]:
        lineas += ["## General", ""] + [f"- {iconos[e['nivel']]} {e['texto']}" for e in catalogo["reporte_general"]] + [""]
    lineas += ["## Categorías", ""]
    for c in catalogo["categorias"]:
        lineas.append(f"- **{c['nombre']}**: {c['estado']}" + (f" ({', '.join(c['datasets'])})" if c["datasets"] else ""))
    for d in catalogo["datasets"]:
        lineas += ["", f"## {d['nombre']} (`{d['id']}`)", "",
                   f"- Carpeta: `{d['carpeta']}`", f"- Filas cargadas: {miles(d['filas'])}"]
        if d.get("periodo"):
            lineas.append(f"- Periodo cubierto (calculado de los datos): {d['periodo']['texto']} "
                          f"({d['periodo']['dias_con_datos']} días con datos)")
        portal = d["fuentes_md"].get("Periodo que cubre (según el portal)")
        if portal:
            lineas.append(f"- Periodo según el portal (_FUENTES.md): {portal}")
        lineas += ["", "| Archivo | Codificación | Filas | Desde | Hasta |", "|---|---|---|---|---|"]
        lineas += [f"| {a['archivo']} | {a['codificacion']} | {miles(a['filas'])} | {a['desde']} | {a['hasta']} |" for a in d["archivos"]]
        if d["archivos_excluidos"]:
            lineas.append(f"\nArchivos excluidos: {', '.join(d['archivos_excluidos'])}")
        if d.get("documentacion"):
            lineas.append(f"\nDocumentación (no se carga como datos): {', '.join(d['documentacion'])}")
        cal = d["calidad"]
        lineas.append(f"\nFilas leídas: {miles(cal['filas_leidas'])} · filas idénticas eliminadas: "
                      f"{miles(cal['filas_identicas_eliminadas'])} · identificadores repetidos con datos distintos: "
                      f"{miles(cal.get('ids_repetidos', 0))}")
        if d.get("meses"):
            lineas += ["", "| Mes | Filas | Promedio por día hábil | Estado |", "|---|---|---|---|"]
            lineas += [f"| {m['nombre']} | {miles(m['filas'])} | {miles(m['promedio_dia_habil'])} | {m['estado']} |" for m in d["meses"]]
        lineas += ["", "| Columna | Tipo | Expuesta | En la base | Distintos | Ocultos | Vacíos |",
                   "|---|---|---|---|---|---|---|"]
        for c in d["columnas"]:
            lineas.append(f"| {c['alias']} ({c['columna_origen']}) | {c['tipo']} | {'sí' if c['expuesta'] else 'no'} "
                          f"| {'sí' if c.get('guardada', True) else 'no'} | {c.get('n_distintos', '')} "
                          f"| {c.get('valores_ocultos', '')} | {c['vacios']['porcentaje']}% |")
        if d["reporte"]:
            lineas += ["", "### Lo que no cuadra", ""] + [f"- {iconos[e['nivel']]} {e['texto']}" for e in d["reporte"]]
    return "\n".join(lineas) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Carga los CSV de data/ en DuckDB y genera el catálogo.")
    parser.add_argument("--si-cambio", action="store_true", help="solo reingiere si cambiaron los datos")
    args = parser.parse_args()
    if args.si_cambio and not necesita_reingesta():
        print("Los datos no cambiaron; no hace falta reingerir.")
        return
    try:
        catalogo = ingerir()
    except RuntimeError as e:
        print(f"✘ {e}")
        sys.exit(1)
    for d in catalogo["datasets"]:
        print(f"✔ {d['nombre']}: {miles(d['filas'])} filas, {d.get('periodo', {}).get('texto', 'sin fechas')}")
    errores = [e for e in catalogo["reporte_general"] if e["nivel"] == "error"]
    for e in errores:
        print(f"✘ {e['texto']}")
    print(f"Reporte: {config.BUILD_DIR / config.NOMBRE_REPORTE}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
