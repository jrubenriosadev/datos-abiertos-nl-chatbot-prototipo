"""Ingesta real del dataset de Salud comparada contra el oráculo (módulo csv).

Ninguna cifra está escrita a mano: lo esperado se recalcula de los CSV, así las pruebas siguen
siendo válidas cuando se agregan partes nuevas.
"""
import json
import tomllib

import config
import oraculo
from ingest import periodo_legible
from normalizar import clave_comparacion

TABLA = "ds_salud_consulta_general"
CFG = oraculo.config_salud()
meses_esperados = oraculo.meses_esperados


def _uno(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]



def test_total_y_meses_contra_oraculo(con_real):
    o = oraculo.salud()
    assert _uno(con_real, f"SELECT count(*) FROM {TABLA}") == o["filas"]
    assert dict(con_real.execute(f"SELECT mes, count(*) FROM {TABLA} GROUP BY mes").fetchall()) == dict(o["mes"])


def test_conteos_diarios_contra_oraculo(con_real):
    assert dict(con_real.execute(f"SELECT fecha, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(oraculo.salud()["dia"])


def test_municipios_contra_oraculo(con_real):
    o = oraculo.salud()
    db = {clave_comparacion(m): n for m, n in con_real.execute(f"SELECT municipio, count(*) FROM {TABLA} GROUP BY 1").fetchall()}
    assert db == dict(o["municipio"])


def test_enfermedades_grupos_y_sexo_contra_oraculo(con_real):
    o = oraculo.salud()
    assert dict(con_real.execute(f"SELECT enfermedad, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["enfermedad"])
    assert dict(con_real.execute(f"SELECT grupo, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["grupo"])
    sexo = {clave_comparacion(s): n for s, n in con_real.execute(f"SELECT sexo, count(*) FROM {TABLA} GROUP BY 1").fetchall()}
    assert sexo == {clave_comparacion(s): n for s, n in o["sexo"].items()}


def test_unidades_unificadas_y_distinguidas_por_municipio(con_real):
    """Las escrituras 'C.S.U. X' y 'C S U  X' son la misma unidad; el mismo nombre en dos municipios, no."""
    o = oraculo.salud()
    assert _uno(con_real, f"SELECT count(DISTINCT unidad_medica) FROM {TABLA}") == len(o["unidades"])
    homonimas = {u for u, ms in o["unidad_municipios"].items() if len(ms) > 1}
    db = con_real.execute(f"SELECT regexp_replace(unidad_medica, ' \\([^)]*\\)$', ''), count(DISTINCT municipio) "
                          f"FROM {TABLA} GROUP BY 1 HAVING count(DISTINCT municipio) > 1").fetchall()
    assert {clave_comparacion(u) for u, _ in db} == homonimas
    # Ninguna unidad conserva puntos de abreviatura ni dobles espacios.
    assert _uno(con_real, f"SELECT count(*) FROM {TABLA} WHERE unidad_medica LIKE '%.%' OR unidad_medica LIKE '%  %'") == 0


def test_archivo_origen_por_parte(con_real):
    o = oraculo.salud()
    assert dict(con_real.execute(f"SELECT archivo_origen, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["archivo"])


def test_documentacion_no_se_carga_como_datos(salud, con_real):
    documentos = [r.name for r in oraculo.CARPETA_SALUD.glob("diccionario*.csv")]
    assert salud["documentacion"] == documentos
    assert not salud["archivos_excluidos"]
    assert _uno(con_real, f"SELECT count(*) FROM {TABLA} WHERE archivo_origen LIKE 'diccionario%'") == 0


def test_todas_las_cabeceras_se_unificaron(salud):
    """Cada esquema distinto de los CSV quedó cargado (con las equivalencias de dataset.toml)."""
    o = oraculo.salud()
    cargados = {a["archivo"] for a in salud["archivos"]}
    assert cargados == {r.name for r in oraculo.archivos_de_datos()}
    if len(o["esquemas"]) > 1:
        assert any("cabeceras distintas" in e["texto"] for e in salud["reporte"])


def test_id_consulta_no_entra_a_la_base(con_real, build_real):
    columnas = [r[0] for r in con_real.execute(f"DESCRIBE {TABLA}").fetchall()]
    assert "id_consulta" not in columnas
    assert "CE_20" not in (build_real / config.NOMBRE_CATALOGO).read_text(encoding="utf-8")


def test_duplicados_e_ids_repetidos(salud):
    o, cal = oraculo.salud(), salud["calidad"]
    assert cal["filas_leidas"] == o["total"]
    assert cal["filas_identicas_eliminadas"] == o["filas_identicas"]
    assert cal["ids_repetidos"] == o["ids_repetidos"]
    if o["ids_repetidos"]:
        assert cal["ids_repetidos_misma_fecha"] == 0
        assert any("numeración se reinicia" in e["texto"] for e in salud["reporte"])


def test_tipos_y_fechas_iso(con_real):
    tipos = dict((r[0], r[1]) for r in con_real.execute(f"DESCRIBE {TABLA}").fetchall())
    assert tipos["fecha"] == "DATE" and tipos["edad"] == "BIGINT"
    assert _uno(con_real, f"SELECT count(*) FROM {TABLA} WHERE fecha IS NULL") == 0


def test_rangos_de_edad_cubren_todo(con_real):
    o = oraculo.salud()
    assert _uno(con_real, f"SELECT count(*) FROM {TABLA} WHERE rango_edad IS NULL") == 0
    for etiqueta, minimo, maximo in CFG["edad"]["rangos"]:
        esperado = sum(n for e, n in o["edad"].items() if minimo <= e <= maximo)
        assert _uno(con_real, f"SELECT count(*) FROM {TABLA} WHERE rango_edad = ?", [etiqueta]) == esperado, etiqueta


def test_periodo_calculado_de_los_datos(salud):
    o = oraculo.salud()
    assert salud["periodo"]["inicio"] == o["inicio"].isoformat()
    assert salud["periodo"]["fin"] == o["fin"].isoformat()
    huecos = [m["nombre"] for m in meses_esperados() if m["estado"] == "sin_datos"]
    texto = periodo_legible(o["inicio"], o["fin"]) + (f" (sin datos de {', '.join(huecos)})" if huecos else "")
    assert salud["periodo"]["texto"] == texto


def test_meses_marcados(salud):
    esperado = {m["mes"]: (m["filas"], m["estado"]) for m in meses_esperados()}
    assert {m["mes"]: (m["filas"], m["estado"]) for m in salud["meses"]} == esperado
    # Los meses sin datos se reportan como hueco.
    for m in salud["meses"]:
        if m["estado"] == "sin_datos":
            assert any(f"No hay ningún registro de {m['nombre']}" in e["texto"] for e in salud["reporte"])


def test_calidad_reportada(salud):
    o, cal = oraculo.salud(), salud["calidad"]
    assert cal["fechas_invalidas"] == 0
    assert cal["vacios"]["peso"]["filas"] == o["vacios"]["peso"]
    assert cal["vacios"]["altura"]["filas"] == o["vacios"]["altura"]
    controles = {c["descripcion"]: c["filas"] for c in cal["controles"]}
    assert controles["Adultos (18 años o más) con peso menor a 30 kg"] == o["controles"]["peso_menor_30_adultos"]
    assert controles["Adultos (18 años o más) con altura menor a 100 cm"] == o["controles"]["altura_menor_100_adultos"]
    assert controles["Pesos mayores a 250 kg"] == o["controles"]["peso_mayor_250"]
    assert controles["Edades mayores a 100 años"] == o["controles"]["edad_mayor_100"]


def test_municipios_faltantes_contra_diccionario(salud):
    dic = tomllib.loads((config.DATA_DIR / "diccionarios" / "municipios_nl.toml").read_text(encoding="utf-8"))["valores"]
    faltan = [nombre for clave, nombre in dic.items() if clave_comparacion(clave) not in oraculo.salud()["municipio"]]
    assert salud["calidad"]["faltantes"][0]["faltan"] == faltan


def test_jerarquia_de_claves(salud):
    """Prefijos minoritarios (menos del 2% de su grupo), calculados también con el oráculo."""
    o = oraculo.salud()
    por_grupo = {}
    for (g, letra), n in o["grupo_letra"].items():
        por_grupo.setdefault(g, {})[letra] = n
    for j in salud["calidad"]["jerarquia"]:
        prefijos = por_grupo[j["padre"]]
        total = sum(prefijos.values())
        esperado = {k: v for k, v in prefijos.items() if len(prefijos) > 1 and v / total < 0.02}
        assert j["minoritarios"] == esperado, j["padre"]


def test_textos_normalizados(con_real):
    nombres = dict(con_real.execute(f"SELECT DISTINCT enfermedad, enfermedad_nombre FROM {TABLA}").fetchall())
    # La coma perdida (doble espacio) se restaura, y no quedan dobles espacios.
    assert all("  " not in n for n in nombres.values())
    assert nombres["E119"].startswith("Diabetes mellitus tipo 2, sin mención") or nombres["E119"].startswith("Diabetes mellitus tipo 2, sin mencion")
    grupos = dict(con_real.execute(f"SELECT DISTINCT grupo, grupo_nombre FROM {TABLA}").fetchall())
    assert grupos["IV"] == "Enfermedades endocrinas, nutricionales y metabólicas"
    assert grupos["II"] == "Tumores (neoplasias)"
    municipios = {r[0] for r in con_real.execute(f"SELECT DISTINCT municipio FROM {TABLA}").fetchall()}
    assert {"San Nicolás de los Garza", "General Treviño", "Anáhuac"} <= municipios


def test_variantes_de_descripcion_para_buscar(con_real):
    """Las descripciones alternativas de una clave se guardan aparte para la búsqueda."""
    variantes = con_real.execute(f"SELECT clave, texto FROM {TABLA}_variantes WHERE columna = 'enfermedad'").fetchall()
    principales = dict(con_real.execute(f"SELECT DISTINCT enfermedad, enfermedad_nombre FROM {TABLA}").fetchall())
    assert all(principales[k] != t for k, t in variantes)


def test_motivo_con_vacios_calculados(salud):
    peso = next(c for c in salud["columnas"] if c["alias"] == "peso")
    assert f"Vacíos: {peso['vacios']['porcentaje']:.1f}% de los registros." in peso["motivo"]


def test_catalogo_es_json_valido_y_completo(build_real, salud):
    datos = json.loads((build_real / config.NOMBRE_CATALOGO).read_text(encoding="utf-8"))
    assert {c["id"]: c["estado"] for c in datos["categorias"]} == \
        {"educacion": "proximamente", "salud": "disponible", "seguridad": "proximamente"}
    assert salud["fuente_url"].startswith("https://catalogodatos.nl.gob.mx")
    assert len(salud["reglas_interpretacion"]) == len(CFG["reglas_interpretacion"])
    assert not [e for e in datos["reporte_general"] if e["nivel"] == "error"]
