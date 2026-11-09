"""Своя строка оператора (Э7 карты, Методика п. 6): работа вне сборника по
прайсу / калькуляции / аналогу с основанием и местом в смете.

Критерий этапа — лист «№1.4_ИЭИ» (Автодорога): итог листа
H114 = 3 311 107,32 = H95 (2 891 064,32, смета по СБЦ-99 × 81,95) + блок
«Работы, не учтённые СБЦ-99» (строки 97–113, прайсы в текущих ценах, после
индекса, 420 043,00). Проверяется двумя способами:

1. весь лист: базы раздела — из ячеек листа (как tests/test_accruals.py),
   начисления — программой, блок прайсов — своими строками «после индекса»
   (данные строк — из ячеек листа) → итог с индексацией = H114 до копейки;
2. настоящий модуль экологии на объёмах эталона (tests/test_ecology_etalons.py)
   через этапы: свои строки прибавляют к итогу ровно блок прайсов листа и не
   индексируются (лабораторного блока в модуле нет — итог раздела по СБЦ с
   листом не совпадает, поэтому сверяется добавка, как разрешает критерий).

Плюс: хранение в версии и черновике, открытие, выгрузка в Excel, сводный
бюджет, «до индекса» индексируется и не входит в базы начислений."""

import io
from pathlib import Path

import openpyxl
import pytest

from core.accruals import apply_accruals
from core.aggregator import BudgetAggregator, ModuleBudget
from core.export_excel import PLACEMENT_HEADER, TOTAL_AFTER_INDEX, export_to_excel
from core.models import (
    PLACEMENT_AFTER_INDEX,
    PLACEMENT_BEFORE_INDEX,
    EstimateLine,
    calculate_amount,
)
from core.object_data import CalculationContext, ObjectData
from core.overrides import BASIS_CALCULATION, BASIS_PRICE_LIST, Edit, OverrideLog
from core.persistence import SmetaStore, _edits_to_json
from core.pricing import calculate_total_with_vat
from core.stages import StagedSession, as_staged, fingerprint, lines_to_json
from modules.ecology.module import ACCRUALS as ECOLOGY, EcologyModule
from tests.test_ecology_etalons import ROAD_INPUT, real_refs  # noqa: F401 (фикстура)

ROAD = Path(__file__).resolve().parent.parent / "reference_data" / "Автодорога_эталон.xlsx"
SHEET = "№1.4_ИЭИ"
INDEX = 81.95  # лист «№1.4_ИЭИ», B95/H95: индекс III кв. 2026
VAT = 0.22


class PricingRefs:
    """Справочник для итога: индекс листа и ставка НДС 2026 г.; цены —
    из обёрнутого справочника (если он есть)."""

    def __init__(self, inner=None):
        self.inner = inner

    def get_price(self, *args, **kwargs):
        return self.inner.get_price(*args, **kwargs)

    def get_index(self, work_type, date):
        return INDEX

    def get_vat_rate(self, date):
        return VAT

    def __getattr__(self, name):
        # индекс и НДС — только свои (выше), не из обёрнутого справочника
        if name.startswith(("get_index", "get_last_index", "get_vat")) or self.inner is None:
            raise AttributeError(name)
        return getattr(self.inner, name)


@pytest.fixture(scope="module")
def sheet():
    return openpyxl.load_workbook(ROAD, data_only=True)[SHEET]


def price_block_lines(ws):
    """Строки блока «Работы, не учтённые СБЦ-99» (97–113) как данные своих
    строк: наименование, единица, количество, цена — из ячеек B–E; основание —
    колонка F, где она пустая — ссылка на строку листа."""
    lines = []
    for row in range(97, 114):
        qty, price = ws[f"D{row}"].value, ws[f"E{row}"].value
        if qty is None or price is None:
            continue  # строка 102 — заголовок группы без чисел
        basis = ws[f"F{row}"].value or f"лист «{SHEET}», строка {row}"
        lines.append({
            "name": ws[f"B{row}"].value, "unit": ws[f"C{row}"].value or "шт.",
            "qty": qty, "price": price, "basis_kind": BASIS_PRICE_LIST,
            "basis": basis, "placement": PLACEMENT_AFTER_INDEX,
        })
    return lines


def fixed(line_id, amount):
    return EstimateLine(line_id, line_id, "руб.", 1, amount, {}, "A", [], "лист эталона")


class NoRefs:
    def get_price(self, *args):
        raise KeyError(args)


def test_block_of_price_lines_matches_sheet(sheet):
    lines = price_block_lines(sheet)
    assert len(lines) == 16
    assert sum(d["qty"] * d["price"] for d in lines) == pytest.approx(
        sheet["H114"].value - sheet["H95"].value, abs=0.005
    )  # 420 043,00


def test_whole_sheet_1_4_with_price_block_after_index(sheet):
    """Весь лист «№1.4_ИЭИ»: H114 = 3 311 107,32 до копейки."""
    v = {c: sheet[c].value for c in ("H18", "H51", "H53", "H72", "H85", "H87", "H95", "H114")}
    estimate = {
        "ecology.program": fixed("ecology.program", v["H18"]),
        "ecology.recon": fixed("ecology.recon", v["H51"]),
        "ecology.transport": fixed("ecology.transport", v["H53"]),
        "ecology.kameral.lab_total": fixed("ecology.kameral.lab_total", v["H72"]),
        "ecology.kameral.recon": fixed("ecology.kameral.recon", v["H85"]),
        "ecology.report": fixed("ecology.report", v["H87"]),
    }
    data = ObjectData(external_transport_rate=0.392, org_liquidation_rate=0.06,
                      regional_field_rate=0.6, regional_office_rate=0.25)
    estimate = apply_accruals(estimate, ECOLOGY, CalculationContext(NoRefs(), data))
    log = OverrideLog()
    for line in price_block_lines(sheet):
        log.add_custom_line(line)
    effective = log.effective_estimate(estimate)

    totals = calculate_total_with_vat(effective, PricingRefs(), "ecology", "2026-09-30")

    assert totals.base_total * INDEX == pytest.approx(v["H95"], abs=0.005)
    assert totals.after_index_total == pytest.approx(420043.0, abs=1e-9)
    assert totals.indexed_total == pytest.approx(v["H114"], abs=0.005)
    assert totals.indexed_total == pytest.approx(3311107.32, abs=0.005)
    # НДС — на всё, включая прайсы
    assert totals.vat_amount == pytest.approx(v["H114"] * VAT, abs=0.01)


def _road_session(refs):
    session = StagedSession(as_staged(EcologyModule()))
    for stage in session.staged.stages:
        session.inputs[stage.key] = {f.name: ROAD_INPUT[f.name] for f in stage.fields
                                     if f.name in ROAD_INPUT}
    session.confirm("volumes", refs)
    return session


def test_real_ecology_module_plus_price_block_adds_exactly_sheet_block(real_refs, sheet):  # noqa: F811
    refs = PricingRefs(real_refs)
    session = _road_session(refs)
    without = calculate_total_with_vat(dict(session.compute("estimate", refs).effective),
                                       refs, "ecology", "2026-09-30")
    for line in price_block_lines(sheet):
        session.overrides["estimate"].add_custom_line(line)
    result = session.confirm("estimate", refs)
    with_block = calculate_total_with_vat(dict(result.lines), refs, "ecology", "2026-09-30")

    assert with_block.base_total == pytest.approx(without.base_total, abs=1e-9)
    assert with_block.indexed_total - without.indexed_total == pytest.approx(
        sheet["H114"].value - sheet["H95"].value, abs=0.005
    )
    assert len([i for i in result.lines if i.startswith("custom.")]) == 16


def test_before_index_line_is_indexed_but_not_in_accrual_base():
    base_line = fixed("ecology.recon", 1000.0)
    estimate = apply_accruals(
        {"ecology.recon": base_line}, ECOLOGY,
        CalculationContext(NoRefs(), ObjectData(regional_field_rate=0.6)),
    )
    log = OverrideLog()
    log.add_custom_line({
        "name": "Калькуляция", "unit": "шт.", "qty": 2, "price": 50,
        "basis_kind": BASIS_CALCULATION, "basis": "калькуляция № 1",
        "placement": PLACEMENT_BEFORE_INDEX,
    })
    effective = log.effective_estimate(estimate)
    cache = {}
    amounts = {i: calculate_amount(line, effective, cache) for i, line in effective.items()}
    assert amounts["ecology.accrual.regional_field"] == pytest.approx(600.0)  # без своей строки
    totals = calculate_total_with_vat(effective, PricingRefs(), "ecology", "2026-09-30")
    assert totals.base_total == pytest.approx(1700.0)
    assert totals.after_index_total == 0.0
    assert totals.indexed_total == pytest.approx(1700.0 * INDEX)


def test_no_custom_lines_keeps_snapshot_json_and_fingerprint_unchanged():
    """Без своих строк снимок и правки пишутся как раньше (placement / custom
    не появляются) — прежние версии и их отпечатки не меняются."""
    line = fixed("a", 10.0)
    assert "placement" not in lines_to_json({"a": line})[0]["data"]
    assert "custom" not in _edits_to_json([Edit("a", "qty", 1.0, 2.0)])
    # отпечаток не зависит от нового поля, пока оно по умолчанию
    import dataclasses
    import hashlib
    import json
    data = dataclasses.asdict(line)
    del data["placement"]
    raw = json.dumps([["a", "EstimateLine", data]], ensure_ascii=False, sort_keys=True, default=str)
    assert fingerprint({"a": line}) == hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def test_custom_lines_saved_with_version_and_draft_and_reopened(real_refs, sheet, tmp_path):  # noqa: F811
    refs = PricingRefs(real_refs)
    store = SmetaStore(tmp_path / "smety.sqlite")
    session = _road_session(refs)
    session.version_id = store.save_version("Автодорога", "ecology", None, session.result("volumes"))
    line_id = session.overrides["estimate"].add_custom_line(price_block_lines(sheet)[0])
    result = session.confirm("estimate", refs)
    version_id = store.save_version("Автодорога", "ecology", session.version_id, result)

    _module, _name, results = store.load_version_chain(version_id)
    reopened = StagedSession(as_staged(EcologyModule()))
    reopened.restore(results)
    assert reopened.overrides["estimate"].custom_lines()[line_id]["name"] == "УГМС"
    restored_line = reopened.result("estimate").lines[line_id]
    assert restored_line.placement == PLACEMENT_AFTER_INDEX
    # пересчёт после открытия даёт тот же снимок — модуль готов к бюджету
    assert fingerprint(reopened.compute("estimate", refs).effective) == result.fingerprint

    # черновик: ещё одна строка, без подтверждения
    reopened.overrides["estimate"].add_custom_line(price_block_lines(sheet)[1])
    draft_id = store.save_draft("Автодорога", "ecology", "estimate", reopened.inputs["estimate"],
                                reopened.overrides["estimate"].history(), version_id)
    draft = store.load_draft(draft_id)
    again = StagedSession(as_staged(EcologyModule()))
    again.restore(store.load_version_chain(version_id)[2])
    again.apply_draft("estimate", draft.inputs, draft.edits)
    assert len(again.overrides["estimate"].custom_lines()) == 2
    assert again.status("estimate") == "draft"  # отличается от снимка — на доработке


def test_custom_line_on_volume_stage_is_not_applied(real_refs):  # noqa: F811
    session = StagedSession(as_staged(EcologyModule()))
    session.inputs["volumes"] = {f.name: ROAD_INPUT[f.name]
                                 for f in session.staged.stage("volumes").fields if f.name in ROAD_INPUT}
    assert not session.accepts_custom_lines("volumes")
    session.overrides["volumes"].add_custom_line(price_block_lines_static())
    draft = session.compute("volumes", real_refs)
    assert not any(i.startswith("custom.") for i in draft.effective)
    assert len(draft.not_applied) == 1


def price_block_lines_static():
    return {"name": "УГМС", "unit": "шт.", "qty": 1, "price": 50000,
            "basis_kind": BASIS_PRICE_LIST, "basis": "лист «№1.4_ИЭИ», строка 97",
            "placement": PLACEMENT_AFTER_INDEX}


def test_excel_export_shows_basis_placement_and_after_index_total(sheet):
    estimate = {"ecology.recon": fixed("ecology.recon", 1000.0)}
    log = OverrideLog()
    line_id = log.add_custom_line(price_block_lines(sheet)[0])
    effective = log.effective_estimate(estimate)
    totals = calculate_total_with_vat(effective, PricingRefs(), "ecology", "2026-09-30")
    buffer = io.BytesIO()
    export_to_excel(effective, buffer, totals=totals)

    ws = openpyxl.load_workbook(io.BytesIO(buffer.getvalue()))["Смета"]
    headers = [c.value for c in ws[1]]
    assert headers[-1] == PLACEMENT_HEADER
    rows = {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}
    custom_row = rows[line_id]
    col = {h: i for i, h in enumerate(headers)}
    assert custom_row[col["Обоснование"]] == "своя строка — по прайсу: лист «№1.4_ИЭИ», строка 97"
    assert custom_row[col["Индекс"]] is None
    assert custom_row[col["Сумма в текущих ценах"]] == 50000.0
    assert custom_row[col[PLACEMENT_HEADER]].startswith("после индекса")
    assert rows["ecology.recon"][col["Сумма в текущих ценах"]] == pytest.approx(1000.0 * INDEX)
    labels = {r[1]: r[5] for r in ws.iter_rows(min_row=2, values_only=True) if r[1]}
    assert labels[TOTAL_AFTER_INDEX] == 50000.0
    assert labels["Итого в базовых ценах"] == 1000.0
    assert labels["Итого в текущих ценах (с индексацией)"] == pytest.approx(1000.0 * INDEX + 50000.0)


def test_excel_export_without_custom_lines_has_no_placement_column():
    buffer = io.BytesIO()
    export_to_excel({"a": fixed("a", 1.0)}, buffer)
    ws = openpyxl.load_workbook(io.BytesIO(buffer.getvalue()))["Смета"]
    assert PLACEMENT_HEADER not in [c.value for c in ws[1]]


def test_budget_includes_after_index_lines_without_indexing(tmp_path):
    log = OverrideLog()
    log.add_custom_line(price_block_lines_static())
    effective = log.effective_estimate({"ecology.recon": fixed("ecology.recon", 1000.0)})
    totals = calculate_total_with_vat(effective, PricingRefs(), "ecology", "2026-09-30")
    aggregator = BudgetAggregator()
    aggregator.add_module(ModuleBudget.from_totals("ecology", "Экология", totals))
    summary = aggregator.summary()
    assert summary["total_with_vat_sum"] == pytest.approx((1000.0 * INDEX + 50000.0) * (1 + VAT))
    assert summary["modules"][0].after_index_total == 50000.0
    path = tmp_path / "budget.xlsx"
    aggregator.to_excel(path)
    notes = {r[0]: r[1] for r in openpyxl.load_workbook(path)["Сведения"].iter_rows(values_only=True)}
    assert notes["Работы вне сборника после индекса: Экология"] == 50000.0


def test_custom_line_field_edit_is_not_applied_and_not_offered(real_refs):  # noqa: F811
    """Ревью Э7 №7: своя строка правится только целиком (update_custom_line);
    обычная правка qty/price мимо её записи в журнале не применяется."""
    session = StagedSession(as_staged(EcologyModule()))
    session.inputs["volumes"] = {f.name: ROAD_INPUT[f.name]
                                 for f in session.staged.stage("volumes").fields if f.name in ROAD_INPUT}
    session.confirm("volumes", real_refs)
    log = session.overrides["estimate"]
    line_id = log.add_custom_line(price_block_lines_static())
    draft = session.compute("estimate", real_refs)
    assert session.editable_fields("estimate", draft.effective[line_id]) == ()
    log.apply(line_id, "qty", 1.0, 3.0)
    draft = session.compute("estimate", real_refs)
    assert draft.effective[line_id].qty == 1
    assert any(e.line_id == line_id and e.field == "qty" for e in draft.not_applied)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_custom_line_rejects_nan_and_infinity(bad):
    data = dict(price_block_lines_static(), qty=bad)
    with pytest.raises(ValueError, match="обычными числами"):
        OverrideLog().add_custom_line(data)
