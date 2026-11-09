"""Своя строка — обязательное основание (прогон 03.11, раздел 2 п. 1): без основания строку
не добавить; в таблицах и Excel своя строка помечена как ручная («своя строка — …»); в сводном
бюджете видно, сколько процентов итога раздела дают свои строки; старая смета со своей
строкой без основания открывается с предупреждением и в бюджет не идёт."""

import datetime
import io

import openpyxl
import pytest

import core.persistence as persistence_module
from app import ui
from core.export_excel import export_to_excel
from core.models import PLACEMENT_AFTER_INDEX, PLACEMENT_BEFORE_INDEX, EstimateLine
from core.overrides import (
    BASIS_PRICE_LIST,
    FIELD_CUSTOM,
    Edit,
    OverrideLog,
    custom_line,
    custom_without_basis,
    display_basis,
)
from core.pricing import TotalWithVat
from tests.test_streamlit_app import _click, _ecology_to_estimate, _texts, goto, open_app


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


def data(**changes):
    base = {"name": "Бактериология", "unit": "проба", "qty": 10, "price": 4500.0,
            "basis_kind": BASIS_PRICE_LIST, "basis": "прайс лаборатория 2026",
            "placement": PLACEMENT_AFTER_INDEX}
    base.update(changes)
    return base


@pytest.mark.parametrize("basis", ["", "   ", "\t \n", None])
def test_custom_line_without_basis_is_rejected(basis):
    with pytest.raises(ValueError, match="не указано основание"):
        OverrideLog().add_custom_line(data(basis=basis))


def test_display_basis_marks_custom_line_as_manual():
    line = custom_line("custom.1", data())
    assert display_basis(line) == "своя строка — по прайсу: прайс лаборатория 2026"
    regular = EstimateLine("geology.drill", "Бурение", "п.м.", 1, 1, {}, "A", [], "СБЦ-99 т.9 п.1")
    assert display_basis(regular) == "СБЦ-99 т.9 п.1"


def test_display_basis_and_check_for_custom_line_without_basis():
    """Так выглядит своя строка без основания из старой или испорченной записи."""
    line = custom_line("custom.1", data(basis=""))
    assert display_basis(line) == "своя строка — по прайсу: основание не указано"
    regular = EstimateLine("geology.drill", "Бурение", "п.м.", 1, 1, {}, "A", [], "")
    assert custom_without_basis({"custom.1": line, "geology.drill": regular}) == ["Бактериология"]
    assert custom_without_basis({"custom.2": custom_line("custom.2", data())}) == []


def test_excel_marks_custom_line():
    lines = {"custom.1": custom_line("custom.1", data())}
    buffer = io.BytesIO()
    export_to_excel(lines, buffer)
    ws = openpyxl.load_workbook(io.BytesIO(buffer.getvalue()))["Смета"]
    headers = [c.value for c in ws[1]]
    row = next(r for r in ws.iter_rows(min_row=2, values_only=True) if r[0] == "custom.1")
    assert row[headers.index("Обоснование")] == "своя строка — по прайсу: прайс лаборатория 2026"


def test_custom_lines_share_of_section_total():
    lines = {
        "geology.drill": EstimateLine("geology.drill", "Бурение", "п.м.", 100, 10.0, {}, "A", [], "СБЦ"),
        "custom.1": custom_line("custom.1", data(qty=1, price=50.0, placement=PLACEMENT_BEFORE_INDEX)),
        "custom.2": custom_line("custom.2", data(qty=1, price=3000.0)),
    }
    # база 1000 + 50 до индекса; ×2 = 2100; + 3000 после индекса = 5100
    totals = TotalWithVat(base_total=1050.0, index=2.0, indexed_total=5100.0, vat_rate=0.22,
                          vat_amount=0.0, total_with_vat=0.0)
    assert ui.custom_lines_share(lines, totals) == pytest.approx((50 * 2 + 3000) / 5100)
    no_custom = {"geology.drill": lines["geology.drill"]}
    assert ui.custom_lines_share(no_custom, totals) is None


def _add_custom_line_without_basis(log):
    """Как в версии, сохранённой до обязательного основания: запись журнала напрямую."""
    log._edits.append(Edit("custom.1", FIELD_CUSTOM, 0.0, 0.0, None, custom=data(basis="")))


def test_old_estimate_with_custom_line_without_basis(isolated_smeta_db):
    at = open_app()
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.run(timeout=30)
    _ecology_to_estimate(at)
    session = at.session_state["staged_sessions"]["ecology"]
    _add_custom_line_without_basis(session.overrides["estimate"])
    at.run(timeout=30)
    assert at.exception == []
    # предупреждение в блоке своих строк и запрет подтверждения
    assert any("без основания" in w for w in _texts(at.warning))
    confirm = at.button(key="confirm_ecology_estimate")
    assert confirm.disabled
    assert any("у своих строк нет основания: Бактериология" in c for c in _texts(at.caption))
    # в таблице сметы строка помечена как своя
    table = next(d.value for d in at.dataframe if "Обоснование" in d.value.columns)
    assert "своя строка — по прайсу: основание не указано" in table["Обоснование"].tolist()

    # подтверждённая так смета (старая версия) открывается, но в бюджет не идёт
    session.confirm("estimate", at.session_state["_run_context"].refs)
    goto(at, "svodny")
    assert at.exception == []
    assert any("у своих строк нет основания — в бюджет не вошёл" in w for w in _texts(at.warning))
    tables = [d.value for d in at.dataframe if "Итого с НДС" in d.value.columns]
    assert not any("Экология (ИЭИ)" in t["Раздел"].tolist() for t in tables)


def test_budget_shows_share_of_custom_lines(isolated_smeta_db):
    at = open_app()
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.run(timeout=30)
    _ecology_to_estimate(at)
    session = at.session_state["staged_sessions"]["ecology"]
    session.overrides["estimate"].add_custom_line(data())
    at.run(timeout=30)
    _click(at, "Подтвердить смету")
    goto(at, "svodny")
    assert at.exception == []
    table = next(d.value for d in at.dataframe if "Итого с НДС" in d.value.columns)
    share = table.loc[table["Раздел"] == "Экология (ИЭИ)", ui.CUSTOM_SHARE_COLUMN].iloc[0]
    indexed = table.loc[table["Раздел"] == "Экология (ИЭИ)", "С индексацией"].iloc[0]
    assert share == pytest.approx(round(45000.0 / indexed * 100, 1))
