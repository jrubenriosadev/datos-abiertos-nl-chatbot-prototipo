"""Ciclo de function calling con Gemini (SDK oficial google-genai).

El modelo decide qué herramientas llamar; el backend las ejecuta, revisa la respuesta
final (marcadores, cifras verificadas) y arma la salida con fuente y avisos deterministas.
El modelo se puede sustituir por uno simulado en las pruebas (ver `generar`).
"""
import time
from dataclasses import dataclass, field

from google.genai import errors, types

import bitacora
from catalogo import Catalogo
from config import Ajustes
from graficas import tabla_de_resultado
from guardrails import (Guardrails, detectar_marcador, extraer_numeros, limpiar_formato, pide_claves, quitar_claves,
                        quitar_marcadores, verificar_cifras)
from herramientas import DECLARACIONES, EstadoSesion, Herramientas, Turno
from prompts import construir_system_prompt

CORRECCION_CIFRAS = ("Revisión automática: estas cifras de tu respuesta no aparecen en los resultados de las herramientas: "
                     "{cifras}. Reescribe la respuesta usando solo cifras devueltas por las herramientas (puedes volver a "
                     "consultar, por ejemplo con la agregación 'proporcion'). No calcules nada por tu cuenta.")
CORRECCION_LENGUAJE = ("Revisión automática: tu respuesta usa un lenguaje que estigmatiza o es sensacionalista: {frases}. "
                       "Reescríbela con lenguaje neutral y respetuoso (por ejemplo, 'personas con depresión' o 'consultas por "
                       "esquizofrenia'), con las mismas cifras.")
CORRECCION_LONGITUD = ("Revisión automática: tu respuesta se cortó por ser demasiado larga. Escríbela completa pero más breve "
                       "(unas 150 palabras), con las cifras principales y las aclaraciones, sin repetir todas las cifras de la gráfica.")
RESERVA_RAZONAMIENTO = 2048  # tokens para el razonamiento interno del modelo (no se muestran)
AVISO_SIN_GRAFICA = "No se pudo dibujar la gráfica; abajo tienes los mismos datos en una tabla."
SIN_RESPUESTA = "No pude completar la respuesta. Intenta preguntarlo de otra forma o de manera más concreta."


@dataclass
class RespuestaModelo:
    texto: str
    llamadas: list[tuple[str | None, str, dict]]      # (id, nombre, argumentos)
    contenido: types.Content | None                   # tal como lo devolvió el modelo (incluye firmas de pensamiento)
    fin: str = ""


class ModeloGemini:
    """Cliente real de Gemini. La API key solo se usa aquí y nunca se registra."""

    def __init__(self, ajustes: Ajustes):
        from google import genai
        self.cliente = genai.Client(api_key=ajustes.gemini_api_key,
                                    http_options=types.HttpOptions(timeout=45_000))
        self.modelo = ajustes.gemini_model
        # En Gemini 3 el razonamiento interno cuenta dentro de max_output_tokens: MAX_OUTPUT_TOKENS de .env
        # es el presupuesto del texto visible y se suma una reserva fija para el razonamiento.
        self.max_tokens = ajustes.max_output_tokens + RESERVA_RAZONAMIENTO
        self.herramientas = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=d["name"], description=d["description"], parameters_json_schema=d["parameters"])
            for d in DECLARACIONES])]

    @staticmethod
    def _con_reintentos(llamada, esperas=(2, 5)):
        """Reintenta errores transitorios de Gemini: 5xx ("alta demanda") y 429 (límite por minuto)."""
        for espera in (*esperas, None):
            try:
                return llamada()
            except (errors.ServerError, errors.ClientError) as e:
                transitorio = isinstance(e, errors.ServerError) or getattr(e, "code", None) == 429
                if not transitorio or espera is None:
                    raise
                time.sleep(espera)

    def generar(self, contenidos: list[types.Content], sistema: str, permitir_herramientas: bool = True) -> RespuestaModelo:
        config = types.GenerateContentConfig(
            system_instruction=sistema,
            tools=self.herramientas,
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="AUTO" if permitir_herramientas else "NONE")),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=self.max_tokens,
            thinking_config=types.ThinkingConfig(thinking_level="LOW"),
        )
        r = self._con_reintentos(lambda: self.cliente.models.generate_content(
            model=self.modelo, contents=contenidos, config=config))
        candidato = r.candidates[0] if r.candidates else None
        contenido = candidato.content if candidato and candidato.content else None
        partes = (contenido.parts or []) if contenido else []
        texto = "".join(p.text for p in partes if p.text and not p.thought)
        llamadas = [(p.function_call.id, p.function_call.name, dict(p.function_call.args or {}))
                    for p in partes if p.function_call]
        fin = str(candidato.finish_reason) if candidato and candidato.finish_reason else ""
        return RespuestaModelo(texto, llamadas, contenido, fin)


@dataclass
class Salida:
    respuesta: str
    grafica: dict | None = None
    fuente: str | None = None
    avisos: list[str] = field(default_factory=list)
    tipo: str = "respuesta"       # respuesta | rechazo del modelo | error_cifras | error_lenguaje | sin_respuesta
    tabla: dict | None = None     # respaldo cuando no se pudo dibujar la gráfica
    contactos: list[dict] | None = None   # líneas de apoyo (si la respuesta las incluye)

    def como_dict(self) -> dict:
        d = {"respuesta": self.respuesta, "grafica": self.grafica, "fuente": self.fuente, "avisos": self.avisos,
             "tabla": self.tabla}
        if self.contactos:
            d["contactos"] = self.contactos
        if self.tipo == "rechazo:crisis":
            d["tipo"] = "apoyo"
        return d


class Conversador:
    def __init__(self, modelo, herramientas: Herramientas, catalogo: Catalogo, guardrails: Guardrails, ajustes: Ajustes):
        self.modelo = modelo
        self.herramientas = herramientas
        self.catalogo = catalogo
        self.guardrails = guardrails
        self.max_llamadas = ajustes.max_tool_calls_per_turn

    def responder(self, mensaje: str, sesion: EstadoSesion, id_sesion: str = "") -> Salida:
        sistema = construir_system_prompt(self.catalogo)
        turno = Turno()
        contenidos = []
        for pregunta, respuesta in sesion.historial:
            contenidos.append(types.Content(role="user", parts=[types.Part(text=pregunta)]))
            contenidos.append(types.Content(role="model", parts=[types.Part(text=respuesta)]))
        contenidos.append(types.Content(role="user", parts=[types.Part(text=mensaje)]))

        # Cifras aceptables: las de la pregunta y las de herramientas del dataset que use esta respuesta
        # (de este turno y de turnos previos); ver EstadoSesion.cifras_permitidas.
        de_la_pregunta = {v * m for _, v, _, m in extraer_numeros(mensaje)}
        llamadas_hechas, corregido, acortado, corregido_lenguaje = 0, False, False, False
        protocolo = self.guardrails.protocolo
        texto = ""
        # Gemini puede escribir parte de la respuesta en la misma vuelta en que llama a una herramienta
        # (por ejemplo, el texto junto con crear_grafica); ese texto se conserva y se une al final.
        textos_previos: list[str] = []
        for _ in range(self.max_llamadas + 4):
            permitir = llamadas_hechas < self.max_llamadas
            r = self.modelo.generar(contenidos, sistema, permitir)
            if r.llamadas and permitir:
                if (r.texto or "").strip():
                    textos_previos.append(r.texto.strip())
                contenidos.append(r.contenido)
                partes = []
                for id_llamada, nombre, args in r.llamadas:
                    if llamadas_hechas >= self.max_llamadas:
                        resultado = {"error": "Se alcanzó el límite de consultas para esta pregunta. Responde con lo que ya tienes."}
                    else:
                        resultado = self.herramientas.ejecutar(nombre, args, turno, sesion, id_sesion)
                        llamadas_hechas += 1
                    partes.append(types.Part(function_response=types.FunctionResponse(
                        id=id_llamada, name=nombre, response={"resultado": resultado})))
                contenidos.append(types.Content(role="user", parts=partes))
                continue

            # Respuesta cortada por el tope de tokens: se pide una versión breve (una sola vez).
            if "MAX_TOKENS" in r.fin:
                if acortado:
                    bitacora.registrar_error("respuesta_cortada", f"fin={r.fin}", id_sesion)
                    return Salida(SIN_RESPUESTA, tipo="sin_respuesta")
                acortado = True
                textos_previos = []
                if r.contenido and r.contenido.parts:
                    contenidos.append(r.contenido)
                contenidos.append(types.Content(role="user", parts=[types.Part(text=CORRECCION_LONGITUD)]))
                continue

            texto = limpiar_formato("\n\n".join(textos_previos + [(r.texto or "").strip()]))
            if not texto:
                bitacora.registrar_error("modelo_sin_texto", f"fin={r.fin}", id_sesion)
                return Salida(SIN_RESPUESTA, tipo="sin_respuesta")

            marcador = detectar_marcador(texto)
            if marcador:
                # [[CRISIS]]: segunda capa del protocolo (el filtro previo no lo detectó). El texto es fijo.
                if marcador == "crisis":
                    sesion.en_crisis = True
                    bitacora.registrar_apoyo("crisis", "modelo", id_sesion)
                respuesta = self.guardrails.mensaje(marcador, mensaje_usuario=mensaje)
                if sesion.en_crisis:
                    respuesta = protocolo.con_linea_apoyo(respuesta)
                return Salida(respuesta, tipo=f"rechazo:{marcador}",
                              contactos=protocolo.contactos if protocolo.linea_vida_en(respuesta) else None)
            # Ningún otro marcador interno llega a la persona ([[GRAFICA_AQUI]], {{grafica}}…).
            texto, pedia_grafica = quitar_marcadores(texto)
            if not pide_claves(mensaje):
                texto = quitar_claves(texto, self.guardrails.patrones_clave)
            if not texto:
                return Salida(SIN_RESPUESTA, tipo="sin_respuesta")

            sin_verificar = verificar_cifras(texto, de_la_pregunta | sesion.cifras_permitidas(turno))
            if sin_verificar and not corregido:
                corregido = True
                textos_previos = []  # el modelo reescribe la respuesta completa
                if r.contenido:
                    contenidos.append(r.contenido)
                contenidos.append(types.Content(role="user", parts=[types.Part(
                    text=CORRECCION_CIFRAS.format(cifras=", ".join(sin_verificar)))]))
                continue
            if sin_verificar:
                bitacora.registrar_error("cifras_no_verificadas", ", ".join(sin_verificar), id_sesion)
                return Salida(self.guardrails.mensaje("error_cifras"), tipo="error_cifras")
            # Lenguaje que estigmatiza o es sensacionalista: se pide una reescritura (una sola vez).
            inadecuado = self.guardrails.lenguaje_inadecuado(texto)
            if inadecuado and not corregido_lenguaje:
                corregido_lenguaje = True
                textos_previos = []
                if r.contenido:
                    contenidos.append(r.contenido)
                contenidos.append(types.Content(role="user", parts=[types.Part(
                    text=CORRECCION_LENGUAJE.format(frases=", ".join(inadecuado)))]))
                continue
            if inadecuado:
                bitacora.registrar_error("lenguaje_inadecuado", ", ".join(inadecuado), id_sesion)
                return Salida(self.guardrails.mensaje("error_lenguaje"), tipo="error_lenguaje")
            break
        else:
            return Salida(SIN_RESPUESTA, tipo="sin_respuesta")

        sesion.cerrar_turno(turno)
        sesion.agregar_turno(mensaje, texto)
        fuente = "; ".join(self.catalogo.fuente_texto(d) for d in turno.datasets) or None
        # Si se esperaba una gráfica (el modelo la intentó o dejó un marcador) y no se pudo crear,
        # se avisa y se entregan los mismos datos en una tabla.
        tabla = None
        if turno.grafica is None and (pedia_grafica or "crear_grafica" in turno.llamadas) and turno.ultimo_resultado:
            tabla = tabla_de_resultado(turno.ultimo_resultado, self.catalogo)
            if tabla:
                texto += "\n\n" + AVISO_SIN_GRAFICA
        # Línea de apoyo fija (de config/crisis.toml) si la respuesta toca autolesión o suicidio, si la pregunta
        # los menciona o si antes hubo una crisis. Se agrega después de verificar las cifras y de guardar el
        # historial: así el modelo no la ve ni la repite, y sus teléfonos no pasan por el verificador.
        contactos = None
        if turno.pide_linea_apoyo or sesion.en_crisis or self.guardrails.menciona_tema_apoyo(mensaje):
            texto = protocolo.con_linea_apoyo(texto)
            contactos = protocolo.contactos
        return Salida(texto, turno.grafica, fuente, list(turno.advertencias), tabla=tabla, contactos=contactos)
