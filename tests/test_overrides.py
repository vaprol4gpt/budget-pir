from core.models import EstimateLine
from core.overrides import Edit, OverrideLog


def make_line(**overrides) -> EstimateLine:
    defaults = dict(
        id="geology.drilling.cat3",
        name="Бурение скважин, кат. III",
        unit="п.м.",
        qty=100.0,
        price=250.0,
        coefs={},
        kind="A",
        base_ids=[],
        basis="СБЦ-99, табл. 9, п. 3",
    )
    defaults.update(overrides)
    return EstimateLine(**defaults)


def test_apply_and_effective_estimate_changes_the_right_field():
    base = {"a": make_line(id="a", qty=100.0)}
    log = OverrideLog()

    log.apply("a", "qty", old_value=100.0, new_value=150.0)
    result = log.effective_estimate(base)

    assert result["a"].qty == 150.0
    assert result["a"].price == base["a"].price


def test_effective_estimate_does_not_mutate_base():
    base = {"a": make_line(id="a", qty=100.0, price=250.0)}
    original = base["a"]
    log = OverrideLog()

    log.apply("a", "price", old_value=250.0, new_value=999.0)
    log.effective_estimate(base)

    assert base["a"] is original
    assert base["a"].price == 250.0


def test_multiple_edits_on_same_line_last_one_wins():
    base = {"a": make_line(id="a", qty=100.0)}
    log = OverrideLog()

    log.apply("a", "qty", old_value=100.0, new_value=150.0)
    log.apply("a", "qty", old_value=150.0, new_value=200.0)
    result = log.effective_estimate(base)

    assert result["a"].qty == 200.0


def test_undo_removes_last_edit_and_restores_previous_value():
    base = {"a": make_line(id="a", qty=100.0)}
    log = OverrideLog()

    log.apply("a", "qty", old_value=100.0, new_value=150.0)
    log.apply("a", "qty", old_value=150.0, new_value=200.0)
    log.undo()
    result = log.effective_estimate(base)

    assert result["a"].qty == 150.0


def test_undo_on_empty_log_does_not_raise_and_returns_false():
    log = OverrideLog()
    assert log.undo() is False


def test_undo_returns_true_when_an_edit_was_removed():
    log = OverrideLog()
    log.apply("a", "qty", old_value=100.0, new_value=150.0)
    assert log.undo() is True


def test_edit_on_missing_line_id_is_skipped_without_raising():
    base = {"a": make_line(id="a", qty=100.0)}
    log = OverrideLog()

    log.apply("does-not-exist", "qty", old_value=1.0, new_value=2.0)
    result = log.effective_estimate(base)

    assert set(result.keys()) == {"a"}
    assert result["a"].qty == 100.0


def test_history_returns_edits_in_order():
    log = OverrideLog()
    log.apply("a", "qty", old_value=100.0, new_value=150.0)
    log.apply("b", "price", old_value=10.0, new_value=20.0)

    history = log.history()

    assert history == [
        Edit("a", "qty", 100.0, 150.0, None),
        Edit("b", "price", 10.0, 20.0, None),
    ]


def test_history_reflects_reason():
    log = OverrideLog()
    log.apply("a", "qty", old_value=100.0, new_value=150.0, reason="уточнение по факту")

    assert log.history()[0].reason == "уточнение по факту"


def test_edits_apply_to_independent_lines_without_interference():
    base = {
        "a": make_line(id="a", qty=100.0, price=250.0),
        "b": make_line(id="b", qty=10.0, price=5.0),
    }
    log = OverrideLog()

    log.apply("a", "qty", old_value=100.0, new_value=150.0)
    log.apply("b", "price", old_value=5.0, new_value=7.5)
    result = log.effective_estimate(base)

    assert result["a"].qty == 150.0
    assert result["b"].price == 7.5


def test_two_independent_logs_for_two_stages_of_one_module():
    # Многоэтапность (core/stages.py): свой OverrideLog на каждый этап —
    # правки и undo одного не видны в другом, класс не менялся.
    from core.stages import SOURCE_CLASS_V, VolumeLine

    volumes_base = {"v": VolumeLine("v", "Монолиты", "проба", 74.4, SOURCE_CLASS_V)}
    estimate_base = {"a": make_line(id="a", qty=74.4, price=100.0)}
    volumes_log, estimate_log = OverrideLog(), OverrideLog()

    volumes_log.apply("v", "qty", 74.4, 60.0)
    estimate_log.apply("a", "price", 100.0, 120.0)
    estimate_log.undo()

    assert volumes_log.effective_estimate(volumes_base)["v"].qty == 60.0
    assert volumes_base["v"].qty == 74.4  # база не мутирована (VolumeLine frozen)
    assert estimate_log.history() == []
    assert len(volumes_log.history()) == 1
    assert estimate_log.effective_estimate(estimate_base)["a"].price == 100.0


# --- своя строка оператора (Э7, Методика п. 6) ---

import pytest  # noqa: E402

from core.models import (  # noqa: E402
    PLACEMENT_AFTER_INDEX,
    PLACEMENT_BEFORE_INDEX,
    calculate_amount,
)
from core.overrides import (  # noqa: E402
    BASIS_ANALOG,
    BASIS_PRICE_LIST,
    FIELD_CUSTOM,
    FIELD_CUSTOM_DELETE,
    is_custom_id,
)


def custom(**overrides):
    data = dict(
        name="Бактериологические исследования почвы", unit="проба", qty=10, price=4500,
        basis_kind=BASIS_PRICE_LIST, basis="прайс ООО «лаборатория»", placement=PLACEMENT_AFTER_INDEX,
    )
    data.update(overrides)
    return data


def test_custom_line_is_added_as_estimate_line_with_basis_and_placement():
    base = {"a": make_line(id="a")}
    log = OverrideLog()

    line_id = log.add_custom_line(custom(), reason="нет в СБЦ-99")
    result = log.effective_estimate(base)

    assert is_custom_id(line_id) and line_id == "custom.1"
    line = result[line_id]
    assert (line.name, line.unit, line.qty, line.price, line.kind) == (
        "Бактериологические исследования почвы", "проба", 10.0, 4500.0, "A",
    )
    assert line.basis == "По прайсу: прайс ООО «лаборатория»"
    assert line.placement == PLACEMENT_AFTER_INDEX
    assert calculate_amount(line, result) == 45000.0
    assert set(base) == {"a"}  # база не мутирована
    edit = log.history()[-1]
    assert (edit.field, edit.old_value, edit.new_value, edit.reason) == (
        FIELD_CUSTOM, 0.0, 45000.0, "нет в СБЦ-99",
    )


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"basis": "  "}, "не указано основание"),
        ({"basis_kind": None}, "вид основания"),
        ({"name": ""}, "наименование"),
        ({"placement": "where"}, "место в смете"),
        ({"qty": -1}, "отрицательными"),
    ],
)
def test_custom_line_requires_basis_name_and_placement(bad, message):
    log = OverrideLog()
    with pytest.raises(ValueError, match=message):
        log.add_custom_line(custom(**bad))
    assert log.history() == []


def test_custom_line_update_and_delete_are_journaled_and_undoable():
    base = {"a": make_line(id="a")}
    log = OverrideLog()
    first = log.add_custom_line(custom())
    second = log.add_custom_line(custom(name="Аналог", basis_kind=BASIS_ANALOG, basis="объект Х",
                                        placement=PLACEMENT_BEFORE_INDEX, qty=1, price=100))

    log.update_custom_line(first, custom(qty=6, price=3800))
    assert log.effective_estimate(base)[first].qty == 6
    assert log.history()[-1].old_value == 45000.0  # сумма до правки — в журнале

    log.remove_custom_line(second, reason="ошибка ввода")
    assert second not in log.effective_estimate(base)
    assert log.history()[-1].field == FIELD_CUSTOM_DELETE
    assert list(log.custom_lines()) == [first]

    log.undo()  # отмена удаления — строка вернулась
    assert second in log.effective_estimate(base)
    # номер удалённой строки повторно не выдаётся
    log.remove_custom_line(second)
    assert log.add_custom_line(custom()) == "custom.3"


def test_custom_delete_cannot_remove_module_line_and_missing_custom_raises():
    base = {"a": make_line(id="a")}
    log = OverrideLog()
    log._edits.append(Edit("a", FIELD_CUSTOM_DELETE, 0.0, 0.0))  # подделанная запись
    assert "a" in log.effective_estimate(base)
    with pytest.raises(KeyError):
        log.remove_custom_line("custom.9")
    with pytest.raises(KeyError):
        log.update_custom_line("custom.9", custom())


def test_from_edits_keeps_custom_line_data():
    log = OverrideLog()
    log.apply("a", "qty", 100.0, 120.0)
    line_id = log.add_custom_line(custom())
    copy_log = OverrideLog.from_edits(log.history())
    assert copy_log.history() == log.history()
    assert copy_log.effective_estimate({"a": make_line(id="a")})[line_id].price == 4500.0


def test_ordinary_qty_edit_applies_to_custom_line():
    log = OverrideLog()
    line_id = log.add_custom_line(custom())
    log.apply(line_id, "qty", 10.0, 12.0)
    assert log.effective_estimate({})[line_id].qty == 12.0
