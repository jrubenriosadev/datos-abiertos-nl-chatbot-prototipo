"""Motor de consultas: cifras contra el oráculo, privacidad, validación y advertencias.

Lo esperado se calcula con el oráculo (lectura directa de los CSV); los nombres que aparecen
(Monterrey, I10X…) son solo los parámetros de la consulta.
"""
import datetime as dt
import json

import pytest

import oraculo
from oraculo import meses_esperados
from consultas import OTROS, ErrorConsulta
from normalizar import clave_comparacion

D = "salud_consulta_general"


def f(columna, operador, valor):
    return {"columna": columna, "operador": operador, "valor": valor}


def conteo(r):
    return r["filas"][0]["conteo"]


def mes_marcado(estado):
    return next((m for m in meses_esperados() if m["estado"] == estado), None)


def municipio_mas_pequeno():
    """Municipio con menos consultas (tiene muchos grupos pequeños: útil para probar la privacidad)."""
    o = oraculo.salud()
    return min(o["municipio"], key=o["municipio"].get)


# ---------- Cifras ----------

def test_cifras_contra_el_oraculo(motor):
    o = oraculo.salud()
    assert conteo(motor.consultar(D)) == o["filas"]
    assert conteo(motor.consultar(D, [f("municipio", "igual", "Monterrey")])) == o["municipio"]["MONTERREY"]
    assert conteo(motor.consultar(D, [f("municipio", "igual", "guadalupe")])) == o["municipio"]["GUADALUPE"]
    assert conteo(motor.consultar(D, [f("enfermedad", "igual", "I10X")])) == o["enfermedad"]["I10X"]
    assert conteo(motor.consultar(D, [f("enfermedad", "igual", "e119")])) == o["enfermedad"]["E119"]
    ultimo = max(o["mes"])
    assert conteo(motor.consultar(D, [f("mes", "igual", ultimo)])) == o["mes"][ultimo]


def test_ranking_de_municipios_contra_oraculo(motor):
    o = oraculo.salud()
    r = motor.consultar(D, agrupar_por=["municipio"], limite=10)
    assert [x["conteo"] for x in r["filas"]] == [n for _, n in o["municipio"].most_common(10)]
    assert clave_comparacion(r["filas"][0]["municipio"]) == o["municipio"].most_common(1)[0][0]
    assert r["total_filtrado"] == o["filas"]


def test_enfermedades_sin_grupo_xxi_contra_oraculo(motor):
    o = oraculo.salud()
    sin_xxi = {k: n for k, n in o["enfermedad"].items() if o["grupo_de"][k] != "XXI"}
    esperado = sorted(sin_xxi.items(), key=lambda t: (-t[1], t[0]))[:10]
    r = motor.consultar(D, [f("grupo", "no_en", ["XXI"])], ["enfermedad"], limite=10)
    assert [x["conteo"] for x in r["filas"]] == [n for _, n in esperado]
    assert all(o["grupo_de"][x["enfermedad"]] != "XXI" for x in r["filas"])


def test_proporcion_ignorando_un_filtro(motor):
    """¿Qué % de las consultas de Linares son por diabetes tipo 2 (E119)?"""
    o = oraculo.salud()
    esperado = round(100 * o["mun_enf"][("LINARES", "E119")] / o["municipio"]["LINARES"], 1)
    r = motor.consultar(D, [f("municipio", "igual", "Linares"), f("enfermedad", "igual", "E119")],
                        agregaciones=["conteo", "proporcion"], proporcion_ignora_filtros=["enfermedad"])
    assert r["filas"][0]["conteo"] == o["mun_enf"][("LINARES", "E119")]
    assert r["filas"][0]["porcentaje"] == esperado


def test_proporcion_dentro_de_cada_municipio(motor):
    o = oraculo.salud()
    r = motor.consultar(D, [f("grupo", "igual", "IV")], ["municipio"], ["conteo", "proporcion"],
                        proporcion_dentro_de="municipio", proporcion_ignora_filtros=["grupo"], limite=50)
    for x in r["filas"]:
        m = clave_comparacion(x["municipio"])
        assert x["porcentaje"] == round(100 * o["mun_grupo"][(m, "IV")] / o["municipio"][m], 1), m
    r2 = motor.consultar(D, [f("grupo", "igual", "IV")], ["municipio"], ["conteo", "proporcion"],
                         proporcion_dentro_de="municipio", proporcion_ignora_filtros=["grupo"],
                         ordenar_por={"campo": "porcentaje", "direccion": "desc"}, limite=50)
    pct = [x["porcentaje"] for x in r2["filas"]]
    assert pct == sorted(pct, reverse=True)


def test_edad_promedio_y_mediana(motor):
    o = oraculo.salud()
    r = motor.consultar(D, [f("enfermedad", "igual", "I10X")], agregaciones=["conteo", "edad_promedio", "edad_mediana"])
    assert r["filas"][0]["edad_promedio"] == round(o["edad_suma"]["I10X"] / o["enfermedad"]["I10X"], 1)
    assert isinstance(r["filas"][0]["edad_mediana"], (int, float))


def test_conteo_distinto_de_unidades(motor):
    o = oraculo.salud()
    r = motor.consultar(D, agrupar_por=["municipio"], agregaciones=["conteo", "conteo_distinto:unidad_medica"], limite=50)
    for x in r["filas"]:
        m = clave_comparacion(x["municipio"])
        assert x["unidad_medica_distintos"] == len({u for u, mu in o["unidades"] if mu == m}), m


def test_serie_mensual_completa_y_en_orden(motor):
    o = oraculo.salud()
    meses = motor.consultar(D, agrupar_por=["mes"])
    assert [x["mes"] for x in meses["filas"]] == sorted(o["mes"])  # sin límite que la corte
    assert [x["conteo"] for x in meses["filas"]] == [o["mes"][m] for m in sorted(o["mes"])]
    edades = motor.consultar(D, agrupar_por=["rango_edad"])
    assert edades["filas"][0]["rango_edad"] == "Menores de 1 año"
    assert edades["filas"][-1]["rango_edad"] == "65 años y más"


# ---------- Privacidad ----------

def test_ningun_grupo_menor_a_5(motor):
    from ingest import valor_en_regla
    r = motor.consultar(D, agrupar_por=["municipio", "enfermedad"], limite=200)
    assert all(x["conteo"] >= 5 for x in r["filas"])
    # Las claves del tema común de autolesión no se muestran por municipio (van a 'Otros' como ocultas).
    temas = [t for t in oraculo.config_salud()["temas_restringidos"] if t["columna"] == "enfermedad"]
    restringida = lambda c: any(valor_en_regla(c, t) for t in temas)
    mun_enf = oraculo.salud()["mun_enf"]
    assert r["suprimidos"]["grupos"] == sum(1 for (m, c), n in mun_enf.items() if n < 5 and not restringida(c))
    assert r["suprimidos"]["ocultos_por_privacidad"] == sum(1 for (m, c) in mun_enf if restringida(c))


def test_grupos_pequenos_se_juntan_en_otros(motor):
    o, mun = oraculo.salud(), municipio_mas_pequeno()
    pequenos = {k: n for (m, k), n in o["mun_enf"].items() if m == mun and n < 5}
    grandes = [n for (m, k), n in o["mun_enf"].items() if m == mun and n >= 5]
    assert pequenos and len(grandes) <= 200, "el municipio elegido debe tener grupos pequeños"
    r = motor.consultar(D, [f("municipio", "igual", mun)], ["enfermedad"], limite=200)
    assert len(r["filas"]) == len(grandes) + (1 if sum(pequenos.values()) >= 5 else 0)
    if sum(pequenos.values()) >= 5:
        assert r["filas"][-1]["enfermedad"] == OTROS and r["filas"][-1]["conteo"] == sum(pequenos.values())
    assert r["suprimidos"]["grupos"] == len(pequenos)


def test_total_menor_a_5_no_se_muestra(motor):
    o = oraculo.salud()
    mun, clave = next(k for k, n in o["mun_enf"].items() if 0 < n < 5)
    r = motor.consultar(D, [f("municipio", "igual", mun), f("enfermedad", "igual", clave)])
    assert r["filas"][0]["conteo"] is None and r["filas"][0]["conteo_texto"] == "menos de 5"
    assert r["total_filtrado"] is None


def test_total_oculto_si_permite_deducir_un_grupo(motor):
    o = oraculo.salud()
    mun = next((m for (m, s), n in o["mun_sexo"].items() if s == "INTERSEXUAL" and 0 < n < 5), None)
    if mun is None:
        pytest.skip("Ningún municipio tiene entre 1 y 4 consultas con sexo intersexual")
    r = motor.consultar(D, [f("municipio", "igual", mun)], ["sexo"])
    assert r["total_filtrado"] is None
    assert all(x["sexo"] != "Intersexual" for x in r["filas"])


def test_filtros_que_identifican_a_una_persona_se_rechazan(motor):
    o = oraculo.salud()
    dia = o["inicio"] + dt.timedelta(days=10)
    with pytest.raises(ErrorConsulta) as e:
        motor.consultar(D, [f("sexo", "igual", "Masculino"), f("edad", "igual", 34),
                            f("municipio", "igual", municipio_mas_pequeno()), f("fecha", "entre", [dia.isoformat()] * 2)])
    assert e.value.tipo == "privacidad"
    # Dos filtros precisos sí se permiten (mujeres en Monterrey).
    assert conteo(motor.consultar(D, [f("sexo", "igual", "Femenino"), f("municipio", "igual", "Monterrey")])) > 0
    # Rangos de fecha combinados también cuentan como precisos.
    with pytest.raises(ErrorConsulta):
        motor.consultar(D, [f("edad", "entre", [34, 35]), f("fecha", "mayor_igual", dia.isoformat()),
                            f("fecha", "menor_igual", (dia + dt.timedelta(days=1)).isoformat()),
                            f("municipio", "igual", "Mina")])


def test_resultados_sin_id_consulta(motor):
    texto = json.dumps(motor.consultar(D, agrupar_por=["unidad_medica"], limite=200), ensure_ascii=False)
    assert "CE_20" not in texto and "id_consulta" not in texto


# ---------- Validación ----------

@pytest.mark.parametrize("kwargs, fragmento", [
    ({"filtros": [f("colonia", "igual", "Centro")]}, "no existe"),
    ({"filtros": [f("peso", "mayor_igual", 100)]}, "no está disponible"),
    ({"filtros": [f("municipio", "entre", ["A", "B"])]}, "operador"),
    ({"filtros": [f("municipio", "igual", "Monterrey'; DROP TABLE ds_salud_consulta_general;--")]}, "no existe"),
    ({"filtros": [f("edad", "igual", "treinta")]}, "número"),
    ({"filtros": [f("fecha", "entre", ["mayo", "junio"])]}, "AAAA-MM-DD"),
    ({"filtros": [f("mes", "igual", "2026-13")]}, "AAAA-MM"),
    ({"filtros": [{"columna": "municipio"}]}, "necesita"),
    ({"agrupar_por": ["fecha"]}, "día"),
    ({"agrupar_por": ["edad"]}, "rango_edad"),
    ({"agrupar_por": ["municipio", "sexo", "mes"]}, "hasta 2"),
    ({"agregaciones": ["suma"]}, "agregación"),
    ({"agregaciones": ["peso_promedio"]}, "agregación"),
    ({"limite": 500}, "entre 1 y 200"),
    ({"agrupar_por": ["municipio"], "ordenar_por": {"campo": "id_consulta"}}, "ordenar"),
    ({"proporcion_dentro_de": "municipio", "agregaciones": ["proporcion"]}, "agrupar_por"),
])
def test_validaciones(motor, kwargs, fragmento):
    with pytest.raises(ErrorConsulta) as e:
        motor.consultar(D, **kwargs)
    assert fragmento in e.value.mensaje


def test_valor_parecido_sugiere_el_correcto(motor):
    with pytest.raises(ErrorConsulta) as e:
        motor.consultar(D, [f("municipio", "igual", "Monterey")])
    assert "Monterrey" in e.value.sugerencias


def test_dataset_inexistente(motor):
    with pytest.raises(ErrorConsulta) as e:
        motor.consultar("educacion_escuelas")
    assert D in e.value.sugerencias


# ---------- Advertencias ----------

def _adv(r):
    return " | ".join(r["advertencias"])


def test_advertencia_siempre_consultas_no_personas(motor):
    assert "consulta, no una persona" in _adv(motor.consultar(D))


def test_advertencia_sin_tasas_al_comparar_municipios(motor):
    assert "No hay datos de población" in _adv(motor.consultar(D, agrupar_por=["municipio"]))
    assert "No hay datos de población" not in _adv(motor.consultar(D, agrupar_por=["sexo"]))


def test_advertencias_de_meses_en_tendencia(motor):
    """Una tendencia mensual repite la nota de cada mes marcado (sin datos, incompleto, rezago, parcial)."""
    a = _adv(motor.consultar(D, agrupar_por=["mes"]))
    marcados = [m for m in meses_esperados() if m["estado"] != "completo"]
    assert marcados
    for m in marcados:
        assert m["nota"] in a, m["mes"]


def test_advertencias_de_meses_solo_si_se_tocan(motor):
    completo = next(m for m in meses_esperados() if m["estado"] == "completo")
    a = _adv(motor.consultar(D, [f("mes", "igual", completo["mes"])]))
    assert not any(m["nota"] in a for m in meses_esperados() if m["estado"] != "completo")
    incompleto = mes_marcado("incompleto")
    if incompleto:
        r = motor.consultar(D, [f("fecha", "entre", [incompleto["desde"], incompleto["hasta"]])])
        assert incompleto["nota"] in _adv(r)


def test_advertencia_de_mes_sin_datos(motor):
    hueco = mes_marcado("sin_datos")
    if hueco is None:
        pytest.skip("Los datos no tienen meses sin datos")
    r = motor.consultar(D, [f("mes", "igual", hueco["mes"])])
    assert r["filas"][0]["conteo"] == 0
    assert hueco["nota"] in _adv(r) and "No significa que hubo cero" in _adv(r)


def test_advertencia_periodo_en_consultas_sin_fechas(motor, salud):
    a = _adv(motor.consultar(D, agrupar_por=["municipio"]))
    assert f"Periodo cubierto: {salud['periodo']['texto']}" in a
    for m in meses_esperados():
        if m["estado"] != "completo":
            assert f"{m['nombre']} ({m['estado'].replace('_', ' ')})" in a


def test_advertencia_fuera_del_periodo(motor, salud):
    o = oraculo.salud()
    antes = [(o["inicio"] - dt.timedelta(days=400)).isoformat(), (o["inicio"] - dt.timedelta(days=30)).isoformat()]
    r = motor.consultar(D, [f("fecha", "entre", antes)])
    assert r["filas"][0]["conteo"] == 0
    assert f"Solo hay datos {salud['periodo']['texto']}" in _adv(r)
    despues = (o["fin"] + dt.timedelta(days=40)).strftime("%Y-%m")
    assert "Solo hay datos" in _adv(motor.consultar(D, [f("mes", "igual", despues)]))


def test_advertencia_sexo(motor):
    assert "quién usa el servicio" in _adv(motor.consultar(D, agrupar_por=["sexo"]))
    assert "quién usa el servicio" in _adv(motor.consultar(D, [f("sexo", "igual", "Femenino")]))


def test_advertencia_grupo_xxi(motor):
    assert "no son enfermedades" in _adv(motor.consultar(D, agrupar_por=["grupo"]))
    assert "no son enfermedades" not in _adv(motor.consultar(D, [f("grupo", "no_en", ["XXI"])], ["grupo"]))


# ---------- buscar_valores ----------

def test_buscar_valores_por_frecuencia_sin_cifras(motor):
    o = oraculo.salud()
    r = motor.buscar_valores(D, "enfermedad", "diabetes")
    claves = [v["clave"] for v in r["valores"]]
    frecuencias = [o["enfermedad"][k] for k in claves]
    assert frecuencias == sorted(frecuencias, reverse=True)  # ordenadas por frecuencia…
    assert "conteo" not in json.dumps(r)                     # …sin mostrar cifras
    assert "I10X" in [v["clave"] for v in motor.buscar_valores(D, "enfermedad", "hipertensión")["valores"]]
    assert all(v["clave"].startswith("E11") for v in motor.buscar_valores(D, "enfermedad", "E11")["valores"][:3])
    assert motor.buscar_valores(D, "enfermedad", "infecciones urinarias")["total_coincidencias"] > 0


def test_buscar_valores_en_descripciones_alternativas(motor, con_real):
    """Si una clave cambió de descripción entre periodos ('TIPO DOS' / 'TIPO 2'), se encuentra con ambas."""
    variante = con_real.execute("SELECT clave, texto FROM ds_salud_consulta_general_variantes "
                                "WHERE columna = 'enfermedad' LIMIT 1").fetchone()
    if variante is None:
        pytest.skip("No hay claves con descripciones alternativas")
    clave, texto = variante
    r = motor.buscar_valores(D, "enfermedad", texto)
    assert clave in [v["clave"] for v in r["valores"]] or r["total_coincidencias"] > len(r["valores"])


def test_buscar_valores_unidades_y_errores(motor):
    o = oraculo.salud()
    homonima = next((u for u, ms in o["unidad_municipios"].items() if len(ms) > 1), None)
    if homonima:
        r = motor.buscar_valores(D, "unidad_medica", homonima.split(" ", 3)[-1].lower())
        encontradas = [v for v in r["valores"] if clave_comparacion(v).startswith(homonima)]
        assert len(encontradas) >= 2  # la misma unidad en dos municipios, distinguidas por el municipio
    with pytest.raises(ErrorConsulta):
        motor.buscar_valores(D, "edad", "30")
    with pytest.raises(ErrorConsulta):
        motor.buscar_valores(D, "enfermedad", "  ")
    assert motor.buscar_valores(D, "enfermedad", "zzzqqq")["valores"] == []
