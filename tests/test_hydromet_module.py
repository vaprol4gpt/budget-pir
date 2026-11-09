"""Поведение модуля гидрометеорологии без эталонных чисел: этапы, пары
полевая → камеральная, параметры сметы без значений по умолчанию,
оргликвидация при расстоянии ≤ 25 км, ограничение категорий II/III."""

import pytest

from core.models import calculate_amount
from core.stages import StagedSession, as_staged
from core.validation import missing_required_fields
from modules.hydromet import module as h
from modules.hydromet.module import HydrometModule


class FakeRefs:
    """Минимальный справочник: цена = 10 для любой позиции, кроме
    ставок; ширины т.48 — две записи."""

    def get_price(self, source, table, item, category=None):
        if (source, table, item) == ("СБЦ-2000", "62", "5"):
            return 0.8, "СБЦ-2000 т.62 §5"
        if (source, table, item) == ("СБЦ-97", "5", "6"):
            return 0.364, "СБЦ-97 т.5 §6"
        if table in ("43", "20", "24", "25", "26", "44") and category and category.split("/")[-1] not in ("II", "III"):
            raise KeyError(category)
        return 10.0, f"{source} т.{table} §{item} {category or ''}".strip()

    def get_categories(self, source, table, item):
        return [("field/св. 20 до 100 м", 130.0), ("field/до 20 м", 78.0), ("kameral/до 20 м", 17.0)]


PARAMETERS = {
    h.P_CATEGORY: "III",
    h.P_FIELD_REGIONAL: 1.85,
    h.P_RIVER_WIDTH: "до 20 м",
    h.P_ICE_NOTE_ITEM: "7",
    h.P_PROGRAM_PRICE: 800,
    h.P_INTERNAL_TRANSPORT_RATE: "0.1875",
    h.P_EXTERNAL_TRANSPORT_RATE: 0.364,
    h.P_DISTANCE_KM: 5000,
}


def estimate(volumes=None, **parameters):
    module = HydrometModule()
    data = {h.VK_RECON_ROUTE: 3, h.VK_FLOW: 2, h.VK_NOTE_ICE: 1, **(volumes or {}),
            **PARAMETERS, **parameters}
    return module.build_estimate(module.build_volumes(data, FakeRefs()), FakeRefs())


def amount(lines, line_id):
    return calculate_amount(lines[line_id], lines)


# --- этапы ---


def test_two_stages_and_field_split():
    staged = as_staged(HydrometModule())
    assert [s.key for s in staged.stages] == ["volumes", "estimate"]
    assert {f.name for f in staged.stage("estimate").fields} == set(h.ESTIMATE_PARAMETER_FIELDS)
    volume_names = {f.name for f in staged.stage("volumes").fields}
    assert volume_names.isdisjoint(h.ESTIMATE_PARAMETER_FIELDS)
    assert h.VK_RECON_ROUTE in volume_names and h.VK_NOTE_ICE in volume_names


def test_volume_sheet_generates_kameral_pairs_program_and_report():
    sheet = HydrometModule().build_volume_sheet({h.VK_RECON_ROUTE: 3, h.VK_FLOW: 2})
    assert sheet["kameral_recon_route"].qty == 3
    assert sheet["kameral_flow"].qty == 2
    assert sheet["kameral_recon_river"].qty == 0
    assert sheet[h.VK_PROGRAM].qty == 1 and sheet[h.VK_REPORT].qty == 1
    assert sheet[h.VK_RECON_ROUTE].source == "input"
    assert sheet["kameral_recon_route"].source == "class_a"


def test_pinned_kameral_pair_wins_over_field_volume():
    sheet = HydrometModule().build_volume_sheet(
        {h.VK_RECON_ROUTE: 3}, pinned={"kameral_recon_route": {"qty": 5}}
    )
    assert sheet["kameral_recon_route"].qty == 5
    assert sheet["kameral_recon_route"].source == "operator"


def test_staged_estimate_uses_confirmed_volumes_and_locks_qty():
    session = StagedSession(as_staged(HydrometModule()))
    session.inputs["volumes"] = {h.VK_RECON_ROUTE: 3}
    session.inputs["estimate"] = dict(PARAMETERS)
    session.confirm("volumes", FakeRefs())
    draft = session.compute("estimate", FakeRefs())
    assert draft.effective["hydromet.kameral.recon_route"].qty == 3
    assert session.editable_fields("estimate", draft.effective["hydromet.recon_route_km"]) == ("price",)
    assert session.editable_fields("estimate", draft.effective["hydromet.kameral.recon_route"]) == ("price",)
    assert session.editable_fields("estimate", draft.effective[h.INTERNAL_TRANSPORT_ID]) == ("rate",)


def test_estimate_stage_requires_parameters_without_defaults():
    staged = as_staged(HydrometModule())
    missing = missing_required_fields(staged.stage("estimate").fields, {})
    assert len(missing) == 5  # категория, районный, ВТ, ВнТ, расстояние
    assert missing_required_fields(staged.stage("estimate").fields, PARAMETERS) == []


# --- пары и коэффициенты ---


def test_kameral_pair_does_not_inherit_field_coefficients():
    lines = estimate()
    assert lines["hydromet.kameral.recon_route"].coefs == {}
    assert lines["hydromet.kameral.recon_route"].qty == lines["hydromet.recon_route_km"].qty


def test_next_watershed_coefficients_are_separate_positions():
    lines = estimate({h.VK_MAX_FLOW_NEXT: 1, h.VK_SPRING_NEXT: 1, h.VK_MIN_FLOW_NEXT: 1})
    assert lines["hydromet.kameral.max_flow_next"].coefs == {"k_prim1": 0.5}
    assert lines["hydromet.kameral.spring_flood_next"].coefs == {"k_prim1": 0.3}
    assert lines["hydromet.kameral.min_flow_next"].coefs == {"k_prim2": 0.5}
    assert amount(lines, "hydromet.kameral.spring_flood_next") == 3.0  # 10 × 0,3


def test_lines_round_like_excel():
    lines = estimate({h.VK_MAX_FLOW_NEXT: 1})
    assert lines["hydromet.kameral.max_flow_next"].rounding == 0
    assert lines[h.REPORT_ID].rounding == 0 and lines[h.PROGRAM_ID].rounding is None


# --- параметры сметы ---


def test_org_liquidation_not_charged_at_25_km_and_shown_explicitly():
    lines = estimate(**{h.P_DISTANCE_KM: 25})
    org = lines[h.ORG_LIQUIDATION_ID]
    assert org.coefs["rate"] == 0
    assert "не начисляется" in org.name
    assert amount(lines, h.ORG_LIQUIDATION_ID) == 0
    assert any("не начисляется" in w for w in HydrometModule().check(lines))


def test_org_liquidation_charged_above_25_km():
    lines = estimate(**{h.P_DISTANCE_KM: 26})
    assert lines[h.ORG_LIQUIDATION_ID].coefs["rate"] == pytest.approx(0.15)
    assert "не начисляется" not in lines[h.ORG_LIQUIDATION_ID].name


def test_internal_transport_rate_has_no_default_and_four_choices():
    options = HydrometModule().field_options(h.P_INTERNAL_TRANSPORT_RATE, FakeRefs())
    assert options[0][0] == "" and [v for v, _ in options[1:]] == ["0.1875", "0.1625", "0.15", "0.1375"]
    lines = estimate(**{h.P_INTERNAL_TRANSPORT_RATE: ""})
    assert amount(lines, h.INTERNAL_TRANSPORT_ID) == 0
    assert any("внутреннего транспорта" in w for w in HydrometModule().check(lines))


def test_external_transport_rate_not_set_warns():
    lines = estimate(**{h.P_EXTERNAL_TRANSPORT_RATE: 0})
    assert amount(lines, h.EXTERNAL_TRANSPORT_ID) == 0
    assert any("внешнего транспорта" in w for w in HydrometModule().check(lines))


def test_program_price_is_operator_input_times_regional():
    lines = estimate(**{h.P_PROGRAM_PRICE: 450})
    assert amount(lines, h.PROGRAM_ID) == 562.5
    assert any("Программа работ" in w for w in HydrometModule().check(lines))
    assert any("не задана" in w for w in HydrometModule().check(estimate(**{h.P_PROGRAM_PRICE: 0})))


def test_only_categories_ii_and_iii_offered_and_limitation_in_check():
    options = HydrometModule().field_options(h.P_CATEGORY, FakeRefs())
    assert [v for v, _ in options] == ["", "II", "III"]
    assert any("только категории сложности II и III" in w for w in HydrometModule().check(estimate()))


def test_category_not_chosen_leaves_dependent_lines_without_price():
    lines = estimate(**{h.P_CATEGORY: ""})
    assert lines["hydromet.recon_route_km"].price == 0
    assert "не выбрана категория" in lines["hydromet.recon_route_km"].basis
    assert any("категория сложности" in w for w in HydrometModule().check(lines))


def test_ice_note_item_not_chosen_leaves_line_without_price():
    lines = estimate(**{h.P_ICE_NOTE_ITEM: ""})
    assert lines["hydromet.kameral.note_ice_count"].price == 0
    assert "т.66" in lines["hydromet.kameral.note_ice_count"].basis


def test_river_width_options_from_reference_grid_sorted_by_price():
    options = HydrometModule().field_options(h.P_RIVER_WIDTH, FakeRefs())
    assert [v for v, _ in options] == ["", "до 20 м", "св. 20 до 100 м"]


def test_external_transport_zero_is_valid_input():
    """Э5а: у карьеров внешний транспорт × 0 — ставку 0 можно ввести
    (поле обязательное, но 0 — значение, а не «пусто»)."""
    fields = as_staged(HydrometModule()).stage("estimate").fields
    assert missing_required_fields(fields, {**PARAMETERS, h.P_EXTERNAL_TRANSPORT_RATE: 0}) == []
    assert missing_required_fields(
        fields, {**PARAMETERS, h.P_EXTERNAL_TRANSPORT_RATE: None}
    ) != []


def test_regional_coefficients_default_to_1_25_and_warn_below_1():
    fields = {f.name: f for f in as_staged(HydrometModule()).stage("estimate").fields}
    assert fields[h.P_KAMERAL_REGIONAL].default == 1.25
    assert fields[h.P_PROGRAM_REGIONAL].default == 1.25
    assert not fields[h.P_KAMERAL_REGIONAL].required
    lines = estimate()
    assert lines[h.KAMERAL_REGIONAL_ID].coefs["rate"] == pytest.approx(0.25)
    assert lines[h.PROGRAM_ID].coefs["k_regional"] == 1.25
    low = estimate(**{h.P_KAMERAL_REGIONAL: 0.9, h.P_PROGRAM_REGIONAL: 0.9})
    warnings = HydrometModule().check(low)
    assert any("к камеральным работам меньше 1" in w for w in warnings)
    assert any("к программе работ меньше 1" in w for w in warnings)


def test_regional_coefficients_below_1_are_rejected_by_validation():
    """Ревью ИГМИ, находка 3: районный к камеральным / программе 0 давал
    нулевую или отрицательную строку. Районный не бывает меньше 1 —
    min_value=1 (форма не даёт ввести меньше, below_min_fields ловит данные
    не из формы), и check() предупреждает."""
    from core.validation import below_min_fields

    fields = as_staged(HydrometModule()).stage("estimate").fields
    for name in (h.P_KAMERAL_REGIONAL, h.P_PROGRAM_REGIONAL):
        for bad in (0, 0.5, -1):
            assert below_min_fields(fields, {**PARAMETERS, name: bad}), (name, bad)
        assert not below_min_fields(fields, {**PARAMETERS, name: 1})
    zero = estimate(**{h.P_KAMERAL_REGIONAL: 0, h.P_PROGRAM_REGIONAL: 0})
    warnings = HydrometModule().check(zero)
    assert any("к камеральным работам меньше 1" in w for w in warnings)
    assert any("к программе работ меньше 1" in w for w in warnings)


def test_report_regional_field_defaults_to_kameral():
    fields = {f.name: f for f in as_staged(HydrometModule()).stage("estimate").fields}
    report = fields[h.P_REPORT_REGIONAL]
    assert not report.required and report.default is None
    assert "как районный к камеральным" in report.label
    # 0,8 × районный × 1,25 прим.6
    assert estimate()[h.REPORT_ID].coefs["rate"] == pytest.approx(0.8 * 1.25 * 1.25)
    assert estimate(**{h.P_KAMERAL_REGIONAL: 1.2})[h.REPORT_ID].coefs["rate"] == pytest.approx(
        0.8 * 1.2 * 1.25
    )
    own = estimate(**{h.P_KAMERAL_REGIONAL: 1.2, h.P_REPORT_REGIONAL: 1.25})
    assert own[h.REPORT_ID].coefs["rate"] == pytest.approx(0.8 * 1.25 * 1.25)
    # Отрицательное — запрещено формой; 0 < к < 1 — предупреждение этапа.
    from core.object_data import CalculationContext, ObjectData
    from core.validation import below_min_fields

    assert below_min_fields(list(fields.values()), {**PARAMETERS, h.P_REPORT_REGIONAL: -1})
    stage = as_staged(HydrometModule()).stage("estimate")
    refs = CalculationContext(FakeRefs(), ObjectData())
    assert any(
        "к техническому отчёту 0.5 меньше 1" in w
        for w in stage.context_warnings({h.P_REPORT_REGIONAL: 0.5}, {}, refs)
    )
    assert stage.context_warnings({h.P_REPORT_REGIONAL: 0}, {}, refs) == []


def test_unfavorable_coefficient_is_estimate_parameter():
    """Ревью ИГМИ, находка 2: 1,4 — поле ввода шаблона (Титул!M18), не норма;
    параметр сметы по умолчанию 1,4, применяется только при отметке объекта."""
    from core.object_data import CalculationContext, ObjectData

    fields = {f.name: f for f in as_staged(HydrometModule()).stage("estimate").fields}
    field = fields[h.P_UNFAVORABLE]
    assert field.default == 1.4 and not field.required
    assert "Титул!M18" in field.label and "нормативный источник не найден" in field.label

    module = HydrometModule()
    on = CalculationContext(FakeRefs(), ObjectData(unfavorable_period=True))
    off = CalculationContext(FakeRefs(), ObjectData())
    route = f"{h.LINE_PREFIX}{h.VK_RECON_ROUTE}"
    # По умолчанию (поле не задано) — «не применять», даже при отметке объекта:
    # в листах компании у ИГМИ коэффициента нет (аудит 05.11.2026, О-5).
    unset = {h.VK_RECON_ROUTE: 3, **PARAMETERS}
    assert module.build_estimate(module.build_volumes(unset, on), on)[route].coefs == {}
    assert h.UNFAVORABLE_MODE_DEFAULT == h.UNFAVORABLE_OFF
    # «по отметке объекта» — прежнее поведение, теперь явным выбором
    data = {**unset, h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_BY_OBJECT}
    default = module.build_estimate(module.build_volumes(data, on), on)
    assert default[route].coefs == {"k_unfavorable": 1.4}
    custom = {**data, h.P_UNFAVORABLE: 1.2}
    lines = module.build_estimate(module.build_volumes(custom, on), on)
    assert lines[route].coefs == {"k_unfavorable": 1.2}
    assert amount(lines, route) == 3 * 10 * 1.2
    assert "× 1.2" in lines[route].basis
    plain = module.build_estimate(module.build_volumes(custom, off), off)
    assert plain[route].coefs == {}

    stage = as_staged(module).stage("estimate")
    by_object = {h.P_UNFAVORABLE: 1.2, h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_BY_OBJECT}
    warnings = stage.context_warnings(by_object, {}, on)
    assert any(
        "Для ИГМИ это означает" in w and "1.2" in w and "«не применять»" in w for w in warnings
    )
    # поле не задано, отметка объекта есть — сказано, что к ИГМИ она не применена
    unset_warnings = stage.context_warnings({}, {}, on)
    assert any("выбрано «не применять» (значение по умолчанию" in w for w in unset_warnings)
    assert not any("Для ИГМИ это означает" in w for w in unset_warnings)
    assert any(
        "не применяется" in w for w in stage.context_warnings({h.P_UNFAVORABLE: 1.2}, {}, off)
    )

    # Выбор этапа сметы: «не применять» при отметке объекта — без коэффициента.
    off_mode = {**data, h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_OFF}
    lines = module.build_estimate(module.build_volumes(off_mode, on), on)
    assert lines[route].coefs == {}
    off_warnings = stage.context_warnings({h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_OFF}, {}, on)
    assert any("выбрано «не применять»" in w for w in off_warnings)
    assert not any("Для ИГМИ это означает" in w for w in off_warnings)
    on_mode = {**data, h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_ON}
    lines = module.build_estimate(module.build_volumes(on_mode, off), off)
    assert lines[route].coefs == {"k_unfavorable": 1.4}
    # значение по умолчанию — первым: форма без сохранённого значения берёт первый вариант
    options = module.field_options(h.P_UNFAVORABLE_MODE, FakeRefs())
    assert [v for v, _ in options] == ["off", "object", "on"]
    assert options[0][1].endswith("(по умолчанию)")


def test_unfavorable_period_warning_on_estimate_stage():
    from core.object_data import CalculationContext, ObjectData

    stage = as_staged(HydrometModule()).stage("estimate")
    refs = CalculationContext(FakeRefs(), ObjectData(unfavorable_period=True))
    by_object = {h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_BY_OBJECT}
    assert any("× 1.4" in w or "на 1.4" in w for w in stage.context_warnings(by_object, {}, refs))
    # по умолчанию отметка объекта к ИГМИ не применяется — и об этом сказано
    assert any("выбрано «не применять»" in w for w in stage.context_warnings({}, {}, refs))
    assert stage.context_warnings({}, {}, CalculationContext(FakeRefs(), ObjectData())) == []
    assert stage.context_warnings(by_object, {}, CalculationContext(FakeRefs(), ObjectData())) == []
