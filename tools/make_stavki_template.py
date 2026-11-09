"""Шаблон Excel «Ставки себестоимости» для бухгалтерии (03.11.2026; все
разделы — с 03.11.2026, трек 3 «два дня»).

Минимальный бюджет (себестоимость всех пяти разделов, `core/cost.py` и
`modules/<раздел>/cost.py`) не калиброван: его ставки — допущения
(`data/norms/*.json`). Этот скрипт делает пустой шаблон, в который
бухгалтерия вписывает фактические ставки компании текущего года;
`tools/import_stavki.py` переносит заполненный шаблон в локальный слой норм
`data/local/norms_firm.json` (не в git).

Перечень статей (`ARTICLES`) — ровно то, что использует расчёт
себестоимости. У статьи — раздел локального слоя (`Article.target`):
"cost_common" — ОБЩИЕ ставки, одни на все разделы (оклады по должностям,
начисления на ФОТ, содержание партии, проезд, топливо, резерв);
"geology_cost" и т.п. — особые ставки раздела (буровая партия, нормы
выработки). Так ответ бухгалтерии один раз закрывает все разделы. Нормы
файлов `data/norms/*.json`, которых в шаблоне нет, перечислены в
`EXCLUDED_NORMS` с причиной — тест `tests/test_import_stavki.py` следит,
чтобы новая норма не потерялась.

Запуск (из корня проекта):
    .venv/Scripts/python.exe -m tools.make_stavki_template            # docs/templates/stavki_sebestoimosti.xlsx
    .venv/Scripts/python.exe -m tools.make_stavki_template --out ФАЙЛ --hints
`--hints` добавляет колонку «Сейчас в программе (допущение)» — seed-значения
из `data/norms/*.json` (они и так в git; значения локального слоя
`data/local/` туда НЕ попадают).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.pagebreak import Break

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "docs" / "templates" / "stavki_sebestoimosti.xlsx"
LOGO_PATH = ROOT / "docs" / "design" / "firm-design-code" / "logos" / "firm-wordmark-emerald.png"
NORMS_NAME = "geology_cost"
COMMON = "cost_common"

SHEET_TITLE = "Ставки"
TEMPLATE_VERSION = 2  # растёт, когда меняется перечень статей или колонки (2 — все разделы)


@dataclass(frozen=True)
class Article:
    """Статья шаблона. `section` — раздел записи локального слоя: "inputs"
    (значение по умолчанию поля ввода) или "norms" (норма `data/norms/*.json`);
    `target` — раздел локального слоя: "cost_common" (общие ставки всех
    разделов) или "<раздел>_cost". Значение в норме = значение в шаблоне ×
    `scale` (проценты → доля). Ключ (`key`) уникален во всём шаблоне."""

    key: str
    section: str
    label: str
    unit: str
    hint: str = ""
    scale: float = 1.0
    target: str = NORMS_NAME


SALARY_HINT = ("Начисленная зарплата одного человека за месяц С районным коэффициентом и "
               "северной надбавкой (программа их отдельно не добавляет), без страховых взносов.")
PERCENT = 0.01

# (заголовок группы, статьи); пояснение ко всей группе — GROUP_NOTES.
GROUP_SALARY_COMMON = "Оклады по должностям — общие для всех разделов (зарплата в месяц на одного человека)"
GROUP_SALARY_GEOLOGY = "Оклады буровой партии — только геология"
C = COMMON
ARTICLES: list[tuple[str, list[Article]]] = [
    (GROUP_SALARY_COMMON, [
        Article("salary_field_lead", "inputs", "Ведущий инженер в поле (начальник отряда)", "руб./мес",
                target=C),
        Article("salary_field_engineer", "inputs", "Инженер в поле", "руб./мес", target=C),
        Article("salary_field_driver", "inputs", "Водитель", "руб./мес",
                "Для всех разделов, в т.ч. водители буровой партии геологии.", target=C),
        Article("salary_field_worker", "inputs", "Рабочий в поле (не буровой)", "руб./мес", target=C),
        Article("salary_office_chief", "inputs", "Главный специалист (камеральная группа)", "руб./мес",
                target=C),
        Article("salary_office_lead", "inputs", "Руководитель группы / ведущий инженер (камеральная)",
                "руб./мес", target=C),
        Article("salary_office_engineer", "inputs", "Инженер камеральной группы", "руб./мес",
                "Для всех разделов, в т.ч. камеральщики геологии.", target=C),
        Article("salary_office_technician", "inputs", "Техник камеральной группы", "руб./мес", target=C),
    ]),
    (GROUP_SALARY_GEOLOGY, [
        Article("salary_worker", "inputs", "Буровой рабочий (машинист, помощник)", "руб./мес"),
        Article("salary_itr", "inputs", "ИТР буровой партии (начальник партии, геолог)", "руб./мес"),
        Article("salary_cook", "inputs", "Повар", "руб./мес"),
    ]),
    ("Начисления на ФОТ (фонд оплаты труда) — все разделы", [
        Article("social_contributions_rate", "norms", "Страховые взносы (соцотчисления)", "% от ФОТ",
                "Сколько процентов к зарплате платит компании во внебюджетные фонды.", PERCENT, C),
        Article("taxes_rate", "norms", "Прочие налоги, относимые на объект", "% от ФОТ",
                "В программе — отдельная строка «Налоги» в процентах к ФОТ.", PERCENT, C),
        Article("overhead_rate", "norms", "Накладные расходы", "% от ФОТ",
                "Общехозяйственные расходы предприятия, отнесённые к ФОТ изысканий.", PERCENT, C),
    ]),
    ("Содержание полевой партии — все разделы", [
        Article("field_allowance_per_person_month", "norms", "Полевое довольствие", "руб./чел.-мес",
                "На одного человека в поле за месяц.", target=C),
        Article("housing_per_person_month", "norms", "Проживание (жильё)", "руб./чел.-мес",
                "На одного человека в поле за месяц.", target=C),
        Article("communication_per_person_month", "norms", "Связь", "руб./чел.-мес",
                "На одного человека в поле за месяц.", target=C),
        Article("household_per_person_month", "norms", "Бытовые расходы", "руб./чел.-мес",
                "На одного человека в поле за месяц.", target=C),
        Article("travel_per_person", "norms", "Проезд до объекта и обратно", "руб./чел. (туда и обратно)",
                "Одна поездка туда и обратно на одного человека.", target=C),
    ]),
    ("Материалы и услуги — геология", [
        Article("drilling_tools_per_pm", "norms", "Буровой инструмент", "руб./п.м.",
                "На 1 погонный метр бурения, в среднем по всем грунтам."),
        Article("spare_parts_per_pm", "norms", "Запчасти к буровой технике", "руб./п.м.",
                "На 1 погонный метр бурения."),
        Article("external_lab_per_sample", "norms", "Сторонние лабораторные испытания грунтов",
                "руб./образец", "Оплата сторонней лаборатории за один образец грунта."),
        Article("lowboy_per_km_one_way", "norms", "Трал (перевозка буровой техники)",
                "руб./км в один конец", "Программа считает: 2 × расстояние × ставка × число машин."),
    ]),
    ("Материалы и услуги — экология", [
        Article("eco_lab_per_sample", "norms", "Сторонняя лаборатория: анализ одной пробы", "руб./проба",
                "Почва, вода, донные отложения — в среднем на одну пробу.", target="ecology_cost"),
        Article("eco_info_services", "norms", "Платные справки и исходные данные", "руб. на объект",
                "Справки уполномоченных органов на один объект.", target="ecology_cost"),
    ]),
    ("Топливо: цены (без НДС) — все разделы", [
        Article("fuel_price_ai92", "inputs", "Бензин АИ-92", "руб./л", target=C),
        Article("fuel_price_diesel", "inputs", "Дизельное топливо", "руб./л", target=C),
    ]),
    ("Топливо: нормы расхода (утверждённые компании) — все разделы", [
        Article("fuel_uaz_l_per_100km", "norms", "УАЗ", "л/100 км, АИ-92", target=C),
        Article("fuel_kamaz_l_per_100km", "norms", "КАМАЗ", "л/100 км, АИ-92",
                "В программе КАМАЗ — на АИ-92; если он дизельный, напишите в комментарии.", target=C),
        Article("fuel_zil_l_per_100km", "norms", "ЗИЛ", "л/100 км, АИ-92", target=C),
        Article("fuel_ural_l_per_100km", "norms", "Урал", "л/100 км, дизель", target=C),
        Article("fuel_urb_l_per_hour", "norms", "Буровая установка УРБ-2А2 при бурении",
                "л/ч при бурении, дизель"),
    ]),
    ("Резерв — все разделы", [
        Article("contingency_rate", "norms", "Резерв на непредвиденные расходы",
                "% от (прямые + накладные + налоги)", "", PERCENT, C),
    ]),
    ("Производственные нормы геологии (уточнить у начальника отдела инженерной геологии)", [
        Article("drilling_rate_pm_per_brigade_month", "norms", "Выработка одной буровой бригады",
                "п.м./мес на одну буровую бригаду", "Погонных метров бурения за месяц."),
        Article("workers_per_brigade", "norms", "Буровых рабочих в бригаде", "чел. на бригаду"),
        Article("field_hours_per_month", "norms", "Часов работы буровой установки в месяц",
                "ч/мес (вахта)", "Нужно для расхода топлива буровой установкой."),
    ]),
    ("Производственные нормы остальных разделов (уточнить у начальников отделов)", [
        Article("survey_ha_per_person_month", "norms", "Геодезия: топосъёмка на одного человека в поле",
                "га съёмки на 1 чел. полевой группы в месяц",
                "Сколько гектаров снимает за месяц полевая группа в пересчёте на человека.",
                target="geodesy_cost"),
        Article("points_per_person_month", "norms", "Геофизика: точек наблюдений на одного человека",
                "физ. точек на 1 чел. полевой группы в месяц", "ВЭЗ и сейсморазведка вместе.",
                target="geophysics_cost"),
        Article("eco_ha_per_person_month", "norms", "Экология: обследование на одного человека",
                "га обследования на 1 чел. полевой группы в месяц", target="ecology_cost"),
    ]),
]

# Нормы data/norms/*.json, которых в шаблоне нет, — с причиной: (раздел, ключ) → почему.
EXCLUDED_NORMS = {
    (NORMS_NAME, "office_hours_per_month"): "справочно, в формуле не участвует",
    (NORMS_NAME, "profit_rate"): "по определению 0: себестоимость — без прибыли",
    (COMMON, "profit_rate"): "по определению 0: себестоимость — без прибыли",
    (NORMS_NAME, "depreciation"): "в расчёте — сумма на объект из поля ввода, а не ставка (см. таблицу ниже)",
    (NORMS_NAME, "business_trip"): "в расчёте — сумма на объект из поля ввода, а не ставка (см. таблицу ниже)",
    ("geodesy_cost", "min_field_party"): "только подсказка состава партии, в расчёте не участвует",
    ("geophysics_cost", "min_field_party"): "только подсказка состава партии, в расчёте не участвует",
    ("ecology_cost", "min_field_party"): "только подсказка состава партии, в расчёте не участвует",
    ("hydromet_cost", "field_party"): "только подсказка состава партии, в расчёте не участвует",
    ("hydromet_cost", "production_rate"): "нормы выработки нет — срок поля вводит оператор",
}
# Разделы локального слоя, нормы которых покрывает шаблон.
NORM_FILES = (COMMON, NORMS_NAME, "geodesy_cost", "geophysics_cost", "ecology_cost", "hydromet_cost")

# Статьи, которые просили, но расчёт их пока не использует: (статья, единица,
# почему). Импорт их не переносит — только сообщает, что они заполнены.
UNUSED_ARTICLES = [
    ("Машино-час своей техники (буровая установка, автомобили)", "руб./маш.-ч",
     "Технику программа считает через топливо и трал, машино-часа в расчёте нет."),
    ("Аренда техники или помещений", "руб./мес",
     "Отдельной статьи аренды в расчёте нет."),
    ("Амортизация буровой установки и автомобилей", "руб./мес",
     "В расчёте амортизация — сумма на объект, её вводит оператор."),
    ("Командировочные (суточные)", "руб./чел.-сут",
     "В расчёте — сумма на объект, её вводит оператор."),
    ("Районный коэффициент и северная надбавка", "%",
     "Для сверки: в окладах выше они уже должны быть учтены."),
]

GROUP_NOTES = {GROUP_SALARY_COMMON: SALARY_HINT, GROUP_SALARY_GEOLOGY: SALARY_HINT}

ALL_ARTICLES = [article for _title, group in ARTICLES for article in group]

# Тексты-метки, по которым импорт находит места в листе.
HEADERS = ["Статья", "Ключ", "Единица", "Значение 2026", "Источник (документ, приказ, счёт)",
           "Комментарий"]
HINT_HEADER = "Сейчас в программе (допущение)"
LABEL_WHO = "Кто заполнил (ФИО, должность)"
LABEL_DATE = "Дата заполнения"
UNUSED_TITLE = "Статьи, которых программа пока не использует — заполните, если знаете"

# Дизайн-код компании (docs/design/firm-design-code/tokens.json, светлая тема).
EMERALD = "00726A"   # emerald-700
STEEL_100 = "E7ECEC"  # шапки таблиц
STEEL_200 = "D3DBDA"  # рамки
STEEL_400 = "788584"  # рамки полей ввода
STEEL_600 = "4A5654"  # второстепенный текст
INK = "0E1413"
FONT = "Arial"  # Golos Text по дизайн-коду есть не на каждом ПК — Arial везде


def _row_height(texts: list[tuple[str, float]]) -> float:
    """Высота строки под самый длинный текст: (текст, ширина колонки в символах)."""
    lines = 1
    for value, width in texts:
        chars = max(1, int(width * 1.1))
        lines = max(lines, sum(max(1, -(-len(part) // chars)) for part in str(value).split("\n")))
    return 13.5 * lines + 4


def _seed_norms() -> dict:
    """Seed-допущения по разделам локального слоя: раздел → {ключ: норма}."""
    from core.cost import load_cost_norms

    return {name: load_cost_norms(name, use_local=False) for name in NORM_FILES}


def _fmt(value: float) -> str:
    text = f"{value:,.4f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")
    return text


def seed_hint(article: Article, seed: dict) -> str:
    """Текущее seed-допущение в единицах шаблона ("" — нет допущения)."""
    norm = seed.get(article.target, {}).get(article.key)
    if article.section != "norms" or norm is None or norm.value is None:
        return "нет (вводит оператор)"
    return _fmt(norm.value / article.scale)


def build_workbook(hints: bool = False) -> Workbook:
    seed = _seed_norms() if hints else {}
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_TITLE
    columns = HEADERS + ([HINT_HEADER] if hints else [])
    last_col = len(columns)
    widths = [46, 24, 22, 16, 34, 34, 20]
    for index, width in enumerate(widths[:last_col], start=1):
        ws.column_dimensions[chr(64 + index)].width = width

    thin = Side(style="thin", color=STEEL_200)
    strong = Side(style="thin", color=STEEL_400)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    input_border = Border(left=strong, right=strong, top=strong, bottom=strong)
    wrap = Alignment(wrap_text=True, vertical="top")
    unlocked = Protection(locked=False)
    white = PatternFill("solid", fgColor="FFFFFF")
    head_fill = PatternFill("solid", fgColor=STEEL_100)

    def text(row, col, value, **font):
        cell = ws.cell(row=row, column=col, value=value)
        cell.font = Font(name=FONT, color=font.pop("color", INK), size=font.pop("size", 10), **font)
        cell.alignment = wrap
        return cell

    def merged(row, value, height=None, **font):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
        text(row, 1, value, **font)
        if height:
            ws.row_dimensions[row].height = height

    # --- шапка: логотип, заголовок, пояснение ---
    ws.row_dimensions[1].height = 48
    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH))
        logo.width, logo.height = 154, 42
        ws.add_image(logo, "A1")
    merged(2, "Ставки себестоимости компании на 2026 год — инженерные изыскания (все разделы)", 26,
           size=16, bold=True, color=EMERALD)
    merged(3, "Нужны фактические затраты компании, не цены для заказчика; без рентабельности и НДС.",
           24, size=13, bold=True)
    merged(4, "Как заполнить: впишите число в колонку «Значение 2026» в указанной единице и "
              "документ-основание в колонку «Источник» (приказ, штатное расписание, счёт, "
              "договор). Не знаете — оставьте строку пустой: программа продолжит считать по "
              "своему допущению. Служебные колонки «Ключ» и «Единица» не меняйте — по ним "
              "программа читает файл.", 44, color=STEEL_600)
    row = 6
    for label in (LABEL_WHO, LABEL_DATE):
        text(row, 1, label, bold=True)
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
        for col in range(2, 5):
            cell = ws.cell(row=row, column=col)
            cell.protection = unlocked
            cell.fill = white
            cell.border = input_border
        ws.cell(row=row, column=2).font = Font(name=FONT, size=10, color=INK)
        row += 1
    ws.cell(row=7, column=2).number_format = "DD.MM.YYYY"

    # --- таблица ставок ---
    row += 1
    header_row = row
    for col, title in enumerate(columns, start=1):
        cell = text(row, col, title, bold=True)
        cell.fill = head_fill
        cell.border = border
    ws.row_dimensions[row].height = 44
    validation = DataValidation(
        type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=True,
        showErrorMessage=True, errorTitle="Нужно число",
        error="Введите число не меньше 0 (без единиц измерения и пробелов).",
    )
    ws.add_data_validation(validation)
    for title, group in ARTICLES:
        row += 1
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
        cell = text(row, 1, title, bold=True, color=EMERALD, size=11)
        for col in range(1, last_col + 1):
            ws.cell(row=row, column=col).border = border
        ws.row_dimensions[row].height = 20
        if title in GROUP_NOTES:
            row += 1
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
            text(row, 1, GROUP_NOTES[title], color=STEEL_600, italic=True)
            for col in range(1, last_col + 1):
                ws.cell(row=row, column=col).border = border
            ws.row_dimensions[row].height = 18
        for article in group:
            row += 1
            label = article.label + (f"\n{article.hint}" if article.hint else "")
            text(row, 1, label)
            text(row, 2, article.key, color=STEEL_400, size=8)
            text(row, 3, article.unit, color=STEEL_600)
            for col in (4, 5, 6):
                cell = ws.cell(row=row, column=col)
                cell.protection = unlocked
                cell.fill = white
                cell.font = Font(name=FONT, size=10, color=INK)
                cell.alignment = wrap
            ws.cell(row=row, column=4).number_format = "#,##0.00##"
            validation.add(ws.cell(row=row, column=4))
            if hints:
                text(row, 7, seed_hint(article, seed), color=STEEL_600)
            for col in range(1, last_col + 1):
                ws.cell(row=row, column=col).border = input_border if col in (4, 5, 6) else border
            ws.row_dimensions[row].height = _row_height(
                [(label, widths[0]), (article.unit, widths[2])])

    # --- статьи, которых программа не использует ---
    row += 2
    ws.row_breaks.append(Break(id=row - 1))  # вторая таблица — с новой страницы при печати
    merged(row, UNUSED_TITLE, 20, bold=True, color=EMERALD, size=11)
    row += 1
    for col, title in enumerate(["Статья", "Почему не используется", "Единица", "Значение 2026",
                                 "Источник (документ, приказ, счёт)", "Комментарий"], start=1):
        cell = text(row, col, title, bold=True)
        cell.fill = head_fill
        cell.border = border
    for label, unit, reason in UNUSED_ARTICLES:
        row += 1
        text(row, 1, label)
        text(row, 2, reason, color=STEEL_600, size=9)
        text(row, 3, unit, color=STEEL_600)
        for col in (4, 5, 6):
            cell = ws.cell(row=row, column=col)
            cell.protection = unlocked
            cell.fill = white
            cell.font = Font(name=FONT, size=10, color=INK)
            cell.alignment = wrap
        validation.add(ws.cell(row=row, column=4))
        for col in range(1, 7):
            ws.cell(row=row, column=col).border = input_border if col in (4, 5, 6) else border
        ws.row_dimensions[row].height = _row_height(
            [(label, widths[0]), (reason, widths[1] * 0.95), (unit, widths[2])])
    row += 2
    merged(row, f"Шаблон v{TEMPLATE_VERSION}. Сделан программой tools/make_stavki_template.py; "
                "перенос в программу — tools/import_stavki.py.", color=STEEL_400, size=8)

    # --- защита структуры (без пароля), печать ---
    ws.protection.sheet = True
    ws.protection.formatColumns = False
    ws.protection.formatRows = False
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.print_title_rows = f"{header_row}:{header_row}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.sheet_view.showGridLines = False
    wb.properties.creator = "BUDGET_PIR"
    wb.properties.title = "Ставки себестоимости компании"
    return wb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Шаблон Excel «Ставки себестоимости» для бухгалтерии")
    parser.add_argument("--out", type=Path, default=TEMPLATE_PATH, help="куда сохранить .xlsx")
    parser.add_argument("--hints", action="store_true",
                        help="колонка с текущими допущениями программы (seed, из git)")
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    build_workbook(hints=args.hints).save(args.out)
    print(f"Шаблон сохранён: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
