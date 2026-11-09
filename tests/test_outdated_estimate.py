"""Смета, выпавшая из бюджета из-за изменения чисел (В-42; прогон 03.11, раздел 2 п. 3):
оператор видит, что изменилось (было/стало по итогу раздела), почему, и одну кнопку
«Пересчитать и подтвердить заново»; после неё смета снова в сводном бюджете."""

import datetime
import re

import pytest

import core.persistence as persistence_module
from app import ui
from tests.test_streamlit_app import _budget_table, _geology_confirmed_estimate, _texts, goto, open_app


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


def primary(at):
    return [b for b in at.button if b.proto.type == "primary"]


def test_outdated_estimate_shows_was_became_and_one_recalc_button(isolated_smeta_db):
    at = open_app("obekt")
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.number_input(key="obj_distance_km").set_value(22.0)
    at.run(timeout=30)
    goto(at, "vedomost")
    _geology_confirmed_estimate(at)
    was = at.session_state["staged_sessions"]["geology"].result("estimate")

    # изменились числа (здесь — расстояние объекта: другой пункт внутреннего транспорта)
    goto(at, "obekt")
    at.number_input(key="obj_distance_km").set_value(3.0)
    at.run(timeout=30)
    goto(at, "smeta")
    assert at.exception == []
    warning = next(w for w in _texts(at.warning) if "Смета устарела" in w)
    amounts = re.findall(r"был ([^,]+,\d\d руб\.), стал ([^,]+,\d\d руб\.)", warning)
    assert amounts and amounts[0][0] != amounts[0][1], warning
    assert "изменились данные объекта, справочник или программа" in warning
    # одна главная кнопка — «Пересчитать и подтвердить заново», доступна
    buttons = primary(at)
    assert [b.label for b in buttons] == [ui.RECALC_CONFIRM_LABEL]
    assert not buttons[0].disabled

    buttons[0].click()
    at.run(timeout=30)
    assert at.exception == []
    session = at.session_state["staged_sessions"]["geology"]
    assert session.result("estimate").confirmed_at != was.confirmed_at
    goto(at, "svodny")
    assert list(_budget_table(at)["Раздел"]) == ["Геология (ОИГ)", "ИТОГО"]


def test_estimate_changed_by_operator_edits_keeps_plain_confirm(isolated_smeta_db):
    """Правки оператора после подтверждения — не «устарела сама»: обычная кнопка."""
    at = open_app("vedomost")
    _geology_confirmed_estimate(at)
    session = at.session_state["staged_sessions"]["geology"]
    line_id, line = next(iter(session.result("estimate").lines.items()))
    session.overrides["estimate"].apply(line_id, "price", line.price, line.price + 1)
    at.run(timeout=30)
    assert any("после подтверждения внесены правки" in w for w in _texts(at.warning))
    assert [b.label for b in primary(at)] == ["Подтвердить смету"]
