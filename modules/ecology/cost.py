"""Себестоимость (минимальный бюджет) инженерно-экологических изысканий
(ИЭИ) — трек 3 прогона «два дня», 03.11.2026. Смету по СБЦ-99
(`module.py`) не трогает.

Драйверы раздела (остальное — типовой расчёт `core/cost.py::compute_party_cost`):
- объём — площадь радиационного обследования, га (`radiation_area` утверждённой
  ведомости; в v5.2 численность ИЭИ считается от площади обследования, ПР1_ТРУД!F11);
- норма выработки — 120 га на человека полевой группы в месяц, ДОПУЩЕНИЕ
  (`data/norms/ecology_cost.json`, v5.2 С2_НОРМЫ!D9); оператор может задать срок сам;
- состав по норме — правило v5.2 ПР1_ТРУД: поле — N инженеров (C41), камералка —
  N инженеров (C70);
- особые статьи: сторонняя лаборатория — число проб (по умолчанию сумма строк
  «Пробы …» ведомости) × цена пробы (ДОПУЩЕНИЕ 5 300 руб., v5.2 С2_НОРМЫ!D47);
  платные справки и исходные данные — сумма на объект (по умолчанию ДОПУЩЕНИЕ
  100 000 руб., v5.2 С2_НОРМЫ!D48).
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from core.cost import (
    ARTICLE_FIXED,
    KIND_OPTIONAL,
    CostLine,
    CostResult,
    InputField,
    PartySpec,
    SharedCosts,
    compute_party_cost,
    norm_value,
)
from core.norms import Norm

NORMS_NAME = "ecology_cost"
AREA_KEY = "radiation_area"
SAMPLE_PREFIX = "sampling_"


def suggest(people: int) -> dict[str, int]:
    """Состав партии ИЭИ из `people` человек поля (v5.2 ПР1_ТРУД C41, C70)."""
    return {"field_engineer": people, "office_engineer": people}


def extra_lines(values: Mapping, norms: Mapping[str, Norm], context: Mapping):
    """Сторонняя лаборатория и платные справки."""
    lines, missing, warnings = [], [], []
    samples = values.get("lab_samples")
    from_sheet = samples is None
    # число проб — целое; дробный ввод округляется ВВЕРХ: неполная проба всё равно
    # оплачивается лаборатории целиком (минимум не должен занижаться)
    samples = math.ceil(float(context.get("samples", 0) if from_sheet else samples) - 1e-9)
    price = norm_value(norms, "eco_lab_per_sample", NORMS_NAME)
    lines.append(CostLine(
        "lab", "Сторонние лабораторные", samples * price,
        f"{samples} проб × {price:g} руб." + (" (число проб — по ведомости)" if from_sheet else ""),
    ))
    if samples == 0:
        warnings.append("Сторонние лабораторные: проб 0 — проверьте.")
    info = values.get("info_services")
    info_amount = norm_value(norms, "eco_info_services", NORMS_NAME) if info is None else float(info)
    lines.append(CostLine("info", "Платные справки и исходные данные", info_amount,
                          "норма на объект (допущение)" if info is None else "ввод оператора",
                          kind=ARTICLE_FIXED))
    return lines, missing, warnings


SPEC = PartySpec(
    code="ecology", label="Экология (ИЭИ)", norms_name=NORMS_NAME,
    volume_label="площадь радиационного обследования", volume_unit="га",
    rate_norm_key="eco_ha_per_person_month", suggest=suggest,
    rule_source="v5.2: численность = площадь / (120 га × срок), не менее 2 (ПР1_ТРУД!F11); состав — "
                "ПР1_ТРУД C41 (поле), C70 (камералка)",
    extra_fields=(
        InputField("lab_samples", "Проб в стороннюю лабораторию (пусто — по ведомости)", KIND_OPTIONAL),
        InputField("info_services", "Платные справки и исходные данные, руб. (пусто — норма)",
                   KIND_OPTIONAL),
    ),
    extra_lines=extra_lines,
    volume_word="площади обследования",
)


def volume_from_sheet(lines: Mapping) -> float:
    """Площадь радиационного обследования, га, по утверждённой ведомости."""
    line = lines.get(AREA_KEY)
    return float(line.qty or 0) if line is not None else 0.0


def samples_from_sheet(lines: Mapping) -> int:
    """Число проб по строкам опробования утверждённой ведомости."""
    return int(sum(line.qty or 0 for key, line in lines.items() if key.startswith(SAMPLE_PREFIX)))


def compute_cost(values: Mapping, volume: float, norms: Mapping[str, Norm] | None = None,
                 shared: SharedCosts | None = None, samples: int = 0) -> CostResult:
    """Себестоимость ИЭИ по вводу, площади обследования и числу проб ведомости."""
    return compute_party_cost(SPEC, values, volume, norms, shared, {"samples": samples})
