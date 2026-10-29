"""Dataset Salud Bucal: ingesta, reglas comunes de Salud, consultas y privacidad, contra el oráculo (módulo csv).

Ninguna cifra está escrita a mano: lo esperado se recalcula de los CSV con oraculo.salud_bucal().
"""
import json
import tomllib

import pytest

import config
import oraculo
from consultas import OTROS
from ingest import miles, periodo_legible
from normalizar import clave_comparacion

D = "salud_bucal"
TABLA = "ds_salud_bucal"
CFG = oraculo.config_salud_bucal()


def _uno(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def f(columna, operador, valor):
    return {"columna": columna, "operador": operador, "valor": valor}


def conteo(r):
    return r["filas"][0]["conteo"]


# ---------- Ingesta ----------

def test_total_meses_y_dias_contra_oraculo(con_real):
    o = oraculo.salud_bucal()
    assert _uno(con_real, f"SELECT count(*) FROM {TABLA}") == o["filas"]
    assert dict(con_real.execute(f"SELECT mes, count(*) FROM {TABLA} GROUP BY mes").fetchall()) == dict(o["mes"])
    assert dict(con_real.execute(f"SELECT fecha, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["dia"])


def test_municipios_enfermedades_grupos_y_sexo_contra_oraculo(con_real):
    o = oraculo.salud_bucal()
    municipios = {clave_comparacion(m): n for m, n in con_real.execute(f"SELECT municipio, count(*) FROM {TABLA} GROUP BY 1").fetchall()}
    assert municipios == dict(o["municipio"])
    assert dict(con_real.execute(f"SELECT enfermedad, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["enfermedad"])
    assert dict(con_real.execute(f"SELECT grupo, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["grupo"])
    sexo = {clave_comparacion(s): n for s, n in con_real.execute(f"SELECT sexo, count(*) FROM {TABLA} GROUP BY 1").fetchall()}
    assert sexo == {clave_comparacion(s): n for s, n in o["sexo"].items()}


def test_archivos_codificaciones_y_cabeceras(bucal, con_real):
    o = oraculo.salud_bucal()
    assert dict(con_real.execute(f"SELECT archivo_origen, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["archivo"])
    assert {a["archivo"] for a in bucal["archivos"]} == set(o["archivo"])
    assert bucal["documentacion"] == [r.name for r in oraculo.CARPETA_SALUD_BUCAL.glob("diccionario*.csv")]
    assert not bucal["archivos_excluidos"]
    # Las dos cabeceras (2024-2025 y 2025-2026) se unificaron con las equivalencias.
    assert len(o["esquemas"]) == 2 and any("cabeceras distintas" in e["texto"] for e in bucal["reporte"])


def test_duplicados_e_ids_reiniciados(bucal):
    o, cal = oraculo.salud_bucal(), bucal["calidad"]
    assert cal["filas_leidas"] == o["total"]
    assert cal["filas_identicas_eliminadas"] == o["filas_identicas"] > 0
    assert cal["ids_repetidos"] == o["ids_repetidos"] > 0
    assert cal["ids_repetidos_misma_fecha"] == o["ids_repetidos_misma_fecha"] == 0
    assert any("numeración se reinicia" in e["texto"] for e in bucal["reporte"])


def test_id_consulta_no_entra_a_la_base(con_real, build_real):
    columnas = [r[0] for r in con_real.execute(f"DESCRIBE {TABLA}").fetchall()]
    assert "id_consulta" not in columnas
    assert "SB_20" not in (build_real / config.NOMBRE_CATALOGO).read_text(encoding="utf-8")


def test_rangos_de_edad_contra_oraculo(con_real):
    o = oraculo.salud_bucal()
    db = dict(con_real.execute(f"SELECT rango_edad, count(*) FROM {TABLA} GROUP BY 1").fetchall())
    assert db == dict(o["rango"])
    assert db.get(None, 0) == o["edad_vacia"]


def test_periodo_y_meses_calculados_de_los_datos(bucal):
    o = oraculo.salud_bucal()
    esperados = oraculo.meses_esperados_bucal()
    huecos = [m["nombre"] for m in esperados if m["estado"] == "sin_datos"]
    assert bucal["periodo"]["texto"] == periodo_legible(o["inicio"], o["fin"]) + \
        (f" (sin datos de {', '.join(huecos)})" if huecos else "")
    assert {m["mes"]: (m["filas"], m["estado"]) for m in bucal["meses"]} == \
           {m["mes"]: (m["filas"], m["estado"]) for m in esperados}


def test_calidad_reportada(bucal):
    o, cal = oraculo.salud_bucal(), bucal["calidad"]
    controles = {c["descripcion"]: c["filas"] for c in cal["controles"]}
    assert controles["Pesos iguales a 999 (parece un marcador de dato faltante)"] == o["controles"]["peso_999"]
    assert controles["Alturas iguales a 999 (parece un marcador de dato faltante)"] == o["controles"]["altura_999"]
    assert controles["Adultos (18 años o más) con peso menor a 30 kg"] == o["controles"]["peso_menor_30_adultos"]
    assert controles["Adultos (18 años o más) con altura menor a 100 cm"] == o["controles"]["altura_menor_100_adultos"]
    assert controles["Edades mayores a 100 años"] == o["controles"]["edad_mayor_100"]
    textos = " ".join(e["texto"] for e in bucal["reporte"])
    # Los 'null' de peso y altura se reportan como valores no numéricos.
    assert f"'peso': {miles(o['no_numericos']['peso'])} valores no numéricos" in textos
    assert f"'altura': {miles(o['no_numericos']['altura'])} valores no numéricos" in textos


def test_municipios_faltantes_y_jerarquia(bucal):
    o = oraculo.salud_bucal()
    dic = tomllib.loads((config.DATA_DIR / "diccionarios" / "municipios_nl.toml").read_text(encoding="utf-8"))["valores"]
    assert bucal["calidad"]["faltantes"][0]["faltan"] == \
        [n for k, n in dic.items() if clave_comparacion(k) not in o["municipio"]]
    por_grupo = {}
    for (g, letra), n in o["grupo_letra"].items():
        por_grupo.setdefault(g, {})[letra] = n
    for j in bucal["calidad"]["jerarquia"]:
        total = sum(por_grupo[j["padre"]].values())
        assert j["minoritarios"] == {k: v for k, v in por_grupo[j["padre"]].items()
                                     if len(por_grupo[j["padre"]]) > 1 and v / total < 0.02}


def test_textos_normalizados(con_real):
    nombres = dict(con_real.execute(f"SELECT DISTINCT enfermedad, enfermedad_nombre FROM {TABLA}").fetchall())
    assert all("  " not in n and n == n.strip() for n in nombres.values())
    grupos = dict(con_real.execute(f"SELECT DISTINCT grupo, grupo_nombre FROM {TABLA}").fetchall())
    assert grupos["XI"] == "Enfermedades del sistema digestivo"
    municipios = {r[0] for r in con_real.execute(f"SELECT DISTINCT municipio FROM {TABLA}").fetchall()}
    assert {"San Nicolás de los Garza", "General Treviño", "Anáhuac"} <= municipios


def test_columnas_ofrecidas_al_modelo(catalogo_real):
    d = catalogo_real.describir(D)
    alias = {c["alias"] for c in d["columnas"]}
    assert alias == {"edad", "sexo", "municipio", "grupo", "enfermedad", "fecha", "mes", "rango_edad"}
    no_disponibles = {c["columna"]: c["motivo"] for c in d["columnas_no_disponibles"]}
    assert {"peso", "altura", "unidad_medica", "edad_meses", "edad_dias"} == set(no_disponibles)
    assert "CSU" in no_disponibles["unidad_medica"] and "menores" in no_disponibles["unidad_medica"]
    assert "conteo_distinto:unidad_medica" not in d["agregaciones"]
    assert {v["valores"][0] for v in d["valores_especiales"]} == {"XI", "XIX", "XXI", "XVIII"}


def test_fuente_del_portal(bucal, catalogo_real):
    assert bucal["fuente_url"] == bucal["fuentes_md"]["Enlace de la página del conjunto de datos"]
    assert catalogo_real.fuente_texto(D).startswith("Salud Bucal (Secretaría de Salud") and bucal["periodo"]["texto"] in \
        catalogo_real.fuente_texto(D)


def test_describir_no_entrega_conteos(catalogo_real):
    o = oraculo.salud_bucal()
    texto = json.dumps(catalogo_real.describir(D), ensure_ascii=False)
    for n in (o["filas"], o["municipio"]["MONTERREY"], o["enfermedad"]["K021"], *o["mes"].values()):
        assert str(n) not in texto and oraculo.cifra(n) not in texto, n


# ---------- Reglas comunes de Salud (en un solo lugar) ----------

def test_reglas_comunes_se_aplican_a_los_dos_datasets(salud, bucal):
    comun = oraculo.config_comun()
    for ds, propia in ((salud, oraculo.config_salud_propia()), (bucal, oraculo.config_salud_bucal_propia())):
        assert ds["reglas_interpretacion"] == comun["reglas_interpretacion"] + propia.get("reglas_interpretacion", [])
        assert ds["datos_no_disponibles"] == comun["datos_no_disponibles"] + propia.get("datos_no_disponibles", [])
        assert ds["privacidad"]["minimo_por_grupo"] == comun["privacidad"]["minimo_por_grupo"]
        assert ds["unidad"] == comun["unidad"]
        siempre = [a["texto"] for a in ds["advertencias_condicionales"] if a["cuando"].get("siempre")]
        assert any("consulta, no una persona" in t for t in siempre)


def test_los_dataset_toml_no_repiten_lo_comun():
    comun = oraculo.config_comun()
    for propia in (oraculo.config_salud_propia(), oraculo.config_salud_bucal_propia()):
        assert not set(comun["reglas_interpretacion"]) & set(propia.get("reglas_interpretacion", []))
        assert not set(comun["datos_no_disponibles"]) & set(propia.get("datos_no_disponibles", []))
        textos_comunes = {a["texto"] for a in comun["advertencias_condicionales"]}
        assert not textos_comunes & {a["texto"] for a in propia.get("advertencias_condicionales", [])}
        assert not {"unidad", "completitud", "edad"} & set(propia)


def test_solo_salud_bucal_tiene_minimo_reforzado(catalogo_real):
    assert catalogo_real.describir("salud_consulta_general")["privacidad"]["combinaciones"] == []
    comb = catalogo_real.describir(D)["privacidad"]["combinaciones"]
    assert comb == [{"columnas": c["columnas"], "minimo_por_grupo": c["minimo_por_grupo"], "motivo": c["motivo"]}
                    for c in CFG["privacidad"]["combinaciones"]]


# ---------- Consultas ----------

def test_cifras_contra_el_oraculo(motor):
    o = oraculo.salud_bucal()
    assert conteo(motor.consultar(D)) == o["filas"]
    assert conteo(motor.consultar(D, [f("municipio", "igual", "Monterrey")])) == o["municipio"]["MONTERREY"]
    assert conteo(motor.consultar(D, [f("enfermedad", "igual", "K021")])) == o["enfermedad"]["K021"]
    meses = motor.consultar(D, agrupar_por=["mes"])
    assert [x["conteo"] for x in meses["filas"]] == [o["mes"][m] for m in sorted(o["mes"])]


def test_problemas_dentales_sin_grupo_xxi(motor):
    o = oraculo.salud_bucal()
    sin_xxi = sorted(((k, n) for k, n in o["enfermedad"].items() if o["grupo_de"][k] != "XXI"), key=lambda t: (-t[1], t[0]))[:10]
    r = motor.consultar(D, [f("grupo", "no_en", ["XXI"])], ["enfermedad"], limite=10)
    assert [x["conteo"] for x in r["filas"]] == [n for _, n in sin_xxi]
    assert all(o["grupo_de"][x["enfermedad"]] != "XXI" for x in r["filas"])


def test_problemas_dentales_por_rango_de_edad_contra_oraculo(motor):
    """Rango de edad sin lugar: aplica el mínimo general (no el reforzado)."""
    o, k = oraculo.salud_bucal(), CFG["privacidad"]["minimo_por_grupo"]
    rango = "5 a 9 años"
    esperado = sorted(((c, n) for (r, c), n in o["rango_enf"].items() if r == rango and o["grupo_de"][c] != "XXI"),
                      key=lambda t: (-t[1], t[0]))[:10]
    r = motor.consultar(D, [f("rango_edad", "igual", rango), f("grupo", "no_en", ["XXI"])], ["enfermedad"], limite=10)
    assert [x["conteo"] for x in r["filas"]] == [n for _, n in esperado]
    assert not any("al cruzar" in a for a in r["advertencias"])
    assert all(x["conteo"] >= k for x in r["filas"])


def test_edad_promedio_contra_oraculo(motor):
    o = oraculo.salud_bucal()
    r = motor.consultar(D, [f("enfermedad", "igual", "K021")], agregaciones=["conteo", "edad_promedio"])
    assert r["filas"][0]["edad_promedio"] == round(o["edad_suma"]["K021"] / o["edad_n"]["K021"], 1)


def test_advertencias_propias(motor):
    r = motor.consultar(D, agrupar_por=["grupo"])
    avisos = " ".join(r["advertencias"])
    assert "consulta, no una persona" in avisos                    # común
    assert "visita al dentista puede generar más de un registro" in avisos
    assert "sistema digestivo" in avisos and "exámenes odontológicos" in avisos
    assert "quién usa el servicio dental" in " ".join(motor.consultar(D, agrupar_por=["sexo"])["advertencias"])


# ---------- Privacidad: menores en municipios pequeños ----------

def test_municipio_por_edad_usa_el_minimo_reforzado(motor):
    o = oraculo.salud_bucal()
    k = CFG["privacidad"]["combinaciones"][0]["minimo_por_grupo"]
    r = motor.consultar(D, agrupar_por=["municipio", "rango_edad"], limite=200)
    visibles = [x for x in r["filas"] if x["municipio"] != OTROS]
    assert visibles and all(x["conteo"] >= k for x in visibles)
    assert r["suprimidos"]["grupos"] == sum(1 for n in o["mun_rango"].values() if n < k)
    assert any(f"al menos {k} consultas" in a for a in r["advertencias"])
    # Hay celdas que con el mínimo general (5) sí se habrían mostrado: el refuerzo sí cambia algo.
    assert any(5 <= n < k for n in o["mun_rango"].values())


def test_municipio_chico_filtrado_por_edad_no_da_cifra_exacta(motor):
    """Un municipio con 5 a 19 consultas de niñas y niños de 5 a 9 años: no se muestra la cifra exacta."""
    o = oraculo.salud_bucal()
    k = CFG["privacidad"]["combinaciones"][0]["minimo_por_grupo"]
    por_mun = {}
    for (m, e), n in o["mun_edad"].items():
        if 5 <= e <= 9:
            por_mun[m] = por_mun.get(m, 0) + n
    mun = next(m for m, n in sorted(por_mun.items()) if 5 <= n < k)
    nombre = next(v for v in motor._valores[(D, "municipio")]["nombres"] if clave_comparacion(v) == mun)
    r = motor.consultar(D, [f("municipio", "igual", nombre), f("edad", "entre", [5, 9])])
    assert r["filas"][0]["conteo"] is None and r["filas"][0]["conteo_texto"] == f"menos de {k}"
    # El mismo filtro de edad sin municipio sí da la cifra exacta.
    total_5_9 = sum(n for e, n in o["edad"].items() if 5 <= e <= 9)
    assert conteo(motor.consultar(D, [f("edad", "entre", [5, 9])])) == total_5_9


def test_lugar_sin_edad_usa_el_minimo_general(motor):
    o, k = oraculo.salud_bucal(), CFG["privacidad"]["minimo_por_grupo"]
    r = motor.consultar(D, agrupar_por=["municipio", "enfermedad"], limite=200)
    assert all(x["conteo"] >= k for x in r["filas"])
    assert r["suprimidos"]["grupos"] == sum(1 for n in o["mun_enf"].values() if n < k)
    assert not any("al cruzar" in a for a in r["advertencias"])


def test_consulta_general_no_cambia_su_privacidad(motor):
    """El refuerzo es solo de Salud Bucal: Consulta General sigue con su mínimo de 5 al cruzar lugar y edad."""
    r = motor.consultar("salud_consulta_general", agrupar_por=["municipio", "rango_edad"], limite=200)
    assert not any("al cruzar" in a for a in r["advertencias"])
    assert min(x["conteo"] for x in r["filas"] if x["municipio"] != OTROS) < 20


# ---------- Página demo ----------

def test_pagina_demo_muestra_los_dos_datasets_sin_periodos_a_mano(catalogo_real):
    import re
    html = (config.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert "Prototipo de propuesta" in html
    for ds_id in catalogo_real.datasets:
        assert f'data-periodo="{ds_id}"' in html, ds_id         # el periodo sale de /api/salud
    # Ningún periodo escrito a mano ("del 3 de mayo al 30 de septiembre de 2026").
    assert not re.search(r"\bdel \d{1,2} de [a-z]+ al \d{1,2} de [a-z]+", html)
    # Los ejemplos de Salud Bucal de la página también se recomiendan a Salud Bucal.
    for pregunta in re.findall(r'data-ask="([^"]*dental[^"]*)"', html):
        assert catalogo_real.buscar(pregunta)["recomendado"] == D, pregunta


@pytest.mark.parametrize("filtros", [
    [f("municipio", "igual", "Higueras"), f("edad", "igual", 7), f("sexo", "igual", "Femenino")],
    [f("municipio", "igual", "Higueras"), f("edad", "entre", [7, 8]), f("mes", "entre", ["2025-01", "2025-01"]),
     f("fecha", "entre", ["2025-01-06", "2025-01-10"])],
])
def test_filtros_demasiado_especificos_se_rechazan(motor, filtros):
    from consultas import ErrorConsulta
    with pytest.raises(ErrorConsulta) as e:
        motor.consultar(D, filtros)
    assert e.value.tipo == "privacidad"
