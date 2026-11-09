"""AppTest меню по шагам процесса компании (29.10.2026): каждая страница
рисуется без исключений, боковая панель показывает, где ты в процессе,
сквозной сценарий «объект → ведомость → смета → сводный бюджет» по всем
пяти модулям даёт в интерфейсе ту же контрольную сумму, что
`run_budget_stub.py --date 2026-09-30` (28 680 880,97 после Э6а геологии 30.10.2026; до неё 28 449 810,51; ещё раньше — 27 983 420,18 при НДС 20 %)."""

import datetime

import pytest

import core.persistence as persistence_module
import run_budget_stub
from core.aggregator import BudgetAggregator
from core.references import ReferenceBook
from core.stages import STATUS_CONFIRMED
from tests.test_streamlit_app import PAGE_FILES, goto, open_app

PAGE_HEADERS = {
    "obekt": "1. Объект и ТЗ",
    "vedomost": "2. Ведомость объёмов",
    "smeta": "3. Максимальный бюджет (смета по сборникам)",
    "minimum": "4. Минимальный бюджет (себестоимость)",
    "svodny": "5. Сводный бюджет",
    "spravochniki": "Справочники",
    "versii": "Сохранённые сметы",
}


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


def _texts(elements):
    return [element.value for element in elements]


@pytest.mark.parametrize("page", list(PAGE_FILES))
def test_every_page_renders_without_exceptions(isolated_smeta_db, page):
    at = open_app(page)
    assert at.exception == []
    assert at.error == []
    assert PAGE_HEADERS[page] in _texts(at.header)
    # боковая панель — на каждой странице
    assert at.sidebar.date_input(key="calc_date") is not None
    assert at.sidebar.radio(key="module_choice") is not None
    assert at.sidebar.text_input(key="save_smeta_name") is not None


def test_default_page_is_object_and_tz(isolated_smeta_db):
    at = open_app("obekt")
    assert "1. Объект и ТЗ" in _texts(at.header)
    # с 03.11.2026 вместо заглушки — ввод параметров ТЗ и «Предложить ведомость» (core/tz.py)
    assert at.number_input(key="obj_tz_length_km").value is None
    assert at.button(key="tz_propose").label == "Предложить ведомость"


def test_sidebar_shows_object_and_step_status(isolated_smeta_db):
    at = open_app("obekt")
    at.text_input(key="obj_code").set_value("Т-01")
    at.text_input(key="obj_name").set_value("АД учебная")
    at.run(timeout=30)
    goto(at, "vedomost")
    captions = _texts(at.sidebar.caption)
    assert any("Т-01 — АД учебная" in c for c in captions)
    assert any(c.startswith("Геология (ОИГ): не начат") or "Геология (ОИГ):" in c for c in captions)

    at.number_input(key="group_geology_drilling_by_category_0_drilling_length").set_value(450)
    at.selectbox(key="group_geology_drilling_by_category_0_soil_category").set_value("IV")
    at.number_input(key="field_geology_drilling_count").set_value(30)
    at.run(timeout=30)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    # подтверждение ведомости само переводит на страницу сметы
    assert "3. Максимальный бюджет (смета по сборникам)" in _texts(at.header)
    status = next(c for c in _texts(at.sidebar.caption) if c.startswith("Геология (ОИГ):"))
    assert "ведомость объёмов работ — подтверждён" in status.lower()
    assert "смета — не начата" in status.lower()


def test_opening_minimum_page_does_not_mark_geology_started(isolated_smeta_db):
    # страница «Минимальный бюджет» создаёт пустую сессию геологии — статус
    # в боковой панели остаётся «не начат» (находка независимой проверки Ф5)
    at = open_app("minimum")
    goto(at, "svodny")
    status = next(c for c in _texts(at.sidebar.caption) if c.startswith("Геология (ОИГ):"))
    assert status == "Геология (ОИГ): не начат"


def test_volume_page_shows_confirmed_sheet_read_only_with_return(isolated_smeta_db):
    at = open_app()
    at.number_input(key="group_geology_drilling_by_category_0_drilling_length").set_value(450)
    at.selectbox(key="group_geology_drilling_by_category_0_soil_category").set_value("IV")
    at.number_input(key="field_geology_drilling_count").set_value(30)
    at.run(timeout=30)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)

    goto(at, "vedomost")
    assert at.exception == []
    # формы ввода ведомости нет, есть снимок и кнопка возврата
    assert not [w for w in at.number_input if w.key == "field_geology_drilling_count"]
    assert any("только для чтения" in s for s in _texts(at.success))
    assert [b for b in at.button if b.key == "reopen_geology_volumes"]

    at.button(key="reopen_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "vedomost")
    assert at.number_input(key="field_geology_drilling_count").value == 30
    assert at.session_state["staged_sessions"]["geology"].current == "volumes"


def test_estimate_page_before_volume_confirmation_points_to_volume_page(isolated_smeta_db):
    at = open_app("smeta")
    assert at.exception == []
    assert any("Сначала подтвердите этап" in i for i in _texts(at.info))


def test_references_page_lists_prices_indices_and_norms(isolated_smeta_db):
    at = open_app("spravochniki")
    assert at.exception == []
    refs = ReferenceBook()
    tables = [d.value for d in at.dataframe]
    prices = next(t for t in tables if "Сборник" in t.columns)
    assert len(prices) == len(refs.all_prices())
    indices = next(t for t in tables if "Индекс" in t.columns and "Вид работ" in t.columns)
    assert len(indices) == len(refs.all_indices())
    assert any("Норма" in t.columns for t in tables)


# --- сквозной сценарий: те же данные, что run_budget_stub.py ---


def _set(at, key, value):
    """Значение поля формы по ключу виджета: число или выбор из списка."""
    for widget in at.number_input:
        if widget.key == key:
            widget.set_value(value)
            return
    at.selectbox(key=key).set_value(value)


def _module_through_both_stages(at, code, volumes, estimate=None, groups=None):
    goto(at, "vedomost")
    at.radio(key="module_choice").set_value(code)
    at.run(timeout=30)
    for name, rows in (groups or {}).items():
        for _ in rows[1:]:
            at.button(key=f"add_{code}_{name}").click()
            at.run(timeout=30)
        row_ids = sorted({
            int(w.key.split("_")[-3]) for w in list(at.number_input) + list(at.selectbox)
            if w.key and w.key.startswith(f"group_{code}_{name}_")
        })
        for row_id, row in zip(row_ids, rows):
            for sub_name, value in row.items():
                _set(at, f"group_{code}_{name}_{row_id}_{sub_name}", value)
        at.run(timeout=30)
    for name, value in volumes.items():
        _set(at, f"field_{code}_{name}", value)
    at.run(timeout=30)
    at.button(key=f"confirm_{code}_volumes").click()
    at.run(timeout=30)
    # подтверждение ведомости само переводит на страницу сметы
    assert "3. Максимальный бюджет (смета по сборникам)" in _texts(at.header)
    goto(at, "smeta")
    for name, value in (estimate or {}).items():
        _set(at, f"field_{code}_estimate_{name}", value)
    at.run(timeout=30)
    at.button(key=f"confirm_{code}_estimate").click()
    at.run(timeout=30)
    assert at.exception == []
    assert at.session_state["staged_sessions"][code].last_stage_status() == STATUS_CONFIRMED


def test_full_scenario_object_volumes_estimate_budget_matches_stub(isolated_smeta_db):
    at = open_app("obekt")
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.run(timeout=30)

    geology_groups = {"drilling_by_category": run_budget_stub.GEOLOGY_INPUT["drilling_by_category"]}
    _module_through_both_stages(
        at, "geology", {"drilling_count": run_budget_stub.GEOLOGY_INPUT["drilling_count"]},
        groups=geology_groups,
    )
    _module_through_both_stages(at, "geophysics", run_budget_stub.GEOPHYSICS_INPUT)
    _module_through_both_stages(at, "geodesy", run_budget_stub.GEODESY_INPUT)
    ecology = dict(run_budget_stub.ECOLOGY_INPUT)
    _module_through_both_stages(at, "ecology", ecology, groups={"sampling": ecology.pop("sampling")})
    _module_through_both_stages(
        at, "hydromet", run_budget_stub.HYDROMET_VOLUMES, estimate=run_budget_stub.HYDROMET_PARAMETERS
    )

    goto(at, "svodny")
    assert at.exception == []
    table = next(d.value for d in at.dataframe if "Итого с НДС" in d.value.columns)
    assert list(table["Раздел"]) == [
        "Геология (ОИГ)", "Геофизика (ИГФИ)", "Геодезия (ИГДИ)", "Экология (ИЭИ)",
        "Гидрометеорология (ИГМИ)", "ИТОГО",
    ]
    expected = run_budget_stub.build_budget(BudgetAggregator(), ReferenceBook(), "2026-09-30")
    expected_total = expected.summary()["total_with_vat_sum"]
    assert round(expected_total, 2) == 28680880.97  # контрольная цифра описание проекта
    assert table["Итого с НДС"].iloc[-1] == pytest.approx(28680880.97, abs=0.005)
    assert any("28 680 880,97 руб." in m for m in _texts(at.markdown))
