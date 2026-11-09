"""Этап Д карты (аудит 05.11.2026): пустая ведомость не подтверждается (О-7, В-62),
отрицательные цена и ставка заметны (О-9), резерв минимума — только доля."""

import pytest

from core.cost import close_cost
from core.models import EstimateLine
from core.norms import Norm
from core.stages import SOURCE_CLASS_A, SOURCE_INPUT, SOURCE_OPERATOR, VolumeLine
from core.validation import empty_volume_sheet, negative_quantity_warnings
from tests.test_streamlit_app import goto, isolated_smeta_db, open_app  # noqa: F401


def volume(qty, source):
    return VolumeLine("a", "Строка", "шт", qty, source, "основание")


@pytest.mark.parametrize("lines, expected", [
    ({"a": volume(0, SOURCE_INPUT)}, True),
    ({"a": volume(1, SOURCE_CLASS_A), "b": volume(0, SOURCE_INPUT)}, True),  # программа работ = 1
    ({"a": volume(3, SOURCE_INPUT)}, False),
    ({"a": volume(3, SOURCE_OPERATOR)}, False),
    ({}, True),
])
def test_empty_volume_sheet(lines, expected):
    assert empty_volume_sheet(lines) is expected


@pytest.mark.parametrize("code", ["geophysics", "geodesy", "ecology", "hydromet"])
def test_empty_form_cannot_be_confirmed_in_the_app(isolated_smeta_db, code):  # noqa: F811
    """Четыре раздела, где подтвердить пустую ведомость раньше было можно."""
    at = open_app()
    at.radio(key="module_choice").set_value(code)
    at.run(timeout=30)
    assert at.exception == []
    assert at.button(key=f"confirm_{code}_volumes").disabled
    assert any("нет ни одного объёма больше нуля" in c for c in (m.value for m in at.caption))


def test_negative_price_and_rate_are_warned():
    lines = {
        "price": EstimateLine("price", "Цена", "шт", 1, -5.0, {}, "A", [], ""),
        "rate": EstimateLine("rate", "Доля", "шт", 0, 0, {"rate": -0.5}, "B", ["price"], ""),
        "ok": EstimateLine("ok", "Норма", "шт", 1, 5.0, {"rate": 0.5}, "A", [], ""),
    }
    warnings = negative_quantity_warnings(lines)
    assert len(warnings) == 2
    assert "Отрицательная цена" in warnings[0] and "Отрицательная ставка" in warnings[1]


def _norms():
    return {key: Norm(key, value, "доля", "тест", True)
            for key, value in (("overhead_rate", 0.0), ("taxes_rate", 0.0), ("contingency_rate", 0.1))}


@pytest.mark.parametrize("override", [8.52, -0.5])
def test_contingency_outside_share_is_not_accepted(override):
    # 8,52 вместо 0,0852 давало себестоимость ×9 при «полном» расчёте
    lines = []
    *_rest, rate = close_cost(lines, 0.0, _norms(), override, "тест")
    assert rate == 0.1
    assert "не принято" in lines[-1].basis


def test_contingency_share_is_accepted():
    lines = []
    *_rest, rate = close_cost(lines, 0.0, _norms(), 0.05, "тест")
    assert rate == 0.05
    assert "не принято" not in lines[-1].basis
