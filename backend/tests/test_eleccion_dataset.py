"""Elección del dataset correcto y ningún cruce entre datasets.

1. buscar_dataset recomienda Salud Bucal para lo dental, Salud Mental para la salud mental y
   Consulta General para lo demás.
2. Una respuesta usa un solo dataset: un segundo dataset en el mismo turno se rechaza.
3. El verificador no deja pasar cifras de otro dataset (ni de este turno ni de turnos anteriores).
Las cifras esperadas salen del oráculo (lectura directa de los CSV).
"""
import pytest

import oraculo
from herramientas import EstadoSesion, Herramientas, Turno
from oraculo import cifra
from test_api import ModeloSimulado, cliente, llamada, preguntar, texto  # noqa: F401 (cliente es una fixture)

CG, SB, SM = "salud_consulta_general", "salud_bucal", "salud_mental"


# ---------- 1. buscar_dataset ----------

@pytest.mark.parametrize("pregunta, esperado", [
    # Lo dental va a Salud Bucal, aunque Consulta General también tenga diagnósticos de caries.
    ("consultas dentales", SB),
    ("¿Cuántas consultas por caries hubo?", SB),
    ("¿Cuántas consultas dentales hubo en Monterrey?", SB),
    ("gingivitis en Monterrey", SB),
    ("consultas de salud bucal", SB),
    ("examen odontológico en niños", SB),
    ("¿Cuáles son los problemas dentales más comunes?", SB),
    ("¿Cuántas veces fueron al dentista en Linares?", SB),
    ("fracturas de dientes", SB),
    ("muelas", SB),
    # Lo de salud mental va a Salud Mental, aunque Consulta General también tenga diagnósticos del grupo V.
    ("¿Cuántas consultas por depresión hubo?", SM),
    ("consultas de salud mental", SM),
    ("¿Cuántas consultas por ansiedad hubo en Monterrey?", SM),
    ("¿Hay datos de suicidio?", SM),
    ("esquizofrenia por grupo de edad", SM),
    ("consumo de drogas y alcoholismo", SM),
    ("¿Cuántas atenciones de psicología hubo cada mes?", SM),
    ("¿Cuáles son los diagnósticos de salud mental más comunes en adolescentes de 15 a 19 años?", SM),
    # Lo demás va a Consulta General.
    ("consultas por diabetes", CG),
    ("¿Cuáles son las enfermedades más comunes?", CG),
    ("infecciones respiratorias en Guadalupe", CG),
    ("hipertensión", CG),
    # Si piden expresamente consultas generales o de especialidad, también.
    ("¿Cuántas consultas generales fueron por caries?", CG),
    ("consultas de especialidad por caries", CG),
    ("¿Cuántas consultas generales fueron por depresión?", CG),
    # Preguntas generales (empate): gana Consulta General por su prioridad, y se avisa que hay otros.
    ("¿Cuántas consultas hubo cada mes?", CG),
    ("¿Cuántas consultas hubo en Monterrey?", CG),
])
def test_buscar_recomienda_el_dataset_correcto(catalogo_real, pregunta, esperado):
    r = catalogo_real.buscar(pregunta)
    assert r["recomendado"] == esperado == r["datasets"][0]["dataset_id"], pregunta
    if len(r["datasets"]) > 1:
        assert "un solo conjunto" in r["nota"]


# ---------- 2. Un solo dataset por respuesta ----------

@pytest.fixture()
def herramientas(motor, catalogo_real):
    return Herramientas(motor, catalogo_real)


def test_segundo_dataset_en_el_mismo_turno_se_rechaza(herramientas):
    turno, sesion = Turno(), EstadoSesion()
    r1 = herramientas.ejecutar("consultar_datos", {"dataset_id": SB}, turno, sesion)
    assert r1["filas"][0]["conteo"] == oraculo.salud_bucal()["filas"]
    r2 = herramientas.ejecutar("consultar_datos", {"dataset_id": CG}, turno, sesion)
    assert r2["tipo_error"] == "un_solo_conjunto" and "Salud Bucal" in r2["error"]
    assert turno.datasets == [SB]
    # Describir el otro dataset sí se puede (sirve para elegir); consultarlo no.
    assert "error" not in herramientas.ejecutar("describir_dataset", {"dataset_id": CG}, turno, sesion)


def test_no_se_grafica_un_resultado_de_otro_dataset(herramientas):
    sesion = EstadoSesion()
    previo = herramientas.ejecutar("consultar_datos", {"dataset_id": CG, "agrupar_por": ["sexo"]}, Turno(), sesion)
    turno = Turno()
    herramientas.ejecutar("consultar_datos", {"dataset_id": SB, "agrupar_por": ["sexo"]}, turno, sesion)
    r = herramientas.ejecutar("crear_grafica", {"result_id": previo["result_id"], "eje_x": "sexo", "eje_y": "conteo",
                                                "titulo": "x", "tipo": "barras"}, turno, sesion)
    assert r["tipo_error"] == "un_solo_conjunto" and turno.grafica is None


def test_cifras_permitidas_son_solo_las_del_dataset_usado(herramientas):
    o_cg, o_sb = oraculo.salud(), oraculo.salud_bucal()
    sesion = EstadoSesion()
    turno1 = Turno()
    herramientas.ejecutar("consultar_datos", {"dataset_id": CG}, turno1, sesion)
    sesion.cerrar_turno(turno1)
    turno2 = Turno()
    herramientas.ejecutar("consultar_datos", {"dataset_id": SB}, turno2, sesion)
    permitidas = sesion.cifras_permitidas(turno2)
    assert o_sb["filas"] in permitidas and o_cg["filas"] not in permitidas
    # Un seguimiento sin herramientas puede citar las cifras de la respuesta anterior (su dataset).
    sesion.cerrar_turno(turno2)
    assert o_sb["filas"] in sesion.cifras_permitidas(Turno())
    assert o_cg["filas"] not in sesion.cifras_permitidas(Turno())


# ---------- 3. Flujo completo por la API (modelo simulado) ----------

def test_consultas_dentales_se_responden_con_salud_bucal(cliente):  # noqa: F811
    total = oraculo.salud_bucal()["filas"]

    def describe_el_recomendado(contenidos):
        resultado = contenidos[-1].parts[0].function_response.response["resultado"]
        assert resultado["recomendado"] == SB        # el modelo recibe la recomendación correcta
        return llamada("describir_dataset", dataset_id=resultado["recomendado"])
    modelo = ModeloSimulado([
        llamada("buscar_dataset", consulta="consultas dentales"),
        describe_el_recomendado,
        llamada("consultar_datos", dataset_id=SB),
        texto(f"Hubo {cifra(total)} consultas dentales registradas.\n\nFuente: Salud Bucal."),
    ])
    d = preguntar(cliente(modelo), "¿Cuántas consultas dentales hubo?").json()
    assert cifra(total) in d["respuesta"]
    assert d["fuente"].startswith("Salud Bucal") and "Consulta General" not in d["fuente"]
    assert any("visita al dentista" in a for a in d["avisos"])


def test_intento_de_usar_dos_datasets_queda_en_uno(cliente):  # noqa: F811
    total_sb = oraculo.salud_bucal()["filas"]

    def revisa_el_rechazo(contenidos):
        resultado = contenidos[-1].parts[0].function_response.response["resultado"]
        assert resultado["tipo_error"] == "un_solo_conjunto"
        return texto(f"Hubo {cifra(total_sb)} consultas dentales. Las consultas generales se pueden consultar aparte.")
    modelo = ModeloSimulado([
        llamada("consultar_datos", dataset_id=SB),
        llamada("consultar_datos", dataset_id=CG),
        revisa_el_rechazo,
    ])
    d = preguntar(cliente(modelo), "¿Hubo más consultas dentales o generales?").json()
    assert cifra(total_sb) in d["respuesta"]
    assert d["fuente"].startswith("Salud Bucal") and "Consulta General" not in d["fuente"]


def test_cifra_de_otro_dataset_no_pasa_el_verificador(cliente):  # noqa: F811
    total_cg, total_sb = oraculo.salud()["filas"], oraculo.salud_bucal()["filas"]
    mezcla = f"Hubo {cifra(total_cg)} consultas generales y {cifra(total_sb)} dentales."
    modelo = ModeloSimulado([llamada("consultar_datos", dataset_id=CG), texto(mezcla), texto(mezcla)])
    c = cliente(modelo)
    d = preguntar(c, "¿Cuántas consultas generales y dentales hubo?").json()
    assert "No pude verificar" in d["respuesta"] and cifra(total_sb) not in d["respuesta"]
    correccion = modelo.recibido[2]["contenidos"][-1].parts[0].text
    assert cifra(total_sb) in correccion and cifra(total_cg) not in correccion


def test_no_se_compara_con_una_respuesta_anterior_de_otro_dataset(cliente):  # noqa: F811
    total_cg, total_sb = oraculo.salud()["filas"], oraculo.salud_bucal()["filas"]
    modelo = ModeloSimulado([
        llamada("consultar_datos", dataset_id=SB),
        texto(f"Hubo {cifra(total_sb)} consultas dentales."),
        llamada("consultar_datos", dataset_id=CG),
        texto(f"Hubo {cifra(total_cg)} consultas generales, más que las {cifra(total_sb)} dentales."),
        texto(f"Hubo {cifra(total_cg)} consultas generales."),
    ])
    c = cliente(modelo)
    preguntar(c, "¿Cuántas consultas dentales hubo?")
    d = preguntar(c, "¿Y cuántas consultas generales? ¿Fueron más?").json()
    assert d["respuesta"] == f"Hubo {cifra(total_cg)} consultas generales."
    assert d["fuente"].startswith("Consulta General") and "Salud Bucal" not in d["fuente"]
    assert cifra(total_sb) in modelo.recibido[4]["contenidos"][-1].parts[0].text   # se pidió corregir
