# Prototipo: asistente de Datos Abiertos Nuevo León

Chatbot local que permite consultar, entender e interpretar datos abiertos de Nuevo León haciendo una pregunta en español, sin descargar CSV ni usar filtros. Responde con cifras verificadas, una explicación sencilla, aclaraciones sobre los límites de los datos y gráficas de barras.

- **Datos disponibles:** Salud, "Consulta General y Especialidades". El periodo se calcula de los propios datos en cada ingesta.
- **Próximamente:** Educación y Seguridad. Si alguien pregunta por ellas, el asistente responde que todavía no están disponibles.
- **Solo sirve para estos datos.** Para cualquier otra cosa responde con un mensaje fijo.
- Corre solo en tu computadora (localhost). Es un prototipo de propuesta.

## Requisitos

- Python 3.12 o más reciente (probado con 3.14.7).
- Una API key de Gemini (Google AI Studio).
- Opcional: Node.js, solo para la prueba automática del widget.

## Puesta en marcha (una sola vez)

Abre una terminal en la carpeta del proyecto.

1. Crea el entorno virtual e instala las dependencias:

   ```bash
   python -m venv .venv
   # Windows (PowerShell):  .venv\Scripts\Activate.ps1
   # Windows (Git Bash):    source .venv/Scripts/activate
   # macOS / Linux:         source .venv/bin/activate
   pip install -r backend/requirements.txt
   ```

2. Copia `.env.example` como `.env` y pon tu clave en `GEMINI_API_KEY`. La clave solo vive ahí; nunca la pegues en el frontend ni en otro archivo.

3. Confirma que los CSV estén en `data/salud/ConsultaGeneralYEspecialidades/`.

## Arrancar (un solo comando)

```powershell
.venv\Scripts\python backend\main.py      # Windows (no hace falta activar el entorno)
```
```bash
.venv/bin/python backend/main.py          # macOS / Linux
```

Si activaste el entorno virtual (paso 1), basta con `python backend/main.py`. Si ves `ModuleNotFoundError: No module named 'duckdb'`, el comando se ejecutó con el Python del sistema y no con el de `.venv`: usa la forma de arriba.

Ese comando revisa si los datos o la configuración cambiaron. Si cambiaron, reconstruye la base local en unos segundos y luego levanta el servidor. Abre **http://localhost:8000/** en el navegador. El botón de abajo a la derecha abre el chat. Para detener el servidor, usa Ctrl+C.

**¿Por qué no basta con abrir `index.html` con doble clic?** Así la página tiene origen `null` y el navegador bloquea la conexión con el backend. Además, `null` nunca se permite en CORS, a propósito. El backend sirve la página y el API en la misma dirección, así que no hace falta configurar nada. Si prefieres otro servidor estático, consulta `frontend/README.md`.

## Pruebas

```bash
pytest                 # 400 pruebas sin red: ingesta, consultas, privacidad, elección de dataset, guardrails, API y widget (~1 min)
pytest -m live         # preguntas reales a Gemini (gasta cuota, ~10 min)
```

- **Todas las pruebas** comparan las cifras con un **oráculo independiente**, que recalcula todo leyendo los CSV con el módulo `csv` de Python, sin DuckDB.
- **`pytest -m live`** hace 12 preguntas con cifras y además prueba los casos que deben rechazarse o aclararse: "el año pasado", "enfermos", comparar municipios, datos de una persona, consejo médico, fuera de alcance, "próximamente" y datos que no existen. Al terminar escribe `build/reporte_en_vivo.md` con cada pregunta y su respuesta.
- **Las respuestas del modelo varían entre ejecuciones.** Las pruebas en vivo revisan lo esencial: las cifras clave, las advertencias y los rechazos.

## Cómo funciona

```
Pregunta ──► Filtro previo ──(fuera de alcance / próximamente / consejo médico / privacidad)──► mensaje fijo
                 │
                 ▼
             Gemini (function calling) ◄──► herramientas del backend ◄──► DuckDB (datos agregados)
                 │
                 ▼
             Revisión: marcadores, cifras verificadas, longitud ──► { respuesta, grafica, fuente, avisos }
```

1. **Ingesta** (`backend/ingest.py`):
   - Recorre `data/`; cada subcarpeta con CSV es un dataset.
   - Une las partes y revisa que la cabecera sea la misma; si no coincide, el archivo se excluye y se reporta.
   - Detecta la codificación (UTF-8 o latin-1), convierte fechas y números, normaliza textos y calcula el periodo y los meses incompletos.
   - Genera `build/datos.duckdb`, `build/catalog.json` y `build/reporte_ingesta.md` (lo que no cuadra en los datos).
   - El identificador de cada consulta no entra a la base.
2. **Herramientas que usa el modelo** (`backend/herramientas.py`):
   - `buscar_dataset`, `describir_dataset` y `buscar_valores` exploran el catálogo.
   - `consultar_datos` hace consultas estructuradas; el modelo nunca escribe SQL.
   - `crear_grafica` arma la gráfica; los valores los pone el backend.
3. **Reglas de interpretación**: las comunes de una categoría viven en `data/<categoría>/_comun.toml` (por ejemplo, `data/salud/_comun.toml`) y las propias de cada dataset en su `dataset.toml`. La ingesta las une y se entregan al modelo como contexto, no como código.
4. **Un dataset por respuesta**: `buscar_dataset` recomienda el más específico (primero cuentan las palabras escritas a mano en `nombre`, `descripcion` y `palabras_clave`; luego el vocabulario sacado de los datos; al final, `prioridad`). Una respuesta no puede consultar un segundo dataset y el verificador solo acepta cifras del dataset que usó: todavía no hay cruces entre datasets.
5. **Respuesta**: `fuente` y `avisos` los arma el backend a partir de las consultas, así que aparecen aunque el modelo los olvide.

## Restricción de uso (defensa en capas; no hay garantía al 100%)

- **Protocolo de crisis, antes que todo** (`backend/crisis.py`, textos y teléfonos en `config/crisis.toml`): si un mensaje expresa malestar propio, desesperanza o ideas de hacerse daño o de morir (también con faltas de ortografía o de forma coloquial), se responde con un mensaje fijo y cálido con la Línea de la Vida y el 911, sin llamar al modelo, aunque se haya llegado al límite de uso. Hay mensajes fijos aparte para la preocupación por otra persona y para quien pide terapia o consejo. El mensaje no se envía al modelo ni se guarda en la bitácora. Después de una crisis, y en toda respuesta sobre autolesión o suicidio, el backend agrega una línea de apoyo fija. Sin `config/crisis.toml` el servidor no arranca.
- **Lenguaje sin estigma:** si la respuesta usa frases de `lenguaje_evitar` (por ejemplo, "enfermos mentales"), se pide reescribirla una vez; si insiste, no se muestra.
- **Filtro previo sin modelo:** saludos, intentos de cambiar o ver las instrucciones, temas ajenos (recetas, código, tareas, política…), consejo médico en primera persona, datos de una persona, temas "próximamente" y estadísticas de columnas no disponibles (peso, altura).
- **System prompt estricto** (`backend/prompts.py`): el modelo responde con marcadores que el backend cambia por mensajes fijos. Lo que devuelven las herramientas se trata como dato, nunca como instrucción.
- **Verificador de cifras:** cada número de la respuesta debe salir de un resultado de herramienta. Si no, se pide una corrección una vez; si vuelve a fallar, la respuesta no se muestra.
- **Privacidad:**
  - Solo se entregan agregados, y los grupos con menos de 5 consultas se juntan en "Otros (grupos pequeños)" o se ocultan.
  - Un dataset puede exigir un mínimo mayor para ciertos cruces (`[[privacidad.combinaciones]]`): en Salud Bucal, al cruzar municipio con edad, el mínimo es 20 consultas, para no acotar a menores en municipios pequeños.
  - Se oculta el total cuando permitiría deducir un grupo pequeño.
  - Se rechazan combinaciones de filtros demasiado precisas (por ejemplo, sexo + edad exacta + municipio + fecha).
  - Un dataset puede subir el mínimo (`minimo_por_grupo`), bloquear cruces completos (`[[privacidad.bloqueos]]`), ocultar los valores poco frecuentes (`ocultar_valores_poco_frecuentes`) o ciertos valores (`[[valores_no_disponibles]]`) y borrar de la base columnas que solo sirven para los controles (`guardar = false`).
  - **Temas restringidos** (`[[temas_restringidos]]`): sus valores solo se cruzan con las columnas permitidas (por ejemplo, autolesión solo en total, por sexo o por edad; nunca por municipio ni por mes). El de autolesión es común a todo Salud.
  - No se puede agrupar por día ni por edad exacta.
- **Límites en `.env`:** longitud del mensaje, mensajes por sesión, peticiones por minuto (por sesión y por IP), tokens de salida, llamadas a herramientas por turno y filas por consulta (máximo 200).
- **Bitácora local** en `logs/`: preguntas rechazadas y errores, con el texto redactado (sin correos, teléfonos, CURP ni números largos), la sesión como hash y sin IP.

**Riesgo residual:** combinando varias consultas válidas se podrían restar totales para deducir un grupo pequeño. Las medidas anteriores lo dificultan, pero no lo eliminan.

## Agregar un dataset nuevo (sin tocar código)

1. Crea `data/<categoria>/<NombreDelConjunto>/` y pon ahí los CSV (pueden ser varias partes con la misma cabecera).
2. Agrega un `_FUENTES.md` (enlace, institución, periodo según el portal, dudas).
3. Agrega un `dataset.toml`. Usa como modelo el de `data/salud/SaludBucal/` y define solo lo que no esté ya en el `_comun.toml` de su categoría:
   - `id`, `categoria` (de `data/categorias.toml`), nombre, descripción, fuente, `palabras_clave` (las que lo distinguen de los otros datasets) y, si hace falta, `prioridad` para desempatar preguntas generales;
   - `[fecha]` (columna y formato), `[identificador]` (se usa para revisar duplicados y no entra a la base), `[privacidad]` y `[completitud]`;
   - `[[columnas]]`: tipo (`categoria`, `codigo` o `numero`), si va expuesta al modelo, normalización, diccionarios y sinónimos;
   - `advertencias_condicionales`, `valores_especiales`, `datos_no_disponibles` y `reglas_interpretacion`, que son el contexto para el modelo.
4. Reinicia con `python backend/main.py`: la reingesta es automática. Revisa `build/reporte_ingesta.md`.

**Ojo con TOML:** las claves sueltas (`siglas`, `reglas_interpretacion`…) van **antes** de la primera sección `[...]` o `[[...]]`. La ingesta rechaza columnas con claves desconocidas para detectar este error.

Para agregar más partes de un dataset que ya existe, basta con copiar los CSV en su carpeta: el periodo, los meses incompletos y las cifras se actualizan solos al reiniciar.

## Lo que hay que saber de los datos de Salud

- **Cada fila es una consulta, no una persona.** No hay identificador de persona.
- **No hay población**, así que no se pueden calcular tasas.
- **Periodo:** lo calcula la ingesta y se ve en `/api/salud`, en la página demo y en cada respuesta. Con los archivos actuales (2024-2025 y 2025-2026) va de octubre de 2024 a septiembre de 2026, **con un hueco: septiembre de 2025 no viene en los archivos**. El asistente lo dice como "sin datos" (no como cero consultas) y lo muestra como una barra vacía en las gráficas por mes. Los meses que terminan una publicación (antes del hueco y los más recientes) se marcan como posible rezago de captura.
- **Dos publicaciones con diferencias:** tres columnas cambian de nombre, las unidades médicas se escriben distinto (`C.S.U. X` contra `C S U  X`) y algunas descripciones cambian de palabras ("tipo dos" contra "tipo 2"). Todo se unifica desde `dataset.toml`, sin código específico.
- **Los identificadores de consulta se reinician entre periodos** (`CE_2025_…` existe en ambos): no son duplicados. La ingesta solo elimina filas idénticas en todas las columnas.
- **Peso y altura** quedaron fuera de las herramientas por su baja calidad.
- **Detalles de los datos:**
  - Falta el municipio de Los Aldamas.
  - Algunos nombres de unidad médica se repiten en dos municipios; por eso cada unidad se muestra con su municipio.
  - Las claves son de la CIE-10, según el diccionario oficial incluido en la carpeta.
- **Detalle completo:** `build/reporte_ingesta.md`. El resumen está en `data/salud/ConsultaGeneralYEspecialidades/_FUENTES.md`.

### Salud Bucal

- **Mismo periodo y mismo hueco** (sin septiembre de 2025) y las mismas tres columnas con otro nombre en 2024-2025.
- **Las enfermedades de boca y dientes están en el grupo XI de la CIE-10** ("Enfermedades del sistema digestivo"); el asistente lo explica. El grupo XXI son exámenes odontológicos y prótesis, no enfermedades.
- **Una visita puede generar varios registros** (por ejemplo, caries y examen el mismo día): se habla de consultas registradas.
- **La unidad médica no se ofrece:** cada publicación la escribe distinto (`CSU X` contra `C S U  X`) y en centros pequeños podría identificar a menores.
- **Muchas consultas son de menores:** al cruzar municipio con edad, el mínimo de privacidad sube a 20.
- **No se suma ni se compara con Consulta General**, aunque esta también tenga diagnósticos de boca y dientes: son registros de otro servicio. Resumen en `data/salud/SaludBucal/_FUENTES.md`.

### Salud Mental

- **Mismo periodo y mismo hueco** (sin septiembre de 2025). **Septiembre de 2026 está incompleto por municipio** (Monterrey y otros municipios casi no reportan): lo detecta la regla `[completitud] por_columna`.
- **Son atenciones registradas, no prevalencia:** muchas personas no buscan atención. Una misma persona suele tener muchas consultas de seguimiento.
- **El municipio muestra dónde están los servicios:** General Escobedo concentra más consultas porque ahí está el Hospital de Especialidades en Salud Mental, que atiende a personas de todo el estado. Solo 16 municipios tienen unidades en estos datos.
- **Privacidad reforzada:** mínimo de 10 consultas por grupo (20 al cruzar municipio con edad); se rechazan los cruces de lugar + diagnóstico + datos de la persona, de lugar + diagnóstico + fecha y de edad + sexo + lugar + fecha; los diagnósticos con menos de 10 consultas y los de identidad de género y sexualidad no se consultan por separado. Peso, altura y unidad médica se borran de la base: juntos con la edad y el lugar funcionan como una huella para seguir a una persona.
- **Autolesión y suicidio:** no hay muertes ni claves de lesión autoinfligida intencional (X60 a X84); solo consultas con antecedente de autolesión (Z915) y con otros síntomas del estado emocional (R458), que se quedan muy por debajo de la realidad. Solo totales, por sexo o por edad, y cada respuesta termina con la línea de apoyo. Lo mismo aplica a violencia y maltrato (sin línea de apoyo).
- **No se suma ni se compara con Consulta General**, aunque esta también tenga diagnósticos de salud mental: son registros de otro servicio (solo 349 filas coinciden). Resumen en `data/salud/SaludMental/_FUENTES.md`.

## Estructura

```
datos-abiertos-chatbot/
├── README.md                 ← este archivo
├── CLAUDE.md                 ← reglas fijas del proyecto
├── .env.example              ← plantilla de configuración (copia como .env)
├── pytest.ini
├── data/
│   ├── categorias.toml       ← categorías y sus palabras clave
│   ├── diccionarios/         ← nombres verificados con acentos (municipios, capítulos CIE-10)
│   ├── salud/_comun.toml     ← reglas comunes de todos los datasets de Salud
│   ├── salud/ConsultaGeneralYEspecialidades/  ← CSV + _FUENTES.md + dataset.toml
│   ├── salud/SaludBucal/     ← CSV + _FUENTES.md + dataset.toml
│   ├── salud/SaludMental/    ← CSV + _FUENTES.md + dataset.toml
│   ├── educacion/            ← próximamente
│   └── seguridad/            ← próximamente
├── config/crisis.toml        ← protocolo de crisis: mensajes fijos, teléfonos y frases de detección
├── backend/                  ← FastAPI, ingesta, herramientas y pruebas (ver backend/README.md)
├── frontend/                 ← página demo y widget embebible (ver frontend/README.md)
├── build/                    ← generado: base DuckDB, catálogo y reportes (fuera de Git)
└── logs/                     ← generado: bitácora (fuera de Git)
```

## Problemas comunes

| Síntoma | Solución |
|---|---|
| `ModuleNotFoundError: No module named 'duckdb'` | Se usó el Python del sistema. Ejecuta `.venv\Scripts\python backend\main.py` (o activa `.venv` primero). |
| PowerShell no deja activar `.venv` ("ejecución de scripts deshabilitada") | Usa `.venv\Scripts\python ...` directamente, o `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| El chat dice "no está configurado" | Falta `GEMINI_API_KEY` en `.env`. |
| "No pude conectarme con el servidor" | ¿Está corriendo `python backend/main.py`? ¿Abriste `http://localhost:8000/` y no el archivo con doble clic? |
| "El servicio del asistente no respondió" | Error de red o de cuota de Gemini. Revisa `logs/errores.jsonl`. |
| Error al reingerir: archivo en uso | Detén el servidor (Ctrl+C) antes de agregar datos y vuelve a arrancarlo. |
| El modelo dejó de existir | Cambia `GEMINI_MODEL` en `.env` por uno vigente según https://ai.google.dev/gemini-api/docs/models. |
| Las respuestas tardan | Es normal: de 10 a 30 segundos, porque el modelo hace varias consultas por pregunta. |
