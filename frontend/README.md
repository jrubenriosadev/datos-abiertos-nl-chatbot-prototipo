# Frontend

Página demo y widget de chat. Todo es HTML, CSS y JavaScript puro.

| Archivo | Qué es |
|---|---|
| `index.html` | Página demo con diseño propio y aviso de prototipo |
| `chat-widget.css` | Estilos del botón flotante y la ventana (prefijo `.dchat-`) |
| `chat-widget.js` | Lógica del widget. Solo manda preguntas al backend y pinta respuestas |

## Cómo verlo

Con el backend encendido (`python backend/main.py` desde la raíz), abre **http://localhost:8000/**. El backend sirve esta página y el API en la misma dirección, así que no hay problemas de CORS.

- **Doble clic en `index.html` no funciona para chatear:** la página abre con origen `null` y el navegador bloquea la conexión al backend (a propósito, `null` nunca se permite). Solo sirve para ver el diseño.
- **Otro servidor estático** (por ejemplo `python -m http.server 5500 -d frontend`): agrega `http://localhost:5500` a `ALLOWED_ORIGINS` en `.env` y cambia `apiUrl` a `http://localhost:8000/api/chat`.

## Configuración del widget

Se define antes de cargar el script, en `index.html`:

```html
<script>
  window.DatosChatConfig = {
    apiUrl: "/api/chat",   // o la dirección completa del backend si la página está en otro origen
    // avatarUrl: "../assets/mascota.png",
    // title, subtitle, welcome, examples, placeholder, maxLength, timeoutMs
  };
</script>
<script src="chat-widget.js" defer></script>
```

Para integrarlo en otra página basta con cargar `chat-widget.css`, definir `DatosChatConfig` y cargar `chat-widget.js`.

## Contrato con el backend

**Petición** `POST {apiUrl}`

```json
{ "mensaje": "¿Cuántas consultas hubo cada mes?", "sesion": "uuid-generado-en-el-navegador" }
```

**Respuesta** (HTTP 200)

```json
{
  "respuesta": "Texto en español sencillo. Los párrafos se separan con una línea en blanco; las listas usan '- '.",
  "grafica": {
    "titulo": "Consultas por mes",
    "tipo": "barras",
    "unidad": "consultas",
    "etiquetas": ["Mayo de 2026", "Junio de 2026"],
    "valores": [89662, 94869],
    "marcadas": [0],
    "nota": "Barras marcadas: mayo de 2026 (parcial). No representan meses completos."
  },
  "fuente": "Consulta General y Especialidades (Secretaría de Salud de Nuevo León), del 1 de octubre de 2024 al 30 de septiembre de 2026 (sin datos de septiembre de 2025)",
  "avisos": ["Cada registro es una consulta, no una persona: una misma persona puede tener varias consultas."]
}
```

- `grafica` y `fuente` pueden ser `null`; `avisos` puede ser una lista vacía o no venir.
- `grafica.tipo`: `barras` (verticales, hasta 8 categorías; con más se dibujan horizontales), `horizontales`, `agrupadas` o `apiladas`. Si falta, se dibujan horizontales.
- Varias series (agrupadas o apiladas): en lugar de `valores` llega `"series": [{"nombre": "Femenino", "valores": [...]}, ...]` (máximo 8). Un valor `null` se muestra como "—".
- `marcadas`: índices de barras con datos incompletos o sin datos; se dibujan con trama y un asterisco, y se explican en `nota`. Un mes sin datos llega con valor `null` (barra vacía con "—"), para que el hueco se vea.
- El texto de `fuente` y el periodo de la página demo los arma el backend con lo calculado en la ingesta; no hay periodos escritos a mano en el frontend.
- `avisos`: aclaraciones que agrega el backend a partir de los datos (meses incompletos, privacidad, consultas ≠ personas). El widget las muestra en el recuadro "Ten en cuenta".
- `tabla` (opcional): si se esperaba una gráfica pero no se pudo crear, llega `"tabla": {"titulo", "columnas": [...], "filas": [[...], ...], "nota"}` con los mismos datos, y el texto lo avisa en una frase. El widget la dibuja como tabla.
- Las etiquetas de las gráficas y de las tablas son siempre nombres legibles ("Hipertensión esencial primaria"), nunca claves.
- `tipo` (opcional): `"apoyo"` cuando la respuesta es un mensaje fijo del protocolo de crisis. El widget le da un estilo propio (`dchat-msg--apoyo`).
- `contactos` (opcional): `[{"nombre", "telefono", "detalle"}]` cuando la respuesta incluye líneas de apoyo. El widget los muestra como enlaces `tel:` (solo con los dígitos). Los textos y teléfonos salen de `config/crisis.toml`; el frontend no los escribe a mano (la página demo toma la línea de apoyo de `GET /api/salud`, campo `apoyo`).
- **Marcadores internos:** el texto nunca debe traer marcadores (`[[…]]`, `{{…}}`). El backend los quita y el widget los vuelve a quitar como segunda defensa.

## Tamaño de la ventana

- En computadora, la ventana mide hasta 560 × 780 px, sin salirse de la pantalla. El botón del encabezado (junto a cerrar) la amplía hasta 1000 px de ancho y casi toda la altura, y vuelve a reducirla; tiene `aria-pressed` y una etiqueta que cambia ("Ampliar la ventana" / "Reducir la ventana").
- En celular (480 px o menos), la ventana ocupa toda la pantalla y el botón de ampliar se oculta.
- Las etiquetas largas de las gráficas se parten en varias líneas en lugar de cortarse con "…".
- Todos los valores de la gráfica los pone el backend con los datos consultados; el modelo nunca los escribe.
- En errores (límite de mensajes, mensaje muy largo, servicio caído), el backend responde con otro código HTTP (400, 422, 429 o 503) y un JSON con `respuesta`. El widget muestra ese texto.
- Las gráficas son barras hechas con HTML y CSS, con una tabla accesible debajo; no usan librerías.

## API pública del widget

```js
window.DatosChat.open();      // abre la ventana
window.DatosChat.close();     // la cierra
window.DatosChat.ask("...");  // abre la ventana y envía una pregunta
```

## Seguridad

El texto que llega del servidor se muestra siempre con `textContent`, nunca con `innerHTML`. No cambies eso: evita que un dato o una respuesta inyecten código en la página.
