"""Минимальный бюджет (себестоимость) всех пяти разделов — 03.11.2026: общий каркас `core/cost.py`, драйверы разделов
`modules/<раздел>/cost.py`, общие ставки один раз, общие затраты объекта
без задвоения, минимум и коэффициент по объекту.

Ставки в тестах — ВЫДУМАННЫЕ круглые числа (не ставки компании, не из исходников
заказчика); локальный слой — во временной папке. Максимумы — итоги
согласованных эталонных листов Автодороги и Карьера (reference_data/, они же в
эталонных тестах разделов)."""

import json

import pytest

import core.norms
from core.cost import (
    ARTICLE_FIXED,
    COMMON_NORMS,
    REDUCTION_IMPOSSIBLE,
    CostLine,
    CostResult,
    SharedCosts,
    coefficient_text,
    compute_party_cost,
    cost_input_defaults,
    cost_structure,
    load_cost_norms,
    object_minimum,
    object_reduction,
    party_rates_status,
    party_size,
    reduction,
    suggested_party,
)
from core.norms import ORIGIN_LOCAL, ORIGIN_SEED, load_norms
from modules.ecology import cost as ecology
from modules.geodesy import cost as geodesy
from modules.geology import cost as geology
from modules.geophysics import cost as geophysics
from modules.hydromet import cost as hydromet

PARTY = {"geodesy": geodesy, "geophysics": geophysics, "ecology": ecology, "hydromet": hydromet}

# Выдуманные «себестоимостные» ставки, руб./мес и руб./л. — нарочно некруглые, чтобы не
# совпасть ни с одной настоящей ставкой (В-77).
FAKE_COMMON = {
    "salary_field_lead": 138_765, "salary_field_engineer": 118_765, "salary_field_driver": 98_765,
    "salary_field_worker": 87_654, "salary_office_chief": 127_654, "salary_office_lead": 117_654,
    "salary_office_engineer": 97_654, "salary_office_technician": 76_543,
    "fuel_price_ai92": 60, "fuel_price_diesel": 70,
}
FAKE_SOURCE = "ТЕСТ: выдуманная ставка, не компании"

# Эталонные объёмы и максимумы (без НДС, с индексом) — из эталонных тестов разделов:
# геодезия — лист «№1.1_ИГДИ» G75 (tests/test_geodesy_etalon.py), съёмка 147 + 2 га;
# геофизика — «№1.2.2_ИГФИ» K34 (test_geophysics_etalon.py), ВЭЗ 80 + МПВ 400;
# экология — Карьер «№2.5_ИЭИ» (test_ecology_etalons.py FULL_CASES), обследование 22,5 га;
# гидрометеорология — Карьер и Автодорога (test_hydromet_etalons.py *_TOTALS).
ETALON = {
    "geodesy": (149, 14_424_312),
    "geophysics": (480, 4_417_072.88),
    "ecology": (22.5, 2_379_806.373395),
    "hydromet": (0, 1_225_808.10),
}


def seed(name):
    return load_cost_norms(name, use_local=False)


def values_for(code, volume, months=1.0, **extra):
    """Общие ставки + состав партии по правилу раздела + камералка 1 мес + УАЗ."""
    module = PARTY[code]
    party = suggested_party(module.SPEC, volume, months, seed(module.NORMS_NAME))
    values = {**FAKE_COMMON, **party, "office_months": 1.0, "mileage_uaz": 2000.0, **extra}
    if module.SPEC.rate_norm_key is None:
        values.setdefault("field_months", months)
    return values


def compute(code, values, volume, shared=None, **extra):
    module = PARTY[code]
    return module.compute_cost(values, volume, seed(module.NORMS_NAME), shared, **extra)


# --- нормы: общие один раз, у раздела — свои; все с источником ----------------------


def test_common_norms_equal_geology_values():
    """Общие нормы = значениям геологии (геология хранит их у себя по истории)."""
    common = load_norms(COMMON_NORMS, use_local=False)
    geo = load_norms("geology_cost", use_local=False)
    for key, norm in common.items():
        assert key in geo, key
        assert norm.value == geo[key].value and norm.unit == geo[key].unit, key


@pytest.mark.parametrize("name", [COMMON_NORMS, "geodesy_cost", "geophysics_cost", "ecology_cost",
                                  "hydromet_cost"])
def test_section_norms_have_source_and_assumption(name):
    for norm in load_norms(name, use_local=False).values():
        assert norm.unit and norm.source, norm.key
        if norm.key != "profit_rate":
            assert norm.assumption, norm.key
        if norm.value is not None and norm.key != "profit_rate":
            assert "ДОПУЩЕНИЕ" in norm.source, norm.key


def test_user_landmarks_checked_against_file_cells():
    """Ориентиры пользователя «ИГДИ 50 га, ИГФИ 100 точек, ИЭИ 120 га на чел.-мес,
    ИГМИ 1 чел.» найдены в v5.2 С2_НОРМЫ D7/D8/D9/D12 — у норм ссылка на ячейку."""
    assert seed("geodesy_cost")["survey_ha_per_person_month"].value == 50
    assert "С2_НОРМЫ!D7" in seed("geodesy_cost")["survey_ha_per_person_month"].source
    assert seed("geophysics_cost")["points_per_person_month"].value == 100
    assert "С2_НОРМЫ!D8" in seed("geophysics_cost")["points_per_person_month"].source
    assert seed("ecology_cost")["eco_ha_per_person_month"].value == 120
    assert "С2_НОРМЫ!D9" in seed("ecology_cost")["eco_ha_per_person_month"].source
    assert seed("hydromet_cost")["field_party"].value == 1
    assert "С2_НОРМЫ!D12" in seed("hydromet_cost")["field_party"].source
    assert seed("hydromet_cost")["production_rate"].value is None  # нормы выработки нет


def _write_local(folder, data):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / core.norms.LOCAL_FILE).write_text(json.dumps({"_формат": 1, **data}, ensure_ascii=False),
                                                encoding="utf-8")
    return folder


def test_common_local_layer_closes_all_sections_section_overrides(tmp_path):
    folder = _write_local(tmp_path / "local", {
        "cost_common": {
            "norms": {"housing_per_person_month": {"value": 11111, "source": FAKE_SOURCE,
                                                   "kind": "accounting"}},
            "inputs": {"salary_field_driver": {"value": 77777, "source": FAKE_SOURCE},
                       "salary_office_engineer": {"value": 66666, "source": FAKE_SOURCE},
                       "fuel_price_diesel": {"value": 55, "source": FAKE_SOURCE}},
        },
        "ecology_cost": {"norms": {"housing_per_person_month": {"value": 22222, "source": FAKE_SOURCE},
                                   "eco_lab_per_sample": {"value": 999, "source": FAKE_SOURCE}}},
    })
    for name in ("geology_cost", "geodesy_cost", "geophysics_cost", "hydromet_cost"):
        housing = load_cost_norms(name, local_dir=folder)["housing_per_person_month"]
        assert (housing.value, housing.origin, housing.kind) == (11111, ORIGIN_LOCAL, "accounting")
    eco = load_cost_norms("ecology_cost", local_dir=folder)
    assert eco["housing_per_person_month"].value == 22222  # раздел перекрывает общее
    assert eco["eco_lab_per_sample"].value == 999
    assert eco["travel_per_person"].origin == ORIGIN_SEED
    # поля ввода: общие должности всем; геологии — водитель и камеральщик через общие
    assert cost_input_defaults("geodesy_cost", local_dir=folder)["salary_field_driver"].value == 77777
    geo = cost_input_defaults("geology_cost", local_dir=folder)
    assert geo["salary_driver"].value == 77777 and geo["salary_office"].value == 66666
    assert geo["fuel_price_diesel"].value == 55


def test_old_rates_in_geology_section_still_serve_other_sections(tmp_path):
    """Файл локального слоя до 03.11.2026: общие ставки лежат в разделе геологии —
    остальные разделы их видят, пока нет записи в "cost_common"."""
    folder = _write_local(tmp_path / "local", {"geology_cost": {
        "norms": {"travel_per_person": {"value": 33333, "source": FAKE_SOURCE}},
        "inputs": {"fuel_price_ai92": {"value": 44, "source": FAKE_SOURCE},
                   "salary_worker": {"value": 1, "source": FAKE_SOURCE}},
    }})
    assert load_cost_norms("hydromet_cost", local_dir=folder)["travel_per_person"].value == 33333
    defaults = cost_input_defaults("geodesy_cost", local_dir=folder)
    assert defaults["fuel_price_ai92"].value == 44
    assert "salary_worker" not in defaults  # особая ставка геологии другим не нужна
    # общий раздел старше прежней записи геологии
    _write_local(folder, {
        "geology_cost": {"norms": {"travel_per_person": {"value": 33333, "source": FAKE_SOURCE}}},
        "cost_common": {"norms": {"travel_per_person": {"value": 12345, "source": FAKE_SOURCE}}},
    })
    assert load_cost_norms("hydromet_cost", local_dir=folder)["travel_per_person"].value == 12345
    assert load_cost_norms("geology_cost", local_dir=folder)["travel_per_person"].value == 33333


# --- геология на общем каркасе: прежние числа ---------------------------------


GEOLOGY_FULL = geology.CostInputs(
    brigades=3, itr=2, drivers=2, cooks=1, office_staff=2, office_months=2,
    salary_worker=50000, salary_itr=50000, salary_driver=50000, salary_cook=50000,
    salary_office=40000, mileage_uaz=1000, fuel_price_ai92=60, fuel_price_diesel=70,
    lab_samples=10,
)


def test_geology_numbers_unchanged_and_shared_is_neutral_by_default():
    from tests.test_geology_cost import with_travel

    norms = with_travel(load_norms("geology_cost", use_local=False))
    base = geology.compute_cost(GEOLOGY_FULL, 1050, 100, norms)
    assert base.cost == pytest.approx(6249100.3256)  # как в test_geology_cost.py
    assert geology.compute_cost(GEOLOGY_FULL, 1050, 100, norms, SharedCosts()).cost == base.cost
    # нормы геологии с общим слоем — те же значения, что seed геологии
    assert geology.compute_cost(GEOLOGY_FULL, 1050, 100, with_travel(seed("geology_cost"))).cost \
        == base.cost
    assert isinstance(base, CostResult) and base.meters == 1050
    assert base.cost_per_meter == pytest.approx(base.cost / 1050)
    kinds = {line.key: line.kind for line in base.lines}
    assert kinds["travel"] == ARTICLE_FIXED and kinds["lowboy"] == ARTICLE_FIXED


# --- драйверы разделов -------------------------------------------------------------


def test_suggested_party_follows_v52_rules():
    """Состав по правилу v5.2 ПР1_ТРУД на объёмах объекта А (ЗАДАНИЕ C14:E19):
    ИГДИ 819 га за 2 мес → 9 чел. (1 + 3 + 1 + 4), камералка 1 + 1 + 5 + 3;
    ИГФИ 300 точек за 1 мес → 3 (1 + 2), камералка 2; ИЭИ 122 га за 1 мес → 2, камералка 2."""
    assert party_size(geodesy.SPEC, 819, 2, seed("geodesy_cost")) == 9
    assert suggested_party(geodesy.SPEC, 819, 2, seed("geodesy_cost")) == {
        "field_lead": 1, "field_engineer": 3, "field_driver": 1, "field_worker": 4,
        "office_chief": 1, "office_lead": 1, "office_engineer": 5, "office_technician": 3}
    assert suggested_party(geophysics.SPEC, 300, 1, seed("geophysics_cost")) == {
        "field_lead": 1, "field_engineer": 2, "office_engineer": 2}
    assert suggested_party(ecology.SPEC, 122, 1, seed("ecology_cost")) == {
        "field_engineer": 2, "office_engineer": 2}
    assert suggested_party(hydromet.SPEC, 0, 1, seed("hydromet_cost")) == {
        "field_lead": 1, "office_lead": 1, "office_engineer": 1}
    # не менее 2 человек в поле при малом объёме
    assert party_size(geodesy.SPEC, 10, 1, seed("geodesy_cost")) == 2


def test_geophysics_full_calculation_by_hand():
    # 300 точек / (100 × 3 чел.) = 1 мес; 3 чел. в поле, 2 инженера × 1,5 мес в камералке
    values = {**FAKE_COMMON, "field_lead": 1, "field_engineer": 2, "office_engineer": 2,
              "office_months": 1.5, "mileage_uaz": 1000}
    r = compute("geophysics", values, 300)
    assert r.complete and r.field_months == pytest.approx(1.0) and r.field_headcount == 3
    fot = 138_765 + 2 * 118_765 + 2 * 97_654 * 1.5
    assert r.fot == pytest.approx(fot)
    a = {line.key: line.amount for line in r.lines}
    assert a["social"] == pytest.approx(fot * 0.302)
    assert a["field_allowance_per_person_month"] == pytest.approx(3 * 18000)
    assert a["housing_per_person_month"] == pytest.approx(3 * 30000)
    travel = seed("geophysics_cost")["travel_per_person"].value  # seed-допущение
    assert a["travel"] == pytest.approx(3 * travel)
    assert a["fuel_ai92"] == pytest.approx(1000 / 100 * 14 * 60)
    direct = fot * 1.302 + 3 * (18000 + 30000 + 500 + 3000) + 3 * travel + 1000 / 100 * 14 * 60
    assert r.direct == pytest.approx(direct)
    assert r.overhead == pytest.approx(fot * 0.482) and r.taxes == pytest.approx(fot * 0.0153)
    assert r.cost == pytest.approx((direct + fot * 0.482 + fot * 0.0153) * 1.0852)
    assert sum(a.amount for a in cost_structure(r)) == pytest.approx(r.cost)


def test_ecology_lab_samples_default_from_sheet_and_info_norm():
    values = values_for("ecology", 22.5)
    r = compute("ecology", values, 22.5, samples=47)
    a = {line.key: line for line in r.lines}
    assert a["lab"].amount == pytest.approx(47 * 5300) and "по ведомости" in a["lab"].basis
    assert a["info"].amount == pytest.approx(100000)
    r2 = compute("ecology", {**values, "lab_samples": 10, "info_services": 0.0}, 22.5, samples=47)
    a2 = {line.key: line.amount for line in r2.lines}
    assert a2["lab"] == pytest.approx(10 * 5300) and a2["info"] == 0


def test_hydromet_without_field_months_is_incomplete_with_reason():
    values = values_for("hydromet", 0)
    values.pop("field_months")
    r = compute("hydromet", values, 0)
    assert not r.complete
    assert any("нормы выработки нет — вводит оператор" in m for m in r.missing)
    assert reduction(r, 1_000_000).coefficient is None
    ok = compute("hydromet", {**values, "field_months": 0.5}, 0)
    assert ok.complete and ok.field_months == 0.5


def test_missing_salary_and_zero_volume_are_reported():
    r = compute("geodesy", {"field_lead": 1, "field_engineer": 1}, 100)
    assert "оклад: ведущий инженер (поле)" in r.missing and not r.complete
    r0 = compute("geodesy", {**FAKE_COMMON, "field_lead": 1}, 0)
    assert any("площадь топографической съёмки" in m for m in r0.missing)
    # срок задан вручную — объём 0 не мешает (линейный объект без площади)
    r1 = compute("ecology", {**FAKE_COMMON, "field_engineer": 2, "field_months": 0.5}, 0)
    assert r1.complete and r1.field_months == 0.5


def test_override_below_norm_warns():
    values = {**FAKE_COMMON, "field_lead": 1, "field_engineer": 1, "field_months": 0.5}
    r = compute("geodesy", values, 500)  # нужно 10 чел.-мес, введено 1
    assert any("меньше нормы выработки" in w for w in r.warnings)


@pytest.mark.parametrize("code", list(ETALON))
def test_minimum_below_maximum_on_etalon_volumes(code):
    """На эталонных объёмах при разумных (выдуманных) ставках минимум < максимума,
    структура по статьям сходится с итогом, коэффициент — число меньше 1."""
    volume, maximum = ETALON[code]
    extra = {"samples": 47} if code == "ecology" else {}
    r = compute(code, values_for(code, volume, months=1.0 if code != "hydromet" else 0.5),
                volume, **extra)
    assert r.complete, r.missing
    red = reduction(r, maximum)
    assert r.cost < maximum and red.possible and red.warnings == []
    assert 0 < red.coefficient < 1
    assert coefficient_text(red) == f"{red.coefficient:.3f}".replace(".", ",")
    structure = cost_structure(r)
    assert sum(a.amount for a in structure) == pytest.approx(r.cost)
    assert sum(a.share for a in structure) == pytest.approx(1.0)


def test_party_minimum_above_maximum_is_explained():
    # малый объект, большая камеральная группа на 2 мес — постоянные затраты не делятся
    values = values_for("geophysics", 50, office_chief=3, office_engineer=10, office_months=2.0)
    r = compute("geophysics", values, 50)
    red = reduction(r, 500_000)
    assert r.cost > 500_000 and not red.possible
    assert coefficient_text(red) == REDUCTION_IMPOSSIBLE
    text = "\n".join(red.warnings)
    assert "снижение невозможно" in text and "Больше всего себестоимость тянут статьи:" in text
    assert "Постоянные затраты (проезд партии, камеральная группа)" in text
    assert "трал" not in text.split("Постоянные затраты")[1].split(".")[0]
    assert "ориентира доли для этого раздела нет" in text


def test_party_rates_status_from_accounting_only_for_present_roles(tmp_path):
    entry = {"source": FAKE_SOURCE, "kind": "accounting", "date": "2026-11-01"}
    folder = _write_local(tmp_path / "local", {"cost_common": {
        "inputs": {"salary_field_engineer": {"value": 120000, **entry}},
        "norms": {k: {"value": v, **entry} for k, v in (
            ("field_allowance_per_person_month", 15000), ("housing_per_person_month", 25000),
            ("travel_per_person", 40000))},
    }})
    norms = load_cost_norms("ecology_cost", local_dir=folder)
    local = cost_input_defaults("ecology_cost", local_dir=folder)
    values = {"field_engineer": 2, "salary_field_engineer": 120000}
    status = party_rates_status(ecology.SPEC, values, norms, local)
    assert status.from_accounting and status.text().startswith("Ставки компании от бухгалтерии")
    # оклад введён на странице иначе — уже не «от бухгалтерии»
    changed = party_rates_status(ecology.SPEC, {**values, "salary_field_engineer": 1.0}, norms, local)
    assert not changed.from_accounting and "ввод на странице" in changed.text()


# --- общие затраты и минимум объекта -------------------------------------------


def test_shared_mobilization_counted_once_in_object():
    shared = SharedCosts(mobilization=300000, delivery=200000)
    gd = compute("geodesy", values_for("geodesy", 149), 149, shared)
    gf = compute("geophysics", values_for("geophysics", 480), 480, shared)
    plain_gd = compute("geodesy", values_for("geodesy", 149), 149)
    plain_gf = compute("geophysics", values_for("geophysics", 480), 480)
    # в минимуме каждого раздела — целиком (с резервом на них)
    assert gd.cost == pytest.approx(plain_gd.cost + 500000 * (1 + gd.contingency_rate))
    om = object_minimum({"geodesy": gd, "geophysics": gf})
    # в минимуме объекта — один раз
    assert om.total == pytest.approx(plain_gd.cost + plain_gf.cost + 500000 * (1 + gd.contingency_rate))
    assert {s.kind: s.amount for s in om.shared} == {"mobilization": 300000, "delivery": 200000}
    assert om.duplicates_excluded == pytest.approx(500000 * (1 + gf.contingency_rate))
    assert "один раз" in om.text()


def test_shared_base_kept_by_longest_field_period():
    shared = SharedCosts(base_per_month=100000)
    short = compute("ecology", {**FAKE_COMMON, "field_engineer": 2, "field_months": 0.5}, 0, shared)
    long = compute("hydromet", {**FAKE_COMMON, "field_lead": 1, "field_months": 2.0}, 0, shared)
    om = object_minimum({"ecology": short, "hydromet": long}, {"ecology": "ИЭИ", "hydromet": "ИГМИ"})
    base = next(s for s in om.shared if s.kind == "base")
    assert base.amount == pytest.approx(200000) and base.section == "ИГМИ"
    assert om.total == pytest.approx(short.cost + long.cost - 50000 * (1 + short.contingency_rate))


def test_geology_takes_part_in_shared_costs():
    shared = SharedCosts(mobilization=250000)
    norms = load_norms("geology_cost", use_local=False)
    geo = geology.compute_cost(GEOLOGY_FULL, 1050, 100, norms, shared)
    plain = geology.compute_cost(GEOLOGY_FULL, 1050, 100, norms)
    assert geo.cost == pytest.approx(plain.cost + 250000 * 1.0852)
    gd = compute("geodesy", values_for("geodesy", 149), 149, shared)
    om = object_minimum({"geology": geo, "geodesy": gd})
    assert om.total == pytest.approx(geo.cost + gd.cost - 250000 * (1 + gd.contingency_rate))


def test_object_minimum_skips_incomplete_and_reduction_by_object():
    gd = compute("geodesy", values_for("geodesy", 149), 149)
    bad = compute("hydromet", {"field_lead": 1}, 0)  # нет срока и оклада
    om = object_minimum({"geodesy": gd, "hydromet": bad}, {"geodesy": "ИГДИ", "hydromet": "ИГМИ"})
    assert om.total == pytest.approx(gd.cost) and not om.complete
    assert [label for label, _missing in om.incomplete] == ["ИГМИ"]
    assert not om.from_accounting and om.calibrated is False
    red = object_reduction(om, {"geodesy": 14_424_312, "hydromet": None})
    assert red.coefficient == pytest.approx(gd.cost / 14_424_312)
    assert any("не вошли разделы с неполным расчётом" in w for w in red.warnings)
    assert object_reduction(om, {"geodesy": None}).coefficient is None
    empty = object_minimum({})
    assert empty.total is None and object_reduction(empty, {}).coefficient is None


def test_object_reduction_above_one_is_impossible():
    r = compute("geophysics", values_for("geophysics", 480), 480)
    red = object_reduction(object_minimum({"geophysics": r}), {"geophysics": r.cost / 2})
    assert red.coefficient == pytest.approx(2) and not red.possible
    assert any("снижение невозможно" in w for w in red.warnings)


def test_shared_costs_dict_roundtrip():
    shared = SharedCosts.from_dict({"mobilization": 1, "delivery": None, "base_per_month": "2", "x": 5})
    assert shared == SharedCosts(1.0, 0.0, 2.0) and not shared.empty
    assert SharedCosts.from_dict(shared.to_dict()) == shared and SharedCosts().empty


def test_generic_line_flags():
    line = CostLine("x", "X", 1.0, "b")
    assert line.direct and not line.fot and not line.mobilization and line.shared == ""


def test_compute_party_cost_direct_call_matches_module_wrapper():
    values = values_for("geodesy", 149)
    norms = seed("geodesy_cost")
    assert compute_party_cost(geodesy.SPEC, values, 149, norms).cost == \
        geodesy.compute_cost(values, 149, norms).cost


def test_volume_from_sheet():
    from core.stages import SOURCE_INPUT, VolumeLine

    def line(key, qty):
        return VolumeLine(key, key, "шт", qty, SOURCE_INPUT)

    assert geodesy.volume_from_sheet({"topo_survey.0": line("topo_survey.0", 147),
                                      "topo_survey.1": line("topo_survey.1", 2),
                                      "topo_plan.0": line("topo_plan.0", 147)}) == 149
    assert geophysics.volume_from_sheet({"vez": line("vez", 80), "mpv": line("mpv", 400),
                                         "kameralka_mpv": line("kameralka_mpv", 400)}) == 480
    sheet = {"radiation_area": line("radiation_area", 22.5), "sampling_soil_chemical":
             line("sampling_soil_chemical", 35), "sampling_x": line("sampling_x", 12)}
    assert ecology.volume_from_sheet(sheet) == 22.5 and ecology.samples_from_sheet(sheet) == 47
    assert hydromet.volume_from_sheet({}) == 0


# --- находки ревью трека 3 ---------------------------------------------------


def test_geology_default_norms_include_common_layer(tmp_path, monkeypatch):
    """Без явных норм геология берёт нормы с общим слоем: общая ставка из
    "cost_common" (после импорта шаблона v2) не теряется."""
    folder = _write_local(tmp_path / "local", {"cost_common": {"norms": {
        "housing_per_person_month": {"value": 1.0, "source": FAKE_SOURCE}}}})
    monkeypatch.setattr(core.norms, "LOCAL_DIR", folder)
    with_layer = geology.compute_cost(GEOLOGY_FULL, 1050, 100)
    housing = {line.key: line.amount for line in with_layer.lines}["housing_per_person_month"]
    assert housing == pytest.approx(14 * 1.0)


def test_ecology_fractional_samples_round_up():
    values = values_for("ecology", 22.5, lab_samples=10.2)
    lab = {line.key: line.amount for line in compute("ecology", values, 22.5).lines}["lab"]
    assert lab == pytest.approx(11 * 5300)
    exact = {line.key: line.amount for line in
             compute("ecology", {**values, "lab_samples": 10.0}, 22.5).lines}["lab"]
    assert exact == pytest.approx(10 * 5300)


def test_party_months_text_shows_actual_term_after_fill():
    """ИГДИ: подбор на 1 мес для 60 га даёт 2 человек, правило должностей — 3
    (ведущий, инженер, водитель) → срок по норме 0,40 мес, а не 1."""
    from app.ui_minimum import party_months_text

    norms = seed("geodesy_cost")
    party = suggested_party(geodesy.SPEC, 60, 1.0, norms)
    assert sum(party[k] for k in ("field_lead", "field_engineer", "field_driver", "field_worker")) == 3
    text = party_months_text(geodesy.SPEC, 60, party, norms, None)
    assert "3 чел. в поле" in text and "0,40" not in text and "0.40 мес" in text
    r = compute("geodesy", {**FAKE_COMMON, **party}, 60)
    assert r.field_months == pytest.approx(0.4)
    assert party_months_text(geodesy.SPEC, 60, party, norms, 2.0) == "Срок поля — введённый: 2 мес."
