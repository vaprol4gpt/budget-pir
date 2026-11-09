import pytest

from core.models import calculate_amount
from modules.geophysics.module import VEZ_COEFFICIENT, GeophysicsModule


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
            ("СЦиР-1982", "267", "3", None): (9.0, "т.267 п.3 (ВЭЗ)"),
            ("СЦиР-1982", "258", "83", None): (14.0, "т.258 п.83 (МПВ)"),
            ("СЦиР-1982", "291", "1", None): (9.0, "т.291 п.1 (камералка МПВ)"),
            ("СЦиР-1982", "4", "1", None): (0.05, "т.4 п.1 (внутр.транспорт)"),
        }
    )


@pytest.fixture
def refs_missing_transport():
    return FakeReferenceBook(
        {
            ("СЦиР-1982", "267", "3", None): (9.0, "т.267 п.3 (ВЭЗ)"),
            ("СЦиР-1982", "258", "83", None): (14.0, "т.258 п.83 (МПВ)"),
            ("СЦиР-1982", "291", "1", None): (9.0, "т.291 п.1 (камералка МПВ)"),
            # т.4 п.1 намеренно отсутствует
        }
    )


# --- build_volumes: прямой перенос ввода оператора ---


def test_build_volumes_uses_operator_input_directly(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)
    assert volumes["vez"] == 80
    assert volumes["mpv"] == 400


def test_build_volumes_kameralka_mpv_equals_mpv_volume(refs):
    # жёсткая связь: камералка МПВ измеряется тем же "ф.н.", что и
    # полевая строка МПВ, и на листе её объём буквально равен объёму
    # поля (см. записи разработки, исправление про камералку МПВ)
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)
    assert volumes["kameralka_mpv"] == 400
    assert volumes["kameralka_mpv"] == volumes["mpv"]


def test_build_volumes_defaults_to_zero_when_missing(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({}, refs)
    assert volumes["vez"] == 0
    assert volumes["mpv"] == 0
    assert volumes["kameralka_mpv"] == 0


# --- build_estimate: полевые позиции (kind A) ---


def test_build_estimate_vez_uses_real_price_and_coefficient(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)

    estimate = module.build_estimate(volumes, refs)

    vez = estimate["geophysics.vez"]
    assert vez.kind == "A"
    assert vez.qty == 80
    assert vez.price == pytest.approx(9.0)
    assert vez.coefs == {"k_252_6": VEZ_COEFFICIENT}
    assert calculate_amount(vez, estimate) == pytest.approx(80 * 9.0 * 1.2)


def test_build_estimate_mpv_uses_real_price_no_extra_coefficient(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)

    estimate = module.build_estimate(volumes, refs)

    mpv = estimate["geophysics.mpv"]
    assert mpv.kind == "A"
    assert mpv.qty == 400
    assert mpv.price == pytest.approx(14.0)
    assert mpv.coefs == {}
    assert calculate_amount(mpv, estimate) == pytest.approx(400 * 14.0)


# --- build_estimate: камералка МПВ (kind A — qty/price напрямую из листа) ---


def test_build_estimate_kameralka_mpv_is_kind_a_with_own_qty_and_price(refs):
    # исправление: первоначальный план (kind="B", ссылка на mpv по id,
    # ставка = отношение цен) не подтвердился данными листа — у этой
    # строки есть собственные qty/price, как у обычной ценовой позиции
    # (см. записи разработки)
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)

    estimate = module.build_estimate(volumes, refs)

    kameralka = estimate["geophysics.kameralka_mpv"]
    assert kameralka.kind == "A"
    assert kameralka.base_ids == []
    assert kameralka.coefs == {}
    assert kameralka.qty == 400
    assert kameralka.price == pytest.approx(9.0)


def test_build_estimate_kameralka_mpv_amount_is_qty_times_price(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)

    estimate = module.build_estimate(volumes, refs)

    kameralka = estimate["geophysics.kameralka_mpv"]
    assert calculate_amount(kameralka, estimate) == pytest.approx(400 * 9.0)


def test_build_estimate_kameralka_mpv_does_not_depend_on_mpv_line(refs):
    # регрессия на исправление: сумма камералки не должна меняться,
    # если бы (гипотетически) поменялась цена/объём строки МПВ — она
    # больше не строится через base_ids на mpv
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)
    estimate = module.build_estimate(volumes, refs)

    kameralka_amount_before = calculate_amount(estimate["geophysics.kameralka_mpv"], estimate)

    # меняем цену МПВ "на лету" и пересчитываем — камералка не зависит от неё
    estimate["geophysics.mpv"].price = 999.0
    kameralka_amount_after = calculate_amount(estimate["geophysics.kameralka_mpv"], estimate)

    assert kameralka_amount_after == pytest.approx(kameralka_amount_before)


# --- build_estimate: внутренний транспорт (kind B, своя ставка) ---


def test_build_estimate_transport_uses_geophysics_specific_rate(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)

    estimate = module.build_estimate(volumes, refs)

    transport = estimate["geophysics.transport"]
    assert transport.kind == "B"
    assert transport.coefs["rate"] == pytest.approx(0.05)
    # ставка геофизики (5%) отличается от ставки геологии (8.75%) —
    # обе приходят из справочника по своим кодам, не общая константа
    assert transport.coefs["rate"] != pytest.approx(0.0875)
    assert set(transport.base_ids) == {"geophysics.vez", "geophysics.mpv"}


def test_build_estimate_transport_amount_is_rate_times_field_work(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)

    estimate = module.build_estimate(volumes, refs)

    transport = estimate["geophysics.transport"]
    field_work_amount = calculate_amount(estimate["geophysics.vez"], estimate) + \
        calculate_amount(estimate["geophysics.mpv"], estimate)
    expected = field_work_amount * 0.05
    assert calculate_amount(transport, estimate) == pytest.approx(expected)


# --- отсутствующие цены не роняют расчёт ---


def test_build_estimate_missing_price_does_not_raise(refs_missing_transport):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs_missing_transport)

    estimate = module.build_estimate(volumes, refs_missing_transport)

    transport = estimate["geophysics.transport"]
    assert transport.coefs["rate"] == 0.0
    assert "не найдена в справочнике" in transport.basis
    assert calculate_amount(transport, estimate) == 0.0


def test_check_warns_about_missing_price(refs_missing_transport):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs_missing_transport)
    estimate = module.build_estimate(volumes, refs_missing_transport)

    warnings = module.check(estimate)

    assert any("не найдена в справочнике" in w and "транспорт" in w for w in warnings)


def test_check_has_no_missing_price_warning_when_all_prices_found(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)

    assert not any("не найдена в справочнике" in w for w in warnings)


# --- check(): предупреждения о неприменённых коэффициентах (документированные форки) ---


def test_check_warns_about_kameralka_unapplied_coefficient(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)

    assert any("1.15" in w and "Камеральная обработка данных МПВ" in w for w in warnings)


def test_check_warns_about_vez_partial_coefficient(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)
    estimate = module.build_estimate(volumes, refs)

    warnings = module.check(estimate)

    assert any("ВЭЗ" in w and "коэффициент" in w for w in warnings)


# --- input_schema / field_options: контракт BudgetModule соблюдается ---


def test_input_schema_has_required_numeric_fields():
    module = GeophysicsModule()
    fields = {field.name: field for field in module.input_schema()}

    assert fields["vez_count"].required is True
    assert fields["mpv_count"].required is True
    assert fields["vez_count"].dynamic_options is False
    assert fields["mpv_count"].dynamic_options is False


def test_field_options_returns_empty_list_for_any_field(refs):
    module = GeophysicsModule()
    assert module.field_options("vez_count", refs) == []
    assert module.field_options("mpv_count", refs) == []
    assert module.field_options("unrelated", refs) == []


def test_module_code_is_geophysics():
    assert GeophysicsModule().code == "geophysics"


# --- Э6в (30.10.2026): тип волн, новые позиции, параметры сметы ---

from modules.geophysics.module import (  # noqa: E402
    F_MPV_WAVES,
    F_OFFICE_VEZ,
    F_PROGRAM,
    F_REPORT,
    ORG_FIXED_ID,
    ORG_MODE_SHEET,
    P_K_OFFICE_P22,
    P_K_VEZ_265_2,
    P_K_VEZ_265_9,
    P_ORG_MODE,
    P_ORG_SHEET_K,
    WAVES_BOTH,
    WAVES_S,
    YES,
)


def test_waves_both_doubles_mpv_and_its_office_lines():
    sheet = GeophysicsModule().build_volume_sheet({"mpv_count": 400, F_MPV_WAVES: WAVES_BOTH})
    assert sheet["mpv"].qty == sheet["mpv_s"].qty == 400
    assert sheet["kameralka_mpv"].qty == sheet["kameralka_mpv_s"].qty == 400
    assert "продольные" in sheet["mpv"].name and "поперечные" in sheet["mpv_s"].name


def test_waves_default_and_transverse_give_one_mpv_line():
    module = GeophysicsModule()
    assert "mpv_s" not in module.build_volume_sheet({"mpv_count": 400})
    transverse = module.build_volume_sheet({"mpv_count": 400, F_MPV_WAVES: WAVES_S})
    assert "mpv_s" not in transverse and "поперечные" in transverse["mpv"].name


def test_transport_base_includes_both_wave_lines(refs):
    module = GeophysicsModule()
    estimate = module.build_estimate(
        module.build_volumes({"vez_count": 80, "mpv_count": 400, F_MPV_WAVES: WAVES_BOTH}, refs), refs
    )
    assert estimate["geophysics.transport"].base_ids == ["geophysics.vez", "geophysics.mpv", "geophysics.mpv_s"]


def test_new_office_positions_only_when_switched_on(refs):
    module = GeophysicsModule()
    refs._prices[("СЦиР-1982", "294", "3", None)] = (600.0, "т.294 п.3")
    refs._prices[("СЦиР-1982", "294", "10", None)] = (0.1, "т.294 п.10")
    data = {"vez_count": 80, "mpv_count": 400, F_PROGRAM: 1, F_OFFICE_VEZ: YES, F_REPORT: YES}
    estimate = module.build_estimate(module.build_volumes(data, refs), refs, {P_K_OFFICE_P22: 1.15})
    cache = {}
    amount = {i: calculate_amount(line, estimate, cache) for i, line in estimate.items()}
    assert amount["geophysics.program"] == pytest.approx(690)
    assert amount["geophysics.office_vez"] == pytest.approx(864 * 0.3 * 1.15)
    office = amount["geophysics.program"] + amount["geophysics.office_vez"] + amount["geophysics.kameralka_mpv"]
    assert amount["geophysics.report_fixed"] + amount["geophysics.report"] == pytest.approx(1000 + 0.1 * office)


def test_coefficient_params_default_to_one_and_silence_warnings(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)
    estimate = module.build_estimate(volumes, refs, {P_K_VEZ_265_2: 1.15, P_K_VEZ_265_9: 1.1})
    assert estimate["geophysics.vez"].coefs == {"k_252_6": 1.2, "k_265_2": 1.15, "k_265_9": 1.1}
    assert "т.265 п.2" in estimate["geophysics.vez"].basis
    # (предупреждение о МПВ упоминает ВЭЗ для сравнения — проверяем именно ВЭЗ-предупреждение)
    assert not any(w.startswith("«Вертикальное") for w in module.check(estimate))
    # пустое значение (старые сметы) — 1
    assert module.build_estimate(volumes, refs, {P_K_VEZ_265_2: None})["geophysics.vez"].coefs == {"k_252_6": 1.2}


def test_org_liquidation_sheet_formula_without_object_rates(refs):
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)
    estimate = module.estimate_with_accruals(volumes, refs, {P_ORG_MODE: ORG_MODE_SHEET, P_ORG_SHEET_K: 1.2})
    cache = {}
    org = calculate_amount(estimate["geophysics.accrual.org_liquidation"], estimate, cache) + \
        calculate_amount(estimate[ORG_FIXED_ID], estimate, cache)
    f = (864 + 5600) * 1.05
    assert org == pytest.approx(((f * 0.03 + 3000) + (f * 0.00025 + 2500)) * 1.2)


def test_field_options_for_selects(refs):
    module = GeophysicsModule()
    assert [v for v, _ in module.field_options(F_MPV_WAVES, refs)][0] == ""
    assert [v for v, _ in module.field_options(F_REPORT, refs)][0] == ""
    assert [v for v, _ in module.field_options(P_ORG_MODE, refs)][0] == ""


# --- ревью модуля геофизики (30.10.2026) ---

from core.object_data import CalculationContext, ObjectData  # noqa: E402
from core.stages import StagedSession, as_staged  # noqa: E402
from modules.geophysics.module import P_K_MPV_252_6  # noqa: E402


def _staged(refs, volume_inputs, estimate_inputs):
    session = StagedSession(as_staged(GeophysicsModule()))
    session.inputs["volumes"] = volume_inputs
    session.confirm("volumes", refs)
    session.inputs["estimate"] = estimate_inputs
    draft = session.compute("estimate", refs)
    return draft.effective, draft.warnings


def test_mpv_coefficient_difference_from_vez_is_explained_in_warning(refs):
    """№1: К=1,2 «с.252 п.6» у ВЭЗ — всегда, у МПВ — параметр; поведение не
    меняется, но check() честно объясняет почему."""
    module = GeophysicsModule()
    volumes = module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs)
    estimate = module.build_estimate(volumes, refs)
    assert estimate["geophysics.vez"].coefs["k_252_6"] == VEZ_COEFFICIENT
    assert "k_252_6" not in estimate["geophysics.mpv"].coefs
    text = [w for w in module.check(estimate) if "Сейсморазведка МПВ" in w]
    assert len(text) == 1 and "исторически" in text[0]
    # задан — предупреждения нет
    estimate = module.build_estimate(volumes, refs, {P_K_MPV_252_6: 1.2})
    assert not any("Сейсморазведка МПВ" in w for w in module.check(estimate))
    import modules.geophysics.module as geophysics_module
    assert "В-37" in geophysics_module.__doc__


def test_org_sheet_not_charged_without_field_work(refs):
    """№2а: при ВЭЗ = 0 и МПВ = 0 постоянная часть 5 500 × К не начисляется."""
    lines, warnings = _staged(refs, {"vez_count": 0, "mpv_count": 0},
                              {P_ORG_MODE: ORG_MODE_SHEET, P_ORG_SHEET_K: 1.2})
    assert ORG_FIXED_ID not in lines and "geophysics.accrual.org_liquidation" not in lines
    assert any("не начислена" in w and "полевых работ нет" in w for w in warnings)
    # с полевыми — начисляется, как раньше
    lines, warnings = _staged(refs, {"vez_count": 80, "mpv_count": 0},
                              {P_ORG_MODE: ORG_MODE_SHEET, P_ORG_SHEET_K: 1.2})
    assert ORG_FIXED_ID in lines
    assert not any("полевых работ нет" in w for w in warnings)


def test_org_sheet_blank_k_warns_and_uses_one(refs):
    """№2б: пустой К — 1 и предупреждение (в листе 1,2); поле без значения
    по умолчанию, чтобы пустота была видна в форме."""
    field = next(f for f in GeophysicsModule().estimate_schema() if f.name == P_ORG_SHEET_K)
    assert field.default is None
    lines, warnings = _staged(refs, {"vez_count": 80, "mpv_count": 400}, {P_ORG_MODE: ORG_MODE_SHEET})
    assert lines[ORG_FIXED_ID].coefs == {"k": 1.0}
    assert any("К не задан" in w and "1,2" in w for w in warnings)
    _lines, warnings = _staged(refs, {"vez_count": 80, "mpv_count": 400},
                               {P_ORG_MODE: ORG_MODE_SHEET, P_ORG_SHEET_K: 1.2})
    assert not any("К не задан" in w for w in warnings)


def test_org_sheet_warns_that_object_rates_are_ignored(refs):
    """№2в: ставки оргликвидации объекта при режиме «по формуле» не
    используются — предупреждение; в режиме по умолчанию его нет."""
    ctx = CalculationContext(refs, ObjectData(org_liquidation_rate=0.06, org_liquidation_k=2.5))
    params = {P_ORG_MODE: ORG_MODE_SHEET, P_ORG_SHEET_K: 1.2}
    lines, warnings = _staged(ctx, {"vez_count": 80, "mpv_count": 400}, params)
    assert lines[ORG_FIXED_ID].coefs == {"k": 1.2}
    assert any("ставки объекта для этого раздела не используются" in w for w in warnings)
    _lines, warnings = _staged(ctx, {"vez_count": 80, "mpv_count": 400}, {})
    assert not any("не используются" in w for w in warnings)
    _lines, warnings = _staged(refs, {"vez_count": 80, "mpv_count": 400}, params)
    assert not any("не используются" in w for w in warnings)


def test_transverse_waves_label_in_estimate(refs):
    """№3: при «поперечных» строки сметы МПВ и камералки подписаны, как в
    ведомости; при продольных по умолчанию — название прежнее."""
    lines, _w = _staged(refs, {"vez_count": 80, "mpv_count": 400, F_MPV_WAVES: WAVES_S}, {})
    assert lines["geophysics.mpv"].name == "Сейсморазведка МПВ с поверхности (поперечные волны)"
    assert lines["geophysics.kameralka_mpv"].name == "Камеральная обработка данных МПВ (поперечные волны)"
    lines, _w = _staged(refs, {"vez_count": 80, "mpv_count": 400}, {})
    assert lines["geophysics.mpv"].name == "Сейсморазведка МПВ с поверхности"
    assert lines["geophysics.kameralka_mpv"].name == "Камеральная обработка данных МПВ"


@pytest.mark.parametrize("name", [P_K_VEZ_265_2, P_K_MPV_252_6, P_K_OFFICE_P22, P_ORG_SHEET_K])
def test_zero_coefficient_warns(refs, name):
    """№4: коэффициент 0 (или меньше) — предупреждение этапа сметы."""
    params = {name: 0, P_ORG_MODE: ORG_MODE_SHEET}
    _lines, warnings = _staged(refs, {"vez_count": 80, "mpv_count": 400}, params)
    assert any("0 или меньше" in w for w in warnings)
    _lines, warnings = _staged(refs, {"vez_count": 80, "mpv_count": 400}, {name: 1.0})
    assert not any("0 или меньше" in w for w in warnings)


def test_coefficient_labels_say_what_and_when():
    """№5: подпись коэффициента — к чему применяется и условие (или что
    лист его не называет)."""
    labels = {f.name: f.label for f in GeophysicsModule().estimate_schema()}
    for name in (P_K_VEZ_265_2, P_K_VEZ_265_9, P_K_MPV_252_6, P_K_OFFICE_P22):
        assert "умножает" in labels[name]
        assert "услови" in labels[name]
    assert "Крайнему Северу" in labels[P_ORG_SHEET_K] and "умножает" in labels[P_ORG_SHEET_K]
