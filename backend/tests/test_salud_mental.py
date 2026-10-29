"""Dataset Salud Mental: ingesta, privacidad reforzada, temas restringidos y valores ocultos, contra el oráculo.

Ninguna cifra está escrita a mano: lo esperado se recalcula de los CSV con oraculo.salud_mental().
"""
import json

import pytest

import config
import oraculo
from consultas import OTROS, ErrorConsulta
from ingest import miles, periodo_legible, valor_en_regla
from normalizar import clave_comparacion

D = "salud_mental"
TABLA = "ds_salud_mental"
CFG = oraculo.config_salud_mental()
K = CFG["privacidad"]["minimo_por_grupo"]


def _uno(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def f(columna, operador, valor):
    return {"columna": columna, "operador": operador, "valor": valor}


def conteo(r):
    return r["filas"][0]["conteo"]


def _tema(id_tema):
    return next(t for t in CFG["temas_restringidos"] if t["id"] == id_tema)


def _oculta(clave: str) -> bool:
    """¿La clave de enfermedad no se consulta por separado? (poco frecuente o no disponible)."""
    o = oraculo.salud_mental()
    return o["enfermedad"][clave] < K or any(valor_en_regla(clave, r) for r in CFG["valores_no_disponibles"])


# ---------- Ingesta ----------

def test_total_meses_y_dias_contra_oraculo(con_real):
    o = oraculo.salud_mental()
    assert _uno(con_real, f"SELECT count(*) FROM {TABLA}") == o["filas"] == o["total"]
    assert dict(con_real.execute(f"SELECT mes, count(*) FROM {TABLA} GROUP BY mes").fetchall()) == dict(o["mes"])
    assert dict(con_real.execute(f"SELECT fecha, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["dia"])


def test_municipios_enfermedades_grupos_y_sexo_contra_oraculo(con_real):
    o = oraculo.salud_mental()
    municipios = {clave_comparacion(m): n for m, n in con_real.execute(f"SELECT municipio, count(*) FROM {TABLA} GROUP BY 1").fetchall()}
    assert municipios == dict(o["municipio"])
    assert dict(con_real.execute(f"SELECT enfermedad, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["enfermedad"])
    assert dict(con_real.execute(f"SELECT grupo, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["grupo"])
    sexo = {clave_comparacion(s): n for s, n in con_real.execute(f"SELECT sexo, count(*) FROM {TABLA} GROUP BY 1").fetchall()}
    assert sexo == {clave_comparacion(s): n for s, n in o["sexo"].items()}
    assert dict(con_real.execute(f"SELECT rango_edad, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["rango"])


def test_archivos_cabeceras_e_ids(mental, con_real):
    o, cal = oraculo.salud_mental(), mental["calidad"]
    assert dict(con_real.execute(f"SELECT archivo_origen, count(*) FROM {TABLA} GROUP BY 1").fetchall()) == dict(o["archivo"])
    assert mental["documentacion"] == [r.name for r in oraculo.CARPETA_SALUD_MENTAL.glob("diccionario*.csv")]
    assert not mental["archivos_excluidos"]
    assert len(o["esquemas"]) == 2 and any("cabeceras distintas" in e["texto"] for e in mental["reporte"])
    assert cal["filas_leidas"] == o["total"]
    assert cal["filas_identicas_eliminadas"] == o["filas_identicas"]
    assert cal["ids_repetidos"] == o["ids_repetidos"] > 0
    assert any("numeración se reinicia" in e["texto"] for e in mental["reporte"])


def test_periodo_y_meses_calculados_de_los_datos(mental):
    """Septiembre de 2026 queda incompleto por municipios que dejan de reportar (regla por_columna)."""
    o = oraculo.salud_mental()
    esperados = oraculo.meses_esperados_mental()
    huecos = [m["nombre"] for m in esperados if m["estado"] == "sin_datos"]
    assert huecos
    assert mental["periodo"]["texto"] == periodo_legible(o["inicio"], o["fin"]) + f" (sin datos de {', '.join(huecos)})"
    assert {m["mes"]: (m["filas"], m["estado"]) for m in mental["meses"]} == \
           {m["mes"]: (m["filas"], m["estado"]) for m in esperados}
    por_cobertura = [m for m in esperados if m.get("cobertura_parcial")]
    assert por_cobertura, "la regla por municipio debe marcar algún mes"
    real = {m["mes"]: m for m in mental["meses"]}
    for m in por_cobertura:
        assert [clave_comparacion(v) for v in real[m["mes"]]["cobertura_parcial"]] == m["cobertura_parcial"]
        assert "faltan o bajan mucho los registros de" in real[m["mes"]]["nota"]


def test_peso_altura_y_unidad_no_estan_en_la_base(con_real, mental, build_real):
    columnas = {r[0] for r in con_real.execute(f"DESCRIBE {TABLA}").fetchall()}
    assert not {"peso", "altura", "unidad_medica", "edad_meses", "edad_dias", "id_consulta"} & columnas
    guardadas = {c["alias"]: c["guardada"] for c in mental["columnas"]}
    assert not any(guardadas[a] for a in ("peso", "altura", "unidad_medica"))
    assert "SM_20" not in (build_real / config.NOMBRE_CATALOGO).read_text(encoding="utf-8")


def test_calidad_reportada(mental):
    o, cal = oraculo.salud_mental(), mental["calidad"]
    controles = {c["descripcion"]: c["filas"] for c in cal["controles"]}
    assert controles["Adultos (18 años o más) con peso menor a 30 kg"] == o["controles"]["peso_menor_30_adultos"]
    assert controles["Adultos (18 años o más) con altura menor a 100 cm"] == o["controles"]["altura_menor_100_adultos"]
    assert controles["Pesos mayores a 250 kg"] == o["controles"]["peso_mayor_250"]
    assert controles["Edades mayores a 100 años"] == o["controles"]["edad_mayor_100"]
    textos = " ".join(e["texto"] for e in mental["reporte"])
    assert f"'peso': {miles(o['no_numericos']['peso'])} valores no numéricos" in textos


def test_textos_normalizados(con_real):
    nombres = dict(con_real.execute(f"SELECT DISTINCT enfermedad, enfermedad_nombre FROM {TABLA}").fetchall())
    assert all("  " not in n and n == n.strip() for n in nombres.values())
    # Una sola etiqueta por clave aunque cambie entre publicaciones (con y sin guion).
    assert _uno(con_real, f"SELECT count(DISTINCT enfermedad_nombre) FROM {TABLA} WHERE enfermedad IN ('F428','F429','F512')") == 3
    assert "obsesivo-compulsivo" in nombres["F429"]
    grupos = dict(con_real.execute(f"SELECT DISTINCT grupo, grupo_nombre FROM {TABLA}").fetchall())
    assert grupos["V"] == "Trastornos mentales y del comportamiento"
    sexos = {r[0] for r in con_real.execute(f"SELECT DISTINCT sexo FROM {TABLA}").fetchall()}
    assert sexos == {"Femenino", "Masculino", "Intersexual"}


def test_columnas_ofrecidas_al_modelo(catalogo_real):
    d = catalogo_real.describir(D)
    alias = {c["alias"] for c in d["columnas"]}
    assert alias == {"edad", "sexo", "municipio", "grupo", "enfermedad", "fecha", "mes", "rango_edad"}
    no_disponibles = {c["columna"] for c in d["columnas_no_disponibles"]}
    assert {"peso", "altura", "unidad_medica", "edad_meses", "edad_dias"} == no_disponibles
    assert d["privacidad"]["minimo_por_grupo"] == K == 10
    assert len(d["privacidad"]["bloqueos"]) == len(CFG["privacidad"]["bloqueos"])
    assert {t["tema"] for t in d["privacidad"]["temas_restringidos"]} == {t["nombre"] for t in CFG["temas_restringidos"]}


def test_describir_no_entrega_conteos_ni_valores_ocultos(catalogo_real):
    o = oraculo.salud_mental()
    texto = json.dumps(catalogo_real.describir(D), ensure_ascii=False)
    for n in (o["filas"], o["municipio"]["GENERAL ESCOBEDO"], o["enfermedad"]["F412"], *o["mes"].values()):
        assert str(n) not in texto and oraculo.cifra(n) not in texto, n
    grupos = next(c for c in catalogo_real.describir(D)["columnas"] if c["alias"] == "grupo")["valores"]
    assert {g["clave"] for g in grupos} == {g for g, n in o["grupo"].items() if n >= K}


def test_vocabulario_sin_valores_ocultos(mental):
    """Las palabras de diagnósticos ocultos (por ejemplo, de identidad de género) no entran al vocabulario."""
    vocab = set(mental["vocabulario"])
    assert "transexualismo" not in vocab and "fetichismo" not in vocab
    assert {"depresivo", "ansiedad", "esquizofrenia"} <= vocab


def test_reglas_comunes_se_aplican(mental):
    comun, propia = oraculo.config_comun(), oraculo.config_salud_mental_propia()
    assert mental["reglas_interpretacion"] == comun["reglas_interpretacion"] + propia["reglas_interpretacion"]
    assert [t["id"] for t in mental["temas_restringidos"]] == \
        [t["id"] for t in comun["temas_restringidos"] + propia["temas_restringidos"]]
    assert mental["lenguaje_evitar"] == comun["lenguaje_evitar"]
    assert not set(comun["reglas_interpretacion"]) & set(propia["reglas_interpretacion"])
    assert not {"unidad", "edad", "lenguaje_evitar"} & set(propia)


def test_fuente_del_portal(mental, catalogo_real):
    assert mental["fuente_url"] == mental["fuentes_md"]["Enlace de la página del conjunto de datos"]
    contiene = mental["fuentes_md"]["Qué contiene, en una frase"].lower()
    assert "salud mental" in contiene and "bucal" not in contiene and "dental" not in contiene
    assert catalogo_real.fuente_texto(D).startswith("Salud Mental (Secretaría de Salud")


# ---------- Consultas y privacidad reforzada ----------

def test_cifras_contra_el_oraculo(motor):
    o = oraculo.salud_mental()
    assert conteo(motor.consultar(D)) == o["filas"]
    assert conteo(motor.consultar(D, [f("municipio", "igual", "Monterrey")])) == o["municipio"]["MONTERREY"]
    assert conteo(motor.consultar(D, [f("enfermedad", "igual", "F412")])) == o["enfermedad"]["F412"]
    meses = motor.consultar(D, agrupar_por=["mes"])
    assert [x["conteo"] for x in meses["filas"]] == [o["mes"][m] for m in sorted(o["mes"])]
    menores = motor.consultar(D, [f("edad", "menor_igual", 17)])
    assert conteo(menores) == o["menores"]


def test_minimo_de_10_y_valores_poco_frecuentes_a_otros(motor):
    """Por grupo: los grupos con menos de 10 consultas en todo el periodo no se muestran por separado."""
    o = oraculo.salud_mental()
    r = motor.consultar(D, agrupar_por=["grupo"])
    visibles = {x["grupo"]: x["conteo"] for x in r["filas"] if x["grupo"] != OTROS}
    assert visibles == {g: n for g, n in o["grupo"].items() if n >= K}
    raros = sum(n for n in o["grupo"].values() if n < K)
    assert 0 < raros
    if raros < K:   # lo oculto suma menos de 10: no hay 'Otros' y el total exacto no se muestra
        assert OTROS not in {x["grupo"] for x in r["filas"]} and r["total_filtrado"] is None


def test_ranking_de_diagnosticos_sin_grupo_xxi(motor):
    o = oraculo.salud_mental()
    esperado = sorted(((c, n) for c, n in o["enfermedad"].items() if o["grupo_de"][c] != "XXI" and not _oculta(c)),
                      key=lambda t: (-t[1], t[0]))[:10]
    r = motor.consultar(D, [f("grupo", "no_en", ["XXI"])], ["enfermedad"], limite=10)
    assert [(x["enfermedad"], x["conteo"]) for x in r["filas"]] == esperado


def test_diagnosticos_por_rango_de_edad_contra_oraculo(motor):
    o = oraculo.salud_mental()
    rango = "15 a 19 años"
    esperado = sorted(((c, n) for (rg, c), n in o["rango_enf"].items() if rg == rango and n >= K and not _oculta(c)),
                      key=lambda t: (-t[1], t[0]))[:10]
    r = motor.consultar(D, [f("rango_edad", "igual", rango)], ["enfermedad"], limite=10)
    assert [(x["enfermedad"], x["conteo"]) for x in r["filas"]] == esperado


def test_municipio_por_edad_usa_el_minimo_de_20(motor):
    o = oraculo.salud_mental()
    k20 = CFG["privacidad"]["combinaciones"][0]["minimo_por_grupo"]
    r = motor.consultar(D, agrupar_por=["municipio", "rango_edad"], limite=200)
    visibles = [x for x in r["filas"] if x["municipio"] != OTROS]
    assert visibles and all(x["conteo"] >= k20 for x in visibles)
    assert any(K <= n < k20 for n in o["mun_rango"].values())     # el refuerzo sí cambia algo
    assert any(f"al menos {k20} consultas" in a for a in r["advertencias"])


def test_municipio_por_diagnostico_usa_el_minimo_de_10(motor):
    o = oraculo.salud_mental()
    r = motor.consultar(D, agrupar_por=["municipio", "enfermedad"], limite=200)
    assert all(x["conteo"] >= K for x in r["filas"])
    # Pequeños: los grupos con menos de 10, salvo los que ya se ocultan por otra razón (diagnóstico o municipio
    # poco frecuente, o un tema restringido, que no se cruza con el municipio).
    temas = [t for t in CFG["temas_restringidos"] if t["columna"] == "enfermedad"]
    restringida = lambda c: any(valor_en_regla(c, t) for t in temas)
    esperado = sum(1 for (m, c), n in o["mun_enf"].items()
                   if n < K and not _oculta(c) and o["municipio"][m] >= K and not restringida(c))
    assert r["suprimidos"]["grupos"] == esperado
    assert r["suprimidos"]["ocultos_por_privacidad"] > 0


@pytest.mark.parametrize("filtros, agrupar", [
    # lugar + diagnóstico + persona
    ([f("municipio", "igual", "Monterrey"), f("sexo", "igual", "Femenino")], ["enfermedad"]),
    ([f("enfermedad", "igual", "F412"), f("rango_edad", "igual", "15 a 19 años")], ["municipio"]),
    ([f("municipio", "igual", "Monterrey")], ["enfermedad", "sexo"]),
    # lugar + diagnóstico + fecha
    ([f("enfermedad", "igual", "F412")], ["municipio", "mes"]),
    ([f("municipio", "igual", "Monterrey"), f("mes", "igual", "2026-03")], ["enfermedad"]),
    # edad + sexo + lugar + fecha
    ([f("sexo", "igual", "Femenino"), f("municipio", "igual", "Monterrey"), f("mes", "igual", "2026-03")], ["rango_edad"]),
])
def test_cruces_bloqueados(motor, filtros, agrupar):
    with pytest.raises(ErrorConsulta) as e:
        motor.consultar(D, filtros, agrupar)
    assert e.value.tipo == "privacidad" and "podría identificar a una persona" in e.value.mensaje


@pytest.mark.parametrize("filtros, agrupar", [
    ([], ["enfermedad", "rango_edad"]),
    ([], ["enfermedad", "sexo"]),
    ([f("municipio", "igual", "Monterrey")], ["enfermedad"]),
    ([f("sexo", "igual", "Femenino"), f("rango_edad", "igual", "15 a 19 años")], ["enfermedad"]),
    ([f("enfermedad", "igual", "F412")], ["mes"]),
    # Excluir valores no acota: un filtro 'no_en' de diagnóstico no cuenta como diagnóstico.
    ([f("municipio", "igual", "Monterrey"), f("enfermedad", "no_en", ["Z504"])], ["sexo"]),
])
def test_cruces_permitidos(motor, filtros, agrupar):
    r = motor.consultar(D, filtros, agrupar, limite=200)
    assert all(x["conteo"] >= K for x in r["filas"])


def test_bloqueos_solo_en_salud_mental(motor):
    r = motor.consultar("salud_consulta_general", [f("municipio", "igual", "Monterrey"), f("sexo", "igual", "Femenino")],
                        ["enfermedad"], limite=5)
    assert r["filas"]


# ---------- Temas restringidos ----------

def test_autolesion_solo_total_sexo_y_edad(motor):
    o = oraculo.salud_mental()
    tema = _tema("autolesion")
    claves = sorted(c for c in o["enfermedad"] if valor_en_regla(c, tema))
    assert claves, "debe haber consultas con claves de autolesión"
    total = motor.consultar(D, [f("enfermedad", "en", claves)])
    assert conteo(total) == sum(o["enfermedad"][c] for c in claves)
    assert total["temas_sensibles"] and total["temas_sensibles"][0]["linea_apoyo"] is True
    z = motor.consultar(D, [f("enfermedad", "igual", "Z915")], ["sexo"])
    assert {clave_comparacion(x["sexo"]): x["conteo"] for x in z["filas"] if x["sexo"] != OTROS} == \
        {clave_comparacion(s): n for (s, c), n in o["sexo_enf"].items() if c == "Z915" and n >= K}
    assert any("antecedente" in a for a in z["advertencias"])


@pytest.mark.parametrize("filtros, agrupar", [
    ([f("enfermedad", "igual", "Z915")], ["municipio"]),
    ([f("enfermedad", "igual", "Z915")], ["mes"]),
    ([f("enfermedad", "en", ["Z915", "R458"]), f("municipio", "igual", "Monterrey")], []),
    ([f("enfermedad", "en", ["Z915", "F412"]), f("fecha", "entre", ["2025-01-01", "2025-12-31"])], []),
    ([f("enfermedad", "igual", "T743")], ["municipio"]),          # violencia
    ([f("grupo", "igual", "XIX"), f("municipio", "igual", "Monterrey")], []),
])
def test_temas_restringidos_no_se_cruzan_con_lugar_ni_fecha(motor, filtros, agrupar):
    with pytest.raises(ErrorConsulta) as e:
        motor.consultar(D, filtros, agrupar)
    assert e.value.tipo == "tema_restringido" and "tema sensible" in e.value.mensaje


def test_autolesion_no_aparece_en_rankings_por_municipio(motor):
    """En un municipio con 10 o más consultas de Z915, el ranking de diagnósticos no la muestra por separado."""
    o = oraculo.salud_mental()
    mun = next(m for (m, c), n in o["mun_enf"].most_common() if c == "Z915" and n >= K)
    nombre = next(v for v in motor._valores[(D, "municipio")]["nombres"] if clave_comparacion(v) == mun)
    r = motor.consultar(D, [f("municipio", "igual", nombre)], ["enfermedad"], limite=200)
    assert "Z915" not in {x["enfermedad"] for x in r["filas"]}
    assert r["suprimidos"]["ocultos_por_privacidad"] >= 1
    assert not r["temas_sensibles"]
    # Por grupo y municipio, los grupos de violencia tampoco salen por separado.
    g = motor.consultar(D, agrupar_por=["municipio", "grupo"], limite=200)
    assert not {"XIX", "XX"} & {x["grupo"] for x in g["filas"]}


def test_autolesion_visible_en_ranking_estatal_marca_el_tema(motor):
    r = motor.consultar(D, agrupar_por=["enfermedad"], limite=200)
    assert "Z915" in {x["enfermedad"] for x in r["filas"]}
    assert any(t["linea_apoyo"] for t in r["temas_sensibles"])


def test_tema_de_autolesion_es_comun_a_consulta_general(motor):
    with pytest.raises(ErrorConsulta) as e:
        motor.consultar("salud_consulta_general", [f("enfermedad", "igual", "Z915")], ["municipio"])
    assert e.value.tipo == "tema_restringido"
    r = motor.consultar("salud_consulta_general", [f("enfermedad", "igual", "Z915")])
    assert conteo(r) == oraculo.salud()["enfermedad"]["Z915"]
    assert r["temas_sensibles"][0]["linea_apoyo"] is True


# ---------- Valores ocultos ----------

def test_buscar_valores_no_lista_valores_ocultos(motor):
    r = motor.buscar_valores(D, "enfermedad", "identidad genero")
    assert not [v for v in r["valores"] if v["clave"].startswith("F64")]
    assert "nota_privacidad" in r
    assert not motor.buscar_valores(D, "enfermedad", "envenenamiento")["valores"]
    assert motor.buscar_valores(D, "enfermedad", "depresivo")["valores"]


def test_filtrar_un_valor_oculto_se_rechaza(motor):
    o = oraculo.salud_mental()
    with pytest.raises(ErrorConsulta) as e:
        motor.consultar(D, [f("enfermedad", "igual", "F640")])
    assert e.value.tipo == "no_disponible" and "CIE-11" in e.value.mensaje
    rara = next(c for c, n in sorted(o["enfermedad"].items()) if n < K)
    with pytest.raises(ErrorConsulta) as e:
        motor.consultar(D, [f("enfermedad", "igual", rara)])
    assert e.value.tipo == "privacidad"
    # El total de su grupo sí los incluye.
    assert conteo(motor.consultar(D, [f("grupo", "igual", "V")])) == o["grupo"]["V"]


# ---------- Página demo ----------

def test_pagina_demo_tiene_salud_mental(catalogo_real):
    import re
    html = (config.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert "Prototipo de propuesta" in html and 'data-periodo="salud_mental"' in html and "data-apoyo" in html
    assert "tres conjuntos de datos" in html and "salud mental" in re.search(r'subtitle: "([^"]*)"', html).group(1)
    assert "salud mental" in re.search(r'welcome: "([^"]*)"', html).group(1)
    preguntas = re.findall(r'data-ask="([^"]*salud mental[^"]*)"', html)
    assert len(preguntas) >= 2
    for pregunta in preguntas:
        assert catalogo_real.buscar(pregunta)["recomendado"] == D, pregunta
    # Ni cifras ni lenguaje que estigmatiza en la página.
    assert not re.search(r"enferm[oa]s mentales|locos", html.lower())
