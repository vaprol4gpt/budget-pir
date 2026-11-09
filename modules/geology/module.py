"""Заглушка модуля геологии — для проверки сквозной цепочки
ввод -> ведомость объёмов -> смета -> Экспорт.

`build_volumes` реально использует ввод оператора (метраж, категорию
грунта и — теперь — число скважин) и генерирует полную
ведомость по закономерностям из docs/predmetnaya_oblast.md (классы
А/Б/В), а не только строку бурения. `build_estimate` берёт расценки
из настоящего справочника (core/references.py, эталонная смета
1.2.1_ИГИ) — там, где для этого хватает однозначно распознанных
данных; при отсутствии цены строка всё равно строится (price=0,
говорящий basis), а не роняет расчёт (см. записи разработки/
записи разработки).

Категория для запроса в справочник собирается как `f"{soil_category}
кат"`, потому что именно в таком виде ("I кат", "II кат", ...,
"VIII кат") категории хранятся в data/references.sqlite.

**Бурение по нескольким категориям одновременно (комбинированный
грунт)**: реальные ведомости объёмов заказчика (приложение к
бюджету контракта объекта А) показывают, что на
одном объекте бурение почти всегда разбито на несколько категорий
буримости с разным метражом на каждую (например, 172 п.м. кат.II +
602 п.м. кат.IV + 602 п.м. кат.VI + 345 п.м. кат.VIII), а не одна
"средняя" категория на весь объём — расчёт по средней категории
системно искажает стоимость бурения. Поэтому `soil_category`/
`drilling_length` — больше не два отдельных скалярных поля, а одна
группа повторяющихся строк `drilling_by_category` (Field
type="group", см. core/module_base.py); каждая строка — категория +
свой метраж. `build_estimate` строит ОТДЕЛЬНУЮ строку сметы (свой id
`geology.drilling.<категория>`, своя цена из справочника) на каждую
встретившуюся категорию, а не одну усреднённую — цена в справочнике
и так уже разная по категориям (табл.17 п.1 СБЦ-99, I…VIII кат,
36.0…74.4 руб/п.м.), просто раньше оператор мог выбрать только одну
за раз. Метраж-зависимые позиции (камеральная обработка бурения)
считаются от СУММЫ метража по всем категориям — эта зависимость сама
по себе не изменилась, изменился только способ получить общий
метраж. Число скважин осталось отдельным скалярным полем, по
категориям не разбивается — в ведомостях заказчика такой разбивки
нет (см. записи разработки).

**Расход бурового инструмента по категориям буримости — сознательно
НЕ реализован в этой версии.** В переписке по проектированию другого
листа ("08_Буровой_инструмент") встречались коэффициенты категорий
(0,7…2,5 в одном варианте, отличающийся набор — в другом), но это
черновые оценки разработчика, откалиброванные на одном объекте
(ТМК) по фактическому перерасходу инструмента, не норматив из
СБЦ-99 или другого утверждённого документа, и два варианта в
исходном документе не совпадают между собой — подтверждения
достаточно нет. Это ОТДЕЛЬНАЯ позиция (расход инструмента), не то же
самое, что уже реализованная разная цена самого бурения по
категориям — см. docs/known_risks.md, открытый вопрос к нач. ОИГ
(нач. ОИГ)."""

import re
from dataclasses import replace
from typing import Any

from core import param_rules
from core.accruals import (
    EXTERNAL_TRANSPORT,
    ORG_LIQUIDATION,
    REGIONAL_FIELD,
    REGIONAL_OFFICE,
    UNFAVORABLE,
    AccrualScheme,
    accrual_warnings,
    apply_accruals,
    line_id as accrual_line_id,
)
from core.models import EstimateLine
from core.module_base import Field
from core.module_helpers import (
    MISSING_PRICE_BASIS_PREFIX,
    estimate_editable_by,
    get_price_or_missing,
    volume_sheet_editable,
    zero_volume_warnings,
)
from core.object_data import transport_item_by_distance
from core.stages import (
    SOURCE_CLASS_A,
    SOURCE_CLASS_B,
    SOURCE_CLASS_V,
    SOURCE_INPUT,
    SOURCE_LAB,
    SOURCE_OPERATOR,
    Stage,
    VolumeLine,
)
from core.tz import KIND_ESTIMATE, OBJECT_LINEAR, Proposal, TzParams
from modules.geology import key_volumes

DRILLING_VOLUME_KEY_PREFIX = "drilling_cat"
SOURCE = "СБЦ-99"
# Внутренний транспорт: т.4, пункт эталона (без данных объекта) — п.3.
TRANSPORT_TABLE = "4"
TRANSPORT_ITEM = "3"

# Код расценки бурения (табл.17, п.1) — вынесен в константу, т.к.
# используется и в build_estimate (цена по конкретной категории), и в
# field_options (список всех известных категорий для этого кода).
DRILLING_PRICE_SOURCE = "СБЦ-99"
DRILLING_PRICE_TABLE = "17"
DRILLING_PRICE_ITEM = "1"

# Категории в справочнике хранятся как "I кат", "II кат", ...,
# "VIII кат" — но для этого же кода расценки (табл.17, п.1) встречается
# и особый случай "IV кат при глубине скв. до10м" (бурение под
# анкерную систему, см. записи разработки прежних этапов) — у него нет
# однозначного короткого кода без риска столкновения с обычной "IV"
# категорией, поэтому в выпадающий список попадают только строки,
# СТРОГО совпадающие с шаблоном "<римские цифры> кат" (см.
# записи разработки, шаг 2 — почему это не гадание, а техническое
# ограничение уже существующей логики build_volumes/build_estimate).
STANDARD_CATEGORY_PATTERN = re.compile(r"^([IVX]+) кат$")

# Ключи ведомости объёмов для позиций классов А/Б/В из
# docs/predmetnaya_oblast.md (бурение и внутренний транспорт
# обрабатываются отдельно — у них своя, более специфичная логика:
# динамическая категория грунта и доля от базовых строк).
# Число скважин — строка ведомости (реальные ведомости заказчика
# называют его рядом с метражом), но не строка сметы: build_estimate
# этот ключ не читает, и build_volumes его наружу не отдаёт.
VK_DRILLING_COUNT = "drilling_count"
VK_GEODESY_PRIVYAZKA = "geodesy_privyazka"
VK_KAMERAL_BURENIYA = "kameral_bureniya"
VK_PROGRAMMA_RABOT = "programma_rabot"
VK_TEHOTCHET = "tehotchet"
VK_TERMOMETRIYA = "termometriya"
VK_MONOLITY_DISPERSNYE = "monolity_dispersnye"
VK_MONOLITY_SKALNYE = "monolity_skalnye"
VK_PROBY_VODY = "proby_vody"

# Лабораторные позиции (раздел VII эталона) — в отличие от классов
# А/Б/В (проверены на 11 сметах из 5 объектов), эти пропорции выведены
# только из ОДНОЙ эталонной сметы: 1:1 к уже отслеживаемым величинам,
# с проверкой не только по числам, но и по физическому смыслу (каждый
# образец получает соответствующее определение). См.
# docs/predmetnaya_oblast.md и записи разработки за разбором, почему
# остальные позиции раздела VII НЕ реализованы (нет обобщаемой
# пропорции из одного объекта).
VK_LAB_SUMMARNAYA_VLAZHNOST = "lab_summarnaya_vlazhnost"
VK_LAB_ANALIZ_VODY = "lab_analiz_vody"
VK_LAB_SKALNYE_FIZMEH = "lab_skalnye_fizmeh"

# Позиции листа «1.2.1_ИГИ», которых не было в модуле до Э6а (30.10.2026,
# аудит §2.1): объём — прямой ввод оператора (поле этапа ведомости, по
# умолчанию 0) или производная от такого ввода. Пока объём 0 и строку не
# правили — строки нет ни в ведомости, ни в смете (прежние сметы и
# контрольный прогон не меняются из-за них). Строки листа — в PRICE_CODES.
VK_REKOGNOSCIROVKA = "rekognoscirovka"
VK_KREPLENIE = "kreplenie_skvazhin"
VK_GIDRONABLYUDENIYA = "gidronablyudeniya"
VK_SHTAMP = "shtamp"
VK_BAZA = "baza_ekspedicii"
VK_LAB_GLINISTYE_FIZ = "lab_glinistye_fiz"
VK_LAB_PESCHANYE_FIZ = "lab_peschanye_fiz"
VK_LAB_OPT_VLAZHNOST = "lab_opt_vlazhnost"
VK_LAB_KORROZIYA_STAL = "lab_korroziya_stal"
VK_LAB_KORROZIYA_BETON = "lab_korroziya_beton"
VK_SBOR_MATERIALOV = "sbor_materialov"
VK_KAMERAL_REKOGNOSCIROVKI = "kameral_rekognoscirovki"
VK_KAMERAL_BURENIYA_GIDRO = "kameral_bureniya_gidro"

# Поле ввода этапа ведомости → ключ строки ведомости (источник — ввод).
# Подпись поля — наименование строки из PRICE_CODES + единица.
OPTIONAL_INPUT_KEYS = {
    "rekognoscirovka_km": VK_REKOGNOSCIROVKA,
    "kreplenie_length": VK_KREPLENIE,
    "gidronablyudeniya_length": VK_GIDRONABLYUDENIYA,
    "shtamp_count": VK_SHTAMP,
    "baza_months": VK_BAZA,
    "lab_glinistye_count": VK_LAB_GLINISTYE_FIZ,
    "lab_peschanye_count": VK_LAB_PESCHANYE_FIZ,
    "lab_opt_vlazhnost_count": VK_LAB_OPT_VLAZHNOST,
    "lab_korroziya_stal_count": VK_LAB_KORROZIYA_STAL,
    "lab_korroziya_beton_count": VK_LAB_KORROZIYA_BETON,
    "sbor_materialov_count": VK_SBOR_MATERIALOV,
}
# Подписи полей, где наименования строки мало (ревью 30.10.2026, находка 6).
# Т.78: измеритель таблицы в листе не указан (единица «ед.» не сверена);
# в листе 1.2.1_ИГИ под строкой — «к-0,7 — неполный объем работ» при к=1.
OPTIONAL_INPUT_LABELS = {
    "sbor_materialov_count": (
        "Сбор и изучение материалов изысканий прошлых лет (т.78 п.1), ед. — сколько единиц "
        "материалов изучается; измеритель таблицы не сверен с СБЦ-99 (в эталоне 300 ед.)"
    ),
}
# Строки, которые появляются только при объёме > 0 (или после правки).
OPTIONAL_VOLUME_KEYS = set(OPTIONAL_INPUT_KEYS.values()) | {
    VK_KAMERAL_REKOGNOSCIROVKI, VK_KAMERAL_BURENIYA_GIDRO,
}

# Класс Б — устойчивый коэффициент от числа скважин (см.
# docs/predmetnaya_oblast.md).
TERMOMETRIYA_RATE_PER_HOLE = 0.5

# Класс В — типовой профиль по умолчанию: середина диапазона "на
# скважину" из docs/predmetnaya_oblast.md. Слоя правок оператора для
# таких значений пока нет (будущий этап роадмапа), поэтому берём
# середину диапазона — самый нейтральный выбор без дополнительных
# предположений о характере грунта на конкретном объекте (не близко
# к нижней или верхней границе, что уже было бы допущением о
# "лёгком" или "сложном" объекте). См. записи разработки.
MONOLITY_DISPERSNYE_PER_HOLE = (1.39 + 3.57) / 2
MONOLITY_SKALNYE_PER_HOLE = (0.455 + 1.94) / 2
PROBY_VODY_PER_HOLE = (0.125 + 0.214) / 2

# Позиции класса В — их объём посчитан по типовому профилю (не по
# прямому вводу оператора); check() отдельно предупреждает об этом.
DEFAULT_PROFILE_VOLUME_KEYS = {VK_MONOLITY_DISPERSNYE, VK_MONOLITY_SKALNYE, VK_PROBY_VODY}

# Лабораторные позиции — предупреждение слабее по надёжности, чем
# класс В (тот хотя бы усреднён по 11 сметам; эти пропорции — только
# из одного объекта), поэтому помечаются ОТДЕЛЬНОЙ формулировкой в
# check(), а не тем же текстом, что класс В.
LAB_PROFILE_VOLUME_KEYS = {VK_LAB_SUMMARNAYA_VLAZHNOST, VK_LAB_ANALIZ_VODY, VK_LAB_SKALNYE_FIZMEH}

# Расценки для "статичных" позиций ведомости (не для бурения,
# транспорта и техотчёта — у них своя логика, см. build_estimate):
# ключ ведомости -> (таблица, пункт, категория, единица, наименование).
# Категория "точек" у привязки скважин — не категория грунта, а
# единица измерения, которая в исходном листе эталона оказалась в той
# же колонке, что и категории грунта у строк бурения (см.
# записи разработки, шаг 1) — это особенность самого справочника,
# не блажь: если её не передать, get_price не найдёт эту позицию.
# Техотчёт сюда намеренно НЕ входит — это строка типа Б (доля от
# камерального блока), а не qty×price, см. build_estimate и
# docs/predmetnaya_oblast.md.
PRICE_CODES: dict[str, tuple[str, str, str | None, str, str]] = {
    VK_GEODESY_PRIVYAZKA: (
        "93", "3", "точек", "точек", "Плановая и высотная привязка скважин",
    ),
    VK_KAMERAL_BURENIYA: (
        "82", "1", None, "п.м.", "Камеральная обработка материалов буровых работ",
    ),
    VK_PROGRAMMA_RABOT: (
        "81", "2", None, "программа", "Составление программы работ",
    ),
    VK_TERMOMETRIYA: (
        "40", "3", None, "набл.", "Наблюдения в скважинах за температурой пород",
    ),
    VK_MONOLITY_DISPERSNYE: (
        "57", "1", None, "проба", "Отбор монолитов дисперсных грунтов",
    ),
    VK_MONOLITY_SKALNYE: (
        "57", "2", None, "проба", "Отбор монолитов скальных грунтов",
    ),
    VK_PROBY_VODY: (
        "60", "2", None, "проба", "Отбор проб воды",
    ),
    VK_LAB_SUMMARNAYA_VLAZHNOST: (
        "64", "2", None, "опред.", "Суммарная влажность грунтов",
    ),
    VK_LAB_ANALIZ_VODY: (
        "73", "2", None, "анализ", "Стандартный (типовой) анализ воды",
    ),
    VK_LAB_SKALNYE_FIZMEH: (
        "68", "3", None, "компл.", "Полный комплекс физико-механических свойств скальных грунтов",
    ),
    # --- Э6а: позиции листа «1.2.1_ИГИ» (строка листа — в комментарии) ---
    # Полевые. Категория — признак работы, которым справочник разводит
    # конфликтующие ключи листа (data/build_references.py::GEOLOGY_KEY_SPLITS).
    VK_REKOGNOSCIROVKA: (  # стр.19: 15 × 47,2 × 1,2
        "9", "3", "field", "км", "Инженерно-геологическая рекогносцировка",
    ),
    VK_KREPLENIE: (  # стр.34: 150 × 2,1
        "18", "4", None, "п.м.", "Крепление скважин",
    ),
    VK_GIDRONABLYUDENIYA: (  # стр.35: 150 × 1,6 (ссылка листа «т.57 п.2» — см. справочник)
        "57", "2", "гидронаблюдения", "п.м.", "Гидрогеологические наблюдения при бурении скважин",
    ),
    VK_SHTAMP: (  # стр.50: 9 × 570
        "54", "15", None, "испыт.", "Испытание грунтов штампом",
    ),
    VK_BAZA: (  # стр.70: 2 × 5 700 (источник в листе — «СБЦ», см. PRICE_SOURCE)
        "101", "5", None, "мес.", "Содержание базы экспедиции",
    ),
    # Лабораторные.
    VK_LAB_GLINISTYE_FIZ: (  # стр.87: 200 × 47,1
        "63", "8", None, "компл.", "Полный комплекс определений физических свойств глинистых грунтов",
    ),
    VK_LAB_PESCHANYE_FIZ: (  # стр.88: 50 × 45,5
        "65", "1", None, "компл.", "Полный комплекс определений физических свойств песчаных грунтов",
    ),
    VK_LAB_OPT_VLAZHNOST: (  # стр.123: 3 × 40
        "65", "2", None, "компл.", "Комплекс определений оптимальной влажности и плотности",
    ),
    VK_LAB_KORROZIYA_STAL: (  # стр.108: 15 × 18,2
        "75", "4", None, "опред.", "Коррозионная активность грунтов к стали",
    ),
    VK_LAB_KORROZIYA_BETON: (  # стр.109: 15 × 25,4
        "75", "5", None, "опред.", "Коррозионная активность грунтов по отношению к бетону",
    ),
    # Камеральные. Единица т.78 в листе не указана — «ед.» (измеритель
    # таблицы по бумажному СБЦ-99 не сверен).
    VK_SBOR_MATERIALOV: (  # стр.129: 300 × 9
        "78", "1", None, "ед.", "Сбор, изучение и систематизация материалов изысканий прошлых лет",
    ),
    VK_KAMERAL_REKOGNOSCIROVKI: (  # стр.131: 15 × 23,4
        "9", "3", "kameral", "км", "Камеральная обработка инженерно-геологической рекогносцировки",
    ),
    VK_KAMERAL_BURENIYA_GIDRO: (  # стр.134: 150 × 9,3
        "82", "2", None, "п.м.", "Камеральная обработка материалов буровых работ с гидронаблюдениями",
    ),
}

# Источник расценки, если не СБЦ-99: у содержания базы в основании листа
# написано «СБЦ Таблица 101, п.5» — справочник хранит его как есть.
PRICE_SOURCE = {VK_BAZA: "СБЦ"}

# Коэффициенты к расценкам — параметры этапа сметы (ввод оператора, по
# умолчанию 1 — нейтрально; в эталоне стоят значения из подписи поля).
# Норма это или практика компании — вопрос к нач. ОИГ (план работ, Э6а).
#   имя поля → (ключ строки, подпись, основание в листе)
# Подпись — одна строка простым языком: к чему коэффициент и при каком
# условии. Условия примечаний СБЦ-99 по бумажному сборнику не сверены —
# так и написано (ревью 30.10.2026, находка 6).
COEFFICIENT_FIELDS = {
    "k_programma_rabot": (
        VK_PROGRAMMA_RABOT,
        "Коэффициент к цене программы работ (т.81, примечание 1; условие применения "
        "не сверено с СБЦ-99; в эталоне 1,25; 1 — без коэффициента)",
        "СБЦ-99 т.81 прим.1, эталон 1.2.1_ИГИ!E128 = 1,25",
    ),
    "k_monolity_skalnye": (
        VK_MONOLITY_SKALNYE,
        "Коэффициент к цене отбора монолитов скальных грунтов (примечание к т.57; "
        "условие применения не сверено с СБЦ-99; в эталоне 0,7; 1 — без коэффициента)",
        "СБЦ-99 прим. к т.57, эталон 1.2.1_ИГИ!E42 = 0,7",
    ),
    "k_rekognoscirovka": (
        VK_REKOGNOSCIROVKA,
        "Коэффициент к цене полевой рекогносцировки (т.9, примечание 3; условие применения "
        "не сверено с СБЦ-99; в эталоне 1,2; 1 — без коэффициента)",
        "СБЦ-99 т.9 прим.3, эталон 1.2.1_ИГИ!E20 = 1,2",
    ),
}
NEUTRAL_COEFFICIENT = 1.0
# Значения коэффициентов в листе-эталоне 1.2.1_ИГИ (для предупреждения
# «не применены»; сами значения по умолчанию остаются 1 — нужен ответ
# нач. ОИГ, норма это или практика компании).
#   имя поля → (значение в листе, ячейка, что это)
ETALON_COEFFICIENTS = {
    "k_programma_rabot": (1.25, "E128", "программа работ, т.81 прим.1"),
    "k_monolity_skalnye": (0.7, "E42", "отбор монолитов скальных грунтов, прим. к т.57"),
    "k_rekognoscirovka": (1.2, "E20", "рекогносцировка, т.9 прим.3"),
}
def coefficient_default(field_name: str) -> float:
    """Значение коэффициента по умолчанию — из справочника правил параметров
    (core/param_rules.py); ответ нач. ОИГ меняет его там, а не здесь."""
    return param_rules.default("geology", field_name, NEUTRAL_COEFFICIENT)


# Коэффициент 0 обнуляет строку — это не «без коэффициента», а ошибка
# ввода; интерфейс не даёт ввести меньше (core/validation.py::below_min_fields).
COEFFICIENT_MIN_VALUE = 0.01

# Рекогносцировка — т.9 п.3, как в эталоне. Пункты п.1/п.2 т.9 в
# справочнике — инженерно-ЭКОЛОГИЧЕСКАЯ рекогносцировка по проходимости
# (модуль экологии выбирает их по проходимости), для геологии оснований
# выбирать другой пункт нет — пункт взят по объекту эталона.
REKOGNOSCIROVKA_ITEM_NOTE = (
    "пункт т.9 п.3 — по объекту эталона 1.2.1_ИГИ (ДОПУЩЕНИЕ: зависимость пункта от "
    "проходимости для инженерно-геологической рекогносцировки по СБЦ-99 не сверена)"
)

# Камеральная обработка бурения т.82 п.1 = метраж − гидронаблюдения:
# правило выведено из одного листа (D132 = 1 000, D134 = 150, всего 1 150).
KAMERAL_BURENIYA_RULE_NOTE = (
    "ДОПУЩЕНИЕ: правило «п.1 = метраж − гидронаблюдения» выведено из листа 1.2.1_ИГИ "
    "(D132 = 1 000, D134 = 150)"
)

# Короткие подписи лабораторных строк — для наименования и основания
# камеральной обработки лабораторных работ (видно, из чего база).
LAB_SHORT_NAMES = {
    "lab_glinistye_fiz": "физ. свойства глинистых",
    "lab_peschanye_fiz": "физ. свойства песчаных",
    "lab_summarnaya_vlazhnost": "суммарная влажность",
    "lab_skalnye_fizmeh": "физ.-мех. свойства скальных",
    "lab_opt_vlazhnost": "оптимальная влажность и плотность",
    "lab_korroziya_stal": "коррозия к стали",
    "lab_korroziya_beton": "коррозия к бетону",
}

# Камеральная обработка хим.состава воды (табл.86 п.5) — единственная
# из 4 камеральных лабораторных строк эталона (86/1,2,5,8), чья база
# в оригинале — ОДНА конкретная лабораторная строка (анализ воды), а
# не смесь нескольких, которых у нас пока нет в ведомости. См.
# записи разработки, шаг 3, за разбором остальных трёх (не реализованы).
KAMERALNAYA_VODY_BASE_IDS = [f"geology.{VK_LAB_ANALIZ_VODY}"]

# Камеральная обработка лабораторных работ (Э6а) — доля от суммы
# лабораторных строк, как в формулах листа «1.2.1_ИГИ»:
#   D136 (т.86 п.1, глинистые, 0,2) = H87 (+ строки, которых в модуле нет);
#   D137 (т.86 п.2, песчаные, 0,15) = H88, H92, H122, H123 (+ строки с объёмом 0);
#   D139 (т.86 п.8, коррозия, 0,15) = H108 + H109.
# В листе в базу п.2 входят суммарная влажность (H92) и скальные (H122) —
# строки, которые модуль строит всегда; поэтому строка п.2 есть в каждой
# смете (это меняет итог модуля — намеренно, по формуле листа).
# Строка создаётся, если в смете есть хотя бы одна строка её базы.
# строка → (пункт т.86, наименование, ключи лабораторных строк базы)
# Строка не создаётся и при нулевой базе (все строки базы с объёмом 0).
# У п.2 наименование листа («…песчаных грунтов») вводит в заблуждение — в
# базе и влажность, и скальные; наименование и основание перечисляют
# строки базы (ревью 30.10.2026, находка 4).
KAMERAL_LAB_LINES = {
    "geology.kameral_lab_glinistye": (
        "1", "Камеральная обработка лабораторных работ глинистых грунтов",
        [VK_LAB_GLINISTYE_FIZ],
    ),
    "geology.kameral_lab_peschanye": (
        "2", "Камеральная обработка лабораторных работ (т.86 п.2)",
        [VK_LAB_PESCHANYE_FIZ, VK_LAB_SUMMARNAYA_VLAZHNOST, VK_LAB_SKALNYE_FIZMEH, VK_LAB_OPT_VLAZHNOST],
    ),
    "geology.kameral_korroziya": (
        "8", "Камеральная обработка коррозионной активности",
        [VK_LAB_KORROZIYA_STAL, VK_LAB_KORROZIYA_BETON],
    ),
}
KAMERAL_LAB_SHEET_CELLS = {
    "geology.kameral_lab_glinistye": "D136",
    "geology.kameral_lab_peschanye": "D137",
    "geology.kameral_korroziya": "D139",
}

# "Камеральный блок", от которого считается доля техотчёта: с Э6а
# (30.10.2026) — ВСЕ камеральные строки сметы, как в листе:
# 1.2.1_ИГИ!D148 = H146 = «Итого п.п. 21-28» = SUM(H127:H145) (программа,
# сбор материалов, камералка рекогносцировки, бурения, лабораторных, воды).
# До Э6а база была из двух строк (программа + камералка бурения) — это
# занижало техотчёт (аудит §3.1: 2 184,6 вместо 3 729). Набор строк —
# группа "office" схемы начислений (ACCRUALS) без самого техотчёта.

# Позиции ведомости без расценки в PRICE_CODES (у техотчёта своя
# логика цены — доля от камерального блока, см. build_estimate).
VOLUME_ONLY_NAMES = {VK_TEHOTCHET: "Составление технического отчёта"}
VOLUME_ONLY_UNITS = {VK_TEHOTCHET: "отчёт"}


def volume_key_for_estimate_line(line_id: str) -> str | None:
    """Ключ строки ведомости, от которой строка сметы берёт объём:
    `geology.drilling.IV` → `drilling_catIV`, `geology.<ключ>` →
    `<ключ>`. У строк без своей строки ведомости (транспорт,
    камеральная обработка хим.состава воды) такого ключа в ведомости
    не окажется — вызывающий код проверяет это по снимку ведомости."""
    if line_id.startswith("geology.drilling."):
        return f"{DRILLING_VOLUME_KEY_PREFIX}{line_id[len('geology.drilling.'):]}"
    if line_id.startswith("geology."):
        return line_id[len("geology."):]
    return None


# Начисления уровня сметы (С3, core/accruals.py) — по формулам эталонного
# листа «1.2.1_ИГИ» (Автодорога), строки 82, 151–158:
#   H82  неблагоприятный ×1,2 к полевым п.1–11 (лабораторные НЕ входят), т.2 п.4;
#   H152 внешний транспорт = ROUND((полевые×1,2 + вн.тр. + лабораторные) × ставка);
#   H153 оргликвидация = ROUND(та же база × 6 % × 2,5);
#   D156 районный к полевым — от полевых×1,2 + вн.тр. + внеш.тр. + оргликв. + лабораторные;
#   D157 районный к камеральным — от камеральных + техотчёта.
# Неблагоприятный, внешний транспорт и оргликвидация округляются до рубля
# (ROUND(…,0) в листе), районные — нет. Внутренний транспорт (с Э6а) — от
# всех полевых с надбавкой за неблагоприятный период, как в листе
# (D151 = H82 = H81 × 1,2), см. build_estimate и stages().
_FIELD_LINES = ("geology.drilling.", "geology.geodesy_privyazka", "geology.termometriya",
                "geology.monolity_", "geology.proby_vody")
# Э6а: полевые — все строки разделов I–VI листа (H81 = «Итого п.п. 1-11»),
# камеральные — раздел VIII (H146) + техотчёт (H148).
_FIELD_LINES = _FIELD_LINES + (
    f"geology.{VK_REKOGNOSCIROVKA}", f"geology.{VK_KREPLENIE}", f"geology.{VK_GIDRONABLYUDENIYA}",
    f"geology.{VK_SHTAMP}", f"geology.{VK_BAZA}",
)
_OFFICE_LINES = ("geology.kameral", "geology.programma_rabot", f"geology.{VK_SBOR_MATERIALOV}",
                 "geology.tehotchet")
ACCRUALS = AccrualScheme(
    module="geology",
    groups={
        "field": lambda i: i.startswith(_FIELD_LINES),
        "lab": lambda i: i.startswith("geology.lab_"),
        "office": lambda i: i.startswith(_OFFICE_LINES),
        "internal_transport": lambda i: i == "geology.transport",
    },
    bases={
        UNFAVORABLE: ("field",),
        EXTERNAL_TRANSPORT: ("field", UNFAVORABLE, "internal_transport", "lab"),
        ORG_LIQUIDATION: ("field", UNFAVORABLE, "internal_transport", "lab"),
        REGIONAL_FIELD: ("field", UNFAVORABLE, "internal_transport", EXTERNAL_TRANSPORT,
                         ORG_LIQUIDATION, "lab"),
        REGIONAL_OFFICE: ("office",),
    },
    unfavorable_source=("СБЦ-99", "2", "4"),
    # районные — без округления (H157 = 5 169,75): в листе округлена их база
    rounding={name: 0 for name in (UNFAVORABLE, EXTERNAL_TRANSPORT, ORG_LIQUIDATION)},
    sources={
        UNFAVORABLE: "СБЦ-99 т.2 п.4 (эталон 1.2.1_ИГИ!H82)",
        EXTERNAL_TRANSPORT: "СБЦ-99 т.5 (эталон 1.2.1_ИГИ!H152)",
        ORG_LIQUIDATION: "ОУ п.13, прим.1 (эталон 1.2.1_ИГИ!H153)",
        REGIONAL_FIELD: "ОУ т.3 п.9, п.8е (эталон 1.2.1_ИГИ!D156)",
        REGIONAL_OFFICE: "ОУ т.3 п.3, п.8е (эталон 1.2.1_ИГИ!D157)",
    },
)



# Находка ревью №10 (30.10.2026): объёмы, которые по смыслу работ и эталонам
# должны быть > 0, когда раздел не пустой, — предупреждение этапа ведомости
# (core/module_helpers.py::zero_volume_warnings; список — список вопросов В-8).
VOLUME_EXPECTED_NONZERO = {}
VOLUME_EXPECTED_NONZERO_IN_ROWS = {"drilling_by_category": ("drilling_length", "Метраж бурения, п.м.")}


def coefficient_warnings(estimate: dict[str, EstimateLine]) -> list[str]:
    """Коэффициенты к расценкам (COEFFICIENT_FIELDS): не задан, а в листе-
    эталоне стоит (только если строка есть в смете), и коэффициент ≤ 0
    (обнуляет строку) — ревью 30.10.2026, находки 1 и 5."""
    warnings = []
    missing = []
    for field_name, (key, _label, _basis) in COEFFICIENT_FIELDS.items():
        line = estimate.get(f"geology.{key}")
        if line is None:
            continue
        k = line.coefs.get("k")
        if k is None:
            value, cell, what = ETALON_COEFFICIENTS[field_name]
            missing.append(f"{what} — в эталоне {f'{value:g}'.replace('.', ',')} ({cell})")
        elif k <= 0:
            warnings.append(
                f"«{line.name}»: коэффициент {k:g} — строка обнуляется; коэффициент должен "
                "быть больше 0 (1 — без коэффициента)"
            )
    if missing:
        warnings.insert(0,
            "Коэффициенты листа-эталона 1.2.1_ИГИ не применены (сейчас 1): "
            + "; ".join(missing)
            + ". Применяйте, если условия вашего объекта те же (норма это или практика "
            "компании — вопрос к нач. ОИГ)"
        )
    return warnings


def _volume_zero_warnings(inputs, previous, refs):
    return zero_volume_warnings(inputs, VOLUME_EXPECTED_NONZERO, VOLUME_EXPECTED_NONZERO_IN_ROWS)

class GeologyModule:
    code = "geology"

    def input_schema(self) -> list[Field]:
        return [
            Field(
                name="drilling_by_category",
                label="Бурение по категориям грунта",
                type="group",
                required=True,
                group_fields=[
                    Field(
                        name="soil_category",
                        label="Категория грунта",
                        type="text",
                        required=True,
                        dynamic_options=True,
                    ),
                    Field(
                        name="drilling_length",
                        label="Метраж бурения, п.м.",
                        type="number",
                        required=True,
                    ),
                ],
            ),
            Field(
                name="drilling_count",
                label="Число скважин",
                type="number",
                required=True,
                integer=True,
                # 0 скважин — не объект изысканий, а незаполненное поле.
                positive=True,
            ),
            # Э6а: остальные позиции листа «1.2.1_ИГИ» — необязательный
            # ввод, по умолчанию 0 (строки нет). Старые сохранённые вводы
            # этих полей не содержат — читаются как 0.
            *[
                Field(
                    name=field_name,
                    label=OPTIONAL_INPUT_LABELS.get(
                        field_name, f"{PRICE_CODES[key][4]}, {PRICE_CODES[key][3]}"
                    ),
                    type="number",
                    required=False,
                    integer=PRICE_CODES[key][3] in ("испыт.", "компл.", "опред.", "ед."),
                )
                for field_name, key in OPTIONAL_INPUT_KEYS.items()
            ],
            # Параметры этапа сметы (stages() относит их ко второму этапу,
            # как у экологии): коэффициенты к расценкам; значение по умолчанию —
            # справочник правил data/pravila_parametrov.json (сейчас 1).
            *[
                Field(name=name, label=label, type="number", required=False,
                      default=coefficient_default(name), min_value=COEFFICIENT_MIN_VALUE)
                for name, (_key, label, _basis) in COEFFICIENT_FIELDS.items()
            ],
        ]

    def field_options(self, field_name: str, refs: Any) -> list[tuple[str, str]]:
        if field_name != "soil_category":
            return []

        categories = refs.get_categories(
            DRILLING_PRICE_SOURCE, DRILLING_PRICE_TABLE, DRILLING_PRICE_ITEM
        )
        options = []
        for category, price in categories:
            match = STANDARD_CATEGORY_PATTERN.match(category)
            if not match:
                continue
            code = match.group(1)
            options.append((code, f"{code} — {price:g} руб/п.м."))
        return options

    def propose_volumes(self, tz: TzParams) -> Proposal:
        """Черновик ввода этапа ведомости по параметрам ТЗ (core/tz.py, 03.11.2026).
        Заполняется только ВВОД: привязку, камеральную обработку, программу, отчёт
        (класс А), термометрию (Б), монолиты и пробы воды (В) модуль, как и прежде,
        считает сам из числа скважин и метража (build_volume_sheet)."""
        proposal = Proposal()
        proposal.set_derived(
            "drilling_count", tz.holes(),
            "нужно решение исполнителя: число скважин или шаг выработок (стр. 1) — в ТЗ "
            "их нет, шаг и глубина — по СП",
            int,
        )
        total = tz.drilling_total()
        if total is not None:
            proposal.set(
                "drilling_by_category", [{"soil_category": None, "drilling_length": total.value}],
                total.kind,
                f"метраж {total.note}; категория грунта — ввод: выберите фактические категории "
                "буримости и разнесите метраж по ним (из ТЗ не выводится)",
            )
        else:
            proposal.leave(
                "drilling_by_category",
                "метраж = число скважин × средняя глубина (решения исполнителя, стр. 1); "
                "категории грунта из ТЗ не выводятся",
            )
        if tz.object_kind == OBJECT_LINEAR and tz.length_km:
            proposal.set(
                "rekognoscirovka_km", tz.length_km, KIND_ESTIMATE,
                "= протяжённость трассы из ТЗ (ДОПУЩЕНИЕ: рекогносцировка вдоль всей трассы)",
            )
        if tz.permafrost is False:
            proposal.notes.append(
                "По ТЗ многолетнемёрзлых грунтов (ММГ) нет, а термометрию (0,5 × число скважин) "
                "модуль добавляет всегда — проверьте строку «Термометрия» и при необходимости "
                "обнулите её правкой строки."
            )
        elif tz.permafrost:
            proposal.notes.append(
                "ММГ есть — термометрия в скважинах нужна; модуль считает её сам "
                "(0,5 × число скважин, устойчивый коэффициент)."
            )
        proposal.notes.append(
            "Монолиты и пробы воды модуль предлагает по типовому профилю (середина диапазона "
            "на скважину) — проверьте по программе изысканий."
        )
        return proposal

    def key_volume_fields(self) -> list[Field]:
        """Ключевые объёмы раздела для режима «ключевые объёмы» (core/tz.py)."""
        return key_volumes.key_volume_fields()

    def propose_from_key_volumes(self, tz: TzParams, keys: dict) -> Proposal:
        """Черновик ввода ведомости по ключевым объёмам: предложение по ТЗ +
        ключевые объёмы + достроенное по связям (modules/geology/key_volumes.py)."""
        return key_volumes.propose(self, tz, keys)

    def build_volumes(self, data: dict, refs: Any) -> dict:
        # Прежний одноэтапный вход сметы: {ключ ведомости: объём}. Сама
        # логика — в build_volume_sheet (общая с многоэтапным
        # сценарием), здесь — без правок оператора и без строки "Число
        # скважин" (build_estimate её не читает).
        sheet = self.build_volume_sheet(data)
        volumes = {key: line.qty for key, line in sheet.items() if key != VK_DRILLING_COUNT}
        # Коэффициенты (параметры сметы) одноэтапный путь передаёт вместе
        # с объёмами — build_estimate читает их оттуда (как у экологии).
        for name in COEFFICIENT_FIELDS:
            if data.get(name) is not None:
                volumes[name] = data[name]
        return volumes

    def build_volume_sheet(
        self, data: dict, pinned: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, VolumeLine]:
        """Ведомость объёмов (этап 1 многоэтапного сценария).

        `pinned` — правки оператора этого этапа (`{ключ: {"qty": v}}`,
        см. core/stages.py::pinned_values). Зависимая позиция считается
        от ЗАКРЕПЛЁННОГО значения своей позиции-источника, а
        собственная правка зависимой позиции побеждает формулу (решение
        №6, docs/multistage_design.md). Например, правка "монолитов
        дисперсных" пересчитывает "суммарную влажность", если её не
        правили отдельно.

        Строки ввода (метраж по категориям, число скважин) правкой не
        закрепляются — они меняются в форме ввода."""
        pinned = pinned or {}
        lines: dict[str, VolumeLine] = {}

        # Несколько строк "категория + метраж" (комбинированный грунт,
        # см. докстринг модуля) — суммируем метраж ПО категории (на
        # случай, если оператор внёс одну категорию несколькими
        # строками) и отдельно суммарный метраж по всем категориям для
        # позиций, зависящих от общего объёма бурения, а не от разбивки.
        drilling_by_category: dict[str, float] = {}
        category_by_key: dict[str, Any] = {}
        for row in data.get("drilling_by_category") or []:
            soil_category = row.get("soil_category")
            row_length = row.get("drilling_length", 0) or 0
            key = f"{DRILLING_VOLUME_KEY_PREFIX}{soil_category}"
            drilling_by_category[key] = drilling_by_category.get(key, 0) + row_length
            category_by_key[key] = soil_category

        for key, length in drilling_by_category.items():
            lines[key] = VolumeLine(
                id=key,
                name=f"Колонковое бурение скважин диам. до 160 мм, {category_by_key[key]} кат.",
                unit="п.м.",
                qty=length,
                source=SOURCE_INPUT,
                basis="метраж по категории грунта",
            )

        drilling_count = data.get("drilling_count", 0)
        lines[VK_DRILLING_COUNT] = VolumeLine(
            id=VK_DRILLING_COUNT,
            name="Число скважин",
            unit="скв.",
            qty=drilling_count,
            source=SOURCE_INPUT,
            basis="ввод оператора",
        )

        def add(key: str, generated: float, source: str, basis: str) -> float:
            if key in PRICE_CODES:
                _table, _item, _category, unit, name = PRICE_CODES[key]
            else:
                unit, name = VOLUME_ONLY_UNITS[key], VOLUME_ONLY_NAMES[key]
            override = pinned.get(key, {})
            if "qty" in override:
                qty, source = override["qty"], SOURCE_OPERATOR
            else:
                qty = generated
            if key in OPTIONAL_VOLUME_KEYS and not qty and "qty" not in override:
                return 0  # необязательная позиция с объёмом 0 — строки нет
            lines[key] = VolumeLine(key, name, unit, qty, source, basis)
            return qty

        total_drilling_length = sum(drilling_by_category.values())

        # Э6а: необязательные позиции листа — прямой ввод оператора.
        entered = {
            key: data.get(field_name) or 0 for field_name, key in OPTIONAL_INPUT_KEYS.items()
        }
        for key, qty in entered.items():
            basis = "ввод оператора"
            if key == VK_REKOGNOSCIROVKA:
                basis = f"{basis}; {REKOGNOSCIROVKA_ITEM_NOTE}"
            add(key, qty, SOURCE_INPUT, basis)
        gidro = lines[VK_GIDRONABLYUDENIYA].qty if VK_GIDRONABLYUDENIYA in lines else 0
        rekognoscirovka = lines[VK_REKOGNOSCIROVKA].qty if VK_REKOGNOSCIROVKA in lines else 0

        # Класс А — жёсткая связь с числом скважин/метражом.
        add(VK_GEODESY_PRIVYAZKA, drilling_count, SOURCE_CLASS_A, "= число скважин")
        # Камеральная обработка бурения: т.82 п.1 — метраж без
        # гидронаблюдений, п.2 — метраж с гидронаблюдениями. В листе
        # 1.2.1_ИГИ: D132 = 1 000 (п.1) + D134 = 150 (п.2) = 1 150 — весь
        # метраж бурения (H29:H33), 150 = объём гидронаблюдений (D35).
        # Без гидронаблюдений — весь метраж в п.1, как раньше.
        add(
            VK_KAMERAL_BURENIYA, max(total_drilling_length - gidro, 0), SOURCE_CLASS_A,
            "= суммарный метраж бурения"
            + (f" − метраж с гидронаблюдениями ({KAMERAL_BURENIYA_RULE_NOTE})" if gidro else ""),
        )
        add(VK_KAMERAL_BURENIYA_GIDRO, gidro, SOURCE_CLASS_A, "= метраж с гидронаблюдениями")
        add(VK_KAMERAL_REKOGNOSCIROVKI, rekognoscirovka, SOURCE_CLASS_A, "= объём рекогносцировки")
        add(VK_PROGRAMMA_RABOT, 1, SOURCE_CLASS_A, "одна на объект")
        add(VK_TEHOTCHET, 1, SOURCE_CLASS_A, "один на объект")
        # Класс Б — устойчивый коэффициент от числа скважин.
        add(
            VK_TERMOMETRIYA, TERMOMETRIYA_RATE_PER_HOLE * drilling_count, SOURCE_CLASS_B,
            f"= {TERMOMETRIYA_RATE_PER_HOLE:g} × число скважин",
        )
        # Класс В — связи с метражом нет; типовой профиль по умолчанию
        # (середина диапазона на скважину, см. выше), оператор вводит
        # фактическое значение правкой строки (см. check_volume_sheet).
        monolity_dispersnye = add(
            VK_MONOLITY_DISPERSNYE, MONOLITY_DISPERSNYE_PER_HOLE * drilling_count, SOURCE_CLASS_V,
            f"типовой профиль {MONOLITY_DISPERSNYE_PER_HOLE:g} на скважину",
        )
        monolity_skalnye = add(
            VK_MONOLITY_SKALNYE, MONOLITY_SKALNYE_PER_HOLE * drilling_count, SOURCE_CLASS_V,
            f"типовой профиль {MONOLITY_SKALNYE_PER_HOLE:g} на скважину",
        )
        proby_vody = add(
            VK_PROBY_VODY, PROBY_VODY_PER_HOLE * drilling_count, SOURCE_CLASS_V,
            f"типовой профиль {PROBY_VODY_PER_HOLE:g} на скважину",
        )
        # Лабораторные позиции — пропорция 1:1 к образцам/пробам,
        # выведена из одной эталонной сметы (см. docs/predmetnaya_oblast.md).
        add(VK_LAB_SUMMARNAYA_VLAZHNOST, monolity_dispersnye, SOURCE_LAB, "= монолиты дисперсных (1:1)")
        add(VK_LAB_ANALIZ_VODY, proby_vody, SOURCE_LAB, "= пробы воды (1:1)")
        add(VK_LAB_SKALNYE_FIZMEH, monolity_skalnye, SOURCE_LAB, "= монолиты скальных (1:1)")

        return lines

    def build_estimate(
        self, volumes: dict, refs: Any, parameters: dict | None = None
    ) -> dict[str, EstimateLine]:
        """`parameters` — ввод этапа сметы (коэффициенты COEFFICIENT_FIELDS);
        не задан / пусто — коэффициент 1, строка как раньше."""
        # Одна строка сметы НА КАЖДУЮ встретившуюся категорию грунта
        # (комбинированный грунт, см. докстринг модуля) — не одна
        # усреднённая. Порядок ключей volumes (а значит и порядок
        # категорий) — порядок их первого появления в data (dict в
        # Python 3.7+ сохраняет порядок вставки), это не влияет на
        # расчёт, только на порядок строк в выгрузке.
        drilling_keys = [k for k in volumes if k.startswith(DRILLING_VOLUME_KEY_PREFIX)]

        drilling_lines: dict[str, EstimateLine] = {}
        for drilling_key in drilling_keys:
            soil_category = drilling_key[len(DRILLING_VOLUME_KEY_PREFIX):]
            category_label = f"{soil_category} кат"
            drilling_name = f"Колонковое бурение скважин диам. до 160 мм, {category_label}."

            drilling_price, drilling_basis = get_price_or_missing(
                refs, SOURCE, DRILLING_PRICE_TABLE, DRILLING_PRICE_ITEM, category_label, drilling_name
            )
            line_id = f"geology.drilling.{soil_category}"
            drilling_lines[line_id] = EstimateLine(
                id=line_id,
                name=drilling_name,
                unit="п.м.",
                qty=volumes[drilling_key],
                price=drilling_price,
                coefs={},
                kind="A",
                base_ids=[],
                basis=drilling_basis,
            )

        # Пункт т.4 — по расстоянию до объекта (данные объекта в refs,
        # core/object_data.py, Э5); расстояние не задано — п.3, как в
        # эталоне (основание строки при этом прежнее). Через
        # get_price_or_missing, как остальные позиции: нет ставки в
        # справочнике — строка с основанием «цена не найдена» и
        # предупреждением check(), а не KeyError на весь расчёт.
        transport_choice = transport_item_by_distance(refs, SOURCE, TRANSPORT_TABLE, TRANSPORT_ITEM, module=self.code)
        transport_rate, transport_basis = get_price_or_missing(
            refs, SOURCE, TRANSPORT_TABLE, transport_choice.item, None, "Внутренний транспорт"
        )
        if transport_choice.note:
            transport_basis = f"{transport_basis} — {transport_choice.note}"
        transport = EstimateLine(
            id="geology.transport",
            name="Внутренний транспорт",
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": transport_rate},
            # База — все полевые строки (заполняется ниже, когда они
            # построены). До Э6а — только бурение; в листе 1.2.1_ИГИ
            # D151 = H82 — все полевые п.1–11 (× 1,2 за неблагоприятный
            # период — его добавляет stages() после начислений).
            base_ids=[],
            kind="B",
            basis=transport_basis,
        )

        lines: dict[str, EstimateLine] = {**drilling_lines, transport.id: transport}

        parameters = {
            **{name: volumes[name] for name in COEFFICIENT_FIELDS if name in volumes},
            **(parameters or {}),
        }
        coefficients: dict[str, tuple[float, str]] = {}
        for field_name, (key, _label, coef_basis) in COEFFICIENT_FIELDS.items():
            value = parameters.get(field_name)
            if value is None:
                value = coefficient_default(field_name)
            if value is not None and value != NEUTRAL_COEFFICIENT:
                coefficients[key] = (float(value), coef_basis)

        for volume_key, (table, item, category, unit, name) in PRICE_CODES.items():
            if volume_key in OPTIONAL_VOLUME_KEYS and volume_key not in volumes:
                continue  # необязательная позиция без объёма — строки нет
            price, basis = get_price_or_missing(
                refs, PRICE_SOURCE.get(volume_key, SOURCE), table, item, category, name
            )
            if volume_key in (VK_REKOGNOSCIROVKA, VK_KAMERAL_REKOGNOSCIROVKI):
                basis = f"{basis}; {REKOGNOSCIROVKA_ITEM_NOTE}"
            coefs = {}
            if volume_key in coefficients:
                value, coef_basis = coefficients[volume_key]
                coefs = {"k": value}
                basis = f"{basis}; × к={value:g} — ввод оператора ({coef_basis})"
            line_id = f"geology.{volume_key}"
            lines[line_id] = EstimateLine(
                id=line_id,
                name=name,
                unit=unit,
                qty=volumes[volume_key],
                price=price,
                coefs=coefs,
                kind="A",
                base_ids=[],
                basis=basis,
            )

        # Техотчёт — не qty×price, а доля (22%) от камерального блока
        # (см. КAMERALNYI_BLOK_LINE_IDS выше и docs/predmetnaya_oblast.md).
        # В эталоне это видно по формуле "16950x0,22" — 16950 сама уже
        # сумма нескольких камеральных строк, 0.22 — ставка, а не
        # готовая цена за единицу.
        tehotchet_name = "Составление технического отчёта"
        tehotchet_rate, tehotchet_basis = get_price_or_missing(
            refs, SOURCE, "87", "2", None, tehotchet_name
        )
        tehotchet = EstimateLine(
            id=f"geology.{VK_TEHOTCHET}",
            name=tehotchet_name,
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": tehotchet_rate},
            kind="B",
            base_ids=[],  # все камеральные строки — заполняется в конце
            basis=tehotchet_basis,
        )
        lines[tehotchet.id] = tehotchet

        # Камеральная обработка хим.состава воды — доля (15%) от
        # КОНКРЕТНОЙ лабораторной строки-источника (анализ воды), а не
        # от блока целиком (см. KAMERALNAYA_VODY_BASE_IDS выше и
        # записи разработки, шаг 3).
        kameralnaya_vody_name = "Камеральная обработка хим.состава воды"
        kameralnaya_vody_rate, kameralnaya_vody_basis = get_price_or_missing(
            refs, SOURCE, "86", "5", None, kameralnaya_vody_name
        )
        kameralnaya_vody = EstimateLine(
            id="geology.kameralnaya_himsostav_vody",
            name=kameralnaya_vody_name,
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": kameralnaya_vody_rate},
            kind="B",
            base_ids=KAMERALNAYA_VODY_BASE_IDS,
            basis=kameralnaya_vody_basis,
        )
        lines[kameralnaya_vody.id] = kameralnaya_vody

        # Камеральная обработка лабораторных работ (т.86 п.1/2/8, Э6а).
        # Строки базы с объёмом 0 в базу не берутся; база пустая — строки нет.
        for line_id, (item, name, base_keys) in KAMERAL_LAB_LINES.items():
            present = [key for key in base_keys
                       if f"geology.{key}" in lines and lines[f"geology.{key}"].qty]
            base_ids = [f"geology.{key}" for key in present]
            if not base_ids:
                continue
            rate, basis = get_price_or_missing(refs, SOURCE, "86", item, None, name)
            base_names = ", ".join(LAB_SHORT_NAMES[key] for key in present)
            if item == "2":
                name = f"{name}: {base_names}"
            basis = (f"{basis}; база — лабораторные строки: {base_names} "
                     f"(как {KAMERAL_LAB_SHEET_CELLS[line_id]} листа 1.2.1_ИГИ)")
            lines[line_id] = EstimateLine(
                id=line_id, name=name, unit="%", qty=0.0, price=0.0,
                coefs={"rate": rate}, kind="B", base_ids=base_ids, basis=basis,
            )

        # Базы как в листе 1.2.1_ИГИ: внутренний транспорт — все полевые
        # (D151), техотчёт — все камеральные (D148 = H146).
        transport.base_ids = [i for i in lines if ACCRUALS.groups["field"](i)]
        tehotchet.base_ids = [
            i for i in lines if ACCRUALS.groups["office"](i) and i != tehotchet.id
        ]

        return lines

    # --- многоэтапный сценарий (core/stages.py) ---

    def stages(self) -> list[Stage]:
        """Два этапа: ведомость объёмов → смета. Прежний одноэтапный
        API (build_volumes/build_estimate/check) сохраняется как есть."""

        schema = self.input_schema()
        volume_fields = [f for f in schema if f.name not in COEFFICIENT_FIELDS]
        estimate_fields = [f for f in schema if f.name in COEFFICIENT_FIELDS]

        def compute_volumes(inputs, previous, refs, pinned):
            return self.build_volume_sheet(inputs, pinned)

        def compute_estimate(inputs, previous, refs, pinned):
            volumes = {
                key: qty
                for key, qty in previous["volumes"].values().items()
                if key != VK_DRILLING_COUNT
            }
            # Начисления уровня сметы (С3) — по ставкам данных объекта;
            # не заданы — смета как раньше (core/accruals.py).
            lines = apply_accruals(
                self.build_estimate(volumes, refs=refs, parameters=inputs), ACCRUALS, refs
            )
            # Внутренний транспорт — от полевых С УЧЁТОМ надбавки за
            # неблагоприятный период, если она начислена (лист 1.2.1_ИГИ:
            # D151 = H82 = H81 × 1,2). Надбавка считается от полевых без
            # транспорта — цикла нет.
            unfavorable_id = accrual_line_id(ACCRUALS, UNFAVORABLE)
            if unfavorable_id in lines and "geology.transport" in lines:
                transport = lines["geology.transport"]
                lines["geology.transport"] = replace(
                    transport, base_ids=[*transport.base_ids, unfavorable_id]
                )
            return lines

        # Строки ввода ведомости меняются в форме; на этапе сметы объём
        # строки с утверждённой ведомостью не правится (решение №5), у
        # строк «доля от базы» правится ставка (core/module_helpers.py).
        volumes_editable = volume_sheet_editable
        estimate_editable = estimate_editable_by(volume_key_for_estimate_line)

        def estimate_context_warnings(inputs, previous, refs):
            choice = transport_item_by_distance(refs, SOURCE, TRANSPORT_TABLE, TRANSPORT_ITEM, module=self.code)
            return ([choice.warning] if choice.warning else []) + accrual_warnings(ACCRUALS, refs)

        return [
            Stage(
                number=1,
                key="volumes",
                title="Ведомость объёмов работ",
                fields=volume_fields,
                compute=compute_volumes,
                check=self.check_volume_sheet,
                editable_fields=volumes_editable,
                confirm_label="Подтвердить ведомость",
                context_warnings=_volume_zero_warnings,
            ),
            Stage(
                number=2,
                key="estimate",
                title="Смета",
                # Коэффициенты к расценкам (программа, монолиты скальных,
                # рекогносцировка) — ввод оператора, по умолчанию 1.
                fields=estimate_fields,
                compute=compute_estimate,
                check=self.check_estimate,
                editable_fields=estimate_editable,
                confirm_label="Подтвердить смету",
                context_warnings=estimate_context_warnings,
            ),
        ]

    def check_volume_sheet(self, lines: dict[str, VolumeLine]) -> list[str]:
        """Предупреждения этапа ведомости: значения по типовому и
        лабораторному профилю, которые оператор ещё не заменил своими."""
        warnings = []
        for line in lines.values():
            if line.source == SOURCE_CLASS_V:
                warnings.append(
                    f"«{line.name}»: объём по типовому профилю по умолчанию "
                    "(среднее из диапазона на скважину, см. "
                    "типовой объект) — введите фактическое значение "
                    "или подтвердите типовое"
                )
            elif line.source == SOURCE_LAB:
                warnings.append(
                    f"«{line.name}»: лабораторный профиль, выведен из одного "
                    "объекта (1:1 к образцам/пробам), требует проверки"
                )
        # Гидронаблюдения длиннее всего метража: камералка т.82 п.1 тогда 0,
        # а п.2 берёт больше, чем пробурено (правило — ДОПУЩЕНИЕ по листу).
        drilled = sum(
            line.qty or 0 for key, line in lines.items()
            if key.startswith(DRILLING_VOLUME_KEY_PREFIX)
        )
        gidro = lines.get(VK_GIDRONABLYUDENIYA)
        if gidro is not None and (gidro.qty or 0) > drilled:
            warnings.append(
                f"«{gidro.name}»: {gidro.qty:g} п.м. — больше всего метража бурения "
                f"({drilled:g} п.м.); гидронаблюдения ведутся в пробуренных скважинах, "
                "проверьте объём (камеральная обработка бурения без гидронаблюдений "
                "получилась 0)"
            )
        return warnings

    def check_estimate(self, estimate: dict[str, EstimateLine]) -> list[str]:
        """Предупреждения этапа сметы: цены, коэффициенты листа-эталона,
        пункт рекогносцировки (о профилях объёмов предупреждает этап
        ведомости)."""
        warnings = [
            f"Цена для «{line.name}» не найдена в справочнике, требуется ручная проверка"
            for line in estimate.values()
            if line.basis.startswith(MISSING_PRICE_BASIS_PREFIX)
        ]
        warnings.extend(coefficient_warnings(estimate))
        rekognoscirovka = estimate.get(f"geology.{VK_REKOGNOSCIROVKA}")
        if rekognoscirovka is not None:
            warnings.append(
                f"«{rekognoscirovka.name}»: цена по т.9 п.3, как на объекте эталона "
                "(ДОПУЩЕНИЕ) — выбор пункта по проходимости для инженерно-геологической "
                "рекогносцировки не сверен с СБЦ-99; проверьте, подходит ли пункт вашему объекту"
            )
        return warnings

    def check(self, estimate: dict[str, EstimateLine]) -> list[str]:
        # Оба признака (цена не найдена / объём по типовому профилю)
        # читаются прямо из уже построенной сметы (basis, id), а не из
        # отдельного состояния объекта модуля — так check() остаётся
        # чистой функцией от estimate и корректно работает даже если
        # её вызвали не сразу вслед за build_estimate на этом же
        # экземпляре.
        warnings = []

        default_profile_line_ids = {
            f"geology.{volume_key}" for volume_key in DEFAULT_PROFILE_VOLUME_KEYS
        }
        for line_id in default_profile_line_ids:
            line = estimate.get(line_id)
            if line is None:
                continue
            warnings.append(
                f"«{line.name}»: объём посчитан по типовому профилю по "
                "умолчанию (среднее из диапазона на скважину, см. "
                "типовой объект), а не по данным оператора — "
                "требуется проверка"
            )

        lab_profile_line_ids = {
            f"geology.{volume_key}" for volume_key in LAB_PROFILE_VOLUME_KEYS
        }
        for line_id in lab_profile_line_ids:
            line = estimate.get(line_id)
            if line is None:
                continue
            warnings.append(
                f"«{line.name}»: лабораторный профиль, выведен из одного "
                "объекта (не подтверждён статистикой по нескольким сметам, "
                "в отличие от типового профиля выше), требует проверки"
            )

        for line in estimate.values():
            if not line.basis.startswith(MISSING_PRICE_BASIS_PREFIX):
                continue
            warnings.append(
                f"Цена для «{line.name}» не найдена в справочнике, "
                "требуется ручная проверка"
            )

        return warnings
