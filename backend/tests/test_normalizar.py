"""Pruebas de la normalización de textos (regla 9)."""
from normalizar import (Diccionario, clave_comparacion, colapsar_espacios, formato_oracion, formato_titulo,
                        normalizar_valor, sin_acentos)

SIGLAS = {"VIH", "COVID", "G6FD"}


def test_colapsar_espacios():
    assert colapsar_espacios("  C S U  LA   ASCENSION ") == "C S U LA ASCENSION"


def test_sin_acentos_y_clave_comparacion():
    assert sin_acentos("Nicolás Treviño Sjögren") == "Nicolas Trevino Sjogren"
    assert clave_comparacion("San Nicolás de los Garza") == "SAN NICOLAS DE LOS GARZA"
    assert clave_comparacion("Tumores (neoplasias)") == "TUMORES NEOPLASIAS"


def test_doble_espacio_es_coma_en_enfermedades():
    col = {"normalizar": "oracion", "doble_espacio": "coma"}
    assert normalizar_valor("DIABETES MELLITUS TIPO 2  SIN MENCION DE COMPLICACION", col, SIGLAS) == \
        "Diabetes mellitus tipo 2, sin mencion de complicacion"
    # Triple espacio también es una coma perdida.
    assert normalizar_valor("DEMENCIA   NO ESPECIFICADA", col, SIGLAS) == "Demencia, no especificada"


def test_oracion_conserva_siglas_y_acentos_existentes():
    assert formato_oracion("ENFERMEDAD POR VIH  SIN OTRA", SIGLAS) == "Enfermedad por VIH sin otra"
    assert formato_oracion("ANEMIA POR DEFICIENCIA DE VITAMINA B12") == "Anemia por deficiencia de vitamina B12"
    assert formato_oracion("HEPATITIS VIRAL TIPO B") == "Hepatitis viral tipo B"
    assert formato_oracion("RINOFARINGITIS AGUDA RESFRIADO COMÚN") == "Rinofaringitis aguda resfriado común"
    assert formato_oracion("GLUCOSA-6-FOSFATO DESHIDROGENASA G6FD", SIGLAS) == "Glucosa-6-fosfato deshidrogenasa G6FD"
    # "Y" es palabra, no sigla.
    assert formato_oracion("EMBARAZO PARTO Y PUERPERIO") == "Embarazo parto y puerperio"


def test_titulo_con_conectores_y_abreviaturas():
    assert formato_titulo("LAMPAZOS DE NARANJO") == "Lampazos de Naranjo"
    assert formato_titulo("C S U EL FRAILE") == "C S U El Fraile"
    col = {"normalizar": "titulo", "doble_espacio": "segmento"}
    assert normalizar_valor("C S U  LA ASCENSION", col, set()) == "C S U La Ascension"
    assert normalizar_valor("C  S  U ITURBIDE", col, set()) == "C S U Iturbide"


def test_no_inventa_acentos_sin_diccionario():
    col = {"normalizar": "titulo"}
    assert normalizar_valor("SAN NICOLAS DE LOS GARZA", col, set()) == "San Nicolas de los Garza"


def test_diccionario_aplica_acentos():
    dic = Diccionario("m", {"SAN NICOLAS DE LOS GARZA": "San Nicolás de los Garza"})
    col = {"normalizar": "titulo"}
    assert normalizar_valor("SAN NICOLAS DE LOS GARZA", col, set(), dic) == "San Nicolás de los Garza"
    assert normalizar_valor("SAN  NICOLAS DE LOS GARZA", col, set(), dic) == "San Nicolás de los Garza"


def test_diccionario_rechaza_entradas_que_cambian_palabras():
    dic = Diccionario("m", {"GARCIA": "García", "GUADALUPE": "Ciudad Guadalupe"})
    assert dic.buscar("GARCIA") == "García"
    assert dic.buscar("GUADALUPE") is None
    assert dic.rechazadas == [("GUADALUPE", "Ciudad Guadalupe")]
