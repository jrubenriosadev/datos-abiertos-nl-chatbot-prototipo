"""Catálogo de datasets generado por ingest.py: búsqueda y descripción para el modelo.

Lo que se entrega al modelo sale de aquí. No incluye conteos: las cifras siempre
deben salir de consultar_datos, que aplica la privacidad y las advertencias.
"""
import json
import re
from pathlib import Path

import config
from ingest import PALABRAS_VACIAS
from normalizar import texto_busqueda

# Operadores permitidos según el tipo de columna (los valida consultas.py).
OPERADORES_POR_TIPO = {
    "categoria": ["igual", "distinto", "en", "no_en"],
    "codigo": ["igual", "distinto", "en", "no_en"],
    "numero": ["igual", "entre", "mayor_igual", "menor_igual"],
    "fecha": ["entre", "mayor_igual", "menor_igual"],
    "mes": ["igual", "en", "entre"],
    "rango": ["igual", "en", "no_en"],
}


# Palabras de la pregunta que no indican el tema ("¿cuántas consultas POR caries HUBO?"): no cuentan al buscar.
NO_CUENTAN = PALABRAS_VACIAS | {"por", "hubo", "han", "fueron", "cuantas", "cuantos", "cuanto", "cuanta", "mas",
                                "menos", "dame", "quiero", "saber"}


def _palabras(texto: str) -> set[str]:
    return {p for p in re.split(r"[^a-z0-9ñ]+", texto_busqueda(texto)) if len(p) >= 3}


class Catalogo:
    def __init__(self, datos: dict):
        self.datos = datos
        self.datasets: dict[str, dict] = {d["id"]: d for d in datos.get("datasets", [])}
        self.categorias: list[dict] = datos.get("categorias", [])

    @classmethod
    def cargar(cls, build_dir: Path = config.BUILD_DIR) -> "Catalogo":
        ruta = build_dir / config.NOMBRE_CATALOGO
        return cls(json.loads(ruta.read_text(encoding="utf-8")))

    # ---------- Consultas sobre el catálogo ----------

    def obtener(self, ds_id: str) -> dict | None:
        return self.datasets.get(ds_id)

    def columna(self, ds_id: str, alias: str) -> dict | None:
        """Columna expuesta o derivada (fecha, mes, rango_edad) por su alias."""
        ds = self.obtener(ds_id)
        if not ds:
            return None
        for c in ds["columnas"]:
            if c["alias"] == alias and c["expuesta"]:
                return c
        for c in ds.get("derivadas", []):
            if c["alias"] == alias:
                return c
        return None

    def proximamente(self) -> list[dict]:
        return [c for c in self.categorias if c["estado"] == "proximamente"]

    def periodo_texto(self, ds_id: str) -> str:
        ds = self.obtener(ds_id) or {}
        return ds.get("periodo", {}).get("texto", "")

    def fuente_texto(self, ds_id: str) -> str:
        """'Consulta General y Especialidades (institución), del 3 de mayo al 30 de septiembre de 2026'."""
        ds = self.obtener(ds_id)
        if not ds:
            return ""
        texto = ds["nombre"]
        if ds.get("institucion"):
            texto += f" ({ds['institucion']})"
        if ds.get("periodo"):
            texto += f", {ds['periodo']['texto']}"
        return texto

    def meses_marcados(self, ds_id: str) -> list[dict]:
        ds = self.obtener(ds_id) or {}
        return [m for m in ds.get("meses", []) if m["estado"] != "completo"]

    # ---------- Herramientas: buscar_dataset y describir_dataset ----------

    def buscar(self, consulta: str) -> dict:
        """Datasets que coinciden con la consulta y categorías que aún no tienen datos.

        Orden: primero las coincidencias con lo que se escribió a mano para distinguir cada dataset
        (nombre, descripción y palabras clave); luego con el resto (categoría, columnas y el vocabulario
        que la ingesta saca de los datos); al final, la 'prioridad' de cada dataset desempata.
        Así "caries" lleva a Salud Bucal aunque Consulta General también tenga diagnósticos de caries."""
        palabras = _palabras(consulta or "") - NO_CUENTAN
        resultados = []
        for ds in self.datasets.values():
            curado = _palabras(" ".join([ds["nombre"], ds["descripcion"], " ".join(ds.get("palabras_clave", []))]))
            resto = _palabras(" ".join([ds["categoria_nombre"],
                                        " ".join(c.get("descripcion", "") for c in ds["columnas"] if c["expuesta"])]))
            resto |= set(ds.get("vocabulario", []))
            puntos = (len(palabras & curado), len(palabras & (resto - curado)), ds.get("prioridad", 0))
            if puntos[0] or puntos[1] or not palabras:
                resultados.append((puntos, ds))
        resultados.sort(key=lambda r: r[0], reverse=True)
        proximamente = [
            {"categoria": c["nombre"], "estado": "próximamente", "mensaje": f"Los datos de {c['nombre']} todavía no están disponibles."}
            for c in self.proximamente()
            if not palabras or palabras & {p for k in c.get("palabras_clave", []) for p in _palabras(k)}
        ]
        respuesta = {
            "datasets": [{"dataset_id": ds["id"], "nombre": ds["nombre"], "categoria": ds["categoria_nombre"],
                          "descripcion": ds["descripcion"], "periodo": ds.get("periodo", {}).get("texto", "")}
                         for _, ds in resultados],
            "proximamente": proximamente,
        }
        if resultados:
            respuesta["recomendado"] = resultados[0][1]["id"]
            if len(resultados) > 1:
                respuesta["nota"] = ("Usa el conjunto recomendado. Cada respuesta usa un solo conjunto: no sumes ni compares "
                                     "cifras de conjuntos distintos. Si la pregunta es general, menciona en una frase que "
                                     "existen los otros conjuntos.")
        else:
            respuesta["sin_coincidencias"] = True
            respuesta["disponibles"] = [{"dataset_id": d["id"], "nombre": d["nombre"]} for d in self.datasets.values()]
        return respuesta

    def describir(self, ds_id: str) -> dict | None:
        """Vista del dataset para el modelo: columnas, periodo, advertencias y reglas."""
        ds = self.obtener(ds_id)
        if not ds:
            return None
        columnas = []
        for c in ds["columnas"]:
            if not c["expuesta"]:
                continue
            col = {"alias": c["alias"], "tipo": c["tipo"], "descripcion": c.get("descripcion", ""),
                   "operadores": OPERADORES_POR_TIPO[c["tipo"]],
                   "agrupable": c["tipo"] != "numero"}
            if "valores" in c:
                col["valores"] = c["valores"]
            elif c["tipo"] in ("categoria", "codigo"):
                col["n_distintos"] = c.get("n_distintos")
                col["nota"] = "Hay demasiados valores para listarlos: usa buscar_valores para encontrarlos."
            if c["tipo"] == "codigo":
                col["nota_codigo"] = "En los filtros usa la clave; el nombre aparece en los resultados."
            columnas.append(col)
        for d in ds.get("derivadas", []):
            col = {"alias": d["alias"], "tipo": d["tipo"], "descripcion": d["descripcion"],
                   "operadores": OPERADORES_POR_TIPO[d["tipo"]], "agrupable": d["tipo"] != "fecha"}
            if d.get("valores"):
                col["valores"] = d["valores"]
            columnas.append(col)

        agregaciones = ["conteo", "proporcion"]
        agregaciones += [f"{c['alias']}_promedio" for c in ds["columnas"] if c["expuesta"] and c["tipo"] == "numero"]
        agregaciones += [f"{c['alias']}_mediana" for c in ds["columnas"] if c["expuesta"] and c["tipo"] == "numero"]
        agregaciones += [f"conteo_distinto:{c['alias']}" for c in ds["columnas"] if c["expuesta"] and c.get("conteo_distinto")]

        siempre = [a["texto"] for a in ds.get("advertencias_condicionales", []) if a.get("cuando", {}).get("siempre")]
        return {
            "dataset_id": ds["id"],
            "nombre": ds["nombre"],
            "categoria": ds["categoria_nombre"],
            "descripcion": ds["descripcion"],
            "institucion": ds.get("institucion", ""),
            "fuente_url": ds.get("fuente_url", ""),
            "cobertura_geografica": ds.get("cobertura_geografica", ""),
            "unidad": ds["unidad"],
            "periodo_cubierto": ds.get("periodo", {}).get("texto", "sin fechas"),
            "meses": [{"mes": m["mes"], "nombre": m["nombre"], "estado": m["estado"], "nota": m["nota"]}
                      for m in ds.get("meses", [])],
            "advertencias": siempre + [m["nota"] for m in self.meses_marcados(ds_id)],
            "columnas": columnas,
            "agregaciones": agregaciones,
            "columnas_no_disponibles": [{"columna": c["alias"], "motivo": c.get("motivo", "")}
                                        for c in ds["columnas"] if not c["expuesta"]],
            "valores_especiales": [{"columna": v["columna"], "valores": v["valores"], "explicacion": v["explicacion"]}
                                   for v in ds.get("valores_especiales", [])],
            "datos_no_disponibles": ds.get("datos_no_disponibles", []),
            "reglas_interpretacion": ds.get("reglas_interpretacion", []),
            "privacidad": {
                "minimo_por_grupo": ds.get("privacidad", {}).get("minimo_por_grupo", 5),
                # Combinaciones de columnas con un mínimo mayor (por ejemplo, lugar y edad).
                "combinaciones": [{"columnas": c["columnas"], "minimo_por_grupo": c["minimo_por_grupo"],
                                   "motivo": c.get("motivo", "")}
                                  for c in ds.get("privacidad", {}).get("combinaciones", [])],
                # Cruces que se rechazan: no uses a la vez una columna de cada lista.
                "bloqueos": [{"columnas": b["columnas"], "motivo": b.get("motivo", "")}
                             for b in ds.get("privacidad", {}).get("bloqueos", [])],
                # Temas sensibles: solo se pueden cruzar con estas columnas (sin lugar ni fechas).
                "temas_restringidos": [{"tema": t.get("nombre", t["id"]), "columna": t["columna"],
                                        "columnas_permitidas": t.get("columnas_permitidas", []),
                                        "indicaciones": t.get("indicaciones", "")}
                                       for t in ds.get("temas_restringidos", [])],
                "valores_no_disponibles": [v["motivo"] for v in ds.get("valores_no_disponibles", [])],
            },
        }
