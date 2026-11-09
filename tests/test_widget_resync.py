"""Аудит 05.11.2026, О-2: данные объекта терялись на живом сервере, когда дату расчёта
набирали с клавиатуры и сразу щёлкали по пункту меню (гонка двух запросов браузера —
`app/ui.py::resync_widgets_after_page_change`). Саму гонку AppTest не воспроизводит —
она проверяется на живом сервере скриптом `tools/check_object_data_race.py`; здесь —
правило, которое её закрывает: на прогоне со сменой страницы значения виджетов,
живущих всю сессию, заново записываются в состояние (Streamlit тогда отправляет их
браузеру), а на прогоне без смены страницы — нет (иначе стирался бы текст, который
оператор набирает в эту секунду)."""

import datetime
from types import SimpleNamespace

import pytest

from app import ui
from tests.test_streamlit_app import goto, isolated_smeta_db, open_app  # noqa: F401


class RecordingState(dict):
    """Состояние сессии, которое помнит, какие ключи в него записывали."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.written = []

    def __setitem__(self, key, value):
        self.written.append(key)
        super().__setitem__(key, value)


@pytest.fixture
def state(monkeypatch):
    values = RecordingState({
        "obj_code": "Т-01", "obj_distance_km": 22.0, "obj_unfavorable_period": True,
        "obj_note": "", "obj_customer": None, "obj_show_mobilization": False,
        "calc_date": datetime.date(2026, 9, 30), "module_choice": "geology",
        ui.TARGET_PRICE_KEY: 1_000_000.0, "form_data_by_module": {"geology": {"drilling_count": 3}},
    })
    monkeypatch.setattr(ui.st, "session_state", values)
    return values


def run_on(monkeypatch, page):
    monkeypatch.setattr("streamlit.runtime.scriptrunner.get_script_run_ctx",
                        lambda: SimpleNamespace(page_script_hash=page))
    ui.resync_widgets_after_page_change()


def test_values_are_resent_only_when_page_changed(monkeypatch, state):
    run_on(monkeypatch, "p1")  # первый прогон сессии — сравнивать не с чем
    assert state.written == [ui.STARTED_PAGE_KEY]

    state.written.clear()
    run_on(monkeypatch, "p1")  # та же страница — поля не трогаем (оператор может печатать)
    assert state.written == [ui.STARTED_PAGE_KEY]

    state.written.clear()
    run_on(monkeypatch, "p2")  # начат прогон другой страницы
    assert state[ui.STARTED_PAGE_KEY] == "p2"
    resent = set(state.written) - {ui.STARTED_PAGE_KEY}
    assert resent == {"obj_code", "obj_distance_km", "obj_unfavorable_period", "calc_date",
                      "module_choice", ui.TARGET_PRICE_KEY}

    state.written.clear()
    run_on(monkeypatch, "p1")  # и обратно — тот самый случай: прогон страницы 2 прерван
    assert "obj_code" in state.written and state["obj_code"] == "Т-01"


def test_empty_values_and_non_widget_state_are_not_touched(monkeypatch, state):
    run_on(monkeypatch, "p1")
    state.written.clear()
    run_on(monkeypatch, "p2")
    for key in ("obj_note", "obj_customer", "obj_show_mobilization", "form_data_by_module"):
        assert key not in state.written


def test_object_data_survives_page_switches_in_the_app(isolated_smeta_db):  # noqa: F811
    """Правило не мешает обычной работе: значения на месте, исключений нет."""
    at = open_app("obekt")
    at.text_input(key="obj_code").set_value("Т-01")
    at.number_input(key="obj_distance_km").set_value(22.0)
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.run(timeout=30)
    goto(at, "vedomost")
    assert at.exception == []
    goto(at, "obekt")
    assert at.exception == []
    assert at.text_input(key="obj_code").value == "Т-01"
    assert at.number_input(key="obj_distance_km").value == 22.0
    assert at.date_input(key="calc_date").value == datetime.date(2026, 9, 30)
    assert at.session_state[ui.STARTED_PAGE_KEY] is not None


def test_every_session_persisted_widget_is_covered():
    """Новый виджет с persist_state="session" должен попасть под правило: ключ — с
    префиксом `obj_` или в `PERSISTED_WIDGET_KEYS` (иначе его значение может потеряться
    так же, как терялись данные объекта)."""
    import ast
    from pathlib import Path

    app_dir = Path(ui.__file__).parent
    literal_keys = set()
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            keywords = {k.arg: k.value for k in node.keywords}
            persist = keywords.get("persist_state")
            if not (isinstance(persist, ast.Constant) and persist.value == "session"):
                continue
            key = keywords.get("key")
            if isinstance(key, ast.Constant):
                literal_keys.add(key.value)
            elif isinstance(key, ast.Name) and key.id.isupper():
                module = ui if path.name == "ui.py" else __import__(
                    f"app.{path.stem}", fromlist=[key.id])
                literal_keys.add(getattr(module, key.id))
    assert literal_keys, "виджеты с persist_state не найдены — тест смотрит не туда"
    uncovered = {k for k in literal_keys
                 if not (k.startswith("obj_") or k in ui.PERSISTED_WIDGET_KEYS)}
    assert uncovered == set()
