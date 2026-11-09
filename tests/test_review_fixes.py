"""Исправления по независимой проверке ветки (25.10.2026): правка поверх
изменившегося расчёта, граничные случаи себестоимости, расстояние ИГМИ,
итоги выгрузки бюджета."""

import openpyxl
import pytest

from core.aggregator import BudgetAggregator, ModuleBudget
from core.models import EstimateLine
from core.module_helpers import estimate_editable_by
from core.object_data import CalculationContext, ObjectData
from core.stages import Stage, StagedModule, StagedSession
from modules.geology.cost import CostInputs, compute_cost, reduction
from modules.hydromet.module import P_DISTANCE_KM, HydrometModule


def _session(rate_holder):
    def compute(inputs, previous, refs, pinned):
        return {
            "a": EstimateLine("a", "Работа", "шт", 10, 5.0, {}, "A", [], ""),
            "t": EstimateLine("t", "Транспорт", "%", 0, 0, {"rate": rate_holder["rate"]}, "B", ["a"], ""),
        }

    stage = Stage(1, "estimate", "Смета", [], compute,
                  editable_fields=estimate_editable_by(lambda line_id: None))
    return StagedSession(StagedModule(object(), [stage]))


def test_edit_over_changed_calculation_is_warned():
    holder = {"rate": 0.0875}
    session = _session(holder)
    session.overrides["estimate"].apply("t", "rate", 0.0875, 0.1)
    assert not any("изменилось после правки" in w for w in session.compute("estimate", None).warnings)
    holder["rate"] = 0.1875  # например, другое расстояние до объекта
    draft = session.compute("estimate", None)
    assert draft.effective["t"].coefs["rate"] == 0.1  # правка по-прежнему побеждает
    assert any("Транспорт" in w and "0.0875 → 0.1875" in w for w in draft.warnings)


def test_pinned_volume_edits_are_not_reported_as_drift():
    from core.stages import SOURCE_OPERATOR, VolumeLine

    def compute(inputs, previous, refs, pinned):
        qty = pinned.get("v", {}).get("qty", 74.4)
        return {"v": VolumeLine("v", "Монолиты", "проба", qty, SOURCE_OPERATOR)}

    session = StagedSession(StagedModule(object(), [Stage(1, "volumes", "Ведомость", [], compute)]))
    session.overrides["volumes"].apply("v", "qty", 74.4, 60)
    draft = session.compute("volumes", None)
    assert draft.effective["v"].qty == 60
    assert draft.warnings == []


def test_cost_with_zero_meters_is_incomplete():
    result = compute_cost(CostInputs(brigades=3), meters=0)
    assert not result.complete
    assert any("метраж бурения" in m for m in result.missing)


def test_reduction_with_zero_smeta_has_no_coefficient_and_no_loss_warning():
    full = CostInputs(brigades=1, salary_worker=1.0, fuel_price_diesel=1.0, lab_samples=1)
    result = compute_cost(full, meters=350, distance_km=1)
    assert result.complete
    red = reduction(result, 0.0)
    assert red.coefficient is None
    assert any("равна 0" in w for w in red.warnings)
    assert not any("убыточна" in w for w in red.warnings)


def test_hydromet_distance_mismatch_with_object_data_is_warned():
    stage = HydrometModule().stages()[1]
    ctx = CalculationContext(None, ObjectData(distance_km=10))
    assert stage.context_warnings({P_DISTANCE_KM: 30}, {}, ctx) != []
    assert stage.context_warnings({P_DISTANCE_KM: 10}, {}, ctx) == []
    assert stage.context_warnings({P_DISTANCE_KM: 30}, {}, CalculationContext(None)) == []


def test_budget_excel_totals_empty_when_no_module_priced(tmp_path):
    aggregator = BudgetAggregator()
    aggregator.add_module(ModuleBudget("geology", "Геология", 100.0, None, None, None))
    path = tmp_path / "b.xlsx"
    aggregator.to_excel(str(path))
    wb = openpyxl.load_workbook(path)
    total = list(wb.active.iter_rows(values_only=True))[-1]
    # с 30.10.2026 (находка ревью №8) база в строке ИТОГО не заполняется
    assert total == ("ИТОГО", None, None, None, None)
    notes = dict(wb["Сведения"].iter_rows(values_only=True))
    assert "разные" in notes["База в строке ИТОГО"]


@pytest.mark.parametrize("value", [0.0, 12.5])
def test_budget_excel_totals_when_priced(tmp_path, value):
    aggregator = BudgetAggregator()
    aggregator.add_module(ModuleBudget("geology", "Геология", 100.0, value, value * 0.2, value * 1.2))
    path = tmp_path / "b.xlsx"
    aggregator.to_excel(str(path))
    total = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))[-1]
    assert total[2:] == (value, value * 0.2, value * 1.2)
