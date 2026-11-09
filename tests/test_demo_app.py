"""Демо-база в интерфейсе (доводка 09.11): после «Открыть все разделы расчёта» каждая
страница каждого объекта заполнена — без ошибок, без «не заполнено», с минимумом и
снижением цены «как в КП». Тест держит демо от деградации: изменится программа так, что
демо-расчёт откроется неполным, — тест упадёт.

База собирается `tools/make_demo.py` во временную папку (как при сборке демо-папки).
"""

import pytest

import core.persistence as persistence_module
from core.object_data import ObjectData
from tests.test_streamlit_app import _click, _texts, goto, open_app
from tools import make_demo

OBJECT_TEXT_FIELDS = ("obj_name", "obj_region", "obj_route", "obj_season", "obj_note", "obj_tz_stage")


@pytest.fixture(scope="module")
def demo_file(tmp_path_factory):
    from core.references import DEFAULT_DB_PATH

    if not (make_demo.ROAD_XLSX.exists() and make_demo.KARIER_XLSX.exists()):
        pytest.skip("нет эталона в reference_data/")
    if not DEFAULT_DB_PATH.exists():
        pytest.skip("нет data/references.sqlite")
    path = tmp_path_factory.mktemp("demo_app") / "smety.sqlite"
    saved = make_demo.build_demo(path)
    return path, saved


@pytest.fixture
def demo(demo_file, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", demo_file[0])
    return demo_file


def _open(name):
    at = open_app("versii")
    at.selectbox(key="open_calculation_choice").set_value(name)
    at.run(timeout=30)
    _click(at, "Открыть все разделы расчёта")
    assert at.exception == []
    return at


def _clean(at, where):
    """Страница без исключений и без сообщений об ошибке."""
    assert at.exception == [], where
    assert _texts(at.error) == [], where


def _metrics(at):
    return {m.label: m.value for m in at.metric}


@pytest.mark.parametrize("name", [make_demo.ROAD_NAME, make_demo.KARIER_NAME])
def test_every_page_of_demo_calculation_is_filled(demo, name):
    _path, saved = demo
    codes = list(saved[name])
    at = _open(name)
    obj = at.session_state["_run_context"].object_data
    reductions = ObjectData.from_dict(obj.to_dict()).price_reductions

    # 1. Объект и ТЗ: сведения, даты, параметры ТЗ
    goto(at, "obekt")
    _clean(at, "obekt")
    for key in OBJECT_TEXT_FIELDS:
        assert at.text_input(key=key).value, key
    assert at.date_input(key="obj_start_date").value and at.date_input(key="obj_end_date").value
    assert obj.tz.get("key_volumes") and obj.tz.get("object_kind") and obj.tz.get("survey_scale")
    assert make_demo.DEMO_MARK in obj.note and make_demo.DEMO_MARK in obj.region

    # 2–4. Разделы: ведомость и смета подтверждены, минимум посчитан полностью
    for code in codes:
        at.session_state["module_choice"] = code
        goto(at, "vedomost")
        _clean(at, f"vedomost/{code}")
        assert any("подтверждён" in s for s in _texts(at.success)), code
        goto(at, "smeta")
        _clean(at, f"smeta/{code}")
        assert any("Смета подтверждена" in s for s in _texts(at.success)), code
        goto(at, "minimum")
        _clean(at, f"minimum/{code}")
        metrics = _metrics(at)
        assert metrics["Минимум: себестоимость"] not in ("—", None), code
        assert not any(i.startswith("Не заполнено") for i in _texts(at.info)), code
        assert make_demo.RATES_NOTE in _texts(at.info), code
        table = next(d.value for d in at.dataframe if "Минимум, руб." in d.value.columns)
        states = dict(zip(table["Раздел"], table["Состояние"]))
        assert sum(s == "посчитан" for s in states.values()) == len(codes), states

    # 5. Сводный бюджет: снижение «как в КП» — из расчёта, без ручного ввода
    goto(at, "svodny")
    _clean(at, "svodny")
    for key in [*make_demo.LABELS, "budget"]:
        expected = reductions.get(key, 1.0)
        assert at.number_input(key=f"obj_red_{key}").value == pytest.approx(expected), key
    budget = next(d.value for d in at.dataframe if "Итого с НДС" in d.value.columns)
    checks, budgets, errors = make_demo.expected_checks(_path)
    assert errors == []
    assert budget["Итого с НДС"].iloc[-1] == pytest.approx(budgets[name]["total_with_vat_sum"], abs=0.05)
    metrics = _metrics(at)
    assert metrics["Объект — минимум (себестоимость)"] not in ("—", None)

    # Справочники и Версии открываются без ошибок
    for page in ("spravochniki", "versii"):
        goto(at, page)
        _clean(at, page)


def test_open_one_section_version_keeps_current_reduction(demo):
    """Открытие одной версии раздела снижение не трогает (решение по объекту), открытие
    всего расчёта — подставляет сохранённое с ним."""
    path, saved = demo
    at = _open(make_demo.ROAD_NAME)
    goto(at, "svodny")
    assert at.number_input(key="obj_red_budget").value == pytest.approx(0.8)
    at.number_input(key="obj_red_budget").set_value(0.9)
    at.run(timeout=30)
    goto(at, "versii")
    at.selectbox(key="open_version_choice").set_value(saved[make_demo.KARIER_NAME]["geodesy"])
    at.run(timeout=30)
    _click(at, "Открыть версию")
    _click(at, "Открыть версию")  # вкладка с вводом — подтверждение замены
    assert at.session_state["staged_sessions"]["geodesy"].version_id == \
        saved[make_demo.KARIER_NAME]["geodesy"]  # версия действительно открыта
    goto(at, "svodny")
    assert at.number_input(key="obj_red_budget").value == pytest.approx(0.9)
