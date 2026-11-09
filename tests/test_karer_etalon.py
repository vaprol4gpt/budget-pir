"""Второй реальный объект целиком — «Карьер» (трек 2 прогона «два дня», 03.11.2026).

Каждый раздел Карьера против своего листа согласованного эталона
`reference_data/Карьер_эталон.xlsx` (очищенная копия: «№2.1_ИГДИ», «№2.2.1/2/3_ИГИ»,
«№2.4_ИГМИ», «№2.5_ИЭИ» и сводная «№1ПС_Сводная»). Ввод — как оператор вводит объект в интерфейсе:
объёмы — в поля ведомости, отличия от правил программы — правками строк,
коэффициенты — параметрами сметы, ставки начислений — данными объекта, работы
вне сборника — своими строками. Критерий (план работ, Э6) — итог листа
± 0,1 %; разбор расхождений по строкам — docs/proverka_karer.md.

Хранение ожидаемых итогов: ни одной суммы листа в этом файле нет — ожидаемое
читается из ячеек файла эталона в момент теста (как в test_geology_etalon.py и
test_aggregator.py). Объёмы ввода — тоже ячейки листа там, где их можно
прочесть; числа в коде — только то, что в листе записано текстом подписи или
формулой (номер ячейки — в комментарии).
"""

from pathlib import Path

import openpyxl
import pytest

from core.models import PLACEMENT_AFTER_INDEX, calculate_amount
from core.object_data import CalculationContext, ObjectData
from core.overrides import BASIS_PRICE_LIST, Edit, OverrideLog
from core.pricing import calculate_total_with_vat
from core.references import DEFAULT_DB_PATH, ReferenceBook
from core.stages import StagedSession, as_staged

KARIER = Path(__file__).resolve().parent.parent / "reference_data" / "Карьер_эталон.xlsx"
DATE = "2026-09-30"  # III кв. 2026 — уровень цен листов (индексы 81,95 и 7,19)

pytestmark = [
    pytest.mark.skipif(not KARIER.exists(), reason="нет эталона в reference_data/"),
    pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite"),
]


@pytest.fixture(scope="module")
def wb():
    return openpyxl.load_workbook(KARIER, data_only=True)


@pytest.fixture(scope="module")
def book():
    return ReferenceBook()


def num(ws, addr) -> float:
    """Число из ячейки листа; пусто или текст — 0."""
    value = ws[addr].value
    return 0.0 if value is None or isinstance(value, str) else float(value)


def staged_session(module, obj, book, volume_input, estimate_input=None, volume_edits=None,
                   estimate_edits=None, customs=(), reason="как в листе Карьера"):
    """Оба этапа, как оператор: ввод ведомости и её правки → подтверждение →
    параметры сметы, правки строк сметы и свои строки → подтверждение.
    volume_edits / estimate_edits — {id строки: (поле, значение)}; прежнее
    значение берётся из расчёта, как в интерфейсе. Возвращает (сессию с
    подтверждёнными этапами, контекст расчёта, предупреждения этапа сметы).
    Общий для этого теста и демо-базы (tools/make_demo.py)."""
    context = CalculationContext(book, obj)
    session = StagedSession(as_staged(module))
    session.inputs["volumes"] = dict(volume_input)
    if volume_edits:
        auto = session.compute("volumes", context).effective
        session.apply_draft("volumes", volume_input, [
            Edit(key, field, getattr(auto[key], field), value, reason)
            for key, (field, value) in volume_edits.items()
        ])
    draft = session.compute("volumes", context)
    assert not draft.not_applied, [e.line_id for e in draft.not_applied]
    session.confirm("volumes", context)
    session.inputs["estimate"] = dict(estimate_input or {})
    auto = session.compute("estimate", context).effective
    log = OverrideLog()
    for key, (field, value) in (estimate_edits or {}).items():
        line = auto[key]
        old = line.coefs.get("rate") if field == "rate" else getattr(line, field)
        log.apply(key, field, old, value, reason)
    for row in customs:
        log.add_custom_line(row, reason)
    session.apply_draft("estimate", estimate_input or {}, log.history())
    draft = session.compute("estimate", context)
    assert not draft.not_applied, [e.line_id for e in draft.not_applied]
    session.confirm("estimate", context)
    return session, context, draft.warnings


def run_staged(module, obj, book, volume_input, estimate_input=None, volume_edits=None,
               estimate_edits=None, customs=(), reason="как в листе Карьера"):
    """`staged_session` → (строки сметы, суммы строк, итоги с индексом и НДС,
    предупреждения этапа сметы)."""
    session, context, warnings = staged_session(
        module, obj, book, volume_input, estimate_input, volume_edits, estimate_edits,
        customs, reason)
    lines = dict(session.result("estimate").lines)
    cache: dict[str, float] = {}
    amounts = {i: calculate_amount(line, lines, cache) for i, line in lines.items()}
    totals = calculate_total_with_vat(lines, context, work_type=module.code, date=DATE)
    return lines, amounts, totals, warnings


# --- Данные объекта (шаг 0): ставки начислений по листам Карьера ---------
#
# Общие — как в листах ИГИ (одинаковы во всех трёх): внешний транспорт E152
# 39,2 %, оргликвидация E153 6 % × E154 2,5, районный к полевым E156 0,35 +
# 0,15, к камеральным E157 0,10 + 0,15; неблагоприятный период — т.2 п.4 (×1,2,
# строка 82). По разделам — то, что в своём листе иначе: геодезия — районный к
# полевым 0,15 + 0,15 (№2.1_ИГДИ!G63); экология — районный к полевым 1,35 + 0,25
# − 1 (H88) и оргликвидация без ×2,5 (H53). Расстояние 12 км — пункт т.4 листа
# экологии (п.4 «от 10 до 15 км», №2.5_ИЭИ!F51). ИГМИ — свои параметры сметы,
# блок начислений объекта на неё не влияет (неблагоприятный период для неё —
# «не применять», в листе №2.4_ИГМИ его нет).
def karier_object(wb) -> ObjectData:
    igi, iei = wb["№2.2.1_ИГИ"], wb["№2.5_ИЭИ"]
    return ObjectData(
        code="Т-01", distance_km=12, unfavorable_period=True,
        external_transport_rate=num(igi, "E152"), org_liquidation_rate=num(igi, "E153"),
        org_liquidation_k=num(igi, "E154"), regional_field_rate=num(igi, "E156"),
        regional_office_rate=num(igi, "E157"),
        accrual_overrides={
            "geodesy": {"regional_field_rate": 0.15 + 0.15},  # формула №2.1_ИГДИ!G63
            "ecology": {"regional_field_rate": num(iei, "E89") + num(iei, "E90") - 1,
                        "org_liquidation_k": 1.0},
        },
    )


# --- Геология: три листа «№2.2.1/2/3_ИГИ» (по карьеру) ----------------------
#
# Лист — тот же шаблон, что эталон Автодороги «1.2.1_ИГИ» (строки и формулы
# совпадают), поэтому ввод — как в tests/test_geology_etalon.py. Отличия
# Карьера, введённые как оператор:
# - камеральная обработка бурения D132 — число в листе, не «метраж −
#   гидронаблюдения» (правило программы) — правка строки ведомости;
# - программа работ: объём 1, цена 0 (E127) — правка цены строки сметы;
# - техотчёт: ставка 0 (E148) — правка ставки;
# - внутренний транспорт: т.4 п.5 со ставкой 11,25 % (E151) — в справочнике у
#   п.5 другая ставка (18,75 %, «от 20 до 25 км»), правка ставки.
GEOLOGY_SHEETS = ("№2.2.1_ИГИ", "№2.2.2_ИГИ", "№2.2.3_ИГИ")


def geology_input(ws):
    d = lambda addr: num(ws, addr)  # noqa: E731
    volume_input = {
        "drilling_by_category": [
            {"soil_category": c, "drilling_length": d(f"D{row}")}
            for c, row in (("IV", 29), ("V", 30), ("VI", 31), ("VII", 32), ("VIII", 33))
        ],
        "drilling_count": d("D17"),
        "rekognoscirovka_km": d("D19"),
        "kreplenie_length": d("D34"),
        "gidronablyudeniya_length": d("D35"),
        "shtamp_count": d("D50"),
        "baza_months": d("D70"),
        "lab_glinistye_count": d("D87"),
        "lab_peschanye_count": d("D88"),
        "lab_opt_vlazhnost_count": d("D123"),
        "lab_korroziya_stal_count": d("D108"),
        "lab_korroziya_beton_count": d("D109"),
        "sbor_materialov_count": d("D129"),
    }
    volume_edits = {key: ("qty", d(cell)) for key, cell in (
        ("termometriya", "D52"), ("monolity_dispersnye", "D40"), ("monolity_skalnye", "D41"),
        ("proby_vody", "D43"), ("lab_summarnaya_vlazhnost", "D92"), ("lab_analiz_vody", "D97"),
        ("lab_skalnye_fizmeh", "D122"), ("kameral_bureniya", "D132"),
    )}
    estimate_input = {
        "k_programma_rabot": d("E128"),   # 1,25 — т.81 прим.1
        "k_monolity_skalnye": d("E42"),   # 0,7 — прим. к т.57
        "k_rekognoscirovka": d("E20"),    # 1,2 — т.9 прим.3
    }
    estimate_edits = {
        "geology.programma_rabot": ("price", d("E127")),
        "geology.tehotchet": ("rate", d("E148")),
        "geology.transport": ("rate", d("E151")),
    }
    return volume_input, volume_edits, estimate_input, estimate_edits


def summed_geology_input(wb):
    """Одна смета геологии на три листа ИГИ: объёмы (поля ведомости и правки
    строк) — суммой по листам, параметры сметы и правки строк сметы — как в
    листах (одинаковы во всех трёх). Так оператор сводит три сметы в один
    раздел бюджета (тест ниже, демо-база tools/make_demo.py)."""
    inputs = [geology_input(wb[sheet]) for sheet in GEOLOGY_SHEETS]
    volume_input = dict(inputs[0][0])
    for key, value in volume_input.items():
        if key == "drilling_by_category":
            volume_input[key] = [
                {"soil_category": row["soil_category"],
                 "drilling_length": sum(i[0][key][n]["drilling_length"] for i in inputs)}
                for n, row in enumerate(value)
            ]
        else:
            volume_input[key] = sum(i[0][key] for i in inputs)
    volume_edits = {key: ("qty", sum(i[1][key][1] for i in inputs)) for key in inputs[0][1]}
    return volume_input, volume_edits, inputs[0][2], inputs[0][3]


def run_geology(ws, book, obj):
    from modules.geology.module import GeologyModule

    volume_input, volume_edits, estimate_input, estimate_edits = geology_input(ws)
    return run_staged(GeologyModule(), obj, book, volume_input, estimate_input,
                      volume_edits, estimate_edits)


# --- Геодезия: лист «№2.1_ИГДИ» ---------------------------------------------
#
# Позиции и выборы, которых не было у Автодороги, — параметры этапа сметы,
# добавленные треком 2 (по умолчанию — как раньше): пункт привязки т.48 п.3,
# база т.82 п.2, коэффициент трассы 1,5, знаки в базе транспорта, ступень
# экспертизы «свыше 500 тыс. до 1 млн», съёмка/план М 1:2000 и условие «участок
# М 1:2000 до 10 га» (к = 1,4). Ставка внутреннего транспорта 13,75 % (G60:
# «св. 15 до 20 км, полевые св. 150 до 300 тыс. руб.») — правкой ставки строки.
def geodesy_input(ws):
    d = lambda addr: num(ws, addr)  # noqa: E731
    section = 0.5  # «Высота сечения рельефа 0,5 м» — подписи B26, B32, B48, B50
    volume_input = {
        "oporny_points_count": d("D19"),
        "height_points_count": d("D23"),
        "trassa_km": d("D29"),
        "privyazka_count": d("D35"),
        "znaki_count": d("D38"),
        "base_months": d("D41"),
        "oporny_kameral_points_count": d("D44"),
        # B26: «3 кар. × 7,5 га» — каждый карьер своей строкой (к = 1,4 — до 10 га на участок)
        "topo_surveys": [
            *[{"topo_scale": "1:2000", "topo_section": section, "topo_area": d("D26") / 3,
               "survey_condition": "small_2000"} for _ in range(3)],
            {"topo_scale": "1:2000", "topo_section": section, "topo_area": d("D32"),
             "survey_condition": "strip"},
        ],
        "topo_plans": [
            {"topo_scale": "1:2000", "topo_section": section, "topo_area": d("D48")},
            {"topo_scale": "1:2000", "topo_section": section, "topo_area": d("D50"),
             "plan_condition": "strip"},
        ],
    }
    estimate_input = {
        "computer_tech_coef": 1.2,      # G54 = ROUND(G53 × 1,2)
        "expertise": "yes",             # G68
        "trassa_length_coef": 1.5,      # E31 «K1 = 1.5»
        "privyazka_item": "3",          # E35 «Таблица 48 п.3»
        "base_item": "2",               # E41 «Таблица 82 п.2»
        "transport_base_misc": "yes",   # G60 = (G36 + G39 + G42) × …
    }
    estimate_edits = {"geodesy.transport": ("rate", 0.1375)}  # G60 «13.75 / 100»
    return volume_input, estimate_input, estimate_edits


def run_geodesy(ws, book, obj):
    from modules.geodesy.module import GeodesyModule

    volume_input, estimate_input, estimate_edits = geodesy_input(ws)
    return run_staged(GeodesyModule(), obj, book, volume_input, estimate_input,
                      estimate_edits=estimate_edits)


# --- Экология «№2.5_ИЭИ» и ИГМИ «№2.4_ИГМИ» — ввод их эталонных тестов -------
#
# Экология: ввод KARIER_FULL_INPUT (tests/test_ecology_etalons.py) + блок
# «Работы, не учтённые СБЦ-99» (строки 95–106, объём > 0) — свои строки «после
# индекса» (в листе H107 = H93 + SUM(H95:H106)). ИГМИ: KARIER_PARAMETERS
# (tests/test_hydromet_etalons.py); неблагоприятный период отмечен в данных
# объекта (для геологии), а в листе ИГМИ его нет — выбор «не применять».
def ecology_customs(ws, rows=range(95, 107)):
    customs = []
    for row in rows:
        qty, price = ws[f"D{row}"].value, ws[f"E{row}"].value
        if qty and price is not None:
            customs.append({
                "name": ws[f"B{row}"].value, "unit": ws[f"C{row}"].value or "шт.", "qty": qty,
                "price": price, "basis_kind": BASIS_PRICE_LIST,
                "basis": f"лист «{ws.title}», строка {row}", "placement": PLACEMENT_AFTER_INDEX,
            })
    return customs


def run_ecology(ws, book, obj):
    from modules.ecology.module import EcologyModule
    from tests.test_ecology_etalons import KARIER_FULL_INPUT

    stages = as_staged(EcologyModule()).stages
    volume_input = {f.name: KARIER_FULL_INPUT[f.name] for f in stages[0].fields if f.name in KARIER_FULL_INPUT}
    estimate_input = {f.name: KARIER_FULL_INPUT[f.name] for f in stages[1].fields if f.name in KARIER_FULL_INPUT}
    return run_staged(EcologyModule(), obj, book, volume_input, estimate_input,
                      customs=ecology_customs(ws))


def run_hydromet(book, obj):
    from modules.hydromet import module as h
    from tests.test_hydromet_etalons import KARIER_PARAMETERS, KARIER_VOLUMES

    parameters = {**KARIER_PARAMETERS, h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_OFF}
    return run_staged(h.HydrometModule(), obj, book, KARIER_VOLUMES, parameters)


# =============================== ТЕСТЫ ======================================

@pytest.fixture(scope="module")
def obj(wb):
    return karier_object(wb)


@pytest.fixture(scope="module")
def geology(wb, book, obj):
    return {sheet: run_geology(wb[sheet], book, obj) for sheet in GEOLOGY_SHEETS}


@pytest.fixture(scope="module")
def geodesy(wb, book, obj):
    return run_geodesy(wb["№2.1_ИГДИ"], book, obj)


@pytest.fixture(scope="module")
def ecology(wb, book, obj):
    return run_ecology(wb["№2.5_ИЭИ"], book, obj)


@pytest.fixture(scope="module")
def hydromet(book, obj):
    return run_hydromet(book, obj)


# --- Геология ---------------------------------------------------------------
#
# Расхождение каждого листа ИГИ — одна объяснённая ячейка: H143 «=11*8.1» =
# 89,1 (камеральная обработка испытаний вращательным срезом т.83 п.4) — сумма
# вписана в формулу при объёме D143 = 0 и без полевых испытаний (D51 = 0):
# ошибка ручного расчёта листа (тот же шаблон, что у Автодороги, — там так же).
# Она входит в базу районного к камеральным (D157 = H146 + H148), поэтому в
# итоге листа её вклад — H143 × (1 + E157). Остальное — округления листа.

def sheet_error_h143(ws) -> float:
    return num(ws, "H143") * (1 + num(ws, "E157"))


@pytest.mark.parametrize("sheet", GEOLOGY_SHEETS)
def test_geology_sheet_total_within_0_1_percent_after_explained_cell(wb, geology, sheet):
    ws = wb[sheet]
    _lines, amounts, totals, _w = geology[sheet]
    expected = num(ws, "H159") - sheet_error_h143(ws)
    assert totals.base_total == pytest.approx(expected, rel=0.001)
    assert abs(totals.base_total - expected) < 2   # остаток — округления листа (ROUND строк)
    assert totals.indexed_total == pytest.approx(num(ws, "H160") - sheet_error_h143(ws) * totals.index,
                                                 rel=0.001)
    # без поправки на H143 — чуть больше 0,1 % (сама ошибка листа, не программы):
    # недобор равен её вкладу с точностью до округлений листа
    assert num(ws, "H159") - totals.base_total == pytest.approx(sheet_error_h143(ws), abs=2)
    assert num(ws, "H159") - totals.base_total > 0.001 * num(ws, "H159")


GEOLOGY_LINE_CELLS = {
    "geology.geodesy_privyazka": "H17", "geology.rekognoscirovka": "H19",
    "geology.kreplenie_skvazhin": "H34", "geology.gidronablyudeniya": "H35",
    "geology.monolity_dispersnye": "H40", "geology.monolity_skalnye": "H41",
    "geology.proby_vody": "H43", "geology.baza_ekspedicii": "H70",
    "geology.lab_glinistye_fiz": "H87", "geology.lab_peschanye_fiz": "H88",
    "geology.lab_summarnaya_vlazhnost": "H92", "geology.lab_analiz_vody": "H97",
    "geology.lab_korroziya_stal": "H108", "geology.lab_korroziya_beton": "H109",
    "geology.lab_skalnye_fizmeh": "H122", "geology.lab_opt_vlazhnost": "H123",
    "geology.programma_rabot": "H127", "geology.sbor_materialov": "H129",
    "geology.kameral_rekognoscirovki": "H131", "geology.kameral_bureniya": "H132",
    "geology.kameral_bureniya_gidro": "H134", "geology.kameral_lab_glinistye": "H136",
    "geology.kameral_lab_peschanye": "H137", "geology.kameralnaya_himsostav_vody": "H138",
    "geology.kameral_korroziya": "H139", "geology.tehotchet": "H148",
    "geology.transport": "H151", "geology.accrual.external_transport": "H152",
    "geology.accrual.org_liquidation": "H153", "geology.accrual.regional_field": "H156",
}


@pytest.mark.parametrize("sheet", GEOLOGY_SHEETS)
def test_geology_each_line_matches_sheet_cell(wb, geology, sheet):
    ws = wb[sheet]
    _lines, amounts, _totals, _w = geology[sheet]
    for line, cell in GEOLOGY_LINE_CELLS.items():
        # лист округляет камеральные строки и подытоги (ROUND), модуль — нет
        assert amounts.get(line, 0.0) == pytest.approx(num(ws, cell), abs=1.5), (line, cell)
    drilling = sum(a for i, a in amounts.items() if i.startswith("geology.drilling."))
    assert drilling == pytest.approx(sum(num(ws, f"H{r}") for r in range(26, 34)), abs=0.01)
    assert amounts["geology.accrual.unfavorable"] == pytest.approx(
        num(ws, "H82") - num(ws, "H81"), abs=1)
    # районный к камеральным — на 25 % от H143 меньше (объяснённая ячейка)
    assert amounts["geology.accrual.regional_office"] == pytest.approx(
        num(ws, "H157") - num(ws, "H143") * num(ws, "E157"), abs=1.5)


def test_three_geology_sheets_equal_one_estimate_with_summed_volumes(wb, book, obj, geology):
    """В сводный бюджет программа берёт одну смету на раздел (повтор модуля —
    ошибка), а у Карьера три сметы ИГИ (по карьеру). Расчёт геологии линеен по
    объёмам, поэтому одна смета с суммой объёмов трёх листов даёт сумму трёх
    смет — так оператор может свести их в один раздел (с точностью до
    округлений начислений до рубля)."""
    from modules.geology.module import GeologyModule

    volume_input, volume_edits, estimate_input, estimate_edits = summed_geology_input(wb)
    _lines, _amounts, totals, _w = run_staged(
        GeologyModule(), obj, book, volume_input, estimate_input, volume_edits, estimate_edits)
    three = sum(geology[sheet][2].base_total for sheet in GEOLOGY_SHEETS)
    assert totals.base_total == pytest.approx(three, abs=5)


# --- Геодезия ---------------------------------------------------------------

GEODESY_LINE_CELLS = {
    "geodesy.oporny_set": "G19", "geodesy.height_set": "G23", "geodesy.trassa": "G29",
    "geodesy.privyazka": "G35", "geodesy.znaki": "G38", "geodesy.base": "G41",
    "geodesy.kameral.oporny_set": "G44", "geodesy.kameral.height_set": "G47",
    "geodesy.topo_plan.0": "G48", "geodesy.kameral.trassa": "G49", "geodesy.topo_plan.1": "G50",
    "geodesy.transport": "G60", "geodesy.accrual.external_transport": "G61",
    "geodesy.accrual.org_liquidation": "G62", "geodesy.accrual.regional_field": "G63",
}


def test_geodesy_total_within_0_1_percent(wb, geodesy):
    ws = wb["№2.1_ИГДИ"]
    _lines, amounts, totals, _w = geodesy
    assert totals.base_total == pytest.approx(num(ws, "G67") + num(ws, "G68"), rel=0.001)
    assert totals.indexed_total == pytest.approx(num(ws, "G70"), rel=0.001)
    # фактически — рубли (строки модуля не округляются до рубля, листа — да)
    assert abs(totals.indexed_total - num(ws, "G70")) < 20


def test_geodesy_each_line_matches_sheet_cell(wb, geodesy):
    ws = wb["№2.1_ИГДИ"]
    _lines, amounts, _totals, _w = geodesy
    for line, cell in GEODESY_LINE_CELLS.items():
        assert amounts[line] == pytest.approx(num(ws, cell), abs=1), (line, cell)
    surveys = sum(amounts[f"geodesy.topo_survey.{n}"] for n in range(3))
    assert surveys == pytest.approx(num(ws, "G26"), abs=1)          # 3 участка × 7,5 га × 1,4
    assert amounts["geodesy.topo_survey.3"] == pytest.approx(num(ws, "G32"), abs=1)
    office = sum(a for i, a in amounts.items()
                 if i.startswith(("geodesy.kameral.", "geodesy.topo_plan.")))
    assert office == pytest.approx(num(ws, "G54"), abs=1)          # камеральные × 1,2
    assert amounts["geodesy.accrual.regional_office"] == pytest.approx(num(ws, "G55") - num(ws, "G54"), abs=1)
    expertise = amounts["geodesy.expertise"] + amounts["geodesy.expertise_fixed"]
    assert expertise == pytest.approx(num(ws, "G68"), abs=1)


def test_geodesy_second_expertise_bracket_is_sheet_formula(geodesy):
    """№2.1_ИГДИ!G68 = ((G67 − 500 тыс.) × 1,5 % + 11,25 тыс.) × 1,3 — вторая ступень т.80."""
    lines, amounts, _totals, _w = geodesy
    before = sum(a for i, a in amounts.items() if i not in ("geodesy.expertise", "geodesy.expertise_fixed"))
    assert 500_000 < before <= 1_000_000
    expertise = amounts["geodesy.expertise"] + amounts["geodesy.expertise_fixed"]
    assert expertise == pytest.approx(((before - 500_000) * 0.015 + 11_250) * 1.3, abs=1)
    assert "500" in lines["geodesy.expertise"].basis and "№2.1_ИГДИ" in lines["geodesy.expertise"].basis


def test_geodesy_new_parameters_by_default_keep_road_estimate(book):
    """Новые параметры по умолчанию — как раньше: смета Автодороги та же (её
    итог проверяет tests/test_geodesy_etalon.py), явные значения по умолчанию
    дают те же строки, что пустые."""
    from modules.geodesy import module as g
    from tests.test_geodesy_etalon import ROAD_OBJECT, ROAD_PARAMETERS, ROAD_VOLUMES

    plain = run_staged(g.GeodesyModule(), ROAD_OBJECT, book, ROAD_VOLUMES, ROAD_PARAMETERS)
    explicit = run_staged(g.GeodesyModule(), ROAD_OBJECT, book, ROAD_VOLUMES, {
        **ROAD_PARAMETERS, g.PARAM_TRASSA_K: 1.0, g.PARAM_PRIVYAZKA_ITEM: "4",
        g.PARAM_BASE_ITEM: "5", g.PARAM_TRANSPORT_MISC: "no"})
    assert plain[1] == explicit[1]
    assert plain[0]["geodesy.privyazka"].price == 256.0
    assert plain[0]["geodesy.base"].price == 85500.0
    assert "geodesy.znaki" not in plain[0]["geodesy.transport"].base_ids


@pytest.mark.parametrize("scale, area, applies", [
    ("1:2000", 7.5, True), ("1:2000", 10, True), ("1:2000", 22.5, False), ("1:1000", 7.5, False),
])
def test_small_2000_condition_only_within_sheet_conditions(book, scale, area, applies):
    from modules.geodesy.module import GeodesyModule

    module = GeodesyModule()
    data = {"oporny_points_count": 0, "privyazka_count": 0, "topo_surveys": [
        {"topo_scale": scale, "topo_section": 0.5, "topo_area": area, "survey_condition": "small_2000"}]}
    estimate = module.build_estimate(module.build_volumes(data, book), book)
    line = estimate["geodesy.topo_survey.0"]
    assert (line.coefs == {"K1": 1.4}) is applies
    assert any("условие съёмки" in w for w in module.check(estimate)) is not applies


def test_scale_1_2000_has_price_from_karier_sheet(book):
    from modules.geodesy.module import GeodesyModule

    module = GeodesyModule()
    data = {"oporny_points_count": 0, "privyazka_count": 0,
            "topo_surveys": [{"topo_scale": "1:2000", "topo_section": 0.5, "topo_area": 1}],
            "topo_plans": [{"topo_scale": "1:2000", "topo_section": 0.5, "topo_area": 1}]}
    estimate = module.build_estimate(module.build_volumes(data, book), book)
    assert estimate["geodesy.topo_survey.0"].price == pytest.approx(804)   # E26 «A=0.804 тыс.руб»
    assert estimate["geodesy.topo_plan.0"].price == pytest.approx(174)   # E48 «A=0.174 тыс.руб»


# --- Экология и ИГМИ ---------------------------------------------------------

def test_ecology_total_matches_sheet(wb, ecology):
    _lines, _amounts, totals, _w = ecology
    assert totals.indexed_total == pytest.approx(num(wb["№2.5_ИЭИ"], "H107"), abs=0.01)


def test_hydromet_total_matches_svodnaya(wb, hydromet):
    _lines, _amounts, totals, _w = hydromet
    assert totals.indexed_total == pytest.approx(num(wb["№1ПС_Сводная"], "D29"), abs=0.01)


# --- Сводная «№1ПС_Сводная», раздел 5 «Инженерные изыскания» ----------------

SVOD_CELLS = {  # раздел → (ячейка «до снижения», ячейка «в КП» = ROUND(F × C39, 2))
    "geodesy": ("D25", "X25"),
    "geology:2.2.1": ("D26", "X26"), "geology:2.2.2": ("D27", "X27"), "geology:2.2.3": ("D28", "X28"),
    "hydromet": ("D29", "X29"), "ecology": ("D30", "X30"),
}


def karier_budget(geology, geodesy, ecology, hydromet, budget_reduction):
    from core.aggregator import BudgetAggregator, ModuleBudget

    totals = {
        "geodesy": geodesy[2], "hydromet": hydromet[2], "ecology": ecology[2],
        **{f"geology:{sheet[1:6]}": geology[sheet][2] for sheet in GEOLOGY_SHEETS},
    }
    aggregator = BudgetAggregator(budget_reduction=budget_reduction)
    for code, total in totals.items():
        assert total.warnings == []
        aggregator.add_module(ModuleBudget.from_totals(code, code, total))
    return aggregator


def test_karier_svodnaya_surveys_section_within_0_1_percent(wb, geology, geodesy, ecology, hydromet):
    """Критерий трека 2: раздел «Инженерные изыскания» (D24 = D25 + … + D30) ±
    0,1 % до понижающего коэффициента и после К = 0,85 сводной (C39; столбец
    «в КП» X24). Три сметы ИГИ — три раздела (у программы повтор модуля в
    бюджете запрещён, поэтому здесь — свои коды; в интерфейсе — одна смета с
    суммой объёмов, см. тест выше)."""
    ws = wb["№1ПС_Сводная"]
    before = karier_budget(geology, geodesy, ecology, hydromet, 1.0).summary()
    by_code = {m.module_code: m for m in before["modules"]}
    for code, (cell, _kp) in SVOD_CELLS.items():
        # у разделов ИГИ — за вычетом объяснённой ячейки H143 своего листа
        error = sheet_error_h143(wb["№" + code[8:] + "_ИГИ"]) if code.startswith("geology:") else 0
        expected = num(ws, cell) - error * by_code[code].index
        assert by_code[code].reduced_total == pytest.approx(expected, rel=0.001), code
    # раздел целиком — в пределах 0,1 % и без поправки (три ошибки листа ≈ 0,1 %) …
    assert before["sections_total"] == pytest.approx(num(ws, "D24"), rel=0.001)
    # … а с поправкой недобор — только округления листов (десятки рублей на 28 млн)
    explained = sum(sheet_error_h143(wb[s]) for s in GEOLOGY_SHEETS) * 81.95
    assert before["sections_total"] == pytest.approx(num(ws, "D24") - explained, abs=300)

    assert num(ws, "C39") == 0.85
    after = karier_budget(geology, geodesy, ecology, hydromet, num(ws, "C39"))
    assert after.summary()["reduced_total"] == pytest.approx(num(ws, "X24"), rel=0.001)
    for code, (_cell, kp) in SVOD_CELLS.items():
        error = sheet_error_h143(wb["№" + code[8:] + "_ИГИ"]) if code.startswith("geology:") else 0
        expected = num(ws, kp) - error * by_code[code].index * num(ws, "C39")
        assert after.final_price(code) == pytest.approx(expected, rel=0.001), code
    # НДС 22 % — от суммы после снижения (F40 = F39 × 0,22)
    summary = after.summary()
    assert summary["vat_amount_sum"] == pytest.approx(summary["reduced_total"] * 0.22, rel=1e-9)


# --- Ревью трека 2: предупреждение о смене ступени экспертизы ----------------

def _geodesy_warnings(wb, book, obj, edits):
    from modules.geodesy.module import GeodesyModule

    volume_input, estimate_input, estimate_edits = geodesy_input(wb["№2.1_ИГДИ"])
    *_rest, warnings = run_staged(GeodesyModule(), obj, book, volume_input, estimate_input,
                                  estimate_edits={**estimate_edits, **edits})
    return [w for w in warnings if "ступень" in w]


def test_manual_expertise_rate_edit_is_not_a_bracket_change(wb, book, obj):
    # ставка 1,95 % → 2 % оператором: итог в той же ступени (500 тыс.–1 млн)
    assert _geodesy_warnings(wb, book, obj, {"geodesy.expertise": ("rate", 0.02)}) == []


def test_edit_moving_total_over_1_million_warns_bracket_change(wb, book, obj):
    # цена базы экспедиции поднята правкой — итог после правок свыше 1 млн, а
    # строки экспертизы — по ступени сметы до правок
    warnings = _geodesy_warnings(wb, book, obj, {"geodesy.base": ("price", 200_000)})
    assert len(warnings) == 1
    assert "своей строкой" in warnings[0] and "поправьте ставку и постоянную часть" in warnings[0]
