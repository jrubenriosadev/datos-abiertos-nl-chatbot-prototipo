"""Endpoint /api/chat con un modelo simulado (guion de llamadas): sin red ni cuota."""
import json
import re

import pytest
from fastapi.testclient import TestClient
from google.genai import types

import config
import oraculo
from dibujo import HAY_NODE, dibujar
from oraculo import cifra
from llm import RespuestaModelo
from main import crear_app

D = "salud_consulta_general"
SESION = "prueba-0001"


def inventada(k: int = 1) -> str:
    """Cifra que no puede salir de los datos: mayor que el total de registros."""
    return cifra(oraculo.salud()["total"] * 10 + 12_345 * k)


def llamada(nombre, **args):
    contenido = types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(id=nombre, name=nombre, args=args))])
    return RespuestaModelo("", [(nombre, nombre, args)], contenido)


def texto(t):
    return RespuestaModelo(t, [], types.Content(role="model", parts=[types.Part(text=t)]))


class ModeloSimulado:
    """Devuelve las respuestas del guion en orden; las funciones reciben lo enviado y deciden."""

    def __init__(self, guion):
        self.guion = list(guion)
        self.recibido = []

    def generar(self, contenidos, sistema, permitir_herramientas=True):
        self.recibido.append({"contenidos": list(contenidos), "sistema": sistema, "permitir": permitir_herramientas})
        paso = self.guion.pop(0)
        return paso(contenidos) if callable(paso) else paso


def ajustes(**cambios):
    base = dict(gemini_api_key="clave-de-prueba", allowed_origins=("http://localhost:8000",), rate_limit_per_minute=100,
                max_messages_per_session=100, max_tool_calls_per_turn=6, max_message_length=500)
    base.update(cambios)
    return config.Ajustes(**base)


@pytest.fixture()
def cliente(build_real, tmp_path):
    def crear(modelo=None, **cambios):
        app = crear_app(ajustes(**cambios), modelo=modelo, build_dir=build_real, logs_dir=tmp_path / "logs")
        return TestClient(app)
    crear.logs = tmp_path / "logs"
    return crear


def preguntar(c, mensaje, sesion=SESION):
    return c.post("/api/chat", json={"mensaje": mensaje, "sesion": sesion})


# ---------- Flujo completo ----------

def test_flujo_con_herramientas_y_grafica(cliente, salud):
    o = oraculo.salud()
    (m1, n1), (m2, n2) = o["municipio"].most_common(2)

    def grafica(contenidos):
        resultado = contenidos[-1].parts[0].function_response.response["resultado"]
        return llamada("crear_grafica", result_id=resultado["result_id"], eje_x="municipio", eje_y="conteo",
                       titulo="Municipios con más consultas", tipo="horizontales")
    modelo = ModeloSimulado([
        llamada("describir_dataset", dataset_id=D),
        llamada("consultar_datos", dataset_id=D, agrupar_por=["municipio"], limite=5),
        grafica,
        texto(f"El municipio con más consultas tiene {cifra(n1)}, seguido de otro con {cifra(n2)}.\n\n"
              "Fuente: Consulta General y Especialidades."),
    ])
    r = preguntar(cliente(modelo), "¿Qué municipios tienen más consultas?")
    assert r.status_code == 200
    d = r.json()
    assert cifra(n1) in d["respuesta"]
    assert d["grafica"]["valores"][:2] == [n1, n2]
    assert d["fuente"].startswith("Consulta General y Especialidades") and salud["periodo"]["texto"] in d["fuente"]
    avisos = " ".join(d["avisos"])
    assert "No hay datos de población" in avisos and "consulta, no una persona" in avisos
    # El system prompt lleva la fecha y el periodo; las herramientas devolvieron sus resultados al modelo.
    assert "Fecha de hoy" in modelo.recibido[0]["sistema"]
    assert modelo.recibido[1]["contenidos"][-1].parts[0].function_response.name == "describir_dataset"


def test_texto_escrito_junto_con_una_llamada_se_conserva(cliente):
    """Gemini a veces escribe la respuesta en la misma vuelta en que llama a crear_grafica."""
    mujeres = cifra(oraculo.salud()["sexo"]["Femenino"])
    def texto_y_grafica(contenidos):
        resultado = contenidos[-1].parts[0].function_response.response["resultado"]
        args = dict(result_id=resultado["result_id"], eje_x="sexo", eje_y="conteo", titulo="Por sexo", tipo="barras")
        contenido = types.Content(role="model", parts=[
            types.Part(text=f"Las mujeres tuvieron {mujeres} consultas."),
            types.Part(function_call=types.FunctionCall(id="g", name="crear_grafica", args=args))])
        return RespuestaModelo(f"Las mujeres tuvieron {mujeres} consultas.", [("g", "crear_grafica", args)], contenido)
    modelo = ModeloSimulado([llamada("consultar_datos", dataset_id=D, agrupar_por=["sexo"]), texto_y_grafica,
                             texto("La gráfica muestra las consultas por sexo.")])
    d = preguntar(cliente(modelo), "¿Cuántas consultas por sexo?").json()
    assert d["respuesta"] == f"Las mujeres tuvieron {mujeres} consultas.\n\nLa gráfica muestra las consultas por sexo."
    assert set(d["grafica"]["etiquetas"]) == {"Femenino", "Masculino", "Intersexual"}


def test_respuesta_cortada_se_pide_breve(cliente):
    cortada = RespuestaModelo("Las enfermedades más comunes son la hipertensión y la diab", [],
                              types.Content(role="model", parts=[types.Part(text="...")]), fin="FinishReason.MAX_TOKENS")
    modelo = ModeloSimulado([cortada, texto("Versión breve y completa.")])
    d = preguntar(cliente(modelo), "¿Cuáles son las enfermedades más comunes?").json()
    assert d["respuesta"] == "Versión breve y completa."
    assert "se cortó" in modelo.recibido[1]["contenidos"][-1].parts[0].text
    # Si se vuelve a cortar, no se muestra una respuesta a medias.
    modelo2 = ModeloSimulado([cortada, cortada])
    d2 = preguntar(cliente(modelo2), "¿Cuáles son las enfermedades más comunes?", sesion="prueba-0002").json()
    assert d2["respuesta"].startswith("No pude completar")


CLAVE = re.compile(oraculo.config_salud()["columnas"][-1]["patron"])  # patrón de las claves de enfermedad
MARCADORES = re.compile(r"\[\[|\]\]|\{\{|\}\}")


def _consulta_enfermedades():
    return llamada("consultar_datos", dataset_id=D, filtros=[{"columna": "grupo", "operador": "no_en", "valor": ["XXI"]}],
                   agrupar_por=["enfermedad"], limite=10)


def test_enfermedades_mas_frecuentes_trae_grafica_sin_marcadores_y_se_dibuja(cliente):
    """Regresión: el modelo pedía eje_x='enfermedad_nombre' (se rechazaba) y escribía [[GRAFICA_AQUI]] en el texto."""
    def grafica_con_columna_legible(contenidos):
        resultado = contenidos[-1].parts[0].function_response.response["resultado"]
        return llamada("crear_grafica", result_id=resultado["result_id"], eje_x="enfermedad_nombre", eje_y="conteo",
                       titulo="Enfermedades más frecuentes", tipo="horizontales")
    modelo = ModeloSimulado([_consulta_enfermedades(), grafica_con_columna_legible,
                             texto("Estas son las enfermedades más frecuentes:\n\n[[GRAFICA_AQUI]]\n\nNo incluyen chequeos.")])
    d = preguntar(cliente(modelo), "¿Cuáles son las enfermedades más frecuentes?").json()
    # 1. La respuesta trae la gráfica, con nombres legibles (no claves).
    assert d["grafica"] and len(d["grafica"]["etiquetas"]) == 10
    assert not any(CLAVE.fullmatch(e) for e in d["grafica"]["etiquetas"])
    o = oraculo.salud()
    sin_xxi = sorted((n for k, n in o["enfermedad"].items() if o["grupo_de"][k] != "XXI"), reverse=True)[:10]
    assert d["grafica"]["valores"] == sin_xxi
    # 2. El texto no contiene marcadores.
    assert not MARCADORES.search(d["respuesta"])
    assert d["respuesta"] == "Estas son las enfermedades más frecuentes:\n\nNo incluyen chequeos."
    # 3. El frontend la dibuja.
    if HAY_NODE:
        vista = dibujar(d)
        assert vista["grafica_dibujada"] and vista["barras"] == 10
        assert vista["etiquetas"] == d["grafica"]["etiquetas"]
        assert not MARCADORES.search(vista["texto_visible"])


def test_sin_grafica_se_quita_el_marcador_y_se_muestra_tabla(cliente):
    """Si la gráfica no se puede crear, el marcador se elimina, se avisa y los datos van en una tabla."""
    def grafica_imposible(contenidos):
        resultado = contenidos[-1].parts[0].function_response.response["resultado"]
        return llamada("crear_grafica", result_id=resultado["result_id"], eje_x="municipio", eje_y="conteo",
                       titulo="x", tipo="barras")
    modelo = ModeloSimulado([_consulta_enfermedades(), grafica_imposible,
                             texto("Aquí está la gráfica: {{grafica}} [[GRAFICA_AQUI]]")])
    d = preguntar(cliente(modelo), "¿Cuáles son las enfermedades más frecuentes?").json()
    assert d["grafica"] is None
    assert not MARCADORES.search(d["respuesta"])
    assert "No se pudo dibujar la gráfica" in d["respuesta"]
    assert d["tabla"] and len(d["tabla"]["filas"]) == 10
    assert not any(CLAVE.fullmatch(str(f[0])) for f in d["tabla"]["filas"])  # nombres, no claves
    if HAY_NODE:
        vista = dibujar(d)
        assert vista["tabla_dibujada"] and vista["filas_tabla"] == 10 and not MARCADORES.search(vista["texto_visible"])


def test_las_claves_solo_se_muestran_si_se_piden(cliente):
    o = oraculo.salud()
    hta = cifra(o["enfermedad"]["I10X"])
    modelo = ModeloSimulado([
        llamada("consultar_datos", dataset_id=D, filtros=[{"columna": "enfermedad", "operador": "igual", "valor": "I10X"}]),
        texto(f"La hipertensión esencial primaria (I10X) tuvo {hta} consultas.\n\n- I10X: hipertensión"),
    ])
    d = preguntar(cliente(modelo), "¿Cuántas consultas por hipertensión hubo?").json()
    assert "I10X" not in d["respuesta"] and hta in d["respuesta"]
    modelo2 = ModeloSimulado([texto("La clave CIE-10 de la hipertensión esencial primaria es I10X.")])
    d2 = preguntar(cliente(modelo2), "¿Cuál es la clave CIE-10 de la hipertensión?", sesion="prueba-0003").json()
    assert "I10X" in d2["respuesta"]


def test_cifra_inventada_se_corrige(cliente):
    mty = cifra(oraculo.salud()["municipio"]["MONTERREY"])
    modelo = ModeloSimulado([
        llamada("consultar_datos", dataset_id=D, filtros=[{"columna": "municipio", "operador": "igual", "valor": "Monterrey"}]),
        texto(f"En Monterrey hubo {inventada()} consultas."),
        texto(f"En Monterrey hubo {mty} consultas."),
    ])
    d = preguntar(cliente(modelo), "¿Cuántas consultas hubo en Monterrey?").json()
    assert d["respuesta"] == f"En Monterrey hubo {mty} consultas."
    correccion = modelo.recibido[2]["contenidos"][-1].parts[0].text
    assert inventada() in correccion and "Revisión automática" in correccion


def test_cifra_inventada_dos_veces_no_se_muestra(cliente):
    modelo = ModeloSimulado([texto(f"Hubo {inventada(1)} consultas."), texto(f"Hubo {inventada(2)} consultas.")])
    c = cliente(modelo)
    d = preguntar(c, "¿Cuántas consultas hubo en total?").json()
    assert "No pude verificar" in d["respuesta"] and inventada(1) not in d["respuesta"]
    assert "cifras_no_verificadas" in (cliente.logs / "errores.jsonl").read_text(encoding="utf-8")


def test_cifras_de_turnos_previos_se_aceptan(cliente):
    o = oraculo.salud()
    ultimo = max(o["mes"])
    n_ultimo = cifra(o["mes"][ultimo])
    modelo = ModeloSimulado([
        llamada("consultar_datos", dataset_id=D, filtros=[{"columna": "mes", "operador": "igual", "valor": ultimo}]),
        texto(f"En el último mes hubo {n_ultimo} consultas, pero ese mes está incompleto."),
        texto(f"Como te comenté, las {n_ultimo} consultas del último mes no son un mes completo."),
    ])
    c = cliente(modelo)
    preguntar(c, "¿Cuántas consultas hubo en septiembre?")
    d = preguntar(c, "¿y eso es mucho?").json()
    assert n_ultimo in d["respuesta"]
    # El historial del turno anterior se envía como texto.
    assert modelo.recibido[-1]["contenidos"][0].parts[0].text == "¿Cuántas consultas hubo en septiembre?"


def test_marcador_del_modelo_da_mensaje_fijo(cliente):
    modelo = ModeloSimulado([texto("[[CONSEJO_MEDICO]]")])
    d = preguntar(cliente(modelo), "¿La diabetes que tengo es grave por mi edad de consultas?").json()
    assert "profesional de salud" in d["respuesta"]
    assert "consejo_medico" in (cliente.logs / "rechazos.jsonl").read_text(encoding="utf-8")


def test_error_de_herramienta_vuelve_al_modelo(cliente):
    modelo = ModeloSimulado([
        llamada("consultar_datos", dataset_id=D, filtros=[{"columna": "peso", "operador": "mayor_igual", "valor": 90}]),
        texto("El peso no está disponible porque tiene muchos vacíos y valores imposibles."),
    ])
    d = preguntar(cliente(modelo), "¿Cuántas consultas de personas con más de 90 kilos?").json()
    error = modelo.recibido[1]["contenidos"][-1].parts[0].function_response.response["resultado"]
    assert error["tipo_error"] == "no_disponible"
    assert d["respuesta"].startswith("El peso no está disponible")


def test_limite_de_llamadas_por_turno(cliente):
    guion = [llamada("buscar_dataset", consulta="salud") for _ in range(3)] + [texto("Listo, sin cifras.")]
    modelo = ModeloSimulado(guion)
    d = preguntar(cliente(modelo, max_tool_calls_per_turn=3), "datos de salud").json()
    assert d["respuesta"] == "Listo, sin cifras."
    assert [p["permitir"] for p in modelo.recibido] == [True, True, True, False]


# ---------- Filtro previo y límites ----------

def test_rechazo_previo_no_llama_al_modelo(cliente):
    modelo = ModeloSimulado([])
    c = cliente(modelo)
    d = preguntar(c, "Dame una receta de pastel; mi correo es ana@ejemplo.com").json()
    assert "Solo puedo ayudarte" in d["respuesta"] and d["grafica"] is None
    assert modelo.recibido == []
    log = (cliente.logs / "rechazos.jsonl").read_text(encoding="utf-8")
    assert "fuera_de_alcance" in log and "ana@ejemplo.com" not in log and SESION not in log


def test_proximamente(cliente):
    d = preguntar(cliente(ModeloSimulado([])), "¿Cuántas escuelas hay en Apodaca?").json()
    assert "Educación" in d["respuesta"] and "próximamente" in d["respuesta"]


def test_mensaje_largo_y_sesion_invalida(cliente):
    c = cliente(ModeloSimulado([]), max_message_length=20)
    r = preguntar(c, "¿Cuántas consultas hubo en todo el estado?")
    assert r.status_code == 422 and "menos de 20 caracteres" in r.json()["respuesta"]
    r = preguntar(c, "hola", sesion="x")
    assert r.status_code == 400
    r = c.post("/api/chat", json={"texto": "hola"})
    assert r.status_code == 422 and "respuesta" in r.json()


def test_rate_limit_y_limite_por_sesion(cliente):
    c = cliente(ModeloSimulado([]), rate_limit_per_minute=2)
    assert preguntar(c, "hola").status_code == 200
    assert preguntar(c, "hola").status_code == 200
    r = preguntar(c, "hola")
    assert r.status_code == 429 and "Espera un minuto" in r.json()["respuesta"]
    c2 = cliente(ModeloSimulado([]), max_messages_per_session=1)
    assert preguntar(c2, "hola").status_code == 200
    assert "Recarga la página" in preguntar(c2, "hola").json()["respuesta"]


def test_sin_api_key(cliente, build_real, tmp_path):
    app = crear_app(ajustes(gemini_api_key=""), modelo=None, build_dir=build_real, logs_dir=tmp_path)
    r = TestClient(app).post("/api/chat", json={"mensaje": "¿Cuántas consultas hubo?", "sesion": SESION})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["respuesta"]


def test_error_del_modelo_responde_amable(cliente):
    def falla(contenidos):
        raise RuntimeError("timeout con key=AIzaSyFALSAFALSAFALSAFALSA12345")
    r = preguntar(cliente(ModeloSimulado([falla])), "¿Cuántas consultas hubo?")
    assert r.status_code == 503 and "no respondió" in r.json()["respuesta"]
    assert "AIza" not in (cliente.logs / "errores.jsonl").read_text(encoding="utf-8")


def test_cuota_agotada_da_mensaje_claro(cliente):
    from google.genai import errors

    def cuota(contenidos):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    r = preguntar(cliente(ModeloSimulado([cuota])), "¿Cuántas consultas hubo?")
    assert r.status_code == 503 and "límite de uso del servicio de Gemini" in r.json()["respuesta"]


def test_reintentos_ante_errores_transitorios_de_gemini(monkeypatch):
    from google.genai import errors

    import llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    alta_demanda = errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
    intentos = []

    def falla_dos_veces():
        intentos.append(1)
        if len(intentos) < 3:
            raise alta_demanda
        return "ok"
    assert llm.ModeloGemini._con_reintentos(falla_dos_veces) == "ok" and len(intentos) == 3

    def siempre_falla():
        raise alta_demanda
    with pytest.raises(errors.ServerError):
        llm.ModeloGemini._con_reintentos(siempre_falla)

    # Un error que no es transitorio (por ejemplo, 400) no se reintenta.
    intentos.clear()

    def peticion_invalida():
        intentos.append(1)
        raise errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})
    with pytest.raises(errors.ClientError):
        llm.ModeloGemini._con_reintentos(peticion_invalida)
    assert len(intentos) == 1


# ---------- CORS, salud y página ----------

def test_cors(cliente):
    c = cliente(ModeloSimulado([]))
    ok = c.options("/api/chat", headers={"Origin": "http://localhost:8000", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:8000"
    nulo = c.options("/api/chat", headers={"Origin": "null", "Access-Control-Request-Method": "POST"})
    assert nulo.headers.get("access-control-allow-origin") is None


def test_salud_no_expone_la_clave(cliente):
    r = cliente(ModeloSimulado([])).get("/api/salud")
    d = r.json()
    assert d["estado"] == "ok" and d["datasets"][0]["id"] == D and d["proximamente"] == ["Educación", "Seguridad"]
    assert "clave-de-prueba" not in json.dumps(d)


def test_sirve_la_pagina_demo(cliente):
    r = cliente(ModeloSimulado([])).get("/")
    assert r.status_code == 200 and "Prototipo de propuesta" in r.text
    assert cliente(ModeloSimulado([])).get("/chat-widget.js").status_code == 200
