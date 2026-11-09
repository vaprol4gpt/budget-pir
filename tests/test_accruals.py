"""Начисления уровня сметы (core/accruals.py, С3 30.10.2026).

1. Без ставок в данных объекта — смета раздела не меняется (эталон
   28 449 810,51 — tests/test_calc_date.py), только предупреждение.
2. Внешняя проверка по эталонным листам компании (`reference_data/`, согласованные
   эталоны в git): базы (полевые, лабораторные, камеральные, внутренний
   транспорт) берутся из ячеек листа как строки сметы, ставки — как в листе,
   начисления считает программа — и сравниваются с ячейками начислений листа.
   Модули считают узкий поднабор позиций, поэтому целиком лист на их строках
   не воспроизводится; правила начисления (база, порядок, округление)
   проверяются здесь, на базах листа. Расхождения — с причиной в докстринге
   теста, не подогнаны.
"""

from pathlib import Path

import openpyxl
import pytest

from core.accruals import (
    EXTERNAL_TRANSPORT,
    METROLOGY,
    ORG_LIQUIDATION,
    REGIONAL_FIELD,
    REGIONAL_OFFICE,
    UNFAVORABLE,
    accrual_warnings,
    apply_accruals,
    line_id,
)
from core.models import EstimateLine, calculate_amount
from core.object_data import CalculationContext, ObjectData
from core.references import DEFAULT_DB_PATH, ReferenceBook
from modules.ecology.module import ACCRUALS as ECOLOGY
from modules.geodesy.module import ACCRUALS as GEODESY
from modules.geology.module import ACCRUALS as GEOLOGY
from modules.geophysics.module import ACCRUALS as GEOPHYSICS

ROAD = Path(__file__).resolve().parent.parent / "reference_data" / "Автодорога_эталон.xlsx"
needs_etalon = pytest.mark.skipif(not ROAD.exists(), reason="нет эталонной книги в reference_data/")
needs_refs = pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite")


class NoRefs:
    """Справочник без позиций (неблагоприятный период не найдётся)."""

    def get_price(self, *args):
        raise KeyError(args)


def fixed(line_id_, amount):
    """Строка-база с готовой суммой (как ячейка листа)."""
    return EstimateLine(line_id_, line_id_, "руб.", 1, amount, {}, "A", [], "лист эталона")


def amounts(estimate):
    cache = {}
    return {i: calculate_amount(line, estimate, cache) for i, line in estimate.items()}


def total(estimate):
    return sum(amounts(estimate).values())


@pytest.fixture(scope="module")
def road():
    return openpyxl.load_workbook(ROAD, data_only=True)


# --- механизм ---


@pytest.mark.parametrize("scheme", [GEOLOGY, GEOPHYSICS, GEODESY, ECOLOGY], ids=lambda s: s.module)
def test_without_rates_estimate_is_unchanged_and_warns(scheme):
    estimate = {f"{scheme.module}.x": fixed(f"{scheme.module}.x", 100.0)}
    refs = CalculationContext(NoRefs(), ObjectData())
    assert apply_accruals(estimate, scheme, refs) is estimate
    warnings = accrual_warnings(scheme, refs)
    assert len(warnings) == 1 and "не применены" in warnings[0]


def test_zero_rate_means_not_charged_but_line_present():
    # 0 — «не начислять» (как внешний транспорт у карьеров), строка видна с нулём
    estimate = {"geology.drilling.IV": fixed("geology.drilling.IV", 1000.0)}
    refs = CalculationContext(NoRefs(), ObjectData(external_transport_rate=0.0))
    result = apply_accruals(estimate, GEOLOGY, refs)
    assert amounts(result)[line_id(GEOLOGY, EXTERNAL_TRANSPORT)] == 0
    assert "внешний транспорт" not in " ".join(accrual_warnings(GEOLOGY, refs))


def test_metrology_alone_is_not_applied():
    # фиксированная ставка (метрология геофизики) — только вместе со ставками объекта
    estimate = {"geophysics.vez": fixed("geophysics.vez", 1000.0)}
    refs = CalculationContext(NoRefs(), ObjectData())
    assert apply_accruals(estimate, GEOPHYSICS, refs) is estimate


def test_unfavorable_only_where_scheme_has_it():
    data = ObjectData(unfavorable_period=True)
    assert any("не применён" in w for w in accrual_warnings(ECOLOGY, CalculationContext(NoRefs(), data)))


def test_unfavorable_missing_in_reference_book_is_named_as_such():
    # у геологии надбавка в схеме есть; нет позиции в справочнике — так и сказать, а не
    # «в эталонных сметах раздела его нет» (аудит 05.11, L-А9)
    refs = CalculationContext(NoRefs(), ObjectData(unfavorable_period=True))
    warnings = accrual_warnings(GEOLOGY, refs)
    assert any("нет позиции СБЦ-99 т.2 п.4" in w for w in warnings)
    assert not any("в эталонных сметах раздела его нет" in w for w in warnings)


@needs_refs
def test_unfavorable_coefficient_comes_from_reference_book():
    refs = CalculationContext(ReferenceBook(), ObjectData(unfavorable_period=True))
    estimate = {"geology.drilling.IV": fixed("geology.drilling.IV", 1000.0)}
    result = apply_accruals(estimate, GEOLOGY, refs)
    line = result[line_id(GEOLOGY, UNFAVORABLE)]
    assert line.coefs["rate"] == pytest.approx(0.2)  # СБЦ-99 т.2 п.4 = 1,2
    assert amounts(result)[line.id] == 200


# --- внешняя проверка по эталонным листам ---


@needs_etalon
@needs_refs
def test_geology_matches_etalon_sheet_1_2_1(road):
    """«1.2.1_ИГИ»: неблагоприятный ×1,2, внеш. транспорт 39,2 %, оргликвидация
    6 % × 2,5, районные 0,5 / 0,25 — каждая строка начислений = ячейке листа.
    Итог листа H158 = ROUND(суммы) — у программы итог без этого последнего
    округления (расхождение 0,25 руб. базы — округление листа, не правило)."""
    ws = road["1.2.1_ИГИ"]
    v = {c: ws[c].value for c in ("H81", "H82", "H125", "H146", "H148", "H151", "H152",
                                  "H153", "H156", "H157", "H158", "H160")}
    estimate = {
        "geology.drilling.IV": fixed("geology.drilling.IV", v["H81"]),
        "geology.transport": fixed("geology.transport", v["H151"]),
        "geology.lab_x": fixed("geology.lab_x", v["H125"]),
        "geology.kameral_bureniya": fixed("geology.kameral_bureniya", v["H146"]),
        "geology.tehotchet": fixed("geology.tehotchet", v["H148"]),
    }
    data = ObjectData(unfavorable_period=True, external_transport_rate=0.392,
                      org_liquidation_rate=0.06, org_liquidation_k=2.5,
                      regional_field_rate=0.5, regional_office_rate=0.25)
    result = apply_accruals(estimate, GEOLOGY, CalculationContext(ReferenceBook(), data))
    a = amounts(result)
    assert a[line_id(GEOLOGY, UNFAVORABLE)] == v["H82"] - v["H81"]
    assert a[line_id(GEOLOGY, EXTERNAL_TRANSPORT)] == v["H152"]
    assert a[line_id(GEOLOGY, ORG_LIQUIDATION)] == v["H153"]
    assert a[line_id(GEOLOGY, REGIONAL_FIELD)] == pytest.approx(v["H156"], abs=0.005)
    assert a[line_id(GEOLOGY, REGIONAL_OFFICE)] == pytest.approx(v["H157"], abs=0.005)
    assert round(total(result)) == v["H158"]
    assert round(round(total(result)) * 81.95) == v["H160"]


KARER = ROAD.parent / "Карьер_эталон.xlsx"


@pytest.mark.skipif(not KARER.exists(), reason="нет эталонной книги в reference_data/")
@needs_refs
def test_geology_unfavorable_rounding_matches_karer_sheet_2_2_1():
    """Округление надбавки за неблагоприятный период (аудит 05.11, L-А3) — на листе, где
    H82 дробный: «№2.2.1_ИГИ» H82 = H81 × 1,2 с десятыми. Лист дальше берёт не H82, а
    D151 = ROUND(H82) (база внутреннего, внешнего транспорта, оргликвидации и районного).
    Программа округляет саму надбавку: H81 + ROUND(H81 × 0,2) = D151 — та же база при целом H81 (лист округляет каждую строку и итог полевых). Районные — без
    округления, как H156 / H157 (округлена их база D156 / D157); итог листа — ROUND (H158)."""
    ws = openpyxl.load_workbook(KARER, data_only=True)["№2.2.1_ИГИ"]
    v = {c: ws[c].value for c in ("H81", "H82", "D151", "H125", "H146", "H148", "H151", "H152",
                                  "H153", "H156", "H157", "H158")}
    assert v["H82"] != round(v["H82"])  # лист действительно с дробной надбавкой
    estimate = {
        "geology.drilling.IV": fixed("geology.drilling.IV", v["H81"]),
        "geology.transport": fixed("geology.transport", v["H151"]),
        "geology.lab_x": fixed("geology.lab_x", v["H125"]),
        "geology.kameral_bureniya": fixed("geology.kameral_bureniya", v["H146"]),
        "geology.tehotchet": fixed("geology.tehotchet", v["H148"]),
    }
    data = ObjectData(unfavorable_period=True, external_transport_rate=0.392,
                      org_liquidation_rate=0.06, org_liquidation_k=2.5,
                      regional_field_rate=0.5, regional_office_rate=0.25)
    a = amounts(apply_accruals(estimate, GEOLOGY, CalculationContext(ReferenceBook(), data)))
    assert v["H81"] + a[line_id(GEOLOGY, UNFAVORABLE)] == v["D151"]
    assert a[line_id(GEOLOGY, EXTERNAL_TRANSPORT)] == v["H152"]
    assert a[line_id(GEOLOGY, ORG_LIQUIDATION)] == v["H153"]
    assert a[line_id(GEOLOGY, REGIONAL_FIELD)] == pytest.approx(v["H156"], abs=0.005)
    assert a[line_id(GEOLOGY, REGIONAL_OFFICE)] == pytest.approx(v["H157"], abs=0.005)
    assert round(sum(a.values())) == v["H158"]


@needs_etalon
def test_ecology_matches_etalon_sheet_1_4_full_total(road):
    """«№1.4_ИЭИ»: внеш. транспорт 39,2 %, оргликвидация 6 % (без ×2,5),
    районные ×1,6 / ×1,25 — строки и итог листа H95 = (H90 + H93) × 81,95 до
    копейки. Лабораторные (H72) — база районного к камеральным, здесь — одной
    строкой группы «lab» (ecology.lab.*). Тот же расчёт на настоящем вводе
    модуля (строки анализов, а не подмены) — tests/test_ecology_etalons.py::
    test_regional_office_base_includes_lab_lines_in_live_estimate (Э5б)."""
    ws = road["№1.4_ИЭИ"]
    v = {c: ws[c].value for c in ("H18", "H51", "H53", "H54", "H55", "H72", "H85", "H87",
                                  "H88", "H90", "H93", "H95")}
    estimate = {
        "ecology.program": fixed("ecology.program", v["H18"]),
        "ecology.recon": fixed("ecology.recon", v["H51"]),
        "ecology.transport": fixed("ecology.transport", v["H53"]),
        "ecology.lab.total": fixed("ecology.lab.total", v["H72"]),
        "ecology.kameral.recon": fixed("ecology.kameral.recon", v["H85"]),
        "ecology.report": fixed("ecology.report", v["H87"]),
    }
    data = ObjectData(external_transport_rate=0.392, org_liquidation_rate=0.06,
                      regional_field_rate=0.6, regional_office_rate=0.25)
    result = apply_accruals(estimate, ECOLOGY, CalculationContext(NoRefs(), data))
    a = amounts(result)
    assert a[line_id(ECOLOGY, EXTERNAL_TRANSPORT)] == v["H54"]
    assert a[line_id(ECOLOGY, ORG_LIQUIDATION)] == v["H55"]
    field_total = v["H51"] + v["H53"] + v["H54"] + v["H55"]
    assert field_total + a[line_id(ECOLOGY, REGIONAL_FIELD)] == pytest.approx(v["H90"], abs=1e-6)
    assert v["H88"] + a[line_id(ECOLOGY, REGIONAL_OFFICE)] == pytest.approx(v["H93"], abs=1e-6)
    assert total(result) * 81.95 == pytest.approx(v["H95"], abs=0.005)


@needs_etalon
def test_geodesy_matches_etalon_sheet_1_1(road):
    """«№1.1_ИГДИ»: внеш. транспорт 39,2 %, оргликвидация 6 % × 2,5, районные
    0,3 / 0,25 — совпадают с G65, G66, G59, G68 до рубля. С Э6б у модуля есть
    своя строка базы экспедиции (G42 — в базах G65, G66, G68) и знаков (G39 —
    только в базе районного к полевым G68), как в формулах листа; камеральные
    — уже с надбавкой ×1,2 «компьютерные технологии» (G58)."""
    ws = road["№1.1_ИГДИ"]
    v = {c: ws[c].value for c in ("G36", "G39", "G42", "G58", "G59", "G64", "G65", "G66", "G68")}
    estimate = {
        "geodesy.oporny_set": fixed("geodesy.oporny_set", v["G36"]),
        "geodesy.znaki": fixed("geodesy.znaki", v["G39"]),
        "geodesy.base": fixed("geodesy.base", v["G42"]),
        "geodesy.transport": fixed("geodesy.transport", v["G64"]),
        "geodesy.topo_plan.0": fixed("geodesy.topo_plan.0", v["G58"]),
    }
    data = ObjectData(external_transport_rate=0.392, org_liquidation_rate=0.06,
                      org_liquidation_k=2.5, regional_field_rate=0.3, regional_office_rate=0.25)
    a = amounts(apply_accruals(estimate, GEODESY, CalculationContext(NoRefs(), data)))
    assert a[line_id(GEODESY, EXTERNAL_TRANSPORT)] == v["G65"]
    assert a[line_id(GEODESY, ORG_LIQUIDATION)] == v["G66"]
    assert v["G58"] + a[line_id(GEODESY, REGIONAL_OFFICE)] == v["G59"]
    assert a[line_id(GEODESY, REGIONAL_FIELD)] == v["G68"]


@needs_etalon
def test_geophysics_matches_etalon_sheet_1_2_2_except_org(road):
    """«№1.2.2_ИГФИ»: внеш. транспорт 6,5 %, метрология 5 % — совпадают с K29,
    K30. Оргликвидация K31 в листе — особая формула СЦиР-1982 с фиксированными
    суммами ((F×3 % + 3000) + (F×0,025 % + 2500)) × 1,2; схема ACCRUALS считает
    «ставка × база», формулу листа модуль строит сам по выбору оператора
    (параметр сметы, Э6в — tests/test_geophysics_etalon.py, K31 и итог листа):
    здесь районный K32 сверяется при подстановке K31 листа — правило «0,66 от
    полевых + вн.тр. + внеш.тр. + метрология + оргл.»."""
    ws = road["№1.2.2_ИГФИ"]
    v = {c: ws[c].value for c in ("K20", "K27", "K28", "K29", "K30", "K31", "K32")}
    estimate = {
        "geophysics.vez": fixed("geophysics.vez", v["K20"]),
        "geophysics.kameralka_mpv": fixed("geophysics.kameralka_mpv", v["K27"]),
        "geophysics.transport": fixed("geophysics.transport", v["K28"]),
    }
    data = ObjectData(external_transport_rate=0.065, regional_field_rate=0.66)
    result = apply_accruals(estimate, GEOPHYSICS, CalculationContext(NoRefs(), data))
    a = amounts(result)
    assert a[line_id(GEOPHYSICS, EXTERNAL_TRANSPORT)] == pytest.approx(v["K29"], abs=1e-6)
    assert a[line_id(GEOPHYSICS, METROLOGY)] == pytest.approx(v["K30"], abs=1e-6)
    regional_base = v["K20"] + v["K28"] + v["K29"] + v["K30"] + v["K31"]
    assert regional_base * 0.66 == pytest.approx(v["K32"], abs=1e-6)
    # без оргликвидации (ставка не задана) база районного программы — без K31
    assert a[line_id(GEOPHYSICS, REGIONAL_FIELD)] == pytest.approx(
        (regional_base - v["K31"]) * 0.66, abs=1e-6
    )


# --- через интерфейс: ставки на странице объекта → смета → сводный бюджет ---


@pytest.fixture
def isolated_smeta_db(tmp_path, monkeypatch):
    import core.persistence as persistence_module

    monkeypatch.setattr(persistence_module, "DEFAULT_DB_PATH", tmp_path / "test_smety.sqlite")


@needs_refs
def test_object_rates_reach_estimate_and_budget_breakdown(isolated_smeta_db):
    from tests.test_streamlit_app import _fill_geology, goto, open_app

    at = open_app("obekt")
    at.number_input(key="obj_external_transport_rate").set_value(0.392)
    at.number_input(key="obj_regional_field_rate").set_value(0.5)
    at.run(timeout=30)
    goto(at, "vedomost")
    _fill_geology(at)
    at.button(key="confirm_geology_volumes").click()
    at.run(timeout=30)
    goto(at, "smeta")
    assert at.exception == []
    lines = at.session_state["staged_sessions"]["geology"].compute("estimate", at.session_state["_run_context"].refs).effective
    assert line_id(GEOLOGY, EXTERNAL_TRANSPORT) in lines
    assert line_id(GEOLOGY, REGIONAL_FIELD) in lines
    assert line_id(GEOLOGY, ORG_LIQUIDATION) not in lines  # ставка не задана
    assert any("не применены" in w.value and "организация" in w.value for w in at.warning)
    at.button(key="confirm_geology_estimate").click()
    at.run(timeout=30)
    goto(at, "svodny")
    assert at.exception == []
    assert any("Начисления по разделам (2)" in e.label for e in at.expander)


# --- после независимой проверки (С8) ---


def test_per_module_rate_overrides_object_rate():
    """Ставка раздела перекрывает общую: у геофизики внешний транспорт 6,5 %,
    у геологии — общие 39,2 % (в эталонах на одном объекте ставки разные)."""
    data = ObjectData.from_dict({
        "external_transport_rate": 0.392,
        "accrual_overrides": {"geophysics": {"external_transport_rate": 0.065, "org_liquidation_k": None},
                              "unknown": {"external_transport_rate": 1.0}},
    })
    assert data.accrual_overrides == {"geophysics": {"external_transport_rate": 0.065}}
    refs = CalculationContext(NoRefs(), data)
    geophysics = apply_accruals({"geophysics.vez": fixed("geophysics.vez", 1000.0)}, GEOPHYSICS, refs)
    geology = apply_accruals({"geology.drilling.IV": fixed("geology.drilling.IV", 1000.0)}, GEOLOGY, refs)
    assert amounts(geophysics)[line_id(GEOPHYSICS, EXTERNAL_TRANSPORT)] == pytest.approx(65.0)
    assert amounts(geology)[line_id(GEOLOGY, EXTERNAL_TRANSPORT)] == 392
    assert "Геофизика" in ObjectData(accrual_overrides={"geophysics": {"external_transport_rate": 0.065}}).info()[
        "Ставки начислений по разделам"]


def test_metrology_not_added_when_all_object_rates_are_zero():
    refs = CalculationContext(NoRefs(), ObjectData(external_transport_rate=0.0))
    result = apply_accruals({"geophysics.vez": fixed("geophysics.vez", 1000.0)}, GEOPHYSICS, refs)
    assert line_id(GEOPHYSICS, METROLOGY) not in result
    assert line_id(GEOPHYSICS, EXTERNAL_TRANSPORT) in result


def test_geophysics_org_liquidation_warns_about_etalon_formula():
    refs = CalculationContext(NoRefs(), ObjectData(org_liquidation_rate=0.06))
    assert any("K31" in w for w in accrual_warnings(GEOPHYSICS, refs))


def test_per_module_rate_from_object_page_reaches_object_data(isolated_smeta_db):
    from tests.test_streamlit_app import open_app

    at = open_app("obekt")
    at.number_input(key="obj_acc_geophysics_external_transport_rate").set_value(0.065)
    at.run(timeout=30)
    assert at.exception == []
    data = at.session_state["_run_context"].object_data
    assert data.rate("geophysics", "external_transport_rate") == pytest.approx(0.065)
    assert data.rate("geology", "external_transport_rate") is None
