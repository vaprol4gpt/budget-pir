import openpyxl
import pytest

from core.export_excel import QTY_COLUMN, export_to_excel
from core.models import EstimateLine


@pytest.fixture
def estimate():
    return {
        "geodesy.oporny_set": EstimateLine(
            id="geodesy.oporny_set",
            name="Создание плановой опорной геодезической сети (2 разряд)",
            unit="1 пункт",
            qty=8.0,
            price=6426.0,
            coefs={"K1": 0.7, "K2": 1.3},
            kind="A",
            base_ids=[],
            basis="Таблица 8, п.3",
        ),
        "geology.drilling.IV": EstimateLine(
            id="geology.drilling.IV",
            name="Колонковое бурение скважин диам. до 160 мм, IV кат.",
            unit="п.м.",
            qty=172.5,
            price=45.6,
            coefs={},
            kind="A",
            base_ids=[],
            basis="Таблица 17, п.1",
        ),
        "ecology.sampling.soil_chemical": EstimateLine(
            id="ecology.sampling.soil_chemical",
            name="Пробы почв на химические загрязнители",
            unit="шт",
            qty=160.0,
            price=6.21,
            coefs={},
            kind="A",
            base_ids=[],
            basis="Таблица 60, п.7",
        ),
    }


def test_export_sets_integer_number_format_for_integer_unit(tmp_path, estimate):
    path = tmp_path / "output.xlsx"
    export_to_excel(estimate, str(path))

    wb = openpyxl.load_workbook(str(path))
    ws = wb.active
    rows_by_id = {row[0].value: row for row in ws.iter_rows(min_row=2)}

    oporny_row = rows_by_id["geodesy.oporny_set"]
    assert oporny_row[QTY_COLUMN - 1].number_format == "0"
    # ячейка остаётся ЧИСЛОМ, не превращается в текст
    assert oporny_row[QTY_COLUMN - 1].value == 8.0


def test_export_sets_two_decimal_number_format_for_fractional_unit(tmp_path, estimate):
    path = tmp_path / "output.xlsx"
    export_to_excel(estimate, str(path))

    wb = openpyxl.load_workbook(str(path))
    ws = wb.active
    rows_by_id = {row[0].value: row for row in ws.iter_rows(min_row=2)}

    drilling_row = rows_by_id["geology.drilling.IV"]
    assert drilling_row[QTY_COLUMN - 1].number_format == "0.00"
    assert drilling_row[QTY_COLUMN - 1].value == 172.5


def test_export_sets_integer_number_format_for_sht_unit(tmp_path, estimate):
    # "шт" (экология, 4-й модуль) — отдельная проверка от "1 пункт"
    # выше, т.к. это другой модуль и другая единица в INTEGER_UNITS.
    path = tmp_path / "output.xlsx"
    export_to_excel(estimate, str(path))

    wb = openpyxl.load_workbook(str(path))
    ws = wb.active
    rows_by_id = {row[0].value: row for row in ws.iter_rows(min_row=2)}

    sampling_row = rows_by_id["ecology.sampling.soil_chemical"]
    assert sampling_row[QTY_COLUMN - 1].number_format == "0"
    assert sampling_row[QTY_COLUMN - 1].value == 160.0


def test_export_does_not_touch_price_or_amount_number_format(tmp_path, estimate):
    # Цена/Сумма НЕ трогали в этой правке — их number_format остаётся
    # дефолтным openpyxl ("General"), как и было раньше.
    path = tmp_path / "output.xlsx"
    export_to_excel(estimate, str(path))

    wb = openpyxl.load_workbook(str(path))
    ws = wb.active
    price_cell = ws.cell(row=2, column=5)  # "Цена"
    assert price_cell.number_format == "General"


# --- Э3 дорожной карты: текущие цены, НДС, итоги, ведомость, сведения ---

from core.export_excel import (  # noqa: E402
    INFO_SHEET_TITLE,
    PRICE_HEADERS,
    TOTAL_WITH_VAT,
    VOLUME_SHEET_TITLE,
    export_file_name,
)
from core.pricing import TotalWithVat  # noqa: E402
from core.stages import SOURCE_INPUT, SOURCE_LABELS, VolumeLine  # noqa: E402


def _totals(estimate, index=81.95, vat_rate=0.2, warnings=()):
    from core.models import calculate_amount

    base = sum(calculate_amount(line, estimate) for line in estimate.values())
    indexed = base * index if index is not None else None
    vat = indexed * vat_rate if indexed is not None else None
    return TotalWithVat(
        base_total=base, index=index, indexed_total=indexed, vat_rate=vat_rate if index else None,
        vat_amount=vat, total_with_vat=indexed + vat if vat is not None else None,
        warnings=list(warnings), index_period="III кв. 2026" if index else None,
    )


def test_export_with_totals_has_price_columns_per_line(tmp_path, estimate):
    from core.models import calculate_amount

    totals = _totals(estimate)
    path = tmp_path / "smeta.xlsx"
    export_to_excel(estimate, path, totals=totals)
    ws = openpyxl.load_workbook(path)["Смета"]
    header = [c.value for c in ws[1]]
    assert header[-4:] == PRICE_HEADERS
    row = {r[0].value: r for r in ws.iter_rows(min_row=2) if r[0].value}["geology.drilling.IV"]
    amount = calculate_amount(estimate["geology.drilling.IV"], estimate)
    assert row[7].value == 81.95
    assert row[8].value == pytest.approx(amount * 81.95)
    assert row[9].value == pytest.approx(amount * 81.95 * 0.2)
    assert row[10].value == pytest.approx(amount * 81.95 * 1.2)


def test_export_with_totals_writes_total_rows_equal_to_totals(tmp_path, estimate):
    totals = _totals(estimate, warnings=["Индекс III кв. 2026 применён к дате 25.10.2026"])
    path = tmp_path / "smeta.xlsx"
    export_to_excel(estimate, path, totals=totals)
    ws = openpyxl.load_workbook(path)["Смета"]
    by_label = {r[1].value: r[5].value for r in ws.iter_rows(min_row=2) if r[0].value is None}
    assert by_label[TOTAL_WITH_VAT] == pytest.approx(totals.total_with_vat)
    assert by_label["Итого в базовых ценах"] == pytest.approx(totals.base_total)
    assert by_label["Индекс пересчёта цен (III кв. 2026)"] == 81.95
    assert any(str(label).startswith("Внимание: Индекс III кв.") for label in by_label)


def test_export_without_index_leaves_price_cells_empty(tmp_path, estimate):
    totals = _totals(estimate, index=None)
    path = tmp_path / "smeta.xlsx"
    export_to_excel(estimate, path, totals=totals)
    ws = openpyxl.load_workbook(path)["Смета"]
    assert [c.value for c in ws[2]][7:] == [None, None, None, None]


def test_export_volume_sheet_and_info(tmp_path, estimate):
    volumes = {
        "drilling_catIV": VolumeLine("drilling_catIV", "Бурение, IV кат.", "п.м.", 450, SOURCE_INPUT, "ввод"),
        "privyazka": VolumeLine("privyazka", "Привязка", "1 точка (выработка)", 30, SOURCE_INPUT, "ввод"),
    }
    path = tmp_path / "smeta.xlsx"
    export_to_excel(
        estimate, path, totals=_totals(estimate), volumes=volumes,
        info={"Смета": "ГОК", "Дата расчёта": "30.09.2026"}, source_labels=SOURCE_LABELS,
    )
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Смета", VOLUME_SHEET_TITLE, INFO_SHEET_TITLE]
    rows = list(wb[VOLUME_SHEET_TITLE].iter_rows(values_only=True))
    assert rows[1][:5] == (1, "drilling_catIV", "Бурение, IV кат.", "п.м.", 450)
    assert rows[1][5] == "ввод оператора"
    assert list(wb[INFO_SHEET_TITLE].iter_rows(values_only=True)) == [
        ("Смета", "ГОК"), ("Дата расчёта", "30.09.2026"),
    ]


def test_export_file_name_is_safe():
    assert export_file_name("Смета_geology", "Объект «Тест» 1/2", "2026-09-30") == (
        "Смета_geology_Объект «Тест» 1_2_2026-09-30.xlsx"
    )
    assert export_file_name("Сводный_бюджет", None, "2026-09-30") == "Сводный_бюджет_2026-09-30.xlsx"
    assert export_file_name("Смета", 'a:b*c?"d<e>|', "2026-01-01") == "Смета_a_b_c__d_e___2026-01-01.xlsx"


# --- находка ревью Э7: строка «после индекса» в выгрузке сметы ---


def _after_index_estimate():
    from core.models import PLACEMENT_AFTER_INDEX
    from core.overrides import BASIS_PRICE_LIST, OverrideLog

    base = EstimateLine("ecology.recon", "Рекогносцировка", "км", 1, 1000.0, {}, "A", [], "т.9")
    log = OverrideLog()
    custom_id = log.add_custom_line({
        "name": "Бактериология", "unit": "проба", "qty": 1, "price": 50000.0,
        "basis_kind": BASIS_PRICE_LIST, "basis": "прайс", "placement": PLACEMENT_AFTER_INDEX,
    })
    return log.effective_estimate({"ecology.recon": base}), custom_id


class _Refs:
    def __init__(self, index):
        self.index = index

    def get_index(self, work_type, date):
        if self.index is None:
            raise KeyError(work_type)
        return self.index

    def get_vat_rate(self, date):
        return 0.22


def _rows(estimate, totals, tmp_path):
    path = tmp_path / "smeta.xlsx"
    export_to_excel(estimate, str(path), totals=totals)
    ws = openpyxl.load_workbook(path)["Смета"]
    headers = [c.value for c in ws[1]]
    return headers, {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}, ws


def test_after_index_line_has_empty_base_amount_and_column_matches_base_total(tmp_path):
    from core.pricing import calculate_total_with_vat

    estimate, custom_id = _after_index_estimate()
    totals = calculate_total_with_vat(estimate, _Refs(81.95), "ecology", "2026-09-30")
    headers, rows, ws = _rows(estimate, totals, tmp_path)
    col = {h: i for i, h in enumerate(headers)}
    assert rows[custom_id][col["Сумма"]] is None
    assert rows[custom_id][col["Сумма в текущих ценах"]] == 50000.0
    column_sum = sum(r[col["Сумма"]] or 0 for r in rows.values())
    labels = {r[1]: r[5] for r in ws.iter_rows(min_row=2, values_only=True) if r[1]}
    assert column_sum == labels["Итого в базовых ценах"] == 1000.0


def test_after_index_line_has_no_current_amount_without_index_in_strict_mode(tmp_path):
    from core.pricing import INDEX_POLICY_STRICT, calculate_total_with_vat

    estimate, custom_id = _after_index_estimate()
    totals = calculate_total_with_vat(
        estimate, _Refs(None), "ecology", "2026-09-30", index_policy=INDEX_POLICY_STRICT)
    assert totals.indexed_total is None
    headers, rows, _ws = _rows(estimate, totals, tmp_path)
    col = {h: i for i, h in enumerate(headers)}
    assert rows[custom_id][col["Сумма в текущих ценах"]] is None
    assert rows[custom_id][col["Итого с НДС"]] is None


# --- аудит 05.11.2026, О-1: текст «= …» остаётся текстом, а не формулой ---


def _formula_cells(path):
    wb = openpyxl.load_workbook(path)
    return [f"{ws.title}!{cell.coordinate}" for ws in wb.worksheets
            for row in ws.iter_rows() for cell in row if cell.data_type == "f"]


def _real_modules():
    import run_budget_stub as stub
    from modules.ecology.module import EcologyModule
    from modules.geodesy.module import GeodesyModule
    from modules.geology.module import GeologyModule
    from modules.geophysics.module import GeophysicsModule
    from modules.hydromet.module import HydrometModule

    return {
        "geology": (GeologyModule, {"volumes": stub.GEOLOGY_INPUT}),
        "geophysics": (GeophysicsModule, {"volumes": stub.GEOPHYSICS_INPUT}),
        "geodesy": (GeodesyModule, {"volumes": stub.GEODESY_INPUT}),
        "ecology": (EcologyModule, {"volumes": stub.ECOLOGY_INPUT}),
        "hydromet": (HydrometModule, {"volumes": stub.HYDROMET_VOLUMES,
                                      "estimate": stub.HYDROMET_PARAMETERS}),
    }


@pytest.mark.parametrize("code", ["geology", "geophysics", "geodesy", "ecology", "hydromet"])
def test_export_of_real_module_has_no_formula_cells(tmp_path, code):
    # Прежний тест подавал в ведомость строки-подмены с основанием «ввод» и не видел,
    # что основания настоящих модулей («= число скважин») уходили в файл формулами:
    # Excel не открывал смету экологии и ИГМИ, у геологии и геофизики показывал «#ИМЯ?».
    from core.object_data import CalculationContext, ObjectData
    from core.references import ReferenceBook
    from core.stages import StagedSession, as_staged

    module_class, inputs = _real_modules()[code]
    refs = CalculationContext(ReferenceBook(), ObjectData())
    session = StagedSession(as_staged(module_class()))
    result = None
    for stage in session.staged.stages:
        session.inputs[stage.key] = inputs.get(stage.key, {})
        result = session.confirm(stage.key, refs)
    estimate = dict(result.lines)
    volumes = dict(session.result("volumes").lines)
    path = tmp_path / f"smeta_{code}.xlsx"
    export_to_excel(estimate, path, totals=_totals(estimate), volumes=volumes,
                    info={"Смета": "тест"}, source_labels=SOURCE_LABELS)

    assert _formula_cells(path) == []
    # основание читается тем же текстом, что в ведомости программы
    sheet = openpyxl.load_workbook(path)[VOLUME_SHEET_TITLE]
    written = {row[1]: row[6] for row in sheet.iter_rows(min_row=2, values_only=True)}
    assert written == {line.id: line.basis for line in volumes.values()}


def test_export_keeps_text_starting_with_equals_sign_as_text(tmp_path, estimate):
    volumes = {"wells": VolumeLine("wells", "=Скважины", "шт", 5, SOURCE_INPUT, "= число скважин")}
    estimate["geology.drilling.IV"].name = "=1+1"
    path = tmp_path / "smeta.xlsx"
    export_to_excel(estimate, path, totals=_totals(estimate, warnings=["=предупреждение"]),
                    volumes=volumes, info={"Наименование объекта": "=СУММ(A1:A2)"},
                    source_labels=SOURCE_LABELS)

    assert _formula_cells(path) == []
    wb = openpyxl.load_workbook(path)
    basis = wb[VOLUME_SHEET_TITLE].cell(row=2, column=7)
    assert (basis.value, basis.data_type, basis.quotePrefix) == ("= число скважин", "s", True)
    assert wb[INFO_SHEET_TITLE].cell(row=1, column=2).value == "=СУММ(A1:A2)"
    names = [row[1] for row in wb["Смета"].iter_rows(min_row=2, values_only=True)]
    assert "=1+1" in names


def test_export_drops_characters_excel_cannot_store(tmp_path, estimate):
    # управляющий символ в имени (вставка из другого документа) ронял выгрузку
    estimate["geology.drilling.IV"].name = "Бурение\x0b скважин"
    path = tmp_path / "smeta.xlsx"
    export_to_excel(estimate, path, info={"Заказчик": "ООО\x00 «Пример»"})

    wb = openpyxl.load_workbook(path)
    names = [row[1] for row in wb["Смета"].iter_rows(min_row=2, values_only=True)]
    assert "Бурение скважин" in names
    assert wb[INFO_SHEET_TITLE].cell(row=1, column=2).value == "ООО «Пример»"


def test_budget_export_keeps_text_starting_with_equals_sign_as_text(tmp_path):
    from core.aggregator import BudgetAggregator, ModuleBudget

    aggregator = BudgetAggregator(budget_reduction=0.8)
    aggregator.add_module(ModuleBudget("geology", "=Геология", 100.0, 8195.0, 1802.9, 9997.9))
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(path, info={"Шифр": "=A1"}, kp_warnings=["=убыток"])

    assert _formula_cells(path) == []
    assert openpyxl.load_workbook(path)["Сведения"].cell(row=1, column=2).value == "=A1"
