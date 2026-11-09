"""Сверка модуля гидрометеорологии с двумя эталонными сметами шаблона
компании — «№2.4_ИГМИ» (Карьер) и «№1.3_ИГМИ» (Автодорога) — на справочнике,
собранном из тех же xlsx настоящим парсером (data/build_references.py).
Цепочка парсер → модуль целиком, до копейки. Разбор листов и формулы —
docs/hydromet_diagnostics.md.

Параметры сметы заданы как на эталонах: категория III, районный к
полевым 1,85, ширина реки «до 20 м», программа 800 / 3500, внутренний
транспорт 18,75 % / 15 %, внешний 0,364, расстояние 5000 км (> 25 —
оргликвидация начисляется), записка о ледовом режиме т.66 §7 / §8."""

import openpyxl
import pytest

from core.models import calculate_amount, excel_round
from core.pricing import calculate_total_with_vat
from core.references import ReferenceBook
from core.stages import StagedSession, as_staged
from data.build_references import (
    ECOLOGY_SOURCE_XLSX,
    HYDROMET_KARIER_SHEET_NAME,
    HYDROMET_ROAD_SHEET_NAME,
    SOURCE_XLSX,
    IndexEntry,
    build_database,
    extract_hydromet_price_entries,
    hydromet_category_ii_supplement,
)
from modules.hydromet import module as h
from modules.hydromet.module import HydrometModule

KARIER_VOLUMES = {
    h.VK_RECON_ROUTE: 3, h.VK_RECON_RIVER: 1, h.VK_MORPHOMETRIC: 1, h.VK_MORPHO_STVOR: 1,
    h.VK_GVV: 2, h.VK_SLOPE: 2, h.VK_SOUNDING_STVOR: 2, h.VK_FLOW: 2, h.VK_DEPTH: 2,
    h.VK_PHOTO: 10,
    h.VK_STUDY_TABLE: 1, h.VK_STUDY_SCHEME: 1, h.VK_FLOW_GRAPHS: 2, h.VK_RATING_CURVE: 2,
    h.VK_BASIN_AREA: 2, h.VK_BASIN_SLOPE: 2,
    h.VK_MAX_FLOW_FIRST: 1, h.VK_MAX_FLOW_NEXT: 1, h.VK_SPRING_FIRST: 1, h.VK_SPRING_NEXT: 1,
    h.VK_MIN_FLOW_FIRST: 1, h.VK_MIN_FLOW_NEXT: 1,
    h.VK_NOTE_CHANNEL: 1, h.VK_NOTE_ICE: 1, h.VK_NOTE_CLIMATE: 1, h.VK_STATION_SELECTION: 1,
}
ROAD_VOLUMES = {
    h.VK_RECON_ROUTE: 15, h.VK_RECON_RIVER: 6, h.VK_MORPHOMETRIC: 6, h.VK_MORPHO_STVOR: 6,
    h.VK_GVV: 12, h.VK_SLOPE: 6, h.VK_SOUNDING_STVOR: 144, h.VK_FLOW: 12, h.VK_DEPTH: 144,
    h.VK_PHOTO: 60,
    h.VK_STUDY_TABLE: 1, h.VK_STUDY_SCHEME: 1, h.VK_AUX_TABLE: 6, h.VK_FLOW_GRAPHS: 12,
    h.VK_RATING_CURVE: 12, h.VK_BASIN_AREA: 15, h.VK_BASIN_SLOPE: 15,
    h.VK_MAX_FLOW_FIRST: 1, h.VK_MAX_FLOW_NEXT: 14, h.VK_SPRING_FIRST: 1, h.VK_SPRING_NEXT: 14,
    h.VK_MIN_FLOW_FIRST: 1, h.VK_MIN_FLOW_NEXT: 14, h.VK_RUNOFF_PARAMS: 2, h.VK_ANALOG: 1,
    h.VK_CHANNEL_SHIFT: 1, h.VK_VERTICAL_DEFORMATION: 12,
    h.VK_NOTE_CHANNEL: 1, h.VK_NOTE_ICE: 1, h.VK_NOTE_CLIMATE: 1, h.VK_STATION_SELECTION: 1,
}
COMMON_PARAMETERS = {
    h.P_CATEGORY: "III",
    h.P_FIELD_REGIONAL: 1.85,
    h.P_RIVER_WIDTH: "до 20 м",
    h.P_EXTERNAL_TRANSPORT_RATE: 0.364,
    h.P_DISTANCE_KM: 5000,
}
KARIER_PARAMETERS = {
    **COMMON_PARAMETERS, h.P_PROGRAM_PRICE: 800, h.P_INTERNAL_TRANSPORT_RATE: "0.1875",
    h.P_ICE_NOTE_ITEM: "7",
}
ROAD_PARAMETERS = {
    **COMMON_PARAMETERS, h.P_PROGRAM_PRICE: 3500, h.P_INTERNAL_TRANSPORT_RATE: "0.15",
    h.P_ICE_NOTE_ITEM: "8",
}

# Суммы строк на листах эталонов (кэш ячеек F), по id строки модуля.
KARIER_LINES = {
    "recon_route_km": 72, "recon_river_km": 42, "morphometric_count": 354,
    "morpho_stvor_km": 264, "gvv_count": 284, "slope_levelling_km": 472,
    "sounding_stvor_count": 76, "flow_measurements": 156, "depth_soundings": 38,
    "photo_count": 70,
    "kameral.recon_route": 24, "kameral.recon_river": 14, "kameral.morpho_stvor": 21,
    "kameral.flow": 34, "kameral.study_table_count": 105, "kameral.study_scheme_count": 61,
    "kameral.flow_graphs_count": 230, "kameral.rating_curve_count": 136,
    "kameral.basin_area_count": 12, "kameral.basin_slope_count": 38,
    "kameral.max_flow_first": 77, "kameral.max_flow_next": 39,
    "kameral.spring_flood_first": 34, "kameral.spring_flood_next": 10,
    "kameral.min_flow_first": 26, "kameral.min_flow_next": 13,
    "kameral.note_channel_count": 393, "kameral.note_ice_count": 1348,
    "kameral.note_climate_count": 446, "kameral.station_selection_count": 90,
}
ROAD_LINES = {
    "recon_route_km": 360, "recon_river_km": 252, "morphometric_count": 2124,
    "morpho_stvor_km": 1584, "gvv_count": 1704, "slope_levelling_km": 1416,
    "sounding_stvor_count": 5472, "flow_measurements": 936, "depth_soundings": 2736,
    "photo_count": 420,
    "kameral.recon_route": 120, "kameral.recon_river": 84, "kameral.morpho_stvor": 126,
    "kameral.flow": 204, "kameral.study_table_count": 105, "kameral.study_scheme_count": 61,
    "kameral.aux_table_count": 648, "kameral.flow_graphs_count": 1380,
    "kameral.rating_curve_count": 816, "kameral.basin_area_count": 90,
    "kameral.basin_slope_count": 285, "kameral.max_flow_first": 77,
    "kameral.max_flow_next": 539, "kameral.spring_flood_first": 34,
    "kameral.spring_flood_next": 143, "kameral.min_flow_first": 26,
    "kameral.min_flow_next": 182, "kameral.runoff_params_count": 90,
    "kameral.analog_count": 217, "kameral.channel_shift_count": 447,
    "kameral.vertical_deformation_count": 5967, "kameral.note_channel_count": 393,
    "kameral.note_ice_count": 1800, "kameral.note_climate_count": 446,
    "kameral.station_selection_count": 90,
}
# Итоги листов: полевые (с районным), камеральные (с районным), ВТ, ВнТ,
# оргликвидация, программа, техотчёт, ИТОГО, × индекс 81,95.
KARIER_TOTALS = (3382, 3939, 634, 1462, 602, 1000, 3939, 14958, 1_225_808.10)
ROAD_TOTALS = (31457, 17963, 4719, 13168, 5426, 4375, 17963, 95071, 7_791_068.45)
# Автодорога в сводную уходит × 0,65 (понижающий) — в модуле сознательно
# не реализован, проверяется только отдельной арифметикой ниже.
ROAD_REDUCED = 5_064_194.49


@pytest.fixture(scope="module")
def real_refs(tmp_path_factory):
    entries = []
    for path, sheet in ((ECOLOGY_SOURCE_XLSX, HYDROMET_KARIER_SHEET_NAME),
                        (SOURCE_XLSX, HYDROMET_ROAD_SHEET_NAME)):
        sheet_entries, warnings = extract_hydromet_price_entries(
            openpyxl.load_workbook(path), openpyxl.load_workbook(path, data_only=True), sheet
        )
        assert warnings == []
        entries += sheet_entries
    index = [IndexEntry("hydromet", "2026-07-01", "2026-09-30", 81.95, "эталон")]
    db_path = tmp_path_factory.mktemp("refs") / "references.sqlite"
    assert build_database(entries + hydromet_category_ii_supplement(), index, db_path) == []
    return ReferenceBook(db_path)


def _direct(volumes, parameters, refs):
    module = HydrometModule()
    return module.build_estimate(module.build_volumes({**volumes, **parameters}, refs), refs)


def _staged(volumes, parameters, refs):
    session = StagedSession(as_staged(HydrometModule()))
    session.inputs["volumes"] = dict(volumes)
    session.inputs["estimate"] = dict(parameters)
    session.confirm("volumes", refs)
    return dict(session.confirm("estimate", refs).lines)


def _sum(estimate, ids):
    cache: dict[str, float] = {}
    return sum(calculate_amount(estimate[i], estimate, cache) for i in ids)


CASES = [
    pytest.param(KARIER_VOLUMES, KARIER_PARAMETERS, KARIER_LINES, KARIER_TOTALS, id="karier"),
    pytest.param(ROAD_VOLUMES, ROAD_PARAMETERS, ROAD_LINES, ROAD_TOTALS, id="road"),
]


@pytest.mark.parametrize("volumes, parameters, expected_lines, totals", CASES)
@pytest.mark.parametrize("calculate", [_direct, _staged], ids=["direct", "staged"])
def test_every_line_matches_etalon(real_refs, calculate, volumes, parameters, expected_lines, totals):
    estimate = calculate(volumes, parameters, real_refs)
    for key, amount in expected_lines.items():
        line_id = f"hydromet.{key}"
        assert _sum(estimate, [line_id]) == pytest.approx(amount, abs=0.005), line_id


@pytest.mark.parametrize("volumes, parameters, expected_lines, totals", CASES)
@pytest.mark.parametrize("calculate", [_direct, _staged], ids=["direct", "staged"])
def test_section_totals_match_etalon(real_refs, calculate, volumes, parameters, expected_lines, totals):
    field_total, kameral_total, internal, external, org, program, report, total, indexed = totals
    estimate = calculate(volumes, parameters, real_refs)

    field_ids = [f"hydromet.{key}" for key in h.FIELD_POSITIONS] + [h.FIELD_REGIONAL_ID]
    kameral_ids = [i for i in estimate if i.startswith(h.KAMERAL_LINE_PREFIX)] + [h.KAMERAL_REGIONAL_ID]
    assert _sum(estimate, field_ids) == pytest.approx(field_total, abs=0.005)
    assert _sum(estimate, kameral_ids) == pytest.approx(kameral_total, abs=0.005)
    assert _sum(estimate, [h.INTERNAL_TRANSPORT_ID]) == pytest.approx(internal, abs=0.005)
    assert _sum(estimate, [h.EXTERNAL_TRANSPORT_ID]) == pytest.approx(external, abs=0.005)
    assert _sum(estimate, [h.ORG_LIQUIDATION_ID]) == pytest.approx(org, abs=0.005)
    assert _sum(estimate, [h.PROGRAM_ID]) == pytest.approx(program, abs=0.005)
    assert _sum(estimate, [h.REPORT_ID]) == pytest.approx(report, abs=0.005)

    totals_with_vat = calculate_total_with_vat(estimate, real_refs, "hydromet", "2026-09-24")
    assert totals_with_vat.base_total == pytest.approx(total, abs=0.005)
    assert totals_with_vat.indexed_total == pytest.approx(indexed, abs=0.005)


def test_road_reduction_is_etalon_arithmetic_not_module(real_refs):
    """К=0,65 у Автодороги — вне модуля (конкурсное снижение отложено):
    проверяем только, что эталонная сумма = индексированный итог модуля ×
    0,65, то есть модуль отдаёт ровно ту базу, к которой его применят."""
    estimate = _direct(ROAD_VOLUMES, ROAD_PARAMETERS, real_refs)
    totals = calculate_total_with_vat(estimate, real_refs, "hydromet", "2026-09-24")
    assert totals.indexed_total * 0.65 == pytest.approx(ROAD_REDUCED, abs=0.005)


def test_all_etalon_lines_get_real_price(real_refs):
    for volumes, parameters in ((KARIER_VOLUMES, KARIER_PARAMETERS), (ROAD_VOLUMES, ROAD_PARAMETERS)):
        estimate = _direct(volumes, parameters, real_refs)
        for line in estimate.values():
            if line.qty:
                assert "не найдена" not in line.basis, line.id


# --- Э5а / Э6д (30.10.2026): районные к камеральным и программе — ввод,
# неблагоприятный период — по отметке объекта ---


def test_explicit_default_regional_coefficients_match_etalon(real_refs):
    """Ввод 1,25 явно = старое поведение (поля не заданы) = лист."""
    explicit = {**KARIER_PARAMETERS, h.P_KAMERAL_REGIONAL: 1.25, h.P_PROGRAM_REGIONAL: 1.25}
    for calculate in (_direct, _staged):
        estimate = calculate(KARIER_VOLUMES, explicit, real_refs)
        totals = calculate_total_with_vat(estimate, real_refs, "hydromet", "2026-09-24")
        assert totals.base_total == pytest.approx(KARIER_TOTALS[7], abs=0.005)
        assert totals.indexed_total == pytest.approx(KARIER_TOTALS[8], abs=0.005)


@pytest.mark.parametrize("volumes, parameters, expected_lines, totals", CASES)
def test_kameral_regional_1_2_changes_total_by_005_of_kameral_sum(
    real_refs, volumes, parameters, expected_lines, totals
):
    """Районный к камеральным 1,2 вместо 1,25 при районном к техотчёту 1,25,
    введённом отдельно (расхождение редакции 2026 г.): итог (до индекса)
    меньше на 0,05 × Σ камеральных — с округлением итога камеральных до
    рубля, как в листе: ROUND(Σ × 1,25) − ROUND(Σ × 1,2). Техотчёт и
    программа не меняются."""
    base = _direct(volumes, parameters, real_refs)
    changed = _direct(
        volumes, {**parameters, h.P_KAMERAL_REGIONAL: 1.2, h.P_REPORT_REGIONAL: 1.25}, real_refs
    )
    kameral_sum = sum(v for k, v in expected_lines.items() if k.startswith("kameral."))
    assert kameral_sum in (3151, 14370)  # Σ камеральных листа (Карьер / Автодорога)
    assert excel_round(kameral_sum * 1.25, 0) == totals[1]

    def total(estimate):
        return calculate_total_with_vat(estimate, real_refs, "hydromet", "2026-09-24").base_total

    difference = total(base) - total(changed)
    assert difference == excel_round(kameral_sum * 1.25, 0) - excel_round(kameral_sum * 1.2, 0)
    assert difference == pytest.approx(0.05 * kameral_sum, abs=0.5)
    assert _sum(changed, [h.REPORT_ID]) == totals[6]
    assert _sum(changed, [h.PROGRAM_ID]) == totals[5]


@pytest.mark.parametrize("volumes, parameters, expected_lines, totals", CASES)
@pytest.mark.parametrize("calculate", [_direct, _staged], ids=["direct", "staged"])
def test_report_regional_follows_kameral_by_default(
    real_refs, calculate, volumes, parameters, expected_lines, totals
):
    """Ревью ИГМИ, находка 1: районный техотчёта в шаблоне — та же ячейка
    Титул!J18, что у итога камеральных (Карьер F353 и D339, Автодорога F354
    и D340). Без отдельного ввода техотчёт = ROUND(Σ кам. × 0,8 × J18 ×
    1,25); с вводом — свой коэффициент; 0 = «не задан»."""
    kameral_sum = sum(v for k, v in expected_lines.items() if k.startswith("kameral."))
    follows = calculate(volumes, {**parameters, h.P_KAMERAL_REGIONAL: 1.2}, real_refs)
    assert _sum(follows, [h.REPORT_ID]) == excel_round(kameral_sum * 0.8 * 1.2 * 1.25, 0)
    assert "= районный к камеральным" in follows[h.REPORT_ID].basis
    zero = calculate(
        volumes, {**parameters, h.P_KAMERAL_REGIONAL: 1.2, h.P_REPORT_REGIONAL: 0}, real_refs
    )
    assert _sum(zero, [h.REPORT_ID]) == _sum(follows, [h.REPORT_ID])
    own = calculate(volumes, {**parameters, h.P_REPORT_REGIONAL: 1.3}, real_refs)
    assert _sum(own, [h.REPORT_ID]) == excel_round(kameral_sum * 0.8 * 1.3 * 1.25, 0)
    assert "задан оператором отдельно" in own[h.REPORT_ID].basis
    # Явный ввод 1,25 = лист до копейки.
    explicit = calculate(volumes, {**parameters, h.P_REPORT_REGIONAL: 1.25}, real_refs)
    assert _sum(explicit, [h.REPORT_ID]) == totals[6]


def test_program_regional_is_literal_in_template_formula():
    """Программа: в обоих эталонах 1,25 вписано в формулу числом
    (Карьер F350 = 800*1.25*D350, Автодорога F351 = 3500*1.25*D351), а не
    ссылкой на Титул!J18, — поэтому районный к программе отдельный ввод.
    Техотчёт (F353 / F354) и итог камеральных (D339 / D340) читают J18."""
    karier = openpyxl.load_workbook(ECOLOGY_SOURCE_XLSX)[HYDROMET_KARIER_SHEET_NAME]
    road = openpyxl.load_workbook(SOURCE_XLSX)[HYDROMET_ROAD_SHEET_NAME]
    assert karier["F350"].value == "=800*1.25*D350"
    assert road["F351"].value == "=3500*1.25*D351"
    for sheet, report, kameral in ((karier, "F353", "D339"), (road, "F354", "D340")):
        assert "Титул!J18" in sheet[report].value
        assert sheet[kameral].value.endswith("Титул!J18")


def test_program_regional_is_operator_input(real_refs):
    estimate = _direct(KARIER_VOLUMES, {**KARIER_PARAMETERS, h.P_PROGRAM_REGIONAL: 1.2}, real_refs)
    assert _sum(estimate, [h.PROGRAM_ID]) == pytest.approx(800 * 1.2)


@pytest.mark.parametrize("volumes, parameters, expected_lines, totals", CASES)
@pytest.mark.parametrize("calculate", [_direct, _staged], ids=["direct", "staged"])
def test_unfavorable_period_multiplies_each_field_line_by_1_4(
    real_refs, calculate, volumes, parameters, expected_lines, totals
):
    """Отметка объекта «неблагоприятный период» при выборе «по отметке объекта» —
    каждая полевая строка × 1,4 до округления: ROUND(D × цена × 1,4; 0), как
    формула шаблона (IF(Титул!M15=1, Титул!M18)); камеральные не меняются. Без
    выбора (по умолчанию — «не применять», аудит 05.11.2026, О-5) отметка объекта
    ИГМИ не меняет: полевые — как на листе."""
    from core.object_data import CalculationContext, ObjectData

    refs = CalculationContext(real_refs, ObjectData(unfavorable_period=True))
    by_default = calculate(volumes, parameters, refs)
    for key in h.FIELD_POSITIONS:
        assert _sum(by_default, [f"hydromet.{key}"]) == expected_lines[key], key
    assert calculate_total_with_vat(
        by_default, real_refs, "hydromet", "2026-09-24"
    ).base_total == pytest.approx(totals[7], abs=0.005)

    parameters = {**parameters, h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_BY_OBJECT}
    estimate = calculate(volumes, parameters, refs)
    for key in h.FIELD_POSITIONS:
        line = estimate[f"hydromet.{key}"]
        assert _sum(estimate, [line.id]) == excel_round(line.qty * line.price * 1.4, 0), key
        assert _sum(estimate, [line.id]) == pytest.approx(
            excel_round(expected_lines[key] * 1.4, 0), abs=1
        ), key
    for key, amount in expected_lines.items():
        if key.startswith("kameral."):
            assert _sum(estimate, [f"hydromet.{key}"]) == amount, key
    plain = calculate(volumes, parameters, CalculationContext(real_refs, ObjectData()))
    assert _sum(plain, [f"hydromet.{k}" for k in h.FIELD_POSITIONS]) == sum(
        v for k, v in expected_lines.items() if not k.startswith("kameral.")
    )


@pytest.mark.parametrize("volumes, parameters, expected_lines, totals", CASES)
@pytest.mark.parametrize("calculate", [_direct, _staged], ids=["direct", "staged"])
def test_unfavorable_mode_off_ignores_object_flag(
    real_refs, calculate, volumes, parameters, expected_lines, totals
):
    """Боевой прогон 30.10.2026: отметка объекта одна на объект, а в листах
    компании 2026 г. у ИГМИ ×1,4 не применён. При отметке объекта и выборе «не
    применять» — полевые строки и итог как на листе (без ×1,4); «применять»
    без отметки объекта — × коэффициент."""
    from core.object_data import CalculationContext, ObjectData

    flagged = CalculationContext(real_refs, ObjectData(unfavorable_period=True))
    off = calculate(volumes, {**parameters, h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_OFF}, flagged)
    for key in h.FIELD_POSITIONS:
        assert _sum(off, [f"hydromet.{key}"]) == expected_lines[key], key
        assert "неблагоприятный" not in off[f"hydromet.{key}"].basis
    assert calculate_total_with_vat(
        off, real_refs, "hydromet", "2026-09-24"
    ).base_total == pytest.approx(totals[7], abs=0.005)

    plain = CalculationContext(real_refs, ObjectData())
    on = calculate(
        volumes, {**parameters, h.P_UNFAVORABLE_MODE: h.UNFAVORABLE_ON, h.P_UNFAVORABLE: 1.2}, plain
    )
    for key in h.FIELD_POSITIONS:
        line = on[f"hydromet.{key}"]
        assert _sum(on, [line.id]) == excel_round(line.qty * line.price * 1.2, 0), key


def test_old_version_without_new_fields_opens_with_same_numbers(real_refs, tmp_path):
    """Версия, сохранённая до 30.10.2026 (в вводе этапа сметы нет районных к
    камеральным и программе): открывается подтверждённой, не устаревшей, и
    пересчёт даёт те же суммы (по умолчанию 1,25)."""
    from core.persistence import SmetaStore

    session = StagedSession(as_staged(HydrometModule()))
    session.inputs["volumes"] = dict(KARIER_VOLUMES)
    session.inputs["estimate"] = dict(KARIER_PARAMETERS)
    assert h.P_KAMERAL_REGIONAL not in session.inputs["estimate"]
    store = SmetaStore(tmp_path / "smety.sqlite")
    first = store.save_version("Карьер", "hydromet", None, session.confirm("volumes", real_refs))
    second = store.save_version("Карьер", "hydromet", first, session.confirm("estimate", real_refs))

    _code, _name, chain = store.load_version_chain(second)
    reopened = StagedSession(as_staged(HydrometModule()))
    reopened.restore(chain)
    assert reopened.last_stage_status() == "confirmed"
    snapshot = dict(reopened.result("estimate").lines)
    recomputed = reopened.compute("estimate", real_refs).effective
    assert _sum(recomputed, list(recomputed)) == pytest.approx(_sum(snapshot, list(snapshot)))
    assert _sum(recomputed, list(recomputed)) == pytest.approx(KARIER_TOTALS[7], abs=0.005)
