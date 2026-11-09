import openpyxl
import pytest

from core.aggregator import KP_SHEET, BudgetAggregator, ModuleBudget
from core.pricing import TotalWithVat


def make_module_budget(**overrides) -> ModuleBudget:
    defaults = dict(
        module_code="geology",
        module_name="Геология",
        base_total=1000.0,
        indexed_total=81950.0,
        vat_amount=16390.0,
        total_with_vat=98340.0,
        warnings=[],
    )
    defaults.update(overrides)
    return ModuleBudget(**defaults)


def test_summary_with_one_module():
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget())

    summary = aggregator.summary()

    assert summary["base_total_sum"] == 1000.0
    assert summary["total_with_vat_sum"] == 98340.0
    assert summary["excluded_from_total"] == []
    assert len(summary["modules"]) == 1


def test_summary_sums_several_fully_priced_modules():
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(module_code="geology", base_total=1000.0, total_with_vat=98340.0))
    aggregator.add_module(
        make_module_budget(
            module_code="geodesy",
            module_name="Геодезия",
            base_total=500.0,
            indexed_total=40000.0,
            vat_amount=8000.0,
            total_with_vat=48000.0,
        )
    )

    summary = aggregator.summary()

    assert summary["base_total_sum"] == 1500.0
    assert summary["total_with_vat_sum"] == 146340.0
    assert summary["excluded_from_total"] == []


def test_summary_sums_three_modules_with_different_price_bases_correctly():
    # реальная ситуация после подключения геодезии: геология/геофизика
    # считаются от базы 1991 г. (индекс ~82), геодезия — от базы 2004 г.
    # (индекс ~7) — суммы total_with_vat/indexed_total/vat_amount уже
    # в текущих рублях у всех трёх (индексация переводит из СВОЕЙ
    # базы), поэтому их сложение корректно, несмотря на разную базу;
    # BudgetAggregator не пытается сравнивать индексы или модули между
    # собой, просто складывает готовые total_with_vat каждого
    aggregator = BudgetAggregator()
    aggregator.add_module(
        make_module_budget(
            module_code="geology", module_name="Геология",
            base_total=35261.41, indexed_total=2889672.92,
            vat_amount=577934.58, total_with_vat=3467607.51,
        )
    )
    aggregator.add_module(
        make_module_budget(
            module_code="geophysics", module_name="Геофизика",
            base_total=10387.20, indexed_total=851231.04,
            vat_amount=170246.21, total_with_vat=1021477.25,
        )
    )
    aggregator.add_module(
        make_module_budget(
            module_code="geodesy", module_name="Геодезия",
            base_total=64794.64, indexed_total=465873.48,
            vat_amount=93174.70, total_with_vat=559048.17,
        )
    )

    summary = aggregator.summary()

    assert len(summary["modules"]) == 3
    assert summary["excluded_from_total"] == []
    assert summary["base_total_sum"] == pytest.approx(35261.41 + 10387.20 + 64794.64)
    assert summary["total_with_vat_sum"] == pytest.approx(3467607.51 + 1021477.25 + 559048.17)
    # geodesy — самый маленький вклад по сумме, но НЕ исключён и не
    # "подогнан" под индекс других модулей — цифра ровно своя
    geodesy_budget = next(m for m in summary["modules"] if m.module_code == "geodesy")
    assert geodesy_budget.total_with_vat == pytest.approx(559048.17)


def test_summary_excludes_module_without_resolved_total_but_keeps_base_total():
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(module_code="geology", base_total=1000.0, total_with_vat=98340.0))
    aggregator.add_module(
        make_module_budget(
            module_code="geophysics",
            module_name="Геофизика",
            base_total=200.0,
            indexed_total=None,
            vat_amount=None,
            total_with_vat=None,
            warnings=["Индекс пересчёта цен на ... не найден в справочнике"],
        )
    )

    summary = aggregator.summary()

    # база считается по всем модулям, даже без индекса
    assert summary["base_total_sum"] == 1200.0
    # но в сумму "с НДС" попадает только то, что реально посчитано
    assert summary["total_with_vat_sum"] == 98340.0
    assert len(summary["excluded_from_total"]) == 1
    assert summary["excluded_from_total"][0]["module_code"] == "geophysics"
    assert summary["excluded_from_total"][0]["warnings"] == [
        "Индекс пересчёта цен на ... не найден в справочнике"
    ]


def test_from_totals_repackages_total_with_vat_without_recomputing():
    totals = TotalWithVat(
        base_total=100.0,
        index=2.0,
        indexed_total=200.0,
        vat_rate=0.2,
        vat_amount=40.0,
        total_with_vat=240.0,
        warnings=[],
    )

    module_budget = ModuleBudget.from_totals("geology", "Геология", totals)

    assert module_budget.base_total == 100.0
    assert module_budget.indexed_total == 200.0
    assert module_budget.vat_amount == 40.0
    assert module_budget.total_with_vat == 240.0


def test_from_totals_carries_partial_result_as_none_not_invented_number():
    totals = TotalWithVat(
        base_total=100.0,
        index=None,
        indexed_total=None,
        vat_rate=None,
        vat_amount=None,
        total_with_vat=None,
        warnings=["Индекс пересчёта цен не найден"],
    )

    module_budget = ModuleBudget.from_totals("geophysics", "Геофизика", totals)

    assert module_budget.base_total == 100.0
    assert module_budget.indexed_total is None
    assert module_budget.vat_amount is None
    assert module_budget.total_with_vat is None
    assert module_budget.warnings == ["Индекс пересчёта цен не найден"]


def test_to_excel_creates_one_row_per_module_plus_total(tmp_path):
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(module_code="geology", module_name="Геология"))
    aggregator.add_module(
        make_module_budget(
            module_code="geophysics",
            module_name="Геофизика",
            base_total=200.0,
            indexed_total=None,
            vat_amount=None,
            total_with_vat=None,
        )
    )

    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path))

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    # заголовок + 2 модуля + ИТОГО
    assert len(rows) == 4
    assert rows[0] == tuple(
        ["Раздел", "База (в ценах раздела)", "С индексацией", "НДС", "Итого с НДС"]
    )
    assert rows[-1][0] == "ИТОГО"
    # находка ревью №8 (30.10.2026): базы разных лет не складываются — ячейка пустая,
    # базы — по модулям
    assert rows[-1][1] is None
    assert [row[1] for row in rows[1:-1]] == [1000.0, 200.0]


def test_to_excel_with_single_module(tmp_path):
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget())

    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path))

    wb = openpyxl.load_workbook(path)
    rows = list(wb.active.iter_rows(values_only=True))

    # заголовок + 1 модуль + ИТОГО
    assert len(rows) == 3


# --- только подтверждённые и не устаревшие модули (многоэтапность) ---


def test_unconfirmed_and_stale_modules_are_left_out_of_budget(tmp_path):
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(module_code="geophysics", module_name="Геофизика"))
    aggregator.add_module(make_module_budget(module_code="geology", confirmed=False))
    aggregator.add_module(make_module_budget(module_code="geodesy", module_name="Геодезия", stale=True))

    summary = aggregator.summary()
    assert [m.module_code for m in summary["modules"]] == ["geophysics"]
    assert summary["base_total_sum"] == 1000.0
    assert summary["total_with_vat_sum"] == 98340.0
    assert summary["not_confirmed"] == [
        {"module_code": "geology", "module_name": "Геология", "reason": "последний этап не подтверждён"},
        {"module_code": "geodesy", "module_name": "Геодезия", "reason": "последний этап устарел"},
    ]

    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path))
    ws = openpyxl.load_workbook(path).active
    assert [row[0] for row in ws.iter_rows(values_only=True)] == ["Раздел", "Геофизика", "ИТОГО"]


def test_from_totals_carries_stage_status():
    totals = TotalWithVat(
        base_total=1.0, index=1.0, indexed_total=1.0, vat_rate=0.2,
        vat_amount=0.2, total_with_vat=1.2, warnings=[],
    )
    budget = ModuleBudget.from_totals("geology", "Геология", totals, confirmed=True, stale=True)
    assert budget.included is False
    assert ModuleBudget.from_totals("geology", "Геология", totals).included is True


# --- Э3 дорожной карты: экспорт бюджета = run_budget_stub на той же дате ---


def test_to_excel_info_sheet_has_date_index_and_not_confirmed(tmp_path):
    aggregator = BudgetAggregator()
    aggregator.add_module(ModuleBudget("geology", "Геология", 100.0, 8195.0, 1639.0, 9834.0,
                                       index=81.95, index_period="III кв. 2026"))
    aggregator.add_module(ModuleBudget("geodesy", "Геодезия", 10.0, 71.9, 14.38, 86.28, confirmed=False))
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path), info={"Дата расчёта": "30.09.2026"})
    info = list(openpyxl.load_workbook(path)["Сведения"].iter_rows(values_only=True))
    assert info[0] == ("Дата расчёта", "30.09.2026")
    assert ("Индекс: Геология", "81.95 (III кв. 2026)") in info
    assert ("Не вошёл в бюджет: Геодезия", "последний этап не подтверждён") in info


def test_budget_export_total_equals_run_budget_stub(tmp_path):
    from core.references import DEFAULT_DB_PATH, ReferenceBook

    if not DEFAULT_DB_PATH.exists():
        pytest.skip("нет data/references.sqlite")
    import run_budget_stub

    aggregator = run_budget_stub.build_budget(BudgetAggregator(), ReferenceBook(), "2026-09-30")
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path))
    rows = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
    assert rows[-1][0] == "ИТОГО"
    assert round(rows[-1][4], 2) == round(aggregator.summary()["total_with_vat_sum"], 2) == 28680880.97
    assert len(rows) == 1 + 5 + 1


# --- Э9: снижение цены на двух уровнях (раздел и сводная), мобилизация ---


def test_default_reductions_keep_old_sums_exactly():
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget())
    summary = aggregator.summary()
    assert aggregator.has_reduction() is False
    assert summary["total_with_vat_sum"] == 98340.0
    assert summary["sections_total"] == summary["reduced_total"] == 81950.0
    assert summary["vat_amount_sum"] == 16390.0


def test_section_and_budget_reduction_vat_after_reduction():
    aggregator = BudgetAggregator(budget_reduction=0.8)
    aggregator.add_module(make_module_budget(reduction=0.65))
    aggregator.add_module(make_module_budget(
        module_code="geodesy", module_name="Геодезия", indexed_total=10000.0,
        vat_amount=2200.0, total_with_vat=12200.0))
    summary = aggregator.summary()
    assert summary["indexed_total_sum"] == pytest.approx(91950.0)
    assert summary["sections_total"] == pytest.approx(81950.0 * 0.65 + 10000.0)
    assert summary["reduced_total"] == pytest.approx((81950.0 * 0.65 + 10000.0) * 0.8)
    assert summary["vat_amount_sum"] == pytest.approx(
        (16390.0 * 0.65 + 2200.0) * 0.8)
    assert summary["total_with_vat_sum"] == pytest.approx(
        summary["reduced_total"] + summary["vat_amount_sum"])
    assert aggregator.final_price("geology") == pytest.approx(81950.0 * 0.65 * 0.8)


@pytest.mark.parametrize("bad", [0, -0.1, 1.2])
def test_reduction_out_of_range_is_rejected(bad):
    with pytest.raises(ValueError):
        BudgetAggregator(budget_reduction=bad)
    with pytest.raises(ValueError):
        BudgetAggregator().add_module(make_module_budget(reduction=bad))


def test_excel_with_reduction_shows_before_coef_after_and_vat(tmp_path):
    aggregator = BudgetAggregator(budget_reduction=0.8)
    aggregator.add_module(make_module_budget(reduction=0.65))
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path))
    rows = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
    assert rows[0][3] == "Коэффициент снижения раздела"
    assert rows[1][2:4] == (81950.0, 0.65)
    assert rows[1][4] == pytest.approx(81950.0 * 0.65)
    labels = {r[0]: r for r in rows}
    assert labels["Коэффициент снижения сводной"][3] == 0.8
    assert labels["Итого с учётом снижения (без НДС)"][4] == pytest.approx(81950.0 * 0.65 * 0.8)
    assert rows[-1][0] == "ВСЕГО с НДС"
    assert rows[-1][4] == pytest.approx(aggregator.summary()["total_with_vat_sum"])
    assert rows[-2][4] == pytest.approx(aggregator.summary()["vat_amount_sum"])


def _line(line_id, amount=0.0, kind="A", rate=None, base_ids=()):
    from core.models import EstimateLine

    coefs = {"rate": rate} if rate is not None else {}
    return EstimateLine(line_id, line_id, "руб.", 1, amount, coefs, kind, list(base_ids), "тест")


def test_mobilization_base_is_transport_and_org_with_their_regional_share():
    from core.aggregator import mobilization_base

    estimate = {
        "geology.drill": _line("geology.drill", 1000.0),
        "geology.accrual.external_transport": _line(
            "geology.accrual.external_transport", kind="B", rate=0.392, base_ids=["geology.drill"]),
        "geology.accrual.org_liquidation": _line(
            "geology.accrual.org_liquidation", kind="B", rate=0.15, base_ids=["geology.drill"]),
        "geology.accrual.regional_field": _line(
            "geology.accrual.regional_field", kind="B", rate=0.5,
            base_ids=["geology.drill", "geology.accrual.external_transport",
                      "geology.accrual.org_liquidation"]),
    }
    assert mobilization_base(estimate) == pytest.approx((392.0 + 150.0) * 1.5)
    assert mobilization_base({"a": _line("a", 5.0)}) == 0.0


def test_mobilization_display_does_not_change_total(tmp_path):
    plain = BudgetAggregator()
    plain.add_module(make_module_budget(index=81.95))
    shown = BudgetAggregator(show_mobilization=True)
    shown.add_module(make_module_budget(index=81.95, mobilization_base=100.0))
    assert shown.summary()["total_with_vat_sum"] == plain.summary()["total_with_vat_sum"]
    assert shown.summary()["mobilization"] == pytest.approx(8195.0)
    path = tmp_path / "budget.xlsx"
    shown.to_excel(str(path))
    rows = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
    # без НДС — под «С индексацией», а не под «Итого с НДС» (ревью Э9, №3)
    assert rows[-2][0].startswith("в т.ч. мобилизация") and rows[-2][2] == pytest.approx(4097.5)
    assert rows[-2][4] is None
    assert rows[-1][0].startswith("в т.ч. демобилизация")


# --- Э3: сводная «№1ПС_Сводная» из пяти модулей на объёмах эталонов ---

ROAD_XLSX = "Автодорога_эталон.xlsx"
KARIER_XLSX = "Карьер_эталон.xlsx"
SVOD = "№1ПС_Сводная"
DATE = "2026-09-30"


def _book():
    from core.references import DEFAULT_DB_PATH, ReferenceBook

    if not DEFAULT_DB_PATH.exists():
        pytest.skip("нет data/references.sqlite")
    return ReferenceBook(DEFAULT_DB_PATH)


def _workbook(name):
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "reference_data" / name
    if not path.exists():
        pytest.skip("нет эталона в reference_data/")
    return openpyxl.load_workbook(path, data_only=True)


def _ecology_with_prices(book, data, distance, ws, rows):
    """Экология через этапы + свои строки «после индекса» — блок прайсов
    листа («Работы, не учтённые СБЦ-99»), строки с объёмом > 0."""
    from core.models import PLACEMENT_AFTER_INDEX
    from core.object_data import CalculationContext, ObjectData
    from core.overrides import BASIS_PRICE_LIST
    from core.stages import StagedSession, as_staged
    from modules.ecology.module import EcologyModule
    from tests.test_ecology_etalons import SHEET_RATES

    context = CalculationContext(book, ObjectData(distance_km=distance, **SHEET_RATES))
    session = StagedSession(as_staged(EcologyModule()))
    for stage in session.staged.stages:
        session.inputs[stage.key] = {f.name: data[f.name] for f in stage.fields if f.name in data}
    session.confirm("volumes", context)
    for row in rows:
        qty, price = ws[f"D{row}"].value, ws[f"E{row}"].value
        if qty and price is not None:
            session.overrides["estimate"].add_custom_line({
                "name": ws[f"B{row}"].value, "unit": ws[f"C{row}"].value or "шт.", "qty": qty,
                "price": price, "basis_kind": BASIS_PRICE_LIST,
                "basis": f"лист «{ws.title}», строка {row}", "placement": PLACEMENT_AFTER_INDEX,
            })
    return dict(session.confirm("estimate", context).lines)


def _budget(estimates, book, reductions, budget_reduction):
    from core.pricing import calculate_total_with_vat

    aggregator = BudgetAggregator(budget_reduction=budget_reduction)
    for code, estimate in estimates.items():
        totals = calculate_total_with_vat(estimate, book, code, DATE)
        assert totals.warnings == []
        aggregator.add_module(ModuleBudget.from_totals(
            code, code, totals, reduction=reductions.get(code, 1.0)))
    return aggregator


@pytest.fixture(scope="module")
def road_estimates():
    from core.object_data import CalculationContext, ObjectData
    from tests import (
        test_geodesy_etalon as geodesy,
        test_geology_etalon as geology,
        test_geophysics_etalon as geophysics,
        test_hydromet_etalons as hydromet,
    )
    from tests.test_ecology_etalons import ROAD_FULL_INPUT

    book, wb = _book(), _workbook(ROAD_XLSX)
    geophysics_context = CalculationContext(
        book, ObjectData(external_transport_rate=0.065, regional_field_rate=0.66))  # G29, G32
    return {
        "geology": dict(geology.run_sheet(wb["1.2.1_ИГИ"])[0]),
        "geophysics": geophysics._staged(geophysics_context),
        "geodesy": dict(geodesy.run_stages(geodesy.ROAD_VOLUMES, geodesy.ROAD_PARAMETERS)[0]),
        # 22 км — пункт т.4 листа (п.5 «от 20 до 25 км»), блок прайсов — строки 97–113
        "ecology": _ecology_with_prices(book, ROAD_FULL_INPUT, 22, wb["№1.4_ИЭИ"], range(97, 114)),
        "hydromet": hydromet._staged(hydromet.ROAD_VOLUMES, hydromet.ROAD_PARAMETERS, book),
    }, book, wb[SVOD]


def test_road_svodnaya_surveys_section_within_0_1_percent(road_estimates):
    """Критерий Э3: раздел «Инженерные изыскания» сводной «№1ПС_Сводная»
    (D18 = ИГДИ G75 + ИГИ H160 + ИГФИ K35 + ИГМИ F370 + ИЭИ H114) ± 0,1 %
    — ИГМИ со снижением раздела 0,65 (№1.3_ИГМИ!D370); и после понижающего
    коэффициента сводной 0,8 (C36) — столбец «в КП» (G18)."""
    estimates, book, ws = road_estimates
    cells = {"geodesy": "D19", "geology": "D20", "geophysics": "D21",
             "hydromet": "D22", "ecology": "D23"}

    before = _budget(estimates, book, {"hydromet": 0.65}, 1.0)
    by_module = {m.module_code: m for m in before.summary()["modules"]}
    for code, cell in cells.items():  # каждый раздел — тоже ± 0,1 %
        assert by_module[code].reduced_total == pytest.approx(ws[cell].value, rel=1e-3), code
    assert by_module["hydromet"].reduced_total == pytest.approx(ws["D22"].value, abs=0.01)
    assert by_module["ecology"].reduced_total == pytest.approx(ws["D23"].value, abs=0.01)
    assert before.summary()["sections_total"] == pytest.approx(ws["D18"].value, rel=1e-3)

    assert ws["C36"].value == 0.8
    after = _budget(estimates, book, {"hydromet": 0.65}, ws["C36"].value).summary()
    assert after["reduced_total"] == pytest.approx(ws["G18"].value, rel=1e-3)
    # НДС 22 % — от суммы после снижения (F37 = F36 × 0,22)
    assert after["vat_amount_sum"] == pytest.approx(after["reduced_total"] * 0.22, rel=1e-9)
    assert after["total_with_vat_sum"] == pytest.approx(after["reduced_total"] * 1.22, rel=1e-9)


@pytest.fixture(scope="module")
def karier_estimates():
    from tests import test_hydromet_etalons as hydromet
    from tests.test_ecology_etalons import KARIER_FULL_INPUT

    book, wb = _book(), _workbook(KARIER_XLSX)
    return {
        # 12 км — пункт т.4 листа (п.4 «от 10 до 15 км»), прайсы — строки 95–106
        "ecology": _ecology_with_prices(book, KARIER_FULL_INPUT, 12, wb["№2.5_ИЭИ"], range(95, 107)),
        "hydromet": hydromet._staged(hydromet.KARIER_VOLUMES, hydromet.KARIER_PARAMETERS, book),
    }, book, wb[SVOD]


def test_karier_svodnaya_ecology_and_hydromet(karier_estimates):
    """Карьер: из разделов сводной у программы есть эталоны экологии (D30 =
    №2.5_ИЭИ!H107) и ИГМИ (D29) — до снижения и после К = 0,85 (C39,
    столбец «в КП» X = ROUND(F × C39, 2)). Геодезия, три листа ИГИ и ГРР
    Карьера — без своих эталонных тестов, не сверяются."""
    estimates, book, ws = karier_estimates
    assert ws["C39"].value == 0.85
    before = {m.module_code: m for m in _budget(estimates, book, {}, 1.0).summary()["modules"]}
    assert before["ecology"].reduced_total == pytest.approx(ws["D30"].value, abs=0.01)
    assert before["hydromet"].reduced_total == pytest.approx(ws["D29"].value, abs=0.01)
    after = _budget(estimates, book, {}, ws["C39"].value)
    assert after.final_price("ecology") == pytest.approx(ws["X30"].value, abs=0.01)
    assert after.final_price("hydromet") == pytest.approx(ws["X29"].value, abs=0.01)
    assert after.summary()["reduced_total"] == pytest.approx(
        ws["X29"].value + ws["X30"].value, abs=0.02)


def test_excel_total_row_sums_only_modules_with_total(tmp_path):
    """Ревью сводной №4: модуль с индексацией, но без НДС (нет ставки) не
    входит в итог — и в ячейки строки ИТОГО тоже, иначе колонки не сходятся."""
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(module_code="geology", module_name="Геология"))
    aggregator.add_module(make_module_budget(
        module_code="geophysics", module_name="Геофизика", base_total=200.0,
        indexed_total=5000.0, vat_amount=None, total_with_vat=None,
    ))
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path))
    total = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))[-1]
    assert total == ("ИТОГО", None, 81950.0, 16390.0, 98340.0)


def test_excel_reduced_header_says_before_budget_reduction(tmp_path):
    """Ревью сводной №2: «после снижения раздела» в Excel — только раздел; на
    экране та же колонка + отдельно «раздел и сводная»."""
    aggregator = BudgetAggregator(budget_reduction=0.8)
    aggregator.add_module(make_module_budget(reduction=0.65))
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(str(path))
    header = next(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
    assert header[4] == "После снижения раздела (до снижения сводной)"


# --- этап В карты, аудит 05.11.2026, С-1 и С-2: отметки о надёжности итога ---


def _sheet_texts(path, sheet):
    ws = openpyxl.load_workbook(path)[sheet]
    return [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str)]


def _reliable_budget():
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget())
    return aggregator


def test_clean_budget_has_no_reliability_notes(tmp_path):
    aggregator = _reliable_budget()
    assert aggregator.reliability_notes() == []
    path = tmp_path / "b.xlsx"
    aggregator.to_excel(path)
    assert not any(t.startswith("Внимание") for t in _sheet_texts(path, "Сводный бюджет"))


def test_section_without_accruals_is_marked_on_every_sheet(tmp_path):
    text = "Начисления не применены — ставки не заданы: районный к полевым."
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(notes=[text]))
    aggregator.add_module(make_module_budget(module_code="geodesy", module_name="Геодезия", notes=[text]))
    notes = aggregator.reliability_notes()
    assert notes == [f"Геология, Геодезия: {text}"]  # одинаковое — одной строкой
    path = tmp_path / "b.xlsx"
    aggregator.to_excel(path)
    assert notes[0] in _sheet_texts(path, KP_SHEET)
    assert f"Внимание: {notes[0]}" in _sheet_texts(path, "Сводный бюджет")


def test_fallback_index_is_one_line_with_all_sections():
    aggregator = BudgetAggregator()
    for code, name, value in (("geology", "Геология", 81.95), ("geodesy", "Геодезия", 7.19)):
        aggregator.add_module(make_module_budget(
            module_code=code, module_name=name, index_fallback=True, index_period="III кв. 2026",
            warnings=[f"Индекс III кв. 2026 (×{value}) применён к дате 25.10.2026: актуальный индекс "
                      "на эту дату не внесён в справочник."],
        ))
    notes = aggregator.reliability_notes()
    assert len(notes) == 1
    assert "применён III кв. 2026" in notes[0] and "Геология, Геодезия" in notes[0]


def test_assumptions_are_counted_with_object_level():
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(assumptions=2))
    aggregator.object_assumptions = 1
    assert aggregator.reliability_notes() == [
        "Итог содержит 3 допущения в параметрах смет: Геология — 2; данные объекта — 1. Это "
        "значения программы до ответа специалистов (справочник правил параметров, страница "
        "«Справочники»)."
    ]


def test_excluded_section_gives_no_reliability_notes():
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(stale=True, notes=["Начисления не применены"], assumptions=3))
    assert aggregator.reliability_notes() == []


# --- смешение объектов в сводной (аудит 05.11.2026) ---


def _two_sections(geology_code, geodesy_code, geodesy_stale=False):
    aggregator = BudgetAggregator()
    aggregator.add_module(make_module_budget(object_code=geology_code))
    aggregator.add_module(make_module_budget(
        module_code="geodesy", module_name="Геодезия", object_code=geodesy_code, stale=geodesy_stale))
    return aggregator


def test_sections_of_different_objects_are_reported_and_not_exported(tmp_path):
    aggregator = _two_sections("Т-01", "Т-02")
    message = aggregator.mixed_objects()
    assert message is not None
    assert "«Т-01» — Геология" in message and "«Т-02» — Геодезия" in message
    assert "В-" not in message and ".py" not in message
    with pytest.raises(ValueError, match="разных объектов"):
        aggregator.to_excel(str(tmp_path / "mixed.xlsx"))
    assert not (tmp_path / "mixed.xlsx").exists()


@pytest.mark.parametrize("codes", [("", ""), ("Т-01", "Т-01"), ("Т-01", ""), (" Т-01 ", "Т-01")])
def test_one_object_or_empty_code_is_not_mixed(codes):
    # пустой шифр у всех — не ошибка; раздел без шифра не считается «другим объектом»
    aggregator = BudgetAggregator()
    aggregator.add_module(ModuleBudget.from_totals("geology", "Геология", _totals(), object_code=codes[0]))
    aggregator.add_module(ModuleBudget.from_totals("geodesy", "Геодезия", _totals(), object_code=codes[1]))
    assert aggregator.mixed_objects() is None


def test_excluded_section_of_other_object_does_not_block():
    # раздел другого объекта, не вошедший в бюджет (устарел), сводную не блокирует
    assert _two_sections("Т-01", "Т-02", geodesy_stale=True).mixed_objects() is None


def _totals():
    return TotalWithVat(base_total=1.0, index=1.0, indexed_total=1.0, vat_rate=0.2,
                        vat_amount=0.2, total_with_vat=1.2, warnings=[])
