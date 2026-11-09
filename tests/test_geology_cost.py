"""Себестоимость геологии (вариант Б) и коэффициент снижения (Э6
дорожной карты, 25.10.2026)."""

import dataclasses
from pathlib import Path

import pytest

from core.norms import NormsFormatError, load_norms
from core.persistence import SmetaStore
from core.stages import SOURCE_INPUT, VolumeLine, make_result
from modules.geology.cost import CostInputs, compute_cost, reduction

# Seed-допущения (data/norms), без локального слоя компании этого ПК (С4). Проезд — условное
# число теста (не норма и не ставка): расчёт «вручную» не зависит от значения seed-нормы.
TEST_TRAVEL = 86_420.0


def with_travel(norms, value=TEST_TRAVEL):
    """Нормы с условным проездом на человека (остальное — как есть)."""
    return {**norms, "travel_per_person": dataclasses.replace(norms["travel_per_person"], value=value)}


NORMS = with_travel(load_norms("geology_cost", use_local=False))

FULL = CostInputs(
    brigades=3, itr=2, drivers=2, cooks=1, office_staff=2, office_months=2,
    salary_worker=50000, salary_itr=50000, salary_driver=50000, salary_cook=50000,
    salary_office=40000, mileage_uaz=1000, fuel_price_ai92=60, fuel_price_diesel=70,
    lab_samples=10,
)


def test_norms_have_value_unit_source_and_assumption():
    assert NORMS["drilling_rate_pm_per_brigade_month"].value == 350
    for norm in NORMS.values():
        assert norm.unit and norm.source
        if norm.key != "profit_rate":
            assert norm.assumption and "ДОПУЩЕНИЕ" in norm.source


def test_norms_format_error(tmp_path):
    (tmp_path / "x.json").write_text('{"norms": {"a": {"value": 1}}}', encoding="utf-8")
    with pytest.raises(NormsFormatError, match="нет полей"):
        load_norms("x", tmp_path)


def test_full_calculation_by_hand():
    # 1050 п.м. / (350 × 3) = 1 мес; партия 9 + 2 + 2 + 1 = 14 чел.
    result = compute_cost(FULL, meters=1050, distance_km=100, norms=NORMS)
    assert result.complete
    assert result.field_months == pytest.approx(1.0)
    assert result.field_headcount == 14
    assert result.fot == pytest.approx(14 * 50000 + 2 * 40000 * 2)  # 860 000
    amounts = {line.key: line.amount for line in result.lines}
    assert amounts["social"] == pytest.approx(860000 * 0.302)
    assert amounts["field_allowance_per_person_month"] == pytest.approx(14 * 18000)
    assert amounts["travel"] == pytest.approx(14 * TEST_TRAVEL)
    assert amounts["fuel_ai92"] == pytest.approx(1000 / 100 * 14 * 60)
    assert amounts["fuel_diesel"] == pytest.approx(3 * 1 * 320 * 12 * 70)  # УРБ
    assert amounts["lowboy"] == pytest.approx(2 * 100 * 450 * 3)
    assert amounts["tools"] == pytest.approx(1050 * (588 + 500))
    assert amounts["lab"] == pytest.approx(10 * 5300)
    assert result.direct == pytest.approx(5330800)  # 14 × 86 420 проезд
    assert result.overhead == pytest.approx(860000 * 0.482)
    assert result.taxes == pytest.approx(860000 * 0.0153)
    assert result.contingency == pytest.approx(0.0852 * (5330800 + 414520 + 13158))
    assert result.cost == pytest.approx(6249100.3256)
    assert result.cost_per_meter == pytest.approx(6249100.3256 / 1050)
    assert result.fot_share == pytest.approx(860000 / 6249100.3256)


def test_missing_salaries_and_fuel_prices_make_result_incomplete():
    result = compute_cost(CostInputs(brigades=3, itr=1, mileage_ural=500), meters=1050,
                          distance_km=None, norms=NORMS)
    assert not result.complete
    assert "оклад: буровые рабочие" in result.missing
    assert "оклад: итр" in result.missing
    assert "цена топлива: дизель" in result.missing
    assert any("трала" in m for m in result.missing)
    assert reduction(result, 1e7).coefficient is None


def test_contingency_override_and_zero_brigades():
    result = compute_cost(CostInputs(contingency_rate=0.0), meters=100, norms=NORMS)
    assert "число буровых бригад" in result.missing
    assert result.contingency == 0


def test_depreciation_warning():
    result = compute_cost(FULL, meters=1050, distance_km=100, norms=NORMS)
    assert any("Амортизация и командировочные = 0" in w for w in result.warnings)
    assert any("ДОПУЩЕНИЯ" in w for w in result.warnings)


def test_reduction_three_numbers_and_warning_when_cost_above_max():
    result = compute_cost(FULL, meters=1050, distance_km=100, norms=NORMS)
    ok = reduction(result, 10_000_000)
    assert (ok.maximum, ok.minimum) == (10_000_000, pytest.approx(result.cost))
    assert ok.coefficient == pytest.approx(result.cost / 10_000_000)
    assert ok.warnings == []
    loss = reduction(result, 5_000_000)
    assert loss.coefficient > 1
    assert any("убыточна" in w for w in loss.warnings)
    assert reduction(result, None).coefficient is None


def test_cost_calc_saved_with_version_and_found_through_chain(tmp_path):
    store = SmetaStore(tmp_path / "smety.sqlite")
    result = make_result("volumes", {"w": VolumeLine("w", "w", "шт", 1, SOURCE_INPUT)}, {}, [], {})
    v1 = store.save_version("ГОК", "geology", None, result)
    v2 = store.save_version("ГОК", "geology", v1, result)
    assert store.load_cost_calc_for_chain(v2) is None
    store.save_cost_calc(v1, "geology", FULL.to_dict(), {"cost": 1.0})
    inputs, summary, at_version = store.load_cost_calc_for_chain(v2)
    assert CostInputs.from_dict(inputs) == FULL and summary == {"cost": 1.0} and at_version == v1
    with pytest.raises(ValueError):
        store.save_cost_calc(v1, "ecology", {}, {})


# --- контроль по архиву: коридор по архиву (объекты архива) ---

ETALON_METERS = 1150  # лист «1.2.1_ИГИ», бурение IV–VIII кат.: 300+250+200+200+200


@pytest.mark.skip(
    reason="Окладов и цен топлива в репозитории нет; коридор архива 2016–18 гг. с окладами 2026 г. "
    "не сопоставим (инфляция, состав партии)."
)
# Подробности (30.10.2026): на окладах 2026 г. локального слоя 1 п.м. ≈ 21 тыс. руб. без топлива —
# вне коридора; см. записи разработки, С4. Подгонять тест под ставки не стали.
def test_cost_per_meter_in_archive_corridor_for_object_a_like_input():
    raise AssertionError("нужны оклады и цены топлива")


LOCAL_LAYER = Path(__file__).resolve().parent.parent / "data" / "local" / "norms_firm.json"


@pytest.mark.skipif(not LOCAL_LAYER.exists(), reason="локального слоя ставок компании на этом ПК нет")
def test_object_a_like_input_on_local_2026_rates_is_computed_except_fuel():
    """Пересчёт контроля коридора на ставках 2026 г. (локальный слой): всё,
    кроме топлива и трала (цен нет — В-12), считается; значение 1 п.м.
    записано в записи разработки и с коридором 2016–18 гг. не сравнивается без
    приведения цен (ставки 2026 выше)."""
    from core.norms import local_input_defaults

    local_dir = LOCAL_LAYER.parent
    norms = load_norms("geology_cost", local_dir=local_dir)
    salaries = {k: v.value for k, v in local_input_defaults("geology_cost", local_dir=local_dir).items()}
    inputs = CostInputs(brigades=3, itr=23, office_staff=24, office_months=1, **salaries)
    result = compute_cost(inputs, meters=ETALON_METERS, distance_km=None, norms=norms)
    assert not any(m.startswith("оклад") for m in result.missing)
    assert result.cost_per_meter is not None and result.cost_per_meter > 0


def test_object_a_like_input_without_salaries_is_reported_incomplete():
    # 3 бригады, 32 чел. в поле (9 рабочих + 23 прочих — разбивка по
    # должностям неизвестна, отнесены к ИТР), 24 чел. камералка.
    inputs = CostInputs(brigades=3, itr=23, office_staff=24, office_months=1)
    result = compute_cost(inputs, meters=ETALON_METERS, distance_km=None, norms=NORMS)
    assert result.field_headcount == 32
    assert result.field_months == pytest.approx(1150 / 1050)
    assert not result.complete
    assert {"оклад: буровые рабочие", "оклад: итр", "оклад: камеральная группа"} <= set(result.missing)


# --- вариант А: архив (Э7) ---



# --- архив: настоящий или демонстрационный (публичная копия, демо-сборка) ---

import modules.geology.cost as geology_cost  # noqa: E402


def test_demo_archive_is_marked_and_used_when_real_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(geology_cost, "ARCHIVE_PATH", tmp_path / "archive_unit_costs.json")
    assert geology_cost.archive_path() == geology_cost.DEMO_ARCHIVE_PATH
    objects, source = geology_cost.load_archive()
    assert geology_cost.is_demo_archive(source)
    assert objects and all(o.code.startswith("Демо-") for o in objects)
    assert "Демо-" in geology_cost.archive_summary(objects, 2026)


def test_real_archive_has_priority_over_demo(tmp_path, monkeypatch):
    real = tmp_path / "archive_unit_costs.json"
    real.write_text('{"source": "архив", "objects": [{"code": "А", "years": "2020", "year": 2020, '
                    '"meters": 1, "fact_cost_per_m": 1.0, "norm_cost_per_m": null, "ratio": null, '
                    '"note": null}]}', encoding="utf-8")
    monkeypatch.setattr(geology_cost, "ARCHIVE_PATH", real)
    objects, source = geology_cost.load_archive()
    assert [o.code for o in objects] == ["А"] and not geology_cost.is_demo_archive(source)


def test_no_archive_at_all_is_file_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(geology_cost, "ARCHIVE_PATH", tmp_path / "a.json")
    monkeypatch.setattr(geology_cost, "DEMO_ARCHIVE_PATH", tmp_path / "a.demo.json")
    with pytest.raises(FileNotFoundError):
        geology_cost.load_archive()
