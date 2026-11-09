"""Себестоимость (минимальный бюджет) инженерно-гидрометеорологических
изысканий (ИГМИ) — 03.11.2026. Смету по СБЦ-2000
(`module.py`) не трогает.

НОРМЫ ВЫРАБОТКИ НЕТ ни в одном найденном источнике (v5.2: состав ИГМИ
фиксированный, срок — ввод, ЗАДАНИЕ!E15): срок полевых работ вводит оператор
(поле «Срок полевых работ»), без него расчёт помечен неполным. Остальное —
типовой расчёт `core/cost.py::compute_party_cost`.

- состав по умолчанию (кнопка «Подставить состав») — 1 ведущий инженер в поле,
  ДОПУЩЕНИЕ (`data/norms/hydromet_cost.json`, v5.2 С2_НОРМЫ!D12, объект А; в
  Форме 2.2 заказчика 2026 г. в поле 3 человека); камералка — ведущий инженер +
  инженер (v5.2 ПР1_ТРУД C53:C54);
- особая статья: покупка гидрометеоданных (климатическая, гидрологическая справки
  УГМС) — сумма на объект, ввод (в Форме 2.2 их покупают или пишут «учтено в
  расчёте по другому объекту»).
"""

from __future__ import annotations

from collections.abc import Mapping

from core.cost import (
    ARTICLE_FIXED,
    KIND_FLOAT,
    CostLine,
    CostResult,
    InputField,
    PartySpec,
    SharedCosts,
    compute_party_cost,
)
from core.norms import Norm

NORMS_NAME = "hydromet_cost"
STVOR_KEY = "sounding_stvor_count"


def suggest(people: int) -> dict[str, int]:
    """Состав партии ИГМИ (v5.2 ПР1_ТРУД C21, C53:C54)."""
    return {"field_lead": people, "office_lead": 1, "office_engineer": 1}


def extra_lines(values: Mapping, norms: Mapping[str, Norm], context: Mapping):
    """Покупка гидрометеоданных (справки УГМС)."""
    amount = float(values.get("data_purchase") or 0.0)
    warnings = [] if amount else [
        "Справки УГМС (климат, гидрология) = 0: если данные покупаются для этого объекта, введите "
        "сумму; если учтены в расчёте другого объекта — оставьте 0."
    ]
    return ([CostLine("data_purchase", "Гидрометеоданные (справки УГМС)", amount, "ввод оператора",
                      kind=ARTICLE_FIXED)], [], warnings)


SPEC = PartySpec(
    code="hydromet", label="Гидрометеорология (ИГМИ)", norms_name=NORMS_NAME,
    volume_label="промерные створы", volume_unit="створов",
    rate_norm_key=None, suggest=suggest, fixed_party_norm_key="field_party",
    rule_source="нормы выработки нет: срок полевых работ — ввод; состав — v5.2 С2_НОРМЫ!D12 "
                "(1 чел. в поле), ПР1_ТРУД C53:C54 (камералка)",
    extra_fields=(InputField("data_purchase", "Справки УГМС (климат, гидрология), руб.", KIND_FLOAT),),
    extra_lines=extra_lines,
    volume_word="объёма работ",
)


def volume_from_sheet(lines: Mapping) -> float:
    """Промерные створы по утверждённой ведомости (справочно: в расчёт срока не входят)."""
    line = lines.get(STVOR_KEY)
    return float(line.qty or 0) if line is not None else 0.0


def compute_cost(values: Mapping, volume: float = 0.0, norms: Mapping[str, Norm] | None = None,
                 shared: SharedCosts | None = None) -> CostResult:
    """Себестоимость ИГМИ по вводу (срок поля — обязательный ввод)."""
    return compute_party_cost(SPEC, values, volume, norms, shared)
