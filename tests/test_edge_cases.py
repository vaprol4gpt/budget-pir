"""Граничные случаи расчёта (Э2 дорожной карты, 25.10.2026): правка
ставки у строк «доля от базы», отрицательный объём, цикл base_ids,
excel_round, kind B с пустым base_ids, повторный add_module, проверка
min_value, общие хелперы модулей."""

import pytest

from core.aggregator import BudgetAggregator, ModuleBudget
from core.models import EstimateLine, calculate_amount, excel_round
from core.module_base import Field
from core.module_helpers import (
    MISSING_PRICE_BASIS_PREFIX,
    estimate_editable_by,
    get_price_or_missing,
    volume_sheet_editable,
)
from core.overrides import OverrideLog
from core.stages import SOURCE_CLASS_A, SOURCE_INPUT, Stage, StagedModule, StagedSession, VolumeLine
from core.validation import below_min_fields, negative_quantity_warnings


def a_line(line_id, qty=10.0, price=5.0):
    return EstimateLine(line_id, line_id, "шт", qty, price, {}, "A", [], "осн.")


def b_line(line_id, base_ids, rate=0.1):
    return EstimateLine(line_id, line_id, "%", 0.0, 0.0, {"rate": rate}, "B", list(base_ids), "осн.")


@pytest.fixture
def estimate():
    lines = [a_line("a"), a_line("b", 2, 25), b_line("t", ["a", "b"], 0.0875)]
    return {line.id: line for line in lines}


# --- правка ставки у kind B ---


def test_rate_edit_changes_amount(estimate):
    log = OverrideLog()
    log.apply("t", "rate", 0.0875, 0.1)
    effective = log.effective_estimate(estimate)
    assert effective["t"].coefs == {"rate": 0.1}
    assert calculate_amount(effective["t"], effective) == pytest.approx(100 * 0.1)
    assert estimate["t"].coefs == {"rate": 0.0875}  # база не мутирована


def test_qty_and_price_edits_of_rate_line_are_rejected(estimate):
    log = OverrideLog()
    log.apply("t", "qty", 0.0, 5.0)
    log.apply("t", "price", 0.0, 7.0)
    effective = log.effective_estimate(estimate)
    assert effective["t"] == estimate["t"]


def test_rate_edit_of_kind_a_line_is_rejected(estimate):
    log = OverrideLog()
    log.apply("a", "rate", 0.0, 2.0)
    assert log.effective_estimate(estimate)["a"] == estimate["a"]


def _staged_session_with_estimate(estimate):
    def editable_volume_key(line_id):
        return None

    stage = Stage(
        number=1, key="estimate", title="Смета", fields=[],
        compute=lambda inputs, previous, refs, pinned: dict(estimate),
        editable_fields=estimate_editable_by(editable_volume_key),
    )
    return StagedSession(StagedModule(object(), [stage]))


def test_staged_qty_edit_of_rate_line_goes_to_not_applied(estimate):
    session = _staged_session_with_estimate(estimate)
    session.overrides["estimate"].apply("t", "qty", 0.0, 5.0)
    session.overrides["estimate"].apply("t", "rate", 0.0875, 0.2)
    draft = session.compute("estimate", refs=None)
    assert [(e.line_id, e.field) for e in draft.not_applied] == [("t", "qty")]
    assert calculate_amount(draft.effective["t"], draft.effective) == pytest.approx(100 * 0.2)


def test_estimate_editable_by_rules():
    sheet = type("Snapshot", (), {"lines": {"x": None}})()
    editable = estimate_editable_by(lambda line_id: line_id.removeprefix("m."))
    assert editable(b_line("m.t", []), {"volumes": sheet}) == ("rate",)
    assert editable(a_line("m.x"), {"volumes": sheet}) == ("price",)
    assert editable(a_line("m.y"), {"volumes": sheet}) == ("qty", "price")
    assert editable(a_line("m.x"), {}) == ("qty", "price")


def test_volume_sheet_editable():
    assert volume_sheet_editable(VolumeLine("v", "v", "шт", 1, SOURCE_INPUT), {}) == ()
    assert volume_sheet_editable(VolumeLine("v", "v", "шт", 1, SOURCE_CLASS_A), {}) == ("qty",)


# --- отрицательный объём ---


def test_negative_quantity_is_warned_in_stage(estimate):
    estimate["a"] = a_line("a", qty=-3)
    session = _staged_session_with_estimate(estimate)
    warnings = session.compute("estimate", refs=None).warnings
    assert any("Отрицательный объём" in w for w in warnings)


def test_negative_quantity_warnings_ignore_non_negative(estimate):
    assert negative_quantity_warnings(estimate) == []


def test_below_min_fields_number_and_group():
    schema = [
        Field(name="n", label="Скважин", type="number", required=True),
        Field(name="k", label="Коэф.", type="number", required=False, min_value=None),
        Field(
            name="g", label="Бурение", type="group", required=True,
            group_fields=[Field(name="len", label="Метраж", type="number", required=True)],
        ),
    ]
    data = {"n": -1, "k": -5, "g": [{"len": 10}, {"len": -2}]}
    assert below_min_fields(schema, data) == ["Скважин", "Бурение, строка 2: Метраж"]
    assert below_min_fields(schema, {"n": 0, "g": [{"len": 0}]}) == []


def test_field_min_value_defaults_to_zero():
    assert Field(name="n", label="n", type="number", required=False).min_value == 0


# --- цикл, пустая база, округление ---


def test_cycle_in_base_ids_raises_with_path():
    lines = {"x": b_line("x", ["y"]), "y": b_line("y", ["x"])}
    with pytest.raises(ValueError, match="x → y → x"):
        calculate_amount(lines["x"], lines)


def test_self_reference_raises():
    lines = {"x": b_line("x", ["x"])}
    with pytest.raises(ValueError, match="цикл"):
        calculate_amount(lines["x"], lines)


def test_kind_b_with_empty_base_ids_is_zero():
    lines = {"t": b_line("t", [], 0.5)}
    assert calculate_amount(lines["t"], lines) == 0.0


def test_kind_b_without_rate_raises():
    line = EstimateLine("t", "t", "%", 0, 0, {}, "B", [], "")
    with pytest.raises(ValueError, match="rate"):
        calculate_amount(line, {"t": line})


@pytest.mark.parametrize(
    "value, digits, expected",
    [(38.5, 0, 39.0), (-38.5, 0, -39.0), (2.675, 2, 2.68), (0.125, 2, 0.13), (1.005, 2, 1.01),
     (1234.5678, -2, 1200.0), (0.0, 0, 0.0)],
)
def test_excel_round_half_away_from_zero(value, digits, expected):
    assert excel_round(value, digits) == expected


# --- повторный add_module ---


def _budget(code):
    return ModuleBudget(code, code, 100.0, 8195.0, 1639.0, 9834.0)


def test_add_module_twice_raises():
    aggregator = BudgetAggregator()
    aggregator.add_module(_budget("geology"))
    with pytest.raises(ValueError, match="уже добавлен"):
        aggregator.add_module(_budget("geology"))
    aggregator.add_module(_budget("geodesy"))
    assert [m.module_code for m in aggregator.summary()["modules"]] == ["geology", "geodesy"]


# --- общий поиск цены ---


class _Refs:
    def get_price(self, source, table, item, category=None):
        if (source, table, item) == ("СБЦ-99", "4", "3"):
            return 0.0875, "Т4 п.3"
        raise KeyError("нет")


def test_get_price_or_missing():
    assert get_price_or_missing(_Refs(), "СБЦ-99", "4", "3", None, "Транспорт") == (0.0875, "Т4 п.3")
    price, basis = get_price_or_missing(_Refs(), "СБЦ-99", "4", "9", None, "Транспорт")
    assert price == 0.0
    assert basis.startswith(MISSING_PRICE_BASIS_PREFIX) and "«Транспорт»" in basis


def test_geology_transport_without_rate_in_refs_is_missing_not_error():
    from modules.geology.module import GeologyModule

    class RefsWithoutTransport(_Refs):
        def get_price(self, source, table, item, category=None):
            if (table, item) == ("4", "3"):
                raise KeyError("нет транспорта")
            return 1.0, "осн."

    module = GeologyModule()
    volumes = module.build_volumes(
        {"drilling_by_category": [{"soil_category": "IV", "drilling_length": 10}], "drilling_count": 1},
        RefsWithoutTransport(),
    )
    estimate = module.build_estimate(volumes, RefsWithoutTransport())
    transport = estimate["geology.transport"]
    assert transport.coefs["rate"] == 0.0
    assert transport.basis.startswith(MISSING_PRICE_BASIS_PREFIX)
    assert any("Внутренний транспорт" in w for w in module.check(estimate))
