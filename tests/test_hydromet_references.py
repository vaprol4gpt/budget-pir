"""Парсер справочника ИГМИ (data/build_references.py) на настоящих
эталонных листах: полные сетки CHOOSE, различение поле/камерал,
кэш внешней книги, индекс."""

import openpyxl
import pytest

from data.build_references import (
    ECOLOGY_SOURCE_XLSX,
    HYDROMET_KARIER_SHEET_NAME,
    HYDROMET_ROAD_SHEET_NAME,
    HYDROMET_TABLE_RE,
    SOURCE_XLSX,
    extract_hydromet_index_entries,
    extract_hydromet_price_entries,
    hydromet_category_ii_supplement,
)


@pytest.fixture(scope="module")
def karier():
    wb_f = openpyxl.load_workbook(ECOLOGY_SOURCE_XLSX)
    wb_v = openpyxl.load_workbook(ECOLOGY_SOURCE_XLSX, data_only=True)
    entries, warnings = extract_hydromet_price_entries(wb_f, wb_v, HYDROMET_KARIER_SHEET_NAME)
    return entries, warnings, wb_v


@pytest.fixture(scope="module")
def road():
    wb_f = openpyxl.load_workbook(SOURCE_XLSX)
    wb_v = openpyxl.load_workbook(SOURCE_XLSX, data_only=True)
    return extract_hydromet_price_entries(wb_f, wb_v, HYDROMET_ROAD_SHEET_NAME)


def _prices(entries):
    return {(e.source, e.table, e.item, e.category): e.price for e in entries}


def test_both_sheets_parse_without_warnings(karier, road):
    assert karier[1] == [] and road[1] == []
    assert len(karier[0]) == len(road[0]) > 60


def test_choose_grid_keeps_all_categories(karier):
    prices = _prices(karier[0])
    assert prices[("СБЦ-2000", "26", "1", "I")] == 127
    assert prices[("СБЦ-2000", "26", "1", "II")] == 174
    assert prices[("СБЦ-2000", "26", "1", "III")] == 236
    assert prices[("СБЦ-2000", "43", "2", "kameral/I")] == 6


def test_field_and_kameral_prices_of_same_code_are_separated(karier):
    prices = _prices(karier[0])
    assert prices[("СБЦ-2000", "43", "2", "field/III")] == 24
    assert prices[("СБЦ-2000", "43", "2", "kameral/III")] == 8
    assert prices[("СБЦ-2000", "48", "1", "field/до 20 м")] == 78
    assert prices[("СБЦ-2000", "48", "1", "kameral/до 20 м")] == 17


def test_external_book_cache_values(karier, road):
    karier_prices, road_prices = _prices(karier[0]), _prices(road[0])
    assert karier_prices[("СБЦ-2000", "20", "1", "III")] == 354
    assert karier_prices[("СБЦ-2000", "66", "7", None)] == 1348
    assert road_prices[("СБЦ-2000", "66", "8", None)] == 1800
    assert karier_prices[("СБЦ-2000", "62", "5", None)] == 0.8
    assert karier_prices[("СБЦ-97", "5", "6", None)] == pytest.approx(0.364)


def test_width_grid_of_table_48(road):
    prices = _prices(road[0])
    assert [prices[("СБЦ-2000", "48", "3", w)] for w in
            ("до 20 м", "св. 20 до 100 м", "св. 100 до 300 м")] == [19, 21, 24]


def test_category_ii_supplement_only_for_cached_positions():
    codes = {(e.table, e.item, e.category) for e in hydromet_category_ii_supplement()}
    assert codes == {("43", "2", "field/II"), ("43", "1", "field/II"), ("20", "1", "II"),
                     ("24", "1", "field/II"), ("25", "1", "II")}


def test_category_ii_supplement_is_declared_constant_not_parsed(karier, road):
    """Происхождение цены зафиксировано: эти пять цен кат. II парсер
    эталонов НЕ выдаёт (на эталонах только кат. III), они приходят только
    из констант, и основание строки сметы прямо это говорит (осознанное
    исключение — сметы сторонних клиентов не копируются в репозиторий,
    docs/known_risks.md)."""
    supplement = hydromet_category_ii_supplement()
    parsed_keys = {(e.source, e.table, e.item, e.category) for e in karier[0] + road[0]}
    for entry in supplement:
        assert (entry.source, entry.table, entry.item, entry.category) not in parsed_keys
        assert entry.basis.startswith(f"СБЦ-2000 т.{entry.table} §{entry.item}, кат. II — константа")
        assert "конфиденциальности" in entry.basis


def test_index_value_reuses_geology_period(karier):
    entries, warnings = extract_hydromet_index_entries(
        karier[2][HYDROMET_KARIER_SHEET_NAME], ("2026-07-01", "2026-09-30")
    )
    assert warnings == []
    assert entries[0].value == 81.95 and entries[0].work_type == "hydromet"


@pytest.mark.parametrize("text, expected", [
    ("СБЦ-2000 т. 43 ", "43"), ("СБЦ - 2000 т 62 § 5", "62"),
    ("§ 1   к=1.25 - п.8в стр.7", None),
])
def test_table_regex_ignores_str_abbreviation(text, expected):
    match = HYDROMET_TABLE_RE.search(text)
    assert (match.group(1) if match else None) == expected
