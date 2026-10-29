"""Pruebas de buscar_dataset y describir_dataset sobre el catálogo real."""
import json

import oraculo
from oraculo import cifra, meses_esperados

DS = "salud_consulta_general"


def test_buscar_encuentra_salud(catalogo_real, salud):
    r = catalogo_real.buscar("consultas por diabetes en Monterrey")
    assert r["datasets"][0]["dataset_id"] == DS
    assert r["datasets"][0]["periodo"] == salud["periodo"]["texto"]


def test_buscar_educacion_y_seguridad_son_proximamente(catalogo_real):
    edu = catalogo_real.buscar("escuelas primarias por municipio")
    assert [p["categoria"] for p in edu["proximamente"]] == ["Educación"]
    seg = catalogo_real.buscar("denuncias de robo")
    assert [p["categoria"] for p in seg["proximamente"]] == ["Seguridad"]


def test_buscar_sin_coincidencias_dice_que_hay(catalogo_real):
    # Ojo: "receta" sí aparece en los datos ("consulta para repetición de receta").
    r = catalogo_real.buscar("horóscopo semanal")
    assert r.get("sin_coincidencias") is True
    assert r["disponibles"][0]["dataset_id"] == DS


def test_describir_para_el_modelo(catalogo_real, salud):
    d = catalogo_real.describir(DS)
    alias = [c["alias"] for c in d["columnas"]]
    assert {"municipio", "sexo", "edad", "grupo", "enfermedad", "unidad_medica", "mes", "rango_edad", "fecha"} <= set(alias)
    # Peso, altura y el identificador no se ofrecen al modelo.
    assert not {"peso", "altura", "id_consulta"} & set(alias)
    assert {c["columna"] for c in d["columnas_no_disponibles"]} >= {"peso", "altura"}
    assert d["periodo_cubierto"] == salud["periodo"]["texto"]
    # Cada mes marcado (calculado con el oráculo) aparece en las advertencias con su nota.
    for m in meses_esperados():
        if m["estado"] != "completo":
            assert m["nota"] in d["advertencias"], m["mes"]
    assert {m["mes"]: m["estado"] for m in d["meses"]} == {m["mes"]: m["estado"] for m in meses_esperados()}
    assert any("consulta, no una persona" in a for a in d["advertencias"])
    assert "conteo_distinto:unidad_medica" in d["agregaciones"]
    assert "edad_promedio" in d["agregaciones"]
    municipio = next(c for c in d["columnas"] if c["alias"] == "municipio")
    assert len(municipio["valores"]) == len(oraculo.salud()["municipio"])
    enfermedad = next(c for c in d["columnas"] if c["alias"] == "enfermedad")
    assert "valores" not in enfermedad and "buscar_valores" in enfermedad["nota"]
    assert any(v["valores"] == ["XXI"] for v in d["valores_especiales"])


def test_describir_no_entrega_conteos(catalogo_real):
    """Las cifras deben salir de consultar_datos, no de la descripción."""
    o = oraculo.salud()
    texto = json.dumps(catalogo_real.describir(DS), ensure_ascii=False)
    for n in (o["filas"], o["municipio"]["MONTERREY"], o["enfermedad"]["I10X"], *o["mes"].values()):
        assert str(n) not in texto and cifra(n) not in texto, n


def test_fuente_y_dataset_inexistente(catalogo_real):
    assert catalogo_real.fuente_texto(DS).startswith("Consulta General y Especialidades (Secretaría de Salud")
    assert catalogo_real.describir("no_existe") is None
