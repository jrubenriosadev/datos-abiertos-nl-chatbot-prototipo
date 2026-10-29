# Guía de archivos: qué va en cada lugar

Esta guía te dice qué debes agregar tú antes de pegar el prompt en Claude Code.

## 1. Datos (obligatorio)

**Dónde:** `data/educacion/`, `data/salud/`, `data/seguridad/`

**Qué poner:** los archivos CSV descargados del portal de datos abiertos de Nuevo León.

**Cómo hacerlo:**
1. Entra al portal de datos abiertos del Gobierno del Estado de Nuevo León y abre la categoría (Educación, Salud o Seguridad).
2. Elige **1 o 2 conjuntos de datos por categoría** para empezar. Es mejor pocos y bien entendidos que muchos a medias.
3. Descarga el CSV. Si el portal ofrece un diccionario de datos o una descripción de columnas, descárgalo también.
4. Guarda todo en la carpeta de la categoría con nombres claros, sin espacios ni acentos. Ejemplo: `escuelas_por_municipio_2024.csv`.
5. Abre el `_FUENTES.md` de esa carpeta y llena una entrada por cada archivo (enlace, fecha de descarga, qué contiene, dudas). Claude Code usará esa información para construir el catálogo.

6. Cada conjunto de datos va en su propia subcarpeta (`data/<categoria>/<NombreDelConjunto>/`) con sus CSV, su `_FUENTES.md` y un `dataset.toml` (columnas, reglas de interpretación y privacidad). El `README.md` explica cómo escribirlo, tomando como modelo el de Salud.

**Importante:** no edites los CSV a mano. Si tienen errores o columnas confusas, anótalo en `_FUENTES.md` y Claude Code lo reportará.

## 2. Clave de Gemini (obligatorio)

**Dónde:** archivo `.env` en la raíz del proyecto (cópialo desde `.env.example`).

**Qué poner:**
- `GEMINI_API_KEY`: tu clave, generada en Google AI Studio.
- `GEMINI_MODEL`: el nombre del modelo. El valor de ejemplo es `gemini-3.1-flash-lite`. Claude Code verificará en la documentación de Google que el nombre vigente sea correcto.

**Nunca** pegues la clave en `index.html`, en el JavaScript ni en ningún `.md`.

## 3. Imágenes y personaje (opcional)

**Dónde:** `assets/`

| Archivo | Para qué | Formato recomendado |
|---|---|---|
| `mascota.png` o `mascota.svg` | Personaje del botón y del encabezado del chat | PNG con fondo transparente o SVG |
| `reference/` | Capturas de pantalla del portal, solo como referencia visual para ti | Cualquiera |

Cuando tengas el personaje, guárdalo en `assets/` y descomenta la línea `avatarUrl` en la configuración al final de `frontend/index.html`. No hace falta tocar nada más.

Por ahora el widget usa un ícono simple de chat, así que puedes avanzar sin el personaje.

## 4. Página demo (ya incluida)

**Dónde:** `frontend/index.html`

Ábrela con doble clic en el navegador para ver el diseño. Qué es y qué no es:
- Es una maqueta con **diseño propio**, pensada para mostrar el chat. No copia el diseño, el escudo ni la marca del portal oficial.
- Tiene un aviso permanente de "Prototipo de propuesta". No lo quites mientras sea una maqueta.
- Los botones de ejemplo y el cuadro de pregunta sí funcionan: abren el chat y envían la pregunta.
- Los colores y textos se cambian en el bloque `<style>` y el HTML de ese mismo archivo.

## 5. Backend (lo crea Claude Code)

**Dónde:** `backend/`

No tienes que agregar nada. El prompt le pide a Claude Code que lo construya y que lea `data/` y `.env`.

## Lista rápida antes de pegar el prompt

- [ ] Al menos un CSV en cada carpeta de `data/`
- [ ] `_FUENTES.md` llenado en cada carpeta
- [ ] `.env` creado con tu `GEMINI_API_KEY`
- [ ] Terminal abierta dentro de la carpeta del proyecto
- [ ] (Opcional) personaje en `assets/`
