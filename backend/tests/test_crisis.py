"""Protocolo de crisis: detección antes del modelo, mensajes fijos de config/crisis.toml, línea de apoyo,
lenguaje sin estigma y bitácora sin texto. El modelo es simulado: sin red ni cuota."""
import json
import tomllib

import pytest

import config
import oraculo
from crisis import ErrorProtocolo, ProtocoloCrisis
from guardrails import Guardrails
from main import crear_app
from test_api import ModeloSimulado, ajustes, cliente, llamada, preguntar, texto  # noqa: F401 (cliente es fixture)

CFG = tomllib.loads(config.RUTA_CRISIS.read_text(encoding="utf-8"))
LINEA_VIDA = CFG["contactos"]["linea_vida"]["telefono"]
EMERGENCIAS = CFG["contactos"]["emergencias"]["telefono"]


@pytest.fixture(scope="module")
def protocolo() -> ProtocoloCrisis:
    return ProtocoloCrisis.cargar()


# ---------- Detección ----------

# Malestar propio, desesperanza o ideas de hacerse daño o de morir: con y sin acentos, coloquiales y con faltas.
CRISIS = [
    "Me quiero morir",
    "me kiero morir",
    "ME QUIERO MORIR!!!",
    "Ya no quiero vivir",
    "ya no kiero vivir, k hago",
    "kiero suisidarme",
    "me qiero matar",
    "neta ya me quiero morir alv",
    "sucidarme es lo único en lo que pienso",
    "Pienso en quitarme la vida",
    "he pensado en matarme",
    "quiero hacerme daño",
    "a veces me corto los brazos",
    "quisiera dormirme y no despertar",
    "tengo ganas de desaparecer",
    "ya no puedo más, todos estarían mejor sin mí",
    "nadie me quiere, soy una carga",
    "no le veo sentido a mi vida",
    "no tengo razones para vivir",
    "estoy muy deprimido",
    "toy muy deprimida",
    "estoy desesperada, no sé qué hacer",
    "me siento muy sola y vacía",
    "me siento muuuuy mal",
    "me siento de la chingada",
    "hola, me siento muy triste",
    "tengo mucha ansiedad y ya no puedo más",
    "ya no aguanto mi vida",
    "nesesito ablar con alguien",
    "me quiero matarrr",
]

# Preguntas estadísticas legítimas: se responden con datos (no activan el protocolo).
ESTADISTICAS = [
    "¿Cuántas consultas por depresión hubo?",
    "¿Cuántas consultas por lesiones autoinfligidas hay registradas?",
    "¿Hay datos de suicidio en Nuevo León?",
    "Quiero saber cuántas consultas por ansiedad hubo en Monterrey",
    "¿Qué porcentaje de las consultas por depresión son de mujeres?",
    "¿Cuántas personas con esquizofrenia se atendieron cada mes?",
    "Muéstrame una gráfica de consultas por ansiedad por edad",
    "¿Cuáles son los diagnósticos más comunes en adolescentes de 15 a 19 años?",
    "¿Cuántas atenciones por ideación suicida hubo en 2025?",
    "¿Me puedes decir cuántas consultas por intento de suicidio hubo?",
    "¿Cuántas consultas hubo por episodio depresivo grave?",
    "Me interesa ver la tendencia de consultas por depresión",
    "¿cuántas personas tienen ganas de morir según los datos?",
    "¿cuántos jóvenes intentaron suicidarse?",
    "¿Cuánta gente se quiere suicidar en Monterrey?",
    "estoy solo revisando los datos de depresión",
    "me muero de risa con esta gráfica",
    "me siento mal del estómago",
]

TERCEROS = [
    "mi hermano dice que se quiere matar",
    "mi amiga se corta los brazos",
    "conozco a alguien que se quiere suicidar",
    "mi hijo ya no quiere vivir",
]

ORIENTACION = [
    "¿Puedes ser mi terapeuta?",
    "¿Debo ir al psicólogo?",
    "¿Cómo controlo la ansiedad?",
    "¿Qué antidepresivo es mejor?",
    "¿Será que soy bipolar?",
]


def test_hay_suficientes_casos():
    assert len(CRISIS) >= 15 and len(ESTADISTICAS) >= 8


@pytest.mark.parametrize("mensaje", CRISIS)
def test_detecta_crisis(protocolo, mensaje):
    d = protocolo.detectar(mensaje)
    assert d is not None and d.tipo == "crisis", (mensaje, protocolo.normalizar(mensaje))


@pytest.mark.parametrize("mensaje", ESTADISTICAS)
def test_preguntas_estadisticas_no_activan_el_protocolo(protocolo, mensaje):
    assert protocolo.detectar(mensaje) is None, (mensaje, protocolo.normalizar(mensaje))


@pytest.mark.parametrize("mensaje", TERCEROS)
def test_preocupacion_por_otra_persona(protocolo, mensaje):
    assert protocolo.detectar(mensaje).tipo == "tercero"


@pytest.mark.parametrize("mensaje", ORIENTACION)
def test_peticion_de_terapia_o_consejo_se_orienta(protocolo, mensaje):
    assert protocolo.detectar(mensaje).tipo == "orientacion"


def test_casos_del_filtro_general_no_activan_el_protocolo(protocolo):
    from test_guardrails import CASOS
    activados = [m for m, _ in CASOS if protocolo.detectar(m)]
    assert not activados


def test_normalizacion(protocolo):
    assert protocolo.normalizar("Kiero SUISIDARMEEE!!") == protocolo.normalizar("quiero suicidarme")
    assert protocolo.normalizar("no tengo ganas d nd") == protocolo.normalizar("no tengo ganas de nada")
    # No se cambia la persona del pronombre: "matarse" no se corrige a "matarme".
    assert protocolo.normalizar("matarse") != protocolo.normalizar("matarme")


# ---------- Mensajes fijos desde la configuración ----------

def test_mensajes_tienen_los_telefonos_de_la_configuracion(protocolo):
    for tipo in ("crisis", "tercero", "orientacion", "despues_de_crisis"):
        assert LINEA_VIDA in protocolo.mensaje(tipo), tipo
    assert EMERGENCIAS in protocolo.mensaje("crisis") and EMERGENCIAS in protocolo.linea_apoyo
    assert "{" not in protocolo.mensaje("crisis")


def test_los_telefonos_no_estan_escritos_en_el_codigo():
    for archivo in ("crisis.py", "guardrails.py", "main.py", "llm.py", "prompts.py"):
        codigo = (config.RAIZ / "backend" / archivo).read_text(encoding="utf-8")
        assert LINEA_VIDA not in codigo and "8009112000" not in codigo, archivo
    widget = (config.FRONTEND_DIR / "chat-widget.js").read_text(encoding="utf-8")
    pagina = (config.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert LINEA_VIDA not in widget and LINEA_VIDA not in pagina


def test_cambiar_el_telefono_en_el_toml_cambia_los_mensajes(tmp_path):
    otro = config.RUTA_CRISIS.read_text(encoding="utf-8").replace(LINEA_VIDA, "800 000 0000")
    (tmp_path / "crisis.toml").write_text(otro, encoding="utf-8")
    p = ProtocoloCrisis.cargar(tmp_path / "crisis.toml")
    assert "800 000 0000" in p.mensaje("crisis") and LINEA_VIDA not in p.mensaje("crisis")


@pytest.mark.parametrize("cambio", [
    lambda t: t.replace("[mensajes]\n", "[mensajes]\nlinea_apoyo_extra = \"{no_existe.telefono}\"\n"),
    lambda t: t.replace("crisis = \"\"\"", "crisis_vieja = \"\"\""),
    lambda t: t.replace("riesgo = [", "riesgo = [\n  \"esquizofrenic[oa]\","),
    lambda t: t.replace('telefono = "911"', 'telefono = ""'),
])
def test_configuracion_invalida_impide_arrancar(tmp_path, cambio):
    (tmp_path / "crisis.toml").write_text(cambio(config.RUTA_CRISIS.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ErrorProtocolo):
        ProtocoloCrisis.cargar(tmp_path / "crisis.toml")


def test_sin_archivo_de_crisis_el_servidor_no_arranca(build_real, tmp_path):
    with pytest.raises(ErrorProtocolo):
        crear_app(ajustes(), build_dir=build_real, logs_dir=tmp_path, ruta_crisis=tmp_path / "no_existe.toml")


def test_consejo_de_salud_mental_se_orienta_con_la_linea(catalogo_real):
    g = Guardrails(catalogo_real)
    r = g.revisar("¿Qué medicamento sirve para la depresión?")
    assert r.tipo == "consejo_medico" and LINEA_VIDA in r.respuesta and r.contactos
    general = g.revisar("¿Qué medicamento es bueno para la diabetes?")
    assert general.tipo == "consejo_medico" and LINEA_VIDA not in general.respuesta
    assert "10 en Salud Mental" in g.mensaje("privacidad")


def test_lenguaje_inadecuado(catalogo_real):
    g = Guardrails(catalogo_real)
    assert g.lenguaje_inadecuado("Los enfermos mentales de Monterrey") == ["enfermos mentales"]
    assert g.lenguaje_inadecuado("Las personas con depresión y las ideas suicidas") == []
    assert g.menciona_tema_apoyo("¿Hay datos de suicidio?") and not g.menciona_tema_apoyo("¿Hay datos de ansiedad?")


# ---------- Flujo por la API (modelo simulado) ----------

def _registros(carpeta, nombre):
    ruta = carpeta / nombre
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()] if ruta.exists() else []


def test_crisis_no_llama_al_modelo_ni_guarda_el_texto(cliente):
    modelo = ModeloSimulado([])          # si se llamara, fallaría
    c = cliente(modelo)
    mensaje = "neta ya me quiero morir"
    r = preguntar(c, mensaje)
    assert r.status_code == 200
    d = r.json()
    assert d["tipo"] == "apoyo" and d["respuesta"] == ProtocoloCrisis.cargar().mensaje("crisis")
    assert {x["telefono"] for x in d["contactos"]} == {LINEA_VIDA, EMERGENCIAS}
    assert d["grafica"] is None and d["fuente"] is None
    assert modelo.recibido == []
    registros = _registros(cliente.logs, "apoyo.jsonl")
    assert registros and set(registros[-1]) == {"fecha", "tipo", "regla", "sesion"}
    for archivo in cliente.logs.glob("*.jsonl"):
        assert "morir" not in archivo.read_text(encoding="utf-8")


def test_crisis_responde_aunque_se_haya_llegado_al_limite(cliente):
    c = cliente(ModeloSimulado([]), rate_limit_per_minute=1, max_messages_per_session=1)
    assert preguntar(c, "Hola").status_code == 200
    assert preguntar(c, "¿Cuántas consultas hubo?").status_code == 429
    r = preguntar(c, "ya no quiero vivir")
    assert r.status_code == 200 and r.json()["tipo"] == "apoyo"
    largo = "estoy muy deprimido " + "x" * 2000      # también antes del límite de largo del mensaje
    assert preguntar(c, largo).json()["tipo"] == "apoyo"


def test_despues_de_la_crisis_cada_respuesta_lleva_la_linea_de_apoyo(cliente):
    o = oraculo.salud_mental()

    def responder(contenidos):
        resultado = contenidos[-1].parts[0].function_response.response["resultado"]
        return texto(f"Hubo {oraculo.cifra(resultado['filas'][0]['conteo'])} consultas de salud mental.")
    modelo = ModeloSimulado([llamada("consultar_datos", dataset_id="salud_mental"), responder])
    c = cliente(modelo)
    preguntar(c, "me siento muy triste")
    gracias = preguntar(c, "gracias").json()
    assert gracias["respuesta"] == ProtocoloCrisis.cargar().mensaje("despues_de_crisis")
    d = preguntar(c, "¿Cuántas consultas de salud mental hubo?").json()
    assert oraculo.cifra(o["filas"]) in d["respuesta"]
    assert d["respuesta"].endswith(ProtocoloCrisis.cargar().linea_apoyo) and d["contactos"]
    # El mensaje de crisis no se envió al modelo ni entró al historial.
    enviado = json.dumps([str(x) for x in modelo.recibido[0]["contenidos"]], ensure_ascii=False)
    assert "triste" not in enviado


def test_marcador_crisis_del_modelo_usa_el_texto_fijo(cliente):
    modelo = ModeloSimulado([texto("[[CRISIS]]"), texto("No debería llegar aquí.")])
    c = cliente(modelo)
    d = preguntar(c, "¿y los datos? la verdad me pasan cosas feas en la casa").json()
    assert d["tipo"] == "apoyo" and d["respuesta"].startswith(ProtocoloCrisis.cargar().mensaje("crisis"))
    assert "[[" not in d["respuesta"] and d["contactos"]
    assert _registros(cliente.logs, "apoyo.jsonl")[-1]["regla"] == "modelo"
    assert preguntar(c, "gracias").json()["respuesta"] == ProtocoloCrisis.cargar().mensaje("despues_de_crisis")


def test_respuesta_sobre_autolesion_termina_con_la_linea_de_apoyo(cliente):
    o = oraculo.salud_mental()

    def responder(contenidos):
        resultado = contenidos[-1].parts[0].function_response.response["resultado"]
        assert resultado["temas_sensibles"][0]["linea_apoyo"] is True
        return texto(f"Se registraron {resultado['filas'][0]['conteo']} consultas con antecedente de lesión autoinfligida.")
    modelo = ModeloSimulado([llamada("describir_dataset", dataset_id="salud_mental"),
                             llamada("consultar_datos", dataset_id="salud_mental",
                                     filtros=[{"columna": "enfermedad", "operador": "igual", "valor": "Z915"}]),
                             responder,
                             texto("Puedo darte también los datos por sexo.")])
    c = cliente(modelo)
    d = preguntar(c, "¿Cuántas consultas por lesión autoinfligida hubo?").json()
    linea = ProtocoloCrisis.cargar().linea_apoyo
    assert str(o["enfermedad"]["Z915"]) in d["respuesta"]
    assert d["respuesta"].endswith(linea) and d["respuesta"].count(linea) == 1 and d["contactos"]
    # La línea no entra al historial: el modelo no la ve en el turno siguiente.
    preguntar(c, "¿y por sexo?")
    assert LINEA_VIDA not in json.dumps([str(x) for x in modelo.recibido[-1]["contenidos"]], ensure_ascii=False)


def test_respuesta_sin_tema_sensible_no_lleva_linea(cliente):
    modelo = ModeloSimulado([texto("Puedo ayudarte con datos de consultas de ansiedad.")])
    d = preguntar(cliente(modelo), "¿Hay datos de ansiedad?").json()
    assert LINEA_VIDA not in d["respuesta"] and "contactos" not in d


def test_lenguaje_que_estigmatiza_se_reescribe(cliente):
    modelo = ModeloSimulado([texto("Los enfermos mentales se atienden en estos servicios."),
                             texto("Las personas con un trastorno mental se atienden en estos servicios.")])
    d = preguntar(cliente(modelo), "¿Qué datos de salud mental hay?").json()
    assert d["respuesta"].startswith("Las personas con un trastorno mental")
    assert "estigmatiza" in str(modelo.recibido[-1]["contenidos"][-1])


def test_lenguaje_que_insiste_se_reemplaza_por_mensaje_fijo(cliente):
    modelo = ModeloSimulado([texto("Los locos de Escobedo."), texto("Los locos de Escobedo, otra vez.")])
    d = preguntar(cliente(modelo), "¿Qué datos de salud mental hay?").json()
    assert "lenguaje adecuado" in d["respuesta"] and "locos" not in d["respuesta"]


def test_api_salud_entrega_la_linea_de_apoyo(cliente):
    d = cliente().get("/api/salud").json()
    assert LINEA_VIDA in d["apoyo"]["texto"] and {c["telefono"] for c in d["apoyo"]["contactos"]} >= {LINEA_VIDA}
    assert "salud_mental" in {x["id"] for x in d["datasets"]}
