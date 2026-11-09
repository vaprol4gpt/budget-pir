"""Общие хелперы модулей (были продублированы в каждом
`modules/<name>/module.py` — вынесены на Э2 дорожной карты, 25.10.2026).

Здесь только то, что у модулей совпадало буквально или по смыслу:
префикс основания «цена не найдена», поиск цены без падения, какие поля
строки правит оператор на этапах ведомости и сметы. Связь «строка сметы →
строка ведомости» у каждого модуля своя (свои префиксы и особые случаи) —
её модуль передаёт сюда функцией (`estimate_editable_by`), сама она
остаётся в модуле."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from core.overrides import RATE_KINDS
from core.stages import SOURCE_INPUT

# Начало основания строки, цена которой не найдена в справочнике, —
# по нему check() модулей и интерфейс отличают такие строки.
MISSING_PRICE_BASIS_PREFIX = "Цена не найдена в справочнике"


def missing_price_basis(name: str, detail: str = "") -> str:
    """Основание строки без цены: «Цена не найдена в справочнике для
    «<name>»<detail> (проверьте вручную)»."""
    return f"{MISSING_PRICE_BASIS_PREFIX} для «{name}»{detail} (проверьте вручную)"


def get_price_or_missing(
    refs: Any, source: str, table: str, item: str, category: str | None, name: str
) -> tuple[float, str]:
    """`refs.get_price(...)`, а если позиции нет — `(0.0, основание
    «цена не найдена»)`: строка всё равно строится, расчёт не падает
    из-за одной неизвестной позиции, check() модуля предупредит."""
    try:
        return refs.get_price(source, table, item, category=category)
    except KeyError:
        return 0.0, missing_price_basis(name)


def is_rate_line(line: Any) -> bool:
    return getattr(line, "kind", None) in RATE_KINDS


def volume_sheet_editable(line: Any, previous: Mapping[str, Any]) -> tuple[str, ...]:
    """Этап ведомости: строки ввода меняются в форме, не правкой строки;
    автосгенерированные — правкой объёма."""
    return () if line.source == SOURCE_INPUT else ("qty",)


def estimate_editable_by(
    volume_key_for_line: Callable[[str], str | None],
) -> Callable[[Any, Mapping[str, Any]], tuple[str, ...]]:
    """`editable_fields` этапа сметы.

    - строка «доля от базы» (kind B/C) — правится только ставка `rate`
      (qty/price у неё в сумме не участвуют, их правка ничего бы не
      изменила);
    - строка, у которой есть строка утверждённой ведомости, — только
      цена (решение №5: объём — возвратом на ведомость);
    - остальные — объём и цена."""

    def editable(line: Any, previous: Mapping[str, Any]) -> tuple[str, ...]:
        if is_rate_line(line):
            return ("rate",)
        volumes = previous.get("volumes")
        if volumes is not None and volume_key_for_line(line.id) in volumes.lines:
            return ("price",)
        return ("qty", "price")

    return editable


def _numbers(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers(v)]
    if isinstance(value, (list, tuple)):
        return [n for v in value for n in _numbers(v)]
    return []


def zero_volume_warnings(
    inputs: Mapping[str, Any],
    expected: Mapping[str, str],
    expected_in_rows: Mapping[str, tuple[str, str]] | None = None,
) -> list[str]:
    """Находка ревью №10 (30.10.2026): «0 в обязательном числовом поле
    считается заполненным» — нулевая ведомость подтверждается без ввода.
    Не блокировка, а предупреждение этапа ведомости: если раздел не пустой
    (хоть один объём > 0), а поле, которое по смыслу работ и эталонам должно
    быть > 0, равно 0 — сказать об этом. `expected` — поле → подпись;
    `expected_in_rows` — группа → (поле строки, подпись): проверяется каждая
    строка группы. Список полей по разделам — на подтверждение (список вопросов В-8)."""
    if not any(n > 0 for n in _numbers(dict(inputs))):
        return []
    warnings = []
    for name, label in expected.items():
        value = inputs.get(name)
        if not value:
            warnings.append(
                f"«{label}» = 0, хотя в разделе есть другие объёмы — обычно это поле больше нуля "
                "(проверьте ввод; если работа действительно не нужна — подтвердите как есть)."
            )
    for group, (field, label) in (expected_in_rows or {}).items():
        for number, row in enumerate(inputs.get(group) or [], start=1):
            if not row.get(field):
                warnings.append(
                    f"«{label}» в строке {number} = 0 — строка без объёма (удалите её или "
                    "введите значение)."
                )
    return warnings
