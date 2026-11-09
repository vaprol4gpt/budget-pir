import pytest

from core.formatting import (
    FRACTIONAL_UNITS,
    INTEGER_UNITS,
    decimals_for_unit,
    excel_number_format,
    format_quantity,
)

# Все unit=, реально встречающиеся в modules/*/module.py на сегодня (см.
# записи разработки) — по каждому проверяем, что
# decimals_for_unit даёт ожидаемое число знаков.
INTEGER_UNIT_EXAMPLES = [
    "программа",
    "проба",
    "1 пункт",
    "точек",
    "1 точка (выработка)",
    "ф.т",
    "ф.н.",
    "шт",
]

FRACTIONAL_UNIT_EXAMPLES = ["п.м.", "га"]

# Единицы, для которых физический смысл неоднозначен (не угаданы,
# сознательно оставлены с текущей точностью — см. docstring
# core/formatting.py::decimals_for_unit) — тоже часть контракта:
# они ДОЛЖНЫ остаться на DEFAULT_DECIMALS, а не внезапно стать целыми.
AMBIGUOUS_UNIT_EXAMPLES = ["%", "набл.", "опред.", "анализ", "компл."]


@pytest.mark.parametrize("unit", INTEGER_UNIT_EXAMPLES)
def test_integer_units_have_zero_decimals(unit):
    assert decimals_for_unit(unit) == 0
    assert unit in INTEGER_UNITS


@pytest.mark.parametrize("unit", FRACTIONAL_UNIT_EXAMPLES)
def test_fractional_units_keep_two_decimals(unit):
    assert decimals_for_unit(unit) == 2
    assert unit in FRACTIONAL_UNITS


@pytest.mark.parametrize("unit", AMBIGUOUS_UNIT_EXAMPLES)
def test_ambiguous_units_are_not_guessed_and_keep_default(unit):
    assert decimals_for_unit(unit) == 2
    assert unit not in INTEGER_UNITS
    assert unit not in FRACTIONAL_UNITS


def test_unknown_unit_not_in_either_list_keeps_default_two_decimals():
    assert decimals_for_unit("совершенно новая единица") == 2


def test_integer_and_fractional_units_are_disjoint():
    assert INTEGER_UNITS.isdisjoint(FRACTIONAL_UNITS)


# --- format_quantity (Streamlit-таблица: текст) ---


def test_format_quantity_integer_unit_shows_no_decimals():
    assert format_quantity(30.0, "проба") == "30"


def test_format_quantity_integer_unit_rounds_display_only():
    # 74.4 "проба" физически странно, но формат — только отображение,
    # не пересчёт (см. docstring core/formatting.py) — округление до
    # ближайшего целого для показа, число в расчётах не меняется.
    assert format_quantity(74.4, "проба") == "74"


def test_format_quantity_fractional_unit_shows_two_decimals():
    assert format_quantity(14.4, "га") == "14,40"
    assert format_quantity(0.7, "га") == "0,70"


def test_format_quantity_unknown_unit_defaults_to_two_decimals():
    assert format_quantity(5.0, "%") == "5,00"


# --- excel_number_format (openpyxl number_format строка) ---


def test_excel_number_format_integer_unit():
    assert excel_number_format("1 пункт") == "0"


def test_excel_number_format_fractional_unit():
    assert excel_number_format("п.м.") == "0.00"


def test_excel_number_format_unknown_unit_defaults_to_two_decimals():
    assert excel_number_format("набл.") == "0.00"
