"""Servidor local: POST /api/chat, GET /api/salud y la página demo en http://localhost:8000/.

Uso (desde la raíz del proyecto, con el entorno virtual):
    .venv\\Scripts\\python backend/main.py      (Windows)
    .venv/bin/python backend/main.py          (macOS / Linux)
Reingiere los datos si cambiaron y levanta el servidor en HOST:PORT de .env.
"""
import sys
from pathlib import Path

try:
    import duckdb
except ModuleNotFoundError:
    # Lo más común: se ejecutó con el Python del sistema y no con el del entorno virtual.
    print("Faltan las dependencias en este Python. Ejecuta el servidor con el entorno virtual:\n"
          "  Windows:        .venv\\Scripts\\python backend\\main.py\n"
          "  macOS / Linux:  .venv/bin/python backend/main.py\n"
          "Si no existe .venv, primero: python -m venv .venv  y luego  "
          ".venv\\Scripts\\python -m pip install -r backend\\requirements.txt", file=sys.stderr)
    sys.exit(1)
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import bitacora
import config
import ingest
from catalogo import Catalogo
from consultas import MotorConsultas
from crisis import ErrorProtocolo, ProtocoloCrisis
from guardrails import SESION_VALIDA, Guardrails, LimitadorUso
from herramientas import AlmacenSesiones, Herramientas
from llm import Conversador, ModeloGemini


class Peticion(BaseModel):
    mensaje: str
    sesion: str


def _json(respuesta: str, estado: int = 200, **extra) -> JSONResponse:
    cuerpo = {"respuesta": respuesta, "grafica": None, "fuente": None, "avisos": [], "tabla": None, **extra}
    return JSONResponse(cuerpo, status_code=estado)


def crear_app(ajustes: config.Ajustes | None = None, modelo=None, build_dir: Path = config.BUILD_DIR,
              data_dir: Path = config.DATA_DIR, logs_dir: Path | None = None, servir_frontend: bool = True,
              ruta_crisis: Path = config.RUTA_CRISIS) -> FastAPI:
    """Crea la aplicación. En pruebas se puede inyectar un modelo simulado y carpetas temporales.

    Si falta config/crisis.toml o tiene errores, lanza ErrorProtocolo: sin protocolo de crisis no se arranca."""
    ajustes = ajustes or config.cargar_ajustes()
    bitacora.configurar(logs_dir)
    protocolo = ProtocoloCrisis.cargar(ruta_crisis)
    if ingest.necesita_reingesta(data_dir, build_dir):
        ingest.ingerir(data_dir, build_dir)
    catalogo = Catalogo.cargar(build_dir)
    con = duckdb.connect(str(build_dir / config.NOMBRE_BASE), read_only=True)
    motor = MotorConsultas(con, catalogo, ajustes.max_rows_per_query)
    guardrails = Guardrails(catalogo, protocolo)
    herramientas = Herramientas(motor, catalogo)
    if modelo is None and ajustes.modelo_configurado:
        modelo = ModeloGemini(ajustes)
    conversador = Conversador(modelo, herramientas, catalogo, guardrails, ajustes) if modelo else None
    sesiones = AlmacenSesiones()
    limitador = LimitadorUso(ajustes.rate_limit_per_minute, ajustes.max_messages_per_session)

    app = FastAPI(title="Asistente de Datos Abiertos NL (prototipo)", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=list(ajustes.allowed_origins),
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def cabeceras_seguridad(request: Request, call_next):
        respuesta = await call_next(request)
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["Referrer-Policy"] = "no-referrer"
        return respuesta

    @app.exception_handler(RequestValidationError)
    async def peticion_invalida(request: Request, exc):
        return _json("No entendí la petición. Escribe tu pregunta e intenta de nuevo.", 422)

    @app.get("/api/salud")
    def salud():
        return {"estado": "ok", "modelo": ajustes.gemini_model, "modelo_configurado": conversador is not None,
                "datasets": [{"id": d["id"], "nombre": d["nombre"], "periodo": d.get("periodo", {}).get("texto")}
                             for d in catalogo.datasets.values()],
                "proximamente": [c["nombre"] for c in catalogo.proximamente()],
                # Línea de apoyo y teléfonos del protocolo de crisis (la página no los escribe a mano).
                "apoyo": {"texto": protocolo.linea_apoyo, "contactos": protocolo.contactos}}

    def con_apoyo(sesion, texto: str) -> JSONResponse:
        """Después de una crisis, toda respuesta termina con la línea de apoyo."""
        if sesion.en_crisis:
            return _json(protocolo.con_linea_apoyo(texto), contactos=protocolo.contactos)
        return _json(texto)

    @app.post("/api/chat")
    def chat(peticion: Peticion, request: Request):
        mensaje = (peticion.mensaje or "").strip()
        id_sesion = peticion.sesion or ""
        if not SESION_VALIDA.match(id_sesion):
            return _json("La sesión no es válida. Recarga la página e intenta de nuevo.", 400)
        # Protocolo de crisis: antes que todo (incluso antes de los límites de uso) y sin llamar al modelo.
        # El mensaje no se envía a Gemini, no entra al historial y la bitácora no guarda su texto.
        apoyo = guardrails.revisar_crisis(mensaje)
        if apoyo:
            sesion = sesiones.obtener(id_sesion)
            if apoyo.tipo in ("crisis", "tercero"):
                # Sin el candado de la sesión: si otra pregunta está en curso, esta respuesta no debe esperar.
                sesion.en_crisis = True
            bitacora.registrar_apoyo(apoyo.tipo, apoyo.regla, id_sesion)
            return _json(apoyo.respuesta, tipo="apoyo", contactos=protocolo.contactos)
        if len(mensaje) > ajustes.max_message_length:
            return _json(f"Tu pregunta es muy larga. Escríbela en menos de {ajustes.max_message_length} caracteres.", 422)
        ip = request.client.host if request.client else "desconocida"
        limite = limitador.permitir(id_sesion, ip)
        if limite == "limite_sesion":
            return _json(f"Llegaste al límite de {ajustes.max_messages_per_session} preguntas en esta conversación. "
                         "Recarga la página para empezar una nueva.", 429)
        if limite == "rate_limit":
            return _json("Hiciste muchas preguntas en poco tiempo. Espera un minuto e inténtalo de nuevo.", 429)

        sesion = sesiones.obtener(id_sesion)
        with sesion.candado:
            rechazo = guardrails.revisar(mensaje, hay_historial=bool(sesion.historial))
            if rechazo:
                if rechazo.tipo not in ("saludo", "agradecimiento"):
                    bitacora.registrar_rechazo(rechazo.tipo, rechazo.regla, mensaje, id_sesion)
                elif sesion.en_crisis:
                    return _json(protocolo.mensaje("despues_de_crisis"), tipo="apoyo", contactos=protocolo.contactos)
                if rechazo.contactos:
                    return _json(rechazo.respuesta, contactos=protocolo.contactos)
                return con_apoyo(sesion, rechazo.respuesta)
            if conversador is None:
                return _json("El asistente no está configurado: falta GEMINI_API_KEY en el archivo .env.", 503)
            try:
                salida = conversador.responder(mensaje, sesion, id_sesion)
            except Exception as e:  # error del modelo o de red: se registra sin datos personales
                bitacora.registrar_error("chat", e, id_sesion)
                if getattr(e, "code", None) == 429:
                    return _json("Se alcanzó el límite de uso del servicio de Gemini. Espera un minuto e inténtalo de nuevo.", 503)
                return _json("El servicio del asistente no respondió. Intenta de nuevo en un momento.", 503)
            if salida.tipo != "respuesta":
                bitacora.registrar_rechazo(salida.tipo, "modelo", mensaje, id_sesion)
            return JSONResponse(salida.como_dict())

    # La página demo y los recursos se sirven desde el mismo origen (sin problemas de CORS).
    if servir_frontend:
        if config.ASSETS_DIR.exists():
            app.mount("/assets", StaticFiles(directory=config.ASSETS_DIR), name="assets")
        app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
    return app


def main():
    import uvicorn
    ajustes = config.cargar_ajustes()
    if ingest.necesita_reingesta():
        print("Preparando los datos (ingesta)…", flush=True)
        catalogo = ingest.ingerir()
        for d in catalogo["datasets"]:
            print(f"  ✔ {d['nombre']}: {d['filas']:,} filas, {d.get('periodo', {}).get('texto', '')}", flush=True)
    if not ajustes.modelo_configurado:
        print("⚠ Falta GEMINI_API_KEY en .env: el chat responderá que no está configurado.", flush=True)
    print(f"Modelo: {ajustes.gemini_model}", flush=True)
    try:
        app = crear_app(ajustes)
    except ErrorProtocolo as e:
        print(f"✘ Protocolo de crisis: {e} El servidor no arranca sin él.", file=sys.stderr)
        sys.exit(1)
    print(f"Abre http://localhost:{ajustes.port}/ en tu navegador (Ctrl+C para detener).", flush=True)
    uvicorn.run(app, host=ajustes.host, port=ajustes.port, log_level="warning")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
