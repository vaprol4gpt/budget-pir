"""Э9 (остаток, 03.11.2026): подбор коэффициента снижения сводной под целевую
цену с НДС и лист «КП» в Excel сводного бюджета.

Формула подбора (`BudgetAggregator.fit_budget_reduction`): итог с НДС линеен по
коэффициенту сводной k (каждая строка раздела после снижения раздела × k, НДС —
от суммы после снижения), поэтому k = целевая / итог с НДС при k = 1. Коэффициент
не округляется (как у компании) — итог совпадает с целевой до копейки."""

import openpyxl
import pytest

import core.persistence as persistence_module
from app.ui import TARGET_ABOVE_ESTIMATE
from core.aggregator import KP_HEADERS, KP_SHEET, BudgetAggregator
from tests.test_aggregator import make_module_budget
from tests.test_streamlit_app import _budget_table, _ecology_confirmed_with_custom_line, goto, open_app
from tests.test_ui_guide import primary_buttons


def two_modules(budget_reduction=1.0, geology_reduction=1.0, **kwargs) -> BudgetAggregator:
    aggregator = BudgetAggregator(budget_reduction=budget_reduction, **kwargs)
    aggregator.add_module(make_module_budget(reduction=geology_reduction))
    aggregator.add_module(make_module_budget(
        module_code="geodesy", module_name="Геодезия", base_total=500.0,
        indexed_total=3594.65, vat_amount=790.82, total_with_vat=4385.47,
    ))
    return aggregator


def fitted_total(aggregator: BudgetAggregator, k: float) -> float:
    """Итог с НДС того же набора разделов при коэффициенте сводной k."""
    fitted = BudgetAggregator(budget_reduction=k)
    for m in aggregator.summary()["modules"]:
        fitted.add_module(m)
    return fitted.summary()["total_with_vat_sum"]


@pytest.mark.parametrize("share", [0.9999, 0.75, 0.5, 0.123457, 0.0137])
@pytest.mark.parametrize("geology_reduction", [1.0, 0.65])
def test_fit_gives_target_to_the_kopeck(share, geology_reduction):
    aggregator = two_modules(geology_reduction=geology_reduction)
    before = aggregator.total_before_budget_reduction()
    target = round(before * share, 2)  # «некруглая» цена в рублях с копейками
    fit = aggregator.fit_budget_reduction(target)
    assert fit.message is None
    assert fit.coefficient == pytest.approx(target / before)
    assert 0 < fit.coefficient < 1
    assert fitted_total(aggregator, fit.coefficient) == pytest.approx(target, abs=0.005)
    assert abs(fit.residual) < 0.005


def test_fit_accounts_for_section_reduction():
    """Снижение раздела уже внутри: k считается от итога ПОСЛЕ него."""
    plain, reduced = two_modules(), two_modules(geology_reduction=0.65)
    target = 60000.0
    assert reduced.total_before_budget_reduction() == pytest.approx(
        98340.0 * 0.65 + 4385.47)
    assert reduced.fit_budget_reduction(target).coefficient > plain.fit_budget_reduction(target).coefficient


def test_fit_ignores_current_budget_reduction():
    """Подбор не зависит от уже введённого коэффициента сводной."""
    assert two_modules(budget_reduction=0.8).fit_budget_reduction(70000.0).coefficient == \
        pytest.approx(two_modules().fit_budget_reduction(70000.0).coefficient)


@pytest.mark.parametrize("extra", [0.0, 0.01, 1e6])
def test_target_not_below_estimate_needs_no_reduction(extra):
    aggregator = two_modules()
    target = aggregator.total_before_budget_reduction() + extra
    fit = aggregator.fit_budget_reduction(target)
    assert fit.coefficient == 1.0
    assert "Снижение не нужно" in fit.message
    assert fit.residual == pytest.approx(extra)


def test_fit_without_priced_modules_and_bad_target():
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(indexed_total=None, vat_amount=None, total_with_vat=None))
    aggregator.add_module(make_module_budget(module_code="geodesy", confirmed=False))
    assert aggregator.total_before_budget_reduction() is None
    fit = aggregator.fit_budget_reduction(1000.0)
    assert fit.coefficient is None and "Подобрать нельзя" in fit.message
    for bad in (0, -5.0):
        with pytest.raises(ValueError):
            two_modules().fit_budget_reduction(bad)


def test_fit_on_control_budget():
    """Контрольный объект run_budget_stub (28 680 880,97): подбор под круглую
    цену даёт её до копейки."""
    from core.references import DEFAULT_DB_PATH, ReferenceBook

    if not DEFAULT_DB_PATH.exists():
        pytest.skip("нет data/references.sqlite")
    import run_budget_stub

    aggregator = run_budget_stub.build_budget(BudgetAggregator(), ReferenceBook(), "2026-09-30")
    assert round(aggregator.total_before_budget_reduction(), 2) == 28680880.97
    for target in (25_000_000.0, 21_500_000.0, 28_680_880.96):
        fit = aggregator.fit_budget_reduction(target)
        assert round(fitted_total(aggregator, fit.coefficient), 2) == target


# --- лист «КП» ---


def kp_rows(aggregator, tmp_path):
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path))
    wb = openpyxl.load_workbook(path)
    assert wb.active.title == "Сводный бюджет"  # прежний лист остаётся первым
    return wb, list(wb[KP_SHEET].iter_rows(values_only=True))


def test_kp_sheet_without_reduction_has_coefficient_one(tmp_path):
    aggregator = two_modules()
    _, rows = kp_rows(aggregator, tmp_path)
    assert rows[2][:5] == tuple(KP_HEADERS[:5])
    group, *sections = rows[3:6]
    assert group[1].startswith("Инженерные изыскания")
    # по нумерации сводной компании: 1.1 геодезия раньше 1.2.1 геологии
    assert [(r[0], r[1], r[3]) for r in sections] == [("1.1", "Геодезия", 1.0), ("1.2.1", "Геология", 1.0)]
    total = next(r for r in rows if r[1] == "ИТОГО")
    assert total[6] == pytest.approx(aggregator.summary()["total_with_vat_sum"])
    assert total[6] == pytest.approx(98340.0 + 4385.47)
    assert all(r[4] == r[2] for r in sections)


def test_kp_sheet_numbers_equal_summary_after_both_reductions(tmp_path):
    aggregator = two_modules(budget_reduction=0.7547, geology_reduction=0.65)
    summary = aggregator.summary()
    _, rows = kp_rows(aggregator, tmp_path)
    sections = rows[4:6]
    geology = next(r for r in sections if r[1] == "Геология")
    assert geology[3] == pytest.approx(0.65 * 0.7547)  # общий коэффициент раздела
    assert geology[4] == pytest.approx(81950.0 * 0.65 * 0.7547)
    total = next(r for r in rows if r[1] == "ИТОГО")
    assert total[4] == pytest.approx(summary["reduced_total"])
    assert total[5] == pytest.approx(summary["vat_amount_sum"])
    assert total[6] == pytest.approx(summary["total_with_vat_sum"])
    assert sum(r[6] for r in sections) == pytest.approx(total[6])
    assert sum(r[4] for r in sections) + sum(r[5] for r in sections) == pytest.approx(total[6])
    assert ("Коэффициент снижения сводной" in [r[1] for r in rows])
    assert not any(r[1] and "мобилизация" in str(r[1]) for r in rows)


def test_kp_sheet_mobilization_rows_only_when_shown(tmp_path):
    aggregator = BudgetAggregator(show_mobilization=True)
    aggregator.add_module(make_module_budget(index=81.95, mobilization_base=10.0))
    _, rows = kp_rows(aggregator, tmp_path)
    mob = [r for r in rows if r[1] and "мобилизация" in str(r[1])]
    assert len(mob) == 2
    assert mob[0][4] == pytest.approx(10.0 * 81.95 / 2)


def test_kp_sheet_design(tmp_path):
    wb, rows = kp_rows(two_modules(budget_reduction=0.8), tmp_path)
    ws = wb[KP_SHEET]
    header, total_row = 3, next(i for i, r in enumerate(rows, 1) if r[1] == "ИТОГО")
    assert ws.cell(header, 1).fill.fgColor.rgb.endswith("E7ECEC")
    assert ws.cell(header, 1).border.left.color.rgb.endswith("D3DBDA")
    assert ws.cell(total_row, 7).fill.fgColor.rgb.endswith("DDF1EE")
    assert ws.cell(total_row, 7).number_format == "#,##0.00"
    assert ws.cell(header + 2, 4).number_format == "0.0000"
    assert {ws.cell(r, c).font.name for r in range(1, total_row + 1) for c in range(1, 8)} == {"Arial"}


def test_no_kp_sheet_without_priced_modules(tmp_path):
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(indexed_total=None, vat_amount=None, total_with_vat=None))
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path))
    assert KP_SHEET not in openpyxl.load_workbook(path).sheetnames


# --- страница «5. Сводный бюджет» ---


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


def test_page5_fit_puts_coefficient_into_field_and_total_equals_target(isolated_smeta_db):
    at = open_app()
    _ecology_confirmed_with_custom_line(at)
    goto(at, "svodny")
    assert at.exception == []
    before = _budget_table(at)["Итого с НДС"].iloc[-1]
    target = round(before * 0.75, 2)

    at.number_input(key="target_price_with_vat").set_value(target)
    at.button(key="fit_target_price").click()
    at.run(timeout=30)
    assert at.exception == []
    k = at.number_input(key="obj_red_budget").value
    assert k == pytest.approx(target / before, rel=1e-6)
    assert _budget_table(at)["Итого с НДС"].iloc[-1] == pytest.approx(target, abs=0.005)
    assert at.session_state["_run_context"].object_data.price_reductions == {"budget": k}
    assert any("Коэффициент сводной подобран" in s.value for s in at.success)
    # главная кнопка страницы — одна и прежняя, «Подобрать» — второстепенная
    assert primary_buttons(at) == [("Скачать сводный бюджет (Excel)", True)]
    assert at.button(key="fit_target_price").proto.type != "primary"

    # оператор правит коэффициент вручную — видит расхождение с целевой
    at.number_input(key="obj_red_budget").set_value(0.8)
    at.run(timeout=30)
    assert any("выше целевой цены" in c.value for c in at.caption)


def test_page5_target_above_estimate_keeps_coefficient_one(isolated_smeta_db):
    at = open_app()
    _ecology_confirmed_with_custom_line(at)
    goto(at, "svodny")
    before = _budget_table(at)["Итого с НДС"].iloc[-1]
    at.number_input(key="target_price_with_vat").set_value(before + 1000.0)
    at.button(key="fit_target_price").click()
    at.run(timeout=30)
    assert at.exception == []
    assert at.number_input(key="obj_red_budget").value == 1.0
    assert any(TARGET_ABOVE_ESTIMATE in s.value for s in at.info)
    assert at.session_state["_run_context"].object_data.price_reductions == {}


def test_page5_target_above_estimate_keeps_manual_coefficient(isolated_smeta_db):
    """Хвост «дальше» №1: целевая цена ≥ итога с НДС при k = 1 — подбирать нечего,
    введённый вручную коэффициент сводной НЕ затирается единицей."""
    at = open_app()
    _ecology_confirmed_with_custom_line(at)
    goto(at, "svodny")
    before = _budget_table(at)["Итого с НДС"].iloc[-1]
    at.number_input(key="obj_red_budget").set_value(0.8)
    at.run(timeout=30)
    for target in (before * 2, before):  # выше сметы и ровно смета (при k = 1)
        at.number_input(key="target_price_with_vat").set_value(round(target, 2) + 0.01)
        at.button(key="fit_target_price").click()
        at.run(timeout=30)
        assert at.exception == []
        assert at.number_input(key="obj_red_budget").value == pytest.approx(0.8)
        assert at.session_state["_run_context"].object_data.price_reductions == {"budget": 0.8}
        assert any(TARGET_ABOVE_ESTIMATE in s.value for s in at.info)
        assert not any("подобран" in s.value for s in at.success)
    # итог — по ручному коэффициенту, под полем — расхождение с целевой
    assert _budget_table(at)["Итого с НДС"].iloc[-1] == pytest.approx(before * 0.8, abs=0.01)
    assert any("ниже целевой цены" in c.value for c in at.caption)


def test_page5_fit_without_target_asks_for_it(isolated_smeta_db):
    at = open_app()
    _ecology_confirmed_with_custom_line(at)
    goto(at, "svodny")
    at.button(key="fit_target_price").click()
    at.run(timeout=30)
    assert at.exception == []
    assert any("Введите целевую цену" in w.value for w in at.warning)
    assert at.number_input(key="obj_red_budget").value == 1.0


def test_page5_fit_button_disabled_without_budget(isolated_smeta_db):
    at = open_app("svodny")
    assert at.exception == []
    assert at.button(key="fit_target_price").disabled is True
