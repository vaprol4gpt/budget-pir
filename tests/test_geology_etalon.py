"""Геология против листа-эталона «1.2.1_ИГИ» целиком (Э6а, 30.10.2026).

Ввод — только то, что оператор вводит в интерфейсе: объёмы этапа ведомости
(поля формы + правки строк ведомости там, где автопрофиль не совпал с
объектом), коэффициенты этапа сметы, ставки начислений данных объекта. Все
числа ввода и ожидаемые итоги — ячейки листа (reference_data/, согласованный
эталон). Критерий карты (план работ, Э6а): итог листа ± 0,1 %.

Остаток расхождения (−0,04 %) объяснён: ячейка H143 листа — «=11*8.1» = 89,1
(камеральная обработка испытаний вращательным срезом т.83 п.4 с объёмом 11,
вписанным в формулу при D143 = 0) — позиции в модуле нет; плюс округления
листа (ROUND каждой камеральной строки и подытогов, модуль — без них).
"""

from pathlib import Path

import openpyxl
import pytest

from core.accruals import UNFAVORABLE, line_id
from core.models import calculate_amount
from core.object_data import CalculationContext, ObjectData
from core.overrides import Edit
from core.references import DEFAULT_DB_PATH, ReferenceBook
from core.stages import StagedSession, as_staged
from modules.geology.module import ACCRUALS, GeologyModule

ETALON = Path(__file__).resolve().parent.parent / "reference_data" / "Автодорога_эталон.xlsx"
SHEET = "1.2.1_ИГИ"

pytestmark = [
    pytest.mark.skipif(not ETALON.exists(), reason="нет эталона в reference_data/"),
    pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite"),
]


@pytest.fixture(scope="module")
def ws():
    return openpyxl.load_workbook(ETALON, data_only=True)[SHEET]


def sheet_input(ws):
    """Ввод оператора по листу: (ввод ведомости, правки объёмов строк ведомости
    {id строки: объём}, ввод этапа сметы, данные объекта со ставками листа).
    Общий для этого теста и демо-базы (tools/make_demo.py)."""
    d = lambda addr: ws[addr].value  # noqa: E731
    volume_input = {
        "drilling_by_category": [
            {"soil_category": c, "drilling_length": d(f"D{row}")}
            for c, row in (("IV", 29), ("V", 30), ("VI", 31), ("VII", 32), ("VIII", 33))
        ],
        "drilling_count": d("D17"),
        "rekognoscirovka_km": d("D19"),
        "kreplenie_length": d("D34"),
        "gidronablyudeniya_length": d("D35"),
        "shtamp_count": d("D50"),
        "baza_months": d("D70"),
        "lab_glinistye_count": d("D87"),
        "lab_peschanye_count": d("D88"),
        "lab_opt_vlazhnost_count": d("D123"),
        "lab_korroziya_stal_count": d("D108"),
        "lab_korroziya_beton_count": d("D109"),
        "sbor_materialov_count": d("D129"),
    }
    # строки ведомости, где автопрофиль (классы Б/В, лабораторный) не совпал
    # с объектом, — правка оператора «как в листе»
    sheet_volumes = {
        "termometriya": d("D52"), "monolity_dispersnye": d("D40"), "monolity_skalnye": d("D41"),
        "proby_vody": d("D43"), "lab_summarnaya_vlazhnost": d("D92"), "lab_analiz_vody": d("D97"),
        "lab_skalnye_fizmeh": d("D122"),
    }
    estimate_input = {
        "k_programma_rabot": d("E128"),   # 1,25 — т.81 прим.1
        "k_monolity_skalnye": d("E42"),   # 0,7 — прим. к т.57
        "k_rekognoscirovka": d("E20"),    # 1,2 — т.9 прим.3
    }
    obj = ObjectData(
        unfavorable_period=True, external_transport_rate=d("E152"),
        org_liquidation_rate=d("E153"), org_liquidation_k=d("E154"),
        regional_field_rate=d("E156"), regional_office_rate=d("E157"),
    )
    return volume_input, sheet_volumes, estimate_input, obj


def run_sheet(ws):
    """Модуль на объёмах листа через оба этапа; (строки сметы, суммы)."""
    volume_input, sheet_volumes, estimate_input, obj = sheet_input(ws)
    refs = CalculationContext(ReferenceBook(), obj)
    session = StagedSession(as_staged(GeologyModule()))
    session.inputs["volumes"] = volume_input
    auto = session.compute("volumes", refs).effective
    session.apply_draft("volumes", volume_input, [
        Edit(key, "qty", auto[key].qty, qty, "как в листе 1.2.1_ИГИ") for key, qty in sheet_volumes.items()
    ])
    session.confirm("volumes", refs)
    session.inputs["estimate"] = estimate_input
    lines = dict(session.confirm("estimate", refs).lines)
    cache: dict[str, float] = {}
    return lines, {i: calculate_amount(line, lines, cache) for i, line in lines.items()}


def test_geology_total_matches_sheet_within_0_1_percent(ws):
    _lines, amounts = run_sheet(ws)
    total = sum(amounts.values())
    assert ws["H158"].value == 355558
    assert total == pytest.approx(ws["H158"].value, rel=0.001)
    assert total * 81.95 == pytest.approx(ws["H160"].value, rel=0.001)  # 29 137 978


@pytest.mark.parametrize("line, cell", [
    ("geology.geodesy_privyazka", "H17"),
    ("geology.rekognoscirovka", "H19"),
    ("geology.kreplenie_skvazhin", "H34"),
    ("geology.gidronablyudeniya", "H35"),
    ("geology.monolity_dispersnye", "H40"),
    ("geology.monolity_skalnye", "H41"),
    ("geology.proby_vody", "H43"),
    ("geology.shtamp", "H50"),
    ("geology.termometriya", "H52"),
    ("geology.baza_ekspedicii", "H70"),
    ("geology.lab_glinistye_fiz", "H87"),
    ("geology.lab_peschanye_fiz", "H88"),
    ("geology.lab_summarnaya_vlazhnost", "H92"),
    ("geology.lab_analiz_vody", "H97"),
    ("geology.lab_korroziya_stal", "H108"),
    ("geology.lab_korroziya_beton", "H109"),
    ("geology.lab_skalnye_fizmeh", "H122"),
    ("geology.lab_opt_vlazhnost", "H123"),
    ("geology.programma_rabot", "H127"),
    ("geology.sbor_materialov", "H129"),
    ("geology.kameral_rekognoscirovki", "H131"),
    ("geology.kameral_bureniya", "H132"),
    ("geology.kameral_bureniya_gidro", "H134"),
    ("geology.kameral_lab_glinistye", "H136"),
    ("geology.kameral_lab_peschanye", "H137"),
    ("geology.kameralnaya_himsostav_vody", "H138"),
    ("geology.kameral_korroziya", "H139"),
    ("geology.transport", "H151"),
    ("geology.accrual.external_transport", "H152"),
    ("geology.accrual.org_liquidation", "H153"),
])
def test_geology_line_matches_sheet_cell(ws, line, cell):
    # лист округляет камеральные строки и подытоги до рубля (ROUND), модуль —
    # нет; у начислений от округлённых баз — до 1 руб.
    _lines, amounts = run_sheet(ws)
    assert amounts[line] == pytest.approx(ws[cell].value, abs=1.5)


def test_rock_monoliths_have_price_and_coefficient(ws):
    lines, amounts = run_sheet(ws)
    skalnye = lines["geology.monolity_skalnye"]
    assert skalnye.price == pytest.approx(30.6)  # E41, ключ т.57 п.2 больше не исключён
    assert skalnye.coefs == {"k": 0.7}
    assert "прим. к т.57" in skalnye.basis
    assert amounts["geology.monolity_skalnye"] == pytest.approx(1071)


def test_internal_transport_base_is_all_field_work_with_unfavorable(ws):
    """D151 = H82 = все полевые п.1–11 × 1,2 (неблагоприятный период)."""
    lines, amounts = run_sheet(ws)
    transport = lines["geology.transport"]
    assert line_id(ACCRUALS, UNFAVORABLE) in transport.base_ids
    base = sum(amounts[i] for i in transport.base_ids)
    assert base == pytest.approx(ws["D151"].value, abs=1)  # 111 960


def test_tehotchet_base_is_all_office_lines(ws):
    """D148 = H146 = все камеральные (16 950; у модуля на 89,1 меньше — H143)."""
    lines, amounts = run_sheet(ws)
    base = sum(amounts[i] for i in lines["geology.tehotchet"].base_ids)
    assert base == pytest.approx(ws["D148"].value - ws["H143"].value, abs=1)
