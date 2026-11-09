"""Сверка модуля экологии с двумя эталонными сметами нового шаблона —
"№2.5_ИЭИ" (Карьер) и "№1.4_ИЭИ" (Автодорога) — на справочнике,
собранном из тех же xlsx настоящим парсером (data/build_references.py),
а не на подставных ценах. Проверяет цепочку парсер → модуль целиком.

Исходные объёмы и эталонные суммы — из листов (формулы ячеек разобраны
в записи разработки):

| | Карьер | Автодорога |
|---|---|---|
| фондовые материалы (Т78 п.2), 10 цифр. знач. | 0 | 2,6 |
| электронная подоснова: пункт Т80 / объём | п.14 / 3 | п.13 / 7 |
| проходимость | хорошая | удовлетворительная |
| рекогносцировка, км | 4 | 14,7 |
| точки наблюдений | 5 | 11 |
| радиационное, га | 22,5 | 0 |
| к полевых маршрутных | — | 1,3 |
| к камеральных маршрутных | — | — |
| ИТОГО лабораторные | 13 813 | 17 740,4 |
| рекогносцировочный блок (полевой) | 231,58 | 946,167 |
| камеральные, итого | 3 228,46 | 3 979,558 |
| предполевые, итого (с программой работ) | 280,06 | 345,39 |
| техотчёт | 736,7892 | 908,23908 |
"""

import re

import openpyxl
import pytest

from core.models import calculate_amount
from core.references import ReferenceBook
from core.stages import StagedSession, as_staged
from data.build_references import (
    ECOLOGY_ROAD_SHEET_NAME,
    ECOLOGY_SHEET_NAME,
    ECOLOGY_SOURCE_XLSX,
    SOURCE_XLSX,
    build_database,
    extract_ecology_price_entries,
    extract_ecology_road_price_entries,
)
from modules.ecology.module import EcologyModule

KAMERAL_IDS = [
    "ecology.kameral.recon",
    "ecology.kameral.soil_recon",
    "ecology.kameral.route",
    "ecology.kameral.observation_points",
    "ecology.kameral.radiation",
    "ecology.kameral.radon",
    "ecology.kameral.lab",
]
PRELIMINARY_IDS = ["ecology.program", "ecology.fund_materials", "ecology.subbase"]
RECON_FIELD_IDS = [
    "ecology.recon",
    "ecology.soil_recon",
    "ecology.route",
    "ecology.observation_points",
]

KARIER_INPUT = {
    "fund_materials_count": 0,
    "subbase_item": "14",
    "subbase_volume": 3,
    "passability": "good",
    "recon_length_km": 4,
    "observation_points": 5,
    "radiation_area": 22.5,
    "radon_points": 0,
    "route_kameral_coefficient": 1.0,  # в эталоне коэффициента нет
    "lab_total_base": 13813,
}
ROAD_INPUT = {
    "fund_materials_count": 2.6,
    "subbase_item": "13",
    "subbase_volume": 7,
    "passability": "satisfactory",
    "recon_length_km": 14.7,
    "observation_points": 11,
    "radiation_area": 0,
    "radon_points": 0,
    "route_field_coefficient": 1.3,
    "route_kameral_coefficient": 1.0,  # в эталоне коэффициента нет (открытый вопрос)
    "lab_total_base": 17740.4,
}


@pytest.fixture(scope="module")
def real_refs(tmp_path_factory):
    ws_karier = openpyxl.load_workbook(ECOLOGY_SOURCE_XLSX, data_only=True)[ECOLOGY_SHEET_NAME]
    ws_road = openpyxl.load_workbook(SOURCE_XLSX, data_only=True)[ECOLOGY_ROAD_SHEET_NAME]
    karier_entries, _ = extract_ecology_price_entries(ws_karier)
    road_entries, _ = extract_ecology_road_price_entries(ws_road)

    db_path = tmp_path_factory.mktemp("refs") / "references.sqlite"
    conflicts = build_database(karier_entries + road_entries, [], db_path)
    assert conflicts == []
    return ReferenceBook(db_path)


def _estimate(data, refs):
    module = EcologyModule()
    return module.build_estimate(module.build_volumes(data, refs), refs)


def _staged_estimate(data, refs):
    """Тот же расчёт через этапы (core/stages.py): ввод раскладывается
    по полям этапов, ведомость подтверждается, смета — от снимка."""
    session = StagedSession(as_staged(EcologyModule()))
    for stage in session.staged.stages:
        session.inputs[stage.key] = {f.name: data[f.name] for f in stage.fields if f.name in data}
    session.confirm("volumes", refs)
    return dict(session.confirm("estimate", refs).lines)


def _sum(estimate, ids):
    cache: dict[str, float] = {}
    return sum(calculate_amount(estimate[i], estimate, cache) for i in ids)


@pytest.mark.parametrize(
    "data, preliminary_total, recon_block, kameral_total, etalon_report",
    [
        (KARIER_INPUT, 280.06, 231.58, 3228.46, 736.7892),
        (ROAD_INPUT, 345.39, 946.167, 3979.558, 908.23908),
    ],
    ids=["karier", "road"],
)
@pytest.mark.parametrize("calculate", [_estimate, _staged_estimate], ids=["direct", "staged"])
def test_matches_etalon(
    real_refs, calculate, data, preliminary_total, recon_block, kameral_total, etalon_report
):
    estimate = calculate(data, real_refs)

    assert _sum(estimate, ["ecology.program"]) == pytest.approx(250, abs=0.005)
    assert _sum(estimate, PRELIMINARY_IDS) == pytest.approx(preliminary_total, abs=0.005)
    assert _sum(estimate, RECON_FIELD_IDS) == pytest.approx(recon_block, abs=0.005)
    assert _sum(estimate, KAMERAL_IDS) == pytest.approx(kameral_total, abs=0.005)

    assert _sum(estimate, ["ecology.report"]) == pytest.approx(etalon_report, abs=0.005)


def test_karier_kameral_lines_line_by_line(real_refs):
    estimate = _estimate(KARIER_INPUT, real_refs)
    expected = {
        "ecology.kameral.recon": 81.4,
        "ecology.kameral.soil_recon": 6.76,
        "ecology.kameral.route": 7.2,
        "ecology.kameral.observation_points": 37.5,
        "ecology.kameral.radiation": 333.0,
        "ecology.kameral.radon": 0.0,
        "ecology.kameral.lab": 2762.6,
    }
    for line_id, amount in expected.items():
        assert _sum(estimate, [line_id]) == pytest.approx(amount, abs=0.005), line_id


def test_road_kameral_lines_line_by_line(real_refs):
    estimate = _estimate(ROAD_INPUT, real_refs)
    expected = {
        "ecology.kameral.recon": 299.145,
        "ecology.kameral.soil_recon": 24.843,
        "ecology.kameral.route": 24.99,
        "ecology.kameral.observation_points": 82.5,
        "ecology.kameral.radiation": 0.0,
        "ecology.kameral.radon": 0.0,
        "ecology.kameral.lab": 3548.08,
    }
    for line_id, amount in expected.items():
        assert _sum(estimate, [line_id]) == pytest.approx(amount, abs=0.005), line_id


def test_all_new_positions_get_real_price_from_reference_book(real_refs):
    for data in (KARIER_INPUT, ROAD_INPUT):
        estimate = _estimate(data, real_refs)
        for line_id in [*PRELIMINARY_IDS, *RECON_FIELD_IDS, *KAMERAL_IDS, "ecology.report"]:
            assert "не найдена" not in estimate[line_id].basis, line_id


# --- Итог листа целиком (Э6г + Э5б, 30.10.2026) ---------------------------
#
# Ввод — как оператор вводит объект по листу: опробование и лабораторные
# анализы строками групп, ставки начислений — данные объекта (как в листе:
# внешний 39,2 %, оргликвидация 6 %, районные 0,6 / 0,25), расстояние —
# по пункту т.4 листа (Карьер п.4 «от 10 до 15 км», Автодорога п.5 «от 20
# до 25 км»). Итог «по СБЦ» — №2.5_ИЭИ!H93 / №1.4_ИЭИ!H95 (работы по
# прайсам после индекса — другой этап карты, Э7).
#
# Особенности листов, повторённые вводом (не правилом модуля):
# - бактериологические + паразитологические пробы (т.60 §10, по 10 шт) —
#   один вид «санитарно-эпидемиологические», 20 шт;
# - Автодорога: у проб почв и фоновых в листе ×5 «количество точечных
#   проб» (D36, D41) — введено числом точечных проб (7 × 5 = 35, 3 × 5 = 15);
#   строка «пробы грунта на химический анализ» (строка 37) в листе без
#   формулы суммы (H37 пусто) — в итог листа не входит, поэтому не введена;
# - тяжёлые металлы в донных отложениях — в обоих листах без ×7 (H59,
#   H61) — так и в модуле (вопрос пользователю).
KARIER_FULL_INPUT = {
    **{k: v for k, v in KARIER_INPUT.items() if k != "lab_total_base"},
    "physical_factors_points": 0,
    "data_acquisition_count": 0,
    "sampling": [
        {"sample_type": "soil_chemical", "sample_count": 5},
        {"sample_type": "subsoil_chemical", "sample_count": 15},
        {"sample_type": "soil_radionuclides", "sample_count": 20},
        {"sample_type": "subsoil_biotest", "sample_count": 3},
        {"sample_type": "soil_sanitary", "sample_count": 20},
        {"sample_type": "surface_water_chemical", "sample_count": 2},
        {"sample_type": "bottom_sediments_chemical", "sample_count": 2},
        {"sample_type": "groundwater_chemical", "sample_count": 3},
    ],
    "lab_analyses": [
        {"analysis_type": "heavy_metals_soil", "analysis_count": 20},
        {"analysis_type": "heavy_metals_sediments", "analysis_count": 2},
        {"analysis_type": "pah_soil", "analysis_count": 20},
        {"analysis_type": "pah_sediments", "analysis_count": 2},
        {"analysis_type": "oil_soil", "analysis_count": 20},
        {"analysis_type": "oil_sediments", "analysis_count": 2},
        {"analysis_type": "granulometric", "analysis_count": 20},
        {"analysis_type": "gamma_spectrometry", "analysis_count": 20},
        {"analysis_type": "surface_water", "analysis_count": 2},
        {"analysis_type": "groundwater", "analysis_count": 3},
    ],
}
ROAD_FULL_INPUT = {
    **{k: v for k, v in ROAD_INPUT.items() if k != "lab_total_base"},
    "physical_factors_points": 0,
    "data_acquisition_count": 0,
    "sampling": [
        {"sample_type": "soil_chemical", "sample_count": 35},
        {"sample_type": "soil_agrochemical", "sample_count": 14},
        {"sample_type": "background_samples", "sample_count": 15},
        {"sample_type": "soil_radionuclides", "sample_count": 21},
        {"sample_type": "soil_sanitary", "sample_count": 20},
        {"sample_type": "surface_water_chemical", "sample_count": 7},
        {"sample_type": "bottom_sediments_chemical", "sample_count": 7},
        {"sample_type": "groundwater_chemical", "sample_count": 3},
    ],
    "lab_analyses": [
        {"analysis_type": "heavy_metals_soil", "analysis_count": 21},
        {"analysis_type": "heavy_metals_sediments", "analysis_count": 7},
        {"analysis_type": "pah_soil", "analysis_count": 21},
        {"analysis_type": "pah_sediments", "analysis_count": 7},
        {"analysis_type": "oil_soil", "analysis_count": 21},
        {"analysis_type": "oil_sediments", "analysis_count": 7},
        {"analysis_type": "water_extract", "analysis_count": 14},
        {"analysis_type": "agrochemical", "analysis_count": 14},
        {"analysis_type": "granulometric", "analysis_count": 35},
        {"analysis_type": "gamma_spectrometry", "analysis_count": 21},
        {"analysis_type": "surface_water", "analysis_count": 7},
        {"analysis_type": "groundwater", "analysis_count": 3},
    ],
}
SHEET_RATES = dict(external_transport_rate=0.392, org_liquidation_rate=0.06,
                   regional_field_rate=0.6, regional_office_rate=0.25)
INDEX = 81.95

# (ввод, расстояние, ячейки листа: лабораторные, полевые с вн. тр. и
#  начислениями (база районного к полевым), база районного к камеральным,
#  итог по СБЦ × индекс)
FULL_CASES = {
    "karier": (KARIER_FULL_INPUT, 12, 13813, 4041.781, 18058.3092, 2379806.373395),
    "road": (ROAD_FULL_INPUT, 22, 17740.4, 4100.8808125, 22973.58708, 2891064.3186425),
}


@pytest.fixture(scope="module")
def full_refs():
    from core.references import DEFAULT_DB_PATH

    if not DEFAULT_DB_PATH.exists():
        pytest.skip("нет data/references.sqlite")
    return ReferenceBook(DEFAULT_DB_PATH)


def _staged_full(data, distance, refs):
    from core.object_data import CalculationContext, ObjectData

    context = CalculationContext(refs, ObjectData(distance_km=distance, **SHEET_RATES))
    return _staged_estimate(data, context)


def _amounts(estimate):
    cache: dict[str, float] = {}
    return {i: calculate_amount(line, estimate, cache) for i, line in estimate.items()}


@pytest.mark.parametrize("case", list(FULL_CASES))
def test_full_sheet_total_from_operator_input(full_refs, case):
    """Э6г: итог листа по СБЦ (H93 / H95) из ввода оператора — до копейки
    (критерий карты ± 0,1 %)."""
    data, distance, lab_total, _field, _office, sheet_total = FULL_CASES[case]
    estimate = _staged_full(data, distance, full_refs)
    a = _amounts(estimate)

    lab = sum(v for i, v in a.items() if i.startswith("ecology.lab."))
    assert lab == pytest.approx(lab_total, abs=0.005)
    assert a["ecology.kameral.lab"] == pytest.approx(0.2 * lab_total, abs=0.005)
    assert sum(a.values()) * INDEX == pytest.approx(sheet_total, abs=0.01)
    assert sum(a.values()) * INDEX == pytest.approx(sheet_total, rel=0.001)


@pytest.mark.parametrize("case", list(FULL_CASES))
def test_regional_office_base_includes_lab_lines_in_live_estimate(full_refs, case):
    """Э5б: районный к камеральным — от (предполевые + лабораторные +
    камеральные + техотчёт), как №2.5_ИЭИ!H86 / №1.4_ИЭИ!H88, на настоящих
    строках модуля (не на строках-подменах)."""
    from core.accruals import REGIONAL_FIELD, REGIONAL_OFFICE, line_id
    from modules.ecology.module import ACCRUALS

    data, distance, _lab, field_base, office_base, _total = FULL_CASES[case]
    estimate = _staged_full(data, distance, full_refs)
    a = _amounts(estimate)

    office = estimate[line_id(ACCRUALS, REGIONAL_OFFICE)]
    assert any(i.startswith("ecology.lab.") for i in office.base_ids)
    assert sum(a[i] for i in office.base_ids) == pytest.approx(office_base, abs=0.005)
    assert a[line_id(ACCRUALS, REGIONAL_OFFICE)] == pytest.approx(0.25 * office_base, abs=0.005)

    field = estimate[line_id(ACCRUALS, REGIONAL_FIELD)]
    assert not any(i.startswith("ecology.lab.") for i in field.base_ids)
    assert sum(a[i] for i in field.base_ids) == pytest.approx(field_base, abs=0.005)


def test_full_input_direct_call_gives_same_lab_lines(full_refs):
    """Прямой вызов build_volumes → build_estimate (без этапов) — те же
    строки лабораторных, что через этапы."""
    data = KARIER_FULL_INPUT
    direct = _amounts(_estimate(data, full_refs))
    staged = _amounts(_staged_estimate(data, full_refs))
    lab_ids = [i for i in direct if i.startswith("ecology.lab.")]
    assert len(lab_ids) == 10
    for i in lab_ids + ["ecology.kameral.lab", "ecology.report"]:
        assert direct[i] == pytest.approx(staged[i], abs=1e-9), i


# --- Ревью модуля экологии (30.10.2026): находки 1–3 -----------------------
#
# 1. Тяжёлые металлы в донных отложениях — в листах без ×7 (H59 / H61), по
#    смыслу расценки — 7 элементов (В-38). По умолчанию как в листе +
#    предупреждение; множитель — параметр этапа сметы.
# 2. Количества лабораторных анализов в листе — формулы от отбора проб;
#    модуль их не подставляет, а предупреждает, если анализ не введён.
# 3. Ошибка листа в агрохимии (H67 / H65 берут количество водной вытяжки).

def _road_formulas():
    return openpyxl.load_workbook(SOURCE_XLSX, data_only=False)[ECOLOGY_ROAD_SHEET_NAME]


def _karier_formulas():
    return openpyxl.load_workbook(ECOLOGY_SOURCE_XLSX, data_only=False)[ECOLOGY_SHEET_NAME]


def test_sediments_heavy_metals_sheet_has_no_x7():
    """Факт листов, на котором стоит находка 1: у почв ×7, у донных — нет."""
    for ws, soil, sediments in ((_karier_formulas(), 58, 59), (_road_formulas(), 60, 61)):
        assert ws[f"H{soil}"].value == f"=PRODUCT(D{soil},E{soil},7)"
        assert ws[f"H{sediments}"].value == f"=PRODUCT(D{sediments},E{sediments},)"
        assert "x\",7)" in ws[f"G{sediments}"].value  # колонка расчёта пишет ×7


@pytest.mark.parametrize("case", list(FULL_CASES))
def test_sediments_default_as_sheet_with_warning(full_refs, case):
    from modules.ecology.module import EcologyModule as M

    data = FULL_CASES[case][0]
    estimate = _staged_full(data, FULL_CASES[case][1], full_refs)
    line = estimate["ecology.lab.heavy_metals_sediments"]
    assert "k_elements" not in line.coefs
    assert _amounts(estimate)[line.id] == pytest.approx(line.qty * 51.2)
    warnings = M().check(estimate)
    assert any("донных отложениях" in w and "×7 не применён" in w and "уточните" in w
               for w in warnings)


def test_sediments_multiplier_7_by_meaning(full_refs):
    """Множитель 7 — только когда оператор его задал; меняется одна строка
    (и зависящие от неё доли), предупреждение — о расхождении с листом."""
    from modules.ecology.module import VK_SEDIMENT_ELEMENTS, EcologyModule as M

    data, distance = FULL_CASES["karier"][0], FULL_CASES["karier"][1]
    base = _amounts(_staged_full(data, distance, full_refs))
    estimate = _staged_full({**data, VK_SEDIMENT_ELEMENTS: 7}, distance, full_refs)
    a = _amounts(estimate)
    sid = "ecology.lab.heavy_metals_sediments"
    assert a[sid] == pytest.approx(2 * 51.2 * 7)
    assert a[sid] - base[sid] == pytest.approx(2 * 51.2 * 6)
    for i in a:
        if i.startswith("ecology.lab.") and i != sid:
            assert a[i] == base[i], i
    warnings = M().check(estimate)
    assert any("множитель числа элементов 7" in w for w in warnings)
    assert not any("×7 не применён (вероятно" in w for w in warnings)


def test_lab_sample_links_match_sheet_formulas():
    """Связи «отбор → анализ» модуля — ровно формулы колонки D листа
    Автодороги (D60–D71); у Карьера те же на 2 строки выше, кроме D64 и D69
    (вписанные числа)."""
    from modules.ecology.module import LAB_SAMPLE_LINKS

    road, karier = _road_formulas(), _karier_formulas()
    for _analysis, _samples, cells in LAB_SAMPLE_LINKS:
        target, formula = cells.split(" = ")
        assert road[target].value == "=" + formula.replace(" ", ""), cells
        row = int(target[1:]) - 2
        if row in (64, 69):
            continue
        shifted = re.sub(r"D(\d+)", lambda m: f"D{int(m.group(1)) - 2}", formula.replace(" ", ""))
        assert karier[f"D{row}"].value == "=" + shifted, cells
    assert karier["D64"].value == 0 and karier["D69"].value == 3


@pytest.mark.parametrize("case", list(FULL_CASES))
def test_no_lab_link_warnings_on_full_sheet_input(full_refs, case):
    """На вводе по листу все анализы введены — поштучных предупреждений нет."""
    from modules.ecology.module import EcologyModule as M

    data, distance = FULL_CASES[case][0], FULL_CASES[case][1]
    assert not any("Отобраны пробы" in w for w in M().check(_staged_full(data, distance, full_refs)))
    session = StagedSession(as_staged(M()))
    volumes_stage = session.staged.stages[0]
    inputs = {f.name: data[f.name] for f in volumes_stage.fields if f.name in data}
    assert not any("Отобраны пробы" in w
                   for w in volumes_stage.context_warnings(inputs, None, full_refs))


def test_lab_link_warning_when_analysis_missing(full_refs):
    """Убран анализ ПАУ в почвах — предупреждение и на ведомости, и в смете;
    смета при этом не меняется сама (автоподстановки нет)."""
    from modules.ecology.module import EcologyModule as M

    data = {**KARIER_FULL_INPUT, "lab_analyses": [
        r for r in KARIER_FULL_INPUT["lab_analyses"] if r["analysis_type"] != "pah_soil"
    ]}
    estimate = _staged_full(data, 12, full_refs)
    assert "ecology.lab.pah_soil" not in estimate
    for warnings in (
        M().check(estimate),
        StagedSession(as_staged(M())).staged.stages[0].context_warnings(
            {k: v for k, v in data.items()}, None, full_refs),
    ):
        hits = [w for w in warnings if "Отобраны пробы" in w]
        assert len(hits) == 1
        assert "Бенз(а)пирен (ПАУ) в почвах" in hits[0]
        assert "Пробы почв на химические загрязнители" in hits[0]
        assert "(5 шт)" in hits[0] and "(15 шт)" in hits[0]
        assert "D62 = D35 + D37" in hits[0]


def test_agrochemical_sheet_error_documented_module_uses_own_qty(full_refs):
    """Находка 3: в листе агрохимия берёт количество из строки водной
    вытяжки (H67 = …D66…, Карьер H65 = …D64…); модуль — своё количество."""
    assert _road_formulas()["H67"].value == "=ROUND(PRODUCT(D66,E67,),0)"
    assert _karier_formulas()["H65"].value == "=ROUND(PRODUCT(D64,E65,),0)"
    data = {**ROAD_FULL_INPUT, "lab_analyses": [
        *(r for r in ROAD_FULL_INPUT["lab_analyses"] if r["analysis_type"] != "water_extract"),
        {"analysis_type": "water_extract", "analysis_count": 3},
    ]}
    a = _amounts(_staged_full(data, 22, full_refs))
    assert a["ecology.lab.agrochemical"] == round(14 * 41.9)
