"""Геодезия (Э6б): модуль с объёмами листа «№1.1_ИГДИ» (Автодорога,
`reference_data/`) даёт итог листа.

Объёмы и ставки начислений — как в листе (ячейки D и формулы G); цены — из
справочника, собранного настоящим парсером (`data/references.sqlite`).
Строки листа округлены до рубля (ROUND), строки модуля — нет, поэтому
построчно сравнение — с допуском 1 руб., итог — ± 0,1 % (критерий карты),
фактически расхождение — десятки рублей на 14,4 млн.
"""

from pathlib import Path

import openpyxl
import pytest

from core.models import calculate_amount
from core.object_data import CalculationContext, ObjectData
from core.references import DEFAULT_DB_PATH, ReferenceBook
from core.stages import StagedSession, as_staged
from modules.geodesy.module import (
    COMPUTER_LINE_ID,
    EXPERTISE_FIXED_LINE_ID,
    EXPERTISE_LINE_ID,
    GeodesyModule,
)

ROAD = Path(__file__).resolve().parent.parent / "reference_data" / "Автодорога_эталон.xlsx"
pytestmark = [
    pytest.mark.skipif(not ROAD.exists(), reason="нет эталонной книги в reference_data/"),
    pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite"),
]

INDEX_2026_Q3 = 7.19  # лист G74

# Объёмы листа (столбец D) и условия съёмки (коэффициенты E31, E34, E54).
ROAD_VOLUMES = {
    "oporny_points_count": 8,            # D21
    "height_points_count": 8,            # D25, D50
    "trassa_km": 14.7,                   # D28, D51
    "privyazka_count": 50,               # D35
    "znaki_count": 8,                    # D38
    "base_months": 2,                    # D41
    "ggs_plan_points": 15,               # D44
    "ggs_height_points": 15,             # D45
    "cartogram_count": 1,                # D46
    "oporny_kameral_points_count": 8,    # D47
    "soglasovanie_count": 3,             # D56
    "topo_surveys": [
        {"topo_scale": "1:1000", "topo_section": 0.5, "topo_area": 147, "survey_condition": "strip"},
        {"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2, "survey_condition": "small"},
    ],
    "topo_plans": [
        {"topo_scale": "1:1000", "topo_section": 0.5, "topo_area": 147, "plan_condition": "strip"},
        {"topo_scale": "1:500", "topo_section": 0.5, "topo_area": 2},
    ],
}
ROAD_PARAMETERS = {"computer_tech_coef": 1.2, "expertise": "yes"}  # G58, G73
# Ставки начислений листа: G65 39,2 %, G66 6 % × 2,5, G68 0,15 + 0,15, G59 0,1 + 0,15.
ROAD_OBJECT = ObjectData(external_transport_rate=0.392, org_liquidation_rate=0.06,
                         org_liquidation_k=2.5, regional_field_rate=0.3,
                         regional_office_rate=0.25)

# Строка модуля → ячейка листа.
LINE_CELLS = {
    "geodesy.oporny_set": "G21",
    "geodesy.height_set": "G25",
    "geodesy.trassa": "G28",
    "geodesy.topo_survey.0": "G29",
    "geodesy.topo_survey.1": "G32",
    "geodesy.privyazka": "G35",
    "geodesy.znaki": "G38",
    "geodesy.base": "G41",
    "geodesy.kameral.ggs_plan": "G44",
    "geodesy.kameral.ggs_height": "G45",
    "geodesy.kameral.cartogram": "G46",
    "geodesy.kameral.oporny_set": "G47",
    "geodesy.kameral.height_set": "G50",
    "geodesy.kameral.trassa": "G51",
    "geodesy.topo_plan.0": "G52",
    "geodesy.topo_plan.1": "G55",
    "geodesy.kameral.soglasovanie": "G56",
    "geodesy.transport": "G64",
    "geodesy.accrual.external_transport": "G65",
    "geodesy.accrual.org_liquidation": "G66",
    "geodesy.accrual.regional_field": "G68",
}


@pytest.fixture(scope="module")
def sheet():
    return openpyxl.load_workbook(ROAD, data_only=True)["№1.1_ИГДИ"]


def run_stages(volume_inputs, estimate_inputs, data=ROAD_OBJECT):
    refs = CalculationContext(ReferenceBook(), data)
    session = StagedSession(as_staged(GeodesyModule()))
    session.inputs["volumes"] = volume_inputs
    session.confirm("volumes", refs)
    session.inputs["estimate"] = estimate_inputs
    lines = session.compute("estimate", refs).effective
    cache: dict[str, float] = {}
    return lines, {i: calculate_amount(line, lines, cache) for i, line in lines.items()}


@pytest.fixture(scope="module")
def road():
    return run_stages(ROAD_VOLUMES, ROAD_PARAMETERS)


def test_total_matches_sheet_within_0_1_percent(sheet, road):
    _lines, amounts = road
    base_total = sum(amounts.values())
    assert sheet["G75"].value == 14424312
    assert base_total == pytest.approx(sheet["G72"].value + sheet["G73"].value, rel=0.001)
    assert base_total * INDEX_2026_Q3 == pytest.approx(sheet["G75"].value, rel=0.001)
    # фактически — десятки рублей (строки модуля не округляются до рубля)
    assert abs(base_total * INDEX_2026_Q3 - sheet["G75"].value) < 100


@pytest.mark.parametrize("line_id", list(LINE_CELLS))
def test_each_line_matches_sheet(sheet, road, line_id):
    _lines, amounts = road
    assert amounts[line_id] == pytest.approx(sheet[LINE_CELLS[line_id]].value, abs=1)


def test_office_with_computer_and_regional_match_sheet(sheet, road):
    _lines, amounts = road
    office = sum(a for i, a in amounts.items()
                 if i.startswith(("geodesy.kameral.", "geodesy.topo_plan.")) and i != COMPUTER_LINE_ID)
    assert office == pytest.approx(sheet["G57"].value, abs=1)
    assert office + amounts[COMPUTER_LINE_ID] == pytest.approx(sheet["G58"].value, abs=1)
    assert (office + amounts[COMPUTER_LINE_ID] + amounts["geodesy.accrual.regional_office"]
            == pytest.approx(sheet["G62"].value, abs=2))


def test_expertise_matches_sheet_formula(sheet, road):
    _lines, amounts = road
    expertise = amounts[EXPERTISE_LINE_ID] + amounts[EXPERTISE_FIXED_LINE_ID]
    assert expertise == pytest.approx(sheet["G73"].value, abs=1)
    before = sum(a for i, a in amounts.items() if i not in (EXPERTISE_LINE_ID, EXPERTISE_FIXED_LINE_ID))
    assert expertise == pytest.approx(round(((before - 1_000_000) * 0.01 + 18_750) * 1.3), abs=1)


def test_transport_base_is_field_plus_expedition_base(road):
    lines, _amounts = road
    base_ids = set(lines["geodesy.transport"].base_ids)
    assert "geodesy.base" in base_ids and "geodesy.trassa" in base_ids
    assert "geodesy.znaki" not in base_ids          # G64 = G36 + G42, без G39
    assert not any(i.startswith("geodesy.kameral.") for i in base_ids)


# --- нейтральность по умолчанию: прежний ввод — прежняя смета ---

OLD_INPUT = {"oporny_points_count": 8, "privyazka_count": 50}


def test_defaults_add_no_lines():
    lines, amounts = run_stages(OLD_INPUT, {}, data=ObjectData())
    assert set(lines) == {"geodesy.oporny_set", "geodesy.privyazka", "geodesy.transport"}
    assert sum(amounts.values()) == pytest.approx((8 * 6426 * 0.7 * 1.3 + 50 * 256) * 1.0875)


def test_computer_coef_one_and_expertise_off_add_nothing():
    lines, _ = run_stages({**OLD_INPUT, "topo_plans": [{"topo_scale": "1:500", "topo_area": 2}]},
                          {"computer_tech_coef": 1.0, "expertise": "no"}, data=ObjectData())
    assert COMPUTER_LINE_ID not in lines and EXPERTISE_LINE_ID not in lines


def test_small_area_coefficient_is_not_applied_to_plan():
    # к = 1,2 (т.10 п.1) — «стоимость полевых работ»; к плану лист его не применяет
    module = GeodesyModule()
    volumes = module.build_volumes(
        {"topo_plans": [{"topo_scale": "1:500", "topo_area": 2, "plan_condition": "small"}]},
        ReferenceBook(),
    )
    plan = module.build_estimate(volumes, ReferenceBook())["geodesy.topo_plan.0"]
    assert plan.coefs == {}


def test_old_volume_snapshot_without_new_lines_still_computes():
    # снимок ведомости до Э6б: строк новых позиций нет — объём 0, строк сметы нет
    sheet = GeodesyModule().build_volume_sheet(OLD_INPUT)
    assert set(sheet) == {"oporny_points_count", "privyazka_count"}


def _estimate_draft(volume_inputs, estimate_inputs, data=None, custom=None):
    refs = CalculationContext(ReferenceBook(), data or ObjectData())
    session = StagedSession(as_staged(GeodesyModule()))
    session.inputs["volumes"] = volume_inputs
    session.confirm("volumes", refs)
    session.inputs["estimate"] = estimate_inputs
    for row in custom or []:
        session.overrides["estimate"].add_custom_line(row)
    return session.compute("estimate", refs)


def test_expertise_below_one_million_creates_no_lines_and_warns():
    # Ревью: формула т.80 прим.2 — только для итога свыше 1 млн; меньше — строк нет
    draft = _estimate_draft(OLD_INPUT, {"expertise": "yes"})
    assert EXPERTISE_LINE_ID not in draft.effective
    assert EXPERTISE_FIXED_LINE_ID not in draft.effective
    assert any("Экспертиза" in w and "1 млн" in w and "своей строкой" in w
               for w in draft.warnings)
    # и смета та же, что без экспертизы
    plain = _estimate_draft(OLD_INPUT, {})
    assert set(draft.effective) == set(plain.effective)


def test_expertise_ignores_custom_lines_and_warns():
    custom = {"name": "Аренда вездехода", "unit": "смена", "qty": 2, "price": 10_000,
              "basis_kind": "price_list", "basis": "прайс", "placement": "before_index"}
    draft = _estimate_draft(ROAD_VOLUMES, ROAD_PARAMETERS, data=ROAD_OBJECT, custom=[custom])
    custom_ids = [i for i in draft.effective if i.startswith("custom.")]
    assert custom_ids
    assert not set(custom_ids) & set(draft.effective[EXPERTISE_LINE_ID].base_ids)
    assert any("Аренда вездехода" in w and "G72" in w for w in draft.warnings)
    # без своих строк — этого предупреждения нет
    plain = _estimate_draft(ROAD_VOLUMES, ROAD_PARAMETERS, data=ROAD_OBJECT)
    assert not any("G72" in w for w in plain.warnings)


def test_expertise_field_label_names_sheet_cell():
    field = next(f for f in GeodesyModule().input_schema() if f.name == "expertise")
    assert "B73 «К=1,3»" in field.label and "основание в листе не указано" in field.label


# --- условия съёмки: коэффициент — только в условиях подписи листа ---

def _survey(scale, area, condition):
    module = GeodesyModule()
    data = {**OLD_INPUT, "topo_surveys": [
        {"topo_scale": scale, "topo_section": 0.5, "topo_area": area, "survey_condition": condition}]}
    estimate = module.build_estimate(module.build_volumes(data, ReferenceBook()), ReferenceBook())
    return estimate["geodesy.topo_survey.0"], module.check(estimate)


@pytest.mark.parametrize("scale, area", [("1:500", 2), ("1:500", 5), ("1:500", 1.01)])
def test_small_area_coefficient_applies_within_sheet_conditions(scale, area):
    line, warnings = _survey(scale, area, "small")
    assert line.coefs == {"K1": 1.2}
    assert not any("условие съёмки" in w for w in warnings)


@pytest.mark.parametrize("scale, area, reason", [
    ("1:1000", 147, "масштаб 1:1000"),   # находка ревью: 1,2 к 1:1000 147 га
    ("1:500", 1, "свыше 1 до 5"),
    ("1:500", 6, "свыше 1 до 5"),
])
def test_small_area_coefficient_not_applied_outside_conditions(scale, area, reason):
    line, warnings = _survey(scale, area, "small")
    assert line.coefs == {}
    assert any("условие съёмки" in w and reason in w for w in warnings)


def test_strip_coefficient_only_for_1_500_to_1_2000():
    line, warnings = _survey("1:1000", 147, "strip")
    assert line.coefs == {"K1": 0.7} and not any("условие съёмки" in w for w in warnings)
    line, warnings = _survey("1:5000", 100, "strip")
    assert line.coefs == {}
    assert any("условие съёмки" in w and "1:500–1:2000" in w for w in warnings)


def test_condition_labels_name_scale_and_area():
    labels = dict(GeodesyModule().field_options("survey_condition", None))
    assert "1:500" in labels["small"] and "свыше 1 до 5 га" in labels["small"]
    assert "1:500–1:2000" in labels["strip"]


# --- камеральная обработка плановой сети: забытое поле ---

def test_oporny_without_kameral_warns_on_both_stages():
    module = GeodesyModule()
    volume_stage, estimate_stage = module.stages()
    refs = CalculationContext(ReferenceBook(), ObjectData())
    assert any("камеральная обработка" in w
               for w in volume_stage.context_warnings(OLD_INPUT, {}, refs))
    draft = _estimate_draft(OLD_INPUT, {})
    assert any("камеральная обработка плановой сети = 0" in w for w in draft.warnings)
    # введена — предупреждения нет; сети нет — тоже нет
    with_kameral = {**OLD_INPUT, "oporny_kameral_points_count": 8}
    assert not any("камеральная обработка" in w
                   for w in volume_stage.context_warnings(with_kameral, {}, refs))
    assert not any("камеральная обработка плановой сети" in w
                   for w in _estimate_draft(with_kameral, {}).warnings)
    no_net = {"oporny_points_count": 0, "privyazka_count": 50}
    assert not any("камеральная обработка плановой сети" in w
                   for w in _estimate_draft(no_net, {}).warnings)


def test_direct_path_has_no_expertise():
    module = GeodesyModule()
    volumes = module.build_volumes({**ROAD_VOLUMES, **ROAD_PARAMETERS}, ReferenceBook())
    estimate = module.build_estimate(volumes, ReferenceBook())
    assert EXPERTISE_LINE_ID not in estimate and COMPUTER_LINE_ID in estimate


def test_new_volume_lines_qty_not_editable_on_estimate():
    module = GeodesyModule()
    estimate_stage = module.stages()[1]
    refs = CalculationContext(ReferenceBook(), ObjectData())
    session = StagedSession(as_staged(module))
    session.inputs["volumes"] = {**OLD_INPUT, "trassa_km": 14.7}
    session.confirm("volumes", refs)
    lines = session.compute("estimate", refs).effective
    previous = session.previous_results("estimate")
    assert estimate_stage.editable_fields(lines["geodesy.trassa"], previous) == ("price",)
    assert estimate_stage.editable_fields(lines["geodesy.kameral.trassa"], previous) == ("price",)
