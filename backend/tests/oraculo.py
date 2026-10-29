"""Oráculo independiente: recalcula cifras leyendo los CSV con el módulo csv de Python.

No usa DuckDB, ni el código de normalización del backend, ni dataset.toml: tiene su propio
mapeo de cabeceras y su propia forma de unificar nombres, para que las pruebas comparen dos
caminos distintos hacia el mismo número. Así las pruebas no llevan cifras escritas a mano:
si se agregan partes, lo esperado se recalcula solo.
"""
import csv
import datetime as dt
import functools
import re
import unicodedata
from collections import Counter
from pathlib import Path

DATA_SALUD = Path(__file__).resolve().parents[2] / "data" / "salud"
CARPETA_SALUD = DATA_SALUD / "ConsultaGeneralYEspecialidades"
CARPETA_SALUD_BUCAL = DATA_SALUD / "SaludBucal"
CARPETA_SALUD_MENTAL = DATA_SALUD / "SaludMental"

# Nombres de columna en los distintos periodos → nombre que usa el oráculo.
COLUMNAS = {
    "fecha": "fecha", "fecha_consulta": "fecha", "id_consulta": "id", "edad": "edad", "edad_anos": "edad",
    "sexo": "sexo", "peso": "peso", "altura": "altura", "municipio_unidad_medica": "municipio",
    "institucion_unidad_medica": "unidad", "clave_grupo_enfermedad": "grupo", "clave_grupo_ enfermedad": "grupo",
    "clave_enfermedad": "clave",
}


def sin_signos(texto: str) -> str:
    """Mayúsculas, sin acentos y sin signos: la forma con la que el oráculo compara nombres."""
    t = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", t).split())


def archivos_de_datos(carpeta: Path = CARPETA_SALUD) -> list[Path]:
    """CSV de datos de la carpeta (el diccionario oficial no es un archivo de datos)."""
    return [r for r in sorted(carpeta.glob("*.csv")) if not r.name.startswith("diccionario")]


def _codificacion(ruta: Path) -> str:
    try:
        ruta.read_bytes().decode("utf-8")
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "latin-1"


def _num(texto: str):
    try:
        return float(texto)
    except ValueError:
        return None


@functools.lru_cache(maxsize=1)
def salud() -> dict:
    """Conteos del dataset de Salud. Municipios y unidades en su forma sin signos (MONTERREY)."""
    r = {"total": 0, "mes": Counter(), "dia": Counter(), "municipio": Counter(), "enfermedad": Counter(),
         "grupo": Counter(), "sexo": Counter(), "archivo": Counter(), "unidades": set(), "vacios": Counter(),
         "mun_enf": Counter(), "edad": Counter(), "grupo_de": {}, "edad_suma": Counter(), "mun_sexo": Counter(),
         "mun_grupo": Counter(), "enf_60_mas": Counter(), "mun_grupo_mes": Counter(), "grupo_letra": Counter(),
         "unidad_municipios": {}, "controles": Counter(), "ids_repetidos": 0, "filas_identicas": 0,
         "esquemas": Counter()}
    ids, filas_vistas = set(), set()
    for ruta in archivos_de_datos():
        with open(ruta, encoding=_codificacion(ruta), newline="") as f:
            lector = csv.reader(f)
            cabecera = [c.strip() for c in next(lector)]
            r["esquemas"][tuple(cabecera)] += 1
            pos = {COLUMNAS[c]: i for i, c in enumerate(cabecera) if c in COLUMNAS}
            for fila in lector:
                x = {k: fila[i] for k, i in pos.items()}
                r["total"] += 1
                d, m, a = x["fecha"].split("/")
                fecha = dt.date(int(a), int(m), int(d))
                mes = fecha.strftime("%Y-%m")
                mun = sin_signos(x["municipio"])
                unidad = sin_signos(x["unidad"])
                edad = int(float(x["edad"]))
                clave, grupo = x["clave"].strip().upper(), x["grupo"].strip()
                r["dia"][fecha] += 1
                r["mes"][mes] += 1
                r["municipio"][mun] += 1
                r["enfermedad"][clave] += 1
                r["grupo"][grupo] += 1
                r["sexo"][x["sexo"]] += 1
                r["archivo"][ruta.name] += 1
                r["unidades"].add((unidad, mun))
                r["unidad_municipios"].setdefault(unidad, set()).add(mun)
                r["mun_enf"][(mun, clave)] += 1
                r["edad"][edad] += 1
                r["grupo_de"][clave] = grupo
                r["edad_suma"][clave] += edad
                r["mun_sexo"][(mun, x["sexo"])] += 1
                r["mun_grupo"][(mun, grupo)] += 1
                r["mun_grupo_mes"][(mun, grupo, mes)] += 1
                r["grupo_letra"][(grupo, clave[:1])] += 1
                if edad >= 60:
                    r["enf_60_mas"][clave] += 1
                peso, altura = _num(x["peso"]), _num(x["altura"])
                for col in ("peso", "altura"):
                    if not x[col].strip():
                        r["vacios"][col] += 1
                if edad >= 18 and peso is not None and peso < 30:
                    r["controles"]["peso_menor_30_adultos"] += 1
                if edad >= 18 and altura is not None and altura < 100:
                    r["controles"]["altura_menor_100_adultos"] += 1
                if peso is not None and peso > 250:
                    r["controles"]["peso_mayor_250"] += 1
                if edad > 100:
                    r["controles"]["edad_mayor_100"] += 1
                # Hash de la fila completa (todas las columnas del CSV) y del identificador.
                h = hash(tuple(fila))
                if h in filas_vistas:
                    r["filas_identicas"] += 1
                filas_vistas.add(h)
                if x["id"] in ids:
                    r["ids_repetidos"] += 1
                ids.add(x["id"])
    r["inicio"], r["fin"] = min(r["dia"]), max(r["dia"])
    r["filas"] = r["total"] - r["filas_identicas"]
    return r


def _toml(ruta: Path) -> dict:
    import tomllib
    return tomllib.loads(ruta.read_text(encoding="utf-8"))


def config_comun() -> dict:
    """Reglas comunes de Salud (data/salud/_comun.toml), tal como están escritas."""
    return _toml(DATA_SALUD / "_comun.toml")


def _con_comun(propia: dict) -> dict:
    """Configuración efectiva: listas comunes + propias; en las tablas gana la del dataset."""
    unida = dict(propia)
    for clave, valor in config_comun().items():
        if isinstance(valor, list):
            unida[clave] = valor + propia.get(clave, [])
        elif isinstance(valor, dict):
            unida[clave] = {**valor, **propia.get(clave, {})}
    return unida


def config_salud_propia() -> dict:
    return _toml(CARPETA_SALUD / "dataset.toml")


def config_salud() -> dict:
    return _con_comun(config_salud_propia())


def config_salud_bucal_propia() -> dict:
    return _toml(CARPETA_SALUD_BUCAL / "dataset.toml")


def config_salud_bucal() -> dict:
    return _con_comun(config_salud_bucal_propia())


def meses_esperados(o: dict | None = None, cfg: dict | None = None) -> list[dict]:
    """La regla de completitud (probada aparte con datos inventados) aplicada a los conteos diarios del oráculo."""
    from ingest import evaluar_completitud
    o, cfg = o or salud(), cfg or config_salud()
    comp = cfg["completitud"]
    return evaluar_completitud(dict(o["dia"]), o["inicio"], o["fin"], comp["umbral_dia_habil"], comp["meses_rezago"],
                               cfg["unidad"]["plural"], comp.get("rezago_antes_de_hueco", False))


def rango_de(edad: int | None, rangos: list) -> str | None:
    """Etiqueta del rango de edad (None si la edad viene vacía)."""
    if edad is None:
        return None
    return next((etiqueta for etiqueta, minimo, maximo in rangos if minimo <= edad <= maximo), None)


@functools.lru_cache(maxsize=1)
def salud_bucal() -> dict:
    """Conteos del dataset de Salud Bucal, sin las filas idénticas repetidas (se queda la primera, en el
    orden de los archivos). Municipios en su forma sin signos (MONTERREY)."""
    rangos = config_comun()["edad"]["rangos"]
    r = {"total": 0, "filas_identicas": 0, "ids_repetidos": 0, "ids_repetidos_misma_fecha": 0, "mes": Counter(),
         "dia": Counter(), "municipio": Counter(), "enfermedad": Counter(), "grupo": Counter(), "grupo_de": {},
         "sexo": Counter(), "archivo": Counter(), "edad": Counter(), "edad_vacia": 0, "rango": Counter(),
         "mun_rango": Counter(), "mun_enf": Counter(), "mun_edad": Counter(), "edad_suma": Counter(),
         "edad_n": Counter(), "rango_enf": Counter(), "grupo_letra": Counter(), "controles": Counter(),
         "no_numericos": Counter(), "esquemas": Counter(), "codificacion": {}}
    filas_vistas, ids, ids_fecha = set(), set(), set()
    for ruta in archivos_de_datos(CARPETA_SALUD_BUCAL):
        cod = _codificacion(ruta)
        r["codificacion"][ruta.name] = cod
        with open(ruta, encoding=cod, newline="") as f:
            lector = csv.reader(f)
            cabecera = [c.strip() for c in next(lector)]
            r["esquemas"][tuple(cabecera)] += 1
            pos = {COLUMNAS[c]: i for i, c in enumerate(cabecera) if c in COLUMNAS}
            for fila in lector:
                r["total"] += 1
                h = hash(tuple(fila))
                if h in filas_vistas:
                    r["filas_identicas"] += 1
                    continue
                filas_vistas.add(h)
                x = {k: fila[i] for k, i in pos.items()}
                d, m, a = x["fecha"].split("/")
                fecha = dt.date(int(a), int(m), int(d))
                mes = fecha.strftime("%Y-%m")
                mun = sin_signos(x["municipio"])
                edad = int(float(x["edad"])) if x["edad"].strip() else None
                rango = rango_de(edad, rangos)
                clave, grupo = x["clave"].strip().upper(), x["grupo"].strip()
                r["dia"][fecha] += 1
                r["mes"][mes] += 1
                r["municipio"][mun] += 1
                r["enfermedad"][clave] += 1
                r["grupo"][grupo] += 1
                r["grupo_de"][clave] = grupo
                r["grupo_letra"][(grupo, clave[:1])] += 1
                r["sexo"][x["sexo"]] += 1
                r["archivo"][ruta.name] += 1
                r["mun_enf"][(mun, clave)] += 1
                r["mun_rango"][(mun, rango)] += 1
                r["rango"][rango] += 1
                r["rango_enf"][(rango, clave)] += 1
                if edad is None:
                    r["edad_vacia"] += 1
                else:
                    r["edad"][edad] += 1
                    r["mun_edad"][(mun, edad)] += 1
                    r["edad_suma"][clave] += edad
                    r["edad_n"][clave] += 1
                peso, altura = _num(x["peso"]), _num(x["altura"])
                for col, valor in (("peso", peso), ("altura", altura)):
                    if x[col].strip() and valor is None:
                        r["no_numericos"][col] += 1
                if peso == 999:
                    r["controles"]["peso_999"] += 1
                if altura == 999:
                    r["controles"]["altura_999"] += 1
                if edad is not None and edad >= 18 and peso is not None and peso < 30:
                    r["controles"]["peso_menor_30_adultos"] += 1
                if edad is not None and edad >= 18 and altura is not None and altura < 100:
                    r["controles"]["altura_menor_100_adultos"] += 1
                if edad is not None and edad > 100:
                    r["controles"]["edad_mayor_100"] += 1
                if x["id"] in ids:
                    r["ids_repetidos"] += 1
                if (x["id"], fecha) in ids_fecha:
                    r["ids_repetidos_misma_fecha"] += 1
                ids.add(x["id"])
                ids_fecha.add((x["id"], fecha))
    r["inicio"], r["fin"] = min(r["dia"]), max(r["dia"])
    r["filas"] = r["total"] - r["filas_identicas"]
    return r


def meses_esperados_bucal() -> list[dict]:
    return meses_esperados(salud_bucal(), config_salud_bucal())


def config_salud_mental_propia() -> dict:
    return _toml(CARPETA_SALUD_MENTAL / "dataset.toml")


def config_salud_mental() -> dict:
    return _con_comun(config_salud_mental_propia())


@functools.lru_cache(maxsize=1)
def salud_mental() -> dict:
    """Conteos del dataset de Salud Mental (no tiene filas idénticas, pero se cuentan igual).
    Municipios en su forma sin signos (MONTERREY)."""
    rangos = config_comun()["edad"]["rangos"]
    r = {"total": 0, "filas_identicas": 0, "ids_repetidos": 0, "mes": Counter(), "dia": Counter(),
         "municipio": Counter(), "enfermedad": Counter(), "grupo": Counter(), "grupo_de": {}, "sexo": Counter(),
         "archivo": Counter(), "edad": Counter(), "rango": Counter(), "mun_rango": Counter(), "mun_enf": Counter(),
         "rango_enf": Counter(), "sexo_enf": Counter(), "mun_mes": Counter(), "enf_mes": Counter(),
         "grupo_letra": Counter(), "controles": Counter(), "no_numericos": Counter(), "esquemas": Counter(),
         "menores": 0}
    filas_vistas, ids = set(), set()
    for ruta in archivos_de_datos(CARPETA_SALUD_MENTAL):
        with open(ruta, encoding=_codificacion(ruta), newline="") as f:
            lector = csv.reader(f)
            cabecera = [c.strip() for c in next(lector)]
            r["esquemas"][tuple(cabecera)] += 1
            pos = {COLUMNAS[c]: i for i, c in enumerate(cabecera) if c in COLUMNAS}
            for fila in lector:
                r["total"] += 1
                h = hash(tuple(fila))
                if h in filas_vistas:
                    r["filas_identicas"] += 1
                    continue
                filas_vistas.add(h)
                x = {k: fila[i] for k, i in pos.items()}
                d, m, a = x["fecha"].split("/")
                fecha = dt.date(int(a), int(m), int(d))
                mes = fecha.strftime("%Y-%m")
                mun = sin_signos(x["municipio"])
                edad = int(float(x["edad"]))
                rango = rango_de(edad, rangos)
                clave, grupo = x["clave"].strip().upper(), x["grupo"].strip()
                sexo = x["sexo"]
                r["dia"][fecha] += 1
                r["mes"][mes] += 1
                r["municipio"][mun] += 1
                r["enfermedad"][clave] += 1
                r["grupo"][grupo] += 1
                r["grupo_de"][clave] = grupo
                r["grupo_letra"][(grupo, clave[:1])] += 1
                r["sexo"][sexo] += 1
                r["archivo"][ruta.name] += 1
                r["edad"][edad] += 1
                r["rango"][rango] += 1
                r["mun_rango"][(mun, rango)] += 1
                r["mun_enf"][(mun, clave)] += 1
                r["rango_enf"][(rango, clave)] += 1
                r["sexo_enf"][(sexo, clave)] += 1
                r["mun_mes"][(mun, mes)] += 1
                r["enf_mes"][(clave, mes)] += 1
                if edad < 18:
                    r["menores"] += 1
                peso, altura = _num(x["peso"]), _num(x["altura"])
                for col, valor in (("peso", peso), ("altura", altura)):
                    if x[col].strip() and valor is None:
                        r["no_numericos"][col] += 1
                if edad >= 18 and peso is not None and peso < 30:
                    r["controles"]["peso_menor_30_adultos"] += 1
                if edad >= 18 and altura is not None and altura < 100:
                    r["controles"]["altura_menor_100_adultos"] += 1
                if peso is not None and peso > 250:
                    r["controles"]["peso_mayor_250"] += 1
                if edad > 100:
                    r["controles"]["edad_mayor_100"] += 1
                if x["id"] in ids:
                    r["ids_repetidos"] += 1
                ids.add(x["id"])
    r["inicio"], r["fin"] = min(r["dia"]), max(r["dia"])
    r["filas"] = r["total"] - r["filas_identicas"]
    return r


def meses_esperados_mental() -> list[dict]:
    """Completitud general y por municipio (reglas probadas aparte con datos inventados) sobre los conteos del oráculo."""
    from ingest import evaluar_cobertura
    o, cfg = salud_mental(), config_salud_mental()
    meses = meses_esperados(o, cfg)
    por = cfg["completitud"]["por_columna"]
    serie = {}
    for (mun, mes), n in o["mun_mes"].items():
        serie.setdefault(mun, {})[mes] = n
    return evaluar_cobertura(meses, serie, por["umbral"], por["peso_minimo"], cfg["unidad"]["plural"])


def cifra(n: int) -> str:
    """71610 → '71,610' (como escribe el asistente)."""
    return f"{n:,}"
