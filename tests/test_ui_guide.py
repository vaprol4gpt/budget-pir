"""Правила интерфейса docs/ui_guide.md (30.10.2026): меню — шаги процесса
и служебное отдельными пунктами; на каждой странице не больше одной главной
кнопки (`type="primary"`), на страницах шагов — ровно одна; сквозной сценарий
«объект → ведомость → подтвердить → смета → подтвердить → сводный бюджет»
проходит главными кнопками."""

import datetime

import pytest

import core.persistence as persistence_module
from app import ui
from tests.test_streamlit_app import PAGE_FILES, _fill_geology, goto, open_app

# Главная кнопка каждой страницы в начальном состоянии (пустая вкладка):
# метка и доступна ли она. На служебных страницах главного действия нет.
START_PRIMARY = {
    "obekt": ("Перейти к ведомости объёмов", True),
    "vedomost": ("Подтвердить ведомость", False),  # обязательные поля пусты
    "smeta": None,  # ведомость не подтверждена — только ссылка на п. 2
    "minimum": ("Сохранить расчёт себестоимости", False),
    "svodny": ("Скачать сводный бюджет (Excel)", False),
    "spravochniki": None,
    "versii": None,
}


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


def primary_buttons(at):
    """(метка, доступна) всех главных кнопок, в т.ч. кнопок скачивания."""
    found = [(b.label, not b.disabled) for b in at.button if b.proto.type == "primary"]
    found += [
        (b.proto.label, not b.proto.disabled)
        for b in at.get("download_button") if b.proto.type == "primary"
    ]
    return found


def test_menu_sections_cover_every_page_once_in_process_order():
    keys = [key for section in ui.MENU_SECTIONS.values() for key in section]
    assert sorted(keys) == sorted(ui.PAGES)
    assert len(keys) == len(set(keys))
    assert list(ui.MENU_SECTIONS) == ["Шаги процесса", "Служебное"]
    assert ui.MENU_SECTIONS["Шаги процесса"] == [
        ui.PAGE_OBJECT, ui.PAGE_VOLUMES, ui.PAGE_ESTIMATE, ui.PAGE_MINIMUM, ui.PAGE_BUDGET,
    ]


@pytest.mark.parametrize("page", list(PAGE_FILES))
def test_each_page_has_at_most_one_primary_button(isolated_smeta_db, page):
    at = open_app(page)
    assert at.exception == []
    expected = START_PRIMARY[page]
    assert primary_buttons(at) == ([] if expected is None else [expected])


@pytest.mark.parametrize("value, text", [
    (28449810.514942, "28 449 810,51"),
    (0, "0,00"),
    (None, "—"),
    (-1234.5, "-1 234,50"),
])
def test_money_text(value, text):
    assert ui.money_text(value) == text


def test_full_scenario_through_primary_buttons(isolated_smeta_db):
    at = open_app("obekt")
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.run(timeout=30)
    # 1 → 2: главная кнопка страницы объекта переводит на ведомость
    at.button(key="goto_volumes").click()
    at.run(timeout=30)
    goto(at, "vedomost")
    _fill_geology(at)
    assert primary_buttons(at) == [("Подтвердить ведомость", True)]
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    assert at.exception == []
    assert primary_buttons(at) == [("Подтвердить смету", True)]
    at.button(key="confirm_geology_estimate").click()
    at.run(timeout=30)
    # смета подтверждена, изменений нет — главная кнопка недоступна с причиной
    assert primary_buttons(at) == [("Подтвердить смету", False)]
    assert any("изменений после подтверждения нет" in c for c in [c.value for c in at.caption])
    goto(at, "svodny")
    assert at.exception == []
    assert primary_buttons(at) == [("Скачать сводный бюджет (Excel)", True)]


def test_open_version_over_existing_input_asks_second_click(isolated_smeta_db):
    at = open_app()
    at.text_input(key="save_smeta_name").set_value("Замена ввода")
    _fill_geology(at)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    # в той же вкладке есть ввод геологии — первое «Открыть версию» только предупреждает
    goto(at, "versii")
    [b for b in at.button if b.label == "Открыть версию"][0].click()
    at.run(timeout=30)
    assert at.exception == []
    assert any("Нажмите кнопку «Открыть» ещё раз" in w.value for w in at.warning)
    assert "pending_version_load" not in at.session_state
    [b for b in at.button if b.label == "Открыть версию"][0].click()
    at.run(timeout=30)
    assert at.exception == []
    assert not any("ещё раз" in w.value for w in at.warning)
