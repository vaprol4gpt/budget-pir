"""Запись строк в лист Excel так, чтобы текст оставался текстом.

openpyxl превращает любую строку, начинающуюся с «=», в формулу. Основания строк
ведомости («= число скважин»), имя своей строки, данные объекта — текст оператора и
модулей, а не формулы: книгу с такими «формулами» Excel либо не открывает, либо
показывает «#ИМЯ?» (аудит 05.11.2026, находка О-1). Управляющие символы в тексте
openpyxl не записывает вовсе (`IllegalCharacterError`) — они убираются.

Выгрузки программы (смета раздела, сводный бюджет, «КП») формул не содержат, поэтому
все их строки пишутся через `append_row`. Шаблон ведомости (`core/vedomost_template.py`)
формулы пишет намеренно и этим помощником не пользуется.
"""

from __future__ import annotations

from typing import Any, Iterable

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


def plain_text(value: Any) -> Any:
    """Строка без символов, которые нельзя записать в xlsx; не строка — как есть."""
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


def append_row(ws, row: Iterable[Any]) -> None:
    """`ws.append(row)`, но строка «= …» остаётся текстом: тип ячейки — строковый,
    с признаком «текст» (апостроф Excel), чтобы и при правке ячейки она не стала формулой."""
    values = [plain_text(value) for value in row]
    ws.append(values)
    if not any(isinstance(value, str) and value.startswith("=") for value in values):
        return
    for cell in ws[ws.max_row]:
        if cell.data_type == "f":
            cell.data_type = "s"
            cell.quotePrefix = True
