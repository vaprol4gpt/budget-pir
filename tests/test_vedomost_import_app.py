"""AppTest блока «Ведомость из Excel» на странице «2. Ведомость объёмов» (app/ui_import.py,
03.11.2026): загрузка → предпросмотр → «Поставить в ведомость» → ведомость построена;
файл с ошибками → отчёт, форма не изменилась; замена непустого ввода — со второго
нажатия; выгрузка текущего ввода читается обратно."""

from __future__ import annotations

import io

import openpyxl
import pytest

import core.persistence as persistence_module
from core.references import DEFAULT_DB_PATH, ReferenceBook
from core.vedomost_import import COL_QTY, COL_WORK, SHEET_VEDOMOST, build_catalog, read_vedomost
from core.vedomost_template import FIRST_ROW, HEADER_ROW, SECTION_TITLES, workbook_bytes
from modules.geology.module import GeologyModule
from tests.test_streamlit_app import _fill_geology, open_app

pytestmark = pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HOLES = "Число скважин"
DRILL_V = "Бурение по категориям грунта: категория грунта V"


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


def geology_file(rows):
    catalog = build_catalog(GeologyModule(), ReferenceBook(), "geology")
    wb = openpyxl.load_workbook(io.BytesIO(workbook_bytes(catalog, SECTION_TITLES["geology"])))
    ws = wb[SHEET_VEDOMOST]
    headers = {c.value: c.column for c in ws[HEADER_ROW] if c.value}
    for offset, row in enumerate(rows):
        for title, value in row.items():
            ws.cell(row=FIRST_ROW + offset, column=headers[title], value=value)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def upload(at, data, name="vedomost.xlsx"):
    [uploader] = [u for u in at.get("file_uploader") if u.key.startswith("vedomost_upload_geology")]
    uploader.set_value((name, data, XLSX))
    at.run(timeout=30)


def apply_button(at):
    return at.button(key="vedomost_apply_geology")


def test_upload_preview_apply_builds_volume_sheet(isolated_smeta_db):
    at = open_app()
    upload(at, geology_file([{COL_WORK: HOLES, COL_QTY: 12}, {COL_WORK: DRILL_V, COL_QTY: 96.5}]))
    assert at.exception == []
    assert at.error == []
    preview = [d.value for d in at.dataframe if "Вид работ" in d.value.columns]
    assert preview and list(preview[0]["Вид работ"]) == [HOLES, DRILL_V]
    assert not apply_button(at).disabled
    # форма ещё не тронута
    assert at.number_input(key="field_geology_drilling_count").value == 0

    apply_button(at).click()
    at.run(timeout=30)  # кнопка → ожидающая загрузка → перерисовка с новым вводом
    assert at.exception == []
    assert at.number_input(key="field_geology_drilling_count").value == 12
    assert at.session_state["form_data_by_module"]["geology"]["drilling_by_category"] == [
        {"soil_category": "V", "drilling_length": 96.5}]
    session = at.session_state["staged_sessions"]["geology"]
    lines = session.compute("volumes", at.session_state["_run_context"].refs).effective
    assert lines["drilling_count"].qty == 12
    assert any("перенесены в форму ведомости" in s.value for s in at.success)
    # дальше — обычный путь: ведомость можно подтвердить главной кнопкой
    assert not at.button(key="confirm_geology_volumes").disabled


def test_file_with_errors_reports_rows_and_keeps_form(isolated_smeta_db):
    at = open_app()
    _fill_geology(at)
    upload(at, geology_file([{COL_WORK: HOLES, COL_QTY: -1}, {COL_WORK: "Бурение кат. 4", COL_QTY: 5}]))
    assert at.exception == []
    assert any("ошибок: 2" in e.value for e in at.error)
    report = [d.value for d in at.dataframe if "Что не так" in d.value.columns][0]
    assert list(report["Строка Excel"]) == [str(FIRST_ROW), str(FIRST_ROW + 1)]
    assert apply_button(at).disabled
    assert at.number_input(key="field_geology_drilling_count").value == 30  # форма не изменилась


def test_replacing_existing_input_needs_second_click(isolated_smeta_db):
    at = open_app()
    _fill_geology(at)
    upload(at, geology_file([{COL_WORK: HOLES, COL_QTY: 7}, {COL_WORK: DRILL_V, COL_QTY: 10}]))
    apply_button(at).click()
    at.run(timeout=30)
    assert any("будет заменён" in w.value for w in at.warning)
    assert at.number_input(key="field_geology_drilling_count").value == 30
    apply_button(at).click()
    at.run(timeout=30)
    assert at.exception == []
    assert at.number_input(key="field_geology_drilling_count").value == 7


def test_export_current_input_reads_back(isolated_smeta_db):
    at = open_app()
    _fill_geology(at)
    [button] = [b for b in at.get("download_button") if b.proto.label == "Скачать в шаблон Excel"]
    assert not button.proto.disabled
    inputs = at.session_state["form_data_by_module"]["geology"]
    refs = ReferenceBook()
    catalog = build_catalog(GeologyModule(), refs, "geology")
    from core.vedomost_import import export_rows, normalized_inputs  # noqa: PLC0415

    rows, _ = export_rows(catalog, inputs)
    result = read_vedomost(workbook_bytes(catalog, SECTION_TITLES["geology"], rows),
                           GeologyModule(), refs, "geology")
    assert result.ok
    assert normalized_inputs(catalog, result.inputs) == normalized_inputs(catalog, inputs)


def test_confirmed_volume_sheet_blocks_apply(isolated_smeta_db):
    at = open_app()
    _fill_geology(at)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    from tests.test_streamlit_app import goto  # noqa: PLC0415

    goto(at, "vedomost")
    upload(at, geology_file([{COL_WORK: HOLES, COL_QTY: 7}]))
    assert at.exception == []
    assert apply_button(at).disabled
    assert any("ведомость подтверждена" in c.value for c in at.caption)


def test_warnings_are_listed_as_table_and_file_can_be_applied(isolated_smeta_db):
    """Замечания — таблицей, как ошибки, с понятной для новичка строкой над ней."""
    at = open_app()
    upload(at, geology_file([{COL_WORK: "Крепление скважин", COL_QTY: 10}]))
    assert at.exception == []
    assert any("Файл можно поставить в ведомость" in w.value for w in at.warning)
    tables = [d.value for d in at.dataframe if "Что не так" in d.value.columns]
    assert tables and any("Число скважин" in text for text in tables[0]["Что не так"])
    assert not apply_button(at).disabled
