"""Базовые модели данных для расчёта сметы ПИР."""

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass
class EstimateLine:
    id: str
    name: str
    unit: str
    qty: float
    price: float
    coefs: dict[str, float]
    kind: str
    base_ids: list[str]
    basis: str
    # Округлить сумму строки до N знаков после запятой, как ROUND(x; N)
    # в Excel (половина — от нуля). None — не округлять (прежнее
    # поведение, все модули до гидрометеорологии). Нужно, когда
    # эталонная смета округляет каждую строку и каждый итог раздела до
    # рубля и считает следующие строки уже от округлённых сумм (лист
    # ИГМИ — см. modules/hydromet/module.py): без этого расхождение с
    # эталоном накапливается до рублей после индексации.
    rounding: int | None = None
    # Место строки в итоге сметы (Э7, «своя строка» оператора, Методика
    # п. 6 — работы вне сборника по прайсу / калькуляции / аналогу).
    # None — обычная строка модуля (в базовых ценах, индексируется).
    # PLACEMENT_BEFORE_INDEX — своя строка в базовых ценах (индексируется,
    # как строки модуля). PLACEMENT_AFTER_INDEX — своя строка уже в
    # текущих ценах: в «Итого в базовых ценах» не входит, к итогу
    # прибавляется ПОСЛЕ индексации (как блок «Работы, не учтённые СБЦ-99»
    # листа «№1.4_ИЭИ»), НДС — на всё (core/pricing.py).
    placement: str | None = None


PLACEMENT_BEFORE_INDEX = "before_index"
PLACEMENT_AFTER_INDEX = "after_index"
PLACEMENT_LABELS = {
    PLACEMENT_BEFORE_INDEX: "до индекса (в базовых ценах сборника)",
    PLACEMENT_AFTER_INDEX: "после индекса (уже в текущих ценах)",
}


def is_after_index(line) -> bool:
    """Строка прибавляется к итогу после индексации (не индексируется)."""
    return getattr(line, "placement", None) == PLACEMENT_AFTER_INDEX


def excel_round(value: float, digits: int) -> float:
    """ROUND(value; digits) как в Excel: половина округляется от нуля
    (38,5 → 39; −38,5 → −39), а не к чётному, как встроенный round().
    Сначала — 15 значащих цифр, как считает Excel: произведение, которое должно
    быть ровно N,5, в двоичном представлении часто чуть меньше (1,5 × 15 × 1,4 =
    31,499999999999996), и без этого уходило вниз (Excel — 32, программа — 31;
    аудит 05.11.2026, О-6). Не конечное число (из повреждённых данных) возвращается
    как есть — его покажет проверка итога, а не ошибка округления."""
    if not math.isfinite(value):
        return value
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(format(value, ".15g")).quantize(quantum, rounding=ROUND_HALF_UP))


def calculate_amount(
    line: EstimateLine,
    all_lines: dict[str, EstimateLine],
    cache: dict[str, float] | None = None,
    _visiting: tuple[str, ...] = (),
) -> float:
    """Считает итоговую сумму строки сметы.

    kind="A": qty * price * произведение значений из coefs.
    kind="B"/"C": сумма amount по строкам из base_ids, умноженная
    на коэффициент coefs["rate"].
    Если у строки задан `rounding` — результат округляется как ROUND
    в Excel (`excel_round`); строки kind B/C получают суммы базовых
    строк уже округлёнными (каждая округляется в своём вызове).

    Поднимает `ValueError` с полным путём цикла (например
    "geology.a → geology.b → geology.a"), если строка ссылается сама
    на себя — напрямую или через цепочку `base_ids` — вместо того,
    чтобы упасть с `RecursionError` без объяснений.

    `cache` — необязательный словарь для мемоизации (id строки ->
    посчитанная сумма). По умолчанию `None` — поведение полностью
    совпадает с прежним, ничего не кешируется. Если вызывающий код
    передаёт один и тот же словарь во все вызовы `calculate_amount`
    для строк одной сметы (см. `core/export_excel.py`,
    `core/pricing.py`, `run_stub.py`), каждая базовая строка Б/В
    считается не больше одного раза за весь расчёт этой сметы, даже
    если на неё ссылаются несколько других строк. Кеш — обычный
    `dict` под управлением вызывающего кода: чтобы не "утекало" между
    разными сметами, для каждой сметы нужно передавать свой, новый
    словарь.
    """
    if cache is not None and line.id in cache:
        return cache[line.id]

    if line.id in _visiting:
        cycle = " → ".join((*_visiting, line.id))
        raise ValueError(f"Обнаружен цикл в base_ids: {cycle}")

    if line.kind == "A":
        amount = line.qty * line.price
        for value in line.coefs.values():
            amount *= value
    elif line.kind in ("B", "C"):
        if "rate" not in line.coefs:
            raise ValueError(
                f"Строка '{line.id}': для kind={line.kind} в coefs нужен ключ 'rate'"
            )
        base_sum = 0.0
        next_visiting = (*_visiting, line.id)
        for base_id in line.base_ids:
            if base_id not in all_lines:
                raise KeyError(f"Строка '{line.id}': base_id '{base_id}' не найден")
            base_sum += calculate_amount(all_lines[base_id], all_lines, cache, next_visiting)
        amount = base_sum * line.coefs["rate"]
    else:
        raise ValueError(f"Строка '{line.id}': неизвестный kind '{line.kind}'")

    if line.rounding is not None:
        amount = excel_round(amount, line.rounding)

    if cache is not None:
        cache[line.id] = amount
    return amount
