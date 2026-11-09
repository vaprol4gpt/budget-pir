"""Себестоимость (минимальный бюджет) инженерно-геодезических изысканий
(ИГДИ) — трек 3 прогона «два дня», 03.11.2026. Смету по СБЦи5.6
(`module.py`) не трогает.

Драйверы раздела (остальное — типовой расчёт `core/cost.py::compute_party_cost`):
- объём — площадь топографической съёмки, га: сумма строк «топосъёмка» утверждённой
  ведомости (`topo_survey.*`; планы и опорная сеть в срок поля не входят — в v5.2
  численность ИГДИ считается от площади съёмки, ПР1_ТРУД!F6);
- норма выработки — 50 га на человека полевой группы в месяц, ДОПУЩЕНИЕ
  (`data/norms/geodesy_cost.json`, v5.2 С2_НОРМЫ!D7): срок поля = площадь / (норма ×
  численность поля); оператор может задать срок сам;
- состав партии по норме (кнопка «Подставить состав») — правило v5.2 ПР1_ТРУД:
  поле — 1 ведущий инженер, инженеров ROUND(N × 0,33) (не меньше 1), 1 водитель,
  остальные рабочие (C15:C18); камералка — 1 главный специалист, 1 руководитель
  группы, инженеров ROUND(N × 0,5), техников ROUND(N × 0,3) (C47:C50).
"""

from __future__ import annotations

from collections.abc import Mapping

from core.cost import CostResult, PartySpec, SharedCosts, compute_party_cost, excel_round_half_up
from core.norms import Norm

NORMS_NAME = "geodesy_cost"
SURVEY_PREFIX = "topo_survey."


def suggest(people: int) -> dict[str, int]:
    """Состав партии ИГДИ из `people` человек поля (v5.2 ПР1_ТРУД C15:C18, C47:C50)."""
    engineers = max(1, excel_round_half_up(people * 0.33))
    return {
        "field_lead": 1, "field_engineer": engineers, "field_driver": 1,
        "field_worker": max(0, people - 2 - engineers),
        "office_chief": 1, "office_lead": 1,
        "office_engineer": max(1, excel_round_half_up(people * 0.5)),
        "office_technician": max(1, excel_round_half_up(people * 0.3)),
    }


SPEC = PartySpec(
    code="geodesy", label="Геодезия (ИГДИ)", norms_name=NORMS_NAME,
    volume_label="площадь топографической съёмки", volume_unit="га",
    rate_norm_key="survey_ha_per_person_month", suggest=suggest,
    rule_source="v5.2: численность = площадь / (50 га × срок), не менее 2 (ПР1_ТРУД!F6); состав — "
                "ПР1_ТРУД C15:C18 (поле), C47:C50 (камералка)",
    volume_word="площади съёмки",
)


def volume_from_sheet(lines: Mapping) -> float:
    """Площадь съёмки, га, по строкам утверждённой ведомости (`VolumeLine`)."""
    return float(sum(line.qty or 0 for key, line in lines.items() if key.startswith(SURVEY_PREFIX)))


def compute_cost(values: Mapping, volume: float, norms: Mapping[str, Norm] | None = None,
                 shared: SharedCosts | None = None) -> CostResult:
    """Себестоимость ИГДИ по вводу (общие поля + поля раздела) и площади съёмки."""
    return compute_party_cost(SPEC, values, volume, norms, shared)
