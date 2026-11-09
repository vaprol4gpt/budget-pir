"""Итоговая сумма сметы с индексацией цен и НДС.

Общий (не специфичный для геологии) шаг, идущий ПОСЛЕ того, как смета
уже посчитана в базовых ценах эталона (`core/models.calculate_amount`,
который не меняется). Воспроизводит порядок эталонной сметы (лист
1.2.1_ИГИ, строки 159-162): "Итого в ценах на 01.01.91" -> "Итого с
учётом коэффициента инфляции" -> "Кроме того НДС" -> "Всего с НДС".
См. записи разработки () за обоснование,
почему это отдельная функция, а не изменение core/models.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type  # `date` — имя аргумента calculate_total_with_vat
from typing import Any

from core.models import EstimateLine, calculate_amount, is_after_index


@dataclass
class TotalWithVat:
    """Результат расчёта итога. base_total всегда заполнен; остальные
    поля — None, если индекс/ставку НДС не удалось найти на дату (см.
    warnings) — вместо того чтобы выдумывать число или падать."""

    base_total: float
    index: float | None
    indexed_total: float | None
    vat_rate: float | None
    vat_amount: float | None
    total_with_vat: float | None
    warnings: list[str] = field(default_factory=list)
    # Индекс взят не на дату расчёта, а последний известный (политика
    # INDEX_POLICY_LAST_KNOWN) — итог есть, но с предупреждением.
    index_fallback: bool = False
    # Период действия применённого индекса («III кв. 2026»), если
    # справочник его сообщает; None — не известен.
    index_period: str | None = None
    # Ставка НДС с основанием («НДС 22 % — 425-ФЗ …, с 01.01.2026»), если
    # справочник его сообщает; None — не известно (или ставка не найдена).
    vat_label: str | None = None
    # Свои строки «после индекса» (Э7, core/models.py::PLACEMENT_AFTER_INDEX)
    # — уже в текущих ценах: в base_total НЕ входят, в indexed_total
    # прибавлены после индексации (лист «№1.4_ИЭИ»: H114 = H95 + SUM(H97:H113)).
    # 0 — таких строк нет (итог как раньше).
    after_index_total: float = 0.0
    # Последний известный индекс отстал от даты расчёта больше чем на квартал.
    index_stale: bool = False


# «Последний известный» индекс старше этого — отдельное, более строгое сообщение (квартал ≤ 92 дней).
INDEX_STALE_DAYS = 92

# Политика, когда на дату расчёта индекс не внесён в справочник:
# LAST_KNOWN — взять последний известный индекс этого вида работ с явным
# предупреждением (по умолчанию; предложение на подтверждение —
# список вопросов); STRICT — итога с индексацией нет, только предупреждение
# (прежнее поведение).
INDEX_POLICY_LAST_KNOWN = "last_known"
INDEX_POLICY_STRICT = "strict"
DEFAULT_INDEX_POLICY = INDEX_POLICY_LAST_KNOWN


def _format_date(iso_date: str) -> str:
    year, month, day = iso_date.split("-")
    return f"{day}.{month}.{year}"


def _days_after(later: str, earlier: str) -> int:
    return (date_type.fromisoformat(later) - date_type.fromisoformat(earlier)).days


def calculate_total_with_vat(
    estimate: dict[str, EstimateLine],
    refs: Any,
    work_type: str,
    date: str,
    cache: dict[str, float] | None = None,
    index_policy: str = DEFAULT_INDEX_POLICY,
) -> TotalWithVat:
    """Посчитать итог сметы: база (1991) -> индексация -> НДС.

    Свои строки оператора «после индекса» (`is_after_index`) в базу не
    входят и не индексируются: `indexed_total = base_total × индекс +
    after_index_total`; НДС — на весь `indexed_total`. Своя строка «до
    индекса» — обычная строка базы. Без своих строк итог прежний.

    Если `refs.get_index` не находит индекс на `date` (бросает
    `KeyError`): при `index_policy="strict"` — частичный результат
    (без индексации и НДС) с понятным предупреждением в `warnings`; при
    `index_policy="last_known"` (по умолчанию) — последний известный
    индекс этого вида работ (`refs.get_last_index`), итог считается,
    в `warnings` — явное предупреждение, `index_fallback=True`. Если и
    последнего известного нет (или справочник не умеет его искать) —
    как в строгом режиме. Ставка НДС не найдена — частичный результат.

    `cache` — необязательный словарь для мемоизации `calculate_amount`
    (см. core/models.py); если не передан, создаётся свой на время
    этого вызова.
    """
    if index_policy not in (INDEX_POLICY_LAST_KNOWN, INDEX_POLICY_STRICT):
        raise ValueError(f"Неизвестная политика индекса: {index_policy!r}")
    if cache is None:
        cache = {}
    base_total = sum(
        calculate_amount(line, estimate, cache)
        for line in estimate.values() if not is_after_index(line)
    )
    after_index_total = sum(
        calculate_amount(line, estimate, cache)
        for line in estimate.values() if is_after_index(line)
    )
    warnings: list[str] = []

    index: float | None = None
    index_fallback = False
    index_stale = False
    index_period: str | None = None
    try:
        if hasattr(refs, "get_index_entry"):
            entry = refs.get_index_entry(work_type, date)
            index, index_period = entry.value, entry.period_label()
        else:
            index = refs.get_index(work_type, date)
    except KeyError as exc:
        last = None
        if index_policy == INDEX_POLICY_LAST_KNOWN and hasattr(refs, "get_last_index"):
            try:
                last = refs.get_last_index(work_type, date)
            except KeyError:
                last = None
        if last is not None:
            index, index_fallback, index_period = last.value, True, last.period_label()
            warnings.append(
                f"Индекс {index_period} (×{last.value}) применён к дате "
                f"{_format_date(date)}: актуальный индекс на эту дату не внесён в справочник."
            )
            index_stale = _days_after(date, last.period_end) > INDEX_STALE_DAYS
            if index_stale:
                warnings[-1] += (
                    " Индекс отстал от даты расчёта больше чем на квартал — цена может заметно "
                    "отличаться от текущей; нужен индекс этого квартала (письмо Минстроя)."
                )
        else:
            warnings.append(f"Индекс пересчёта цен на {date} не найден в справочнике: {exc}")

    indexed_total: float | None = None
    vat_rate: float | None = None
    vat_label: str | None = None
    vat_amount: float | None = None
    total_with_vat: float | None = None

    if index is not None:
        indexed_total = base_total * index
        if after_index_total:
            indexed_total += after_index_total
        # Ставка — на дату расчёта (не на период индекса): НДС начисляется
        # по закону, действующему на дату, см. data/build_references.py::VAT_RATES.
        try:
            if hasattr(refs, "get_vat_entry"):
                vat_entry = refs.get_vat_entry(date)
                vat_rate, vat_label = vat_entry.rate, vat_entry.label()
            else:
                vat_rate = refs.get_vat_rate(date)
        except KeyError as exc:
            warnings.append(f"Ставка НДС на {date} не найдена в справочнике: {exc}")

        if vat_rate is not None:
            vat_amount = indexed_total * vat_rate
            total_with_vat = indexed_total + vat_amount

    return TotalWithVat(
        base_total=base_total,
        index=index,
        indexed_total=indexed_total,
        vat_rate=vat_rate,
        vat_amount=vat_amount,
        total_with_vat=total_with_vat,
        warnings=warnings,
        index_fallback=index_fallback,
        index_stale=index_stale,
        index_period=index_period,
        vat_label=vat_label,
        after_index_total=after_index_total,
    )
