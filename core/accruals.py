"""Начисления уровня сметы раздела (С3, 30.10.2026): неблагоприятный период,
внешний транспорт, организация и ликвидация работ, районный коэффициент и
северные надбавки к полевым и к камеральным работам.

Один механизм для всех модулей: каждое начисление — строка «доля от базы»
(`EstimateLine` kind="B", `coefs["rate"]` × сумма строк `base_ids`), как уже
устроены внутренний транспорт и техотчёт; логика расчёта строки
(`core/models.py`) не меняется. Модуль описывает только СВОЮ схему
(`AccrualScheme`): какие его строки полевые / лабораторные / камеральные и
на какую базу какое начисление ложится — по формулам своего эталонного листа
(таблица норм и источников — `docs/nachisleniya.md`, разбор эталонов —
записи разработки, С3).

Откуда ставки (`_rates`):
- внешний транспорт, оргликвидация, районные к полевым и к камеральным — из
  данных объекта (шаг 0, `core/object_data.py`): ставка раздела, если задана,
  иначе общая (`ObjectData.rate`). Сборников с общими указаниями в репозитории
  нет, поэтому ставки не выводятся, а вводятся оператором; в подсказке поля —
  значения эталонных смет (ДОПУЩЕНИЕ, список вопросов В-18). Не задана — строки
  нет, в предупреждениях этапа сметы — что не применено (`accrual_warnings`);
  0 — строка есть, с нулём («не начислять»);
- оргликвидация — ставка × `org_liquidation_k` данных объекта (не задан — 1);
- неблагоприятный период — только при отметке объекта и позиции
  `unfavorable_source` в справочнике расценок: ставка = коэффициент − 1 (нет
  позиции — начисления нет, с предупреждением);
- `fixed_rates` (метрология геофизики) — только вместе хотя бы с одной
  ненулевой ставкой объекта: без данных объекта смета раздела не меняется.
Ни одной ставки — смета возвращается тем же объектом, без строк начислений.

Округление — по `AccrualScheme.rounding` (как ROUND листа), округляется сумма
строки начисления, не её база; итог раздела программа не округляет.

ИГМИ свои начисления считает сам (`modules/hydromet/module.py`, ввод этапа
сметы) — схема ИГМИ здесь не описывается.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core.models import EstimateLine
from core.object_data import ObjectData, object_data_of

# Имена начислений (суффикс id строки «<модуль>.accrual.<имя>») — в порядке
# применения: каждое следующее может брать предыдущие в базу.
UNFAVORABLE = "unfavorable"
EXTERNAL_TRANSPORT = "external_transport"
ORG_LIQUIDATION = "org_liquidation"
METROLOGY = "metrology"
REGIONAL_FIELD = "regional_field"
REGIONAL_OFFICE = "regional_office"
ORDER = (UNFAVORABLE, EXTERNAL_TRANSPORT, ORG_LIQUIDATION, METROLOGY, REGIONAL_FIELD, REGIONAL_OFFICE)

TITLES = {
    UNFAVORABLE: "Работы в неблагоприятный период",
    EXTERNAL_TRANSPORT: "Внешний транспорт",
    ORG_LIQUIDATION: "Организация и ликвидация работ",
    METROLOGY: "Метрологическое обеспечение",
    REGIONAL_FIELD: "Районный коэффициент и северные надбавки к полевым работам",
    REGIONAL_OFFICE: "Районный коэффициент и северные надбавки к камеральным работам",
}

# Поля данных объекта со ставками начислений (core/object_data.py) — для
# группировки в интерфейсе и сообщений «не задано».
PARAM_FIELDS = {
    EXTERNAL_TRANSPORT: "external_transport_rate",
    ORG_LIQUIDATION: "org_liquidation_rate",
    REGIONAL_FIELD: "regional_field_rate",
    REGIONAL_OFFICE: "regional_office_rate",
}


@dataclass(frozen=True)
class AccrualScheme:
    """Схема начислений модуля.

    `groups` — именованные группы строк сметы раздела: имя → признак строки
    (по id). Обычные имена: "field" (полевые), "lab" (лабораторные),
    "office" (камеральные и техотчёт), "internal_transport".
    `bases` — начисление → имена групп и/или ранее применённых начислений,
    сумма которых — база. Начисление, которого нет в `bases`, у раздела не
    применяется (в эталоне его нет).
    `unfavorable_source` — (сборник, таблица, пункт) коэффициента
    неблагоприятного периода в справочнике (цена позиции = коэффициент,
    например СБЦ-99 т.2 п.4 = 1,2); надбавка = коэффициент − 1.
    `fixed_rates` — ставки, не зависящие от объекта (метрология 5 % у
    геофизики — подпись эталона «К=0.05 П-22»).
    `rounding` — начисление → знаков после запятой (как ROUND в листе
    эталона; округляется сумма строки); нет ключа — без округления.
    `sources` — начисление → подпись основания из эталона (в строку сметы).
    `applied_warnings` — начисление → предупреждение, когда оно применено.
    Коэффициент к ставке оргликвидации — из данных объекта
    (`org_liquidation_k`), не задан — 1; своего поля в схеме у него нет."""

    module: str
    groups: dict[str, Callable[[str], bool]]
    bases: dict[str, tuple[str, ...]]
    unfavorable_source: tuple[str, str, str] | None = None
    fixed_rates: dict[str, float] = field(default_factory=dict)
    rounding: dict[str, int] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    # начисление → предупреждение, когда оно применено (известное расхождение
    # с эталоном, например особая формула оргликвидации геофизики)
    applied_warnings: dict[str, str] = field(default_factory=dict)


def line_id(scheme: AccrualScheme, name: str) -> str:
    return f"{scheme.module}.accrual.{name}"


def _percent(rate: float) -> str:
    text = f"{rate * 100:.4f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{text} %"


def _rates(scheme: AccrualScheme, data: ObjectData, refs: Any) -> tuple[dict[str, float], dict[str, str]]:
    """Ставки начислений раздела (доля базы) и пояснения к ним; начисления
    без ставки в результат не попадают."""
    rates: dict[str, float] = {}
    notes: dict[str, str] = {}
    for name in ORDER:
        if name not in scheme.bases:
            continue
        if name in scheme.fixed_rates:
            rates[name] = scheme.fixed_rates[name]
            notes[name] = _percent(rates[name])
        elif name == UNFAVORABLE:
            if not data.unfavorable_period or scheme.unfavorable_source is None:
                continue
            try:
                k, basis = refs.get_price(*scheme.unfavorable_source)
            except (KeyError, AttributeError):
                continue
            rates[name] = k - 1
            notes[name] = f"к = {k:g} ({basis})"
        elif name == ORG_LIQUIDATION:
            rate = data.rate(scheme.module, "org_liquidation_rate")
            if rate is None:
                continue
            k_value = data.rate(scheme.module, "org_liquidation_k")
            k = k_value if k_value is not None else 1.0
            rates[name] = rate * k
            notes[name] = _percent(rate) + (f" × {k:g}" if k != 1 else "")
        else:
            rate = data.rate(scheme.module, PARAM_FIELDS[name])
            if rate is None:
                continue
            rates[name] = rate
            notes[name] = _percent(rate)
    # Ставки, не зависящие от объекта (метрология), — только вместе с
    # начислениями по данным объекта: без них смета раздела — как до С3.
    if not any(v > 0 for n, v in rates.items() if n not in scheme.fixed_rates):
        rates = {n: v for n, v in rates.items() if n not in scheme.fixed_rates}
        notes = {n: v for n, v in notes.items() if n in rates}
        if not rates:
            return {}, {}
    return rates, notes


def apply_accruals(
    estimate: dict[str, EstimateLine], scheme: AccrualScheme, refs: Any
) -> dict[str, EstimateLine]:
    """Смета раздела + строки начислений (по ставкам данных объекта в
    `refs`). Ставки не заданы — смета возвращается как есть. Строки сметы
    не меняются, добавляются новые в конец."""
    data = object_data_of(refs)
    rates, notes = _rates(scheme, data, refs)
    if not rates:
        return estimate
    result = dict(estimate)
    applied: list[str] = []
    for name in ORDER:
        if name not in rates:
            continue
        base_ids: list[str] = []
        for part in scheme.bases[name]:
            if part in scheme.groups:
                base_ids += [i for i in estimate if scheme.groups[part](i)]
            elif part in applied:
                base_ids.append(line_id(scheme, part))
        source = scheme.sources.get(name, "")
        result[line_id(scheme, name)] = EstimateLine(
            id=line_id(scheme, name),
            name=TITLES[name],
            unit="%",
            qty=0,
            price=0.0,
            coefs={"rate": rates[name]},
            kind="B",
            base_ids=base_ids,
            basis=f"{notes[name]} от суммы {_describe(scheme.bases[name])}"
            + (f"; {source}" if source else "") + "; ставка — данные объекта",
            rounding=scheme.rounding.get(name),
        )
        applied.append(name)
    return result


_PART_LABELS = {
    "field": "полевых работ",
    "lab": "лабораторных работ",
    "office": "камеральных работ и техотчёта",
    "prefield": "предполевых работ",
    "internal_transport": "внутреннего транспорта",
    UNFAVORABLE: "надбавки за неблагоприятный период",
    EXTERNAL_TRANSPORT: "внешнего транспорта",
    ORG_LIQUIDATION: "организации и ликвидации",
    METROLOGY: "метрологии",
}


def _describe(parts: tuple[str, ...]) -> str:
    return " + ".join(_PART_LABELS.get(p, p) for p in parts)


def accrual_breakdown(estimate: dict[str, EstimateLine]) -> list[tuple[str, float, float]]:
    """Расшифровка начислений сметы для сводного бюджета: [(название,
    ставка, сумма в ценах модуля)] по строкам «<модуль>.accrual.<имя>»."""
    from core.models import calculate_amount

    cache: dict[str, float] = {}
    return [
        (line.name, line.coefs.get("rate", 0.0), calculate_amount(line, estimate, cache))
        for line_id_, line in estimate.items()
        if ".accrual." in line_id_
    ]


# Начало предупреждения о незаданных ставках: по нему сводный бюджет отбирает его из
# предупреждений этапа сметы (раздел без начислений не должен выглядеть там обычным).
MISSING_ACCRUALS_PREFIX = "Начисления не применены"


def accrual_warnings(scheme: AccrualScheme, refs: Any) -> list[str]:
    """Предупреждения этапа сметы: какие начисления раздела не применены и
    почему (ставка не задана в данных объекта; неблагоприятный период
    отмечен, но в схеме раздела его нет или коэффициента нет в справочнике),
    плюс `applied_warnings` применённых начислений."""
    data = object_data_of(refs)
    rates, _notes = _rates(scheme, data, refs)
    missing = [
        TITLES[name].lower() for name in ORDER
        if name in scheme.bases and name in PARAM_FIELDS and name not in rates
    ]
    warnings = []
    if missing:
        warnings.append(
            f"{MISSING_ACCRUALS_PREFIX} — ставки не заданы в данных объекта (п. 1 «Объект и ТЗ», "
            "блок «Начисления»): " + "; ".join(missing) + "."
        )
    warnings += [text for name, text in scheme.applied_warnings.items() if name in rates]
    if data.unfavorable_period and UNFAVORABLE not in rates:
        if UNFAVORABLE in scheme.bases and scheme.unfavorable_source is not None:
            # раздел надбавку знает, но коэффициента нет в справочнике расценок
            collection, table, item = scheme.unfavorable_source
            warnings.append(
                "Отмечены работы в неблагоприятный период, но коэффициент не применён: в "
                f"справочнике расценок нет позиции {collection} т.{table} п.{item}."
            )
        else:
            warnings.append(
                "Отмечены работы в неблагоприятный период, но для этого раздела коэффициент не "
                "применён: в эталонных сметах раздела его нет."
            )
    return warnings
