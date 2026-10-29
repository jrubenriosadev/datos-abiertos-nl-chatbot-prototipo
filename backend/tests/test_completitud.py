"""Pruebas de la regla de meses parciales, incompletos y con posible rezago."""
import datetime as dt

from ingest import evaluar_completitud


def _serie(inicio: dt.date, fin: dt.date, por_dia_habil: dict[str, int]) -> dict:
    """Genera conteos diarios: N por día hábil según el mes, y pocos en fin de semana."""
    conteo, dia = {}, inicio
    while dia <= fin:
        n = por_dia_habil[dia.strftime("%Y-%m")]
        conteo[dia] = n if dia.weekday() < 5 else n // 30
        dia += dt.timedelta(days=1)
    return conteo


def test_marca_parcial_incompleto_y_rezago():
    inicio, fin = dt.date(2026, 5, 3), dt.date(2026, 9, 30)
    conteo = _serie(inicio, fin, {"2026-05": 4400, "2026-06": 4250, "2026-07": 3900, "2026-08": 3500, "2026-09": 80})
    meses = {m["mes"]: m for m in evaluar_completitud(conteo, inicio, fin, 0.5, 2, "consultas")}
    assert meses["2026-05"]["estado"] == "parcial"
    assert "del 3 al 31 de mayo" in meses["2026-05"]["nota"]
    assert meses["2026-06"]["estado"] == "completo"
    assert meses["2026-07"]["estado"] == "completo"
    assert meses["2026-08"]["estado"] == "posible_rezago"
    assert meses["2026-09"]["estado"] == "incompleto"
    assert "incompleto" in meses["2026-09"]["nota"]


def test_sin_rezago_configurado_solo_marca_incompletos():
    inicio, fin = dt.date(2026, 1, 1), dt.date(2026, 3, 31)
    conteo = _serie(inicio, fin, {"2026-01": 100, "2026-02": 100, "2026-03": 100})
    meses = evaluar_completitud(conteo, inicio, fin, 0.5, 0, "registros")
    assert [m["estado"] for m in meses] == ["completo", "completo", "completo"]


def test_mes_sin_datos_y_rezago_antes_del_hueco():
    inicio, fin = dt.date(2025, 6, 1), dt.date(2025, 10, 31)
    conteo = _serie(inicio, fin, {"2025-06": 4000, "2025-07": 3900, "2025-08": 3000, "2025-09": 0, "2025-10": 4100})
    meses = {m["mes"]: m for m in evaluar_completitud(conteo, inicio, fin, 0.5, 0, "consultas", rezago_antes_de_hueco=True)}
    assert meses["2025-09"]["estado"] == "sin_datos"
    assert "No hay datos de septiembre de 2025" in meses["2025-09"]["nota"]
    assert "No significa que hubo cero consultas" in meses["2025-09"]["nota"]
    assert meses["2025-08"]["estado"] == "posible_rezago"
    assert "último mes antes de un periodo sin datos" in meses["2025-08"]["nota"]
    # El mes sin datos no entra en la mediana: octubre (después del hueco) queda completo.
    assert meses["2025-10"]["estado"] == "completo"
    # Sin la regla, agosto no se marca.
    sin_regla = {m["mes"]: m["estado"] for m in evaluar_completitud(conteo, inicio, fin, 0.5, 0, "consultas")}
    assert sin_regla["2025-08"] == "completo" and sin_regla["2025-09"] == "sin_datos"


def test_mes_que_termina_antes_es_parcial():
    inicio, fin = dt.date(2026, 1, 1), dt.date(2026, 2, 14)
    conteo = _serie(inicio, fin, {"2026-01": 100, "2026-02": 100})
    meses = evaluar_completitud(conteo, inicio, fin, 0.5, 0, "registros")
    assert meses[1]["estado"] == "parcial"
    assert meses[1]["hasta"] == "2026-02-14"


# ---------- Cobertura por columna (por ejemplo, municipios que dejan de reportar) ----------

def _cinco_meses():
    inicio, fin = dt.date(2026, 1, 1), dt.date(2026, 5, 31)
    conteo = _serie(inicio, fin, {f"2026-0{i}": 100 for i in range(1, 6)})
    return evaluar_completitud(conteo, inicio, fin, 0.5, 0, "consultas")


def test_cobertura_marca_el_mes_en_que_un_municipio_grande_deja_de_reportar():
    from ingest import evaluar_cobertura
    serie = {"Grande": {"2026-01": 600, "2026-02": 620, "2026-03": 610, "2026-04": 600, "2026-05": 40},
             "Chico": {"2026-01": 50, "2026-02": 55, "2026-03": 52, "2026-04": 50, "2026-05": 51},
             "Mediano": {"2026-01": 200, "2026-02": 210, "2026-03": 205, "2026-04": 200, "2026-05": 230}}
    meses = {m["mes"]: m for m in evaluar_cobertura(_cinco_meses(), serie, 0.5, 0.2, "consultas")}
    assert meses["2026-05"]["estado"] == "incompleto"
    assert meses["2026-05"]["cobertura_parcial"] == ["Grande"]
    assert "faltan o bajan mucho los registros de Grande" in meses["2026-05"]["nota"]
    assert all(meses[m]["estado"] == "completo" for m in ("2026-01", "2026-02", "2026-03", "2026-04"))


def test_cobertura_ignora_bajas_chicas_y_valores_que_ya_no_reportaban():
    from ingest import evaluar_cobertura
    # 'Chico' baja a cero (menos del 20% de lo esperado) y 'Cerrado' dejó de aparecer en marzo: nada se marca.
    serie = {"Grande": {f"2026-0{i}": 600 for i in range(1, 6)},
             "Chico": {"2026-01": 50, "2026-02": 50, "2026-03": 50, "2026-04": 50},
             "Cerrado": {"2026-01": 400, "2026-02": 400, "2026-03": 400}}
    meses = evaluar_cobertura(_cinco_meses(), serie, 0.5, 0.2, "consultas")
    assert meses[3]["estado"] == "incompleto"            # abril: 'Cerrado' (400 de ~1,050) deja de reportar
    assert meses[3]["cobertura_parcial"] == ["Cerrado"]
    assert meses[4]["estado"] == "completo"              # mayo: 'Cerrado' ya no reportaba y 'Chico' pesa poco
