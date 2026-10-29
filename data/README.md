# Datos

Aquí van los CSV descargados del portal de datos abiertos de Nuevo León, uno por categoría:

- `educacion/`
- `salud/`
- `seguridad/`

Reglas:
1. Empieza con 1 o 2 conjuntos de datos por categoría.
2. Nombres de archivo claros, sin espacios ni acentos, con el año si aplica (`unidades_medicas_2024.csv`).
3. No edites los CSV a mano. Si tienen errores, anótalos en el `_FUENTES.md` de la carpeta.
4. Llena el `_FUENTES.md` de cada carpeta: de ahí sale el catálogo que usa el asistente.
5. Solo datos públicos. Si algún archivo trae información personal, no lo uses.

La base de datos local que genera `ingest.py` también se guarda fuera de Git (ver `.gitignore`).
