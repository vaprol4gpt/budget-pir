"""Справка в программе и руководство оператора — один источник текста
(03.11.2026): `app/help_texts.json` читают и интерфейс
(`app/ui_help.py`, раскрывающаяся «Как работать с этим шагом» под шапкой каждой
страницы, подсказки «?» полей), и генератор руководства `tools/make_rukovodstvo.py`
(строки справки — начало раздела страницы)."""

import ast
from pathlib import Path

import pytest

import core.persistence as persistence_module
from app import ui, ui_help
from core.object_data import BASE_OBJECT_FIELDS
from tests.test_streamlit_app import PAGE_FILES, open_app
from tools import make_rukovodstvo

APP_DIR = Path(ui.__file__).parent


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


def header_of(page_key):
    """Заголовок, с которым файл страницы вызывает ui.page_header (первый аргумент)."""
    tree = ast.parse((APP_DIR / ui.page_path(page_key)).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "page_header"):
            return ast.literal_eval(node.args[0])
    raise AssertionError(f"{page_key}: нет вызова ui.page_header")


def test_help_file_is_one_source_for_program_and_manual():
    assert make_rukovodstvo.HELP_PATH.resolve() == ui_help.HELP_PATH.resolve()
    assert make_rukovodstvo.load_spravka() == ui_help.load_help()


@pytest.mark.parametrize("page_key", list(ui.PAGES))
def test_every_menu_page_has_help(page_key):
    steps = ui_help.page_steps(page_key)
    assert 3 <= len(steps) <= 6, f"{page_key}: справка — 3–6 строк"
    assert all(s.strip() for s in steps)
    # страница находит свою справку по заголовку шапки
    assert ui_help.page_key_for_header(header_of(page_key)) == page_key


@pytest.mark.parametrize("page_key", list(ui.PAGES))
def test_manual_section_starts_with_program_help(page_key):
    """Раздел страницы в руководстве начинается рамкой со справкой этой страницы, а её
    строки — ровно строки программы."""
    blocks = make_rukovodstvo.content()
    index = blocks.index(("help", page_key))
    assert blocks[index - 1][0] == "h1"
    assert make_rukovodstvo.page_intro(page_key) == ui_help.page_steps(page_key)


def test_manual_covers_all_pages_in_menu_order():
    assert make_rukovodstvo.PAGE_ORDER == list(ui.PAGES)
    helps = [data[0] for kind, *data in make_rukovodstvo.content() if kind == "help"]
    assert helps == list(ui.PAGES)
    images = [data[0] for kind, *data in make_rukovodstvo.content() if kind == "img"]
    assert set(images) == set(make_rukovodstvo.IMAGES)


@pytest.mark.parametrize("page_key", list(PAGE_FILES))
def test_page_shows_help_expander(isolated_smeta_db, page_key):
    at = open_app(page_key)
    assert at.exception == []
    label = ui_help.STEP_LABEL if ui.step_number(page_key) else ui_help.SERVICE_LABEL
    expanders = [e for e in at.expander if e.label == label]
    assert len(expanders) == 1
    text = " ".join(m.value for m in expanders[0].markdown)
    assert ui_help.page_steps(page_key)[0] in text


def test_field_help_covers_object_and_geology_cost_fields():
    assert set(ui_help.load_help()["fields"]["object"]) == {f.name for f in BASE_OBJECT_FIELDS}
    assert set(ui_help.load_help()["fields"]["geology_cost"]) == {n for n, _l, _k in ui.COST_FIELDS}
    assert ui_help.field_help("object", "нет_такого") is None
    assert ui_help.field_help("object", "нет_такого", "запасной") == "запасной"


def test_broken_help_file_gives_no_help(tmp_path):
    bad = tmp_path / "help.json"
    bad.write_text("{не json", encoding="utf-8")
    assert ui_help.load_help(str(bad)) == {}
    assert ui_help.load_help(str(tmp_path / "нет.json")) == {}


@pytest.mark.parametrize("broken", [
    [],                                             # не словарь вовсе
    {"pages": [], "fields": "строка"},              # разделы не того типа
    {"pages": {"obekt": "строка"}, "fields": {"object": ["список"]}},
    {"pages": {"obekt": {"header": "1. Объект и ТЗ", "steps": "не список"}}},
    {"pages": {"obekt": {"header": "1. Объект и ТЗ", "steps": [1, None, " "]}},
     "fields": {"object": {"code": 5}}},
])
def test_broken_types_in_help_give_no_help(monkeypatch, broken):
    """Битые типы на любом уровне файла справки не роняют страницу — справки просто нет."""
    monkeypatch.setattr(ui_help, "load_help", lambda *a, **k: broken)
    assert ui_help.page_steps("obekt") == []
    assert ui_help.field_help("object", "code", "запасной") == "запасной"
    key = ui_help.page_key_for_header("1. Объект и ТЗ")
    assert key in (None, "obekt")
    ui_help.render_page_help("1. Объект и ТЗ", is_step=True)  # без исключения и без вывода
