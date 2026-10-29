"""Motor de consultas estructuradas (herramientas consultar_datos y buscar_valores).

El modelo nunca escribe SQL: manda una consulta estructurada que aquí se valida
contra el catálogo. Los nombres de columna salen del catálogo (nunca del modelo) y
todos los valores van como parámetros. Además:
- privacidad: los grupos con menos de k registros se juntan en "Otros" o se ocultan
  (k sube si la consulta combina columnas sensibles, según privacidad.combinaciones),
  y se rechazan las combinaciones de filtros que podrían identificar a una persona
  y los cruces de privacidad.bloqueos;
- temas restringidos (temas_restringidos): sus valores solo se cruzan con columnas permitidas;
- valores ocultos: los poco frecuentes (si el dataset lo pide) y los de valores_no_disponibles
  no se listan, no se filtran y, al agrupar, se juntan en "Otros";
- advertencias: se agregan las del dataset y las de meses incompletos.
"""
import datetime as dt
import difflib
import re
import uuid

from catalogo import OPERADORES_POR_TIPO, Catalogo
from ingest import lista_legible, nombre_mes, q, valor_en_regla
from normalizar import clave_comparacion, texto_busqueda

OTROS = "Otros (grupos pequeños)"
MAX_FILTROS = 8
MAX_VALORES_LISTA = 60
MAX_AGRUPAR = 2
SPAN_NUMERO_ESTRECHO = 2      # un rango de edad de 2 años o menos es "estrecho"
SPAN_DIAS_ESTRECHO = 7        # un rango de fechas de una semana o menos es "estrecho"
MAX_VALORES_ESTRECHO = 3      # filtrar por 3 municipios o menos es "estrecho"
NO_ACOTAN = ("distinto", "no_en")   # operadores que excluyen valores: no acotan la consulta
MOTIVO_POCO_FRECUENTE = ("tiene muy pocos registros en todo el periodo; por privacidad no se consulta por separado "
                         "(sí cuenta dentro del total de su grupo)")


class ErrorConsulta(Exception):
    """Error que se le devuelve al modelo para que corrija la consulta (o explique el rechazo)."""

    def __init__(self, mensaje: str, sugerencias: list | None = None, tipo: str = "validacion"):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.sugerencias = sugerencias or []
        self.tipo = tipo

    def como_dict(self) -> dict:
        d = {"error": self.mensaje, "tipo_error": self.tipo}
        if self.sugerencias:
            d["sugerencias"] = self.sugerencias
        return d


def _redondear(valor, decimales=1):
    if valor is None:
        return None
    valor = round(float(valor), decimales)
    return int(valor) if valor.is_integer() else valor


class MotorConsultas:
    def __init__(self, con, catalogo: Catalogo, max_filas: int = 200):
        self.con = con
        self.catalogo = catalogo
        self.max_filas = max_filas
        # Valores válidos de cada columna categórica o de código, para validar filtros.
        # Las frecuencias solo se usan para ordenar búsquedas; nunca se devuelven.
        self._valores: dict[tuple[str, str], dict] = {}
        for ds in catalogo.datasets.values():
            for c in ds["columnas"]:
                if c["expuesta"] and c["tipo"] in ("categoria", "codigo"):
                    self._cargar_valores(ds, c)

    def _cargar_valores(self, ds: dict, col: dict):
        alias, tabla = col["alias"], ds["tabla"]
        if col["tipo"] == "codigo":
            filas = self.con.execute(f"SELECT {q(alias)}, any_value({q(alias + '_nombre')}), count(*) FROM {q(tabla)} "
                                     f"WHERE {q(alias)} IS NOT NULL GROUP BY 1").fetchall()
        else:
            filas = self.con.execute(f"SELECT {q(alias)}, {q(alias)}, count(*) FROM {q(tabla)} "
                                     f"WHERE {q(alias)} IS NOT NULL GROUP BY 1").fetchall()
        # Descripciones alternativas de cada clave (por ejemplo "tipo dos" y "tipo 2"): solo para buscar.
        alternativas = {}
        if col["tipo"] == "codigo":
            existe = self.con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                                      [tabla + "_variantes"]).fetchone()[0]
            if existe:
                for clave, texto in self.con.execute(f"SELECT clave, texto FROM {q(tabla + '_variantes')} WHERE columna = ?",
                                                     [alias]).fetchall():
                    alternativas[clave] = alternativas.get(clave, "") + " " + texto
        info = {"canonicos": {}, "nombres": {}, "frecuencia": {}, "busqueda": {}, "ocultos": {}}
        privacidad = ds.get("privacidad", {})
        k_raros = int(privacidad.get("minimo_por_grupo", 5)) if privacidad.get("ocultar_valores_poco_frecuentes") else 0
        reglas = [r for r in ds.get("valores_no_disponibles", []) if r["columna"] == alias]
        for valor, nombre, n in filas:
            # Valores ocultos (valor → motivo): no se listan, no se filtran y al agrupar van a "Otros".
            regla = next((r for r in reglas if valor_en_regla(valor, r)), None)
            if regla:
                info["ocultos"][valor] = regla["motivo"]
            elif n < k_raros:
                info["ocultos"][valor] = MOTIVO_POCO_FRECUENTE
            info["canonicos"][clave_comparacion(valor)] = valor
            info["nombres"][valor] = nombre
            info["frecuencia"][valor] = n
            info["busqueda"][valor] = texto_busqueda(f"{valor} {nombre}{alternativas.get(valor, '')}"
                                                     if col["tipo"] == "codigo" else valor)
            # En columnas de código también se acepta el nombre exacto como valor.
            if col["tipo"] == "codigo":
                info["canonicos"].setdefault(clave_comparacion(nombre), valor)
        self._valores[(ds["id"], alias)] = info

    # ---------- Validación ----------

    def _dataset(self, ds_id) -> dict:
        ds = self.catalogo.obtener(str(ds_id or ""))
        if not ds:
            raise ErrorConsulta(f"No existe el dataset {ds_id!r}.",
                                [d["id"] for d in self.catalogo.datasets.values()])
        return ds

    def _columna(self, ds: dict, alias) -> dict:
        alias = str(alias or "")
        col = self.catalogo.columna(ds["id"], alias)
        if col:
            return col
        for c in ds["columnas"]:
            if c["alias"] == alias and not c["expuesta"]:
                raise ErrorConsulta(f"La columna '{alias}' no está disponible: {c.get('motivo', '')}", tipo="no_disponible")
        disponibles = [c["alias"] for c in ds["columnas"] if c["expuesta"]] + [d["alias"] for d in ds.get("derivadas", [])]
        raise ErrorConsulta(f"La columna '{alias}' no existe.", disponibles)

    def _resolver_valor(self, ds: dict, col: dict, valor) -> str:
        """Convierte un valor del modelo en el valor exacto de la base (o explica qué sí existe)."""
        info = self._valores[(ds["id"], col["alias"])]
        texto = str(valor).strip()
        encontrado = info["canonicos"].get(clave_comparacion(texto))
        if encontrado is not None and encontrado in info["ocultos"]:
            motivo = info["ocultos"][encontrado]
            tipo = "privacidad" if motivo == MOTIVO_POCO_FRECUENTE else "no_disponible"
            raise ErrorConsulta(f"El valor {texto!r} de '{col['alias']}' no está disponible: "
                                f"{motivo[:1].lower() + motivo[1:]}", tipo=tipo)
        if encontrado is not None:
            return encontrado
        parecidos = difflib.get_close_matches(clave_comparacion(texto), list(info["canonicos"]), n=5, cutoff=0.75)
        sugerencias = [info["canonicos"][p] for p in parecidos if info["canonicos"][p] not in info["ocultos"]]
        raise ErrorConsulta(
            f"El valor {texto!r} no existe en '{col['alias']}'."
            + (" Usa buscar_valores para encontrar el valor correcto." if not sugerencias else ""),
            sugerencias)

    @staticmethod
    def _como_lista(valor) -> list:
        return list(valor) if isinstance(valor, (list, tuple)) else [valor]

    def _numero(self, valor, alias):
        try:
            if isinstance(valor, bool):
                raise ValueError
            return float(valor)
        except (TypeError, ValueError):
            raise ErrorConsulta(f"El valor {valor!r} de '{alias}' debe ser un número.")

    def _normalizar_filtro(self, ds: dict, f) -> dict:
        if not isinstance(f, dict) or "columna" not in f or "operador" not in f or "valor" not in f:
            raise ErrorConsulta("Cada filtro necesita 'columna', 'operador' y 'valor'.")
        col = self._columna(ds, f["columna"])
        alias, tipo, op = col["alias"], col["tipo"], str(f["operador"])
        if op not in OPERADORES_POR_TIPO[tipo]:
            raise ErrorConsulta(f"El operador '{op}' no se puede usar con '{alias}'.", OPERADORES_POR_TIPO[tipo])
        valores = self._como_lista(f["valor"])
        if op == "entre" and len(valores) != 2:
            raise ErrorConsulta(f"El operador 'entre' necesita una lista de dos valores [desde, hasta] en '{alias}'.")
        if op in ("igual", "distinto", "mayor_igual", "menor_igual") and len(valores) != 1:
            raise ErrorConsulta(f"El operador '{op}' necesita un solo valor en '{alias}'.")
        if not valores or len(valores) > MAX_VALORES_LISTA:
            raise ErrorConsulta(f"La lista de valores de '{alias}' debe tener entre 1 y {MAX_VALORES_LISTA} elementos.")

        if tipo in ("categoria", "codigo"):
            valores = list(dict.fromkeys(self._resolver_valor(ds, col, v) for v in valores))
        elif tipo == "numero":
            valores = [self._numero(v, alias) for v in valores]
        elif tipo == "fecha":
            try:
                valores = [dt.date.fromisoformat(str(v)) for v in valores]
            except ValueError:
                raise ErrorConsulta(f"Las fechas de '{alias}' deben tener el formato AAAA-MM-DD.")
        elif tipo == "mes":
            valores = [str(v) for v in valores]
            if not all(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v) for v in valores):
                raise ErrorConsulta(f"Los meses de '{alias}' deben tener el formato AAAA-MM.")
        elif tipo == "rango":
            mapa = {clave_comparacion(v): v for v in col.get("valores", [])}
            resueltos = []
            for v in valores:
                if clave_comparacion(str(v)) not in mapa:
                    raise ErrorConsulta(f"El valor {v!r} no existe en '{alias}'.", col.get("valores", []))
                resueltos.append(mapa[clave_comparacion(str(v))])
            valores = resueltos
        if op == "entre" and valores[0] > valores[1]:
            valores = [valores[1], valores[0]]

        marcas = ", ".join("?" for _ in valores)
        sql = {"igual": f"{q(alias)} = ?", "distinto": f"{q(alias)} <> ?", "en": f"{q(alias)} IN ({marcas})",
               "no_en": f"{q(alias)} NOT IN ({marcas})", "entre": f"{q(alias)} BETWEEN ? AND ?",
               "mayor_igual": f"{q(alias)} >= ?", "menor_igual": f"{q(alias)} <= ?"}[op]
        return {"columna": alias, "tipo": tipo, "operador": op, "valores": valores, "sql": sql,
                "cuasi": bool(col.get("cuasi_identificador")) or tipo == "fecha"}

    def _contar_estrechos(self, filtros: list[dict]) -> list[str]:
        """Columnas cuasi-identificadoras filtradas de forma muy precisa (riesgo de identificar a alguien)."""
        estrechas = []
        for alias in dict.fromkeys(f["columna"] for f in filtros if f["cuasi"]):
            fs = [f for f in filtros if f["columna"] == alias]
            tipo = fs[0]["tipo"]
            if tipo in ("categoria", "codigo"):
                if any(f["operador"] in ("igual", "en") and len(f["valores"]) <= MAX_VALORES_ESTRECHO for f in fs):
                    estrechas.append(alias)
                continue
            # Números y fechas: se combinan los límites de todos los filtros de esa columna.
            bajo, alto = None, None
            for f in fs:
                v = f["valores"]
                if f["operador"] == "igual":
                    bajo, alto = v[0], v[0]
                elif f["operador"] == "entre":
                    bajo = v[0] if bajo is None else max(bajo, v[0])
                    alto = v[1] if alto is None else min(alto, v[1])
                elif f["operador"] == "mayor_igual":
                    bajo = v[0] if bajo is None else max(bajo, v[0])
                elif f["operador"] == "menor_igual":
                    alto = v[0] if alto is None else min(alto, v[0])
            if bajo is not None and alto is not None:
                ancho = (alto - bajo).days if tipo == "fecha" else alto - bajo
                if ancho <= (SPAN_DIAS_ESTRECHO if tipo == "fecha" else SPAN_NUMERO_ESTRECHO):
                    estrechas.append(alias)
        return estrechas

    def _agregaciones_validas(self, ds_id: str) -> list[str]:
        return self.catalogo.describir(ds_id)["agregaciones"]

    # ---------- consultar_datos ----------

    def consultar(self, dataset_id, filtros=None, agrupar_por=None, agregaciones=None, proporcion_dentro_de=None,
                  proporcion_ignora_filtros=None, ordenar_por=None, limite=None) -> dict:
        ds = self._dataset(dataset_id)
        tabla = ds["tabla"]
        k = int(ds.get("privacidad", {}).get("minimo_por_grupo", 5))
        unidad = ds["unidad"]["plural"]

        # Filtros
        filtros = filtros or []
        if not isinstance(filtros, list) or len(filtros) > MAX_FILTROS:
            raise ErrorConsulta(f"'filtros' debe ser una lista de hasta {MAX_FILTROS} filtros.")
        filtros_n = [self._normalizar_filtro(ds, f) for f in filtros]
        estrechas = self._contar_estrechos(filtros_n)
        maximo = int(ds.get("privacidad", {}).get("max_filtros_estrechos", 2))
        if len(estrechas) > maximo:
            raise ErrorConsulta(
                "La consulta es demasiado específica: combina filtros muy precisos sobre "
                f"{', '.join(estrechas)} y podría identificar a una persona. Por privacidad no se responde. "
                "Amplía el periodo, el rango de edad o el lugar.", tipo="privacidad")

        # Agrupación
        agrupar_por = list(dict.fromkeys(agrupar_por or []))
        if len(agrupar_por) > MAX_AGRUPAR:
            raise ErrorConsulta(f"Solo se puede agrupar por hasta {MAX_AGRUPAR} columnas.")
        grupos = []
        for alias in agrupar_por:
            col = self._columna(ds, alias)
            if col["tipo"] == "numero":
                raise ErrorConsulta(f"No se puede agrupar por '{alias}' exacta; usa 'rango_edad' u otra columna de grupos.",
                                    [d["alias"] for d in ds.get("derivadas", []) if d["tipo"] == "rango"])
            if col["tipo"] == "fecha":
                raise ErrorConsulta("No se puede agrupar por día (riesgo de identificar personas); agrupa por 'mes'.", ["mes"])
            grupos.append(col)

        # Columnas que acotan la consulta (las agrupadas y las filtradas; excluir valores no acota).
        acotan = set(agrupar_por) | {f["columna"] for f in filtros_n if f["operador"] not in NO_ACOTAN}
        # Cruces bloqueados: una columna de cada lista a la vez podría identificar a una persona.
        for bloqueo in ds.get("privacidad", {}).get("bloqueos", []):
            cruzadas = [sorted(acotan & set(lista)) for lista in bloqueo["columnas"]]
            if all(cruzadas):
                raise ErrorConsulta(
                    f"Por privacidad no se puede hacer este cruce: {bloqueo.get('motivo', 'combina demasiados datos')} "
                    f"({lista_legible([', '.join(c) for c in cruzadas])}) y podría identificar a una persona. "
                    "Quita una de esas columnas (por ejemplo, el municipio) o pregunta por separado.", tipo="privacidad")
        # Temas restringidos: si se filtra uno de sus valores, solo se cruza con las columnas permitidas.
        temas = ds.get("temas_restringidos", [])
        temas_tocados = []
        for tema in temas:
            filtrado = any(f["columna"] == tema["columna"] and f["operador"] not in NO_ACOTAN
                           and any(valor_en_regla(v, tema) for v in f["valores"]) for f in filtros_n)
            if not filtrado:
                continue
            otras = sorted(acotan - {tema["columna"]} - set(tema.get("columnas_permitidas", [])))
            if otras:
                permitidas = [c for c in tema.get("columnas_permitidas", []) if c != "grupo"]
                raise ErrorConsulta(
                    f"Por ser un tema sensible ({tema.get('nombre', tema['id'])}), estas consultas solo se muestran en "
                    f"total" + (f" o por {lista_legible(permitidas)}" if permitidas else "") +
                    f", no por {lista_legible(otras)}: así se evitan rankings y se protege a las personas.",
                    tipo="tema_restringido")
            temas_tocados.append(tema)

        # Privacidad reforzada: si la consulta usa (filtra o agrupa) una columna de cada lista de una
        # combinación (por ejemplo, lugar y edad), los grupos deben ser más grandes.
        usadas = set(agrupar_por) | {f["columna"] for f in filtros_n}
        aviso_reforzado = None
        for comb in ds.get("privacidad", {}).get("combinaciones", []):
            cruzadas = [sorted(usadas & set(lista)) for lista in comb["columnas"]]
            if all(cruzadas) and int(comb["minimo_por_grupo"]) > k:
                k = int(comb["minimo_por_grupo"])
                nombres = " y ".join(", ".join(c) for c in cruzadas)
                aviso_reforzado = (f"Por privacidad, al cruzar {nombres} solo se muestran los grupos con al menos {k} "
                                   f"{unidad}." + (f" {comb['motivo']}" if comb.get("motivo") else ""))

        # Agregaciones
        validas = self._agregaciones_validas(ds["id"])
        agregaciones = list(dict.fromkeys(agregaciones or ["conteo"]))
        for a in agregaciones:
            if a not in validas:
                raise ErrorConsulta(f"La agregación {a!r} no existe.", validas)
        extras_sql, columnas_salida = [], []
        for a in agregaciones:
            if a.startswith("conteo_distinto:"):
                alias = a.split(":", 1)[1]
                extras_sql.append(f"count(DISTINCT {q(alias)}) AS {q(alias + '_distintos')}")
                columnas_salida.append(alias + "_distintos")
            elif a.endswith("_promedio"):
                extras_sql.append(f"avg({q(a[:-9])}) AS {q(a)}")
                columnas_salida.append(a)
            elif a.endswith("_mediana"):
                extras_sql.append(f"median({q(a[:-8])}) AS {q(a)}")
                columnas_salida.append(a)
        con_proporcion = "proporcion" in agregaciones

        if proporcion_dentro_de and proporcion_dentro_de not in agrupar_por:
            raise ErrorConsulta("'proporcion_dentro_de' debe ser una de las columnas de 'agrupar_por'.", agrupar_por)
        ignora = list(proporcion_ignora_filtros or [])
        for alias in ignora:
            if alias not in {f["columna"] for f in filtros_n}:
                raise ErrorConsulta(f"'proporcion_ignora_filtros' menciona '{alias}', que no está en los filtros.")

        # Límite
        if limite is None:
            # Una serie mensual se devuelve completa; los rankings, con los primeros 20.
            limite = self.max_filas if any(c["tipo"] == "mes" for c in grupos) else min(20, self.max_filas)
        try:
            limite = int(limite)
        except (TypeError, ValueError):
            raise ErrorConsulta("'limite' debe ser un número entero.")
        if not 1 <= limite <= self.max_filas:
            raise ErrorConsulta(f"'limite' debe estar entre 1 y {self.max_filas}.")

        # Consulta principal (identificadores del catálogo, valores como parámetros).
        donde, params = self._where(filtros_n)
        sel_grupos = []
        for col in grupos:
            sel_grupos.append(q(col["alias"]))
            if col["tipo"] == "codigo":
                sel_grupos.append(f"any_value({q(col['alias'] + '_nombre')}) AS {q(col['alias'] + '_nombre')}")
            if col["tipo"] == "rango":
                sel_grupos.append(f"min({q(col['alias'] + '_orden')}) AS {q('_orden_' + col['alias'])}")
        select = ", ".join(sel_grupos + ["count(*) AS conteo"] + extras_sql)
        sql = f"SELECT {select} FROM {q(tabla)} WHERE {donde}"
        if grupos:
            sql += " GROUP BY " + ", ".join(q(c["alias"]) for c in grupos)
        cur = self.con.cursor()
        try:
            cur.execute(sql, params)
            nombres = [d[0] for d in cur.description]
            filas = [dict(zip(nombres, f)) for f in cur.fetchall()]

            # Denominadores de la proporción.
            denominadores, den_total = {}, None
            if con_proporcion:
                donde_d, params_d = self._where([f for f in filtros_n if f["columna"] not in ignora])
                if proporcion_dentro_de:
                    cur.execute(f"SELECT {q(proporcion_dentro_de)}, count(*) FROM {q(tabla)} WHERE {donde_d} "
                                f"GROUP BY 1", params_d)
                    denominadores = dict(cur.fetchall())
                else:
                    cur.execute(f"SELECT count(*) FROM {q(tabla)} WHERE {donde_d}", params_d)
                    den_total = cur.fetchone()[0]
        finally:
            cur.close()

        def porcentaje(fila):
            den = denominadores.get(fila.get(proporcion_dentro_de)) if proporcion_dentro_de else den_total
            if not den or fila["conteo"] is None:
                return None
            p = 100 * fila["conteo"] / den
            return _redondear(p, 2 if 0 < p < 0.1 else 1)

        if con_proporcion:
            for f in filas:
                f["porcentaje"] = porcentaje(f)
        advertencias = []
        suprimidos = None
        total = sum(f["conteo"] for f in filas)

        # Privacidad: supresión de grupos pequeños.
        if not grupos:
            fila = filas[0] if filas else {"conteo": 0}
            if 0 < fila["conteo"] < k:
                filas_salida = [{"conteo": None, "conteo_texto": f"menos de {k}"}]
                advertencias.append(f"El resultado tiene menos de {k} {unidad}; por privacidad no se muestra la cifra exacta.")
                total = None
            else:
                salida = {"conteo": fila["conteo"]}
                if con_proporcion:
                    salida["porcentaje"] = fila.get("porcentaje")
                for c in columnas_salida:
                    salida[c] = _redondear(fila.get(c))
                filas_salida = [salida]
            grupos_totales = 1
            truncado = False
        else:
            # Filas que no se muestran por separado aunque sean grandes: valores ocultos y valores de un
            # tema restringido cruzados con columnas no permitidas (por ejemplo, autolesión por municipio).
            ocultas = [f for f in filas if self._fila_oculta(ds, f, grupos, acotan)]
            ids_ocultas = {id(f) for f in ocultas}
            visibles = [f for f in filas if f["conteo"] >= k and id(f) not in ids_ocultas]
            pequenos = [f for f in filas if f["conteo"] < k and id(f) not in ids_ocultas]
            self._ordenar(visibles, grupos, ordenar_por, agregaciones, columnas_salida)
            grupos_totales = len(visibles)
            truncado = len(visibles) > limite
            filas_salida = [self._fila_salida(f, grupos, con_proporcion, columnas_salida) for f in visibles[:limite]]
            if pequenos or ocultas:
                suma_pequenos = sum(f["conteo"] for f in pequenos)
                suma = suma_pequenos + sum(f["conteo"] for f in ocultas)
                suprimidos = {"grupos": len(pequenos), "registros": suma_pequenos if suma_pequenos >= k else f"menos de {k}"}
                if ocultas:
                    suprimidos["ocultos_por_privacidad"] = len(ocultas)
                if not truncado and suma >= k:
                    otros = {c["alias"]: OTROS for c in grupos}
                    for c in grupos:
                        if c["tipo"] == "codigo":
                            otros[c["alias"] + "_nombre"] = OTROS
                    otros["conteo"] = suma
                    if con_proporcion:
                        otros["porcentaje"] = porcentaje({"conteo": suma}) if not proporcion_dentro_de else None
                    filas_salida.append(otros)
                    if pequenos:
                        advertencias.append(f"Por privacidad, {len(pequenos)} grupos con menos de {k} {unidad} cada uno "
                                            f"se juntaron en '{OTROS}'.")
                    if ocultas:
                        advertencias.append(f"Por privacidad o por ser temas sensibles, algunos valores no se muestran por "
                                            f"separado en este cruce y se juntaron en '{OTROS}'.")
                else:
                    if pequenos:
                        advertencias.append(f"Por privacidad, se ocultaron {len(pequenos)} grupos con menos de {k} {unidad} cada uno.")
                    if ocultas:
                        advertencias.append("Por privacidad o por ser temas sensibles, algunos valores no se muestran por "
                                            "separado en este cruce.")
                if suma < k:
                    total = None  # el total exacto permitiría deducir los grupos ocultos
                    advertencias.append("Por privacidad no se muestra el total exacto de esta consulta.")
            if truncado:
                advertencias.append(f"Se muestran los primeros {limite} de {grupos_totales} grupos.")
            # Temas sensibles que quedaron visibles en el resultado (por ejemplo, en un ranking sin lugar ni fecha).
            for tema in temas:
                if tema not in temas_tocados and any(valor_en_regla(x.get(tema["columna"]), tema) for x in filas_salida):
                    temas_tocados.append(tema)

        if aviso_reforzado:
            advertencias.insert(0, aviso_reforzado)
        advertencias = self._advertencias_dataset(ds, filtros_n, agrupar_por, filas_salida) + advertencias

        columnas = []
        for c in grupos:
            columnas.append(c["alias"])
            if c["tipo"] == "codigo":
                columnas.append(c["alias"] + "_nombre")
            if c["tipo"] == "mes":
                columnas.append("mes_nombre")
        columnas += ["conteo"] + (["porcentaje"] if con_proporcion else []) + columnas_salida

        return {
            "result_id": "r" + uuid.uuid4().hex[:8],
            "dataset_id": ds["id"],
            "dataset": ds["nombre"],
            "periodo_cubierto": ds.get("periodo", {}).get("texto", ""),
            "unidad": unidad,
            "agrupar_por": agrupar_por,
            "filtros": [{"columna": f["columna"], "operador": f["operador"],
                         "valor": [v.isoformat() if isinstance(v, dt.date) else v for v in f["valores"]]} for f in filtros_n],
            "columnas": columnas,
            "filas": filas_salida,
            "total_filtrado": total,
            "grupos_totales": grupos_totales,
            "suprimidos": suprimidos,
            "advertencias": list(dict.fromkeys(advertencias)),
            # Temas sensibles de la respuesta: indicaciones para el modelo y si el sistema agrega la línea de apoyo.
            "temas_sensibles": [{"tema": t.get("nombre", t["id"]), "indicaciones": t.get("indicaciones", ""),
                                 "linea_apoyo": bool(t.get("linea_apoyo"))} for t in temas_tocados],
        }

    def _fila_oculta(self, ds: dict, fila: dict, grupos: list[dict], acotan: set) -> bool:
        """¿La fila agrupada se junta en 'Otros' aunque sea grande? Sí, si alguno de sus valores está oculto o es de
        un tema restringido y la consulta usa columnas que ese tema no permite."""
        for c in grupos:
            valor = fila.get(c["alias"])
            info = self._valores.get((ds["id"], c["alias"]))
            if info and valor in info["ocultos"]:
                return True
            for tema in ds.get("temas_restringidos", []):
                if tema["columna"] == c["alias"] and valor_en_regla(valor, tema) and \
                        acotan - {tema["columna"]} - set(tema.get("columnas_permitidas", [])):
                    return True
        return False

    def _where(self, filtros_n: list[dict]) -> tuple[str, list]:
        if not filtros_n:
            return "TRUE", []
        params = []
        for f in filtros_n:
            params.extend(f["valores"])
        return " AND ".join(f"({f['sql']})" for f in filtros_n), params

    def _ordenar(self, filas, grupos, ordenar_por, agregaciones, columnas_salida):
        """Orden por defecto: cronológico para meses, por edad para rangos y de mayor a menor en lo demás."""
        tipos = {c["alias"]: c["tipo"] for c in grupos}
        if ordenar_por:
            if not isinstance(ordenar_por, dict):
                raise ErrorConsulta("'ordenar_por' debe ser {campo, direccion}.")
            campo = ordenar_por.get("campo", "conteo")
            permitidos = list(tipos) + ["conteo"] + (["porcentaje"] if "proporcion" in agregaciones else []) + columnas_salida
            if campo not in permitidos:
                raise ErrorConsulta(f"No se puede ordenar por {campo!r}.", permitidos)
            desc = str(ordenar_por.get("direccion", "desc")).lower() != "asc"
        elif "mes" in tipos.values():
            campo, desc = next(a for a, t in tipos.items() if t == "mes"), False
        elif "rango" in tipos.values():
            campo, desc = "_orden_" + next(a for a, t in tipos.items() if t == "rango"), False
        else:
            campo, desc = "conteo", True
        if tipos.get(campo) == "rango":
            campo = "_orden_" + campo
        # Los valores vacíos van al final; el nombre del primer grupo desempata.
        con_valor = [f for f in filas if f.get(campo) is not None]
        sin_valor = [f for f in filas if f.get(campo) is None]
        con_valor.sort(key=lambda f: (f[campo], str(f.get(grupos[0]["alias"]))), reverse=desc)
        filas[:] = con_valor + sin_valor

    def _fila_salida(self, f, grupos, con_proporcion, columnas_salida) -> dict:
        salida = {}
        for c in grupos:
            salida[c["alias"]] = f[c["alias"]]
            if c["tipo"] == "codigo":
                salida[c["alias"] + "_nombre"] = f[c["alias"] + "_nombre"]
            if c["tipo"] == "mes" and f[c["alias"]]:
                salida["mes_nombre"] = nombre_mes(f[c["alias"]])
        salida["conteo"] = f["conteo"]
        if con_proporcion:
            salida["porcentaje"] = f.get("porcentaje")
        for c in columnas_salida:
            salida[c] = _redondear(f.get(c))
        return salida

    def _advertencias_dataset(self, ds, filtros_n, agrupar_por, filas_salida) -> list[str]:
        """Advertencias configuradas en el dataset + periodo cubierto y meses marcados."""
        avisos = []
        usadas = set(agrupar_por) | {f["columna"] for f in filtros_n}
        for a in ds.get("advertencias_condicionales", []):
            cuando = a.get("cuando", {})
            aplica = False
            if cuando.get("siempre"):
                aplica = True
            if set(cuando.get("agrupar_por", [])) & set(agrupar_por):
                aplica = True
            if set(cuando.get("usa_columna", [])) & usadas:
                aplica = True
            incluye = cuando.get("resultado_incluye")
            if incluye:
                col, vals = incluye["columna"], set(incluye["valores"])
                if any(f.get(col) in vals for f in filas_salida):
                    aplica = True
                for f in filtros_n:
                    if f["columna"] == col and f["operador"] in ("igual", "en") and vals & set(f["valores"]):
                        aplica = True
            if aplica:
                avisos.append(a["texto"])

        periodo = ds.get("periodo")
        if not periodo:
            return avisos
        inicio, fin = dt.date.fromisoformat(periodo["inicio"]), dt.date.fromisoformat(periodo["fin"])
        # Rango de fechas que toca la consulta.
        bajo, alto = inicio, fin
        for f in filtros_n:
            if f["tipo"] == "fecha":
                v = f["valores"]
                if f["operador"] == "entre":
                    bajo, alto = max(bajo, v[0]), min(alto, v[1])
                elif f["operador"] == "mayor_igual":
                    bajo = max(bajo, v[0])
                elif f["operador"] == "menor_igual":
                    alto = min(alto, v[0])
                pedido = (v[0], v[-1]) if f["operador"] == "entre" else (v[0], v[0])
                if pedido[0] < inicio or pedido[1] > fin:
                    avisos.append(f"Solo hay datos {periodo['texto']}; la parte del filtro fuera de ese periodo no tiene datos.")
            if f["tipo"] == "mes":
                meses_f = sorted(f["valores"])
                m_bajo = dt.date.fromisoformat(meses_f[0] + "-01")
                m_alto_ini = dt.date.fromisoformat(meses_f[-1] + "-01")
                m_alto = (m_alto_ini.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
                if f["operador"] in ("igual", "en", "entre"):
                    bajo, alto = max(bajo, m_bajo), min(alto, m_alto)
                if m_bajo < inicio.replace(day=1) or m_alto_ini > fin:
                    avisos.append(f"Solo hay datos {periodo['texto']}; los meses pedidos fuera de ese periodo no tienen datos.")
        tocados = [m for m in ds.get("meses", []) if m["estado"] != "completo"
                   and dt.date.fromisoformat(m["desde"]) <= alto and dt.date.fromisoformat(m["hasta"]) >= bajo]
        temporal = "mes" in agrupar_por or any(f["tipo"] in ("fecha", "mes") for f in filtros_n)
        if temporal:
            avisos += [m["nota"] for m in tocados]
        else:
            avisos.append(f"Periodo cubierto: {periodo['texto']}. Los totales suman todo ese periodo, no un año calendario.")
            if tocados:
                avisos.append("Incluye meses sin datos, incompletos o con posible rezago de captura: "
                              + ", ".join(f"{m['nombre']} ({m['estado'].replace('_', ' ')})" for m in tocados) + ".")
        return avisos

    # ---------- buscar_valores ----------

    def buscar_valores(self, dataset_id, columna, texto, limite: int = 25) -> dict:
        ds = self._dataset(dataset_id)
        col = self._columna(ds, columna)
        if col["tipo"] not in ("categoria", "codigo"):
            raise ErrorConsulta(f"'{col['alias']}' no es una columna de categorías; revisa sus valores en describir_dataset.")
        info = self._valores[(ds["id"], col["alias"])]
        palabras = [p for p in re.split(r"[^a-z0-9ñ\-]+", texto_busqueda(str(texto or ""))) if len(p) >= 2]
        if not palabras:
            raise ErrorConsulta("Escribe al menos una palabra para buscar.")

        def coincide(objetivo: str, palabras: list[str]) -> int:
            return sum(1 for p in palabras if p in objetivo)

        # Primero todas las palabras; si no hay resultados, sin plurales y luego por coincidencia parcial.
        candidatos = [v for v, t in info["busqueda"].items() if coincide(t, palabras) == len(palabras)]
        if not candidatos:
            raices = [re.sub(r"(es|s)$", "", p) if len(p) > 4 else p for p in palabras]
            candidatos = [v for v, t in info["busqueda"].items() if coincide(t, raices) == len(raices)]
        if not candidatos and len(palabras) > 1:
            puntaje = {v: coincide(t, palabras) for v, t in info["busqueda"].items()}
            candidatos = [v for v, p in puntaje.items() if p > 0]
            candidatos.sort(key=lambda v: -puntaje[v])
        # Los valores ocultos (poco frecuentes o no disponibles) nunca se listan.
        hay_ocultos = any(v in info["ocultos"] for v in candidatos)
        candidatos = [v for v in candidatos if v not in info["ocultos"]]
        candidatos.sort(key=lambda v: -info["frecuencia"][v])
        mostrados = candidatos[:limite]
        if col["tipo"] == "codigo":
            valores = [{"clave": v, "nombre": info["nombres"][v]} for v in mostrados]
        else:
            valores = mostrados
        respuesta = {"dataset_id": ds["id"], "columna": col["alias"], "valores": valores,
                     "total_coincidencias": len(candidatos)}
        if len(candidatos) > limite:
            respuesta["nota"] = f"Hay {len(candidatos)} coincidencias; se muestran las {limite} más frecuentes. Afina la búsqueda si hace falta."
        if not candidatos:
            respuesta["nota"] = "No hay coincidencias. Prueba con otra palabra o un sinónimo (los nombres no llevan acentos)."
        if hay_ocultos:
            respuesta["nota_privacidad"] = ("Algunos valores no se muestran: por privacidad (muy pocos registros) o porque "
                                            "no se consultan por separado. Sí cuentan dentro del total de su grupo.")
        return respuesta
