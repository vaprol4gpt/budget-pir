"""Геофизика (Э6в, 30.10.2026): модуль с объёмами и параметрами листа
«№1.2.2_ИГФИ» (reference_data/Автодорога_эталон.xlsx)
даёт итог листа K34 = 4 417 072,88 — построчно и по итогу.

Объёмы листа: ВЭЗ 80 (E17), МПВ 400 на каждый из двух типов волн (E18, E19),
программа 1 (E22), обработка ЭР и техотчёт — есть. Параметры сметы:
коэффициенты 1,15 / 1,1 к ВЭЗ (I17, H17), 1,2 к МПВ (H18), 1,15 × 1,15 к
камеральным (G24, H24), оргликвидация по формуле листа с К = 1,2 (G31).
Ставки объекта: внешний транспорт 6,5 % (G29), районный к полевым 0,66 (G32).
Цены — из справочника `data/references.sqlite` (парсер листа)."""

from pathlib import Path

import openpyxl
import pytest

from core.models import calculate_amount
from core.object_data import CalculationContext, ObjectData
from core.references import DEFAULT_DB_PATH, ReferenceBook
from core.stages import StagedSession, as_staged
from modules.geophysics.module import (
    F_MPV_WAVES,
    F_OFFICE_VEZ,
    F_PROGRAM,
    F_REPORT,
    ORG_FIXED_ID,
    ORG_MODE_SHEET,
    P_K_MPV_252_6,
    P_K_OFFICE_MPV_NOTE2,
    P_K_OFFICE_P22,
    P_K_VEZ_265_2,
    P_K_VEZ_265_9,
    P_ORG_MODE,
    P_ORG_SHEET_K,
    WAVES_BOTH,
    YES,
    GeophysicsModule,
)

ROAD = Path(__file__).resolve().parent.parent / "reference_data" / "Автодорога_эталон.xlsx"
pytestmark = [
    pytest.mark.skipif(not ROAD.exists(), reason="нет эталонной книги в reference_data/"),
    pytest.mark.skipif(not DEFAULT_DB_PATH.exists(), reason="нет data/references.sqlite"),
]

VOLUMES = {"vez_count": 80, "mpv_count": 400, F_MPV_WAVES: WAVES_BOTH, F_PROGRAM: 1,
           F_OFFICE_VEZ: YES, F_REPORT: YES}
PARAMS = {P_K_VEZ_265_2: 1.15, P_K_VEZ_265_9: 1.1, P_K_MPV_252_6: 1.2, P_K_OFFICE_P22: 1.15,
          P_K_OFFICE_MPV_NOTE2: 1.15, P_ORG_MODE: ORG_MODE_SHEET, P_ORG_SHEET_K: 1.2}


@pytest.fixture(scope="module")
def sheet():
    ws = openpyxl.load_workbook(ROAD, data_only=True)["№1.2.2_ИГФИ"]
    return {f"{col}{row}": ws[f"{col}{row}"].value for col in "EFGHK" for row in range(17, 35)}


@pytest.fixture(scope="module")
def refs():
    data = ObjectData(external_transport_rate=0.065, regional_field_rate=0.66)
    return CalculationContext(ReferenceBook(DEFAULT_DB_PATH), data)


def _staged(refs):
    session = StagedSession(as_staged(GeophysicsModule()))
    session.inputs["volumes"] = dict(VOLUMES)
    session.inputs["estimate"] = dict(PARAMS)
    session.confirm("volumes", refs)
    return dict(session.confirm("estimate", refs).lines)


def _amounts(estimate):
    cache: dict[str, float] = {}
    return {i: calculate_amount(line, estimate, cache) for i, line in estimate.items()}


def test_lines_match_sheet_cells(refs, sheet):
    a = _amounts(_staged(refs))
    assert a["geophysics.vez"] == pytest.approx(sheet["K17"], abs=1e-6)
    assert a["geophysics.mpv"] == pytest.approx(sheet["K18"], abs=1e-6)
    assert a["geophysics.mpv_s"] == pytest.approx(sheet["K19"], abs=1e-6)
    assert a["geophysics.program"] == pytest.approx(sheet["K22"], abs=1e-6)
    assert a["geophysics.office_vez"] == pytest.approx(sheet["K23"], abs=1e-6)
    assert a["geophysics.kameralka_mpv"] == pytest.approx(sheet["K24"], abs=1e-6)
    assert a["geophysics.kameralka_mpv_s"] == pytest.approx(sheet["K25"], abs=1e-6)
    assert a["geophysics.report"] + a["geophysics.report_fixed"] == pytest.approx(sheet["K26"], abs=1e-6)
    assert a["geophysics.transport"] == pytest.approx(sheet["K28"], abs=1e-6)
    assert a["geophysics.accrual.external_transport"] == pytest.approx(sheet["K29"], abs=1e-6)
    assert a["geophysics.accrual.metrology"] == pytest.approx(sheet["K30"], abs=1e-6)
    assert a["geophysics.accrual.org_liquidation"] + a[ORG_FIXED_ID] == pytest.approx(sheet["K31"], abs=1e-6)
    assert a["geophysics.accrual.regional_field"] == pytest.approx(sheet["K32"], abs=1e-6)


def test_total_matches_sheet_within_0_1_percent(refs, sheet):
    """Критерий Э6в: итог листа ± 0,1 % (фактически — до копейки)."""
    base = sum(_amounts(_staged(refs)).values())
    assert base == pytest.approx(sheet["K33"], abs=1e-6)
    total = round(base * sheet["G34"], 2)
    assert sheet["K34"] == pytest.approx(4_417_072.88, abs=0.005)
    assert total == pytest.approx(sheet["K34"], rel=1e-3)
    assert total == pytest.approx(sheet["K34"], abs=0.005)


def test_direct_call_gives_same_numbers_as_stages(refs):
    module = GeophysicsModule()
    direct = module.estimate_with_accruals(module.build_volumes(VOLUMES, refs), refs, PARAMS)
    assert _amounts(direct) == pytest.approx(_amounts(_staged(refs)))


def test_defaults_keep_old_numbers(refs):
    """Без новых полей ведомости и параметров — смета как до Э6в
    (контрольная цифра сквозного прогона не меняется)."""
    module = GeophysicsModule()
    old = module.estimate_with_accruals(module.build_volumes({"vez_count": 80, "mpv_count": 400}, refs),
                                        refs, {})
    a = _amounts(old)
    assert set(a) >= {"geophysics.vez", "geophysics.mpv", "geophysics.kameralka_mpv", "geophysics.transport"}
    assert not {"geophysics.mpv_s", "geophysics.program", "geophysics.office_vez",
                "geophysics.report", ORG_FIXED_ID} & set(a)
    assert a["geophysics.vez"] == pytest.approx(80 * 9 * 1.2)
    assert a["geophysics.mpv"] == pytest.approx(400 * 14)
    assert a["geophysics.kameralka_mpv"] == pytest.approx(400 * 9)
