"""Слой правок оператора поверх сметы (МВП, без сохранения между сессиями).

Правки не трогают базовую смету (`base`), возвращаемую модулем через
`build_estimate()` — вместо этого `OverrideLog` хранит список правок и
умеет построить из `base` и этого списка новый, "эффективный" словарь
строк (`effective_estimate`), не мутируя `base`. Это то же самое
свойство чистой функции от входа, что и у `calculate_amount` в
`core/models.py`.

Своя строка оператора (Э7, Методика п. 6: работа, которой нет в сборнике,
оценивается по прайсу, калькуляции или аналогу) — тоже запись журнала
правок: `Edit` с полем `FIELD_CUSTOM` (добавить/изменить, данные строки —
в `Edit.custom`) или `FIELD_CUSTOM_DELETE` (удалить). Поэтому она без
отдельной таблицы хранится в версии сметы и в черновике, отменяется
«Отменить последнюю правку» и видна в истории правок. id своей строки —
`custom.<n>` (не начинается с кода модуля, поэтому в группы начислений
модуля — `ACCRUALS` — не попадает: схема модуля не меняется).
"""

import dataclasses
import math
from dataclasses import dataclass

from core.models import (
    PLACEMENT_AFTER_INDEX,
    PLACEMENT_BEFORE_INDEX,
    PLACEMENT_LABELS,
    EstimateLine,
)

# Виды строк «доля от базы» (core/models.py::calculate_amount).
RATE_KINDS = ("B", "C")

# Своя строка оператора (см. докстринг модуля).
CUSTOM_PREFIX = "custom."
FIELD_CUSTOM = "custom"
FIELD_CUSTOM_DELETE = "custom_delete"
CUSTOM_FIELDS = (FIELD_CUSTOM, FIELD_CUSTOM_DELETE)

# Вид основания своей строки (Методика п. 6) и подпись в смете.
BASIS_PRICE_LIST = "price_list"
BASIS_CALCULATION = "calculation"
BASIS_ANALOG = "analog"
BASIS_KIND_LABELS = {
    BASIS_PRICE_LIST: "по прайсу",
    BASIS_CALCULATION: "по калькуляции",
    BASIS_ANALOG: "по аналогу",
}
PLACEMENTS = (PLACEMENT_BEFORE_INDEX, PLACEMENT_AFTER_INDEX)


def is_custom_id(line_id: str) -> bool:
    return str(line_id).startswith(CUSTOM_PREFIX)


def custom_basis_text(basis_kind: str, basis: str) -> str:
    """«По прайсу: ООО «лаборатория», 2026» — колонка «Обоснование» сметы."""
    label = BASIS_KIND_LABELS[basis_kind]
    return f"{label[0].upper()}{label[1:]}: {basis}"


CUSTOM_MARK = "своя строка"
NO_BASIS_TEXT = "основание не указано"


def display_basis(line) -> str:
    """Колонка «Обоснование» в таблицах и в Excel: своя строка помечена как ручная —
    «своя строка — по прайсу: …» (у строк расчёта — их основание как есть). Только показ:
    в снимке и журнале хранится прежний текст, числа не меняются."""
    basis = str(getattr(line, "basis", "") or "")
    if not is_custom_id(getattr(line, "id", "")):
        return basis
    if not has_basis(line):
        head, sep, _tail = basis.partition(": ")
        basis = f"{head}: {NO_BASIS_TEXT}" if sep else NO_BASIS_TEXT
    return f"{CUSTOM_MARK} — {basis[:1].lower()}{basis[1:]}"


def has_basis(line) -> bool:
    """Есть ли у своей строки основание (текст после «По прайсу: » не пустой)."""
    basis = str(getattr(line, "basis", "") or "")
    head, sep, tail = basis.partition(": ")
    return bool(tail.strip()) if sep else bool(basis.strip())


def custom_without_basis(lines) -> list[str]:
    """Наименования своих строк сметы без основания — так могла сохраниться версия до
    обязательного основания или испорченная запись. Такая смета открывается, но в сводный
    бюджет не идёт, пока основание не заполнено (как смета с изменившимися числами, В-42)."""
    return [
        line.name or line.id
        for line in lines.values()
        if is_custom_id(getattr(line, "id", "")) and not has_basis(line)
    ]


def validate_custom(data: dict) -> dict:
    """Проверка и нормализация данных своей строки. Ошибка — ValueError с
    понятным оператору текстом."""
    name = str(data.get("name") or "").strip()
    unit = str(data.get("unit") or "").strip()
    basis = str(data.get("basis") or "").strip()
    basis_kind = data.get("basis_kind")
    placement = data.get("placement")
    errors = []
    if not name:
        errors.append("не указано наименование")
    if basis_kind not in BASIS_KIND_LABELS:
        errors.append("не выбран вид основания (прайс, калькуляция или аналог)")
    if not basis:
        errors.append("не указано основание (чей прайс, какая калькуляция, какой аналог)")
    if placement not in PLACEMENTS:
        errors.append("не выбрано место в смете (до индекса или после)")
    try:
        qty = float(data.get("qty"))
        price = float(data.get("price"))
    except (TypeError, ValueError):
        errors.append("количество и цена должны быть числами")
        qty = price = 0.0
    if not (math.isfinite(qty) and math.isfinite(price)):
        errors.append("количество и цена должны быть обычными числами")
    elif qty < 0 or price < 0:
        errors.append("количество и цена не могут быть отрицательными")
    if errors:
        raise ValueError("Своя строка: " + "; ".join(errors) + ".")
    return {
        "name": name, "unit": unit or "шт.", "qty": qty, "price": price,
        "basis_kind": basis_kind, "basis": basis, "placement": placement,
    }


def custom_line(line_id: str, data: dict) -> EstimateLine:
    """Строка сметы из данных своей строки (kind A: кол-во × цена)."""
    return EstimateLine(
        id=line_id,
        name=data["name"],
        unit=data["unit"],
        qty=data["qty"],
        price=data["price"],
        coefs={},
        kind="A",
        base_ids=[],
        basis=custom_basis_text(data["basis_kind"], data["basis"]),
        placement=data["placement"],
    )


def placement_label(placement: str | None) -> str:
    return PLACEMENT_LABELS.get(placement, "")


@dataclass
class Edit:
    line_id: str
    field: str
    old_value: float
    new_value: float
    reason: str | None = None
    # Только у записей своей строки (`FIELD_CUSTOM`): её данные —
    # name, unit, qty, price, basis_kind, basis, placement. У обычной
    # правки — None (и в JSON не пишется, core/persistence.py).
    custom: dict | None = None


class OverrideLog:
    """Список правок оператора для одной сметы (одна на сессию браузера)."""

    def __init__(self) -> None:
        self._edits: list[Edit] = []

    def apply(
        self,
        line_id: str,
        field: str,
        old_value: float,
        new_value: float,
        reason: str | None = None,
    ) -> None:
        """Добавляет правку в конец истории.

        Не проверяет, что `line_id`/`field` существуют в какой-либо
        смете — эту смету здесь никто не передаёт (apply можно
        вызывать до того, как смета вообще посчитана). Проверка того,
        что правка ссылается на реально существующую строку, происходит
        в `effective_estimate` в момент применения.

        `old_value` — значение поля на момент правки, известное
        вызывающему коду (интерфейсу, который показывает текущую
        эффективную строку оператору) — оно нужно только для истории
        правок, на сам расчёт не влияет.
        """
        self._edits.append(Edit(line_id, field, old_value, new_value, reason))

    @classmethod
    def from_edits(cls, edits) -> "OverrideLog":
        """Журнал из сохранённого списка правок (версия, черновик) — как
        есть, вместе с записями своих строк."""
        log = cls()
        log._edits = [dataclasses.replace(edit) for edit in edits]
        return log

    # --- своя строка (Э7) ---

    def custom_lines(self) -> dict[str, dict]:
        """Текущие свои строки журнала `{line_id: данные}` в порядке
        добавления (после всех добавлений, изменений и удалений)."""
        result: dict[str, dict] = {}
        for edit in self._edits:
            if edit.field == FIELD_CUSTOM and edit.custom is not None:
                result[edit.line_id] = dict(edit.custom)
            elif edit.field == FIELD_CUSTOM_DELETE:
                result.pop(edit.line_id, None)
        return result

    def _next_custom_id(self) -> str:
        used = {edit.line_id for edit in self._edits if is_custom_id(edit.line_id)}
        number = 1
        while f"{CUSTOM_PREFIX}{number}" in used:
            number += 1
        return f"{CUSTOM_PREFIX}{number}"

    def add_custom_line(self, data: dict, reason: str | None = None) -> str:
        """Добавляет свою строку, возвращает её id (`custom.<n>`; номер
        удалённой строки повторно не выдаётся). Неполные данные — ValueError."""
        clean = validate_custom(data)
        line_id = self._next_custom_id()
        self._edits.append(
            Edit(line_id, FIELD_CUSTOM, 0.0, clean["qty"] * clean["price"], reason, clean)
        )
        return line_id

    def update_custom_line(self, line_id: str, data: dict, reason: str | None = None) -> None:
        """Меняет свою строку целиком (новые данные). Нет такой строки —
        KeyError."""
        current = self.custom_lines()
        if line_id not in current:
            raise KeyError(f"Своей строки {line_id!r} нет")
        clean = validate_custom(data)
        old = current[line_id]
        self._edits.append(
            Edit(line_id, FIELD_CUSTOM, old["qty"] * old["price"],
                 clean["qty"] * clean["price"], reason, clean)
        )

    def remove_custom_line(self, line_id: str, reason: str | None = None) -> None:
        """Удаляет свою строку (запись в журнале — удаление отменяется
        «Отменить последнюю правку»). Нет такой строки — KeyError."""
        current = self.custom_lines()
        if line_id not in current:
            raise KeyError(f"Своей строки {line_id!r} нет")
        old = current[line_id]
        self._edits.append(
            Edit(line_id, FIELD_CUSTOM_DELETE, old["qty"] * old["price"], 0.0, reason)
        )

    def undo(self) -> bool:
        """Убирает последнюю правку. Возвращает False, если список пуст."""
        if not self._edits:
            return False
        self._edits.pop()
        return True

    def history(self) -> list[Edit]:
        """Список применённых правок в порядке применения (для UI)."""
        return list(self._edits)

    def effective_estimate(self, base: dict[str, EstimateLine]) -> dict[str, EstimateLine]:
        """Строит новый словарь строк сметы с применёнными правками.

        `base` не мутируется: результат состоит из копий строк
        `base`, к которым по очереди применены все правки из истории.
        Правка на несуществующий `line_id` (строка могла исчезнуть,
        если оператор поменял исходные данные) или несуществующее
        `field` молча пропускается — она могла устареть, это не
        повод падать.

        Строка «доля от базы» (`kind` B/C, сумма = `coefs["rate"]` ×
        сумма базовых строк): правимое поле — `rate` (пишется в копию
        `coefs`); правка `qty`/`price` у такой строки пропускается — эти
        поля в её сумме не участвуют. У строки другого вида `rate`
        пропускается.
        """
        result = dict(base)
        for edit in self._edits:
            # Своя строка: добавить/заменить или удалить (только свою —
            # строку модуля так удалить нельзя).
            if edit.field == FIELD_CUSTOM:
                if edit.custom is not None and is_custom_id(edit.line_id):
                    result[edit.line_id] = custom_line(edit.line_id, edit.custom)
                continue
            if edit.field == FIELD_CUSTOM_DELETE:
                if is_custom_id(edit.line_id):
                    result.pop(edit.line_id, None)
                continue
            line = result.get(edit.line_id)
            if line is None:
                continue
            rate_line = getattr(line, "kind", None) in RATE_KINDS
            if edit.field == "rate":
                if not rate_line:
                    continue
                coefs = {**line.coefs, "rate": edit.new_value}
                result[edit.line_id] = dataclasses.replace(line, coefs=coefs)
                continue
            if not hasattr(line, edit.field) or (rate_line and edit.field in ("qty", "price")):
                continue
            result[edit.line_id] = dataclasses.replace(line, **{edit.field: edit.new_value})
        return result
