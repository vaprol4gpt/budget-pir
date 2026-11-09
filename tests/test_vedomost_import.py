"""Импорт ведомости объёмов из Excel (core/vedomost_import.py, core/vedomost_template.py,
tools/make_vedomost_templates.py; 03.11.2026): круговой тест на каждый раздел
(ввод → шаблон → загрузка → тот же ввод → та же ведомость строка в строку), ошибки
по строкам, шаблоны = модуль и свежие."""

from __future__ import annotations

import dataclasses
import io

import openpyxl
import pytest

from core.references import DEFAULT_DB_PATH, ReferenceBook
from core.stages import StagedSession, as_staged
from core.vedomost_import import (
    COL_NOTE,
    COL_QTY,
    COL_UNIT,
    COL_WORK,
    KIND_FLAG,
    KIND_GROUP,
    SHEET_CATALOG,
    SHEET_VEDOMOST,
    build_catalog,
    empty_inputs,
    export_rows,
    normalized_inputs,
    read_vedomost,
    split_label,
)
from core.vedomost_template import FIRST_ROW, HEADER_ROW, SECTION_TITLES, workbook_bytes
from modules.geodesy.module import CONDITION_NORMAL
from modules.geophysics.module import (
    F_MPV_WAVES,
    F_OFFICE_VEZ,
    F_PROGRAM,
    F_REPORT,
    WAVES_BOTH,
    YES,
)
from run_budget_stub import ECOLOGY_INPUT, GEOLOGY_INPUT, HYDROMET_VOLUMES
from tools.make_vedomost_templates import SECTIONS, stale_templates, template_path

pytestmark = pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite")


@pytest.fixture(scope="module")
def refs():
    return ReferenceBook()


def module_of(code):
    return SECTIONS[code][0]()


def catalog_of(code, refs):
    return build_catalog(module_of(code), refs, code)


# --- вводы эталонных тестов разделов ---------------------------------------------

# Геология — объёмы листа «1.2.1_ИГИ» (как tests/test_geology_etalon.py, D-столбец).
GEOLOGY_SHEET = {
    "drilling_by_category": [
        {"soil_category": "IV", "drilling_length": 120.5}, {"soil_category": "V", "drilling_length": 80},
        {"soil_category": "VI", "drilling_length": 40}, {"soil_category": "VII", "drilling_length": 12},
        {"soil_category": "VIII", "drilling_length": 3.5},
    ],
    "drilling_count": 30, "rekognoscirovka_km": 2.5, "kreplenie_length": 60,
    "gidronablyudeniya_length": 45, "shtamp_count": 2, "baza_months": 1.5,
    "lab_glinistye_count": 40, "lab_peschanye_count": 12, "lab_opt_vlazhnost_count": 3,
    "lab_korroziya_stal_count": 6, "lab_korroziya_beton_count": 6, "sbor_materialov_count": 300,
}
# Геофизика — tests/test_geophysics_etalon.py::VOLUMES.
GEOPHYSICS_SHEET = {"vez_count": 80, "mpv_count": 400, F_MPV_WAVES: WAVES_BOTH, F_PROGRAM: 1,
                    F_OFFICE_VEZ: YES, F_REPORT: YES}
# Геодезия — tests/test_geodesy_etalon.py::ROAD_VOLUMES (лист «№1.1_ИГДИ»).
GEODESY_SHEET = {
    "oporny_points_count": 8, "height_points_count": 8, "trassa_km": 14.7, "privyazka_count": 50,
    "znaki_count": 8, "base_months": 2, "ggs_plan_points": 15, "ggs_height_points": 15,
    "cartogram_count": 1, "oporny_kameral_points_count": 8, "soglasovanie_count": 3,
    "topo_surveys": [
        {"topo_scale": "1:1000", "topo_section": 0.5, "topo_area": 147, "survey_condition": "strip"},
        {"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2, "survey_condition": "small"},
    ],
    "topo_plans": [
        {"topo_scale": "1:1000", "topo_section": 0.5, "topo_area": 147, "plan_condition": "strip"},
        {"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2},
    ],
}
# Экология — объёмы tests/test_ecology_etalons.py::ROAD_INPUT + пробы run_budget_stub + анализы.
ECOLOGY_SHEET = {
    "fund_materials_count": 2.6, "subbase_volume": 7, "recon_length_km": 14.7,
    "observation_points": 11, "radiation_area": 0, "radon_points": 0,
    "physical_factors_points": 10, "data_acquisition_count": 26,
    "sampling": ECOLOGY_INPUT["sampling"],
    "lab_analyses": [{"analysis_type": "heavy_metals_soil", "analysis_count": 16},
                     {"analysis_type": "surface_water", "analysis_count": 3}],
}

ETALON_INPUTS = {
    "geology": [GEOLOGY_SHEET, GEOLOGY_INPUT],
    "geophysics": [GEOPHYSICS_SHEET, {"vez_count": 80, "mpv_count": 400}],
    "geodesy": [GEODESY_SHEET, {"oporny_points_count": 8, "privyazka_count": 50}],
    "ecology": [ECOLOGY_SHEET, ECOLOGY_INPUT],
    "hydromet": [HYDROMET_VOLUMES],
}


def full_input(catalog):
    """Ввод «заполнено всё»: каждое числовое поле, каждый вариант каждой группы
    (с параметрами строки не по умолчанию), выбор не по умолчанию."""
    data = empty_inputs(catalog)
    for i, work in enumerate(catalog.work_types, start=1):
        qty = i if work.integer else i + 0.25
        if work.kind == KIND_GROUP:
            spec = catalog.groups[work.field]
            row = {spec.choice.name: work.option, spec.qty.name: qty}
            for sub in spec.params:
                if sub.dynamic_options:
                    row[sub.name] = catalog.param(sub.label).options[work.field][-1][0]
                else:
                    row[sub.name] = 0.5 * i
            data[work.field].append(row)
        elif work.kind == KIND_FLAG:
            data[work.field] = work.option  # последний вариант поля побеждает
        else:
            data[work.field] = qty
    return data


def roundtrip(code, refs, inputs, **kwargs):
    catalog = catalog_of(code, refs)
    rows, skipped = export_rows(catalog, inputs)
    assert skipped == []
    data = workbook_bytes(catalog, SECTION_TITLES[code], rows)
    return catalog, read_vedomost(data, module_of(code), refs, code, **kwargs)


def volume_sheet(code, refs, inputs):
    session = StagedSession(as_staged(module_of(code)))
    session.inputs["volumes"] = dict(inputs)
    lines = session.compute("volumes", refs).effective
    return [dataclasses.astuple(line) for line in lines.values()]


def all_inputs():
    for code, inputs in ETALON_INPUTS.items():
        for number, data in enumerate(inputs):
            yield pytest.param(code, data, id=f"{code}-{number}")


@pytest.mark.parametrize("code, inputs", list(all_inputs()))
def test_roundtrip_etalon_input(refs, code, inputs):
    catalog, result = roundtrip(code, refs, inputs)
    assert result.errors == []
    assert normalized_inputs(catalog, result.inputs) == normalized_inputs(catalog, inputs)
    assert volume_sheet(code, refs, result.inputs) == volume_sheet(code, refs, inputs)


@pytest.mark.parametrize("code", list(SECTIONS))
def test_roundtrip_everything_filled(refs, code):
    catalog = catalog_of(code, refs)
    inputs = full_input(catalog)
    catalog, result = roundtrip(code, refs, inputs)
    assert result.errors == []
    assert result.inputs == inputs  # точно тот же ввод, включая порядок строк групп
    assert volume_sheet(code, refs, result.inputs) == volume_sheet(code, refs, inputs)


# --- каталог видов работ = модуль ---------------------------------------------------


@pytest.mark.parametrize("code", list(SECTIONS))
def test_catalog_covers_every_stage_one_field(refs, code):
    module = module_of(code)
    catalog = catalog_of(code, refs)
    assert catalog.unsupported == []
    fields = {f.name for f in as_staged(module).stages[0].fields}
    assert {w.field for w in catalog.work_types} == fields
    names = [w.name for w in catalog.work_types]
    assert len(names) == len(set(names))
    assert all(w.unit for w in catalog.work_types)
    # каждый вариант выбора каждой группы — отдельный вид работ
    for name, spec in catalog.groups.items():
        assert [w.option for w in catalog.work_types if w.field == name] == \
            [value for value, _label in module.field_options(spec.choice.name, refs)]


def test_geology_categories_are_from_references(refs):
    catalog = catalog_of("geology", refs)
    names = [w.name for w in catalog.work_types if w.field == "drilling_by_category"]
    assert "Бурение по категориям грунта: категория грунта IV" in names
    assert all(w.unit == "п.м." for w in catalog.work_types if w.field == "drilling_by_category")
    assert catalog.find("число  СКВАЖИН").unit == "скв."  # единица строки ведомости модуля


@pytest.mark.parametrize("label, expected", [
    ("Крепление скважин, п.м.", ("Крепление скважин", "п.м.")),
    ("Объём сейсморазведки МПВ, физ.наблюдений (на один тип волн)",
     ("Объём сейсморазведки МПВ (на один тип волн)", "физ.наблюдений")),
    ("Составление программы работ (т.294 п.3), шт. (в эталоне — 1; 0 — строки нет)",
     ("Составление программы работ (т.294 п.3)", "шт.")),
    ("Число скважин", ("Число скважин", None)),
])
def test_split_label(label, expected):
    assert split_label(label) == expected


# --- шаблоны ------------------------------------------------------------------------


def test_templates_in_git_match_generator(refs):
    assert stale_templates(refs=refs) == []


@pytest.mark.parametrize("code", list(SECTIONS))
def test_template_lists_module_work_types(refs, code):
    wb = openpyxl.load_workbook(template_path(code))
    catalog = catalog_of(code, refs)
    cat = wb[SHEET_CATALOG]
    listed = [(cat.cell(row=r, column=1).value, cat.cell(row=r, column=2).value)
              for r in range(4, 4 + len(catalog.work_types))]
    assert listed == [(w.name, w.unit) for w in catalog.work_types]
    ws = wb[SHEET_VEDOMOST]
    headers = [c.value for c in ws[HEADER_ROW] if c.value]
    assert headers == catalog.columns()
    assert f"код раздела: {code}" in ws.cell(row=3, column=1).value
    assert ws.protection.sheet and ws.protection.password is None
    # вид работ — выпадающий список по «Справочнику», объём — число ≥ 0
    formulas = {dv.formula1: dv.type for dv in ws.data_validations.dataValidation}
    assert formulas[f"='{SHEET_CATALOG}'!$A$4:$A${3 + len(catalog.work_types)}"] == "list"
    assert "decimal" in formulas.values()
    # единица — формула, защищена; вид работ, объём, примечание — ввод
    unit_col = headers.index(COL_UNIT) + 1
    assert str(ws.cell(row=FIRST_ROW, column=unit_col).value).startswith("=IF(")
    assert ws.cell(row=FIRST_ROW, column=unit_col).protection.locked
    for title in (COL_WORK, COL_QTY, COL_NOTE):
        assert not ws.cell(row=FIRST_ROW, column=headers.index(title) + 1).protection.locked
    assert ws.cell(row=HEADER_ROW, column=1).protection.locked
    assert ws.cell(row=FIRST_ROW, column=1).value is None  # шаблон пуст
    assert "Как заполнить" in wb.sheetnames


# --- ошибки по строкам ------------------------------------------------------------------


def make_file(code, refs, rows, edit=None):
    """Пустой шаблон раздела с заполненными строками: [{колонка: значение}]."""
    catalog = catalog_of(code, refs)
    wb = openpyxl.load_workbook(io.BytesIO(workbook_bytes(catalog, SECTION_TITLES[code])))
    ws = wb[SHEET_VEDOMOST]
    headers = {c.value: c.column for c in ws[HEADER_ROW] if c.value}
    for offset, row in enumerate(rows):
        for title, value in row.items():
            ws.cell(row=FIRST_ROW + offset, column=headers[title], value=value)
    if edit:
        edit(ws)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def read(code, refs, rows, **kwargs):
    edit = kwargs.pop("edit", None)
    return read_vedomost(make_file(code, refs, rows, edit), module_of(code), refs, code, **kwargs)


DRILL_IV = "Бурение по категориям грунта: категория грунта IV"
HOLES = "Число скважин"


def errors_text(result):
    return " | ".join(str(e) for e in result.errors)


def test_good_file_gives_inputs_and_preview(refs):
    result = read("geology", refs, [
        {COL_WORK: HOLES, COL_QTY: 30, COL_NOTE: "по ТЗ"},
        {COL_WORK: DRILL_IV, COL_QTY: "450,5"},  # число текстом с запятой — принимается
    ])
    assert result.ok, errors_text(result)
    assert result.inputs["drilling_count"] == 30
    assert result.inputs["drilling_by_category"] == [{"soil_category": "IV", "drilling_length": 450.5}]
    assert result.inputs["rekognoscirovka_km"] == 0
    assert [p.row for p in result.preview] == [FIRST_ROW, FIRST_ROW + 1]
    assert result.preview[0].note == "по ТЗ"


@pytest.mark.parametrize("rows, text", [
    ([{COL_WORK: "Бурение кат. 4", COL_QTY: 1}], "неизвестный вид работ «Бурение кат. 4»"),
    ([{COL_WORK: "Число скважн", COL_QTY: 1}], "может быть, «Число скважин»"),
    ([{COL_WORK: HOLES, COL_QTY: "тридцать"}], "не число"),
    ([{COL_WORK: HOLES, COL_QTY: -3}], "отрицательный"),
    ([{COL_WORK: HOLES}], "не указан объём"),
    ([{COL_WORK: HOLES, COL_QTY: 2.5}], "нужно целое число"),
    ([{COL_QTY: 5}], "не выбран вид работ"),
    ([{COL_WORK: HOLES, COL_QTY: 3}, {COL_WORK: HOLES, COL_QTY: 4}], "встречается второй раз (впервые — в строке 7)"),
    ([{COL_WORK: HOLES, COL_UNIT: "м", COL_QTY: 3}], "единица «м» не та"),
])
def test_row_errors(refs, rows, text):
    result = read("geology", refs, rows)
    assert not result.ok
    assert text in errors_text(result)
    assert result.errors[0].row is not None


def test_errors_leave_inputs_empty(refs):
    """Файл с ошибкой в ввод не ставится: значения не заполняются ни для одной строки."""
    result = read("geology", refs, [{COL_WORK: HOLES, COL_QTY: 30}, {COL_WORK: "???", COL_QTY: 1}])
    assert not result.ok
    assert result.inputs["drilling_count"] == 0


def test_duplicates_summed_on_request(refs):
    rows = [{COL_WORK: DRILL_IV, COL_QTY: 60}, {COL_WORK: DRILL_IV, COL_QTY: 40},
            {COL_WORK: HOLES, COL_QTY: 6}, {COL_WORK: HOLES, COL_QTY: 4}]
    result = read("geology", refs, rows, sum_duplicates=True)
    assert result.ok, errors_text(result)
    assert result.inputs["drilling_by_category"] == [{"soil_category": "IV", "drilling_length": 100}]
    assert result.inputs["drilling_count"] == 10
    assert any("сложены" in w.text for w in result.warnings)


def test_file_of_other_section_is_rejected(refs):
    data = make_file("geodesy", refs, [{COL_WORK: "Число пунктов опорной геодезической сети", COL_QTY: 8}])
    labels = {"geodesy": "Геодезия (ИГДИ)", "geology": "Геология (ОИГ)"}
    result = read_vedomost(data, module_of("geology"), refs, "geology", labels)
    assert not result.ok
    assert "ведомость раздела «Геодезия (ИГДИ)», а выбран раздел «Геология (ОИГ)»" in errors_text(result)
    assert result.file_section == "geodesy"


def test_file_without_section_mark_is_read_with_warning(refs):
    def drop_mark(ws):
        ws.cell(row=3, column=1).value = "Ведомость отдела"
    result = read("geology", refs, [{COL_WORK: HOLES, COL_QTY: 30}], edit=drop_mark)
    assert result.ok
    assert any("нет метки раздела" in w.text for w in result.warnings)


def test_not_excel_and_empty_files(refs):
    bad = read_vedomost(b"not an xlsx", module_of("geology"), refs, "geology")
    assert not bad.ok and "Не удалось открыть" in errors_text(bad)
    empty = read("geology", refs, [])
    assert not empty.ok and "нет ни одной строки" in errors_text(empty)


def test_required_missing_is_warning(refs):
    result = read("geology", refs, [{COL_WORK: "Крепление скважин", COL_QTY: 10}])
    assert result.ok
    texts = " | ".join(w.text for w in result.warnings)
    assert "Число скважин" in texts and "Бурение по категориям грунта" in texts


def test_flag_rules(refs):
    office = "Камеральная обработка данных электроразведки (ВЭЗ)"
    s_waves = "Сейсморазведка МПВ: тип волн — поперечные волны (одна строка МПВ)"
    both = "Сейсморазведка МПВ: тип волн — оба типа"
    ok = read("geophysics", refs, [{COL_WORK: office, COL_QTY: 1}, {COL_WORK: both, COL_QTY: 1}])
    assert ok.ok, errors_text(ok)
    assert ok.inputs[F_OFFICE_VEZ] == YES and ok.inputs[F_MPV_WAVES] == WAVES_BOTH
    assert ok.inputs[F_REPORT] == ""  # не указан — по умолчанию «нет»
    bad = read("geophysics", refs, [{COL_WORK: office, COL_QTY: 2}])
    assert "1 (да) или 0 (нет)" in errors_text(bad)
    conflict = read("geophysics", refs, [{COL_WORK: s_waves, COL_QTY: 1}, {COL_WORK: both, COL_QTY: 1}])
    assert "оставьте один вариант" in errors_text(conflict)


def test_group_params(refs):
    survey = "Топографическая съёмка: масштаб 1:500"
    plan = "Инженерно-топографические планы: масштаб 1:500"
    small = "Небольшой участок или узкая полоса: только М 1:500, площадь свыше 1 до 5 га, " \
            "кроме трасс линейных сооружений (к = 1,2, т.10 п.1)"
    ok = read("geodesy", refs, [
        {COL_WORK: survey, COL_QTY: 2, "Сечение рельефа, м": 0.5, "Условие съёмки (коэффициент)": small},
        {COL_WORK: plan, COL_QTY: 2},
    ])
    assert ok.ok, errors_text(ok)
    assert ok.inputs["topo_surveys"] == [
        {"topo_scale": "1:500", "topo_area": 2, "topo_section": 0.5, "survey_condition": "small"}]
    assert ok.inputs["topo_plans"] == [
        {"topo_scale": "1:500", "topo_area": 2, "plan_condition": CONDITION_NORMAL}]
    # к = 1,2 плану не положен — условия нет в списке плана
    bad = read("geodesy", refs, [{COL_WORK: plan, COL_QTY: 2, "Условие съёмки (коэффициент)": small}])
    assert "нет в списке" in errors_text(bad)
    # параметр у вида работ без группы — предупреждение, значение не используется
    other = read("geodesy", refs, [
        {COL_WORK: "Число пунктов опорной геодезической сети", COL_QTY: 8, "Сечение рельефа, м": 1}])
    assert other.ok
    assert any("не относится" in w.text for w in other.warnings)
    # разные сечения одного масштаба — две строки группы, не дубль
    two = read("geodesy", refs, [
        {COL_WORK: survey, COL_QTY: 2, "Сечение рельефа, м": 0.5},
        {COL_WORK: survey, COL_QTY: 3, "Сечение рельефа, м": 1},
    ])
    assert two.ok and len(two.inputs["topo_surveys"]) == 2


def test_export_skips_unknown_choice_with_message(refs):
    catalog = catalog_of("geology", refs)
    rows, skipped = export_rows(catalog, {"drilling_by_category": [
        {"soil_category": "IX", "drilling_length": 5}], "drilling_count": 1})
    assert [r.work.name for r in rows] == [HOLES]
    assert skipped and "IX" in skipped[0]


# --- находки ревью 03.11 ----------------------------------------------------------------


def help_text(code, refs):
    from core.vedomost_template import derived_text  # noqa: PLC0415

    return derived_text(catalog_of(code, refs))


@pytest.mark.parametrize("code, names", [
    ("geophysics", ["Составление программы работ (т.294 п.3)",
                    "Камеральная обработка данных электроразведки (ВЭЗ)",
                    "Составление технического отчёта"]),
    ("geodesy", ["Камеральная обработка плановой опорной сети"]),
])
def test_help_names_derived_works_that_are_in_the_list(refs, code, names):
    """№1: «Как заполнить» не говорит «программы/камералки/отчёта в списке нет», если они
    в списке раздела есть, — перечисляет их как обязательные к указанию."""
    text = help_text(code, refs)
    for name in names:
        assert name in text
    wb = openpyxl.load_workbook(template_path(code))
    help_values = " ".join(str(c) for row in wb["Как заполнить"].iter_rows(values_only=True)
                           for c in row if c)
    assert names[0] in help_values
    assert "их вводить не нужно" not in help_values


def test_help_without_derived_in_list(refs):
    assert "сами не добавятся" not in help_text("geology", refs)


def test_readme_does_not_claim_derived_are_absent():
    from pathlib import Path  # noqa: PLC0415

    text = (Path(__file__).resolve().parent.parent / "docs" / "templates" / "README.md").read_text(
        encoding="utf-8")
    assert "нет камеральной обработки, программы, отчёта" not in text


def test_missing_derived_works_warn_geophysics(refs):
    result = read("geophysics", refs, [{COL_WORK: "Объём ВЭЗ", COL_QTY: 80}])
    assert result.ok
    texts = " | ".join(w.text for w in result.warnings)
    for name in ("Составление программы работ", "Камеральная обработка данных электроразведки",
                 "Составление технического отчёта"):
        assert name in texts
    full = read("geophysics", refs, [
        {COL_WORK: "Объём ВЭЗ", COL_QTY: 80},
        {COL_WORK: "Составление программы работ (т.294 п.3)", COL_QTY: 1},
        {COL_WORK: "Камеральная обработка данных электроразведки (ВЭЗ)", COL_QTY: 1},
        {COL_WORK: "Составление технического отчёта (т.294 п.10: 1000 руб. + 10 % камеральных)",
         COL_QTY: 1},
    ])
    assert full.ok and not any("сама не добавится" in w.text for w in full.warnings)


def test_missing_derived_works_warn_geodesy(refs):
    result = read("geodesy", refs, [{COL_WORK: "Число пунктов опорной геодезической сети", COL_QTY: 8}])
    assert any("Камеральная обработка плановой опорной сети" in w.text for w in result.warnings)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN"])
def test_nan_and_inf_are_not_numbers(refs, value):
    """№2."""
    result = read("geology", refs, [{COL_WORK: "Крепление скважин", COL_QTY: value}])
    assert "не число" in errors_text(result)


def test_float_noise_in_integer_field_is_accepted(refs):
    """№3: =0,1*30 в Excel — 3,0000000000000004."""
    result = read("geology", refs, [{COL_WORK: HOLES, COL_QTY: 0.1 * 30}])
    assert result.ok, errors_text(result)
    assert result.inputs["drilling_count"] == 3 and isinstance(result.inputs["drilling_count"], int)


def test_formula_without_value_is_named(refs):
    """№4: формула без вычисленного значения — не «пусто», а понятная ошибка."""
    result = read("geology", refs, [{COL_WORK: HOLES, COL_QTY: "=2+3"}])
    assert "формула без вычисленного значения" in errors_text(result)
    assert "сохраните" in errors_text(result)


def _set_headers(changes, extra=None):
    def edit(ws):
        headers = {c.value: c.column for c in ws[HEADER_ROW] if c.value}
        for old, new in changes.items():
            ws.cell(row=HEADER_ROW, column=headers[old], value=new)
        for col, (title, value) in (extra or {}).items():
            ws.cell(row=HEADER_ROW, column=col, value=title)
            ws.cell(row=FIRST_ROW, column=col, value=value)
    return edit


def test_several_matching_columns_is_error(refs):
    """№5: «Объём работ (по ТЗ)» и «Объём работ (по факту)» — не брать молча первую."""
    edit = _set_headers({COL_QTY: "Объём работ (по ТЗ)"}, {20: ("Объём работ (по факту)", 5)})
    result = read_vedomost(make_file("geology", refs, [{COL_WORK: HOLES, COL_QTY: 3}], edit),
                           module_of("geology"), refs, "geology")
    assert "подходят несколько" in errors_text(result)
    assert "«Объём работ (по ТЗ)», «Объём работ (по факту)»" in errors_text(result)


def test_volume_weight_is_not_volume(refs):
    """№5: «Объёмный вес» не считается колонкой объёма."""
    edit = _set_headers({COL_QTY: "Объём, ед."}, {20: ("Объёмный вес", 1.8)})
    result = read_vedomost(make_file("geology", refs, [{COL_WORK: HOLES, COL_QTY: 3}], edit),
                           module_of("geology"), refs, "geology")
    assert result.ok, errors_text(result)
    assert result.inputs["drilling_count"] == 3


@pytest.mark.parametrize("code, work, unit", [
    ("geology", "Крепление скважин", "п. м."),
    ("geology", "Крепление скважин", "п.м"),
    ("hydromet", "Определение площади водосбора", "дм2"),
])
def test_unit_spelling_variants(refs, code, work, unit):
    """№6."""
    result = read(code, refs, [{COL_WORK: work, COL_UNIT: unit, COL_QTY: 5}])
    assert result.ok, errors_text(result)


def test_norm_unit():
    from core.vedomost_import import norm_unit  # noqa: PLC0415

    assert norm_unit("м2") == norm_unit("м²") and norm_unit("М3") == norm_unit("м³")
    assert norm_unit("п. м.") == norm_unit("п.м.")


def test_file_read_by_key_after_label_rename(refs):
    """№7: подпись вида работ в программе изменилась — файл, выгруженный раньше
    (со старыми названиями), читается по служебному ключу."""
    catalog = catalog_of("geodesy", refs)
    old = dataclasses.replace(catalog, work_types=[
        dataclasses.replace(w, name=f"Старое название {i}") for i, w in enumerate(catalog.work_types)])
    rows, _ = export_rows(old, GEODESY_SHEET)
    data = workbook_bytes(old, SECTION_TITLES["geodesy"], rows)
    result = read_vedomost(data, module_of("geodesy"), refs, "geodesy")
    assert result.ok, errors_text(result)
    assert normalized_inputs(catalog, result.inputs) == normalized_inputs(catalog, GEODESY_SHEET)


def test_key_column_is_hidden_and_locked(refs):
    from openpyxl.utils import get_column_letter  # noqa: PLC0415

    from core.vedomost_import import COL_KEY  # noqa: PLC0415

    ws = openpyxl.load_workbook(template_path("geology"))[SHEET_VEDOMOST]
    headers = [c.value for c in ws[HEADER_ROW]]
    col = headers.index(COL_KEY) + 1
    assert ws.column_dimensions[get_column_letter(col)].hidden
    assert ws.cell(row=FIRST_ROW, column=col).protection.locked
    assert str(ws.cell(row=FIRST_ROW, column=col).value).startswith("=IF(")


def test_work_changed_in_exported_file_goes_to_the_new_work(refs):
    """Аудит 05.11.2026, О-3: в выгруженном файле ключ вида работ записан значением.
    Отдел сменил вид работ в строке через список — ключ остался прежним, и объём молча
    уходил в прежний вид работ (ошибок 0, замечаний 0). Теперь — по названию в строке,
    с замечанием."""
    catalog = catalog_of("geology", refs)
    old = next(w for w in catalog.work_types if w.field == "kreplenie_length")
    new = next(w for w in catalog.work_types if w.field == "gidronablyudeniya_length")
    rows, _ = export_rows(catalog, {"kreplenie_length": 11.37})
    wb = openpyxl.load_workbook(io.BytesIO(workbook_bytes(catalog, SECTION_TITLES["geology"], rows)))
    ws = wb[SHEET_VEDOMOST]
    work_col = [c.value for c in ws[HEADER_ROW]].index(COL_WORK) + 1
    assert ws.cell(row=FIRST_ROW, column=work_col).value == old.name
    ws.cell(row=FIRST_ROW, column=work_col).value = new.name  # как выбор из списка в Excel
    buffer = io.BytesIO()
    wb.save(buffer)

    result = read_vedomost(buffer.getvalue(), module_of("geology"), refs, "geology")

    assert result.ok, errors_text(result)
    assert result.inputs["gidronablyudeniya_length"] == 11.37
    assert not result.inputs["kreplenie_length"]
    assert [p.work for p in result.preview] == [new.name]
    assert any(old.name in str(w) and new.name in str(w) for w in result.warnings)


def test_huge_volume_gets_a_warning(refs):
    """Аудит 05.11.2026, Н13: импорт молча принимал 10^308 п.м."""
    result = read("geology", refs, [{COL_WORK: "Крепление скважин", COL_QTY: 1e12}])
    assert result.ok, errors_text(result)
    assert any("необычно большой" in str(w) for w in result.warnings)


def test_default_condition_written_explicitly_is_still_a_duplicate(refs):
    """Аудит 05.11.2026, Н14: «условие пусто» и «условие по умолчанию явно» считались разными
    строками — площадь съёмки входила дважды."""
    catalog = catalog_of("geodesy", refs)
    survey = next(w for w in catalog.work_types if w.field == "topo_surveys")
    column = next(p for p in catalog.params
                  if p.type == "choice" and survey.field in p.subfields and "услови" in p.header.lower())
    default_label = column.options[survey.field][0][1]
    rows = [{COL_WORK: survey.name, COL_QTY: 10},
            {COL_WORK: survey.name, COL_QTY: 10, column.header: default_label}]
    result = read("geodesy", refs, rows)
    assert not result.ok
    assert "встречается второй раз" in errors_text(result)
