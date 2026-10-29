# Reglas del proyecto (Claude Code las lee automáticamente)

Prototipo local de un chatbot que consulta datos abiertos de Nuevo León. Primera versión: solo Salud ("Consulta General y Especialidades", "Salud Bucal" y "Salud Mental"). Educación y Seguridad quedan como "próximamente". Las reglas comunes de Salud viven en `data/salud/_comun.toml`; cada respuesta usa un solo dataset (aún no hay cruces).

## Alcance del chatbot
- Solo sirve para buscar, consultar, interpretar y graficar los datos de `data/`. Para cualquier otra cosa se niega con un mensaje fijo.
- Toda cifra mostrada debe venir de un resultado de herramienta (consulta a la base local). Nunca de la memoria del modelo.
- Cada respuesta cita el conjunto de datos y el periodo real cubierto.
- Sin consejos médicos: no diagnostica, no recomienda tratamientos ni interpreta síntomas.

## Datos y privacidad
- Cada fila de Salud es una consulta, no una persona. Se habla de "consultas".
- Nunca se devuelven filas individuales ni `id_consulta`. Solo agregados; los grupos con menos de 5 consultas se ocultan o se agrupan.
- La cobertura (periodo, meses incompletos) se calcula de los datos en la ingesta, no se escribe a mano, y se informa siempre.
- Sin población no hay tasas: al comparar municipios se advierte y se prefieren proporciones internas.
- El diseño es dataset-agnóstico: las columnas y las reglas de cada dataset viven en su catálogo y configuración, no en el código genérico.

## Técnica
- Backend: Python + FastAPI. Frontend: HTML, CSS y JavaScript sin frameworks.
- Modelo de Gemini configurable con `GEMINI_MODEL` en `.env`. Verifica el nombre vigente en la documentación oficial de Google.
- El modelo usa function calling con consultas estructuradas. No escribe SQL ni HTML ni JavaScript.
- Gráficas: solo barras. Los valores los pone el backend, no el modelo.
- Todo corre en localhost. La API key vive solo en `.env`: nunca en el frontend ni en archivos versionados, y nunca se imprime ni se copia.

## Forma de trabajar
- Planea primero y espera aprobación antes de escribir código.
- Al terminar cada fase, corre las pruebas y reporta el resultado.
- Código comentado en español. Respuestas del bot solo en español, en lenguaje sencillo para cualquier persona.

## Frontend
- `frontend/index.html` es una maqueta con diseño propio. No copies el escudo, el logo ni la marca del portal oficial, y mantén visible el aviso de "Prototipo de propuesta".
- El widget (`chat-widget.js` y `chat-widget.css`) debe seguir siendo independiente y embebible.

