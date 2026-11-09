"""Защита минимального бюджета (карта Э8 (б), аудит 30.10.2026 §4.3):
минимум не калиброван фактом, а при «минимум > максимума» оператор видит,
что виноваты постоянные затраты, и как их разделить между объектами."""

import pytest

from core.norms import load_norms
from modules.geology.cost import (
    CALIBRATION_NOTE,
    CostInputs,
    compute_cost,
    fixed_costs,
    reduction,
)

NORMS = load_norms("geology_cost", use_local=False)

FULL = CostInputs(
    brigades=3, itr=2, drivers=2, cooks=1, office_staff=2, office_months=2,
    salary_worker=50000, salary_itr=50000, salary_driver=50000, salary_cook=50000,
    salary_office=40000, mileage_uaz=1000, fuel_price_ai92=60, fuel_price_diesel=70,
    lab_samples=10,
)


def test_fixed_costs_by_hand():
    result = compute_cost(FULL, meters=1050, distance_km=100, norms=NORMS)
    amounts = {line.key: line.amount for line in result.lines}
    # проезд 14 чел. + камеральный ФОТ 2 × 40 000 × 2 мес с соцотчислениями 30,2 % + трал
    expected = amounts["travel"] + 160000 * 1.302 + amounts["lowboy"]
    assert fixed_costs(result) == pytest.approx(expected)


def test_reduction_is_not_calibrated():
    result = compute_cost(FULL, meters=1050, distance_km=100, norms=NORMS)
    assert reduction(result, 10_000_000).calibrated is False
    assert "не калиброван" in CALIBRATION_NOTE


def test_loss_explains_fixed_costs():
    result = compute_cost(FULL, meters=1050, distance_km=100, norms=NORMS)
    loss = reduction(result, 5_000_000)
    assert loss.coefficient > 1
    share = fixed_costs(result) / 5_000_000
    text = " ".join(loss.warnings)
    assert "убыточна" in text
    assert "Постоянные затраты" in text and f"{share:.0%}" in text
    assert "разделите эти затраты между объектами" in text
    # суммы — в рублях с запятой, как в интерфейсе
    assert "руб." in text and "," in text.split("руб.")[0][-4:]


def test_no_fixed_costs_warning_without_loss():
    result = compute_cost(FULL, meters=1050, distance_km=100, norms=NORMS)
    assert reduction(result, 10_000_000).warnings == []
