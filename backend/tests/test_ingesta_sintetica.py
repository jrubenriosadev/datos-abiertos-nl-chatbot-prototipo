"""Ingesta genérica con un dataset inventado: demuestra que agregar un dataset no requiere tocar código."""
import json
import os
import time

import duckdb
import pytest

import config
import ingest
from catalogo import Catalogo

DATASET_TOML = """
id = "prueba_demo"
categoria = "demo"
nombre = "Registros de prueba"
descripcion = "Dataset sintético para pruebas."
palabras_clave = ["registro", "prueba"]
siglas = ["VIH"]

[unidad]
singular = "registro"
plural = "registros"

[fecha]
columna = "dia"
formato = "%Y-%m-%d"

[identificador]
columna = "folio"

[[valores_esperados]]
columna = "municipio"
diccionario = "municipios_demo"

[[columnas]]
nombre = "municipio"
alias = "municipio"
tipo = "categoria"
normalizar = "titulo"
diccionario = "municipios_demo"
expuesta = true

[[columnas]]
nombre = "edad"
alias = "edad"
tipo = "numero"
expuesta = true

[[columnas]]
nombre = "clave"
alias = "motivo"
tipo = "codigo"
etiqueta = "descripcion"
normalizar = "oracion"
doble_espacio = "coma"
expuesta = true
"""

CABECERA = "folio,dia,edad,municipio,clave,descripcion\n"


@pytest.fixture()
def data_demo(tmp_path):
    data = tmp_path / "data"
    (data / "diccionarios").mkdir(parents=True)
    (data / "categorias.toml").write_text(
        '[[categorias]]\nid = "demo"\nnombre = "Demo"\n\n[[categorias]]\nid = "futura"\nnombre = "Futura"\n'
        'palabras_clave = ["cohete"]\n', encoding="utf-8")
    (data / "diccionarios" / "municipios_demo.toml").write_text(
        'nombre = "demo"\n[valores]\n"SAN NICOLAS DE LOS GARZA" = "San Nicolás de los Garza"\n"GARCIA" = "García"\n'
        '"LOS ALDAMAS" = "Los Aldamas"\n', encoding="utf-8")
    carpeta = data / "demo" / "Registros"
    carpeta.mkdir(parents=True)
    (carpeta / "dataset.toml").write_text(DATASET_TOML, encoding="utf-8")
    (carpeta / "_FUENTES.md").write_text("- **Enlace:** https://ejemplo.mx\n", encoding="utf-8")
    # Parte 1 en UTF-8 (con acento) y parte 2 en latin-1 (con Ó codificada en un byte).
    (carpeta / "parte1.csv").write_text(
        CABECERA + "1,2026-01-05,30,SAN NICOLAS DE LOS GARZA,E119,DIABETES  TIPO 2\n"
                   "2,2026-01-06,0,GARCIA,B24X,ENFERMEDAD POR VIH  SIN OTRA\n", encoding="utf-8")
    (carpeta / "parte2.csv").write_bytes(
        (CABECERA + "3,2026-02-02,45,GARCIA,A09X,INFECCIÓN INTESTINAL\n"
                    "3,2026-02-03,50,GARCIA,A09X,INFECCIÓN INTESTINAL\n").encode("latin-1"))
    # Parte 3 con una cabecera distinta: debe excluirse y reportarse.
    (carpeta / "parte3.csv").write_text("folio,dia,edad,municipio,clave\n9,2026-02-04,1,GARCIA,A09X\n", encoding="utf-8")
    # Carpeta con CSV y sin configuración: debe reportarse, no adivinarse.
    (data / "demo" / "SinConfig").mkdir()
    (data / "demo" / "SinConfig" / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return data


def test_ingesta_generica(data_demo, tmp_path):
    build = tmp_path / "build"
    cat = ingest.ingerir(data_demo, build)
    ds = cat["datasets"][0]
    assert ds["id"] == "prueba_demo"
    assert ds["filas"] == 4
    assert {a["archivo"]: a["codificacion"] for a in ds["archivos"]} == {"parte1.csv": "utf-8", "parte2.csv": "latin-1"}
    assert ds["archivos_excluidos"] == ["parte3.csv"]
    textos = " ".join(e["texto"] for e in ds["reporte"])
    assert "parte3.csv" in textos and "cabecera" in textos
    assert "identificadores de 'folio' se repiten con datos distintos" in textos  # folio 3 en dos fechas
    assert "Faltan: Los Aldamas" in textos  # valor esperado del diccionario que no aparece en los datos
    generales = " ".join(e["texto"] for e in cat["reporte_general"])
    assert "SinConfig" in generales and "dataset.toml" in generales
    assert {c["id"]: c["estado"] for c in cat["categorias"]} == {"demo": "disponible", "futura": "proximamente"}

    con = duckdb.connect(str(build / config.NOMBRE_BASE), read_only=True)
    filas = con.execute("SELECT fecha::VARCHAR, municipio, motivo, motivo_nombre, archivo_origen FROM ds_prueba_demo ORDER BY fecha").fetchall()
    columnas = [r[0] for r in con.execute("DESCRIBE ds_prueba_demo").fetchall()]
    con.close()
    assert "folio" not in columnas
    assert filas[0] == ("2026-01-05", "San Nicolás de los Garza", "E119", "Diabetes, tipo 2", "parte1.csv")
    assert filas[1][3] == "Enfermedad por VIH, sin otra"
    assert filas[2][3] == "Infección intestinal"  # acento de latin-1 conservado
    assert ds["periodo"]["texto"] == "del 5 de enero al 3 de febrero de 2026"


def test_reingesta_al_agregar_partes(data_demo, tmp_path):
    build = tmp_path / "build"
    ingest.ingerir(data_demo, build)
    assert not ingest.necesita_reingesta(data_demo, build)
    nueva = data_demo / "demo" / "Registros" / "parte4.csv"
    nueva.write_text(CABECERA + "10,2026-03-02,20,GARCIA,A09X,INFECCION\n", encoding="utf-8")
    assert ingest.necesita_reingesta(data_demo, build)
    cat = ingest.ingerir(data_demo, build)
    assert cat["datasets"][0]["filas"] == 5
    assert cat["datasets"][0]["periodo"]["fin"] == "2026-03-02"


def test_config_mal_formada_se_reporta(data_demo, tmp_path):
    ruta = data_demo / "demo" / "Registros" / "dataset.toml"
    # Una clave suelta después de [[columnas]] queda dentro de la columna: debe detectarse.
    ruta.write_text(DATASET_TOML + '\nreglas_interpretacion = ["x"]\n', encoding="utf-8")
    cat = ingest.ingerir(data_demo, tmp_path / "build")
    assert cat["datasets"] == []
    assert "claves desconocidas" in " ".join(e["texto"] for e in cat["reporte_general"])


DOS_PERIODOS_TOML = """
id = "dos_periodos"
categoria = "demo"
nombre = "Dos periodos"
descripcion = "Dos publicaciones con cabeceras distintas."

[unidad]
singular = "registro"
plural = "registros"

[archivos]
excluir = ["diccionario_*.csv"]

[archivos.equivalencias]
"edad_anos" = "edad"
"clave_ grupo" = "clave"

[fecha]
columna = "dia"
formato = "%Y-%m-%d"

[identificador]
columna = "folio"

[completitud]
umbral_dia_habil = 0.5
meses_rezago = 0
rezago_antes_de_hueco = true

[[columnas]]
nombre = "edad"
alias = "edad"
tipo = "numero"
expuesta = true

[[columnas]]
nombre = "unidad"
alias = "unidad"
tipo = "categoria"
normalizar = "titulo"
unificar = "sin_signos"
expuesta = true

[[columnas]]
nombre = "clave"
alias = "motivo"
tipo = "codigo"
etiqueta = "descripcion"
normalizar = "oracion"
doble_espacio = "coma"
expuesta = true
"""


@pytest.fixture()
def data_dos_periodos(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "categorias.toml").write_text('[[categorias]]\nid = "demo"\nnombre = "Demo"\n', encoding="utf-8")
    carpeta = data / "demo" / "DosPeriodos"
    carpeta.mkdir(parents=True)
    (carpeta / "dataset.toml").write_text(DOS_PERIODOS_TOML, encoding="utf-8")
    # Periodo viejo: otros nombres de columna (uno con un espacio dentro), puntos en la unidad, "TIPO DOS".
    (carpeta / "2024_parte1.csv").write_text(
        "dia,folio,edad_anos,unidad,clave_ grupo,descripcion\n"
        "2026-01-05,1,40,C.S.U. CENTRO,E119,DIABETES TIPO DOS\n"
        "2026-01-06,2,50,C.S.U. CENTRO,E119,DIABETES TIPO DOS\n"
        "2026-01-07,3,60,C.S.U. CENTRO,J00X,RESFRIADO\n", encoding="utf-8")
    # Periodo nuevo (latin-1): mismos datos con otra escritura; la numeración de folios se reinicia,
    # y la fila del folio 3 se repite idéntica en otra parte (parte encimada).
    (carpeta / "2025_parte1.csv").write_bytes((
        "dia,folio,edad,unidad,clave,descripcion\n"
        "2026-03-02,1,30,C S U  CENTRO,E119,DIABETES TIPO 2  SIN COMPLICACIÓN\n"
        "2026-03-03,2,35,C S U  CENTRO,E119,DIABETES TIPO 2  SIN COMPLICACIÓN\n").encode("latin-1"))
    (carpeta / "2024_parte2.csv").write_text(
        "dia,folio,edad_anos,unidad,clave_ grupo,descripcion\n"
        "2026-01-07,3,60,C.S.U. CENTRO,J00X,RESFRIADO\n", encoding="utf-8")
    # Diccionario de datos: es documentación, no datos.
    (carpeta / "diccionario_campos.csv").write_text("campo,descripcion\nfolio,identificador\n", encoding="utf-8")
    return data


def test_dos_periodos_con_cabeceras_distintas(data_dos_periodos, tmp_path):
    build = tmp_path / "build"
    cat = ingest.ingerir(data_dos_periodos, build)
    ds = cat["datasets"][0]
    textos = " ".join(e["texto"] for e in ds["reporte"])
    # Las dos cabeceras se unificaron y el diccionario no se cargó.
    assert {a["archivo"] for a in ds["archivos"]} == {"2024_parte1.csv", "2025_parte1.csv", "2024_parte2.csv"}
    assert ds["documentacion"] == ["diccionario_campos.csv"] and not ds["archivos_excluidos"]
    assert "2 cabeceras distintas" in textos
    # La fila idéntica se eliminó; los folios repetidos con otros datos NO son duplicados.
    assert ds["calidad"]["filas_leidas"] == 6 and ds["calidad"]["filas_identicas_eliminadas"] == 1
    assert ds["filas"] == 5
    assert ds["calidad"]["ids_repetidos"] == 2 and ds["calidad"]["ids_repetidos_misma_fecha"] == 0
    assert "numeración se reinicia" in textos

    con = duckdb.connect(str(build / config.NOMBRE_BASE), read_only=True)
    unidades = {r[0] for r in con.execute("SELECT DISTINCT unidad FROM ds_dos_periodos").fetchall()}
    motivos = dict(con.execute("SELECT DISTINCT motivo, motivo_nombre FROM ds_dos_periodos").fetchall())
    variantes = con.execute("SELECT clave, texto FROM ds_dos_periodos_variantes").fetchall()
    edades = sorted(r[0] for r in con.execute("SELECT edad FROM ds_dos_periodos").fetchall())
    con.close()
    assert unidades == {"C S U Centro"}                                    # una sola escritura
    assert motivos["E119"] == "Diabetes tipo 2, sin complicación"          # la descripción más reciente
    assert variantes == [("E119", "Diabetes tipo dos")]                    # la otra se guarda para buscar
    assert edades == [30, 35, 40, 50, 60]                                  # 'edad_anos' se leyó como 'edad'
    assert "palabras distintas" in textos


def test_mes_sin_datos_y_rezago_antes_del_hueco(data_dos_periodos, tmp_path):
    cat = ingest.ingerir(data_dos_periodos, tmp_path / "build")
    ds = cat["datasets"][0]
    estados = {m["mes"]: m["estado"] for m in ds["meses"]}
    assert estados["2026-02"] == "sin_datos"
    assert estados["2026-01"] == "posible_rezago"   # el mes justo antes del hueco
    assert ds["periodo"]["texto"].endswith("(sin datos de febrero de 2026)")
    assert ds["periodo"]["meses_sin_datos"] == ["2026-02"]


COMUN_TOML = """
reglas_interpretacion = ["Regla común."]
datos_no_disponibles = ["Dato común no disponible."]
siglas = ["ONU"]

[privacidad]
minimo_por_grupo = 7
max_filtros_estrechos = 1

[[advertencias_condicionales]]
cuando = { siempre = true }
texto = "Advertencia común."
"""


def test_reglas_comunes_de_la_categoria(data_demo, tmp_path):
    """data/<categoría>/_comun.toml se une a cada dataset: listas comunes primero; en tablas gana el dataset."""
    (data_demo / "demo" / "_comun.toml").write_text(COMUN_TOML, encoding="utf-8")
    ruta = data_demo / "demo" / "Registros" / "dataset.toml"
    ruta.write_text('reglas_interpretacion = ["Regla propia."]\n' + DATASET_TOML +
                    "\n[privacidad]\nminimo_por_grupo = 10\n", encoding="utf-8")
    cat = ingest.ingerir(data_demo, tmp_path / "build")
    ds = cat["datasets"][0]
    assert ds["reglas_interpretacion"] == ["Regla común.", "Regla propia."]
    assert ds["datos_no_disponibles"] == ["Dato común no disponible."]
    assert ds["privacidad"] == {"minimo_por_grupo": 10, "max_filtros_estrechos": 1}
    assert ds["advertencias_condicionales"][0]["texto"] == "Advertencia común."
    assert any("_comun.toml" in e["texto"] for e in ds["reporte"])


def test_comun_con_claves_no_permitidas_se_reporta(data_demo, tmp_path):
    (data_demo / "demo" / "_comun.toml").write_text('id = "otro"\n' + COMUN_TOML, encoding="utf-8")
    cat = ingest.ingerir(data_demo, tmp_path / "build")
    assert cat["datasets"][0]["id"] == "prueba_demo"        # 'id' no se puede imponer desde lo común
    assert "claves no permitidas ['id']" in " ".join(e["texto"] for e in cat["reporte_general"])


@pytest.mark.parametrize("combinacion, error", [
    ('columnas = [["municipio"], ["no_existe"]]\nminimo_por_grupo = 20', "no existen"),
    ('columnas = [["municipio"]]\nminimo_por_grupo = 20', "al menos dos listas"),
    ('columnas = [["municipio"], ["edad"]]', "minimo_por_grupo"),
])
def test_combinaciones_de_privacidad_mal_configuradas(data_demo, tmp_path, combinacion, error):
    ruta = data_demo / "demo" / "Registros" / "dataset.toml"
    ruta.write_text(DATASET_TOML + f"\n[privacidad]\n[[privacidad.combinaciones]]\n{combinacion}\n", encoding="utf-8")
    cat = ingest.ingerir(data_demo, tmp_path / "build")
    assert cat["datasets"] == []
    assert error in " ".join(e["texto"] for e in cat["reporte_general"])


def test_combinacion_de_privacidad_sube_el_minimo(data_demo, tmp_path):
    from consultas import MotorConsultas
    ruta = data_demo / "demo" / "Registros" / "dataset.toml"
    ruta.write_text(DATASET_TOML + '\n[privacidad]\nminimo_por_grupo = 1\n[[privacidad.combinaciones]]\n'
                                   'columnas = [["municipio"], ["edad"]]\nminimo_por_grupo = 3\n', encoding="utf-8")
    build = tmp_path / "build"
    ingest.ingerir(data_demo, build)
    con = duckdb.connect(str(build / config.NOMBRE_BASE), read_only=True)
    motor = MotorConsultas(con, Catalogo.cargar(build))
    # García tiene 3 registros (folio 3 repetido con otra fecha): con k=1 se ven; al cruzar con edad, k=3.
    sin_edad = motor.consultar("prueba_demo", [{"columna": "municipio", "operador": "igual", "valor": "García"}])
    con_edad = motor.consultar("prueba_demo", [{"columna": "municipio", "operador": "igual", "valor": "García"},
                                               {"columna": "edad", "operador": "mayor_igual", "valor": 1}])
    con.close()
    assert sin_edad["filas"][0]["conteo"] == 3
    assert con_edad["filas"][0]["conteo"] is None and con_edad["filas"][0]["conteo_texto"] == "menos de 3"
    assert any("al cruzar municipio y edad" in a for a in con_edad["advertencias"])


def test_catalogo_de_dataset_sintetico(data_demo, tmp_path):
    build = tmp_path / "build"
    ingest.ingerir(data_demo, build)
    cat = Catalogo.cargar(build)
    desc = cat.describir("prueba_demo")
    assert [c["alias"] for c in desc["columnas"]][:3] == ["municipio", "edad", "motivo"]
    assert "edad_promedio" in desc["agregaciones"]
    assert cat.buscar("cohete espacial")["proximamente"][0]["categoria"] == "Futura"
