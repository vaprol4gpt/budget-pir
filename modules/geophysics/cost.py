"""Себестоимость (минимальный бюджет) инженерно-геофизических исследований
(ИГФИ) — 03.11.2026. Смету по СЦиР-1982
(`module.py`) не трогает.

Драйверы раздела (остальное — типовой расчёт `core/cost.py::compute_party_cost`):
- объём — физические точки наблюдений: ВЭЗ (`vez`) + сейсморазведка МПВ (`mpv`)
  утверждённой ведомости; камеральные строки в срок поля не входят;
- норма выработки — 100 точек на человека полевой группы в месяц, ДОПУЩЕНИЕ
  (`data/norms/geophysics_cost.json`, v5.2 С2_НОРМЫ!D8 — по одному объекту А);
  норма не различает методы (ВЭЗ и МПВ идут с разной скоростью) — вопрос к
  начальнику отдела; оператор может задать срок поля сам;
- состав по норме — правило v5.2 ПР1_ТРУД: поле — 1 ведущий инженер + (N − 1)
  инженеров (C37:C38), камералка — инженеров ROUND(N × 0,67) (C67).
"""

from __future__ import annotations

from collections.abc import Mapping

from core.cost import CostResult, PartySpec, SharedCosts, compute_party_cost, excel_round_half_up
from core.norms import Norm

NORMS_NAME = "geophysics_cost"
POINT_KEYS = ("vez", "mpv")


def suggest(people: int) -> dict[str, int]:
    """Состав партии ИГФИ из `people` человек поля (v5.2 ПР1_ТРУД C37:C38, C67)."""
    return {
        "field_lead": 1, "field_engineer": max(1, people - 1),
        "office_engineer": max(1, excel_round_half_up(people * 0.67)),
    }


SPEC = PartySpec(
    code="geophysics", label="Геофизика (ИГФИ)", norms_name=NORMS_NAME,
    volume_label="точки наблюдений (ВЭЗ + МПВ)", volume_unit="точек",
    rate_norm_key="points_per_person_month", suggest=suggest,
    rule_source="v5.2: численность = точки / (100 × срок), не менее 2 (ПР1_ТРУД!F10); состав — "
                "ПР1_ТРУД C37:C38 (поле), C67 (камералка)",
    volume_word="числа точек",
)


def volume_from_sheet(lines: Mapping) -> float:
    """Точки ВЭЗ + МПВ по строкам утверждённой ведомости (`VolumeLine`)."""
    return float(sum(lines[key].qty or 0 for key in POINT_KEYS if key in lines))


def compute_cost(values: Mapping, volume: float, norms: Mapping[str, Norm] | None = None,
                 shared: SharedCosts | None = None) -> CostResult:
    """Себестоимость ИГФИ по вводу (общие поля + поля раздела) и числу точек."""
    return compute_party_cost(SPEC, values, volume, norms, shared)
