"""Хвост прогона «дальше» №2: целевая цена ниже минимального бюджета —
«Работа в убыток на N руб.» на странице «5. Сводный бюджет» и на листе «КП».

Сравнение — без НДС (минимум — себестоимость без НДС): целевой цене с НДС
соответствует k* = целевая / итог с НДС при k = 1, цена разделов с минимумом
без НДС = k* × их сумма после снижения раздела (`core/target_loss.py`)."""

import io

import openpyxl
import pytest

import core.aggregator as aggregator_module
import core.persistence as persistence_module
from core.aggregator import KP_SHEET, BudgetAggregator
from core.cost import ObjectMinimum, SectionShare, SharedOnce
from core.target_loss import LOSS_PREFIX, NOT_CALIBRATED_NOTE, SECTION_PREFIX, target_loss
from tests.test_aggregator import make_module_budget
from tests.test_streamlit_app import (
    FULL_COST,
    _budget_table,
    _ecology_confirmed_with_custom_line,
    _geology_confirmed_estimate,
    _set_cost,
    _texts,
    goto,
    open_app,
)
from tests.test_target_price import two_modules

LABELS = {"geology": "Геология", "geodesy": "Геодезия"}


def omin(total, costs, calibrated=False, incomplete=(), shared=()):
    """Минимум объекта (класс трека «минимум», core/cost.py): `costs` — код → минимум раздела."""
    return ObjectMinimum(total, tuple(SectionShare(c, LABELS[c], v) for c, v in costs.items()),
                         tuple(incomplete), tuple(shared), False, calibrated)


GEOLOGY_MIN = omin(50000.0, {"geology": 50000.0})


def geology_only(**kwargs) -> BudgetAggregator:
    aggregator = BudgetAggregator(**kwargs)
    aggregator.add_module(make_module_budget())  # 81 950 без НДС, 98 340 с НДС (20 %)
    return aggregator


def test_full_coverage_compares_target_without_vat():
    aggregator = geology_only()
    # целевая с НДС 54 000 → без НДС 45 000 (НДС 20 % в make_module_budget) < 50 000
    loss = target_loss(aggregator, 54000.0, GEOLOGY_MIN)
    assert loss.price_without_vat == pytest.approx(45000.0)
    assert loss.loss == pytest.approx(5000.0)
    assert loss.text.startswith(f"{LOSS_PREFIX} 5 000,00 руб.")
    assert "только по" not in loss.text and "ниже минимума" in loss.text
    assert NOT_CALIBRATED_NOTE in loss.text.lower()
    # ровно на минимуме и выше — убытка нет
    assert target_loss(aggregator, 60000.0, GEOLOGY_MIN) is None
    assert target_loss(aggregator, 60000.0 + 0.01, GEOLOGY_MIN) is None


def test_without_target_compares_current_price_after_reduction():
    """Целевой нет — одно предупреждение по текущей цене после снижения (раньше —
    отдельное «ниже минимума» только по геологии)."""
    assert target_loss(geology_only(), None, GEOLOGY_MIN) is None  # снижения нет
    loss = target_loss(geology_only(budget_reduction=0.5), None, GEOLOGY_MIN)
    assert loss.price_without_vat == pytest.approx(81950.0 * 0.5)
    assert "цена после снижения" in loss.text and "не ниже 0,610" in loss.text  # 50 000 / 81 950
    assert target_loss(geology_only(budget_reduction=0.7), None, GEOLOGY_MIN) is None
    # целевая важнее текущего коэффициента, пока коэффициент в поле не даёт цену ниже неё
    a = target_loss(geology_only(), 54000.0, GEOLOGY_MIN)
    b = target_loss(geology_only(budget_reduction=0.6), 54000.0, GEOLOGY_MIN)  # k* = 0,549 < 0,6
    assert a.loss == pytest.approx(b.loss) and "целевая цена" in b.text
    reduced = BudgetAggregator()
    reduced.add_module(make_module_budget(reduction=0.65))
    assert target_loss(reduced, 54000.0, GEOLOGY_MIN).price_without_vat == pytest.approx(45000.0)


def test_sum_of_two_sections_shared_once_and_section_below_own_minimum():
    aggregator = two_modules()  # геология 81 950 / 98 340 + геодезия 3 594,65 / 4 385,47
    shared = (SharedOnce("delivery", "Общие: доставка", 1000.0, "Геология", 1085.2),)
    # сумма минимумов 60 000 + 2 000 = 62 000, общая доставка исключена у геодезии один раз
    minimum = omin(62000.0 - 1085.2, {"geology": 60000.0, "geodesy": 2000.0}, shared=shared)
    before = aggregator.total_before_budget_reduction()
    loss = target_loss(aggregator, before * 0.7, minimum)
    assert loss.price_without_vat == pytest.approx((81950.0 + 3594.65) * 0.7)
    assert loss.minimum == pytest.approx(60914.8)
    assert "учтены в минимуме один раз" in loss.text and "только по" not in loss.text
    # объект не в убытке, но геология ниже своего минимума — своё предупреждение
    loss = target_loss(aggregator, before * 0.73, minimum)
    assert loss.loss == 0 and loss.text.startswith(SECTION_PREFIX)
    assert "Геология —" in loss.text and "Геодезия —" not in loss.text


def test_partial_and_incomplete_sections_are_named():
    aggregator = two_modules()
    before = aggregator.total_before_budget_reduction()
    minimum = omin(45000.0, {"geology": 45000.0},
                   incomplete=[("Геодезия", ("оклады", "месяцы"))])
    loss = target_loss(aggregator, before * 0.5, minimum, skipped=("Экология",))
    assert loss.price_without_vat == pytest.approx(81950.0 * 0.5)
    assert "Минимум посчитан только по: Геология" in loss.text
    assert "«Геодезия» неполный (не заполнено: оклады, месяцы)" in loss.text
    assert "вне бюджета (Экология)" in loss.text


@pytest.mark.parametrize("target, minimum", [
    (None, GEOLOGY_MIN), (0.0, GEOLOGY_MIN), (-1000.0, GEOLOGY_MIN), (1000.0, None),
    (1000.0, omin(None, {})),
    (1000.0, omin(50000.0, {"geodesy": 50000.0})),  # минимум не по разделам бюджета
])
def test_nothing_to_compare(target, minimum):
    assert target_loss(geology_only(), target, minimum) is None


def test_no_priced_modules():
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(indexed_total=None, vat_amount=None, total_with_vat=None))
    assert target_loss(aggregator, 1000.0, GEOLOGY_MIN) is None


# --- лист «КП» ---


def test_kp_sheet_shows_loss_warning(tmp_path):
    aggregator = geology_only()
    loss = target_loss(aggregator, 54000.0, GEOLOGY_MIN)
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path), kp_warnings=[loss.text])
    ws = openpyxl.load_workbook(path)[KP_SHEET]
    cells = [c for row in ws.iter_rows() for c in row if isinstance(c.value, str) and LOSS_PREFIX in c.value]
    assert [c.value for c in cells] == [loss.text]
    assert cells[0].font.bold and cells[0].font.color.rgb.endswith(aggregator_module.KP_WARNING_COLOR)
    # без предупреждений — лист прежний
    aggregator.to_excel(str(tmp_path / "plain.xlsx"))
    plain = openpyxl.load_workbook(tmp_path / "plain.xlsx")[KP_SHEET]
    assert not any(LOSS_PREFIX in str(c.value) for row in plain.iter_rows() for c in row)


# --- страница «5. Сводный бюджет» ---


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


@pytest.fixture
def kp_capture(monkeypatch):
    """Перехват выгрузки сводного бюджета: какие предупреждения ушли на лист «КП»
    и сам лист (настоящий файл в памяти)."""
    calls = []
    original = BudgetAggregator.to_excel

    def recording(self, path, info=None, kp_warnings=None):
        buffer = io.BytesIO()
        original(self, buffer, info=info, kp_warnings=kp_warnings)
        calls.append((kp_warnings, openpyxl.load_workbook(io.BytesIO(buffer.getvalue()))))
        return original(self, path, info=info, kp_warnings=kp_warnings)

    monkeypatch.setattr(BudgetAggregator, "to_excel", recording)
    return calls


def _with_distance():
    """Приложение с расстоянием до объекта (нужно себестоимости — трал) и датой
    расчёта в известном периоде индекса."""
    import datetime

    at = open_app("obekt")
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.number_input(key="obj_distance_km").set_value(22.0)
    at.run(timeout=30)
    goto(at, "vedomost")
    return at


def _geology_with_minimum(at):
    goto(at, "vedomost")
    at.radio(key="module_choice").set_value("geology")
    at.run(timeout=30)
    _geology_confirmed_estimate(at)
    goto(at, "minimum")
    _set_cost(at, **FULL_COST)
    goto(at, "svodny")
    assert at.exception == []


def _kp_texts(workbook):
    return [str(c.value) for row in workbook[KP_SHEET].iter_rows() for c in row if c.value]


def test_page5_target_below_minimum_warns_on_page_and_kp(isolated_smeta_db, kp_capture):
    at = _with_distance()
    _geology_with_minimum(at)
    # целевой и снижения нет — предупреждение только если себестоимость выше самой сметы
    assert all("снижение невозможно" in w for w in _texts(at.warning) if LOSS_PREFIX in w)
    before = _budget_table(at)["Итого с НДС"].iloc[-1]

    at.number_input(key="target_price_with_vat").set_value(round(before * 0.01, 2))
    at.run(timeout=30)
    assert at.exception == []
    warnings = [w for w in _texts(at.warning) if LOSS_PREFIX in w]
    assert len(warnings) == 1
    assert "только по" not in warnings[0]  # в бюджете одна геология — минимум по всем разделам
    assert NOT_CALIBRATED_NOTE in warnings[0].lower()
    kp_warnings, workbook = kp_capture[-1]
    assert kp_warnings == warnings
    assert warnings[0] in _kp_texts(workbook)

    # целевая выше сметы — цена ограничена сметой (ревью №1): убыток только если
    # себестоимость выше самой сметы, и тогда — с оговоркой; иначе предупреждения нет
    at.number_input(key="target_price_with_vat").set_value(round(before * 2, 2))
    at.run(timeout=30)
    warnings = [w for w in _texts(at.warning) if LOSS_PREFIX in w]
    kp_warnings, workbook = kp_capture[-1]
    if warnings:
        assert len(warnings) == 1 and "ограничена сметой" in warnings[0]
        assert kp_warnings == warnings
    else:
        assert kp_warnings is None
        assert not any(LOSS_PREFIX in t for t in _kp_texts(workbook))



def test_page5_loss_with_partial_minimum_names_sections(isolated_smeta_db, kp_capture):
    at = _with_distance()
    _ecology_confirmed_with_custom_line(at)
    _geology_with_minimum(at)
    assert len(_budget_table(at)) == 3  # геология, экология, ИТОГО
    before = _budget_table(at)["Итого с НДС"].iloc[-1]
    at.number_input(key="target_price_with_vat").set_value(round(before * 0.01, 2))
    at.run(timeout=30)
    assert at.exception == []
    warnings = [w for w in _texts(at.warning) if LOSS_PREFIX in w]
    assert len(warnings) == 1 and "Минимум посчитан только по: Геология" in warnings[0]
    assert kp_capture[-1][0] == warnings


def test_target_above_estimate_is_capped_by_estimate():
    """Ревью №1: целевая выше сметы — цену выше сметы снижением не получить,
    сравнивается сама смета (k* = 1), с оговоркой."""
    aggregator = geology_only()  # 81 950 без НДС
    minimum = omin(100000.0, {"geology": 100000.0})
    loss = target_loss(aggregator, 98340.0 * 1.1, minimum)
    assert loss.price_without_vat == pytest.approx(81950.0)
    assert loss.loss == pytest.approx(100000.0 - 81950.0)
    assert "ограничена сметой" in loss.text
    below = target_loss(aggregator, 54000.0, minimum)
    assert "ограничена сметой" not in below.text


def test_kp_loss_row_spans_table_width(tmp_path):
    """Ревью №6: строка убытка — объединённые ячейки B:G, перенос и высота под текст."""
    aggregator = geology_only()
    text = target_loss(aggregator, 54000.0, GEOLOGY_MIN).text
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path), kp_warnings=[text])
    ws = openpyxl.load_workbook(path)[KP_SHEET]
    row = next(c.row for r in ws.iter_rows() for c in r if c.value == text)
    assert f"B{row}:G{row}" in {str(m) for m in ws.merged_cells.ranges}
    assert ws.cell(row, 2).alignment.wrap_text
    assert ws.row_dimensions[row].height >= 28  # текст длиннее одной строки


def _number(text):
    """«1 234 567,89» (с обычными / неразрывными пробелами) → число."""
    return float(text.replace("\xa0", "").replace(" ", "").replace("руб.", "").replace(",", "."))


def test_page5_loss_by_two_sections_shared_once_and_kp(isolated_smeta_db, kp_capture):
    """Связка с минимумом всех разделов: предупреждение считается по сумме минимумов
    геологии и геофизики, общая затрата (организация и ликвидация) — один раз;
    тот же текст — на листе «КП»; отдельного «ниже минимума геологии» нет."""
    from tests.test_minimum_razdely_ui import SALARIES

    at = _with_distance()
    _geology_with_minimum(at)
    goto(at, "vedomost")
    at.radio(key="module_choice").set_value("geophysics")
    at.run(timeout=30)
    at.number_input(key="field_geophysics_vez_count").set_value(80)
    at.number_input(key="field_geophysics_mpv_count").set_value(400)
    at.run(timeout=30)
    at.button(key="confirm_geophysics_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    at.button(key="confirm_geophysics_estimate").click()
    at.run(timeout=30)
    goto(at, "minimum")
    for name, value in SALARIES.items():
        at.number_input(key=f"cost_common_{name}").set_value(value)
    at.number_input(key="cost_geophysics_office_months").set_value(1.0)
    at.number_input(key="cost_shared_mobilization").set_value(300000.0)
    at.run(timeout=30)
    at.button(key="suggest_party_geophysics").click()
    at.run(timeout=30)
    assert at.exception == []

    goto(at, "svodny")
    assert at.exception == []
    assert len(_budget_table(at)) == 3  # геология, геофизика, ИТОГО
    metrics = {m.label: m.value for m in at.metric}
    object_min = _number(metrics["Объект — минимум (себестоимость)"])
    table = next(d.value for d in at.dataframe if "Минимум, руб." in getattr(d.value, "columns", []))
    rows = {r["Раздел"]: r for r in table.to_dict("records")}
    geo_min = float(rows["Геология (ОИГ)"]["Минимум, руб."])
    gph_min = float(rows["Геофизика (ИГФИ)"]["Минимум, руб."])
    assert object_min < geo_min + gph_min  # общая затрата не задвоена

    at.number_input(key="obj_red_budget").set_value(0.05)
    at.run(timeout=30)
    assert at.exception == []
    warnings = [w for w in _texts(at.warning) if LOSS_PREFIX in w or "ниже минимума" in w]
    assert len(warnings) == 1  # одно предупреждение, а не два (геология + объект)
    text = warnings[0]
    assert text.startswith(LOSS_PREFIX) and "цена после снижения" in text
    assert "Геология (ОИГ)" in text and "Геофизика (ИГФИ)" in text
    assert "только по" not in text
    assert "учтены в минимуме один раз" in text
    assert f"{object_min:,.2f}".replace(",", " ").replace(".", ",") in text
    assert kp_capture[-1][0] == [text] and text in _kp_texts(kp_capture[-1][1])


# --- ревью стыка (f03b0d2): советы, тексты, оговорки ---


def test_minimum_above_estimate_reduction_impossible():
    """Сценарий А: себестоимость выше самой сметы — «снижение невозможно», а не совет
    «коэффициент сводной не ниже 1,098»; так же без снижения и без целевой."""
    for aggregator in (geology_only(budget_reduction=0.9), geology_only()):
        loss = target_loss(aggregator, None, omin(90000.0, {"geology": 90000.0}))
        assert "снижение невозможно" in loss.text and "не ниже" not in loss.text


def test_section_reduction_too_deep_advice_is_section_not_budget():
    """Сценарий Б: раздел ×0,65, сводная ×0,95, минимум 60 000 < сметы 81 950 —
    совет уменьшить снижение раздела, а не «сводная не ниже 1,126»."""
    aggregator = BudgetAggregator(budget_reduction=0.95)
    aggregator.add_module(make_module_budget(reduction=0.65))
    loss = target_loss(aggregator, None, omin(60000.0, {"geology": 60000.0}))
    assert "уменьшите снижение раздела" in loss.text
    assert "не ниже 1," not in loss.text and "снижение невозможно" not in loss.text


def test_texts_punctuation_and_no_repeat_for_one_section():
    loss = target_loss(geology_only(budget_reduction=0.5), None, GEOLOGY_MIN)
    assert ".." not in loss.text
    import re

    sentences = [x for x in re.split(r"(?<!руб)\. ", loss.text) if x]  # «руб.» — не конец
    assert all(x[0].isupper() for x in sentences)
    assert "ниже минимума раздела" not in loss.text  # раздел один — повтор первого предложения


def test_incomplete_section_named_once():
    aggregator = two_modules()
    loss = target_loss(aggregator, aggregator.total_before_budget_reduction() * 0.5,
                       omin(45000.0, {"geology": 45000.0}, incomplete=[("Геодезия", ("оклады",))]))
    assert loss.text.count("Геодезия") == 1 and "не проверено" not in loss.text


def test_no_complete_minimum_says_so_instead_of_silence():
    minimum = omin(None, {}, incomplete=[("Геология", ("оклады",))])
    loss = target_loss(geology_only(budget_reduction=0.8), None, minimum)
    assert loss.text.startswith("Минимум не посчитан") and "«Геология»" in loss.text
    assert target_loss(geology_only(), 54000.0, minimum) is not None
    # сравнивать не с чем и нечего (нет ни целевой, ни снижения) — молчим: блок минимума скажет
    assert target_loss(geology_only(), None, minimum) is None


# --- аудит 05.11.2026, О-4: целевая цена задана, а в поле сводной — более сильное снижение ---


@pytest.mark.parametrize("target", [
    98340.0 * 2,   # целевая выше сметы: подбор коэффициент в поле не трогает
    98340.0 * 0.9,  # целевая сама не в убыток (73 755 без НДС при минимуме 50 000)
])
def test_field_coefficient_below_target_is_checked(target):
    """Раньше при введённой целевой цене проверялась только она: в поле сводной 0,5 —
    цена «КП» 40 975 без НДС при минимуме 50 000, а предупреждения не было."""
    aggregator = geology_only(budget_reduction=0.5)
    loss = target_loss(aggregator, target, GEOLOGY_MIN)
    assert loss is not None
    assert loss.price_without_vat == pytest.approx(81950.0 * 0.5)
    assert loss.loss == pytest.approx(50000.0 - 81950.0 * 0.5)
    assert loss.text.startswith(LOSS_PREFIX) and "цена после снижения" in loss.text
    assert "в поле коэффициента сводной стоит 0,5000" in loss.text
    assert "не ниже 0,610" in loss.text
    assert "ограничена сметой" not in loss.text
    # то же предупреждение, что и без целевой цены, плюс пояснение про поле
    assert loss.loss == pytest.approx(target_loss(aggregator, None, GEOLOGY_MIN).loss)


def test_target_below_field_price_is_still_checked_by_target():
    """Целевая ниже цены по полю — как раньше: предупреждение по целевой цене."""
    loss = target_loss(geology_only(budget_reduction=0.9), 54000.0, GEOLOGY_MIN)
    assert loss.price_without_vat == pytest.approx(45000.0)
    assert "целевая цена" in loss.text and "в поле коэффициента" not in loss.text


def test_fitted_coefficient_equal_to_target_is_checked_by_target():
    aggregator = geology_only()
    aggregator.budget_reduction = aggregator.fit_budget_reduction(54000.0).coefficient
    loss = target_loss(aggregator, 54000.0, GEOLOGY_MIN)
    assert loss.loss == pytest.approx(5000.0) and "целевая цена" in loss.text


def test_field_coefficient_below_target_without_loss_gives_no_warning():
    assert target_loss(geology_only(budget_reduction=0.7), 98340.0 * 2, GEOLOGY_MIN) is None
