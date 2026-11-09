"""Экспорт сметы в Excel."""

from __future__ import annotations

import re
from typing import Any, Mapping

from openpyxl import Workbook

from core.excel_text import append_row
from core.formatting import excel_number_format
from core.models import PLACEMENT_LABELS, EstimateLine, calculate_amount, is_after_index
from core.overrides import display_basis

HEADERS = ["id", "Наименование", "Ед.", "Кол-во", "Цена", "Сумма", "Обоснование"]
# Колонки пересчёта в текущие цены — добавляются, когда передан итог
# (`totals`, core/pricing.py::TotalWithVat), Э3 дорожной карты.
PRICE_HEADERS = ["Индекс", "Сумма в текущих ценах", "НДС", "Итого с НДС"]

# Колонка "Кол-во" — 4-я по счёту в HEADERS (1-indexed, как ожидает openpyxl).
QTY_COLUMN = 4
# Колонка "Сумма" — сюда же пишутся строки итогов под таблицей.
AMOUNT_COLUMN = 6

VOLUME_SHEET_TITLE = "Ведомость объёмов"
VOLUME_HEADERS = ["№", "id", "Наименование", "Ед.", "Объём", "Источник", "Основание"]
INFO_SHEET_TITLE = "Сведения"

# Подписи строк итога под таблицей сметы (порядок эталонной сметы:
# база → с индексацией → НДС → всего с НДС, см. core/pricing.py).
TOTAL_BASE = "Итого в базовых ценах"
TOTAL_INDEXED = "Итого в текущих ценах (с индексацией)"
TOTAL_VAT = "НДС"
TOTAL_WITH_VAT = "Всего с НДС"
# Свои строки оператора (Э7): колонка места в смете (только если такие
# строки есть) и строка итога «после индекса».
PLACEMENT_HEADER = "Место в смете"
TOTAL_AFTER_INDEX = "Работы вне сборника после индекса (в текущих ценах)"


def export_file_name(kind: str, name: str | None, calc_date: str) -> str:
    """Имя файла выгрузки: «<вид>_<шифр или имя>_<дата>.xlsx». Символы,
    недопустимые в имени файла Windows, заменяются на «_»."""
    parts = [kind]
    if name:
        parts.append(name)
    parts.append(calc_date)
    raw = "_".join(parts)
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).strip(" .")
    return f"{safe}.xlsx"


def export_to_excel(
    estimate: dict[str, EstimateLine],
    path: Any,
    cache: dict[str, float] | None = None,
    totals: Any = None,
    volumes: Mapping[str, Any] | None = None,
    info: Mapping[str, Any] | None = None,
    source_labels: Mapping[str, str] | None = None,
) -> None:
    """Смета на первый лист.

    `cache` — необязательный словарь для мемоизации
    `calculate_amount` (см. core/models.py). Если не передан — экспорт
    создаёт свой на время этого вызова.

    `totals` — итог `core/pricing.py::calculate_total_with_vat` той же
    сметы: тогда у каждой строки есть колонки «Индекс», «Сумма в текущих
    ценах», «НДС», «Итого с НДС», а под таблицей — строки итога (база →
    с индексацией → НДС → всего с НДС; значения — из `totals`, те же, что
    видит оператор) и предупреждения итога. Индекс/НДС не найдены —
    ячейки пустые, не 0.

    `volumes` — строки утверждённой ведомости (`VolumeLine`): второй лист
    «Ведомость объёмов». `info` — пары «подпись: значение» на лист
    «Сведения» (имя сметы, модуль, дата расчёта…).

    Свои строки оператора (Э7, `EstimateLine.placement`): основание — в
    колонке «Обоснование» («По прайсу: …»), место в смете — в последней
    колонке «Место в смете» (появляется, только если такие строки есть).
    Строка «после индекса» уже в текущих ценах: индекс и «Сумма» (базовые
    цены) у неё пустые, её сумма — в «Сумме в текущих ценах» (пусто, если
    итога с индексацией нет — строгий режим без индекса); в «Итого в базовых
    ценах» она не входит, под таблицей — отдельной строкой перед итогом с
    индексацией."""
    if cache is None:
        cache = {}

    wb = Workbook()
    ws = wb.active
    ws.title = "Смета"
    has_placement = any(getattr(line, "placement", None) for line in estimate.values())
    headers = (
        HEADERS + (PRICE_HEADERS if totals is not None else [])
        + ([PLACEMENT_HEADER] if has_placement else [])
    )
    append_row(ws, headers)

    index = getattr(totals, "index", None)
    vat_rate = getattr(totals, "vat_rate", None)
    for line in estimate.values():
        amount = calculate_amount(line, estimate, cache)
        row_index = ws.max_row + 1
        after = is_after_index(line)
        # «Сумма» — в базовых ценах: у строки «после индекса» (она уже в
        # текущих ценах) пусто, иначе колонка не сходится с «Итого в базовых
        # ценах» (находка ревью Э7); её сумма — в «Сумме в текущих ценах».
        row = [line.id, line.name, line.unit, line.qty, line.price,
               None if after else amount, display_basis(line)]
        if totals is not None:
            if after:
                # без индекса (строгий режим) итога в текущих ценах нет —
                # и у строки «после индекса» текущая сумма не показывается
                line_index = None
                current = amount if getattr(totals, "indexed_total", None) is not None else None
            else:
                line_index = index
                current = amount * index if index is not None else None
            vat = current * vat_rate if current is not None and vat_rate is not None else None
            row += [line_index, current, vat, current + vat if vat is not None else None]
        if has_placement:
            row.append(PLACEMENT_LABELS.get(getattr(line, "placement", None), ""))
        append_row(ws, row)
        # Ячейка остаётся числом (не текстом) — только формат
        # отображения знаков после запятой меняется по единице
        # измерения этой конкретной строки (см. core/formatting.py).
        ws.cell(row=row_index, column=QTY_COLUMN).number_format = excel_number_format(line.unit)

    if totals is not None:
        append_row(ws, [])
        period = f" ({totals.index_period})" if getattr(totals, "index_period", None) else ""
        # «НДС 22 % — 425-ФЗ …, с 01.01.2026»: ставка и её основание, если
        # справочник его сообщает (core/references.py::VatRecord.label).
        vat_label = (
            getattr(totals, "vat_label", None)
            or (f"{TOTAL_VAT} {vat_rate:.0%}" if vat_rate is not None else TOTAL_VAT)
        )
        after_index = getattr(totals, "after_index_total", 0.0) or 0.0
        for label, value in (
            (TOTAL_BASE, totals.base_total),
            (f"Индекс пересчёта цен{period}", totals.index),
            *([(TOTAL_AFTER_INDEX, after_index)] if after_index else []),
            (TOTAL_INDEXED, totals.indexed_total),
            (vat_label, totals.vat_amount),
            (TOTAL_WITH_VAT, totals.total_with_vat),
        ):
            row = [None] * len(headers)
            row[1], row[AMOUNT_COLUMN - 1] = label, value
            append_row(ws, row)
        for warning in totals.warnings:
            append_row(ws, [None, f"Внимание: {warning}"])

    if volumes is not None:
        ws_volumes = wb.create_sheet(VOLUME_SHEET_TITLE)
        append_row(ws_volumes, VOLUME_HEADERS)
        for number, line in enumerate(volumes.values(), start=1):
            source = (source_labels or {}).get(line.source, line.source)
            append_row(ws_volumes, [number, line.id, line.name, line.unit, line.qty, source, line.basis])
            ws_volumes.cell(row=ws_volumes.max_row, column=5).number_format = excel_number_format(line.unit)

    if info:
        ws_info = wb.create_sheet(INFO_SHEET_TITLE)
        for label, value in info.items():
            append_row(ws_info, [label, value])

    wb.save(path)
