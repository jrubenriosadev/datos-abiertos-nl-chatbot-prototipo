"""Herramienta crear_grafica: arma el JSON de una gráfica de barras con los datos reales de un resultado.

El modelo solo elige columnas, tipo y título. Los valores y las etiquetas salen del
resultado guardado (result_id); el modelo nunca escribe valores, HTML ni JavaScript.
"""
import bisect
import re

from catalogo import Catalogo
from consultas import OTROS, ErrorConsulta

TIPOS = ("barras", "horizontales", "agrupadas", "apiladas")
MAX_BARRAS = 30
MAX_SERIES = 8
MAX_TITULO = 120


def _etiqueta(fila: dict, alias: str, tipos: dict) -> str:
    """Texto legible de una categoría: nombre para códigos, 'Mayo de 2026' para meses."""
    if tipos.get(alias) == "codigo":
        return str(fila.get(alias + "_nombre") or fila.get(alias))
    if tipos.get(alias) == "mes" and fila.get("mes_nombre"):
        nombre = fila["mes_nombre"]
        return nombre[:1].upper() + nombre[1:]
    return str(fila.get(alias))


def _columna_agrupada(nombre, grupos: list[str]) -> str:
    """'enfermedad_nombre' → 'enfermedad' si 'enfermedad' es una columna agrupada del resultado."""
    nombre = str(nombre or "")
    if nombre not in grupos and nombre.endswith("_nombre") and nombre[:-7] in grupos:
        return nombre[:-7]
    return nombre


def _unidad(eje_y: str, resultado: dict, ds: dict) -> str:
    if eje_y == "conteo":
        return resultado["unidad"]
    if eje_y == "porcentaje":
        return "%"
    for c in ds["columnas"]:
        if eje_y in (c["alias"] + "_promedio", c["alias"] + "_mediana"):
            return c.get("unidad", "")
        if eje_y == c["alias"] + "_distintos":
            return ""
    return ""


NOMBRES_COLUMNAS = {"conteo": None, "porcentaje": "Porcentaje"}


def tabla_de_resultado(resultado: dict, catalogo: Catalogo, maximo: int = MAX_BARRAS) -> dict | None:
    """Tabla con los datos reales de un resultado (respaldo cuando no se pudo dibujar la gráfica)."""
    grupos = resultado.get("agrupar_por") or []
    if not grupos or not resultado.get("filas"):
        return None
    ds = catalogo.obtener(resultado["dataset_id"])
    tipos = {a: (catalogo.columna(ds["id"], a) or {}).get("tipo", "") for a in grupos}
    numericas = [c for c in resultado["columnas"] if c in ("conteo", "porcentaje") or c.endswith(("_promedio", "_mediana", "_distintos"))]
    encabezados = [g.replace("_", " ").capitalize() for g in grupos]
    for c in numericas:
        nombre = NOMBRES_COLUMNAS.get(c, c.replace("_", " ").capitalize()) or resultado["unidad"].capitalize()
        unidad = _unidad(c, resultado, ds)
        encabezados.append(f"{nombre} ({unidad})" if unidad and unidad not in (resultado["unidad"], "") else nombre)
    filas = [[_etiqueta(f, g, tipos) for g in grupos] + [f.get(c) for c in numericas] for f in resultado["filas"][:maximo]]
    return {"titulo": "Datos de la consulta", "columnas": encabezados, "filas": filas,
            "nota": f"Se muestran {len(filas)} de {resultado['grupos_totales']} grupos." if resultado["grupos_totales"] > len(filas) else None}


def crear_grafica(resultado: dict, catalogo: Catalogo, eje_x, eje_y="conteo", titulo="", tipo="barras",
                  serie=None) -> dict:
    ds = catalogo.obtener(resultado["dataset_id"])
    grupos = resultado["agrupar_por"]
    if not grupos:
        raise ErrorConsulta("El resultado no tiene grupos; para graficar, consulta con 'agrupar_por'.")
    # El modelo suele pedir la columna legible ('enfermedad_nombre', 'mes_nombre'): es la misma categoría,
    # y las etiquetas siempre se muestran con el nombre legible.
    eje_x, serie = _columna_agrupada(eje_x, grupos), _columna_agrupada(serie, grupos) if serie else serie
    if eje_x not in grupos:
        raise ErrorConsulta("'eje_x' debe ser una de las columnas agrupadas del resultado.", grupos)
    numericas = [c for c in resultado["columnas"] if c in ("conteo", "porcentaje") or c.endswith(("_promedio", "_mediana", "_distintos"))]
    if eje_y not in numericas:
        raise ErrorConsulta("'eje_y' debe ser una columna numérica del resultado.", numericas)
    tipo = tipo or "barras"
    if tipo not in TIPOS:
        raise ErrorConsulta(f"Tipo de gráfica no válido: {tipo!r}. Solo hay barras.", list(TIPOS))

    tipos = {}
    for alias in grupos:
        col = catalogo.columna(ds["id"], alias)
        tipos[alias] = col["tipo"] if col else ""
    filas = [f for f in resultado["filas"] if f.get(eje_y) is not None]

    # Limpieza del título (se muestra con textContent; aquí solo se acota).
    titulo = re.sub(r"[\x00-\x1f<>]", "", str(titulo or "")).strip()[:MAX_TITULO] or \
        f"{resultado['unidad'].capitalize()} por {eje_x.replace('_', ' ')}"

    grafica = {"result_id": resultado["result_id"], "titulo": titulo, "tipo": tipo,
               "unidad": _unidad(eje_y, resultado, ds), "marcadas": [], "nota": None}

    if tipo in ("agrupadas", "apiladas"):
        if len(grupos) != 2:
            raise ErrorConsulta("Las barras agrupadas o apiladas necesitan un resultado agrupado por dos columnas.")
        serie = serie or next(g for g in grupos if g != eje_x)
        if serie not in grupos or serie == eje_x:
            raise ErrorConsulta("'serie' debe ser la otra columna agrupada del resultado.", [g for g in grupos if g != eje_x])
        etiquetas, nombres_serie, valores = [], [], {}
        for f in filas:
            x, s = _etiqueta(f, eje_x, tipos), _etiqueta(f, serie, tipos)
            if x == OTROS or s == OTROS:
                continue
            if x not in etiquetas:
                etiquetas.append(x)
            if s not in nombres_serie:
                nombres_serie.append(s)
            valores[(x, s)] = f[eje_y]
        if len(nombres_serie) > MAX_SERIES:
            raise ErrorConsulta(f"Hay {len(nombres_serie)} series; el máximo es {MAX_SERIES}. Filtra o usa menos categorías.")
        grafica["etiquetas"] = etiquetas
        grafica["series"] = [{"nombre": s, "valores": [valores.get((x, s)) for x in etiquetas]} for s in nombres_serie]
        claves_x = list(dict.fromkeys(f.get(eje_x) for f in filas if _etiqueta(f, eje_x, tipos) in etiquetas))
    else:
        if len(grupos) == 2:
            raise ErrorConsulta("El resultado está agrupado por dos columnas: usa tipo 'agrupadas' o 'apiladas' con 'serie'.")
        grafica["etiquetas"] = [_etiqueta(f, eje_x, tipos) for f in filas]
        grafica["valores"] = [f[eje_y] for f in filas]
        claves_x = [f.get(eje_x) for f in filas]

    if not grafica["etiquetas"]:
        raise ErrorConsulta("No hay datos que graficar en ese resultado.")

    # Meses sin datos dentro del rango graficado: se insertan como barras vacías (valor nulo) para que
    # el hueco se vea, en lugar de que dos meses separados parezcan seguidos.
    if tipos.get(eje_x) == "mes" and claves_x and claves_x == sorted(claves_x):
        for m in catalogo.meses_marcados(ds["id"]):
            if m["estado"] == "sin_datos" and claves_x[0] < m["mes"] < claves_x[-1] and m["mes"] not in claves_x:
                i = bisect.bisect(claves_x, m["mes"])
                claves_x.insert(i, m["mes"])
                grafica["etiquetas"].insert(i, m["nombre"][:1].upper() + m["nombre"][1:])
                if "series" in grafica:
                    for s in grafica["series"]:
                        s["valores"].insert(i, None)
                else:
                    grafica["valores"].insert(i, None)
    if len(grafica["etiquetas"]) > MAX_BARRAS:
        raise ErrorConsulta(f"Hay {len(grafica['etiquetas'])} barras; el máximo es {MAX_BARRAS}. Consulta con un 'limite' menor.")

    # Meses incompletos: se marcan para dibujarlos distinto y se explica en una nota.
    if tipos.get(eje_x) == "mes":
        marcados = {m["mes"]: m for m in catalogo.meses_marcados(ds["id"])}
        grafica["marcadas"] = [i for i, k in enumerate(claves_x) if k in marcados]
        if grafica["marcadas"]:
            grafica["nota"] = "Barras marcadas: " + "; ".join(
                f"{marcados[claves_x[i]]['nombre']} ({marcados[claves_x[i]]['estado'].replace('_', ' ')})"
                for i in grafica["marcadas"]) + ". No representan meses completos (sin datos = el mes no viene en los archivos)."
    return grafica
