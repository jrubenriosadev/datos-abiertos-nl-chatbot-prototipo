"""crear_grafica: los valores salen del resultado, nunca del modelo. Lo esperado sale del oráculo."""
import pytest

import oraculo
from consultas import ErrorConsulta
from graficas import crear_grafica
from normalizar import clave_comparacion
from oraculo import meses_esperados

D = "salud_consulta_general"


def test_barras_con_valores_reales(motor, catalogo_real):
    o = oraculo.salud()
    r = motor.consultar(D, agrupar_por=["municipio"], limite=10)
    g = crear_grafica(r, catalogo_real, "municipio", "conteo", "Consultas por municipio", "horizontales")
    assert g["valores"] == [x["conteo"] for x in r["filas"]] == [n for _, n in o["municipio"].most_common(10)]
    assert [clave_comparacion(e) for e in g["etiquetas"]] == [m for m, _ in o["municipio"].most_common(10)]
    assert g["unidad"] == "consultas" and g["tipo"] == "horizontales" and g["result_id"] == r["result_id"]


def test_eje_x_acepta_la_columna_legible_y_muestra_nombres(motor, catalogo_real):
    """El modelo suele pedir 'enfermedad_nombre': es la misma categoría y las etiquetas son nombres, no claves."""
    import re
    clave = re.compile(oraculo.config_salud()["columnas"][-1]["patron"])
    r = motor.consultar(D, [{"columna": "grupo", "operador": "no_en", "valor": ["XXI"]}], ["enfermedad"], limite=10)
    for eje in ("enfermedad", "enfermedad_nombre"):
        g = crear_grafica(r, catalogo_real, eje, "conteo", "Enfermedades más frecuentes", "horizontales")
        assert g["etiquetas"] == [x["enfermedad_nombre"] for x in r["filas"]]
        assert not any(clave.fullmatch(e) for e in g["etiquetas"])
    g = crear_grafica(motor.consultar(D, agrupar_por=["mes"]), catalogo_real, "mes_nombre", "conteo", "Por mes", "barras")
    assert g["etiquetas"][0][0].isupper()


def test_tabla_de_respaldo_con_nombres(motor, catalogo_real):
    from graficas import tabla_de_resultado
    r = motor.consultar(D, [{"columna": "grupo", "operador": "no_en", "valor": ["XXI"]}], ["enfermedad"],
                        ["conteo", "proporcion"], limite=5)
    t = tabla_de_resultado(r, catalogo_real)
    assert t["columnas"] == ["Enfermedad", "Consultas", "Porcentaje (%)"]
    assert [f[0] for f in t["filas"]] == [x["enfermedad_nombre"] for x in r["filas"]]
    assert [f[1] for f in t["filas"]] == [x["conteo"] for x in r["filas"]]


def test_meses_marcados_y_huecos(motor, catalogo_real):
    """Una barra por mes del periodo (los meses sin datos quedan vacíos) y los meses marcados señalados."""
    o = oraculo.salud()
    esperados = meses_esperados()
    r = motor.consultar(D, agrupar_por=["mes"])
    g = crear_grafica(r, catalogo_real, "mes", "conteo", "Consultas por mes", "barras")
    assert len(g["etiquetas"]) == len(esperados)
    assert g["etiquetas"][0] == esperados[0]["nombre"][:1].upper() + esperados[0]["nombre"][1:]
    assert g["valores"] == [o["mes"].get(m["mes"]) if m["estado"] != "sin_datos" else None for m in esperados]
    assert g["marcadas"] == [i for i, m in enumerate(esperados) if m["estado"] != "completo"]
    for m in esperados:
        if m["estado"] != "completo":
            assert f"{m['nombre']} ({m['estado'].replace('_', ' ')})" in g["nota"]


def test_codigos_usan_nombre_y_otras_unidades(motor, catalogo_real):
    r = motor.consultar(D, [{"columna": "grupo", "operador": "no_en", "valor": ["XXI"]}], ["enfermedad"],
                        ["conteo", "proporcion", "edad_promedio"], limite=5)
    g = crear_grafica(r, catalogo_real, "enfermedad", "porcentaje", "Porcentaje", "barras")
    assert g["etiquetas"][0] == r["filas"][0]["enfermedad_nombre"] and g["unidad"] == "%"
    g2 = crear_grafica(r, catalogo_real, "enfermedad", "edad_promedio", "Edad promedio", "barras")
    assert g2["unidad"] == "años"


def test_barras_agrupadas_por_sexo(motor, catalogo_real):
    o = oraculo.salud()
    esperados = meses_esperados()
    r = motor.consultar(D, [{"columna": "sexo", "operador": "en", "valor": ["Femenino", "Masculino"]}],
                        ["mes", "sexo"], limite=200)
    g = crear_grafica(r, catalogo_real, "mes", "conteo", "Consultas por mes y sexo", "agrupadas", "sexo")
    assert len(g["etiquetas"]) == len(esperados)  # incluye los meses sin datos como barras vacías
    assert {s["nombre"] for s in g["series"]} == {"Femenino", "Masculino"}
    fem = next(s for s in g["series"] if s["nombre"] == "Femenino")
    por_mes = {x["mes"]: x["conteo"] for x in r["filas"] if x["sexo"] == "Femenino"}
    assert fem["valores"] == [por_mes.get(m["mes"]) for m in esperados]
    assert sum(v for v in fem["valores"] if v) == sum(n for (m, s), n in o["mun_sexo"].items() if s == "Femenino")
    assert g["marcadas"] == [i for i, m in enumerate(esperados) if m["estado"] != "completo"]


@pytest.mark.parametrize("args, fragmento", [
    (("sexo", "conteo", "x", "barras"), "eje_x"),
    (("municipio", "edad_promedio", "x", "barras"), "eje_y"),
    (("municipio", "conteo", "x", "pastel"), "Solo hay barras"),
    (("municipio", "conteo", "x", "agrupadas"), "dos columnas"),
])
def test_errores(motor, catalogo_real, args, fragmento):
    r = motor.consultar(D, agrupar_por=["municipio"], limite=10)
    with pytest.raises(ErrorConsulta) as e:
        crear_grafica(r, catalogo_real, *args)
    assert fragmento in e.value.mensaje


def test_demasiadas_barras(motor, catalogo_real):
    r = motor.consultar(D, agrupar_por=["municipio"], limite=50)
    with pytest.raises(ErrorConsulta):
        crear_grafica(r, catalogo_real, "municipio", "conteo", "x", "barras")


def test_titulo_se_limpia(motor, catalogo_real):
    r = motor.consultar(D, agrupar_por=["sexo"])
    g = crear_grafica(r, catalogo_real, "sexo", "conteo", "<script>alert(1)</script>" + "x" * 300, "barras")
    assert "<" not in g["titulo"] and len(g["titulo"]) <= 120
