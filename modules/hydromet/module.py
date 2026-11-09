"""Модуль гидрометеорологии (инженерно-гидрометеорологические изыскания,
ИГМИ) — 5-й модуль, Модель 1 методики (объём × расценка), сразу
двухэтапный (core/stages.py): ведомость объёмов → смета.

**Справочник.** СБЦ-2000 «Инженерно-гидрографические работы.
Инженерно-гидрометеорологические изыскания на реках»; ставка внешнего
транспорта — СБЦ-97, т.5 §6. Цены собирает data/build_references.py из
формул двух эталонных листов одного шаблона компании: «№2.4_ИГМИ» (Карьер) и
«№1.3_ИГМИ» (Автодорога). Разбор листов, формулы и кросс-валидация —
docs/hydromet_diagnostics.md.

**Откуда цены (все модуль берёт через ReferenceBook, но происхождение
записей справочника разное):**
- парсер эталонов (reference_data/) — все цены категории III, полные
  сетки CHOOSE по категориям I/II/III (т.24 и т.43 камеральные, т.26,
  т.44), сетки ширин т.48, все камеральные позиции без пары, ставка
  т.62 §5 и справочная ставка СБЦ-97 т.5 §6;
- **константы** (data/build_references.py::HYDROMET_CATEGORY_II_SUPPLEMENT) —
  категория II пяти полевых позиций, у которых цена на эталонах лежит
  только в кэше внешней книги: т.43 §2 и §1 (поле), т.20 §1, т.24 §1
  (поле), т.25 §1. Источник — сметы компании для сторонних клиентов; файлы
  сознательно не копируются в репозиторий (конфиденциальность — чужие
  заказчики и коммерческие условия), сами числа — нормативные цены
  СБЦ-2000. Обоснование и где перепроверить — комментарий у константы,
  docs/known_risks.md.

**Состав.** Только позиции, реально встретившиеся в эталонах с ненулевой
суммой (шаблон содержит ~370 строк-заготовок всех видов работ):
- полевые (10): рекогносцировка трассы/реки (т.43 §2/§1),
  гидролого-морфометрическое обследование (т.20 §1), морфостворы (т.24
  §1), отметки ГВВ (т.25 §1), нивелирование уклонов (т.26 §1), разбивка
  промерного створа до 100 м (т.44 §7), измерение расхода воды (т.48 §1),
  промеры глубин (т.48 §3), фотоработы (т.48 §15);
- камеральные пары (4): рекогносцировка трассы/реки, морфостворы, расход
  воды — объём = объём полевой строки (на эталонах — одна ячейка
  ведомости или ссылка =D81), коэффициент полевой строки НЕ переносится
  (проверено по формулам всех камеральных строк шаблона; единственное
  исключение шаблона — промеры т.9 — в модуле не реализовано, в эталонах
  их нет);
- самостоятельные камеральные: таблица/схема изученности (т.51 §1/§3),
  вспомогательная таблица (т.52 §1), графики расхода (т.54 §9), кривая
  расходов, площадь и уклон водосбора (т.55 §1/§9/§11), гидрологические
  расчёты (т.56 §1/§2/§3 — первый водосбор и последующие с к=0,5/0,3/0,5
  по прим.1/прим.1/прим.2 отдельными позициями; §12 параметры стока; §15
  выбор аналога), русловые деформации (т.57 §9, §13 × 0,85), записки
  «естественный режим русла» (т.64 §2 × 0,2 «состав работ»), «ледовый
  режим» (т.66 §7/§8), климатическая (т.69 §1), подбор станций (т.67 §1);
- вспомогательные: программа работ (т.53), технический отчёт (т.62 §5);
- зависимые: внутренний транспорт (т.4 §5), внешний транспорт (СБЦ-97
  т.5 §6), организация и ликвидация (п.13 общих указаний).

Единицы измерения — из ведомости объёмов заказчика объекта А
(файл заказчика) (лист «Объем и стоимость работ», раздел ИГМИ): км,
комплекс, створ, фото, таблица, схема, расчёт, дм², водосбор, расход,
записка, подбор. На листе сметы колонки единиц нет. Допущения (в той
ведомости позиции нет): т.20 — «водоток», т.48 §1 — «измерение», т.54 —
«график», т.57 — «расчёт», т.66 — «записка».

**Округление — как в эталоне.** Каждая строка округляется до рубля,
итоги разделов с районным коэффициентом — тоже, транспорт и оргликвидация
считаются от округлённых сумм (EstimateLine.rounding, core/models.py).
Без этого расхождение с эталоном после индексации — рубли.

**Параметры сметы (этап 2), решения по открытым вопросам диагностики:**
- категория сложности — **только II и III** (I и IV+ не встречались ни в
  одном эталоне, цен части позиций для них в исходных данных нет);
  одна на все позиции объекта;
- районный коэффициент к полевым — вводит оператор (1,85 на эталонах,
  1,45 / 1,5 на других объектах); к камеральным — тоже ввод (с 30.10.2026),
  по умолчанию 1,25 (так на всех 24 найденных листах шаблона до 2026 г.;
  в редакции 2026 г. встречается 1,2);
- программа работ — сумма по т.53 вводится оператором (правило выбора
  цены 450…7000 не выведено), к ней — районный коэффициент, ввод, по
  умолчанию 1,25 (в шаблоне — число в формуле, не ячейка Титул!J18);
- районные к камеральным, программе и техотчёту — не меньше 1;
- работы в неблагоприятный период — отметка в данных объекта: каждая
  полевая строка × коэффициент до округления (шаблон: Титул!M15/M18);
  коэффициент — параметр сметы, по умолчанию 1,4 (значение ячейки
  шаблона, нормативный источник не найден);
- внешний транспорт: ставка 0 допустима (у карьеров внешний транспорт × 0);
- внутренний транспорт — ставка из четырёх найденных значений (18,75 /
  16,25 / 15 / 13,75 %), без значения по умолчанию и без автоподбора;
- внешний транспорт — ставку вводит оператор (на эталонах 0,364),
  без значения по умолчанию;
- организация и ликвидация — 6 % × 2,5 от (полевые + внутренний
  транспорт), только при расстоянии > 25 км; при ≤ 25 км строка остаётся
  в смете со ставкой 0 и пометкой «не начисляется»;
- технический отчёт — 0,8 × 1,25 (районный) × 1,25 (прим.6) от суммы
  камеральных строк без районного коэффициента; районный техотчёта —
  отдельный ввод, по умолчанию равен районному к камеральным (в шаблоне
  одна ячейка Титул!J18; в редакции 2026 г. у техотчёта 1,25 при
  камеральных 1,2 — оператор задаёт вводом).

**Не реализовано сознательно:** коэффициент конкурсного снижения (0,65 у
Автодороги — параметр уровня всей сметы, общий для модулей, отложен);
получение гидрометматериалов УГМС, научное сопровождение, аренда
техники, страховой фонд 1 %, выдача промежуточных материалов 1 % (в обоих
эталонах 0); вариант шаблона «расстояние < 25 км» (× 0,85 к полевым без
полевого довольствия и оргликвидация 6 % × 0,7). См. docs/known_risks.md."""

from typing import Any

from core import param_rules
from core.models import EstimateLine
from core.module_base import Field
from core.module_helpers import (
    MISSING_PRICE_BASIS_PREFIX,
    estimate_editable_by,
    missing_price_basis,
    volume_sheet_editable,
    zero_volume_warnings,
)
from core.object_data import object_data_of
from core.stages import SOURCE_CLASS_A, SOURCE_INPUT, SOURCE_OPERATOR, Stage, VolumeLine
from core.tz import KIND_ESTIMATE, OBJECT_LINEAR, Proposal, TzParams
from modules.hydromet import key_volumes

SOURCE = "СБЦ-2000"
EXTERNAL_TRANSPORT_SOURCE = "СБЦ-97"

SUPPORTED_CATEGORIES = ["II", "III"]

# Районный коэффициент к камеральным работам — ввод оператора на этапе сметы
# (Э5а, 30.10.2026). Значение по умолчанию 1,25 — Титул!J18 обоих эталонов
# (Карьер «№2.4_ИГМИ», Автодорога «№1.3_ИГМИ») и всех 24 найденных листов
# шаблона компании до 2026 г. (docs/hydromet_diagnostics.md, раздел 7); с ним
# эталоны сходятся до копейки и открываются старые версии без этого поля.
# В редакции смет 2026 г. встречается 1,2.
# Значение по умолчанию — справочник правил параметров (data/pravila_parametrov.json).
KAMERAL_REGIONAL_COEFFICIENT = param_rules.default("hydromet", "kameral_regional_coefficient", 1.25)
# Программа работ: цена т.53 × районный. В шаблоне 1,25 вписан в формулу
# ЧИСЛОМ, а не ссылкой на Титул!J18: Карьер F350 = 800*1.25*D350,
# Автодорога F351 = 3500*1.25*D351 (на Титул!J18 ссылается только подпись в
# столбце C). Поэтому районный к программе — отдельный ввод, не связанный с
# районным к камеральным; по умолчанию 1,25. В редакции 2026 г. встречается
# и 1,2, и 1,25.
PROGRAM_REGIONAL_COEFFICIENT = param_rules.default("hydromet", "program_regional_coefficient", 1.25)
# Технический отчёт: ставка т.62 §5 (0,8 — из справочника) × районный ×
# прим.6 1,25 (оба эталона). Районный техотчёта в шаблоне — ТА ЖЕ ячейка
# Титул!J18, что у итога камеральных: Карьер F353 = ROUND(D353 × ТБЛ!S18 ×
# Титул!J18 × …) и D339 = Титул!J18; Автодорога F354 / D340 — так же. Поэтому
# отдельное поле «районный к техотчёту» по умолчанию (не введено или 0)
# равно районному к камеральным — как шаблон; расхождение редакции 2026 г.
# (у техотчёта 1,25 при камеральных 1,2) оператор задаёт вводом.
REPORT_TABLE, REPORT_ITEM = "62", "5"
REPORT_NOTE6_COEFFICIENT = 1.25
# Районный коэффициент не бывает меньше 1: поля районных к камеральным,
# программе и техотчёту не принимают значение < 1 (Field.min_value), а
# check() предупреждает, если такое значение пришло не из формы.
REGIONAL_MIN = 1.0
# Работы в неблагоприятный период: шаблон компании умножает КАЖДУЮ полевую строку
# на Титул!M18 при Титул!M15 = 1 — формула всех 10 полевых строк обоих
# эталонов: F = ROUND(D × цена × … × IF(Титул!M15=1, Титул!M18, 1); 0)
# (Карьер F20, F21, F34, F38, F67, F71, F75, F81, F95, F127; Автодорога — те
# же строки +1). M18 = 1,4 — кэш внешней книги «Титул» в обоих файлах; это
# ПОЛЕ ВВОДА шаблона, а не норма: нормативный источник 1,4 не найден. В
# модуле — параметр этапа сметы, по умолчанию 1,4; применяется по выбору
# «Неблагоприятный период для гидрометеорологии» (UNFAVORABLE_MODES ниже; на
# эталонах флаг выключен, M15 = 2). Камеральные строки флаг не читают.
UNFAVORABLE_PERIOD_COEFFICIENT = param_rules.default("hydromet", "unfavorable_period_coefficient", 1.4)
# Организация и ликвидация: п.13 общих указаний — 6 %, × 2,5 по прим. п.13.1,
# начисляется при расстоянии > 25 км.
ORG_LIQUIDATION_RATE = 0.06
ORG_LIQUIDATION_NOTE_COEFFICIENT = 2.5
ORG_LIQUIDATION_MIN_DISTANCE_KM = 25
EXTERNAL_TRANSPORT_TABLE, EXTERNAL_TRANSPORT_ITEM = "5", "6"

# Внутренний транспорт (т.4 §5): четыре ставки, найденные на 24 листах
# шаблона; границы диапазонов по стоимости полевых работ не выведены —
# ставку выбирает оператор.
INTERNAL_TRANSPORT_RATES = [
    ("0.1875", "18,75 % (полевые до ~4,3 тыс. руб. на найденных листах; Карьер)"),
    ("0.1625", "16,25 % (полевые 11,7–14,0 тыс. руб.)"),
    ("0.15", "15 % (полевые 31,5 тыс. руб.; Автодорога)"),
    ("0.1375", "13,75 % (полевые 73–75 тыс. руб.)"),
]

# Записка «Характеристика бытового ледового режима реки» (т.66): § =
# 3 × (тип режима − 1) + диапазон числа годопунктов — по формуле
# наименования шаблона; цены в исходных данных есть только для типа
# «затруднительное замерзание, длительный ледостав, тяжёлое вскрытие».
ICE_NOTE_ITEMS = [
    ("7", "т.66 §7 — затруднительное замерзание, до 10 годопунктов (Карьер)"),
    ("8", "т.66 §8 — затруднительное замерзание, св. 10 до 50 годопунктов (Автодорога)"),
]
# Записка «Характеристика естественного режима русла» (т.64 §2, св. 1 до
# 5 участков) × 0,2 «состав работ» — одинаково на обоих эталонах.
CHANNEL_NOTE_ITEM = "2"
CHANNEL_NOTE_WORK_SHARE = 0.2

EMPTY_CHOICE = ""

# --- ключи ведомости (VolumeLine.id) ---
VK_RECON_ROUTE = "recon_route_km"
VK_RECON_RIVER = "recon_river_km"
VK_MORPHOMETRIC = "morphometric_count"
VK_MORPHO_STVOR = "morpho_stvor_km"
VK_GVV = "gvv_count"
VK_SLOPE = "slope_levelling_km"
VK_SOUNDING_STVOR = "sounding_stvor_count"
VK_FLOW = "flow_measurements"
VK_DEPTH = "depth_soundings"
VK_PHOTO = "photo_count"

VK_STUDY_TABLE = "study_table_count"
VK_STUDY_SCHEME = "study_scheme_count"
VK_AUX_TABLE = "aux_table_count"
VK_FLOW_GRAPHS = "flow_graphs_count"
VK_RATING_CURVE = "rating_curve_count"
VK_BASIN_AREA = "basin_area_count"
VK_BASIN_SLOPE = "basin_slope_count"
VK_MAX_FLOW_FIRST = "max_flow_first"
VK_MAX_FLOW_NEXT = "max_flow_next"
VK_SPRING_FIRST = "spring_flood_first"
VK_SPRING_NEXT = "spring_flood_next"
VK_MIN_FLOW_FIRST = "min_flow_first"
VK_MIN_FLOW_NEXT = "min_flow_next"
VK_RUNOFF_PARAMS = "runoff_params_count"
VK_ANALOG = "analog_count"
VK_CHANNEL_SHIFT = "channel_shift_count"
VK_VERTICAL_DEFORMATION = "vertical_deformation_count"
VK_NOTE_CHANNEL = "note_channel_count"
VK_NOTE_ICE = "note_ice_count"
VK_NOTE_CLIMATE = "note_climate_count"
VK_STATION_SELECTION = "station_selection_count"

VK_PROGRAM = "program"
VK_REPORT = "report"
KAMERAL_VOLUME_KEY_PREFIX = "kameral_"

# --- параметры сметы (ввод этапа 2) ---
P_CATEGORY = "complexity_category"
P_FIELD_REGIONAL = "field_regional_coefficient"
P_KAMERAL_REGIONAL = "kameral_regional_coefficient"
P_PROGRAM_REGIONAL = "program_regional_coefficient"
P_REPORT_REGIONAL = "report_regional_coefficient"
P_UNFAVORABLE = "unfavorable_period_coefficient"
P_UNFAVORABLE_MODE = "unfavorable_period_mode"
# Выбор «Неблагоприятный период для гидрометеорологии»: отметка в данных
# объекта одна на объект, а в листах компании 2026 г. того же объекта геологии
# неблагоприятный период начислен, ИГМИ — нет (боевой прогон 30.10.2026), нет
# его и в листах обоих эталонов. Поэтому по умолчанию — «не применять»
# (с 05.11.2026, аудит О-5 / В-61: при «по отметке объекта» по умолчанию отметка,
# нужная геологии, завышала ИГМИ на 16–23 %); значение по умолчанию — из
# справочника правил параметров. Пусто (поле не задано, старые версии) — значение
# по умолчанию; до 05.11.2026 пустое означало «по отметке объекта» — такие
# сметы с отметкой объекта программа покажет как пересчитанные («был / стал»).
UNFAVORABLE_OFF, UNFAVORABLE_BY_OBJECT, UNFAVORABLE_ON = "off", "object", "on"
_UNFAVORABLE_MODE_LABELS = {
    UNFAVORABLE_OFF: "не применять к гидрометеорологии",
    UNFAVORABLE_BY_OBJECT: "по отметке объекта",
    UNFAVORABLE_ON: "применять к гидрометеорологии",
}
UNFAVORABLE_MODE_DEFAULT = param_rules.default("hydromet", "unfavorable_period_mode", UNFAVORABLE_OFF)
if UNFAVORABLE_MODE_DEFAULT not in _UNFAVORABLE_MODE_LABELS:
    UNFAVORABLE_MODE_DEFAULT = UNFAVORABLE_OFF
# Значение по умолчанию — первым: форма без сохранённого значения берёт первый вариант.
UNFAVORABLE_MODES = [
    (value, label + (" (по умолчанию)" if value == UNFAVORABLE_MODE_DEFAULT else ""))
    for value, label in sorted(_UNFAVORABLE_MODE_LABELS.items(),
                               key=lambda item: item[0] != UNFAVORABLE_MODE_DEFAULT)
]
P_RIVER_WIDTH = "river_width"
P_ICE_NOTE_ITEM = "ice_note_item"
P_PROGRAM_PRICE = "program_price"
P_INTERNAL_TRANSPORT_RATE = "internal_transport_rate"
P_EXTERNAL_TRANSPORT_RATE = "external_transport_rate"
P_DISTANCE_KM = "distance_km"

ESTIMATE_PARAMETER_FIELDS = (
    P_CATEGORY,
    P_FIELD_REGIONAL,
    P_KAMERAL_REGIONAL,
    P_RIVER_WIDTH,
    P_ICE_NOTE_ITEM,
    P_PROGRAM_PRICE,
    P_PROGRAM_REGIONAL,
    P_REPORT_REGIONAL,
    P_UNFAVORABLE_MODE,
    P_UNFAVORABLE,
    P_INTERNAL_TRANSPORT_RATE,
    P_EXTERNAL_TRANSPORT_RATE,
    P_DISTANCE_KM,
)


# Полевые позиции: ключ ведомости -> (наименование, ед., таблица, пункт,
# зависит ли цена от категории сложности, зависит ли от ширины реки).
FIELD_POSITIONS: dict[str, tuple[str, str, str, str, bool, bool]] = {
    VK_RECON_ROUTE: ("Рекогносцировочное обследование трассы", "км", "43", "2", True, False),
    VK_RECON_RIVER: ("Рекогносцировочное обследование реки", "км", "43", "1", True, False),
    VK_MORPHOMETRIC: (
        "Гидролого-морфометрическое обследование водотоков (ширина долины до 1 км)",
        "водоток", "20", "1", True, False,
    ),
    VK_MORPHO_STVOR: ("Разбивка и нивелирование морфостворов", "км", "24", "1", True, False),
    VK_GVV: (
        "Установление отметок ГВВ по меткам или опросу (удаление от оси до 1 км)",
        "комплекс", "25", "1", True, False,
    ),
    VK_SLOPE: ("Нивелирование уклонов", "км", "26", "1", True, False),
    VK_SOUNDING_STVOR: (
        "Разбивка промерного створа при ширине реки до 100 м", "створ", "44", "7", True, False,
    ),
    VK_FLOW: ("Гидрологические работы: измерение расхода воды", "измерение", "48", "1", False, True),
    VK_DEPTH: ("Гидрологические работы: промеры глубин", "створ", "48", "3", False, True),
    VK_PHOTO: ("Фотоработы", "фото", "48", "15", False, False),
}
# Коды, которые справочник различает по разделу (поле/камерал) — см.
# data/build_references.py::HYDROMET_DUAL_PRICE_CODES.
DUAL_PRICE_CODES = {("43", "1"), ("43", "2"), ("24", "1"), ("48", "1")}

# Камеральные пары: пара -> ключ полевой строки.
KAMERAL_PAIRS: dict[str, str] = {
    "recon_route": VK_RECON_ROUTE,
    "recon_river": VK_RECON_RIVER,
    "morpho_stvor": VK_MORPHO_STVOR,
    "flow": VK_FLOW,
}
KAMERAL_PAIR_NAMES = {
    "recon_route": "Камеральная обработка рекогносцировочного обследования трассы",
    "recon_river": "Камеральная обработка рекогносцировочного обследования реки",
    "morpho_stvor": "Камеральная обработка нивелирования морфостворов",
    "flow": "Камеральная обработка измерений расхода воды",
}

# Самостоятельные камеральные позиции: ключ -> (наименование, ед.,
# таблица, пункт, коэффициенты). Пункт None — выбирается параметром сметы
# (записка о ледовом режиме).
KAMERAL_POSITIONS: dict[str, tuple[str, str, str, str | None, dict[str, float]]] = {
    VK_STUDY_TABLE: (
        "Составление таблицы гидрологической изученности бассейна реки (до 50 пунктов)",
        "таблица", "51", "1", {},
    ),
    VK_STUDY_SCHEME: (
        "Составление схемы гидрометеорологической изученности бассейна реки (до 50 пунктов)",
        "схема", "51", "3", {},
    ),
    VK_AUX_TABLE: (
        "Составление вспомогательной таблицы при неискажённом водном режиме (до 50 лет)",
        "таблица", "52", "1", {},
    ),
    VK_FLOW_GRAPHS: (
        "Построение графиков зависимости расхода воды при сильно деформирующемся русле "
        "(до 50 расходов)",
        "график", "54", "9", {},
    ),
    VK_RATING_CURVE: ("Построение кривой расходов гидравлическим методом", "расчёт", "55", "1", {}),
    VK_BASIN_AREA: ("Определение площади водосбора", "дм²", "55", "9", {}),
    VK_BASIN_SLOPE: ("Определение уклона водосбора", "водосбор", "55", "11", {}),
    VK_MAX_FLOW_FIRST: (
        "Определение максимального расхода воды по формулам предельной интенсивности стока",
        "расход", "56", "1", {},
    ),
    VK_MAX_FLOW_NEXT: (
        "Определение максимального расхода воды по РПВ (последующие водосборы, к=0,5 прим.1)",
        "расход", "56", "1", {"k_prim1": 0.5},
    ),
    VK_SPRING_FIRST: (
        "Определение максимальных расходов весеннего половодья", "расход", "56", "2", {},
    ),
    VK_SPRING_NEXT: (
        "Определение максимальных расходов весеннего половодья или дождевых паводков "
        "(последующие водосборы, к=0,3 прим.1)",
        "расход", "56", "2", {"k_prim1": 0.3},
    ),
    VK_MIN_FLOW_FIRST: ("Определение минимального расхода воды", "расход", "56", "3", {}),
    VK_MIN_FLOW_NEXT: (
        "Определение минимального расхода воды (последующие водосборы, к=0,5 прим.2)",
        "расход", "56", "3", {"k_prim2": 0.5},
    ),
    VK_RUNOFF_PARAMS: (
        "Вычисление параметров стока с построением кривой обеспеченности (до 50 лет)",
        "расчёт", "56", "12", {},
    ),
    VK_ANALOG: (
        "Выбор аналога при весьма сходных условиях формирования стока (1 аналог)",
        "расчёт", "56", "15", {},
    ),
    VK_CHANNEL_SHIFT: (
        "Определение смещения русла по данным съёмок (до 3 съёмок)", "расчёт", "57", "9", {},
    ),
    VK_VERTICAL_DEFORMATION: (
        "Определение вертикальных деформаций русла без построения плана (к=0,85 прим.)",
        "расчёт", "57", "13", {"k_prim": 0.85},
    ),
    VK_NOTE_CHANNEL: (
        "Составление записки «Характеристика естественного режима русла реки» "
        "(св. 1 до 5 участков, к=0,2 состав работ)",
        "записка", "64", CHANNEL_NOTE_ITEM, {"k_work_share": CHANNEL_NOTE_WORK_SHARE},
    ),
    VK_NOTE_ICE: (
        "Составление записки «Характеристика бытового ледового режима реки»",
        "записка", "66", None, {},
    ),
    VK_NOTE_CLIMATE: ("Составление климатической записки", "записка", "69", "1", {}),
    VK_STATION_SELECTION: ("Подбор станций", "подбор", "67", "1", {}),
}

LINE_PREFIX = "hydromet."
KAMERAL_LINE_PREFIX = "hydromet.kameral."
FIELD_REGIONAL_ID = "hydromet.field_regional"
KAMERAL_REGIONAL_ID = "hydromet.kameral_regional"
INTERNAL_TRANSPORT_ID = "hydromet.internal_transport"
EXTERNAL_TRANSPORT_ID = "hydromet.external_transport"
ORG_LIQUIDATION_ID = "hydromet.org_liquidation"
PROGRAM_ID = "hydromet.program"
REPORT_ID = "hydromet.report"


def volume_key_for_estimate_line(line_id: str) -> str | None:
    """Ключ строки ведомости, от которой строка сметы берёт объём
    (None — строка без объёма: районные коэффициенты, транспорт,
    оргликвидация)."""
    if line_id.startswith(KAMERAL_LINE_PREFIX):
        pair = line_id[len(KAMERAL_LINE_PREFIX):]
        return f"{KAMERAL_VOLUME_KEY_PREFIX}{pair}" if pair in KAMERAL_PAIRS else pair
    key = line_id[len(LINE_PREFIX):] if line_id.startswith(LINE_PREFIX) else None
    if key in FIELD_POSITIONS or key in (VK_PROGRAM, VK_REPORT):
        return key
    return None



# Находка ревью №10 (30.10.2026): объёмы, которые по смыслу работ и эталонам
# должны быть > 0, когда раздел не пустой, — предупреждение этапа ведомости
# (core/module_helpers.py::zero_volume_warnings; список — список вопросов В-8).
VOLUME_EXPECTED_NONZERO = {"recon_route_km": "Рекогносцировочное обследование трассы, км", "morphometric_count": "Гидролого-морфометрическое обследование водотоков"}
VOLUME_EXPECTED_NONZERO_IN_ROWS = {}


def _volume_zero_warnings(inputs, previous, refs):
    return zero_volume_warnings(inputs, VOLUME_EXPECTED_NONZERO, VOLUME_EXPECTED_NONZERO_IN_ROWS)

class HydrometModule:
    code = "hydromet"

    # --- поля ввода ---

    def volume_schema(self) -> list[Field]:
        """Поля этапа 1 (ведомость объёмов): объёмы, которые вводит
        оператор."""
        fields = [
            Field(name=key, label=f"{name}, {unit}", type="number", required=False,
                  integer=unit not in ("км",))
            for key, (name, unit, *_rest) in FIELD_POSITIONS.items()
        ]
        fields += [
            Field(name=key, label=f"{name}, {unit}", type="number", required=False, integer=True)
            for key, (name, unit, *_rest) in KAMERAL_POSITIONS.items()
        ]
        return fields

    def estimate_schema(self) -> list[Field]:
        """Поля этапа 2 (смета): параметры расценки — решение №12,
        docs/multistage_design.md."""
        return [
            Field(
                name=P_CATEGORY,
                label="Категория сложности (СБЦ-2000; поддерживаются только II и III)",
                type="text", required=True, dynamic_options=True,
            ),
            Field(
                name=P_FIELD_REGIONAL,
                label="Районный коэффициент к полевым работам (на эталонах — 1,85)",
                type="number", required=True, positive=True,
            ),
            Field(
                name=P_KAMERAL_REGIONAL,
                label="Районный коэффициент к камеральным работам (множитель к сумме камеральных; "
                "1,25 — как в эталонах, Титул!J18; в сметах 2026 г. встречается 1,2; "
                "не меньше 1)",
                type="number", required=False, default=KAMERAL_REGIONAL_COEFFICIENT,
                min_value=REGIONAL_MIN,
            ),
            Field(
                name=P_REPORT_REGIONAL,
                label="Районный коэффициент к техническому отчёту (0 — как районный к камеральным: "
                "в шаблоне это одна ячейка Титул!J18; в сметах 2026 г. встречается 1,25 при "
                "камеральных 1,2 — тогда введите отдельно)",
                # 0 — «не задан, как к камеральным» (интерфейс не умеет пустое
                # число); значение от 0 до 1 check() считает ошибкой ввода.
                type="number", required=False,
            ),
            Field(
                name=P_UNFAVORABLE_MODE,
                label="Неблагоприятный период для гидрометеорологии (не применять, по отметке в "
                "данных объекта или применять; в листах компании у ИГМИ не применён)",
                type="text", required=False, dynamic_options=True,
            ),
            Field(
                name=P_UNFAVORABLE,
                label="Коэффициент к полевым работам в неблагоприятный период (применяется по "
                "выбору выше; 1,4 — значение ячейки шаблона компании (Титул!M18); нормативный "
                "источник не найден)",
                type="number", required=False, default=UNFAVORABLE_PERIOD_COEFFICIENT,
                min_value=1.0,
            ),
            Field(
                name=P_RIVER_WIDTH,
                label="Ширина реки (т.48: измерение расхода воды и промеры глубин)",
                type="text", required=False, dynamic_options=True,
            ),
            Field(
                name=P_ICE_NOTE_ITEM,
                label="Записка «Бытовой ледовый режим» — пункт т.66 (по числу годопунктов)",
                type="text", required=False, dynamic_options=True,
            ),
            Field(
                name=P_PROGRAM_PRICE,
                label="Программа работ (т.53): сумма, руб. в базисных ценах СБЦ-2000, "
                "до районного коэффициента (на эталонах 800 / 3500; правило выбора не выведено)",
                type="number", required=False,
            ),
            Field(
                name=P_PROGRAM_REGIONAL,
                label="Районный коэффициент к программе работ (множитель к сумме т.53; в шаблоне "
                "1,25 вписано в формулу числом, отдельно от районного к камеральным; в сметах "
                "2026 г. встречается 1,2; не меньше 1)",
                type="number", required=False, default=PROGRAM_REGIONAL_COEFFICIENT,
                min_value=REGIONAL_MIN,
            ),
            Field(
                name=P_INTERNAL_TRANSPORT_RATE,
                label="Внутренний транспорт (т.4 §5): ставка",
                type="text", required=True, dynamic_options=True,
            ),
            Field(
                name=P_EXTERNAL_TRANSPORT_RATE,
                label="Внешний транспорт (СБЦ-97 т.5 §6): ставка, доля (на эталонах 0,364; "
                "0 — не начислять, как у карьеров)",
                # 0 допустим: у карьеров в сметах компании внешний транспорт × 0
                # (аудит 30.10.2026, §2.5); «не заполнено» — только пусто.
                type="number", required=True,
            ),
            Field(
                name=P_DISTANCE_KM,
                label="Расстояние от базы до участка работ, км (оргликвидация — только при > 25 км)",
                type="number", required=True, positive=True,
            ),
        ]

    def input_schema(self) -> list[Field]:
        return self.volume_schema() + self.estimate_schema()

    def field_options(self, field_name: str, refs: Any) -> list[tuple[str, str]]:
        choose = [(EMPTY_CHOICE, "— выберите —")]
        if field_name == P_CATEGORY:
            return choose + [(cat, f"{cat} категория") for cat in SUPPORTED_CATEGORIES]
        if field_name == P_UNFAVORABLE_MODE:
            return list(UNFAVORABLE_MODES)
        if field_name == P_INTERNAL_TRANSPORT_RATE:
            return choose + list(INTERNAL_TRANSPORT_RATES)
        if field_name == P_ICE_NOTE_ITEM:
            options = []
            for item, label in ICE_NOTE_ITEMS:
                try:
                    price, _ = refs.get_price(SOURCE, "66", item)
                    options.append((item, f"{label} — {price:g} руб."))
                except KeyError:
                    options.append((item, f"{label} — цена не найдена в справочнике"))
            return choose + options
        if field_name == P_RIVER_WIDTH:
            # Сетка ширин т.48 §1 (полевая) — из справочника, по возрастанию.
            try:
                widths = refs.get_categories(SOURCE, "48", "1")
            except AttributeError:
                widths = []
            labels = sorted(
                (cat.split("/", 1)[1], price) for cat, price in widths if cat.startswith("field/")
            )
            labels.sort(key=lambda pair: pair[1])
            return choose + [(width, width) for width, _price in labels]
        return []

    # --- одноэтапный контракт BudgetModule ---

    def propose_volumes(self, tz: TzParams) -> Proposal:
        """Черновик ввода этапа ведомости по параметрам ТЗ (core/tz.py, 03.11.2026).
        Число водотоков, створов, промеров и расчётов из ТЗ не выводится (по карте и
        решению исполнителя) — ввод. Предлагаются: рекогносцировка трассы
        (протяжённость) и камеральные документы «одна штука на объект»."""
        proposal = Proposal()
        if tz.object_kind == OBJECT_LINEAR and tz.length_km:
            proposal.set(
                VK_RECON_ROUTE, tz.length_km, KIND_ESTIMATE,
                "= протяжённость трассы из ТЗ (ДОПУЩЕНИЕ: обследование вдоль всей трассы)",
            )
        else:
            proposal.leave(VK_RECON_ROUTE, "только для линейного объекта с протяжённостью трассы")
        for name in (VK_STUDY_TABLE, VK_STUDY_SCHEME, VK_NOTE_CHANNEL, VK_NOTE_ICE,
                     VK_NOTE_CLIMATE, VK_STATION_SELECTION):
            proposal.set(
                name, 1, KIND_ESTIMATE,
                "одна на объект — ДОПУЩЕНИЕ (так в обоих эталонных листах компании); при "
                "нескольких реках или бассейнах — проверьте",
            )
        if tz.isso_count:
            proposal.notes.append(
                f"По ТЗ искусственных сооружений (мостов и труб): {tz.isso_count}. Число "
                "обследуемых водотоков, отметок высоких вод, измерений расхода, створов и "
                "промеров из ТЗ не выводится — ввод оператора (правило «на одно сооружение» "
                "не подтверждено)."
            )
        return proposal

    def key_volume_fields(self) -> list[Field]:
        """Ключевые объёмы раздела для режима «ключевые объёмы» (core/tz.py)."""
        return key_volumes.key_volume_fields()

    def propose_from_key_volumes(self, tz: TzParams, keys: dict) -> Proposal:
        """Черновик ввода ведомости по ключевым объёмам: предложение по ТЗ +
        ключевые объёмы + достроенное по связям (modules/hydromet/key_volumes.py)."""
        return key_volumes.propose(self, tz, keys)

    def build_volumes(self, data: dict, refs: Any) -> dict:
        """Объёмы + параметры сметы в одном словаре (прямой вызов
        build_volumes → build_estimate, как у остальных модулей)."""
        volumes = {key: data.get(key, 0) or 0 for key in FIELD_POSITIONS}
        volumes.update({key: data.get(key, 0) or 0 for key in KAMERAL_POSITIONS})
        volumes.update(self._parameters(data))
        return volumes

    @staticmethod
    def _parameters(data: dict) -> dict:
        def text(name):
            return str(data.get(name) or EMPTY_CHOICE)

        def number(name, default=0.0):
            value = data.get(name)
            return float(value) if value not in (None, "") else default

        return {
            P_CATEGORY: text(P_CATEGORY),
            P_FIELD_REGIONAL: number(P_FIELD_REGIONAL),
            # Нет поля (старая версия / черновик до 30.10.2026) — 1,25, как
            # было константой: числа старых смет не меняются.
            P_KAMERAL_REGIONAL: number(P_KAMERAL_REGIONAL, KAMERAL_REGIONAL_COEFFICIENT),
            P_RIVER_WIDTH: text(P_RIVER_WIDTH),
            P_ICE_NOTE_ITEM: text(P_ICE_NOTE_ITEM),
            P_PROGRAM_PRICE: number(P_PROGRAM_PRICE),
            P_PROGRAM_REGIONAL: number(P_PROGRAM_REGIONAL, PROGRAM_REGIONAL_COEFFICIENT),
            # Нет поля или 0 — как районный к камеральным (одна ячейка
            # шаблона, см. REPORT_NOTE6_COEFFICIENT).
            P_REPORT_REGIONAL: number(P_REPORT_REGIONAL),
            P_UNFAVORABLE: number(P_UNFAVORABLE, UNFAVORABLE_PERIOD_COEFFICIENT),
            P_UNFAVORABLE_MODE: text(P_UNFAVORABLE_MODE),
            P_INTERNAL_TRANSPORT_RATE: text(P_INTERNAL_TRANSPORT_RATE),
            P_EXTERNAL_TRANSPORT_RATE: number(P_EXTERNAL_TRANSPORT_RATE),
            P_DISTANCE_KM: number(P_DISTANCE_KM),
        }

    def build_estimate(self, volumes: dict, refs: Any) -> dict[str, EstimateLine]:
        category = volumes.get(P_CATEGORY, EMPTY_CHOICE)
        width = volumes.get(P_RIVER_WIDTH, EMPTY_CHOICE)
        lines: dict[str, EstimateLine] = {}

        # --- полевые ---
        # Неблагоприятный период — множитель каждой полевой строки до её
        # округления, как в шаблоне (см. UNFAVORABLE_PERIOD_COEFFICIENT).
        unfavorable = self._unfavorable_applies(volumes, object_data_of(refs).unfavorable_period)
        unfavorable_coefficient = self._unfavorable_coefficient(volumes)
        field_coefs = {"k_unfavorable": unfavorable_coefficient} if unfavorable else {}
        field_ids = []
        for key, (name, unit, table, item, by_category, by_width) in FIELD_POSITIONS.items():
            grade = category if by_category else (width if by_width else None)
            line = self._priced_line(
                refs, f"{LINE_PREFIX}{key}", name, unit, volumes.get(key, 0),
                table, item, self._category(table, item, "field", grade), dict(field_coefs),
                missing_reason=self._missing_reason(by_category, by_width, grade),
            )
            if unfavorable:
                line.basis += (
                    f" × {unfavorable_coefficient:g} — работы в неблагоприятный период "
                    "(отметка в данных объекта или выбор «применять» в смете; коэффициент — параметр сметы, по умолчанию 1,4 "
                    "из ячейки шаблона компании Титул!M18, нормативный источник не найден)"
                )
            lines[line.id] = line
            field_ids.append(line.id)

        field_coefficient = volumes.get(P_FIELD_REGIONAL, 0) or 0
        lines[FIELD_REGIONAL_ID] = EstimateLine(
            id=FIELD_REGIONAL_ID,
            name=f"Районный коэффициент к полевым работам (к={field_coefficient:g})",
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": field_coefficient - 1},
            kind="B",
            base_ids=list(field_ids),
            basis=f"Итого полевых = ROUND(Σ полевых × {field_coefficient:g}) — надбавка "
            f"{field_coefficient - 1:g} × Σ (коэффициент задан оператором)",
            rounding=0,
        )
        field_total_ids = [*field_ids, FIELD_REGIONAL_ID]

        # --- камеральные ---
        kameral_ids = []
        for pair, field_key in KAMERAL_PAIRS.items():
            _name, unit, table, item, by_category, by_width = FIELD_POSITIONS[field_key]
            grade = category if by_category else (width if by_width else None)
            line = self._priced_line(
                refs, f"{KAMERAL_LINE_PREFIX}{pair}", KAMERAL_PAIR_NAMES[pair], unit,
                volumes.get(f"{KAMERAL_VOLUME_KEY_PREFIX}{pair}", volumes.get(field_key, 0)),
                table, item, self._category(table, item, "kameral", grade), {},
                missing_reason=self._missing_reason(by_category, by_width, grade),
            )
            lines[line.id] = line
            kameral_ids.append(line.id)

        ice_item = volumes.get(P_ICE_NOTE_ITEM, EMPTY_CHOICE)
        for key, (name, unit, table, item, coefs) in KAMERAL_POSITIONS.items():
            reason = None
            if item is None:  # записка о ледовом режиме — пункт из параметра
                item = ice_item
                reason = None if ice_item else "не выбран пункт т.66 (число годопунктов)"
            line = self._priced_line(
                refs, f"{KAMERAL_LINE_PREFIX}{key}", name, unit, volumes.get(key, 0),
                table, item, None, dict(coefs), missing_reason=reason,
            )
            lines[line.id] = line
            kameral_ids.append(line.id)

        kameral_coefficient = volumes.get(P_KAMERAL_REGIONAL, KAMERAL_REGIONAL_COEFFICIENT)
        lines[KAMERAL_REGIONAL_ID] = EstimateLine(
            id=KAMERAL_REGIONAL_ID,
            name=f"Районный коэффициент к камеральным работам (к={kameral_coefficient:g})",
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": kameral_coefficient - 1},
            kind="B",
            base_ids=list(kameral_ids),
            basis=f"Итого камеральных = ROUND(Σ камеральных × {kameral_coefficient:g}) — надбавка "
            f"{kameral_coefficient - 1:g} × Σ (коэффициент задан оператором; в эталонах — 1,25)",
            rounding=0,
        )

        # --- зависимые расходы ---
        internal_rate_text = volumes.get(P_INTERNAL_TRANSPORT_RATE, EMPTY_CHOICE)
        internal_rate = float(internal_rate_text) if internal_rate_text else 0.0
        lines[INTERNAL_TRANSPORT_ID] = EstimateLine(
            id=INTERNAL_TRANSPORT_ID,
            name="Внутренний транспорт",
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": internal_rate},
            kind="B",
            base_ids=list(field_total_ids),
            basis=(
                f"СБЦ-2000 т.4 §5 — {internal_rate * 100:g} % от итого полевых (ставка выбрана "
                "оператором)" if internal_rate_text
                else "СБЦ-2000 т.4 §5 — ставка не выбрана оператором, строка = 0"
            ),
            rounding=0,
        )

        external_rate = volumes.get(P_EXTERNAL_TRANSPORT_RATE, 0) or 0
        external_basis = (
            f"{EXTERNAL_TRANSPORT_SOURCE} т.5 §6 — {external_rate:g} × (итого полевых + "
            "внутренний транспорт), ставка введена оператором"
        )
        try:
            reference_rate, _ = refs.get_price(
                EXTERNAL_TRANSPORT_SOURCE, EXTERNAL_TRANSPORT_TABLE, EXTERNAL_TRANSPORT_ITEM
            )
            external_basis += f" (на эталонах {reference_rate:g})"
        except KeyError:
            pass
        lines[EXTERNAL_TRANSPORT_ID] = EstimateLine(
            id=EXTERNAL_TRANSPORT_ID,
            name="Внешний транспорт",
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": external_rate},
            kind="B",
            base_ids=[*field_total_ids, INTERNAL_TRANSPORT_ID],
            basis=external_basis,
            rounding=0,
        )

        distance = volumes.get(P_DISTANCE_KM, 0) or 0
        applies = distance > ORG_LIQUIDATION_MIN_DISTANCE_KM
        lines[ORG_LIQUIDATION_ID] = EstimateLine(
            id=ORG_LIQUIDATION_ID,
            name="Организация и ликвидация гидрологических работ"
            + ("" if applies else f" — не начисляется (расстояние {distance:g} км ≤ 25 км)"),
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": ORG_LIQUIDATION_RATE * ORG_LIQUIDATION_NOTE_COEFFICIENT if applies else 0.0},
            kind="B",
            base_ids=[*field_total_ids, INTERNAL_TRANSPORT_ID],
            basis=(
                "СБЦ-2000 п.13 общих указаний — 6 % × 2,5 (прим. п.13.1) от (итого полевых + "
                f"внутренний транспорт), расстояние {distance:g} км > 25 км"
                if applies else
                f"СБЦ-2000 п.13 общих указаний — начисляется только при расстоянии > 25 км; "
                f"задано {distance:g} км"
            ),
            rounding=0,
        )

        # --- вспомогательные ---
        program_price = volumes.get(P_PROGRAM_PRICE, 0) or 0
        program_coefficient = volumes.get(P_PROGRAM_REGIONAL, PROGRAM_REGIONAL_COEFFICIENT)
        lines[PROGRAM_ID] = EstimateLine(
            id=PROGRAM_ID,
            name="Составление программы производства гидрологических работ",
            unit="программа",
            qty=volumes.get(VK_PROGRAM, 1),
            price=program_price,
            coefs={"k_regional": program_coefficient},
            kind="A",
            base_ids=[],
            basis="СБЦ-2000 т.53 — сумма введена оператором (правило выбора цены не выведено) "
            f"× {program_coefficient:g} районный (задан оператором)",
        )

        report_rate, report_basis = self._price_or_missing(
            refs, SOURCE, REPORT_TABLE, REPORT_ITEM, None, "Составление технического отчёта", None
        )
        own_report_coefficient = volumes.get(P_REPORT_REGIONAL) or 0
        report_coefficient = own_report_coefficient or kameral_coefficient
        report_regional_basis = (
            f"{report_coefficient:g} районный (задан оператором отдельно)"
            if own_report_coefficient else
            f"{report_coefficient:g} районный (= районный к камеральным, как в шаблоне: Титул!J18)"
        )
        lines[REPORT_ID] = EstimateLine(
            id=REPORT_ID,
            name="Составление технического отчёта",
            unit="%",
            qty=0.0,
            price=0.0,
            coefs={"rate": report_rate * report_coefficient * REPORT_NOTE6_COEFFICIENT
                   * volumes.get(VK_REPORT, 1)},
            kind="B",
            base_ids=list(kameral_ids),
            basis=f"{report_basis} × {report_regional_basis} × 1,25 прим.6 от суммы камеральных "
            "строк без районного коэффициента",
            rounding=0,
        )
        return lines

    @staticmethod
    def _unfavorable_coefficient(volumes: dict) -> float:
        """Коэффициент неблагоприятного периода; нет поля или 0 — 1,4."""
        return volumes.get(P_UNFAVORABLE) or UNFAVORABLE_PERIOD_COEFFICIENT

    @staticmethod
    def _unfavorable_applies(volumes: dict, object_flag: bool) -> bool:
        """Применять ли неблагоприятный период к ИГМИ: выбор этапа сметы,
        пусто (поле не задано) — значение по умолчанию («не применять»)."""
        mode = volumes.get(P_UNFAVORABLE_MODE) or UNFAVORABLE_MODE_DEFAULT
        if mode == UNFAVORABLE_ON:
            return True
        if mode == UNFAVORABLE_BY_OBJECT:
            return bool(object_flag)
        return False

    # --- многоэтапный сценарий ---

    def build_volume_sheet(
        self, data: dict, pinned: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, VolumeLine]:
        """Ведомость объёмов (этап 1). Ввод — объёмы полевых и
        самостоятельных камеральных позиций; по жёсткой связи —
        программа (1), технический отчёт (1) и четыре камеральные пары
        (= объём своей полевой строки). Зависимая строка следует за
        строкой-источником, собственная правка зависимой побеждает."""
        pinned = pinned or {}
        lines: dict[str, VolumeLine] = {}

        def put(key, name, unit, qty, source, basis):
            lines[key] = VolumeLine(key, name, unit, qty, source, basis)
            return qty

        def auto(key, name, unit, generated, basis):
            override = pinned.get(key, {})
            if "qty" in override:
                return put(key, name, unit, override["qty"], SOURCE_OPERATOR, basis)
            return put(key, name, unit, generated, SOURCE_CLASS_A, basis)

        auto(VK_PROGRAM, "Составление программы производства гидрологических работ",
             "программа", 1, "одна на объект")
        for key, (name, unit, *_rest) in FIELD_POSITIONS.items():
            put(key, name, unit, data.get(key, 0) or 0, SOURCE_INPUT, "ввод оператора")
        for pair, field_key in KAMERAL_PAIRS.items():
            auto(
                f"{KAMERAL_VOLUME_KEY_PREFIX}{pair}", KAMERAL_PAIR_NAMES[pair],
                lines[field_key].unit, lines[field_key].qty,
                f"= объём полевой строки «{lines[field_key].name}»",
            )
        for key, (name, unit, *_rest) in KAMERAL_POSITIONS.items():
            put(key, name, unit, data.get(key, 0) or 0, SOURCE_INPUT, "ввод оператора")
        auto(VK_REPORT, "Составление технического отчёта", "отчёт", 1, "один на объект")
        return lines

    def stages(self) -> list[Stage]:
        def compute_volumes(inputs, previous, refs, pinned):
            return self.build_volume_sheet(inputs, pinned)

        def compute_estimate(inputs, previous, refs, pinned):
            volumes = {**previous["volumes"].values(), **self._parameters(inputs)}
            return self.build_estimate(volumes, refs=refs)

        # См. core/module_helpers.py: решение №5, ставка у долей.
        volumes_editable = volume_sheet_editable
        estimate_editable = estimate_editable_by(volume_key_for_estimate_line)

        def estimate_context_warnings(inputs, previous, refs):
            # Своё поле расстояния у ИГМИ (оргликвидация при > 25 км) не
            # связано с расстоянием в данных объекта — расхождение для одного
            # объекта показываем, а не выбираем молча одно из двух.
            warnings = []
            object_data = object_data_of(refs)
            object_distance = object_data.distance_km
            own = inputs.get(P_DISTANCE_KM)
            if object_distance is not None and own and abs(float(own) - object_distance) > 1e-9:
                warnings.append(
                    f"Расстояние в смете ИГМИ ({float(own):g} км) не совпадает с расстоянием в данных "
                    f"объекта ({object_distance:g} км) — проверьте."
                )
            parameters = self._parameters(inputs)
            unfavorable_coefficient = self._unfavorable_coefficient(parameters)
            mode = parameters[P_UNFAVORABLE_MODE] or UNFAVORABLE_MODE_DEFAULT
            applies = self._unfavorable_applies(parameters, object_data.unfavorable_period)
            if applies:
                source = (
                    "выбрано «применять» в смете ИГМИ" if mode == UNFAVORABLE_ON
                    else "отмечены работы в неблагоприятный период (данные объекта), для ИГМИ "
                    "действует «по отметке объекта»"
                )
                warning = (
                    f"Неблагоприятный период: {source}. Для ИГМИ это означает — каждая полевая "
                    f"строка умножена на {unfavorable_coefficient:g} (параметр сметы «коэффициент "
                    "к полевым в неблагоприятный период»; 1,4 — значение ячейки шаблона компании "
                    "Титул!M18, нормативный источник не найден); камеральные — без коэффициента."
                )
                if mode == UNFAVORABLE_BY_OBJECT:
                    warning += (
                        " Если для гидрометеорологии он не нужен (в листах компании 2026 г. ×1,4 у "
                        "ИГМИ не применён, при этом у геологии того же объекта — применён), "
                        "выберите «не применять» в поле «Неблагоприятный период для "
                        "гидрометеорологии» — отметка объекта для других разделов останется."
                    )
                warnings.append(warning)
            elif object_data.unfavorable_period:
                warnings.append(
                    "В данных объекта отмечены работы в неблагоприятный период, но для ИГМИ "
                    "выбрано «не применять» (значение по умолчанию: в листах компании у ИГМИ этого "
                    "коэффициента нет) — полевые строки ИГМИ без коэффициента. Если он нужен, "
                    "выберите «применять» или «по отметке объекта» в поле «Неблагоприятный период "
                    "для гидрометеорологии»."
                )
            elif unfavorable_coefficient != UNFAVORABLE_PERIOD_COEFFICIENT:
                warnings.append(
                    f"Коэффициент неблагоприятного периода задан ({unfavorable_coefficient:g}), но "
                    "неблагоприятный период к ИГМИ не применяется (нет отметки объекта или выбрано "
                    "«не применять») — коэффициент не действует."
                )
            own_report = parameters[P_REPORT_REGIONAL]
            if own_report < 0 or 0 < own_report < REGIONAL_MIN:
                warnings.append(
                    f"Районный коэффициент к техническому отчёту {own_report:g} меньше 1 — "
                    "районный коэффициент не бывает меньше 1, проверьте (0 — как к камеральным)."
                )
            return warnings

        return [
            Stage(
                number=1, key="volumes", title="Ведомость объёмов работ",
                fields=self.volume_schema(), compute=compute_volumes,
                editable_fields=volumes_editable, confirm_label="Подтвердить ведомость",
                context_warnings=_volume_zero_warnings,
            ),
            Stage(
                number=2, key="estimate", title="Смета",
                fields=self.estimate_schema(), compute=compute_estimate,
                check=self.check, editable_fields=estimate_editable,
                confirm_label="Подтвердить смету", context_warnings=estimate_context_warnings,
            ),
        ]

    # --- цены ---

    @staticmethod
    def _category(table: str, item: str, section: str, grade: str | None) -> str | None:
        if (table, item) in DUAL_PRICE_CODES:
            return f"{section}/{grade}" if grade else section
        return grade or None

    @staticmethod
    def _missing_reason(by_category: bool, by_width: bool, grade: str | None) -> str | None:
        if by_category and not grade:
            return "не выбрана категория сложности"
        if by_width and not grade:
            return "не выбрана ширина реки"
        return None

    def _priced_line(
        self, refs, line_id, name, unit, qty, table, item, category, coefs, missing_reason=None,
    ) -> EstimateLine:
        price, basis = self._price_or_missing(
            refs, SOURCE, table, item, category, name, missing_reason
        )
        return EstimateLine(
            id=line_id, name=name, unit=unit, qty=qty, price=price, coefs=coefs,
            kind="A", base_ids=[], basis=basis, rounding=0,
        )

    @staticmethod
    def _price_or_missing(refs, source, table, item, category, name, reason) -> tuple[float, str]:
        if reason is None:
            try:
                return refs.get_price(source, table, item, category=category)
            except KeyError:
                pass
        detail = f": {reason}" if reason else ""
        return 0.0, missing_price_basis(
            name, f" ({source} т.{table} §{item or '?'}{', ' + category if category else ''}){detail}"
        )

    # --- предупреждения ---

    def check(self, estimate: dict[str, EstimateLine]) -> list[str]:
        warnings = []
        for line in estimate.values():
            if line.basis.startswith(MISSING_PRICE_BASIS_PREFIX) and line.qty:
                warnings.append(f"«{line.name}»: {line.basis}")

        category_line = estimate.get(f"{LINE_PREFIX}{VK_RECON_ROUTE}")
        if category_line is not None and "не выбрана категория" in category_line.basis:
            warnings.append("Не выбрана категория сложности — позиции, зависящие от неё, без цены")

        field_regional = estimate.get(FIELD_REGIONAL_ID)
        if field_regional is not None and field_regional.coefs["rate"] < 0:
            warnings.append(
                "Районный коэффициент к полевым работам меньше 1 (или не задан) — итог полевых "
                "уменьшен, проверьте"
            )
        kameral_regional = estimate.get(KAMERAL_REGIONAL_ID)
        if kameral_regional is not None and kameral_regional.coefs["rate"] < 0:
            warnings.append(
                "Районный коэффициент к камеральным работам меньше 1 — итог камеральных уменьшен, "
                "проверьте (в эталонах 1,25)"
            )
        program_line = estimate.get(PROGRAM_ID)
        if program_line is not None and program_line.coefs.get("k_regional", 1) < 1:
            warnings.append(
                "Районный коэффициент к программе работ меньше 1 — проверьте (в эталонах 1,25)"
            )

        if estimate.get(INTERNAL_TRANSPORT_ID) and not estimate[INTERNAL_TRANSPORT_ID].coefs["rate"]:
            warnings.append("Ставка внутреннего транспорта (т.4 §5) не выбрана — строка = 0")
        if estimate.get(EXTERNAL_TRANSPORT_ID) and not estimate[EXTERNAL_TRANSPORT_ID].coefs["rate"]:
            warnings.append(
                "Ставка внешнего транспорта (СБЦ-97 т.5 §6) равна 0 — внешний транспорт не "
                "начисляется (так у карьеров); если это не нужно, введите ставку (на эталонах 0,364)"
            )

        org = estimate.get(ORG_LIQUIDATION_ID)
        if org is not None and not org.coefs["rate"]:
            warnings.append(
                "Организация и ликвидация не начисляется: расстояние ≤ 25 км. В шаблоне компании при "
                "расстоянии < 25 км полевые умножаются на 0,85 (без полевого довольствия), а "
                "оргликвидация идёт по 6 % × 0,7 — в модуле этот вариант не реализован, проверьте "
                "вручную"
            )

        program = estimate.get(PROGRAM_ID)
        if program is not None:
            if not program.price:
                warnings.append("Сумма программы работ (т.53) не задана — строка = 0")
            else:
                warnings.append(
                    f"Программа работ: сумма {program.price:g} руб. введена оператором — правило "
                    "выбора цены т.53 не выведено (на найденных листах 450…7000), проверьте"
                )

        warnings.append(
            "Ограничения раздела: только категории сложности II и III; не реализованы коэффициент "
            "конкурсного снижения, гидрометматериалы УГМС, научное сопровождение, аренда техники, "
            "страховой фонд и выдача промежуточных материалов."
        )
        return warnings
