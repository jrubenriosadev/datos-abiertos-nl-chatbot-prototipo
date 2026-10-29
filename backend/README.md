# Backend

Servidor local (FastAPI), ingesta de datos y herramientas del asistente. Las instrucciones para correrlo están en el `README.md` de la raíz.

```
backend/
├── main.py          ← servidor: POST /api/chat, GET /api/salud y la página demo; reingesta si hace falta
├── config.py        ← rutas y lectura de .env (la clave nunca se imprime)
├── ingest.py        ← data/ → build/datos.duckdb + build/catalog.json + build/reporte_ingesta.md
├── normalizar.py    ← normalización de textos (regla 9) y diccionarios verificados
├── catalogo.py      ← catálogo: buscar_dataset y describir_dataset
├── consultas.py     ← consultar_datos y buscar_valores: validación, SQL parametrizado, privacidad y advertencias
├── graficas.py      ← crear_grafica: JSON de barras con los valores reales de un resultado
├── herramientas.py  ← declaración de las herramientas para Gemini, ejecución y estado de cada sesión
├── guardrails.py    ← filtro previo, mensajes fijos, verificador de cifras, lenguaje sin estigma y límites de uso
├── crisis.py        ← protocolo de crisis: detección antes del modelo (textos y teléfonos en config/crisis.toml)
├── prompts.py       ← system prompt
├── llm.py           ← ciclo de function calling con Gemini (SDK google-genai)
├── bitacora.py      ← logs/ en JSONL, sin datos personales
├── requirements.txt
└── tests/           ← pytest; oraculo.py recalcula las cifras leyendo los CSV con el módulo csv
```

## Dependencias (justificación)

| Paquete | Para qué |
|---|---|
| `fastapi`, `uvicorn` | Servidor web local y endpoint `/api/chat` |
| `duckdb` | Base columnar local: agrega cientos de miles de filas en milisegundos, lee CSV en latin-1, tiene `median` y consultas parametrizadas |
| `google-genai` | SDK oficial de Google para Gemini con function calling |
| `python-dotenv` | Lee `.env` |
| `pytest`, `httpx` | Pruebas (`httpx` lo usa el cliente de pruebas de FastAPI) |

No se usa pandas. La configuración de los datasets está en TOML, que Python lee sin dependencias (`tomllib`).

## Ingesta sola

```bash
python backend/ingest.py              # reconstruye todo
python backend/ingest.py --si-cambio  # solo si cambiaron datos, configuración o código de ingesta
```
