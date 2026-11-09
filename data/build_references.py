"""Скрипт наполнения справочника расценок из эталонных смет.

Читает лист "1.2.1_ИГИ" файла
`reference_data/Автодорога_эталон.xlsx` (плюс
листы геофизики/геодезии того же файла) и лист "№2.5_ИЭИ" файла
`reference_data/Карьер_эталон.xlsx` (экология — сборник
расценок для неё ни в одном документе заказчика не назван явно,
источник найден по структуре реальной сметы, см.
записи разработки; плюс лист "№1.4_ИЭИ" первого файла —
второй эталон экологии, см. ECOLOGY_ROAD_SHEET_NAME; плюс листы ИГМИ
"№2.4_ИГМИ"/"№1.3_ИГМИ" обоих файлов — цены в формулах ячеек, см.
HYDROMET_KARIER_SHEET_NAME) — и строит
SQLite-базу `data/references.sqlite` с таблицей цен (prices) и
таблицей индексов пересчёта цен (price_indices). База — сгенерированный
артефакт, в git не коммитится (см. .gitignore); сам этот скрипт и
исходный xlsx коммитятся.

Что распознаётся (см. записи разработки): только
строки, где основание (столбец F) распознаётся как "Таблица N, п.M"
(или "т.N п.M") ОДНОЗНАЧНО — без ссылки сразу на несколько пунктов.
Строки-продолжения без явного основания в этой же строке (например,
категории бурения II-VIII в исходном листе — там основание указано
только в первой строке блока, дальше "протянуто" визуально вручную
без формальной разметки) сознательно пропускаются: надёжно и
автоматически восстановить, к какому основанию они относятся,
нельзя без риска ошибиться в цифрах. Список пропущенных строк
печатается при запуске скрипта.

Индекс пересчёта цен (строка "Итого с учетом коэффициента инфляции
К-N на <квартал> квартал <год> года") публикуется письмом Минстроя
одним значением сразу на весь квартал (см.
записи разработки, "развилка про дату индекса
решена"), поэтому хранится диапазоном дат (period_start/period_end -
границы календарного квартала), а не одной точной датой.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import openpyxl

if __package__ in (None, ""):
    # Запуск файлом (`python data/build_references.py` из setup.bat,
    # launch_app.bat, build_release.bat): в sys.path — папка data/, а не корень,
    # и пакет проекта может быть не установлен в окружение. Корень — в путь,
    # чтобы импортировать core (С2: версия формата справочника — в core).
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.references import REFERENCES_SCHEMA_VERSION, ReferencesError, stale_reason  # noqa: E402

SHEET_NAME = "1.2.1_ИГИ"
SOURCE_XLSX = (
    Path(__file__).resolve().parent.parent
    / "reference_data"
    / "Автодорога_эталон.xlsx"
)
DB_PATH = Path(__file__).resolve().parent / "references.sqlite"
# Источник для строк листа 1.2.1_ИГИ, в основании которых сборник не
# назван («Таблица 17, п.1»). ДОПУЩЕНИЕ: лист ИГИ в целом составлен по
# СБЦ-99 (docs/etalon.md), и под этим источником эти позиции ищет модуль
# геологии. До 25.10.2026 подставлялся молча (DEFAULT_SOURCE); теперь
# такая позиция помечена `PriceEntry.source_assumed` и перечисляется в
# выводе сборки (список — записи разработки). Значения
# цен и ключи справочника не изменились.
GEOLOGY_ASSUMED_SOURCE = "СБЦ-99"
# Источники, которые в основании листа 1.2.1_ИГИ распознаются как есть.
# Любой другой текст перед «Таблица N» («Применительно», «СБЦ», «С.ц.1982г»)
# — нераспознанный источник: позиция пишется как раньше, но попадает в
# отчёт проблем сборки (см. collect_issues).
GEOLOGY_RECOGNIZED_SOURCES = {"СБЦ-99"}

# Проблемы сборки, разобранные вручную 25.10.2026 (Э4 дорожной карты):
# с ними справочник собирается с кодом 0; любая НОВАЯ проблема (другой
# конфликт ключа, другой нераспознанный источник, ненайденный якорь
# индекса/цены) — код возврата 1, сборка в setup.bat/build_release.bat
# останавливается. Из нераспознанных источников модуль геологии запрашивает
# один — «СБЦ» т.101 п.5 (содержание базы экспедиции, Э6а) — под тем
# источником, как он написан в листе. Конфликты ключей (до 30.10.2026 —
# ключ исключался, решение 18.09.2026, из-за чего монолиты скальных были без
# цены) с Э6а разведены по GEOLOGY_KEY_SPLITS.
REVIEWED_ISSUES = frozenset({
    # Конфликты ключей т.9 п.3, т.57 п.2, т.101 п.5 (были здесь до 30.10.2026,
    # ключи исключались) разведены признаком в категории — GEOLOGY_KEY_SPLITS
    # ниже (Э6а); конфликтами они больше не являются.
    # нераспознанный источник в основании листа ИГИ:
    ("source", "Применительно", "63", "5"),
    ("source", "Применительно", "63", "7"),
    ("source", "Применительно", "63", "8"),
    ("source", "Применительно", "65", "8"),
    ("source", "С.ц.1982г", "267", "6"),         # СЦиР-1982, геофизика внутри листа ИГИ
    ("source", "СЦиР-82", "410", "18"),
    ("source", "СБЦ", "101", "5"),
})

# Разведение конфликтующих ключей листа «1.2.1_ИГИ» (Э6а, 30.10.2026). Лист
# цитирует одной ссылкой «таблица, пункт» две разные работы с разной ценой;
# раньше такой ключ исключался из справочника целиком (решение 18.09.2026), и
# «Отбор монолитов скальных грунтов» оставался без цены. Теперь у каждой
# работы свой ключ: признак работы пишется в категорию (как у экологии —
# т.9 «field»/«kameral»). Правило: (источник, таблица, пункт, фрагмент
# наименования строки листа, категория) — первое совпавшее; позиция без
# категории в листе получает указанную категорию. None — ключ остаётся без
# категории (основная работа пункта). Каждое правило обязано сработать ровно
# один раз — иначе сборка сообщает проблему (лист поменялся, правило устарело).
#   т.9 п.3 — строка 19 «Инженерно-геологическая рекогносцировка» 47,2 (полевая)
#            и строка 131 «Камеральная обработка … рекогносцировки» 23,4 —
#            у пункта т.9 две графы (полевые / камеральные), как у экологии;
#   т.57 п.2 — строка 41 «Отбор монолитов скальных грунтов» 30,6 (т.57 —
#            отбор монолитов, п.1 — дисперсные 22,9, п.2 — скальные: основная
#            работа пункта) и строка 35 «Гидрогеологические наблюдения при
#            бурении» 1,6 — ссылка листа на т.57 п.2 для неё сомнительна (по
#            бумажному СБЦ-99 не сверено), цена — как в листе, категория
#            «гидронаблюдения»;
#   т.101 п.5 — строка 70 «Содержание базы экспедиции» 5 700 (основная) и
#            строка 72 «Содержание перевалочной базы» 3 600 (в листе объём 0).
GEOLOGY_KEY_SPLITS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("СБЦ-99", "9", "3", "камеральная обработка", "kameral"),
    ("СБЦ-99", "9", "3", "рекогносцировка", "field"),
    ("СБЦ-99", "57", "2", "гидрогеологические наблюдения", "гидронаблюдения"),
    ("СБЦ-99", "57", "2", "монолитов скальных", None),
    ("СБЦ", "101", "5", "перевалочной базы", "перевалочная база"),
    ("СБЦ", "101", "5", "базы экспедиции", None),
)


def apply_geology_key_splits(
    entries: list[PriceEntry],
) -> tuple[list[PriceEntry], list[str]]:
    """Развести конфликтующие ключи листа ИГИ по GEOLOGY_KEY_SPLITS.
    Возвращает (позиции, предупреждения о правилах, сработавших не ровно один
    раз — их сборка считает новыми проблемами)."""
    used = [0] * len(GEOLOGY_KEY_SPLITS)
    result: list[PriceEntry] = []
    for entry in entries:
        for n, (source, table, item, fragment, category) in enumerate(GEOLOGY_KEY_SPLITS):
            if (entry.category is None and (entry.source, entry.table, entry.item) == (source, table, item)
                    and fragment in entry.name.lower()):
                used[n] += 1
                if category is not None:
                    entry = PriceEntry(entry.source, entry.table, entry.item, category, entry.price,
                                       entry.basis, entry.name, entry.row, entry.source_assumed)
                break
        result.append(entry)
    warnings = [
        f"правило разведения ключа {rule[:3]} «{rule[3]}» сработало {count} раз (ожидалось 1)"
        for rule, count in zip(GEOLOGY_KEY_SPLITS, used) if count != 1
    ]
    return result, warnings


FIRST_DATA_ROW = 17
LAST_DATA_ROW = 157

# Геофизика (Этап 6 роадмапа — проверка универсальности каркаса на
# другом виде работ и другом сборнике расценок, см. описание проекта "Что
# дальше"). Лист устроен иначе, чем 1.2.1_ИГИ: столбец C — не
# категория, а само основание (номер таблицы/пункта + через "К="
# список дополнительных коэффициентов в одной ячейке), столбец D —
# единица измерения, E — объём, F — цена/ставка, G..J — числовые
# коэффициенты (см. GEOPHYSICS_SHEET_NAME докстринг
# extract_geophysics_price_entries). Поэтому геофизика разбирается
# ОТДЕЛЬНОЙ функцией, а не переиспользует extract_price_entries
# (риск случайно сломать уже проверенный разбор геологии не
# оправдан экономией кода — см. записи разработки).
GEOPHYSICS_SHEET_NAME = "№1.2.2_ИГФИ"
GEOPHYSICS_SOURCE = "СЦиР-1982"
GEOPHYSICS_FIRST_DATA_ROW = 17
GEOPHYSICS_LAST_DATA_ROW = 32

# Строка листа со сводным индексом пересчёта цен ("Итого с учетом
# коэффициента инфляции К-... на ... квартал ... года") — фиксированная
# позиция в этом конкретном эталонном листе, как FIRST_DATA_ROW/LAST_DATA_ROW.
INDEX_ROW = 160
# Эта смета целиком про инженерно-геологические работы (ИГИ) — индекс
# из неё относится к этому виду работ; совпадает с GeologyModule.code.
INDEX_WORK_TYPE = "geology"

# Индекс пересчёта цен для геофизики устроен на листе иначе, чем у
# геологии: нет отдельной строки с готовым текстом "... на N квартал
# ГГГГ года" в столбце A — вместо этого строка "23" (столбец A) даёт
# коэффициент прямо числом в столбце G (см. GEOPHYSICS_INDEX_ROW,
# GEOPHYSICS_INDEX_VALUE_COLUMN), а комментарий в столбце C
# ("...с инфляционным индексом 2 квартал 2025 г. Письмо Минстроя
# России №45358-ИФ/09 от 23.07.2026 г.") называет квартал ("2 квартал
# 2025 г."), НЕ СОВПАДАЮЩИЙ с датой того же письма Минстроя (23.07.2026,
# т.е. письмо физически не могло быть про 2 квартал 2025 — это то же
# письмо № 45358-ИФ/09 от 23.07.2026 г., что и у геологии, значит и
# квартал в нём тот же, "2 квартал 2025" в тексте — расхождение в
# исходном файле). Поэтому здесь НЕ парсится квартал/год из текста
# этой строки (это было бы гаданием при известном противоречии) — по
# явному решению пользователя переиспользуется период уже
# распознанного индекса геологии (тот же номер и дата письма Минстроя
# = тот же квартал действия, значение при этом своё, геофизическое,
# см. записи разработки).
GEOPHYSICS_INDEX_ROW = 34
GEOPHYSICS_INDEX_VALUE_COLUMN = 7
GEOPHYSICS_INDEX_WORK_TYPE = "geophysics"

# Геодезия (Этап 7 роадмапа — третья проверка универсальности каркаса,
# третий, ЕЩЁ ОДИН формат листа/сборника расценок). Лист "№1.1_ИГДИ",
# сборник "Справочник базовых цен... Инженерно-геодезические изыскания,
# 2004 г." (база цен 2004 г., НЕ 1991 — отсюда маленький индекс
# пересчёта ~7.19, это не ошибка, см. GEODESY_INDEX_ROW ниже). Формат
# ячейки "Обоснование стоимости" (столбец E) — НЕ "Таблица N, п.M" одной
# строкой, как у геологии, и НЕ "т.N п.M \n К=..." с коэффициентами в
# одной ячейке, как у геофизики, а составной код + путь к таблице +
# сама расценка "A" текстом в тысячах рублей, например:
# "СБЦи5.6_0-1-8-3-2-1 Часть I, Глава 1, Таблица 8 п.3\nA=6.426 тыс.руб; \n
# Количество = 8 (1 пункт)". Значение "A" (тыс.руб) переводится в рубли
# при сохранении в справочник (см. extract_geodesy_price_entries) —
# явно, чтобы не перепутать единицы с СБЦ-99/СЦиР-1982, где цена в
# справочнике уже в рублях.
GEODESY_SHEET_NAME = "№1.1_ИГДИ"
GEODESY_SOURCE = "СБЦи5.6"
GEODESY_FIRST_DATA_ROW = 19
GEODESY_LAST_DATA_ROW = 70

# Строка со сводным индексом пересчёта цен устроена на этом листе
# ТРЕТЬИМ отдельным способом (не как у геологии — целая фраза в
# столбце A; не как у геофизики — голое число в столбце коэффициентов):
# здесь это текст "Коэф - т 7,19 от п.6 - 7" в столбце F (расчёт
# стоимости) строки 74, а рядом, в столбце E — "Письмо Минстроя России
# №45358-ИФ/09 от 23.07.2026 г." — ТО ЖЕ САМОЕ письмо, что и у индекса
# геологии (INDEX_ROW выше) и геофизики (GEOPHYSICS_INDEX_ROW) — значит
# тот же период действия (III квартал 2026), переиспользуется буквально
# с уже распознанного индекса геологии, как и для геофизики (см.
# extract_geophysics_index_entries) — не парсится заново из этой
# строки, чтобы не зависеть ещё и от третьего похожего, но чуть иначе
# написанного текста.
# Второй лист геодезии — Карьер «№2.1_ИГДИ» (ECOLOGY_SOURCE_XLSX, 03.11.2026): тот же формат (программа «Адепт», цена «A=… тыс.руб» в
# основании, № пп — category), те же первая и последняя строки данных. Даёт
# пункты, которых нет на листе Автодороги: съёмка и план М 1:2000 (т.9 п.17,
# № пп 1.3/1.5 и 4.3/4.5), привязка т.48 п.3 (1.6), база экспедиции т.82 п.2
# (3.1), а также дубли уже известных пунктов под № пп Карьера (т.8 п.3/п.4,
# т.12 п.2 — 1.4/4.1/4.2/4.4). Позиция с тем же ключом и той же ценой, что у
# Автодороги, — дубль (пишется один раз, build_database); с другой ценой —
# конфликт, сборка о нём сообщит (на 03.11.2026 конфликтов нет).
GEODESY_KARIER_SHEET_NAME = "№2.1_ИГДИ"
GEODESY_INDEX_ROW = 74
GEODESY_INDEX_VALUE_COLUMN = 6
GEODESY_INDEX_WORK_TYPE = "geodesy"

# Экология (4-й модуль). В документах заказчика НИ ОДИН явно не
# называет сборник расценок для инженерно-экологических изысканий (в
# отличие от геологии/геофизики/геодезии, где сборник назван прямо) —
# нашёлся не по названию, а по факту структуры реальной сметы:
# "исходная книга смет Карьера" (не коммитился в
# reference_data/ раньше, лежал на Desktop пользователя),
# лист "№2.5_ИЭИ" — цитирует "Таблица N, п.M"/"Таблица N, §M" того же
# сборника СБЦ-99, что и геология (не отдельный сборник для экологии).
# См. записи разработки за тем, как найден, и почему это не
# гадание, а прямое чтение исходника.
ECOLOGY_SOURCE_XLSX = (
    Path(__file__).resolve().parent.parent
    / "reference_data"
    / "Карьер_эталон.xlsx"
)
ECOLOGY_SHEET_NAME = "№2.5_ИЭИ"
ECOLOGY_SOURCE = "СБЦ-99"
ECOLOGY_FIRST_DATA_ROW = 12
ECOLOGY_LAST_DATA_ROW = 95

# "Радиационное обследование участка" (Таблица 92, п.3) и "Измерение
# плотности потока радона" (Таблица 91, п.1) на этом листе цитируются
# ДВАЖДЫ с РАЗНОЙ ценой: один раз в разделе "Радиометрические работы"
# (полевая цена, строки 28/30), второй раз в разделе "Камеральные
# работы" (камеральная цена, строки 80/82) — без разделения по
# category build_database() исключила бы оба варианта целиком как
# конфликт одинакового ключа (см. build_database). Порог строки найден
# вручную по структуре ИМЕННО этого листа (граница раздела "IV этап –
# Камеральные работы"), не выводится автоматически.
ECOLOGY_KAMERAL_SECTION_START_ROW = 71
# Таблица 9 (рекогносцировка/почвенная), 10 (маршрутные наблюдения), 11
# (описание точек) — та же ситуация, что у Т92/Т91: один код цитируется
# и полевой строкой, и её камеральной парой с разной ценой (см.
# записи разработки). раньше они не были
# перечислены здесь и поэтому исключались build_database() целиком как
# конфликт. Пункты 1/4/3 — "при хорошей проходимости" (лист Карьера),
# 2/5/2 — "при удовлетворительной" (лист Автодороги ниже).
ECOLOGY_DUAL_PRICE_CODES = {
    ("92", "3"),
    ("91", "1"),
    ("9", "1"),
    ("9", "2"),
    ("9", "4"),
    ("9", "5"),
    ("10", "2"),
    ("10", "3"),
    ("11", "2"),
}

# Второй эталонный лист экологии — "№1.4_ИЭИ" объекта "Автодорога" (тот
# же файл SOURCE_XLSX, что у геологии/геофизики/геодезии). Нужен ради
# пунктов "при удовлетворительной проходимости" (Т9 п.2/п.5, Т10 п.2),
# которых нет на листе Карьера. Структура листа та же (столбцы A..H,
# раздел "IV этап – Камеральные работы" начинается со строки 73).
ECOLOGY_ROAD_SHEET_NAME = "№1.4_ИЭИ"
ECOLOGY_ROAD_FIRST_DATA_ROW = 13
ECOLOGY_ROAD_LAST_DATA_ROW = 97
ECOLOGY_ROAD_KAMERAL_SECTION_START_ROW = 73
# Строка 17 ("Разработка и составление запросов... 10% от предполевых
# работ", цена 0,1) ссылается на "Таблица 9, п.5" — тот же код, что у
# полевой почвенной рекогносцировки (4,33) строкой ниже. По названию и
# цене это доля, а не расценка Т9 п.5 — ошибочная ссылка в исходнике
# (см. записи разбора техотчёта). Без исключения конфликт
# выбросил бы из справочника настоящую полевую цену Т9 п.5.
ECOLOGY_ROAD_SKIP_ROWS = {17}

# Индекс пересчёта цен экологии на этом листе — ЦЕЛАЯ описательная
# строка в столбце B (не отдельная строка-заголовок, как у геологии, и
# не число в столбце коэффициентов, как у геофизики): "Итого с учетом
# инфляционного коэффициента (Индексы изменения сметной стоимости
# изыскательских работ к уровню цен по состоянию на 01.01.1991 на III
# квартал 2026 г.) - 81,95" — значение (81,95) и квартал (III 2026)
# СОВПАДАЮТ с уже распознанным индексом геологии (тот же сборник
# СБЦ-99, та же база цен 1991 г., см. INDEX_ROW выше) — это не
# переиспользование по аналогии с геофизикой/геодезией (те берут
# только ПЕРИОД, значение у них своё, из другого сборника), а
# самостоятельно распознанное совпадающее значение из текста ЭТОЙ
# строки — проверяется парсером заново, не копируется вслепую.
ECOLOGY_INDEX_ROW = 93
ECOLOGY_INDEX_VALUE_COLUMN = 2
ECOLOGY_INDEX_WORK_TYPE = "ecology"

ECOLOGY_BASIS_RE = re.compile(
    r"(?:Таблица|Табл\.?|т\.)\s*№?\s*(\d+)\s*,?\s*(?:п\.?|§)\s*(\d+[а-яА-Я]?)",
    re.IGNORECASE,
)

ECOLOGY_INDEX_RE = re.compile(
    r"на\s+([IVX]+)\s*квартал[а-я]*\s*(\d{4})\s*г\.?\)\s*-\s*([\d,]+)",
    re.IGNORECASE,
)

QUARTER_MONTHS = {"I": (1, 3), "II": (4, 6), "III": (7, 9), "IV": (10, 12)}


@dataclass
class PriceEntry:
    source: str
    table: str
    item: str
    category: str | None
    price: float
    basis: str
    name: str
    row: int
    # Источник не назван в основании и принят по листу
    # (GEOLOGY_ASSUMED_SOURCE) — ДОПУЩЕНИЕ, перечисляется в выводе сборки.
    source_assumed: bool = False


@dataclass
class IndexEntry:
    work_type: str
    period_start: str
    period_end: str
    value: float
    basis: str


@dataclass
class VatEntry:
    period_start: str
    period_end: str
    rate: float
    basis: str


# Ставка НДС по периодам (базовая ставка, НК РФ ст. 164 п. 3) — не данные
# эталона, а закон, поэтому задаётся здесь, а не извлекается из листа.
# Раньше в коде была одна ставка 20 % из текстовой подписи ячейки эталона,
# формулы эталонов и исходники 2026 г. считают 22 % (список вопросов В-16, закрыт
# 30.10.2026, записи разработки). До 01.01.2019 ставка была 18 % —
# такой период не внесён: на более раннюю дату ставка «не найдена», а не 20 %.
# Открытый конец периода — 9999-12-31.
VAT_OPEN_END = "9999-12-31"
VAT_RATES = [
    VatEntry("2019-01-01", "2025-12-31", 0.20,
             "НК РФ ст. 164 п. 3 в ред. 303-ФЗ от 03.08.2018"),
    VatEntry("2026-01-01", VAT_OPEN_END, 0.22,
             "425-ФЗ от 28.11.2025 (НК РФ ст. 164 п. 3)"),
]


# Индексы по кварталам — файл, который правит человек (этап Г карты, аудит 05.11.2026): без него
# индекс нового квартала можно было внести только правкой эталонного листа.
INDEX_TABLE_PATH = Path(__file__).resolve().parent / "indeksy.json"
INDEX_WORK_TYPES = ("geology", "geophysics", "geodesy", "ecology", "hydromet")
# Новый индекс не может отличаться от индекса того же раздела из эталона больше чем вдвое:
# запятая не на месте (8,195 вместо 81,95) давала бы итог в десять раз меньше без единого сигнала.
INDEX_MAX_RATIO = 2.0


class IndexTableError(ValueError):
    """Файл индексов по кварталам не соответствует формату: сборка останавливается."""


def load_index_table(path: Path | None = None) -> list[IndexEntry]:
    """Записи файла индексов; файла нет — пусто; ошибка записи — `IndexTableError` с её номером."""
    path = INDEX_TABLE_PATH if path is None else Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise IndexTableError(f"не читается файл индексов {path.name}: {exc}") from exc
    items = data.get("indices") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise IndexTableError(f"файл индексов {path.name}: ожидается объект с ключом «indices» — списком")
    entries = []
    for number, item in enumerate(items, start=1):
        where = f"файл индексов {path.name}, запись №{number}"
        try:
            work_type, start, end, value, source = (
                item["work_type"], item["period_start"], item["period_end"], item["value"], item["source"])
        except (KeyError, TypeError) as exc:
            raise IndexTableError(f"{where}: нет поля {exc}") from exc
        if work_type not in INDEX_WORK_TYPES:
            raise IndexTableError(f"{where}: неизвестный раздел «{work_type}»")
        try:
            if date.fromisoformat(start) > date.fromisoformat(end):
                raise IndexTableError(f"{where}: начало периода позже конца")
        except (TypeError, ValueError) as exc:
            if isinstance(exc, IndexTableError):
                raise
            raise IndexTableError(f"{where}: дата не в виде ГГГГ-ММ-ДД ({exc})") from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < float("inf"):
            raise IndexTableError(f"{where}: индекс {value!r} — ожидается число больше 0, без кавычек")
        if not str(source).strip():
            raise IndexTableError(f"{where}: не указан документ-источник")
        entries.append(IndexEntry(work_type, start, end, float(value), str(source).strip()))
    return entries


def check_index_table(table: list[IndexEntry], etalon: list[IndexEntry]) -> None:
    """Значения файла — того же порядка, что индекс раздела из эталонного листа."""
    known = {e.work_type: e.value for e in etalon}
    for entry in table:
        base = known.get(entry.work_type)
        if base and not base / INDEX_MAX_RATIO <= entry.value <= base * INDEX_MAX_RATIO:
            raise IndexTableError(
                f"файл индексов: {entry.work_type}, {entry.period_start}: индекс {entry.value:g} отличается "
                f"от индекса эталона {base:g} больше чем в {INDEX_MAX_RATIO:g} раза — проверьте запись"
            )


def parse_quarterly_index(text: str) -> tuple[float, str, int] | tuple[None, None, None]:
    """Разобрать текст вида "Итого с учетом коэффициента инфляции
    К-81,95 на III квартал 2026 года (...)" на (значение индекса,
    номер квартала римскими цифрами, год).

    Возвращает (None, None, None), если текст не распознан однозначно
    (нет числа индекса или ссылки на квартал/год).
    """
    value_match = re.search(r"К-([\d,]+)", text)
    quarter_match = re.search(r"\b([IVX]+)\s*квартал[а-я]*\s*(\d{4})", text, re.IGNORECASE)
    if not value_match or not quarter_match:
        return None, None, None

    quarter = quarter_match.group(1).upper()
    if quarter not in QUARTER_MONTHS:
        return None, None, None

    value = float(value_match.group(1).replace(",", "."))
    year = int(quarter_match.group(2))
    return value, quarter, year


def quarter_period(quarter: str, year: int) -> tuple[str, str]:
    """Вернуть (первый день, последний день) календарного квартала
    включительно, в формате 'YYYY-MM-DD'."""
    start_month, end_month = QUARTER_MONTHS[quarter]
    period_start = date(year, start_month, 1)
    last_day = calendar.monthrange(year, end_month)[1]
    period_end = date(year, end_month, last_day)
    return period_start.isoformat(), period_end.isoformat()


def extract_index_entries(
    ws, row: int = INDEX_ROW, work_type: str = INDEX_WORK_TYPE
) -> tuple[list[IndexEntry], list[str]]:
    """Извлечь индекс пересчёта цен из строки листа (по умолчанию
    строка со сводным индексом сметы). Возвращает (позиции,
    предупреждения) — предупреждение, если строка пуста или текст не
    распознан, вместо того чтобы выдумывать значение."""
    text = ws.cell(row=row, column=1).value
    if not text:
        return [], [f"Строка {row}: пусто, индекс пересчёта цен не найден"]

    value, quarter, year = parse_quarterly_index(str(text))
    if value is None:
        return [], [f"Строка {row}: не удалось распознать индекс/квартал в тексте: {text!r}"]

    period_start, period_end = quarter_period(quarter, year)
    entry = IndexEntry(
        work_type=work_type,
        period_start=period_start,
        period_end=period_end,
        value=value,
        basis=" ".join(str(text).split()),
    )
    return [entry], []


def extract_geophysics_index_entries(
    ws,
    geology_period: tuple[str, str],
    row: int = GEOPHYSICS_INDEX_ROW,
    value_column: int = GEOPHYSICS_INDEX_VALUE_COLUMN,
    work_type: str = GEOPHYSICS_INDEX_WORK_TYPE,
) -> tuple[list[IndexEntry], list[str]]:
    """Извлечь индекс пересчёта цен геофизики (см. GEOPHYSICS_INDEX_ROW).

    `geology_period` — уже определённый период (period_start,
    period_end) индекса геологии из этого же файла: переиспользуется
    буквально, а не парсится заново из комментария этой строки (см.
    докстринг у GEOPHYSICS_INDEX_ROW — квартал в тексте комментария
    противоречит дате указанного там же письма Минстроя, а письмо —
    то же самое, что у геологии, значит и период тот же)."""
    value_cell = ws.cell(row=row, column=value_column).value
    if value_cell is None:
        return [], [f"Строка {row}: пусто, индекс пересчёта цен для геофизики не найден"]

    try:
        value = float(value_cell)
    except (TypeError, ValueError):
        return [], [f"Строка {row}: значение индекса не число: {value_cell!r}"]

    comment = ws.cell(row=row, column=3).value
    basis = " ".join(str(comment).split()) if comment else f"строка {row}, столбец {value_column}"
    period_start, period_end = geology_period

    entry = IndexEntry(
        work_type=work_type,
        period_start=period_start,
        period_end=period_end,
        value=value,
        basis=basis,
    )
    return [entry], []


def parse_basis(text: str) -> tuple[str | None, str | None, str | None]:
    """Разобрать текст основания на (источник, номер таблицы, номер пункта).

    Возвращает (None, None, None), если текст не похож однозначно на
    "[источник] Таблица N, п.M" — например, ссылается сразу на
    несколько пунктов через "и", или вообще не содержит номер таблицы
    (ссылка на общие указания вида "п.13 ОУ").
    """
    normalized = " ".join(text.split())

    table_match = re.search(r"(?:Таблица|Табл\.?)\s*№?\s*(\d+)", normalized, re.IGNORECASE)
    if not table_match:
        table_match = re.search(r"\bт\.?\s*(\d+)\s*п", normalized, re.IGNORECASE)
    if not table_match:
        return None, None, None

    item_matches = re.findall(r"п\.?\s*(\d+[а-яА-Я]?)", normalized)
    if len(item_matches) != 1:
        return None, None, None

    source = normalized[: table_match.start()].strip(" ,.")
    return (source or None, table_match.group(1), item_matches[0])


def extract_price_entries(
    ws, first_row: int = FIRST_DATA_ROW, last_row: int = LAST_DATA_ROW
) -> tuple[list[PriceEntry], list[int]]:
    """Пройти по строкам данных листа и извлечь однозначные позиции цен.

    Возвращает (найденные_позиции, номера_пропущенных_строк).

    Протягивание основания (forward-fill): если у строки есть категория
    (столбец C, например "IV кат"), но нет собственного основания
    (столбец F пуст) — она считается продолжением блока категорий одной
    и той же работы (например, категории бурения II-VIII под одним и
    тем же "Таблица 17, п.1", указанным только у категории I) и
    наследует основание последней строки выше, где оно было указано
    явно. Протягивание сбрасывается на заголовке раздела ("I.
    Геодезические работы" и т.п.) — но не на строке подытога ("Итого
    п.п. ...", это просто промежуточная сумма внутри раздела, а не
    смена вида работ).

    Строки БЕЗ категории (просто число в столбце "цена", без пометки —
    например, безымянные коэффициенты у "Содержание базы экспедиции")
    протягивание не наследуют и остаются пропущенными, как раньше —
    иначе им бы приписалось основание совсем другой работы (см.
    записи разработки, группа В).
    """
    entries: list[PriceEntry] = []
    skipped_rows: list[int] = []
    last_name = ""
    last_basis: str | None = None

    for row_idx in range(first_row, last_row + 1):
        a = ws.cell(row=row_idx, column=1).value
        b = ws.cell(row=row_idx, column=2).value
        c = ws.cell(row=row_idx, column=3).value
        e = ws.cell(row=row_idx, column=5).value
        f = ws.cell(row=row_idx, column=6).value

        if isinstance(a, str):
            # заголовок раздела ("I. Геодезические работы") сбрасывает
            # протянутое основание — дальше начинается другой вид работ.
            # Строка "Итого п.п. ..." — просто подытог, не сбрасывает.
            if not a.strip().startswith("Итого"):
                last_basis = None
            continue

        if b:
            last_name = str(b).strip()

        if e is None:
            continue
        if isinstance(c, str) and c.strip() == "к=":
            continue  # строка коэффициента, относится к предыдущей позиции

        category = (
            str(c).strip()
            if isinstance(c, str) and c.strip() and c.strip() != "к="
            else None
        )

        basis_text = f
        if not basis_text and category is not None and last_basis is not None:
            basis_text = last_basis  # строка-продолжение блока категорий

        if not basis_text:
            skipped_rows.append(row_idx)
            continue

        source, table, item = parse_basis(str(basis_text))
        if table is None or item is None:
            skipped_rows.append(row_idx)
            continue

        last_basis = str(basis_text)

        entries.append(
            PriceEntry(
                source=source or GEOLOGY_ASSUMED_SOURCE,
                table=table,
                item=item,
                category=category,
                price=float(e),
                basis=" ".join(str(basis_text).split()),
                name=last_name,
                row=row_idx,
                source_assumed=source is None,
            )
        )

    return entries, skipped_rows


def primary_basis_segment(text: str) -> str:
    """Отрезать текст основания до первого "К=".

    На листе геофизики (в отличие от 1.2.1_ИГИ) в одной ячейке столбца
    "основание" вместе с номером таблицы/пункта перечислены ещё и
    коэффициенты со СВОИМИ ссылками на другие таблицы/страницы
    (например "т.267 п.3 \\nК=1.2 с.252 п.6 \\nК=1.15 т.265 п.2"). Если
    отдать такой текст целиком в parse_basis, он найдёт несколько
    "п.N" и посчитает основание неоднозначным (что верно — но для ЭТОЙ
    основной ссылки, до первого "К=", неоднозначности нет). Поэтому
    здесь текст обрезается до первого "К=", а parse_basis применяется
    уже к обрезанному куску."""
    head = text.split("К=", 1)[0]
    return " ".join(head.split())


def extract_geophysics_price_entries(
    ws, first_row: int = GEOPHYSICS_FIRST_DATA_ROW, last_row: int = GEOPHYSICS_LAST_DATA_ROW,
    source: str = GEOPHYSICS_SOURCE,
) -> tuple[list[PriceEntry], list[int]]:
    """Извлечь однозначные позиции цен с листа геофизики (см.
    GEOPHYSICS_SHEET_NAME).

    Столбцы (см. заголовок листа, строка 11): A — N п/п, B —
    наименование, C — основание (номер таблицы/пункта + коэффициенты
    через "К="), D — единица измерения, E — объём работ, F — цена
    ед.изм./ставка, G..J — коэффициенты (несколько под-колонок одного
    объединённого заголовка "Коэффициенты").

    Категорий (в смысле геологии — "IV кат" и т.п.) на этом листе нет —
    category всегда None.

    Коэффициенты в столбцах G..J сюда НЕ переносятся: сверка нескольких
    строк показала, что их позиция внутри G..J не привязана однозначно
    к конкретному "К=" из текста основания (например, у строки ВЭЗ три
    коэффициента текста 1.2/1.15/1.1 лежат в G/H/I, а у строки
    техотчёта единственный коэффициент текста 1.2 лежит не в G, а в H)
    — автоматически сопоставить коэффициент нужному "К=" без риска
    ошибиться нельзя. Кто использует эти позиции (см.
    modules/geophysics/module.py), берёт нужный коэффициент как
    отдельную, вручную проверенную константу — по тому же принципу,
    что уже применялся в geology/module.py для констант класса Б/В (см.
    записи разработки).

    Особый случай: если единица измерения — "%" и объём (столбец E)
    пуст, реальная ставка лежит не в столбце "цена" (там в этом случае
    — уже готовая по этой смете сумма базы в рублях, не переиспользуемая
    ставка), а в первом столбце коэффициентов (G). Обнаружено сверкой
    строк листа (внутренний/внешний транспорт: E пусто, F — большое
    "рублёвое" число, G — процентная ставка вроде 0.05/0.065), а НЕ
    предположением — см. записи разработки."""
    entries: list[PriceEntry] = []
    skipped_rows: list[int] = []

    for row_idx in range(first_row, last_row + 1):
        item_no = ws.cell(row=row_idx, column=1).value
        name = ws.cell(row=row_idx, column=2).value
        basis_text = ws.cell(row=row_idx, column=3).value
        unit = ws.cell(row=row_idx, column=4).value
        qty = ws.cell(row=row_idx, column=5).value
        price_cell = ws.cell(row=row_idx, column=6).value
        rate_cell = ws.cell(row=row_idx, column=7).value

        if not isinstance(item_no, (int, float)):
            continue  # заголовки разделов/подытоги — не пронумерованные позиции

        if not basis_text:
            skipped_rows.append(row_idx)
            continue

        src, table, item = parse_basis(primary_basis_segment(str(basis_text)))
        if table is None or item is None:
            skipped_rows.append(row_idx)
            continue

        is_percent_without_volume = isinstance(unit, str) and unit.strip() == "%" and qty is None
        value = rate_cell if is_percent_without_volume else price_cell
        if value is None:
            skipped_rows.append(row_idx)
            continue

        entries.append(
            PriceEntry(
                source=src or source,
                table=table,
                item=item,
                category=None,
                price=float(value),
                basis=" ".join(str(basis_text).split()),
                name=str(name).strip() if name else "",
                row=row_idx,
            )
        )

    return entries, skipped_rows


GEODESY_PRICE_MARKER_RE = re.compile(r"A\s*=\s*(\d+(?:[.,]\d+)?)\s*тыс", re.IGNORECASE)


def extract_geodesy_price_entries(
    ws, first_row: int = GEODESY_FIRST_DATA_ROW, last_row: int = GEODESY_LAST_DATA_ROW,
    source: str = GEODESY_SOURCE,
) -> tuple[list[PriceEntry], list[int]]:
    """Извлечь однозначные позиции цен с листа геодезии (см.
    GEODESY_SHEET_NAME).

    Столбцы (заголовок листа, строка 18): A — № пп (текстовое, вида
    "1.1", "4.10" — НЕ целое число, как у геофизики), B — наименование,
    C — единица измерения, D — количество, E — обоснование стоимости
    (составной код + путь к таблице + "A=X тыс.руб" — расценка за
    единицу измерения ДО количества и коэффициентов), F — расчёт
    стоимости (текст формулы, не используется здесь), G — итоговая
    стоимость строки (тоже не используется — это A×количество×
    коэффициенты ЦЕЛИКОМ, а не цена за единицу, которая нужна
    справочнику).

    Строка считается позицией цены, только если в столбце E есть
    маркер "A=...тыс" — так отличаются настоящие расценочные строки от
    заголовков разделов, подытогов и строк-коэффициентов (у них этого
    маркера нет; проверено сверкой всех строк листа, см.
    записи разработки) — a НЕ по типу столбца A, как у геофизики
    (там № пп — число), потому что здесь № пп всегда текст.

    **category = собственный № пп строки (например "1.1", "4.4").**
    Причина: как минимум 2 пары позиций на этом листе ссылаются на ОДИН
    И ТОТ ЖЕ код расценки (Таблица+пункт), но с разной ценой — по
    структуре видно, что это одна и та же позиция справочника,
    процитированная дважды (в разделе "Полевые работы" и второй раз в
    разделе "Камеральные работы", т.е. поле/камералка), например
    Таблица 8 п.3: строка "1.1" (A=6.426) и строка "4.4" (A=2.538).
    Это ТА ЖЕ ситуация, что уже возникала и была ЗАКРЫТА у геологии
    (записи разработки, "3 пары позиций... решено исключить из справочника,
    подтверждено пользователем 2026-09-18, пересматривать не нужно") —
    но здесь решение НЕ переоткрывается и не копируется автоматически:
    в отличие от той ситуации, для ЭТОГО листа под рукой есть надёжный,
    явно присутствующий в самих данных способ различить дубли — № пп
    строки, уже уникальный и не требующий никакого предположения о
    том, что означает разница (не нужно даже знать, что это
    "поле/камералка" — просто у каждой строки СВОЙ пп-номер). Поэтому
    здесь применён `category`, а не исключение — сознательное решение,
    не пересмотр геологического прецедента (см. записи разработки)."""
    entries: list[PriceEntry] = []
    skipped_rows: list[int] = []

    for row_idx in range(first_row, last_row + 1):
        item_no = ws.cell(row=row_idx, column=1).value
        name = ws.cell(row=row_idx, column=2).value
        basis_text = ws.cell(row=row_idx, column=5).value

        if not basis_text:
            continue  # заголовки разделов/подытоги без обоснования — не позиции, не пропуск

        price_match = GEODESY_PRICE_MARKER_RE.search(str(basis_text))
        if price_match is None:
            continue  # строка-коэффициент/подытог без "A=...тыс" — не позиция цены, не пропуск

        head = str(basis_text).split("A=", 1)[0]
        src, table, item = parse_basis(head)
        if table is None or item is None:
            skipped_rows.append(row_idx)
            continue

        price_tys_rub = float(price_match.group(1).replace(",", "."))
        price_rub = price_tys_rub * 1000.0  # тыс.руб -> руб, явно

        entries.append(
            PriceEntry(
                source=source,  # НЕ src — см. докстринг GEODESY_SHEET_NAME, префикс перед
                                 # "Таблица" в этом листе — составной внутренний код, не
                                 # чистое имя сборника, использовать его как source нельзя
                table=table,
                item=item,
                category=str(item_no).strip() if item_no else None,
                price=price_rub,
                basis=" ".join(str(basis_text).split()),
                name=str(name).strip() if name else "",
                row=row_idx,
            )
        )

    return entries, skipped_rows


GEODESY_INDEX_RE = re.compile(r"Коэф\s*-?\s*т\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)


def extract_geodesy_index_entries(
    ws,
    geology_period: tuple[str, str],
    row: int = GEODESY_INDEX_ROW,
    value_column: int = GEODESY_INDEX_VALUE_COLUMN,
    work_type: str = GEODESY_INDEX_WORK_TYPE,
) -> tuple[list[IndexEntry], list[str]]:
    """Извлечь индекс пересчёта цен геодезии (см. GEODESY_INDEX_ROW).

    Как и у геофизики, период переиспользуется буквально с уже
    распознанного индекса геологии (`geology_period`) — то же письмо
    Минстроя, значит тот же период действия; значение — своё, из
    текста этой строки ("Коэф - т 7,19 от п.6 - 7")."""
    text = ws.cell(row=row, column=value_column).value
    if not text:
        return [], [f"Строка {row}: пусто, индекс пересчёта цен для геодезии не найден"]

    match = GEODESY_INDEX_RE.search(str(text))
    if match is None:
        return [], [f"Строка {row}: не удалось распознать индекс в тексте: {text!r}"]

    value = float(match.group(1).replace(",", "."))
    period_start, period_end = geology_period

    entry = IndexEntry(
        work_type=work_type,
        period_start=period_start,
        period_end=period_end,
        value=value,
        basis=" ".join(str(text).split()),
    )
    return [entry], []


def parse_ecology_basis(text: str) -> tuple[str | None, str | None]:
    """Разобрать "Таблица N, п.M" / "Таблица N, §M" / "т. N § M" на
    (номер таблицы, номер пункта).

    В отличие от `parse_basis` (общий для геологии/геофизики/геодезии),
    здесь ДОПОЛНИТЕЛЬНО распознаётся маркер пункта "§" вместо "п." —
    на этом листе оба варианта встречаются вперемешку для РАЗНЫХ строк
    (например, "Таблица 60, §10" рядом с "Таблица 60, п.7") — это
    особенность именно этого листа, не два разных источника.

    Возвращает (None, None), если после найденного "п.M"/"§M" сразу
    следует запятая с ещё одной цифрой (например "§14, 22, 32, 38") —
    значит ссылка на НЕСКОЛЬКО пунктов сразу, однозначного основания
    нет (та же логика, что и у `parse_basis` для нескольких "п.N", но
    parse_basis не ловит этот случай для формата с "§", поэтому здесь
    проверка отдельная)."""
    normalized = " ".join(text.split())
    matches = list(ECOLOGY_BASIS_RE.finditer(normalized))
    if len(matches) != 1:
        return None, None

    match = matches[0]
    tail = normalized[match.end():]
    if re.match(r"\s*,\s*\d", tail):
        return None, None

    return match.group(1), match.group(2)


def extract_ecology_price_entries(
    ws,
    first_row: int = ECOLOGY_FIRST_DATA_ROW,
    last_row: int = ECOLOGY_LAST_DATA_ROW,
    source: str = ECOLOGY_SOURCE,
    kameral_start_row: int = ECOLOGY_KAMERAL_SECTION_START_ROW,
    dual_price_codes: set[tuple[str, str]] = ECOLOGY_DUAL_PRICE_CODES,
    skip_rows: set[int] = frozenset(),
) -> tuple[list[PriceEntry], list[int]]:
    """Извлечь однозначные позиции цен с листа экологии (см.
    ECOLOGY_SHEET_NAME).

    Столбцы (заголовок листа, строка 8-10): A — №№ п/п (целое число —
    как у геофизики, НЕ текст, как у геодезии), B — наименование работ
    и затрат, C — коэффициент ИЛИ единица измерения лабораторных работ
    (перегруженный смысл, здесь не используется вовсе — коэффициенты
    для позиций, которые реально применяются в modules/ecology/module.py,
    сверены вручную и хранятся там константами, тот же принцип, что и
    VEZ_COEFFICIENT геофизики/K1,K2 геодезии), D — объём, E — цена,
    F — обоснование стоимости, G — расчёт (текст формулы, не
    используется).

    category — только для кодов из `dual_price_codes` (Т92/Т91 и
    Т9/Т10/Т11 — см. ECOLOGY_DUAL_PRICE_CODES), которые на листе
    цитируются ДВАЖДЫ с разной ценой (полевая/камеральная). Для всех
    остальных кодов — category=None (проверено вручную по всем строкам
    диапазона: остальные коды цитируются на листе один раз).

    `skip_rows` — строки, заведомо исключённые вручную с объяснением у
    константы (см. ECOLOGY_ROAD_SKIP_ROWS); попадают в skipped_rows."""
    entries: list[PriceEntry] = []
    skipped_rows: list[int] = []

    for row_idx in range(first_row, last_row + 1):
        if row_idx in skip_rows:
            skipped_rows.append(row_idx)
            continue
        item_no = ws.cell(row=row_idx, column=1).value
        name = ws.cell(row=row_idx, column=2).value
        price_cell = ws.cell(row=row_idx, column=5).value
        basis_text = ws.cell(row=row_idx, column=6).value

        if not isinstance(item_no, (int, float)):
            continue  # заголовки разделов/подытоги — не пронумерованные позиции

        if not basis_text or price_cell is None:
            skipped_rows.append(row_idx)
            continue

        table, item = parse_ecology_basis(str(basis_text))
        if table is None or item is None:
            skipped_rows.append(row_idx)
            continue

        category = None
        if (table, item) in dual_price_codes:
            category = "kameral" if row_idx >= kameral_start_row else "field"

        entries.append(
            PriceEntry(
                source=source,
                table=table,
                item=item,
                category=category,
                price=float(price_cell),
                basis=" ".join(str(basis_text).split()),
                name=str(name).strip() if name else "",
                row=row_idx,
            )
        )

    return entries, skipped_rows


def extract_ecology_road_price_entries(ws) -> tuple[list[PriceEntry], list[int]]:
    """Тот же разбор, что `extract_ecology_price_entries`, с границами
    листа "№1.4_ИЭИ" (Автодорога) — см. ECOLOGY_ROAD_SHEET_NAME."""
    return extract_ecology_price_entries(
        ws,
        first_row=ECOLOGY_ROAD_FIRST_DATA_ROW,
        last_row=ECOLOGY_ROAD_LAST_DATA_ROW,
        kameral_start_row=ECOLOGY_ROAD_KAMERAL_SECTION_START_ROW,
        skip_rows=ECOLOGY_ROAD_SKIP_ROWS,
    )


def extract_ecology_index_entries(
    ws,
    row: int = ECOLOGY_INDEX_ROW,
    value_column: int = ECOLOGY_INDEX_VALUE_COLUMN,
    work_type: str = ECOLOGY_INDEX_WORK_TYPE,
) -> tuple[list[IndexEntry], list[str]]:
    """Извлечь индекс пересчёта цен экологии (см. ECOLOGY_INDEX_ROW) —
    самостоятельно распознанный из текста этой строки, СОВПАВШИЙ со
    значением индекса геологии (тот же сборник СБЦ-99), а не
    переиспользованный по аналогии (см. докстринг ECOLOGY_INDEX_ROW)."""
    text = ws.cell(row=row, column=value_column).value
    if not text:
        return [], [f"Строка {row}: пусто, индекс пересчёта цен для экологии не найден"]

    match = ECOLOGY_INDEX_RE.search(str(text))
    if match is None:
        return [], [f"Строка {row}: не удалось распознать индекс в тексте: {text!r}"]

    quarter = match.group(1).upper()
    year = int(match.group(2))
    value = float(match.group(3).replace(",", "."))
    if quarter not in QUARTER_MONTHS:
        return [], [f"Строка {row}: неизвестный квартал {quarter!r} в тексте: {text!r}"]

    period_start, period_end = quarter_period(quarter, year)
    entry = IndexEntry(
        work_type=work_type,
        period_start=period_start,
        period_end=period_end,
        value=value,
        basis=" ".join(str(text).split()),
    )
    return [entry], []


# --- Гидрометеорология (ИГМИ, 5-й модуль) ---------------------------------
#
# Сборник — СБЦ-2000 «Инженерно-гидрографические работы. Инженерно-
# гидрометеорологические изыскания на реках»; ставка внешнего транспорта
# — СБЦ-97, т.5. Два эталонных листа одного и того же шаблона компании:
# "№2.4_ИГМИ" (Карьер, ECOLOGY_SOURCE_XLSX) и "№1.3_ИГМИ" (Автодорога,
# SOURCE_XLSX). Полный разбор листа — docs/hydromet_diagnostics.md.
#
# ЧЕТВЁРТЫЙ формат цены в проекте: цены не в отдельной колонке и не
# текстом в основании, а в ФОРМУЛЕ ячейки F — поэтому лист читается
# с data_only=False (формулы) вместе с data_only=True (наименования,
# номера §). Три вида формул:
#   D20*78            — константа;
#   CHOOSE(Vnew!E21,127,174,236) — сетка по категории сложности I/II/III
#                       (вся сетка, не только использованная категория);
#   D20*[25]ТБЛ!P14   — ссылка на внешнюю книгу автора сметы (её в
#                       исходных данных нет, но xlsx хранит кэш значений
#                       использованных ячеек — только для той категории,
#                       что стоит на листе).
# Шаблон — ~370 строк заготовок всех видов работ; разбираются только
# позиции, реально использованные модулем (HYDROMET_PRICE_SPECS), каждая
# находится по тексту наименования в своём разделе листа, а номер
# таблицы проверяется по заголовку блока.
HYDROMET_KARIER_SHEET_NAME = "№2.4_ИГМИ"
HYDROMET_ROAD_SHEET_NAME = "№1.3_ИГМИ"
HYDROMET_SOURCE = "СБЦ-2000"
HYDROMET_EXTERNAL_TRANSPORT_SOURCE = "СБЦ-97"
HYDROMET_INDEX_WORK_TYPE = "hydromet"

# Коды, которые цитируются и полевой строкой, и камеральной с РАЗНОЙ
# ценой (как Т9/Т10/Т11/Т91/Т92 у экологии) — различаются префиксом
# раздела в category: "field/III", "kameral/до 20 м".
HYDROMET_DUAL_PRICE_CODES = {("43", "1"), ("43", "2"), ("24", "1"), ("48", "1")}

ROMAN_CATEGORIES = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}


@dataclass(frozen=True)
class HydrometPriceSpec:
    """Где на листе ИГМИ искать цену одной позиции справочника.

    section — "field" (до строки "Итого полевых") или "kameral" (после
    неё); anchor — подстрока наименования (столбец B, значение) строки
    позиции; rows — сколько строк подряд, начиная с найденной, относятся
    к позиции (сетка ширин реки у т.48 — 6 строк); mode — способ чтения
    цены из формулы F:
      "const"        — число после D<строка>*;
      "choose_cat"   — CHOOSE([N]Vnew!..., a, b, c) → категории I, II, III;
      "choose_item"  — CHOOSE(I<строка>, a, b, c) → пункты 1, 2, 3;
      "ext_cat"      — первая ссылка [N]ТБЛ!ячейка, категория — из ссылки
                       [N]Vnew!ячейка перед "кат." в формуле наименования;
      "ext"          — первая ссылка [N]ТБЛ!ячейка, без категории.
    grade_from_name — категорией служит наименование строки (ширина реки
    "до 20 м" и т.п.). source — сборник (СБЦ-97 для внешнего транспорта)."""

    table: str
    section: str
    anchor: str
    mode: str
    rows: int = 1
    grade_from_name: bool = False
    source: str = HYDROMET_SOURCE


HYDROMET_PRICE_SPECS: list[HydrometPriceSpec] = [
    # --- полевые ---
    HydrometPriceSpec("43", "field", "трассы в местности", "ext_cat"),
    HydrometPriceSpec("43", "field", "реки в местности", "ext_cat"),
    HydrometPriceSpec("20", "field", "при ширине долины реки", "ext_cat"),
    HydrometPriceSpec("24", "field", "Разбивка и нивелирование морфостворов", "ext_cat", rows=2),
    HydrometPriceSpec("25", "field", "при удалении точек от оси", "ext_cat"),
    HydrometPriceSpec("26", "field", "Нивелирование уклонов", "choose_cat", rows=2),
    HydrometPriceSpec("44", "field", "до 100 м", "choose_cat"),
    HydrometPriceSpec("48", "field", "измерение расхода воды", "const", rows=7, grade_from_name=True),
    HydrometPriceSpec("48", "field", "промеры глубин при ширине реки", "const", rows=7, grade_from_name=True),
    HydrometPriceSpec("48", "field", "Фотоработы", "const"),
    # --- камеральные ---
    HydrometPriceSpec("43", "kameral", "трассы в местности", "choose_cat"),
    HydrometPriceSpec("43", "kameral", "реки в местности", "choose_cat"),
    HydrometPriceSpec("24", "kameral", "Камеральная обработка нивелирования морфостворов", "choose_cat", rows=2),
    HydrometPriceSpec("48", "kameral", "измерение расхода воды", "const", rows=7, grade_from_name=True),
    HydrometPriceSpec("51", "kameral", "таблицы гидрологической изученности", "const", rows=3),
    HydrometPriceSpec("51", "kameral", "схемы гидрометеорологической изученности", "const", rows=3),
    HydrometPriceSpec("52", "kameral", "при неискаженном водном режиме", "const", rows=5),
    HydrometPriceSpec("54", "kameral", "сильно деформирующемся русле", "const", rows=4),
    HydrometPriceSpec("55", "kameral", "кривой расходов гидравлическим методом", "const"),
    HydrometPriceSpec("55", "kameral", "определение площади водосбора", "const"),
    HydrometPriceSpec("55", "kameral", "определение уклона водосбора", "const"),
    HydrometPriceSpec("56", "kameral", "по формулам предельной интенсивности", "const"),
    HydrometPriceSpec("56", "kameral", "максимальных расходов весеннего половодья", "const"),
    HydrometPriceSpec("56", "kameral", "определение минимального расхода воды", "const"),
    HydrometPriceSpec("56", "kameral", "параметров стока с построением кривой обеспеченности", "const", rows=4),
    HydrometPriceSpec("56", "kameral", "при весьма сходных условиях", "const"),
    HydrometPriceSpec("57", "kameral", "смещения русла по данным съемок", "const", rows=2),
    HydrometPriceSpec("57", "kameral", "вертикальных деформаций русла без построения плана", "const"),
    HydrometPriceSpec("64", "kameral", "при его общей устойчивости", "choose_item"),
    HydrometPriceSpec("66", "kameral", "бытового ледового режима", "ext", rows=2),
    HydrometPriceSpec("69", "kameral", "климатической записки", "ext"),
    HydrometPriceSpec("67", "kameral", "Подбор станций", "const"),
    # --- вспомогательные / зависимые (ставки, не цены) ---
    HydrometPriceSpec("62", "kameral", "Составление технического отчета", "ext"),
    HydrometPriceSpec("5", "kameral", "Внешний транспорт", "ext",
                      source=HYDROMET_EXTERNAL_TRANSPORT_SOURCE),
]

# Цены категории сложности II для позиций, у которых цена на эталонах —
# только кэш внешней книги (оба эталона — категория III, кат. II там нет).
#
# ОСОЗНАННОЕ ИСКЛЮЧЕНИЕ из практики «источник цены — файл в reference_data/,
# прочитанный парсером» (решение 2026-09-24, записи разработки):
# цена категории II взята из смет СТОРОННИХ клиентов компании — не заказчика
# объектов-эталонов (Карьер/Автодорога), чьи сметы лежат в reference_data/, —
# и эти файлы не копируются в репозиторий по соображениям
# конфиденциальности (в них чужие заказчики, объёмы и коммерческие условия
# ТКП, в т.ч. скидки). В отличие от файлов, сами числа ниже — нормативные
# базовые цены опубликованного СБЦ-2000, данных клиента в них нет.
#
# При необходимости пересчитать — кэш внешних книг ([N]ТБЛ!P14/P15/R14/S14/
# P18) листов ИГМИ в исходных данных пользователя (папка
# «КОСМОС 4\ЗАмечания к работе агента», вне репозитория):
# "смета стороннего объекта А" (листы "см ИГМИ 3-1…3-6"), "компании_Форма
# ценовое предложение.xlsx" ("1-4 ИГМИ"), "смета стороннего объекта В" ("1-4 ИГМИ") — везде одинаковые; для т.43/т.20/
# т.24 совпадают и с независимой распечаткой «Адепт» ("Идеальное
# оформление комплекта смет ПИР.xlsx", лист "1.3.": 20, 30, 272, 161).
HYDROMET_CATEGORY_II_SUPPLEMENT_BASIS = (
    "СБЦ-2000 т.{table} §{item}, кат. II — константа: цена из смет ИГМИ шаблона компании "
    "для сторонних клиентов (файлы не копируются в репозиторий по соображениям "
    "конфиденциальности, см. data/build_references.py)"
)
HYDROMET_CATEGORY_II_SUPPLEMENT: list[tuple[str, str, str, float, str]] = [
    # (таблица, пункт, category, цена, наименование)
    ("43", "2", "field/II", 20.0, "Рекогносцировочное обследование трассы"),
    ("43", "1", "field/II", 30.0, "Рекогносцировочное обследование реки"),
    ("20", "1", "II", 272.0, "Гидролого-морфометрическое обследование водотоков"),
    ("24", "1", "field/II", 161.0, "Разбивка и нивелирование морфостворов"),
    ("25", "1", "II", 133.0, "Установление отметок ГВВ по меткам или опросу"),
]

HYDROMET_EXT_REF_RE = re.compile(r"\[(\d+)\](ТБЛ|Титул|Vnew)!\$?([A-Z]+)\$?(\d+)")
HYDROMET_CATEGORY_REF_RE = re.compile(r"\[(\d+)\]Vnew!\$?([A-Z]+)\$?(\d+)\s*&\s*\"\s*кат")
HYDROMET_CONST_RE = re.compile(r"D\$?\d+\s*\*\s*(\d+(?:\.\d+)?)")
HYDROMET_CHOOSE_CAT_RE = re.compile(r"CHOOSE\(\[\d+\]Vnew!\$?[A-Z]+\$?\d+\s*,\s*([\d.,\s]+)\)")
HYDROMET_CHOOSE_ITEM_RE = re.compile(r"CHOOSE\(I\d+\s*,\s*([\d.,\s]+)\)")
# (?<!буква) — чтобы "стр.7" в тексте "к=1.25 - п.8в стр.7" не читалось как "т.7".
HYDROMET_TABLE_RE = re.compile(r"(?<![а-яА-Яa-zA-Z])т\.?\s*(\d+)")
HYDROMET_ITEM_RE = re.compile(r"§\s*(\d+)")
HYDROMET_INDEX_RE = re.compile(r"текущих цен\s*к\s*=\s*([\d,.]+)", re.IGNORECASE)


def hydromet_external_value(wb_formulas, link_number: int, sheet: str, cell: str) -> str | None:
    """Кэшированное значение ячейки внешней книги `[link_number]sheet!cell`
    (openpyxl хранит кэш внешних ссылок в `wb._external_links` — закрытый
    атрибут, публичного API для этого нет). None — ячейки нет в кэше."""
    links = getattr(wb_formulas, "_external_links", [])
    if not 1 <= link_number <= len(links):
        return None
    book = links[link_number - 1].externalBook
    if book.sheetNames is None or book.sheetDataSet is None:
        return None
    names = [getattr(name, "val", name) for name in book.sheetNames.sheetName]
    for sheet_data in book.sheetDataSet.sheetData:
        if sheet_data.sheetId >= len(names) or names[sheet_data.sheetId] != sheet:
            continue
        for row in sheet_data.row or []:
            for c in row.cell or []:
                if c.r == cell:
                    return c.v
    return None


def _hydromet_numbers(text: str) -> list[float]:
    return [float(x) for x in re.split(r"\s*,\s*", text.strip()) if x]


def _hydromet_section_bounds(ws_values) -> tuple[int, int]:
    """(строка "Итого полевых работ", строка "Итого камеральных работ")."""
    field_end = kameral_end = None
    for row_idx in range(1, ws_values.max_row + 1):
        a = str(ws_values.cell(row=row_idx, column=1).value or "")
        if field_end is None and a.startswith("Итого полевых"):
            field_end = row_idx
        elif kameral_end is None and a.startswith("Итого камеральных"):
            kameral_end = row_idx
    if field_end is None or kameral_end is None:
        raise ValueError("Лист ИГМИ: не найдены строки «Итого полевых»/«Итого камеральных»")
    return field_end, kameral_end


def _hydromet_nearest_above(ws_values, row_idx: int, pattern: re.Pattern, stop_row: int) -> str | None:
    """Первое совпадение `pattern` в столбце C (значение) от row_idx вверх
    до stop_row включительно."""
    for r in range(row_idx, stop_row - 1, -1):
        match = pattern.search(str(ws_values.cell(row=r, column=3).value or ""))
        if match:
            return match.group(1)
    return None


def extract_hydromet_price_entries(
    wb_formulas, wb_values, sheet_name: str, specs: list[HydrometPriceSpec] = HYDROMET_PRICE_SPECS,
) -> tuple[list[PriceEntry], list[str]]:
    """Извлечь цены позиций `specs` с листа ИГМИ (см. комментарий к
    HYDROMET_KARIER_SHEET_NAME). Возвращает (позиции, предупреждения) —
    предупреждение, если позиция не найдена, номер таблицы в заголовке
    блока не совпал или формулу не удалось разобрать (не гадаем цену).

    Строки с нулевой ценой пропускаются молча: у шаблона это заготовки
    (например, пустой кэш внешней книги для неиспользованной позиции)."""
    ws_f = wb_formulas[sheet_name]
    ws_v = wb_values[sheet_name]
    field_end, kameral_end = _hydromet_section_bounds(ws_v)
    ranges = {"field": (1, field_end - 1), "kameral": (field_end + 1, ws_v.max_row)}

    entries: list[PriceEntry] = []
    warnings: list[str] = []

    for spec in specs:
        first, last = ranges[spec.section]
        start = next(
            (r for r in range(first, last + 1)
             if spec.anchor.lower() in str(ws_v.cell(row=r, column=2).value or "").lower()),
            None,
        )
        if start is None:
            warnings.append(f"{sheet_name}: позиция «{spec.anchor}» ({spec.section}) не найдена")
            continue

        for row_idx in range(start, min(start + spec.rows, last + 1)):
            formula = ws_f.cell(row=row_idx, column=6).value
            if not isinstance(formula, str) or not formula.startswith("="):
                continue  # подзаголовок блока (без формулы суммы)

            table = _hydromet_nearest_above(ws_v, row_idx, HYDROMET_TABLE_RE, first)
            if table != spec.table:
                warnings.append(
                    f"{sheet_name}, строка {row_idx}: ожидалась т.{spec.table}, в заголовке блока "
                    f"т.{table} — позиция «{spec.anchor}» пропущена"
                )
                break
            item = _hydromet_nearest_above(ws_v, row_idx, HYDROMET_ITEM_RE, first)
            name_value = str(ws_v.cell(row=row_idx, column=2).value or "").strip()
            # Наименование строки листа у сеток по категории — текст
            # ИСПОЛЬЗОВАННОЙ категории ("3 кат. сл."), для записей I/II он
            # вводил бы в заблуждение — там берётся только якорь позиции.
            if spec.mode in ("choose_cat", "ext_cat", "choose_item") or not name_value:
                name = spec.anchor
            elif spec.anchor.lower() in name_value.lower():
                name = name_value.strip(" *:")
            else:
                name = f"{spec.anchor} — {name_value}"
            basis_c = " ".join(str(ws_v.cell(row=row_idx, column=3).value or "").split())

            # (пункт, категория, цена)
            found: list[tuple[str | None, str | None, float]] = []
            if spec.mode == "const":
                m = HYDROMET_CONST_RE.search(formula)
                if m:
                    found.append((item, name_value if spec.grade_from_name else None, float(m.group(1))))
            elif spec.mode == "choose_cat":
                m = HYDROMET_CHOOSE_CAT_RE.search(formula)
                if m:
                    for number, price in enumerate(_hydromet_numbers(m.group(1)), start=1):
                        found.append((item, ROMAN_CATEGORIES[number], price))
            elif spec.mode == "choose_item":
                m = HYDROMET_CHOOSE_ITEM_RE.search(formula)
                if m:
                    for number, price in enumerate(_hydromet_numbers(m.group(1)), start=1):
                        found.append((str(number), None, price))
            elif spec.mode in ("ext", "ext_cat"):
                ref = next((r for r in HYDROMET_EXT_REF_RE.finditer(formula) if r.group(2) == "ТБЛ"), None)
                if ref:
                    link, _sheet, col, row = ref.groups()
                    value = hydromet_external_value(wb_formulas, int(link), "ТБЛ", f"{col}{row}")
                    if value is None or not float(value):
                        continue  # заготовка шаблона: в кэше внешней книги пусто/0
                    category = None
                    if spec.mode == "ext_cat":
                        name_formula = str(ws_f.cell(row=row_idx, column=2).value or "")
                        cat_ref = HYDROMET_CATEGORY_REF_RE.search(name_formula)
                        cat_value = (
                            hydromet_external_value(wb_formulas, int(cat_ref.group(1)), "Vnew",
                                                    f"{cat_ref.group(2)}{cat_ref.group(3)}")
                            if cat_ref else None
                        )
                        category = ROMAN_CATEGORIES.get(int(float(cat_value))) if cat_value else None
                        if category is None:
                            warnings.append(
                                f"{sheet_name}, строка {row_idx}: категория сложности не найдена "
                                f"в кэше внешней книги — позиция «{spec.anchor}» пропущена"
                            )
                            continue
                    found.append((item, category, float(value)))

            if not found:
                warnings.append(
                    f"{sheet_name}, строка {row_idx}: цена не распознана в формуле {formula!r}"
                )
                continue

            for entry_item, grade, price in found:
                if not price or entry_item is None:
                    continue
                category = grade
                if (spec.table, entry_item) in HYDROMET_DUAL_PRICE_CODES:
                    category = f"{spec.section}/{grade}" if grade else spec.section
                entries.append(
                    PriceEntry(
                        source=spec.source,
                        table=spec.table,
                        item=entry_item,
                        category=category,
                        price=price,
                        basis=f"{spec.source} т.{spec.table} §{entry_item}"
                        + (f", {grade}" if grade else "")
                        + f" (лист {sheet_name}, строка {row_idx}: {basis_c})",
                        name=name,
                        row=row_idx,
                    )
                )

    return entries, warnings


def hydromet_category_ii_supplement() -> list[PriceEntry]:
    """Цены категории II — константы, не результат парсера: источник —
    сметы сторонних клиентов, в репозиторий не копируются (см. комментарий
    к HYDROMET_CATEGORY_II_SUPPLEMENT_BASIS)."""
    return [
        PriceEntry(
            source=HYDROMET_SOURCE,
            table=table,
            item=item,
            category=category,
            price=price,
            basis=HYDROMET_CATEGORY_II_SUPPLEMENT_BASIS.format(table=table, item=item),
            name=name,
            row=0,
        )
        for table, item, category, price, name in HYDROMET_CATEGORY_II_SUPPLEMENT
    ]


def extract_hydromet_index_entries(
    ws_values, geology_period: tuple[str, str], work_type: str = HYDROMET_INDEX_WORK_TYPE,
) -> tuple[list[IndexEntry], list[str]]:
    """Индекс пересчёта цен ИГМИ: строка "ИТОГО по смете с учетом текущих
    цен к=81,95" (столбец B). Квартала в строке нет — период
    переиспользуется с индекса геологии, как у геофизики/геодезии (значение
    совпадает с индексом СБЦ-99 на III квартал 2026, 81,95)."""
    for row_idx in range(1, ws_values.max_row + 1):
        text = ws_values.cell(row=row_idx, column=2).value
        if not isinstance(text, str):
            continue
        match = HYDROMET_INDEX_RE.search(text)
        if match:
            value = float(match.group(1).replace(",", "."))
            period_start, period_end = geology_period
            return [IndexEntry(work_type, period_start, period_end, value, " ".join(text.split()))], []
    return [], ["Лист ИГМИ: строка «ИТОГО по смете с учетом текущих цен к=…» не найдена"]


def build_database(
    entries: list[PriceEntry], index_entries: list[IndexEntry], db_path: Path,
    vat_entries: list[VatEntry] | None = None,
    sources_fingerprint: str | None = None,
) -> list[str]:
    """Записать позиции цен, индексы и ставки НДС (`vat_entries`, по
    умолчанию `VAT_RATES`) в SQLite, плюс таблицу meta (версия формата,
    `sources_fingerprint` — отпечаток исходников, если передан). Возвращает
    список конфликтов цен и индексов (если есть).

    Конфликт — несколько позиций с одинаковым ключом (source, table,
    item, category), но разной ценой: значит одна и та же ссылка на
    таблицу/пункт в исходной смете на самом деле означает разные
    расценки (например, у пункта таблицы несколько граф — полевые и
    камеральные работы — а смета цитирует его одной строкой без
    уточнения). Здесь нельзя угадывать, какая из цен "правильная" —
    поэтому ВЕСЬ такой ключ целиком исключается из справочника (не
    только повторные строки), а конфликт сообщается вызывающему.
    """
    conflicting = price_conflict_groups(entries)
    conflicts: list[str] = []
    by_key: dict[tuple[str, str, str, str | None], PriceEntry] = {}
    for entry in entries:
        key = (entry.source, entry.table, entry.item, entry.category)
        if key not in conflicting:
            by_key.setdefault(key, entry)
    for key, group in conflicting.items():
        details = ", ".join(f"строка {e.row}={e.price} ({e.name})" for e in group)
        conflicts.append(f"неоднозначный ключ {key}, исключён из справочника: {details}")

    # Индексы: один период (work_type, period_start, period_end) — одно
    # значение (UNIQUE в таблице). Повтор с тем же значением — дубль,
    # пишется один раз; с разным — конфликт, период исключается целиком
    # (тот же принцип, что у цен: не угадываем, какое значение верное).
    index_grouped: dict[tuple[str, str, str], list[IndexEntry]] = {}
    for entry in index_entries:
        index_grouped.setdefault((entry.work_type, entry.period_start, entry.period_end), []).append(entry)
    unique_index_entries: list[IndexEntry] = []
    for key, group in index_grouped.items():
        if len({round(e.value, 6) for e in group}) > 1:
            details = ", ".join(f"{e.value} ({e.basis})" for e in group)
            conflicts.append(f"неоднозначный индекс {key}, исключён из справочника: {details}")
            continue
        unique_index_entries.append(group[0])

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE prices (
                source TEXT NOT NULL,
                "table" TEXT NOT NULL,
                item TEXT NOT NULL,
                category TEXT,
                price REAL NOT NULL,
                basis TEXT NOT NULL,
                name TEXT NOT NULL,
                UNIQUE(source, "table", item, category)
            )
            """
        )
        conn.executemany(
            'INSERT INTO prices (source, "table", item, category, price, basis, name) '
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (e.source, e.table, e.item, e.category, e.price, e.basis, e.name)
                for e in by_key.values()
            ],
        )

        conn.execute(
            """
            CREATE TABLE price_indices (
                work_type TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                value REAL NOT NULL,
                basis TEXT NOT NULL,
                UNIQUE(work_type, period_start, period_end)
            )
            """
        )
        conn.executemany(
            "INSERT INTO price_indices (work_type, period_start, period_end, value, basis) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (e.work_type, e.period_start, e.period_end, e.value, e.basis)
                for e in unique_index_entries
            ],
        )

        conn.execute(
            """
            CREATE TABLE vat_rates (
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                rate REAL NOT NULL,
                basis TEXT NOT NULL,
                UNIQUE(period_start, period_end)
            )
            """
        )
        conn.executemany(
            "INSERT INTO vat_rates (period_start, period_end, rate, basis) VALUES (?, ?, ?, ?)",
            [
                (e.period_start, e.period_end, e.rate, e.basis)
                for e in (VAT_RATES if vat_entries is None else vat_entries)
            ],
        )

        # Метаданные (С2): версия формата и отпечаток исходников — по ним
        # программа при запуске понимает, что справочник устарел.
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        meta = {"schema_version": str(REFERENCES_SCHEMA_VERSION)}
        if sources_fingerprint is not None:
            meta["sources_fingerprint"] = sources_fingerprint
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", list(meta.items()))
        conn.commit()
    finally:
        conn.close()

    return conflicts


def price_conflict_groups(
    entries: list[PriceEntry],
) -> dict[tuple[str, str, str, str | None], list[PriceEntry]]:
    """Ключи (source, table, item, category), под которыми встречаются
    РАЗНЫЕ цены, — со всеми позициями ключа (см. build_database)."""
    grouped: dict[tuple[str, str, str, str | None], list[PriceEntry]] = {}
    for entry in entries:
        grouped.setdefault((entry.source, entry.table, entry.item, entry.category), []).append(entry)
    return {
        key: group for key, group in grouped.items()
        if len({round(e.price, 6) for e in group}) > 1
    }


def collect_issues(
    geology_entries: list[PriceEntry],
    all_entries: list[PriceEntry],
    anchor_warnings: list[str],
) -> list[tuple[tuple, str]]:
    """Проблемы сборки — (ключ, сообщение): конфликты ключей цен,
    нераспознанные источники в основании листа ИГИ, ненайденные якоря
    (индексы и цены, которые парсер не нашёл там, где ждал). Ключ сверяется
    с REVIEWED_ISSUES; у ненайденного якоря ключ — сам текст, он никогда не
    считается разобранным."""
    issues: list[tuple[tuple, str]] = []
    for key, group in price_conflict_groups(all_entries).items():
        rows = ", ".join(f"строка {e.row}={e.price}" for e in group)
        issues.append((("conflict", *key), f"конфликт ключа {key}: {rows} — ключ исключён"))
    seen: set[tuple] = set()
    for entry in geology_entries:
        if entry.source_assumed or entry.source in GEOLOGY_RECOGNIZED_SOURCES:
            continue
        key = ("source", entry.source, entry.table, entry.item)
        if key in seen:
            continue
        seen.add(key)
        issues.append((key, f"нераспознанный источник {entry.source!r} в основании строки "
                            f"{entry.row} «{entry.basis}»"))
    for warning in anchor_warnings:
        issues.append((("anchor", warning), f"не найден якорь: {warning}"))
    return issues


def unreviewed_issues(issues: list[tuple[tuple, str]]) -> list[str]:
    """Сообщения проблем, которых нет в REVIEWED_ISSUES."""
    return [message for key, message in issues if key not in REVIEWED_ISSUES]


def build(db_path: Path | None = None) -> list[str]:
    """Собрать справочник по пути `db_path`; вернуть новые (не разобранные)
    проблемы сборки — пустой список, если их нет. Путь по умолчанию — DB_PATH
    (читается при вызове: тесты его подменяют)."""
    db_path = Path(db_path) if db_path is not None else DB_PATH
    wb = openpyxl.load_workbook(SOURCE_XLSX, data_only=True)
    ws = wb[SHEET_NAME]

    entries, skipped_rows = extract_price_entries(ws)
    entries, split_warnings = apply_geology_key_splits(entries)
    index_entries, index_warnings = extract_index_entries(ws)

    ws_geophysics = wb[GEOPHYSICS_SHEET_NAME]
    geophysics_entries, geophysics_skipped_rows = extract_geophysics_price_entries(ws_geophysics)

    geophysics_index_entries: list[IndexEntry] = []
    geophysics_index_warnings: list[str] = []
    if index_entries:
        geology_period = (index_entries[0].period_start, index_entries[0].period_end)
        geophysics_index_entries, geophysics_index_warnings = extract_geophysics_index_entries(
            ws_geophysics, geology_period
        )
    else:
        geophysics_index_warnings = [
            "Индекс геофизики не извлечён: индекс геологии (период которого "
            "переиспользуется) сам не найден"
        ]

    ws_geodesy = wb[GEODESY_SHEET_NAME]
    geodesy_entries, geodesy_skipped_rows = extract_geodesy_price_entries(ws_geodesy)

    geodesy_index_entries: list[IndexEntry] = []
    geodesy_index_warnings: list[str] = []
    if index_entries:
        geology_period = (index_entries[0].period_start, index_entries[0].period_end)
        geodesy_index_entries, geodesy_index_warnings = extract_geodesy_index_entries(
            ws_geodesy, geology_period
        )
    else:
        geodesy_index_warnings = [
            "Индекс геодезии не извлечён: индекс геологии (период которого "
            "переиспользуется) сам не найден"
        ]

    # Отдельная книга (не SOURCE_XLSX) — сборник расценок для экологии
    # найден в ДРУГОМ исходном файле заказчика, см. ECOLOGY_SOURCE_XLSX.
    wb_ecology = openpyxl.load_workbook(ECOLOGY_SOURCE_XLSX, data_only=True)
    # Геодезия Карьера — второй лист того же формата (GEODESY_KARIER_SHEET_NAME).
    geodesy_karier_entries, geodesy_karier_skipped_rows = extract_geodesy_price_entries(
        wb_ecology[GEODESY_KARIER_SHEET_NAME]
    )
    geodesy_entries = geodesy_entries + geodesy_karier_entries
    ws_ecology = wb_ecology[ECOLOGY_SHEET_NAME]
    ecology_entries, ecology_skipped_rows = extract_ecology_price_entries(ws_ecology)
    ecology_index_entries, ecology_index_warnings = extract_ecology_index_entries(ws_ecology)

    ws_ecology_road = wb[ECOLOGY_ROAD_SHEET_NAME]
    ecology_road_entries, ecology_road_skipped_rows = extract_ecology_road_price_entries(
        ws_ecology_road
    )

    # Гидрометеорология: цены — в формулах ячеек, поэтому те же книги
    # открываются второй раз с формулами (data_only=False), см.
    # HYDROMET_KARIER_SHEET_NAME.
    wb_karier_formulas = openpyxl.load_workbook(ECOLOGY_SOURCE_XLSX)
    wb_road_formulas = openpyxl.load_workbook(SOURCE_XLSX)
    hydromet_entries, hydromet_warnings = extract_hydromet_price_entries(
        wb_karier_formulas, wb_ecology, HYDROMET_KARIER_SHEET_NAME
    )
    hydromet_road_entries, hydromet_road_warnings = extract_hydromet_price_entries(
        wb_road_formulas, wb, HYDROMET_ROAD_SHEET_NAME
    )
    hydromet_supplement = hydromet_category_ii_supplement()
    hydromet_index_entries: list[IndexEntry] = []
    hydromet_index_warnings: list[str] = []
    if index_entries:
        geology_period = (index_entries[0].period_start, index_entries[0].period_end)
        hydromet_index_entries, hydromet_index_warnings = extract_hydromet_index_entries(
            wb_ecology[HYDROMET_KARIER_SHEET_NAME], geology_period
        )
    else:
        hydromet_index_warnings = [
            "Индекс ИГМИ не извлечён: индекс геологии (период которого "
            "переиспользуется) сам не найден"
        ]

    etalon_index_entries = (index_entries + geophysics_index_entries + geodesy_index_entries
                            + ecology_index_entries + hydromet_index_entries)
    table_index_entries = load_index_table()
    check_index_table(table_index_entries, etalon_index_entries)
    conflicts = build_database(
        entries + geophysics_entries + geodesy_entries + ecology_entries + ecology_road_entries
        + hydromet_entries + hydromet_road_entries + hydromet_supplement,
        etalon_index_entries + table_index_entries,
        db_path,
        sources_fingerprint=source_fingerprint(),
    )

    print(f"[{SHEET_NAME}] Извлечено позиций: {len(entries)}")
    print(
        f"[{SHEET_NAME}] Пропущено строк (нет однозначного основания): "
        f"{len(skipped_rows)} -> {skipped_rows}"
    )
    print(f"[{GEOPHYSICS_SHEET_NAME}] Извлечено позиций: {len(geophysics_entries)}")
    print(
        f"[{GEOPHYSICS_SHEET_NAME}] Пропущено строк (нет однозначного основания): "
        f"{len(geophysics_skipped_rows)} -> {geophysics_skipped_rows}"
    )
    print(f"[{GEODESY_SHEET_NAME} + {GEODESY_KARIER_SHEET_NAME}] Извлечено позиций: "
          f"{len(geodesy_entries)} (из них Карьер: {len(geodesy_karier_entries)})")
    print(
        f"[{GEODESY_SHEET_NAME}] Пропущено строк (нет однозначного основания): "
        f"{len(geodesy_skipped_rows)} -> {geodesy_skipped_rows}"
    )
    print(
        f"[{GEODESY_KARIER_SHEET_NAME}] Пропущено строк (нет однозначного основания): "
        f"{len(geodesy_karier_skipped_rows)} -> {geodesy_karier_skipped_rows}"
    )
    print(f"[{ECOLOGY_SHEET_NAME}] Извлечено позиций: {len(ecology_entries)}")
    print(
        f"[{ECOLOGY_SHEET_NAME}] Пропущено строк (нет однозначного основания): "
        f"{len(ecology_skipped_rows)} -> {ecology_skipped_rows}"
    )
    print(f"[{ECOLOGY_ROAD_SHEET_NAME}] Извлечено позиций: {len(ecology_road_entries)}")
    print(
        f"[{ECOLOGY_ROAD_SHEET_NAME}] Пропущено строк (нет однозначного основания "
        f"или исключены вручную): {len(ecology_road_skipped_rows)} -> {ecology_road_skipped_rows}"
    )
    print(
        f"[{HYDROMET_KARIER_SHEET_NAME} + {HYDROMET_ROAD_SHEET_NAME}] Извлечено позиций: "
        f"{len(hydromet_entries)} + {len(hydromet_road_entries)} "
        f"(+ {len(hydromet_supplement)} цен кат. II — константы: сметы сторонних клиентов, "
        "не копируются в репозиторий)"
    )
    for w in hydromet_warnings + hydromet_road_warnings:
        print(f"  Внимание (ИГМИ): {w}")
    if conflicts:
        print("Конфликты цен при одинаковом ключе:")
        for c in conflicts:
            print(f"  {c}")
    all_index_entries = (
        index_entries + geophysics_index_entries + geodesy_index_entries + ecology_index_entries
        + hydromet_index_entries
    )
    print(f"Извлечено индексов пересчёта цен: {len(all_index_entries)}")
    for entry in all_index_entries:
        print(
            f"  {entry.work_type}: {entry.value} на период "
            f"{entry.period_start}..{entry.period_end} ({entry.basis})"
        )
    anchor_warnings = (
        index_warnings + geophysics_index_warnings + geodesy_index_warnings + ecology_index_warnings
        + hydromet_index_warnings + hydromet_warnings + hydromet_road_warnings + split_warnings
    )
    for w in anchor_warnings:
        print(f"  Внимание: {w}")
    print(f"Ставки НДС по периодам: {len(VAT_RATES)}")
    for entry in VAT_RATES:
        print(f"  {entry.rate:.0%} на период {entry.period_start}..{entry.period_end} ({entry.basis})")
    print(f"База записана: {db_path}")

    assumed = [e for e in entries if e.source_assumed]
    print(
        f"[{SHEET_NAME}] Источник не назван в основании — принят {GEOLOGY_ASSUMED_SOURCE} "
        f"(ДОПУЩЕНИЕ): {len(assumed)} позиций, строки {[e.row for e in assumed]}"
    )
    all_entries = (
        entries + geophysics_entries + geodesy_entries + ecology_entries + ecology_road_entries
        + hydromet_entries + hydromet_road_entries + hydromet_supplement
    )
    issues = collect_issues(entries, all_entries, anchor_warnings)
    new_issues = unreviewed_issues(issues)
    print(f"Проблем сборки: {len(issues)}, из них не разобранных: {len(new_issues)}")
    for message in new_issues:
        print(f"  НОВАЯ ПРОБЛЕМА: {message}")
    return new_issues


def source_fingerprint() -> str:
    """Отпечаток исходников справочника (С2): эталонные книги
    `reference_data/`, сам этот сборщик (в нём ставки НДС и константы) и файл
    индексов по кварталам. Изменился — справочник устарел и пересобирается."""
    digest = hashlib.sha256()
    for path in (Path(__file__).resolve(), SOURCE_XLSX, ECOLOGY_SOURCE_XLSX):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    if INDEX_TABLE_PATH.exists():
        digest.update(INDEX_TABLE_PATH.name.encode("utf-8"))
        digest.update(INDEX_TABLE_PATH.read_bytes())
    return digest.hexdigest()


def ensure_references(db_path: Path | None = None) -> str | None:
    """Справочник актуален → None. Отсутствует, повреждён, старой версии
    формата или собран из других исходников → пересобрать и вернуть
    сообщение «пересобран: причина». Пересборка не удалась (нет исходников,
    новые проблемы сборки) → `ReferencesError` с понятным текстом."""
    db_path = Path(db_path) if db_path is not None else DB_PATH
    try:
        fingerprint = source_fingerprint()
    except OSError as exc:
        reason = stale_reason(db_path)
        if reason is None:
            return None  # исходников нет (не та папка), но справочник годен
        raise ReferencesError(
            f"Справочник расценок нельзя использовать: {reason}; пересобрать нельзя — нет "
            f"исходных эталонов ({exc})."
        ) from exc
    reason = stale_reason(db_path, fingerprint)
    if reason is None:
        return None
    # Сборка — во временный файл рядом и замена одним переименованием: другой
    # запущенный экземпляр программы не увидит наполовину записанный справочник (С8).
    tmp_path = db_path.with_name(db_path.name + ".new")
    try:
        new_issues = build(tmp_path)
        tmp_path.replace(db_path)
    except Exception as exc:  # noqa: BLE001 — любой сбой сборки — понятная ошибка оператору
        raise ReferencesError(
            f"Справочник расценок устарел ({reason}), автоматическая пересборка не удалась: "
            f"{exc}. Запустите вручную: python data/build_references.py"
        ) from exc
    if new_issues:
        raise ReferencesError(
            f"Справочник пересобран ({reason}), но сборка нашла новые проблемы данных: "
            + "; ".join(new_issues)
        )
    return f"Справочник расценок пересобран автоматически: {reason}."


def main(argv: list[str] | None = None) -> None:
    """`python data/build_references.py` — пересобрать всегда;
    `--if-stale` — только если справочник устарел (launch_app.bat)."""
    args = sys.argv[1:] if argv is None else argv
    if "--help" in args or "-h" in args:
        # Раньше --help пересобирал справочник: любой запуск без --if-stale — полная сборка.
        print(main.__doc__)
        return
    if "--if-stale" in args:
        try:
            message = ensure_references()
        except ReferencesError as exc:
            print(f"ОШИБКА: {exc}")
            sys.exit(1)
        print(message or "Справочник расценок актуален.")
        return
    if build():
        # Справочник записан (чтобы проблему можно было изучить), но
        # сборка считается неуспешной: setup.bat / build_release.bat
        # останавливаются на ненулевом коде.
        sys.exit(1)


if __name__ == "__main__":
    main()
