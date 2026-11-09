import pytest

from core.models import calculate_amount
from modules.geodesy.module import (
    OPORNY_SET_K1,
    OPORNY_SET_K2,
    TOPO_SCALE_CHOICES,
    TRANSPORT_RATE,
    GeodesyModule,
    match_topo_survey_area,
    match_topo_survey_section,
)


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
            ("СБЦи5.6", "8", "3", "1.1"): (6426.0, "Таблица 8 п.3 (опорная сеть)"),
            ("СБЦи5.6", "48", "4", "1.6"): (256.0, "Таблица 48 п.4 (привязка выработок)"),
        }
    )


@pytest.fixture
def refs_missing_privyazka():
    return FakeReferenceBook(
        {
            ("СБЦи5.6", "8", "3", "1.1"): (6426.0, "Таблица 8 п.3 (опорная сеть)"),
            # табл.48 п.4 намеренно отсутствует
        }
    )


# --- build_volumes: прямой перенос ввода оператора ---


def test_build_volumes_uses_operator_input_directly(refs):
    module = GeodesyModule()
    volumes = module.build_volumes(
        {"oporny_points_count": 8, "privyazka_count": 50}, refs
    )
    assert volumes["oporny_points_count"] == 8
    assert volumes["privyazka_count"] == 50


def test_build_volumes_defaults_to_zero_when_missing(refs):
    module = GeodesyModule()
    volumes = module.build_volumes({}, refs)
    assert volumes["oporny_points_count"] == 0
    assert volumes["privyazka_count"] == 0


# --- build_estimate: опорная сеть (kind A, ДВА коэффициента) ---


def test_build_estimate_oporny_set_uses_real_price_and_both_coefficients(refs):
    module = GeodesyModule()
    volumes = module.build_volumes({"oporny_points_count": 8, "privyazka_count": 50}, refs)

    estimate = module.build_estimate(volumes, refs)

    oporny_set = estimate["geodesy.oporny_set"]
    assert oporny_set.kind == "A"
    assert oporny_set.qty == 8
    assert oporny_set.price == pytest.approx(6426.0)
    assert oporny_set.coefs == {"K1": OPORNY_SET_K1, "K2": OPORNY_SET_K2}
    assert calculate_amount(oporny_set, estimate) == pytest.approx(8 * 6426.0 * 0.7 * 1.3)


def test_build_estimate_oporny_set_matches_reference_example():
    # эталонный лист "№1.1_ИГДИ", строка 21: 6426 * 8 * 0.7 * 1.3 = 46781 (округление)
    refs = FakeReferenceBook({("СБЦи5.6", "8", "3", "1.1"): (6426.0, "basis")})
    module = GeodesyModule()
    volumes = module.build_volumes({"oporny_points_count": 8, "privyazka_count": 0}, refs)
    estimate = module.build_estimate(volumes, refs)

    amount = calculate_amount(estimate["geodesy.oporny_set"], estimate)
    assert amount == pytest.approx(46781, abs=1)


# --- build_estimate: привязка выработок (kind A, без коэффициентов) ---


def test_build_estimate_privyazka_uses_real_price_no_coefficient(refs):
    module = GeodesyModule()
    volumes = module.build_volumes({"oporny_points_count": 8, "privyazka_count": 50}, refs)

    estimate = module.build_estimate(volumes, refs)

    privyazka = estimate["geodesy.privyazka"]
    assert privyazka.kind == "A"
    assert privyazka.qty == 50
    assert privyazka.price == pytest.approx(256.0)
    assert privyazka.coefs == {}
    assert calculate_amount(privyazka, estimate) == pytest.approx(50 * 256.0)


def test_build_estimate_privyazka_matches_reference_example():
    # эталонный лист, строка 35: 256 * 50 = 12800
    refs = FakeReferenceBook({("СБЦи5.6", "48", "4", "1.6"): (256.0, "basis")})
    module = GeodesyModule()
    volumes = module.build_volumes({"oporny_points_count": 0, "privyazka_count": 50}, refs)
    estimate = module.build_estimate(volumes, refs)

    amount = calculate_amount(estimate["geodesy.privyazka"], estimate)
    assert amount == pytest.approx(12800.0)


# --- build_estimate: внутренний транспорт (kind B, ставка от суммы ДВУХ базовых строк) ---


def test_build_estimate_transport_is_kind_b_with_hardcoded_rate(refs):
    module = GeodesyModule()
    volumes = module.build_volumes({"oporny_points_count": 8, "privyazka_count": 50}, refs)

    estimate = module.build_estimate(volumes, refs)

    transport = estimate["geodesy.transport"]
    assert transport.kind == "B"
    assert transport.coefs["rate"] == pytest.approx(TRANSPORT_RATE)
    assert transport.coefs["rate"] == pytest.approx(0.0875)
    assert set(transport.base_ids) == {"geodesy.oporny_set", "geodesy.privyazka"}


def test_build_estimate_transport_amount_is_rate_times_sum_of_both_base_lines(refs):
    # новый для этого каркаса случай: kind="B" ссылается на СУММУ ДВУХ
    # базовых строк одновременно, не одной (в отличие от геологии/
    # геофизики, где транспорт ссылался на amount ОДНОЙ базовой строки
    # или суммы всех строк модуля через несколько base_ids, но не
    # специально проверялось именно "две конкретные")
    module = GeodesyModule()
    volumes = module.build_volumes({"oporny_points_count": 8, "privyazka_count": 50}, refs)

    estimate = module.build_estimate(volumes, refs)

    transport = estimate["geodesy.transport"]
    field_work_amount = calculate_amount(estimate["geodesy.oporny_set"], estimate) + calculate_amount(
        estimate["geodesy.privyazka"], estimate
    )
    expected = field_work_amount * TRANSPORT_RATE
    assert calculate_amount(transport, estimate) == pytest.approx(expected)


def test_build_estimate_transport_changes_if_either_base_line_changes(refs):
    # регрессия на "ссылается на сумму нескольких, а не одной": если
    # поменять любую из двух базовых строк, сумма транспорта тоже
    # должна измениться (иначе он ссылается только на одну из них)
    module = GeodesyModule()
    volumes = module.build_volumes({"oporny_points_count": 8, "privyazka_count": 50}, refs)
    estimate = module.build_estimate(volumes, refs)
    before = calculate_amount(estimate["geodesy.transport"], estimate)

    estimate["geodesy.privyazka"].qty = 999.0
    after = calculate_amount(estimate["geodesy.transport"], estimate)

    assert after != pytest.approx(before)


# --- отсутствующие цены не роняют расчёт ---


def test_build_estimate_missing_price_does_not_raise(refs_missing_privyazka):
    module = GeodesyModule()
    volumes = module.build_volumes(
        {"oporny_points_count": 8, "privyazka_count": 50}, refs_missing_privyazka
    )

    estimate = module.build_estimate(volumes, refs_missing_privyazka)

    privyazka = estimate["geodesy.privyazka"]
    assert privyazka.price == 0.0
    assert "не найдена в справочнике" in privyazka.basis
    assert calculate_amount(privyazka, estimate) == 0.0


def test_check_warns_about_missing_price(refs_missing_privyazka):
    module = GeodesyModule()
    volumes = module.build_volumes(
        {"oporny_points_count": 8, "privyazka_count": 50}, refs_missing_privyazka
    )
    estimate = module.build_estimate(volumes, refs_missing_privyazka)

    warnings = module.check(estimate)

    assert any("не найдена в справочнике" in w and "привязка" in w for w in warnings)


def test_check_has_no_missing_price_warning_when_all_prices_found(refs):
    module = GeodesyModule()
    volumes = module.build_volumes({"oporny_points_count": 8, "privyazka_count": 50}, refs)
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)

    assert not any("не найдена в справочнике" in w for w in warnings)


def test_check_always_warns_about_etalon_scope(refs):
    # Э6б: модуль покрывает все позиции листа «№1.1_ИГДИ», но цены — только
    # пунктов и категорий этого листа; об этом — постоянное предупреждение.
    module = GeodesyModule()
    volumes = module.build_volumes({"oporny_points_count": 8, "privyazka_count": 50}, refs)
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)

    assert any("позиции эталонного листа" in w for w in warnings)


# --- топографическая съёмка/план (group-поля, главный драйвер площади) ---


@pytest.fixture
def refs_with_topo():
    return FakeReferenceBook(
        {
            ("СБЦи5.6", "8", "3", "1.1"): (6426.0, "Таблица 8 п.3 (опорная сеть)"),
            ("СБЦи5.6", "48", "4", "1.6"): (256.0, "Таблица 48 п.4 (привязка выработок)"),
            ("СБЦи5.6", "9", "5", "1.5"): (2432.0, "Таблица 9 п.5 (съёмка 1:500)"),
            ("СБЦи5.6", "9", "5", "4.8"): (589.0, "Таблица 9 п.5 (план 1:500)"),
            ("СБЦи5.6", "9", "11", "1.4"): (1430.0, "Таблица 9 п.11 (съёмка 1:1000)"),
            ("СБЦи5.6", "9", "11", "4.7"): (343.0, "Таблица 9 п.11 (план 1:1000)"),
        }
    )


def test_build_volumes_without_topo_rows_defaults_to_empty(refs):
    module = GeodesyModule()
    volumes = module.build_volumes({"oporny_points_count": 8, "privyazka_count": 50}, refs)
    assert volumes["_topo_surveys"] == []
    assert volumes["_topo_plans"] == []


def test_build_estimate_creates_one_line_per_topo_survey_row(refs_with_topo):
    module = GeodesyModule()
    volumes = module.build_volumes(
        {
            "oporny_points_count": 0,
            "privyazka_count": 0,
            "topo_surveys": [
                {"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2},
                {"topo_scale": "1:1000", "topo_section": 0.5, "topo_area": 147},
            ],
        },
        refs_with_topo,
    )
    estimate = module.build_estimate(volumes, refs_with_topo)

    survey_500 = estimate["geodesy.topo_survey.0"]
    assert survey_500.qty == 2
    assert survey_500.price == pytest.approx(2432.0)
    assert survey_500.unit == "га"
    assert calculate_amount(survey_500, estimate) == pytest.approx(2 * 2432.0)

    survey_1000 = estimate["geodesy.topo_survey.1"]
    assert survey_1000.qty == 147
    assert survey_1000.price == pytest.approx(1430.0)


def test_build_estimate_creates_one_line_per_topo_plan_row(refs_with_topo):
    module = GeodesyModule()
    volumes = module.build_volumes(
        {
            "oporny_points_count": 0,
            "privyazka_count": 0,
            "topo_plans": [{"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2}],
        },
        refs_with_topo,
    )
    estimate = module.build_estimate(volumes, refs_with_topo)

    plan_500 = estimate["geodesy.topo_plan.0"]
    assert plan_500.qty == 2
    assert plan_500.price == pytest.approx(589.0)


def test_build_estimate_topo_survey_scale_not_in_reference_sheet_does_not_raise(refs_with_topo):
    # масштаб 1:5000 (и 1:2000, 1:10000) отсутствует в эталонном листе
    # вообще — не пропуск парсера, самой строки в источнике нет
    # (см. docs/known_risks.md) — расчёт не должен упасть.
    module = GeodesyModule()
    volumes = module.build_volumes(
        {
            "oporny_points_count": 0,
            "privyazka_count": 0,
            "topo_surveys": [{"topo_scale": "1:5000", "topo_section": 2.0, "topo_area": 500}],
        },
        refs_with_topo,
    )
    estimate = module.build_estimate(volumes, refs_with_topo)

    survey = estimate["geodesy.topo_survey.0"]
    assert survey.price == 0.0
    assert survey.qty == 500  # объём сохраняется, даже если цена не найдена
    assert "не найдена в справочнике" in survey.basis

    warnings = module.check(estimate)
    assert any("не найдена в справочнике" in w for w in warnings)


def test_build_estimate_transport_base_includes_survey_lines_not_plan_lines(refs_with_topo):
    # съёмка — "полевые работы" (входят в базу транспорта, как опорная
    # сеть/привязка), план — "камеральные" (НЕ входят), см. докстринг
    # modules/geodesy/module.py
    module = GeodesyModule()
    volumes = module.build_volumes(
        {
            "oporny_points_count": 8,
            "privyazka_count": 50,
            "topo_surveys": [{"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2}],
            "topo_plans": [{"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2}],
        },
        refs_with_topo,
    )
    estimate = module.build_estimate(volumes, refs_with_topo)

    transport = estimate["geodesy.transport"]
    assert "geodesy.topo_survey.0" in transport.base_ids
    assert "geodesy.topo_plan.0" not in transport.base_ids


def test_field_options_topo_scale_returns_standard_scale_row(refs):
    module = GeodesyModule()
    options = module.field_options("topo_scale", refs)
    assert options == [(scale, scale) for scale in TOPO_SCALE_CHOICES]
    assert "1:500" in [value for value, _label in options]


# --- предзаполнение площади плана: матчинг по масштабу+сечению, не по порядку ввода ---


def test_match_topo_survey_area_finds_survey_with_same_scale_and_section():
    surveys = [{"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2}]
    assert match_topo_survey_area(surveys, "1:500", 0.5) == 2


def test_match_topo_survey_area_ignores_surveys_added_later_gok_krasny_case():
    # Реальный кейс Объект «Тест»: три съёмки РАЗНЫХ масштабов введены
    # именно в этом порядке — 1:500, 1:1000, 1:5000 (последняя —
    # 1:5000). Площадь плана 1:500 должна подтянуться от ПЕРВОЙ
    # съёмки (совпадение по масштабу+сечению), а НЕ от последней
    # введённой (1:5000) — раньше ошибочно матчилось по порядку ввода.
    surveys = [
        {"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2},
        {"topo_scale": "1:1000", "topo_section": 0.5, "topo_area": 147},
        {"topo_scale": "1:5000", "topo_section": 2.0, "topo_area": 500},
    ]
    assert match_topo_survey_area(surveys, "1:500", 0.5) == 2
    assert match_topo_survey_section(surveys, "1:500") == 0.5


def test_match_topo_survey_area_requires_both_scale_and_section_to_match():
    # Одинаковый масштаб, РАЗНОЕ сечение — не совпадение (заказчик
    # явно просил матчить по масштабу И сечению одновременно)
    surveys = [{"topo_scale": "1:500", "topo_section": 2.0, "topo_area": 999}]
    assert match_topo_survey_area(surveys, "1:500", 0.5) is None


def test_match_topo_survey_area_multiple_matches_uses_last_one():
    # Оператор ввёл одну и ту же комбинацию масштаб+сечение дважды
    # (дубли строк) — берём ПОСЛЕДНЮЮ из совпадающих
    surveys = [
        {"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2},
        {"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 5},
    ]
    assert match_topo_survey_area(surveys, "1:500", 0.5) == 5


def test_match_topo_survey_area_no_matching_survey_returns_none():
    # Масштаб плана не встречается среди введённых съёмок вообще —
    # не гадаем, не берём площадь произвольной другой съёмки
    surveys = [{"topo_scale": "1:1000", "topo_section": 0.5, "topo_area": 147}]
    assert match_topo_survey_area(surveys, "1:500", 0.5) is None


def test_match_topo_survey_area_no_surveys_at_all_returns_none():
    assert match_topo_survey_area([], "1:500", 0.5) is None


def test_match_topo_survey_section_no_matching_scale_returns_none():
    surveys = [{"topo_scale": "1:1000", "topo_section": 0.5, "topo_area": 147}]
    assert match_topo_survey_section(surveys, "1:500") is None


# --- input_schema / field_options: контракт BudgetModule соблюдается ---


def test_input_schema_has_required_numeric_fields():
    module = GeodesyModule()
    fields = {field.name: field for field in module.input_schema()}

    assert fields["oporny_points_count"].required is True
    assert fields["privyazka_count"].required is True
    assert fields["oporny_points_count"].dynamic_options is False
    assert fields["privyazka_count"].dynamic_options is False


def test_field_options_returns_empty_list_for_any_field(refs):
    module = GeodesyModule()
    assert module.field_options("oporny_points_count", refs) == []
    assert module.field_options("privyazka_count", refs) == []
    assert module.field_options("unrelated", refs) == []


def test_module_code_is_geodesy():
    assert GeodesyModule().code == "geodesy"
