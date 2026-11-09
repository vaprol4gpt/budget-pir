import pytest

from core.models import calculate_amount
from modules.ecology.module import SAMPLE_TYPE_CHOICES, EcologyModule


class FakeReferenceBook:
    """Двойник ReferenceBook для теста стаба без обращения к SQLite."""

    def __init__(self, prices):
        self._prices = prices

    def get_price(self, source, table, item, category=None):
        key = (source, table, item, category)
        if key not in self._prices:
            raise KeyError(key)
        return self._prices[key]


@pytest.fixture
def refs():
    return FakeReferenceBook(
        {
            ("СБЦ-99", "92", "3", "field"): (49.2, "Таблица 92, п.3 (радиационное, полевая)"),
            ("СБЦ-99", "91", "1", "field"): (535.0, "Таблица 91, п.1 (радон, полевая)"),
            ("СБЦ-99", "60", "7", None): (6.9, "Таблица 60, п.7 (почва/грунт хим/ЕРН/фон)"),
            ("СБЦ-99", "60", "6", None): (6.9, "Таблица 60, п.6 (агрохимия)"),
            ("СБЦ-99", "60", "10", None): (37.7, "Таблица 60, §10 (санэпид)"),
            ("СБЦ-99", "60", "1", None): (4.6, "Таблица 60, п.1 (поверхностная вода)"),
            ("СБЦ-99", "60", "2", None): (7.6, "Таблица 60, п.2 (подземная вода)"),
            ("СБЦ-99", "60", "5", None): (6.1, "Таблица 60, п.5 (донные отложения)"),
            ("СБЦ-99", "4", "4", None): (0.1375, "Таблица 4, п.4 (транспорт)"),
        }
    )


# --- build_volumes ---


def test_build_volumes_passes_through_scalar_fields(refs):
    module = EcologyModule()
    volumes = module.build_volumes(
        {
            "radiation_area": 760,
            "radon_points": 200,
            "physical_factors_points": 10,
            "data_acquisition_count": 26,
        },
        refs,
    )
    assert volumes["radiation_area"] == 760
    assert volumes["radon_points"] == 200
    assert volumes["physical_factors_points"] == 10
    assert volumes["data_acquisition_count"] == 26


def test_build_volumes_sums_sampling_rows_by_type(refs):
    module = EcologyModule()
    volumes = module.build_volumes(
        {
            "sampling": [
                {"sample_type": "soil_chemical", "sample_count": 100},
                {"sample_type": "soil_chemical", "sample_count": 60},
                {"sample_type": "surface_water_chemical", "sample_count": 3},
            ]
        },
        refs,
    )
    assert volumes["sampling_soil_chemical"] == 160
    assert volumes["sampling_surface_water_chemical"] == 3


def test_build_volumes_empty_sampling_list_is_safe(refs):
    module = EcologyModule()
    volumes = module.build_volumes({}, refs)
    assert volumes["radiation_area"] == 0
    assert not any(k.startswith("sampling_") for k in volumes)


# --- build_estimate: радиационное/радон (kind A, найденные цены) ---


def test_build_estimate_radiation_uses_real_price(refs):
    module = EcologyModule()
    volumes = module.build_volumes({"radiation_area": 760}, refs)
    estimate = module.build_estimate(volumes, refs)

    radiation = estimate["ecology.radiation"]
    assert radiation.kind == "A"
    assert radiation.unit == "га"
    assert radiation.qty == 760
    assert radiation.price == pytest.approx(49.2)
    assert calculate_amount(radiation, estimate) == pytest.approx(760 * 49.2)


def test_build_estimate_radon_uses_real_price(refs):
    module = EcologyModule()
    volumes = module.build_volumes({"radon_points": 200}, refs)
    estimate = module.build_estimate(volumes, refs)

    radon = estimate["ecology.radon"]
    assert radon.unit == "точек"
    assert radon.qty == 200
    assert radon.price == pytest.approx(535.0)


# --- build_estimate: физфакторы/получение данных — сознательно без цены ---


def test_build_estimate_physical_factors_has_no_price_but_does_not_raise(refs):
    module = EcologyModule()
    volumes = module.build_volumes({"physical_factors_points": 10}, refs)
    estimate = module.build_estimate(volumes, refs)

    physical_factors = estimate["ecology.physical_factors"]
    assert physical_factors.price == 0.0
    assert physical_factors.qty == 10  # объём сохраняется, даже если цена не найдена
    assert "Не найдена как расценка СБЦ-99" in physical_factors.basis


def test_build_estimate_data_acquisition_has_no_price_but_does_not_raise(refs):
    module = EcologyModule()
    volumes = module.build_volumes({"data_acquisition_count": 26}, refs)
    estimate = module.build_estimate(volumes, refs)

    data_acquisition = estimate["ecology.data_acquisition"]
    assert data_acquisition.price == 0.0
    assert data_acquisition.qty == 26
    assert "Не найдена как расценка СБЦ-99" in data_acquisition.basis


# --- build_estimate: опробование (group-поле -> одна строка на вид пробы) ---


def test_build_estimate_creates_one_line_per_sample_type(refs):
    module = EcologyModule()
    volumes = module.build_volumes(
        {
            "sampling": [
                {"sample_type": "soil_chemical", "sample_count": 160},
                {"sample_type": "surface_water_chemical", "sample_count": 3},
            ]
        },
        refs,
    )
    estimate = module.build_estimate(volumes, refs)

    soil_chemical = estimate["ecology.sampling.soil_chemical"]
    assert soil_chemical.unit == "шт"
    assert soil_chemical.qty == 160
    # коэффициент 0.9 вручную сверен (Таблица 60, п.7, прим.1). С Э2
    # (25.10.2026) цена строки — цена справочника 6.9 (как в основании),
    # коэффициент — отдельным множителем в coefs; сумма прежняя.
    assert soil_chemical.price == pytest.approx(6.9)
    assert soil_chemical.coefs == {"k_sample": 0.9}
    assert calculate_amount(soil_chemical, estimate) == pytest.approx(160 * 6.9 * 0.9)

    surface_water = estimate["ecology.sampling.surface_water_chemical"]
    assert surface_water.qty == 3
    assert surface_water.price == pytest.approx(4.6)  # без доп. коэффициента
    assert surface_water.coefs == {}


def test_build_estimate_soil_radionuclides_uses_different_coefficient_same_table_item(refs):
    # ЕРН почв — тот же код справочника (Таблица 60, п.7), что и
    # обычная химия почв, но ДРУГОЙ коэффициент (1.2, не 0.9)
    module = EcologyModule()
    volumes = module.build_volumes(
        {"sampling": [{"sample_type": "soil_radionuclides", "sample_count": 20}]}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    line = estimate["ecology.sampling.soil_radionuclides"]
    assert line.price == pytest.approx(6.9)
    assert line.coefs == {"k_sample": 1.2}
    assert calculate_amount(line, estimate) == pytest.approx(20 * 6.9 * 1.2)


def test_build_estimate_does_not_create_lines_for_sample_types_not_entered(refs):
    module = EcologyModule()
    volumes = module.build_volumes(
        {"sampling": [{"sample_type": "soil_chemical", "sample_count": 1}]}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    assert "ecology.sampling.soil_chemical" in estimate
    assert "ecology.sampling.groundwater_chemical" not in estimate


def test_build_estimate_sample_type_without_known_code_has_no_price(refs):
    # soil_chemical_extended сознательно не входит в SAMPLE_TYPE_PRICE_CODES
    module = EcologyModule()
    volumes = module.build_volumes(
        {"sampling": [{"sample_type": "soil_chemical_extended", "sample_count": 16}]}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    line = estimate["ecology.sampling.soil_chemical_extended"]
    assert line.price == 0.0
    assert line.qty == 16
    assert "Не найдена как расценка СБЦ-99" in line.basis


def test_build_estimate_missing_price_in_reference_book_does_not_raise():
    # реалистичный справочник без табл.60 п.6 вовсе
    refs_without_agrochem = FakeReferenceBook(
        {
            ("СБЦ-99", "92", "3", "field"): (49.2, "..."),
            ("СБЦ-99", "91", "1", "field"): (535.0, "..."),
        }
    )
    module = EcologyModule()
    volumes = module.build_volumes(
        {"sampling": [{"sample_type": "soil_agrochemical", "sample_count": 10}]},
        refs_without_agrochem,
    )
    estimate = module.build_estimate(volumes, refs_without_agrochem)

    line = estimate["ecology.sampling.soil_agrochemical"]
    assert line.price == 0.0
    assert "не найдена в справочнике" in line.basis


# --- build_estimate: внутренний транспорт (kind B) ---


def test_build_estimate_transport_sums_field_lines_not_data_acquisition(refs):
    module = EcologyModule()
    volumes = module.build_volumes(
        {
            "radiation_area": 760,
            "radon_points": 200,
            "physical_factors_points": 10,
            "sampling": [{"sample_type": "soil_chemical", "sample_count": 160}],
            "data_acquisition_count": 26,
        },
        refs,
    )
    estimate = module.build_estimate(volumes, refs)

    transport = estimate["ecology.transport"]
    assert transport.kind == "B"
    assert transport.coefs["rate"] == pytest.approx(0.1375)
    assert "ecology.data_acquisition" not in transport.base_ids
    # База транспорта = все полевые работы, как "Итого полевые работы"
    # эталона (рекогносцировочный блок + радиометрия + отбор проб).
    assert set(transport.base_ids) == {
        "ecology.recon", "ecology.soil_recon", "ecology.route", "ecology.observation_points",
        "ecology.radiation", "ecology.radon", "ecology.physical_factors",
        "ecology.sampling.soil_chemical",
    }
    assert not any(line_id.startswith("ecology.kameral.") for line_id in transport.base_ids)

    field_sum = (
        calculate_amount(estimate["ecology.radiation"], estimate)
        + calculate_amount(estimate["ecology.radon"], estimate)
        + calculate_amount(estimate["ecology.physical_factors"], estimate)
        + calculate_amount(estimate["ecology.sampling.soil_chemical"], estimate)
    )
    expected = field_sum * 0.1375
    assert calculate_amount(transport, estimate) == pytest.approx(expected)


# --- check(): предупреждения ---


def test_check_warns_about_positions_without_known_price_code(refs):
    module = EcologyModule()
    volumes = module.build_volumes(
        {"physical_factors_points": 10, "data_acquisition_count": 26}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)
    not_rated_warnings = [w for w in warnings if "не найдена как расценка" in w]
    assert len(not_rated_warnings) == 2
    joined = " ".join(not_rated_warnings)
    assert "физических факторов" in joined or "ЭМИ" in joined
    assert "исходных данных" in joined


def test_check_warns_about_missing_reference_book_price():
    refs_without_agrochem = FakeReferenceBook({})
    module = EcologyModule()
    volumes = module.build_volumes(
        {"sampling": [{"sample_type": "soil_agrochemical", "sample_count": 10}]},
        refs_without_agrochem,
    )
    estimate = module.build_estimate(volumes, refs_without_agrochem)

    warnings = module.check(estimate)
    assert any("не найдена в справочнике" in w and "агрохимические" in w for w in warnings)


def test_check_always_notes_narrow_scope(refs):
    module = EcologyModule()
    volumes = module.build_volumes({}, refs)
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)
    scope = [w for w in warnings if w.startswith("Упрощение модуля")]
    assert len(scope) == 1
    assert "лабораторных" in scope[0]


def test_check_warns_about_operator_route_kameral_coefficient(refs):
    module = EcologyModule()
    volumes = module.build_volumes({"recon_length_km": 5}, refs)
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)
    assert any("маршрутных наблюдений задан оператором (1)" in w for w in warnings)


def test_check_no_route_coefficient_warning_without_reconnaissance(refs):
    module = EcologyModule()
    estimate = module.build_estimate(module.build_volumes({}, refs), refs)

    assert not any("маршрутных наблюдений" in w for w in module.check(estimate))


def test_check_warns_when_sampling_entered_but_lab_total_missing(refs):
    module = EcologyModule()
    volumes = module.build_volumes(
        {"sampling": [{"sample_type": "soil_chemical", "sample_count": 5}]}, refs
    )
    estimate = module.build_estimate(volumes, refs)

    assert any("Итого лабораторные работы" in w for w in module.check(estimate))


# --- input_schema / field_options ---


def test_input_schema_marks_expected_fields_as_integer():
    module = EcologyModule()
    fields = {field.name: field for field in module.input_schema()}

    assert fields["radiation_area"].integer is False  # площадь — дробная
    assert fields["radon_points"].integer is True
    assert fields["physical_factors_points"].integer is True
    assert fields["data_acquisition_count"].integer is True
    assert fields["sampling"].type == "group"

    sample_count_field = next(f for f in fields["sampling"].group_fields if f.name == "sample_count")
    assert sample_count_field.integer is True


def test_field_options_sample_type_returns_fixed_eleven_types():
    module = EcologyModule()
    refs_unused = FakeReferenceBook({})

    options = module.field_options("sample_type", refs_unused)

    assert options == SAMPLE_TYPE_CHOICES
    assert len(options) == 13  # 11 из ведомости Объект «Тест» + 2 биотестирования эталонов


def test_field_options_returns_empty_list_for_unrelated_field():
    module = EcologyModule()
    assert module.field_options("radiation_area", FakeReferenceBook({})) == []


def test_module_code_is_ecology():
    assert EcologyModule().code == "ecology"


# --- программа работ, полевая рекогносцировка, камеральные пары, техотчёт ---


@pytest.fixture
def full_refs():
    prices = {
        ("СБЦ-99", "81", "1", None): (200.0, "Таблица 81, п.1"),
        ("СБЦ-99", "86", "6", None): (0.2, "Таблица 86, п.6"),
        ("СБЦ-99", "87", "1", None): (0.21, "Таблица 87, п.1"),
        ("СБЦ-99", "92", "3", "field"): (49.2, "Т92 п.3 полевая"),
        ("СБЦ-99", "92", "3", "kameral"): (14.8, "Т92 п.3 камеральная"),
        ("СБЦ-99", "91", "1", "field"): (535.0, "Т91 п.1 полевая"),
        ("СБЦ-99", "91", "1", "kameral"): (161.0, "Т91 п.1 камеральная"),
        ("СБЦ-99", "11", "2", "field"): (11.7, "Т11 п.2 полевая"),
        ("СБЦ-99", "11", "2", "kameral"): (7.5, "Т11 п.2 камеральная"),
        ("СБЦ-99", "4", "4", None): (0.1375, "Т4 п.4"),
    }
    for item, field_price, kameral_price in [
        (("9", "1"), 23.3, 18.5), (("9", "4"), 3.24, 1.69), (("10", "3"), 14.4, 1.8),
        (("9", "2"), 27.0, 18.5), (("9", "5"), 4.33, 1.69), (("10", "2"), 16.6, 1.7),
    ]:
        prices[("СБЦ-99", *item, "field")] = (field_price, f"Т{item[0]} п.{item[1]} полевая")
        prices[("СБЦ-99", *item, "kameral")] = (kameral_price, f"Т{item[0]} п.{item[1]} камеральная")
    return FakeReferenceBook(prices)


def _build(data, refs):
    module = EcologyModule()
    return module.build_estimate(module.build_volumes(data, refs), refs)


def test_program_is_constant_250_regardless_of_input(full_refs):
    for data in ({}, {"recon_length_km": 100, "radiation_area": 500, "lab_total_base": 1e6}):
        program = _build(data, full_refs)["ecology.program"]
        assert program.qty == 1
        assert calculate_amount(program, {}) == pytest.approx(250)


@pytest.mark.parametrize(
    "passability, recon_item, soil_item, route_item",
    [("good", "п.1", "п.4", "п.3"), ("satisfactory", "п.2", "п.5", "п.2")],
)
def test_passability_selects_sbc_items(full_refs, passability, recon_item, soil_item, route_item):
    estimate = _build({"passability": passability, "recon_length_km": 1}, full_refs)
    assert f"Т9 {recon_item}" in estimate["ecology.recon"].basis
    assert f"Т9 {soil_item}" in estimate["ecology.soil_recon"].basis
    assert f"Т10 {route_item}" in estimate["ecology.route"].basis
    assert f"Т9 {recon_item} камеральная" == estimate["ecology.kameral.recon"].basis


def test_missing_passability_defaults_to_good(full_refs):
    estimate = _build({"recon_length_km": 1}, full_refs)
    assert "Т9 п.1" in estimate["ecology.recon"].basis


def test_kameral_pairs_take_field_volumes(full_refs):
    estimate = _build(
        {"recon_length_km": 7, "observation_points": 9, "radiation_area": 3.5, "radon_points": 2},
        full_refs,
    )
    for kameral_id, field_id in [
        ("ecology.kameral.recon", "ecology.recon"),
        ("ecology.kameral.soil_recon", "ecology.soil_recon"),
        ("ecology.kameral.route", "ecology.route"),
        ("ecology.kameral.observation_points", "ecology.observation_points"),
        ("ecology.kameral.radiation", "ecology.radiation"),
        ("ecology.kameral.radon", "ecology.radon"),
    ]:
        assert estimate[kameral_id].qty == estimate[field_id].qty, kameral_id


def test_recon_kameral_coefficient_is_fixed_1_1(full_refs):
    estimate = _build({"recon_length_km": 2}, full_refs)
    assert estimate["ecology.recon"].coefs == {"k_prim1": 1.1}
    assert estimate["ecology.kameral.recon"].coefs == {"k_prim1": 1.1}


def test_route_coefficients_default_to_neutral_and_are_independent(full_refs):
    default = _build({"recon_length_km": 2}, full_refs)
    assert default["ecology.route"].coefs == {"k": 1.0}
    assert default["ecology.kameral.route"].coefs == {"k": 1.0}

    only_field = _build({"recon_length_km": 2, "route_field_coefficient": 1.3}, full_refs)
    assert only_field["ecology.route"].coefs == {"k": 1.3}
    assert only_field["ecology.kameral.route"].coefs == {"k": 1.0}  # не копируется


def test_kameral_radiation_copies_field_coefficients(full_refs):
    estimate = _build({"radiation_area": 10}, full_refs)
    assert estimate["ecology.kameral.radiation"].coefs == estimate["ecology.radiation"].coefs


def test_kameral_lab_is_0_2_of_operator_lab_total(full_refs):
    estimate = _build({"lab_total_base": 13813}, full_refs)
    assert calculate_amount(estimate["ecology.kameral.lab"], estimate) == pytest.approx(2762.6)


def test_radiation_and_radon_stay_outside_lab_base(full_refs):
    estimate = _build({"radiation_area": 10, "radon_points": 3, "lab_total_base": 0}, full_refs)
    assert calculate_amount(estimate["ecology.kameral.lab"], estimate) == 0
    assert estimate["ecology.kameral.lab"].base_ids == []


def test_report_base_is_preliminary_plus_kameral_only(full_refs):
    estimate = _build(
        {"recon_length_km": 4, "observation_points": 5, "radiation_area": 22.5,
         "lab_total_base": 13813, "fund_materials_count": 2.6, "subbase_volume": 7},
        full_refs,
    )
    report = estimate["ecology.report"]
    assert report.kind == "B"
    assert report.coefs["rate"] == pytest.approx(0.21)
    assert set(report.base_ids) == {
        "ecology.program", "ecology.fund_materials", "ecology.subbase",
    } | {line_id for line_id in estimate if line_id.startswith("ecology.kameral.")}
    assert not any(line_id in report.base_ids for line_id in ("ecology.recon", "ecology.transport"))


def test_preliminary_lines_are_outside_transport_base(full_refs):
    estimate = _build({"fund_materials_count": 2.6, "subbase_volume": 7}, full_refs)
    transport_base = estimate["ecology.transport"].base_ids
    for line_id in ("ecology.program", "ecology.fund_materials", "ecology.subbase"):
        assert line_id not in transport_base


@pytest.mark.parametrize("item, price", [("13", 12.29), ("14", 10.02)])
def test_subbase_item_chosen_by_operator(item, price):
    refs = FakeReferenceBook({("СБЦ-99", "80", item, None): (price, f"Т80 п.{item}")})
    estimate = _build({"subbase_item": item, "subbase_volume": 3}, refs)
    subbase = estimate["ecology.subbase"]
    assert subbase.basis == f"Т80 п.{item}"
    assert calculate_amount(subbase, estimate) == pytest.approx(3 * price)


def test_fund_materials_uses_t78_item_2():
    refs = FakeReferenceBook({("СБЦ-99", "78", "2", None): (3.6, "Т78 п.2")})
    estimate = _build({"fund_materials_count": 2.6}, refs)
    assert calculate_amount(estimate["ecology.fund_materials"], estimate) == pytest.approx(9.36)


def test_check_warns_about_operator_subbase_item_only_when_used(full_refs):
    module = EcologyModule()
    used = _build({"subbase_volume": 3}, full_refs)
    unused = _build({}, full_refs)
    assert any("Пункт Т80" in w for w in module.check(used))
    assert not any("Пункт Т80" in w for w in module.check(unused))


def test_field_options_subbase_item_shows_price_from_reference_book():
    refs = FakeReferenceBook({("СБЦ-99", "80", "13", None): (12.29, "Т80 п.13")})
    options = dict(EcologyModule().field_options("subbase_item", refs))
    assert list(options) == ["13", "14"]
    assert "12.29 руб." in options["13"]
    assert "цена не найдена" in options["14"]


def test_new_input_fields_schema():
    module = EcologyModule()
    fields = {field.name: field for field in module.input_schema()}

    assert fields["passability"].dynamic_options is True
    assert fields["observation_points"].integer is True
    assert fields["route_field_coefficient"].default == 1.0
    assert fields["route_kameral_coefficient"].default == 1.0
    assert fields["lab_total_base"].default is None
    for name in ("passability", "recon_length_km", "observation_points",
                 "route_field_coefficient", "route_kameral_coefficient", "lab_total_base"):
        assert fields[name].required is False, name


def test_field_options_passability():
    options = EcologyModule().field_options("passability", FakeReferenceBook({}))
    assert [value for value, _ in options] == ["good", "satisfactory"]


# --- лабораторный блок (Э6г, 30.10.2026) ---

LAB_ROWS = [
    {"analysis_type": "heavy_metals_soil", "analysis_count": 20},
    {"analysis_type": "pah_soil", "analysis_count": 2},
    {"analysis_type": "surface_water", "analysis_count": 1},
    {"analysis_type": "heavy_metals_soil", "analysis_count": 1},  # повтор вида — суммируется
]


@pytest.fixture
def lab_refs(full_refs):
    full_refs._prices.update({
        ("СБЦ-99", "70", "62", None): (51.2, "т. 70 § 62"),
        ("СБЦ-99", "70", "66", None): (95.8, "т. 70 § 66"),
    })
    return full_refs


def test_lab_rows_become_estimate_lines_with_sheet_formulas(lab_refs):
    estimate = _build({"lab_analyses": LAB_ROWS}, lab_refs)
    metals = estimate["ecology.lab.heavy_metals_soil"]
    assert metals.qty == 21
    assert calculate_amount(metals, estimate) == pytest.approx(21 * 51.2 * 7)  # ×7 элементов
    pah = estimate["ecology.lab.pah_soil"]
    assert pah.rounding == 0
    assert calculate_amount(pah, estimate) == 192  # ROUND(2 × 95,8; 0), как в листе
    water = estimate["ecology.lab.surface_water"]
    assert water.price == 218.8 and "№2.5_ИЭИ!E68" in water.basis


def test_kameral_lab_is_share_of_lab_rows(lab_refs):
    estimate = _build({"lab_analyses": LAB_ROWS}, lab_refs)
    kameral = estimate["ecology.kameral.lab"]
    assert kameral.kind == "B"
    assert set(kameral.base_ids) == {"ecology.lab.heavy_metals_soil", "ecology.lab.pah_soil",
                                     "ecology.lab.surface_water"}
    lab = sum(calculate_amount(estimate[i], estimate) for i in kameral.base_ids)
    assert calculate_amount(kameral, estimate) == pytest.approx(0.2 * lab)
    assert not any("Упрощение модуля" in w for w in EcologyModule().check(estimate))


def test_empty_lab_group_keeps_old_sum_path(full_refs):
    """Группа пуста — как до Э6г: строк ecology.lab.* нет, камеральная —
    0,2 × сумма, введённая оператором (старые версии открываются так же)."""
    old = _build({"lab_total_base": 13813}, full_refs)
    empty = _build({"lab_total_base": 13813, "lab_analyses": []}, full_refs)
    for estimate in (old, empty):
        assert not any(i.startswith("ecology.lab.") for i in estimate)
        assert estimate["ecology.kameral.lab"].kind == "A"
        assert calculate_amount(estimate["ecology.kameral.lab"], estimate) == pytest.approx(2762.6)


def test_both_lab_rows_and_sum_rows_win_with_warning(lab_refs):
    estimate = _build({"lab_analyses": LAB_ROWS, "lab_total_base": 99999}, lab_refs)
    kameral = estimate["ecology.kameral.lab"]
    assert kameral.kind == "B"
    lab = sum(calculate_amount(estimate[i], estimate) for i in kameral.base_ids)
    assert calculate_amount(kameral, estimate) == pytest.approx(0.2 * lab)
    assert any("введены дважды" in w for w in EcologyModule().check(estimate))


def test_lab_rows_are_in_volume_sheet_and_report_base(lab_refs):
    module = EcologyModule()
    sheet = module.build_volume_sheet({"lab_analyses": LAB_ROWS})
    assert sheet["lab_analysis_heavy_metals_soil"].qty == 21
    assert "lab_analysis_water_extract" not in sheet
    estimate = _build({"lab_analyses": LAB_ROWS}, lab_refs)
    # техотчёт — от предполевых и камеральных (лабораторные — через 0,2 × итого)
    assert not any(i.startswith("ecology.lab.") for i in estimate["ecology.report"].base_ids)
    # транспорт — только полевые
    assert not any(i.startswith("ecology.lab.") for i in estimate["ecology.transport"].base_ids)


def test_lab_analysis_options_cover_both_etalons():
    options = dict(EcologyModule().field_options("analysis_type", FakeReferenceBook({})))
    assert len(options) == 12
    assert "agrochemical" in options and "groundwater" in options


def test_placeholder_lab_row_with_zero_count_keeps_sum_path(full_refs):
    """Строка-заглушка формы (вид выбран, количество 0) — не «строки анализов»:
    итог суммой по-прежнему работает."""
    estimate = _build({"lab_total_base": 13813,
                       "lab_analyses": [{"analysis_type": "heavy_metals_soil",
                                         "analysis_count": 0}]}, full_refs)
    assert not any(i.startswith("ecology.lab.") for i in estimate)
    assert calculate_amount(estimate["ecology.kameral.lab"], estimate) == pytest.approx(2762.6)
