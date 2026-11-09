"""Минимальный бюджет — готовность к калибровке (, 03.11.2026).

1. Статус калибровки: откуда ставки (от бухгалтерии — признак "kind":
   "accounting" у записи локального слоя / цены / допущения / ввод).
2. Минимум > максимума — объяснение по статьям, доля ФОТ против ориентира,
   постоянные затраты; коэффициент не показывается как рекомендация.
3. Структура по статьям: сумма статей = себестоимость.

Ставки в тестах — ВЫДУМАННЫЕ круглые числа «похожие на себестоимостные»
(не ставки компании, не из исходников заказчика); подаются как локальный слой
через временную папку — так же, как его читает программа.
"""

import datetime
import json

import pytest

import core.norms
from core.norms import load_norms, local_input_defaults
from core.pricing import calculate_total_with_vat
from core.references import DEFAULT_DB_PATH, ReferenceBook
from core.stages import StagedSession, as_staged
from modules.geology.cost import (
    ARTICLE_FIXED,
    REDUCTION_IMPOSSIBLE,
    CostInputs,
    archive_disclaimer,
    coefficient_text,
    compute_cost,
    cost_structure,
    fot_share_text,
    load_archive,
    rates_status,
    reduction,
)
from modules.geology.module import GeologyModule

FAKE_SOURCE = "ТЕСТ: выдуманная ставка, не компании"
FAKE_DATE = "2026-11-01"
# Выдуманные «себестоимостные» ставки: оклады с районным, руб./мес; нормы на человека.
FAKE_SALARIES = {
    "salary_worker": 120000, "salary_itr": 140000, "salary_driver": 100000,
    "salary_cook": 80000, "salary_office": 110000,
}
FAKE_NORMS = {
    "field_allowance_per_person_month": 15000, "housing_per_person_month": 25000,
    "travel_per_person": 40000,
}


def write_local(folder, kind="accounting"):
    """Локальный слой с выдуманными ставками; `kind` — признак происхождения
    (как его ставит импорт шаблона бухгалтерии); None — без признака."""
    extra = {} if kind is None else {"kind": kind, "date": FAKE_DATE}
    section = {
        "inputs": {k: {"value": v, "source": FAKE_SOURCE, **extra} for k, v in FAKE_SALARIES.items()},
        "norms": {k: {"value": v, "source": FAKE_SOURCE, "assumption": False, **extra}
                  for k, v in FAKE_NORMS.items()},
    }
    folder.mkdir(parents=True, exist_ok=True)
    (folder / core.norms.LOCAL_FILE).write_text(
        json.dumps({"_формат": 1, "geology_cost": section}, ensure_ascii=False), encoding="utf-8"
    )
    return folder


# Партия «как в Форме 2.2» по составу (аудит 30.10.2026 §4.1): 3 бригады по 3 чел.,
# 2 ИТР, 2 водителя, повар; камералка — 3 чел. × 1,5 мес; техника и топливо — условные.
PARTY = dict(
    brigades=3, itr=2, drivers=2, cooks=1, office_staff=3, office_months=1.5,
    mileage_uaz=2000, mileage_ural=1000, fuel_price_ai92=60, fuel_price_diesel=70, lab_samples=20,
)


def party_inputs(**changes):
    return CostInputs(**{**PARTY, **FAKE_SALARIES, **changes})


@pytest.fixture
def accounting_layer(tmp_path):
    return write_local(tmp_path / "local")


# --- 1. статус калибровки: откуда ставки ---


def test_seed_rates_are_assumptions(tmp_path):
    norms = load_norms("geology_cost", local_dir=tmp_path)  # слоя нет — допущения
    status = rates_status(party_inputs(), norms, {})
    assert not status.from_accounting
    text = status.text()
    assert text.startswith("Минимум не калиброван: ставки — допущения / цены, не себестоимость")
    assert "допущения: полевое довольствие, жильё, проезд" in text
    # оклады введены вручную (локального слоя нет) — «ввод на странице»
    assert "ввод на странице: оклад бурового рабочего" in text


def test_accounting_layer_gives_firm_rates_but_still_not_calibrated(accounting_layer):
    norms = load_norms("geology_cost", local_dir=accounting_layer)
    local = local_input_defaults("geology_cost", local_dir=accounting_layer)
    assert local["salary_worker"].kind == "accounting" and local["salary_worker"].date == FAKE_DATE
    status = rates_status(party_inputs(), norms, local)
    assert status.from_accounting
    text = status.text()
    assert text.startswith("Ставки компании от бухгалтерии")
    assert FAKE_SOURCE in text and FAKE_DATE in text
    assert "не калиброван по факту закрытого объекта" in text


def test_layer_without_kind_is_not_accounting(tmp_path):
    # файлы, заполненные до 03.11.2026 (без признака), — не «от бухгалтерии»
    folder = write_local(tmp_path / "old", kind=None)
    status = rates_status(party_inputs(), load_norms("geology_cost", local_dir=folder),
                          local_input_defaults("geology_cost", local_dir=folder))
    assert not status.from_accounting
    assert "локальный слой без отметки «от бухгалтерии»" in status.text()


def test_price_kind_and_operator_change_are_named(tmp_path):
    folder = write_local(tmp_path / "price", kind="price")
    local = local_input_defaults("geology_cost", local_dir=folder)
    # один оклад оператор поменял — это уже не ставка бухгалтерии
    status = rates_status(party_inputs(salary_itr=1.0), load_norms("geology_cost", local_dir=folder), local)
    text = status.text()
    assert "цены «Бюджетного расчёта» (Форма 2.2): оклад бурового рабочего" in text
    assert "ввод на странице: оклад ИТР" in text


def test_salary_of_absent_role_does_not_block_accounting_status(accounting_layer):
    norms = load_norms("geology_cost", local_dir=accounting_layer)
    local = local_input_defaults("geology_cost", local_dir=accounting_layer)
    status = rates_status(party_inputs(cooks=0, salary_cook=None), norms, local)
    assert status.from_accounting
    assert "salary_cook" not in {r.key for r in status.rates}


# --- 2–3. структура по статьям, средний и малый объект ---


def module_max(meters, holes):
    """Максимум для малого объекта: модуль геологии (бурение IV категории)
    через оба этапа, с индексацией на 30.09.2026, без НДС."""
    refs = ReferenceBook()
    session = StagedSession(as_staged(GeologyModule()))
    session.inputs["volumes"] = {
        "drilling_by_category": [{"soil_category": "IV", "drilling_length": meters}],
        "drilling_count": holes,
    }
    session.confirm("volumes", refs)
    session.inputs["estimate"] = {}
    lines = dict(session.confirm("estimate", refs).lines)
    return calculate_total_with_vat(lines, refs, work_type="geology", date="2026-09-30").indexed_total


@pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite")
def test_medium_object_minimum_below_maximum(accounting_layer):
    openpyxl = pytest.importorskip("openpyxl")
    from tests.test_geology_etalon import ETALON, SHEET, run_sheet

    if not ETALON.exists():
        pytest.skip("нет эталона в reference_data/")
    ws = openpyxl.load_workbook(ETALON, data_only=True)[SHEET]
    # средний объект Т-01 (эталон Автодороги, согласованный, reference_data/): метраж
    # бурения листа и максимум — модуль на объёмах листа × индекс листа 81,95 (без НДС)
    meters = sum(ws[f"D{row}"].value for row in range(29, 34))
    assert meters == 1150
    _lines, amounts = run_sheet(ws)
    maximum = sum(amounts.values()) * 81.95

    norms = load_norms("geology_cost", local_dir=accounting_layer)
    cost = compute_cost(party_inputs(), meters, distance_km=300, norms=norms)
    assert cost.complete
    red = reduction(cost, maximum)
    assert cost.cost < maximum and red.possible and red.warnings == []
    # Разумный диапазон коэффициента 0,25–0,95. Снизу: факт архива — 6 470–9 308
    # руб./п.м. в ценах 2016–2022 гг. (reference_data/archive_unit_costs.json), это
    # ≈ 0,26–0,37 от максимума 1 п.м. листа в ценах 2026 г.; себестоимость 2026 г.
    # ниже старых цен — признак ошибки ввода. Сверху: при 0,95 и выше снижать почти
    # нечего — на практике компании снижает на ×0,65–0,85 (Э9), т.е. себестоимость ниже.
    assert 0.25 <= red.coefficient <= 0.95
    assert coefficient_text(red) == f"{red.coefficient:.3f}".replace(".", ",")

    structure = cost_structure(cost)
    assert sum(a.amount for a in structure) == pytest.approx(cost.cost)
    assert sum(a.share for a in structure) == pytest.approx(1.0)
    assert {a.key for a in structure if a.kind == ARTICLE_FIXED} >= {"travel", "fot_office", "lowboy"}
    fot = fot_share_text(cost)
    assert fot.startswith("Доля ФОТ (фонда оплаты труда) — ") and "ориентир 30–33 %" in fot
    assert "ДОПУЩЕНИЕ" in fot


@pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite")
def test_small_object_with_full_party_minimum_above_maximum_is_explained(accounting_layer):
    # малый объект (300 п.м., как карьер) с полной партией и камералкой 13 чел. × 1 мес
    # (состав Формы 2.2 по аудиту §4.3) — постоянные затраты не делятся на метраж
    maximum = module_max(300, 20)
    norms = load_norms("geology_cost", local_dir=accounting_layer)
    cost = compute_cost(party_inputs(office_staff=13, office_months=1), 300, distance_km=300, norms=norms)
    red = reduction(cost, maximum)
    assert cost.cost > maximum
    assert not red.possible and coefficient_text(red) == REDUCTION_IMPOSSIBLE
    text = "\n".join(red.warnings)
    assert "снижение невозможно" in text
    assert "Больше всего себестоимость тянут статьи:" in text
    assert "Доля ФОТ" in text and "ориентир 30–33 %" in text
    assert "Постоянные затраты (проезд партии, камеральная группа, трал)" in text
    assert "вероятно, это цены" in text and "делится между объектами экспедиции" in text


def test_archive_disclaimer_names_years_and_not_statistics():
    objects, _source = load_archive()
    text = archive_disclaimer(objects, 2026)
    years = [o.year for o in objects]  # настоящий архив или демонстрационный — число и годы из файла
    assert text.startswith(f"Это не статистика: {len(objects)} объектов разных лет ({min(years)}–{max(years)})")
    assert "цены не приведены к 2026 г." in text


# --- интерфейс: страницы 4 и 5 ---


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    import core.persistence as persistence_module

    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


def _ui_to_minimum():
    from tests.test_streamlit_app import _geology_to_estimate, goto, open_app

    at = open_app("obekt")
    at.date_input(key="calc_date").set_value(datetime.date(2026, 9, 30))
    at.number_input(key="obj_distance_km").set_value(300.0)
    at.run(timeout=30)
    goto(at, "vedomost")
    _geology_to_estimate(at)
    goto(at, "minimum")
    for name in ("brigades", "itr", "drivers", "cooks"):
        at.number_input(key=f"cost_{name}").set_value(PARTY[name])
    at.number_input(key="cost_fuel_price_diesel").set_value(70.0)
    at.run(timeout=30)
    assert at.exception == []
    return at


def _values(elements):
    return [e.value for e in elements]


def test_ui_seed_rates_show_not_calibrated_warning(isolated_smeta_db):
    from tests.test_streamlit_app import goto

    at = _ui_to_minimum()
    # оклады на странице — ввод оператора, нормы — допущения
    for name in ("worker", "itr", "driver", "cook"):
        at.number_input(key=f"cost_salary_{name}").set_value(50000.0)
    at.run(timeout=30)
    warning = "Минимум не калиброван: ставки — допущения / цены, не себестоимость"
    assert any(w.startswith(warning) for w in _values(at.warning))
    assert not any("от бухгалтерии (" in i for i in _values(at.info))
    assert any("ориентир 30–33 %" in c for c in _values(at.caption))
    goto(at, "svodny")
    assert any(w.startswith(warning) for w in _values(at.warning))


def test_ui_accounting_rates_show_other_text(isolated_smeta_db, tmp_path, monkeypatch):
    from tests.test_streamlit_app import goto

    monkeypatch.setattr(core.norms, "LOCAL_DIR", write_local(tmp_path / "local"))
    at = _ui_to_minimum()
    assert at.number_input(key="cost_salary_worker").value == FAKE_SALARIES["salary_worker"]
    assert any(i.startswith("Ставки компании от бухгалтерии") and FAKE_DATE in i for i in _values(at.info))
    assert not any(w.startswith("Минимум не калиброван: ставки") for w in _values(at.warning))
    goto(at, "svodny")
    assert any(i.startswith("Ставки компании от бухгалтерии") for i in _values(at.info))


def test_ui_small_object_shows_impossible_reduction(isolated_smeta_db):
    # ведомость интерфейсных тестов — 450 п.м.; камералка 13 чел. × 2 мес, высокие оклады
    # и дальний трал дают минимум выше максимума
    from tests.test_streamlit_app import goto

    at = _ui_to_minimum()
    for name in ("worker", "itr", "driver", "cook", "office"):
        at.number_input(key=f"cost_salary_{name}").set_value(150000.0)
    at.number_input(key="cost_office_staff").set_value(13)
    at.number_input(key="cost_office_months").set_value(2.0)
    at.run(timeout=30)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Коэффициент снижения"] == REDUCTION_IMPOSSIBLE
    assert any("Больше всего себестоимость тянут" in w and "Постоянные затраты" in w
               for w in _values(at.warning))
    goto(at, "svodny")
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Коэффициент снижения"] == REDUCTION_IMPOSSIBLE
    assert any("Больше всего себестоимость тянут" in w for w in _values(at.warning))
