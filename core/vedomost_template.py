"""Шаблон Excel «Ведомость объёмов работ» раздела и выгрузка текущего ввода в него
(03.11.2026, трек «импорт ведомости»; чтение — `core/vedomost_import.py`).

Книга из трёх листов:
- «Ведомость» — плоская таблица «Вид работ | Единица | Объём | [поля строки
  группы] | Примечание». Вид работ — выпадающий список (проверка данных Excel по
  листу «Справочник»), единица подставляется формулой по виду работ; заголовки и
  колонка единицы защищены, ввод — вид работ, объём, поля строки, примечание.
  Защита листа без пароля — снять можно штатно («Рецензирование → Снять защиту»).
  Над таблицей — метка раздела («код раздела: geology»): по ней программа
  отличает файл другого раздела.
- «Справочник» — все виды работ раздела с единицами и подсказкой, как вводить;
  варианты полей строки (условие съёмки у геодезии).
- «Как заполнить» — инструкция простым языком.

Оформление — дизайн-код компании (`docs/design/firm-design-code/tokens.json`, светлая
тема), так же, как шаблон ставок (`tools/make_stavki_template.py`).
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from core.vedomost_import import (
    COL_KEY,
    COL_NOTE,
    COL_QTY,
    COL_UNIT,
    COL_WORK,
    SECTION_MARK,
    SHEET_CATALOG,
    SHEET_HELP,
    SHEET_VEDOMOST,
    TEMPLATE_VERSION,
    Catalog,
    ExportRow,
)

ROOT = Path(__file__).resolve().parent.parent
LOGO_PATH = ROOT / "app" / "theme" / "logos" / "firm-wordmark-emerald.png"  # копия из дизайн-кода; попадает и в exe

HEADER_ROW = 6
FIRST_ROW = HEADER_ROW + 1
INPUT_ROWS = 200          # пустых строк для ввода в шаблоне
EXTRA_ROWS = 50           # запас пустых строк после выгруженных
CATALOG_FIRST_ROW = 4
# Фиксированная дата свойств книги: шаблон под git пересоздаётся без лишних различий.
FIXED_TIMESTAMP = datetime(2026, 11, 3)

# Код раздела → название раздела в шаблоне (код — как `module.code` и ключи
# app/ui.py::MODULES).
SECTION_TITLES = {
    "geology": "Инженерно-геологические изыскания",
    "geophysics": "Инженерно-геофизические изыскания",
    "geodesy": "Инженерно-геодезические изыскания",
    "ecology": "Инженерно-экологические изыскания",
    "hydromet": "Инженерно-гидрометеорологические изыскания",
}

# Дизайн-код компании (docs/design/firm-design-code/tokens.json, светлая тема).
EMERALD = "00726A"   # emerald-700
STEEL_100 = "E7ECEC"  # шапки таблиц
STEEL_200 = "D3DBDA"  # рамки
STEEL_400 = "788584"  # рамки полей ввода, служебный текст
STEEL_600 = "4A5654"  # второстепенный текст
INK = "0E1413"
FONT = "Arial"  # Golos Text по дизайн-коду есть не на каждом ПК — Arial везде

UNKNOWN_UNIT = "нет в списке"


def section_line(catalog: Catalog, section_title: str) -> str:
    return (f"Раздел: {section_title} · {SECTION_MARK}: {catalog.module_code} · "
            f"шаблон v{TEMPLATE_VERSION}")


def derived_text(catalog: Catalog) -> str:
    """«Что вводить» — по списку раздела: какие программа / камеральная обработка /
    отчёт в списке есть (их нужно указать самим), остальное программа добавит сама."""
    text = ("Указывайте все работы из списка, которые нужны: строки, которой нет в файле, "
            "в ведомости не будет (объём 0, «нет»). ")
    derived = catalog.derived_in_list()
    if derived:
        names = "; ".join(f"«{w.name}»" for w in derived)
        text += (f"В том числе — эти работы раздела есть в списке и сами не добавятся: {names}. ")
    text += ("Работы, которых в списке нет и которые следуют из введённых (например, камеральную "
             "обработку полевых работ, если её нет в списке), программа добавит сама по связям "
             "раздела. Работу вне сборника можно добавить на странице сметы «своей строкой» с "
             "основанием.")
    return text


def help_lines(catalog: Catalog, section_title: str) -> list[tuple[str, str]]:
    """(заголовок, текст) листа «Как заполнить»."""
    lines = [
        ("Что это", f"Ведомость объёмов работ раздела «{section_title}» для программы расчёта "
                    "бюджета изысканий. Заполненный файл загружается на странице «2. Ведомость "
                    "объёмов» (блок «Ведомость из Excel») — программа проверит его и поставит "
                    "объёмы в ведомость так же, как при ручном вводе."),
        ("Как заполнить", f"Лист «{SHEET_VEDOMOST}»: в каждой строке выберите вид работ из "
                          "выпадающего списка и впишите объём числом (без единиц и пробелов). "
                          "Единица подставится сама — объём указывайте именно в ней. "
                          "«Примечание» — по желанию, для себя и проверяющего (в расчёт не идёт)."),
        ("Каждый вид — один раз", "Один вид работ — одна строка. Если ведомость собрана по "
                                  "сооружениям и вид повторяется, сложите объёмы или при загрузке "
                                  "отметьте «Сложить одинаковые строки»."),
        ("Отметки «да/нет»", "У видов работ с единицей «да/нет» объём 1 — работа есть (выбрано), "
                             "0 или нет строки — работы нет."),
        ("Что вводить", derived_text(catalog)),
        ("Пустые строки", "Пустые строки пропускаются. Не указанный вид работ — объём 0."),
        ("Защита листа", "Заголовки и колонка «Единица» защищены от случайной правки. Защита без "
                         "пароля: «Рецензирование → Снять защиту листа», если нужно."),
        ("Не меняйте", f"Строку «{SECTION_MARK}: {catalog.module_code}» над таблицей, названия "
                       "колонок и скрытую служебную колонку ключа — по ним программа узнаёт раздел "
                       "и читает файл."),
    ]
    if catalog.params:
        headers = ", ".join(f"«{p.header}»" for p in catalog.params)
        lines.insert(4, ("Колонки строки", f"{headers} заполняются только у видов работ "
                         "повторяющихся групп (съёмка, планы); у остальных — пусто. Пустое условие "
                         "съёмки — обычные условия."))
    return lines


def build_workbook(catalog: Catalog, section_title: str,
                   rows: list[ExportRow] | None = None) -> Workbook:
    """Книга шаблона; `rows` — выгружаемый ввод (None/[] — пустой шаблон)."""
    rows = rows or []
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_VEDOMOST
    cat_ws = wb.create_sheet(SHEET_CATALOG)
    help_ws = wb.create_sheet(SHEET_HELP)

    thin = Side(style="thin", color=STEEL_200)
    strong = Side(style="thin", color=STEEL_400)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    input_border = Border(left=strong, right=strong, top=strong, bottom=strong)
    wrap = Alignment(wrap_text=True, vertical="top")
    unlocked = Protection(locked=False)
    white = PatternFill("solid", fgColor="FFFFFF")
    head_fill = PatternFill("solid", fgColor=STEEL_100)

    def put(sheet, row, col, value, **font):
        cell = sheet.cell(row=row, column=col, value=value)
        cell.font = Font(name=FONT, color=font.pop("color", INK), size=font.pop("size", 10), **font)
        cell.alignment = wrap
        return cell

    # --- «Справочник»: виды работ, затем варианты полей строки ---
    put(cat_ws, 1, 1, f"Справочник видов работ — {section_title}", size=14, bold=True, color=EMERALD)
    put(cat_ws, 2, 1, "Список выводится из полей ввода раздела в программе; правится только "
                      "программой (tools/make_vedomost_templates.py).", color=STEEL_600)
    cat_headers = [COL_WORK, COL_UNIT, "Как вводить", "Поле в программе", COL_KEY]
    for col, title in enumerate(cat_headers, start=1):
        cell = put(cat_ws, CATALOG_FIRST_ROW - 1, col, title, bold=True)
        cell.fill, cell.border = head_fill, border
    for i, work in enumerate(catalog.work_types):
        r = CATALOG_FIRST_ROW + i
        put(cat_ws, r, 1, work.name)
        put(cat_ws, r, 2, work.unit, color=STEEL_600)
        put(cat_ws, r, 3, work.hint, color=STEEL_600)
        put(cat_ws, r, 4, work.form_label, color=STEEL_400, size=8)
        put(cat_ws, r, 5, work.key, color=STEEL_400, size=8)
        for col in range(1, 6):
            cat_ws.cell(row=r, column=col).border = border
    last_work_row = CATALOG_FIRST_ROW + len(catalog.work_types) - 1
    work_range = f"'{SHEET_CATALOG}'!$A${CATALOG_FIRST_ROW}:$A${last_work_row}"
    lookup_range = f"'{SHEET_CATALOG}'!$A${CATALOG_FIRST_ROW}:$E${last_work_row}"
    for col, width in zip("ABCDE", (70, 16, 26, 60, 24)):
        cat_ws.column_dimensions[col].width = width
    option_ranges: dict[str, str] = {}
    col = 6
    for param in catalog.params:
        if param.type != "choice":
            continue
        letter = get_column_letter(col)
        cell = put(cat_ws, CATALOG_FIRST_ROW - 1, col, param.header, bold=True)
        cell.fill, cell.border = head_fill, border
        labels = param.all_labels()
        for i, label in enumerate(labels):
            put(cat_ws, CATALOG_FIRST_ROW + i, col, label).border = border
        option_ranges[param.header] = (f"'{SHEET_CATALOG}'!${letter}${CATALOG_FIRST_ROW}:"
                                       f"${letter}${CATALOG_FIRST_ROW + len(labels) - 1}")
        cat_ws.column_dimensions[letter].width = 60
        col += 1
    cat_ws.freeze_panes = cat_ws.cell(row=CATALOG_FIRST_ROW, column=1)
    cat_ws.protection.sheet = True
    cat_ws.sheet_view.showGridLines = False

    # --- «Ведомость» ---
    columns = catalog.columns()
    widths = {COL_WORK: 70, COL_UNIT: 14, COL_QTY: 14, COL_NOTE: 40}
    for index, title in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(index)].width = widths.get(title, 30)
    last_col = len(columns)
    ws.row_dimensions[1].height = 48
    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH))
        logo.width, logo.height = 154, 42
        ws.add_image(logo, "A1")

    def merged(row, value, height=None, **font):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
        put(ws, row, 1, value, **font)
        if height:
            ws.row_dimensions[row].height = height

    merged(2, f"Ведомость объёмов работ — {section_title}", 26, size=16, bold=True, color=EMERALD)
    merged(3, section_line(catalog, section_title), None, size=8, color=STEEL_400)
    merged(4, "Выберите вид работ из списка, впишите объём числом в указанной единице. Каждый "
              "вид — одной строкой. Пустые строки пропускаются. Подробно — лист «Как заполнить».",
           30, color=STEEL_600)

    col_index = {title: i for i, title in enumerate(columns, start=1)}
    for title, i in col_index.items():
        cell = put(ws, HEADER_ROW, i, title, bold=True)
        cell.fill, cell.border = head_fill, border
    ws.row_dimensions[HEADER_ROW].height = 30

    work_dv = DataValidation(type="list", formula1=f"={work_range}", allow_blank=True,
                             showErrorMessage=True, errorTitle="Вид работ не из списка",
                             error="Выберите вид работ из выпадающего списка (лист «Справочник»).")
    qty_dv = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0",
                            allow_blank=True, showErrorMessage=True, errorTitle="Нужно число",
                            error="Введите число не меньше 0 (без единиц измерения и пробелов).")
    ws.add_data_validation(work_dv)
    ws.add_data_validation(qty_dv)
    param_dvs = {}
    for param in catalog.params:
        if param.type == "choice":
            dv = DataValidation(type="list", formula1=f"={option_ranges[param.header]}",
                                allow_blank=True, showErrorMessage=True,
                                errorTitle="Значение не из списка",
                                error=f"Выберите «{param.header}» из списка.")
        else:
            dv = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0",
                                allow_blank=True, showErrorMessage=True, errorTitle="Нужно число",
                                error="Введите число не меньше 0.")
        ws.add_data_validation(dv)
        param_dvs[param.header] = dv

    total = max(INPUT_ROWS, len(rows) + EXTRA_ROWS)
    input_cols = [col_index[COL_WORK], col_index[COL_QTY], col_index[COL_NOTE],
                  *[col_index[p.header] for p in catalog.params]]
    for offset in range(total):
        r = FIRST_ROW + offset
        work_cell = f"{get_column_letter(col_index[COL_WORK])}{r}"
        for c in range(1, last_col + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = Font(name=FONT, size=10, color=INK)
            cell.alignment = wrap
            if c in input_cols:
                cell.protection = unlocked
                cell.fill = white
                cell.border = input_border
            else:
                cell.border = border
        unit_cell = ws.cell(row=r, column=col_index[COL_UNIT])
        unit_cell.value = (f'=IF({work_cell}="","",IFERROR(VLOOKUP({work_cell},{lookup_range},2,FALSE),'
                           f'"{UNKNOWN_UNIT}"))')
        unit_cell.font = Font(name=FONT, size=10, color=STEEL_600)
        ws.cell(row=r, column=col_index[COL_QTY]).number_format = "#,##0.####"
        key_cell = ws.cell(row=r, column=col_index[COL_KEY])
        key_cell.value = (f'=IF({work_cell}="","",IFERROR(VLOOKUP({work_cell},{lookup_range},5,FALSE),'
                          '""))')
        key_cell.font = Font(name=FONT, size=8, color=STEEL_400)
    for title, dv in [(COL_WORK, work_dv), (COL_QTY, qty_dv),
                      *[(p.header, param_dvs[p.header]) for p in catalog.params]]:
        letter = get_column_letter(col_index[title])
        dv.add(f"{letter}{FIRST_ROW}:{letter}{FIRST_ROW + total - 1}")

    for offset, item in enumerate(rows):
        r = FIRST_ROW + offset
        ws.cell(row=r, column=col_index[COL_WORK], value=item.work.name)
        # выгрузка: ключ — значением (Excel формулу не считал), по нему файл читается и
        # после переименования вида работ в программе
        ws.cell(row=r, column=col_index[COL_KEY], value=item.work.key)
        qty = item.qty
        if isinstance(qty, float) and qty.is_integer() and item.work.integer:
            qty = int(qty)
        ws.cell(row=r, column=col_index[COL_QTY], value=qty)
        for header, value in item.params.items():
            ws.cell(row=r, column=col_index[header], value=value)

    ws.column_dimensions[get_column_letter(col_index[COL_KEY])].hidden = True
    ws.protection.sheet = True
    ws.protection.formatColumns = False
    ws.protection.formatRows = False
    ws.freeze_panes = ws.cell(row=FIRST_ROW, column=1)
    ws.print_title_rows = f"{HEADER_ROW}:{HEADER_ROW}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.sheet_view.showGridLines = False

    # --- «Как заполнить» ---
    put(help_ws, 1, 1, f"Как заполнить ведомость — {section_title}", size=14, bold=True, color=EMERALD)
    help_ws.column_dimensions["A"].width = 26
    help_ws.column_dimensions["B"].width = 100
    for i, (title, text) in enumerate(help_lines(catalog, section_title), start=3):
        put(help_ws, i, 1, title, bold=True)
        put(help_ws, i, 2, text)
        help_ws.row_dimensions[i].height = 15 * max(1, -(-len(text) // 95)) + 4
    put(help_ws, i + 2, 1, f"Шаблон v{TEMPLATE_VERSION}. Сделан программой "
                           "tools/make_vedomost_templates.py; чтение — core/vedomost_import.py.",
        size=8, color=STEEL_400)
    help_ws.protection.sheet = True
    help_ws.sheet_view.showGridLines = False

    wb.active = 0
    wb.properties.creator = "BUDGET_PIR"
    wb.properties.title = f"Ведомость объёмов работ — {section_title}"
    wb.properties.created = FIXED_TIMESTAMP
    wb.properties.modified = FIXED_TIMESTAMP
    return wb


def workbook_bytes(catalog: Catalog, section_title: str, rows: list[ExportRow] | None = None) -> bytes:
    """Книга в байтах — для кнопки «Скачать» интерфейса."""
    buffer = io.BytesIO()
    build_workbook(catalog, section_title, rows).save(buffer)
    return buffer.getvalue()


def template_file_name(module_code: str) -> str:
    return f"vedomost_{module_code}.xlsx"
