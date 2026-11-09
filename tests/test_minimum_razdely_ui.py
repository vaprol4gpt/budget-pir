"""Интерфейс минимального бюджета всех разделов (03.11.2026): страница
«4. Минимальный бюджет» — раздел из боковой панели, общие ставки, «Подставить
состав по норме», общие затраты, сохранение и открытие версии; страница
«5. Сводный бюджет» — минимум по разделам и объекту. AppTest, база смет —
временная (как в tests/test_streamlit_app.py); ставки — выдуманные."""

import pytest

import core.persistence as persistence_module
from app.ui import money_text
from core.cost import SharedCosts, load_cost_norms
from core.persistence import SmetaStore
from modules.geophysics import cost as geophysics
from tests.test_streamlit_app import _click, goto, open_app

SALARIES = {"salary_field_lead": 140000.0, "salary_field_engineer": 120000.0,
            "salary_office_engineer": 100000.0}


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


def _texts(elements):
    return [e.value for e in elements]


def _geophysics_to_minimum():
    at = open_app()
    at.radio(key="module_choice").set_value("geophysics")
    at.run(timeout=30)
    at.number_input(key="field_geophysics_vez_count").set_value(80)
    at.number_input(key="field_geophysics_mpv_count").set_value(400)
    at.run(timeout=30)
    at.button(key="confirm_geophysics_volumes").click()
    at.run(timeout=30)
    goto(at, "minimum")
    assert at.exception == []
    return at


def _fill(at):
    for name, value in SALARIES.items():
        at.number_input(key=f"cost_common_{name}").set_value(value)
    at.number_input(key="cost_geophysics_office_months").set_value(1.0)
    at.run(timeout=30)
    at.button(key="suggest_party_geophysics").click()
    at.run(timeout=30)
    assert at.exception == []


def test_page4_waits_for_volume_sheet_of_selected_section(isolated_smeta_db):
    at = open_app()
    at.radio(key="module_choice").set_value("ecology")
    at.run(timeout=30)
    goto(at, "minimum")
    assert at.exception == []
    assert any("после подтверждения ведомости объёмов раздела" in i for i in _texts(at.info))
    save = at.button(key="save_cost_ecology")
    assert save.disabled
    # сводка всех пяти разделов есть и без расчётов
    table = at.dataframe[0].value
    assert list(table["Раздел"]) == ["Геология (ОИГ)", "Геофизика (ИГФИ)", "Геодезия (ИГДИ)",
                                     "Экология (ИЭИ)", "Гидрометеорология (ИГМИ)"]


def test_page4_geophysics_minimum_saved_and_reopened(isolated_smeta_db):
    at = _geophysics_to_minimum()
    assert at.number_input(key="cost_geophysics_field_lead").value == 0
    _fill(at)
    # 480 точек при норме 100 за 1 мес → 5 чел.: 1 ведущий + 4 инженера, камералка 3
    assert at.number_input(key="cost_geophysics_field_lead").value == 1
    assert at.number_input(key="cost_geophysics_field_engineer").value == 4
    assert at.number_input(key="cost_geophysics_office_engineer").value == 3

    values = {**SALARIES, "field_lead": 1, "field_engineer": 4, "office_engineer": 3,
              "office_months": 1.0}
    expected = geophysics.compute_cost(values, 480, load_cost_norms("geophysics_cost"))
    assert expected.complete
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Минимум: себестоимость"] == money_text(expected.cost)
    assert metrics["Объект — минимум (себестоимость)"] == money_text(expected.cost)
    assert any(w.startswith("Минимум не калиброван: ставки") for w in _texts(at.warning))
    assert any("Контроль по архиву невозможен" in i for i in _texts(at.info))
    table = at.dataframe[0].value
    row = table[table["Раздел"] == "Геофизика (ИГФИ)"].iloc[0]
    assert row["Минимум, руб."] == pytest.approx(round(expected.cost, 2))

    # общие затраты: в минимуме раздела — целиком
    at.number_input(key="cost_shared_mobilization").set_value(100000.0)
    at.run(timeout=30)
    with_shared = geophysics.compute_cost(values, 480, load_cost_norms("geophysics_cost"),
                                          SharedCosts(mobilization=100000))
    assert {m.label: m.value for m in at.metric}["Минимум: себестоимость"] == money_text(with_shared.cost)

    at.button(key="save_cost_geophysics").click()
    at.run(timeout=30)
    assert any("Расчёт себестоимости сохранён" in s for s in _texts(at.success))
    version_id = at.session_state["staged_sessions"]["geophysics"].version_id
    inputs, summary, _ = SmetaStore().load_cost_calc_for_chain(version_id)
    assert inputs["field_engineer"] == 4 and inputs["salary_field_lead"] == 140000.0
    assert inputs["_shared"]["mobilization"] == 100000.0
    assert summary["cost"] == pytest.approx(with_shared.cost)

    at2 = open_app("versii")
    _click(at2, "Открыть версию")
    assert at2.exception == []
    goto(at2, "minimum")
    assert at2.exception == []
    assert at2.number_input(key="cost_geophysics_field_engineer").value == 4
    assert at2.number_input(key="cost_common_salary_field_lead").value == 140000.0
    assert at2.number_input(key="cost_shared_mobilization").value == 100000.0


def test_page5_shows_minimum_by_sections_and_object(isolated_smeta_db):
    at = _geophysics_to_minimum()
    _fill(at)
    minimum = {m.label: m.value for m in at.metric}["Минимум: себестоимость"]
    goto(at, "svodny")
    assert at.exception == []
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Объект — минимум (себестоимость)"] == minimum
    assert "Объект — коэффициент снижения" in metrics
    assert not any("пока реализован только для геологии" in c for c in _texts(at.caption))


def test_hydromet_without_field_months_says_operator_enters_it(isolated_smeta_db):
    from run_budget_stub import HYDROMET_VOLUMES

    at = open_app()
    at.radio(key="module_choice").set_value("hydromet")
    at.run(timeout=30)
    for name, value in HYDROMET_VOLUMES.items():
        at.number_input(key=f"field_hydromet_{name}").set_value(value)
    at.run(timeout=30)
    at.button(key="confirm_hydromet_volumes").click()
    at.run(timeout=30)
    goto(at, "minimum")
    assert at.exception == []
    at.button(key="suggest_party_hydromet").click()
    at.run(timeout=30)
    assert at.number_input(key="cost_hydromet_field_lead").value == 1
    assert any("срок полевых работ (нормы выработки нет — вводит оператор)" in i
               for i in _texts(at.info))
    at.number_input(key="cost_hydromet_field_months").set_value(0.5)
    at.number_input(key="cost_common_salary_field_lead").set_value(140000.0)
    at.run(timeout=30)
    assert at.exception == []
    assert not any("Не заполнено" in i for i in _texts(at.info))


def test_opening_version_without_shared_resets_shared_costs(isolated_smeta_db):
    """Ревью трека 3, №1: открыта версия без общих затрат (сохранена до ввода
    мобилизации / старая) — общие затраты сбрасываются в 0, минимум = сохранённому."""
    at = _geophysics_to_minimum()
    _fill(at)
    at.button(key="save_cost_geophysics").click()  # сохранено без общих затрат
    at.run(timeout=30)
    saved = {m.label: m.value for m in at.metric}["Минимум: себестоимость"]
    at.number_input(key="cost_shared_mobilization").set_value(250000.0)
    at.run(timeout=30)
    assert {m.label: m.value for m in at.metric}["Минимум: себестоимость"] != saved
    goto(at, "versii")
    for _ in range(2):  # в вкладке есть ввод — первое нажатие только предупреждает
        [b for b in at.button if b.label == "Открыть версию"][0].click()
        at.run(timeout=30)
    assert at.exception == []
    goto(at, "minimum")
    assert at.number_input(key="cost_shared_mobilization").value == 0.0
    assert {m.label: m.value for m in at.metric}["Минимум: себестоимость"] == saved


def test_opening_old_geology_cost_without_shared_key_resets_shared(isolated_smeta_db):
    from app import ui_minimum
    from core.stages import SOURCE_INPUT, VolumeLine, make_result

    store = SmetaStore()
    result = make_result("volumes", {"w": VolumeLine("w", "w", "шт", 1, SOURCE_INPUT)}, {}, [], {})
    version = store.save_version("Т-01", "geology", None, result)
    store.save_cost_calc(version, "geology", {"brigades": 2}, {"cost": 1.0})  # формат до 03.11
    at = open_app("minimum")
    at.session_state[ui_minimum.SHARED_KEY] = {"mobilization": 5.0}
    at.session_state["cost_shared_mobilization"] = 5.0
    at.session_state["pending_version_load"] = version
    at.run(timeout=30)
    assert at.session_state["cost_shared_mobilization"] == 0.0
    assert at.session_state[ui_minimum.SHARED_KEY]["mobilization"] == 0.0
